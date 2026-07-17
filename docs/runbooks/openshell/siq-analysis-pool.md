# siq_analysis 多公司 OpenShell 运行池增量方案

## 当前实现边界

2026-07-17 live 快照：上汽集团运行 `canary-8e695febb483`、宿主端口 `28652`，贵州茅台
运行 `canary-ee8d4d9c4e97`、宿主端口 `28653`；两者均使用候选镜像
`siq/hermes-openshell-siq-analysis:aadca453805ef6d783956e93`，lifecycle `status`、认证
`probe` 和 Docker health 均正常。API pool recovery 为 ready。该快照的范围是可信内部
公司级 canary，不是正式 V0.6 GO。

`scripts/openshell/siq_analysis_pool_registry.py` 提供最小、credential-free 的公司级
运行池注册表。它已经能够：

- 只用 canonical `market/company` 选择 binding，不从用户问题正文猜公司；
- 每次解析时重新验证 owner-only `active.json`、manifest、API key 摘要、policy 和
  mount plan；
- 为每家公司绑定独立的 sandbox、run、runtime snapshot、session namespace、
  `analysis/` 写根和 loopback forward 端口；
- 未注册公司返回 `host`；已注册但状态、摘要或隔离契约损坏时失败关闭，不把失败请求
  自动重放到 Host；
- 注册表只保存 key SHA-256 和状态指针，不保存明文 key。CLI 的 `resolve` 也不输出 key。

当前两个 live binding 均使用 owner-only pool slot；旧 `28651` legacy slot 已通过受约束
迁移退出 live registry，只保留兼容读取和 `migrate-legacy` 维护路径。只有显式执行 start
并在双重认证健康检查后注册的公司才会获得 binding；未注册公司不会因 manager 存在而
自动启动 sandbox，而是继续使用 Host。

`scripts/openshell/siq_analysis_pool_concurrency.py` 在同一 owner-only flock 下增加持久化
admission/affinity 状态：

- scheduler 的完整路由身份契约为
  `tenant_id + user_id + session_id + canonical market/company`；状态文件只保存
  project-local HMAC，不保存原始用户或会话 ID。当前 API 在 session 所有权校验后显式传入
  `DEFAULT_TENANT_ID` 和已认证 `user_id`；durable runtime coordination row 保存同一
  principal，恢复、heartbeat、takeover 和 release 必须精确匹配，半缺失 principal 失败关闭。
  用户身份不再从 session ID 推导，但这仍不等于共享长驻 sandbox 具备强多租户物理隔离；
- 同一身份重连保持原 binding，同一 session 试图在运行中换公司会失败，绝不热切
  sandbox scope；
- 当前完整 `analysis/` 可写的 slot 固定 `max_active=1`，同公司其他写任务按持久化 FIFO
  排队；跨公司 slot 可以并行；
- lease 默认心跳 TTL 为 900 秒，每个 binding 最多保留 64 个 waiter、全池最多 1024 个
  lease。waiting 到期可以删除；active 到期只能转为 `orphaned` 并继续占住 writer，绝不
  自动提升下一位。只有 exact Hermes run 的 terminal/stop receipt 确认后，release 才能
  删除 active/orphaned lease 并原子提升 FIFO；
- `draining` 停止接收新 lease，既有 lease 不再续期且默认 300 秒后转 `orphaned`；
  `failed` 撤流并把 active 置为 quarantine/orphaned，不假定底层 run 已停止。两者都只
  隔离对应公司，不影响其他公司；
- 每个 active lease 派生不同的 Hermes session namespace 和 `.work/openshell-leases/`
  写目录标识。

FIFO admission 和 active writer 状态由 owner-only 共享状态与锁跨 API 进程协调，不是单个
worker 的内存队列。因此多个 API worker/进程同时请求同一家公司时仍只有一个 active
writer；不同公司的 slot 可以并行。

当前的 task policy 仍允许该公司完整 `analysis/`，所以派生写目录只是工作流目标，不是
新的 Landlock 安全边界。为避免把“路径命名不同”误当成真正隔离，当前 scheduler 强制
每公司单写者。真正的同公司并行必须新增多个固定公司 replica，并让每个 replica 的
mount/policy 只写自己的 task leaf，再由宿主 Publisher 串行发布最终产物；在此之前不得
把 `max_active` 调大。

## 多用户信任与物理隔离边界

当前 pool 解决的是公司级写并发和路由亲和，不是同一长驻 sandbox 内的强用户隔离：

- 同一公司固定一个 active writer，其他请求持久化 FIFO 排队；不同公司的独立 slot 可以
  并行。这防止公司 `analysis/` 并发写冲突，但不阻止后一位用户读取前一位用户留下的状态；
- API 的 session 所有权校验、按 session/company 过滤的 history、宿主 Agent memory ACL，
  以及每个 active lease 的 Hermes session namespace 都属于逻辑隔离。它们阻止正常 API
  和 Hermes 会话复用发生串话，不构成 sandbox 内文件读取的物理边界；
- 同一公司长驻 sandbox 仍共享 `state.db`、`response_store.db`、sessions、checkpoints、
  memories、cache/log/workspace 和完整 `analysis/` 写根。terminal、file 或 code execution
  与 gateway 处于同一 sandbox/用户权限域时，可以看到同一 runtime snapshot 内其他 session
  或旧 lease 的状态；`.work/openshell-leases/<lease-id>` 目前也只是命名约定；
- 因此当前公司长驻模式只允许用于可信内部单租户、且公司级输出本来允许共享的运行范围。
  `session_mode=all` 只表示匹配公司的会话具备路由资格，不表示已经取得多用户或多租户
  物理隔离 GO。

若目标范围包含互不信任用户或多个租户，切流前至少完成以下一种边界：

1. 在现有显式认证 principal 基础上，把整个 slot/runtime epoch 排他绑定单一 principal；
   principal 变化时必须 drain，并以 fresh runtime snapshot 重建后才接纳下一位；
2. 每个 lease 使用 fresh sandbox/gateway replica，只挂载当前 task leaf，并由宿主 Publisher
   串行发布正式产物。

两种方案都必须使 agent tool 无法读取 runtime-state 和 sibling lease 目录，并以两个用户在
同一公司顺序运行的 API、Hermes、SQLite、checkpoint 和工作目录负向测试证明。完成前，任何
readiness 或发布结论必须明确限定为“可信内部单租户公司级 canary”，不得表述为正式强多租户
GO。

## Slot 契约

当前 live slot 统一使用：

```text
var/openshell/canary/siq-analysis/pool/slots/<scope-id>/active.json
var/openshell/canary/siq-analysis/pool/slots/<scope-id>/runs/<run-id>/
127.0.0.1:<28652..28750> -> sandbox:28651
```

旧兼容布局只允许由 `migrate-legacy` 识别并退出，不再作为新增公司的注册目标：

```text
var/openshell/canary/siq-analysis/active.json
var/openshell/canary/siq-analysis/runs/<run-id>/
127.0.0.1:28651 -> sandbox:28651
```

`scope-id` 是 canonical `market + NUL + company` 的 SHA-256 前 24 位。端口只用于宿主
loopback forward；每个容器有独立网络命名空间，所以 sandbox 内 Hermes 仍监听固定
`28651`，不需要为每家公司重编译镜像。

每个 binding 必须同时唯一：

- `market/company` 和 `analysis_relative_path`；
- `run_id`、sandbox name、runtime snapshot；
- local forward port；
- session namespace。

mount plan 仍固定为一个 Wiki 只读 mount、当前公司唯一 `analysis/` 可写 mount、五个
run-specific runtime mounts。其他公司目录、项目代码、Prompt、workflow、凭据和控制面
不得因池化变成可写。

## CLI

新增公司通过 manager 启动。manager 在同一进程内预留端口，reservation token 不进入
命令行或 JSON 输出；status/stop 从已验证 binding 读取 endpoint，不接受运维者手写端口：

```bash
scripts/openshell/run_siq_analysis_pool_lifecycle.sh start \
  --acknowledge-not-production-canary \
  --market cn \
  --company '600519-贵州茅台' \
  --run-id canary-0123456789ab \
  --local-port 28652

scripts/openshell/run_siq_analysis_pool_lifecycle.sh status \
  --market cn \
  --company '600519-贵州茅台' \
  --run-id canary-0123456789ab

scripts/openshell/run_siq_analysis_pool_lifecycle.sh stop \
  --market cn \
  --company '600519-贵州茅台' \
  --run-id canary-0123456789ab
```

wrapper 以空环境启动 Python；start/migrate-legacy/stop/rollback 还必须持有项目
maintenance lock。
manager 不输出 API key、run nonce 或 reservation token。

旧 `28651` binding 只能通过单一维护锁下的受约束命令迁移，不能手工拆成
unregister/stop/start：

```bash
scripts/openshell/run_siq_analysis_pool_lifecycle.sh migrate-legacy \
  --acknowledge-not-production-canary \
  --market cn \
  --company '600104-上汽集团' \
  --old-run-id canary-0123456789ab \
  --new-run-id canary-fedcba987654 \
  --local-port 28652
```

该命令先校验 exact legacy binding 和运行状态，再要求 active/orphaned/waiting lease 全为
零并进入 draining；随后复验 registry 未变化、注销旧 binding，并确认该公司已解析为 Host，
才精确停止旧 sandbox 并启动新 slot。旧 stop、新 start 或 postcheck 失败时，公司保持 Host；
其他公司 binding 必须逐项不变。注销失败且旧 binding 可证实时恢复 accepting，注销结果不
确定时失败关闭。这个 Host fallback 是迁移维护窗口的显式业务连续性边界，不是已注册
OpenShell 请求失败后的静默重放。

底层 registry CLI 仍用于审计和新 slot 规划；live lifecycle 与 legacy binding 变更必须走
上述 manager，不得直接调用 `register`/`unregister` 拆分事务。

规划新 slot 和选择空闲端口：

```bash
python3 scripts/openshell/siq_analysis_pool_registry.py allocate-port
python3 scripts/openshell/siq_analysis_pool_registry.py plan \
  --market cn \
  --company '600519-贵州茅台' \
  --run-id canary-0123456789ab \
  --local-port 28652
```

`allocate-port` 在 registry flock 内写入 900 秒 owner-only reservation，并返回
`reservation_token`。新增非 legacy slot 的 `register` 必须同时提交该 token；仅观察到
socket 当前空闲不构成端口所有权。

检查路由只返回脱敏信息：

```bash
python3 scripts/openshell/siq_analysis_pool_registry.py resolve \
  --market cn \
  --company '600104-上汽集团'
```

## lifecycle 参数化

第二家公司 lifecycle 已按以下边界实现，不能复制 legacy shell 命令绕过 manager：

1. `NonProductionLifecycleSettings` 已增加 slot state root；所有 active/run path 校验改为
   settings-relative，legacy path 继续兼容。
2. `LifecycleAdapter` 已区分固定 sandbox target port `28651` 与 per-slot local port；forward
   argv、process receipt、status 和 stop 的端口核验全部消费同一个 endpoint 对象。
3. preflight 的“gateway 必须零 sandbox”已改为“除 registry 中身份完全匹配的
   pool-owned sandbox 外不得有其他 sandbox”。仍需验证 namespace、label、run nonce、
   container ID 和端口唯一，不能按名称宽松放行。
4. start 只在 authenticated forward `/v1/models` 和 OpenShell `sandbox exec` 内部认证探针
   同时成功、manifest 已为 `running` 后消费 reservation 并注册 binding。Docker 外层
   namespace 的 health 状态不作为业务健康判据。
5. stop 顺序固定为 `drain -> 确认 active/orphaned/waiting lease 全为零 -> durable stopping
   marker -> unregister -> 精确停止 forward/sandbox/guard`。registry 仍会拒绝任何 live
   lease；若 marker 后注销中断，重试只读核对 draining/零 lease 后继续，不重新解析已失效
   的 running binding。这样不会因 active TTL、API 重启或心跳中断放行第二个 writer。

代码和离线测试已覆盖独立 state/endpoint、精确 stop 和 legacy 迁移；2026-07-17 已用
`28652 + 28653` 两个真实 sandbox 验证两家公司可同时健康运行和跨公司并行。当前每个实例
上限为 4 CPU、8 GiB，先采用最多两个常驻公司；更多公司应做有上限的按需启动与空闲回收，
而不是无界常驻。

## API 路由与上下文隔离

独立的 `apps/api/services/openshell_pool_adapter.py` 已提供安全模块加载、
`resolve_binding(context)`、同步/异步 acquire-wait-release-heartbeat 以及稳定无密钥错误，
主 API 已按 canonical `market/company` 接入 admission 后再创建 Hermes run。selector 只能
执行一次，precheck 和实际 create 必须消费同一个带 `expected_run_id` 的不可变
binding/lease 对象，避免两次解析之间发生排空或公司切换竞态：

- exact binding 健康：使用该 slot 的 endpoint、key 和 session namespace；
- 公司未注册：继续 Host；
- 公司已注册但 binding 损坏或停止中：返回明确运行时错误，不自动回放；
- 非 `siq_analysis` profile：继续 Host。

route/lease 必须在 API 任务记录中持久化其 `lease_id`、scope/run 摘要、runtime target 及
显式 `DEFAULT_TENANT_ID + authenticated user_id` principal，使 recovery、reconnect、stop、
heartbeat、审计和历史查询使用同一 binding 和身份 CAS。历史消息和 Agent memory 仍需按
tenant、user、session 及 canonical company identity 过滤，不能只按外部 session ID 查询。

OpenShell 不会把 sandbox 自身状态注入模型上下文。真正的串话风险来自复用 Hermes
session、checkpoint 或原生 memory。因此每个 slot 使用 run-specific fresh runtime
snapshot，并把 session namespace 同时绑定 `scope-id + run-id + profile`。即使外部
session ID 相同，不同公司也不会命中同一个 Hermes session 文件。SIQ 的 PostgreSQL/
Milvus Agent memory 仍由宿主 API 按 tenant、agent group 和 ACL 管理，sandbox 不获得
其写凭据。

上述规则使正常 API history/memory 和 Hermes namespace 不会因“新开会话”自动串用旧会话
上下文；当前 API recovery ready 也只恢复 exact durable principal 的 lease。它不清空同一
公司长驻 sandbox 的物理 runtime state 或 `analysis/`：具备 terminal/file/code execution
能力的 agent 仍可能读取共享路径，因此只允许可信内部、公司级产物可共享的 canary 范围。

新会话是否走 OpenShell 只取决于其 canonical 公司是否有健康 binding，与“新开会话”
本身无关。同一会话若切换公司，必须解析到新的 namespace；不得继续复用上一家公司的
Hermes session。运行中的 session 不允许直接换公司；应先停止或完成旧 lease，再以新的
session identity 获取目标公司的 binding。
