# SIQ OpenShell v0.0.83 补丁

上游基线：

- 仓库：`NVIDIA/OpenShell`
- tag：`v0.0.83`
- commit：`e3d26dd3ae0dee247bbc5db368545832757ac493`
- 原始 `landlock.rs` SHA-256：`2c2305fabdd66a42a6c2c5969dc38a9054d42e8978f09d845f62a17264ac1aa0`
- 原始 ARM64 supervisor SHA-256：`d94630658eb1e62090281160db7cdc542c8cf6667d0c11ff7d9084251f86cfd6`

`0001-landlock-mask-file-access.patch` 修复 v0.0.83 supervisor 的失败关闭启动问题。上游代码会把仅目录适用的 Landlock 权限传给所有路径，包括 `/dev/urandom` 和 `/dev/null`。在 `hard_requirement` 模式下，`landlock 0.4.4` 会在内核看到规则前拒绝这些文件规则。

补丁会检查已经打开的 `O_PATH` 文件描述符。目录规则保留上游 mask；非目录规则与 `AccessFs::from_file(ABI::V2)` 取交集。它不会放宽 `/dev`，不会改变网络行为，也不会把 `hard_requirement` 降级为 `best_effort`。

补丁还增加目录、普通文件、`/dev/null` 和 `/dev/urandom` 的回归测试。构建并安装：

```bash
scripts/openshell/build_patched_supervisor.sh
```

构建器使用 `python:3.11.15-slim-bookworm` 的固定 ARM64 manifest，并从官方带日期的发布归档安装 Rust 1.95.0。归档 SHA-256 是 `094c9c36531911c5cc7dd6ab2d3069ab8dcd744d6239b0bda1387b243dfc391e`。生成的构建镜像带有该 digest、OpenShell commit 和本补丁 digest 标签；构建脚本会在执行 Cargo 前验证三者。

`0002-siq-strict-bind-mount-contract.patch` 增加 gateway 侧的 `siq_analysis_v1` bind-mount 合同。启用后，Docker driver 只接受无 host mounts，或严格等于固定 12 挂载 SIQ 计划：一个只读 Wiki root，一个同路径任务 `analysis` 目录，以及来自单个 Hermes runtime snapshot 的十个读写文件/目录。它会拒绝项目根、项目外路径、symlink、`..`、Docker socket、TLS/control state、任意 volume/tmpfs、混合 run 和 mode/target 混淆请求。

gateway 使用已验证 supervisor builder 的 metadata-only 子镜像，并用 Cargo `bundled-z3` feature 编译 Z3 policy prover。生成的 gateway 不得依赖宿主 `libz3.so`。构建并安装：

```bash
scripts/openshell/build_patched_gateway.sh
```

严格合同只有在项目 gateway template 同时设置 `enable_bind_mounts`、`bind_mount_contract` 和 `bind_mount_project_root` 后才会生效。

源码、Cargo cache、构建输出、上游备份和可执行文件仍位于被忽略的 `var/openshell/`。补丁及其非敏感 provenance 会提交用于评审和可复现性。
