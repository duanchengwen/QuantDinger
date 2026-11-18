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
from app.ashare.strategy_template import minimum_buy_config, saved_template
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


def propose(connection, template, *, now=None):
    template = normalize_template(template)
    now = _instant(now if now is not None else utc_now())
    if not template['enabled'] or _instant(template['expires_at']) <= now:
        return {'execution_allowed': False, 'approvals_created': 0, 'status': 'inactive_template'}
    return _propose(connection, template['tenant_id'], template['account_id'], now, file_template=template)


def propose_saved(connection, tenant, account_id, *, now=None):
    if not _valid_scope(tenant, account_id):
        raise ValueError('invalid_proposal_scope')
    return _propose(connection, tenant, account_id, _instant(now if now is not None else utc_now()))


def _propose(connection, tenant, account_id, now, *, file_template=None):
    local_day = now.astimezone(SHANGHAI).date()
    outcome = {'execution_allowed': False, 'approvals_created': 0}
    if not trading_day_status(local_day)['is_trading_day']:
        return {**outcome, 'status': 'no_verified_trading_day'}

    actor = int(tenant[5:])
    versions, research = StrategyVersions(connection, tenant, account_id), ResearchStore(connection, tenant, account_id)
    with connection, connection.cursor(cursor_factory=RealDictCursor) as cursor:
        account, web = versions._lock_account(cursor)
        settings = web['settings']
        if file_template is None:
            saved = saved_template(settings)
            template = saved['template']
            if template is None or not template['enabled']:
                return {**outcome, 'status': 'inactive_template'}
            origin = {'origin': 'scheduled_research_saved_template',
                      'template_sha256': config_hash(template), 'template_revision': saved['revision']}
        else:
            origin = {'origin': 'scheduled_research_template', 'template_sha256': config_hash(file_template)}

        run = research.find_completed_scheduled_run(cursor, local_day)
        if run is None:
            return {**outcome, 'status': 'no_completed_scheduled_run'}
        created, started, completed = [_instant(run[key]) for key in ('created_at', 'started_at', 'completed_at')]
        if not (created <= started <= completed <= now
                and created.astimezone(SHANGHAI).date() == local_day):
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
            return {**outcome, 'status': 'already_proposed', 'version_id': existing['id']}

        candidates = [candidate['symbol'] for candidate in run['candidates']]
        eligible = set(candidates).intersection(settings.get('symbols', []))
        if file_template is None:
            if template['symbol_scope'] == 'selected':
                eligible.intersection_update(template['symbols'])
            rules = [minimum_buy_config(template, symbol) for symbol in candidates if symbol in eligible]
        else:
            rules = file_template['rules']
        for rule in rules:
            if rule['symbol'] not in eligible:
                continue
            funding = versions._funding(rule, account)
            if not funding['cap_sufficient']:
                continue
            config = normalize_config({**rule, 'research_reference': {
                'run_id': run['id'], 'input_hash': run['input_hash'], 'output_hash': run['output_hash'],
            }})
            draft = versions._insert_draft(cursor, config, request_key, actor, funding)
            versions.broker._audit(cursor, PROPOSED_EVENT, {
                'version_id': draft['id'], 'run_id': run['id'], 'template_owner': actor, **origin,
            })
            return {**outcome, 'status': 'draft_created', 'version_id': draft['id']}
        return {**outcome, 'status': 'no_eligible_template_match'}


def decode_template(raw):
    if not 0 < len(raw) <= MAX_TEMPLATE_BYTES:
        raise ValueError('invalid_proposal_template')
    template = _decode(raw.decode('utf-8'))
    normalize_template(template)
    return template


def propose_and_notify(connection, raw_template=None, *, scope=None):
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
        outcome = (propose_saved(connection, *scope) if raw_template is None
                   else propose(connection, template))
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
