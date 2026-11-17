# Jev、Laya 与 DeepSeek 接入评估

[English](DECISION_PROVIDERS.md) · [使用教程](../USER_GUIDE_CN.md)

核查日期：2026-11-17。这是资料与源码评估，不是已安装或验收的模型能力。Laya 固定检查版本为 [`b1d656952f0f9581ea691a70cb6f38abf3eb13a9`](https://github.com/NandhaKishorM/laya/tree/b1d656952f0f9581ea691a70cb6f38abf3eb13a9)。本次没有执行第三方代码、安装依赖、下载权重、调用收费模型或发送账户数据。

## 是否还要申请 Jev Key

当前 A 股人工模拟闭环不需要 Jev、Laya 或 DeepSeek。后续可以选择 **DeepSeek 负责研究、Laya 负责本地分类**，从而不依赖收费 Jev 服务；二者仍是不同组件。Laya 使用自己的本地模型权重，不是 DeepSeek API 的转接工具。[Laya 模型说明](https://huggingface.co/convaiinnovations/laya/blob/main/README.md)

DeepSeek 的生成式接口可承担研究说明和策略建议；使用其托管 API 需要自己的 DeepSeek Key。官方当前提供兼容 OpenAI 的接口，地址为 `https://api.deepseek.com`；具体模型以配置当日官方文档/控制台为准。本轮没有配置或发送用户 Key，也没有把研究建议接到模拟成交。[DeepSeek 官方接入说明](https://api-docs.deepseek.com/)

| 组件 | 职责 | 凭据和成本 | 当前项目状态 |
| --- | --- | --- | --- |
| A 股 PaperBroker | 数据、虚拟账本及确定性规则 | 无模型 Key；行情接口可有可用性限制 | 已有运行闭环 |
| DeepSeek API | 生成式研究和方案建议 | DeepSeek Key，按服务计费 | 本轮未做真实模型验收 |
| Laya 本地服务 | 给定文本和有限选项，返回分类及概率 | 本地算力、模型下载；可自设本地访问令牌 | 尚未集成或部署 |
| Jev 托管服务 | 上游可选的结构化决策服务 | Jev Key；当前模拟闭环无需申请 | 不在当前模拟 worker 中运行 |

## PR #358 尚未实现 Laya 接入

截至核查时，PR #358 仍为 Open，仅抽取 `DecisionProvider` 接口和 Jev 适配器；Laya、配置选择及 Compose 服务明确留待后续。Issue #266 是接入提议；维护者同时提醒概率与置信度语义需处理。不能将这两个链接当作当前已支持 Laya 的证据。[PR #358](https://github.com/OpenByteInc/QuantDinger/pull/358) · [Issue #266](https://github.com/OpenByteInc/QuantDinger/issues/266)

上游交易前过滤器有 LLM 回退及全部 AI 不可用时放行的旧策略。这属于上游行为，不应直接迁入本项目的授权边界。[维护者说明](https://github.com/OpenByteInc/QuantDinger/issues/266#issuecomment-5875864959)

## 当前 Laya 接口与兼容性

固定版本的正式 `laya-serve` 提供 `POST /v1/systemone`、批量接口和健康检查；`examples/server.py` 的 `/predict` 是另一套试用界面。正式接口支持 Jev 风格的 `state` 和 `questions`，另有 `LAYA_JEV_STRICT` 响应兼容开关。这说明传输适配可复用部分结构，**不代表更换地址便已完成可靠替代**。[HTTP 接口](https://github.com/NandhaKishorM/laya/blob/b1d656952f0f9581ea691a70cb6f38abf3eb13a9/docs/http-api.md)

Laya 的 `confidence`、`answer_confidence` 与 Jev 置信度不是同一度量。其主文档也明确提示出厂过度自信、阈值需要自己的留出数据验证；不能直接沿用 Jev 默认阈值。[置信度与局限](https://github.com/NandhaKishorM/laya/blob/b1d656952f0f9581ea691a70cb6f38abf3eb13a9/README.md#automated-confidence-gating) · [校准实现](https://github.com/NandhaKishorM/laya/blob/b1d656952f0f9581ea691a70cb6f38abf3eb13a9/laya/calibrate.py)

## 可靠性结论：可隔离评测，暂不能当交易批准器

代码元数据声明 Beta、Python ≥3.10，版本 0.4.35；核心依赖包括 PyTorch、Transformers、Hugging Face、vegaml，服务额外依赖 FastAPI/Uvicorn。应与现有模拟核心隔离，锁定依赖和模型版本，避免直接扩充核心运行环境。[依赖元数据](https://github.com/NandhaKishorM/laya/blob/b1d656952f0f9581ea691a70cb6f38abf3eb13a9/pyproject.toml)

仓库有安全策略、校验与测试，但 `SECURITY.md` 的受支持版本表仍写 0.3.x 为当前线，与元数据 0.4.35 不一致，需在采用前复核。默认监听 `0.0.0.0` 且未设 `LAYA_API_KEY` 时不鉴权；本项目若采用，将仅在受限本地网络提供服务、自设访问令牌、固定权重与摘要，不发布公共端口。这个令牌不是收费 Jev Key。[安全策略](https://github.com/NandhaKishorM/laya/blob/b1d656952f0f9581ea691a70cb6f38abf3eb13a9/SECURITY.md) · [权重固定与完整性](https://github.com/NandhaKishorM/laya/blob/b1d656952f0f9581ea691a70cb6f38abf3eb13a9/docs/security.md)

代码为 Apache-2.0，模型卡也标注 Apache-2.0；实际选用的模型、可选引擎和依赖仍须按固定版本分别核查，不能从代码许可证推断整套依赖的许可。[代码许可证](https://github.com/NandhaKishorM/laya/blob/b1d656952f0f9581ea691a70cb6f38abf3eb13a9/LICENSE) · [模型卡](https://huggingface.co/convaiinnovations/laya/blob/main/README.md)

项目保留的中文合成工作流诊断显示特定历史版本可出现较低准确率；另一套 18 条中文短指令夹具也显示高置信错误。它们不是当前 A 股效果测试，既不能证明新版本不行，也不能证明可用。[中文工作流诊断](https://github.com/NandhaKishorM/laya/blob/b1d656952f0f9581ea691a70cb6f38abf3eb13a9/research/benchmarks/feishu_zh/README.zh-CN.md) · [短指令诊断](https://github.com/NandhaKishorM/laya/blob/b1d656952f0f9581ea691a70cb6f38abf3eb13a9/research/benchmarks/zh_short_commands/README.zh-CN.md)

## 后续实现验收门槛

以下是本项目建议的工程门槛，尚未完成：

1. 独立的只读研究/分类适配器，保留 provider、模型/权重摘要、问题版本、原始输出、来源与耗时；未知字段和超时有明确拒绝结果。
2. 固定中文 A 股任务与独立时间留出集，覆盖否定、引用、过期新闻、矛盾来源和缺失数据；报告逐类误差、概率校准、覆盖率及高置信错误，禁止以训练集替代留出测试。
3. 先以影子模式记录建议，与规则和人工决策比较，不影响成交；测量本机资源、重启、超时、并发和断网表现。
4. 验收通过后，研究与优化结果仍需人工批准；获批策略可在批准范围内自动模拟执行。模型分类不得自动批准证券状态、晋级策略或连接实盘。

本轮判断是“有条件适合下一阶段隔离验证”，不是“已经证明可靠、可以直接替换”。
