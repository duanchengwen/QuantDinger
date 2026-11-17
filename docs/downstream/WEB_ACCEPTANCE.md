# Official UI and continuous-worker acceptance — 2026-11-17

[简体中文](WEB_ACCEPTANCE_CN.md) · [Run guide](WEB_WORKSPACE.md)

## Delivery

The official Vue 5.7.1 frontend at `2ccc70b208141b3898060c4888853f83111ecfd8` now has an A-share paper route/menu using upstream login, JWT, roles and localization. Backend baseline remains `79caf19`. The fork contains the extension and pinned build tooling, not the entire separately licensed frontend.

The page uses real APIs/PostgreSQL for quotes, cash, positions, orders/fills, daily review and manual paper buy/sell/cancel. An independent worker owns calendar checks, expiry, polling and recovery. Feishu has guided setup, encrypted storage, disabling and an explicit delivery-test button.

## Automated checks

| Check | Result |
|---|---|
| Python 3.11 / PostgreSQL 18 | 159 passed, 1 explicitly deselected |
| Python 3.12 / PostgreSQL 18 | 159 passed, 1 explicitly deselected |
| Frontend freshness/date/viewer compatibility | 6 Node tests passed |
| Full official Vue build | Passed, upstream large-chunk warning retained |
| Extension ESLint, backend Ruff, docs and diff checks | Passed |
| Windows / GitHub Actions | Not executed; credential still lacks workflow write scope, template retained |

Coverage includes cash/share rules, database concurrency/idempotency, tenant isolation, viewer denial, daily bands, freshness/expiry, review provenance/audit, encrypted secret redaction, worker ownership/isolation/reconnect and authentication failures. Invalid/revoked tokens remain 401; unavailable authentication storage returns 503 without granting access or using cached identity.

The full-app `retired_parameter_tuning_is_not_exposed_or_billable` case remains deselected; this is not an upstream-wide pass. Four upstream calendar/pandas deprecation warnings remain. API integration tests replace the authentication boundary, while browser acceptance below uses real upstream login and database-backed user/session checks.

## Actual browser acceptance

A dedicated local database and generated test account logged in through the official page without injecting browser authentication state:

1. Initialized CNY 100,000 virtual capital and observed actual intraday sz.000001 quotes with both timestamps.
2. Explicitly approved dated rules with an engineering-evidence note, retaining reviewer/time/raw hash/audit. The note disclaims exchange-certified security status.
3. Submitted a 100-share buy limited at CNY 12.30. The page first showed CNY 1,235.01 reserved; a later actual quote filled at 12.20, fees 5.01, leaving cash 98,774.99 and 100 shares.
4. A same-day 100-share sell was rejected by T+1.
5. A nonmarketable 11.07 buy reserved CNY 1,112.01; UI cancellation released it. The account still had exactly one fill.
6. Inspected the Feishu three-step guide, secret-style inputs, unconfigured status and delivery-test control. No real Webhook was supplied or messaged.

## Recovery and session transitions

Stopped and restarted the dedicated PostgreSQL container. The first exercise exposed upstream auth treating storage failure as invalid credentials; this was fixed with regression coverage. On retest the browser retained its valid session, marked old quotes stale and blocked orders. Database restoration automatically recovered API/worker/page without another login or changed balances/positions/fill count.

Natural running crossed 11:30 Asia/Shanghai and entered `lunch`, stopping matching. Day rollover, expiry, holidays and calendar coverage use controlled-time tests; this is not multi-day natural observation or a claim of observed afternoon reopening.

Visual review also fixed dark-mode contrast, a browser-clock refresh flicker and raw service error codes. Screenshots, account JSON, logs and JUnit outputs are retained locally under `work/quantdinger-ui-acceptance/`; secrets are excluded from Git.

## Boundaries

Live brokerage and AI promotion remain prohibited, with read-only Agent Gateway. Calendar coverage currently ends 2026-12-31; daily security status remains human-reviewed. Corporate actions, auctions, STAR/BSE, odd lots and arbitrary Strategy V2 auto-deployment remain unsupported. Web snapshots have no exchange-grade latency or queue-fill guarantee. The local preview is a development/acceptance environment; sustained operation needs the documented service supervision and backups.

## Password-change follow-up (2026-11-17)

The reported `Invalid verification code` exposed an email-only profile flow in a local deployment without SMTP. Sending also created a code before checking mail availability. The profile now defaults to the existing current-password endpoint while retaining email verification as an alternative. Proof payloads stay separate; localized errors distinguish incorrect passwords, invalid codes, missing mail setup and rate limiting. Success clears local session state and returns to login.

The current-password endpoint now uses the same strength policy as email changes, rate-limits failed attempts and records an audit event. Passwordless accounts must verify email; an existing session alone cannot set their initial password. No undeliverable code is created when SMTP is unconfigured. The actual user's password was not changed.

Validation: Python 3.11 regression 179 passed / 1 deselected; Python 3.12 targeted password/auth regression 30 passed; 10 frontend tests, full build, ESLint, Ruff, documentation and repeatable patch application passed. Thirteen new backend tests cover proof validation, tenant identity, passwordless accounts, strength, rate limiting and unavailable mail; existing auth tests cover token invalidation. The browser remains at login; entering and submitting the user's chosen password is a user handoff and is not claimed as completed browser acceptance.
