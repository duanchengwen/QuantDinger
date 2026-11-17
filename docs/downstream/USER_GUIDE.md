# Personal-user guide: real A-share quotes and virtual funds

[简体中文](USER_GUIDE_CN.md) · [Web workspace](WEB_WORKSPACE.md) · [Deployment](DEPLOYMENT.md) · [Monitoring](MONITORING.md)

## Accounts and API access

**The current manual A-share paper loop does not require multiple API subscriptions or a brokerage account.** The deployed machine needs power and access to the public quote source. Your local QuantDinger login is separate from Feishu or a market-data account.

| Item | Required setup | Current purpose |
| --- | --- | --- |
| QuantDinger account | Existing deployment-created login | Your isolated virtual ledger; do not recreate an existing account |
| Intraday quotes | No user-supplied API key | Tencent web snapshots with source and receipt times |
| BaoStock historical data | Default client login without personal credentials or API key | Dated historical security lists, daily bars and provider-reported security status |
| AKShare | No unified paid API key for the public interfaces used here | Python access to public sources; source-specific restrictions still apply |
| Feishu | Optional group custom-bot Webhook and recommended signing secret | Paper fills, state changes and daily-report events |
| Model API | Not required for this manual paper loop | Other upstream AI screens may need a provider key; this does not integrate AI research into this A-share ledger |
| SMTP | Not required for password login/current-password changes | Needed for email-code registration or recovery features |
| Broker API | Not required; do not configure one | Live trading and automatic AI promotion remain prohibited |

BaoStock's publisher [quick start](https://pypi.org/project/baostock/) documents free historical data and a credential-free `bs.login()` example. AKShare describes [public financial websites and exchange sources](https://akshare.akfamily.xyz/introduction.html). Public access does not certify exchange-grade real-time performance, a complete point-in-time database or perpetual availability. A future licensed source requires separate access and licensing review.

## First use

1. Open `http://127.0.0.1:5177/#/ashare-paper` and log in. Initial credentials belong to the private deployment configuration; after changing your password, use the new password. Do not paste credentials into chat.
2. Open **A-share Paper** and inspect funds. Set initial virtual funds only if no virtual account exists. Existing cash, holdings and orders continue unchanged.
3. In **Watchlist**, save a small set of symbols such as `sz.000001`. Symbols with positions or open orders cannot be removed directly.
4. Inspect quote price, bid/ask, source time, receipt time and freshness. During continuous trading, matching needs fresh quotes. Closed sessions, lunch, stale input and awaiting review are separate from process failure.
5. Use **Review rules** to verify today's status, price band and evidence. If the version offers batch review, inspect evidence and explicitly confirm each candidate. Missing, unknown or previous-day evidence cannot approve today's status.
6. Enter direction, a supported 100-share quantity and limit price, then confirm the paper order. Buy orders reserve virtual funds; only subsequent fresh quotes can fill them. Submission does not guarantee a fill. T+1 applies, and remaining open quantity may be cancelled.
7. Inspect **Orders, Fills, Positions and Daily Ledger**. Cash, fees and fills derive from the actual virtual ledger. Missing trustworthy closing valuations remain unavailable.

## Daily routine

Check worker health, watched symbols and today's status sources before use. During continuous trading, check freshness and review today's rules. Reviews are not approved or carried forward automatically. Unknown historical status, IPO/relisting and corporate actions still require human evidence.

The independent worker continues polling and matching after the browser closes, provided the host, power, network, Docker and storage remain available. Unfilled DAY orders expire at close and release reservations; daily reports are generated. The following trading day needs its own rules review.

The supported boards and rules are described in the [workspace guide](WEB_WORKSPACE.md). Missing auctions, board-specific rules, odd lots or corporate actions cannot be enabled merely by checking a box. Web snapshots do not guarantee tick-for-tick equivalence with another paper platform or exchange queue execution.

## Feishu

Use the **desktop Feishu client**: target group → Settings → Bots → Add bot → Custom bot. Copy its Webhook and, if enabled, signing secret. The mobile client does not offer custom-bot creation; see the [official group-bot guide](https://www.feishu.cn/hc/zh-CN/articles/360024984973-在群组中使用机器人). A personal account still needs permission in that group.

Open **Feishu notifications** in A-share Paper; older layouts place this tab below orders/positions. Save the Webhook/secret, explicitly send one test message and check actual group delivery. If keyword verification is enabled, allow `模拟交易`, which is present in every message. Never commit or share the secret.

This is one-way notification. Group trading commands and strategy approvals are not connected. “Configured” means saved, not verified delivery.

Newly generated daily ledgers trigger a summary of daily fill counts, buy/sell notional, fees, ending cash, positions and reconciliation, plus a clearly labelled watchlist. Without trustworthy closing valuations, equity, PnL and returns explicitly remain unavailable; cash is not presented as profit. AI recommendations and strategy updates are marked unavailable while unimplemented. Bounded messages identify truncation; full fill records and evidence remain in the page. Previously generated reports are not resent after configuring a bot, and uncertain transport delivery is not automatically retried.

## Remaining workflow

If the page offers AI, robotics, new energy, semiconductors, current events or custom directions, these currently save research preferences. Selecting a topic does not start news ingestion, recommendations or automatic trades.

Neither Jev nor DeepSeek is required for the current paper loop. A future generative DeepSeek integration needs its separate model-service key. A self-hosted Laya path needs local checkpoints, authorization, calibration and gateway integration; it is not enabled by changing a URL and does not run through the DeepSeek API. No Laya weights or working gateway are deployed here. See the [decision-provider research](research/DECISION_PROVIDERS.md) for implementation status.

Selecting industries, collecting news, recommending stocks, automatically paper-trading a preapproved strategy and proposing strategy updates remain separate implementation/acceptance items. This guide does not present that desired workflow as complete. AI may not automatically upgrade or promote strategies. The current primary flow is human-reviewed rules, manually confirmed virtual orders and continuous background quote matching.

Natural multi-session validation requires actual elapsed trading days. The [monitor](MONITORING.md) records sampled dates, failures and pending acceptance; synthetic replay cannot complete that evidence or all remaining work.

News time semantics: the official research collector preserves the provider-reported publication value, leaves missing values empty, and records receipt time separately in UTC. Reports exclude news with missing or unparseable publication times from evidence instead of treating receipt time as freshness. Search dates are not publisher verification; scheduled A-share original-document archiving and hash provenance remain unfinished.

The **Theme members** tab in the A-share paper page lets you select an archived board and view members, collection time and evidence hash. As of early November 18, 2026, one real receipt is available for each of 人工智能 (AI), 机器人概念 (robotics concept), and 新能源 (new energy). Open the tab or refresh its archive view to see the additional choices. These are membership observations at collection time, not live quotes or certified historical lists. Viewing or refreshing does not fetch providers, change watchlists or place orders. Research direction choices remain preferences; scheduled news and automatic research require further integration. See [theme documentation](THEMES.md).

This local deployment also has a daily 20:10 Asia/Shanghai collection job for those three themes, randomized by at most one minute. Failure preserves old observations with an explicit problem rather than presenting them as new success; failed or uncertain same-evening attempts are not automatically retried. Installation and pre-window skips are verified; the first natural evening run remains pending. This updates theme data only, without automatic stock selection, strategy approval or trading.
