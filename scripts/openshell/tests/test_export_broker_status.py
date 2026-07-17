from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from scripts.openshell import broker_lifecycle as lifecycle, export_broker_status as module


def _state_tree(project: Path) -> None:
    for path in (project / "var", project / "var/openshell", project / "var/openshell/brokers"):
        path.mkdir(mode=0o700, exist_ok=True)
        path.chmod(0o700)


def _status_payload(*, pid: int = 1234) -> dict[str, object]:
    return {
        "schema_version": lifecycle.SCHEMA_VERSION,
        "ok": True,
        "action": "status",
        "bridge": {"network": "siq-openshell-dev", "alias": "host.openshell.internal"},
        "brokers": {
            "egress": {
                "pid": pid,
                "port": 18792,
                "state": "running",
                "request_identity_required": True,
            },
            "data": {
                "pid": pid + 1,
                "port": 18793,
                "state": "running",
                "request_identity_required": True,
            },
        },
    }


def test_normalize_status_keeps_fixed_contract_and_drops_pids() -> None:
    normalized = module.normalize_status(_status_payload(), status_ok=True)

    assert normalized == {
        "schema_version": lifecycle.SCHEMA_VERSION,
        "ok": True,
        "action": "status",
        "bridge": {"network": "siq-openshell-dev", "alias": "host.openshell.internal"},
        "brokers": {
            "egress": {"port": 18792, "state": "running", "request_identity_required": True},
            "data": {"port": 18793, "state": "running", "request_identity_required": True},
        },
    }
    assert "pid" not in json.dumps(normalized)


@pytest.mark.parametrize(
    "mutation,error_code",
    [
        ({"schema_version": "wrong"}, "broker_status_contract_invalid"),
        ({"bridge": {"network": "other", "alias": "host.openshell.internal"}}, "broker_status_contract_invalid"),
        ({"brokers": {"egress": {"port": 18792, "state": "running"}}}, "broker_status_broker_set_invalid"),
        (
            {
                "brokers": {
                    "egress": {"port": 18792, "state": "stopped"},
                    "data": {"port": 18793, "state": "running"},
                }
            },
            "broker_status_not_running",
        ),
        (
            {
                "brokers": {
                    "egress": {"port": 18792, "state": "running"},
                    "data": {"port": 19530, "state": "running"},
                }
            },
            "broker_status_not_running",
        ),
    ],
)
def test_normalize_status_rejects_drift(mutation: dict[str, object], error_code: str) -> None:
    payload = _status_payload()
    payload.update(mutation)
    with pytest.raises(module.BrokerStatusExportError, match=error_code):
        module.normalize_status(payload, status_ok=True)


def test_collect_status_is_read_only_and_uses_status_only(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    _state_tree(project)
    calls: list[Path] = []

    def reader(root: Path) -> tuple[object, bool]:
        calls.append(root)
        return _status_payload(), True

    status = module.collect_status(project_root=project, status_reader=reader)

    assert calls == [project]
    assert status["brokers"] == {
        "egress": {"port": 18792, "state": "running", "request_identity_required": True},
        "data": {"port": 18793, "state": "running", "request_identity_required": True},
    }
    assert not list((project / "var/openshell/brokers").iterdir())


def test_collect_status_does_not_create_missing_runtime_state(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    (project / "var/openshell").mkdir(parents=True, mode=0o700)

    with pytest.raises(module.BrokerStatusExportError, match="broker_status_state_root_invalid"):
        module.collect_status(project_root=project, status_reader=lambda _root: (_status_payload(), True))

    assert not (project / "var/openshell/brokers").exists()


def test_write_status_is_private_atomic_and_fixed_path(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    status = module.normalize_status(_status_payload(), status_ok=True)

    output = module.write_status(project_root=project, output=module.OUTPUT_RELATIVE, status=status)

    assert output == project / module.OUTPUT_RELATIVE
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert json.loads(output.read_text(encoding="ascii")) == status
    assert not list(output.parent.glob(".broker-status.*.tmp"))

    updated = module.normalize_status(_status_payload(pid=9999), status_ok=True)
    module.write_status(project_root=project, output=module.OUTPUT_RELATIVE, status=updated)
    assert json.loads(output.read_text(encoding="ascii")) == updated


def test_write_status_rejects_arbitrary_or_symlinked_output(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    status = module.normalize_status(_status_payload(), status_ok=True)

    with pytest.raises(module.BrokerStatusExportError, match="broker_status_output_path_invalid"):
        module.write_status(project_root=project, output=Path("var/openshell/proofs/other.json"), status=status)

    proof_root = project / module.OUTPUT_RELATIVE.parent
    proof_root.mkdir(parents=True, mode=0o700, exist_ok=True)
    for directory in (project / "var", project / "var/openshell", proof_root):
        directory.chmod(0o700)
    outside = tmp_path / "outside.json"
    outside.write_text("unchanged\n", encoding="ascii")
    (project / module.OUTPUT_RELATIVE).symlink_to(outside)
    with pytest.raises(module.BrokerStatusExportError, match="broker_status_output_file_unsafe"):
        module.write_status(project_root=project, output=module.OUTPUT_RELATIVE, status=status)
    assert outside.read_text(encoding="ascii") == "unchanged\n"
