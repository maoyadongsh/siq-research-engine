# OpenShell 与 Hermes 集成参考

## 固定基线

SIQ 固定 OpenShell `v0.0.83`（commit `e3d26dd3ae0dee247bbc5db368545832757ac493`），CLI、gateway 和 sandbox supervisor 必须同版本。Hermes 继续使用 SIQ 已验证的 `0.13.0`、当前 commit 和本地补丁，不升级。

官方依据：

- [OpenShell v0.0.83 release](https://github.com/NVIDIA/OpenShell/releases/tag/v0.0.83)
- [OpenShell support matrix](https://github.com/NVIDIA/OpenShell/blob/v0.0.83/docs/reference/support-matrix.mdx)
- [OpenShell BYOC example](https://github.com/NVIDIA/OpenShell/tree/v0.0.83/examples/bring-your-own-container)
- [OpenShell gateway management](https://github.com/NVIDIA/OpenShell/blob/v0.0.83/docs/sandboxes/manage-gateways.mdx)

## 可借鉴的官方做法

1. 自建 BYOC 镜像，固定 Hermes 源码、SIQ 补丁和依赖，不依赖浮动 `main`。
2. OpenShell gateway 只承担 sandbox 生命周期、policy、provider 和 inference 控制；Hermes API 作为 sandbox 内服务，通过 `openshell forward` 暴露。
3. 凭据保留在 gateway/provider 侧，sandbox 只访问 `inference.local` 或经批准的代理端点。
4. profile、Prompt、skills、workflow 和代码只读；session、memory、checkpoint、SQLite/WAL 与任务输出按路径精确可写。
5. filesystem/process 策略在 sandbox 创建时锁定；network/inference 才允许运行时更新。
6. gateway、sandbox、policy、模型路由和 host runtime 回退分别验收，不能把 `doctor check` 当成整体健康检查。

GitHub 上还存在多个直接 OpenShell + Hermes 或控制器集成项目。逐仓库、固定
commit 的核验结果见 `github-hermes-openshell-projects.md`。这些项目证明“不使用
NemoClaw 也能把 Hermes 运行在 OpenShell 中”，但其安全默认值不能替代 SIQ 的
路径级 policy、只读数据 broker 和上传门禁。

## NemoClaw 不能直接复用的部分

- [NemoClaw v0.0.83 blueprint](https://github.com/NVIDIA/NemoClaw/blob/v0.0.83/nemoclaw-blueprint/blueprint.yaml) 将 OpenShell 固定为 `0.0.72`，不能直接驱动 SIQ 的 `0.0.83`。
- [NemoClaw Hermes Dockerfile](https://github.com/NVIDIA/NemoClaw/blob/v0.0.83/agents/hermes/Dockerfile.base) 固定 Hermes `0.18.0`，与 SIQ 冻结 `0.13.0` 冲突。
- [NemoClaw Hermes quickstart](https://docs.nvidia.com/nemoclaw/latest/user-guide/hermes/get-started/quickstart) 只说明端口 `8642` 的 OpenAI-compatible API，没有覆盖 SIQ 的 `/v1/runs`、stream、collect、stop 契约。
- NemoClaw Hermes plugin 会在 `pre_llm_call` 注入上下文并新增工具和 hook，可能改变输出与工具选择；SIQ PoC 不安装该 plugin。
- `inference.local` 是 gateway 级的单 provider/model 路由。SIQ 的 StepFun、Kimi、Qwen、Gemma fallback 需要宿主模型路由器，不能假设 OpenShell 自动保留现有 fallback 链。

## CSDN 文章评估

参考文章：[《基于OpenShell硬件沙箱与Hermes Agent构建安全可控的本地AI智能体》](https://blog.csdn.net/weixin_42521558/article/details/160804027)。

可借鉴：

- 分层考虑网络、文件系统、进程和凭据，不把安全寄托在 Hermes 自身判断上。
- 用 `inference.local` 隔离真实模型凭据和后端地址。
- 将 Hermes 状态与临时工作区分开，便于持久化、快照和回退。
- 按任务切换网络能力，研究任务宽放行、敏感任务收紧。

需要修正：

- OpenShell 不能统一称为“硬件级沙箱”。当前 SIQ 计划使用 Docker、Landlock、进程隔离和网络代理；MicroVM/GPU 是可选部署形态。
- 文章提到的 OPA、`strict/gateway/permissive` 是特定社区实现描述，不是 SIQ `v0.0.83` 的稳定接口契约。
- 不是所有 policy 都能热切换。filesystem/process 是静态策略，修改后必须重建 sandbox。
- SIQ 不能只开放 `~/.hermes`、`/sandbox` 和 `/tmp`；必须保持原项目绝对路径，并对 task-scoped `analysis` 与 Hermes 状态做精确写授权。
- 项目根只读不会隐藏 `.env` 或 `auth.json`。必须使用通过 `scripts/openshell/check_mount_safety.py` 的 staged mount，禁止原样挂载当前仓库根。
- “agent 被完全攻破也无法绕过”属于过度承诺。Docker socket、gateway 控制面、内核/容器漏洞和错误 mount 仍需要独立防护与回退。

## SIQ 最小 PoC 门槛

1. 独立 `siq-openshell-dev` gateway，仅监听 loopback，mTLS、数据库和 XDG 状态全部位于 Git 忽略的 `var/openshell/`。
2. 使用最小脱敏 staged mount 和当前 Hermes，不安装 NemoClaw plugin，不加载 SIQ 真实凭据。
3. 单模型、单测试工具先验证 health、`/v1/runs`、stream、collect、stop、Python/shell 和 policy deny。
4. PoC 通过后才接 `siq_analysis`，再验证同名绝对路径、task-scoped 写入、immutable 写拒绝、memory 和模型 fallback。
5. `start_all.sh` 默认继续使用 host runtime，A/B 质量门槛通过后才允许显式切换。
