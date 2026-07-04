# 韩国市场 PDF Wiki 管线设计

## 状态

已确认方向：第一版韩国年报 wiki 管线以 PDF 解析产物为主输入，同时保持产物包兼容 PostgreSQL 和多市场 evidence API。DART/XBRL 后续可以补强同一个产物包，但不是第一版生产落地的前置条件。

## 目标

- 基于已完成的韩国市场 PDF 解析结果，在 `data/wiki/kr_reports` 下生成韩国市场 wiki 根目录。
- 参考 A 股 wiki 的公司中心化组织方式，保留报告、指标、证据、语义路由和可审计 catalog。
- 韩国市场数据与 A 股 `data/wiki/companies` 列表、A 股 `_meta` catalog 隔离。
- 固化证据契约，确保智能体问答能回溯到原始 PDF 页码、表格索引、Markdown 行号和解析任务。
- 将生成后的韩国市场 package 接入现有前端/后端市场报告交互。
- 支持对 30 家韩国主流上市公司年报样本批量生成 wiki。

## 非目标

- 不重写 A 股 wiki 管线。
- 第一版韩国 package 生成不强依赖 DART/XBRL facts。
- 不把韩国公司合入 A 股 dashboard 公司列表。
- 不把 LLM 语义抽取作为第一版 wiki 生成的必需依赖。

## 现有上下文

A 股 wiki 使用 `data/wiki/companies/<stock_code>-<name>`，并在公司目录下组织 `reports`、`metrics`、`evidence`、`semantic`，同时通过 `_meta` catalog 做全局索引。它最重要的契约是可回溯性：财务数字和经营判断必须能回到 `task_id`、`report_id`、PDF 页码、表格索引和 Markdown 行号。

当前仓库已经有 HK、JP、KR、EU、US 的多市场 package 框架。后端 API 已有 `MARKET_WIKI_ROOTS`、`MARKET_BUILD_SCRIPTS` 和 `MARKET_IMPORT_SCRIPTS`；其中 KR 默认根目录是 `data/wiki/kr_reports`，当前构建脚本是 `scripts/kr/build_kr_evidence_package.py`，PostgreSQL 入库脚本是 `db/imports/import_kr_evidence_package_to_postgres.py`，目标 schema 为 `dart_kr`。

现有 KR 脚本偏 DART XBRL/API 输入，PDF 解析目录只是可选补充。新的第一版工作流需要倒过来：PDF 解析产物是主来源，`xbrl/facts_raw.json` 可以为空，但必须写入明确 warning。

## 目录契约

韩国市场 wiki package 使用独立根目录：

```text
data/wiki/kr_reports/
  README.md
  AGENTS.md
  _meta/
    company_catalog.json
    report_catalog.json
    ingest_manifest.json
    coverage_report.json
    wiki_naming_contract.md
  companies/
    005930-SamsungElectronics/
      company.md
      company.json
      reports/
        2025-annual_<task_or_rcp>/
          manifest.json
          README.md
          raw/
            report.pdf
            report.metadata.json
          parser/
            document_full.json
            quality_report.json
            financial_data.json
            financial_checks.json
            table_relations.json
            content_list_enhanced.json
          sections/
            report.md
            report_complete.md
            section_index.json
          tables/
            table_index.json
            table_0001.json
          metrics/
            financial_data.json
            financial_checks.json
            load_plan.json
            normalized_metrics.json
            operating_metrics.json
            three_statements.json
            key_metrics.json
            validation.json
          evidence/
            evidence_index.json
            pdf_refs.json
          semantic/
            retrieval_index.json
            segments.json
            facts.json
            claims.json
            note_links.json
            extraction_log.json
          qa/
            quality_report.json
            source_map.json
            extraction_warnings.json
            table_quality_signals.json
          xbrl/
            facts_raw.json
```

package 路径采用 A 股式公司/报告组织，但每个报告目录仍通过 `manifest.json` 保持为合法的 `market_evidence_package_v1`。这样智能体可以把它当 wiki 读，`/api/market-reports/*`、PostgreSQL 入库和向量入库也能把它当多市场 evidence package 读。

路径里的 `company_id` 采用文件系统安全格式：`<six_digit_ticker>-<ascii_company_slug>`。技术 ID 仍明确写在 JSON 中，例如 `company_id: "KR:005930"`、`ticker: "005930"`。

## 元数据与命名

韩国市场命名契约如下：

- `market`: `KR`
- `ticker`: 6 位 KRX 股票代码，不足 6 位时补零
- `company_id`: 默认 `KR:<ticker>`，只有在特定 DART 披露链接需要时才补充 `corp_code`
- `company_dir`: `<ticker>-<ascii_slug>`
- `report_id`: `<fiscal_year>-annual_<task_id_or_rcp_no>`
- `filing_id`: `KR:<ticker>:<task_id_or_rcp_no>`
- `accounting_standard`: `KIFRS`
- `currency`: `KRW`

来源元数据从韩国下载 manifest、相邻 `*.metadata.json`、parser task metadata 和文件名 fallback 中读取。允许字段缺失，但必须记录到 `qa/extraction_warnings.json` 和 `_meta/coverage_report.json`。

## 输入来源

第一版消费已完成的 PDF parser 结果目录：

```text
data/pdf-parser/results/<task_id>/document_full.json
data/pdf-parser/results/<task_id>/quality_report.json
data/pdf-parser/results/<task_id>/financial_data.json
data/pdf-parser/results/<task_id>/financial_checks.json
data/pdf-parser/results/<task_id>/table_relations.json
data/pdf-parser/results/<task_id>/content_list_enhanced.json
```

case discovery 脚本同时读取：

```text
data/market-report-finder/kr_2025_annual_download_queue_manifest.json
data/market-report-finder/downloads/KR/**/<report>.pdf
```

只有当 `market` 为 `KR`、`document_full.json` 存在、且 parser task 已完成或具备足够产物时，解析结果才可进入 KR wiki。市场字段不明确的结果直接跳过，不允许 fallback 导入 A 股路径。

## Scripts

### `scripts/kr/discover_kr_parsed_cases.py`

发现韩国市场 parser case，并写出确定性的 case set，例如：

```text
eval_datasets/market_ingestion_cases/kr_30_pdf_cases.json
```

每条 case 包含 `market`、`ticker`、`company_name`、`industry`、`pdf_path`、`parser_result`、`task_id`、`report_year`、`report_type`、`period_end`、`published_at` 和 metadata provenance。脚本需要过滤 CN/HK/JP/EU/US 任务、非年报、缺失 parser 产物的任务，以及重复 PDF/task 组合。

### `scripts/kr/build_kr_pdf_wiki_package.py`

构建单份韩国 PDF wiki package：

```bash
python3 scripts/kr/build_kr_pdf_wiki_package.py \
  --pdf data/market-report-finder/downloads/KR/.../Samsung_KR_005930_2025-12-31_年报_2026-03-10_dart_public_x.pdf \
  --parser-result data/pdf-parser/results/<task_id> \
  --output-root data/wiki/kr_reports \
  --force
```

脚本写入 A 股式目录、`manifest.json`、metrics、evidence、semantic seed 文件和 QA 文件。脚本必须在 stdout 输出最终 package 目录，方便现有 API job runner 在构建后读取 package detail。

### `scripts/kr/kr_pdf_wiki_lib.py`

沉淀可复用逻辑：

- identity 与 report ID 归一化
- 从 manifest、parser task、文件名推断元数据
- parser 产物复制
- Markdown 与 section index 生成
- 表格拆分与 table index 生成
- KR financial data 转换为兼容 metrics
- source map 与 A 股式 evidence index 生成
- semantic retrieval seed 生成
- catalog 更新
- package 校验

该模块可以复用 `market_report_rules_service.evidence_package` helper 和 KR profile 的既有输出，但不应整段复制 A 股 wikiset 大模块。

### `scripts/kr/ingest_kr_case_set.py`

批量构建 30 家韩国公司样本：

```bash
python3 scripts/kr/ingest_kr_case_set.py \
  --case-set eval_datasets/market_ingestion_cases/kr_30_pdf_cases.json \
  --output-root data/wiki/kr_reports \
  --force
```

可选参数：

- `--limit N`：小样本 smoke run
- `--ticker 005930`：单家公司定向重建
- `--import-postgres`：调用 `db/imports/import_kr_evidence_package_to_postgres.py`
- `--vector-ingest`：调用 `scripts/vector-index/milvus-ingestion/ingest_market_evidence_chunks.py`

脚本写入 `_meta/ingest_manifest.json` 和 `_meta/coverage_report.json`，记录数量、跳过 case、warning 汇总、package 路径和校验结果。

## Package 产物

`manifest.json` 是主要机器契约。必填字段包括 `schema_version`、`market`、`filing_id`、`company_id`、`ticker`、`company_name`、`report_type`、`fiscal_year`、`fiscal_period`、`period_end`、`published_at`、`source_url`、`local_source_path`、`accounting_standard`、`parser_version`、`rules_version`、`quality_status`、`pdf_parser_task_id`、`parser_result_dir` 和 `artifact_hashes`。

`metrics/financial_data.json` 和 `metrics/financial_checks.json` 在 parser 产物存在时直接复制。`metrics/three_statements.json`、`metrics/key_metrics.json` 和 `metrics/validation.json` 是面向 A 股式智能体的兼容入口，只能从 KR `financial_data`、KR quality candidates 和 financial checks 派生，不能凭空生成。

`evidence/evidence_index.json` 是 A 股兼容的 evidence 入口。`qa/source_map.json` 仍是多市场 evidence package 入口。两者应尽量指向同一组底层 evidence ID。

`semantic/retrieval_index.json` 是第一版智能体检索路由索引，保存核心财报、分部信息、收入、营业利润、净利润、总资产、EPS、风险、治理、股东和管理层讨论等章节/表格候选。第一版可以只包含稀疏语义事实，但每条记录必须有 source pointer 或 warning。

## 证据契约

每条生成的 evidence entry 必须尽量保留 parser 能提供的字段：

- `evidence_id`
- `market`
- `company_id`
- `ticker`
- `report_id`
- `filing_id`
- `task_id` 或 `pdf_parser_task_id`
- `source_type`
- `target`
- `canonical_name`
- `local_name`
- `quote_text`
- `pdf_page_number`
- `table_index`
- `row_index`
- `column_index`
- `md_line`
- `wiki_path`
- `local_path`
- `source_url`
- `confidence`
- `fallback_reason`

智能体问答引用至少必须包含 `report_id` 和 `pdf_page_number`。财务表格类答案在可用时必须包含 `table_index`。如果只有 `md_line` 可用，引用必须从 `sections/report.md` 中向上寻找最近的 `[PDF_PAGE: n]` 标记，并把页码标记为 inferred。缺失页码/表格锚点是 warning，不允许静默当作成功。

## 前端与 API 联动

现有后端配置已经定义：

```text
MARKET_WIKI_ROOTS["KR"] = data/wiki/kr_reports
MARKET_BUILD_SCRIPTS["KR"] = scripts/kr/build_kr_evidence_package.py
MARKET_IMPORT_SCRIPTS["KR"] = db/imports/import_kr_evidence_package_to_postgres.py
```

实现时可以将 `MARKET_BUILD_SCRIPTS["KR"]` 指向 `build_kr_pdf_wiki_package.py`，也可以保留 `build_kr_evidence_package.py` 作为兼容 wrapper：当输入为 PDF source 且提供 `--parser-result` 时，自动委派给 PDF wiki builder。

`market_package_repository.iter_market_packages()` 当前对非 EU package 使用浅层 `*/*/*/manifest.json` pattern。KR 采用 A 股式路径后，需要新增 KR 专用扫描 pattern：

```text
companies/*/reports/*/manifest.json
```

完成后，以下既有端点应能直接读取 KR package：

- `GET /api/market-reports/packages?market=KR`
- `GET /api/market-reports/package?market=KR&package_path=...`
- `GET /api/market-reports/packages/{filing_id}?market=KR`
- `GET /api/market-reports/package/quality?market=KR&package_path=...`
- `GET /api/market-reports/evidence/{evidence_id}?market=KR&package_path=...`
- `GET /api/market-reports/package-file?market=KR&package_path=...&file=sections/report.md`

前端市场解析页在 KR package 构建后应展示 package 关联入口：

- 展示生成的 `package_path`、`filing_id`、`quality_status` 和 warning 数量
- 提供打开 package detail 的操作，读取 `GET /api/market-reports/package`
- 对质量候选和财务勾稽项，基于 `evidence_id` 展示 evidence/source 按钮
- 通过带鉴权的 `/api/market-reports/package-file` 打开 source 文件
- 对存在 `pdf_page_number` 和 `table_index` 的证据，复用现有 PDF source trace UI 打开 PDF 页/表格

当市场筛选为 `KR` 时，KR package 列表应出现在多市场 package 面板中。除非未来产品明确增加跨市场视图，否则 KR package 不应出现在 A 股 `/api/wiki/companies/list` dashboard 中。

## PostgreSQL 与检索后续

第一版 wiki 产物必须保持与 `db/imports/import_kr_evidence_package_to_postgres.py` 兼容。PostgreSQL 入库后应写入 `dart_kr.companies`、`filings`、`parse_runs`、`pdf_tables`、`evidence_citations`、`financial_facts`、`operating_metric_facts`、`financial_checks` 和 `retrieval_chunks`。

`retrieval_chunks` 应保留 `wiki_path`、`evidence_id`、`page_number`、`table_index`、`canonical_name` 和 `period_key`，这样后续 chat retrieval 无需重读整个 package，也能引用 PDF 页码。

## 校验与测试

单元测试应覆盖：

- KR 文件名和 manifest 元数据推断
- 只发现 KR case，并验证市场隔离
- 单个 package 目录生成
- `manifest.json` 必填字段
- A 股兼容的 `metrics/three_statements.json`、`evidence/evidence_index.json` 和 `semantic/retrieval_index.json`
- evidence entry 包含 PDF 页码和表格锚点
- `data/wiki/kr_reports/_meta` 下 catalog 生成
- 后端支持扫描 `companies/*/reports/*/manifest.json`
- 前端/API payload 返回 KR package detail 和 evidence file URL

Smoke test 应构建一个已知 KR parser 结果，然后断言：

- `GET /api/market-reports/packages?market=KR` 返回该 package
- package detail 包含 metrics、evidence、tables、QA 和 files
- 至少一个核心财报证据 lookup 能返回 `pdf_page_number` 和 `table_index`
- KR package 不出现在 `/api/wiki/companies/list`

## 落地步骤

1. 新增 KR PDF wiki library 和单 package builder。
2. 新增 30 家样本 discovery 与 batch ingest 脚本。
3. 在后端 repository helper 中新增 KR 专用 market package 路径扫描。
4. 将前端 package/evidence 操作接到现有 KR market-report API。
5. 构建一个 package 并运行 API smoke test。
6. 构建完整 30 家样本集并写出 coverage report。
7. 在 wiki package 质量通过后，可选执行 PostgreSQL 入库和向量入库。
