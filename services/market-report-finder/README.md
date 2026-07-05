# 统一市场公告搜索下载服务

## 模块定位

`services/market-report-finder` 是 SIQ 的官方披露入口抽象层。它负责在不同市场的官方入口上解析公司主体、筛选披露文件、下载原始材料并输出统一的下载元数据，而不是把下载逻辑散落在前端或脚本里。

它的职责可以概括为一句话：把“去哪里找官方文件”变成一个稳定服务，而不是变成一堆一次性爬虫脚本。

## 在系统中的位置

```text
apps/web / apps/api
  -> services/market-report-finder
     -> CNINFO / HKEXnews / SEC / ESEF / EDINET / DART
     -> 本地下载目录与元数据索引
```

它只负责下载前链路：

1. 解析公司身份。
2. 查询官方披露来源。
3. 选中目标报告。
4. 下载原始文件。
5. 按市场和公司目录保存结果。

它不负责 PDF / HTML 解析、财务抽取、勾稽校验、数据库导入或 Agent 问答。

## 核心能力

| 能力 | 说明 |
| --- | --- |
| 公司主体解析 | 股票代码、ticker、CIK、EDINET code、corp code 等主体标识解析 |
| 多市场官方检索 | 面向 CN / HK / US / EU / JP / KR 的官方入口统一提供搜索与最新披露发现 |
| 原始文件下载 | 保存 PDF、HTML、XHTML、XML、ZIP 等官方材料 |
| 下载目录治理 | 按市场、公司、年份和报告语义组织落盘结构 |
| 元数据索引 | 为后续 package build、解析和批处理提供统一下载清单 |
| 限速与来源策略 | 对 SEC、HKEX、EDINET、DART 等来源控制请求频率和请求头 |

## 技术难点

这个服务看起来像“下载器”，实际上承担的是多市场披露入口抽象工作：

- 市场入口差异大：CNINFO、HKEXnews、SEC EDGAR、EDINET、DART 和 ESEF 的主体标识、分页策略、文件类型和字段命名完全不同。
- 官方源要求严格：部分来源需要合规 User-Agent、限速、分页回溯或多阶段请求。
- 文件语义复杂：10-K、20-F、6-K、季报、中报、年报、摘要版和附件版需要区分，不能一股脑下载。
- 目录治理要稳定：后续 parser、rules、importer 和前端都依赖下载目录与元数据结构，不能今天按公司名存、明天按 ticker 存。

## 输入输出或关键合同

### 输入

- 公司关键词、股票代码、ticker、CIK、EDINET code、DART corp code。
- 市场标识、目标报告类型、时间范围或直接下载 URL。

### 输出

- 原始官方披露文件。
- 统一下载元数据。
- 下游可消费的本地相对路径与目录结构。

### 下载目录约定

```text
data/market-report-finder/downloads/
  CN/
  HK/
  US/
  EU/
  JP/
  KR/
```

### 核心 API

| API | 用途 |
| --- | --- |
| `GET /health` | 健康检查 |
| `GET /v1/sources` | 当前支持的数据源 |
| `POST /v1/company/resolve` | 公司主体解析 |
| `POST /v1/reports/recent` | 查询近期披露 |
| `POST /v1/reports/latest` | 查询最新披露 |
| `POST /v1/reports/select-download` | 按类型选择并下载报告 |
| `POST /v1/reports/batch-download` | 批量下载指定文件 |
| `POST /v1/reports/direct-download` | 直接下载单个文件 |

## 启动方式

```bash
cd /home/maoyd/siq-research-engine/services/market-report-finder
uv sync --extra dev
MARKET_REPORT_DOWNLOAD_DIR=/home/maoyd/siq-research-engine/data/market-report-finder/downloads \
uv run python -m uvicorn market_report_finder_service.app:app --host 127.0.0.1 --port 18000
```

可选备用实例常见端口：`18010`。

## 关键环境变量

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `MARKET_REPORT_DOWNLOAD_DIR` | `downloads` | 原始披露文件根目录 |
| `SEC_USER_AGENT` | 服务默认值 | SEC 请求 User-Agent |
| `SEC_MAX_REQUESTS_PER_SECOND` | `8.0` | SEC 限速 |
| `HKEX_MAX_REQUESTS_PER_SECOND` | `4.0` | HKEX 限速 |
| `DART_API_KEY` | 空 | DART / OpenDART 凭证 |
| `DART_MAX_REQUESTS_PER_SECOND` | `3.0` | DART 限速 |
| `EDINET_API_KEY` | 空 | EDINET 凭证 |
| `EDINET_MAX_REQUESTS_PER_SECOND` | `3.0` | EDINET 限速 |

## 验证方式

```bash
cd /home/maoyd/siq-research-engine/services/market-report-finder
uv run pytest
curl -s http://127.0.0.1:18000/health
```

如果调整了来源解析或下载目录逻辑，应至少补跑对应市场测试，并手动验证一个 `company/resolve` 与一个下载请求。

## 维护原则

- 只把官方来源作为主来源，不让第三方聚合站成为事实入口。
- 下载目录与相对路径约定一旦被下游消费，应尽量保持稳定。
- 新增市场时优先增加独立 `markets/<code>` 模块，而不是把差异逻辑堆进共享层。
- 限速、User-Agent 和 API key 只通过环境变量或配置注入，不写死在脚本或 README 中。
- 这个服务负责“找到并保存官方文件”，不负责解释文件内容。
