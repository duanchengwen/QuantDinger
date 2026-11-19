"""Managed account acceptance using real PostgreSQL and an external quote feed."""
import os
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import uuid

import pytest

from app.ashare.managed_worker import ManagedWorker
from app.ashare.operations import DAILY_FEISHU_ATTEMPT, DAILY_FEISHU_RESULT, list_daily_reports
from app.ashare.strategy_versions import StrategyVersions
from app.ashare.trade_notifications import TRADE_FEISHU_ATTEMPT, TRADE_FEISHU_RESULT, notify_new_fills
from app.ashare.web_store import WebStore


def instant(day=17, hour=2, second=0):
    return datetime(2026, 11, day, hour, 0, second, tzinfo=timezone.utc)


def quote(now):
    return {"symbol": "sz.000001", "name": "平安银行", "exchange_time": now.isoformat(),
            "received_at": now.isoformat(), "last": "10", "bid": "9.99", "ask": "10",
            "bid_size": 1000, "ask_size": 1000, "volume": 100000,
            "previous_close": "10", "open": "10", "high": "10.1", "low": "9.9",
            "limit_up": "11", "limit_down": "9", "source": "tencent_web",
            "quote_id": now.isoformat(), "evidence_sha256": "a" * 64}


@pytest.fixture
def account(tmp_path):
    dsn = os.getenv("TEST_ASHARE_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_ASHARE_DATABASE_URL required")
    import psycopg2
    from psycopg2 import sql
    connection = psycopg2.connect(dsn)
    schema = "managed_test_" + uuid.uuid4().hex
    with connection, connection.cursor() as cursor:
        cursor.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        cursor.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        for name in ("20261117_ashare_paper.sql", "20261117_ashare_web.sql", "20261117_ashare_operations.sql",
                     "20261118_ashare_strategy_versions.sql", "20261118_ashare_strategy_activation.sql"):
            cursor.execute((Path(__file__).parents[2] / "migrations" / name).read_text())
    store = WebStore(connection, "managed_" + uuid.uuid4().hex)
    store.initialize("10000")
    yield connection, store, tmp_path
    if not connection.closed:
        with connection, connection.cursor() as cursor:
            cursor.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
    connection.close()


def approve(store, day=17):
    store.review({"symbol": "sz.000001", "reviewed": True, "evidence": "controlled fixture",
                  "trading_day": f"2026-11-{day}", "is_trading_day": True,
                  "suspended": False, "st": False, "limit_up": "11", "limit_down": "9"})


def test_worker_publishes_quotes_without_auto_approving_rules(account):
    connection, store, path = account
    now = instant()
    worker = ManagedWorker(connection, path, fetcher=lambda *a, **k: [quote(now)])
    result = worker.process_account(store.tenant_id, now=now)
    state = store.state()
    assert result["state"] == "awaiting_review"
    assert state["quotes"][0]["last"] == "10"
    assert state["rules"] == {}
    assert state["orders"] == state["fills"] == []


def test_review_changes_take_effect_and_next_quote_fills_without_restart(account):
    connection, store, path = account
    times = [instant(second=0), instant(second=6)]
    worker = ManagedWorker(connection, path, fetcher=lambda *a, **k: [quote(times.pop(0))])
    assert worker.process_account(store.tenant_id, now=instant())["state"] == "awaiting_review"
    approve(store)
    store.broker.place_order("manual", "sz.000001", "buy", 100, "10", instant(second=1), instant().date())
    assert worker.process_account(store.tenant_id, now=instant(second=6))["state"] == "healthy"
    assert store.state()["positions"][0]["quantity"] == 100
    assert len(store.state()["fills"]) == 1


@pytest.mark.parametrize("day,hour,expected", [(17, 4, "lunch"), (15, 2, "non_trading_day"), (17, 8, "closed")])
def test_no_matching_during_lunch_weekend_or_closed_session(account, day, hour, expected):
    connection, store, path = account
    approve(store, day)
    now = instant(day=day, hour=hour, second=6)
    store.broker.place_order("manual", "sz.000001", "buy", 100, "10", instant(day=day, hour=hour), now.date())
    worker = ManagedWorker(connection, path, fetcher=lambda *a, **k: [quote(now)])
    assert worker.process_account(store.tenant_id, now=now)["state"] == expected
    assert store.state()["fills"] == []
    assert store.state()["quotes"]


def test_prior_day_reservations_expire_even_when_quote_source_fails(account):
    connection, store, path = account
    store.broker.place_order("yesterday", "sz.000001", "buy", 100, "10", instant(day=16), instant(day=16).date())
    def unavailable(*args, **kwargs):
        raise TimeoutError("external quote source unavailable")
    worker = ManagedWorker(connection, path, fetcher=unavailable)
    result = worker.process_account(store.tenant_id, now=instant())
    assert result["state"] == "failed"
    assert store.state()["orders"][0]["status"] == "expired"
    assert store.state()["account"]["reserved_cash"] == 0
    assert store.state()["worker"]["error_type"] == "TimeoutError"


def test_state_changes_notify_without_using_global_environment_credentials(account, monkeypatch):
    connection, store, path = account
    monkeypatch.setenv("FEISHU_WEBHOOK_URL", "https://open.feishu.cn/open-apis/bot/v2/hook/other-user")
    sent = []
    def notification(text, **credentials):
        sent.append((text, credentials))
        return {"status": "disabled"}
    worker = ManagedWorker(connection, path, fetcher=lambda *a, **k: [quote(instant())], notifier=notification)
    worker.process_account(store.tenant_id, now=instant())
    worker.process_account(store.tenant_id, now=instant(second=1))
    text, credentials = sent[0]
    assert len(sent) == 1 and credentials == {"webhook_url": "", "signing_secret": ""}
    assert "观察时间：2026-11-17 10:00:00（北京时间）" in text
    assert store.state()["worker"]["state"] == "awaiting_review"


def test_new_daily_report_sends_ledger_summary_once_without_retrying_unknown_delivery(account):
    connection, store, path = account
    sent = []
    def notification(text, **credentials):
        sent.append((text, credentials))
        return {"status": "failed", "error": "transport_error_delivery_unknown"}
    worker = ManagedWorker(connection, path, fetcher=lambda *a, **k: [quote(instant())], notifier=notification)
    worker.process_account(store.tenant_id, now=instant())
    sent.clear()
    result = worker.process_account(store.tenant_id, now=instant(hour=8))
    assert result["daily_reports_created"] == ["2026-11-17"]
    assert len(sent) == 1
    text, credentials = sent[0]
    assert "[模拟交易日报]" in text
    assert "生成时间：2026-11-17 16:00:00（北京时间）" in text
    assert "期末现金：10000.00" in text
    assert "当日成交：0 笔" in text
    assert "账本对账：通过" in text
    assert "收益率：不可用" in text
    assert "关注列表：sz.000001" in text
    assert credentials == {"webhook_url": "", "signing_secret": ""}
    # A second cycle and a new worker instance cannot re-notify a persisted day.
    assert worker.process_account(store.tenant_id, now=instant(hour=8, second=1))["daily_reports_created"] == []
    restarted = ManagedWorker(connection, path, fetcher=lambda *a, **k: [quote(instant())], notifier=notification)
    assert restarted.process_account(store.tenant_id, now=instant(hour=8, second=6))["daily_reports_created"] == []
    assert len(sent) == 1
    assert store.state()["rules"] == {}
    assert store.state()["fills"] == []
    receipt = list_daily_reports(connection, store.tenant_id)[0]["feishu_delivery"]
    assert receipt["status"] == "unknown" and "recorded_at" in receipt


@pytest.mark.parametrize(("response", "status"), [
    ({"status": "sent"}, "webhook_acknowledged"),
    ({"status": "failed", "error": "webhook_rejected"}, "rejected"),
    ({"status": "failed", "error": "invalid_webhook_response"}, "unknown"),
    ({"status": "disabled"}, "disabled"),
])
def test_new_daily_report_records_only_sanitized_delivery_events(account, response, status):
    connection, store, path = account
    sent = []

    def notification(text, **_credentials):
        observed = list_daily_reports(connection, store.tenant_id)[0]["feishu_delivery"]
        assert observed["status"] == "unknown" and "recorded_at" not in observed
        sent.append(text)
        return response

    worker = ManagedWorker(connection, path, fetcher=lambda *a, **k: [quote(instant())], notifier=notification)
    worker.process_account(store.tenant_id, now=instant(hour=8))
    assert len(sent) == 1
    assert list_daily_reports(connection, store.tenant_id)[0]["feishu_delivery"]["status"] == status
    assert list_daily_reports(connection, "other-tenant") == []
    with connection, connection.cursor() as cursor:
        cursor.execute("SELECT event,details FROM qd_ashare_audit WHERE event IN (%s,%s) ORDER BY id",
                       (DAILY_FEISHU_ATTEMPT, DAILY_FEISHU_RESULT))
        (attempt_event, intent), (result_event, receipt) = cursor.fetchall()
    assert attempt_event == DAILY_FEISHU_ATTEMPT and result_event == DAILY_FEISHU_RESULT
    assert intent == {"attempt_id": receipt["attempt_id"], "trading_days": ["2026-11-17"],
                      "summary_sha256": sha256(sent[0].encode()).hexdigest()}
    assert receipt == {**intent, "status": status}


def test_worker_interruption_keeps_ambiguous_daily_delivery_without_replaying(account):
    connection, store, path = account
    attempts = []

    def interruption(*args, **kwargs):
        attempts.append("interrupted")
        raise KeyboardInterrupt

    worker = ManagedWorker(connection, path, fetcher=lambda *a, **k: [quote(instant())], notifier=interruption)
    with pytest.raises(KeyboardInterrupt):
        worker.process_account(store.tenant_id, now=instant(hour=8))
    restarted = ManagedWorker(connection, path, fetcher=worker.fetcher, notifier=interruption)
    restarted.process_account(store.tenant_id, now=instant(hour=8, second=8))
    assert attempts == ["interrupted"]
    receipt = list_daily_reports(connection, store.tenant_id)[0]["feishu_delivery"]
    assert receipt["status"] == "unknown" and "recorded_at" not in receipt


def test_new_fill_and_backlogged_daily_report_get_separate_notices(account, monkeypatch):
    connection, store, path = account
    # The prior day is observed but its daily report is not generated until the next cycle.
    first = ManagedWorker(connection, path, fetcher=lambda *a, **k: [quote(instant())], notifications_enabled=False)
    assert first.process_account(store.tenant_id, now=instant())["daily_reports_created"] == []
    now = instant(day=18, second=6)
    store.broker.place_order("new-paper-fill", "sz.000001", "buy", 100, "10", instant(day=18), now.date())
    approve(store, day=18)
    monkeypatch.setenv("SECRET_KEY", "test-fixture-not-a-secret-with-enough-bytes")
    webhook = "https://open.feishu.cn/open-apis/bot/v2/hook/owner-fixture"
    store.configure_notifications(webhook, "fixture-secret")
    sent = []

    def notifier(text, **credentials):
        assert credentials == {"webhook_url": webhook, "signing_secret": "fixture-secret"}
        sent.append(text)
        if "[模拟成交]" in text:
            with connection, connection.cursor() as cursor:
                cursor.execute("SELECT event FROM qd_ashare_audit WHERE event IN (%s,%s)", (TRADE_FEISHU_ATTEMPT, TRADE_FEISHU_RESULT))
                assert cursor.fetchall() == [(TRADE_FEISHU_ATTEMPT,)]
        return {"status": "sent"}

    worker = ManagedWorker(connection, path, fetcher=lambda *a, **k: [quote(now)], notifier=notifier)
    result = worker.process_account(store.tenant_id, now=now)
    assert len(result["fills"]) == 1 and result["daily_reports_created"] == ["2026-11-17"]
    assert len(sent) == 2
    trade, daily = sent
    assert "[模拟成交]" in trade and "[模拟交易日报]" in daily
    assert "2026-11-18 10:00:06（北京时间） · 买入 sz.000001" in trade
    assert "100 股 × 10.00 元 · 成交额 1000.00 元 · 费用 5.01 元" in trade
    assert list_daily_reports(connection, store.tenant_id)[0]["feishu_delivery"]["status"] == "webhook_acknowledged"
    with connection, connection.cursor() as cursor:
        cursor.execute("SELECT event,details FROM qd_ashare_audit WHERE event IN (%s,%s) ORDER BY id",
                       (TRADE_FEISHU_ATTEMPT, TRADE_FEISHU_RESULT))
        (attempt_event, attempt), (result_event, receipt) = cursor.fetchall()
    assert (attempt_event, result_event) == (TRADE_FEISHU_ATTEMPT, TRADE_FEISHU_RESULT)
    assert attempt["fill_ids"] == [str(result["fills"][0]["id"])]
    assert attempt["summary_sha256"] == sha256(trade.encode()).hexdigest()
    assert receipt == {**attempt, "status": "webhook_acknowledged"}
    assert result["trade_notifications"] == [receipt]


@pytest.mark.parametrize("interrupted", [False, True])
def test_committed_fill_notice_is_never_retried_after_unknown_delivery(account, monkeypatch, interrupted):
    connection, store, path = account
    monkeypatch.setenv("SECRET_KEY", "test-fixture-not-a-secret-with-enough-bytes")
    store.configure_notifications("https://open.feishu.cn/open-apis/bot/v2/hook/owner-fixture")
    approve(store)
    now = instant(second=6)
    store.broker.place_order("unknown-paper-fill", "sz.000001", "buy", 100, "10", instant(), now.date())
    sent = []

    def notifier(text, **_credentials):
        sent.append(text)
        if interrupted:
            raise KeyboardInterrupt
        return {"status": "failed", "error": "transport_error_delivery_unknown"}

    worker = ManagedWorker(connection, path, fetcher=lambda *a, **k: [quote(now)], notifier=notifier)
    if interrupted:
        with pytest.raises(KeyboardInterrupt):
            worker.process_account(store.tenant_id, now=now)
        fills = store.state()["fills"]
    else:
        current = worker.process_account(store.tenant_id, now=now)
        fills = current["fills"]
        assert current["trade_notifications"][0]["status"] == "unknown"
    assert len(fills) == 1 and len(sent) == 1
    assert notify_new_fills(store, fills, now, notifier) == []
    restarted = ManagedWorker(connection, path, fetcher=worker.fetcher, notifier=notifier)
    assert restarted.process_account(store.tenant_id, now=instant(second=7))["fills"] == []
    assert len(sent) == 1
    with connection, connection.cursor() as cursor:
        cursor.execute("SELECT event FROM qd_ashare_audit WHERE event IN (%s,%s) ORDER BY id",
                       (TRADE_FEISHU_ATTEMPT, TRADE_FEISHU_RESULT))
        expected = [(TRADE_FEISHU_ATTEMPT,)]
        if not interrupted:
            expected.append((TRADE_FEISHU_RESULT,))
        assert cursor.fetchall() == expected


def test_old_unnotified_fill_stays_archived_and_new_sell_reports_its_actual_fee(account, monkeypatch):
    connection, store, path = account
    approve(store)
    buy_time = instant(second=6)
    store.broker.place_order("historical-buy", "sz.000001", "buy", 100, "10", instant(), buy_time.date())
    earlier = ManagedWorker(connection, path, fetcher=lambda *a, **k: [quote(buy_time)], notifications_enabled=False)
    archived = earlier.process_account(store.tenant_id, now=buy_time)["fills"]
    assert len(archived) == 1
    monkeypatch.setenv("SECRET_KEY", "test-fixture-not-a-secret-with-enough-bytes")
    store.configure_notifications("https://open.feishu.cn/open-apis/bot/v2/hook/owner-fixture")
    sent = []

    def notifier(text, **_credentials):
        sent.append(text)
        return {"status": "sent"}

    sell_time = instant(day=18, second=6)
    assert notify_new_fills(store, archived, sell_time, notifier) == []
    assert not sent
    approve(store, day=18)
    store.broker.place_order("new-paper-sell", "sz.000001", "sell", 100, "9.99", instant(day=18), sell_time.date())
    upgraded = ManagedWorker(connection, path, fetcher=lambda *a, **k: [quote(sell_time)], notifier=notifier)
    result = upgraded.process_account(store.tenant_id, now=sell_time)
    trade, = [text for text in sent if "[模拟成交]" in text]
    assert len(result["fills"]) == 1 and result["fills"][0]["side"] == "sell"
    assert result["trade_notifications"][0]["fill_ids"] == [str(result["fills"][0]["id"])]
    assert "2026-11-18 10:00:06（北京时间） · 卖出 sz.000001" in trade
    assert "100 股 × 9.99 元 · 成交额 999.00 元 · 费用 5.51 元" in trade


def test_database_connection_reconnect_uses_capped_backoff(tmp_path):
    from app.ashare.managed_worker import run_forever
    import psycopg2
    class StopAfterRetries:
        waits = []
        def is_set(self):
            return len(self.waits) == 7
        def wait(self, seconds):
            self.waits.append(seconds)
    stop = StopAfterRetries()
    def unavailable(*args, **kwargs):
        raise psycopg2.OperationalError("do not print connection secret")
    assert run_forever("never-log-this-dsn", tmp_path, connect=unavailable, stop_event=stop) == 0
    assert stop.waits == [1, 2, 4, 8, 16, 30, 30]


def test_one_accounts_quote_failure_does_not_prevent_other_account_cycle(account):
    connection, store, path = account
    second = WebStore(connection, "other_" + uuid.uuid4().hex)
    second.initialize("10000")
    second.save_settings(["sh.600000"], 5)
    def source(symbols, *args, **kwargs):
        if symbols == ["sz.000001"]:
            raise TimeoutError("failed source")
        return [{**quote(instant()), "symbol": "sh.600000", "name": "浦发银行"}]
    worker = ManagedWorker(connection, path, fetcher=source)
    results = {result["tenant_id"]: result for result in worker.cycle(now=instant())}
    assert results[store.tenant_id]["state"] == "failed"
    assert results[second.tenant_id]["state"] == "awaiting_review"
    assert second.state()["quotes"][0]["symbol"] == "sh.600000"


def test_review_updated_during_quote_request_is_reloaded_before_matching(account):
    connection, store, path = account
    approve(store)
    store.broker.place_order("manual", "sz.000001", "buy", 100, "10", instant(), instant().date())
    def source(*args, **kwargs):
        rule = store.state()["rules"]["sz.000001"]
        store.review({**rule, "suspended": True})
        return [quote(instant(second=6))]
    worker = ManagedWorker(connection, path, fetcher=source)
    assert worker.process_account(store.tenant_id, now=instant(second=6))["state"] == "degraded"
    assert store.state()["fills"] == []


def test_prior_day_rule_is_not_automatically_promoted_to_new_day(account):
    connection, store, path = account
    approve(store)
    now = instant(day=18, second=6)
    store.broker.place_order("manual", "sz.000001", "buy", 100, "10", instant(day=18), now.date())
    worker = ManagedWorker(connection, path, fetcher=lambda *a, **k: [quote(now)])
    assert worker.process_account(store.tenant_id, now=now)["state"] == "awaiting_review"
    assert store.state()["rules"]["sz.000001"]["trading_day"] == "2026-11-17"
    assert store.state()["fills"] == []


def test_loop_recovers_database_connection_then_completes_bounded_cycle(account, capsys):
    from app.ashare.managed_worker import run_forever
    import psycopg2
    connection, store, path = account
    with connection, connection.cursor() as cursor:
        cursor.execute("DELETE FROM qd_ashare_web WHERE tenant_id=%s", (store.tenant_id,))
        cursor.execute("SELECT current_schema()")
        schema = cursor.fetchone()[0]
    attempts = []
    class ImmediateWait:
        waits = []
        def is_set(self):
            return False
        def wait(self, seconds):
            self.waits.append(seconds)
    wait = ImmediateWait()
    def reconnect(dsn, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise psycopg2.OperationalError("hidden credential text")
        return psycopg2.connect(os.environ["TEST_ASHARE_DATABASE_URL"], options="-c search_path=" + schema + " " + kwargs.pop("options", ""), **kwargs)
    assert run_forever("not-a-real-dsn", path, cycles=1, connect=reconnect, stop_event=wait) == 0
    assert len(attempts) == 2
    assert wait.waits == [1]
    output = capsys.readouterr().out
    assert "database_reconnect" in output and '"accounts": 0' in output
    assert "hidden credential" not in output and "not-a-real-dsn" not in output


def strategy_fixture(account, *, quantity=100, cap='1100'):
    conn, _, path = account
    actor = 42
    store = WebStore(conn, f'user_{actor}')
    store.initialize('10000')
    versions = StrategyVersions(conn, store.tenant_id, store.account_id, clock=lambda: instant(second=7))
    config = {'mode': 'paper', 'kind': 'buy_once_below', 'symbol': 'sz.000001',
              'quantity': quantity, 'threshold': '10.10', 'limit_price': '10', 'max_cash': cap}
    draft = versions.create(config, 'test-managed-strategy', actor)

    def decide(action):
        return versions.decide(draft['id'], draft['config_sha256'], action, True, actor)

    def rules(**changes):
        store.review({'symbol': 'sz.000001', 'reviewed': True, 'evidence': 'controlled fixture',
                      'trading_day': '2026-11-17', 'is_trading_day': True,
                      'suspended': False, 'st': False, 'limit_up': '11', 'limit_down': '9',
                      'approval_method': 'explicit_human_review', 'reviewed_at': instant().isoformat(),
                      'reviewed_by': actor, **changes})

    def worker(feed):
        return ManagedWorker(conn, path, fetcher=feed, notifications_enabled=False)

    return store, draft, decide, rules, worker


def test_managed_execution_one_order_across_restart_and_revocation_releases_unfilled_remainder(account):
    import psycopg2
    from contextlib import closing
    from psycopg2 import sql

    conn, _, path = account
    store, draft, decide, rules, worker_factory = strategy_fixture(account, quantity=200, cap='2100')
    versions = StrategyVersions(conn, *store.key)
    current = instant()
    tick = current
    worker = worker_factory(lambda *_, **__: [{**quote(tick), 'ask_size': 100}])
    rules()
    with pytest.raises(ValueError, match='invalid_strategy_state_transition'):
        decide('activated')
    assert worker.process_account(store.tenant_id, now=current)['strategy_submitted'] == []
    assert decide('approved')['execution_allowed'] is False
    current = tick = instant(second=6)
    assert worker.process_account(store.tenant_id, now=current)['strategy_submitted'] == []

    activated = decide('activated')
    assert activated['execution_allowed'] is True and activated['activated_at'] == instant(second=7)
    assert activated['order'] is None
    assert decide('activated') == activated
    current = instant(second=12)
    # The latest source tick predates activation and must not trigger a new order.
    assert worker.process_account(store.tenant_id, now=current)['strategy_submitted'] == []
    current = tick = instant(second=18)
    fired = worker.process_account(store.tenant_id, now=current)
    assert len(fired['strategy_submitted']) == 1
    order_id = fired['strategy_submitted'][0]
    assert fired['fills'] == []
    placed = versions.list(20)['versions'][0]['order']
    assert placed == {'id': str(order_id), 'status': 'open', 'filled': 0, 'quantity': 200,
                      'submitted_at': current.isoformat(), 'filled_amount': '0.00', 'fees': '0.00',
                      'reserved_cash': '2005.02'}
    state = store.state()
    assert float(state['account']['reserved_cash']) == 2005.02
    audit = next(row['details'] for row in state['audit'] if row['event'] == 'approved_strategy_order_placed')
    assert audit['version_id'] == draft['id'] and audit['evidence_sha256'] == 'a' * 64

    with conn, conn.cursor() as cur:
        cur.execute('SELECT current_schema()')
        schema = cur.fetchone()[0]
        cur.execute('SELECT pg_advisory_unlock_all()')
    with closing(psycopg2.connect(os.environ['TEST_ASHARE_DATABASE_URL'])) as replacement:
        with replacement, replacement.cursor() as cur:
            cur.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
        current = tick = instant(second=24)
        restarted = ManagedWorker(replacement, path, fetcher=worker.fetcher, notifications_enabled=False)
        fills = restarted.process_account(store.tenant_id, now=current)
        assert fills['strategy_submitted'] == [] and fills['fills'][0]['quantity'] == 100
    partial = versions.list(20)['versions'][0]['order']
    assert partial == {**placed, 'status': 'partial', 'filled': 100, 'filled_amount': '1000.00',
                       'fees': '5.01', 'reserved_cash': '1000.01'}
    revoked = decide('revoked')
    assert revoked['execution_allowed'] is False
    assert revoked['order'] == {**partial, 'status': 'cancelled', 'reserved_cash': '0.00'}
    state = store.state()
    assert state['orders'][0]['id'] == order_id and state['orders'][0]['status'] == 'cancelled'
    assert state['orders'][0]['filled'] == 100 and state['account']['reserved_cash'] == 0
    current = tick = instant(second=30)
    later = worker.process_account(store.tenant_id, now=current)
    assert later['strategy_submitted'] == later['fills'] == []
    assert len(store.state()['orders']) == len(store.state()['fills']) == 1


def test_strategy_order_view_uses_exact_ledger_key_and_retains_expired_order(account):
    from decimal import Decimal
    from app.ashare.broker import STRATEGY_ORDER_PREFIX

    store, draft, decide, rules, worker_factory = strategy_fixture(account)
    versions = StrategyVersions(store.connection, *store.key)
    other = StrategyVersions(store.connection, account[1].tenant_id, account[1].account_id)
    store.broker.place_order('manual-same-symbol', draft['config']['symbol'], 'buy', 100,
                             Decimal('9.50'), instant(), instant().date())
    with store.connection, store.connection.cursor() as cursor:
        cursor.execute("""INSERT INTO qd_ashare_orders
            (tenant_id,account_id,client_order_id,symbol,side,quantity,limit_price,submitted_at,trading_day,status)
            VALUES (%s,%s,%s,%s,'buy',100,10,%s,%s,'expired')""",
                       (*other.key, STRATEGY_ORDER_PREFIX + draft['id'], draft['config']['symbol'],
                        instant(), instant().date()))
    rules()
    decide('approved')
    assert decide('activated')['order'] is None
    assert versions.list(20)['versions'][0]['order'] is None
    assert other.list(20) == {'versions': []}
    now = instant(second=20)
    worker = worker_factory(lambda *_, **__: [quote(now)])
    submitted = worker.process_account(store.tenant_id, now=now)['strategy_submitted']
    assert len(submitted) == 1
    close = worker.process_account(store.tenant_id, now=instant(hour=7, second=1))
    assert submitted[0] in close['expired_day_orders']
    result = versions.list(20)['versions'][0]['order']
    assert result['id'] == str(submitted[0]) and result['status'] == 'expired'
    assert result['filled'] == 0 and result['reserved_cash'] == '0.00'


def test_automatic_order_waits_if_human_review_changes_before_matching(account):
    store, _, decide, rules, worker_factory = strategy_fixture(account)
    rules()
    decide('approved')
    decide('activated')
    worker = worker_factory(lambda *_, **__: [quote(instant(second=10))])
    worker.process_account(store.tenant_id, now=instant(second=10))
    previous_rules = store.state()['rules']['sz.000001']
    rules(evidence='renewed human review')
    received = instant(second=16)
    assert store.broker.on_quote(quote(received), previous_rules, received) == []
    assert store.state()['orders'][0]['status'] == 'open'
    current_rules = store.state()['rules']['sz.000001']
    received = instant(second=22)
    assert len(store.broker.on_quote(quote(received), current_rules, received)) == 1


@pytest.mark.parametrize('reason', ['unreviewed', 'previous_day', 'untrusted_reviewer', 'unknown_status', 'stale', 'fees_exceed_cap'])
def test_managed_strategy_does_not_bypass_rule_quote_or_cash_gates(account, reason):
    store, _, decide, rules, worker_factory = strategy_fixture(account)
    decide('approved')
    decide('activated')
    if reason == 'fees_exceed_cap':
        conn = account[0]
        with conn, conn.cursor() as cursor:
            cursor.execute("""UPDATE qd_ashare_accounts
                SET fee_policy=fee_policy || '{"minimum_commission":"200"}'
                WHERE tenant_id=%s AND account_id=%s""", store.key)
    if reason != 'unreviewed':
        adjustments = {'previous_day': {'trading_day': '2026-11-16'},
                       'untrusted_reviewer': {'reviewed_by': 999}, 'unknown_status': {'suspended': None}}
        rules(**adjustments.get(reason, {}))
    now = instant(second=30)
    worker = worker_factory(lambda *_, **__: [quote(instant(second=6) if reason == 'stale' else now)])
    result = worker.process_account(store.tenant_id, now=now)
    assert result['strategy_submitted'] == [] and store.state()['orders'] == []
    assert store.state()['account']['reserved_cash'] == 0
    if reason == 'fees_exceed_cap':
        assert result['strategy_rejections'][0]['reason'] == 'strategy_cash_cap_exceeded'
        assert result['state'] == 'healthy'
