"""Read-only Deal OS report artifact index and reader."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from services import deal_store


REPORTS_INDEX_SCHEMA = "siq_deal_reports_index_v1"
REPORT_DETAIL_SCHEMA = "siq_deal_report_detail_v1"
ALLOWED_REPORT_DIRS = ("phases", "discussion", "decision", "evidence")
ALLOWED_REPORT_SUFFIXES = (".json", ".md", ".html", ".txt", ".ndjson")
MAX_REPORT_DETAIL_BYTES = 2_000_000
NDJSON_PREVIEW_LIMIT = 100
EXCLUDED_REPORT_PATHS = {"phases/audit_log.json", "audit/audit_log.json"}

EXPECTED_REPORTS: tuple[dict[str, str], ...] = (
    {"path": "phases/workflow_state.json", "title": "Workflow state", "category": "workflow"},
    {"path": "phases/r1_reports.json", "title": "R1 expert reports", "category": "workflow"},
    {"path": "phases/startup_receipts.json", "title": "Startup retrieval receipts", "category": "retrieval"},
    {"path": "phases/r1_5_disputes.json", "title": "R1.5 disputes", "category": "workflow"},
    {"path": "phases/r2_reports.json", "title": "R2 revision reports", "category": "workflow"},
    {"path": "phases/r3_reports.json", "title": "R3 red-blue reports", "category": "workflow"},
    {"path": "phases/r4_decision.json", "title": "R4 decision payload", "category": "decision"},
    {"path": "decision/IC_DECISION_REPORT.md", "title": "IC decision report", "category": "decision"},
    {"path": "decision/IC_DECISION_REPORT.html", "title": "IC decision report HTML", "category": "decision"},
    {"path": "evidence/evidence_index.json", "title": "Evidence index", "category": "evidence"},
    {"path": "evidence/evidence_quality_report.json", "title": "Evidence quality report", "category": "evidence"},
    {"path": "evidence/evidence_ingest_dry_run.json", "title": "Evidence ingest dry-run", "category": "evidence"},
)


def _require_package_dir(deal_id: str, *, wiki_root: Path | str | None = None) -> Path:
    package_dir = deal_store.safe_deal_dir(deal_id, wiki_root=wiki_root)
    if not (package_dir / "manifest.json").is_file():
        raise FileNotFoundError(deal_id)
    return package_dir


def _normalize_report_path(report_path: str) -> str:
    normalized = str(report_path or "").strip().replace("\\", "/").strip("/")
    if not normalized:
        raise ValueError("report_path is required")
    path = Path(normalized)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("report_path must stay inside the deal package")
    if path.as_posix() in EXCLUDED_REPORT_PATHS:
        raise ValueError("audit logs must be read through the audit endpoint")
    if path.parts[0] not in ALLOWED_REPORT_DIRS:
        raise ValueError("report_path must be under phases, discussion, decision, or evidence")
    if path.suffix.lower() not in ALLOWED_REPORT_SUFFIXES:
        raise ValueError("unsupported report file type")
    return path.as_posix()


def _safe_report_file(package_dir: Path, report_path: str) -> Path:
    normalized = _normalize_report_path(report_path)
    candidate = (package_dir / normalized).resolve()
    try:
        candidate.relative_to(package_dir.resolve())
    except ValueError as exc:
        raise ValueError("report_path escapes deal package") from exc
    if not candidate.is_file():
        raise FileNotFoundError(report_path)
    return candidate


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _modified_at(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
    except OSError:
        return None


def _format_for_path(path: str) -> str:
    suffix = Path(path).suffix.lower().lstrip(".")
    return "markdown" if suffix == "md" else suffix or "unknown"


def _category_for_path(path: str) -> str:
    for item in EXPECTED_REPORTS:
        if item["path"] == path:
            return item["category"]
    first = Path(path).parts[0]
    if first == "phases":
        return "workflow"
    return first


def _title_for_path(path: str) -> str:
    for item in EXPECTED_REPORTS:
        if item["path"] == path:
            return item["title"]
    stem = Path(path).stem.replace("_", " ").replace("-", " ").strip()
    return stem[:1].upper() + stem[1:] if stem else path


def _metadata(package_dir: Path, path: str, *, status: str = "available") -> dict[str, Any]:
    payload: dict[str, Any] = {
        "path": path,
        "title": _title_for_path(path),
        "category": _category_for_path(path),
        "format": _format_for_path(path),
        "status": status,
    }
    file_path = package_dir / path
    if status == "available" and file_path.is_file():
        stat = file_path.stat()
        payload.update({
            "size_bytes": stat.st_size,
            "sha256": _sha256(file_path),
            "updated_at": _modified_at(file_path),
        })
    return payload


def _iter_report_files(package_dir: Path) -> list[str]:
    paths: list[str] = []
    root = package_dir.resolve()
    for dirname in ALLOWED_REPORT_DIRS:
        directory = package_dir / dirname
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*")):
            if path.name.startswith(".") or path.suffix.lower() not in ALLOWED_REPORT_SUFFIXES:
                continue
            if not path.is_file():
                continue
            try:
                resolved = path.resolve()
                resolved.relative_to(root)
                relative = resolved.relative_to(root).as_posix()
            except ValueError:
                continue
            if relative in EXCLUDED_REPORT_PATHS:
                continue
            paths.append(relative)
    return sorted(dict.fromkeys(paths))


def _parse_json_report(path: Path) -> tuple[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    redacted = deal_store.redact_public_payload(payload)
    return json.dumps(redacted, ensure_ascii=False, indent=2) + "\n", redacted


def _parse_ndjson_report(path: Path) -> tuple[str, list[Any], int]:
    rows: list[Any] = []
    invalid_lines = 0
    redacted_lines: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            invalid_lines += 1
            continue
        redacted = deal_store.redact_public_payload(parsed)
        if len(rows) < NDJSON_PREVIEW_LIMIT:
            rows.append(redacted)
        redacted_lines.append(json.dumps(redacted, ensure_ascii=False))
    return "\n".join(redacted_lines) + ("\n" if redacted_lines else ""), rows, invalid_lines


def list_deal_reports(deal_id: str, *, wiki_root: Path | str | None = None) -> dict[str, Any]:
    package_dir = _require_package_dir(deal_id, wiki_root=wiki_root)
    available_paths = _iter_report_files(package_dir)
    reports = [_metadata(package_dir, path) for path in available_paths]
    expected_paths = {item["path"] for item in EXPECTED_REPORTS}
    missing_expected = [
        _metadata(package_dir, item["path"], status="missing")
        for item in EXPECTED_REPORTS
        if item["path"] not in available_paths
    ]
    categories = sorted({str(item["category"]) for item in reports + missing_expected if item.get("category")})
    return {
        "schema_version": REPORTS_INDEX_SCHEMA,
        "deal_id": deal_id,
        "generated_at": deal_store.utc_now_iso(),
        "counts": {
            "reports": len(reports),
            "expected": len(EXPECTED_REPORTS),
            "expected_available": len(expected_paths.intersection(available_paths)),
            "missing_expected": len(missing_expected),
        },
        "available_categories": categories,
        "reports": reports,
        "missing_expected": missing_expected,
    }


def read_deal_report(
    deal_id: str,
    report_path: str,
    *,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    package_dir = _require_package_dir(deal_id, wiki_root=wiki_root)
    path = _safe_report_file(package_dir, report_path)
    stat = path.stat()
    if stat.st_size > MAX_REPORT_DETAIL_BYTES:
        raise ValueError("report file is too large to read through this endpoint")

    normalized = path.resolve().relative_to(package_dir.resolve()).as_posix()
    suffix = path.suffix.lower()
    parsed_json: Any = None
    rows_preview: list[Any] | None = None
    invalid_lines: int | None = None
    parse_error: str | None = None
    if suffix == ".json":
        try:
            content, parsed_json = _parse_json_report(path)
        except json.JSONDecodeError as exc:
            content = path.read_text(encoding="utf-8", errors="replace")
            parse_error = f"Invalid JSON: {exc.msg}"
    elif suffix == ".ndjson":
        content, rows_preview, invalid_lines = _parse_ndjson_report(path)
    else:
        content = path.read_text(encoding="utf-8", errors="replace")

    payload: dict[str, Any] = {
        "schema_version": REPORT_DETAIL_SCHEMA,
        "deal_id": deal_id,
        "report": _metadata(package_dir, normalized),
        "content": content,
    }
    if parsed_json is not None:
        payload["json"] = parsed_json
    if parse_error:
        payload["parse_error"] = parse_error
    if rows_preview is not None:
        payload["rows_preview"] = rows_preview
        payload["invalid_lines"] = invalid_lines
    return payload
