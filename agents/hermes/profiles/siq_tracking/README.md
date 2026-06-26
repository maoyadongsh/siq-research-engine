# siq_tracking README

`siq_tracking` 是 SIQ / Hermes 体系中的 A股二级市场持续跟踪与预警智能体。它负责承接上游 `siq_analysis` 的研究报告、`siq_factchecker` 的核查结果以及公司财务指标数据，把一次性分析转化为可持续跟踪的事项清单、指标面板、预警记录、更新日志和综合 HTML 跟踪报告。

本 README 按当前已经落地并验证通过的功能编写，重点说明“现在能做什么、如何运行、产物在哪里、规则是什么、哪些能力仍是边界”。

## 0. 当前检查结论

检查时间：2026-05-29。`http://127.0.0.1:8650/health` 返回正常。当前 profile 的 `state.db` 中有 79 个 sessions、1425 条 messages。实际生产级规则脚本位于 `/home/maoyd/wiki/tracking/scripts`，profile 负责身份、规则和对话入口。

当前主 Wiki 中已有多家公司生成了 `tracking/*.html`，前端 `/tracking` 页面读取这些 HTML，并通过聚合后端 `/api/tracking/chat/*` 连接本 Agent。

### 决赛关注点

| 维度 | 本 Agent 贡献 |
| --- | --- |
| 创新性 | 把一次性财报分析延伸为持续跟踪系统：事项、指标、舆情、预警、更新记录和综合报告 |
| 技术难度 | 需要把分析报告、核查结果、metrics、公告/舆情和证据引用转换成可持续规则与预警 |
| 完成度 | 已有 `run_all.py`、六个模块、HTML 综合报告、前端展示和 Hermes 聊天入口 |
| 商业价值 | 适合投后/二级市场跟踪，帮助研究员把“看过一次报告”变成“持续监控变化” |

### 评委技术说明

`siq_tracking` 的技术定位是把一次性的年度分析转成可持续观察系统。它不重写分析报告，而是从上游报告和财务指标中抽取“后续要盯什么、触发什么算异常、异常后怎么更新结论”。

| 模块 | 输入 | 输出 |
| --- | --- | --- |
| 技术架构 | Hermes profile + `/home/maoyd/wiki/tracking/scripts` 规则流水线 + Wiki HTML 展示 | 把报告后续观察从对话变成持久化产物 |
| 技术栈 | Python、Markdown/JSON、Wiki 文件系统、Hermes Runs、前端 ReportViewer | 适合本地私有化持续跟踪 |
| 数据流 | 分析/核查报告 -> 跟踪事项 -> 指标面板 -> 预警触发 -> 更新记录 -> 综合 HTML 报告 | 把一次性分析转成持续监控 |
| 跟踪事项提取 | 分析报告、核查结果、风险段落、metrics | `tracking-items.md`、事项清单、验证方式 |
| 指标面板 | `metrics/key_metrics.json`、三表指标、历史值 | 指标快照、阈值、异常原因 |
| 舆情/公告观察 | 外部更新文本、报告变动线索、人工输入 | 更新记录、情绪摘要、待核查事项 |
| 预警触发 | 指标阈值、风险关键词、事项状态 | `alerts/*.md`、`CRITICAL/HIGH/MEDIUM/LOW` |
| 报告更新 | 新证据、预警、事项进度 | 综合跟踪 HTML、更新日志 |
| 前端交互 | `/tracking` 页面和 `/api/tracking/chat/*` | 右侧跟踪助手、已有报告展示 |

跟踪算法采用“规则阈值 + 事项状态 + 证据引用”的组合：财务异常不能只靠模型语感判断，必须落到指标变化、阈值、原始证据和复核动作。商业价值在于把研究员的一次性阅读沉淀为持续监控资产，适用于投后管理、二级市场覆盖和财报后续验证。

## 1. 角色定位

`siq_tracking` 不负责重新撰写完整深度分析报告，也不负责直接给出交易指令。它的核心职责是持续回答以下问题：

1. 上游分析报告中哪些假设、风险、承诺、异常和待核查事项需要持续跟踪？
2. 最新财务指标、公告/舆情和事项进度是否触发预警？
3. 哪些预警或变化需要反馈到分析报告中，形成更新记录？
4. 哪些指标异常可能来自数据单位、口径或抽取问题，应先复核而不是直接解释为经营风险？

输出原则：

- 不使用评分层，不给公司打总分。
- 不直接输出买入、卖出、减仓、止损等交易动作。
- 用跟踪事项、预警等级、证据来源、验证方式和后续动作表达研究结论。
- 优先保证财务口径、单位、路径和证据链一致。

## 2. 当前运行环境

Hermes profile 目录：

```text
/home/maoyd/.hermes/profiles/siq_tracking
```

生产级跟踪脚本目录：

```text
/home/maoyd/wiki/tracking/scripts
```

公司数据根目录：

```text
/home/maoyd/wiki/companies
```

Hermes API Server 端口：

```text
8650
```

默认模型配置：

```text
provider: kimi-coding
model: kimi-for-coding
```

注意：`.env` 中可能包含 API Key，不要把密钥写入 README、报告、日志或对话输出。

## 3. 重要实现边界

当前可一键运行、已验证的执行链位于：

```text
/home/maoyd/wiki/tracking/scripts/run_all.py
```

profile 目录中的 `agent.py`、`models.py`、`schemas.py`、`modules/` 更接近早期原型或包装层。除非明确要调试 profile 内模块，否则运行和维护应以 `/home/maoyd/wiki/tracking/scripts` 为准。

## 4. 标准输入

### 4.1 必需输入

每家公司需要存在标准目录：

```text
/home/maoyd/wiki/companies/<stock_code>-<company_name>/
```

至少需要上游分析报告：

```text
analysis/*.md
```

其中 `<stock_code>` 为 6 位股票代码，例如 `600399`；`<company_name>` 为公司简称，例如 `抚顺特钢`。

### 4.2 推荐输入

财务关键指标文件：

```text
metrics/key_metrics.json
```

该文件用于模块1提取异常指标、模块3生成指标面板、模块4触发指标预警。

典型字段包括：

```json
{
  "name": "营业收入",
  "canonical_name": "operating_revenue",
  "unit": "元",
  "scale": 1.0,
  "values": {
    "2025": 7783430662.66,
    "2024": 8483918806.35
  },
  "raw_values": {
    "2025": "7,783,430,662.66",
    "2024": "8,483,918,806.35"
  },
  "sources": {
    "2025": {
      "table_index": 9,
      "line": 109
    }
  }
}
```

### 4.3 当前可识别的核心 canonical_name

```text
operating_revenue
net_profit / parent_net_profit
gross_profit_margin
net_profit_margin
roe
roa
debt_ratio
current_ratio
quick_ratio
inventory_turnover
receivable_turnover
cash_flow_operating / operating_cash_flow_net
eps / basic_eps
```

## 5. 标准输出目录

所有跟踪产物都写入公司目录下的 `tracking/`：

```text
/home/maoyd/wiki/companies/<stock_code>-<company_name>/tracking/
```

标准结构：

```text
tracking/
  tracking-items.md
  sentiment/
    <date>.md
  metrics/
    <period>.md
  alerts/
    <date>-<level>-<seq>.md
  updates/
    <date>-update.md
    archive/
  <stock_code>-<company_name>-跟踪报告-<date>.html
```

不要把公司跟踪产物写到 Hermes profile 目录，也不要写到脚本目录。

## 6. 一键运行

推荐入口：

```bash
python3 /home/maoyd/wiki/tracking/scripts/run_all.py --stock 600399 --company 抚顺特钢 --wiki-base /home/maoyd/wiki
```

如果暂不需要舆情模块，或当前不希望使用模拟舆情数据：

```bash
python3 /home/maoyd/wiki/tracking/scripts/run_all.py --stock 600399 --company 抚顺特钢 --wiki-base /home/maoyd/wiki --skip-sentiment
```

初始化公司跟踪目录：

```bash
python3 /home/maoyd/wiki/tracking/scripts/run_all.py --setup --stock 600399 --company 抚顺特钢 --wiki-base /home/maoyd/wiki
```

验证所有公司规则合规性：

```bash
python3 /home/maoyd/wiki/tracking/scripts/run_all.py --validate-all --wiki-base /home/maoyd/wiki
```

## 7. 主控执行流程

`run_all.py` 会按以下顺序运行：

1. 前置检查：确认公司目录、上游分析报告等条件满足。
2. 目录初始化：确保 `tracking/`、`sentiment/`、`metrics/`、`alerts/`、`updates/` 存在。
3. 单报告原则检查：清理或约束分散 HTML，确保最终只产出综合 HTML。
4. 模块1：生成跟踪事项清单。
5. 模块2：生成舆情日报，除非传入 `--skip-sentiment`。
6. 模块3：生成指标追踪面板。
7. 模块4：生成预警报告。
8. 模块5：生成更新记录并追加到分析报告。
9. 模块6：生成综合 HTML 跟踪报告。
10. 最终规则检查：确认综合 HTML 是否生成。

## 8. 六个模块说明

### 8.1 模块1：跟踪事项提取器

脚本：

```text
/home/maoyd/wiki/tracking/scripts/module1_item_extractor.py
```

输入：

```text
analysis/*.md
metrics/key_metrics.json
```

输出：

```text
tracking/tracking-items.md
```

功能：

- 从分析报告中提取需要持续验证的风险、承诺、异常、关联交易、会计变更、监管动态、重大事项和行业变化。
- 从 `key_metrics.json` 中识别异常指标。
- 为每个事项生成 `id`、分类、描述、来源、到期日、阈值、验证方式、状态和优先级。
- 附带 YAML 结构化数据，供模块4解析。

跟踪事项分类：

```text
财务承诺
风险信号
异常指标
关联交易
会计变更
监管动态
重大事项
行业变化
```

数据质量保护：

- 对总资产、总负债、归母净资产等规模类资产负债表指标，如果同比变化超过 90% 且两期量级差异超过一个数量级，默认标记为“疑似单位/口径/抽取异常”。
- 这类事项优先级默认为 `medium`，验证方式要求核对 `key_metrics.json`、三大报表结构化数据和 PDF 原文。
- 不应直接把疑似数据质量问题升级为经营风险。

### 8.2 模块2：舆情监控器

脚本：

```text
/home/maoyd/wiki/tracking/scripts/module2_sentiment_monitor.py
```

输出：

```text
tracking/sentiment/<date>.md
```

功能：

- 生成公司舆情日报。
- 将舆情分为正面、负面、中性。
- 为模块4提供负面舆情数量和重大负面关键词判断。

当前边界：

- 当前舆情模块仍可能使用模拟数据。
- 如果使用模拟数据，报告和对话中必须说明，不能把模拟舆情作为真实事实依据。
- 在正式投研流程中，建议接入公告、交易所互动、主流财经媒体、研报和社区数据源。

### 8.3 模块3：指标追踪器

脚本：

```text
/home/maoyd/wiki/tracking/scripts/module3_metrics_tracker.py
```

输入：

```text
metrics/key_metrics.json
```

输出：

```text
tracking/metrics/<period>.md
```

功能：

- 生成关键指标概览表。
- 计算同比变化、趋势方向和 CAGR。
- 输出历史数据表、趋势解读和 JSON 原始附录。
- 识别 `parent_net_profit`、`operating_cash_flow_net`、`basic_eps` 等常见别名。

金额展示规则：

- 来源单位为 `元`：展示为 `亿元`。
- 来源单位为 `万元`：展示为 `亿元`。
- 来源单位为 `百万元`：展示为 `亿元`。
- 来源单位为 `亿元`：保持 `亿元`。
- 比率和周转类指标保持 `%`、`倍`、`次`、`元` 等原单位。

CAGR 规则：

- 只有首末值均为正数且跨期有效时才计算 CAGR。
- 如果指标跨正负号或包含零值，CAGR 显示为 `N/A`，避免生成复数或误导性结论。

趋势规则：

- 对收入、利润、ROE、现金流等“越高越好”的指标，正增长为正向，明显负增长为负向。
- 对资产负债率等“越低越好”的指标，下降为正向，上升为负向。

### 8.4 模块4：预警触发器

脚本：

```text
/home/maoyd/wiki/tracking/scripts/module4_alert_trigger.py
```

输入：

```text
tracking/tracking-items.md
tracking/sentiment/*.md
tracking/metrics/*.md
```

输出：

```text
tracking/alerts/<date>-<level>-<seq>.md
```

预警等级：

| 等级 | 含义 | 典型动作 |
|------|------|----------|
| `INFO` | 信息提示 | 记录存档，定期复查 |
| `WATCH` | 关注信号 | 纳入日常监控，设置复查提醒 |
| `WARNING` | 明确预警 | 深入分析原因，跟进公告和舆情 |
| `CRITICAL` | 严重预警 | 召开专项复核，更新风险模型和跟踪阈值 |

当前内置预警规则：

1. 归母净利润同比下滑超过 30%：`WARNING`。
2. 归母净利润连续两期同比下滑超过 10%：`CRITICAL`。
3. 毛利率同比下降超过 5 个百分点：`WATCH`。
4. 资产负债率同比上升超过 10 个百分点：`WATCH`。
5. 经营现金流同比下滑超过 50%：`WARNING`。
6. 单日负面舆情达到 3 条及以上：`WATCH`。
7. 出现监管、处罚、立案等重大负面舆情：`WARNING`。
8. 跟踪事项 7 天内到期：`INFO`。
9. 跟踪事项超期：`WARNING`。
10. ROE 低于 5%：`WATCH`。

预警报告必须包含：

- 预警级别。
- 触发规则。
- 触发时间。
- 指标或事项详情。
- 建议措施。
- 原始 JSON 附录。

措辞边界：

- 可以提示“复核投资假设、风险暴露和组合影响”。
- 不直接给出“买入、卖出、减仓、止损”等交易动作。

### 8.5 模块5：报告更新器

脚本：

```text
/home/maoyd/wiki/tracking/scripts/module5_report_updater.py
```

输出：

```text
tracking/updates/<date>-update.md
```

功能：

- 汇总最新跟踪事项、舆情、指标和预警状态。
- 生成独立更新记录。
- 将“跟踪更新”章节追加到 `analysis/*.md`。
- 如果原分析报告已有跟踪更新，会先归档旧版本到 `tracking/updates/archive/`。

链接规则：

更新记录位于 `tracking/updates/`，相对链接应从该目录出发：

```text
../tracking-items.md
../sentiment/<file>
../metrics/<file>
../alerts/<file>
```

### 8.6 模块6：综合 HTML 报告生成器

脚本：

```text
/home/maoyd/wiki/tracking/scripts/module6_html_reporter.py
```

输出：

```text
tracking/<stock_code>-<company_name>-跟踪报告-<date>.html
```

功能：

- 汇总跟踪事项、舆情日报、指标面板、预警报告和更新记录。
- 生成单一综合 HTML 页面。
- 提供统计卡片和可折叠内容区块。
- 遵守单报告原则，不为每个模块单独生成 HTML。

## 9. 规则引擎

规则文件：

```text
/home/maoyd/wiki/tracking/scripts/siq_tracking_rules.py
```

规则内容：

1. 工作目录固定为 `companies/<stock>-<name>/tracking/`。
2. 脚本固定在 `wiki/tracking/scripts/`。
3. 综合报告命名为 `<stock>-<name>-跟踪报告-<date>.html`。
4. 只生成综合 HTML，禁止分模块 HTML。
5. 只跟踪已完成 `siq_analysis` 的公司。
6. `tracking/` 下必须包含 `sentiment/`、`metrics/`、`alerts/`、`updates/`。

验证命令：

```bash
python3 /home/maoyd/wiki/tracking/scripts/run_all.py --validate-all --wiki-base /home/maoyd/wiki
```

## 10. 典型执行结果

以 `600399-抚顺特钢` 为例，执行：

```bash
python3 /home/maoyd/wiki/tracking/scripts/run_all.py --stock 600399 --company 抚顺特钢 --wiki-base /home/maoyd/wiki --skip-sentiment
```

当前验证结果：

```text
module1: success -> tracking-items.md
module2: skipped -> --skip-sentiment
module3: success -> metrics/2026-Q1.md
module4: success -> alerts/2026-05-16-critical-002.md
module5: success -> updates/2026-05-16-update.md
module6: success -> 600399-抚顺特钢-跟踪报告-2026-05-16.html
```

全量规则验证结果：

```text
总计: 2 家公司
通过: 2
失败: 0
```

## 11. 当前已验证的关键修复

1. 金额指标不会再把“元”误展示为“百万元”，会按规则转换为“亿元”。
2. `parent_net_profit`、`operating_cash_flow_net`、`basic_eps` 等别名已纳入指标追踪和预警。
3. 跨正负号指标不再强行计算 CAGR，统一显示 `N/A`。
4. 疑似单位、口径、抽取异常的资产负债表规模指标会被标为数据质量事项。
5. 模块4、模块6 的路径初始化问题已修复。
6. 经营现金流预警规则已实际接入评估逻辑。
7. “净利润连续下滑”规则已改为真正检查连续两期。
8. 预警建议已去除直接交易动作表述。

## 12. 已知限制

1. 舆情模块当前可能仍使用模拟数据，正式使用前应接入真实数据源。
2. PostgreSQL 财务宽表尚未直接接入 tracking 主流程，目前主要依赖 `metrics/key_metrics.json`。
3. 证据链目前主要通过文件、指标、表格索引和行号表达，尚未完整打通数据库级 claim-evidence 链接。
4. 跟踪事项生命周期较基础，当前以 `open` 为主，后续应扩展 `watching`、`resolved`、`invalid`。
5. profile 内原型模块与 wiki tracking 脚本尚未完全收敛，维护时应以 wiki tracking 脚本为准。
6. 模块5 会更新 `analysis/*.md` 并归档旧版本，重复运行会生成归档文件，应在批量任务中注意版本管理。

## 13. 排障指南

### 13.1 智能体未显示在 Hermes Web UI

检查：

```text
/home/maoyd/.hermes/profiles/siq_tracking
```

确认：

- profile 目录存在。
- `config.yaml` 中 API Server 端口为 `8650`。
- Hermes gateway 进程正在运行。
- 模型配置使用 `kimi-for-coding` / `kimi-coding`。

### 13.2 API 认证错误

如果出现类似认证方式无法解析的错误，应检查：

- Hermes profile `.env` 是否配置模型 API Key。
- API Server 是否需要 `API_SERVER_KEY`。
- 调用方是否正确传递认证头。

不要在日志、README 或报告中明文输出真实密钥。

### 13.3 指标单位异常

检查：

```text
metrics/key_metrics.json
```

重点看：

- `unit` 是否为 `元`、`万元`、`百万元`、`亿元`。
- `raw_values` 是否存在异常逗号或截断。
- 三大财务报表结构化数据和 PDF 原文是否一致。

处理原则：

- 先标记数据质量事项。
- 先复核单位和口径。
- 不直接解释为经营风险。

### 13.4 没有生成预警报告

可能原因：

- 指标面板为空或没有可识别 canonical_name。
- 舆情数据为空且无负面触发。
- 跟踪事项未到期或未超期。
- 所有规则均未触发。

检查文件：

```text
tracking/metrics/*.md
tracking/tracking-items.md
tracking/sentiment/*.md
```

### 13.5 HTML 报告未生成

检查：

```bash
python3 /home/maoyd/wiki/tracking/scripts/run_all.py --validate-all --wiki-base /home/maoyd/wiki
```

常见原因：

- 公司目录命名不符合 `<stock>-<company>`。
- `tracking/` 子目录缺失。
- 上游分析报告不存在。

## 14. 维护建议

优先级较高的后续改进：

1. 接入真实公告和舆情数据源，替换模拟舆情。
2. 将 PostgreSQL 财务宽表纳入 `key_metrics.json` 校验和报告证据链。
3. 为每条预警补充可点击证据链，连接到 PDF 页码、表格、行号或数据库记录。
4. 将 profile 原型模块与 wiki tracking 生产脚本合并，减少重复实现。
5. 增加事项生命周期管理和重复事项去重。
6. 增加批量运行日志和失败重试机制。

## 15. 最小可用命令清单

语法检查：

```bash
python3 -m py_compile /home/maoyd/wiki/tracking/scripts/module1_item_extractor.py /home/maoyd/wiki/tracking/scripts/module3_metrics_tracker.py /home/maoyd/wiki/tracking/scripts/module4_alert_trigger.py /home/maoyd/wiki/tracking/scripts/module5_report_updater.py /home/maoyd/wiki/tracking/scripts/module6_html_reporter.py
```

单家公司完整运行：

```bash
python3 /home/maoyd/wiki/tracking/scripts/run_all.py --stock 600399 --company 抚顺特钢 --wiki-base /home/maoyd/wiki --skip-sentiment
```

全量规则验证：

```bash
python3 /home/maoyd/wiki/tracking/scripts/run_all.py --validate-all --wiki-base /home/maoyd/wiki
```
