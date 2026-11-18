"""Durable paper-worker health and immutable ledger-derived daily reports."""
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
import json

from psycopg2.extras import RealDictCursor

from app.ashare.broker import PaperBroker
from app.ashare.calendar_rules import SHANGHAI, trading_day_status

PROCESS_NAME = "managed_paper_worker"
DAILY_FEISHU_ATTEMPT = "daily_report_feishu_attempt"
DAILY_FEISHU_RESULT = "daily_report_feishu_result"
DAILY_FEISHU_STATUSES = frozenset({"unknown", "webhook_acknowledged", "disabled", "rejected", "failed"})


def write_heartbeat(connection, instance_id, now, details):
    """Called only by the process holding the global worker advisory lock."""
    with connection, connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO qd_ashare_process_health(process_name,instance_id,observed_at,details) "
            "VALUES (%s,%s,%s,%s::jsonb) ON CONFLICT(process_name) DO UPDATE "
            "SET instance_id=EXCLUDED.instance_id,observed_at=EXCLUDED.observed_at,details=EXCLUDED.details",
            (PROCESS_NAME, instance_id, now, json.dumps(details, default=str)),
        )


def read_health(connection, now, max_age_seconds=120):
    """Process liveness does not imply fresh quotes or permission to trade."""
    with connection, connection.cursor(cursor_factory=RealDictCursor) as cursor:
        cursor.execute("SELECT * FROM qd_ashare_process_health WHERE process_name=%s", (PROCESS_NAME,))
        row = cursor.fetchone()
    if row is None:
        return {"healthy": False, "reason": "worker_not_observed", "mode": "paper"}
    age = (now - row["observed_at"]).total_seconds()
    details = row["details"]
    healthy = 0 <= age <= max_age_seconds and details.get("status") in ("running", "starting")
    return {"healthy": healthy, "reason": "alive" if healthy else "worker_stale_or_stopped",
            "mode": "paper", "age_seconds": age, "observed_at": row["observed_at"].isoformat(),
            "instance_id": row["instance_id"], "details": details}


def reconcile_snapshot(snapshot):
    """Check cash, shares, per-order fills, fees and reservations against fills."""
    account = snapshot["account"]
    expected_cash = Decimal(str(account["initial_cash"]))
    shares = defaultdict(int)
    by_order = defaultdict(lambda: {"quantity": 0, "fee": Decimal("0"), "notional": Decimal("0")})
    for fill in snapshot["fills"]:
        value = Decimal(str(fill["price"])) * fill["quantity"]
        fee = Decimal(str(fill["fee"]))
        direction = 1 if fill["side"] == "buy" else -1
        expected_cash -= direction * value + fee
        shares[fill["symbol"]] += direction * fill["quantity"]
        sums = by_order[fill["order_id"]]
        sums["quantity"] += fill["quantity"]
        sums["fee"] += fee
        sums["notional"] += value
    errors = []
    if expected_cash != Decimal(str(account["cash"])):
        errors.append("cash_does_not_match_fills")
    held = defaultdict(int)
    for lot in snapshot["positions"]:
        held[lot["symbol"]] += lot["quantity"]
    if {k: v for k, v in shares.items() if v} != dict(held):
        errors.append("positions_do_not_match_fills")
    reserved = Decimal("0")
    for order in snapshot["orders"]:
        sums = by_order[order["id"]]
        if sums["quantity"] != order["filled"] or sums["notional"] != Decimal(str(order["notional"])):
            errors.append("order_fill_totals_mismatch")
        fees = sum((Decimal(str(order[k])) for k in ("commission", "transfer_fee", "sell_tax")), Decimal("0"))
        if fees != sums["fee"]:
            errors.append("order_fee_totals_mismatch")
        reserve = Decimal(str(order["reserved_cash"]))
        if order["status"] in ("open", "partial"):
            reserved += reserve
        elif reserve:
            errors.append("closed_order_has_reservation")
    if reserved != Decimal(str(account["reserved_cash"])):
        errors.append("reservation_totals_mismatch")
    return {"ok": not errors, "errors": sorted(set(errors)), "expected_cash": str(expected_cash)}


def reconcile_account(connection, tenant_id, account_id):
    """Use bounded aggregate results; UI history truncation cannot affect checks."""
    key = (tenant_id, account_id)
    errors = []
    with connection, connection.cursor(cursor_factory=RealDictCursor) as cursor:
        cursor.execute("SELECT initial_cash,cash,reserved_cash FROM qd_ashare_accounts WHERE tenant_id=%s AND account_id=%s FOR UPDATE", key)
        account = cursor.fetchone()
        if account is None:
            raise ValueError("account not initialized")
        cursor.execute("SELECT COALESCE(SUM(CASE WHEN side='buy' THEN -price*quantity-fee ELSE price*quantity-fee END),0) AS change FROM qd_ashare_fills WHERE tenant_id=%s AND account_id=%s", key)
        expected_cash = account["initial_cash"] + cursor.fetchone()["change"]
        if expected_cash != account["cash"]:
            errors.append("cash_does_not_match_fills")
        cursor.execute("SELECT symbol,SUM(CASE WHEN side='buy' THEN quantity ELSE -quantity END) AS quantity FROM qd_ashare_fills WHERE tenant_id=%s AND account_id=%s GROUP BY symbol", key)
        expected_shares = {row["symbol"]: row["quantity"] for row in cursor.fetchall() if row["quantity"]}
        cursor.execute("SELECT symbol,SUM(quantity) AS quantity FROM qd_ashare_lots WHERE tenant_id=%s AND account_id=%s AND quantity>0 GROUP BY symbol", key)
        actual_shares = {row["symbol"]: row["quantity"] for row in cursor.fetchall()}
        if expected_shares != actual_shares:
            errors.append("positions_do_not_match_fills")
        cursor.execute("SELECT COALESCE(SUM(reserved_cash) FILTER(WHERE status IN ('open','partial')),0) AS reserved, COUNT(*) FILTER(WHERE status NOT IN ('open','partial') AND reserved_cash<>0) AS invalid FROM qd_ashare_orders WHERE tenant_id=%s AND account_id=%s", key)
        reservations = cursor.fetchone()
        if reservations["reserved"] != account["reserved_cash"]:
            errors.append("reservation_totals_mismatch")
        if reservations["invalid"]:
            errors.append("closed_order_has_reservation")
        cursor.execute("""SELECT COUNT(*) AS invalid FROM qd_ashare_orders o LEFT JOIN (
            SELECT order_id,SUM(quantity) AS quantity,SUM(price*quantity) AS notional,SUM(fee) AS fee
            FROM qd_ashare_fills WHERE tenant_id=%s AND account_id=%s GROUP BY order_id
            ) f ON f.order_id=o.id WHERE o.tenant_id=%s AND o.account_id=%s AND
            (o.filled<>COALESCE(f.quantity,0) OR o.notional<>COALESCE(f.notional,0) OR
             o.commission+o.transfer_fee+o.sell_tax<>COALESCE(f.fee,0))""", (*key, *key))
        if cursor.fetchone()["invalid"]:
            errors.append("order_fill_or_fee_totals_mismatch")
    return {"ok": not errors, "errors": errors, "expected_cash": str(expected_cash)}


def record_poll(connection, tenant_id, account_id, now, quotes, state, clock_source):
    """Persist bounded observation evidence; sample counts do not prove uptime."""
    day = now.astimezone(SHANGHAI).date()
    ages, timestamps, evidence = [], [], set()
    for quote in quotes:
        try:
            stamp = datetime.fromisoformat(quote["exchange_time"])
            if stamp.utcoffset() is None:
                continue
            ages.append((now - stamp).total_seconds())
            timestamps.append(stamp.isoformat())
            if quote.get("evidence_sha256"):
                evidence.add(quote["evidence_sha256"])
        except (KeyError, TypeError, ValueError):
            continue
    max_age = max(ages) if ages else None
    stale = bool(ages and (max(ages) > 15 or min(ages) < 0))
    details = {"clock_source": clock_source, "worker_state": state,
               "quote_count": len(quotes), "source_timestamps": timestamps[:30],
               "evidence_sha256": sorted(evidence)[:30]}
    with connection, connection.cursor() as cursor:
        cursor.execute("""INSERT INTO qd_ashare_day_observations
            (tenant_id,account_id,trading_day,first_observed_at,last_observed_at,poll_count,
             wall_clock_polls,controlled_time_polls,failed_polls,ready_polls,stale_quote_polls,
             max_source_age_seconds,last_details)
            VALUES(%s,%s,%s,%s,%s,1,%s,%s,%s,%s,%s,%s,%s::jsonb)
            ON CONFLICT(tenant_id,account_id,trading_day) DO UPDATE SET
            first_observed_at=LEAST(qd_ashare_day_observations.first_observed_at,EXCLUDED.first_observed_at),
            last_observed_at=GREATEST(qd_ashare_day_observations.last_observed_at,EXCLUDED.last_observed_at),
            poll_count=qd_ashare_day_observations.poll_count+1,
            wall_clock_polls=qd_ashare_day_observations.wall_clock_polls+EXCLUDED.wall_clock_polls,
            controlled_time_polls=qd_ashare_day_observations.controlled_time_polls+EXCLUDED.controlled_time_polls,
            failed_polls=qd_ashare_day_observations.failed_polls+EXCLUDED.failed_polls,
            ready_polls=qd_ashare_day_observations.ready_polls+EXCLUDED.ready_polls,
            stale_quote_polls=qd_ashare_day_observations.stale_quote_polls+EXCLUDED.stale_quote_polls,
            max_source_age_seconds=GREATEST(qd_ashare_day_observations.max_source_age_seconds,EXCLUDED.max_source_age_seconds),
            last_details=EXCLUDED.last_details""",
            (tenant_id, account_id, day, now, now, int(clock_source == "wall_clock"),
             int(clock_source == "controlled_time"), int(state == "failed"), int(state == "healthy"),
             int(stale), max_age, json.dumps(details)))


def observation_summary(connection, tenant_id, account_id, day):
    with connection, connection.cursor(cursor_factory=RealDictCursor) as cursor:
        cursor.execute("SELECT * FROM qd_ashare_day_observations WHERE tenant_id=%s AND account_id=%s AND trading_day=%s", (tenant_id, account_id, day))
        row = cursor.fetchone()
    if row is None:
        return {"status": "not_observed", "poll_count": 0, "coverage": "not_proven"}
    result = dict(row)
    result.pop("tenant_id")
    result.pop("account_id")
    for name in ("trading_day", "first_observed_at", "last_observed_at"):
        result[name] = result[name].isoformat()
    result.update(status="samples_recorded", coverage="sampled_not_continuous_uptime_proof")
    return result


def _daily_report(snapshot, day, now):
    cash = Decimal(str(snapshot["account"]["initial_cash"]))
    shares = defaultdict(int)
    buy = sell = fees = Decimal("0")
    daily_count = 0
    evidence = set()
    for fill in snapshot["fills"]:
        timestamp = fill["exchange_time"]
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp)
        fill_day = timestamp.astimezone(SHANGHAI).date()
        if fill_day > day:
            continue
        value = Decimal(str(fill["price"])) * fill["quantity"]
        fee = Decimal(str(fill["fee"]))
        direction = 1 if fill["side"] == "buy" else -1
        cash -= direction * value + fee
        shares[fill["symbol"]] += direction * fill["quantity"]
        if fill_day == day:
            daily_count += 1
            fees += fee
            if direction == 1:
                buy += value
            else:
                sell += value
            evidence.add(fill["evidence_sha256"])
    return {
        "mode": "paper", "currency": "CNY", "provenance": "ledger_reconstruction",
        "trading_day": day.isoformat(), "generated_at": now.isoformat(),
        "late_generation": now.astimezone(SHANGHAI).date() > day,
        "cash_end": str(cash), "positions_end": [{"symbol": symbol, "quantity": quantity} for symbol, quantity in sorted(shares.items()) if quantity],
        "day_fill_count": daily_count, "buy_notional": str(buy), "sell_notional": str(sell), "fees": str(fees),
        "fill_evidence_sha256": sorted(evidence), "equity_end": None,
        "valuation_status": "unavailable_no_verified_closing_prices",
        "reconciliation": {**reconcile_snapshot(snapshot), "scope": "entire_ledger_at_generation"},
    }


def complete_daily_reports(connection, tenant_id, account_id, now, session, *, max_days=31):
    """Catch up bounded calendar dates, never assign today's positions to history.

    The first observed date (or earliest actual order date for existing ledgers)
    bounds coverage. Reports are immutable; a restart cannot duplicate them.
    Non-session dates are scanned without fabricating a trading-day report.
    """
    day = now.astimezone(SHANGHAI).date()
    target = day if session["state"] == "closed" else day - timedelta(days=1)
    key = (tenant_id, account_id)
    with connection, connection.cursor(cursor_factory=RealDictCursor) as cursor:
        cursor.execute("SELECT MIN(trading_day) AS day FROM qd_ashare_orders WHERE tenant_id=%s AND account_id=%s", key)
        first_order = cursor.fetchone()["day"]
        first_day = min(day, first_order) if first_order else day
        cursor.execute("INSERT INTO qd_ashare_report_progress(tenant_id,account_id,first_observed_day) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING", (*key, first_day))
        cursor.execute("SELECT * FROM qd_ashare_report_progress WHERE tenant_id=%s AND account_id=%s", key)
        progress = cursor.fetchone()
    candidate = progress["last_scanned_day"] + timedelta(days=1) if progress["last_scanned_day"] else progress["first_observed_day"]
    if candidate > target:
        return []
    snapshot = PaperBroker(connection, *key).snapshot(include_audit=False)
    created = []
    for _ in range(max_days):
        if candidate > target:
            break
        status = trading_day_status(candidate)
        if status["state"] == "calendar_unknown":
            break
        if status["is_trading_day"]:
            report = _daily_report(snapshot, candidate, now)
            report["observation_summary"] = observation_summary(connection, tenant_id, account_id, candidate)
        with connection, connection.cursor() as cursor:
            if status["is_trading_day"]:
                cursor.execute("INSERT INTO qd_ashare_daily_reports(tenant_id,account_id,trading_day,generated_at,report) VALUES(%s,%s,%s,%s,%s::jsonb) ON CONFLICT DO NOTHING RETURNING trading_day",
                               (*key, candidate, now, json.dumps(report)))
                if cursor.fetchone():
                    created.append(report)
                    cursor.execute("INSERT INTO qd_ashare_audit(tenant_id,account_id,event,details) VALUES(%s,%s,'daily_report_generated',%s::jsonb)",
                                   (*key, json.dumps({"trading_day": candidate.isoformat(), "reconciliation_ok": report["reconciliation"]["ok"]})))
            cursor.execute("UPDATE qd_ashare_report_progress SET last_scanned_day=%s WHERE tenant_id=%s AND account_id=%s", (candidate, *key))
        candidate += timedelta(days=1)
    return created


def list_daily_reports(connection, tenant_id, account_id="paper", limit=30):
    if type(limit) is not int or not 1 <= limit <= 366:
        raise ValueError("invalid_report_limit")
    with connection, connection.cursor(cursor_factory=RealDictCursor) as cursor:
        cursor.execute("SELECT trading_day,generated_at,report FROM qd_ashare_daily_reports WHERE tenant_id=%s AND account_id=%s ORDER BY trading_day DESC LIMIT %s", (tenant_id, account_id, limit))
        reports = [{
            "trading_day": row["trading_day"].isoformat(), "generated_at": row["generated_at"].isoformat(),
            "report": row["report"], "feishu_delivery": {"status": "not_observed"},
        } for row in cursor.fetchall()]
        if not reports:
            return reports
        by_day = {report["trading_day"]: report for report in reports}
        cursor.execute("""SELECT event,recorded_at,details FROM qd_ashare_audit
            WHERE tenant_id=%s AND account_id=%s AND event IN (%s,%s) AND details->'trading_days' ?| %s
            ORDER BY id""", (tenant_id, account_id, DAILY_FEISHU_ATTEMPT, DAILY_FEISHU_RESULT, list(by_day)))
        attempts = {}
        for row in cursor:
            entry = row["details"]
            if not isinstance(entry, dict) or not isinstance(entry.get("attempt_id"), str):
                continue
            if row["event"] == DAILY_FEISHU_ATTEMPT:
                days = entry.get("trading_days")
                if not isinstance(days, list) or not all(isinstance(day, str) for day in days):
                    continue
                receipt = {"status": "unknown", "attempted_at": row["recorded_at"].isoformat()}
                attempts[entry["attempt_id"]] = receipt
                for day in days:
                    if day in by_day:
                        by_day[day]["feishu_delivery"] = receipt
            elif entry["attempt_id"] in attempts and isinstance(entry.get("status"), str) and entry["status"] in DAILY_FEISHU_STATUSES:
                attempts[entry["attempt_id"]].update(
                    status=entry["status"], recorded_at=row["recorded_at"].isoformat(),
                )
        return reports


def record_daily_feishu_audit(connection, tenant_id, account_id, event, details):
    with connection, connection.cursor() as cursor:
        cursor.execute("INSERT INTO qd_ashare_audit(tenant_id,account_id,event,details) VALUES(%s,%s,%s,%s::jsonb)",
                       (tenant_id, account_id, event, json.dumps(details)))
