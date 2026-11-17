"""Dated provider evidence, missing states and knowledge-time isolation."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import uuid

import pytest

from app.ashare.securities import FIELDS, SecuritiesStore, fetch_baostock, normalize

DAY = "2026-11-16"
RECEIVED = datetime(2026, 11, 17, 12, tzinfo=timezone.utc)


def payload(day=DAY):
    return {"request": {"trading_day": day, "symbols": ["sz.000001"], "universe_api": "query_all_stock", "bars_api": "query_history_k_data_plus", "frequency": "d", "adjustflag": "3", "fields": FIELDS},
            "universe": {"fields": ["code", "tradeStatus", "code_name"], "rows": [["sz.000001", "1", "平安银行"], ["sh.600000", "0", "浦发银行"], ["sh.000001", "1", "上证指数"]]},
            "bars": {"sz.000001": {"fields": FIELDS.split(","), "rows": [[day, "sz.000001", "10", "11", "9", "10", "10", "100", "1000", "3", "1", "0"]]}},
            "sdk_version": "fixture-not-real-market-data"}


def test_dated_pool_preserves_suspensions_and_excludes_indices():
    members, statuses = normalize(payload(), DAY, ["sz.000001"])
    assert [r["symbol"] for r in members] == ["sz.000001", "sh.600000"]
    assert members[1]["suspended"] is True
    assert statuses[0]["st"] is False and statuses[0]["previous_close"] == "10"
    assert statuses[0]["special_listing_status"] is None


@pytest.mark.parametrize("field", ["tradestatus", "isST"])
def test_empty_status_flag_is_unknown(field):
    source = payload()
    source["bars"]["sz.000001"]["rows"][0][FIELDS.split(",").index(field)] = ""
    _, statuses = normalize(source, DAY, ["sz.000001"])
    assert statuses[0]["suspended" if field == "tradestatus" else "is_st"] is None


def test_missing_bar_is_not_inferred_as_suspension():
    source = payload()
    source["bars"]["sz.000001"]["rows"] = []
    _, statuses = normalize(source, DAY, ["sz.000001"])
    assert statuses[0]["suspended"] is None and statuses[0]["st"] is None
    assert statuses[0]["missing_reason"] == "daily_bar_missing"


@pytest.mark.parametrize("mutation", ["day", "symbol", "adjustment", "flag", "conflict", "duplicate", "scope", "empty", "price"])
def test_inconsistent_or_incomplete_responses_rejected(mutation):
    source = payload()
    row = source["bars"]["sz.000001"]["rows"][0]
    if mutation == "day": row[0] = "2026-11-17"
    if mutation == "symbol": row[1] = "sh.600000"
    if mutation == "adjustment": row[9] = "1"
    if mutation == "flag": row[11] = "2"
    if mutation == "conflict": row[10] = "0"
    if mutation == "duplicate": source["universe"]["rows"].append(source["universe"]["rows"][0])
    if mutation == "scope": source["bars"] = {}
    if mutation == "empty": source["universe"]["rows"] = []
    if mutation == "price": row[6] = "NaN"
    with pytest.raises(ValueError): normalize(source, DAY, ["sz.000001"])


def test_socket_sdk_runs_in_disposable_child_with_timeout(monkeypatch):
    calls = []
    def blocked(args, **kwargs):
        calls.append((args, kwargs))
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])
    monkeypatch.setattr(subprocess, "run", blocked)
    with pytest.raises(ValueError, match="baostock_provider_timeout"):
        fetch_baostock(DAY, ["sz.000001"], 2)
    assert calls[0][1]["timeout"] == 2 and calls[0][0][-1] == "_provider"


@pytest.fixture
def store(tmp_path):
    dsn = os.getenv("TEST_ASHARE_DATABASE_URL")
    if not dsn: pytest.skip("TEST_ASHARE_DATABASE_URL required")
    import psycopg2
    from psycopg2 import sql
    connection = psycopg2.connect(dsn)
    schema = "securities_test_" + uuid.uuid4().hex
    with connection, connection.cursor() as cursor:
        cursor.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        cursor.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        cursor.execute((Path(__file__).parents[2] / "migrations/20261117_ashare_securities.sql").read_text())
    yield SecuritiesStore(connection), tmp_path
    with connection, connection.cursor() as cursor:
        cursor.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
    connection.close()


def test_real_pg_backfill_not_visible_before_receipt_and_no_nextday_fallback(store):
    repository, directory = store
    receipt = repository.archive(payload(), DAY, ["sz.000001"], directory, RECEIVED)
    assert receipt["universe_count"] == 2
    assert repository.status("sz.000001", DAY, "2026-11-17T11:59:59Z") is None
    assert repository.universe(DAY, "2026-11-17T11:59:59Z")["status"] == "not_observed"
    status = repository.status("sz.000001", DAY, RECEIVED)
    assert status["source_time"] is None and status["available_at"] == RECEIVED.isoformat()
    assert status["reviewed"] is False and status["auto_approval_eligible"] is False
    assert status["evidence_sha256"] == hashlib.sha256(Path(receipt["evidence_path"]).read_bytes()).hexdigest()
    assert repository.status("sz.000001", "2026-11-17", RECEIVED) is None
    page = repository.universe(DAY, RECEIVED, limit=1, offset=1)
    assert page["total"] == 2 and len(page["members"]) == 1
    assert page["complete_exchange_universe_certified"] is False


def test_real_pg_revisions_append_and_asof_selects_original(store):
    repository, directory = store
    repository.archive(payload(), DAY, ["sz.000001"], directory, RECEIVED)
    revised = deepcopy(payload())
    revised["bars"]["sz.000001"]["rows"][0][-1] = "1"
    repository.archive(revised, DAY, ["sz.000001"], directory, "2026-11-17T12:01:00Z")
    assert repository.status("sz.000001", DAY, RECEIVED)["is_st"] is False
    assert repository.status("sz.000001", DAY, "2026-11-17T12:01:00Z")["is_st"] is True
    with repository.connection, repository.connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM qd_ashare_security_receipts")
        assert cursor.fetchone()[0] == 2


def test_real_pg_missing_or_corrupt_evidence_fails_closed(store):
    repository, directory = store
    receipt = repository.archive(payload(), DAY, ["sz.000001"], directory, RECEIVED)
    path = Path(receipt["evidence_path"])
    path.write_text("{}")
    with pytest.raises(ValueError, match="hash_mismatch"): repository.status("sz.000001", DAY, RECEIVED)
    with pytest.raises(ValueError, match="hash_mismatch"): repository.universe(DAY, RECEIVED)
    path.unlink()
    with pytest.raises(ValueError, match="unavailable"): repository.status("sz.000001", DAY, RECEIVED)


@pytest.mark.parametrize("day,received", [("2026-11-18", RECEIVED), ("2026-11-17", "2026-11-17T10:59:59Z")])
def test_real_pg_future_and_intraday_eod_rejected(store, day, received):
    repository, directory = store
    with pytest.raises(ValueError): repository.archive(payload(day), day, ["sz.000001"], directory, received)
    assert not list(directory.iterdir())


def test_real_pg_original_decoded_provider_rows_preserved(store):
    repository, directory = store
    source = payload()
    receipt = repository.archive(source, DAY, ["sz.000001"], directory, RECEIVED)
    assert json.loads(Path(receipt["evidence_path"]).read_text()) == source
    assert receipt["source_representation"].endswith("not_wire_bytes")


def test_not_yet_listed_watch_symbol_does_not_invalidate_whole_historical_pool():
    source = payload()
    source["universe"]["rows"] = [row for row in source["universe"]["rows"] if row[0] != "sz.000001"]
    source["bars"]["sz.000001"]["rows"] = []
    members, statuses = normalize(source, DAY, ["sz.000001"])
    assert [row["symbol"] for row in members] == ["sh.600000"]
    assert statuses[0]["missing_reason"] == "not_in_dated_universe"
    assert statuses[0]["suspended"] is None


def test_real_pg_database_details_cannot_disagree_with_original_evidence(store):
    repository, directory = store
    repository.archive(payload(), DAY, ["sz.000001"], directory, RECEIVED)
    with repository.connection, repository.connection.cursor() as cursor:
        cursor.execute("UPDATE qd_ashare_security_statuses SET details=details || %s::jsonb", ('{"is_st":true}',))
    with pytest.raises(ValueError, match="details_mismatch"):
        repository.status("sz.000001", DAY, RECEIVED)
    with repository.connection, repository.connection.cursor() as cursor:
        cursor.execute("UPDATE qd_ashare_universe_members SET details=details || %s::jsonb", ('{"name":"tampered"}',))
    with pytest.raises(ValueError, match="details_mismatch"):
        repository.universe(DAY, RECEIVED)


def test_real_pg_naive_knowledge_time_rejected(store):
    repository, _ = store
    with pytest.raises(ValueError, match="timezone-aware"):
        repository.universe(DAY, datetime(2026, 11, 17))


def test_real_pg_other_valid_symbol_details_cannot_be_substituted(store):
    repository, directory = store
    source = payload()
    source["request"]["symbols"].append("sh.600000")
    row = source["bars"]["sz.000001"]["rows"][0].copy()
    row[1], row[10] = "sh.600000", "0"
    source["bars"]["sh.600000"] = {"fields": FIELDS.split(","), "rows": [row]}
    repository.archive(source, DAY, source["request"]["symbols"], directory, RECEIVED)
    with repository.connection, repository.connection.cursor() as cursor:
        cursor.execute("UPDATE qd_ashare_security_statuses SET details=(SELECT details FROM qd_ashare_security_statuses WHERE symbol='sh.600000') WHERE symbol='sz.000001'")
    with pytest.raises(ValueError, match="details_mismatch"):
        repository.status("sz.000001", DAY, RECEIVED)
