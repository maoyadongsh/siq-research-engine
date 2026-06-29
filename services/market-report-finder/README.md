# 统一市场公告搜索下载服务

这是 SIQ Research Engine 的官方披露入口服务，负责 CN/HK/US/EU/JP/KR 上市公司公告、定期报告和原始披露文件的解析、检索和下载。

项目路径：

```text
/home/maoyd/siq-research-engine/services/market-report-finder
```

本服务只负责“下载前链路”：

1. 解析公司身份。
2. 查询官方披露来源。
3. 筛选年报、半年报、季报等目标文件。
4. 下载原始文件。
5. 按市场和报告年度保存。
6. 生成下载元数据和缓存索引。

它不负责 PDF/HTML/OCR 解析、财务抽取、勾稽校验、入库或 Agent 问答。这些职责由 `apps/pdf-parser`、`apps/document-parser`、`services/market-report-rules` 和 `apps/api` 承接。

## 当前支持范围

### A 股

- 市场标识：`CN`
- 数据来源：巨潮资讯 CNINFO
- 公司解析：股票代码、公司名、简称
- 报告类型：年度、半年度、一季报、三季报
- 特点：只保留正式定期报告，排除摘要和英文版公告

### 港股

- 市场标识：`HK`
- 数据来源：HKEXnews
- 公司解析：股票代码、公司名、HKEX stock id
- 报告类型：年报、中报、季报、季度结果
- 特点：多数披露为 PDF，表格结构和繁简体差异明显

### 美股

- 市场标识：`US`
- 数据来源：SEC EDGAR、submissions、company ticker mapping
- 公司解析：ticker、公司名、CIK
- 报告类型：10-K、10-Q、20-F、6-K
- 特点：HTML/iXBRL 常见，结构化程度高但期间语义复杂

### 欧股

- 市场标识：`EU`
- 数据来源：ESEF / 各国披露入口聚合
- 公司解析：ticker、国家和本地目录
- 特点：与后续规则服务、ESEF 包和导入脚本联动

### 日股

- 市场标识：`JP`
- 数据来源：EDINET API v2
- 公司解析：证券代码、公司名、EDINET code
- 报告类型：有価証券報告書、半期報告書、四半期報告书

### 韩股

- 市场标识：`KR`
- 数据来源：DART / OpenDART
- 公司解析：股票代码、公司名、corp code
- 报告类型：사업보고서、반기보고서、분기보고서

## 设计原则

### 1. 服务边界独立，接入通过 API

本服务负责下载前链路，不直接耦合 Web 页面、PDF 解析和入库流程。主项目接入时建议只通过 HTTP API 调用：

```text
SIQ Web/API -> services/market-report-finder -> 下载原始文件
SIQ Web/API -> 解析服务 -> 解析 PDF/HTML/iXBRL/ESEF/EDINET/DART
SIQ Web/API -> market-report-rules -> 抽取/校验/入库计划
```

### 2. 一个端口，市场模块隔离

本服务只暴露一个 FastAPI 应用和一个端口，内部按市场拆分维护：

| 模块 | 负责内容 |
| --- | --- |
| `markets/cn` | 巨潮资讯公司解析、定期报告下载 |
| `markets/us` | SEC EDGAR、company ticker、CIK、HTML/iXBRL 下载 |
| `markets/hk` | HKEXnews、股票代码、PDF 公告下载 |
| `markets/eu` | 欧股披露目录和证据包前置下载 |
| `markets/jp` | EDINET、证券代码、PDF/XBRL 下载 |
| `markets/kr` | DART、corp code、XML/zip 下载 |
| `services/downloader.py` | 跨市场共享的文件下载、命名、缓存、去重和元数据 |
| `services/orchestrator.py` | 统一 API 分发层，按 `market` 路由到对应市场模块 |

### 3. 下载目录和文件命名沿用 A 股逻辑

为了后续接入时减少适配成本，各市场下载后的目录和文件名遵循一致的分层规则：

```text
data/market-report-finder/downloads/
  CN/
    <company_name>/
      <report_year>/
  US/
    <company_name>/
      <report_year>/
  HK/
    <company_name>/
      <report_year>/
  EU/
    <company_name>/
      <report_year>/
  JP/
    <company_name>/
      <report_year>/
  KR/
    <company_name>/
      <report_year>/
```

归类规则：

| 报告类型 | 保存目录 |
| --- | --- |
| 年报、10-K、20-F、有価証券報告书、사업보고서 | `年报/` 语义目录 |
| 半年报、季报、10-Q、6-K、半期报告、Quarterly report | `财报/` 语义目录 |

### 4. 下载统一入口，解析和入库分市场标签页

本服务只负责下载，因此 CN/HK/US/EU/JP/KR 可以共享 API 形状。后续解析后的指标抽取、校验、入库、智能体问答必须分市场：

- 美股走 SEC / XBRL / iXBRL 规则。
- 港股走 HKEX / PDF 表格 / HKFRS / IFRS 规则。
- 日股走 EDINET / PDF / XBRL 规则。
- 韩股走 DART / XML zip / K-IFRS 规则。
- 欧股走 ESEF / PDF / IFRS 规则。

### 5. 官方来源优先

当前只接官方来源，不依赖第三方聚合站作为主来源。

## 运行方式

```bash
cd /home/maoyd/siq-research-engine/services/market-report-finder
uv sync
MARKET_REPORT_DOWNLOAD_DIR=/home/maoyd/siq-research-engine/data/market-report-finder/downloads \
uv run python -m uvicorn market_report_finder_service.app:app --host 127.0.0.1 --port 18000
```

启用 SEC 请求时，建议设置：

```bash
export SEC_USER_AGENT="SIQ Research your_email@example.com"
```

在 SIQ 一键编排中，本服务默认运行在 `18000`；如需额外启动备用实例，可运行在 `18010`。

## 环境变量

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MARKET_REPORT_DOWNLOAD_DIR` | `downloads` | 下载文件根目录；建议指向 `data/market-report-finder/downloads` |
| `SEC_USER_AGENT` | 服务默认值 | SEC 请求 User-Agent，生产应填写真实联系邮箱 |
| `SEC_MAX_REQUESTS_PER_SECOND` | `8.0` | SEC 请求限速 |
| `HKEX_MAX_REQUESTS_PER_SECOND` | `4.0` | HKEX 请求限速 |
| `DART_API_KEY` | 空 | 韩国 DART/OpenDART API key |
| `DART_MAX_REQUESTS_PER_SECOND` | `3.0` | DART 请求限速 |
| `EDINET_API_KEY` | 空 | 日本 EDINET API key |
| `EDINET_MAX_REQUESTS_PER_SECOND` | `3.0` | EDINET 请求限速 |

## 核心 API

| API | 作用 |
| --- | --- |
| `GET /health` | 健康检查 |
| `GET /v1/sources` | 查看当前支持的数据源 |
| `POST /v1/company/resolve` | 解析公司主体 |
| `POST /v1/reports/recent` | 查询近期报告 |
| `POST /v1/reports/latest` | 查询最新报告 |
| `POST /v1/reports/select-download` | 按类型下载报告 |
| `POST /v1/reports/batch-download` | 批量下载指定 URL |
| `POST /v1/reports/direct-download` | 直接下载单个报告 |

## 开发检查

```bash
cd /home/maoyd/siq-research-engine/services/market-report-finder
uv run pytest
```

## 维护原则

- 只接官方来源。
- 下载目录必须按市场隔离。
- 同名公司不能跨市场混放。
- 新增市场时优先增加 `markets/<code>` 模块，再补 README 和测试。
