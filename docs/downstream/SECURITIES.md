# Historical universes and dated security observations

[简体中文](SECURITIES_CN.md) · [Operations](OPERATIONS.md)

## Implemented scope

`app.ashare.securities` requests BaoStock `query_all_stock(day=...)` for an explicit date, retaining suspended members and filtering indices/unsupported codes. It never reconstructs historical membership from today's surviving stocks. Coverage is the simulator's supported Shanghai/Shenzhen A-share code range, not a certified complete exchange master, Beijing exchange or all delisted securities.

Selected symbols use daily `query_history_k_data_plus` with `adjustflag=3` (unadjusted), retaining `tradestatus`, `isST`, previous close and original source values. Validation rejects missing columns, date/security/adjustment mismatches, duplicates and cross-interface status conflicts. Empty flags remain `null`; an absent bar is `daily_bar_missing`, not inferred suspension. A watched security absent from the dated universe and bar response is `not_in_dated_universe`; the remaining historical universe remains ingestible. Absence is not certified evidence of IPO/delisting status.

These are **end-of-day research observations**, not premarket/intraday trading-status authorization. Same-day ingestion is prohibited before 19:00 Asia/Shanghai; this is a conservative application cutoff, not a provider availability promise. EOD flags cannot be used for earlier intraday decisions or treated as next-day authority. IPO/relisting, corporate actions, special bands and delisting-period status remain unknown and require human checks.

## Source assessment

- [Official BaoStock PyPI release 0.9.4](https://pypi.org/project/baostock/0.9.4/) supplies the maintained SDK and daily field/adjustment example. Installed `baostock.security` methods and `baostock.common.contants` were inspected. SDK 0.9.4 uses `public-api.baostock.com:10030`; 0.8.9 uses the former domain.
- [BaoStock](https://www.baostock.com/) is the provider entry point. During this check the former `mainContent?file=...` documentation routes served the redesigned site shell. Third-party reproductions were not treated as current official service guarantees. An official free SDK is not an exchange-certified security master or SLA.
- [AKShare's official README](https://github.com/akfamily/akshare/blob/main/README.md) documents upstream-dependent interfaces and interface-name migrations. Current-list/daily-price interfaces do not establish reliable historical status. This module does not silently fall back to current membership; a future second source must retain separate provenance and expose conflicts.

Initial host checks timed out after 20 seconds with SDK 0.8.9 and 30 seconds with 0.9.4. Host DNS resolved the new hostname to a transparent-proxy fake-IP; TCP connected but the initial login response did not arrive. Subsequently, the deployed Compose container's normal DNS/network path **successfully retrieved real BaoStock data at 2026-11-17 19:56:38 Asia/Shanghai**: 5,224 supported Shanghai/Shenzhen A-share universe members for 2026-11-17 and one watched security's daily status, with `approvals_created=0`.

Receipt `56e43b9c9b4245f0a96a05f40b10ecaa` hashes the original decoded source JSON as `07a5f3c820cc474130e23b1bf06e31b2f30a53d741faa5f4f7e14bf70e396068`. Local evidence is `work/quantdinger-securities-acceptance/deployed-securities-sync.log`. This verifies the current deployed network path, not repaired host routing, future uptime, exchange-certified master data or premarket authorization. No system proxy, DNS bypass or security setting was changed. Later cross-date backfills are history fetched now, not naturally observed market days. The subsequent three-day scheduled sync **partially succeeded before failing**: a 2026-11-13 receipt was archived at 19:59:53 Asia/Shanghai, again covering 5,224 members and one watched status. Its receipt is `6b5fe62e00fa4301b423115337d14994`, source hash `bd306b1c5bcd68afc1a392bc435682250afc3e9b332be570a58cf62c9fb677dc`. No successful 2026-11-16 receipt exists yet. Later independent retries for November 13 and 16 timed out after 60 seconds; a separate login probe returned `10002007`. Later retry failures do not invalidate the earlier verified archive. Three-date coverage and sustained scheduled availability remain incomplete.

## Read-only comparison of two real dates

The deployed database and source evidence verify 5,224 supported members on both November 13 and 17, with no added/removed codes. Three provider-reported trading flags differ: `sh.605255` active→suspended, `sz.000831` suspended→active, and `sz.002129` suspended→active. These are independently dated observations, not inferred historical flags.

Watched `sz.000001` reports non-ST/not suspended on both dates. Previous close/close are 11.7900/12.0700 on November 13 and 12.3000/12.2500 on November 17. November 17's previous close does not establish missing November 16 membership or status. Local `work/quantdinger-securities-acceptance/cross-date-comparison.json` records the comparison after source-hash and decoded-content consistency checks. Both historical dates were backfilled today; this is not naturally observed multi-day operation.

## Persistence and knowledge time

Three PostgreSQL tables store append-only receipt versions, their universe members and selected daily statuses: `qd_ashare_security_receipts`, `qd_ashare_universe_members`, and `qd_ashare_security_statuses`. Each successful fetch appends a receipt. No previous observation is overwritten.

`known_at` only selects receipts with `received_at <= known_at`. Today's historical backfill therefore cannot appear to have been locally known years ago. No certified provider publication timestamp exists: `source_time=null` and `available_at=received_at`. This enforces local knowledge/version boundaries, not a complete vendor point-in-time publication history.

Evidence is canonical JSON containing the SDK's original decoded fields/rows and request parameters, **not raw network bytes**. `source_hash` / `evidence_sha256` hashes that file. Metadata records date, receipt time, parser/SDK version and representation. Reads check existence/hash, revalidate source semantics and compare returned database details with original evidence. Missing/corrupt/inconsistent evidence fails closed.

## Usage

Apply `20261117_ashare_securities.sql` through the shared migrations. Install pinned `baostock==0.9.4` and retain evidence with backups. The following writes only public market observations, never approvals or orders:

```bash
cd backend_api_python
python -m app.ashare.securities ingest \
  --day 2026-11-16 \
  --symbol sz.000001 \
  --symbol sh.600000 \
  --evidence-dir /path/to/private/evidence/securities \
  --timeout 30
```

Read the DSN from `ASHARE_DATABASE_URL` or `DATABASE_URL`; never commit or print credentials. Repeat `--symbol` zero to thirty times. Membership covers all supported symbols returned for the date; detailed EOD flags cover only selected symbols. Zero symbols imports membership only. The SDK runs in a bounded disposable child process, avoiding its global socket state in the real quote worker.

```python
store = SecuritiesStore(connection)
store.universe("2026-11-16", known_at=aware_datetime, limit=500, offset=0)
store.status("sz.000001", "2026-11-16", known_at=aware_datetime)
```

Universe responses contain `members`, `total`, pagination and provenance; absent receipts return `status=not_observed`. Status returns `None` without an exact-date receipt and never substitutes prior-day observations. All observations carry `reviewed=false`, `auto_approval_eligible=false`; review helpers may prepare human review, never grant permission.

## Validation

The 24 tests in `tests/ashare/test_securities.py` cover inconsistent dates/security/adjustments, missing flags/bars, preserved suspensions, excluded indices, watched symbols absent from historical pools, subprocess timeouts, knowledge-time isolation, append-only revisions, missing/corrupt evidence and database-detail tampering. Integration tests use real PostgreSQL with isolated random schemas and explicitly synthetic provider fixtures. The actual provider success above is assessed separately from unit/integration test counts.
