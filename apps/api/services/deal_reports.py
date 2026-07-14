"""Read-only Deal OS report artifact index and reader."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from services import deal_store, ic_policy

REPORTS_INDEX_SCHEMA = "siq_deal_reports_index_v1"
REPORT_DETAIL_SCHEMA = "siq_deal_report_detail_v1"
R1_REPORTS_SUMMARY_SCHEMA = "siq_deal_r1_reports_summary_v1"
R2_REPORTS_SUMMARY_SCHEMA = "siq_deal_r2_reports_summary_v1"
R3_REVIEW_SUMMARY_SCHEMA = "siq_deal_r3_review_summary_v1"
R4_DECISION_SUMMARY_SCHEMA = "siq_deal_r4_decision_summary_v1"
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
R1_REPORT_REQUIRED_FIELDS = (
    "agent_id",
    "round_name",
    "score",
    "recommendation",
    "verified",
    "assumed",
    "open_questions",
    "startup_receipt_id",
)
R1_REPORT_ADVISORY_FIELDS = (
    "summary",
    "key_points",
    "risk_flags",
    "evidence_stats",
    "artifact_path",
    "created_at",
)
R1_CONTRACT_FIELD_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("agent_id", ("agent_id", "profile_id")),
    ("deal_id", ("deal_id", "project_id")),
    ("round_name", ("round_name", "phase")),
    ("score", ("score",)),
    ("recommendation", ("recommendation",)),
    ("verified", ("verified",)),
    ("assumed", ("assumed",)),
    ("open_questions", ("open_questions", "questions")),
    ("risk_flags", ("risk_flags", "risks")),
    ("startup_receipt_id", ("startup_receipt_id", "receipt_id")),
    ("artifact_path", ("artifact_path", "markdown_path")),
)
R1_MARKDOWN_SECTION_MARKERS = (
    "## 检索结果摘要",
    "### 共享底稿证据",
    "### 私有知识库证据",
    "### 信息缺口清单",
    "### 检索后观点",
)
R1_REPORT_ARTIFACTS = {
    "siq_ic_strategist": "discussion/01_R1_strategist_report.md",
    "siq_ic_sector_expert": "discussion/01_R1_sector_expert_report.md",
    "siq_ic_finance_auditor": "discussion/01_R1_finance_auditor_report.md",
    "siq_ic_legal_scanner": "discussion/01_R1_legal_scanner_report.md",
    "siq_ic_risk_controller": "discussion/01_R1_risk_controller_report.md",
    "siq_ic_chairman": "discussion/01_R1_chairman_report.md",
}
R2_REPORT_CONTRACT_FIELD_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("agent_id", ("agent_id", "profile_id")),
    ("round_name", ("round_name", "phase")),
    ("r2_score", ("r2_score", "score")),
    ("recommendation", ("recommendation",)),
    ("summary", ("summary",)),
)
R2_REPORT_ADVISORY_FIELDS = (
    "confidence",
    "r1_score",
    "score_change",
    "revisions",
    "verified",
    "assumed",
    "open_questions",
    "key_points",
    "artifact_path",
    "created_at",
)
R2_REPORT_ARTIFACT_PATH = "discussion/03_R2_\u89c2\u70b9\u5b8c\u5584\u6c47\u603b.md"
R3_REVIEW_ARTIFACT_PATH = "discussion/04_R3_\u7ea2\u84dd\u5bf9\u6297.md"
R4_DECISION_REQUIRED_FIELDS = (
    "weighted_agent_score",
    "chairman_dimension_score",
    "chairman_qualitative_decision",
)
R4_DECISION_ADVISORY_FIELDS = (
    "schema_version",
    "deal_id",
    "decision",
    "final_score",
    "conditions",
    "monitoring_metrics",
    "human_confirmation",
    "artifact_paths",
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


def _canonical_keyed_payload(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    payload: dict[str, dict[str, Any]] = {}
    for key, item in value.items():
        if not isinstance(item, dict):
            continue
        profile_id = ic_policy.canonical_ic_profile_id(str(item.get("agent_id") or key))
        normalized = dict(item)
        normalized["agent_id"] = profile_id
        payload[profile_id] = normalized
    return payload


def _canonical_report_list(value: Any) -> dict[str, dict[str, Any]]:
    if isinstance(value, dict):
        return _canonical_keyed_payload(value)
    if not isinstance(value, list):
        return {}
    payload: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            continue
        profile_id = ic_policy.canonical_ic_profile_id(str(item.get("agent_id") or item.get("profile_id") or f"item_{index}"))
        normalized = dict(item)
        normalized["agent_id"] = profile_id
        payload[profile_id] = normalized
    return payload


def _round_reports_payload(raw: Any) -> dict[str, dict[str, Any]]:
    if isinstance(raw, dict) and "reports" in raw:
        return _canonical_report_list(raw.get("reports"))
    return _canonical_report_list(raw)


def _receipt_agents(value: Any) -> dict[str, dict[str, Any]]:
    payload = value if isinstance(value, dict) else {}
    agents = payload.get("agents", payload)
    return _canonical_keyed_payload(agents)


def _missing_fields(payload: dict[str, Any], fields: tuple[str, ...]) -> list[str]:
    return [field for field in fields if field not in payload or payload.get(field) in (None, "")]


def _has_field_value(payload: dict[str, Any], field: str) -> bool:
    return field in payload and payload.get(field) not in (None, "")


def _missing_contract_field_groups(
    payload: dict[str, Any],
    groups: tuple[tuple[str, tuple[str, ...]], ...],
) -> list[str]:
    return [
        field
        for field, aliases in groups
        if not any(_has_field_value(payload, alias) for alias in aliases)
    ]


def _contract_field_groups_payload(groups: tuple[tuple[str, tuple[str, ...]], ...]) -> list[dict[str, Any]]:
    return [{"field": field, "aliases": list(aliases)} for field, aliases in groups]


def _missing_r4_advisory_fields(payload: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for field in R4_DECISION_ADVISORY_FIELDS:
        if field == "artifact_paths":
            if not (_has_field_value(payload, "artifact_paths") or _has_field_value(payload, "artifacts")):
                missing.append(field)
            continue
        if not _has_field_value(payload, field):
            missing.append(field)
    return missing


def _expected_artifact_path(profile_id: str) -> str:
    return R1_REPORT_ARTIFACTS.get(profile_id, f"discussion/01_R1_{profile_id.removeprefix('siq_ic_')}_report.md")


def _report_artifact_path(report: dict[str, Any], profile_id: str) -> str:
    raw = str(report.get("artifact_path") or "").strip().replace("\\", "/").strip("/")
    if raw:
        try:
            normalized = _normalize_report_path(raw)
        except ValueError:
            return _expected_artifact_path(profile_id)
        if normalized.startswith("discussion/"):
            return normalized
    return _expected_artifact_path(profile_id)


def _r2_report_artifact_path(report: dict[str, Any]) -> str:
    raw = str(report.get("artifact_path") or report.get("markdown_path") or "").strip().replace("\\", "/").strip("/")
    if raw:
        try:
            normalized = _normalize_report_path(raw)
        except ValueError:
            return R2_REPORT_ARTIFACT_PATH
        if normalized.startswith("discussion/"):
            return normalized
    return R2_REPORT_ARTIFACT_PATH


def _markdown_section_status(package_dir: Path, artifact_path: str) -> tuple[str, list[str], int]:
    path = package_dir / artifact_path
    if not path.is_file():
        return "missing", list(R1_MARKDOWN_SECTION_MARKERS), 0
    content = path.read_text(encoding="utf-8", errors="replace")
    missing = [marker for marker in R1_MARKDOWN_SECTION_MARKERS if marker not in content]
    return ("pass" if not missing else "advisory"), missing, len(content)


def _recommendation_value(report: dict[str, Any]) -> str | None:
    value = report.get("recommendation")
    return str(value) if value not in (None, "") else None


def _list_count(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


def _score_value(report: dict[str, Any], *fields: str) -> Any:
    for field in fields:
        value = report.get(field)
        if value not in (None, ""):
            return value
    return None


def _text_value(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _r3_mode(raw: Any, reports: dict[str, dict[str, Any]]) -> str:
    payload = raw if isinstance(raw, dict) else {}
    mode = str(payload.get("mode") or payload.get("status") or "").strip().lower()
    if mode in {"skip", "skipped"}:
        return "skip"
    if reports:
        return "normal"
    if payload:
        return "normal"
    return "unknown"


def _r3_skip_reason(raw: Any) -> str | None:
    payload = raw if isinstance(raw, dict) else {}
    return _text_value(payload.get("skip_reason") or payload.get("reason"))


def _r3_report_status(report: dict[str, Any]) -> str:
    return "pass" if report.get("summary") or report.get("recommendation") or report.get("stance") else "warn"


def _file_summary(package_dir: Path, relative_path: str) -> dict[str, Any]:
    normalized = _normalize_report_path(relative_path)
    path = package_dir / normalized
    if not path.is_file():
        return {
            "path": normalized,
            "available": False,
            "format": _format_for_path(normalized),
        }
    stat = path.stat()
    return {
        "path": normalized,
        "available": True,
        "format": _format_for_path(normalized),
        "size_bytes": stat.st_size,
        "sha256": _sha256(path),
        "updated_at": _modified_at(path),
    }


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


def list_r1_agent_reports(
    deal_id: str,
    *,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    package_dir = _require_package_dir(deal_id, wiki_root=wiki_root)
    normalized_deal_id = deal_store.validate_deal_id(deal_id)
    reports = _canonical_keyed_payload(deal_store.read_json(package_dir / "phases" / "r1_reports.json", {}) or {})
    receipts = _receipt_agents(deal_store.read_json(package_dir / "phases" / "startup_receipts.json", {}) or {})
    profiles = {profile["id"]: profile for profile in ic_policy.list_ic_profiles(include_runtime=False)}

    agents: list[dict[str, Any]] = []
    for profile_id in ic_policy.R1_AGENT_SEQUENCE:
        profile = profiles.get(profile_id, {"id": profile_id, "label": profile_id, "role": profile_id})
        report = reports.get(profile_id, {})
        receipt = receipts.get(profile_id, {})
        artifact_path = _report_artifact_path(report, profile_id) if report else _expected_artifact_path(profile_id)
        markdown_status, missing_sections, markdown_chars = _markdown_section_status(package_dir, artifact_path)
        missing_required = _missing_fields(report, R1_REPORT_REQUIRED_FIELDS) if report else list(R1_REPORT_REQUIRED_FIELDS)
        missing_advisory = _missing_fields(report, R1_REPORT_ADVISORY_FIELDS) if report else list(R1_REPORT_ADVISORY_FIELDS)
        missing_contract_fields = (
            _missing_contract_field_groups(report, R1_CONTRACT_FIELD_GROUPS)
            if report
            else [field for field, _aliases in R1_CONTRACT_FIELD_GROUPS]
        )
        receipt_id = report.get("startup_receipt_id") if isinstance(report, dict) else None
        expected_receipt_id = receipt.get("receipt_id") if isinstance(receipt, dict) else None
        linkage_status = "missing"
        if receipt_id and expected_receipt_id and receipt_id == expected_receipt_id:
            linkage_status = "match"
        elif receipt_id and expected_receipt_id:
            linkage_status = "mismatch"
        elif receipt_id:
            linkage_status = "report_only"
        elif expected_receipt_id:
            linkage_status = "receipt_only"
        status = "missing"
        if report:
            status = (
                "pass"
                if not missing_required and linkage_status == "match" and markdown_status == "pass"
                else "warn"
            )
        agents.append({
            "agent_id": profile_id,
            "role": profile.get("role"),
            "label": profile.get("label") or profile_id,
            "r1_sequence_index": profile.get("r1_sequence_index"),
            "status": status,
            "has_report": bool(report),
            "has_startup_receipt": bool(receipt),
            "startup_receipt_id": receipt_id,
            "expected_startup_receipt_id": expected_receipt_id,
            "startup_receipt_linkage": linkage_status,
            "score": report.get("score") if report else None,
            "recommendation": _recommendation_value(report),
            "confidence": report.get("confidence") if report else None,
            "summary": report.get("summary") if report else None,
            "missing_required_fields": missing_required,
            "missing_advisory_fields": missing_advisory,
            "missing_contract_fields": missing_contract_fields,
            "artifact_path": artifact_path,
            "artifact_available": (package_dir / artifact_path).is_file(),
            "markdown_section_status": markdown_status,
            "missing_markdown_sections": missing_sections,
            "markdown_chars": markdown_chars,
        })

    return deal_store.redact_public_payload({
        "schema_version": R1_REPORTS_SUMMARY_SCHEMA,
        "deal_id": normalized_deal_id,
        "generated_at": deal_store.utc_now_iso(),
        "required_fields": list(R1_REPORT_REQUIRED_FIELDS),
        "advisory_fields": list(R1_REPORT_ADVISORY_FIELDS),
        "contract_field_groups": _contract_field_groups_payload(R1_CONTRACT_FIELD_GROUPS),
        "required_markdown_sections": list(R1_MARKDOWN_SECTION_MARKERS),
        "counts": {
            "agents": len(agents),
            "reports": sum(1 for item in agents if item.get("has_report")),
            "receipts": sum(1 for item in agents if item.get("has_startup_receipt")),
            "pass": sum(1 for item in agents if item.get("status") == "pass"),
            "warn": sum(1 for item in agents if item.get("status") == "warn"),
            "missing": sum(1 for item in agents if item.get("status") == "missing"),
            "artifacts_available": sum(1 for item in agents if item.get("artifact_available")),
        },
        "agents": agents,
    })


def list_r2_agent_reports(
    deal_id: str,
    *,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    package_dir = _require_package_dir(deal_id, wiki_root=wiki_root)
    normalized_deal_id = deal_store.validate_deal_id(deal_id)
    raw_reports = deal_store.read_json(package_dir / "phases" / "r2_reports.json", {}) or {}
    reports = _round_reports_payload(raw_reports)
    profiles = {profile["id"]: profile for profile in ic_policy.list_ic_profiles(include_runtime=False)}
    r2_sequence = [profile_id for profile_id in ic_policy.R1_AGENT_SEQUENCE if profile_id != "siq_ic_chairman"]

    agents: list[dict[str, Any]] = []
    for profile_id in r2_sequence:
        profile = profiles.get(profile_id, {"id": profile_id, "label": profile_id, "role": profile_id})
        report = reports.get(profile_id, {})
        artifact_path = _r2_report_artifact_path(report) if report else R2_REPORT_ARTIFACT_PATH
        missing_contract_fields = (
            _missing_contract_field_groups(report, R2_REPORT_CONTRACT_FIELD_GROUPS)
            if report
            else [field for field, _aliases in R2_REPORT_CONTRACT_FIELD_GROUPS]
        )
        missing_advisory = _missing_fields(report, R2_REPORT_ADVISORY_FIELDS) if report else list(R2_REPORT_ADVISORY_FIELDS)
        artifact_available = (package_dir / artifact_path).is_file()
        status = "missing"
        if report:
            status = "pass" if not missing_contract_fields else "warn"
        r1_score = _score_value(report, "r1_score")
        r2_score = _score_value(report, "r2_score", "score")
        agents.append({
            "agent_id": profile_id,
            "role": profile.get("role"),
            "label": profile.get("label") or profile_id,
            "r2_sequence_index": r2_sequence.index(profile_id),
            "status": status,
            "has_report": bool(report),
            "score": r2_score,
            "r1_score": r1_score,
            "r2_score": r2_score,
            "score_change": report.get("score_change") if report else None,
            "recommendation": _recommendation_value(report),
            "confidence": report.get("confidence") if report else None,
            "summary": report.get("summary") if report else None,
            "revision_count": _list_count(report.get("revisions")) if report else 0,
            "verified_count": _list_count(report.get("verified")) if report else 0,
            "assumed_count": _list_count(report.get("assumed")) if report else 0,
            "open_questions_count": _list_count(report.get("open_questions")) if report else 0,
            "key_points_count": _list_count(report.get("key_points")) if report else 0,
            "missing_contract_fields": missing_contract_fields,
            "missing_advisory_fields": missing_advisory,
            "artifact_path": artifact_path,
            "artifact_available": artifact_available,
            "created_at": report.get("created_at") if report else None,
        })

    return deal_store.redact_public_payload({
        "schema_version": R2_REPORTS_SUMMARY_SCHEMA,
        "deal_id": normalized_deal_id,
        "generated_at": deal_store.utc_now_iso(),
        "contract_field_groups": _contract_field_groups_payload(R2_REPORT_CONTRACT_FIELD_GROUPS),
        "advisory_fields": list(R2_REPORT_ADVISORY_FIELDS),
        "artifact_path": R2_REPORT_ARTIFACT_PATH,
        "artifact_available": (package_dir / R2_REPORT_ARTIFACT_PATH).is_file(),
        "counts": {
            "agents": len(agents),
            "reports": sum(1 for item in agents if item.get("has_report")),
            "pass": sum(1 for item in agents if item.get("status") == "pass"),
            "warn": sum(1 for item in agents if item.get("status") == "warn"),
            "missing": sum(1 for item in agents if item.get("status") == "missing"),
            "artifacts_available": sum(1 for item in agents if item.get("artifact_available")),
            "revisions": sum(int(item.get("revision_count") or 0) for item in agents),
        },
        "agents": agents,
    })


def summarize_r3_review(
    deal_id: str,
    *,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    package_dir = _require_package_dir(deal_id, wiki_root=wiki_root)
    normalized_deal_id = deal_store.validate_deal_id(deal_id)
    raw = deal_store.read_json(package_dir / "phases" / "r3_reports.json", {}) or {}
    if not isinstance(raw, (dict, list)):
        raw = {}
    reports = _round_reports_payload(raw)
    profiles = {profile["id"]: profile for profile in ic_policy.list_ic_profiles(include_runtime=False)}
    mode = _r3_mode(raw, reports)
    skip_reason = _r3_skip_reason(raw)
    json_available = (package_dir / "phases" / "r3_reports.json").is_file()
    markdown = _file_summary(package_dir, R3_REVIEW_ARTIFACT_PATH)
    markdown_available = bool(markdown.get("available"))

    report_items: list[dict[str, Any]] = []
    ordered_ids = [
        profile_id
        for profile_id in ic_policy.R1_AGENT_SEQUENCE
        if profile_id in reports
    ] + sorted(profile_id for profile_id in reports if profile_id not in ic_policy.R1_AGENT_SEQUENCE)
    for profile_id in ordered_ids:
        report = reports.get(profile_id, {})
        profile = profiles.get(profile_id, {"id": profile_id, "label": profile_id, "role": profile_id})
        report_items.append({
            "agent_id": profile_id,
            "role": profile.get("role"),
            "label": profile.get("label") or profile_id,
            "status": _r3_report_status(report),
            "stance": _text_value(report.get("stance")),
            "recommendation": _recommendation_value(report),
            "summary": _text_value(report.get("summary") or report.get("rationale") or report.get("challenge")),
            "challenge_count": (
                _list_count(report.get("challenges"))
                or _list_count(report.get("red_flags"))
                or _list_count(report.get("risk_flags"))
            ),
            "evidence_count": _list_count(report.get("evidence_ids")),
            "created_at": report.get("created_at"),
        })

    warnings: list[str] = []
    if not json_available and not markdown_available:
        warnings.append("r3_artifacts_missing")
    if json_available and not raw:
        warnings.append("r3_reports_empty")
    if mode == "skip" and not skip_reason and not markdown_available and not (
        isinstance(raw, dict) and "reports" in raw
    ):
        warnings.append("r3_skip_reason_missing")
    if mode == "normal" and json_available and not report_items:
        warnings.append("r3_reports_missing")

    status = "missing"
    if json_available or markdown_available:
        status = "warn" if warnings else "pass"

    return deal_store.redact_public_payload({
        "schema_version": R3_REVIEW_SUMMARY_SCHEMA,
        "deal_id": normalized_deal_id,
        "generated_at": deal_store.utc_now_iso(),
        "status": status,
        "mode": mode,
        "skipped": mode == "skip",
        "skip_reason": skip_reason,
        "artifacts": {
            "json": _file_summary(package_dir, "phases/r3_reports.json"),
            "markdown": markdown,
        },
        "counts": {
            "reports": len(report_items),
            "pass": sum(1 for item in report_items if item.get("status") == "pass"),
            "warn": sum(1 for item in report_items if item.get("status") == "warn"),
            "artifacts_available": int(json_available) + int(markdown_available),
            "warnings": len(warnings),
            "challenges": sum(int(item.get("challenge_count") or 0) for item in report_items),
        },
        "reports": report_items,
        "warnings": warnings,
    })


def summarize_r4_decision(
    deal_id: str,
    *,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    package_dir = _require_package_dir(deal_id, wiki_root=wiki_root)
    normalized_deal_id = deal_store.validate_deal_id(deal_id)
    decision = deal_store.read_json(package_dir / "phases" / "r4_decision.json", {}) or {}
    if not isinstance(decision, dict):
        decision = {}
    missing_required = _missing_fields(decision, R4_DECISION_REQUIRED_FIELDS)
    missing_advisory = _missing_r4_advisory_fields(decision)
    human_confirmation = decision.get("human_confirmation") if isinstance(decision.get("human_confirmation"), dict) else {}
    confirmation_status = str(human_confirmation.get("status") or "pending")
    if confirmation_status in {"rejected", "overridden"}:
        confirmed = False
    else:
        confirmed = confirmation_status in {"confirmed", "approved"} or bool(human_confirmation.get("confirmed_at"))
    artifacts = decision.get("artifact_paths") if isinstance(decision.get("artifact_paths"), dict) else {}
    if not artifacts and isinstance(decision.get("artifacts"), dict):
        artifacts = decision.get("artifacts") or {}
    markdown_path = str(artifacts.get("markdown") or artifacts.get("markdown_path") or "decision/IC_DECISION_REPORT.md")
    html_path = str(artifacts.get("html") or artifacts.get("html_path") or "decision/IC_DECISION_REPORT.html")
    markdown = _file_summary(package_dir, markdown_path)
    html = _file_summary(package_dir, html_path)
    status = "missing"
    if decision:
        status = "pass" if not missing_required and markdown.get("available") else "warn"
    decision_value = decision.get("decision")
    final_score = decision.get("final_score")
    weighted_agent_score = decision.get("weighted_agent_score")
    chairman_dimension_score = decision.get("chairman_dimension_score")
    chairman_qualitative_decision = decision.get("chairman_qualitative_decision")
    return deal_store.redact_public_payload({
        "schema_version": R4_DECISION_SUMMARY_SCHEMA,
        "deal_id": normalized_deal_id,
        "status": status,
        "required_fields": list(R4_DECISION_REQUIRED_FIELDS),
        "advisory_fields": list(R4_DECISION_ADVISORY_FIELDS),
        "missing_required_fields": missing_required,
        "missing_advisory_fields": missing_advisory,
        "scoring": {
            "weighted_agent_score": weighted_agent_score,
            "chairman_dimension_score": chairman_dimension_score,
            "final_score": final_score,
        },
        "decision": {
            "value": decision_value,
            "qualitative": chairman_qualitative_decision,
        },
        "decision_value": decision_value,
        "final_score": final_score,
        "weighted_agent_score": weighted_agent_score,
        "chairman_dimension_score": chairman_dimension_score,
        "chairman_qualitative_decision": chairman_qualitative_decision,
        "human_confirmation": {
            "status": confirmation_status,
            "confirmed": confirmed,
            "confirmed_by": human_confirmation.get("confirmed_by"),
            "confirmed_at": human_confirmation.get("confirmed_at"),
            "attestation_schema_version": human_confirmation.get("attestation_schema_version"),
            "report_id": human_confirmation.get("report_id"),
            "report_revision": human_confirmation.get("report_revision"),
            "workflow_run_id": human_confirmation.get("workflow_run_id"),
            "evidence_snapshot_hash": human_confirmation.get("evidence_snapshot_hash"),
            "decision_sha256": human_confirmation.get("decision_sha256"),
            "quality_sha256": human_confirmation.get("quality_sha256"),
            "factcheck_sha256": human_confirmation.get("factcheck_sha256"),
            "override_reason": human_confirmation.get("override_reason"),
            "override_decision": human_confirmation.get("override_decision"),
            "override_score": human_confirmation.get("override_score"),
        },
        "artifacts": {
            "markdown": markdown,
            "html": html,
            "raw": artifacts,
        },
        "generated_at": deal_store.utc_now_iso(),
    })


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
