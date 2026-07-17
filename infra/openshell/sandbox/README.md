# SIQ Hermes BYOC 沙箱

该镜像是接近生产形态的 `siq_analysis` 沙箱。它默认不启用，也不会改变 `scripts/hermes/run_gateway.sh`。

镜像只包含：

- 冻结的 Hermes `0.13.0` 源码和已评审 SIQ patch；
- 已跟踪的 `siq_analysis` 和共享 profile 代码；
- 当前运行配置的无密钥编译副本；
- 固定 Python/Node 依赖和中文字体；
- Landlock 启动前需要存在的空可写 Hermes 状态路径。

镜像有意排除 Wiki 数据、`.env`、`auth.json`、sessions、logs、宿主数据库文件、OpenShell 状态和 Docker 控制 socket。`data/wiki` 与选定的可写任务/状态路径是独立运行时挂载；绝不能把整个仓库挂进该镜像。

仅生命周期 smoke 使用一个私有目录 bind，记录两代 WAL 和 runtime metadata，但不启动 Hermes。它的 proof 只针对 image/container 路径（`readiness_effect=none`）；不能替代正式 OpenShell mount、Landlock 或 gateway 证据。

镜像 healthcheck 有两个刻意分离的合同。直接 `docker run` 校验认证 Hermes loopback HTTP。OpenShell 管理的容器校验精确外层 supervisor 和 Hermes 子进程身份，因为 Docker health command 不会加入嵌套 sandbox network namespace。正式业务就绪仍需要 lifecycle 的认证 host-forward 加 `sandbox exec` HTTP 检查；Docker health 本身永远不是流量就绪回执。

构建但不启动 sandbox：

```bash
scripts/openshell/prepare_siq_analysis_context.sh
scripts/openshell/build_siq_analysis_image.sh
scripts/openshell/smoke_siq_analysis_image.sh
scripts/openshell/smoke_siq_analysis_image.sh --runtime-lifecycle-only
```

生成的 context 和 image metadata 位于被忽略的 `var/openshell/siq-analysis/`。后续 lifecycle 步骤仍必须证明精确挂载身份、只读数据库访问、模型/搜索路由、snapshot 恢复和 A/B 质量，才能从 Host 运行面切流。

同一镜像还包含 `/opt/siq/observe-entrypoint.sh`，用于 `infra/openshell/poc/siq-analysis-observe/` 中显式确认的一次性可行性路径。该 entrypoint 会把内嵌 profile 复制到 `/sandbox`，永不挂载宿主业务数据，也不影响正式就绪状态或默认 Host 运行面。
