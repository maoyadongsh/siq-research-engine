# 多市场财报规则服务

这是 SIQ Research Engine 的多市场解析后规则服务，用于处理已经下载并解析后的财报产物，生成可入库、可校验、可供后续智能体问答溯源使用的结构化结果。

项目路径：

```text
/home/maoyd/siq-research-engine/services/market-report-rules
```

本服务不负责下载财报，也不直接负责 PDF/HTML/OCR 解析。它处理的是“解析后的产物”，例如：

- SEC XBRL/iXBRL/companyfacts 结构化事实
- SEC HTML/iXBRL filing 的定位信息
- 港股 PDF 解析后的表格、页面、行列、文本块、表格标题
- 欧股 PDF/ESEF 解析后的 evidence package
- 日股 EDINET 和韩股 DART 解析后的结构化证据

服务目标是把这些解析产物转换为：

- `financial_data`：财务数据结构化结果
- `financial_checks`：财务勾稽、质量校验、经营指标校验结果
- `load_plan`：市场隔离数据库的入库计划
- `evidence_targets`：问答智能体可回溯展示的证据定位

## 核心设计原则

### 1. 市场数据物理隔离

不同市场的下载、解析、抽取、校验、入库和智能体消费边界必须隔离。当前默认设计：

| 市场 | PostgreSQL 数据库 | Schema | Wiki 命名空间 |
| --- | --- | --- | --- |
| A 股 | `siq` | `pdf2md` | `data/wiki/cn_reports` |
| 美股 | `siq` | `sec_us` | `data/wiki/us_sec` |
| 港股 | `siq` | `pdf2md_hk` | `data/wiki/hk_reports` |
| 日股 | `siq` | `edinet_jp` | `data/wiki/jp_reports` |
| 韩股 | `siq` | `dart_kr` | `data/wiki/kr_reports` |
| 欧股 | `siq` | `eu_ifrs` | `data/wiki/eu_reports` |

注意：市场隔离使用同一个 `siq` 数据库下的独立 schema。

### 2. 规则按市场、行业、公司逐层叠加

规则不是一张大表硬套所有公司，而是分层组合：

1. 市场规则：CN / HK / US / EU / JP / KR
2. 会计准则规则：US GAAP / IFRS / HKFRS / CASBE / K-IFRS / J-GAAP 相关映射
3. 行业规则：SaaS、互联网平台、零售、制造、银行、保险、地产、能源等
4. 公司级 override：特殊口径、特殊 KPI 名称、特殊披露模板

### 3. 下载和解析可以统一，抽取和校验必须分市场

原因很简单：

- 美股以 SEC XBRL/iXBRL 为主，QTD/YTD 期间语义复杂。
- 港股以 PDF 表格为主，表格标题、繁简体和跨页结构复杂。
- 日股和韩股更依赖 XML/zip、表单和本地披露格式。
- 欧股 ESEF 又引入 XBRL 与 PDF 并存的混合证据形态。

## 当前支持范围

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

### 港股

规则 profile：`hkex_pdf_tables_v1`

支持报告类型：

- 年报
- 中报 / 半年报
- 季报 / Q1 / Q3 / 自愿披露

优先解析来源：

- PDF 解析后的表格
- 表格标题
- 页面编号
- 表格索引
- Markdown / content list / source map

### 日股

规则 profile：`edinet_jp_pdf_xbrl_v1`

支持报告类型：

- 有価証券報告書
- 半期報告書
- 四半期報告書

优先解析来源：

- EDINET XML / PDF
- XBRL facts
- 文档列表和原始披露锚点

### 韩股

规则 profile：`dart_kr_pdf_xml_v1`

支持报告类型：

- 사업보고서
- 반기보고서
- 분기보고서

优先解析来源：

- DART XML / ZIP
- PDF / HTML
- 公告目录和原始锚点

### 欧股

规则 profile：`eu_ifrs_pdf_esef_v1`

支持报告类型：

- 年报
- 中报
- ESEF / PDF 混合披露

优先解析来源：

- PDF 解析后的表格
- ESEF XBRL
- 质量报告和 evidence map

## 三大表识别逻辑

### 美股

美股优先通过 XBRL tag 归属三大表。

### 港股

港股优先通过表格标题和行项目上下文识别。

### 日股 / 韩股 / 欧股

这几个市场常见的是 PDF + XML zip + 原始披露并存，识别逻辑以 market module 内的 definition / extractor / rules 为准。

## 财务指标抽取规则

当前财务指标分为：

- 资产负债表项目
- 利润表项目
- 现金流量表项目
- 关键财务指标

经营指标不混入三大表，单独进入 `operating_metrics`。指标都必须带证据，不允许无来源进入事实层。

## 校验规则

当前实现的基础校验包括：

- 财务硬勾稽
- 跨表软校验
- 经营指标校验
- 报告类型差异处理

## 输出边界

- 不输出评分、星级、目标价或交易建议。
- 不把抽取结果伪装成正式数据库事实。
- 不将缺项直接解释成业务恶化，必须先说明证据缺口。
- 不把模型生成的字段当作事实库直接写入下游 schema。

## API 接口

启动服务：

```bash
cd /home/maoyd/siq-research-engine/services/market-report-rules
uv sync
uv run python -m uvicorn market_report_rules_service.app:app --host 0.0.0.0 --port 18020
```

在 SIQ 一键编排中，本服务作为可选服务运行在 `18020`。

当前接口：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/healthz` | 健康检查，返回服务版本、规则 profile、存储 profile |
| GET | `/profiles` | 查看规则 profile、存储 profile、行业 profile |
| GET | `/markets` | 查看市场模块列表 |
| GET | `/rules` | 查看规则数量和经营指标规则 |
| POST | `/extract` | 解析产物转 `financial_data` |
| POST | `/validate` | `financial_data` 转 `financial_checks` |
| POST | `/process` | 一次性生成 `financial_data`、`financial_checks`、`load_plan` |
| POST | `/load-plan` | 为抽取结果生成入库计划 |

## 入库计划

本服务当前只生成 `load_plan`，不直接写数据库。

这样做有几个好处：

- 防止误写错误市场的库。
- 便于人工审查规则结果。
- 便于后续接入独立 writer。
- 便于不同市场采用不同数据库连接配置。

## 与 A 股逻辑的关系

本服务参考 A 股当前成熟的产物形状，但不复用 A 股实现。A 股当前的同类能力主要分布在：

| 路径 | 职责 |
| --- | --- |
| `apps/pdf-parser/financial_extractor.py` | A 股财务表识别、指标抽取、勾稽校验 |
| `apps/pdf-parser/app.py` | 解析任务编排、质量报告、溯源、`document_full.json` 聚合 |
| `db/imports/import_document_full_to_postgres.py` | 将 A 股 `document_full.json` 写入 PostgreSQL |

## 测试

运行测试：

```bash
cd /home/maoyd/siq-research-engine/services/market-report-rules
uv run --extra dev pytest -q
```

## 目录结构

```text
services/market-report-rules/
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
```

## 后续建议

1. 为每个市场补充真实 fixture 样本。
2. 为公司级 override 建立更明确的规则文件。
3. 建立 market-specific writer，把 `load_plan` 写入各市场独立数据库。
4. 增加 HTML/iXBRL 原文锚点抽取和渲染页码增强。
5. 扩展欧洲、日韩市场时继续保持“独立 market module、独立 profile、独立数据库、独立 Wiki”的模式。
