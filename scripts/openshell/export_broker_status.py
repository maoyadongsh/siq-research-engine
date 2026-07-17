#!/usr/bin/env python3
"""Export a fixed, secret-free broker status proof for the SIQ A/B gate.

The exporter only calls ``BrokerLifecycle.status``.  It never starts, stops,
repairs, or creates a broker.  Runtime PIDs and other process details are
deliberately discarded before the proof is written.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Mapping

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.openshell import broker_lifecycle

SCHEMA_VERSION = broker_lifecycle.SCHEMA_VERSION
NETWORK_NAME = "siq-openshell-dev"
HOST_ALIAS = "host.openshell.internal"
BROKER_PORTS = {spec.name: spec.port for spec in broker_lifecycle.BROKER_SPECS}
REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_RELATIVE = Path("var/openshell/proofs/broker-status.json")
MAX_OUTPUT_BYTES = 64 * 1024


class BrokerStatusExportError(RuntimeError):
    """Stable failure code without process state or environment values."""


StatusReader = Callable[[Path], tuple[Any, bool]]


def _safe_existing_directory(path: Path, *, error_code: str) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise BrokerStatusExportError(error_code) from exc
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise BrokerStatusExportError(error_code)


def _assert_existing_runtime_state(project_root: Path) -> None:
    """Keep status collection read-only when the state tree is absent/unsafe."""

    root = project_root / "var/openshell"
    _safe_existing_directory(root, error_code="broker_status_state_root_invalid")
    _safe_existing_directory(root / "brokers", error_code="broker_status_state_root_invalid")


def normalize_status(payload: Any, *, status_ok: bool) -> dict[str, Any]:
    """Validate lifecycle status and remove all process-specific fields."""

    if not status_ok or not isinstance(payload, dict):
        raise BrokerStatusExportError("broker_status_not_verified")
    if (
        payload.get("schema_version") != SCHEMA_VERSION
        or payload.get("ok") is not True
        or payload.get("action") != "status"
        or payload.get("bridge")
        != {"network": NETWORK_NAME, "alias": HOST_ALIAS}
    ):
        raise BrokerStatusExportError("broker_status_contract_invalid")

    brokers = payload.get("brokers")
    if not isinstance(brokers, dict) or set(brokers) != set(BROKER_PORTS):
        raise BrokerStatusExportError("broker_status_broker_set_invalid")
    normalized_brokers: dict[str, dict[str, Any]] = {}
    for name, expected_port in BROKER_PORTS.items():
        item = brokers.get(name)
        if (
            not isinstance(item, dict)
            or item.get("port") != expected_port
            or item.get("state") != "running"
            or item.get("request_identity_required") is not True
        ):
            raise BrokerStatusExportError("broker_status_not_running")
        # Do not copy pid, error_code, or any future process metadata.
        normalized_brokers[name] = {
            "port": expected_port,
            "state": "running",
            "request_identity_required": True,
        }

    return {
        "schema_version": SCHEMA_VERSION,
        "ok": True,
        "action": "status",
        "bridge": {"network": NETWORK_NAME, "alias": HOST_ALIAS},
        "brokers": normalized_brokers,
    }


def collect_status(*, project_root: Path, status_reader: StatusReader | None = None) -> dict[str, Any]:
    root = project_root.resolve(strict=True)
    _assert_existing_runtime_state(root)
    if status_reader is None:
        lifecycle = broker_lifecycle.BrokerLifecycle(
            project_root=root,
            require_request_identity=True,
        )
        payload, status_ok = lifecycle.status()
    else:
        payload, status_ok = status_reader(root)
    return normalize_status(payload, status_ok=status_ok)


def _safe_output(project_root: Path, output: Path) -> tuple[Path, Path]:
    root = project_root.resolve(strict=True)
    proof_root = root / OUTPUT_RELATIVE.parent
    candidate = output if output.is_absolute() else root / output
    if candidate != root / OUTPUT_RELATIVE:
        raise BrokerStatusExportError("broker_status_output_path_invalid")

    current = root
    for part in OUTPUT_RELATIVE.parent.parts:
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            current.mkdir(mode=0o700)
            continue
        if (
            stat.S_ISLNK(info.st_mode)
            or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid()
            or (current != root / "var" and stat.S_IMODE(info.st_mode) & 0o077)
        ):
            raise BrokerStatusExportError("broker_status_output_root_unsafe")

    try:
        existing = candidate.lstat()
    except FileNotFoundError:
        pass
    else:
        if (
            stat.S_ISLNK(existing.st_mode)
            or not stat.S_ISREG(existing.st_mode)
            or existing.st_uid != os.geteuid()
            or existing.st_nlink != 1
            or stat.S_IMODE(existing.st_mode) != 0o600
        ):
            raise BrokerStatusExportError("broker_status_output_file_unsafe")
    return proof_root, candidate


def write_status(*, project_root: Path, output: Path, status: Mapping[str, Any]) -> Path:
    proof_root, destination = _safe_output(project_root, output)
    content = (json.dumps(status, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode("ascii")
    if len(content) > MAX_OUTPUT_BYTES:
        raise BrokerStatusExportError("broker_status_output_too_large")
    descriptor, temporary_name = tempfile.mkstemp(prefix=".broker-status.", suffix=".tmp", dir=proof_root)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        directory_fd = os.open(proof_root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError as exc:
        raise BrokerStatusExportError("broker_status_output_write_failed") from exc
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--output", type=Path, default=OUTPUT_RELATIVE)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        root = args.project_root.resolve(strict=True)
        status = collect_status(project_root=root)
        destination = write_status(project_root=root, output=args.output, status=status)
        print(
            json.dumps(
                {
                    "ok": True,
                    "schema_version": SCHEMA_VERSION,
                    "output": destination.relative_to(root).as_posix(),
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        )
        return 0
    except (OSError, BrokerStatusExportError, ValueError) as exc:
        code = str(exc) if isinstance(exc, BrokerStatusExportError) else "broker_status_export_failed"
        print(json.dumps({"ok": False, "error_code": code}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
