# SIQ 年度财务诊断报告模板 v1.1

模板文件路径：

`/home/maoyd/siq-research-engine/data/hermes/home/profiles/siq_analysis/templates/siq_analysis_report_v1.1.md`

机器可读结构文件：

`/home/maoyd/siq-research-engine/data/hermes/home/profiles/siq_analysis/templates/siq_analysis_report_v1.1.json`

## 使用规则

- 完整年度诊断报告必须使用 `template_id=siq_analysis_report_v1.1`。
- 必须正好输出 14 个一级大模块，顺序和 `section_id` 以 JSON 模板文件为准。
- Markdown 标题可以使用中文标题，但 JSON 必须保留固定 `section_id`。
- 可见正文必须使用每章专属的 CFO 分析动作，不得把每章机械拆成“事实 / 计算 / 判断 / 风险/改善条件”四段。
- JSON 必须包含 `narrative_blocks`；旧字段 `facts/calculations/judgements/risks_or_improvement_conditions` 仅作为兼容字段和质量校验底座。
- 每章仍必须形成“证据事实 -> 口径/模型 -> 财务解释 -> 可证伪判断 -> 跟踪信号”的链条，但这条链应融入章节特有结构。
- 数字、年份、比率、同业分位、监管事项、管理层表述等事实尽量引用已有 evidence；没有证据时必须降级为缺口或假设，不得补写成事实。
- 定性和定量分析要充分发挥大模型智慧：用经典财务模型、商业模式分析、三表联动、成因拆解、风险传导、情景推演和反证条件解释“为什么”和“接下来验证什么”。
- 杜邦分析、现金转换周期、自由现金流、Altman Z-Score、三表钩稽等是工具模型，只能嵌入指定章节，不得升级为一级大模块。
- 不输出综合评分、维度得分、评级、目标价、买入/卖出评级或无证据违法舞弊定性。

## CFO 叙事标准

报告语气应接近高质量的长安汽车样例：用清晰诊断句开场，用表格承载核心数字，用自然小标题组织具体问题，用“当前状态、形成原因、财务影响、后续验证”串联判断。

避免以下机械写法：

- 十四个章节都出现同样的“事实 / 计算 / 判断 / 风险/改善条件”。
- 把“经营质量计算应……”这类写作指令塞进正文。
- 每章都重复“改善条件、风险链、待验证信号”而不体现章节主题差异。

推荐章节动作：

| section_id | 可见小标题风格 | 核心分析动作 |
|---|---|---|
| `executive_summary` | 经营状态定性、财务健康度速览、核心结论、改变结论的条件 | 像投委会备忘录一样给出总判断 |
| `key_changes` | 年度异动雷达、改善/恶化/观察项、三表联动解释、口径与证据 | 把变化分成改善、恶化和待验证 |
| `operating_quality` | 收入变化分析、收入与现金流匹配度、经营稳定性评估、业务韧性 | 解释订单到现金的闭环 |
| `profitability_and_cost` | 杜邦分析、利润变化桥、毛利率与成本成因、费用/减值/非经常性损益 | 解释利润为什么这样变化 |
| `asset_quality_working_capital` | 资产结构与安全垫、存货分析、应收款项分析、现金转换周期 | 判断资产能否转化为利润和现金 |
| `debt_liquidity` | 短期偿债能力、长期偿债能力、现金覆盖与融资弹性、Altman 适用性 | 判断流动性墙和融资弹性 |
| `cash_flow_quality` | 现金流量表概览、经营现金流与利润匹配度、自由现金流、现金流原因拆解 | 判断利润含金量和内生造血 |
| `industry_competition` | 行业周期判断、同业对比、竞争位置、价格战与产品结构传导 | 判断相对位置和周期压力 |
| `strategy_policy_external_risk` | 管理层战略、政策/出口/供应链变量、战略兑现的财务验证、待验证事项 | 把战略语言落到报表变量 |
| `governance_compliance_shareholders` | 治理观察、股东结构与资本动作、合规/审计/监管事项、治理风险信号 | 做可审计治理检查 |
| `valuation_expectation_gap` | 估值数据缺口、基本面锚、市场预期差、A 股特有风险 | 审计估值是否有足够数据支撑 |
| `risk_chain_scenario` | 主要风险链条、情景推演、可能推翻当前结论的证据、风险缓释条件 | 做因果链和可证伪推演 |
| `tracking_checklist` | 核心跟踪指标、改善信号、恶化信号、跟踪频率与数据源 | 把判断转成可执行跟踪 |
| `data_quality_traceability` | 数据来源、数据质量检查、关键证据索引、限制与免责声明 | 保证可回溯、可审计 |

## 固定模块

| 顺序 | section_id | Markdown 标题 |
|---:|---|---|
| 01 | `executive_summary` | 一、执行摘要 |
| 02 | `key_changes` | 二、关键变化概览 |
| 03 | `operating_quality` | 三、经营质量分析 |
| 04 | `profitability_and_cost` | 四、盈利能力与成本成因 |
| 05 | `asset_quality_working_capital` | 五、资产质量与营运资金 |
| 06 | `debt_liquidity` | 六、债务安全与流动性 |
| 07 | `cash_flow_quality` | 七、现金流质量 |
| 08 | `industry_competition` | 八、行业周期与竞争位置 |
| 09 | `strategy_policy_external_risk` | 九、战略政策与外部风险 |
| 10 | `governance_compliance_shareholders` | 十、治理合规与股东结构 |
| 11 | `valuation_expectation_gap` | 十一、A 股估值与市场预期差 |
| 12 | `risk_chain_scenario` | 十二、风险链条与情景推演 |
| 13 | `tracking_checklist` | 十三、后续跟踪清单 |
| 14 | `data_quality_traceability` | 十四、数据质量与溯源声明 |

## JSON 输出契约

完整年度诊断报告 JSON 必须包含：

```json
{
  "report_meta": {
    "company_id": "",
    "stock_code": "",
    "company_short_name": "",
    "report_year": 2025,
    "scope": "consolidated",
    "generated_at": "",
    "task_id": ""
  },
  "template": {
    "template_id": "siq_analysis_report_v1.1",
    "module_count": 14,
    "section_ids": []
  },
  "preflight": {
    "semantic_status": "ready/stale/missing",
    "postgres_status": "ready/unavailable/not_used",
    "evidence_status": "complete/partial/missing",
    "missing_inputs": [],
    "stale_inputs": []
  },
  "sections": [
    {
      "section_id": "executive_summary",
      "title": "一、执行摘要",
      "section_type": "investment_committee_memo",
      "narrative_blocks": [
        {
          "title": "经营状态定性",
          "role": "diagnosis",
          "items": []
        }
      ],
      "facts": [],
      "calculations": [],
      "judgements": [],
      "risks_or_improvement_conditions": [],
      "evidence_ids": [],
      "review_required": false
    }
  ],
  "quality_report": {
    "module_count": 14,
    "missing_section_ids": [],
    "section_order_valid": true,
    "tool_sections_misused": [],
    "all_key_numbers_have_evidence": true,
    "prohibited_outputs": [],
    "review_queue": []
  },
  "evidence_index": []
}
```

若某个字段无法取得，必须保留字段并写明 `missing`、`unknown` 或 `not_used`，不得删除结构或用模型猜测补齐。

## 章节要求

### 一、执行摘要

- 一句话概括公司当前状态，必须同时包含“经营状态 + 主要矛盾 + 二级市场含义”。
- 输出 3-5 点核心判断，每点包含判断、证据、含义。
- 必须覆盖盈利质量、现金流、资产/债务安全、行业/竞争、治理/外部风险中的至少 4 类。
- 用红旗、黄旗、观察项表格列出重大事项，不得使用分数。
- 列出 3-5 个能改变当前结论的关键改善条件。

### 二、关键变化概览

必须给核心指标表，列示本年、上年、变化、初步解读和证据。至少覆盖：

- 营业收入
- 毛利率
- 归母净利润
- 扣非归母净利润
- 经营现金流净额
- 自由现金流
- 总资产
- 资产负债率
- 货币资金
- 存货
- 应收账款/票据

若数据库与 wiki JSON 冲突，写明采用口径和舍弃口径。若缺少上年数据，不得外推，写“数据缺失”。

### 三、经营质量分析

- 分析收入增长或下滑来自销量、价格、产品结构、区域、客户还是行业周期。
- 判断收入变化是否与销售收现、应收、存货匹配。
- 检查是否存在年末集中确认、赊销拉动收入、合同负债异常变化。
- 分析经营稳定性、业务韧性、毛利率弹性、费用刚性、客户粘性和订单可持续性。

### 四、盈利能力与成本成因

- 区分主营利润、投资收益、公允价值变动、政府补助、资产减值、信用减值、非经常性损益。
- 毛利率明显变化时，从售价、原材料/能源、产能利用率、产品结构、减值或一次性因素解释。
- 必须回答归母净利润与扣非净利润是否背离，非经常性损益是否掩盖主营下滑，利润改善是否能由主营经营解释。
- 杜邦分析嵌入本节；字段不足时列明缺失字段，不得强算。

### 五、资产质量与营运资金

- 识别经营性资产、金融资产、固定资产/在建工程、商誉、其他应收款等占比变化。
- 分析存货余额、存货周转天数 DIO、存货跌价准备。
- 分析应收账款/票据、应收周转天数 DSO、信用减值损失。
- 计算或说明无法计算 DSO、DIO、DPO、CCC；无法计算时列出缺失字段。

### 六、债务安全与流动性

- 检查货币资金、受限资金、短期借款、一年内到期非流动负债、应付票据、流动比率、速动比率、现金比率。
- 债务覆盖能力至少看：货币资金/短期有息负债、EBIT/利息费用、经营现金流/总债务、资产负债率。
- 不得只凭资产负债率下结论，必须结合账面流动性、现金覆盖、现金流覆盖、融资续接能力。
- Altman Z-Score 仅在适用且字段充分时嵌入本节；字段不足时说明原因。

### 七、现金流质量

- 解释经营现金流偏离净利润的原因。
- 计算自由现金流：经营现金流净额 - 购建固定资产、无形资产和其他长期资产支付的现金。
- 若自由现金流连续为负且债务上升，提示外部融资依赖增强。
- 说明公司是在扩张、维持、收缩、偿债，还是依赖融资维持现金余额。

### 八、行业周期与竞争位置

- 判断行业处于导入、成长、成熟、衰退、出清或技术替代阶段，并说明证据。
- 优先使用同申万二级/三级公司；样本少于 3 家时标注“样本量不足，对比仅供参考”。
- 竞争力判断必须覆盖成本曲线、产品壁垒、客户质量、认证资质、研发投入、价格传导能力。

### 九、战略政策与外部风险

- 用 A 股二级市场语言解释政策和外部风险，不得停留在政策口号。
- 至少覆盖政策 Beta、出口/汇率/关税、原材料/能源、供应链/客户集中、ESG/安全环保。
- 每个外部变量必须写清楚“变量 -> 公司经营 -> 财务报表 -> 二级市场含义”的传导路径。

### 十、治理合规与股东结构

- 检查实控人稳定性、股权质押/冻结、关联交易、资金占用、违规担保、重大诉讼、行政处罚、审计意见、内控缺陷、董监高变动、审计机构变更。
- 发现异常只能写“风险信号/需核验”，不得直接定性违法犯罪。

### 十一、A 股估值与市场预期差

- 仅在具备股价、市值、股本、同业估值或历史估值数据时分析。
- 若没有，必须写“估值数据缺口”，不得生成伪估值。
- 必须区分基本面改善、会计利润改善、估值修复、主题交易。
- 允许结论类型包括：价值陷阱、困境反转待验证、现金流防御型、周期底部待确认、成长兑现不足、高估值依赖强预期、估值数据不足无法判断。

### 十二、风险链条与情景推演

- 至少写 2-3 条因果链。
- 格式：`触发因素 -> 经营影响 -> 财务报表影响 -> 现金流/债务后果 -> 二级市场含义`。
- 情景推演至少包含改善情景、中性情景、压力情景。
- 禁止给没有依据的概率和精确利润弹性。

### 十三、后续跟踪清单

必须列出跟踪指标、当前状态、改善信号、恶化信号、频率、数据源。至少覆盖：

- 毛利率
- 经营现金流
- 存货
- 短债覆盖
- 治理事项

### 十四、数据质量与溯源声明

必须包含：

- 数据来源：wiki metrics、wiki evidence、PostgreSQL 三表/宽表、document_tables、年报原文。
- 数据新鲜度：PDF 解析产物生成时间、Wiki 入库时间或 artifact_manifest 版本、PostgreSQL 入库状态、规则语义层生成时间、LLM 语义增强生成时间。
- 数据质量检查：三表钩稽、wiki 与数据库口径、单位换算、缺失字段、可疑表格。
- 无法计算或仅方向性判断的模型：杜邦、CCC、FCF、Altman Z-Score、估值判断等。
- 关键证据索引：核心数字必须回溯到 task_id、pdf_page、table_index、md_line。
- 人工复核清单：证据链不完整、口径差异、review_queue、原文含糊、PDF 页码无法回溯、法律/监管/治理证据不足事项。
- 限制与免责声明：公开信息财务诊断，不构成投资建议。

## HTML 输出要求

- HTML 必须包含至少 1 个可视化图表；Chart.js 不可用时使用内嵌 SVG。
- HTML 必须使用浅色背景、深色文字，禁止暗黑主题和低对比渐变。
- HTML 不使用侧边目录导航栏；主体宽度优先留给正文、表格和图表。
- 报告主体容器使用稳定宽度约束，例如 `max-width: 1120px; width: 100%; margin: 0 auto;`。
- 每个报告章节容器必须成对闭合。
- 结构校验必须通过：报告章节数为 14；`<section class="section">` 为 14；总 `<section` 与 `</section>` 数量一致。

## 模板执行红线

- 不得输出少于或多于 14 个一级大模块的完整年度报告。
- 不得调整 14 个大模块顺序；局部分析除外。
- 不得把杜邦分析、现金转换周期、自由现金流、Altman Z-Score、三表钩稽等工具模型作为一级大模块。
- 不得在 JSON 中缺失 `template`、`preflight`、`sections`、`quality_report`、`evidence_index`。
- 不得输出“综合评分”“维度得分”“评级 AAA/CCC/A-E”“目标价”“买入/卖出”等内容，除非用户明确要求且提供市场数据。
- 不得只写指标变化，必须解释成因和传导路径。
- 不得编造缺失的 PDF 页码、市场估值、同行数据、订单数据、客户结构。
- 不得把政府补助、投资收益、公允价值收益直接等同于主营改善。
- 不得把政策支持、国产替代、AI、低空、机器人等主题词直接等同于公司基本面改善。
- 不得把异常信号定性为舞弊或违法；只能写风险信号和待核验事项。
- 不得在未执行 `validate_report_quality.py` 或验收失败时宣称完整年度报告已完成。
- 不得在未执行 `repair_report_citations.py` 时宣称完整年度报告已完成。
- 不得让最终报告残留 `{revenue_2025}`、`{profit_change}`、`{company_name}` 等模板占位符。
- 不得输出没有图表的 HTML 年度报告。
- 不得输出无 `/api/pdf_page/` 证据链接的完整报告；页码确实缺失时，必须在第十四章列入证据缺口并至少保留 source/table/task_id。
- 不得让“pdf_page=未返回 + table_index=数字”的引用进入最终报告；这类引用必须先尝试自动回填 PDF 页码和原页链接。
