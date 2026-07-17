from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "gateway_patch_migration.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("siq_gateway_patch_migration_under_test", SOURCE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def module():
    return _load_module()


def _validate(module, record: bytes, **overrides: str) -> None:
    values = {
        "current_binary_sha256": module.LEGACY_PATCHED_BINARY_SHA256,
        "upstream_backup_sha256": module.UPSTREAM_BINARY_SHA256,
        "target_patch_sha256": module.TARGET_PATCH_SHA256,
    }
    values.update(overrides)
    module.validate_allowlisted_migration(runtime_record=record, **values)


def test_exact_reviewed_legacy_provenance_is_allowlisted(module) -> None:
    _validate(module, module.expected_runtime_record())


def test_every_legacy_runtime_field_is_fail_closed(module) -> None:
    for index, (key, value) in enumerate(module.LEGACY_RUNTIME_FIELDS):
        fields = list(module.LEGACY_RUNTIME_FIELDS)
        fields[index] = (key, f"{value}-changed")
        record = "".join(f"{field_key}={field_value}\n" for field_key, field_value in fields).encode("ascii")
        with pytest.raises(module.MigrationProvenanceError):
            _validate(module, record)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "unknown", "reordered"])
def test_runtime_record_shape_changes_are_rejected(module, mutation: str) -> None:
    fields = list(module.LEGACY_RUNTIME_FIELDS)
    if mutation == "missing":
        fields.pop()
    elif mutation == "duplicate":
        fields.append(fields[-1])
    elif mutation == "unknown":
        fields.append(("unknown", "value"))
    else:
        fields[0], fields[1] = fields[1], fields[0]
    record = "".join(f"{key}={value}\n" for key, value in fields).encode("ascii")
    with pytest.raises(module.MigrationProvenanceError):
        _validate(module, record)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("current_binary_sha256", "0" * 64),
        ("upstream_backup_sha256", "0" * 64),
        ("target_patch_sha256", "0" * 64),
    ],
)
def test_artifact_and_target_hash_drift_is_rejected(module, field: str, value: str) -> None:
    with pytest.raises(module.MigrationProvenanceError):
        _validate(module, module.expected_runtime_record(), **{field: value})


def test_project_verifier_requires_private_non_symlink_artifacts(module, tmp_path: Path, monkeypatch) -> None:
    build_root = tmp_path / f"var/openshell/build/v{module.VERSION}"
    bin_root = tmp_path / f"var/openshell/toolchains/v{module.VERSION}/bin"
    build_root.mkdir(parents=True)
    bin_root.mkdir(parents=True)
    record = build_root / "gateway-patch.runtime"
    binary = bin_root / "openshell-gateway"
    backup = bin_root / f"openshell-gateway.upstream-v{module.VERSION}"
    record.write_bytes(module.expected_runtime_record())
    binary.write_bytes(b"legacy-patched-binary")
    backup.write_bytes(b"verified-upstream-binary")
    record.chmod(0o600)
    binary.chmod(0o700)
    backup.chmod(0o700)

    hashes = {
        binary: module.LEGACY_PATCHED_BINARY_SHA256,
        backup: module.UPSTREAM_BINARY_SHA256,
    }
    monkeypatch.setattr(module, "_sha256_file", lambda path: hashes[path])
    module.verify_project_migration(
        tmp_path,
        current_binary_sha256=module.LEGACY_PATCHED_BINARY_SHA256,
        target_patch_sha256=module.TARGET_PATCH_SHA256,
    )

    record.chmod(0o644)
    with pytest.raises(module.MigrationProvenanceError):
        module.verify_project_migration(
            tmp_path,
            current_binary_sha256=module.LEGACY_PATCHED_BINARY_SHA256,
            target_patch_sha256=module.TARGET_PATCH_SHA256,
        )

    record.unlink()
    target = build_root / "runtime-target"
    target.write_bytes(module.expected_runtime_record())
    target.chmod(0o600)
    os.symlink(target.name, record)
    with pytest.raises(module.MigrationProvenanceError):
        module.verify_project_migration(
            tmp_path,
            current_binary_sha256=module.LEGACY_PATCHED_BINARY_SHA256,
            target_patch_sha256=module.TARGET_PATCH_SHA256,
        )


def test_active_legacy_contract_requires_exact_activation_and_config(module, tmp_path: Path, monkeypatch) -> None:
    build_root = tmp_path / f"var/openshell/build/v{module.VERSION}"
    bin_root = tmp_path / f"var/openshell/toolchains/v{module.VERSION}/bin"
    gateway_root = tmp_path / "var/openshell/gateway/siq-openshell-dev"
    build_root.mkdir(parents=True)
    bin_root.mkdir(parents=True)
    gateway_root.mkdir(parents=True)
    record = build_root / "gateway-patch.runtime"
    binary = bin_root / "openshell-gateway"
    backup = bin_root / f"openshell-gateway.upstream-v{module.VERSION}"
    activation = gateway_root / "bind-contract.activation.json"
    config = gateway_root / "gateway.toml"
    record.write_bytes(module.expected_runtime_record())
    binary.write_bytes(b"legacy-patched-binary")
    backup.write_bytes(b"verified-upstream-binary")
    payload = {
        "active_binary_sha256": module.LEGACY_PATCHED_BINARY_SHA256,
        "contract": "siq_analysis_v1",
        "gateway_name": "siq-openshell-dev",
        "gateway_version": module.VERSION,
        "patch_sha256": module.LEGACY_PATCH_SHA256,
        "project_root": str(tmp_path),
        "runtime_record_sha256": module.LEGACY_RUNTIME_RECORD_SHA256,
        "schema": "siq.openshell.bind_contract_activation.v1",
        "state": "enabled",
    }
    activation.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    config.write_text("reviewed legacy gateway config\n", encoding="utf-8")
    artifacts = (
        (record, 0o600),
        (binary, 0o700),
        (backup, 0o700),
        (activation, 0o600),
        (config, 0o600),
    )
    for path, mode in artifacts:
        path.chmod(mode)

    activation_sha = hashlib.sha256(activation.read_bytes()).hexdigest()
    config_sha = hashlib.sha256(config.read_bytes()).hexdigest()
    monkeypatch.setattr(module, "LEGACY_ACTIVATION_RECORD_SHA256", activation_sha)
    monkeypatch.setattr(module, "LEGACY_GATEWAY_CONFIG_SHA256", config_sha)
    hashes = {
        binary: module.LEGACY_PATCHED_BINARY_SHA256,
        backup: module.UPSTREAM_BINARY_SHA256,
        config: config_sha,
    }
    monkeypatch.setattr(module, "_sha256_file", lambda path: hashes[path])
    module.verify_active_legacy_contract(
        tmp_path,
        current_binary_sha256=module.LEGACY_PATCHED_BINARY_SHA256,
        target_patch_sha256=module.TARGET_PATCH_SHA256,
    )

    payload["contract"] = "siq_analysis_v2"
    activation.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    monkeypatch.setattr(
        module,
        "LEGACY_ACTIVATION_RECORD_SHA256",
        hashlib.sha256(activation.read_bytes()).hexdigest(),
    )
    with pytest.raises(module.MigrationProvenanceError):
        module.verify_active_legacy_contract(
            tmp_path,
            current_binary_sha256=module.LEGACY_PATCHED_BINARY_SHA256,
            target_patch_sha256=module.TARGET_PATCH_SHA256,
        )
