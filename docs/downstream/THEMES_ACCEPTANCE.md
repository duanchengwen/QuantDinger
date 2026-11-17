# Theme observation acceptance — 2026-11-17

[简体中文](THEMES_ACCEPTANCE_CN.md) · [Operations](THEMES.md) · [Backlog](backlog.json)

## Actual outcome

A bounded independent theme collector and append-only PostgreSQL archive are deployed locally. No trading, strategy approval or model calls were connected. Saved research preferences remain unchanged; UI recommendations, preference mapping and periodic theme collection are not enabled.

Exactly one real collection job ran, without automatic retries. At **22:08:51 Asia/Shanghai**, Eastmoney **人工智能 / BK0800** yielded 758 provider members: 730 match the current supported Shanghai/Shenzhen A-share code scope and 28 are explicitly excluded. Code scope does not authorize trading; daily rules, risk controls and human-approved strategy versions remain necessary.

- Receipt: `62b9544bb5ff4741b402db7d1b20d24b`
- Manifest SHA-256: `7e0b0d9e95fbc4b198b67713875e5ccbf64f5615b9e2843923609b61057253e5`
- Sixteen original responses with parameters and per-page receipt times are archived, including directory/member pagination and first-page rechecks.
- Reads revalidate raw files, manifest and all database members before pagination. All 730 supported members were verified.
- Queries before first receipt return `not_observed`. Publication time remains unknown; receipt time is not a provider publication claim, and current membership is never backfilled.

HTTP pagination is not atomic. Stable totals, unique identities and first-page rechecks cannot exclude same-count membership replacements during collection. One success does not prove long-term availability or complete historical membership. Robotics, new energy and other themes/mapping versions remain unaccepted.

## Validation

| Check | Result |
| --- | --- |
| Python 3.11 full A-share suite with isolated PostgreSQL | 277 passed |
| Python 3.12 same suite | 277 passed |
| New theme tests, included above | 58 per version, covering pagination/time/hash/completeness/transactions |
| Independent offline adversarial review | 21 passed, no blocking finding; not represented as real data |
| Deployment/monitor/backup tooling | 39 passed |
| Ruff, backend structure and docs | Passed |
| Deployed source identity | Theme module, migration and schema hashes match |
| Account and model settings preservation | Seven account/order/fill/lot/audit/user-session table fingerprints and model-settings file hash unchanged |

Four existing pandas/calendar deprecation warnings remain. Windows, remote CI and future natural trading days were not executed. Offline review, database testing and the actual provider request have separate evidence.

## Runtime and remaining work

Monitoring now validates theme observations; the real receipt is verified. The known BaoStock synchronization failure and missing November16 data remain; overall monitoring is still attention. Theme success does not make every subsystem healthy. No live order, automatic AI promotion or extra Feishu test message occurred.

Natural observation still covers only part of November17; five natural trading days are not complete. The full news/recommendation/human-approved-strategy/automatic-paper workflow remains tracked in the backlog.

Local evidence is under the workspace parent's `work/quantdinger-theme-acceptance/`, including JUnit, independent review, actual receipt, deployed proof, monitoring and backup logs. Raw theme files are on the persistent `/app/data/evidence/themes/` volume. Credentials, runtime data and private backups are not committed.

A new backup containing theme data was restored to a fully isolated database: all 18 table fingerprints matched, with evidence/settings archive checksums validated. The first isolated restore failed because the readiness probe accepted PostgreSQL’s temporary Unix-socket bootstrap server before its restart. The helper now waits for local TCP readiness; the regression first failed then passed, followed by a successful real restore. Failure evidence is retained; the production database was never overwritten.
