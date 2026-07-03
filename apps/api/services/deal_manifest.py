"""Read-only Deal OS manifest and OpenClaw import summaries."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from services import deal_store


DEAL_MANIFEST_SUMMARY_SCHEMA = "siq_deal_manifest_summary_v1"
ARCHIVE_MANIFEST_PATH = "audit/archive_manifest.json"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_package_file(package_dir: Path, relative_path: Any) -> Path | None:
    normalized = str(relative_path or "").strip().replace("\\", "/").strip("/")
    if not normalized:
        return None
    path = Path(normalized)
    if path.is_absolute() or ".." in path.parts:
        return None
    candidate = (package_dir / path).resolve()
    try:
        candidate.relative_to(package_dir.resolve())
    except ValueError:
        return None
    return candidate


def _file_identity(item: Any) -> str:
    payload = _as_dict(item)
    return "|".join([
        str(payload.get("source") or ""),
        str(payload.get("target") or ""),
        str(payload.get("status") or ""),
        str(payload.get("sha256") or ""),
        str(payload.get("reason") or ""),
    ])


def _archive_consistency(openclaw_import: dict[str, Any], archive_manifest: dict[str, Any] | None) -> str:
    if not archive_manifest:
        return "missing"
    manifest_files = _as_list(openclaw_import.get("files"))
    archive_files = _as_list(archive_manifest.get("files"))
    if not openclaw_import:
        return "archive_only"
    if sorted(_file_identity(item) for item in manifest_files) == sorted(_file_identity(item) for item in archive_files):
        return "match"
    return "mismatch"


def _summarize_file(
    item: Any,
    *,
    package_dir: Path,
    hashes: dict[str, Any],
) -> dict[str, Any]:
    payload = _as_dict(item)
    target = str(payload.get("target") or "")
    source = str(payload.get("source") or "")
    status = str(payload.get("status") or "unknown")
    recorded_sha = str(hashes.get(target) or "") if target else ""
    imported_sha = str(payload.get("sha256") or "")
    target_path = _safe_package_file(package_dir, target)
    target_exists = bool(target_path and target_path.is_file())
    actual_sha = _sha256(target_path) if target_exists and target_path else ""
    hash_recorded = bool(recorded_sha)
    hash_matches: bool | None
    if status != "imported":
        hash_matches = None
    elif not target_exists:
        hash_matches = False
    elif not hash_recorded:
        hash_matches = False
    elif actual_sha:
        hash_matches = recorded_sha == actual_sha and (not imported_sha or imported_sha == actual_sha)
    elif imported_sha:
        hash_matches = recorded_sha == imported_sha
    else:
        hash_matches = False
    return {
        "source": source,
        "target": target,
        "status": status,
        "reason": payload.get("reason"),
        "sha256": imported_sha or actual_sha or recorded_sha or None,
        "hash_recorded": hash_recorded,
        "hash_matches": hash_matches,
        "target_exists": target_exists,
    }


def _manifest_status(openclaw_import_present: bool, warnings: list[str]) -> str:
    if not openclaw_import_present:
        return "missing"
    if warnings:
        return "warn"
    return "pass"


def summarize_deal_manifest(
    deal_id: str,
    *,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    package_dir = deal_store.safe_deal_dir(deal_id, wiki_root=wiki_root)
    manifest = deal_store.read_json(package_dir / "manifest.json", None)
    if manifest is None:
        raise FileNotFoundError(deal_id)
    manifest = _as_dict(manifest)
    normalized_deal_id = deal_store.validate_deal_id(deal_id)
    hashes = _as_dict(manifest.get("hashes"))
    openclaw_import = _as_dict(manifest.get("openclaw_import"))
    archive_manifest = deal_store.read_json(package_dir / ARCHIVE_MANIFEST_PATH, None)
    archive_manifest = _as_dict(archive_manifest) if archive_manifest is not None else None
    archive_files = _as_list(archive_manifest.get("files")) if archive_manifest else []
    import_files = _as_list(openclaw_import.get("files")) or archive_files
    files = [
        _summarize_file(item, package_dir=package_dir, hashes=hashes)
        for item in import_files
        if isinstance(item, dict)
    ]
    status_counts = {
        status: len([item for item in files if item.get("status") == status])
        for status in sorted({str(item.get("status") or "unknown") for item in files})
    }
    imported_files = [item for item in files if item.get("status") == "imported"]
    missing_files = [item for item in files if item.get("status") == "missing"]
    rejected_files = [item for item in files if item.get("status") == "rejected"]
    files_missing_hash = [
        item for item in imported_files
        if not item.get("hash_recorded") or item.get("hash_matches") is False
    ]
    consistency = _archive_consistency(openclaw_import, archive_manifest)

    warnings: list[str] = []
    if not openclaw_import:
        warnings.append("openclaw_import_missing")
    if openclaw_import and not archive_manifest:
        warnings.append("archive_manifest_missing")
    if consistency == "mismatch":
        warnings.append("archive_manifest_mismatch")
    declared_file_count = openclaw_import.get("file_count")
    if isinstance(declared_file_count, int) and declared_file_count != len(imported_files):
        warnings.append("openclaw_file_count_mismatch")
    for item in missing_files:
        warnings.append(f"import_file_missing:{item.get('target') or item.get('source')}")
    for item in rejected_files:
        warnings.append(f"import_file_rejected:{item.get('target') or item.get('source')}")
    for item in files_missing_hash:
        reason = "mismatch" if item.get("hash_matches") is False and item.get("hash_recorded") else "missing"
        warnings.append(f"imported_file_hash_{reason}:{item.get('target')}")

    payload = {
        "schema_version": DEAL_MANIFEST_SUMMARY_SCHEMA,
        "deal_id": normalized_deal_id,
        "generated_at": deal_store.utc_now_iso(),
        "status": _manifest_status(bool(openclaw_import), warnings),
        "counts": {
            "hashes": len(hashes),
            "import_files": len(files),
            "imported_files": len(imported_files),
            "missing_files": len(missing_files),
            "rejected_files": len(rejected_files),
            "files_with_hash": len([item for item in imported_files if item.get("hash_recorded")]),
            "files_missing_hash": len(files_missing_hash),
            "archive_files": len(archive_files),
            "status": status_counts,
        },
        "openclaw_import": {
            "present": bool(openclaw_import),
            "legacy_project_id": openclaw_import.get("legacy_project_id"),
            "imported_at": openclaw_import.get("imported_at"),
            "file_count": openclaw_import.get("file_count"),
            "metadata_present": bool(openclaw_import.get("metadata")),
        },
        "archive_manifest": {
            "available": bool(archive_manifest),
            "path": ARCHIVE_MANIFEST_PATH,
            "file_count": archive_manifest.get("file_count") if archive_manifest else None,
            "consistency": consistency,
        },
        "files": files,
        "warnings": warnings,
    }
    return deal_store.redact_public_payload(payload)
