"""PostgreSQL-backed, long-only A-share cash simulator. No live dependencies."""
from __future__ import annotations

import json
import re
from datetime import date, datetime, time
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from psycopg2.extras import RealDictCursor

SHANGHAI = ZoneInfo("Asia/Shanghai")
CENT = Decimal("0.01")
STRATEGY_ORDER_PREFIX = "managed-strategy-"
DEFAULT_FEES = {
    "commission_rate": "0.0003", "minimum_commission": "5",
    "sell_tax_rate": "0.0005", "transfer_rate": "0.00001",
}


def money(value):
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError("non-finite money")
    return number.quantize(CENT, rounding=ROUND_HALF_UP)


def aware(value):
    result = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(result, datetime) or result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("timezone-aware timestamp required")
    return result


def supported(symbol):
    return isinstance(symbol, str) and re.fullmatch(r"(?:sh\.60\d{4}|sz\.(?:000|001|002|003|300|301)\d{3})", symbol)


class PaperBroker:
    """Each mutation serializes on the tenant/account row, including quote intake."""

    def __init__(self, connection, tenant_id: str, account_id: str, *, fee_policy=None):
        if not tenant_id or not account_id:
            raise ValueError("tenant and account required")
        self.connection = connection
        self.tenant_id, self.account_id = tenant_id, account_id
        self.key = (tenant_id, account_id)
        self.fees = dict(DEFAULT_FEES if fee_policy is None else fee_policy)
        if set(self.fees) != set(DEFAULT_FEES):
            raise ValueError("complete fee policy required")
        for value in self.fees.values():
            number = Decimal(str(value))
            if not number.is_finite() or number < 0:
                raise ValueError("invalid fee policy")
        self.fees = {key: str(value) for key, value in self.fees.items()}

    def _cursor(self):
        return self.connection.cursor(cursor_factory=RealDictCursor)

    def _account(self, cursor, *, lock=True):
        query = "SELECT * FROM qd_ashare_accounts WHERE tenant_id=%s AND account_id=%s"
        cursor.execute(query + (" FOR UPDATE" if lock else ""), self.key)
        account = cursor.fetchone()
        if account is None:
            raise ValueError("account not initialized")
        self.fees = account["fee_policy"]
        return account

    def _audit(self, cursor, event, details):
        cursor.execute("INSERT INTO qd_ashare_audit(tenant_id,account_id,event,details) VALUES (%s,%s,%s,%s::jsonb)",
                       (*self.key, event, json.dumps(details, default=str)))

    def _costs(self, notional, side):
        commission = max(money(self.fees["minimum_commission"]), money(notional * Decimal(self.fees["commission_rate"]))) if notional else Decimal("0")
        transfer = money(notional * Decimal(self.fees["transfer_rate"]))
        tax = money(notional * Decimal(self.fees["sell_tax_rate"])) if side == "sell" else Decimal("0")
        return commission, transfer, tax

    def _buy_reserve(self, quantity, price):
        notional = price * quantity
        return notional + sum(self._costs(notional, "buy"))

    def initialize(self, initial_cash: Decimal):
        cash = money(initial_cash)
        if cash <= 0:
            raise ValueError("positive capital required")
        with self.connection, self._cursor() as cursor:
            cursor.execute("INSERT INTO qd_ashare_accounts(tenant_id,account_id,initial_cash,cash,fee_policy) VALUES (%s,%s,%s,%s,%s::jsonb) ON CONFLICT DO NOTHING",
                           (*self.key, cash, cash, json.dumps(self.fees)))
            account = self._account(cursor)
            if account["initial_cash"] != cash:
                raise ValueError("existing account capital cannot be reset")
        return self.snapshot()["account"]

    def place_order(self, client_order_id, symbol, side, quantity: int, limit_price: Decimal,
                    submitted_at: datetime, trading_day: date):
        submitted_at = aware(submitted_at)
        if isinstance(trading_day, str):
            trading_day = date.fromisoformat(trading_day)
        price = money(limit_price)
        if (not client_order_id or len(client_order_id) > 160 or client_order_id.startswith(STRATEGY_ORDER_PREFIX)
                or not supported(symbol)):
            raise ValueError("invalid id or unsupported A-share symbol")
        if side not in ("buy", "sell") or type(quantity) is not int or quantity <= 0 or quantity % 100:
            raise ValueError("only positive 100-share lots are supported")
        if price <= 0 or Decimal(str(limit_price)) != price:
            raise ValueError("positive whole-cent limit required")
        if submitted_at.astimezone(SHANGHAI).date() != trading_day:
            raise ValueError("order trading day mismatch")
        with self.connection, self._cursor() as cursor:
            account = self._account(cursor)
            return self._place_order_locked(cursor, account, client_order_id, symbol, side, quantity,
                                            price, submitted_at, trading_day)

    def _place_order_locked(self, cursor, account, client_order_id, symbol, side, quantity, price,
                            submitted_at, trading_day, *, max_cash=None, sell_upper_bound=None):
        """Insert with the caller already holding the account row and validating inputs."""
        existing = self._find_order(cursor, client_order_id)
        if existing:
            if any(existing[key] != value for key, value in {"symbol": symbol, "side": side, "quantity": quantity, "limit_price": price,
                                                           "submitted_at": submitted_at, "trading_day": trading_day}.items()):
                raise ValueError("idempotency key payload conflict")
            return dict(existing)
        reserve = Decimal("0")
        if side == "buy":
            reserve = self._buy_reserve(quantity, price)
            if max_cash is not None and reserve > max_cash:
                raise ValueError("strategy_cash_cap_exceeded")
            if reserve > account["cash"] - account["reserved_cash"]:
                raise ValueError("insufficient available cash")
        else:
            if max_cash is not None:
                if sell_upper_bound is None or sell_upper_bound < price:
                    raise ValueError("strategy_sell_upper_band_required")
                worst_notional = sell_upper_bound * quantity
                if worst_notional + sum(self._costs(worst_notional, "sell")) > max_cash:
                    raise ValueError("strategy_cash_cap_exceeded")
            cursor.execute("SELECT COALESCE(SUM(quantity),0) AS qty FROM qd_ashare_lots WHERE tenant_id=%s AND account_id=%s AND symbol=%s AND acquired_day<%s", (*self.key, symbol, trading_day))
            settled = cursor.fetchone()["qty"]
            cursor.execute("SELECT COALESCE(SUM(quantity-filled),0) AS qty FROM qd_ashare_orders WHERE tenant_id=%s AND account_id=%s AND symbol=%s AND side='sell' AND status IN ('open','partial')", (*self.key, symbol))
            if quantity > settled - cursor.fetchone()["qty"]:
                raise ValueError("insufficient T+1 available shares")
        cursor.execute("UPDATE qd_ashare_accounts SET reserved_cash=reserved_cash+%s WHERE tenant_id=%s AND account_id=%s", (reserve, *self.key))
        cursor.execute("INSERT INTO qd_ashare_orders(tenant_id,account_id,client_order_id,symbol,side,quantity,limit_price,submitted_at,trading_day,status,reserved_cash) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'open',%s) RETURNING *",
                       (*self.key, client_order_id, symbol, side, quantity, price, submitted_at, trading_day, reserve))
        order = dict(cursor.fetchone())
        self._audit(cursor, "order_placed", {"id": order["id"], "client_order_id": client_order_id})
        return order

    def _release(self, cursor, order, status):
        cursor.execute("UPDATE qd_ashare_accounts SET reserved_cash=reserved_cash-%s WHERE tenant_id=%s AND account_id=%s", (order["reserved_cash"], *self.key))
        cursor.execute("UPDATE qd_ashare_orders SET reserved_cash=0,status=%s WHERE id=%s RETURNING *", (status, order["id"]))
        result = dict(cursor.fetchone())
        self._audit(cursor, "order_" + status, {"id": order["id"]})
        return result

    def cancel(self, order_id):
        with self.connection, self._cursor() as cursor:
            self._account(cursor)
            cursor.execute("SELECT * FROM qd_ashare_orders WHERE tenant_id=%s AND account_id=%s AND id=%s", (*self.key, order_id))
            order = cursor.fetchone()
            if not order:
                raise ValueError("unknown order")
            return self._release(cursor, order, "cancelled") if order["status"] in ("open", "partial") else dict(order)

    def expire_day_orders(self, trading_day: date):
        """Release prior-day reservations without requiring a fresh market quote."""
        if type(trading_day) is not date:
            raise ValueError("trading_day must be a date")
        with self.connection, self._cursor() as cursor:
            self._account(cursor)
            cursor.execute(
                "SELECT * FROM qd_ashare_orders WHERE tenant_id=%s AND account_id=%s "
                "AND status IN ('open','partial') AND trading_day<%s ORDER BY id",
                (*self.key, trading_day),
            )
            return [self._release(cursor, order, "expired") for order in cursor.fetchall()]

    def on_quote(self, quote: dict, rules: dict, now: datetime):
        from app.ashare.market import validate_quote
        from app.ashare.calendar_rules import market_session
        now = aware(now)
        validate_quote(quote, now=now, max_age_seconds=15)
        timestamp = aware(quote["exchange_time"])
        if not market_session(now)["can_match"] or not market_session(timestamp)["can_match"]:
            raise ValueError("outside verified continuous trading session")
        local = timestamp.astimezone(SHANGHAI)
        day = local.date()
        if not supported(quote["symbol"]) or not quote.get("quote_id") or not quote.get("evidence_sha256"):
            raise ValueError("unsupported or untraceable quote")
        name = str(quote.get("name") or "").upper()
        if name.startswith(("ST", "*ST")) or "退" in name:
            raise ValueError("quote name contradicts supported security status")
        if any(Decimal(str(quote[field])) != money(quote[field]) for field in ("bid", "ask", "last")):
            raise ValueError("subcent quote price unsupported")
        if rules.get("reviewed") is not True or rules.get("is_trading_day") is not True or rules.get("trading_day") != day.isoformat():
            raise ValueError("dated reviewed trading rules required")
        if type(rules.get("suspended")) is not bool or type(rules.get("st")) is not bool:
            raise ValueError("security status unknown")
        if rules["suspended"] or rules["st"]:
            raise ValueError("suspended and ST shares are unsupported")
        if now.astimezone(SHANGHAI).date() != day:
            raise ValueError("quote trading day mismatch")
        if not (time(9, 30) <= local.time() < time(11, 30) or time(13) <= local.time() < time(14, 57)):
            raise ValueError("outside continuous auction")
        lower, upper = money(rules["limit_down"]), money(rules["limit_up"])
        if lower <= 0 or upper <= lower:
            raise ValueError("invalid reviewed price limits")
        for key, bound in (("limit_down", lower), ("limit_up", upper)):
            if quote.get(key) is not None and money(quote[key]) != bound:
                raise ValueError("quote and reviewed price limits disagree")
        if not lower <= money(quote["last"]) <= upper:
            raise ValueError("last price outside daily limits")
        for key in ("bid_size", "ask_size"):
            if type(quote[key]) is not int or quote[key] < 0:
                raise ValueError("invalid displayed size")
        with self.connection, self._cursor() as cursor:
            self._account(cursor)
            cursor.execute("SELECT MAX(exchange_time) AS latest FROM qd_ashare_quotes WHERE tenant_id=%s AND account_id=%s AND symbol=%s", (*self.key, quote["symbol"]))
            latest = cursor.fetchone()["latest"]
            if latest is not None and timestamp <= latest:
                return []
            cursor.execute("INSERT INTO qd_ashare_quotes(tenant_id,account_id,symbol,exchange_time,quote_id) VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING quote_id", (*self.key, quote["symbol"], timestamp, quote["quote_id"]))
            if cursor.fetchone() is None:
                return []
            self._audit(cursor, "quote_accepted", {"quote_id": quote["quote_id"],
                        "exchange_time": timestamp, "received_at": quote["received_at"],
                        "source": quote.get("source"), "evidence_sha256": quote["evidence_sha256"],
                        "rules": rules})
            cursor.execute("SELECT * FROM qd_ashare_orders WHERE tenant_id=%s AND account_id=%s AND status IN ('open','partial') AND trading_day<%s", (*self.key, day))
            for expired in cursor.fetchall():
                self._release(cursor, expired, "expired")
            cursor.execute("SELECT * FROM qd_ashare_orders WHERE tenant_id=%s AND account_id=%s AND symbol=%s AND trading_day=%s AND status IN ('open','partial') ORDER BY submitted_at,id", (*self.key, quote["symbol"], day))
            orders = cursor.fetchall()
            strategy_allowed = False
            if any(order["client_order_id"].startswith(STRATEGY_ORDER_PREFIX) for order in orders):
                # A review can change after the worker reads it. Hold the web row until matching commits.
                cursor.execute("SELECT rules -> %s AS rule FROM qd_ashare_web WHERE tenant_id=%s AND account_id=%s FOR UPDATE",
                               (quote["symbol"], *self.key))
                reviewed = cursor.fetchone()
                strategy_allowed = (reviewed is not None and reviewed["rule"] == rules
                                    and rules.get("approval_method") == "explicit_human_review")
            capacities = {"buy": quote["ask_size"], "sell": quote["bid_size"]}
            fills = []
            for order in orders:
                if order["client_order_id"].startswith(STRATEGY_ORDER_PREFIX) and not strategy_allowed:
                    continue
                side = order["side"]
                price = money(quote["ask" if side == "buy" else "bid"])
                if timestamp <= order["submitted_at"] or not lower <= price <= upper:
                    continue
                if not lower <= order["limit_price"] <= upper:
                    continue
                if side == "buy" and price > order["limit_price"] or side == "sell" and price < order["limit_price"]:
                    continue
                quantity = min(order["quantity"] - order["filled"], capacities[side]) // 100 * 100
                if not quantity:
                    continue
                if order["client_order_id"].startswith(STRATEGY_ORDER_PREFIX):
                    try:
                        self._validate_strategy_fill(cursor, order, rules, quantity, price)
                    except ValueError as exc:
                        self._audit(cursor, "strategy_order_fill_blocked", {
                            "order_id": order["id"], "reason": str(exc), "quote_id": quote["quote_id"],
                        })
                        continue
                capacities[side] -= quantity
                fills.append(self._fill(cursor, order, quantity, price, quote, timestamp, day))
            return fills

    def _validate_strategy_fill(self, cursor, order, rules, quantity, price):
        """Recheck immutable authorization and actual absolute notional before every managed fill."""
        from app.ashare.strategy_versions import StrategyVersions

        versions = StrategyVersions(self.connection, *self.key)
        version = versions._find(cursor, order["client_order_id"][len(STRATEGY_ORDER_PREFIX):])
        config = version["config"]
        side = "sell" if config["kind"] == "sell_once_above" else "buy"
        if (not version["execution_allowed"] or rules.get("reviewed_by") != version["activated_by"]
                or order["side"] != side or order["symbol"] != config["symbol"]
                or order["quantity"] != config["quantity"]
                or order["limit_price"] != Decimal(config["limit_price"])):
            raise ValueError("strategy_execution_authorization_unavailable")
        versions._verify_research(cursor, config)
        notional = order["notional"] + quantity * price
        if notional + sum(self._costs(notional, side)) > Decimal(config["max_cash"]):
            raise ValueError("strategy_cash_cap_exceeded")

    def _fill(self, cursor, order, quantity, price, quote, timestamp, day):
        side = order["side"]
        trade_value = price * quantity
        cumulative = order["notional"] + trade_value
        commission, transfer, tax = self._costs(cumulative, side)
        fee = commission + transfer + tax - order["commission"] - order["transfer_fee"] - order["sell_tax"]
        remaining = order["quantity"] - order["filled"] - quantity
        reserve = Decimal("0")
        if side == "buy":
            if remaining:
                worst_value = remaining * order["limit_price"]
                reserve = worst_value + sum(self._costs(cumulative + worst_value, side)) - commission - transfer - tax
            cursor.execute("INSERT INTO qd_ashare_lots(tenant_id,account_id,symbol,acquired_day,quantity) VALUES (%s,%s,%s,%s,%s)", (*self.key, order["symbol"], day, quantity))
            cash_delta = -trade_value - fee
        else:
            cursor.execute("SELECT * FROM qd_ashare_lots WHERE tenant_id=%s AND account_id=%s AND symbol=%s AND acquired_day<%s AND quantity>0 ORDER BY acquired_day,id", (*self.key, order["symbol"], day))
            to_sell = quantity
            for lot in cursor.fetchall():
                consumed = min(to_sell, lot["quantity"])
                cursor.execute("UPDATE qd_ashare_lots SET quantity=quantity-%s WHERE id=%s", (consumed, lot["id"]))
                to_sell -= consumed
                if not to_sell:
                    break
            if to_sell:
                raise ValueError("settled share invariant violated")
            cash_delta = trade_value - fee
        cursor.execute("UPDATE qd_ashare_accounts SET cash=cash+%s,reserved_cash=reserved_cash-%s+%s WHERE tenant_id=%s AND account_id=%s",
                       (cash_delta, order["reserved_cash"], reserve, *self.key))
        cursor.execute("UPDATE qd_ashare_orders SET filled=filled+%s,status=%s,reserved_cash=%s,notional=%s,commission=%s,transfer_fee=%s,sell_tax=%s WHERE id=%s",
                       (quantity, "filled" if remaining == 0 else "partial", reserve, cumulative, commission, transfer, tax, order["id"]))
        cursor.execute("INSERT INTO qd_ashare_fills(tenant_id,account_id,order_id,symbol,side,quantity,price,fee,exchange_time,quote_id,evidence_sha256) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
                       (*self.key, order["id"], order["symbol"], side, quantity, price, fee, timestamp, quote["quote_id"], quote["evidence_sha256"]))
        fill = dict(cursor.fetchone())
        self._audit(cursor, "paper_fill", fill)
        return fill

    def find_order(self, client_order_id):
        """Look up an idempotency key without relying on a bounded UI history."""
        with self.connection, self._cursor() as cursor:
            return self._find_order(cursor, client_order_id)

    def _find_order(self, cursor, client_order_id):
        cursor.execute("SELECT * FROM qd_ashare_orders WHERE tenant_id=%s AND account_id=%s AND client_order_id=%s",
                       (*self.key, client_order_id))
        row = cursor.fetchone()
        return dict(row) if row else None

    def snapshot(self, history_limit=None, *, include_audit=True):
        """Return active state and optional bounded history; never trim open orders."""
        if history_limit is not None and (type(history_limit) is not int or not 1 <= history_limit <= 1000):
            raise ValueError("invalid history limit")
        with self.connection, self._cursor() as cursor:
            account = dict(self._account(cursor))
            account["available_cash"] = account["cash"] - account["reserved_cash"]
            account["currency"] = "CNY"
            result = {"account": account}
            truncated = {}
            for key, table in (("orders", "qd_ashare_orders"), ("fills", "qd_ashare_fills"), ("audit", "qd_ashare_audit")):
                if key == "audit" and not include_audit:
                    result[key] = []
                    continue
                if history_limit is None:
                    cursor.execute(f"SELECT * FROM {table} WHERE tenant_id=%s AND account_id=%s ORDER BY id", self.key)
                    result[key] = [dict(row) for row in cursor.fetchall()]
                    continue
                closed = " AND status NOT IN ('open','partial')" if key == "orders" else ""
                cursor.execute(f"SELECT * FROM {table} WHERE tenant_id=%s AND account_id=%s{closed} ORDER BY id DESC LIMIT %s",
                               (*self.key, history_limit + 1))
                rows = [dict(row) for row in cursor.fetchall()]
                truncated[key] = len(rows) > history_limit
                rows = rows[:history_limit]
                if key == "orders":
                    cursor.execute("SELECT * FROM qd_ashare_orders WHERE tenant_id=%s AND account_id=%s AND status IN ('open','partial')", self.key)
                    rows.extend(dict(row) for row in cursor.fetchall())
                result[key] = sorted(rows, key=lambda row: row["id"])
            if history_limit is not None:
                result["history"] = {"limit": history_limit, "truncated": truncated}
            cursor.execute("SELECT COALESCE(SUM(commission),0) AS commission, COALESCE(SUM(transfer_fee),0) AS transfer_fee, COALESCE(SUM(sell_tax),0) AS sell_tax FROM qd_ashare_orders WHERE tenant_id=%s AND account_id=%s", self.key)
            fees = cursor.fetchone()
            for field in ("commission", "transfer_fee", "sell_tax"):
                account["total_" + field] = fees[field]
            account["total_fees"] = account["total_commission"] + account["total_transfer_fee"] + account["total_sell_tax"]
            cursor.execute("SELECT symbol,acquired_day,SUM(quantity) AS quantity FROM qd_ashare_lots WHERE tenant_id=%s AND account_id=%s AND quantity>0 GROUP BY symbol,acquired_day ORDER BY symbol,acquired_day", self.key)
            result["positions"] = [dict(row) for row in cursor.fetchall()]
            return result
