# siq_analysis OpenShell 观察 PoC

> NOT_PRODUCTION / 仅观察。该路径只证明可行性，不得接收 SIQ API 流量，也不能作为 V0.6 生产就绪证据。

这是通过 OpenShell 运行真实 `siq_analysis` 的最短路径。它使用接近生产形态的 SIQ 镜像和已配置 `siq-minimax-cn-pool` Provider，但有意省略正式任务挂载、Wiki/数据库访问、出网与数据 brokers、Exa 和本地回退就绪要求。

完整 start/smoke/stop 序列已于 2026-07-16 通过。脱敏结果保存在 `artifacts/openshell/v0.6/siq-analysis-observe-20260716/`。这是可重复的可行性结果，不是生产就绪或质量结果。

隔离合同刻意保持简单：

- sandbox 名固定为 `siq-analysis-observe-poc`；
- 宿主只在 loopback `127.0.0.1:28651` 暴露；
- 宿主 Hermes 保持在 `127.0.0.1:18651`，永不停止或重配置；
- 不挂载宿主项目路径、Wiki、数据库、profile 状态、Docker socket、凭据文件或 OpenShell 运行态目录；
- 内嵌项目/profile 只读；
- Hermes 只在一次性 `/sandbox/siq-analysis-observe/hermes-home` 下写入 config、SQLite/WAL、sessions、logs 和 gateway state；
- 已配置 MiniMax OpenShell Provider 作为 primary；
- 唯一直连内部服务路由是当前可用的 Nemotron 回退 `host.openshell.internal:8007`；离线 `8004/8006`、Exa 和所有数据库/API 端口都在该 PoC 策略之外；
- API key 和 run nonce 是被忽略 `var/openshell/poc/siq-analysis-observe/` 下的随机 `0600` 文件，并在已验证 stop 时删除。

## 证明内容

smoke 合同要求 OpenShell forward 上全部满足：

1. 认证 Hermes `/health` 成功，未认证 `/v1/runs` 被拒绝；
2. 真实 `siq_analysis` run 以 HTTP 202 创建；
3. SSE 发出 `message.delta`、`tool.started`、`tool.completed` 和 `run.completed`；
4. 模型调用一次终端计算并返回 `SIQ_OBSERVE_SUM=16`；
5. 第二个 run 接受 `/stop` 并以 `run.cancelled` 终止；
6. 内嵌项目保持只读，一次性 Hermes home 可写；
7. Docker inspection 未发现业务数据或宿主状态挂载。

它不证明报告质量、回退一致性、Tavily/Exa、Wiki/数据库行为、immutable-path 强制、broker identity、上传控制、A/B 等价、正式任务状态回滚或生产就绪。

## 手动运行

start 命令要求显式确认，并拒绝端口 `28651` 或固定 sandbox 名的既有 owner：

```bash
scripts/openshell/start_siq_analysis_observe_poc.sh --acknowledge-not-production
scripts/openshell/smoke_siq_analysis_observe_poc.sh
scripts/openshell/stop_siq_analysis_observe_poc.sh
```

`start_siq_analysis_observe_poc.sh` 会在必要时构建固定 SIQ 镜像，只检查隔离 OpenShell gateway/supervisor 和一个必需 Provider，然后启动一次性 sandbox。它不要求完整正式服务 preflight，也不改变 `start_all.sh`；`8007` 可用性仍是有用回退，不是脚本会启动或修复的前置条件。

smoke 失败后也必须执行 stop 命令。start 自带 best-effort 且身份校验的 rollback；如果 rollback 无法证明资源身份，它会失败关闭并保留 nonce/PID 状态供人工检查，而不是只按名称删除。
