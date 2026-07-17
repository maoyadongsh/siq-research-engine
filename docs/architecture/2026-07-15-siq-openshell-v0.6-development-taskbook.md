# SIQ OpenShell 宽松安全门禁 V0.6 开发任务书

> 状态：可信内部公司级 OpenShell canary 已承接两家已注册公司的真实 `siq_analysis` 流量；同公司跨进程 FIFO 单写、跨公司并行、显式认证用户 principal 和 API 恢复均已接入。Host 仍是默认 runtime，未注册公司和其他 profile 保持 Host。正式 A/B、正式安全证据和人工评审尚未完成；`check_v06_completion.py` 当前为 `NO_GO`，仅通过 `1/13`，不能把 live canary 误报为 V0.6 正式发布完成。
> 日期：2026-07-17
> 适用仓库：`/home/maoyd/siq-research-engine`
> 目标执行者：Codex 或具备仓库读写权限的开发人员
> 本期定位：在不改变智能体正常输出路径、工具行为和回答质量的前提下，将 OpenShell 作为 Hermes 执行面的最后一道安全保险。

> 2026-07-15 决策：Hermes 升级冻结，当前版本和 SIQ 本地补丁作为不可变兼容基线。已完成脱敏基线、回退演练、immutable registry、首个 `siq_analysis` policy 编译器、项目内 OpenShell v0.0.83 工具链和裸 Hermes sandbox 验证；未切换任何 SIQ 流量。

> 2026-07-16 实施快照：正式 ARM64 BYOC 镜像及断网 smoke 已通过；项目 gateway `siq-openshell-dev` 和 `18792/18793` 两个 host broker 健康；broker lifecycle 已解除“启动解释器等于检查解释器”的错误耦合，同时保留 PID/start ticks/exact argv/command digest/listener/bridge/key/pidfd 复验，`start_all.sh` 固定要求 strict request identity。PostgreSQL 六库专用只读角色已实连验证；MiniMax、StepFun、Kimi、Tavily provider 已配置，Exa 延期配置。`8004/8006/8007/8013` 固定保留为内部服务白名单，当前 `8007/8013` 在线、`8004/8006` optional disabled。provider-independent probe 已验证 7 个业务挂载加 5 个控制挂载、事实面/控制面只读、任务 analysis/session/memory 文件面可写、未知文件上传拒绝和清理闭环。独立 NOT_PRODUCTION `siq-analysis-observe-poc` 已跑通最小 Hermes 链路；其后 wide pilot 又以当前候选镜像挂载真实公司路径，验证固化 `company.json` 只读、唯一 `analysis/.work` 叶目录可写、runtime state 可写、四 provider placeholder、Tavily 真实检索、Bearer 保护的 `/v1/runs`、SSE terminal 完成、源文件不变、派生输出删除、sandbox/端口/临时身份清理和宿主 Hermes identity 不变，并发布脱敏辅助证据。Milvus boundary proof 已通过真实 OpenShell sandbox 验证：知识集合 `describe/get/query/search` 可用、直连 `19530` 和 mutation 路由拒绝、业务行修改数为 0、身份和临时状态清理完成，并发布脱敏证据。宿主 memory service 的 `siq_agent_memory_active` alias 已完成一级/二级市场合成记录 `upsert/get/delete` 实测，PostgreSQL `agent_memory` schema 已完成 `insert/readback/rollback` 实测且两者残留均为 0；两组 runtime 共用 memory alias，以 `agent_group` 和 ACL 字段隔离，sandbox 不获得写凭据。lifecycle 已接入 transaction v2、fail-closed recover/repair、pidfd、精确 Host receipt、正式服务/broker preflight、编译 runtime config 与候选镜像 hash 绑定；固化 `_index.json` 已改为 host Publisher（sandbox 内延期，stop 后由 host 侧固定路径发布）。宿主 `siq_assistant` 观察到 Kimi 认证不可用后 fallback，以及一次 SSE transport reset；没有修改模型、fallback、凭据或 Prompt 来掩盖该基线问题。仓库 Nginx 模板已为通用 Agent SSE 设置 `1900s` 读写超时，但外部 Synology 反代未验证。wide pilot 的 `readiness_effect` 明确为 `none`；声明启用能力的正式证据、正常 A/B、独立 fallback drill、质量门和人工评审仍未完成，因此 `SIQ_HERMES_RUNTIME=host` 继续是默认且唯一自动流量路径，readiness 保持 `NO_GO`。Exa 和 optional disabled `8004/8006` 不构成该结论的 blocker。离线测试数量以当前 CI 输出为准，仍不替代正式业务验收。

> 2026-07-16 完成门禁加固：新增 attach-only `run_formal_filesystem_boundary.py`，只在已有正式 `running` transaction 内验证固化数据/代码/Prompt/workflow 拒写和 analysis/session/memory 文件面可写，不调用模型或公网。当前没有 active formal transaction，真实调用稳定返回 `formal_active_transaction_required`，且 raw/JSON/Markdown 均未生成。新增 `build_v06_readiness.py` 从现有证据确定性计算机器 readiness；人工评审改为 readiness 生成后的单向 completion gate，消除了 review/readiness SHA 循环。辅助 provider-independent probe 不再贡献正式文件控制结论；filesystem、rollback、delete、egress 必须共享 image/policy/mount provenance，正式可发布证据还必须与 Git stage-zero blob 完全一致。OpenShell 非敏感源码、配置、文档、测试、证据和脱敏日志默认允许提交；真实密码、API key、token、cookie、DSN、私钥及含私有业务正文的原件继续禁止。Exa 明确延期到切流后配置，`8004/8006` 当前禁用并作为 optional 服务记录，三者不再构成 readiness blocker；provider 模板与内部服务白名单继续保留，未来启用时必须补真实协议/fallback 验收。当前机器仍因 service/provider/broker 证据过期及缺正式 filesystem/delete/egress/audit/rollback/A-B 证据而保持 `NO_GO`。

> 2026-07-16 Canary 快照：API 路由已修复为读取 `SIQ_HERMES_RUNTIME`，显式 `host` 保留即时回滚，OpenShell create/connect 失败不再自动重放到无 sandbox 的 host。独立 `NOT_PRODUCTION_CANARY` 已在真实上汽集团路径完成 start/status/probe、受保护 `/v1/runs`、SSE `run.completed`、模型响应标记和 rollback 闭环；当前公司 `analysis/` 内 create/modify/rename/delete 通过，固化资产、控制面和跨公司拒写通过，清理后 sandbox 列表为空、`28651` 释放、临时 key 删除且宿主 Hermes/brokers 健康。宿主 lifecycle 现在只在已存在且无符号链接的公司根下创建缺失的 `analysis/` 叶目录，不创建公司或项目父目录。Milvus boundary proof 已刷新为 `GO` 且业务修改数为 0，service preflight 为 `GO`，完整 OpenShell 回归为 `970 passed`。该 canary 仍固定 `readiness_effect=none`；当前 `NO_GO` 仅保留正式 filesystem/delete/egress/audit/rollback/A-B 证据和 Git stage-zero 发布绑定缺口，默认 runtime 仍为 host。

> 2026-07-16 运行优先切流快照：canary runtime snapshot 已改为 fresh 模式，只复制编译后的无凭据配置；启动前数据库、sidecar、sessions、memories、checkpoints 和 cron 清单均为空，真实 Hermes 启动后自建空 SQLite，未复制 Host 历史上下文。API 新增 owner-only 原子热切换状态，`session_mode=all` 只开放会话资格，仍要求请求 `market/company` 与 active canary 精确匹配；公司投影进入 OpenShell session namespace，缺失或不匹配的隐式请求留在 Host，显式 OpenShell 请求拒绝。正式报告请求在范围匹配时不再被 API 绕到 Host workflow。真实 `18081 /api/analysis/chat/stream` 新建上汽会话已观察到 `runtime_target=openshell`、SSE done 和 sandbox namespace；贵州茅台不匹配会话已观察到 `runtime_target=host` 且正常完成，18 个项目/OpenShell HTTP 检查均为 200。该快照证明公司级运行优先切流可用，不代表多公司并发运行池或正式 readiness GO。

> 2026-07-16 运行时凭据例外：OpenShell sandbox JWT 与 mTLS client key 是 supervisor/gateway 通信的必需控制文件，允许在 sandbox 内读取，但只能通过身份校验的五个固定 control mounts 只读挂载；禁止修改、禁止扩大到其他宿主凭据根，且内容不得进入业务输出、审计日志或可发布证据。正式 filesystem probe 按该边界验证只读而不再要求不可读。

> 2026-07-17 当前权威运行快照：候选镜像 `siq/hermes-openshell-siq-analysis:aadca453805ef6d783956e93` 已滚动到两个 live canary。上汽集团为 `canary-8e695febb483`、宿主端口 `28652`，贵州茅台为 `canary-ee8d4d9c4e97`、宿主端口 `28653`；两者 lifecycle `status`、认证 `probe` 和 Docker health 均正常。API 报告 OpenShell pool recovery `ready`；Host 仍为进程默认值，未注册公司解析为 Host。`8004/8006` 继续 optional 且禁用，Exa 延期配置，三者不构成本轮 canary blocker。此前记录的 `formal-4a7a63f67995`、formal filesystem `GO` 和正式 host rollback 不作为当前可发布正式证据，completion 不得消费或据其推导 GO；当前机器门禁真实结果仍为 `NO_GO 1/13`。

## 1. 项目背景

SIQ Research Engine 当前由 FastAPI、React、文档解析服务、市场报告服务、PostgreSQL、Milvus 和多组 Hermes profiles 组成。Hermes 通过独立 `/v1/runs` gateway 与 API 通信，承担通用问答、智能分析、事实核查、持续跟踪、法务和一级市场 IC 多角色任务。

当前 Hermes profiles 普遍具备 `terminal`、`file`、`code_execution` 和 `web` 能力。部分 profile 会直接执行仓库中的 Python 脚本，并向以下路径写入派生产物：

- `data/wiki/companies/*/analysis/**`
- `data/wiki/companies/*/factcheck/**`
- `data/wiki/companies/*/tracking/**`
- `data/wiki/companies/*/legal/**`
- `artifacts/**`
- `var/**`
- `data/hermes/home/profiles/*` 下的运行时状态目录和数据库

与此同时，仓库代码、Hermes Prompt、skills、workflow 配置、入库后的 Wiki 事实包、原始文档和正式数据库事实不应被智能体直接修改。

本任务书要求引入 OpenShell，但不采用 NemoClaw，不重构 Hermes 的业务角色，不替换现有 `/v1/runs` 契约，不改变现有智能体可见的绝对路径。

## 2. 目标与非目标

### 2.1 必须实现的目标

1. Hermes gateway 运行在 OpenShell sandbox 中，SIQ API 继续通过现有 `/v1/runs` 接口调用。
2. 智能体读取项目代码、Prompt、skills、workflow 和固化数据的行为保持不变。
3. 智能体不能修改项目源代码、Agent 控制面文件及已固化入库数据。
4. 智能体仍可在现有路径生成分析、核查、跟踪、法务、任务和运行时产物。
5. 聊天、会议、任务、审计、Agent memory 和 Milvus memory 写入继续由宿主 FastAPI 正常完成。
6. 公开网页读取、模型调用、当前必需的 MiniMax、StepFun、Kimi、Tavily 四个 provider、已启用本地模型和 SIQ 内部服务正常放行；Exa 与禁用的 optional 服务只在实际启用后纳入同等门禁。
7. 明确的未知公网文件上传、系统提权、Docker socket 访问和 sandbox 逃逸行为被阻断。
8. 大范围破坏性删除被宿主侧守卫终止，并可从任务前快照恢复；正常单文件和当前任务清理不受影响。
9. 策略拒绝、网络请求和 sandbox 生命周期能够关联 SIQ `run_id`、`task_id`、profile 和 session。
10. 支持观察模式、灰度启用、快速回滚和 A/B 质量评测。
11. 外部检索结果可以完整进入任务工作面，但外部内容始终是无权限数据，不能修改代码、配置、Prompt、workflow、policy 或固化事实面。
12. Tavily、已启用模型 provider，以及未来启用后的 Exa 是显式批准的数据处理方；仅允许其业务协议需要的查询、Prompt 和 URL，不因此获得任意本地文件上传、账号管理或通用写入能力。

### 2.2 本期明确不做

1. 不引入 NemoClaw。
2. 不引入 NeMo Agent Toolkit，不改变多 Agent 调度逻辑。
3. 不将所有 SIQ 服务迁移到 OpenShell。
4. 不重构现有报告输出路径。
5. 不把所有公网访问改成严格域名白名单。
6. 不实现通用 DLP 或请求体语义识别系统。
7. 不实现任意单文件修改审批。
8. 不改变 Hermes 模型、Prompt 内容、temperature、上下文长度和 fallback 顺序。
9. 不修改现有数据库 schema，除非为审计记录增加独立、向后兼容的表或字段且获得单独评审。

### 2.3 OpenShell 项目内目录约束

所有由 SIQ 维护、生成或消费的 OpenShell 相关文件必须位于当前项目根目录下，不得把 SIQ policy、sandbox 定义、运行状态、日志或评测产物散落到 `~/.openshell`、`~/.config` 或其他个人目录。

规范目录结构如下：

```text
siq-research-engine/
├── infra/openshell/                 # 版本化基础设施源码
│   ├── README.md
│   ├── schemas/                     # registry、policy 辅助 schema
│   ├── policies/                    # base/profile policy 模板
│   ├── sandbox/                     # BYOC Dockerfile、entrypoint、依赖锁定
│   └── providers/                   # 无密钥 provider 模板
├── scripts/openshell/               # 启动、停止、诊断、编译、灰度和回滚命令
├── var/openshell/                   # 提交白名单管理的运行状态与可复现快照
│   ├── README.md                    # 提交：目录说明与数据分级
│   ├── manifests/                   # 提交：版本和资源清单的脱敏快照
│   ├── gateway/
│   ├── sandboxes/
│   ├── policies/
│   ├── registry/
│   ├── audit/
│   ├── logs/
│   ├── cache/
│   └── xdg/                         # wrapper 可定向的 XDG config/state/cache
├── artifacts/openshell/             # 基线、A/B、安全测试和发布证据
└── docs/runbooks/openshell/          # 运维、故障处理、升级和回滚说明
```

约束：

1. `infra/openshell/**` 和 `scripts/openshell/**` 必须进入版本控制，且对 sandbox 内智能体只读。
2. 除真实凭据值和 SIQ 私有业务正文外，OpenShell 源码、配置模板、策略、测试、文档、证据和日志默认可发布。`var/openshell/**` 因混放凭据与机器运行态而采用“默认忽略、脱敏导出、manifest 精确绑定”的提交策略；日志和运行证据导出到 `artifacts/openshell/**` 后进入候选范围。
3. `artifacts/openshell/**` 允许 baseline/readiness 以及 `*.sanitized.json|md` 进入候选范围；日志必须由专用导出器生成同目录成对的 `logs.sanitized.json|md`，自由格式 `.sanitized.log` 在具备逐行严格 schema 前不开放。候选文件必须登记在 `tracked-artifacts.json`，绑定分类、大小和 SHA-256，并由 tracked-state、内容扫描、large-file 和 CI 四道门禁复核；文件名本身不构成授权。
4. 所有管理命令必须通过项目 wrapper 设置 `SIQ_PROJECT_ROOT`、`SIQ_RUNTIME_ROOT`、`SIQ_ARTIFACTS_ROOT`，并在 OpenShell 支持时将 `XDG_CONFIG_HOME`、`XDG_STATE_HOME`、`XDG_CACHE_HOME` 定向到 `var/openshell/xdg/`。
5. wrapper 启动前必须打印实际使用的项目目录和 OpenShell 状态目录；检测到意外写入用户 home 时应告警或失败关闭。
6. 真实 API Key、token、数据库密码不得写入 `infra/openshell/providers/`；该目录只保存字段模板和 provider 名称。
7. OpenShell CLI 二进制保存在项目工作树内但由 Git 忽略的 `var/openshell/toolchains/<version>/bin/`，不纳入版本控制；Docker daemon 的镜像层、容器层和内核级运行时状态仍由操作系统或容器运行时管理。项目必须记录版本和资源 ID，但不得把外部状态伪装成可提交文件。
8. 如果当前 OpenShell 版本不支持重定向某项 CLI metadata，必须在 T0/T4 中记录该例外、实际路径、敏感性和清理方式；不得静默散落状态。
9. 任何准备提交的 `var/openshell/**` 或 `artifacts/openshell/**` 文件必须先通过自动脱敏和 secret scan；扫描失败时禁止提交。

提交策略如下：

| 内容 | 是否提交 | 处理要求 |
|---|---|---|
| policy 模板和稳定 profile 规则 | 是 | 不包含密钥和机器专属认证信息 |
| 编译后 policy 示例 | 是 | 归一化项目根为 `${SIQ_PROJECT_ROOT}`，移除本机资源 ID |
| immutable registry 示例 | 是 | 使用公开/合成样本或脱敏路径，保留 schema 和摘要算法 |
| OpenShell/Docker/Hermes 版本 manifest | 是 | 允许版本号和镜像 digest，不含私有 registry token |
| A/B 质量摘要和安全测试报告 | 是 | 不含 Prompt、用户输入、附件正文和凭据 |
| 审计汇总、计数和拒绝类型样例 | 是 | host/path 使用安全投影，ID 脱敏 |
| gateway 认证、provider credentials | 否 | 必须忽略，禁止生成可提交副本 |
| XDG auth/config 中的 token | 否 | 必须忽略 |
| socket、PID、lock、临时端口状态 | 否 | 机器绑定且无复现价值 |
| OpenShell 审计和运行日志 | 脱敏后是 | 原件留在 `var/openshell`；提交严格 schema 聚合或经专用导出器处理的脱敏日志 |
| sandbox filesystem、session DB、response store | 否 | 可能含 Prompt、记忆和用户内容 |
| 大体积镜像层和容器数据 | 否 | 由 Docker/OpenShell 管理，提交 digest 即可 |

建议 `.gitignore` 仅开放“候选文件形态”，实际授权由 tracked artifact manifest 完成：

```gitignore
var/openshell/**
!var/openshell/README.md
!var/openshell/manifests/
!var/openshell/manifests/*.sanitized.json
!var/openshell/manifests/*.sanitized.md

artifacts/openshell/**
!artifacts/openshell/README.md
!artifacts/openshell/tracked-artifacts.json
!artifacts/openshell/v0.6/baseline.json
!artifacts/openshell/v0.6/baseline.md
!artifacts/openshell/v0.6/readiness.json
!artifacts/openshell/v0.6/readiness.md
!artifacts/openshell/**/
!artifacts/openshell/**/*.sanitized.json
!artifacts/openshell/**/*.sanitized.md
```

实际实现必须同时提供 `build_tracked_artifact_manifest.py`、脱敏产物生成/审查流程、`check_sanitized_artifacts.py` 和 `check_tracked_state.py`，不能只依赖开发者人工判断。manifest 必须从 Git index 绑定并复核所有实际待提交 blob；多余、缺失、mode/size/digest 漂移或敏感内容均失败关闭。

## 3. 设计原则

### 3.1 宽松运行，保护控制面和事实面

权限分为四个平面：

| 平面 | 内容 | 权限 |
|---|---|---|
| 控制面 | 项目代码、Prompt、profiles、skills、workflow、安全策略、凭据 | 智能体只读或不可见 |
| 事实面 | 固化 Wiki、原始文档、事实数据库、正式向量集合 | 智能体只读 |
| 工作面 | analysis、factcheck、tracking、legal、artifacts、var、workspace | 智能体正常读写 |
| 状态面 | 聊天、会议、任务、Agent memory | 宿主 API 正常读写 |

业务正常运转优先于策略完备度。OpenShell 是业务执行面的最后一道高危行为保险，不是对正常
任务中每个命令、工具选择或中间文件的审批器。强制阻断面限定为：改写 immutable registry
固化数据，改写项目底层代码或 Agent 控制面，读取、修改或外传真实凭据，越权 egress 和明确
高风险文件上传，宿主提权、Docker socket 或 sandbox 逃逸，以及异常批量删除。正常的目录创建、
下载解析、子进程和工具调用、任务内清理、无害的非零退出、失败后重试或改用同等工具，默认应
放行并审计；一次工具失败后成功恢复，不得仅因历史 `tool error` 被归类为策略拒绝或业务任务
失败。工具调用错误率、重试率和恢复率作为独立运行指标记录，不能替代业务结果质量判断。

该宽松口径只扩大正常业务行为面，不改变 6.7 的用户信任边界：当前长驻模式只适用于可信
内部单租户公司级 canary；互不信任用户或多个租户仍必须使用 principal-bound fresh runtime
epoch 或 per-lease fresh sandbox。

### 3.2 路径兼容优先

Hermes 在 sandbox 中继续看到：

```text
/home/maoyd/siq-research-engine
```

不得要求修改 AGENTS.md、Prompt 或业务脚本中的路径。OpenShell/BYOC 镜像、挂载或文件映射负责保持路径一致。

### 3.3 不把宿主应用数据库整体设为只读

`SIQ_APP_DATABASE_URL` 当前承载认证、聊天、Agent memory、会议、任务租约、quota 和审计。宿主 FastAPI 必须保留现有写权限。

Sandbox 内 Hermes 仅获得市场事实查询所需的只读连接。不得向 sandbox 暴露宿主 API 的通用写账号。

### 3.4 固化状态由 manifest 决定

是否只读不能只依赖目录名称。只有通过入库 finalization、拥有稳定身份和内容摘要的路径才进入 immutable registry。staging 路径仍由入库 worker 管理。

### 3.5 默认保持质量，拒绝必须可解释

策略拒绝应返回结构化、不可重试或可替代的错误，避免 Hermes 反复调用工具。例如：

```json
{
  "error": "policy_denied",
  "operation": "filesystem.write",
  "path": "/home/maoyd/siq-research-engine/apps/api/main.py",
  "retryable": false,
  "allowed_alternative": "write generated artifacts under artifacts/ or the profile output directory"
}
```

质量是切换前置条件，不是上线后的观察指标。`SIQ_HERMES_RUNTIME=host` 必须保持默认，直到真实 A/B 证明 OpenShell 路径在同一 Hermes 版本、模型、temperature、Prompt、输入、数据和 fallback 顺序下没有业务质量回退。任何正常工具调用、memory/session 写入、检索、流式输出、stop 或 reconnect 出现策略误拒绝时，readiness 必须保持 `NO_GO`；不得用自动降级到无门禁 host runtime 掩盖失败。

## 4. 当前架构约束

### 4.1 Hermes 启动链

当前启动入口：

- `start_all.sh`
- `scripts/hermes/run_gateway.sh`
- `scripts/hermes/profile_dir.sh`
- `infra/systemd-user/hermes-gateway-siq@.service`
- `infra/systemd-user/hermes-gateway-siq-ic@.service`

`run_gateway.sh` 会先将 `agents/hermes/profiles/<profile>` 同步到 `data/hermes/home/profiles/<profile>`，然后执行：

```bash
hermes gateway run --replace --accept-hooks
```

OpenShell 接入必须保留同步语义，推荐先在宿主完成 profile materialization，再启动 sandbox。

### 4.2 API 与 Hermes 契约

`apps/api/services/hermes_client.py` 按 profile 调用 `/v1/runs`，并支持 create、stream、collect 和 stop。不得改变前端和 API 上层调用契约。

只允许通过环境变量将 profile 的 runs URL 指向 OpenShell 转发端口。

### 4.3 本地模型地址

多个 profile 使用：

```text
http://127.0.0.1:8004/v1
http://127.0.0.1:8006/v1
```

进入 sandbox 后，`127.0.0.1` 指向 sandbox 自身。必须通过可路由宿主地址、OpenShell inference route 或内部 DNS alias 解决，不得静默失去 fallback。

### 4.4 Agent memory

Hermes 原生 memory 在多数 profile 中关闭。SIQ memory 由宿主 API 的以下模块管理：

- `apps/api/services/agent_memory_service.py`
- `apps/api/services/agent_memory_milvus.py`
- `apps/api/services/agent_runtime_memory.py`
- `apps/api/services/agent_chat_runtime_impl.py`

OpenShell 接入不得阻断这些宿主写入。

## 5. 目标架构

```text
Browser / Client
       |
       v
SIQ FastAPI --------------------------------------+
  | chat / workflow / memory / audit              |
  |                                               |
  +--> PostgreSQL app schema (read-write)          |
  +--> agent_memory schema (read-write)            |
  +--> Milvus memory collection (read-write)       |
  |                                               |
  +--> /v1/runs via forwarded port                 |
             |                                     |
             v                                     |
       OpenShell Gateway                           |
             |                                     |
             v                                     |
       Hermes Profile Sandbox                      |
       - project/control files read-only           |
       - finalized facts read-only                 |
       - derived outputs read-write                |
       - runtime state read-write                  |
       - governed network egress                   |
             |                                     |
             +--> cloud LLM/search allowlist       |
             +--> local model aliases              |
             +--> read-only market PostgreSQL      |
             +--> read-only knowledge Milvus       |
             +--> public GET/HEAD                  |
```

每个 Hermes profile 使用独立 sandbox，或至少使用独立 policy 和独立运行时状态。不得让多个 profile 共享可写 Hermes state 目录。

## 6. 路径权限基线

### 6.1 项目控制面只读

以下路径在 sandbox 中只读：

```text
apps/**
services/**
packages/**
scripts/**
db/**
infra/**
runtimes/**
agents/hermes/profiles/**
start_all.sh
docker-compose.yml
pyproject.toml
ruff.toml
mypy.ini
.git/**
```

这包括但不限于：

- `AGENTS.md`
- `config.yaml`
- `profile.yaml`
- `skills/**`
- profile scripts
- shared scripts
- IC workflow Prompt 和角色契约

### 6.2 凭据和宿主配置不可见或只读

以下路径默认不挂载；确需读取时仅允许专用 provider：

```text
/home/maoyd/.ssh
/home/maoyd/.aws
/home/maoyd/.config
/home/maoyd/.kube
/home/maoyd/.docker
/var/run/docker.sock
infra/env/*.env
env/*.env
```

示例文件 `*.example` 可随项目代码只读提供，真实 `.env` 不得进入 sandbox。

OpenShell 为建立 sandbox 到 gateway 的控制连接而注入的 JWT、client certificate 和
client key 属于唯一例外：它们以固定、只读的 control mount 提供给运行时，允许读取但
必须拒绝覆盖、截断、删除和替换。该例外不允许扩展到 provider key、用户凭据、宿主
`.env`、Docker socket 或 `var/openshell` 的其他私有状态。

### 6.3 公司 Wiki 固化路径只读

对已经进入 immutable registry 的公司路径，以下内容只读：

```text
data/wiki/companies/*/company.json
data/wiki/companies/*/reports/**
data/wiki/companies/*/metrics/**
data/wiki/companies/*/evidence/**
data/wiki/companies/*/graph/**
data/wiki/companies/*/semantic/**
data/wiki/companies/*/obsidian/**
```

多市场 Wiki 中，经 finalization 的以下内容只读：

```text
reports/<report_id>/**
metrics/**
qa/**
evidence/**
document_full.json
table_index.json
source_map.json
artifact_manifest.json
manifest.json
company.json
```

### 6.4 Agent 派生产物可写

以下路径保持现有读写行为：

```text
data/wiki/companies/*/analysis/**
data/wiki/companies/*/factcheck/**
data/wiki/companies/*/tracking/**
data/wiki/companies/*/legal/**
artifacts/**
var/**
```

`data/wiki/companies/*/_index.json` 由宿主 Publisher 更新。迁移期允许保留现有 best-effort 更新失败不影响主任务的行为，但不得因此让公司根目录整体可写。

首个 `siq_analysis` sandbox 挂载当前公司的完整 `analysis/` 根为读写，而不是只挂载一个
预先存在的 `.work` 叶目录。智能体可在该根内按正常任务需要执行目录创建、下载解析、
文件创建/覆盖、改名和当前任务清理；宿主只负责确认公司根身份，并在 `analysis/` 本身
缺失时安全创建该叶目录，不替智能体预建后续任务目录。高危删除守卫保护的是任务启动
前的持久文件和 analysis 根身份，`.work/cache/tmp` 等任务临时目录不计入批量删除阈值。

### 6.5 Deal OS 路径

只读：

```text
data/wiki/deals/*/data_room/raw/**
data/wiki/deals/*/parsed_documents/**
data/wiki/deals/*/sources/**
data/wiki/deals/*/evidence/snapshots/**
```

可写或由现有宿主服务管理：

```text
data/wiki/deals/*/phases/**
data/wiki/deals/*/discussion/**
data/wiki/deals/*/decision/**
data/wiki/deals/*/audit/**
```

本期优先保持 Deal OS 现有宿主 API 写路径。若某个 Hermes tool 直接写上述目录，应在路径审计中标记并单独决定，不得直接扩大整个 Deal 根目录权限。

### 6.6 Hermes 运行时路径

materialized profile 控制文件只读：

```text
data/hermes/home/profiles/*/AGENTS.md
data/hermes/home/profiles/*/config.yaml
data/hermes/home/profiles/*/profile.yaml
data/hermes/home/profiles/*/skills/**
data/hermes/home/profiles/*/scripts/**
data/hermes/home/profiles/shared/**
data/hermes/home/profiles/siq_ic_shared/**
```

运行状态可写：

```text
data/hermes/home/profiles/*/sessions/**
data/hermes/home/profiles/*/logs/**
data/hermes/home/profiles/*/memories/**
data/hermes/home/profiles/*/workspace/**
data/hermes/home/profiles/*/checkpoints/**
data/hermes/home/profiles/*/cache/**
data/hermes/home/profiles/*/state.db*
data/hermes/home/profiles/*/response_store.db*
```

如果 OpenShell filesystem policy 无法安全表达“只读父目录中的可写子目录”，必须使用独立 mount/volume，不得通过给父目录 `read_write` 来绕过。

### 6.7 公司运行池的并发和多用户隔离边界

当前公司运行池按 canonical company 绑定长驻 sandbox。每个公司完整 `analysis/` 写根固定
`max_active=1`：同公司请求由持久化 FIFO 串行执行，不同公司使用独立 slot 并行。该设计
提供写并发安全和故障隔离，但不自动提供同一公司 sandbox 内的用户级物理隔离。

当前调度器把全局 active lease 上限固定为 `16`，每个公司最多等待 `64` 个请求、全局最多
保留 `1024` 个 lease。客户端取消或等待超时会原子移除尚未绑定 Hermes run 的排队 lease；
已经绑定 run 的 writer 若心跳过期则转为 orphan，并继续占住公司写槽，直到精确 terminal
确认或 API recovery 完成接管，不能靠 TTL 直接放入第二个 writer。单个公司故障只把对应
binding 标为 failed，不阻断其他公司 slot。

API 已将 `DEFAULT_TENANT_ID` 和通过认证的 `user_id` 显式传给 pool，并在 acquire 前验证
session 所有权；durable lease 以同一 principal 做 heartbeat、takeover 和 release。按
tenant/user/session/company 过滤的 history、宿主 Agent memory ACL，以及 lease-specific
Hermes session namespace 都是必须保留的逻辑隔离。namespace 使用完整 128-bit lease HMAC，
不包含 tenant、user 或 session 明文；缺失 principal、非 canonical user ID 和跨 profile
session 均拒绝进入 pool。它们不能替代文件系统边界：同一长驻
sandbox 的 Hermes gateway 和 agent tool 仍共享 `state.db`、
`response_store.db`、sessions、checkpoints、memories、cache/log/workspace 及公司
`analysis/` 写根。只要 terminal/file/code execution 能读取这些共享路径，后一位用户就可能
读取前一位用户的 Prompt、消息、tool receipt 或中间文件；FIFO 只能避免同时写，不能消除
顺序读取风险。

因此 V0.6 当前公司长驻模式的允许范围是可信内部单租户，且同公司派生产物按业务定义可以
共享。该范围内的 canary/GO 结论必须显式携带这一 trust model，不能简写为“已实现多用户
sandbox 隔离”。若发布范围包含互不信任用户或多个租户，必须先完成以下至少一种方案：

1. 在现有显式认证 principal 基础上，将整个 slot/runtime epoch 排他绑定单一 principal；
   principal 变化时先 drain，再从 fresh runtime snapshot 重建，且 agent tool 看不到旧 epoch；
2. 每个 lease 使用 fresh sandbox/gateway replica，仅挂载当前 task leaf，再由宿主 Publisher
   发布最终产物。

无论选择哪一种，agent tool 都必须看不到 gateway runtime-state 和 sibling lease 目录，并由
双用户同公司顺序运行的负向测试验证。仅增加 session namespace、SQLite 行过滤或目录命名
不能单独作为强多租户隔离证据。

## 7. 网络门禁 V0.6

### 7.1 放行

1. 任意公网 `GET` 和 `HEAD`，用于网页和文件读取。
2. 当前模型 provider 的必要推理路径。
3. Tavily 等当前已配置搜索 API 的必要 POST 路径；Exa 只在启用后加入同等规则。
4. SIQ 内部 API、PostgreSQL、Milvus、解析器和本地模型服务。
5. 未知域名、小于等于 128 KiB 的 JSON POST：观察模式记录，不阻断。

### 7.2 阻断

1. 未知公网 `multipart/form-data` 文件上传。
2. 未知公网 `application/octet-stream`。
3. 未知公网 `PUT` 文件上传。
4. `curl -T`、`curl --upload-file`、`scp`、`sftp`、`rsync`、`rclone` 到公网。
5. 未经批准的对象存储预签名上传。
6. 超过 128 KiB 且未命中模型、搜索或批准服务规则的外部请求体。
7. 云 metadata endpoint，例如 `169.254.169.254`。
8. 任意未批准的原始 TCP/UDP、WebSocket 上传通道。

### 7.3 模型和搜索请求例外

模型和搜索请求不能应用通用 128 KiB 阈值。每条规则必须至少绑定：

- source profile
- host
- port
- method
- path
- provider 名称
- credential provider
- 超时和最大请求体

禁止只按 `*.amazonaws.com`、`*.aliyuncs.com` 等公共云大域名开放写权限。

Tavily，以及启用后的 Exa，其“完全放开”特指完整检索能力和完整返回内容：可以覆盖 search、extract/contents、crawl/map、answer/research 及读取异步研究结果所需的官方接口，不应用未知域 `128 KiB` 通用阈值，也不按响应正文、MIME 或观点过滤结果。它不等于允许整个域名上的任意方法和路径；`multipart`、`octet-stream`、本地文件路径/字节、账号管理、持久监控、导入和非检索写操作仍不属于检索白名单。

### 7.4 数据流边界与强机密模式

检索词、Prompt、URL 和模型上下文本身都是出站数据。只要同一个 sandbox 同时能够读取固化数据并自由构造 Tavily、启用后的 Exa、模型或任意公网查询，网络层就不能从合法 JSON 中判断某段文本是否来自内部事实，也不能阻止小请求分片。因此 V0.6 可以保证高风险文件/大体积未知上传被拦截，但不能虚假宣称已经实现通用语义 DLP。

当前宽松模式将 Tavily、已批准模型 provider，以及启用后的 Exa 视为获准数据处理方，同时禁止其文件上传形态。若比赛或生产要求升级为“固化数据原文不得发送给任何第三方，包括搜索 provider 和云模型”，必须启用能力分离：

1. 数据分析 sandbox 可读任务范围内的固化数据，但没有任意公网和直接搜索 provider egress。
2. 检索 worker 拥有完整公网、Tavily 和已启用搜索 provider 能力，但不挂载 Wiki、数据库 broker、Hermes session、项目控制面或凭据。
3. 宿主 retrieval broker 只接受带 provenance 的结构化 `SearchPlan`，服务端从批准的公司名、ticker、market、year、industry、research dimension 和用户公开检索意图生成请求。
4. 检索结果完整返回并作为不可执行 evidence object；外部文本不能提升工具权限或改变宿主确定性 workflow 状态。
5. 需要把内部事实写进自由文本查询时，必须走显式 declassification；不能同时承诺任意自由查询和数学意义上的零外泄。

强机密模式必须单独 A/B，未证明召回率、证据覆盖和时延不回退前，不得替换当前 host 路径。

## 8. 进程门禁 V0.6

阻断：

- `sudo`、`su` 和 privilege escalation
- mount、修改 namespace 和加载内核模块
- Docker/Podman socket
- 特权容器
- 裸磁盘设备
- 修改防火墙和路由
- 修改 OpenShell gateway/policy
- 访问宿主敏感目录

保持可用：

- Python、Node、shell
- Git 只读和正常仓库检查
- PDF、文档和财务计算工具
- profile scripts
- 报告渲染和验证脚本
- 当前任务临时进程

设置：

```bash
PYTHONDONTWRITEBYTECODE=1
PYTHONPYCACHEPREFIX=/tmp/siq-pycache
```

## 9. 数据库和 Milvus 权限

### 9.1 宿主 API

保持 `SIQ_APP_DATABASE_URL` 现有读写能力。不得将其替换为只读账号。

### 9.2 Hermes PostgreSQL

创建或复用市场事实只读账号，仅允许：

- CONNECT
- schema USAGE
- SELECT
- 必要只读 view/function

禁止 DDL、DML 和可写 `SECURITY DEFINER` 函数。继续使用 `agents/hermes/profiles/shared/scripts/pg_query.py` 的 SQL 级只读校验。

### 9.3 Agent memory

保持以下宿主写入：

- `ChatMessage`
- `ChatSessionMemory`
- `agent_memory.sessions`
- `agent_memory.messages`
- `agent_memory.memory_items`
- `agent_memory.session_summaries`
- `agent_memory.feedback_events`
- Milvus `siq_agent_memory_active`

一级市场和二级市场共用逻辑 alias `siq_agent_memory_active`，分别写入
`agent_group=primary_market` 和 `agent_group=secondary_market`。运行时只允许该 alias，
不得直接配置物理版本集合、legacy memory 集合或知识集合；alias 解析后的集合必须通过
`siq_agent_memory_milvus_v2` schema preflight。Sandbox 不获得这些写凭据。

### 9.4 Milvus 知识集合

Hermes 只允许 Search、Query、Get 和 Describe。禁止 Insert、Upsert、Delete、Drop、Create/Alter Index。入库 worker 和宿主 memory service 使用独立身份。

## 10. Immutable Path Registry

### 10.1 新增组件

新增建议模块：

```text
apps/api/services/immutable_path_registry.py
scripts/openshell/build_immutable_path_registry.py
infra/openshell/schemas/immutable-paths.schema.json
infra/openshell/policies/
```

实际命名可遵循仓库现有模式调整，但不得把生成逻辑散落到多个启动脚本。

### 10.2 Registry 数据来源

优先读取现有：

- `artifact_manifest.json`
- `manifest.json`
- `company.json`
- `parse_run_id`
- `filing_id`
- `report_id`
- quality/finalization 状态
- 文件内容 SHA-256

只有满足 finalization 条件的路径才能标记为 immutable。

### 10.3 Registry 输出

建议输出：

```text
var/openshell/registry/immutable-paths.json
var/openshell/registry/immutable-paths.sha256
```

Schema 至少包含：

```json
{
  "schema_version": "siq.immutable_paths.v1",
  "generated_at": "2026-07-15T00:00:00Z",
  "project_root": "/home/maoyd/siq-research-engine",
  "entries": [
    {
      "path": "data/wiki/companies/600519-贵州茅台/reports/2025-annual",
      "kind": "finalized_report",
      "owner": "ingestion",
      "identity": {
        "company_id": "600519",
        "report_id": "2025-annual",
        "parse_run_id": "..."
      },
      "manifest_sha256": "...",
      "recursive": true
    }
  ]
}
```

Registry 生成必须：

- 路径 canonicalize 后仍在允许的 SIQ data root 内
- 拒绝符号链接逃逸
- 排序稳定
- 输出原子写入
- 生成摘要
- dry-run 可预览差异
- 默认不修改任何文件权限

## 11. OpenShell 集成任务拆分

### T0：建立基线和保护现有改动

实施状态：已完成。脱敏交付物位于 `artifacts/openshell/v0.6/`，Hermes 冻结与回退说明位于 `docs/runbooks/hermes-upgrade-freeze.md`。原始备份保留在 Git 忽略区。

目标：在任何实现前建立可比较基线。

任务：

1. 记录当前 git 状态，不清理、不覆盖用户未提交改动。
2. 记录 OpenShell、Docker、Hermes、Python、Node 和 GPU 环境版本。
3. 运行 OpenShell `doctor check`。
4. 诊断当前 gateway 的协议错误；不得直接销毁现有 gateway，除非用户明确批准。
5. 运行现有 Hermes/API 相关测试基线。
6. 选择 `siq_analysis` 作为首个 PoC profile。

交付物：

- `artifacts/openshell/v0.6/baseline.json`
- `artifacts/openshell/v0.6/baseline.md`

验收：基线中明确记录成功、失败和未运行项。

### T1：实现路径分类和 registry 生成器

实施状态：已完成报告路径和 Deal archive snapshot 契约的离线实现。当前真实 Wiki dry-run 收录 183 个满足严格 finalization 条件的报告；`needs_review`、warning/fail、staging、缺 manifest、身份错绑和缺稳定摘要的路径均不收录。Deal 仅收录带 `siq.deal_evidence_snapshot.v1` finalization manifest、稳定摘要和唯一 snapshot ID 的 `evidence/snapshots/<id>/`；当前真实数据没有满足该契约的 Deal snapshot，可刷新 `evidence/evidence_snapshot.json` 不会被误锁。

目标：从 SIQ 实际数据结构生成固化路径清单。

任务：

1. 定义 `siq.immutable_paths.v1` schema。
2. 支持 CN company Wiki。
3. 支持 HK、JP、KR、EU、US 当前 market package。
4. 支持 Deal evidence snapshot，未固化 Deal 工作流目录不得误锁。
5. 增加 symlink/path traversal 防护。
6. 增加 deterministic output 测试。
7. 增加 dry-run 和 diff 输出。

测试至少覆盖：

- finalized report 被收录
- staging report 不被收录
- analysis/factcheck/tracking/legal 不被收录
- 缺 manifest 不误判为 finalized
- 路径逃逸被拒绝
- 重复运行输出一致

### T2：定义 OpenShell policy 模板和编译器

实施状态：已完成离线实现，并已由裸 Hermes PoC 使用独立最小 policy 验证 Landlock 设备规则。首个 `siq_analysis` policy 仅使用任务级 `analysis` 写路径、自身运行态子目录、SQLite/WAL 文件和 gateway 运行元数据；当前生成 policy 为 26 条 filesystem 规则，并在编译前校验六个 Hermes SQLite 状态文件均为真实普通文件、registry source manifest 摘要仍有效、写路径不存在符号链接别名。整个 `artifacts`、`var`、其他 Hermes profile、代码、Prompt、workflow 和固化数据均不能作为可写根；正式 `siq_analysis` sandbox 尚未切流。

目标：将 registry、静态控制面和可写工作面编译为 profile policy。

任务：

1. 创建公共 policy 模板。
2. 为 `siq_analysis` 生成首个 profile policy。
3. 配置 filesystem、process 和 network policy。
4. 检测 read-only/read-write 路径重叠。
5. 对不安全父级 `read_write` 直接编译失败。
6. 输出 policy 摘要和规则来源。
7. 支持 `--check`、`--dry-run` 和 `--output`。

交付物建议：

```text
infra/openshell/policies/base.yaml
infra/openshell/policies/profiles/siq-analysis.yaml
scripts/openshell/build_policy.py
```

注意：`infra/openshell/policies/` 只保存可审查模板和稳定 profile 规则；包含机器绝对状态、动态 immutable paths 或本机资源 ID 的编译结果必须写入 `var/openshell/policies/`。

OpenShell `v0.0.83` 的实际约束必须保留：

- Landlock 权限是授权叠加；只读父目录可以追加可写子目录，可写父目录不能再用具体只读子目录收紧；
- `filesystem_policy.include_workdir` 必须显式为 `false`；
- filesystem path 不支持 glob，read-only 与 read-write 合计最多 256 条；
- filesystem policy 是静态配置，immutable registry 变化后需要轮换 sandbox；
- 原生 network policy 不能按通用 Content-Type 或任意请求体大小识别上传，T6 的 128 KiB、multipart 和 octet-stream 规则必须由 SIQ egress guard 实现；
- 项目根只读不等于 secrets 不可见，T3 的 build context/mount 必须从源头排除 `.env`、真实 env 文件、宿主 OpenShell 状态和凭据目录。

### T3：制作 SIQ Hermes BYOC sandbox

实施状态：部分完成。正式镜像固定 Hermes `0.13.0` commit `ddb8d8fa...`，保留原项目绝对路径、中文字体、Node `v20.20.2` 校验归档、非 root 用户和 profile 依赖；不包含真实凭据。断网 smoke 已验证代码/Prompt 只读、运行态可写、全部 Hermes metadata 启动前物化、运行态 marker 语义、API key、placeholder auth 持久化和 healthcheck，并写入与镜像 ID、候选状态及 smoke 脚本哈希绑定的私有证明。provider-independent 探针已实跑 Docker bootstrap 精确校验及 Agent 子进程的非 root、capability、`no_new_privs`、Docker socket、提权、mount/namespace 和裸设备负向检查。runtime snapshot、固定 7 个业务挂载加 5 个控制挂载和删除守卫已实现；高危删除触发后的三类执行路径恢复仍待正式业务 sandbox 实跑。

目标：提供与当前 Hermes 环境能力一致的 sandbox 镜像。

任务：

1. 安装当前兼容 Hermes 版本。
2. 安装 profile scripts 所需 Python/Node/system 依赖。
3. 保留 UTF-8、中文字体和报告渲染依赖。
4. 提供非 root `sandbox` 用户。
5. 不复制真实凭据。
6. 设置 Python cache 到 `/tmp`。
7. 提供 healthcheck。
8. 锁定镜像 digest 和依赖版本。
9. 对 SQLite WAL、`gateway.pid`、`gateway.lock`、`gateway_state.json` 和 `processes.json` 完成真实创建、删除和重建 smoke；失败时不得进入 T4。
10. 生成任务前文件系统快照，并为 T6.1 的批量删除守卫提供可终止 sandbox 和恢复当前任务外文件的接口。
11. BYOC staged mount 必须通过 `scripts/openshell/check_mount_safety.py`；当前仓库根包含凭据与宿主状态，禁止原样挂载。

建议路径：

```text
infra/openshell/sandbox/Dockerfile
infra/openshell/sandbox/entrypoint.sh
infra/openshell/sandbox/README.md
```

### T4：实现 Hermes gateway OpenShell 启动适配

实施状态：裸 Hermes PoC、正式 `siq_analysis` 单任务 adapter 和 provider-independent 安全 probe 已完成。NOT_PRODUCTION `siq-analysis-observe-poc` 已使用正式镜像的只读 profile seed、sandbox 内临时 Hermes home、MiniMax OpenShell provider 和 `28651` 独立 forward 完成真实验证：鉴权、`/v1/runs` create、SSE、一次 terminal 工具调用、`run.completed`、第二次 run 的 stop/cancel 均通过；项目写入被拒绝，sandbox runtime 写入成功，宿主 profile/immutable 摘要不变，随后完成身份校验删除且无端口、容器或临时身份状态残留。它不挂载宿主 Wiki/profile/database，不消费正式 preflight，也不影响 host runtime，结果固定为 `readiness_effect=none`。正式 adapter 提供 start/stop/status/recover/repair/host rollback；`repair` 复用同一身份绑定、fail-closed recovery，不引入猜测式资源清理。adapter 还包含 transaction v2 durable journal、maintenance lock 串行化、pidfd 终止、精确 Host receipt、候选镜像证明、固定 7 个业务挂载加 5 个控制挂载、task policy、删除守卫、一次性 key/nonce、sandbox/Docker/PID 交叉验证和 `28651` loopback forward。正式 start 在创建 transaction 前强制执行只读服务 preflight、PostgreSQL/Milvus proof 和双 broker 状态校验；runtime snapshot 重新编译当前配置，并要求其 digest 与候选镜像一致。stop/rollback 清理 sandbox 后调用固定 host Publisher 更新公司索引，发布失败只产生 audit-only/deferred 状态，不影响主产物。provider-independent probe 已验证隔离路径和清理闭环，但不包含 Hermes/provider/业务质量。`start_all.sh` 默认启动或复用项目 gateway，并在本机 reader secret 存在时以 `auto` 模式启动/复用 host brokers；它仍拒绝自动切换 OpenShell Hermes 流量。真实正式业务 sandbox 尚未创建。

正式文件权限证据 runner 已实现，但尚未产生真实 GO 产物。它只 attach 到已有 `running` transaction，执行 filesystem-only probe，并在前后交叉验证 transaction/manifest/sandbox/policy/host receipt 不变；固化路径拒写与 analysis/session/memory 文件面可写只能由该正式证据贡献 readiness，不能再由辅助 probe 推断。当前缺少正式 sandbox，因此真实调用失败关闭且零产物。

2026-07-17 运行优先 pool 已用同一新候选镜像运行上汽集团和贵州茅台两个公司级
canary，分别占用 `28652` 和 `28653`；两者的 lifecycle status、认证业务 probe 和 Docker
health 均通过。API pool recovery 已启用并为 ready。该结果证明受约束的可信内部公司级
canary 可以持续承接匹配流量，但 `readiness_effect=none`，不补齐任何正式 filesystem、
egress/audit、delete、rollback 或 A/B 证据。

目标：保持现有 profile 同步和 `/v1/runs` 契约。

建议新增：

```text
scripts/openshell/run_hermes_gateway.sh
scripts/openshell/profile_env.sh
scripts/openshell/status.sh
scripts/openshell/rollback_to_host.sh
```

任务：

1. 复用现有 profile canonicalization。
2. 在宿主完成 materialization。
3. 创建/复用命名 sandbox。
4. 挂载或映射相同绝对项目路径。
5. 为 gateway 配置 `--forward` 端口。
6. 将 `SIQ_HERMES_<PROFILE>_RUNS_URL` 指向转发地址。
7. 正确传递非敏感环境变量。
8. 使用 OpenShell provider/inference route 管理敏感凭据。
9. 支持 start、stop、status、repair。
10. 支持 `SIQ_HERMES_RUNTIME=host|openshell` 快速切换。

不得删除或改变现有 `scripts/hermes/run_gateway.sh` 的默认行为，直到灰度验收通过。

### T5：修复 sandbox 内部服务寻址

实施状态：部分完成。编译 runtime config 已把 `8004/8006/8007/8013` 固定改写为 `host.openshell.internal`、API 固定为 `28651`、关闭 `auto_source_bashrc` 并保留 provider/broker 环境 contract；正式 lifecycle 在 snapshot 中重新编译并与候选镜像 runtime hash 绑定，避免配置漂移。上述端口即使离线也不得从白名单删除。service preflight v2 已区分 TCP transport 与只读 HTTP 协议契约：对声明启用的本地模型/embedding 固定验证无请求体、无重定向的 `GET /v1/models` 最小 OpenAI JSON shape，对 SIQ API/host Hermes 固定验证 `GET /health` 的 `status=ok`，且不记录响应正文或模型 ID；lifecycle、A/B 前置检查和 completion gate 均失败关闭消费已启用服务的契约。当前 `8007/8013` 协议在线；`8004/8006` 为 optional、当前禁用，不构成 readiness blocker，未来启用时才必须补真实协议和 fallback 验收。MiniMax、StepFun、Kimi、Tavily 已进入 OpenShell provider；Exa 已明确延期，当前不构成 readiness blocker，启用时必须补真实检索契约。PostgreSQL 六库固定 schema 路由和专用只读角色已真实验证。Milvus 已补齐独立 NOT_PRODUCTION boundary-proof lifecycle：不启动 Hermes/provider，只开放签名身份访问的 `18793` data broker；broker v2 仅提供 Search/Query/Get/Describe，active policy 必须排除 `5432/15432/19530`，sandbox 实测直连 `19530` 拒绝且所有 mutation 路由不存在，清理验证后才发布绑定 policy、sandbox/container、bridge、broker 和 3600 秒有效期的 proof。当前运行中 broker 已是 v2，真实 sandbox proof 已发布到 `artifacts/openshell/v0.6/milvus-write-protection.sanitized.json`；proof 有效期为 3600 秒，正式启动前必须重新生成或验证仍在有效期内。该证据不替代已启用 fallback、embedding、stream/stop 或 A/B 的正式证据。

当前 required provider 契约固定为恰好四个：MiniMax、StepFun、Kimi、Tavily。
Exa 为 deferred，不计入 required provider 数量；`8004/8006` 为 optional disabled，不计入当前
required service 或正常 A/B 前置条件。只有显式启用后，三者才进入对应 service/provider、
协议、fallback 和质量验收，不能因模板或白名单仍存在而提前计为已验证能力。

目标：保证模型、搜索、PostgreSQL、Milvus 和 SIQ 内部 API 可达。

任务：

1. 为本地模型提供稳定 alias，禁止继续依赖 sandbox `127.0.0.1`。
2. 启用 `8004/8006` 时，验证对应 fallback 实际生效；保持禁用时记录 optional 状态。
3. 验证 embedding `8013`。
4. 验证 PostgreSQL 和 Milvus 只读访问。
5. 验证 Tavily 和已启用云模型；Exa 启用时补同等验收。
6. 验证 API 到 forwarded Hermes gateway 的 stop/stream 行为。
7. 记录 DNS、TLS、代理和超时配置。

### T6：实现宽松网络上传门禁

实施状态：代码和宿主真实负向测试已完成。公网 GET/HEAD 与未知小 JSON POST 保持宽松；multipart、octet-stream、PUT、metadata/private 地址和文件路径输入被拒绝，每个重定向 hop 重新解析与校验。2026-07-16 已生成绑定运行中 allowlist/source bundle、strict request identity 和 13 条结构化审计的 `host_egress_broker` 脱敏证明；该证明明确为 `readiness_effect=none`，不替代正式业务 sandbox 的直连旁路、传输客户端、provider 路由或语义 DLP 证据。模型与搜索继续要求 OpenShell provider，不改变输出路径。

目标：阻断明确文件外传，不影响正常查询和模型调用。

任务：

1. 定义模型、搜索和批准服务规则。
2. 放行公网 GET/HEAD。
3. 对未知小 JSON POST 进入 audit-only。
4. 阻断 multipart/octet-stream/PUT 文件上传。
5. 阻断常见文件传输二进制到未知公网。
6. 阻断 metadata endpoint。
7. 记录 policy decision，但不得记录请求正文、Prompt、凭据或用户数据。
8. 增加重定向后重新检查目标的测试。

如果 OpenShell 原生 policy 无法表达请求体大小或 content-type 门禁，应新增 SIQ egress proxy，仅将该能力放在 proxy 中。不得用脆弱 shell 正则冒充网络级强制控制。

### T6.1：实现高危批量删除守卫

实施状态：守卫和严格正式证据 runner 已实现并完成离线测试。守卫按单任务/公司 `analysis/` 建立私有快照和递归 inotify watch，覆盖 shell、Python、Node 和 syscall 文件事件；超过 500 个文件、比例阈值、根路径事件或 watch overflow 会调用验证式 sandbox terminator 并恢复，正常少量删除继续放行。触发前写入 durable guard event，长驻 guard/forward 不继承启动 maintenance lock，触发动作和独立 watchdog 以新锁完成清理；并发 stop/rollback 保留既有 terminal action 但恢复已持久化快照，transaction 保留 `stopping` 并由幂等 recovery 收敛。正式 runner 只在现有公司 `analysis/` 下创建唯一、digest-bound 合成 fixture，以三个独立正式 transaction 验证 shell/Python/Node 各删除 501 个基线文件并完整恢复，以第四个 transaction 验证缺失叶目录的 mkdir/create/write/overwrite/rename/少量删除/递归清理正常放行，并要求 fixture 外原始 analysis 树摘要不变；最终 exporter 重验四个 terminal journal、host identity、image/policy/normalized 7+5 mount provenance 后才清理 fixture/snapshot 并发布。当前仍未执行这组 live suite，因此正式 delete GO 证据尚未产生。

任务：

1. 任务启动前为允许写入的工作面创建轻量快照，不复制凭据和 OpenShell 控制状态。
2. 监控 sandbox 文件删除事件，按单次操作和滑动时间窗计数。
3. 明确阻断项目根、`.git`、`infra/env`、卷、裸设备和超过阈值（首版 500 个文件）的递归删除。
4. 达到阈值时终止对应 sandbox，保留最小脱敏审计，并恢复当前任务范围外的误删文件。
5. 继续允许单文件修复、失败报告清理、临时目录和缓存清理。
6. shell、Python `shutil`、Node 和直接 syscall 路径均必须进入同一文件事件验收，不能只匹配 `rm` 命令文本。

### T7：处理共享索引和派生产物写入

实施状态：已实现受控 Publisher 集成。Publisher 只接受固定 market/company 参数，以 `dirfd`/inode 锚定项目、私有锁目录、锁文件和公司目录，构建前后核对输入树身份，并使用固定 `_index.json` 文件名原子发布；symlink、hardlink、目录或锁运行中替换均失败关闭。host Hermes 直接调用它，OpenShell sandbox 内不再调用旧的任意路径 updater，而是在生命周期 stop/rollback 清理后由 host 侧调用。analysis/factcheck 主产物不因索引发布失败而失败，只产生明确 deferred warning 和 `publisher.index` audit-only 记录，业务索引中的原有绝对路径会从内部 fd anchor 还原为原公司路径。正式业务 sandbox 的端到端发布仍待灰度验证。

目标：保证事实核查等现有功能不因公司根目录只读而退化。

任务：

1. 审计 `update_company_index.py` 的调用点。
2. 将 `_index.json` 更新迁移到宿主 Publisher 或受控 API。
3. 保持 factcheck 主结果写入成功。
4. 保持 analysis/tracking/legal 输出路径不变。
5. Publisher 只允许重建已知索引，禁止接受任意目标路径。
6. 使用原子写、锁和内容校验。

迁移期必须保留向后兼容：索引更新失败不得导致主报告任务失败，但必须产生明确审计事件。

### T8：审计与可观测性

实施状态：代码闭环、严格请求身份和 Milvus 安全实证已完成，正式业务审计证据仍待生成。host egress/data broker 支持显式请求身份强制模式；正式 lifecycle 在 transaction 创建前要求两者均为严格模式，并为每个 run 生成两个 6 小时、分 audience 的 HMAC token，绑定 `profile/run/sandbox/session/policy/run nonce`。`siq_fetch.py` 和 `pg_query.py` 分别只发送 egress/data token，broker 在访问上游前 fail-closed 校验，逐请求审计使用已验证 claims 覆盖进程默认上下文。HMAC 密钥只保存在 ignored 的 `var/openshell/secrets`，token 与 API key/nonce 共同纳入 transaction receipt 并在 stop/rollback/recover 中清理；离线轮换要求 broker 停止且无 active run。原始 SQL、向量、Prompt、请求/响应正文、密钥和 token 均不落审计或脱敏证据。当前 live broker 已通过严格请求身份前置检查，但没有正式业务 sandbox audit，因此 T8 不能标记为正式验收完成。

目标：关联 SIQ 业务运行和 OpenShell 执行动作。

每条审计至少包含：

- schema version
- timestamp
- profile
- sandbox ID
- SIQ run ID
- session ID（脱敏或稳定内部 ID）
- operation class
- target host/path 的安全投影
- allow/deny/audit-only
- policy version/digest
- error code
- duration

禁止记录：

- API Key
- Authorization header
- Prompt 正文
- 用户附件正文
- 数据库密码
- 完整请求体

建议输出：

```text
var/openshell/audit/*.jsonl
```

并提供聚合指标：

- policy deny count
- audit-only count
- sandbox start failures
- tool failure rate
- external upload blocks
- immutable path write blocks
- P50/P95 gateway overhead

增加参赛证据导出流程：

1. 由专用脱敏流程将原始运行结果转换为可提交的 `*.sanitized.json`、`*.sanitized.md`；日志固定生成同目录成对的 `logs.sanitized.json|md`，再交给严格日志契约、`scripts/openshell/check_sanitized_artifacts.py` 和 tracked artifact manifest 审核；通配文件名不能绕过 manifest 的路径、分类、大小和摘要绑定。
2. 脱敏器必须移除 token、Authorization、cookie、数据库 DSN、用户 home、绝对机器路径、Prompt、用户输入和附件正文。
3. 保留 profile 名称、规则 ID、allow/deny 类型、延迟、成功率、质量指标、版本号和 digest，以便评审复现与核验。
4. `scripts/openshell/check_tracked_state.py` 检查所有已跟踪的 OpenShell 文件，命中疑似 secret 或禁止字段时非零退出。
5. 将检查加入 `scripts/check_all.sh` 或独立 CI job；不得要求 CI 访问真实凭据或运行 gateway。

### T9：测试与质量评测

#### 单元测试

1. registry 路径分类。
2. policy 编译和重叠检测。
3. profile 名称、端口和环境映射。
4. 审计脱敏。
5. Publisher 路径约束。
6. 网络规则分类。

#### 集成测试

至少验证：

1. Hermes 能读取项目代码但不能修改。
2. Hermes 能读取 AGENTS.md/skills 但不能修改。
3. Hermes 能读取固化 Wiki，但写入被拒绝。
4. Hermes 能正常写 analysis/factcheck/tracking/legal。
5. Hermes 能正常写 workspace/checkpoint/session。
6. 宿主 API 能正常保存聊天和 memory。
7. PostgreSQL 市场事实查询正常，DML 被拒绝。
8. Sandbox 的知识 Milvus 查询正常，upsert/delete 被拒绝；宿主 memory service 对专用 memory collection 的 upsert/search 正常。
9. 模型主路由正常可用；声明启用的 fallback 由独立 fallback drill 验证。
10. Tavily 正常；Exa 启用时再纳入同等门禁。
11. 未知公网文件上传被拒绝。
12. 公网 GET/HEAD 正常。
13. stop、timeout、stream 和 reconnect 行为保持。
14. 正常单文件删除继续成功，超过阈值的批量删除会终止 sandbox 并完成恢复。
15. 在声明强多用户或多租户 GO 前，以两个不同 principal 在同一公司顺序运行，验证后一位
    无法经 API history、Agent memory、Hermes session、SQLite、checkpoint 或 sibling lease
    工作目录读取前一位状态；可信内部单租户灰度未执行该门禁时，结论必须明确限定范围。

#### 安全测试

```text
尝试修改 apps/api/main.py                       -> 必须失败
尝试修改 profile AGENTS.md                      -> 必须失败
尝试修改 data/wiki/.../reports/...              -> 必须失败
尝试写 data/wiki/.../analysis/...               -> 必须成功
尝试读取真实 .env                               -> 必须失败
尝试访问 Docker socket                          -> 必须失败
尝试 curl --upload-file 到未知域名              -> 必须失败
尝试在短时间内递归删除超过 500 个工作文件        -> 必须终止并恢复
尝试正常调用模型和搜索                          -> 必须成功
```

#### A/B 质量评测

已实现严格的双 `/v1/runs` A/B harness、显式 dataset schema、SSE/stop/timeout、工具与策略 telemetry、质量阈值、policy false-positive 和脱敏 `0600` 产物；质量比较严格要求 task success 不低于 host、总时长 P95 比值不超过 `1.10`、golden 正常路径 policy false positive 为 `0`，并拒绝低于 10 个 case、3 次 repetition、每臂 30 次执行或关键指标分母不足的结果。新增只读 `check_siq_analysis_ab_prerequisites.py` 固定拒绝 assistant `18642`，要求分析 host `18651`、独立 key、四个 required provider、已启用 service/broker 证据、正式数据集，以及两臂一致的 Hermes/profile/模型路由/工具/数据快照 provenance。OpenShell 臂还必须固定 image、policy、mount plan 和 runtime config 摘要。当前 required provider 恰好为 MiniMax、StepFun、Kimi、Tavily；延期的 Exa 与禁用的 optional `8004/8006` 不得被重新当作正常 A/B 前置 blocker。

正常 A/B 只评估 primary route 的真实业务路径：dataset 不注入 provider 故障，不包含 fallback case，
也不产生 fallback 指标分母。fallback 的可达性、顺序和生效结果必须由独立 drill 在同一候选
provenance 下验证，使用独立计划、原始产物、脱敏摘要和门禁结论；不得把 drill 样本混入正常
A/B，或用 fallback 成功掩盖 primary route 的正常路径失败。

业务结果与工具运行指标必须分开。业务 task success 以最终输出、业务契约和必要事实质量为准；
required tool 最终完成率单独统计。单次工具非零退出、失败后重试或换用等价工具后成功，不得仅
因历史 `tool error` 自动把业务任务判失败。与此同时，工具调用错误率、重试率、恢复率和未恢复
失败率必须独立保留并与 Host 比较，不能通过只看最终答案隐藏运行回退。policy false positive
只统计正常允许操作被策略明确拒绝的情况。任何评分口径调整都必须升级 schema 并重新运行两臂，
不得事后重解释既有正式结果。

同一模型、temperature、Prompt、输入和数据分别运行：

```text
A：当前宿主 Hermes
B：OpenShell Hermes
```

比较：

- 任务成功率
- answer citation rate
- numeric accuracy
- hallucination block rate
- evidence coverage
- required tool 最终完成率
- 工具调用错误率、重试率、恢复率和未恢复失败率
- 报告完整率
- P50/P95 首 token 延迟
- P50/P95 总时长
- timeout rate
- policy false positive rate

独立 fallback drill 比较 primary 故障注入是否生效、fallback provider/model 是否按既定顺序
接管、最终成功率、telemetry 完整性和静默失效；其结论是单独发布门禁，不属于上表正常 A/B
业务质量分母。

### T10：灰度发布与回滚

实施状态：可信内部公司级 canary 已灰度，正式发布尚未开始。2026-07-17 上汽集团和贵州茅台均由新候选镜像的独立长驻 sandbox 承接精确匹配的 `siq_analysis`，同公司 `max_active=1` 跨进程 FIFO、跨公司并行；API recovery 为 ready。`start_all.sh` 保持 `SIQ_HERMES_RUNTIME=host` 为默认，未注册公司和其他 profile 自动留在 Host，已注册但 binding 损坏的请求失败关闭而不静默重放。正式 lifecycle 的 transaction v2、幂等 recover、pidfd stop、精确 Host receipt rollback 和失败回滚已完成离线/单元验证；live canary 不构成正式质量或安全证据，真实正式 A/B 尚未运行。

发布阶段：

1. `siq_analysis` 本地 PoC。
2. `siq_analysis` 观察模式。
3. `siq_analysis` 高风险阻断模式。
4. factchecker、tracking、legal。
5. IC profiles。
6. assistant 最后迁移。

每阶段至少运行完整 smoke 和目标 profile 质量集。

必须保留：

```text
SIQ_HERMES_RUNTIME=host
```

作为即时回滚开关。回滚不得要求数据迁移，不得改变 session ID 和 API 契约。

## 12. 验收标准

### 12.1 功能验收

- 普通聊天、流式输出、停止和超时行为与现状一致。
- analysis、factcheck、tracking、legal 产物仍写入原路径。
- 聊天、会议、任务和 Agent memory 正常写入。
- 模型主路由通过正常业务验证，声明启用的生产 fallback 通过独立 drill。
- 公开网页和 Tavily 正常；Exa 仅在启用后纳入验收。
- `8004/8006` 保持 optional disabled 时不作为功能失败；启用后再补协议和 fallback 验收。

### 12.2 安全验收

- Agent 无法修改项目代码。
- Agent 无法修改 Prompt、profiles、skills 和 workflow。
- Agent 无法修改 immutable registry 中的固化数据。
- Agent 无法读取真实凭据文件。
- Agent 无法访问 Docker socket 或提权。
- 明确未知公网文件上传被阻断并审计。
- 大范围破坏性删除会终止 sandbox 并从任务前快照恢复，正常任务内清理保持成功。
- 所有 GO/readiness 结论明确记录用户信任模型；未实现 principal-bound fresh runtime epoch
  或 per-lease fresh sandbox 时，不得宣称公司长驻 sandbox 已具备强多用户或多租户物理隔离。

### 12.3 质量验收

- numeric accuracy 不下降。
- answer citation rate 不下降。
- IC golden suite 保持通过。
- 任务成功率不得低于同配置 host 基线，golden 正常路径不得出现 OpenShell 独有失败。
- 业务 task success 与工具调用指标分别报告；正常工具失败后重试成功不得自动改写业务结果，
  工具错误、重试、恢复和未恢复失败仍必须作为独立非回退指标比较。
- P95 总时长增加不超过 10%，且 timeout、stop、stream 和 reconnect 成功率不得下降。
- golden 正常路径 policy false positive 必须为 0；影子流量聚合 false positive rate 小于 0.5%。
- 不允许发生 fallback 静默失效。
- 任一质量指标证据不足、有统计不确定性或无法复现时，默认结论为 `NO_GO`。

### 12.4 运维验收

- 有启动、停止、状态、修复和回滚 runbook。
- policy 和 registry 有 schema version 及 digest。
- 审计不包含敏感正文和凭据。
- 新增入库数据能进入 registry，并在 sandbox 轮换后生效。
- gateway 故障不会破坏宿主 API 数据。

## 13. 推荐验证命令

实现者应根据实际新增文件补充命令，至少运行：

```bash
cd /home/maoyd/siq-research-engine

openshell doctor check

# 项目内 OpenShell v0.0.83（不触碰 legacy nemoclaw）
scripts/openshell/status_gateway.sh
scripts/openshell/run_cli.sh sandbox list

# 裸 Hermes PoC（仅在明确验证阶段执行）
scripts/openshell/build_patched_supervisor.sh
scripts/openshell/start_hermes_poc.sh
scripts/openshell/smoke_hermes_poc.sh
scripts/openshell/stop_hermes_poc.sh

# siq_analysis observe-only 可行性 PoC（始终 start -> smoke -> stop）
scripts/openshell/start_siq_analysis_observe_poc.sh --acknowledge-not-production
scripts/openshell/smoke_siq_analysis_observe_poc.sh
scripts/openshell/stop_siq_analysis_observe_poc.sh

cd apps/api
uv run python -m pytest tests/test_hermes_client.py
uv run python -m pytest tests/test_hermes_pg_query.py
uv run python -m pytest tests/test_agent_memory_service.py
uv run python -m pytest tests/test_agent_memory_milvus.py
uv run python -m pytest tests/test_agent_runtime_memory.py
uv run python -m pytest tests/test_ic_agent_output_quality.py

cd /home/maoyd/siq-research-engine
python3 scripts/openshell/run_formal_filesystem_boundary.py --help
python3 scripts/openshell/run_formal_host_rollback.py --help
python3 scripts/openshell/run_formal_delete_guard.py --help
python3 scripts/openshell/build_v06_readiness.py --generated-at YYYY-MM-DDThh:mm:ssZ
python3 scripts/openshell/check_v06_completion.py --json
scripts/check_all.sh
```

新增 OpenShell 测试必须提供可跳过的环境探测，普通单元测试不得要求运行中的 OpenShell gateway 或公网密钥。

## 14. 实施顺序和依赖

```text
T0 基线
  -> T1 registry
  -> T2 policy compiler
  -> T3 BYOC image
  -> T4 gateway adapter
  -> T5 service routing
  -> T6 network gate
  -> T7 Publisher compatibility
  -> T8 audit
  -> T9 A/B verification
  -> T10 staged rollout
```

T1/T2 可以与 T3 并行开发；裸 Hermes PoC 已完成，但 T4 进入 `siq_analysis` 前仍必须完成 T0、T2、最小 T3、T5 路由和 profile-specific policy review。T9 未通过不得进入下一 profile 灰度。

## 15. Codex 执行约束

1. 开始前读取仓库根 `AGENTS.md` 和目标目录下更具体的说明。
2. 当前 worktree 可能包含大量用户未提交改动，不得清理、reset、checkout 或覆盖无关文件。
3. 每个阶段先检查现有实现和测试，不复制已有 helper。
4. 使用 `apply_patch` 修改文件。
5. 不提交真实凭据、绝对密钥值、token 或本地 `.env`。
6. 不将 NemoClaw 引入依赖树。
7. 不在未批准情况下销毁当前 OpenShell gateway。
8. 不一次性迁移全部 profiles；首个目标固定为 `siq_analysis`。
9. 每个阶段产出变更说明、验证命令、失败项和回滚方式。
10. 若 OpenShell 当前版本无法表达某条门禁，应明确记录能力缺口并使用最小宿主侧补充组件，不得声称策略已生效。

## 16. 完成定义

2026-07-17 机器门禁共有 13 项，当前仅 `tracked_state_secret_scan` 通过，结果为
`NO_GO 1/13`。两家公司 live canary、API recovery ready 和 Docker healthy 都是运行能力
证据，不替代以下正式完成项；未重新生成并通过当前 completion 输入契约的历史正式产物不得
计入。

本项目只有在以下条件全部满足时才算 V0.6 完成：

1. `siq_analysis` 至少完成一次宿主与 OpenShell A/B 真实运行。
2. 原输出路径和 API 契约未变化。
3. 正式 running transaction 中，代码、Prompt、workflow 和固化数据写入测试全部被拒绝，并生成 path/SHA/source-bound filesystem 证据。
4. 同一正式 filesystem 证据证明 analysis/session/memory 文件面可写，且宿主 memory 持久化测试成功。
5. MiniMax、StepFun、Kimi、Tavily 四个 required provider、已启用本地服务和 primary route
   全部可达；声明启用的 fallback 由独立 drill 通过。Exa 与 optional disabled `8004/8006`
   不计入当前完成分母。
6. 未知公网文件上传测试被拒绝。
7. 正常 A/B 质量门槛与独立 fallback drill 门槛分别通过，且未混用样本或分母。
8. 回滚到 host runtime 已演练成功。
9. 运维文档和审计说明完整。
10. 扩展到其他 profile 前完成一次人工架构与安全评审。
11. 仓库包含可复现的脱敏 policy、registry、版本 manifest、A/B 质量摘要和安全测试证据；completion 消费的公开证据与 Git stage-zero blob 完全一致。
12. 所有已跟踪 OpenShell 状态和脱敏日志通过 secret scan，且 gateway credentials、未经脱敏的 audit、session DB 和原始机器绑定状态未进入 Git。
13. 高危批量删除守卫已通过 shell/Python/Node 三类路径测试，能终止 sandbox、恢复误删文件，并保持正常任务清理成功。
14. 发布结论已声明用户信任边界；该范围约束独立于当前 13 项机器门禁。若目标范围包含互不
    信任用户或多个租户，已完成 6.7 所述 principal-bound fresh epoch 或 per-lease fresh
    sandbox，并通过双用户物理状态隔离测试。
