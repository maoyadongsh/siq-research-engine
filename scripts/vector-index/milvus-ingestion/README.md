# Milvus 向量库入库脚本

## 目录职责

`scripts/vector-index/milvus-ingestion` 负责把可检索材料切块、向量化并写入 Milvus collection。它是 SIQ 语义层和知识库层的重要工具目录，但不绑定某一条单一业务工作流。

## 产品归属与业务边界

Milvus 入库是应用中心的核心工具，也服务二级市场和一级市场智能体召回。

需要特别区分：本目录可以把 Wiki package 作为**来源材料**派生出独立的 Milvus 索引，但这不代表 LLM-Wiki 使用 embedding、reranker 或 Milvus。LLM-Wiki 自身通过 `retrieval_index.json`、对象 ID、主题别名、priority files、`document_links`/`note_links` 做逻辑跳转查询；本目录的切片、向量化和精排属于 Wiki 之外的可选召回通道。删除或重建 Milvus collection 不会改变 Wiki 内部查询结果。

| 产品面 | 作用 | 边界 |
| --- | --- | --- |
| 二级市场 | 将 Wiki package、财报 evidence、法规和报告片段转成可重建语义索引 | 不把 chunk 当事实真值，必须保留 source identity |
| 一级市场 | 将 data room、访谈、投委会材料和专家报告转成 project-scoped 检索资产 | 必须保留 ACL、project scope 和材料来源 |
| 应用中心 | `/vector-ingest`、Gradio UI、knowledge ingest 工具和 metadata schema | 负责索引治理，不负责最终投研判断 |

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

## 当前最新状态

| 方向 | 状态 | 说明 |
| --- | --- | --- |
| Market evidence chunks | 支持多市场 package 的语义入库 | metadata 必须保留 market、company、report、Wiki path、source evidence |
| Document chunks | 支持通用文档 parser artifact 入库 | 面向合同、会议材料、网页和非财报资料 |
| Agent memory / profile knowledge | 与 Hermes 记忆系统协同 | Milvus 是语义索引，PostgreSQL 仍是权威记忆账本 |
| MVP vector dry-run | `/parse-hk` warning/fail package 默认阻断真实生成 | 只有确认 force 后才允许高风险动作进入后续链路 |

Milvus 层的商业价值是召回效率和泛化能力，但它不是事实真相来源。SIQ 的设计要求每个 chunk 保留足够 metadata，能够回到 Wiki package、parser artifact 或原始披露坐标。

## 多模态检索与高精度召回

向量入库保留文档结构与证据身份，不把所有内容压平成匿名文本：

| 索引对象 | 建议表示 | 必需 metadata |
| --- | --- | --- |
| 文本/章节 | 结构感知 chunk + Qwen VL embedding | market/company/filing/parse run、page/line、artifact hash |
| 表格 | 标题、表头、行列语义与原始表格定位共同编码 | table index、page、bbox、unit/currency、logical table ID |
| 图片/图表 | image embedding 或本地多模态描述 + 原图引用 | figure/page/bbox、source image hash、modality |
| 法规/知识 | 条款级 chunk + jurisdiction/effective date | source file、条款、版本、可见性 |
| Agent memory | memory item 文本向量 | tenant/user/project/profile/visibility、时间、importance |

Qwen3-VL Embedding 与 Reranker 使文本查图、图文混合召回和候选精排可以在本地完成。Nemotron 原生视觉理解负责解释图片内容，embedding/reranker 负责找到相关证据，两者职责不同，不能用生成模型的描述替代原图 locator。

## 索引不是事实源

Milvus collection 可以重建，因此每个 entity 必须能回到 Wiki package、PostgreSQL memory item 或 document artifact。入库前先检查 quality gate 与 artifact hash；检索后仍需由 evidence resolver 和回答守卫验证数值、身份、期间和引用。这个边界避免“相似度很高”被误当成“事实正确”。

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

常用本地模型环境变量示例：

```bash
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

- `project_tag` 必须稳定，建议使用 `SIQ-{项目或公司}-{年份}`。
- 多市场财报 evidence package 入库必须保留公司级 Wiki 路径。日本市场主入口是 `data/wiki/jp/companies/<ticker>-<company>/reports/<report_id>/`，metadata 中应同时保留 `company_wiki_path`、`wiki_report_path` 和 `report_id`；`jp_reports` 等旧路径只作兼容来源。
- 重置 collection 前确认已有数据可重建。
- API key 和数据库口令只放环境变量，不写入脚本和 README。
- 大文件先小批量试跑，检查质量报告后再全量入库。
- 运行态文件如 `.progress_*`、`.ingest_runtime_state.json`、`.mineru_ingest_cache/`、`ingest_quality_reports/` 不作为源码提交。

## 技术创新与检索治理

SIQ 强调“可重建索引”，而不是把向量库当作唯一知识库。市场 evidence package、通用文档 artifact 和 Hermes 记忆分别使用领域 chunk builder，但统一保留 source identity、scope、quality 和 evidence metadata。

| 机制 | 技术难点 | 商业价值 |
| --- | --- | --- |
| 领域化切块 | 表格、段落、事实、claim 与 source target 不能只按字符切分 | 提高财务、法务和尽调问题的召回精度 |
| 多模态检索 | 文本、表格、页面图与视觉 embedding/reranker 协同 | 支持复杂版面与图表信息检索 |
| 权限与市场过滤 | collection、project、market、scope、ACL metadata 一致 | 防止跨项目、跨用户和跨市场错误召回 |
| Stable ID 与 dry-run | 重跑去重、变更预览、质量门禁后再写入 | 降低索引污染并支持低风险批量更新 |
| 可替换模型 | 本地 Qwen VL 或受控云 embedding 接口 | 在隐私、成本和效果间选择 |

通用文档语义构建还会生成 segments、facts、claims、evidence 和 retrieval index，使没有预先 LLM 增强的 package 也能先获得可审计的规则语义基线。
