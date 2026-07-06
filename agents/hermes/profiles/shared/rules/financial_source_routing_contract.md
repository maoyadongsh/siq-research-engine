# Financial Source Routing Contract

本契约用于固化财报问答的证据召回顺序，优先级高于普通写作偏好。凡涉及三大表、附注、主表项目展开、原值/准备/净额勾稽的问题，所有 Hermes profile 都必须执行。

## 核心原则

1. 先解析公司和报告期，再按问题类型路由；不得用一个线性顺序回答所有财务问题。
2. 主表数值、账面价值、账面净值、净额、余额、利润、现金流、资产负债项目，第一事实源必须是 `metrics/reports/<report_id>/three_statements.json`、`key_metrics.json`、`validation.json`，并结合 `evidence/evidence_index.json` 回到正文主表 PDF 页和 `table_index`。
3. 附注明细、构成、原值、减值准备、坏账准备、跌价准备、累计折旧/摊销、账龄、前五名、资产组、可收回金额、变动原因，才进入 `semantic/document_links.json`、`semantic/note_links.json` 或 `note_detail_lookup.py`。
4. `report.md` 全文命中只用于补上下文、补页码、补表格或交叉验证；`document_full.json` 和 PostgreSQL/pdf2md 只在深度审计、证据补全失败、口径冲突或用户明确要求数据库时兜底。全文/RAG 切片不得替代主表数值来源。
5. 同一问题同时包含主表口径和附注口径时，必须双链路召回并双来源引用；不得因为附注已命中就跳过主表，也不得用附注表替代主表项目。

## 混合口径强制规则

以下问题属于“主表项目 -> 附注展开”的混合检索，不是单纯附注问题：

- 商誉：`账面价值/账面净值/净额/余额/主表/资产负债表商誉` 必须先命中三大表 `goodwill/商誉`；`账面原值/减值准备/减值损失/资产组/可收回金额/构成/变动` 再命中附注表。若用户同时询问“账面价值/原值/减值准备”，答案必须包含主表净额和附注原值、减值准备三类来源。
- 应收账款、其他应收款、合同资产：主表余额/账面价值先命中三大表；账龄、坏账准备、前五名、单项/组合计提再命中附注。
- 存货、固定资产、无形资产、长期股权投资：账面价值/净额先命中三大表；原值、累计折旧/摊销、跌价/减值准备、变动表再命中附注。
- 借款、合同负债、收入成本等主表项目：主表余额/发生额先命中三大表；到期结构、明细分类、地区/产品/客户拆分、变动原因再命中附注或管理层讨论。

## 输出与校验

- 引用来源中必须分别保留 `source_type=wiki_metrics, file=metrics/three_statements.json` 和附注来源 `source_type=wiki_document_links` / `wiki_note_links` 的 `task_id/pdf_page/table_index/md_line`。
- 涉及原值/准备/净额关系时，必须按 `financial_calculation_contract.md` 调用 `financial_reconciliation_validator.py` 或后端同源函数；派生占比、同比、单位换算必须调用 `financial_calculator.py` 或后端同源函数。
- 若任何必需来源缺失，只能说明“证据链不完整”并标注缺口；不得把“未检索”写成“未披露”。
