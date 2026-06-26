# SIQ Citation Contract v1

只要回答涉及财报、财务指标、经营分析、风险判断、事实核查、持续跟踪或数据库/Wiki/PDF 解析结果，就必须执行本契约。

## 必须绑定引用的内容

- 财务数字、同比/环比、比率、排名、行业对比、模型计算输入和输出。
- 年报原文表述、管理层讨论、风险因素、审计意见、治理/合规事项。
- 盈利质量、现金流质量、偿债能力、资产质量、经营拐点、风险等级等判断。
- Wiki 指标、PostgreSQL 查询结果、PDF 解析结果、事实核查结果和跟踪预警。

## 禁止事项

- 不允许输出没有来源的具体数字、页码、表格编号、报告编号或数据库记录。
- 不允许编造 `report_id`、`task_id`、`evidence_id`、PDF 页码、`table_index`、`md_line`、URL 或文件路径。
- 不允许把模型推论伪装成已验证事实。
- 证据链缺失或不完整时，不得强行下确定性结论；必须写明“证据不足”或“证据链不完整”。

## 对话引用格式

除完整报告中已包含“数据质量与溯源声明/关键证据索引”外，任何普通对话回答、局部分析、短答、核查摘要或跟踪摘要，只要包含财报事实或判断，末尾必须追加：

```markdown
## 引用来源

[1] source_type=wiki_metrics, file=..., metric=..., period=..., evidence_id/task_id=..., pdf_page=..., table_index=..., md_line=...
[2] source_type=postgresql, table=..., statement_id=..., period_key=..., task_id=..., pdf_page=..., table_index=...
```

字段未知时必须写 `未返回`，不得猜测。若完全没有可用证据，引用区写：

```markdown
## 引用来源

证据不足：当前可用材料未返回可审计来源，无法支持确定性结论。
```

## PDF 页码与可打开链接

- 优先使用本地 Wiki 证据链字段：`pdf_page`、`printed_page`、`pdf_page_number`、`table_index`、`md_line`、`open_pdf_page_url`、`open_source_page_url`、`open_source_table_url`。
- 优先使用 `evidence/evidence_index.json` 中的 `pdf_page_number`、`table_index`、`md_line`、`task_id`、`open_pdf_page_url`、`open_source_page_url`、`open_source_table_url`。
- 若使用 `metrics/three_statements.json`，必须读取其 `source/ref` 中的 `pdf_page`、`table_index`、`md_line`、`pdf_path`，并结合公司 `task_id` 生成页码链接。
- 若使用 `metrics/key_metrics.json` 且只返回 `table_index`，必须通过 `evidence/evidence_index.json`、`reports/<report_id>/document_full.json` 或 PostgreSQL `document_tables` 将 `table_index` 解析为 `pdf_page_number`；解析不到时必须写明“PDF 页码未返回/证据链不完整”。
- 禁止把可回溯但暂未补全的页码简单隐藏。若 `table_index`、`md_line`、`pdf_page`、`pdf_refs.json` 或 `document_full.json` 足以推回 PDF 页码，必须先补全 `pdf_page_number`、`open_pdf_page_url`、`open_source_page_url`、`open_source_table_url`，再渲染报告。
- 当前统一解析器为 `/home/maoyd/.hermes/profiles/shared/scripts/local_citations.py`。引用出现 `未返回`、只有 `table_index`、或只有 `wiki_metrics/wiki_evidence/wiki_analysis/wiki_semantic` 文件线索时，应按该解析器路径回溯：`company.json -> task_id/report_id -> metrics/evidence/semantic -> reports/<report_id>/document_full.json/pdf_refs.json -> pdf_page/table/md_line`。
- 纯 `report.md` 原文引用以 `md_line` 附近向上最近的 `[PDF_PAGE: n]` 作为 Markdown 到 PDF 的权威页码锚点；结构化证据来自 `metrics/*.json`、`evidence/*.json`、`semantic/document_links.json` 或 `semantic/note_links.json` 时，优先使用结构化记录自带的 `pdf_page_number` 和 `table_index`。
- 表格跨页或页码标记轻微漂移时，邻近页差异可接受；但必须保留 `table_index` 和 `/api/source/{task_id}/table/{table_index}` 表格入口。若结构化页码与 `report.md` 锚点不同，标注 `pdf_page_conflict`、`table_pdf_page` 或 `table_index_conflict`，不得把 fallback 页码伪装成结构化来源页。
- 不允许在可解析页码的情况下只引用 `table_index`；最终引用必须包含 `pdf_page/pdf_page_number` 或显式说明未返回。
- 若证据记录已含 `open_pdf_page_url`，直接使用；否则按 `/api/pdf_page/{task_id}/{pdf_page_number}` 生成“打开PDF页”链接，并按 `/api/source/{task_id}/page/{pdf_page_number}`、`/api/source/{task_id}/table/{table_index}` 生成“查看页来源/查看表格”链接。
- 完整 HTML 报告中的 `PDF`、`页来源`、`表格` 等 `/api/pdf_page` 与 `/api/source` 链接必须写成新标签/新窗口打开：`target="_blank" rel="noopener noreferrer"`。不得让证据链接在长滚动报告页内原地跳转，避免用户返回报告时被保留在错误滚动位置。
- 远程互联网入口发布前，必须对最终 URL 对应的 HTML 做同样检查：所有 `/api/pdf_page` 与 `/api/source` 链接都应可正常打开、无 `/None`、`/unknown`、`pNone`、`punknown`，且全部带新标签属性。

推荐引用格式：

```markdown
[1] source_type=wiki_evidence, file=..., metric=..., period=..., task_id=..., pdf_page=132, table_index=89, md_line=2497，[打开PDF页](/api/pdf_page/<task_id>/132)，[查看页来源](/api/source/<task_id>/page/132)，[查看表格](/api/source/<task_id>/table/89)
```

## 输出前自检

- 普通对话和局部分析优先用 Markdown 列表、紧凑表格或短分节展示；能列表展示的结论、依据、差异、风险和后续动作，不要写成大段纯文字。
- 是否出现关键数字但没有引用来源。
- 是否出现风险/经营判断但没有至少一个支撑证据。
- 引用来源是否能回到 Wiki 文件、PostgreSQL 表、PDF 页码、表格编号、Markdown 行或 task_id。
- 是否把缺失字段、口径冲突、证据链不完整显式写出。

若自检不通过，必须重写；仍无法补齐时，输出“现有材料不足以支持该结论”。
