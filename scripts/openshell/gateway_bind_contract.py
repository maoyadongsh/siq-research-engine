#!/usr/bin/env python3
"""Verify and attest the SIQ gateway bind-mount contract activation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping

REPO_ROOT = Path(__file__).resolve().parents[2]
VERSION = "0.0.83"
GATEWAY_NAME = "siq-openshell-dev"
CONTRACT = "siq_analysis_v2"
PATCH_SHA256 = "a877673ef005212049b860168c3401651e189beb96d39489fdea53fac61c2752"
UPSTREAM_COMMIT = "e3d26dd3ae0dee247bbc5db368545832757ac493"
ACTIVATION_SCHEMA = "siq.openshell.bind_contract_activation.v1"
RUNTIME_SCHEMA = "siq.openshell.gateway_patch.v1"
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")

ACTIVATION_KEYS = {
    "schema",
    "state",
    "gateway_name",
    "gateway_version",
    "contract",
    "project_root",
    "active_binary_sha256",
    "runtime_record_sha256",
    "patch_sha256",
}


class BindContractError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _require_private_regular_file(path: Path, *, label: str) -> os.stat_result:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise BindContractError(f"{label} is missing: {path}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise BindContractError(f"{label} must be a regular non-symlink file: {path}")
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise BindContractError(f"{label} must be owned by the current user and mode 0600/0700: {path}")
    return info


def _require_project_state_path(path: Path, *, project_root: Path, label: str) -> None:
    state_root = project_root / "var/openshell"
    try:
        path.absolute().relative_to(state_root)
    except ValueError as exc:
        raise BindContractError(f"{label} must remain below {state_root}") from exc

    current = project_root
    for component in path.absolute().relative_to(project_root).parts[:-1]:
        current /= component
        if current.is_symlink():
            raise BindContractError(f"{label} parent contains a symlink: {current}")


def _parse_runtime_record(path: Path) -> dict[str, str]:
    _require_private_regular_file(path, label="gateway patch runtime record")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise BindContractError("gateway patch runtime record is not UTF-8") from exc

    values: dict[str, str] = {}
    for line in lines:
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not key or key in values or not re.fullmatch(r"[a-z0-9_]+", key):
            raise BindContractError("gateway patch runtime record is malformed")
        values[key] = value
    return values


def validate_gateway_patch_runtime(project_root: Path) -> dict[str, str]:
    root = project_root.resolve(strict=True)
    binary = root / f"var/openshell/toolchains/v{VERSION}/bin/openshell-gateway"
    runtime_record = root / f"var/openshell/build/v{VERSION}/gateway-patch.runtime"
    _require_project_state_path(binary, project_root=root, label="gateway binary")
    _require_project_state_path(runtime_record, project_root=root, label="gateway runtime record")
    _require_private_regular_file(binary, label="patched gateway binary")
    values = _parse_runtime_record(runtime_record)
    binary_sha = _sha256(binary)

    expected = {
        "schema": RUNTIME_SCHEMA,
        "state": "committed",
        "active": "patched",
        "version": VERSION,
        "upstream_commit": UPSTREAM_COMMIT,
        "patch_sha256": PATCH_SHA256,
        "patched_binary_sha256": binary_sha,
        "active_binary_sha256": binary_sha,
        "installed_path": f"var/openshell/toolchains/v{VERSION}/bin/openshell-gateway",
    }
    mismatched = [key for key, expected_value in expected.items() if values.get(key) != expected_value]
    if mismatched:
        raise BindContractError(
            "gateway patch runtime evidence does not match the active binary: " + ", ".join(mismatched)
        )
    if not SHA256_PATTERN.fullmatch(binary_sha):
        raise BindContractError("active gateway binary SHA-256 is malformed")
    return {
        "binary_sha256": binary_sha,
        "runtime_record_sha256": _sha256(runtime_record),
    }


def build_activation_record(project_root: Path) -> dict[str, str]:
    root = project_root.resolve(strict=True)
    evidence = validate_gateway_patch_runtime(root)
    return {
        "schema": ACTIVATION_SCHEMA,
        "state": "enabled",
        "gateway_name": GATEWAY_NAME,
        "gateway_version": VERSION,
        "contract": CONTRACT,
        "project_root": str(root),
        "active_binary_sha256": evidence["binary_sha256"],
        "runtime_record_sha256": evidence["runtime_record_sha256"],
        "patch_sha256": PATCH_SHA256,
    }


def validate_activation_record(payload: Mapping[str, Any], *, project_root: Path) -> dict[str, str]:
    root = project_root.resolve(strict=True)
    if set(payload) != ACTIVATION_KEYS or not all(isinstance(value, str) for value in payload.values()):
        raise BindContractError("bind-contract activation record has an unexpected schema")
    expected = build_activation_record(root)
    mismatched = [key for key, expected_value in expected.items() if payload.get(key) != expected_value]
    if mismatched:
        raise BindContractError("bind-contract activation record is stale or mismatched: " + ", ".join(mismatched))
    return expected


def load_activation_record(path: Path, *, project_root: Path, required: bool = False) -> dict[str, str] | None:
    root = project_root.resolve(strict=True)
    _require_project_state_path(path, project_root=root, label="bind-contract activation record")
    try:
        path.lstat()
    except FileNotFoundError:
        if required:
            raise BindContractError(f"bind-contract activation record is missing: {path}") from None
        return None
    _require_private_regular_file(path, label="bind-contract activation record")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BindContractError("bind-contract activation record is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise BindContractError("bind-contract activation record must be a JSON object")
    return validate_activation_record(payload, project_root=root)


def _atomic_write_json(path: Path, payload: Mapping[str, str], *, project_root: Path) -> None:
    root = project_root.resolve(strict=True)
    _require_project_state_path(path, project_root=root, label="bind-contract activation output")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=True, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=REPO_ROOT)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("verify-runtime")
    create = subparsers.add_parser("create")
    create.add_argument("--output", type=Path, required=True)
    verify = subparsers.add_parser("verify-activation")
    verify.add_argument("--activation-record", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        root = args.project_root.resolve(strict=True)
        if args.command == "verify-runtime":
            evidence = validate_gateway_patch_runtime(root)
            print(f"gateway patch runtime verified: {evidence['binary_sha256']}")
        elif args.command == "create":
            output = args.output if args.output.is_absolute() else root / args.output
            _atomic_write_json(output, build_activation_record(root), project_root=root)
            print(f"bind-contract activation record created: {output}")
        else:
            record = args.activation_record or Path(
                f"var/openshell/gateway/{GATEWAY_NAME}/bind-contract.activation.json"
            )
            record = record if record.is_absolute() else root / record
            payload = load_activation_record(record, project_root=root, required=True)
            assert payload is not None
            print(f"bind-contract activation verified: {payload['contract']}")
        return 0
    except (OSError, ValueError, BindContractError) as exc:
        print(f"bind-contract error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
