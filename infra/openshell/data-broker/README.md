# SIQ OpenShell 只读数据 Broker

`scripts/openshell/read_only_data_broker.py` 是 OpenShell sandbox 访问宿主数据的窄边界。默认监听 `127.0.0.1:18793`；显式 bridge 模式只绑定 Docker 网络 `siq-openshell-dev` 中经过校验的 gateway，不能绑定任意私网地址。它只暴露以下路由：

```text
GET  /healthz
POST /v1/postgresql/query
POST /v1/milvus/search
POST /v1/milvus/query
POST /v1/milvus/get
POST /v1/milvus/describe
```

`start_all.sh` 在项目 gateway 健康后，通过 `SIQ_START_OPENSHELL_BROKERS=auto` 管理该 broker。自动模式仅在固定私有 reader 文件存在时启动或复用 broker；否则宿主 Hermes 照常启动并跳过 broker。不要让它监听 `0.0.0.0`；sandbox 访问统一走 OpenShell 既有别名 `host.openshell.internal:18793`。

## PostgreSQL 边界

broker 只接受 `sql`、`schema`、`limit`、`timeout_ms` 四类字段。请求不能选择数据库，不能提供 DSN、凭据或输出路径。

只读取以下专用环境变量：

```text
SIQ_OPENSHELL_PG_RO_HOST
SIQ_OPENSHELL_PG_RO_PORT
SIQ_OPENSHELL_PG_RO_USER
SIQ_OPENSHELL_PG_RO_PASSWORD
SIQ_OPENSHELL_PG_RO_SSLMODE        # 可选，默认 prefer
```

前四项必须同时存在。broker 不会回退到 `SIQ_APP_DATABASE_URL`、`POSTGRES_*`、`PG*`、profile `.env` 文件或 `postgres` 角色；也刻意不提供数据库环境变量。高权限或通用应用角色名会被拒绝。凭据只应保存在本机 secret 环境中，不能提交到仓库。

专用只读角色的规划、应用和验证命令如下，执行过程不会打印密码：

```bash
python3 scripts/openshell/provision_postgres_reader.py plan
python3 scripts/openshell/provision_postgres_reader.py apply \
  --confirm-role siq_openshell_reader
python3 scripts/openshell/provision_postgres_reader.py verify \
  --confirm-role siq_openshell_reader
```

生成的 `var/openshell/secrets/postgres-reader.env` 仅 owner 可读写，并已被 Git 忽略。`apply` 会写入私有 preflight 记录，回滚必须显式执行。

请求只选择 schema，broker 在服务端执行固定路由：

| Schema | 数据库 |
| --- | --- |
| `pdf2md` | `siq` |
| `pdf2md_hk` | `siq_hk` |
| `sec_us` | `siq_us` |
| `edinet_jp` | `siq_jp` |
| `dart_kr` | `siq_kr` |
| `eu_ifrs` | `siq_eu` |

六个数据库共用同一个专用只读角色和密码。请求不能携带数据库、DSN、host、port、role、password 或输出路径；adapter 每次打开连接前都会重新校验 schema 与数据库配对。

数据库角色本身也必须独立满足最小权限：

```text
允许 CONNECT 到六个固定 SIQ 数据库
允许 USAGE 到对应市场 schema
允许 SELECT 到经批准的表或视图
不允许 CREATE、DML、角色管理或宽泛函数 EXECUTE 授权
```

broker 还会强制执行单条 `SELECT`、`WITH` 或 `SHOW` 语句、SIQ 现有 schema allowlist、最多 500 行、最长 30 秒超时、只读事务、受限 search path 和外层结果 limit。`SELECT INTO`、行锁以及已知有状态或文件读取类 PostgreSQL 函数会被拒绝。

当 sandbox 配置了 broker route，Hermes 既有命令路径仍可使用：

```bash
SIQ_PG_QUERY_BROKER_URL=http://host.openshell.internal:18793 \
python agents/hermes/profiles/shared/scripts/pg_query.py \
  --profile-env /path/that/is-not-read/in/broker/mode.env \
  --schema pdf2md \
  --limit 50 \
  --timeout-ms 5000 \
  --sql 'SELECT filing_id FROM pdf2md.filings'
```

未设置 `SIQ_PG_QUERY_BROKER_URL` 时，`pg_query.py` 保留原有宿主行为。broker URL 只允许固定端口和 SIQ loopback/host-route 名称。

## Milvus 边界

API 只暴露 Search、Query、Get 和经过脱敏投影的 Describe，不暴露原始 SDK 对象，也不允许 insert、upsert、delete、drop、create、load、alter 或索引管理操作。连接设置只来自：

```text
SIQ_OPENSHELL_MILVUS_RO_HOST       # 默认 127.0.0.1
SIQ_OPENSHELL_MILVUS_RO_PORT       # 默认 19530
SIQ_OPENSHELL_MILVUS_RO_DATABASE   # 默认 default
SIQ_OPENSHELL_MILVUS_RO_USER
SIQ_OPENSHELL_MILVUS_RO_PASSWORD
SIQ_OPENSHELL_MILVUS_RO_TOKEN
```

启用 RBAC 时应使用仅具备 Search/Query 权限的 Milvus 角色。请求不能提供 Milvus 地址、数据库、凭据或路径。

当前本地 Milvus 未启用授权，因此安全结论不能表述为“匿名 Milvus 只读”。正式 sandbox policy 必须省略 `19530` 端口，只暴露本 broker。短期证明流程见 `docs/runbooks/openshell/milvus-write-protection-proof.md`。

固定集合契约覆盖 SIQ 已使用的五个市场证据集合，以及一级市场 IC 的物理/逻辑知识集合。市场集合只允许按角色返回 `vector`、`chunk_uid`、`batch_tag`、`metadata` 中的适用字段；IC 知识集合只允许返回 `vector`、`id`、`project_tag`、`metadata`。向量字段不能作为输出返回。

Agent memory 不经过这个只读 broker。一级市场与二级市场 memory 均由宿主 FastAPI 写入唯一逻辑 alias `siq_agent_memory_active`，其机器白名单位于 `infra/openshell/data-broker/memory-collections.json`。运行时配置不得直接指向物理版本集合、legacy memory 集合或知识集合；sandbox 仍不得直连 `19530`。完整边界见 `docs/runbooks/openshell/memory-write-boundary.md`。

可选表达式只允许对集合特定标量字段做一次简单比较，或使用 `in` / `not in`。布尔组合、JSON 字段遍历、函数调用和任意表达式文本都会被拒绝。Search 最多 50 条命中，标量 Query 最多 200 行。

## 请求合同

PostgreSQL query:

```json
{
  "sql": "SELECT filing_id FROM pdf2md.filings",
  "schema": "pdf2md",
  "limit": 50,
  "timeout_ms": 5000
}
```

Milvus search:

```json
{
  "collection": "siq_deal_shared",
  "vector": [0.1, 0.2, 0.3],
  "vector_field": "vector",
  "output_fields": ["metadata", "project_tag"],
  "expr": "project_tag == \"approved-project-tag\"",
  "limit": 10
}
```

Milvus scalar query:

```json
{
  "collection": "siq_hk_reports",
  "output_fields": ["chunk_uid", "metadata"],
  "expr": "batch_tag == \"approved-batch-tag\"",
  "limit": 50
}
```

未知字段按失败关闭处理。错误响应只包含稳定 `error_code`，不会回显 SQL、向量、凭据、后端异常文本或结果内容。

## 审计与运维

每次允许或拒绝的数据操作，都会在 `var/openshell/audit/` 下写入既有 `siq.openshell.audit.v1` 最小记录。记录只保存经过投影的 schema/collection 目标、decision、稳定 error code 和耗时，不保存 SQL、向量、请求体或结果体。审计写入失败时，请求按失败关闭。

可选关联配置如下：

```text
SIQ_OPENSHELL_AUDIT_PROFILE
SIQ_OPENSHELL_AUDIT_SANDBOX_ID
SIQ_OPENSHELL_AUDIT_SESSION_ID
SIQ_OPENSHELL_AUDIT_POLICY_DIGEST
```

正式 sandbox 不使用这些进程级默认值作为最终请求身份。严格模式要求 `X-SIQ-OpenShell-Identity`，并从验证通过、audience 为 `siq-read-only-data-broker` 的短期 claims 覆盖 profile、run、sandbox、session 和 policy 审计上下文。完整启停、回退和轮换流程见 `docs/runbooks/openshell/broker-request-identity.md`。

在宿主导出专用 PostgreSQL 设置后，可启动两个 broker：

```bash
scripts/openshell/start_brokers.sh
scripts/openshell/status_brokers.sh
scripts/openshell/stop_brokers.sh
```

上述默认启动保持宿主兼容；正式 sandbox 前必须在维护窗口使用 `start_brokers.sh --require-request-identity`。

生命周期命令不传递凭据参数，也不加载 dotenv 文件。PostgreSQL 与 Milvus 设置只由 broker 进程从宿主环境继承。PID 和日志文件位于 `var/openshell/brokers/`，仅 owner 可读写。HTTP access log 已关闭，避免请求或响应体进入 broker 日志。

隔离单元测试不依赖任何真实数据服务：

```bash
PYTHONPATH=. pytest -q scripts/openshell/tests/test_read_only_data_broker.py
PYTHONPATH=. pytest -q apps/api/tests/test_hermes_pg_query.py
```
