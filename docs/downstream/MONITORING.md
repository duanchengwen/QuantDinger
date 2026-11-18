# Read-only monitoring of the A-share paper deployment

[简体中文](MONITORING_CN.md) · [User guide](USER_GUIDE.md) · [Operations](OPERATIONS.md)

Run from the repository root, substituting real deployment paths:

```bash
python scripts/ops/paper_monitor.py \
  --state-dir /srv/quantdinger-paper/state \
  --backup-root /srv/quantdinger-paper/backups \
  --backlog docs/downstream/backlog.json \
  --output /srv/quantdinger-paper/monitor/latest.json \
  --quiet-unchanged /srv/quantdinger-paper/monitor/baseline.json
```

The monitor submits no orders, approves no rules, sends no notifications and restarts no services. It executes a bounded PostgreSQL **read-only, repeatable-read transaction** in the existing API container. No extra DB port or secret-bearing command arguments are needed. Reports omit tenant/account names, balances, position quantities and bot URLs; hashed account references and operational records still belong in private storage. Output and quiet baselines are mode 0600 and cannot be written inside the Git checkout.

## Checks

- Four running/healthy containers and a worker heartbeat no older than 120 seconds.
- Cash, shares, per-order fill notional/fees and reservations reconciled against all fills in the same read-only snapshot, independent of UI history limits.
- During continuous trading, quote source/receipt times no older than 15 seconds, no future timestamps, and evidence hashes present. Old closed-session quotes are retained but do not become intraday failure alerts.
- Current quote raw files are actually read and SHA256-verified, including off hours. Paths outside `/app/data` and files above 16 MiB are rejected. If installed, the latest ten security-reference receipts also show date/universe/status counts, verified raw files and nonfuture receipt times. Knowledge time is receipt time, not a claimed original publication time. Before the reference migration, monitoring continues with a pending item.
- Data-volume free space below 100 MiB and backup-filesystem free space below 500 MiB generate issues. These thresholds cannot predict the size of the next backup.
- Live, Agent-live and AI auto-promotion environment flags remain false. This checks configuration and does not replace source safety tests.
- The newest timestamped backup manifest passes all file checksums and archive path checks and is no older than 30 hours. Corruption of the newest backup does not silently fall back to an older one. This does not perform restore or deletion.
- The latest 90 recorded dates per account, separating wall-clock from controlled-time polls and daily reports. Only calendar-confirmed trading dates with wall-clock samples count. `natural_sample_days` is a union across accounts, not evidence that every account covered all those dates.

Exit 0 means no current operational issues, 1 means issues exist, and 2 means the monitor itself failed. Failures print only an exception type, excluding raw Docker stderr or connection secrets. A healthy exit does not complete pending acceptance.

`--quiet-unchanged` prints on significant changes to health, issues, pending acceptance, sampled dates or report count. Advancing clocks, quotes and counters remain quiet. A quiet failing run still has a failing exit code. `--output` updates the full JSON on every run.

`--backlog` reads an explicitly maintained `{items:[{id,status,acceptance}]}` manifest, exposing its states, evidence references and pending IDs. `accepted`/`complete` are explicit acceptance assertions, `prohibited` is excluded scope, and other states remain pending. The monitor never modifies or approves this manifest from daemon health alone.

## Natural multi-day evidence

The default sample-date target is five natural trading days; `--target-days` changes only that progress threshold. A partial first day or one sample per day does not prove a complete session or uninterrupted service. Existing daily aggregates cannot reconstruct every polling gap. The monitor retains `intraday_gap_and_full_session_coverage_not_proven` and `natural_multiday_complete=false`; session logs and detailed observations require separate review. Controlled replay never counts as natural days.

Daily rules remain human-approved. Corporate actions, point-in-time datasets, industry news/recommendations, strategy research, Windows and CI work require separate acceptance. Future strategy changes require human approval. `completed_backlog=false` prevents a healthy daemon from silently marking all work complete.

A recurring follow-up in this chat can invoke the command and remain quiet unless there is a new actionable fault, recovery, acceptance milestone or required user action. Host shutdown/sleep or an inactive scheduler prevents observation; this is not a zero-interruption guarantee. The script creates no system timer itself.

When theme snapshots are deployed, monitoring validates the latest observation for up to ten boards: manifest, every original page and database membership are rechecked, and knowledge time must not be in the future. Valid membership evidence neither completes historical coverage nor authorizes trading.

After installing nightly theme collection, the monitor reads `quantdinger-themes-<project>.service` results and exposes only validated status, configuration version, window date and timestamps from the private journal. Systemd success alone does not prove collection: pre-window skips or missing first execution retain `theme_sync_first_window_not_completed`. Partial failure, corrupt state, future timestamps and completion older than 30 hours are reported. The journal does not replace database/raw theme evidence checks, and monitoring never triggers collection or retries.

Database evidence is timed after the read-only snapshot has been established and heartbeats have been read. Host job summaries use a separate clock after collection so a concurrent update does not look future-dated. A queued acknowledgement from the scheduled request confirms that it enqueued work. The database run record supplies research progress.
