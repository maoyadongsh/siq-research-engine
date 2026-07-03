"""Offline Deal Evidence package builder.

P0-E deliberately stays local and deterministic: it reads already-bound
document-parser Markdown artifacts and writes evidence package files under the
deal package. It does not call LLMs, Hermes agents, PostgreSQL, or Milvus.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from services import deal_documents
from services import deal_store
from services import ic_policy
from services.path_config import DOCUMENT_PARSER_RESULTS_ROOT


EVIDENCE_ITEM_SCHEMA = "siq_deal_evidence_item_v1"
EVIDENCE_INDEX_SCHEMA = "siq_deal_evidence_index_v1"
EVIDENCE_QUALITY_SCHEMA = "siq_deal_evidence_quality_v1"
EVIDENCE_INGEST_DRY_RUN_SCHEMA = "siq_deal_evidence_ingest_dry_run_v1"
BUILD_MODE = "offline_document_md_v1"
INGEST_DRY_RUN_MODE = "deal_evidence_ingest_dry_run_v1"
MAX_CHUNK_CHARS = 1600
MAX_QUOTE_CHARS = 1200
ITEMS_PREVIEW_LIMIT = 50
ITEMS_PREVIEW_MAX_LIMIT = 200
ITEMS_PREVIEW_LIMIT_OPTIONS = (10, 20, 50, 100, 200)

DOC_BLOCK_RE = re.compile(r"<!--\s*DOC_BLOCK:\s*(?P<block_id>[^\s>]+)(?P<attrs>.*?)-->", re.I)
ATTR_RE = re.compile(r"(?P<key>[A-Za-z_][\w-]*)=(?P<value>\"[^\"]*\"|'[^']*'|[^\s>]+)")
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)

DIMENSION_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("finance", ("finance", "financial", "model", "valuation", "audit", "revenue", "income", "cash", "cap_table")),
    ("legal", ("legal", "contract", "license", "licence", "ip", "patent", "term_sheet", "regulatory")),
    ("risk", ("risk", "compliance", "security", "privacy", "litigation", "lawsuit", "sanction")),
    ("business", ("business", "bp", "deck", "teaser", "memo", "market", "customer", "product", "commercial", "founder")),
)
ROLE_HINTS_BY_DIMENSION = {
    "business": ["siq_ic_strategist", "siq_ic_sector_expert", "siq_ic_chairman"],
    "finance": ["siq_ic_finance_auditor", "siq_ic_chairman"],
    "legal": ["siq_ic_legal_scanner", "siq_ic_chairman"],
    "risk": ["siq_ic_risk_controller", "siq_ic_chairman"],
}


def _require_package_dir(deal_id: str, *, wiki_root: Path | str | None = None) -> Path:
    package_dir = deal_store.safe_deal_dir(deal_id, wiki_root=wiki_root)
    if not (package_dir / "manifest.json").is_file():
        raise FileNotFoundError(deal_id)
    deal_store.ensure_deal_package_dirs(package_dir)
    return package_dir


def _read_document_metadata(package_dir: Path) -> tuple[list[dict[str, Any]], list[str]]:
    metadata_dir = package_dir / "data_room" / "metadata"
    documents: list[dict[str, Any]] = []
    invalid_files: list[str] = []
    if not metadata_dir.exists():
        return documents, invalid_files
    for path in sorted(metadata_dir.glob("DOC-*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            invalid_files.append(path.name)
            continue
        if isinstance(payload, dict):
            documents.append(payload)
        else:
            invalid_files.append(path.name)
    documents.sort(key=lambda item: str(item.get("document_id") or ""))
    return documents, invalid_files


def _safe_parser_artifact_path(value: Any) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    if not raw:
        return ""
    path = Path(raw)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("parsed_artifact_path must be a relative parser artifact path")
    return path.as_posix().strip("/")[:300]


def _result_relative_path(task_id: str, path: Path) -> str:
    result_dir = DOCUMENT_PARSER_RESULTS_ROOT / task_id
    try:
        relative = path.resolve().relative_to(result_dir.resolve()).as_posix()
    except ValueError:
        relative = path.name
    return f"parser_results/{task_id}/{relative}"


def _safe_existing_result_file(result_dir: Path, candidate: Path) -> Path | None:
    if not candidate.is_file():
        return None
    try:
        candidate.resolve().relative_to(result_dir.resolve())
    except ValueError:
        return None
    return candidate


def _document_md_candidate(document: dict[str, Any]) -> tuple[Path | None, str, str | None]:
    task_id = deal_documents.validate_parser_task_id(str(document.get("parse_task_id") or ""))
    result_dir = DOCUMENT_PARSER_RESULTS_ROOT / task_id
    if not result_dir.is_dir():
        return None, f"parser_results/{task_id}/document.md", "missing_task_dir"

    artifact_path = _safe_parser_artifact_path(document.get("parsed_artifact_path"))
    candidates: list[Path] = []
    if artifact_path:
        artifact = result_dir / artifact_path
        if artifact.is_dir():
            candidates.append(artifact / "document.md")
        elif artifact.name == "document.md":
            candidates.append(artifact)
    candidates.append(result_dir / "document.md")

    for candidate in candidates:
        safe_candidate = _safe_existing_result_file(result_dir, candidate)
        if safe_candidate:
            return safe_candidate, _result_relative_path(task_id, safe_candidate), None
    expected = candidates[0] if candidates else result_dir / "document.md"
    return None, _result_relative_path(task_id, expected), "missing_document_md"


def _dimension_for_document(document: dict[str, Any]) -> str:
    haystack = " ".join(
        str(document.get(key) or "")
        for key in ("document_type", "original_filename", "filename", "source_note")
    ).lower().replace("-", "_").replace(" ", "_")
    for dimension, needles in DIMENSION_RULES:
        if any(needle in haystack for needle in needles):
            return dimension
    return "unknown"


def _parse_int(value: Any) -> int | None:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _parse_marker(line: str) -> dict[str, Any] | None:
    match = DOC_BLOCK_RE.search(line)
    if not match:
        return None
    attrs: dict[str, str] = {}
    for attr in ATTR_RE.finditer(match.group("attrs") or ""):
        value = attr.group("value").strip("\"'")
        attrs[attr.group("key")] = value
    anchor: dict[str, Any] = {"block_id": match.group("block_id")}
    page = _parse_int(attrs.get("page"))
    if page is not None:
        anchor["page"] = page
    if attrs.get("evidence"):
        anchor["source_evidence_id"] = attrs["evidence"]
    return anchor


def _markdown_segments(markdown: str) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    current_anchor: dict[str, Any] = {}
    current_lines: list[tuple[int, str]] = []

    def flush() -> None:
        nonlocal current_lines
        if current_lines:
            segments.append({"anchor": dict(current_anchor), "lines": current_lines})
            current_lines = []

    for line_no, line in enumerate(markdown.splitlines(), start=1):
        marker = _parse_marker(line)
        if marker:
            flush()
            current_anchor = {**marker, "marker_line": line_no}
            remainder = DOC_BLOCK_RE.sub("", line).strip()
            if remainder:
                current_lines.append((line_no, remainder))
            continue
        current_lines.append((line_no, line))
    flush()
    return segments


def _clean_line(line: str) -> str:
    return HTML_COMMENT_RE.sub("", line).rstrip()


def _paragraphs(lines: list[tuple[int, str]]) -> list[list[tuple[int, str]]]:
    paragraphs: list[list[tuple[int, str]]] = []
    current: list[tuple[int, str]] = []
    for line_no, line in lines:
        cleaned = _clean_line(line)
        if not cleaned.strip():
            if current:
                paragraphs.append(current)
                current = []
            continue
        current.append((line_no, cleaned))
    if current:
        paragraphs.append(current)
    return paragraphs


def _chunk_segment(segment: dict[str, Any]) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    current: list[list[tuple[int, str]]] = []
    current_chars = 0
    for paragraph in _paragraphs(segment.get("lines") or []):
        paragraph_text = "\n".join(line for _, line in paragraph).strip()
        if not paragraph_text:
            continue
        next_chars = current_chars + len(paragraph_text) + (2 if current else 0)
        if current and next_chars > MAX_CHUNK_CHARS:
            chunks.append(_make_chunk(segment.get("anchor") or {}, current))
            current = []
            current_chars = 0
        current.append(paragraph)
        current_chars += len(paragraph_text) + (2 if current_chars else 0)
    if current:
        chunks.append(_make_chunk(segment.get("anchor") or {}, current))
    return [chunk for chunk in chunks if chunk.get("text")]


def _make_chunk(anchor: dict[str, Any], paragraphs: list[list[tuple[int, str]]]) -> dict[str, Any]:
    lines = [line for paragraph in paragraphs for line in paragraph]
    line_numbers = [line_no for line_no, _ in lines]
    text = "\n\n".join("\n".join(line for _, line in paragraph).strip() for paragraph in paragraphs).strip()
    return {
        "anchor": dict(anchor),
        "line_start": min(line_numbers) if line_numbers else None,
        "line_end": max(line_numbers) if line_numbers else None,
        "text": text,
    }


def _quote_text(text: str) -> str:
    normalized = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(normalized) <= MAX_QUOTE_CHARS:
        return normalized
    return normalized[:MAX_QUOTE_CHARS].rstrip() + "..."


def _source_anchor(chunk: dict[str, Any]) -> dict[str, Any]:
    anchor = dict(chunk.get("anchor") or {})
    line_start = chunk.get("line_start")
    line_end = chunk.get("line_end")
    if line_start:
        anchor["md_line_start"] = line_start
        anchor["md_line"] = line_start
    if line_end:
        anchor["md_line_end"] = line_end
    return anchor


def _locator(chunk: dict[str, Any]) -> str:
    line_start = chunk.get("line_start")
    line_end = chunk.get("line_end")
    if line_start and line_end:
        return f"document.md:L{line_start}-L{line_end}"
    if line_start:
        return f"document.md:L{line_start}"
    return "document.md"


def _citation(document: dict[str, Any], locator: str, anchor: dict[str, Any]) -> str:
    title = str(document.get("original_filename") or document.get("filename") or document.get("document_id") or "document")
    page = anchor.get("page")
    suffix = f" · p{page}" if page else ""
    return f"{title} · {locator}{suffix}"


def _build_items_for_document(
    *,
    deal_id: str,
    document: dict[str, Any],
    document_path: Path,
    source_path: str,
    start_index: int,
    built_at: str,
) -> list[dict[str, Any]]:
    markdown = document_path.read_text(encoding="utf-8")
    if not markdown.strip():
        return []
    dimension = _dimension_for_document(document)
    role_hints = ROLE_HINTS_BY_DIMENSION.get(dimension, [])
    task_id = str(document.get("parse_task_id") or "")
    items: list[dict[str, Any]] = []
    for segment in _markdown_segments(markdown):
        for chunk in _chunk_segment(segment):
            quote = _quote_text(str(chunk.get("text") or ""))
            if not quote:
                continue
            sequence = start_index + len(items)
            anchor = _source_anchor(chunk)
            locator = _locator(chunk)
            item = {
                "schema_version": EVIDENCE_ITEM_SCHEMA,
                "evidence_id": f"EVID-{deal_id}-{sequence:06d}",
                "deal_id": deal_id,
                "document_id": document.get("document_id"),
                "parse_task_id": task_id,
                "source_id": document.get("document_id"),
                "source_type": "data_room_document",
                "source_path": source_path,
                "source_anchor": anchor,
                "locator": locator,
                "citation": _citation(document, locator, anchor),
                "claim": quote,
                "quote": quote,
                "evidence_type": "verified",
                "dimension": dimension,
                "confidence": 0.6,
                "role_hints": role_hints,
                "parser_page_url": f"/documents?task={task_id}" if task_id else None,
                "source_url": (
                    f"/api/documents/source/{task_id}/block/{anchor.get('block_id')}"
                    if task_id and anchor.get("block_id")
                    else f"/api/documents/source/{task_id}/page/{anchor.get('page')}"
                    if task_id and anchor.get("page")
                    else None
                ),
                "artifact_url": f"/api/documents/artifact/{task_id}/document.md" if task_id else None,
                "created_at": built_at,
            }
            items.append(item)
    return items


def _read_ndjson(path: Path, *, limit: int | None = None) -> tuple[list[dict[str, Any]], int]:
    items: list[dict[str, Any]] = []
    invalid = 0
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return items, invalid
    for line in lines:
        raw = line.strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            invalid += 1
            continue
        if isinstance(payload, dict):
            if limit is None or len(items) < limit:
                items.append(payload)
        else:
            invalid += 1
    return items, invalid


def _normalize_preview_limit(value: int | str | None) -> int:
    try:
        parsed = int(value) if value is not None else ITEMS_PREVIEW_LIMIT
    except (TypeError, ValueError):
        parsed = ITEMS_PREVIEW_LIMIT
    return max(1, min(parsed, ITEMS_PREVIEW_MAX_LIMIT))


def _clean_filter(value: str | None) -> str:
    return str(value or "").strip()


def _item_search_text(item: dict[str, Any]) -> str:
    fields = (
        item.get("claim"),
        item.get("quote"),
        item.get("citation"),
        item.get("locator"),
        item.get("evidence_id"),
        item.get("source_path"),
        item.get("document_id"),
    )
    return " ".join(str(value or "") for value in fields).lower()


def _matches_evidence_filters(
    item: dict[str, Any],
    *,
    q: str = "",
    dimension: str = "",
    document_id: str = "",
    source_url: str = "",
) -> bool:
    if dimension and str(item.get("dimension") or "").lower() != dimension.lower():
        return False
    if document_id and str(item.get("document_id") or "") != document_id:
        return False
    if source_url and source_url.lower() not in str(item.get("source_url") or "").lower():
        return False
    if q and q.lower() not in _item_search_text(item):
        return False
    return True


def _available_filters(items: list[dict[str, Any]], quality: dict[str, Any] | None) -> dict[str, Any]:
    dimensions = sorted({str(item.get("dimension")) for item in items if item.get("dimension")})
    document_ids = sorted({str(item.get("document_id")) for item in items if item.get("document_id")})
    quality_documents = quality.get("documents") if isinstance(quality, dict) else []
    documents: list[dict[str, Any]] = []
    if isinstance(quality_documents, list):
        for document in quality_documents:
            if isinstance(document, dict) and document.get("document_id"):
                documents.append({
                    "document_id": document.get("document_id"),
                    "filename": document.get("filename"),
                    "original_filename": document.get("original_filename"),
                    "document_type": document.get("document_type"),
                    "status": document.get("status"),
                    "items": document.get("items"),
                })
    known = {str(document.get("document_id") or "") for document in documents}
    for document_id in document_ids:
        if document_id not in known:
            documents.append({"document_id": document_id})
    return {
        "dimensions": dimensions,
        "document_ids": document_ids,
        "documents": documents,
        "limits": list(ITEMS_PREVIEW_LIMIT_OPTIONS),
    }


def _write_ndjson(path: Path, items: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in items), encoding="utf-8")


def _policy_gate() -> dict[str, Any]:
    policy = ic_policy.read_ic_workflow_policy()
    gate = policy.get("evidence_gate") if isinstance(policy.get("evidence_gate"), dict) else {}
    return {
        "policy_version": policy.get("version"),
        "required_verified_items": int(gate.get("required_verified_items") or 0),
        "required_dimensions": list(gate.get("required_dimensions") or []),
    }


def _gate(gate_id: str, status: str, message: str, **details: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"id": gate_id, "status": status, "message": message}
    if details:
        payload["details"] = details
    return payload


def _quality_status(*, errors: list[str], warnings: list[str]) -> str:
    if errors:
        return "fail"
    if warnings:
        return "warn"
    return "pass"


def _item_summary(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "evidence_id": item.get("evidence_id"),
        "document_id": item.get("document_id"),
        "dimension": item.get("dimension"),
        "locator": item.get("locator"),
        "quote_preview": str(item.get("quote") or item.get("claim") or "")[:240],
    }


def _sync_manifest_evidence(package_dir: Path, index: dict[str, Any], quality: dict[str, Any]) -> None:
    manifest = deal_store.read_json(package_dir / "manifest.json", {}) or {}
    evidence = manifest.get("evidence") if isinstance(manifest.get("evidence"), dict) else {}
    evidence.update({
        "index_path": "evidence/evidence_index.json",
        "items_path": "evidence/evidence_items.ndjson",
        "quality_path": "evidence/evidence_quality_report.json",
        "last_build": {
            "build_mode": BUILD_MODE,
            "status": quality.get("status"),
            "built_at": quality.get("built_at"),
            "counts": quality.get("counts") or {},
        },
    })
    manifest["evidence"] = evidence
    manifest["updated_at"] = deal_store.utc_now_iso()
    deal_store.write_json(package_dir / "manifest.json", manifest)


def _sync_manifest_ingest_dry_run(package_dir: Path, report: dict[str, Any]) -> None:
    manifest = deal_store.read_json(package_dir / "manifest.json", {}) or {}
    evidence = manifest.get("evidence") if isinstance(manifest.get("evidence"), dict) else {}
    evidence["ingest_dry_run_path"] = "evidence/evidence_ingest_dry_run.json"
    evidence["last_ingest_dry_run"] = {
        "status": report.get("status"),
        "created_at": report.get("created_at"),
        "counts": report.get("counts") or {},
        "postgres_written": False,
        "milvus_written": False,
    }
    manifest["evidence"] = evidence
    manifest["updated_at"] = deal_store.utc_now_iso()
    deal_store.write_json(package_dir / "manifest.json", manifest)


def _as_text(value: Any) -> str:
    return str(value or "").strip()


def _ingest_status(errors: list[str], warnings: list[str]) -> str:
    if errors:
        return "fail"
    if warnings:
        return "warn"
    return "pass"


def _postgres_row_plan(item: dict[str, Any]) -> dict[str, Any]:
    anchor = item.get("source_anchor") if isinstance(item.get("source_anchor"), dict) else {}
    return {
        "schema_version": "siq_deal_evidence_row_v1",
        "deal_id": item.get("deal_id"),
        "document_id": item.get("document_id"),
        "evidence_id": item.get("evidence_id"),
        "artifact_path": item.get("source_path"),
        "source_path": item.get("source_path"),
        "source_type": item.get("source_type"),
        "evidence_type": item.get("evidence_type"),
        "dimension": item.get("dimension"),
        "claim": item.get("claim"),
        "quote": item.get("quote"),
        "citation": item.get("citation"),
        "confidence": item.get("confidence"),
        "locator": item.get("locator"),
        "page": anchor.get("page"),
        "block_id": anchor.get("block_id"),
        "md_line_start": anchor.get("md_line_start"),
        "md_line_end": anchor.get("md_line_end"),
        "source_url": item.get("source_url"),
        "artifact_url": item.get("artifact_url"),
        "created_at": item.get("created_at"),
    }


def _milvus_chunk_plan(item: dict[str, Any]) -> dict[str, Any]:
    role_hints = item.get("role_hints") if isinstance(item.get("role_hints"), list) else []
    text = _as_text(item.get("quote") or item.get("claim"))
    return {
        "schema_version": "siq_deal_chunk_v1",
        "collection": "siq_deal_shared",
        "deal_id": item.get("deal_id"),
        "document_id": item.get("document_id"),
        "evidence_id": item.get("evidence_id"),
        "source_path": item.get("source_path"),
        "source_type": item.get("source_type"),
        "confidence": item.get("confidence"),
        "role_hint": role_hints[0] if role_hints else None,
        "role_hints": role_hints,
        "citation": item.get("citation"),
        "dimension": item.get("dimension"),
        "text": text,
        "text_length": len(text),
        "source_url": item.get("source_url"),
        "artifact_url": item.get("artifact_url"),
    }


def build_deal_evidence_ingest_dry_run(
    deal_id: str,
    *,
    created_by: dict[str, Any] | None = None,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    package_dir = _require_package_dir(deal_id, wiki_root=wiki_root)
    normalized_deal_id = deal_store.validate_deal_id(deal_id)
    created_at = deal_store.utc_now_iso()
    items, invalid_lines = _read_ndjson(package_dir / "evidence" / "evidence_items.ndjson")
    quality = deal_store.read_json(package_dir / "evidence" / "evidence_quality_report.json", {}) or {}

    errors: list[str] = []
    warnings: list[str] = []
    if invalid_lines:
        errors.append(f"evidence_items.ndjson has {invalid_lines} invalid lines")
    if not items:
        errors.append("No evidence items found. Build evidence package before ingest dry-run.")
    quality_status = quality.get("status") if isinstance(quality, dict) else None
    if quality_status == "fail":
        warnings.append("Evidence quality status is fail; dry-run generated for inspection only.")
    elif quality_status == "warn":
        warnings.append("Evidence quality status is warn; review quality report before real ingest.")

    required_fields = ("evidence_id", "deal_id", "document_id", "source_path", "quote", "evidence_type", "dimension")
    postgres_rows: list[dict[str, Any]] = []
    milvus_chunks: list[dict[str, Any]] = []
    item_errors: list[dict[str, Any]] = []
    seen_evidence_ids: set[str] = set()
    duplicate_evidence_ids: set[str] = set()
    for item in items:
        evidence_id = _as_text(item.get("evidence_id"))
        missing = [field for field in required_fields if not _as_text(item.get(field))]
        if evidence_id in seen_evidence_ids:
            duplicate_evidence_ids.add(evidence_id)
        if evidence_id:
            seen_evidence_ids.add(evidence_id)
        if missing:
            item_errors.append({"evidence_id": evidence_id or None, "missing": missing})
            continue
        postgres_rows.append(_postgres_row_plan(item))
        milvus_chunks.append(_milvus_chunk_plan(item))

    if duplicate_evidence_ids:
        errors.append(f"Duplicate evidence_id values: {', '.join(sorted(duplicate_evidence_ids))}")
    if item_errors:
        errors.append(f"{len(item_errors)} evidence items are missing required ingest fields")

    counts = {
        "items_total": len(items),
        "items_valid": len(postgres_rows),
        "items_invalid": len(item_errors),
        "postgres_rows_planned": len(postgres_rows),
        "milvus_chunks_planned": len(milvus_chunks),
        "invalid_ndjson_lines": invalid_lines,
    }
    report = {
        "schema_version": EVIDENCE_INGEST_DRY_RUN_SCHEMA,
        "deal_id": normalized_deal_id,
        "mode": INGEST_DRY_RUN_MODE,
        "status": _ingest_status(errors, warnings),
        "created_at": created_at,
        "postgres_written": False,
        "milvus_written": False,
        "target_postgres": {
            "schema": "deal_os",
            "tables": ["deal_os.evidence_items"],
            "write_enabled": False,
        },
        "target_milvus": {
            "collections": ["siq_deal_shared"],
            "write_enabled": False,
        },
        "counts": counts,
        "quality_status": quality_status,
        "required_fields": list(required_fields),
        "errors": errors,
        "warnings": warnings,
        "item_errors": item_errors,
        "postgres_rows_preview": postgres_rows[:20],
        "milvus_chunks_preview": milvus_chunks[:20],
        "created_by": created_by,
    }
    evidence_dir = package_dir / "evidence"
    deal_store.write_json(evidence_dir / "evidence_ingest_dry_run.json", report)
    _sync_manifest_ingest_dry_run(package_dir, report)
    deal_store.append_audit_event(
        normalized_deal_id,
        {
            "event_type": "deal_evidence_ingest_dry_run_generated",
            "status": report["status"],
            "counts": counts,
            "created_by": created_by,
        },
        wiki_root=wiki_root,
    )
    return report


def read_deal_evidence_ingest_dry_run(
    deal_id: str,
    *,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    package_dir = _require_package_dir(deal_id, wiki_root=wiki_root)
    report = deal_store.read_json(package_dir / "evidence" / "evidence_ingest_dry_run.json", None)
    return {
        "deal_id": deal_store.validate_deal_id(deal_id),
        "ingest_dry_run": report if isinstance(report, dict) else None,
    }


def build_deal_evidence_package(
    deal_id: str,
    *,
    built_by: dict[str, Any] | None = None,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    package_dir = _require_package_dir(deal_id, wiki_root=wiki_root)
    normalized_deal_id = deal_store.validate_deal_id(deal_id)
    built_at = deal_store.utc_now_iso()
    policy_gate = _policy_gate()
    documents, invalid_metadata_files = _read_document_metadata(package_dir)

    items: list[dict[str, Any]] = []
    document_results: list[dict[str, Any]] = []
    warnings: list[str] = []
    errors: list[str] = []
    if invalid_metadata_files:
        errors.append(f"Invalid document metadata files: {', '.join(invalid_metadata_files)}")

    for document in documents:
        document_id = str(document.get("document_id") or "")
        result = {
            "document_id": document_id,
            "original_filename": document.get("original_filename"),
            "document_type": document.get("document_type"),
            "parse_task_id": document.get("parse_task_id"),
            "parsed_artifact_path": document.get("parsed_artifact_path"),
            "dimension": _dimension_for_document(document),
            "status": "not_bound",
            "items": 0,
        }
        if not document.get("parse_task_id"):
            result["reason"] = "No parser task is bound"
            document_results.append(result)
            warnings.append(f"{document_id}: parser task is not bound")
            continue

        try:
            document_path, source_path, missing_reason = _document_md_candidate(document)
        except ValueError as exc:
            result["status"] = "invalid_parser_binding"
            result["reason"] = str(exc)
            document_results.append(result)
            warnings.append(f"{document_id}: invalid parser binding")
            continue

        result["source_path"] = source_path
        if missing_reason:
            result["status"] = missing_reason
            result["reason"] = missing_reason
            document_results.append(result)
            warnings.append(f"{document_id}: {missing_reason}")
            continue
        if not document_path:
            result["status"] = "missing_document_md"
            result["reason"] = "missing_document_md"
            document_results.append(result)
            warnings.append(f"{document_id}: missing_document_md")
            continue

        document_items = _build_items_for_document(
            deal_id=normalized_deal_id,
            document=document,
            document_path=document_path,
            source_path=source_path,
            start_index=len(items) + 1,
            built_at=built_at,
        )
        if not document_items:
            result["status"] = "empty_document_md"
            result["reason"] = "document.md is empty or contains no indexable text"
            document_results.append(result)
            warnings.append(f"{document_id}: empty_document_md")
            continue

        items.extend(document_items)
        result["status"] = "indexed"
        result["items"] = len(document_items)
        document_results.append(result)

    documents_bound = sum(1 for item in documents if item.get("parse_task_id"))
    documents_indexed = sum(1 for item in document_results if item.get("status") == "indexed")
    verified_items = [item for item in items if item.get("evidence_type") == "verified"]
    dimensions = sorted({str(item.get("dimension")) for item in verified_items if item.get("dimension")})
    missing_dimensions = sorted(set(policy_gate["required_dimensions"]) - set(dimensions))
    required_verified_items = int(policy_gate["required_verified_items"])

    if not documents:
        warnings.append("No data-room documents found")
    if documents_bound == 0:
        warnings.append("No data-room documents are bound to parser tasks")
    if documents_bound > 0 and documents_indexed == 0:
        errors.append("All parser-bound documents failed evidence indexing")
    if len(verified_items) < required_verified_items:
        warnings.append(f"Verified evidence item count {len(verified_items)}/{required_verified_items}")
    if missing_dimensions:
        warnings.append(f"Missing evidence dimensions: {', '.join(missing_dimensions)}")

    evidence_dir = package_dir / "evidence"
    items_path = evidence_dir / "evidence_items.ndjson"
    _write_ndjson(items_path, items)
    _, invalid_ndjson_lines = _read_ndjson(items_path)
    if invalid_ndjson_lines:
        errors.append(f"evidence_items.ndjson has {invalid_ndjson_lines} invalid lines")

    counts = {
        "documents_total": len(documents),
        "documents_bound": documents_bound,
        "documents_indexed": documents_indexed,
        "documents_skipped": len([item for item in document_results if item.get("status") != "indexed"]),
        "items": len(items),
        "verified_items": len(verified_items),
        "invalid_metadata_files": len(invalid_metadata_files),
        "invalid_ndjson_lines": invalid_ndjson_lines,
    }
    gates = [
        _gate(
            "document_bindings",
            "pass" if documents_bound else "warn",
            f"{documents_bound}/{len(documents)} documents have parser task bindings",
        ),
        _gate(
            "parser_artifacts",
            "pass" if documents_bound == documents_indexed else "warn" if documents_indexed else "fail" if documents_bound else "warn",
            f"{documents_indexed}/{documents_bound} bound documents were indexed",
        ),
        _gate(
            "verified_items",
            "pass" if len(verified_items) >= required_verified_items else "warn",
            f"{len(verified_items)}/{required_verified_items} verified evidence items",
        ),
        _gate(
            "dimension_coverage",
            "pass" if not missing_dimensions else "warn",
            "Required dimensions are covered" if not missing_dimensions else "Required dimensions are missing",
            missing_dimensions=missing_dimensions,
            dimensions=dimensions,
        ),
        _gate(
            "ndjson_valid",
            "pass" if invalid_ndjson_lines == 0 else "fail",
            "evidence_items.ndjson is valid" if invalid_ndjson_lines == 0 else "evidence_items.ndjson contains invalid lines",
        ),
    ]

    quality = {
        "schema_version": EVIDENCE_QUALITY_SCHEMA,
        "deal_id": normalized_deal_id,
        "status": _quality_status(errors=errors, warnings=warnings),
        "build_mode": BUILD_MODE,
        "llm_used": False,
        "agent_used": False,
        "milvus_written": False,
        "built_at": built_at,
        "policy_version": policy_gate.get("policy_version"),
        "required_verified_items": required_verified_items,
        "required_dimensions": policy_gate["required_dimensions"],
        "item_count": len(items),
        "verified_count": len(verified_items),
        "dimensions": dimensions,
        "missing_dimensions": missing_dimensions,
        "counts": counts,
        "gates": gates,
        "documents": document_results,
        "warnings": sorted(set(warnings)),
        "errors": errors,
    }
    index = {
        "schema_version": EVIDENCE_INDEX_SCHEMA,
        "deal_id": normalized_deal_id,
        "build_mode": BUILD_MODE,
        "llm_used": False,
        "agent_used": False,
        "milvus_written": False,
        "built_at": built_at,
        "paths": {
            "items": "evidence/evidence_items.ndjson",
            "quality": "evidence/evidence_quality_report.json",
        },
        "counts": counts,
        "documents": document_results,
        "items": [_item_summary(item) for item in items],
    }

    deal_store.write_json(evidence_dir / "evidence_index.json", index)
    deal_store.write_json(evidence_dir / "evidence_quality_report.json", quality)
    _sync_manifest_evidence(package_dir, index, quality)
    deal_store.append_audit_event(
        normalized_deal_id,
        {
            "event_type": "deal_evidence_built",
            "status": quality["status"],
            "counts": counts,
            "built_by": built_by,
        },
        wiki_root=wiki_root,
    )

    return {
        "deal_id": normalized_deal_id,
        "status": quality["status"],
        "counts": counts,
        "evidence_index": index,
        "quality_report": quality,
        "items_preview": items[:ITEMS_PREVIEW_LIMIT],
        "index": index,
        "quality": quality,
        "documents": document_results,
    }


def read_deal_evidence_package(
    deal_id: str,
    *,
    wiki_root: Path | str | None = None,
    preview_limit: int = ITEMS_PREVIEW_LIMIT,
    q: str | None = None,
    dimension: str | None = None,
    document_id: str | None = None,
    source_url: str | None = None,
) -> dict[str, Any]:
    package_dir = _require_package_dir(deal_id, wiki_root=wiki_root)
    normalized_deal_id = deal_store.validate_deal_id(deal_id)
    index = deal_store.read_json(package_dir / "evidence" / "evidence_index.json", None)
    quality = deal_store.read_json(package_dir / "evidence" / "evidence_quality_report.json", None)
    items, invalid_lines = _read_ndjson(package_dir / "evidence" / "evidence_items.ndjson")
    normalized_limit = _normalize_preview_limit(preview_limit)
    applied_filters = {
        "q": _clean_filter(q),
        "dimension": _clean_filter(dimension),
        "document_id": _clean_filter(document_id),
        "source_url": _clean_filter(source_url),
        "limit": normalized_limit,
    }
    filtered_items = [
        item for item in items
        if _matches_evidence_filters(
            item,
            q=applied_filters["q"],
            dimension=applied_filters["dimension"],
            document_id=applied_filters["document_id"],
            source_url=applied_filters["source_url"],
        )
    ]
    if isinstance(quality, dict) and invalid_lines:
        quality = dict(quality)
        quality.setdefault("warnings", [])
        if isinstance(quality["warnings"], list):
            quality["warnings"].append(f"evidence_items.ndjson has {invalid_lines} invalid lines")
    return {
        "deal_id": normalized_deal_id,
        "status": quality.get("status") if isinstance(quality, dict) else None,
        "counts": quality.get("counts") if isinstance(quality, dict) else {},
        "evidence_index": index if isinstance(index, dict) else None,
        "quality_report": quality if isinstance(quality, dict) else None,
        "items_preview": filtered_items[:normalized_limit],
        "matched_count": len(filtered_items),
        "total_item_count": len(items),
        "applied_filters": applied_filters,
        "available_filters": _available_filters(items, quality if isinstance(quality, dict) else None),
        "index": index if isinstance(index, dict) else None,
        "quality": quality if isinstance(quality, dict) else None,
    }


def read_deal_evidence_quality(
    deal_id: str,
    *,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    package_dir = _require_package_dir(deal_id, wiki_root=wiki_root)
    quality = deal_store.read_json(package_dir / "evidence" / "evidence_quality_report.json", None)
    return {
        "deal_id": deal_store.validate_deal_id(deal_id),
        "quality_report": quality if isinstance(quality, dict) else None,
        "quality": quality if isinstance(quality, dict) else None,
    }


def get_deal_evidence_item(
    deal_id: str,
    evidence_id: str,
    *,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    package_dir = _require_package_dir(deal_id, wiki_root=wiki_root)
    normalized = str(evidence_id or "").strip()
    items, _ = _read_ndjson(package_dir / "evidence" / "evidence_items.ndjson")
    for item in items:
        if item.get("evidence_id") == normalized:
            return {"deal_id": deal_store.validate_deal_id(deal_id), "evidence": item}
    raise FileNotFoundError(normalized)
