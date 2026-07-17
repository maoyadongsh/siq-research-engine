# SIQ Research Engine 全方位代码审查报告

> 审查日期：2026-07-17 · 审查方式：5 路并行智能体对 apps/api、apps/web、apps/pdf-parser、apps/document-parser、services/*、packages/market-contracts、agents/hermes、infra、scripts、db、CI 做只读深度审查。

## 总体评价

工程基础整体扎实：API 分层清晰（routers → services → models）、JWT/CORS 有生产强制校验、下载层有 DNS 固定防 SSRF、前端有统一 API client + DOMPurify + 路由级代码分割、CI 含 gitleaks/pip-audit/trivy、Docker 非 root + read_only。核心问题集中在五个方面：**①巨型文件、②复制粘贴式分叉、③未提交改动失控、④鉴权/类型/重试的"静默降级"模式、⑤DB 无迁移机制**。

---

## 🔴 高严重级别（按优先级排序）

### H1. 未提交改动巨型化、多主题混杂（治理风险，最高优先）
- `git diff --stat`：**194 文件 +19889/−2370**，另有 66 个未跟踪文件。
- 同一工作区混杂 ≥5 个主题：新增 3 个 multi_market profiles、7 个 IC profile 治理、`agent_chat_runtime_impl.py` 单文件 +2179 行、前端大改版（chat.css +470、PrimaryMarketMaterials.tsx +484）、start_all.sh +527。
- 直接违反自身 AGENTS.md「把无关改动拆分开来」的提交规范。任何一次回滚/冲突都会牵连无关主题。
- **建议**：立即按主题拆成 4–5 个提交（hermes 治理 → 新 profiles → api → web → infra），先提交风险最低的。

### H2. 新 multi_market profiles 未注册且复制式分叉
- `agents/hermes/profiles/manifest.json:5-19` 与 `agents/hermes/README.md` 智能体矩阵均未列 `siq_analysis_multi_market` / `siq_factchecker_multi_market` / `siq_tracking_multi_market`。
- multi_market 与旧 profile 大量同名文件各自分叉（复制式建 profile），双向漂移风险高——与 H3 同源的"复制维护"反模式。
- **建议**：补 manifest/README 注册；共享逻辑下沉 `shared/`，旧 profile 明确 deprecation 路径。

### H3. 复制粘贴式分叉遍布四个层面
| 位置 | 证据 | 漂移风险 |
| --- | --- | --- |
| pdf-parser ↔ document-parser | `table_merge.py` diff 相似度 **99.4%**；`path_config.py`、`task_store.py` 同源分叉 | 双份维护必然漂移 |
| Gate 契约常量三处定义 | `packages/market-contracts/.../evidence_gates.py:8-55`、`market-report-rules/.../validation.py:19-31`、finder `url_ownership.py:9-18`；监管域名白名单维护两份 | finder 判 official 的源 rules 可能判非 official，**质量门结论失真** |
| 六市场 HTTP 重试 | 仅 CN/JP 有重试（无 429 处理、无抖动），US/HK/EU/KR `raise_for_status()` 直接失败；`_retry_delay_seconds` 逐行重复 | SEC/DART 限流即整体失败 |
| 公司名→市场映射两份 | finder `assist.py:97-107` 与 `orchestrator.py:487-496`；无法识别时静默路由到 SEC | 日韩欧公司被错路由 |
- **建议**：抽 `packages/parser-common`；常量/白名单全部从 `siq_market_contracts` 单一导入并加一致性测试；抽统一带退避/429/抖动的 HTTP 客户端封装；市场映射合并为单表，无法识别时返回 unknown 强制显式确认。

### H4. 上帝文件（可维护性定时炸弹）
- `apps/api/services/agent_chat_runtime_impl.py` **8122 行 / 326 函数**（本次还 +2179 行）；`ic_phase_orchestrator.py` 6086 行；`agent_runtime_financial_claim_verifier.py` 4868 行；`meeting_repository.py` 4695 行；`pdf_parser_app_impl.py` 4691 行；`financial_extractor.py` 4039 行。
- 前端同级问题：`primaryMarketViewModel.ts` 2379 行、`PrimaryMarketMeeting.tsx` 2100 行、`DealWorkflow.tsx` 1752 行、`dealTypes.ts` 1488 行。
- **建议**：按既有 `agent_runtime_*` 小模块先例渐进拆分；为文件行数设 CI 上限（如新增代码不得进入 >1500 行文件）。

### H5. document-parser / pdf-parser 本地零鉴权的静默降级
- `apps/document-parser/app.py:107,124-167`：`APP_ACCESS_TOKEN` 默认空串，非 prod/docker profile 未配 token 时直接放行，且 `X-SIQ-User-Id/Role` 头被当作可信身份（:220-227），**任何能访问端口的人可伪造 admin**。pdf-parser 同构（`pdf_parser_request_utils.py:28,49-64`）。
- 若部署时 profile 环境变量漏配（只设 `APP_ENV=production` 而 token 为空），会**静默**降级为无鉴权。
- **建议**：token 为空时在非显式 local profile 下 fail-fast（启动即拒），而非仅 warning。

### H6. DB 无迁移机制 + 破坏性 DDL 重放
- `db/migrations/` 是空目录，无 alembic；7 个手写 DDL（5843 行）靠整体重放演进。
- `db/ddl/010_create_sec_us_schema.sql:3-9` 顶部直接 `drop view if exists ... cascade`（级联删除未预期依赖）；`db/dml/001_upsert_document_full.sql:5-14` 以 DELETE+重插代替 upsert。
- **建议**：引入 alembic（或 sqitch），增量变更与基线 DDL 分离；drop 去 CASCADE 改显式依赖清单。

### H7. 前端 Token 存 localStorage + 未开 TS strict
- `apps/web/src/lib/auth.tsx:22,93` 读写 localStorage `access_token`，XSS 可窃取；Cookie 模式已实现（`client.ts:207`）但非默认。
- `tsconfig.app.json` 未开 `"strict": true`，类型保障名存实亡。
- **建议**：默认启用 HttpOnly Cookie 模式（或缩短 token 寿命 + 加 CSP）；开启 strict 并按目录增量修复。

### H8. start_all.sh 单体贴合度过高、关停不健壮
- 1056 行内联启动 20+ 服务；`cleanup()`(:286-297) 只发 SIGTERM 无超时升级，子进程忽略 TERM 会永久挂住；document-parser 无限重启固定 3s 无退避（:418-433）；`wait -n`(:1055) 任一非核心服务退出即全栈 teardown；启动时执行 `uv sync`/`npm install` 有副作用。
- **建议**：迁移到已有的 supervisord.conf（功能重叠）或按服务模块化；cleanup 加超时 `kill -9` 升级；重启加指数退避；核心/可选服务分级。

---

## 🟡 中严重级别

### M1. check_all.sh 与 CI 漏掉 hermes profile 测试
- `scripts/check_all.sh` 全文无 hermes/profiles；profiles 下 23 个测试文件、84 个 scripts/*.py 无回归基线（本次也在改动这些文件）。**建议**：check_all.sh 与 CI 各加一个 "Hermes profile tests" step。

### M2. 同步阻塞 HTTP 在核心检索链路
- `rerank_provider.py:98`、`vector_retrieval.py:244`、`deal_evidence_milvus.py:298` 用同步 `httpx.Client`，经 `deal_retrieval.retrieve_for_agent()`(:503) 被调；仅部分调用点（`primary_market_meeting.py:2127/2413`）用 `asyncio.to_thread` 包裹，`ic_startup_retrieval.py:344` 等未保证。**建议**：检索链路改 `httpx.AsyncClient`，或在所有 async 调用点强制 `to_thread` 并加 lint 约束。

### M3. DART viewer 解析静默降级 + 策划目录硬编码
- `downloader.py:504-534` 大正则匹配 JS 变量抽章节，DART 改版会静默走整页 HTML fallback，无 warning，证据链质量悄悄降级。**建议**：解析失败写 `parser_degraded` 标记并计入质量门。
- `eu/catalog.py`/`jp/catalog.py`/`kr/catalog.py` 把发行人年报 URL 写死为 Python 字面量（部分 published_at 为未来日期），测试钉死样本数。**建议**：外置为版本化 JSON + URL 存活巡检，测试只断言 schema。

### M4. mypy 严格配置形同虚设 + 静态检查整体偏弱
- `mypy.ini` 开了 `disallow_untyped_defs`，但白名单仅 6 个维护脚本，~1900 个测试文件、数百个服务模块不受约束。ruff 仅 B/E/F/I/W 且 ignore E501；pre-commit 与 CI 的 ruff/mypy 版本两处手工维护。
- **建议**：按包逐步扩 mypy 白名单（apps/api/services 优先）；ruff 渐进加 RUF/C4/SIM；工具版本提取到单一常量文件。

### M5. except Exception 静默吞咽普遍 + source_tier 窄绕过
- api 非测试代码 278 处 `except Exception`（多数无日志）；`chat.py:401/511` 等静默返回 None。
- `url_ownership.py:303-307`：URL 非法时直接信任调用方 metadata 的 `source_tier`（质量门输入不应信任入参）。
- **建议**：异常至少 `logger.debug` 并收窄类型；`_candidate_with_original_url` 强制重算 source_tier。

### M6. 前端性能债：0 处 React.memo + 列表虚拟化不足
- 2100 行页内大量子组件在父组件每次 state 变化时全量重渲染；仅 `TranscriptTimeline.tsx` 用了虚拟化，`SearchDownload.tsx:1099`、`MyWorkspace.tsx` 等长列表没有。`recharts` 是死依赖；入口 chunk 428KB 无 manualChunks。
- **建议**：重子树（聊天/会话列表）加 memo 或下沉 state；长列表虚拟化；删死依赖；vite 配 manualChunks。

### M7. 治理一致性问题
- `siq_assistant/SOUL.md:28-34` 与 `rules/OPERATING_RULES.md:30-36` 双轨规则已措辞分叉；15 个 profile 仅 1 个有 OPERATING_RULES.md，约定不统一。
- `.gitignore:148-166` 新增放行（data/wiki 27G 已出现 `??` 状态）与 AGENTS.md 安全节声明的例外清单不一致，存在误 `git add` 面。
- `infra/systemd-user/siq-research-engine.service:8-11` 硬编码 `/home/maoyd` 个人路径，无法复用。
- legacy 双轨遗留：`env/backend.env` 与 `infra/env/local.env` 并存（start_all.sh:24-51）、两套 hermes gateway 入口（scripts/hermes vs scripts/openshell）。
- **建议**：规则单一事实源；同步 AGENTS.md 例外清单并把放行收窄到精确文件名；systemd 改 `%h`/模板；为 legacy 分支设迁移截止点。

---

## 🟢 低严重级别

- `apps/api/siq.db`、`apps/api/financial_metrics.db` 二进制 DB 被 git 跟踪，应 gitignore。
- 运行时代码 print 残留：`auth_service.py` 3 处、`agent_memory_service.py` 2 处、`routers/deals.py` 2 处，应改 logging。
- `primary_market_meeting.py:944-948` 把 `str(exc)` 拼进降级文案进入模型上下文，建议脱敏为固定文案。
- `_legacy_evidence_package.py`（582 行）死代码，仅有"不许 import 它"的测试，应删除。
- scripts/ 999 个文件堆积，`scripts/openshell/` 独占 448 个含 poc/patches/build 阶段性产物，建议归档。
- `data/` 69G 仍是主运行态（与 var/ 方向差距大）、`artifacts/` 20G 无 retention 策略、存在 2026-06-29 遗留 stash。
- ios-meeting-capture 提交了 web 构建产物（`ios/App/App/public/assets/`），应 gitignore 改 CI 注入。
- localStorage 键散落 45 处无统一 storage 模块；`as` 断言 541 处偏多；compose 中 grafana 无 healthcheck、redis 无认证；无 dependabot/renovate。

---

## 验证为健康、无需行动的项

- 全仓无 pickle/eval/exec/yaml.load 非安全用法；下载路径有 `safe_path_join` + 扩展名白名单；两个 parser 均有路径穿越校验。
- 密钥管理良好：真实 env 未跟踪、git 历史无 env 文件、CI 有 gitleaks。
- finder 网络层安全设计出色：DNS 固定防 SSRF 重绑定、重定向逐跳重校验、敏感参数脱敏。
- 测试覆盖总体厚实：api 184 个测试文件、finder 13、rules 14、web 94 个单测 + 20 个 e2e spec。
- TODO/FIXME 密度极低（三服务合计 1 处）；docker-compose 健康检查/资源限制齐全；未提交 diff 中未见调试残留。

---

## 建议行动顺序

1. **本周**：H1 拆分提交（解锁一切后续工作）→ H2 注册 profiles → H5 鉴权 fail-fast（安全，改动小）。
2. **下周**：H3 中成本最低的契约常量单源化 + 六市场统一重试封装 → M1 把 hermes 测试挂进 check_all.sh → H7 前端开 strict + Cookie 模式默认化评估。
3. **中期（2–4 周）**：H6 引入 alembic 基线 → H4 上帝文件渐进拆分（先封 CI 上限防继续膨胀）→ M2 检索链路异步化 → H8 start_all.sh 收敛到 supervisor。
4. **持续**：H3 剩余去重（parser-common、市场映射）、M3/M5 质量门加固、运行时目录治理与 retention。
