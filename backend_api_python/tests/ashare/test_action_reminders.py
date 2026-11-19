"""Concrete user-action notices with disposable PostgreSQL and fake transport."""
from contextlib import closing
from datetime import timedelta
from decimal import Decimal
import os
from pathlib import Path
import uuid

import psycopg2
from psycopg2 import sql
import pytest

from app.ashare import action_reminders as reminders
from app.ashare.broker import SHANGHAI, STRATEGY_ORDER_PREFIX
from app.ashare.schema import MIGRATIONS
from app.ashare.strategy_versions import StrategyVersions
from app.ashare.web_store import WebStore


SCOPE = ('user_71', 'paper')
SYMBOL = 'sz.000001'
BASE = 'https://paper.example.test:5188'
WEBHOOK = 'https://open.feishu.cn/open-apis/bot/v2/hook/owner-fixture'
CONFIG = {'mode': 'paper', 'kind': 'buy_once_below', 'symbol': SYMBOL, 'quantity': 100,
          'threshold': '10.00', 'limit_price': '10.00', 'max_cash': '1100.00'}


@pytest.fixture
def account(monkeypatch):
    dsn = os.getenv('TEST_ASHARE_DATABASE_URL')
    if not dsn:
        pytest.skip('TEST_ASHARE_DATABASE_URL required')
    scheme = 'action_test_' + uuid.uuid4().hex

    def connect():
        connection = psycopg2.connect(dsn)
        with connection, connection.cursor() as cursor:
            cursor.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(scheme)))
        return connection

    with closing(psycopg2.connect(dsn)) as admin:
        with admin, admin.cursor() as cursor:
            cursor.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(scheme)))
        try:
            with closing(connect()) as connection:
                with connection, connection.cursor() as cursor:
                    for name in MIGRATIONS:
                        cursor.execute((Path(__file__).parents[2] / 'migrations' / name).read_text())
                store = WebStore(connection, *SCOPE)
                store.initialize('10000')
                monkeypatch.setenv('SECRET_KEY', 'test-fixture-not-a-secret-with-enough-bytes')
                monkeypatch.setenv('ASHARE_REVIEW_BASE_URL', BASE)
                monkeypatch.setenv('FEISHU_WEBHOOK_URL', 'https://open.feishu.cn/open-apis/bot/v2/hook/wrong')
                store.configure_notifications(WEBHOOK, 'owner-fixture-secret')
                yield store, connect
        finally:
            with admin, admin.cursor() as cursor:
                cursor.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(scheme)))


def approved(store):
    versions = StrategyVersions(store.connection, *store.key)
    draft = versions.create(CONFIG, 'action-test-draft', 71)
    return versions, versions.decide(draft['id'], draft['config_sha256'], 'approved', True, 71)


def audit(connection):
    with connection, connection.cursor() as cursor:
        cursor.execute("""SELECT event,details FROM qd_ashare_audit WHERE event IN (%s,%s) ORDER BY id""",
                       (reminders.INTENT, reminders.RESULT))
        return cursor.fetchall()


@pytest.mark.parametrize('receipt', ['ack', 'timeout', 'interruption'])
def test_exact_activation_reminder_once_and_never_activates(account, monkeypatch, receipt):
    store, connect = account
    _, version = approved(store)
    now = version['approved_at'] + reminders.APPROVAL_GRACE
    sent = []

    def notify(title, lines, button, url, **credentials):
        assert title == '策略待启用' and button == '打开此策略'
        assert url == f"{BASE}/#/ashare-paper?strategy_version={version['id']}"
        assert credentials == {'webhook_url': WEBHOOK, 'signing_secret': 'owner-fixture-secret'}
        assert f"检查时间：{now.astimezone(SHANGHAI):%Y-%m-%d %H:%M:%S}（北京时间）" in lines
        with closing(connect()) as observer:
            [(event, attempted)] = audit(observer)
            assert event == reminders.INTENT and attempted['version_id'] == version['id']
        sent.append(version['id'])
        if receipt == 'interruption':
            raise SystemExit(9)
        return ({'status': 'sent'} if receipt == 'ack'
                else {'status': 'failed', 'error': 'transport_error_delivery_unknown'})

    monkeypatch.setattr(reminders, 'notify_paper_link', notify)
    assert reminders.send_due_reminder(store.connection, SCOPE, now=now - timedelta(seconds=1)) is None
    if receipt == 'interruption':
        with pytest.raises(SystemExit):
            reminders.send_due_reminder(store.connection, SCOPE, now=now)
    else:
        reminders.send_due_reminder(store.connection, SCOPE, now=now)
    expected = 'webhook_acknowledged' if receipt == 'ack' else 'unknown'
    with closing(connect()) as restarted:
        result = reminders.send_due_reminder(restarted, SCOPE, now=now + timedelta(minutes=2))
        assert result == {'kind': reminders.ACTIVATE, 'version_id': version['id'], 'notice': expected}
        assert len(sent) == 1
        rows = audit(restarted)
        assert rows[0][1]['key'] == f"{reminders.ACTIVATE}:{version['id']}"
        assert len(rows) == (1 if receipt == 'interruption' else 2)
        with restarted, restarted.cursor() as cursor:
            cursor.execute("""SELECT (SELECT count(*) FROM qd_ashare_strategy_decisions),
                (SELECT count(*) FROM qd_ashare_strategy_activations),(SELECT count(*) FROM qd_ashare_orders)""")
            assert cursor.fetchone() == (1, 0, 0)


def _quote(now, *, name='平安银行'):
    stamp = now.isoformat()
    return {'symbol': SYMBOL, 'name': name, 'source': 'tencent_web', 'exchange_time': stamp, 'received_at': stamp,
            'last': '10', 'bid': '9.99', 'ask': '10', 'bid_size': 100, 'ask_size': 100, 'volume': 10000,
            'previous_close': '10', 'open': '10', 'high': '10.1', 'low': '9.9',
            'limit_up': '11', 'limit_down': '9', 'evidence_sha256': '9' * 64}


def test_review_reminder_requires_continuous_fresh_eligible_evidence_and_is_daily(account, monkeypatch):
    store, _ = account
    versions, version = approved(store)
    versions.decide(version['id'], version['config_sha256'], 'activated', True, 71)
    now = (version['approved_at'].astimezone(SHANGHAI) + timedelta(days=1)).replace(
        hour=10, minute=0, second=0, microsecond=0,
    )
    live = False

    def session(stamp):
        return {'can_match': live, 'is_trading_day': True, 'trading_day': stamp.astimezone(SHANGHAI).date().isoformat()}

    sent = []
    monkeypatch.setattr(reminders, 'market_session', session)
    monkeypatch.setattr(reminders, 'notify_paper_link',
                        lambda *args, **kwargs: sent.append((args, kwargs)) or {'status': 'sent'})
    store.observe([_quote(now)], {})
    assert reminders.send_due_reminder(store.connection, SCOPE, now=now) is None
    live = True
    assert reminders.send_due_reminder(store.connection, SCOPE, now=now + timedelta(seconds=16)) is None
    store.observe([_quote(now, name='ST证券')], {})
    assert reminders.send_due_reminder(store.connection, SCOPE, now=now) is None
    store.observe([_quote(now)], {})
    day = now.date().isoformat()
    rule = {'symbol': SYMBOL, 'trading_day': day, 'reviewed': True, 'is_trading_day': True,
            'suspended': False, 'st': False, 'limit_up': '11', 'limit_down': '9', 'evidence': 'offline fixture',
            'approval_method': 'explicit_human_review', 'reviewed_by': 71, 'reviewed_at': now.isoformat()}
    store.review(rule)
    assert reminders.send_due_reminder(store.connection, SCOPE, now=now) is None
    store.review({**rule, 'reviewed_by': 72})
    first = reminders.send_due_reminder(store.connection, SCOPE, now=now)
    assert first == {'kind': reminders.REVIEW, 'version_id': version['id'], 'notice': 'webhook_acknowledged'}
    assert reminders.send_due_reminder(store.connection, SCOPE, now=now) == first
    assert len(sent) == 1
    assert sent[0][0][3] == f'{BASE}/#/ashare-paper'
    assert SYMBOL in sent[0][0][1][0] and '人工审核' in sent[0][0][1][-1]
    [(event, details), _] = audit(store.connection)
    assert event == reminders.INTENT and details['key'] == f"{reminders.REVIEW}:{version['id']}:{day}"

    tomorrow = now + timedelta(days=1)
    store.observe([_quote(tomorrow)], {})
    assert reminders.send_due_reminder(store.connection, SCOPE, now=tomorrow) == first
    assert len(sent) == 2
    later = tomorrow + timedelta(days=1)
    with store.connection, versions.broker._cursor() as cursor:
        ledger = versions.broker._account(cursor)
        versions.broker._place_order_locked(cursor, ledger, STRATEGY_ORDER_PREFIX + version['id'], SYMBOL,
                                            'buy', 100, Decimal('10'), later, later.date())
    store.observe([_quote(later)], {})
    assert reminders.send_due_reminder(store.connection, SCOPE, now=later) is None
    assert len(sent) == 2


def test_unconfigured_and_other_accounts_leave_no_notice_intent(account, monkeypatch):
    store, _ = account
    _, version = approved(store)
    now = version['approved_at'] + reminders.APPROVAL_GRACE
    store.configure_notifications('')
    other = WebStore(store.connection, 'user_72', 'paper')
    other.initialize('10000')
    other.configure_notifications('https://open.feishu.cn/open-apis/bot/v2/hook/other-fixture')
    monkeypatch.setattr(reminders, 'notify_paper_link', lambda *_args, **_kwargs: pytest.fail('unexpected transport'))
    assert reminders.send_due_reminder(store.connection, other.key, now=now) is None
    assert reminders.send_due_reminder(store.connection, SCOPE, now=now)['notice'] == 'unconfigured'
    assert not audit(store.connection)


def test_parallel_pollers_claim_only_one_reminder(account, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    store, connect = account
    _, version = approved(store)
    now = version['approved_at'] + reminders.APPROVAL_GRACE
    start = Barrier(2)
    sent = []
    monkeypatch.setattr(reminders, 'notify_paper_link',
                        lambda *args, **_kwargs: sent.append(args[3]) or {'status': 'sent'})

    def poll():
        with closing(connect()) as connection:
            start.wait(timeout=5)
            return reminders.send_due_reminder(connection, SCOPE, now=now)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [future.result(timeout=10) for future in (pool.submit(poll), pool.submit(poll))]
    assert all(item['version_id'] == version['id'] for item in results)
    assert [event for event, _ in audit(store.connection)] == [reminders.INTENT, reminders.RESULT]
    assert len(sent) == 1
