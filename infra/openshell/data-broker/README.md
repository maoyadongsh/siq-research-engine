# SIQ Read-Only Data Broker

`scripts/openshell/read_only_data_broker.py` is the narrow host-side data boundary for
OpenShell sandboxes. It listens on `127.0.0.1:18793` by default. Its explicit bridge
mode binds only to the verified gateway of Docker network `siq-openshell-dev`; it
cannot bind an arbitrary private address. It exposes only these routes:

```text
GET  /healthz
POST /v1/postgresql/query
POST /v1/milvus/search
POST /v1/milvus/query
POST /v1/milvus/get
POST /v1/milvus/describe
```

`start_all.sh` manages it through `SIQ_START_OPENSHELL_BROKERS=auto` after the project
gateway is healthy. Auto mode starts or reuses the brokers only when the fixed private
reader file exists; otherwise host Hermes starts normally and brokers are skipped. Do
not make it reachable on `0.0.0.0`; sandbox access uses the single existing OpenShell
alias `host.openshell.internal:18793`.

## PostgreSQL boundary

The broker accepts only `sql`, `schema`, `limit`, and `timeout_ms`. A request cannot
select a database, provide a DSN, provide credentials, or name an output path.

Only the following dedicated environment variables are read:

```text
SIQ_OPENSHELL_PG_RO_HOST
SIQ_OPENSHELL_PG_RO_PORT
SIQ_OPENSHELL_PG_RO_USER
SIQ_OPENSHELL_PG_RO_PASSWORD
SIQ_OPENSHELL_PG_RO_SSLMODE        # optional, default: prefer
```

All four non-optional values must be present. The broker never falls back to
`SIQ_APP_DATABASE_URL`, `POSTGRES_*`, `PG*`, profile `.env` files, or the `postgres`
role. There is deliberately no database environment variable. Privileged/common
application role names are rejected. Keep the values in the local secret environment;
never commit them.

Provision and verify the dedicated role without printing its password:

```bash
python3 scripts/openshell/provision_postgres_reader.py plan
python3 scripts/openshell/provision_postgres_reader.py apply \
  --confirm-role siq_openshell_reader
python3 scripts/openshell/provision_postgres_reader.py verify \
  --confirm-role siq_openshell_reader
```

The generated `var/openshell/secrets/postgres-reader.env` is owner-only and Git
ignored. Apply writes a private preflight record and rollback is explicit.

The request selects a schema, and the broker performs this fixed server-side routing:

| Schema | Database |
| --- | --- |
| `pdf2md` | `siq` |
| `pdf2md_hk` | `siq_hk` |
| `sec_us` | `siq_us` |
| `edinet_jp` | `siq_jp` |
| `dart_kr` | `siq_kr` |
| `eu_ifrs` | `siq_eu` |

The same dedicated read-only role and password are used across these six databases.
Requests cannot supply a database, DSN, host, port, role, password, or output path.
The adapter rechecks the schema/database pair before opening each connection.

The database role must independently enforce least privilege:

```text
CONNECT on the six fixed SIQ databases
USAGE on the matching market schema in each database
SELECT on approved tables/views
no CREATE, DML, role management, or broad function EXECUTE grants
```

The broker also enforces one `SELECT`, `WITH`, or `SHOW` statement, the existing SIQ
schema allowlist, a maximum of 500 rows, a maximum 30 second timeout, a read-only
transaction, a restricted search path, and an outer result limit. `SELECT INTO`, row
locking, and known stateful/file-reading PostgreSQL functions are rejected.

When the sandbox has a broker route, the existing Hermes command path remains valid:

```bash
SIQ_PG_QUERY_BROKER_URL=http://host.openshell.internal:18793 \
python agents/hermes/profiles/shared/scripts/pg_query.py \
  --profile-env /path/that/is-not-read/in/broker/mode.env \
  --schema pdf2md \
  --limit 50 \
  --timeout-ms 5000 \
  --sql 'SELECT filing_id FROM pdf2md.filings'
```

With `SIQ_PG_QUERY_BROKER_URL` unset, `pg_query.py` retains its existing host behavior.
The broker URL is restricted to the fixed port and SIQ loopback/host-route names.

## Milvus boundary

The API exposes only Search, Query, Get and a sanitized Describe projection. It exposes
no raw SDK object and no insert, upsert, delete, drop, create, load, alter, or
index-management operation. Connection settings come only from:

```text
SIQ_OPENSHELL_MILVUS_RO_HOST       # default: 127.0.0.1
SIQ_OPENSHELL_MILVUS_RO_PORT       # default: 19530
SIQ_OPENSHELL_MILVUS_RO_DATABASE   # default: default
SIQ_OPENSHELL_MILVUS_RO_USER
SIQ_OPENSHELL_MILVUS_RO_PASSWORD
SIQ_OPENSHELL_MILVUS_RO_TOKEN
```

Use a Milvus role with Search/Query privileges only when RBAC is enabled. The request
cannot supply a Milvus address, database, credential, or path.

The current local Milvus has authorization disabled, so the security claim is not
"anonymous Milvus is read-only". Formal sandbox policy must omit port `19530` and expose
only this broker. The short-lived proof procedure is documented in
`docs/runbooks/openshell/milvus-write-protection-proof.md`.

The fixed collection contract contains the five market evidence collections and the
physical/logical IC knowledge collections already used by SIQ. Market collections
allow only `vector`, `chunk_uid`, `batch_tag`, and `metadata` in their applicable
roles. IC knowledge collections allow only `vector`, `id`, `project_tag`, and
`metadata`. Vector fields cannot be returned as output.

Agent memory 不经过这个只读 broker。一级市场和二级市场 memory 均由宿主
FastAPI 写入唯一逻辑 alias `siq_agent_memory_active`，其机器白名单位于
`infra/openshell/data-broker/memory-collections.json`。运行时配置不得直接指向
物理版本集合、legacy memory 集合或知识集合；sandbox 仍不得直连 `19530`。
完整边界见 `docs/runbooks/openshell/memory-write-boundary.md`。

An optional expression is limited to one simple comparison or `in`/`not in` operation
on a collection-specific scalar field. Boolean composition, JSON field traversal,
function calls, and arbitrary expression text are rejected. Search is capped at 50
hits and scalar query at 200 rows.

## Request contracts

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

Unknown fields fail closed. Error responses contain a stable `error_code` and never
echo SQL, vectors, credentials, backend exception text, or result content.

## Audit and operation

Each allowed or denied data operation writes the existing
`siq.openshell.audit.v1` minimal record under `var/openshell/audit/`. The record stores
only a projected schema/collection target, decision, stable error code, and duration.
It does not store SQL, vectors, request bodies, or result bodies. Audit write failure
fails the request closed.

Optional correlation settings are:

```text
SIQ_OPENSHELL_AUDIT_PROFILE
SIQ_OPENSHELL_AUDIT_SANDBOX_ID
SIQ_OPENSHELL_AUDIT_SESSION_ID
SIQ_OPENSHELL_AUDIT_POLICY_DIGEST
```

正式 sandbox 不使用这些进程级默认值作为最终请求身份。严格模式要求
`X-SIQ-OpenShell-Identity`，并从验证通过、audience 为
`siq-read-only-data-broker` 的短期 claims 覆盖 profile/run/sandbox/session/policy
审计上下文。完整启停、回退和轮换流程见
`docs/runbooks/openshell/broker-request-identity.md`。

Start both brokers after exporting the dedicated PostgreSQL settings on the host:

```bash
scripts/openshell/start_brokers.sh
scripts/openshell/status_brokers.sh
scripts/openshell/stop_brokers.sh
```

上述默认启动保持宿主兼容；正式 sandbox 前必须在维护窗口使用
`start_brokers.sh --require-request-identity`。

The lifecycle passes no credential argument and loads no dotenv file. PostgreSQL and
Milvus settings are inherited by the broker processes from the host environment only.
PID and log files are owner-only under `var/openshell/brokers/`. HTTP access logging is
disabled so request and response bodies cannot enter broker logs.

Run the isolated unit tests without any live data service:

```bash
PYTHONPATH=. pytest -q scripts/openshell/tests/test_read_only_data_broker.py
PYTHONPATH=. pytest -q apps/api/tests/test_hermes_pg_query.py
```
