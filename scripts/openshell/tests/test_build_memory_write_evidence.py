from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from scripts.openshell import build_memory_write_evidence as evidence

NOW = 2_000_000_000
SHA_A = "a" * 64
SHA_B = "b" * 64


def _postgres_receipt() -> dict[str, object]:
    outcome = {
        "insert": True,
        "readback": True,
        "rollback": True,
        "post_rollback_verify": True,
        "residual_count": 0,
    }
    return {
        "schema_version": evidence.POSTGRES_RECEIPT_SCHEMA,
        "backend": "postgresql",
        "executor": evidence.EXECUTOR,
        "captured_at_unix": NOW - 120,
        "completed_at_unix": NOW - 110,
        "probe_sha256": SHA_A,
        "agent_groups": {
            "primary_market": dict(outcome),
            "secondary_market": dict(outcome),
        },
        "residual_count": 0,
    }


def _milvus_receipt() -> dict[str, object]:
    outcome = {
        "upsert": True,
        "get": True,
        "search": True,
        "delete": True,
        "post_delete_verify": True,
        "residual_count": 0,
    }
    return {
        "schema_version": evidence.MILVUS_RECEIPT_SCHEMA,
        "backend": "milvus",
        "executor": evidence.EXECUTOR,
        "captured_at_unix": NOW - 100,
        "completed_at_unix": NOW - 90,
        "probe_sha256": SHA_B,
        "logical_alias": evidence.LOGICAL_ALIAS,
        "physical_collection": "siq_agent_memory__v2",
        "required_schema_version": evidence.REQUIRED_COLLECTION_SCHEMA,
        "schema_preflight_passed": True,
        "agent_groups": {
            "primary_market": dict(outcome),
            "secondary_market": dict(outcome),
        },
        "residual_count": 0,
    }


def _write_json(path: Path, payload: object, *, mode: int) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True).encode("ascii") + b"\n"
    path.write_bytes(content)
    path.chmod(mode)
    return content


def _project(tmp_path: Path) -> tuple[Path, dict[str, object], dict[str, object]]:
    root = tmp_path / "repo"
    root.mkdir()
    _write_json(root / evidence.BOUNDARY_RELATIVE, evidence.EXPECTED_BOUNDARY, mode=0o664)
    schema_source = evidence.REPO_ROOT / evidence.EVIDENCE_SCHEMA_RELATIVE
    schema_path = root / evidence.EVIDENCE_SCHEMA_RELATIVE
    schema_path.parent.mkdir(parents=True)
    schema_path.write_bytes(schema_source.read_bytes())
    schema_path.chmod(0o664)
    postgres = _postgres_receipt()
    milvus = _milvus_receipt()
    _write_json(root / evidence.POSTGRES_RECEIPT_RELATIVE, postgres, mode=0o600)
    _write_json(root / evidence.MILVUS_RECEIPT_RELATIVE, milvus, mode=0o600)
    return root, postgres, milvus


def _rewrite(root: Path, relative: Path, payload: object, *, mode: int = 0o600) -> bytes:
    return _write_json(root / relative, payload, mode=mode)


def test_builds_schema_valid_sanitized_owner_only_evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, _postgres, _milvus = _project(tmp_path)
    original_scan = evidence.check_sanitized_artifacts.scan_paths
    calls: list[list[Path]] = []

    def scan(paths: list[Path]):
        calls.append(paths)
        return original_scan(paths)

    monkeypatch.setattr(evidence.check_sanitized_artifacts, "scan_paths", scan)
    result = evidence.build_and_publish(project_root=root, now=NOW)
    json_path = root / evidence.OUTPUT_JSON_RELATIVE
    markdown_path = root / evidence.OUTPUT_MARKDOWN_RELATIVE
    payload = json.loads(json_path.read_text(encoding="ascii"))
    schema = json.loads((root / evidence.EVIDENCE_SCHEMA_RELATIVE).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(payload)

    assert result == payload
    assert calls == [[json_path, markdown_path]]
    assert os.stat(json_path).st_mode & 0o777 == 0o600
    assert os.stat(markdown_path).st_mode & 0o777 == 0o600
    assert evidence.check_sanitized_artifacts.scan_paths([json_path, markdown_path]) == []
    assert payload["agent_groups"] == ["primary_market", "secondary_market"]
    assert payload["contract_binding"]["logical_alias"] == "siq_agent_memory_active"
    assert (
        payload["contract_binding"]["physical_collection_sha256"] == hashlib.sha256(b"siq_agent_memory__v2").hexdigest()
    )
    assert payload["backends"]["postgresql"]["residual_count"] == 0
    assert payload["backends"]["milvus"]["residual_count"] == 0
    published = json_path.read_text(encoding="ascii") + markdown_path.read_text(encoding="ascii")
    assert "siq_agent_memory__v2" not in published
    assert "user_id" not in published
    assert "company_id" not in published
    assert "run_id" not in published
    assert "postgresql://" not in published
    assert evidence.validate_consumable_evidence(json_path, project_root=root, now=NOW) == payload


def test_source_receipts_are_bound_by_exact_byte_digest(tmp_path: Path) -> None:
    root, postgres, _milvus = _project(tmp_path)
    postgres_bytes = _rewrite(root, evidence.POSTGRES_RECEIPT_RELATIVE, postgres)
    result = evidence.build_evidence(project_root=root, now=NOW)
    assert result["backends"]["postgresql"]["receipt_sha256"] == hashlib.sha256(postgres_bytes).hexdigest()
    assert (
        result["contract_binding"]["boundary_contract_sha256"]
        == hashlib.sha256((root / evidence.BOUNDARY_RELATIVE).read_bytes()).hexdigest()
    )
    assert (
        result["contract_binding"]["evidence_schema_sha256"]
        == hashlib.sha256((root / evidence.EVIDENCE_SCHEMA_RELATIVE).read_bytes()).hexdigest()
    )


def test_consumable_validator_rejects_wrong_evidence_path(tmp_path: Path) -> None:
    root, _postgres, _milvus = _project(tmp_path)
    evidence.build_and_publish(project_root=root, now=NOW)
    alternate = root / "memory-write-evidence.sanitized.json"
    alternate.write_bytes((root / evidence.OUTPUT_JSON_RELATIVE).read_bytes())
    alternate.chmod(0o600)
    with pytest.raises(evidence.MemoryEvidenceError, match="^evidence_path_invalid$"):
        evidence.validate_consumable_evidence(alternate, project_root=root, now=NOW)


def test_consumable_validator_rejects_digest_or_operation_tampering(tmp_path: Path) -> None:
    root, _postgres, _milvus = _project(tmp_path)
    evidence.build_and_publish(project_root=root, now=NOW)
    output = root / evidence.OUTPUT_JSON_RELATIVE
    payload = json.loads(output.read_text(encoding="ascii"))
    payload["contract_binding"]["boundary_contract_sha256"] = "f" * 64
    _write_json(output, payload, mode=0o600)
    with pytest.raises(evidence.MemoryEvidenceError, match="^evidence_schema_invalid$"):
        evidence.validate_consumable_evidence(output, project_root=root, now=NOW)

    evidence.build_and_publish(project_root=root, now=NOW)
    payload = json.loads(output.read_text(encoding="ascii"))
    payload["backends"]["milvus"]["operation_outcomes"]["secondary_market"]["search"] = False
    _write_json(output, payload, mode=0o600)
    with pytest.raises(evidence.MemoryEvidenceError, match="^receipt_operation_outcome_invalid$"):
        evidence.validate_consumable_evidence(output, project_root=root, now=NOW)


def test_refuses_missing_fixed_receipt_even_when_an_alternate_receipt_exists(tmp_path: Path) -> None:
    root, postgres, _milvus = _project(tmp_path)
    (root / evidence.POSTGRES_RECEIPT_RELATIVE).unlink()
    _write_json(root / "var/openshell/proofs/alternate.json", postgres, mode=0o600)
    with pytest.raises(evidence.MemoryEvidenceError, match="^input_missing$"):
        evidence.build_evidence(project_root=root, now=NOW)


def test_refuses_non_owner_only_raw_receipt(tmp_path: Path) -> None:
    root, postgres, _milvus = _project(tmp_path)
    _rewrite(root, evidence.POSTGRES_RECEIPT_RELATIVE, postgres, mode=0o640)
    with pytest.raises(evidence.MemoryEvidenceError, match="^input_file_unsafe$"):
        evidence.build_evidence(project_root=root, now=NOW)


def test_refuses_symlinked_raw_receipt(tmp_path: Path) -> None:
    root, postgres, _milvus = _project(tmp_path)
    receipt = root / evidence.POSTGRES_RECEIPT_RELATIVE
    receipt.unlink()
    target = root / "owner-only.json"
    _write_json(target, postgres, mode=0o600)
    receipt.symlink_to(target)
    with pytest.raises(evidence.MemoryEvidenceError, match="^input_symlink_not_allowed$"):
        evidence.build_evidence(project_root=root, now=NOW)


def test_refuses_duplicate_json_keys(tmp_path: Path) -> None:
    root, _postgres, _milvus = _project(tmp_path)
    path = root / evidence.POSTGRES_RECEIPT_RELATIVE
    path.write_text('{"schema_version":"one","schema_version":"two"}\n', encoding="ascii")
    path.chmod(0o600)
    with pytest.raises(evidence.MemoryEvidenceError, match="^input_json_duplicate_key$"):
        evidence.build_evidence(project_root=root, now=NOW)


def test_refuses_extra_sensitive_receipt_field(tmp_path: Path) -> None:
    root, postgres, _milvus = _project(tmp_path)
    postgres["database_url"] = "must-not-be-accepted"
    _rewrite(root, evidence.POSTGRES_RECEIPT_RELATIVE, postgres)
    with pytest.raises(evidence.MemoryEvidenceError, match="^postgres_receipt_schema_invalid$"):
        evidence.build_evidence(project_root=root, now=NOW)


@pytest.mark.parametrize("backend", ["postgresql", "milvus"])
def test_refuses_any_nonzero_residual(tmp_path: Path, backend: str) -> None:
    root, postgres, milvus = _project(tmp_path)
    receipt = postgres if backend == "postgresql" else milvus
    receipt["residual_count"] = 1
    relative = evidence.POSTGRES_RECEIPT_RELATIVE if backend == "postgresql" else evidence.MILVUS_RECEIPT_RELATIVE
    _rewrite(root, relative, receipt)
    code = "postgres_receipt_schema_invalid" if backend == "postgresql" else "milvus_receipt_schema_invalid"
    with pytest.raises(evidence.MemoryEvidenceError, match=f"^{code}$"):
        evidence.build_evidence(project_root=root, now=NOW)


@pytest.mark.parametrize("backend", ["postgresql", "milvus"])
def test_refuses_failed_market_operation_or_cleanup(tmp_path: Path, backend: str) -> None:
    root, postgres, milvus = _project(tmp_path)
    receipt = postgres if backend == "postgresql" else milvus
    operation = "post_rollback_verify" if backend == "postgresql" else "post_delete_verify"
    receipt["agent_groups"]["primary_market"][operation] = False
    relative = evidence.POSTGRES_RECEIPT_RELATIVE if backend == "postgresql" else evidence.MILVUS_RECEIPT_RELATIVE
    _rewrite(root, relative, receipt)
    with pytest.raises(evidence.MemoryEvidenceError, match="^receipt_operation_outcome_invalid$"):
        evidence.build_evidence(project_root=root, now=NOW)


def test_refuses_incomplete_market_group_coverage(tmp_path: Path) -> None:
    root, postgres, _milvus = _project(tmp_path)
    del postgres["agent_groups"]["primary_market"]
    _rewrite(root, evidence.POSTGRES_RECEIPT_RELATIVE, postgres)
    with pytest.raises(evidence.MemoryEvidenceError, match="^receipt_agent_groups_invalid$"):
        evidence.build_evidence(project_root=root, now=NOW)


def test_refuses_stale_receipt(tmp_path: Path) -> None:
    root, postgres, _milvus = _project(tmp_path)
    postgres["captured_at_unix"] = NOW - evidence.MAX_EVIDENCE_AGE_SECONDS - 100
    postgres["completed_at_unix"] = NOW - evidence.MAX_EVIDENCE_AGE_SECONDS - 90
    _rewrite(root, evidence.POSTGRES_RECEIPT_RELATIVE, postgres)
    with pytest.raises(evidence.MemoryEvidenceError, match="^receipt_timestamp_invalid$"):
        evidence.build_evidence(project_root=root, now=NOW)


def test_refuses_receipts_outside_one_combined_probe_window(tmp_path: Path) -> None:
    root, postgres, _milvus = _project(tmp_path)
    postgres["captured_at_unix"] = NOW - evidence.MAX_COMBINED_WINDOW_SECONDS - 100
    postgres["completed_at_unix"] = NOW - evidence.MAX_COMBINED_WINDOW_SECONDS - 90
    _rewrite(root, evidence.POSTGRES_RECEIPT_RELATIVE, postgres)
    with pytest.raises(evidence.MemoryEvidenceError, match="^combined_probe_window_invalid$"):
        evidence.build_evidence(project_root=root, now=NOW)


def test_refuses_alias_as_physical_collection(tmp_path: Path) -> None:
    root, _postgres, milvus = _project(tmp_path)
    milvus["physical_collection"] = evidence.LOGICAL_ALIAS
    _rewrite(root, evidence.MILVUS_RECEIPT_RELATIVE, milvus)
    with pytest.raises(evidence.MemoryEvidenceError, match="^milvus_receipt_schema_invalid$"):
        evidence.build_evidence(project_root=root, now=NOW)


def test_refuses_drifted_memory_boundary_contract(tmp_path: Path) -> None:
    root, _postgres, _milvus = _project(tmp_path)
    boundary = dict(evidence.EXPECTED_BOUNDARY)
    boundary["sandbox_direct_milvus"] = True
    _write_json(root / evidence.BOUNDARY_RELATIVE, boundary, mode=0o664)
    with pytest.raises(evidence.MemoryEvidenceError, match="^memory_boundary_contract_invalid$"):
        evidence.build_evidence(project_root=root, now=NOW)


def test_sanitizer_failure_removes_both_outputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, _postgres, _milvus = _project(tmp_path)
    monkeypatch.setattr(evidence.check_sanitized_artifacts, "scan_paths", lambda _paths: [object()])
    with pytest.raises(evidence.MemoryEvidenceError, match="^evidence_sanitization_failed$"):
        evidence.build_and_publish(project_root=root, now=NOW)
    assert not (root / evidence.OUTPUT_JSON_RELATIVE).exists()
    assert not (root / evidence.OUTPUT_MARKDOWN_RELATIVE).exists()


def test_refuses_symlinked_output_without_replacing_target(tmp_path: Path) -> None:
    root, _postgres, _milvus = _project(tmp_path)
    target = root / "do-not-replace.json"
    target.write_text("sentinel\n", encoding="ascii")
    output = root / evidence.OUTPUT_JSON_RELATIVE
    output.parent.mkdir(parents=True, exist_ok=True)
    output.symlink_to(target)
    with pytest.raises(evidence.MemoryEvidenceError, match="^evidence_output_unsafe$"):
        evidence.build_and_publish(project_root=root, now=NOW)
    assert target.read_text(encoding="ascii") == "sentinel\n"


def test_cli_fails_closed_when_real_receipts_are_absent(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, _postgres, _milvus = _project(tmp_path)
    (root / evidence.POSTGRES_RECEIPT_RELATIVE).unlink()
    assert evidence.main(["--project-root", str(root)]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"decision": "NO_GO", "error_code": "input_missing", "ok": False}
    assert not (root / evidence.OUTPUT_JSON_RELATIVE).exists()
