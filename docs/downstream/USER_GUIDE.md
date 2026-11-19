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
| Model API | Not required for this manual paper loop | The optional research panel uses the separately configured DeepSeek key; it never authorizes trades |
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

The custom bot sends one-way notices. When the administrator sets `ASHARE_REVIEW_BASE_URL` to the site's HTTPS origin, saving a new strategy draft sends a **View pending strategy** card to the account's configured Webhook. The button opens that exact version for sign-in and manual approval. Enabling automatic paper orders remains a separate confirmation. The server attempts a notice at most once per draft.

Newly generated daily ledgers trigger a summary of daily fill counts, buy/sell notional, fees, ending cash, positions and reconciliation, plus a clearly labelled watchlist. Without trustworthy closing valuations, equity, PnL and returns explicitly remain unavailable; cash is not presented as profit. The digest marks absent selection and strategy data and displays event times in Asia/Shanghai. Bounded messages identify truncation; full fill records and evidence remain in the page. Previously generated reports are not resent after configuring a bot, and uncertain transport delivery is not automatically retried.

The worker sends one exact-version reminder if a human-approved strategy is still disabled after ten minutes. With a qualifying intraday quote, it also identifies a missing daily security and band review for an enabled, unsubmitted version. Each daily-review reminder gets at most one attempt per version and trading day.

New virtual fills have their own notification with Beijing time, direction, symbol, quantity, execution price, notional and fees. The worker saves the fill identifiers and delivery receipt. The daily ledger notification can run in the same cycle. Older fills retain their original receipt state.

In **A-share Paper → Daily ledger**, check the **Feishu receipt** column. Webhook acknowledgement records provider acceptance. Check the Feishu group for actual display. Unknown outcomes remain in the ledger, and older reports show that no receipt was recorded.

## Remaining workflow

If the page offers AI, robotics, new energy, semiconductors, current events or custom directions, these currently save research preferences. Selecting a topic does not start news ingestion, recommendations or automatic trades.

The paper loop runs without Jev or DeepSeek. The optional announcement research panel uses a separately configured DeepSeek key and a supported model. See the [research guide](RESEARCH.md) for setup and evidence limits. A self-hosted Laya path needs local checkpoints, authorization, calibration and gateway integration. Laya weights and a working gateway have yet to be deployed. See the [decision-provider research](research/DECISION_PROVIDERS.md) for implementation status.

This version implements bounded announcement collection, an account-scoped research queue and evidence-linked candidates. Scheduled research can use an operator template to generate a review draft. Broader original industry news, out-of-sample evaluation and strategy iteration remain open. You must approve each strategy before it can run.

The local A-share Paper page now has a **Strategy Versions** tab. Save a buy-once draft with a symbol, trigger price, limit price, lot quantity and cash cap including fees. Approval and revocation bind the exact version ID and configuration SHA-256 to the signed-in user. Each account can have one approved version at a time. After approval, confirm **Enable Automatic Paper Order**. The worker can submit at most one simulated buy per version, using a verified fresh quote after activation and the same day’s human review. The reserved amount including fees must fit the version cap and available cash. Revoking cancels the unfilled remainder and releases its reserve. The strategy list was empty at deployment. Actual user activation and a naturally triggered order are pending. See the [local deployment verification](SECURITIES_ACCEPTANCE.md).

### Paper buy and sell settings

Open **Paper buy and sell settings** above the A-share Paper workspace. Use the current watchlist or select specific symbols. Set and enable the fee-inclusive cap per order. A fixed rule considers verified historical trends and settled virtual holdings before proposing a 100-share purchase, sale or no trade. The previous complete session's closing price anchors the trigger and limit shown in the draft. The earlier fixed-price template retains its scope and cap and requires explicit opt-in for the new rule.

A proposal uses completed same-day research with archived scheduling provenance. The evaluator first verifies 60 consecutive prior BaoStock sessions of unadjusted daily bars, with 20 warmup and 40 holdout sessions. A fixed rule uses conservative next-session limit fills, costs and T+1. Missing or inconsistent history and a lack of usable signals leave the strategy list as it is. The holdout covers the fixed rule for the current candidate. The research selection itself has no out-of-sample verification.

At most one immutable draft per research run attaches the original research and its verified historical evaluation. The Feishu card opens the exact version to review the side, prices, and history before separate approval and activation. Sales require settled T+1 holdings. The cap includes gross consideration and fees even at an improved fill price. Existing strategy versions retain their own controls after generation is disabled. Legacy operator files can produce a draft only when their exact terms match the evaluated rule.

In **News and Research**, use **Draft a paper rule** for a candidate on your watchlist. The form carries over the symbol, research run ID and both evidence hashes. Enter the lot quantity, trigger price, limit price and cash cap yourself. Saving a draft still requires separate version approval and activation, and the same day's security rules need human review. The worker verifies the research reference again before submitting a new order. For an independent draft, start in **Strategy Versions** or remove the research reference before saving.

**Strategy Versions** shows the estimated reserve including fees and available cash at refresh. Draft creation, approval and activation check the cap using the paper account's fee policy. If the cap falls short, revise it in a new draft. Available cash can change with other orders. The worker rechecks the ledger at the time of submission.

After activation, select **Refresh** in **Strategy Versions** to see the automatic paper order for that version. The detail shows when there is no submitted order. Once submitted, it displays the order ID, status, filled shares and amount, fees, and remaining reserve. The order stays visible after cancellation or end-of-day expiry.

Natural multi-session validation requires actual elapsed trading days. The [monitor](MONITORING.md) records sampled dates, failures and pending acceptance; synthetic replay cannot complete that evidence or all remaining work.

The announcement collector archives original CNinfo JSON and PDF files along with the provider-reported publication time and a separate UTC receipt time. Research excludes missing or unparseable publication times from fresh evidence. The local queue can include verified excerpts from the first three pages of eligible announcements. Research results distinguish title-only citations from those that include an excerpt. After ingestion, the queue leaves up to two minutes for extraction and checks on the next scheduled tick. An eligible title remains usable if an excerpt is still unavailable after the window. Broader industry coverage and the first real scheduled run using an excerpt remain pending. See the [news acceptance record](NEWS_RESEARCH_ACCEPTANCE.md).

Expand an announcement under a completed candidate to see the exact cited version, original PDF, publication time and archived hashes. If the research included an excerpt, the detail shows the text and hash recorded in that run. Later revisions appear separately in the news feed.

The **Theme members** tab in the A-share paper page lets you select an archived board and view members, collection time and evidence hash. As of early November 18, 2026, one real receipt is available for each of 人工智能 (AI), 机器人概念 (robotics concept), and 新能源 (new energy). Open the tab or refresh its archive view to see the additional choices. These are membership observations at collection time, not live quotes or certified historical lists. Viewing or refreshing does not fetch providers, change watchlists or place orders. Research direction choices remain preferences. Explicit research requests use the separate evidence-bound queue; scheduled collection/research requires deployment of the separately reviewed timers. See [theme documentation](THEMES.md).

This local deployment also has a daily 20:10 Asia/Shanghai collection job for those three themes, randomized by at most one minute. Failure preserves old observations with an explicit problem rather than presenting them as new success; failed or uncertain same-evening attempts are not automatically retried. Installation and pre-window skips are verified; the first natural evening run remains pending. This updates theme data only, without automatic stock selection, strategy approval or trading.

The local 2026-11-18 release includes the News and Research tab and one verified real DeepSeek call. Open A-share paper → News and Research → Refresh to read existing results without requesting another model call. The authorized account has a daily 08:30 request schedule, a two-minute queue worker and half-hour news collection from 07:00 through 22:30 China time. Manual and scheduled requests share the daily maximum of three new runs; none approve strategies or submit orders. The first scheduled announcement collection passed archive verification at 08:00 China time on November 18. The first daily research run completed at 08:30 on the same day and can be read with its evidence links in the panel. See [actual acceptance](NEWS_RESEARCH_ACCEPTANCE.md).
