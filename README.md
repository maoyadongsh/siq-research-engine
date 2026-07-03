# SIQ Research Engine

SIQ Research Engine 是一个本地化、可审计的智能研究工作台。它把官方公告获取、财报和通用文档解析、结构化证据包、PostgreSQL / Milvus 入库、多智能体报告生成、事实核查、持续跟踪和法务合规串成一条可复核的研究生产线。

它的目标不是生成一段“像研报”的文字，而是让每个数字、判断、风险提示和引用都能回到官方披露文件、PDF 页码、表格单元格、XBRL tag、Markdown 行、数据库记录或法规条款。

## 覆盖范围

| 范围 | 主链路 | 说明 |
| --- | --- | --- |
| A 股 | `apps/pdf-parser` -> `db/imports` | 巨潮公告下载、PDF/MinerU 解析、财务抽取、勾稽校验、`pdf2md` 入库 |
| 港股 | `services/market-report-finder` -> `services/market-report-rules` | HKEXnews 官方下载、证据包、规则服务、入库计划和前端入口 |
| 美股 | `services/market-report-finder` -> `services/market-report-rules` | SEC EDGAR、submissions、HTML / iXBRL / XBRL 证据链 |
| 欧股 | `services/market-report-finder` -> `services/market-report-rules` | ESEF / PDF 官方披露、市场隔离证据包 |
| 日股 | `services/market-report-finder` -> `services/market-report-rules` | EDINET 官方披露和结构化证据定位 |
| 韩股 | `services/market-report-finder` -> `services/market-report-rules` | DART / OpenDART 官方披露和结构化证据定位 |
| 通用文档 | `apps/document-parser` | PDF、HTML、Office、文本、URL 和 MinerU 产物统一归一 |

## 系统架构

```text
官方披露源 / 本地文件 / URL / MinerU 结果
  -> 下载与主体解析
  -> PDF / HTML / iXBRL / ESEF / DART / EDINET / 通用文档解析
  -> 质量报告、source map、财务抽取、证据包、load plan
  -> Wiki / PostgreSQL / Milvus / 本地文件系统
  -> API 聚合后端
  -> Web 工作台 + Hermes 智能体
```

可以把系统理解为四层：

1. 控制面：`apps/web` + `apps/api`，负责交互、鉴权、任务编排、报告读取和 Agent 对话。
2. 数据获取面：`services/market-report-finder`、`apps/pdf-parser`、`apps/document-parser`，负责把外部材料变成可用产物。
3. 证据与规则面：`services/market-report-rules`、`db/imports`、`agents/hermes`，负责抽取、校验、入库和解释边界。
4. 基础设施面：`data/`、PostgreSQL、Redis、Milvus、模型服务和运行脚本，负责持久化与推理能力。

系统原则是“证据先行、规则可审、模型受控”。模型可以组织材料，但不能凭空制造财务数字、页码、表格、法规或数据库记录。

## 技术栈

| 层 | 选型 | 作用 |
| --- | --- | --- |
| 前端 | React 19、React Router 7、Vite 8、TypeScript 6 | Web 工作台和交互式研究界面 |
| UI | Tailwind CSS 4、Radix UI、lucide-react、class-variance-authority | 组件、图标、样式与交互基础 |
| 可视化 | Recharts、DOMPurify | 图表和安全富文本渲染 |
| 前端测试 | Playwright、ESLint | E2E、lint 和构建校验 |
| API 控制面 | FastAPI、Uvicorn、SQLModel、SSE Starlette | 统一鉴权、工作流、Agent 和流式输出 |
| Python 依赖 | httpx、pyjwt、asyncpg、psycopg、redis、python-multipart | HTTP、认证、数据库和缓存 |
| A 股解析 | Flask、pypdf、MinerU bridge、VLM 上游 | PDF 解析、质量报告、财务抽取 |
| 通用文档解析 | Flask、解析 provider、MinerU 导入 | 文档归一、source map、Schema 抽取 |
| 市场下载与规则 | FastAPI、Pydantic、HTTPX、Uvicorn | 官方披露下载、解析后规则和 load plan |
| 数据存储 | SQLite、PostgreSQL、Redis、Milvus、文件系统 Wiki | 任务状态、事实层、缓存、语义层和证据层 |
| 模型与推理 | MinerU、vLLM、Embedding / Reranker、Hermes gateway | OCR / 解析、生成、检索与智能体运行 |
| 运维编排 | Docker Compose、systemd user units、supervisor | 本地服务编排和开机管理 |
| 包管理 | `uv`、`npm` | Python 和前端依赖管理 |

## 服务总览

| 服务 | 路径 | 默认端口 | 技术栈 | 作用 |
| --- | --- | ---: | --- | --- |
| Web 工作台 | `apps/web` | `15173` | React / Vite / TS | 主入口，承载下载、解析、报告、对话和设置 |
| API 聚合后端 | `apps/api` | `18081` | FastAPI / SQLModel / SSE | 鉴权、工作流、报告、Agent 代理、PDF / 文档代理 |
| PDF 解析服务 | `apps/pdf-parser` | `15000` | Flask / pypdf / MinerU bridge | A 股财报解析、财务抽取、质量报告和 PDF 溯源 |
| 通用文档解析服务 | `apps/document-parser` | `15010` | Flask / 解析 provider | 任意文档归一、source map、表格关系、Schema 抽取 |
| 官方公告下载服务 | `services/market-report-finder` | `18000` | FastAPI / HTTPX / Pydantic | CN / HK / US / EU / JP / KR 官方披露搜索与下载 |
| 备用下载实例 | `services/market-report-finder` | `18010` | FastAPI / HTTPX / Pydantic | 可选并行联调实例 |
| 解析后规则服务 | `services/market-report-rules` | `18020` | FastAPI / Pydantic / HTTPX | 多市场抽取、校验、证据定位和 load plan |
| Hermes 助手 | `agents/hermes` | `18642` | Hermes gateway | 通用问答 |
| Hermes 核查 | `agents/hermes` | `18649` | Hermes gateway | 事实核查 |
| Hermes 跟踪 | `agents/hermes` | `18650` | Hermes gateway | 持续跟踪 |
| Hermes 分析 | `agents/hermes` | `18651` | Hermes gateway | 年度分析 |
| Hermes 法务 | `agents/hermes` | `18652` | Hermes gateway | 法务合规 |
| Milvus 入库控制台 | `scripts/vector-index/milvus-ingestion` | `7862` | Gradio / Python | 可选向量入库工具 |

## 关键数据合同

| 产物 | 位置 | 作用 |
| --- | --- | --- |
| `document_full.json` | `data/pdf-parser/results/<task_id>/`、`data/document-parser/results/<task_id>/` | 统一文档证据合同 |
| `quality_report.json` | 同上 | 质量门禁和异常说明 |
| `source_map.json` | 同上 | 文本块、表格、页码和原始来源映射 |
| `financial_data.json` / `financial_checks.json` | `data/pdf-parser/results/<task_id>/` | A 股财务抽取和勾稽校验 |
| market evidence package | `data/wiki/<market>_reports/...` | 多市场归档、入库和回放单元 |
| 下载文件 | `data/market-report-finder/downloads/...` | 官方原始披露文件和元数据 |
| Hermes 运行态 | `data/hermes/home/...` | profile、会话、响应和状态 |
| API 本地状态 | `data/backend/...` | 用户、任务、设置和运行状态 |

典型目录约定：

```text
data/
  wiki/
  market-report-finder/downloads/
  pdf-parser/
  document-parser/
  backend/
  hermes/home/
```

## 典型链路

1. 输入公司名、股票代码、CIK、EDINET code、DART corp code、文件或 URL。
2. `market-report-finder` 连接官方来源并下载原始披露文件。
3. `pdf-parser` 或 `document-parser` 生成 `document_full.json`、`source_map.json`、`quality_report.json` 等产物。
4. `market-report-rules` 或 A 股财务抽取逻辑生成结构化数据、校验结果和入库计划。
5. `db/imports`、Wiki、PostgreSQL 和 Milvus 接住事实层和语义层。
6. `apps/api` 向 `apps/web` 和 Hermes 暴露统一的任务、报告和对话入口。

## 仓库布局

| 路径 | 职责 |
| --- | --- |
| `apps/web` | React/Vite 工作台 |
| `apps/api` | FastAPI 聚合后端 |
| `apps/pdf-parser` | A 股财报解析服务 |
| `apps/document-parser` | 通用文档解析服务 |
| `services/market-report-finder` | 官方公告下载服务 |
| `services/market-report-rules` | 多市场规则服务 |
| `packages/market-contracts` | 多市场证据包共享契约 |
| `agents/hermes` | Hermes profiles、角色规则和共享脚本 |
| `db/imports` | PostgreSQL 入库和财务查询工具 |
| `scripts` | 运维、评测、批处理和向量入库脚本 |
| `infra` | Docker、模型服务和环境样例 |
| `eval_datasets` | 评测与回归语料 |
| `datasets` | 可版本化小型数据集、fixtures 和样本 |
| `data` | 旧版兼容运行态数据目录，默认不提交业务内容 |
| `var` | 新增本地运行态建议目录，默认被 Git 忽略 |
| `artifacts` | 构建、测试、评测和批处理生成产物，默认被 Git 忽略 |

## 快速启动

```bash
cd /home/maoyd/siq-research-engine
cp infra/env/local.example infra/env/local.env
# edit infra/env/local.env and replace secrets before long-running local use
export SIQ_AUTH_SECRET_KEY="${SIQ_AUTH_SECRET_KEY:-$(openssl rand -hex 32)}"
export SIQ_SOURCE_TOKEN_SECRET="${SIQ_SOURCE_TOKEN_SECRET:-$(openssl rand -hex 32)}"
./start_all.sh
```

`start_all.sh` 默认读取 `infra/env/local.env`。兼容期内，如果该文件不存在且未显式设置 `SIQ_ENV_FILE`，脚本仍会尝试读取旧路径 `env/backend.env`；前端旧配置 `env/frontend-dev.env` 也会被兼容读取。

启动后访问：

```text
http://localhost:15173
```

`start_all.sh` 默认会启动：

- 统一公告下载服务 `:18000`
- API 聚合后端 `:18081`
- PDF 解析服务 `:15000`
- 通用文档解析服务 `:15010`
- Web 工作台 `:15173`
- Hermes 网关 `:18642`, `:18649`, `:18650`, `:18651`, `:18652`

可选服务通过环境变量控制：

```bash
SIQ_START_HERMES_GATEWAYS=0 ./start_all.sh
SIQ_START_MARKET_REPORT_RULES=1 ./start_all.sh
SIQ_START_MARKET_REPORT_FINDER=1 ./start_all.sh
SIQ_START_VECTOR_INGEST=1 ./start_all.sh
```

## Docker Compose

容器版以 `infra/docker/docker-compose.yml` 为准，根目录 `docker-compose.yml` 只是兼容包装。

```bash
docker compose -f infra/docker/docker-compose.yml \
  --env-file infra/env/local.env \
  up
```

需要可选服务时可加 profile：

```bash
docker compose -f infra/docker/docker-compose.yml \
  --env-file infra/env/local.env \
  --profile external-services \
  --profile monitoring \
  up
```

默认 Compose 服务图包含 Web、API、report-finder、PDF parser、document-parser、PostgreSQL 和 Redis；`external-services` profile 额外启动备用 market-report-finder 与 market-report-rules，`monitoring` profile 启动 Grafana。Hermes 网关当前依赖本机 Hermes editable venv，仍通过 `start_all.sh` 或 `scripts/hermes/run_gateway.sh` 启动。

## 手动启动

API：

```bash
cd /home/maoyd/siq-research-engine/apps/api
export SIQ_AUTH_SECRET_KEY="$(openssl rand -hex 32)"
export SIQ_SOURCE_TOKEN_SECRET="$(openssl rand -hex 32)"
./start.sh
```

Web：

```bash
cd /home/maoyd/siq-research-engine/apps/web
npm install
npm run dev -- --host 0.0.0.0 --port 15173
```

PDF 解析：

```bash
cd /home/maoyd/siq-research-engine/apps/pdf-parser
./run.sh
```

通用文档解析：

```bash
cd /home/maoyd/siq-research-engine/apps/document-parser
./run.sh
```

官方公告下载：

```bash
cd /home/maoyd/siq-research-engine/services/market-report-finder
uv sync
MARKET_REPORT_DOWNLOAD_DIR=/home/maoyd/siq-research-engine/data/market-report-finder/downloads \
uv run python -m uvicorn market_report_finder_service.app:app --host 127.0.0.1 --port 18000
```

解析后规则：

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
curl -s http://localhost:15010/api/health
curl -s http://localhost:18000/health
curl -s http://localhost:18020/healthz
curl -s http://localhost:18642/health
curl -s http://localhost:18649/health
curl -s http://localhost:18650/health
curl -s http://localhost:18651/health
curl -s http://localhost:18652/health
```

## 关键环境变量

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `SIQ_PROJECT_ROOT` | 仓库根目录 | 项目根路径 |
| `SIQ_DATA_ROOT` | `data` | 兼容期运行态数据根目录；后续可切到 `var` |
| `SIQ_RUNTIME_ROOT` | `var` | 新增本地运行态建议根目录 |
| `SIQ_ARTIFACTS_ROOT` | `artifacts` | 构建、测试、评测和批处理输出根目录 |
| `SIQ_DATASETS_ROOT` | `datasets` | 可版本化小型 fixtures 和稳定样本根目录 |
| `SIQ_WIKI_ROOT` | `$SIQ_DATA_ROOT/wiki` | 公司 Wiki、报告和 evidence package |
| `SIQ_BACKEND_PORT` | `18081` | API 聚合后端端口 |
| `SIQ_FRONTEND_PORT` | `15173` | Web 工作台端口 |
| `SIQ_PDF2MD_PORT` | `15000` | PDF 解析服务端口 |
| `SIQ_DOCUMENT_PARSER_PORT` | `15010` | 通用文档解析服务端口 |
| `SIQ_REPORT_FINDER_PORT` | `18000` | 统一公告搜索下载端口 |
| `SIQ_MARKET_REPORT_FINDER_PORT` | `18010` | 备用下载实例端口 |
| `SIQ_MARKET_REPORT_RULES_PORT` | `18020` | 多市场规则服务端口 |
| `SIQ_REPORT_DOWNLOADS_ROOT` | `$SIQ_DATA_ROOT/market-report-finder/downloads` | 已下载官方披露文件目录 |
| `SIQ_PDF2MD_DATA_DIR` | `$SIQ_DATA_ROOT/pdf-parser` | PDF 解析运行态目录 |
| `SIQ_DOCUMENT_PARSE_DATA_DIR` | `$SIQ_DATA_ROOT/document-parser` | 通用文档解析运行态目录 |
| `SIQ_HERMES_HOME` | `$SIQ_DATA_ROOT/hermes/home` | Hermes 运行态根目录 |
| `SIQ_AUTH_SECRET_KEY` | 无 | JWT/session 密钥，必须设置，至少 32 字符 |
| `SIQ_SOURCE_TOKEN_SECRET` | fallback 到 `SIQ_AUTH_SECRET_KEY` | `/api/source*` 短期签名访问 token 密钥，建议使用独立至少 32 字符密钥；设置后新 token 用该 source secret 签发，默认不再验证旧 auth secret token |
| `SIQ_SOURCE_ACCEPT_LEGACY_AUTH_SECRET` | `0` | 配置独立 source secret 后是否继续验证旧 auth secret 签发的 source token；短期迁移需要时显式设为 `1` |
| `SEC_USER_AGENT` | 服务默认值 | SEC EDGAR 合规请求头 |
| `DART_API_KEY` | 空 | 韩国 DART / OpenDART API key |
| `EDINET_API_KEY` | 空 | 日本 EDINET API key |
| `PDF2MD_ACCESS_TOKEN` | 无 | PDF 解析服务访问令牌 |
| `MINERU_API_URL` | `http://127.0.0.1:8003` | MinerU API 地址 |
| `VLM_API_URL` | `http://127.0.0.1:8002` | VLM 地址 |
| `DATABASE_URL` | 无 | PostgreSQL 连接串 |
| `REDIS_URL` | 无 | Redis 连接串 |

## 合并前基础门禁

GitHub Actions 的 `CI` workflow 是 P0 稳定子集：它在 GitHub runner 上执行脚本语法检查、API 聚焦测试、Web unit 和 frontend check，避免依赖本机服务或过重的解析链路。PDF parser、document-parser、market-report-finder、market-report-rules 和 `packages/market-contracts` 的扩展覆盖仍以本地 `scripts/check_all.sh` 或对应目录测试为准，按变更范围在合并前补跑。

一键执行：

```bash
cd /home/maoyd/siq-research-engine
scripts/check_all.sh
```

分步定位：

```bash
cd /home/maoyd/siq-research-engine/apps/api
uv run python -m pytest tests

cd /home/maoyd/siq-research-engine/apps/pdf-parser
python3 -m pytest tests

cd /home/maoyd/siq-research-engine/apps/document-parser
python3 -m pytest tests

cd /home/maoyd/siq-research-engine/services/market-report-finder
uv sync --extra dev
uv run python -m pytest tests

cd /home/maoyd/siq-research-engine/services/market-report-rules
uv run --extra dev pytest

cd /home/maoyd/siq-research-engine/apps/web
npm run test:unit
npm run check:frontend
```

红灯 owner 收口门禁：

```bash
cd /home/maoyd/siq-research-engine
scripts/check_owner_migration.sh
```

该脚本用于当前架构优化收口的聚焦验证，不替代上方基础门禁。

一键脚本和 shell 入口检查：

```bash
cd /home/maoyd/siq-research-engine
bash -n scripts/check_all.sh
bash -n scripts/check_owner_migration.sh
bash -n scripts/check_async_db_audit.sh
bash -n start_all.sh
bash -n apps/api/start.sh
bash -n apps/pdf-parser/run.sh
bash -n apps/document-parser/run.sh
```

## 文档入口

| 文档 | 用途 |
| --- | --- |
| `apps/web/README.md` | Web 工作台功能、路由、代理和前端开发说明 |
| `apps/api/README.md` | API 聚合后端、路由分组、鉴权、Agent 代理、市场 / 文档工作流说明 |
| `apps/pdf-parser/README.md` | 专业财报 PDF 解析、质量报告、财务抽取和溯源 API |
| `apps/document-parser/README.md` | 通用文档解析、artifact 合同、Schema 抽取和 API |
| `services/market-report-finder/README.md` | CN / HK / US / EU / JP / KR 官方公告搜索下载服务 |
| `services/market-report-rules/README.md` | 多市场解析后规则、校验、证据定位和入库计划 |
| `agents/hermes/README.md` | Hermes profiles 与多 Agent 协作总览 |
| `db/imports/README.md` | PostgreSQL 入库和财务查询工具 |
| `scripts/README.md` | 运维、评测和向量入库脚本 |
| `infra/model-services/README.md` | 本地模型服务和 systemd 用户单元 |
| `data/README.md` | 旧版兼容运行态数据目录和边界 |
| `var/README.md` | 新增本地运行态建议目录 |
| `artifacts/README.md` | 构建、测试、评测和批处理生成产物目录 |
| `datasets/README.md` | 可版本化数据集、fixtures 和小型样本目录 |
| `eval_datasets/README.md` | 评测语料和回归集说明 |

## 数据安全与运行态边界

`data/` 仍作为现有服务的兼容运行态目录，保存上传文件、下载披露文件、解析结果、SQLite、聊天附件、日志、缓存、PostgreSQL / Milvus / MinIO 数据和 Hermes 状态。新增运行态默认建议落到 `var/`，构建/测试/评测输出落到 `artifacts/`，可版本化小型 fixtures 和稳定样本落到 `datasets/`。

除 README、`.gitkeep` 或明确的小型 fixtures 外，不提交运行态数据、下载披露文件、解析产物、数据库文件、缓存、日志和本地模型运行时。

`.env`、API key、数据库口令、模型服务密钥、用户会话、聊天附件和未公开研究材料不应写入 README、报告、日志或提交记录。`SIQ_AUTH_SECRET_KEY` 和 `SIQ_SOURCE_TOKEN_SECRET` 都应使用至少 32 字符随机密钥；`SIQ_SOURCE_TOKEN_SECRET` 建议独立于 auth secret 配置，未设置时 source token 会 fallback 到 `SIQ_AUTH_SECRET_KEY`，设置后新 token 使用 source secret，默认不再验证旧 auth secret token；如短期迁移必须接受旧 token，可显式设置 `SIQ_SOURCE_ACCEPT_LEGACY_AUTH_SECRET=1`。涉及财务和法务结论的产物应保留证据来源、生成时间、模型 / 规则版本和人工复核状态。
