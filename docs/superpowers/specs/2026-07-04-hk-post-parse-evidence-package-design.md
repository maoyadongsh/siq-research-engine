# HK 解析后证据包设计

日期：2026-07-04
状态：已确认，可进入实施计划
仓库：`/home/maoyd/siq-research-engine`

## 1. 设计边界

HK 应该在解析产物结构上对齐 A 股 PDF 解析后的增强合同，但抽取规则必须按 HK 市场单独设计。也就是说，工程合同可以复用，市场语义不能硬搬。HK 的会计 taxonomy、科目 alias、附注格式、币种单位、语言和行业 profile 都要按 HKEX/HKFRS/IFRS 的实际披露处理。

硬性规则：

- 不修改 A 股默认行为。
- 不把 HK 数据写入 `siq.pdf2md`。
- HK package 输出到 `data/wiki/hk/companies/<ticker>-<company>/reports/<report_id>`，路径语义对齐 A 股 `data/wiki/companies/<code>-<company>/reports/<report_id>`。
- HK PostgreSQL 目标是 `siq_hk.pdf2md_hk`。
- HK Milvus collection 目标是 `siq_hk_reports`。
- 财务数字只能来自解析表格、parser 产物或明确的人工修正记录，不能由 LLM 猜数。
- 每个核心 fact 必须能回溯到 PDF 页码、表格、行、列、quote_text 和来源产物。

## 2. 需要复用的 A 股产物合同

A 股 PDF 解析后的增强链路是：

```text
MinerU/VLM result
  -> 带 PDF 页码标记的 Markdown
  -> 从 content_list 回填稀疏页
  -> content_list_enhanced.json
  -> result_complete.md
  -> table_relations.json
  -> financial_data.json
  -> financial_checks.json
  -> quality_report.json
  -> document_full.json
  -> Wiki / PostgreSQL / 前端证据溯源
```

HK 应保留同类产物和同类用户能力：

- PDF 页码标记和稀疏页补全。
- 表格增强来源信息：`table_index`、PDF 页码、bbox、caption、footnote、source image、confidence。
- 脚注引用、脚注定义、脚注绑定关系。
- TOC、标题、content heading 索引。
- 财务主表项目到附注标题的关联。
- `report_complete.md`，在原始 Markdown 后追加可恢复 PDF 结构信息。
- `table_relations.json`，支持物理表/逻辑表、跨页表、拆分合并和人工修正。
- `quality_report.json`，汇总 parser、结构、evidence 和财务勾稽质量。
- `financial_checks.json`，输出 pass、warning、fail 及原因。

## 3. HK 不能硬搬 A 股的规则

HK 抽取规则必须符合 HKEX PDF 的真实披露情况：

- 会计准则：HKFRS、IFRS，部分发行人可能出现 CASBE。
- 语言：英文、繁体中文、简体中文、双语表格。
- 币种和单位：HKD、RMB、USD；`million`、`thousand`、`HK$ million`、`RMB million`、`千元`、`百万元`。
- 报表标题：
  - `Consolidated statement of financial position`
  - `Consolidated income statement`
  - `Consolidated statement of profit or loss`
  - `Consolidated statement of profit or loss and other comprehensive income`
  - `Consolidated statement of cash flows`
  - `Consolidated statement of changes in equity`
  - `Notes to the consolidated financial statements`
- 附注引用：`Note 5`、`Notes 5(a)`、数字附注列、英文附注标题、双语附注标题。
- 行业 profile：general、bank、insurance、property、energy、internet_platform、manufacturing、retail。
- 银行和保险不能强行套普通工商企业的资产负债表、现金流和利润表规则。

## 4. HK Evidence Package V2 结构

HK package 使用市场独立根路径，但目录语义与 A 股公司 Wiki 对齐：

```text
data/wiki/hk/
  _meta/
    company_catalog.json
    AGENT_GUIDE.md
  companies/
    <ticker>-<company>/
      company.json
      _index.json
      reports/
        <fiscal_year>-<report_type>-<filing_key>/
          manifest.json
          README.md
          raw/
            report.pdf
            report.metadata.json
          sections/
            report.md
            report_complete.md
            section_index.json
          parser/
            document_full.json
            content_list_enhanced.json
            table_relations.json
            quality_report.json
            financial_data.json
            financial_checks.json
          tables/
            table_index.json
            table_0001.json
            table_0002.json
          metrics/
            financial_data.json
            financial_checks.json
            load_plan.json
            normalized_metrics.json
            operating_metrics.json
          qa/
            quality_report.json
            source_map.json
            extraction_warnings.json
            footnotes.json
            toc.json
            financial_note_links.json
            table_quality_signals.json
```

`parser/` 保存 parser 侧原始增强产物，作为 provenance。`metrics/` 保存 HK 规则归一化后的市场事实。`qa/` 保存 evidence、溯源、warning 和审计辅助信息。

旧 `data/wiki/hk_reports` 仅作为迁移兼容路径，不作为长期主路径。

## 5. 主体身份和主键规则

HK 公司身份应沿用 A 股原则：市场代码是业务锚点，`company_id` 是稳定技术 ID。

推荐标识：

```text
company_id = HK:<5 位 HKEX code>          示例：HK:00700
ticker = <5 位 HKEX code>                 示例：00700
stock_code = <5 位 HKEX code>             示例：00700
filing_id = HK:<ticker>:<accession>        示例：HK:00700:12100024
parse_run_id = filing_id + parser_version + rules_version + artifact hashes 的稳定 hash
evidence_id = hk:<filing_id>:p<page>:t<table>:r<row>:c<column>
```

名称和别名是属性，不是主键：

- `company_name`
- `short_name`
- `company_full_name`
- `exchange`
- `aliases`

公司名称、公司简称不能作为主键，因为语言、拼写、简称和发行人命名都可能变化。

## 6. HK PostgreSQL 需要补齐的内容

现有 `pdf2md_hk` 已包含主要市场表：

- `companies`
- `filings`
- `parse_runs`
- `artifacts`
- `filing_sections`
- `pdf_pages`
- `pdf_tables`
- `evidence_citations`
- `financial_facts`
- `operating_metric_facts`
- `financial_checks`
- `quality_reports`
- `retrieval_chunks`

为对齐 A 股解析后增强产物，需要新增或扩展：

- 公司主数据增强字段：`stock_code`、`short_name`、`company_full_name`、`exchange`、`aliases`。
- `content_blocks` 或等价的轻量 page/block 索引。
- `footnotes`。
- `toc_entries`。
- `financial_note_links`。
- `table_relations`。
- parser artifact 引用，用于保存 `document_full`、`content_list_enhanced`、`table_relations`、parser 原始 quality/financial 产物。

Importer 必须幂等。同一个 package 连续导入两次，不允许重复插入 facts、evidence、tables 或 parser artifacts。

## 7. HK 抽取层

HK 抽取层运行在 parser enhancement 之后、DB/vector ingest 之前。

输入：

- PDF 路径和 metadata。
- `document_full.json`。
- `content_list_enhanced.json`。
- 可用时读取 `table_relations.json`。
- parser 侧 `quality_report.json`。
- 可选 correction records。

输出：

- HK 归一化后的 `financial_data.json`。
- HK `financial_checks.json`。
- evidence `source_map.json`。
- HK `quality_report.json`。
- 带稳定 ID 和 artifact hashes 的完整 `manifest.json`。

规则模块按职责拆分：

- statement detection。
- period column detection。
- unit/currency normalization。
- alias matching。
- industry profile handling。
- note-link extraction。
- evidence generation。
- quality and check aggregation。

## 8. 质量门禁

P0 package 质量门禁：

- package 包含必需文件，并能通过 common market evidence package contract 校验。
- 表格有稳定 table ID，并尽量具备 page/table 坐标。
- 核心抽取 fact 有 evidence coverage。
- parser warnings 被保留。
- HK rule warnings 和 parser warnings 分开展示。
- 财务勾稽状态为 `pass`、`warning` 或 `fail`，并带原因。
- SQL 能从 fact 追到 `evidence_citations`，再追回 package/PDF 位置。

P1 质量门禁：

- `content_list_enhanced` 的 exact/inferred/missing page rate 可见。
- footnote 和 note-link 统计可见。
- table relation/correction 状态可见。
- Milvus chunk metadata 能反查 `siq_hk.pdf2md_hk.evidence_citations`。

## 9. 初始验收样本

P0 用 5 个差异明显的 HK 发行人做验收：

- `00700` 腾讯：互联网平台，英文年报。
- `01299` 友邦保险：保险。
- `00981` 中芯国际：制造/半导体。
- `03988` 中国银行：银行。
- `09988` 阿里巴巴-W：互联网平台，披露复杂。

每个样本必须满足：

- package detail 可在 `/parse-hk` 加载。
- package 可导入 `siq_hk.pdf2md_hk`。
- facts 能 join 到 evidence citations。
- quality report 能区分 parser warnings 和 rule warnings。
- Milvus dry-run 生成带 `market=HK`、`filing_id`、`parse_run_id` 和 evidence metadata 的 chunks。

## 10. 推荐实施顺序

1. 增加 HK package V2 writer，把与 A 股对齐的 parser/enhancement 产物复制或引用进 package。
2. 扩展 HK DDL/importer，支持公司身份增强字段和增强产物表。
3. HK importer 默认目标调整为 `siq_hk`，同时保留显式 database URL 覆盖能力。
4. 跑 5 个样本的 package rebuild 和 DB import smoke。
5. 接通 `/parse-hk` package panel 的真实 package/detail/import/vector 状态。
6. 增强 HK note-link 和行业专属抽取规则。
7. 扩展到现有 50 个 HK package，并生成质量缺口报告。
