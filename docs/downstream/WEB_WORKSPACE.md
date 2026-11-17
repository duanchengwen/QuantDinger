# A-share paper trading in the official UI

[简体中文](WEB_WORKSPACE_CN.md)

The extension adds **A-share paper** (`/#/ashare-paper`) to the official Vue shell, reusing login, JWT, roles and localization. It shows quotes/freshness, virtual cash/positions, manual buy/sell/cancel, orders/fills, daily review, watchlists and Feishu setup. HTTP performs authentication, validation and persistence; an independent worker collects and matches.

## Deployment

Update both backend and frontend: the upstream prebuilt frontend image does not contain this extension. Follow the [pinned frontend build guide](../../frontend_extensions/ashare-paper/README.md). The frontend has a separate license; this fork stores only the extension, small integration patch and reproducible preparation script.

Install `backend_api_python/requirements-ashare.txt` into the existing full backend environment. Keep `DATABASE_URL` and a stable `SECRET_KEY`; optionally set `ASHARE_DATABASE_URL` for a separate paper database. Both authentication and paper databases must be available. The new `qd_ashare_*` ledger never shares live balances.

From `backend_api_python`:

```bash
python -m app.commands.migrate
# Local acceptance backend; retain the official production WSGI deployment otherwise.
export QD_PROCESS_ROLE=api
export SKIP_STARTUP_HOOKS=1
export PYTHON_API_HOST=127.0.0.1
export PYTHON_API_PORT=5017
python run.py
```

Run separately with the same environment:

```bash
python -m app.ashare.managed_worker --cycles 0
```

From the repository root, using the official frontend Node/pnpm requirements:

```bash
python scripts/frontend/prepare_ashare_frontend.py ../work/quantdinger-frontend --build
cd ../work/quantdinger-frontend
VITE_DEV_PROXY_TARGET=http://127.0.0.1:5017 pnpm preview --host 127.0.0.1 --port 5177
```

PowerShell uses `$env:NAME` assignments. These localhost commands are for development/acceptance. For sustained deployment, retain the official reverse proxy/production WSGI setup and supervise API and worker separately with restart policies. Do not publicly expose a development server.

## Using the page

Log in, select **A-share paper**, and initialize virtual capital. Reinitialization cannot reset it. Review today's status/bands with an evidence note before trading; approval stores the operator, timestamps, raw quote hash and audit event. Source snapshots do not automatically approve rules.

Orders require a fresh quote, healthy worker, current review and continuous auction. Confirm symbol/side/quantity/limit before submission. Buys reserve cash, fills use later quotes, same-day shares cannot be sold, and cancellation releases remaining reserves. Accounts are isolated by authenticated user; viewers cannot mutate. Symbols with holdings/open orders cannot be removed from the watchlist.

Only reviewed non-ST MAIN/CHINEXT stocks in 100-share lots are supported. STAR, BSE, odd lots, auctions and corporate actions are excluded. Web snapshots are not exchange tick feeds or queue-priority guarantees.

## Continuous operation

The worker identifies exchange holidays, pre-open, lunch, closing auction and close. Rules expire daily and require renewed human review. Pinned XSHG calendar coverage ends **2026-12-31**; unknown dates fail closed until a verified calendar update. Prior-day orders expire without needing a fresh quote.

Settings/reviews are reloaded while running. Database ownership locks prevent duplicate workers; disconnects reconnect with capped 1–30 second backoff. One account's feed failure does not stop others. Polling is currently serial and not accepted for large account counts.

Authentication infrastructure failures return 503 and deny business access without invalidating a valid client session. Invalid/revoked tokens still return 401. The browser continuously recomputes quote age, including during failed polling.

## Feishu guidance

The page explains custom group-bot creation, Webhook/signing-key entry, saving and explicit test delivery. Group-bot permissions may not be available to every personal account. Configuration is optional and encrypted per account, never returned to the page. Disabling clears it. Keep a stable `SECRET_KEY` of at least 32 bytes; rotation requires reconfiguration of unreadable credentials.

The worker sends only fills and state changes, accepts no inbound trading commands and does not blindly retry uncertain deliveries. No real Webhook was supplied for acceptance; real delivery awaits the user's explicit test.

See [web acceptance](WEB_ACCEPTANCE.md) and [API contract](paper-web-openapi.json). Live brokerage and AI promotion remain prohibited. This does not automatically connect arbitrary upstream Strategy V2 deployments to the new ledger or claim multi-day natural observation.

## Changing a local account password

Profile → Change Password defaults to **Current password**. Enter the current login password, the new password and its confirmation; SMTP is not required. New passwords require at least 8 characters with uppercase, lowercase and a digit. A successful change invalidates existing sessions and returns to login. Repeated incorrect attempts are rate-limited.

**Email code** remains an alternative and requires configured server SMTP plus a reachable account email. The code is not the login password. Passwordless accounts must verify email before setting a password. Unconfigured email fails clearly before creating an undeliverable code. Never share passwords, email codes or Webhooks in chat or commit them to the repository.
