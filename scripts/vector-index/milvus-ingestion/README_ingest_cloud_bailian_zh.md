# Cloud Bailian Milvus 入库脚本说明

本文档说明 `ingest_cloud_bailian.py` 的用途、启动方式、UI 参数、入库流程、VGraph 图谱同步、Dry run、暂停继续、质量报告和常见问题。

该脚本是一个云端版文档入库工具：解析本地材料，调用阿里百炼/DashScope 生成 embedding 和视觉 caption，写入 Milvus，并可选同步到 Vector Graph RAG 侧边集合。

脚本不依赖本地 MinerU、vLLM、OCR、reranker 或其他本地模型服务。

## 在 SIQ 中的位置

这是 SIQ 本地 embedding / reranker 路径的云端替代方案。只有在部署环境允许把项目材料发送到百炼 / DashScope 时才建议使用；如果材料涉及未公开投研、合同、数据房或客户敏感信息，应优先使用 `infra/model-services` 下的本地模型服务，保证证据留在客户内网或本机环境。

## 快速启动

```bash
cd /home/maoyd/siq-research-engine/scripts/vector-index/milvus-ingestion
python3 ingest_cloud_bailian.py --ui --host 0.0.0.0 --port 7863
```

打开：

```text
http://127.0.0.1:7863/
```

## 命令行示例

### 启动 UI

```bash
python3 ingest_cloud_bailian.py \
  --ui \
  --host 0.0.0.0 \
  --port 7863
```

### Dry run 试跑

```bash
python3 ingest_cloud_bailian.py \
  --no-ui \
  --dry-run \
  --input-dir /path/to/docs \
  --project-tag SIQ-PROJECT-2026 \
  --collection cloud_bailian_smoke_test \
  --db-name default \
  --enable-visual \
  --enable-pdf-page-visuals \
  --max-pdf-visual-pages 3
```

### Full Ingest

```bash
export DASHSCOPE_API_KEY='sk-...'

python3 ingest_cloud_bailian.py \
  --no-ui \
  --input-dir /path/to/docs \
  --project-tag SIQ-PROJECT-2026 \
  --milvus-uri http://127.0.0.1:19530 \
  --db-name default \
  --collection cloud_bailian_smoke_test \
  --enable-visual \
  --enable-captions \
  --enable-pdf-page-visuals \
  --max-pdf-visual-pages 8 \
  --enable-vgrag-passages
```

### Remote Milvus / Zilliz

```bash
python3 ingest_cloud_bailian.py \
  --no-ui \
  --input-dir ./docs \
  --project-tag SIQ-PROJECT-2026 \
  --milvus-uri https://example.api.zillizcloud.com \
  --milvus-token "$ZILLIZ_TOKEN" \
  --db-name default \
  --collection ic_collaboration_shared
```
