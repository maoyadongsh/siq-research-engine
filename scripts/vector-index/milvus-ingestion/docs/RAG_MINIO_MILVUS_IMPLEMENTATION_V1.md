# RAG + MinIO + PostgreSQL + Milvus 实施文档 V1

更新日期：2026-04-17  
适用环境：局域网私有化部署  
当前基础：已存在 [ingest_final.py](/home/maoyd/.openclaw/workspace/ic_master_coordinator_workspace/ingest_final.py)

> 2026-07-06 说明：本文是 OpenClaw 时代的底层向量/RAG 参考，不是 Hermes IC profile 的运行合同。一级市场智能体运行时应通过 Deal OS 的 `deal_retrieval.py`、`vector_retrieval.py`、`rerank_provider.py` 和 startup receipt API 接入，不迁移本地脚本、缓存、凭证或会话状态。

## 1. 目标

本方案用于把现有 `Milvus` 向量入库能力升级为一套可持续运行的本地 RAG 系统，满足以下目标：

- 支持原文件统一存储与版本管理
- 支持多格式文档解析、切块、向量化入库
- 支持基于证据生成报告
- 支持报告 citation 回源并打开原文件
- 支持失败重试、重建索引、后续扩展 rerank 与审计

本方案不是推倒重来，而是在你现有的 `ingest_final.py` 基础上逐步演进。

## 2. 硬件与角色分工

### 2.1 推荐部署角色

- `DGX Spark`
  - embedding 服务
  - rerank 服务
  - 报告生成 LLM 服务
  - 可选 OCR 重型任务

- `Mac mini`
  - `FastAPI` 业务服务
  - `PostgreSQL`
  - `MinIO`
  - ingest worker
  - report worker
  - 定时任务与备份脚本

- `Synology NAS`
  - PostgreSQL 逻辑备份
  - MinIO 冷备份
  - 归档报告

- `Windows 客户端`
  - 文件上传
  - 报告查看
  - 检索与引用跳转

### 2.2 不建议的做法

- 不建议把 `MinIO` 主数据盘直接放到 NAS 挂载目录上跑在线主服务
- 不建议让 DGX 同时承载数据库和对象存储
- 不建议第一阶段引入 Kafka、ES、复杂微服务编排

## 3. 最终系统结构

### 3.1 逻辑结构

```text
Windows / Browser
    -> FastAPI Gateway (Mac mini)
        -> MinIO (原文件、解析产物)
        -> PostgreSQL (元数据、任务、引用、报告)
        -> Milvus (向量检索)
        -> Model APIs on DGX (embedding / rerank / llm)
    -> NAS (备份与归档)
```

### 3.2 职责边界

- `MinIO`
  - 存原文件本体
  - 存解析结果
  - 存页图、OCR 结果、预览产物

- `PostgreSQL`
  - 存文档记录
  - 存 chunk 与页映射
  - 存入库任务
  - 存报告与 citation

- `Milvus`
  - 仅负责向量召回
  - 不作为业务真相源

- `LLM / Embedding / Rerank`
  - 运行在 DGX
  - 通过 HTTP API 供业务服务调用

## 4. 部署

### 4.1 第一阶段部署原则

第一阶段只部署 5 类核心组件：

- `postgres`
- `minio`
- `api`
- `worker`
- `milvus`

其中：

- 你已有 `Milvus`，可先保留现状
- `api` 与 `worker` 部署在 `Mac mini`
- 模型服务部署在 `DGX`

### 4.2 目录规划

推荐在 `Mac mini` 上使用如下目录：

```text
/opt/siq-rag/
  compose/
  env/
  data/
    postgres/
    minio/
    logs/
  backups/
  scripts/
```

项目代码仓建议保留在：

```text
/home/maoyd/.openclaw/workspace/ic_master_coordinator_workspace/
```

### 4.3 环境变量

统一定义一个 `.env`：

```env
POSTGRES_DB=siq_rag
POSTGRES_USER=siq
POSTGRES_PASSWORD=change_me_now

MINIO_ROOT_USER=minioadmin
MINIO_ROOT_PASSWORD=change_me_now
MINIO_ENDPOINT=http://127.0.0.1:9000

MILVUS_HOST=127.0.0.1
MILVUS_PORT=19530

EMBEDDING_API_BASE=http://DGX_IP:8000
RERANK_API_BASE=http://DGX_IP:8001
LLM_API_BASE=http://DGX_IP:8002

RAW_BUCKET=raw-documents
DERIVED_BUCKET=derived-artifacts
```

### 4.4 Docker Compose 基线

第一版建议 `Mac mini` 先用 `docker compose` 跑 `postgres` 和 `minio`。  
如果你的 `api/worker` 暂时还在本地 Python 环境跑，也可以先不放进 compose。

建议新增：

```yaml
services:
  postgres:
    image: postgres:16
    container_name: siq-postgres
    restart: unless-stopped
    environment:
      POSTGRES_DB: ${POSTGRES_DB}
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    ports:
      - "5432:5432"
    volumes:
      - /opt/siq-rag/data/postgres:/var/lib/postgresql/data

  minio:
    image: minio/minio:latest
    container_name: siq-minio
    restart: unless-stopped
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: ${MINIO_ROOT_USER}
      MINIO_ROOT_PASSWORD: ${MINIO_ROOT_PASSWORD}
    ports:
      - "9000:9000"
      - "9001:9001"
    volumes:
      - /opt/siq-rag/data/minio:/data
```

### 4.5 启动顺序

按照这个顺序启动：

1. PostgreSQL
2. MinIO
3. Milvus
4. DGX 模型接口
5. API
6. worker

### 4.6 端口建议

- PostgreSQL: `5432`
- MinIO API: `9000`
- MinIO Console: `9001`
- FastAPI: `8080`
- DGX Embedding: `8000`
- DGX Rerank: `8001`
- DGX LLM: `8002`

## 5. 建库

### 5.1 为什么选 PostgreSQL

本方案中，`PostgreSQL` 是除 `Milvus` 之外唯一必须新增掌握的数据库。  
它负责文档元数据、任务状态、报告与引用，是整个系统的业务真相源。

### 5.2 建库命令

```sql
create database siq_rag;
```

### 5.3 核心表结构

#### 5.3.1 documents

```sql
create table documents (
    doc_id            varchar(64) primary key,
    tenant_id         varchar(64) default 'default',
    title             text not null,
    file_name         text not null,
    bucket            varchar(128) not null,
    object_key        text not null,
    object_version_id varchar(256),
    mime_type         varchar(128),
    file_size         bigint,
    file_hash         varchar(128) not null,
    source_type       varchar(64) not null,
    source_uri        text,
    parser_version    varchar(64),
    doc_version       int not null default 1,
    ingest_status     varchar(32) not null default 'uploaded',
    failed_stage      varchar(64),
    error_message     text,
    is_deleted        boolean not null default false,
    created_at        timestamptz not null default now(),
    updated_at        timestamptz not null default now()
);

create index idx_documents_status on documents(ingest_status);
create index idx_documents_tenant on documents(tenant_id);
create index idx_documents_hash on documents(file_hash);
```

#### 5.3.2 document_pages

```sql
create table document_pages (
    page_id             varchar(64) primary key,
    doc_id              varchar(64) not null references documents(doc_id),
    page_no             int not null,
    page_text           text,
    preview_bucket      varchar(128),
    preview_object_key  text,
    width               int,
    height              int,
    created_at          timestamptz not null default now()
);

create unique index uq_document_pages on document_pages(doc_id, page_no);
```

#### 5.3.3 document_chunks

```sql
create table document_chunks (
    chunk_id           varchar(64) primary key,
    doc_id             varchar(64) not null references documents(doc_id),
    chunk_index        int not null,
    page_no_start      int,
    page_no_end        int,
    section_title      text,
    char_start         int,
    char_end           int,
    token_count        int,
    chunk_text         text not null,
    retrieval_text     text not null,
    chunk_hash         varchar(128) not null,
    embedding_status   varchar(32) not null default 'pending',
    created_at         timestamptz not null default now()
);

create unique index uq_document_chunks_doc_idx on document_chunks(doc_id, chunk_index);
create index idx_document_chunks_doc on document_chunks(doc_id);
create index idx_document_chunks_embedding_status on document_chunks(embedding_status);
```

#### 5.3.4 ingest_jobs

```sql
create table ingest_jobs (
    job_id             varchar(64) primary key,
    doc_id             varchar(64) references documents(doc_id),
    job_type           varchar(32) not null,
    status             varchar(32) not null,
    current_stage      varchar(64),
    retry_count        int not null default 0,
    error_message      text,
    payload_json       jsonb,
    created_at         timestamptz not null default now(),
    updated_at         timestamptz not null default now()
);

create index idx_ingest_jobs_status on ingest_jobs(status);
create index idx_ingest_jobs_doc on ingest_jobs(doc_id);
```

#### 5.3.5 reports

```sql
create table reports (
    report_id          varchar(64) primary key,
    report_type        varchar(64) not null,
    user_query         text not null,
    filters_json       jsonb,
    result_json        jsonb not null,
    rendered_markdown  text,
    rendered_html      text,
    status             varchar(32) not null default 'done',
    created_at         timestamptz not null default now()
);
```

#### 5.3.6 report_citations

```sql
create table report_citations (
    citation_id        varchar(64) primary key,
    report_id          varchar(64) not null references reports(report_id),
    evidence_id        varchar(64) not null,
    doc_id             varchar(64) not null references documents(doc_id),
    chunk_id           varchar(64) references document_chunks(chunk_id),
    page_no            int,
    quote_text         text,
    title              text,
    created_at         timestamptz not null default now()
);

create index idx_report_citations_report on report_citations(report_id);
create index idx_report_citations_doc on report_citations(doc_id);
```

### 5.4 状态约定

`documents.ingest_status`：

- `uploaded`
- `parsing`
- `parsed`
- `embedding`
- `ready`
- `failed`

`document_chunks.embedding_status`：

- `pending`
- `done`
- `failed`

`ingest_jobs.status`：

- `queued`
- `running`
- `done`
- `failed`

## 6. 建桶

### 6.1 Bucket 规划

只创建两个 bucket：

- `raw-documents`
- `derived-artifacts`

### 6.2 职责

`raw-documents`：

- PDF
- DOCX
- XLSX
- Markdown
- TXT
- 原始图片

`derived-artifacts`：

- `parsed/document.json`
- OCR 中间结果
- PDF 页图
- 预览图
- 可选结构化解析结果

### 6.3 对象 Key 规范

```text
raw-documents/default/{doc_id}/v{doc_version}/source/{file_name}
derived-artifacts/default/{doc_id}/v{doc_version}/parsed/document.json
derived-artifacts/default/{doc_id}/v{doc_version}/pages/{page_no}.png
derived-artifacts/default/{doc_id}/v{doc_version}/ocr/{page_no}.json
```

### 6.4 MinIO 配置要求

- 开启 bucket versioning
- 不覆盖已有版本
- 原文件与衍生产物分桶
- 通过预签名 URL 暴露下载或预览

### 6.5 初始化命令示例

如果使用 `mc`：

```bash
mc alias set local http://127.0.0.1:9000 minioadmin change_me_now
mc mb local/raw-documents
mc mb local/derived-artifacts
mc version enable local/raw-documents
mc version enable local/derived-artifacts
```

## 7. 服务拆分

### 7.1 第一阶段建议

第一阶段不要拆成很多仓库，先保留一个仓库，新增几个模块目录：

```text
services/
  api/
  worker/
  retrieval/
  reporting/
  storage/
db/
  migrations/
ops/
  compose/
  scripts/
```

### 7.2 服务职责

#### 7.2.1 gateway-api

职责：

- 上传文件
- 查询文档状态
- 发起报告生成
- 查询 citation
- 生成 MinIO 预签名链接

推荐技术：

- `FastAPI`
- `Pydantic`
- `SQLAlchemy` 或 `psycopg`

#### 7.2.2 ingest-worker

职责：

- 消费 `ingest_jobs`
- 拉取 MinIO 原文件
- 调用解析器
- 生成 chunk
- 写 PostgreSQL
- 调 embedding
- 写 Milvus

#### 7.2.3 retrieve-service

职责：

- query 规范化
- 调 Milvus 初召回
- 元数据过滤
- rerank
- evidence pack 拼装

第一阶段可以先作为 `api` 内部模块，不必单独部署。

#### 7.2.4 report-service

职责：

- 调 LLM 生成结构化报告
- 校验 evidence 引用
- 渲染 Markdown / HTML
- 保存 `reports` 与 `report_citations`

#### 7.2.5 file-service

职责：

- 根据 `doc_id` 生成原文件访问地址
- 返回页图预览地址
- 支持引用点击回源

第一阶段可以并入 `gateway-api`。

## 8. 现有 ingest_final.py 的演进方案

### 8.1 现状判断

你现有 [ingest_final.py](/home/maoyd/.openclaw/workspace/ic_master_coordinator_workspace/ingest_final.py) 已经具备这些基础能力：

- 多格式解析
- 动态 chunk 策略
- OCR 兜底
- Milvus collection 初始化
- embedding 后端切换
- metadata 结构写入
- 进度文件保存

这说明它已经很接近 `ingest-worker` 的核心原型。

### 8.2 保留的部分

以下能力建议直接保留并抽成可复用模块：

- `_smart_chunk`
- `_split_text`
- `_parse_pdf`
- `_parse_docx`
- `_parse_plain_text`
- embedding fetch 系列函数
- file hash / progress 逻辑

### 8.3 需要调整的部分

当前脚本偏“单脚本直接入 Milvus”，下一阶段要改为“先写 MinIO / PostgreSQL，再写 Milvus”。

改造方向：

1. 将文件入口改为 `doc_id + bucket + object_key`
2. 先把原文件传到 MinIO
3. 再从 MinIO 或本地 staging 目录解析
4. 把 chunk metadata 持久化到 PostgreSQL
5. Milvus 只写最小必要字段
6. 原 `.progress_{collection}.json` 逐步迁移到 `ingest_jobs`

### 8.4 推荐拆分为 4 个内部模块

```text
ingestion/
  parsers.py
  chunking.py
  embeddings.py
  milvus_indexer.py
```

再新增：

```text
storage/
  minio_client.py
  pg_repo.py
```

### 8.5 推荐阶段式迁移

#### 阶段 A

保留 `ingest_final.py` 主流程，仅新增：

- MinIO 上传
- PostgreSQL `documents` 记录

#### 阶段 B

在 `process_file()` 中补充：

- `document_pages`
- `document_chunks`
- `ingest_jobs`

#### 阶段 C

把 UI 逻辑与 worker 逻辑拆开：

- `gradio` 继续保留为管理界面
- worker 改为后台任务入口

#### 阶段 D

逐步弃用 `.progress_*.json`，统一改为数据库任务状态。

## 9. 数据流

### 9.1 入库数据流

```text
上传文件
-> MinIO raw-documents
-> documents 写库
-> ingest_jobs 入队
-> worker 解析原文
-> parsed/document.json 写 MinIO
-> pages/chunks 写 PostgreSQL
-> embedding 调 DGX
-> 向量写 Milvus
-> documents 状态更新为 ready
```

### 9.2 检索生成数据流

```text
用户提问
-> query 规范化
-> embedding
-> Milvus top30 召回
-> PostgreSQL 补全文档与 chunk 信息
-> rerank
-> 合并 evidence
-> LLM 生成结构化报告
-> reports / report_citations 写库
-> citation 点击后生成 MinIO 预签名 URL
-> 打开原文件
```

### 9.3 删除与更新规则

- 文档删除先逻辑删除
- 同名文件但 hash 不同视为新版本
- 报告引用必须保留旧版本可访问能力
- 不允许覆盖已被报告引用的历史对象

## 10. API 设计

### 10.1 文档上传

`POST /api/documents/upload`

请求：

- multipart file
- `source_type`
- `tenant_id`

处理：

1. 上传 MinIO
2. 写 `documents`
3. 写 `ingest_jobs`
4. 返回 `doc_id`

### 10.2 查询文档详情

`GET /api/documents/{doc_id}`

返回：

- 文档元数据
- 入库状态
- 当前版本

### 10.3 打开原文件

`GET /api/documents/{doc_id}/open`

处理：

- 从 PostgreSQL 查 `bucket/object_key/object_version_id`
- 生成预签名 URL

### 10.4 重新入库

`POST /api/ingest/retry/{doc_id}`

支持：

- 从解析阶段重试
- 从 embedding 阶段重试

### 10.5 生成报告

`POST /api/reports/generate`

输入：

- `query`
- `filters`
- `report_type`

输出：

- `report_id`
- 结构化结果
- citation 列表

### 10.6 查看 citation

`GET /api/citations/{citation_id}`

返回：

- `doc_id`
- `chunk_id`
- `page_no`
- `quote_text`
- `open_url`

## 11. 检索策略

### 11.1 第一阶段默认策略

- query embedding
- Milvus `top30`
- 按文档类型、时间、版本过滤
- rerank 到 `top8`
- 合并为 `3-6` 个 evidence
- LLM 输出结构化结果

### 11.2 chunk 策略

与现有脚本保持一致并逐步优化：

- 目标 `300-600 tokens`
- 最大 `800 tokens`
- overlap `80-120 tokens`
- PDF 尽量不跨页
- DOCX / MD 按标题聚合
- OCR 页走更保守 chunk

### 11.3 retrieval_text 规则

`retrieval_text` 推荐结构：

```text
文档标题：{title}
章节：{section_title}
正文：{chunk_text}
```

这样更利于召回与后续 rerank。

## 12. 报告生成协议

### 12.1 evidence pack 格式

```json
[
  {
    "evidence_id": "ev_001",
    "doc_id": "doc_001",
    "title": "项目周报.pdf",
    "page_no": 6,
    "chunk_ids": ["ck_012", "ck_013"],
    "section_title": "本周进展",
    "text": "本周完成需求分析，并完成数据接入联调准备。下周开始测试环境联调。"
  }
]
```

### 12.2 模型输出协议

```json
{
  "title": "项目进展与风险报告",
  "summary": [
    {
      "claim": "本月已完成需求分析，并完成数据接入联调准备。",
      "citations": ["ev_001"]
    }
  ],
  "risks": [
    {
      "claim": "测试启动依赖联调完成，存在进度顺延风险。",
      "citations": ["ev_001", "ev_002"]
    }
  ],
  "gaps": [
    {
      "claim": "当前证据无法确认联调是否已经完成。",
      "citations": ["ev_001"]
    }
  ]
}
```

### 12.3 系统约束

- 只能依据 evidence 输出
- 不允许编造来源
- 每条 claim 至少一个 citation
- 输出不合法时自动重试一次

## 13. 备份与恢复

### 13.1 PostgreSQL

每天夜间逻辑备份：

```bash
pg_dump -Fc -h 127.0.0.1 -U siq -d siq_rag -f /opt/siq-rag/backups/siq_rag_$(date +%F).dump
```

然后同步到 NAS。

### 13.2 MinIO

建议每天将对象同步到 NAS 备份目录。  
第一阶段可以使用 `mc mirror`。

### 13.3 恢复优先级

恢复顺序：

1. PostgreSQL
2. MinIO
3. Milvus 重建索引

因为 `Milvus` 可以由 `document_chunks` 重新生成，不需要把它作为唯一恢复源。

## 14. 里程碑

### 里程碑 M1：基础设施就绪

目标：

- PostgreSQL 可用
- MinIO 可用
- bucket 初始化完成
- 基础网络连通

验收：

- 能上传并取回一个测试文件
- 能连通 PostgreSQL 和 MinIO

### 里程碑 M2：入库闭环打通

目标：

- `documents` / `ingest_jobs` 入库
- 原文件进入 MinIO
- `document_chunks` 成功写库
- Milvus 成功写向量

验收：

- 任意 PDF / DOCX 成功入库
- `doc_id` 能查到 chunk 与页信息

### 里程碑 M3：检索与 citation 打通

目标：

- query 检索
- evidence pack
- citation 回源

验收：

- 检索结果可追溯到 `doc_id/chunk_id/page_no`
- 点击 citation 可打开原文件

### 里程碑 M4：报告生成打通

目标：

- LLM 生成结构化报告
- `reports` / `report_citations` 落库
- Markdown / HTML 渲染

验收：

- 生成一份报告
- 每条关键结论都能看到来源

### 里程碑 M5：生产化优化

目标：

- rerank
- 重试机制
- 每日备份
- 版本治理

验收：

- 失败任务可重试
- 可恢复历史文档

## 15. 立即开工清单

按先后顺序执行：

1. 在 `Mac mini` 部署 `PostgreSQL` 与 `MinIO`
2. 建库 `siq_rag`
3. 创建 6 张核心表
4. 建两个 bucket 并开启 versioning
5. 为现有 `ingest_final.py` 增加 MinIO 上传与 `documents` 写库
6. 将 chunk metadata 写入 `document_chunks`
7. 保留现有 Milvus 写入逻辑
8. 增加 `GET /documents/{doc_id}/open`
9. 增加最小版 `POST /reports/generate`

## 16. 下一步代码改造建议

下一轮建议直接进入代码层，顺序如下：

1. 先从 `ingest_final.py` 抽出 `chunking.py` 和 `embeddings.py`
2. 新增 `pg_repo.py` 与 `minio_client.py`
3. 写第一版 `FastAPI`
4. 打通上传入库
5. 再做检索与报告

如果继续推进，本文件应作为主实施文档，后续再补：

- `docker-compose.yml`
- `db/init.sql`
- `services/api/app.py`
- `services/worker/run_ingest.py`
