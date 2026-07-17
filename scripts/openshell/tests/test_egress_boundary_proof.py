from __future__ import annotations

import json
import stat
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from scripts.openshell import check_sanitized_artifacts, run_egress_boundary_proof as proof, security_audit


def _digests() -> dict[str, str]:
    return {
        "allowlist_sha256": "a" * 64,
        "allowlist_contract_sha256": "b" * 64,
        "audit_contract_sha256": "c" * 64,
        "broker_lifecycle_sha256": "d" * 64,
        "broker_identity_contract_sha256": "e" * 64,
        "egress_decision_sha256": "f" * 64,
        "egress_guard_sha256": "1" * 64,
        "evidence_schema_sha256": "2" * 64,
        "mihomo_runtime_config_sha256": "3" * 64,
        "proof_runner_sha256": "4" * 64,
        "runtime_source_bundle_sha256": "5" * 64,
        "siq_fetch_sha256": "6" * 64,
        "toolchain_manifest_sha256": "7" * 64,
    }


def _results() -> list[proof.CaseResult]:
    values: list[proof.CaseResult] = []
    for spec in proof._case_specs():
        values.append(
            proof.CaseResult(
                case_id=spec.case_id,
                decision=spec.decision,
                rule_id=spec.rule_id,
                outer_http_status=spec.outer_status,
                upstream_http_status=200 if spec.upstream_required else None,
            )
        )
    return values


def _records(results: list[proof.CaseResult], run_id: str = "egress-proof-fixture") -> list[dict[str, Any]]:
    context = security_audit.SecurityRunContext(
        profile=proof.PROFILE,
        sandbox_id="host-egress-proof",
        run_id=run_id,
        session_id="egress-boundary-proof",
        policy_digest="a" * 64,
    )
    values = [
        security_audit.build_record(
            context=context,
            operation_class="network.request",
            target=security_audit.project_target(
                kind="host",
                scope=f"egress.{item.rule_id}",
                value=f"fixture-{item.case_id}",
            ),
            decision=item.decision,
            error_code=item.rule_id if item.decision == "deny" else "",
            duration_ms=1,
        )
        for item in results
    ]
    values.extend(
        security_audit.build_record(
            context=context,
            operation_class="network.request",
            target=security_audit.project_target(
                kind="host",
                scope="egress.mihomo_fake_ip_compat_resolved",
                value=f"fixture-resolver-{index}",
            ),
            decision="audit_only",
            error_code="",
            duration_ms=1,
        )
        for index in range(6)
    )
    return values


def test_evidence_is_strict_schema_valid_and_sanitized() -> None:
    run_id = "egress-proof-fixture"
    results = _results()
    value = proof.build_evidence(
        captured_at=10_000,
        run_id=run_id,
        source_digests=_digests(),
        results=results,
        audit_records=_records(results, run_id),
        identity_checks={"missing_identity_denied": True, "wrong_audience_denied": True},
    )
    schema = json.loads((proof.REPO_ROOT / proof.SCHEMA_RELATIVE).read_text(encoding="utf-8"))

    jsonschema.Draft202012Validator(schema).validate(value)
    serialized = proof._canonical(value) + b"\n"
    assert check_sanitized_artifacts.scan_content(Path("evidence.json"), serialized) == []
    assert run_id.encode() not in serialized
    assert b"example.com" not in serialized
    assert b"169.254.169.254" not in serialized
    assert value["formal_business_sandbox_evidence"] is False
    assert value["formal_business_run"] is False
    assert value["eligible_for_completion"] is False
    assert value["readiness_effect"] == "none"
    assert value["not_claimed"] == proof.EXPECTED_NOT_CLAIMED


def test_evidence_rejects_missing_case_or_invalid_source_binding() -> None:
    results = _results()
    with pytest.raises(proof.EgressBoundaryProofError, match="egress_case_set_invalid"):
        proof.build_evidence(
            captured_at=10_000,
            run_id="egress-proof-fixture",
            source_digests=_digests(),
            results=results[:-1],
            audit_records=_records(results),
            identity_checks={"missing_identity_denied": True, "wrong_audience_denied": True},
        )
    invalid = _digests()
    invalid.pop("allowlist_sha256")
    with pytest.raises(proof.EgressBoundaryProofError, match="source_binding_invalid"):
        proof.build_evidence(
            captured_at=10_000,
            run_id="egress-proof-fixture",
            source_digests=invalid,
            results=results,
            audit_records=_records(results),
            identity_checks={"missing_identity_denied": True, "wrong_audience_denied": True},
        )


def test_audit_binding_requires_each_case_and_resolver_provenance(tmp_path: Path) -> None:
    audit_root = tmp_path / security_audit.AUDIT_RELATIVE_ROOT
    audit_root.mkdir(parents=True)
    audit_root.chmod(0o700)
    run_id = "egress-proof-fixture"
    results = _results()
    records = _records(results, run_id)
    path = audit_root / "2026-07-16.jsonl"
    path.write_bytes(b"".join(security_audit.serialize_record(item) for item in records[:-1]))
    path.chmod(0o600)

    with pytest.raises(proof.EgressBoundaryProofError, match="audit_resolver_evidence_incomplete"):
        proof._bound_audit_records(
            tmp_path,
            run_id=run_id,
            sandbox_id="host-egress-proof",
            policy_digest="a" * 64,
            results=results,
        )


def test_outputs_are_atomic_regular_and_pass_sanitizer(tmp_path: Path) -> None:
    run_id = "egress-proof-fixture"
    results = _results()
    value = proof.build_evidence(
        captured_at=10_000,
        run_id=run_id,
        source_digests=_digests(),
        results=results,
        audit_records=_records(results, run_id),
        identity_checks={"missing_identity_denied": True, "wrong_audience_denied": True},
    )

    json_path, markdown_path = proof._write_outputs(tmp_path, value)

    for path in (json_path, markdown_path):
        info = path.lstat()
        assert stat.S_ISREG(info.st_mode)
        assert stat.S_IMODE(info.st_mode) == 0o644
    assert check_sanitized_artifacts.scan_paths([json_path, markdown_path]) == []


def test_run_case_discards_raw_response_material(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = proof._case_specs()[0]

    def fake_http(*_args, **_kwargs):
        return 200, {
            "ok": True,
            "status": 200,
            "body_base64": "c2Vuc2l0aXZlLWZpeHR1cmU=",
            "egress": {"rule_id": "unknown_safe_read", "decision": "allow"},
        }

    monkeypatch.setattr(proof, "_http_json", fake_http)
    result = proof._run_case(object(), "fixture-identity", spec)

    assert result.as_dict() == {
        "case_id": "public_get",
        "decision": "allow",
        "outer_http_status": 200,
        "rule_id": "unknown_safe_read",
        "upstream_http_status": 200,
    }
    assert "sensitive" not in json.dumps(result.as_dict())
