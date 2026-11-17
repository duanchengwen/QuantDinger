"""Human-authenticated paper endpoints. Agents and real brokers are excluded."""
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from functools import wraps

from flask import Blueprint, g, jsonify, request
import psycopg2

from app.utils.auth import login_required
from app.ashare.market import validate_quote
from app.ashare.runtime import SHANGHAI, reviewed_rules
from app.ashare.web_store import WebStore
from app.ashare.schema import get_database_url

blueprint = Blueprint("ashare_paper", __name__, url_prefix="/api/ashare-paper")


def endpoint(write=False):
    def decorate(function):
        @wraps(function)
        @login_required
        def wrapped(*args, **kwargs):
            if write and g.user_role not in ("admin", "user"):
                return jsonify(code=0, msg="paper_write_forbidden", data=None), 403
            if write and not request.is_json:
                return jsonify(code=0, msg="json_required", data=None), 400
            dsn = get_database_url()
            if not dsn:
                return jsonify(code=0, msg="paper_database_not_configured", data=None), 503
            try:
                with closing(psycopg2.connect(dsn, connect_timeout=5)) as connection:
                    store = WebStore(connection, "user_" + str(g.user_id))
                    result = function(store, *args, **kwargs)
                    return jsonify(code=1, msg="success", data=serialize(result))
            except (ValueError, TypeError, KeyError, InvalidOperation) as exc:
                return jsonify(code=0, msg=str(exc)[:180], data=None), 400
            except psycopg2.Error:
                return jsonify(code=0, msg="paper_database_unavailable", data=None), 503
        return wrapped
    return decorate


def serialize(value):
    from datetime import date
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: serialize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [serialize(item) for item in value]
    return value


def body(allowed):
    data = request.get_json()
    if not isinstance(data, dict) or set(data) - set(allowed):
        raise ValueError("unexpected_fields")
    return data


@blueprint.get("/state")
@endpoint()
def state(store):
    return store.state()


@blueprint.post("/initialize")
@endpoint(write=True)
def initialize(store):
    return store.initialize(body({"initial_cash"})["initial_cash"])


@blueprint.put("/settings")
@endpoint(write=True)
def settings(store):
    data = body({"symbols", "interval_seconds"})
    return store.save_settings(data["symbols"], data["interval_seconds"])


@blueprint.post("/rules/review")
@endpoint(write=True)
def review(store):
    data = body({"symbol", "trading_day", "suspended", "st", "evidence", "limit_up", "limit_down"})
    now = datetime.now(timezone.utc)
    if data["symbol"] not in store.settings()["symbols"]:
        raise ValueError("symbol_not_configured")
    if not isinstance(data.get("evidence"), str) or not 3 <= len(data["evidence"]) <= 1000:
        raise ValueError("review_evidence_required")
    rule = {**data, "reviewed": True, "is_trading_day": True,
            "reviewed_at": now.isoformat(), "reviewed_by": g.user_id}
    reviewed_rules({"rules": {data["symbol"]: rule}}, data["symbol"], now)
    # The calendar is a separately maintained source; human review cannot open a holiday.
    from app.ashare.calendar_rules import trading_day_status
    calendar = trading_day_status(now.astimezone(SHANGHAI).date())
    if not calendar["is_trading_day"]:
        raise ValueError("not_trading_day")
    quotes = [quote for quote in store.state()["quotes"] if quote["symbol"] == data["symbol"]]
    if not quotes:
        raise ValueError("daily_quote_evidence_required")
    quote = quotes[0]
    quoted_at = datetime.fromisoformat(quote["exchange_time"])
    if quoted_at.utcoffset() is None or quoted_at > now or quoted_at.astimezone(SHANGHAI).date() != now.astimezone(SHANGHAI).date():
        raise ValueError("daily_quote_evidence_expired")
    for field in ("limit_up", "limit_down"):
        if Decimal(str(quote[field])) != Decimal(str(rule[field])):
            raise ValueError("daily_price_band_mismatch")
    import re
    if not re.fullmatch(r"[0-9a-f]{64}", str(quote.get("evidence_sha256", ""))):
        raise ValueError("daily_quote_evidence_hash_required")
    rule["quote_evidence"] = {key: quote.get(key) for key in (
        "source", "symbol", "exchange_time", "received_at", "quote_id",
        "evidence_sha256", "evidence_path", "limit_up", "limit_down",
    )}
    return store.review(rule)


@blueprint.post("/orders")
@endpoint(write=True)
def order(store):
    data = body({"client_order_id", "symbol", "side", "quantity", "limit_price"})
    now = datetime.now(timezone.utc)
    snapshot = store.state()
    # Exact retries retain the original timestamp and remain valid after the quote expires.
    for previous in snapshot["orders"]:
        if previous["client_order_id"] == data["client_order_id"]:
            return store.broker.place_order(**data, submitted_at=previous["submitted_at"], trading_day=previous["trading_day"])
    if data["symbol"] not in snapshot["settings"]["symbols"]:
        raise ValueError("symbol_not_configured")
    if snapshot["worker"].get("stale", True):
        raise ValueError("paper_worker_stale")
    if snapshot["worker"].get("state") != "healthy":
        raise ValueError("paper_worker_not_healthy")
    rules = reviewed_rules({"rules": snapshot["rules"]}, data["symbol"], now)
    quotes = [quote for quote in snapshot["quotes"] if quote["symbol"] == data["symbol"]]
    if not quotes:
        raise ValueError("fresh_quote_required")
    quote = quotes[0]
    validate_quote(quote, now, 15)
    if "ST" in str(quote.get("name", "")).upper() or "退" in str(quote.get("name", "")):
        raise ValueError("ineligible_security_name")
    for field in ("limit_up", "limit_down"):
        if Decimal(str(quote[field])) != Decimal(str(rules[field])):
            raise ValueError("daily_price_band_mismatch")
    if not Decimal(str(rules["limit_down"])) <= Decimal(str(data["limit_price"])) <= Decimal(str(rules["limit_up"])):
        raise ValueError("order_price_outside_daily_band")
    from app.ashare.calendar_rules import market_session
    if not market_session(now)["can_match"]:
        raise ValueError("outside_continuous_auction")
    return store.broker.place_order(**data, submitted_at=now, trading_day=now.astimezone(SHANGHAI).date())


@blueprint.post("/orders/<int:order_id>/cancel")
@endpoint(write=True)
def cancel(store, order_id):
    body(set())
    return store.broker.cancel(order_id)


@blueprint.post("/notifications/config")
@endpoint(write=True)
def notification_config(store):
    data = body({"webhook_url", "signing_secret"})
    return store.configure_notifications(data["webhook_url"], data.get("signing_secret", ""))


@blueprint.post("/notifications/test")
@endpoint(write=True)
def notification_test(store):
    body(set())
    from app.ashare.notifications import notify_text
    return notify_text("Paper account notification test", **store.notification_credentials())
