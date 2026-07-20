# SIQ Research Engine 性能、可扩展性与数据处理深度分析报告

> 分析范围：`/home/maoyd/siq-research-engine`
> 分析性质：只读（未修改任何代码）
> 重点模块：`db/`、`apps/api`、`apps/pdf-parser`、`apps/document-parser`、`scripts/vector-index/milvus-ingestion`

---

## 1. 数据库设计

### 1.1 Schema 结构与分区
- **文件**：`db/ddl/001_create_pdf2md_schema.sql`、`db/ddl/010_create_sec_us_schema.sql`、`db/ddl/060_create_document_parser_schema.sql` 等。
- 采用 **按市场/业务分 schema** 的设计（`pdf2md`、`sec_us`、`document_parser`、`pdf2md_hk`、`edinet_jp`、`dart_kr`、`eu_ifrs`），逻辑清晰，但带来跨市场统一查询的复杂度。
- **无分区表**：全量 DDL 中未出现 `PARTITION BY`（`grep -R "PARTITION" db/ddl/*.sql` 结果为 0）。
  - `pdf2md.content_blocks`、`pdf2md.document_pages`、`pdf2md.document_tables`、`pdf2md.financial_statement_items` 等表会随解析任务线性膨胀，后期单表可能达到数亿行，全表扫描与索引维护成本急剧上升。
  - `sec_us.xbrl_facts_raw`、`sec_us.financial_statement_items` 同样依赖 filing/parse_run 维度增长，缺乏时间或 filing 分区。

### 1.2 JSONB 使用
- **统计**：`db/ddl/*.sql` 中 JSONB 出现约 342 次，GIN 索引约 65 次。
- **使用情况**：
  - 合理使用：将 `raw`、`source`、`quality_summary` 等半结构化、变化频繁的字段放入 JSONB，避免 schema 频繁变更，符合设计意图。
  - 过度/高风险：
    - `pdf2md.documents.quality_summary / financial_summary / resources_summary / raw_task` 等 7+ 个 JSONB 列同时存在，且建有 GIN 索引（`idx_documents_quality_gin`）。GIN 索引写入成本高，会拖慢导入吞吐。
    - `pdf2md.document_tables.raw`、`pdf2md.content_blocks.raw` 等大块 JSONB 建有 GIN 索引（`idx_tables_raw_gin`、`idx_blocks_raw_gin`），每行可能数百 KB，索引体积会远超表体积，且更新为阻塞式。
    - `pdf2md.financial_all_metrics_wide` 把三大表 + key_metrics 打成 JSONB 宽表，并建 GIN(`all_metrics`)，适合读取但写入放大严重。

### 1.3 索引评估
- **合理之处**：
  - 主表基本都有 `(company_id, report_year, ...)`、`(stock_code, report_year, ...)` 复合索引。
  - `sec_us` 事实表有 `(ticker, canonical_name, period_key)`、`(filing_id, concept, context_ref)` 等针对金融查询的索引。
- **不足之处**：
  - `apps/api/models.py` 中 `ChatMessage` 仅对 `session_id` 单列索引，没有 `(session_id, created_at)` 复合索引，历史消息翻页查询会回表扫描。
  - `apps/api/services/usage_service.py` 中 `UsageEvent` 仅有 `user_id`、`event_type`、`event_date` 三个独立索引，没有 `(user_id, event_type, event_date)` 复合索引；且查询代码用 Python 在内存中 `sum`（见 `get_usage_count`），没有利用 SQL `SUM()`。
  - `document_parser.blocks` 有 `(document_id, reading_order)`，但没有 `(document_id, page_number)`，按页查询会全表扫描。

### 1.4 外键与级联
- 外键使用较多且基本带 `ON DELETE CASCADE/SET NULL`，一致性较好。
- 风险：大量级联删除在删除一个 `task_id` 或 `filing_id` 时会触发多表连锁删除；大表下应评估是否改用软删除 + 后台清理。

### 1.5 迁移管理
- `db/migrations/` 目录为空，schema 演进依赖 `db/ddl/*.sql` + `ALTER TABLE ... IF NOT EXISTS`。
- 文件 `001_create_pdf2md_schema.sql` 自身包含大量 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`，说明 schema 是累积式补丁，缺少版本化迁移工具（如 Alembic / yoyo）。
- 风险：多人并行修改 DDL 时难以保证幂等与回滚；生产环境升级缺乏事务化版本控制。

---

## 2. 查询模式（apps/api）

### 2.1 N+1 查询风险
- **文件**：`apps/api/routers/agent_user_router.py`、`apps/api/routers/chat.py`
- 会话列表先 `list_user_sessions` 取 session ids，再对每个 session 查询消息/元数据；虽然 Redis 缓存了一层，但回退到 DB 路径时仍是典型的循环查询。
- `apps/api/services/achievement_checker.py:7`：`check_achievements(session)` 内部执行 `select(Achievement).all()` 后若按用户维度过滤，可能出现 ORM 级 N+1（取决于调用方）。

### 2.2 未分页/大查询
- **文件**：`apps/api/services/agent_chat_runtime_impl.py`
  - `select(ChatMessage).where(ChatMessage.session_id == session_id).order_by(ChatMessage.id)` 后 `result.all()`（多处），长会话消息数可达数千条，全部加载到内存再处理。
  - `_iter_pdf2md_task_infos()` 递归扫描 wiki/results 目录，无分页、无流式，目录下文件数多时阻塞事件循环。
- **文件**：`apps/api/services/usage_service.py:101`
  - `get_usage_count` 把当日所有 UsageEvent 行拉取后 Python 求和，未使用 SQL 聚合。
- **文件**：`apps/api/services/agent_memory_service.py`
  - 多处 `result.mappings().all()` 未限制返回行数。

### 2.3 同步阻塞与事件循环
- **文件**：`apps/api/database.py:27-28`
  - 同时创建同步 `engine` 与异步 `async_engine`。
- **统计**：约 21 个文件使用 `Session(engine)`（同步），30 个文件使用 `AsyncSession`。
- **关键问题**：
  - `apps/api/services/agent_chat_runtime_impl.py`（6282 行，核心聊天/投委会逻辑）中大量函数为同步 `def`，包括文件读取、子进程调用、SQLModel 同步会话、同步 `httpx.Client` 等；这些函数被 async 路由直接调用时会阻塞整个 FastAPI 事件循环。
  - `apps/api/services/vector_retrieval.py:60`：`_embed_query` 使用 `with httpx.Client() as client:` 同步请求 embedding 服务。
  - `apps/api/services/external_research_clients.py`：`_exa_search`、`_tavily_search`、`_qcc_search` 使用同步 `httpx.Client`。
  - `apps/api/services/rerank_provider.py`：同样使用同步 `httpx.Client`。

### 2.4 慢查询风险点
- `sec_us.v_latest_company_reports` 等视图使用 `distinct on (f.company_id, coalesce(f.report_type, f.form))` + 子查询排序，数据量大时性能会退化。
- `pdf2md.financial_items_enriched` 的构建（`db/dml/002_build_financial_items_enriched.sql`）使用 `UNION ALL` 三大事实表 + `LEFT JOIN` `companies`/`non_a_share_companies`/`document_tables`，且包含大量 `CASE WHEN`/`regexp_replace`；作为全量重建脚本，每次运行都会全表扫描，不适合增量更新。

---

## 3. PDF/文档解析性能

### 3.1 单线程/单 Worker 瓶颈
- **文件**：`apps/pdf-parser/pdf_parser_app_impl.py:641-675`
  - 仅一个 `_queue_worker_loop` 线程，通过 `_has_active_upstream_task()` 保证同时只有 1 个任务提交给 MinerU。
  - 本地结果合并、质量报告、财务抽取等环节也串行执行。
- **文件**：`apps/document-parser/app.py:884-910`
  - 仅一个 `document-parser-worker` 线程，`claim_next_queued_task()` 原子取出一个任务后串行 `_process_task`。
  - 这意味着无论部署多少 CPU/GPU，文档解析队列的吞吐都被限制在“单任务串行”。

### 3.2 大文件一次性读入内存
- **文件**：`apps/pdf-parser/pdf_parser_app_impl.py:1494-1501`、`_load_json_artifact` 链
  - `document_full.json`、`content_list.json`、`content_list_enhanced.json`、`table_relations.json` 等均通过 `json.loads(path.read_text(...))` 全量加载。
  - `result.md`、`result_complete.md` 通过 `path.read_text(...)` 全量读取（如质量服务、脚本诊断）。
- **文件**：`apps/document-parser/mineru_import.py`
  - `content_list.json`、`middle.json`、`metadata.json` 同样全量读取。
- 风险：大型年报 PDF 解析后的 `document_full.json` 可达数十 MB 至数百 MB，内存占用高；多任务并发时易 OOM。

### 3.3 重复解析/重复加载
- **文件**：`apps/pdf-parser/pdf_parser_runtime_utils.py:68-113`
  - 虽然实现了 `FileCache`（默认最大 32 项），但：
    - 缓存项是完整文件内容，大文件会快速占满缓存并逐出。
    - 仅存在于 pdf-parser；document-parser 未见类似缓存。
- **文件**：`apps/pdf-parser/pdf_parser_app_impl.py:185`
  - `FILE_CACHE_MAX_ITEMS = 32`，可配置但默认偏小。

### 3.4 流式处理的亮点与不足
- **亮点**：`apps/pdf-parser/mineru_client.py:44-98` 的 `stream_multipart_post` 以 1MB 块流式上传 PDF，避免了大文件上传内存问题。
- **不足**：上传后的结果下载、JSON 解析、Markdown 处理均未流式化；服务内部数据传输仍依赖全量 JSON。

---

## 4. 向量检索（Milvus）

### 4.1 写入策略与批量大小
- **文件**：`scripts/vector-index/milvus-ingestion/ingest_document_chunks.py:335`
  - 默认 `batch_size = 32`（`SIQ_DOCUMENT_VECTOR_BATCH_SIZE`）。
- **文件**：`scripts/vector-index/milvus-ingestion/ingest_sec_wiki_chunks.py`
  - 默认 `batch_size = 32`（`SIQ_SEC_VECTOR_BATCH_SIZE`）。
- **文件**：`scripts/vector-index/milvus-ingestion/scripts/embedding_client.py:133-161`
  - API embedding 硬编码 `batch_size = 16`；`embed_chunks` 硬编码 32。
- **评估**：对于本地 vLLM / 自托管 embedding 服务，32 的批量偏小，GPU 利用率低；对于云 API（DashScope 等），4 的批量又会导致大量 RTT。

### 4.2 索引类型
- **文件**：`scripts/vector-index/milvus-ingestion/ingest_document_chunks.py:262`
  - 使用 HNSW：`{"M": 16, "efConstruction": 128}`，metric 为 IP。
  - HNSW 是合理的近似最近邻选择，但构建参数（M=16）对千万级向量略显保守，检索参数 `ef=128`（见 `apps/api/services/vector_retrieval.py:154`）可动态调优。
- **问题**：每个 ingestion 脚本各自调用 `init_collection`，集合 schema 散落在多个文件，版本管理困难。

### 4.3 Embedding 调用效率
- **文件**：`scripts/vector-index/milvus-ingestion/ingest_document_chunks.py:219-234`
  - 使用同步 `requests.post(..., timeout=180)`，单线程顺序 embedding；大量 chunks 时整体耗时高。
- **文件**：`scripts/vector-index/milvus-ingestion/scripts/embedding_client.py`
  - 多后端支持（local/openai/dashscope/siliconflow），但 local 后端加载 `sentence-transformers` 模型到进程内；API 后端均为同步调用。
  - 未使用异步 `aiohttp`/`httpx.AsyncClient` 并发请求，也未对本地模型做 batching 优化。

### 4.4 连接管理
- **文件**：`apps/api/services/vector_retrieval.py:134-162`
  - 每次检索都新建 Milvus 连接：`connections.connect(alias=alias, ...)`、`Collection(...)`、`collection.load()`。
  - 无连接池，高并发查询时会反复建连、加载集合，延迟抖动大。
- **文件**：`scripts/vector-index/milvus-ingestion/ingest_document_chunks.py:237-265`
  - ingestion 脚本同样每运行一次新建连接，但短期运行可接受。

---

## 5. 缓存策略

### 5.1 已有缓存
- **Redis 会话缓存**：`apps/api/services/session_manager.py`
  - 用于用户会话元数据、当前会话、会话列表，支持 Redis 不可用时回退到内存字典。
  - 配置项：Redis URL、TTL、用户保留数量（默认 100）。
- **pdf-parser 文件缓存**：`apps/pdf-parser/pdf_parser_runtime_utils.py:68`
  - 进程内 LRU，最多 32 个完整文件内容。

### 5.2 明显缺失的热点缓存
- **公司/证券主数据**：`pdf2md.companies`、`sec_us.companies` 查询频繁，目前未缓存；每次解析入库、每次聊天检索都可能 JOIN。
- **最新财报 filing**：`v_latest_company_reports`、`v_latest_parse_runs` 视图每次实时计算，没有物化视图或缓存。
- **embedding 结果**：同一 chunk 重复 embedding 无缓存，文档更新少量内容时会全量重新 embedding。
- **向量检索结果**：`retrieve_vector_hits` 每次实时查询 Milvus，无查询缓存。
- **market report finder 代理结果**：`apps/api/services/market_report_proxy.py` 中对外部服务的每次请求都实时转发，没有短期缓存。

---

## 6. 并发与异步

### 6.1 FastAPI 利用不足
- `apps/api/main.py` 注册了数十个路由器，大量路由声明为 `async def`，但底层服务层混用同步实现。
- 典型阻塞点：
  - 同步 SQLModel `Session(engine)` 在 async 路由中直接使用（如 `apps/api/routers/source.py:108-123` 的 `_token_user`）。
  - 同步文件 I/O、同步 subprocess（`apps/api/routers/market_reports.py` 大量 `subprocess.run`）。
  - 同步 HTTP 客户端（`httpx.Client`、`requests`）。

### 6.2 线程池/进程池使用
- 全库搜索 `ProcessPoolExecutor`、`ThreadPoolExecutor`、`asyncio.gather`、`run_in_executor` 极少：
  - 仅 `apps/api/routers/agent_user_router.py` 使用 `loop.run_in_executor(None, _write)` 写文件。
  - 仅 `apps/api/services/system_status.py` 使用 `asyncio.gather` 并发检查服务状态。
  - 没有针对 CPU 密集型解析任务（PDF 后处理、财务抽取、质量报告）的进程池。

### 6.3 解析服务本身非异步
- `apps/pdf-parser` 与 `apps/document-parser` 均为 Flask 应用，运行在同步 WSGI 模型下；即使部署多 worker（gunicorn），每个 worker 内部仍是单线程事件循环（Flask 默认）。
- document-parser 的 worker 是单线程，pdf-parser 的上游提交队列也是单线程，整个解析吞吐受限于这两处串行瓶颈。

---

## 7. 数据流水线（披露下载 → 入库）

### 7.1 链路瓶颈
- **下载**：`services/market-report-finder` 统一下载；脚本侧 `scripts/ops/download_*_2025_*.py` 负责批量入队。
- **解析**：pdf-parser / document-parser 单 worker 串行处理。
- **入库**：
  - `db/imports/import_document_full_to_postgres.py`（1941 行）单文件承担 A 股 document_full 全量解析、公司匹配、财务事实抽取，逻辑复杂。
  - `db/imports/import_market_document_full_to_postgres.py` 通过 `MarketDocumentFullWriter` 单连接串行写入。
- **全链路缺少背压控制**：下载端可以批量入队，但解析端吞吐固定，队列会无限堆积。

### 7.2 幂等/去重
- 数据库层：`ON CONFLICT (task_id) DO UPDATE`、`ON CONFLICT (source_table, task_id, ...)` 提供了基本幂等写入。
- 应用层：
  - `apps/api/routers/workspace.py` 实现了 PDF 上传去重（`_pdf_dedupe_key`）。
  - `apps/document-parser/app.py`、pdf-parser 脚本有 `requeue_interrupted_tasks`，可将中断任务重新入队。
- **不足**：去重键多为 task_id/filing_id，缺少基于 PDF 内容 SHA256 的全局去重；同一文件重命名后会重复解析。

### 7.3 重试
- `apps/api/routers/market_reports.py` 有 market report assist 的 retry 逻辑（`_attempt` + `retry_index`）。
- `apps/document-parser/app.py` 提供 `/api/retry/<task_id>` 端点。
- **不足**：
  - 没有统一的指数退避/熔断机制。
  - 重试状态依赖 SQLite/Postgres 状态字段，缺少独立的重试队列与死信队列。
  - MinerU / embedding / 外部搜索等外部依赖的失败没有分级重试。

### 7.4 断点续传
- 解析任务粒度有 `task_id` 与状态机，可在任务失败后重新入队，属于任务级断点。
- **缺失**：
  - 大文件上传无断点续传；100MB/200MB 文件一旦失败需重新上传。
  - 批量 ingestion 脚本一旦中断，无法从已处理 chunk 继续，只能全量重跑或依赖 Milvus upsert 幂等。

---

## 8. 大对象与内存

### 8.1 大 JSON / Markdown 全量加载
- `document_full.json`、`content_list.json`、`middle.json`、`result.md`、`result_complete.md` 均通过 `read_text()`/`read_bytes()` 全量读入内存。
- `apps/pdf-parser/scripts/diagnose_content_list_quality.py`、`scripts/evaluate_parse_quality.py` 等运维脚本同样全量读取。
- 风险：数百页年报的 `result.md` 可达 10MB+，`document_full.json` 可达 100MB+；多任务并发时 JVM/Python 堆内存压力巨大。

### 8.2 缺少流式处理
- JSON 解析未使用 `ijson`、`orjson` 流式接口；全部使用标准 `json.loads`。
- Markdown 处理未使用生成器/行迭代；全文正则、全文 split 频繁。
- 数据库 `COPY`/`批量 insert` 已有，但前置的数据准备阶段仍是全量内存操作。

### 8.3 SQLite 作为大对象元数据存储
- `apps/document-parser/task_store.py` 使用 SQLite 存储任务状态、日志；单文件 SQLite 在并发写与大量任务历史下会成为瓶颈。
- `apps/pdf-parser` 的 task store 同样基于 SQLite（`pdf_parser_task_repository.py`）。

### 8.4 对象存储缺失
- PDF、图片、产物均存储在本地文件系统（`data/pdf-parser/results`、`data/document-parser/...`）。
- 没有抽象出 S3/MinIO/OSS 对象存储层，横向扩容与多实例部署困难。

---

## 9. 性能优化建议（8 条）

| # | 优化方向 | 具体措施 | 预期收益 | 实施难度 |
|---|---------|---------|---------|---------|
| 1 | **大表分区** | 对 `pdf2md.content_blocks`、`pdf2md.document_pages`、`pdf2md.document_tables`、`pdf2md.financial_statement_items`、`sec_us.xbrl_facts_raw` 等按 `report_year` 或 `filing_id` 做范围/哈希分区；删除过期数据时可直接 DROP 分区。 | 查询性能提升 3-10×，维护成本下降，避免单表过大。 | 中（需停机重建或在线迁移） |
| 2 | **索引精细化** | 添加 `(session_id, created_at)`、`(user_id, event_type, event_date)`、`(document_id, page_number)` 等复合索引；移除低频使用的 GIN 索引（如 `idx_tables_raw_gin`）或改为部分索引。 | 消除全表扫描与回表，API 响应提升 30-70%。 | 低 |
| 3 | **全链路异步化** | 将 `apps/api/services/agent_chat_runtime_impl.py` 中的同步文件 I/O、同步 DB、同步 HTTP 调用改为 async；统一使用 `AsyncSession` 与 `httpx.AsyncClient`；同步阻塞操作放入 `loop.run_in_executor`。 | 提升 FastAPI 并发吞吐 2-5×，降低长尾延迟。 | 高（涉及面广） |
| 4 | **解析并发化** | 将 pdf-parser / document-parser 的单 worker 线程改为可配置的多 worker / 多进程模型；CPU 密集型后处理（质量报告、财务抽取）使用 `ProcessPoolExecutor`；GPU 推理保持队列但允许并行提交多个任务。 | 解析吞吐提升 N 倍（取决于 CPU/GPU），消除队列积压。 | 高 |
| 5 | **大文件流式处理** | 对 `document_full.json`、`content_list.json`、`result.md` 使用流式 JSON 解析（`ijson`/`orjson` 增量）和生成器处理 Markdown；避免 `path.read_text()` 全量加载。 | 内存占用下降 50-90%，支持更大文件与更高并发。 | 中 |
| 6 | **向量 ingestion 优化** | 增大 embedding batch size（本地 vLLM 建议 128-512，云 API 建议 16-32）；使用 `httpx.AsyncClient`/`aiohttp` 并发请求；Milvus 连接池化，避免每次检索新建连接；对 embedding 结果做缓存。 | ingestion 吞吐提升 3-10×，检索延迟下降 50%+。 | 中 |
| 7 | **引入 Redis 缓存层** | 缓存公司主数据、最新 filing、向量检索结果、market finder 响应；设置合理的 TTL；缓存失效与 DB 写入联动。 | 热点查询延迟从 100ms+ 降至 <10ms，外部依赖压力下降。 | 中 |
| 8 | **统一任务队列与重试** | 引入 Celery / RQ / 自研队列，替代单线程 worker；实现指数退避、最大重试、死信队列；任务状态持久化到 Postgres 而非 SQLite，支持多实例与断点续传。 | 系统可用性与可观测性提升，支持水平扩展。 | 高 |

---

## 10. 总结

SIQ Research Engine 当前处于“功能完整、性能尚可”的阶段，核心瓶颈集中在：
1. **解析层串行**：单 worker 线程严重限制吞吐。
2. **API 层混合同步/异步**：FastAPI 事件循环被大量同步 I/O 阻塞。
3. **大对象全量内存处理**：JSON/Markdown 全量加载是内存与延迟的主要杀手。
4. **数据库缺少分区与大表治理**：随着解析量增长，查询与维护成本将指数级上升。
5. **缓存与队列基础设施不足**：Redis 仅用于会话，未覆盖热点业务数据；缺少统一重试/断点续传机制。

建议按“先索引/分区 → 再流式化/缓存 → 最后异步化/队列化”的顺序分阶段实施，优先解决第 1、2、3 项，可在较短时间内获得显著性能提升。
