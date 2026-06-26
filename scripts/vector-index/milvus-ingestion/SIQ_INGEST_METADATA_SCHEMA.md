# SIQ Ingest Metadata Schema

Version: `siq_chunk_v1`

This schema is produced by `ingest_final.py` and stored in each Milvus row's
`metadata` JSON field. The goal is stable retrieval, citation, evaluation, and
future graph sidecar construction.

## Required Fields

| Field | Meaning |
| --- | --- |
| `schema_version` | Always `siq_chunk_v1` for this generation. |
| `text` | The exact readable chunk text returned to agents and rerankers. |
| `source` | Source filename. |
| `source_path` | Absolute source path when available. |
| `project_tag` | Batch/project tag, mirrored from Milvus scalar field. |
| `collection` | Physical Milvus collection name. |
| `collection_role` | `shared`, `private`, `archive`, or `custom`. |
| `agent_id` | Agent id for private collections, otherwise null. |
| `doc_type` | `teaser`, `financials`, `legal`, `industry_research`, `meeting_note`, `committee_opinion`, `sop`, or `default`. |
| `evidence_level` | `source_doc`, `regulation`, `research`, `methodology`, or `expert_opinion`. |
| `section_path` | Best-effort heading/article path around the chunk. |
| `chunk_index` | 1-based chunk number within the parsed document/page context. |
| `total_chunks` | Number of chunks produced for that parsed document/page context. |
| `chunk_uid` | Deterministic hash for dedupe, citation, and evaluation. |
| `citation` | Human-readable citation string for agent output. |

## Chunking Rules

Chunking is structure-first, length-second:

- Markdown and discussion files split first on headings / speaker blocks.
- Legal material splits first on chapter/article/section markers.
- Oversized structural units are then split on paragraph or sentence boundaries.
- PDF pages keep page metadata; scanned pages use OCR when available, otherwise
  a visual proxy chunk is retained.

## Rebuild Rule

When a collection is reset from the UI, the script writes a lightweight manifest
under `reset_manifests/` before dropping the existing collection. This manifest
records entity count, index metric, target schema version, and timestamp.
