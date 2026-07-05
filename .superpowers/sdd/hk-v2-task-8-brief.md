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
  --output-root data/wiki/hk_reports \
  --force
```

- [ ] 运行 package validator：

```bash
cd /home/maoyd/siq-research-engine/packages/market-contracts
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python - <<'PY'
from pathlib import Path
from siq_market_contracts.evidence_package import validate_evidence_package, read_market_package_detail
p = Path('/home/maoyd/siq-research-engine/data/wiki/hk_reports/00700/2025/annual_12100024')
result = validate_evidence_package(p)
print(result.ok, result.errors)
print(read_market_package_detail(p)['paths'])
PY
```

- [ ] 执行 DDL 和导入：

```bash
cd /home/maoyd/siq-research-engine
SIQ_HK_PGDATABASE=siq_hk PYTHONDONTWRITEBYTECODE=1 python3 db/imports/import_hk_evidence_package_to_postgres.py \
  data/wiki/hk_reports/00700/2025/annual_12100024 \
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
  --package data/wiki/hk_reports/00700/2025/annual_12100024 \
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
