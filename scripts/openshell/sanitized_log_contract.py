#!/usr/bin/env python3
"""Strict, deterministic contract for publishable OpenShell log bundles."""

from __future__ import annotations

import json
import math
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping

try:
    from scripts.openshell import aggregate_security_audit, security_audit
except ModuleNotFoundError:  # direct execution from scripts/openshell
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scripts.openshell import aggregate_security_audit, security_audit


SCHEMA_VERSION = "siq.openshell.sanitized-log-bundle.v1"
MAX_BUNDLE_BYTES = 8 * 1024 * 1024
MAX_OPERATIONAL_LOGS = 64
MAX_OPERATIONAL_LOG_BYTES = 64 * 1024 * 1024
MAX_RECORDS = aggregate_security_audit.MAX_RECORDS
COMPONENT_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")
UTC_TIMESTAMP_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z\Z"
)
SEVERITIES = ("critical", "error", "warning", "info", "debug", "unclassified")


class SanitizedLogContractError(ValueError):
    """A stable validation failure that never contains bundle content."""


def _fail(code: str) -> None:
    raise SanitizedLogContractError(code)


def _exact_dict(value: Any, keys: set[str], code: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        _fail(code)
    return value


def _count(value: Any, code: str, *, maximum: int = MAX_RECORDS) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        _fail(code)
    return value


def _positive_count(value: Any, code: str, *, maximum: int = MAX_RECORDS) -> int:
    result = _count(value, code, maximum=maximum)
    if result == 0:
        _fail(code)
    return result


def _finite_number(value: Any, code: str) -> float | int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        _fail(code)
    return value


def _timestamp(value: Any, code: str) -> datetime:
    if not isinstance(value, str) or not UTC_TIMESTAMP_RE.fullmatch(value):
        _fail(code)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail(code)
    if parsed.utcoffset() is None:
        _fail(code)
    return parsed


def _count_map(
    value: Any,
    *,
    code: str,
    key_validator: Callable[[str], bool],
    exact_keys: set[str] | None = None,
) -> tuple[dict[str, int], int]:
    if type(value) is not dict:
        _fail(code)
    if exact_keys is not None and set(value) != exact_keys:
        _fail(code)
    result: dict[str, int] = {}
    total = 0
    for key, raw_count in value.items():
        if not isinstance(key, str) or not key_validator(key):
            _fail(code)
        count = _count(raw_count, code)
        result[key] = count
        total += count
        if total > MAX_RECORDS:
            _fail(code)
    return result, total


def _validate_structured_audit(value: Any) -> dict[str, Any]:
    audit = _exact_dict(
        value,
        {
            "schema_version",
            "source_schema_version",
            "source_file_count",
            "source_sha256",
            "record_count",
            "period",
            "profiles",
            "policy_digests",
            "decisions",
            "operation_classes",
            "deny_error_codes",
            "metrics",
        },
        "structured_audit_fields_invalid",
    )
    if audit["schema_version"] != aggregate_security_audit.SCHEMA_VERSION:
        _fail("structured_audit_schema_invalid")
    if audit["source_schema_version"] != security_audit.SCHEMA_VERSION:
        _fail("structured_audit_source_schema_invalid")

    source_file_count = _positive_count(
        audit["source_file_count"],
        "structured_audit_source_count_invalid",
        maximum=aggregate_security_audit.MAX_INPUT_FILES,
    )
    source_digests = audit["source_sha256"]
    if (
        type(source_digests) is not list
        or len(source_digests) != source_file_count
        or source_digests != sorted(source_digests)
        or any(not isinstance(item, str) or not security_audit.SHA256_RE.fullmatch(item) for item in source_digests)
    ):
        _fail("structured_audit_source_digest_invalid")

    record_count = _positive_count(audit["record_count"], "structured_audit_record_count_invalid")
    period = _exact_dict(audit["period"], {"start", "end"}, "structured_audit_period_fields_invalid")
    if _timestamp(period["start"], "structured_audit_period_invalid") > _timestamp(
        period["end"], "structured_audit_period_invalid"
    ):
        _fail("structured_audit_period_invalid")

    _, profile_total = _count_map(
        audit["profiles"],
        code="structured_audit_profiles_invalid",
        key_validator=lambda key: bool(security_audit.SAFE_ID_RE.fullmatch(key)),
    )
    _, policy_total = _count_map(
        audit["policy_digests"],
        code="structured_audit_policy_digests_invalid",
        key_validator=lambda key: bool(security_audit.SHA256_RE.fullmatch(key)),
    )
    decisions, decision_total = _count_map(
        audit["decisions"],
        code="structured_audit_decisions_invalid",
        key_validator=lambda key: key in security_audit.DECISIONS,
        exact_keys=set(security_audit.DECISIONS),
    )
    operations, operation_total = _count_map(
        audit["operation_classes"],
        code="structured_audit_operations_invalid",
        key_validator=lambda key: key in security_audit.OPERATION_CLASSES,
        exact_keys=set(security_audit.OPERATION_CLASSES),
    )
    _, deny_total = _count_map(
        audit["deny_error_codes"],
        code="structured_audit_deny_codes_invalid",
        key_validator=lambda key: key == "unspecified" or bool(security_audit.ERROR_CODE_RE.fullmatch(key)),
    )
    if any(total != record_count for total in (profile_total, policy_total, decision_total, operation_total)):
        _fail("structured_audit_count_mismatch")
    if deny_total != decisions["deny"]:
        _fail("structured_audit_deny_count_mismatch")

    metrics = _exact_dict(
        audit["metrics"],
        {
            "policy_deny_count",
            "audit_only_count",
            "sandbox_start_failures",
            "tool_operation_count",
            "tool_failure_count",
            "tool_failure_rate",
            "external_upload_blocks",
            "immutable_write_blocks",
            "gateway_overhead_ms",
        },
        "structured_audit_metrics_fields_invalid",
    )
    policy_denies = _count(metrics["policy_deny_count"], "structured_audit_metric_invalid")
    audit_only = _count(metrics["audit_only_count"], "structured_audit_metric_invalid")
    sandbox_failures = _count(metrics["sandbox_start_failures"], "structured_audit_metric_invalid")
    tool_operations = _count(metrics["tool_operation_count"], "structured_audit_metric_invalid")
    tool_failures = _count(metrics["tool_failure_count"], "structured_audit_metric_invalid")
    external_blocks = _count(metrics["external_upload_blocks"], "structured_audit_metric_invalid")
    immutable_blocks = _count(metrics["immutable_write_blocks"], "structured_audit_metric_invalid")
    rate = metrics["tool_failure_rate"]
    if type(rate) is not float or not math.isfinite(rate) or not 0.0 <= rate <= 1.0:
        _fail("structured_audit_tool_rate_invalid")
    expected_rate = round(tool_failures / tool_operations, 6) if tool_operations else 0.0
    if rate != expected_rate:
        _fail("structured_audit_tool_rate_invalid")
    if (
        policy_denies != decisions["deny"]
        or audit_only != decisions["audit_only"]
        or tool_failures > tool_operations
        or any(value > decisions["deny"] for value in (sandbox_failures, external_blocks, immutable_blocks))
    ):
        _fail("structured_audit_metric_mismatch")

    overhead = _exact_dict(
        metrics["gateway_overhead_ms"],
        {"sample_count", "p50", "p95"},
        "structured_audit_overhead_fields_invalid",
    )
    sample_count = _count(overhead["sample_count"], "structured_audit_overhead_invalid")
    if sample_count != operations["runtime.route"]:
        _fail("structured_audit_overhead_count_mismatch")
    p50 = overhead["p50"]
    p95 = overhead["p95"]
    if sample_count == 0:
        if p50 is not None or p95 is not None:
            _fail("structured_audit_overhead_invalid")
    else:
        p50_number = _finite_number(p50, "structured_audit_overhead_invalid")
        p95_number = _finite_number(p95, "structured_audit_overhead_invalid")
        if not 0 <= p50_number <= p95_number <= 86_400_000:
            _fail("structured_audit_overhead_invalid")
    return audit


def validate_bundle(value: Any) -> dict[str, Any]:
    bundle = _exact_dict(
        value,
        {"schema_version", "generated_at", "source_contract", "structured_audit", "operational_logs"},
        "bundle_fields_invalid",
    )
    if bundle["schema_version"] != SCHEMA_VERSION:
        _fail("bundle_schema_invalid")
    _timestamp(bundle["generated_at"], "bundle_generated_at_invalid")

    source = _exact_dict(
        bundle["source_contract"],
        {
            "audit_record_schema_version",
            "audit_summary_schema_version",
            "operational_log_count",
            "raw_log_messages_included",
            "raw_log_paths_included",
            "source_file_names_included",
        },
        "source_contract_fields_invalid",
    )
    if (
        source["audit_record_schema_version"] != security_audit.SCHEMA_VERSION
        or source["audit_summary_schema_version"] != aggregate_security_audit.SCHEMA_VERSION
        or source["raw_log_messages_included"] is not False
        or source["raw_log_paths_included"] is not False
        or source["source_file_names_included"] is not False
    ):
        _fail("source_contract_invalid")

    _validate_structured_audit(bundle["structured_audit"])
    logs = bundle["operational_logs"]
    if type(logs) is not list or len(logs) > MAX_OPERATIONAL_LOGS:
        _fail("operational_logs_invalid")
    if _count(
        source["operational_log_count"],
        "source_contract_log_count_invalid",
        maximum=MAX_OPERATIONAL_LOGS,
    ) != len(logs):
        _fail("source_contract_log_count_mismatch")

    components: list[str] = []
    for item in logs:
        log = _exact_dict(
            item,
            {"component", "byte_count", "line_count", "severity_counts", "sha256"},
            "operational_log_fields_invalid",
        )
        component = log["component"]
        if not isinstance(component, str) or not COMPONENT_RE.fullmatch(component):
            _fail("operational_log_component_invalid")
        components.append(component)
        byte_count = _count(
            log["byte_count"], "operational_log_byte_count_invalid", maximum=MAX_OPERATIONAL_LOG_BYTES
        )
        line_count = _count(
            log["line_count"], "operational_log_line_count_invalid", maximum=MAX_OPERATIONAL_LOG_BYTES + 1
        )
        _, severity_total = _count_map(
            log["severity_counts"],
            code="operational_log_severity_invalid",
            key_validator=lambda key: key in SEVERITIES,
            exact_keys=set(SEVERITIES),
        )
        if severity_total != line_count or (byte_count == 0) != (line_count == 0):
            _fail("operational_log_count_mismatch")
        digest = log["sha256"]
        if not isinstance(digest, str) or not security_audit.SHA256_RE.fullmatch(digest):
            _fail("operational_log_digest_invalid")
    if components != sorted(components) or len(components) != len(set(components)):
        _fail("operational_log_components_invalid")
    return bundle


def canonical_json_bytes(bundle: Mapping[str, Any]) -> bytes:
    validated = validate_bundle(bundle)
    return (json.dumps(validated, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode("ascii")


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("bundle_duplicate_key")
        result[key] = value
    return result


def load_canonical_bundle(content: bytes) -> dict[str, Any]:
    if len(content) > MAX_BUNDLE_BYTES:
        _fail("bundle_too_large")
    try:
        text = content.decode("ascii")
    except UnicodeDecodeError:
        _fail("bundle_ascii_required")
    try:
        payload = json.loads(
            text,
            object_pairs_hook=_strict_object,
            parse_constant=lambda _value: _fail("bundle_number_invalid"),
        )
    except SanitizedLogContractError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError):
        _fail("bundle_json_invalid")
    validated = validate_bundle(payload)
    if content != canonical_json_bytes(validated):
        _fail("bundle_not_canonical")
    return validated


def render_markdown(bundle: Mapping[str, Any]) -> str:
    validated = validate_bundle(bundle)
    audit = validated["structured_audit"]
    metrics = audit["metrics"]
    overhead = metrics["gateway_overhead_ms"]
    lines = [
        "# SIQ OpenShell Sanitized Log Bundle",
        "",
        f"- Schema version: `{validated['schema_version']}`",
        f"- Generated at: `{validated['generated_at']}`",
        f"- Structured audit records: `{audit['record_count']}`",
        f"- Structured audit source files: `{audit['source_file_count']}`",
        "- Raw operational messages included: `false`",
        "- Raw log paths included: `false`",
        f"- Policy denies: `{metrics['policy_deny_count']}`",
        f"- Audit-only decisions: `{metrics['audit_only_count']}`",
        f"- External upload blocks: `{metrics['external_upload_blocks']}`",
        f"- Immutable write blocks: `{metrics['immutable_write_blocks']}`",
        f"- Gateway overhead P50/P95 ms: `{overhead['p50']}` / `{overhead['p95']}`",
        "",
        "## Operational Log Metadata",
        "",
    ]
    operational = validated["operational_logs"]
    if not operational:
        lines.append("No operational logs were selected.")
    else:
        lines.extend(
            [
                "| Component | Bytes | Lines | Critical | Error | Warning | Info | Debug | Unclassified | SHA-256 |",
                "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
            ]
        )
        for item in operational:
            severities = item["severity_counts"]
            lines.append(
                f"| `{item['component']}` | {item['byte_count']} | {item['line_count']} | "
                f"{severities['critical']} | {severities['error']} | {severities['warning']} | "
                f"{severities['info']} | {severities['debug']} | {severities['unclassified']} | "
                f"`{item['sha256']}` |"
            )
    return "\n".join(lines).rstrip() + "\n"


def validate_pair(json_content: bytes, markdown_content: bytes) -> dict[str, Any]:
    bundle = load_canonical_bundle(json_content)
    try:
        expected_markdown = render_markdown(bundle).encode("ascii")
    except UnicodeEncodeError:
        _fail("markdown_ascii_invalid")
    if markdown_content != expected_markdown:
        _fail("markdown_mismatch")
    return bundle
