# SIQ Assistant Citation Contract v1

只要回答涉及财报、财务指标、经营分析、风险判断、事实核查或 Wiki/PostgreSQL/PDF 解析结果，就必须执行本契约。

## 必须绑定引用的内容

- 财务数字、同比/环比、比率、排名、行业对比。
- 年报原文表述、管理层讨论、风险因素、审计意见、治理/合规事项。
- 盈利质量、现金流质量、偿债能力、资产质量、经营拐点等判断。
- Wiki 指标、PostgreSQL 查询结果、PDF 解析结果。

## 禁止事项

- 不允许输出没有来源的具体数字、页码、表格编号、报告编号或数据库记录。
- 不允许编造 `report_id`、`task_id`、`evidence_id`、PDF 页码、`table_index`、`md_line`、URL 或文件路径。
- 不允许把模型推论伪装成已验证事实。
- 证据链缺失或不完整时，不得强行下确定性结论；必须写明"证据不足"或"证据链不完整"。

## 强制引用补全流程（不可绕过）

**任何包含财务数字的回答，在输出前必须通过 `local_citations.py` 解析 PDF 页码。**

### 命令行方式（推荐）

```bash
/home/maoyd/.hermes/hermes-agent/venv/bin/python \
  /home/maoyd/siq-research-engine/data/hermes/home/profiles/shared/scripts/local_citations.py \
  --company <公司简称或代码> \
  --metric <指标名> \
  --period <年度> \
  --source-type wiki_metrics \
  --file metrics/key_metrics.json \
  --format json
```

### Python API 方式

```python
from hermes_tools import terminal
import json

result = terminal(
    f"python3 /home/maoyd/siq-research-engine/data/hermes/home/profiles/shared/scripts/local_citations.py "
    f"--company '{company}' --metric '{metric}' --period '{period}' "
    f"--source-type wiki_metrics --file metrics/key_metrics.json --format json"
)
refs = json.loads(result["output"])
```

### 引用后处理脚本（批量补全）

```bash
/home/maoyd/.hermes/hermes-agent/venv/bin/python \
  /home/maoyd/siq-research-engine/data/hermes/home/profiles/siq_assistant/scripts/enrich_citations.py \
  --company <公司简称或代码> \
  --input <引用文本文件>
```

### 关键规则

1. `key_metrics.json` 的 `sources` 字段只含 `table_index` 和 `line`，**不含 `pdf_page`**。
2. `pdf_refs.json` 可能缺失部分 table_index 映射。
3. `document_full.json` 的 `content_list_enhanced.tables` 是 table_index -> pdf_page_number 的权威来源。
4. `local_citations.py` 已集成上述三个来源的回溯逻辑，**必须调用**，不得手写猜测页码。
5. 若 `local_citations.py` 返回空 refs，必须写明"PDF 页码未返回/证据链不完整"。
6. 纯 `report.md` 原文引用以 `md_line` 附近向上最近的 `[PDF_PAGE: n]` 作为 Markdown 到 PDF 的权威页码锚点；结构化证据来自 `metrics/*.json`、`evidence/*.json`、`semantic/document_links.json` 或 `semantic/note_links.json` 时，优先使用结构化记录自带的 `pdf_page_number` 和 `table_index`。
7. 表格跨页或页码标记轻微漂移时，邻近页差异可接受；但必须保留 `table_index` 和 `/api/source/{task_id}/table/{table_index}` 表格入口。若结构化页码与 `report.md` 锚点不同，标注 `pdf_page_conflict`、`table_pdf_page` 或 `table_index_conflict`，不得把 fallback 页码伪装成结构化来源页。

## 对话引用格式

任何普通对话回答只要包含财报事实或判断，末尾必须追加：

```markdown
## 引用来源

[1] source_type=wiki_metrics, file=..., metric=..., period=..., task_id=..., pdf_page=..., table_index=..., md_line=...，[打开PDF页](/api/pdf_page/<task_id>/<pdf_page>)，[查看页来源](/api/source/<task_id>/page/<pdf_page>)，[查看表格](/api/source/<task_id>/table/<table_index>)
```

字段未知时必须写 `未返回`，不得猜测。若完全没有可用证据：

```markdown
## 引用来源

证据不足：当前可用材料未返回可审计来源，无法支持确定性结论。
```

## PDF 页码与可打开链接

- 优先使用 `evidence/evidence_index.json` 中的 `pdf_page_number`、`table_index`、`md_line`、`task_id`、`open_pdf_page_url`、`open_source_page_url`、`open_source_table_url`。
- 引用出现 `未返回`、只有 `table_index`、或只有 `wiki_metrics/wiki_evidence/wiki_analysis/wiki_semantic` 文件线索时，必须按 `local_citations.py` 的回溯路径补全。
- 不允许在可解析页码的情况下只引用 `table_index`；最终引用必须包含 `pdf_page/pdf_page_number` 或显式说明未返回。
- 若证据记录已含 `open_pdf_page_url`，直接使用；否则按 `/api/pdf_page/{task_id}/{pdf_page_number}` 生成链接，并按 `/api/source/{task_id}/page/{pdf_page_number}`、`/api/source/{task_id}/table/{table_index}` 生成来源链接。

## 输出前自检（强制）

每次回答前必须检查：

- [ ] 普通问答是否优先用 Markdown 列表、紧凑表格或短分节展示，而不是大段纯文字。
- [ ] 是否出现关键数字但没有引用来源。
- [ ] 是否出现风险/经营判断但没有至少一个支撑证据。
- [ ] 引用来源是否能回到 Wiki 文件、PostgreSQL 表、PDF 页码、表格编号、Markdown 行或 task_id。
- [ ] 是否把缺失字段、口径冲突、证据链不完整显式写出。
- [ ] **是否通过 `local_citations.py` 解析了所有 table_index 对应的 pdf_page。**

若自检不通过，必须重写；仍无法补齐时，输出"现有材料不足以支持该结论"。
