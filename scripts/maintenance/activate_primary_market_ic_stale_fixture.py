#!/usr/bin/env python3
"""Activate the synthetic update source for the PMIC stale-snapshot case.

This helper deliberately requires an already confirmed R4 decision. It copies
no fixture, produces no confirmation, and promotes no golden result. The only
mutation is adding the committed update source to an isolated Deal package and
asking the normal Evidence service to refresh the snapshot and stale prior
artifacts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "apps/api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from services import deal_decision, deal_evidence, deal_store  # noqa: E402

DESCRIPTOR_PATH = Path("scenario_inputs/stale_update.json")
AUDIT_PATH = Path("phases/audit_log.json")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON artifact: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"JSON artifact must be an object: {path}")
    return payload


def _contained_path(root: Path, relative: Any) -> Path:
    text = str(relative or "").strip()
    if not text or Path(text).is_absolute():
        raise ValueError("stale update artifact path must be relative")
    candidate = (root / text).resolve()
    if root.resolve() not in candidate.parents:
        raise ValueError("stale update artifact escaped the Deal package")
    return candidate


def _canonical_source(source: Mapping[str, Any]) -> dict[str, Any]:
    volatile = {"activated_at", "activated_by", "created_at", "updated_at"}
    return {key: value for key, value in source.items() if key not in volatile}


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _path_segment(value: Any, *, field: str) -> str:
    text = str(value or "").strip()
    if not text or Path(text).is_absolute() or len(Path(text).parts) != 1 or text in {".", ".."}:
        raise ValueError(f"stale update {field} must be one relative path segment")
    return text


def _verify_staged_source(
    *,
    source: Mapping[str, Any],
    archive_path: Path,
    content_path: Path,
    deal_id: str,
    document_id: str,
    parse_run_id: str,
) -> None:
    expected_archive_hash = str(source.get("archive_manifest_sha256") or "").lower()
    if len(expected_archive_hash) != 64 or _file_sha256(archive_path) != expected_archive_hash:
        raise ValueError("stale update archive digest mismatch")
    archive = _read_json(archive_path)
    artifacts = archive.get("artifacts")
    content_entry = (
        next(
            (
                item
                for item in artifacts
                if isinstance(item, Mapping) and item.get("path") == "content_list_enhanced.json"
            ),
            None,
        )
        if isinstance(artifacts, list)
        else None
    )
    expected_content_hash = str((content_entry or {}).get("sha256") or "").lower()
    actual_content_hash = _file_sha256(content_path)
    if len(expected_content_hash) != 64 or actual_content_hash != expected_content_hash:
        raise ValueError("stale update content digest mismatch")
    if archive.get("bundle_sha256") != actual_content_hash:
        raise ValueError("stale update bundle digest mismatch")
    expected_identity = {
        "deal_id": deal_id,
        "document_id": document_id,
        "parse_run_id": parse_run_id,
    }
    if any(archive.get(key) != value for key, value in expected_identity.items()):
        raise ValueError("stale update archive identity mismatch")
    content = _read_json(content_path)
    if any(content.get(key) != value for key, value in expected_identity.items()):
        raise ValueError("stale update content identity mismatch")
    expected_source_id = f"PM:{deal_id}:{document_id}:{parse_run_id}"
    if (
        source.get("schema_version") != "siq_primary_market_analysis_source_v1"
        or source.get("source_id") != expected_source_id
        or source.get("domain") != "primary_market"
        or source.get("source_type") != "primary_market_prospectus"
        or source.get("status") != "ready"
        or source.get("synthetic_evaluation_only") is not True
    ):
        raise ValueError("stale update source contract or identity is invalid")


def _completed_activation_result(
    *,
    deal_id: str,
    source_id: Any,
    confirmed_snapshot_hash: str,
    current_snapshot: Mapping[str, Any],
    workflow: Mapping[str, Any],
    idempotent_replay: bool,
) -> dict[str, Any]:
    current_hash = str(current_snapshot.get("snapshot_hash") or "")
    if (
        not current_hash
        or current_hash == confirmed_snapshot_hash
        or source_id not in current_snapshot.get("source_ids", [])
        or workflow.get("status") != "decision_review_required"
        or workflow.get("confirmed_decision_snapshot_hash") != confirmed_snapshot_hash
        or workflow.get("current_evidence_snapshot_hash") != current_hash
    ):
        raise RuntimeError("normal Evidence refresh did not block the confirmed decision")
    return {
        "schema_version": "siq_primary_market_ic_stale_activation_result_v1",
        "deal_id": deal_id,
        "source_id": source_id,
        "previous_snapshot_hash": confirmed_snapshot_hash,
        "current_snapshot_hash": current_hash,
        "workflow_status": workflow.get("status"),
        "idempotent_replay": idempotent_replay,
        "quality_accepted": False,
    }


def activate_stale_update(
    package_dir: Path,
    *,
    wiki_root: Path,
    actor: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    package_dir = package_dir.resolve()
    wiki_root = wiki_root.resolve()
    deal_id = package_dir.name
    if deal_store.safe_deal_dir(deal_id, wiki_root=wiki_root).resolve() != package_dir:
        raise ValueError("package must be the canonical Deal directory under wiki_root/deals")
    descriptor = _read_json(package_dir / DESCRIPTOR_PATH)
    if descriptor.get("schema_version") != "siq_primary_market_ic_stale_update_v1":
        raise ValueError("stale update descriptor schema is invalid")
    if (
        descriptor.get("deal_id") != deal_id
        or descriptor.get("synthetic_evaluation_only") is not True
        or descriptor.get("requires_existing_human_confirmation") is not True
    ):
        raise ValueError("stale update descriptor identity is invalid")

    decision_path = package_dir / "phases/r4_decision.json"
    decision = _read_json(decision_path)
    quality = _read_json(package_dir / deal_decision.R4_QUALITY_PATH)
    factcheck = _read_json(package_dir / deal_decision.R4_FACTCHECK_PATH)
    workflow_runs = _read_json(package_dir / deal_decision.WORKFLOW_RUNS_PATH)
    audit = _read_json(package_dir / AUDIT_PATH)
    deal_decision.validate_human_confirmation_attestation(
        decision,
        quality=quality,
        factcheck=factcheck,
        workflow_runs=workflow_runs,
        audit=audit,
    )
    current_snapshot = _read_json(package_dir / "evidence/evidence_snapshot.json")
    confirmed_snapshot_hash = str(decision.get("evidence_snapshot_hash") or "")

    source = descriptor.get("source")
    if not isinstance(source, dict) or source.get("deal_id") != deal_id:
        raise ValueError("stale update source identity is invalid")
    document_id = _path_segment(source.get("document_id"), field="document_id")
    parse_run_id = _path_segment(source.get("parse_run_id"), field="parse_run_id")
    staged_archive = _contained_path(package_dir, source.get("artifact_manifest_path"))
    staged_content = staged_archive.with_name("content_list_enhanced.json")
    if not staged_archive.is_file() or not staged_content.is_file():
        raise ValueError("stale update source artifacts are missing")
    _verify_staged_source(
        source=source,
        archive_path=staged_archive,
        content_path=staged_content,
        deal_id=deal_id,
        document_id=document_id,
        parse_run_id=parse_run_id,
    )

    final_dir = _contained_path(
        package_dir,
        Path("parsed_documents") / document_id / "runs" / parse_run_id,
    )
    final_archive = final_dir / "archive_manifest.json"
    final_content = final_dir / "content_list_enhanced.json"
    if final_dir.exists() and (not final_archive.is_file() or not final_content.is_file()):
        raise ValueError("partial stale update activation already exists")
    if final_dir.exists():
        if _file_sha256(final_archive) != _file_sha256(staged_archive) or _file_sha256(final_content) != _file_sha256(
            staged_content
        ):
            raise ValueError("existing stale update activation artifacts differ")

    registry_path = package_dir / "sources/analysis_sources.json"
    registry = _read_json(registry_path)
    sources = registry.get("sources")
    if not isinstance(sources, list):
        raise ValueError("analysis source registry is invalid")
    now = deal_store.utc_now_iso()
    final_source = {
        **source,
        "artifact_manifest_path": final_archive.relative_to(package_dir).as_posix(),
        "activated_by": dict(actor or {}),
        "activated_at": now,
        "created_at": now,
        "updated_at": now,
    }
    matching = [item for item in sources if isinstance(item, dict) and item.get("source_id") == source.get("source_id")]
    if len(matching) > 1:
        raise ValueError("stale update source_id is duplicated")
    if matching:
        if _canonical_source(matching[0]) != _canonical_source(final_source):
            raise ValueError("stale update source_id already exists with different content")
        current_hash = str(current_snapshot.get("snapshot_hash") or "")
        if current_hash != confirmed_snapshot_hash:
            workflow = _read_json(package_dir / "phases/workflow_state.json")
            return _completed_activation_result(
                deal_id=deal_id,
                source_id=source.get("source_id"),
                confirmed_snapshot_hash=confirmed_snapshot_hash,
                current_snapshot=current_snapshot,
                workflow=workflow,
                idempotent_replay=True,
            )
    elif current_snapshot.get("snapshot_hash") != confirmed_snapshot_hash:
        raise ValueError("confirmed R4 decision is not bound to the current pre-update snapshot")

    if not final_dir.exists():
        final_dir.mkdir(parents=True, exist_ok=False)
        try:
            shutil.copy2(staged_archive, final_archive)
            shutil.copy2(staged_content, final_content)
        except Exception:
            shutil.rmtree(final_dir)
            raise
    if not matching:
        sources.append(final_source)
        deal_store.write_json(registry_path, registry)

    refreshed = deal_evidence.refresh_evidence_snapshot(
        deal_id,
        built_by=dict(actor or {}),
        wiki_root=wiki_root,
    )
    workflow = _read_json(package_dir / "phases/workflow_state.json")
    return _completed_activation_result(
        deal_id=deal_id,
        source_id=source.get("source_id"),
        confirmed_snapshot_hash=confirmed_snapshot_hash,
        current_snapshot=refreshed,
        workflow=workflow,
        idempotent_replay=False,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--wiki-root", type=Path, required=True)
    parser.add_argument("--actor-id", default="pmic-golden-operator")
    parser.add_argument("--actor-username", default="pmic-golden-operator")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = activate_stale_update(
        args.package,
        wiki_root=args.wiki_root,
        actor={"id": args.actor_id, "username": args.actor_username},
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
