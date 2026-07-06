# Hermes 智能体统一记忆架构升级任务书

日期: 2026-07-06
状态: Draft for implementation
适用范围: `agents/hermes`、`apps/api`、`infra/docker`、`docs/architecture`

## 0. 执行摘要

本任务书用于指导智能体直接执行 Hermes 二次开发中的统一记忆系统升级。升级目标是提高问答聊天精度、智能体任务执行准确度、一级市场 IC 智能体协同能力，并补强用户隔离、项目共享记忆、长期记忆检索、证据追踪和可审计能力。

核心决策如下:

1. PostgreSQL 使用当前项目路径下的实例和数据目录，不使用外部路径。
2. PostgreSQL 数据目录固定为 `/home/maoyd/siq-research-engine/data/postgres`。
3. 不新建独立 `siq_memory` database。统一使用现有 `siq_app` database。
4. 在 `siq_app` 内新增 `agent_memory` schema。
5. 第一阶段直接使用 Milvus 做向量检索，专用 collection 为 `siq_agent_memory`。
6. 记忆隔离模型采用 `用户私有 + 项目/团队共享 + 系统共享`。
7. 一级市场 IC 智能体输出和记忆支持按 `deal_id/project_id` 共享。
8. Hermes 本地目录只保留运行态、缓存、日志和诊断信息，不作为权威长期记忆源。

## 1. 目标

### 1.1 业务目标

1. 提升二级市场问答智能体的上下文连续性、用户偏好识别和历史纠错能力。
2. 提升一级市场 IC 智能体在项目尽调、讨论、审核、风险控制和阶段输出中的协同精度。
3. 支持同一项目团队共享一级市场智能体产出的关键结论、证据摘要、风险判断和决策依据。
4. 防止用户之间、项目之间、智能体 profile 之间发生记忆串扰。
5. 让每条被使用的长期记忆都可追溯到来源消息、工具调用、文件证据、Deal OS artifact 或人工确认。

### 1.2 技术目标

1. 建立统一的 PostgreSQL 权威记忆存储层。
2. 建立统一的记忆写入、提取、晋升、检索和审计服务。
3. 建立可插拔向量检索接口，第一阶段实现 Milvus dense recall、PostgreSQL lexical recall 和本机 reranker 重排。
4. 补强 session/message/memory 的显式用户隔离字段和查询约束。
5. 统一 Hermes profile 结构，清理源码 profile 下的运行态污染。
6. 保留本地开发可运行性，同时将高级记忆能力默认绑定项目内 PostgreSQL。

## 2. 非目标

1. 第一阶段不新建 `siq_memory` database。
2. 第一阶段不把 Milvus 作为权威事实源，Milvus 只保存向量索引和检索字段。
3. 第一阶段不让 Hermes profile 本地 `memories/` 或 `state.db` 成为长期记忆主库。
4. 第一阶段不做跨组织物理库隔离，只做数据库字段、权限、查询和可选 RLS 的逻辑隔离。
5. 第一阶段不重构全部聊天和智能体业务，只围绕记忆、会话隔离和 IC 共享能力做最小闭环。

## 3. 当前状态摘要

### 3.1 PostgreSQL

项目 Docker PostgreSQL 当前定义在 `infra/docker/docker-compose.yml`，服务名为 `postgres`，容器通常为 `docker-postgres-1`，端口默认 `15432`。

初始化脚本 `infra/docker/postgres-init/001_create_databases.sql` 会创建:

```text
siq_app
siq_document_parser
siq_us
siq_hk
siq_jp
siq_kr
siq_eu
```

Compose 还配置了默认 `POSTGRES_DB=siq`，因此项目库集合中包含 `siq`。

当前实际容器中存在的非模板 database 包括:

```text
ai_platform
ai_platform_trial
finsight
pdf2md_fix_20260521
postgres
siq
siq_app
siq_document_parser
siq_eu
siq_hk
siq_jp
siq_kr
siq_us
```

本升级只使用 `siq_app`。`ai_platform`、`ai_platform_trial`、`finsight`、`pdf2md_fix_20260521` 视为历史或旁路库，不参与本次记忆架构。

### 3.2 现有本地数据库文件

项目 `data/` 下已存在多个 SQLite 运行态数据库:

```text
data/backend/agent.db
data/wiki/derived/financial_metrics.db
data/document-parser/db/tasks.db
data/pdf-parser/db/tasks.db
data/hermes/home/state.db
data/hermes/home/kanban.db
data/hermes/home/response_store.db
data/hermes/home/profiles/*/state.db
data/hermes/home/profiles/*/response_store.db
```

这些文件不能作为新统一记忆系统的权威长期存储。必要时只做迁移来源、诊断来源或运行态保留。

### 3.3 Hermes profile

当前 `agents/hermes/profiles` 下大致分为:

1. 二级市场智能体: `siq_assistant`、`siq_analysis`、`siq_factchecker`、`siq_tracking`、`siq_legal`。
2. 一级市场 IC 智能体: `siq_ic_master_coordinator`、`siq_ic_chairman`、`siq_ic_strategist`、`siq_ic_sector_expert`、`siq_ic_finance_auditor`、`siq_ic_legal_scanner`、`siq_ic_risk_controller`。
3. 共享目录: `shared`、`siq_ic_shared`。

现状问题:

1. `manifest.json` 对二级市场和一级市场分组表达不完整。
2. 部分源码 profile 下存在 `logs/`、`memories/`、`sandboxes/`、`platforms/`、`__pycache__` 等运行态污染。
3. Hermes profile 内部 `memory.memory_enabled` 基本关闭，当前真正的本地记忆来自 API 层。
4. 一级市场 IC profile 有共享技能和角色文件，但长期记忆共享没有统一数据库层。

### 3.4 当前用户隔离

当前 API 路由已通过登录用户、session 前缀和 `SessionManager` 做了较多隔离。典型 session id 形态为:

```text
user-{user_id}-{profile}-{uuid}
```

但数据库层的旧 `ChatMessage` 和 `ChatSessionMemory` 缺少显式 `user_id`、`tenant_id/org_id`、`profile` 等字段，存在结构性脆弱点。升级后必须将用户隔离下沉到数据模型和查询条件中，而不是只依赖 session id 前缀。

### 3.5 一级市场 Deal OS 隔离

当前 Deal OS 项目包主要按 `deal_id` 写入本地文件结构，存在 `created_by` 元数据，但读取多处依赖 RBAC 权限而不是 deal 成员关系过滤。升级后一级市场共享记忆必须按 `deal_id/project_id` 和成员权限过滤。

## 4. 目标架构

### 4.1 总体架构

```text
User / Web
  -> API Router
    -> SessionManager
    -> AgentChatRuntime / IC Agent Runtime
    -> MemoryService
      -> PostgreSQL siq_app.agent_memory
      -> Milvus siq_agent_memory collection
      -> Redis
      -> Evidence/File/Deal OS index
    -> Hermes Gateway
      -> Hermes profile runtime
```

### 4.2 记忆分层

```text
L0 Raw Event Memory
  原始消息、工具调用、运行记录、输入输出、附件引用。

L1 Working Session Memory
  当前会话窗口、滚动摘要、短期任务状态。

L2 Long-term Semantic Memory
  用户偏好、项目结论、历史纠错、长期事实、任务经验。

L3 Evidence Memory
  文档、报告、Deal OS evidence、Wiki、PostgreSQL 事实表和 artifact 的索引。

L4 Procedural Memory
  成功工作流、失败原因、审计经验、智能体协作模式。
```

### 4.3 记忆可见性

```text
user_private
  用户私有。默认用于二级市场聊天偏好、个人历史、个人纠错。

project_shared
  项目或团队共享。默认用于一级市场 deal/project 智能体协作。

system_shared
  系统共享。用于全局流程、工具经验、通用规则。
```

### 4.4 二级市场记忆策略

默认 scope:

```text
tenant_id + user_id + profile
```

可选补充 scope:

```text
market
symbol
company_id
report_task_id
```

二级市场智能体默认不把用户私人聊天沉淀为团队共享记忆。只有用户明确保存、管理员审核或系统规则判定为公共研究资料时，才允许进入 `project_shared` 或 `system_shared`。

### 4.5 一级市场记忆策略

默认 scope:

```text
tenant_id + deal_id/project_id + agent_group=primary_market
```

一级市场 IC 智能体输出默认可以沉淀到项目共享记忆，但必须满足:

1. 当前用户有 deal/project 访问权限。
2. 记忆来源可追踪到会话、阶段输出、证据、文件或审计记录。
3. 记忆标记 `visibility=project_shared`。
4. 风险判断、法务扫描、财务审核等结论必须保存来源和置信度。

## 5. 数据库与存储设计

### 5.1 PostgreSQL 数据目录

必须将 Docker PostgreSQL host 数据目录改为项目路径:

```text
/home/maoyd/siq-research-engine/data/postgres
```

Compose 建议:

```yaml
services:
  postgres:
    volumes:
      - ../../data/postgres:/var/lib/postgresql/data
      - ./postgres-init:/docker-entrypoint-initdb.d:ro
```

要求:

1. 不使用 Docker named volume `postgres_data` 承载新数据。
2. 不使用系统目录如 `/var/lib/postgresql` 作为 host 数据目录。
3. 不使用项目外路径。
4. `data/postgres` 必须保持 `.gitignore` 忽略。

### 5.2 Database 与 schema

目标:

```text
database: siq_app
schema: agent_memory
```

初始化 SQL:

```sql
CREATE SCHEMA IF NOT EXISTS agent_memory;
CREATE EXTENSION IF NOT EXISTS vector;
```

不得创建:

```text
siq_memory
```

除非未来进入第二阶段并明确需要物理隔离。

### 5.3 核心表

#### 5.3.1 `agent_memory.sessions`

用途: 权威会话表，替代只靠 session id 字符串前缀的隔离方式。

关键字段:

```text
id
session_id
tenant_id
user_id
profile
agent_group
title
status
visibility
deal_id
project_id
metadata_json
created_at
updated_at
last_active_at
deleted_at
```

唯一约束:

```text
unique(session_id)
index(tenant_id, user_id, profile, last_active_at)
index(tenant_id, deal_id, profile)
```

#### 5.3.2 `agent_memory.messages`

用途: 权威聊天消息表。

关键字段:

```text
id
session_id
tenant_id
user_id
profile
agent_group
role
content
attachments_json
token_count
model_name
created_at
```

索引:

```text
index(tenant_id, user_id, profile, session_id, created_at)
index(tenant_id, session_id, created_at)
```

#### 5.3.3 `agent_memory.runs`

用途: 智能体任务执行记录。

关键字段:

```text
id
run_id
session_id
tenant_id
user_id
profile
agent_group
deal_id
project_id
task_type
status
input_json
output_json
error_json
started_at
finished_at
```

#### 5.3.4 `agent_memory.tool_events`

用途: 工具调用、数据库检索、文件读取、Web/API 调用审计。

关键字段:

```text
id
run_id
session_id
tenant_id
user_id
profile
tool_name
tool_input_json
tool_output_ref
status
latency_ms
created_at
```

注意: 大型输出不要直接塞入 JSON，应保存 artifact 引用。

#### 5.3.5 `agent_memory.session_summaries`

用途: 会话滚动摘要。

关键字段:

```text
id
session_id
tenant_id
user_id
profile
summary
last_message_id
message_count
summary_version
created_at
updated_at
```

#### 5.3.6 `agent_memory.memory_items`

用途: 长期记忆主表。

关键字段:

```text
id
tenant_id
owner_user_id
created_by
profile
agent_group
visibility
deal_id
project_id
memory_type
title
content
normalized_content
source_type
source_id
confidence
importance
valid_from
valid_until
status
metadata_json
created_at
updated_at
deleted_at
```

推荐 `memory_type`:

```text
user_preference
correction
project_fact
project_decision
risk_finding
legal_finding
financial_finding
workflow_pattern
tooling_note
evidence_summary
```

推荐 `status`:

```text
candidate
active
superseded
rejected
deleted
```

#### 5.3.7 `agent_memory.memory_embeddings`

用途: 可选 pgvector 兼容表。当前第一阶段默认使用 Milvus，不依赖此表。

关键字段:

```text
id
memory_id
embedding_model
embedding vector(...)
content_hash
created_at
```

索引:

```sql
CREATE INDEX IF NOT EXISTS idx_memory_embeddings_vector
ON agent_memory.memory_embeddings
USING ivfflat (embedding vector_cosine_ops);
```

实际维度必须与项目选定 embedding 模型一致。

#### 5.3.8 `agent_memory.memory_links`

用途: 连接记忆与来源消息、artifact、evidence、deal 文件、PostgreSQL 事实表。

关键字段:

```text
id
memory_id
link_type
target_type
target_id
target_uri
metadata_json
created_at
```

#### 5.3.9 `agent_memory.access_bindings`

用途: 项目共享记忆访问控制。

关键字段:

```text
id
tenant_id
resource_type
resource_id
principal_type
principal_id
role
created_at
```

示例:

```text
resource_type=deal
resource_id={deal_id}
principal_type=user
principal_id={user_id}
role=viewer/editor/owner
```

#### 5.3.10 `agent_memory.feedback_events`

用途: 用户反馈、纠错、记忆质量评价。

关键字段:

```text
id
tenant_id
user_id
memory_id
session_id
feedback_type
feedback_text
created_at
```

## 6. 服务设计

### 6.1 新增 `MemoryService`

建议新增:

```text
apps/api/services/agent_memory_service.py
apps/api/services/agent_memory_models.py
apps/api/services/agent_memory_retrieval.py
apps/api/services/agent_memory_extraction.py
apps/api/services/agent_memory_access.py
```

核心接口:

```python
record_session(...)
record_message(...)
record_run_start(...)
record_run_finish(...)
record_tool_event(...)
refresh_session_summary(...)
extract_memory_candidates(...)
promote_memory_item(...)
reject_memory_candidate(...)
search_memory_context(...)
build_memory_context_block(...)
```

### 6.2 写入流程

```text
1. 用户发起聊天或智能体任务。
2. API 解析 current_user、profile、session_id、deal_id/project_id。
3. 写入 agent_memory.sessions。
4. 写入 user message。
5. 启动 agent run。
6. 记录工具调用和检索事件。
7. 写入 assistant response。
8. 刷新 session summary。
9. 异步或同步低优先级执行 memory extraction。
10. 候选记忆经规则校验后进入 memory_items。
11. 生成 embedding，写入 memory_embeddings。
```

### 6.3 记忆晋升规则

允许晋升:

1. 用户明确表达的长期偏好。
2. 用户明确纠正过的错误。
3. 一级市场项目阶段性结论。
4. IC 主席、风控、法务、财务等角色输出的已落盘结论。
5. 带来源证据的事实摘要。
6. 可复用的成功任务流程。

禁止晋升:

1. 密码、token、密钥、Cookie。
2. 未验证的临时猜测。
3. 纯粹寒暄。
4. 与来源证据冲突且未标记为争议的信息。
5. 过期事实且没有有效期标注的信息。

### 6.4 检索流程

输入:

```text
current_user
tenant_id
profile
agent_group
session_id
deal_id/project_id
message
```

检索顺序:

```text
1. 最近 N 条消息
2. 当前 session summary
3. user_private semantic memory
4. project_shared memory
5. system_shared procedural memory
6. evidence memory
7. current deal/task state
```

过滤条件:

```text
tenant_id = current_tenant
AND status = active
AND deleted_at IS NULL
AND (
  visibility = system_shared
  OR (visibility = user_private AND owner_user_id = current_user.id)
  OR (visibility = project_shared AND user_has_project_access(...))
)
```

排序信号:

```text
semantic_similarity
profile_match
deal_or_project_match
recency
importance
confidence
feedback_score
source_reliability
```

输出格式:

```text
<memory-context>
  <session-summary>...</session-summary>
  <user-memory>...</user-memory>
  <project-memory>...</project-memory>
  <evidence-memory>...</evidence-memory>
</memory-context>
```

约束:

1. 当前用户问题优先级高于长期记忆。
2. 已验证证据优先级高于长期记忆。
3. 记忆必须带来源或置信度。
4. 不允许将不可见记忆注入 prompt。

## 7. Hermes profile 与网关任务

### 7.1 统一 profile 分组

更新 `agents/hermes/profiles/manifest.json`，建议新增:

```json
{
  "groups": {
    "secondary_market": [
      "siq_assistant",
      "siq_analysis",
      "siq_factchecker",
      "siq_tracking",
      "siq_legal"
    ],
    "primary_market_ic": [
      "siq_ic_master_coordinator",
      "siq_ic_chairman",
      "siq_ic_strategist",
      "siq_ic_sector_expert",
      "siq_ic_finance_auditor",
      "siq_ic_legal_scanner",
      "siq_ic_risk_controller"
    ],
    "shared": [
      "shared",
      "siq_ic_shared"
    ]
  }
}
```

保留原有字段时要兼容旧逻辑，不得破坏 `scripts/hermes/profile_dir.sh` 和 `scripts/hermes/run_gateway.sh`。

### 7.2 统一 profile 文件结构

每个业务 profile 建议具备:

```text
config.yaml
README.md
SOUL.md
IDENTITY.md
BOOTSTRAP.md
AGENTS.md
HEARTBEAT.md
TOOLS.md
USER.md
```

一级市场角色可额外保留:

```text
WORKFLOW.md
ORCHESTRATION_BRIDGE.md
KNOWLEDGE_BASE.md
```

### 7.3 清理源码运行态污染

源码 profile 下不得保留:

```text
logs/
memories/
sandboxes/
platforms/
__pycache__/
state.db
response_store.db
```

这些内容只允许出现在:

```text
data/hermes/home
```

建议新增校验脚本:

```text
scripts/hermes/validate_profiles.py
```

校验内容:

1. 必需文件是否齐全。
2. 禁止目录是否存在。
3. `config.yaml` 是否存在。
4. profile 是否被 manifest 引用。
5. group 是否设置正确。

## 8. 用户隔离与权限任务

### 8.1 会话隔离

所有新查询必须显式包含:

```text
tenant_id
user_id
profile
session_id
```

不得只用:

```text
WHERE session_id = :session_id
```

除非该查询之前已经通过强类型 session context 验证并绑定了用户。

### 8.2 一级市场项目共享权限

新增统一函数:

```python
user_has_project_memory_access(user_id, tenant_id, deal_id=None, project_id=None, role=None) -> bool
```

一级市场共享记忆读取必须通过此函数或等价权限层。

### 8.3 旧 router 风险

检查并处理旧的非用户隔离路由:

```text
apps/api/routers/agent_chat_router.py
```

如果未使用:

1. 标记 deprecated。
2. 从主路由确认未挂载。
3. 增加测试防止重新挂载。

如果仍有使用:

1. 必须接入 current_user。
2. 必须改用 `SessionManager`。
3. 必须写入 `agent_memory.sessions/messages`。

## 9. 开发阶段与任务拆分

### P0: 基础设施和安全备份

目标: 将 PostgreSQL 数据落到项目路径，并为 schema 升级做好备份。

任务:

1. 备份当前 `docker-postgres-1` 数据。
2. 导出 `siq_app`、`siq_document_parser`、市场库和必要历史库。
3. 修改 `infra/docker/docker-compose.yml`，将 `postgres_data` named volume 改为 `../../data/postgres` bind mount。
4. 保留 `./postgres-init:/docker-entrypoint-initdb.d:ro`。
5. 确认 `data/postgres` 被 `.gitignore` 忽略。
6. 更新 `infra/env/local.example`，默认使用项目内 PostgreSQL 的 `siq_app`。
7. 增加运维文档，说明如何从旧 named volume 迁移到项目路径。

验收:

1. `docker compose up postgres` 后数据目录出现在 `/home/maoyd/siq-research-engine/data/postgres`。
2. API 可连接 `siq_app`。
3. 重启容器后数据不丢失。
4. 不再依赖 Docker named volume `postgres_data` 保存新数据。

### P1: 数据库 schema 与迁移

目标: 在 `siq_app` 中创建 `agent_memory` schema 和核心表。

任务:

1. 新增 SQL migration 或项目现有迁移脚本。
2. 创建 `agent_memory` schema。
3. 创建 `agent_memory` schema；仅在显式选择 pgvector 兼容后才创建 `vector` extension。
4. 创建 sessions/messages/runs/tool_events/session_summaries/memory_items/memory_embeddings/memory_links/access_bindings/feedback_events。
5. 为用户隔离和检索加索引。
6. 保留 SQLModel 模型定义或数据库访问 DTO。

验收:

1. 空库初始化成功。
2. 已有 `siq_app` 增量迁移成功。
3. 重复执行迁移不会破坏数据。
4. Milvus `siq_agent_memory` collection 可创建、写入和查询测试 embedding。

### P2: 统一 MemoryService

目标: 建立统一记忆写入和读取服务。

任务:

1. 新增 `agent_memory_service.py`。
2. 新增 `agent_memory_access.py` 处理权限。
3. 新增 `agent_memory_retrieval.py` 或等价服务处理 Milvus dense recall、PostgreSQL lexical recall 和 reranker。
4. 新增 `agent_memory_extraction.py` 处理候选记忆生成。
5. 接入现有 `agent_runtime_memory.py`，逐步替换旧 `ChatSessionMemory` 逻辑。
6. 所有服务函数必须接受明确的 `MemoryRequestContext`。

建议上下文结构:

```python
class MemoryRequestContext:
    tenant_id: str
    user_id: int
    profile: str
    agent_group: str
    session_id: str
    deal_id: str | None = None
    project_id: str | None = None
```

验收:

1. 单元测试覆盖 user_private、project_shared、system_shared。
2. 无 current_user 时不能读写用户记忆。
3. 伪造 session_id 不能越权读取。

### P3: 聊天链路接入

目标: 将主聊天和 specialist 聊天写入统一记忆系统。

涉及文件:

```text
apps/api/routers/chat.py
apps/api/services/agent_chat_runtime_impl.py
apps/api/routers/agent_user_router.py
apps/api/services/session_manager.py
```

任务:

1. 创建或恢复 session 时写入 `agent_memory.sessions`。
2. 用户消息写入 `agent_memory.messages`。
3. assistant 回复写入 `agent_memory.messages`。
4. specialist agent 路由使用同一 MemoryService。
5. 历史查询改为优先从 `agent_memory.messages` 查询。
6. 旧 `ChatMessage` 保留兼容期，或通过迁移转换。

验收:

1. 普通聊天可正常返回。
2. 流式聊天可正常返回。
3. 历史记录按用户隔离。
4. 不同 profile 的会话不会串。
5. 关闭记忆检索时仍可正常聊天。

### P4: 记忆检索与 prompt 注入

目标: 在模型调用前注入高质量、可控、可追踪的记忆上下文。

任务:

1. 实现 session summary 加载。
2. 实现最近消息窗口加载。
3. 实现 user_private Milvus dense 检索。
4. 实现 project_shared Milvus dense 检索。
5. 实现 evidence memory link 渲染。
6. 实现 memory context block 渲染。
7. 在 `agent_chat_runtime_impl.py` 合适位置注入。
8. 增加开关:

```text
SIQ_AGENT_MEMORY_ENABLED=true
SIQ_AGENT_MEMORY_PGVECTOR_ENABLED=true
SIQ_AGENT_MEMORY_MAX_ITEMS=8
SIQ_AGENT_MEMORY_MAX_TOKENS=1800
```

验收:

1. 对同一用户的后续问题能召回其历史偏好。
2. 对不同用户不能召回彼此私有记忆。
3. 对一级市场同一 deal 能召回项目共享结论。
4. prompt 中不出现无权限记忆。
5. 记忆上下文带来源和置信度。

### P5: 一级市场 IC 共享记忆

目标: 让一级市场智能体围绕 deal/project 建立共享记忆。

涉及文件:

```text
apps/api/services/ic_agent_runtime.py
apps/api/services/ic_startup_retrieval.py
apps/api/services/deal_store.py
apps/api/routers/deals.py
```

任务:

1. 为 deal/project 建立 `project_shared` memory scope。
2. IC 阶段输出写入 memory_items。
3. 风控、法务、财务、行业专家结论写入对应 memory_type。
4. `ic_startup_retrieval.py` 从 PostgreSQL 读取项目共享记忆。
5. Deal OS evidence 和 memory_items 建立 memory_links。
6. deal 读取接口增加成员或访问权限过滤。

验收:

1. 同一 deal 的多个 IC 智能体能共享已确认结论。
2. 不同 deal 之间不串记忆。
3. 无 deal 权限的用户不能读取项目共享记忆。
4. 项目报告可追溯到 memory_links 和 evidence。

### P6: 记忆提取、去重和冲突处理

目标: 控制长期记忆质量，防止污染。

任务:

1. 实现候选记忆提取规则。
2. 实现内容 hash 去重。
3. 实现相似记忆合并。
4. 实现冲突检测。
5. 支持 `superseded` 状态。
6. 支持用户反馈修正。
7. 支持人工审核接口预留。

验收:

1. 重复偏好不会无限新增。
2. 新纠错能覆盖旧错误。
3. 冲突记忆不会直接作为确定事实注入。
4. 被删除或 rejected 的记忆不会被检索。

### P7: Hermes profile 对齐

目标: 统一 profile 结构，避免本地记忆和源码污染。

任务:

1. 更新 `manifest.json` 分组。
2. 编写 profile lint 脚本。
3. 清理源码 profile 下禁止目录。
4. 保留 `data/hermes/home` 下运行态。
5. 更新 README，说明权威记忆在 PostgreSQL。
6. 确认 gateway 启动不受影响。

验收:

1. lint 通过。
2. gateway 可启动所有二级市场 profile。
3. 设置 `SIQ_ENABLE_IC_HERMES=1` 后可启动一级市场 IC profile。
4. 源码 profile 不再包含运行态数据库或日志。

### P8: 旧数据迁移

目标: 尽量保留现有聊天历史和摘要。

任务:

1. 从旧 `ChatMessage` 迁移到 `agent_memory.messages`。
2. 从旧 `ChatSessionMemory` 迁移到 `agent_memory.session_summaries` 或 memory_items。
3. 根据 session id 前缀解析 user_id/profile。
4. 不能解析的记录标记为 `migration_unknown`，不得默认公开。
5. Hermes 本地 `state.db/response_store.db` 暂不迁移为长期记忆，只可做诊断参考。

验收:

1. 可解析旧消息迁移成功。
2. 不能解析的记录不会被任意用户读取。
3. 迁移脚本可 dry-run。
4. 迁移脚本可重复执行且幂等。

### P9: 测试、观测和运维

目标: 保证记忆系统安全、稳定、可审计。

任务:

1. 增加单元测试。
2. 增加 API 集成测试。
3. 增加越权访问测试。
4. 增加 Milvus collection 入库、召回和 rerank 降级测试。
5. 增加一级市场 deal 共享记忆测试。
6. 增加 memory extraction 质量测试。
7. 增加日志字段:

```text
memory_query_count
memory_injected_count
memory_filtered_count
memory_latency_ms
memory_scope
```

8. 增加备份脚本对项目内 PostgreSQL 的说明。

验收:

1. 测试覆盖私有记忆、共享记忆、无权限过滤、迁移、Milvus 召回和 rerank 降级。
2. 记忆检索失败时聊天降级可用。
3. 日志不输出敏感记忆全文。
4. 备份和恢复流程可执行。

## 10. 推荐开发顺序

必须按以下顺序推进:

```text
P0 基础设施和备份
P1 schema 与迁移
P2 MemoryService
P3 聊天链路写入
P4 检索与 prompt 注入
P5 一级市场共享记忆
P6 质量控制
P7 profile 对齐
P8 旧数据迁移
P9 测试和运维
```

不得先做 P4 prompt 注入再做 P1/P2，否则会放大越权和记忆污染风险。

## 11. 配置项

新增建议配置:

```text
SIQ_AGENT_MEMORY_ENABLED=true
SIQ_AGENT_MEMORY_WRITE_ENABLED=true
SIQ_AGENT_MEMORY_RETRIEVAL_ENABLED=true
SIQ_AGENT_MEMORY_PGVECTOR_ENABLED=true
SIQ_AGENT_MEMORY_EXTRACTION_ENABLED=true
SIQ_AGENT_MEMORY_MAX_ITEMS=8
SIQ_AGENT_MEMORY_MAX_TOKENS=1800
SIQ_AGENT_MEMORY_MIN_SCORE=0.72
SIQ_AGENT_MEMORY_DEFAULT_VISIBILITY=user_private
SIQ_AGENT_MEMORY_PRIMARY_MARKET_VISIBILITY=project_shared
SIQ_AGENT_MEMORY_SCHEMA=agent_memory
```

本地数据库连接建议:

```text
SIQ_APP_DATABASE_URL=postgresql+psycopg://postgres:${POSTGRES_PASSWORD}@127.0.0.1:15432/siq_app
```

Docker 内 API 连接建议保持:

```text
SIQ_APP_DATABASE_URL=postgresql+psycopg://postgres:${POSTGRES_PASSWORD}@postgres:5432/siq_app
```

## 12. 安全约束

开发智能体必须遵守:

1. 不创建 `siq_memory` database。
2. 不把 PostgreSQL host 数据目录放到项目外。
3. 不把用户私有记忆注入其他用户 prompt。
4. 不把无权限 deal/project 记忆注入 prompt。
5. 不将密钥、token、Cookie、密码沉淀为长期记忆。
6. 不将 Hermes 本地 `state.db` 作为长期记忆权威源。
7. 不删除现有数据，迁移前必须备份。
8. 不重构无关业务模块。
9. 所有记忆读取必须经过权限过滤。
10. 所有长期记忆必须保留 source/provenance。

## 13. 关键验收用例

### 13.1 用户私有记忆隔离

步骤:

1. 用户 A 在 `siq_assistant` 中保存偏好。
2. 用户 B 用同一 profile 提问。
3. 用户 B 不能召回用户 A 偏好。

通过标准:

```text
memory_injected_count 不包含用户 A 的 memory_id
```

### 13.2 伪造 session id

步骤:

1. 用户 B 构造 `user-{A_id}-siq_assistant-xxx`。
2. 调用历史或聊天接口。

通过标准:

```text
返回 403 或创建用户 B 自己的新 session
不得返回用户 A 历史
```

### 13.3 一级市场 deal 共享记忆

步骤:

1. 用户 A 在 deal X 中运行 IC 法务扫描。
2. 生成 `legal_finding` project_shared memory。
3. 用户 B 是 deal X 成员。
4. 用户 B 运行 IC 主席总结。

通过标准:

```text
用户 B 可以召回 deal X 的 legal_finding
```

### 13.4 一级市场 deal 隔离

步骤:

1. 用户 A 在 deal X 中生成风险结论。
2. 用户 A 或 B 在 deal Y 中提问。

通过标准:

```text
deal X 的 project_shared memory 不得注入 deal Y prompt
```

### 13.5 Milvus 混合检索

步骤:

1. 写入一条 user_private memory。
2. 写入 embedding。
3. 用语义相近问题检索，并与 PostgreSQL lexical 候选合并后 rerank。

通过标准:

```text
返回来自 `siq_agent_memory` 的候选，且通过权限过滤和 rerank 排序。
```

### 13.6 记忆冲突

步骤:

1. 写入旧偏好。
2. 用户明确纠正。
3. 系统生成新 correction。

通过标准:

```text
旧 memory status=superseded
新 memory status=active
prompt 只注入新记忆
```

## 14. 回滚方案

### 14.1 基础设施回滚

1. 保留旧 Docker named volume 的 dump。
2. 如果 bind mount 启动失败，停止服务并恢复 compose volume。
3. 用 dump 恢复 `siq_app`。

### 14.2 应用回滚

1. 通过 `SIQ_AGENT_MEMORY_ENABLED=false` 关闭记忆系统。
2. 保留旧聊天流程。
3. 不删除新表，只停止写入和检索。

### 14.3 数据回滚

1. 新表使用独立 schema `agent_memory`。
2. 若出现问题，可停止使用 schema。
3. 不影响 `public` 旧表。

## 15. 开发智能体启动清单

开始执行前必须阅读:

```text
apps/api/database.py
apps/api/models.py
apps/api/services/agent_runtime_memory.py
apps/api/services/agent_chat_runtime_impl.py
apps/api/services/session_manager.py
apps/api/routers/chat.py
apps/api/routers/agent_user_router.py
apps/api/services/ic_agent_runtime.py
apps/api/services/ic_startup_retrieval.py
apps/api/services/deal_store.py
apps/api/routers/deals.py
infra/docker/docker-compose.yml
infra/docker/postgres-init/001_create_databases.sql
agents/hermes/profiles/manifest.json
scripts/hermes/run_gateway.sh
scripts/hermes/profile_dir.sh
```

执行前必须确认:

```text
1. 当前 git worktree 状态。
2. PostgreSQL dump 已完成。
3. data/postgres 不在 git tracking 中。
4. 当前 API 使用的是 siq_app。
5. 没有其它服务依赖 Docker named volume 的隐含路径。
```

每个阶段完成后必须输出:

```text
1. 修改文件列表。
2. 数据库迁移说明。
3. 测试命令和结果。
4. 已知风险。
5. 下一阶段建议。
```

## 16. 最终完成定义

本升级完成必须同时满足:

1. 项目内 PostgreSQL 使用 `/home/maoyd/siq-research-engine/data/postgres`。
2. `siq_app.agent_memory` schema 可用。
3. 聊天消息、会话摘要、任务运行、工具调用和长期记忆统一入库。
4. 二级市场用户私有记忆可检索且不串用户。
5. 一级市场 deal/project 共享记忆可检索且不串项目。
6. Milvus `siq_agent_memory` 检索可用。
7. pgvector 仅保留为可选兼容后端，不影响第一阶段运行。
8. Hermes profile 源目录结构统一且无运行态污染。
9. 旧会话隔离脆弱点已通过显式字段和测试补强。
10. 记忆系统可关闭、可降级、可审计、可回滚。
