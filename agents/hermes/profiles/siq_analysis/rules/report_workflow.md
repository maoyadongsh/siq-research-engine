# Report Workflow

完整年度报告生成必须执行固定且可回放的流水线，不能自由拼路径、自由拼命令；默认 research pack 来源为确定性本地生成。

## 主入口

```bash
/home/maoyd/siq-research-engine/data/hermes/home/profiles/siq_analysis/scripts/run_analysis_report.py --company <股票代码或company_id> --year <年度> --use-research-packs
```

续跑：

```bash
/home/maoyd/siq-research-engine/data/hermes/home/profiles/siq_analysis/scripts/run_analysis_report.py --company <股票代码或company_id> --year <年度> --reuse-checkpoint --use-research-packs
```

推荐高质量三步：

```bash
# 1. 准备材料与检查点
/home/maoyd/siq-research-engine/data/hermes/home/profiles/siq_analysis/scripts/run_analysis_report.py --company <股票代码或company_id> --year <年度> --prepare-only --use-research-packs

# 2. 检查 .work 中的 research_packs、research_pack_validation.json 和 review_required_agent_ids。
# 必须先处理 pack 缺失、同业错配、搜索来源缺口和 prohibited_content_hits。

# 3. 复用检查点，执行最终渲染、溯源修复和质量验收
/home/maoyd/siq-research-engine/data/hermes/home/profiles/siq_analysis/scripts/run_analysis_report.py --company <股票代码或company_id> --year <年度> --reuse-checkpoint --use-research-packs
```

如果用户只要求快速结构恢复，可省略 `--use-research-packs`；如果用户要求高质量、深度分析、补全 14 章、使用 Hermes 网络搜索或改善报告质量，必须启用 `--use-research-packs`。

`--use-research-packs` 启用后，报告入口必须优先通过 `run_research_subagents.py` 准备 research pack，而不是直接调用单一生成器。新增执行参数：

- `--research-subagent-mode deterministic|external|hybrid|prompt-only`：默认 `deterministic`。
- `--research-subagent-pack-dir <目录>`：`external` 或 `hybrid` 模式下的真实子智能体 pack 来源目录。
- `--no-research-subagent-fallback`：禁止 fallback；通常只在验收真实子智能体产出完整性时使用。

四种模式含义：

- `deterministic`：调用现有 `generate_research_packs.py`，产出可回放的本地确定性 pack。
- `external`：从 `--external-pack-dir` 或上层传入的 pack 目录复制 Hermes/LLM 子智能体产物；缺失 pack 不自动补齐。
- `hybrid`：优先使用 external pack，再用确定性 fallback 填补缺失必需 pack，适合渐进接入真实子智能体。
- `prompt-only`：只生成 `research_subagent_prompts.json` 给 Hermes/LLM 子智能体消费，不进入最终报告渲染。

## 防覆盖规则

默认情况下，最终渲染不会覆盖已有 `analysis/<stock>-<short>-<year>-analysis.md/.json/.html`。

若目标文件已存在：
- 优先使用 `--output-prefix` 写入测试前缀。
- 只有用户明确允许覆盖，或任务明确要求覆盖时，才追加 `--allow-overwrite`。
- 覆盖前脚本会自动备份旧文件到 `.work/backups/`；最终回复必须披露备份路径。

## 阶段产物

阶段产物默认写入：

```text
wiki/companies/<company_id>/analysis/.work/<report_slug>/
```

至少包括：
- `preflight.json`
- `evidence_package.json`
- `metric_snapshot.json`
- `analysis_outline.json`
- `peer_metrics.json`
- `qualitative_snapshot.json`
- `market_snapshot.json`
- `industry_research.json`
- `research_packs/`
- `research_subagent_prompts.json`
- `research_pack_manifest.json`
- `research_pack_validation.json`
- `research_pack_merge_manifest.json`
- `section_drafts.json`
- `quality_report.json`
- `citation_repair.json`
- `final_validation.json`

## 阶段顺序

1. 公司与报告定位：`resolve_company.py`
2. 单公司 wiki 全量盘点：读取目标公司目录下所有可用内容清单，包括 `company.*`、`reports/`、`metrics/`、`evidence/`、`semantic/`、`graph/`、`tracking/`、`factcheck/`、既有 `analysis/` 和 `_index.json`。
3. 数据状态预检：`preflight.json`
4. 证据包构建：`evidence_package.json`
5. 指标快照构建：`metric_snapshot.json`
6. 分析主线草稿：`analysis_outline.json`
7. 同业、定性、市场、行业研究检查点：`peer_metrics.json`、`qualitative_snapshot.json`、`market_snapshot.json`、`industry_research.json`
8. 内部子智能体执行层：`run_research_subagents.py` 按模式写入五个 `research_packs/*.json`，默认 `deterministic` 会调用 `generate_research_packs.py`
9. research pack 契约校验：`validate_research_packs.py`
10. 14 章结构化生成：`generate_section_drafts.py`
11. research pack 合并：`merge_research_packs.py`
12. 证据绑定与引用修补：`repair_report_citations.py`
13. 模板合规与质量验收：`validate_report_quality.py`
14. 最终完成摘要：只报告路径、验收结果和复核项。

## Research Pack 合同

`research_packs` 是内部子智能体协作边界。默认必须有五个研究型 pack：

- `evidence_curator.json`：证据覆盖、引用与缺口。
- `financial_modeler.json`：核心财务诊断、模型计算、估值锚。
- `business_strategy_researcher.json`：业务结构、战略、产品、经营驱动。
- `industry_peer_researcher.json`：同业样本、行业趋势、Hermes 外部搜索来源。
- `governance_risk_researcher.json`：治理、合规、股东和风险链。

合法补充 pack：`editor_in_chief.json`。

所有 pack 必须满足：

- `schema_version="1.0"`。
- `agent_id/company_id/report_year/generated_at` 完整。
- `coverage.section_ids` 明确覆盖章节。
- `key_findings/evidence_facts/calculations/risk_chains/tracking_signals` 使用结构化数组。
- `external_sources` 只放 provider/query/url/title 等来源元数据，不把长 snippet 直接写入可见正文。
- `missing_inputs` 必须说明原因、影响和对应章节。
- `prohibited_content_hits` 必须为空。

失败处理：

- `research_packs_dir_missing`：先跑 `run_analysis_report.py --prepare-only --use-research-packs`，默认会经由 `run_research_subagents.py --mode deterministic` 生成 pack。
- `missing_required_pack:*`：缺哪个子智能体 pack 就补哪个 pack。
- `external_pack_dir_missing`：检查 `--research-subagent-pack-dir` / `--external-pack-dir` 是否指向真实子智能体输出目录。
- `prompt_only_without_packs`：这是预期中间状态，应把 `research_subagent_prompts.json` 交给 Hermes/LLM 子智能体执行后，再用 `external` 或 `hybrid` 模式续跑。
- `industry_peer_external_sources_missing`：优先检查 Hermes Tavily/EXA 配置或在 `missing_inputs` 中明确外部来源缺口。
- `prohibited_content_hits_present`：禁止进入最终渲染，必须修复 pack。

## 样本案例

`600104-上汽集团` 是汽车行业主样本案例，用来验证完整 research-pack 流程、汽车行业话术、车企同业选择、行业外部检索和 14 章报告质量。它不是与其他公司做横向对比的对象。

```bash
/home/maoyd/siq-research-engine/agents/hermes/profiles/siq_analysis/scripts/run_analysis_report.py \
  --company 600104 \
  --year 2025 \
  --use-research-packs \
  --research-subagent-mode deterministic \
  --output-prefix data/wiki/companies/600104-上汽集团/analysis/600104-上汽集团-2025-analysis-research-pack-sample
```

当前汽车样本池共 8 家，上汽集团作为目标样本，其余公司作为汽车同业候选：

- 目标公司：`600104-上汽集团`
- 同业候选：`601633-长城汽车`、`601127-赛力斯`、`601238-广汽集团`、`000625-长安汽车`、`600733-北汽蓝谷`、`600418-江淮汽车`、`002594-比亚迪`

上汽集团作为目标公司时，同业样本应为其余 7 家。若 `company_catalog.json/company.json` 行业字段为空，`peer_metrics_builder.py` 可以基于明确车企身份降级进入汽车 fallback，但结果仍应披露 `peer_selection_warnings`，不能伪装成严格同业样本。

当前项目内样本产物：

```text
data/wiki/companies/600104-上汽集团/analysis/600104-上汽集团-2025-analysis-research-pack-sample.md
data/wiki/companies/600104-上汽集团/analysis/600104-上汽集团-2025-analysis-research-pack-sample.json
data/wiki/companies/600104-上汽集团/analysis/600104-上汽集团-2025-analysis-research-pack-sample.html
data/wiki/companies/600104-上汽集团/analysis/.work/600104-上汽集团-2025-analysis-research-pack-sample/
```

## 图表能力要求

完整年度报告进入最终渲染前，必须具备并使用本地金融图表规范：

```text
/home/maoyd/.agents/skills/finsight-chart-craft/SKILL.md
```

执行要求：
- 收支拆解、利润桥和瀑布图必须先通过三表勾稽，再进入视觉渲染。
- 图表数据优先从 `metrics/reports/<report_id>/three_statements.json` 的原始合并利润表项目名取值，避免重复 normalized key 造成口径混淆。
- `营业成本` 和 `营业总成本` 是不同项目；不得把 `营业总成本` 当作 `营业成本` 后又重复扣费用。
- `资产减值损失`、`信用减值损失`、`公允价值变动收益`、`资产处置收益` 要作为显式桥接项或显式汇总项，不得无说明并入残差。
- 首屏收支拆解图必须输出可交互 HTML/SVG 或 ECharts，支持 tooltip、键盘 focus 和点击高亮；不得只输出截图。

## 单公司 Wiki 全量读取要求

完整年度分析报告开始写作前，必须先完成目标公司 wiki 目录的全量获取和可用性判断。全量获取不是把所有大文件原样塞入正文，而是必须形成可审计的目录清单、关键文件读取状态、核心数据抽取状态和缺口清单。

最低读取范围：
- `company.json`、`company.md`、`_index.json`
- `reports/<year>-annual/report.json`、`report.md`、`document_full.json`、`artifact_manifest.json`
- `metrics/key_metrics.json`、`metrics/three_statements.json`、`metrics/validation.json`
- `evidence/evidence_index.json`、`evidence/pdf_refs.json`、`evidence/image_manifest.json`
- `semantic/facts.json`、`claims.json`、`relations.json`、`segments.json`、`evidence_semantic.json`、`semantic/llm/<year>-annual/*.json`
- `graph/report.md`、`graph/company.md`、`graph/facts/*`、`graph/claims/*`、`graph/segments/*`
- `tracking/report_manifest.json`、`tracking/tracking-items.md`、`tracking/updates/*`、`tracking/alerts/*`、`tracking/metrics/*`、`tracking/sentiment/*`
- `factcheck/*`
- 既有 `analysis/*`，尤其是高质量历史样例或人工修订版，用于学习结构和校验风格，但不得无证据复制旧结论。

全量读取后，报告生成必须说明：
- 哪些目录和文件已读取。
- 哪些文件缺失、陈旧、过大仅索引读取或解析失败。
- 当前报告采用哪些证据源作为事实底座。
- 哪些判断依赖模型推论、需要后续人工复核。

## 恢复与循环限制

- 若 `.work` 中已有阶段文件且 JSON 可解析，必须从最近有效检查点续跑。
- 若 `section_drafts.json` 已存在但不足 14 章，只生成缺失章节。
- 同一 `work_dir + output_prefix` 的恢复命令最多执行 2 次。
- 若 `run_analysis_report.py` 返回 `ok=false`，不得回复“报告已完成”；必须读取 `stage`、`validation.failures`、`validation.warnings` 和 `next_action`。
- 若恢复命令返回 `stage=output_exists`，不得直接覆盖；改用测试 `--output-prefix` 或取得覆盖授权后添加 `--allow-overwrite`。

## 应急恢复器定位

`render_report_from_checkpoint.py` 和 `recover_report_from_workdir.py` 是应急结构恢复器，不是高质量报告生成器。它们可以补齐 14 章、HTML 和质量报告，但若 `quality_report.all_key_numbers_have_evidence=false` 或验收失败，不能把产物声明为高质量最终报告。
