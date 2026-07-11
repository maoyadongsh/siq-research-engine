# Market PostgreSQL Ingestion Paths

## 源解析产物根目录

| 根目录 | 当前承载内容 | `document_full.json` 位置 |
| --- | --- | --- |
| `/home/maoyd/siq-research-engine/data/parser-results` | SEC / HTML parser 结果；当前主要是美股 SEC 10-K | `/home/maoyd/siq-research-engine/data/parser-results/us-sec/<filing_id>/document_full.json` |
| `/home/maoyd/siq-research-engine/data/pdf-parser/results` | PDF parser 结果；当前覆盖 CN / HK / JP / KR / EU / US PDF 解析任务 | `/home/maoyd/siq-research-engine/data/pdf-parser/results/<task_id>/document_full.json` |

注意：`/home/maoyd/siq-research-engine/data/pdf-parser` 是 PDF parser 的运行根目录，下面有 `results`、`output`、`cache`、`reports`、`db` 等子目录；PostgreSQL 入库使用的 full json 源文件在 `data/pdf-parser/results/<task_id>/document_full.json`。

| 市场 | 解析产物 / Package 来源 | PostgreSQL 入库读取入口 | 构建脚本 | PostgreSQL 入库脚本 | DDL / DML | 默认 database | schema |
| --- | --- | --- | --- | --- | --- | --- | --- |
| A股 / CN | `/home/maoyd/siq-research-engine/data/pdf-parser/results/<task_id>/document_full.json` | 直接读取 `/home/maoyd/siq-research-engine/data/pdf-parser/results/<task_id>/document_full.json`；递归模式读取 `/home/maoyd/siq-research-engine/data/pdf-parser/results/**/document_full.json` | 无需二级市场 package 构建，直接导入 `document_full.json` | `db/imports/import_document_full_to_postgres.py` | `db/ddl/001_create_pdf2md_schema.sql`; `db/dml/001_upsert_document_full.sql`; `db/dml/002_build_financial_items_enriched.sql` | `siq`，可由 `SIQ_PDF2MD_PGDATABASE` / `SIQ_PGDATABASE` / `PGDATABASE` 覆盖 | `pdf2md` |
| 通用文档 | 通用 document parse package | 读取传入的 package 目录，通常为 `<package_dir>/manifest.json` 及 package 内标准子目录 | package 已生成后导入 | `db/imports/import_document_parse_package_to_postgres.py` | `db/ddl/060_create_document_parser_schema.sql` | `siq_document_parser`，可由 `SIQ_DOCUMENT_PGDATABASE` / `SIQ_PGDATABASE` / `PGDATABASE` 覆盖 | `document_parser` |
| 美股 / US SEC | Wiki package 在 `/home/maoyd/siq-research-engine/data/wiki/us/...`；SEC parser full json 源头在 `/home/maoyd/siq-research-engine/data/parser-results/us-sec/...` | 默认读取 `/home/maoyd/siq-research-engine/data/parser-results/us-sec/<filing_id>/document_full.json`；package 仅作为证据展示和旧链路兼容 | `scripts/us-sec/build_sec_evidence_package.py` | 默认 `db/imports/import_us_sec_document_full_to_postgres.py`；兼容 `db/imports/import_sec_filing_to_postgres.py` | `db/ddl/010_create_sec_us_schema.sql` | `siq_us`，可由 `SIQ_US_PGDATABASE` 覆盖 | `sec_us` |
| 港股 / HK | Wiki package 在 `/home/maoyd/siq-research-engine/data/wiki/hk/companies/.../reports/...`；PDF parser full json 源头在 `/home/maoyd/siq-research-engine/data/pdf-parser/results/<task_id>` | 默认读取 `/home/maoyd/siq-research-engine/data/pdf-parser/results/<task_id>/document_full.json`；package 仅作为证据展示和旧链路兼容 | `scripts/hk/build_hk_evidence_package.py` | 默认 `db/imports/import_hk_document_full_to_postgres.py`；兼容 `db/imports/import_hk_evidence_package_to_postgres.py` | `db/ddl/020_create_pdf2md_hk_schema.sql` | `siq_hk`，可由 `SIQ_HK_PGDATABASE` 覆盖 | `pdf2md_hk` |
| 日股 / JP | Wiki package 在 `/home/maoyd/siq-research-engine/data/wiki/jp/companies/.../reports/...`；PDF parser full json 源头在 `/home/maoyd/siq-research-engine/data/pdf-parser/results/<task_id>` | 默认读取 `/home/maoyd/siq-research-engine/data/pdf-parser/results/<task_id>/document_full.json`；package 仅作为证据展示和旧链路兼容 | `scripts/jp/build_jp_evidence_package.py` | 默认 `db/imports/import_jp_document_full_to_postgres.py`；兼容 `db/imports/import_jp_evidence_package_to_postgres.py` | `db/ddl/030_create_edinet_jp_schema.sql` | `siq_jp`，可由 `SIQ_JP_PGDATABASE` 覆盖 | `edinet_jp` |
| 韩股 / KR | Wiki package 在 `/home/maoyd/siq-research-engine/data/wiki/kr/companies/.../reports/...`；PDF parser full json 源头在 `/home/maoyd/siq-research-engine/data/pdf-parser/results/<task_id>` | 默认读取 `/home/maoyd/siq-research-engine/data/pdf-parser/results/<task_id>/document_full.json`；package 仅作为证据展示和旧链路兼容 | `scripts/kr/build_kr_evidence_package.py` | 默认 `db/imports/import_kr_document_full_to_postgres.py`；兼容 `db/imports/import_kr_evidence_package_to_postgres.py` | `db/ddl/040_create_dart_kr_schema.sql` | `siq_kr`，可由 `SIQ_KR_PGDATABASE` 覆盖 | `dart_kr` |
| 欧股 / EU | Wiki package 在 `/home/maoyd/siq-research-engine/data/wiki/eu/companies/.../reports/...`；PDF parser full json 源头在 `/home/maoyd/siq-research-engine/data/pdf-parser/results/<task_id>` | 默认读取 `/home/maoyd/siq-research-engine/data/pdf-parser/results/<task_id>/document_full.json`；package 仅作为证据展示和旧链路兼容 | PDF: `scripts/eu/build_eu_pdf_evidence_package.py`; ESEF: `scripts/eu/build_eu_esef_evidence_package.py` | 默认 `db/imports/import_eu_document_full_to_postgres.py`；兼容 `db/imports/import_eu_evidence_package_to_postgres.py` | `db/ddl/050_create_eu_ifrs_schema.sql` | `siq_eu`，可由 `SIQ_EU_PGDATABASE` 覆盖 | `eu_ifrs` |

## PostgreSQL 入库取数明细

| 市场 / 类型 | importer 实际读取的数据路径 | 主要用途 |
| --- | --- | --- |
| A股 / CN | `/home/maoyd/siq-research-engine/data/pdf-parser/results/<task_id>/document_full.json` | 主输入文件。 importer 从其中读取 `task`、`source_files`、`markdown`、`content_list`、`content_list_enhanced`、`quality_report`、`financial_data`、`financial_checks`、`table_relations`、`resources`、`artifacts` 等字段，写入 `pdf2md` schema。 |
| A股 / CN | `data/wiki/companies/<stock_code>-<company>/company.json` | 辅助匹配公司主数据。 importer 会用 Wiki 公司索引补齐 `company_id`、股票代码、公司简称、报告归属等信息；匹配不到时从文件名推断。 |
| 通用文档 | `<package_dir>/manifest.json` | package 主索引，确认 `schema_version=generic_document_package_v1`，生成 document / parse run identity。 |
| 通用文档 | `<package_dir>/qa/parse_manifest.json`; `<package_dir>/qa/quality_report.json`; `<package_dir>/qa/source_map.json` | 解析元数据、质量状态、证据坐标。若 package 内缺文件，会按 `artifact_manifest.json` / `source_result_dir` 回源到原始解析目录。 |
| 通用文档 | `<package_dir>/sections/blocks.json`; `<package_dir>/tables/tables.json`; `<package_dir>/logical_tables/logical_tables.json`; `<package_dir>/logical_tables/table_relations.json`; `<package_dir>/figures/figures.json`; `<package_dir>/extraction/*.json` | 块、表格、逻辑表、表间关系、图像、抽取结果入库。 |
| 美股 / US SEC | `<package_dir>/manifest.json`; `<package_dir>/qa/quality_report.json`; `<package_dir>/qa/extraction_warnings.json` | 公司、filing、parse run、质量状态和告警。 |
| 美股 / US SEC | `<package_dir>/sections.json`; `<package_dir>/tables/table_index.json`; `<package_dir>/qa/source_map.json` | SEC 分节、HTML 表格索引、证据引用。 |
| 美股 / US SEC | `<package_dir>/xbrl/contexts.json`; `<package_dir>/xbrl/units.json`; `<package_dir>/xbrl/facts_raw.json`; `<package_dir>/metrics/normalized_metrics.json`; `<package_dir>/metrics/operating_metrics.json` | XBRL context/unit/raw facts、标准化财务事实和运营指标。 |
| 港股 / HK | `<package_dir>/manifest.json`; `<package_dir>/qa/quality_report.json`; `<package_dir>/metrics/financial_data.json`; `<package_dir>/qa/source_map.json` | 公司、filing、parse run、质量状态、结构化财务报表项、证据坐标。 |
| 港股 / HK | `<package_dir>/sections/report.md`; `<package_dir>/tables/table_index.json`; `<package_dir>/metrics/normalized_metrics.json`; `<package_dir>/metrics/financial_checks.json` | 报告正文 section、PDF 页/表格索引、标准化指标、财务勾稽检查。 |
| 港股 / HK | `<package_dir>/parser/document_full.json`; `<package_dir>/parser/content_list_enhanced.json`; `<package_dir>/parser/table_relations.json`; `<package_dir>/sections/report_complete.md` | 来自 PDF parser 的完整解析产物。当前 importer 会把 package 内文件作为 artifact 记录；结构化事实主要从 `metrics`、`qa`、`tables`、`sections` 读取。 |
| 日股 / JP | `<package_dir>/manifest.json`; `<package_dir>/qa/quality_report.json`; `<package_dir>/tables/table_index.json`; `<package_dir>/qa/source_map.json` | 公司/filing/parse run、质量状态、PDF 表格、证据坐标。 |
| 日股 / JP | `<package_dir>/xbrl/facts_raw.json`; `<package_dir>/metrics/normalized_metrics.json`; `<package_dir>/metrics/financial_checks.json` | EDINET/XBRL 原始 facts、标准化财务事实、勾稽检查。 |
| 韩股 / KR | `<package_dir>/manifest.json`; `<package_dir>/qa/quality_report.json`; `<package_dir>/tables/table_index.json`; `<package_dir>/qa/source_map.json` | 公司/filing/parse run、质量状态、PDF 表格、证据坐标。 |
| 韩股 / KR | `<package_dir>/xbrl/facts_raw.json`; `<package_dir>/metrics/normalized_metrics.json`; `<package_dir>/metrics/financial_checks.json` | DART/XBRL 原始 facts、标准化财务事实、勾稽检查。 |
| 欧股 / EU | `<package_dir>/manifest.json`; `<package_dir>/qa/quality_report.json`; `<package_dir>/sections/section_index.json` 或 `<package_dir>/sections/report.md`; `<package_dir>/tables/table_index.json` | 公司/filing/parse run、质量状态、section、PDF/HTML 表格。 |
| 欧股 / EU | `<package_dir>/xbrl/contexts.json`; `<package_dir>/xbrl/units.json`; `<package_dir>/xbrl/facts_raw.json`; `<package_dir>/qa/source_map.json`; `<package_dir>/metrics/normalized_metrics.json`; `<package_dir>/metrics/financial_checks.json` | ESEF/XBRL context/unit/raw facts、证据坐标、标准化财务事实、勾稽检查。 |

说明：full json 源解析产物确实在 `/home/maoyd/siq-research-engine/data/parser-results` 和 `/home/maoyd/siq-research-engine/data/pdf-parser/results` 两个根目录下。非 A 股市场的新 PostgreSQL 默认链路已经切换为直接读取这些 `document_full.json`；`data/wiki/<market>/companies/.../reports/<report_id>` package 继续用于 Agent/UI 证据展示、公司目录和旧 importer 兼容，不再作为 PostgreSQL 事实层默认主输入。

## 后续目标方案：统一 `document_full.json` 入库链路

目标：其他市场后续 PostgreSQL 抽取参考 A 股链路，以 `document_full.json` 作为唯一主解析输入；各市场只实现自己的抽取规则和字段映射规则，不再把 `metrics/qa/tables/xbrl` 等 package 分散文件作为 PostgreSQL 事实层的主输入。

### 目标原则

| 原则 | 说明 |
| --- | --- |
| 主输入统一 | PostgreSQL importer 的主输入统一为 `document_full.json`。PDF 市场读取 `/home/maoyd/siq-research-engine/data/pdf-parser/results/<task_id>/document_full.json`；US SEC HTML 读取 `/home/maoyd/siq-research-engine/data/parser-results/us-sec/<filing_id>/document_full.json`。 |
| 规则按市场拆分 | 每个市场保留独立抽取规则：CN / HK / JP / KR / EU / US 的报表标题、科目名、币种、单位、会计准则、XBRL/tag 映射、页码/表格证据规则可以不同。 |
| 欧洲多国家多币种 | EU 不是单一国家/单一币种市场。EU 规则必须记录 country、exchange、ISIN/LEI、reporting_currency、fact_currency、raw_unit、scale；不能默认所有欧股都是 EUR，也不能把 GBP/CHF/SEK/DKK/NOK/PLN 等原币事实混入同一数值口径。 |
| 指标和单位分层归一 | 每个市场/国家都要做指标、单位、币种、期间的归一化，但只能作为派生增强层；原始科目、原始值、原始单位和原始证据必须保留。同一市场内不同公司的差异指标不能硬塞进通用口径，应先沉淀通用核心指标，再兼容市场/国家/行业/公司差异化指标。 |
| loader 尽量共享 | 读取 `document_full.json`、生成 `company / filing / parse_run / artifacts / pages / tables / facts / quality / chunks` 的通用写库流程尽量复用；差异放在 market rules。 |
| package 降级为证据与展示层 | `data/wiki/<market>/companies/.../reports/<report_id>` package 继续作为 Agent / UI / evidence 展示入口，但 PostgreSQL 事实抽取不再依赖 package 内的 `metrics/qa/tables/xbrl` 分散文件。 |
| identity enrichment 可旁路 | 类似 A 股读取 `company.json` 补公司身份，其他市场也可以读取公司 catalog / metadata 补充 identity；但财务事实、表格、质量、证据坐标应来自 `document_full.json`。 |
| 幂等与可回放 | 同一个 `document_full.json` 多次入库应稳定生成同一组 company / filing / parse_run 逻辑身份，并先删除旧 run 子表数据再写入。 |

### 指标和单位归一化策略

深度参考 A 股现有做法：原始事实表忠实保存 `financial_data.statements` / `key_metrics`；三大表拆分表便于 SQL 查询；`financial_all_metrics_wide` 负责 Agent 快速召回；`financial_items_enriched` 作为只追加派生层，登记 canonical、metric_family、unit_standardized、value_standardized、period、quality_flags，不覆盖原始事实。

| 层级 | 目标 | 典型字段 / 表 |
| --- | --- | --- |
| 原始事实层 | 忠实保存报告中出现的项目和值，不做破坏性改写。 | `item_name`、`metric_name`、`raw_value`、`value`、`raw_unit`、`currency`、`source`、`raw_item/raw_metric`。 |
| 通用核心指标层 | 先归一所有市场都高频需要的指标，支撑 Agent 稳定查询和跨公司基础比较。 | revenue、gross_profit、operating_profit、net_profit、total_assets、total_liabilities、equity、operating_cash_flow、cash_and_equivalents、basic_eps、roe 等。 |
| 市场/国家指标层 | 处理 IFRS/HKFRS/JGAAP/K-GAAP/US GAAP/ESEF taxonomy、本地语言科目和披露习惯。 | `market_canonical_name`、`country_canonical_name`、`taxonomy_tag`、`accounting_standard`、`rule_id`。 |
| 行业/公司差异层 | 保留行业 KPI、公司自定义口径、非通用运营指标，不强行并入通用核心指标。 | `differentiated_metrics` 或 `operating_metric_facts`，带 `industry`、`metric_scope`、`company_id`、`raw_definition`、`quality_flags`。 |
| 宽表/检索层 | 把通用和差异指标聚合为 Agent 友好的 JSONB/检索 chunk。 | `financial_all_metrics_wide` 等价层、`document_chunks`、`evidence_citations`。 |

单位归一化也按 A 股 `financial_items_enriched` 思路处理：

| 规则 | 说明 |
| --- | --- |
| 原始单位永远保留 | `raw_unit` / `unit_raw`、`raw_value`、`value_extracted` 不被覆盖。 |
| 标准化值只做派生 | 可信规则才填 `unit_standardized`、`unit_scale`、`value_standardized`；无法判断时保留 raw 并打 `unit_missing` / `unit_unmapped`。 |
| 金额、每股、百分比不可混算 | 金额类、per-share、ratio/percentage、数量类指标分开归一，不能只靠 `canonical_name` 混在一起比较。 |
| 多币种不能静默折算 | 原币事实以 `fact_currency/reporting_currency` 查询；跨币种比较需要另建可追溯折算派生字段，记录 `converted_currency`、`fx_rate_date`、`fx_rate_source`。 |

### 建议新增/改造的脚本结构

| 路径 | 角色 | 说明 |
| --- | --- | --- |
| `db/imports/import_market_document_full_to_postgres.py` | 统一入口 | 新增通用 importer：接收 `--market`、`--document-full` 或 `--results-root`，读取 `document_full.json` 后分发到市场规则。 |
| `db/imports/market_document_full_rules/base.py` | 规则接口 | 定义各市场规则模块共同返回的数据结构，如 company、filing、statement_items、facts、tables、quality、chunks。 |
| `db/imports/market_document_full_rules/cn.py` | A 股规则 | 可先包裹或迁移现有 `import_document_full_to_postgres.py` 的抽取逻辑，作为基准实现。 |
| `db/imports/market_document_full_rules/hk.py` | 港股规则 | 从 `document_full.financial_data`、`content_list_enhanced`、`quality_report`、`table_relations` 中抽取 HK 报表、指标、表格和证据坐标。 |
| `db/imports/market_document_full_rules/jp.py` | 日股规则 | 处理 JP 年报标题、EDINET/证券代码、日文科目、IFRS/JGAAP 口径和单位。 |
| `db/imports/market_document_full_rules/kr.py` | 韩股规则 | 处理 DART 公司身份、韩文科目、KR 报表结构、单位和期间口径。 |
| `db/imports/market_document_full_rules/eu.py` | 欧股规则 | 处理 ESEF/IFRS tag、国家/交易所/ISIN/LEI、HTML/PDF 表格混合来源、多币种和 reporting unit。 |
| `db/imports/market_document_full_rules/us_sec.py` | 美股 SEC 规则 | 处理 `data/parser-results/us-sec/<filing_id>/document_full.json` 的 `filing`、`facts`、`tables`、`sections`、`relations`。 |
| `db/imports/tests/test_import_market_document_full_to_postgres.py` | 统一入口测试 | 验证 market dispatch、路径解析、幂等 parse_run、禁止误读 package 分散文件。 |
| `db/imports/tests/test_market_document_full_rules_<market>.py` | 市场规则测试 | 每个市场至少使用一个真实/fixture `document_full.json` 验证核心财务事实、表格页码和证据字段。 |

### 统一入口建议

```bash
# A 股：继续支持现有路径，也可迁移到统一入口
python3 db/imports/import_market_document_full_to_postgres.py \
  --market CN \
  --document-full /home/maoyd/siq-research-engine/data/pdf-parser/results/<task_id>/document_full.json \
  --ddl

# 港/日/韩/欧：从 PDF parser results 的 document_full.json 入库
python3 db/imports/import_market_document_full_to_postgres.py \
  --market HK \
  --document-full /home/maoyd/siq-research-engine/data/pdf-parser/results/<task_id>/document_full.json \
  --ddl

# 美股 SEC：从 SEC parser results 的 document_full.json 入库
python3 db/imports/import_market_document_full_to_postgres.py \
  --market US \
  --document-full /home/maoyd/siq-research-engine/data/parser-results/us-sec/<filing_id>/document_full.json \
  --ddl
```

批量导入建议：

```bash
# PDF parser 多市场批量：按 document_full.task / metadata 推断 market
python3 db/imports/import_market_document_full_to_postgres.py \
  --results-root /home/maoyd/siq-research-engine/data/pdf-parser/results \
  --recursive \
  --ddl

# SEC 批量
python3 db/imports/import_market_document_full_to_postgres.py \
  --market US \
  --results-root /home/maoyd/siq-research-engine/data/parser-results/us-sec \
  --recursive \
  --ddl
```

### 规则接口设计

各市场规则模块建议只做“从 `document_full.json` 到标准中间对象”的转换，不直接执行 SQL。

```python
class MarketDocumentFullRule:
    market: str

    def detect(self, document_full: dict, path: Path) -> bool:
        ...

    def build_company(self, document_full: dict, context: ImportContext) -> dict:
        ...

    def build_filing(self, document_full: dict, company: dict, context: ImportContext) -> dict:
        ...

    def build_parse_run(self, document_full: dict, filing: dict, context: ImportContext) -> dict:
        ...

    def build_tables(self, document_full: dict, context: ImportContext) -> list[dict]:
        ...

    def build_financial_facts(self, document_full: dict, context: ImportContext) -> list[dict]:
        ...

    def build_quality(self, document_full: dict, context: ImportContext) -> dict:
        ...

    def build_retrieval_chunks(self, document_full: dict, context: ImportContext) -> list[dict]:
        ...
```

统一 loader 负责：

| loader 职责 | 说明 |
| --- | --- |
| 连接目标数据库 | 根据 market 路由到 `siq` / `siq_us` / `siq_hk` / `siq_jp` / `siq_kr` / `siq_eu`。 |
| 校验目标 schema | CN 写 `pdf2md`；US 写 `sec_us`；HK 写 `pdf2md_hk`；JP 写 `edinet_jp`；KR 写 `dart_kr`；EU 写 `eu_ifrs`。 |
| 执行 DDL | `--ddl` 时执行对应 DDL。后续可把市场 DDL 向统一宽表/事实表结构靠拢，但不要求一步完成。 |
| 幂等删除旧数据 | 以 `parse_run_id` 或 `task_id + filing_id` 删除子表旧数据。 |
| 写入标准行 | 写 company、filing、parse_run、artifacts、pages、tables、facts、checks、citations、chunks。 |

### 各市场规则要点

| 市场 | `document_full.json` 来源 | 规则重点 | PostgreSQL 目标 |
| --- | --- | --- | --- |
| CN | `data/pdf-parser/results/<task_id>/document_full.json` | 沿用 A 股现有逻辑：中文三大表、A 股代码/交易所、CN GAAP 科目标准化、`financial_data.statements` 和 `key_metrics`。 | `siq.pdf2md` |
| HK | `data/pdf-parser/results/<task_id>/document_full.json` | 港股代码、HKEX ticker、IFRS/HKFRS、英文/中文报表标题、港币/人民币单位、`financial_data.statements`、`content_list_enhanced.tables` 证据页码。 | `siq_hk.pdf2md_hk` |
| JP | `data/pdf-parser/results/<task_id>/document_full.json` | EDINET code / security code、日文科目、IFRS/JGAAP、百万/千日元单位、年报/有価証券報告書识别。 | `siq_jp.edinet_jp` |
| KR | `data/pdf-parser/results/<task_id>/document_full.json` | DART corp code / stock code、韩文科目、KRW 单位、business report 结构、IFRS/K-GAAP 口径。 | `siq_kr.dart_kr` |
| EU | `data/pdf-parser/results/<task_id>/document_full.json` | country、exchange、ticker、ISIN、LEI、ESEF/IFRS tag、PDF/HTML 表格混合来源、多国家 reporting unit 和多币种字段；不得假定 EUR。 | `siq_eu.eu_ifrs` |
| US SEC | `data/parser-results/us-sec/<filing_id>/document_full.json` | SEC filing identity、CIK/ticker、10-K、inline XBRL facts、contexts、units、sections、HTML tables。 | `siq_us.sec_us` |

### 迁移步骤

| 阶段 | 动作 | 结果 |
| --- | --- | --- |
| 1. 固化 A 股基准 | 把 `import_document_full_to_postgres.py` 的抽取逻辑梳理为 CN rule 或 adapter；保留现有命令兼容。 | A 股行为不变，成为其他市场参考实现。 |
| 2. 建统一入口 | 新增 `import_market_document_full_to_postgres.py`，支持 `--market`、`--document-full`、`--results-root --recursive`。 | 所有市场可以从 full json 触发入库。 |
| 3. HK 先迁移 | HK 已有 PDF parser `document_full.json` 和 package 两套产物，优先实现 `hk.py`，对比现有 `import_hk_evidence_package_to_postgres.py` 的入库结果。 | 验证“package 文件取数”可迁到“document_full 取数”。 |
| 4. JP/KR/EU 迁移 | 分别实现 `jp.py`、`kr.py`、`eu.py`，保留旧 importer 作为 fallback。 | 非 A 股 PDF 市场统一从 `data/pdf-parser/results/<task_id>/document_full.json` 入库。 |
| 5. US SEC 迁移 | 实现 `us_sec.py`，从 `data/parser-results/us-sec/<filing_id>/document_full.json` 入库。 | SEC HTML parser 也进入 full json 主输入模型。 |
| 6. API 路由切换 | 市场 PostgreSQL import API 从 package importer 切换到统一 document_full importer；package import 仅保留兼容入口。 | 前端/后端行为统一：先定位 `document_full.json`，再入库。 |

### 验收标准

| 验收项 | 标准 |
| --- | --- |
| 单一主输入 | 每个市场 PostgreSQL 抽取测试都能证明：财务事实、表格、质量、证据坐标来自 `document_full.json`，不是 package 内 `metrics/qa/tables/xbrl` 分散文件。 |
| A 股兼容 | 统一入口导入同一个 CN `document_full.json` 后，核心表行数和关键指标与现有 `import_document_full_to_postgres.py` 一致。 |
| 市场隔离 | CN 写 `siq.pdf2md`；US/HK/JP/KR/EU 分别写自己的 database/schema，不允许跨库误写。 |
| EU 多币种隔离 | EU 入库测试必须覆盖至少两个不同国家/币种样本，证明 EUR/GBP/CHF 等原币字段、scale 和 reporting unit 不被覆盖或混算。 |
| 证据可回溯 | 每条核心财务事实至少保留 `task_id` / `filing_id` / `table_index` / `page_number` / `source_path` 中可用字段。 |
| 幂等 | 重复导入同一个 `document_full.json` 不重复产生 facts/chunks/tables。 |
| 可渐进切换 | 旧 package importer 在迁移期保留，但新开发和 API 默认走 document_full importer。 |

## API 配置入口

| 配置内容 | 路径 |
| --- | --- |
| 多市场 Wiki root / build script / import script / 默认 database | `apps/api/services/market_report_settings.py` |
| 多市场 package build/import 参数拼装 | `apps/api/services/market_report_commands.py` |
| 市场 package import API 调用入口 | `apps/api/routers/market_reports.py` |
| A股 PDF parser `document_full.json` 入库 API 调用入口 | `apps/api/routers/workflow.py` |

## 数据库初始化

Docker Compose 默认主库为 `siq`：

```text
infra/docker/docker-compose.yml
POSTGRES_DB=siq
```

初始化脚本额外创建以下数据库：

```text
infra/docker/postgres-init/001_create_databases.sql
siq_app
siq_document_parser
siq_us
siq_hk
siq_jp
siq_kr
siq_eu
```

因此当前链路可以概括为：

| 类别 | database | schema |
| --- | --- | --- |
| A股完整 PDF parser 入库 | `siq` | `pdf2md` |
| 通用文档解析 package | `siq_document_parser` | `document_parser` |
| 非 A 股市场 package | `siq_us` / `siq_hk` / `siq_jp` / `siq_kr` / `siq_eu` | `sec_us` / `pdf2md_hk` / `edinet_jp` / `dart_kr` / `eu_ifrs` |
