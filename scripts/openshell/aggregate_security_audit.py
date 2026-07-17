#!/usr/bin/env python3
"""Aggregate explicitly selected SIQ OpenShell audit JSONL files."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

try:
    from scripts.openshell import security_audit
except ModuleNotFoundError:  # direct execution from scripts/openshell
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scripts.openshell import security_audit


SCHEMA_VERSION = "siq.openshell.audit-summary.v1"
MAX_INPUT_BYTES = 64 * 1024 * 1024
MAX_INPUT_FILES = 256
MAX_RECORDS = 1_000_000
SESSION_PROJECTION_RE = re.compile(r"[0-9a-f]{24}\Z")
TARGET_PROJECTION_RE = re.compile(r"(?:none|[0-9a-f]{24})\Z")
UPLOAD_BLOCK_CODES = frozenset(
    {
        "approved_destination_rule_mismatch",
        "blocked_transfer_client",
        "blocked_transfer_scheme",
        "broker_method_denied",
        "broker_multipart_denied",
        "broker_octet_stream_denied",
        "json_body_too_large",
        "unknown_body_size",
        "unknown_body_too_large",
        "unknown_multipart_upload",
        "unknown_non_json_post",
        "unknown_octet_stream_upload",
        "unknown_put_upload",
    }
)
FORMAL_TRANSFER_SCOPES = frozenset(
    {
        "formal_egress.curl_upload",
        "formal_egress.rclone",
        "formal_egress.rsync",
        "formal_egress.scp",
        "formal_egress.sftp",
    }
)
RECORD_FIELDS = frozenset(
    {
        "schema_version",
        "timestamp",
        "profile",
        "sandbox_id",
        "siq_run_id",
        "session_projection",
        "operation_class",
        "target",
        "decision",
        "policy_digest",
        "error_code",
        "duration_ms",
    }
)


class AuditAggregationError(RuntimeError):
    """Stable audit input/output error that never includes record content."""


def _safe_explicit_file(path: Path, *, suffix: str | None = None) -> Path:
    candidate = path.expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    current = Path(candidate.anchor)
    for part in candidate.parts[1:]:
        current /= part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError as exc:
            raise AuditAggregationError("audit_input_missing") from exc
        if stat.S_ISLNK(mode):
            raise AuditAggregationError("audit_input_symlink_not_allowed")
    info = candidate.stat()
    if not stat.S_ISREG(info.st_mode):
        raise AuditAggregationError("audit_input_regular_file_required")
    if info.st_size > MAX_INPUT_BYTES:
        raise AuditAggregationError("audit_input_too_large")
    if suffix and candidate.suffix.lower() != suffix:
        raise AuditAggregationError("audit_input_suffix_invalid")
    return candidate


def _parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise AuditAggregationError("audit_record_timestamp_invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AuditAggregationError("audit_record_timestamp_invalid") from exc
    if parsed.utcoffset() is None:
        raise AuditAggregationError("audit_record_timestamp_invalid")
    return parsed


def validate_record(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != RECORD_FIELDS:
        raise AuditAggregationError("audit_record_fields_invalid")
    if payload.get("schema_version") != security_audit.SCHEMA_VERSION:
        raise AuditAggregationError("audit_record_schema_version_invalid")
    _parse_timestamp(payload.get("timestamp"))
    for key in ("profile", "sandbox_id", "siq_run_id"):
        value = payload.get(key)
        if not isinstance(value, str) or not security_audit.SAFE_ID_RE.fullmatch(value):
            raise AuditAggregationError(f"audit_record_{key}_invalid")
    session_projection = payload.get("session_projection")
    if not isinstance(session_projection, str) or not SESSION_PROJECTION_RE.fullmatch(session_projection):
        raise AuditAggregationError("audit_record_session_projection_invalid")
    if payload.get("operation_class") not in security_audit.OPERATION_CLASSES:
        raise AuditAggregationError("audit_record_operation_class_invalid")
    if payload.get("decision") not in security_audit.DECISIONS:
        raise AuditAggregationError("audit_record_decision_invalid")
    digest = payload.get("policy_digest")
    if not isinstance(digest, str) or not security_audit.SHA256_RE.fullmatch(digest):
        raise AuditAggregationError("audit_record_policy_digest_invalid")
    error_code = payload.get("error_code")
    if not isinstance(error_code, str) or (error_code and not security_audit.ERROR_CODE_RE.fullmatch(error_code)):
        raise AuditAggregationError("audit_record_error_code_invalid")
    duration_ms = payload.get("duration_ms")
    if isinstance(duration_ms, bool) or not isinstance(duration_ms, int) or not 0 <= duration_ms <= 86_400_000:
        raise AuditAggregationError("audit_record_duration_invalid")
    target = payload.get("target")
    if not isinstance(target, dict) or set(target) != {"kind", "scope", "projection"}:
        raise AuditAggregationError("audit_record_target_invalid")
    if target.get("kind") not in security_audit.TARGET_KINDS:
        raise AuditAggregationError("audit_record_target_invalid")
    scope = target.get("scope")
    projection = target.get("projection")
    if not isinstance(scope, str) or not security_audit.SAFE_ID_RE.fullmatch(scope):
        raise AuditAggregationError("audit_record_target_invalid")
    if not isinstance(projection, str) or not TARGET_PROJECTION_RE.fullmatch(projection):
        raise AuditAggregationError("audit_record_target_invalid")
    if (target["kind"] == "none") != (projection == "none"):
        raise AuditAggregationError("audit_record_target_invalid")
    try:
        security_audit.serialize_record(payload)
    except security_audit.SecurityAuditError as exc:
        raise AuditAggregationError("audit_record_serialization_invalid") from exc
    return dict(payload)


def load_records(paths: Iterable[Path]) -> tuple[list[dict[str, Any]], list[str]]:
    requested = list(paths)
    if not requested or len(requested) > MAX_INPUT_FILES:
        raise AuditAggregationError("audit_input_count_invalid")
    records: list[dict[str, Any]] = []
    source_digests: list[str] = []
    seen_files: set[Path] = set()
    for requested_path in requested:
        path = _safe_explicit_file(requested_path, suffix=".jsonl")
        resolved = path.resolve(strict=True)
        if resolved in seen_files:
            raise AuditAggregationError("audit_input_duplicate")
        seen_files.add(resolved)
        content = path.read_bytes()
        source_digests.append(hashlib.sha256(content).hexdigest())
        for line_number, raw_line in enumerate(content.splitlines(), start=1):
            if not raw_line.strip():
                continue
            if len(records) >= MAX_RECORDS:
                raise AuditAggregationError("audit_record_count_exceeded")
            try:
                payload = json.loads(raw_line)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise AuditAggregationError(f"audit_record_json_invalid_line_{line_number}") from exc
            try:
                records.append(validate_record(payload))
            except AuditAggregationError as exc:
                raise AuditAggregationError(f"{exc}_line_{line_number}") from exc
    if not records:
        raise AuditAggregationError("audit_records_empty")
    return records, source_digests


def percentile(values: Iterable[int], quantile: float) -> float | int | None:
    ordered = sorted(values)
    if not ordered:
        return None
    if not 0 <= quantile <= 1:
        raise ValueError("quantile must be between zero and one")
    position = (len(ordered) - 1) * quantile
    lower_index = int(position)
    upper_index = min(lower_index + 1, len(ordered) - 1)
    fraction = position - lower_index
    result = ordered[lower_index] + (ordered[upper_index] - ordered[lower_index]) * fraction
    rounded = round(result, 3)
    return int(rounded) if rounded.is_integer() else rounded


def _is_sandbox_start_failure(record: Mapping[str, Any]) -> bool:
    if record["operation_class"] != "sandbox.lifecycle" or record["decision"] != "deny":
        return False
    scope = str(record["target"]["scope"])
    error_code = str(record["error_code"])
    return scope in {"sandbox.start", "sandbox_start"} or error_code.startswith(("gateway_start_", "sandbox_start_"))


def _is_tool_operation(record: Mapping[str, Any]) -> bool:
    scope = str(record["target"]["scope"])
    error_code = str(record["error_code"])
    return scope == "tool" or scope.startswith("tool.") or error_code.startswith("tool_")


def _is_external_upload_block(record: Mapping[str, Any]) -> bool:
    if record["operation_class"] != "network.request" or record["decision"] != "deny":
        return False
    error_code = str(record["error_code"])
    scope = str(record["target"]["scope"])
    return (
        error_code in UPLOAD_BLOCK_CODES
        or scope in FORMAL_TRANSFER_SCOPES
        or "upload" in error_code
        or "transfer" in error_code
    )


def aggregate_records(
    records: Iterable[Mapping[str, Any]],
    *,
    source_digests: Iterable[str],
) -> dict[str, Any]:
    materialized = [validate_record(dict(record)) for record in records]
    if not materialized:
        raise AuditAggregationError("audit_records_empty")
    digests = sorted(source_digests)
    if not digests or any(not security_audit.SHA256_RE.fullmatch(item) for item in digests):
        raise AuditAggregationError("audit_source_digest_invalid")

    decision_counts = Counter(str(record["decision"]) for record in materialized)
    operation_counts = Counter(str(record["operation_class"]) for record in materialized)
    profile_counts = Counter(str(record["profile"]) for record in materialized)
    policy_digests = Counter(str(record["policy_digest"]) for record in materialized)
    deny_codes = Counter(
        str(record["error_code"] or "unspecified") for record in materialized if record["decision"] == "deny"
    )
    tool_records = [record for record in materialized if _is_tool_operation(record)]
    tool_failures = [record for record in tool_records if record["decision"] == "deny" or bool(record["error_code"])]
    gateway_durations = [
        int(record["duration_ms"]) for record in materialized if record["operation_class"] == "runtime.route"
    ]
    timestamps = sorted(str(record["timestamp"]) for record in materialized)
    tool_rate = len(tool_failures) / len(tool_records) if tool_records else 0.0
    return {
        "schema_version": SCHEMA_VERSION,
        "source_schema_version": security_audit.SCHEMA_VERSION,
        "source_file_count": len(digests),
        "source_sha256": digests,
        "record_count": len(materialized),
        "period": {"start": timestamps[0], "end": timestamps[-1]},
        "profiles": dict(sorted(profile_counts.items())),
        "policy_digests": dict(sorted(policy_digests.items())),
        "decisions": {decision: decision_counts.get(decision, 0) for decision in sorted(security_audit.DECISIONS)},
        "operation_classes": {
            operation: operation_counts.get(operation, 0) for operation in sorted(security_audit.OPERATION_CLASSES)
        },
        "deny_error_codes": dict(sorted(deny_codes.items())),
        "metrics": {
            "policy_deny_count": decision_counts.get("deny", 0),
            "audit_only_count": decision_counts.get("audit_only", 0),
            "sandbox_start_failures": sum(_is_sandbox_start_failure(record) for record in materialized),
            "tool_operation_count": len(tool_records),
            "tool_failure_count": len(tool_failures),
            "tool_failure_rate": round(tool_rate, 6),
            "external_upload_blocks": sum(_is_external_upload_block(record) for record in materialized),
            "immutable_write_blocks": sum(
                record["operation_class"] == "immutable.write" and record["decision"] == "deny"
                for record in materialized
            ),
            "gateway_overhead_ms": {
                "sample_count": len(gateway_durations),
                "p50": percentile(gateway_durations, 0.50),
                "p95": percentile(gateway_durations, 0.95),
            },
        },
    }


def _safe_output_file(path: Path) -> Path:
    candidate = path.expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    parent = candidate.parent
    current = Path(parent.anchor)
    for part in parent.parts[1:]:
        current /= part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError as exc:
            raise AuditAggregationError("audit_output_parent_invalid") from exc
        if stat.S_ISLNK(mode):
            raise AuditAggregationError("audit_output_symlink_not_allowed")
    if not parent.is_dir():
        raise AuditAggregationError("audit_output_parent_invalid")
    if candidate.exists() and (candidate.is_symlink() or not candidate.is_file()):
        raise AuditAggregationError("audit_output_file_invalid")
    return candidate


def write_summary(path: Path, summary: Mapping[str, Any]) -> Path:
    output = _safe_output_file(path)
    content = (json.dumps(summary, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode()
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return output


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, action="append", required=True, help="explicit audit JSONL file")
    parser.add_argument("--output", type=Path, required=True, help="explicit aggregate JSON output")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        records, digests = load_records(args.input)
        write_summary(args.output, aggregate_records(records, source_digests=digests))
        print(json.dumps({"ok": True, "schema_version": SCHEMA_VERSION}, sort_keys=True))
        return 0
    except (AuditAggregationError, OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error_code": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
