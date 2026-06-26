# Report Finder Service - 后端启动 & API 接口文档

## 一、项目路径

```
/home/maoyd/siq-research-engine/services/report-finder
```

---

## 二、后端启动

```bash
cd /home/maoyd/siq-research-engine/services/report-finder
uv sync --extra dev
```

### 方式 1：前台启动（开发调试）

```bash
uv run uvicorn report_finder_service.app:app --host 127.0.0.1 --port 8000
```

### 方式 2：前台启动（供队友/局域网访问）

```bash
uv run uvicorn report_finder_service.app:app --host 0.0.0.0 --port 8000
```

### 方式 3：后台常驻（推荐）

```bash
nohup uv run uvicorn report_finder_service.app:app --host 0.0.0.0 --port 8000 > uvicorn.log 2>&1 &
```

- 查看日志：`tail -f uvicorn.log`
- 查看进程：`ps aux | grep uvicorn`
- 停止服务：`pkill -f "uvicorn report_finder_service.app:app"`

---

## 三、接口文档

启动后访问：
- **Swagger UI**：`http://<IP>:8000/docs`
- **OpenAPI JSON**：`http://<IP>:8000/openapi.json`
- **前端页面**：`http://<IP>:8000/`

---

## 四、API 接口总览

| 方法 | 路径 | 功能 |
|------|------|------|
| `GET` | `/` | **前端页面**（财报查询下载网页） |
| `GET` | `/health` | 健康检查 |
| `GET` | `/v1/sources` | 查看支持的数据源 |
| `POST` | `/v1/resolve` | 公司名称/代码 → 标准证券实体 |
| `POST` | `/v1/reports/latest` | 查询最新年报（仅元数据） |
| `POST` | `/v1/reports/recent` | 批量查询近期财报列表 |
| `POST` | `/v1/reports/latest/download` | **下载**最新年报 PDF（文件流） |
| `POST` | `/v1/reports/download` | **下载**指定 URL 的 PDF（文件流） |
| `POST` | `/v1/reports/batch-download` | **批量下载**多个 URL（返回 JSON） |
| `POST` | `/v1/reports/select-download` | **选择下载**到本地（返回 JSON） |
| `POST` | `/v1/reports/direct-download` | 给定完整元数据直接下载（文件流） |

---

## 五、接口详解

### 1. 健康检查

```bash
curl http://<IP>:8000/health
```

```json
{"status": "ok", "service": "report-finder-service"}
```

---

### 2. 解析公司

```bash
curl -s http://<IP>:8000/v1/resolve \
  -H 'Content-Type: application/json' \
  -d '{"company_name":"宁德时代"}'
```

| 字段 | 必填 | 说明 |
|------|------|------|
| `company_name` | ✅ | 公司名称/简称/俗称 |
| `ticker` | ❌ | 股票代码，如 `000001`、`300750` |
| `exchange_hint` | ❌ | 交易所提示：`SZSE`（深市）或 `SSE`（沪市） |

**返回**：标准证券实体（名称、代码、交易所等）。

---

### 3. 查询最新年报（仅元数据）

```bash
curl -s http://<IP>:8000/v1/reports/latest \
  -H 'Content-Type: application/json' \
  -d '{"company_name":"宁德时代","target":"annual_report"}'
```

| 字段 | 必填 | 说明 |
|------|------|------|
| `company_name` | ✅ | 公司名称 |
| `ticker` | ❌ | 股票代码 |
| `exchange_hint` | ❌ | 交易所提示 |
| `target` | ❌ | `annual_report`（默认）或 `financial_report` |

**返回**：最新一份年报的元数据（标题、披露日期、PDF 直链等）。

---

### 4. 批量查询近期财报列表

```bash
curl -s http://<IP>:8000/v1/reports/recent \
  -H 'Content-Type: application/json' \
  -d '{"company_name":"宁德时代","target":"financial_report","report_year":2025,"limit":20}'
```

| 字段 | 必填 | 说明 |
|------|------|------|
| `company_name` / `ticker` | ⚠️ 至少一个 | 公司名或股票代码 |
| `exchange_hint` | ❌ | 交易所提示 |
| `target` | ❌ | `financial_report`（默认，含半年报/季报）或 `annual_report` |
| `report_year` | ❌ | **指定年份**，如 `2025`。不指定则只返回最近一年的报告 |
| `include_earnings` | ❌ | 是否包含业绩快报，默认 `false` |
| `limit` | ❌ | 返回条数上限，默认 `20` |

> ⚠️ **关键点**：`report_year` 不指定时，只返回**最近一年**的报告。当前 2026 年 5 月，最近一年是 2026 年，目前只披露了 2026 年一季度报告。要看到完整年报，需指定 `report_year: 2025`。

---

### 5. 下载最新年报 PDF（文件流）

```bash
curl -s -o 年报.pdf http://<IP>:8000/v1/reports/latest/download \
  -H 'Content-Type: application/json' \
  -d '{"company_name":"宁德时代","target":"annual_report"}'
```

**返回**：`Content-Type: application/pdf`，PDF 二进制流。

**Response Headers**：
| Header | 说明 |
|--------|------|
| `X-Company-Name` | 公司名称（URL 编码） |
| `X-Ticker` | 股票代码 |
| `X-Report-End` | 报告期 |
| `X-Report-Title` | 报告标题（URL 编码） |
| `X-Published-At` | 披露日期 |
| `X-Document-Url` | PDF 直链 |
| `X-Cache-Hit` | 是否命中缓存 |

**文件保存位置**：
```
downloads/宁德时代/年报/cninfo_300750_2025-12-31_annual.pdf
```

---

### 6. 单文件下载（已知 URL）

```bash
curl -s -o report.pdf http://<IP>:8000/v1/reports/download \
  -H 'Content-Type: application/json' \
  -d '{"company_name":"宁德时代","document_url":"https://static.cninfo.com.cn/..."}'
```

| 字段 | 必填 | 说明 |
|------|------|------|
| `company_name` | ✅ | 公司名称 |
| `document_url` | ✅ | PDF 直链 |
| `title` | ❌ | 报告标题 |

**Response Headers**：
| Header | 说明 |
|--------|------|
| `X-Company-Name` | 公司名称（URL 编码） |
| `X-Document-Url` | PDF 直链 |
| `X-Cache-Hit` | 是否命中缓存 |

---

### 7. 批量下载（多个 URL）

```bash
curl -s http://<IP>:8000/v1/reports/batch-download \
  -H 'Content-Type: application/json' \
  -d '{
    "default_company_name":"宁德时代",
    "items":[
      {"document_url":"https://static.cninfo.com.cn/...","company_name":"宁德时代","title":"2025年报"},
      {"document_url":"https://static.cninfo.com.cn/...","company_name":"比亚迪","title":"2025年报"}
    ]
  }'
```

**返回**：JSON，按公司名分子目录存放到 `downloads/`。

```json
{
  "total": 2,
  "succeeded": 2,
  "failed": 0,
  "results": [
    {"document_url": "...", "company_name": "宁德时代", "file_name": "...", "size_bytes": 2043710, "success": true},
    {"document_url": "...", "company_name": "比亚迪", "file_name": "...", "size_bytes": 7533972, "success": true},
    {"document_url": "...", "company_name": "某失效公司", "file_name": "", "size_bytes": 0, "success": false, "error": "HTTP 404"}
  ],
  "checked_at": "2026-05-16T04:16:18.018021Z",
  "zip_file_name": ""
}
```

---

### 8. 选择下载（核心接口）

**功能**：传入公司名 + 报告类型列表 → 自动查询 → 筛选 → **下载到本地 `downloads/`** → 返回 JSON 确认。

```bash
curl -s http://<IP>:8000/v1/reports/select-download \
  -H 'Content-Type: application/json' \
  -d '{
    "company_name": "宁德时代",
    "report_types": ["annual", "semiannual"]
  }'
```

| 字段 | 必填 | 说明 |
|------|------|------|
| `company_name` | ⚠️ | 公司名称（`company_name` 和 `ticker` 至少一个） |
| `ticker` | ⚠️ | 股票代码 |
| `exchange_hint` | ❌ | `SZSE` / `SSE` |
| `report_types` | ❌ | 报告类型数组，如 `["annual", "semiannual"]`。不传则返回空（建议必传） |
| `report_year` | ❌ | 指定年份，如 `2025` |

**支持的 `report_types`**：
- `"annual"` — 年度报告
- `"semiannual"` — 半年度报告
- `"q1"` — 一季度报告
- `"q3"` — 三季度报告

**返回**：
```json
{
  "company_name": "宁德时代",
  "ticker": "300750",
  "total": 2,
  "succeeded": 2,
  "failed": 0,
  "files": [
    {
      "title": "2025年年度报告",
      "report_type": "annual",
      "report_end": "2025-12-31",
      "published_at": "2026-03-09",
      "document_url": "https://static.cninfo.com.cn/...",
      "file_name": "cninfo_300750_2025-12-31_annual.pdf",
      "saved_path": "/home/maoyd/siq-research-engine/data/report-finder/downloads/宁德时代/年报/cninfo_300750_2025-12-31_annual.pdf",
      "size_bytes": 2043710,
      "cache_hit": false
    },
    {
      "title": "2025年半年度报告",
      "report_type": "semiannual",
      "report_end": "2025-06-30",
      "published_at": "2025-07-30",
      "file_name": "cninfo_300750_2025-06-30_semiannual.pdf",
      "saved_path": "/home/maoyd/siq-research-engine/data/report-finder/downloads/宁德时代/财报/cninfo_300750_2025-06-30_semiannual.pdf",
      "size_bytes": 1590151,
      "cache_hit": false
    }
  ],
  "download_dir": "/home/maoyd/siq-research-engine/data/report-finder/downloads",
  "checked_at": "2026-05-16T04:16:18.018021Z"
}
```

---

### 9. 直接下载（已知完整元数据）

**功能**：传入完整的报告元数据，直接下载 PDF（文件流）。适用于前端已经拿到元数据的场景。

```bash
curl -s -o report.pdf http://<IP>:8000/v1/reports/direct-download \
  -H 'Content-Type: application/json' \
  -d '{
    "company_name": "宁德时代",
    "document_url": "https://static.cninfo.com.cn/...",
    "landing_url": "",
    "source_name": "cninfo",
    "report_type": "annual",
    "report_end": "2025-12-31",
    "published_at": "2026-03-09"
  }'
```

| 字段 | 必填 | 说明 |
|------|------|------|
| `company_name` | ✅ | 公司名称 |
| `document_url` | ✅ | PDF 直链 |
| `landing_url` | ❌ | 公告详情页链接 |
| `source_name` | ❌ | 数据源名称，如 `cninfo` |
| `report_type` | ❌ | 报告类型：`annual`/`semiannual`/`q1`/`q3` |
| `report_end` | ❌ | 报告截止日期，如 `2025-12-31` |
| `published_at` | ❌ | 披露日期，如 `2026-03-09` |

**Response Headers**：
| Header | 说明 |
|--------|------|
| `X-Company-Name` | 公司名称（URL 编码） |
| `X-Report-Type` | 报告类型 |
| `X-Report-End` | 报告期 |
| `X-Published-At` | 披露日期 |
| `X-Document-Url` | PDF 直链 |
| `X-Cache-Hit` | 是否命中缓存 |

---

## 六、文件存放目录结构

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
├── 比亚迪/
│   ├── 年报/
│   │   └── cninfo_002594_2025-12-31_annual.pdf
│   └── 财报/
│       └── ...
└── 华数传媒/
    ├── 年报/
    │   └── cninfo_000156_2025-12-31_annual.pdf
    └── 财报/
        └── ...
```

| 报告类型 | 子目录 |
|---------|--------|
| `annual`（年度报告） | `年报/` |
| `semiannual`（半年度报告） | `财报/` |
| `q1`（一季度报告） | `财报/` |
| `q3`（三季度报告） | `财报/` |

---

## 七、Python 调用示例

```python
import requests

BASE = "http://127.0.0.1:8000"

# 1. 查询并下载年报+半年报
resp = requests.post(f"{BASE}/v1/reports/select-download", json={
    "company_name": "宁德时代",
    "report_types": ["annual", "semiannual"],
})

data = resp.json()
print(f"公司: {data['company_name']}, 成功: {data['succeeded']}/{data['total']}")
for f in data['files']:
    print(f"  [{f['report_type']}] {f['saved_path']} ({f['size_bytes']} bytes)")

# 2. 按股票代码下载
resp = requests.post(f"{BASE}/v1/reports/select-download", json={
    "ticker": "300750",
    "exchange_hint": "SZSE",
    "report_types": ["annual"],
})
```

---

## 八、注意事项

1. **巨潮接口偶发超时**：解析阶段如果遇到 `504 Gateway Time-out`，cninfo adapter 会自动重试 3 次（退避 2s→4s→8s）。
2. **年份选择**：`/v1/reports/recent` 默认只返回最近一年的报告。当前 2026 年 5 月，最近一年是 2026 年，目前只披露了 2026 年一季度报告。要看到完整年报，需指定 `report_year: 2025`。
3. **文件缓存**：同一份 PDF（基于 `content_sha256`）重复下载会命中缓存，不会重复落盘。
4. **中文 Header 已 URL 编码**：读取 `X-Company-Name`、`X-Report-Title` 时需要 `urllib.parse.unquote` 解码。
5. **前端页面**：浏览器访问 `http://<IP>:8000/` 即可使用网页版查询下载。
