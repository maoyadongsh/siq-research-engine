# SIQ Research Engine 优化实施路线图

> 生成时间：2026-07-10
> 依据：架构 / 代码质量 / 测试 / DevOps 安全 / 性能 / 文档与开发者体验六维度深度检查
> 状态：规划文档（尚未开始代码修改）

## 一、执行摘要

当前 SIQ Research Engine 已进入“可售卖样板闭环”阶段，代码量约 **42–46 万行**（核心源码），工程化程度较高。但伴随规模增长，项目在**安全基线、服务边界、单文件体积、测试覆盖、性能吞吐、文档治理**六个方向出现明显债务。

本路线图把 40+ 条改进建议收敛为 **5 个阶段、28 个可执行任务**，按以下原则排序：

1. **先止血，再治病**：P0 安全与稳定性问题必须在本届点上线/对外暴露前完成。
2. **先边界，再细节**：先把服务边界、共享契约、依赖方向摆正，再拆分大文件、补单测。
3. **先度量，再优化**：补全测试、健康检查、监控后再做性能大改，避免“优化了但不可测”。
4. **先本地，再生产**：DevEx（环境、文档、故障排查）优先，降低团队并行开发成本。

预期总周期：**8 周**（可按团队人力拆分并行）。

---

## 二、阶段总览

| 阶段 | 主题 | 周期 | 核心目标 | 关键产出 |
|---|---|---|---|---|
| Phase 1 | 安全与稳定性止血 | 第 1–2 周 | 消除 P0 安全风险，让本地/CI 可安全运行 | 密钥轮换、权限收敛、CI 加固、启动脚本安全化 |
| Phase 2 | 架构边界与契约收敛 | 第 2–4 周 | 服务边界清晰，`market-contracts` 成为单一事实来源 | 删除非法跨服务 import、重构重复模块、引入 import-linter |
| Phase 3 | 代码质量与可维护性 | 第 3–5 周 | 超大文件拆分、重复代码消除、类型/文档补齐 | 大文件拆分、ruff/mypy/pre-commit、docstring 覆盖率提升 |
| Phase 4 | 测试体系与回归覆盖 | 第 4–6 周 | CI 跑全量测试，核心路径有契约/集成测试 | API 全量 CI、核心编排层测试、fixtures 工厂化 |
| Phase 5 | 性能、可观测与 DevEx | 第 6–8 周 | 消除明显吞吐瓶颈，建立监控与故障排查入口 | 索引/分区、异步化、缓存、Prometheus/健康检查、troubleshooting 手册 |

---

## 三、任务清单

### Phase 1：安全与稳定性止血（第 1–2 周）

| ID | 优先级 | 领域 | 问题 | 具体动作 | 验收标准 | 依赖 | 预估工时 |
|---|---|---|---|---|---|---|---|
| S01 | P0 | 密钥安全 | `env/backend.env` 中 `SIQ_AUTH_SECRET_KEY` 仍是公开占位符，且同文件存真实 PG/LLM/API 密钥 | 1. 轮换所有真实密钥；2. 将 `SIQ_AUTH_SECRET_KEY`、`SIQ_SOURCE_TOKEN_SECRET` 设为 `openssl rand -hex 32` 以上随机值；3. 迁移到 `infra/env/local.env`；4. 从 Git 历史移除已提交 env 文件（BFG/git-filter-repo） | CI secret-scan 通过；本地 `start_all.sh` 不再读取 legacy `env/backend.env`；密钥不再出现在仓库 | - | 1–2 天 |
| S02 | P0 | 文件权限 | `data/`（61 GB）、`var/`、`artifacts/`、`runtime/`、`runtimes/` 对其它用户开放读/执行 | `chmod -R o-rwx data/ var/ artifacts/ runtime/ runtimes/`；在 `start_all.sh` 启动前增加权限检查；文档写明数据目录应 700 | `find data -maxdepth 4 -type f -perm -o+r` 返回空 | - | 0.5 天 |
| S03 | P0 | CI 安全 | `.github/workflows/market-postgres-release-gate.yml` 使用 `POSTGRES_HOST_AUTH_METHOD=trust` 并暴露 5432 | 改为强密码认证；端口仅绑定 `127.0.0.1:5432`；self-hosted runner 做网络隔离；文档更新 | 工作流文件无 `trust`；runner 网络策略文档化 | - | 0.5–1 天 |
| S04 | P1 | 容器安全 | `services/market-report-finder/Dockerfile` 与 `services/market-report-rules/Dockerfile` 默认 root 运行 | 添加非 root 用户（uid 10001），`infra/docker/docker-compose.yml` 补 `user:` 字段；服务 Dockerfile 加入 hadolint 扫描 | 容器内 `whoami` 非 root；CI hadolint 覆盖全部 6 个 Dockerfile | S01 | 1 天 |
| S05 | P1 | 网络安全 | CORS 硬编码 localhost/tauri，生产域名无法使用 | CORS 来源从 `SIQ_CORS_ORIGINS` 读取；生产禁用 `*`；写操作增加 CSRF token 校验 | `apps/api/main.py` 无硬编码 origin；新增环境变量示例与校验 | - | 1 天 |
| S06 | P1 | 调试安全 | 生产启动脚本默认带 `uvicorn --reload`，Flask 支持 `FLASK_DEBUG=1` | 新增 `SIQ_DEPLOYMENT_PROFILE=production` 判断；生产脚本移除 `--reload`；debug 模式强制关闭 | 生产 profile 下 `--reload` 与 `FLASK_DEBUG=1` 不可启动 | - | 0.5 天 |
| S07 | P1 | 基础设施安全 | Milvus MinIO 使用默认凭证 `minioadmin/minioadmin`，端口绑定 0.0.0.0 | 修改默认凭证为环境变量；端口改为 `127.0.0.1:` 或内部网络；同步更新 Milvus 配置 | `infra/vector-index/milvus/docker-compose.yml` 无硬编码凭证；端口不对外暴露 | - | 1 天 |
| S08 | P1 | 配置治理 | `env/frontend-dev.env` 权限 664；环境变量示例 160+ 行，认知负担大 | 所有 env 文件改为 600；重构 `infra/env/local.example` 为“必需/可选/兼容”三类；新增 `infra/env/local.minimal.env` | `ls -l env/ infra/env/` 全部为 `-rw-------`；新成员 5 分钟内可启动核心服务 | S01 | 1 天 |
| S09 | P2 | 运维稳定 | Supervisor/systemd 日志无轮转，存在磁盘打满风险 | `infra/supervisor/supervisord.conf` 增加 `stdout_logfile_maxbytes=50MB`、`stdout_logfile_backups=5`；systemd 使用 `journald` 或 logrotate | 日志文件大小受控；`start_all.sh` 运行 7 天不产生 GB 级单日志 | - | 0.5 天 |
| S10 | P3 | 配置修复 | `infra/model-services/systemd-user/qwen36-vllm.service` 路径拼写错误 `modles_setup` | 修正为 `models_setup`；增加 systemd 服务语法检查到 CI | 服务文件可正常加载；shellcheck/systemd-analyze 通过 | - | 0.25 天 |

---

### Phase 2：架构边界与契约收敛（第 2–4 周）

| ID | 优先级 | 领域 | 问题 | 具体动作 | 验收标准 | 依赖 | 预估工时 |
|---|---|---|---|---|---|---|---|
| A01 | P0 | 共享契约 | `packages/market-contracts` 未成单一事实来源；`_legacy_evidence_package.py` 复制实现 | 1. 删除 `services/market-report-rules/.../_legacy_evidence_package.py`；2. 所有非服务代码只 import `siq_market_contracts`；3. `market-report-finder` schema 迁移到 `market-contracts` | `grep -r "_legacy_evidence_package"` 返回空；`market-report-finder` 引用 `siq_market_contracts` | - | 2–3 天 |
| A02 | P0 | 服务边界 | `apps/pdf-parser`、`db/imports`、`scripts/*` 直接库调用 `services/*` 内部模块 | 1. 为 `market-report-finder` / `market-report-rules` 提供轻量 Python SDK（封装 HTTP/CLI）；2. 脚本/db/pdf-parser 改为调用 SDK 或 HTTP API；3. 禁止跨层 import | `import-linter` / `deptry` CI 检查通过；非法 import 归零 | A01 | 3–5 天 |
| A03 | P1 | API 内部分层 | `apps/api/routers/*.py` 大量直接 import `database`/`models`；`routers/market_reports.py` 2,195 行，含 raw SQL、subprocess、路径校验 | 1. 新增 `apps/api/repositories/` 层；2. Router 只负责校验/序列化/调用 Service；3. 将 `market_reports.py` 拆分为 `MarketReportService`、`MarketPackageRepository`、`MarketScriptOrchestrator` | Router 不再 import `database`；`market_reports.py` < 800 行；相关测试通过 | - | 3–4 天 |
| A04 | P1 | 运行时拆分 | `apps/api/services/agent_chat_runtime_impl.py` 6,282 行，职责过多 | 按子域拆分为 `agent_chat_runtime/` 目录：`sessions.py`、`streaming.py`、`tools.py`、`citations.py`、`guardrails.py`、`evidence.py` | 单文件 < 1,500 行；原测试全部通过；新增模块间 import 无循环 | A03 | 4–5 天 |
| A05 | P1 | 智能体去重 | `apps/api/agents/tracking` 与 `agents/hermes/profiles/siq_tracking` 高度重复 | 明确唯一实现源；Hermes profile 只保留配置/提示词；运行时逻辑下沉到共享包或 `apps/api` | 删除重复目录之一；tracking 路由只引用一份运行时 | A04 | 2 天 |
| A06 | P1 | 前端依赖规则 | `components/`、`lib/` 反向依赖 `features/`；feature 间横向依赖 | 1. ESLint `import/no-restricted-paths` 禁止 `components/**`、`lib/**` 导入 `features/**`；2. 移除 `lib/secApi.ts` 等 re-export；3. feature 间通信通过 `shared` | `npm run check:frontend` 通过；无新增反向依赖 | - | 2–3 天 |
| A07 | P2 | 架构门禁 | 无自动化检查防止非法 import 回退 | CI 增加 `deptry` + `import-linter`；对 `apps/*`、`db/imports`、`scripts/*` 与 `services/*` 的非法 import 做 fail-fast | CI job 失败当检测到非法 import | A02 | 1 天 |
| A08 | P2 | 市场插件化 | `market-report-finder` / `market-report-rules` 各市场 client/service 大量 boilerplate | 引入 `MarketPlugin` 基类与注册表，6 个市场通过配置注册，减少重复文件 | 每个市场新增代码量减少 30% 以上；现有测试通过 | A01 | 3 天 |

---

### Phase 3：代码质量与可维护性（第 3–5 周）

| ID | 优先级 | 领域 | 问题 | 具体动作 | 验收标准 | 依赖 | 预估工时 |
|---|---|---|---|---|---|---|---|
| Q01 | P0 | 文档化 | 核心函数 docstring 覆盖率仅 5.9% | 1. 为 Router/Service/Public function 补充 Google/NumPy 风格 docstring；2. 复杂算法/业务规则加中文注释；3. CI 可选检查 docstring 覆盖率 | 核心 API 与 Service 文件 docstring 覆盖率 > 80% | A03, A04 | 3–4 天 |
| Q02 | P1 | 大文件拆分 | 60+ 文件超过 1000 行，TOP 5 集中在核心运行时 | 优先拆分：`pdf_parser_app_impl.py`、`ingest_final.py`、`workflow.py`、`ic_agent_runtime.py`、`market_reports.py` | 每个目标文件 < 1,500 行；测试与 CI 通过 | A03, A04 | 5–7 天 |
| Q03 | P1 | 重复代码 | `table_merge.py`、`task_store.py`、`path_config.py` 在 document-parser/pdf-parser 重复；wiki_data_accessor 在 analysis/factchecker 重复 | 抽到 `packages/siq-parsers-common` 或 `market-contracts`；analysis/factchecker 共享 `agents/hermes/profiles/shared/scripts/wiki_data_accessor.py` | 重复文件删除；引用方测试通过 | A01 | 2–3 天 |
| Q04 | P1 | Lint/Type | 无项目级 ruff/mypy 配置；TS 未启用 strict | 1. 根目录新增 `pyproject.toml` 统一 ruff/mypy；2. 各子项目 extends 根配置；3. `apps/web/tsconfig.app.json` 开启 `strict` 并分期修复 | `ruff check .` 与 `mypy apps/api/src` 无新增错误；TS strict 错误数逐周下降 | - | 3–5 天 |
| Q05 | P2 | 预提交 | 无 pre-commit hooks | 新增 `.pre-commit-config.yaml`：ruff、mypy、shellcheck、actionlint、eslint、禁止大文件提交 | 所有新提交自动通过 pre-commit | Q04 | 1 天 |
| Q06 | P2 | 未使用导入 | 数十个疑似未使用 import | 在各虚拟环境运行 `ruff check --select F401,F841` 并修复；保留 `from __future__ import annotations` | F401/F841 错误归零 | Q04 | 1 天 |
| Q07 | P2 | 超长函数 | 305 个函数 ≥80 行，51 个类 ≥150 行 | 对 TOP 10 超长函数提取私有函数/策略对象；如 `_collect_stream_run`、`build_ui`、`build_sections` | 目标函数 < 80 行；测试通过 | Q02 | 2–3 天 |
| Q08 | P3 | 命名与常量 | 少量单字母变量、SVG 图表局部变量 | 仅在数学/图形计算局部保留；核心流程变量命名自解释 | 无新增难以理解的核心变量 | - | 0.5 天 |

---

### Phase 4：测试体系与回归覆盖（第 4–6 周）

| ID | 优先级 | 领域 | 问题 | 具体动作 | 验收标准 | 依赖 | 预估工时 |
|---|---|---|---|---|---|---|---|
| T01 | P0 | CI 全量回归 | CI `api-focused` 仅运行 20/84 个 API 测试文件 | 改为 `uv run python -m pytest tests` 或按模块并行 job；`scripts/check_all.sh` 同样全量 | CI 中 API 测试文件覆盖率 100%；PR 合并前全量通过 | - | 1–2 天 |
| T02 | P1 | 核心编排测试 | `orchestrator.py`、`api/routes/*.py`、pdf-parser app 入口无测试 | 1. 为 `market-report-finder` orchestrator 和 routes 加集成测试；2. 为 `pdf_parser_app_impl.py`、`mineru_client.py`、`task_store.py` 加单元/集成测试 | 新增测试 ≥ 50 个；覆盖核心分支 | A02 | 3–4 天 |
| T03 | P1 | DB 导入测试 | `db/imports/import_*_to_postgres.py` 各市场导入脚本几乎无直接测试 | 将导入脚本核心转换逻辑拆分为可测试函数；使用 SQLite/内存 Postgres 替代真实 PG | 每个市场导入脚本至少 1 个契约测试 | A02 | 3–4 天 |
| T04 | P1 | Agent Runtime 测试 | `agent_chat_runtime_impl.py` 底层实现与工具链未覆盖 | 拆分后对各子模块（sessions、tools、memory、citations）增加契约测试；使用 Fake LLM/Milvus/Redis | 新增测试 ≥ 80 个；覆盖流式、工具调用、证据回退 | A04 | 4–5 天 |
| T05 | P1 | Deal/IC 测试 | Deal 与 IC 业务编排层大量无测试 | 为 `deal_agents.py`、`deal_evidence.py`、`ic_agent_runtime.py` 等增加集成测试 | Deal/IC 核心路径测试覆盖率 > 50% | A04, A05 | 3–4 天 |
| T06 | P2 | 测试分层 | 无 `@pytest.mark.integration/slow/network` 标记 | 引入 pytest 标记；CI 分层运行：quick unit / integration / slow / network | 本地 `pytest -m "not slow"` 可在 5 分钟内完成 | T01 | 1 天 |
| T07 | P2 | Fixtures 工厂化 | 测试内联大量 JSON/markdown；大型测试文件（166KB）可读性差 | 创建 `tests/fixtures/` 与 factory：sample packages、mock responses、user/session factory | 大型测试文件体积下降 50%；fixture 复用率提升 | T01 | 2–3 天 |
| T08 | P2 | 覆盖率门槛 | 无覆盖率阈值 | 配置 `pytest-cov`，设置逐步提升阈值：api ≥60%、pdf-parser ≥55%、services ≥50% | CI 上传 coverage report；未达标阻塞合并 | T01 | 1 天 |
| T09 | P3 | 前端测试 | 页面/路由/错误态覆盖不足 | 为 `pages/*`、关键布局、全局状态、错误边界增加单元测试；补充 E2E 失败场景 | 前端单元测试增加 ≥ 30 条 | - | 2 天 |

---

### Phase 5：性能、可观测与 DevEx（第 6–8 周）

| ID | 优先级 | 领域 | 问题 | 具体动作 | 验收标准 | 依赖 | 预估工时 |
|---|---|---|---|---|---|---|---|
| P01 | P1 | 数据库索引 | `ChatMessage`、`UsageEvent` 等缺少复合索引；GIN 索引写入成本高 | 添加 `(session_id, created_at)`、`(user_id, event_type, event_date)` 等复合索引；评估部分 GIN 索引 | 慢查询日志中无全表扫描；关键 API 响应提升 30%+ | - | 2 天 |
| P02 | P1 | 大表分区 | `content_blocks`、`document_pages`、`document_tables`、`financial_statement_items` 等线性膨胀 | 按 `report_year` 或 `filing_id` 范围分区；建立分区维护脚本 | 单表扫描成本下降；新增分区自动化 | P01 | 3–4 天 |
| P03 | P1 | API 异步化 | `agent_chat_runtime_impl.py` 大量同步 I/O、DB、HTTP 阻塞 FastAPI | 统一使用 `AsyncSession` + `httpx.AsyncClient`；阻塞操作放 `run_in_executor` | 长连接并发吞吐提升 2–5×；`/chat` 延迟下降 | A04 | 4–5 天 |
| P04 | P1 | 解析并发化 | pdf-parser / document-parser 单 worker 线程串行处理 | 多 worker/多进程；CPU 密集型后处理用 `ProcessPoolExecutor`；任务队列化 | 解析吞吐提升 N 倍；队列不再无限堆积 | A02 | 4–5 天 |
| P05 | P2 | 大文件流式 | `document_full.json` 等全量加载到内存 | 使用 `ijson`/`orjson` 增量解析；Markdown 生成器流式处理 | 大文件解析内存下降 50–90% | P04 | 2–3 天 |
| P06 | P2 | 向量检索优化 | batch size 小、同步 embedding、Milvus 连接池缺失 | 增大 batch、异步并发 embedding、连接池、embedding 结果缓存 | ingestion 3–10×；检索 P99 延迟降 50%+ | - | 3 天 |
| P07 | P2 | Redis 缓存层 | 公司主数据、最新 filing、向量检索结果、finder 代理结果无缓存 | 增加 Redis 缓存抽象；为热点查询加 TTL | 热点查询 < 10ms；下游调用量下降 | P03 | 2–3 天 |
| P08 | P2 | 任务队列 | 无统一重试/死信/熔断；失败任务处理弱 | 引入 Celery/RQ 替代单线程 worker；指数退避、死信队列、幂等去重 | 任务失败可自动重试；死信可审计 | P04 | 4–5 天 |
| P09 | P2 | 可观测性 | `/health` 仅返回 ok；无 metrics/tracing | `/health` 增加 PG/Redis/Parser 依赖探针；暴露 `/metrics`（Prometheus）；接入 OpenTelemetry tracing | Grafana dashboard 可查看核心指标；告警规则生效 | S09 | 2–3 天 |
| P10 | P2 | 开发者体验 | 环境复杂、缺少 troubleshooting 入口、API 文档人难读 | 1. 新增 `docs/operations/troubleshooting.md`；2. 提供 `infra/env/local.minimal.env`；3. 为高频路由/schema 补充 `summary`/`Field(description=...)`；4. CI 生成 `openapi.json` | 新成员 30 分钟完成首次启动；`/docs` 可直接理解核心接口 | S08 | 2 天 |
| P11 | P3 | CI 效率 | 未缓存 uv/pip/Docker 层；无镜像扫描 | 增加 `actions/cache` 与 Docker layer cache；构建后 Trivy/Grype 镜像扫描 | CI 平均耗时下降 30% | S04 | 1–2 天 |
| P12 | P3 | 文档生命周期 | taskbook 容易过时 | 给 `docs/architecture/*.md` 加 YAML frontmatter（status/owner/last_reviewed）；每月自动标记 stale | 超过 30 天未审阅文档自动标 stale | P10 | 1 天 |

---

## 四、跨阶段依赖图

```text
Phase 1 (安全止血)
  │
  ├─→ S04 (service Dockerfile root) ──→ P11 (CI 镜像扫描)
  ├─→ S08 (env 治理) ──→ P10 (DevEx minimal env)
  └─→ S09 (日志轮转) ──→ P09 (可观测性)

Phase 2 (架构边界)
  │
  ├─→ A01 (market-contracts 收敛) ──→ A02/A08/Q03
  ├─→ A02 (非法 import 消除) ──→ A07 (import-linter)
  ├─→ A03 (API 分层) ──→ A04 (agent runtime 拆分)
  └─→ A04 ──→ A05 (tracking 去重) / T04 (agent runtime 测试)

Phase 3 (代码质量)
  │
  ├─→ Q04 (ruff/mypy) ──→ Q05 (pre-commit) / Q06 (unused import)
  └─→ Q02 (大文件拆分) ──→ Q07 (超长函数)

Phase 4 (测试)
  │
  ├─→ T01 (CI 全量) ──→ T06 (pytest 标记) / T08 (覆盖率)
  └─→ T07 (fixtures) ──→ T02/T03/T04/T05

Phase 5 (性能/DevEx)
  │
  ├─→ P01/P02 (DB 索引分区)
  ├─→ P03/P04 (并发异步) ──→ P05 (流式) / P08 (队列)
  └─→ P06/P07/P09/P10/P11/P12
```

---

## 五、关键成功指标（8 周后可度量）

| 指标 | 当前基线 | 8 周目标 |
|---|---|---|
| 核心源码文件数 | ~1,540 | 通过拆分/合并后保持或略降 |
| 超大文件（>1500 行） | 60+ | < 30 |
| Python docstring 覆盖率 | 5.9% | > 60%（核心模块 >80%） |
| CI API 测试文件覆盖率 | 24%（20/84） | 100% |
| 非法跨服务 import | 数十处 | 0（import-linter 拦截） |
| `_legacy_evidence_package.py` | 存在 | 删除 |
| `data/` 目录其它用户可读文件 | 22,692+ | 0 |
| `/health` 依赖探针 | 无 | PG/Redis/Parser 全量 |
| 本地从零启动到 first test pass | > 30 分钟 | < 30 分钟 |
| CI 平均耗时 | 未统计 | 下降 30% |

---

## 六、实施建议

1. **组队方式**：建议按 Phase 拆分为 2–3 个并行小组：
   - **安全与 DevOps 小组**：S01–S10、P09、P11
   - **架构与质量小组**：A01–A08、Q01–Q08
   - **测试与性能小组**：T01–T09、P01–P08

2. **每周节奏**：
   - 周一：各组提交本周计划与依赖阻塞点
   - 周三：代码评审与跨组同步
   - 周五：合并已通过 CI 的 PR，更新本路线图状态

3. **PR 规范**：每个任务单独 PR，标题使用 `[S01]`, `[A01]`, `[Q02]` 等前缀；合并前必须：
   - 通过 `scripts/check_all.sh`
   - 通过新增/更新的 lint/type/import 检查
   - 补充或更新对应测试
   - 更新 `docs/operations/troubleshooting.md` 或相关 README（如影响启动/配置）

4. **风险控制**：
   - **S01 密钥轮换**必须在第一周完成，否则后续所有环境相关改动都可能泄露新密钥。
   - **A01/A02 契约收敛**是高风险重构，建议先写契约测试再迁移实现。
   - **P03/P04 异步化/并发化**改动面广，建议在独立 feature branch 上跑 `market-eval` 全量通过后再合并。

---

## 七、下一步行动

若团队确认本路线图，建议立即执行：

1. 召开 30 分钟对齐会，确认优先级与负责人分配。
2. 创建 GitHub Project / 看板，把上表 28 个任务转为 issue/PR。
3. 第一周先合并 S01–S03（安全止血），作为后续所有工作的基线。
4. 同步更新 `AGENTS.md` 与 `README.md` 中的“当前优化焦点”段落，让所有贡献者了解边界规则。

---

*本文件为动态文档，建议每两周根据实际进展更新一次任务状态、工时和阻塞点。*
