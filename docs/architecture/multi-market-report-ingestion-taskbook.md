# 多市场年报/财报下载解析后续开发任务书

日期：2026-06-27

适用仓库：

```text
/home/maoyd/siq-research-engine
```

## 0. 当前执行进度快照

更新时间：2026-06-27

当前状态：P0/P1 骨架已大体铺开，规则服务和导入器单元测试通过；US 已有 5 个 10-K 样本通过批量评估，HK 已有 2 个真实 PDF 样本通过批量评估，但 JP/KR 真实 wiki evidence package 仍缺失，尚未达到完整 Definition of Done。

已验证通过：

- `services/market-report-rules` 全量测试通过：`python3 -m pytest tests`，19 passed。
- `db/imports/tests` 导入器单元测试通过：4 passed。
- API 市场报告代理测试通过：`cd apps/api && uv run python -m pytest tests/test_market_reports_proxy.py`，7 passed。注意：直接用系统 `python3` 会缺 `sqlmodel`，API 测试应使用 `uv run`。
- P1 批量评估脚本可运行：`scripts/maintenance/run_market_ingestion_eval.py`。
- US case set 已扩展为 5 个本机可验证 10-K 样本：`MSFT 2025`、`AAPL 2025`、`COST 2024`、`TSLA 2025`、`NVDA 2026`。
- HK `TENCENT 00700 2025`、`AIA 01299 2025` 已基于现有 `data/pdf-parser/results` 生成真实 evidence package，validator 通过。
- 当前批量评估结果：9 cases，US 5/5 pass，HK 2/2 pass，JP/KR 2 个 case 为 `missing_package`。

仍未完成：

- US 仍缺任务书要求的至少 1 个 `20-F/IFRS` 样本；当前本机 `data/wiki/us_sec` 和 `data/market-report-finder/downloads/US` 未发现 20-F/IFRS package。
- US 5 个样本已通过 package 级评估，但还未完成 PostgreSQL `sec_us` 真实库导入和 SQL evidence 追溯验收。
- HK 已有下载 PDF 和 builder/DDL/导入器/测试骨架；`00700`、`01299` 真实 wiki package 已生成并通过 package 级评估。真实 DB 入库和 SQL evidence 追溯仍待执行。
- JP `7203`、KR `005930` 真实 wiki package 尚未生成，评估仍为 `missing_package`。
- Milvus 多市场 collection 重建、metadata 反查 DB evidence 尚未完成端到端验收。
- 前端/API 已有市场入口和包状态 API 骨架，但 evidence 点击跳转 PDF 页/表格或 SEC anchor 仍需真实样本验收。

## 一、任务背景和总目标

今天已重点推进：

- 美股：SEC 年报、财报下载；SEC HTML/iXBRL 证据包、XBRL facts 抽取、规则归一化、入库 DDL/导入脚本已有雏形。
- 港股：HKEX 年报 PDF 下载；规则服务已能基于 PDF 表格标题和行项目抽取三大表。
- 日股：EDINET/TDnet 年报下载；规则服务已有 EDINET XBRL + PDF 表格 hybrid 抽取骨架。
- 韩股：DART 年报下载；规则服务已有 DART XBRL + PDF 表格 fallback 抽取骨架。

本任务书的目标不是重写下载器，而是把多市场后续链路补齐到接近 A 股现有“抽取、入库、校验、可回溯证据链”的精度：

```text
官方披露下载
  -> 市场专属解析/证据包
  -> 规则抽取 financial_data / financial_checks
  -> PostgreSQL 市场隔离事实库
  -> Wiki 证据包
  -> Milvus 市场隔离向量索引
  -> 前端/Agent 可回溯引用
```

## 二、当前状态判断

### 1. 已有可复用资产

下载服务：

- `services/market-report-finder`
- 市场模块：`markets/cn`、`markets/us`、`markets/hk`、`markets/jp`、`markets/kr`
- 统一保存目录和 metadata 合同已基本形成。

规则服务：

- `services/market-report-rules`
- US：`markets/us/extractor.py`，基于 SEC/XBRL facts。
- HK：`markets/hk/extractor.py`，基于 PDF 表格。
- JP/KR：`markets/xbrl_table_hybrid.py`，已支持 XBRL/API facts + PDF 表格 fallback。
- 验证：`validation.py` 已有三大表、勾稽、经营指标、证据引用检查。

SEC 证据链：

- `scripts/us-sec/sec_evidence_lib.py`
- `scripts/us-sec/build_sec_evidence_package.py`
- `db/ddl/010_create_sec_us_schema.sql`
- `db/imports/import_sec_filing_to_postgres.py`

A 股参考链路：

- `apps/pdf-parser`
- `db/ddl/001_create_pdf2md_schema.sql`
- `db/imports/import_document_full_to_postgres.py`
- 重点参考：PDF 页码、表格索引、质量报告、`financial_data.json`、`financial_checks.json`、证据定位。
- 仅允许只读参考，不允许在本计划中修改这些 A 股相关文件和行为。

### 2. 当前主要缺口

- US：已有 evidence package 和 sec_us 入库，但章节定位、XBRL fact source map、10-Q 期间选择、20-F/IFRS 覆盖还需生产化。
- HK：下载与规则抽取、HK PDF evidence package builder、`pdf2md_hk` DDL/导入器已有骨架和单元测试；`00700`、`01299` package 已由真实 parser result 生成并通过批量质量门禁。仍缺真实 DB 入库和 SQL evidence 追溯验收。
- JP：下载和 rules hybrid、JP evidence package builder、`edinet_jp` DDL/导入器已有骨架和单元测试，但缺少 `7203` 真实 package 与入库验收。
- KR：下载和 rules hybrid、KR evidence package builder、`dart_kr` DDL/导入器已有骨架和单元测试，但缺少 `005930` 真实 package 与入库验收。
- 向量层：已有 `ingest_sec_wiki_chunks.py` 和统一 `ingest_market_evidence_chunks.py` 骨架，HK/JP/KR collection 仍缺少真实 package 重建验收。
- 前端/Agent：市场页面和 API 骨架已有，仍缺少真实 evidence 点击跳转和质量报告联调验收。

## 三、硬性工程原则

0. 不动现有 A 股年报、财报功能。
   - 本计划只能新增或修改 US/HK/JP/KR 相关链路，以及新增跨市场只读聚合层。
   - 不修改 `apps/pdf-parser` 中 A 股已在使用的解析、抽取、校验逻辑，除非新增代码默认关闭且不影响 CN。
   - 不修改 `db/ddl/001_create_pdf2md_schema.sql`、`db/imports/import_document_full_to_postgres.py` 等 A 股 legacy 入库脚本。
   - 不修改 `pdf2md` schema 的现有表结构、字段语义、索引和数据写入路径。
   - 不修改 A 股下载器、CNINFO 解析逻辑、A 股前端入口的既有行为。
   - 参考 A 股时只能做只读分析：学习 evidence、page/table、quality report 的合同与精度，不复用会造成行为变化的代码路径。
   - 如确需抽公共工具，必须先新增旁路工具并证明 CN 测试、A 股样本回归完全不变，再单独提评审，不纳入本任务书默认范围。

1. 不把不同市场写入同一 schema。
   - CN legacy：`pdf2md`
   - US：`sec_us`
   - HK：`pdf2md_hk`
   - JP：`edinet_jp`
   - KR：`dart_kr`

2. 不用大模型猜财务数字。
   - 财务数字只能来自 XBRL/API facts、PDF 表格单元格、人工修正记录。
   - 大模型最多用于标题分类、章节辅助、经营指标候选，不允许无证据入库。

3. 每个事实必须有 evidence。
   - US：`filing_id + accession + xbrl_tag + context_ref + html_anchor/source_url`
   - HK：`filing_id + pdf_page + table_index + row_index + column_index`
   - JP：`filing_id/doc_id + xbrl_tag/context_ref` 或 PDF 表格坐标
   - KR：`filing_id/rcp_no + xbrl_tag/context_ref` 或 PDF/XML 表格坐标

4. 入库脚本必须幂等。
   - 同一个 evidence package 重跑不得重复写事实。
   - parse_run_id 应由 filing_id、parser_version、rules_version、artifact_hashes 稳定生成。

5. Milvus 只做召回，不做事实源。
   - 可从 Wiki/DB 重建。
   - metadata 必须包含 market、schema、filing_id、parse_run_id、evidence_id。

## 四、P0 开发包：先打通四个市场的闭环

### P0-1：统一 evidence package 合同

负责人窗口：任一后端/数据窗口

目标：

定义多市场统一证据包最小合同，供 US/HK/JP/KR 构建器和导入器共用。

新增/修改文件：

- 新增 `docs/architecture/market-evidence-package-contract.md`
- 新增 `services/market-report-rules/src/market_report_rules_service/evidence_package.py`
- 补测试 `services/market-report-rules/tests/test_evidence_package_contract.py`

最小目录合同：

```text
data/wiki/<market_namespace>/<ticker>/<fiscal_year>/<form_or_type>_<filing_key>/
  manifest.json
  README.md
  raw/
  sections/
  tables/
  xbrl/
  metrics/
  qa/
```

`manifest.json` 必填字段：

```json
{
  "schema_version": "market_evidence_package_v1",
  "market": "US|HK|JP|KR",
  "filing_id": "...",
  "company_id": "...",
  "ticker": "...",
  "company_name": "...",
  "source_id": "sec|hkex|edinet|dart",
  "form": "...",
  "report_type": "annual|semiannual|quarterly",
  "fiscal_year": 2025,
  "fiscal_period": "FY|H1|Q1|Q2|Q3|Q4",
  "period_end": "2025-12-31",
  "published_at": "2026-04-01",
  "source_url": "...",
  "local_source_path": "raw/...",
  "accounting_standard": "US_GAAP|IFRS|HKFRS|CASBE|JGAAP|KIFRS|UNKNOWN",
  "parser_version": "...",
  "rules_version": "...",
  "quality_status": "pass|warning|fail",
  "artifact_hashes": {}
}
```

验收标准：

- 四个市场证据包都能通过同一个 validator。
- validator 检查：必填字段、artifact path 存在、hash 可重算、metrics/financial_data.json 与 checks 存在。
- 测试命令：

```bash
cd /home/maoyd/siq-research-engine/services/market-report-rules
python3 -m pytest tests/test_evidence_package_contract.py
```

### P0-2：补齐 HK PDF evidence package 构建器

负责人窗口：HK/PDF 窗口

目标：

把 HKEX 下载 PDF 经 `apps/pdf-parser` 解析后的产物转换成港股证据包，并调用 `market-report-rules` 生成 `financial_data.json`、`financial_checks.json`、`load_plan.json`。

边界：

- 只消费 `apps/pdf-parser` 已有产物，不改 A 股解析服务既有逻辑。
- 如需要 HK 专属适配，放在 `scripts/hk/` 或 `services/market-report-rules/markets/hk/`，不得改 CN extractor 或 A 股规则。

新增文件：

- `scripts/hk/build_hk_evidence_package.py`
- `scripts/hk/hk_evidence_lib.py`
- `scripts/hk/ingest_hk_case_set.py`
- `tests` 可放在 `services/market-report-rules/tests/test_hk_evidence_package.py`

输入：

- 原始 PDF：`downloads/HK/.../*.pdf` 或 `data/market-report-finder/downloads/HK/.../*.pdf`
- finder metadata：`*.metadata.json`
- PDF parser result：`data/pdf-parser/results/<task_id>/document_full.json`

输出：

```text
data/wiki/hk_reports/<ticker>/<fiscal_year>/<report_type>_<filing_key>/
  manifest.json
  raw/report.pdf
  raw/report.metadata.json
  sections/report.md
  tables/table_index.json
  tables/table_0001.json
  metrics/financial_data.json
  metrics/financial_checks.json
  metrics/load_plan.json
  qa/quality_report.json
  qa/source_map.json
```

关键实现要求：

- 将 `content_list_enhanced.json`、`document_full.json` 中表格转成 `ParsedTable`。
- 保留 `page_number`、`table_index`、`row_index`、`column_index`。
- 调用 `process_artifact(ParsedArtifact(market=Market.HK))`。
- `source_map.json` 中每个抽取事实都能定位到 PDF 页码和表格坐标。
- 对双语/繁简体标题不做强行翻译，规则中维护 alias。

验收样本：

- `TENCENT_HK_00700`
- `AIA_HK_01299`
- `SMIC_HK_00981`
- `BANK-OF-CHINA_HK_03988`
- `BABA-W_HK_09988`

质量门禁：

- 年报至少识别三大表中的 3 张，银行/保险可先允许 cash flow warning。
- `financial_checks.overall_status` 不得为 fail，除非明确是解析质量问题并有 warning。
- 每个 `financial_data.statements[].items[]` 至少一个 evidence target。

验收命令：

```bash
cd /home/maoyd/siq-research-engine
python3 scripts/hk/build_hk_evidence_package.py <pdf_path> --parser-result <result_dir> --metadata <metadata_json> --force
cd services/market-report-rules && python3 -m pytest tests/test_hk_rules.py
```

### P0-3：补齐 HK schema 和入库器

负责人窗口：DB/后端窗口

目标：

为港股建立与 A 股兼容、但物理隔离的 `pdf2md_hk` schema，并提供幂等导入器。

新增文件：

- `db/ddl/020_create_pdf2md_hk_schema.sql`
- `db/imports/import_hk_evidence_package_to_postgres.py`
- `db/imports/tests/test_import_hk_evidence_package.py`

推荐表：

- `pdf2md_hk.companies`
- `pdf2md_hk.filings`
- `pdf2md_hk.parse_runs`
- `pdf2md_hk.artifacts`
- `pdf2md_hk.filing_sections`
- `pdf2md_hk.pdf_pages`
- `pdf2md_hk.pdf_tables`
- `pdf2md_hk.financial_facts`
- `pdf2md_hk.operating_metric_facts`
- `pdf2md_hk.financial_checks`
- `pdf2md_hk.evidence_citations`
- `pdf2md_hk.retrieval_chunks`

与 A 股精度对齐点：

- `pdf_tables` 必须保存 `page_number/table_index/title/row_count/column_count/table_json_path`。
- `financial_facts` 必须保存 `canonical_name/value/unit/currency/period_key/confidence/evidence_id`。
- `evidence_citations` 必须保存 `page_number/table_index/row_index/column_index/quote_text/local_path/source_url`。

验收标准：

- 同一 HK package 连续导入两次，事实行数不翻倍。
- 能用 SQL 从 `financial_facts` 追到 `evidence_citations` 再追到 `tables/table_000N.json`。
- 验收 SQL：

```sql
select ticker, canonical_name, period_key, value, evidence_id
from pdf2md_hk.financial_facts
where ticker = '00700'
order by canonical_name, period_key;
```

### P0-4：生产化 US SEC 链路

负责人窗口：US/SEC 窗口

目标：

在已有 `scripts/us-sec/sec_evidence_lib.py`、`db/ddl/010_create_sec_us_schema.sql`、`db/imports/import_sec_filing_to_postgres.py` 基础上补精度和批处理。

修改文件：

- `scripts/us-sec/sec_evidence_lib.py`
- `scripts/us-sec/build_sec_evidence_package.py`
- `scripts/us-sec/ingest_sec_case_set.py`
- `db/imports/import_sec_filing_to_postgres.py`
- `services/market-report-rules/src/market_report_rules_service/markets/us/rules.py`
- `services/market-report-rules/tests/test_us_rules.py`

开发项：

1. 章节定位增强
   - 10-K、10-Q、20-F 分开 section pattern。
   - 不仅用纯文本 offset，尽量保留 HTML id/name anchor。
   - `sections.json` 增加 `char_start/char_end/html_anchor/xpath/text_hash`。

2. XBRL context 精度增强
   - 区分 instant、duration、QTD、YTD、FY。
   - context dimensions 不为空时默认不覆盖 consolidated 主口径，除非规则允许 segment。
   - 同一 canonical/period 多事实选择要记录 rejected candidates 到 `qa/extraction_warnings.json`。

3. 20-F/IFRS 覆盖
   - 补 `ifrs-full:*` 常用 tag 映射。
   - 20-F 年报缺 US-GAAP tag 不应 fail。

4. companyfacts fallback
   - iXBRL facts 不完整时允许下载/读取 SEC companyfacts 补充。
   - fallback fact 的 evidence source_type 标记为 `sec_companyfacts_fact`，不能伪装 HTML anchor。

5. 批处理
   - `ingest_sec_case_set.py` 支持扫描 `data/market-report-finder/downloads/US`。
   - 支持 `--limit`、`--ticker`、`--form`、`--force`、`--import-db`。

验收样本：

- `MSFT` 10-K：已通过 package 级评估。
- `AAPL` 10-K：已通过 package 级评估。
- `COST` 10-K，财年非自然年：已通过 package 级评估。
- `TSLA` 10-K：已通过 package 级评估。
- `NVDA` 10-K，财年非自然年：`NVDA 2026` 已通过 package 级评估；`NVDA 2025` package 存在但 `quality_status=fail`，后续可作为规则修复样本。
- 至少 1 个 20-F/IFRS 样本：未完成，当前本机未发现可用 package。

验收标准：

- 每个样本生成 package 并入库 `sec_us`。当前 5 个 10-K 样本 package 级评估通过；真实 DB 入库和 SQL 追溯仍待执行。
- 年报三大表核心指标有值：收入、净利润、总资产、总负债、权益、经营现金流。当前批量评估检查收入、净利润、总资产；总负债、权益、经营现金流仍需纳入后续 case 断言。
- 10-Q 测试保持 QTD/YTD 不混淆。
- SQL 能从 `sec_us.financial_facts.raw_fact_id` 追到 `sec_us.xbrl_facts_raw`，再追到 `evidence_citations`。

验收命令：

```bash
cd /home/maoyd/siq-research-engine
python3 scripts/us-sec/build_sec_evidence_package.py <sec_html> --metadata <metadata_json> --force
python3 db/imports/import_sec_filing_to_postgres.py <package_dir> --run-ddl
cd services/market-report-rules && python3 -m pytest tests/test_us_rules.py
```

### P0-5：JP/KR 先完成“下载后可解析、可入库”的最小闭环

负责人窗口：JP/KR 窗口

目标：

JP/KR 不要求第一阶段达到 HK/US 同等覆盖率，但必须完成“证据包 + rules + 入库 + evidence”闭环。

新增文件：

- `scripts/jp/build_jp_evidence_package.py`
- `scripts/jp/jp_evidence_lib.py`
- `scripts/kr/build_kr_evidence_package.py`
- `scripts/kr/kr_evidence_lib.py`
- `db/ddl/030_create_edinet_jp_schema.sql`
- `db/ddl/040_create_dart_kr_schema.sql`
- `db/imports/import_jp_evidence_package_to_postgres.py`
- `db/imports/import_kr_evidence_package_to_postgres.py`

JP 实现要求：

- 支持 EDINET `documents/{docID}?type=1` XBRL zip 解包，优先抽 XBRL。
- PDF 作为证据展示和 fallback 表格来源。
- manifest 使用 `doc_id`、`edinet_code`、`security_code`。
- source_type 使用 `edinet_xbrl_fact`、`edinet_pdf_statement_table`。

KR 实现要求：

- 支持 DART `document.xml` zip/XML 解包。
- 优先解析 DART XBRL 或 `fnlttSinglAcntAll` 结构化 API；PDF/XML 表格作为 fallback。
- manifest 使用 `rcp_no`、`corp_code`、`stock_code`。
- source_type 使用 `dart_xbrl_fact`、`dart_api_fact`、`dart_pdf_statement_table`。

schema 名称：

- JP：`edinet_jp`
- KR：`dart_kr`

验收样本：

- JP：Toyota `7203` / EDINET `E02144`
- KR：Samsung Electronics `005930` / DART corp code `00126380`

验收标准：

- 至少能抽出三大表核心指标中的 6 个 canonical metric。
- 每个 fact 有 XBRL tag 或 PDF/XML 表格坐标 evidence。
- rules 现有测试必须通过：

```bash
cd /home/maoyd/siq-research-engine/services/market-report-rules
python3 -m pytest tests/test_jp_kr_rules.py
```

## 五、P1 开发包：质量、覆盖率、前端联通

### P1-1：多市场质量门禁报告

目标：

统一四个市场的 `qa/quality_report.json` 结构，支持前端和批处理看板。

必填字段：

- `overall_status`
- `section_count`
- `table_count`
- `raw_fact_count`
- `normalized_metric_count`
- `evidence_coverage_ratio`
- `required_statement_status`
- `critical_warnings`
- `parser_warnings`
- `rule_warnings`

验收标准：

- US/HK/JP/KR package 都有同名字段。
- `evidence_coverage_ratio < 1` 时必须列出缺 evidence 的 metric。

### P1-2：多市场批量 case set

新增目录：

```text
eval_datasets/market_ingestion_cases/
  us_cases.json
  hk_cases.json
  jp_cases.json
  kr_cases.json
```

每个 case：

```json
{
  "market": "HK",
  "ticker": "00700",
  "company_name": "TENCENT",
  "report_type": "annual",
  "fiscal_year": 2025,
  "expected_metrics": ["operating_revenue", "net_profit", "total_assets"],
  "expected_evidence": true
}
```

新增脚本：

- `scripts/maintenance/run_market_ingestion_eval.py`

验收标准：

- 输出 JSON/Markdown 报告。
- 按市场统计下载、解析、抽取、入库、证据覆盖率。
- 当前已落地并运行：9 cases，US 5/5 pass，HK 2/2 pass，JP/KR 2 个 case missing package。后续应先生成 JP `7203`、KR `005930` package，再将评估提升到全市场 pass。

### P1-3：Milvus 多市场 collection 接入

目标：

将 Wiki 证据包可读文本和关键事实 chunk 入向量库。

collection：

- `siq_us_sec_filings`
- `siq_hk_reports`
- `siq_jp_reports`
- `siq_kr_reports`

新增/修改：

- `scripts/vector-index/milvus-ingestion/ingest_market_evidence_chunks.py`
- 保留或适配 `ingest_sec_wiki_chunks.py`

chunk metadata 必填：

```json
{
  "market": "US",
  "ticker": "MSFT",
  "filing_id": "...",
  "parse_run_id": "...",
  "doc_type": "section|table|fact|qa",
  "evidence_id": "...",
  "source_url": "...",
  "wiki_path": "...",
  "period_key": "...",
  "canonical_name": "..."
}
```

验收标准：

- Milvus chunk 可从 metadata 反查 DB evidence。
- 删除 collection 后能从 Wiki 重建。

### P1-4：API 和前端状态联通

目标：

下载页面、市场解析页面能看到每个文件：

- 下载状态
- 解析状态
- 证据包路径
- 入库状态
- 质量状态
- 证据预览链接

修改范围：

- `apps/api/routers/market_reports.py`
- `apps/api/routers/downloads.py` 仅可新增市场隔离分支，不改变 A 股请求/响应语义
- `apps/web/src/pages/MarketParsingPage.tsx`
- `apps/web/src/pages/UsParsing.tsx`
- `apps/web/src/pages/HkParsing.tsx`
- `apps/web/src/pages/JpParsing.tsx`
- `apps/web/src/pages/KrParsing.tsx`
- `apps/web/src/lib/secApi.ts` 或新增 `marketIngestionApi.ts`

边界：

- 不改 A 股搜索下载、A 股 PDF 解析页的默认行为。
- 新增入口必须按 market 显式路由，不能让 CN 自动进入新链路。

API 建议：

- `GET /api/market-reports/packages?market=HK`
- `POST /api/market-reports/packages/build`
- `POST /api/market-reports/packages/import`
- `GET /api/market-reports/packages/{filing_id}/quality`
- `GET /api/market-reports/evidence/{evidence_id}`

验收标准：

- 前端可从 HK/US/JP/KR 列表进入质量报告。
- 点击某条指标 evidence 能打开 PDF 页/表格或 SEC HTML anchor。

## 六、P2 开发包：精度提升和行业扩展

### P2-1：行业 profile 和经营指标

重点行业：

- 银行
- 保险
- 地产
- 能源
- 互联网平台
- SaaS
- 零售
- 制造

要求：

- 经营指标进入 `operating_metric_facts`，不混入三大表。
- 每个经营指标必须有 evidence。
- company-level override 放独立规则文件，不写死在 extractor 主流程。

### P2-2：人工修正闭环

目标：

复用 A 股 PDF parser 的表格修正思想，为 HK/JP/KR PDF fallback 提供人工修正入口。

要求：

- 修正记录保存原值、新值、操作者、时间、原因。
- 入库 fact 标记 `gaap_status` 或 `raw.corrected=true`。
- source_map 仍保留原始证据和修正证据。

### P2-3：跨市场聚合视图

新增 schema：

- `analytics`

只读视图：

- `analytics.filing_catalog`
- `analytics.company_universe`
- `analytics.normalized_financial_facts`

要求：

- 保留 `market`、`accounting_standard`、`source_schema`。
- 不把不同准则数字无上下文合并。

## 七、建议分工顺序

1. US 窗口：先把 `sec_us` 链路跑通 5 个样本，证明 HTML/iXBRL 路线可稳定回放。
2. HK 窗口：优先完成 evidence package + `pdf2md_hk` DDL/导入器，因为 HK 最接近 A 股 PDF 经验。
3. JP/KR 窗口：先做最小闭环，不追求第一轮覆盖所有披露格式。
4. DB/API 窗口：抽统一 package validator、统一导入接口、统一状态 API。
5. Eval 窗口：建立 case set 和批量质量报告，用样本驱动后续规则补强。

## 八、统一验收清单

每个市场交付时必须满足：

- 能从官方下载产物或本地样本生成 evidence package。
- `manifest.json` 合同完整，artifact hash 可重算。
- `metrics/financial_data.json` 和 `metrics/financial_checks.json` 存在。
- `qa/source_map.json` 能追溯每个核心财务 fact。
- PostgreSQL schema 独立，导入幂等。
- 至少 3 个样本通过市场级测试，US/HK 要求 5 个样本。
- 失败样本不能静默丢弃，必须输出 warning/fail 原因。
- A 股回归不变：现有 CN 下载、解析、入库、查询测试必须保持通过；若没有自动化样本，至少提供“未修改 A 股相关文件”的 diff 说明。

建议总测试命令：

```bash
cd /home/maoyd/siq-research-engine/services/market-report-finder
python3 -m pytest tests

cd /home/maoyd/siq-research-engine/services/market-report-rules
python3 -m pytest tests

cd /home/maoyd/siq-research-engine/apps/pdf-parser
python3 -m pytest tests
```

## 九、不要做的事

- 不要把 HK/JP/KR 写入 `pdf2md` legacy schema。
- 不要修改 A 股年报、财报下载、解析、抽取、校验、入库相关功能和脚本。
- 不要把 SEC HTML 渲染成假 PDF 页码再当主证据。
- 不要用 LLM 补全缺失财务数字。
- 不要把 Milvus 当作唯一事实库。
- 不要在跨市场聚合层丢掉会计准则、市场、来源 schema。
- 不要为了通过校验删除 warning；warning 是质量闭环的一部分。
