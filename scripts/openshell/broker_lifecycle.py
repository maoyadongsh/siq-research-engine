#!/usr/bin/env python3
"""Start, stop, or inspect the two fixed SIQ OpenShell host brokers."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import http.client
import ipaddress
import json
import os
import re
import signal
import stat
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Protocol, Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.openshell import bridge_endpoint, broker_request_identity
from scripts.openshell.gateway_runtime_identity import GatewayRuntimeError, _pidfd_open, _pidfd_send_signal

SCHEMA_VERSION = "siq.openshell.broker-lifecycle.v1"
PID_SCHEMA_VERSION = "siq.openshell.broker-pid.v2"
LEGACY_PID_SCHEMA_VERSION = "siq.openshell.broker-pid.v1"
REPO_ROOT = Path(__file__).resolve().parents[2]
STATE_RELATIVE_ROOT = Path("var/openshell/brokers")
LOCK_RELATIVE_PATH = Path("var/openshell/locks/maintenance.lock")
STARTUP_ATTEMPTS = 100
STOP_ATTEMPTS = 50
POLL_SECONDS = 0.1
MAX_STATE_BYTES = 16 * 1024
MAX_SECRET_BYTES = 16 * 1024
POSTGRES_SECRET_RELATIVE_PATH = Path("var/openshell/secrets/postgres-reader.env")
IDENTITY_KEY_RELATIVE_PATH = Path("var/openshell/secrets/broker-request-identity.key")
MIHOMO_RUNTIME_RELATIVE_PATH = Path("infra/openshell/egress/mihomo-runtime.json")
ACTIVE_FORMAL_RUN_RELATIVE_PATH = Path("var/openshell/siq-analysis/active-run.json")
REQUIRED_POSTGRES_ENV = (
    "SIQ_OPENSHELL_PG_RO_HOST",
    "SIQ_OPENSHELL_PG_RO_PORT",
    "SIQ_OPENSHELL_PG_RO_USER",
    "SIQ_OPENSHELL_PG_RO_PASSWORD",
)
SENSITIVE_ENV_NAMES = (
    "SIQ_OPENSHELL_PG_RO_PASSWORD",
    "SIQ_OPENSHELL_MILVUS_RO_PASSWORD",
    "SIQ_OPENSHELL_MILVUS_RO_TOKEN",
)
DATA_BROKER_ENV_NAMES = (
    *REQUIRED_POSTGRES_ENV,
    "SIQ_OPENSHELL_PG_RO_SSLMODE",
    "SIQ_OPENSHELL_MILVUS_RO_HOST",
    "SIQ_OPENSHELL_MILVUS_RO_PORT",
    "SIQ_OPENSHELL_MILVUS_RO_DATABASE",
    "SIQ_OPENSHELL_MILVUS_RO_USER",
    "SIQ_OPENSHELL_MILVUS_RO_PASSWORD",
    "SIQ_OPENSHELL_MILVUS_RO_TOKEN",
)
AUDIT_ENV_NAMES = (
    "SIQ_OPENSHELL_AUDIT_PROFILE",
    "SIQ_OPENSHELL_AUDIT_SANDBOX_ID",
    "SIQ_OPENSHELL_AUDIT_SESSION_ID",
    "SIQ_OPENSHELL_AUDIT_POLICY_DIGEST",
)
EGRESS_BROKER_ENV_NAMES = (
    "SIQ_OPENSHELL_MIHOMO_FAKE_IP_COMPAT",
    "SIQ_OPENSHELL_MIHOMO_CONTROL_SOCKET",
    "SIQ_OPENSHELL_MIHOMO_FAKE_IP_RANGE",
)
PID_KEYS_V1 = {
    "schema_version",
    "broker",
    "pid",
    "start_ticks",
    "command_digest",
    "network_id",
    "gateway_ip",
    "port",
}
PID_KEYS = PID_KEYS_V1 | {"request_identity_required", "identity_key_sha256"}
SOCKET_INODE_RE = re.compile(r"socket:\[([0-9]+)\]\Z")
SECRET_LINE_RE = re.compile(r"([A-Z_][A-Z0-9_]*)=([^\r\n\x00]*)\Z")
MIHOMO_RUNTIME_SCHEMA = "siq.openshell.mihomo-runtime.v1"
MIHOMO_ENV_NAMES = (
    "SIQ_OPENSHELL_MIHOMO_FAKE_IP_COMPAT",
    "SIQ_OPENSHELL_MIHOMO_CONTROL_SOCKET",
    "SIQ_OPENSHELL_MIHOMO_FAKE_IP_RANGE",
)


class LifecycleError(RuntimeError):
    """Stable lifecycle error that never includes environment values or raw logs."""


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, child in pairs:
        if key in value:
            raise ValueError("duplicate_json_key")
        value[key] = child
    return value


@dataclass(frozen=True)
class BrokerSpec:
    name: str
    script_name: str
    port: int
    health_path: str
    service_name: str

    def command(self, *, project_root: Path, python_executable: str) -> tuple[str, ...]:
        return (
            python_executable,
            str((project_root / "scripts/openshell" / self.script_name).resolve(strict=True)),
            "--bridge-bind",
            "--port",
            str(self.port),
            "--project-root",
            str(project_root),
        )


BROKER_SPECS = (
    BrokerSpec(
        name="egress",
        script_name="egress_guard.py",
        port=18_792,
        health_path="/health",
        service_name="siq-egress-guard",
    ),
    BrokerSpec(
        name="data",
        script_name="read_only_data_broker.py",
        port=18_793,
        health_path="/healthz",
        service_name="siq-read-only-data-broker",
    ),
)


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    executable: str
    cmdline: tuple[str, ...]
    start_ticks: int


@dataclass(frozen=True)
class Listener:
    address: str
    port: int
    pids: frozenset[int]


@dataclass(frozen=True)
class PidRecord:
    broker: str
    pid: int
    start_ticks: int
    command_digest: str
    network_id: str
    gateway_ip: str
    port: int
    request_identity_required: bool = False
    identity_key_sha256: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": PID_SCHEMA_VERSION,
            "broker": self.broker,
            "pid": self.pid,
            "start_ticks": self.start_ticks,
            "command_digest": self.command_digest,
            "network_id": self.network_id,
            "gateway_ip": self.gateway_ip,
            "port": self.port,
            "request_identity_required": self.request_identity_required,
            "identity_key_sha256": self.identity_key_sha256,
        }


class RuntimeBackend(Protocol):
    def spawn(self, command: Sequence[str], *, env: Mapping[str, str], log_path: Path) -> int: ...

    def process_info(self, pid: int) -> ProcessInfo | None: ...

    def matching_pids(self, command: Sequence[str]) -> tuple[int, ...]: ...

    def listeners(self, port: int) -> tuple[Listener, ...]: ...

    def health(
        self,
        *,
        host: str,
        port: int,
        path: str,
        host_alias: str,
        service_name: str,
    ) -> bool: ...

    def terminate(self, pid: int, *, verify: Callable[[], bool]) -> None: ...

    def sleep(self, seconds: float) -> None: ...


def _load_project_postgres_environment(project_root: Path) -> dict[str, str]:
    root = project_root.resolve(strict=True)
    path = root / POSTGRES_SECRET_RELATIVE_PATH
    current = root
    for component in POSTGRES_SECRET_RELATIVE_PATH.parts:
        current = current / component
        if current.is_symlink():
            raise LifecycleError("postgresql_secret_file_unsafe")
    try:
        info = path.lstat()
    except OSError as exc:
        raise LifecycleError("postgresql_secret_file_missing") from exc
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_size <= 0
        or info.st_size > MAX_SECRET_BYTES
    ):
        raise LifecycleError("postgresql_secret_file_unsafe")
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise LifecycleError("postgresql_secret_file_unreadable") from exc
    values: dict[str, str] = {}
    allowed = {*REQUIRED_POSTGRES_ENV, "SIQ_OPENSHELL_PG_RO_SSLMODE"}
    for line in content.splitlines():
        matched = SECRET_LINE_RE.fullmatch(line)
        if matched is None or matched.group(1) in values or matched.group(1) not in allowed:
            raise LifecycleError("postgresql_secret_file_invalid")
        values[matched.group(1)] = matched.group(2)
    if set(values) != allowed:
        raise LifecycleError("postgresql_secret_file_invalid")
    if (
        values["SIQ_OPENSHELL_PG_RO_HOST"] != "127.0.0.1"
        or values["SIQ_OPENSHELL_PG_RO_PORT"] != "15432"
        or values["SIQ_OPENSHELL_PG_RO_USER"] != "siq_openshell_reader"
        or values["SIQ_OPENSHELL_PG_RO_SSLMODE"] != "prefer"
        or not values["SIQ_OPENSHELL_PG_RO_PASSWORD"]
        or len(values["SIQ_OPENSHELL_PG_RO_PASSWORD"]) > 4_096
    ):
        raise LifecycleError("postgresql_secret_file_invalid")
    return values


def _load_project_mihomo_environment(
    project_root: Path,
    environment: Mapping[str, str],
) -> dict[str, str]:
    if any(name in environment for name in MIHOMO_ENV_NAMES):
        return {}
    path = project_root.resolve(strict=True) / MIHOMO_RUNTIME_RELATIVE_PATH
    try:
        info = path.lstat()
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise LifecycleError("mihomo_runtime_config_unreadable") from exc
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_nlink != 1
        or info.st_size <= 0
        or info.st_size > 4_096
        or stat.S_IMODE(info.st_mode) & 0o002
    ):
        raise LifecycleError("mihomo_runtime_config_unsafe")
    descriptor = -1
    try:
        if path.resolve(strict=True) != path:
            raise LifecycleError("mihomo_runtime_config_unsafe")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino, opened.st_size) != (info.st_dev, info.st_ino, info.st_size):
            raise LifecycleError("mihomo_runtime_config_changed")
        content = os.read(descriptor, 4_097)
        after = os.fstat(descriptor)
        if len(content) != info.st_size or (after.st_dev, after.st_ino, after.st_size) != (
            info.st_dev,
            info.st_ino,
            info.st_size,
        ):
            raise LifecycleError("mihomo_runtime_config_changed")
        payload = json.loads(content, object_pairs_hook=_reject_duplicate_json_keys)
    except LifecycleError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise LifecycleError("mihomo_runtime_config_invalid") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema_version", "mode", "control_socket", "fake_ip_range"}
        or payload.get("schema_version") != MIHOMO_RUNTIME_SCHEMA
        or payload.get("mode") != "auto_if_socket_present"
        or payload.get("fake_ip_range") != "198.18.0.0/16"
        or not isinstance(payload.get("control_socket"), str)
    ):
        raise LifecycleError("mihomo_runtime_config_invalid")
    control_socket = Path(payload["control_socket"])
    if not control_socket.is_absolute() or ".." in control_socket.parts:
        raise LifecycleError("mihomo_runtime_config_invalid")
    try:
        socket_info = control_socket.lstat()
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise LifecycleError("mihomo_runtime_socket_unreadable") from exc
    if (
        stat.S_ISLNK(socket_info.st_mode)
        or not stat.S_ISSOCK(socket_info.st_mode)
        or socket_info.st_uid not in {0, os.geteuid()}
    ):
        raise LifecycleError("mihomo_runtime_socket_unsafe")
    return {
        "SIQ_OPENSHELL_MIHOMO_FAKE_IP_COMPAT": "1",
        "SIQ_OPENSHELL_MIHOMO_CONTROL_SOCKET": str(control_socket),
        "SIQ_OPENSHELL_MIHOMO_FAKE_IP_RANGE": payload["fake_ip_range"],
    }


def _startup_environment(project_root: Path, environment: Mapping[str, str]) -> dict[str, str]:
    resolved = dict(environment)
    secret_path = project_root.resolve(strict=True) / POSTGRES_SECRET_RELATIVE_PATH
    if secret_path.exists() or not all(str(resolved.get(name) or "").strip() for name in REQUIRED_POSTGRES_ENV):
        file_values = _load_project_postgres_environment(project_root)
        for name, value in file_values.items():
            inherited = resolved.get(name)
            if inherited is not None and inherited != value:
                raise LifecycleError("postgresql_environment_conflict")
            resolved[name] = value
    resolved.update(_load_project_mihomo_environment(project_root, resolved))
    return resolved


def _decode_proc_address(value: str, *, ipv6: bool) -> str:
    try:
        raw = bytes.fromhex(value)
        if ipv6:
            if len(raw) != 16:
                raise ValueError
            packed = b"".join(raw[index : index + 4][::-1] for index in range(0, 16, 4))
            return ipaddress.ip_address(packed).compressed
        if len(raw) != 4:
            raise ValueError
        return ipaddress.ip_address(raw[::-1]).compressed
    except ValueError as exc:
        raise LifecycleError("proc_listener_address_invalid") from exc


class ProcRuntimeBackend:
    def spawn(self, command: Sequence[str], *, env: Mapping[str, str], log_path: Path) -> int:
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(log_path, flags, 0o600)
        except OSError as exc:
            raise LifecycleError("broker_log_open_failed") from exc
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                raise LifecycleError("broker_log_unsafe")
            os.fchmod(descriptor, 0o600)
            try:
                process = subprocess.Popen(
                    list(command),
                    stdin=subprocess.DEVNULL,
                    stdout=descriptor,
                    stderr=subprocess.STDOUT,
                    env=dict(env),
                    close_fds=True,
                    start_new_session=True,
                )
            except OSError as exc:
                raise LifecycleError("broker_spawn_failed") from exc
            return process.pid
        finally:
            os.close(descriptor)

    def process_info(self, pid: int) -> ProcessInfo | None:
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 1:
            return None
        root = Path("/proc") / str(pid)
        try:
            executable = str((root / "exe").resolve(strict=True))
            content = (root / "cmdline").read_bytes()
            stat_content = (root / "stat").read_text(encoding="ascii")
        except (FileNotFoundError, PermissionError, OSError, UnicodeDecodeError):
            return None
        if not content or len(content) > 64 * 1024:
            return None
        raw_parts = content.rstrip(b"\0").split(b"\0")
        try:
            cmdline = tuple(part.decode("utf-8") for part in raw_parts)
            _prefix, separator, fields = stat_content.rpartition(") ")
            if not separator:
                return None
            start_ticks = int(fields.split()[19])
        except (UnicodeDecodeError, ValueError, IndexError):
            return None
        return ProcessInfo(pid=pid, executable=executable, cmdline=cmdline, start_ticks=start_ticks)

    def matching_pids(self, command: Sequence[str]) -> tuple[int, ...]:
        expected = tuple(command)
        matches: list[int] = []
        for candidate in Path("/proc").glob("[0-9]*"):
            try:
                pid = int(candidate.name)
            except ValueError:
                continue
            info = self.process_info(pid)
            if info is not None and info.cmdline == expected:
                matches.append(pid)
        return tuple(sorted(matches))

    def listeners(self, port: int) -> tuple[Listener, ...]:
        sockets: list[tuple[str, int, str]] = []
        for path, ipv6 in ((Path("/proc/net/tcp"), False), (Path("/proc/net/tcp6"), True)):
            try:
                lines = path.read_text(encoding="ascii").splitlines()[1:]
            except (OSError, UnicodeDecodeError):
                continue
            for line in lines:
                fields = line.split()
                if len(fields) < 10 or fields[3] != "0A":
                    continue
                try:
                    raw_address, raw_port = fields[1].split(":", 1)
                    resolved_port = int(raw_port, 16)
                except (ValueError, IndexError):
                    continue
                if resolved_port != port:
                    continue
                address = _decode_proc_address(raw_address, ipv6=ipv6)
                sockets.append((address, resolved_port, fields[9]))
        inode_pids: dict[str, set[int]] = {inode: set() for _, _, inode in sockets}
        if inode_pids:
            for process in Path("/proc").glob("[0-9]*"):
                try:
                    pid = int(process.name)
                except ValueError:
                    continue
                try:
                    descriptors = list((process / "fd").iterdir())
                except (FileNotFoundError, PermissionError, OSError):
                    continue
                for descriptor in descriptors:
                    try:
                        target = os.readlink(descriptor)
                    except (FileNotFoundError, PermissionError, OSError):
                        continue
                    matched = SOCKET_INODE_RE.fullmatch(target)
                    if matched and matched.group(1) in inode_pids:
                        inode_pids[matched.group(1)].add(pid)
        return tuple(
            Listener(address=address, port=resolved_port, pids=frozenset(inode_pids[inode]))
            for address, resolved_port, inode in sockets
        )

    def health(
        self,
        *,
        host: str,
        port: int,
        path: str,
        host_alias: str,
        service_name: str,
    ) -> bool:
        connection = http.client.HTTPConnection(host, port, timeout=0.5)
        try:
            connection.request("GET", path, headers={"Host": f"{host_alias}:{port}"})
            response = connection.getresponse()
            content = response.read(64 * 1024 + 1)
        except (OSError, http.client.HTTPException):
            return False
        finally:
            connection.close()
        if response.status != 200 or len(content) > 64 * 1024:
            return False
        try:
            payload = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return False
        return isinstance(payload, dict) and payload.get("ok") is True and payload.get("service") == service_name

    def terminate(self, pid: int, *, verify: Callable[[], bool]) -> None:
        try:
            descriptor = _pidfd_open(pid)
        except ProcessLookupError:
            return
        except (GatewayRuntimeError, OSError) as exc:
            raise LifecycleError("broker_pidfd_open_failed") from exc
        try:
            if not verify():
                return
            try:
                _pidfd_send_signal(descriptor, signal.SIGTERM)
            except ProcessLookupError:
                return
        except (GatewayRuntimeError, OSError) as exc:
            raise LifecycleError("broker_terminate_failed") from exc
        finally:
            os.close(descriptor)

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


def _command_digest(command: Sequence[str]) -> str:
    return hashlib.sha256(b"\0".join(item.encode("utf-8") for item in command)).hexdigest()


def _assert_safe_directory(path: Path, *, create: bool = False) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        if not create:
            raise LifecycleError("broker_state_directory_missing") from exc
        try:
            path.mkdir(mode=0o700)
            info = path.lstat()
        except OSError as exc:
            raise LifecycleError("broker_state_directory_create_failed") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise LifecycleError("broker_state_directory_unsafe")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise LifecycleError("broker_state_directory_permissions")


def _prepare_state_root(project_root: Path) -> Path:
    try:
        root = project_root.resolve(strict=True)
    except OSError as exc:
        raise LifecycleError("project_root_invalid") from exc
    openshell_root = root / "var/openshell"
    _assert_safe_directory(openshell_root)
    state_root = root / STATE_RELATIVE_ROOT
    _assert_safe_directory(state_root, create=True)
    return state_root


def _assert_safe_regular_file(path: Path, *, required: bool, expected_mode: int = 0o600) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        if required:
            raise LifecycleError("broker_state_file_missing") from exc
        return False
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != expected_mode
    ):
        raise LifecycleError("broker_state_file_unsafe")
    return True


def _read_secure_json(path: Path) -> Any:
    _assert_safe_regular_file(path, required=True)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise LifecycleError("broker_state_read_failed") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise LifecycleError("broker_state_file_unsafe")
        content = os.read(descriptor, MAX_STATE_BYTES + 1)
        if len(content) > MAX_STATE_BYTES:
            raise LifecycleError("broker_state_too_large")
    finally:
        os.close(descriptor)
    try:
        return json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LifecycleError("broker_state_json_invalid") from exc


def _write_secure_json(path: Path, payload: Mapping[str, Any]) -> None:
    if _assert_safe_regular_file(path, required=False) and path.is_symlink():
        raise LifecycleError("broker_state_file_unsafe")
    content = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode() + b"\n"
    if len(content) > MAX_STATE_BYTES:
        raise LifecycleError("broker_state_too_large")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(temporary, flags, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            if os.write(descriptor, content) != len(content):
                raise LifecycleError("broker_state_short_write")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, path)
    except OSError as exc:
        raise LifecycleError("broker_state_write_failed") from exc
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _load_pid_record(path: Path, spec: BrokerSpec) -> PidRecord | None:
    if not _assert_safe_regular_file(path, required=False):
        return None
    payload = _read_secure_json(path)
    if not isinstance(payload, dict):
        raise LifecycleError("broker_pid_state_invalid")
    schema_version = payload.get("schema_version")
    if not (
        schema_version == PID_SCHEMA_VERSION
        and set(payload) == PID_KEYS
        or schema_version == LEGACY_PID_SCHEMA_VERSION
        and set(payload) == PID_KEYS_V1
    ):
        raise LifecycleError("broker_pid_state_invalid")
    if any(
        isinstance(payload.get(key), bool) or not isinstance(payload.get(key), int)
        for key in ("pid", "start_ticks", "port")
    ) or any(not isinstance(payload.get(key), str) for key in ("broker", "command_digest", "network_id", "gateway_ip")):
        raise LifecycleError("broker_pid_state_invalid")
    try:
        record = PidRecord(
            broker=str(payload["broker"]),
            pid=int(payload["pid"]),
            start_ticks=int(payload["start_ticks"]),
            command_digest=str(payload["command_digest"]),
            network_id=str(payload["network_id"]),
            gateway_ip=str(payload["gateway_ip"]),
            port=int(payload["port"]),
            request_identity_required=bool(payload.get("request_identity_required", False)),
            identity_key_sha256=str(payload.get("identity_key_sha256") or ""),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise LifecycleError("broker_pid_state_invalid") from exc
    if (
        record.broker != spec.name
        or record.pid <= 1
        or record.start_ticks <= 0
        or not re.fullmatch(r"[0-9a-f]{64}", record.command_digest)
        or not re.fullmatch(r"[0-9a-f]{64}", record.network_id)
        or record.port != spec.port
        or not isinstance(payload.get("request_identity_required", False), bool)
        or (record.request_identity_required and not re.fullmatch(r"[0-9a-f]{64}", record.identity_key_sha256))
        or (not record.request_identity_required and record.identity_key_sha256 != "")
    ):
        raise LifecycleError("broker_pid_state_invalid")
    try:
        address = ipaddress.ip_address(record.gateway_ip)
    except ValueError as exc:
        raise LifecycleError("broker_pid_state_invalid") from exc
    if not isinstance(address, ipaddress.IPv4Address) or not address.is_private:
        raise LifecycleError("broker_pid_state_invalid")
    return record


@contextmanager
def _maintenance_lock(project_root: Path) -> Iterator[None]:
    lock_path = project_root / LOCK_RELATIVE_PATH
    _assert_safe_directory(lock_path.parent, create=True)
    if lock_path.exists():
        _assert_safe_regular_file(lock_path, required=True)
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise LifecycleError("maintenance_lock_open_failed") from exc
    try:
        os.fchmod(descriptor, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise LifecycleError("maintenance_lock_busy") from exc
        yield
    finally:
        os.close(descriptor)


class BrokerLifecycle:
    def __init__(
        self,
        *,
        project_root: Path = REPO_ROOT,
        backend: RuntimeBackend | None = None,
        discoverer: Any = bridge_endpoint.discover_bridge_endpoint,
        environ: Mapping[str, str] | None = None,
        startup_attempts: int = STARTUP_ATTEMPTS,
        stop_attempts: int = STOP_ATTEMPTS,
        require_request_identity: bool = False,
    ) -> None:
        self.project_root = project_root.resolve(strict=True)
        self.state_root = _prepare_state_root(self.project_root)
        self.backend = backend or ProcRuntimeBackend()
        self.discoverer = discoverer
        self.environ = dict(os.environ if environ is None else environ)
        self.python_executable = str(Path(sys.executable).resolve(strict=True))
        self.startup_attempts = startup_attempts
        self.stop_attempts = stop_attempts
        self.require_request_identity = require_request_identity

    def _command(self, spec: BrokerSpec) -> tuple[str, ...]:
        return spec.command(project_root=self.project_root, python_executable=self.python_executable)

    def _pid_path(self, spec: BrokerSpec) -> Path:
        return self.state_root / f"{spec.name}.pid"

    def _log_path(self, spec: BrokerSpec) -> Path:
        return self.state_root / f"{spec.name}.log"

    def _bridge_path(self) -> Path:
        return self.state_root / "bridge.json"

    def _prepare_log(self, spec: BrokerSpec) -> None:
        path = self._log_path(spec)
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags, 0o600)
        except OSError as exc:
            raise LifecycleError("broker_log_open_failed") from exc
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                raise LifecycleError("broker_log_unsafe")
            os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)

    def _required_environment(self) -> None:
        if any(not str(self.environ.get(name) or "").strip() for name in REQUIRED_POSTGRES_ENV):
            raise LifecycleError("postgresql_broker_environment_missing")
        commands = "\0".join(item for spec in BROKER_SPECS for item in self._command(spec))
        for name in SENSITIVE_ENV_NAMES:
            value = str(self.environ.get(name) or "")
            if value and value in commands:
                raise LifecycleError("secret_in_broker_argv")

    def _child_environment(self, spec: BrokerSpec) -> dict[str, str]:
        environment = {
            "HOME": str(self.state_root),
            "LANG": "C.UTF-8",
            "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
            "XDG_CACHE_HOME": str(self.state_root / "cache"),
        }
        names = (
            (*AUDIT_ENV_NAMES, *DATA_BROKER_ENV_NAMES)
            if spec.name == "data"
            else (*AUDIT_ENV_NAMES, *EGRESS_BROKER_ENV_NAMES)
        )
        for name in names:
            value = self.environ.get(name)
            if value is not None:
                environment[name] = str(value)
        if self.require_request_identity:
            environment["SIQ_OPENSHELL_REQUIRE_REQUEST_IDENTITY"] = "1"
            environment["SIQ_OPENSHELL_BROKER_IDENTITY_KEY_FILE"] = str(self.project_root / IDENTITY_KEY_RELATIVE_PATH)
        return environment

    def _identity_key_digest(self, *, create: bool) -> str:
        path = self.project_root / IDENTITY_KEY_RELATIVE_PATH
        try:
            key = (
                broker_request_identity.ensure_key_file(path) if create else broker_request_identity.read_key_file(path)
            )
        except broker_request_identity.IdentityError as exc:
            raise LifecycleError("broker_identity_key_invalid") from exc
        return hashlib.sha256(key).hexdigest()

    def _discover(self) -> bridge_endpoint.BridgeEndpoint:
        try:
            endpoint = self.discoverer()
            endpoint.validate()
            return endpoint
        except bridge_endpoint.BridgeEndpointError as exc:
            raise LifecycleError("verified_bridge_unavailable") from exc

    def _recorded_command(
        self,
        spec: BrokerSpec,
        record: PidRecord,
        info: ProcessInfo | None,
    ) -> tuple[str, ...] | None:
        if info is None or info.pid != record.pid or info.start_ticks != record.start_ticks:
            return None
        try:
            observed_executable = Path(info.executable)
            if (
                not observed_executable.is_absolute()
                or str(observed_executable.resolve(strict=True)) != info.executable
            ):
                return None
        except OSError:
            return None
        command = spec.command(
            project_root=self.project_root,
            python_executable=info.executable,
        )
        if info.cmdline != command or record.command_digest != _command_digest(command):
            return None
        return command

    def _process_matches(self, spec: BrokerSpec, record: PidRecord, info: ProcessInfo | None) -> bool:
        return self._recorded_command(spec, record, info) is not None

    def _listener_matches(self, spec: BrokerSpec, pid: int, endpoint: bridge_endpoint.BridgeEndpoint) -> bool:
        listeners = self.backend.listeners(spec.port)
        return len(listeners) == 1 and listeners[0] == Listener(
            address=endpoint.gateway_ip,
            port=spec.port,
            pids=frozenset({pid}),
        )

    def _health(self, spec: BrokerSpec, endpoint: bridge_endpoint.BridgeEndpoint) -> bool:
        return self.backend.health(
            host=endpoint.gateway_ip,
            port=spec.port,
            path=spec.health_path,
            host_alias=endpoint.host_alias,
            service_name=spec.service_name,
        )

    def _record_matches_endpoint(self, record: PidRecord, endpoint: bridge_endpoint.BridgeEndpoint) -> bool:
        return record.network_id == endpoint.network_id and record.gateway_ip == endpoint.gateway_ip

    def _inspect(
        self,
        spec: BrokerSpec,
        endpoint: bridge_endpoint.BridgeEndpoint,
        *,
        clean_stale: bool,
        validate_identity_key: bool = True,
    ) -> tuple[str, PidRecord | None]:
        record = _load_pid_record(self._pid_path(spec), spec)
        listeners = self.backend.listeners(spec.port)
        if record is None:
            matching = self.backend.matching_pids(self._command(spec))
            if matching:
                raise LifecycleError(f"{spec.name}_orphan_process")
            if listeners:
                raise LifecycleError(f"{spec.name}_port_occupied")
            return "stopped", None
        info = self.backend.process_info(record.pid)
        if info is None:
            matching = self.backend.matching_pids(self._command(spec))
            if matching or listeners:
                raise LifecycleError(f"{spec.name}_stale_state_conflict")
            if clean_stale:
                self._pid_path(spec).unlink()
                return "stopped", None
            return "stale", record
        command = self._recorded_command(spec, record, info)
        if not self._record_matches_endpoint(record, endpoint):
            raise LifecycleError(f"{spec.name}_bridge_state_mismatch")
        if self.require_request_identity and not record.request_identity_required:
            raise LifecycleError(f"{spec.name}_request_identity_not_required")
        if (
            validate_identity_key
            and record.request_identity_required
            and self._identity_key_digest(create=False) != record.identity_key_sha256
        ):
            raise LifecycleError(f"{spec.name}_identity_key_mismatch")
        if command is None or self.backend.matching_pids(command) != (record.pid,):
            raise LifecycleError(f"{spec.name}_process_identity_mismatch")
        if not self._listener_matches(spec, record.pid, endpoint):
            raise LifecycleError(f"{spec.name}_listener_mismatch")
        _assert_safe_regular_file(self._log_path(spec), required=True)
        if not self._health(spec, endpoint):
            return "degraded", record
        return "running", record

    def _wait_ready(
        self,
        spec: BrokerSpec,
        record: PidRecord,
        endpoint: bridge_endpoint.BridgeEndpoint,
    ) -> None:
        for _ in range(self.startup_attempts):
            info = self.backend.process_info(record.pid)
            if info is None:
                raise LifecycleError(f"{spec.name}_exited_during_start")
            if not self._process_matches(spec, record, info):
                raise LifecycleError(f"{spec.name}_process_identity_mismatch")
            if self._listener_matches(spec, record.pid, endpoint) and self._health(spec, endpoint):
                return
            self.backend.sleep(POLL_SECONDS)
        raise LifecycleError(f"{spec.name}_startup_timeout")

    def _spawn_one(
        self,
        spec: BrokerSpec,
        endpoint: bridge_endpoint.BridgeEndpoint,
        *,
        identity_key_sha256: str,
    ) -> PidRecord:
        self._prepare_log(spec)
        command = self._command(spec)
        pid = self.backend.spawn(command, env=self._child_environment(spec), log_path=self._log_path(spec))
        info: ProcessInfo | None = None
        for _ in range(10):
            info = self.backend.process_info(pid)
            if info is not None:
                break
            self.backend.sleep(POLL_SECONDS)
        if info is None or info.cmdline != command or info.executable != self.python_executable:
            raise LifecycleError(f"{spec.name}_spawn_identity_invalid")
        record = PidRecord(
            broker=spec.name,
            pid=pid,
            start_ticks=info.start_ticks,
            command_digest=_command_digest(command),
            network_id=endpoint.network_id,
            gateway_ip=endpoint.gateway_ip,
            port=spec.port,
            request_identity_required=self.require_request_identity,
            identity_key_sha256=identity_key_sha256,
        )
        _write_secure_json(self._pid_path(spec), record.as_dict())
        return record

    def _stop_verified(
        self,
        spec: BrokerSpec,
        record: PidRecord,
        endpoint: bridge_endpoint.BridgeEndpoint,
        *,
        require_listener: bool = True,
    ) -> None:
        info = self.backend.process_info(record.pid)
        command = self._recorded_command(spec, record, info)
        if command is None or self.backend.matching_pids(command) != (record.pid,):
            raise LifecycleError(f"{spec.name}_process_identity_mismatch")
        listeners = self.backend.listeners(spec.port)
        expected_listener = (Listener(address=endpoint.gateway_ip, port=spec.port, pids=frozenset({record.pid})),)
        if require_listener and listeners != expected_listener:
            raise LifecycleError(f"{spec.name}_listener_mismatch")
        if not require_listener and listeners and listeners != expected_listener:
            raise LifecycleError(f"{spec.name}_listener_mismatch")

        def verify_after_pidfd_open() -> bool:
            current = self.backend.process_info(record.pid)
            if current is None:
                return False
            current_command = self._recorded_command(spec, record, current)
            if current_command is None or self.backend.matching_pids(current_command) != (record.pid,):
                raise LifecycleError(f"{spec.name}_process_identity_mismatch")
            current_listeners = self.backend.listeners(spec.port)
            if require_listener and current_listeners != expected_listener:
                raise LifecycleError(f"{spec.name}_listener_mismatch")
            if not require_listener and current_listeners and current_listeners != expected_listener:
                raise LifecycleError(f"{spec.name}_listener_mismatch")
            return True

        self.backend.terminate(record.pid, verify=verify_after_pidfd_open)
        process_running = True
        listeners: tuple[Listener, ...] = ()
        for _ in range(self.stop_attempts):
            process_running = self.backend.process_info(record.pid) is not None
            listeners = self.backend.listeners(spec.port)
            if not process_running and not listeners:
                break
            self.backend.sleep(POLL_SECONDS)
        else:
            if process_running:
                raise LifecycleError(f"{spec.name}_stop_timeout")
            raise LifecycleError(f"{spec.name}_listener_remains")
        self._pid_path(spec).unlink(missing_ok=True)

    def start(self) -> dict[str, Any]:
        self._required_environment()
        endpoint = self._discover()
        started: list[tuple[BrokerSpec, PidRecord]] = []
        with _maintenance_lock(self.project_root):
            try:
                identity_key_sha256 = self._identity_key_digest(create=True) if self.require_request_identity else ""
                states = {spec.name: self._inspect(spec, endpoint, clean_stale=True)[0] for spec in BROKER_SPECS}
                if any(state == "degraded" for state in states.values()):
                    raise LifecycleError("existing_broker_degraded")
                for spec in BROKER_SPECS:
                    if states[spec.name] == "running":
                        continue
                    record = self._spawn_one(
                        spec,
                        endpoint,
                        identity_key_sha256=identity_key_sha256,
                    )
                    started.append((spec, record))
                    self._wait_ready(spec, record, endpoint)
                confirmed = self._discover()
                if confirmed != endpoint:
                    raise LifecycleError("bridge_changed_during_start")
                _write_secure_json(self._bridge_path(), endpoint.as_dict())
            except Exception as exc:
                rollback_failed = False
                for spec, record in reversed(started):
                    try:
                        self._stop_verified(spec, record, endpoint, require_listener=False)
                    except LifecycleError:
                        rollback_failed = True
                if rollback_failed:
                    raise LifecycleError("broker_start_rollback_incomplete") from exc
                if isinstance(exc, LifecycleError):
                    raise
                raise LifecycleError("broker_start_failed") from exc
        return {
            "schema_version": SCHEMA_VERSION,
            "ok": True,
            "action": "start",
            "bridge": {"network": endpoint.network_name, "alias": endpoint.host_alias},
            "started_by_this_call": [spec.name for spec, _record in started],
            "brokers": {spec.name: {"port": spec.port, "state": "running"} for spec in BROKER_SPECS},
            "request_identity_required": self.require_request_identity,
        }

    def stop(self) -> dict[str, Any]:
        endpoint = self._discover()
        with _maintenance_lock(self.project_root):
            for spec in reversed(BROKER_SPECS):
                state, record = self._inspect(
                    spec,
                    endpoint,
                    clean_stale=True,
                    validate_identity_key=False,
                )
                if state == "stopped":
                    continue
                if record is None:
                    raise LifecycleError(f"{spec.name}_pid_state_missing")
                self._stop_verified(spec, record, endpoint)
            self._bridge_path().unlink(missing_ok=True)
        return {
            "schema_version": SCHEMA_VERSION,
            "ok": True,
            "action": "stop",
            "brokers": {spec.name: {"port": spec.port, "state": "stopped"} for spec in BROKER_SPECS},
        }

    def status(self) -> tuple[dict[str, Any], bool]:
        try:
            endpoint = self._discover()
        except LifecycleError:
            return (
                {
                    "schema_version": SCHEMA_VERSION,
                    "ok": False,
                    "action": "status",
                    "error_code": "verified_bridge_unavailable",
                },
                False,
            )
        brokers: dict[str, dict[str, Any]] = {}
        valid = True
        for spec in BROKER_SPECS:
            try:
                state, record = self._inspect(spec, endpoint, clean_stale=False)
            except LifecycleError as exc:
                state = "invalid"
                record = None
                valid = False
                error_code = str(exc)
            entry: dict[str, Any] = {"port": spec.port, "state": state}
            if record is not None:
                entry["pid"] = record.pid
                entry["request_identity_required"] = record.request_identity_required
            if state == "invalid":
                entry["error_code"] = error_code
            if state in {"degraded", "stale"}:
                valid = False
            brokers[spec.name] = entry
        return (
            {
                "schema_version": SCHEMA_VERSION,
                "ok": valid,
                "action": "status",
                "bridge": {"network": endpoint.network_name, "alias": endpoint.host_alias},
                "brokers": brokers,
            },
            valid,
        )

    def rotate_identity_key(self) -> dict[str, Any]:
        endpoint = self._discover()
        with _maintenance_lock(self.project_root):
            active = self.project_root / ACTIVE_FORMAL_RUN_RELATIVE_PATH
            if active.exists() or active.is_symlink():
                raise LifecycleError("broker_identity_rotation_active_run")
            for spec in BROKER_SPECS:
                state, _record = self._inspect(
                    spec,
                    endpoint,
                    clean_stale=True,
                    validate_identity_key=False,
                )
                if state != "stopped":
                    raise LifecycleError("broker_identity_rotation_brokers_running")
            path = self.project_root / IDENTITY_KEY_RELATIVE_PATH
            try:
                if path.exists() or path.is_symlink():
                    key = broker_request_identity.rotate_key_file(path)
                    operation = "rotated"
                else:
                    key = broker_request_identity.ensure_key_file(path)
                    operation = "created"
            except broker_request_identity.IdentityError as exc:
                raise LifecycleError("broker_identity_key_rotation_failed") from exc
        return {
            "schema_version": SCHEMA_VERSION,
            "ok": True,
            "action": "rotate-identity-key",
            "operation": operation,
            "key_sha256": hashlib.sha256(key).hexdigest(),
        }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "stop", "status", "rotate-identity-key"))
    parser.add_argument("--project-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--require-request-identity", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        environment = _startup_environment(args.project_root, os.environ) if args.action == "start" else os.environ
        lifecycle = BrokerLifecycle(
            project_root=args.project_root,
            environ=environment,
            require_request_identity=args.require_request_identity,
        )
        if args.action == "start":
            result = lifecycle.start()
            success = True
        elif args.action == "stop":
            result = lifecycle.stop()
            success = True
        elif args.action == "status":
            result, success = lifecycle.status()
        else:
            result = lifecycle.rotate_identity_key()
            success = True
    except LifecycleError as exc:
        result = {
            "schema_version": SCHEMA_VERSION,
            "ok": False,
            "action": args.action,
            "error_code": str(exc),
        }
        success = False
    print(json.dumps(result, ensure_ascii=True, separators=(",", ":"), sort_keys=True))
    return 0 if success else 2


if __name__ == "__main__":
    raise SystemExit(main())
