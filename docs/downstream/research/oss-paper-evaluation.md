> 历史调研记录（2026-11-17）：本文保留原始评估上下文，“本仓库”及本地探针路径可能指已归档的独立项目。它不代表当前 Fork 已集成全部功能；固定提交链接仍用于核对当时版本。原始报告、探针及证据保存于本机 `outputs/legacy-archive-20261117/` 的归档中。

# A 股真实行情、虚拟资金模拟系统：开源执行层核查

核查日期：2026-11-17。范围是 Ubuntu 当前纯模拟、未来 Windows 人工决定是否接券商；本轮没有连接任何券商、没有发送任何真实委托、没有实现自动晋级。

## 结论

**没有找到本轮证据足以支持“直接装上即可满足全部要求”的整套方案。** 最值得复用的是 vn.py 的行情/事件/对象接口与成熟适配器；`vnpy_paperaccount` 不能直接充当有资金上限的 A 股账户。WonderTrader 的实时 `TraderMocker` 同样没有资金检查。LEAN 的资金、订单、回测模型更完整，但默认市场/成交/结算并非 A 股规则，迁移成本显著；不能把“有 CashBuyingPowerModel / T+1 settlement”误认成已完成 A 股现金账户。

因此，先复用可靠组件，账户核心只保留确实无法由已验证组件提供的薄层，并以拒单、冻结、解冻、T+1、部分成交、重启一致性验收来决定替换，不能仅按星数决定。这里的“可靠”指能力有证据、版本可锁定、缺口清楚，不表示开源许可证保证正确性。

## 版本与证据等级

所有 checkout、依赖、探针与输出在 `/media/ai/DATA1/dcw/demo/work/oss-evaluation/`，没有修改业务源代码、项目依赖或锁文件，没有提交 Git。

| 项目 | 锁定 commit | 默认分支最后提交日期 | 本轮验证等级 |
|---|---|---|---|
| [vnpy/vnpy](https://github.com/vnpy/vnpy/tree/c6e231caf32b7fc97e6459817fff66458cf7e7c4) | `c6e231caf32b7fc97e6459817fff66458cf7e7c4` | 2026-10-06 | 阅读源代码；作为 paper 探针真实依赖直接导入 |
| [vnpy/vnpy_paperaccount](https://github.com/vnpy/vnpy_paperaccount/tree/1167dec502331c3f4385972631de2e89dac80f71) | `1167dec502331c3f4385972631de2e89dac80f71` | 2026-10-05 | 原始引擎离线执行；上游两项测试通过；九组自有探针通过 |
| [wondertrader/wondertrader](https://github.com/wondertrader/wondertrader/tree/08b230dd05facf6d650d949bfe51054115a2ecb1) | `08b230dd05facf6d650d949bfe51054115a2ecb1` | 2026-09-01 | 静态阅读实时 TraderMocker、策略上下文、适配器，未构建 C++ |
| [wondertrader/wtpy](https://github.com/wondertrader/wtpy/tree/9fd4cd98feaa6fa2ea084741b3bb2adbe2b6bd78) | `9fd4cd98feaa6fa2ea084741b3bb2adbe2b6bd78` | 2023-12-31 | 静态阅读；未加载随库分发的二进制 |
| [QuantConnect/Lean](https://github.com/QuantConnect/Lean/tree/8a0c6494fa02c76ca14fa08cc58d7756afb878e9) | `8a0c6494fa02c76ca14fa08cc58d7756afb878e9` | 2026-11-17 UTC | 锁定源码审阅，未执行 .NET 构建/交易引擎 |

LEAN 全量 checkout 下载耗时过长，已停止该后台下载；固定 commit 的重点源文件另由官方 raw GitHub 下载到 `lean-selected/`，本报告没有把不完整工作树视为安装完成。

日期来自固定 HEAD 的 git log，不是 GitHub `updated_at` 或 `pushed_at`。特别是 wtpy 的仓库 pushed_at 是 2025-08-06，但默认分支 HEAD 仍为 2023-12-31；不能把仓库元数据更新当成核心代码最近维护。元数据与提交记录分别保存在 `*-metadata.json`、`paper-evaluation-commits.json`。

## vn.py：可复用接口，不可直接复用为资金账本

[官方 PaperAccount 文档](https://www.vnpy.com/docs/cn/community/app/paper_account.html)说明资金组件显示实盘接口资金。**这不是本次仅引用旧文档得出的结论：当前 `PaperEngine` 源码仍没有 AccountData、现金余额、初始资金、交易费用、购买力或资金冻结逻辑。** 订单校验只处理类型和部分持仓检查，撮合只更新委托、成交、持仓。[当前引擎](https://github.com/vnpy/vnpy_paperaccount/blob/1167dec502331c3f4385972631de2e89dac80f71/vnpy_paperaccount/engine.py#L360-L455)

| 能力 | 当前源码实际行为 | 适用边界 |
|---|---|---|
| 虚拟资金、不足拒单、冻结/解冻资金 | 无资金账户和资金校验 | 不能用来回答“余额够不够”“挂单冻结多少” |
| 持仓不足拒单 | 双向持仓的平仓单检查 `volume - frozen`；开仓跳过；净持仓直接跳过 | 这是持仓冻结，不是资金冻结；净仓模式可产生负持仓 |
| A 股 T+1 | 不记录买入交易日/可卖批次 | 双向和净仓模式均可当日买入后卖出 |
| 涨跌停、有效申报价、数量规则 | 无读取 tick limit_up/limit_down 的校验，无股票整手校验 | 含涨停字段不等于执行涨停限制 |
| 撮合 | 买单取 ask1、卖单取 bid1，触价后全量成交 | 普通订单不读取盘口挂单量，不模拟队列和部分成交 |
| 撤单 | 活动订单取消；双向平仓释放冻结仓位 | 不能恢复资金，因为未实现资金 |
| 持久化 | 仅持仓 `vt_symbol / volume / price / direction` 写 JSON | 未保存活动订单、成交、冻结、当日买入批次、现金；非事务账户账本 |
| 实盘隔离 | 替换 MainEngine 的下单/撤单/报价方法 | 方便仿真，但不是运行时权限边界；不应加载真实交易 gateway 后依赖开关保证当前绝不实盘 |

证据：[委托与撤单](https://github.com/vnpy/vnpy_paperaccount/blob/1167dec502331c3f4385972631de2e89dac80f71/vnpy_paperaccount/engine.py#L198-L289)、[接管 MainEngine](https://github.com/vnpy/vnpy_paperaccount/blob/1167dec502331c3f4385972631de2e89dac80f71/vnpy_paperaccount/engine.py#L83-L102)、[持仓与保存](https://github.com/vnpy/vnpy_paperaccount/blob/1167dec502331c3f4385972631de2e89dac80f71/vnpy_paperaccount/engine.py#L495-L626)。源码的 quote 双边报价存在 PARTTRADED 状态，并不能证明普通订单支持盘口量部分成交。

### 已执行的离线探针

真实导入上述锁定版本源码，不改上游实现；使用上游测试中的 `FakeMainEngine/FakeEventEngine`（合约查询和本地同步事件容器），没有模拟/替换撮合、校验、持仓或持久化函数。测试目录有独立 `.vntrader`，禁用日志文件；自有探针还将 socket connect/create_connection 改为拒绝，且没有构造任何 gateway。未启动 GUI 或 EventEngine 后台线程。因此验证的是引擎业务行为，不是完整 GUI/SDK/异步压力测试。

| 探针输入 | 实际输出 |
|---|---|
| 不初始化任何现金/账户，买入 1,000,000,000 股，卖一数量只有 1 | ALLTRADED，成交量 1,000,000,000；账户事件 0 |
| 净仓模式同一天买 100、卖 100 | 两笔全成，剩余 0 |
| 双向持仓模式同一天买 100、卖出平仓 100 | 两笔全成，剩余多仓 0 |
| 净仓无持仓先卖 100 | 全成，净持仓 -100 |
| tick 涨停价 11，买单申报价 12、卖一 10 | 买单未被拒绝并成交 |
| 股票买入 1 股 | 全成 |
| 未成交限价单撤销 | CANCELLED，移出活动订单 |
| 买入后留下 1 笔未成单，再重建引擎 | 恢复 100 股持仓，未成单 1→0；保存字段仅四项 |
| 双向持仓模式无持仓卖出平仓 | REJECTED，说明已有持仓检查确实可执行 |

`paper_probe.py`、`paper-probe-results.json`、`paper-probe-stdout.json` 是可复现证据。依赖隔离在 `paper-probe-venv`，确切版本保存在 `paper-probe-requirements.txt`；仅安装依赖 wheel，未运行仓库安装脚本。采用直接源码导入的最小 headless 环境，没有安装完整 GUI 包，所以不能说完整 VeighNa Studio 安装已验证。

复现命令（工作目录是本轮研究目录）：

```bash
paper-probe-venv/bin/python paper_probe.py
PYTHONPATH="$PWD/vnpy:$PWD/vnpy_paperaccount" paper-probe-venv/bin/python -m pytest vnpy_paperaccount/tests -q
```

上游结果为 `2 passed`，覆盖触价/未触价两种限价撮合，没有覆盖资金或 A 股规则。[上游 tests](https://github.com/vnpy/vnpy_paperaccount/blob/1167dec502331c3f4385972631de2e89dac80f71/tests/test_matching.py)

## WonderTrader / wtpy：有更多撮合机制，资金仍是缺口

`TraderMocker::orderInsert` 检查股票买入 100 倍数、禁止不允许的做空、最小价格变动；随后开仓直接通过，不检查资金。`queryAccount` 把余额、可用、手续费、冻结等全部设为 0。[订单校验](https://github.com/wondertrader/wondertrader/blob/08b230dd05facf6d650d949bfe51054115a2ecb1/src/TraderMocker/TraderMocker.cpp#L138-L264)、[资金查询](https://github.com/wondertrader/wondertrader/blob/08b230dd05facf6d650d949bfe51054115a2ecb1/src/TraderMocker/TraderMocker.cpp#L830-L860)

它比 vn.py PaperAccount 多了盘口量限制：每次按 `min(对手一档数量, 剩余委托量)` 切分成交，并产生部分成交状态；但源码每个订单重新读取同一档量，且“新行情时间”判断被注释，不能据此声称它准确维护跨订单、跨撮合轮次的市场流动性消耗。还发现 `setVolTraded(getVolTraded() - curVol)` 的可疑累计方向，应在采用前用运行测试核实，不能当作已复现缺陷。[撮合循环](https://github.com/wondertrader/wondertrader/blob/08b230dd05facf6d650d949bfe51054115a2ecb1/src/TraderMocker/TraderMocker.cpp#L345-L438)

T+1 不能简单标成“整个 WonderTrader 没有”：品种元数据有 `TM_LongT1/isT1`，策略上下文和 TraderAdapter 有冻结/可用仓处理。但本轮审阅的实时 `TraderMocker` 自身没有按交易日维护股份可卖批次，不能把回测 `CtaMocker/HftMocker` 或上层策略逻辑自动归因给仿真券商的任何调用路径；需要完整指定配置与路径后再验收。[品种标记](https://github.com/wondertrader/wondertrader/blob/08b230dd05facf6d650d949bfe51054115a2ecb1/src/Includes/WTSContractInfo.hpp#L48)、[策略上下文](https://github.com/wondertrader/wondertrader/blob/08b230dd05facf6d650d949bfe51054115a2ecb1/src/WtCore/SelStraBaseCtx.cpp#L756-L816)、[成交适配层](https://github.com/wondertrader/wondertrader/blob/08b230dd05facf6d650d949bfe51054115a2ecb1/src/WtCore/TraderAdapter.cpp#L2175)

实时 mocker 保存/恢复的仍是多空持仓数量，不是完整资金、订单、成交、冻结与交易日账本；撤单可释放剩余平仓冻结。未发现该 mocker 订单入口校验每日涨跌停带，不能把真实柜台的拒单能力当成本地仿真能力。[持久化](https://github.com/wondertrader/wondertrader/blob/08b230dd05facf6d650d949bfe51054115a2ecb1/src/TraderMocker/TraderMocker.cpp#L549-L633)、[撤单](https://github.com/wondertrader/wondertrader/blob/08b230dd05facf6d650d949bfe51054115a2ecb1/src/TraderMocker/TraderMocker.cpp#L762-L824)

wtpy 根据系统加载 `.dll/.so`，仓库包含二进制，不代表本机 ABI、行情权限或该二进制与最新 C++ HEAD 一致。本轮没有加载这些二进制；源代码审查结论适用于上述 WonderTrader C++ HEAD，不冒充对 wtpy 所捆绑旧二进制的运行验证。[平台加载逻辑](https://github.com/wondertrader/wtpy/blob/9fd4cd98feaa6fa2ea084741b3bb2adbe2b6bd78/wtpy/wrapper/PlatformHelper.py)

## LEAN：账户框架更完整，默认不等于 A 股

`PaperBrokerage` 继承 `BacktestingBrokerage`，从 CashBook/启动数据取得现金和持仓；后者维护待处理订单、撤单、调用组合购买力检查再撮合。这是实际的可复用账户/交易框架，优于仅推送委托与持仓的演示模块。[PaperBrokerage](https://github.com/QuantConnect/Lean/blob/8a0c6494fa02c76ca14fa08cc58d7756afb878e9/Brokerages/Paper/PaperBrokerage.cs)、[购买力检查与撮合](https://github.com/QuantConnect/Lean/blob/8a0c6494fa02c76ca14fa08cc58d7756afb878e9/Brokerages/Backtesting/BacktestingBrokerage.cs#L317-L403)

然而三个容易误判的点必须明确：

1. 默认股票市场是 USA；`DefaultBrokerageModel.GetBuyingPowerModel` 对股票返回 `SecurityMarginModel`。`CashBuyingPowerModel` 的 open-orders 预留数量逻辑确实存在，但该类要求 `IBaseCurrencySymbol`，默认用于 Forex/Crypto，不能直接拿类名证明 A 股冻结资金、禁空规则完整。[默认模型](https://github.com/QuantConnect/Lean/blob/8a0c6494fa02c76ca14fa08cc58d7756afb878e9/Common/Brokerages/DefaultBrokerageModel.cs#L330-L362)、[CashBuyingPowerModel](https://github.com/QuantConnect/Lean/blob/8a0c6494fa02c76ca14fa08cc58d7756afb878e9/Common/Securities/CashBuyingPowerModel.cs#L82-L168)
2. `DelayedSettlementModel` 延迟的是卖出所得现金结算；这不等同于 A 股买入股票当日不可卖，二者约束对象不同。[结算实现](https://github.com/QuantConnect/Lean/blob/8a0c6494fa02c76ca14fa08cc58d7756afb878e9/Common/Securities/DelayedSettlementModel.cs#L52-L100)
3. `EquityFillModel` 的限价成交默认全部数量，并明确留下按深度/成交量部分成交的 TODO；框架可定制 fill model 不等于默认具有真实盘口撮合。[股票限价成交](https://github.com/QuantConnect/Lean/blob/8a0c6494fa02c76ca14fa08cc58d7756afb878e9/Common/Orders/Fills/EquityFillModel.cs#L437-L457)

本轮没有验证 LEAN 的 A 股行情适配、A 股涨跌停/集合竞价/申报数量/费用模型或重启后未成订单完整恢复；这些应列为集成缺口，而不是笼统宣称 LEAN 没有持久化。其大型 .NET/Python 框架和自托管容器 CI 有成熟工程基础，但在当前仅需 Ubuntu 本地 A 股模拟的范围内，整套引入不是已证明的低成本路线。[构建与测试工作流](https://github.com/QuantConnect/Lean/blob/8a0c6494fa02c76ca14fa08cc58d7756afb878e9/.github/workflows/gh-actions.yml)

AKQuant 的研究与模拟能力由并行研究报告单独评估；本报告没有独立运行其执行引擎，不能给出已验证通过的结论，避免把“回测支持 A 股”当成“盘中实时账户可直接上线”。

## 行情适配与跨平台：开源代码不赠送行情授权

- vn.py 主框架 README 明确列 Ubuntu 22.04+ / Windows 11+，可作为跨平台接口基础，但每个 SDK 要独立确认。[平台与接口目录](https://github.com/vnpy/vnpy/blob/c6e231caf32b7fc97e6459817fff66458cf7e7c4/README.md#L232)
- `vnpy_rqdata` 固定版本 `8cad1ef97efe1a48559abcff6fd741b2cdf2a132` 有 `RqdataGateway`，初始化 RQData 后建立 `LiveMarketDataClient`，股票订阅 `tick_代码.交易所`；它的下单方法返回空字符串、撤单无操作，适合作为**无交易能力的行情接入候选**。README 标 Windows/Linux/macOS，并明确需要相应数据服务权限。尚未使用账号进行实时权限和延迟验收。[README](https://github.com/vnpy/vnpy_rqdata/blob/8cad1ef97efe1a48559abcff6fd741b2cdf2a132/README.md)、[实时接口](https://github.com/vnpy/vnpy_rqdata/blob/8cad1ef97efe1a48559abcff6fd741b2cdf2a132/vnpy_rqdata/rqdata_gateway.py#L55-L143)
- `vnpy_xt` 固定版本 `84821063f7d76b0c4ee592184d65fd9e3fd5cdfc` README 标 Windows，要求 XtQuant 与 token 或保持迅投客户端运行。因此不能宣布 Ubuntu 原生已验证可用。[README](https://github.com/vnpy/vnpy_xt/blob/84821063f7d76b0c4ee592184d65fd9e3fd5cdfc/README.md)
- `vnpy_xtp` 固定版本 `97522b8c6ef668e2e845b63069de103b24ec28a6` 标 Windows/Linux；源码连接设置含账号、密码、行情/交易地址和授权码，`connect` 同时调用行情与交易 API。它可列未来券商适配候选，当前不应直接加载；MIT 包装层不代表 SDK 授权、券商开放或用户交易权限。[README](https://github.com/vnpy/vnpy_xtp/blob/97522b8c6ef668e2e845b63069de103b24ec28a6/README.md)、[连接实现](https://github.com/vnpy/vnpy_xtp/blob/97522b8c6ef668e2e845b63069de103b24ec28a6/vnpy_xtp/gateway/xtp_gateway.py#L176-L222)

这些交易框架并不自动解决及时新闻的授权、时间戳、重复稿、断流和延迟质量；应把资讯供应商与交易账户分开验收。本报告没有用“实时行情”营销文字冒充实测盘中延迟。

## 维护、测试与许可证

- vn.py 与 PaperAccount 为 MIT。固定 HEAD 的 CI 均有成功记录；PaperAccount workflow 在 Windows Python 3.13 运行 lint/typecheck/pytest/build，本轮 Ubuntu 两项上游测试也通过。此证据不代表 A 股业务规则齐全。[vn.py 固定 HEAD CI](https://github.com/vnpy/vnpy/actions/runs/37446013534)、[PaperAccount 固定 HEAD CI](https://github.com/vnpy/vnpy_paperaccount/actions/runs/37326179955)、[测试配置](https://github.com/vnpy/vnpy_paperaccount/blob/1167dec502331c3f4385972631de2e89dac80f71/.github/workflows/pythonapp.yml)、[MIT](https://github.com/vnpy/vnpy_paperaccount/blob/1167dec502331c3f4385972631de2e89dac80f71/LICENSE)
- WonderTrader/wtpy 为 MIT。WonderTrader 有 TestUnits 源码，但本轮公开 actions 看到的成功是 Pages 部署，不是 C++ 撮合测试；wtpy actions 查询返回空列表，不能声称已有持续业务回归证据。[测试目录](https://github.com/wondertrader/wondertrader/tree/08b230dd05facf6d650d949bfe51054115a2ecb1/src/TestUnits)、[Pages run](https://github.com/wondertrader/wondertrader/actions/runs/33463061054)、[MIT](https://github.com/wondertrader/wondertrader/blob/08b230dd05facf6d650d949bfe51054115a2ecb1/LICENSE)
- LEAN 为 Apache-2.0；固定 HEAD 的 Build & Test 和 Regression Tests 成功。它的工程测试证据更广，但本轮没有运行，也没有找到本任务 A 股规则的验收证据。[Build & Test](https://github.com/QuantConnect/Lean/actions/runs/43990930126)、[Regression](https://github.com/QuantConnect/Lean/actions/runs/43990930081)、[许可证](https://github.com/QuantConnect/Lean/blob/8a0c6494fa02c76ca14fa08cc58d7756afb878e9/LICENSE)

GitHub API 原始精简结果保存在 `*-actions.json`、`*-pinned-actions.json`。查询默认最新 runs 可能属于其他分支/PR，本报告以固定 SHA 的结果作判断。许可复用时须保留相应许可证/通知；行情服务和专有 SDK 的授权另行核查。

## 采用决策

| 决策 | 组件/范围 | 具体理由与剩余工作 |
|---|---|---|
| 直接复用候选 | vn.py 事件、行情/合约对象、订阅接口约定 | 避免重造生态接口；版本锁定后仍需与现有服务边界适配 |
| 需封装/验收 | RQData 只读实时 gateway | 无交易入口，跨平台声明清楚；要取得权限并实测时效、交易所覆盖、盘口字段与断线重连 |
| 需封装，不能直接承担账户 | vnpy_paperaccount | 可用于策略流程 smoke test；作为生产模拟账户需补资金/费用/股份批次/交易规则/部分成交/事务持久化，改动已不属于配置一下即可 |
| 暂不替换当前执行层 | WonderTrader TraderMocker / wtpy | 现金缺口相同；另有二进制版本、完整 T+1 路径与撮合一致性验证成本 |
| 暂不整体引入 | LEAN | 值得借鉴/复用账户模型，但中国市场适配尚未证明低成本；当前引入会增加 .NET 与行情/规则集成面 |
| 当前禁用 | 任意真实券商 gateway、凭据、自动 paper→live 晋级 | 用户明确当前只模拟；用独立部署包/权限与配置层排除真实执行，不依赖 UI 开关 |

最低通过标准应是可复现行为：资金不足拒单、并发挂单冻结不透支、撤单/成交原子解冻、A 股可卖批次与交易日切换、涨跌停及数据缺失保守拒绝、按已声明盘口模型部分成交、订单幂等、崩溃重启恢复及账户恒等式。它们是采用门槛，不是暗示需要从零自研整套平台。
