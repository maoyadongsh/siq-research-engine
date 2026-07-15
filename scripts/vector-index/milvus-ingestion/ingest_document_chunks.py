#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_COLLECTION = "siq_documents"
DEFAULT_VECTOR_DIM = int(os.environ.get("SIQ_EMBED_VECTOR_DIM", "1024"))


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return {} if default is None else default
    return json.loads(path.read_text(encoding="utf-8"))


PACKAGE_TO_SOURCE_ARTIFACT = {
    "qa/parse_manifest.json": "manifest.json",
    "sections/document.md": "document.md",
    "sections/blocks.json": "blocks.json",
    "tables/tables.json": "tables.json",
    "logical_tables/logical_tables.json": "logical_tables.json",
    "logical_tables/table_relations.json": "table_relations.json",
    "figures/figures.json": "figures.json",
    "figures/figure_index.json": "figure_index.json",
    "qa/source_map.json": "source_map.json",
    "qa/quality_report.json": "quality_report.json",
    "extraction/schema.json": "extraction/schema.json",
    "extraction/result.json": "extraction/result.json",
    "extraction/evidence_map.json": "extraction/evidence_map.json",
    "extraction/validation_report.json": "extraction/validation_report.json",
}


def package_artifact_path(package_dir: Path, package_rel: str) -> Path:
    local = package_dir / package_rel
    if local.is_file():
        return local
    artifact_name = PACKAGE_TO_SOURCE_ARTIFACT.get(package_rel, package_rel)
    artifact_manifest = read_json(package_dir / "artifact_manifest.json", {})
    artifacts = artifact_manifest.get("artifacts") if isinstance(artifact_manifest, dict) else {}
    item = artifacts.get(artifact_name) if isinstance(artifacts, dict) else None
    source = item.get("source") if isinstance(item, dict) else None
    if source:
        return Path(str(source))
    source_root = artifact_manifest.get("source_result_dir") if isinstance(artifact_manifest, dict) else None
    return Path(str(source_root)) / artifact_name if source_root else local


def read_package_json(package_dir: Path, package_rel: str, default: Any | None = None) -> Any:
    return read_json(package_artifact_path(package_dir, package_rel), default)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _segment_type(block: dict[str, Any], text: str) -> str:
    value = text.lower()
    rules = (
        ("risk_factors", ("风险", "risk")),
        ("key_financials", ("财务", "资产负债", "利润", "现金流", "financial")),
        ("management_discussion", ("管理层讨论", "经营情况讨论", "management discussion")),
        ("business_overview", ("主营业务", "业务概要", "business overview")),
        ("company_profile", ("公司简介", "公司信息", "company profile")),
    )
    for segment_type, aliases in rules:
        if any(alias in value for alias in aliases):
            return segment_type
    return "document_section" if block.get("type") in {"title", "heading"} else "document_content"


def build_rule_semantics(package_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    blocks = read_package_json(package_dir, "sections/blocks.json").get("blocks") or []
    source_payload = read_package_json(package_dir, "qa/source_map.json")
    source_by_block = {
        str(item.get("block_id")): item
        for item in source_payload.get("sources") or []
        if isinstance(item, dict) and item.get("block_id")
    }
    document_id = str(manifest.get("document_id") or f"doc-{manifest.get('task_id')}")
    segments: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    for block in blocks:
        text = str(block.get("text") or block.get("markdown") or "").strip()
        if not text or block.get("type") in {"table", "image"}:
            continue
        block_id = str(block.get("block_id") or stable_id(document_id, len(segments)))
        source = source_by_block.get(block_id) or {}
        evidence_id = str(source.get("evidence_id") or f"ev_{stable_id(document_id, block_id)[:24]}")
        segment_id = f"seg_{stable_id(document_id, block_id, text)[:24]}"
        evidence.append({
            "evidence_id": evidence_id,
            "document_id": document_id,
            "source_type": "text",
            "source_file": "sections/document.md",
            "block_id": block_id,
            "pdf_page_number": block.get("page_number"),
            "quote": text[:500],
            "open_source_url": source.get("open_source_url") or "",
            "confidence": "high" if source else "medium",
            "needs_review": not bool(source),
        })
        segments.append({
            "segment_id": segment_id,
            "document_id": document_id,
            "segment_type": _segment_type(block, text),
            "title": text[:160] if block.get("type") in {"title", "heading"} else "",
            "text": text,
            "block_id": block_id,
            "page_number": block.get("page_number"),
            "evidence_ids": [evidence_id],
            "confidence": "high" if source else "medium",
            "needs_review": not bool(source),
        })

    extracted = read_package_json(package_dir, "extraction/result.json")
    values = extracted.get("result") if isinstance(extracted, dict) else {}
    facts = []
    if isinstance(values, dict):
        for key, value in values.items():
            if value in (None, "", [], {}):
                continue
            facts.append({
                "fact_id": f"fact_{stable_id(document_id, key, value)[:24]}",
                "fact_type": "document_extraction",
                "subject": {"type": "document", "id": document_id, "name": manifest.get("filename") or document_id},
                "predicate": str(key),
                "object": value,
                "evidence_ids": [],
                "confidence": "medium",
                "needs_review": True,
            })
    claims = [{
        "claim_id": f"claim_{fact['fact_id'][5:]}",
        "claim_type": "extracted_field",
        "text": f"{fact['predicate']}: {fact['object']}",
        "supporting_facts": [fact["fact_id"]],
        "evidence_ids": fact["evidence_ids"],
        "needs_review": fact["needs_review"],
    } for fact in facts]
    topics: dict[str, list[str]] = {}
    for segment in segments:
        topics.setdefault(segment["segment_type"], []).append(segment["segment_id"])
    semantic_dir = package_dir / "semantic"
    generated = {
        "segments.json": {"schema_version": "document_semantic_segments_v1", "segments": segments},
        "facts.json": {"schema_version": "document_semantic_facts_v1", "facts": facts},
        "relations.json": {"schema_version": "document_semantic_relations_v1", "relations": []},
        "claims.json": {"schema_version": "document_semantic_claims_v1", "claims": claims},
        "evidence_semantic.json": {"schema_version": "document_semantic_evidence_v1", "evidence": evidence},
        "retrieval_index.json": {
            "schema_version": "document_semantic_retrieval_v1",
            "document_id": document_id,
            "topics": [{"topic": key, "segment_ids": ids, "evidence_ids": sorted({eid for item in segments if item["segment_id"] in ids for eid in item["evidence_ids"]})} for key, ids in topics.items()],
            "usage_policy": "LLM output must bind existing segment/evidence ids; extracted financial values require source evidence.",
        },
    }
    for name, payload in generated.items():
        write_json(semantic_dir / name, payload)
    summary = {"rule_version": "a_share_compatible_document_rules_v1", "segments": len(segments), "facts": len(facts), "evidence": len(evidence), "claims": len(claims)}
    write_json(semantic_dir / "semantic_manifest.json", summary)
    return summary


def stable_id(*parts: Any) -> str:
    return hashlib.sha256("\x1f".join("" if part is None else str(part) for part in parts).encode("utf-8")).hexdigest()


def repo_relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


def split_text(text: str, chunk_size: int = 900, overlap: int = 120) -> list[str]:
    paragraphs = [item.strip() for item in re.split(r"\n\s*\n", text) if item.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(current) + len(paragraph) + 2 <= chunk_size:
            current = f"{current}\n\n{paragraph}".strip()
            continue
        if current:
            chunks.append(current)
        if len(paragraph) <= chunk_size:
            current = paragraph
        else:
            step = max(1, chunk_size - overlap)
            chunks.extend(paragraph[i : i + chunk_size] for i in range(0, len(paragraph), step))
            current = ""
    if current:
        chunks.append(current)
    return chunks


def iter_chunks(package_dir: Path, *, collection_name: str = DEFAULT_COLLECTION) -> list[dict[str, Any]]:
    package_dir = package_dir.resolve()
    manifest = read_json(package_dir / "manifest.json")
    if manifest.get("schema_version") != "generic_document_package_v1":
        raise ValueError("manifest schema_version must be generic_document_package_v1")
    build_rule_semantics(package_dir, manifest)
    sources_by_block, sources_by_table, sources_by_image = _source_maps(package_dir)
    chunks: list[dict[str, Any]] = []
    chunks.extend(_section_chunks(package_dir, manifest, sources_by_block, collection_name))
    chunks.extend(_table_chunks(package_dir, manifest, sources_by_table, collection_name))
    chunks.extend(_image_chunks(package_dir, manifest, sources_by_image, collection_name))
    chunks.extend(_extraction_chunks(package_dir, manifest, collection_name))
    return chunks


def _base_metadata(package_dir: Path, manifest: dict[str, Any], chunk_type: str, collection_name: str) -> dict[str, Any]:
    return {
        "schema_version": "siq_generic_document_chunk_v1",
        "source_domain": "generic_document",
        "collection": manifest.get("collection") or "default",
        "milvus_collection": collection_name,
        "document_id": manifest.get("document_id"),
        "task_id": manifest.get("task_id"),
        "wiki_package_path": repo_relative(package_dir),
        "postgres_schema": "document_parser",
        "chunk_type": chunk_type,
        "document_kind": manifest.get("document_kind"),
        "filename": manifest.get("filename"),
        "block_id": "",
        "table_id": "",
        "image_id": "",
        "evidence_id": "",
        "page_number": None,
        "section_title": "",
        "open_source_url": "",
    }


def _source_maps(package_dir: Path) -> tuple[dict[str, dict], dict[str, dict], dict[str, dict]]:
    payload = read_package_json(package_dir, "qa/source_map.json")
    block: dict[str, dict] = {}
    table: dict[str, dict] = {}
    image: dict[str, dict] = {}
    for item in payload.get("sources") or []:
        if not isinstance(item, dict):
            continue
        if item.get("block_id"):
            block[str(item["block_id"])] = item
        if item.get("table_id"):
            table[str(item["table_id"])] = item
        if item.get("image_id"):
            image[str(item["image_id"])] = item
    return block, table, image


def _section_chunks(package_dir: Path, manifest: dict[str, Any], sources_by_block: dict[str, dict], collection_name: str) -> list[dict[str, Any]]:
    blocks = read_package_json(package_dir, "sections/blocks.json").get("blocks") or []
    semantic_segments = read_json(package_dir / "semantic" / "segments.json", {}).get("segments") or []
    semantic_by_block = {
        str(item.get("block_id")): item
        for item in semantic_segments
        if isinstance(item, dict) and item.get("block_id")
    }
    chunks: list[dict[str, Any]] = []
    for block in blocks:
        text = str(block.get("text") or block.get("markdown") or "").strip()
        if not text or block.get("type") in {"table", "image"}:
            continue
        source = sources_by_block.get(str(block.get("block_id") or "")) or {}
        semantic = semantic_by_block.get(str(block.get("block_id") or "")) or {}
        for index, chunk in enumerate(split_text(text), start=1):
            uid = stable_id(manifest.get("document_id"), "section", block.get("block_id"), index, chunk)
            metadata = {
                **_base_metadata(package_dir, manifest, "section", collection_name),
                "block_id": block.get("block_id") or "",
                "evidence_id": source.get("evidence_id") or (block.get("source_ref") or {}).get("evidence_id") or "",
                "semantic_evidence_ids": semantic.get("evidence_ids") or [],
                "segment_id": semantic.get("segment_id") or "",
                "segment_type": semantic.get("segment_type") or "document_content",
                "page_number": block.get("page_number"),
                "section_title": _section_title(block),
                "open_source_url": source.get("open_source_url") or "",
                "chunk_index": index,
                "chunk_uid": uid,
            }
            chunks.append({"chunk_uid": uid, "text": chunk, "metadata": metadata})
    return chunks


def _table_chunks(package_dir: Path, manifest: dict[str, Any], sources_by_table: dict[str, dict], collection_name: str) -> list[dict[str, Any]]:
    payload = read_package_json(package_dir, "tables/tables.json")
    chunks: list[dict[str, Any]] = []
    for table in payload.get("physical_tables") or payload.get("tables") or []:
        text = _table_text(table)
        if not text.strip():
            continue
        table_id = str(table.get("table_id") or "")
        source = sources_by_table.get(table_id) or {}
        uid = stable_id(manifest.get("document_id"), "table", table_id, text)
        metadata = {
            **_base_metadata(package_dir, manifest, "table_summary", collection_name),
            "table_id": table_id,
            "evidence_id": source.get("evidence_id") or "",
            "page_number": table.get("page_number"),
            "section_title": table.get("title") or table.get("caption") or "",
            "open_source_url": source.get("open_source_url") or "",
            "chunk_uid": uid,
        }
        chunks.append({"chunk_uid": uid, "text": text, "metadata": metadata})
    return chunks


def _image_chunks(package_dir: Path, manifest: dict[str, Any], sources_by_image: dict[str, dict], collection_name: str) -> list[dict[str, Any]]:
    figures = read_package_json(package_dir, "figures/figures.json").get("figures") or []
    chunks: list[dict[str, Any]] = []
    for figure in figures:
        text = "\n".join(str(figure.get(key) or "").strip() for key in ("caption", "alt_text", "ocr_text") if figure.get(key)).strip()
        if not text:
            continue
        image_id = str(figure.get("image_id") or "")
        source = sources_by_image.get(image_id) or {}
        uid = stable_id(manifest.get("document_id"), "image", image_id, text)
        metadata = {
            **_base_metadata(package_dir, manifest, "image_caption", collection_name),
            "block_id": figure.get("block_id") or "",
            "image_id": image_id,
            "evidence_id": source.get("evidence_id") or figure.get("evidence_id") or "",
            "page_number": figure.get("page_number"),
            "section_title": figure.get("nearby_heading") or "",
            "open_source_url": source.get("open_source_url") or "",
            "chunk_uid": uid,
        }
        chunks.append({"chunk_uid": uid, "text": text, "metadata": metadata})
    return chunks


def _extraction_chunks(package_dir: Path, manifest: dict[str, Any], collection_name: str) -> list[dict[str, Any]]:
    result = read_package_json(package_dir, "extraction/result.json")
    values = result.get("result") if isinstance(result, dict) else {}
    if not isinstance(values, dict):
        return []
    chunks = []
    for key, value in values.items():
        if value in (None, "", [], {}):
            continue
        text = f"{key}: {value}"
        uid = stable_id(manifest.get("document_id"), "extraction", key, text)
        metadata = {
            **_base_metadata(package_dir, manifest, "extraction_field", collection_name),
            "section_title": str(key),
            "chunk_uid": uid,
        }
        chunks.append({"chunk_uid": uid, "text": text, "metadata": metadata})
    return chunks


def _section_title(block: dict[str, Any]) -> str:
    if block.get("type") in {"title", "heading"}:
        return str(block.get("text") or "").strip()
    return ""


def _table_text(table: dict[str, Any]) -> str:
    title = str(table.get("title") or table.get("caption") or table.get("table_id") or "").strip()
    markdown = str(table.get("markdown") or "").strip()
    if title and markdown:
        return f"{title}\n\n{markdown}"
    return title or markdown


def write_jsonl(chunks: list[dict[str, Any]], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in chunks), encoding="utf-8")


def embed_texts(texts: list[str], embed_url: str, embed_model: str, vector_dim: int) -> list[list[float]]:
    import numpy as np
    import requests

    response = requests.post(embed_url, json={"model": embed_model, "input": texts}, timeout=180)
    response.raise_for_status()
    rows = response.json()["data"]
    rows.sort(key=lambda row: row["index"])
    vectors = []
    for row in rows:
        arr = np.array(row["embedding"], dtype=np.float32)
        if arr.shape[0] != vector_dim:
            raise ValueError(f"embedding dimension {arr.shape[0]} != expected {vector_dim}")
        arr = arr / (np.linalg.norm(arr) + 1e-12)
        vectors.append(arr.tolist())
    return vectors


def connect_milvus() -> None:
    from pymilvus import connections

    connections.connect(
        "default",
        host=os.environ.get("SIQ_MILVUS_HOST", "127.0.0.1"),
        port=os.environ.get("SIQ_MILVUS_PORT", "19530"),
        db_name=os.environ.get("SIQ_MILVUS_DB_NAME", "default"),
    )


def init_collection(collection_name: str, vector_dim: int, reset: bool = False):
    from pymilvus import Collection, CollectionSchema, DataType, FieldSchema, utility

    if reset and utility.has_collection(collection_name):
        utility.drop_collection(collection_name)
    if not utility.has_collection(collection_name):
        fields = [
            FieldSchema(name="chunk_uid", dtype=DataType.VARCHAR, max_length=128, is_primary=True),
            FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=vector_dim),
            FieldSchema(name="batch_tag", dtype=DataType.VARCHAR, max_length=128),
            FieldSchema(name="metadata", dtype=DataType.JSON),
        ]
        schema = CollectionSchema(fields, description=f"SIQ generic document chunks: {collection_name}")
        collection = Collection(collection_name, schema)
        collection.create_index("vector", {"metric_type": "IP", "index_type": "HNSW", "params": {"M": 16, "efConstruction": 128}})
    collection = Collection(collection_name)
    collection.load()
    return collection


def _milvus_metadata(item: dict[str, Any], *, collection_name: str, batch_tag: str) -> dict[str, Any]:
    metadata = dict(item.get("metadata") or {})
    metadata.update({
        "milvus_collection": collection_name,
        "batch_tag": batch_tag,
        "text": item.get("text") or "",
        "citation": _citation(metadata),
    })
    return metadata


def _citation(metadata: dict[str, Any]) -> str:
    filename = str(metadata.get("filename") or "document")
    page = metadata.get("page_number")
    section = str(metadata.get("section_title") or "").strip()
    suffix = f", p{page}" if page not in (None, "", 0) else ""
    if section:
        suffix += f", {section}"
    return f"{filename}{suffix}"


def ingest_milvus(
    chunks: list[dict[str, Any]],
    *,
    collection_name: str,
    batch_tag: str,
    embed_url: str,
    embed_model: str,
    vector_dim: int,
    batch_size: int,
    reset_collection: bool = False,
) -> int:
    connect_milvus()
    target = init_collection(collection_name, vector_dim, reset=reset_collection)
    inserted = 0
    for index in range(0, len(chunks), batch_size):
        batch = chunks[index : index + batch_size]
        vectors = embed_texts([str(item.get("text") or "") for item in batch], embed_url, embed_model, vector_dim)
        target.upsert([
            [item["chunk_uid"] for item in batch],
            vectors,
            [batch_tag] * len(batch),
            [_milvus_metadata(item, collection_name=collection_name, batch_tag=batch_tag) for item in batch],
        ])
        inserted += len(batch)
    target.flush()
    return inserted


def write_report(summary: dict[str, Any], report: Path | None) -> None:
    if not report:
        return
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build generic document chunks for Milvus ingestion.")
    parser.add_argument("package_dir", type=Path)
    parser.add_argument("--collection", default=DEFAULT_COLLECTION)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument("--milvus", action="store_true", help="Embed and upsert chunks into Milvus. Default only writes JSONL.")
    parser.add_argument("--batch-tag", default="generic-documents")
    parser.add_argument("--embed-url", default=os.environ.get("SIQ_EMBED_URL", "http://127.0.0.1:8013/v1/embeddings"))
    parser.add_argument("--embed-model", default=os.environ.get("SIQ_EMBED_MODEL", "Qwen3-VL-Embedding-2B"))
    parser.add_argument("--vector-dim", type=int, default=DEFAULT_VECTOR_DIM)
    parser.add_argument("--batch-size", type=int, default=int(os.environ.get("SIQ_DOCUMENT_VECTOR_BATCH_SIZE", "32")))
    parser.add_argument("--reset-collection", action="store_true")
    args = parser.parse_args()

    chunks = iter_chunks(args.package_dir, collection_name=args.collection)
    if args.output:
        write_jsonl(chunks, args.output)
    inserted = 0
    if args.milvus:
        inserted = ingest_milvus(
            chunks,
            collection_name=args.collection,
            batch_tag=args.batch_tag,
            embed_url=args.embed_url,
            embed_model=args.embed_model,
            vector_dim=args.vector_dim,
            batch_size=max(1, args.batch_size),
            reset_collection=args.reset_collection,
        )
    summary = {
        "ok": True,
        "chunk_count": len(chunks),
        "collection": args.collection,
        "output": str(args.output) if args.output else "",
        "report": str(args.report) if args.report else "",
        "milvus": bool(args.milvus),
        "milvus_inserted": inserted,
        "batch_tag": args.batch_tag,
        "vector_dim": args.vector_dim,
    }
    write_report(summary, args.report)
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
