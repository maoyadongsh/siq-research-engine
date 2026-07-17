from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from scripts.openshell import (
    aggregate_security_audit as aggregate,
    check_sanitized_artifacts,
    export_sanitized_evidence as exporter,
    security_audit,
)

BASE_TIME = datetime(2026, 7, 15, 8, 0, tzinfo=timezone.utc)
POLICY_DIGEST = hashlib.sha256(b"test-policy").hexdigest()


def _record(
    index: int,
    *,
    operation_class: str,
    scope: str,
    decision: str = "allow",
    error_code: str = "",
    duration_ms: int = 1,
    profile: str = "siq_analysis",
) -> dict[str, Any]:
    return security_audit.build_record(
        context=security_audit.SecurityRunContext(
            profile=profile,
            sandbox_id="sandbox-fixture",
            run_id=f"run-{index}",
            session_id="session-fixture",
            policy_digest=POLICY_DIGEST,
        ),
        operation_class=operation_class,
        target=security_audit.project_target(kind="service", scope=scope, value=f"target-{index}"),
        decision=decision,
        error_code=error_code,
        duration_ms=duration_ms,
        timestamp=BASE_TIME + timedelta(seconds=index),
    )


def _audit_fixture() -> list[dict[str, Any]]:
    return [
        *[
            _record(
                index,
                operation_class="runtime.route",
                scope="gateway.route",
                duration_ms=duration,
            )
            for index, duration in enumerate((10, 20, 30, 40), start=1)
        ],
        _record(
            5,
            operation_class="network.request",
            scope="egress.upload",
            decision="deny",
            error_code="unknown_octet_stream_upload",
        ),
        _record(
            6,
            operation_class="network.request",
            scope="egress.request",
            decision="audit_only",
            error_code="unknown_json_post_audit",
        ),
        _record(
            7,
            operation_class="sandbox.lifecycle",
            scope="sandbox.start",
            decision="deny",
            error_code="sandbox_start_failed",
        ),
        _record(8, operation_class="service.preflight", scope="tool.shell"),
        _record(
            9,
            operation_class="service.preflight",
            scope="tool.python",
            decision="deny",
            error_code="tool_execution_failed",
        ),
        _record(
            10,
            operation_class="immutable.write",
            scope="immutable.path",
            decision="deny",
            error_code="immutable_write_blocked",
        ),
        _record(
            11,
            operation_class="network.request",
            scope="egress.upload",
            decision="deny",
            error_code="broker_octet_stream_denied",
        ),
    ]


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> Path:
    path.write_bytes(b"".join(security_audit.serialize_record(record) for record in records))
    return path


def test_aggregate_reports_required_security_counts_and_gateway_percentiles(tmp_path: Path) -> None:
    source = _write_jsonl(tmp_path / "audit.jsonl", _audit_fixture())
    records, digests = aggregate.load_records([source])
    summary = aggregate.aggregate_records(records, source_digests=digests)

    assert summary["schema_version"] == aggregate.SCHEMA_VERSION
    assert summary["source_schema_version"] == security_audit.SCHEMA_VERSION
    assert summary["record_count"] == 11
    assert summary["source_file_count"] == 1
    assert summary["source_sha256"] == [hashlib.sha256(source.read_bytes()).hexdigest()]
    assert summary["profiles"] == {"siq_analysis": 11}
    assert summary["decisions"] == {"allow": 5, "audit_only": 1, "deny": 5}
    assert summary["metrics"] == {
        "policy_deny_count": 5,
        "audit_only_count": 1,
        "sandbox_start_failures": 1,
        "tool_operation_count": 2,
        "tool_failure_count": 1,
        "tool_failure_rate": 0.5,
        "external_upload_blocks": 2,
        "immutable_write_blocks": 1,
        "gateway_overhead_ms": {"sample_count": 4, "p50": 25, "p95": 38.5},
    }
    serialized = json.dumps(summary, sort_keys=True)
    assert str(source) not in serialized
    assert "sandbox-fixture" not in serialized
    assert "session-fixture" not in serialized


def test_aggregate_counts_only_fixed_formal_transfer_scopes_as_upload_blocks() -> None:
    records = [
        _record(
            index,
            operation_class="network.request",
            scope=scope,
            decision="deny",
            error_code="direct_egress_denied",
        )
        for index, scope in enumerate(sorted(aggregate.FORMAL_TRANSFER_SCOPES), start=1)
    ]
    records.append(
        _record(
            20,
            operation_class="network.request",
            scope="formal_egress.direct_public_tcp",
            decision="deny",
            error_code="direct_egress_denied",
        )
    )

    summary = aggregate.aggregate_records(records, source_digests=["a" * 64])

    assert summary["metrics"]["external_upload_blocks"] == len(aggregate.FORMAL_TRANSFER_SCOPES)


def test_aggregate_rejects_schema_drift_directories_symlinks_and_duplicates(tmp_path: Path) -> None:
    record = _audit_fixture()[0]
    drifted = {**record, "prompt": "must never aggregate"}
    invalid = tmp_path / "invalid.jsonl"
    invalid.write_text(json.dumps(drifted) + "\n", encoding="utf-8")
    with pytest.raises(aggregate.AuditAggregationError, match="audit_record_fields_invalid"):
        aggregate.load_records([invalid])

    directory = tmp_path / "directory.jsonl"
    directory.mkdir()
    with pytest.raises(aggregate.AuditAggregationError, match="regular_file_required"):
        aggregate.load_records([directory])

    source = _write_jsonl(tmp_path / "source.jsonl", [record])
    alias = tmp_path / "alias.jsonl"
    alias.symlink_to(source)
    with pytest.raises(aggregate.AuditAggregationError, match="symlink_not_allowed"):
        aggregate.load_records([alias])
    with pytest.raises(aggregate.AuditAggregationError, match="duplicate"):
        aggregate.load_records([source, source])


def test_aggregate_cli_writes_only_explicit_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = _write_jsonl(tmp_path / "audit.jsonl", _audit_fixture())
    output = tmp_path / "summary.json"

    assert aggregate.main(["--input", str(source), "--output", str(output)]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["ok"] is True
    assert output.is_file()
    assert json.loads(output.read_text(encoding="utf-8"))["metrics"]["policy_deny_count"] == 5


def test_sanitized_export_removes_sensitive_json_and_markdown_bodies(tmp_path: Path, monkeypatch) -> None:
    raw_json = tmp_path / "run-evidence.json"
    raw_json.write_text(
        json.dumps(
            {
                "profile": "siq_analysis",
                "rule_id": "unknown_octet_stream_upload",
                "latency_ms": 17,
                "quality_score": 0.98,
                "version": "0.0.83",
                "policy_digest": POLICY_DIGEST,
                "token": "must-not-survive",
                "Authorization": "Bearer must-not-survive",
                "cookie": "session=must-not-survive",
                "dsn": "postgresql://reader:must-not-survive@example.invalid/siq",
                "home": "/home/private-user",
                "source_path": "/workspace/private/project/report.md",
                "prompt": "must-not-survive",
                "user_input": "must-not-survive",
                "question": "must-not-survive",
                "query": "must-not-survive",
                "content": "must-not-survive",
                "access_key_id": "must-not-survive",
                "signing_passphrase": "must-not-survive",
                "messages": [
                    {"role": "system", "content": "must-not-survive"},
                    {"role": "user", "content": "must-not-survive"},
                    {"role": "assistant", "content": "safe aggregate outcome"},
                ],
                "attachments": [
                    {"name": "public-fixture.pdf", "content": "must-not-survive"},
                ],
                "attachment": "must-not-survive scalar attachment body",
                "attachment_raw_data": "must-not-survive compound attachment body",
                "note": "database=postgresql://reader@example.invalid/siq from /opt/private/file",
            }
        ),
        encoding="utf-8",
    )
    raw_markdown = tmp_path / "audit-notes.md"
    raw_markdown.write_text(
        "# Audit Notes\n\n"
        "profile: siq_analysis\n"
        "rule_id: immutable_write_blocked\n"
        "quality_score: 0.97\n\n"
        "## Prompt\n"
        "must-not-survive prompt body\n\n"
        "## Metrics\n"
        "gateway_p95_ms: 41\n"
        "Authorization: Bearer must-not-survive\n"
        "cookie: must-not-survive\n"
        "dsn: postgresql://reader:must-not-survive@example.invalid/siq\n"
        "source: /home/private-user/project/result.json\n\n"
        "## User Input\n"
        "must-not-survive user body\n\n"
        "## Attachment Body\n"
        "must-not-survive attachment body\n\n"
        "## Evidence\n"
        "decision: deny\n",
        encoding="utf-8",
    )
    output_root = tmp_path / "sanitized"
    original_scan = check_sanitized_artifacts.scan_paths
    scanned: list[list[Path]] = []

    def recording_scan(paths):
        materialized = list(paths)
        scanned.append(materialized)
        return original_scan(materialized)

    monkeypatch.setattr(exporter.check_sanitized_artifacts, "scan_paths", recording_scan)
    outputs = exporter.export_evidence([raw_json, raw_markdown], output_root=output_root)

    assert {path.name for path in outputs} == {
        "run-evidence.sanitized.json",
        "run-evidence.sanitized.md",
        "audit-notes.sanitized.json",
        "audit-notes.sanitized.md",
    }
    assert scanned == [outputs]
    assert original_scan(outputs) == []
    combined = "\n".join(path.read_text(encoding="utf-8") for path in outputs)
    assert "must-not-survive" not in combined
    assert "/home/" not in combined
    assert "/workspace/" not in combined
    assert "/opt/" not in combined
    assert "postgresql://" not in combined
    assert "siq_analysis" in combined
    assert "unknown_octet_stream_upload" in combined
    assert "immutable_write_blocked" in combined
    assert "quality_score" in combined
    assert "0.0.83" in combined
    assert POLICY_DIGEST in combined
    assert "gateway_p95_ms" in combined

    sanitized_json = json.loads((output_root / "run-evidence.sanitized.json").read_text(encoding="utf-8"))
    evidence = sanitized_json["evidence"]
    assert evidence["profile"] == "siq_analysis"
    assert "token" not in evidence
    assert "Authorization" not in evidence
    assert "cookie" not in evidence
    assert "dsn" not in evidence
    assert "home" not in evidence
    assert "prompt" not in evidence
    assert "user_input" not in evidence
    assert evidence["source_path"] == "<redacted-path>"
    assert "messages" not in evidence
    assert evidence["attachments"] == [{"name": "public-fixture.pdf"}]
    assert "attachment" not in evidence
    assert "attachment_raw_data" not in evidence


def test_sanitized_export_removes_single_run_runtime_identity_fields(tmp_path: Path) -> None:
    source = tmp_path / "probe.json"
    source.write_text(
        json.dumps(
            {
                "schema_version": "siq.openshell.provider_independent_security_probe.v1",
                "container_id": "deadbeef1234",
                "probe_id": "probe-123456789abc",
                "run_nonce_sha256": "a" * 64,
                "runtime_snapshot": "var/openshell/runs/probe-123456789abc",
                "sandbox_id": "11111111-1111-1111-1111-111111111111",
                "sandbox_name": "siq-analysis-security-probe-123456789abc",
                "sentinel_marker": "siq-security-probe:abc",
                "sentinel_name": ".siq-security-probe-abc",
                "policy": "var/openshell/policies/probe-123456789abc/task-policy.yaml",
                "mount_plan": "var/openshell/mount-plans/probe-123456789abc.json",
                "policy_sha256": POLICY_DIGEST,
                "phase": "passed",
            }
        ),
        encoding="utf-8",
    )

    outputs = exporter.export_evidence([source], output_root=tmp_path / "evidence")
    sanitized = json.loads((tmp_path / "evidence/probe.sanitized.json").read_text(encoding="utf-8"))
    evidence = sanitized["evidence"]

    for key in (
        "container_id",
        "probe_id",
        "run_nonce_sha256",
        "runtime_snapshot",
        "sandbox_id",
        "sandbox_name",
        "sentinel_marker",
        "sentinel_name",
        "policy",
        "mount_plan",
    ):
        assert key not in evidence
    assert evidence["policy_sha256"] == POLICY_DIGEST
    assert evidence["phase"] == "passed"
    assert len(outputs) == 2
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in outputs)


def test_exporter_renders_audit_summary_as_competition_json_and_markdown(tmp_path: Path) -> None:
    source = _write_jsonl(tmp_path / "audit.jsonl", _audit_fixture())
    records, digests = aggregate.load_records([source])
    summary_path = tmp_path / "audit-summary.json"
    aggregate.write_summary(
        summary_path,
        aggregate.aggregate_records(records, source_digests=digests),
    )

    outputs = exporter.export_evidence([summary_path], output_root=tmp_path / "evidence")

    assert check_sanitized_artifacts.scan_paths(outputs) == []
    markdown = (tmp_path / "evidence" / "audit-summary.sanitized.md").read_text(encoding="utf-8")
    assert "Policy denies: `5`" in markdown
    assert "Audit-only decisions: `1`" in markdown
    assert "Gateway overhead P50/P95 ms: `25` / `38.5`" in markdown
    assert "unknown_octet_stream_upload" in markdown


def test_export_fails_closed_on_checker_finding_and_removes_outputs(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "evidence.json"
    source.write_text('{"status":"safe"}\n', encoding="utf-8")
    output_root = tmp_path / "output"
    finding = check_sanitized_artifacts.Finding("fixture", "test_finding")
    monkeypatch.setattr(exporter.check_sanitized_artifacts, "scan_paths", lambda _paths: [finding])

    with pytest.raises(exporter.EvidenceExportError, match="validation_failed"):
        exporter.export_evidence([source], output_root=output_root)

    assert list(output_root.iterdir()) == []


def test_export_requires_explicit_regular_inputs_and_safe_output_root(tmp_path: Path) -> None:
    source = tmp_path / "evidence.json"
    source.write_text('{"status":"safe"}\n', encoding="utf-8")
    directory = tmp_path / "directory.json"
    directory.mkdir()
    with pytest.raises(exporter.EvidenceExportError, match="regular_file_required"):
        exporter.export_evidence([directory], output_root=tmp_path / "output-a")

    alias = tmp_path / "alias.json"
    alias.symlink_to(source)
    with pytest.raises(exporter.EvidenceExportError, match="symlink_not_allowed"):
        exporter.export_evidence([alias], output_root=tmp_path / "output-b")

    real_output = tmp_path / "real-output"
    real_output.mkdir()
    alias_output = tmp_path / "alias-output"
    alias_output.symlink_to(real_output, target_is_directory=True)
    with pytest.raises(exporter.EvidenceExportError, match="output_root_invalid"):
        exporter.export_evidence([source], output_root=alias_output)


def test_sanitized_output_names_cannot_collide_or_overwrite(tmp_path: Path) -> None:
    json_input = tmp_path / "same.json"
    md_input = tmp_path / "same.md"
    json_input.write_text('{"status":"safe"}\n', encoding="utf-8")
    md_input.write_text("status: safe\n", encoding="utf-8")
    with pytest.raises(exporter.EvidenceExportError, match="name_collision"):
        exporter.export_evidence([json_input, md_input], output_root=tmp_path / "collision")

    output_root = tmp_path / "existing"
    output_root.mkdir()
    (output_root / "same.sanitized.json").write_text("do-not-overwrite\n", encoding="utf-8")
    with pytest.raises(exporter.EvidenceExportError, match="output_exists"):
        exporter.export_evidence([json_input], output_root=output_root)
    assert (output_root / "same.sanitized.json").read_text(encoding="utf-8") == "do-not-overwrite\n"
