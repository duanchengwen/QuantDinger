"""Synthetic multi-session replay in a generated, disposable PostgreSQL schema.

Quotes, operator reviews and time are fixtures. ManagedWorker, ledger, locking,
matching and daily reports are real. This is NOT natural multi-day observation.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
import platform
from pathlib import Path
import uuid
from zoneinfo import ZoneInfo

import psycopg2
from psycopg2 import sql

from app.ashare.calendar_rules import market_session
from app.ashare.managed_worker import ManagedWorker
from app.ashare.runtime import save_report
from app.ashare.web_store import WebStore

SHANGHAI = ZoneInfo("Asia/Shanghai")
SESSIONS = ("2026-09-28", "2026-09-29", "2026-09-30", "2026-10-08", "2026-10-09", "2026-10-12", "2026-10-13")
NON_SESSIONS = ("2026-10-01", "2026-10-03", "2026-10-10")
SYMBOL = "sz.000001"


def at(day, clock):
    return datetime.fromisoformat(f"{day}T{clock}").replace(tzinfo=SHANGHAI)


class ValidationFailure(AssertionError):
    """An internally named failed check is safe to include in an artifact."""

    def __init__(self, name, checks):
        super().__init__("multiday acceptance failed: " + name)
        self.name, self.checks = name, checks


class Replay:
    """Own a generated schema; never accept an existing user/account/schema."""

    def __init__(self, dsn, evidence_dir):
        self.dsn, self.evidence_dir = dsn, Path(evidence_dir)
        self.schema = "ashare_replay_" + uuid.uuid4().hex
        self.tenant = "synthetic_" + self.schema.removeprefix("ashare_replay_")
        self.connection = None
        self.quote = None
        self.checks, self.evidence = [], set()
        self.restarts = 0
        self.abrupt_restarts = 0

    def __enter__(self):
        self.connection = psycopg2.connect(self.dsn, connect_timeout=10)
        try:
            with self.connection, self.connection.cursor() as cur:
                cur.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
                cur.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema)))
                for name in ("20261117_ashare_paper.sql", "20261117_ashare_web.sql", "20261117_ashare_operations.sql"):
                    migration = Path(__file__).resolve().parents[2] / "migrations" / name
                    cur.execute(migration.read_text())
            self.bind()
            self.store.initialize("100000")
            return self
        except Exception:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_):
        if self.connection is not None:
            self.connection.close()
        with psycopg2.connect(self.dsn, connect_timeout=10) as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(self.schema)))

    def bind(self):
        self.store = WebStore(self.connection, self.tenant)
        self.worker = ManagedWorker(self.connection, self.evidence_dir, fetcher=self.fetch, notifications_enabled=False)

    def fetch(self, symbols, *_args, **_kwargs):
        if symbols != [SYMBOL]:
            raise AssertionError("unexpected replay watchlist")
        return [dict(self.quote)]

    def check(self, name, condition):
        self.checks.append({"name": name, "passed": bool(condition)})
        if not condition:
            raise ValidationFailure(name, list(self.checks))

    def restart(self, *, abrupt=False):
        if abrupt:
            # Terminate only this replay's own database session, not a user worker.
            backend_pid = self.connection.get_backend_pid()
            with psycopg2.connect(self.dsn, connect_timeout=10) as control, control.cursor() as cur:
                cur.execute("SELECT pg_terminate_backend(%s, 5000)", (backend_pid,))
                self.check("terminated_own_session:" + str(self.restarts), cur.fetchone()[0])
            self.abrupt_restarts += 1
        else:
            # Release ownership explicitly for deterministic graceful restarts.
            with self.connection, self.connection.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock_all()")
        self.connection.close()
        self.connection = psycopg2.connect(self.dsn, connect_timeout=10, options="-c search_path=" + self.schema)
        self.bind()
        self.restarts += 1

    def tick(self, now, *, source_time=None):
        source_time = source_time or now
        quote = {"symbol": SYMBOL, "name": "平安银行", "exchange_time": source_time.isoformat(),
                 "received_at": now.isoformat(), "last": "10", "bid": "9.99", "ask": "10",
                 "bid_size": 100, "ask_size": 100, "volume": 100000,
                 "previous_close": "10", "open": "10", "high": "10.1", "low": "9.9",
                 "limit_up": "11", "limit_down": "9", "source": "synthetic_multiday_fixture"}
        raw = json.dumps({"synthetic": True, "quote": quote}, sort_keys=True, ensure_ascii=False).encode()
        digest = hashlib.sha256(raw).hexdigest()
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        (self.evidence_dir / (digest + ".json")).write_bytes(raw)
        self.evidence.add(digest)
        self.quote = {**quote, "quote_id": "synthetic:" + source_time.isoformat(), "evidence_sha256": digest}
        return self.worker.process_account(self.tenant, now=now)

    def approve(self, day):
        # An explicit fixture operator action, never performed by the worker.
        self.store.review({"symbol": SYMBOL, "reviewed": True, "evidence": "synthetic operator review for acceptance only",
                           "trading_day": day, "is_trading_day": True, "suspended": False, "st": False,
                           "limit_up": "11", "limit_down": "9", "reviewed_by": "synthetic_operator"})

    def place(self, key, side, quantity, price, submitted_at):
        return self.store.broker.place_order(key, SYMBOL, side, quantity, price, submitted_at, submitted_at.date())

    def ledger_check(self, label):
        state = self.store.broker.snapshot()
        fills, account = state["fills"], state["account"]
        purchases = sum((r["price"] * r["quantity"] for r in fills if r["side"] == "buy"), Decimal("0"))
        sales = sum((r["price"] * r["quantity"] for r in fills if r["side"] == "sell"), Decimal("0"))
        fees = sum((r["fee"] for r in fills), Decimal("0"))
        expected_qty = sum(r["quantity"] * (1 if r["side"] == "buy" else -1) for r in fills)
        self.check(label + ":cash_conservation", account["cash"] == account["initial_cash"] - purchases + sales - fees)
        self.check(label + ":position_conservation", sum(p["quantity"] for p in state["positions"]) == expected_qty)
        self.check(label + ":reservation_conservation", account["reserved_cash"] == sum((r["reserved_cash"] for r in state["orders"]), Decimal("0")))
        self.check(label + ":nonnegative_available_cash", account["available_cash"] >= 0)
        self.check(label + ":no_unrequested_orders", all(r["client_order_id"].startswith("replay-") for r in state["orders"]))
        return state

    def trading_day(self, day, index):
        before = self.store.state()
        self.check(day + ":calendar_session", market_session(at(day, "10:00:00"))["can_match"])
        self.check(day + ":pre_open", self.tick(at(day, "09:00:00"))["state"] == "pre_open")
        self.check(day + ":review_required", self.tick(at(day, "10:00:00"))["state"] == "awaiting_review")
        self.check(day + ":no_auto_review", self.store.state()["rules"] == before["rules"])
        self.check(day + ":no_unreviewed_fill", len(self.store.state()["fills"]) == len(before["fills"]))
        self.approve(day)
        self.check(day + ":review_enables_matching", self.tick(at(day, "10:00:10"))["state"] == "healthy")
        if index:
            sell = self.place(f"replay-{day}-sell", "sell", 100, "9.99", at(day, "10:01:00"))
            result = self.tick(at(day, "10:01:10"))
            self.check(day + ":prior_session_shares_sellable", len(result["fills"]) == 1 and result["fills"][0]["order_id"] == sell["id"])
        submitted = at(day, "10:02:00")
        key = f"replay-{day}-buy"
        order = self.place(key, "buy", 300, "10", submitted)
        fill_count = len(self.store.state()["fills"])
        self.tick(at(day, "10:02:20"), source_time=at(day, "10:01:59"))
        self.check(day + ":stale_quote_rejected", len(self.store.state()["fills"]) == fill_count)
        self.tick(at(day, "10:02:25"), source_time=at(day, "10:02:26"))
        self.check(day + ":future_quote_rejected", len(self.store.state()["fills"]) == fill_count)
        result = self.tick(at(day, "10:02:30"))
        self.check(day + ":partial_fill_limited_to_displayed_size", len(result["fills"]) == 1 and result["fills"][0]["quantity"] == 100)
        partial = self.ledger_check(day + ":partial")
        self.check(day + ":remaining_reserve", partial["account"]["reserved_cash"] == Decimal("2000.02"))
        self.restart(abrupt=True)
        self.check(day + ":order_retry_idempotent_after_restart", self.place(key, "buy", 300, "10", submitted)["id"] == order["id"])
        self.tick(at(day, "10:02:35"), source_time=at(day, "10:02:30"))
        self.tick(at(day, "10:02:40"), source_time=at(day, "10:02:29"))
        replayed = self.store.state()
        self.check(day + ":duplicate_and_out_of_order_do_not_refill", len(replayed["fills"]) == len(partial["fills"]) and replayed["account"]["cash"] == partial["account"]["cash"])
        self.check(day + ":partial_cancel", self.store.broker.cancel(order["id"])["status"] == "cancelled")
        self.check(day + ":cancel_releases_cash", self.store.state()["account"]["reserved_cash"] == 0)
        rejected = False
        try:
            self.place(f"replay-{day}-forbidden-same-day-sell", "sell", 100, "9.99", at(day, "10:03:00"))
        except ValueError as exc:
            rejected = "T+1" in str(exc)
        self.check(day + ":same_day_sell_rejected", rejected)
        self.check(day + ":lunch", self.tick(at(day, "11:30:00"))["state"] == "lunch")
        self.check(day + ":afternoon_resume", self.tick(at(day, "13:00:10"))["state"] == "healthy")
        pending = self.place(f"replay-{day}-day-expiry", "buy", 100, "9", at(day, "14:56:00"))
        self.tick(at(day, "14:56:10"))
        self.check(day + ":closing_auction_not_matched", self.tick(at(day, "14:57:00"))["state"] == "closing_auction")
        self.tick(at(day, "15:00:00"))
        final = self.ledger_check(day + ":close")
        ended = next(r for r in final["orders"] if r["id"] == pending["id"])
        self.check(day + ":day_order_expires_at_close", ended["status"] == "expired")
        self.check(day + ":close_releases_reservations", final["account"]["reserved_cash"] == 0)
        self.restart()
        self.tick(at(day, "15:00:10"))
        self.ledger_check(day + ":restart_after_close")

    def non_trading_day(self, day):
        before = self.store.state()
        self.check(day + ":non_trading_day", self.tick(at(day, "10:00:00"))["state"] == "non_trading_day")
        after = self.store.state()
        self.check(day + ":no_holiday_fill", before["fills"] == after["fills"])
        self.check(day + ":holiday_preserves_cash", before["account"]["cash"] == after["account"]["cash"])

    def run(self):
        for day in sorted(set(SESSIONS) | set(NON_SESSIONS)):
            if day in SESSIONS:
                self.trading_day(day, SESSIONS.index(day))
            else:
                self.non_trading_day(day)
        final = self.ledger_check("final")
        self.check("final:cash", final["account"]["cash"] == Decimal("98925.87"))
        self.check("final:position", sum(p["quantity"] for p in final["positions"]) == 100)
        self.check("final:fills", len(final["fills"]) == 13)
        self.check("final:human_reviews", sum(a["event"] == "daily_rules_reviewed" for a in final["audit"]) == len(SESSIONS))
        from app.ashare.operations import list_daily_reports
        reports = list_daily_reports(self.connection, self.tenant, "paper", limit=30)
        self.check("daily_reports:unique_sessions", sorted(r["trading_day"] for r in reports) == sorted(SESSIONS))
        self.check("daily_reports:reconciled", all(r["report"]["reconciliation"]["ok"] for r in reports))
        self.check("daily_reports:fill_counts", sum(r["report"]["day_fill_count"] for r in reports) == 13)
        observations = [row["report"]["observation_summary"] for row in reports]
        self.check("observations:controlled_time_only", all(row["controlled_time_polls"] > 0 and row["wall_clock_polls"] == 0 for row in observations))
        self.check("observations:no_continuous_uptime_claim", all(row["coverage"] == "sampled_not_continuous_uptime_proof" for row in observations))
        for fill in final["fills"]:
            raw = (self.evidence_dir / (fill["evidence_sha256"] + ".json")).read_bytes()
            self.check("evidence:" + str(fill["id"]), hashlib.sha256(raw).hexdigest() == fill["evidence_sha256"])
        with self.connection, self.connection.cursor() as cur:
            cur.execute("SHOW server_version")
            postgres_version = cur.fetchone()[0]
        sources = ("multiday_validation.py", "broker.py", "managed_worker.py", "runtime.py", "operations.py", "calendar_rules.py")
        source_hashes = {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() for name in sources}
        return {"status": "passed", "validation_kind": "synthetic_multi_session_replay",
                "naturally_observed_multiple_trading_days": False, "mode": "paper", "ai_auto_promotion": False,
                "generated_at": datetime.now(timezone.utc).isoformat(), "sessions": list(SESSIONS),
                "python_version": platform.python_version(), "postgresql_version": postgres_version,
                "calendar_version": market_session(at(SESSIONS[0], "10:00:00"))["calendar_version"], "source_sha256": source_hashes,
                "non_sessions": list(NON_SESSIONS), "connection_restarts": self.restarts, "abrupt_connection_restarts": self.abrupt_restarts,
                "checks": self.checks, "daily_reports": reports, "account": final["account"],
                "orders": final["orders"], "fills": final["fills"], "positions": final["positions"],
                "synthetic_evidence_files": len(self.evidence), "schema_cleaned_up": True}


def run_validation(dsn, evidence_dir):
    with Replay(dsn, evidence_dir) as replay:
        report = replay.run()
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="Synthetic acceptance JSON report")
    parser.add_argument("--database-env", default="TEST_ASHARE_DATABASE_URL", help="Environment variable holding a dedicated test PostgreSQL DSN")
    args = parser.parse_args(argv)
    dsn = os.environ.get(args.database_env)
    if not dsn:
        parser.error(f"{args.database_env} is required; use a disposable test database")
    evidence_dir = args.output.resolve().parent / ("synthetic-evidence-" + uuid.uuid4().hex)
    try:
        report = run_validation(dsn, evidence_dir)
    except ValidationFailure as exc:
        save_report(args.output, {"status": "failed", "validation_kind": "synthetic_multi_session_replay",
                                  "failed_check": exc.name, "checks": exc.checks})
        print(json.dumps({"status": "failed", "failed_check": exc.name}))
        return 1
    except Exception as exc:
        save_report(args.output, {"status": "failed", "validation_kind": "synthetic_multi_session_replay", "error_type": type(exc).__name__})
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__}))
        return 1
    report["evidence_directory"] = str(evidence_dir)
    save_report(args.output, report)
    print(json.dumps({"status": report["status"], "sessions": len(report["sessions"]), "checks": len(report["checks"]), "report": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
