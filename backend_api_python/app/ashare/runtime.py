"""Explicit operator configuration and observable paper worker cycles."""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo


SHANGHAI = ZoneInfo("Asia/Shanghai")


def load_config(path: Path) -> dict:
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    if config.get("mode") != "paper":
        raise ValueError("Only paper mode is supported")
    if config.get("ai_auto_promotion", False) is not False:
        raise ValueError("AI automatic promotion is prohibited")
    for field in ("tenant_id", "account_id"):
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", str(config.get(field, ""))):
            raise ValueError(f"Invalid {field}")
    symbols = config.get("symbols")
    if not isinstance(symbols, list) or not 1 <= len(symbols) <= 30:
        raise ValueError("symbols must contain 1..30 symbols")
    if len(set(symbols)) != len(symbols) or any(
        not isinstance(s, str) or not re.fullmatch(r"(?:sh\.60\d{4}|sz\.(?:00|30)\d{4})", s)
        for s in symbols
    ):
        raise ValueError("Only distinct MAIN/CHINEXT stock symbols are supported")
    cash = Decimal(str(config.get("initial_cash", "100000")))
    if not cash.is_finite() or cash <= 0 or cash != cash.quantize(Decimal(".01")):
        raise ValueError("initial_cash must be positive CNY cents")
    interval = float(config.get("interval_seconds", 5))
    if not 2 <= interval <= 3600:
        raise ValueError("interval_seconds must be between 2 and 3600")
    strategy = config.get("strategy", {})
    if strategy.get("enabled", False):
        if strategy.get("kind") != "buy_once_below" or strategy.get("approved_by_operator") is not True:
            raise ValueError("Example strategy requires explicit operator approval")
        if strategy.get("symbol") not in symbols:
            raise ValueError("Strategy symbol not in configured universe")
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", str(strategy.get("id", ""))):
            raise ValueError("Strategy id required for restart idempotency")
        quantity = strategy.get("quantity")
        if type(quantity) is not int or quantity <= 0 or quantity % 100:
            raise ValueError("Strategy quantity must be a positive multiple of 100")
        for key in ("threshold", "limit_price"):
            value = Decimal(str(strategy.get(key, "0")))
            if not value.is_finite() or value <= 0 or value != value.quantize(Decimal(".01")):
                raise ValueError(f"Strategy {key} must be positive CNY cents")
    config["config_sha256"] = hashlib.sha256(
        json.dumps(config, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return config


def reviewed_rules(config: dict, symbol: str, now: datetime) -> dict:
    rules = config.get("rules", {}).get(symbol, {})
    if rules.get("reviewed") is not True or not rules.get("evidence"):
        raise ValueError("Daily rules require operator review and evidence")
    if rules.get("trading_day") != now.astimezone(SHANGHAI).date().isoformat():
        raise ValueError("Daily rules expired; review the new trading day")
    if rules.get("is_trading_day") is not True:
        raise ValueError("Not a reviewed trading day")
    for key in ("suspended", "st"):
        if type(rules.get(key)) is not bool:
            raise ValueError(f"Missing explicit daily status: {key}")
    if rules["suspended"] or rules["st"]:
        raise ValueError("Suspended and ST stocks are excluded from this MVP")
    low, high = (Decimal(str(rules.get(key, "0"))) for key in ("limit_down", "limit_up"))
    if not low.is_finite() or not high.is_finite() or not 0 < low < high:
        raise ValueError("Reviewed daily price band required")
    return rules


def save_report(path: Path, payload: dict) -> None:
    """Atomically replace a derived report; PostgreSQL remains authoritative."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, default=str)
        temporary = Path(handle.name)
    temporary.replace(path)


def claim_worker(connection, config: dict) -> None:
    """Session ownership survives commits and is released on connection loss."""
    identity = json.dumps(["ashare-paper", config["tenant_id"], config["account_id"]])
    key = int.from_bytes(hashlib.sha256(identity.encode()).digest()[:8], "big", signed=True)
    with connection, connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", (key,))
        if not cursor.fetchone()[0]:
            raise ValueError("A paper worker already owns this account")


def process_quotes(broker, config: dict, quotes: list[dict], now: datetime) -> dict:
    """Process only reviewed fresh quotes; no strategy promotion or broker factory."""
    from app.ashare.market import validate_quote

    result = {"observed_at": now.isoformat(), "mode": "paper", "fills": [], "rejections": [],
              "config_sha256": config["config_sha256"], "submitted": []}
    received = {q["symbol"] for q in quotes}
    for missing in set(config["symbols"]) - received:
        result["rejections"].append({"symbol": missing, "reason": "missing_quote"})
    for quote in quotes:
        symbol = quote["symbol"]
        if symbol not in config["symbols"]:
            continue
        try:
            validate_quote(quote, now=now, max_age_seconds=15)
            rules = reviewed_rules(config, symbol, now)
            if "ST" in str(quote.get("name", "")).upper() or "退" in str(quote.get("name", "")):
                raise ValueError("Quote name contradicts eligible stock status")
            for key in ("limit_up", "limit_down"):
                if quote.get(key) is not None and Decimal(str(quote[key])) != Decimal(str(rules[key])):
                    raise ValueError("Quote price band contradicts reviewed rules")
            result["fills"].extend(broker.on_quote(quote, rules, now=now))
            strategy = config.get("strategy", {})
            if strategy.get("enabled") and strategy["symbol"] == symbol:
                order_key = "example-" + strategy["id"]
                existing = broker.snapshot()["orders"]
                if not any(o["client_order_id"] == order_key for o in existing):
                    if Decimal(quote["last"]) <= Decimal(str(strategy["threshold"])):
                        if not Decimal(rules["limit_down"]) <= Decimal(str(strategy["limit_price"])) <= Decimal(rules["limit_up"]):
                            raise ValueError("Strategy price outside reviewed daily band")
                        order = broker.place_order(
                            client_order_id=order_key, symbol=symbol, side="buy",
                            quantity=strategy["quantity"], limit_price=Decimal(str(strategy["limit_price"])),
                            submitted_at=now, trading_day=now.astimezone(SHANGHAI).date(),
                        )
                        result["submitted"].append(order)
        except (ValueError, RuntimeError) as exc:
            result["rejections"].append({"symbol": symbol, "reason": str(exc)[:250]})
    result["state"] = "degraded" if result["rejections"] else "healthy"
    result["account"] = broker.snapshot()
    return result


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
