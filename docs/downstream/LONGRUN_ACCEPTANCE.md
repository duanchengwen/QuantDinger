# Durable deployment and multi-session acceptance — 2026-11-17

[中文](LONGRUN_ACCEPTANCE_CN.md) · [Deployment](DEPLOYMENT.md) · [Operations](OPERATIONS.md) · [Replay](MULTIDAY_VALIDATION.md)

## Delivered and running

The local preview has been migrated to an isolated persistent Compose stack at `http://127.0.0.1:5177/#/ashare-paper`. Gunicorn API, paper worker, PostgreSQL and nginx are separate services. No upstream live executor, AI queue or evaluator is running. Password, session-version, cash, position, order and fill fingerprints matched before/after migration. Legacy temporary API/worker processes were stopped; the source database and pre-cutover backup remain.

Docker restarts exited processes. Database statement/lock timeouts bound stalls and feed the reconnect loop. Liveness is separate from trading readiness: lunch and awaiting review do not trigger restarts. An unhealthy Docker status alone does not restart a hung container; a physical host reboot was not performed.

A user systemd backup timer is enabled for **18:10 Asia/Shanghai daily**, with up to 60 seconds jitter. Docker startup and user lingering are enabled. The same service was actually invoked and succeeded. Persistent pause intent, signal cleanup and ExecStopPost recover previously running services after interrupted backups. Deployment mutations and backups share a lock. Evidence and old backups are not automatically deleted.

## Runtime changes

- Expire DAY orders at session close and release reservations. Daily rules still require human review. The underlying broker checks both current and quote time against XSHG, so the old CLI cannot use a manual flag to bypass holidays or unknown calendar coverage.
- Reconcile cash, shares, fills, fees and reservations; mismatches block matching until actually repaired.
- Heartbeat even with no accounts; pause fetching/matching below 100 MiB free evidence space. Poll at least 300 seconds apart off-session and 60 seconds during lunch/pre-open. Normal worker logs emit on state changes or once per minute; container logs rotate.
- Immutable per-account daily reports reconstruct historical cash/shares from fills and catch up offline dates. Missing verified closing prices leave equity null, not a fabricated return.
- Observation counters retain first/last samples, wall-clock versus controlled-time counts, failure/staleness statistics, source timestamps and hashes. Samples are not continuous-uptime proof; unobserved dates are explicit.
- The official UI adds Daily reports. Recent history is bounded to 200 records plus every active order; fee totals and idempotency still use the full authoritative ledger.

## Validation

| Check | Result |
| --- | --- |
| Python 3.11 / PostgreSQL 18 selected regression | 202 passed, 1 deselected |
| Python 3.12 / PostgreSQL 18 selected regression | 202 passed, 1 deselected |
| Deployment/backup safety tests | 13 passed |
| Frontend tests / full build / ESLint | 10 passed; build/lint passed |
| Ruff / documentation / patch application | Passed |
| Full non-root image | Built; UID/GID 10001 |
| Real deployed HTTP/auth/safety/fault checks | 14 passed |
| Isolated database restore | 13 ledger/user/session table fingerprints matched |
| Full fresh-project restore | Database matched; 1,126 evidence-file hashes matched |
| Real scheduled-backup service invocation | Result=success, ExecMainStatus=0; timer enabled |

This is not the complete upstream suite. The existing `retired_parameter_tuning_is_not_exposed_or_billable` exclusion remains; four upstream calendar/pandas deprecation warnings remain. The image supplements three missing transitive dependencies (Babel, humanize, docopt-ng) and permits only the upstream-documented LiteLLM/filelock metadata conflict; see deployment documentation.

Real drills killed the paper worker child and observed Docker restart, recreated the API container and observed nginx DNS recovery, and stopped/restarted PostgreSQL. Authenticated requests returned 503 during the outage and the same valid session recovered. Password/session, cash/share, order/fill fingerprints stayed unchanged. HTTP acceptance used a short-lived server-signed token without changing the user's password or revoking their browser session. The browser connection was unavailable; repeated click-through UI acceptance is not claimed for this iteration.

Backups were restored first into a network-isolated PostgreSQL container and then into a fresh Compose project with evidence extraction and per-file comparison. Only the generated restore project/volumes were cleaned up; source deployment and backups remain. No real Feishu Webhook was configured or message sent.

## Controlled multi-session replay

Each Python version passed **294 scenario assertions** using real PostgreSQL, ManagedWorker, PaperBroker and XSHG:

- Seven sessions: 2026-09-28/29/30, 10-08/09/12/13, spanning National Day closure and weekends.
- Fourteen reconnects, including seven forced terminations of the test's own database session.
- Thirteen virtual fills and seven unique reconciled reports; final cash CNY 98,925.87, 100 shares and zero reservation.
- T+1, expired human reviews, partial-fill cancellation, lunch/afternoon resumption, close expiry, duplicate/out-of-order/stale quotes and idempotent retries.

Quotes, time and reviews are explicitly synthetic fixtures. Both artifacts have `wall_clock_polls=0`; this is not seven naturally observed market days. The real deployment now collects actual samples from this date, starting partway through the first day.

## Remaining acceptance dependencies

| Item | Status |
| --- | --- |
| This iteration P0: durable deployment, restart, backup/restore, health | Implemented and exercised above |
| This iteration P1: multi-day state, reports, reconciliation, history growth | Implemented; controlled replay and PostgreSQL regression passed |
| Natural multi-day acceptance | Collection running, multiple natural days have not elapsed. Accumulate at least five real sessions, label the partial startup day, and examine samples, incidents, ledger and recovery evidence per day. Trading still needs daily human review |
| GitHub Ubuntu/Windows CI | Template updated; credential scope is public_repo without workflow write permission. New workflow not installed/run; real Windows not validated |
| Real Feishu delivery | User must configure the bot and explicitly test delivery through the existing guide |
| Certified historical universe/status, corporate actions, full boards/auctions/odd lots | Still incomplete; human review and the supported subset are not full A-share coverage |
| VectorBT/Backtrader/Optuna research, news, industry rotation/approvals, arbitrary Strategy V2 integration | Earlier standalone research code is archived and not migrated into this Fork's runtime; not accepted features of this iteration |
| Live trading, AI automatic promotion, Windows brokerage connection | Still prohibited; no unlock switch added |

Local evidence is under the workspace parent `work/quantdinger-longrun-acceptance/`: JUnit, both replay JSON artifacts and synthetic raw evidence, build logs, deployment-drill.json and full-restore-proof.json. Private state/backups live in `runtime/quantdinger-paper/` and `runtime/quantdinger-paper-backups/`, outside Git. Immutable served frontend assets are under `runtime/quantdinger-paper-assets/20261117-longrun/`.

Legacy absolute evidence references remain intact; copied artifacts live at container `/app/data/evidence`, with prefix mapping in the migration manifest and unchanged hashes. Backups contain secrets; filesystem permissions are not encryption. Encrypt off-host copies. Web quotes carry no exchange-grade latency SLA, paper depth is not a real queue, and unknown calendar coverage still blocks trading.

The structure baseline records only the existing hard-deny guards introduced in `cd0bab1` (+11 HTTP-file lines, +2 queued-live-order function lines); all other upstream growth thresholds and banned patterns remain unchanged.


## November 18 cloud cutover at 20:20 Asia/Shanghai

The two-core Ubuntu 24.04 instance took ownership of the paper stack and six background timers. The source services and timers were stopped. Source data and the cutover snapshot were retained. PostgreSQL, API, web and worker passed health checks. The local web port binds only to loopback. Public access uses validated HTTPS. A simulated certificate renewal succeeded. The first actual scheduled renewal remains to be observed.

Restoration used a post-close snapshot taken with writers stopped. The restore verified every saved database fingerprint and both evidence and private settings archives. The restore command consumed the remaining launcher standard input, leaving the verified database ready and the services unstarted. A separate script resumed using that same verified snapshot and enabled public access after local health and authorization checks. Post-cutover checks matched 20 stable table fingerprints and the private model settings file. The existing two paper orders, one fill, one position and two reconciled reports were readable. Five anonymous request types returned 401.

Signed Feishu configuration was restored. A browser rehearsal in isolation preserved the exact review target through login and opened that version read-only. Approval and activation are separate user actions. Actual receipt in the Feishu group awaits a normal message. Acceptance did not call a model, resend reports or write strategies.

November 17 had partial local coverage. November 18 was observed on the source host. The migrated quote archive passed read and hash checks on the cloud host. Five complete natural cloud sessions, Windows and remote CI remain open. Local evidence is in the project parent's `work/quantdinger-natural-acceptance/`.

Scheduled review drafts and read-only strategy evaluation were integrated on the same day. With isolated PostgreSQL, 550 backend tests passed on each of Python 3.11 and 3.12. Operations passed 163 tests and three subtests. The frontend passed 48 tests and an isolated production build. The first combined Python 3.12 run omitted the `scripts/` mount. A fresh-database rerun with the proper mount passed. The cloud remains on cutover release `c5187ef`. The two integrated features still require a confirmed template, release and natural acceptance.

The initial public browser load exceeded 18 seconds. After enabling HTTP/2 and static asset compression at the proxy, the main script transferred about 1.24 MB. A public cold-browser check reached login in about 26 seconds, retained the exact strategy version and made zero write requests. The next application image built and passed an import smoke test. It is staged locally.

The first natural cloud CNinfo task ran at 20:30 and archived seven items. Receipt `b5b14175350f4f6788a5f272ea6d2098` recorded source SHA-256 `bf7850ffc32aeec48ae11e08ee4790d69f1e1810880a1d5071df1f4328be6925`. Read-only monitoring verified the archive. The next regular research request is scheduled for November 19 at 08:30 Asia/Shanghai.


## November 19 cloud release for the paper buy template

Release `554ff58` went live at 07:30 Asia/Shanghai. Public HTTPS on 5188 returned 200 with a valid certificate. Port 443 remains the Feishu review entry point. The four services passed health checks. The template endpoint rejected an anonymous request and returned revision 0 to the authenticated paper account at release. Eight fingerprints matched the verified pre-release backup for accounts, orders, fills, lots, strategy versions, human decisions, activations and reports. A signed-in public browser on 5188 rendered the template form, and the actual API returned 200 with zero write requests. Deployment and read-only probes did not call the model, send Feishu messages or submit any orders.

In **A-share Paper → Strategy versions → Automatic buy proposal settings**, the user can save a symbol scope, trigger, limit and fee-inclusive cap. Daily research and proposal evaluation have the same explicit account scope. Feishu review cards, paper operations and ledger summaries show the actual event time in Asia/Shanghai. Cards open the exact version for human approval and separate activation.

Python 3.11 and 3.12 each passed 574 backend tests. Operations passed 169 tests and three subtests. The frontend passed 54 tests, ESLint and its production build. An isolated 390 px browser exercised symbol selection by name, save and stale revision handling. An isolated PostgreSQL end-to-end test observed one research run, one draft, one simulated Feishu attempt and no premature orders. After separate approval, activation and security review, controlled quotes submitted one paper buy and filled 100 shares. Third-party responses and quotes in the test were controlled fixtures. Natural research, Feishu display, human approval and a naturally triggered order are tracked separately.
