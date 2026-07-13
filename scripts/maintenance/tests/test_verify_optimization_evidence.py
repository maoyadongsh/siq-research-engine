from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "verify_optimization_evidence.py"
spec = importlib.util.spec_from_file_location("verify_optimization_evidence_under_test", SOURCE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.path.insert(0, str(SOURCE.parent))
try:
    spec.loader.exec_module(module)
finally:
    sys.path.remove(str(SOURCE.parent))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _valid_payload(artifact: str, checksum: str) -> dict:
    return {
        "generated_at": "2026-07-13T00:00:00Z",
        "base_commit": "a" * 40,
        "worktree_dirty": True,
        "task_id": "T12",
        "environment_profile": "test-read-only",
        "command": "python verifier.py --report <configured-report>",
        "result": "pass",
        "duration_seconds": 0.25,
        "failures": [],
        "artifact_checksums": {artifact: checksum},
    }


def _write_report(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def test_verify_report_accepts_complete_metadata_and_matching_repo_artifact(tmp_path):
    artifact = tmp_path / "evidence" / "input.json"
    artifact.parent.mkdir()
    artifact.write_text('{"ok": true}\n', encoding="utf-8")
    report_path = tmp_path / "report.json"
    _write_report(report_path, _valid_payload("evidence/input.json", _sha256(artifact)))

    result = module.verify_report(report_path, repo_root=tmp_path)

    assert result == {
        "report": "report.json",
        "result": "pass",
        "failures": [],
        "artifact_verifications": [
            {"artifact": "evidence/input.json", "status": "verified"}
        ],
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("generated_at", 1),
        ("base_commit", ""),
        ("worktree_dirty", 1),
        ("task_id", None),
        ("environment_profile", []),
        ("command", " "),
        ("result", False),
        ("duration_seconds", -1),
        ("duration_seconds", float("inf")),
        ("failures", {}),
        ("artifact_checksums", []),
    ],
)
def test_verify_report_rejects_invalid_required_field_types(tmp_path, field, value):
    artifact = tmp_path / "input.json"
    artifact.write_text("{}\n", encoding="utf-8")
    payload = _valid_payload("input.json", _sha256(artifact))
    payload[field] = value
    report_path = tmp_path / "report.json"
    _write_report(report_path, payload)

    result = module.verify_report(report_path, repo_root=tmp_path)

    assert result["result"] == "fail"
    assert any(finding.get("field") == f"$.{field}" for finding in result["failures"])


def test_verify_report_lists_every_missing_contract_field(tmp_path):
    report_path = tmp_path / "report.json"
    _write_report(report_path, {})

    result = module.verify_report(report_path, repo_root=tmp_path)

    missing = {
        finding["field"]
        for finding in result["failures"]
        if finding["code"] == "missing_required_field"
    }
    assert missing == {f"$.{field}" for field in module.REQUIRED_FIELDS}


@pytest.mark.parametrize(
    ("artifact_key", "expected_code", "expected_status"),
    [
        ("missing.json", "artifact_missing", "missing"),
        ("../outside.json", "artifact_path_not_repo_relative", "invalid"),
        ("/tmp/private.json", "artifact_path_not_repo_relative", "invalid"),
        ("evidence\\input.json", "artifact_path_not_repo_relative", "invalid"),
    ],
)
def test_verify_report_fails_closed_for_unsafe_or_missing_artifacts(
    tmp_path, artifact_key, expected_code, expected_status
):
    report_path = tmp_path / "report.json"
    _write_report(report_path, _valid_payload(artifact_key, "a" * 64))

    result = module.verify_report(report_path, repo_root=tmp_path)

    assert result["result"] == "fail"
    assert expected_code in {finding["code"] for finding in result["failures"]}
    assert result["artifact_verifications"][0]["status"] == expected_status
    if artifact_key.startswith("/"):
        assert artifact_key not in json.dumps(result)


def test_verify_report_detects_checksum_mismatch(tmp_path):
    artifact = tmp_path / "input.json"
    artifact.write_text("{}\n", encoding="utf-8")
    report_path = tmp_path / "report.json"
    _write_report(report_path, _valid_payload("input.json", "0" * 64))

    result = module.verify_report(report_path, repo_root=tmp_path)

    assert {finding["code"] for finding in result["failures"]} == {
        "artifact_checksum_mismatch"
    }
    assert result["artifact_verifications"] == [
        {"artifact": "input.json", "status": "mismatch"}
    ]


def test_external_artifact_is_explicitly_unverifiable_and_fails_closed(tmp_path):
    report_path = tmp_path / "report.json"
    _write_report(report_path, _valid_payload("<external>/input.json", "a" * 64))

    result = module.verify_report(report_path, repo_root=tmp_path)

    assert result["result"] == "fail"
    assert result["failures"] == [
        {
            "code": "external_artifact_unverifiable",
            "artifact": "<external>/input.json",
        }
    ]
    assert result["artifact_verifications"] == [
        {"artifact": "<external>/input.json", "status": "unverifiable"}
    ]


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("source", "/home/operator/private/report.json", "local_absolute_path"),
        ("working_dir", "/tmp/evidence", "local_absolute_path"),
        ("database_url", "postgresql://user:private@db/siq", "credential_value"),
        ("command", "python gate.py --token private-token", "credential_value"),
        ("header", "Authorization: Bearer private-token", "credential_value"),
        ("url", "https://example.test/a?api_key=private-token", "credential_value"),
    ],
)
def test_content_policy_findings_never_echo_sensitive_values(tmp_path, field, value, code):
    artifact = tmp_path / "input.json"
    artifact.write_text("{}\n", encoding="utf-8")
    payload = _valid_payload("input.json", _sha256(artifact))
    payload[field] = value
    report_path = tmp_path / "report.json"
    _write_report(report_path, payload)

    result = module.verify_report(report_path, repo_root=tmp_path)
    serialized = json.dumps(result)

    assert result["result"] == "fail"
    assert code in {finding["code"] for finding in result["failures"]}
    assert value not in serialized
    assert "private-token" not in serialized
    assert "user:private" not in serialized


def test_redacted_credential_states_are_allowed(tmp_path):
    artifact = tmp_path / "input.json"
    artifact.write_text("{}\n", encoding="utf-8")
    payload = _valid_payload("input.json", _sha256(artifact))
    payload.update(
        {
            "api_key": "configured",
            "auth_token": "<redacted>",
            "credential_source": "secret_manager",
            "password_present": True,
        }
    )
    report_path = tmp_path / "report.json"
    _write_report(report_path, payload)

    result = module.verify_report(report_path, repo_root=tmp_path)

    assert result["result"] == "pass"


def test_invalid_json_and_non_object_reports_fail_without_content_echo(tmp_path):
    invalid = tmp_path / "invalid.json"
    invalid.write_text('{"token": "private-token"', encoding="utf-8")
    non_object = tmp_path / "list.json"
    _write_report(non_object, ["/home/private/document"])

    invalid_result = module.verify_report(invalid, repo_root=tmp_path)
    non_object_result = module.verify_report(non_object, repo_root=tmp_path)

    assert invalid_result["failures"] == [{"code": "invalid_json", "field": "$"}]
    assert non_object_result["failures"] == [{"code": "report_not_object", "field": "$"}]
    assert "private-token" not in json.dumps(invalid_result)
    assert "/home/private" not in json.dumps(non_object_result)


def test_verify_reports_is_stably_sorted_and_has_its_own_evidence_contract(tmp_path):
    artifact = tmp_path / "input.json"
    artifact.write_text("{}\n", encoding="utf-8")
    reports = [tmp_path / "b.json", tmp_path / "a.json"]
    for report_path in reports:
        _write_report(report_path, _valid_payload("input.json", _sha256(artifact)))

    result = module.verify_reports(reports, repo_root=tmp_path)

    assert result["passed"] is True
    assert [item["report"] for item in result["reports"]] == ["a.json", "b.json"]
    assert all(field in result for field in module.REQUIRED_FIELDS)
    assert result["task_id"] == "T12"
    assert result["result"] == "pass"
    assert result["failures"] == []
    assert set(result["artifact_checksums"]) == {"a.json", "b.json"}


def test_main_supports_multiple_reports_and_both_outputs(tmp_path, capsys):
    source_checksum = _sha256(SOURCE)
    payload = _valid_payload(
        "scripts/maintenance/verify_optimization_evidence.py", source_checksum
    )
    inputs = [tmp_path / "b.json", tmp_path / "a.json"]
    for input_path in inputs:
        _write_report(input_path, payload)
    json_output = tmp_path / "verification.json"
    markdown_output = tmp_path / "verification.md"

    exit_code = module.main(
        [
            "--report",
            str(inputs[0]),
            "--report",
            str(inputs[1]),
            "--json-output",
            str(json_output),
            "--markdown-output",
            str(markdown_output),
        ]
    )

    assert exit_code == 0
    assert "PASS optimization evidence verification" in capsys.readouterr().out
    output = json.loads(json_output.read_text(encoding="utf-8"))
    assert output["report_count"] == 2
    assert output["summary"]["artifact_verified"] == 2
    markdown_text = markdown_output.read_text(encoding="utf-8")
    assert "# Optimization Evidence Verification" in markdown_text
    assert "- Command:" in markdown_text
    assert "- Duration:" in markdown_text
    assert "## Aggregate Failures" in markdown_text
    assert "## Input Artifact Checksums" in markdown_text
    assert str(tmp_path) not in json.dumps(output)


def test_main_refuses_to_overwrite_an_input_report(tmp_path, capsys):
    report_path = tmp_path / "report.json"
    original = json.dumps(_valid_payload("<external>/input.json", "a" * 64)) + "\n"
    report_path.write_text(original, encoding="utf-8")

    exit_code = module.main(
        ["--report", str(report_path), "--json-output", str(report_path)]
    )

    assert exit_code == 2
    assert report_path.read_text(encoding="utf-8") == original
    assert "unsafe output path configuration" in capsys.readouterr().out
