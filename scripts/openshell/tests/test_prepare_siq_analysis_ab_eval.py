from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.openshell import prepare_siq_analysis_ab_eval as module

IMAGE_ID = "sha256:" + "b" * 64
EVALUATION_ID = "live-20260716-a"


def _write_json(path: Path, value: Any, *, mode: int = 0o600) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(mode)
    return path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _plan() -> dict[str, Any]:
    return {
        "schema_version": module.CASE_PLAN_SCHEMA,
        "profile": module.PROFILE,
        "report_id": "2025-annual",
        "period": "2025",
        "model": "eval-model",
        "temperature": 0.2,
        "repetitions": 3,
        "run_timeout_seconds": 900,
        "metric_cases": [
            {"case_id": f"metric-{index}", "metric_key": f"metric_{index}", "absolute_tolerance": 0.01}
            for index in range(4)
        ],
        "absence_cases": [
            {
                "case_id": f"absent-{index}",
                "metric_key": f"absent_metric_{index}",
                "abstention_marker": f"SOURCE_METRIC_ABSENT_{index}",
            }
            for index in range(4)
        ],
        "workflow_cases": [
            {"case_id": "workflow_analysis_roundtrip", "kind": "analysis_roundtrip"},
            {"case_id": "workflow_tavily_search", "kind": "approved_tavily_search"},
            {"case_id": "workflow_public_download_parse", "kind": "public_download_parse"},
            {"case_id": "workflow_session_continuity", "kind": "session_continuity"},
        ],
    }


def _source_tree(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    root = tmp_path / "project"
    company = root / "data/wiki/companies/600104-company"
    report_dir = company / "reports/2025-annual"
    metrics = [
        {"canonical_name": f"metric_{index}", "values": {"2025": 100.0 + index}}
        for index in range(4)
    ]
    report = {
        "status": "ready",
        "identity": {"company_id": company.name},
        "report": {"report_id": "2025-annual"},
        "financial_data_summary": {"key_metrics": metrics},
    }
    evidence_items = [
        {
            "metric_key": f"metric_{index}",
            "report_id": "2025-annual",
            "period": "2025-12-31",
            "value": 100.0 + index,
            "open_source_table_url": f"/api/source/600104/table/metric-{index}",
        }
        for index in range(4)
    ]
    evidence = {"company_id": company.name, "evidence_count": len(evidence_items), "evidence": evidence_items}
    registry = {
        "schema_version": "siq.immutable_paths.v1",
        "entries": [
            {
                "path": report_dir.relative_to(root).as_posix(),
                "kind": "finalized_report",
                "recursive": True,
                "identity": {"company_id": company.name, "report_id": "2025-annual"},
                "finalization_sha256": "c" * 64,
                "manifest_sha256": "d" * 64,
            }
        ],
    }
    _write_json(report_dir / "report.json", report, mode=0o664)
    _write_json(company / "evidence/evidence_index.json", evidence, mode=0o664)
    registry_path = _write_json(root / "var/openshell/registry/immutable-paths.json", registry)
    plan_path = _write_json(root / "private/case-plan.json", _plan())
    return root, company, registry_path, plan_path


def _prepare_dataset(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path]:
    root, company, registry, plan = _source_tree(tmp_path)
    dataset_path, bindings_path = module.prepare_dataset_files(
        project_root=root,
        evaluation_id=EVALUATION_ID,
        company_dir=company,
        case_plan_path=plan,
        registry_path=registry,
    )
    return root, company, plan, dataset_path, bindings_path


def test_real_source_dataset_has_scored_search_download_and_normal_policy_cases(tmp_path: Path) -> None:
    root, _company, _plan, dataset_path, bindings_path = _prepare_dataset(tmp_path)
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    bindings = json.loads(bindings_path.read_text(encoding="utf-8"))

    assert len(dataset["cases"]) == 12
    assert len(dataset["cases"]) * dataset["repetitions"] == 36
    assert bindings["dataset_sha256"] == _sha(dataset_path)
    assert set(bindings["bindings"]) == {"case_plan", "immutable_registry", "report", "evidence_index"}
    assert bindings["expected_metric_samples_per_arm"] == {
        "numeric": 15,
        "citations": 24,
        "evidence": 24,
        "sections": 36,
        "tools": 33,
        "hallucination": 12,
    }
    cases = {case["case_id"]: case for case in dataset["cases"]}
    assert cases["workflow_tavily_search"]["expectations"]["required_tools"] == ["web_search"]
    assert cases["workflow_public_download_parse"]["expectations"]["required_tools"] == ["terminal"]
    assert "siq-fetch" in cases["workflow_public_download_parse"]["input"]
    assert all(case["expectations"]["fallback_expected"] is None for case in dataset["cases"])
    assert all(case["expectations"]["policy_denial_expected"] is False for case in dataset["cases"])
    assert dataset_path.stat().st_mode & 0o777 == 0o600
    assert bindings_path.stat().st_mode & 0o777 == 0o600
    assert dataset_path.is_relative_to(root / "var/openshell/eval" / EVALUATION_ID)


def test_dataset_rejects_false_absence_and_source_binding_drift(tmp_path: Path) -> None:
    root, company, registry, plan = _source_tree(tmp_path)
    plan_payload = json.loads(plan.read_text(encoding="utf-8"))
    plan_payload["absence_cases"][0]["metric_key"] = "metric_0"
    _write_json(plan, plan_payload)
    with pytest.raises(module.PreparationError, match="absence_case_metric_present"):
        module.build_dataset(
            project_root=root,
            evaluation_id=EVALUATION_ID,
            company_dir=company,
            case_plan_path=plan,
            registry_path=registry,
        )

    _write_json(plan, _plan())
    _dataset, source_bindings = module.build_dataset(
        project_root=root,
        evaluation_id=EVALUATION_ID,
        company_dir=company,
        case_plan_path=plan,
        registry_path=registry,
    )
    report_binding = source_bindings["bindings"]["report"]
    Path(report_binding["path"]).write_text("{}\n", encoding="utf-8")
    with pytest.raises(module.PreparationError, match="source_binding_drift"):
        module.recapture_source_binding(report_binding, maximum=1024 * 1024)


def test_provenance_binds_equal_arms_and_all_source_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _company, _plan, dataset_path, bindings_path = _prepare_dataset(tmp_path)
    profile = root / "agents/hermes/profiles/siq_analysis"
    shared = root / "agents/hermes/profiles/shared"
    (profile / "scripts").mkdir(parents=True)
    (shared / "scripts").mkdir(parents=True)
    (profile / "scripts/runtime.py").write_text("PROFILE = True\n", encoding="utf-8")
    (profile / "__pycache__").mkdir()
    (profile / "__pycache__/ignored.pyc").write_bytes(b"ignored")
    (shared / "scripts/shared.py").write_text("SHARED = True\n", encoding="utf-8")
    host_config = root / "data/hermes/home/profiles/siq_analysis/config.yaml"
    host_config.parent.mkdir(parents=True, exist_ok=True)
    host_config.write_text(yaml.safe_dump({"model": {"default": "eval-model", "temperature": 0.2}}), encoding="utf-8")
    host_config.chmod(0o664)

    candidate_api_content = b"RUNTIME_METADATA = True\n"
    candidate_run_agent_content = b"RUN_AGENT = True\n"
    files_content = (
        f"{hashlib.sha256(candidate_api_content).hexdigest()}  ./hermes-agent/gateway/platforms/api_server.py\n"
        f"{hashlib.sha256(candidate_run_agent_content).hexdigest()}  ./hermes-agent/run_agent.py\n"
    ).encode("ascii")
    context_sha = hashlib.sha256(files_content).hexdigest()
    runtime_config_sha = "f" * 64
    context = root / "var/openshell/siq-analysis/contexts" / context_sha
    candidate_api_server = context / "hermes-agent/gateway/platforms/api_server.py"
    candidate_run_agent = context / "hermes-agent/run_agent.py"
    candidate_api_server.parent.mkdir(parents=True)
    candidate_api_server.write_bytes(candidate_api_content)
    candidate_run_agent.write_bytes(candidate_run_agent_content)
    files_manifest = context / "FILES.sha256"
    files_manifest.write_bytes(files_content)
    files_manifest.chmod(0o600)
    baseline = {
        "schema_version": "siq.openshell.siq_analysis_context.v1",
        "hermes_commit": module.HERMES_COMMIT,
        "hermes_patch_sha256": "1" * 64,
        "hermes_auth_patch_sha256": "2" * 64,
        "hermes_runtime_state_patch_sha256": "3" * 64,
        "hermes_integration_patch_sha256": "4" * 64,
        "profile_tree_sha256": module._tree_digest(profile),
        "shared_tree_sha256": module._tree_digest(shared),
        "fixture_sha256": "5" * 64,
        "runtime_source_config_sha256": _sha(host_config),
        "runtime_config_sha256": runtime_config_sha,
        "contains_credentials": False,
        "contains_wiki_data": False,
    }
    routes = [
        {"provider": "primary", "model": "eval-model", "host": "provider-default"},
        {"provider": "fallback", "model": "fallback-model", "host": "fallback.example"},
    ]
    runtime_summary = {
        "schema_version": "siq.openshell.hermes_runtime_config.v1",
        "profile": module.PROFILE,
        "route_order_preserved": True,
        "routes": routes,
        "source_routes": routes,
        "source_sha256": _sha(host_config),
        "output_sha256": runtime_config_sha,
    }
    _write_json(context / "SOURCE_BASELINE.json", baseline)
    _write_json(context / "runtime-config.summary.json", runtime_summary)
    policy = _write_json(root / "var/openshell/siq-analysis/runs/live/policy.json", {"policy": True})
    analysis_root = root / "data/wiki/companies/600104-company/analysis"
    analysis_root.mkdir()
    runtime_snapshot = root / "var/openshell/siq-analysis/runtime-snapshots/live"
    runtime_snapshot.mkdir(parents=True)
    mount_payload = {
        "docker": {
            "mounts": module.formal_runtime_contract.mount_builder._expected_mounts(
                root,
                runtime_snapshot,
                analysis_root,
            )
        }
    }
    mount_content = (json.dumps(mount_payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True) + "\n").encode()
    mount_digest = hashlib.sha256(mount_content).hexdigest()
    mount = root / f"var/openshell/siq-analysis/mount-plans/{mount_digest}.driver-config.json"
    mount.parent.mkdir(parents=True, exist_ok=True)
    mount.write_bytes(mount_content)
    mount.chmod(0o600)
    run = {
        "schema_version": "siq.openshell.siq_analysis_lifecycle.v1",
        "profile": module.PROFILE,
        "phase": "running",
        "forward_host": "127.0.0.1",
        "forward_port": 28651,
        "image_id": IMAGE_ID,
        "policy": policy.relative_to(root).as_posix(),
        "policy_sha256": _sha(policy),
        "mount_plan": mount.relative_to(root).as_posix(),
        "mount_plan_sha256": _sha(mount),
        "analysis_relative_path": analysis_root.relative_to(root).as_posix(),
        "runtime_snapshot": runtime_snapshot.relative_to(root).as_posix(),
    }
    run_path = _write_json(root / "var/openshell/siq-analysis/runs/live/run.json", run)
    image = {
        "Id": IMAGE_ID,
        "Config": {
            "Labels": {
                "org.opencontainers.image.revision": module.HERMES_COMMIT,
                "ai.siq.openshell.context-sha256": context_sha,
                "ai.siq.openshell.runtime-config-sha256": runtime_config_sha,
                "ai.siq.hermes.patch-sha256": "1" * 64,
            }
        },
    }
    evaluation_dir = dataset_path.parent
    _write_json(evaluation_dir / "host-key-receipt.json", {"private": "key-receipt"})
    runtime_receipt = {
        "listener": {
            "api_server_sha256": _sha(candidate_api_server),
            "run_agent_sha256": _sha(candidate_run_agent),
        },
        "capabilities": {
            "document_sha256": "6" * 64,
            "run_runtime_metadata_v1": True,
        },
    }
    _write_json(evaluation_dir / "host-runtime-receipt.json", runtime_receipt)
    _write_json(evaluation_dir / "host.key", {"private": "host-key"})
    monkeypatch.setattr(module, "verify_host_runtime_receipts", lambda **_kwargs: runtime_receipt)

    provenance = module.build_provenance(
        project_root=root,
        evaluation_id=EVALUATION_ID,
        dataset_path=dataset_path,
        source_bindings_path=bindings_path,
        run_manifest_path=run_path,
        image_metadata=image,
    )

    assert provenance["schema_version"] == module.PROVENANCE_SCHEMA
    assert set(provenance["sources"]) == module.PROVENANCE_SOURCE_NAMES
    for field in ("hermes_commit", "profile_sha256", "model_route_sha256", "tools_sha256", "data_snapshot_sha256"):
        assert provenance["arms"]["host"][field] == provenance["arms"]["openshell"][field]
    assert provenance["runtime_attestation"]["arms_match"] is True
    assert provenance["runtime_attestation"]["host_runtime_metadata_v1"] is True
    assert provenance["runtime_attestation"]["host_candidate_source_match"] is True
    assert provenance["runtime_attestation"]["temperature_kind"] == "explicit"

    drifted = json.loads(json.dumps(runtime_receipt))
    drifted["listener"]["api_server_sha256"] = "9" * 64
    monkeypatch.setattr(module, "verify_host_runtime_receipts", lambda **_kwargs: drifted)
    with pytest.raises(module.PreparationError, match="host_candidate_runtime_source_mismatch"):
        module.build_provenance(
            project_root=root,
            evaluation_id=EVALUATION_ID,
            dataset_path=dataset_path,
            source_bindings_path=bindings_path,
            run_manifest_path=run_path,
            image_metadata=image,
        )


def test_host_key_materialization_never_puts_secret_in_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    key = b"private-host-api-key-000000"
    monkeypatch.setattr(module, "_listener_pid", lambda _port: 4242)
    monkeypatch.setattr(module, "_process_identity", lambda _pid: ("6" * 64, 12345, b"API_SERVER_KEY=" + key))
    monkeypatch.setattr(module, "_host_health", lambda _key: None)
    runtime_receipt = {
        "schema_version": module.HOST_RUNTIME_RECEIPT_SCHEMA,
        "profile": module.PROFILE,
        "host_runs_url": module.HOST_RUNS_URL,
        "host_runs_url_sha256": hashlib.sha256(module.HOST_RUNS_URL.encode()).hexdigest(),
        "host_api_key_sha256": hashlib.sha256(key).hexdigest(),
        "listener": {"pid": 4242},
        "capabilities": {"run_runtime_metadata_v1": True},
        "credential_values_recorded": False,
    }
    monkeypatch.setattr(module, "_capture_host_runtime_receipt", lambda **_kwargs: runtime_receipt)

    key_path, receipt_path, runtime_path = module.materialize_host_key(project_root=root, evaluation_id=EVALUATION_ID)
    receipt_text = receipt_path.read_text(encoding="utf-8")
    runtime_text = runtime_path.read_text(encoding="utf-8")
    receipt = json.loads(receipt_text)

    assert key_path.read_bytes() == key + b"\n"
    assert key.decode() not in receipt_text
    assert key.decode() not in runtime_text
    assert receipt["api_key_sha256"] == hashlib.sha256(key).hexdigest()
    assert receipt["key_file_created"] is True
    assert receipt["key_value_in_receipt"] is False
    assert key_path.stat().st_mode & 0o777 == 0o600
    assert receipt_path.stat().st_mode & 0o777 == 0o600
    assert runtime_path.stat().st_mode & 0o777 == 0o600


def test_host_capability_probe_requires_authenticated_runtime_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _maximum: int) -> bytes:
            return json.dumps(
                {
                    "object": "hermes.api_server.capabilities",
                    "auth": {"type": "bearer", "required": True},
                    "features": {"run_submission": True},
                }
            ).encode()

    class Opener:
        def open(self, _request, timeout: int):
            assert timeout == 5
            return Response()

    monkeypatch.setattr(module.urllib.request, "build_opener", lambda *_args: Opener())

    with pytest.raises(module.PreparationError, match="host_runtime_metadata_capability_missing"):
        module._host_capabilities(b"private-host-api-key-000000")


def test_host_runtime_receipt_revalidation_rejects_process_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = b"private-host-api-key-000000"
    key_path = tmp_path / "host.key"
    key_path.write_bytes(key + b"\n")
    key_path.chmod(0o600)
    key_receipt = {
        "schema_version": module.HOST_KEY_RECEIPT_SCHEMA,
        "profile": module.PROFILE,
        "port": module.HOST_PORT,
        "listener_command_sha256": "1" * 64,
        "listener_start_ticks": 12345,
        "api_key_sha256": hashlib.sha256(key).hexdigest(),
        "health_status_ok": True,
        "key_file_created": True,
        "key_value_in_receipt": False,
        "key_file_mode": 0o600,
    }
    key_receipt_path = _write_json(tmp_path / "host-key-receipt.json", key_receipt)
    runtime_receipt = {
        "schema_version": module.HOST_RUNTIME_RECEIPT_SCHEMA,
        "profile": module.PROFILE,
        "host_runs_url": module.HOST_RUNS_URL,
        "host_runs_url_sha256": hashlib.sha256(module.HOST_RUNS_URL.encode()).hexdigest(),
        "host_api_key_sha256": hashlib.sha256(key).hexdigest(),
        "listener": {"pid": 4242, "start_ticks": 12345, "argv_sha256": "1" * 64},
        "capabilities": {"document_sha256": "2" * 64, "run_runtime_metadata_v1": True},
        "credential_values_recorded": False,
    }
    runtime_path = _write_json(tmp_path / "host-runtime-receipt.json", runtime_receipt)
    drifted = json.loads(json.dumps(runtime_receipt))
    drifted["listener"]["pid"] = 4343
    monkeypatch.setattr(module, "_capture_host_runtime_receipt", lambda **_kwargs: drifted)

    with pytest.raises(module.PreparationError, match="host_runtime_receipt_drift"):
        module.verify_host_runtime_receipts(
            project_root=tmp_path,
            host_runs_url=module.HOST_RUNS_URL,
            host_api_key_file=key_path,
            host_key_receipt_path=key_receipt_path,
            host_runtime_receipt_path=runtime_path,
        )


def test_process_start_time_uses_stable_boot_clock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proc = tmp_path / "4242"
    proc.mkdir()
    fields = ["0"] * 22
    fields[0] = "4242"
    fields[1] = "(hermes gateway)"
    fields[21] = "250"
    (proc / "stat").write_text(" ".join(fields) + "\n", encoding="ascii")
    system_stat = tmp_path / "proc-stat"
    system_stat.write_text("cpu 1 2 3 4\nbtime 1000\n", encoding="ascii")
    monkeypatch.setattr(module, "PROC_STAT_PATH", system_stat)
    monkeypatch.setattr(module.os, "sysconf", lambda name: 100 if name == "SC_CLK_TCK" else 0)

    first = module._process_start_time_ns(proc, expected_start_ticks=250)
    proc.touch()
    second = module._process_start_time_ns(proc, expected_start_ticks=250)

    assert first == 1_002_500_000_000
    assert second == first


def test_process_start_time_rejects_tick_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proc = tmp_path / "4242"
    proc.mkdir()
    fields = ["0"] * 22
    fields[0] = "4242"
    fields[1] = "(hermes gateway)"
    fields[21] = "250"
    (proc / "stat").write_text(" ".join(fields) + "\n", encoding="ascii")
    system_stat = tmp_path / "proc-stat"
    system_stat.write_text("btime 1000\n", encoding="ascii")
    monkeypatch.setattr(module, "PROC_STAT_PATH", system_stat)
    monkeypatch.setattr(module.os, "sysconf", lambda _name: 100)

    with pytest.raises(module.PreparationError, match="host_process_clock_invalid"):
        module._process_start_time_ns(proc, expected_start_ticks=251)
