#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CASE_SET = REPO_ROOT / "data" / "wiki" / "us" / "_meta" / "case_set_50_us_10k.json"
DEFAULT_REPORT = REPO_ROOT / "data" / "wiki" / "us" / "_meta" / "case_set_50_us_10k_ingest_report.json"
POSTGRES_IMPORT_PATH = REPO_ROOT / "db" / "imports" / "import_sec_filing_to_postgres.py"
MILVUS_INGEST_PATH = REPO_ROOT / "scripts" / "vector-index" / "milvus-ingestion" / "ingest_sec_wiki_chunks.py"
BUILD_PACKAGE_PATH = REPO_ROOT / "scripts" / "us-sec" / "build_sec_evidence_package.py"
DEFAULT_DOWNLOADS_ROOT = REPO_ROOT / "data" / "market-report-finder" / "downloads" / "US"
DEFAULT_VECTOR_DIM = int(os.environ.get("SIQ_EMBED_VECTOR_DIM", "1024"))


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _default_collection() -> str:
    contract = load_module(REPO_ROOT / "db" / "imports" / "market_ingestion_contract.py", "siq_market_ingestion_contract_for_us_sec_ingest")
    target = contract.target_for_market("US")
    collection = str(target.default_collection or "").strip()
    if not collection:
        raise RuntimeError("US market ingestion contract is missing default_collection")
    return os.environ.get("SIQ_US_SEC_MILVUS_COLLECTION", collection).strip() or collection


DEFAULT_COLLECTION = _default_collection()


pg_import = load_module(POSTGRES_IMPORT_PATH, "siq_sec_pg_import")
_milvus_ingest = None


def load_milvus_ingest():
    global _milvus_ingest
    if _milvus_ingest is None:
        _milvus_ingest = load_module(MILVUS_INGEST_PATH, "siq_sec_milvus_ingest")
    return _milvus_ingest


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def resolve_repo_path(value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else REPO_ROOT / path


def load_case_items(case_set_path: Path, *, include_fail: bool, tickers: set[str] | None) -> list[dict[str, Any]]:
    payload = read_json(case_set_path)
    items = payload.get("items") or []
    if not isinstance(items, list):
        raise SystemExit(f"Invalid case set items: {case_set_path}")
    selected = []
    for item in items:
        if not isinstance(item, dict):
            continue
        ticker = str(item.get("ticker") or "").upper()
        if tickers and ticker not in tickers:
            continue
        quality = str(item.get("quality_status") or "").lower()
        if quality == "fail" and not include_fail:
            continue
        package_path = resolve_repo_path(item.get("package_path"))
        if not package_path or not (package_path / "manifest.json").is_file():
            continue
        selected.append({**item, "ticker": ticker, "package_path": str(package_path)})
    return selected


def scan_download_items(downloads_root: Path, *, tickers: set[str] | None, forms: set[str] | None, limit: int = 0) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(downloads_root.rglob("*")):
        if path.suffix.lower() not in {".htm", ".html"}:
            continue
        metadata_path = path.with_suffix(path.suffix + ".metadata.json")
        metadata = read_json(metadata_path)
        candidate = metadata.get("candidate") if isinstance(metadata, dict) else {}
        if not isinstance(candidate, dict):
            candidate = {}
        ticker = str(candidate.get("ticker") or _ticker_from_filename(path.name) or "").upper()
        form = str(candidate.get("form") or candidate.get("report_type") or _form_from_filename(path.name) or "").upper()
        if tickers and ticker not in tickers:
            continue
        if forms and form not in forms:
            continue
        rows.append({"source_path": str(path), "metadata_path": str(metadata_path) if metadata_path.exists() else None, "ticker": ticker, "form": form})
        if limit and len(rows) >= limit:
            break
    return rows


def build_package_from_download(item: dict[str, Any], *, output_root: Path, force: bool) -> Path:
    args = [sys.executable, str(BUILD_PACKAGE_PATH), item["source_path"], "--output-root", str(output_root)]
    if item.get("metadata_path"):
        args.extend(["--metadata", item["metadata_path"]])
    if force:
        args.append("--force")
    completed = subprocess.run(args, cwd=str(REPO_ROOT), check=False, capture_output=True, text=True, timeout=900)
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout)[-4000:])
    return Path(completed.stdout.strip().splitlines()[-1])


def _ticker_from_filename(filename: str) -> str | None:
    match = re.search(r"_US_([A-Z0-9.-]+)_", filename)
    return match.group(1) if match else None


def _form_from_filename(filename: str) -> str | None:
    for token in ("10-K", "10-Q", "20-F", "6-K"):
        if f"_{token}_" in filename or token in filename:
            return token
    return None


def package_counts(package_dir: Path, chunks: list[dict[str, Any]] | None = None) -> dict[str, int]:
    facts = read_json(package_dir / "xbrl" / "facts_raw.json").get("facts") or []
    metrics = read_json(package_dir / "metrics" / "normalized_metrics.json").get("metrics") or []
    sections = read_json(package_dir / "sections.json").get("sections") or []
    evidence = read_json(package_dir / "qa" / "source_map.json").get("entries") or []
    tables = [path for path in (package_dir / "tables").glob("table_*.json") if path.stem.removeprefix("table_").isdigit()]
    chunks = chunks or []
    return {
        "xbrl_facts": len(facts),
        "normalized_metrics": len(metrics),
        "sections": len(sections),
        "tables": len(tables),
        "evidence_items": len(evidence),
        "retrieval_chunks": len(chunks),
        "section_chunks": sum(1 for chunk in chunks if chunk["metadata"].get("doc_type") == "sec_filing_section"),
        "metric_chunks": sum(1 for chunk in chunks if chunk["metadata"].get("doc_type") == "sec_metric_evidence"),
    }


def build_relationship_summary(package_dir: Path) -> dict[str, Any]:
    manifest = read_json(package_dir / "manifest.json")
    sections = read_json(package_dir / "sections.json").get("sections") or []
    tables = read_json(package_dir / "tables" / "table_index.json").get("tables") or []
    metrics = read_json(package_dir / "metrics" / "normalized_metrics.json").get("metrics") or []
    dimensions: dict[str, int] = {}
    statement_metrics: dict[str, int] = {}
    section_tables: dict[str, int] = {}
    for table in tables:
        section_id = str(table.get("section_id") or "unknown")
        section_tables[section_id] = section_tables.get(section_id, 0) + 1
    for metric in metrics:
        statement = str(metric.get("statement_type") or "unknown")
        statement_metrics[statement] = statement_metrics.get(statement, 0) + 1
        for axis, member in (metric.get("dimensions") or {}).items():
            key = f"{axis}={member}"
            dimensions[key] = dimensions.get(key, 0) + 1
    return {
        "ticker": manifest.get("ticker"),
        "filing_id": manifest.get("filing_id"),
        "quality_status": manifest.get("quality_status"),
        "sections": [{"section_id": s.get("section_id"), "title": s.get("section_title"), "tables": section_tables.get(s.get("section_id"), 0)} for s in sections],
        "statement_metrics": statement_metrics,
        "dimension_count": len(dimensions),
        "top_dimensions": sorted(dimensions.items(), key=lambda item: item[1], reverse=True)[:12],
        "relationship_note": (
            "Chunks preserve filing -> section/table/metric -> evidence/raw_fact relations. "
            "Metric chunks retain XBRL dimensions to separate consolidated facts from segment, subsidiary, investee, class-of-stock, or note-level facts."
        ),
    }


def connect_postgres(database_url: str | None):
    return pg_import.psycopg.connect(pg_import.database_url(database_url), autocommit=False)


def upsert_retrieval_chunks(conn: Any, package_dir: Path, chunks: list[dict[str, Any]], *, collection: str, batch_tag: str, embedded: bool) -> int:
    manifest = read_json(package_dir / "manifest.json")
    filing_id = manifest["filing_id"]
    parse_run_row = conn.execute(
        "select parse_run_id from sec_us.parse_runs where filing_id = %s order by completed_at desc nulls last, parse_run_id desc limit 1",
        (filing_id,),
    ).fetchone()
    parse_run_id = parse_run_row[0] if parse_run_row else None
    for chunk in chunks:
        metadata = chunk["metadata"]
        conn.execute(
            """
            insert into sec_us.retrieval_chunks (
              chunk_uid, filing_id, parse_run_id, ticker, collection_name, batch_tag, doc_type,
              evidence_level, section_id, section_title, table_id, canonical_name, concept,
              period_key, segment_key, dimensions, evidence_id, raw_fact_id, wiki_path,
              source_url, metadata, text_hash, embedded, updated_at
            ) values (
              %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now()
            )
            on conflict (chunk_uid) do update set
              parse_run_id = excluded.parse_run_id,
              collection_name = excluded.collection_name,
              batch_tag = excluded.batch_tag,
              metadata = excluded.metadata,
              text_hash = excluded.text_hash,
              embedded = excluded.embedded,
              updated_at = now()
            """,
            (
                chunk["chunk_uid"],
                filing_id,
                parse_run_id,
                metadata.get("ticker"),
                collection,
                batch_tag,
                metadata.get("doc_type"),
                metadata.get("evidence_level"),
                metadata.get("section_id"),
                metadata.get("section_title"),
                metadata.get("table_id"),
                metadata.get("canonical_name"),
                metadata.get("concept"),
                metadata.get("period_key"),
                metadata.get("segment_key"),
                pg_import.Jsonb(metadata.get("dimensions") or {}),
                metadata.get("evidence_id"),
                metadata.get("raw_fact_id"),
                metadata.get("wiki_path"),
                metadata.get("source_url"),
                pg_import.Jsonb(metadata),
                text_hash(chunk["text"]),
                embedded,
            ),
        )
    return len(chunks)


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest a curated US SEC case set from wiki packages into PostgreSQL, with optional Milvus ingestion.")
    parser.add_argument("--case-set", type=Path, default=DEFAULT_CASE_SET)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--collection", default=DEFAULT_COLLECTION)
    parser.add_argument("--batch-tag", default="us-sec-case-set-50")
    parser.add_argument("--embed-url", default=os.environ.get("SIQ_EMBED_URL", "http://127.0.0.1:8000/v1/embeddings"))
    parser.add_argument("--embed-model", default=os.environ.get("SIQ_EMBED_MODEL", "Qwen3-VL-Embedding-2B"))
    parser.add_argument("--vector-dim", type=int, default=DEFAULT_VECTOR_DIM)
    parser.add_argument("--tickers", default="", help="Comma-separated ticker subset.")
    parser.add_argument("--ticker", default="", help="Single ticker alias for --tickers.")
    parser.add_argument("--form", default="", help="Comma-separated SEC form subset when scanning downloads, e.g. 10-K,20-F.")
    parser.add_argument("--downloads-root", type=Path, default=DEFAULT_DOWNLOADS_ROOT)
    parser.add_argument("--output-root", type=Path, default=REPO_ROOT / "data" / "wiki" / "us")
    parser.add_argument("--scan-downloads", action="store_true", help="Scan downloads/US HTML files and build packages before ingesting.")
    parser.add_argument("--limit", type=int, default=0, help="Limit scanned downloads.")
    parser.add_argument("--force", action="store_true", help="Rebuild existing packages when scanning downloads.")
    parser.add_argument("--import-db", action="store_true", help="Alias for --postgres.")
    parser.add_argument("--include-fail", action="store_true", help="Include quality_status=fail packages.")
    parser.add_argument("--postgres", action="store_true", help="Import packages and retrieval chunk audit rows into PostgreSQL.")
    parser.add_argument("--milvus", action="store_true", help="Embed and upsert chunks into Milvus.")
    parser.add_argument("--ddl", action="store_true", help="Apply sec_us DDL before PostgreSQL import.")
    parser.add_argument("--dry-run", action="store_true", help="Plan only; do not write PostgreSQL or Milvus.")
    args = parser.parse_args()

    ticker_text = ",".join(part for part in (args.tickers, args.ticker) if part)
    ticker_filter = {item.strip().upper() for item in ticker_text.split(",") if item.strip()} or None
    form_filter = {item.strip().upper() for item in args.form.split(",") if item.strip()} or None
    case_set_path = args.case_set if args.case_set.is_absolute() else REPO_ROOT / args.case_set
    if args.scan_downloads:
        scanned = scan_download_items(args.downloads_root if args.downloads_root.is_absolute() else REPO_ROOT / args.downloads_root, tickers=ticker_filter, forms=form_filter, limit=args.limit)
        items = []
        for row in scanned:
            try:
                package_dir = build_package_from_download(row, output_root=args.output_root if args.output_root.is_absolute() else REPO_ROOT / args.output_root, force=args.force)
                manifest = read_json(package_dir / "manifest.json")
                items.append({**row, "package_path": str(package_dir), "quality_status": manifest.get("quality_status"), "ticker": manifest.get("ticker")})
            except Exception as exc:
                items.append({**row, "package_path": "", "quality_status": "fail", "build_error": str(exc)})
        items = [item for item in items if item.get("package_path")]
    else:
        items = load_case_items(case_set_path, include_fail=args.include_fail, tickers=ticker_filter)
    if args.import_db:
        args.postgres = True
    report: dict[str, Any] = {
        "schema_version": "siq_us_sec_case_set_ingest_report_v1",
        "generated_at": now_iso(),
        "case_set": str(case_set_path),
        "dry_run": args.dry_run,
        "postgres_requested": bool(args.postgres),
        "milvus_requested": bool(args.milvus),
        "collection": args.collection,
        "batch_tag": args.batch_tag,
        "package_count": len(items),
        "summary": {
            "xbrl_facts": 0,
            "normalized_metrics": 0,
            "sections": 0,
            "tables": 0,
            "evidence_items": 0,
            "retrieval_chunks": 0,
            "section_chunks": 0,
            "metric_chunks": 0,
            "quality": {},
        },
        "items": [],
        "warnings": [],
    }

    conn = None
    milvus_ingest = load_milvus_ingest() if args.milvus else None
    if args.postgres and not args.dry_run:
        conn = connect_postgres(args.database_url)
        if args.ddl:
            pg_import.run_ddl(conn)
            conn.commit()

    try:
        for item in items:
            package_dir = Path(item["package_path"])
            manifest = read_json(package_dir / "manifest.json")
            chunks = milvus_ingest.iter_chunks(package_dir, include_sections=True, include_metrics=True) if milvus_ingest else []
            counts = package_counts(package_dir, chunks)
            relationship = build_relationship_summary(package_dir)
            quality = str(manifest.get("quality_status") or item.get("quality_status") or "unknown")
            report["summary"]["quality"][quality] = report["summary"]["quality"].get(quality, 0) + 1
            for key, value in counts.items():
                report["summary"][key] += value
            row = {
                "ticker": manifest.get("ticker"),
                "company_name": manifest.get("company_name"),
                "filing_id": manifest.get("filing_id"),
                "accession_number": manifest.get("accession_number"),
                "fiscal_year": manifest.get("fiscal_year"),
                "quality_status": quality,
                "package_path": str(package_dir),
                "counts": counts,
                "relationship_summary": relationship,
                "postgres": "skipped",
                "milvus": "skipped",
            }
            if conn is not None:
                parse_run_id = pg_import.import_package(conn, package_dir, "sec_us")
                audit_count = 0
                if milvus_ingest:
                    audit_count = upsert_retrieval_chunks(conn, package_dir, chunks, collection=args.collection, batch_tag=args.batch_tag, embedded=bool(args.milvus))
                conn.commit()
                row["postgres"] = {"status": "imported", "parse_run_id": parse_run_id, "retrieval_chunks": audit_count}
            if args.milvus and not args.dry_run:
                assert milvus_ingest is not None
                inserted = milvus_ingest.ingest(
                    package_dir,
                    args.collection,
                    args.batch_tag,
                    args.embed_url,
                    args.embed_model,
                    args.vector_dim,
                    dry_run=False,
                    include_sections=True,
                    include_metrics=True,
                )
                row["milvus"] = {"status": "upserted", "chunks": inserted}
            report["items"].append(row)
    finally:
        if conn is not None:
            conn.close()

    write_json(args.report if args.report.is_absolute() else REPO_ROOT / args.report, report)
    print(json.dumps({"report": str(args.report), "package_count": len(items), "summary": report["summary"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
