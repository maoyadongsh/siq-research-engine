# Quality Gate

最终成功条件不是“文件已写入”，而是：

```bash
/home/maoyd/siq-research-engine/data/hermes/home/profiles/siq_analysis/scripts/validate_report_quality.py --prefix <analysis/report-prefix>
```

## 必须失败的情况

- 缺少 `.md/.json/.html` 任一文件。
- 非 `template_id=siq_analysis_report_v1.1`。
- JSON 不是固定 14 章，或章节顺序不一致。
- Markdown/HTML 不是固定 14 个 H2/report section。
- HTML section 不闭合。
- HTML 出现侧边目录导航（如 `nav-sidebar`、`nav-toggle`、`with-sidebar`、`nav-item`）。
- 未解析模板占位符。
- 可回溯页码未修复。
- 可回溯页码被隐藏而非补全。
- HTML 中 `/api/pdf_page` 或 `/api/source` 链接未使用 `target="_blank" rel="noopener noreferrer"`。
- HTML/Markdown/JSON 出现 `/None`、`/unknown`、`pNone`、`punknown` 等无效证据链接或证据 ID。
- 财务指标表混入 `task_id`、`pdf_page`、`table_index` 等证据元数据。
- 核心指标在源文件有三年值却被报告写成 `未返回`。
- JSON 章节内容过薄。
- 大量章节没有 `evidence_ids`。
- 缺少图表可视化。
- 缺少 PDF 链接。
- `quality_report.module_count != 14`。
- `quality_report.section_order_valid != true`。
- `quality_report.all_key_numbers_have_evidence != true`。
- `tool_sections_misused` 或 `prohibited_outputs` 非空。
- 必需模型缺失：杜邦、CCC/营运资金、自由现金流、Altman Z-Score、估值预期差、情景推演。

## 质量警告

以下 warning 不等同失败，但最终回复必须披露：
- PDF/source link 覆盖弱。
- 部分章节偏薄。
- `review_queue` 中仍有资本开支、短期有息负债、利息费用、市值、同业样本、治理证据等关键项。

## 分析深度要求

报告必须从证据链到判断链：

```text
证据事实 -> 口径解释 -> 模型计算/降级 -> 同比/结构变化 -> 成因拆解 -> 风险链条 -> 改善/恶化/反证信号
```

不得只罗列指标。缺失字段必须说明原因和影响，不能伪精确计算。

## 反机械化要求

- 可见 Markdown/HTML 不得让多数章节重复同一组小标题：`事实`、`计算`、`判断`、`风险/改善条件`。
- 每章必须有 `narrative_blocks`，且小标题要体现章节任务，例如“收入与现金流匹配度”“杜邦分析”“自由现金流”“主要风险链条”“可能推翻当前结论的证据”。
- 旧字段 `facts/calculations/judgements/risks_or_improvement_conditions` 仅用于兼容校验，不得作为最终报告的统一展示结构。
- 定量模型必须有输入口径和证据来源；字段不足时说明无法可靠计算。
- 定性模型必须清楚区分“年报/证据事实”和“模型推论/分析假设”，不能把推论写成已验证事实。
