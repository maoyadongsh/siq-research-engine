# SIQ 智能分析 Agent

## 角色定位

`siq_analysis` 是面向上市公司经营研究的深度分析 profile。它负责把结构化财务事实、报告正文、证据引用和行业上下文组织成可回溯的分析报告，而不是做一句话式摘要或情绪化点评。

## 当前产品位置

`siq_analysis` 是二级市场研究报告生产角色。它应围绕 Wiki evidence package、PostgreSQL 结构化指标和 source map 形成分析，而不是直接从模型记忆生成“像研报”的文本。HK 二级市场 MVP 成熟后，它将优先消费 quality pass 的 package。

## 职责边界

- 负责年度经营诊断、盈利质量、现金流质量、资产质量、债务安全和风险链条分析。
- 负责在报告中把“事实 -> 解释 -> 风险 / 关注点 -> 后续验证事项”串起来。
- 不负责输出评分、目标价、交易建议或无依据的宏观判断。
- 不替代事实核查、跟踪更新或法务意见角色。

## 依赖证据

典型输入来自公司 Wiki、结构化 metrics、证据索引和可选数据库回查：

- `company.json`
- `metrics/*.json`
- `evidence/*.json`
- `reports/<report_id>/report.md`
- `reports/<report_id>/document_full.json`
- PostgreSQL 中的只读证据表

所有关键数字、口径说明和风险判断都应能映射回这些证据入口。

## 内部子智能体

高质量年度分析默认采用 `research_packs` 中间契约，把 14 章报告拆给内部研究角色形成结构化底稿，再由 `siq_analysis` 统一整合。子智能体定义放在本 profile 内部，不单独暴露网关或 API：

```text
agents/hermes/profiles/siq_analysis/subagents/
  evidence_curator.md
  financial_modeler.md
  business_strategy_researcher.md
  industry_peer_researcher.md
  governance_risk_researcher.md
  chart_visual_designer.md
  editor_in_chief.md
```

五个研究型 pack 写入：

```text
analysis/.work/<report_slug>/research_packs/*.json
```

`chart_visual_designer` 是可选图表设计角色，负责把 `financial_modeler` 的公式、口径和缺口转化为可读、可交互、可复核的图表蓝图；`editor_in_chief` 是整合角色，负责把各 pack 的发现、缺口和复核项合入 `section_drafts.json`，但最终仍必须通过 `validate_report_quality.py`。

### 子智能体执行层

`scripts/run_research_subagents.py` 是 research pack 的统一执行入口，负责把“真实子智能体产出”和“确定性本地生成”收敛为同一目录合同：

- `--mode deterministic`：默认模式，调用现有 `generate_research_packs.py` 生成确定性 pack。
- `--mode external`：从 `--external-pack-dir` 复制 Hermes/LLM 子智能体真实产出的 pack，不做确定性补写。
- `--mode hybrid`：先采用 external pack，再用确定性 fallback 补齐缺失的必需 pack。
- `--mode prompt-only`：只生成 `research_subagent_prompts.json`，供 Hermes/LLM 子智能体消费，不生成最终 pack。

年度报告入口在启用 `--use-research-packs` 时应优先调用 `run_research_subagents.py`，并通过 `--research-subagent-mode`、`--research-subagent-pack-dir`、`--no-research-subagent-fallback` 控制执行方式。默认仍保持确定性可回放；只有明确接入 Hermes/LLM 子智能体时才使用 `external` 或 `hybrid`。

子智能体的额外标杆或外部检索应由任务提示词驱动，而不是在脚本中硬编码公司或查询词。可通过 `--research-subagent-prompt`、`--research-subagent-prompt-file` 或可重复的 `--research-benchmark-hint` 把用户意图传入 prompt bundle；`industry_peer_researcher` 再基于这些提示检索本地多市场 wiki 或 Hermes Tavily/EXA web 工具。

报告事实底座以本地 wiki、年报、metrics、evidence 和 semantic 为主。Tavily/EXA 可以充分补充行业趋势、政策、技术路线、专利/知识产权、同业竞争和跨市场参考，但必须作为外部补证进入 `external_sources`、`evidence_facts` 或低/中置信度 `key_findings`，不得覆盖公司年报事实。最终报告可见正文应让用户区分：

- `本地事实证据`：来自本地年报/wiki/metrics/evidence/semantic。
- `模型测算`：由本地指标计算或明确降级说明得出。
- `外部搜索补证`：来自 Tavily/EXA 或外部网页，用于上下文、技术/政策/竞争补充。
- `风险链/跟踪信号`：由研究角色基于事实和模型推演形成，需可证伪。

`research_subagent_run_manifest.json` 是研究子智能体运行的排障入口，会记录 `started_at`、`completed_at`、`elapsed_ms`、pack 来源统计、fallback 次数、验证状态和失败/告警数量。脚本命令审计字段会对 prompt、benchmark hint、token、password 等敏感参数值脱敏；完整任务提示只保留在供子智能体消费的 prompt bundle 中。

## 输出产物

分析报告通常写入：

```text
companies/<company_id>/analysis/
  <stock_code>-<short_name>-<year>-analysis.md
  <stock_code>-<short_name>-<year>-analysis.json
  <stock_code>-<short_name>-<year>-analysis.html
```

产物既要适合前端阅读，也要保留足够证据元数据供后续核查与跟踪使用。

高质量流程还会在 `.work/<report_slug>/` 下保留：

- `research_packs/`
- `research_subagent_prompts.json`
- `research_pack_manifest.json`
- `research_pack_validation.json`
- `research_pack_merge_manifest.json`

## 财务精度与质量门禁

深度报告中的财务判断必须同时满足“源事实可引用”和“派生计算可重算”：

| 报告内容 | 最低证据/校验要求 |
| --- | --- |
| 三表余额与发生额 | 绑定同一 ResearchIdentity 的 metrics/evidence，保留期间、单位、币种和页表定位 |
| 同比、占比、CAGR、周转与人均 | 使用 `financial_calculator.py`，保存分子/分母 evidence ID 与结构化 trace |
| 商誉、应收、存货等净额 | 主表净额 + 附注原值/准备双路召回，使用 reconciliation trace |
| 风险链与趋势 | 区分事实、模型测算、外部补证与可证伪假设，不能用叙事替代数值缺口 |
| 图表 | 数据点来自已验证 pack；chart designer 只改变表达，不改变公式、口径或事实 |

报告发布前由 research pack validation、citation/quality gate 和 API answer/report audit 分层检查。某个章节证据不足时允许 `degraded` 或留出待补证项，不允许为了章节完整度伪造数据。

## 与其他 Agent 的协同关系

- `siq_assistant` 负责轻量问答与解释，可把复杂分析需求引导到 `siq_analysis`。
- `siq_factchecker` 对 `siq_analysis` 报告做独立复核，不共享结论权限。
- `siq_tracking` 把分析报告中的重点假设、风险和异常转化为持续观察事项。
- `siq_legal` 负责法规依据、合规分析和法律边界，不替代经营分析。

## 禁止行为

- 不输出综合评分、星级、目标价或交易动作。
- 不在缺证据时补写数值或替模型猜测口径。
- 不把宏观叙事或行业印象包装成已经验证的公司事实。
- 不忽略单位、期间、合并范围和审计状态差异。

## 运行入口

前端入口：`/analysis`

API 前缀：`/api/analysis/*`

单 profile 启动示例：

```bash
cd /home/maoyd/siq-research-engine
scripts/hermes/run_gateway.sh siq_analysis
```

年度报告推荐命令：

```bash
cd /home/maoyd/siq-research-engine
agents/hermes/profiles/siq_analysis/scripts/run_analysis_report.py \
  --company <股票代码或company_id> \
  --year <年度> \
  --use-research-packs
```

真实子智能体 pack 已在外部目录生成时：

```bash
agents/hermes/profiles/siq_analysis/scripts/run_analysis_report.py \
  --company <股票代码或company_id> \
  --year <年度> \
  --use-research-packs \
  --research-subagent-mode hybrid \
  --research-subagent-pack-dir <外部pack目录>
```

## 维护原则

- 报告结构稳定优先于文风花哨，章节应服务证据与判断链路。
- 引用修复能力要随底层 source / wiki / parser 合同演进而同步维护。
- 涉及财务模型扩展时，应明确公式、口径和缺失条件。
- 当证据不足时允许保守结论，不允许强行“写满整份报告”。
