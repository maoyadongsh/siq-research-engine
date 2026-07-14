"""Read-only Investment Committee profile and workflow policy helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from services.hermes_client import HERMES_PROFILE_ALIASES, SIQ_HERMES_DEFAULT_PORTS, hermes_profile_config
from services.path_config import PROJECT_ROOT


IC_PROFILE_IDS: tuple[str, ...] = (
    "siq_ic_master_coordinator",
    "siq_ic_chairman",
    "siq_ic_strategist",
    "siq_ic_sector_expert",
    "siq_ic_finance_auditor",
    "siq_ic_legal_scanner",
    "siq_ic_risk_controller",
)
R1_AGENT_SEQUENCE: tuple[str, ...] = (
    "siq_ic_strategist",
    "siq_ic_sector_expert",
    "siq_ic_finance_auditor",
    "siq_ic_legal_scanner",
    "siq_ic_risk_controller",
    "siq_ic_chairman",
)
LEGACY_PROFILE_IDS: dict[str, str] = {
    "ic_master_coordinator": "siq_ic_master_coordinator",
    "ic_chairman": "siq_ic_chairman",
    "ic_strategist": "siq_ic_strategist",
    "ic_sector_expert": "siq_ic_sector_expert",
    "ic_finance_auditor": "siq_ic_finance_auditor",
    "ic_legal_scanner": "siq_ic_legal_scanner",
    "ic_risk_controller": "siq_ic_risk_controller",
}

IC_PROFILES_ROOT = PROJECT_ROOT / "agents" / "hermes" / "profiles"
IC_SHARED_ROOT = IC_PROFILES_ROOT / "siq_ic_shared"
IC_WORKFLOW_POLICY_PATH = IC_SHARED_ROOT / "ic_workflow_policy.json"
IC_PROFILE_MATRIX_PATH = IC_SHARED_ROOT / "ic_profile_matrix.json"
IC_SCRIPT_MIGRATION_MATRIX_PATH = IC_SHARED_ROOT / "openclaw_script_migration_matrix.json"
IC_PROFILES_MANIFEST_PATH = IC_PROFILES_ROOT / "manifest.json"


def _read_json_file(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"IC policy file not found: {path.name}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"IC policy file is not valid JSON: {path.name}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"IC policy file must contain a JSON object: {path.name}")
    return payload


def read_ic_workflow_policy(*, policy_path: Path | str | None = None) -> dict[str, Any]:
    return _read_json_file(Path(policy_path) if policy_path else IC_WORKFLOW_POLICY_PATH)


def read_ic_profile_matrix(*, matrix_path: Path | str | None = None) -> dict[str, Any]:
    return _read_json_file(Path(matrix_path) if matrix_path else IC_PROFILE_MATRIX_PATH)


def read_ic_profiles_manifest(*, manifest_path: Path | str | None = None) -> dict[str, Any]:
    return _read_json_file(Path(manifest_path) if manifest_path else IC_PROFILES_MANIFEST_PATH)


def read_openclaw_script_migration_matrix(*, matrix_path: Path | str | None = None) -> dict[str, Any]:
    return _read_json_file(Path(matrix_path) if matrix_path else IC_SCRIPT_MIGRATION_MATRIX_PATH)


def public_openclaw_script_migration_matrix_payload(matrix: dict[str, Any]) -> dict[str, Any]:
    behavior_entries = matrix.get("behavior_entries")
    is_behavior_v2 = isinstance(behavior_entries, list)
    entries = [
        item for item in (behavior_entries if is_behavior_v2 else matrix.get("entries", []))
        if isinstance(item, dict)
    ]
    status_counts: dict[str, int] = {}
    category_counts: dict[str, int] = {}
    owner_counts: dict[str, int] = {}
    quality_accepted_count = 0
    for item in entries:
        status = str(item.get("parity_level") or item.get("status") or "unknown")
        category = str(item.get("phase") or item.get("category") or "unknown")
        owner = str(item.get("owner") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
        category_counts[category] = category_counts.get(category, 0) + 1
        owner_counts[owner] = owner_counts.get(owner, 0) + 1
        if item.get("quality_accepted") is True:
            quality_accepted_count += 1
    payload = {
        "schema_version": matrix.get("schema_version"),
        "updated_at": matrix.get("updated_at"),
        "purpose": matrix.get("purpose"),
        "source_scope": matrix.get("source_scope") or [],
        "status_definitions": matrix.get("status_definitions") or {},
        "parity_definitions": matrix.get("parity_definitions") or {},
        "counts": {
            "entries": len(entries),
            "by_status": dict(sorted(status_counts.items())),
            "by_category": dict(sorted(category_counts.items())),
            "by_owner": dict(sorted(owner_counts.items())),
            "quality_accepted": quality_accepted_count,
        },
        "entries": entries,
    }
    if is_behavior_v2:
        payload.update({
            "authority": matrix.get("authority") or {},
            "acceptance_policy": matrix.get("acceptance_policy") or {},
            "behavior_entries": entries,
        })
        payload["counts"]["by_parity_level"] = payload["counts"]["by_status"]
        payload["counts"]["by_phase"] = payload["counts"]["by_category"]
    return payload


def public_openclaw_script_migration_matrix() -> dict[str, Any]:
    return public_openclaw_script_migration_matrix_payload(read_openclaw_script_migration_matrix())


def _aliases_for(profile_id: str) -> list[str]:
    return sorted(alias for alias, canonical in HERMES_PROFILE_ALIASES.items() if canonical == profile_id and alias != profile_id)


def canonical_ic_profile_id(agent_id: str | None) -> str:
    normalized = str(agent_id or "").strip()
    return HERMES_PROFILE_ALIASES.get(normalized, LEGACY_PROFILE_IDS.get(normalized, normalized))


def list_ic_profiles(*, include_runtime: bool = False) -> list[dict[str, Any]]:
    matrix = read_ic_profile_matrix()
    manifest = read_ic_profiles_manifest()
    raw_profiles = matrix.get("profiles", [])
    if not isinstance(raw_profiles, list):
        raw_profiles = []
    by_id = {item.get("id"): item for item in raw_profiles if isinstance(item, dict)}
    manifest_profiles = manifest.get("profiles", [])
    if not isinstance(manifest_profiles, list):
        manifest_profiles = []
    manifest_ic_group = (manifest.get("groups") or {}).get("ic", []) if isinstance(manifest.get("groups"), dict) else []
    if not isinstance(manifest_ic_group, list):
        manifest_ic_group = []

    profiles: list[dict[str, Any]] = []
    for profile_id in IC_PROFILE_IDS:
        item = dict(by_id.get(profile_id) or {})
        item.setdefault("id", profile_id)
        item.setdefault("label", profile_id)
        item.setdefault("role", profile_id.removeprefix("siq_ic_"))
        item["aliases"] = _aliases_for(profile_id)
        item["default_port"] = SIQ_HERMES_DEFAULT_PORTS[profile_id]
        item["profile_path"] = f"agents/hermes/profiles/{profile_id}"
        item["config_exists"] = (IC_PROFILES_ROOT / profile_id / "config.yaml").is_file()
        item["in_manifest"] = profile_id in manifest_profiles
        item["in_manifest_group"] = profile_id in manifest_ic_group
        item["startup_retrieval_required"] = True
        item["r1_sequence_index"] = (
            R1_AGENT_SEQUENCE.index(profile_id)
            if profile_id in R1_AGENT_SEQUENCE
            else None
        )
        if include_runtime:
            item["runtime"] = hermes_profile_config(profile_id)
        profiles.append(item)
    return profiles


def public_ic_workflow_policy() -> dict[str, Any]:
    policy = read_ic_workflow_policy()
    return {
        "version": policy.get("version"),
        "workflow": policy.get("workflow") or {},
        "weights": policy.get("weights") or {},
        "thresholds": policy.get("thresholds") or {},
        "roles": policy.get("roles") or {},
        "evidence_gate": policy.get("evidence_gate") or {},
        "chairman_scoring": policy.get("chairman_scoring") or {},
        "r1_agent_sequence": list(R1_AGENT_SEQUENCE),
        "profiles": list_ic_profiles(include_runtime=False),
    }
