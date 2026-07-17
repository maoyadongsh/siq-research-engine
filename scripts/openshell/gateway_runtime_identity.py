#!/usr/bin/env python3
"""Create and verify a process-bound runtime identity for the SIQ gateway."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import platform
import re
import signal
import socket
import stat
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

REPO_ROOT = Path(__file__).resolve().parents[2]
VERSION = "0.0.83"
GATEWAY_NAME = "siq-openshell-dev"
SCHEMA = "siq.openshell.gateway_process.v1"
STARTING_SCHEMA = "siq.openshell.gateway_starting_process.v1"
GATEWAY_PORT = 17671
HEALTH_PORT = 17672
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
PIDFD_SEND_SIGNAL_SYSCALL = 424
PIDFD_OPEN_SYSCALL = 434
RECORD_KEYS = {
    "schema",
    "pid",
    "start_ticks",
    "executable",
    "binary_sha256",
    "cmdline_sha256",
    "config_path",
    "config_sha256",
    "activation_sha256",
    "db_path",
    "created_at",
}


class GatewayRuntimeError(RuntimeError):
    pass


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _private_regular(path: Path, *, label: str) -> os.stat_result:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise GatewayRuntimeError(f"{label} is missing: {path}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise GatewayRuntimeError(f"{label} must be a regular non-symlink file: {path}")
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise GatewayRuntimeError(f"{label} has unsafe ownership or mode: {path}")
    return info


def _process_start_ticks(pid: int) -> int:
    stat_line = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    closing = stat_line.rfind(")")
    if closing < 0:
        raise GatewayRuntimeError("gateway process stat is malformed")
    fields = stat_line[closing + 2 :].split()
    if len(fields) < 20:
        raise GatewayRuntimeError("gateway process stat has too few fields")
    value = int(fields[19])
    if value <= 0:
        raise GatewayRuntimeError("gateway process start ticks are invalid")
    return value


def _read_environ(pid: int) -> dict[bytes, bytes]:
    result: dict[bytes, bytes] = {}
    for entry in Path(f"/proc/{pid}/environ").read_bytes().split(b"\0"):
        if not entry:
            continue
        key, separator, value = entry.partition(b"=")
        if not separator or key in result:
            raise GatewayRuntimeError("gateway process environment is malformed")
        result[key] = value
    return result


def _process_socket_inodes(pid: int) -> set[int]:
    inodes: set[int] = set()
    try:
        descriptors = Path(f"/proc/{pid}/fd").iterdir()
        for descriptor in descriptors:
            try:
                target = os.readlink(descriptor)
            except FileNotFoundError:
                continue
            match = re.fullmatch(r"socket:\[(\d+)]", target)
            if match:
                inodes.add(int(match.group(1)))
    except FileNotFoundError as exc:
        raise GatewayRuntimeError("gateway process disappeared while inspecting sockets") from exc
    return inodes


def _listening_sockets(ports: set[int]) -> list[tuple[str, int, int]]:
    listeners: list[tuple[str, int, int]] = []
    for source in (Path("/proc/net/tcp"), Path("/proc/net/tcp6")):
        try:
            lines = source.read_text(encoding="ascii").splitlines()[1:]
        except FileNotFoundError:
            continue
        for line in lines:
            fields = line.split()
            if len(fields) < 10 or fields[3] != "0A":
                continue
            address, port_hex = fields[1].rsplit(":", 1)
            port = int(port_hex, 16)
            if port in ports:
                listeners.append((address, port, int(fields[9])))
    return listeners


def _verify_listener_ownership(pid: int) -> None:
    process_inodes = _process_socket_inodes(pid)
    listeners = _listening_sockets({GATEWAY_PORT, HEALTH_PORT})
    if not listeners or any(inode not in process_inodes for _, _, inode in listeners):
        raise GatewayRuntimeError("gateway ports are not exclusively owned by the attested process")
    loopback = "0100007F"
    if not any(address == loopback and port == GATEWAY_PORT for address, port, _ in listeners):
        raise GatewayRuntimeError("gateway loopback listener is missing")
    if not any(address == loopback and port == HEALTH_PORT for address, port, _ in listeners):
        raise GatewayRuntimeError("gateway health listener is missing")


def _verify_health() -> None:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(
        f"http://127.0.0.1:{HEALTH_PORT}/healthz",
        method="GET",
        headers={"Connection": "close"},
    )
    try:
        with opener.open(request, timeout=1.0) as response:
            if response.status != 200:
                raise GatewayRuntimeError("gateway health endpoint returned a non-200 status")
            response.read(4096)
    except (OSError, urllib.error.URLError, socket.timeout) as exc:
        raise GatewayRuntimeError("gateway health endpoint is unavailable") from exc


def _paths(project_root: Path) -> dict[str, Path]:
    root = project_root.resolve(strict=True)
    gateway_root = root / f"var/openshell/gateway/{GATEWAY_NAME}"
    return {
        "root": root,
        "gateway_root": gateway_root,
        "binary": root / f"var/openshell/toolchains/v{VERSION}/bin/openshell-gateway",
        "config": gateway_root / "gateway.toml",
        "activation": gateway_root / "bind-contract.activation.json",
        "database": gateway_root / "openshell.db",
        "pid_file": gateway_root / "gateway.pid",
        "runtime": gateway_root / "gateway.runtime.json",
        "start_intent": gateway_root / "gateway.start.intent.json",
        "starting": gateway_root / "gateway.starting.json",
    }


def _activation_sha(path: Path) -> str:
    try:
        path.lstat()
    except FileNotFoundError:
        return "absent"
    _private_regular(path, label="bind-contract activation record")
    return _sha256_file(path)


def _read_pid_file(path: Path) -> int:
    _private_regular(path, label="gateway PID file")
    value = path.read_text(encoding="ascii")
    if not re.fullmatch(r"[1-9][0-9]*\n?", value):
        raise GatewayRuntimeError("gateway PID file is malformed")
    return int(value)


def collect_process_identity(
    project_root: Path,
    *,
    pid: int,
    require_pid_file: bool = True,
) -> dict[str, Any]:
    paths = _paths(project_root)
    binary = paths["binary"].resolve(strict=True)
    config = paths["config"].resolve(strict=True)
    _private_regular(binary, label="gateway binary")
    _private_regular(config, label="gateway configuration")
    if require_pid_file and _read_pid_file(paths["pid_file"]) != pid:
        raise GatewayRuntimeError("gateway PID argument does not match its PID file")
    try:
        executable = Path(f"/proc/{pid}/exe").resolve(strict=True)
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
    except FileNotFoundError as exc:
        raise GatewayRuntimeError("gateway process is not running") from exc
    if executable != binary or cmdline != os.fsencode(binary) + b"\0":
        raise GatewayRuntimeError("gateway executable or argv does not match the project binary")

    environment = _read_environ(pid)
    expected_environment = {
        b"OPENSHELL_GATEWAY_CONFIG": os.fsencode(config),
        b"OPENSHELL_DB_URL": os.fsencode(f"sqlite:{paths['database']}"),
        b"OPENSHELL_TELEMETRY_ENABLED": b"false",
        b"OPENSHELL_GATEWAY": GATEWAY_NAME.encode("ascii"),
    }
    if any(environment.get(key) != value for key, value in expected_environment.items()):
        raise GatewayRuntimeError("gateway launch environment does not match the isolated project config")
    return {
        "schema": STARTING_SCHEMA,
        "pid": pid,
        "start_ticks": _process_start_ticks(pid),
        "executable": str(binary),
        "binary_sha256": _sha256_file(binary),
        "cmdline_sha256": _sha256_bytes(cmdline),
        "config_path": str(config),
        "config_sha256": _sha256_file(config),
        "activation_sha256": _activation_sha(paths["activation"]),
        "db_path": str(paths["database"]),
        "created_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }


def collect_runtime_identity(project_root: Path, *, pid: int) -> dict[str, Any]:
    payload = collect_process_identity(project_root, pid=pid)
    _verify_listener_ownership(pid)
    _verify_health()
    payload["schema"] = SCHEMA
    return payload


def _validate_record_shape(payload: Mapping[str, Any], *, expected_schema: str = SCHEMA) -> None:
    if set(payload) != RECORD_KEYS:
        raise GatewayRuntimeError("gateway runtime identity has an unexpected schema")
    if payload.get("schema") != expected_schema:
        raise GatewayRuntimeError("gateway runtime identity schema version is invalid")
    for key in ("pid", "start_ticks"):
        if isinstance(payload.get(key), bool) or not isinstance(payload.get(key), int) or payload[key] <= 0:
            raise GatewayRuntimeError(f"gateway runtime identity field is invalid: {key}")
    for key in (
        "executable",
        "binary_sha256",
        "cmdline_sha256",
        "config_path",
        "config_sha256",
        "activation_sha256",
        "db_path",
        "created_at",
    ):
        if not isinstance(payload.get(key), str) or not payload[key]:
            raise GatewayRuntimeError(f"gateway runtime identity field is invalid: {key}")
    for key in ("binary_sha256", "cmdline_sha256", "config_sha256"):
        if not SHA256_PATTERN.fullmatch(payload[key]):
            raise GatewayRuntimeError(f"gateway runtime identity digest is invalid: {key}")
    if payload["activation_sha256"] != "absent" and not SHA256_PATTERN.fullmatch(payload["activation_sha256"]):
        raise GatewayRuntimeError("gateway activation digest is invalid")


def load_process_identity(
    path: Path,
    *,
    expected_schema: str = SCHEMA,
    label: str = "gateway runtime identity",
) -> dict[str, Any]:
    _private_regular(path, label=label)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GatewayRuntimeError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise GatewayRuntimeError(f"{label} must be a JSON object")
    _validate_record_shape(payload, expected_schema=expected_schema)
    return payload


def load_runtime_identity(path: Path) -> dict[str, Any]:
    return load_process_identity(path)


def verify_starting_identity(project_root: Path, *, starting_path: Path | None = None) -> dict[str, Any]:
    paths = _paths(project_root)
    record_path = starting_path or paths["starting"]
    payload = load_process_identity(
        record_path,
        expected_schema=STARTING_SCHEMA,
        label="gateway provisional identity",
    )
    current = collect_process_identity(paths["root"], pid=payload["pid"])
    for key in RECORD_KEYS - {"created_at"}:
        if payload[key] != current[key]:
            raise GatewayRuntimeError(f"gateway provisional identity drifted: {key}")
    return payload


def verify_runtime_identity(project_root: Path, *, runtime_path: Path | None = None) -> dict[str, Any]:
    paths = _paths(project_root)
    record_path = runtime_path or paths["runtime"]
    payload = load_runtime_identity(record_path)
    current = collect_runtime_identity(paths["root"], pid=payload["pid"])
    for key in RECORD_KEYS - {"created_at"}:
        if payload[key] != current[key]:
            raise GatewayRuntimeError(f"gateway runtime identity drifted: {key}")
    return payload


def _pidfd_send_signal(descriptor: int, signum: int) -> None:
    if hasattr(signal, "pidfd_send_signal"):
        signal.pidfd_send_signal(descriptor, signum)
        return
    if platform.system() != "Linux" or platform.machine() not in {"aarch64", "x86_64"}:
        raise GatewayRuntimeError("this host lacks a reviewed pidfd signaling implementation")
    libc = ctypes.CDLL(None, use_errno=True)
    syscall = libc.syscall
    syscall.restype = ctypes.c_long
    result = syscall(
        ctypes.c_long(PIDFD_SEND_SIGNAL_SYSCALL),
        ctypes.c_int(descriptor),
        ctypes.c_int(signum),
        ctypes.c_void_p(),
        ctypes.c_uint(0),
    )
    if result == -1:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number))


def _pidfd_open(pid: int) -> int:
    if hasattr(os, "pidfd_open"):
        return os.pidfd_open(pid, 0)
    if platform.system() != "Linux" or platform.machine() not in {"aarch64", "x86_64"}:
        raise GatewayRuntimeError("this host lacks a reviewed pidfd_open implementation")
    libc = ctypes.CDLL(None, use_errno=True)
    syscall = libc.syscall
    syscall.restype = ctypes.c_long
    result = syscall(
        ctypes.c_long(PIDFD_OPEN_SYSCALL),
        ctypes.c_int(pid),
        ctypes.c_uint(0),
    )
    if result == -1:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number))
    return int(result)


def terminate_attested_gateway(project_root: Path, *, runtime_path: Path | None = None) -> int:
    paths = _paths(project_root)
    record_path = runtime_path or paths["runtime"]
    payload = verify_runtime_identity(paths["root"], runtime_path=record_path)
    try:
        descriptor = _pidfd_open(payload["pid"])
    except ProcessLookupError as exc:
        raise GatewayRuntimeError("gateway exited before a pidfd could be opened") from exc
    try:
        verify_runtime_identity(paths["root"], runtime_path=record_path)
        _pidfd_send_signal(descriptor, signal.SIGTERM)
    finally:
        os.close(descriptor)
    return payload["pid"]


def _atomic_write(path: Path, payload: Mapping[str, Any]) -> None:
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
    create = subparsers.add_parser("create")
    create.add_argument("--pid", type=int, required=True)
    create.add_argument("--output", type=Path)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--runtime-record", type=Path)
    terminate = subparsers.add_parser("terminate")
    terminate.add_argument("--runtime-record", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        root = args.project_root.resolve(strict=True)
        paths = _paths(root)
        if args.command == "create":
            output = args.output or paths["runtime"]
            output = output if output.is_absolute() else root / output
            if output.parent.resolve(strict=True) != paths["gateway_root"].resolve(strict=True):
                raise GatewayRuntimeError("gateway runtime identity must remain in the project gateway state")
            payload = collect_runtime_identity(root, pid=args.pid)
            _atomic_write(output, payload)
            verify_runtime_identity(root, runtime_path=output)
            print(f"gateway runtime identity created: pid={payload['pid']}")
        elif args.command == "verify":
            record = args.runtime_record or paths["runtime"]
            record = record if record.is_absolute() else root / record
            if record.resolve(strict=True).parent != paths["gateway_root"].resolve(strict=True):
                raise GatewayRuntimeError("gateway runtime identity escaped project gateway state")
            payload = verify_runtime_identity(root, runtime_path=record)
            print(f"gateway runtime identity verified: pid={payload['pid']}")
        else:
            record = args.runtime_record or paths["runtime"]
            record = record if record.is_absolute() else root / record
            if record.resolve(strict=True).parent != paths["gateway_root"].resolve(strict=True):
                raise GatewayRuntimeError("gateway runtime identity escaped project gateway state")
            pid = terminate_attested_gateway(root, runtime_path=record)
            print(f"gateway termination signaled through pidfd: pid={pid}")
        return 0
    except (OSError, ValueError, GatewayRuntimeError) as exc:
        print(f"gateway runtime error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
