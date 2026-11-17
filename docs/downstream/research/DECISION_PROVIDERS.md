# Jev, Laya and DeepSeek assessment

[中文](DECISION_PROVIDERS_CN.md) · [User guide](../USER_GUIDE.md)

Reviewed 2026-11-17. Laya source revision: [`b1d656952f0f9581ea691a70cb6f38abf3eb13a9`](https://github.com/NandhaKishorM/laya/tree/b1d656952f0f9581ea691a70cb6f38abf3eb13a9). This was source/document inspection: no third-party code execution, installation, model downloads, paid inference or account-data transmission.

## Keys and responsibilities

The current human-operated A-share paper loop requires none of Jev, Laya or DeepSeek. A future arrangement could use DeepSeek for generative research and Laya for local classification, avoiding the hosted Jev dependency. Laya uses its own checkpoints; it is not an API proxy backed by DeepSeek. [Model documentation](https://huggingface.co/convaiinnovations/laya/blob/main/README.md)

DeepSeek's hosted API requires a DeepSeek key. Its OpenAI-compatible base URL is `https://api.deepseek.com`; select a supported model from current official documentation. This iteration did not configure a key or connect model suggestions to fills. [DeepSeek API](https://api-docs.deepseek.com/)

| Component | Responsibility | Current status |
| --- | --- | --- |
| PaperBroker | Virtual ledger and deterministic rules | Running; no model key required |
| DeepSeek | Generative research and proposals | No real-model acceptance in this iteration |
| Laya | Local typed classification | Not integrated or deployed; requires compute and checkpoints |
| Jev | Optional upstream hosted decision service | Not running in the paper worker; no key needed for this loop |

## Upstream proposal and current Laya transport

PR #358 remains open. It extracts a provider protocol and Jev adapter; Laya, selection settings and Compose integration are explicitly deferred. Issue #266 remains a proposal. The upstream filter's legacy all-AI-unavailable fail-open policy must not become this project's authorization policy. [PR #358](https://github.com/OpenByteInc/QuantDinger/pull/358) · [Maintainer explanation](https://github.com/OpenByteInc/QuantDinger/issues/266#issuecomment-5875864959)

Current production `laya-serve` offers Jev-style `POST /v1/systemone`, batching and health, plus `LAYA_JEV_STRICT`. The playground's `/predict` is a separate example surface. Some wire structure can therefore be reused, but changing a base URL is not a validated replacement. [HTTP contract](https://github.com/NandhaKishorM/laya/blob/b1d656952f0f9581ea691a70cb6f38abf3eb13a9/docs/http-api.md)

Laya `confidence`, `answer_confidence` and Jev confidence differ. Laya documents overconfidence and the need for workload-specific held-out calibration; an existing Jev cutoff cannot be carried over. [Confidence limitations](https://github.com/NandhaKishorM/laya/blob/b1d656952f0f9581ea691a70cb6f38abf3eb13a9/README.md#automated-confidence-gating) · [Calibration code](https://github.com/NandhaKishorM/laya/blob/b1d656952f0f9581ea691a70cb6f38abf3eb13a9/laya/calibrate.py)

## Reliability and deployment assessment

Metadata declares Beta, version 0.4.35, Python ≥3.10, with PyTorch, Transformers, Hugging Face and vegaml dependencies; serving adds FastAPI/Uvicorn. Isolate and lock these dependencies rather than expanding the paper runtime directly. [Package metadata](https://github.com/NandhaKishorM/laya/blob/b1d656952f0f9581ea691a70cb6f38abf3eb13a9/pyproject.toml)

There is a security policy and supporting checks, but its supported-version table still calls 0.3.x current, inconsistent with package metadata. The server defaults to `0.0.0.0`, without authentication when `LAYA_API_KEY` is absent. Any later deployment here should use restricted local networking, an operator-generated bearer, pinned model revisions and digests. That local token is not a paid Jev key. [Security policy](https://github.com/NandhaKishorM/laya/blob/b1d656952f0f9581ea691a70cb6f38abf3eb13a9/SECURITY.md) · [Artifact integrity](https://github.com/NandhaKishorM/laya/blob/b1d656952f0f9581ea691a70cb6f38abf3eb13a9/docs/security.md)

Code and the reviewed model card identify Apache-2.0; check each actually selected model, optional engine and dependency separately. [Code license](https://github.com/NandhaKishorM/laya/blob/b1d656952f0f9581ea691a70cb6f38abf3eb13a9/LICENSE) · [Model card](https://huggingface.co/convaiinnovations/laya/blob/main/README.md)

Committed Chinese diagnostics include poor results on some historical synthetic workflows and high-confidence errors on short-command fixtures. Neither measures today's A-share workload or establishes current-version trading reliability. [Chinese workflow diagnostic](https://github.com/NandhaKishorM/laya/blob/b1d656952f0f9581ea691a70cb6f38abf3eb13a9/research/benchmarks/feishu_zh/README.zh-CN.md) · [Short-command diagnostic](https://github.com/NandhaKishorM/laya/blob/b1d656952f0f9581ea691a70cb6f38abf3eb13a9/research/benchmarks/zh_short_commands/README.zh-CN.md)

## Proposed acceptance gates

These are this project's proposed engineering gates, not completed work:

1. An isolated read-only adapter records provider/model/artifact identities, question versions, evidence, raw output and latency; unknown output and timeouts have explicit rejection results.
2. Fix Chinese A-share tasks and independent temporal holdouts covering negation, quoted material, stale news, conflicting sources and missing evidence. Report class errors, calibration, coverage and confident failures.
3. Start in shadow mode with no fill effects. Compare with rules and human decisions; measure local resource usage, restarts, timeouts, concurrency and offline behavior.
4. Human approval remains necessary for research and optimization proposals. Only approved paper strategies may execute within approved scope; classifiers must not approve security status, promote strategies or connect live brokers.

Assessment: a reasonable candidate for isolated evaluation, without an established direct-replacement reliability claim.
