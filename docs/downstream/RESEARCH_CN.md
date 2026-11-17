# 有证据边界的 A 股研究

[English](RESEARCH.md) · [用户教程](USER_GUIDE_CN.md) · [资讯证据](research/NEWS_SOURCES_CN.md)

本版本实现了独立研究队列和 A 股页面研究面板：读取已归档主题成分与巨潮公司公告标题的有限样本，再调用用户明确配置的 DeepSeek 生成研究候选。它尚未完成“行业全文资讯 → 可执行策略 → 人工批准后自动 PAPER”的整条流程。部署、真实模型请求、自然定时执行分别需要实际验收；代码实现及离线测试不能证明这些步骤已经成功。

## 用户怎样使用

1. 保存账户研究偏好。保存本身不会启动采集、模型调用或交易。
2. 在模型设置中私下保存 DeepSeek 密钥，并选择受支持模型，例如 `deepseek-flash`。不要把密钥粘贴到聊天。研究适配器读取已有服务配置，不会修改用户全局默认服务商或模型。
3. 打开资讯研究面板，查看来源、发布时间、接收时间、哈希及“仅公告标题”说明。刷新只读取已有记录，不采集也不调用模型。
4. 主动请求一次研究，或为明确授权的账户另行部署研究日程。HTTP 请求先持久化排队并及时返回，独立有限 worker 再处理。
5. 查看候选、引用公告 ID 与风险。任何结果都不会修改关注列表、规则、审批、持仓或委托。当前研究流程不提供策略审批或激活按钮。

DeepSeek [官方退役公告](https://api-docs.deepseek.com/news/news260424/)说明，`deepseek-chat` 和 `deepseek-reasoner` 已在 2026 年 7 月 24 日 15:59 UTC 后停止访问。适配器接受[官方模型页](https://api-docs.deepseek.com/quick_start/pricing/)所列当前模型及仍受支持的兼容别名。旧模型名或非官方端点会进入配置缺失状态，不会被程序悄悄替换。

## 输入和候选范围

| 边界 | 本版本实际行为 |
| --- | --- |
| 偏好映射 | 版本化保存 `ai → BK0800`、`robotics → BK1090`、`new_energy → BK0493`。半导体、自定义方向仍保留原偏好，但不会猜测对应板块。“跟随热点”不开放任意网页检索。 |
| 主题 | 读取研究开始时已经知道的最新归档，验证完整成员和原始归档哈希。它们是采集观测，不是认证历史成分或交易所实时名单。 |
| 公告 | 固定巨潮原站索引和 PDF，每次每个受支持主题最多取第一页五条，归档 JSON/PDF 并保留哈希。不解析 PDF 正文，也不把正文提供给模型。 |
| 新鲜度 | 供应商发布时间与本机接收/获知时间分开保存。研究只使用七天内有效发布时间、无未来发布时间/接收时间且 `known_at <= research_started_at` 的记录；不把缺日期替换为今天。 |
| 候选范围 | 已验证主题成员、公告明确发行人身份以及匹配主题三者的交集。仅标题与主题相关，不能据此把无关联股票加入候选。 |
| 模型限制 | 最多输入 20 条公告、输出 10 个候选；固定 JSON 提示词、4,096 输出 token、禁用工具、只发一次 HTTP 请求，子进程总时限 90 秒。 |
| 输出检查 | 引用必须存在于该发行人的冻结输入中。新股票代码、任意额外字段/URL/代码、明显交易或审批指令、数值价格或收益预测均拒绝。结构过滤不能证明每句话事实正确。 |
| 权限 | 每个研究记录和候选始终不可执行，`approval_status=not_submitted`。发行人公告不等于该股票受益证据；仅标题研究不能声称已读 PDF 全文。 |

来源样本零命中、缺少已验证成员、没有有效发行人交集时，结果是 `no_evidence`，不会补造看似合理的候选。`completed` 也可以合法返回零个候选。

## 持久化、费用和失败处理

每个账户独立保存研究历史与幂等键；同一时间最多一条排队或执行中记录，上海日历每天最多创建三条新请求。同一个幂等键重复提交只返回原记录。配置缺失和无证据请求也计入每天三条的限制。

每次研究保存冻结偏好、版本化主题映射、完整固定系统提示词、证据快照及 SHA-256 输入哈希；保存模型原始响应、输出哈希、解析后的研究结果、服务商实际返回的 token 用量、请求模型和响应声明模型。费用保持 `null`：token 数不冒充已核实的人民币或美元扣费；缺失用量仍为未知。公开读取接口不返回私钥、模型原始响应或内部认领令牌。读取时重新校验输入/输出哈希以及解析结果与候选/摘要/风险的一致性。

状态包括 `queued`、`started`、`needs_model_configuration`、`no_evidence`、`failed`、`completed`。worker 在模型请求前提交 started 意图和防并发令牌，并先持久化完整输入。180 秒租约限制过期 worker 回写。租约过期的 started 记录变成 `failed/outcome_unknown`，不会重新进入可收费队列。超时、空白/截断 JSON、格式错误和未知结果都不会自动重试。用户重新发起是新的有限请求，不能证明上一次服务商没有收费。

通用 LLM 调用只返回生成文字，且有重试/备用模型路径。因此研究适配器复用 `LLMService(provider='deepseek')` 的配置读取，在独立子进程中仅向官方端点发一次受限 HTTP 请求，保留原始响应和真实用量，不修改全局设置。请求遵循官方 [JSON 输出约束](https://api-docs.deepseek.com/guides/json_mode/)和 [Chat Completions API](https://api-docs.deepseek.com/api/create-chat-completion/)。

## 运维命令

先部署相应应用镜像和数据库迁移，再从仓库根目录运行。`STATE` 是仓库外已有的私有部署目录；必须使用用户实际授权的租户和账户。下方变量是占位符，不应猜测或自动遍历所有用户。

```bash
python scripts/ops/research_jobs.py --state-dir "$STATE" news
python scripts/ops/research_jobs.py --state-dir "$STATE" request \
  --tenant-id "$AUTHORIZED_TENANT" --account-id paper \
  --idempotency-key human-research-20261118-01
python scripts/ops/research_jobs.py --state-dir "$STATE" work
```

`news` 采集一次，`request` 只排队，`work` 最多处理一条全局排队记录。任务复用部署操作锁和 Compose 配置，启动独立唯一名称的临时 worker 容器，对应总时限为 90/30/150 秒，并执行有界清理，不启停常驻交易 worker。若无法确认容器清理成功，会在私有目录写入 `.research-job-cleanup-unknown.json` 并阻止后续任务。运维人员应核实并移除其中指定的遗留容器，再明确清除标记；不要仅为强制重试而删除它。

生成可审查的用户级 systemd 单元文件，不安装也不启动：

```bash
python scripts/ops/research_jobs.py --state-dir "$STATE" templates \
  --tenant-id "$AUTHORIZED_TENANT" --account-id paper \
  --output-dir "$REVIEWED_UNIT_DIRECTORY"
```

六个文件分别定义：上海时间 07:00 至 22:30 每半小时一次资讯采集、每两分钟处理一次队列、明确账户每天 08:30 请求研究。每日请求使用账户内固定幂等键 `scheduled-YYYYMMDD`；08:30 前跳过，不查数据库也不调用模型。服务设置 `Restart=no`。审查账户、路径和日程后，才将文件复制到 `~/.config/systemd/user/`、执行 `systemctl --user daemon-reload` 并启用选择的 `.timer`。生成模板不代表授权安装或已经安装成功；研究日程不会审批策略或开启自动 PAPER 委托。

已有后端运行环境也可使用有限模块命令：

```bash
python -m app.ashare.research request --tenant-id "$AUTHORIZED_TENANT" \
  --account-id paper --idempotency-key human-research-20261118-01
python -m app.ashare.research run-once
```

## 尚未完成的完整流程

更广泛的原站行业全文资讯、全文理解、更强的事实核验、不可变可执行策略版本、用户批准具体版本，以及只执行获批版本的自动 PAPER，仍是各自独立的待办。当前研究记录不授予上述权限。实盘券商连接和 AI 自动晋级策略继续禁止。

[本机真实验收结果](NEWS_RESEARCH_ACCEPTANCE_CN.md)：已验证真实公告归档、一次 DeepSeek 请求及页面展示；自然定时执行按报告单列。
