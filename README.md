# SIQ Research Engine

SIQ Research Engine 是一个面向 A 股上市公司研究的本地化智能研究工作台。系统把官方公告获取、PDF 解析、结构化证据沉淀、财务分析报告、事实核查、持续跟踪、法务合规和多智能体问答整合为一条可复核的研究生产线。

它的核心目标不是“生成一段看起来像研报的文字”，而是让每个数字、结论和风险提示都能回到公开披露文件、PDF 页码、表格索引、结构化指标或法规依据。

## 核心亮点

| 方向 | 能力 | 技术优势 |
| --- | --- | --- |
| 权威数据入口 | 从巨潮资讯、SEC EDGAR、HKEXnews 等官方披露源解析公司主体并下载定期报告 | 避免从不明文件或模型记忆开始分析，降低源头错误 |
| PDF 结构化解析 | 将年报 PDF 转为 Markdown、表格、页码、质量报告和财务抽取产物 | 保留页面、表格、行号和人工修正链路，便于审计 |
| 证据层沉淀 | 将 `document_full.json`、财务表、引用、页面和质量告警写入 Wiki / PostgreSQL / 向量库 | 支撑多 Agent 共享同一事实底座 |
| 多智能体分工 | 通用问答、深度分析、事实核查、持续跟踪、法务合规各自独立 | 生成、复核、监控、合规判断解耦，减少单 Agent 职责膨胀 |
| 金融质量门禁 | 禁止无依据评分、目标价和交易指令，强调事实、公式、证据和风险链条 | 更贴近投研审稿和合规边界 |
| 私有化运行 | 前端、API、解析、下载、Hermes 网关、模型服务均可本地部署 | 适合处理未公开研究材料、内部报告和企业合规知识库 |

## 系统链路

```text
公司名 / 股票代码
  -> 官方公告搜索与下载
  -> PDF 解析、页码定位、表格抽取、质量评估
  -> Wiki / PostgreSQL / Milvus 证据层
  -> 分析报告、事实核查、持续跟踪、法务意见
  -> Web 工作台展示、SSE 对话、溯源链接与人工复核
```

这条链路强调“证据先行、模型受控、产物可复查”。模型负责解释、组织和推理；数字、来源、页码、表格和法规引用必须来自可定位的数据层。

## 仓库布局

| 路径 | 职责 |
| --- | --- |
| `apps/web` | React / Vite 研究工作台，承载搜索下载、PDF 解析、报告浏览、Agent 对话、设置和用户管理 |
| `apps/api` | FastAPI 聚合后端，负责鉴权、Wiki 报告、下载管理、PDF 溯源、工作流导入、系统状态和 Agent 代理 |
| `apps/pdf-parser` | Flask PDF 解析服务，负责上传任务、MinerU/VLM 调用、质量报告、财务抽取和溯源 API |
| `services/market-report-finder` | CN/HK/US 统一下载入口，内部按 `markets/cn`、`markets/hk`、`markets/us` 拆分巨潮/HKEX/SEC 公司解析、报告检索和原始文件下载 |
| `services/market-report-rules` | 境外市场解析后规则服务，负责结构化抽取、校验、证据定位和入库计划 |
| `agents/hermes` | SIQ Hermes profiles 的源码说明、角色规则、配置和运行边界 |
| `db/imports` | `document_full.json` 入库 PostgreSQL 的工具和财务查询辅助入口 |
| `scripts` | 本地运维、维护和向量入库等脚本入口 |
| `infra` | Docker、模型服务、环境变量样例和本地基础设施说明 |
| `eval_datasets` | 财报智能分析评测语料 |
| `data` | 本地运行态数据根目录，默认不提交业务数据和模型缓存 |

## 服务与端口

| 服务 | 路径 | 默认端口 | 说明 |
| --- | --- | ---: | --- |
| Web 工作台 | `apps/web` | `15173` | 浏览器入口 |
| API 聚合后端 | `apps/api` | `18081` | 主业务 API、鉴权、Agent 代理 |
| PDF 解析服务 | `apps/pdf-parser` | `15000` | PDF 转结构化产物与证据溯源 |
| CN/HK/US 公告搜索下载 | `services/market-report-finder` | `18000` | 统一入口，内部按 CN/HK/US 市场模块检索和下载 |
| 备用市场下载实例 | `services/market-report-finder` | `18010` | 可选服务，用于联调或并行测试 |
| 美股/港股规则服务 | `services/market-report-rules` | `18020` | 可选服务，解析后抽取、校验和入库计划 |
| Hermes 通用助手 | `siq_assistant` | `18642` | 通用财报问答 |
| Hermes 事实核查 | `siq_factchecker` | `18649` | 生成报告复核 |
| Hermes 持续跟踪 | `siq_tracking` | `18650` | 事项、指标、预警和更新 |
| Hermes 智能分析 | `siq_analysis` | `18651` | 年度经营诊断报告 |
| Hermes 法务合规 | `siq_legal` | `18652` | 法规检索和意见书草拟 |
| Milvus 向量入库控制台 | `scripts/vector-index/milvus-ingestion` | `7862` | 可选 Gradio 入库工具 |

`services/market-report-finder` 默认可运行在 `8010`；仓库一键编排使用 `18000` 作为统一公告下载入口，备用实例端口为 `18010`。`services/market-report-rules` 原型默认可运行在 `8020`，一键编排的可选端口为 `18020`。

当前 A 股解析后规则尚未独立成服务，主要位于 `apps/pdf-parser/financial_extractor.py`、`apps/pdf-parser/app.py` 和 `db/imports`：PDF 解析服务生成 `financial_data.json`、`financial_checks.json`、`quality_report.json`、`document_full.json`，再由 `db/imports/import_document_full_to_postgres.py` 写入 PostgreSQL。

## 快速启动

```bash
cd /home/maoyd/siq-research-engine
export SIQ_AUTH_SECRET_KEY="$(openssl rand -hex 32)"
./start_all.sh
```

启动后打开：

```text
http://localhost:15173
```

`start_all.sh` 会启动公告搜索下载、API 后端、PDF 解析、Vite 前端和五个 Hermes 网关。若只想启动 Web/API/PDF/下载服务，可设置：

```bash
SIQ_START_HERMES_GATEWAYS=0 ./start_all.sh
```

Milvus 向量入库控制台是高权限数据管理工具，默认不随一键脚本启动。需要在 Web 工作台“向量入库”页面嵌入控制台时：

```bash
SIQ_START_VECTOR_INGEST=1 ./start_all.sh
```

美股/港股公告下载服务当前作为可选能力迁入仓库，默认不随一键脚本启动。需要联调 SEC/HKEX 下载链路时：

```bash
SIQ_START_MARKET_REPORT_FINDER=1 ./start_all.sh
```

需要联调美股/港股解析后规则和入库计划时：

```bash
SIQ_START_MARKET_REPORT_RULES=1 ./start_all.sh
```

## 手动启动

API 聚合后端：

```bash
cd /home/maoyd/siq-research-engine/apps/api
export SIQ_AUTH_SECRET_KEY="$(openssl rand -hex 32)"
./start.sh
```

Web 工作台：

```bash
cd /home/maoyd/siq-research-engine/apps/web
npm install
npm run dev -- --host 0.0.0.0 --port 15173
```

PDF 解析服务：

```bash
cd /home/maoyd/siq-research-engine/apps/pdf-parser
./run.sh
```

统一公告搜索下载服务：

```bash
cd /home/maoyd/siq-research-engine/services/market-report-finder
uv sync
MARKET_REPORT_DOWNLOAD_DIR=/home/maoyd/siq-research-engine/data/market-report-finder/downloads \
uv run python -m uvicorn market_report_finder_service.app:app --host 127.0.0.1 --port 18000
```

美股/港股公告搜索下载服务：

```bash
cd /home/maoyd/siq-research-engine/services/market-report-finder
uv sync
MARKET_REPORT_DOWNLOAD_DIR=/home/maoyd/siq-research-engine/data/market-report-finder/downloads \
SEC_USER_AGENT="SIQ Research your_email@example.com" \
uv run python -m uvicorn market_report_finder_service.app:app --host 127.0.0.1 --port 18010
```

美股/港股规则服务：

```bash
cd /home/maoyd/siq-research-engine/services/market-report-rules
uv sync
uv run python -m uvicorn market_report_rules_service.app:app --host 127.0.0.1 --port 18020
```

## 健康检查

```bash
curl -s http://localhost:15173
curl -s http://localhost:18081/health
curl -s http://localhost:15000/api/health
curl -s http://localhost:18000/health
curl -s http://localhost:18010/health
curl -s http://localhost:18020/healthz
curl -s http://localhost:18642/health
curl -s http://localhost:18651/health
curl -s http://localhost:18649/health
curl -s http://localhost:18650/health
curl -s http://localhost:18652/health
```

## 关键环境变量

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `SIQ_PROJECT_ROOT` | 仓库根目录 | 系统根路径 |
| `SIQ_DATA_ROOT` | `data` | 运行态数据根目录 |
| `SIQ_WIKI_ROOT` | `data/wiki` | 公司 Wiki、报告和产物目录 |
| `SIQ_BACKEND_PORT` | `18081` | API 聚合后端端口 |
| `SIQ_FRONTEND_PORT` | `15173` | Web 工作台端口 |
| `SIQ_PDF2MD_PORT` | `15000` | PDF 解析服务端口 |
| `SIQ_REPORT_FINDER_PORT` | `18000` | 公告搜索下载端口 |
| `SIQ_MARKET_REPORT_FINDER_PORT` | `18010` | 美股/港股公告搜索下载端口 |
| `SIQ_MARKET_REPORT_RULES_PORT` | `18020` | 美股/港股规则服务端口 |
| `SIQ_REPORT_DOWNLOADS_ROOT` | `data/market-report-finder/downloads` | 已下载报告目录 |
| `SIQ_MARKET_REPORT_DOWNLOADS_ROOT` | `data/market-report-finder/downloads` | CN/HK/US 原始报告下载目录 |
| `SIQ_PDF2MD_DATA_DIR` | `data/pdf-parser` | PDF 解析运行态目录 |
| `SIQ_HERMES_HOME` | `data/hermes/home` | Hermes 网关运行态根目录 |
| `SIQ_AUTH_SECRET_KEY` | 无 | API 鉴权密钥，必须设置 |
| `SIQ_START_MARKET_REPORT_FINDER` | `0` | 是否启动美股/港股公告下载服务 |
| `SIQ_START_MARKET_REPORT_RULES` | `0` | 是否启动美股/港股规则服务 |
| `SIQ_START_VECTOR_INGEST` | `0` | 是否启动 Milvus 向量入库控制台 |
| `SIQ_VECTOR_INGEST_PORT` | `7862` | 向量入库控制台端口 |

## 开发验证

API：

```bash
cd /home/maoyd/siq-research-engine/apps/api
uv run python -m pytest tests
```

PDF 解析：

```bash
cd /home/maoyd/siq-research-engine/apps/pdf-parser
python3 -m pytest tests
```

Web：

```bash
cd /home/maoyd/siq-research-engine/apps/web
npm run build
npm run lint
```

Shell 入口：

```bash
cd /home/maoyd/siq-research-engine
bash -n start_all.sh
bash -n apps/api/start.sh
bash -n apps/pdf-parser/run.sh
```

## 文档入口

| 文档 | 用途 |
| --- | --- |
| `apps/web/README.md` | Web 工作台功能、路由、代理和前端开发说明 |
| `apps/api/README.md` | API 聚合后端、路由分组、鉴权和 Agent 代理说明 |
| `apps/pdf-parser/README.md` | PDF 解析、质量报告、财务抽取和溯源 API 说明 |
| `services/market-report-finder/README.md` | CN/HK/US 官方公告搜索下载服务说明 |
| `services/market-report-rules/README.md` | 美股/港股解析后规则、校验和入库计划说明 |
| `agents/hermes/README.md` | Hermes profiles 总览 |
| `db/imports/README.md` | PostgreSQL 入库和财务查询工具说明 |
| `infra/model-services/README.md` | 本地模型服务启动脚本说明 |
| `eval_datasets/README.md` | 评测语料说明 |

## 数据安全与运行态边界

`data/` 用于本地运行态数据，包括上传 PDF、解析结果、SQLite、聊天附件、日志、缓存、PostgreSQL/Milvus 数据和 Hermes 状态。除 README、`.gitkeep` 和小型 manifest 外，不提交其中内容。

`.env`、API Key、数据库口令、模型服务密钥和用户会话数据不应写入 README、报告、日志或提交记录。涉及财务和法务结论的产物应保留证据来源、生成时间和人工复核状态。
