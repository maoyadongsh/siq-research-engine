from __future__ import annotations

import hashlib
import json
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.openshell import (  # noqa: E402
    destructive_action_guard as delete_guard,
    siq_analysis_canary as canary,
    siq_analysis_lifecycle as formal,
    siq_analysis_wide_pilot as wide,
    siq_analysis_wide_pilot_guard as wide_guard,
)
from scripts.openshell.siq_analysis_lifecycle import LifecycleAdapter, _write_json  # noqa: E402


def _project(tmp_path: Path, *, with_work: bool = False) -> tuple[Path, str]:
    root = tmp_path / "project"
    company = "600104-test"
    company_root = root / "data/wiki/companies" / company
    analysis = company_root / "analysis"
    analysis.mkdir(parents=True)
    if with_work:
        (analysis / ".work").mkdir()
    (company_root / "reports").mkdir()
    (company_root / "company.json").write_text('{"stock_code":"600104"}\n', encoding="ascii")
    return root, company


def _lifecycle(root: Path) -> canary.CanaryLifecycle:
    return canary.CanaryLifecycle(project_root=root, adapter=LifecycleAdapter(project_root=root))


def _spec(lifecycle: canary.CanaryLifecycle, company: str, run_id: str = "canary-0123456789ab"):
    return lifecycle._spec(market="cn", company=company, pilot_id=run_id)


def test_canary_identity_is_independent_and_never_formal_evidence() -> None:
    assert canary.MODE == "NOT_PRODUCTION_CANARY"
    assert canary.SCHEMA_VERSION == "siq.openshell.siq_analysis_canary_lifecycle.v1"
    assert canary.STATE_RELATIVE == Path("var/openshell/canary/siq-analysis")
    assert canary.ACKNOWLEDGEMENT == "--acknowledge-not-production-canary"
    assert canary.RUN_ID_RE.fullmatch("canary-0123456789ab")
    assert not canary.RUN_ID_RE.fullmatch("pilot-0123456789ab")
    assert canary.CANARY_SETTINGS.lifecycle_label == formal.CANARY_LIFECYCLE_LABEL
    assert canary.CANARY_SETTINGS.identity_field == "run_id"
    assert wide.WIDE_PILOT_SETTINGS.mode == "NOT_PRODUCTION_WIDE_PILOT"


def test_pool_slot_parameterizes_state_and_local_forward_without_changing_target(tmp_path: Path) -> None:
    root, company = _project(tmp_path)
    slot_id = hashlib.sha256(f"cn\0{company}".encode()).hexdigest()[:24]
    lifecycle = canary.CanaryLifecycle(
        project_root=root,
        adapter=LifecycleAdapter(project_root=root),
        pool_slot_id=slot_id,
        local_port=28652,
        reservation_token="reservation-" + "a" * 32,
    )
    spec = _spec(lifecycle, company)

    assert lifecycle.settings.pool_managed is True
    assert lifecycle.settings.pool_slot_id == slot_id
    assert lifecycle.settings.local_port == 28652
    assert lifecycle.settings.target_port == 28651
    assert lifecycle.settings.state_relative == canary.POOL_SLOTS_RELATIVE / slot_id
    assert spec.run_dir == root / canary.POOL_SLOTS_RELATIVE / slot_id / "runs" / spec.run_id
    assert spec.sandbox_name == f"siq-analysis-{spec.run_id}"


def test_pool_slot_guard_uses_the_exact_parameterized_state_root(tmp_path: Path) -> None:
    root, company = _project(tmp_path)
    slot_id = hashlib.sha256(f"cn\0{company}".encode()).hexdigest()[:24]
    lifecycle = canary.CanaryLifecycle(
        project_root=root,
        adapter=LifecycleAdapter(project_root=root),
        pool_slot_id=slot_id,
        local_port=28652,
        reservation_token="reservation-" + "a" * 32,
    )
    spec = _spec(lifecycle, company)
    settings = wide_guard._guard_settings(canary.MODE, slot_id)

    assert settings.runs_relative == canary.POOL_SLOTS_RELATIVE / slot_id / "runs"
    assert root / settings.runs_relative / spec.run_id == spec.run_dir

    arguments = lifecycle._guard_arguments(spec, "a" * 64)
    assert arguments[-2:] == ["--pool-slot-id", slot_id]


def test_guard_rejects_pool_slot_for_wide_pilot_and_invalid_canary_slot() -> None:
    with pytest.raises(formal.LifecycleError, match="wide_pilot_guard_pool_slot_forbidden"):
        wide_guard._guard_settings(wide.MODE, "a" * 24)
    with pytest.raises(formal.LifecycleError, match="canary_guard_pool_slot_invalid"):
        wide_guard._guard_settings(canary.MODE, "not-a-slot")


def test_pool_slot_arguments_are_explicit_and_legacy_cli_remains_compatible() -> None:
    parser = canary._parser()
    legacy = parser.parse_args(["status", "--run-id", "canary-0123456789ab"])
    pooled = parser.parse_args(
        [
            "status",
            "--run-id",
            "canary-0123456789ab",
            "--pool-slot-id",
            "a" * 24,
            "--local-port",
            "28652",
        ]
    )

    assert legacy.pool_slot_id is None
    assert legacy.local_port is None
    assert pooled.pool_slot_id == "a" * 24
    assert pooled.local_port == 28652

    with pytest.raises(wide.WidePilotError, match="canary_pool_slot_invalid"):
        canary.CanaryLifecycle(pool_slot_id="a" * 24, local_port=28651)


def test_pool_slot_health_uses_authenticated_forward_and_exec_target(tmp_path: Path, monkeypatch) -> None:
    root, company = _project(tmp_path)
    slot_id = hashlib.sha256(f"cn\0{company}".encode()).hexdigest()[:24]
    lifecycle = canary.CanaryLifecycle(
        project_root=root,
        adapter=LifecycleAdapter(project_root=root),
        pool_slot_id=slot_id,
        local_port=28652,
        reservation_token="reservation-" + "a" * 32,
    )
    spec = _spec(lifecycle, company)
    observed: dict[str, object] = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        @staticmethod
        def read(*_args):
            return b'{"object":"list","data":[{"id":"siq_analysis"}]}'

    def urlopen(request, *, timeout):
        observed["url"] = request.full_url
        observed["authorization"] = request.get_header("Authorization")
        observed["timeout"] = timeout
        return Response()

    monkeypatch.setattr(wide.urllib.request, "urlopen", urlopen)
    assert lifecycle._authenticated_forward_health("a" * 64) is True
    assert observed == {
        "url": "http://127.0.0.1:28652/v1/models",
        "authorization": f"Bearer {'a' * 64}",
        "timeout": 2,
    }

    command: dict[str, object] = {}

    def run_cli(arguments, code, *, timeout_seconds):
        command.update(arguments=arguments, code=code, timeout_seconds=timeout_seconds)
        return "SIQ_AUTHENTICATED_SANDBOX_HEALTH_OK\n"

    lifecycle.adapter = SimpleNamespace(_run_cli=run_cli)
    assert lifecycle._authenticated_sandbox_exec_health(spec) is True
    arguments = command["arguments"]
    assert isinstance(arguments, list)
    assert arguments[:4] == ["sandbox", "exec", "--name", spec.sandbox_name]
    assert "http://127.0.0.1:28651/v1/models" in arguments[-1]
    assert "API_SERVER_KEY" in arguments[-1]
    assert "a" * 64 not in arguments[-1]


def test_pool_slot_cleanup_checks_only_its_parameterized_local_port(tmp_path: Path) -> None:
    root, company = _project(tmp_path)
    slot_id = hashlib.sha256(f"cn\0{company}".encode()).hexdigest()[:24]
    lifecycle = canary.CanaryLifecycle(
        project_root=root,
        adapter=LifecycleAdapter(project_root=root),
        pool_slot_id=slot_id,
        local_port=28652,
        reservation_token="reservation-" + "a" * 32,
    )
    spec = _spec(lifecycle, company)
    forward = formal.ProcessRecord(
        schema_version=formal.PROCESS_SCHEMA_VERSION,
        role="forward",
        pid=4102,
        start_ticks=100,
        executable="/fixture/openshell",
        argv_sha256="a" * 64,
    )
    observed: dict[str, object] = {}

    def terminate(record):
        observed["terminated"] = record

    def port_listener_absent(host, port):
        observed["port"] = (host, port)
        return True

    lifecycle.adapter = SimpleNamespace(
        backend=SimpleNamespace(
            terminate=terminate,
            port_listener_absent=port_listener_absent,
        )
    )
    lifecycle._cleanup_started(
        spec,
        manifest=None,
        nonce="",
        guard_record=None,
        forward_record=forward,
        guard_pid=0,
        forward_pid=forward.pid,
        sandbox_attempted=False,
        allow_missing_output=True,
    )

    assert observed == {
        "terminated": forward,
        "port": ("127.0.0.1", 28652),
    }


def test_analysis_root_is_the_writable_scope_without_precreated_work(tmp_path: Path) -> None:
    root, company = _project(tmp_path, with_work=False)
    lifecycle = _lifecycle(root)
    spec = _spec(lifecycle, company)

    paths = lifecycle._prepare_output_root(spec)

    assert not (spec.analysis_root / ".work").exists()
    assert paths.output_root == spec.analysis_root
    assert paths.source == spec.analysis_root.parent / "company.json"
    assert lifecycle._manifest_scope_fields(paths) == {
        "writable_relative_path": f"data/wiki/companies/{company}/analysis",
        "write_scope": "current_company_analysis_root",
        "normal_business_mutations": ["create", "modify", "rename", "delete"],
    }


def test_canary_start_preparation_creates_analysis_but_never_company_root(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    lifecycle = _lifecycle(root)

    with pytest.raises(wide.WidePilotError, match="canary_company_root_missing"):
        lifecycle._prepare_host_business_root(
            market="cn", company="600104-test", pilot_id="canary-0123456789ab"
        )

    assert not (root / "data/wiki/companies/600104-test").exists()

    company_root = root / "data/wiki/companies/600104-test"
    company_root.mkdir(parents=True)
    lifecycle._prepare_host_business_root(
        market="cn", company="600104-test", pilot_id="canary-0123456789ab"
    )
    assert (company_root / "analysis").is_dir()


def test_policy_allows_full_analysis_business_surface_but_not_company_or_control_plane(tmp_path: Path) -> None:
    root, company = _project(tmp_path)
    lifecycle = _lifecycle(root)
    spec = _spec(lifecycle, company)
    paths = lifecycle._paths(spec)
    filesystem = {
        "read_only": [str(root), str(root / "scripts"), str(root / "infra")],
        "read_write": [str(spec.analysis_root)],
    }
    summary = {"profile": "siq_analysis", "task_scoped_write_count": 1}

    lifecycle._validate_policy_scope(spec, paths, summary=summary, filesystem=filesystem)

    for forbidden in (spec.analysis_root.parent, root / "scripts", root / "infra"):
        mutated = {**filesystem, "read_write": [str(spec.analysis_root), str(forbidden)]}
        with pytest.raises(wide.WidePilotError, match="canary_policy_scope_invalid"):
            lifecycle._validate_policy_scope(spec, paths, summary=summary, filesystem=mutated)


def test_cleanup_retains_normal_business_outputs(tmp_path: Path) -> None:
    root, company = _project(tmp_path)
    lifecycle = _lifecycle(root)
    spec = _spec(lifecycle, company)
    generated = spec.analysis_root / "derived-report" / "chart.json"
    generated.parent.mkdir()
    generated.write_text("{}\n", encoding="ascii")

    lifecycle._cleanup_output(spec, {}, allow_missing=True)
    lifecycle._cleanup_uncommitted_business_scope(spec)

    assert generated.read_text(encoding="ascii") == "{}\n"


def test_active_pointer_has_one_exact_path_authority(tmp_path: Path) -> None:
    root, company = _project(tmp_path)
    lifecycle = _lifecycle(root)
    run_id = "canary-0123456789ab"
    active = root / canary.ACTIVE_RELATIVE
    active.parent.mkdir(parents=True, mode=0o700)
    run_dir = root / canary.RUNS_RELATIVE / run_id
    run_dir.mkdir(parents=True, mode=0o700)
    manifest_path = run_dir / canary.MANIFEST_NAME
    api_key_sha256 = "a" * 64
    _write_json(
        manifest_path,
        {
            "schema_version": canary.SCHEMA_VERSION,
            "mode": canary.MODE,
            "phase": "running",
            "run_id": run_id,
            "market": "cn",
            "company": company,
            "api_key_sha256": api_key_sha256,
        },
        root=root,
    )
    manifest_relative = manifest_path.relative_to(root).as_posix()
    _write_json(
        active,
        {
            "schema_version": canary.SCHEMA_VERSION,
            "mode": canary.MODE,
            "readiness_effect": "none",
            "profile": "siq_analysis",
            "run_id": run_id,
            "market": "cn",
            "company": company,
            "run_state": (canary.RUNS_RELATIVE / run_id).as_posix(),
            "manifest": manifest_relative,
            "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            "api_key_sha256": api_key_sha256,
        },
        root=root,
    )

    assert lifecycle._active()["run_id"] == run_id
    assert stat.S_IMODE(active.stat().st_mode) == 0o600

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    lifecycle._before_stop(_spec(lifecycle, company, run_id), manifest)
    assert lifecycle._active()["manifest_sha256"] == hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["phase"] == "stopping"

    value = json.loads(active.read_text(encoding="utf-8"))
    value["manifest"] = "untrusted-second-authority"
    active.unlink()
    _write_json(active, value, root=root)
    with pytest.raises(wide.WidePilotError, match="canary_active_state_invalid"):
        lifecycle._active()


def test_path_contract_rejects_symlinked_reports(tmp_path: Path) -> None:
    root, company = _project(tmp_path)
    reports = root / "data/wiki/companies" / company / "reports"
    reports.rmdir()
    reports.symlink_to(root / "data/wiki/companies" / company / "analysis", target_is_directory=True)
    lifecycle = _lifecycle(root)

    with pytest.raises(wide.WidePilotError, match="canary_path_symlinked"):
        lifecycle._paths(_spec(lifecycle, company))


def test_normal_delete_is_allowed_and_only_abnormal_thresholds_trigger() -> None:
    assert canary.CANARY_SETTINGS.sandbox_mode_environment == ("SIQ_OPENSHELL_CANARY", "1")
    assert delete_guard._threshold_reason(deleted_count=2, baseline_count=40) is None
    assert delete_guard._threshold_reason(deleted_count=500, baseline_count=2000) is None
    assert delete_guard._threshold_reason(deleted_count=501, baseline_count=2000) == "deletion_count_gt_500"
    assert delete_guard._threshold_reason(deleted_count=20, baseline_count=40) == "deletion_ratio_threshold"


def test_canary_probe_covers_business_mutations_and_protected_boundaries() -> None:
    source = (ROOT / "scripts/openshell/siq_analysis_canary.py").read_text(encoding="utf-8")

    for directory in ("parsed", "checkpoint", "charts", "derived-report"):
        assert directory in source
    assert "item.write_text" in source
    assert "item.rename" in source
    assert "moved.unlink" in source
    assert "cross_company" in source
    assert "scripts/openshell" in source
    assert "control_mount_count" in source
    assert "result_is_formal_evidence" in source
    assert "TAVILY_API_KEY" in source
    assert "EXA_API_KEY' not in os.environ" in source


def test_wrappers_require_ack_and_never_change_default_host_runtime() -> None:
    parser = canary._parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["start", "--market", "cn", "--company", "600104-test", "--run-id", "canary-0123456789ab"])
    args = parser.parse_args(
        [
            "start",
            "--acknowledge-not-production-canary",
            "--market",
            "cn",
            "--company",
            "600104-test",
            "--run-id",
            "canary-0123456789ab",
        ]
    )
    assert args.acknowledge_not_production_canary is True

    scripts = "\n".join(
        (ROOT / "scripts/openshell" / name).read_text(encoding="utf-8")
        for name in (
            "run_siq_analysis_canary_lifecycle.sh",
            "start_siq_analysis_canary.sh",
            "status_siq_analysis_canary.sh",
            "stop_siq_analysis_canary.sh",
            "rollback_siq_analysis_canary.sh",
        )
    )
    assert "siq_openshell_acquire_maintenance_lock" in scripts
    assert "start_all.sh" not in scripts
    assert "stop_hermes_gateway.sh" not in scripts
    assert "SIQ_HERMES_RUNTIME" not in scripts


def test_canary_label_is_allowed_without_changing_formal_preflight() -> None:
    lifecycle_source = (ROOT / "scripts/openshell/siq_analysis_lifecycle.py").read_text(encoding="utf-8")
    canary_source = (ROOT / "scripts/openshell/siq_analysis_canary.py").read_text(encoding="utf-8")

    assert "CANARY_LIFECYCLE_LABEL" in lifecycle_source
    assert "validate_security_probe_prerequisites" in (ROOT / "scripts/openshell/siq_analysis_wide_pilot.py").read_text(
        encoding="utf-8"
    )
    assert "_validate_service_preflight" not in canary_source
    assert '"siq-exa-search"' not in canary_source
    assert "8004" not in canary_source
    assert "8006" not in canary_source
