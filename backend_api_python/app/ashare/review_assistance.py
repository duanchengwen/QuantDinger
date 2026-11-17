"""Evidence suggestions never grant approval or carry a prior day's status forward."""
from datetime import datetime
from decimal import Decimal, InvalidOperation
import re

from app.ashare.runtime import SHANGHAI, reviewed_rules


RULE_FIELDS = {"symbol", "trading_day", "suspended", "st", "evidence", "limit_up", "limit_down"}


def dated_status(connection, symbol, trading_day, now):
    from app.ashare.securities import SecuritiesStore
    return SecuritiesStore(connection).status(symbol, trading_day, known_at=now)


def daily_quote(snapshot, symbol, now):
    quotes = [quote for quote in snapshot["quotes"] if quote["symbol"] == symbol]
    if not quotes:
        raise ValueError("daily_quote_evidence_required")
    quote = quotes[0]
    quoted_at = datetime.fromisoformat(quote["exchange_time"])
    if quoted_at.utcoffset() is None or quoted_at > now or quoted_at.astimezone(SHANGHAI).date() != now.astimezone(SHANGHAI).date():
        raise ValueError("daily_quote_evidence_expired")
    if not re.fullmatch(r"[0-9a-f]{64}", str(quote.get("evidence_sha256", ""))):
        raise ValueError("daily_quote_evidence_hash_required")
    return quote


def suggestions(store, now):
    snapshot = store.state()
    day = now.astimezone(SHANGHAI).date()
    rows = []
    for symbol in snapshot["settings"]["symbols"]:
        evidence_error = False
        try:
            status = dated_status(store.connection, symbol, day, now)
        except ValueError:
            status, evidence_error = None, True
        row = {"symbol": symbol, "trading_day": day.isoformat(), "st": None,
               "suspended": None, "limit_up": None, "limit_down": None,
               "status_evidence": status, "quote_evidence": None,
               "unknown_fields": [], "blockers": [], "reviewed": False}
        if evidence_error:
            row["blockers"].append("security_evidence_unavailable")
        if status:
            row.update(st=status.get("st"), suspended=status.get("suspended"))
            if status.get("st") is True or status.get("suspended") is True:
                row["blockers"].append("source_reports_ineligible_security")
        try:
            quote = daily_quote(snapshot, symbol, now)
            row.update({field: quote.get(field) for field in ("limit_up", "limit_down")})
            low, high = (Decimal(str(row[field])) for field in ("limit_down", "limit_up"))
            if not low.is_finite() or not high.is_finite() or not 0 < low < high:
                raise ValueError("invalid_daily_price_band")
            row["quote_evidence"] = {key: quote.get(key) for key in (
                "source", "exchange_time", "received_at", "evidence_sha256", "evidence_path")}
            if "ST" in str(quote.get("name", "")).upper() or "退" in str(quote.get("name", "")):
                row["blockers"].append("ineligible_security_name")
        except (ValueError, TypeError, KeyError, InvalidOperation):
            row["blockers"].append("current_day_quote_evidence_required")
        row["unknown_fields"] = [field for field in ("st", "suspended", "limit_up", "limit_down") if row[field] is None]
        rows.append(row)
    return {"trading_day": day.isoformat(), "generated_at": now.isoformat(),
            "requires_human_confirmation": True, "auto_approval": False, "rows": rows}


def validated_review(store, data, now, user_id, snapshot=None):
    if not isinstance(data, dict) or set(data) != RULE_FIELDS:
        raise ValueError("unexpected_fields")
    snapshot = snapshot if snapshot is not None else store.state()
    if data["symbol"] not in snapshot["settings"]["symbols"]:
        raise ValueError("symbol_not_configured")
    if not isinstance(data.get("evidence"), str) or not 3 <= len(data["evidence"].strip()) <= 1000:
        raise ValueError("review_evidence_required")
    rule = {**data, "reviewed": True, "is_trading_day": True,
            "reviewed_at": now.isoformat(), "reviewed_by": user_id}
    reviewed_rules({"rules": {data["symbol"]: rule}}, data["symbol"], now)
    from app.ashare.calendar_rules import trading_day_status
    day = now.astimezone(SHANGHAI).date()
    if not trading_day_status(day)["is_trading_day"]:
        raise ValueError("not_trading_day")
    status = dated_status(store.connection, data["symbol"], day, now)
    if status and (status.get("st") is True or status.get("suspended") is True):
        raise ValueError("source_reports_ineligible_security")
    quote = daily_quote(snapshot, data["symbol"], now)
    if "ST" in str(quote.get("name", "")).upper() or "退" in str(quote.get("name", "")):
        raise ValueError("ineligible_security_name")
    for field in ("limit_up", "limit_down"):
        if Decimal(str(quote[field])) != Decimal(str(rule[field])):
            raise ValueError("daily_price_band_mismatch")
    rule["quote_evidence"] = {key: quote.get(key) for key in (
        "source", "symbol", "exchange_time", "received_at", "quote_id",
        "evidence_sha256", "evidence_path", "limit_up", "limit_down")}
    rule["status_evidence"] = status
    rule["approval_method"] = "explicit_human_review"
    return rule
