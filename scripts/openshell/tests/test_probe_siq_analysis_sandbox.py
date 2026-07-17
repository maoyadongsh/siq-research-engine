from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.openshell import probe_siq_analysis_sandbox as probe
from scripts.openshell.siq_analysis_lifecycle import RunSpec, SandboxIdentity, SecurityProbePlan


class FakeProcess:
    pid = 999999999
    stdout = None
    stderr = None

    def kill(self) -> None:
        return None

    def wait(self) -> int:
        return 0


def test_probe_command_uses_shared_minimal_environment(monkeypatch) -> None:
    captured: dict[str, str] = {}

    def fake_popen(*args, **kwargs):
        del args
        assert kwargs["cwd"] == probe.REPO_ROOT
        captured.update(kwargs["env"])
        return FakeProcess()

    monkeypatch.setenv("BASH_ENV", "/tmp/injected")
    monkeypatch.setenv("PYTHONPATH", "/tmp/injected-python")
    monkeypatch.setenv("LD_PRELOAD", "/tmp/injected.so")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid")
    monkeypatch.setenv("TAVILY_API_KEY", "host-secret")
    monkeypatch.setattr(probe.subprocess, "Popen", fake_popen)

    try:
        probe._run_command(["/bin/true"], timeout=1, code="probe_test_failed")
    except probe.ProbeError as exc:
        assert exc.code == "probe_test_failed"
    else:
        raise AssertionError("fake process must stop before selector setup")

    assert (
        not {
            "BASH_ENV",
            "PYTHONPATH",
            "LD_PRELOAD",
            "HTTPS_PROXY",
            "TAVILY_API_KEY",
        }
        & captured.keys()
    )
    assert captured["DOCKER_HOST"] == "unix:///var/run/docker.sock"
    assert captured["DOCKER_CONFIG"] == str(probe.REPO_ROOT / "var/openshell/docker-cli-config")
    assert captured["SIQ_PROJECT_ROOT"] == str(probe.REPO_ROOT)
    assert captured["OPENSHELL_GATEWAY"] == "siq-openshell-dev"
    assert captured["XDG_STATE_HOME"] == str(probe.REPO_ROOT / "var/openshell/xdg/state")


def test_probe_imports_one_environment_contract() -> None:
    lifecycle_path = Path(probe.__file__).with_name("siq_analysis_lifecycle.py")

    assert lifecycle_path.is_file()
    assert probe._minimal_child_environment.__module__ == "scripts.openshell.siq_analysis_lifecycle"


def _filesystem_response() -> dict:
    return {
        "ok": True,
        "check": "filesystem",
        "immutable_write_denials": {key: True for key in probe.FILESYSTEM_IMMUTABLE_DENIALS},
        "sensitive_read_denials": {key: True for key in probe.FILESYSTEM_SENSITIVE_DENIALS},
        "allowed_writes": {key: True for key in probe.FILESYSTEM_ALLOWED_WRITES},
    }


def test_filesystem_boundary_probe_has_no_network_or_model_dependency(monkeypatch, tmp_path: Path) -> None:
    context = type(
        "Context",
        (),
        {"run_id": "formal-run", "manifest": {"analysis_relative_path": "data/wiki/companies/acme/analysis"}},
    )()
    sentinels = type(
        "Sentinels",
        (),
        {
            "name": ".siq-security-probe-" + "1" * 24,
            "marker": b"siq-security-probe:" + b"1" * 24,
            "analysis_host": tmp_path / "analysis",
            "state_host": tmp_path / "state",
            "runtime_host": tmp_path / "session",
            "memory_host": tmp_path / "memory",
            "wiki_host": tmp_path / "wiki",
        },
    )()
    monkeypatch.setattr(probe.SentinelPaths, "build", lambda _context: sentinels)
    monkeypatch.setattr(probe, "_validate_lifecycle_status", lambda *args, **kwargs: None)
    monkeypatch.setattr(probe, "_validate_openshell_identity", lambda *args, **kwargs: None)
    monkeypatch.setattr(probe, "_validate_active_policy", lambda *args, **kwargs: None)
    monkeypatch.setattr(probe, "_docker_inspect_mounts", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        probe,
        "validate_container_mounts",
        lambda *args, **kwargs: {"business_mount_count": 7, "control_mount_count": 5, "total_mount_count": 12},
    )
    commands: list[list[str]] = []

    def fake_exec(_context, command, **kwargs):
        del kwargs
        commands.append(list(command))
        return _filesystem_response()

    monkeypatch.setattr(probe, "_sandbox_exec_json", fake_exec)
    monkeypatch.setattr(
        probe,
        "_verify_host_sentinel",
        lambda path, marker, **kwargs: hashlib.sha256(path.name.encode() + marker).hexdigest(),
    )
    cleanup_calls: list[object] = []
    monkeypatch.setattr(probe, "_cleanup", lambda *args, **kwargs: cleanup_calls.append(args[1]))

    result = probe.run_filesystem_boundary_probe(context, timeout=5)

    assert len(commands) == 1
    assert probe.FILESYSTEM_PROBE in commands[0]
    serialized = json.dumps(commands)
    assert "BROKER_MULTIPART_PROBE" not in serialized
    assert "NEMOTRON_PROBE" not in serialized
    assert cleanup_calls == [sentinels]
    assert result["cleanup_succeeded"] is True
    assert result["residual_host_sentinel_count"] == 0
    assert result["checks"][-2:] == ["tmp_scratch_write", "probe_sentinels_removed"]


def test_filesystem_boundary_probe_cleans_partial_sentinels_on_failure(monkeypatch, tmp_path: Path) -> None:
    context = type(
        "Context",
        (),
        {"run_id": "formal-run", "manifest": {"analysis_relative_path": "data/wiki/companies/acme/analysis"}},
    )()
    sentinels = type(
        "Sentinels",
        (),
        {
            "name": ".siq-security-probe-" + "2" * 24,
            "marker": b"siq-security-probe:" + b"2" * 24,
            "analysis_host": tmp_path / "analysis",
            "state_host": tmp_path / "state",
            "runtime_host": tmp_path / "session",
            "memory_host": tmp_path / "memory",
            "wiki_host": tmp_path / "wiki",
        },
    )()
    monkeypatch.setattr(probe.SentinelPaths, "build", lambda _context: sentinels)
    monkeypatch.setattr(probe, "_validate_lifecycle_status", lambda *args, **kwargs: None)
    monkeypatch.setattr(probe, "_validate_openshell_identity", lambda *args, **kwargs: None)
    monkeypatch.setattr(probe, "_validate_active_policy", lambda *args, **kwargs: None)
    monkeypatch.setattr(probe, "_docker_inspect_mounts", lambda *args, **kwargs: [])
    monkeypatch.setattr(probe, "validate_container_mounts", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        probe,
        "_sandbox_exec_json",
        lambda *args, **kwargs: (_ for _ in ()).throw(probe.ProbeError("filesystem_probe_failed")),
    )
    cleanup_calls: list[object] = []
    monkeypatch.setattr(probe, "_cleanup", lambda *args, **kwargs: cleanup_calls.append(args[1]))

    try:
        probe.run_filesystem_boundary_probe(context, timeout=5)
    except probe.ProbeError as exc:
        assert exc.code == "filesystem_probe_failed"
    else:
        raise AssertionError("failed filesystem probe must fail closed")
    assert cleanup_calls == [sentinels]


def test_mount_observation_excludes_host_sources_and_preserves_access_mode() -> None:
    observed = probe._mount_observation(
        [
            {
                "Type": "bind",
                "Source": "/host/private/token",
                "Destination": "/etc/openshell/auth/sandbox.jwt",
                "Mode": "ro",
                "RW": False,
                "Propagation": "rprivate",
            }
        ]
    )

    assert observed == [
        {
            "destination": "/etc/openshell/auth/sandbox.jwt",
            "type": "bind",
            "read_write": False,
            "mode": "ro",
            "propagation": "rprivate",
        }
    ]
    assert "/host/private/token" not in json.dumps(observed)


def _container_inspection(*, mounts=None) -> dict:
    return {
        "mounts": [] if mounts is None else mounts,
        "privileged": False,
        "cap_add": ["SYS_ADMIN", "NET_ADMIN", "SYS_PTRACE", "SYSLOG"],
        "devices": [],
        "device_requests": None,
        "security_opt": ["apparmor=unconfined"],
        "user": "0",
    }


def test_container_hardening_accepts_only_exact_openshell_bootstrap_contract() -> None:
    summary = probe.validate_container_hardening(_container_inspection())

    assert summary == {
        "schema_version": probe.CONTAINER_HARDENING_SCHEMA_VERSION,
        "privileged": False,
        "supervisor_user": "0",
        "cap_add_count": 4,
        "cap_add_profile": "openshell_v0.0.83_bootstrap_exact",
        "host_device_count": 0,
        "device_request_count": 0,
        "security_opt_profile": "apparmor_unconfined_only",
    }

    invalid = (
        {"privileged": True},
        {"cap_add": ["SYS_ADMIN", "NET_ADMIN", "SYS_PTRACE", "SYSLOG", "SYS_MODULE"]},
        {"cap_add": ["SYS_ADMIN", "NET_ADMIN", "SYS_PTRACE"]},
        {"devices": [{"PathOnHost": "/dev/sda"}]},
        {"device_requests": [{"Driver": "cdi"}]},
        {"security_opt": []},
        {"security_opt": ["apparmor=unconfined", "seccomp=unconfined"]},
        {"user": "root"},
        {"user": "sandbox:sandbox"},
    )
    for mutation in invalid:
        inspection = {**_container_inspection(), **mutation}
        try:
            probe.validate_container_hardening(inspection)
        except probe.ProbeError as exc:
            assert exc.code == "container_hardening_contract_mismatch"
        else:
            raise AssertionError(f"unsafe Docker inspection accepted: {sorted(mutation)}")


def test_docker_inspect_uses_selected_non_secret_contract(monkeypatch) -> None:
    captured: list[str] = []

    def fake_run(command, **kwargs):
        del kwargs
        captured.extend(command)
        return probe.subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout=json.dumps(_container_inspection()),
            stderr="",
        )

    monkeypatch.setattr(probe, "_run_command", fake_run)
    context = type("InspectContext", (), {"container_id": "f" * 64})()

    inspection = probe._docker_inspect_container(context, timeout=5)

    assert inspection["user"] == "0"
    assert probe.DOCKER_INSPECT_FORMAT in captured
    assert all(forbidden not in probe.DOCKER_INSPECT_FORMAT for forbidden in (".Config.Env", ".Path", ".Args"))


def test_provider_independent_exec_failure_diagnostic_is_private_and_bounded(tmp_path: Path) -> None:
    project = tmp_path / "project"
    _private_directory(project)
    plan = _probe_plan(project)
    context = probe.IndependentProbeContext(
        project_root=project,
        probe_id=plan.spec.run_id,
        state_dir=plan.spec.run_dir,
        sandbox_name=plan.spec.sandbox_name,
        sandbox_id="11111111-1111-1111-1111-111111111111",
        container_id="f" * 64,
        nonce="1" * 48,
        analysis_path=plan.spec.analysis_root,
        runtime_snapshot=plan.runtime_snapshot,
        mount_plan=plan.mount_plan,
        mount_plan_sha256=plan.mount_plan_sha256,
        policy_path=plan.policy_path,
        policy={},
        manifest={},
    )
    result = probe.subprocess.CompletedProcess(
        args=[],
        returncode=2,
        stdout="x" * 9000,
        stderr="synthetic-error\n",
    )

    probe._record_independent_exec_failure(context, code="filesystem_probe_failed", result=result)

    path = context.state_dir / "exec-failure-filesystem_probe_failed.json"
    diagnostic = json.loads(path.read_text(encoding="utf-8"))
    assert path.stat().st_mode & 0o777 == 0o600
    assert diagnostic["stage"] == "filesystem_probe_failed"
    assert len(diagnostic["stdout"]) == 8192
    assert diagnostic["stdout_truncated"] is True
    assert diagnostic["stderr"] == "synthetic-error\n"


def test_active_policy_normalizes_only_omitted_empty_network_map(monkeypatch, tmp_path: Path) -> None:
    expected = {
        "version": 1,
        "filesystem_policy": {"read_only": ["/usr"], "read_write": ["/tmp"]},
        "network_policies": {},
        "process": {"run_as_user": "sandbox", "run_as_group": "sandbox"},
    }
    exported = {key: value for key, value in expected.items() if key != "network_policies"}
    monkeypatch.setattr(
        probe,
        "_run_command",
        lambda *args, **kwargs: probe.subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=probe.yaml.safe_dump(exported),
            stderr="",
        ),
    )
    context = type(
        "PolicyContext",
        (),
        {"project_root": tmp_path, "sandbox_name": "sandbox", "policy": expected},
    )()

    probe._validate_active_policy(context, timeout=5)

    context.policy = {**expected, "network_policies": {"search": {"allow": []}}}
    try:
        probe._validate_active_policy(context, timeout=5)
    except probe.ProbeError as exc:
        assert exc.code == "active_policy_mismatch"
    else:
        raise AssertionError("omitted non-empty network policy must not be normalized")


def _private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    candidate = path
    while True:
        candidate.chmod(0o700)
        if candidate.name == "project":
            break
        candidate = candidate.parent


def _private_file(path: Path, content: bytes) -> None:
    _private_directory(path.parent)
    path.write_bytes(content)
    path.chmod(0o600)


def _probe_plan(project: Path, probe_id: str = "probe-123456789abc") -> SecurityProbePlan:
    analysis = project / "data/wiki/companies/600104-上汽集团/analysis"
    _private_directory(analysis)
    state_dir = project / probe.SECURITY_PROBES_RELATIVE / probe_id
    _private_directory(state_dir)
    runtime = project / probe.SNAPSHOTS_RELATIVE / probe_id
    for name in ("sessions", "memories", "checkpoints", "cron"):
        _private_directory(runtime / name)
    _private_directory(runtime / probe.RUNTIME_STATE_DIRECTORY)
    _private_file(runtime / "sessions/nested/state.bin", b"runtime\n")
    mount_content = b'{"docker":{"mounts":[]}}\n'
    mount_digest = hashlib.sha256(mount_content).hexdigest()
    mount_plan = project / probe.MOUNT_PLANS_RELATIVE / f"{mount_digest}.driver-config.json"
    _private_file(mount_plan, mount_content)
    policy_content = b'{"network_policies":{},"version":1}\n'
    policy_path = state_dir / "task-policy.yaml"
    _private_file(policy_path, policy_content)
    spec = RunSpec(
        profile=probe.PROFILE,
        market="cn",
        company="600104-上汽集团",
        run_id=probe_id,
        project_root=project,
        analysis_root=analysis,
        analysis_relative_path="data/wiki/companies/600104-上汽集团/analysis",
        sandbox_name=f"siq-analysis-security-{probe_id}",
        run_dir=state_dir,
    )
    return SecurityProbePlan(
        spec=spec,
        image_ref="siq/hermes-openshell-siq-analysis:" + "b" * 24,
        image_id="sha256:" + "a" * 64,
        runtime_snapshot=runtime,
        mount_plan=mount_plan,
        mount_plan_sha256=mount_digest,
        policy_path=policy_path,
        policy_sha256=hashlib.sha256(policy_content).hexdigest(),
    )


def test_control_mounts_accept_only_read_only_docker_selinux_mode(tmp_path: Path) -> None:
    project = tmp_path / "project"
    sandbox_id = "11111111-1111-1111-1111-111111111111"
    pairs = (
        (
            project / "var/openshell/toolchains/v0.0.83/bin/openshell-sandbox",
            "/opt/openshell/bin/openshell-sandbox",
        ),
        (project / "var/openshell/gateway/siq-openshell-dev/tls/ca.crt", "/etc/openshell/tls/client/ca.crt"),
        (
            project / "var/openshell/gateway/siq-openshell-dev/tls/client/tls.crt",
            "/etc/openshell/tls/client/tls.crt",
        ),
        (
            project / "var/openshell/gateway/siq-openshell-dev/tls/client/tls.key",
            "/etc/openshell/tls/client/tls.key",
        ),
        (
            project
            / "var/openshell/xdg/state/openshell/docker-sandbox-tokens/siq-openshell-dev"
            / sandbox_id
            / "sandbox.jwt",
            "/etc/openshell/auth/sandbox.jwt",
        ),
    )
    controls = []
    for source, destination in pairs:
        _private_file(source, b"control\n")
        controls.append(
            {
                "Type": "bind",
                "Source": str(source),
                "Destination": destination,
                "Mode": "ro,z",
                "RW": False,
                "Propagation": "rprivate",
            }
        )

    probe._validate_control_mounts(project, sandbox_id, controls)

    controls[0] = {**controls[0], "RW": True}
    try:
        probe._validate_control_mounts(project, sandbox_id, controls)
    except probe.ProbeError as exc:
        assert exc.code == "control_mount_not_read_only"
    else:
        raise AssertionError("writable control mount must be rejected")


class FakeIndependentAdapter:
    def __init__(self, plan: SecurityProbePlan) -> None:
        self.plan = plan
        self.created = False
        self.deleted = False
        self.recovered = False
        self.intent_seen_before_create = False

    def security_probe_spec(self, **kwargs) -> RunSpec:
        assert kwargs["probe_id"] == self.plan.spec.run_id
        return self.plan.spec

    def prepare_security_probe_runtime(self, spec: RunSpec) -> SecurityProbePlan:
        assert spec == self.plan.spec
        return self.plan

    def create_security_probe_sandbox(self, **kwargs) -> SandboxIdentity:
        assert kwargs["mount_plan"] == self.plan.mount_plan
        assert kwargs["policy_path"] == self.plan.policy_path
        intent = json.loads((self.plan.spec.run_dir / "probe.json").read_text(encoding="utf-8"))
        nonce = (self.plan.spec.run_dir / "run.nonce").read_text(encoding="ascii").strip()
        self.intent_seen_before_create = intent["phase"] == "prepared" and nonce == kwargs["nonce"]
        self.created = True
        return SandboxIdentity(
            sandbox_id="11111111-1111-1111-1111-111111111111",
            container_id="f" * 64,
        )

    def delete_security_probe_sandbox(self, **kwargs) -> None:
        assert kwargs["nonce"]
        self.deleted = True

    def recover_security_probe_sandbox(self, **kwargs) -> None:
        assert kwargs["nonce"]
        self.recovered = True


class FailingPreparationAdapter:
    def __init__(self, spec: RunSpec) -> None:
        self.spec = spec

    def security_probe_spec(self, **kwargs) -> RunSpec:
        assert kwargs["probe_id"] == self.spec.run_id
        return self.spec

    def prepare_security_probe_runtime(self, spec: RunSpec) -> SecurityProbePlan:
        assert spec == self.spec
        _private_file(spec.run_dir / "partial-policy.json", b"partial\n")
        snapshot = spec.project_root / probe.SNAPSHOTS_RELATIVE / spec.run_id
        _private_file(snapshot / "sessions/partial.json", b"partial\n")
        raise probe.LifecycleError("task_policy_compile_failed")


def test_provider_independent_prepare_failure_removes_only_new_private_state(tmp_path: Path) -> None:
    project = tmp_path / "project"
    _private_directory(project)
    analysis = project / "data/wiki/companies/600104-上汽集团/analysis"
    _private_directory(analysis)
    probe_id = "probe-123456789abc"
    spec = RunSpec(
        profile=probe.PROFILE,
        market="cn",
        company="600104-上汽集团",
        run_id=probe_id,
        project_root=project,
        analysis_root=analysis,
        analysis_relative_path="data/wiki/companies/600104-上汽集团/analysis",
        sandbox_name=f"siq-analysis-security-{probe_id}",
        run_dir=project / probe.SECURITY_PROBES_RELATIVE / probe_id,
    )

    try:
        probe.run_provider_independent_probe(
            project_root=project,
            profile=probe.PROFILE,
            market="cn",
            company=spec.company,
            probe_id=probe_id,
            timeout=5,
            adapter=FailingPreparationAdapter(spec),
        )
    except probe.ProbeError as exc:
        assert exc.code == "task_policy_compile_failed"
        assert exc.cleanup_code == ""
    else:
        raise AssertionError("failed preparation must fail the probe")

    assert not spec.run_dir.exists()
    assert not (project / probe.SNAPSHOTS_RELATIVE / probe_id).exists()
    assert analysis.is_dir()


def test_provider_independent_probe_runs_before_formal_state_and_cleans_up(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "project"
    _private_directory(project)
    plan = _probe_plan(project)
    adapter = FakeIndependentAdapter(plan)

    monkeypatch.setattr(probe, "_validate_active_policy", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        probe,
        "_docker_inspect_container",
        lambda *args, **kwargs: _container_inspection(
            mounts=[{}] * (probe.BUSINESS_MOUNT_COUNT + probe.CONTROL_MOUNT_COUNT)
        ),
    )
    monkeypatch.setattr(
        probe,
        "validate_container_mounts",
        lambda *args, **kwargs: {
            "business_mount_count": probe.BUSINESS_MOUNT_COUNT,
            "control_mount_count": probe.CONTROL_MOUNT_COUNT,
            "total_mount_count": probe.BUSINESS_MOUNT_COUNT + probe.CONTROL_MOUNT_COUNT,
        },
    )

    def fake_exec(context, command, **kwargs):
        del kwargs
        script = command[2] if len(command) > 2 else ""
        if script == probe.FILESYSTEM_PROBE:
            name = command[-2]
            marker = command[-1].encode("ascii")
            for path in (
                context.analysis_path / name,
                context.runtime_snapshot / probe.RUNTIME_STATE_DIRECTORY / name,
                context.runtime_snapshot / "sessions" / name,
                context.runtime_snapshot / "memories" / name,
            ):
                _private_file(path, marker)
            return {"ok": True, "check": "filesystem"}
        if script == probe.PROVIDER_INDEPENDENT_NETWORK_PROBE:
            return {
                "ok": True,
                "check": "network_deny",
                "policy_evidence": "active_network_policies_empty",
                "observed": {
                    name: {"result": "denied", "reason": "EPERM"}
                    for name in ("public_https", "internal_model", "egress_broker", "cloud_metadata")
                },
            }
        if script == probe.PROVIDER_INDEPENDENT_RUNTIME_PROBE:
            return {
                "ok": True,
                "check": "runtime_isolation",
                "hermes_process_count": 0,
                "api_listener_count": 0,
                "provider_env_count": 0,
                "auth_material_count": 0,
                "provider_call_capable_processes": 0,
            }
        if script == probe.PROCESS_HARDENING_PROBE:
            return {
                "ok": True,
                "schema_version": probe.PROCESS_HARDENING_SCHEMA_VERSION,
                "check": "process_hardening",
                "uid": 1000,
                "gid": 1000,
                "controls": {name: True for name in probe.PROCESS_HARDENING_CONTROLS},
            }
        return {"ok": True, "check": "unknown_upload_deny", "curl_returncode": 7, "http_code": "000"}

    def fake_cleanup(context, sentinels, **kwargs):
        del context, kwargs
        for path in (
            sentinels.analysis_host,
            sentinels.state_host,
            sentinels.runtime_host,
            sentinels.memory_host,
        ):
            path.unlink(missing_ok=True)

    monkeypatch.setattr(probe, "_sandbox_exec_json", fake_exec)
    monkeypatch.setattr(probe, "_cleanup", fake_cleanup)

    result = probe.run_provider_independent_probe(
        project_root=project,
        profile=probe.PROFILE,
        market="cn",
        company="600104-上汽集团",
        probe_id=plan.spec.run_id,
        timeout=5,
        adapter=adapter,
    )

    assert adapter.intent_seen_before_create is True
    assert adapter.created is True and adapter.deleted is True
    assert result["mounts"] == {
        "business_mount_count": probe.BUSINESS_MOUNT_COUNT,
        "control_mount_count": probe.CONTROL_MOUNT_COUNT,
        "total_mount_count": probe.BUSINESS_MOUNT_COUNT + probe.CONTROL_MOUNT_COUNT,
    }
    assert result["provider_calls"] == 0 and result["provider_calls_observed"] is True
    assert result["container_hardening"]["privileged"] is False
    assert result["process_hardening"]["no_new_privs"] is True
    assert result["process_hardening"]["capability_sets_clear"] is True
    assert set(probe.PROCESS_HARDENING_CONTROLS).issubset(result["checks"])
    assert result["quality_validated"] is False and result["readiness_effect"] == "none"
    assert result["phase"] == "passed"
    manifest = json.loads((plan.spec.run_dir / "probe.json").read_text(encoding="utf-8"))
    assert manifest["phase"] == "passed"
    assert "running" not in json.dumps(manifest)
    assert "runtime_snapshot_removed" in result["checks"]
    assert not plan.runtime_snapshot.exists()
    assert not (plan.spec.run_dir / "run.nonce").exists()
    assert not (project / "var/openshell/siq-analysis/active-run.json").exists()
    assert not (project / "var/openshell/siq-analysis/runs").exists()


def test_provider_independent_probe_failure_deletes_sandbox_and_never_marks_running(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    _private_directory(project)
    plan = _probe_plan(project)
    adapter = FakeIndependentAdapter(plan)

    def fail_policy(*args, **kwargs):
        del args, kwargs
        raise probe.ProbeError("active_policy_mismatch")

    monkeypatch.setattr(probe, "_validate_active_policy", fail_policy)

    try:
        probe.run_provider_independent_probe(
            project_root=project,
            profile=probe.PROFILE,
            market="cn",
            company="600104-上汽集团",
            probe_id=plan.spec.run_id,
            timeout=5,
            adapter=adapter,
        )
    except probe.ProbeError as exc:
        assert exc.code == "active_policy_mismatch"
    else:
        raise AssertionError("failed policy verification must fail the probe")

    assert adapter.deleted is True
    manifest = json.loads((plan.spec.run_dir / "probe.json").read_text(encoding="utf-8"))
    assert manifest["phase"] == "failed"
    assert manifest["error_code"] == "active_policy_mismatch"
    assert "running" not in json.dumps(manifest)
    assert "runtime_snapshot_removed" in manifest["checks"]
    assert not plan.runtime_snapshot.exists()
    assert not (plan.spec.run_dir / "run.nonce").exists()


def test_recover_consumes_prepared_intent_after_interrupted_create(tmp_path: Path) -> None:
    project = tmp_path / "project"
    _private_directory(project)
    plan = _probe_plan(project)
    adapter = FakeIndependentAdapter(plan)
    nonce = "1" * 48
    manifest = probe._security_probe_manifest(plan, nonce)
    _private_file(plan.spec.run_dir / "run.nonce", f"{nonce}\n".encode())
    _private_file(
        plan.spec.run_dir / "probe.json",
        (json.dumps(manifest, ensure_ascii=True, sort_keys=True) + "\n").encode(),
    )
    plan.policy_path.unlink()
    plan.mount_plan.unlink()

    result = probe.recover_provider_independent_probe(
        project_root=project,
        probe_id=plan.spec.run_id,
        timeout=5,
        adapter=adapter,
    )

    assert adapter.recovered is True
    assert result["phase"] == "recovered"
    assert not plan.spec.run_dir.exists()
    assert not plan.runtime_snapshot.exists()


def test_recover_rejects_wrong_nonce_without_deleting_identity_state(tmp_path: Path) -> None:
    project = tmp_path / "project"
    _private_directory(project)
    plan = _probe_plan(project)
    adapter = FakeIndependentAdapter(plan)
    nonce = "1" * 48
    manifest = probe._security_probe_manifest(plan, nonce)
    _private_file(plan.spec.run_dir / "run.nonce", ("2" * 48 + "\n").encode())
    _private_file(
        plan.spec.run_dir / "probe.json",
        (json.dumps(manifest, ensure_ascii=True, sort_keys=True) + "\n").encode(),
    )

    try:
        probe.recover_provider_independent_probe(
            project_root=project,
            probe_id=plan.spec.run_id,
            timeout=5,
            adapter=adapter,
        )
    except probe.ProbeError as exc:
        assert exc.code == "security_probe_recovery_intent_invalid"
    else:
        raise AssertionError("wrong nonce must block recovery")

    assert adapter.recovered is False
    assert (plan.spec.run_dir / "probe.json").exists()
    assert (plan.spec.run_dir / "run.nonce").exists()


def test_private_tree_cleanup_validates_then_removes_nested_files(tmp_path: Path) -> None:
    project = tmp_path / "project"
    tree = project / "var/openshell/siq-analysis/runtime-snapshots/probe-123456789abc"
    _private_file(tree / "sessions/nested/state.bin", b"state\n")
    (tree / "sessions/nested").chmod(0o755)

    probe._remove_private_tree(tree, project_root=project)

    assert not tree.exists()


def test_provider_independent_cli_requires_explicit_probe_id(capsys) -> None:
    result = probe.main(
        [
            "--mode",
            "provider-independent",
            "--market",
            "cn",
            "--company",
            "600104-上汽集团",
        ]
    )

    report = json.loads(capsys.readouterr().out)
    assert result == 1
    assert report["provider_calls"] is None
    assert report["provider_calls_observed"] is False
    assert report["quality_validated"] is False
    assert report["readiness_effect"] == "none"


def test_smoke_wrapper_isolated_environment_and_recovery_contract() -> None:
    content = (probe.REPO_ROOT / "scripts/openshell/smoke_siq_analysis_sandbox.sh").read_text(encoding="utf-8")

    assert content.startswith("#!/bin/bash -p\n")
    assert "unset BASH_ENV ENV CDPATH PYTHONPATH PYTHONHOME LD_PRELOAD LD_LIBRARY_PATH" in content
    assert "unset HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY http_proxy https_proxy all_proxy no_proxy" in content
    assert "siq_openshell_acquire_maintenance_lock" in content
    assert 'SIQ_OPENSHELL_MAINTENANCE_FD="$MAINTENANCE_FD"' in content
    assert "exec /usr/bin/env -i" in content
    assert "/usr/bin/python3 -I -B" in content
    assert "--recover" in probe._parser().format_help()


def test_provider_independent_probe_scripts_cover_required_negative_controls() -> None:
    assert "169.254.169.254" in probe.PROVIDER_INDEPENDENT_NETWORK_PROBE
    assert "18792" in probe.PROVIDER_INDEPENDENT_NETWORK_PROBE
    assert "ECONNREFUSED" in probe.PROVIDER_INDEPENDENT_NETWORK_PROBE
    assert "gaierror" in probe.PROVIDER_INDEPENDENT_NETWORK_PROBE
    assert "--upload-file" in probe.PROVIDER_INDEPENDENT_UPLOAD_PROBE
    assert "/proc/net/tcp" in probe.PROVIDER_INDEPENDENT_RUNTIME_PROBE
    assert "API_SERVER_KEY" in probe.PROVIDER_INDEPENDENT_RUNTIME_PROBE
    assert "NoNewPrivs" in probe.PROCESS_HARDENING_PROBE
    assert "CapBnd" in probe.PROCESS_HARDENING_PROBE
    assert "/var/run/docker.sock" in probe.PROCESS_HARDENING_PROBE
    assert 'Path("/usr/bin/sudo")' in probe.PROCESS_HARDENING_PROBE
    assert 'Path("/usr/bin/su")' in probe.PROCESS_HARDENING_PROBE
    assert "libc.mount" in probe.PROCESS_HARDENING_PROBE
    assert "libc.unshare" in probe.PROCESS_HARDENING_PROBE
    assert "libc.setns" in probe.PROCESS_HARDENING_PROBE
    assert "stat.S_ISBLK" in probe.PROCESS_HARDENING_PROBE
    assert "os.mknod" in probe.PROCESS_HARDENING_PROBE
