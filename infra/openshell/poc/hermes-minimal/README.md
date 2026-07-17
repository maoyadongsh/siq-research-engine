# 最小 Hermes PoC

该 fixture 用于验证 OpenShell `0.0.83` 内现有 SIQ patch 版 Hermes `0.13.0` API。它有意不作为输出质量 benchmark。

安全边界：

- 从冻结 rollback bundle、commit 和 dirty patch 重建 Hermes；
- 将 source allowlist 复制到被忽略、会做 mount scan 的 build context；
- 不挂载 SIQ 仓库或任何宿主状态；
- 不使用真实模型、搜索或数据库凭据；
- 将确定性模型 stub 和 Hermes API 保持在 sandbox loopback；
- 只通过 OpenShell 暴露专用宿主端口 `127.0.0.1:28642`；
- 以 uid/gid `10001` 运行 Hermes，要求 Landlock，且没有公网 network policy。

ARM64 `0.0.83` supervisor 已观测到两个比公开通用支持下限更严格的镜像要求：glibc 必须提供 `GLIBC_2.38` 和 `GLIBC_2.39`，`iproute2` 必须提供可信 `ip` binary。固定 Python 3.11.15 trixie 镜像满足二者，并包含 `nftables`，因此 proxy-bypass detection 不会降级启动。

OpenShell `0.0.83` 还会把仅目录适用的 `ReadDir` 权限应用到每条 Landlock rule。上游示例中的单独 `/dev/null` 和 `/dev/urandom` 规则因此在 `hard_requirement` 模式下失败。SIQ 保留这些精确路径，并使用 `infra/openshell/patches/v0.0.83/` 下的项目补丁，从已经打开的文件描述符选择 file-only 权限。`/dev` 不会放宽，Landlock 仍是强要求。

通过项目脚本运行：

```bash
scripts/openshell/build_patched_supervisor.sh
scripts/openshell/prepare_hermes_poc.sh
scripts/openshell/build_hermes_poc.sh
scripts/openshell/start_hermes_poc.sh
scripts/openshell/smoke_hermes_poc.sh
scripts/openshell/stop_hermes_poc.sh
```

`start_hermes_poc.sh` 会在被忽略的 `var/openshell/poc/hermes-minimal/api.key` 下创建随机一次性 Bearer key，显式传入 sandbox，并在 stop/rollback 时移除。该 key 永远不进入镜像或 Git。脚本还会显式注入 `HOME` 和 `HERMES_HOME`；OpenShell initial commands 不能假设镜像环境变量已经存在。

每次运行还有一个 192-bit nonce，保存在被忽略的 `0600` 状态和 gateway sandbox labels 中。stop 与 rollback 会在删除前交叉检查 nonce、gateway sandbox ID 和 Docker 管理的 sandbox ID/name/namespace。缺少或冲突的身份状态会失败关闭。合同测试会在正常、终端工具和取消流程前验证缺失或错误 Bearer key 会以 HTTP 401 被拒绝。

该 PoC 有意一次只验证一个 profile。生产形态是每个 Hermes profile 一个独立 sandbox 和 policy；在 profile 级 A/B 通过前，现有 Host 运行面仍是默认值。
