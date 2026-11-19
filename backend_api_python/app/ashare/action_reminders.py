"""One-shot reminders for manual steps in the paper strategy workflow."""
from datetime import timedelta
from decimal import Decimal, InvalidOperation

import psycopg2
from psycopg2.extras import RealDictCursor

from app.ashare.broker import SHANGHAI, aware
from app.ashare.calendar_rules import market_session
from app.ashare.daily_notification import beijing_time, daily_feishu_status
from app.ashare.market import SOURCE, validate_quote
from app.ashare.notifications import notify_paper_link, review_origin
from app.ashare.review_assistance import daily_quote
from app.ashare.runtime import reviewed_rules, utc_now
from app.ashare.strategy_proposals import _valid_scope
from app.ashare.strategy_versions import SELECT_VERSION, StrategyVersions
from app.ashare.web_store import WebStore


ACTIVATE = 'activate_approved_strategy'
REVIEW = 'review_daily_security_rules'
INTENT = 'paper_action_reminder_intent'
RESULT = 'paper_action_reminder_result'
APPROVAL_GRACE = timedelta(minutes=10)


def _daily_review_due(cursor, versions, web, config, day, now, owner):
    symbol = config['symbol']
    existing = web['rules'].get(symbol, {})
    if existing.get('trading_day') == day and (existing.get('st') is True or existing.get('suspended') is True):
        return False

    cursor.execute('SELECT quotes FROM qd_ashare_web WHERE tenant_id=%s AND account_id=%s', versions.key)
    try:
        quote = daily_quote(cursor.fetchone(), symbol, now)
        validate_quote(quote, now)
        low, high = (Decimal(str(quote[key])) for key in ('limit_down', 'limit_up'))
    except (ValueError, TypeError, KeyError, InvalidOperation):
        return False
    name = quote.get('name')
    if (quote.get('source') != SOURCE or not isinstance(name, str) or not name
            or 'ST' in name.upper() or '退' in name or not low <= Decimal(config['limit_price']) <= high):
        return False
    cursor.execute("""SELECT EXISTS (
        SELECT 1 FROM qd_ashare_security_receipts r
        JOIN qd_ashare_security_statuses s ON s.receipt_id=r.id
        WHERE r.trading_day=%s AND s.symbol=%s AND r.received_at<=%s
          AND ('true'::jsonb IN (s.details->'st', s.details->'suspended'))
    ) AS ineligible""", (day, symbol, now))
    if cursor.fetchone()['ineligible']:
        return False

    try:
        rule = reviewed_rules(web, symbol, now)
        reviewed = aware(rule.get('reviewed_at'))
        return (rule.get('approval_method') != 'explicit_human_review'
                or type(rule.get('reviewed_by')) is not int or rule['reviewed_by'] != owner
                or reviewed > now or reviewed.astimezone(SHANGHAI).date().isoformat() != day
                or Decimal(str(rule['limit_down'])) != low or Decimal(str(rule['limit_up'])) != high)
    except (ValueError, TypeError, KeyError, InvalidOperation):
        return True


def _due_action(cursor, versions, account, web, owner, now):
    cursor.execute(SELECT_VERSION + ' AND a.decided_at IS NOT NULL AND r.decided_at IS NULL LIMIT 2',
                   versions.key)
    rows = cursor.fetchall()
    if len(rows) != 1:
        return None
    version = versions._view(rows[0])
    config = version['config']
    if (version['approved_by'] != owner or version['created_by'] != owner
            or config['symbol'] not in web['settings'].get('symbols', [])
            or version['approved_at'] > now or version['created_at'] > version['approved_at']):
        return None
    funding = versions._funding(config, account)
    if not funding['cap_sufficient']:
        return None
    versions._verify_research(cursor, config)

    day = now.astimezone(SHANGHAI).date().isoformat()
    if version['activated_at'] is None:
        if now - version['approved_at'] < APPROVAL_GRACE:
            return None
        kind, key = ACTIVATE, f"{ACTIVATE}:{version['id']}"
    else:
        if (version['order'] is not None or version['activated_by'] != owner
                or not version['approved_at'] <= version['activated_at'] <= now
                or not funding['cash_sufficient']):
            return None
        session = market_session(now)
        if (session['can_match'] is not True or session['is_trading_day'] is not True
                or session['trading_day'] != day
                or not _daily_review_due(cursor, versions, web, config, day, now, owner)):
            return None
        kind, key = REVIEW, f"{REVIEW}:{version['id']}:{day}"
    action = {'kind': kind, 'version_id': version['id'], 'config_sha256': version['config_sha256'], 'key': key}
    if kind == REVIEW:
        action['trading_day'] = day
    return action, config['symbol']


def _send_card(base, action, symbol, now, credentials):
    link = f"{base}/#/ashare-paper"
    lines = [f"标的：{symbol}", f"检查时间：{beijing_time(now)}"]
    if action['kind'] == ACTIVATE:
        lines.append("此版本已获批准。若要运行，请登录后单独启用自动模拟委托。")
        return notify_paper_link("策略待启用", lines, "打开此策略", f"{link}?strategy_version={action['version_id']}",
                                 **credentials)
    lines.append("该策略已启用。请在 A 股模拟页面核对当日证券状态和价格限制，再人工审核此标的。")
    return notify_paper_link("当日证券规则待审核", lines, "打开 A 股模拟", link, **credentials)


def send_due_reminder(connection, scope, *, now=None):
    """Claim one concrete user action; a committed send intent is terminal for retry."""
    if not isinstance(scope, tuple) or len(scope) != 2 or not _valid_scope(*scope):
        return {'status': 'unavailable'}
    now = aware(now if now is not None else utc_now())
    versions = StrategyVersions(connection, *scope)
    store = WebStore(connection, *scope)
    try:
        base = review_origin()
        credentials = store.notification_credentials() if base else {}
        with connection, connection.cursor(cursor_factory=RealDictCursor) as cursor:
            account, web = versions._lock_account(cursor)
            due = _due_action(cursor, versions, account, web, int(scope[0][5:]), now)
            if due is None:
                return None
            action, symbol = due
            summary = {'kind': action['kind'], 'version_id': action['version_id']}
            cursor.execute("""SELECT event, details->>'status' AS status FROM qd_ashare_audit
                WHERE tenant_id=%s AND account_id=%s AND event IN (%s,%s) AND details->>'key'=%s
                ORDER BY id DESC LIMIT 1""", (*scope, INTENT, RESULT, action['key']))
            previous = cursor.fetchone()
            if previous is not None:
                status = previous['status'] if previous['event'] == RESULT else 'unknown'
                if status not in ('webhook_acknowledged', 'rejected', 'failed', 'disabled'):
                    status = 'unknown'
                return {**summary, 'notice': status}
            if not credentials.get('webhook_url'):
                return {**summary, 'notice': 'unconfigured'}
            versions.broker._audit(cursor, INTENT, action)
    except (ValueError, KeyError, TypeError, AttributeError, InvalidOperation, psycopg2.Error):
        return {'status': 'unavailable'}
    try:
        notice = daily_feishu_status(_send_card(base, action, symbol, now, credentials))
        with connection, connection.cursor() as cursor:
            versions.broker._audit(cursor, RESULT, {**action, 'status': notice})
    except Exception:
        # The attempt committed before send; leaving its outcome unknown blocks retries.
        notice = 'unknown'
    return {**summary, 'notice': notice}
