# A 股模拟交易二次开发

[English](README.md)

开发仓库为 https://github.com/duanchengwen/QuantDinger ，官方仓库为 https://github.com/OpenByteInc/QuantDinger 。固定起点见 [baseline.json](baseline.json)。已接入官方页面和独立模拟进程，运行方式与范围见 [页面运行指南](WEB_WORKSPACE_CN.md)。以下是持续维护要求，不代表全部产品集成和验收已完成。

## 分支和远程

- `origin` 指向用户 Fork，`upstream` 指向官方。
- `main` 保持官方基线，`ashare-paper` 承载经测试的二开改动；功能使用短期分支。
- 本地默认推送目标设为 `origin`，`upstream` 的 push URL 设为 `DISABLED`。这些 Git 配置不随克隆传播，新环境需重新设置。
- 开发推送至 `origin/ashare-paper`；远程分支保护尚未设置。

新环境可按以下方式准备；已有分支时直接切换，勿覆盖：

```bash
git clone https://github.com/duanchengwen/QuantDinger.git
cd QuantDinger
git remote add upstream https://github.com/OpenByteInc/QuantDinger.git
git remote set-url --push upstream DISABLED
git config remote.pushDefault origin
git fetch upstream
git switch -c ashare-paper 79caf19b056e4bf04338e3d87c7d70fe2b4a932d
git config branch.ashare-paper.pushRemote origin
```

## 官方更新流程

1. 保持工作区干净，抓取官方更新并记录目标完整提交号。浅克隆在分析历史前补齐官方历史：`git fetch --unshallow upstream`；完整克隆使用 `git fetch upstream`。
2. 对比固定基线与目标提交，审查许可、依赖、配置、数据迁移、API、订单执行及 Agent 权限变化。只抓取不会改变运行版本。
3. 从当前 `ashare-paper` 创建 `review/upstream-日期` 分支。小范围独立修复可 `cherry-pick -x`；相互依赖的版本更新采用整体合并。不要随意摘取依赖其他迁移或接口变更的提交。
4. 在独立测试库处理冲突并运行相关上游测试、A 股验收和升级恢复演练。测试通过、人工审阅后合入开发分支，记录已采用的提交和未采用原因。整体升级更新基线；零散摘取另记清单，不能把整版标为已合入。
5. 发布与代码合并分开。升级前保存应用版本、镜像摘要和经过恢复验证的数据库备份；数据库不可逆迁移不能靠切换旧代码回退。升级不会自动晋级策略，也不会自动部署到模拟账户。

## 首次模拟运行前的验收门槛

- 真实行情保留上游报价时间、接收时间、原始证据与来源；对陈旧、缺失、时间错乱和来源口径变化明确拒绝成交。
- 历史股票池、名称/ST/停牌/价格限制按当日证据管理；今日名单或带未来名称的历史查询不能当完整历史状态。
- 在执行边界实现 A 股现金扣减与挂单冻结、股份 T+1、数量单位、涨跌停/停牌、盘口限量与部分成交、费用、撤单和重启幂等，并用 PostgreSQL 验证并发与恢复。保留官方租户隔离、事务、审计和 worker 所有权。
- 统一硬禁真实下单。仅把 `LIVE_TRADING_ENABLED` 和 `AGENT_LIVE_TRADING_ENABLED` 置为 false 不足以证明所有执行路径受控；在完成边界测试前不导入真实下单凭据、不连接券商网关、不启动存量实盘任务。
- AI 只生成研究候选，不直接改动运行策略；研究期间关闭 Agent Gateway 或仅授予读取权限。人工审核后才允许显式引入模拟配置。
- 资讯记录原始来源、发布时间、首次观察时间和存档哈希；搜索摘要不等于交易所盘中公告流。
- Ubuntu 为首发环境，Windows 为后续兼容验证目标；未执行的 Windows CI、连续运行和行情延迟测试不得标为通过。

## 复用范围和维护原则

保留官方的账户、研究界面、持久化、租户隔离与任务框架；将 A 股规则和行情证据通过清晰适配接口接入，必要的核心补丁保持小而独立。已有独立项目的模块和测试作为迁移参考，不直接复制其 SQLite 运行模式替代这里的 PostgreSQL。

官方已有 CNStock paper 路径，但当前虚拟账户算法不能直接验收为完整 A 股现金模拟券商。后端 Apache-2.0 不代表前端采用相同许可；前端独立仓库的许可、商标和归属要求需单独遵守。未来真实券商适配需要另行授权和验收；统一接口能减少迁移工作，但不能保证模拟成交与真实成交完全一致。

最新版本：[本轮验收](SECURITIES_ACCEPTANCE_CN.md) · [个人使用教程](USER_GUIDE_CN.md) · [实际待办](backlog.json)。

主题数据：[当前成分与时间追溯](THEMES_CN.md)。

主题归档最新结果：[本轮验收](THEMES_ACCEPTANCE_CN.md)。
