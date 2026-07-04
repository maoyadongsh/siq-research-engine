# Milvus 向量库入库脚本

`scripts/vector-index/milvus-ingestion` 用于存放 Milvus 向量库入库脚本。`ingest_final.py` 是归档进来的入库工具，可将 PDF、DOCX、Markdown、TXT 等材料切片、向量化并写入指定 Milvus collection。

该目录不绑定某一条业务工作流，是一个可复用的向量库入库工具。默认 collection 指向现有 SIQ Milvus collection，运行时可以通过环境变量或 UI 改到其他 collection。

## 存档位置设计

| 文件/目录 | 用途 |
| --- | --- |
| `ingest_final.py` | Milvus 入库脚本，Gradio UI + 异步入库引擎 |
| `SIQ_INGEST_METADATA_SCHEMA.md` | `metadata` JSON 字段规范 |
| `SIQ_MULTIMODAL_VGRAG_INGEST_PLAN.md` | 多模态和 Vector Graph RAG 规划 |
| `init_collections.py` | Collection 初始化辅助脚本 |
| `ingest_cloud_bailian.py` | 云端 embedding / 视觉 caption 版入库脚本 |
| `tools/knowledge_ingest/` | 轻量知识库入库 UI |
| `docs/`、`shared/` | 架构、实施和审计说明 |

这个目录属于项目脚本层，不放运行态数据。入库进度、质量报告、MinerU 缓存和临时状态由脚本在本目录下生成，提交前应按需清理或加入忽略规则。

## 默认连接目标

`ingest_final.py` 默认连接本机 Milvus，并把 UI 中的默认 collection 设为 `ic_collaboration_shared`。这只是默认选择，不限制实际入库目标：

| 配置 | 默认值 | 说明 |
| --- | --- | --- |
| Milvus host | `localhost` | 可用 `SIQ_MILVUS_HOST` 覆盖 |
| Milvus port | `19530` | 可用 `SIQ_MILVUS_PORT` 覆盖 |
| Database | `default` | 可用 `SIQ_MILVUS_DB_NAME` 覆盖 |
| Collection | `ic_collaboration_shared` | 可用 `SIQ_MILVUS_COLLECTION` 覆盖 |
| 向量维度 | `1024` | Qwen3-VL Embedding 系列 |
| 索引 | HNSW / L2 | `M=32`, `efConstruction=256` |
| Metadata | `siq_chunk_v1` | 见 `SIQ_INGEST_METADATA_SCHEMA.md` |

可选的 SIQ collections：

| Collection | 用途 |
| --- | --- |
| `ic_collaboration_shared` | 项目共享底稿库，默认选中 |
| `ic_legal_scanner` | 法规和合规知识库 |
| `ic_finance_auditor` | 财务审计、估值和尽调知识库 |
| `ic_sector_expert` | 行业研究知识库 |
| `ic_risk_controller` | 风险控制知识库 |
| `ic_strategist` | 战略和宏观政策知识库 |
| `ic_chairman` | 投资方法论和综合裁决知识库 |
| `ic_archive_sop` | SOP、历史案例和归档知识库 |

## 快速启动

```bash
cd /home/maoyd/siq-research-engine/scripts/vector-index/milvus-ingestion
python3 ingest_final.py
```

默认从 `7862` 开始寻找可用端口。打开终端输出中的 Gradio 地址，例如：

```text
http://127.0.0.1:7862
```

如需指定端口：

```bash
GRADIO_SERVER_PORT=7862 GRADIO_SERVER_PORT_MAX=7870 python3 ingest_final.py
```

也可以由项目一键脚本按需启动：

```bash
cd /home/maoyd/siq-research-engine
SIQ_START_VECTOR_INGEST=1 ./start_all.sh
```

启动后可在 Web 工作台 `/vector-ingest` 页面查看状态并嵌入控制台。

## 推荐环境变量

```bash
export SIQ_MILVUS_HOST=localhost
export SIQ_MILVUS_PORT=19530
export SIQ_MILVUS_DB_NAME=default
export SIQ_MILVUS_COLLECTION=ic_collaboration_shared

export VLLM_EMBED_MODEL=qwen3-vl-embedding-2b
export VLLM_EMBED_MODEL_FALLBACK=Qwen3-VL-Embedding-2B
export MINERU_API_URL=http://127.0.0.1:8003
export VLM_API_URL=http://127.0.0.1:8002
```

云端 embedding 可按需配置：

```bash
export DASHSCOPE_API_KEY=...
export MINIMAX_API_KEY=...
export MINIMAX_EMBED_MODEL=embo-01
```

## 维护原则

- `project_tag` 必须稳定，建议使用 `SIQ-{项目或公司}-{年份}`。
- 多市场财报 evidence package 入库必须保留公司级 Wiki 路径。日本市场主入口是 `data/wiki/jp/companies/<ticker>-<company>/reports/<report_id>/`，metadata 中应同时保留 `company_wiki_path`、`wiki_report_path` 和 `report_id`；`jp_reports` 等旧路径只作兼容来源。
- 重置 collection 前确认已有数据可重建。
- API key 和数据库口令只放环境变量，不写入脚本和 README。
- 大文件先小批量试跑，检查质量报告后再全量入库。
- 运行态文件如 `.progress_*`、`.ingest_runtime_state.json`、`.mineru_ingest_cache/`、`ingest_quality_reports/` 不作为源码提交。
