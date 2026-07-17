# SIQ OpenShell V0.6 安全基线

基线时间：2026-07-15
状态：通过；裸 Hermes sandbox PoC 已完成并回滚，尚未接入 SIQ `siq_analysis` 流量。

## 冻结结论

- Hermes 保持 `0.13.0 (2026.5.7)`，升级已冻结；
- 本次证据刷新时 SIQ HEAD 为 `75c052546773be1a053ff0c62c93c5d5f2b853e3`；工作树中已有并保留其他开发改动，因此本文件只证明 OpenShell/Hermes 前置基线，不宣称完整仓库处于可复现 clean commit；
- Hermes 固定在 source commit `ddb8d8fa842283ef651a6e4514f8f561f736c72e` 加 SIQ 本地补丁；
- 回退 bundle、工作树 patch 和未跟踪文件归档已在临时目录验证可用；
- 现有 `nemoclaw` gateway 不可用且不得复用、升级或销毁；
- 后续 OpenShell 只能使用独立 `siq-openshell-dev` 名称、端口、数据库和 XDG 根目录。
- OpenShell v0.0.83 项目内 CLI/gateway 已安装；sandbox supervisor 使用经过审查的 Landlock 文件规则补丁。

## 验证结果

| 检查 | 结果 |
| --- | --- |
| OpenShell `doctor check` | 通过 |
| 现有 OpenShell `sandbox list` | 失败：`InvalidContentType`，已记录，不修复旧 gateway |
| SIQ Hermes/API 合同 | 63 passed |
| Hermes 本地补丁合同 | 145 passed，34 个 aiohttp warning |
| IC R1 profile dry-run | 6/6 allowed，无真实模型调用 |
| Hermes gateway health | 12/12 HTTP 200 |
| SIQ API health | HTTP 200 |
| Hermes 回退演练 | 通过 |
| OpenShell 项目专项测试 | 61 passed |

## 裸 Hermes PoC

2026-07-15 在独立 `siq-openshell-dev` gateway 完成 `hermes-minimal` / `Hermes 0.13.0` ARM64 sandbox 验证，随后删除 sandbox、移除临时 API key 与 run nonce，并确认宿主 Hermes 服务和 `28642` 端口恢复原状。该 PoC 不是 `siq_analysis`，也不构成输出质量结论。

| 检查 | 结果 |
| --- | --- |
| supervisor Landlock 单元测试 | 15 passed |
| supervisor 官方回退并重新安装补丁 | 通过 |
| `/v1/runs` 无 key / 错 key | 均拒绝，HTTP 401 |
| `/v1/runs` 正常输出 | `completed` |
| 分片 terminal tool call | `completed` |
| 并发 SSE stop | `run.cancelled` / `cancelled` |
| 非 root 与精确设备规则 | 通过 |
| 代码只读、运行时目录写入 | 通过 |
| `/dev/zero`、公网 TCP | 拒绝 |
| Docker 控制挂载 | 5 个，全部只读且在白名单 |
| sandbox 测试后状态 | 无 |

启动与停止使用一次性 gateway label nonce，并将 gateway 返回的 sandbox ID、固定名称和 `siq-openshell-dev` namespace 与 Docker 管理标签交叉核验后才允许删除。脚本、合同测试和 fixture 的 SHA-256 已写入脱敏 JSON 证据；nonce 本身不提交。

候选 supervisor SHA-256：`d88f7a288e82ce8243216883ba8389524f31ff501a22d8b3555962a67b68cc57`；上游备份仍为 `d94630658eb1e62090281160db7cdc542c8cf6667d0c11ff7d9084251f86cfd6`。PoC 镜像和 fixture 摘要记录在 `var/openshell/manifests/toolchain.sanitized.json`，不提交镜像层、TLS、token 或原始日志。

## 已知差异

所有 Hermes source profile 与当前 runtime `config.yaml` 均存在差异。`siq_analysis` 当前 runtime 主模型与 source 配置不同，因此 OpenShell PoC 必须复制并冻结实际 runtime 配置，不能通过强制 profile sync 生成测试环境。

本机模型端点也不是全部在线：最终复查时只有 embedding `8013` 监听，Qwen `8004`、Gemma `8006` 与 Nemotron `8007` 均未监听。后续 fallback 失败不能直接归因于 OpenShell；进入 T5 前必须恢复这些端点或明确调整路由基线。

## 安全边界

本基线不包含环境变量、token、DSN、请求正文、用户输入输出或原始日志。真实备份保存在 Git 忽略目录，并使用仅当前用户可访问的权限。

当前只允许继续：

- immutable path registry 的离线生成与测试；
- OpenShell policy 模板和编译器的离线开发；
- BYOC Dockerfile 的静态构建定义和依赖审查。
- `siq_analysis` 单 profile 的路径、模型路由和只读 registry 灰度设计；默认 host runtime 仍保持不变。

当前禁止：

- Hermes 升级或现有 venv 变更；
- OpenShell 全局安装或官方破坏性升级脚本；
- 操作现有 `nemoclaw` gateway；
- 将任何 SIQ 生产/默认流量切换到 OpenShell；裸 PoC 使用的 API key 仅为运行时临时值，不得复用。
