from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.openshell import check_siq_services as module


def _proofs(*, postgres: bool = True, milvus: bool = True) -> dict[str, dict[str, object]]:
    return {
        "postgres_readonly_identity": {"proven": postgres, "source": "cli" if postgres else "none"},
        "milvus_write_protection": {
            "proven": milvus,
            "source": "milvus_proof_file" if milvus else "none",
            "schema_version": module.milvus_proof.SCHEMA_VERSION if milvus else "",
        },
    }


def _valid_milvus_proof(_path: Path, **_kwargs: object) -> dict[str, object]:
    return {"schema_version": module.milvus_proof.SCHEMA_VERSION, "passed": True}


def _probe_with_unreachable(*ports: int):
    unreachable = set(ports)

    def probe(_host: str, port: int, _timeout: float) -> module.ProbeOutcome:
        if port in unreachable:
            return module.ProbeOutcome(False, "connection_refused", 1)
        return module.ProbeOutcome(True, "", 1)

    return probe


def _protocol_pass(_host: str, spec: module.ServiceSpec, _timeout: float) -> module.ProtocolOutcome:
    if not spec.protocol_contract:
        return module.ProtocolOutcome(False, True, "", 0, None)
    return module.ProtocolOutcome(True, True, "", 1, 200)


def test_service_contract_uses_actual_ports_and_explicit_requirement_classes() -> None:
    by_id = {spec.service_id: spec for spec in module.SERVICE_SPECS}

    assert {spec.port for spec in module.SERVICE_SPECS} == {
        8004,
        8006,
        8007,
        8013,
        15432,
        19530,
        18081,
        18651,
    }
    assert by_id["postgres"].port == 15432
    assert {service_id for service_id, spec in by_id.items() if spec.requirement == "optional"} == {
        "qwen_local",
        "gemma_local",
        "nemotron_local",
    }


def test_all_required_services_and_security_proofs_produce_go() -> None:
    report = module.build_report(
        host_alias="host.openshell.internal",
        timeout_seconds=0.2,
        proofs=_proofs(),
        probe=_probe_with_unreachable(8004, 8006, 8007),
        protocol_probe=_protocol_pass,
    )

    assert report["decision"] == "GO"
    assert report["passed"] is True
    assert report["summary"] == {
        "required_total": 5,
        "required_reachable": 5,
        "optional_total": 3,
        "optional_reachable": 0,
        "required_protocol_total": 3,
        "required_protocol_available": 3,
        "optional_protocol_total": 3,
        "optional_protocol_available": 0,
        "security_proofs_required": 2,
        "security_proofs_present": 2,
        "blocking_count": 0,
        "warning_count": 3,
    }
    assert {item["port"] for item in report["warnings"]} == {8004, 8006, 8007}
    assert "host.openshell.internal" not in json.dumps(report)
    assert report["services"][2]["protocol_check"]["status"] == "not_run"


@pytest.mark.parametrize(
    ("port", "expected_code"),
    [
        (8013, "embedding_service_unreachable"),
    ],
)
def test_missing_required_local_model_services_are_explicit_no_go(port: int, expected_code: str) -> None:
    report = module.build_report(
        host_alias="127.0.0.1",
        timeout_seconds=0.1,
        proofs=_proofs(),
        probe=_probe_with_unreachable(port),
        protocol_probe=_protocol_pass,
    )

    assert report["decision"] == "NO_GO"
    blocker = next(item for item in report["blockers"] if item["error_code"] == expected_code)
    assert blocker == {
        "check_id": next(
            f"service:{spec.service_id}" for spec in module.SERVICE_SPECS if spec.blocker_code == expected_code
        ),
        "kind": "service_connectivity",
        "error_code": expected_code,
        "port": port,
    }


def test_reachable_databases_still_fail_without_explicit_readonly_and_write_protection_proofs() -> None:
    report = module.build_report(
        host_alias="127.0.0.1",
        timeout_seconds=0.1,
        proofs=_proofs(postgres=False, milvus=False),
        probe=_probe_with_unreachable(),
        protocol_probe=_protocol_pass,
    )

    assert report["decision"] == "NO_GO"
    assert {item["error_code"] for item in report["blockers"]} == {
        "postgres_readonly_identity_unproven",
        "milvus_anonymous_write_not_excluded",
    }
    assert all(check["status"] == "no_go" for check in report["security_checks"])


def test_unrecognized_proof_source_fails_closed_and_is_not_echoed() -> None:
    report = module.build_report(
        host_alias="127.0.0.1",
        timeout_seconds=0.1,
        proofs={
            "postgres_readonly_identity": {"proven": True, "source": "unique-secret"},
            "milvus_write_protection": {"proven": True, "source": "unique-secret"},
        },
        probe=_probe_with_unreachable(),
        protocol_probe=_protocol_pass,
    )

    assert report["decision"] == "NO_GO"
    assert {check["proof_source"] for check in report["security_checks"]} == {"none"}
    assert "unique-secret" not in json.dumps(report)


def test_postgres_cli_and_strict_milvus_file_produce_go_when_services_are_reachable(monkeypatch, capsys) -> None:
    monkeypatch.setattr(module, "tcp_probe", _probe_with_unreachable(8007))
    monkeypatch.setattr(module, "http_protocol_probe", _protocol_pass)
    monkeypatch.setattr(module.milvus_proof, "validate_consumable_proof", _valid_milvus_proof)

    exit_code = module.main(
        [
            "--postgres-readonly-proof",
            "--milvus-proof-file",
            "/not-read-by-validator.json",
            "--json",
        ]
    )

    report = json.loads(capsys.readouterr().out)
    assert exit_code == module.EXIT_GO
    assert report["decision"] == "GO"
    assert {check["proof_source"] for check in report["security_checks"]} == {"cli", "proof_file"}


def test_legacy_milvus_cli_assertion_is_rejected(capsys) -> None:
    assert module.main(["--milvus-write-protection-proof", "--json"]) == module.EXIT_CONFIGURATION_ERROR
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "milvus_cli_proof_unsupported" in captured.err


def test_legacy_file_only_proves_postgres_and_detailed_file_proves_milvus(tmp_path: Path) -> None:
    proof_file = tmp_path / "proofs.json"
    proof_file.write_text(
        json.dumps(
            {
                "schema_version": module.PROOF_SCHEMA_VERSION,
                "postgres_readonly_identity": True,
                "milvus_write_protection": True,
            }
        ),
        encoding="utf-8",
    )

    proofs = module.resolve_security_proofs(
        proof_file=proof_file,
        postgres_cli_proof=False,
        milvus_cli_proof=False,
        milvus_proof_file=tmp_path / "milvus.json",
        milvus_validator=_valid_milvus_proof,
    )

    assert proofs == {
        "postgres_readonly_identity": {"proven": True, "source": "proof_file"},
        "milvus_write_protection": {
            "proven": True,
            "source": "milvus_proof_file",
            "schema_version": module.milvus_proof.SCHEMA_VERSION,
        },
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"schema_version": module.PROOF_SCHEMA_VERSION},
        {
            "schema_version": module.PROOF_SCHEMA_VERSION,
            "postgres_readonly_identity": True,
            "milvus_write_protection": True,
            "database_url": "postgresql://reader:unique-secret@db/siq",
        },
        {
            "schema_version": "wrong",
            "postgres_readonly_identity": True,
            "milvus_write_protection": True,
        },
    ],
)
def test_invalid_proof_file_fails_with_generic_error_without_echoing_content(
    payload: dict[str, object],
    tmp_path: Path,
    capsys,
) -> None:
    proof_file = tmp_path / "proof-unique-secret.json"
    proof_file.write_text(json.dumps(payload), encoding="utf-8")

    exit_code = module.main(["--proof-file", str(proof_file), "--json"])
    captured = capsys.readouterr()

    assert exit_code == module.EXIT_CONFIGURATION_ERROR
    assert captured.out == ""
    assert "proof_file_invalid" in captured.err
    assert "unique-secret" not in captured.err
    assert "postgresql://" not in captured.err


def test_invalid_host_alias_and_timeout_do_not_echo_the_supplied_value(capsys) -> None:
    assert module.main(["--host-alias", "https://user:unique-secret@host", "--json"]) == 2
    first = capsys.readouterr()
    assert "host_alias_invalid" in first.err
    assert "unique-secret" not in first.err

    assert module.main(["--timeout", "60", "--json"]) == 2
    second = capsys.readouterr()
    assert "timeout_out_of_range" in second.err
    assert "60" not in second.err

    assert module.main(["--timeout", "postgresql://reader:unique-secret@db", "--json"]) == 2
    third = capsys.readouterr()
    assert "timeout_invalid" in third.err
    assert "unique-secret" not in third.err
    assert "postgresql://" not in third.err


def test_probe_exception_message_and_environment_values_never_enter_report(monkeypatch, capsys) -> None:
    def raising_probe(_host: str, _port: int, _timeout: float) -> module.ProbeOutcome:
        raise OSError("postgresql://reader:unique-secret@db/siq")

    monkeypatch.setattr(module, "tcp_probe", raising_probe)
    monkeypatch.setattr(module, "http_protocol_probe", _protocol_pass)
    monkeypatch.setattr(module.milvus_proof, "validate_consumable_proof", _valid_milvus_proof)
    monkeypatch.setenv("SIQ_APP_DATABASE_URL", "postgresql://reader:environment-secret@db/siq")

    exit_code = module.main(
        [
            "--postgres-readonly-proof",
            "--milvus-proof-file",
            "/not-read-by-validator.json",
            "--json",
        ]
    )
    output = capsys.readouterr().out
    report = json.loads(output)

    assert exit_code == module.EXIT_NO_GO
    assert {service["error_code"] for service in report["services"]} == {"probe_failed"}
    assert "unique-secret" not in output
    assert "environment-secret" not in output
    assert "postgresql://" not in output


def test_probe_contract_rejects_unbounded_error_text_without_echoing_it() -> None:
    def unsafe_probe(_host: str, _port: int, _timeout: float) -> module.ProbeOutcome:
        return module.ProbeOutcome(False, "postgresql://reader:unique-secret@db/siq", 1)

    report = module.build_report(
        host_alias="127.0.0.1",
        timeout_seconds=0.1,
        proofs=_proofs(),
        probe=unsafe_probe,
        protocol_probe=_protocol_pass,
    )

    serialized = json.dumps(report)
    assert {service["error_code"] for service in report["services"]} == {"probe_contract_invalid"}
    assert "unique-secret" not in serialized
    assert "postgresql://" not in serialized


def test_probe_receives_validated_alias_ports_and_short_timeout() -> None:
    calls: list[tuple[str, int, float]] = []

    def probe(host: str, port: int, timeout: float) -> module.ProbeOutcome:
        calls.append((host, port, timeout))
        return module.ProbeOutcome(True, "", 0)

    module.build_report(
        host_alias="HOST.OPENshell.Internal",
        timeout_seconds=0.25,
        proofs=_proofs(),
        probe=probe,
        protocol_probe=_protocol_pass,
    )

    assert sorted(calls) == sorted(("host.openshell.internal", spec.port, 0.25) for spec in module.SERVICE_SPECS)


def test_http_protocol_probe_accepts_only_minimal_openai_models_shape(monkeypatch) -> None:
    class Response:
        status = 200

        def getheader(self, name: str) -> str:
            assert name == "Content-Type"
            return "application/json; charset=utf-8"

        def read(self, _limit: int) -> bytes:
            return b'{"object":"list","data":[{"id":"model-secret-name"}]}'

    class Connection:
        def __init__(self, host: str, port: int, timeout: float) -> None:
            assert host == "127.0.0.1"
            assert port == 8007
            assert timeout == 0.2

        def request(self, method: str, path: str, *, headers: dict[str, str]) -> None:
            assert method == "GET"
            assert path == "/v1/models"
            assert headers["Connection"] == "close"
            assert headers["Accept"] == "application/json"

        def getresponse(self) -> Response:
            return Response()

        def close(self) -> None:
            return None

    monkeypatch.setattr(module.http.client, "HTTPConnection", Connection)
    spec = next(item for item in module.SERVICE_SPECS if item.service_id == "nemotron_local")
    outcome = module.http_protocol_probe("127.0.0.1", spec, 0.2)
    assert outcome == module.ProtocolOutcome(True, True, "", outcome.latency_ms, 200)


def test_protocol_failure_is_distinct_from_tcp_failure_and_does_not_echo_response() -> None:
    def protocol_probe(_host: str, spec: module.ServiceSpec, _timeout: float) -> module.ProtocolOutcome:
        if spec.service_id == "qwen_local":
            return module.ProtocolOutcome(True, False, "response_contract_invalid", 1, 200)
        return _protocol_pass(_host, spec, _timeout)

    report = module.build_report(
        host_alias="127.0.0.1",
        timeout_seconds=0.1,
        proofs=_proofs(),
        probe=_probe_with_unreachable(),
        protocol_probe=protocol_probe,
    )
    warning = next(item for item in report["warnings"] if item["check_id"] == "service:qwen_local")
    assert warning["kind"] == "optional_service_protocol"
    assert warning["error_code"] == "qwen_local_protocol_unavailable"
    assert report["services"][0]["protocol_check"]["error_code"] == "response_contract_invalid"
    assert "model-secret-name" not in json.dumps(report)


def test_protocol_probe_exception_message_never_enters_report() -> None:
    def protocol_probe(_host: str, _spec: module.ServiceSpec, _timeout: float) -> module.ProtocolOutcome:
        raise OSError("https://user:unique-secret@example.invalid/private")

    report = module.build_report(
        host_alias="127.0.0.1",
        timeout_seconds=0.1,
        proofs=_proofs(),
        probe=_probe_with_unreachable(),
        protocol_probe=protocol_probe,
    )
    serialized = json.dumps(report)
    assert report["decision"] == "NO_GO"
    assert "unique-secret" not in serialized
    assert "example.invalid" not in serialized
    assert {
        item["protocol_check"]["error_code"]
        for item in report["services"]
        if item["protocol_check"]["contract"] != "not_applicable"
    } == {"protocol_probe_failed"}


def test_explicit_report_export_is_atomic_and_requires_replace(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(module, "tcp_probe", _probe_with_unreachable(8013))
    monkeypatch.setattr(module, "http_protocol_probe", _protocol_pass)
    monkeypatch.setattr(module.milvus_proof, "validate_consumable_proof", _valid_milvus_proof)
    output = tmp_path / "service.json"
    markdown = tmp_path / "service.md"
    args = [
        "--postgres-readonly-proof",
        "--milvus-proof-file",
        "/not-read-by-validator.json",
        "--output",
        str(output),
        "--markdown-output",
        str(markdown),
        "--json",
    ]
    assert module.main(args) == module.EXIT_NO_GO
    assert json.loads(output.read_text(encoding="utf-8"))["schema_version"] == module.SCHEMA_VERSION
    assert "model-secret" not in markdown.read_text(encoding="utf-8")
    assert module.main(args) == module.EXIT_CONFIGURATION_ERROR
    assert "output_exists" in capsys.readouterr().err
    assert module.main([*args, "--replace"]) == module.EXIT_NO_GO
