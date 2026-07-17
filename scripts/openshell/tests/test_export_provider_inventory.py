from __future__ import annotations

import json
import stat
from pathlib import Path
from typing import Sequence

import pytest

from scripts.openshell import export_provider_inventory as module


class FakeRunner:
    def __init__(self, providers: object, *, version: str = "openshell 0.0.83\n") -> None:
        self.providers = providers
        self.version = version
        self.calls: list[tuple[str, ...]] = []

    def run(self, arguments: Sequence[str], *, project_root: Path) -> module.CommandResult:
        assert project_root.is_absolute()
        command = tuple(arguments)
        self.calls.append(command)
        if command == ("--version",):
            return module.CommandResult(0, self.version.encode("ascii"), b"")
        assert command == ("provider", "list", "--limit", "1000", "-o", "json")
        return module.CommandResult(0, json.dumps(self.providers).encode("utf-8"), b"")


def _provider(name: str = "siq-stepfun") -> dict[str, object]:
    return {
        "id": "provider-id-must-not-be-exported",
        "name": name,
        "type": "siq-stepfun",
        "resource_version": 9,
        "credential_keys": ["SIQ_STEPFUN_LLM_API_KEY"],
        "credential_values": {"SIQ_STEPFUN_LLM_API_KEY": "secret-must-not-be-exported"},
    }


def test_collect_inventory_uses_fixed_read_only_commands_and_drops_sensitive_fields(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    runner = FakeRunner([_provider("siq-stepfun"), _provider("siq-exa-search")])

    inventory = module.collect_inventory(project_root=project, runner=runner)

    assert runner.calls == [
        ("--version",),
        ("provider", "list", "--limit", "1000", "-o", "json"),
    ]
    assert inventory == {
        "schema_version": module.SCHEMA_VERSION,
        "openshell_version": "0.0.83",
        "gateway": "siq-openshell-dev",
        "providers": [
            {"name": "siq-exa-search", "state": "configured"},
            {"name": "siq-stepfun", "state": "configured"},
        ],
    }
    serialized = json.dumps(inventory)
    assert "credential" not in serialized
    assert "provider-id" not in serialized
    assert "secret-must-not-be-exported" not in serialized


def test_collect_inventory_rejects_version_drift_before_provider_list(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    runner = FakeRunner([], version="openshell 0.0.84\n")

    with pytest.raises(module.ProviderInventoryError, match="openshell_version_mismatch"):
        module.collect_inventory(project_root=project, runner=runner)

    assert runner.calls == [("--version",)]


@pytest.mark.parametrize(
    "providers",
    [
        [{"name": "invalid/name", "type": "siq-stepfun", "credential_keys": []}],
        [_provider("duplicate"), _provider("duplicate")],
        [{"name": "siq-stepfun", "type": "siq-stepfun", "credential_keys": ["bad-key"]}],
        {"providers": []},
    ],
)
def test_normalize_inventory_rejects_untrusted_shapes(providers: object) -> None:
    with pytest.raises(module.ProviderInventoryError):
        module.normalize_inventory(providers)


def test_write_inventory_is_private_atomic_and_replaces_only_the_fixed_proof(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    inventory = module.normalize_inventory([_provider()])

    output = module.write_inventory(
        project_root=project,
        output=module.OUTPUT_RELATIVE,
        inventory=inventory,
    )

    assert output == project / module.OUTPUT_RELATIVE
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert json.loads(output.read_text(encoding="ascii")) == inventory
    assert not list(output.parent.glob(".provider-inventory.*.tmp"))

    updated = module.normalize_inventory([])
    module.write_inventory(project_root=project, output=module.OUTPUT_RELATIVE, inventory=updated)
    assert json.loads(output.read_text(encoding="ascii")) == updated


def test_write_inventory_rejects_arbitrary_or_symlinked_output(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    inventory = module.normalize_inventory([])

    with pytest.raises(module.ProviderInventoryError, match="provider_inventory_output_path_invalid"):
        module.write_inventory(project_root=project, output=Path("var/openshell/proofs/other.json"), inventory=inventory)

    proof_root = project / module.OUTPUT_RELATIVE.parent
    proof_root.mkdir(parents=True, mode=0o700, exist_ok=True)
    outside = tmp_path / "outside.json"
    outside.write_text("unchanged\n", encoding="utf-8")
    (project / module.OUTPUT_RELATIVE).symlink_to(outside)
    with pytest.raises(module.ProviderInventoryError, match="provider_inventory_output_file_unsafe"):
        module.write_inventory(project_root=project, output=module.OUTPUT_RELATIVE, inventory=inventory)
    assert outside.read_text(encoding="utf-8") == "unchanged\n"
