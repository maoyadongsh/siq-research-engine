# SIQ_factchecker

SIQ_factchecker 是 SIQ 系列里的事实核查智能体，负责对 `SIQ_analysis` 已生成的 A 股上市公司财务分析报告进行后置审校。它关注事实、计算、证据链、逻辑支撑、A 股二级市场风险完整性和模板合规性。

这个 agent 不是评分器。当前版本为 v2，无百分制、无 A/B/C/D 评级、无综合得分，只输出 `approve`、`request_changes`、`block` 三类审校结论。

## 0. 当前检查结论

检查时间：2026-05-29。`http://127.0.0.1:8649/health` 返回正常。当前 profile 的 `state.db` 中有 60 个 sessions、1111 条 messages。主前端 `/verify` 页面通过聚合后端 `/api/factchecker/chat/*` 调用该 Agent，并读取 Wiki 中 `factcheck/*.html` 结果。

核查产物标准位置：

```text
/home/maoyd/wiki/companies/<company_id>/factcheck/<stock_code>-<short_name>-<year>-factcheck.{json,html}
```

### 决赛关注点

| 维度 | 本 Agent 贡献 |
| --- | --- |
| 创新性 | 把生成报告后的事实、计算、证据链和风险遗漏审计做成独立智能体，而不是让生成者自我证明 |
| 技术难度 | 需要跨 Wiki metrics、evidence、PostgreSQL 表格页码、报告 JSON/Markdown 做一致性核查 |
| 完成度 | 已接入 Hermes gateway、聚合后端和前端事实核查页，输出独立 `factcheck` 目录 |
| 商业价值 | 降低投研报告幻觉和错引风险，适合金融场景里“生成-复核-修订”的工作流 |

### 评委技术说明

`siq_factchecker` 是报告生产链路中的独立审校层。它与 `siq_analysis` 分离，避免“生成者自证正确”，并将金融报告常见风险拆成事实、计算、证据、逻辑、模板和二级市场风险六类检查。

| 核查维度 | 技术实现 | 判定目标 |
| --- | --- | --- |
| 技术架构 | Hermes profile + CLI 核查引擎 + Wiki/PostgreSQL 证据读取 + HTML 渲染 | 与分析 Agent 解耦，形成独立审校链 |
| 技术栈 | Python、Hermes、Pydantic/JSON、PostgreSQL 查询、Wiki 文件读取 | 支撑批量核查和前端展示 |
| 数据流 | 读取分析报告 -> 抽取声明和数字 -> 对照 Wiki/DB/PDF 证据 -> 标注问题等级 -> 输出 verdict 与 HTML | 形成生成后审计闭环 |
| 事实一致性 | 读取分析报告 JSON/Markdown、Wiki metrics、evidence 和 PostgreSQL 表格来源 | 数字、年份、公司、指标口径是否一致 |
| 计算正确性 | 三大表勾稽、同比/比率公式、单位归一、容差判断 | 计算是否能由源数据推出 |
| 证据链完整性 | 检查 `task_id`、`pdf_page`、`table_index`、`md_line` 和可打开链接 | 结论是否可复核 |
| 逻辑支撑 | 判断风险链、改善条件和经营解释是否被证据支撑 | 避免“数据正确但结论跳跃” |
| 模板合规 | 无评分层、无评级、无目标价、章节结构和免责声明检查 | 满足 A 股二级市场公开报告边界 |
| 风险遗漏 | ST/退市、审计意见、问询函、处罚、质押、减持、商誉、政府补助等清单 | 发现会影响投研判断的缺失点 |

输出采用 `approve`、`request_changes`、`block` 三类 verdict，而不是百分制评分。这样更贴近真实投研审稿流程：报告不是“得 83 分就能用”，而是必须知道哪些问题可放行、哪些需要修改、哪些阻断发布。

## 1. 基本信息

| 项目 | 当前配置 |
| --- | --- |
| Hermes profile | `/home/maoyd/.hermes/profiles/siq_factchecker` |
| 角色文件 | `SOUL.md` |
| 默认模型 | `kimi-for-coding` |
| 模型 provider | `kimi-coding` |
| API Server | `127.0.0.1:8649` |
| 启动器 | `./SIQ_factchecker` |
| Canonical CLI | `scripts/factcheck_cli.py` |
| 兼容入口 | `scripts/factcheck_engine.py` |
| Wiki 数据目录 | `/home/maoyd/wiki` |
| PostgreSQL | `127.0.0.1:5432 / ai_platform / pdf2md` |

注意：`.env` 中包含本地数据库和模型访问凭据，不要提交到公开仓库，不要粘贴到外部系统。

## 2. Agent 定位

SIQ_factchecker 的工作对象是分析报告，而不是原始年报解析任务。它默认假设上游已经由 `SIQ_analysis` 生成报告，然后对报告进行复核。

它需要回答三个问题：

1. 报告中的事实、数据、计算是否可信。
2. 报告中的判断是否有证据支撑。
3. 报告是否遗漏会影响 A 股二级市场判断的关键风险。

它不会输出投资建议，不负责替代研究员做买卖判断，也不会因为报告“看起来不错”而放行。所有结论必须来自可定位的问题清单。

## 3. 目录结构

```text
/home/maoyd/.hermes/profiles/siq_factchecker
├── SOUL.md
├── README.md
├── config.yaml
├── .env
├── SIQ_factchecker
├── scripts/
│   ├── factcheck_cli.py
│   ├── factcheck_engine.py
│   ├── generate_factcheck_html.py
│   └── wiki_data_accessor.py
├── skills/data-science/financial-report-factcheck/
│   ├── SKILL.md
│   └── references/factcheck_verification.py
├── memories/
├── logs/
├── sessions/
└── state.db
```

关键文件说明：

| 文件 | 作用 |
| --- | --- |
| `SOUL.md` | Hermes agent 的核心工作规则，定义无评分层、六维核查、verdict 规则。 |
| `SIQ_factchecker` | Bash 启动器，加载 `.env` 后调用 `scripts/factcheck_cli.py`。 |
| `scripts/factcheck_cli.py` | 当前主实现，包含 CLI、核查引擎、PostgreSQL 证据增强、JSON 输出。 |
| `scripts/factcheck_engine.py` | 兼容包装器，转调 `factcheck_cli.py`，避免旧入口输出评分字段。 |
| `scripts/wiki_data_accessor.py` | Wiki 公司目录、metrics、evidence、report 文件读取层。 |
| `scripts/generate_factcheck_html.py` | 将 v2 factcheck JSON 渲染为 HTML。 |
| `skills/.../SKILL.md` | 可被 Hermes skill 系统引用的核查流程说明。 |

## 4. 输入与输出

### 输入报告

默认核查 `SIQ_analysis` 生成的分析报告：

```text
/home/maoyd/wiki/companies/<company_id>/analysis/<stock_code>-<company_short_name>-<year>-analysis.md
/home/maoyd/wiki/companies/<company_id>/analysis/<stock_code>-<company_short_name>-<year>-analysis.json
```

示例：

```text
/home/maoyd/wiki/companies/600399-抚顺特钢/analysis/600399-抚顺特钢-2025-analysis.md
/home/maoyd/wiki/companies/600399-抚顺特钢/analysis/600399-抚顺特钢-2025-analysis.json
```

### 输出文件

`verify` 会把事实核查结果写入独立的 `factcheck/` 目录：

```text
/home/maoyd/wiki/companies/<company_id>/factcheck/<stock_code>-<company_short_name>-<year>-factcheck.json
```

HTML 报告由独立脚本生成，默认与 JSON 保存在同一目录：

```text
/home/maoyd/wiki/companies/<company_id>/factcheck/<stock_code>-<company_short_name>-<year>-factcheck.html
```

当前 600399 示例产物：

```text
/home/maoyd/wiki/companies/600399-抚顺特钢/factcheck/600399-抚顺特钢-2025-factcheck.json
/home/maoyd/wiki/companies/600399-抚顺特钢/factcheck/600399-抚顺特钢-2025-factcheck.html
```

## 5. 数据源优先级

### 5.1 Wiki 本地结构化数据

第一优先级来自 `/home/maoyd/wiki/companies/<company_id>/`：

```text
company.json
metrics/three_statements.json
metrics/key_metrics.json
metrics/validation.json
evidence/evidence_index.json
evidence/pdf_refs.json
reports/<report_id>/report.md
reports/<report_id>/document_full.json
```

`three_statements.json` 是主要指标源。当前实现会读取 `data.metrics[]` 中的 `metric_key`、`normalized_value`、`statement_type`、`source` 等字段。

`key_metrics.json` 用于多年份关键指标、同比异常和基数效应检查。

`evidence_index.json` 用于判断本地证据链是否存在，并辅助追溯 PDF 页、表格编号和 Markdown 行。

### 5.2 PostgreSQL 证据增强

第二优先级是 PostgreSQL，只读使用：

```text
host=127.0.0.1
port=5432
database=ai_platform
schema=pdf2md
```

重点表：

```text
pdf2md.financial_balance_sheet_items
pdf2md.financial_income_statement_items
pdf2md.financial_cash_flow_statement_items
pdf2md.financial_all_metrics_wide
pdf2md.financial_key_metrics
pdf2md.companies
pdf2md.documents
pdf2md.company_filings
pdf2md.document_tables
pdf2md.evidence_citations
```

当前 CLI 使用三张明细表联查 `document_tables`，生成 `evidence_summary`：

```text
financial_*_items.task_id + source_table_index
  -> document_tables.task_id + table_index
  -> pdf_page_number / markdown_line
```

数据库不可用时不会阻断核查，但 JSON 中会标记：

```json
"database_status": "unavailable"
```

### 5.3 年报原文与完整解析产物

当 wiki 指标或数据库证据不足时，可以回退读取：

```text
reports/<report_id>/report.md
reports/<report_id>/document_full.json
```

当前自动化 CLI 对这些文件的利用还比较轻，更多作为 agent 交互核查时的 fallback。

## 6. 六维核查逻辑

### 6.1 数据原文一致性 `data_consistency`

目标：确认报告中的核心数据能被本地 metrics 或 PostgreSQL 证据支撑。

重点指标包括：

```text
营业收入、营业成本、净利润、归母净利润、扣非归母净利润、总资产、总负债、所有者权益、货币资金、存货、应收账款、短期借款、经营现金流、投资现金流、筹资现金流、资产减值、信用减值
```

当前实现会检查核心指标是否可在报告中清晰出现，并尝试拉取 PostgreSQL 证据摘要。如果 PostgreSQL 没有返回证据，会生成 warning。

### 6.2 计算公式一致性 `calculation_consistency`

目标：复核计算口径和异常变动。

当前自动化检查包括：

- 毛利率口径提示：如果报告使用毛利率且本地仅能识别 `operating_cost`，会提示人工确认该字段是否为“营业成本”而非“营业总成本”。
- 同比异常提示：如果关键指标 2025/2024 同比变动绝对值超过 500%，且报告中提到该指标，会提示补充基数效应说明。

重要边界：当前版本不会在营业成本口径不明时直接判定毛利率错误，避免把“营业总成本”误当“营业成本”。

后续可扩展的公式检查：

```text
毛利率 = (营业收入 - 营业成本) / 营业收入
资产负债率 = 负债合计 / 资产总计
流动比率 = 流动资产 / 流动负债
速动比率 = (流动资产 - 存货) / 流动负债
现金短债覆盖 = 可用货币资金 / 短期有息债务
自由现金流 = 经营现金流净额 - 资本开支
ROE / 杜邦拆解
现金转换周期
```

### 6.3 证据链完整性 `traceability`

目标：确认关键数据点可追溯。

当前自动化检查包括：

- 报告 Markdown 中是否存在 `^[]` 或 `[^N]` 证据标记。
- 数值型数据点数量与证据标记覆盖情况。
- 本地 `evidence_index.json` 是否可用。
- PostgreSQL 是否能补充证据摘要。

典型 warning：报告有大量数值，但 Markdown 证据标记为 0。

### 6.4 结论支撑充分性 `logic_support`

目标：检查定性判断是否被附近数据支撑，以及是否存在结论和数据方向相反。

当前自动化检查包括：

- “现金流充裕/强劲/健康”附近是否出现负经营现金流。
- “偿债能力强/良好/无忧”附近是否出现短期偿债压力、现金覆盖不足、流动性紧张。
- “盈利能力强/优秀/改善”附近是否出现亏损或负净利润。
- “估值合理/安全边际/困境反转/拐点/高确定性”等定性判断附近是否缺少量化支撑。

### 6.5 A 股风险完整性 `a_share_risk_completeness`

目标：检查报告是否覆盖国内二级市场常见风险。

当前自动化检查分组：

```text
ST/退市风险：ST、退市、净资产、持续亏损
审计与内控：审计意见、内控、强调事项
监管问询处罚：问询、监管、处罚、立案
股东质押减持解禁：质押、减持、解禁、冻结
关联与担保：关联交易、资金占用、担保
减值风险：商誉、资产减值、信用减值
```

如果某组完全未出现，会生成 warning。

### 6.6 模板与规则合规性 `template_compliance`

目标：检查报告是否符合 SIQ_analysis v3 的无评分模板。

当前自动化检查包括：

- 禁止字段：`综合得分`、`overall_score`、`overall_rating`、`评级为`、`总分`。
- 必备语义模块：`核心判断`、`证据`、`现金流`、`偿债`、`风险`、`跟踪`、`情景`。

如果报告仍出现评分层字段，会标记为 critical。

## 7. verdict 判定规则

verdict 只由问题清单决定，不由分数决定。

| verdict | 条件 |
| --- | --- |
| `approve` | 无 critical，warning 少于 3 个。 |
| `request_changes` | 有 1 个 critical，或 warning 达到 3 个及以上。 |
| `block` | critical 达到 2 个及以上，或报告/公司无法定位。 |

当前实现位置：`FactCheckEngine._decide_verdict()`。

## 8. 输出 JSON 结构

标准 v2 JSON 顶层字段：

```json
{
  "verdict": "request_changes",
  "company_id": "600399-抚顺特钢",
  "report_file": "600399-抚顺特钢-2025-analysis.md",
  "summary": {
    "critical": 0,
    "warning": 4,
    "suggestion": 0,
    "database_status": "available",
    "database_error": "",
    "evidence_rows": 24
  },
  "checks": {
    "data_consistency": {"status": "pass", "issues": []},
    "calculation_consistency": {"status": "warning", "issues": []},
    "traceability": {"status": "warning", "issues": []},
    "logic_support": {"status": "warning", "issues": []},
    "a_share_risk_completeness": {"status": "pass", "issues": []},
    "template_compliance": {"status": "pass", "issues": []}
  },
  "evidence_summary": [],
  "recommendations": [],
  "verified_at": "2026-05-16T21:04:00+08:00"
}
```

禁止出现：

```text
overall_score
overall_rating
score
max_score
```

## 9. CLI 使用

进入 profile 目录：

```bash
cd /home/maoyd/.hermes/profiles/siq_factchecker
```

列出公司：

```bash
./SIQ_factchecker list
```

查看公司信息：

```bash
./SIQ_factchecker info 600399
./SIQ_factchecker info 600399-抚顺特钢
```

检查核查前置条件：

```bash
./SIQ_factchecker check 600399 --year 2025
```

执行事实核查：

```bash
./SIQ_factchecker verify 600399 --year 2025
```

查看所有公司核查状态：

```bash
./SIQ_factchecker status
```

生成 HTML：

```bash
python3 scripts/generate_factcheck_html.py /home/maoyd/wiki/companies/600399-抚顺特钢/factcheck/600399-抚顺特钢-2025-factcheck.json
```

## 10. API Server 使用

该 profile 的 API Server 配置在 `config.yaml`：

```yaml
platforms:
  api_server:
    enabled: true
    extra:
      host: 127.0.0.1
      port: 8649
```

健康检查：

```bash
curl -s http://127.0.0.1:8649/health
```

注意：API Server key 当前在 `config.yaml` 中为空，实际认证行为可能由 Hermes gateway 或外层环境控制。如果通过 API 调用出现认证错误，应检查 gateway 启动参数、`API_SERVER_KEY` 和 Hermes Web UI 侧配置。

## 11. 当前 600399 样例结果

最近一次对 `600399-抚顺特钢` 的核查结果：

```text
verdict: request_changes
critical: 0
warning: 4
suggestion: 0
PostgreSQL: available
evidence_rows: 24
```

主要问题：

- 毛利率涉及营业成本口径，需要确认 `operating_cost` 是营业成本还是营业总成本。
- 基本每股收益同比变动幅度极大，需要解释基数效应或经营原因。
- 报告包含大量数值型数据点，但 Markdown 未使用 `^[]` 或 `[^N]` 证据标记。
- “困境反转”等定性判断附近缺少量化支撑。

## 12. 已知边界与风险

### 12.1 毛利率口径

A 股利润表里“营业成本”和“营业总成本”容易混淆。当前 CLI 不会在口径不明时直接判定毛利率计算错误，只给 warning。若要自动判定，需要从原始表格或数据库中确认项目名称和口径。

### 12.2 证据标记覆盖

当前 Markdown 证据链检查主要看 `^[]` 和 `[^N]`。如果报告用自然语言写“PDF第77页表格68”，但没有脚注标记，自动化仍会提示证据标记不足。这是为了推动报告结构化证据引用。

### 12.3 PostgreSQL 证据摘要

当前 `evidence_summary` 是增强证据摘要，不等于完整核查。它按股票代码或公司简称匹配三张财务明细表，并联表 `document_tables` 补充 PDF 页和 Markdown 行。若数据库中 `stock_code` 为空，会使用 `stock_name` 匹配。

### 12.4 A 股风险完整性

当前风险完整性检查是关键词级启发式。它能发现“完全没提某类风险”的问题，但不能证明风险不存在。更强版本应接入监管公告、交易所问询、股东质押、解禁、处罚等结构化数据源。

### 12.5 非 2025 年报

CLI 默认 `--year 2025`。核查其他年份时，需要确保 wiki 目录中存在对应年份分析报告、metrics 和数据库记录。

## 13. 常见问题排查

### 报告不存在

现象：`check` 显示 `.md` 或 `.json` 缺失。

处理：先运行或修复 `SIQ_analysis`，确保目标路径下存在分析报告。

### PostgreSQL 不可用

现象：`database_status=unavailable` 或 `PostgreSQL证据: ✗ 不可用`。

检查：

```bash
nc -vz 127.0.0.1 5432
```

确认 `.env`：

```text
DB_HOST=127.0.0.1
DB_PORT=5432
DB_NAME=ai_platform
DB_USER=dgx
DB_SCHEMA=pdf2md
```

如果数据库连通但 rows=0，优先检查 `stock_code` 是否为空、`stock_name` 是否能匹配公司简称、`report_year` 是否正确。

### 输出里出现评分字段

现象：factcheck JSON 中出现 `overall_score`、`overall_rating`、`score` 或 `max_score`。

处理：说明旧脚本或旧产物被调用。优先使用：

```bash
./SIQ_factchecker verify <stock_code> --year <year>
```

并确认 `scripts/factcheck_engine.py` 仍然只是兼容 wrapper。

### HTML 生成失败

先确认 JSON 是 v2 结构：

```bash
python3 -m json.tool /path/to/factcheck.json
```

再运行：

```bash
python3 /home/maoyd/.hermes/profiles/siq_factchecker/scripts/generate_factcheck_html.py /path/to/factcheck.json
```

## 14. 维护规范

### 更新 SOUL.md 后

如果更新了核心核查规则，应同步检查：

```text
skills/data-science/financial-report-factcheck/SKILL.md
scripts/factcheck_cli.py
README.md
```

规则、skill、CLI、README 必须保持一致。

### 更新 CLI 后

至少运行：

```bash
python3 -m py_compile scripts/factcheck_cli.py scripts/factcheck_engine.py scripts/generate_factcheck_html.py scripts/wiki_data_accessor.py
./SIQ_factchecker check 600399 --year 2025
./SIQ_factchecker verify 600399 --year 2025
```

### 更新输出结构后

同步更新：

```text
scripts/generate_factcheck_html.py
README.md 的 JSON 示例
SOUL.md 的输出格式
SKILL.md 的输出结构
```

### 更新数据库 schema 后

重点检查：

```text
PostgresEvidenceAccessor.fetch_company_evidence()
pdf2md.financial_*_items 字段
pdf2md.document_tables 字段
stock_code / stock_name / report_year 匹配规则
```

## 15. 后续优化路线

建议按优先级推进：

1. 把 `evidence_summary` 扩展为逐指标证据链校验，而不只是摘要。
2. 增加 report JSON 内 evidence 字段与 `evidence_index.json` 的双向一致性检查。
3. 增加完整公式复核：资产负债率、流动比率、速动比率、现金短债覆盖、自由现金流、ROE、杜邦拆解。
4. 引入 A 股公告和监管数据源，强化质押、减持、解禁、问询、处罚、审计意见检查。
5. 增加批量核查模式，输出全工作集 factcheck dashboard。
6. 将 `analysis_claims`、`claim_evidence_links`、`review_feedback` 等 PostgreSQL 表纳入结构化审校闭环。
