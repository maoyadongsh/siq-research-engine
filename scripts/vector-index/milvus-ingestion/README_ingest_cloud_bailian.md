# Cloud Bailian Milvus Ingest README

`ingest_cloud_bailian.py` is a cloud-only document ingestion tool for SIQ project evidence. It parses local documents, calls Alibaba Bailian/DashScope for embeddings and visual captions, writes vectors into Milvus, and can optionally mirror chunks into Vector Graph RAG sidecar collections.

The script is intentionally independent from local MinerU, vLLM, OCR, reranker, or other localhost model services.

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
cd /home/maoyd/.openclaw/workspace/ic_master_coordinator_workspace
/home/maoyd/miniconda3/bin/python ingest_cloud_bailian.py --ui --host 0.0.0.0 --port 7863
```

Open:

```text
http://127.0.0.1:7863/
```

## UI Workflow

1. Upload files or fill a local document directory.
2. Fill `project_tag`, for example `SIQ-PROJECT-2026`.
3. Select `Milvus Database`.
4. Select target `Collection`.
5. Fill Alibaba Bailian API Key, or set `DASHSCOPE_API_KEY` in the environment.
6. Choose feature switches.
7. Click `开始入库`.

The UI shows runtime status and logs every 2 seconds.

## Key UI Options

### Milvus Database

Target Milvus database. The dropdown is refreshed from Milvus. Selecting a database automatically refreshes the target collection list.

### 目标 Collection

Main Milvus collection to write chunks into. Existing role collections include:

- `ic_collaboration_shared`
- `ic_chairman`
- `ic_finance_auditor`
- `ic_sector_expert`
- `ic_legal_scanner`
- `ic_strategist`
- `ic_risk_controller`
- `ic_master_coordinator`
- `ic_archive_sop`

Custom collection names are also supported.

### 启用多模态 visual_chunk

Creates visual chunks for images and rendered PDF pages. Keep this enabled for scanned/image-based PDFs.

### 生成视觉 caption

Uses Bailian vision model to extract evidence text from images/pages. This is important for scanned PDFs and chart/table pages.

### PDF 页面渲染为视觉块

Renders PDF pages as images and ingests them as `visual_chunk`.

### PDF 视觉页数上限

Limits how many PDF pages are rendered as visual chunks.

- `0`: no limit
- `8`: default UI value

For large scanned PDFs, use a small number first to test quality and cost.

### 同步到 Vector Graph RAG passages

Writes chunks to VGraph sidecars and extracts rule-based entities/relations.

If enabled, the script writes:

```text
<graph_prefix>_vgrag_passages
<graph_prefix>_vgrag_entities
<graph_prefix>_vgrag_relations
```

### Graph prefix

Controls VGraph sidecar collection names.

Usually leave this empty. If empty, the script uses the target collection name. For example:

```text
target collection: cloud_bailian_smoke_test
```

Sidecars:

```text
cloud_bailian_smoke_test_vgrag_passages
cloud_bailian_smoke_test_vgrag_entities
cloud_bailian_smoke_test_vgrag_relations
```

Only fill a custom prefix if multiple collections should share one graph namespace.

### Dry run（不调用 API，不写 Milvus）

Preflight mode. It parses files, creates chunks, and writes quality reports, but does not call Bailian and does not write Milvus.

Use dry run to inspect:

- File parsing success
- Chunk counts
- Page coverage
- Table/visual detection
- Metadata and citation samples

### 暂停 / 继续

Pause/resume is cooperative. It pauses before the next caption call, visual embedding call, file, or Milvus write. It does not interrupt a single API request already in flight.

## Chunk Types

### `text_chunk`

Text extracted from PDF/DOCX/Markdown/TXT. Embedded with `text-embedding-v4`.

### `table_chunk`

Markdown or DOCX table evidence. Embedded with `text-embedding-v4`.

### `visual_chunk`

Image or rendered PDF page. Embedded with `qwen3-vl-embedding`.

### `caption_text_chunk`

Text-only chunk created from a useful visual caption. Embedded with `text-embedding-v4`. This improves natural-language query recall for scanned PDFs.

## Low-Information Visual Filtering

After visual captioning, the script skips pages whose caption indicates no useful evidence, for example:

- Pure blank/white background
- No identifiable text
- No effective information
- No company/product/financial/risk/time/amount evidence

These chunks are marked as skipped and are not embedded or written.

## Duplicate Handling

Before inserting valid chunks, the script attempts to delete existing chunks with matching `chunk_uid` under the same `project_tag` and source path. This reduces duplicate accumulation when re-running the same ingestion.

Note: files uploaded through the UI may have different upload paths between runs. If the same file is repeatedly uploaded, exact source-path dedupe may not remove older uploads from different upload directories.

## VGraph Entity/Relation Extraction

When VGraph is enabled, each valid chunk is mirrored into passages and rule-based entities/relations are extracted.

Profiles vary by `doc_type`:

- `teaser`: company, product, region, capacity metric, certification, capability, stakeholder, date
- `financials`: company, financial metric, capacity metric, date, risk, stakeholder
- `legal`: company, legal clause, regulation, risk, date, stakeholder
- `industry_research`: company, product, region, capacity metric, financial metric, date
- `meeting_note`: company, product, region, financial metric, risk, stakeholder, date
- `committee_opinion`: company, product, financial metric, risk, stakeholder, date
- `sop`: company, capability, risk, stakeholder
- `default`: all rule groups

Example relation predicates:

- `涉及产品`
- `覆盖区域`
- `具备产能`
- `具备指标`
- `获得认证`
- `具备能力`
- `具备财务指标`
- `包含条款`
- `适用法规`
- `存在风险`
- `发生时间`
- `相关方`

## CLI Examples

### UI

```bash
/home/maoyd/miniconda3/bin/python ingest_cloud_bailian.py \
  --ui \
  --host 0.0.0.0 \
  --port 7863
```

### Dry Run

```bash
/home/maoyd/miniconda3/bin/python ingest_cloud_bailian.py \
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

/home/maoyd/miniconda3/bin/python ingest_cloud_bailian.py \
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
/home/maoyd/miniconda3/bin/python ingest_cloud_bailian.py \
  --no-ui \
  --input-dir ./docs \
  --project-tag SIQ-PROJECT-2026 \
  --milvus-uri https://example.api.zillizcloud.com \
  --milvus-token "$ZILLIZ_TOKEN" \
  --db-name default \
  --collection ic_collaboration_shared
```

## Quality Reports

Quality reports are written to:

```text
ingest_quality_reports/
```

Each report contains:

- Source file path/name
- Collection/project tag
- Total chunks
- Valid/skipped chunks
- Type and modality counts
- Visual/table flags
- Sample citations
- Sample text previews

Use these reports to inspect ingestion quality before trusting retrieval results.

## Retrieval Notes

Use the query embedding model that matches the chunk type:

- Text-heavy collections: use `text-embedding-v4` query vectors.
- Visual-only collections: use `qwen3-vl-embedding` query vectors.
- Mixed collections: hybrid or two-pass retrieval is recommended.

For scanned PDFs, retrieval improves when `caption_text_chunk` is present because text queries can hit text embeddings from visual captions.

## Common Problems

### `InvalidApiKey`

The Bailian/DashScope key is invalid.

Check that only the `sk-...` key is entered. Do not paste explanatory text.

### `latin-1 codec can't encode characters`

The API key field contains Chinese or non-ASCII characters. The script now normalizes common prefixes such as `Bearer` and `DASHSCOPE_API_KEY=`, but the key itself must be ASCII.

### Database dropdown only shows one item

Click `刷新 Database`. The script lists databases via `MilvusClient.list_databases()`.

### Collection does not update after database change

The UI binds database changes to collection refresh. If stale, click `刷新 Collection`.

### VGraph entities/relations are zero

For older runs, only passages may have been written. Re-run ingestion with the updated script and `同步到 Vector Graph RAG passages` enabled.

### Blank pages pollute recall

The updated script filters low-information visual captions. Re-run ingestion to apply this filtering.

## Recommended Settings

### Text PDF

- Enable table recognition.
- Visual parsing optional.
- Keep `PDF 视觉页数上限` small unless charts/images matter.

### Scanned/Image PDF

- Enable visual chunks.
- Enable visual captions.
- Enable PDF page rendering.
- Use a page limit first, then run full ingest if quality is good.
- Enable VGraph if entity/relation retrieval is needed.

### Repeated Testing

- Use `Dry run` first.
- Use a smoke-test collection, for example `cloud_bailian_smoke_test`.
- Review `ingest_quality_reports/`.
- Then ingest into production collection.

## Security Notes

- Do not commit API keys.
- Prefer environment variables for CLI usage.
- UI password fields hide values in the browser but runtime processes can still use them for API calls.

