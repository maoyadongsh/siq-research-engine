# SIQ Research Engine 仓库指南

## 1. 项目定位与当前工程事实

SIQ Research Engine 是一个面向证券研究、一级市场尽调、投委会决策和通用文档处理的多应用仓库。项目同时包含 Python、TypeScript、SQL、Shell、Hermes 智能体配置以及 NVIDIA OpenShell 安全运行面，并在 NVIDIA DGX Spark 上协同运行本地模型和数据服务。

仓库需要同时区分三类内容：

1. **可版本化源码**：应用、服务、智能体、共享契约、数据库脚本、部署模板、测试和文档。
2. **本地运行状态**：数据库、Wiki 事实资产、上传文件、解析结果、Hermes 会话、OpenShell 状态、缓存和模型运行环境。
3. **生成产物**：测试报告、评测结果、截图、脱敏审计证据和一次性分析输出。

不要因为这些内容都位于同一台开发机，就把它们视为可以相互替代的目录。源码应可审查和复现；运行状态默认不进入 Git；生成产物只有在经过筛选、脱敏并有明确长期价值时才允许提交。

## 2. 目录职责

### 2.1 应用、服务与共享代码

| 路径 | 当前职责 |
| --- | --- |
| `apps/web` | React 19、Vite、TypeScript 和 Tailwind CSS 4 前端工作台。 |
| `apps/api` | FastAPI 聚合后端，负责鉴权、会话、任务编排、Agent 代理、证据访问、会议和一级市场 Deal OS。 |
| `apps/pdf-parser` | 面向财报的 PDF/MinerU 解析服务、质量报告、财务抽取和 source map。 |
| `apps/document-parser` | 通用文档解析、schema extraction、表格关系和 PDF bridge。 |
| `apps/ios-meeting-capture` | 会议采集的 iOS/Capacitor 客户端及配套构建脚本。 |
| `services/market-report-finder` | CN、HK、US、EU、JP、KR 官方披露检索、主体解析和下载。 |
| `services/market-report-rules` | 多市场解析后规则、校验、质量门和 PostgreSQL load plan。 |
| `packages/market-contracts` | 跨服务共享的市场证据、解析和校验契约。 |
| `agents/hermes` | 二级市场、一级市场和应用场景的 Hermes profiles、规则、模板、skills 与共享脚本。 |

新增跨服务 schema、枚举、evidence gate 或市场身份规则时，优先进入 `packages/market-contracts`，不要在 API、finder、rules 和脚本中复制多份常量。

### 2.2 数据库、脚本与基础设施

| 路径 | 当前职责 |
| --- | --- |
| `db/ddl` | 数据库基线 DDL。 |
| `db/dml` | 可重复执行的 DML、视图和事实构建脚本。 |
| `db/imports` | PostgreSQL 导入、回测、规则和相邻测试。 |
| `scripts/ci` | CI 辅助脚本。 |
| `scripts/dev` | 本地开发工具。 |
| `scripts/ops` | 健康检查、备份、恢复和发布操作。 |
| `scripts/maintenance` | 安全、依赖、质量、性能和仓库治理门禁。 |
| `scripts/hermes` | Hermes profile 解析、gateway 和会议模型目标管理。 |
| `scripts/openshell` | OpenShell gateway、sandbox、broker、证据导出、生命周期和回滚工具。 |
| `scripts/meeting` | 会议服务编排、质量门、基线和测试。 |
| `scripts/vector-index` | Milvus 入库与向量索引工具。 |
| `scripts/wiki` | LLM-Wiki 构建、语义组织、市场 wikiset 和可追溯性工具。 |
| `scripts/{hk,jp,kr,eu,us-sec}` | 各市场的下载、package、解析和验证工具。 |
| `infra/docker` | 规范 Docker Compose 服务图；根级 `docker-compose.yml` 只是兼容包装。 |
| `infra/env` | 可提交环境模板；真实 `local.env` 不进入 Git。 |
| `infra/model-services` | DGX Spark 本地模型启动/管理脚本归档，不包含模型权重、缓存和日志。 |
| `infra/openshell` | 自研 OpenShell + Hermes 集成的策略、补丁、BYOC、Provider、Broker、schema 和说明。 |
| `infra/systemd-user`、`infra/supervisor` | 长期运行和进程监管模板；根级 `supervisord.conf` 是兼容包装。 |
| `infra/vector-index` | Milvus 等向量基础设施配置。 |

项目不使用 NemoClaw 作为运行时。`infra/openshell` 基于固定 NVIDIA OpenShell 版本直接承载 Hermes，并保留 SIQ 的 API、工具、证据和运行合同。

### 2.3 文档、数据集、运行态与产物

| 路径 | 当前职责与规则 |
| --- | --- |
| `docs/architecture` | 架构设计、技术任务书和 ADR；不是一次性运行输出目录。 |
| `docs/operations`、`docs/runbooks` | 本地开发、部署、运维、发布和故障处理说明。 |
| `docs/reports` | 有日期和审查范围的代码审计、性能分析、回测报告。 |
| `docs/site` | MkDocs 站点正文和可发布静态资源。 |
| `docs/competition` | DGX Spark Hackathon 等赛事说明、评审映射和公开提交材料。 |
| `docs/superpowers` | 已整理的设计、计划、规格和报告；与 `.superpowers/sdd` 工具工作区区分。 |
| `datasets` | 新增的、小型、脱敏、稳定、可版本化评测集与 fixtures。 |
| `eval_datasets` | 历史评测语料和兼容回归集；新数据默认不要继续堆入。 |
| `data` | 历史兼容运行态和本地事实资产，当前仍被 Wiki、parser、Hermes 和下载链路广泛使用。 |
| `var` | 新增本地运行状态的推荐根目录，当前主要承载 OpenShell、meeting、PID、日志和缓存。 |
| `artifacts` | 测试、评测、截图和脱敏审计证据等生成产物，默认可清理、默认忽略。 |
| `runtimes` | 本机推理或解析运行环境，例如 MinerU venv；不提交 Git。 |
| `test-results`、`tmp`、`downloads` | 本地测试、临时和下载内容；不作为长期源码目录。 |

`data/` 约定为兼容路径，`var/` 是新增运行态方向。当前系统尚未完成大规模物理迁移；任何涉及 `data/`、`var/`、`artifacts/` 或 `runtimes/` 的移动，都必须先盘点调用方、容量、权限、备份、回滚和正在运行的服务，禁止为追求目录整齐直接移动或删除。

LLM-Wiki 位于本地事实体系中，通过知识抽取、ResearchIdentity、主题/对象关系和逻辑跳转索引组织知识。它不使用 Qwen3-VL Embedding 或 Reranker 完成 Wiki 查询，也不把传统 RAG 切片作为权威事实层。Milvus、embedding 和 reranker 是独立、可重建的补充检索路径。

## 3. 根目录文件放置规范

根目录只保留跨仓库约定或工具必须从根发现的文件：

- `README.md`、`AGENTS.md`；
- `LICENSE`、`LICENSE.zh-CN.md`、`NOTICE`、`THIRD_PARTY_LICENSES.md`；
- `.gitignore`、`.gitattributes`、`.dockerignore` 和质量工具配置；
- `mkdocs.yml`、`vercel.json`；
- `start_all.sh`；
- 兼容入口 `docker-compose.yml`、`supervisord.conf`。

新增内容按以下规则放置：

- 代码审查、性能分析和阶段总结进入 `docs/reports/`；
- 架构说明进入 `docs/architecture/`，站点使用的 HTML/图片进入 `docs/site/assets/`；
- 赛事材料进入 `docs/competition/`；
- 本地 HTML 草稿、截图和一次性输出进入 `artifacts/`；
- 不在根目录新增带版本后缀的 `architecture-vN.html`、临时报告或模型启动脚本。

移动已有入口文件前必须检查 GitHub Actions、systemd、文档、测试和外部脚本中的路径引用。`start_all.sh`、根级 Compose/Supervisor 包装和平台配置当前具有兼容价值，不能只因“看起来不整齐”而移动。

## 4. 运行路径合同

当前统一路径变量如下：

```text
SIQ_PROJECT_ROOT       仓库根目录
SIQ_LOCAL_STATE_ROOT   本机状态总根，默认等于项目根
SIQ_DATA_ROOT          历史兼容数据根，默认 ${SIQ_LOCAL_STATE_ROOT}/data
SIQ_RUNTIME_ROOT       新运行态根，默认 ${SIQ_LOCAL_STATE_ROOT}/var
SIQ_ARTIFACTS_ROOT     生成产物根，默认 ${SIQ_LOCAL_STATE_ROOT}/artifacts
SIQ_DATASETS_ROOT      可版本化数据集根，默认 ${SIQ_PROJECT_ROOT}/datasets
SIQ_WIKI_ROOT          Wiki 根，默认 ${SIQ_DATA_ROOT}/wiki
```

新增代码必须从环境变量或相对当前文件推导项目路径，不得新增 `/home/maoyd/siq-research-engine` 之类的个人绝对路径。仓库中仍有历史硬编码，修改相邻模块时应渐进替换并补测试，但不要在无关变更中批量重写。

路径迁移应使用“双读或显式回退、单写新路径、可观测、可回滚”的方式推进。不得用符号链接或静默 fallback 掩盖生产配置错误，安全边界和 OpenShell mount 合同要求路径来源可审计。

## 5. 本地模型、数据服务与智能体边界

DGX Spark 上的本地模型由独立 vLLM/Conda/Docker 进程并行运行，项目内归档入口位于 `infra/model-services/`：

- Nemotron 3 Nano Omni：本地主模型和原生图片/音频/视频理解；
- MinerU2.5-Pro：文档版面、表格和图片解析；
- Qwen3-VL Embedding：选定向量检索和记忆路径；
- Qwen3-VL Reranker：候选精排；
- Fun-ASR Nano 和 meeting-speech：语音识别、VAD、说话人和会议链路；
- StepFun Step-3.7 Flash：通过受控 Provider 接入的云端主模型。

模型权重、Hugging Face/ModelScope 缓存、Conda 环境、PID 和日志不得放入 Git。模型启动脚本的默认路径是本机部署基线，新增脚本应优先支持环境变量覆盖。

OpenShell 已在 `siq_analysis` 分析助手真实业务链路中跑通 scope resolve、sandbox Hermes、SSE、终态释放和回收。Host 仍是环境回退基线；“分析助手功能跑通”与“完成全部正式生产 A/B、人工评审和发布门禁”是两个不同结论，代码、文档和演示不得混写。

## 6. 构建与开发命令

初始化本地配置：

```bash
cp infra/env/local.example infra/env/local.env
```

真实密钥只写入被忽略的 `infra/env/local.env` 或受控外部 secret store。兼容期内 `start_all.sh` 仍会读取旧的 `env/backend.env` 和 `env/frontend-dev.env`，但新配置不得继续写入旧路径。

启动主栈：

```bash
SIQ_START_HERMES_GATEWAYS=0 ./start_all.sh
```

已安装并配置 Hermes 时可直接运行 `./start_all.sh`。当前脚本还管理或复用 OpenShell gateway/brokers，并可通过 `SIQ_START_MARKET_REPORT_FINDER`、`SIQ_START_MARKET_REPORT_RULES`、`SIQ_START_VECTOR_INGEST`、`SIQ_MEETINGS_ENABLED` 等开关启用可选服务。不要假设某个端口存在就等于业务 ready；同时检查 process、HTTP readiness、最小推理和业务质量。

规范 Docker Compose 入口：

```bash
docker compose -f infra/docker/docker-compose.yml --env-file infra/env/local.env up
```

常用聚焦命令：

```bash
cd apps/api && uv sync --frozen --extra dev && uv run --frozen python -m pytest tests
cd apps/web && npm ci && npm run test:unit && npm run check:frontend
cd services/market-report-finder && uv sync --frozen --extra dev && uv run --frozen python -m pytest tests
cd services/market-report-rules && uv sync --frozen --extra dev && uv run --frozen pytest
```

## 7. 测试与质量门禁

全仓主检查入口：

```bash
scripts/check_all.sh
```

它当前覆盖 API、两个 parser、finder、rules、market-contracts、前端单测/构建检查，以及安全卫生、大文件、OpenShell tracked-state/completion、OpenShell 离线测试和 PostgreSQL contract gate。部分门禁依赖本机运行证据或服务前置条件；失败时要区分代码回归、环境缺失和正式发布证据缺口，不能为了绿灯删除 fail-closed 检查。

测试约定：

- Python 使用 `pytest`，测试放在相邻模块的 `tests/`；
- 前端单元测试使用 `npm run test:unit`，Playwright 位于 `apps/web/e2e`；
- 修改共享契约、启动脚本或跨服务路径时，除聚焦测试外运行相应 maintenance/contract gate；
- 单元测试优先使用固定 fixture，不依赖实时外部网络；真实模型、真实数据和 live smoke 必须显式标记；
- Mock 通过只证明协议或 fail-closed 行为，不得写成真实模型质量结论；
- 解析、财务、问答、OpenShell 和会议功能的验收必须保留 evidence、版本、参数和运行来源。

## 8. 编码与架构约定

- 服务端使用 Python 3.11+；CI 也会用 Python 3.13 执行语法和治理检查。
- Python 模块、函数和测试使用 `snake_case`；API 边界优先使用带类型的 Pydantic/FastAPI/SQLModel 契约。
- 前端使用 TypeScript、React 19、Vite 和 Tailwind CSS 4；组件使用 PascalCase，Hooks 使用 `use*`。
- 新增前端领域逻辑优先进入 `apps/web/src/features/<domain>`；通用 UI、布局、工具和 API 基础设施分别进入既有 `components`、`shared`、`lib` 边界。
- 结构化数据使用 JSON/SQL/YAML 解析器，不用脆弱的字符串拼接替代已有 schema。
- 财务计算使用 `Decimal` 和确定性工具，保留输入 evidence、公式、期间、币种、单位与 trace；不得由模型心算代替后端校验。
- 事实结论优先来自 LLM-Wiki、PostgreSQL 权威记录、解析 artifact 和官方来源；Milvus 召回与模型输出不能自动升级为权威事实。
- 新增环境变量使用 `SIQ_*` 前缀，并同步模板、启动入口、Compose、文档和测试。

## 9. 变更范围与迁移纪律

- 工作区可能包含用户未提交内容。不要删除、覆盖、移动或提交与当前任务无关的文件。
- 做目录治理时先处理低风险根级文档，再处理运行路径；不要把路径移动与业务重构混在一个提交。
- 使用 `git mv` 保留历史，并在同一变更中更新所有有效引用、测试、CI、systemd 和运维文档。
- 大体量运行目录迁移前必须记录容量、所有者、权限、服务进程、备份位置、校验 hash 和回滚方法。
- `data/wiki/tracking/scripts*` 当前是历史遗留但仍在使用的生产代码例外；在专门迁移方案落地前不要随意移动，也不要继续向 `data/` 添加新的通用源码。
- `.superpowers/sdd` 是工具工作区，现有内容包含历史 review diff 和任务报告。不要把新的大型 diff、临时 review 包或生成日志默认提交；长期文档应整理到 `docs/superpowers` 或 `docs/reports`。

## 10. Git、提交与公开发布

提交应小而聚焦，优先使用 `web:`、`api:`、`docs:`、`infra:`、`tests:` 等明确前缀。提交前至少检查：

```bash
git status --short
git diff --check
git diff --cached --name-only
```

不要使用宽泛 `git add .` 吸收本地运行数据或用户草稿。目录迁移、许可证、文档、代码和运行证据应按逻辑拆分提交。

以下目录默认忽略，但存在经过审查的精确放行项：

- `data/`：README、`.gitkeep`，以及兼容期 `data/wiki/tracking/scripts*` 源码；
- `var/`：README、`.gitkeep` 和明确放行的脱敏 OpenShell manifest；
- `artifacts/`：README、manifest 绑定的脱敏 OpenShell 证据，以及明确放行的二级市场脱敏评测/截图；
- `runtimes/`、`downloads/`、`tmp/`、`test-results/`：不提交。

新增放行不能只修改 `.gitignore`。必须同步路径 allowlist、secret scan、大小门禁、manifest/hash 和相应测试。公开材料不得包含：

- `.env`、API key、数据库密码、JWT secret、TLS 私钥、Provider 凭据；
- 客户文档、用户会话、完整日志、原始音频、声纹 embedding；
- 模型权重、缓存、虚拟环境和容器运行状态；
- 私人联系方式、未授权团队资料或不可公开的绝对路径内容。

仓库自研代码采用 Apache License 2.0；第三方软件、模型和服务边界见 `NOTICE` 与 `THIRD_PARTY_LICENSES.md`。提交新依赖、模型或上游源码/补丁时，必须记录来源、版本、commit 或 revision、许可证和修改事实。

Pull Request 应说明变更范围、验证命令、运行前置条件和回滚方式。涉及可见前端应附截图；涉及路径、环境变量、启动脚本、模型或 Hermes/OpenShell profiles 时，应明确兼容期、默认值和迁移影响。
