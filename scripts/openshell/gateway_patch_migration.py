#!/usr/bin/env python3
"""Verify the single reviewed legacy gateway patch upgrade path."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
from pathlib import Path

VERSION = "0.0.83"
TARGET_PATCH_SHA256 = "a877673ef005212049b860168c3401651e189beb96d39489fdea53fac61c2752"
LEGACY_PATCH_SHA256 = "64026fc68cdc0177297cfe648cfaf84abcf7630b04fee5280a1491b882d48dc4"
LEGACY_PATCHED_BINARY_SHA256 = "9f26b7c3e7af2eefdf0c22eef82472422865aa63c114091ccdc25ea9968cff00"
UPSTREAM_BINARY_SHA256 = "198591e1e13b9cee94f0b7eb5875c6db484a3bcc9b371225cebc528c6116a31e"
LEGACY_RUNTIME_RECORD_SHA256 = "19fd64bc3f6f384dec7bb462a76a07cf88b87edbb5ca9dbc54ae9e18d800b637"
LEGACY_ACTIVATION_RECORD_SHA256 = "b54ed4b8a8264fb552a53b87010aaccbd4bb784291025409a60f2d8b330b7727"
LEGACY_GATEWAY_CONFIG_SHA256 = "39b229baac7d461ba90c942bdea5fc13b8d3afd0e6f2677a81c7d9313af2a186"

LEGACY_RUNTIME_FIELDS = (
    ("schema", "siq.openshell.gateway_patch.v1"),
    ("state", "committed"),
    ("active", "patched"),
    ("version", VERSION),
    ("upstream_commit", "e3d26dd3ae0dee247bbc5db368545832757ac493"),
    ("patch_sha256", LEGACY_PATCH_SHA256),
    ("normalized_source_diff_sha256", "c5a6d64cc2ca54d857da80a5551691c5d0ee6ba55dfb9fe7615f9f9303ae2ab4"),
    ("builder_image_id", "sha256:67231d2af825f51a58cb590221a18ef426869f9a8ba7461d728bf7f9ee6070d8"),
    ("builder_dockerfile_sha256", "7df3c6f16fa35f344234d10298a868e8d4ff2207d883038808a0f5d316620a18"),
    ("builder_packages_sha256", "d14a6597abe3bc068a8347ef78312102a0127b7ff8b1f9e453809c61b9c3fb9c"),
    ("builder_base", "ubuntu-24.04-arm64@sha256:4fbb8e6a8395de5a7550b33509421a2bafbc0aab6c06ba2cef9ebffbc7092d90"),
    ("builder_apt_mirror", "http://mirrors.aliyun.com/ubuntu-ports"),
    ("z3_commit", "ddb49568d3520e99799e364fb22f35fc67d887b1"),
    ("z3_archive_sha256", "34deac6d0d46002b1040c56a51c4385ebb4ea56baa95fa8dd66e315a25b0cfa6"),
    ("upstream_binary_sha256", UPSTREAM_BINARY_SHA256),
    ("patched_binary_sha256", LEGACY_PATCHED_BINARY_SHA256),
    ("active_binary_sha256", LEGACY_PATCHED_BINARY_SHA256),
    ("installed_path", f"var/openshell/toolchains/v{VERSION}/bin/openshell-gateway"),
)


class MigrationProvenanceError(RuntimeError):
    """The installed legacy artifact is not the reviewed upgrade source."""


def expected_runtime_record() -> bytes:
    return ("".join(f"{key}={value}\n" for key, value in LEGACY_RUNTIME_FIELDS)).encode("ascii")


def validate_allowlisted_migration(
    *,
    runtime_record: bytes,
    current_binary_sha256: str,
    upstream_backup_sha256: str,
    target_patch_sha256: str,
) -> None:
    if target_patch_sha256 != TARGET_PATCH_SHA256:
        raise MigrationProvenanceError("target patch is not allowlisted")
    if current_binary_sha256 != LEGACY_PATCHED_BINARY_SHA256:
        raise MigrationProvenanceError("current binary is not allowlisted")
    if upstream_backup_sha256 != UPSTREAM_BINARY_SHA256:
        raise MigrationProvenanceError("upstream backup is not allowlisted")
    if hashlib.sha256(runtime_record).hexdigest() != LEGACY_RUNTIME_RECORD_SHA256:
        raise MigrationProvenanceError("legacy runtime record digest is not allowlisted")
    if runtime_record != expected_runtime_record():
        raise MigrationProvenanceError("legacy runtime record fields are not allowlisted")


def _private_regular(path: Path, *, expected_mode: int, label: str) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise MigrationProvenanceError(f"{label} is missing") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise MigrationProvenanceError(f"{label} is not a regular file")
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != expected_mode:
        raise MigrationProvenanceError(f"{label} ownership or mode is unsafe")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_project_migration(
    project_root: Path,
    *,
    current_binary_sha256: str,
    target_patch_sha256: str,
) -> None:
    root = project_root.resolve(strict=True)
    build_root = root / f"var/openshell/build/v{VERSION}"
    bin_root = root / f"var/openshell/toolchains/v{VERSION}/bin"
    runtime_record = build_root / "gateway-patch.runtime"
    current_binary = bin_root / "openshell-gateway"
    upstream_backup = bin_root / f"openshell-gateway.upstream-v{VERSION}"

    _private_regular(runtime_record, expected_mode=0o600, label="legacy runtime record")
    _private_regular(current_binary, expected_mode=0o700, label="current gateway binary")
    _private_regular(upstream_backup, expected_mode=0o700, label="upstream gateway backup")

    measured_current_sha256 = _sha256_file(current_binary)
    if current_binary_sha256 != measured_current_sha256:
        raise MigrationProvenanceError("current binary changed during migration validation")
    validate_allowlisted_migration(
        runtime_record=runtime_record.read_bytes(),
        current_binary_sha256=measured_current_sha256,
        upstream_backup_sha256=_sha256_file(upstream_backup),
        target_patch_sha256=target_patch_sha256,
    )


def verify_active_legacy_contract(
    project_root: Path,
    *,
    current_binary_sha256: str,
    target_patch_sha256: str,
) -> None:
    """Verify the exact one-time v1 activation before atomically disabling it."""

    root = project_root.resolve(strict=True)
    verify_project_migration(
        root,
        current_binary_sha256=current_binary_sha256,
        target_patch_sha256=target_patch_sha256,
    )
    gateway_root = root / "var/openshell/gateway/siq-openshell-dev"
    activation = gateway_root / "bind-contract.activation.json"
    config = gateway_root / "gateway.toml"
    _private_regular(activation, expected_mode=0o600, label="legacy activation record")
    _private_regular(config, expected_mode=0o600, label="legacy gateway configuration")
    activation_bytes = activation.read_bytes()
    if hashlib.sha256(activation_bytes).hexdigest() != LEGACY_ACTIVATION_RECORD_SHA256:
        raise MigrationProvenanceError("legacy activation record digest is not allowlisted")
    if _sha256_file(config) != LEGACY_GATEWAY_CONFIG_SHA256:
        raise MigrationProvenanceError("legacy gateway configuration digest is not allowlisted")
    try:
        activation_payload = json.loads(activation_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MigrationProvenanceError("legacy activation record is malformed") from exc
    expected_activation = {
        "active_binary_sha256": LEGACY_PATCHED_BINARY_SHA256,
        "contract": "siq_analysis_v1",
        "gateway_name": "siq-openshell-dev",
        "gateway_version": VERSION,
        "patch_sha256": LEGACY_PATCH_SHA256,
        "project_root": str(root),
        "runtime_record_sha256": LEGACY_RUNTIME_RECORD_SHA256,
        "schema": "siq.openshell.bind_contract_activation.v1",
        "state": "enabled",
    }
    if activation_payload != expected_activation:
        raise MigrationProvenanceError("legacy activation fields are not allowlisted")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--current-binary-sha256", required=True)
    parser.add_argument("--target-patch-sha256", required=True)
    parser.add_argument("--require-active-legacy-contract", action="store_true")
    args = parser.parse_args()
    try:
        verifier = verify_active_legacy_contract if args.require_active_legacy_contract else verify_project_migration
        verifier(
            args.project_root,
            current_binary_sha256=args.current_binary_sha256,
            target_patch_sha256=args.target_patch_sha256,
        )
    except (MigrationProvenanceError, OSError, UnicodeError, ValueError):
        print("Legacy gateway patch provenance is not allowlisted; refusing migration.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
