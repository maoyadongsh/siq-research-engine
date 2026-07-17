# SIQ 原生 OpenShell + Hermes 集成现状

**更新时间：2026 年 7 月 17 日**

## 结论

**是的。**

SIQ Research Engine 已经在**没有安装或运行 NemoClaw/NemoHermes** 的情况下，直接完成了 OpenShell 与 Hermes Agent 的真实集成：

```text
SIQ 前端
  → FastAPI 分析助手
  → Runtime Selection
  → Conversation Sandbox Generation
  → Scope Auto-Provisioning
  → Pool Registry / Lease / Recovery
  → NVIDIA OpenShell Gateway
  → OpenShell Sandbox
  → Hermes Agent /v1/runs
  → OpenShell Provider / Broker / 受控外部服务
```

这不是仅仅把 Hermes 放进普通 Docker，也不是只调用了 OpenShell CLI。当前实现已经实际使用了 OpenShell 的：

- Gateway 控制面
- Sandbox 数据面
- 文件系统与 Landlock 隔离
- 进程权限控制
- 网络策略
- Provider 凭据管理
- 推理和外部服务路由
- Service forwarding
- Sandbox 创建、探测、停止和删除
- 多 sandbox pool
- 请求级 lease 和 fencing
- API 重启恢复
- Host fallback
- 按公司 scope 自动创建 sandbox
- 对话级 sandbox generation
- 空闲 TTL 自动回收

NVIDIA 官方文档明确说明，NemoClaw 并不替代 OpenShell 或所选择的 Agent Runtime，而是将它们包装成具有 host CLI、版本化 blueprint、默认策略、推理配置和状态管理的可重复安装方案；官方也明确表示，这套架构可以直接使用，也可以适配为自己的 OpenShell 集成。([docs.nvidia.com](https://docs.nvidia.com/nemoclaw/user-guide/hermes/about/how-it-works?utm_source=openai))

因此，当前项目可以准确描述为：

> **针对 SIQ Research Engine 业务契约定制的、非 NemoClaw 路径的原生 OpenShell + Hermes 集成和控制层。**

---

## 官方组件关系

NVIDIA 官方 Hermes 路径可以概括为：

```text
NemoClaw / NemoHermes
  ├── onboarding
  ├── versioned blueprint
  ├── host CLI
  ├── dashboard
  ├── provider configuration
  ├── inference configuration
  ├── messaging channel configuration
  ├── state helpers
  └── 调用 OpenShell CLI
         → OpenShell Gateway
         → OpenShell Sandbox
         → Hermes Agent
```

在官方实现中，`nemohermes` 是预选 Hermes Agent 的 `nemoclaw` CLI 别名，负责创建并管理运行 Hermes 的 OpenShell sandbox。([docs.nvidia.com](https://docs.nvidia.com/nemoclaw/latest/get-started/quickstart-hermes.html?utm_source=openai))

NemoClaw 官方 Hermes blueprint 会准备 `/sandbox/.hermes`，生成 Hermes 配置并在 OpenShell proxy 后启动 `hermes gateway run`。([docs.nvidia.com](https://docs.nvidia.com/nemoclaw/user-guide/hermes/reference/architecture?utm_source=openai))

但真正实施运行时安全边界的仍是 OpenShell：

- Gateway 负责创建、删除和监控 sandbox。
- Gateway 保存和分发 provider credential。
- Gateway 下发网络、文件系统和推理配置。
- Sandbox 内的 proxy、OPA、Landlock 和 seccomp 实施具体策略。
- Sandbox 是运行 Agent 的隔离数据面。([docs.nvidia.com](https://docs.nvidia.com/openshell/latest/sandboxes/manage-gateways?utm_source=openai))

---

# 当前 SIQ 架构

## 1. 自研控制面

SIQ 没有使用：

```text
nemoclaw
nemohermes
NemoClaw blueprint runner
NemoClaw Hermes plugin
NemoClaw dashboard
```

项目明确选择不引入 NemoClaw，相关约束记录在：

- `infra/openshell/README.md:1`
- `infra/openshell/reference/hermes-integration-notes.md:1`
- `infra/openshell/reference/github-hermes-openshell-projects.md:1`

SIQ 自己承担了原本由 NemoClaw host orchestration 层负责的部分职责：

- OpenShell 安装和版本冻结
- Gateway 生命周期
- Provider 初始化
- Hermes 镜像构建
- Runtime snapshot
- Policy 编译
- Mount plan
- Sandbox 生命周期
- API 路由
- Pool registry
- Lease 和 fencing
- Restart recovery
- Host fallback
- Sandbox generation
- 闲置资源回收

---

## 2. 版本与供应链固定

当前项目冻结：

```text
OpenShell version: v0.0.83
Upstream commit: e3d26dd3ae0dee247bbc5db368545832757ac493
License: Apache-2.0
```

同时记录了：

- 上游源码摘要
- 原始 binary SHA-256
- patched binary SHA-256
- 本地补丁 SHA-256
- 固定 release URL
- 固定 upstream commit

版本契约位于：

- `infra/openshell/upstream-version.json:1`

项目还对 OpenShell sandbox 应用了 Landlock 文件访问补丁：

- `infra/openshell/patches/v0.0.83/0001-landlock-mask-file-access.patch`

这相当于自行实现了 NemoClaw versioned blueprint 的部分供应链职责，但目前还没有实现 NemoClaw 完整的 blueprint digest runner 和官方升级兼容矩阵。

---

## 3. 独立 OpenShell Gateway

当前运行的是 SIQ 专用 Gateway：

```text
Gateway: siq-openshell-dev
Endpoint: https://127.0.0.1:17671
OpenShell version: 0.0.83
Status: Connected
```

该 Gateway 使用项目独立的：

- XDG 配置目录
- TLS 状态
- Gateway 数据库
- Docker CLI 配置
- Provider inventory
- 运行日志
- maintenance lock
- 私有运行状态目录

OpenShell 官方定义 Gateway 为控制面，负责 sandbox 生命周期、provider credential、策略下发、推理配置和 sandbox 连接管理；SIQ 当前直接使用的就是这一层，而不是自行模拟 Gateway。([docs.nvidia.com](https://docs.nvidia.com/openshell/latest/sandboxes/manage-gateways?utm_source=openai))

---

## 4. Hermes 运行在真实 OpenShell Sandbox 内

SIQ 构建自己的 Hermes sandbox image 和 runtime snapshot，在 sandbox 内启动现有 Hermes Gateway。

保留原有 SIQ Hermes 协议：

```text
POST /v1/runs
GET  /v1/runs/{run_id}
GET  /v1/runs/{run_id}/events
POST /v1/runs/{run_id}/stop
```

宿主 API 不直接进入容器，而是通过 OpenShell service forwarding：

```text
Host local pool port
  → OpenShell Gateway
  → sandbox loopback :28651
  → Hermes /v1/runs
```

核心生命周期实现：

- `scripts/openshell/siq_analysis_lifecycle.py:923`
- `scripts/openshell/siq_analysis_canary.py:104`
- `scripts/openshell/siq_analysis_pool_lifecycle.py:43`
- `scripts/openshell/siq_analysis_wide_pilot.py:809`

这与 NVIDIA 官方 NemoClaw Hermes 架构在技术方向上一致：Hermes 运行于 OpenShell sandbox 内，并由 Gateway 和 proxy 管理外部能力；区别是 SIQ 使用自己的 `/v1/runs` 契约和生命周期，而不是 NemoClaw onboarding 与 plugin。([docs.nvidia.com](https://docs.nvidia.com/nemoclaw/user-guide/hermes/about/how-it-works?utm_source=openai))

---

# OpenShell 能力使用情况

## 5. Provider 和凭据隔离

当前 Gateway 已配置：

```text
siq-minimax-cn-pool
siq-stepfun
siq-kimi-coding
siq-tavily-search
```

Provider credential 由 OpenShell Gateway 保存，sandbox 只接收受控 provider 配置或 credential placeholder，不需要把真实上游密钥直接写入 Hermes profile、业务代码或 Git。

OpenShell 官方将 Provider 作为一等资源管理，负责向 sandbox 提供 credential；官方最新实现还提供 Providers v2，包括 profile-backed provider policy、provider-owned network rules、credential refresh，以及运行时 provider attach/detach。([docs.nvidia.com](https://docs.nvidia.com/openshell/latest/sandboxes/manage-providers?utm_source=openai))

SIQ 当前已经使用 Provider 的核心 credential isolation 能力，但尚未声明完全采用官方最新 Providers v2 的所有动态能力。

---

## 6. 文件系统安全边界

每个公司 scope sandbox 使用编译后的 mount plan。

当前策略包括：

- 当前公司 Wiki 资料只读
- `company.json` 等固化输入只读
- 项目控制面只读
- 其他公司目录不可写
- 当前公司 `analysis/` 可写
- Sandbox `/sandbox` runtime 独立可写
- Hermes session 独立
- memory、checkpoint、response store 独立
- runtime snapshot 独立
- 删除守卫限制危险操作
- 跨公司写入 probe 必须被拒绝

当前 canary manifest 明确声明：

```text
write_scope=current_company_analysis_root
normal_business_mutations=[
  create,
  modify,
  rename,
  delete
]
mount_count=7
broker_request_identity_required=true
```

OpenShell 官方安全模型将文件系统、网络、进程和推理划分为四层。文件系统和进程属于 sandbox 创建时锁定的静态控制，修改这些边界需要重新创建 sandbox；网络和推理则可以动态更新。([docs.nvidia.com](https://docs.nvidia.com/openshell/sandboxes/policies?utm_source=openai))

SIQ 当前“公司变化创建新 sandbox generation”的设计正符合这一模型，而不是在一个已启动 sandbox 中不断扩大静态文件权限。

---

## 7. 进程安全

当前 sandbox 使用 OpenShell 的进程和用户权限模型：

- 非特权用户运行
- 固定 UID/GID
- Seccomp 约束
- 禁止危险系统调用
- 禁止特权提升
- 固定启动命令
- Sandbox identity 与 container identity 双重校验
- 删除时校验 sandbox ID、container ID、run ID 和 nonce

OpenShell 官方将 process protection 定义为不可热更新的静态层，主要通过 seccomp BPF 和 privilege drop 防止提权、危险 syscall 和进程滥用。([docs.nvidia.com](https://docs.nvidia.com/openshell/latest/security/best-practices?utm_source=openai))

---

## 8. 网络与 Broker 边界

项目没有给 sandbox 开放无限制网络，而是实现了：

- OpenShell network policy
- Provider inference route
- Host egress broker
- Host data broker
- Request identity
- Broker token
- Method allowlist
- Content-Type 限制
- Payload 大小限制
- SSRF 防护
- 直接公共网络访问拦截
- 直接上传工具拦截
- Broker 审计
- Sandbox provider placeholder
- Credential unresolved 时 fail-closed

当前策略目标包括阻止：

```text
curl upload
scp
sftp
rsync
rclone
direct public TCP
direct public UDP
direct public WebSocket
```

OpenShell 官方同样把网络出站和推理调用分开管理，推荐模型推理通过 Gateway inference routing，而不是把 provider host 直接放入普通 network allowlist。([docs.nvidia.com](https://docs.nvidia.com/openshell/latest/security/best-practices?utm_source=openai))

---

# 自研 Pool 与恢复层

## 9. Company Scope Registry

SIQ 使用自己的 registry 把公司 scope 映射到 OpenShell sandbox：

```text
market + canonical company
  → scope_id
  → canary run_id
  → local forwarding port
  → sandbox identity
  → API key digest
  → analysis path
  → base session namespace
```

实现位于：

- `scripts/openshell/siq_analysis_pool_registry.py:723`

Registry 支持：

- 多公司 binding
- 固定公司 scope ID
- 动态端口分配
- 端口 reservation
- 精确 unregister
- Host unmatched fallback
- 防止同一公司重复 binding
- 防止端口和 sandbox identity 冲突

---

## 10. Lease、并发与 Fencing

当前 pool 实现：

- `active`
- `waiting`
- `orphaned`
- `released`
- 单公司单写者
- 最大并发约束
- Lease TTL
- Owner token
- Owner generation
- Heartbeat
- Run-bound 标记
- Terminal confirmation
- Recovery takeover
- Stale owner fencing

实现位于：

- `scripts/openshell/siq_analysis_pool_concurrency.py:1065`
- `apps/api/services/openshell_pool_adapter.py:309`

请求完成后，只有在 Hermes terminal 且写入已静默时才正常释放写入 lease；不确定状态会进入 orphan/recovery 流程，而不是立即允许第二个写者。

---

## 11. API 重启恢复

项目实现了独立的 restart recovery manager：

- 扫描 durable active-run lease
- 对旧 API owner 执行 fencing
- 接管 pool owner generation
- 查询原 Hermes run
- 等待 terminal/write-quiesced
- 释放 terminal lease
- 保留不确定 writer 为 orphaned
- 防止两个 API worker 同时恢复
- 使用固定 recovery lock 建立恢复权限

代码位于：

- `apps/api/services/openshell_pool_recovery.py:131`

当前 API 健康状态为：

```text
openshell_recovery.enabled=true
openshell_recovery.required=true
openshell_recovery.ready=true
```

同时已修复同一 API 进程多次生成不同 runtime owner ID、导致恢复管理器误判当前请求为旧进程请求的问题：

- `apps/api/services/runtime_coordination.py:25`
- `apps/api/services/runtime_coordination.py:96`

---

# 最新 Conversation Sandbox Generation

## 12. 一个前端对话，多代 Sandbox

最新实现不再把一个前端对话永久绑定到一个拥有宽权限的 sandbox。

采用：

```text
一个前端 conversation
  → 一个逻辑 OpenShell 会话
  → 可包含多个 company-scoped sandbox generation
```

Generation 安全范围为：

```text
tenant/user
+ profile
+ company scope
+ sandbox run
+ provider/write policy
+ conversation affinity
```

核心代码：

- `apps/api/services/openshell_scope_lifecycle.py:67`
- `apps/api/services/agent_chat_runtime_impl.py:2183`
- `apps/api/services/agent_chat_runtime_impl.py:6713`

运行手册：

- `docs/runbooks/openshell/siq-analysis-conversation-generations.md:1`

---

## 13. 同公司连续追问

同一前端对话连续询问相同公司：

```text
上汽集团 → 上汽集团 → 上汽集团
```

会复用：

- 相同 company scope
- 相同 warm sandbox run
- 相同 Hermes conversation-affinity namespace
- 相同 `sandbox_generation_id`

这样可以保持：

- 连续追问上下文
- Hermes session 连续性
- 工具缓存
- checkpoint
- 当前任务中间状态

同时不会与其他用户或其他对话共享 Hermes namespace。

---

## 14. 同一对话切换公司

同一对话从上汽集团切换到贵州茅台：

```text
generation A：600104-上汽集团
generation B：600519-贵州茅台
```

API 会：

1. 根据结构化 context 验证 market、company code、name 和 directory。
2. 查找目标公司 pool binding。
3. 如果不存在，自动创建新的 company-scoped sandbox。
4. 执行 lifecycle probe。
5. 为同一前端 conversation 生成新的 lease-affinity namespace。
6. 使用新的 `sandbox_generation_id`。
7. 按目标公司过滤历史和 agent memory。
8. 只挂载目标公司允许的数据范围。
9. 请求完成后释放 lease。

真实同 session 验收结果：

```text
session:
user-9-analysis-a2b590f2

上汽 A1:
canary_run_id=canary-13c8e5482e92
scope_id=9bc20683a73220cad2e19d40
generation_id=0709b62db28e0d4f

贵州茅台:
canary_run_id=canary-1dd963506a88
scope_id=7025f3f8b5186fe8a87f8a12
generation_id=176faeacb262b32a

返回上汽 A2:
canary_run_id=canary-13c8e5482e92
scope_id=9bc20683a73220cad2e19d40
generation_id=0709b62db28e0d4f
```

这证明：

- 同公司 generation 可复用。
- 公司切换会改变 scope 和 generation。
- 返回仍然 warm 的原公司时，可以重新使用原 generation。
- 三次请求都实际经过 OpenShell。
- 请求完成后没有残余 lease。

---

## 15. Runtime Provenance

每次 OpenShell 回答现在可以携带：

```text
runtime_target
canary_run_id
sandbox_generation_id
sandbox_scope_id
sandbox_company
```

示例：

```json
{
  "runtime_target": "openshell",
  "canary_run_id": "canary-13c8e5482e92",
  "sandbox_generation_id": "0709b62db28e0d4f",
  "sandbox_scope_id": "9bc20683a73220cad2e19d40",
  "sandbox_company": "600104-上汽集团"
}
```

Generation ID 是不可逆摘要，不会暴露：

- API key
- Owner token
- Lease identity key
- 原始内部 namespace
- 用户凭据

---

# 自动创建与回收

## 16. Scope Auto-Provisioning

当前部署已启用：

```text
SIQ_OPENSHELL_SCOPE_AUTO_PROVISION=1
```

对于一个经过验证、但尚无 binding 的公司：

1. API 获取固定 OpenShell maintenance lock。
2. 检查现有 registry。
3. 分配可用 forwarding port。
4. 生成新的 `canary-<hex12>` run ID。
5. 创建 runtime snapshot。
6. 编译 mount plan 和 policy。
7. 创建 OpenShell sandbox。
8. 启动 sandbox 内 Hermes。
9. 启动 authenticated service forward。
10. 启动 guard。
11. 注册 pool binding。
12. 执行 lifecycle probe。
13. probe 通过后才允许真实请求进入。

自动 provisioning 代码：

- `apps/api/services/openshell_scope_lifecycle.py:91`
- `apps/api/services/openshell_scope_lifecycle.py:115`
- `apps/api/services/openshell_scope_lifecycle.py:147`

并发请求同一个缺失 scope 时，会在 API 内进行串行化，防止创建重复 sandbox。

---

## 17. 空闲 TTL 回收

当前部署配置：

```text
SIQ_OPENSHELL_SCOPE_IDLE_TTL_SECONDS=300
SIQ_OPENSHELL_SCOPE_SWEEP_SECONDS=30
```

回收条件必须同时满足：

- 公司 scope 超过 300 秒未使用
- `active_leases=0`
- `waiting_leases=0`
- `orphaned_leases=0`
- Binding identity 未变化
- Sandbox lifecycle identity 验证通过
- Maintenance lock 获取成功

当前实时状态已经证明 TTL 回收实际生效：

```text
OpenShell gateway: running
Provider inventory: present
Sandbox list: []
Pool registry bindings: []
Pool scheduler bindings: []
Active/waiting/orphaned leases: 0
```

也就是说，此前用于真实测试的上汽集团和贵州茅台 sandbox 已经在空闲 TTL 后被自动、安全地删除。

但 runtime selection 仍保持：

```text
target=openshell
session_mode=all
unmatched_scope=host
```

因此，下一次带有有效公司上下文的分析请求会自动创建新的 sandbox，而不是永久回退 Host。

这是当前实现相较上一版文档最重要的变化：

> **OpenShell 已从“长期驻留的手工 canary”升级为“按公司 scope 按需创建、对话 generation 复用、空闲自动销毁”的执行池。**

---

# 多公司比较

## 18. 当前安全行为

当前自动 provisioner只接受一个经过验证的公司 scope。

例如：

```text
比较上汽集团和贵州茅台的现金流质量
```

不能通过模型自行判断后，直接把两个公司的可写目录挂进一个已有单公司 sandbox。

当前不会：

- 将所有公司目录挂入单一 sandbox
- 给 sandbox 全公司写权限
- 根据自然语言动态扩大静态文件权限
- 把多公司问题错误路由到其中一家公司的写入 sandbox

由于 OpenShell 文件系统和进程权限属于创建时锁定的静态边界，多公司权限应通过创建新的专用 sandbox generation 实现，而不是热修改原 sandbox。([docs.nvidia.com](https://docs.nvidia.com/openshell/sandboxes/policies?utm_source=openai))

---

## 19. 规划中的 Multi-company Generation

正式支持多公司比较需要独立契约：

```text
normalized company set
+ all source companies read-only
+ isolated comparison workspace read-write
+ no company analysis root directly writable
+ dedicated generation ID
+ Host-side publication
```

计划行为：

```text
conversation-123
├── generation-A：上汽集团
├── generation-B：贵州茅台
└── generation-C：上汽 + 茅台比较
```

在该 mount contract 和测试完成前，多公司请求继续采用安全 fallback，不会获得宽泛 sandbox 权限。

---

# 前端真实流量

## 20. 已验证的真实链路

不是只验证 CLI 或 `/health`。

已通过真实前端同源接口：

```text
POST /api/analysis/chat/stream
```

验证：

- HTTP `200`
- SSE 完整结束
- 输出 `OK`
- 执行期间观察到目标 OpenShell active lease
- Lease 对应正确的 company scope
- Lease 对应正确的 canary run
- Runtime provenance 返回 generation 信息
- 请求完成后 lease 归零
- 没有 Host 冒充 OpenShell
- 没有 owner mismatch
- 没有 release error
- 没有 orphaned lease

当前前端正常分析请求的流程是：

```text
前端分析助手
  → API 鉴权
  → 公司 context 验证
  → OpenShell scope resolve
  → 缺失则自动创建 sandbox
  → 获取 pool lease
  → Hermes /v1/runs
  → SSE 返回
  → terminal/write-quiesced
  → 释放 lease
  → 空闲 TTL 后删除 sandbox
```

---

# 当前能力矩阵

| 能力 | 当前状态 |
|---|---|
| Hermes 在真实 OpenShell sandbox 中运行 | 已实现 |
| OpenShell Gateway | 已实现并运行 |
| 独立项目 Gateway | 已实现 |
| Service forwarding | 已实现 |
| Provider credential isolation | 已实现 |
| Provider/inference routing | 已实现 |
| 文件系统隔离 | 已实现并 probe |
| Landlock | 已实现并有本地补丁 |
| 进程和 seccomp 边界 | 已实现 |
| 当前公司写入边界 | 已实现 |
| 跨公司拒写 | 已实现并 probe |
| Network policy | 已实现 |
| Host egress broker | 已实现 |
| Host data broker | 已实现 |
| Broker request identity | 已实现 |
| 生命周期 start/status/probe/stop/rollback | 已实现 |
| 多 sandbox pool | 已实现 |
| 单写者 lease | 已实现 |
| Waiting/orphaned 状态 | 已实现 |
| Owner generation fencing | 已实现 |
| API restart recovery | 已实现 |
| Host fallback | 已实现 |
| 前端真实业务流量 | 已验证 |
| 新公司自动建 sandbox | 已实现并真实验证 |
| 同对话跨公司 generation | 已实现并真实验证 |
| Generation provenance | 已实现 |
| 空闲 TTL 自动回收 | 已实现并真实验证 |
| Providers v2 全功能 | 尚未采用 |
| Multi-company 只读比较 sandbox | 尚未实现 |
| NemoClaw blueprint/onboarding/dashboard | 未采用 |
| Formal production quality gate | NO_GO |

---

# 与 NVIDIA 最新 OpenShell 的差异

## 已采用的核心能力

当前实现与 OpenShell 官方核心模型一致：

- Gateway 是控制面
- Sandbox 是数据面
- 静态文件系统权限在创建时确定
- 进程权限在创建时确定
- 网络策略由 Gateway 管理
- Provider credential 不写入 Agent 代码
- 推理和外部服务通过受控路径
- Sandbox 可以使用自定义镜像
- Sandbox 生命周期可创建、连接、转发和删除

OpenShell 官方将 sandbox 定义为结合运行隔离与策略控制的私有 Agent 执行环境，用于防止未授权数据访问、credential 暴露和网络外传。([docs.nvidia.com](https://docs.nvidia.com/openshell/latest/about/overview?utm_source=openai))

## 尚未采用的最新能力

官方最新文档已经包含：

- Providers v2
- Provider profile-backed policy
- Provider-owned network rules
- Gateway-managed credential refresh
- Runtime provider attach/detach
- 更完整的动态 network policy update
- 官方 Gateway 多部署模式
- 官方 blueprint/NemoClaw 操作体验

当前 SIQ 固定在 OpenShell `v0.0.83` 和自定义 provider 类型上，不能直接宣称具备最新 OpenShell 所有能力。([docs.nvidia.com](https://docs.nvidia.com/openshell/latest/sandboxes/manage-providers?utm_source=openai))

---

# 与 NemoClaw 的区别

当前项目不是 NemoClaw 的完整等价实现。

没有实现或没有采用：

- `nemoclaw` CLI
- `nemohermes` CLI
- 官方 onboarding wizard
- NVIDIA versioned blueprint runner
- 官方 Hermes plugin
- 官方 dashboard
- 官方 messaging channel 配置
- 通用 MCP server 管理
- NemoClaw state snapshot/backup UX
- 官方 inference configuration UX
- 官方升级和支持矩阵

NemoClaw 的优势是：

- 标准化安装
- 可重复 onboarding
- 官方 blueprint
- Digest verification
- 官方 agent manifest
- 通用运维体验
- 官方兼容性与升级路径

SIQ 的优势是：

- 保持现有 Hermes `/v1/runs` 契约
- 保持 SIQ profile、prompt 和 tool 行为
- 深度集成公司 Wiki
- 深度集成 `analysis/` 输出
- 自定义 durable active-run coordination
- 公司 scope pool
- 对话 sandbox generation
- 自定义 broker identity
- 自定义 Host fallback
- 自定义恢复和 fencing
- 不引入 NemoClaw Hermes plugin 行为变化

---

# 是否应切换到 NemoClaw

目前仍不建议直接替换。

原因是项目已经深度绑定：

- SIQ `/v1/runs`、SSE 和 stop 契约
- 公司 Wiki 数据目录
- 公司 `analysis/` 输出路径
- API durable coordination
- Company scope registry
- Conversation generation
- 自定义 Provider
- Broker request identity
- 自定义 Hermes profile
- 自定义报告和工具流程
- OpenShell `v0.0.83`
- Landlock 本地补丁

直接换成 NemoClaw 可能改变：

- Hermes 配置路径
- Plugin 和 hook
- Prompt 注入
- 工具选择
- Session 状态
- Provider 配置
- Sandbox 生命周期
- 输出行为

更合理的路线是：

1. 保留当前原生集成。
2. 持续对照 NemoClaw 官方架构。
3. 引入 blueprint digest 和可重复构建思想。
4. 补齐 provider refresh 和 Providers v2 评估。
5. 补齐 multi-company comparison sandbox。
6. 补齐监控、备份和升级策略。
7. 单独开展 NemoClaw Hermes plugin 兼容性 A/B，而不是直接安装到现有运行面。

---

# 测试与发布状态

最新相关回归：

```text
78 passed
```

已覆盖：

- Runtime selection
- Pool binding
- Lease acquire/release
- Conversation affinity namespace
- Scope auto-provision
- 并发只创建一个 binding
- Port retry
- Maintenance lock
- Idle TTL candidate
- Recovery manager
- Host fallback
- Runtime owner stability
- Sandbox generation provenance
- 同对话跨公司切换

但正式 A/B `formal11` 仍然是：

```text
quality_gate_passed=false
```

主要 blocker 仍包括：

- OpenShell timeout rate 超过门槛
- P95 延迟回归
- Citation rate 不足
- Evidence coverage 不足
- Hallucination block rate 不足
- Report completeness 不足
- Task success rate 不足
- Host/OpenShell primary route telemetry 不完整
- Contract failure

因此当前准确状态是：

```text
Demo / Canary runtime: 已上线
真实前端 OpenShell 流量: 已验证
自动 sandbox lifecycle: 已验证
Conversation generations: 已验证
Idle TTL cleanup: 已验证
Formal production quality gate: NO_GO
```

---

# 最终评价

> **SIQ Research Engine 已经在不依赖 NemoClaw 的前提下，独立完成了 OpenShell 与 Hermes 的真实结合，并实际发挥了 OpenShell 的 Gateway、Sandbox、Provider、Policy、Filesystem、Process、Network、Forwarding 和 Lifecycle 能力。**

最新实现已经不再只是手工启动一个长期 canary，而是升级为：

```text
按公司 scope 自动创建
→ 同对话同公司 generation 复用
→ 同对话切公司 generation 隔离
→ 请求级 lease
→ terminal/write-quiesced 释放
→ 空闲 TTL 自动销毁
→ 下一请求重新创建
```

但它仍然是：

> **SIQ 定制的原生 OpenShell + Hermes demo/canary control plane**

而不是：

> **NVIDIA 官方 NemoClaw 支持路径或已经通过 formal production gate 的正式生产发行版。**

此外，最新实现代码已部署并运行，但相关 conversation-generation 和 auto-provision 变更目前仍在工作区，尚未完成独立 Git 提交。
