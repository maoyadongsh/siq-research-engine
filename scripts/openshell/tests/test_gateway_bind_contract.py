from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "gateway_bind_contract.py"


def _module():
    spec = importlib.util.spec_from_file_location("siq_gateway_bind_contract_under_test", SOURCE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_runtime(root: Path, module) -> tuple[Path, Path]:
    binary = root / "var/openshell/toolchains/v0.0.83/bin/openshell-gateway"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"strict-gateway-binary")
    binary.chmod(0o700)
    binary_sha = hashlib.sha256(binary.read_bytes()).hexdigest()

    record = root / "var/openshell/build/v0.0.83/gateway-patch.runtime"
    record.parent.mkdir(parents=True)
    record.write_text(
        "\n".join(
            (
                f"schema={module.RUNTIME_SCHEMA}",
                "state=committed",
                "active=patched",
                f"version={module.VERSION}",
                f"upstream_commit={module.UPSTREAM_COMMIT}",
                f"patch_sha256={module.PATCH_SHA256}",
                f"patched_binary_sha256={binary_sha}",
                f"active_binary_sha256={binary_sha}",
                "installed_path=var/openshell/toolchains/v0.0.83/bin/openshell-gateway",
                "",
            )
        ),
        encoding="utf-8",
    )
    record.chmod(0o600)
    return binary, record


def test_activation_record_attests_exact_runtime_and_project(tmp_path: Path) -> None:
    module = _module()
    _write_runtime(tmp_path, module)

    payload = module.build_activation_record(tmp_path)

    assert payload["contract"] == "siq_analysis_v2"
    assert payload["project_root"] == str(tmp_path.resolve())
    assert payload["gateway_name"] == "siq-openshell-dev"
    assert payload["state"] == "enabled"
    assert module.validate_activation_record(payload, project_root=tmp_path) == payload


def test_runtime_verification_rejects_binary_drift(tmp_path: Path) -> None:
    module = _module()
    binary, _ = _write_runtime(tmp_path, module)
    binary.write_bytes(b"tampered")
    binary.chmod(0o700)

    with pytest.raises(module.BindContractError, match="active binary"):
        module.validate_gateway_patch_runtime(tmp_path)


def test_activation_rejects_stale_runtime_record_hash(tmp_path: Path) -> None:
    module = _module()
    _, runtime = _write_runtime(tmp_path, module)
    payload = module.build_activation_record(tmp_path)
    runtime.write_text(runtime.read_text(encoding="utf-8") + "builder_note=changed\n", encoding="utf-8")
    runtime.chmod(0o600)

    with pytest.raises(module.BindContractError, match="runtime_record_sha256"):
        module.validate_activation_record(payload, project_root=tmp_path)


def test_activation_file_must_be_private_regular_and_exact_schema(tmp_path: Path) -> None:
    module = _module()
    _write_runtime(tmp_path, module)
    payload = module.build_activation_record(tmp_path)
    activation = tmp_path / "var/openshell/gateway/siq-openshell-dev/activation.json"
    activation.parent.mkdir(parents=True)
    activation.write_text(json.dumps(payload), encoding="utf-8")
    activation.chmod(0o644)

    with pytest.raises(module.BindContractError, match="mode"):
        module.load_activation_record(activation, project_root=tmp_path, required=True)

    activation.chmod(0o600)
    payload["unexpected"] = "value"
    activation.write_text(json.dumps(payload), encoding="utf-8")
    activation.chmod(0o600)
    with pytest.raises(module.BindContractError, match="schema"):
        module.load_activation_record(activation, project_root=tmp_path, required=True)


def test_activation_file_rejects_symlink(tmp_path: Path) -> None:
    module = _module()
    _write_runtime(tmp_path, module)
    target = tmp_path / "var/openshell/target.json"
    target.write_text("{}", encoding="utf-8")
    target.chmod(0o600)
    activation = tmp_path / "var/openshell/gateway/siq-openshell-dev/activation.json"
    activation.parent.mkdir(parents=True)
    os.symlink(target, activation)

    with pytest.raises(module.BindContractError, match="non-symlink"):
        module.load_activation_record(activation, project_root=tmp_path, required=True)
