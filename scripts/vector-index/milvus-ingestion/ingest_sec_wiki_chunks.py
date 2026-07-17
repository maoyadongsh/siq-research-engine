#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

import numpy as np
import requests
from pymilvus import Collection, CollectionSchema, DataType, FieldSchema, connections, utility

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_COLLECTION = "siq_us_sec_filings"
DEFAULT_VECTOR_DIM = int(os.environ.get("SIQ_EMBED_VECTOR_DIM", "1024"))
METRIC_ALIASES = {
    "revenue": ["sales", "net sales", "total revenue", "营业收入", "收入"],
    "gross_profit": ["gross margin dollars", "毛利"],
    "operating_income": ["operating profit", "income from operations", "经营利润"],
    "net_income": ["net earnings", "profit attributable", "净利润"],
    "basic_eps": ["basic earnings per share", "基本每股收益"],
    "diluted_eps": ["diluted earnings per share", "摊薄每股收益"],
    "total_assets": ["assets", "总资产"],
    "total_liabilities": ["liabilities", "总负债"],
    "total_equity": ["shareholders equity", "stockholders equity", "股东权益"],
    "cash_and_cash_equivalents": ["cash", "cash equivalents", "现金及等价物"],
    "operating_cash_flow": ["cash provided by operating activities", "经营现金流"],
    "capital_expenditures": ["capex", "capital expenditure", "资本开支"],
    "free_cash_flow": ["fcf", "自由现金流"],
}


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def read_list_payload(path: Path, key: str) -> list[dict[str, Any]]:
    payload = read_json(path)
    rows = payload.get(key) if isinstance(payload, dict) else None
    return rows if isinstance(rows, list) else []


def stable_id(*parts: Any) -> str:
    return hashlib.sha256("\x1f".join("" if p is None else str(p) for p in parts).encode("utf-8")).hexdigest()


def strip_frontmatter(text: str) -> str:
    return re.sub(r"\A---\n.*?\n---\n", "", text, flags=re.DOTALL).strip()


def split_text(text: str, chunk_size: int = 900, overlap: int = 120) -> list[str]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(current) + len(paragraph) + 2 <= chunk_size:
            current = f"{current}\n\n{paragraph}".strip()
        else:
            if current:
                chunks.append(current)
            if len(paragraph) <= chunk_size:
                current = paragraph
            else:
                step = max(1, chunk_size - overlap)
                for i in range(0, len(paragraph), step):
                    chunks.append(paragraph[i : i + chunk_size])
                current = ""
    if current:
        chunks.append(current)
    return chunks


def _repo_relative(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _quality_rank(status: str | None) -> int:
    return {"pass": 3, "warning": 2, "fail": 1}.get(str(status or "").lower(), 0)


def _section_role(section_id: str | None) -> str:
    value = str(section_id or "").lower()
    if value == "notes":
        return "financial_statement_notes"
    if value in {"item_8", "financial_statements"}:
        return "financial_statements"
    if value in {"item_7", "mda"}:
        return "mda"
    if value in {"item_1a", "risk_factors"}:
        return "risk_factors"
    if value in {"item_1", "business"}:
        return "business"
    return value or "unknown"


def _section_table_map(package_dir: Path) -> dict[str, dict[str, Any]]:
    payload = read_json(package_dir / "tables" / "table_index.json")
    result: dict[str, dict[str, Any]] = {}
    for table in payload.get("tables") or []:
        section_id = str(table.get("section_id") or "unknown")
        bucket = result.setdefault(section_id, {"table_ids": [], "financial_statement_table_count": 0})
        if table.get("table_id"):
            bucket["table_ids"].append(table["table_id"])
        if table.get("is_financial_statement_candidate"):
            bucket["financial_statement_table_count"] += 1
    return result


def _verified_section_anchor(package_dir: Path, anchor: Any) -> str | None:
    candidate = str(anchor or "").strip().lstrip("#")
    if not candidate:
        return None
    source_path = package_dir / "raw" / "filing.htm"
    if not source_path.is_file():
        return None
    escaped = re.escape(candidate)
    pattern = re.compile(rf"\b(?:id|name)\s*=\s*(['\"]){escaped}\1", flags=re.IGNORECASE)
    try:
        return candidate if pattern.search(source_path.read_text(encoding="utf-8", errors="ignore")) else None
    except OSError:
        return None


def _section_chunks(package_dir: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    sections_payload = read_json(package_dir / "sections.json")
    section_tables = _section_table_map(package_dir)
    items: list[dict[str, Any]] = []
    for section in sections_payload.get("sections") or []:
        path = package_dir / "sections" / section["file"]
        if not path.exists():
            continue
        section_id = section.get("section_id")
        table_info = section_tables.get(str(section_id), {})
        text = strip_frontmatter(path.read_text(encoding="utf-8"))
        source_url = manifest.get("source_url")
        source_anchor = _verified_section_anchor(package_dir, section.get("html_anchor"))
        source_target = _sec_source_target(source_url, source_anchor)
        for index, chunk in enumerate(split_text(text), start=1):
            chunk_uid = stable_id(manifest["filing_id"], "section", section_id, index, chunk)
            metadata = {
                "schema_version": "siq_chunk_v1",
                "market": "US",
                "doc_type": "sec_filing_section",
                "evidence_level": "source_doc",
                "ticker": manifest.get("ticker"),
                "cik": manifest.get("cik"),
                "company_name": manifest.get("company_name"),
                "form": manifest.get("form"),
                "accession_number": manifest.get("accession_number"),
                "filing_id": manifest.get("filing_id"),
                "report_id": _report_id(manifest),
                "parse_run_id": manifest.get("parse_run_id"),
                "research_identity": _research_identity(manifest),
                "fiscal_year": manifest.get("fiscal_year"),
                "fiscal_period": manifest.get("fiscal_period"),
                "period_end": manifest.get("period_end"),
                "filing_date": manifest.get("filing_date"),
                "quality_status": manifest.get("quality_status"),
                "quality_rank": _quality_rank(manifest.get("quality_status")),
                "industry_profile": manifest.get("industry_profile"),
                "section_id": section_id,
                "section_title": section.get("section_title"),
                "section_role": _section_role(section_id),
                "section_order": section.get("section_order"),
                "text_hash": section.get("text_hash"),
                "related_table_ids": table_info.get("table_ids", [])[:80],
                "related_table_count": len(table_info.get("table_ids", [])),
                "financial_statement_table_count": table_info.get("financial_statement_table_count", 0),
                "wiki_package_path": _repo_relative(package_dir),
                "wiki_path": _repo_relative(path),
                "source_type": "sec_html_section",
                "source_family": "sec_filing_html",
                "citation_mode": "sec_html_section",
                "source_url": source_url,
                "source_anchor": source_anchor,
                "source_target": source_target,
                "target": source_target,
                "html_anchor": source_anchor,
                "chunk_uid": chunk_uid,
                "chunk_index": index,
                "citation": f"{manifest.get('ticker')} {manifest.get('fiscal_year')} {manifest.get('form')}, {section.get('section_title')}",
                "citation_url": source_target,
                "text": chunk,
            }
            items.append({"chunk_uid": chunk_uid, "text": chunk, "metadata": metadata})
    return items


def _metric_value_text(metric: dict[str, Any]) -> str:
    value = metric.get("value")
    unit = metric.get("unit") or metric.get("currency") or ""
    if value is None:
        return ""
    return f"{value} {unit}".strip()


def _metric_anchor(metric: dict[str, Any]) -> str | None:
    if metric.get("source_anchor"):
        return str(metric["source_anchor"])
    raw = metric.get("raw") if isinstance(metric.get("raw"), dict) else {}
    nested = raw.get("raw") if isinstance(raw.get("raw"), dict) else {}
    return nested.get("anchor") or raw.get("anchor")


def _metric_html_snippet(metric: dict[str, Any]) -> str | None:
    raw = metric.get("raw") if isinstance(metric.get("raw"), dict) else {}
    nested = raw.get("raw") if isinstance(raw.get("raw"), dict) else {}
    return nested.get("html_snippet") or raw.get("html_snippet")


def _sec_source_target(source_url: Any, source_anchor: Any = None) -> str | None:
    url = str(source_url or "").strip()
    if not url:
        return None
    base_url = url.split("#", 1)[0]
    anchor = str(source_anchor or "").strip().lstrip("#")
    return f"{base_url}#{anchor}" if anchor else base_url


def _report_id(manifest: dict[str, Any], metric: dict[str, Any] | None = None) -> str:
    metric = metric or {}
    existing = metric.get("report_id") or manifest.get("report_id")
    if existing:
        return str(existing)
    return "-".join(
        (
            str(manifest.get("fiscal_year") or "unknown"),
            str(manifest.get("form") or "filing").upper(),
            str(manifest.get("accession_number") or "unknown"),
        )
    )


def _research_identity(manifest: dict[str, Any], metric: dict[str, Any] | None = None) -> dict[str, Any]:
    metric = metric or {}
    return {
        "market": "US",
        "company_id": manifest.get("company_id"),
        "filing_id": metric.get("filing_id") or manifest.get("filing_id"),
        "report_id": _report_id(manifest, metric),
        "parse_run_id": metric.get("parse_run_id") or manifest.get("parse_run_id"),
    }


def _metric_text(manifest: dict[str, Any], metric: dict[str, Any]) -> str:
    canonical = metric.get("canonical_name") or metric.get("metric_name") or "metric"
    label = metric.get("label") or canonical.replace("_", " ")
    aliases = ", ".join(METRIC_ALIASES.get(str(canonical), []))
    alias_text = f" Aliases: {aliases}." if aliases else ""
    dimensions = metric.get("dimensions") or {}
    subject_scope = "consolidated reporting entity" if not dimensions else "dimension-specific disclosure, segment, subsidiary, investee, class, or note member"
    dimension_text = f" Dimensions: {json.dumps(dimensions, ensure_ascii=False, sort_keys=True)}." if dimensions else " Dimensions: consolidated/no explicit XBRL member."
    return (
        f"{manifest.get('ticker')} {manifest.get('company_name')} {manifest.get('fiscal_year')} {manifest.get('form')} "
        f"{metric.get('statement_type')} metric {canonical} ({label}, {metric.get('concept')}) "
        f"equals {_metric_value_text(metric)} for period {metric.get('period_key') or metric.get('period_end')}. "
        f"Subject scope: {subject_scope}. Fiscal period {metric.get('fiscal_period')}; duration type {metric.get('qtd_ytd_type')}; "
        f"segment {metric.get('segment_key') or 'consolidated'}. "
        f"Evidence id {metric.get('evidence_id')}; raw fact id {metric.get('raw_fact_id')}; accession {manifest.get('accession_number')}."
        f"{alias_text}{dimension_text}"
    )


def _metric_chunks(package_dir: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    metrics = read_list_payload(package_dir / "metrics" / "normalized_metrics.json", "metrics")
    items: list[dict[str, Any]] = []
    for metric in metrics:
        text = _metric_text(manifest, metric)
        chunk_uid = stable_id(manifest["filing_id"], "metric", metric.get("metric_id"), text)
        canonical = metric.get("canonical_name") or metric.get("metric_name")
        dimensions = metric.get("dimensions") or {}
        source_url = metric.get("source_url") or manifest.get("source_url")
        source_anchor = _metric_anchor(metric)
        source_target = metric.get("source_target") or _sec_source_target(source_url, source_anchor)
        metadata = {
            "schema_version": "siq_chunk_v1",
            "market": "US",
            "doc_type": "sec_metric_evidence",
            "evidence_level": "xbrl_fact",
            "ticker": manifest.get("ticker"),
            "cik": manifest.get("cik"),
            "company_name": manifest.get("company_name"),
            "form": manifest.get("form"),
            "accession_number": manifest.get("accession_number"),
            "filing_id": manifest.get("filing_id"),
            "report_id": _report_id(manifest, metric),
            "parse_run_id": metric.get("parse_run_id") or manifest.get("parse_run_id"),
            "research_identity": _research_identity(manifest, metric),
            "fiscal_year": manifest.get("fiscal_year"),
            "fiscal_period": manifest.get("fiscal_period"),
            "period_end": manifest.get("period_end"),
            "filing_date": manifest.get("filing_date"),
            "quality_status": manifest.get("quality_status"),
            "quality_rank": _quality_rank(manifest.get("quality_status")),
            "industry_profile": manifest.get("industry_profile"),
            "statement_type": metric.get("statement_type"),
            "canonical_name": canonical,
            "metric_aliases": METRIC_ALIASES.get(str(canonical), []),
            "concept": metric.get("concept"),
            "xbrl_tag": metric.get("xbrl_tag") or metric.get("concept"),
            "label": metric.get("label"),
            "value": metric.get("value"),
            "unit": metric.get("unit"),
            "currency": metric.get("currency"),
            "period_key": metric.get("period_key"),
            "metric_period_end": metric.get("period_end"),
            "qtd_ytd_type": metric.get("qtd_ytd_type"),
            "segment_key": metric.get("segment_key"),
            "dimensions": dimensions,
            "dimension_axes": list(dimensions.keys()),
            "dimension_members": list(dimensions.values()),
            "subject_scope": "consolidated" if not dimensions else "dimension_specific",
            "relationship_kind": "registrant_consolidated_metric" if not dimensions else "dimension_member_metric",
            "confidence": metric.get("confidence"),
            "evidence_id": metric.get("evidence_id"),
            "raw_fact_id": metric.get("raw_fact_id"),
            "wiki_package_path": _repo_relative(package_dir),
            "source_type": metric.get("source_type") or "sec_xbrl_fact",
            "source_family": metric.get("source_family") or "sec_ixbrl",
            "citation_mode": "sec_html_ixbrl",
            "source_url": source_url,
            "source_anchor": source_anchor,
            "source_target": source_target,
            "target": source_target,
            "html_anchor": source_anchor,
            "html_snippet": _metric_html_snippet(metric),
            "chunk_uid": chunk_uid,
            "chunk_index": 1,
            "citation": f"{manifest.get('ticker')} {manifest.get('fiscal_year')} {manifest.get('form')}, {canonical} {metric.get('period_key')}",
            "citation_url": source_target,
            "text": text,
        }
        items.append({"chunk_uid": chunk_uid, "text": text, "metadata": metadata})
    return items


def iter_chunks(package_dir: Path, include_sections: bool = True, include_metrics: bool = True) -> list[dict[str, Any]]:
    manifest = read_json(package_dir / "manifest.json")
    items: list[dict[str, Any]] = []
    if include_sections:
        items.extend(_section_chunks(package_dir, manifest))
    if include_metrics:
        items.extend(_metric_chunks(package_dir, manifest))
    return items


def embed_texts(texts: list[str], embed_url: str, embed_model: str, vector_dim: int) -> list[list[float]]:
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


def init_collection(collection_name: str, vector_dim: int, reset: bool = False) -> Collection:
    if collection_name != DEFAULT_COLLECTION:
        raise SystemExit(f"US SEC chunks must be written to {DEFAULT_COLLECTION}")
    if reset and utility.has_collection(collection_name):
        utility.drop_collection(collection_name)
    if not utility.has_collection(collection_name):
        fields = [
            FieldSchema(name="chunk_uid", dtype=DataType.VARCHAR, max_length=128, is_primary=True),
            FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=vector_dim),
            FieldSchema(name="batch_tag", dtype=DataType.VARCHAR, max_length=128),
            FieldSchema(name="metadata", dtype=DataType.JSON),
        ]
        collection = Collection(collection_name, CollectionSchema(fields, description="SIQ US SEC filing section chunks"))
        collection.create_index("vector", {"metric_type": "IP", "index_type": "HNSW", "params": {"M": 16, "efConstruction": 128}})
    collection = Collection(collection_name)
    collection.load()
    return collection


def ingest(
    package_dir: Path,
    collection_name: str,
    batch_tag: str,
    embed_url: str,
    embed_model: str,
    vector_dim: int,
    dry_run: bool = False,
    include_sections: bool = True,
    include_metrics: bool = True,
) -> int:
    chunks = iter_chunks(package_dir, include_sections=include_sections, include_metrics=include_metrics)
    if dry_run:
        print(json.dumps({"chunk_count": len(chunks), "first": chunks[0]["metadata"] if chunks else None}, ensure_ascii=False, indent=2))
        return len(chunks)
    connections.connect(
        "default",
        host=os.environ.get("SIQ_MILVUS_HOST", "127.0.0.1"),
        port=os.environ.get("SIQ_MILVUS_PORT", "19530"),
        db_name=os.environ.get("SIQ_MILVUS_DB_NAME", "default"),
    )
    collection = init_collection(collection_name, vector_dim)
    inserted = 0
    batch_size = int(os.environ.get("SIQ_SEC_VECTOR_BATCH_SIZE", "32"))
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i : i + batch_size]
        vectors = embed_texts([item["text"] for item in batch], embed_url, embed_model, vector_dim)
        collection.upsert([[item["chunk_uid"] for item in batch], vectors, [batch_tag] * len(batch), [item["metadata"] for item in batch]])
        inserted += len(batch)
    collection.flush()
    return inserted


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest US SEC wiki section chunks into Milvus.")
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--collection", default=os.environ.get("SIQ_US_SEC_MILVUS_COLLECTION", DEFAULT_COLLECTION))
    parser.add_argument("--batch-tag", default="us-sec")
    parser.add_argument("--embed-url", default=os.environ.get("SIQ_EMBED_URL", "http://127.0.0.1:8000/v1/embeddings"))
    parser.add_argument("--embed-model", default=os.environ.get("SIQ_EMBED_MODEL", "Qwen3-VL-Embedding-2B"))
    parser.add_argument("--vector-dim", type=int, default=DEFAULT_VECTOR_DIM)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--sections-only", action="store_true")
    parser.add_argument("--metrics-only", action="store_true")
    args = parser.parse_args()
    if args.sections_only and args.metrics_only:
        raise SystemExit("--sections-only and --metrics-only cannot be used together")
    count = ingest(
        args.package.resolve(),
        args.collection,
        args.batch_tag,
        args.embed_url,
        args.embed_model,
        args.vector_dim,
        args.dry_run,
        include_sections=not args.metrics_only,
        include_metrics=not args.sections_only,
    )
    print(f"chunks={count}")


if __name__ == "__main__":
    main()
