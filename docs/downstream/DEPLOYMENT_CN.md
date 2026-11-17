# A 股模拟长期运行部署

[English](DEPLOYMENT.md) · [页面使用](WEB_WORKSPACE_CN.md)

本部署仅运行 PostgreSQL、一次性迁移、正式 Gunicorn API、独立模拟 worker 和官方前端 nginx。没有官方实盘 worker、策略自动评估/晋级、AI 队列或自动审批服务。源码中的券商访问禁令与 Agent 只读约束继续生效。不要把官方完整 Compose 与本文件叠加启动。

## 首次部署（Ubuntu）

需要 Docker Engine 与 Compose v2、Python 3.11+，以及构建官方前端所需的 Node/pnpm。先在仓库根目录准备前端：

```bash
python scripts/frontend/prepare_ashare_frontend.py ../work/quantdinger-frontend --build
```

将构建出的完整 `dist/` 复制到仓库外的版本化资产目录，例如 `/srv/quantdinger-paper/assets/release-1/`，保留其中的官方许可证。不要直接挂载持续开发时会被删除重建的 `dist/`。

```bash
python scripts/ops/paper_stack.py --state-dir /srv/quantdinger-paper/state init \
  --frontend-dist /srv/quantdinger-paper/assets/release-1 \
  --project quantdinger-paper --port 8077
python scripts/ops/paper_stack.py --state-dir /srv/quantdinger-paper/state build
python scripts/ops/paper_stack.py --state-dir /srv/quantdinger-paper/state up
python scripts/ops/paper_stack.py --state-dir /srv/quantdinger-paper/state status
```

打开 `http://127.0.0.1:8077/#/ashare-paper`。初始化脚本生成持久随机管理员密码、数据库密码、JWT/通知加密密钥，保存在权限 0600 的 `state/config.env`；目录权限 0700，不打印秘密。默认账号 `paper_admin`。密码在页面修改后以数据库为准，重新启动不恢复初始密码。`init` 拒绝覆盖已有目录，也拒绝复用已经拥有容器或卷的项目名。已有本机验收配置可用 `--import-env-json /private/local-env.json` 保留管理员与加密密钥，但这**不迁移数据库**，须另行完成经核验的数据恢复。

迁移必须成功且存在有效管理员，API/worker 才启动。所有应用进程使用 UID 10001；数据库、行情原始证据和日志分别保存在 Compose 命名卷。只发布回环地址的网页端口；数据库/API 不发布宿主机端口。远程访问应使用 SSH 隧道，或由管理员另行配置 TLS、访问控制和反向代理，不能直接将开发服务器公开。

官方完整后端依赖锁包含一个明确记录的兼容例外：LiteLLM 声明 `filelock<4`，而上游为安全修复固定 `filelock==4.2.4`。镜像沿用官方 `--no-deps` 锁定安装；构建检查只允许这一条精确例外，其余依赖错误均失败，不降级安全修复版本。镜像含官方依赖并不代表允许启用券商或 AI 执行。

## 运行与恢复

```bash
python scripts/ops/paper_stack.py --state-dir /srv/quantdinger-paper/state status
python scripts/ops/paper_stack.py --state-dir /srv/quantdinger-paper/state logs
python scripts/ops/paper_stack.py --state-dir /srv/quantdinger-paper/state stop
python scripts/ops/paper_stack.py --state-dir /srv/quantdinger-paper/state up
```

服务异常退出及主机重启后由 Docker `unless-stopped` 重启。需启用 Docker 开机启动。`stop` 是有意停机，保持到再次 `up`。API 使用正式 Gunicorn、请求数轮换、超时与独立健康检查；nginx 动态解析 Docker DNS，API 容器重建后可重新连接。Docker 日志最多每服务 3×10 MB，应用文件日志沿用轮换器。

worker 的健康检查读取数据库中的全局心跳，空账户也有心跳；它与“可下单”不同。午休、休市、等待当日人工审核不应因而重启。行情延迟、停牌/规则缺失等仍阻止交易。Docker 的 `unhealthy` 标记**不会自行重启挂住的容器**：应结合心跳检查、监控或人工处理；本部署不装有权控制所有容器的通用 autoheal 服务。

每天登录完成证券状态和价格带审核，worker 不会自动审批。自然多日观察需要真实日历推进；受控跨日回放不能冒充实际连续运行。日历覆盖范围、支持板块和成交模型限制见验收报告。所有数据与报告仍需磁盘容量监控；证据不自动删除，避免破坏可追溯性。

## 一致备份与隔离恢复

```bash
python scripts/ops/paper_stack.py --state-dir /srv/quantdinger-paper/state backup \
  /srv/quantdinger-paper/backups/manual-001
python scripts/ops/paper_stack.py --state-dir /srv/quantdinger-paper/state restore-check \
  /srv/quantdinger-paper/backups/manual-001
```

备份短暂停止原本运行的 API/worker，导出完整 PostgreSQL 自定义格式备份和原始证据归档，保存私有配置、代码提交、镜像 ID、每个虚拟账本/用户表的行数与内容指纹及 SHA256 清单，最后恢复此前运行的服务。备份暂停前持久保存此前运行的服务清单；SIGTERM 会触发恢复，计时器的 ExecStopPost 还会在超时/强杀后按清单补做恢复，不启动原本已停止的服务。人工恢复命令为 `python scripts/ops/paper_stack.py --state-dir /srv/quantdinger-paper/state resume-backup`。备份失败也会尝试恢复服务；失败目录留作排查，不能视为完成的备份。备份目录拒绝覆盖且必须在 Git 仓库外。**备份含账户与密钥，目录权限不等于加密；请加密后离机保管。** 修改 SECRET_KEY 会影响旧通知配置解密，迁移必须保留它。

`restore-check` 校验所有文件和证据归档安全性，在**无网络、无宿主端口**的新 PostgreSQL 容器中恢复数据库，逐表核对账本与用户指纹，最后仅删除自己创建的临时容器/卷，不修改源部署。

正式恢复也只允许新的状态目录、项目名、数据库卷：

```bash
python scripts/ops/paper_stack.py --state-dir /srv/quantdinger-paper/restored-state restore \
  /srv/quantdinger-paper/backups/manual-001 \
  --project quantdinger-paper-restored --port 8078 \
  --frontend-dist /srv/quantdinger-paper/assets/release-1
python scripts/ops/paper_stack.py --state-dir /srv/quantdinger-paper/restored-state up
```

恢复命令验证完成后仍保持 API/worker 停止，便于检查版本后再 `up`；现有目标项目、容器或卷都会被拒绝。失败的恢复目标保留用于排查，重新尝试须换新目录/项目。不要运行 `down -v` 删除正式数据。更新前备份并隔离恢复验证，再构建新代码、使用新的前端资产版本，运行 `up` 执行迁移；数据库迁移不能仅靠回退镜像撤销。

## 每日自动备份

```bash
python scripts/ops/paper_stack.py --state-dir /srv/quantdinger-paper/state install-backup-timer \
  --backup-root /srv/quantdinger-paper/backups
systemctl --user list-timers 'quantdinger-backup-*'
```

生成当前用户的独立 service/timer，每天北京时间 18:10 后随机延迟至多 60 秒备份，错过时补执行。同一状态目录用排他锁阻止备份与 build/up/stop 等操作交叉。备份期间不要绕过工具直接执行 Docker 启停或更新命令。已有单元拒绝覆盖。计时器需要运行中的用户 systemd；无人登录时运行还需管理员为该用户启用 lingering。检查 `loginctl show-user "$USER" -p Linger`，需要时由管理员执行 `loginctl enable-linger 用户名`。失败用 `journalctl --user -u quantdinger-backup-quantdinger-paper.service` 查看；不自动删除历史备份，必须按容量和保留要求另行管理。

飞书是单向可选通知；Webhook 由用户在页面引导中配置并主动测试。部署或备份不会替用户发送测试消息，也不把未配置状态记作送达成功。

更多心跳、存储保护和每日报告说明见[连续运行运维](OPERATIONS_CN.md)。
