from __future__ import annotations

import hashlib
import json
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.openshell import (
    check_sanitized_artifacts,
    check_v06_completion,
    run_formal_egress_audit as runner,
    run_formal_filesystem_boundary as formal_binding,
    security_audit,
)

ROOT = Path(__file__).resolve().parents[3]
POLICY_SHA256 = "3" * 64
RUN_ID = "formal-fixture"
SANDBOX_NAME = "siq-analysis-formal-fixture"
BASE_TIME = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)


def _binding() -> formal_binding.ActiveBinding:
    return formal_binding.ActiveBinding(
        transaction_receipt_sha256="1" * 64,
        transaction_generation=7,
        manifest_sha256="2" * 64,
        sandbox_binding_sha256="3" * 64,
        host_receipt_sha256="4" * 64,
        run_id_sha256=hashlib.sha256(RUN_ID.encode()).hexdigest(),
        sandbox_id_sha256="5" * 64,
        container_id_sha256="6" * 64,
        session_id_sha256=hashlib.sha256(RUN_ID.encode()).hexdigest(),
        resource_receipts_sha256="7" * 64,
        image_sha256="8" * 64,
        policy_sha256=POLICY_SHA256,
        mount_plan_sha256="9" * 64,
        mount_contract_sha256="b" * 64,
        runtime_config_sha256="a" * 64,
    )


def _capture(root: Path) -> formal_binding.ActiveCapture:
    context = SimpleNamespace(sandbox_name=SANDBOX_NAME, project_root=root)
    return formal_binding.ActiveCapture(
        context=context,
        binding=_binding(),
        transaction_id="tx-formal-fixture",
        run_id=RUN_ID,
        sandbox_id="11111111-1111-1111-1111-111111111111",
        container_id="abcdef123456",
        analysis_relative_path="data/wiki/cn/companies/fixed/analysis",
    )


def _context() -> security_audit.SecurityRunContext:
    return security_audit.SecurityRunContext(
        profile="siq_analysis",
        sandbox_id=SANDBOX_NAME,
        run_id=RUN_ID,
        session_id=RUN_ID,
        policy_digest=POLICY_SHA256,
    )


def _record(
    index: int,
    *,
    operation_class: str,
    scope: str,
    decision: str,
    error_code: str = "",
    kind: str = "service",
) -> dict[str, object]:
    return security_audit.build_record(
        context=_context(),
        operation_class=operation_class,
        target=security_audit.project_target(kind=kind, scope=scope, value=f"fixed-{index}"),
        decision=decision,
        error_code=error_code,
        duration_ms=index,
        timestamp=BASE_TIME + timedelta(milliseconds=index),
    )


def _results_and_delta() -> tuple[list[runner.CaseResult], list[dict[str, object]]]:
    results = [
        runner.CaseResult(
            case_id=spec.case_id,
            decision=spec.decision,
            enforcement_layer=spec.enforcement_layer,
            reason_code=spec.reason_code,
            duration_ms=index,
            observation_contract=(
                "controlled_receiver_no_connection" if spec.executor == "direct" else "broker_decision_receipt"
            ),
        )
        for index, spec in enumerate(runner.case_specs(), start=1)
    ]
    records: list[dict[str, object]] = [
        _record(
            1,
            operation_class="sandbox.lifecycle",
            scope="formal_transaction.before",
            decision="allow",
        ),
        _record(2, operation_class="service.preflight", scope="formal_runtime.health", decision="allow"),
    ]
    next_index = 3
    for result in results:
        if result.enforcement_layer != "sandbox_network_policy" and next_index == 3:
            records.append(
                _record(
                    next_index,
                    operation_class="network.request",
                    scope=runner.RESOLVER_AUDIT_SCOPE,
                    decision="audit_only",
                    kind="host",
                )
            )
            next_index += 1
        scope = (
            f"egress.{result.reason_code}"
            if result.enforcement_layer != "sandbox_network_policy"
            else f"formal_egress.{result.case_id}"
        )
        records.append(
            _record(
                next_index,
                operation_class="network.request",
                scope=scope,
                decision=result.decision,
                error_code=result.reason_code if result.decision == "deny" else "",
                kind="host" if result.enforcement_layer != "sandbox_network_policy" else "service",
            )
        )
        next_index += 1
    records.append(
        _record(
            next_index,
            operation_class="sandbox.lifecycle",
            scope="formal_transaction.after",
            decision="allow",
        )
    )
    return results, records


def _copy_sources(root: Path) -> None:
    for relative in (
        runner.EGRESS_SCHEMA_RELATIVE,
        runner.AUDIT_SCHEMA_RELATIVE,
        runner.RUNNER_RELATIVE,
        runner.EGRESS_GUARD_RELATIVE,
        runner.REQUEST_IDENTITY_RELATIVE,
        runner.AUDIT_CONTRACT_RELATIVE,
        runner.AUDIT_AGGREGATOR_RELATIVE,
    ):
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((ROOT / relative).read_bytes())


def _host_component() -> dict[str, object]:
    return {
        "schema_version": "siq.openshell.egress-boundary-proof.v1",
        "scope": "host_egress_broker",
        "decision": "GO",
        "passed": True,
        "eligible_for_completion": False,
        "captured_at_unix": 1_000,
        "valid_until_unix": 2_000,
    }


def test_case_contract_matches_completion_and_real_broker_reason_codes() -> None:
    observed = {
        spec.case_id: (spec.decision, spec.enforcement_layer, spec.reason_code)
        for spec in runner.case_specs()
    }

    assert observed == check_v06_completion.FORMAL_EGRESS_CASES
    assert observed["unknown_multipart"][2] == "broker_multipart_denied"
    assert observed["unknown_octet_stream"][2] == "broker_octet_stream_denied"
    assert observed["unknown_put"][2] == "broker_method_denied"
    assert observed["oversized_unknown_body"][2] == "json_body_too_large"


def test_selects_exact_transaction_records_and_excludes_resolver_auxiliary(tmp_path: Path) -> None:
    capture = _capture(tmp_path)
    results, delta = _results_and_delta()

    selected, event_digests = runner._select_bound_records(delta, capture, results)

    assert len(selected) == 20
    assert len(event_digests) == 17
    assert all(len(value) == 64 for value in event_digests.values())
    assert runner.RESOLVER_AUDIT_SCOPE not in {record["target"]["scope"] for record in selected}


def test_selection_rejects_cross_transaction_and_unclassified_records(tmp_path: Path) -> None:
    capture = _capture(tmp_path)
    results, delta = _results_and_delta()
    delta[2] = {**delta[2], "policy_digest": "f" * 64}

    with pytest.raises(runner.FormalEgressAuditError, match="formal_audit_identity_mismatch"):
        runner._select_bound_records(delta, capture, results)

    _, clean = _results_and_delta()
    clean.append(
        _record(
            100,
            operation_class="database.query",
            scope="unexpected",
            decision="allow",
        )
    )
    with pytest.raises(runner.FormalEgressAuditError, match="formal_audit_unexpected_record"):
        runner._select_bound_records(clean, capture, results)


def test_builds_cross_bound_strict_sanitized_evidence(tmp_path: Path) -> None:
    _copy_sources(tmp_path)
    capture = _capture(tmp_path)
    results, delta = _results_and_delta()
    selected, event_digests = runner._select_bound_records(delta, capture, results)
    raw_audit = b"".join(security_audit.serialize_record(record) for record in selected)

    egress, audit = runner.build_evidence(
        root=tmp_path,
        generated_at="2026-07-16T12:01:00Z",
        before=capture,
        after=capture,
        host_component=_host_component(),
        host_component_digest="b" * 64,
        host_component_relative="artifacts/openshell/v0.6/egress-boundary.sanitized.json",
        results=results,
        selected_records=selected,
        event_digests=event_digests,
        raw_audit_sha256=hashlib.sha256(raw_audit).hexdigest(),
    )

    assert egress["transaction"] == audit["transaction"]
    assert egress["transfer_clients_tested"] == runner.TRANSFER_CLIENTS
    assert audit["source_contract"]["record_count"] == 20
    assert audit["decision_counts"] == {"allow": 5, "audit_only": 1, "deny": 14}
    assert audit["operation_counts"]["network.request"] == 17
    assert audit["operation_counts"]["runtime.route"] == 0
    assert audit["operation_counts"]["service.preflight"] == 1
    assert audit["event_classification"] == {
        "formal_runner_observation_count": 3,
        "security_probe_event_count": 17,
        "unclassified_count": 0,
    }
    assert audit["metrics"]["external_upload_blocks"] == 10
    assert audit["metrics"]["sandbox_start_failures"] == 0
    assert egress["provenance"]["exporter_sha256"] == audit["provenance"]["exporter_sha256"]
    serialized = json.dumps([egress, audit], ensure_ascii=True, sort_keys=True).encode("ascii")
    assert RUN_ID.encode() not in serialized
    assert SANDBOX_NAME.encode() not in serialized
    assert check_sanitized_artifacts.scan_content(Path("formal.sanitized.json"), serialized) == []


def test_controlled_receiver_detects_a_protocol_or_auth_reachable_connection() -> None:
    with runner.ControlledDenyReceiver("127.0.0.1") as receiver:
        connection = socket.create_connection(("127.0.0.1", receiver.port), timeout=1)
        connection.close()
        with pytest.raises(runner.FormalEgressAuditError, match="formal_direct_receiver_reached"):
            receiver.assert_unreached()


def test_build_rejects_exit_only_direct_case_claim(tmp_path: Path) -> None:
    _copy_sources(tmp_path)
    capture = _capture(tmp_path)
    results, delta = _results_and_delta()
    selected, event_digests = runner._select_bound_records(delta, capture, results)
    results[-1] = runner.CaseResult(
        **{
            **results[-1].__dict__,
            "observation_contract": "broker_decision_receipt",
        }
    )
    raw_audit = b"".join(security_audit.serialize_record(record) for record in selected)

    with pytest.raises(runner.FormalEgressAuditError, match="formal_egress_case_set_invalid"):
        runner.build_evidence(
            root=tmp_path,
            generated_at="2026-07-16T12:01:00Z",
            before=capture,
            after=capture,
            host_component=_host_component(),
            host_component_digest="b" * 64,
            host_component_relative="artifacts/openshell/v0.6/egress-boundary.sanitized.json",
            results=results,
            selected_records=selected,
            event_digests=event_digests,
            raw_audit_sha256=hashlib.sha256(raw_audit).hexdigest(),
        )


def test_network_contract_rejects_transfer_binary_allow_rule(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = {
        "network_policies": {
            "siq_data_broker": {
                "endpoints": [{"host": "host.openshell.internal", "port": 18793, "allowed_ips": ["172.23.0.1/32"]}],
                "binaries": [{"path": "/opt/siq/hermes/venv/bin/python"}],
            },
            "siq_egress_guard": {
                "endpoints": [{"host": "host.openshell.internal", "port": 18792, "allowed_ips": ["172.23.0.1/32"]}],
                "binaries": [{"path": "/usr/bin/curl"}, {"path": "/usr/bin/rclone"}],
            },
            "siq_internal_services": {
                "endpoints": [
                    {"host": "host.openshell.internal", "port": port, "allowed_ips": ["172.23.0.1/32"]}
                    for port in (8004, 8006, 8007, 8013)
                ],
                "binaries": [{"path": "/opt/siq/hermes/venv/bin/python"}],
            },
        }
    }
    capture = _capture(tmp_path)
    capture.context.policy = policy
    monkeypatch.setattr(runner.sandbox_probe, "_validate_active_policy", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runner.sandbox_probe, "_docker_inspect_mounts", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        runner.sandbox_probe,
        "validate_container_mounts",
        lambda *_args, **_kwargs: {"business_mount_count": 7, "control_mount_count": 5, "total_mount_count": 12},
    )
    monkeypatch.setattr(
        runner.bridge_endpoint,
        "discover_bridge_endpoint",
        lambda: SimpleNamespace(gateway_ip="172.23.0.1"),
    )

    with pytest.raises(runner.FormalEgressAuditError, match="formal_network_policy_invalid"):
        runner._validate_network_contract(capture, timeout=10)
