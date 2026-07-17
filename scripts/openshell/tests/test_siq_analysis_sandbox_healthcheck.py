from __future__ import annotations

import importlib.util
import io
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
HEALTHCHECK_PATH = ROOT / "infra/openshell/sandbox/healthcheck.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("siq_sandbox_healthcheck", HEALTHCHECK_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_cmdline(proc_root: Path, pid: int, *args: bytes) -> None:
    process = proc_root / str(pid)
    process.mkdir(parents=True)
    (process / "cmdline").write_bytes(b"\0".join(args) + b"\0")


def _base_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_SERVER_KEY", "a" * 64)
    monkeypatch.setenv("API_SERVER_HOST", "127.0.0.1")
    monkeypatch.setenv("API_SERVER_PORT", "28651")


def test_openshell_healthchecks_process_contract_outside_nested_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_module()
    proc_root = tmp_path / "proc"
    _write_cmdline(proc_root, 1, b"/opt/openshell/bin/openshell-sandbox")
    _write_cmdline(
        proc_root,
        46,
        b"/opt/siq/hermes/venv/bin/python3",
        b"/opt/siq/hermes/venv/bin/hermes",
        b"gateway",
        b"run",
    )
    _base_environment(monkeypatch)
    monkeypatch.setenv("OPENSHELL_SANDBOX", "siq-analysis-canary-123")
    monkeypatch.setenv("OPENSHELL_SANDBOX_ID", "220f33f2-5139-4e77-b1dc-76cad3616900")
    monkeypatch.setattr(module, "PROC_ROOT", proc_root)

    def forbidden_http(*_args, **_kwargs):
        raise AssertionError("outer Docker namespace must not probe sandbox loopback")

    monkeypatch.setattr(module.urllib.request, "urlopen", forbidden_http)

    module.main()


@pytest.mark.parametrize(
    ("supervisor", "hermes_count", "message"),
    (
        (b"/bin/sleep", 1, "OpenShell supervisor identity is invalid"),
        (b"/opt/openshell/bin/openshell-sandbox", 0, "OpenShell Hermes process identity is invalid"),
        (b"/opt/openshell/bin/openshell-sandbox", 2, "OpenShell Hermes process identity is invalid"),
    ),
)
def test_openshell_healthcheck_rejects_process_identity_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    supervisor: bytes,
    hermes_count: int,
    message: str,
) -> None:
    module = _load_module()
    proc_root = tmp_path / "proc"
    _write_cmdline(proc_root, 1, supervisor)
    for offset in range(hermes_count):
        _write_cmdline(
            proc_root,
            46 + offset,
            b"/opt/siq/hermes/venv/bin/python3",
            b"/opt/siq/hermes/venv/bin/hermes",
            b"gateway",
            b"run",
        )
    _base_environment(monkeypatch)
    monkeypatch.setenv("OPENSHELL_SANDBOX", "siq-analysis-canary-123")
    monkeypatch.setenv("OPENSHELL_SANDBOX_ID", "220f33f2-5139-4e77-b1dc-76cad3616900")
    monkeypatch.setattr(module, "PROC_ROOT", proc_root)

    with pytest.raises(SystemExit, match=f"^{message}$"):
        module.main()


def test_direct_image_healthcheck_preserves_authenticated_loopback_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_module()
    _base_environment(monkeypatch)
    monkeypatch.delenv("OPENSHELL_SANDBOX", raising=False)
    monkeypatch.delenv("OPENSHELL_SANDBOX_ID", raising=False)
    requests = []

    class Response(io.BytesIO):
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    def fake_urlopen(request, *, timeout):
        requests.append((request, timeout))
        return Response(b'{"status":"ok"}')

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)

    module.main()

    assert len(requests) == 1
    request, timeout = requests[0]
    assert request.full_url == "http://127.0.0.1:28651/health"
    assert request.get_header("Authorization") == "Bearer " + "a" * 64
    assert timeout == 2


def test_partial_openshell_identity_does_not_fall_back_to_direct_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_module()
    _base_environment(monkeypatch)
    monkeypatch.setenv("OPENSHELL_SANDBOX", "siq-analysis-canary-123")
    monkeypatch.delenv("OPENSHELL_SANDBOX_ID", raising=False)

    with pytest.raises(SystemExit, match="^OpenShell sandbox identity is invalid$"):
        module.main()
