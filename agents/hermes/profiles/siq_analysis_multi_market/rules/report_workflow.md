# Multi-market Analysis Report Workflow

本规则只适用于 HK、US、EU、KR、JP 已解析报告。CN/A 股不进入本 profile，也不得复用本 profile 的模板、适配器或 bundle runner。

## 唯一生产入口

API 必须先解析页面选择的 `company_key + report_id + ResearchIdentity`，再生成只读 `AnalysisInputBundle`：

```bash
/home/maoyd/siq-research-engine/agents/hermes/profiles/siq_analysis_multi_market/scripts/run_analysis_report.py \
  --input-bundle <server-generated-analysis_input_bundle.json> \
  --output-prefix <server-approved-analysis-prefix> \
  --force
```

禁止使用 `--company`、`--year`、`resolve_company.py`、“最新报告”或 ticker 模糊匹配作为本 profile 的生产入口。若 market 为 CN，API 必须改走原 `siq_analysis` company/year 链。

## 身份与只读边界

- 四字段 `market/company_id/filing_id/parse_run_id` 必须完整，并与权威 report package 逐项一致。
- `company_key` 和 `report_id` 必须来自服务端 Research Universe resolver；客户端路径不可信。
- bundle 内的 `ResearchTargetV1`、manifest、source family 和 adapter version 必须贯穿 HTML、JSON、Markdown 与 artifact sidecar。
- 源报告、指标、证据、parser、sections、tables、xbrl 和 qa 目录只读；流水线只能写公司 `analysis/` 与其临时 staging 目录。
- 身份漂移、report_id 漂移或 locator 跨 filing/parse run 时失败关闭，不得降级到名称、ticker、年份或其他报告补证。

## Source Family 路由

- `pdf_market`：用于 HK/EU/KR/JP 的已解析 PDF 报告，以 manifest 声明的正文、指标、校验和 EvidenceRef locator 为准。
- `sec_ixbrl`：用于 US SEC 报告，消费 `parser/document_full.json`、`sections/report_complete.md`、`financial_data.json`、`normalized_metrics.json`、`financial_checks.json` 及 SEC/XBRL locator。
- 适配器选择以 manifest 的 source family/document format 为准，不以 ticker 或目录名猜测。
- 不得为 SEC 来源伪造 PDF 页码；不得把 PDF 市场缺少 XBRL 当作失败条件。

## 固定阶段

1. 校验非 CN 市场和完整 ResearchIdentity。
2. 解析权威 report package，并核对 company_key/report_id/身份。
3. 按 source family 构建只读 AnalysisInputBundle。
4. 应用对应市场 policy、会计口径、币种、scale 和报告期规则。
5. 生成结构化 claims、语义引用和中文分析正文。
6. 渲染 HTML/Markdown/JSON；完整证据数组只保存在 JSON，HTML/Markdown 的折叠目录仅展示核心 claims 使用的可读定位且最多 64 条，不在正文铺满内部 ID。
7. 校验 locator、引用覆盖、禁用内容、原币/期间和 degraded 状态。
8. 原子发布产物与 `siq_agent_artifact_v2` sidecar。

## 发布状态

- `ready`：必需来源与引用可解析，质量门禁通过。
- `degraded`：源 package 为 warning、非关键字段缺失或部分分析只能保守表达；必须披露原因。
- `blocked`：身份不一致、必需输入缺失、核心事实无证据、引用越界或产物校验失败；禁止发布为完成。

## 失败处理

- `research_identity_incomplete`：要求重新选择确切市场、公司和源报告。
- `research_identity_mismatch`：重新解析权威 package，禁止继续使用旧 bundle。
- `source_package_not_ready`：修复上游解析产物，不得从其他报告拼接。
- `unsupported_market`：停止当前 profile；CN 交回原 A 股链，其他未知市场保持不支持。
- `multi_market_research_disabled`：保持失败关闭，不得回退到 A 股脚本。
- renderer/validation 失败：保留 staging 排障信息，但不得写入可见产物索引。
