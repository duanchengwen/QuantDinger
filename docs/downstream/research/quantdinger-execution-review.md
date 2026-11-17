> 历史调研记录（2026-11-17）：本文保留原始评估上下文，“本仓库”及本地探针路径可能指已归档的独立项目。它不代表当前 Fork 已集成全部功能；固定提交链接仍用于核对当时版本。原始报告、探针及证据保存于本机 `outputs/legacy-archive-20261117/` 的归档中。

# QuantDinger 执行与虚拟账户专项核查

日期：2026-11-17。固定版本：[79caf19b056e4bf04338e3d87c7d70fe2b4a932d](https://github.com/OpenByteInc/QuantDinger/tree/79caf19b056e4bf04338e3d87c7d70fe2b4a932d)。范围：盘中 signal/notification 虚拟执行、资金与 A 股规则、订单持久化、实盘隔离。部署、安全、行情时效、许可证由主报告另审。本轮没有启动 Flask、PostgreSQL、Redis、Kafka、worker 或任何服务；没有安装项目、运行安装脚本、连接券商或发送通知。

## 结论

**QuantDinger 确实有 A 股 paper 模式、持久化虚拟账户和订单流程；但不能把它直接当成已经满足 A 股真实规则的现金模拟券商。** 它比 vn.py PaperAccount 更接近完整产品，值得作为界面、策略研究、订单审计和虚拟账户外壳候选。当前盘中虚拟成交仍是“参考价格加固定成本、全量成交”的策略盈亏账本；资金冻结、股份 T+1、A 股涨跌停和盘口部分成交没有在已核查路径中得到实现证据。现有实现有预算限制，不能笼统说“完全不管资金”；但预算限制也不能等价于可用现金与挂单冻结账本。

### A 股 paper 与实盘需要分开判断

- `app/markets/registry.py` 的 CNStock 明确有 `research/backtest/paper`，币种 CNY；不是只支持加密币 paper。[市场注册](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/backend_api_python/app/markets/registry.py#L109-L133)
- Strategy V2 部署 `_validate_execution_account` 对非 live 直接返回，对 live 只允许 Crypto/USStock/HKStock。报价币种校验也明确支持 CNStock/AStock 的 CNY。因此“无 A 股实盘”不能推出“无 A 股 signal 虚拟”。[部署限制](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/backend_api_python/app/services/strategy_v2/deployment.py#L442-L457)、[CNY 路径](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/backend_api_python/app/services/strategy_v2/deployment.py#L545-L619)
- `broker_market_policy.validate_strategy_config(require_exchange=False)` 自身仍会拒绝 CNStock，但当前已查到的执行调用在 live support 路径，不能只看该函数报错就声称 signal 不能部署。应以实际调用链和 UI/API 验收确认；本轮未启动 UI/API。
- 当前没有可直接用于未来 A 股实盘的已验证券商路径。live 白名单与部署校验均明确排除 CNStock。[市场策略](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/backend_api_python/app/services/broker_market_policy.py#L76-L80)

## 实际调用链与已有价值

`TradingExecutor` 计算意图与预算 → `StrategyV2OrderGateway` 落 pending_orders → `PendingOrderWorker._dispatch_one` 检查固定 execution contract → signal 分支 `dispatch_virtual_signal_order` → `execute_virtual_signal_order` 写独立虚拟账本 → 尝试发送通知。signal 分支结束后 return，不进入 `_execute_live_order`。[分派边界](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/backend_api_python/app/services/pending_order_worker.py#L1319-L1390)、[虚拟执行后通知](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/backend_api_python/app/services/pending_orders/signal_execution.py)

值得保留的机制：

- 虚拟账户、订单、持仓、成交是 PostgreSQL 表；不是退出即丢的进程字典。账户按 strategy_id 隔离，订单 pending_order_id 唯一，成交对应虚拟订单唯一。[建表](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/backend_api_python/migrations/20260926_signal_virtual_account.sql)
- 虚拟成交锁策略、已有订单、账户、持仓，最后提交；已有 pending_order 会返回幂等结果。订单回写 pending_orders/order_intents/fills，具有跨页面可追溯性。[账户与锁](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/backend_api_python/app/services/virtual_trading.py#L146-L263)、[成交落库](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/backend_api_python/app/services/virtual_trading.py#L440-L505)
- 未触价限价单保留 open；休市时市价单也暂存 open。`match_virtual_limit_orders` 后续读取数据库未成单，按当前价格重试；股票开市检查使用交易日历，CNStock 映射 XSHG，包含交易日、开闭市和午休判断。[延期订单](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/backend_api_python/app/services/virtual_trading.py#L264-L313)、[后续撮合](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/backend_api_python/app/services/virtual_trading.py#L605-L700)、[日历](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/backend_api_python/app/services/market_schedule.py#L201-L219)
- 有虚拟撤单/策略停止撤单路径，订单状态同步到 pending 和 intent。[撤单](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/backend_api_python/app/services/virtual_trading.py#L550-L603)

这些是源码证据；**本轮没有运行 PostgreSQL，不能声称事务并发、崩溃重启与 worker 恢复已通过集成测试**。

## 关键缺口

| 要求 | 当前行为 | 对采用的影响 |
|---|---|---|
| 独立初始虚拟资金 | 有 initial_cash、realized_pnl、commission、equity | 可作为策略盈亏账本 |
| 买入扣本金、挂单冻结现金 | `cash_balance = initial_cash + realized_pnl`；成交买入仅将佣金计入 realized；open 挂单不更新预留/冻结 | 该字段不能当现金可用余额；需要现金与冻结不透支约束 |
| 资金不足拒单 | 上层 budget guard 存在；成交服务自身没有预算重验/现金拒单 | 不能直接把服务当可靠券商边界，需统一校验和原子预留 |
| A 股 T+1 | 虚拟持仓只有 size/entry_price 等聚合值，没有购买日期/可卖数量批次；连续开平可全成 | 需要按市场交易日管理股份可卖批次 |
| 股票申报数量、涨跌停、停牌 | 服务输入参考价和数量，不取订单簿、停牌、每日价格带字段 | 数据与规则要贯穿成交边界，不能只靠研究侧字段 |
| 盘口撮合、部分成交 | `calculate_virtual_fill` 对有效开仓直接全部 requested；平仓只按现有持仓截断；一单一条 virtual trade | 平仓数量截断不等于盘口部分成交；需明确选择保守模型或真实盘口限量模型 |
| 真实费用 | 固定费率和滑点各 0.0005；虚拟 cost policy 不按 CN 市场分税费 | 不能当作 A 股佣金、最低佣金、卖出税费等真实费用模型 |
| 币种一致性 | 部署对 CN 识别 CNY，但统一 `strategy_order_fills` 插入把 fee_ccy 固定为 USDT | 如复用，应修正币种语义和报表统一口径 |

证据：[纯成交算法](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/backend_api_python/app/services/virtual_trading.py#L37-L101)、[现金更新](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/backend_api_python/app/services/virtual_trading.py#L323-L438)、[USDT fee_ccy](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/backend_api_python/app/services/virtual_trading.py#L471-L489)、[成本策略](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/backend_api_python/app/services/virtual_execution_costs.py)

### 不忽略已有预算和禁空约束

`strategy_order_budget_snapshot` 会拒绝不可验证资本、超额名义开仓；默认容差是资本的 2%，最大可配置 10%。调用者 `_execute_strategy_v2_intent` 先取当前 symbol 的持仓，再传到 `_execute_signal`；预算函数只看到这些同方向持仓，不读取其他标的占用或挂单冻结。正常路径还计算 `available_strategy_cash` 供策略上下文查看，但其输入也只有现有持仓，不是订单原子冻结。[调用者](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/backend_api_python/app/services/trading_executor.py#L1707-L1719)、[传递当前标的持仓](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/backend_api_python/app/services/trading_executor.py#L1764-L1793)、[预算入口](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/backend_api_python/app/services/trading_executor.py#L1875-L1901)、[预算算法](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/backend_api_python/app/services/pending_orders/order_budget.py)

因此，大额直接调用虚拟服务放行的探针证明的是**成交边界缺少资金校验**，不是证明 UI 任意单笔都能绕过上层预算。单独两个标的各申请 900、总资本 1000，各自拿空持仓快照都通过预算，可解释为何还需要账户层全局冻结。是否能通过所有端到端入口复现透支，本轮未做 UI/数据库集成测试。

普通 spot 策略提交有禁空：目标仓位不能为负，gateway 也拒绝 short action。纯虚拟算法支持 short 是通用算法设计，不应误报成“普通 A 股 UI 已证实允许裸空”。[目标禁空](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/backend_api_python/app/services/trading_executor.py#L1732-L1739)、[gateway 校验](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/backend_api_python/app/services/strategy_v2/live_execution.py#L385-L415)

### 回测能力不能冒充盘中 paper 能力

`strategy_v2/runtime.py` 的 `MultiAssetSimulationBroker` 确实有现金扣减、购买力裁剪、bar 的 suspended/limit_up/limit_down 检查、部分成交状态；这些是在另一条模拟/回测执行实现中。已核查的 `virtual_trading.execute_virtual_signal_order` 没有调用该类。也未发现上述回测类维护 A 股 T+1 股份可卖批次；非加密默认 amount_step 是 1，不是自动按 A 股板块完整申报规则。[回测现金](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/backend_api_python/app/services/strategy_v2/runtime.py#L1030-L1069)、[回测限制](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/backend_api_python/app/services/strategy_v2/runtime.py#L1209-L1276)、[默认数量步进](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/backend_api_python/app/services/instrument_rules.py#L826-L835)

## 已执行验证与复现

独立目录：`/media/ai/DATA1/dcw/demo/work/oss-evaluation/quantdinger-probe/`。真实导入锁定源码中的 virtual_trading、virtual_execution_costs、order_budget、capabilities；namespace 包引导绕过 Flask app 初始化，数据库依赖替换为仅响应/记录 SQL 的内存容器，calendar 固定为开市，socket 外连拒绝。没有替换价格、成交、预算或现金算法。

**八项自有 pytest：8 passed。** 结果在 `quantdinger-probe-results.json`。

| 探针 | 观察结果 |
|---|---|
| 初始资金 1000，直接服务买 100000 股、参考价 10 | 全成 100000 股，成交价 10.005，名义 1000500；cash_balance 仅减手续费 500.25 后为 499.75 |
| 连续同日买入 100 股再卖出 100 股 | 两笔 filled，持仓清空；固定开市 stub，未测试真实日历 |
| 服务买 1.5 股，参考价 12，附加 limit_up=11、盘口量0 | 成交 1.5 股、价 12.006；服务未消费这些字段 |
| 1000 资金挂买入 8000 名义未触价限价单 | open；现金仍1000；无冻结/预留 SQL |
| 重复同一 pending_order_id | 返回 idempotent，不重复增加持仓 |
| live 模式调用虚拟服务 | 入库前拒绝 signalModeRequired |
| 预算 guard | 900/另一空标的900各通过；1010对1000也通过；2000被拒 |
| 成本/限价纯函数 | 固定万五费用与滑点；未触价不成交，触价后滑点不突破限价 |

另对**原版未改动** `tests/test_execution_contract.py` 运行：**10 passed，1 warning**（隔离环境未装 pytest-timeout，所以原 pytest.ini 的 timeout 配置被提示未知）。该组验证 queue 固定 execution identity、payload 冲突拒绝、模式变更后新开仓拒绝等；不构成完整实盘安全认证。[原版测试](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/backend_api_python/tests/test_execution_contract.py)

原项目 `tests/conftest.py` 导入 create_app；为避免服务初始化，本轮使用 `--noconftest` 和 namespace 引导。**没有声称原版 test_signal_virtual_account.py 全套已运行**；它的存在、已有 mock 单测覆盖可以作为维护信号，但不替代 PostgreSQL 并发和恢复验证。[上游 signal 测试](https://github.com/OpenByteInc/QuantDinger/blob/79caf19b056e4bf04338e3d87c7d70fe2b4a932d/backend_api_python/tests/test_signal_virtual_account.py)

复现（在本轮研究根目录）：

```bash
PYTHONPATH= PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 paper-probe-venv/bin/python -m pytest quantdinger-probe/test_quantdinger_virtual.py -q
PYTHONPATH= PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 paper-probe-venv/bin/python - <<'PY'
import runpy, pytest
runpy.run_path('quantdinger-probe/test_quantdinger_virtual.py')
raise SystemExit(pytest.main(['--noconftest', 'QuantDinger/backend_api_python/tests/test_execution_contract.py', '-q']))
PY
```

## 对“能否直接用”的执行层建议

可以作为**受限研究/信号观察候选**，或通过验收后复用产品外壳、策略管理、通知配置和审计记录。若目标是用户要求的 A 股盘中真实行情、有限虚拟现金、真实交易约束模拟，当前不能直接验收为成品。

采用前至少补齐并测试：账户级现金/挂单预留与费用原子校验；股份交易日批次/T+1；A 股板块与证券类型规则；带源时间戳、盘口、涨跌停/停牌状态的行情输入；部分成交与撤单剩余量；币种；PostgreSQL 并发、重启恢复、撤单与成交竞态。优先复用现有表与事务边界，避免重写整个 UI/策略系统；但不能因为已有“虚拟账户”表就省略上述业务验收。

当前保持 signal-only，运行包不配置/导入真实券商凭据；禁止任何 AI 自动切换 live。固定 queue mode 和服务入口校验是已有的良好保护，还需由部署层落实用户的永久当前约束。未来 Windows 接 A 股券商是另一项未实现的适配工作，不是简单开启开关。
