# SIQ 智能分析助手子智能体报告质量提升设计方案

## 1. 背景与问题

`siq_analysis` 当前已经具备完整年度财务诊断报告的基础骨架：固定 14 章模板、`.md/.json/.html` 三种报告产物、Wiki evidence package、PDF/source 链接、引用修复和质量门禁。当前流水线大致是：

```text
resolve company
-> wiki inventory / preflight
-> evidence_package.json
-> metric_snapshot.json
-> analysis_outline.json
-> section_drafts.json
-> render markdown/json/html
-> repair citations
-> validate_report_quality.py
```

这套机制解决了“能稳定生成一份结构完整报告”的问题，但还没有充分解决“每章都有足够深度、行业上下文正确、写法像成熟研究员”的问题。

现阶段主要痛点：

- 14 章结构完整，但部分章节内容偏薄，甚至只保留模板化句子或缺口声明。
- `generate_section_drafts.py` 里存在行业模板句，例如汽车行业、广汽、单车盈利等，可能污染非汽车公司报告。
- 同业样本选择逻辑过粗，可能因为公司名、关键词或 fallback 误选行业。
- 网络搜索结果已经进入 `industry_research.json`，但没有被足够精炼成“可引用、可解释、可反证”的行业判断。
- 质量门禁更擅长检查结构、链接、章节数量和必需模型，较少检查行业错配、洞察密度、重复度、观点支撑度。
- 报告 HTML 已有图表能力，但正文和图表之间的叙事连接还不够强，部分图表可能只是展示数据，未解释“所以什么”。

## 2. 设计结论

采用 **5 个内部子智能体 + `siq_analysis` 总编** 的设计。

这 5 个子智能体不作为独立产品入口，不单独暴露网关或 API。它们是 `siq_analysis` 报告生产线内部的专题研究角色，产出结构化 `research_packs`，最终由 `siq_analysis` 统一整合成 `section_drafts.json` 和完整报告。

推荐子智能体：

1. `evidence_curator`：证据管家
2. `financial_modeler`：财务建模
3. `business_strategy_researcher`：业务战略
4. `industry_peer_researcher`：行业同业
5. `governance_risk_researcher`：治理风险

`siq_analysis` 主智能体承担 `editor_in_chief` 总编职责。

不建议按 14 章拆成 14 个子智能体。章节之间高度交叉，按章节拆容易带来重复、冲突、风格割裂和证据重复引用。按研究能力域拆更稳定。

## 3. 目标与非目标

### 3.1 目标

- 让 14 章内容从“结构完整”升级为“每章有诊断、有证据、有模型、有判断、有反证条件”。
- 降低行业错配、历史样例污染、泛化模板句进入报告的概率。
- 让 Tavily/EXA 等网络搜索工具真正服务行业与同业分析，而不是把搜索片段直接贴进正文。
- 把每个专题研究过程沉淀成可审计的结构化中间产物。
- 让主报告生成可恢复、可回放、可验证。
- 保持现有 `/analysis` 产品入口、API、前端展示和报告目录不扩散。

### 3.2 非目标

- 第一阶段不新增对外智能体入口。
- 第一阶段不新增独立网关。
- 第一阶段不要求用户分别和每个子智能体聊天。
- 第一阶段不把报告生成改成完全自由写作，仍以固定 14 章模板为最终合同。
- 不输出买入/卖出、目标价、评级、评分或无证据法律定性。

## 4. 目录设计

新增内容建议放在 `agents/hermes/profiles/siq_analysis/` 内部：

```text
agents/hermes/profiles/siq_analysis/
  subagents/
    evidence_curator.md
    financial_modeler.md
    business_strategy_researcher.md
    industry_peer_researcher.md
    governance_risk_researcher.md
    editor_in_chief.md
  templates/
    research_pack.schema.json
    section_pack.schema.json
  scripts/
    run_research_subagents.py
    generate_research_packs.py
    merge_research_packs.py
    validate_research_packs.py
```

运行时产物继续放入公司分析 `.work` 目录：

```text
data/wiki/companies/<company_id>/analysis/.work/<report_slug>/
  wiki_inventory.json
  preflight.json
  evidence_package.json
  metric_snapshot.json
  analysis_outline.json
  research_packs/
    evidence_curator.json
    financial_modeler.json
    business_strategy_researcher.json
    industry_peer_researcher.json
    governance_risk_researcher.json
  research_subagent_prompts.json
  research_pack_manifest.json
  research_pack_validation.json
  section_drafts.json
  quality_report.json
  citation_repair.json
  final_validation.json
```

## 5. 子智能体职责

### 5.1 证据管家：`evidence_curator`

职责：

- 读取并盘点公司 Wiki、年报、metrics、evidence、semantic、graph、source map、PDF refs。
- 判断核心数据是否可用、是否过期、是否缺页码、是否存在多口径冲突。
- 为其他子智能体提供统一事实底座。
- 不写投资判断，不做行业推理。

输入：

- `company.json`
- `_index.json`
- `reports/<report_id>/report.md`
- `reports/<report_id>/document_full.json`
- `metrics/key_metrics.json`
- `metrics/three_statements.json`
- `metrics/validation.json`
- `evidence/evidence_index.json`
- `semantic/*`
- 可选 PostgreSQL 回查结果

输出重点：

- 核心指标证据表
- 可追溯 PDF/source 链接状态
- 关键缺口清单
- 口径冲突清单
- 可用于全报告的 `evidence_alias_map`

质量要求：

- 核心数字必须附 `metric_key`、period、unit、value、source_file、task_id、pdf_page、table_index、md_line。
- 缺失字段必须显式标记 `missing`，不得补写。
- 若同一指标存在多个来源，必须标记 preferred source 和 discarded source。

### 5.2 财务建模：`financial_modeler`

职责：

- 负责三表联动、利润桥、杜邦分析、营运资金、CCC、自由现金流、偿债、Altman、估值数据缺口。
- 为第 2、4、5、6、7、11 章提供深度底稿。

输入：

- `metric_snapshot.json`
- `evidence_curator.json`
- `metrics/three_statements.json`
- `metrics/validation.json`
- 必要时调用共享财务计算脚本

输出重点：

- 核心财务快照
- 同比、占比、覆盖倍数、周转天数等计算
- 可计算模型和不可计算模型的降级说明
- 利润桥和现金流桥数据
- 需要人工复核的关键字段

质量要求：

- 每个计算必须包含 `formula`、`inputs`、`output`、`unit`、`evidence_refs`。
- 不允许用缺失字段强算。
- 对资本开支、营业成本、营业总成本、利息费用、EBIT、市值等高风险字段做专项校验。

### 5.3 业务战略：`business_strategy_researcher`

职责：

- 解释收入质量、业务结构、产品/区域/渠道、战略兑现、研发投入、管理层表述与财务变量的关系。
- 为第 3、4、9、13 章提供底稿。

输入：

- `semantic/llm/<report_id>/business_profile.json`
- `semantic/llm/<report_id>/risks.json`
- `semantic/llm/<report_id>/events.json`
- `graph/facts/*`
- `graph/claims/*`
- 年报管理层讨论与分析相关片段
- `evidence_curator.json`
- `financial_modeler.json`

输出重点：

- 业务结构与收入质量诊断
- 战略表述到财务变量的映射
- 研发投入、产品结构、渠道/区域变量的财务验证方式
- 后续跟踪指标

质量要求：

- 先确认公司业务标签，再组织语言。
- 不得复用不属于该公司的行业模板。
- 每条战略判断必须落到至少一个财务变量，例如毛利率、费用率、资本开支、库存、经营现金流。

### 5.4 行业同业：`industry_peer_researcher`

职责：

- 负责行业周期、竞争格局、政策变量、出口/价格/供应链风险、同业样本选择和同业指标对比。
- 使用 Tavily/EXA 网络搜索，但必须把搜索结果转为结构化研究底稿。
- 为第 8、9、11、12 章提供底稿。

输入：

- `company.json` 中的行业标签
- `company_catalog.json`
- `metric_snapshot.json`
- `peer_metrics.json`
- Tavily/EXA 搜索结果
- 可靠外部行业来源

输出重点：

- 行业分类确认
- 同业样本选择理由和排除理由
- 同业指标分位
- 行业趋势、政策、竞争变量
- 外部来源证据表

质量要求：

- 同业样本必须优先按 `industry_sw3 -> industry_sw2 -> industry_sw1` 选择。
- 若样本不足，可降级，但必须写明 `peer_selection_confidence=low`。
- 禁止因为公司名后缀为“集团”就进入汽车样本。
- 搜索结果必须记录 query、provider、url、title、published_date、snippet、可信度。
- 正文只能引用经过归纳后的行业判断，不直接堆搜索片段。

### 5.5 治理风险：`governance_risk_researcher`

职责：

- 负责治理、审计意见、关联交易、承诺事项、监管/诉讼、股权质押/冻结、重大风险链条和情景推演。
- 为第 10、12、13、14 章提供底稿。

输入：

- 年报治理章节
- `semantic/risks.json`
- `semantic/events.json`
- `graph/claims/*`
- 可选外部公告/监管搜索结果
- `evidence_curator.json`
- `industry_peer_researcher.json`

输出重点：

- 红旗/黄旗/观察项
- 治理证据清单
- 主要风险链条
- 改善、中性、压力情景
- 可推翻当前结论的反证条件

质量要求：

- 只能写风险信号和待核验事项，不直接定性违法犯罪。
- 风险链必须满足：触发因素 -> 经营影响 -> 财务报表影响 -> 现金流/债务后果 -> 二级市场含义。
- 情景推演不得给无依据概率和伪精确盈利弹性。

## 6. Research Pack 契约

所有子智能体统一输出 `research_pack`，不直接输出最终 Markdown。

建议核心结构：

```json
{
  "schema_version": 1,
  "agent_id": "financial_modeler",
  "company_id": "600104-上汽集团",
  "report_year": 2025,
  "generated_at": "",
  "input_files": [],
  "coverage": {
    "target_section_ids": [],
    "covered_questions": [],
    "missing_questions": []
  },
  "key_findings": [
    {
      "finding_id": "",
      "section_ids": [],
      "claim": "",
      "basis": "",
      "implication": "",
      "confidence": "high",
      "evidence_refs": []
    }
  ],
  "evidence_facts": [],
  "calculations": [],
  "risk_chains": [],
  "tracking_signals": [],
  "external_sources": [],
  "missing_inputs": [],
  "review_required": [],
  "prohibited_content_hits": []
}
```

核心原则：

- 子智能体输出结构化材料，不直接决定最终措辞。
- `key_findings` 必须能映射到目标章节。
- `confidence=low` 的发现只能进入缺口、待验证、风险提示，不得写成确定性判断。
- 所有外部搜索来源必须保留 provider、query、url 和摘要。

## 7. 总编整合逻辑

`siq_analysis` 主体作为 `editor_in_chief` 执行以下工作：

1. 读取全部 research packs。
2. 检查每个 pack 的 schema、覆盖章节、证据引用、缺口清单。
3. 生成 14 章 `section_drafts.json`。
4. 为每章补齐：
   - 核心诊断句
   - 关键事实表
   - 模型和口径
   - 财务解释
   - 风险链或改善条件
   - 可推翻当前结论的证据
   - 后续跟踪指标
   - 本节证据
5. 去重与冲突处理：
   - 同一事实只保留最强证据版本。
   - 财务数字冲突时优先采用证据管家的 preferred source。
   - 行业判断与公司业务标签冲突时拒绝进入正文。
   - 子智能体观点冲突时写入 `review_required`，不强行调和。
6. 输出 `section_drafts.json` 后进入现有渲染和校验流程。

## 8. 目标工作流

### 8.1 执行层接口

新增 `scripts/run_research_subagents.py` 作为 research pack 的统一执行层。它不改变 `research_pack.schema.json` 合同，只负责把不同来源的子智能体底稿收敛到同一 `.work/<report_slug>/research_packs/` 目录。

支持模式：

- `--mode deterministic`：默认模式，调用现有 `generate_research_packs.py` 生成五个确定性 pack，保证本地可回放和 CI 稳定。
- `--mode external`：从 `--external-pack-dir` 复制真实 Hermes/LLM 子智能体产出的 pack，缺失 pack 交由校验阶段暴露。
- `--mode hybrid`：先使用 external pack，再以 deterministic fallback 补齐缺失的必需 pack，适合真实子智能体逐步接入。
- `--mode prompt-only`：只生成 `research_subagent_prompts.json`，供 Hermes/LLM 子智能体执行；该模式不产出最终 pack，也不应直接进入报告渲染。

执行层应输出或更新：

- `research_packs/*.json`
- `research_pack_manifest.json`
- `research_subagent_prompts.json`（`prompt-only` 或需要外部执行时）
- 必要的模式、来源目录、fallback 使用情况和缺失 pack 记录

`run_analysis_report.py` 在 `--use-research-packs` 开启时优先调用该执行层，并新增透传参数：

- `--research-subagent-mode deterministic|external|hybrid|prompt-only`
- `--research-subagent-pack-dir <目录>`
- `--no-research-subagent-fallback`
- `--research-subagent-prompt` / `--research-subagent-prompt-file`
- `--research-benchmark-hint <提示>`，可重复

默认值必须保持 `deterministic`，避免在没有 Hermes/LLM 子智能体时破坏现有报告流程。

标杆检索必须保持提示词驱动：执行层只把用户任务提示、显式 benchmark hint、本地多市场 wiki 根目录和 Hermes web 工具约束写入 `research_subagent_prompts.json`，不在脚本层硬编码公司、市场或查询词。行业同业子智能体从提示词抽取对象后自行检索，并把海外公司标记为 `cross_market_reference`，不得纳入 A 股严格同业分位、`peer_count`、估值均值或中位数。

运行审计必须以 `research_subagent_run_manifest.json` 为入口，记录 `started_at`、`completed_at`、`elapsed_ms`、pack 来源统计、fallback 次数、验证状态、失败/告警数量和 benchmark/prompt 的长度级指标。脚本命令字段只能保留脱敏后的参数值，不得明文写入 prompt、benchmark hint、token、password 或 secret。

`research_packs` 的低置信发现必须被约束在复核链路中：`key_findings[].confidence` 只能是 0 到 1 的数字；`confidence < 0.60` 时必须设置 `review_required=true`，否则验证失败。中等置信但缺少 rationale/evidence 的发现只产生 warning，避免把探索性研究全部硬阻断。

### 8.2 报告流水线

目标流水线：

```text
run_analysis_report.py
  -> resolve_company.py
  -> build_wiki_inventory
  -> build_preflight
  -> build_evidence_package
  -> build_metric_snapshot
  -> build_analysis_outline
  -> run_research_subagents.py
       -> deterministic: generate_research_packs.py
       -> external: copy packs from external pack dir
       -> hybrid: external first, deterministic fallback for missing packs
       -> prompt-only: write research_subagent_prompts.json
  -> validate_research_packs.py
  -> merge_research_packs.py
       -> section_drafts.json
  -> repair_report_citations.py
  -> html_renderer_v2.py
  -> validate_report_quality.py
  -> optional siq_factchecker
```

并行策略：

```text
evidence_curator 必须先跑

financial_modeler
business_strategy_researcher
industry_peer_researcher
governance_risk_researcher
可以在 evidence_curator 之后并行

editor_in_chief 必须最后跑
```

第一阶段如果 Hermes delegation 并发仍为 1，可以先串行跑，保证产物契约稳定。后续再将 `siq_analysis/config.yaml` 的 `delegation.max_concurrent_children` 提升到 3 或 4。

在 `deterministic` 模式下，上述并行策略体现为生成顺序和 pack 依赖关系；在 `external` 或 `hybrid` 模式下，应通过 `research_subagent_prompts.json` 把依赖、输入文件和输出 schema 明确传给真实子智能体。

## 9. 网关与 API 设计

### 9.1 第一阶段

不新增独立网关。

不新增独立对外 API。

继续复用：

- 前端入口：`/analysis`
- API 前缀：`/api/analysis`
- Hermes profile：`siq_analysis`
- 报告目录：`companies/<company_id>/analysis/`

子智能体只是内部生产线，运行状态写入 `.work/research_packs/`。

### 9.2 第二阶段可选增强

当 research packs 稳定后，可以增加只读调试 API，但不必作为用户主入口：

```text
GET /api/wiki/companies/{company_id}/analysis-runs/{run_id}/research-packs
GET /api/wiki/companies/{company_id}/analysis-runs/{run_id}/research-packs/{agent_id}
GET /api/wiki/companies/{company_id}/analysis-runs/{run_id}/quality
```

用途：

- 前端展示每个内部子智能体完成状态。
- 调试报告章节为什么薄。
- 查看行业搜索来源和同业样本选择理由。

### 9.3 何时拆独立 profile

只有满足以下条件时，才考虑把子智能体拆成独立 Hermes profile 和独立网关：

- 用户需要单独和财务建模、行业研究等智能体对话。
- 子智能体需要独立长期记忆或独立权限。
- 子智能体要服务其他产品线，而不是只服务 `siq_analysis` 报告。
- 前端有明确的“单独生成行业研究/单独生成财务模型”产品按钮。

在此之前保持内部化，避免系统边界过早扩散。

## 10. 质量门禁升级

现有 `validate_report_quality.py` 应保留结构性校验，并新增语义质量检查。

建议新增失败项：

- `industry_mismatch_terms_present`：报告中出现与公司行业明显不匹配的关键词。
- `peer_selection_industry_mismatch`：同业样本行业与目标公司行业不一致且未降级说明。
- `hardcoded_template_residue`：出现“广汽这类”“汽车类公司”等历史样例残留。
- `thin_section_by_question_coverage`：章节虽然有字数，但没有回答模板要求的问题。
- `unsupported_external_claim`：外部行业结论没有来源或没有日期。
- `search_snippet_dumping`：直接堆搜索摘要，未形成分析判断。
- `risk_chain_incomplete`：风险链缺少经营影响、报表影响、现金流/债务后果或二级市场含义。
- `section_without_core_diagnosis`：章节没有开场诊断句。
- `section_without_counter_evidence`：关键判断没有反证条件。

建议新增 warning 项：

- `weak_business_specificity`：公司业务特征不足，泛行业语言占比过高。
- `weak_chart_narrative_link`：图表存在但正文没有解释图表含义。
- `weak_tracking_actionability`：跟踪清单只有指标名，没有当前状态、改善信号、恶化信号、频率和数据源。

## 11. 报告内容升级标准

每章最低内容标准：

```text
一句核心诊断
-> 2-5 个关键证据事实
-> 1 个模型/口径/表格
-> 1 段成因解释
-> 1 条风险链或改善链
-> 1-3 个反证/跟踪信号
-> 证据引用
```

对于核心章节，应提升到更高标准：

- 执行摘要：必须给出 3-5 条总判断，每条包含证据和含义。
- 关键变化：必须按改善、恶化、观察项拆解。
- 经营质量：必须解释收入、应收、存货、合同负债、现金流之间的闭环。
- 盈利能力：必须区分毛利、费用、减值、投资收益、非经常性损益。
- 现金流：必须解释经营现金流与利润背离原因。
- 行业同业：必须说明样本选择和行业周期判断依据。
- 风险链条：必须写成可传导、可验证、可推翻的因果链。

## 12. 网络搜索使用原则

`siq_analysis` 当前配置已有 web toolset，Tavily 作为 search backend，EXA 作为 extract backend。后续应将网络搜索限定在 `industry_peer_researcher` 和必要的 `governance_risk_researcher` 中。

使用原则：

- 搜索前必须确认公司行业标签和报告期。
- 查询词应包含行业、年份、竞争格局、政策、价格、出口、成本、需求等关键词。
- 优先来源包括交易所公告、公司官网、行业协会、券商/研究机构公开报告、主流财经媒体、监管机构。
- 搜索结果只进入 `external_sources`，不得直接写成最终结论。
- 外部资料与公司年报冲突时，以公司披露和本地 evidence 为事实底座，外部资料只作为背景和风险触发器。

## 13. 实施计划

### Phase 1：契约和目录

- 新增 `subagents/*.md`。
- 新增 `research_pack.schema.json`。
- 新增 `run_research_subagents.py`，先支持 `deterministic` 和 `prompt-only`。
- 新增 `validate_research_packs.py`。
- 不改报告主流程，只先允许手工或脚本生成 packs。

验收：

- 5 个 pack 都能写入 `.work/research_packs/`。
- pack schema 校验通过。
- 不影响现有报告生成。

### Phase 2：财务与行业两个高价值 pack

- 先实现 `financial_modeler` 和 `industry_peer_researcher`。
- 修复同业选择误入汽车行业的问题。
- 将行业搜索结果结构化为 `external_sources` 和 `key_findings`。

验收：

- 上汽集团作为汽车行业主样本案例，能够生成 5 个 research pack 并通过最终质量门禁。
- 汽车样本池共 8 家：目标公司为上汽集团；同业候选包括长城汽车、赛力斯、广汽集团、长安汽车、北汽蓝谷、江淮汽车、比亚迪。以上汽集团为目标公司时，同业样本应为其余 7 家。
- 同业样本选择理由可追溯。
- 第 4、5、6、7、8 章明显增厚。

### Phase 3：完整 5 pack 编排

- 实现 `evidence_curator`、`business_strategy_researcher`、`governance_risk_researcher`。
- 新增 `merge_research_packs.py`。
- `run_research_subagents.py` 支持 `external` 和 `hybrid`，并记录 fallback 使用情况。
- `run_analysis_report.py` 增加 `--use-research-packs`、`--research-subagent-mode`、`--research-subagent-pack-dir`、`--no-research-subagent-fallback`。

验收：

- `section_drafts.json` 由 packs 合成。
- 14 章无空白章节。
- 每章有核心诊断、证据、模型/口径、风险/反证。
- `prompt-only` 能生成可交给 Hermes/LLM 子智能体的 `research_subagent_prompts.json`。
- `hybrid` 能优先使用真实 pack，并只对缺失必需 pack 进行确定性补齐。

### Phase 4：质量门禁 v1.2

- 增加行业错配、同业错配、模板残留、洞察密度、搜索片段堆砌检查。
- 将 `validate_report_quality.py` 的语义检查结果写入 `quality_report.json`。

验收：

- 含“广汽这类集团型车企”等明显错配语句的报告必须失败。
- peer industry mismatch 必须失败或强制降级。
- 空白/薄章节必须失败。

### Phase 5：前端可观测增强

- 可选增加 research pack 查看面板。
- 可选展示每个专题研究状态、耗时、来源数量、缺口数量。

验收：

- 用户能看到报告为什么可信、哪些地方仍需复核。

## 14. 风险与对策

风险 1：子智能体越多，系统更复杂。

对策：第一阶段只作为内部 packs，不新增网关/API；先串行跑通，再并行。

风险 2：多个子智能体结论冲突。

对策：主总编不强行调和，冲突写入 `review_required`，并在第 14 章披露。

风险 3：网络搜索带来噪声。

对策：搜索结果只能进入 `external_sources`，必须由 `industry_peer_researcher` 归纳后才能进入正文。

风险 4：报告变长但不变深。

对策：质量门禁检查问题覆盖、诊断句、模型口径、风险链、反证条件，而不是只检查字数。

风险 5：行业错配继续发生。

对策：新增公司行业标签、同业样本、正文关键词三重一致性校验。

## 15. 推荐的第一批代码改动

优先级从高到低：

1. 删除或行业化替换 `generate_section_drafts.py` 中硬编码的汽车行业句子。
2. 修复 `peer_metrics_builder.py` 的 `auto_keyword_automotive` fallback，禁止因公司名以“集团”结尾误选汽车同业。
3. 新增 `research_pack.schema.json`。
4. 新增 `run_research_subagents.py`，把 deterministic、external、hybrid、prompt-only 四种执行模式收敛到统一 pack 合同。
5. 新增 `industry_peer_researcher` pack 生成逻辑。
6. 新增 `financial_modeler` pack 生成逻辑。
7. 新增 `merge_research_packs.py`，先支持两个 pack 合成章节，再扩展到五个。
8. 升级 `validate_report_quality.py`，加入行业错配和薄章节语义检查。

## 16. 最终判断

“多个内部子智能体分别完成报告专题研究，最终由分析助手整合完整报告”的方案可行，而且非常适合 SIQ 当前阶段。

推荐采用 5 个内部子智能体，不拆独立网关和对外 API，先把它们作为 `siq_analysis` 的研究生产线。这样既能显著提升报告内容密度和行业适配，又能保持当前产品入口、权限、前端和报告合同稳定。
