"""PostgreSQL acceptance checks for the public paper broker boundary."""
import os
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app.ashare.broker import PaperBroker


@pytest.fixture
def broker():
    dsn = os.getenv("TEST_ASHARE_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_ASHARE_DATABASE_URL is required for PostgreSQL integration")
    import psycopg2
    connection = psycopg2.connect(dsn)
    with connection, connection.cursor() as cursor:
        cursor.execute((Path(__file__).parents[2] / "migrations/20261117_ashare_paper.sql").read_text())
    instance = PaperBroker(connection, "test-" + uuid.uuid4().hex, "paper")
    instance.initialize(Decimal("10000"))
    yield instance
    connection.close()


def instant(day=17, second=0):
    return datetime(2026, 11, day, 2, 0, second, tzinfo=timezone.utc)


def place(broker, key="one", side="buy", quantity=100, price="10", day=17):
    return broker.place_order(key, "sz.000001", side, quantity, Decimal(price), instant(day), date(2026, 11, day))


def test_cash_reservation_and_cancel_are_persistent(broker):
    order = place(broker)
    assert broker.snapshot()["account"]["reserved_cash"] == Decimal("1005.01")
    restarted = PaperBroker(broker.connection, broker.tenant_id, broker.account_id)
    assert restarted.snapshot()["account"]["available_cash"] == Decimal("8994.99")
    assert restarted.cancel(order["id"])["status"] == "cancelled"
    assert restarted.snapshot()["account"]["available_cash"] == Decimal("10000")


def quote(day=17, second=1, size=100):
    return {
        "symbol": "sz.000001", "exchange_time": instant(day, second).isoformat(),
        "received_at": instant(day, second).isoformat(), "last": "10", "bid": "9.99", "ask": "10",
        "bid_size": size, "ask_size": size, "previous_close": "10", "volume": 100000,
        "open": "10", "high": "10.1", "low": "9.9",
        "limit_up": "11", "limit_down": "9", "source": "tencent_web",
        "quote_id": f"quote-{day}-{second}", "evidence_sha256": "a" * 64,
    }


def rules(day=17):
    return {"trading_day": f"2026-11-{day}", "reviewed": True, "is_trading_day": True,
            "suspended": False, "st": False, "limit_up": "11", "limit_down": "9"}


def match(broker, day=17, second=1, size=100):
    return broker.on_quote(quote(day, second, size), rules(day), instant(day, second))


def test_partial_fills_cash_t1_and_restart_dedup(broker):
    order = place(broker, quantity=200)
    fills = match(broker)
    assert [fill["quantity"] for fill in fills] == [100]
    state = broker.snapshot()
    assert state["account"]["cash"] == Decimal("8994.99")
    assert state["account"]["reserved_cash"] == Decimal("1000.01")
    assert state["orders"][0]["status"] == "partial"
    with pytest.raises(ValueError, match="T\\+1"):
        place(broker, "same-day-sell", side="sell")
    restarted = PaperBroker(broker.connection, broker.tenant_id, broker.account_id)
    assert match(restarted) == []
    assert match(restarted, second=2)[0]["fee"] == Decimal("0.01")
    assert restarted.snapshot()["account"]["cash"] == Decimal("7994.98")
    assert restarted.snapshot()["orders"][0]["commission"] == Decimal("5.00")
    assert restarted.snapshot()["account"]["total_commission"] == Decimal("5.00")
    assert restarted.snapshot()["account"]["total_fees"] == Decimal("5.02")
    sell = place(restarted, "next-day-sell", side="sell", day=18, price="9.99", quantity=200)
    assert match(restarted, day=18, size=200)[0]["quantity"] == 200
    assert restarted.snapshot()["positions"] == []
    assert restarted.cancel(order["id"])["status"] == "filled"
    assert sell["side"] == "sell"


def test_atomic_cash_reservation_and_idempotency(broker):
    one = place(broker, quantity=900)
    assert place(broker, quantity=900)["id"] == one["id"]
    with pytest.raises(ValueError, match="payload conflict"):
        place(broker, quantity=800)
    with pytest.raises(ValueError, match="cash"):
        place(broker, "overbook", quantity=100)
    assert len(broker.snapshot()["orders"]) == 1


def test_depth_shared_across_orders_and_same_exchange_time(broker):
    place(broker)
    place(broker, "two")
    assert len(match(broker, size=100)) == 1
    changed = quote()
    changed["quote_id"] = "different-raw-poll-same-exchange-time"
    assert broker.on_quote(changed, rules(), instant(second=1)) == []
    assert len(match(broker, second=2)) == 1


def test_no_fill_on_submission_quote_stale_or_unreviewed(broker):
    place(broker)
    assert match(broker, second=0) == []
    with pytest.raises(ValueError):
        broker.on_quote(quote(), rules(), instant(second=20))
    invalid = rules()
    invalid["reviewed"] = False
    with pytest.raises(ValueError):
        broker.on_quote(quote(second=2), invalid, instant(second=2))
    assert broker.snapshot()["fills"] == []


def test_price_limits_and_zero_book_fail_closed(broker):
    place(broker, price="11.01")
    assert match(broker) == []
    with pytest.raises(ValueError, match="book"):
        match(broker, second=2, size=0)
    bad = rules()
    bad["limit_up"] = "10.99"
    with pytest.raises(ValueError, match="disagree"):
        broker.on_quote(quote(second=3), bad, instant(second=3))


def test_prior_day_order_expires_releasing_funds(broker):
    place(broker)
    assert match(broker, day=18) == []
    assert broker.snapshot()["orders"][0]["status"] == "expired"
    assert broker.snapshot()["account"]["reserved_cash"] == 0


def test_tenant_cannot_read_or_cancel_other_account(broker):
    one = place(broker)
    other = PaperBroker(broker.connection, "other-" + uuid.uuid4().hex, broker.account_id)
    other.initialize(Decimal("1000"))
    with pytest.raises(ValueError, match="unknown order"):
        other.cancel(one["id"])
    assert other.snapshot()["orders"] == []
    assert other.snapshot()["positions"] == []


@pytest.mark.parametrize("symbol", ["sh.688001", "bj.920001", "sz.159001", "sh.000001"])
def test_unsupported_boards_and_instruments_rejected(broker, symbol):
    with pytest.raises(ValueError, match="unsupported"):
        broker.place_order("invalid", symbol, "buy", 100, Decimal("10"), instant(), date(2026, 11, 17))


def test_competing_connections_cannot_overspend(broker):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    import psycopg2

    barrier = Barrier(2)

    def submit(key):
        connection = psycopg2.connect(os.environ["TEST_ASHARE_DATABASE_URL"])
        instance = PaperBroker(connection, broker.tenant_id, broker.account_id)
        try:
            barrier.wait(timeout=5)
            place(instance, key, quantity=600)
            return "accepted"
        except ValueError as exc:
            assert "cash" in str(exc)
            return "rejected"
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, ["parallel-one", "parallel-two"]))
    assert sorted(results) == ["accepted", "rejected"]
    assert broker.snapshot()["account"]["reserved_cash"] == Decimal("6005.06")


def test_sell_reservations_prevent_double_sale(broker):
    place(broker, quantity=200)
    match(broker, size=200)
    sell = place(broker, "sell-one", side="sell", quantity=200, day=18, price="9.99")
    with pytest.raises(ValueError, match="T\\+1"):
        place(broker, "sell-two", side="sell", quantity=100, day=18)
    broker.cancel(sell["id"])
    assert place(broker, "sell-after-cancel", side="sell", quantity=200, day=18)["status"] == "open"


@pytest.mark.parametrize("change", [{"name": "*ST example"}, {"ask": "10.001"}])
def test_quote_status_contradiction_or_subcent_price_rejected(broker, change):
    place(broker)
    data = quote()
    data.update(change)
    with pytest.raises(ValueError):
        broker.on_quote(data, rules(), instant(second=1))
    assert broker.snapshot()["fills"] == []
