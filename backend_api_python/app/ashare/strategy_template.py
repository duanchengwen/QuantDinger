"""Human-owned settings for creating review-only A-share paper drafts."""
from decimal import Decimal

from psycopg2.extras import Json, RealDictCursor

from app.ashare.broker import supported
from app.ashare.strategy_versions import MONEY, StrategyVersions, config_hash


STORAGE_KEY = "strategy_template"
TEMPLATE_FIELDS = {"enabled", "symbol_scope", "symbols", "max_cash"}
LEGACY_FIELDS = {"trigger_price", "limit_price"}


class StrategyTemplateConflict(ValueError):
    """The caller's revision no longer matches the saved template."""


def _normalize_template(template):
    if not isinstance(template, dict) or set(template) != TEMPLATE_FIELDS:
        raise ValueError("invalid_strategy_template")
    scope, symbols, maximum = template["symbol_scope"], template["symbols"], template["max_cash"]
    if (type(template["enabled"]) is not bool or scope not in ("watched", "selected")
            or not isinstance(symbols, list) or len(symbols) > 30
            or any(not supported(symbol) for symbol in symbols) or len(set(symbols)) != len(symbols)
            or (scope == "selected") != bool(symbols)):
        raise ValueError("invalid_strategy_template")
    if not isinstance(maximum, str) or not MONEY.fullmatch(maximum) or Decimal(maximum) <= 0:
        raise ValueError("invalid_strategy_money")
    return {**template, "symbols": list(symbols), "max_cash": f"{Decimal(maximum):.2f}"}


def saved_template(settings):
    """Read validated settings independently of changes to the current watchlist."""
    if not isinstance(settings, dict):
        raise ValueError("strategy_template_integrity_error")
    if STORAGE_KEY not in settings:
        return {"revision": 0, "template": None}
    record = settings[STORAGE_KEY]
    if (not isinstance(record, dict) or set(record) != {"revision", "template"}
            or type(record["revision"]) is not int or record["revision"] < 1):
        raise ValueError("strategy_template_integrity_error")
    source = record["template"]
    if not isinstance(source, dict):
        raise ValueError("strategy_template_integrity_error")
    legacy = set(source) == TEMPLATE_FIELDS | LEGACY_FIELDS
    projected = {key: value for key, value in source.items() if not legacy or key in TEMPLATE_FIELDS}
    try:
        template = _normalize_template(projected)
    except ValueError:
        raise ValueError("strategy_template_integrity_error") from None
    if template != projected:
        raise ValueError("strategy_template_integrity_error")
    if legacy:
        # A previously agreed set of specific prices is not consent to a new rule.
        template["enabled"] = False
    return {"revision": record["revision"], "template": template}


def save_template(store, expected_revision, template, actor):
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError("invalid_strategy_template_revision")
    versions = StrategyVersions(store.connection, *store.key)
    versions._operator(actor)
    with store.connection, store.connection.cursor(cursor_factory=RealDictCursor) as cursor:
        _, web = versions._lock_account(cursor)
        settings = web["settings"]
        if saved_template(settings)["revision"] != expected_revision:
            raise StrategyTemplateConflict("strategy_template_conflict")
        template = _normalize_template(template)
        if template["enabled"] and template["symbol_scope"] == "selected":
            if not set(template["symbols"]).issubset(settings.get("symbols", [])):
                raise ValueError("strategy_template_symbols_not_watched")
        result = {"revision": expected_revision + 1, "template": template}
        cursor.execute("""UPDATE qd_ashare_web SET settings=settings || %s::jsonb,updated_at=now()
            WHERE tenant_id=%s AND account_id=%s""", (Json({STORAGE_KEY: result}), *store.key))
        versions.broker._audit(cursor, "strategy_template_saved", {
            "revision": result["revision"], "actor_user_id": actor, "enabled": template["enabled"],
            "symbol_scope": template["symbol_scope"], "template_sha256": config_hash(template),
        })
    return result
