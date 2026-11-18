# Current theme membership observations and provenance

[简体中文](THEMES_CN.md) · [User guide](USER_GUIDE.md) · [Backlog](backlog.json)

## Scope

Theme data uses Eastmoney public concept-directory and membership endpoints, the same source as AKShare concept-board adapters. These are provider-reported current members, not exchange-certified classifications or a historical membership database. Public accessibility does not establish long-term availability, licensing or an SLA.

Each successful observation stores the board code, exact name, every original response page, request parameters, per-page receipt times, manifest hash and supported securities. Both directory and membership pagination must complete with stable totals and unique identities. Missing pages, count mismatches, name conflicts and invalid fields prevent publication of a complete observation. Multiple HTTP pages are not an atomic provider snapshot: passing these checks cannot rule out same-count membership replacements during collection.

Archived bytes are the HTTP client's decoded response body, not TLS or compressed transport frames. The manifest binds page hashes, requests and receipt times. Queries revalidate the manifest, original pages and all database members before returning a page. Observations are append-only; current members never overwrite old versions.

## Time and research semantics

`published_at` is unknown. `known_at` uses the receipt time after complete collection. A historical knowledge query can only return already archived versions; times before first collection remain unobserved. There is no historical-date input for backfilling today's list and no caller-supplied past receipt timestamp.

Results separate provider total, supported Shanghai/Shenzhen A-share count and exclusions. Unsupported Beijing exchange or other securities are not silently treated as tradable. Membership establishes a provider classification, not current ST/suspension status, price bands, liquidity or trading authorization.

Broad preferences such as new energy may map to several concepts. This version requires an explicit board code or exact provider name (both must match when supplied together); it does not merge solar/new-energy vehicle concepts or alter saved user preferences. A read-only archive panel is available. The operations job below collects three explicit boards; it is not automatic mapping of account preferences. Research recommendations still require separate integration.

## Operation and safety

Collection is a bounded independent task, separate from quote matching. Networking uses fixed HTTPS endpoints, no arbitrary URL, no redirects and no automatic retry. Request count, response size, pages and whole-task duration are bounded. Failure preserves previously published observations without treating them as new data.

PostgreSQL is the durable observation/member store; original files reside in the evidence volume. Existing backups cover `qd_ashare_*` tables and that volume. Monitoring validates recent board observations and reports corruption. One successful fetch does not complete historical theme coverage.

This module holds no model or brokerage keys, writes no paper orders, reviews no daily rules and approves no strategies. Live execution and automatic AI promotion remain prohibited.

## Operational entry points

After migration in the backend environment, ingest one exact provider name:

```bash
python -m app.ashare.themes ingest --name 人工智能 --evidence-dir /app/data/evidence/themes --timeout 60
```

Use the existing `ASHARE_DATABASE_URL` or `DATABASE_URL`; never print or paste connection credentials. In the deployed stack run inside the existing paper container, without exposing a host database port. This does not add an account watchlist item or submit an order.

To inspect a full directory separately, use `python -m app.ashare.themes catalog --evidence-dir /app/data/evidence/themes --timeout 30`. Catalog output is a raw inspection archive, not a durable membership record. Only successful `ingest` records establish stored themes; an extra catalog request is not needed before ingestion.

Use the actual board code from the receipt with `python -m app.ashare.themes snapshot --board-code BOARD_CODE_FROM_RECEIPT --limit 100 --offset 0`. Replace the placeholder with the returned code. An optional timezone-aware ISO `--known-at` queries versions already known at that time; it cannot write or backfill historical membership.
## Nightly collection

On Ubuntu, `scripts/ops/theme_scheduler.py` installs a user systemd timer using the existing private deployment directory:

```bash
python scripts/ops/theme_scheduler.py --state-dir /srv/quantdinger-paper/state install-timer
python scripts/ops/theme_scheduler.py --state-dir /srv/quantdinger-paper/state run
```

The example path must be replaced. Collection configuration `eastmoney-configured-themes-20261118-v1` contains exactly 人工智能 `BK0800`, 机器人概念 `BK1090`, and 新能源 `BK0493`. It identifies provider inputs only, not strategy approval or automatic mapping of account preferences to tradable securities.

The timer runs every day at 20:10 Asia/Shanghai with at most 60 seconds of randomized delay, including non-trading days. It captures current membership returned then, not certified closing membership or next-session security status. Before 20:10 it skips without provider requests. Persistent catch-up obeys the same window and never backfills yesterday's knowledge time.

A verified receipt from the same evening is not collected again; an early-morning manual observation does not satisfy the evening update. Attempts are persisted before execution. Failure, timeout or uncertain outcome permits only verification on subsequent same-window runs, never an automatic fetch retry. The next evening opens a new window. Boards run independently; partial failure returns nonzero while retaining successful receipts. Dedicated job containers use the shared deployment lock and bounded timeouts, without stopping the quote worker, placing orders or calling models.

`theme-sync-state.json` and its initialization marker reside in the private host deployment directory. Only installation initializes them; missing or corrupt run state blocks collection. Do not delete state to retry. Preserve and restore this state during migration/recovery rather than assuming an uncertain request is safe to repeat. Existing database/evidence-volume backups cover theme data but do not automatically include this host scheduling journal; scheduler disaster recovery is not claimed as accepted.

Before launch, the scheduler records the exact container name alongside the attempt. After an interruption, it checks ownership and cleans that container before resuming. Uncertain cleanup pauses the run. The earlier attempt remains in the journal, so that board is eligible for archive verification only in the same window. Full host reboot and disaster recovery still require independent verification.

Monitoring distinguishes systemd failure, partial failure, unreadable state, the first window not yet completed, and completion. Valid old archives do not hide a new collection failure. Installation and pre-window skip tests do not prove the first real nightly collection succeeded.
