from datetime import datetime

import pytest

from app.ashare.calendar_rules import market_session


@pytest.mark.parametrize("instant,state", [
    ("2026-11-17T09:20:00+08:00", "pre_open"),
    ("2026-11-17T09:30:00+08:00", "continuous"),
    ("2026-11-17T11:30:00+08:00", "lunch"),
    ("2026-11-17T13:00:00+08:00", "continuous"),
    ("2026-11-17T14:57:00+08:00", "closing_auction"),
    ("2026-11-17T15:00:00+08:00", "closed"),
    ("2026-11-15T10:00:00+08:00", "non_trading_day"),
    ("2026-10-01T10:00:00+08:00", "non_trading_day"),
])
def test_session_uses_exchange_holidays_and_continuous_boundaries(instant, state):
    result = market_session(datetime.fromisoformat(instant))
    assert result["state"] == state
    assert result["can_match"] is (state == "continuous")
    assert result["calendar"] == "XSHG"


def test_unknown_calendar_future_fails_closed():
    result = market_session(datetime.fromisoformat("2050-11-17T10:00:00+08:00"))
    assert result["state"] == "calendar_unknown"
    assert result["can_match"] is False


def test_shanghai_date_used_across_utc_midnight():
    result = market_session(datetime.fromisoformat("2026-11-16T17:00:00+00:00"))
    assert result["trading_day"] == "2026-11-17"
    assert result["state"] == "pre_open"
