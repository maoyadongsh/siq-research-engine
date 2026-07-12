#!/usr/bin/env python3
# isort: skip_file
"""Build an offline, non-mutating Agent Memory Milvus migration plan."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = PROJECT_ROOT / "apps" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from services import agent_memory_milvus  # noqa: E402


SNAPSHOT_SCHEMA = "siq_agent_memory_milvus_snapshot_v1"
REPORT_SCHEMA = "siq_agent_memory_milvus_migration_plan_v1"
ROLLBACK_SCHEMA = "siq_agent_memory_milvus_alias_rollback_v1"
IDENTITY_COUNT_FIELDS = (
    "research_scoped_count",
    "complete_count",
    "partial_count",
    "unscoped_count",
)
IDENTITY_FIELDS = ("market", "company_id", "filing_id", "parse_run_id")
DEFAULT_SNAPSHOT = (
    PROJECT_ROOT
    / "eval_datasets"
    / "agent_memory_milvus_migration_contract"
    / "v1"
    / "source_snapshot.json"
)
DEFAULT_OUTPUT = PROJECT_ROOT / "artifacts/eval-runs/local/agent-memory-milvus-migration-plan.json"
DEFAULT_MARKDOWN = PROJECT_ROOT / "artifacts/eval-runs/local/agent-memory-milvus-migration-plan.md"


def _non_negative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _positive_int(value: Any, field: str) -> int:
    result = _non_negative_int(value, field)
    if result == 0:
        raise ValueError(f"{field} must be a positive integer")
    return result


def validate_snapshot(snapshot: dict[str, Any]) -> None:
    if snapshot.get("schema_version") != SNAPSHOT_SCHEMA:
        raise ValueError(f"snapshot schema_version must be {SNAPSHOT_SCHEMA}")
    if snapshot.get("snapshot_kind") not in {"synthetic_contract", "redacted_read_only_inventory"}:
        raise ValueError("snapshot_kind must be synthetic_contract or redacted_read_only_inventory")
    collection = snapshot.get("collection")
    if not isinstance(collection, dict):
        raise ValueError("snapshot collection must be an object")
    if not str(collection.get("name") or "").strip():
        raise ValueError("snapshot collection.name is required")
    fields = collection.get("fields")
    if not isinstance(fields, list) or not fields or any(not str(item).strip() for item in fields):
        raise ValueError("snapshot collection.fields must be a non-empty string list")
    if len(set(fields)) != len(fields):
        raise ValueError("snapshot collection.fields contains duplicates")

    entity_count = _non_negative_int(collection.get("entity_count"), "collection.entity_count")
    _positive_int(collection.get("vector_dimension"), "collection.vector_dimension")
    if not str(collection.get("metric_type") or "").strip():
        raise ValueError("collection.metric_type is required")
    if not str(collection.get("index_type") or "").strip():
        raise ValueError("collection.index_type is required")
    manifest_hash = str(collection.get("id_content_hash_manifest_sha256") or "")
    if len(manifest_hash) != 64 or any(char not in "0123456789abcdef" for char in manifest_hash):
        raise ValueError("collection.id_content_hash_manifest_sha256 must be a lowercase SHA-256")
    identity = snapshot.get("identity")
    if not isinstance(identity, dict):
        raise ValueError("snapshot identity must be an object")
    observation_status = str(identity.get("observation_status") or "observed")
    if observation_status not in {"observed", "unavailable"}:
        raise ValueError("identity.observation_status must be observed or unavailable")
    missing_by_field = identity.get("missing_by_field")
    if not isinstance(missing_by_field, dict) or set(missing_by_field) != set(IDENTITY_FIELDS):
        raise ValueError("identity.missing_by_field must contain the four ResearchIdentity fields")

    if observation_status == "unavailable":
        if not str(identity.get("observation_reason") or "").strip():
            raise ValueError("identity.observation_reason is required when observation_status is unavailable")
        if any(identity.get(field) is not None for field in IDENTITY_COUNT_FIELDS):
            raise ValueError("unavailable identity counts must be null, not zero or inferred")
        if any(missing_by_field.get(field) is not None for field in IDENTITY_FIELDS):
            raise ValueError("unavailable identity missing_by_field values must be null")
    else:
        scoped = _non_negative_int(identity.get("research_scoped_count"), "identity.research_scoped_count")
        complete = _non_negative_int(identity.get("complete_count"), "identity.complete_count")
        partial = _non_negative_int(identity.get("partial_count"), "identity.partial_count")
        unscoped = _non_negative_int(identity.get("unscoped_count"), "identity.unscoped_count")
        if scoped != complete + partial:
            raise ValueError("research_scoped_count must equal complete_count + partial_count")
        if entity_count != scoped + unscoped:
            raise ValueError("entity_count must equal research_scoped_count + unscoped_count")
    for field, value in missing_by_field.items():
        if observation_status == "unavailable":
            continue
        missing = _non_negative_int(value, f"identity.missing_by_field.{field}")
        if missing > partial:
            raise ValueError(f"identity.missing_by_field.{field} exceeds partial_count")

    aliases = snapshot.get("aliases", [])
    if not isinstance(aliases, list):
        raise ValueError("snapshot aliases must be a list")
    for item in aliases:
        if not isinstance(item, dict) or not item.get("name") or not item.get("collection"):
            raise ValueError("each snapshot alias requires name and collection")


def _alias_target(snapshot: dict[str, Any], alias: str) -> str | None:
    for item in snapshot.get("aliases") or []:
        if str(item.get("name")) == alias:
            return str(item.get("collection"))
    return None


def build_migration_plan(
    snapshot: dict[str, Any],
    *,
    target_collection: str = "",
    alias: str = "",
) -> dict[str, Any]:
    validate_snapshot(snapshot)
    source = snapshot["collection"]
    source_name = str(source["name"])
    target_name = target_collection.strip() or f"{source_name}__v2"
    alias_name = alias.strip() or f"{source_name}_active"
    if target_name == source_name:
        raise ValueError("target collection must differ from source collection")
    if alias_name in {source_name, target_name}:
        raise ValueError("alias must differ from source and target collection names")

    source_fields = {str(item) for item in source["fields"]}
    target_fields = set(agent_memory_milvus.REQUIRED_FIELDS)
    missing_target_fields = sorted(target_fields - source_fields)
    unexpected_source_fields = sorted(source_fields - target_fields)
    identity = snapshot["identity"]
    observation_status = str(identity.get("observation_status") or "observed")
    partial_count = int(identity["partial_count"]) if observation_status == "observed" else None
    snapshot_kind = str(snapshot["snapshot_kind"])
    existing_alias_target = _alias_target(snapshot, alias_name)
    alias_bootstrap_required = existing_alias_target is None
    alias_already_on_target = existing_alias_target == target_name
    blocking_reasons: list[str] = []
    if snapshot_kind == "synthetic_contract":
        blocking_reasons.append("redacted_read_only_inventory_required")
    if observation_status == "unavailable":
        blocking_reasons.append("identity_inventory_unavailable")
    elif partial_count:
        blocking_reasons.append("partial_research_identity_backfill_required")
    if existing_alias_target not in {None, source_name, target_name}:
        blocking_reasons.append("alias_points_to_unexpected_collection")
    if alias_bootstrap_required:
        blocking_reasons.append("source_alias_bootstrap_required")

    entity_count = int(source["entity_count"])
    provenance = snapshot.get("provenance") if isinstance(snapshot.get("provenance"), dict) else {}
    source_inventory_live_milvus_contacted = bool(
        snapshot.get("live_milvus_contacted", provenance.get("source_inventory_live_milvus_contacted", False))
    )
    rollback = {
        "schema_version": ROLLBACK_SCHEMA,
        "alias": alias_name,
        "restore_collection": source_name,
        "from_collection": target_name,
        "preconditions": [
            "source collection remains loaded and unchanged",
            "source count and content-hash manifest were captured before alias switch",
            "clients resolve the configured alias instead of a physical collection name",
        ],
        "steps": [
            {"action": "switch_alias", "alias": alias_name, "collection": source_name},
            {"action": "verify_alias_target", "alias": alias_name, "collection": source_name},
            {"action": "run_read_only_retrieval_smoke", "collection": alias_name},
        ],
        "destructive_actions": [],
    }
    return {
        "schema_version": REPORT_SCHEMA,
        "passed": True,
        "dry_run": True,
        "writes_performed": False,
        "planner_live_milvus_contacted": False,
        "source_inventory_live_milvus_contacted": source_inventory_live_milvus_contacted,
        "live_milvus_contacted": False,
        "snapshot_kind": snapshot_kind,
        "evidence_scope": "contract_only" if snapshot_kind == "synthetic_contract" else "provided_read_only_inventory",
        "migration_ready": not blocking_reasons,
        "blocking_reasons": blocking_reasons,
        "source": {
            "collection": source_name,
            "declared_schema_version": source.get("declared_schema_version"),
            "entity_count": entity_count,
            "field_count": len(source_fields),
            "vector_dimension": int(source["vector_dimension"]),
            "metric_type": str(source["metric_type"]),
            "index_type": str(source["index_type"]),
            "id_content_hash_manifest_sha256": str(source["id_content_hash_manifest_sha256"]),
            "fields": sorted(source_fields),
            "missing_v2_fields": missing_target_fields,
            "unexpected_fields": unexpected_source_fields,
        },
        "target": {
            "collection": target_name,
            "schema_version": agent_memory_milvus.COLLECTION_SCHEMA_VERSION,
            "expected_entity_count": entity_count,
            "vector_dimension": int(source["vector_dimension"]),
            "metric_type": str(source["metric_type"]),
            "index_type": str(source["index_type"]),
            "expected_id_content_hash_manifest_sha256": str(source["id_content_hash_manifest_sha256"]),
            "required_fields": sorted(target_fields),
            "create_only": True,
        },
        "identity_backfill": {
            "observation_status": observation_status,
            "observation_reason": identity.get("observation_reason"),
            "research_scoped_count": (
                int(identity["research_scoped_count"]) if observation_status == "observed" else None
            ),
            "complete_count": int(identity["complete_count"]) if observation_status == "observed" else None,
            "partial_count": partial_count,
            "unscoped_count": int(identity["unscoped_count"]) if observation_status == "observed" else None,
            "missing_by_field": dict(identity["missing_by_field"]),
            "policy": {
                "complete": "copy exact ResearchIdentity fields",
                "partial": "block migration until authoritative backfill or quarantine",
                "unscoped": "keep all ResearchIdentity fields empty",
                "unavailable": "block migration; require a read-only scalar inventory with explicit unknowns resolved",
                "inference": "never infer identity from content, title, or source path",
            },
        },
        "alias_plan": {
            "alias": alias_name,
            "current_target": existing_alias_target,
            "bootstrap_required": alias_bootstrap_required,
            "already_on_target": alias_already_on_target,
            "switch_from": existing_alias_target or source_name,
            "switch_to": target_name,
        },
        "verification": {
            "required_before_alias_switch": [
                "target schema contains every required v2 field",
                "target entity count equals source entity count",
                "stable id and content_hash sets match source",
                "complete, partial, and unscoped identity counts match the approved backfill manifest",
                "unscoped retrieval excludes all research-scoped records",
                "complete-identity retrieval matches all four ResearchIdentity fields",
            ],
            "expected_entity_count": entity_count,
            "max_partial_identity_count": 0,
            "identity_observation_required": True,
        },
        "rollback_manifest": rollback,
        "prohibited_actions": [
            "drop source collection",
            "enable destructive schema recreate",
            "switch alias before count/hash/identity verification",
            "infer missing ResearchIdentity from free text",
        ],
    }


def render_markdown(report: dict[str, Any]) -> str:
    source = report["source"]
    target = report["target"]
    identity = report["identity_backfill"]
    alias = report["alias_plan"]
    lines = [
        "# Agent Memory Milvus Migration Dry Run",
        "",
        f"- Contract: **{'PASS' if report['passed'] else 'FAIL'}**",
        f"- Migration ready: `{report['migration_ready']}`",
        f"- Writes performed: `{report['writes_performed']}`",
        f"- Planner contacted live Milvus: `{report['planner_live_milvus_contacted']}`",
        f"- Source inventory contacted live Milvus: `{report['source_inventory_live_milvus_contacted']}`",
        f"- Snapshot kind: `{report['snapshot_kind']}`",
        f"- Source: `{source['collection']}` ({source['entity_count']} records)",
        f"- Target: `{target['collection']}` ({target['schema_version']})",
        f"- Missing v2 fields: `{', '.join(source['missing_v2_fields']) or 'none'}`",
        f"- Identity observation: `{identity['observation_status']}`",
        f"- Partial ResearchIdentity records: `{identity['partial_count'] if identity['partial_count'] is not None else 'unknown'}`",
        f"- Alias: `{alias['alias']}` (`{alias['current_target']}` -> `{target['collection']}`)",
    ]
    if report["blocking_reasons"]:
        lines.extend(["", "## Blocking Reasons", ""])
        lines.extend(f"- `{reason}`" for reason in report["blocking_reasons"])
    lines.extend(["", "## Rollback", ""])
    lines.append(
        f"Switch `{report['rollback_manifest']['alias']}` back to "
        f"`{report['rollback_manifest']['restore_collection']}`; the plan contains no destructive actions."
    )
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--target-collection", default="")
    parser.add_argument("--alias", default="")
    parser.add_argument("--require-ready", action="store_true")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
    report = build_migration_plan(
        snapshot,
        target_collection=args.target_collection,
        alias=args.alias,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "migration_ready": report["migration_ready"]}, sort_keys=True))
    return 1 if args.require_ready and not report["migration_ready"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
