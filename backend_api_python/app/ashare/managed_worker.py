"""Independent durable paper worker for authenticated web-managed accounts."""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
import signal
import threading

import psycopg2

from app.ashare.calendar_rules import market_session
from app.ashare.market import fetch_quotes
from app.ashare.notifications import notify_text
from app.ashare.runtime import claim_worker, process_quotes, utc_now
from app.ashare.schema import get_database_url
from app.ashare.web_store import WebStore


class ManagedWorker:
    """Only this process polls providers or matches web-account orders.

    Feed, clock and notifier are external boundaries injectable for controlled
    acceptance. Database state, account locking and matching remain real.
    """

    def __init__(self, connection, evidence_dir: Path, *, fetcher=fetch_quotes,
                 clock=utc_now, notifier=notify_text, notifications_enabled=True):
        self.connection = connection
        self.evidence_dir = Path(evidence_dir)
        self.fetcher, self.clock, self.notifier = fetcher, clock, notifier
        self.notifications_enabled = notifications_enabled
        self.claimed = set()
        self.polled_at = {}

    def claim(self):
        claim_worker(self.connection, {"tenant_id": "__managed_worker__", "account_id": "all"})

    def process_account(self, tenant_id: str, account_id="paper", *, now: datetime | None = None) -> dict:
        store = WebStore(self.connection, tenant_id, account_id)
        key = (tenant_id, account_id)
        if key not in self.claimed:
            claim_worker(self.connection, {"tenant_id": tenant_id, "account_id": account_id})
            self.claimed.add(key)
        before = store.state()
        observed = now or self.clock()
        session = market_session(observed)
        expired = store.broker.expire_day_orders(date.fromisoformat(session["trading_day"]))
        settings = before["settings"]
        symbols = set(settings["symbols"])
        symbols.update(position["symbol"] for position in before["positions"])
        symbols.update(order["symbol"] for order in before["orders"] if order["status"] in ("open", "partial"))
        symbols = sorted(symbols)
        worker = {"observed_at": observed.isoformat(), "mode": "paper", "session": session,
                  "state": session["state"], "fills": [], "expired_day_orders": [order["id"] for order in expired]}
        quotes = before["quotes"]
        last_poll = self.polled_at.get(key)
        interval = min(int(settings.get("interval_seconds", 5)), 3600)
        should_poll = last_poll is None or (observed - last_poll).total_seconds() >= interval
        if should_poll:
            try:
                quotes = []
                for offset in range(0, len(symbols), 30):
                    quotes.extend(self.fetcher(symbols[offset:offset + 30], self.evidence_dir / tenant_id / account_id, timeout=15))
                observed = now or self.clock()
                session = market_session(observed)
                worker.update(observed_at=observed.isoformat(), session=session, state=session["state"])
                self.polled_at[key] = observed
                if session["can_match"]:
                    # Human reviews can change while the provider request is in flight.
                    latest_rules = store.state()["rules"]
                    config = {"symbols": symbols, "rules": latest_rules, "strategy": {"enabled": False}}
                    config["config_sha256"] = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
                    result = process_quotes(store.broker, config, quotes, observed)
                    worker.update(state=result["state"], fills=result["fills"], rejections=result["rejections"])
                    if any(latest_rules.get(symbol, {}).get("trading_day") != session["trading_day"]
                           or latest_rules.get(symbol, {}).get("reviewed") is not True for symbol in symbols):
                        worker["state"] = "awaiting_review"
            except psycopg2.Error:
                raise
            except Exception as exc:
                worker.update(state="failed", error_type=type(exc).__name__)
                quotes = before["quotes"]
                self.polled_at[key] = observed
        elif session["can_match"]:
            worker["state"] = before["worker"].get("state", "awaiting_review")
            worker["rejections"] = before["worker"].get("rejections", [])

        # Persist health before best-effort notification; it is never a trade command.
        store.observe(quotes, worker)
        changed = worker["state"] != before["worker"].get("state")
        if self.notifications_enabled and (worker["fills"] or changed):
            try:
                credentials = store.notification_credentials()
                notification = self.notifier(
                    f"[模拟交易] {account_id} 状态={worker['state']} "
                    f"本轮虚拟成交={len(worker['fills'])} 时间={worker['observed_at']}",
                    webhook_url=credentials.get("webhook_url", ""),
                    signing_secret=credentials.get("signing_secret", ""),
                )
            except Exception:
                notification = {"status": "failed", "error": "notification_unavailable"}
            worker["notification"] = notification
            store.observe(quotes, worker)
        return worker

    def cycle(self, *, now: datetime | None = None) -> list[dict]:
        results = []
        for account in WebStore.accounts(self.connection):
            try:
                result = self.process_account(account["tenant_id"], account["account_id"], now=now)
            except psycopg2.Error:
                raise
            except Exception as exc:
                observed = now or self.clock()
                result = {"state": "failed", "observed_at": observed.isoformat(),
                          "mode": "paper", "error_type": type(exc).__name__, "fills": []}
                store = WebStore(self.connection, account["tenant_id"], account["account_id"])
                store.observe(store.state()["quotes"], result)
            results.append({"tenant_id": account["tenant_id"], "account_id": account["account_id"], **result})
        return results


def run_forever(dsn: str, evidence_dir: Path, *, cycles=0, tick_seconds=1,
                stop_event=None, connect=psycopg2.connect) -> int:
    """Reconnect with capped backoff; never log the DSN or transport secrets."""
    if type(cycles) is not int or cycles < 0 or not 0.1 <= tick_seconds <= 30:
        raise ValueError("invalid worker loop bounds")
    stop = stop_event or threading.Event()
    done, backoff = 0, 1
    while not stop.is_set() and (cycles == 0 or done < cycles):
        try:
            with closing(connect(dsn, connect_timeout=10)) as connection:
                worker = ManagedWorker(connection, evidence_dir)
                worker.claim()
                while not stop.is_set() and (cycles == 0 or done < cycles):
                    results = worker.cycle()
                    print(json.dumps({"event": "paper_cycle", "accounts": len(results), "cycle": done + 1,
                                      "states": [item["state"] for item in results]}, ensure_ascii=False), flush=True)
                    done += 1
                    backoff = 1
                    if cycles == 0 or done < cycles:
                        stop.wait(tick_seconds)
        except psycopg2.Error as exc:
            print(json.dumps({"event": "database_reconnect", "error_type": type(exc).__name__,
                              "retry_seconds": backoff}), flush=True)
            stop.wait(backoff)
            backoff = min(backoff * 2, 30)
        except ValueError as exc:
            # An existing owner is a configuration/ownership error, not a feed retry.
            print(json.dumps({"event": "worker_rejected", "reason": str(exc)[:150]}), flush=True)
            return 2
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Managed A-share paper-only quote worker")
    parser.add_argument("--cycles", type=int, default=0, help="0 runs until stopped")
    parser.add_argument("--tick-seconds", type=float, default=1)
    parser.add_argument("--evidence-dir", type=Path, default=Path("reports/ashare-managed/evidence"))
    args = parser.parse_args(argv)
    try:
        dsn = get_database_url()
    except ValueError:
        parser.error("ASHARE_DATABASE_URL or DATABASE_URL required")
    stop = threading.Event()
    for name in ("SIGINT", "SIGTERM"):
        signal.signal(getattr(signal, name), lambda *_: stop.set())
    return run_forever(dsn, args.evidence_dir, cycles=args.cycles,
                       tick_seconds=args.tick_seconds, stop_event=stop)


if __name__ == "__main__":
    raise SystemExit(main())
