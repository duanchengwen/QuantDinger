> 历史调研记录（2026-11-17）：本文保留原始评估上下文，“本仓库”及本地探针路径可能指已归档的独立项目。它不代表当前 Fork 已集成全部功能；固定提交链接仍用于核对当时版本。原始报告、探针及证据保存于本机 `outputs/legacy-archive-20261117/` 的归档中。

# 开源研究与模拟内核复用评估

核验日期：2026-11-17。目标是 **真实市场同步行情、虚拟资金、本地模拟成交**；历史研究仅作辅助。证据区分官方文档/源码检查、本机实际运行和尚未完成的接入验证，不以 star 数代替可靠性。本轮未连接真实券商，未启用 AI 自动晋级。

## 建议

1. **Optuna 直接复用**，继续负责有限预算参数搜索与试验存储；训练/验证隔离、最终保留集登记和人工发布由外围流程负责。
2. **VectorBT 保留为隔离的批量研究工具**，不承担同步行情撮合；应记录当前安装版本的许可全文，不能只写“Apache 2.0”。
3. **Backtrader 保留作冻结版本的事件回放对照**。其主分支多年没有新提交，不建议让它成为新的 A 股实时模拟主运行时。
4. **AKQuant 值得作为 paper 内核优先做适配验证**：本机已证明其纸面执行核心具备资金检查、T+1、部分成交及底层状态恢复，能减少自研撮合。它仍缺本项目要求的可直接确认的挂单现金预留、高层 live 检查点稳定性、A 股行情接入验收；不应直接宣布生产就绪。
5. **RQAlpha 不作为当前默认依赖**：具有中国市场研究/撮合基础，但许可明确区分用途且附有额外授权要求；数据/实时插件和日频撮合语义也须单独核验。没有复制其实现到本项目。

以上是基于下面证据的工程取舍，不是软件质量或投资结果保证。

## 版本、维护与许可

| 项目 | 本次核验版本/提交 | 维护与质量证据 | 许可事实与使用边界 |
| --- | --- | --- | --- |
| VectorBT | 项目已装 1.0.0；官方最新 release v1.2.1，2026-11-11；主分支 `9ac1b69` | 当前官方 CI 定义有 Ubuntu/Windows/macOS 与 Python/Rust 路径；本机既有共享目标研究测试通过 | 1.0.0 安装包及当前源码均为 Apache 2.0 + Commons Clause，包含收费出售/服务的附加限制，不能简写为宽松 Apache。见 [1.0.0 许可](https://github.com/polakowo/vectorbt/blob/v1.0.0/LICENSE.md)、[当前许可](https://github.com/polakowo/vectorbt/blob/9ac1b697166b00911def494cf95937274218aa54/LICENSE.md)、[release](https://github.com/polakowo/vectorbt/releases/tag/v1.2.1)、[CI](https://github.com/polakowo/vectorbt/blob/9ac1b697166b00911def494cf95937274218aa54/.github/workflows/tests.yml) |
| Backtrader | 已装 1.9.78.123；主分支最新提交 `b853d7c`，2023-04-19 | GitHub 仓库 `pushed_at` 为 2024-08-19，但这不是主分支最新提交时间；源码仍有旧 Travis Python 3.6–3.8 配置。本机能运行不等于持续上游维护 | 安装元数据 GPLv3+，仓库 GPL-3.0；分发与组合许可需按具体方式核对。见 [提交](https://github.com/mementum/backtrader/commit/b853d7c90b6721476eb5a5ea3135224e33db1f14)、[许可](https://github.com/mementum/backtrader/blob/b853d7c90b6721476eb5a5ea3135224e33db1f14/LICENSE)、[旧 CI](https://github.com/mementum/backtrader/blob/b853d7c90b6721476eb5a5ea3135224e33db1f14/.travis.yml) |
| Optuna | 已装及最新 release v5.1.0，2026-11-16；`c44f2ce` | 有平台测试、存储测试与异常 trial 管理。本机 SQLite study 恢复测试通过；它本身不证明交易策略有效 | MIT，另保留第三方许可文件。见 [release](https://github.com/optuna/optuna/releases/tag/v5.1.0)、[许可](https://github.com/optuna/optuna/blob/c44f2ce3cdc8f096f83897e0089fbf7e1375f70c/LICENSE)、[Windows CI](https://github.com/optuna/optuna/blob/c44f2ce3cdc8f096f83897e0089fbf7e1375f70c/.github/workflows/windows-tests.yml)、[存储/恢复 FAQ](https://optuna.readthedocs.io/en/stable/faq.html) |
| AKQuant | 隔离安装官方 PyPI 0.3.91；release 2026-11-16；`d9ebe28` | 项目 2026 年建立，发布活跃但历史较短；有 T+1、分钟竞价、撤单与网关恢复测试。发布 workflow 的 PR 路径主要是 `cargo check`，构建成功不代表每次全部业务回归通过 | MIT。见 [仓库元数据](https://api.github.com/repos/akfamily/akquant)、[release](https://github.com/akfamily/akquant/releases/tag/v0.3.91)、[许可](https://github.com/akfamily/akquant/blob/v0.3.91/LICENSE)、[CI](https://github.com/akfamily/akquant/blob/d9ebe28994fef0fa47c5f0a7d25be2c7e654a170/.github/workflows/release.yml)、[golden 基线说明](https://github.com/akfamily/akquant/blob/d9ebe28994fef0fa47c5f0a7d25be2c7e654a170/tests/golden/BASELINE_REPORT.md) |
| RQAlpha | release/6.5.0，2026-10-28；`0bf0494` | 有 bar/tick matcher、账户与事前风控模块，官方 CI 包含 Python 3.8–3.14 Linux 测试；本轮未运行其代码/数据 bundle | LICENSE 对非商业个人/非营利研究用途和商业/法人组织用途作了不同规定，后者有米筐授权要求。这里仅记录条文，不对用户用途作法律判定。见 [release](https://github.com/ricequant/rqalpha/releases/tag/release/6.5.0)、[完整许可](https://github.com/ricequant/rqalpha/blob/0bf04946e8250b457e30c3c81d338a6b925da0bb/LICENSE)、[CI](https://github.com/ricequant/rqalpha/blob/0bf04946e8250b457e30c3c81d338a6b925da0bb/.github/workflows/test.yml) |

## 实时、回放与数据源不是同一件事

VectorBT 的矩阵计算适合快速批量研究；Backtrader 官方有实时 feed/broker 文档，但通用接口存在不代表 A 股实时数据、交易规则和凭据已经准备好。[VectorBT 官方说明](https://vectorbt.dev/)、[Backtrader 实时接口](https://www.backtrader.com/docu/live/ib/ib/)。

AKQuant 已有 `run_live(..., trading_mode="paper")`，与回测共用 `SimulatedExecutionClient`。其 `broker="replay"` 是无柜台的确定性历史推送，官方明确不保证墙钟 timer 行为，不能用它冒充真实行情同步验收。核验的内置 broker 注册只有 `ctp` 和 `replay`；行情/交易源可由外部插件分离组合。因此不能从 `cn_stock_miniqmt` 这个费率 profile 名称推断已经接好 MiniQMT A 股行情。[paper 参数实现](https://github.com/akfamily/akquant/blob/d9ebe28994fef0fa47c5f0a7d25be2c7e654a170/python/akquant/live/_paper_execution.py)、[live/replay 边界](https://github.com/akfamily/akquant/blob/d9ebe28994fef0fa47c5f0a7d25be2c7e654a170/docs/zh/advanced/live_functional_quickstart.md)、[内置 broker](https://github.com/akfamily/akquant/blob/d9ebe28994fef0fa47c5f0a7d25be2c7e654a170/python/akquant/gateway/brokers/builtins.py)。

RQAlpha 的 `sys_simulation` 有 tick matcher；但源码在 `LIVE_TRADING` 时跳过该模拟模块，具体 live 接入仍需另外的实现。另外，其日频 `NEXT_BAR_OPEN` 会改成 `CURRENT_BAR_CLOSE` 并告警，迁移现有“收盘信号、下一日开盘”策略时必须核对时序，不能只比较收益总数。[模拟模块入口](https://github.com/ricequant/rqalpha/blob/0bf04946e8250b457e30c3c81d338a6b925da0bb/rqalpha/mod/rqalpha_mod_sys_simulation/mod.py)、[tick matcher](https://github.com/ricequant/rqalpha/blob/0bf04946e8250b457e30c3c81d338a6b925da0bb/rqalpha/mod/rqalpha_mod_sys_simulation/matcher/tick_matcher.py)。

## AKQuant 0.3.91 隔离动态探针

环境为独立目录 `work/oss-evaluation/akquant-venv`，Python 3.11.15；没有修改项目主依赖，没有运行第三方示例脚本或接入券商。探针由本项目编写，只调用明确 `paper` + `replay` 的 API，使用价格固定的构造分钟数据。为观察 engine 检查点，探针直接实例化内部 `LiveRunner`，这不是推荐给产品使用的稳定公共入口。

| 场景 | 实际结果 | 解释 |
| --- | --- | --- |
| 3,000 元账户，400 股 × 10 元买入 | 无成交，收到资金不足拒单 | 可复用其资金检查；尚非冻结资金证明 |
| 3,000 元账户，连续两个 200 股 × 9 元限价买单 | 两单均 `New`，策略可用现金仍显示 3,000 | 未观察到挂单现金预留；本项目不能依赖其默认行为满足预留要求 |
| 撤销第一个限价单 | 收到 `Cancelled`，未成交；另一单保留 | 订单撤销路径存在；因为此前没有冻结，不能把现金不变称作“解冻验收通过” |
| 10,000 元买入 100 股，当日卖出，再下一日卖出 | 买入后当日可卖 0，次日完成卖出，共两笔成交 | T+1 语义通过本例 |
| 买入 300 股，bar 成交量 1,000、比例上限 10% | 100 + 100 + 100 三次部分成交 | 本例量约束通过；仍是构造 bar，不是盘口排队证明 |
| 创建新 `LiveRunner` 并运行新会话 | 按传入现金重置、持仓为空 | 默认启动不是自动恢复持续账户 |
| `duration="8s"` 的 live 会话调用 `save_checkpoint` | `AttributeError`，局部 `wrapped_on_bar` 不能 pickle | 高层 live 检查点未通过本例 |
| 移除 duration，但仍使用 bounded replay 后保存 | 另一个局部 `wrapped` 不能 pickle；新进程加载产生 EOFError | 不能把失败写出的文件当有效检查点；外围应原子写入且验证 |
| 底层 `Engine.get_state_bytes/load_state_bytes`，新进程续跑 | 恢复现金 8,994.99、100 股和可卖数量 | 底层现金/持仓状态具有可复用的序列化边界 |
| 底层状态含未成交 200 股 × 9 元限价单，新进程送入 9 元行情 | 原挂单成交 200 股 | 挂单状态也能恢复；仍需覆盖异常中断与原子账户/策略状态协调 |

最后一个场景返回费用 5.018 元、现金 1,194.982 元。该结果说明需要明确费用和余额的分币舍入口径，不能直接假定与现有整数分账本完全一致。底层状态重新序列化的字节哈希并不恒等，因此验收比较的是恢复后的账户语义，不把二进制哈希相等当唯一恢复证明。

这些结果支持“**复用已有内核，加小而明确的适配与核验**”，不支持“已经完成 A 股实时模拟”。源码也说明该执行器的订单队列在内存中；高层 checkpoint 同时涉及 Python 策略 pickle，状态持久化责任必须明确。[内存执行器](https://github.com/akfamily/akquant/blob/d9ebe28994fef0fa47c5f0a7d25be2c7e654a170/src/execution/simulated.rs)、[checkpoint 实现](https://github.com/akfamily/akquant/blob/d9ebe28994fef0fa47c5f0a7d25be2c7e654a170/python/akquant/checkpoint.py)、[LiveRunner 初始化](https://github.com/akfamily/akquant/blob/d9ebe28994fef0fa47c5f0a7d25be2c7e654a170/python/akquant/live/_runner.py)。

探针文件保存在当前工作区 `work/oss-evaluation/`：`probe_akquant_paper.py`、`akquant-paper-probe.json`、`akquant-checkpoint-probe.json`、`akquant-engine-state-probe.json`、`akquant-engine-resume-probe.json`、`akquant-pending-resume-probe.json`。没有把这些探针加载到主模拟账户。

## 仍需单独验收的复用适配

- 固定 AKQuant 版本和许可证，包装行情 feed、委托与状态接口；运行模式只能 `paper`，任何 `broker_live` 路径在适配层硬拒绝。
- 为长驻未成交买单增加现金预留、到期/撤单释放和恢复对账；确认核心不足资金检查与外部预留没有重复扣款。
- 使用经过验证的底层状态接口，原子保存 engine 状态、策略状态、输入行情游标及版本；不要依赖当前失败的 live pickle 快照。
- 对接有真实来源时间戳的 A 股行情，测延迟、乱序、断流、重复与补数据；行情授权与框架许可分别确认。公告采用官方来源轮询，不能承诺与交易所行情同样的更新速度。
- 先用相同市场事件在内核与现有核对工具上比对成交、费用、T+1、拒单和恢复，再经过自然时间的盘中模拟。模拟成交仍不是交易所排队和实际成交回报。

AKQuant 的回测默认费用/T+1 与 paper 默认值不同：官方 README 明确提示回测不传 profile 时费用为零、T+1 未开启、单位为 1 股；必须显式配置中国股票规则、真实交易日历与分时范围。[官方 README](https://github.com/akfamily/akquant/blob/d9ebe28994fef0fa47c5f0a7d25be2c7e654a170/README.md)。

## 本机已验证与没有验证的内容

现有 VectorBT/Backtrader 共用目标的受控案例、Optuna study 恢复，以及本轮研究候选隔离测试合计 **12 项通过**。这只能证明当前锁定版本在这些受控场景可运行；不是完整上游测试、Windows 执行证明或真实同步行情验收。

AKQuant 上述动态探针已实际运行。RQAlpha 只做公开许可、源码与 CI 配置核验，未安装或运行。没有基于任一框架启用实盘，也没有自动晋级策略。各上游 CI 配置的存在不等同于我们验证过最新所有运行结果。
