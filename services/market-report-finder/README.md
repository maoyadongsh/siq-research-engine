# 统一市场公告搜索下载服务

这是 SIQ Research Engine 内部的 A 股、港股、美股、韩股和日股上市公司公告和财报发现、检索、下载入口服务。

项目路径：

```text
/home/maoyd/siq-research-engine/services/market-report-finder
```

本服务提供一个 HTTP 端口和一套统一 API，内部按市场拆分为 `markets/cn`、`markets/hk`、`markets/us`、`markets/kr` 与 `markets/jp` 下载服务模块。后续由 API 聚合后端或前端统一下载入口通过 HTTP API 调用。

## 一、项目定位

本服务负责“下载前链路”：

1. 解析公司身份
2. 查询官方披露来源
3. 筛选年报、半年报、季报等目标文件
4. 下载原始文件
5. 按当前 A 股下载器一致的目录和文件命名规则保存
6. 生成下载元数据和缓存索引

本服务不负责：

- PDF/HTML/OCR 解析
- 财务指标抽取
- 财务勾稽校验
- 入库
- 智能体问答
- Wiki 构建

解析后规则服务后续可独立实现或迁入主仓库：

```text
services/market-report-rules
```

两者职责边界：

| 服务 | 负责内容 |
|---|---|
| `services/market-report-finder` | 找报告、选报告、下载原始文件 |
| `services/market-report-rules` | 处理解析后的产物、抽取指标、校验、生成入库计划 |

## 二、当前支持范围

### A 股

市场标识：

```text
CN
```

数据来源：

- 巨潮资讯 CNINFO
- `topSearch/query` 公司解析
- `hisAnnouncement/query` 定期报告检索
- 巨潮静态 PDF 下载

支持公司解析方式：

- 股票代码，例如 `600519`
- 公司名/简称，例如 `贵州茅台`、`茅台`

支持报告类型：

| 用户侧 report type | CNINFO 类别 |
|---|---|
| `annual` | 年度报告 |
| `semiannual` | 半年度报告 |
| `q1` | 一季度报告 |
| `q3` | 三季度报告 |

注意：

- A 股当前聚焦正式定期报告，会排除摘要和英文版公告。
- A 股 PDF 后续解析继续使用 A 股现有 PDF/财务抽取规则。

### 美股

市场标识：

```text
US
```

数据来源：

- SEC EDGAR
- SEC submissions
- SEC company ticker mapping
- SEC primary document download

支持公司解析方式：

- ticker，例如 `AAPL`
- 公司名，例如 `Apple Inc.`
- CIK，例如 `320193`
- company_id / cik 形式

支持报告表单：

| 用户侧 report type | SEC 表单 | 说明 |
|---|---|---|
| `annual` | `10-K` | 美国本土上市公司年报 |
| `annual` | `20-F` | 外国私人发行人年报 |
| `q1/q2/q3/q4/quarterly` | `10-Q` | 美国本土上市公司季报 |
| `q1/q2/q3/q4/quarterly` | `6-K` | 外国私人发行人临时/中期披露，可能包含季报或半年报 |

注意：

- 美股下载到的文件不一定是 PDF，常见是 HTML / iXBRL。
- 美股年报、季报的结构化解析应优先使用 XBRL/iXBRL，而不是强行走 PDF 逻辑。
- SEC 请求应设置合规的 `SEC_USER_AGENT`。

### 港股

市场标识：

```text
HK
```

数据来源：

- HKEXnews
- HKEX issuer stock list
- HKEX title search
- HKEX 公告 PDF

支持公司解析方式：

- 股票代码，例如 `00700`
- 公司名，例如 `TENCENT`
- HKEX stock id

支持报告类型：

| 用户侧 report type | HKEX 对应类型 |
|---|---|
| `annual` | 年报 / Annual Report |
| `semiannual` | 中报 / Interim Report / Half-Year Report |
| `q1/q2/q3/q4/quarterly` | 季报 / Quarterly Report / Quarterly Results |

注意：

- 港股大多数报告以 PDF 形式披露。
- 主板公司通常不强制季度报告，但历史 GEM、双重上市、自愿披露可能存在 Q1/Q3。
- 港股 PDF 文件后续解析和指标抽取应走港股专属规则，不复用 A 股规则。

### 韩股

市场标识：

```text
KR
```

数据来源：

- 韩国 DART / OpenDART
- `corpCode.xml` 公司目录
- `list.json` 定期报告检索
- `document.xml` 原始披露文档下载

支持公司解析方式：

- 6 位股票代码，例如 `005930`
- 公司名，例如 `삼성전자`
- DART corp code，例如 `00126380`

支持报告类型：

| 用户侧 report type | DART 报告 |
|---|---|
| `annual` | 사업보고서 |
| `semiannual` | 반기보고서 |
| `quarterly` / `q1` / `q3` | 분기보고서 |

注意：

- 韩股查询和下载需要配置 `DART_API_KEY`。
- DART `document.xml` 通常返回 XML zip 包，后续解析应按 DART XML/XBRL 或通用文档链路处理。

### 日股

市场标识：

```text
JP
```

数据来源：

- 日本 EDINET API v2
- `documents.json` 文档列表
- `documents/{docID}?type=2` PDF 下载

支持公司解析方式：

- 4 位证券代码，例如 `7203`
- 公司名，例如 `トヨタ自動車`
- EDINET code，例如 `E02144`

支持报告类型：

| 用户侧 report type | EDINET 报告 |
|---|---|
| `annual` | 有価証券報告書 |
| `semiannual` | 半期報告書 |
| `quarterly` / `q1` / `q2` / `q3` | 四半期報告書 |

注意：

- EDINET API v2 如启用订阅鉴权，请配置 `EDINET_API_KEY`。
- 日股 PDF 可先走通用 PDF 解析，XBRL zip 可后续由规则服务单独接入。

## 三、设计原则

### 1. 服务边界独立，接入通过 API

本服务负责下载前链路，不直接耦合 Web 页面、PDF 解析和入库流程。主项目接入时建议只通过 HTTP API 调用：

```text
SIQ Web/API -> services/market-report-finder -> 下载原始文件
SIQ Web/API -> 解析服务 -> 解析 PDF/HTML/iXBRL
SIQ Web/API -> market-report-rules -> 抽取/校验/入库计划
```

### 2. 一个端口，市场服务模块隔离

本服务只暴露一个 FastAPI 应用和一个端口，内部按市场拆分维护：

| 模块 | 负责内容 |
|---|---|
| `markets/cn` | 巨潮资讯、A 股代码/公司名、年报/半年报/一季报/三季报 PDF 下载 |
| `markets/us` | SEC EDGAR、company tickers、CIK、10-K/10-Q/20-F/6-K、HTML/iXBRL 下载 |
| `markets/hk` | HKEXnews、股票代码/stock id、年报/中报/季报公告 PDF 下载 |
| `markets/kr` | DART、股票代码/corp code、사업보고서/반기보고서/분기보고서 下载 |
| `markets/jp` | EDINET、证券代码/EDINET code、有価証券報告書/半期/四半期 PDF 下载 |
| `services/downloader.py` | 跨市场共享的文件下载、命名、缓存、去重和元数据 |
| `services/orchestrator.py` | 统一 API 分发层，按 `market` 路由到对应市场模块 |

未来新增市场时新增 `markets/<market>`，不改乱 CN/HK/US 既有逻辑。

### 3. 下载目录和文件命名沿用 A 股逻辑

为了后续接入时减少适配成本，各市场下载后的目录和文件名遵循原 A 股下载器风格。

目录规则：

```text
data/market-report-finder/downloads/
  CN/
    <company_name>/
      <report_year>/
        年报/
        财报/
  US/
    <company_name>/
      <report_year>/
        年报/
        财报/
  HK/
    <company_name>/
      <report_year>/
        年报/
        财报/
  KR/
    <company_name>/
      <report_year>/
        年报/
        财报/
  JP/
    <company_name>/
      <report_year>/
        年报/
        财报/
```

落盘目录强制包含市场维度和报告年度，避免 US/HK/CN 同名公司互相混淆，也方便同一公司多年归档。

归类规则：

| 报告类型 | 保存目录 |
|---|---|
| 年报、10-K、20-F | `年报/` |
| 半年报、中报、季报、10-Q、6-K、业绩公告 | `财报/` |

### 4. 下载统一入口，解析和入库分市场标签页

本服务只负责下载，因此 CN/HK/US/KR/JP 可以共享 API 形状。

Web 工作台建议采用：

- 搜索下载：一个统一页面，页面内按 `A股 / 港股 / 美股 / 韩股 / 日股` 做市场切换。
- 解析：按市场设置标签页或工作流，分别承载 A 股 PDF、港股 PDF、美股 SEC/iXBRL。
- 入库：按市场设置标签页或任务类型，分别执行 CN/HK/US 的字段映射、指标规则和质量门禁。

后续解析后的指标抽取、校验、入库、智能体问答必须分市场：

- 美股走 SEC / XBRL / iXBRL 规则
- 港股走 HKEX / PDF 表格 / HKFRS / IFRS / CASBE 规则
- 韩股走 DART / XML zip / K-IFRS 规则
- 日股走 EDINET / PDF / XBRL 规则
- A 股继续使用 A 股现有规则

### 5. 官方来源优先

当前只接官方来源：

| 市场 | 官方来源 |
|---|---|
| A 股 | 巨潮资讯 CNINFO |
| 美股 | SEC EDGAR |
| 港股 | HKEXnews |

不依赖第三方聚合网站作为主来源。

## 四、运行方式

进入项目目录：

```bash
cd /home/maoyd/siq-research-engine/services/market-report-finder
```

建议设置 SEC User-Agent：

```bash
export SEC_USER_AGENT="SIQ Research your_email@example.com"
```

启动服务：

```bash
uv run uvicorn market_report_finder_service.app:app --host 127.0.0.1 --port 8010
```

在 SIQ 一键编排中，本服务作为统一公告下载入口运行在 `18000`；如需额外启动备用实例，可运行在 `18010`：

```bash
cd /home/maoyd/siq-research-engine
SIQ_START_MARKET_REPORT_FINDER=1 ./start_all.sh
```

启动后可访问：

```text
http://127.0.0.1:8010
http://127.0.0.1:8010/docs
http://127.0.0.1:8010/health
```

## 五、环境变量

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `MARKET_REPORT_DOWNLOAD_DIR` | `downloads` | 下载文件根目录；SIQ 编排建议指向 `data/market-report-finder/downloads` |
| `SEC_USER_AGENT` | `market-report-finder-service/0.1 contact@example.com` | SEC 请求 User-Agent |
| `SEC_MAX_REQUESTS_PER_SECOND` | `8.0` | SEC 请求限速 |
| `HKEX_MAX_REQUESTS_PER_SECOND` | `4.0` | HKEX 请求限速 |
| `DART_API_KEY` | 空 | 韩国 DART OpenAPI key；韩股查询和下载必需 |
| `DART_MAX_REQUESTS_PER_SECOND` | `3.0` | DART 请求限速 |
| `EDINET_API_KEY` | 空 | 日本 EDINET API v2 subscription key；按账号要求配置 |
| `EDINET_MAX_REQUESTS_PER_SECOND` | `3.0` | EDINET 请求限速 |

示例：

```bash
export MARKET_REPORT_DOWNLOAD_DIR="/home/maoyd/siq-research-engine/data/market-report-finder/downloads"
export SEC_USER_AGENT="SIQ Research your_email@example.com"
export SEC_MAX_REQUESTS_PER_SECOND="6"
export HKEX_MAX_REQUESTS_PER_SECOND="3"
export DART_API_KEY="replace-with-opendart-key"
export EDINET_API_KEY="replace-with-edinet-key-if-required"
```

## 六、核心 API

### 1. 健康检查

```bash
curl -s http://127.0.0.1:8010/health
```

### 2. 查看数据源

```bash
curl -s http://127.0.0.1:8010/v1/sources
```

返回当前支持的数据源、市场、表单和说明。

### 3. 解析美股公司

```bash
curl -s http://127.0.0.1:8010/v1/company/resolve \
  -H 'content-type: application/json' \
  -d '{"market":"US","ticker":"AAPL"}'
```

也可以使用 CIK：

```bash
curl -s http://127.0.0.1:8010/v1/company/resolve \
  -H 'content-type: application/json' \
  -d '{"market":"US","cik":"320193"}'
```

### 4. 解析港股公司

```bash
curl -s http://127.0.0.1:8010/v1/company/resolve \
  -H 'content-type: application/json' \
  -d '{"market":"HK","ticker":"00700"}'
```

也可以使用公司名：

```bash
curl -s http://127.0.0.1:8010/v1/company/resolve \
  -H 'content-type: application/json' \
  -d '{"market":"HK","company_name":"TENCENT"}'
```

### 5. 查询近期报告

查询美股近期财报：

```bash
curl -s http://127.0.0.1:8010/v1/reports/recent \
  -H 'content-type: application/json' \
  -d '{"market":"US","ticker":"AAPL","target":"financial_report","limit":8}'
```

查询港股近期财报：

```bash
curl -s http://127.0.0.1:8010/v1/reports/recent \
  -H 'content-type: application/json' \
  -d '{"market":"HK","ticker":"00700","target":"financial_report","limit":8}'
```

按报告年份筛选：

```bash
curl -s http://127.0.0.1:8010/v1/reports/recent \
  -H 'content-type: application/json' \
  -d '{"market":"HK","ticker":"00700","target":"financial_report","report_year":2025,"limit":20}'
```

### 6. 查询最新报告

```bash
curl -s http://127.0.0.1:8010/v1/reports/latest \
  -H 'content-type: application/json' \
  -d '{"market":"US","ticker":"AAPL","target":"annual_report"}'
```

### 7. 按类型下载报告

下载港股年报、中报、一季报、三季报：

```bash
curl -s http://127.0.0.1:8010/v1/reports/select-download \
  -H 'content-type: application/json' \
  -d '{"market":"HK","ticker":"00700","report_types":["annual","semiannual","q1","q3"],"report_year":2025}'
```

下载美股年报和季报：

```bash
curl -s http://127.0.0.1:8010/v1/reports/select-download \
  -H 'content-type: application/json' \
  -d '{"market":"US","ticker":"AAPL","report_types":["annual","quarterly"],"report_year":2025}'
```

### 8. 批量下载指定 URL

适合外部已经拿到公告 URL 的场景：

```bash
curl -s http://127.0.0.1:8010/v1/reports/batch-download \
  -H 'content-type: application/json' \
  -d '{
    "market":"HK",
    "default_company_name":"TENCENT",
    "items":[
      {
        "document_url":"https://www1.hkexnews.hk/listedco/listconews/sehk/example.pdf",
        "company_name":"TENCENT",
        "ticker":"00700",
        "market":"HK",
        "report_type":"annual",
        "report_end":"2025-12-31",
        "published_at":"2026-04-16"
      }
    ]
  }'
```

### 9. 直接下载单个报告

```bash
curl -s http://127.0.0.1:8010/v1/reports/direct-download \
  -H 'content-type: application/json' \
  -d '{
    "market":"US",
    "company_name":"Apple Inc.",
    "ticker":"AAPL",
    "company_id":"320193",
    "document_url":"https://www.sec.gov/Archives/edgar/data/320193/example.htm",
    "form":"10-K",
    "report_end":"2025-09-27",
    "published_at":"2025-10-31"
  }'
```

## 七、请求参数说明

### 公司标识字段

至少提供一个：

| 字段 | 说明 |
|---|---|
| `market` | 市场，`US` 或 `HK` |
| `ticker` | 股票代码，例如 `AAPL`、`00700` |
| `company_name` | 公司名 |
| `company_id` | 内部或外部公司 ID |
| `cik` | SEC CIK，美股可用 |

### 报告目标 target

| target | 说明 |
|---|---|
| `latest_report` | 最新报告 |
| `annual_report` | 年报 |
| `semiannual_report` | 半年报 / 中报 |
| `quarterly_report` | 季报 |
| `financial_report` | 财务报告综合目标 |

### report_types

`select-download` 支持的报告类型：

| report_type | 说明 |
|---|---|
| `annual` | 年报 |
| `semiannual` | 半年报 / 中报 |
| `q1` | 一季报 |
| `q2` | 二季报 |
| `q3` | 三季报 |
| `q4` | 四季报 |
| `quarterly` | 季报泛化类型 |

## 八、下载文件目录与命名规则

### 目录结构

默认下载根目录：

```text
downloads/
```

可通过 `MARKET_REPORT_DOWNLOAD_DIR` 修改。

保存结构：

```text
downloads/
  CN/
    <company_name>/
      <report_year>/
        年报/
        财报/
  US/
    <company_name>/
      <report_year>/
        年报/
        财报/
  HK/
    <company_name>/
      <report_year>/
        年报/
        财报/
```

示例：

```text
downloads/
  CN/
    贵州茅台/
      2025/
        年报/
          贵州茅台_CN_600519_2025-12-31_年报_2026-04-17_cninfo_ab12cd34.pdf
  US/
    Apple-Inc/
      2025/
        年报/
          Apple-Inc_US_AAPL_2025-09-27_10-K_2025-10-31_sec_9a1590d0.html
  HK/
    TENCENT/
      2026/
        财报/
          TENCENT_HK_00700_2026-03-31_一季报_2026-05-13_hkex_38abb4a2.pdf
```

### 文件名规则

```text
<company>_<market>_<ticker>_<report_end>_<report_type>_<published_at>_<source_id>_<url_hash>.<ext>
```

字段说明：

| 字段 | 说明 |
|---|---|
| `company` | 公司名，已做文件名安全处理 |
| `market` | `CN`、`HK` 或 `US` |
| `ticker` | 股票代码或公司 ID |
| `report_end` | 报告期截止日 |
| `report_type` | 报告类型，例如 `10-K`、`年报`、`一季报` |
| `published_at` | 披露日期 |
| `source_id` | 来源，例如 `cninfo`、`hkex`、`sec` |
| `url_hash` | URL SHA256 前 8 位，避免重名 |
| `ext` | 文件扩展名，例如 `.pdf`、`.html` |

### 报告类型中文标签

下载器会把部分报告类型转换为中文标签：

| 类型 | 文件名标签 |
|---|---|
| `annual` | `年报` |
| `semiannual` | `半年报` |
| `quarterly` 且报告期为 3 月 31 日 | `一季报` |
| `quarterly` 且报告期为 6 月 30 日 | `半年报` 或季度标签，按候选报告类型决定 |
| `quarterly` 且报告期为 9 月 30 日 | `三季报` |
| `10-K` | `10-K` |
| `10-Q` | `10-Q` |
| `20-F` | `20-F` |
| `6-K` | `6-K` |

## 九、下载缓存、去重与元数据

下载器会在文件目录中维护：

```text
.download_index.json
```

用于：

- 按 URL 判断缓存命中
- 按文件 SHA256 判断内容去重
- 避免重复下载同一文件
- 记录文件路径、报告类型、报告期、披露日期等元数据

每个下载文件旁边会生成：

```text
<file_name>.<ext>.metadata.json
```

元数据内容包括：

- 原始候选报告 `candidate`
- 下载文件名
- 本地保存路径
- 文件大小
- content type
- SHA256

如果 `download_overwrite` 为 false，已经下载过的 URL 或相同内容会优先复用缓存。

## 十、返回数据结构概览

### CompanyEntity

公司解析结果核心字段：

| 字段 | 说明 |
|---|---|
| `market` | 市场 |
| `company_id` | 公司 ID |
| `ticker` | 股票代码 |
| `company_name` | 公司名 |
| `exchange` | 交易所 |
| `cik` | SEC CIK |
| `hkex_stock_id` | HKEX 内部 stock id |
| `aliases` | 别名 |
| `confidence` | 匹配置信度 |
| `match_reason` | 匹配原因 |

### FilingCandidate

候选报告核心字段：

| 字段 | 说明 |
|---|---|
| `source_id` | 来源 ID，例如 `sec`、`hkex` |
| `market` | 市场 |
| `company_id` | 公司 ID |
| `ticker` | 股票代码 |
| `report_type` | 报告类型 |
| `report_family` | 报告家族，年报/半年报/季报/current |
| `form` | 原始表单或公告类型 |
| `title` | 报告标题 |
| `report_end` | 报告期截止日 |
| `published_at` | 披露日期 |
| `document_url` | 直接下载 URL |
| `landing_url` | 公告或 filing 页面 URL |
| `file_format` | 文件格式 |
| `inline_xbrl` | 是否 iXBRL，美股可能有 |

### DownloadedReportFile

下载结果核心字段：

| 字段 | 说明 |
|---|---|
| `file_name` | 本地文件名 |
| `saved_path` | 本地绝对路径 |
| `size_bytes` | 文件大小 |
| `content_type` | HTTP content type |
| `cache_hit` | 是否缓存命中 |
| `deduplicated` | 是否通过内容去重 |
| `content_sha256` | 文件 SHA256 |
| `metadata_path` | 元数据 JSON 路径 |

## 十一、与解析/规则服务的衔接

下载服务产出的是原始文件，例如：

- 港股 PDF
- SEC HTML
- SEC iXBRL HTML
- SEC filing primary document

后续推荐流程：

```text
1. `services/market-report-finder` 下载报告
2. PDF/HTML 解析服务生成 document_full / table_index / XBRL facts
3. `services/market-report-rules` 抽取财务指标、经营指标、校验并生成 load_plan
4. 市场专属 writer 写入 `siq` 数据库中的市场 schema
5. 市场专属智能体读取对应市场数据库和 Wiki
```

关键约束：

- 美股、港股不要写入 A 股库。
- 美股、港股不要复用 A 股分析智能体。
- 下载界面可以统一，但抽取规则、校验规则、入库规则和智能体应按市场隔离。

## 十二、官方来源说明

### SEC

使用 SEC EDGAR 官方数据。

需要注意：

- SEC 对自动化访问有 fair access 要求。
- 请求必须带明确 User-Agent。
- 请求频率应限制，默认 `SEC_MAX_REQUESTS_PER_SECOND=8`。
- 下载 HTML/iXBRL 后，后续证据溯源应保留 SEC URL、accession number、XBRL tag、anchor/xpath。

官方参考：

```text
https://www.sec.gov/search-filings/edgar-application-programming-interfaces
https://www.sec.gov/about/developer-resources
```

### HKEX

使用 HKEXnews 官方公告搜索与下载。

需要注意：

- 港股报告多为 PDF。
- 港股公告标题有中英文、繁简体和类别差异。
- 港股季度报告不是所有发行人都有。
- 港股 PDF 后续解析应保留页码、表格编号、行列和 bbox。

官方参考：

```text
https://www1.hkexnews.hk/search/titlesearch.xhtml
```

## 十三、测试

运行测试：

```bash
cd /home/maoyd/siq-research-engine/services/market-report-finder
uv run --extra dev pytest -q
```

当前测试覆盖方向：

- SEC recent filings payload 到候选报告的映射
- HKEX 股票解析
- 下载目录规则
- 文件命名规则
- 年报/财报目录归类
- 下载缓存和去重基础逻辑

## 十四、当前文件结构

```text
services/market-report-finder/
  README.md
  pyproject.toml
  src/market_report_finder_service/
    app.py
    api/
      routes/
        company.py
        health.py
        reports.py
        sources.py
    core/
      config.py
    models/
      schemas.py
    markets/
      base.py
      us/
        client.py
        service.py
      hk/
        client.py
        service.py
    services/
      downloader.py
      orchestrator.py
  tests/
    test_downloader.py
    test_hkex_client.py
    test_sec_client.py
```

## 十五、后续扩展建议

后续欧洲、日本、韩国市场建议作为新 connector 接入，而不是改乱 US/HK 逻辑。

每新增一个市场，应至少补齐：

1. `Market` enum
2. 公司解析器
3. 官方公告/财报检索 connector
4. 本地报告类型到统一 `annual/semiannual/quarterly/current` 的映射
5. 下载候选 `FilingCandidate` 生成规则
6. 官方来源限速策略
7. 文件命名和目录归类规则
8. 对应市场的解析、抽取、校验、入库规则服务
9. 对应市场的 PostgreSQL 库和 Wiki 命名空间
10. 对应市场专属智能体

统一下载入口可以保留，但市场内部规则必须隔离。这样后续扩展欧洲、日韩时，不会破坏美股、港股和 A 股已有链路。
