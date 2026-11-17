"""Real PostgreSQL checks for configured quote-to-paper execution cycles."""

import json
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app.ashare.broker import PaperBroker
from app.ashare.runtime import load_config, process_quotes


def instant(second=0, day=17):
    return datetime(2026, 11, day, 2, 0, second, tzinfo=timezone.utc)


def sample_quote(second=0, day=17):
    return {
        "symbol": "sz.000001", "name": "平安银行",
        "exchange_time": instant(second, day).isoformat(),
        "received_at": instant(second, day).isoformat(),
        "last": "10", "bid": "9.99", "ask": "10", "bid_size": 100, "ask_size": 100,
        "previous_close": "10", "volume": 100000, "open": "10", "high": "10.1", "low": "9.9",
        "limit_up": "11", "limit_down": "9", "source": "tencent_web",
        "quote_id": f"runtime-{day}-{second}", "evidence_sha256": "a" * 64,
    }


@pytest.fixture
def config_file(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({
        "mode": "paper", "ai_auto_promotion": False,
        "tenant_id": "runtime-" + uuid.uuid4().hex, "account_id": "paper",
        "symbols": ["sz.000001"], "initial_cash": "10000",
        "rules": {"sz.000001": {
            "reviewed": True, "evidence": "explicit-synthetic-test-fixture",
            "trading_day": "2026-11-17", "is_trading_day": True,
            "suspended": False, "st": False, "limit_up": "11", "limit_down": "9",
        }},
        "strategy": {"enabled": True, "kind": "buy_once_below", "approved_by_operator": True,
                     "id": "approved-example", "symbol": "sz.000001", "quantity": 100,
                     "threshold": "10.05", "limit_price": "10.00"},
    }), encoding="utf-8")
    return path


@pytest.fixture
def runtime_account(config_file):
    dsn = os.environ.get("TEST_ASHARE_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_ASHARE_DATABASE_URL required for PostgreSQL integration")
    import psycopg2
    connection = psycopg2.connect(dsn)
    with connection, connection.cursor() as cursor:
        cursor.execute((Path(__file__).parents[2] / "migrations/20261117_ashare_paper.sql").read_text())
    config = load_config(config_file)
    broker = PaperBroker(connection, config["tenant_id"], config["account_id"])
    broker.initialize(Decimal(config["initial_cash"]))
    yield broker, config
    connection.close()


def test_stale_quote_cannot_trigger_approved_strategy(runtime_account):
    broker, config = runtime_account
    result = process_quotes(broker, config, [sample_quote()], instant(30))
    assert result["state"] == "degraded"
    assert "stale" in result["rejections"][0]["reason"]
    assert result["submitted"] == result["fills"] == []
    assert broker.snapshot()["orders"] == []
    assert broker.snapshot()["account"]["reserved_cash"] == 0


def test_approved_strategy_uses_only_later_quote_and_survives_new_connection(runtime_account):
    import psycopg2
    broker, config = runtime_account
    first = process_quotes(broker, config, [sample_quote(0)], instant(1))
    assert first["state"] == "healthy"
    assert len(first["submitted"]) == 1
    assert first["fills"] == []
    assert broker.snapshot()["account"]["cash"] == Decimal("10000.00")
    assert broker.snapshot()["account"]["reserved_cash"] == Decimal("1005.01")

    same_time = process_quotes(broker, config, [sample_quote(1)], instant(2))
    assert same_time["submitted"] == same_time["fills"] == []
    connection = psycopg2.connect(os.environ["TEST_ASHARE_DATABASE_URL"])
    try:
        restarted = PaperBroker(connection, config["tenant_id"], config["account_id"])
        later = process_quotes(restarted, config, [sample_quote(2)], instant(3))
        assert len(later["fills"]) == 1
        assert later["fills"][0]["quantity"] == 100
        assert later["submitted"] == []
        assert later["account"]["account"]["cash"] == Decimal("8994.99")
        duplicate = process_quotes(restarted, config, [sample_quote(2)], instant(3))
        assert duplicate["submitted"] == duplicate["fills"] == []
        subsequent = process_quotes(restarted, config, [sample_quote(4)], instant(5))
        assert subsequent["submitted"] == subsequent["fills"] == []
        assert len(restarted.snapshot()["orders"]) == len(restarted.snapshot()["fills"]) == 1
        assert restarted.snapshot()["positions"][0]["quantity"] == 100
    finally:
        connection.close()


def test_expired_rules_do_not_place_or_match_orders(runtime_account):
    broker, config = runtime_account
    result = process_quotes(broker, config, [sample_quote(day=18)], instant(day=18))
    assert result["state"] == "degraded"
    assert "expired" in result["rejections"][0]["reason"]
    assert broker.snapshot()["orders"] == []


@pytest.mark.parametrize("change", [{"name": "*ST sample"}, {"limit_up": "11.01"}])
def test_quote_rules_contradictions_do_not_submit(runtime_account, change):
    broker, config = runtime_account
    quote = sample_quote()
    quote.update(change)
    result = process_quotes(broker, config, [quote], instant())
    assert result["state"] == "degraded"
    assert "contradicts" in result["rejections"][0]["reason"]
    assert broker.snapshot()["orders"] == []


def test_missing_data_degrades_and_recovers_without_invented_quote(runtime_account):
    broker, config = runtime_account
    missing = process_quotes(broker, config, [], instant())
    assert missing["state"] == "degraded"
    assert missing["rejections"] == [{"symbol": "sz.000001", "reason": "missing_quote"}]
    assert missing["submitted"] == missing["fills"] == []
    resumed = process_quotes(broker, config, [sample_quote(1)], instant(2))
    assert resumed["state"] == "healthy"
    assert len(resumed["submitted"]) == 1
    assert resumed["fills"] == []


def test_strategy_limit_outside_reviewed_band_cannot_freeze_cash(runtime_account, config_file):
    broker, _ = runtime_account
    candidate = json.loads(config_file.read_text())
    candidate["strategy"]["limit_price"] = "11.01"
    config_file.write_text(json.dumps(candidate))
    config = load_config(config_file)
    result = process_quotes(broker, config, [sample_quote()], instant(1))
    assert result["state"] == "degraded"
    assert result["submitted"] == []
    assert broker.snapshot()["orders"] == []
    assert broker.snapshot()["account"]["reserved_cash"] == 0


def test_disabled_strategy_only_monitors(runtime_account, config_file):
    broker, _ = runtime_account
    candidate = json.loads(config_file.read_text())
    candidate["strategy"]["enabled"] = False
    config_file.write_text(json.dumps(candidate))
    result = process_quotes(broker, load_config(config_file), [sample_quote()], instant(1))
    assert result["state"] == "healthy"
    assert result["submitted"] == result["fills"] == []
    assert broker.snapshot()["orders"] == []
