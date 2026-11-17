"""Scheduling must never turn unavailable daily data into current approvals."""
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from app.ashare.securities_scheduler import completed_days, sync


def weekday(day):
    return {"state": "continuous" if day.weekday() < 5 else "non_trading_day",
            "is_trading_day": day.weekday() < 5}


def test_prepublication_uses_prior_sessions_and_skips_weekend():
    now = datetime.fromisoformat("2026-11-16T10:00:00+08:00")
    assert completed_days(now, 2, weekday) == ["2026-11-12", "2026-11-13"]


def test_postpublication_can_request_today_without_claiming_availability():
    now = datetime.fromisoformat("2026-11-17T19:00:00+08:00")
    assert completed_days(now, 2, weekday) == ["2026-11-16", "2026-11-17"]


def test_unknown_calendar_and_naive_time_rejected():
    with pytest.raises(ValueError, match="coverage"):
        completed_days(datetime.now(timezone.utc), calendar=lambda day: {"state": "calendar_unknown"})
    with pytest.raises(ValueError, match="aware"):
        completed_days(datetime(2026, 11, 17))


def test_sync_coverage_is_explicit_and_never_approves(tmp_path):
    calls = []
    def ingest(connection, evidence, day, **kwargs):
        calls.append((day, kwargs))
        return {"day": day, "status": "ingested"}
    with patch("app.ashare.securities_scheduler.watched_symbols", return_value=["sz.000001"]):
        result = sync(object(), tmp_path, now=datetime.fromisoformat("2026-11-17T20:00:00+08:00"), lookback=1, ingest=ingest)
    assert result["approvals_created"] == 0
    assert result["status_coverage"] == "watched_and_existing_exposure_only"
    assert calls == [("2026-11-17", {"symbols": ["sz.000001"], "timeout": 60})]


def test_ingestion_failure_is_not_relabelled_success(tmp_path):
    def fail(*args, **kwargs):
        raise TimeoutError("provider unavailable")
    with patch("app.ashare.securities_scheduler.watched_symbols", return_value=[]):
        result = sync(object(), tmp_path, now=datetime.fromisoformat("2026-11-17T20:00:00+08:00"), lookback=1, ingest=fail)
    assert result["status"] == "failed"
    assert result["receipts"] == []
    assert result["failures"] == [{"trading_day": "2026-11-17", "error": "securities_source_rejected"}]


def test_one_source_failure_keeps_success_and_attempts_other_dates(tmp_path):
    calls = []
    def ingest(connection, evidence, day, **kwargs):
        calls.append(day)
        if day == "2026-11-16":
            raise ValueError("baostock_provider_timeout")
        return {"trading_day": day}
    with patch("app.ashare.securities_scheduler.watched_symbols", return_value=[]):
        result = sync(object(), tmp_path, now=datetime.fromisoformat("2026-11-17T20:00:00+08:00"), lookback=3, ingest=ingest)
    assert calls == ["2026-11-17", "2026-11-16", "2026-11-13"]
    assert result["status"] == "partial"
    assert len(result["receipts"]) == 2
    assert result["failures"] == [{"trading_day": "2026-11-16", "error": "baostock_provider_timeout"}]
