"""Run with python -m app.ashare; never initializes upstream trading services."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from contextlib import closing
from decimal import Decimal
from pathlib import Path

from app.ashare.runtime import SHANGHAI, claim_worker, load_config, process_quotes, reviewed_rules, save_report, utc_now


def emit(value):
    print(json.dumps(value, ensure_ascii=False, default=str), flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="A-share paper-only worker (no real broker)")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("reports/ashare"))
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("migrate", "init", "status", "quotes", "report"):
        commands.add_parser(name)
    order = commands.add_parser("order")
    order.add_argument("--id", required=True)
    order.add_argument("--symbol", required=True)
    order.add_argument("--side", choices=("buy", "sell"), required=True)
    order.add_argument("--quantity", type=int, required=True)
    order.add_argument("--limit", type=Decimal, required=True)
    cancel = commands.add_parser("cancel")
    cancel.add_argument("--id", type=int, required=True)
    run = commands.add_parser("run")
    run.add_argument("--cycles", type=int, default=1, help="0 runs until interrupted")
    run.add_argument("--notify", action="store_true", help="Send fill and state-change notices to configured Feishu bot")
    args = parser.parse_args(argv)
    config = load_config(args.config)
    if args.command == "quotes":
        from app.ashare.market import fetch_quotes
        quotes = fetch_quotes(config["symbols"], args.output / "evidence", timeout=15)
        save_report(args.output / "quotes.json", {"quotes": quotes})
        emit({"quotes": quotes})
        return 0

    import psycopg2
    from app.ashare.broker import PaperBroker

    dsn = os.environ.get("ASHARE_DATABASE_URL")
    if not dsn:
        raise ValueError("ASHARE_DATABASE_URL required; use a dedicated paper database")
    with closing(psycopg2.connect(dsn, connect_timeout=10)) as connection:
        if args.command == "migrate":
            migration = Path(__file__).resolve().parents[2] / "migrations" / "20261117_ashare_paper.sql"
            # __file__: app/ashare/__main__.py; backend root is parents[2].
            with connection, connection.cursor() as cursor:
                cursor.execute(migration.read_text(encoding="utf-8"))
            emit({"status": "migrated", "migration": migration.name})
            return 0
        broker = PaperBroker(connection, config["tenant_id"], config["account_id"],
                             fee_policy=config.get("fee_policy"))
        if args.command == "init":
            emit(broker.initialize(Decimal(str(config.get("initial_cash", "100000")))))
        elif args.command in ("status", "report"):
            snapshot = broker.snapshot()
            if args.command == "report":
                save_report(args.output / "daily" / (utc_now().astimezone(SHANGHAI).date().isoformat() + ".json"), snapshot)
            emit(snapshot)
        elif args.command == "order":
            if args.symbol not in config["symbols"]:
                raise ValueError("Symbol not in configured universe")
            now = utc_now()
            rules = reviewed_rules(config, args.symbol, now)
            if not Decimal(rules["limit_down"]) <= args.limit <= Decimal(rules["limit_up"]):
                raise ValueError("Order price outside reviewed daily band")
            emit(broker.place_order(args.id, args.symbol, args.side, args.quantity, args.limit,
                                    submitted_at=now, trading_day=now.astimezone(SHANGHAI).date()))
        elif args.command == "cancel":
            emit(broker.cancel(args.id))
        elif args.command == "run":
            if args.cycles < 0:
                raise ValueError("cycles must be nonnegative")
            from app.ashare.market import fetch_quotes
            from app.ashare.notifications import notify_text

            claim_worker(connection, config)
            previous_state = None
            cycle = 0
            while args.cycles == 0 or cycle < args.cycles:
                cycle += 1
                fatal_database_error = False
                try:
                    quotes = fetch_quotes(config["symbols"], args.output / "evidence", timeout=15)
                    result = process_quotes(broker, config, quotes, utc_now())
                except Exception as exc:
                    fatal_database_error = isinstance(exc, psycopg2.Error)
                    if not connection.closed:
                        connection.rollback()
                    # Never expose DSN, webhook or transport exception text in reports.
                    result = {"observed_at": utc_now().isoformat(), "state": "failed",
                              "error_type": type(exc).__name__, "fills": [], "mode": "paper"}
                result["cycle"] = cycle
                if args.notify and (result["fills"] or result["state"] != previous_state):
                    result["notification"] = notify_text(
                        f"[模拟交易] {config['account_id']} 状态={result['state']} "
                        f"本轮虚拟成交={len(result['fills'])} 时间={result['observed_at']}"
                    )
                previous_state = result["state"]
                save_report(args.output / "latest.json", result)
                save_report(args.output / "daily" / (utc_now().astimezone(SHANGHAI).date().isoformat() + ".json"), result)
                emit({key: value for key, value in result.items() if key != "account"})
                if fatal_database_error:
                    return 1
                if args.cycles == 0 or cycle < args.cycles:
                    time.sleep(float(config.get("interval_seconds", 5)))
            return 0 if previous_state == "healthy" else 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except (ValueError, FileNotFoundError) as exc:
        emit({"status": "rejected", "reason": str(exc)[:250]})
        sys.exit(2)
    except Exception as exc:
        emit({"status": "failed", "error_type": type(exc).__name__})
        sys.exit(1)
