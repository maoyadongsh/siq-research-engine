#!/usr/bin/env python3
# isort: skip_file
"""Stage a non-destructive Agent Memory Milvus v1 to v2 migration."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = PROJECT_ROOT / "apps" / "api"
SCRIPT_ROOT = Path(__file__).resolve().parent
for path in (API_ROOT, SCRIPT_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from services import agent_memory_milvus  # noqa: E402
import inspect_agent_memory_milvus_inventory as inventory  # noqa: E402
import plan_agent_memory_milvus_migration as planner  # noqa: E402


REPORT_SCHEMA = "siq_agent_memory_milvus_migration_execution_v1"
DEFAULT_SNAPSHOT = PROJECT_ROOT / "artifacts/eval-runs/local/agent-memory-milvus-profile-inventory-20260712.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "artifacts/eval-runs/local/agent-memory-milvus-migration-execution.json"
SOURCE_COPY_FIELDS = sorted(agent_memory_milvus.REQUIRED_FIELDS - set(agent_memory_milvus.RESEARCH_IDENTITY_FIELDS))


def _alias_names(client: Any, collection: str) -> list[str]:
    try:
        raw = client.list_aliases(collection_name=collection)
    except TypeError:
        raw = client.list_aliases(collection)
    raw_aliases = raw.get("aliases") if isinstance(raw, dict) else raw
    names: list[str] = []
    for item in raw_aliases or []:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, dict):
            name = str(item.get("alias_name") or item.get("name") or "")
            if name:
                names.append(name)
    return sorted(set(names))


def alias_target(client: Any, alias: str, collections: list[str]) -> str | None:
    for collection in collections:
        if client.has_collection(collection) and alias in _alias_names(client, collection):
            describe_alias = getattr(client, "describe_alias", None)
            if callable(describe_alias):
                try:
                    try:
                        description = describe_alias(alias=alias)
                    except TypeError:
                        description = describe_alias(alias)
                    physical = str(
                        description.get("collection_name") or description.get("collection") or ""
                    ) if isinstance(description, dict) else ""
                    if physical:
                        if physical == collection:
                            return collection
                        continue
                except Exception:
                    continue
            return collection
    return None


def _query_rows(client: Any, collection: str, fields: list[str], count: int) -> list[dict[str, Any]]:
    if count <= 16384:
        rows = client.query(
            collection_name=collection,
            filter='id != ""',
            output_fields=fields,
            limit=max(1, count),
        )
        return [row for row in rows if isinstance(row, dict)]
    iterator = client.query_iterator(
        collection_name=collection,
        filter='id != ""',
        output_fields=fields,
        batch_size=1024,
        limit=count,
    )
    rows: list[dict[str, Any]] = []
    try:
        while True:
            batch = iterator.next()
            if not batch:
                break
            rows.extend(row for row in batch if isinstance(row, dict))
    finally:
        close = getattr(iterator, "close", None)
        if callable(close):
            close()
    return rows


def validate_profile_snapshot(snapshot: dict[str, Any], *, source: str, target: str, alias: str) -> None:
    planner.validate_snapshot(snapshot)
    collection = snapshot["collection"]
    identity = snapshot["identity"]
    if str(collection.get("name")) != source:
        raise ValueError("snapshot collection does not match source")
    if target == source or alias in {source, target}:
        raise ValueError("source, target, and alias must be distinct")
    if not bool((snapshot.get("provenance") or {}).get("contract_match")):
        raise ValueError("snapshot must prove the complete structured profile-file contract")
    fields = set(collection.get("fields") or [])
    if not set(SOURCE_COPY_FIELDS).issubset(fields):
        raise ValueError("snapshot source schema is missing v1 copy fields")
    if set(agent_memory_milvus.RESEARCH_IDENTITY_FIELDS).intersection(fields):
        raise ValueError("profile-file migration refuses a source schema with identity scalar fields")
    if int(collection.get("vector_dimension") or 0) <= 0:
        raise ValueError("snapshot vector_dimension must be positive")
    if not str(collection.get("metric_type") or "").strip() or not str(collection.get("index_type") or "").strip():
        raise ValueError("snapshot metric_type and index_type are required")
    if identity.get("observation_status") != "observed":
        raise ValueError("snapshot ResearchIdentity observation must be observed")
    entity_count = int(collection["entity_count"])
    expected = {
        "research_scoped_count": 0,
        "complete_count": 0,
        "partial_count": 0,
        "unscoped_count": entity_count,
    }
    if any(identity.get(field) != value for field, value in expected.items()):
        raise ValueError("profile-file migration requires every source record to be observed as unscoped")
    if any(identity.get("missing_by_field", {}).get(field) != 0 for field in inventory.IDENTITY_FIELDS):
        raise ValueError("profile-file identity field observations must be complete")


def _compare_live_snapshot(expected: dict[str, Any], observed: dict[str, Any]) -> None:
    expected_collection = expected["collection"]
    observed_collection = observed["collection"]
    for field in (
        "name",
        "entity_count",
        "fields",
        "vector_dimension",
        "metric_type",
        "index_type",
        "id_content_hash_manifest_sha256",
    ):
        if expected_collection.get(field) != observed_collection.get(field):
            raise RuntimeError(f"live source inventory drifted from approved snapshot: {field}")
    if not bool((observed.get("provenance") or {}).get("contract_match")):
        raise RuntimeError("live source no longer matches the profile-file contract")


def _copy_source_rows(client: Any, *, source: str, target: str, count: int, batch_size: int) -> int:
    rows = _query_rows(client, source, SOURCE_COPY_FIELDS, count)
    if len(rows) != count:
        raise RuntimeError("source row count changed while copying")
    copied = 0
    for offset in range(0, len(rows), batch_size):
        batch = []
        for row in rows[offset : offset + batch_size]:
            payload = {field: row.get(field) for field in SOURCE_COPY_FIELDS}
            payload.update({field: "" for field in agent_memory_milvus.RESEARCH_IDENTITY_FIELDS})
            batch.append(payload)
        client.upsert(collection_name=target, data=batch)
        copied += len(batch)
    client.flush(collection_name=target)
    return copied


def verify_target(client: Any, *, target: str, expected: dict[str, Any]) -> dict[str, Any]:
    preflight = agent_memory_milvus.collection_schema_preflight(client=client, name=target)
    expected_count = int(expected["collection"]["entity_count"])
    rows = _query_rows(
        client,
        target,
        ["id", "content_hash", *agent_memory_milvus.RESEARCH_IDENTITY_FIELDS],
        expected_count,
    )
    manifest = inventory._manifest_sha256(rows) if len(rows) == expected_count else ""
    identities_empty = all(
        not str(row.get(field) or "")
        for row in rows
        for field in agent_memory_milvus.RESEARCH_IDENTITY_FIELDS
    )
    target_fields = inventory._field_details(client, target)
    target_vector = next((item for item in target_fields if item["name"] == agent_memory_milvus.VECTOR_FIELD), {})
    target_indexes = inventory._index_probe(client, target).get("indexes") or []
    target_index = next(
        (item.get("details") for item in target_indexes if isinstance(item, dict) and item.get("details")),
        {},
    )
    checks = {
        "schema_compatible": bool(preflight["compatible"]),
        "entity_count_matches": len(rows) == expected_count,
        "manifest_matches": manifest == expected["collection"]["id_content_hash_manifest_sha256"],
        "research_identity_fields_empty": identities_empty,
        "vector_dimension_matches": int(target_vector.get("dimension") or 0) == int(expected["collection"]["vector_dimension"]),
        "metric_type_matches": str(target_index.get("metric_type") or "") == str(expected["collection"]["metric_type"]),
        "index_type_matches": str(target_index.get("index_type") or "") == str(expected["collection"]["index_type"]),
    }
    return {"passed": all(checks.values()), "checks": checks, "observed_entity_count": len(rows), "manifest_sha256": manifest}


def execute_migration(
    snapshot: dict[str, Any],
    *,
    source: str,
    target: str,
    alias: str,
    apply: bool,
    switch_alias: bool,
    resume_existing_target: bool,
    rollback: bool = False,
    batch_size: int,
    client: Any | None = None,
) -> dict[str, Any]:
    validate_profile_snapshot(snapshot, source=source, target=target, alias=alias)
    report: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA,
        "passed": True,
        "dry_run": not apply,
        "writes_performed": [],
        "source": source,
        "target": target,
        "alias": alias,
        "switch_alias_requested": switch_alias,
        "rollback_requested": rollback,
        "source_preserved": True,
        "expected_entity_count": int(snapshot["collection"]["entity_count"]),
        "expected_manifest_sha256": snapshot["collection"]["id_content_hash_manifest_sha256"],
    }
    if rollback:
        if not apply:
            raise ValueError("--rollback requires --apply")
        resolved_client = client or agent_memory_milvus._client()
        if not resolved_client.has_collection(source):
            raise RuntimeError("refusing rollback because the source collection is missing")
        if not resolved_client.has_collection(target):
            raise RuntimeError("refusing rollback because the target collection is missing")
        current_alias_target = alias_target(resolved_client, alias, [source, target])
        if current_alias_target != target:
            raise RuntimeError("refusing rollback because alias does not point to the target collection")
        resolved_client.alter_alias(collection_name=source, alias=alias)
        if alias_target(resolved_client, alias, [source, target]) != source:
            raise RuntimeError("rollback alias verification failed")
        report["writes_performed"].append("rollback_alias_to_source")
        report["alias_target_after"] = source
        report["rollback"] = {"action": "alter_alias", "alias": alias, "collection": source}
        return report
    if not apply:
        report["next_action"] = "rerun with --apply; add --switch-alias only after reviewing verification gates"
        return report

    try:
        resolved_client = client or agent_memory_milvus._client()
        live_snapshot = inventory.collect_inventory(resolved_client, source)
        _compare_live_snapshot(snapshot, live_snapshot)
        current_alias_target = alias_target(resolved_client, alias, [source, target])
        if current_alias_target not in {None, source, target}:
            raise RuntimeError("migration alias points to an unexpected collection")

        target_exists = bool(resolved_client.has_collection(target))
        if target_exists and not resume_existing_target:
            raise RuntimeError("target collection already exists; use --resume-existing-target after inspection")

        if current_alias_target is None:
            resolved_client.create_alias(collection_name=source, alias=alias)
            report["writes_performed"].append("bootstrap_source_alias")
            current_alias_target = alias_target(resolved_client, alias, [source, target])
            if current_alias_target != source:
                raise RuntimeError("source alias bootstrap verification failed")

        if not target_exists:
            agent_memory_milvus.create_versioned_collection(
                client=resolved_client,
                name=target,
                dimension=int(snapshot["collection"]["vector_dimension"]),
                index_type=str(snapshot["collection"]["index_type"]),
                metric_type=str(snapshot["collection"]["metric_type"]),
            )
            report["writes_performed"].append("create_v2_collection")
            copied = _copy_source_rows(
                resolved_client,
                source=source,
                target=target,
                count=int(snapshot["collection"]["entity_count"]),
                batch_size=max(1, min(batch_size, 1000)),
            )
            report["writes_performed"].append("copy_unscoped_profile_records")
            report["copied_records"] = copied

        verification = verify_target(resolved_client, target=target, expected=snapshot)
        report["target_verification"] = verification
        if not verification["passed"]:
            raise RuntimeError("target verification failed; alias remains on source")
        report["target_ready_for_alias_switch"] = True
        if switch_alias and current_alias_target != target:
            try:
                resolved_client.alter_alias(collection_name=target, alias=alias)
                report["writes_performed"].append("switch_alias_to_v2")
                current_alias_target = alias_target(resolved_client, alias, [source, target])
                if current_alias_target != target:
                    raise RuntimeError("alias switch verification failed")
            except Exception as switch_error:
                try:
                    resolved_client.alter_alias(collection_name=source, alias=alias)
                    report["writes_performed"].append("rollback_alias_after_switch_failure")
                    report["alias_target_after_rollback"] = alias_target(resolved_client, alias, [source, target])
                except Exception as rollback_error:
                    report["rollback_error_type"] = type(rollback_error).__name__
                raise switch_error
        report["alias_target_after"] = current_alias_target
        report["rollback"] = {"action": "alter_alias", "alias": alias, "collection": source}
        return report
    except Exception as exc:
        report["passed"] = False
        report["error_type"] = type(exc).__name__
        exc.migration_report = report
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--source", default="siq_agent_memory")
    parser.add_argument("--target", default="siq_agent_memory__v2")
    parser.add_argument("--alias", default="siq_agent_memory_active")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--switch-alias", action="store_true")
    parser.add_argument("--resume-existing-target", action="store_true")
    parser.add_argument(
        "--rollback",
        "--rollback-alias",
        dest="rollback",
        action="store_true",
        help="Restore the alias to the source collection after a verified v2 switch.",
    )
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.switch_alias and not args.apply:
        raise SystemExit("--switch-alias requires --apply")
    if args.rollback and not args.apply:
        raise SystemExit("--rollback requires --apply")
    snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
    try:
        report = execute_migration(
            snapshot,
            source=args.source,
            target=args.target,
            alias=args.alias,
            apply=args.apply,
            switch_alias=args.switch_alias,
            resume_existing_target=args.resume_existing_target,
            rollback=args.rollback,
            batch_size=args.batch_size,
        )
    except Exception as exc:
        report = getattr(
            exc,
            "migration_report",
            {
                "schema_version": REPORT_SCHEMA,
                "passed": False,
                "dry_run": not args.apply,
                "writes_performed": [],
                "source": args.source,
                "target": args.target,
                "alias": args.alias,
                "error_type": type(exc).__name__,
            },
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "dry_run": report["dry_run"]}, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
