#!/usr/bin/env python3
"""Build sanitized evidence for host-owned PostgreSQL and Milvus memory writes.

The builder does not execute a database probe. It consumes two fixed, owner-only
probe receipts and fails closed unless both receipts prove the complete primary-
and secondary-market write/read/cleanup contract.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.openshell import check_sanitized_artifacts  # noqa: E402

SCHEMA_VERSION = "siq.openshell.memory-write-evidence.v1"
POSTGRES_RECEIPT_SCHEMA = "siq.openshell.postgresql-memory-write-probe-receipt.v1"
MILVUS_RECEIPT_SCHEMA = "siq.openshell.milvus-memory-write-probe-receipt.v1"
REPO_ROOT = Path(__file__).resolve().parents[2]
BOUNDARY_RELATIVE = Path("infra/openshell/data-broker/memory-collections.json")
EVIDENCE_SCHEMA_RELATIVE = Path("infra/openshell/schemas/memory-write-evidence.schema.json")
POSTGRES_RECEIPT_RELATIVE = Path("var/openshell/proofs/memory-postgresql-write-receipt.json")
MILVUS_RECEIPT_RELATIVE = Path("var/openshell/proofs/memory-milvus-write-receipt.json")
OUTPUT_JSON_RELATIVE = Path("artifacts/openshell/v0.6/memory-write-evidence.sanitized.json")
OUTPUT_MARKDOWN_RELATIVE = Path("artifacts/openshell/v0.6/memory-write-evidence.sanitized.md")

EXECUTOR = "host_fastapi_memory_service_only"
LOGICAL_ALIAS = "siq_agent_memory_active"
REQUIRED_COLLECTION_SCHEMA = "siq_agent_memory_milvus_v2"
AGENT_GROUPS = ("primary_market", "secondary_market")
MAX_INPUT_BYTES = 256 * 1024
MAX_CLOCK_SKEW_SECONDS = 30
MAX_EVIDENCE_AGE_SECONDS = 24 * 60 * 60
MAX_BACKEND_PROBE_SECONDS = 15 * 60
MAX_COMBINED_WINDOW_SECONDS = 30 * 60
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
PHYSICAL_COLLECTION_RE = re.compile(r"siq_agent_memory__v[1-9][0-9]*\Z")

EXPECTED_BOUNDARY = {
    "allowed_agent_groups": list(AGENT_GROUPS),
    "allowed_logical_aliases": [LOGICAL_ALIAS],
    "allowed_operations": ["delete_by_id", "flush", "search", "upsert"],
    "executor": EXECUTOR,
    "knowledge_collections_mutable": False,
    "required_schema_version": REQUIRED_COLLECTION_SCHEMA,
    "sandbox_direct_milvus": False,
    "schema_version": "siq.openshell.memory-collection-boundary.v1",
}
POSTGRES_OPERATIONS = ("insert", "readback", "rollback", "post_rollback_verify")
MILVUS_OPERATIONS = ("upsert", "get", "search", "delete", "post_delete_verify")


class MemoryEvidenceError(RuntimeError):
    """Stable failure code that does not echo local paths or receipt content."""


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("ascii")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise MemoryEvidenceError("input_json_duplicate_key")
        result[key] = value
    return result


def _walk_fixed_path(root: Path, relative: Path, *, missing_code: str) -> Path:
    if relative.is_absolute() or ".." in relative.parts:
        raise MemoryEvidenceError("fixed_path_invalid")
    current = root
    for part in relative.parts:
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError as exc:
            raise MemoryEvidenceError(missing_code) from exc
        if stat.S_ISLNK(info.st_mode):
            raise MemoryEvidenceError("input_symlink_not_allowed")
    return current


def _safe_read(root: Path, relative: Path, *, private: bool) -> bytes:
    path = _walk_fixed_path(root, relative, missing_code="input_missing")
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_nlink != 1
        or info.st_size > MAX_INPUT_BYTES
        or (private and stat.S_IMODE(info.st_mode) != 0o600)
        or (not private and stat.S_IMODE(info.st_mode) & 0o002)
    ):
        raise MemoryEvidenceError("input_file_unsafe")

    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino, opened.st_size) != (info.st_dev, info.st_ino, info.st_size):
            raise MemoryEvidenceError("input_changed")
        chunks: list[bytes] = []
        remaining = MAX_INPUT_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        final = os.fstat(descriptor)
        if (final.st_dev, final.st_ino, final.st_size) != (opened.st_dev, opened.st_ino, opened.st_size):
            raise MemoryEvidenceError("input_changed")
    except OSError as exc:
        raise MemoryEvidenceError("input_unreadable") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    content = b"".join(chunks)
    if len(content) > MAX_INPUT_BYTES:
        raise MemoryEvidenceError("input_too_large")
    return content


def _parse_json(content: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(content.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except MemoryEvidenceError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MemoryEvidenceError("input_json_invalid") from exc
    if not isinstance(payload, dict):
        raise MemoryEvidenceError("input_schema_invalid")
    return payload


def _integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _validate_time_window(receipt: Mapping[str, Any], *, now: int) -> tuple[int, int]:
    captured = receipt.get("captured_at_unix")
    completed = receipt.get("completed_at_unix")
    if (
        not _integer(captured)
        or not _integer(completed)
        or captured <= 0
        or completed < captured
        or completed - captured > MAX_BACKEND_PROBE_SECONDS
        or completed > now + MAX_CLOCK_SKEW_SECONDS
        or now - completed > MAX_EVIDENCE_AGE_SECONDS
    ):
        raise MemoryEvidenceError("receipt_timestamp_invalid")
    return captured, completed


def _validate_outcomes(value: Any, operations: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict) or set(value) != set(AGENT_GROUPS):
        raise MemoryEvidenceError("receipt_agent_groups_invalid")
    expected = {*operations, "residual_count"}
    normalized: dict[str, dict[str, Any]] = {}
    for group in AGENT_GROUPS:
        outcome = value.get(group)
        if (
            not isinstance(outcome, dict)
            or set(outcome) != expected
            or outcome.get("residual_count") != 0
            or any(outcome.get(operation) is not True for operation in operations)
        ):
            raise MemoryEvidenceError("receipt_operation_outcome_invalid")
        normalized[group] = {operation: True for operation in operations}
        normalized[group]["residual_count"] = 0
    return normalized


def _validate_postgres_receipt(receipt: Mapping[str, Any], *, now: int) -> tuple[int, int]:
    if set(receipt) != {
        "agent_groups",
        "backend",
        "captured_at_unix",
        "completed_at_unix",
        "executor",
        "probe_sha256",
        "residual_count",
        "schema_version",
    }:
        raise MemoryEvidenceError("postgres_receipt_schema_invalid")
    if (
        receipt.get("schema_version") != POSTGRES_RECEIPT_SCHEMA
        or receipt.get("backend") != "postgresql"
        or receipt.get("executor") != EXECUTOR
        or receipt.get("residual_count") != 0
        or not SHA256_RE.fullmatch(str(receipt.get("probe_sha256") or ""))
    ):
        raise MemoryEvidenceError("postgres_receipt_schema_invalid")
    _validate_outcomes(receipt.get("agent_groups"), POSTGRES_OPERATIONS)
    return _validate_time_window(receipt, now=now)


def _validate_milvus_receipt(receipt: Mapping[str, Any], *, now: int) -> tuple[int, int]:
    if set(receipt) != {
        "agent_groups",
        "backend",
        "captured_at_unix",
        "completed_at_unix",
        "executor",
        "logical_alias",
        "physical_collection",
        "probe_sha256",
        "required_schema_version",
        "residual_count",
        "schema_preflight_passed",
        "schema_version",
    }:
        raise MemoryEvidenceError("milvus_receipt_schema_invalid")
    if (
        receipt.get("schema_version") != MILVUS_RECEIPT_SCHEMA
        or receipt.get("backend") != "milvus"
        or receipt.get("executor") != EXECUTOR
        or receipt.get("logical_alias") != LOGICAL_ALIAS
        or receipt.get("required_schema_version") != REQUIRED_COLLECTION_SCHEMA
        or receipt.get("schema_preflight_passed") is not True
        or receipt.get("residual_count") != 0
        or not SHA256_RE.fullmatch(str(receipt.get("probe_sha256") or ""))
        or not PHYSICAL_COLLECTION_RE.fullmatch(str(receipt.get("physical_collection") or ""))
    ):
        raise MemoryEvidenceError("milvus_receipt_schema_invalid")
    _validate_outcomes(receipt.get("agent_groups"), MILVUS_OPERATIONS)
    return _validate_time_window(receipt, now=now)


def _validate_boundary(payload: Mapping[str, Any]) -> None:
    if payload != EXPECTED_BOUNDARY:
        raise MemoryEvidenceError("memory_boundary_contract_invalid")


def _validate_schema_contract(payload: Mapping[str, Any]) -> None:
    properties = payload.get("properties")
    schema_version = properties.get("schema_version") if isinstance(properties, dict) else None
    if (
        payload.get("$schema") != "https://json-schema.org/draft/2020-12/schema"
        or payload.get("type") != "object"
        or payload.get("additionalProperties") is not False
        or not isinstance(schema_version, dict)
        or schema_version.get("const") != SCHEMA_VERSION
    ):
        raise MemoryEvidenceError("evidence_schema_contract_invalid")


def build_evidence(*, project_root: Path, now: int | None = None) -> dict[str, Any]:
    root = project_root.expanduser().resolve(strict=True)
    current = int(time.time()) if now is None else now
    if not _integer(current) or current <= 0:
        raise MemoryEvidenceError("current_time_invalid")

    boundary_bytes = _safe_read(root, BOUNDARY_RELATIVE, private=False)
    schema_bytes = _safe_read(root, EVIDENCE_SCHEMA_RELATIVE, private=False)
    postgres_bytes = _safe_read(root, POSTGRES_RECEIPT_RELATIVE, private=True)
    milvus_bytes = _safe_read(root, MILVUS_RECEIPT_RELATIVE, private=True)
    boundary = _parse_json(boundary_bytes)
    schema = _parse_json(schema_bytes)
    postgres = _parse_json(postgres_bytes)
    milvus = _parse_json(milvus_bytes)
    _validate_boundary(boundary)
    _validate_schema_contract(schema)
    postgres_window = _validate_postgres_receipt(postgres, now=current)
    milvus_window = _validate_milvus_receipt(milvus, now=current)
    started = min(postgres_window[0], milvus_window[0])
    completed = max(postgres_window[1], milvus_window[1])
    if completed - started > MAX_COMBINED_WINDOW_SECONDS:
        raise MemoryEvidenceError("combined_probe_window_invalid")

    postgres_outcomes = _validate_outcomes(postgres["agent_groups"], POSTGRES_OPERATIONS)
    milvus_outcomes = _validate_outcomes(milvus["agent_groups"], MILVUS_OPERATIONS)
    evidence = {
        "schema_version": SCHEMA_VERSION,
        "decision": "GO",
        "passed": True,
        "evidence_window": {
            "started_at_unix": started,
            "completed_at_unix": completed,
        },
        "contract_binding": {
            "boundary_contract_sha256": _sha256(boundary_bytes),
            "evidence_schema_sha256": _sha256(schema_bytes),
            "executor": EXECUTOR,
            "logical_alias": LOGICAL_ALIAS,
            "physical_collection_sha256": _sha256(str(milvus["physical_collection"]).encode("ascii")),
            "required_schema_version": REQUIRED_COLLECTION_SCHEMA,
            "sandbox_direct_milvus": False,
        },
        "agent_groups": list(AGENT_GROUPS),
        "backends": {
            "postgresql": {
                "captured_at_unix": postgres_window[0],
                "completed_at_unix": postgres_window[1],
                "probe_sha256": postgres["probe_sha256"],
                "receipt_sha256": _sha256(postgres_bytes),
                "operation_outcomes": postgres_outcomes,
                "residual_count": 0,
            },
            "milvus": {
                "captured_at_unix": milvus_window[0],
                "completed_at_unix": milvus_window[1],
                "probe_sha256": milvus["probe_sha256"],
                "receipt_sha256": _sha256(milvus_bytes),
                "schema_preflight_passed": True,
                "operation_outcomes": milvus_outcomes,
                "residual_count": 0,
            },
        },
    }
    _validate_evidence_payload(
        evidence,
        boundary_sha256=_sha256(boundary_bytes),
        schema_sha256=_sha256(schema_bytes),
        now=current,
    )
    return evidence


def _validate_backend_evidence(
    backend: Any,
    *,
    operations: tuple[str, ...],
    now: int,
    milvus: bool,
) -> tuple[int, int]:
    expected = {
        "captured_at_unix",
        "completed_at_unix",
        "operation_outcomes",
        "probe_sha256",
        "receipt_sha256",
        "residual_count",
    }
    if milvus:
        expected.add("schema_preflight_passed")
    if (
        not isinstance(backend, dict)
        or set(backend) != expected
        or backend.get("residual_count") != 0
        or not SHA256_RE.fullmatch(str(backend.get("probe_sha256") or ""))
        or not SHA256_RE.fullmatch(str(backend.get("receipt_sha256") or ""))
        or (milvus and backend.get("schema_preflight_passed") is not True)
    ):
        raise MemoryEvidenceError("evidence_backend_invalid")
    _validate_outcomes(backend.get("operation_outcomes"), operations)
    return _validate_time_window(backend, now=now)


def _validate_evidence_payload(
    payload: Mapping[str, Any],
    *,
    boundary_sha256: str,
    schema_sha256: str,
    now: int,
) -> None:
    if set(payload) != {
        "agent_groups",
        "backends",
        "contract_binding",
        "decision",
        "evidence_window",
        "passed",
        "schema_version",
    }:
        raise MemoryEvidenceError("evidence_schema_invalid")
    window = payload.get("evidence_window")
    binding = payload.get("contract_binding")
    backends = payload.get("backends")
    if (
        payload.get("schema_version") != SCHEMA_VERSION
        or payload.get("decision") != "GO"
        or payload.get("passed") is not True
        or payload.get("agent_groups") != list(AGENT_GROUPS)
        or not isinstance(window, dict)
        or set(window) != {"completed_at_unix", "started_at_unix"}
        or not isinstance(binding, dict)
        or set(binding)
        != {
            "boundary_contract_sha256",
            "evidence_schema_sha256",
            "executor",
            "logical_alias",
            "physical_collection_sha256",
            "required_schema_version",
            "sandbox_direct_milvus",
        }
        or binding.get("boundary_contract_sha256") != boundary_sha256
        or binding.get("evidence_schema_sha256") != schema_sha256
        or binding.get("executor") != EXECUTOR
        or binding.get("logical_alias") != LOGICAL_ALIAS
        or not SHA256_RE.fullmatch(str(binding.get("physical_collection_sha256") or ""))
        or binding.get("required_schema_version") != REQUIRED_COLLECTION_SCHEMA
        or binding.get("sandbox_direct_milvus") is not False
        or not isinstance(backends, dict)
        or set(backends) != {"milvus", "postgresql"}
    ):
        raise MemoryEvidenceError("evidence_schema_invalid")
    postgres_window = _validate_backend_evidence(
        backends["postgresql"], operations=POSTGRES_OPERATIONS, now=now, milvus=False
    )
    milvus_window = _validate_backend_evidence(backends["milvus"], operations=MILVUS_OPERATIONS, now=now, milvus=True)
    started = min(postgres_window[0], milvus_window[0])
    completed = max(postgres_window[1], milvus_window[1])
    if (
        completed - started > MAX_COMBINED_WINDOW_SECONDS
        or window.get("started_at_unix") != started
        or window.get("completed_at_unix") != completed
    ):
        raise MemoryEvidenceError("evidence_window_invalid")


def validate_consumable_evidence(
    path: Path,
    *,
    project_root: Path = REPO_ROOT,
    now: int | None = None,
) -> dict[str, Any]:
    """Validate a published evidence file against current repository contracts."""

    root = project_root.expanduser().resolve(strict=True)
    candidate = path.expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    expected = root / OUTPUT_JSON_RELATIVE
    if Path(os.path.abspath(candidate)) != expected:
        raise MemoryEvidenceError("evidence_path_invalid")
    current = int(time.time()) if now is None else now
    if not _integer(current) or current <= 0:
        raise MemoryEvidenceError("current_time_invalid")

    evidence_bytes = _safe_read(root, OUTPUT_JSON_RELATIVE, private=True)
    boundary_bytes = _safe_read(root, BOUNDARY_RELATIVE, private=False)
    schema_bytes = _safe_read(root, EVIDENCE_SCHEMA_RELATIVE, private=False)
    _validate_boundary(_parse_json(boundary_bytes))
    _validate_schema_contract(_parse_json(schema_bytes))
    payload = _parse_json(evidence_bytes)
    _validate_evidence_payload(
        payload,
        boundary_sha256=_sha256(boundary_bytes),
        schema_sha256=_sha256(schema_bytes),
        now=current,
    )
    if check_sanitized_artifacts.scan_content(expected, evidence_bytes):
        raise MemoryEvidenceError("evidence_sanitization_failed")
    return payload


def _markdown(evidence: Mapping[str, Any]) -> bytes:
    binding = evidence["contract_binding"]
    lines = [
        "# Host Memory Write Evidence",
        "",
        "- Decision: `GO`",
        f"- Logical Milvus alias: `{binding['logical_alias']}`",
        f"- Physical collection digest: `{binding['physical_collection_sha256']}`",
        "- Agent groups: `primary_market`, `secondary_market`",
        "- PostgreSQL: `insert / readback / rollback / post-rollback verify` passed",
        "- Milvus: `upsert / get / search / delete / post-delete verify` passed",
        "- PostgreSQL residual count: `0`",
        "- Milvus residual count: `0`",
        "- Sandbox direct memory writes: `disabled`",
        "",
        "The evidence contains only operation outcomes, timestamps and SHA-256 bindings.",
        "Record content, runtime identifiers, endpoints and credentials are excluded.",
        "",
    ]
    return "\n".join(lines).encode("ascii")


def _check_existing_output(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
        raise MemoryEvidenceError("evidence_output_unsafe")


def _write_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _check_existing_output(path)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if stat.S_IMODE(path.stat().st_mode) != 0o600:
            raise MemoryEvidenceError("evidence_output_mode_invalid")
    except MemoryEvidenceError:
        raise
    except OSError as exc:
        raise MemoryEvidenceError("evidence_output_failed") from exc
    finally:
        temporary.unlink(missing_ok=True)


def publish_evidence(*, project_root: Path, evidence: Mapping[str, Any]) -> tuple[Path, Path]:
    root = project_root.expanduser().resolve(strict=True)
    json_path = root / OUTPUT_JSON_RELATIVE
    markdown_path = root / OUTPUT_MARKDOWN_RELATIVE
    json_content = json.dumps(evidence, ensure_ascii=True, indent=2, sort_keys=True).encode("ascii") + b"\n"
    markdown_content = _markdown(evidence)
    preflight_findings = check_sanitized_artifacts.scan_content(json_path, json_content)
    preflight_findings.extend(check_sanitized_artifacts.scan_content(markdown_path, markdown_content))
    if preflight_findings:
        raise MemoryEvidenceError("evidence_sanitization_failed")
    _write_atomic(json_path, json_content)
    try:
        _write_atomic(markdown_path, markdown_content)
        if check_sanitized_artifacts.scan_paths([json_path, markdown_path]):
            raise MemoryEvidenceError("evidence_sanitization_failed")
    except Exception:
        json_path.unlink(missing_ok=True)
        markdown_path.unlink(missing_ok=True)
        raise
    return json_path, markdown_path


def build_and_publish(*, project_root: Path, now: int | None = None) -> dict[str, Any]:
    evidence = build_evidence(project_root=project_root, now=now)
    publish_evidence(project_root=project_root, evidence=evidence)
    return evidence


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=REPO_ROOT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        evidence = build_and_publish(project_root=args.project_root)
    except (MemoryEvidenceError, OSError, ValueError) as exc:
        code = str(exc) if isinstance(exc, MemoryEvidenceError) else "memory_write_evidence_failed"
        print(json.dumps({"ok": False, "decision": "NO_GO", "error_code": code}, sort_keys=True))
        return 1
    print(
        json.dumps(
            {
                "ok": True,
                "decision": evidence["decision"],
                "schema_version": evidence["schema_version"],
                "evidence": OUTPUT_JSON_RELATIVE.as_posix(),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
