# 本地开发操作说明

本文记录 SIQ Research Engine 当前推荐的本地开发启动方式。所有命令默认在本仓库执行：

```text
/home/maoyd/siq-research-engine
```

## 一键启动

```bash
cd /home/maoyd/siq-research-engine
cp infra/env/local.example infra/env/local.env
# edit infra/env/local.env and replace secrets before long-running local use
# optional: set SIQ_LOCAL_STATE_ROOT to an external disk or user state dir
export SIQ_AUTH_SECRET_KEY="${SIQ_AUTH_SECRET_KEY:-$(openssl rand -hex 32)}"
./start_all.sh
```

`start_all.sh` 默认读取 `infra/env/local.env`。兼容期内，如果该文件不存在且未显式设置 `SIQ_ENV_FILE`，脚本仍会尝试读取旧路径 `env/backend.env`；前端旧配置 `env/frontend-dev.env` 也会被兼容读取，并在启动日志中提示迁移到 `infra/env/local.env`。

依赖安装默认使用可复现模式：Python 项目执行 `uv sync --frozen`，Web 执行 `npm ci`。只有显式设置 `SIQ_UPDATE_DEPS=1` 时，`start_all.sh` 才会允许 `uv sync` 或 `npm install` 更新依赖。

该脚本会按 SIQ 默认路径启动：

- CN/HK/US 统一公告搜索下载服务 `:18000`
- 备用市场下载服务 `:18010`（可选）
- 境外市场规则服务 `:18020`（可选）
- API 聚合后端 `:18081`
- PDF 解析服务 `:15000`
- 通用文档解析服务 `:15010`
- Web 前端 `:15173`
- Hermes gateway `:18642`, `:18649`, `:18650`, `:18651`, `:18652`

MinerU、VLM、本地 LLM 等本机模型推理服务可以共享，例如 `:8002`、`:8003`、`:8004`、`:8006`。

打开：

```text
http://localhost:15173
```

Docker Compose 默认服务图包含 Web、API、report-finder、PDF parser、document-parser、PostgreSQL 和 Redis；`external-services` profile 额外启动备用 market-report-finder 与 market-report-rules，`monitoring` profile 启动 Grafana。Hermes gateway 当前依赖本机 Hermes editable venv，仍通过 `start_all.sh` 或 `scripts/hermes/run_gateway.sh` 启动。

Compose 默认服务端口绑定 `127.0.0.1`，可通过 `SIQ_COMPOSE_BIND_HOST` 覆盖。Postgres 和 Redis 仍为本机开发发布 localhost 端口；生产部署应通过内网服务或反代访问，不直接公开 DB、Redis 或 parser 服务。

Compose 的 Postgres 容器会在首次初始化 `postgres_data` volume 时执行 `infra/docker/postgres-init/001_create_databases.sql`，创建 `siq_app`、`siq_document_parser`、`siq_us`、`siq_hk`、`siq_jp`、`siq_kr`、`siq_eu`。如果需要继续复用旧的 `data/postgres` bind mount，不要移动或删除目录；在 env 文件中设置 `SIQ_POSTGRES_DATA_VOLUME=../../data/postgres` 后再启动 compose。Docker 不会对已有 volume 或 bind mount 重放 init 脚本；需要保留数据时可用 `POSTGRES_ADMIN_DATABASE_URL=postgresql+psycopg://postgres:<password>@localhost:15432/postgres SIQ_APP_DATABASE_NAME=siq_app uv run python apps/api/scripts/create_database.py` 逐库补建，或在确认可清空数据后重建 volume。

## 路径治理

推荐新增本地状态根：

```bash
SIQ_LOCAL_STATE_ROOT=/home/maoyd/.local/state/siq-research-engine
SIQ_DATA_ROOT=${SIQ_LOCAL_STATE_ROOT}/data
SIQ_RUNTIME_ROOT=${SIQ_LOCAL_STATE_ROOT}/var
SIQ_ARTIFACTS_ROOT=${SIQ_LOCAL_STATE_ROOT}/artifacts
```

为了兼容现有本地数据，`infra/env/local.example` 仍默认让 `SIQ_LOCAL_STATE_ROOT` 指向仓库根，因此 `data/`、`var/`、`artifacts/` 的旧路径继续可用。切换到外部路径前，可先生成只读迁移计划：

```bash
python3 scripts/migration/plan_runtime_paths.py \
  --source-data-root data \
  --target-local-state-root /home/maoyd/.local/state/siq-research-engine
```

该脚本只输出计划，不创建、移动或删除任何文件。确认服务已经指向新路径前，请保留旧 `data/`。

## 手动启动

API：

```bash
cd /home/maoyd/siq-research-engine/apps/api
export SIQ_AUTH_SECRET_KEY="$(openssl rand -hex 32)"
./start.sh
```

Web：

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
MARKET_REPORT_DOWNLOAD_DIR=/home/maoyd/siq-research-engine/data/market-report-finder/downloads \
uv run uvicorn market_report_finder_service.app:app --host 127.0.0.1 --port 18000
```

备用市场下载服务：

```bash
cd /home/maoyd/siq-research-engine/services/market-report-finder
MARKET_REPORT_DOWNLOAD_DIR=/home/maoyd/siq-research-engine/data/market-report-finder/downloads \
uv run uvicorn market_report_finder_service.app:app --host 127.0.0.1 --port 18010
```

美股/港股规则服务：

```bash
cd /home/maoyd/siq-research-engine/services/market-report-rules
uv run uvicorn market_report_rules_service.app:app --host 127.0.0.1 --port 18020
```

## 常用环境变量

| 变量 | 用途 |
| --- | --- |
| `SIQ_AUTH_SECRET_KEY` | API 鉴权密钥，开发环境也必须设置 |
| `SIQ_AUTH_COOKIE_MODE` | 兼容式 HttpOnly cookie 会话开关；本地默认 `0`，公网部署建议设为 `1` |
| `SIQ_AUTH_COOKIE_SECURE` | cookie mode 下是否设置 Secure；HTTPS 公网部署应为 `1` |
| `SIQ_AUTH_COOKIE_SAMESITE` | cookie SameSite 策略，默认 `lax` |
| `SIQ_SOURCE_TOKEN_SECRET` | PDF/source 溯源短期访问 token 密钥；建议与 `SIQ_AUTH_SECRET_KEY` 不同，至少 32 字符 |
| `SIQ_SOURCE_ACCEPT_LEGACY_AUTH_SECRET` | source token 迁移兼容开关；默认 `0` 不接受旧 auth secret source token，短期迁移需要时显式设 `1` |
| `SIQ_LOCAL_STATE_ROOT` | 推荐本地状态根；兼容默认是仓库根，也可设为外部磁盘或用户 state 目录 |
| `SIQ_DATA_ROOT` | 兼容期运行态根目录，默认 `$SIQ_LOCAL_STATE_ROOT/data` |
| `SIQ_RUNTIME_ROOT` | 新增本地运行态建议根目录，默认 `$SIQ_LOCAL_STATE_ROOT/var` |
| `SIQ_ARTIFACTS_ROOT` | 构建、测试、评测和批处理输出根目录，默认 `$SIQ_LOCAL_STATE_ROOT/artifacts` |
| `SIQ_DATASETS_ROOT` | 可版本化小型 fixtures 和稳定样本根目录，默认 `datasets` |
| `SIQ_WIKI_ROOT` | Wiki 根目录，默认 `$SIQ_DATA_ROOT/wiki` |
| `SIQ_PDF2MD_DATA_DIR` | PDF 解析运行态目录，默认 `$SIQ_DATA_ROOT/pdf-parser` |
| `SIQ_DOCUMENT_PARSE_DATA_DIR` | 通用文档解析运行态目录，默认 `$SIQ_DATA_ROOT/document-parser` |
| `SIQ_REPORT_FINDER_ROOT` | 兼容变量；统一公告下载入口默认使用 `services/market-report-finder` |
| `SIQ_MARKET_REPORT_FINDER_ROOT` | 市场下载服务目录，默认 `services/market-report-finder` |
| `SIQ_MARKET_REPORT_RULES_ROOT` | 境外市场规则服务目录，默认 `services/market-report-rules` |
| `SIQ_START_MARKET_REPORT_FINDER` | 是否额外启动备用市场下载服务，默认 `0` |
| `SIQ_START_MARKET_REPORT_RULES` | 是否随一键脚本启动境外市场规则服务，默认 `0` |
| `SIQ_UPDATE_DEPS` | 设为 `1` 时允许启动脚本更新 Python/Node 依赖；默认 frozen |
| `SIQ_COMPOSE_BIND_HOST` | Docker Compose 发布端口绑定地址，默认 `127.0.0.1` |
| `SIQ_HERMES_HOME` | Hermes 运行态目录，默认 `$SIQ_DATA_ROOT/hermes/home` |
| `SIQ_HERMES_PROFILES_ROOT` | Hermes profiles 目录，默认 `$SIQ_HERMES_HOME/profiles` |
| `SIQ_APP_DATABASE_URL` | API 应用状态库连接串，PostgreSQL 部署使用 `siq_app` |
| `DATABASE_URL` | 导入脚本兼容连接串；API 应用优先使用 `SIQ_APP_DATABASE_URL` |

## 健康检查

```bash
curl -s http://localhost:15173
curl -s http://localhost:18081/health
curl -s http://localhost:18081/metrics | head
curl -s http://localhost:15000/api/health
curl -s http://localhost:15010/api/health
curl -s http://localhost:18000/health
curl -s http://localhost:18010/health
curl -s http://localhost:18020/healthz
```

Hermes gateway 按需单独启动后检查：

```bash
curl -s http://localhost:18642/health
curl -s http://localhost:18651/health
curl -s http://localhost:18649/health
curl -s http://localhost:18650/health
curl -s http://localhost:18652/health
```

## 开发验证

```bash
cd /home/maoyd/siq-research-engine/apps/api
uv run python -m pytest tests
```

```bash
cd /home/maoyd/siq-research-engine/apps/pdf-parser
python3 -m pytest tests
```

```bash
cd /home/maoyd/siq-research-engine/apps/web
npm run build
```

```bash
cd /home/maoyd/siq-research-engine
bash -n start_all.sh
bash -n apps/api/start.sh
bash -n apps/pdf-parser/run.sh
```

```bash
cd /home/maoyd/siq-research-engine/services/market-report-finder
uv run python -m pytest tests
```

```bash
cd /home/maoyd/siq-research-engine/services/market-report-rules
uv run python -m pytest tests
```

## 注意事项

- 模型服务、MinerU API、VLM 和 vLLM 是主机相关服务，不由 `start_all.sh` 自动启动。
- `data/` 是运行态目录，不应整体纳入 Git。
- Docker Compose 的 Postgres init 脚本只在首次创建数据库 volume 时执行；已有 volume 需要手动补建数据库或重建 volume。
- 新增路径配置时优先使用 `SIQ_*`，旧变量只作为兼容回退。
