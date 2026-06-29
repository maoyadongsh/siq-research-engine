# SIQ 知识库入库系统

`knowledge_ingest` 是面向 Milvus 的知识库入库工具，提供 Gradio Web UI 和异步入库引擎。它支持将 PDF、DOCX、Markdown、TXT 文档切块、向量化并写入 Milvus collection，用于法务法规库、研究知识库和企业内部资料检索。

## 核心能力

| 能力 | 说明 |
| --- | --- |
| Collection 管理 | 自动发现、新建、删除和选中 Milvus collection |
| 多格式入库 | 支持 PDF、DOCX、Markdown、TXT |
| Embedding 选择 | 支持本地 vLLM embedding 或 DashScope |
| 参数化切块 | 支持 chunk size、条款感知切分和文件名前缀 |
| 断点续传 | 按文件粒度记录进度，失败后可继续 |
| 检索测试 | 入库后即时 Top-K 检索并查看片段 |
| 实时监控 | 展示总文件数、已处理、向量数、失败数和日志 |

## 快速启动

```bash
cd /home/maoyd/siq-research-engine/scripts/vector-index/milvus-ingestion/tools/knowledge_ingest
unset ALL_PROXY all_proxy
python3 knowledge_ingest_ui.py
```

访问：

```text
http://localhost:7860
```

如果本机使用专用 Python 环境，可替换为对应解释器：

```bash
/path/to/.venv/bin/python knowledge_ingest_ui.py
```

## 环境要求

| 依赖 | 默认地址 / 说明 |
| --- | --- |
| Milvus | `localhost:19530` |
| Embedding vLLM | `localhost:8000`，1024 维向量模型 |
| DashScope | 可选，通过 `DASHSCOPE_API_KEY` 启用 |
| Python | 建议 Python 3.12 或项目专用虚拟环境 |

## 入库策略

| 参数 | 建议 |
| --- | --- |
| 向量维度 | 与 embedding 模型保持一致，例如 Qwen3-VL Embedding 1024 维 |
| Metric | `IP`，用于 cosine 近似 |
| 索引 | HNSW，按 collection 规模调整 `M` 和 `efConstruction` |
| 切块 | 法规场景建议较短 chunk，并优先在“第X条”边界切分 |
| 元数据 | 保留文件名、project_tag、chunk 序号、原文片段和来源路径 |
| 标签 | 使用 `project_tag` 区分法规库、公司资料或专题知识库 |

## 与 SIQ 的关系

| 使用方 | 价值 |
| --- | --- |
| `siq_legal` | 构建法规向量库，支撑 hybrid_search 和意见书依据引用 |
| 研究知识库 | 将公司资料、行业资料和内部笔记向量化 |
| 评测与调试 | 通过检索测试确认 chunk、embedding 和 metadata 是否符合预期 |

## 操作建议

- 大批量入库前先用小目录验证 collection schema 和检索质量。
- 重置 collection 前确认没有其他服务正在使用。
- 入库日志和失败清单应保留到运行态目录或任务记录中。
- 对法规、合同等条款型文档，优先开启条款边界切分和文件名前缀。
- API key 只放环境变量，不写入配置截图和 README。
