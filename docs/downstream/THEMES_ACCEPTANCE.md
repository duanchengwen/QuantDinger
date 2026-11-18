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

## November 18 follow-up: real robotics and new-energy archives

The existing AI receipt's 554-entry directory was hash-verified before selecting two exact board names and codes. Each board was collected once with a bounded deadline and no retry. The directory separately contains new energy, new-energy vehicles and several robotics concepts; this run does not merge them or rewrite user preferences.

| Board | Actual receipt time (Asia/Shanghai) | Provider members | Supported members | Explicit exclusions |
| --- | --- | --- | --- | --- |
| 机器人概念 BK1090 | 2026-11-18 01:27:56 | 840 | 796 | 44 |
| 新能源 BK0493 | 2026-11-18 01:27:58 | 220 | 215 | 5 |

- Robotics receipt `f2d2c62437e4485a907d618e0011215a`, manifest SHA-256 `fe39d905a86fedbd00817bfc35ff5d669f11e30ee970990e6f795959110ab0bd`.
- New-energy receipt `59e35f4e53a4486781afc7dfb46c69cd`, manifest SHA-256 `8b0f0de607577b8e36fd2336fa58e256b199f6378b8b3515eac2c932c5975e9b`.

Deployed HTTP acceptance passed **52 checks** covering the three-board list, all new member pages, uniqueness, raw evidence, timestamps, provenance, exclusions and absence before collection. Seven account/order/fill/lot/audit/session table fingerprints and the model-settings hash were unchanged; saved topic preferences retained their existing values. The first acceptance harness incorrectly required the entire `qd_ashare_web` table to stay unchanged while the running worker updates quotes and heartbeat fields. That volatile-table assertion was replaced with an explicit preference check; no application change or collection retry occurred.

The theme regression passed **88 tests on each of Python 3.11 and 3.12**. Independent review checked exact matching and time semantics; **9 frontend theme-state tests passed**. Actual browser checks confirmed three options, robotics pages 1–100 and 101–200, and new-energy selection resetting to 1–100 with matching counts, time and hash. Existing deployed code/assets were reused without rebuilding. Evidence resides under the workspace parent's `work/quantdinger-theme-followup-acceptance/`: receipts, HTTP proof, JUnit, screenshot and backup log.

A new private backup at `runtime/quantdinger-paper-backups/three-themes-20261118/` covers 18 tables and all three theme receipts/raw files. Backup verification passed and all four resumed services are healthy. No new isolated restore drill ran in this follow-up; the earlier drill is not claimed as validation of this new backup's restoration.

These are current observations received on November 18, never November 17 historical knowledge. Historical coverage, versioned preference mapping, scheduled collection, news and automatic research remain unfinished. Midnight sampling is not a second complete trading session. The November 16 BaoStock gap and synchronization failure remain open. No orders, strategy approvals, model requests or extra Feishu messages occurred; live trading and AI promotion remain disabled.

## November 18 follow-up: evening job installation and pre-window validation

A host scheduler now reuses the deployed theme collector without rebuilding images, migrating the database or restarting API/quote services. Collection configuration `eastmoney-configured-themes-20261118-v1` contains the three exact boards above, not a strategy approval. Separate agents implemented and independently reviewed the scheduler; the root agent integrated monitoring, tests, installation and acceptance.

The timer starts daily at 20:10 Asia/Shanghai with at most 60 seconds of randomized delay. Job containers have a 45-second deadline and at most 10 seconds for cleanup; the total budget is 240 seconds, with systemd startup/stop limits of 270/30 seconds. Intent is persisted before each board attempt. Same-window failure or uncertainty permits verification only, without provider retry. Existing success also requires raw-evidence verification before skipping. Uncertain cleanup prevents subsequent board jobs. Missing, corrupt or mismatched journal/marker scope blocks execution. See [theme operations](THEMES.md).

| Check | Actual result |
| --- | --- |
| Python 3.11 complete operations suite | 73 passed |
| Python 3.12 same suite | 73 passed |
| New scheduler tests, included above | 24, including offline execution of the actual container program with replaced boundaries |
| Independent offline adversarial checks | 20 passed |
| Real installation/systemd/existing worker image/data-preservation checks | 22 passed |
| Ruff, documentation and diff checks | Passed |

Review found and fixed two problems: initialization status disagreed with the monitor contract, and generic nonzero container-removal results were too broadly treated as absence. Absence now needs a separate bounded confirmation, otherwise the outcome remains unknown. Monitoring also reuses marker/project/attempt validation rather than treating lost state as completion.

After installation, `quantdinger-themes-quantdinger-paper.timer` reported its next trigger as **2026-11-18 20:10:42 CST**. An actual systemd invocation and one isolated worker probe explicitly forbidden from ingesting both respected the pre-window guard: zero provider calls, probe container removed. Nine user/session/ledger/trade/theme table fingerprints, private deployment configuration and model-setting hashes were unchanged; scheduling files are mode 0600. No orders, approvals, model calls or Feishu messages occurred.

**The first natural evening collection has not been accepted.** Service success means the pre-window skip worked; journal status remains `initialized` and monitoring retains `theme_sync_first_window_not_completed`. Four persistent services are healthy, existing evidence and backup checks pass, and the known BaoStock failure still keeps overall monitoring at attention. No additional claims were made for complete natural sessions, Windows, remote CI or actual Feishu delivery.

Evidence is in the workspace parent's `work/quantdinger-theme-scheduler-acceptance/`: both JUnit reports, independent review/source hashes, 22-check installation proof, systemd logs and final monitoring. A restricted copy of the initial private scheduling journal was retained, but host reboot, SIGKILL and recovery after lost scheduling state were not exercised. Existing database/evidence-volume backups do not automatically include the host journal; recovery remains pending.

## November 18 interruption rehearsal

The scheduler now saves the exact container name with its attempt before launch. On restart it validates ownership and cleans up the recorded container before proceeding. Uncertain cleanup pauses the run and a previously attempted board remains eligible for archive checks only.

The rehearsal sent SIGKILL to an isolated host scheduler process and confirmed its network-disabled Docker container survived. Restart cleaned up the exact container and limited the original board to verification. A missing journal failed closed. Real provider requests totaled zero, and the live journal hashes matched. Python 3.11 and 3.12 each passed 150 operations tests. Local proof is `theme-scheduler-kill-rehearsal-20261118.json`. The first natural evening capture and full host disaster recovery remain pending.


## November 18 first natural evening collection

The local timer started at 20:11 Asia/Shanghai before the cloud cutover. The AI and robotics boards succeeded. The new-energy board failed, leaving the run `partial`. Its earlier receipt remains available. No provider retry was made in this window.

| Board | Received (Asia/Shanghai) | Verified members | Receipt | Manifest SHA-256 |
| --- | --- | --- | --- | --- |
| Artificial intelligence BK0800 | 20:11:12 | 731 | `ef92e3433070486293f2b16c5959f11a` | `8b096d2013780c23943db994001013f14ef9a945ae05a62870f4ef9fbeb5fc74` |
| Robotics BK1090 | 20:11:16 | 796 | `d20d27c144484497ba4bc390768694fd` | `e8494d7e1a398d2e884928ca2661450d02759d414d830e6943b679404d22fa05` |

The scheduler journal records the real requests, verified archive results and receipt times. The original journal was saved outside the repository. Complete recurring coverage across all three boards remains open.
