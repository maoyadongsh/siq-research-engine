# 多市场财报规则服务

这是 SIQ Research Engine 内部的多市场财报解析后规则服务，用于处理已经下载并解析后的财报产物，生成可入库、可校验、可供后续智能体问答溯源使用的结构化结果。

项目路径：

```text
/home/maoyd/siq-research-engine/services/market-report-rules
```

本服务从外部原型 `/home/maoyd/market-report-rules-service` 迁入主仓库后，仍保持独立 FastAPI 服务边界。后续由解析页、入库页或 API 聚合后端通过 HTTP/JSON 或文件产物合同接入主链路。

## 一、项目定位

本服务不负责下载财报，也不直接负责 PDF/HTML/OCR 解析。它处理的是“解析后的产物”，例如：

- SEC XBRL/iXBRL/companyfacts 结构化事实
- SEC HTML/iXBRL filing 的定位信息
- 港股 PDF 解析后的表格、页面、行列、文本块、表格标题
- 后续可能补充的 HTML-to-PDF 渲染页码

服务目标是把这些解析产物转换为：

- `financial_data`：财务数据结构化结果
- `financial_checks`：财务勾稽、质量校验、经营指标校验结果
- `load_plan`：市场隔离数据库的入库计划
- `evidence_targets`：问答智能体可回溯展示的证据定位

## 二、核心设计原则

### 1. 市场数据物理隔离

不同市场的下载、解析、抽取、校验、入库和智能体消费边界必须隔离。A 股当前注册为 `CN` 市场，下载入口已统一到 `services/market-report-finder`，解析能力仍由 `apps/pdf-parser` legacy 链路承接；港股、美股使用本服务内的市场模块。

当前默认设计：

| 市场 | PostgreSQL 数据库 | Schema | Wiki 命名空间 | 智能体策略 |
|---|---|---|---|---|
| A 股 | `siq` | `pdf2md` | `data/wiki/cn_reports` | 当前使用 A 股 legacy 智能体/解析链路 |
| 美股 | `siq` | `sec_us` | `data/wiki/us_sec` | 后续使用美股专属智能体 |
| 港股 | `siq` | `pdf2md_hk` | `data/wiki/hk_reports` | 后续使用港股专属智能体 |

注意：市场隔离使用同一个 `siq` 数据库下的独立 schema；`pdf2md` 是 A 股现有 legacy schema，HK/US 不写入该 schema。

### 2. 规则按市场、行业、公司逐层叠加

规则不是一张大表硬套所有公司，而是分层组合：

1. 市场规则：CN / HK / US / 后续新增市场
2. 会计准则规则：US GAAP / IFRS / HKFRS / CASBE
3. 行业规则：SaaS、互联网平台、零售、制造、银行、保险、地产、能源等
4. 公司级 override：后续支持公司特殊口径、特殊 KPI 名称、特殊披露模板

这样可以避免把 A 股 PDF、港股地产、港股银行、美股 SaaS、美股制造企业或后续其他市场强行塞进同一套抽取逻辑。

### 市场模块约定

本服务按“一个市场一个模块”组织代码：

```text
src/market_report_rules_service/markets/
  cn/
    definition.py
    adapter.py
    extractor.py
  hk/
    definition.py
    extractor.py
    rules.py
  us/
    definition.py
    extractor.py
    rules.py
```

新增市场时优先新增 `markets/<code>/definition.py`，如果有抽取能力再新增 `extractor.py` 和 `rules.py`。顶层 `registry.py`、`storage.py`、`extraction.py` 只做注册和分发，不写市场业务逻辑。

### 3. 下载和解析可以统一，抽取和校验必须分市场

下载、解析、任务调度可以走统一界面。但抽取规则、校验规则、入库规则和智能体消费层必须按市场隔离。

原因：

- 美股以 SEC XBRL/iXBRL 为主，结构化程度高，但 10-Q 有 QTD/YTD 混合问题。
- 港股以 PDF 表格为主，三大表标题、语言、繁简体、表格结构更复杂。
- 不同市场的会计准则、披露习惯、报告类型和监管语义不同。

Web 工作台建议采用：

- 下载：统一“搜索下载”页面，按 `A股 / 港股 / 美股` 切换市场。
- 解析：单独设置市场标签页或工作流，A 股 PDF、港股 PDF、美股 SEC/iXBRL 分开处理。
- 入库：单独设置市场标签页或任务类型，CN/HK/US 分别执行字段映射、指标规则和质量门禁。

## 三、当前支持范围

### 美股

规则 profile：`us_sec_xbrl_v1`

支持表单：

- `10-K`
- `10-Q`
- `20-F`
- `6-K`

优先解析来源：

- SEC companyfacts
- XBRL facts
- iXBRL HTML
- SEC filing HTML

会计准则：

- `US_GAAP`
- `IFRS`

关键处理点：

- 10-K / 20-F 年报：通常要求三大表完整。
- 10-Q 季报：同一 filing 可能同时存在 QTD 和 YTD，需要保留期间语义。
- 6-K：可能只是公告、业绩快报或中期报表，不保证三大表完整，缺表不应直接硬失败。
- 外国私人发行人可能使用 IFRS，不应强行转为 US GAAP。

### 港股

规则 profile：`hkex_pdf_tables_v1`

支持报告类型：

- 年报
- 中报 / 半年报
- 季报 / Q1 / Q3 / 自愿披露

优先解析来源：

- PDF 解析后的表格
- 表格标题
- 表格行列
- 页面编号
- 表格索引
- Markdown / content list / table index

会计准则：

- `HKFRS`
- `IFRS`
- `CASBE`

关键处理点：

- 年报：三大表缺失是严重问题。
- 中报：可接受简明报表，缺项一般降级为 warning。
- 季报：港股主板通常不强制完整季度报告，缺三大表不应硬失败。
- PDF 表格需要先识别三大表类型，再抽取行项目，避免把附注表或经营分析表误识别为主表。

## 四、三大表识别逻辑

### 美股三大表识别

美股优先通过 XBRL tag 归属三大表：

| 报表 | 识别方式 |
|---|---|
| 资产负债表 | `us-gaap:Assets`、`us-gaap:Liabilities`、`us-gaap:StockholdersEquity` 等 |
| 利润表 | `us-gaap:Revenues`、`us-gaap:NetIncomeLoss`、`us-gaap:OperatingIncomeLoss` 等 |
| 现金流量表 | `us-gaap:NetCashProvidedByUsedInOperatingActivities` 等 |

IFRS issuer 使用 `ifrs-full:*` taxonomy，例如：

- `ifrs-full:Revenue`
- `ifrs-full:Assets`
- `ifrs-full:CashFlowsFromUsedInOperatingActivities`

### 港股三大表识别

港股优先通过表格标题和行项目上下文识别：

| 报表 | 常见英文标题 | 常见中文标题 |
|---|---|---|
| 资产负债表 | `Consolidated Statement of Financial Position`、`Balance Sheet` | 资产负债表、財務狀況表、综合财务状况表 |
| 利润表 | `Statement of Profit or Loss`、`Income Statement`、`Statement of Comprehensive Income` | 利润表、損益表、综合收益表、全面收益表 |
| 现金流量表 | `Statement of Cash Flows`、`Cash Flow Statement` | 现金流量表、綜合現金流量表 |

如果标题不清晰，会结合行项目判断，例如：

- `Total assets`、`Total liabilities`、`Total equity`
- `Revenue`、`Gross profit`、`Profit before tax`
- `Net cash generated from operating activities`

## 五、财务指标抽取规则

当前财务指标分为：

- 资产负债表项目
- 利润表项目
- 现金流量表项目
- 关键财务指标

示例 canonical 指标：

| canonical_name | 含义 | 报表 |
|---|---|---|
| `operating_revenue` | 收入 / 营业收入 / Revenue | 利润表 |
| `cost_of_sales` | 销售成本 / 收入成本 | 利润表 |
| `gross_profit` | 毛利 | 利润表 |
| `operating_profit` | 经营利润 | 利润表 |
| `total_profit` | 税前利润 / 利润总额 | 利润表 |
| `income_tax_expense` | 所得税费用 | 利润表 |
| `net_profit` | 净利润 | 利润表 |
| `parent_net_profit` | 归母净利润 / owners attributable profit | 利润表 |
| `nci_profit` | 少数股东损益 / 非控股权益损益 | 利润表 |
| `total_assets` | 总资产 | 资产负债表 |
| `total_liabilities` | 总负债 | 资产负债表 |
| `total_equity` | 总权益 | 资产负债表 |
| `parent_equity` | 归母权益 | 资产负债表 |
| `nci_equity` | 非控股权益 | 资产负债表 |
| `cash_and_cash_equivalents` | 现金及现金等价物 | 资产负债表 |
| `operating_cash_flow_net` | 经营活动现金流量净额 | 现金流量表 |
| `investing_cash_flow_net` | 投资活动现金流量净额 | 现金流量表 |
| `financing_cash_flow_net` | 融资/筹资活动现金流量净额 | 现金流量表 |
| `cash_equivalents_net_increase` | 现金及现金等价物净增加额 | 现金流量表 |
| `basic_eps` | 基本每股收益 | 关键指标 |
| `diluted_eps` | 稀释每股收益 | 关键指标 |

## 六、经营指标抽取规则

经营指标不混入三大表，单独进入 `operating_metrics`。

当前支持的行业 profile 包括：

- `general`
- `saas`
- `internet_platform`
- `retail`
- `manufacturing`
- `bank`
- `insurance`
- `real_estate`
- `energy`

示例经营指标：

| 行业 | 指标 |
|---|---|
| 通用 | 活跃客户、员工人数 |
| SaaS | ARR、付费客户、净收入留存率 |
| 互联网平台 | MAU、DAU、GMV |
| 零售 | 门店数、同店销售增长 |
| 制造 | 产量、出货量 |
| 银行 | 贷款余额、存款、净息差、不良贷款率 |
| 保险 | 总保费、综合成本率 |
| 地产 | 合约销售、建筑面积 |
| 能源 | 探明储量、日产量 |

经营指标都必须带证据，不允许无来源地进入事实层。

## 七、校验规则

当前实现的基础校验包括：

### 财务硬勾稽

- `总资产 = 总负债 + 总权益`
- `总资产 = 流动资产 + 非流动资产`
- `总负债 = 流动负债 + 非流动负债`
- `总权益 = 归母权益 + 非控股权益`
- `毛利 = 收入 - 销售成本`
- `净利润 = 税前利润 - 所得税`
- `净利润 = 归母净利润 + 非控股权益损益`
- `现金净增加额 = 经营现金流 + 投资现金流 + 融资现金流 + 汇率影响`
- `期末现金 = 期初现金 + 现金净增加额`

### 跨表软校验

- 资产负债表现金与现金流量表期末现金接近即可。
- 如果差异可能来自 restricted cash、time deposits、现金定义差异，则标记 warning，而不是直接 fail。

### 经营指标校验

- 指标是否非负
- 比率是否处于合理范围
- `DAU <= MAU`
- 付费客户不应大于活跃客户
- 后续可继续扩展 GMV take rate、银行 NPL、地产净负债率、保险 combined ratio 等行业规则

### 报告类型差异

年报、中报、季报的缺项严重程度不同：

| 报告类型 | 缺三大表处理 |
|---|---|
| 年报 / 10-K / 20-F | 更严格，核心三表缺失可 fail |
| 中报 / 半年报 | 通常 warning |
| 季报 / 10-Q / 6-K / 自愿公告 | 多数情况下 skipped 或 warning |

## 八、美股非 PDF 证据溯源

美股 SEC filing 很多不是 PDF，而是 HTML/iXBRL。

本服务不强行把 HTML 伪造成 PDF 页码，而是使用 SEC 原始证据定位：

- `source_url`
- `accession_number`
- `section`
- `xbrl_tag`
- `anchor`
- `xpath`
- `html_snippet`

如果后续额外生成 HTML-to-PDF 渲染产物，可以再补：

- `rendered_page_number`

但原始 SEC URL、XBRL tag 和 accession number 仍然是权威证据。

这样未来问答智能体回答问题时，可以展示：

- 港股：PDF 第几页、第几个表、哪一行哪一列
- 美股：SEC Filing、Item 8、XBRL tag、原文锚点或 XPath

## 九、美股 10-Q 的期间语义

美股 10-Q 中，同一个 XBRL tag 可能同时披露：

- QTD：本季度数
- YTD：年初至本季度累计数

因此本服务不会只保留一个 `period_key`，还会保留：

- `period_start`
- `period_end`
- `duration_days`
- `frame`
- `qtd_ytd_type`

这能避免把季度收入和累计收入混为一谈。

## 十、API 接口

启动服务：

```bash
cd /home/maoyd/siq-research-engine/services/market-report-rules
uv run uvicorn market_report_rules_service.app:app --host 0.0.0.0 --port 8020
```

在 SIQ 一键编排中，本服务作为可选服务运行在 `18020`：

```bash
cd /home/maoyd/siq-research-engine
SIQ_START_MARKET_REPORT_RULES=1 ./start_all.sh
```

当前接口：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/healthz` | 健康检查，返回服务版本、规则 profile、存储 profile |
| GET | `/profiles` | 查看规则 profile、存储 profile、行业 profile |
| GET | `/rules` | 查看规则数量和经营指标规则 |
| POST | `/extract` | 解析产物转 `financial_data` |
| POST | `/validate` | `financial_data` 转 `financial_checks` |
| POST | `/process` | 一次性生成 `financial_data`、`financial_checks`、`load_plan` |
| POST | `/load-plan` | 为抽取结果生成入库计划 |

## 十一、入库计划

本服务当前只生成 `load_plan`，不直接写数据库。

这样做有几个好处：

- 防止误写 A 股库
- 便于人工审查规则结果
- 便于后续接入独立 writer
- 便于不同市场采用不同数据库连接配置

DDL 文件：

```bash
sql/001_market_rules_staging.sql
```

应在本项目 `siq` 数据库中针对市场 schema 执行：

```bash
psql siq -f sql/001_market_rules_staging.sql
```

## 十二、与 A 股逻辑的关系

本服务参考 A 股当前成熟的产物形状，但不复用 A 股实现。A 股当前的同类能力尚未独立为 `rules-service`，主要分布在：

| 路径 | 职责 |
|---|---|
| `apps/pdf-parser/financial_extractor.py` | A 股财务表识别、指标抽取、勾稽校验，生成 `financial_data.json` 和 `financial_checks.json` |
| `apps/pdf-parser/app.py` | 解析任务编排、质量报告、溯源、`document_full.json` 聚合 |
| `apps/pdf-parser/quality_report.py` | A 股核心章节、核心表、质量报告常量 |
| `db/imports/import_document_full_to_postgres.py` | 将 A 股 `document_full.json` 写入 PostgreSQL |
| `db/ddl` / `db/dml` | A 股当前 PostgreSQL `pdf2md` schema 和衍生指标 SQL |

保留相似形状：

- `financial_data.statements`
- `financial_data.key_metrics`
- `financial_checks.checks`
- `pass / fail / warning / skipped`
- `evidence`

不复用内容：

- 不 import A 股 `financial_extractor.py`
- 不复用 A 股分析智能体
- 不写 A 股 PostgreSQL 库
- 不写 A 股 Wiki
- 不强行使用 A 股会计准则项目名

未来接入 SIQ 主系统时，推荐通过 HTTP/JSON 合同集成：

1. 主系统完成下载和解析
2. 调用本服务 `/process`
3. 获取 `financial_data`、`financial_checks`、`load_plan`
4. 由市场专属 writer 写入对应市场数据库
5. 市场专属智能体读取对应市场数据库和 Wiki

## 十三、测试

运行测试：

```bash
cd /home/maoyd/market-report-rules-service
uv run --extra dev pytest -q
```

当前测试覆盖：

- 日期、金额、币种、单位倍率
- 美股 SEC companyfacts 抽取
- 美股 10-Q QTD/YTD 区分
- 港股 PDF 三大表识别
- 港股多期间列抽取
- 港股经营指标与财务三大表分离
- API 健康检查和 `/process` 合同
- 入库计划市场隔离

当前验证结果：

```text
10 passed
```

## 十四、当前文件结构

```text
market-report-rules-service/
  README.md
  pyproject.toml
  sql/
    001_market_rules_staging.sql
  src/market_report_rules_service/
    app.py
    contracts.py
    extraction.py
    industry_profiles.py
    load_plan.py
    markets/
      README.md
      base.py
      common.py
      cn/
        adapter.py
        definition.py
        extractor.py
      hk/
        definition.py
        extractor.py
        rules.py
      us/
        definition.py
        extractor.py
        rules.py
    models.py
    normalization.py
    operating_metrics.py
    pipeline.py
    provenance.py
    registry.py
    rules.py
    statement_detection.py
    storage.py
    validation.py
  tests/
    test_api.py
    test_hk_rules.py
    test_normalization.py
    test_us_rules.py
```

## 十五、后续建议

下一阶段建议继续做：

1. 增加真实 SEC filing / HKEX PDF 的 fixture 样本。
2. 增加公司级 override 规则文件，例如腾讯、阿里、苹果、微软、银行、保险、地产样本。
3. 建立 market-specific writer，把 `load_plan` 写入各市场独立数据库。
4. 增加 HTML/iXBRL 原文锚点抽取和 HTML-to-PDF 渲染页码增强。
5. 为 CN/HK/US 分别保留专属问答智能体或 adapter，智能体只读取对应市场数据库和 Wiki。
6. 扩展欧洲、日韩市场时，沿用同样模式：独立 market module、独立 profile、独立数据库、独立 Wiki、独立智能体。
