# Milvus 向量库入库脚本

## 目录职责

`scripts/vector-index/milvus-ingestion` 负责把可检索材料切块、向量化并写入 Milvus collection。它是 SIQ 语义层和知识库层的重要工具目录，但不绑定某一条单一业务工作流。

## 在系统中的位置

```text
文档 / evidence package / 知识资料
  -> scripts/vector-index/milvus-ingestion
     -> embedding / chunking / metadata / Milvus collections
     -> Web `/vector-ingest` / Agent retrieval / 法规库 / 项目底稿库
```

## 核心内容

| 文件 / 目录 | 作用 |
| --- | --- |
| `ingest_final.py` | 主入库脚本，提供 Gradio UI 与异步入库引擎 |
| `init_collections.py` | collection 初始化辅助脚本 |
| `ingest_cloud_bailian.py` | 云端 embedding / caption 版入库脚本 |
| `SIQ_INGEST_METADATA_SCHEMA.md` | metadata 字段合同 |
| `SIQ_MULTIMODAL_VGRAG_INGEST_PLAN.md` | 多模态 / VGRAG 规划说明 |
| `tools/knowledge_ingest/` | 轻量知识库入库 UI |
| `docs/` `shared/` | 设计、审计和实施说明 |

## 典型用法

### 直接启动主入库 UI

```bash
cd /home/maoyd/siq-research-engine/scripts/vector-index/milvus-ingestion
python3 ingest_final.py
```

### 指定端口启动

```bash
cd /home/maoyd/siq-research-engine/scripts/vector-index/milvus-ingestion
GRADIO_SERVER_PORT=7862 GRADIO_SERVER_PORT_MAX=7870 python3 ingest_final.py
```

### 通过主项目统一启动

```bash
cd /home/maoyd/siq-research-engine
SIQ_START_VECTOR_INGEST=1 ./start_all.sh
```

## 关键边界或治理规则

- 这是向量入库工具层，不是业务事实层。Milvus 存的是检索索引和语义 chunk，不是原始事实真值。
- `project_tag`、`collection`、`metadata schema` 和 `source path` 应保持稳定，避免后续 Agent 检索语义漂移。
- 多市场 package 入库时必须保留公司级 Wiki 路径、`report_id` 和 package 标识，不能只存“文本片段”。
- 重置 collection 前必须确认数据可重建。
- `.progress_*`、`.ingest_runtime_state.json`、缓存目录和质量报告不应作为源码提交。

## 维护建议

- 先小批量试跑，再全量入库。
- collection schema 或 metadata contract 变化时，要同步检查 Agent 检索消费侧。
- embedding 模型切换时，要显式记录向量维度、metric 和 index 参数。
- 文档型 README 应始终强调“Milvus 是语义层，不是事实层”。
