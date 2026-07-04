# HK Post-Parse Evidence Package Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 HK 解析产物处理升级为与 A 股 PDF 后处理产物结构对齐的 V2 evidence package，并完成 HK 独立 PostgreSQL 数据库 `siq_hk.pdf2md_hk` 入库管道、前端状态入口可见性、以及可重复的样本验收。结构复用 A 股的工程契约；抽取规则只按 HKEX 年报/中报的实际 PDF 产物适配。

**Architecture:** HK 构建器从 `document_full.json`、`content_list_enhanced`、`quality_report.json` 等 parser 产物生成 `data/wiki/hk/companies/<ticker>-<company>/reports/<fiscal_year>-<report_type>-<filing_key>/`。包内保留原始 parser 产物、增强 Markdown、表格索引、脚注/目录/附注关系、质量报告、财务指标与 evidence 坐标，并写入公司级 `company.json`、`_index.json` 与 `_meta/company_catalog.json`。统一 package contract 读取这些路径，API 将 HK 导入路由到 `siq_hk`，importer 将 V2 结构写入 `pdf2md_hk` 新增表。Milvus 继续使用市场级 collection 参数，HK 默认 collection 为 `siq_hk_reports`。

**Tech Stack:** Python 3.13, pytest, psycopg, PostgreSQL, existing `market-report-rules`, `market-contracts`, `apps/api`, `scripts/hk`, `db/ddl`, `db/imports`, existing evidence package validator.

## Global Constraints

- 不修改 A 股 CN 默认流程、schema、产物路径和解析结果。
- HK wiki 根目录固定为 `data/wiki/hk`，单报告包固定在 `companies/<ticker>-<company>/reports/<report_id>`；旧 `data/wiki/hk_reports` 仅作为迁移兼容路径。HK PostgreSQL 默认目标库为同一 Postgres 实例内的 `siq_hk`，schema 为 `pdf2md_hk`。
- `company_id` 使用 `HK:<5位股票代码>`，例如 `HK:00700`；`ticker`、`stock_code`、`hkex_stock_code` 均保留 `00700` 形式；公司名称、简称、别名只作为属性或搜索字段，不作为主键。
- HK 数值抽取不得由 LLM 猜测。所有 `financial_facts`、`operating_metric_facts` 和 `evidence_citations` 必须可追溯到 package 内的 page/table/row/column/quote/path/source 信息。
- HK 可以复用 A 股“PDF 表格 -> 科目 alias -> evidence 坐标 -> 入库”的工程方式，但科目 alias、报表识别、会计准则、币种/单位、双语标题和 HKEX 披露字段必须独立适配。
- 新增用户可见文档、README 和注释使用中文；代码标识符、文件路径、SQL 对象名保持英文。
- 所有新增 importer 写入逻辑必须幂等；重跑同一 `parse_run_id` 前必须清理该 run 的派生行。
- V2 包必须兼容现有 `market_evidence_package_v1` manifest schema，不破坏 US/JP/KR/EU 的 validator。
- 前端 `/parse-hk` 当前 Evidence Packages 面板不能只显示 0；如果 package 已存在，必须能看到 package 路径、quality、tables、metrics、evidence、package files。

---

## File Map

- `docs/superpowers/specs/2026-07-04-hk-post-parse-evidence-package-design.md`：已完成的中文设计说明，作为本计划的需求来源。
- `scripts/hk/hk_evidence_lib.py`：HK package 构建主逻辑；新增 V2 目录、parser 产物复制、增强 QA 文件、`report_complete.md`。
- `services/market-report-rules/tests/test_hk_evidence_package.py`：HK 构建器 TDD 测试。
- `packages/market-contracts/src/siq_market_contracts/evidence_package.py`：统一 package validator/detail reader；新增可选 V2 package path 和 detail 字段。
- `packages/market-contracts/tests/test_evidence_package.py`：contract 层测试。
- `db/ddl/020_create_pdf2md_hk_schema.sql`：HK schema 扩展；新增 V2 parser/QA/关系表与公司身份字段。
- `db/imports/import_hk_evidence_package_to_postgres.py`：HK importer；默认库、V2 文件读取、增强表入库、幂等删除。
- `db/imports/tests/test_import_hk_evidence_package.py`：importer 单元测试。
- `apps/api/services/market_report_settings.py`：市场级数据库/collection 默认配置。
- `apps/api/services/market_report_commands.py`：HK import/vector 命令参数与默认 env 注入。
- `apps/api/tests/test_market_report_commands.py`：命令构造测试。
- `apps/api/routers/market_reports.py`：package detail/quality/file 读取入口；必要时只补充返回字段，不改变路由路径。
- `apps/api/tests/test_market_reports_proxy.py`：API package detail 和 `/parse-hk` 相关后端测试。
- `scripts/hk/run_hk_v2_smoke.py`：新增 HK 5 样本 smoke 脚本，输出中文验收报告。
- `docs/superpowers/reports/`：保存 smoke 验收输出。

---

## Task 1: 锁定 HK V2 Package 产物契约

**Files:**
- `services/market-report-rules/tests/test_hk_evidence_package.py`
- `scripts/hk/hk_evidence_lib.py`

**Behavior:**
HK 构建器在现有最小包基础上输出完整 V2 结构：`parser/document_full.json`、`parser/content_list_enhanced.json`、`parser/table_relations.json`、`sections/report_complete.md`、`qa/footnotes.json`、`qa/toc.json`、`qa/financial_note_links.json`、`qa/table_quality_signals.json`，并继续保留现有 `manifest.json`、`tables/*`、`metrics/*`、`qa/source_map.json`。

**TDD Steps:**

- [ ] 扩展 `test_build_hk_evidence_package_from_parser_result` 的 fake `document_full.json`，加入 `content_list_enhanced.footnotes`、`toc`、`financial_note_links`、`quality_signals`、`tables`、`pages`。
- [ ] 增加断言：上述 V2 文件均存在。
- [ ] 增加断言：`manifest.artifact_hashes` 包含上述 V2 文件，`validate_evidence_package(package_dir).ok` 仍为 true。
- [ ] 运行测试，确认新增断言先失败。

**Implementation Steps:**

- [ ] 在 `write_hk_evidence_package()` 中创建 `parser` 目录。
- [ ] 新增 `_write_parser_artifacts(package_dir, parser_result_dir, document_full, financial_data, financial_checks)`：写入 `document_full`、`content_list_enhanced`、`table_relations`、原始 parser quality/financial 文件；缺失时写入空契约。
- [ ] 新增 `_write_report_complete(package_dir, markdown, document_full, quality)`，正文后追加“可恢复结构摘要”“目录候选”“脚注摘要”“附注关系摘要”“图片/表格摘要”。
- [ ] 新增 `_write_enhancement_qa(package_dir, document_full)`，从 `content_list_enhanced` 抽取 footnotes、toc、financial_note_links、quality_signals。
- [ ] 在计算 `artifact_hashes` 前写完所有 V2 文件。

**Code Sketch:**

```python
def _content_list_enhanced(document_full: dict[str, Any]) -> dict[str, Any]:
    enhanced = document_full.get("content_list_enhanced")
    return enhanced if isinstance(enhanced, dict) else {}


def _write_enhancement_qa(package_dir: Path, document_full: dict[str, Any]) -> None:
    enhanced = _content_list_enhanced(document_full)
    write_json(package_dir / "qa" / "footnotes.json", {
        "schema_version": "hk_footnotes_v1",
        "payload": enhanced.get("footnotes") or {"references": [], "definitions": [], "bindings": [], "summary": {}},
    })
    write_json(package_dir / "qa" / "toc.json", {
        "schema_version": "hk_toc_v1",
        "payload": enhanced.get("toc") or {"headings": [], "toc_candidates": [], "content_headings": [], "summary": {}},
    })
```

**Verification:**

```bash
cd /home/maoyd/siq-research-engine/services/market-report-rules
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_hk_evidence_package.py
```

Expected: HK package test passes and generated package validates under `market_evidence_package_v1`.

---

## Task 2: 让统一 Package Reader 展示 HK V2 文件和计数

**Files:**
- `packages/market-contracts/src/siq_market_contracts/evidence_package.py`
- `packages/market-contracts/tests/test_evidence_package.py`
- `apps/api/tests/test_market_reports_proxy.py`

**Behavior:**
`read_market_package_summary()` 和 `read_market_package_detail()` 读取 HK V2 package 时，`paths` 和 `detail` 返回新增 parser/QA 文件，前端 Evidence Packages 面板能显示非 0 的 sections/tables/raw facts/metrics/evidence，以及 V2 package files。

**TDD Steps:**

- [ ] 在 `packages/market-contracts/tests/test_evidence_package.py` 的 HK fixture 中写入 V2 文件。
- [ ] 断言 `summary["paths"]` 包含 `document_full`、`content_list_enhanced`、`report_complete`、`footnotes`、`toc`、`financial_note_links`、`table_quality_signals`。
- [ ] 断言 `detail` 包含 `parser_artifacts` 和 `qa_artifacts`，并能读取对应 JSON。
- [ ] 在 `apps/api/tests/test_market_reports_proxy.py` 增加一个 HK package detail 测试，确认 API 返回上述 `paths`。
- [ ] 运行测试，确认新增断言先失败。

**Implementation Steps:**

- [ ] 扩展 `PACKAGE_FILE_PATHS`，加入 V2 文件映射：`report_complete`、`document_full`、`content_list_enhanced`、`table_relations`、`footnotes`、`toc`、`financial_note_links`、`table_quality_signals`。
- [ ] 在 `read_market_package_detail()` 返回 `parser_artifacts` 和 `qa_artifacts`。
- [ ] 不把这些 V2 文件加入 `REQUIRED_FILES`，避免破坏 US/JP/KR/EU 旧包。

**Verification:**

```bash
cd /home/maoyd/siq-research-engine/packages/market-contracts
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_evidence_package.py

cd /home/maoyd/siq-research-engine/apps/api
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_market_reports_proxy.py::test_market_package_quality_by_path_and_filing_id
```

Expected: HK package detail includes V2 paths and existing markets remain compatible.

---

## Task 3: 扩展 `pdf2md_hk` Schema 支持 V2 结构

**Files:**
- `db/ddl/020_create_pdf2md_hk_schema.sql`
- `db/imports/tests/test_import_hk_evidence_package.py`

**Behavior:**
HK schema 支持公司身份属性、parser artifacts、content blocks、footnotes、toc、financial note links、table relations、table quality signals。所有新增表按 `parse_run_id` 可幂等清理。

**TDD Steps:**

- [ ] 在 `test_import_hk_evidence_package.py` 新增 `test_hk_ddl_contains_v2_tables_and_identity_columns`，读取 DDL 文本并断言包含 `short_name`、`stock_code`、`hkex_stock_code`、`content_blocks`、`footnotes`、`toc_entries`、`financial_note_links`、`table_relations`、`parser_artifacts`、`table_quality_signals`。
- [ ] 运行测试，确认先失败。

**Implementation Steps:**

- [ ] 在 `companies` 表增加可空字段：`stock_code text`、`hkex_stock_code text`、`short_name text`、`company_name_en text`、`company_name_zh text`、`aliases jsonb not null default '[]'::jsonb`。
- [ ] 在 `filings` 表确认已有 `stock_code`，若没有则增加。
- [ ] 新增表 `parser_artifacts`、`content_blocks`、`footnotes`、`toc_entries`、`financial_note_links`、`table_relations`、`table_quality_signals`。
- [ ] 新增表均包含 `filing_id`、`parse_run_id`、稳定主键、page/table/target 字段和 `raw jsonb`。
- [ ] 为新增表增加 `parse_run_id`、`filing_id`、`page_number`、`table_index` 常用索引。
- [ ] DDL 使用 `create table if not exists` 和 `alter table ... add column if not exists`，保证可重复执行。

**SQL Sketch:**

```sql
create table if not exists pdf2md_hk.parser_artifacts (
  parse_run_id text not null references pdf2md_hk.parse_runs(parse_run_id) on delete cascade,
  artifact_key text not null,
  local_path text not null,
  schema_version text,
  raw jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  primary key (parse_run_id, artifact_key)
);
```

**Verification:**

```bash
cd /home/maoyd/siq-research-engine/db/imports
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider tests/test_import_hk_evidence_package.py
```

Expected: DDL 文本测试通过。

---

## Task 4: HK Importer 写入 V2 表并默认使用 `siq_hk`

**Files:**
- `db/imports/import_hk_evidence_package_to_postgres.py`
- `db/imports/tests/test_import_hk_evidence_package.py`

**Behavior:**
`import_hk_evidence_package_to_postgres.py` 默认连接 `siq_hk`，除非显式传入 `--database-url` 或环境变量覆盖。导入 V2 package 时写入新增表，并且同一 `parse_run_id` 重跑不会重复数据。

**TDD Steps:**

- [ ] 新增 `test_hk_database_url_defaults_to_siq_hk`，清空 `DATABASE_URL`、`SIQ_PGDATABASE`、`PGDATABASE` 后断言 URL 以 `/siq_hk` 结尾。
- [ ] 新增 fake connection，记录 `execute(sql, params)` 调用。
- [ ] 新增 `test_delete_run_rows_includes_v2_tables`，断言删除表包含新增 V2 表。
- [ ] 新增 `test_import_v2_artifacts_writes_parser_and_qa_tables`，构造 tmp package，调用 V2 insert 函数，断言 SQL 触达对应表。
- [ ] 运行测试，确认先失败。

**Implementation Steps:**

- [ ] 修改 `database_url()` 默认数据库优先级：`SIQ_HK_PGDATABASE` -> `SIQ_PGDATABASE` -> `PGDATABASE` -> `siq_hk`。
- [ ] `_upsert_company()` 写入 `stock_code`、`hkex_stock_code`、`short_name`、`company_name_en`、`company_name_zh`、`aliases`。
- [ ] `_delete_run_rows()` 增加 `table_quality_signals`、`table_relations`、`financial_note_links`、`toc_entries`、`footnotes`、`content_blocks`、`parser_artifacts`。
- [ ] 在 `import_package()` 中 `_insert_artifacts()` 后追加 `_insert_parser_artifacts()`、`_insert_content_blocks()`、`_insert_footnotes()`、`_insert_toc_entries()`、`_insert_financial_note_links()`、`_insert_table_relations()`、`_insert_table_quality_signals()`。
- [ ] 各插入函数从 package 文件读取 JSON，空文件或缺失文件时直接跳过。
- [ ] 所有主键使用 `stable_id(parse_run_id, artifact_key, page_number, table_index, target_id, row_index)`，避免不同 run 的同名脚注互相覆盖。

**Code Sketch:**

```python
def database_url(explicit: str | None) -> str:
    url = explicit or os.environ.get("DATABASE_URL")
    if url:
        return url.replace("postgresql+psycopg://", "postgresql://")
    db = (
        os.environ.get("SIQ_HK_PGDATABASE")
        or os.environ.get("SIQ_PGDATABASE")
        or os.environ.get("PGDATABASE")
        or "siq_hk"
    )
    host = os.environ.get("SIQ_PGHOST") or os.environ.get("PGHOST") or "127.0.0.1"
    port = os.environ.get("SIQ_PGPORT") or os.environ.get("PGPORT") or "15432"
    user = os.environ.get("SIQ_PGUSER") or os.environ.get("PGUSER") or "postgres"
    password = os.environ.get("SIQ_PGPASSWORD") or os.environ.get("PGPASSWORD") or ""
    auth = f"{user}:{password}" if password else user
    return f"postgresql://{auth}@{host}:{port}/{db}"
```

**Verification:**

```bash
cd /home/maoyd/siq-research-engine/db/imports
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider tests/test_import_hk_evidence_package.py
```

Expected: importer 单元测试通过，默认 DB 指向 `siq_hk`。

---

## Task 5: API 命令层注入 HK 数据库和 Milvus 默认 collection

**Files:**
- `apps/api/services/market_report_settings.py`
- `apps/api/services/market_report_commands.py`
- `apps/api/tests/test_market_report_commands.py`
- `apps/api/tests/test_market_report_settings.py`

**Behavior:**
从 `/parse-hk` 触发 PostgreSQL 导入时，如果用户没有传 `database_url`，API 给 HK importer 注入 `SIQ_HK_PGDATABASE=siq_hk`。Milvus dry run/ingest 若未指定 collection，HK 使用 `siq_hk_reports`。

**TDD Steps:**

- [ ] 在 `test_market_report_settings.py` 断言 `MARKET_DATABASES["HK"] == "siq_hk"`，`MARKET_VECTOR_COLLECTIONS["HK"] == "siq_hk_reports"`。
- [ ] 在 `test_market_report_commands.py` 新增 `test_market_package_import_env_defaults_hk_database`，断言 HK import plan/env 包含 `SIQ_HK_PGDATABASE=siq_hk`，US 不受影响。
- [ ] 新增 `test_market_vector_ingest_args_defaults_hk_collection`，HK payload 不传 collection 时 args 含 `--collection siq_hk_reports`。
- [ ] 运行测试，确认先失败。

**Implementation Steps:**

- [ ] 在 `market_report_settings.py` 新增 `MARKET_DATABASES` 和 `MARKET_VECTOR_COLLECTIONS`，默认值分别包含 `HK: siq_hk` 和 `HK: siq_hk_reports`。
- [ ] 在 `market_report_commands.py` 增加纯函数 `market_package_import_env(market, market_databases, base_env=None)`，返回可传给 runner 的 env overlay。
- [ ] 如果当前 command runner 没有 env overlay 能力，则在调用处传入 `env={**os.environ, **market_package_import_env(market, market_databases, base_env)}`，不要把数据库写死在命令行参数中。
- [ ] 更新 `market_vector_ingest_args()`：payload 未传 `collection` 时按市场默认 collection 注入；保留用户显式传入的 collection 优先级。

**Verification:**

```bash
cd /home/maoyd/siq-research-engine/apps/api
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_market_report_settings.py tests/test_market_report_commands.py
```

Expected: 命令层测试通过，HK 默认导入库与 collection 可由环境覆盖。

---

## Task 6: HK 前端状态入口展示 V2 Package 内容

**Files:**
- `apps/api/routers/market_reports.py`
- `apps/api/tests/test_market_reports_proxy.py`
- 前端相关文件通过搜索确认后再改，优先检查包含 `HK Evidence Packages` 或 `Package Files` 的组件。

**Behavior:**
`https://arthurmao.synology.me:9391/parse-hk` 的 Evidence Packages 区域选择 package 后，不再只显示 0 和空文件；应展示 sections/tables/raw facts/metrics/evidence 数量、quality JSON、package files 中的 V2 路径，以及 parser/QA artifacts。

**TDD Steps:**

- [ ] 后端先加 `test_market_package_detail_returns_hk_v2_paths`：构造 HK V2 package，调用 package detail endpoint/函数，断言返回 `paths.document_full`、`paths.report_complete`、`paths.footnotes`。
- [ ] 如果前端已有测试框架，增加一个组件测试，mock package detail 响应后断言文件列表出现 `parser/document_full.json` 和 `sections/report_complete.md`。
- [ ] 若前端无现成测试，保留后端测试，并在 Task 8 用浏览器验收覆盖 UI。

**Implementation Steps:**

- [ ] 确认 `market_reports.py` 的 detail/quality/file endpoint 使用 `read_market_package_detail()`，若只返回部分字段，补充 `paths`、`parser_artifacts`、`qa_artifacts`。
- [ ] 搜索前端组件：

```bash
cd /home/maoyd/siq-research-engine
grep -RIn "HK Evidence Packages\|Package Files\|Milvus Dry Run\|Build Package" apps packages frontend 2>/dev/null | head -n 80
```

- [ ] 若组件只渲染固定 `PACKAGE_FILE_PATHS`，改为遍历后端返回的 `paths`，按 `manifest/quality/source/financial/parser/qa/sections/tables` 分组。
- [ ] 保持 `/parse-hk` 视觉和其他市场页一致，不引入新的说明性大段文本。

**Verification:**

```bash
cd /home/maoyd/siq-research-engine/apps/api
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_market_reports_proxy.py
```

Expected: HK package detail 后端返回 V2 文件，前端能够消费动态 paths。

---

## Task 7: 建立 HK 5 样本 Smoke 验收脚本

**Files:**
- `scripts/hk/run_hk_v2_smoke.py`
- `docs/superpowers/reports/`

**Behavior:**
对现有 `data/wiki/hk/companies/*/reports/*` 中 5 个代表性 package 做结构、质量、导入 dry run 检查，并输出中文报告。首批样本对应：

- `data/wiki/hk/companies/00700-*/reports/2025-annual-12100024`
- `data/wiki/hk/companies/01299-*/reports/2025-annual-12106543`
- `data/wiki/hk/companies/00981-*/reports/2025-annual-12097338`
- `data/wiki/hk/companies/03988-*/reports/2025-annual-12132549`
- `data/wiki/hk/companies/09988-*/reports/2025-annual-11727038`

**TDD Steps:**

- [ ] 新增脚本级测试可选；若仓库没有脚本测试模式，则用 CLI dry run 验收。
- [ ] 脚本必须返回非 0 当任一样本缺失必需 V2 文件、validator 失败、metrics/evidence 全空、或 package detail 无 V2 paths。

**Implementation Steps:**

- [ ] 脚本参数：`--root data/wiki/hk`、`--output docs/superpowers/reports/hk_v2_smoke_report.md`、`--json-output docs/superpowers/reports/hk_v2_smoke_report.json`。
- [ ] 对每个样本读取 `manifest.json`、`qa/quality_report.json`、`tables/table_index.json`、`metrics/normalized_metrics.json`、`qa/source_map.json`。
- [ ] 输出每个样本：公司、ticker、filing_id、quality、sections、tables、metrics、evidence、缺失文件、主要 warnings。
- [ ] 输出聚合结论：通过/警告/失败、下一步需要补的 HK alias 或表格规则。

**Verification:**

```bash
cd /home/maoyd/siq-research-engine
PYTHONDONTWRITEBYTECODE=1 python3 scripts/hk/run_hk_v2_smoke.py \
  --root data/wiki/hk \
  --output docs/superpowers/reports/hk_v2_smoke_report.md \
  --json-output docs/superpowers/reports/hk_v2_smoke_report.json
```

Expected: 生成中文 smoke 报告；若当前 package 尚未重建为 V2，报告明确列出缺失 V2 文件，而不是静默通过。

---

## Task 8: 端到端验收与数据库实测

**Files:**
- 前述所有修改文件
- `docs/superpowers/reports/hk_v2_smoke_report.md`
- `docs/superpowers/reports/hk_v2_smoke_report.json`

**Behavior:**
重建至少一个 HK package，导入 `siq_hk.pdf2md_hk`，确认 PostgreSQL 行数、package contract、API detail、Milvus dry run 均可用。

**Steps:**

- [ ] 选择已有 parser_result 和 source PDF 对应的 `00700/2025/annual_12100024`。若原始 PDF/parser_result 路径只在 manifest 中保存，先读取 manifest 的 `parser_result_dir` 和 `local_source_path`。
- [ ] 重建 package：

```bash
cd /home/maoyd/siq-research-engine
PYTHONDONTWRITEBYTECODE=1 python3 scripts/hk/build_hk_evidence_package.py \
  <source_pdf_path> \
  --parser-result <parser_result_dir> \
  --metadata <metadata_path> \
  --output-root data/wiki/hk \
  --force
```

- [ ] 运行 package validator：

```bash
cd /home/maoyd/siq-research-engine/packages/market-contracts
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python - <<'PY'
from pathlib import Path
from siq_market_contracts.evidence_package import validate_evidence_package, read_market_package_detail
p = next(Path('/home/maoyd/siq-research-engine/data/wiki/hk/companies').glob('00700-*/reports/2025-annual-12100024'))
result = validate_evidence_package(p)
print(result.ok, result.errors)
print(read_market_package_detail(p)['paths'])
PY
```

- [ ] 执行 DDL 和导入：

```bash
cd /home/maoyd/siq-research-engine
SIQ_HK_PGDATABASE=siq_hk PYTHONDONTWRITEBYTECODE=1 python3 db/imports/import_hk_evidence_package_to_postgres.py \
  data/wiki/hk/companies/00700-TENCENT/reports/2025-annual-12100024 \
  --ddl
```

- [ ] 用容器内 `psql` 验证行数：

```bash
docker exec docker-postgres-1 psql -U postgres -d siq_hk -c "
select 'companies' table_name, count(*) from pdf2md_hk.companies
union all select 'filings', count(*) from pdf2md_hk.filings
union all select 'parse_runs', count(*) from pdf2md_hk.parse_runs
union all select 'pdf_tables', count(*) from pdf2md_hk.pdf_tables
union all select 'financial_facts', count(*) from pdf2md_hk.financial_facts
union all select 'evidence_citations', count(*) from pdf2md_hk.evidence_citations
union all select 'parser_artifacts', count(*) from pdf2md_hk.parser_artifacts
union all select 'footnotes', count(*) from pdf2md_hk.footnotes
union all select 'toc_entries', count(*) from pdf2md_hk.toc_entries
union all select 'financial_note_links', count(*) from pdf2md_hk.financial_note_links;
"
```

- [ ] Milvus dry run：

```bash
cd /home/maoyd/siq-research-engine
PYTHONDONTWRITEBYTECODE=1 python3 scripts/vector-index/milvus-ingestion/ingest_market_evidence_chunks.py \
  --package data/wiki/hk/companies/00700-TENCENT/reports/2025-annual-12100024 \
  --batch-tag hk-v2-smoke \
  --collection siq_hk_reports \
  --dry-run
```

- [ ] 浏览器打开 `https://arthurmao.synology.me:9391/parse-hk`，选择 `00700/2025/annual_12100024`，确认 Package Files 和 counts 可见。

**Verification:**

```bash
cd /home/maoyd/siq-research-engine/services/market-report-rules
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_hk_evidence_package.py

cd /home/maoyd/siq-research-engine/packages/market-contracts
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_evidence_package.py

cd /home/maoyd/siq-research-engine/db/imports
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider tests/test_import_hk_evidence_package.py

cd /home/maoyd/siq-research-engine/apps/api
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_market_report_settings.py \
  tests/test_market_report_commands.py \
  tests/test_market_reports_proxy.py
```

Expected: 所有相关测试通过；`siq_hk.pdf2md_hk` 有 HK package 数据；`/parse-hk` 可见 V2 package 状态；Milvus dry run 输出 chunk 计划但不写入生产 collection。

---

## Commit Strategy

- 一个实现提交：`feat: add hk v2 evidence package pipeline`
- 若改动过大，可拆为：
  - `feat: write hk v2 evidence package artifacts`
  - `feat: import hk v2 evidence package to siq_hk`
  - `feat: expose hk v2 package status`
- 提交前必须运行 Task 8 的测试命令；无法运行的命令需要在最终说明中写清原因。

## Rollback Plan

- Package V2 新增文件均为附加文件，不删除旧 `manifest.json`、`metrics/*`、`qa/source_map.json`、`tables/table_index.json`，旧 reader 可继续读取。
- DB DDL 使用 `if not exists`/`add column if not exists`，新增表可停用但不影响旧表查询。
- API 默认数据库通过环境变量覆盖，若线上需要临时回退，可设置 `SIQ_HK_PGDATABASE=siq`。
- Milvus collection 由 payload 或环境变量覆盖，dry run 默认保持安全。
