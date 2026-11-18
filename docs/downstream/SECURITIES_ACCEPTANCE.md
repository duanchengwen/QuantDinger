# Dated securities, review assistance and configuration acceptance — 2026-11-17

[中文](SECURITIES_ACCEPTANCE_CN.md) · [User guide](USER_GUIDE.md) · [Source assessment](SECURITIES.md) · [Actual backlog](backlog.json)

## Delivered and real-data limits

Three parallel agents implemented and independently reviewed this increment. The root agent tested, built and deployed the combined result at `http://127.0.0.1:5177/#/ashare-paper`. Live broker access and automatic AI strategy promotion remain prohibited. Password/session versions, virtual cash, positions, orders and fills match pre-upgrade fingerprints.

Actual BaoStock receipts exist for **2026-11-13 and 2026-11-17**, each containing **5,224 supported Shanghai/Shenzhen symbols** and one selected security's unadjusted daily status. Retrieval times were 19:59:53 and 19:56:38 Shanghai time respectively. November 16 remains missing after later SDK login timeouts. The latest scheduled job failed; successful partial receipts are retained. This is not acceptance of stable provider availability or complete historical coverage.

The symbol sets match, while three provider trading-status observations differ (sh.605255, sz.000831, sz.002129); the watched stock also has different dated closing prices. Every page and selected status was checked against the corresponding original receipt. This establishes distinct observed dated responses, not exchange certification.

November 17 source SHA-256: `07a5f3c820cc474130e23b1bf06e31b2f30a53d741faa5f4f7e14bf70e396068`. November 13: `bd306b1c5bcd68afc1a392bc435682250afc3e9b332be570a58cf62c9fb677dc`. Evidence is canonical original SDK decoded fields/rows and request parameters, not raw transport bytes. Query paths check identity, dates, hashes and normalized content against database details. No current constituents fill missing dates. Conservative knowledge time is retrieval time, not a certified historic publication timestamp. EOD observations do not authorize earlier intraday or next-session trading. IPO/relisting/corporate-action certification remains unavailable.

## Features and fixes

- Append-only dated receipts, universe membership and selected daily status tables, shared schema migration and evidence backup.
- Historical-pool UI with source, date, retrieval time and hash, explicit missing-date warning and bounded pagination.
- Same-day evidence suggestions and all-or-none, explicitly human-confirmed batch reviews. Unknown fields stay unknown; negative status, names and corrupt evidence block approval. No automatic carry-forward.
- Account-scoped theme preferences; the user's AI, robotics, new-energy and current-events preferences were saved. This is preference persistence, not an enabled autonomous research/execution workflow.
- Top-level Feishu shortcut and richer newly generated daily-report summaries: actual ledger cash/trades/fees/positions/reconciliation. Missing valuation/PnL/return, recommendations or strategy updates are labelled unavailable. Bounded messages disclose truncation, old reports are not resent and uncertain delivery is not retried. No extra real test message was sent.
- Settings-save fix: mutable configuration now resides on a private persistent volume at `/app/settings/runtime.env`, directory0700/file0600, non-root UID10001, atomic replacement and failure preservation. Backups include it and accept legacy bundles. It is not encryption at rest.
- Official A-share picker visibility enabled only in this isolated deployment. Browser search for 平安银行 returns000001. CNStock metadata is normalized to equity while preserving identities and crypto product semantics.
- Official Vue `_t` cache parameter is accepted by the strict read endpoints; other unknown fields remain rejected. This fixed actual browser errors in pool/report/review queries.

## Verification

| Check | Result |
| --- | --- |
| Python3.11, real PostgreSQL, focused backend suite | 289 passed,1 explicitly deselected |
| Python3.12, same suite | 289 passed,1 explicitly deselected |
| Deployment/backup/sync/monitor tests | 36 passed |
| Frontend state tests | 10 passed |
| Official Vue production build and ESLint | Passed, upstream chunk-size warning remains |
| Ruff, backend structural guard and docs | Passed |
| Actual HTTP/security/configuration/data checks | 18 passed;19 paper-core file hashes match deployment |
| Full isolated restore with new settings archive | 16 table fingerprints,3886 evidence files and1 settings file match; correct permissions/non-root ownership |
| Browser | A-share search, real pool and saved topic preferences verified; no approval or trade submitted |

Excluded upstream retired case: `retired_parameter_tuning_is_not_exposed_or_billable`; four existing calendar/pandas deprecation warnings. This is not the full upstream suite, remote CI or a Windows execution. The expanded Python3.12 settings suite first failed because the test environment lacked Redis/bs4 dependencies; after environment repair, the complete selected suite passed.

The production settings-save probe used only `JEV_ENABLED=false`; no real model key was submitted, logged or sent to an inference service. A fresh process verified persisted settings. The user's earlier failed model-key save did not persist and requires re-entry. Restore testing started only an isolated database, never its API or worker; only that created drill project and copied private state were cleaned afterward.

## Operations and unfinished work

Four production services are healthy, ledger/quote evidence and backup checks pass. Overall monitoring is nevertheless **attention** because the reference-data job failed. Its bounded per-date attempts now run newest-first, preserve successes and report failures; no stale/current-list substitution hides a failure.

The installed local reference-data timer runs at19:10 Asia/Shanghai with up to60s jitter, requesting the latest three completed sessions and at most30 watched/exposed securities' daily detail rows. Each SDK child has a deadline; the outer job is capped at300s and shares the deployment/backup operation lock.19:00 is an application cutoff, not a vendor availability promise.

A30-minute follow-up was created in this chat to inspect actual backlog/monitoring evidence and continue authorized fixes/tests/pushes, quiet when unchanged. Local work requires the host and desktop application running and the project accessible; this cannot guarantee third-party uptime or completion of every backlog item.

Only November17 has natural wall-clock simulation observations, starting partway through the day. Historical download dates do not count as natural simulation days. Five-date/full-session gap acceptance remains open.

The complete desired loop—sector news, AI stock recommendations, approved-strategy automatic paper execution, optimization and human activation of new strategy versions—is **not yet connected** to this ledger. Reliable news timestamps, dated theme memberships, recommendation evidence, approval/versioning and strategy execution/research budgets remain explicit in the backlog. DeepSeek configuration is user-provided; Jev is optional and Laya is researched but not deployed. Webhooks are one-way, not interactive approvals. Missing valuation is never replaced with invented returns.

Local evidence resides outside Git under `work/quantdinger-securities-acceptance/`; private backup under `runtime/quantdinger-paper-backups/securities-settings-20261117/`. No credentials, private settings or backups are committed.

## Follow-up after 21:00: news timestamp correction

Read-only monitoring still finds four healthy services, reconciled ledger and valid raw evidence. The known reference-sync failure remains; no repeated provider retry was made. The official collector filled absent search publication dates with today, and report evidence replaced absent dates with collection time. Both search paths now preserve unknown publication time and expose separate UTC receipt time. Missing, nonpositive, boolean or invalid Finnhub timestamps no longer become 1970 dates. Reports exclude missing/unparseable publication times, counting and flagging `news_publication_time_unknown`. Existing provider dates are preserved, not certified as original publisher dates.

Python 3.11 and 3.12 each pass **136 focused tests**, including all professional_report tests and research_agent tests. Regression cases first reproduced the incorrect dates. No actual model call, order or extra Feishu test was made. Evidence: `news-timestamps-py311.xml` and `news-timestamps-py312.xml` in the existing local acceptance directory. This does not complete scheduled news ingestion, original-document hashes or automatic selection.

A bounded read-only theme audit identified AKShare Eastmoney concept directory/current-member adapters. One successful directory probe reported total554 but returned100 rows; a first-page miss cannot establish theme absence. Production uses AKShare1.19.1, development1.20.65. Complete bounded pagination, versioned mapping and raw evidence remain pending. Current members cannot be backfilled into historical dates; times before first receipt must remain unobserved.

The local image was rebuilt and deployed. Eight deterministic in-container assertions and both changed source-file hashes passed. A pre-upgrade backup completed; five account/order/fill/user-session table fingerprints and the user model-settings file hash remained unchanged. No credential or model selection was changed. Deployment proof: `news-timestamps-deployed-proof.json`.

## November 18 lunch-break strategy deployment

Local deployment of application revision `cc20bba96dc8f81e539bfe5115cdac7112a5ab93` completed at 11:32 China time. The pre-upgrade backup passed verification. All four services were healthy, eight account and ledger table fingerprints matched, and the private model settings checksum matched. The served strategy browser bundle and three HTTP authorization checks passed. All three new tables for versions, human decisions and activations were empty.

Python 3.11 and 3.12 each passed 489 focused tests. The frontend passed 41 tests, ESLint and its production build. A rehearsal restored real backup data into an isolated database and verified forward migration and rollback with empty strategy tables. The **Strategy Versions** tab supports a buy-once rule with explicit human approval and a separate activation. User approval, activation and a naturally triggered order remain pending. Execution uses virtual funds only. Live trading, broker connections and AI promotion remain prohibited.

The November 18 morning archive contained 1,290 usable quote batches and one stale opening batch. The longest usable-receipt gap was 16.123 seconds. The formal November 17 observations cover the afternoon only. Afternoon coverage, end-of-day reports and the five natural trading days still need per-day verification.

Local evidence in the parent workspace's `work/quantdinger-natural-acceptance/` includes `strategy-production-deployment-20261118.json`, `strategy-upgrade-rehearsal-20261118.json` and `coverage-20261118-morning-close.json`.

## November 18 close

| Continuous auction session | Usable at receipt | Stale at receipt | Longest usable receipt gap | Upper bound on fresh archived quote coverage |
| --- | ---: | ---: | ---: | ---: |
| Morning | 1,290 | 1 | 16.123 seconds | 99.6% |
| Afternoon | 1,247 | 37 | 19.564 seconds | 49.5% |

The coverage estimate combines the 15-second freshness windows of hash-verified batches. The longest afternoon interval without a fresh archived batch was about 17.9 seconds. Historical trial receipts from November 17 morning are separate from the November 17 managed afternoon observations. Five natural trading days and reliable intraday quality remain open.

The November 18 report was generated naturally at 15:00:00 China time and reconciled. It recorded zero fills and missing verified closing valuation, leaving equity unavailable. The account held zero outstanding orders at the close. Feishu delivery remains unconfirmed. Local proof comprises `coverage-20261117-18-close.json` and `natural-close-ledger-20261118.json`.

## Feishu report receipts

Future daily notifications record the included report dates and summary SHA-256 before sending. A separate audit entry records the sanitized outcome. The daily ledger shows missing or unknown receipts, webhook acknowledgement or rejection, disabled configuration and pre-send validation failure. Webhook acknowledgement records provider acceptance. Group display requires separate confirmation. An interrupted send stays unknown across restart. Existing reports keep an unrecorded receipt status.

Tests used a simulated notifier to cover acknowledgement, rejection, an ambiguous response, disabled configuration, interruption and tenant isolation. Python 3.11 and 3.12 each passed 508 A-share tests, and the frontend passed 41 tests and its production build.

Local deployment completed at 16:12 China time. All four services were healthy, and 14 critical table fingerprints and private model settings matched the verified backup. The authenticated endpoint returned both existing daily reports with their original content and unrecorded receipt status. Two earlier attempts automatically rolled back after verification included volatile timer metadata and truncated a 4.4 MB browser bundle at 4 MB. The final check verified the configuration files and complete bundle. Existing reports were preserved, and actual delivery is pending a future natural daily report. Evidence is `feishu-receipt-upgrade-rehearsal-20261118.json` and `feishu-receipt-production-deployment-20261118.json`.

## Drafting a strategy from a research candidate

A signed-in user can carry a completed research candidate from their watchlist into a buy-once paper draft. The version hash binds the research run ID and input and output hashes. Draft creation, approval, activation and first order submission verify account ownership, candidate membership and research integrity. Entry parameters come from the user. Approval and activation require separate confirmations. Revocation can release unfilled reserves even when the research is unavailable.

Python 3.11 and 3.12 each passed 509 A-share tests, and the frontend passed 42 tests, ESLint and the production build. A controlled quote produced one revocable simulated order after research integrity was restored. The authenticated endpoint also created a draft using an actual archived result in an isolated database restored from backup. The decision, activation and ledger tables matched the backup. No model requests were made. Evidence is `strategy-reference-upgrade-rehearsal-20261118.json`. Actual user approval and natural intraday execution remain open.

Versions linked to research require a reader that understands the optional reference. The rehearsal verified a direct rollback before creation of a linked draft.

The local deployment switched to `13233f4` after the close at 17:01 China time on November 18. All four services were healthy, and the authenticated endpoints preserved the existing research and two daily reports. The served browser files matched the build. Fourteen critical table fingerprints and private model settings were unchanged. The production strategy list remained empty. Evidence is `strategy-reference-production-deployment-20261118.json`.
