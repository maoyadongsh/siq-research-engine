from __future__ import annotations

import json
import stat
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts.openshell.security_audit import (
    SCHEMA_VERSION,
    SecurityAuditError,
    SecurityRunContext,
    append_record,
    build_record,
    project_target,
    serialize_record,
)


def _context() -> SecurityRunContext:
    return SecurityRunContext(
        profile="siq_analysis",
        sandbox_id="siq-analysis-test",
        run_id="run_123",
        session_id="session-with-user-data-not-emitted",
        policy_digest="a" * 64,
    )


def _record() -> dict[str, object]:
    return build_record(
        context=_context(),
        operation_class="network.request",
        target=project_target(kind="host", scope="unknown_public", value="upload.example:443"),
        decision="deny",
        error_code="unknown_file_upload",
        duration_ms=17,
        timestamp=datetime(2026, 7, 15, 1, 2, 3, tzinfo=timezone.utc),
    )


def test_record_contains_required_safe_fields_and_hashes_sensitive_ids() -> None:
    record = _record()

    assert record["schema_version"] == SCHEMA_VERSION
    assert record["timestamp"] == "2026-07-15T01:02:03.000Z"
    assert record["session_projection"] != _context().session_id
    assert record["target"]["projection"] != "upload.example:443"  # type: ignore[index]
    serialized = serialize_record(record)
    assert b"session-with-user-data" not in serialized
    assert b"upload.example" not in serialized


@pytest.mark.parametrize("decision", ["ALLOW", "block", ""])
def test_record_rejects_unknown_decision(decision: str) -> None:
    with pytest.raises(SecurityAuditError, match="invalid_decision"):
        build_record(
            context=_context(),
            operation_class="network.request",
            target=project_target(kind="none", scope="not_applicable"),
            decision=decision,
            error_code="",
            duration_ms=0,
        )


def test_serializer_rejects_extra_fields_and_forbidden_field_names() -> None:
    record = _record()
    record["prompt"] = "never write this"
    with pytest.raises(SecurityAuditError, match="invalid_record_fields"):
        serialize_record(record)

    record = _record()
    record["error_code"] = "request_body"
    with pytest.raises(SecurityAuditError, match="forbidden_audit_field"):
        serialize_record(record)


def test_append_is_jsonl_mode_0600_and_contains_no_absolute_target(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    record = _record()

    output = append_record(project_root=project, record=record)

    assert output == project / "var/openshell/audit/2026-07-15.jsonl"
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload == record
    assert "/home/" not in output.read_text(encoding="utf-8")


def test_append_rejects_symlinked_audit_root(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    outside = tmp_path / "outside"
    (project / "var/openshell").mkdir(parents=True)
    outside.mkdir()
    (project / "var/openshell/audit").symlink_to(outside, target_is_directory=True)

    with pytest.raises(SecurityAuditError, match="unsafe_audit_directory"):
        append_record(project_root=project, record=_record())


def test_context_and_target_validation_reject_unsafe_values() -> None:
    context = SecurityRunContext(
        profile="../siq",
        sandbox_id="sandbox",
        run_id="run",
        session_id="session",
        policy_digest="a" * 64,
    )
    with pytest.raises(SecurityAuditError, match="invalid_profile"):
        context.validate()
    with pytest.raises(SecurityAuditError, match="invalid_target_value"):
        project_target(kind="path", scope="analysis", value="")
