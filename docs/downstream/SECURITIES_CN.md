# 历史股票池与逐日证券状态

[English](SECURITIES.md) · [运行指南](OPERATIONS_CN.md)

## 已实现的边界

`app.ashare.securities` 通过官方 BaoStock Python SDK 获取**指定日期**的证券列表 `query_all_stock(day=...)`，保留停牌成员，排除指数和当前模拟器不支持的代码。不会把今天仍上市的股票列表回填为历史股票池，也不把当前证券名称推断为过去的 ST 状态。当前支持沪深 A 股代码范围，不宣称覆盖北交所、所有退市证券或完整交易所历史主档。

指定关注证券的日线使用 `query_history_k_data_plus`、日频和 `adjustflag=3`（不复权），保存 `tradestatus`、`isST`、前收盘价等源字段。所有字段均先检查完整性、日期、证券代码、重复项和跨接口状态冲突。空状态保留 `null`；缺失日线为 `daily_bar_missing`，不视为停牌；不在该日股票池且没有日线的关注证券为 `not_in_dated_universe`，不因此阻止整个历史股票池入库。缺失不等于退市或未上市的认证结论。

**这是收盘后的研究资料，不是盘前/盘中的证券状态授权。**程序拒绝在北京时间 19:00 之前导入当天日终资料；这个时间是保守的应用防护阈值，不是供应商可用性承诺。即使收盘后成功取得当天 `isST` 或停牌标记，也不能提前用于当天盘中的交易决定，更不能视为下一交易日状态。IPO、重新上市、除权除息、特殊涨跌幅、退市整理等认证状态当前保留未知，继续需要人工核对。

## 数据来源评估

- [BaoStock 官方 PyPI 0.9.4 发布页](https://pypi.org/project/baostock/0.9.4/)由 BaoStock 维护者发布，示例列出日线 `tradestatus`、`isST` 及不复权参数。安装包中 `baostock.security` 的日期查询方法和 `baostock.common.contants` 是本次接口核对依据。SDK 0.9.4 使用 `public-api.baostock.com:10030`；较早的 0.8.9 使用旧域名。
- [BaoStock 官网](https://www.baostock.com/)是数据平台入口。旧的 `mainContent?file=...` 文档地址在本次检查时返回新版网站壳，未把第三方转载当作官方现行 SLA。免费服务、官方 SDK 都不等于交易所认证主数据或稳定性保证。
- [AKShare 官方 README](https://github.com/akfamily/akshare/blob/main/README.md)明确接口会随上游变动，当前还存在旧接口名称迁移。AKShare 的当前列表/日线接口不能自动补足可靠的历史证券状态；本模块没有把这些当前列表作为无提示后备数据。未来增加第二来源时，应独立保存来源、日期和冲突，不能静默覆盖。

本次先在宿主机检查：0.8.9 的匿名 SDK 查询等待 20 秒超时；升级 0.9.4 后等待 30 秒超时。系统 DNS 将新域名解析为透明代理 fake-IP，TCP 连接可建立但首次登录接收超时。随后使用正式 Compose 容器的正常 DNS/网络路径，**已于 2026-11-17 19:56:38 北京时间成功获取真实 BaoStock 数据**：指定日期 2026-11-17 的沪深支持范围股票池 5,224 只、关注证券日终状态 1 条，`approvals_created=0`。

成功回执为 `56e43b9c9b4245f0a96a05f40b10ecaa`，源 JSON SHA-256 为 `07a5f3c820cc474130e23b1bf06e31b2f30a53d741faa5f4f7e14bf70e396068`。本机验收日志为 `work/quantdinger-securities-acceptance/deployed-securities-sync.log`。成功依赖当前正式容器的网络路径，不能推断宿主机路径已修复或服务此后不会中断；也不能将单次成功提升为交易所认证历史主档或盘前状态授权。没有修改系统代理、强制绕过 DNS 或降低安全检查。后续跨日期补采是当下取得的历史数据，不是已经自然观察了这些交易日。 后续三日定时补采**部分成功后失败**：2026-11-13 的回执已于北京时间 19:59:53 入库，同样有 5,224 个成员及 1 条关注证券状态；回执 `6b5fe62e00fa4301b423115337d14994`，源哈希 `bd306b1c5bcd68afc1a392bc435682250afc3e9b332be570a58cf62c9fb677dc`。2026-11-16 尚无成功回执。之后独立诊断重试 11 月 13 日与 16 日均在 60 秒内未取得响应，另一登录检查返回 `10002007`；较晚的重试失败不抹去此前已验证的成功回执。三个日期历史覆盖仍未全部完成，不能声称夜间任务持续可用。

## 两个真实日期的只读对比

正式数据库与原始证据核验显示：11 月 13 日和 17 日的支持范围成员均为 5,224 只，代码集合没有新增/移除，3 只证券的源交易状态不同：`sh.605255` 正常→停牌，`sz.000831` 停牌→正常，`sz.002129` 停牌→正常。它们是供应商逐日返回的状态差异，不由当前状态反推。

关注证券 `sz.000001` 两日均报告非 ST、非停牌；11 月 13 日前收盘价/收盘价为 11.7900/12.0700，11 月 17 日为 12.3000/12.2500。11 月 17 日的前收盘价不能替代缺失的 11 月 16 日股票池或证券状态。对比文件为 `work/quantdinger-securities-acceptance/cross-date-comparison.json`，逐项通过源文件哈希和原始解码数据一致性验证。这是同一天补采两个历史日期，不是自然连续交易日运行证明。

## 存储和时间语义

三个独立的 PostgreSQL 表保存原始回执索引、每次回执的股票池成员和选定证券状态：

- `qd_ashare_security_receipts`：日期、接收时间、来源、SHA-256、证据路径、覆盖数量和解析器版本。
- `qd_ashare_universe_members`：每个回执中的股票池成员及该日源交易状态。
- `qd_ashare_security_statuses`：选定证券的日终字段和缺失原因。

每次成功同步新增回执，不更新旧版本。`known_at` 查询只使用 `received_at <= known_at` 的回执；今日补采的历史记录不会伪装成当年已采集的信息。供应商未提供可认证的发布时间，`source_time=null`，`available_at=received_at`。这可以防止本地回放读取尚未采集的版本，但不构成供应商历史发布时间的完整 point-in-time 证明。

证据保存 SDK 返回的**原始解码字段/行**（未标准化前）及请求参数的规范 JSON，不声称是原始网络字节。`source_hash` / `evidence_sha256` 指向该 JSON；`metadata.json` 记录接收时刻、日期、版本和表示方式。查询验证文件存在、哈希和字段语义，并核对数据库返回内容与源证据一致。缺失、损坏或不一致时拒绝提供可用状态，不能绕过为自动批准。

## 使用

先部署 `20261117_ashare_securities.sql`（已列入统一迁移），稳定保存证据目录。安装依赖使用仓库锁定的 `baostock==0.9.4`。以下命令只写公共市场资料，不写账户批准或订单：

```bash
cd backend_api_python
python -m app.ashare.securities ingest \
  --day 2026-11-16 \
  --symbol sz.000001 \
  --symbol sh.600000 \
  --evidence-dir /path/to/private/evidence/securities \
  --timeout 30
```

数据库由 `ASHARE_DATABASE_URL` 或 `DATABASE_URL` 提供；不要把包含密码的地址写入命令记录或提交仓库。`--symbol` 可重复 0～30 次；股票池始终获取指定日期的完整供应商返回范围，逐日 `isST` 等明细仅覆盖指定证券。未指定证券时只导入股票池，不宣称所有股票都有日线状态。供应商 SDK 在独立子进程中运行，超时会结束该子进程，不阻塞真实行情 worker 的进程全局 socket。

读取接口：

```python
store = SecuritiesStore(connection)
store.universe("2026-11-16", known_at=aware_datetime, limit=500, offset=0)
store.status("sz.000001", "2026-11-16", known_at=aware_datetime)
```

股票池结果含 `members`、`total`、分页和来源；无回执是 `status=not_observed`。状态结果无回执返回 `None`，绝不自动取前一天替代。返回的 `reviewed=false`、`auto_approval_eligible=false` 是固定安全边界；审批助手只能用它们准备人工审核资料。

## 验证

`tests/ashare/test_securities.py` 的 24 项测试涵盖日期/证券/复权方式不一致、状态缺失、停牌成员保留、指数过滤、不在历史池的关注证券、子进程超时、知识时间隔离、追加修订、证据损坏/丢失和数据库细节篡改。数据库用真实 PostgreSQL 的独立随机 schema，数据均明确为合成夹具。外部 BaoStock 成功返回已按上文正式部署回执单独验收，不包含在单元/集成测试的通过数量内。
