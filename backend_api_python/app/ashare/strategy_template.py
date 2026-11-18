"""Human-owned settings for creating review-only A-share paper drafts."""
from psycopg2.extras import Json, RealDictCursor

from app.ashare.broker import supported
from app.ashare.strategy_versions import StrategyVersions, config_hash, normalize_config


STORAGE_KEY = "strategy_template"
TEMPLATE_FIELDS = {"enabled", "symbol_scope", "symbols", "trigger_price", "limit_price", "max_cash"}


class StrategyTemplateConflict(ValueError):
    """The caller's revision no longer matches the saved template."""


def minimum_buy_config(template, symbol):
    """Convert one lot of an eligible symbol into a review-only draft config."""
    return normalize_config({"mode": "paper", "kind": "buy_once_below", "quantity": 100,
                             "symbol": symbol, "threshold": template["trigger_price"],
                             "limit_price": template["limit_price"], "max_cash": template["max_cash"]})


def _normalize_template(template):
    if not isinstance(template, dict) or set(template) != TEMPLATE_FIELDS:
        raise ValueError("invalid_strategy_template")
    scope, symbols = template["symbol_scope"], template["symbols"]
    if (type(template["enabled"]) is not bool or scope not in ("watched", "selected")
            or not isinstance(symbols, list) or len(symbols) > 30
            or any(not supported(symbol) for symbol in symbols) or len(set(symbols)) != len(symbols)
            or (scope == "selected") != bool(symbols)):
        raise ValueError("invalid_strategy_template")
    # A watched template resolves its symbol when a draft is proposed.
    config = minimum_buy_config(template, symbols[0] if symbols else "sz.000001")
    return ({**template, "symbols": list(symbols), "trigger_price": config["threshold"],
             "limit_price": config["limit_price"], "max_cash": config["max_cash"]}, config)


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
    try:
        template, _ = _normalize_template(record["template"])
    except ValueError:
        raise ValueError("strategy_template_integrity_error") from None
    if template != record["template"]:
        raise ValueError("strategy_template_integrity_error")
    return {"revision": record["revision"], "template": template}


def save_template(store, expected_revision, template, actor):
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError("invalid_strategy_template_revision")
    versions = StrategyVersions(store.connection, *store.key)
    versions._operator(actor)
    with store.connection, store.connection.cursor(cursor_factory=RealDictCursor) as cursor:
        account, web = versions._lock_account(cursor)
        settings = web["settings"]
        if saved_template(settings)["revision"] != expected_revision:
            raise StrategyTemplateConflict("strategy_template_conflict")
        template, config = _normalize_template(template)
        if template["enabled"] and template["symbol_scope"] == "selected":
            if not set(template["symbols"]).issubset(settings.get("symbols", [])):
                raise ValueError("strategy_template_symbols_not_watched")
        versions._validate_cap(config, account)
        result = {"revision": expected_revision + 1, "template": template}
        cursor.execute("""UPDATE qd_ashare_web SET settings=settings || %s::jsonb,updated_at=now()
            WHERE tenant_id=%s AND account_id=%s""", (Json({STORAGE_KEY: result}), *store.key))
        versions.broker._audit(cursor, "strategy_template_saved", {
            "revision": result["revision"], "actor_user_id": actor, "enabled": template["enabled"],
            "symbol_scope": template["symbol_scope"], "template_sha256": config_hash(template),
        })
    return result
