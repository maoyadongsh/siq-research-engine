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

## 为什么投研智能体需要 OpenShell

Hermes 智能体需要读取公司材料、查询数据库、调用搜索、运行计算脚本并写出报告。单纯把进程放进 Docker 只能提供粗粒度容器边界，无法表达“只读当前公司事实、只写当前公司分析、可访问特定 Provider、禁止读取凭据、切换公司必须换工作空间”这类业务权限。SIQ 将这些要求编译为 OpenShell scope、mount、network/provider、broker、lease 和 audit 合同。

| 风险 | OpenShell/SIQ 控制 | 业务效果 |
| --- | --- | --- |
| 智能体误改原始事实 | 固化 Wiki/report 只读；只开放当前公司 `analysis/` 写入 | 报告生成不能污染 evidence package |
| 跨公司数据混流 | scope key + owner + conversation generation + company binding | 切公司产生新沙箱代际，旧工作区不复用 |
| 凭据泄露 | Provider placeholder/网关注入，agent 环境无真实 token | 可审计地调用模型/搜索而不暴露密钥 |
| 任意出网 | exact host/port/method/path policy、egress broker | 降低数据外传与 SSRF 面 |
| 数据库越权 | 只读 PostgreSQL/Milvus broker、SQL grammar/collection/field allowlist | 工具可以查证据，不能写库或无限查询 |
| 并发写冲突 | request lease、单 scope writer、waiter/orphan recovery | 同公司任务不会无序覆盖分析产物 |
| 沙箱泄漏 | terminal state + write quiescence + idle TTL | 请求结束后可复用热沙箱，空闲后自动回收 |
| 质量不可比较 | runtime origin receipt + Host/OpenShell A/B artifact | 安全改造不掩盖报告质量回归 |

## 运行生命周期

```text
请求进入
  -> API 验证 user/profile/conversation/company context
  -> 计算 scope 与 sandbox generation
  -> 查找可复用 READY sandbox；否则自动创建
  -> 获取 request lease / single-writer ownership
  -> OpenShell gateway 转发 Hermes /v1/runs
  -> Provider/Broker 注入签名请求身份
  -> SSE / stop / terminal state 保持原 SIQ 协议
  -> terminal + write quiescence 后释放 lease
  -> API 重启时从 registry/coordination state 恢复
  -> 无 active/waiting/orphan lease 且超过 idle TTL 后删除
```

公司上下文缺失、scope 不合法、sandbox 不健康或 lease 无法安全获取时，不应“尽量创建一个宽松沙箱”。控制面按配置拒绝、隔离或回退 Host，并返回可解释的 runtime origin/fallback receipt。

## 文件与数据边界

正式候选运行面把目录按用途分成三类：

- 固化输入：公司 Wiki、reports、metrics、evidence、profile seed 和运行配置，挂载为只读并校验 hash。
- 任务写入：当前公司 `analysis/`、Hermes session/checkpoint/memory 所需的明确路径；删除操作受 root/批量阈值守卫。
- 永不暴露：仓库源码写权限、Prompt/workflow 配置写权限、其他公司资料、宿主凭据/TLS/数据库文件、未批准的运行目录。

PostgreSQL 与 Milvus 不直接挂进沙箱。宿主 broker 只接受签名请求身份，并施加只读角色、statement timeout、row/response size、collection、field、filter grammar 和函数限制。这样即使工具生成错误 SQL，也不能退化成任意数据库访问。

## Provider 与网络治理

Provider 配置固定到已审阅的 OpenShell `v0.0.83` 子集。每个 provider 明确声明 endpoint、credential env placeholder、auth style、允许的 REST method/path 和可执行 Hermes Python。Tavily 等 body credential rewrite 还有独立大小边界；未注册 host、任意 wildcard 路径和非审阅 method 会在 provision 阶段失败。

Provider/broker 的存在不等于允许智能体把任意材料发出。业务 Prompt、数据 scope、请求身份和网络 policy 共同约束调用，审计产物只保留脱敏后的必要元数据。

## 成熟度与门禁

| 状态 | 能说明什么 | 不能说明什么 |
| --- | --- | --- |
| PoC/observe 通过 | BYOC Hermes、鉴权、SSE、terminal、取消和清理可运行 | 不能证明正式数据边界、质量或生产稳定性 |
| wide pilot 通过 | 真实公司只读 Wiki、7+5 mounts、broker identity 和限定写入可运行 | 不影响正式 readiness，不允许默认流量 |
| canary 可用 | 可做真实小流量、完整 `analysis/` 业务路径验证 | 不等于正式 GO |
| `check_v06_completion.py` 为 `GO` | 机器证据、A/B、质量门与人工评审满足发布合同 | 仍需按发布流程和回滚预案切流 |

`siq_analysis` 分析助手的功能结论是“已全面跑通”：真实前端请求已经经过 scope resolve/auto-provision、OpenShell sandbox 内 Hermes、SSE、lease、generation、终态释放和 TTL 回收。正式质量发布结论仍是 `NO_GO`，只表示尚未完成全套正式 A/B、人工评审和可发布证据，不能据此否定已经跑通的分析助手链路。

当前能力已经从手工灰度演进到按公司范围自动创建沙箱、同对话代际复用/隔离、请求级租约、API 重启恢复和空闲 TTL 回收。对 `siq_analysis` 而言，它是已完成真实端到端验证的安全运行面；对全局生产发布而言，它仍未获得 formal quality gate 的 `GO`。

当前约束：

- Hermes 固定在 SIQ 当前版本和本地补丁，不做同步升级；
- 不引入 NemoClaw；
- 不运行 OpenShell 官方就地升级脚本；
- 不操作现有 `nemoclaw` gateway；
- 后续 gateway 使用项目专用名称、端口和 XDG 根目录；
- 真实 token、TLS 私钥和运行数据库只能位于被 Git 忽略的 `var/openshell/` 子目录；日志可以作为参赛证据提交，但必须先导出脱敏副本并通过 tracked artifact manifest，OpenShell 的其他非敏感源码、策略、模板、测试和文档默认直接进入 Git。

OpenShell 首期最小 Hermes profile 已通过并回滚；正式 `siq_analysis` 镜像、单任务生命周期、服务/broker 预检查、编译运行配置 hash 门禁、宿主 Publisher、宿主 brokers 和只读 PostgreSQL 已完成实现。`start_all.sh` 会启动或复用项目网关，并在本机私有 reader 配置存在时以严格请求身份管理 brokers；Host 是环境回退基线，`siq_analysis` 则可按运行选择进入 OpenShell，且真实前端业务链已经跑通。Milvus 沙箱写保护边界和 NOT_PRODUCTION 真实业务路径宽松 pilot 已实测通过；后者验证当前候选镜像的 7+5 挂载、固化输入只读、唯一派生叶写入、Tavily、Bearer 保护的 `/v1/runs`、SSE 和清理闭环。Exa 凭据仍缺失，8004/8006 回退未启动，正式 A/B 与质量门也尚未完成，因此不能执行全局默认生产切流。这不是单智能体架构限制；后续每个 profile 仍应使用独立沙箱/策略/状态逐个灰度。

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
