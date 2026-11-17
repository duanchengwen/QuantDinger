> 历史调研记录（2026-11-17）：本文保留原始评估上下文，“本仓库”及本地探针路径可能指已归档的独立项目。它不代表当前 Fork 已集成全部功能；固定提交链接仍用于核对当时版本。原始报告、探针及证据保存于本机 `outputs/legacy-archive-20261117/` 的归档中。

# QuantDinger 平台边界独立审阅

核验日期：2026-11-17。后端固定 commit `79caf19b056e4bf04338e3d87c7d70fe2b4a932d`（本地 v5.6.1）；前端独立仓库 HEAD 固定为 `2ccc70b208141b3898060c4888853f83111ecfd8`。目标是 A 股真实盘中行情、资讯和虚拟资金，当前禁实盘、禁 AI 自动晋级；未来 Windows 券商另行处理。

## 结论

**不宜按默认配置直接验收为目标系统；可作为待改造的自托管研究/策略工作台。** 其 Agent 的“创建后停止”确有代码约束，不能误称已存在自动启动策略；但 W 权限仍可更改部署配置，`paper_only` 也不是禁止自动修改策略的权限。行情/新闻缺少满足严格盘中时效和历史可得性验收的证据；实盘总开关覆盖不完整；A 股实盘执行市场被明确拒绝。后端 Apache 2.0 不等于前端同许可。以下各节区分源码事实、局部实测与判断。

## 1. AI / Agent 是否会自动创建、启动、晋级

- **能创建源码和部署。** Agent `POST /strategies` 只需 W scope，调用 StrategyService；SQL INSERT 固定 `status='stopped'`，并非信任客户端传来的状态。Agent 路由没有 `/start`；启动接口在登录用户 Web API 中。MCP 的 create 方法也明确创建 stopped 部署。依据：[Agent strategies](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/backend_api_python/app/routes/agent_v1/strategies.py)、[Deployment save / SQL](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/backend_api_python/app/services/strategy_v2/deployment.py#L27-L369)、[MCP create_strategy](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/mcp_server/src/quantdinger_mcp/server.py#L630-L676)、[Web start_strategy](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/backend_api_python/app/routes/strategy.py#L250-L282)。
- **不能通过 create/update 的 status 字段直接启动。** `_deployment_payload` 白名单没有 status，未知字段直接报错。纯函数探针传 `{'name':'x','status':'running'}` 得到 `ValueError: strategyV2.unsupportedFields`。这比工具描述里的“必须人工启动”更强，是实际校验。依据：[payload 白名单](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/backend_api_python/app/services/strategy.py#L528-L542)。
- **但 W 不是“仅新建隔离草稿”。** PATCH 允许 `sourceId`、`params`、`executionMode`、`credentialId` 等；`update_strategy` 没有要求现有策略 stopped，UPDATE SQL 不重置 status。该路径不读取 token 的 paper_only / AGENT_LIVE_TRADING_ENABLED。纯函数探针确认 `executionMode='live',credentialId=7` 能通过字段白名单；后续仍受全局 live 开关、合法凭据和支持市场校验，不能据此说已成功实盘。对于已运行部署，这意味着不能把 W 权限解释成“永不改变已批准运行配置”。运行进程何时加载新配置未做动态验证。依据：[update_strategy](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/backend_api_python/app/services/strategy.py#L264-L307)、[Deployment save / SQL](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/backend_api_python/app/services/strategy_v2/deployment.py#L27-L369)、[live market whitelist](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/backend_api_python/app/services/strategy_v2/deployment.py#L442-L459)。
- **paper_only 约束的是 Agent 快捷订单的分流。** 本次扫描其消费点为 Agent quick order 路径，而不是统一策略晋级审批。工具注册文本说明停止创建、需要确认，但提示词不是独立审批状态机。未在本次审阅路径发现研究→候选→人工签发→运行的不可绕过晋级票据。依据：[Agent order gates](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/backend_api_python/app/routes/agent_v1/quick_trade.py#L476-L522)、[AI tools registry](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/backend_api_python/app/services/ai_tool_registry.py#L319-L347)。

建议：仅研究期关闭 Agent Gateway，或只发 R；若需要 AI 生成代码，用隔离草稿产物交由人审，不直接给运行账户 W/T。要允许 W，应另加“仅 stopped、不可改运行部署、不可改 executionMode/credential、批准后才复制到运行区”的硬边界。外部 Agent 也不要持有人类登录令牌或数据库写权限。这是针对目标要求的改造建议，不是项目已实现功能。

## 2. 如何禁实盘：必须区分局部 gate 与全局熔断

| 配置 / 路径 | 核验结果 |
|---|---|
| `LIVE_TRADING_ENABLED` | `.env.example` 为 true，代码缺省也 true；save(live) 和人类 start(live) 检查它。 |
| `AGENT_LIVE_TRADING_ENABLED` | 示例 false；Agent 非 paper 快捷订单同时要求本开关和 LIVE 开关为真。 |
| Agent token `paper_only` | 默认 true，决定 Agent quick order 用模拟订单还是 live 分支。 |
| `BROKER_ACCOUNTS_ENABLED` | 本次全 app 搜索只找到能力输出 / 设置定义，没有订单提交保护。不能当断路器。 |
| `AGENT_GATEWAY_ENABLED=false` | Agent blueprint 的 before_request 直接阻止网关请求，是实际入口 gate。 |
| 人类 `/quick-trade/place-order` | 未检查上述 LIVE 开关；代码可调用实际交易执行。 |
| worker / executor preflight | `_preflight_live_strategy` 检查凭据、产品、冲突等，没有读 LIVE 开关。不能推断关开关会停止存量运行任务或撤单。 |

依据：[env defaults](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/.env.example)、[feature defaults](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/backend_api_python/app/services/settings/capabilities.py)、[Deployment save / SQL](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/backend_api_python/app/services/strategy_v2/deployment.py#L27-L369)、[Web start_strategy](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/backend_api_python/app/routes/strategy.py#L250-L282)、[Agent order gates](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/backend_api_python/app/routes/agent_v1/quick_trade.py#L476-L522)、[Human quick-trade order](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/backend_api_python/app/routes/quick_trade.py#L219-L490)、[Executor preflight](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/backend_api_python/app/services/trading_executor.py#L266-L310)、[Gateway before_request](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/backend_api_python/app/routes/agent_v1/__init__.py)。

**当前安全基线应至少：** 两个 live flag 均 false；不导入任何券商/交易所下单凭据；不连接下单网关；确认无既有 live 策略/挂单；Agent Gateway 关闭或 R-only。若要达到“任何路径都不能实盘”的产品验收，仍需订单执行边界统一硬拦截及回归测试。本文没有连接券商、下单或实测绕过，以上是调用链缺口，不是成功漏洞利用报告。

A 股未来接券商也不是简单改配置：部署校验 live 只接受 Crypto、USStock、HKStock，CNStock 会报 `strategyV2.liveMarketUnsupported`。Windows 文档证明能部署服务端，不证明有中国券商客户端桥接。依据：[live market whitelist](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/backend_api_python/app/services/strategy_v2/deployment.py#L442-L459)、[Windows installation](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/docs/deployment/WINDOWS_INSTALLATION.md)。

## 3. 行情来源、延迟与前视边界

CNStock ticker 固定从腾讯 `qt.gtimg.cn` HTTP 请求获取，失败返回 `last=0`。返回字段为 last/change/changePercent/high/low/open/previousClose/name/symbol；解析器虽有 raw，CNStock 返回时丢掉 raw，未输出上游报价时间、买卖盘口、成交量、涨跌停价、停牌状态或 provider 身份。不能区分“刚收到的陈旧行情”和新行情；请求成功不能证明可成交或新鲜。**未实测供应商延迟，不给出秒级/分钟级时效承诺。** 依据：[CNStock source](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/backend_api_python/app/data_sources/cn_stock.py)、[Tencent quote parser](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/backend_api_python/app/data_sources/tencent.py#L75-L147)。

K 线优先 Twelve Data，其次日/周腾讯，再 yfinance，最后部分周期 AkShare；前者需要 key。CNStock 调腾讯日周线明确 `adj='qfq'`；返回 bar 主要是时间和 OHLCV，缺少逐条来源/可得时间。多源 fallback 有可用性价值，但无法自动证明不同源的复权、时间戳和历史口径一致。`before_time/after_time` 是时间过滤，不足以证明 point-in-time 数据版本、历史修订、成分股或当时可得资讯。前复权历史数据还需验证回测时的复权基准是否使用了未来企业行动。这里只指出验收边界，未断言所有回测已发生前视。依据：[CNStock source](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/backend_api_python/app/data_sources/cn_stock.py)、[Asia K-line adapters](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/backend_api_python/app/data_sources/asia_stock_kline.py)。

目标系统应记录 provider、exchange_time、received_at、交易日、复权模式、错误/陈旧原因，并对不新鲜行情拒绝新开模拟仓；A 股盘口、涨跌停、停牌与规则真实性由执行审阅另行覆盖。

## 4. 新闻不是 A 股盘中事件流，且存在时间参数问题

`fetch_financial_news` 使用通用搜索聚合，中文查询是加密货币、美联储、美股、外汇、全球经济、期货；没有在此路径中接入 A 股交易所公告专线。每个查询取 5 条，按 link 去重后每种语言最多 15 条。`/global-market/news` 缓存 TTL=180 秒。来源为 Tavily、SearXNG、GDELT、SerpAPI、Alpha Vantage、Google、DuckDuckGo 等配置后的 fallback；这是可检索上下文，不能自动等同公告原始发布时间或完整盘中快讯流。依据：[News aggregation](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/backend_api_python/app/data_providers/news.py)、[News cache](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/backend_api_python/app/routes/global_market.py#L91-L107)、[Search providers](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/backend_api_python/app/services/search.py#L679-L726)。

**已用无外网纯函数探针确认：** news 调用 `search(..., date_restrict='d1')`，而方法形参 `days=7`；只有 `not days` 才解析 date_restrict。所以实际向下游传 7 天；显式 days=0 才变为 1 天。另 Tavily REST fallback 的 payload 没有 days 限制。不能把当前结果标作“可靠最近 24 小时新闻”。依据：[Search date handling](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/backend_api_python/app/services/search.py#L767-L791)、[Tavily REST request](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/backend_api_python/app/services/search.py#L84-L101)。

结果的 published 字段可为空，SearchResult 没有 observed_at / ingested_at / available_at；GDELT 用 seendate 写入 published_date，属于不同时间语义。当前搜索接口也没有历史 as-of 截止和快照重放协议。因此若把“今日搜索出来的文章”喂给历史回测，需要另建可得时间冻结和原文来源追踪，否则存在资讯前视风险。不能据此否定其他因子模块的 point-in-time 功能；此结论只针对本次核验的新闻链路。依据：[News result model](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/backend_api_python/app/services/search_models.py#L13-L68)、[GDELT time mapping](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/backend_api_python/app/services/search.py#L357-L390)。

## 5. 前端独立许可及自托管完整性

后端 LICENSE 是 Apache 2.0；README 明示 Web、Mobile 另属 source-available。独立前端的 [LICENSE（固定 commit）](https://github.com/OpenByteInc/QuantDinger-Vue/blob/2ccc70b208141b3898060c4888853f83111ecfd8/LICENSE) 定义了非商业学习、研究、内部评估、实验等免费使用；一般商业用途需要另行许可，商业定义含私人金钱收益；要求保留品牌、水印、版权和许可。**因此不能称整套前后端均 Apache/完全自由开源。** 当前虚拟资金研究有明确非商业条款可对照；未来以个人实盘获利为目的不能仅凭“个人使用”认定免费，应取得权利人明确许可说明。这里转述许可证，不对具体用户做法律定性。依据：[Backend LICENSE](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/LICENSE)、[README license terms](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/README.md#L837-L852)。

运行层面可以自托管应用服务：默认 backend 本地构建、frontend/mobile 拉 GHCR 预构建镜像；另有 build override 从独立 Vue 和 Mobile 仓库本地构建。构建 override 同时涉及 mobile，不能假定只 clone Vue 就满足整份 override。默认镜像标签回退 latest，需要固定匹配版本；此审阅不证明前端上述 HEAD 与后端 v5.6.1 已通过兼容测试。依据：[Compose topology](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/docker-compose.yml)、[Local frontend/mobile build override](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/docker-compose.build.yml)。

默认 Compose 不是轻量单进程程序：PostgreSQL 18.3、Redis 缓存、独立持久化 Redis jobs、Kafka、初始化/迁移、Flask API、交易/审计/分发/评估/scheduler workers、Celery AI/maintenance 与独立 backtest worker、beat、前端、Mobile，另有 MCP 服务定义。Windows 依赖 Docker Desktop+WSL2，官方建议至少 8GB RAM/20GB 磁盘，并发 AI/回测建议16GB。由此判断部署及运维复杂度为中高，需要数据库/队列备份、进程健康、镜像版本管理；“一键安装”不等于无运维。依据：[Compose topology](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/docker-compose.yml)、[Windows installation](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/docs/deployment/WINDOWS_INSTALLATION.md)。

**自托管不等于完全离线：** 应用/持久化可本地，行情和新闻依赖外部源，LLM依配置使用外部或自建兼容服务；免费源不代表有数据授权或实时 SLA。官方集成文档也明确公共数据可能延迟、缺失或受许可限制。依据：[Integration data caveats](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/docs/deployment/INTEGRATION_GUIDE.md)、[CNStock source](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/backend_api_python/app/data_sources/cn_stock.py)、[Search providers](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/backend_api_python/app/services/search.py#L679-L726)。

## 6. 核验方法与未覆盖范围

已做：只读源码/文档核验、全 app 定向搜索、固定前端 HEAD 和官方 LICENSE 读取；AST 提取原函数的无服务探针（search 日期参数、部署 payload 白名单），没有导入应用、初始化数据库或调用外网行情服务。探针结果如上，可由同 commit 重现；没有修改第三方源码，本次读取后的 git status 为空。

未做：Docker部署、安装脚本、依赖安装、端到端UI、供应商延迟压测、券商连接、任何真实订单；未审完整前端构建链/所有第三方依赖许可证，未确认全部 worker 热加载语义。本文是平台选型和改造边界审阅，不能充当实盘安全认证；虚拟成交与A股交易规则以另份执行审阅为准。
