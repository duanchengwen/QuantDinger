"""Create at most one review-only paper draft from today's scheduled research."""
from __future__ import annotations

import argparse
from contextlib import closing
import json
import re
import sys

import psycopg2
from psycopg2.extras import RealDictCursor

from app.ashare.broker import SHANGHAI
from app.ashare.calendar_rules import trading_day_status
from app.ashare.notifications import send_strategy_review_notice
from app.ashare.research import ResearchError, ResearchStore, _decode, _instant
from app.ashare.runtime import utc_now
from app.ashare.schema import get_database_url
from app.ashare.strategy_backtest_store import save_result
from app.ashare.strategy_template import saved_template
from app.ashare.strategy_versions import (
    PROPOSAL_REQUEST_PREFIX,
    SELECT_VERSION,
    StrategyVersions,
    config_hash,
    normalize_config,
)
from app.ashare.web_store import WebStore


RULE_FIELDS = {'symbol', 'quantity', 'threshold', 'limit_price', 'max_cash'}
TEMPLATE_FIELDS = {'schema_version', 'enabled', 'tenant_id', 'account_id', 'expires_at', 'rules'}
MAX_TEMPLATE_BYTES = 16_384
MAX_TEMPLATE_RULES = 10
PROPOSED_EVENT = 'strategy_draft_proposed_from_research'
HISTORY_ATTEMPT = 'strategy_history_collection_intent'
MAX_EVALUATED_CANDIDATES = 3


def _valid_scope(tenant, account_id):
    return (account_id == 'paper' and isinstance(tenant, str)
            and re.fullmatch(r'user_[1-9][0-9]{0,18}', tenant) is not None
            and int(tenant[5:]) <= 2**63 - 1)


def normalize_template(value):
    if not isinstance(value, dict) or set(value) != TEMPLATE_FIELDS:
        raise ValueError('invalid_proposal_template')
    tenant, rules = value['tenant_id'], value['rules']
    if (type(value['schema_version']) is not int or value['schema_version'] != 1
            or type(value['enabled']) is not bool or not _valid_scope(tenant, value['account_id'])
            or not isinstance(value['expires_at'], str)
            or not isinstance(rules, list) or not 1 <= len(rules) <= MAX_TEMPLATE_RULES):
        raise ValueError('invalid_proposal_template')
    try:
        expires = _instant(value['expires_at']).isoformat()
        configs = []
        for rule in rules:
            if not isinstance(rule, dict) or set(rule) != RULE_FIELDS:
                raise ValueError()
            configs.append(normalize_config({'mode': 'paper', 'kind': 'buy_once_below', **rule}))
        if len({config['symbol'] for config in configs}) != len(configs):
            raise ValueError()
    except ValueError:
        raise ValueError('invalid_proposal_template') from None
    return {**value, 'expires_at': expires, 'rules': configs}


def propose(connection, template, *, now=None, collect=False):
    template = normalize_template(template)
    now = _instant(now if now is not None else utc_now())
    if not template['enabled'] or _instant(template['expires_at']) <= now:
        return _outcome('inactive_template')
    return _propose(connection, template['tenant_id'], template['account_id'], now, collect=collect, file_template=template)


def propose_saved(connection, tenant, account_id, *, now=None, collect=False):
    if not _valid_scope(tenant, account_id):
        raise ValueError('invalid_proposal_scope')
    return _propose(connection, tenant, account_id, _instant(now if now is not None else utc_now()), collect=collect)


def _outcome(status, **details):
    return {'execution_allowed': False, 'approvals_created': 0, 'status': status, **details}


def _context(cursor, versions, research, now, file_template):
    account, web = versions._lock_account(cursor)
    settings = web['settings']
    saved = saved_template(settings) if file_template is None else None
    if saved is not None and (saved['template'] is None or not saved['template']['enabled']):
        return _outcome('inactive_template'), None
    local_day = now.astimezone(SHANGHAI).date()
    run = research.find_completed_scheduled_run(cursor, local_day)
    if run is None:
        return _outcome('no_completed_scheduled_run'), None
    created, started, completed = [_instant(run[key]) for key in ('created_at', 'started_at', 'completed_at')]
    if not (created <= started <= completed <= now and created.astimezone(SHANGHAI).date() == local_day):
        raise ResearchError('invalid_evidence_time')

    request_key = PROPOSAL_REQUEST_PREFIX + run['id']
    cursor.execute(SELECT_VERSION + ' AND v.request_key=%s', (*versions.key, request_key))
    previous = cursor.fetchone()
    if previous is not None:
        existing = versions._view(previous)
        cursor.execute("""SELECT 1 FROM qd_ashare_audit
            WHERE tenant_id=%s AND account_id=%s AND event=%s
              AND details->>'version_id'=%s AND details->>'run_id'=%s LIMIT 1""",
                       (*versions.key, PROPOSED_EVENT, existing['id'], run['id']))
        if cursor.fetchone() is None:
            raise ValueError('strategy_proposal_request_key_conflict')
        return _outcome('already_proposed', version_id=existing['id']), None

    symbols = [item['symbol'] for item in run['candidates'] if item['symbol'] in settings.get('symbols', [])]
    if saved is not None:
        template = saved['template']
        if template['symbol_scope'] == 'selected':
            symbols = [symbol for symbol in symbols if symbol in template['symbols']]
        caps = {symbol: template['max_cash'] for symbol in symbols}
        operator_rules = {}
        origin = {'origin': 'scheduled_research_saved_template',
                  'template_sha256': config_hash(template), 'template_revision': saved['revision']}
    else:
        operator_rules = {rule['symbol']: rule for rule in file_template['rules']}
        symbols = [symbol for symbol in operator_rules if symbol in symbols]
        caps = {symbol: operator_rules[symbol]['max_cash'] for symbol in symbols}
        origin = {'origin': 'scheduled_research_template', 'template_sha256': config_hash(file_template)}
    symbols = symbols[:MAX_EVALUATED_CANDIDATES]
    if not symbols:
        return _outcome('no_eligible_template_match'), None

    cursor.execute("""SELECT symbol,SUM(quantity) AS quantity FROM qd_ashare_lots
        WHERE tenant_id=%s AND account_id=%s AND acquired_day<%s AND symbol=ANY(%s) GROUP BY symbol
        """, (*versions.key, local_day, symbols))
    available = {row['symbol']: row['quantity'] for row in cursor.fetchall()}
    cursor.execute("""SELECT symbol,SUM(quantity-filled) AS quantity FROM qd_ashare_orders
        WHERE tenant_id=%s AND account_id=%s AND side='sell' AND status IN ('open','partial')
            AND symbol=ANY(%s) GROUP BY symbol""", (*versions.key, symbols))
    reserved = {row['symbol']: row['quantity'] for row in cursor.fetchall()}
    settled = {s: max(0, available.get(s, 0) - reserved.get(s, 0)) for s in symbols}
    reference = {'run_id': run['id'], 'input_hash': run['input_hash'], 'output_hash': run['output_hash']}
    return None, {'request_key': request_key, 'reference': reference, 'origin': origin, 'caps': caps,
                  'symbols': symbols, 'operator_rules': operator_rules,
                  'settled': settled, 'fee_policy': account['fee_policy']}


def _evaluate(connection, plan, now):
    from app.ashare.strategy_backtest import evaluate

    return {symbol: evaluate(connection, symbol, as_of=now, max_cash=plan['caps'][symbol],
                             fee_policy=plan['fee_policy'], settled_quantity=plan['settled'][symbol])
            for symbol in plan['symbols']}


def _claim_history(connection, versions, research, now, file_template, plan):
    # The account lock serializes the durable intent. Network collection never holds it.
    with connection, connection.cursor(cursor_factory=RealDictCursor) as cursor:
        _, current = _context(cursor, versions, research, now, file_template)
        if current != plan:
            return False
        run_id = plan['reference']['run_id']
        cursor.execute("""SELECT 1 FROM qd_ashare_audit
            WHERE tenant_id=%s AND account_id=%s AND event=%s AND details->>'run_id'=%s LIMIT 1
            """, (*versions.key, HISTORY_ATTEMPT, run_id))
        if cursor.fetchone() is not None:
            return False
        versions.broker._audit(cursor, HISTORY_ATTEMPT, {'run_id': run_id})
        return True


def _propose(connection, tenant, account_id, now, *, collect, file_template=None):
    if not trading_day_status(now.astimezone(SHANGHAI).date())['is_trading_day']:
        return _outcome('no_verified_trading_day')
    versions = StrategyVersions(connection, tenant, account_id)
    research = ResearchStore(connection, tenant, account_id)
    with connection, connection.cursor(cursor_factory=RealDictCursor) as cursor:
        existing, plan = _context(cursor, versions, research, now, file_template)
    if existing is not None:
        return existing

    reports = _evaluate(connection, plan, now)
    missing = [symbol for symbol, report in reports.items() if report['status'] == 'insufficient_evidence']
    if (collect and missing and not any(report['status'] == 'candidate' for report in reports.values())
            and _claim_history(connection, versions, research, now, file_template, plan)):
        from app.ashare.strategy_history import collect_history

        collect_history(missing, as_of=now)
        current = _instant(utc_now())
        if current.astimezone(SHANGHAI).date() != now.astimezone(SHANGHAI).date():
            return _outcome('backtest_insufficient_evidence')
        now = current
        reports = _evaluate(connection, plan, now)

    with connection, connection.cursor(cursor_factory=RealDictCursor) as cursor:
        prior, current = _context(cursor, versions, research, now, file_template)
        if prior is not None:
            return prior
        if current != plan:
            return _outcome('backtest_inputs_changed')
        account, _ = versions._lock_account(cursor)
        for symbol, report in reports.items():
            if report['status'] != 'candidate':
                continue
            terms = report['config']
            if plan['operator_rules'] and terms != plan['operator_rules'][symbol]:
                continue
            if terms['symbol'] != symbol or terms['max_cash'] != plan['caps'][symbol]:
                raise ValueError('strategy_backtest_unavailable')
            funding = versions._funding(terms, account)
            if not funding['cap_sufficient']:
                continue
            backtest = save_result(cursor, tenant, account_id, plan['reference']['run_id'], report)
            config = normalize_config({**terms, 'research_reference': plan['reference'], 'backtest_reference': backtest})
            actor = int(tenant[5:])
            draft = versions._insert_draft(cursor, config, plan['request_key'], actor, funding)
            versions.broker._audit(cursor, PROPOSED_EVENT, {
                'version_id': draft['id'], 'run_id': plan['reference']['run_id'], 'template_owner': actor,
                'backtest': backtest, **plan['origin'],
            })
            return _outcome('draft_created', version_id=draft['id'])
    if any(report['status'] == 'insufficient_evidence' for report in reports.values()):
        return _outcome('backtest_insufficient_evidence')
    return _outcome('no_backtest_signal')


def decode_template(raw):
    if not 0 < len(raw) <= MAX_TEMPLATE_BYTES:
        raise ValueError('invalid_proposal_template')
    template = _decode(raw.decode('utf-8'))
    normalize_template(template)
    return template


def propose_and_notify(connection, raw_template=None, *, scope=None, collect=False):
    """Continue a completed research run through draft commit and one notice claim."""
    if raw_template is not None:
        try:
            template = decode_template(raw_template)
        except (ValueError, UnicodeError):
            return {'status': 'invalid_template'}
        file_scope = (template['tenant_id'], template['account_id'])
        if scope is not None and scope != file_scope:
            return {'status': 'invalid_template'}
        scope = file_scope
    if scope is None:
        return {'status': 'proposal_failed'}
    try:
        outcome = (propose_saved(connection, *scope, collect=collect) if raw_template is None
                   else propose(connection, template, collect=collect))
    except (ValueError, psycopg2.Error):
        return {'status': 'proposal_failed'}
    version_id = outcome.get('version_id')
    result = {'status': outcome['status']}
    if not version_id:
        return result
    result['version_id'] = version_id
    store = WebStore(connection, *scope)
    versions = StrategyVersions(connection, *store.key)
    try:
        version = versions.get(version_id)
        result['notice'] = send_strategy_review_notice(store, versions, version)
    except (ValueError, KeyError, TypeError, psycopg2.Error):
        # The claim may already be committed. Leave it intact if the outcome
        # cannot be established and let the next run consult that durable claim.
        result['notice'] = 'unknown'
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description='Apply one operator-owned JSON strategy template from stdin')
    parser.add_argument('--apply', required=True, action='store_true', help='Allow saving one review-only draft')
    parser.parse_args(argv)
    try:
        template = decode_template(sys.stdin.buffer.read(MAX_TEMPLATE_BYTES + 1))
    except (ValueError, UnicodeError):
        print('{"status":"invalid_proposal_template","execution_allowed":false,"approvals_created":0}')
        return 1
    try:
        dsn = get_database_url()
        if not dsn:
            raise ValueError()
        with closing(psycopg2.connect(dsn, connect_timeout=5,
                                      options='-c statement_timeout=5000 -c lock_timeout=2000')) as connection:
            result = propose(connection, template)
    except (ValueError, psycopg2.Error):
        print('{"status":"draft_proposal_failed","execution_allowed":false,"approvals_created":0}')
        return 1
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
