"""Filesystem-backed Deal OS package helpers.

P0 keeps deal packages in data/wiki/deals and treats PostgreSQL as a later
indexing layer. These helpers centralize path safety and lightweight JSON
contracts so routers and importers do not hand-roll filesystem access.
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from services.path_config import WIKI_ROOT


DEAL_ID_RE = re.compile(r"^[A-Z0-9][A-Z0-9_-]{2,96}$")
DEAL_MANIFEST_SCHEMA = "siq_deal_manifest_v1"
DEAL_PROJECT_SCHEMA = "siq_deal_project_v1"
DEAL_WORKFLOW_SCHEMA = "siq_deal_workflow_state_v1"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def deals_root(*, wiki_root: Path | str | None = None) -> Path:
    root = Path(wiki_root) if wiki_root is not None else WIKI_ROOT
    return (root / "deals").resolve()


def validate_deal_id(deal_id: str) -> str:
    normalized = str(deal_id or "").strip()
    if not DEAL_ID_RE.fullmatch(normalized):
        raise ValueError("deal_id must be 3-97 chars of A-Z, 0-9, underscore, or dash")
    return normalized


def safe_deal_dir(deal_id: str, *, wiki_root: Path | str | None = None) -> Path:
    root = deals_root(wiki_root=wiki_root)
    normalized = validate_deal_id(deal_id)
    target = (root / normalized).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("deal path escapes deals root") from exc
    return target


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except json.JSONDecodeError:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def relative_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def ensure_deal_package_dirs(package_dir: Path) -> None:
    for relative in (
        "data_room/raw",
        "data_room/metadata",
        "parsed_documents",
        "evidence",
        "phases",
        "discussion",
        "decision",
        "audit",
    ):
        (package_dir / relative).mkdir(parents=True, exist_ok=True)


def default_workflow_state(
    *,
    deal_id: str,
    company_name: str,
    industry: str = "",
    stage: str = "",
    legacy_project_id: str | None = None,
) -> dict[str, Any]:
    now = utc_now_iso()
    return {
        "schema_version": DEAL_WORKFLOW_SCHEMA,
        "deal_id": deal_id,
        "legacy_project_id": legacy_project_id,
        "company_name": company_name,
        "industry": industry,
        "stage": stage,
        "status": "draft",
        "current_phase": "R0",
        "policy_version": "2026-04-13-siq-port",
        "phases": {
            "R0": {"status": "pending"},
            "R1": {"status": "pending"},
            "R1.5": {"status": "pending"},
            "R2": {"status": "pending"},
            "R3": {"status": "pending"},
            "R4": {"status": "pending"},
        },
        "created_at": now,
        "updated_at": now,
    }


def build_project_meta(
    *,
    deal_id: str,
    company_name: str,
    industry: str = "",
    stage: str = "",
    deal_type: str = "",
    source: str = "manual",
    created_by: dict[str, Any] | None = None,
    legacy_project_id: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = utc_now_iso()
    payload: dict[str, Any] = {
        "schema_version": DEAL_PROJECT_SCHEMA,
        "deal_id": deal_id,
        "legacy_project_id": legacy_project_id,
        "company_name": company_name,
        "industry": industry,
        "stage": stage,
        "deal_type": deal_type,
        "source": source,
        "status": "draft",
        "created_by": created_by,
        "created_at": now,
        "updated_at": now,
        "final_decision": None,
        "final_score": None,
        "confidentiality_level": "private",
    }
    if extra:
        payload.update({key: value for key, value in extra.items() if value is not None})
    return payload


def build_manifest(
    *,
    deal_id: str,
    company_name: str,
    legacy_project_id: str | None = None,
    documents: list[dict[str, Any]] | None = None,
    hashes: dict[str, str] | None = None,
) -> dict[str, Any]:
    now = utc_now_iso()
    return {
        "schema_version": DEAL_MANIFEST_SCHEMA,
        "deal_id": deal_id,
        "legacy_project_id": legacy_project_id,
        "company_name": company_name,
        "created_at": now,
        "updated_at": now,
        "documents": documents or [],
        "evidence": {
            "index_path": "evidence/evidence_index.json",
            "items_path": "evidence/evidence_items.ndjson",
            "quality_path": "evidence/evidence_quality_report.json",
        },
        "workflow": {
            "state_path": "phases/workflow_state.json",
            "policy_version": "2026-04-13-siq-port",
        },
        "decision": {
            "markdown_path": "decision/IC_DECISION_REPORT.md",
            "html_path": "decision/IC_DECISION_REPORT.html",
        },
        "hashes": hashes or {},
    }


def create_deal_package(
    *,
    deal_id: str,
    company_name: str,
    industry: str = "",
    stage: str = "",
    deal_type: str = "",
    source: str = "manual",
    created_by: dict[str, Any] | None = None,
    legacy_project_id: str | None = None,
    wiki_root: Path | str | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    package_dir = safe_deal_dir(deal_id, wiki_root=wiki_root)
    if package_dir.exists():
        if not overwrite:
            raise FileExistsError(f"deal package already exists: {deal_id}")
        shutil.rmtree(package_dir)
    ensure_deal_package_dirs(package_dir)

    project_meta = build_project_meta(
        deal_id=deal_id,
        legacy_project_id=legacy_project_id,
        company_name=company_name,
        industry=industry,
        stage=stage,
        deal_type=deal_type,
        source=source,
        created_by=created_by,
    )
    manifest = build_manifest(
        deal_id=deal_id,
        legacy_project_id=legacy_project_id,
        company_name=company_name,
    )
    workflow = default_workflow_state(
        deal_id=deal_id,
        legacy_project_id=legacy_project_id,
        company_name=company_name,
        industry=industry,
        stage=stage,
    )
    write_json(package_dir / "project_meta.json", project_meta)
    write_json(package_dir / "manifest.json", manifest)
    write_json(package_dir / "phases" / "workflow_state.json", workflow)
    write_json(package_dir / "phases" / "audit_log.json", {"events": []})
    write_json(package_dir / "audit" / "audit_log.json", {"events": []})
    return read_deal_summary(package_dir)


def read_deal_summary(package_dir: Path) -> dict[str, Any]:
    project_meta = read_json(package_dir / "project_meta.json", {}) or {}
    workflow = read_json(package_dir / "phases" / "workflow_state.json", {}) or {}
    manifest = read_json(package_dir / "manifest.json", {}) or {}
    return {
        "deal_id": project_meta.get("deal_id") or manifest.get("deal_id") or package_dir.name,
        "legacy_project_id": project_meta.get("legacy_project_id") or manifest.get("legacy_project_id"),
        "company_name": project_meta.get("company_name") or manifest.get("company_name") or package_dir.name,
        "industry": project_meta.get("industry") or workflow.get("industry") or "",
        "stage": project_meta.get("stage") or workflow.get("stage") or "",
        "status": project_meta.get("status") or workflow.get("status") or "unknown",
        "current_phase": workflow.get("current_phase"),
        "final_decision": project_meta.get("final_decision") or workflow.get("final_decision"),
        "final_score": project_meta.get("final_score") or workflow.get("final_score"),
        "updated_at": project_meta.get("updated_at") or workflow.get("updated_at") or manifest.get("updated_at"),
        "package_path": f"deals/{package_dir.name}",
    }


def _redact_user_payload(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    return {
        key: item
        for key, item in value.items()
        if key in {"id", "username"} and item not in (None, "")
    }


def redact_public_payload(value: Any) -> Any:
    """Redact local paths and user PII from API-facing deal payloads."""
    if isinstance(value, list):
        return [redact_public_payload(item) for item in value]
    if not isinstance(value, dict):
        return value

    redacted: dict[str, Any] = {}
    for key, item in value.items():
        if key in {"source_root", "absolute_path"}:
            continue
        if key in {"created_by", "updated_by", "confirmed_by"}:
            redacted[key] = _redact_user_payload(item)
            continue
        if key == "package_path" and isinstance(item, str):
            redacted[key] = item if not item.startswith("/") else f"deals/{Path(item).name}"
            continue
        redacted[key] = redact_public_payload(item)
    return redacted


def list_deals(*, wiki_root: Path | str | None = None) -> list[dict[str, Any]]:
    root = deals_root(wiki_root=wiki_root)
    if not root.exists():
        return []
    deals = [
        read_deal_summary(path)
        for path in root.iterdir()
        if path.is_dir() and (path / "manifest.json").is_file()
    ]
    deals.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    return deals


def read_deal_detail(deal_id: str, *, wiki_root: Path | str | None = None) -> dict[str, Any]:
    package_dir = safe_deal_dir(deal_id, wiki_root=wiki_root)
    if not (package_dir / "manifest.json").is_file():
        raise FileNotFoundError(deal_id)
    return redact_public_payload({
        "summary": read_deal_summary(package_dir),
        "project_meta": read_json(package_dir / "project_meta.json", {}) or {},
        "manifest": read_json(package_dir / "manifest.json", {}) or {},
        "workflow": read_json(package_dir / "phases" / "workflow_state.json", {}) or {},
    })


def append_audit_event(
    deal_id: str,
    event: dict[str, Any],
    *,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    package_dir = safe_deal_dir(deal_id, wiki_root=wiki_root)
    payload = dict(event)
    payload.setdefault("created_at", utc_now_iso())
    for relative in ("phases/audit_log.json", "audit/audit_log.json"):
        path = package_dir / relative
        audit = read_json(path, {"events": []}) or {"events": []}
        events = audit.setdefault("events", [])
        if isinstance(events, list):
            events.append(payload)
        write_json(path, audit)
    return payload
