# 官方前端模拟交易扩展

本扩展在官方 QuantDinger Vue 的菜单中加入 `/#/ashare-paper`，复用登录、JWT 请求和多语言系统，不是另外创建一个无鉴权看板。

前端使用独立的源码可用许可，见 `UPSTREAM_LICENSE.txt`，不适用后端 Apache 许可。固定版本见 `manifest.json`。保留官方品牌、署名和许可证；商业使用需另外获得许可。本次用于非商业研究和模拟验证。

在本仓库根目录执行：

```bash
python scripts/frontend/prepare_ashare_frontend.py ../work/quantdinger-frontend --build
```

脚本会在独立目录取得官方源码，校验版本和许可证，应用小范围的路由/语言补丁，再复制新增页面。遇到上游变更或用户独立修改的文件会拒绝覆盖。安装使用锁定依赖并禁用安装生命周期脚本，随后构建完整官方应用；构建结果保留在独立前端目录的 `dist/` 中。

本地后端端口 5017 时：

```bash
cd ../work/quantdinger-frontend
VITE_DEV_PROXY_TARGET=http://127.0.0.1:5017 pnpm dev --host 127.0.0.1 --port 5177
```

访问 `http://127.0.0.1:5177/#/ashare-paper`，通过官方页面登录。页面每三秒读取服务器持久化状态，不在 HTTP 进程或浏览器里撮合；服务器需另行运行 managed worker。买卖入口要求新鲜行情和正常运行状态，最终约束以服务端校验为准。

页面提供每日规则审核、虚拟买卖/撤单、持仓/成交以及可选的飞书配置引导和测试消息。已保存的机器人密钥不会返回页面。

跟进官方更新时先审核新固定版本，重建补丁并验证完整构建、鉴权、实际页面行为及接口契约，不能直接对任意前端版本盲目覆盖。
