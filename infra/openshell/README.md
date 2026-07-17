# SIQ OpenShell 基础设施

本目录保存可审查、可版本化且不含凭据的 OpenShell policy、schema、BYOC 定义、provider 模板、broker 组件、patch 和 PoC 说明。运行时状态不得写入这里；运行态在 `var/openshell/`，脱敏证据在 `artifacts/openshell/`。

## 架构口径

SIQ 当前采用的是自研 NVIDIA OpenShell + Hermes 集成，而不是 NemoClaw / NemoHermes 运行路径。核心设计目标是保留 SIQ 已验证的 Hermes `/v1/runs`、SSE、停止、报告路径、profile、Prompt 和工具合同，把不可信执行面放入 OpenShell 网关管理的沙箱中。

```text
apps/api 运行面选择
  -> 公司范围自动创建
  -> 对话沙箱代际
  -> 资源池注册表 / 租约 / 隔离 / 恢复
  -> OpenShell Gateway `siq-openshell-dev`
  -> BYOC Hermes sandbox
  -> Provider / broker / policy / forwarding
  -> Host 回退 / 空闲 TTL 清理
```

已经落地的基础设施能力：

| 能力 | 目录或组件 | 说明 |
| --- | --- | --- |
| 版本冻结 | `upstream-version.json`、`patches/v0.0.83/` | 固定 OpenShell `v0.0.83`、上游 commit 和本地 Landlock 补丁 |
| BYOC Hermes | `sandbox/`、`poc/siq-analysis-*` | 在自定义镜像中运行冻结 Hermes，并保留 SIQ API 合同 |
| Policy / mount | `policies/`、`guards/` | 固化输入只读、当前公司写入边界、跨公司拒写和删除守卫 |
| Provider / Broker | `providers/`、`brokers/`、`egress/`、`data-broker/` | OpenShell Provider 凭据隔离，宿主出网/数据 broker 和请求身份 |
| 审计 / 评测 | `audit/`、`eval/` | 脱敏审计、Host/OpenShell A/B 和正式门禁证据契约 |

当前能力已经从手工灰度演进到按公司范围自动创建沙箱、同对话代际复用/隔离、请求级租约、API 重启恢复和空闲 TTL 回收。它是演示/灰度控制面，不是正式生产 GO。

当前约束：

- Hermes 固定在 SIQ 当前版本和本地补丁，不做同步升级；
- 不引入 NemoClaw；
- 不运行 OpenShell 官方就地升级脚本；
- 不操作现有 `nemoclaw` gateway；
- 后续 gateway 使用项目专用名称、端口和 XDG 根目录；
- 真实 token、TLS 私钥和运行数据库只能位于被 Git 忽略的 `var/openshell/` 子目录；日志可以作为参赛证据提交，但必须先导出脱敏副本并通过 tracked artifact manifest，OpenShell 的其他非敏感源码、策略、模板、测试和文档默认直接进入 Git。

OpenShell 首期最小 Hermes profile 已通过并回滚；正式 `siq_analysis` 镜像、单任务生命周期、服务/broker 预检查、编译运行配置 hash 门禁、宿主 Publisher、宿主 brokers 和只读 PostgreSQL 已完成实现。`start_all.sh` 会启动或复用项目网关，并在本机私有 reader 配置存在时以严格请求身份管理 brokers，但 Hermes 仍固定使用 Host 运行面。Milvus 沙箱写保护边界和 NOT_PRODUCTION 真实业务路径宽松 pilot 已实测通过；后者验证当前候选镜像的 7+5 挂载、固化输入只读、唯一派生叶写入、Tavily、Bearer 保护的 `/v1/runs`、SSE 和清理闭环。Exa 凭据仍缺失，8004/8006 回退未启动，正式 A/B 与质量门也尚未完成，因此不能切换默认流量。这不是单智能体架构限制；后续每个 profile 仍应使用独立沙箱/策略/状态逐个灰度。

正式 filesystem boundary schema 和 attach-only runner 已实现。它只在已有 formal
transaction 内验证固化数据/控制面拒写和 analysis/session/memory 文件面可写，不调用
模型或外网；当前没有 active formal transaction，因此尚无正式 GO 证据。证据契约与执行
流程见 `docs/runbooks/openshell/formal-filesystem-boundary.md`。

为尽快验证真实 `siq_analysis` 集成，`poc/siq-analysis-observe/` 提供独立的
NOT_PRODUCTION observe-only 路径。它复用正式镜像中的只读 profile seed，把可写 Hermes
状态放在 sandbox `/sandbox` 中，只绑定一个已配置 provider，不挂载宿主业务数据，也不
修改 `start_all.sh` 或正式 entrypoint 的断言。2026-07-16 的真实验证已经跑通鉴权、
`/v1/runs`、SSE、terminal 工具、完成和取消链路，并完成无残留清理；脱敏证明位于
`artifacts/openshell/v0.6/siq-analysis-observe-20260716/`。该结果只证明集成可行性，
不替代正式数据挂载、安全门禁、质量 A/B 或默认流量切换。

`poc/siq-analysis-wide/` 在该可行性结果上增加一个真实公司只读 Wiki mount、正式
七挂载运行态、严格 broker 请求身份、删除守卫和唯一 `analysis/.work/pilot-*` 写入
leaf。它只使用当前已配置的 MiniMax/StepFun/Kimi/Tavily 子集，并直接执行一次只返回
状态和数量的 Tavily provider probe；Exa、8004/8006 和正式 sandbox 网络证据仍保留为
blocker。Clash fake-IP 兼容已经完成宿主 egress broker 组件级实证，但尚未证明项目重启
持久生效，也不替代正式 sandbox 直连旁路测试。该路径同样固定为 NOT_PRODUCTION、`readiness_effect=none`，
不得被完成门禁或 A/B 当作 GO。2026-07-16 当前候选镜像的真实 smoke 已通过，脱敏
辅助证据位于 `artifacts/openshell/v0.6/siq-analysis-wide-pilot-20260716/`。

`poc/siq-analysis-canary/` 是后续真实小流量入口。它复用上述镜像、7+5 挂载、
provider 子集、broker identity、sandbox 身份验证、forward 和删除守卫，但把任务写权限
放宽到当前公司既存的完整 `analysis/` 根，使解析、checkpoint、图表、派生报告及正常
清理保持原业务路径。公司事实/固化 reports、其他公司、项目代码配置 Prompt/workflow
仍不可写；只有根删除和超过固定数量/比例阈值的批量删除被守卫终止。该 lifecycle 不要求
Exa、8004/8006 或 Milvus formal proof，不影响正式门禁，也不会自行切换默认 host 流量。

固定上游版本见 `upstream-version.json`，Hermes、NemoClaw 和二手文章的参考做法与 SIQ 差异见 `reference/hermes-integration-notes.md`。
