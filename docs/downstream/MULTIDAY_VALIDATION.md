# Multi-session paper acceptance

[中文](MULTIDAY_VALIDATION_CN.md)

## Evidence classification

This command uses **synthetic quotes, a controlled clock and explicitly labeled test-operator reviews** against real PostgreSQL, ManagedWorker, PaperBroker, fees and daily reporting. It verifies session transitions. It is not evidence of naturally observing several real market days, strategy profitability or provider reliability.

It reads no brokerage credentials, contacts no real broker, calls no model and sends no Feishu messages. A random isolated schema and test account are created; success and failure clean up only that generated schema. Existing account/schema names are not accepted. Use a dedicated disposable test database with create/drop-schema privileges and permission to terminate its own PostgreSQL sessions. PostgreSQL 18 is recommended.

## Run

Install the existing backend test dependencies and configure a dedicated test connection. Do not commit actual passwords:

```bash
cd backend_api_python
export TEST_ASHARE_DATABASE_URL='postgresql://test_user:TEST_PASSWORD@127.0.0.1:5432/test_database'
python -m app.ashare.multiday_validation --output reports/acceptance/multiday.json
python -m pytest tests/ashare/test_multiday_validation.py -q
```

The default reads only `TEST_ASHARE_DATABASE_URL`; there is no fallback to the application's database. `--database-env` explicitly selects another environment variable. Exit codes are 0 for success, 1 for failure, and 2 for missing arguments/configuration. The JSON includes checks, daily reports, orders, fills, final cash/positions, Python/PostgreSQL/calendar versions and source SHA-256 hashes. A neighboring directory preserves clearly labeled synthetic input JSON; each fill's evidence hash can be checked against the original file.

On success the temporary database schema is gone, while the JSON and synthetic evidence remain. A failed report contains the failed check or sanitized exception type. DSNs are never included in reports or logs.

## Coverage

Consecutive sessions are 2026-09-28, 09-29, 09-30, 10-08, 10-09, 10-12 and 10-13. The sequence includes checks on the October 1 holiday and October 3/10 weekends. Dates are revalidated using the pinned XSHG calendar.

Each trading session verifies:

- No pre-open matching; prior reviews expire, and matching requires an explicit new fixture-operator review.
- Prior-session shares are sellable while newly bought same-day shares remain subject to T+1.
- A 300-share buy fills only 100 shares against a displayed 100-share ask; the remainder stays reserved and cancellation releases it.
- Stale/future quotes cannot fill, and duplicate/out-of-order quotes cannot consume displayed liquidity again.
- Forced termination of **this test's own database session**, reconnect and replay preserve order and quote idempotency.
- Lunch pause, afternoon resume, exclusion of closing auction and DAY expiry at 15:00 with reserve release.
- Repeated closed cycles and reconnection do not duplicate daily reports; report cash and positions reconcile.
- Cash, shares and reserves remain conserved, available cash stays nonnegative and no extra strategy orders appear.

## Current result

On 2026-11-17, PostgreSQL 18 completed 7 sessions, 294 scenario checks, 14 connection rebuilds (7 forced termination of test sessions), 13 virtual fills and 7 unique daily reports. Starting cash was CNY 100,000; ending cash CNY 98,925.87, 100 shares remained and reserved cash was zero. These amounts are deterministic fixture checks, not a performance evaluation.

Python 3.11 and 3.12 passed. Upstream calendar/pandas deprecation warnings remain. Additional tests verify connection-secret redaction in failed reports and isolated-schema cleanup on failure.

## Natural observation still required

Multi-day live-provider availability, throttling, network outages, actual close/next-day startup, daily operator review and real Feishu delivery require records from deployment over natural time. This replay does not replace them and must never be labeled “seven days of real-market continuous operation.” Store and report natural observations separately.
