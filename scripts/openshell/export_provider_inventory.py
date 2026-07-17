#!/usr/bin/env python3
"""Export a minimal, secret-free inventory from the project OpenShell gateway."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

SCHEMA_VERSION = "siq.openshell.provider_inventory.v1"
OPENSHELL_VERSION = "0.0.83"
GATEWAY = "siq-openshell-dev"
REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_RELATIVE = Path("var/openshell/proofs/provider-inventory.json")
MAX_COMMAND_OUTPUT_BYTES = 1024 * 1024
COMMAND_TIMEOUT_SECONDS = 15
SAFE_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
SAFE_TYPE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
ENV_NAME_RE = re.compile(r"[A-Z][A-Z0-9_]{0,127}\Z")


class ProviderInventoryError(RuntimeError):
    """Stable failure code that never contains CLI output or credential metadata."""


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: bytes
    stderr: bytes


class Runner(Protocol):
    def run(self, arguments: Sequence[str], *, project_root: Path) -> CommandResult: ...


class SubprocessRunner:
    def run(self, arguments: Sequence[str], *, project_root: Path) -> CommandResult:
        wrapper = project_root / "scripts/openshell/run_cli.sh"
        if wrapper.is_symlink() or not wrapper.is_file() or not os.access(wrapper, os.X_OK):
            raise ProviderInventoryError("openshell_wrapper_invalid")
        environment = {
            "HOME": str(project_root / "var/openshell/xdg/home"),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "NO_COLOR": "1",
            "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        }
        try:
            completed = subprocess.run(
                [str(wrapper), *arguments],
                cwd=project_root,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                close_fds=True,
                timeout=COMMAND_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ProviderInventoryError("openshell_provider_inventory_command_failed") from exc
        if len(completed.stdout) > MAX_COMMAND_OUTPUT_BYTES or len(completed.stderr) > MAX_COMMAND_OUTPUT_BYTES:
            raise ProviderInventoryError("openshell_provider_inventory_output_too_large")
        return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def _checked_output(result: CommandResult, *, error_code: str) -> bytes:
    if result.returncode != 0 or result.stderr.strip():
        raise ProviderInventoryError(error_code)
    if len(result.stdout) > MAX_COMMAND_OUTPUT_BYTES:
        raise ProviderInventoryError("openshell_provider_inventory_output_too_large")
    return result.stdout


def normalize_inventory(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, list):
        raise ProviderInventoryError("openshell_provider_inventory_shape_invalid")
    providers: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in payload:
        if not isinstance(raw, dict):
            raise ProviderInventoryError("openshell_provider_inventory_item_invalid")
        name = raw.get("name")
        provider_type = raw.get("type")
        credential_keys = raw.get("credential_keys")
        if (
            not isinstance(name, str)
            or not SAFE_NAME_RE.fullmatch(name)
            or not isinstance(provider_type, str)
            or not SAFE_TYPE_RE.fullmatch(provider_type)
            or not isinstance(credential_keys, list)
            or any(not isinstance(key, str) or not ENV_NAME_RE.fullmatch(key) for key in credential_keys)
        ):
            raise ProviderInventoryError("openshell_provider_inventory_item_invalid")
        if name in seen:
            raise ProviderInventoryError("openshell_provider_inventory_duplicate")
        seen.add(name)
        # OpenShell 0.0.83 has no provider health/state field. Presence in the
        # gateway registry with valid type/credential metadata proves only that
        # the provider is configured, not that an external request will succeed.
        providers.append({"name": name, "state": "configured"})
    providers.sort(key=lambda item: item["name"])
    return {
        "schema_version": SCHEMA_VERSION,
        "openshell_version": OPENSHELL_VERSION,
        "gateway": GATEWAY,
        "providers": providers,
    }


def collect_inventory(*, project_root: Path, runner: Runner | None = None) -> dict[str, Any]:
    root = project_root.resolve(strict=True)
    selected_runner = runner or SubprocessRunner()
    version = _checked_output(
        selected_runner.run(["--version"], project_root=root),
        error_code="openshell_version_check_failed",
    )
    try:
        version_line = version.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise ProviderInventoryError("openshell_version_check_failed") from exc
    if version_line != f"openshell {OPENSHELL_VERSION}":
        raise ProviderInventoryError("openshell_version_mismatch")

    raw = _checked_output(
        selected_runner.run(["provider", "list", "--limit", "1000", "-o", "json"], project_root=root),
        error_code="openshell_provider_list_failed",
    )
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProviderInventoryError("openshell_provider_inventory_json_invalid") from exc
    return normalize_inventory(payload)


def _safe_output(project_root: Path, output: Path) -> tuple[Path, Path]:
    root = project_root.resolve(strict=True)
    proof_root = root / OUTPUT_RELATIVE.parent
    current = root
    for part in OUTPUT_RELATIVE.parent.parts:
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            current.mkdir(mode=0o700)
            continue
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
            raise ProviderInventoryError("provider_inventory_output_root_unsafe")
        if current != root / "var" and stat.S_IMODE(info.st_mode) & 0o077:
            raise ProviderInventoryError("provider_inventory_output_root_unsafe")

    candidate = output if output.is_absolute() else root / output
    if candidate.parent != proof_root or candidate.name != OUTPUT_RELATIVE.name:
        raise ProviderInventoryError("provider_inventory_output_path_invalid")
    try:
        existing = candidate.lstat()
    except FileNotFoundError:
        pass
    else:
        if (
            not stat.S_ISREG(existing.st_mode)
            or existing.st_uid != os.geteuid()
            or existing.st_nlink != 1
            or stat.S_IMODE(existing.st_mode) != 0o600
        ):
            raise ProviderInventoryError("provider_inventory_output_file_unsafe")
    return proof_root, candidate


def write_inventory(*, project_root: Path, output: Path, inventory: dict[str, Any]) -> Path:
    proof_root, destination = _safe_output(project_root, output)
    content = (json.dumps(inventory, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode("ascii")
    descriptor, temporary_name = tempfile.mkstemp(prefix=".provider-inventory.", suffix=".tmp", dir=proof_root)
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
        inventory = collect_inventory(project_root=root)
        destination = write_inventory(project_root=root, output=args.output, inventory=inventory)
        result = {
            "ok": True,
            "schema_version": SCHEMA_VERSION,
            "provider_count": len(inventory["providers"]),
            "output": destination.relative_to(root).as_posix(),
        }
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
        return 0
    except (OSError, ProviderInventoryError, ValueError) as exc:
        code = str(exc) if isinstance(exc, ProviderInventoryError) else "provider_inventory_export_failed"
        print(json.dumps({"ok": False, "error_code": code}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
