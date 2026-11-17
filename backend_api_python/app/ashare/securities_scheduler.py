"""Post-close historical ingestion, separate from quote matching and approval."""
from __future__ import annotations

import argparse
import json
from contextlib import closing
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg2

from app.ashare.calendar_rules import trading_day_status
from app.ashare.schema import get_database_url

SHANGHAI = ZoneInfo("Asia/Shanghai")


def completed_days(now, lookback=3, calendar=trading_day_status):
    """Only request completed sessions; unknown calendar dates are not guessed."""
    if now.utcoffset() is None or type(lookback) is not int or not 1 <= lookback <= 20:
        raise ValueError("aware time and lookback 1..20 required")
    local = now.astimezone(SHANGHAI)
    day = local.date()
    # Daily vendor data is not assumed available at the exchange closing bell.
    if local.time() < time(19):
        day -= timedelta(days=1)
    days = []
    for offset in range(80):
        candidate = day - timedelta(days=offset)
        status = calendar(candidate)
        if status["state"] == "calendar_unknown":
            raise ValueError("calendar coverage unknown")
        if status["is_trading_day"]:
            days.append(candidate.isoformat())
        if len(days) == lookback:
            return list(reversed(days))
    raise ValueError("insufficient completed calendar sessions")


def watched_symbols(connection):
    """The shared public dataset includes watchlists and existing exposure."""
    with connection, connection.cursor() as cursor:
        cursor.execute("""SELECT DISTINCT symbol FROM (
          SELECT jsonb_array_elements_text(settings->'symbols') AS symbol FROM qd_ashare_web
          UNION SELECT symbol FROM qd_ashare_lots WHERE quantity > 0
          UNION SELECT symbol FROM qd_ashare_orders WHERE status IN ('open','partial')
        ) AS watched ORDER BY symbol""")
        return [row[0] for row in cursor.fetchall()]


def sync(connection, evidence_dir, *, now=None, lookback=3, ingest=None):
    if ingest is None:
        from app.ashare.securities import ingest_baostock
        ingest = ingest_baostock
    days = completed_days(now or datetime.now(timezone.utc), lookback)
    symbols = watched_symbols(connection)
    # A corrupt or unexpectedly large configuration must not cause an unbounded job.
    if len(symbols) > 30:
        raise ValueError("scheduled status coverage capped at 30 watched securities")
    receipts, failures = [], []
    for day in reversed(days):
        try:
            receipts.append(ingest(connection, Path(evidence_dir), day, symbols=symbols, timeout=60))
        except (ValueError, TimeoutError) as exc:
            # Each dated request is independent. A historical outage must not
            # hide the latest day or prevent the remaining bounded attempts.
            code = str(exc)
            if code not in {"baostock_provider_timeout", "baostock_provider_failed", "baostock_provider_invalid_response"}:
                code = "securities_source_rejected"
            failures.append({"trading_day": day, "error": code})
    return {"mode": "paper", "status": "partial" if failures and receipts else "failed" if failures else "completed",
            "days_requested": days, "status_symbols_requested": len(symbols),
            "status_coverage": "watched_and_existing_exposure_only", "receipts": receipts,
            "failures": failures, "approvals_created": 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--lookback", type=int, default=3)
    args = parser.parse_args(argv)
    dsn = get_database_url()
    if not dsn:
        parser.error("ASHARE_DATABASE_URL or DATABASE_URL required")
    try:
        with closing(psycopg2.connect(dsn, connect_timeout=10,
                                     options="-c statement_timeout=30000 -c lock_timeout=5000")) as connection:
            result = sync(connection, args.evidence_dir, lookback=args.lookback)
        print(json.dumps(result, ensure_ascii=False, default=str))
        return 1 if result["failures"] else 0
    except Exception as exc:
        # Provider exception strings may contain transport or database details.
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "approvals_created": 0}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
