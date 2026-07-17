from __future__ import annotations

import ctypes
import importlib.util
import os
import select
import shutil
import signal
import subprocess
import sys
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "gateway_start_recovery.py"
PR_SET_PTRACER = 0x59616D61
PR_SET_PTRACER_ANY = -1


def _allow_test_recovery_child_to_inspect_process() -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(PR_SET_PTRACER, PR_SET_PTRACER_ANY, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "PR_SET_PTRACER failed")


def _modules():
    spec = importlib.util.spec_from_file_location("siq_gateway_stop_recovery_under_test", SOURCE)
    assert spec and spec.loader
    recovery = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = recovery
    spec.loader.exec_module(recovery)
    return recovery, sys.modules["gateway_runtime_identity"]


def _private_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)


def test_recover_finishes_stop_after_crash_between_pidfd_signal_and_evidence_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recovery, identity = _modules()
    project_root = tmp_path / "project"
    project_root.mkdir(mode=0o700)
    paths = identity._paths(project_root)

    paths["binary"].parent.mkdir(parents=True, mode=0o700)
    cat_binary = shutil.which("cat")
    assert cat_binary is not None
    shutil.copyfile(cat_binary, paths["binary"])
    paths["binary"].chmod(0o700)
    _private_file(paths["config"], "[gateway]\n")

    environment = dict(os.environ)
    environment.update(
        {
            "OPENSHELL_GATEWAY_CONFIG": str(paths["config"]),
            "OPENSHELL_DB_URL": f"sqlite:{paths['database']}",
            "OPENSHELL_TELEMETRY_ENABLED": "false",
            "OPENSHELL_GATEWAY": identity.GATEWAY_NAME,
        }
    )
    process = subprocess.Popen(
        [str(paths["binary"])],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=environment,
        preexec_fn=_allow_test_recovery_child_to_inspect_process,
    )
    recovery_child: int | None = None
    recovery_pidfd: int | None = None

    try:
        monkeypatch.setattr(identity, "_verify_listener_ownership", lambda _pid: None)
        monkeypatch.setattr(identity, "_verify_health", lambda: None)
        monkeypatch.setattr(recovery, "_listening_sockets", lambda _ports: [])

        recovery.prepare_start(project_root)
        assert recovery.attach_start(project_root, process.pid) == process.pid
        recovery.commit_start(project_root, process.pid)
        assert paths["runtime"].exists()
        assert paths["pid_file"].exists()
        assert not paths["start_intent"].exists()
        assert not paths["starting"].exists()

        ready_read, ready_write = os.pipe()
        recovery_child = os.fork()
        if recovery_child == 0:
            os.close(ready_read)

            def pause_before_evidence_cleanup(_paths) -> None:
                os.write(ready_write, b"ready\n")
                while True:
                    signal.pause()

            recovery._remove_stopped_runtime_evidence = pause_before_evidence_cleanup
            try:
                recovery.recover_start(project_root, reap=True)
            except BaseException as exc:
                message = f"error:{type(exc).__name__}:{exc}\n".encode("utf-8", errors="replace")
                os.write(ready_write, message[:4096])
                os._exit(101)
            os._exit(102)

        os.close(ready_write)
        recovery_pidfd = identity._pidfd_open(recovery_child)
        readable, _, _ = select.select([ready_read], [], [], 10.0)
        assert readable, "recovery did not reach the post-signal, pre-cleanup crash point"
        child_message = os.read(ready_read, 4096)
        assert child_message == b"ready\n", child_message.decode("utf-8", errors="replace")
        os.close(ready_read)

        process.wait(timeout=5)
        assert all(paths[name].exists() for name in ("runtime", "start_intent", "starting", "pid_file"))

        identity._pidfd_send_signal(recovery_pidfd, signal.SIGKILL)
        _, status = os.waitpid(recovery_child, 0)
        assert os.WIFSIGNALED(status)
        assert os.WTERMSIG(status) == signal.SIGKILL
        recovery_child = None
        os.close(recovery_pidfd)
        recovery_pidfd = None

        assert recovery.recover_start(project_root) == "stopped"
        assert all(not paths[name].exists() for name in ("runtime", "start_intent", "starting", "pid_file"))
        assert recovery.recover_start(project_root) == "stopped"
    finally:
        if recovery_child is not None:
            if recovery_pidfd is None:
                recovery_pidfd = identity._pidfd_open(recovery_child)
            identity._pidfd_send_signal(recovery_pidfd, signal.SIGKILL)
            os.waitpid(recovery_child, 0)
        if recovery_pidfd is not None:
            os.close(recovery_pidfd)
        if process.poll() is None:
            descriptor = identity._pidfd_open(process.pid)
            try:
                identity._pidfd_send_signal(descriptor, identity.signal.SIGTERM)
            finally:
                os.close(descriptor)
            process.wait(timeout=5)
        if process.stdin is not None:
            process.stdin.close()
