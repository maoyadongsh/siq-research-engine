# 数据恢复操作说明

本文记录 SIQ Research Engine 从本地备份或旧项目只读对照恢复数据的推荐路径。恢复目标默认放在本仓库的 `data/` 或由环境变量指向的外部挂载目录。

## 恢复原则

- 优先恢复到 `data/*`，不要把运行态数据写回源码目录。
- 优先使用逻辑导出、manifest 和可重复脚本，谨慎使用原始容器快照。
- 恢复后通过 `SIQ_*` 环境变量指向新位置。
- 大体量数据、数据库文件、上传 PDF、缓存和日志继续保持 Git 忽略。

## Wiki

默认位置：

```text
data/wiki
```

恢复后设置：

```bash
export SIQ_WIKI_ROOT=/home/maoyd/siq-research-engine/data/wiki
```

如果从旧只读来源恢复成本地副本：

```bash
cd /home/maoyd/siq-research-engine
mkdir -p data/wiki
rsync -a /path/to/old/wiki/ data/wiki/
export SIQ_WIKI_ROOT=/home/maoyd/siq-research-engine/data/wiki
```

## PDF 解析运行态

推荐恢复目标：

```text
data/pdf-parser/
```

常见子目录：

```text
uploads/
results/
output/
db/tasks.db
cache/financial_llm/
logs/
workflow_jobs.json
```

恢复后设置：

```bash
export SIQ_PDF2MD_DATA_DIR=/home/maoyd/siq-research-engine/data/pdf-parser
```

单项目录也可通过 `SIQ_PDF_UPLOADS_ROOT`、`SIQ_PDF_RESULTS_ROOT`、`SIQ_PDF_OUTPUT_ROOT`、`SIQ_PDF_TASK_DB_PATH` 覆盖。

## 公告下载文件

默认位置：

```text
data/market-report-finder/downloads
```

API 通过以下变量定位：

```bash
export SIQ_REPORT_FINDER_ROOT=/home/maoyd/siq-research-engine/services/market-report-finder
export SIQ_REPORT_DOWNLOADS_ROOT=/home/maoyd/siq-research-engine/data/market-report-finder/downloads
```

## PostgreSQL

`scripts/ops/backup.sh` 默认按容器初始化契约分别导出以下业务数据库：

```text
siq_app
siq_document_parser
siq_us
siq_hk
siq_jp
siq_kr
siq_eu
```

设置管理连接后执行备份：

```bash
set -a
source /run/secrets/siq/backup.env
set +a
export SIQ_BACKUP_DIR=/path/outside/repository/siq
export SIQ_BACKUP_MODE=required
export SIQ_BACKUP_SKIP_LARGE=0
./scripts/ops/backup.sh
```

required/release 模式先完成全量预检，再创建时间戳目录。缺数据库连接、命令、schema authority、任一文件目标，或设置 `SIQ_BACKUP_SKIP_LARGE=1` 时，会在任何 `pg_dump`/`tar` 前失败，不留下可误认成有效备份的半成品目录。

每次成功运行生成独立时间戳目录，其中 `postgres/<database>.sql.gz` 是逐库逻辑导出，`postgres/<database>.schema.sql.gz` 是同库 schema 快照。发布级全备份还必须包含 `backend-data.tar.gz`、`pdf-parser-data.tar.gz`、`wiki.tar.gz`、`report-downloads.tar.gz` 和 `hermes-home.tar.gz`。最后一个对象归档完整 `SIQ_HERMES_HOME`，包括 profiles、session/checkpoint、状态库和配置；不会再单独重复归档 `profiles`。Hermes home 可能包含认证材料，备份目录必须保持 `0700`、对象保持 `0600`，并存放在受控的仓库外介质。

`manifest.txt` 记录固定 schema contract、每库 migration/DDL authority SHA-256、schema 快照映射，以及五个文件归档对象的状态和精确尺寸；`checksums.sha256` 覆盖本次全部备份文件。恢复前必须先校验：

```bash
cd /path/outside/repository/siq/<timestamp>
sha256sum --check checksums.sha256
```

旧备份中如有 PostgreSQL 逻辑导出，应优先使用：

```text
/path/to/postgres/exports
```

不要直接覆盖现有业务库。先使用自动创建并自动删除临时数据库的恢复冒烟：

```bash
set -a
source /run/secrets/siq/restore-single.env
set +a
export SIQ_RESTORE_SMOKE=1
export SIQ_RESTORE_SMOKE_SOURCE=/path/to/backup/postgres/siq_us.sql.gz
export SIQ_RESTORE_SMOKE_CHECKSUM_MANIFEST=/path/to/backup/checksums.sha256
export SIQ_RESTORE_SMOKE_EXPECTED_SCHEMA_SNAPSHOT=/path/to/backup/postgres/siq_us.schema.sql.gz
export SIQ_RESTORE_SMOKE_COMPATIBILITY_MIGRATION=/path/to/checkout/db/ddl/010_create_sec_us_schema.sql
export SIQ_RESTORE_SMOKE_EXPECTED_RELATIONS='sec_us.filings,sec_us.financial_facts'
export SIQ_RESTORE_SMOKE_AGENT_VIEW='sec_us.v_agent_financial_facts'
./scripts/ops/restore_smoke.sh
```

脚本只在 `SIQ_RESTORE_SMOKE=1` 时运行，并创建名称以 `siq_restore_smoke_` 开头的临时数据库。配置 schema 快照后，它会重新导出恢复库 schema 并与备份快照比较；配置兼容 migration 后，它会通过 `ON_ERROR_STOP=1` 在 `BEGIN ... ROLLBACK` 中真实执行当前 additive migration/DDL。完成这些检查、relation 和 Agent view 查询后通过 `trap` 自动删除。未提供开关或必要配置时不会创建、覆盖或删除任何数据库。

发布前应对同一备份批次的七个数据库运行批量恢复矩阵，而不是逐库手工拼接参数：

```bash
set -a
source /run/secrets/siq/restore-matrix.env
set +a
python3 scripts/ops/run_restore_matrix.py \
  --backup-dir /path/outside/repository/siq/<timestamp> \
  --output artifacts/operations/restore-matrix.json \
  --markdown artifacts/operations/restore-matrix.md
```

批量器只接受 `manifest.txt` 中 `backup_mode=required|release` 且 `skip_large=0` 的发布级全备份；缺少这些字段、optional/development 备份或跳过大目录的备份会在创建临时数据库前被拒绝。它还会在创建任何临时库之前强制校验五个文件归档各有且只有一条 `status=ok` manifest 记录、声明尺寸与非空文件一致、checksum 条目唯一且摘要匹配。数据库集合和顺序必须精确匹配初始化契约，七个 dump、七个 schema 快照、schema contract 版本和每库 authority digest 必须来自同一备份批次。

每库恢复后先比较实际 schema 与同批快照，再在第二个隔离 schema 数据库上按顺序真实应用当前完整 authority 链，并与未变更的主恢复库重新比较。`siq_app` 必须应用 `apps/api/migrations/*.sql` 的完整有序链，其余库应用各自 checked-in DDL；authority 应用后出现任何 schema 变化都表示备份落后于当前 authority，并以 `backup_schema_behind_authority` 阻断。五个市场库还必须存在可查询且非空的 Agent view；`siq_app` 和 `siq_document_parser` 验证关键业务 relation 可查询。`siq_app` 会先认证完整的数据库域外声纹删除 tombstone 链，并将实际条数和 head HMAC 与外部 checkpoint 精确比较；只有 checkpoint 匹配才会 replay，replay 后还会再次比较以拒绝执行期间发生漂移的 ledger。任一文件归档、dump/schema 快照缺失、authority 漂移或不收敛、checksum 不匹配、relation 缺失、市场 Agent view 为空、tombstone checkpoint/完整性失败或单库恢复失败，整份矩阵均失败。报告不记录管理连接、备份绝对路径、profile 标识或数据库内容。

`/run/secrets/siq/restore-matrix.env` 由 secret manager 临时渲染，至少包含 `SIQ_RESTORE_MATRIX_ADMIN_URL`、`SIQ_MEETING_VOICEPRINT_TOMBSTONE_PATH`、`SIQ_MEETING_VOICEPRINT_TOMBSTONE_HMAC_KEY`、`SIQ_MEETING_VOICEPRINT_TOMBSTONE_EXPECTED_COUNT`、`SIQ_MEETING_VOICEPRINT_TOMBSTONE_EXPECTED_HEAD_HMAC` 和 `SIQ_RESTORE_MATRIX_VOICEPRINT_TOMBSTONE_REQUIRED=1`。expected count 是完整 append-only 链的行数，不是去重后的 profile 数；expected head 是最后一条记录的 `hmac`，空链必须配置为 count `0` 和 64 个 `0`。checkpoint 必须由独立外部控制面保存并与 ledger 的持久化更新协调，不能从待验 ledger 临时自算，否则不能防止旧的有效前缀被接受。

tombstone ledger 必须独立于数据库备份保存，目录权限为 `0700`、文件权限为 `0600`；执行完成后删除临时环境文件，不应写入 shell history 或仓库。矩阵 JSON 会记录 checkpoint 摘要，并计算同时包含 backup ID 和 checkpoint 摘要的 binding SHA-256，从而使恢复证据明确绑定到本批备份和本次外部删除状态。开发或单独运行的非 required reconciler 不强制 checkpoint；正式矩阵始终 fail-closed。

冒烟通过后，再按变更审批和维护窗口恢复正式目标库。恢复完成后设置应用连接：

```bash
set -a
source /run/secrets/siq/application-database.env
set +a
```

原始 PostgreSQL 容器数据只作为最后恢复手段，不应直接提交或移动到源码目录。

## 发布证据门禁

生产等价 `offline-postgres` 发布门禁固定执行 strict final-v5 market ingestion、Agent memory/IC 向量实时探针、当前性能报告和版本化 before/current 比较。必须从只读外部证据存储提供 `SIQ_PERFORMANCE_BASELINE_REPORT`；该 JSON 的 mode、repeat 和 benchmark 集必须与 current 报告兼容。默认相对预算由比较器维护：关键 contract P95 为 5%，其余端到端和业务 P95 为 10%，召回率和 MRR 不得下降。

```bash
export SIQ_AGENT_MEMORY_VECTOR_PROBES_REQUIRED=1
export SIQ_AGENT_MEMORY_VECTOR_PROBES_SKIP=0
export SIQ_PERFORMANCE_COMPARISON_REQUIRED=1
export SIQ_PERFORMANCE_BASELINE_REPORT=/approved/evidence/performance/v2026-07-12/nightly.json
export SIQ_MARKET_INGESTION_EVIDENCE_PROFILE=final-v5-staging
bash scripts/ops/run_market_postgres_release_gate.sh --mode offline-postgres
```

`market_ingestion_eval_report.json`、`performance_baseline_nightly.json`、`performance-comparison.json` 和向量 preflight 均为 release manifest 的 required artifacts。缺 baseline、缺 embedding/Milvus、strict ingestion 存在 missing/fail、向量 probe 未真实执行，或相对性能/召回回退时，发布保持失败。

五市场 fixture contamination audit 同样是独立 required artifact。它固定以只读事务连接 `siq_hk`、`siq_jp`、`siq_kr`、`siq_eu`、`siq_us`，任何 exact legacy signature、非精确可疑行、数据库身份错误或查询错误都会阻断发布。审计报告中的 cleanup plan 固定 `execute=false`；当前 backup/restore 能提供恢复点和临时验证环境，但不能代替关联表依赖快照、逐行授权、受控 retirement 和提交后对账，因此本流程不提供通用自动删除 executor。清理必须作为独立维护窗口变更审批，不能与 HK staging retirement 合并或由 release wrapper 触发。

## Milvus 和 MinIO

旧备份中如有 Milvus 和 MinIO 快照，优先视为离线恢复资产：

```text
/path/to/milvus
/path/to/minio
```

这两类数据优先视为快照资产。正式恢复前需要确认版本、容器参数、volume 挂载路径和数据一致性。没有经过验证的恢复流程前，不应把它们作为日常开发必需路径。

## Hermes 运行态

默认位置：

```text
data/hermes/home
```

恢复后设置：

```bash
export SIQ_HERMES_HOME=/home/maoyd/siq-research-engine/data/hermes/home
export SIQ_HERMES_PROFILES_ROOT=$SIQ_HERMES_HOME/profiles
```

profile 名称迁移期保持兼容，例如 `siq_assistant`、`siq_analysis`、`siq_factchecker`、`siq_tracking`、`siq_legal`。

## 验证清单

```bash
test -d "$SIQ_WIKI_ROOT/companies"
test -d "$SIQ_PDF2MD_DATA_DIR"
curl -s http://localhost:18081/health
curl -s http://localhost:15000/api/ready
```

若恢复 PostgreSQL，再运行：

```bash
sha256sum --check /path/to/backup/checksums.sha256
./scripts/ops/restore_smoke.sh
python3 db/imports/import_document_full_to_postgres.py --help
python3 db/imports/test_financial_query_api_cases.py
```

数据库测试需要有效的 `DATABASE_URL` 或 libpq 环境变量。
