from __future__ import annotations

import hashlib
import json
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from scripts.openshell import (
    aggregate_security_audit,
    check_sanitized_artifacts,
    export_sanitized_logs as exporter,
    security_audit,
)

GENERATED_AT = datetime(2026, 7, 16, 8, 30, tzinfo=timezone.utc)
POLICY_DIGEST = hashlib.sha256(b"sanitized-log-test-policy").hexdigest()


def _record(*, index: int = 1, decision: str = "allow", error_code: str = "") -> dict[str, Any]:
    return security_audit.build_record(
        context=security_audit.SecurityRunContext(
            profile="siq_analysis",
            sandbox_id="sandbox-fixture",
            run_id=f"run-{index}",
            session_id="session-fixture",
            policy_digest=POLICY_DIGEST,
        ),
        operation_class="network.request",
        target=security_audit.project_target(kind="host", scope="egress.request", value="example.invalid"),
        decision=decision,
        error_code=error_code,
        duration_ms=index,
        timestamp=GENERATED_AT,
    )


def _audit(path: Path, records: list[dict[str, Any]] | None = None) -> Path:
    selected = records or [_record()]
    path.write_bytes(b"".join(security_audit.serialize_record(record) for record in selected))
    return path


def test_publish_outputs_fixed_private_bundle_without_operational_messages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audit = _audit(
        tmp_path / "audit.jsonl",
        [
            _record(index=1),
            _record(index=2, decision="deny", error_code="unknown_octet_stream_upload"),
        ],
    )
    operational_content = (
        b"INFO service started at /home/private/project\n"
        b"WARN retry error=Authorization: Bearer abcdefghijklmnopqrstuvwxyz\n"
        b"ERROR database password=must-not-survive\n"
        b"FATAL private marker must-not-survive\n"
        b"DEBUG token=must-not-survive\n"
        b"plain message must-not-survive\n"
    )
    operational = tmp_path / "gateway.log"
    operational.write_bytes(operational_content)
    original_scan = check_sanitized_artifacts.scan_paths
    scan_calls: list[list[Path]] = []

    def recording_scan(paths):
        selected = list(paths)
        assert all(path.is_file() for path in selected)
        scan_calls.append(selected)
        return original_scan(selected)

    monkeypatch.setattr(exporter.check_sanitized_artifacts, "scan_paths", recording_scan)
    outputs = exporter.publish_sanitized_logs(
        audit_paths=[audit],
        operational_inputs=[("gateway", operational)],
        output_root=tmp_path / "published",
        generated_at=GENERATED_AT,
    )

    assert [path.name for path in outputs] == [exporter.OUTPUT_JSON_NAME, exporter.OUTPUT_MARKDOWN_NAME]
    assert scan_calls == [outputs]
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in outputs)
    assert original_scan(outputs) == []

    bundle = json.loads(outputs[0].read_text(encoding="ascii"))
    assert bundle["schema_version"] == exporter.SCHEMA_VERSION
    assert bundle["generated_at"] == "2026-07-16T08:30:00Z"
    assert bundle["structured_audit"]["schema_version"] == aggregate_security_audit.SCHEMA_VERSION
    assert bundle["structured_audit"]["record_count"] == 2
    assert bundle["structured_audit"]["metrics"]["external_upload_blocks"] == 1
    assert bundle["source_contract"] == {
        "audit_record_schema_version": security_audit.SCHEMA_VERSION,
        "audit_summary_schema_version": aggregate_security_audit.SCHEMA_VERSION,
        "operational_log_count": 1,
        "raw_log_messages_included": False,
        "raw_log_paths_included": False,
        "source_file_names_included": False,
    }
    assert bundle["operational_logs"] == [
        {
            "component": "gateway",
            "byte_count": len(operational_content),
            "line_count": 6,
            "severity_counts": {
                "critical": 1,
                "error": 1,
                "warning": 1,
                "info": 1,
                "debug": 1,
                "unclassified": 1,
            },
            "sha256": hashlib.sha256(operational_content).hexdigest(),
        }
    ]

    combined = "\n".join(path.read_text(encoding="ascii") for path in outputs)
    assert "must-not-survive" not in combined
    assert "Authorization" not in combined
    assert "password=" not in combined
    assert "token=" not in combined
    assert "/home/private" not in combined
    assert str(operational) not in combined
    assert operational.name not in combined


def test_operational_logs_are_optional_and_components_are_sorted(tmp_path: Path) -> None:
    audit = _audit(tmp_path / "audit.jsonl")
    first = tmp_path / "first.log"
    second = tmp_path / "second.log"
    first.write_bytes(b"ERROR one\n")
    second.write_bytes(b"INFO two\n")

    bundle = exporter.build_bundle(
        audit_paths=[audit],
        operational_inputs=[("zeta", first), ("alpha", second)],
        generated_at=GENERATED_AT,
    )
    assert [item["component"] for item in bundle["operational_logs"]] == ["alpha", "zeta"]

    no_operational = exporter.build_bundle(audit_paths=[audit], generated_at=GENERATED_AT)
    assert no_operational["operational_logs"] == []
    assert no_operational["source_contract"]["operational_log_count"] == 0
    assert "No operational logs were selected." in exporter.render_markdown(no_operational)


def test_structured_audit_must_pass_existing_strict_schema_validation(tmp_path: Path) -> None:
    record = {**_record(), "prompt": "must-not-survive"}
    audit = tmp_path / "invalid.jsonl"
    audit.write_text(json.dumps(record) + "\n", encoding="utf-8")

    with pytest.raises(aggregate_security_audit.AuditAggregationError, match="audit_record_fields_invalid"):
        exporter.build_bundle(audit_paths=[audit], generated_at=GENERATED_AT)


@pytest.mark.parametrize("kind", ["symlink", "directory", "too_large"])
def test_operational_input_rejects_unsafe_or_oversized_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    source = tmp_path / "source.log"
    source.write_bytes(b"INFO fixture\n")
    candidate = tmp_path / "candidate.log"
    expected = ""
    if kind == "symlink":
        candidate.symlink_to(source)
        expected = "symlink_not_allowed"
    elif kind == "directory":
        candidate.mkdir()
        expected = "regular_file_required"
    else:
        candidate.write_bytes(b"12345")
        monkeypatch.setattr(exporter, "MAX_OPERATIONAL_LOG_BYTES", 4)
        expected = "too_large"

    with pytest.raises(exporter.SanitizedLogExportError, match=expected):
        exporter.summarize_operational_logs([("gateway", candidate)])


def test_operational_components_and_sources_cannot_be_duplicated(tmp_path: Path) -> None:
    first = tmp_path / "first.log"
    second = tmp_path / "second.log"
    first.write_bytes(b"INFO first\n")
    second.write_bytes(b"INFO second\n")

    with pytest.raises(exporter.SanitizedLogExportError, match="component_duplicate"):
        exporter.summarize_operational_logs([("gateway", first), ("gateway", second)])
    with pytest.raises(exporter.SanitizedLogExportError, match="input_duplicate"):
        exporter.summarize_operational_logs([("gateway", first), ("broker", first)])
    with pytest.raises(exporter.SanitizedLogExportError, match="component_duplicate"):
        exporter.parse_operational_specs([f"gateway={first}", f"gateway={second}"])
    with pytest.raises(exporter.SanitizedLogExportError, match="spec_invalid"):
        exporter.parse_operational_specs([str(first)])
    with pytest.raises(exporter.SanitizedLogExportError, match="component_invalid"):
        exporter.summarize_operational_logs([("Gateway Invalid", first)])


def test_output_is_exclusive_and_rejects_symlinked_root(tmp_path: Path) -> None:
    audit = _audit(tmp_path / "audit.jsonl")
    output = tmp_path / "output"
    output.mkdir()
    existing = output / exporter.OUTPUT_JSON_NAME
    existing.write_text("do-not-overwrite\n", encoding="utf-8")

    with pytest.raises(exporter.SanitizedLogExportError, match="output_exists"):
        exporter.publish_sanitized_logs(audit_paths=[audit], output_root=output, generated_at=GENERATED_AT)
    assert existing.read_text(encoding="utf-8") == "do-not-overwrite\n"
    assert not (output / exporter.OUTPUT_MARKDOWN_NAME).exists()

    real_root = tmp_path / "real-root"
    real_root.mkdir()
    alias_root = tmp_path / "alias-root"
    alias_root.symlink_to(real_root, target_is_directory=True)
    with pytest.raises(exporter.SanitizedLogExportError, match="output_root_invalid"):
        exporter.publish_sanitized_logs(audit_paths=[audit], output_root=alias_root, generated_at=GENERATED_AT)


def test_checker_failure_removes_both_published_outputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    audit = _audit(tmp_path / "audit.jsonl")
    output = tmp_path / "output"
    finding = check_sanitized_artifacts.Finding("fixture", "forced_failure")
    monkeypatch.setattr(exporter.check_sanitized_artifacts, "scan_paths", lambda _paths: [finding])

    with pytest.raises(exporter.SanitizedLogExportError, match="validation_failed"):
        exporter.publish_sanitized_logs(audit_paths=[audit], output_root=output, generated_at=GENERATED_AT)

    assert output.is_dir()
    assert list(output.iterdir()) == []


def test_atomic_writer_removes_published_file_when_directory_sync_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "output"
    output.mkdir()
    target = output / exporter.OUTPUT_JSON_NAME
    calls = 0
    original_fsync = exporter._fsync_directory

    def fail_first_sync(path: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("injected without a filesystem path")
        original_fsync(path)

    monkeypatch.setattr(exporter, "_fsync_directory", fail_first_sync)
    with pytest.raises(exporter.SanitizedLogExportError, match="output_write_failed"):
        exporter._atomic_write_exclusive(target, b"{}\n")

    assert not target.exists()
    assert list(output.iterdir()) == []


def test_cli_emits_only_stable_status(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    audit = _audit(tmp_path / "audit.jsonl")
    operational = tmp_path / "gateway.log"
    operational.write_bytes(b"INFO private-message\n")

    assert (
        exporter.main(
            [
                "--audit",
                str(audit),
                "--operational",
                f"gateway={operational}",
                "--output-root",
                str(tmp_path / "output"),
            ]
        )
        == 0
    )
    stdout = json.loads(capsys.readouterr().out)
    assert stdout == {"ok": True, "schema_version": exporter.SCHEMA_VERSION}
    assert "private-message" not in json.dumps(stdout)
