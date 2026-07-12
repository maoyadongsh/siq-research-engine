# SIQ 智能投研平台生产可信性优化方案与可执行任务书

> 日期：2026-07-12
>
> 文档编号：SIQ-OPT-EXEC-2026-07-12
>
> 版本：1.0
>
> 状态：执行就绪，启动前需确认目标模式授权范围
>
> 适用范围：`apps/web`、`apps/api`、Hermes Agent runtime、市场报告服务、解析服务、数据库与发布门禁
>
> 参考基线：`docs/architecture/2026-07-11-siq-intelligent-research-platform-optimization-plan.md` 及 2026-07-12 多维度只读复审结果
> 执行权威：本文第 19-24 节；架构决策与约束以第 1-18 节为准

## 1. 方案定位

本方案不是对既有系统的重写计划，也不是对 2026-07-11 方案的增补。它是一份独立的、面向下一阶段实施的工程方案，重点解决以下问题：

1. 已有投研事实链路较强，但生产边界、任务终态、并发一致性和恢复能力仍存在可确认缺口。
2. 当前项目功能面较广，不适合用大规模框架替换或全链路异步化解决局部问题。
3. Agent runtime、市场入库和工作台仍需要继续收敛职责，但不能为了减少文件行数制造更多空壳模块。
4. 后续优化必须同时守住金融事实精度、检索速度、可审计性和现有产品体验。

本方案的核心目标是：

> 在不大改前端、不重写核心框架、不降低检索精度与速度的前提下，将 SIQ 从“功能和样板链路较完整”推进到“生产行为可证明、失败状态可信、任务可恢复、发布可重复验收”的智能投研平台。

## 2. 总体约束

### 2.1 必须保持的能力

- 保持 React 19 + Vite 的前端技术栈和现有工作台信息架构。
- 保持 FastAPI API 边界、现有 URL 兼容性和主要响应结构。
- 保持 Wiki-first、PostgreSQL fallback 的金融事实路线。
- 保持 `ResearchIdentity` 对市场、公司、报告和解析批次的完整约束。
- 保持 A 股 importer 的外部事实契约、幂等语义和现有样板兼容性。
- 保持多市场原始 taxonomy、准则、币种、期间和证据坐标的真实差异。
- 保持 Agent answer audit、calculator trace、citation guard 和离线 benchmark。
- 保持现有可用的 parser、市场入库、Wiki、PostgreSQL、Milvus 和工作流功能。

### 2.2 禁止采用的实施方式

- 不重写前端框架，不进行全站 UI 重做。
- 不一次性重写 Agent runtime。
- 不一次性将所有同步依赖改成异步客户端。
- 不引入 Kafka、Celery、Temporal 等重量级基础设施，除非后续容量数据证明现有 PostgreSQL/Redis 无法满足要求。
- 不以文件行数作为拆分目标，不创建只有一层转发且没有稳定职责的 facade。
- 不为了统一而抹平不同市场 schema。
- 不让向量召回结果直接成为金融数字的事实来源。
- 不在没有证据的情况下执行 Git 历史改写。
- 不在功能 PR 中混入全仓格式化、大型回测 JSON 或无关重构。

### 2.3 质量原则

每个实施项必须同时具备：

1. 可复现的问题或明确的容量目标。
2. 最小可行改动范围。
3. 自动化回归测试。
4. 可观察的成功/失败状态。
5. 明确的回滚方式。
6. 不降低金融事实和检索质量的对照证据。

## 3. 复审基线与证据等级

### 3.1 证据等级

本方案只将满足以下条件的问题纳入开发计划：

| 等级 | 定义 | 处理方式 |
| --- | --- | --- |
| E1：行为复现 | 已通过定向测试、运行探针或容器检查复现 | 可作为发布阻断项 |
| E2：确定代码路径 | 代码路径、触发条件和影响均明确，但尚未在线上流量复现 | 纳入近期修复，并补行为测试 |
| E3：条件风险 | 仅在特定部署、数据量或并发规模下成立 | 先补度量和阈值，不直接大改 |
| E4：建议项 | 可维护性或体验优化，没有当前故障证据 | 放入常规演进，不阻断发布 |

### 3.2 已确认基线

以下结论来自 2026-07-12 只读复审和定向验证：

| 领域 | 结论 | 证据 | 等级 |
| --- | --- | --- | --- |
| Git 与本地凭据 | `env/backend.env` 被 `.gitignore` 排除、未被 Git 跟踪，按文件路径未发现历史提交记录 | `git check-ignore`、`git ls-files`、`git log --all -- env/backend.env` | E1 |
| Docker 构建上下文 | 根 `.dockerignore` 未排除 `env/`，API Dockerfile 使用 `COPY --chown=siq:siq . .`；本地构建镜像中确认存在 `/app/env/backend.env` | `.dockerignore`、`apps/api/Dockerfile`、本地镜像检查 | E1 |
| 数据库 URL 日志 | `init_auth_system.py` 使用 `replace(':@', ':***@')`，对标准 `user:password@host` URL 不生效 | 定向字符串探针保留了完整示例密码 | E1 |
| 文件任务恢复 | `FileBackedJobService` 重载持久化的 `running` 任务后仍返回 `running`，没有中断恢复或租约 | 临时 `jobs.json` 行为探针 | E1 |
| 生产上传入口 | Web Nginx 未配置 `client_max_body_size`，前端市场解析允许单文件 100 MB | Nginx 配置和上传校验代码 | E2 |
| US SEC 上传 | async 路由同步读取完整上传文件并一次性写盘，缺少服务端体积上限 | `market_reports.py::_persist_us_sec_upload` | E2 |
| Agent 终态 | Hermes `failed`、`cancelled` 或未收到终态的 EOF 存在被上层当成普通回答继续保存的路径 | Hermes client 与 runtime 调用链 | E2 |
| 并发运行 | active-run 检查与注册不是一个原子动作，且状态主要为进程内结构 | Agent runtime active run 调用链 | E2 |
| 配额 | 配额检查和使用记录分离，并发请求可能同时通过检查 | usage service 与 chat 路由调用顺序 | E2 |
| 后台任务认领 | IC 任务存在读取待执行状态后再更新的非原子窗口 | IC runtime 调用链 | E2 |
| 前端任务切换 | 通用文档任务没有 request generation 或 AbortController；旧任务响应可写入当前界面状态 | `useDocumentTasks.ts` | E2 |
| 前端搜索状态 | 搜索页只在首次挂载读取 URL，多次基于旧 `searchParams` 写入可能丢失前一字段 | URL patch 定向诊断 | E1 |
| 会话恢复 | API 客户端没有全局 401 失效通知，AuthProvider 只在启动时校验 | API client 与 AuthProvider | E2 |
| SEC HTML 展示 | 同源 blob URL 被无 sandbox iframe 加载，恶意 HTML 可能访问同源上下文 | SEC workbench 与 authenticated file loader | E2 |
| 报告审核文件 | 审核接口接受调用方提供的报告路径并读取，缺少业务根目录约束 | auth router | E2 |
| 可观察性 | metrics 使用实际请求 path 作为 label，动态 ID 会形成高基数；`/metrics` 缺少独立保护 | API middleware 与 metrics route | E2 |
| 数据备份 | 当前备份脚本围绕单个 `DATABASE_URL`，而初始化脚本创建多个业务数据库 | backup script 与 PostgreSQL init SQL | E2 |
| Parser 运行方式 | PDF/document parser Dockerfile 直接运行 Flask 开发服务器入口 | 两个 parser Dockerfile | E2 |

说明：

- 本方案不将 `env/backend.env` 描述为“已经通过 Git 泄露”。当前证据只证明其进入过本地 API 镜像层。
- 私有 Git 仓库降低了代码仓库的外部暴露面，但不改变 Docker 镜像、日志、备份、制品上传和宿主机权限的风险边界。
- E2 项在修复前必须先补最小失败测试，避免依据静态推断直接进行大范围重构。

### 3.3 当前测试快照

复审期间得到的测试快照如下，实施前应在最新工作树重新建立基线：

- Web unit：265/265 通过。
- API 定向测试：126/126 通过。
- 容器安全与生产启动定向测试：17/17 通过。
- API 较大范围测试曾得到 1522 通过、1 失败、3 deselected；失败为响应新增 `research_identity: null` 后旧断言未同步。
- PDF parser：480 通过、9 skipped。
- Document parser：61 通过。
- Market report finder：109 通过。
- Market report rules：79 通过。
- Market contracts：15 通过。
- Web build、frontend check、npm production dependency audit 通过。

这些通过项说明现有基础较稳，但也说明部分生产边界尚未被当前测试覆盖，不能用“测试全绿”替代发布场景验收。

## 4. 目标工程形态

### 4.1 保持现有部署拓扑，收敛职责边界

本阶段不新增大型基础设施，目标边界如下：

```text
Web Workbench
  -> API Router：认证、参数校验、权限、HTTP/SSE 映射
  -> Application Service：用例编排、状态机、事务边界
  -> Domain Contract：ResearchIdentity、FinancialFact、JobState、AuditTrace
  -> Adapter：Wiki / PostgreSQL / Redis / Milvus / Hermes / Filesystem
  -> Durable State：PostgreSQL 为权威状态；Redis 只做短期协调和缓存
  -> Artifact Storage：文件产物可重建、可校验、路径受控
```

### 4.2 Agent 金融事实路线保持不变

```text
用户问题
  -> 完整 ResearchIdentity
  -> Wiki metrics/evidence
  -> validation / financial checks
  -> calculator / reconciliation
  -> Wiki 缺失时按完整身份查询 PostgreSQL Agent view
  -> semantic/vector 只补解释和定位候选
  -> claim verifier + citation guard
  -> answer_audit_trace
  -> 回答
```

任何性能优化不得改变以下事实：

- 数值必须来自结构化事实或可定位披露证据。
- 缓存 key 必须包含完整身份和数据版本，不能只用 ticker 或公司展示名。
- 向量结果不得绕过 validation、calculator 和 citation guard。
- PostgreSQL fallback 缺少完整身份时必须 fail closed。

### 4.3 状态机统一原则

所有长任务统一使用以下终态语义，但不要求立即统一所有底层表：

```text
queued -> running -> succeeded
                  -> failed
                  -> cancelled
                  -> interrupted
                  -> timed_out
```

约束：

- `failed/cancelled/interrupted/timed_out` 不能产生成功回答或成功 artifact。
- 每个任务只能由一个 owner/lease 持有。
- 终态写入与结果引用写入必须位于同一事务或具备可证明的幂等补偿。
- 进程重启后，旧的 `queued/running` 必须被恢复、重新认领或明确标记为 `interrupted`，不能永久悬挂。

## 5. 实施路线总览

| 阶段 | 时间建议 | 目标 | 是否阻断生产发布 |
| --- | --- | --- | --- |
| R0 | 第 1 周 | 生产暴露面和边界止血 | 是 |
| R1 | 第 1-2 周 | Agent/任务终态、并发和恢复可信 | 是 |
| R2 | 第 2-3 周 | 前端异步状态和入口一致性 | 关键项阻断 |
| R3 | 第 3-5 周 | 可观察性、备份和性能基线 | 关键项阻断 |
| R4 | 第 4-8 周 | 有边界的大文件 owner 化 | 否，持续演进 |
| R5 | 持续 | 金融精度、检索质量和发布门禁 | 是，长期门禁 |

阶段可以局部并行，但 R0/R1 的状态和安全修复不得与核心 Agent prompt、检索策略或前端大改放在同一个 PR。

## 6. R0：生产暴露面和边界止血

### R0-1. 隔离 Docker 构建上下文

问题：Git 已正确忽略 `env/backend.env`，但 Docker 构建上下文没有排除 `env/`，API 全仓复制会把本地 env 文件写入镜像层。

最小改动：

1. 在根 `.dockerignore` 增加 `env/`、`*.env` 的精确策略，并为允许进入镜像的示例文件添加反向规则。
2. 保持运行时通过 compose/Kubernetes secret/env 注入配置，不复制真实 env。
3. 增加镜像内容测试，断言 `/app/env/backend.env`、`.env`、本地认证备份不存在。
4. 重建本地镜像；是否轮换凭据根据镜像是否离开可信宿主机判断，不将“存在于本地镜像”等同于“已外泄”。

验收：

- `git check-ignore -v env/backend.env` 继续通过。
- 构建 API 镜像后，敏感路径均不存在。
- compose 使用显式 env 注入后 API 能正常启动。

回滚：仅回滚 `.dockerignore` 会重新引入风险，因此回滚必须改为调整显式 allowlist，不能恢复全量复制敏感目录。

### R0-2. 统一敏感 URL 脱敏

问题：认证初始化脚本的字符串替换无法处理正常数据库 URL。

最小改动：

1. 新增一个共享 `redact_connection_url()`，使用 URL parser 处理 userinfo、query token 和多种 driver 前缀。
2. 初始化、迁移、release gate、健康检查和异常日志统一使用该函数。
3. 日志只显示 driver、host、port、database；用户名可按需要部分遮罩，密码永不输出。
4. 对 URL 编码密码、无用户名、Unix socket、多个 query secret 增加测试。

验收：日志测试不得出现测试密码、token 或完整 userinfo。

### R0-3. 隔离不可信 HTML

问题：SEC HTML 通过同源 blob URL 加载到未 sandbox 的 iframe。

最小改动：

1. iframe 增加 sandbox，默认不授予 `allow-same-origin` 和脚本权限。
2. 服务端或前端生成受控阅读 HTML：移除 script、event handler、object/embed、危险 URL scheme。
3. 对原文链接使用显式新窗口动作，并增加 `noopener noreferrer`。
4. 如需保留复杂 SEC 样式，使用独立无凭据 origin，而不是放宽同源 sandbox。

验收：恶意 fixture 不能读取 localStorage、cookie、父窗口 DOM，也不能触发任意网络脚本；正常表格、文本、锚点和图片仍可阅读。

### R0-4. 约束服务端文件读取

问题：报告审核 API 接受调用方路径并读取文件。

最小改动：

1. API 只接收 artifact/report ID；兼容期如保留 path，必须先解析到业务允许根目录。
2. 使用 `Path.resolve()` 后验证 `is_relative_to(allowed_root)`。
3. 拒绝绝对越界路径、`..`、符号链接逃逸、设备文件和超限文件。
4. 审核记录保存 canonical artifact identity，不保存调用方原始任意路径作为权威引用。

验收：补充目录穿越、symlink escape、其他用户 artifact 和正常报告四类测试。

### R0-5. 内部服务认证 fail closed

问题：market-report-finder 和 market-report-rules 在 token 为空时允许请求通过；compose 还允许空默认值。

最小改动：

1. production profile 下 token 缺失直接启动失败。
2. 开发 profile 仅允许绑定 loopback 时显式关闭 token。
3. compose 不发布不需要宿主访问的内部端口。
4. 使用恒定时间比较，并统一 401/503 行为。

验收：production 缺 token 启动失败；错误 token 返回 401；内部调用正确 token 正常。

### R0-6. 对齐上传边界

问题：前端允许市场 PDF 到 100 MB，而 Nginx 未显式配置上传上限；US SEC 上传还会同步读完整文件。

最小改动：

1. Nginx 按产品上限配置 `client_max_body_size`，建议略高于 API 允许的 multipart 总体积。
2. API 对文件数量、单文件大小和请求总大小做独立校验，不能只依赖前端或 Nginx。
3. US SEC 上传采用分块 hash + 分块写入临时文件，再原子 rename。
4. 文件写入和 hash 放入线程池或同步 worker 边界，避免阻塞事件循环。
5. 超限统一返回 413，并在前端显示可操作错误。

验收：覆盖 0 字节、边界值、超限、多文件总量、断开上传和并发上传；Nginx 与直连 API 行为一致。

## 7. R1：运行时终态、并发和恢复可信

### R1-1. Hermes 终态严格映射

目标：上游失败不能成为成功回答。

实施：

1. Hermes client 返回结构化终态，而不是只依赖累计文本。
2. 明确映射 `completed/failed/cancelled/timed_out/protocol_eof`。
3. SSE 在没有 terminal event 时结束，标记为 `protocol_eof`，不得保存 assistant success message。
4. 失败可以保存脱敏的运行诊断，但与用户回答消息分离。
5. 流式和非流式复用同一终态判定函数。

验收：

- `failed` 携带部分文本时仍为失败。
- `cancelled` 不进入记忆和 completed-run dedupe。
- EOF 无 done event 不产生成功消息。
- 重试不会产生两条成功回答。

### R1-2. active run 原子认领

目标：同一用户会话同一时刻只有一个被认可的 active run。

分两步实施：

1. 单进程止血：将“检查 + 注册”放入同一临界区，并让 stream/non-stream 共享入口。
2. 多 worker 生产方案：使用 Redis `SET NX EX` 或 PostgreSQL 唯一约束保存 `session_id + run_id + lease_until`，释放时校验 run owner。

约束：

- 不在锁内执行 LLM 或网络 I/O。
- lease 必须有 TTL 和 heartbeat。
- stop 只能终止当前 owner，不能误停新 run。
- run 终态和审计 trace 必须记录同一 run ID。

验收：并发请求、worker 切换、旧请求迟到释放、stop 与新 run 竞争均有测试。

### R1-3. 配额原子化

目标：并发请求不能共同绕过剩余额度。

实施：

1. 将配额判断与 reservation 写入一个数据库事务。
2. 使用条件 UPDATE、行锁或唯一 usage reservation，避免 Python 层先查后写。
3. 请求失败时按策略释放 reservation；已经消耗外部模型资源时记录真实消耗。
4. 前端区分 `limited/unlimited/unavailable/loading`，接口失败不得显示“不限”。

验收：N 个并发请求在余额为 1 时最多一个成功认领；超限和服务错误有不同错误码。

### R1-4. IC 与后台任务租约

目标：任务不会重复执行，也不会在进程退出后永久处于 running。

近期最小方案：

1. `FileBackedJobService` 启动时将无法恢复 target 的 `queued/running` 标记为 `interrupted`。
2. 持久化失败必须记录 metric 和结构化日志，不能静默吞掉。
3. job response 增加 `attempt/owner/heartbeat_at/interrupted_reason`。
4. IC 任务使用数据库原子 claim，推荐 `UPDATE ... WHERE status='queued' RETURNING ...` 或 `FOR UPDATE SKIP LOCKED`。

生产多 worker 方案：

- 使用现有 PostgreSQL 增加轻量 durable job 表和 lease，不引入新的队列产品。
- 文件仍保存大 artifact，数据库只保存任务状态、owner、输入摘要和 artifact 引用。
- runner 通过 heartbeat 延长 lease；超时后允许安全重试。

验收：进程在 queued、running、完成写入前分别退出，重启后状态都可解释且可恢复。

### R1-5. 定点处理异步阻塞

本阶段不做 Agent runtime 全链路异步化，只处理已确认或已测量的阻塞调用：

- 同步 Redis 调用。
- 同步 Milvus 查询/写入。
- 大文件读取、hash、压缩和 JSON 序列化。
- 长时间子进程等待。

实施规则：

1. 优先换成熟异步客户端；改动面过大时使用有界线程池。
2. 每个外部调用有 timeout、取消和并发上限。
3. 不改变 SSE 事件顺序、DB session 生命周期和 audit trace。
4. 先记录 event-loop lag、调用耗时和并发量，再决定是否继续迁移。

验收：金融 QA benchmark 不下降；P95 首 token 和检索延迟不回退超过约定预算。

## 8. R2：前端状态一致性与产品恢复能力

### 8.1 实施原则

- 不改页面整体布局和导航。
- 不引入新的全局状态框架作为前置条件。
- 优先复用 PDF 工作台已有的 request scope 模式。
- 每个异步页面至少具备 loading、success、empty、error、stale/cancelled 状态。
- URL、选中任务和服务端 identity 各自只能有一个权威来源。

### R2-1. 通用文档任务 request scope

实施：

1. 将 PDF 工作台的 generation + AbortController 模式抽成小型通用 helper。
2. status、artifacts、poll 分别持有 request generation。
3. A 切换到 B 后，A 的迟到响应只能被丢弃，不能写入 B 的 state。
4. `stopPolling()` 必须绑定 poll owner，旧轮询不能停止新任务轮询。

验收：使用可控 deferred promise 测试 A/B 乱序响应、快速三次切换、终态到达和组件卸载。

### R2-2. 搜索 URL 原子同步

实施：

1. 明确 URL 为可分享搜索条件的单一事实源。
2. 智能解析产生 market/year/query 后一次性 patch URL。
3. 监听浏览器前进/后退并回灌表单状态。
4. 搜索结果请求使用 AbortController 或 request ID，旧搜索结果不能覆盖新查询。

验收：刷新、前进、后退、连续智能解析和慢响应乱序均保持 URL 与界面一致。

### R2-3. 全局会话失效恢复

实施：

1. API client 对同源业务 API 的 401 发送去重的 `session-invalidated` 事件。
2. AuthProvider 清理认证和用户态缓存，并保存当前目标 URL。
3. 登录接口自身的 401、显式 logout 和外部请求不触发循环重定向。
4. 优先生产 cookie mode；Bearer 兼容模式不扩大 localStorage 使用范围。

验收：运行中 token/cookie 过期后只触发一次退出，重新登录能回到原页面。

### R2-4. 工作台数据加载收敛

实施：

- `PrimaryMarketWorkbench`：列表接口增加分页和状态摘要，消除每项目一个 status 请求的无界 N+1。
- 一级市场子页面：项目列表加载与默认选择拆成不同 effect，切换项目不重复拉完整列表。
- `MyWorkspace`：错误不能伪装为空状态；focus、visibility 和 timer 请求去重并取消旧响应。
- 通知：隐藏标签暂停轮询，使用增量游标；已读 key 按 user ID 和 schema version 分区并限制长度。

说明：N+1 的代码路径确定存在，但是否造成当前性能故障取决于项目规模，因此先通过分页、批量摘要和请求计数解决，不引入新的客户端数据框架。

### R2-5. 低风险可用性修复

- 登录字段使用 `autocomplete="username"` 和 `autocomplete="current-password"`，移除阻止密码管理器的技巧。
- 文档配额接口失败显示“暂不可用”，不显示“不限”。
- 移除嵌套 `<main>` landmark。
- 删除、覆盖、强制入库等不可逆操作统一确认和结果反馈。
- 对异步完成、失败和配额变化增加必要的 `aria-live`，不重做视觉系统。

## 9. R3：生产运维、可观察性与性能基线

### R3-1. Metrics 低基数化与保护

实施：

1. 使用路由模板而非原始 URL path 作为 label，例如 `/api/jobs/{job_id}`。
2. 404/未匹配路径统一聚合，禁止用户输入进入 label。
3. `/metrics` 仅对内部网络或带独立 token 的采集器开放。
4. 增加 cardinality 自监控和 label allowlist 测试。

### R3-2. 多数据库备份和恢复演练

实施：

1. 备份清单从 PostgreSQL 初始化配置生成或由显式 allowlist 管理。
2. 每个业务数据库独立 dump、校验、加密和保留周期。
3. 记录 schema/version、时间、大小和 checksum manifest。
4. 每月至少自动恢复到临时实例并运行最小查询和 migration compatibility 检查。

验收标准不是“备份命令退出 0”，而是“恢复后的关键表、Agent view 和样本查询可用”。

### R3-3. Parser 生产服务器

实施：

1. PDF/document parser 容器改用受支持的生产 WSGI server。
2. 配置 worker、timeout、最大请求和优雅退出。
3. parser 内部任务如已是 CPU/GPU 重任务，HTTP worker 只负责提交和查询，不在增加 worker 时重复加载超大模型。
4. 使用 readiness 区分“进程存活”和“模型/依赖可服务”。

### R3-4. 性能预算

在优化前建立下列基线：

| 指标 | 目标方向 | 保护线 |
| --- | --- | --- |
| Agent 首 token P95 | 不变或下降 | 不回退超过 10% |
| Wiki 核心事实检索 P95 | 不变或下降 | 不回退超过 5% |
| PostgreSQL fallback P95 | 不变或下降 | 不回退超过 10% |
| Milvus retrieval recall@k | 不下降 | benchmark 不得下降 |
| 金融 claim/evidence coverage | 不下降 | release gate 必须保持通过 |
| Web 关键工作台请求数 | 下降 | 项目列表不再为 1+N 无界增长 |
| Event-loop lag P95 | 下降 | 超阈值产生告警 |

没有 baseline 的性能改动只能作为实验，不直接进入生产默认路径。

## 10. R4：大文件按 owner 边界渐进拆解

### 10.1 拆分准入条件

一个模块只有满足以下任一条件才进入拆分：

- 同一文件同时拥有两个以上独立生命周期或数据源。
- 某一职责已有稳定输入/输出契约和独立测试。
- 多个调用方重复相同业务规则，需要单一 owner。
- 当前文件导致高频冲突或修改必须加载大量无关依赖。
- 安全、事务或状态机边界无法在现有结构中清晰表达。

以下情况不拆：

- 只是文件行数较多。
- 新模块只会转发一次调用。
- 拆分后需要双向 import、全局 monkeypatch 或大量兼容别名。
- 业务规则仍在快速变化，接口尚不稳定。
- 迁移同时改变行为、数据模型和外部 API。

### 10.2 拆分完成标准

每次拆分必须满足：

1. 新模块拥有明确职责和稳定类型。
2. 原 owner 的依赖数量或决策复杂度真实下降。
3. 没有新增循环依赖。
4. 新模块可独立单测。
5. 外部行为和 benchmark 保持一致。
6. 兼容 wrapper 有删除条件和计划，不无限保留。

### R4-1. Agent runtime

保留 `agent_chat_runtime_impl.py` 作为运行用例编排者，职责限定为：

- 组装 request/run context。
- 调用 preflight、source routing、Hermes stream、guard、audit 和 persistence。
- 执行终态状态机。

稳定 owner 建议：

| Owner | 职责 | 不应包含 |
| --- | --- | --- |
| `agent_runtime_identity.py` | ResearchIdentity 解析、完整性和冲突检查 | Wiki 扫目录、数据库查询 |
| `agent_runtime_query_plan.py` | 公司/期间/指标/意图计划 | 直接执行外部 I/O |
| `agent_runtime_financial_sources.py` | Wiki/PostgreSQL/semantic 来源调度 | importer、package build |
| `agent_runtime_guardrails.py` | claim/evidence/calculator/identity 阻断 | UI formatter |
| `agent_runtime_streaming.py` | SSE event、active run、terminal projection | 金融事实规则 |
| `agent_runtime_answer_audit.py` | trace schema、落盘和读取投影 | 回答生成 |

实施顺序：先终态和 active-run，再迁纯逻辑；不得在同一 PR 同时调整 prompt、检索优先级和模块结构。

### R4-2. Market reports

`market_reports.py` 只保留：

- FastAPI 参数与依赖。
- 权限和业务 identity 输入。
- service 调用。
- HTTP error、SSE/FileResponse 映射。

业务 owner：

- package/status：`market_report_package_service.py`、`market_report_status_service.py`。
- PostgreSQL import/status：`market_report_postgres_service.py`。
- eval/release gate：`market_report_eval_service.py`。
- queue/job projection：`market_report_queueing.py`。
- upload：新增或复用独立 upload service，负责限制、临时文件、hash 和 metadata。

不再继续创建只为移动十几行代码的 service；优先完成上传和路径安全这种真实边界。

### R4-3. 前端

前端不按页面大小机械拆分。只抽取以下稳定能力：

- task request scope。
- URL/search state reducer。
- session invalidation channel。
- paginated workspace/project query hook。
- 统一的 async state 与错误展示 primitive。

页面仍负责页面级编排和布局，避免把简单状态拆成大量 context/provider。

## 11. R5：金融精度与检索性能保护

### R5-1. 双门禁

所有影响 Agent、Wiki、PostgreSQL、Milvus、identity 或 calculator 的 PR 必须同时通过：

1. 正确性门禁：claim、value、period、currency、identity、evidence、calculator trace。
2. 性能门禁：关键 fixture 的检索 latency、候选数量、recall/coverage 和首 token。

### R5-2. Benchmark 分层

| 层级 | 环境 | 内容 |
| --- | --- | --- |
| PR deterministic | 无外部 DB/LLM | contract fixture、trace-offline、攻击样本、identity |
| PR integration | 临时服务 | HTTP/auth、SSE 终态、并发、路径和任务恢复 |
| Nightly real data | self-hosted | 六市场 Wiki/PostgreSQL、Milvus、真实样本、性能 |
| Release live model | 受控生产等价环境 | Hermes live 攻击集、引用、拒答、延迟和成本 |

### R5-3. 缓存与索引规则

- 缓存 key 至少包含 market、company_id、filing_id、parse_run_id、query contract version。
- 缓存不得绕过权限、validation 或 evidence guard。
- 仅在慢查询证据存在时增加索引，并用 `EXPLAIN ANALYZE` 验证。
- Milvus 参数只通过固定 benchmark 调整，不凭单次主观回答效果修改。
- embedding/model 升级必须保留旧 collection 或具备可回滚重建策略。

## 12. 建议 PR 序列

为降低风险，建议按以下小批次落盘：

| PR | 内容 | 主要验收 |
| --- | --- | --- |
| 1 | `.dockerignore`、镜像 secret absence test、URL 脱敏 | 容器安全测试 |
| 2 | SEC iframe sandbox、HTML 安全 fixture | Web unit + 浏览器安全 smoke |
| 3 | report path allowlist、内部服务 token fail closed | API 权限/路径负向测试 |
| 4 | Nginx/API 上传上限、US SEC 流式写入 | 边界上传矩阵 |
| 5 | Hermes terminal state 单源化 | stream/non-stream terminal tests |
| 6 | active-run 原子 claim、quota reservation | 并发测试 |
| 7 | job interrupted recovery、IC lease | restart/duplicate execution tests |
| 8 | document request scope、search URL 原子同步 | Web deferred-response tests |
| 9 | 401 恢复、workspace error/dedupe | Web auth/runtime tests |
| 10 | metrics route normalization、metrics protection | cardinality/auth tests |
| 11 | 多数据库 backup/restore drill | restore artifact |
| 12+ | 每次一个稳定 owner 的渐进拆分 | 行为不变 + 模块单测 |

每个 PR 避免同时修改 Agent prompt、金融事实契约、数据库 schema 和前端交互。

## 13. 验收矩阵

### 13.1 PR quick gate

```bash
cd apps/api
uv run --frozen pytest -q

cd ../web
npm run test:unit
npm run check:frontend

cd ../..
python3 scripts/maintenance/check_local_security_hygiene.py --scope workflow
python3 scripts/maintenance/check_api_ci_test_coverage.py --fail-on-uncovered
python3 scripts/maintenance/check_python_quality_touched.py --base-ref HEAD --require-ruff --json
python3 scripts/maintenance/run_market_document_full_postgres_gate.py --mode contract
python3 scripts/maintenance/run_market_ingestion_eval.py --strict
git diff --check
```

### 13.2 新增定向门禁

需要新增或扩展以下测试域：

- Docker image secret absence。
- connection URL redaction。
- untrusted HTML iframe isolation。
- arbitrary path 与 symlink escape。
- upload boundary through Nginx 与 direct API。
- Hermes failed/cancelled/protocol EOF。
- active run 和 quota 并发。
- job restart/interrupted/lease expiry。
- document task switch stale response。
- browser history/search URL roundtrip。
- runtime 401 invalidation。
- metrics route template cardinality。
- backup restore smoke。

### 13.3 Nightly/release gate

```bash
bash scripts/ops/run_market_postgres_release_gate.sh \
  --mode offline-postgres \
  --output-dir artifacts/eval-runs/release

cd apps/api
uv run python ../../scripts/maintenance/run_live_market_qa_smoke.py \
  --output ../../artifacts/eval-runs/financial-qa/live-market-qa-smoke.json \
  --json
```

release gate 还必须运行：

- 生产镜像启动与非 root 检查。
- Web -> API -> controlled Hermes gateway 的真实 HTTP/SSE 测试。
- 双用户对象级权限负向测试。
- 多数据库恢复演练最近成功记录检查。
- live model 金融攻击集和性能预算检查。

## 14. Definition of Done

一个任务只有同时满足以下条件才算完成：

- 失败用例在修复前可稳定复现，修复后通过。
- 正常链路和负向链路都有自动化测试。
- HTTP/SSE 错误码和用户可见状态一致。
- 日志和 metrics 不包含凭据、任意路径或高基数 ID。
- 对 Agent/检索有影响时，金融 QA 与检索性能门禁不下降。
- 数据模型变更有 migration、兼容读取和回滚说明。
- 长任务变更覆盖重启、取消、超时和重复请求。
- 文档记录 owner、边界、配置和排障入口。
- PR 不包含无关格式化或大型生成 artifact。

## 15. 风险与控制

| 风险 | 控制措施 |
| --- | --- |
| 安全修复破坏本地开发 | production fail closed；development 需要显式且仅 loopback 的 opt-out |
| 终态修复导致历史客户端不兼容 | 保留旧字段，新增稳定 error/status code，分阶段切换 |
| 原子 claim 引入锁竞争 | 短事务、条件更新、TTL/lease，不在锁内执行外部 I/O |
| 任务持久化变重 | PostgreSQL 只保存状态和引用，大 artifact 仍走文件存储 |
| 前端取消请求导致状态不更新 | request generation 与 AbortController 并用，终态允许主动刷新 |
| 模块拆分增加间接层 | 拆分准入和完成标准；禁止 facade-only 扩散 |
| 性能优化降低召回 | correctness + performance 双门禁，保留旧配置回滚 |
| 多市场统一破坏市场特性 | 统一上层事实契约，不统一底层 taxonomy/schema |
| 工作树已有大量改动导致基线漂移 | 每个 PR 先记录 base commit 和测试快照，禁止顺手清理无关文件 |

## 16. 明确不做的事

| 不做 | 原因与边界 |
| --- | --- |
| 重写 React/Vite 前端 | 当前框架足以支持工作台，优先修异步状态和数据边界 |
| 重写 FastAPI 或改成微服务群 | 当前主要问题不是框架能力，不增加部署和调用复杂度 |
| 为统一而重写 A 股 importer | 冻结外部契约；内部实现仅在完整回归保护下演进 |
| 让 Agent 用向量结果直接回答数字 | 向量仅定位和解释，数字必须经过事实与证据门禁 |
| 统一所有市场底层 schema | 保留真实市场差异，统一 ResearchIdentity 和事实契约 |
| 一次性全异步化 Agent runtime | 只对已确认阻塞点进行有界迁移 |
| 以行数 KPI 拆文件 | 只按 owner、事务、状态机和稳定契约拆分 |
| 引入重量级任务平台 | 先使用 PostgreSQL lease + Redis 短期协调，容量不足再评估 |
| 全仓 strict typing/formatting | 使用 touched-files 与模块白名单逐步提高标准 |
| 普通 PR 更新大型 backtest JSON | 生成物进入 artifact；Git 只保留小 fixture 和摘要 |
| 无证据执行 Git 历史改写 | 当前未发现 `env/backend.env` 的 Git 历史；如未来发现再单独评估 |

## 17. 首个两周执行清单

### 第 1-2 天：建立可失败基线

- 固定 base commit、部署 profile 和测试快照。
- 为 Docker secret、URL 脱敏、Hermes 终态、job restart、上传边界补失败测试。
- 记录 Agent 首 token、Wiki/PostgreSQL/Milvus latency 基线。

### 第 3-5 天：完成 R0

- Docker context 隔离。
- URL 脱敏。
- SEC HTML sandbox。
- report path allowlist。
- internal service token fail closed。
- Nginx/API 上传边界对齐。

### 第 6-8 天：完成核心 R1

- Hermes terminal state 单源化。
- active run 原子认领。
- quota reservation 原子化。
- job restart 标记 interrupted，IC 增加原子 claim。

### 第 9-10 天：完成关键 R2 与发布复验

- 通用文档 request scope。
- 搜索 URL 原子同步。
- 全局 401 恢复。
- 运行 quick、integration、financial QA、容器和 release smoke。
- 形成一份脱敏发布证据摘要，不刷新大型 tracked JSON。

## 18. 预期结果

完成 R0-R2 后，SIQ 应达到以下可观察状态：

- 本地 env 不再进入新构建镜像，且没有夸大为 Git 泄露。
- 上游 Agent 失败、取消或协议中断不会伪装成成功回答。
- 同一会话、配额和 IC 任务在并发条件下具备原子 owner。
- 后台任务在进程重启后不会永久显示 running。
- 上传上限在浏览器、Nginx 和 API 三层一致。
- 前端快速切换任务、搜索条件或会话失效时不会显示错任务数据。
- 金融事实路线、ResearchIdentity、citation guard 和 retrieval benchmark 保持或优于当前水平。
- 大文件继续缩小，但每次拆分都对应真实 owner，项目不会因过度抽象变重。

完成 R3-R5 后，SIQ 的发布结论不再依赖“功能看起来能运行”，而是由生产容器、真实 HTTP/SSE、恢复演练、双用户权限、金融事实门禁和性能预算共同证明。

## 19. 可执行任务书

本节是后续 Codex 目标模式的权威执行清单。第 1-18 节负责解释架构目标和决策依据，本节负责定义工作顺序、交付物和完成条件。

### 19.1 总目标

在保持现有产品框架、金融事实契约和多市场检索能力的前提下，完成以下结果：

1. 关闭已确认的生产安全和暴露面缺口。
2. 确保 Agent、配额、IC 和后台任务具有可信终态、原子 owner 和重启恢复能力。
3. 修复关键前端工作流的跨任务状态污染、URL 漂移和会话失效恢复。
4. 建立生产入口、监控、备份、恢复和 parser 运行基线。
5. 只对具备稳定 owner 的核心文件进行渐进拆分。
6. 全程保护 Wiki-first、PostgreSQL fallback、ResearchIdentity、claim verifier、citation guard、Milvus recall 和响应性能。

### 19.2 任务状态

每个任务只允许使用以下状态：

| 状态 | 含义 |
| --- | --- |
| `pending` | 尚未开始，前置条件可能未满足 |
| `in_progress` | 正在实施，最多允许一个主任务处于此状态 |
| `validation` | 实现完成，正在运行规定的验证矩阵 |
| `completed` | 所有验收和交付物完整，且无遗留必做项 |
| `blocked` | 已明确记录阻断原因、尝试过的替代方案和所需外部输入 |

禁止将“代码已写”“单测通过”或“剩余问题以后再说”视为 `completed`。

### 19.3 主任务账本

| ID | 优先级 | 任务 | 依赖 | 主要交付物 | 初始状态 |
| --- | --- | --- | --- | --- | --- |
| T00 | P0 | 建立基线、失败证据和变更边界 | 无 | baseline artifact、失败测试、路径清单 | pending |
| T01 | P0 | Docker context 与敏感 URL 止血 | T00 | ignore 规则、脱敏 helper、镜像测试 | pending |
| T02 | P0 | 不可信 HTML 与任意文件路径隔离 | T00 | sandbox、sanitizer、path policy | pending |
| T03 | P0 | 内部服务认证和上传入口一致化 | T00 | fail-closed auth、上传边界、流式落盘 | pending |
| T04 | P0 | Hermes 终态单源化 | T00 | terminal contract、stream/non-stream 回归 | pending |
| T05 | P0 | active run 与配额原子化 | T04 | run lease/claim、quota reservation | pending |
| T06 | P0 | 后台任务恢复与 IC 原子认领 | T00 | interrupted recovery、lease、可观察持久化 | pending |
| T07 | P1 | 前端 task/search request scope | T00 | request scope、URL reducer、乱序测试 | pending |
| T08 | P1 | 会话失效与工作台数据恢复 | T07 | 401 channel、错误态、分页/去重 | pending |
| T09 | P1 | Metrics、备份、恢复和 parser 生产化 | T01、T03 | 低基数 metrics、restore drill、WSGI | pending |
| T10 | P0 持续门禁 | 金融精度和检索性能保护 | T00 | 双门禁、baseline、回归报告 | pending |
| T11 | P2 | 核心文件按 owner 渐进拆分 | T04-T10 稳定后 | owner 模块、依赖收敛证据 | pending |
| T12 | P0 | 生产等价发布验收与收口 | T01-T11 中所有发布必需项 | release evidence、遗留风险清单 | pending |

T01-T03、T04-T06、T07-T08 可以在不同工作流中并行，但共享文件发生冲突时必须串行。T10 从 T00 开始持续运行，不是最后补做的测试任务。

## 20. 任务详细定义

### T00. 建立基线、失败证据和变更边界

**目标**

建立可重复的开发起点，避免在大型脏工作树中误改用户内容，也避免修复后无法证明原问题真实存在。

**允许修改**

- 新增定向测试、fixture、ignored benchmark artifact。
- 为测试可注入性做不改变行为的小调整。

**必须执行**

1. 阅读根 `AGENTS.md` 及相关子目录说明。
2. 记录 base commit、`git status --short` 和本任务涉及的已有用户改动。
3. 对 T01-T09 的每个 E1/E2 问题建立失败测试或最小行为探针。
4. 运行第 13 节 quick gate，并将当前失败区分为：既有失败、本任务新增失败、环境缺失。
5. 记录 Agent、Wiki、PostgreSQL、Milvus 和 Web 关键路径性能基线。

**禁止**

- 清理、回退或格式化用户已有改动。
- 为得到全绿而删除断言、跳过测试或降低门禁。
- 在基线阶段修改生产行为。

**完成条件**

- 每个 P0 缺陷都有修复前会失败的测试或可归档探针。
- baseline 记录不含密钥、真实 token 或完整数据库 URL。
- 已知 API 旧断言与 `research_identity: null` 的契约差异得到明确处理，不隐藏失败。

### T01. Docker context 与敏感 URL 止血

**目标**

保证本地真实 env 不进入新镜像，并让所有数据库/服务 URL 日志使用同一安全脱敏逻辑。

**重点文件**

- `.dockerignore`
- `apps/api/Dockerfile`
- `apps/api/scripts/init_auth_system.py`
- 现有 security/redaction helper 或一个新的单一 owner
- `scripts/maintenance/tests/test_container_security_config.py`

**实施步骤**

1. 增加 `env/`、`.env` 和本地认证备份排除规则；只 allowlist 明确需要的 example/template。
2. 新增镜像文件系统断言，不仅检查 Git ignore。
3. 使用结构化 URL parser 实现 `redact_connection_url()`。
4. 搜索项目中打印 `DATABASE_URL`、service URL、DSN 的路径并迁移到该 helper。
5. 对 percent-encoded 密码、IPv6、query secret、无密码 URL 和错误 URL 增加测试。

**验收**

- API 镜像不包含真实 env、本地 token backup 或 Git metadata。
- 标准 PostgreSQL URL 测试中密码不出现在 stdout/stderr。
- API/compose 仍能通过运行时 env 正常启动。
- 不声称 Git 已泄露，也不执行历史改写。

### T02. 不可信 HTML 与任意文件路径隔离

**目标**

关闭同源 HTML 执行和任意服务端路径读取能力，不破坏 SEC 原文阅读和正常报告审核。

**重点文件**

- `apps/web/src/components/sec/UsSecSourceWorkbench.tsx`
- `apps/web/src/lib/authenticatedFiles.ts`
- `apps/api/routers/auth.py`
- `apps/api/services/auth_service.py`
- 对应 Web/API tests

**实施步骤**

1. 为 SEC iframe 建立 sandbox 最小权限配置。
2. 对阅读 HTML 做 sanitizer/CSP；不得通过 `allow-same-origin + allow-scripts` 恢复旧风险。
3. 报告审核改为 artifact identity 或受控根目录 canonical path。
4. 验证 symlink、绝对路径、`..`、其他用户 artifact 和超大文件。
5. 将所谓 report signature 的真实语义改名为 content fingerprint，或使用服务端密钥实现真正签名；不得继续把普通 SHA-256 描述为不可伪造签名。

**验收**

- 恶意 HTML 无法访问认证存储或父页面。
- 正常 SEC 表格、文本和链接可用。
- 越界文件读取全部拒绝，正常 artifact 审核不回退。
- 审计记录包含 canonical artifact ID 和 actor，不包含不受控路径。

### T03. 内部服务认证和上传入口一致化

**目标**

生产内部服务缺少 token 时拒绝启动；浏览器、Nginx 和 API 对上传数量与体积给出一致行为。

**重点文件**

- `services/market-report-finder/.../core/config.py`
- `services/market-report-finder/.../app.py`
- `services/market-report-rules/.../app.py`
- `infra/docker/docker-compose.yml`
- `apps/web/nginx.conf.template`
- `apps/api/routers/market_reports.py`
- 市场上传前端校验和测试

**实施步骤**

1. production profile 下内部 token 缺失直接启动失败。
2. 移除不必要的宿主端口；开发免 token 仅允许显式启用并绑定 loopback。
3. 定义单文件、文件数、请求总量三个上传限制，Nginx 与 API 使用同一配置来源或契约测试。
4. US SEC 上传改为分块 hash、临时文件和原子 rename。
5. 阻塞文件 I/O 使用有界线程执行，不在 async route 内同步读取完整文件。
6. 统一 400、401、413、503 错误语义和前端提示。

**验收**

- production 缺 token 启动失败，错误 token 返回 401。
- 1 MB 以上且在产品限制内的文件能通过生产 Nginx 路径。
- 边界值成功，超限稳定返回 413；直连 API 不可绕过。
- 中断上传不留下可被业务读取的半文件。

### T04. Hermes 终态单源化

**目标**

任何 failed、cancelled、timed out 或无终态 EOF 都不会被保存为成功回答。

**重点文件**

- `apps/api/services/hermes_client.py`
- `apps/api/services/agent_chat_runtime_impl.py`
- `apps/api/services/agent_runtime_streaming.py`
- Agent runtime 和 chat router tests

**实施步骤**

1. 定义版本化 `RunTerminalResult` 或等价类型。
2. Hermes stream/non-stream 统一返回终态、错误码、是否可重试和已接收文本。
3. runtime 只在 `succeeded` 后执行成功消息、memory、dedupe 和 artifact 提交。
4. 失败诊断与用户回答分离，保留 trace ID。
5. SSE done/error event 与数据库终态保持一致。

**验收**

- 覆盖 failed+partial text、cancelled、timeout、EOF、重复 terminal event。
- 失败运行不进入 successful message history、memory 或 completed-run cache。
- 流式与非流式得到相同业务终态。
- 现有金融回答 benchmark 不下降。

### T05. active run 与配额原子化

**目标**

同一 session 不产生两个受认可的活动运行；并发请求不能超用配额。

**重点文件**

- `apps/api/services/agent_runtime_streaming.py`
- `apps/api/services/agent_chat_runtime_impl.py`
- `apps/api/services/usage_service.py`
- `apps/api/routers/chat.py`
- 数据库 model/migration（如需要）

**实施步骤**

1. 将 active-run check/register 合并为原子 claim。
2. 多 worker 使用现有 Redis 或 PostgreSQL lease；禁止依赖纯进程内字典作为生产权威。
3. stop/release 校验 `run_id + owner`，防止旧请求释放新 run。
4. quota check 改为事务内 reservation。
5. 记录 reserved、consumed、released 和 reconciliation 状态。

**验收**

- 同 session 并发 N 次只产生一个 owner。
- 余额为 1 时并发 N 次最多一个 reservation 成功。
- worker/请求取消后 lease 可过期或明确释放。
- 不在数据库锁或 Redis critical section 内执行 Hermes/Milvus I/O。

### T06. 后台任务恢复与 IC 原子认领

**目标**

消除永久 running、静默持久化失败和 IC 重复执行。

**重点文件**

- `apps/api/services/job_service.py`
- `apps/api/services/ic_agent_runtime.py`
- job/IC API 与 tests
- PostgreSQL migration（生产 durable job 需要时）

**实施步骤**

1. 先让 file-backed job 重启后将不可恢复的 queued/running 转为 interrupted。
2. 持久化异常记录 structured log 和 metric；API 能反映 durability degraded。
3. IC 使用条件更新或 `FOR UPDATE SKIP LOCKED` 原子 claim。
4. 为生产多 worker 增加轻量 PostgreSQL job lease；不引入新队列平台。
5. artifact 继续走文件存储，数据库只保存状态和引用。

**验收**

- queued/running 三个崩溃时点均有重启测试。
- 两个 worker 竞争同一 IC task 时只执行一次。
- lease expiry、heartbeat 和 retry attempt 可审计。
- 持久化失败不再静默伪装为 durable success。

### T07. 前端 task/search request scope

**目标**

快速切换任务和搜索条件时，旧响应不能污染新状态。

**重点文件**

- `apps/web/src/pages/documents/useDocumentTasks.ts`
- `apps/web/src/pages/pdf/taskRequestScope.ts`
- `apps/web/src/pages/SearchDownload.tsx`
- 搜索参数 helper 和 tests

**实施步骤**

1. 将现有 PDF request scope 抽成可复用 helper，但不建立复杂全局框架。
2. document status/artifact/poll 分别绑定 generation 和 abort signal。
3. poll stop 校验 owner。
4. 搜索 market/year/query 使用一次原子 URL update。
5. popstate/searchParams 变化回灌表单；搜索响应按 request ID 丢弃迟到结果。

**验收**

- A->B、A->B->C 乱序响应测试通过。
- 旧 poll 不能停止新 poll。
- 智能解析一次更新保留 market/year/query 全部字段。
- 浏览器前进、后退、刷新后状态一致。

### T08. 会话失效与工作台数据恢复

**目标**

运行中认证失效能够一致恢复；数据加载失败不显示为真实空状态；列表请求不会无界增长。

**重点文件**

- `apps/web/src/shared/api/client.ts`
- `apps/web/src/lib/auth.tsx`
- `apps/web/src/pages/MyWorkspace.tsx`
- `apps/web/src/pages/PrimaryMarketWorkbench.tsx`
- `apps/web/src/components/layout/NotificationMenu.tsx`
- 对应 API list/summary routes

**实施步骤**

1. 建立去重的 session-invalidated channel。
2. 保存回跳 URL，排除 login/logout 自身的循环。
3. workspace 加显式 error/retry，并取消或去重 focus/timer 请求。
4. primary market list 增加服务端分页和状态摘要，移除无界 1+N。
5. notification 使用增量游标；后台页面暂停；read state 按 user/version 分区。
6. 修正 login autocomplete、quota 四态和 landmark 等低风险问题。

**验收**

- 同时出现多个 401 时只执行一次 session reset。
- 重新登录回到原目标。
- API 错误显示错误态，不显示“暂无数据”或“不限”。
- 项目数增加时列表请求数保持常数级。

### T09. Metrics、备份、恢复和 parser 生产化

**目标**

让监控、备份和 parser 运行方式满足生产长期运行要求。

**重点文件**

- `apps/api/main.py`
- `apps/api/services/observability.py`
- `scripts/ops/backup.sh`
- `infra/docker/postgres-init/001_create_databases.sql`
- `apps/pdf-parser/Dockerfile`
- `apps/document-parser/Dockerfile`
- supervisor/compose/health checks

**实施步骤**

1. metrics path label 改为路由模板；保护 `/metrics`。
2. backup 覆盖显式业务数据库清单，生成 checksum manifest。
3. 新增临时实例 restore smoke，验证关键 relation 和 Agent view。
4. parser 使用生产 WSGI server，并配置 timeout、graceful shutdown、readiness。
5. 对大模型 parser 避免因多 worker 重复加载模型。

**验收**

- 动态 ID 不增加 metrics label cardinality。
- 所有业务数据库可从最新备份恢复并通过最小查询。
- parser 容器不再运行 Flask dev server，生产 smoke 通过。

### T10. 金融精度和检索性能保护

**目标**

保证所有安全、可靠性和架构改动不降低金融答案质量与检索速度。

**必须持续执行**

1. PR deterministic financial QA。
2. 多市场 contract 和 identity tests。
3. Nightly Wiki/PostgreSQL parity。
4. Milvus recall/latency 和 embedding throughput。
5. 首 token、完整回答、fallback 和 calculator latency。
6. live model 攻击集（release 环境）。

**硬门禁**

- claim/evidence coverage 不下降。
- 已有攻击 case 不得从拒绝变为通过。
- retrieval recall 不下降。
- P95 超过 `R3-4` 性能保护线时不得默认发布，除非有书面评审和可量化收益。
- 不得通过减少候选、跳过 evidence、关闭 guard 或缩短 context 的方式伪造速度提升。

### T11. 核心文件按 owner 渐进拆分

**目标**

在行为稳定后降低核心模块修改风险，不以拆分数量或行数作为成果。

**启动条件**

- T04-T10 涉及的行为契约已经稳定。
- 待迁职责有独立输入输出和测试。
- 拆分不会与正在进行的功能修改产生大范围冲突。

**每轮步骤**

1. 选择一个稳定职责。
2. 写 characterization test。
3. 迁移到唯一 owner。
4. 原调用点改为直接依赖 owner 或短期兼容 wrapper。
5. 运行模块测试、API tests 和金融门禁。
6. 记录依赖数量、循环依赖和 wrapper 删除条件。

**停止条件**

- 下一职责没有稳定边界。
- 拆分需要同时改变 schema、prompt 或外部行为。
- 新模块只是转发层。
- 测试无法隔离 owner 行为。

### T12. 生产等价发布验收与收口

**目标**

用可重复证据证明目标完成，而不是用实现清单代替发布结论。

**必须交付**

- 任务账本最终状态。
- 测试和 benchmark 摘要。
- 容器 secret absence 报告。
- controlled Hermes HTTP/SSE 报告。
- 双用户权限负向报告。
- backup/restore 报告。
- 金融 QA、live smoke、Milvus 性能报告。
- 未完成项、残余风险、部署前人工动作和回滚步骤。

**完成条件**

- 所有 P0 任务 completed。
- P1 中标记为发布必需的任务 completed。
- 没有将条件风险误报为已修复，也没有将本地风险误报为外部泄露。
- 所有真实密钥、用户数据和绝对本机路径已从报告中脱敏。
- 旧方案文件和用户无关改动未被覆盖。

## 21. Codex 目标模式执行协议

### 21.1 可直接使用的目标指令

后续可将下面内容与本文路径一起交给 Codex：

```text
目标：以
docs/architecture/2026-07-12-siq-intelligent-research-platform-optimization-plan.md
为唯一实施任务书，在不重写现有框架、不降低金融事实精度和检索性能、
不覆盖用户已有改动的前提下，按 T00 -> T12 的依赖关系完成生产可信性优化。

执行要求：
1. 先完整阅读根 AGENTS.md、任务书和任务涉及的现有实现。
2. 建立并持续更新任务计划；任何时刻最多一个主任务 in_progress。
3. 每个缺陷先补失败测试或行为复现，再实施最小修复。
4. 完成每个任务时运行该任务验收和受影响的回归矩阵。
5. T10 金融精度与检索性能门禁从第一天持续执行，不得最后补测。
6. 不做前端框架重写、全链路异步化、全仓格式化或无依据的大文件拆分。
7. 不引入新的重量级基础设施；优先复用 PostgreSQL、Redis 和现有 artifact storage。
8. 保持 Wiki-first、PostgreSQL fallback、完整 ResearchIdentity、calculator、citation guard 和 answer audit。
9. 发现任务书判断与当前代码不符时，先用代码和测试验证；记录偏差并采用更小、更安全的实现，不盲从过期描述。
10. 保留用户脏工作树；不要回退、覆盖或清理不属于本目标的改动。
11. 未通过生产等价验收、恢复演练和金融/性能门禁，不得宣布总目标完成。
12. 不主动提交、推送、部署、轮换凭据或删除本地镜像，除非用户另行明确授权。

最终交付：
- 已完成任务和具体修改；
- 未完成任务及原因；
- 全部验证结果；
- 金融精度与性能对照；
- migration、部署、回滚和人工操作说明；
- 残余风险与后续建议。
```

### 21.2 执行过程报告格式

每完成一个任务，更新以下记录：

```markdown
### Txx 执行记录

- 状态：completed / blocked
- Base commit：<sha>
- 变更范围：<files/modules>
- 修复前证据：<test/probe>
- 实现摘要：<behavior, not file list only>
- 验证结果：<commands and counts>
- 金融/性能影响：<before vs after>
- Migration/部署动作：<none or exact steps>
- 回滚方式：<exact steps>
- 遗留风险：<none or explicit list>
```

### 21.3 偏差处理

执行中出现以下情况时，不得自行扩大范围：

- 需要修改外部生产资源或真实数据。
- 需要轮换真实凭据、推送镜像、执行部署或 Git 历史改写。
- 需要引入新的基础设施或付费服务。
- 当前用户改动与任务修改无法安全合并。
- 基准显示方案会降低金融精度、recall 或关键延迟。

此时应保留已完成的安全验证，明确说明证据、影响和可选方案，再由用户决定。

## 22. 最终验收清单

### 22.1 安全与边界

- [ ] 新 API 镜像不存在真实 env 和本地 token backup。
- [ ] Git 结论准确：忽略、未跟踪、未发现路径历史，不夸大为泄露。
- [ ] 所有连接 URL 日志完成结构化脱敏。
- [ ] SEC HTML 无同源脚本执行能力。
- [ ] 报告读取不能越出授权 artifact 根目录。
- [ ] production 内部服务 token 缺失时 fail closed。
- [ ] 上传边界在 Nginx 和 API 一致。

### 22.2 运行时与任务

- [ ] failed/cancelled/EOF 不生成成功回答。
- [ ] active run 在多 worker 下原子认领。
- [ ] quota reservation 并发安全。
- [ ] IC task 不会重复认领。
- [ ] queued/running job 重启后状态可恢复或明确 interrupted。
- [ ] 持久化失败有日志、metric 和 API 可见状态。

### 22.3 前端产品行为

- [ ] 文档任务切换无迟到响应污染。
- [ ] 搜索 URL、表单和浏览器历史一致。
- [ ] 运行中 401 只触发一次会话失效并支持回跳。
- [ ] workspace 错误不伪装为空状态。
- [ ] primary market 列表无无界 1+N 请求。
- [ ] 通知按用户隔离并在后台暂停轮询。

### 22.4 运维与质量

- [ ] metrics 使用低基数路由模板并受保护。
- [ ] 所有业务数据库完成可验证备份和恢复。
- [ ] parser 使用生产 server 并通过 readiness/shutdown smoke。
- [ ] PR、nightly、release gate 职责清晰且报告脱敏。
- [ ] Ruff/mypy/touched-files 门禁没有因本方案降级。

### 22.5 AI 与金融事实

- [ ] Wiki-first/PostgreSQL fallback 路线保持。
- [ ] 完整 ResearchIdentity 在请求、检索、回答、历史和审计中保持。
- [ ] claim/value/period/currency/evidence/calculator 门禁通过。
- [ ] 六市场 benchmark 和攻击样本不回退。
- [ ] Milvus recall、检索延迟和首 token 满足性能保护线。
- [ ] 向量结果没有成为金融数字的直接事实来源。

### 22.6 架构质量

- [ ] 没有前端或后端框架重写。
- [ ] 没有全链路一次性异步化。
- [ ] 没有新增无必要的重量级基础设施。
- [ ] 每个新 owner 都有稳定职责和独立测试。
- [ ] 没有为了减少行数创建 facade-only 模块。
- [ ] 兼容 wrapper 有明确删除条件。

## 23. 任务验证与证据矩阵

### 23.1 证据目录

执行产生的非源码证据统一写入 ignored 目录，避免污染功能 PR：

```text
artifacts/optimization/2026-07-12/
  baseline/
  security/
  runtime/
  frontend/
  operations/
  financial-quality/
  performance/
  release/
```

每份 JSON/Markdown 报告至少包含：

- `generated_at`
- `base_commit`
- `worktree_dirty`，只记录布尔值和脱敏摘要
- `task_id`
- `environment_profile`
- `command`，去除 secret 参数
- `result`
- `duration_seconds`
- `failures`
- `artifact_checksums`

不得写入真实 token、密码、cookie、完整 DSN、用户私有文档正文或不必要的绝对路径。

### 23.2 每任务最小验证

| 任务 | 现有回归 | 必须新增的定向验证 | 证据目录 |
| --- | --- | --- | --- |
| T00 | API/Web/parser/service 基线 | E1/E2 修复前失败探针、性能 baseline | `baseline/` |
| T01 | container security、runtime security | image secret absence、URL redaction 参数矩阵 | `security/` |
| T02 | auth report review、Web unit | iframe 隔离、path traversal、symlink、跨用户 artifact | `security/` |
| T03 | finder/rules auth、market report proxy | production missing-token startup、Nginx/API 上传边界 | `security/` |
| T04 | Agent runtime loops、Hermes client | failed/cancelled/timeout/EOF/duplicate terminal | `runtime/` |
| T05 | chat、usage service | active-run 多 worker 竞争、quota 并发 reservation | `runtime/` |
| T06 | job service、IC runtime | 三崩溃点重启、lease expiry、双 worker claim | `runtime/` |
| T07 | Web unit | deferred A/B/C、poll owner、URL history roundtrip | `frontend/` |
| T08 | auth client、workspace/primary market | 401 storm、错误态、分页请求数、用户通知隔离 | `frontend/` |
| T09 | observability、startup guards | cardinality、restore smoke、parser readiness/shutdown | `operations/` |
| T10 | financial QA、market contract | recall/latency、live attack、before/after 对照 | `financial-quality/`、`performance/` |
| T11 | 受影响模块完整回归 | characterization、import graph、owner dependency 对照 | 对应任务目录 |
| T12 | 全矩阵 | production-equivalent release、恢复、权限、live model | `release/` |

### 23.3 推荐命令

命令以执行时仓库实际脚本为准；若脚本接口已经演进，应先验证 `--help`，更新任务执行记录，不得静默跳过同等门禁。

**基础质量**

```bash
cd apps/api
uv run --frozen pytest -q

cd ../web
npm run test:unit
npm run check:frontend

cd ../..
python3 scripts/maintenance/check_local_security_hygiene.py --scope workflow
python3 scripts/maintenance/check_api_ci_test_coverage.py --fail-on-uncovered
python3 scripts/maintenance/check_python_quality_touched.py --base-ref HEAD --require-ruff --json
git diff --check
```

**安全与生产启动**

```bash
python3 -m pytest \
  scripts/maintenance/tests/test_container_security_config.py \
  scripts/maintenance/tests/test_production_startup_guards.py -q

cd apps/api
uv run --frozen pytest \
  tests/test_auth_report_review.py \
  tests/test_auth_router_current_user.py \
  tests/test_market_reports_proxy.py \
  tests/test_job_service.py -q
```

**金融事实与多市场门禁**

```bash
python3 scripts/maintenance/run_market_document_full_postgres_gate.py --mode contract
python3 scripts/maintenance/run_market_ingestion_eval.py --strict

bash scripts/ops/run_market_postgres_release_gate.sh \
  --mode offline-postgres \
  --output-dir artifacts/optimization/2026-07-12/financial-quality
```

**live smoke，仅在具备受控真实环境时执行**

```bash
cd apps/api
uv run python ../../scripts/maintenance/run_live_market_qa_smoke.py \
  --output ../../artifacts/optimization/2026-07-12/financial-quality/live-market-qa-smoke.json \
  --json
```

### 23.4 数据库和部署变更规则

- 使用项目确定的 DDL authority 和 migration 机制；本目标不顺带引入新的 migration 框架。
- migration 必须支持向前执行、兼容旧行读取，并提供可验证的回滚或前向修复方案。
- job/lease/quota 表结构不得与业务 artifact 大对象耦合。
- 任何生产数据 backfill 先提供 dry-run、影响行数、批次大小、锁范围和恢复点。
- Codex 可以编写 migration 和部署说明，但未经用户明确授权不得连接生产库、执行部署或变更外部资源。

### 23.5 变更集控制

每个任务原则上形成一个独立、可审查的变更集。以下组合禁止出现在同一变更集：

- 安全边界修复 + Agent prompt 改写。
- 数据库 migration + 全局格式化。
- active-run/终态状态机 + 前端视觉重做。
- Milvus 参数调整 + embedding model 升级。
- owner 拆分 + 外部 API contract 变更。

若一个任务必须跨前后端，必须以同一行为契约为中心，例如上传 413、401 session invalidation 或 task status；不得趁机整理无关代码。

## 24. 最终完成判定

只有满足以下全部条件，Codex 才能将总目标标记为完成：

1. T00-T10、T12 中全部 P0 和发布必需任务完成。
2. T11 已按准入条件完成合理范围，或有证据说明继续拆分会违反“不为了拆而拆”。
3. 第 22 节所有适用项完成；不适用项有书面理由。
4. quick、integration、nightly/release 中当前环境可执行的测试全部通过。
5. 真实数据或外部环境无法执行的门禁被明确列出，不能用 mock 结果冒充 live 结果。
6. 金融准确性、检索 recall 和性能没有低于基线或保护线。
7. 交付报告包含部署、migration、回滚、残余风险和人工动作。

本方案追求的不是最大改动量，而是最小必要改动下的最大可信度。完成后的 SIQ 应更容易证明正确、更容易恢复、更容易发布，同时保持当前投研事实链路、产品结构和多市场能力的连续性。
