from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "gateway_runtime_identity.py"
ROOT = Path(__file__).resolve().parents[3]


def _module():
    spec = importlib.util.spec_from_file_location("siq_gateway_runtime_identity_under_test", SOURCE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _payload(module) -> dict[str, object]:
    return {
        "schema": module.SCHEMA,
        "pid": 1234,
        "start_ticks": 5678,
        "executable": "/project/var/openshell/bin/openshell-gateway",
        "binary_sha256": "a" * 64,
        "cmdline_sha256": "b" * 64,
        "config_path": "/project/var/openshell/gateway/gateway.toml",
        "config_sha256": "c" * 64,
        "activation_sha256": "absent",
        "db_path": "/project/var/openshell/gateway/openshell.db",
        "created_at": "2026-07-15T00:00:00Z",
    }


def _write_record(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)


def test_runtime_record_requires_exact_private_schema(tmp_path: Path) -> None:
    module = _module()
    record = tmp_path / "gateway.runtime.json"
    payload = _payload(module)
    _write_record(record, payload)

    assert module.load_runtime_identity(record) == payload

    record.chmod(0o644)
    with pytest.raises(module.GatewayRuntimeError, match="unsafe"):
        module.load_runtime_identity(record)
    record.chmod(0o600)
    payload["extra"] = "not-allowed"
    _write_record(record, payload)
    with pytest.raises(module.GatewayRuntimeError, match="schema"):
        module.load_runtime_identity(record)


def test_verify_detects_any_process_or_config_identity_drift(tmp_path: Path, monkeypatch) -> None:
    module = _module()
    record = tmp_path / "gateway.runtime.json"
    payload = _payload(module)
    _write_record(record, payload)
    current = dict(payload)
    current["config_sha256"] = "d" * 64
    current["created_at"] = "ignored"
    monkeypatch.setattr(module, "collect_runtime_identity", lambda *_args, **_kwargs: current)

    with pytest.raises(module.GatewayRuntimeError, match="config_sha256"):
        module.verify_runtime_identity(tmp_path, runtime_path=record)


def test_created_at_is_not_recomputed_as_identity_drift(tmp_path: Path, monkeypatch) -> None:
    module = _module()
    record = tmp_path / "gateway.runtime.json"
    payload = _payload(module)
    _write_record(record, payload)
    current = dict(payload)
    current["created_at"] = "2026-07-15T00:01:00Z"
    monkeypatch.setattr(module, "collect_runtime_identity", lambda *_args, **_kwargs: current)

    assert module.verify_runtime_identity(tmp_path, runtime_path=record) == payload


def test_pidfd_signal_uses_reviewed_linux_syscall_when_python_lacks_wrapper(monkeypatch) -> None:
    module = _module()
    calls: list[tuple[object, ...]] = []

    class FakeSyscall:
        restype = None

        def __call__(self, *args):
            calls.append(args)
            return 0

    class FakeLibc:
        syscall = FakeSyscall()

    monkeypatch.delattr(module.signal, "pidfd_send_signal", raising=False)
    monkeypatch.setattr(module.platform, "system", lambda: "Linux")
    monkeypatch.setattr(module.platform, "machine", lambda: "aarch64")
    monkeypatch.setattr(module.ctypes, "CDLL", lambda *_args, **_kwargs: FakeLibc())

    module._pidfd_send_signal(42, module.signal.SIGTERM)

    assert len(calls) == 1
    assert calls[0][0].value == module.PIDFD_SEND_SIGNAL_SYSCALL
    assert calls[0][1].value == 42
    assert calls[0][2].value == module.signal.SIGTERM


def test_pidfd_open_uses_reviewed_linux_syscall_when_python_lacks_wrapper(monkeypatch) -> None:
    module = _module()
    calls: list[tuple[object, ...]] = []

    class FakeSyscall:
        restype = None

        def __call__(self, *args):
            calls.append(args)
            return 77

    class FakeLibc:
        syscall = FakeSyscall()

    monkeypatch.delattr(module.os, "pidfd_open", raising=False)
    monkeypatch.setattr(module.platform, "system", lambda: "Linux")
    monkeypatch.setattr(module.platform, "machine", lambda: "aarch64")
    monkeypatch.setattr(module.ctypes, "CDLL", lambda *_args, **_kwargs: FakeLibc())

    assert module._pidfd_open(1234) == 77
    assert len(calls) == 1
    assert calls[0][0].value == module.PIDFD_OPEN_SYSCALL
    assert calls[0][1].value == 1234


def test_gateway_scripts_require_runtime_identity_and_pidfd_signal() -> None:
    start = (ROOT / "scripts/openshell/start_gateway.sh").read_text(encoding="utf-8")
    stop = (ROOT / "scripts/openshell/stop_gateway.sh").read_text(encoding="utf-8")
    status = (ROOT / "scripts/openshell/status_gateway.sh").read_text(encoding="utf-8")
    configure = (ROOT / "scripts/openshell/configure_bind_mount_contract.sh").read_text(encoding="utf-8")

    assert "gateway_runtime_identity.py" in start
    assert "gateway_start_recovery.py" in stop
    assert "gateway_runtime_identity.py" in status
    assert "gateway_runtime_identity.py" in configure
    assert "recover --reap" in stop
    assert " terminate " not in stop
    assert 'kill "$pid"' not in stop
    assert "tr -cd '0-9'" not in start + stop + status + configure
