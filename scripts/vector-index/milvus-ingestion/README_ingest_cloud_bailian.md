# Cloud Bailian Milvus Ingest README

`ingest_cloud_bailian.py` is a cloud-only document ingestion tool for SIQ project evidence. It parses local documents, calls Alibaba Bailian/DashScope for embeddings and visual captions, writes vectors into Milvus, and can optionally mirror chunks into Vector Graph RAG sidecar collections.

The script is intentionally independent from local MinerU, vLLM, OCR, reranker, or other localhost model services.

## SIQ Positioning

This script is the cloud-model alternative to SIQ's local embedding / reranker path. Use it when a deployment is allowed to send project materials to Bailian/DashScope and needs visual caption or cloud embedding capability. For private or sensitive investment materials, prefer the local model services under `infra/model-services` and keep evidence inside the customer's environment.

## What It Does

- Ingests PDF, DOCX, Markdown, TXT, and image files.
- Extracts text, Markdown tables, DOCX structure, PDF page text, and PDF/page images.
- Uses Bailian `text-embedding-v4` for text/table/caption text chunks.
- Uses Bailian `qwen3-vl-embedding` for image/page visual chunks.
- Uses Bailian vision caption model, default `qwen3-vl-flash`, for visual evidence extraction.
- Writes to Milvus collections with `id`, `vector`, `project_tag`, and JSON `metadata`.
- Optionally writes Vector Graph RAG sidecars:
  - `<prefix>_vgrag_passages`
  - `<prefix>_vgrag_entities`
  - `<prefix>_vgrag_relations`
- Supports UI pause/resume, runtime logs, quality reports, and dry-run parsing.

## Quick Start

Run the Gradio UI:

```bash
cd /home/maoyd/siq-research-engine/scripts/vector-index/milvus-ingestion
python3 ingest_cloud_bailian.py --ui --host 0.0.0.0 --port 7863
```

Open:

```text
http://127.0.0.1:7863/
```

## CLI Examples

### UI

```bash
python3 ingest_cloud_bailian.py \
  --ui \
  --host 0.0.0.0 \
  --port 7863
```

### Dry Run

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
