"""Bounded retrospective replay using controlled BaoStock evidence only."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import subprocess

import pytest
from psycopg2.extensions import TRANSACTION_STATUS_IDLE

from app.ashare import strategy_history as history
from app.ashare.broker import DEFAULT_FEES, PaperBroker
from app.ashare.securities import FIELDS, HISTORY_URL, _canonical
from app.ashare.strategy_backtest import WARMUP, _replay, evaluate
from test_securities import payload as daily_payload, store  # noqa: F401


AS_OF = datetime(2026, 11, 19, 2, tzinfo=timezone.utc)
SYMBOL = "sz.000001"


class ReceiptClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return (AS_OF + timedelta(seconds=1)).astimezone(tz)


def _source(days, ending="up"):
    steps = ([3] * 20 + [30] * 10 + [-15] * 15 + [18 if ending == "up" else -1] * 15
             if ending != "flat" else [0] * 60)
    rows, previous = [], Decimal("10.00")
    for index, (day, increment) in enumerate(zip(days, steps)):
        close = previous + Decimal(increment) / 100
        opening = previous + Decimal("0.04") * (1 if index % 2 else -1)
        high = max(close, opening, previous) + Decimal("0.06")
        low = min(close, opening, previous) - Decimal("0.06")
        rows.append([day.isoformat(), SYMBOL, *(f"{price:.2f}" for price in (opening, high, low, close, previous)),
                     "10000", "100000", "3", "1", "0"])
        previous = close
    return {"request": history._range_request(SYMBOL, days),
            "bars": {"fields": FIELDS.split(","), "rows": rows}, "sdk_version": "offline-fixture"}


def _evaluate(path, *, as_of=AS_OF, connection=None, settled=0, cap="3000.00"):
    return evaluate(connection, SYMBOL, as_of=as_of, max_cash=cap, fee_policy=DEFAULT_FEES,
                    settled_quantity=settled, evidence_dir=path)


def _archive(path, *, ending="up", received=AS_OF - timedelta(hours=1)):
    days, _ = history.history_window(AS_OF)
    source = _source(days, ending)
    return history._archive_range(path, source, SYMBOL, days, received)


def test_fixed_holdout_replay_yields_review_only_candidate_and_provenance(tmp_path):
    receipt = _archive(tmp_path)
    result = _evaluate(tmp_path)
    assert result["status"] == "candidate" and result["execution_allowed"] is False
    assert result["config"] == {"mode": "paper", "kind": "buy_once_below", "symbol": SYMBOL,
                                "quantity": 100, "threshold": "14.05", "limit_price": "14.05", "max_cash": "3000.00"}
    evidence = result["evidence"]
    assert evidence["adjustment"] == "3" and evidence["receipts"] == [receipt]
    assert evidence["decision_shanghai_day"] == "2026-11-19" and evidence["last_session"] == "2026-11-18"
    assert evidence["warmup_sessions"] == 20 and evidence["holdout_sessions"] == 40
    assert evidence["historical_point_in_time_certified"] is False
    metrics = result["metrics"]
    assert metrics["sell_fills"] == metrics["closed_round_trips"] == 1 and metrics["buy_fills"] == 2
    assert Decimal(metrics["realized_net_pnl"]) > 0 and Decimal(metrics["net_pnl"]) > 0
    assert Decimal(metrics["closing_equity_after_exit_costs"]) == Decimal("3000.00") + Decimal(metrics["net_pnl"])
    assert _evaluate(tmp_path, settled=100)["reason_code"] == "settled_lot_already_held"
    assert _evaluate(tmp_path, cap="1050.00")["config"] is None


def test_sell_proposal_requires_settled_lot_and_neutral_market_creates_no_trades(tmp_path):
    _archive(tmp_path / "down", ending="down")
    sale = _evaluate(tmp_path / "down", settled=Decimal("100"))
    assert sale["status"] == "candidate" and sale["config"]["kind"] == "sell_once_above"
    assert sale["config"]["quantity"] == 100 and sale["execution_allowed"] is False
    assert _evaluate(tmp_path / "down", settled=99)["reason_code"] == "no_settled_lot_to_sell"
    _archive(tmp_path / "flat", ending="flat")
    flat = _evaluate(tmp_path / "flat")
    assert flat["status"] == "no_trade" and flat["config"] is None
    assert flat["reason_code"] == "no_closed_holdout_round_trip"
    assert flat["metrics"]["buy_fills"] == flat["metrics"]["sell_fills"] == 0
    assert flat["metrics"]["realized_net_pnl"] == "0.00"


def test_first_holdout_fill_uses_prior_signal_strict_crossing_and_no_same_day_exit():
    days, _ = history.history_window(AS_OF)
    bars = history._range_bars(_source(days), SYMBOL, days)[:WARMUP + 1]
    costs = PaperBroker(None, "test", "paper", fee_policy=DEFAULT_FEES)
    cap = Decimal("3000.00")
    bar, limit = bars[-1], bars[-2].close
    # A low close on the execution date changes the end mark and later
    # signals, but must not change an entry already determined by prior days.
    low_close = replace(bar, close=Decimal("5.00"), low=Decimal("4.95"))
    one_day, *_ = _replay([*bars[:-1], low_close], cap, costs)
    assert one_day["buy_fills"] == 1 and one_day["sell_fills"] == 0
    touch_only = replace(bar, close=limit, high=limit)
    touched, *_ = _replay([*bars[:-1], touch_only], cap, costs)
    assert touched["buy_fills"] == touched["sell_fills"] == 0


def test_window_ignores_receipts_unavailable_at_as_of_and_contains_no_same_day_bars(tmp_path):
    _archive(tmp_path, received=AS_OF + timedelta(seconds=1))
    unseen = _evaluate(tmp_path)
    assert unseen["status"] == "insufficient_evidence" and unseen["metrics"] is unseen["config"] is None
    assert _evaluate(tmp_path, as_of=AS_OF + timedelta(seconds=1))["status"] == "candidate"
    assert _evaluate(tmp_path, as_of=datetime(2050, 1, 1, tzinfo=timezone.utc))["reason_code"] == "calendar_window_unverified"


@pytest.mark.parametrize(("change", "reason"), [
    ("extra_day", "historical_bars_missing_or_noncontiguous"),
    ("single_missing_day", "historical_bars_missing_or_noncontiguous"),
    ("adjusted", "historical_bar_scope_mismatch"),
    ("unreviewed", "unverified_or_ineligible_historical_status"),
    ("st", "unverified_or_ineligible_historical_status"),
    ("preclose", "historical_preclose_discontinuity"),
    ("ohlc", "invalid_historical_ohlc"),
    ("hash", "historical_evidence_hash_mismatch"),
])
def test_replay_aborts_without_partial_metrics_on_unreliable_evidence(tmp_path, change, reason):
    days, _ = history.history_window(AS_OF)
    source = _source(days)
    rows = source["bars"]["rows"]
    if change == "extra_day":
        rows.append(["2026-11-19", *rows[-1][1:]])
    if change == "single_missing_day":
        del rows[37]
    if change == "adjusted":
        rows[0][9] = "2"
    if change == "unreviewed":
        rows[0][10] = ""
    if change == "st":
        rows[0][11] = "1"
    if change == "preclose":
        rows[27][6] = "10.00"
    if change == "ohlc":
        rows[0][4] = "999"
    data = {"schema_version": 1, "received_at": AS_OF.isoformat(), "source_url": HISTORY_URL,
            "provider_published_at": None, "source_sha256": hashlib.sha256(_canonical(source)).hexdigest(),
            "source": source}
    raw = _canonical(data)
    digest = hashlib.sha256(raw).hexdigest()
    folder = history._range_dir(tmp_path, SYMBOL, days)
    folder.mkdir(parents=True)
    (folder / f"{digest}.json").write_bytes(raw if change != "hash" else raw + b" ")
    result = _evaluate(tmp_path)
    assert result["status"] == "insufficient_evidence" and result["reason_code"] == reason
    assert result["metrics"] is result["config"] is None


def test_daily_receipts_are_reconciled_from_raw_and_read_without_writes(store):  # noqa: F811
    repository, path = store
    days, _ = history.history_window(AS_OF)
    source = _source(days)
    for row in source["bars"]["rows"]:
        payload = daily_payload(row[0])
        payload["bars"][SYMBOL]["rows"] = [row]
        repository.archive(payload, row[0], [SYMBOL], path, AS_OF)
    result = _evaluate(path, connection=repository.connection)
    assert result["status"] == "candidate" and result["evidence"]["source_kind"] == "daily_receipts"
    assert [receipt["session"] for receipt in result["evidence"]["receipts"]] == [day.isoformat() for day in days]
    assert repository.connection.get_transaction_status() == TRANSACTION_STATUS_IDLE
    assert _evaluate(path, as_of=AS_OF - timedelta(seconds=1), connection=repository.connection)["metrics"] is None
    with repository.connection, repository.connection.cursor() as cursor:
        cursor.execute("""UPDATE qd_ashare_security_statuses SET details=details || '{"st":true}'::jsonb
            WHERE receipt_id=%s""", (result["evidence"]["receipts"][0]["receipt_id"],))
    tampered = _evaluate(path, connection=repository.connection)
    assert tampered["reason_code"] == "historical_daily_status_mismatch" and tampered["metrics"] is None


def test_range_collection_is_bound_to_one_child_and_actual_receipt_time(tmp_path, monkeypatch):
    days, _ = history.history_window(AS_OF)
    expected = _source(days)
    attempts = []

    def fake_sdk(command, **options):
        attempts.append(command)
        assert command[-2:] == ["app.ashare.strategy_history", "_provider"] and options["timeout"] == 5
        assert json.loads(options["input"]) == {"symbols": [SYMBOL], "start": days[0].isoformat(), "end": "2026-11-18"}
        return subprocess.CompletedProcess(command, 0, json.dumps({"sdk_version": expected["sdk_version"],
                                                                     "bars": {SYMBOL: expected["bars"]}}))

    monkeypatch.setattr(history, "datetime", ReceiptClock)
    monkeypatch.setattr(history.subprocess, "run", fake_sdk)
    results = history.collect_history([SYMBOL], as_of=AS_OF, evidence_dir=tmp_path, timeout=5)
    assert results[SYMBOL]["status"] == "collected"
    assert _evaluate(tmp_path)["status"] == "insufficient_evidence"
    fresh = AS_OF + timedelta(seconds=1)
    assert _evaluate(tmp_path, as_of=fresh)["status"] == "candidate"
    results = history.collect_history([SYMBOL], as_of=fresh, evidence_dir=tmp_path, timeout=5)
    assert results[SYMBOL]["status"] == "already_available" and len(attempts) == 1


def test_provider_timeout_does_not_archive_or_retry(tmp_path, monkeypatch):
    attempts = []

    def timeout(command, **options):
        attempts.append(command)
        raise subprocess.TimeoutExpired(command, options["timeout"])

    monkeypatch.setattr(history, "datetime", ReceiptClock)
    monkeypatch.setattr(history.subprocess, "run", timeout)
    output = history.collect_history(["sh.600000", SYMBOL], as_of=AS_OF, evidence_dir=tmp_path, timeout=2)
    assert output == {symbol: {"status": "insufficient_evidence", "reason_code": "baostock_provider_timeout"}
                      for symbol in ("sh.600000", SYMBOL)}
    assert len(attempts) == 1 and not list(tmp_path.iterdir())
