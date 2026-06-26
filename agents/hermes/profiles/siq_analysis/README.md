# SIQ Analysis Agent

`siq_analysis` 是一个面向 A 股上市公司的年度财务诊断型 Hermes Agent。它基于本地 wiki 知识库和 `pdf2md` PostgreSQL 数据库，生成可追溯、可复核、适合二级市场语境的公司财务分析报告。

它不是一级市场投委会 Agent，也不是简单的财务指标摘要器。它的核心任务是把年报数据解释成经营质量、盈利成因、资产质量、现金流质量、债务安全、行业周期、治理合规和 A 股市场预期差。

## 0. 当前检查结论

检查时间：2026-05-29。`http://127.0.0.1:8651/health` 返回正常。当前 profile 的 `state.db` 中有 308 个 sessions、4669 条 messages，说明该智能体已有较多分析调试与生成历史；这些状态用于本地运行和检索，不作为对外公开数据。

当前主执行入口仍是确定性流水线：

```bash
/home/maoyd/.hermes/profiles/siq_analysis/scripts/run_analysis_report.py --company <股票代码或简称> --year <年度>
```

它生成的最终报告落在：

```text
/home/maoyd/wiki/companies/<company_id>/analysis/<stock_code>-<short_name>-<year>-analysis.{md,json,html}
```

### 决赛关注点

| 维度 | 本 Agent 贡献 |
| --- | --- |
| 创新性 | 不是摘要器，而是“证据事实 -> 经营解释 -> 风险链条 -> 后续跟踪信号”的经营诊断型智能体 |
| 技术难度 | 14 章模板、Wiki 全量盘点、证据包、引用修复、质量门禁、杜邦/FCF/CCC/Altman 等模型降级处理 |
| 完成度 | 已有脚本化流水线、检查点、HTML 渲染、质量验收与前端报告页展示 |
| 商业价值 | 把年报处理从人工摘数升级为可复核、可复用的公司年度诊断报告生产流程 |

### 评委技术说明

`siq_analysis` 是 SIQ 的报告生成中枢。它的核心技术路线不是让大模型直接阅读整本年报后自由发挥，而是先由 Wiki metrics、evidence、semantic 和 PostgreSQL 证据包提供事实，再由模板和质量门禁约束模型完成经营诊断。

| 环节 | 技术实现 | 产物/价值 |
| --- | --- | --- |
| 技术架构 | Hermes profile + 确定性报告脚本 + Wiki/PostgreSQL 证据层 + HTML 渲染器 | 将模型生成约束在可审计流水线中 |
| 技术栈 | Python、Hermes Runs、Kimi provider、本地 Wiki、PostgreSQL、HTML renderer | 兼顾自动生成、证据检索和前端展示 |
| 数据流 | 用户指定公司/年份 -> catalog 定位 -> 证据包装配 -> 分章节生成 -> 引用修复 -> 质量验收 -> HTML 落盘 | 让报告生成路径可复现 |
| 公司与年度定位 | `resolve_company.py` 读取 catalog，避免手写路径或误识别公司 | 确保报告主体准确 |
| 证据装配 | metrics、evidence、semantic、report.md、PostgreSQL 三表数据 | 形成可引用的事实包 |
| 分析生成 | `run_analysis_report.py`、14 章模板、分章节草稿、检查点恢复 | 生成结构稳定的年度诊断报告 |
| 经典模型 | 毛利率、扣非、经营现金流/净利润、FCF、流动比率、资产负债率、杜邦/CCC/Altman 降级计算 | 用财务模型支撑判断，但字段不足时明确降级 |
| 引用修复 | `repair_report_citations.py`、PDF 页码/表格/Markdown 行补全 | 保证关键结论可回溯 |
| 质量验收 | `validate_report_quality.py`、无评分层、证据链、风险链条和越界表述检查 | 避免生成投资评级、目标价或不可审计结论 |
| 渲染展示 | `html_renderer_v2.py` 输出 HTML，前端 `/analysis` iframe 展示 | 将报告转为可读、可下载、可分享产物 |

算法模型的亮点在于“财务诊断链条”：每个核心判断都按事实、计算、解释、风险/改善条件组织，而不是简单罗列指标。模型可以参与文字推理，但数字、页码、表格和证据 ID 必须来自已入库材料；当字段不足时，报告必须写明模型无法可靠计算，而不是补假设值。

## 当前状态

- Profile 路径：`/home/maoyd/.hermes/profiles/siq_analysis`
- Hermes gateway：`http://127.0.0.1:8651`
- 模型：`kimi-for-coding`
- Provider：`kimi-coding`
- 默认知识库：`/home/maoyd/wiki`
- 当前 wiki 工作集：以 `/home/maoyd/wiki/_meta/company_catalog.json` 实时内容为准，不维护静态公司数量
- PostgreSQL：`127.0.0.1:5432 / ai_platform / schema pdf2md`
- 本地查询 API：`http://127.0.0.1:18888/query`

## 核心定位

这个 Agent 的报告风格是“经营诊断型”，而不是“评分评级型”。

它必须回答：

- 公司当前主要矛盾是什么。
- 利润是真改善、会计改善、非经常性收益驱动，还是资产减值/成本压力导致恶化。
- 现金流是否支撑利润。
- 资产质量是否恶化。
- 债务和流动性是否安全。
- 行业周期和竞争位置是否支持公司改善。
- 治理、监管和 A 股交易层面的风险是否会影响估值折价。
- 哪些证据可以推翻当前判断。

## 强制工作规则

完整规则写在 `SOUL.md` 的 `强制工作规则` 章节。这里是维护摘要：

1. 先定位公司、年份、报告口径，再开始分析。
2. 默认数据优先级为 wiki metrics、wiki evidence、semantic/年报原文、PostgreSQL、18888 API。
3. 所有关键数字必须有证据链。
4. 数据库只能只读查询。
5. 禁止输出综合评分、维度评分、星级、AAA/CCC、A-E 评级。
6. 允许使用高/中/低风险、安全/承压/脆弱/失衡、红旗/黄旗/观察项等定性标签。
7. 每个核心结论必须形成“事实 -> 解释 -> 财务影响 -> 后续验证”的链条。
8. A 股语境必须覆盖 ST/退市、审计意见、问询函、监管处罚、股权质押、减持、限售解禁、再融资、资金占用、违规担保、商誉减值、政府补助依赖等风险。
9. 无实时股价、市值、股本、同业估值数据时，不得判断估值是否充分反映风险。
10. 输出前必须自检：证据链、风险传导、数据缺口、模型无法计算项、越界表述。

## 数据源

### 1. Wiki 主数据

默认从 `/home/maoyd/wiki` 读取：

- `_meta/company_catalog.json`
- `companies/<company_id>/company.json`
- `companies/<company_id>/metrics/three_statements.json`
- `companies/<company_id>/metrics/key_metrics.json`
- `companies/<company_id>/metrics/validation.json`
- `companies/<company_id>/evidence/evidence_index.json`
- `companies/<company_id>/semantic/retrieval_index.json`
- `companies/<company_id>/reports/<report_id>/report.md`

wiki 的 `metrics/*.json` 是归一化主数据。若数据库数据与 wiki 冲突，默认采用 wiki，并在报告的数据质量说明中列出口径差异。

### 2. PostgreSQL 数据库

首选本地直连：

```text
Host: 127.0.0.1
Port: 5432
Database: ai_platform
Schema: pdf2md
User: dgx
```

外部地址 `192.168.2.121:5432` 曾出现 PostgreSQL 握手超时或服务端关闭连接。优先使用 `127.0.0.1:5432`。

当前已确认 `pdf2md` schema 有 31 张表，覆盖 `_meta/company_catalog.json` 列出的全部公司、2025 年年报（实际公司数以 catalog 为准）。重点表：

- `financial_balance_sheet_items`：资产负债表明细，3703 行
- `financial_income_statement_items`：利润表明细，2347 行
- `financial_cash_flow_statement_items`：现金流量表明细，2631 行
- `financial_all_metrics_wide`：财务指标宽表，106 行，JSONB 聚合结构
- `financial_key_metrics`：关键指标，830 行
- `document_tables`：表格结构，7643 行，含 PDF 页码和 markdown 行号
- `document_pages`：PDF 页面，5251 行
- `content_blocks`：文本块，68676 行
- `evidence_citations`：证据引用，8927 行
- `quality_warnings`：质量警告，23 行

当前为空的表包括：

- `analysis_claims`
- `claim_evidence_links`
- `evaluation_results`
- `evaluation_runs`
- `financial_note_links`
- `generated_reports`
- `gold_financial_items`
- `report_sections`
- `review_feedback`
- `toc_entries`

### 3. 本地查询 API

当只需快速查单指标，或直连数据库失败时，可使用：

```text
POST http://127.0.0.1:18888/query
```

示例问题：

```text
600399 抚顺特钢 2025 营业收入
600399 抚顺特钢 2025 经营活动现金流量净额
抚顺特钢 2025 总资产
```

注意：18888 API 当前主要覆盖四张财务表，不是完整证据/报告表浏览器。完整证据链应优先直连 PostgreSQL。

## 证据链规则

三大表中的 `source_page_number` 可能为空，但 `source_table_index` 可用。应使用以下关联补全页码和 markdown 行号：

```sql
financial_*_items.task_id = document_tables.task_id
financial_*_items.source_table_index = document_tables.table_index
```

报告中的数据库证据链建议格式：

```text
source_table=<财务表名>,
task_id=<解析任务 ID>,
statement_id=<报表 ID>,
period_key=<期间>,
table_index=<表格索引>,
pdf_page=<PDF 页码>,
markdown_line=<markdown 行号>
```

例如抚顺特钢 2025 年部分指标已验证：

- 营业收入：`7,783,430,662.66 元`，PDF 第 77 页，表 68，markdown line 1736
- 总资产：`12,513,798,992.25 元`，PDF 第 73 页，表 66，markdown line 1713
- 经营现金流净额：`-1,370,002,307.71 元`，PDF 第 80 页，表 70，markdown line 1760

若需要原文引用，可进一步关联：

- `evidence_citations`
- `content_blocks`
- wiki 年报 markdown

不得编造 PDF 页码、markdown 行号或 quote。

## 报告模板

完整年度诊断报告必须使用 `SOUL.md` 中的“报告结构（14 大模块，强制模板）”。

14 个模块为：

1. 执行摘要
2. 关键变化概览
3. 经营质量分析
4. 盈利能力与成本成因
5. 资产质量与营运资金
6. 债务安全与流动性
7. 现金流质量
8. 行业周期与竞争位置
9. 战略政策与外部风险
10. 治理合规与股东结构
11. A 股估值与市场预期差
12. 风险链条与情景推演
13. 后续跟踪清单
14. 数据质量与溯源声明

每一节都必须尽量写成：

```text
事实 -> 解释 -> 风险/改善条件 -> 证据
```

局部分析可以裁剪模板，但仍必须保留核心判断、证据链、风险/改善条件和数据质量说明。

## 经典模型

报告生成时必须检查字段是否足够，并按需使用：

- 杜邦分析：解释 ROE 来源
- 盈利质量模型：扣非、非经常性损益、经营现金流/净利润
- 自由现金流：经营现金流净额减资本开支
- 现金转换周期：DSO、DIO、DPO、CCC
- 偿债安全：现金覆盖、利息覆盖、现金流覆盖、资产负债率
- Altman Z-Score：仅适用于非金融、制造/工业类企业
- 异常识别：收入与现金流背离、应收/存货快于收入、毛利率背离同行、减值异常

字段不足时必须写“模型无法可靠计算”，并列出缺失字段。不得用假设值强行计算。

## A 股二级市场适配

这个 Agent 必须使用国内二级市场语境，不得照搬一级市场投委会模板。

必须关注：

- ST/退市风险
- 非标审计意见
- 持续经营重大不确定性
- 交易所问询函
- 监管处罚
- 业绩预告修正
- 股权质押、冻结、减持
- 限售解禁
- 再融资
- 资金占用、违规担保
- 商誉减值、资产减值
- 政府补助依赖
- 金融资产占比过高
- 主题交易与基本面脱节

不得输出：

- 融资轮次
- 投资条款
- 回购条款
- Go/No-Go
- 投委会投票
- 一级市场综合评分

## 使用方式

### API Server

当前 gateway：

```text
http://127.0.0.1:8651
```

健康检查：

```bash
curl -s http://127.0.0.1:8651/health
```

### tmux 托管

当前 tmux session：

```text
hermes_siq_analysis
```

查看：

```bash
tmux ls
tmux attach -t hermes_siq_analysis
```

重启：

```bash
tmux send-keys -t hermes_siq_analysis C-c
tmux new-session -d -s hermes_siq_analysis 'env HERMES_HOME=/home/maoyd/.hermes /home/maoyd/.local/bin/hermes gateway restart --profile siq_analysis'
```

## 维护文件

关键文件：

- `SOUL.md`：核心行为、工作规则、报告模板
- `config.yaml`：模型、工具、gateway 配置
- `.env`：API key 和环境变量，不要提交或外泄
- `profile.yaml`：profile 元信息
- `scripts/wiki_data_accessor.py`：wiki 数据访问封装
- `scripts/siq_cli.py`：命令行辅助入口
- `logs/gateway.log`：gateway 日志
- `sessions/`：历史会话记录

`SOUL.md.bak-*` 是历史备份，可用于回滚规则，但不要自动删除。

## 安全与红线

- 不要把数据库密码、API key 写入报告正文。
- 不要在数据库执行写操作。
- 不要编造缺失数据。
- 不要生成评分层。
- 不要把风险信号直接定性为违法、舞弊或欺诈。
- 不要在缺少实时市场数据时输出目标价或买卖评级。
- 不要把政策主题直接等同于公司基本面改善。

## 推荐提问方式

```text
请分析 600399 抚顺特钢 2025 年报，使用 PostgreSQL 补充证据链。
```

```text
请对 600399 抚顺特钢生成年度财务诊断报告，取消评分层，重点分析现金流、债务安全、资产质量和行业周期。
```

```text
请检查某份报告是否符合 siq_analysis 的强制工作规则。
```

```text
请只分析某公司经营现金流和利润质量，并标注数据库证据链。
```
