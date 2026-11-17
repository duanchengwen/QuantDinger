# A 股盘中模拟运行

[English](PAPER_WORKER.md)

这是 Fork 中首个可运行的 A 股纵向闭环：真实腾讯网页行情 → 已配置示例策略/人工限价单 → PostgreSQL 虚拟现金与股份 → 日报/可选飞书通知。它使用独立进程 `python -m app.ashare`，不启动官方完整服务栈。当前未接入官方前端的交易按钮、Strategy V2 部署和 Kafka 分发；官方原有 signal 虚拟账户与新账本分开，不能混作同一账户。

## 安装与初始化

需要 Python 3.11/3.12 和独立 PostgreSQL 数据库。先进入 `backend_api_python`：

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-ashare.txt
cp ../configs/ashare-paper.example.json paper.local.json
export ASHARE_DATABASE_URL='postgresql://USER:PASSWORD@HOST:5432/PAPER_DATABASE'
python -m app.ashare --config paper.local.json migrate
python -m app.ashare --config paper.local.json init
python -m app.ashare --config paper.local.json quotes
```

Windows 使用 `.venv\Scripts\Activate.ps1`，环境变量通过 `$env:ASHARE_DATABASE_URL` 设置。数据库地址只通过环境变量提供，不写入配置、源码或日报。将本地配置保存在仓库之外，或使用 `*.local.json`。迁移只创建 `qd_ashare_*` 表；重复执行不重置账户。旧账户初始资金不能通过再次初始化重置。

## 每日规则与示例策略

示例配置默认不下单。使用 `quotes` 查看来源时间、接收时间、价格带、盘口与证据路径。每天按实际交易日核对股票状态和价格限制，填入 `rules` 中的日期、`is_trading_day`、`suspended`、`st`、上下限与 `evidence`，确认后才将 `reviewed` 改为 true。该字段表示操作员声明，系统不会把它当作交易所自动认证。未知状态、ST、停牌、过期规则、盘口缺失、价格带矛盾或超过 15 秒的行情均禁止成交。

仅支持非 ST 主板/创业板股票、100 股整手限价单，暂不支持科创板、北交所、零股、集合竞价或公司行动。日期规则不会自动顺延；连续运行跨日后等待新一天的审核配置。配置在进程启动时读取，修改后需停止并重新启动。

内置 `buy_once_below` 是验收示例，不是投资推荐。显式设置阈值、限价、数量、唯一策略 ID、`enabled=true` 和 `approved_by_operator=true` 后，它最多提交一次订单；第一次观察到的报价不用于成交，后续新报价才撮合。重启保留幂等性；改变策略 ID 表示新的人工配置。没有 AI 研究自动激活路径。

```bash
# 单次或连续运行；按 Ctrl+C 停止
python -m app.ashare --config paper.local.json run --cycles 1
python -m app.ashare --config paper.local.json run --cycles 0

# 人工虚拟订单：示例价格必须自行替换为已审核价格带内的价格
python -m app.ashare --config paper.local.json order --id manual-001 --symbol sz.000001 --side buy --quantity 100 --limit 10.00
python -m app.ashare --config paper.local.json status
python -m app.ashare --config paper.local.json cancel --id 1
python -m app.ashare --config paper.local.json report
```

同一账户只允许一个行情运行进程持有数据库锁；现金预留、成交和撤单另由账户行锁串行处理。数据库连接故障使进程退出，便于进程管理器重启；网络行情错误写入失败状态并按轮询间隔继续。挂单为当日单，跨日后在下一笔合格行情到达时释放旧预留；在此之前可人工撤单。行情证据和报表默认写入 `backend_api_python/reports/ashare`，数据库才是资金、订单和成交的权威状态。

## 成交模型与费用

买入先冻结本金与费用，成交扣本金；卖出仅能使用此前交易日买入的股份。新鲜报价的卖一/买一价格和显示量决定可模拟成交的数量，一轮报价的数量在同账户订单之间共享。相同来源时间的重复报价不能重复消费盘口。部分成交按整手处理，累计计算最低佣金。

默认费用是可配置工程模型：佣金 0.0003、最低 5 元、卖出税费 0.0005、过户费 0.00001；具体账户费用需单独核实。`fee_policy` 在初始化时持久化，重启以数据库中的费率为准。网页快照不提供真实队列位置，成交模型不保证与真实券商成交一致；本版本不覆盖公司行动与除权现金/股份调整，应在这些事件前停止涉及的股票模拟。

## 飞书单向推送

个人账户只有能够创建自定义群机器人并取得 Webhook 时才可使用；代码不假定个人账户一定具备权限。按[飞书官方说明](https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot)配置机器人，将地址和可选签名密钥放在环境中：

```bash
export FEISHU_WEBHOOK_URL='https://open.feishu.cn/open-apis/bot/v2/hook/REPLACE_ME'
export FEISHU_SIGNING_SECRET='REPLACE_IF_SIGNING_ENABLED'
python -m app.ashare --config paper.local.json run --cycles 0 --notify
```

推送标记为模拟交易，仅在成交或健康状态改变时发送。默认未配置即禁用；不自动重试超时消息，避免重复。推送失败不回滚已完成的虚拟成交。没有飞书收单、审批或策略晋级接口。真实 Webhook 尚未提供，本次仅验证协议、签名、拒绝重定向和错误脱敏。

## 安全与验收

本分支硬禁真实券商工厂、直接客户端、REST/WS 下单、待执行实盘任务和 Quick Trade 写入口；环境开关不能解除。Agent Gateway 仅允许 GET/HEAD/OPTIONS，其余请求返回 403。不要向此模拟环境导入真实下单凭据。由于禁令，上游要求成功连接真实/演示券商的测试不能作为本分支的通过目标。

运行数据库集成测试时必须设置专用 `TEST_ASHARE_DATABASE_URL`，避免默认跳过：

```bash
python -m pip install -r requirements-ashare-test.txt
python -m pytest tests/ashare -q
```

本地结果及明确限制见 [验收报告](ACCEPTANCE_CN.md)。[待启用 CI 模板](ci/ashare-paper-ci.yml)规划 Ubuntu PostgreSQL 集成与 Windows 行情、配置、安全、通知和命令入口测试。当前 GitHub 凭据缺少工作流写权限，模板尚未安装或远端执行，不代表 Windows 券商实盘适配完成。
