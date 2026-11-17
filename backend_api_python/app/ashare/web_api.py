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
                with closing(psycopg2.connect(dsn, connect_timeout=5, options="-c statement_timeout=10000 -c lock_timeout=3000")) as connection:
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


def query_fields(allowed):
    # The official Vue request interceptor appends this cache-busting field.
    if set(request.args) - (set(allowed) | {"_t"}):
        raise ValueError("unexpected_fields")


@blueprint.get("/state")
@endpoint()
def state(store):
    return store.state()


@blueprint.get("/daily-reports")
@endpoint()
def daily_reports(store):
    from app.ashare.operations import list_daily_reports
    query_fields({"limit"})
    try:
        limit = int(request.args.get("limit", "30"))
    except ValueError:
        raise ValueError("invalid_report_limit") from None
    if not 1 <= limit <= 90:
        raise ValueError("invalid_report_limit")
    return list_daily_reports(store.connection, store.tenant_id, store.account_id, limit=limit)


class _ThemeReadUnavailable(Exception):
    """Only theme reads translate evidence errors into a safe 503 response."""


class _ResearchUnavailable(Exception):
    """Keep provider/configuration/filesystem exception details out of HTTP."""


def _research_endpoint(write=False):
    def decorate(function):
        authenticated = endpoint(write=write)(function)

        @wraps(function)
        def wrapped(*args, **kwargs):
            try:
                return authenticated(*args, **kwargs)
            except _ResearchUnavailable:
                return jsonify(code=0, msg="research_evidence_unavailable", data=None), 503
        return wrapped
    return decorate


def _research_limit(default, maximum):
    query_fields({'limit'})
    if any(len(request.args.getlist(key)) != 1 for key in request.args):
        raise ValueError('duplicate_query_fields')
    value = request.args.get('limit', str(default))
    if not value.isascii() or not value.isdecimal() or len(value) > 3 or not 1 <= int(value) <= maximum:
        raise ValueError('invalid_research_limit')
    return int(value)


def _research_operation(store, operation, *, write=False):
    if not write:
        store.connection.set_session(readonly=True, isolation_level='REPEATABLE READ')
    try:
        return operation()
    except (ValueError, TypeError, KeyError, OSError) as exc:
        from app.ashare.research import ResearchError
        public_errors = {'invalid_idempotency_key', 'paper_account_required',
                         'invalid_research_preferences', 'research_already_active',
                         'research_daily_limit', 'invalid_research_limit'}
        if isinstance(exc, ResearchError) and str(exc) in public_errors:
            raise ValueError(str(exc)) from None
        raise _ResearchUnavailable() from None


@blueprint.get('/news')
@_research_endpoint()
def research_news(store):
    from app.ashare.news import NewsStore
    limit = _research_limit(20, 50)
    def read_news():
        preferences = store.settings().get('research_preferences', {})
        topics = [topic for topic in preferences.get('topics', []) if topic in ('ai', 'robotics', 'new_energy')]
        if not topics:
            return {'items': [], 'status': 'no_selected_topics'}
        return NewsStore(store.connection).list_items(
            topics=topics, known_at=datetime.now(timezone.utc), limit=limit)
    return _research_operation(store, read_news)


@blueprint.get('/research/status')
@_research_endpoint()
def research_status(store):
    from app.ashare.research import ResearchStore
    _theme_query(set())
    return _research_operation(store, lambda: ResearchStore(store.connection, *store.key).status())


@blueprint.get('/research/runs')
@_research_endpoint()
def research_runs(store):
    from app.ashare.research import ResearchStore
    limit = _research_limit(10, 20)
    return _research_operation(store, lambda: ResearchStore(store.connection, *store.key).list_runs(limit=limit))


@blueprint.get('/research/runs/<run_id>')
@_research_endpoint()
def research_run(store, run_id):
    import re
    from app.ashare.research import ResearchStore
    _theme_query(set())
    if not re.fullmatch(r'[a-f0-9]{32}', run_id):
        raise ValueError('invalid_research_run_id')
    result = _research_operation(store, lambda: ResearchStore(store.connection, *store.key).get_run(run_id))
    if result is None:
        raise ValueError('research_run_not_found')
    return result


@blueprint.post('/research/runs')
@_research_endpoint(write=True)
def request_research(store):
    import re
    from app.ashare.research import ResearchStore
    data = body({'idempotency_key'})
    key = data.get('idempotency_key')
    if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,100}', key):
        raise ValueError('invalid_research_idempotency_key')
    return _research_operation(store, lambda: ResearchStore(store.connection, *store.key).request_run(key), write=True)


def _theme_endpoint(function):
    authenticated = endpoint()(function)

    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return authenticated(*args, **kwargs)
        except _ThemeReadUnavailable:
            return jsonify(code=0, msg="theme_evidence_unavailable", data=None), 503
    return wrapped


def _theme_query(allowed):
    query_fields(allowed)
    if any(len(request.args.getlist(key)) != 1 for key in request.args):
        raise ValueError("duplicate_query_fields")
    result = {}
    for key, default, minimum, maximum in (("limit", "100", 1, 500), ("offset", "0", 0, 10000)):
        if key not in allowed:
            continue
        raw = request.args.get(key, default)
        if not raw.isascii() or not raw.isdecimal() or len(raw) > 5:
            raise ValueError("invalid_theme_pagination")
        value = int(raw)
        if not minimum <= value <= maximum:
            raise ValueError("invalid_theme_pagination")
        result[key] = value
    return result


def _theme_read(store, operation):
    from app.ashare.themes import ThemeError
    # This connection is fresh and this policy applies only to these two reads.
    store.connection.set_session(readonly=True, isolation_level="REPEATABLE READ")
    try:
        return operation()
    except (ThemeError, OSError):
        # Neither filesystem paths nor provider exception details leave the API.
        raise _ThemeReadUnavailable() from None


@blueprint.get("/themes")
@_theme_endpoint
def themes(store):
    from app.ashare.themes import ThemeStore
    _theme_query(set())
    now = datetime.now(timezone.utc)
    return _theme_read(store, lambda: ThemeStore(store.connection).latest_snapshots(known_at=now))


@blueprint.get("/themes/<board_code>")
@_theme_endpoint
def theme_snapshot(store, board_code):
    from app.ashare.themes import BOARD_CODE, ThemeStore
    pagination = _theme_query({"limit", "offset"})
    if not BOARD_CODE.fullmatch(board_code):
        raise ValueError("invalid_board_code")
    now = datetime.now(timezone.utc)
    result = _theme_read(store, lambda: ThemeStore(store.connection).snapshot(board_code, known_at=now, **pagination))
    result.pop("evidence_path", None)
    return result


@blueprint.post("/initialize")
@endpoint(write=True)
def initialize(store):
    return store.initialize(body({"initial_cash"})["initial_cash"])


@blueprint.get("/universe")
@endpoint()
def universe(store):
    from datetime import date
    from app.ashare.securities import SecuritiesStore
    query_fields({"trading_day", "limit", "offset"})
    day = date.fromisoformat(request.args.get("trading_day", ""))
    now = datetime.now(timezone.utc)
    if day > now.astimezone(SHANGHAI).date():
        raise ValueError("future_universe_not_available")
    limit, offset = int(request.args.get("limit", "100")), int(request.args.get("offset", "0"))
    if not 1 <= limit <= 500 or not 0 <= offset <= 20000:
        raise ValueError("invalid_universe_pagination")
    return SecuritiesStore(store.connection).universe(day, known_at=now, limit=limit, offset=offset)


@blueprint.put("/settings")
@endpoint(write=True)
def settings(store):
    data = body({"symbols", "interval_seconds", "research_preferences"})
    return store.save_settings(data["symbols"], data["interval_seconds"], data.get("research_preferences"))


@blueprint.get("/rules/suggestions")
@endpoint()
def rule_suggestions(store):
    from app.ashare.review_assistance import suggestions
    query_fields(set())
    return suggestions(store, datetime.now(timezone.utc))


@blueprint.post("/rules/review")
@endpoint(write=True)
def review(store):
    from app.ashare.review_assistance import RULE_FIELDS, validated_review
    return store.review(validated_review(store, body(RULE_FIELDS), datetime.now(timezone.utc), g.user_id))


@blueprint.post("/rules/review-batch")
@endpoint(write=True)
def review_batch(store):
    from app.ashare.review_assistance import validated_review
    data = body({"rules", "confirmed"})
    rules = data.get("rules")
    if data.get("confirmed") is not True:
        raise ValueError("explicit_human_confirmation_required")
    if not isinstance(rules, list) or not 1 <= len(rules) <= 30:
        raise ValueError("invalid_review_batch")
    if any(not isinstance(rule, dict) or not isinstance(rule.get("symbol"), str) for rule in rules):
        raise ValueError("invalid_review_batch")
    if len({rule["symbol"] for rule in rules}) != len(rules):
        raise ValueError("duplicate_review_symbol")
    now = datetime.now(timezone.utc)
    snapshot = store.state()
    validated = [validated_review(store, rule, now, g.user_id, snapshot) for rule in rules]
    return store.review_many(validated)


@blueprint.post("/orders")
@endpoint(write=True)
def order(store):
    data = body({"client_order_id", "symbol", "side", "quantity", "limit_price"})
    now = datetime.now(timezone.utc)
    # Exact retries remain valid after their rows leave the bounded UI history.
    previous = store.broker.find_order(data["client_order_id"])
    if previous:
        return store.broker.place_order(**data, submitted_at=previous["submitted_at"], trading_day=previous["trading_day"])
    snapshot = store.state()
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
