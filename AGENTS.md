# 仓库指南

## 项目结构与模块组织

SIQ Research Engine 是一个同时包含 Python、TypeScript 与 Hermes 多智能体配置的研究型工作区。核心应用位于 `apps/`：`apps/web` 是基于 React 19/Vite 的前端界面，`apps/api` 是基于 FastAPI 的聚合后端，`apps/pdf-parser` 与 `apps/document-parser` 负责解析流水线。市场相关服务位于 `services/market-report-finder` 与 `services/market-report-rules`；统一公告下载入口和可选的备用市场下载入口共用前者代码与不同启动配置。`agents/hermes` 存放研究、跟踪、法务和投委会多智能体 profiles、共享模板与 gateway 脚本。共享的 Python 契约代码位于 `packages/market-contracts`。数据库 DDL/DML、迁移与导入工具位于 `db/`，批处理、运维、Hermes 与向量入库脚本位于 `scripts/`。`infra/` 包含 Docker、环境示例、systemd/supervisor 以及模型服务配置，`runtimes/` 保存本地推理运行环境（例如 `mineru-native`）。版本化样本与回归集位于 `datasets/` 和 `eval_datasets/`；运行时输出优先放在 `var/` 与 `artifacts/`，兼容期数据仍可能出现在 `data/` 和根级 `test-results/` 目录。

## 构建、测试与开发命令

- `cp infra/env/local.example infra/env/local.env`：初始化本地环境模板；兼容期内，`start_all.sh` 仍会回退读取旧的 `env/backend.env` 与 `env/frontend-dev.env`。
- `SIQ_START_HERMES_GATEWAYS=0 ./start_all.sh`：未安装 `hermes` 命令时推荐使用；会启动统一公告下载服务、API、PDF parser、document parser 与前端。
- `./start_all.sh`：默认也会启动 Hermes gateways；可结合 `SIQ_START_MARKET_REPORT_FINDER=1`、`SIQ_START_MARKET_REPORT_RULES=1`、`SIQ_START_VECTOR_INGEST=1` 打开可选服务。
- `docker compose -f infra/docker/docker-compose.yml --env-file infra/env/local.env up`：启动容器化主栈。根目录 `docker-compose.yml` 只是兼容包装，规范路径仍是 `infra/docker/docker-compose.yml`。
- `scripts/check_all.sh`：运行 API、PDF parser、document parser、market-report-finder、market-report-rules、web 单元测试与前端检查。
- `cd apps/web && npm run dev -- --host 0.0.0.0 --port 15173`：启动前端界面。
- `cd apps/web && npm run test:unit`：运行前端单元测试。
- `cd apps/web && npm run check:frontend`：运行 ESLint 并执行生产构建检查。
- `cd apps/api && uv sync --extra dev && uv run python -m pytest tests`：运行 API 测试。

## 编码风格与命名约定

服务端统一使用 Python 3.11+，并在 API 边界优先采用带类型定义的 Pydantic/FastAPI/SQLModel 契约。Python 模块与测试文件使用 `snake_case` 命名，例如 `test_source_access.py`。前端代码使用 TypeScript、React 19、Vite、ESLint 与 Tailwind CSS 4；组件使用 PascalCase，Hooks 使用 `use*` 命名。新增的领域逻辑优先放在 `apps/web/src/features/<domain>` 下，可复用的 UI、布局与工具代码分别放在 `components/`、`shared/`、`lib/`。新增路径、环境变量或相关配置时，延续现有的 `SIQ_*` 命名约定。

## 测试指南

Python 项目统一使用 `pytest`，测试文件放在各包自己的 `tests/` 目录中，例如 `apps/api/tests`、`apps/pdf-parser/tests`、`apps/document-parser/tests` 与 `services/*/tests`。修改模块时，优先在相邻位置补充有针对性的测试，并尽量使用可复现的固定夹具，而不是依赖实时外部调用。涉及跨服务流程、启动脚本或共享契约变更时，优先运行 `scripts/check_all.sh` 作为整体回归基线。前端单元测试使用 `npm run test:unit`；Playwright 用例位于 `apps/web/e2e`，当文档解析、PDF 解析、报告检索或路由行为发生变化时应运行相关 `npm run e2e` 用例，并检查 `test-results/` 产物。

## 提交与拉取请求指南

提交主题保持简洁且范围明确，优先采用 `web:`、`api:`、`docs:`、`tests:` 等前缀并使用祈使语气。请把无关改动拆分开来，避免把脚本、配置、文档和功能变更混在同一个提交中。Pull Request 需要说明变更范围、列出验证命令、关联相关 issue 或设计文档；如果涉及可见的前端更新，还应附上截图；如果涉及启动脚本、环境变量或 Hermes profiles，也应写清楚所需的本地开关与迁移注意事项。

## 安全与配置提示

不要提交密钥、凭据或本地运行时数据。请从 `infra/env/local.example` 复制起步，并将本地覆盖配置写入 `infra/env/local.env`。`SIQ_AUTH_SECRET_KEY` 必须设置，且长度至少为 32 个字符；`SIQ_SOURCE_TOKEN_SECRET` 强烈建议单独配置为不同的 32+ 字符密钥，未配置时 source token 逻辑会为了兼容性回退到 `SIQ_AUTH_SECRET_KEY`。`SIQ_SOURCE_ACCEPT_LEGACY_AUTH_SECRET=1` 仅应用于短期迁移兼容，不要作为常态配置。`data/`、`var/`、`artifacts/` 与 `test-results/` 中的本地产物不应进入版本控制。唯一例外是 `.gitignore` 按精确文件名放行、已经脱敏并通过 tracked-state 与 secret scan 的 OpenShell baseline 和 toolchain manifest；新增证据必须同步更新三道 allowlist 和测试，原始日志、trace、TLS、数据库、凭据与备份始终不得提交。
