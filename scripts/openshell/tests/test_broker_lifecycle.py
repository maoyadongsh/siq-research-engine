from __future__ import annotations

import json
import socket
import stat
from pathlib import Path
from typing import Callable, Mapping, Sequence

import pytest

from scripts.openshell import bridge_endpoint, broker_lifecycle as module


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "scripts/openshell").mkdir(parents=True)
    (root / "var/openshell").mkdir(parents=True, mode=0o700)
    (root / "var/openshell").chmod(0o700)
    for spec in module.BROKER_SPECS:
        (root / "scripts/openshell" / spec.script_name).write_text("# fake broker\n", encoding="utf-8")
    return root


def _endpoint(*, network_id: str = "a" * 64, gateway_ip: str = "172.28.0.1") -> bridge_endpoint.BridgeEndpoint:
    return bridge_endpoint.BridgeEndpoint(
        network_name="siq-openshell-dev",
        network_id=network_id,
        subnet="172.28.0.0/16",
        gateway_ip=gateway_ip,
    )


def _environment(**overrides: str) -> dict[str, str]:
    values = {
        "PATH": "/usr/bin:/bin",
        "SIQ_OPENSHELL_PG_RO_HOST": "127.0.0.1",
        "SIQ_OPENSHELL_PG_RO_PORT": "15432",
        "SIQ_OPENSHELL_PG_RO_USER": "siq_openshell_reader",
        "SIQ_OPENSHELL_PG_RO_PASSWORD": "postgres-secret-fixture",
        "SIQ_OPENSHELL_MILVUS_RO_TOKEN": "milvus-secret-fixture",
        "UNRELATED_MODEL_API_KEY": "must-not-reach-brokers",
    }
    values.update(overrides)
    return values


class FakeRuntime:
    def __init__(self, endpoint: bridge_endpoint.BridgeEndpoint, *, fail_port: int | None = None) -> None:
        self.endpoint = endpoint
        self.fail_port = fail_port
        self.processes: dict[int, module.ProcessInfo] = {}
        self.bound: dict[int, module.Listener] = {}
        self.spawn_calls: list[dict[str, object]] = []
        self.terminated: list[int] = []
        self.pidfd_opened: list[int] = []
        self.pidfd_signaled: list[int] = []
        self.before_terminate_verify: Callable[[int], None] | None = None
        self.next_pid = 4100

    def spawn(self, command: Sequence[str], *, env: Mapping[str, str], log_path: Path) -> int:
        self.next_pid += 1
        pid = self.next_pid
        port = int(command[command.index("--port") + 1])
        self.processes[pid] = module.ProcessInfo(
            pid=pid,
            executable=str(Path(command[0]).resolve()),
            cmdline=tuple(command),
            start_ticks=100_000 + pid,
        )
        if port != self.fail_port:
            self.bound[port] = module.Listener(
                address=self.endpoint.gateway_ip,
                port=port,
                pids=frozenset({pid}),
            )
        self.spawn_calls.append(
            {"command": tuple(command), "env": dict(env), "log_path": log_path, "pid": pid, "port": port}
        )
        return pid

    def process_info(self, pid: int) -> module.ProcessInfo | None:
        return self.processes.get(pid)

    def matching_pids(self, command: Sequence[str]) -> tuple[int, ...]:
        expected = tuple(command)
        return tuple(sorted(pid for pid, info in self.processes.items() if info.cmdline == expected))

    def listeners(self, port: int) -> tuple[module.Listener, ...]:
        listener = self.bound.get(port)
        return (listener,) if listener is not None else ()

    def health(
        self,
        *,
        host: str,
        port: int,
        path: str,
        host_alias: str,
        service_name: str,
    ) -> bool:
        assert host == self.endpoint.gateway_ip
        assert host_alias == "host.openshell.internal"
        assert path in {"/health", "/healthz"}
        assert service_name in {"siq-egress-guard", "siq-read-only-data-broker"}
        return port in self.bound and port != self.fail_port

    def terminate(self, pid: int, *, verify: Callable[[], bool]) -> None:
        self.pidfd_opened.append(pid)
        if self.before_terminate_verify is not None:
            self.before_terminate_verify(pid)
        if not verify():
            return
        self.pidfd_signaled.append(pid)
        self.terminated.append(pid)
        self.processes.pop(pid, None)
        for port, listener in list(self.bound.items()):
            if pid in listener.pids:
                self.bound.pop(port)

    def sleep(self, _seconds: float) -> None:
        return


class DelayedListenerCleanupRuntime(FakeRuntime):
    """Model the short /proc listener lag observed after a real process exits."""

    def __init__(self, endpoint: bridge_endpoint.BridgeEndpoint) -> None:
        super().__init__(endpoint)
        self.pending_listener_polls: dict[int, int] = {}

    def terminate(self, pid: int, *, verify: Callable[[], bool]) -> None:
        owned = [port for port, listener in self.bound.items() if pid in listener.pids]
        super().terminate(pid, verify=verify)
        for port in owned:
            self.pending_listener_polls[port] = 1

    def listeners(self, port: int) -> tuple[module.Listener, ...]:
        remaining = self.pending_listener_polls.get(port, 0)
        if remaining:
            self.pending_listener_polls[port] = remaining - 1
            return (module.Listener(self.endpoint.gateway_ip, port, frozenset()),)
        return super().listeners(port)


def _lifecycle(
    project: Path,
    runtime: FakeRuntime,
    *,
    endpoint: bridge_endpoint.BridgeEndpoint | None = None,
    environ: Mapping[str, str] | None = None,
    startup_attempts: int = 3,
    require_request_identity: bool = False,
) -> module.BrokerLifecycle:
    selected = endpoint or runtime.endpoint
    return module.BrokerLifecycle(
        project_root=project,
        backend=runtime,
        discoverer=lambda: selected,
        environ=environ or _environment(),
        startup_attempts=startup_attempts,
        stop_attempts=3,
        require_request_identity=require_request_identity,
    )


def test_start_uses_fixed_ports_bridge_mode_and_minimal_child_environment(tmp_path: Path) -> None:
    project = _project(tmp_path)
    endpoint = _endpoint()
    runtime = FakeRuntime(endpoint)
    environment = _environment()
    lifecycle = _lifecycle(project, runtime, environ=environment)

    result = lifecycle.start()

    assert result["ok"] is True
    assert result["started_by_this_call"] == ["egress", "data"]
    assert [call["port"] for call in runtime.spawn_calls] == [18_792, 18_793]
    for call in runtime.spawn_calls:
        command = call["command"]
        assert "--bridge-bind" in command
        assert "--bind-host" not in command
        assert "--host" not in command
        child_env = call["env"]
        assert child_env["HOME"] == str(project / module.STATE_RELATIVE_ROOT)
        if call["port"] == 18_793:
            assert child_env["SIQ_OPENSHELL_PG_RO_PASSWORD"] == "postgres-secret-fixture"
            assert child_env["SIQ_OPENSHELL_MILVUS_RO_TOKEN"] == "milvus-secret-fixture"
        else:
            assert "SIQ_OPENSHELL_PG_RO_PASSWORD" not in child_env
            assert "SIQ_OPENSHELL_MILVUS_RO_TOKEN" not in child_env
        assert "UNRELATED_MODEL_API_KEY" not in child_env
        assert "must-not-reach-brokers" not in child_env.values()
        serialized_command = "\0".join(command)
        assert "postgres-secret-fixture" not in serialized_command
        assert "milvus-secret-fixture" not in serialized_command
        assert "SIQ_OPENSHELL_PG_RO_PASSWORD" not in serialized_command
        assert "SIQ_OPENSHELL_MILVUS_RO_TOKEN" not in serialized_command
    state_root = project / module.STATE_RELATIVE_ROOT
    assert stat.S_IMODE(state_root.stat().st_mode) == 0o700
    for name in ("egress.pid", "egress.log", "data.pid", "data.log", "bridge.json"):
        path = state_root / name
        assert path.is_file()
        assert not path.is_symlink()
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    bridge_state = json.loads((state_root / "bridge.json").read_text(encoding="utf-8"))
    assert bridge_state == endpoint.as_dict()
    logs = (state_root / "egress.log").read_text() + (state_root / "data.log").read_text()
    assert "secret-fixture" not in logs


def test_project_secret_file_loads_only_exact_postgres_contract(tmp_path: Path) -> None:
    project = _project(tmp_path)
    secret = project / module.POSTGRES_SECRET_RELATIVE_PATH
    secret.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    secret.write_text(
        "SIQ_OPENSHELL_PG_RO_HOST=127.0.0.1\n"
        "SIQ_OPENSHELL_PG_RO_PORT=15432\n"
        "SIQ_OPENSHELL_PG_RO_USER=siq_openshell_reader\n"
        "SIQ_OPENSHELL_PG_RO_PASSWORD=reader-secret-fixture\n"
        "SIQ_OPENSHELL_PG_RO_SSLMODE=prefer\n",
        encoding="utf-8",
    )
    secret.chmod(0o600)

    loaded = module._load_project_postgres_environment(project)

    assert set(loaded) == {
        "SIQ_OPENSHELL_PG_RO_HOST",
        "SIQ_OPENSHELL_PG_RO_PORT",
        "SIQ_OPENSHELL_PG_RO_USER",
        "SIQ_OPENSHELL_PG_RO_PASSWORD",
        "SIQ_OPENSHELL_PG_RO_SSLMODE",
    }
    assert "DATABASE" not in " ".join(loaded)


def test_project_secret_file_rejects_mode_unknown_key_and_environment_conflict(tmp_path: Path) -> None:
    project = _project(tmp_path)
    secret = project / module.POSTGRES_SECRET_RELATIVE_PATH
    secret.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    valid = (
        "SIQ_OPENSHELL_PG_RO_HOST=127.0.0.1\n"
        "SIQ_OPENSHELL_PG_RO_PORT=15432\n"
        "SIQ_OPENSHELL_PG_RO_USER=siq_openshell_reader\n"
        "SIQ_OPENSHELL_PG_RO_PASSWORD=reader-secret-fixture\n"
        "SIQ_OPENSHELL_PG_RO_SSLMODE=prefer\n"
    )
    secret.write_text(valid, encoding="utf-8")
    secret.chmod(0o640)
    with pytest.raises(module.LifecycleError, match="secret_file_unsafe"):
        module._load_project_postgres_environment(project)

    secret.chmod(0o600)
    secret.write_text(valid + "SIQ_OPENSHELL_PG_RO_DATABASE=siq\n", encoding="utf-8")
    with pytest.raises(module.LifecycleError, match="secret_file_invalid"):
        module._load_project_postgres_environment(project)

    secret.write_text(valid, encoding="utf-8")
    with pytest.raises(module.LifecycleError, match="environment_conflict"):
        module._startup_environment(project, {"SIQ_OPENSHELL_PG_RO_USER": "other"})


def test_project_mihomo_runtime_auto_enables_only_for_present_safe_socket(tmp_path: Path) -> None:
    project = _project(tmp_path)
    config = project / module.MIHOMO_RUNTIME_RELATIVE_PATH
    config.parent.mkdir(parents=True)
    control_socket = tmp_path / "mihomo.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        listener.bind(str(control_socket))
        config.write_text(
            json.dumps(
                {
                    "schema_version": module.MIHOMO_RUNTIME_SCHEMA,
                    "mode": "auto_if_socket_present",
                    "control_socket": str(control_socket),
                    "fake_ip_range": "198.18.0.0/16",
                }
            ),
            encoding="utf-8",
        )
        config.chmod(0o644)

        resolved = module._startup_environment(project, _environment())

        assert resolved["SIQ_OPENSHELL_MIHOMO_FAKE_IP_COMPAT"] == "1"
        assert resolved["SIQ_OPENSHELL_MIHOMO_CONTROL_SOCKET"] == str(control_socket)
        assert resolved["SIQ_OPENSHELL_MIHOMO_FAKE_IP_RANGE"] == "198.18.0.0/16"
    finally:
        listener.close()
        control_socket.unlink(missing_ok=True)


def test_project_mihomo_runtime_missing_socket_is_noop_and_explicit_off_wins(tmp_path: Path) -> None:
    project = _project(tmp_path)
    config = project / module.MIHOMO_RUNTIME_RELATIVE_PATH
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps(
            {
                "schema_version": module.MIHOMO_RUNTIME_SCHEMA,
                "mode": "auto_if_socket_present",
                "control_socket": str(tmp_path / "missing.sock"),
                "fake_ip_range": "198.18.0.0/16",
            }
        ),
        encoding="utf-8",
    )
    config.chmod(0o644)

    resolved = module._startup_environment(project, _environment())
    explicit = module._startup_environment(
        project,
        _environment(SIQ_OPENSHELL_MIHOMO_FAKE_IP_COMPAT="0"),
    )

    assert not any(name in resolved for name in module.MIHOMO_ENV_NAMES)
    assert explicit["SIQ_OPENSHELL_MIHOMO_FAKE_IP_COMPAT"] == "0"
    assert "SIQ_OPENSHELL_MIHOMO_CONTROL_SOCKET" not in explicit


def test_project_mihomo_runtime_rejects_duplicate_or_world_writable_config(tmp_path: Path) -> None:
    project = _project(tmp_path)
    config = project / module.MIHOMO_RUNTIME_RELATIVE_PATH
    config.parent.mkdir(parents=True)
    config.write_text(
        '{"schema_version":"siq.openshell.mihomo-runtime.v1",'
        '"schema_version":"siq.openshell.mihomo-runtime.v1",'
        '"mode":"auto_if_socket_present","control_socket":"/missing.sock",'
        '"fake_ip_range":"198.18.0.0/16"}',
        encoding="utf-8",
    )
    config.chmod(0o644)
    with pytest.raises(module.LifecycleError, match="config_invalid"):
        module._startup_environment(project, _environment())

    config.write_text("{}\n", encoding="utf-8")
    config.chmod(0o666)
    with pytest.raises(module.LifecycleError, match="config_unsafe"):
        module._startup_environment(project, _environment())


def test_repository_mihomo_runtime_config_is_non_secret_and_fail_closed() -> None:
    path = module.REPO_ROOT / module.MIHOMO_RUNTIME_RELATIVE_PATH
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload == {
        "schema_version": module.MIHOMO_RUNTIME_SCHEMA,
        "mode": "auto_if_socket_present",
        "control_socket": "/tmp/verge/verge-mihomo.sock",
        "fake_ip_range": "198.18.0.0/16",
    }
    assert stat.S_IMODE(path.stat().st_mode) & 0o002 == 0
    serialized = json.dumps(payload).lower()
    assert all(term not in serialized for term in ("password", "api_key", "token", "cookie"))


def test_start_is_idempotent_only_after_pid_cmdline_listener_and_health_cross_validation(tmp_path: Path) -> None:
    project = _project(tmp_path)
    runtime = FakeRuntime(_endpoint())
    lifecycle = _lifecycle(project, runtime)

    lifecycle.start()
    first_spawn_count = len(runtime.spawn_calls)
    result = lifecycle.start()

    assert result["ok"] is True
    assert result["schema_version"] == module.SCHEMA_VERSION
    assert len(runtime.spawn_calls) == first_spawn_count
    status, valid = lifecycle.status()
    assert valid is True
    assert status["schema_version"] == module.SCHEMA_VERSION
    assert status["brokers"]["egress"]["state"] == "running"
    assert status["brokers"]["data"]["state"] == "running"


def test_strict_start_generates_private_key_and_binds_both_brokers_to_it(tmp_path: Path) -> None:
    project = _project(tmp_path)
    runtime = FakeRuntime(_endpoint())
    lifecycle = _lifecycle(project, runtime, require_request_identity=True)

    result = lifecycle.start()

    assert result["request_identity_required"] is True
    key_path = project / module.IDENTITY_KEY_RELATIVE_PATH
    key = module.broker_request_identity.read_key_file(key_path)
    key_digest = module.hashlib.sha256(key).hexdigest()
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600
    for call in runtime.spawn_calls:
        child_env = call["env"]
        assert child_env["SIQ_OPENSHELL_REQUIRE_REQUEST_IDENTITY"] == "1"
        assert child_env["SIQ_OPENSHELL_BROKER_IDENTITY_KEY_FILE"] == str(key_path)
    for spec in module.BROKER_SPECS:
        state = json.loads((project / module.STATE_RELATIVE_ROOT / f"{spec.name}.pid").read_text())
        assert state["schema_version"] == module.PID_SCHEMA_VERSION
        assert state["request_identity_required"] is True
        assert state["identity_key_sha256"] == key_digest
    status, valid = lifecycle.status()
    assert valid is True
    assert all(item["request_identity_required"] is True for item in status["brokers"].values())


def test_formal_status_rejects_permissive_brokers_but_default_status_remains_compatible(tmp_path: Path) -> None:
    project = _project(tmp_path)
    runtime = FakeRuntime(_endpoint())
    default = _lifecycle(project, runtime)
    default.start()

    status, valid = default.status()
    assert valid is True
    assert all(item["request_identity_required"] is False for item in status["brokers"].values())

    formal = _lifecycle(project, runtime, require_request_identity=True)
    strict_status, strict_valid = formal.status()
    assert strict_valid is False
    assert all(item["state"] == "invalid" for item in strict_status["brokers"].values())


def test_strict_status_detects_key_rotation_or_replacement(tmp_path: Path) -> None:
    project = _project(tmp_path)
    runtime = FakeRuntime(_endpoint())
    lifecycle = _lifecycle(project, runtime, require_request_identity=True)
    lifecycle.start()
    key_path = project / module.IDENTITY_KEY_RELATIVE_PATH
    module.broker_request_identity.rotate_key_file(key_path)

    status, valid = lifecycle.status()

    assert valid is False
    assert all(item["state"] == "invalid" for item in status["brokers"].values())
    lifecycle.stop()


def test_key_rotation_requires_stopped_brokers_and_no_active_formal_run(tmp_path: Path) -> None:
    project = _project(tmp_path)
    runtime = FakeRuntime(_endpoint())
    lifecycle = _lifecycle(project, runtime, require_request_identity=True)
    lifecycle.start()
    with pytest.raises(module.LifecycleError, match="rotation_brokers_running"):
        lifecycle.rotate_identity_key()
    lifecycle.stop()

    active = project / module.ACTIVE_FORMAL_RUN_RELATIVE_PATH
    active.parent.mkdir(parents=True, exist_ok=True)
    active.write_text("{}\n", encoding="ascii")
    with pytest.raises(module.LifecycleError, match="rotation_active_run"):
        lifecycle.rotate_identity_key()
    active.unlink()

    before = module.broker_request_identity.read_key_file(project / module.IDENTITY_KEY_RELATIVE_PATH)
    result = lifecycle.rotate_identity_key()
    after = module.broker_request_identity.read_key_file(project / module.IDENTITY_KEY_RELATIVE_PATH)
    assert result["operation"] == "rotated"
    assert result["key_sha256"] == module.hashlib.sha256(after).hexdigest()
    assert after != before


def test_status_error_still_emits_versioned_contract(tmp_path: Path) -> None:
    project = _project(tmp_path)
    runtime = FakeRuntime(_endpoint())

    def unavailable():
        raise module.LifecycleError("bridge_missing")

    lifecycle = module.BrokerLifecycle(
        project_root=project,
        backend=runtime,
        discoverer=unavailable,
        environ=_environment(),
    )

    status, valid = lifecycle.status()

    assert valid is False
    assert status == {
        "schema_version": module.SCHEMA_VERSION,
        "ok": False,
        "action": "status",
        "error_code": "verified_bridge_unavailable",
    }


def test_second_broker_failure_rolls_back_every_process_started_by_this_attempt(tmp_path: Path) -> None:
    project = _project(tmp_path)
    runtime = FakeRuntime(_endpoint(), fail_port=18_793)
    lifecycle = _lifecycle(project, runtime, startup_attempts=2)

    with pytest.raises(module.LifecycleError, match="data_startup_timeout"):
        lifecycle.start()

    assert runtime.processes == {}
    assert runtime.bound == {}
    assert len(runtime.terminated) == 2
    state_root = project / module.STATE_RELATIVE_ROOT
    assert not (state_root / "egress.pid").exists()
    assert not (state_root / "data.pid").exists()
    assert not (state_root / "bridge.json").exists()


def test_missing_postgres_host_environment_fails_before_docker_or_spawn(tmp_path: Path) -> None:
    project = _project(tmp_path)
    endpoint = _endpoint()
    runtime = FakeRuntime(endpoint)
    calls = 0

    def discover() -> bridge_endpoint.BridgeEndpoint:
        nonlocal calls
        calls += 1
        return endpoint

    environment = _environment()
    environment.pop("SIQ_OPENSHELL_PG_RO_PASSWORD")
    lifecycle = module.BrokerLifecycle(
        project_root=project,
        backend=runtime,
        discoverer=discover,
        environ=environment,
    )

    with pytest.raises(module.LifecycleError, match="environment_missing"):
        lifecycle.start()

    assert calls == 0
    assert runtime.spawn_calls == []


@pytest.mark.parametrize("tamper", ["cmdline", "executable", "start_ticks", "listener_address", "listener_pid"])
def test_status_detects_process_or_listener_identity_tampering(tmp_path: Path, tamper: str) -> None:
    project = _project(tmp_path)
    runtime = FakeRuntime(_endpoint())
    lifecycle = _lifecycle(project, runtime)
    lifecycle.start()
    egress_call = runtime.spawn_calls[0]
    pid = int(egress_call["pid"])
    info = runtime.processes[pid]
    if tamper == "cmdline":
        runtime.processes[pid] = module.ProcessInfo(pid, info.executable, (*info.cmdline, "--other"), info.start_ticks)
    elif tamper == "executable":
        runtime.processes[pid] = module.ProcessInfo(
            pid,
            str(Path("/bin/sh").resolve(strict=True)),
            info.cmdline,
            info.start_ticks,
        )
    elif tamper == "start_ticks":
        runtime.processes[pid] = module.ProcessInfo(pid, info.executable, info.cmdline, info.start_ticks + 1)
    elif tamper == "listener_address":
        runtime.bound[18_792] = module.Listener("0.0.0.0", 18_792, frozenset({pid}))
    else:
        runtime.bound[18_792] = module.Listener(runtime.endpoint.gateway_ip, 18_792, frozenset({9999}))

    status, valid = lifecycle.status()

    assert valid is False
    assert status["brokers"]["egress"]["state"] == "invalid"
    assert status["brokers"]["egress"]["error_code"].startswith("egress_")


def test_status_rejects_recorded_command_digest_tampering(tmp_path: Path) -> None:
    project = _project(tmp_path)
    runtime = FakeRuntime(_endpoint())
    lifecycle = _lifecycle(project, runtime)
    lifecycle.start()
    state_path = project / module.STATE_RELATIVE_ROOT / "egress.pid"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["command_digest"] = "f" * 64
    state_path.write_text(json.dumps(state), encoding="utf-8")

    status, valid = lifecycle.status()

    assert valid is False
    assert status["brokers"]["egress"] == {
        "port": 18_792,
        "state": "invalid",
        "error_code": "egress_process_identity_mismatch",
    }
    assert runtime.pidfd_signaled == []


def test_status_reports_stale_record_when_recorded_process_has_exited(tmp_path: Path) -> None:
    project = _project(tmp_path)
    runtime = FakeRuntime(_endpoint())
    lifecycle = _lifecycle(project, runtime)
    lifecycle.start()
    egress_pid = int(runtime.spawn_calls[0]["pid"])
    runtime.processes.pop(egress_pid)
    runtime.bound.pop(18_792)

    status, valid = lifecycle.status()

    assert valid is False
    assert status["brokers"]["egress"]["state"] == "stale"
    assert status["brokers"]["egress"]["pid"] == egress_pid
    assert (project / module.STATE_RELATIVE_ROOT / "egress.pid").is_file()


def test_stop_signals_only_cross_verified_processes_then_removes_pid_and_bridge_state(tmp_path: Path) -> None:
    project = _project(tmp_path)
    runtime = FakeRuntime(_endpoint())
    lifecycle = _lifecycle(project, runtime)
    lifecycle.start()
    pids = [int(call["pid"]) for call in runtime.spawn_calls]

    result = lifecycle.stop()

    assert result["ok"] is True
    assert runtime.pidfd_opened == list(reversed(pids))
    assert runtime.pidfd_signaled == list(reversed(pids))
    assert runtime.terminated == list(reversed(pids))


def test_stop_waits_for_listener_cleanup_after_process_exit(tmp_path: Path) -> None:
    project = _project(tmp_path)
    runtime = DelayedListenerCleanupRuntime(_endpoint())
    lifecycle = _lifecycle(project, runtime)
    lifecycle.start()

    result = lifecycle.stop()

    assert result["ok"] is True
    assert runtime.processes == {}
    assert runtime.pending_listener_polls == {18_792: 0, 18_793: 0}
    assert runtime.processes == {}
    state_root = project / module.STATE_RELATIVE_ROOT
    assert not (state_root / "egress.pid").exists()
    assert not (state_root / "data.pid").exists()
    assert not (state_root / "bridge.json").exists()


def test_status_and_stop_use_recorded_command_across_verifier_interpreters(tmp_path: Path) -> None:
    project = _project(tmp_path)
    runtime = FakeRuntime(_endpoint())
    starter = _lifecycle(project, runtime, require_request_identity=True)
    alternate_interpreter = str(Path("/bin/sh").resolve(strict=True))
    assert alternate_interpreter != starter.python_executable
    starter.python_executable = alternate_interpreter
    starter.start()
    pids = [int(call["pid"]) for call in runtime.spawn_calls]

    verifier = _lifecycle(project, runtime, require_request_identity=True)
    assert verifier.python_executable != starter.python_executable
    status, valid = verifier.status()

    assert valid is True
    assert all(item["state"] == "running" for item in status["brokers"].values())
    assert all(item["request_identity_required"] is True for item in status["brokers"].values())

    result = verifier.stop()

    assert result["ok"] is True
    assert runtime.pidfd_opened == list(reversed(pids))
    assert runtime.pidfd_signaled == list(reversed(pids))
    assert runtime.terminated == list(reversed(pids))


def test_stop_refuses_pid_reuse_or_unrelated_command_without_signalling(tmp_path: Path) -> None:
    project = _project(tmp_path)
    runtime = FakeRuntime(_endpoint())
    lifecycle = _lifecycle(project, runtime)
    lifecycle.start()
    data_pid = int(runtime.spawn_calls[1]["pid"])
    info = runtime.processes[data_pid]
    runtime.processes[data_pid] = module.ProcessInfo(
        pid=data_pid,
        executable=info.executable,
        cmdline=(info.executable, "unrelated.py"),
        start_ticks=info.start_ticks + 5,
    )

    with pytest.raises(module.LifecycleError, match="data_process_identity_mismatch"):
        lifecycle.stop()

    assert runtime.terminated == []


def test_stop_rechecks_identity_after_pidfd_open_and_sends_no_signal_on_pid_reuse(tmp_path: Path) -> None:
    project = _project(tmp_path)
    runtime = FakeRuntime(_endpoint())
    lifecycle = _lifecycle(project, runtime)
    lifecycle.start()
    data_pid = int(runtime.spawn_calls[1]["pid"])
    original = runtime.processes[data_pid]

    def reuse_pid_after_open(pid: int) -> None:
        assert pid == data_pid
        runtime.processes[pid] = module.ProcessInfo(
            pid=pid,
            executable=original.executable,
            cmdline=(original.executable, "unrelated.py"),
            start_ticks=original.start_ticks + 1,
        )

    runtime.before_terminate_verify = reuse_pid_after_open

    with pytest.raises(module.LifecycleError, match="data_process_identity_mismatch"):
        lifecycle.stop()

    assert runtime.pidfd_opened == [data_pid]
    assert runtime.pidfd_signaled == []
    assert runtime.terminated == []


def test_proc_backend_opens_pidfd_then_verifies_and_signals_with_reviewed_helper(monkeypatch) -> None:
    events: list[tuple[str, int]] = []

    def open_pidfd(pid: int) -> int:
        events.append(("open", pid))
        return 73

    def verify() -> bool:
        events.append(("verify", 73))
        return True

    monkeypatch.setattr(module, "_pidfd_open", open_pidfd)
    monkeypatch.setattr(
        module,
        "_pidfd_send_signal",
        lambda descriptor, signum: events.append(("signal", descriptor if signum == module.signal.SIGTERM else -1)),
    )
    monkeypatch.setattr(module.os, "close", lambda descriptor: events.append(("close", descriptor)))

    module.ProcRuntimeBackend().terminate(4242, verify=verify)

    assert events == [("open", 4242), ("verify", 73), ("signal", 73), ("close", 73)]


def test_broker_lifecycle_has_no_raw_pid_signal_fallback() -> None:
    source = (Path(__file__).resolve().parents[1] / "broker_lifecycle.py").read_text(encoding="utf-8")

    assert "os.kill(" not in source
    assert "gateway_runtime_identity import GatewayRuntimeError, _pidfd_open, _pidfd_send_signal" in source


def test_start_refuses_an_unowned_listener_on_either_fixed_port(tmp_path: Path) -> None:
    project = _project(tmp_path)
    endpoint = _endpoint()
    runtime = FakeRuntime(endpoint)
    runtime.bound[18_792] = module.Listener(endpoint.gateway_ip, 18_792, frozenset({9999}))
    lifecycle = _lifecycle(project, runtime)

    with pytest.raises(module.LifecycleError, match="egress_port_occupied"):
        lifecycle.start()

    assert runtime.spawn_calls == []


def test_network_recreation_during_start_triggers_full_rollback(tmp_path: Path) -> None:
    project = _project(tmp_path)
    first = _endpoint(network_id="a" * 64)
    second = _endpoint(network_id="b" * 64)
    runtime = FakeRuntime(first)
    discoveries = iter((first, second))
    lifecycle = module.BrokerLifecycle(
        project_root=project,
        backend=runtime,
        discoverer=lambda: next(discoveries),
        environ=_environment(),
        startup_attempts=2,
        stop_attempts=2,
    )

    with pytest.raises(module.LifecycleError, match="bridge_changed_during_start"):
        lifecycle.start()

    assert runtime.processes == {}
    assert len(runtime.terminated) == 2


def test_lifecycle_wrappers_are_thin_and_do_not_touch_main_runtime_or_secrets() -> None:
    root = Path(__file__).resolve().parents[3]
    for action in ("start", "stop", "status"):
        content = (root / f"scripts/openshell/{action}_brokers.sh").read_text(encoding="utf-8")
        assert f'broker_lifecycle.py" {action}' in content
        assert "start_all.sh" not in content
        assert "PASSWORD" not in content
        assert "TOKEN" not in content
        assert "docker" not in content


def test_proc_ipv4_and_ipv6_listener_decoding_is_deterministic() -> None:
    assert module._decode_proc_address("0100007F", ipv6=False) == "127.0.0.1"
    assert module._decode_proc_address("00000000000000000000000001000000", ipv6=True) == "::1"
