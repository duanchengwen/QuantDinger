# Sustained A-share paper operation and observation

[中文](OPERATIONS_CN.md) · [Web workspace](WEB_WORKSPACE.md)

This module controls virtual cash and paper fills only. It never connects to a live broker, approves daily rules or promotes a strategy. HTTP handles human commands; the independent `app.ashare.managed_worker` owns PostgreSQL global/account advisory locks, provider polling, matching and operational records.

## Liveness and readiness

```bash
python -m app.ashare.healthcheck --max-age-seconds 120
```

Exit 0 means the database is reachable and a running process has a recent, non-future heartbeat. Exit 1 covers an unavailable database/schema, absent/stale/future heartbeat or stopped process. JSON contains no DSN/password. Empty account sets still heartbeat; updates occur before each account and after each complete cycle. Graceful exit records `stopped`; abrupt failure is detected by heartbeat expiry.

A healthy process can be closed, at lunch, awaiting human review, rejecting quotes, short of disk space or blocked by a ledger discrepancy. Health is not trading permission and does not trigger restart loops for business states. API/UI freshness, daily rules, account state and market-session gates remain independent.

Database reconnect uses capped 1, 2, 4…30-second backoff. SIGTERM/SIGINT stops after the current bounded external call finishes. Ordinary cycle logs appear only on state change or every 60 seconds; deployment must still rotate logs.

## Day end, reports and reconciliation

After the session closes, remaining DAY orders expire and release reservations. Startup on a later date also releases old orders even if the feed fails. The final three minutes remain an unsupported closing auction with no matching.

Reports use a unique `(tenant_id, account_id, trading_day)` key and a generation audit event. Restart neither duplicates nor overwrites them. Catch-up scans at most 31 calendar dates per cycle and creates reports only for calendar-confirmed sessions, starting at first observation or an earlier actual order. Unknown calendar coverage stops scanning rather than guessing holidays.

`cash_end`, `positions_end`, daily fill notional and fees are reconstructed from durable fills through that date. Current positions never impersonate historical closing positions. `late_generation` identifies catch-up. Without verified closing prices, `equity_end` is null; no equity, return or P&L is invented. Reconciliation explicitly covers the entire ledger at generation time.

Before account processing, bounded SQL aggregate results reconcile cash, shares, per-order fill quantities/notional/fees and reservations. A discrepancy enters `reconciliation_failed` and blocks ingestion and matching. Every subsequent cycle checks the real ledger; recovery requires the discrepancy to be genuinely fixed. The system never silently rewrites balances or deletes audit evidence. Reports, reconciliation and read APIs remain tenant-scoped.

## Natural observation versus controlled replay

Each actual poll attempt updates daily first/last sample timestamps, poll/failure/ready/stale-or-future counts, maximum source timestamp age, and the last batch's source timestamps/raw-evidence hashes. Natural and injected clocks are counted separately as `wall_clock_polls` and `controlled_time_polls`.

Reports include `observation_summary` as of generation. An unobserved catch-up date is `not_observed`; recorded samples are labeled `sampled_not_continuous_uptime_proof`. First/last timestamps and counts do not prove uninterrupted operation, and replayed dates do not substitute for observing multiple natural sessions. Maximum source age is not network latency; normal stale closed-market prices contribute to it.

For natural multi-day acceptance, retain daily reports, rotated service logs, database backups and raw market evidence, and check natural-clock samples, failures/staleness and ledger changes day by day. Intraday observation rows remain mutable; completed reports never absorb later samples.

## Storage and polling

Continuous sessions use the configured interval. Lunch/pre-open polls are at least 60 seconds apart; closed/non-session/unknown-calendar polls are at least 300 seconds apart, while heartbeats continue. Continuous-session resumption immediately returns to the configured interval.

`ASHARE_MIN_FREE_DISK_BYTES` defaults to `104857600` (100 MiB) on the evidence filesystem. Below this threshold, `storage_low` pauses ingestion and matching; subsequent space checks allow recovery. No evidence is automatically deleted. This guard does not replace capacity alerts for the PostgreSQL volume, backups, WAL or logs.

Archive only with verified, restore-tested backups preserving evidence referenced by fills, audit and reports. Deleting old directories while retaining unresolvable hashes is not archival. The single worker processes accounts sequentially; no large-account low-latency SLA or execution beyond verified calendar coverage is claimed.
