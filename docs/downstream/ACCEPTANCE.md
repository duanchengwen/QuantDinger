# Intraday paper MVP acceptance — 2026-11-17

[简体中文](ACCEPTANCE_CN.md)

## Result and scope

The Ubuntu worker completed configuration → real intraday web quotes → example strategy → virtual orders/positions → reports. No real broker connection or AI promotion was enabled. Feishu is optional and send-only. This is a scoped engineering MVP, not full upstream UI integration, complete A-share coverage or long-term unattended acceptance.

## Local verification

| Environment / check | Result |
|---|---|
| Python 3.11.15 / PostgreSQL 16 | 97 new-module tests passed |
| Python 3.11.15 / PostgreSQL 18 | 115 passed, 1 explicitly deselected |
| Clean Python 3.12.15 / PostgreSQL 18 | 115 passed, 1 explicitly deselected |
| Ruff / documentation links / diff whitespace | Passed |
| Windows | CI template provided; neither local nor remote Windows execution |

The 115 tests comprise 97 downstream tests, 10 upstream execution-contract tests and 8 upstream Agent/OpenAPI alignment checks. The deselected `retired_parameter_tuning_is_not_exposed_or_billable` requires full upstream app initialization, outside the minimal environment. This is not a full upstream-suite pass; tests expecting successful live client creation conflict with the downstream deny policy.

GitHub rejected the initial push containing a new Actions workflow because the configured credential lacks `workflow` scope. The code push therefore contains no active workflow addition. The Ubuntu/Windows template is retained at [ci/ashare-paper-ci.yml](ci/ashare-paper-ci.yml), ready to install under `.github/workflows/` after the credential is authorized. Remote CI is not reported as passed.

Coverage includes evidence/hash/timestamps, stale and invalid quotes, daily bands, atomic reserves and concurrent buys, T+1, sell reservations, partial fills and cumulative commissions, quote idempotency, tenancy, cancellation/expiry, persistence, CLI, exclusive worker ownership, disabled strategies, expired daily rules, brokerage denial, read-only Agents and Feishu signature/redaction/redirect handling.

Integration found and fixed nested transaction contexts in the CLI, mismatched quote-universe limits, missing strategy price-band validation and a missing upstream metrics dependency in the clean test environment. Relevant regression checks/dependencies were added.

## Actual intraday smoke

On 2026-11-17 around 10:52–10:54 Asia/Shanghai, 20 real Tencent quote cycles were healthy. A fixed acceptance example submitted one 100-share buy; a later quote generated one virtual fill:

| Field | Result |
|---|---|
| Symbol | sz.000001 |
| Initial virtual cash | CNY 100,000.00 |
| Fill quote timestamp | 2026-11-17 10:52:45 +08:00 |
| Virtual fill | Buy 100 at CNY 12.20 |
| Model fees | CNY 5.01 |
| Remaining cash | CNY 98,774.99 |
| Shares | 100, unavailable for same-day sale |
| Raw quote SHA256 | `757216f965c1804bcd9c47f92f0551d9da05f6bd07a7a2e24e949a23c9784204` |

After restarting PostgreSQL and the worker, five additional cycles were healthy, with the same cash/position and still only one fill. Observed quote delay was seconds in this probe; it is not a latency SLA or multi-day uptime result. Sell/T+1 and partial-fill behavior used controlled quotes with real PostgreSQL tests, not fabricated next-day live-market observations.

The dedicated acceptance account used explicitly stated engineering assumptions for daily security status, not an authoritative historical ST/suspension database. The shipped example remains disabled. No real Feishu Webhook was supplied or messaged.

Local raw quotes, run/restart reports and JUnit outputs are under the workspace's `work/quantdinger-acceptance/`. Earlier failed runs remain recorded and are not counted as the repaired `live-run-fixed.jsonl` result. Credentials and temporary acceptance configuration are excluded from Git.

## Cleanup and remaining work

As requested, the superseded active `a-share-agent` project and `work/oss-evaluation` copies were removed after per-file verified archives under `outputs/legacy-archive-20261117/`. Archives preserve source, uncommitted changes, Git history, research modules and evidence. Unmigrated Optuna/research features are archived rather than active. Five historical OSS reviews remain in `research/`.

Next steps are upstream UI/Strategy V2 and worker/audit integration; authoritative dated security/universe and corporate-action data; broader board/lot/auction rules; multi-day monitoring, recovery and latency evidence; valuation/risk reporting; actual Feishu delivery after configuration; and Windows validation. Approvals are not inbound trading commands. Real brokerage requires new explicit authorization and reviewed code; this branch has no enable switch.
