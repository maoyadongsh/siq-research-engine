# HK V2 5 样本 Smoke 报告

- 生成时间: `2026-07-04T10:11:53.845709+00:00`
- 样本根目录: `data/wiki/hk_reports`
- 聚合结论: **失败**
- 样本数: 5；通过: 0；警告: 0；失败: 5

## 样本摘要

| 样本 | 公司 | ticker | filing_id | quality | sections | tables | metrics | evidence | 状态 |
| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- |
| 00700/2025/annual_12100024 | TENCENT | 00700 | HK:00700:12100024 | fail | 1 | 0 | 0 | 0 | 失败 |
| 01299/2025/annual_12106543 | AIA | 01299 | HK:01299:12106543 | pass | 1 | 250 | 153 | 153 | 失败 |
| 00981/2025/annual_12097338 | SMIC | 00981 | HK:00981:12097338 | pass | 1 | 208 | 253 | 253 | 失败 |
| 03988/2025/annual_12132549 | BANK OF CHINA | 03988 | HK:03988:12132549 | fail | 1 | 234 | 112 | 112 | 失败 |
| 09988/2025/annual_11727038 | BABA-W | 09988 | HK:09988:11727038 | pass | 1 | 130 | 202 | 202 | 失败 |

## 失败与缺口

### 00700/2025/annual_12100024
- 状态: 失败
- 导入 dry run: validator 通过（未连接数据库，未写入数据）
- 主要 warnings: {'type': 'missing_statement', 'required_statement_status': {'balance_sheet': 'missing', 'income_statement': 'missing', 'cash_flow_statement': 'missing'}}; No parsed PDF tables were converted to ParsedTable.; No mapped HKEX/PDF table rows were extracted. Check table parsing quality or add issuer-specific aliases.; No mapped HKEX/PDF table rows were extracted. Check table parsing quality or add issuer-specific aliases.; Use standard three-statement bridge checks.
- 硬失败原因: metrics/normalized_metrics.json 中 metrics 为空; qa/source_map.json 中 entries 为空; quality_report overall_status 为 fail

### 01299/2025/annual_12106543
- 状态: 失败
- 导入 dry run: validator 通过（未连接数据库，未写入数据）
- 缺失 V2 文件: `sections/report_complete.md`, `parser/document_full.json`, `parser/content_list_enhanced.json`, `parser/table_relations.json`, `qa/footnotes.json`, `qa/toc.json`, `qa/financial_note_links.json`, `qa/table_quality_signals.json`
- package detail 缺少 V2 paths: `report_complete`, `document_full`, `content_list_enhanced`, `table_relations`, `footnotes`, `toc`, `financial_note_links`, `table_quality_signals`
- 主要 warnings: Use standard three-statement bridge checks.
- 硬失败原因: 缺失必需 V2 文件: sections/report_complete.md, parser/document_full.json, parser/content_list_enhanced.json, parser/table_relations.json, qa/footnotes.json, qa/toc.json, qa/financial_note_links.json, qa/table_quality_signals.json; package detail 缺少 V2 paths: report_complete, document_full, content_list_enhanced, table_relations, footnotes, toc, financial_note_links, table_quality_signals

### 00981/2025/annual_12097338
- 状态: 失败
- 导入 dry run: validator 通过（未连接数据库，未写入数据）
- 缺失 V2 文件: `sections/report_complete.md`, `parser/document_full.json`, `parser/content_list_enhanced.json`, `parser/table_relations.json`, `qa/footnotes.json`, `qa/toc.json`, `qa/financial_note_links.json`, `qa/table_quality_signals.json`
- package detail 缺少 V2 paths: `report_complete`, `document_full`, `content_list_enhanced`, `table_relations`, `footnotes`, `toc`, `financial_note_links`, `table_quality_signals`
- 主要 warnings: Use standard three-statement bridge checks.
- 硬失败原因: 缺失必需 V2 文件: sections/report_complete.md, parser/document_full.json, parser/content_list_enhanced.json, parser/table_relations.json, qa/footnotes.json, qa/toc.json, qa/financial_note_links.json, qa/table_quality_signals.json; package detail 缺少 V2 paths: report_complete, document_full, content_list_enhanced, table_relations, footnotes, toc, financial_note_links, table_quality_signals

### 03988/2025/annual_12132549
- 状态: 失败
- 导入 dry run: validator 通过（未连接数据库，未写入数据）
- 缺失 V2 文件: `sections/report_complete.md`, `parser/document_full.json`, `parser/content_list_enhanced.json`, `parser/table_relations.json`, `qa/footnotes.json`, `qa/toc.json`, `qa/financial_note_links.json`, `qa/table_quality_signals.json`
- package detail 缺少 V2 paths: `report_complete`, `document_full`, `content_list_enhanced`, `table_relations`, `footnotes`, `toc`, `financial_note_links`, `table_quality_signals`
- 主要 warnings: Use standard three-statement bridge checks.
- 硬失败原因: 缺失必需 V2 文件: sections/report_complete.md, parser/document_full.json, parser/content_list_enhanced.json, parser/table_relations.json, qa/footnotes.json, qa/toc.json, qa/financial_note_links.json, qa/table_quality_signals.json; package detail 缺少 V2 paths: report_complete, document_full, content_list_enhanced, table_relations, footnotes, toc, financial_note_links, table_quality_signals; quality_report overall_status 为 fail

### 09988/2025/annual_11727038
- 状态: 失败
- 导入 dry run: validator 通过（未连接数据库，未写入数据）
- 缺失 V2 文件: `sections/report_complete.md`, `parser/document_full.json`, `parser/content_list_enhanced.json`, `parser/table_relations.json`, `qa/footnotes.json`, `qa/toc.json`, `qa/financial_note_links.json`, `qa/table_quality_signals.json`
- package detail 缺少 V2 paths: `report_complete`, `document_full`, `content_list_enhanced`, `table_relations`, `footnotes`, `toc`, `financial_note_links`, `table_quality_signals`
- 主要 warnings: Use standard three-statement bridge checks.
- 硬失败原因: 缺失必需 V2 文件: sections/report_complete.md, parser/document_full.json, parser/content_list_enhanced.json, parser/table_relations.json, qa/footnotes.json, qa/toc.json, qa/financial_note_links.json, qa/table_quality_signals.json; package detail 缺少 V2 paths: report_complete, document_full, content_list_enhanced, table_relations, footnotes, toc, financial_note_links, table_quality_signals

## 下一步

- 重建 5 个 HK 样本为 V2 package，补齐 parser、report_complete 与 V2 QA artifacts。
- 补充 HK 指标 alias 和财务表格规则，确保 normalized_metrics 至少产出一条指标。
- 补齐 source_map 生成逻辑，确保每个指标有可追溯 evidence。
- 复查 quality_report 为 fail 的样本，优先补银行/保险等行业表格规则。
