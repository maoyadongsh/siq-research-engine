"""Filesystem-backed Deal OS package helpers.

P0 keeps deal packages in data/wiki/deals and treats PostgreSQL as a later
indexing layer. These helpers centralize path safety and lightweight JSON
contracts so routers and importers do not hand-roll filesystem access.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import stat
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator
from weakref import WeakValueDictionary

from services.path_config import WIKI_ROOT

DEAL_ID_RE = re.compile(r"^[A-Z0-9][A-Z0-9_-]{2,96}$")
DEAL_MANIFEST_SCHEMA = "siq_deal_manifest_v1"
DEAL_PROJECT_SCHEMA = "siq_deal_project_v1"
DEAL_WORKFLOW_SCHEMA = "siq_deal_workflow_state_v1"


class JsonRevisionConflictError(RuntimeError):
    """Raised when an optimistic JSON update is based on stale state."""


_path_locks: WeakValueDictionary[str, threading.RLock] = WeakValueDictionary()
_path_locks_guard = threading.Lock()


def _thread_lock_for(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _path_locks_guard:
        return _path_locks.setdefault(key, threading.RLock())


@contextmanager
def _locked_path(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    thread_lock = _thread_lock_for(path)
    with thread_lock, lock_path.open("a+b") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


def _atomic_write_text(path: Path, text: str) -> None:
    temp_path: Path | None = None
    try:
        target_mode = stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        target_mode = 0o644
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, target_mode)
        os.replace(temp_path, path)
        temp_path = None
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


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
    serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    with _locked_path(path):
        _atomic_write_text(path, serialized)


def update_json(
    path: Path,
    updater: Callable[[Any], Any],
    *,
    default: Any = None,
    expected_updated_at: str | None = None,
) -> Any:
    """Atomically read, update, and replace a JSON document.

    ``expected_updated_at`` is opt-in so existing callers remain compatible.
    Callers that read before doing expensive work can use it as an optimistic
    precondition and receive an explicit conflict instead of overwriting a
    newer document.
    """
    with _locked_path(path):
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            current = default
        if expected_updated_at is not None:
            current_updated_at = current.get("updated_at") if isinstance(current, dict) else None
            if current_updated_at != expected_updated_at:
                raise JsonRevisionConflictError(
                    f"stale JSON update for {path}: expected updated_at={expected_updated_at!r}, "
                    f"found {current_updated_at!r}"
                )
        updated = updater(current)
        serialized = json.dumps(updated, ensure_ascii=False, indent=2) + "\n"
        _atomic_write_text(path, serialized)
        return updated


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
        "sources",
        "evidence",
        "phases",
        "discussion",
        "decision",
        "audit",
        "wiki",
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


def _role_value(user: Any) -> str:
    role = getattr(user, "role", "")
    return str(role.value if hasattr(role, "value") else role)


def is_deal_admin_user(user: Any) -> bool:
    return _role_value(user) in {"admin", "super_admin"}


def _user_identity_values(user: Any) -> set[str]:
    values: set[str] = set()
    for attr in ("id", "username", "email"):
        raw = getattr(user, attr, None)
        if raw not in (None, ""):
            values.add(str(raw).strip().lower())
    return {value for value in values if value}


def _principal_matches_user(value: Any, user_values: set[str]) -> bool:
    if not user_values:
        return False
    if isinstance(value, dict):
        keys = (
            "id",
            "user_id",
            "principal_id",
            "username",
            "email",
            "name",
        )
        return any(str(value.get(key) or "").strip().lower() in user_values for key in keys)
    if isinstance(value, (list, tuple, set)):
        return any(_principal_matches_user(item, user_values) for item in value)
    if value in (None, ""):
        return False
    return str(value).strip().lower() in user_values


def _role_from_member_payload(value: Any, *, default: str = "member") -> str:
    if isinstance(value, dict):
        return str(value.get("role") or value.get("access") or default).strip().lower() or default
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"owner", "member", "editor", "viewer", "reviewer", "maintainer"}:
            return lowered
    return default


def _iter_member_payloads(project_meta: dict[str, Any]) -> list[Any]:
    payloads: list[Any] = []
    for key in ("members", "team", "collaborators", "shared_with", "access", "access_bindings"):
        value = project_meta.get(key)
        if isinstance(value, dict):
            payloads.extend({"principal": principal, "role": role} for principal, role in value.items())
        elif isinstance(value, list):
            payloads.extend(value)
    return payloads


def _deal_project_meta_for_access(deal_id: str, *, wiki_root: Path | str | None = None) -> dict[str, Any]:
    package_dir = safe_deal_dir(deal_id, wiki_root=wiki_root)
    if not (package_dir / "manifest.json").is_file():
        raise FileNotFoundError(deal_id)
    return read_json(package_dir / "project_meta.json", {}) or {}


def deal_access_role(
    deal_id: str,
    user: Any,
    *,
    wiki_root: Path | str | None = None,
) -> str | None:
    if is_deal_admin_user(user):
        return "admin"
    project_meta = _deal_project_meta_for_access(deal_id, wiki_root=wiki_root)
    user_values = _user_identity_values(user)
    if _principal_matches_user(project_meta.get("created_by"), user_values):
        return "owner"
    if _principal_matches_user(project_meta.get("owner"), user_values):
        return "owner"
    if _principal_matches_user(project_meta.get("owner_id"), user_values):
        return "owner"
    if _principal_matches_user(project_meta.get("owner_user_id"), user_values):
        return "owner"

    for item in _iter_member_payloads(project_meta):
        if isinstance(item, dict) and "principal" in item:
            if _principal_matches_user(item.get("principal"), user_values):
                return _role_from_member_payload(item.get("role"), default="member")
            continue
        if _principal_matches_user(item, user_values):
            return _role_from_member_payload(item, default="member")
    return None


def _is_private_deal(project_meta: dict[str, Any]) -> bool:
    level = str(project_meta.get("confidentiality_level") or "private").strip().lower()
    return level not in {"public", "internal", "shared", "team"}


def user_can_access_deal(
    deal_id: str,
    user: Any,
    *,
    action: str = "view",
    wiki_root: Path | str | None = None,
) -> bool:
    project_meta = _deal_project_meta_for_access(deal_id, wiki_root=wiki_root)
    normalized_action = str(action or "view").strip().lower()
    if is_deal_admin_user(user):
        return True
    if normalized_action in {"view", "read", "list"} and not _is_private_deal(project_meta):
        return True
    role = deal_access_role(deal_id, user, wiki_root=wiki_root)
    if normalized_action in {"view", "read", "list"}:
        return role in {"admin", "owner", "member", "editor", "viewer", "reviewer", "maintainer"}
    return role in {"admin", "owner", "member", "editor", "maintainer"}


def filter_deals_for_user(
    deals: list[dict[str, Any]],
    user: Any,
    *,
    action: str = "list",
    wiki_root: Path | str | None = None,
) -> list[dict[str, Any]]:
    visible: list[dict[str, Any]] = []
    for item in deals:
        deal_id = str(item.get("deal_id") or "").strip()
        if not deal_id:
            continue
        try:
            if user_can_access_deal(deal_id, user, action=action, wiki_root=wiki_root):
                visible.append(item)
        except (FileNotFoundError, ValueError):
            continue
    return visible


def append_access_decision(
    deal_id: str,
    *,
    action: str,
    decision: str,
    actor: dict[str, Any],
    reason: str | None = None,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    package_dir = safe_deal_dir(deal_id, wiki_root=wiki_root)
    if not (package_dir / "manifest.json").is_file():
        raise FileNotFoundError(deal_id)
    event = {
        "schema_version": "siq_deal_access_decision_v1",
        "created_at": utc_now_iso(),
        "deal_id": deal_id,
        "action": action,
        "decision": decision,
        "actor": _redact_user_payload(actor),
    }
    if reason:
        event["reason"] = reason
    path = package_dir / "audit" / "access_decisions.ndjson"
    encoded = (json.dumps(event, ensure_ascii=False, default=str) + "\n").encode("utf-8")
    with _locked_path(path):
        descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "ab") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    return event


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
        if key in {
            "created_by",
            "updated_by",
            "confirmed_by",
            "deleted_by",
            "bound_by",
            "parse_bound_by",
            "built_by",
            "generated_by",
            "ruled_by",
        }:
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

        def append_event(current: Any, *, event_path: Path = path) -> dict[str, Any]:
            audit = current if isinstance(current, dict) else {"events": []}
            events = audit.setdefault("events", [])
            if not isinstance(events, list):
                raise ValueError(f"deal audit events must be a list: {event_path}")
            events.append(payload)
            return audit

        update_json(path, append_event, default={"events": []})
    return payload
