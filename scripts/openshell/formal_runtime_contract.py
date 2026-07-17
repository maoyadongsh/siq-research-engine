#!/usr/bin/env python3
"""Strict, run-id-independent projections for formal OpenShell runtime evidence."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any, Mapping, Sequence

from scripts.openshell import build_siq_analysis_mount_plan as mount_builder

SCHEMA_VERSION = "siq.openshell.formal-runtime-contract.v1"
CONTROL_MOUNT_COUNT = 5
MAX_PLAN_BYTES = 1024 * 1024


class FormalRuntimeContractError(RuntimeError):
    """Stable failure for evidence-time runtime contract projection."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def canonical_sha256(value: Any) -> str:
    return sha256_bytes(
        json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("ascii")
    )


def stable_regular_file(path: Path, *, max_bytes: int = MAX_PLAN_BYTES) -> bytes:
    descriptor = -1
    try:
        expected = path.lstat()
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(expected.st_mode)
            or stat.S_ISLNK(expected.st_mode)
            or expected.st_nlink != 1
            or opened.st_nlink != 1
            or (expected.st_dev, expected.st_ino) != (opened.st_dev, opened.st_ino)
            or opened.st_size <= 0
            or opened.st_size > max_bytes
        ):
            raise FormalRuntimeContractError("formal_runtime_source_invalid")
        content = bytearray()
        while chunk := os.read(descriptor, min(64 * 1024, max_bytes + 1 - len(content))):
            content.extend(chunk)
            if len(content) > max_bytes:
                raise FormalRuntimeContractError("formal_runtime_source_invalid")
        finished = os.fstat(descriptor)
        final = path.lstat()
        identity = (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
        if identity != (finished.st_dev, finished.st_ino, finished.st_size, finished.st_mtime_ns) or identity != (
            final.st_dev,
            final.st_ino,
            final.st_size,
            final.st_mtime_ns,
        ):
            raise FormalRuntimeContractError("formal_runtime_source_changed")
        return bytes(content)
    except FormalRuntimeContractError:
        raise
    except OSError as exc:
        raise FormalRuntimeContractError("formal_runtime_source_invalid") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _relative_to_root(root: Path, path: Path, *, code: str) -> str:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise FormalRuntimeContractError(code) from exc
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise FormalRuntimeContractError(code)
    return relative.as_posix()


def normalized_mount_contract(
    *,
    project_root: Path,
    mount_plan: Path,
    analysis_root: Path,
    runtime_snapshot: Path,
) -> dict[str, Any]:
    """Validate one exact 7-mount plan and remove only its run-id source prefix."""

    try:
        root = project_root.resolve(strict=True)
        plan = mount_plan.resolve(strict=True)
        analysis = analysis_root.resolve(strict=True)
        snapshot = runtime_snapshot.resolve(strict=True)
    except OSError as exc:
        raise FormalRuntimeContractError("formal_mount_contract_invalid") from exc
    if root != project_root.absolute():
        raise FormalRuntimeContractError("formal_mount_contract_invalid")
    _relative_to_root(root, analysis, code="formal_mount_contract_invalid")
    _relative_to_root(root, snapshot, code="formal_mount_contract_invalid")
    expected_plan_root = root / mount_builder.PLAN_ROOT_RELATIVE
    if plan.parent != expected_plan_root or not plan.name.endswith(".driver-config.json"):
        raise FormalRuntimeContractError("formal_mount_contract_invalid")
    content = stable_regular_file(plan)
    raw_sha256 = sha256_bytes(content)
    if plan.name != f"{raw_sha256}.driver-config.json":
        raise FormalRuntimeContractError("formal_mount_contract_digest_mismatch")
    try:
        payload = json.loads(content)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise FormalRuntimeContractError("formal_mount_contract_invalid") from exc
    expected = {"docker": {"mounts": mount_builder._expected_mounts(root, snapshot, analysis)}}
    if payload != expected:
        raise FormalRuntimeContractError("formal_mount_contract_invalid")

    analysis_relative = _relative_to_root(root, analysis, code="formal_mount_contract_invalid")
    business_mounts = [
        {
            "source_role": "immutable_wiki",
            "target": mount_builder.WIKI_RELATIVE.as_posix(),
            "read_only": True,
        },
        {
            "source_role": "task_analysis",
            "target": analysis_relative,
            "read_only": False,
        },
        {
            "source_role": f"runtime_snapshot/{mount_builder.RUNTIME_STATE_DIRECTORY}",
            "target": mount_builder.SANDBOX_RUNTIME_STATE_ROOT.as_posix(),
            "read_only": False,
        },
        *[
            {
                "source_role": f"runtime_snapshot/{name}",
                "target": (mount_builder.HERMES_HOME_RELATIVE / name).as_posix(),
                "read_only": False,
            }
            for name in mount_builder.RUNTIME_DIRECTORIES
        ],
    ]
    control_mounts = [
        {"source_role": "pinned_supervisor", "target": "/opt/openshell/bin/openshell-sandbox", "read_only": True},
        {"source_role": "gateway_ca", "target": "/etc/openshell/tls/client/ca.crt", "read_only": True},
        {"source_role": "gateway_client_cert", "target": "/etc/openshell/tls/client/tls.crt", "read_only": True},
        {"source_role": "gateway_client_key", "target": "/etc/openshell/tls/client/tls.key", "read_only": True},
        {"source_role": "sandbox_jwt", "target": "/etc/openshell/auth/sandbox.jwt", "read_only": True},
    ]
    if len(business_mounts) != mount_builder.BUSINESS_MOUNT_COUNT or len(control_mounts) != CONTROL_MOUNT_COUNT:
        raise FormalRuntimeContractError("formal_mount_contract_invalid")
    projection = {
        "schema_version": SCHEMA_VERSION,
        "profile": mount_builder.PROFILE,
        "business_mount_count": mount_builder.BUSINESS_MOUNT_COUNT,
        "control_mount_count": CONTROL_MOUNT_COUNT,
        "total_mount_count": mount_builder.BUSINESS_MOUNT_COUNT + CONTROL_MOUNT_COUNT,
        "business_mounts": business_mounts,
        "control_mounts": control_mounts,
    }
    return {
        "raw_mount_plan_sha256": raw_sha256,
        "mount_contract_sha256": canonical_sha256(projection),
        "projection": projection,
    }


def validate_runtime_mounts(
    *,
    context: Any,
    mounts: Sequence[Mapping[str, Any]],
    validator: Any,
) -> dict[str, int]:
    """Require the live container's exact 7+5 mount realization."""

    try:
        counts = validator(context, mounts)
    except Exception as exc:
        raise FormalRuntimeContractError("formal_live_mount_contract_invalid") from exc
    expected = {
        "business_mount_count": mount_builder.BUSINESS_MOUNT_COUNT,
        "control_mount_count": CONTROL_MOUNT_COUNT,
        "total_mount_count": mount_builder.BUSINESS_MOUNT_COUNT + CONTROL_MOUNT_COUNT,
    }
    if counts != expected:
        raise FormalRuntimeContractError("formal_live_mount_contract_invalid")
    return expected


__all__ = [
    "FormalRuntimeContractError",
    "canonical_sha256",
    "normalized_mount_contract",
    "sha256_bytes",
    "stable_regular_file",
    "validate_runtime_mounts",
]
