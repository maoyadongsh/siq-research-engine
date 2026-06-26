# Report Finder Service

一个面向"A 股公司名/股票代码 -> 巨潮资讯官方公开年报/财报文件"的轻量后端底座。

## 当前检查结论

检查时间：2026-05-29。当前服务运行于 `http://127.0.0.1:8000`，`/health` 返回：

```json
{"status":"ok","service":"report-finder-service"}
```

SIQ 主前端通过 Vite 将 `/api/v1/*` 代理到本服务；聚合后端只负责读取和管理本服务下载目录中的 PDF 文件。因此本服务是“搜索下载”功能的真实后端，不在 `/home/maoyd/siq-research-engine/backend` 内。

### 与 SIQ 的关系

| SIQ 功能 | 本服务提供的能力 |
| --- | --- |
| `/search` 公司解析 | `/v1/resolve` |
| `/search` 近期报告列表 | `/v1/reports/recent` |
| `/search` 批量/选择下载 | `/v1/reports/batch-download`、`/v1/reports/select-download` |
| `/parse` 选择已下载 PDF | 下载文件落在 `downloads/`，再由聚合后端 `/api/downloads/*` 暴露给前端 |

### 决赛关注点

| 维度 | 本服务贡献 |
| --- | --- |
| 创新性 | 把用户输入的公司简称/代码映射到官方公告源，作为“从真实披露文件开始”的第一步 |
| 技术难度 | 巨潮 `topSearch/query` 与 `hisAnnouncement/query` 串联、公告类型筛选、下载去重和公司映射 Agent 预留 |
| 完成度 | 当前 A 股巨潮主链路已打通，支持最近财报查询、单文件/批量/选择下载 |
| 商业价值 | 降低投研人员寻找官方年报的时间成本，保证后续分析链路以权威公开披露文件为输入 |

### 评委技术说明

本服务是 SIQ 数据链路的入口。金融智能体如果从非官方 PDF 或用户随手上传的未知文件开始，后续再强的分析也难以保证可信；因此 Report Finder 先解决“找到正确披露文件”这一问题，再把下载结果交给解析和证据层。

| 模块 | 技术实现 | 说明 |
| --- | --- | --- |
| 技术架构 | FastAPI 服务层 + 巨潮适配器 + 报告选择器 + 下载器 + 本地文件缓存 | 从官方披露源到本地 PDF 的可复现链路 |
| 技术栈 | FastAPI、Pydantic、httpx/requests、Jinja2 | 同时支持 API 调用和独立页面调试 |
| 数据流 | 用户输入公司 -> 解析证券主体 -> 检索官方公告 -> 筛选目标报告 -> 下载 PDF -> 暴露本地路径给 SIQ | 保证后续解析从权威文件开始 |
| 公司解析 | 本地别名候选、巨潮 `topSearch/query`、可选 Company Mapping Agent | 将简称、代码、俗称映射为证券主体、`orgId` 和交易所信息 |
| 公告检索 | 巨潮 `hisAnnouncement/query`、公告类型和时间排序 | 获取年度报告、半年报、一季报、三季报等候选 |
| 报告选择 | `annual_report` / `financial_report` 策略、标题过滤、最新优先 | 避免误选摘要、修订公告或非目标类型文件 |
| 下载去重 | URL、落地页、`content_sha256`、本地路径缓存 | 避免重复下载同一披露文件 |
| 服务接口 | FastAPI + Pydantic + Jinja2 页面 | 同时支持前端 API 调用和独立网页调试 |
| 扩展位 | 公司映射 Agent | 为模糊公司名预留增强路径 |

该服务的算法复杂度主要体现在“低幻觉实体解析”和“官方公告筛选”：候选池必须来自真实接口或本地别名，Agent 只能在候选中选择，不允许生成不存在的股票代码；报告选择策略也必须保守，宁可返回候选和原因，也不把错误文件送入后续财务分析链路。

当前版本先把后端骨架搭稳：

- `FastAPI` 提供 API
- `Pydantic` 约束输入输出
- `Resolver -> Router -> Cninfo Adapter -> Selector -> Downloader` 串成主链
- 预留 `LangGraph` 公司映射扩展位
- 可选 `Company Mapping Agent` 负责把模糊公司名按严格 JSON 结构映射到标准证券实体
- 新增前端页面，支持浏览器直接查询和下载

## 适用场景

- 用户输入公司名称、简称、俗称
- 后端解析到标准公司实体
- 路由到巨潮资讯官方披露源
- 从候选文件中优先选出"最新年度报告"
- 返回文件元数据、来源站点、选取原因，并支持直接下载到本地
- 支持批量下载、选择下载、近期财报列表查询

## 当前实现状态

当前主链路已收敛为 A 股场景，只启用巨潮资讯 `cninfo` 适配器。

当前启用目标源：

- `cninfo`：A 股/深市/沪市

当前已打通的真实官方源：

- `CNINFO 巨潮资讯`：通过 `topSearch/query` 解析 `orgId`，再通过 `hisAnnouncement/query` 获取公告列表

当前下载口径：

- 默认目标仍为 `annual_report`
- `annual_report`：只返回最新 A 股年度报告
- `financial_report`：返回最新 A 股正式定期财报，允许年报、半年报、一季报、三季报参与排序
- 支持单文件下载、批量下载、选择下载，均落到本地 `downloads/`
- 下载层支持基于 `document_url / landing_url / content_sha256` 的缓存与去重

公司映射 Agent 现状：

- 默认关闭，不影响当前可运行链路
- 打开后会先收集本地别名候选和巨潮动态候选
- Agent 只允许从候选池里按严格 JSON 选一个，不允许虚构 ticker
- 适合处理"俗称、简称、错别字、模糊公司名 -> 标准证券实体"这一层

前端页面现状：

- 启动后访问 `http://<IP>:8000/` 即可使用网页版查询下载
- 基于 Jinja2 模板 + static 静态文件

## 快速开始

```bash
cd /home/maoyd/siq-research-engine/services/report-finder
uv sync --extra dev
uv run uvicorn report_finder_service.app:app --reload
```

启动后访问：
- **前端页面**：`http://127.0.0.1:8000/`
- **Swagger UI**：`http://127.0.0.1:8000/docs`
- **OpenAPI JSON**：`http://127.0.0.1:8000/openapi.json`

## API 总览

| 方法 | 路径 | 功能 |
|------|------|------|
| `GET` | `/` | **前端页面**（财报查询下载网页） |
| `GET` | `/health` | 健康检查 |
| `GET` | `/v1/sources` | 查看支持的数据源 |
| `POST` | `/v1/resolve` | 公司名称/代码 -> 标准证券实体 |
| `POST` | `/v1/reports/latest` | 查询最新年报（仅元数据） |
| `POST` | `/v1/reports/recent` | 批量查询近期财报列表 |
| `POST` | `/v1/reports/latest/download` | **下载**最新年报 PDF（文件流） |
| `POST` | `/v1/reports/download` | **下载**指定 URL 的 PDF（文件流） |
| `POST` | `/v1/reports/batch-download` | **批量下载**多个 URL（返回 JSON） |
| `POST` | `/v1/reports/select-download` | **选择下载**到本地（返回 JSON） |
| `POST` | `/v1/reports/direct-download` | 给定完整元数据直接下载（文件流） |

## 示例请求

### 1. 解析公司

```bash
curl -s http://127.0.0.1:8000/v1/resolve \
  -H 'Content-Type: application/json' \
  -d '{"company_name":"贵州茅台"}'
```

按股票代码精确解析：

```bash
curl -s http://127.0.0.1:8000/v1/resolve \
  -H 'Content-Type: application/json' \
  -d '{"company_name":"任意输入","ticker":"000001","exchange_hint":"SZSE"}'
```

### 2. 查询最新年报（仅元数据）

```bash
curl -s http://127.0.0.1:8000/v1/reports/latest \
  -H 'Content-Type: application/json' \
  -d '{"company_name":"贵州茅台","target":"annual_report"}'
```

查询最新正式财报：

```bash
curl -s http://127.0.0.1:8000/v1/reports/latest \
  -H 'Content-Type: application/json' \
  -d '{"company_name":"华安证券","target":"financial_report"}'
```

未指定类型时，默认查询最新年度报告：

```bash
curl -s http://127.0.0.1:8000/v1/reports/latest \
  -H 'Content-Type: application/json' \
  -d '{"company_name":"比亚迪"}'
```

### 3. 批量查询近期财报列表

```bash
curl -s http://127.0.0.1:8000/v1/reports/recent \
  -H 'Content-Type: application/json' \
  -d '{"company_name":"宁德时代","target":"financial_report","report_year":2025,"limit":20}'
```

> 关键点：`report_year` 不指定时，只返回**最近一年**的报告。当前 2026 年 5 月，最近一年是 2026 年，目前只披露了 2026 年一季度报告。要看到完整年报，需指定 `report_year: 2025`。

### 4. 下载最新年报 PDF（文件流）

```bash
curl -s -o 年报.pdf http://127.0.0.1:8000/v1/reports/latest/download \
  -H 'Content-Type: application/json' \
  -d '{"company_name":"平安银行","target":"annual_report"}'
```

### 5. 单文件下载（已知 URL）

```bash
curl -s -o report.pdf http://127.0.0.1:8000/v1/reports/download \
  -H 'Content-Type: application/json' \
  -d '{
    "company_name":"宁德时代",
    "document_url":"https://static.cninfo.com.cn/..."
  }'
```

### 6. 批量下载（多个 URL）

```bash
curl -s http://127.0.0.1:8000/v1/reports/batch-download \
  -H 'Content-Type: application/json' \
  -d '{
    "default_company_name":"宁德时代",
    "items":[
      {"document_url":"https://static.cninfo.com.cn/...","company_name":"宁德时代","title":"2025年报"},
      {"document_url":"https://static.cninfo.com.cn/...","company_name":"比亚迪","title":"2025年报"}
    ]
  }'
```

### 7. 选择下载（核心接口）

传入公司名 + 报告类型列表 -> 自动查询 -> 筛选 -> **下载到本地 `downloads/`** -> 返回 JSON 确认。

```bash
curl -s http://127.0.0.1:8000/v1/reports/select-download \
  -H 'Content-Type: application/json' \
  -d '{
    "company_name": "宁德时代",
    "report_types": ["annual", "semiannual"]
  }'
```

支持的 `report_types`：
- `"annual"` -- 年度报告
- `"semiannual"` -- 半年度报告
- `"q1"` -- 一季度报告
- `"q3"` -- 三季度报告

### 8. 直接下载（已知完整元数据）

```bash
curl -s -o report.pdf http://127.0.0.1:8000/v1/reports/direct-download \
  -H 'Content-Type: application/json' \
  -d '{
    "company_name":"浦发银行",
    "document_url":"https://example.com/pfbank-2025-annual.pdf",
    "landing_url":"https://example.com/pfbank-2025-annual",
    "source_name":"manual_official",
    "report_type":"annual",
    "report_end":"2025-12-31",
    "published_at":"2026-03-20"
  }'
```

### 9. 健康检查

```bash
curl http://127.0.0.1:8000/health
```

### 10. 查看支持的数据源

```bash
curl http://127.0.0.1:8000/v1/sources
```

### 11. 本地 smoke

```bash
source .venv/bin/activate
python scripts/cninfo_live_smoke.py
python scripts/playwright_smoke.py
```

## 目录结构

```text
report-finder-service/
  src/report_finder_service/
    api/
      routes/
        company.py          # /v1/resolve, /v1/sources
        downloads.py        # 各类下载接口
        health.py           # /health
        reports.py          # /v1/reports/*
    adapters/
      base.py               # 适配器基类
      cninfo.py             # 巨潮资讯适配器
    core/
      config.py             # 配置管理
    data/                   # 数据资源
    models/
      schemas.py            # Pydantic 模型
    services/
      company_mapping_agent.py  # 公司映射 Agent
      company_resolver.py       # 公司解析器
      latest_selector.py        # 最新报告选择器
      official_company_lookup.py# 官方公司查询
      orchestrator.py           # 编排器
      report_downloader.py      # 报告下载器
      source_router.py          # 来源路由
      workflow.py               # 工作流
    app.py                  # FastAPI 应用入口
  scripts/                  # Smoke 测试脚本
  static/                   # 前端静态文件
  templates/                # Jinja2 模板
  tests/                    # 单元测试
  downloads/                # 下载文件存放目录
```

## 文件存放目录结构

所有下载的 PDF 按以下规则存放：

```
downloads/
├── 宁德时代/
│   ├── 年报/
│   │   └── cninfo_300750_2025-12-31_annual.pdf
│   └── 财报/
│       ├── cninfo_300750_2025-06-30_semiannual.pdf
│       ├── cninfo_300750_2025-03-31_q1.pdf
│       └── cninfo_300750_2025-09-30_q3.pdf
└── ...
```

| 报告类型 | 子目录 |
|---------|--------|
| `annual`（年度报告） | `年报/` |
| `semiannual`（半年度报告） | `财报/` |
| `q1`（一季度报告） | `财报/` |
| `q3`（三季度报告） | `财报/` |

## 语料边界

这个服务当前只负责：

- A 股上市公司公开财报、年报文件入口
- A 股公司名称/股票代码到巨潮资讯披露源的解析、检索、选择、下载

这个服务当前不负责：

- 港股、美股财报检索
- 公开行业数据抓取
- 行业统计或宏观数据整合

这些应由上层研究系统的外部数据层单独接入。

## 注意事项

1. **巨潮接口偶发超时**：解析阶段如果遇到 `504 Gateway Time-out`，cninfo adapter 会自动重试 3 次（退避 2s->4s->8s）。
2. **年份选择**：`/v1/reports/recent` 默认只返回最近一年的报告。当前 2026 年 5 月，最近一年是 2026 年，目前只披露了 2026 年一季度报告。要看到完整年报，需指定 `report_year: 2025`。
3. **文件缓存**：同一份 PDF（基于 `content_sha256`）重复下载会命中缓存，不会重复落盘。
4. **中文 Header 已 URL 编码**：读取 `X-Company-Name`、`X-Report-Title` 时需要 `urllib.parse.unquote` 解码。
5. **前端页面**：浏览器访问 `http://<IP>:8000/` 即可使用网页版查询下载。

## 下一步建议

1. ~~给下载层增加缓存、去重和对象存储落盘策略。~~（已完成：本地 `content_sha256` 缓存与去重已落地）
2. 给 `company_resolver` 增加 LLM 消歧分支。
3. 增加对象存储（如 S3/OSS）落盘策略，作为本地存储的扩展。
