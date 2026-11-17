# Intraday A-share paper worker

[简体中文](PAPER_WORKER_CN.md)

This runnable vertical slice connects live Tencent web snapshots, explicitly configured example/manual limit orders, a PostgreSQL cash/share ledger, reports and optional Feishu notices. Run `python -m app.ashare` as an independent process; it does not start the full upstream stack. The upstream UI, Strategy V2 deployments and Kafka dispatch are not integrated with this ledger. Upstream signal accounts remain separate.

## Setup

Use Python 3.11/3.12 and a dedicated PostgreSQL database. From `backend_api_python`:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-ashare.txt
cp ../configs/ashare-paper.example.json paper.local.json
export ASHARE_DATABASE_URL='postgresql://USER:PASSWORD@HOST:5432/PAPER_DATABASE'
python -m app.ashare --config paper.local.json migrate
python -m app.ashare --config paper.local.json init
python -m app.ashare --config paper.local.json quotes
```

On Windows activate `.venv\Scripts\Activate.ps1` and use `$env:ASHARE_DATABASE_URL`. Keep database credentials in environment variables and local configuration outside Git or in `*.local.json`. Migration creates only `qd_ashare_*` tables and does not reset accounts. Reinitialization cannot replace existing initial capital.

## Daily review and operation

The example configuration does not trade by default. Review the actual trading date, suspension/ST status and daily price limits, then populate dated `rules`, its `evidence` and `reviewed=true`. This is an operator assertion, not automatic exchange certification. Unknown status, ST, suspension, expired rules, missing books, contradictory limits and quotes older than 15 seconds cannot execute.

Supported scope: non-ST MAIN/CHINEXT stocks, 100-share limit orders and continuous auction. STAR, BSE, odd lots, call auctions and corporate actions are excluded. Daily rules do not roll forward automatically. Configuration is loaded at startup; stop and restart after reviewing changes.

The `buy_once_below` example is an acceptance strategy, not an investment recommendation. Set an explicit threshold, limit, quantity, unique ID and both approval/enabled flags to use it. It submits at most once, fills only against a later fresh quote and remains idempotent across restarts. A new ID denotes a new operator configuration. There is no AI activation path.

```bash
python -m app.ashare --config paper.local.json run --cycles 1
python -m app.ashare --config paper.local.json run --cycles 0
# Replace the example price with a reviewed price inside the daily band.
python -m app.ashare --config paper.local.json order --id manual-001 --symbol sz.000001 --side buy --quantity 100 --limit 10.00
python -m app.ashare --config paper.local.json status
python -m app.ashare --config paper.local.json cancel --id 1
python -m app.ashare --config paper.local.json report
```

Ctrl+C stops the worker. A database session lock permits one quote worker per account; account row locks serialize cash/order mutations. Database errors terminate for supervisor recovery, while quote transport errors record failed health and continue polling. Day orders expire on the next accepted later-day quote; until then they can be cancelled manually. Default evidence/reports are under `backend_api_python/reports/ashare`; PostgreSQL remains authoritative.

## Simulation model

Buys reserve cash including fees, then debit principal on fills. Sells reserve only shares acquired on earlier trading dates. Best bid/ask prices and displayed sizes bound fills, sharing depth across account orders. Repeated source timestamps cannot consume liquidity again. Partial fills use whole lots and cumulative minimum commissions.

Default configurable engineering fees are commission 0.0003, minimum CNY 5, sell tax 0.0005 and transfer fee 0.00001. Verify the intended account's fees separately. Initialization persists `fee_policy`; database policy wins on restart. Web snapshots do not reveal queue priority and cannot guarantee real-broker-equivalent fills. Stop affected simulation before corporate actions: cash/share adjustments are not implemented.

## Feishu notices

A personal user needs permission to create a custom group bot and obtain its Webhook; availability is not assumed. Follow the [official guide](https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot), set `FEISHU_WEBHOOK_URL` and optional `FEISHU_SIGNING_SECRET`, then add `--notify` to `run`.

Only fills and health-state changes produce paper-labelled messages. Missing configuration disables delivery. Transport failures are not retried automatically, and notice failures do not undo fills. There is no inbound ordering, approval or strategy promotion endpoint. No real Webhook was supplied; verification covers protocol/signature, redirect rejection and redaction using fake transport.

## Safety and tests

This downstream hard-denies brokerage factories/direct clients, REST/WS submission, queued live work and Quick Trade mutation endpoints. Environment flags cannot enable them. Agent Gateway permits only GET/HEAD/OPTIONS and returns 403 for other methods. Do not import real trading credentials. Upstream tests expecting successful real/demo broker connections are intentionally incompatible.

Install `requirements-ashare-test.txt`, set a dedicated `TEST_ASHARE_DATABASE_URL`, and run `python -m pytest tests/ashare -q`. Without the database variable integration tests skip. The [pending CI template](ci/ashare-paper-ci.yml) defines Ubuntu PostgreSQL acceptance and Windows quote/configuration/safety/notification/CLI checks. The current GitHub credential cannot write workflows, so it is not installed or remotely executed. This is not a Windows live broker. See [acceptance results](ACCEPTANCE.md).
