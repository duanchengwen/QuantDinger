"""Bounded exchange-calendar evidence for continuous-auction paper matching."""

from datetime import date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")


@lru_cache(maxsize=1)
def _calendar():
    import exchange_calendars
    return exchange_calendars.get_calendar("XSHG"), exchange_calendars.__version__


def market_session(now: datetime) -> dict:
    """Unknown calendar coverage never grants execution permission.

    XSHG provides dated holidays and session/break times. The final three
    minutes are conservatively excluded because this simulator does not model
    the closing call auction. Calendar coverage is not stock-status evidence.
    """
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("timezone-aware current time required")
    day = now.astimezone(SHANGHAI).date().isoformat()
    result = {"calendar": "XSHG", "trading_day": day, "is_trading_day": False,
              "can_match": False, "state": "calendar_unknown"}
    try:
        calendar, version = _calendar()
        result.update(calendar_version=version,
                      coverage_start=calendar.first_session.date().isoformat(),
                      coverage_end=calendar.last_session.date().isoformat())
        if not result["coverage_start"] <= day <= result["coverage_end"]:
            return result
        if not calendar.is_session(day):
            result["state"] = "non_trading_day"
            return result
        schedule = calendar.schedule.loc[day]
        opening = schedule["open"].to_pydatetime()
        lunch_start = schedule["break_start"].to_pydatetime()
        lunch_end = schedule["break_end"].to_pydatetime()
        closing = schedule["close"].to_pydatetime()
        result["is_trading_day"] = True
        if now < opening:
            result["state"] = "pre_open"
        elif now < lunch_start:
            result.update(state="continuous", can_match=True)
        elif now < lunch_end:
            result["state"] = "lunch"
        elif now < closing - timedelta(minutes=3):
            result.update(state="continuous", can_match=True)
        elif now < closing:
            result["state"] = "closing_auction"
        else:
            result["state"] = "closed"
        return result
    except (ImportError, ValueError, KeyError, TypeError, OverflowError):
        return result


def trading_day_status(day: date) -> dict:
    """Date-only calendar check, without treating a weekday as exchange proof."""
    if isinstance(day, str):
        day = date.fromisoformat(day)
    if not isinstance(day, date) or isinstance(day, datetime):
        raise ValueError("trading day must be a date")
    return market_session(datetime.combine(day, time(10), tzinfo=SHANGHAI))
