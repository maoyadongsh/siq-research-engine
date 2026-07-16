# SIQ 非 A 股智能分析 Profile

`siq_analysis_multi_market` 是与 A 股 `siq_analysis` 硬隔离的确定性报告 profile，仅处理 HK、US、EU、KR、JP 已解析公司报告。它不注册独立 Hermes gateway；API 报告 workflow 直接调用本目录的 bundle runner。

## 输入边界

生产入口只接受服务端生成的 `AnalysisInputBundle`。bundle 必须绑定：

- `company_key`
- `report_id`
- 完整 `ResearchIdentity`：`market/company_id/filing_id/parse_run_id`
- manifest 与 source family
- 报告正文、指标、校验结果和证据 locator 的只读路径

CN/A 股请求不得进入本 profile。A 股继续使用原 `siq_analysis/scripts/run_analysis_report.py --company --year`、原 rules、原 renderer 和原发布行为。

## 市场适配

- HK/EU/KR/JP：`pdf_market` adapter，使用 PDF/Markdown/表格 locator。
- US：`sec_ixbrl` adapter，使用 SEC HTML/section/anchor 与 XBRL concept/fact/context/unit locator。

上游产物差异由 adapter 收敛为统一 bundle；报告仍保留原币、实际 fiscal period、会计准则和上市地规则，不把 A 股字段或风险模板套到境外市场。

## 运行入口

```bash
agents/hermes/profiles/siq_analysis_multi_market/scripts/run_analysis_report.py \
  --input-bundle <server-generated-analysis_input_bundle.json> \
  --output-prefix <server-approved-analysis-prefix> \
  --force
```

禁止在本 profile 的生产流程使用 `--company`、`--year`、`resolve_company.py` 或“最新报告”推断。API 负责选择市场对应脚本并在运行前完成身份核对。

## 主要模块

- `scripts/analysis_input_bundle.py`：构建和加载统一只读输入。
- `scripts/input_adapters/`：PDF 与 SEC/iXBRL source-family adapter。
- `scripts/analysis_market_policy.py`：HK/US/EU/KR/JP 市场写作与数据规则。
- `scripts/analysis_bundle_renderer.py`：中文结构化报告与 artifact sidecar 渲染。
- `scripts/run_analysis_report.py`：bundle runner；同时保留复制时的旧函数仅供代码兼容，生产路由不会调用。
- `rules/`：非 A 股引用、来源、写作与质量门禁。

## 输出契约

流水线原子写入 HTML、Markdown、JSON 和 `<artifact_id>.artifact.json`。关键结论必须绑定语义引用；完整证据数组保存在 JSON，HTML/Markdown 仅默认折叠展示核心 claims 使用的可读定位，最多 64 条。源 package 为 warning 或关键分析字段不足时状态保持 `degraded`，不得伪装为 `ready`。

## 测试

本 profile 的 adapter、market policy 和 renderer 测试位于 `tests/`。API 路由测试还必须证明：

- CN 在功能开关开启且带 structured context 时仍调用原 A 股脚本，命令中没有 `--input-bundle`。
- HK/US/EU/KR/JP 调用本 profile 的脚本，只使用 `--input-bundle`，不执行 company/year resolution。
