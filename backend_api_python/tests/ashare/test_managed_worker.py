"""Managed account acceptance using real PostgreSQL and an external quote feed."""
import os
from datetime import datetime, timezone
from pathlib import Path
import uuid

import pytest

from app.ashare.managed_worker import ManagedWorker
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
        for name in ("20261117_ashare_paper.sql", "20261117_ashare_web.sql", "20261117_ashare_operations.sql"):
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
        sent.append(credentials)
        return {"status": "disabled"}
    worker = ManagedWorker(connection, path, fetcher=lambda *a, **k: [quote(instant())], notifier=notification)
    worker.process_account(store.tenant_id, now=instant())
    worker.process_account(store.tenant_id, now=instant(second=1))
    assert sent == [{"webhook_url": "", "signing_secret": ""}]
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
