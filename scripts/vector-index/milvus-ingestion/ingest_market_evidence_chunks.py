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
DEFAULT_VECTOR_DIM = int(os.environ.get("SIQ_EMBED_VECTOR_DIM", "1024"))
COLLECTIONS = {
    "US": "siq_us_sec_filings",
    "HK": "siq_hk_reports",
    "JP": "siq_jp_reports",
    "KR": "siq_kr_reports",
    "EU": "siq_eu_reports",
}


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def stable_id(*parts: Any) -> str:
    return hashlib.sha256("\x1f".join("" if part is None else str(part) for part in parts).encode("utf-8")).hexdigest()


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def repo_relative(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def strip_frontmatter(text: str) -> str:
    return re.sub(r"\A---\n.*?\n---\n", "", text, flags=re.DOTALL).strip()


def stable_parse_run_id(manifest: dict[str, Any]) -> str:
    return stable_id(
        manifest.get("filing_id"),
        manifest.get("parser_version"),
        manifest.get("rules_version"),
        json.dumps(manifest.get("artifact_hashes") or {}, sort_keys=True),
    )


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


def iter_chunks(package_dir: Path, *, include_sections: bool = True, include_tables: bool = True, include_metrics: bool = True, include_qa: bool = True) -> list[dict[str, Any]]:
    manifest = read_json(package_dir / "manifest.json")
    market = str(manifest.get("market") or "").upper()
    if market not in COLLECTIONS:
        raise ValueError(f"Unsupported market for chunks: {market}")
    parse_run_id = manifest.get("parse_run_id") or stable_parse_run_id(manifest)
    items: list[dict[str, Any]] = []
    if include_sections:
        items.extend(_section_chunks(package_dir, manifest, parse_run_id))
    if include_tables:
        items.extend(_table_chunks(package_dir, manifest, parse_run_id))
    if include_metrics:
        items.extend(_metric_chunks(package_dir, manifest, parse_run_id))
    if include_qa:
        items.extend(_qa_chunks(package_dir, manifest, parse_run_id))
    return items


def _base_metadata(package_dir: Path, manifest: dict[str, Any], parse_run_id: str | None, doc_type: str) -> dict[str, Any]:
    market = str(manifest.get("market") or "").upper()
    return {
        "schema_version": "siq_market_chunk_v1",
        "market": manifest.get("market"),
        "country": manifest.get("country"),
        "schema": _schema_for_market(market),
        "collection": COLLECTIONS.get(market),
        "ticker": manifest.get("ticker"),
        "company_id": manifest.get("company_id"),
        "company_name": manifest.get("company_name"),
        "filing_id": manifest.get("filing_id"),
        "parse_run_id": parse_run_id,
        "source_id": manifest.get("source_id"),
        "source_tier": manifest.get("source_tier"),
        "form": manifest.get("form"),
        "report_type": manifest.get("report_type"),
        "fiscal_year": manifest.get("fiscal_year"),
        "fiscal_period": manifest.get("fiscal_period"),
        "period_end": manifest.get("period_end"),
        "document_format": manifest.get("document_format"),
        "quality_status": manifest.get("quality_status"),
        "source_url": manifest.get("source_url"),
        "wiki_package_path": repo_relative(package_dir),
        "doc_type": doc_type,
        "source_type": doc_type,
        "evidence_id": None,
    }


def _source_entries_by_local_path(package_dir: Path) -> dict[str, dict[str, Any]]:
    payload = read_json(package_dir / "qa" / "source_map.json")
    entries = payload.get("entries") if isinstance(payload, dict) else []
    if not isinstance(entries, list):
        return {}
    return {
        str(entry.get("local_path")): entry
        for entry in entries
        if isinstance(entry, dict) and entry.get("local_path")
    }


def _section_chunks(package_dir: Path, manifest: dict[str, Any], parse_run_id: str | None) -> list[dict[str, Any]]:
    items = []
    source_by_path = _source_entries_by_local_path(package_dir)
    for path in sorted((package_dir / "sections").glob("*.md")):
        text = strip_frontmatter(path.read_text(encoding="utf-8"))
        local_path = f"sections/{path.name}"
        evidence = source_by_path.get(local_path) or {}
        for index, chunk in enumerate(split_text(text), start=1):
            uid = stable_id(manifest.get("filing_id"), "section", path.name, index, chunk)
            metadata = {
                **_base_metadata(package_dir, manifest, parse_run_id, "section"),
                "wiki_path": repo_relative(path),
                "evidence_id": evidence.get("evidence_id"),
                "source_type": evidence.get("source_type") or "section",
                "section_id": path.stem,
                "chunk_index": index,
                "chunk_uid": uid,
            }
            items.append({"chunk_uid": uid, "text": chunk, "metadata": metadata})
    return items


def _table_chunks(package_dir: Path, manifest: dict[str, Any], parse_run_id: str | None) -> list[dict[str, Any]]:
    payload = read_json(package_dir / "tables" / "table_index.json")
    source_by_path = _source_entries_by_local_path(package_dir)
    items = []
    for table in payload.get("tables") or []:
        local_path = str(table.get("table_json_path") or _table_json_path(table) or "")
        table_path = package_dir / local_path
        rows = read_json(table_path).get("rows") if table_path.exists() else None
        text = _table_text(manifest, table, rows if isinstance(rows, list) else [])
        uid = stable_id(manifest.get("filing_id"), "table", table.get("table_id"), text)
        evidence = source_by_path.get(local_path) or {}
        metadata = {
            **_base_metadata(package_dir, manifest, parse_run_id, "table"),
            "evidence_id": evidence.get("evidence_id"),
            "source_type": evidence.get("source_type") or _table_source_type(manifest, table),
            "table_id": table.get("table_id"),
            "table_index": table.get("table_index"),
            "page_number": table.get("page_number"),
            "html_anchor": table.get("html_anchor") or (table.get("raw") or {}).get("html_anchor"),
            "xpath": table.get("xpath") or (table.get("raw") or {}).get("xpath"),
            "wiki_path": repo_relative(table_path) if table_path.exists() else None,
            "chunk_uid": uid,
        }
        items.append({"chunk_uid": uid, "text": text, "metadata": metadata})
    return items


def _table_json_path(table: dict[str, Any]) -> str | None:
    table_index = table.get("table_index")
    if table_index is None:
        return None
    try:
        return f"tables/table_{int(table_index):04d}.json"
    except (TypeError, ValueError):
        return None


def _table_source_type(manifest: dict[str, Any], table: dict[str, Any]) -> str:
    source_type = str(table.get("source_type") or (table.get("raw") or {}).get("source_type") or "").lower()
    if source_type:
        return source_type
    document_format = str(manifest.get("document_format") or "").lower()
    if document_format in {"html", "ixbrl_xhtml", "xhtml"} and table.get("page_number") is None:
        return "html_table"
    return "pdf_table"


def _metric_chunks(package_dir: Path, manifest: dict[str, Any], parse_run_id: str | None) -> list[dict[str, Any]]:
    payload = read_json(package_dir / "metrics" / "normalized_metrics.json")
    items = []
    for metric in payload.get("metrics") or []:
        text = (
            f"{manifest.get('ticker')} {manifest.get('company_name')} {manifest.get('fiscal_year')} "
            f"{manifest.get('form')} {metric.get('statement_type')} metric {metric.get('canonical_name')} "
            f"equals {metric.get('value')} {metric.get('unit') or metric.get('currency') or ''} "
            f"for {metric.get('period_key')}. Evidence {metric.get('evidence_id')}."
        )
        uid = stable_id(manifest.get("filing_id"), "fact", metric.get("metric_id"), text)
        metadata = {
            **_base_metadata(package_dir, manifest, parse_run_id, "fact"),
            "evidence_id": metric.get("evidence_id"),
            "source_type": metric.get("source_type") or ("xbrl_fact" if metric.get("xbrl_tag") else "normalized_metric"),
            "canonical_name": metric.get("canonical_name"),
            "period_key": metric.get("period_key"),
            "statement_type": metric.get("statement_type"),
            "xbrl_tag": metric.get("xbrl_tag"),
            "source_url": manifest.get("source_url"),
            "wiki_path": repo_relative(package_dir / "metrics" / "normalized_metrics.json"),
            "chunk_uid": uid,
        }
        items.append({"chunk_uid": uid, "text": text, "metadata": metadata})
    return items


def _qa_chunks(package_dir: Path, manifest: dict[str, Any], parse_run_id: str | None) -> list[dict[str, Any]]:
    quality = read_json(package_dir / "qa" / "quality_report.json")
    text = f"{manifest.get('ticker')} {manifest.get('fiscal_year')} quality report: {json.dumps(quality, ensure_ascii=False, sort_keys=True)[:3000]}"
    uid = stable_id(manifest.get("filing_id"), "qa", text)
    metadata = {
        **_base_metadata(package_dir, manifest, parse_run_id, "qa"),
        "wiki_path": repo_relative(package_dir / "qa" / "quality_report.json"),
        "chunk_uid": uid,
    }
    return [{"chunk_uid": uid, "text": text, "metadata": metadata}]


def _table_text(manifest: dict[str, Any], table: dict[str, Any], rows: list[Any]) -> str:
    preview_rows = []
    for row in rows[:12]:
        if isinstance(row, list):
            preview_rows.append(" | ".join(str(cell) for cell in row[:8]))
    return (
        f"{manifest.get('ticker')} {manifest.get('fiscal_year')} table {table.get('table_index')} "
        f"{table.get('title') or ''} page {table.get('page_number')}. "
        + " / ".join(preview_rows)
    )


def _schema_for_market(market: str) -> str:
    return {"US": "sec_us", "HK": "pdf2md_hk", "JP": "edinet_jp", "KR": "dart_kr", "EU": "eu_ifrs"}.get(market, "")


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


def init_collection(collection_name: str, vector_dim: int, reset: bool = False):
    from pymilvus import Collection, CollectionSchema, DataType, FieldSchema, utility

    if collection_name not in set(COLLECTIONS.values()):
        raise SystemExit(f"Unsupported market collection: {collection_name}")
    if reset and utility.has_collection(collection_name):
        utility.drop_collection(collection_name)
    if not utility.has_collection(collection_name):
        fields = [
            FieldSchema(name="chunk_uid", dtype=DataType.VARCHAR, max_length=128, is_primary=True),
            FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=vector_dim),
            FieldSchema(name="batch_tag", dtype=DataType.VARCHAR, max_length=128),
            FieldSchema(name="metadata", dtype=DataType.JSON),
        ]
        collection = Collection(collection_name, CollectionSchema(fields, description=f"SIQ market evidence chunks: {collection_name}"))
        collection.create_index("vector", {"metric_type": "IP", "index_type": "HNSW", "params": {"M": 16, "efConstruction": 128}})
    collection = Collection(collection_name)
    collection.load()
    return collection


def ingest(package_dir: Path, collection_name: str | None, batch_tag: str, embed_url: str, embed_model: str, vector_dim: int, dry_run: bool = False) -> int:
    manifest = read_json(package_dir / "manifest.json")
    collection = collection_name or COLLECTIONS[str(manifest.get("market")).upper()]
    chunks = iter_chunks(package_dir)
    if dry_run:
        print(json.dumps({"collection": collection, "chunk_count": len(chunks), "first": chunks[0]["metadata"] if chunks else None}, ensure_ascii=False, indent=2))
        return len(chunks)
    from pymilvus import connections

    connections.connect(
        "default",
        host=os.environ.get("SIQ_MILVUS_HOST", "127.0.0.1"),
        port=os.environ.get("SIQ_MILVUS_PORT", "19530"),
        db_name=os.environ.get("SIQ_MILVUS_DB_NAME", "default"),
    )
    target = init_collection(collection, vector_dim)
    batch_size = int(os.environ.get("SIQ_MARKET_VECTOR_BATCH_SIZE", "32"))
    inserted = 0
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i : i + batch_size]
        vectors = embed_texts([item["text"] for item in batch], embed_url, embed_model, vector_dim)
        target.upsert([[item["chunk_uid"] for item in batch], vectors, [batch_tag] * len(batch), [item["metadata"] for item in batch]])
        inserted += len(batch)
    target.flush()
    return inserted


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest US/HK/JP/KR/EU market evidence package chunks into Milvus.")
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--collection", default=None)
    parser.add_argument("--batch-tag", default="market-evidence")
    parser.add_argument("--embed-url", default=os.environ.get("SIQ_EMBED_URL", "http://127.0.0.1:8000/v1/embeddings"))
    parser.add_argument("--embed-model", default=os.environ.get("SIQ_EMBED_MODEL", "Qwen3-VL-Embedding-2B"))
    parser.add_argument("--vector-dim", type=int, default=DEFAULT_VECTOR_DIM)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    count = ingest(args.package.resolve(), args.collection, args.batch_tag, args.embed_url, args.embed_model, args.vector_dim, dry_run=args.dry_run)
    print(f"chunks={count}")


if __name__ == "__main__":
    main()
