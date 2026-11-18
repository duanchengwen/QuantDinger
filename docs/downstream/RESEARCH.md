# Evidence-bound A-share research

[简体中文](RESEARCH_CN.md) · [User guide](USER_GUIDE.md) · [News evidence](research/NEWS_SOURCES.md)

This version implements a separate research queue and an A-share page panel. It reads archived theme members and a limited sample of CNinfo company announcement titles, then asks the explicitly configured DeepSeek model for research candidates. It does not implement the complete industry-news → executable strategy → approved automatic PAPER workflow. Deployment, real provider calls and natural timer execution require their own acceptance evidence; implementation and offline tests do not establish that those steps succeeded.

## User flow

1. Save the account's research preferences. This does not launch collection, model calls or trades.
2. In model settings, save the DeepSeek key privately and choose a supported DeepSeek model, such as `deepseek-flash`. Do not paste keys into chat. The research adapter reads the existing service configuration and does not change the user's global provider or model choice.
3. Open the news/research panel. Inspect the source, publication and receipt times, hashes and explicit title-only scope. Refreshing reads existing records; it does not fetch sources or run the model.
4. Request research explicitly, or use a separately deployed schedule for an explicitly authorized account. The HTTP request creates a durable queue record and returns promptly. The independent finite worker processes the record.
5. Read the result, cited announcement IDs and risks. No result changes watchlists, rules, approvals, positions or orders. There is no strategy approval/activation button in this research flow.

The official [DeepSeek retirement notice](https://api-docs.deepseek.com/news/news260424/) states that `deepseek-chat` and `deepseek-reasoner` became inaccessible after July 24, 2026, 15:59 UTC. The adapter accepts the current models and compatibility aliases documented on the [official model page](https://api-docs.deepseek.com/quick_start/pricing/). Unsupported model names or non-official endpoints produce a configuration state; they are not silently replaced.

## Evidence and selection limits

| Boundary | Implemented behavior |
| --- | --- |
| Preference mapping | Versioned `ai → BK0800`, `robotics → BK1090`, `new_energy → BK0493`. Semiconductors and custom topics are retained in preferences but do not invent a board mapping. “Current events” does not enable an unrestricted web search. |
| Themes | Read and verify the latest archived observation known at research start, including all stored members and raw archive hashes. These are observations, not certified historical or current exchange constituents. |
| News | Fixed CNinfo original index and PDF source; at most five first-page announcements per supported topic per collection. JSON and PDF bytes are archived and hashed. PDF bodies are not parsed or supplied to the model. |
| Freshness | Provider-reported publication time and local receipt/knowledge time remain distinct. Research requires a valid publication within seven days, no future publication/receipt, and `known_at <= research_started_at`. Missing timestamps are not replaced with today's date. |
| Candidate universe | Verified theme members intersected with explicit official announcement issuer identity and matching topic. A topical title alone cannot associate an unrelated stock. |
| Model limits | At most 20 input announcements and 10 output candidates; fixed JSON prompt, 4,096 output tokens, tools disabled, one provider HTTP attempt, 90-second total subprocess limit. |
| Output | Candidate references must exist for that exact issuer in the frozen input. Unknown symbols, arbitrary output fields/URLs/code, obvious trading/approval instructions and numerical price/return forecasts fail closed. This structural filtering does not fact-check every generated sentence. |
| Authority | Every record and candidate remains non-executable, with `approval_status=not_submitted`. An issuer announcement is not evidence that the stock benefits. Title-only research cannot claim to have read the PDF body. |

A zero-item source sample, absent verified members, or no valid issuer intersection produces `no_evidence`; it never creates plausible-looking substitute candidates. A completed result may legitimately contain no candidates.

## Durable records, cost and failures

Each account has its own request history and idempotency keys. One request may be queued or started at a time, and at most three new requests may be created per Shanghai calendar day. Replaying the same key returns the original run. Configuration failures and no-evidence requests also count toward that daily request cap.

The run stores frozen preferences, versioned topic mapping, the exact fixed system prompt, evidence snapshot and SHA-256 input hash; the model's raw response and output hash; parsed research, provider-reported token usage, requested model and response-reported model. Cost remains `null`: token counts are not presented as a verified monetary charge. Missing provider usage remains unknown. Private keys, raw provider responses and internal claim tokens are not returned by the public read APIs. Stored input/output hashes and parsed result consistency are rechecked on reads.

States are `queued`, `started`, `needs_model_configuration`, `no_evidence`, `failed` and `completed`. The worker commits a started intent and fencing token before any model request, and commits the evidence snapshot before calling the provider. A 180-second lease prevents stale workers from publishing results. Expired started work becomes `failed/outcome_unknown`; it never returns to the chargeable queue. Timeouts, blank/truncated JSON, malformed responses or unknown outcomes are not automatically retried. A new human request is a new bounded request, not proof that the previous provider call was uncharged.

The generic LLM call path returns only generated text and contains retry/fallback paths. The research adapter therefore reuses `LLMService(provider='deepseek')` configuration but sends one restricted official-endpoint HTTP request in a subprocess. This preserves raw responses and actual usage without changing global model settings. It follows the official [JSON output contract](https://api-docs.deepseek.com/guides/json_mode/) and [Chat Completions API](https://api-docs.deepseek.com/api/create-chat-completion/).

## Operator commands

Run from the repository root after deploying the corresponding application image and migrations. `STATE` is the existing private deployment directory outside the checkout; use the tenant/account actually authorized by the user. Examples contain placeholders, not account discovery instructions.

```bash
python scripts/ops/research_jobs.py --state-dir "$STATE" news
python scripts/ops/research_jobs.py --state-dir "$STATE" request \
  --tenant-id "$AUTHORIZED_TENANT" --account-id paper \
  --idempotency-key human-research-20261118-01
python scripts/ops/research_jobs.py --state-dir "$STATE" work
```

`news` collects once; `request` only enqueues; `work` processes at most one global queued record. The jobs reuse the deployment's operation lock and Compose configuration. They launch uniquely named disposable worker containers with 90/30/150-second limits respectively, followed by bounded cleanup. They do not start or stop the normal trading worker. A cleanup failure writes `.research-job-cleanup-unknown.json` in the private state directory and blocks later jobs until an operator verifies/removes the named leftover container and deliberately clears the marker. Do not clear it merely to force another attempt.

Generate reviewable user-systemd unit files without installing or starting anything:

```bash
python scripts/ops/research_jobs.py --state-dir "$STATE" templates \
  --tenant-id "$AUTHORIZED_TENANT" --account-id paper \
  --output-dir "$REVIEWED_UNIT_DIRECTORY"
```

The six files define news collection every 30 minutes from 07:00 through 22:30 Asia/Shanghai, one worker invocation every two minutes, and a request for the explicit account at 08:30 daily. The request uses `scheduled-YYYYMMDD` as its per-account idempotency key; before 08:30 it skips without querying or calling a model. When scheduled services start together, the units order collection, enqueue and queue processing. The research jobs wait up to 15 seconds for the shared operation lock, then attempt the underlying job once after acquiring it. Templates set `Restart=no`. Review the account, paths and schedules before copying them into `~/.config/systemd/user/`, running `systemctl --user daemon-reload`, and enabling the selected `.timer` units. Generating templates does not authorize or prove installation. The research schedule does not approve strategies or enable automatic PAPER orders.

For an already configured backend environment, the finite module commands are also available:

```bash
python -m app.ashare.research request --tenant-id "$AUTHORIZED_TENANT" \
  --account-id paper --idempotency-key human-research-20261118-01
python -m app.ashare.research run-once
```

## Remaining work

Broader original industry-news text, full-document understanding, stronger factual verification, immutable executable strategy versions, explicit human approval of a specific version, and automatic PAPER execution of that approved version remain separate deliverables. The current research record grants none of those permissions. Live brokerage connectivity and automatic AI strategy promotion remain prohibited.

[Local actual acceptance](NEWS_RESEARCH_ACCEPTANCE.md): real announcement archiving, one DeepSeek call and browser presentation are verified; natural scheduled execution is tracked separately.
