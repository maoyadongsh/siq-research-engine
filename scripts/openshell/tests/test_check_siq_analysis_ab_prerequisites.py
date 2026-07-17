from __future__ import annotations

import hashlib
import json
import os
import stat
import time
from pathlib import Path

import pytest

from scripts.openshell import check_siq_analysis_ab_prerequisites as module, run_siq_analysis_ab_eval as ab_eval

ROOT = Path(__file__).resolve().parents[3]


def _write(path: Path, content: str, mode: int = 0o600) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(mode)
    return path


def _source_binding(path: Path) -> dict[str, object]:
    info = path.stat()
    return {
        "path": str(path.resolve()),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "size_bytes": info.st_size,
        "device": info.st_dev,
        "inode": info.st_ino,
        "mode": stat.S_IMODE(info.st_mode),
        "mtime_ns": info.st_mtime_ns,
        "ctime_ns": info.st_ctime_ns,
    }


@pytest.fixture(autouse=True)
def _stub_live_host_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    def verify(**kwargs):
        return json.loads(Path(kwargs["host_runtime_receipt_path"]).read_text(encoding="utf-8"))

    monkeypatch.setattr(module.ab_prepare, "verify_host_runtime_receipts", verify)


def _dataset(path: Path) -> Path:
    cases = []
    for index in range(5):
        cases.append(
            {
                "case_id": f"case-normal-{index + 1}",
                "input": f"reviewed input normal {index + 1}",
                "history": [],
                "expectations": {
                    "numeric": [{"expectation_id": f"value-{index + 1}", "value": 1, "absolute_tolerance": 0}],
                    "citations": [f"CIT-{index + 1}"],
                    "evidence_ids": [f"EVID-{index + 1}"],
                    "required_sections": ["Executive Summary"],
                    "abstention_required": False,
                    "abstention_markers": [],
                    "required_tools": ["pg_query"],
                    "fallback_expected": None,
                    "policy_denial_expected": False,
                },
            }
        )
    for index in range(5):
        cases.append(
            {
                "case_id": f"case-fallback-{index + 1}",
                "input": f"reviewed input fallback {index + 1}",
                "history": [],
                "expectations": {
                    "numeric": [{"expectation_id": f"fallback-value-{index + 1}", "value": 1, "absolute_tolerance": 0}],
                    "citations": [f"F-CIT-{index + 1}"],
                    "evidence_ids": [f"F-EVID-{index + 1}"],
                    "required_sections": ["Risk"],
                    "abstention_required": True,
                    "abstention_markers": ["insufficient evidence"],
                    "required_tools": ["pg_query"],
                    "fallback_expected": None,
                    "policy_denial_expected": False,
                },
            }
        )
    return _write(
        path,
        json.dumps(
            {
                "schema_version": ab_eval.DATASET_SCHEMA_VERSION,
                "profile": "siq_analysis",
                "model": "reviewed-model",
                "temperature": 0.1,
                "instructions": "reviewed instructions",
                "repetitions": 3,
                "run_timeout_seconds": 30,
                "cases": cases,
            }
        ),
    )


def _fixtures(tmp_path: Path) -> dict[str, Path]:
    host_key = _write(tmp_path / "host.key", "host-key-value-0001\n")
    open_key = _write(tmp_path / "open.key", "openshell-key-value-0001\n")
    dataset = _dataset(tmp_path / "dataset.json")
    common = {
        "hermes_commit": module.HERMES_COMMIT,
        "profile_sha256": "1" * 64,
        "model_route_sha256": "2" * 64,
        "tools_sha256": "3" * 64,
        "data_snapshot_sha256": "4" * 64,
    }
    source_bindings = {}
    for index, name in enumerate(sorted(module.ab_prepare.PROVENANCE_SOURCE_NAMES), start=1):
        source = _write(tmp_path / "provenance-sources" / f"{name}.json", json.dumps({"source": index}))
        source_bindings[name] = _source_binding(source)
    candidate_manifest = (
        f"{source_bindings['candidate_api_server']['sha256']}  ./hermes-agent/gateway/platforms/api_server.py\n"
        f"{source_bindings['candidate_run_agent']['sha256']}  ./hermes-agent/run_agent.py\n"
    ).encode("ascii")
    candidate_manifest_path = Path(source_bindings["candidate_files_manifest"]["path"])
    candidate_manifest_path.write_bytes(candidate_manifest)
    source_bindings["candidate_files_manifest"] = _source_binding(candidate_manifest_path)
    context_sha256 = hashlib.sha256(candidate_manifest).hexdigest()
    runtime_contract_sha256 = "e" * 64
    runtime_receipt_path = Path(source_bindings["host_runtime_receipt"]["path"])
    runtime_receipt_path.write_text(
        json.dumps(
            {
                "listener": {
                    "api_server_sha256": source_bindings["candidate_api_server"]["sha256"],
                    "run_agent_sha256": source_bindings["candidate_run_agent"]["sha256"],
                },
                "capabilities": {
                    "document_sha256": runtime_contract_sha256,
                    "run_runtime_metadata_v1": True,
                },
            }
        ),
        encoding="utf-8",
    )
    source_bindings["host_runtime_receipt"] = _source_binding(runtime_receipt_path)
    provenance = _write(
        tmp_path / "provenance.json",
        json.dumps(
            {
                "schema_version": module.PROVENANCE_SCHEMA_VERSION,
                "evaluation_id": "eval-20260716-a",
                "profile": "siq_analysis",
                "dataset_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
                "arms": {
                    "host": {
                        "runtime": "host",
                        **common,
                        "host_key_receipt_sha256": source_bindings["host_key_receipt"]["sha256"],
                        "host_runtime_receipt_sha256": source_bindings["host_runtime_receipt"]["sha256"],
                        "runtime_contract_sha256": runtime_contract_sha256,
                    },
                    "openshell": {
                        "runtime": "openshell",
                        **common,
                        "image_id": "sha256:" + "5" * 64,
                        "policy_sha256": "6" * 64,
                        "mount_plan_sha256": "7" * 64,
                        "mount_contract_sha256": "9" * 64,
                        "runtime_config_sha256": "8" * 64,
                    },
                },
                "runtime_attestation": {
                    "context_sha256": context_sha256,
                    "hermes_patch_sha256": "b" * 64,
                    "source_config_sha256": "c" * 64,
                    "compiled_config_sha256": "8" * 64,
                    "primary_provider": "minimax-cn",
                    "primary_model": "reviewed-model",
                    "fallback_route_sha256": "d" * 64,
                    "temperature_kind": "explicit",
                    "request_temperature": 0.1,
                    "host_runtime_metadata_v1": True,
                    "host_candidate_source_match": True,
                    "arms_match": True,
                },
                "sources": source_bindings,
            }
        ),
    )
    provider = _write(
        tmp_path / "providers.json",
        json.dumps(
                [
                    {"name": name, "state": "ready"}
                    for name in (
                        "siq-minimax-cn-pool",
                        "siq-stepfun",
                        "siq-kimi-coding",
                        "siq-tavily-search",
                    )
                ]
        ),
    )
    provider_payload = {
        "schema_version": "siq.openshell.provider_inventory.v1",
        "openshell_version": "0.0.83",
        "gateway": "siq-openshell-dev",
        "providers": json.loads(provider.read_text(encoding="utf-8")),
    }
    provider.write_text(json.dumps(provider_payload), encoding="utf-8")
    services = []
    for service_id, (port, required) in module.SERVICE_CONTRACT.items():
        expected_protocol = module.SERVICE_PROTOCOL_CONTRACT.get(service_id)
        services.append(
            {
                "service_id": service_id,
                "port": port,
                "requirement": "required" if required else "optional",
                "blocking": required,
                "reachable": True,
                "status": "pass",
                "error_code": "",
                "protocol_check": {
                    "contract": expected_protocol[0] if expected_protocol else "not_applicable",
                    "method": "GET" if expected_protocol else "",
                    "path": expected_protocol[1] if expected_protocol else "",
                    "checked": bool(expected_protocol),
                    "available": True if expected_protocol else None,
                    "status": "pass" if expected_protocol else "not_applicable",
                },
            }
        )
    service = _write(
        tmp_path / "service.json",
        json.dumps(
            {
                "schema_version": "siq.openshell.service_preflight.v2",
                "decision": "GO",
                "passed": True,
                "probe_scope": {
                    "protocol": "tcp_connect_plus_read_only_http_get",
                    "read_only": True,
                    "host_alias_kind": "loopback",
                    "http_method": "GET",
                    "request_body_sent": False,
                    "redirects_followed": False,
                    "response_body_recorded": False,
                },
                "services": services,
                "security_checks": [
                    {
                        "check_id": "postgres_readonly_identity",
                        "status": "pass",
                        "proof_present": True,
                        "proof_source": "proof_file",
                    },
                    {
                        "check_id": "milvus_write_protection",
                        "status": "pass",
                        "proof_present": True,
                        "proof_source": "proof_file",
                    },
                ],
                "summary": {
                    "required_total": 5,
                    "required_reachable": 5,
                    "required_protocol_total": 3,
                    "required_protocol_available": 3,
                },
            }
        ),
    )
    broker = _write(
        tmp_path / "broker.json",
        json.dumps(
            {
                "schema_version": "siq.openshell.broker-lifecycle.v1",
                "ok": True,
                "bridge": {"network": "siq-openshell-dev", "alias": "host.openshell.internal"},
                "brokers": {
                    "egress": {
                        "state": "running",
                        "port": module.BROKER_CONTRACT["egress"],
                        "request_identity_required": True,
                    },
                    "data": {
                        "state": "running",
                        "port": module.BROKER_CONTRACT["data"],
                        "request_identity_required": True,
                    },
                },
            }
        ),
    )
    return {
        "host_key": host_key,
        "open_key": open_key,
        "dataset": dataset,
        "provenance": provenance,
        "provider": provider,
        "service": service,
        "broker": broker,
    }


def _report(fixtures: dict[str, Path], **overrides):
    values = {
        "host_runs_url": "http://127.0.0.1:18651/v1/runs",
        "openshell_runs_url": "http://127.0.0.1:28651/v1/runs",
        "host_api_key_file": fixtures["host_key"],
        "openshell_api_key_file": fixtures["open_key"],
        "dataset_file": fixtures["dataset"],
        "evaluation_id": "eval-20260716-a",
        "provenance_report": fixtures["provenance"],
        "provider_inventory": fixtures["provider"],
        "service_report": fixtures["service"],
        "broker_report": fixtures["broker"],
    }
    values.update(overrides)
    return module.build_report(**values)


def test_real_prerequisite_contract_passes_without_network_probe(tmp_path: Path) -> None:
    fixtures = _fixtures(tmp_path)

    report = _report(fixtures)

    assert report["decision"] == "GO"
    assert report["schema_version"] == module.SCHEMA_VERSION
    assert report["network_probe_performed"] is True
    assert report["cutover_performed"] is False
    assert report["host"]["analysis_port"] == 18651
    assert report["dataset"]["case_count"] == 10
    assert report["key_fingerprints"]["host"] != report["key_fingerprints"]["openshell"]
    assert set(report["evidence"]) == {"provider_inventory", "service_report", "broker_report"}
    evidence_sources = {
        "provider_inventory": fixtures["provider"],
        "service_report": fixtures["service"],
        "broker_report": fixtures["broker"],
    }
    for name, binding in report["evidence"].items():
        assert set(binding) == module.EVIDENCE_BINDING_FIELDS
        assert Path(binding["path"]) == evidence_sources[name]
        assert binding["sha256"] == hashlib.sha256(Path(binding["path"]).read_bytes()).hexdigest()

    prerequisites = _write(tmp_path / "prerequisites.json", json.dumps(report))
    validated, digest = module.validate_report_for_evaluation(
        prerequisites,
        evaluation_id="eval-20260716-a",
        dataset_sha256=hashlib.sha256(fixtures["dataset"].read_bytes()).hexdigest(),
        host_runs_url="http://127.0.0.1:18651/v1/runs",
        openshell_runs_url="http://127.0.0.1:28651/v1/runs",
        host_key_fingerprint=report["key_fingerprints"]["host"],
        openshell_key_fingerprint=report["key_fingerprints"]["openshell"],
    )
    assert validated == report
    assert digest == hashlib.sha256(prerequisites.read_bytes()).hexdigest()


def test_assistant_port_cannot_be_used_as_analysis_baseline(tmp_path: Path) -> None:
    fixtures = _fixtures(tmp_path)

    report = _report(fixtures, host_runs_url="http://127.0.0.1:18642/v1/runs")

    assert report["decision"] == "NO_GO"
    assert "host_port_forbidden" in report["blockers"]


def test_openshell_formal_endpoint_is_fixed_to_loopback_28651(tmp_path: Path) -> None:
    fixtures = _fixtures(tmp_path)

    report = _report(fixtures, openshell_runs_url="http://127.0.0.1:29001/v1/runs")

    assert report["decision"] == "NO_GO"
    assert "openshell_analysis_port_must_be_28651" in report["blockers"]


def test_public_or_https_runs_url_is_rejected_before_any_key_use(tmp_path: Path) -> None:
    fixtures = _fixtures(tmp_path)

    report = _report(fixtures, host_runs_url="https://example.invalid:18651/v1/runs")

    assert report["decision"] == "NO_GO"
    assert "host_url_invalid" in report["blockers"]


def test_missing_provider_and_service_go_block_a_b(tmp_path: Path) -> None:
    fixtures = _fixtures(tmp_path)
    provider = json.loads(fixtures["provider"].read_text(encoding="utf-8"))
    provider["providers"] = [{"name": "siq-stepfun", "state": "ready"}]
    fixtures["provider"].write_text(json.dumps(provider), encoding="utf-8")
    service = json.loads(fixtures["service"].read_text(encoding="utf-8"))
    service["decision"] = "NO_GO"
    service["passed"] = False
    fixtures["service"].write_text(json.dumps(service), encoding="utf-8")

    report = _report(fixtures)

    assert report["decision"] == "NO_GO"
    assert "required_providers_missing" in report["blockers"]
    assert "service_preflight_not_go" in report["blockers"]


def test_required_service_protocol_contract_must_be_available(tmp_path: Path) -> None:
    fixtures = _fixtures(tmp_path)
    service = json.loads(fixtures["service"].read_text(encoding="utf-8"))
    embedding = next(item for item in service["services"] if item["service_id"] == "embedding")
    embedding["status"] = "no_go"
    embedding["protocol_check"].update({"available": False, "status": "no_go"})
    fixtures["service"].write_text(json.dumps(service), encoding="utf-8")

    report = _report(fixtures)

    assert report["decision"] == "NO_GO"
    assert "service_preflight_not_go" in report["blockers"]


def test_keys_must_be_private_and_distinct(tmp_path: Path) -> None:
    fixtures = _fixtures(tmp_path)
    fixtures["open_key"].write_text("host-key-value-0001\n", encoding="utf-8")
    fixtures["open_key"].chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP)

    report = _report(fixtures)

    assert report["decision"] == "NO_GO"
    assert "input_file_permissions_invalid" in report["blockers"]


def test_key_contract_matches_evaluator_length_and_ascii_rules(tmp_path: Path) -> None:
    fixtures = _fixtures(tmp_path)
    fixtures["host_key"].write_text("too-short\n", encoding="utf-8")

    report = _report(fixtures)

    assert report["decision"] == "NO_GO"
    assert "api_key_file_invalid" in report["blockers"]


def test_provenance_requires_identical_business_inputs_across_arms(tmp_path: Path) -> None:
    fixtures = _fixtures(tmp_path)
    provenance = json.loads(fixtures["provenance"].read_text(encoding="utf-8"))
    provenance["arms"]["openshell"]["tools_sha256"] = "9" * 64
    fixtures["provenance"].write_text(json.dumps(provenance), encoding="utf-8")

    report = _report(fixtures)

    assert report["decision"] == "NO_GO"
    assert "ab_provenance_arms_mismatch" in report["blockers"]


def test_missing_host_runtime_capability_blocks_prerequisite_without_model_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixtures = _fixtures(tmp_path)

    def reject(**_kwargs):
        raise module.ab_prepare.PreparationError("host_runtime_metadata_capability_missing")

    monkeypatch.setattr(module.ab_prepare, "verify_host_runtime_receipts", reject)
    report = _report(fixtures)

    assert report["decision"] == "NO_GO"
    assert report["network_probe_performed"] is False
    assert "host_runtime_metadata_capability_missing" in report["blockers"]


def test_evaluator_preflight_rechecks_host_runtime_after_go_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixtures = _fixtures(tmp_path)
    report = _report(fixtures)
    prerequisites = _write(tmp_path / "prerequisites.json", json.dumps(report))

    def drift(**_kwargs):
        raise module.ab_prepare.PreparationError("host_runtime_receipt_drift")

    monkeypatch.setattr(module.ab_prepare, "verify_host_runtime_receipts", drift)
    with pytest.raises(module.PrerequisiteError, match="host_runtime_receipt_drift"):
        module.validate_report_for_evaluation(
            prerequisites,
            evaluation_id="eval-20260716-a",
            dataset_sha256=hashlib.sha256(fixtures["dataset"].read_bytes()).hexdigest(),
            host_runs_url="http://127.0.0.1:18651/v1/runs",
            openshell_runs_url="http://127.0.0.1:28651/v1/runs",
            host_key_fingerprint=report["key_fingerprints"]["host"],
            openshell_key_fingerprint=report["key_fingerprints"]["openshell"],
        )


def test_provenance_schema_pins_the_reviewed_hermes_commit() -> None:
    schema = json.loads(
        (ROOT / "infra/openshell/schemas/siq-analysis-ab-provenance.schema.json").read_text(encoding="utf-8")
    )

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["properties"]["schema_version"]["const"] == module.PROVENANCE_SCHEMA_VERSION
    assert schema["$defs"]["hermesCommit"]["const"] == module.HERMES_COMMIT


def test_stale_external_reports_fail_closed(tmp_path: Path) -> None:
    fixtures = _fixtures(tmp_path)
    stale = time.time() - module.EVIDENCE_MAX_AGE_SECONDS["broker_report"] - 10
    os.utime(fixtures["broker"], (stale, stale))

    report = _report(fixtures)

    assert report["decision"] == "NO_GO"
    assert "broker_report_invalid_stale" in report["blockers"]


def test_evaluator_rejects_bound_report_drift_and_legacy_contract(tmp_path: Path) -> None:
    fixtures = _fixtures(tmp_path)
    report = _report(fixtures)
    prerequisites = _write(tmp_path / "prerequisites.json", json.dumps(report))
    validation = {
        "evaluation_id": "eval-20260716-a",
        "dataset_sha256": hashlib.sha256(fixtures["dataset"].read_bytes()).hexdigest(),
        "host_runs_url": "http://127.0.0.1:18651/v1/runs",
        "openshell_runs_url": "http://127.0.0.1:28651/v1/runs",
        "host_key_fingerprint": report["key_fingerprints"]["host"],
        "openshell_key_fingerprint": report["key_fingerprints"]["openshell"],
    }

    provider = json.loads(fixtures["provider"].read_text(encoding="utf-8"))
    fixtures["provider"].write_text(json.dumps(provider, indent=2), encoding="utf-8")
    with pytest.raises(module.PrerequisiteError, match="provider_inventory_binding_drift"):
        module.validate_report_for_evaluation(prerequisites, **validation)

    fixtures = _fixtures(tmp_path / "legacy")
    legacy = _report(fixtures)
    legacy["schema_version"] = "siq.openshell.siq-analysis-ab-prerequisites.v2"
    legacy_path = _write(tmp_path / "legacy-prerequisites.json", json.dumps(legacy))
    validation.update(
        {
            "dataset_sha256": hashlib.sha256(fixtures["dataset"].read_bytes()).hexdigest(),
            "host_key_fingerprint": legacy["key_fingerprints"]["host"],
            "openshell_key_fingerprint": legacy["key_fingerprints"]["openshell"],
        }
    )
    with pytest.raises(module.PrerequisiteError, match="prerequisites_legacy_contract_forbidden"):
        module.validate_report_for_evaluation(legacy_path, **validation)


@pytest.mark.parametrize(
    ("override", "error_code"),
    [
        ({"evaluation_id": "eval-20260716-b"}, "prerequisites_not_go"),
        ({"dataset_sha256": "f" * 64}, "prerequisites_dataset_drift"),
        ({"openshell_runs_url": "http://127.0.0.1:28652/v1/runs"}, "prerequisites_endpoint_drift"),
        ({"host_key_fingerprint": "f" * 64}, "prerequisites_api_key_drift"),
    ],
)
def test_evaluator_rejects_all_formal_input_drift(
    tmp_path: Path,
    override: dict[str, str],
    error_code: str,
) -> None:
    fixtures = _fixtures(tmp_path)
    report = _report(fixtures)
    prerequisites = _write(tmp_path / "prerequisites.json", json.dumps(report))
    validation = {
        "evaluation_id": "eval-20260716-a",
        "dataset_sha256": hashlib.sha256(fixtures["dataset"].read_bytes()).hexdigest(),
        "host_runs_url": "http://127.0.0.1:18651/v1/runs",
        "openshell_runs_url": "http://127.0.0.1:28651/v1/runs",
        "host_key_fingerprint": report["key_fingerprints"]["host"],
        "openshell_key_fingerprint": report["key_fingerprints"]["openshell"],
    }
    validation.update(override)

    with pytest.raises(module.PrerequisiteError, match=error_code):
        module.validate_report_for_evaluation(prerequisites, **validation)


def _output_tree(root: Path, evaluation_id: str = "eval-20260716-a") -> Path:
    var = root / "var"
    var.mkdir(mode=0o770)
    var.chmod(0o770)
    openshell = var / "openshell"
    openshell.mkdir(mode=0o700)
    eval_root = openshell / "eval"
    eval_root.mkdir(mode=0o700)
    evaluation = eval_root / evaluation_id
    evaluation.mkdir(mode=0o700)
    return evaluation / module.OUTPUT_NAME


def _output_relative(evaluation_id: str = "eval-20260716-a") -> Path:
    return module.OUTPUT_ROOT_RELATIVE / evaluation_id / module.OUTPUT_NAME


def _cli_args(output: Path | None = None) -> list[str]:
    args = [
        "--host-runs-url",
        "http://127.0.0.1:18651/v1/runs",
        "--openshell-runs-url",
        "http://127.0.0.1:28651/v1/runs",
        "--host-api-key-file",
        "host.key",
        "--openshell-api-key-file",
        "openshell.key",
        "--dataset",
        "dataset.json",
        "--evaluation-id",
        "eval-20260716-a",
        "--provenance",
        "provenance.json",
        "--provider-inventory",
        "provider.json",
        "--service-report",
        "service.json",
        "--broker-report",
        "broker.json",
    ]
    if output is not None:
        args.extend(("--output", output.as_posix()))
    return args


def test_safe_writer_publishes_only_canonical_private_exact_path(tmp_path: Path) -> None:
    fixtures = _fixtures(tmp_path / "inputs")
    output = _output_tree(tmp_path)
    report = _report(fixtures)

    published = module.write_report(
        report,
        project_root=tmp_path,
        evaluation_id="eval-20260716-a",
        output=_output_relative(),
    )

    expected = (json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode("ascii")
    assert published == output
    assert output.read_bytes() == expected
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert output.stat().st_nlink == 1
    for name, source in (("provenance.json", fixtures["provenance"]), ("host.key", fixtures["host_key"])):
        target = output.with_name(name)
        target.write_bytes(source.read_bytes())
        target.chmod(0o600)
    validated, digest = module.validate_report_for_evaluation(
        output,
        evaluation_id="eval-20260716-a",
        dataset_sha256=hashlib.sha256(fixtures["dataset"].read_bytes()).hexdigest(),
        host_runs_url="http://127.0.0.1:18651/v1/runs",
        openshell_runs_url="http://127.0.0.1:28651/v1/runs",
        host_key_fingerprint=report["key_fingerprints"]["host"],
        openshell_key_fingerprint=report["key_fingerprints"]["openshell"],
    )
    assert validated == report
    assert digest == hashlib.sha256(expected).hexdigest()


def test_safe_writer_requires_replace_and_atomically_replaces_valid_output(tmp_path: Path) -> None:
    fixtures = _fixtures(tmp_path / "inputs")
    output = _output_tree(tmp_path)
    output.write_bytes(b"old\n")
    output.chmod(0o600)
    report = _report(fixtures)

    with pytest.raises(module.PrerequisiteError, match="prerequisites_output_exists"):
        module.write_report(
            report,
            project_root=tmp_path,
            evaluation_id="eval-20260716-a",
            output=_output_relative(),
        )
    assert output.read_bytes() == b"old\n"

    module.write_report(
        report,
        project_root=tmp_path,
        evaluation_id="eval-20260716-a",
        output=_output_relative(),
        replace=True,
    )

    assert output.read_bytes() == module._canonical_report(report)
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert output.stat().st_nlink == 1
    assert [item.name for item in output.parent.iterdir()] == [module.OUTPUT_NAME]


@pytest.mark.parametrize(
    "output",
    (
        Path("prerequisites.json"),
        Path("var/openshell/eval/eval-20260716-a/other.json"),
        Path("var/openshell/eval/eval-20260716-a/../prerequisites.json"),
    ),
)
def test_safe_writer_rejects_every_non_exact_output_path(tmp_path: Path, output: Path) -> None:
    fixtures = _fixtures(tmp_path / "inputs")
    _output_tree(tmp_path)

    with pytest.raises(module.PrerequisiteError, match="prerequisites_output_path_invalid"):
        module.write_report(
            _report(fixtures),
            project_root=tmp_path,
            evaluation_id="eval-20260716-a",
            output=output,
        )


def test_safe_writer_rejects_absolute_output_outside_project(tmp_path: Path) -> None:
    fixtures = _fixtures(tmp_path / "inputs")
    _output_tree(tmp_path)

    with pytest.raises(module.PrerequisiteError, match="prerequisites_output_path_invalid"):
        module.write_report(
            _report(fixtures),
            project_root=tmp_path,
            evaluation_id="eval-20260716-a",
            output=tmp_path.parent / "prerequisites.json",
        )


def test_safe_writer_rejects_symlink_non_owner_and_wrong_parent_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixtures = _fixtures(tmp_path / "inputs")
    output = _output_tree(tmp_path)
    evaluation = output.parent
    evaluation.rmdir()
    backing = tmp_path / "backing"
    backing.mkdir(mode=0o700)
    evaluation.symlink_to(backing, target_is_directory=True)
    with pytest.raises(module.PrerequisiteError, match="prerequisites_output_parent_invalid"):
        module.write_report(
            _report(fixtures),
            project_root=tmp_path,
            evaluation_id="eval-20260716-a",
            output=_output_relative(),
        )

    evaluation.unlink()
    evaluation.mkdir(mode=0o750)
    with pytest.raises(module.PrerequisiteError, match="prerequisites_output_parent_invalid"):
        module.write_report(
            _report(fixtures),
            project_root=tmp_path,
            evaluation_id="eval-20260716-a",
            output=_output_relative(),
        )

    evaluation.chmod(0o700)
    actual_euid = os.geteuid()
    monkeypatch.setattr(module.os, "geteuid", lambda: actual_euid + 1)
    with pytest.raises(module.PrerequisiteError, match="prerequisites_output_root_invalid"):
        module.write_report(
            _report(fixtures),
            project_root=tmp_path,
            evaluation_id="eval-20260716-a",
            output=_output_relative(),
        )


@pytest.mark.parametrize("kind", ("symlink", "mode", "hardlink"))
def test_safe_writer_rejects_unsafe_existing_output(tmp_path: Path, kind: str) -> None:
    fixtures = _fixtures(tmp_path / "inputs")
    output = _output_tree(tmp_path)
    if kind == "symlink":
        target = tmp_path / "target.json"
        target.write_text("{}\n", encoding="ascii")
        output.symlink_to(target)
    else:
        output.write_text("{}\n", encoding="ascii")
        output.chmod(0o600 if kind == "hardlink" else 0o640)
        if kind == "hardlink":
            os.link(output, tmp_path / "second-link.json")

    with pytest.raises(module.PrerequisiteError, match="prerequisites_output_file_invalid"):
        module.write_report(
            _report(fixtures),
            project_root=tmp_path,
            evaluation_id="eval-20260716-a",
            output=_output_relative(),
            replace=True,
        )


def test_safe_writer_removes_all_artifacts_when_staged_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixtures = _fixtures(tmp_path / "inputs")
    output = _output_tree(tmp_path)
    monkeypatch.setattr(module.os, "fsync", lambda _descriptor: (_ for _ in ()).throw(OSError("fsync failed")))

    with pytest.raises(module.PrerequisiteError, match="prerequisites_output_write_failed"):
        module.write_report(
            _report(fixtures),
            project_root=tmp_path,
            evaluation_id="eval-20260716-a",
            output=_output_relative(),
        )

    assert not output.exists()
    assert list(output.parent.iterdir()) == []


def test_safe_writer_restores_existing_file_when_replace_validation_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixtures = _fixtures(tmp_path / "inputs")
    output = _output_tree(tmp_path)
    output.write_bytes(b"old\n")
    output.chmod(0o600)
    monkeypatch.setattr(
        module,
        "_read_output_at",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(module.PrerequisiteError("forced_validation_failure")),
    )

    with pytest.raises(module.PrerequisiteError, match="forced_validation_failure"):
        module.write_report(
            _report(fixtures),
            project_root=tmp_path,
            evaluation_id="eval-20260716-a",
            output=_output_relative(),
            replace=True,
        )

    assert output.read_bytes() == b"old\n"
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert output.stat().st_nlink == 1
    assert [item.name for item in output.parent.iterdir()] == [module.OUTPUT_NAME]


def test_cli_require_go_never_writes_no_go_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = _output_tree(tmp_path)
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(module, "build_report", lambda **_kwargs: {"decision": "NO_GO", "blockers": ["blocked"]})

    result = module.main([*_cli_args(_output_relative()), "--require-go", "--json"])

    assert result == 1
    assert not output.exists()


def test_cli_output_writes_go_and_replace_requires_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = _output_tree(tmp_path)
    report = {"decision": "GO", "blockers": [], "ascii": "\u6b63\u5e38"}
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(module, "build_report", lambda **_kwargs: report)

    assert module.main(_cli_args(_output_relative())) == 0
    assert output.read_bytes() == module._canonical_report(report)
    assert module.main([*_cli_args(), "--replace"]) == 2
