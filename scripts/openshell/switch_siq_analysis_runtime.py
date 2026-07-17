#!/usr/bin/env python3
"""Atomically select Host or OpenShell for new siq_analysis runs."""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "siq.openshell.runtime_selection.v1"
PROFILE = "siq_analysis"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATE_RELATIVE = Path("var/openshell/runtime-selection")
STATE_NAME = "siq-analysis.json"


class RuntimeSwitchError(RuntimeError):
    pass


def _private_directory(path: Path, *, create: bool) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        if not create:
            raise RuntimeSwitchError("runtime_selection_directory_missing") from None
        path.mkdir(mode=0o700)
        info = path.lstat()
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise RuntimeSwitchError("runtime_selection_directory_unsafe")


def _state_path(project_root: Path, *, create: bool) -> Path:
    project_root = project_root.resolve(strict=True)
    current = project_root / "var"
    try:
        info = current.lstat()
    except FileNotFoundError:
        if not create:
            raise RuntimeSwitchError("runtime_selection_directory_missing") from None
        current.mkdir(mode=0o700)
        info = current.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
        raise RuntimeSwitchError("runtime_selection_directory_unsafe")
    for part in ("openshell", "runtime-selection"):
        current /= part
        _private_directory(current, create=create)
    return current / STATE_NAME


def _payload(target: str, session_mode: str) -> dict[str, str]:
    if target not in {"host", "openshell"}:
        raise RuntimeSwitchError("runtime_target_invalid")
    if session_mode not in {"allowlist", "all"}:
        raise RuntimeSwitchError("runtime_session_mode_invalid")
    return {
        "schema_version": SCHEMA_VERSION,
        "profile": PROFILE,
        "target": target,
        "session_mode": session_mode,
        "unmatched_scope": "host",
    }


def _atomic_write(path: Path, payload: dict[str, str]) -> None:
    if path.is_symlink():
        raise RuntimeSwitchError("runtime_selection_file_unsafe")
    if path.exists():
        info = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) & 0o077
        ):
            raise RuntimeSwitchError("runtime_selection_file_unsafe")
    content = (json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode("ascii")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        descriptor = -1
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _read(path: Path) -> dict[str, Any]:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return {
            "schema_version": SCHEMA_VERSION,
            "profile": PROFILE,
            "target": os.getenv("SIQ_HERMES_RUNTIME", "host").strip().lower(),
            "session_mode": os.getenv(
                "SIQ_HERMES_ANALYSIS_OPENSHELL_CANARY_SESSION_MODE",
                "allowlist",
            ).strip().lower(),
            "unmatched_scope": "host",
            "source": "environment",
        }
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise RuntimeSwitchError("runtime_selection_file_unsafe")
    try:
        payload = json.loads(path.read_text(encoding="ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeSwitchError("runtime_selection_file_invalid") from exc
    if not isinstance(payload, dict) or payload != _payload(
        str(payload.get("target")),
        str(payload.get("session_mode")),
    ):
        raise RuntimeSwitchError("runtime_selection_file_invalid")
    return {**payload, "source": "runtime_file"}


def switch_runtime(project_root: Path, *, target: str, session_mode: str) -> dict[str, Any]:
    path = _state_path(project_root, create=True)
    payload = _payload(target, session_mode)
    _atomic_write(path, payload)
    return {**payload, "source": "runtime_file", "state": path.relative_to(project_root).as_posix()}


def runtime_status(project_root: Path) -> dict[str, Any]:
    try:
        path = _state_path(project_root, create=False)
    except RuntimeSwitchError as exc:
        if str(exc) != "runtime_selection_directory_missing":
            raise
        path = project_root / STATE_RELATIVE / STATE_NAME
    return {**_read(path), "state": path.relative_to(project_root).as_posix()}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", choices=("host", "openshell", "status"))
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--session-mode", choices=("allowlist", "all"))
    return parser


def main(argv: list[str] | None = None) -> int:
    os.umask(0o077)
    args = _parser().parse_args(argv)
    try:
        root = args.project_root.resolve(strict=True)
        if args.target == "status":
            result = runtime_status(root)
        else:
            session_mode = args.session_mode or ("all" if args.target == "openshell" else "allowlist")
            result = switch_runtime(root, target=args.target, session_mode=session_mode)
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
        return 0
    except (OSError, RuntimeSwitchError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
