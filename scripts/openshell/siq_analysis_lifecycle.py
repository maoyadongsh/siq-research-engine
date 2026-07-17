#!/usr/bin/python3 -IB
"""Manage one formal, task-scoped siq_analysis OpenShell runtime."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import ipaddress
import json
import os
import pwd
import re
import secrets
import signal
import socket
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

_MODULE_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_MODULE_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_MODULE_REPO_ROOT))

from scripts.openshell import (  # noqa: E402
    broker_request_identity,
    siq_analysis_transaction as transaction,
)
from scripts.openshell.build_siq_analysis_mount_plan import (  # noqa: E402
    BUSINESS_MOUNT_COUNT,
    SANDBOX_RUNTIME_STATE_ROOT,
)
from scripts.openshell.gateway_runtime_identity import (  # noqa: E402
    GatewayRuntimeError,
    _pidfd_open,
    _pidfd_send_signal,
)
from scripts.openshell.runtime_state_lifecycle_smoke import is_passed_lifecycle_result  # noqa: E402
from scripts.openshell.security_audit import (  # noqa: E402
    SecurityRunContext,
    append_record,
    build_record,
    project_target,
)

SCHEMA_VERSION = "siq.openshell.siq_analysis_lifecycle.v1"
ACTIVE_SCHEMA_VERSION = transaction.ACTIVE_SCHEMA
PROCESS_SCHEMA_VERSION = "siq.openshell.process_identity.v1"
HOST_HERMES_RECEIPT_SCHEMA_VERSION = "siq.openshell.host_hermes_receipt.v1"
PROFILE = "siq_analysis"
GATEWAY = "siq-openshell-dev"
GATEWAY_ENDPOINT = "https://127.0.0.1:17671"
NAMESPACE = "siq-openshell-dev"
OPENSHELL_VERSION = "0.0.83"
HERMES_COMMIT = "ddb8d8fa842283ef651a6e4514f8f561f736c72e"
CHILD_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
DOCKER_HOST = "unix:///var/run/docker.sock"
SYSTEM_PYTHON = "/usr/bin/python3"
FORWARD_HOST = "127.0.0.1"
FORWARD_PORT = 28651
HOST_HERMES_PORT = 18651
SANDBOX_HERMES_PROFILE_RELATIVE = Path("data/hermes/home/profiles/siq_analysis")
SANDBOX_HERMES_AUTH_FILE = "/sandbox/runtime-auth/auth.json"
SANDBOX_PATH = (
    "/opt/siq/hermes/venv/bin:/usr/local/node/bin:/usr/local/sbin:/usr/local/bin:"
    "/usr/sbin:/usr/bin:/sbin:/bin"
)
SUPERVISOR_PATCH_SHA256 = "f38cdb0788a9c1f2a38c9aa23ab36b33c4cc6faea135bf6f04bf5eb7bbcdd12f"
LIFECYCLE_LABEL = "siq-analysis-v1"
SECURITY_PROBE_LIFECYCLE_LABEL = "siq-analysis-security-probe-v1"
WIDE_PILOT_LIFECYCLE_LABEL = "siq-analysis-wide-pilot-not-production-v1"
CANARY_LIFECYCLE_LABEL = "siq-analysis-canary-not-production-v1"
FALLBACK_FAULT_INJECTION_SCHEMA = "siq.openshell.siq_analysis_fallback_fault.v1"
FALLBACK_FAULT_INJECTION_NAME = "fallback-fault-injection.json"
FALLBACK_FAULT_RUN_ID_RE = re.compile(r"fallback-[0-9a-f]{12}\Z")
FALLBACK_FAULT_ENVIRONMENT = {
    "MINIMAX_CN_BASE_URL": "http://host.openshell.internal:8004/v1",
}
SECURITY_PROBE_SCHEMA_VERSION = "siq.openshell.provider_independent_security_probe.v1"
SECURITY_PROBE_ID_RE = re.compile(r"probe-[0-9a-f]{12}\Z")
SECURITY_PROBE_NETWORK_MODES = frozenset({"deny-all", "data-broker-only"})
REPO_ROOT = _MODULE_REPO_ROOT
STATE_RELATIVE = Path("var/openshell/siq-analysis")
RUNS_RELATIVE = STATE_RELATIVE / "runs"
ACTIVE_RELATIVE = transaction.ACTIVE_RELATIVE
RUNTIME_SNAPSHOTS_RELATIVE = STATE_RELATIVE / "runtime-snapshots"
IMAGE_STATE_RELATIVE = STATE_RELATIVE / "current-image.json"
IMAGE_SMOKE_RELATIVE = STATE_RELATIVE / "current-image.smoke.json"
POLICY_REGISTRY_RELATIVE = Path("var/openshell/registry/immutable-paths.json")
RUN_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,47}\Z")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
CONTAINER_ID_RE = re.compile(r"[0-9a-f]{12,64}\Z")
IMAGE_ID_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
NONCE_RE = re.compile(r"[0-9a-f]{48}\Z")
KEY_RE = re.compile(r"[0-9a-f]{64}\Z")
BROKER_IDENTITY_KEY_RELATIVE = Path("var/openshell/secrets/broker-request-identity.key")
BROKER_IDENTITY_TTL_SECONDS = 6 * 60 * 60
BROKER_IDENTITY_SECRET_FILES = (
    "egress.identity.token",
    "data.identity.token",
)
TRANSACTION_RESOURCE_SCHEMA = "siq.openshell.siq_analysis_resource.v1"
HOST_BASELINE_NAME = "host-hermes-baseline.json"
SERVICE_PREFLIGHT_SCHEMA = "siq.openshell.service_preflight.v2"
SERVICE_PREFLIGHT_PROTOCOL = "tcp_connect_plus_read_only_http_get"
SERVICE_PROOF_SCHEMA = "siq.openshell.service_security_proofs.v1"
SERVICE_PROOF_RELATIVE = Path("var/openshell/proofs/service-security.json")
MILVUS_PROOF_RELATIVE = Path("var/openshell/proofs/milvus-write-protection.json")
RUNTIME_CONFIG_COMPILER_SCHEMA = "siq.openshell.hermes_runtime_config.v1"
PUBLISHER_SCHEMA = "siq.openshell.company_index_publish.v1"
PUBLISHER_RELATIVE = Path("scripts/openshell/publish_company_index.py")
PUBLISHER_TIMEOUT_SECONDS = 30.0
GUARD_ACTION_LOCK_TIMEOUT_SECONDS = 2.0
GUARD_RECOVERY_LOCK_TIMEOUT_SECONDS = 30.0
MAINTENANCE_LOCK_RELATIVE = Path("var/openshell/locks/maintenance.lock")
GUARD_EVENT_SCHEMA = "siq.openshell.deletion_guard_event.v1"
GUARD_TRIGGER_NAME = "guard.trigger.json"
GUARD_OUTCOME_NAME = "guard.outcome.json"
GUARD_CLEANUP_PENDING_NAME = "guard.cleanup.pending.json"
FORMAL_SERVICE_PORTS = {
    "qwen_local": 8004,
    "gemma_local": 8006,
    "nemotron_local": 8007,
    "embedding": 8013,
    "postgres": 15432,
    "milvus": 19530,
    "siq_api": 18081,
    "hermes_host": 18651,
}
FORMAL_REQUIRED_SERVICES = frozenset(FORMAL_SERVICE_PORTS) - {
    "qwen_local",
    "gemma_local",
    "nemotron_local",
}
FORMAL_PROTOCOL_CONTRACTS = {
    "qwen_local": ("openai_models_list_v1", "/v1/models"),
    "gemma_local": ("openai_models_list_v1", "/v1/models"),
    "nemotron_local": ("openai_models_list_v1", "/v1/models"),
    "embedding": ("openai_models_list_v1", "/v1/models"),
    "siq_api": ("status_ok_json_v1", "/health"),
    "hermes_host": ("status_ok_json_v1", "/health"),
}
FORMAL_BROKER_PORTS = {"egress": 18792, "data": 18793}
MARKET_ROOTS = {
    "cn": Path("data/wiki/companies"),
    "eu": Path("data/wiki/eu/companies"),
    "hk": Path("data/wiki/hk/companies"),
    "jp": Path("data/wiki/jp/companies"),
    "kr": Path("data/wiki/kr/companies"),
    "us": Path("data/wiki/us/companies"),
}
PROVIDERS = (
    "siq-minimax-cn-pool",
    "siq-stepfun",
    "siq-kimi-coding",
    "siq-tavily-search",
)
DEFERRED_PROVIDERS = ("siq-exa-search",)
IMAGE_SMOKE_CHECKS = [
    "network_none",
    "non_root_user",
    "hermes_version_exact",
    "credential_absence",
    "runtime_state_writable",
    "runtime_metadata_materialized",
    "api_key_required",
    "hermes_auth_placeholder_persistence",
    "healthcheck",
    "runtime_lifecycle_two_rounds",
    "runtime_lifecycle_directory_bind",
]
MANIFEST_FIELDS = {
    "schema_version",
    "phase",
    "profile",
    "run_id",
    "market",
    "company",
    "analysis_relative_path",
    "sandbox_name",
    "namespace",
    "forward_host",
    "forward_port",
    "image_ref",
    "image_id",
    "runtime_snapshot",
    "mount_plan",
    "mount_plan_sha256",
    "mount_count",
    "policy",
    "policy_sha256",
    "providers",
    "api_key_sha256",
    "run_nonce_sha256",
    "sandbox_id",
    "container_id",
    "guard_process",
    "forward_process",
    "created_at",
    "updated_at",
    "error_code",
}


class LifecycleError(RuntimeError):
    """A secret-free, stable lifecycle failure."""

    def __init__(self, code: str) -> None:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,95}", code):
            code = "invalid_lifecycle_error"
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class ProcessRecord:
    schema_version: str
    role: str
    pid: int
    start_ticks: int
    executable: str
    argv_sha256: str


@dataclass(frozen=True)
class HostHermesReceipt:
    schema_version: str
    profile: str
    host: str
    port: int
    pid: int
    start_ticks: int
    executable: str
    argv_sha256: str
    state_identity_sha256: str


@dataclass(frozen=True)
class RunSpec:
    profile: str
    market: str
    company: str
    run_id: str
    project_root: Path
    analysis_root: Path
    analysis_relative_path: str
    sandbox_name: str
    run_dir: Path


@dataclass(frozen=True)
class SandboxIdentity:
    sandbox_id: str
    container_id: str


@dataclass(frozen=True)
class SecurityProbePlan:
    spec: RunSpec
    image_ref: str
    image_id: str
    runtime_snapshot: Path
    mount_plan: Path
    mount_plan_sha256: str
    policy_path: Path
    policy_sha256: str


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sha256_file(path: Path) -> str:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    digest = hashlib.sha256()
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise LifecycleError("unsafe_state_file")
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
    finally:
        os.close(descriptor)
    return digest.hexdigest()


def _safe_company(value: str) -> bool:
    return bool(
        value
        and value not in {".", ".."}
        and len(value) <= 128
        and value[0].isalnum()
        and all(character.isalnum() or character in "-_.()（）" for character in value)
    )


def _assert_no_symlink_chain(root: Path, path: Path) -> None:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise LifecycleError("path_outside_project") from exc
    current = root
    for component in relative.parts:
        current /= component
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            break
        if stat.S_ISLNK(mode):
            raise LifecycleError("symlinked_lifecycle_path")


def _mkdir_private(path: Path, *, root: Path) -> None:
    _assert_no_symlink_chain(root, path)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise LifecycleError("unsafe_state_directory")


def _require_private_file(path: Path, *, root: Path) -> os.stat_result:
    _assert_no_symlink_chain(root, path)
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise LifecycleError("required_state_missing") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise LifecycleError("unsafe_state_file")
    return info


def _read_private(path: Path, *, root: Path, max_bytes: int = 1024 * 1024) -> bytes:
    expected = _require_private_file(path, root=root)
    if expected.st_size > max_bytes:
        raise LifecycleError("state_file_too_large")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (expected.st_dev, expected.st_ino):
            raise LifecycleError("state_file_changed")
        content = b""
        while chunk := os.read(descriptor, min(64 * 1024, max_bytes + 1 - len(content))):
            content += chunk
            if len(content) > max_bytes:
                raise LifecycleError("state_file_too_large")
        finished = os.fstat(descriptor)
        if (finished.st_size, finished.st_mtime_ns, finished.st_ino) != (
            opened.st_size,
            opened.st_mtime_ns,
            opened.st_ino,
        ):
            raise LifecycleError("state_file_changed")
        return content
    finally:
        os.close(descriptor)


def _read_json(path: Path, *, root: Path, private: bool = True) -> dict[str, Any]:
    if private:
        content = _read_private(path, root=root)
    else:
        _assert_no_symlink_chain(root, path)
        if path.is_symlink() or not path.is_file():
            raise LifecycleError("required_input_missing")
        content = path.read_bytes()
    try:
        value = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LifecycleError("invalid_json_state") from exc
    if not isinstance(value, dict):
        raise LifecycleError("invalid_json_state")
    return value


def _write_private_atomic(path: Path, content: bytes, *, root: Path) -> None:
    _mkdir_private(path.parent, root=root)
    _assert_no_symlink_chain(root, path)
    if path.is_symlink():
        raise LifecycleError("symlinked_lifecycle_path")
    temporary = path.parent / f".{path.name}.{secrets.token_hex(8)}.tmp"
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise LifecycleError("short_state_write")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def _write_json(path: Path, value: Mapping[str, Any], *, root: Path) -> None:
    content = (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode()
    _write_private_atomic(path, content, root=root)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _remove_private(path: Path, *, root: Path, missing_ok: bool = True) -> None:
    _assert_no_symlink_chain(root, path)
    if path.is_symlink():
        raise LifecycleError("symlinked_lifecycle_path")
    if path.exists():
        _require_private_file(path, root=root)
        path.unlink()
    elif not missing_ok:
        raise LifecycleError("required_state_missing")


def _argv_sha256(argv: Sequence[str]) -> str:
    return _sha256_bytes(b"\0".join(os.fsencode(item) for item in argv))


def _canonical_sha256(value: Mapping[str, Any]) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return _sha256_bytes(payload)


def _host_receipt_sha256(receipt: HostHermesReceipt) -> str:
    return _canonical_sha256(asdict(receipt))


def _sandbox_entrypoint_env_arguments(project_root: Path) -> tuple[str, ...]:
    root = project_root.resolve(strict=True)
    assignments = (
        f"SIQ_PROJECT_ROOT={root}",
        f"HERMES_HOME={root / SANDBOX_HERMES_PROFILE_RELATIVE}",
        f"HERMES_RUNTIME_HOME={SANDBOX_RUNTIME_STATE_ROOT.as_posix()}",
        f"HERMES_AUTH_FILE={SANDBOX_HERMES_AUTH_FILE}",
        "API_SERVER_ENABLED=true",
        f"API_SERVER_HOST={FORWARD_HOST}",
        f"API_SERVER_PORT={FORWARD_PORT}",
        f"API_SERVER_MODEL_NAME={PROFILE}",
        "PYTHONDONTWRITEBYTECODE=1",
        "PYTHONUNBUFFERED=1",
        "PYTHONPYCACHEPREFIX=/tmp/siq-pycache",
        f"PATH={SANDBOX_PATH}",
    )
    return tuple(value for assignment in assignments for value in ("--env", assignment))


def _minimal_child_environment(project_root: Path, *, maintenance_fd: int | None = None) -> dict[str, str]:
    account = pwd.getpwuid(os.geteuid())
    state_root = project_root / "var/openshell"
    xdg_root = state_root / "xdg"
    environment = {
        "PATH": CHILD_PATH,
        "HOME": account.pw_dir,
        "USER": account.pw_name,
        "LOGNAME": account.pw_name,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TERM": "dumb",
        "NO_COLOR": "1",
        "DOCKER_HOST": DOCKER_HOST,
        "DOCKER_CONFIG": str(state_root / "docker-cli-config"),
        "SIQ_PROJECT_ROOT": str(project_root),
        "SIQ_RUNTIME_ROOT": str(project_root / "var"),
        "SIQ_ARTIFACTS_ROOT": str(project_root / "artifacts"),
        "SIQ_OPENSHELL_STATE_ROOT": str(state_root),
        "SIQ_OPENSHELL_BIN": str(state_root / "toolchains/v0.0.83/bin/openshell"),
        "XDG_CONFIG_HOME": str(xdg_root / "config"),
        "XDG_STATE_HOME": str(xdg_root / "state"),
        "XDG_DATA_HOME": str(xdg_root / "data"),
        "XDG_CACHE_HOME": str(xdg_root / "cache"),
        "OPENSHELL_LOCAL_TLS_DIR": str(xdg_root / "state/openshell/tls"),
        "OPENSHELL_SYSTEM_GATEWAY_DIR": str(xdg_root / "state/openshell/system"),
        "OPENSHELL_GATEWAY": GATEWAY,
    }
    if maintenance_fd is not None:
        environment["SIQ_OPENSHELL_MAINTENANCE_FD"] = str(maintenance_fd)
    return environment


def _proc_start_ticks(pid: int) -> int:
    content = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    fields = content[content.rfind(")") + 2 :].split()
    return int(fields[19])


def _process_snapshot(pid: int, role: str) -> ProcessRecord | None:
    proc = Path(f"/proc/{pid}")
    try:
        executable = str((proc / "exe").resolve(strict=True))
        raw_argv = (proc / "cmdline").read_bytes().rstrip(b"\0")
        argv = [os.fsdecode(item) for item in raw_argv.split(b"\0") if item]
        start_ticks = _proc_start_ticks(pid)
    except (FileNotFoundError, ProcessLookupError):
        return None
    if not argv:
        return None
    return ProcessRecord(
        schema_version=PROCESS_SCHEMA_VERSION,
        role=role,
        pid=pid,
        start_ticks=start_ticks,
        executable=executable,
        argv_sha256=_argv_sha256(argv),
    )


def _proc_argv(pid: int) -> list[str]:
    raw = Path(f"/proc/{pid}/cmdline").read_bytes().rstrip(b"\0")
    return [os.fsdecode(item) for item in raw.split(b"\0") if item]


def _proc_environment(pid: int) -> dict[bytes, bytes]:
    environment: dict[bytes, bytes] = {}
    for item in Path(f"/proc/{pid}/environ").read_bytes().split(b"\0"):
        if not item:
            continue
        key, separator, value = item.partition(b"=")
        if not separator or not key or key in environment:
            raise LifecycleError("host_hermes_receipt_invalid")
        environment[key] = value
    return environment


def _process_socket_inodes(pid: int) -> set[int]:
    inodes: set[int] = set()
    for descriptor in Path(f"/proc/{pid}/fd").iterdir():
        try:
            target = os.readlink(descriptor)
        except FileNotFoundError:
            continue
        matched = re.fullmatch(r"socket:\[(\d+)]", target)
        if matched:
            inodes.add(int(matched.group(1)))
    return inodes


def _loopback_listener_inodes(port: int) -> set[int]:
    inodes: set[int] = set()
    for source in (Path("/proc/net/tcp"), Path("/proc/net/tcp6")):
        for line in source.read_text(encoding="ascii").splitlines()[1:]:
            fields = line.split()
            if len(fields) < 10 or fields[3] != "0A":
                continue
            address, port_hex = fields[1].rsplit(":", 1)
            if int(port_hex, 16) == port:
                if source.name != "tcp" or address != "0100007F":
                    raise LifecycleError("host_hermes_listener_identity_mismatch")
                inodes.add(int(fields[9]))
    return inodes


class SystemBackend:
    """Fixed host operations; tests provide a non-mutating fake backend."""

    def __init__(self, *, project_root: Path = REPO_ROOT) -> None:
        try:
            self._project_root = project_root.resolve(strict=True)
        except OSError as exc:
            raise LifecycleError("unsafe_project_root") from exc
        if self._project_root != project_root.absolute():
            raise LifecycleError("unsafe_project_root")
        self._maintenance_fd = self._validated_maintenance_fd()

    def _validated_maintenance_fd(self) -> int | None:
        raw = os.environ.get("SIQ_OPENSHELL_MAINTENANCE_FD", "")
        if not raw:
            return None
        if not raw.isdigit():
            raise LifecycleError("invalid_maintenance_lock")
        descriptor = int(raw)
        try:
            target = Path(f"/proc/{os.getpid()}/fd/{descriptor}").resolve(strict=True)
            expected = self._project_root / "var/openshell/locks/maintenance.lock"
            _assert_no_symlink_chain(self._project_root, expected)
            expected_target = expected.resolve(strict=True)
            info = os.fstat(descriptor)
        except (FileNotFoundError, OSError) as exc:
            raise LifecycleError("invalid_maintenance_lock") from exc
        if (
            target != expected_target
            or not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o077
        ):
            raise LifecycleError("invalid_maintenance_lock")
        return descriptor

    def run(
        self,
        argv: Sequence[str],
        *,
        secrets_to_redact: Sequence[str] = (),
        timeout_seconds: float | None = None,
    ) -> CommandResult:
        pass_fds: tuple[int, ...] = ()
        if self._maintenance_fd is not None:
            pass_fds = (self._maintenance_fd,)
        try:
            completed = subprocess.run(
                list(argv),
                cwd=self._project_root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                text=True,
                encoding="utf-8",
                errors="replace",
                close_fds=True,
                pass_fds=pass_fds,
                env=_minimal_child_environment(
                    self._project_root,
                    maintenance_fd=self._maintenance_fd,
                ),
                timeout=timeout_seconds,
            )
            stdout, stderr = completed.stdout, completed.stderr
            returncode = completed.returncode
        except subprocess.TimeoutExpired:
            # Do not return partial child output; it may contain provider or
            # filesystem details.  The caller records a stable timeout code.
            stdout, stderr, returncode = "", "", 124
        for value in secrets_to_redact:
            if value:
                stdout = stdout.replace(value, "<redacted>")
                stderr = stderr.replace(value, "<redacted>")
        return CommandResult(returncode, stdout, stderr)

    def run_bounded(
        self,
        argv: Sequence[str],
        *,
        timeout_seconds: float,
        secrets_to_redact: Sequence[str] = (),
    ) -> CommandResult:
        return self.run(
            argv,
            timeout_seconds=timeout_seconds,
            secrets_to_redact=secrets_to_redact,
        )

    def maintenance_lock_held(self) -> bool:
        if self._maintenance_fd is None:
            return False
        try:
            if self._validated_maintenance_fd() != self._maintenance_fd:
                return False
            fcntl.flock(self._maintenance_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except (LifecycleError, OSError):
            return False

    def acquire_maintenance_lock(self, *, timeout_seconds: float = GUARD_RECOVERY_LOCK_TIMEOUT_SECONDS) -> None:
        """Acquire an operation-scoped lock for a background guard recovery."""

        if self.maintenance_lock_held():
            return
        if self._maintenance_fd is not None or not 0 < timeout_seconds <= 300:
            raise LifecycleError("invalid_maintenance_lock")
        path = self._project_root / MAINTENANCE_LOCK_RELATIVE
        try:
            _assert_no_symlink_chain(self._project_root, path)
            expected = path.lstat()
            descriptor = os.open(
                path,
                os.O_WRONLY | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0),
            )
        except (FileNotFoundError, OSError) as exc:
            raise LifecycleError("invalid_maintenance_lock") from exc
        try:
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_uid != os.geteuid()
                or stat.S_IMODE(opened.st_mode) & 0o077
                or (opened.st_dev, opened.st_ino) != (expected.st_dev, expected.st_ino)
            ):
                raise LifecycleError("invalid_maintenance_lock")
            deadline = time.monotonic() + timeout_seconds
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise LifecycleError("maintenance_lock_timeout") from None
                    time.sleep(0.05)
            os.environ["SIQ_OPENSHELL_MAINTENANCE_FD"] = str(descriptor)
            self._maintenance_fd = descriptor
        except Exception:
            os.close(descriptor)
            raise

    def spawn(self, argv: Sequence[str], *, log_path: Path) -> int:
        descriptor = os.open(
            log_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        # Long-running guard/forward children must not retain the operation
        # lock acquired by start. A guard obtains a fresh bounded lock only
        # when it has to fence or recover a run.
        pass_fds: tuple[int, ...] = ()
        environment = _minimal_child_environment(self._project_root)
        try:
            process = subprocess.Popen(
                list(argv),
                cwd=self._project_root,
                stdin=subprocess.DEVNULL,
                stdout=descriptor,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
                pass_fds=pass_fds,
                env=environment,
            )
        finally:
            os.close(descriptor)
        return process.pid

    def process_snapshot(self, pid: int, role: str) -> ProcessRecord | None:
        return _process_snapshot(pid, role)

    def terminate(self, record: ProcessRecord, *, timeout_seconds: float = 8.0) -> None:
        current = self.process_snapshot(record.pid, record.role)
        if current != record:
            if current is None:
                return
            raise LifecycleError(f"{record.role}_pid_identity_mismatch")
        try:
            descriptor = _pidfd_open(record.pid)
        except ProcessLookupError:
            return
        except (GatewayRuntimeError, OSError) as exc:
            raise LifecycleError(f"{record.role}_pidfd_open_failed") from exc
        try:
            current = self.process_snapshot(record.pid, record.role)
            if current != record:
                if current is None:
                    return
                raise LifecycleError(f"{record.role}_pid_identity_mismatch")
            try:
                _pidfd_send_signal(descriptor, signal.SIGTERM)
            except ProcessLookupError:
                return
            except (GatewayRuntimeError, OSError) as exc:
                raise LifecycleError(f"{record.role}_terminate_failed") from exc
        finally:
            os.close(descriptor)
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            current = self.process_snapshot(record.pid, record.role)
            if current is None or current != record:
                return
            time.sleep(0.1)
        raise LifecycleError(f"{record.role}_did_not_stop")

    def host_hermes_receipt(self, *, profile: str, host: str, port: int) -> HostHermesReceipt:
        if profile != PROFILE or host != FORWARD_HOST or port != HOST_HERMES_PORT:
            raise LifecycleError("host_hermes_receipt_invalid")
        state_path = self._project_root / "data/hermes/home/profiles" / profile / "gateway_state.json"
        try:
            state = _read_json(state_path, root=self._project_root)
            pid = state.get("pid")
            start_ticks = state.get("start_time")
            recorded_argv = state.get("argv")
            if (
                state.get("kind") != "hermes-gateway"
                or state.get("gateway_state") != "running"
                or isinstance(pid, bool)
                or not isinstance(pid, int)
                or pid <= 1
                or isinstance(start_ticks, bool)
                or not isinstance(start_ticks, int)
                or start_ticks <= 0
                or not isinstance(recorded_argv, list)
                or not recorded_argv
                or not all(isinstance(value, str) and value for value in recorded_argv)
            ):
                raise LifecycleError("host_hermes_receipt_invalid")
            expected_argv = [
                str(Path(pwd.getpwuid(os.geteuid()).pw_dir) / ".local/bin/hermes"),
                "gateway",
                "run",
                "--replace",
                "--accept-hooks",
            ]
            if recorded_argv != expected_argv:
                raise LifecycleError("host_hermes_receipt_invalid")

            process = self.process_snapshot(pid, "host_hermes")
            if process is None or process.start_ticks != start_ticks:
                raise LifecycleError("host_hermes_receipt_invalid")
            live_argv = _proc_argv(pid)
            if live_argv != recorded_argv and live_argv[1:] != recorded_argv:
                raise LifecycleError("host_hermes_receipt_invalid")
            environment = _proc_environment(pid)
            expected_home = self._project_root / "data/hermes/home/profiles" / profile
            if environment.get(b"HERMES_HOME") != os.fsencode(expected_home) or environment.get(
                b"API_SERVER_MODEL_NAME"
            ) != profile.encode("ascii"):
                raise LifecycleError("host_hermes_receipt_invalid")
            listener_inodes = _loopback_listener_inodes(port)
            if not listener_inodes or not listener_inodes.issubset(_process_socket_inodes(pid)):
                raise LifecycleError("host_hermes_listener_identity_mismatch")
            if self.process_snapshot(pid, "host_hermes") != process:
                raise LifecycleError("host_hermes_receipt_invalid")
        except LifecycleError:
            raise
        except (OSError, UnicodeError, ValueError) as exc:
            raise LifecycleError("host_hermes_receipt_invalid") from exc

        state_identity = {
            "argv": recorded_argv,
            "kind": state["kind"],
            "pid": pid,
            "start_time": start_ticks,
        }
        state_identity_sha256 = _sha256_bytes(
            json.dumps(state_identity, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()
        )
        return HostHermesReceipt(
            schema_version=HOST_HERMES_RECEIPT_SCHEMA_VERSION,
            profile=profile,
            host=host,
            port=port,
            pid=process.pid,
            start_ticks=process.start_ticks,
            executable=process.executable,
            argv_sha256=process.argv_sha256,
            state_identity_sha256=state_identity_sha256,
        )

    def port_available(self, host: str, port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind((host, port))
            except OSError:
                return False
        return True

    def port_listener_absent(self, host: str, port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            return sock.connect_ex((host, port)) != 0

    def hermes_health(self, host: str, port: int, key: str | None) -> bool:
        request = urllib.request.Request(f"http://{host}:{port}/health")
        if key is not None:
            request.add_header("Authorization", f"Bearer {key}")
        try:
            with urllib.request.urlopen(request, timeout=1) as response:
                payload = json.load(response)
                return response.status == 200 and payload.get("status") == "ok"
        except (OSError, ValueError, urllib.error.HTTPError, urllib.error.URLError):
            return False

    def hermes_rejects(self, host: str, port: int, key: str | None) -> bool:
        request = urllib.request.Request(
            f"http://{host}:{port}/v1/runs",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        if key is not None:
            request.add_header("Authorization", f"Bearer {key}")
        try:
            urllib.request.urlopen(request, timeout=1)
        except urllib.error.HTTPError as exc:
            return exc.code == 401
        except (OSError, urllib.error.URLError):
            return False
        return False


def _require_success(result: CommandResult, code: str) -> str:
    if result.returncode != 0:
        raise LifecycleError(code)
    return result.stdout


class LifecycleAdapter:
    def __init__(self, *, project_root: Path = REPO_ROOT, backend: SystemBackend | None = None) -> None:
        self.project_root = project_root.resolve(strict=True)
        if self.project_root != project_root.absolute() or self.project_root in {
            Path("/"),
            Path("/home"),
            Path("/tmp"),
            Path("/var"),
            Path.home(),
        }:
            raise LifecycleError("unsafe_project_root")
        self.backend = backend or SystemBackend(project_root=self.project_root)
        self.state_root = self.project_root / STATE_RELATIVE
        self.run_cli = self.project_root / "scripts/openshell/run_cli.sh"
        self.guard_worker = self.project_root / "scripts/openshell/siq_analysis_guard_worker.py"
        self.snapshot_builder = self.project_root / "scripts/openshell/snapshot_siq_analysis_runtime.py"
        self.mount_builder = self.project_root / "scripts/openshell/build_siq_analysis_mount_plan.py"
        self.policy_builder = self.project_root / "scripts/openshell/build_policy.py"
        self.gateway_config_builder = self.project_root / "scripts/openshell/render_gateway_config.py"
        self.service_preflight = self.project_root / "scripts/openshell/check_siq_services.py"
        self.broker_lifecycle = self.project_root / "scripts/openshell/broker_lifecycle.py"
        self.service_proof = self.project_root / SERVICE_PROOF_RELATIVE
        self.milvus_proof = self.project_root / MILVUS_PROOF_RELATIVE
        self.publisher_script = self.project_root / PUBLISHER_RELATIVE
        self.docker = Path("/usr/bin/docker")

    def require_security_probe_lock(self) -> None:
        checker = getattr(self.backend, "maintenance_lock_held", None)
        if not callable(checker) or checker() is not True:
            raise LifecycleError("security_probe_maintenance_lock_required")

    def require_formal_lock(self) -> None:
        checker = getattr(self.backend, "maintenance_lock_held", None)
        if not callable(checker) or checker() is not True:
            raise LifecycleError("formal_maintenance_lock_required")

    def spec(
        self,
        *,
        profile: str,
        market: str,
        company: str,
        run_id: str,
        require_analysis: bool = True,
    ) -> RunSpec:
        if profile != PROFILE:
            raise LifecycleError("profile_must_be_siq_analysis")
        if market not in MARKET_ROOTS:
            raise LifecycleError("unsupported_market")
        if not _safe_company(company):
            raise LifecycleError("unsafe_company")
        if not RUN_ID_RE.fullmatch(run_id):
            raise LifecycleError("unsafe_run_id")
        analysis_relative = MARKET_ROOTS[market] / company / "analysis"
        analysis_root = self.project_root / analysis_relative
        _assert_no_symlink_chain(self.project_root, analysis_root)
        if require_analysis and (not analysis_root.is_dir() or analysis_root.is_symlink()):
            raise LifecycleError("analysis_root_missing")
        run_dir = self.project_root / RUNS_RELATIVE / run_id
        return RunSpec(
            profile=profile,
            market=market,
            company=company,
            run_id=run_id,
            project_root=self.project_root,
            analysis_root=analysis_root,
            analysis_relative_path=analysis_relative.as_posix(),
            sandbox_name=f"siq-analysis-{run_id}",
            run_dir=run_dir,
        )

    def prepare_analysis_root_for_start(
        self,
        *,
        profile: str,
        market: str,
        company: str,
        run_id: str,
    ) -> RunSpec:
        """Materialize only the normal host-owned analysis write root for a new run."""

        spec = self.spec(
            profile=profile,
            market=market,
            company=company,
            run_id=run_id,
            require_analysis=False,
        )
        company_root = spec.analysis_root.parent
        _assert_no_symlink_chain(self.project_root, company_root)
        if not company_root.is_dir() or company_root.is_symlink():
            raise LifecycleError("company_root_missing")
        try:
            spec.analysis_root.mkdir(mode=0o700, exist_ok=True)
        except OSError as exc:
            raise LifecycleError("analysis_root_create_failed") from exc
        _assert_no_symlink_chain(self.project_root, spec.analysis_root)
        if not spec.analysis_root.is_dir() or spec.analysis_root.is_symlink():
            raise LifecycleError("analysis_root_invalid")
        return self.spec(
            profile=profile,
            market=market,
            company=company,
            run_id=run_id,
        )

    @staticmethod
    def _transaction_id(run_id: str) -> str:
        return f"tx-{run_id}"

    def _tx(self, operation, *args, **kwargs) -> dict[str, Any]:
        try:
            return operation(self.project_root, *args, **kwargs)
        except transaction.TransactionError as exc:
            raise LifecycleError(exc.code) from exc

    def _transaction_intent(self, spec: RunSpec) -> dict[str, str]:
        return {
            "profile": PROFILE,
            "run_id": spec.run_id,
            "market": spec.market,
            "company": spec.company,
            "run_dir": spec.run_dir.relative_to(self.project_root).as_posix(),
            "sandbox_name": spec.sandbox_name,
            "namespace": NAMESPACE,
        }

    def _resource_document(self, spec: RunSpec, resource: str, **identity: Any) -> dict[str, Any]:
        return {
            "schema_version": TRANSACTION_RESOURCE_SCHEMA,
            "resource": resource,
            "run_id": spec.run_id,
            **identity,
        }

    def _stable_host_receipt(self, *, after_stop: bool = False) -> HostHermesReceipt:
        first = self.backend.host_hermes_receipt(
            profile=PROFILE,
            host=FORWARD_HOST,
            port=HOST_HERMES_PORT,
        )
        if not self.backend.hermes_health(FORWARD_HOST, HOST_HERMES_PORT, None):
            raise LifecycleError("host_hermes_not_healthy_after_stop" if after_stop else "host_hermes_not_healthy")
        second = self.backend.host_hermes_receipt(
            profile=PROFILE,
            host=FORWARD_HOST,
            port=HOST_HERMES_PORT,
        )
        if second != first:
            raise LifecycleError(
                "host_hermes_identity_changed_after_stop" if after_stop else "host_hermes_identity_changed_before_stop"
            )
        return first

    def _read_host_baseline(self, spec: RunSpec) -> HostHermesReceipt:
        value = _read_json(spec.run_dir / HOST_BASELINE_NAME, root=self.project_root)
        if set(value) != set(HostHermesReceipt.__dataclass_fields__):
            raise LifecycleError("host_hermes_baseline_invalid")
        try:
            receipt = HostHermesReceipt(**value)
        except TypeError as exc:
            raise LifecycleError("host_hermes_baseline_invalid") from exc
        if (
            receipt.schema_version != HOST_HERMES_RECEIPT_SCHEMA_VERSION
            or receipt.profile != PROFILE
            or receipt.host != FORWARD_HOST
            or receipt.port != HOST_HERMES_PORT
            or isinstance(receipt.pid, bool)
            or not isinstance(receipt.pid, int)
            or receipt.pid <= 1
            or isinstance(receipt.start_ticks, bool)
            or not isinstance(receipt.start_ticks, int)
            or receipt.start_ticks <= 0
            or not isinstance(receipt.executable, str)
            or not receipt.executable
            or not SHA256_RE.fullmatch(str(receipt.argv_sha256))
            or not SHA256_RE.fullmatch(str(receipt.state_identity_sha256))
        ):
            raise LifecycleError("host_hermes_baseline_invalid")
        return receipt

    def _write_host_baseline(self, spec: RunSpec, receipt: HostHermesReceipt) -> None:
        path = spec.run_dir / HOST_BASELINE_NAME
        if path.exists() or path.is_symlink():
            if self._read_host_baseline(spec) != receipt:
                raise LifecycleError("host_hermes_baseline_conflict")
            return
        _write_json(path, asdict(receipt), root=self.project_root)

    def _run_dir_intent_sha(self, spec: RunSpec, baseline: HostHermesReceipt) -> str:
        return _canonical_sha256(
            self._resource_document(
                spec,
                "run_dir",
                path=spec.run_dir.relative_to(self.project_root).as_posix(),
                mode="0700",
                disposition="retain",
                host_baseline_name=HOST_BASELINE_NAME,
                host_baseline_sha256=_host_receipt_sha256(baseline),
            )
        )

    def _run_dir_receipt_sha(self, spec: RunSpec) -> str:
        _assert_no_symlink_chain(self.project_root, spec.run_dir)
        info = spec.run_dir.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise LifecycleError("run_dir_identity_mismatch")
        baseline = self._read_host_baseline(spec)
        return _canonical_sha256(
            self._resource_document(
                spec,
                "run_dir",
                path=spec.run_dir.relative_to(self.project_root).as_posix(),
                device=info.st_dev,
                inode=info.st_ino,
                uid=info.st_uid,
                mode="0700",
                host_baseline_sha256=_host_receipt_sha256(baseline),
            )
        )

    def _guard_arguments(self, spec: RunSpec, policy_sha256: str) -> list[str]:
        return [
            SYSTEM_PYTHON,
            "-I",
            "-B",
            str(self.guard_worker),
            "--project-root",
            str(self.project_root),
            "--analysis-root",
            str(spec.analysis_root),
            "--run-id",
            spec.run_id,
            "--sandbox-name",
            spec.sandbox_name,
            "--run-dir",
            str(spec.run_dir),
            "--policy-digest",
            policy_sha256,
        ]

    def _forward_arguments(
        self,
        spec: RunSpec,
        *,
        local_port: int = FORWARD_PORT,
        target_port: int = FORWARD_PORT,
    ) -> list[str]:
        return [
            str(self.run_cli),
            "forward",
            "service",
            spec.sandbox_name,
            "--target-port",
            str(target_port),
            "--local",
            f"{FORWARD_HOST}:{local_port}",
        ]

    def _guard_intent_sha(self, spec: RunSpec, policy_sha256: str) -> str:
        return _canonical_sha256(
            self._resource_document(
                spec,
                "guard",
                argv_sha256=_argv_sha256(self._guard_arguments(spec, policy_sha256)),
                policy_sha256=policy_sha256,
                worker_sha256=_sha256_file(self.guard_worker),
            )
        )

    def _forward_intent_sha(self, spec: RunSpec) -> str:
        executable = self.project_root / "var/openshell/toolchains/v0.0.83/bin/openshell"
        effective_arguments = [str(executable), *self._forward_arguments(spec)[1:]]
        return _canonical_sha256(
            self._resource_document(
                spec,
                "forward",
                argv_sha256=_argv_sha256(effective_arguments),
                executable_sha256=_sha256_file(executable),
                host=FORWARD_HOST,
                port=FORWARD_PORT,
                target_port=FORWARD_PORT,
            )
        )

    def _secrets_intent_sha(self, spec: RunSpec) -> str:
        return _canonical_sha256(
            self._resource_document(
                spec,
                "secrets",
                files=["api.key", "run.nonce", *BROKER_IDENTITY_SECRET_FILES],
                mode="0600",
                api_key_hex_length=64,
                nonce_hex_length=48,
                broker_identity_ttl_seconds=BROKER_IDENTITY_TTL_SECONDS,
                broker_identity_audiences=["siq-egress-guard", "siq-read-only-data-broker"],
            )
        )

    def _fallback_fault_injection_sha(self, spec: RunSpec) -> str:
        path = spec.run_dir / FALLBACK_FAULT_INJECTION_NAME
        if not path.exists() and not path.is_symlink():
            return ""
        payload = _read_json(path, root=self.project_root)
        expected = {
            "schema_version": FALLBACK_FAULT_INJECTION_SCHEMA,
            "kind": "primary_http_503",
            "environment": dict(FALLBACK_FAULT_ENVIRONMENT),
            "bind_scope": "verified_docker_bridge_gateway_only",
            "expected_status": 503,
        }
        if payload != expected or not FALLBACK_FAULT_RUN_ID_RE.fullmatch(spec.run_id):
            raise LifecycleError("fallback_fault_injection_invalid")
        return _sha256_file(path)

    def _sandbox_intent_sha(self, spec: RunSpec, manifest: Mapping[str, Any]) -> str:
        return _canonical_sha256(
            self._resource_document(
                spec,
                "sandbox",
                sandbox_name=spec.sandbox_name,
                image_id=str(manifest.get("image_id") or ""),
                mount_plan_sha256=str(manifest.get("mount_plan_sha256") or ""),
                policy_sha256=str(manifest.get("policy_sha256") or ""),
                api_key_sha256=str(manifest.get("api_key_sha256") or ""),
                run_nonce_sha256=str(manifest.get("run_nonce_sha256") or ""),
                fallback_fault_injection_sha256=self._fallback_fault_injection_sha(spec),
                providers=list(PROVIDERS),
                cpu=4,
                memory="8Gi",
                lifecycle=LIFECYCLE_LABEL,
            )
        )

    def _process_receipt_sha(self, spec: RunSpec, resource: str, record: ProcessRecord) -> str:
        process_path = spec.run_dir / f"{resource}.process.json"
        if resource == "guard":
            ready_path = spec.run_dir / "guard.ready.json"
            ready_sha256 = _sha256_file(ready_path)
        else:
            ready_sha256 = ""
        return _canonical_sha256(
            self._resource_document(
                spec,
                resource,
                process=asdict(record),
                process_file_sha256=_sha256_file(process_path),
                ready_file_sha256=ready_sha256,
                host=FORWARD_HOST if resource == "forward" else "",
                port=FORWARD_PORT if resource == "forward" else 0,
            )
        )

    def _secrets_receipt_sha(self, spec: RunSpec) -> str:
        key = _read_private(spec.run_dir / "api.key", root=self.project_root, max_bytes=256).decode("ascii").strip()
        nonce = _read_private(spec.run_dir / "run.nonce", root=self.project_root, max_bytes=256).decode("ascii").strip()
        egress_token = _read_private(
            spec.run_dir / "egress.identity.token",
            root=self.project_root,
            max_bytes=broker_request_identity.TOKEN_MAX_BYTES + 1,
        ).decode("ascii").strip()
        data_token = _read_private(
            spec.run_dir / "data.identity.token",
            root=self.project_root,
            max_bytes=broker_request_identity.TOKEN_MAX_BYTES + 1,
        ).decode("ascii").strip()
        if not KEY_RE.fullmatch(key) or not NONCE_RE.fullmatch(nonce):
            raise LifecycleError("secret_state_invalid")
        if (
            broker_request_identity.TOKEN_RE.fullmatch(egress_token) is None
            or broker_request_identity.TOKEN_RE.fullmatch(data_token) is None
        ):
            raise LifecycleError("broker_identity_secret_invalid")
        return _canonical_sha256(
            self._resource_document(
                spec,
                "secrets",
                api_key_sha256=_sha256_bytes(key.encode("ascii")),
                run_nonce_sha256=_sha256_bytes(nonce.encode("ascii")),
                egress_identity_sha256=_sha256_bytes(egress_token.encode("ascii")),
                data_identity_sha256=_sha256_bytes(data_token.encode("ascii")),
                mode="0600",
            )
        )

    def _sandbox_receipt_sha(self, spec: RunSpec, manifest: Mapping[str, Any]) -> str:
        sandbox_id = str(manifest.get("sandbox_id") or "")
        container_id = str(manifest.get("container_id") or "")
        if not UUID_RE.fullmatch(sandbox_id) or not container_id:
            raise LifecycleError("sandbox_receipt_invalid")
        return _canonical_sha256(
            self._resource_document(
                spec,
                "sandbox",
                sandbox_name=spec.sandbox_name,
                sandbox_id=sandbox_id,
                container_id=container_id,
                image_id=str(manifest.get("image_id") or ""),
                mount_plan_sha256=str(manifest.get("mount_plan_sha256") or ""),
                policy_sha256=str(manifest.get("policy_sha256") or ""),
                run_nonce_sha256=str(manifest.get("run_nonce_sha256") or ""),
                fallback_fault_injection_sha256=self._fallback_fault_injection_sha(spec),
                lifecycle=LIFECYCLE_LABEL,
            )
        )

    def _bind_resource(self, record: dict[str, Any], resource: str, digest: str) -> dict[str, Any]:
        current = record["resources"][resource]
        if current["intent_sha256"]:
            if current["intent_sha256"] != digest:
                raise LifecycleError(f"{resource}_intent_mismatch")
            return record
        return self._tx(
            transaction.bind_resource_intent,
            record["transaction_id"],
            expected_generation=record["generation"],
            resource=resource,
            intent_sha256=digest,
        )

    def _commit_resource(self, record: dict[str, Any], resource: str, digest: str) -> dict[str, Any]:
        current = record["resources"][resource]
        if current["state"] == "present":
            if current["receipt_sha256"] != digest:
                raise LifecycleError(f"{resource}_receipt_mismatch")
            return record
        return self._tx(
            transaction.commit_resource_present,
            record["transaction_id"],
            expected_generation=record["generation"],
            resource=resource,
            receipt_sha256=digest,
        )

    def security_probe_spec(
        self,
        *,
        profile: str,
        market: str,
        company: str,
        probe_id: str,
    ) -> RunSpec:
        if not SECURITY_PROBE_ID_RE.fullmatch(probe_id):
            raise LifecycleError("security_probe_input_invalid")
        formal = self.spec(
            profile=profile,
            market=market,
            company=company,
            run_id=probe_id,
        )
        return RunSpec(
            profile=formal.profile,
            market=formal.market,
            company=formal.company,
            run_id=formal.run_id,
            project_root=formal.project_root,
            analysis_root=formal.analysis_root,
            analysis_relative_path=formal.analysis_relative_path,
            sandbox_name=f"siq-analysis-security-{probe_id}",
            run_dir=self.project_root / STATE_RELATIVE / "security-probes" / probe_id,
        )

    def _run(
        self,
        argv: Sequence[str],
        code: str,
        *,
        secret_values: Sequence[str] = (),
        timeout_seconds: float | None = None,
    ) -> str:
        if timeout_seconds is not None:
            run_kwargs: dict[str, Any] = {
                "secrets_to_redact": secret_values,
                "timeout_seconds": timeout_seconds,
            }
            try:
                result = self.backend.run(argv, **run_kwargs)
            except TypeError as exc:
                # Test/fake backends may expose the pre-timeout contract. The
                # production SystemBackend accepts timeout_seconds, so this
                # compatibility path cannot weaken the real cleanup boundary.
                if "timeout_seconds" not in str(exc):
                    raise
                result = self.backend.run(argv, secrets_to_redact=secret_values)
        else:
            result = self.backend.run(argv, secrets_to_redact=secret_values)
        return _require_success(result, code)

    def _run_cli(
        self,
        arguments: Sequence[str],
        code: str,
        *,
        secret_values: Sequence[str] = (),
        timeout_seconds: float | None = None,
    ) -> str:
        return self._run(
            [str(self.run_cli), *arguments],
            code,
            secret_values=secret_values,
            timeout_seconds=timeout_seconds,
        )

    def _docker_run(
        self,
        arguments: Sequence[str],
        code: str,
        *,
        timeout_seconds: float | None = None,
    ) -> str:
        return self._run([str(self.docker), *arguments], code, timeout_seconds=timeout_seconds)

    def _registered_pool_sandboxes(self) -> dict[str, dict[str, str]]:
        """Return only pool identities revalidated from owner-only durable state."""

        try:
            from scripts.openshell import siq_analysis_pool_registry as pool_registry

            registry = pool_registry.load_registry(project_root=self.project_root)
            result: dict[str, dict[str, str]] = {}
            for entry in registry["bindings"]:
                route = pool_registry.resolve(
                    market=str(entry["market"]),
                    company=str(entry["company"]),
                    project_root=self.project_root,
                )
                expected_base = (
                    f"http://{pool_registry.FORWARD_HOST}:{entry['local_port']}/v1/runs"
                )
                manifest_path = self.project_root / str(entry["manifest"])
                manifest_content = _read_private(
                    manifest_path,
                    root=self.project_root,
                    max_bytes=64 * 1024,
                )
                manifest = json.loads(manifest_content)
                name = entry["sandbox_name"]
                sandbox_id = manifest.get("sandbox_id") if isinstance(manifest, dict) else None
                container_id = manifest.get("container_id") if isinstance(manifest, dict) else None
                if (
                    route.target != "openshell"
                    or route.run_id != entry["run_id"]
                    or route.base != expected_base
                    or hashlib.sha256(manifest_content).hexdigest() != entry["manifest_sha256"]
                    or manifest.get("run_id") != entry["run_id"]
                    or manifest.get("sandbox_name") != name
                    or manifest.get("lifecycle_label") != CANARY_LIFECYCLE_LABEL
                    or not isinstance(name, str)
                    or name in result
                    or not isinstance(sandbox_id, str)
                    or UUID_RE.fullmatch(sandbox_id) is None
                    or not isinstance(container_id, str)
                    or CONTAINER_ID_RE.fullmatch(container_id) is None
                ):
                    raise LifecycleError("pool_sandbox_registry_invalid")
                result[name] = {
                    "run_id": str(entry["run_id"]),
                    "sandbox_id": sandbox_id,
                    "container_id": container_id,
                }
            return result
        except LifecycleError:
            raise
        except Exception as exc:
            raise LifecycleError("pool_sandbox_registry_invalid") from exc

    def _validate_gateway_and_conflicts(
        self,
        *,
        require_providers: bool = True,
        allowed_pool_sandboxes: Mapping[str, Mapping[str, str]] | None = None,
        forward_port: int = FORWARD_PORT,
    ) -> None:
        if allowed_pool_sandboxes is None:
            allowed_pool_sandboxes = self._registered_pool_sandboxes()
        version = self._run_cli(["--version"], "openshell_cli_unavailable").strip()
        if version != f"openshell {OPENSHELL_VERSION}":
            raise LifecycleError("openshell_version_mismatch")
        status = ANSI_ESCAPE_RE.sub("", self._run_cli(["status"], "gateway_status_unavailable"))
        if not all(
            marker in status
            for marker in (
                f"Gateway: {GATEWAY}",
                f"Server: {GATEWAY_ENDPOINT}",
                "Status: Connected",
                f"Version: {OPENSHELL_VERSION}",
            )
        ):
            raise LifecycleError("gateway_version_or_connection_mismatch")
        self._run(
            [
                SYSTEM_PYTHON,
                "-I",
                "-B",
                str(self.gateway_config_builder),
                "--project-root",
                str(self.project_root),
                "--check",
            ],
            "gateway_config_drift",
        )

        record_path = self.project_root / "var/openshell/build/v0.0.83/supervisor-patch.runtime"
        content = _read_private(record_path, root=self.project_root, max_bytes=16 * 1024).decode("ascii")
        fields = dict(line.split("=", 1) for line in content.splitlines() if "=" in line)
        supervisor = self.project_root / "var/openshell/toolchains/v0.0.83/bin/openshell-sandbox"
        patch = self.project_root / "infra/openshell/patches/v0.0.83/0001-landlock-mask-file-access.patch"
        if (
            fields.get("schema") != "siq.openshell.supervisor_patch.v1"
            or fields.get("active") != "patched"
            or fields.get("version") != OPENSHELL_VERSION
            or fields.get("patch_sha256") != SUPERVISOR_PATCH_SHA256
            or fields.get("patched_binary_sha256") != _sha256_file(supervisor)
            or fields.get("active_binary_sha256") != fields.get("patched_binary_sha256")
            or _sha256_file(patch) != SUPERVISOR_PATCH_SHA256
        ):
            raise LifecycleError("supervisor_patch_mismatch")

        try:
            sandboxes = json.loads(self._run_cli(["sandbox", "list", "-o", "json"], "sandbox_inventory_failed"))
        except json.JSONDecodeError as exc:
            raise LifecycleError("sandbox_inventory_invalid") from exc
        containers_text = self._docker_run(
            [
                "ps",
                "-aq",
                "--filter",
                "label=openshell.ai/managed-by=openshell",
                "--filter",
                f"label=openshell.ai/sandbox-namespace={NAMESPACE}",
            ],
            "docker_inventory_failed",
        ).strip()
        container_ids = {item for item in containers_text.splitlines() if item}
        if not isinstance(sandboxes, list) or len(sandboxes) != len(allowed_pool_sandboxes):
            raise LifecycleError("pool_sandbox_inventory_mismatch")
        observed_names: set[str] = set()
        for item in sandboxes:
            if not isinstance(item, dict):
                raise LifecycleError("pool_sandbox_inventory_mismatch")
            name = item.get("name")
            expected = allowed_pool_sandboxes.get(name) if isinstance(name, str) else None
            labels = item.get("labels")
            if (
                expected is None
                or set(expected) != {"run_id", "sandbox_id", "container_id"}
                or name in observed_names
                or item.get("id") != expected.get("sandbox_id")
                or item.get("phase") != "Ready"
                or not isinstance(labels, dict)
                or labels.get("ai.siq.lifecycle") != CANARY_LIFECYCLE_LABEL
                or labels.get("ai.siq.profile") != PROFILE
                or labels.get("ai.siq.readiness-effect") != "none"
                or labels.get("ai.siq.run-id") != expected.get("run_id")
            ):
                raise LifecycleError("pool_sandbox_inventory_mismatch")
            observed_names.add(name)
        if observed_names != set(allowed_pool_sandboxes) or container_ids != {
            value.get("container_id") for value in allowed_pool_sandboxes.values()
        }:
            raise LifecycleError("pool_sandbox_inventory_mismatch")
        if not self.backend.port_available(FORWARD_HOST, forward_port):
            raise LifecycleError("forward_port_conflict")

        if not require_providers:
            return

        provider_names = set(self._run_cli(["provider", "list", "--names"], "provider_inventory_failed").splitlines())
        provider_manifest = _read_json(
            self.project_root / "infra/openshell/providers/manifest.json",
            root=self.project_root,
            private=False,
        )
        raw_manifest_providers = provider_manifest.get("providers")
        if not isinstance(raw_manifest_providers, list) or not all(
            isinstance(item, dict) for item in raw_manifest_providers
        ):
            raise LifecycleError("formal_provider_manifest_mismatch")
        manifest_names = [item.get("name") for item in raw_manifest_providers]
        if (
            provider_manifest.get("schema_version") != "siq.openshell.provider_manifest.v1"
            or provider_manifest.get("openshell_version") != OPENSHELL_VERSION
            or provider_manifest.get("gateway") != GATEWAY
            or manifest_names != [*PROVIDERS, *DEFERRED_PROVIDERS]
        ):
            raise LifecycleError("formal_provider_manifest_mismatch")
        if not set(PROVIDERS).issubset(provider_names):
            raise LifecycleError("formal_providers_missing")

    @staticmethod
    def _command_json(result: CommandResult, *, code: str) -> dict[str, Any]:
        if len(result.stdout.encode("utf-8", errors="replace")) > 256 * 1024:
            raise LifecycleError(code)
        try:
            payload = json.loads(result.stdout)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise LifecycleError(code) from exc
        if not isinstance(payload, dict):
            raise LifecycleError(code)
        return payload

    def _validate_service_preflight(self) -> None:
        proof = _read_json(self.service_proof, root=self.project_root)
        if (
            set(proof) != {"schema_version", "postgres_readonly_identity", "milvus_write_protection"}
            or proof.get("schema_version") != SERVICE_PROOF_SCHEMA
            or proof.get("postgres_readonly_identity") is not True
            or not isinstance(proof.get("milvus_write_protection"), bool)
        ):
            raise LifecycleError("service_security_proof_incomplete")
        try:
            _read_private(self.milvus_proof, root=self.project_root, max_bytes=1024 * 1024)
        except (LifecycleError, OSError) as exc:
            raise LifecycleError("service_security_proof_incomplete") from exc

        result = self.backend.run(
            [
                SYSTEM_PYTHON,
                "-I",
                "-B",
                str(self.service_preflight),
                "--host-alias",
                "127.0.0.1",
                "--proof-file",
                str(self.service_proof),
                "--milvus-proof-file",
                str(self.milvus_proof),
                "--json",
            ]
        )
        if result.returncode not in {0, 1}:
            raise LifecycleError("service_preflight_unavailable")
        report = self._command_json(result, code="service_preflight_invalid")
        if result.returncode == 1:
            if report.get("schema_version") != SERVICE_PREFLIGHT_SCHEMA or report.get("decision") != "NO_GO":
                raise LifecycleError("service_preflight_invalid")
            raise LifecycleError("service_preflight_no_go")

        services = report.get("services")
        checks = report.get("security_checks")
        summary = report.get("summary")
        scope = report.get("probe_scope")
        if (
            report.get("schema_version") != SERVICE_PREFLIGHT_SCHEMA
            or report.get("decision") != "GO"
            or report.get("passed") is not True
            or report.get("blockers") != []
            or not isinstance(services, list)
            or not isinstance(checks, list)
            or not isinstance(summary, dict)
            or not isinstance(scope, dict)
            or scope.get("read_only") is not True
            or scope.get("protocol") != SERVICE_PREFLIGHT_PROTOCOL
            or scope.get("host_alias_kind") != "loopback"
            or scope.get("http_method") != "GET"
            or scope.get("request_body_sent") is not False
            or scope.get("redirects_followed") is not False
            or scope.get("response_body_recorded") is not False
        ):
            raise LifecycleError("service_preflight_invalid")
        by_service = {
            item.get("service_id"): item
            for item in services
            if isinstance(item, dict) and isinstance(item.get("service_id"), str)
        }
        if set(by_service) != set(FORMAL_SERVICE_PORTS) or len(services) != len(by_service):
            raise LifecycleError("service_preflight_invalid")
        for service_id, port in FORMAL_SERVICE_PORTS.items():
            item = by_service[service_id]
            required = service_id in FORMAL_REQUIRED_SERVICES
            protocol = item.get("protocol_check")
            if (
                item.get("port") != port
                or item.get("requirement") != ("required" if required else "optional")
                or item.get("blocking") is not required
                or (required and (item.get("reachable") is not True or item.get("status") != "pass"))
                or (not required and item.get("status") not in {"pass", "warning"})
                or not isinstance(protocol, dict)
            ):
                raise LifecycleError("service_preflight_invalid")
            expected_protocol = FORMAL_PROTOCOL_CONTRACTS.get(service_id)
            if expected_protocol is None:
                if (
                    protocol.get("contract") != "not_applicable"
                    or protocol.get("checked") is not False
                    or protocol.get("available") is not None
                    or protocol.get("status") != "not_applicable"
                    or protocol.get("method") != ""
                    or protocol.get("path") != ""
                ):
                    raise LifecycleError("service_preflight_invalid")
            else:
                if (
                    protocol.get("contract") != expected_protocol[0]
                    or protocol.get("method") != "GET"
                    or protocol.get("path") != expected_protocol[1]
                    or protocol.get("checked") is not item.get("reachable")
                ):
                    raise LifecycleError("service_preflight_invalid")
                if item.get("reachable") is True:
                    if protocol.get("available") is not True or protocol.get("status") != "pass":
                        raise LifecycleError("service_preflight_invalid")
                elif protocol.get("available") is not False or protocol.get("status") != "not_run":
                    raise LifecycleError("service_preflight_invalid")
        by_check = {
            item.get("check_id"): item
            for item in checks
            if isinstance(item, dict) and isinstance(item.get("check_id"), str)
        }
        if set(by_check) != {"postgres_readonly_identity", "milvus_write_protection"} or len(checks) != len(by_check):
            raise LifecycleError("service_preflight_invalid")
        if any(
            item.get("status") != "pass"
            or item.get("proof_present") is not True
            or item.get("proof_source") != "proof_file"
            for item in by_check.values()
        ):
            raise LifecycleError("service_preflight_invalid")
        optional_protocol_count = len(
            (set(FORMAL_SERVICE_PORTS) - FORMAL_REQUIRED_SERVICES) & FORMAL_PROTOCOL_CONTRACTS.keys()
        )
        if (
            summary.get("required_total") != len(FORMAL_REQUIRED_SERVICES)
            or summary.get("required_reachable") != len(FORMAL_REQUIRED_SERVICES)
            or summary.get("required_protocol_total")
            != len(FORMAL_REQUIRED_SERVICES & FORMAL_PROTOCOL_CONTRACTS.keys())
            or summary.get("required_protocol_available")
            != len(FORMAL_REQUIRED_SERVICES & FORMAL_PROTOCOL_CONTRACTS.keys())
            or summary.get("optional_protocol_total") != optional_protocol_count
            or summary.get("optional_protocol_available") not in set(range(optional_protocol_count + 1))
            or summary.get("security_proofs_required") != 2
            or summary.get("security_proofs_present") != 2
            or summary.get("blocking_count") != 0
        ):
            raise LifecycleError("service_preflight_invalid")

        broker_result = self.backend.run(
            [
                SYSTEM_PYTHON,
                "-I",
                "-B",
                str(self.broker_lifecycle),
                "status",
                "--project-root",
                str(self.project_root),
                "--require-request-identity",
            ]
        )
        if broker_result.returncode != 0:
            raise LifecycleError("broker_preflight_no_go")
        broker_report = self._command_json(broker_result, code="broker_preflight_invalid")
        brokers = broker_report.get("brokers")
        bridge = broker_report.get("bridge")
        if (
            broker_report.get("ok") is not True
            or broker_report.get("action") != "status"
            or not isinstance(brokers, dict)
            or not isinstance(bridge, dict)
            or bridge != {"network": NAMESPACE, "alias": "host.openshell.internal"}
            or set(brokers) != set(FORMAL_BROKER_PORTS)
        ):
            raise LifecycleError("broker_preflight_invalid")
        for name, port in FORMAL_BROKER_PORTS.items():
            item = brokers[name]
            if (
                not isinstance(item, dict)
                or item.get("state") != "running"
                or item.get("port") != port
                or isinstance(item.get("pid"), bool)
                or not isinstance(item.get("pid"), int)
                or item["pid"] <= 0
                or item.get("request_identity_required") is not True
            ):
                raise LifecycleError("broker_preflight_invalid")

    def _validate_candidate_image(self) -> tuple[str, str, str]:
        candidate_path = self.project_root / IMAGE_STATE_RELATIVE
        smoke_path = self.project_root / IMAGE_SMOKE_RELATIVE
        candidate = _read_json(candidate_path, root=self.project_root)
        smoke = _read_json(smoke_path, root=self.project_root)
        image_ref = candidate.get("image_ref")
        image_id = candidate.get("image_id")
        if (
            candidate.get("schema_version") != "siq.openshell.candidate_image.v1"
            or candidate.get("architecture") != "arm64"
            or candidate.get("user") != "sandbox:sandbox"
            or not isinstance(image_ref, str)
            or not re.fullmatch(r"siq/hermes-openshell-siq-analysis:[0-9a-f]{24}", image_ref)
            or not isinstance(image_id, str)
            or not IMAGE_ID_RE.fullmatch(image_id)
            or not SHA256_RE.fullmatch(str(candidate.get("context_sha256") or ""))
            or not SHA256_RE.fullmatch(str(candidate.get("runtime_config_sha256") or ""))
            or candidate.get("hermes_commit") != HERMES_COMMIT
        ):
            raise LifecycleError("candidate_image_state_invalid")
        smoke_script = self.project_root / "scripts/openshell/smoke_siq_analysis_image.sh"
        if (
            smoke.get("schema_version") != "siq.openshell.candidate_image_smoke.v1"
            or smoke.get("status") != "passed"
            or smoke.get("profile") != PROFILE
            or smoke.get("image_ref") != image_ref
            or smoke.get("image_id") != image_id
            or smoke.get("candidate_state_sha256") != _sha256_file(candidate_path)
            or smoke.get("smoke_script_sha256") != _sha256_file(smoke_script)
            or smoke.get("checks") != IMAGE_SMOKE_CHECKS
            or smoke.get("readiness_effect") != "none"
            or not is_passed_lifecycle_result(smoke.get("runtime_lifecycle"))
        ):
            raise LifecycleError("candidate_image_smoke_missing_or_stale")

        formats = {
            "id": "{{.Id}}",
            "architecture": "{{.Architecture}}",
            "user": "{{.Config.User}}",
            "command": "{{json .Config.Cmd}}",
            "revision": '{{index .Config.Labels "org.opencontainers.image.revision"}}',
            "context": '{{index .Config.Labels "ai.siq.openshell.context-sha256"}}',
            "runtime_config": '{{index .Config.Labels "ai.siq.openshell.runtime-config-sha256"}}',
        }
        observed = {
            key: self._docker_run(["image", "inspect", image_ref, "--format", fmt], "candidate_image_missing").strip()
            for key, fmt in formats.items()
        }
        if (
            observed["id"] != image_id
            or observed["architecture"] != "arm64"
            or observed["user"] != "sandbox:sandbox"
            or observed["command"] != '["/opt/siq/entrypoint.sh"]'
            or observed["revision"] != candidate["hermes_commit"]
            or observed["context"] != candidate["context_sha256"]
            or observed["runtime_config"] != candidate["runtime_config_sha256"]
        ):
            raise LifecycleError("candidate_image_provenance_mismatch")
        return image_ref, image_id, str(candidate["runtime_config_sha256"])

    def validate_security_probe_prerequisites(
        self,
        *,
        allowed_pool_sandboxes: Mapping[str, Mapping[str, str]] | None = None,
        forward_port: int = FORWARD_PORT,
    ) -> tuple[str, str, str]:
        active_path = self.project_root / ACTIVE_RELATIVE
        if active_path.exists() or active_path.is_symlink():
            raise LifecycleError("formal_run_state_conflict")
        self._validate_gateway_and_conflicts(
            require_providers=False,
            allowed_pool_sandboxes=allowed_pool_sandboxes,
            forward_port=forward_port,
        )
        return self._validate_candidate_image()

    def _validate_compiled_runtime_snapshot(self, snapshot: Path, *, expected_sha256: str) -> None:
        manifest = _read_json(snapshot / "snapshot-manifest.json", root=self.project_root)
        inventory = manifest.get("inventory")
        config = inventory.get("config") if isinstance(inventory, dict) else None
        config_path = snapshot / "config.yaml"
        if (
            manifest.get("schema_version") != "siq.openshell.siq_analysis_runtime_snapshot.v3"
            or manifest.get("profile") != PROFILE
            or manifest.get("snapshot_mode") != "fresh"
            or manifest.get("host_runtime_records_copied") is not False
            or manifest.get("source_scope") != "current_project_siq_analysis_config_only"
            or not isinstance(config, dict)
            or config.get("compiled") is not True
            or config.get("compiler_schema_version") != RUNTIME_CONFIG_COMPILER_SCHEMA
            or config.get("compiled_sha256") != expected_sha256
            or config.get("tree_sha256") != expected_sha256
            or _sha256_file(config_path) != expected_sha256
        ):
            raise LifecycleError("runtime_config_provenance_mismatch")

    def _prepare_runtime(
        self,
        spec: RunSpec,
        *,
        expected_runtime_config_sha256: str,
    ) -> tuple[Path, Path, str, Path, str]:
        runtime_snapshot = self.project_root / RUNTIME_SNAPSHOTS_RELATIVE / spec.run_id
        snapshot_output = self._run(
            [
                SYSTEM_PYTHON,
                "-I",
                "-B",
                str(self.snapshot_builder),
                "--project-root",
                str(self.project_root),
                "--compile-config",
                "--fresh",
                "--output",
                str(runtime_snapshot),
            ],
            "runtime_snapshot_failed",
        )
        try:
            snapshot_result = json.loads(snapshot_output)
        except json.JSONDecodeError as exc:
            raise LifecycleError("runtime_snapshot_result_invalid") from exc
        snapshot_path_value = snapshot_result.get("snapshot")
        if (
            snapshot_result.get("profile") != PROFILE
            or not isinstance(snapshot_path_value, str)
            or Path(snapshot_path_value) != runtime_snapshot
            or snapshot_result.get("runtime_config_sha256") != expected_runtime_config_sha256
            or snapshot_result.get("snapshot_mode") != "fresh"
            or snapshot_result.get("host_runtime_records_copied") is not False
        ):
            raise LifecycleError("runtime_snapshot_result_invalid")
        self._validate_compiled_runtime_snapshot(
            runtime_snapshot,
            expected_sha256=expected_runtime_config_sha256,
        )

        mount_output = self._run(
            [
                SYSTEM_PYTHON,
                "-I",
                "-B",
                str(self.mount_builder),
                "--project-root",
                str(self.project_root),
                "--snapshot",
                str(runtime_snapshot),
                "--analysis-dir",
                str(spec.analysis_root),
            ],
            "mount_plan_failed",
        )
        try:
            mount_result = json.loads(mount_output)
        except json.JSONDecodeError as exc:
            raise LifecycleError("mount_plan_result_invalid") from exc
        if mount_result.get("mount_count") != BUSINESS_MOUNT_COUNT or not SHA256_RE.fullmatch(
            str(mount_result.get("sha256") or "")
        ):
            raise LifecycleError("mount_plan_contract_invalid")
        mount_plan = self.project_root / str(mount_result.get("driver_config") or "")
        mount_summary = self.project_root / str(mount_result.get("summary") or "")
        mount_document = _read_json(mount_plan, root=self.project_root)
        docker_config = mount_document.get("docker")
        mounts = docker_config.get("mounts") if isinstance(docker_config, dict) else None
        if (
            set(mount_document) != {"docker"}
            or not isinstance(mounts, list)
            or len(mounts) != BUSINESS_MOUNT_COUNT
        ):
            raise LifecycleError("mount_plan_contract_invalid")
        summary = _read_json(mount_summary, root=self.project_root)
        if (
            summary.get("profile") != PROFILE
            or summary.get("mount_count") != BUSINESS_MOUNT_COUNT
            or summary.get("analysis_relative_path") != spec.analysis_relative_path
            or summary.get("driver_config_sha256") != _sha256_file(mount_plan)
            or summary.get("driver_config_sha256") != mount_result["sha256"]
        ):
            raise LifecycleError("mount_plan_summary_mismatch")

        policy_path = spec.run_dir / "task-policy.yaml"
        policy_summary = spec.run_dir / "task-policy.summary.json"
        policy_result = self.backend.run(
            [
                SYSTEM_PYTHON,
                "-I",
                "-B",
                str(self.policy_builder),
                "--project-root",
                str(self.project_root),
                "--output",
                str(policy_path),
                "--summary-output",
                str(policy_summary),
                "--runtime-file-source",
                "candidate-image",
                "--writable-path",
                str(spec.analysis_root),
            ]
        )
        if policy_result.returncode != 0:
            if "required Hermes runtime file is missing or not regular" in policy_result.stderr:
                raise LifecycleError("policy_runtime_file_validation_blocked")
            raise LifecycleError("task_policy_compile_failed")
        policy_summary_value = _read_json(policy_summary, root=self.project_root)
        policy_sha256 = _sha256_file(policy_path)
        if (
            policy_summary_value.get("profile") != PROFILE
            or policy_summary_value.get("task_scoped_write_count") != 1
            or policy_summary_value.get("project_root") != "${SIQ_PROJECT_ROOT}"
        ):
            raise LifecycleError("task_policy_summary_invalid")
        return runtime_snapshot, mount_plan, mount_result["sha256"], policy_path, policy_sha256

    def prepare_security_probe_runtime(
        self,
        spec: RunSpec,
        *,
        network_mode: str = "deny-all",
    ) -> SecurityProbePlan:
        self.require_security_probe_lock()
        expected_run_dir = self.project_root / STATE_RELATIVE / "security-probes" / spec.run_id
        if (
            not SECURITY_PROBE_ID_RE.fullmatch(spec.run_id)
            or spec.sandbox_name != f"siq-analysis-security-{spec.run_id}"
            or spec.run_dir != expected_run_dir
            or spec.run_dir.exists()
            or spec.run_dir.is_symlink()
            or network_mode not in SECURITY_PROBE_NETWORK_MODES
        ):
            raise LifecycleError("security_probe_state_conflict")
        image_ref, image_id, runtime_config_sha256 = self.validate_security_probe_prerequisites()
        _mkdir_private(spec.run_dir, root=self.project_root)
        runtime_snapshot, mount_plan, mount_sha256, policy_path, _ = self._prepare_runtime(
            spec,
            expected_runtime_config_sha256=runtime_config_sha256,
        )
        policy = _read_json(policy_path, root=self.project_root)
        network_policies = policy.get("network_policies")
        if not isinstance(network_policies, dict) or not network_policies:
            raise LifecycleError("security_probe_policy_invalid")
        if network_mode == "deny-all":
            policy["network_policies"] = {}
        else:
            data_broker = network_policies.get("siq_data_broker")
            if not isinstance(data_broker, dict):
                raise LifecycleError("security_probe_policy_invalid")
            policy["network_policies"] = {"siq_data_broker": data_broker}
        _write_json(policy_path, policy, root=self.project_root)
        policy_sha256 = _sha256_file(policy_path)
        return SecurityProbePlan(
            spec=spec,
            image_ref=image_ref,
            image_id=image_id,
            runtime_snapshot=runtime_snapshot,
            mount_plan=mount_plan,
            mount_plan_sha256=mount_sha256,
            policy_path=policy_path,
            policy_sha256=policy_sha256,
        )

    def _manifest_base(
        self,
        spec: RunSpec,
        *,
        image_ref: str,
        image_id: str,
        runtime_snapshot: Path,
        mount_plan: Path,
        mount_sha256: str,
        policy_path: Path,
        policy_sha256: str,
    ) -> dict[str, Any]:
        now = _utc_now()
        return {
            "schema_version": SCHEMA_VERSION,
            "phase": "prepared",
            "profile": PROFILE,
            "run_id": spec.run_id,
            "market": spec.market,
            "company": spec.company,
            "analysis_relative_path": spec.analysis_relative_path,
            "sandbox_name": spec.sandbox_name,
            "namespace": NAMESPACE,
            "forward_host": FORWARD_HOST,
            "forward_port": FORWARD_PORT,
            "image_ref": image_ref,
            "image_id": image_id,
            "runtime_snapshot": runtime_snapshot.relative_to(self.project_root).as_posix(),
            "mount_plan": mount_plan.relative_to(self.project_root).as_posix(),
            "mount_plan_sha256": mount_sha256,
            "mount_count": BUSINESS_MOUNT_COUNT,
            "policy": policy_path.relative_to(self.project_root).as_posix(),
            "policy_sha256": policy_sha256,
            "providers": list(PROVIDERS),
            "api_key_sha256": "",
            "run_nonce_sha256": "",
            "sandbox_id": "",
            "container_id": "",
            "guard_process": "guard.process.json",
            "forward_process": "forward.process.json",
            "created_at": now,
            "updated_at": now,
            "error_code": "",
        }

    def _write_manifest(self, spec: RunSpec, manifest: Mapping[str, Any]) -> None:
        if set(manifest) != MANIFEST_FIELDS:
            raise LifecycleError("manifest_fields_invalid")
        _write_json(spec.run_dir / "run.json", manifest, root=self.project_root)

    def _spawn_guard(self, spec: RunSpec, policy_sha256: str) -> ProcessRecord:
        ready = spec.run_dir / "guard.ready.json"
        arguments = self._guard_arguments(spec, policy_sha256)
        pid = self.backend.spawn(arguments, log_path=spec.run_dir / "guard.log")
        record = self._wait_process_identity(pid, "guard", expected_argv=arguments)
        _write_json(spec.run_dir / "guard.process.json", asdict(record), root=self.project_root)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if ready.exists():
                value = _read_json(ready, root=self.project_root)
                if value.get("run_id") != spec.run_id or value.get("pid") != pid or value.get("status") != "ready":
                    raise LifecycleError("guard_ready_identity_mismatch")
                return record
            if self.backend.process_snapshot(pid, "guard") is None:
                raise LifecycleError("guard_failed_before_ready")
            time.sleep(0.05)
        raise LifecycleError("guard_ready_timeout")

    def _wait_process_identity(
        self,
        pid: int,
        role: str,
        *,
        expected_argv: Sequence[str] | None = None,
        expected_executable: str | None = None,
        timeout: float = 8.0,
    ) -> ProcessRecord:
        deadline = time.monotonic() + timeout
        expected_digest = _argv_sha256(expected_argv) if expected_argv is not None else None
        while time.monotonic() < deadline:
            record = self.backend.process_snapshot(pid, role)
            if record is not None:
                if expected_digest is not None and record.argv_sha256 != expected_digest:
                    time.sleep(0.05)
                    continue
                if expected_executable is not None and record.executable != expected_executable:
                    time.sleep(0.05)
                    continue
                return record
            time.sleep(0.05)
        raise LifecycleError(f"{role}_process_identity_timeout")

    def _write_secret(self, path: Path, value: str, pattern: re.Pattern[str]) -> None:
        if not pattern.fullmatch(value):
            raise LifecycleError("generated_secret_invalid")
        if path.exists() or path.is_symlink():
            raise LifecycleError("secret_state_conflict")
        _write_private_atomic(path, f"{value}\n".encode("ascii"), root=self.project_root)

    def _sandbox_inventory(
        self,
        *,
        selector: str | None = None,
        timeout_seconds: float | None = None,
    ) -> list[dict[str, Any]]:
        arguments = ["sandbox", "list", "-o", "json"]
        if selector:
            arguments.extend(["--selector", selector])
        try:
            value = json.loads(
                self._run_cli(
                    arguments,
                    "sandbox_inventory_failed",
                    timeout_seconds=timeout_seconds,
                )
            )
        except json.JSONDecodeError as exc:
            raise LifecycleError("sandbox_inventory_invalid") from exc
        if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
            raise LifecycleError("sandbox_inventory_invalid")
        return value

    def _docker_container_ids(
        self,
        sandbox_name: str,
        sandbox_id: str | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> list[str]:
        arguments = [
            "ps",
            "-aq",
            "--filter",
            "label=openshell.ai/managed-by=openshell",
            "--filter",
            f"label=openshell.ai/sandbox-namespace={NAMESPACE}",
            "--filter",
            f"label=openshell.ai/sandbox-name={sandbox_name}",
        ]
        if sandbox_id:
            arguments.extend(["--filter", f"label=openshell.ai/sandbox-id={sandbox_id}"])
        output = self._docker_run(arguments, "docker_inventory_failed", timeout_seconds=timeout_seconds)
        return [line for line in output.splitlines() if line]

    def verify_sandbox_identity(
        self,
        *,
        sandbox_name: str,
        run_id: str,
        nonce: str,
        expected_sandbox_id: str = "",
        expected_container_id: str = "",
        lifecycle_label: str = LIFECYCLE_LABEL,
        timeout_seconds: float | None = None,
    ) -> SandboxIdentity:
        if (
            not RUN_ID_RE.fullmatch(run_id)
            or not NONCE_RE.fullmatch(nonce)
            or lifecycle_label
            not in {
                LIFECYCLE_LABEL,
                SECURITY_PROBE_LIFECYCLE_LABEL,
                WIDE_PILOT_LIFECYCLE_LABEL,
                CANARY_LIFECYCLE_LABEL,
            }
        ):
            raise LifecycleError("sandbox_identity_input_invalid")
        selector = f"ai.siq.run-nonce={nonce}"
        inventory = self._sandbox_inventory(selector=selector, timeout_seconds=timeout_seconds)
        if len(inventory) != 1:
            raise LifecycleError("sandbox_gateway_identity_mismatch")
        item = inventory[0]
        labels = item.get("labels")
        sandbox_id = str(item.get("id") or "")
        if (
            item.get("name") != sandbox_name
            or not UUID_RE.fullmatch(sandbox_id)
            or not isinstance(labels, dict)
            or labels.get("ai.siq.run-nonce") != nonce
            or labels.get("ai.siq.run-id") != run_id
            or labels.get("ai.siq.profile") != PROFILE
            or labels.get("ai.siq.lifecycle") != lifecycle_label
            or (expected_sandbox_id and sandbox_id != expected_sandbox_id)
        ):
            raise LifecycleError("sandbox_gateway_identity_mismatch")
        container_ids = self._docker_container_ids(sandbox_name, sandbox_id, timeout_seconds=timeout_seconds)
        if len(container_ids) != 1 or (expected_container_id and container_ids[0] != expected_container_id):
            raise LifecycleError("sandbox_docker_identity_mismatch")
        container_id = container_ids[0]
        try:
            docker_labels = json.loads(
                self._docker_run(
                    ["inspect", container_id, "--format", "{{json .Config.Labels}}"],
                    "docker_label_inspection_failed",
                    timeout_seconds=timeout_seconds,
                )
            )
        except json.JSONDecodeError as exc:
            raise LifecycleError("sandbox_docker_labels_invalid") from exc
        required_labels = {
            "openshell.ai/managed-by": "openshell",
            "openshell.ai/sandbox-namespace": NAMESPACE,
            "openshell.ai/sandbox-name": sandbox_name,
            "openshell.ai/sandbox-id": sandbox_id,
        }
        # OpenShell resource labels remain in the gateway inventory. The Docker
        # driver exposes only its own namespace/name/id labels on the container.
        if not isinstance(docker_labels, dict) or any(
            docker_labels.get(key) != value for key, value in required_labels.items()
        ):
            raise LifecycleError("sandbox_docker_labels_invalid")
        return SandboxIdentity(sandbox_id=sandbox_id, container_id=container_id)

    def _delete_verified_sandbox(
        self,
        *,
        sandbox_name: str,
        run_id: str,
        nonce: str,
        expected_sandbox_id: str = "",
        expected_container_id: str = "",
        lifecycle_label: str = LIFECYCLE_LABEL,
    ) -> None:
        cleanup_timeout = 30.0
        inventory = self._sandbox_inventory(timeout_seconds=cleanup_timeout)
        named = [item for item in inventory if item.get("name") == sandbox_name]
        if not named:
            if self._docker_container_ids(sandbox_name, timeout_seconds=cleanup_timeout):
                raise LifecycleError("orphaned_managed_container")
            return
        self.verify_sandbox_identity(
            sandbox_name=sandbox_name,
            run_id=run_id,
            nonce=nonce,
            expected_sandbox_id=expected_sandbox_id,
            expected_container_id=expected_container_id,
            lifecycle_label=lifecycle_label,
            timeout_seconds=cleanup_timeout,
        )
        self._run_cli(
            ["sandbox", "delete", sandbox_name],
            "sandbox_delete_failed",
            timeout_seconds=30.0,
        )
        sandbox_present = True
        container_present = True
        last_inventory_error: LifecycleError | None = None
        for delay in (0.0, 0.05, 0.2, 0.5, 1.0):
            if delay:
                time.sleep(delay)
            try:
                sandbox_present = any(
                    item.get("name") == sandbox_name
                    for item in self._sandbox_inventory(timeout_seconds=cleanup_timeout)
                )
                last_inventory_error = None
            except LifecycleError as exc:
                last_inventory_error = exc
                sandbox_present = True
            container_present = bool(
                self._docker_container_ids(sandbox_name, timeout_seconds=cleanup_timeout)
            )
            if not sandbox_present and not container_present:
                return
        if last_inventory_error is not None:
            raise LifecycleError("sandbox_cleanup_verification_failed") from last_inventory_error
        if sandbox_present:
            raise LifecycleError("sandbox_remained_after_delete")
        raise LifecycleError("container_remained_after_delete")

    def create_security_probe_sandbox(
        self,
        *,
        probe_id: str,
        nonce: str,
        image_ref: str,
        mount_plan: Path,
        policy_path: Path,
        network_mode: str = "deny-all",
        data_identity_token: str = "",
    ) -> SandboxIdentity:
        self.require_security_probe_lock()
        sandbox_name = f"siq-analysis-security-{probe_id}"
        state_dir = self.project_root / STATE_RELATIVE / "security-probes" / probe_id
        expected_policy = state_dir / "task-policy.yaml"
        if (
            not SECURITY_PROBE_ID_RE.fullmatch(probe_id)
            or not NONCE_RE.fullmatch(nonce)
            or not re.fullmatch(r"siq/hermes-openshell-siq-analysis:[0-9a-f]{24}", image_ref)
            or policy_path != expected_policy
            or network_mode not in SECURITY_PROBE_NETWORK_MODES
        ):
            raise LifecycleError("security_probe_input_invalid")
        policy = _read_json(policy_path, root=self.project_root)
        mount_content = _read_private(mount_plan, root=self.project_root, max_bytes=1024 * 1024)
        intent = _read_json(state_dir / "probe.json", root=self.project_root)
        persisted_nonce = (
            _read_private(state_dir / "run.nonce", root=self.project_root, max_bytes=128).decode("ascii").strip()
        )
        network_policies = policy.get("network_policies")
        expected_network = {} if network_mode == "deny-all" else network_policies
        if network_mode == "data-broker-only":
            data_endpoints = (
                network_policies.get("siq_data_broker", {}).get("endpoints")
                if isinstance(network_policies, dict)
                else None
            )
            data_endpoint = data_endpoints[0] if isinstance(data_endpoints, list) and len(data_endpoints) == 1 else None
            try:
                allowed_gateway = ipaddress.ip_network(
                    data_endpoint.get("allowed_ips", [""])[0], strict=True
                )
            except (AttributeError, IndexError, ValueError):
                allowed_gateway = None
            if (
                not isinstance(network_policies, dict)
                or set(network_policies) != {"siq_data_broker"}
                or not isinstance(data_endpoint, dict)
                or set(data_endpoint) != {"host", "port", "allowed_ips"}
                or data_endpoint.get("host") != "host.openshell.internal"
                or data_endpoint.get("port") != 18793
                or not isinstance(allowed_gateway, ipaddress.IPv4Network)
                or allowed_gateway.prefixlen != 32
                or not allowed_gateway.is_private
                or data_endpoint.get("allowed_ips") != [allowed_gateway.with_prefixlen]
                or broker_request_identity.TOKEN_RE.fullmatch(data_identity_token) is None
            ):
                raise LifecycleError("security_probe_intent_invalid")
        elif data_identity_token:
            raise LifecycleError("security_probe_input_invalid")
        if (
            network_policies != expected_network
            or persisted_nonce != nonce
            or intent.get("schema_version") != SECURITY_PROBE_SCHEMA_VERSION
            or intent.get("phase") != "prepared"
            or intent.get("probe_id") != probe_id
            or intent.get("sandbox_name") != sandbox_name
            or intent.get("run_nonce_sha256") != _sha256_bytes(nonce.encode("ascii"))
            or intent.get("mount_plan_sha256") != _sha256_bytes(mount_content)
            or intent.get("policy_sha256") != _sha256_file(policy_path)
            or intent.get("network_mode", "deny-all") != network_mode
        ):
            raise LifecycleError("security_probe_intent_invalid")
        driver_config = mount_content.decode("utf-8").strip()
        create_arguments = [
            "sandbox",
            "create",
            "--name",
            sandbox_name,
            "--from",
            image_ref,
            "--cpu",
            "1",
            "--memory",
            "1Gi",
            "--driver-config-json",
            driver_config,
            "--policy",
            str(policy_path),
            "--label",
            f"ai.siq.run-nonce={nonce}",
            "--label",
            f"ai.siq.run-id={probe_id}",
            "--label",
            f"ai.siq.profile={PROFILE}",
            "--label",
            f"ai.siq.lifecycle={SECURITY_PROBE_LIFECYCLE_LABEL}",
            "--no-auto-providers",
            "--no-tty",
            "--",
            "/bin/sh",
            "-c",
            "nohup setsid /bin/sleep 300 >/tmp/siq-security-probe.log 2>&1 </dev/null &",
        ]
        if network_mode == "data-broker-only":
            insertion = create_arguments.index("--no-auto-providers")
            create_arguments[insertion:insertion] = [
                "--env",
                f"{broker_request_identity.DATA_TOKEN_ENV}={data_identity_token}",
                "--env",
                "NO_PROXY=127.0.0.1,localhost,::1",
                "--env",
                "no_proxy=127.0.0.1,localhost,::1",
            ]
        secret_values = (nonce, data_identity_token) if data_identity_token else (nonce,)
        self._run_cli(create_arguments, "security_probe_sandbox_create_failed", secret_values=secret_values)
        return self.verify_sandbox_identity(
            sandbox_name=sandbox_name,
            run_id=probe_id,
            nonce=nonce,
            lifecycle_label=SECURITY_PROBE_LIFECYCLE_LABEL,
        )

    def delete_security_probe_sandbox(
        self,
        *,
        probe_id: str,
        nonce: str,
        expected_sandbox_id: str = "",
        expected_container_id: str = "",
    ) -> None:
        self.require_security_probe_lock()
        if not SECURITY_PROBE_ID_RE.fullmatch(probe_id):
            raise LifecycleError("security_probe_input_invalid")
        self._delete_verified_sandbox(
            sandbox_name=f"siq-analysis-security-{probe_id}",
            run_id=probe_id,
            nonce=nonce,
            expected_sandbox_id=expected_sandbox_id,
            expected_container_id=expected_container_id,
            lifecycle_label=SECURITY_PROBE_LIFECYCLE_LABEL,
        )

    def recover_security_probe_sandbox(
        self,
        *,
        probe_id: str,
        nonce: str,
        expected_sandbox_id: str = "",
        expected_container_id: str = "",
    ) -> None:
        self.require_security_probe_lock()
        if not SECURITY_PROBE_ID_RE.fullmatch(probe_id) or not NONCE_RE.fullmatch(nonce):
            raise LifecycleError("security_probe_input_invalid")
        sandbox_name = f"siq-analysis-security-{probe_id}"
        named = [item for item in self._sandbox_inventory() if item.get("name") == sandbox_name]
        if named:
            self._delete_verified_sandbox(
                sandbox_name=sandbox_name,
                run_id=probe_id,
                nonce=nonce,
                expected_sandbox_id=expected_sandbox_id,
                expected_container_id=expected_container_id,
                lifecycle_label=SECURITY_PROBE_LIFECYCLE_LABEL,
            )
            return

        container_ids = self._docker_container_ids(sandbox_name)
        if not container_ids:
            return
        if (
            not expected_sandbox_id
            or not expected_container_id
            or len(container_ids) != 1
            or container_ids[0] != expected_container_id
        ):
            raise LifecycleError("security_probe_orphan_identity_failed")
        container_id = container_ids[0]
        try:
            labels = json.loads(
                self._docker_run(
                    ["inspect", container_id, "--format", "{{json .Config.Labels}}"],
                    "security_probe_orphan_inspection_failed",
                )
            )
        except json.JSONDecodeError as exc:
            raise LifecycleError("security_probe_orphan_identity_failed") from exc
        sandbox_id = str(labels.get("openshell.ai/sandbox-id") or "") if isinstance(labels, dict) else ""
        required = {
            "openshell.ai/managed-by": "openshell",
            "openshell.ai/sandbox-namespace": NAMESPACE,
            "openshell.ai/sandbox-name": sandbox_name,
        }
        if (
            not isinstance(labels, dict)
            or not UUID_RE.fullmatch(sandbox_id)
            or labels.get("openshell.ai/sandbox-id") != sandbox_id
            or (expected_sandbox_id and sandbox_id != expected_sandbox_id)
            or any(labels.get(key) != value for key, value in required.items())
        ):
            raise LifecycleError("security_probe_orphan_identity_failed")
        self._docker_run(["rm", "-f", container_id], "security_probe_orphan_cleanup_failed")
        if self._docker_container_ids(sandbox_name) or any(
            item.get("name") == sandbox_name for item in self._sandbox_inventory()
        ):
            raise LifecycleError("security_probe_orphan_cleanup_failed")

    def _spawn_forward(
        self,
        spec: RunSpec,
        *,
        local_port: int = FORWARD_PORT,
        target_port: int = FORWARD_PORT,
    ) -> ProcessRecord:
        arguments = self._forward_arguments(spec, local_port=local_port, target_port=target_port)
        pid = self.backend.spawn(arguments, log_path=spec.run_dir / "forward.log")
        record = self._wait_process_identity(
            pid,
            "forward",
            expected_argv=[
                str(self.project_root / "var/openshell/toolchains/v0.0.83/bin/openshell"),
                "forward",
                "service",
                spec.sandbox_name,
                "--target-port",
                str(target_port),
                "--local",
                f"{FORWARD_HOST}:{local_port}",
            ],
            expected_executable=str(
                (self.project_root / "var/openshell/toolchains/v0.0.83/bin/openshell").resolve(strict=True)
            ),
        )
        _write_json(spec.run_dir / "forward.process.json", asdict(record), root=self.project_root)
        return record

    def _wait_health(
        self,
        key: str,
        forward_record: ProcessRecord,
        *,
        local_port: int = FORWARD_PORT,
    ) -> None:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            current = self.backend.process_snapshot(forward_record.pid, "forward")
            if current != forward_record:
                raise LifecycleError("forward_process_exited")
            if self.backend.hermes_health(FORWARD_HOST, local_port, key):
                if not self.backend.hermes_rejects(FORWARD_HOST, local_port, None):
                    raise LifecycleError("hermes_missing_key_not_rejected")
                if not self.backend.hermes_rejects(FORWARD_HOST, local_port, "0" * 64):
                    raise LifecycleError("hermes_wrong_key_not_rejected")
                return
            time.sleep(0.25)
        raise LifecycleError("hermes_health_timeout")

    def _audit(self, *, run_id: str, sandbox_name: str, policy_digest: str, code: str) -> None:
        digest = policy_digest if SHA256_RE.fullmatch(policy_digest) else "0" * 64
        record = build_record(
            context=SecurityRunContext(
                profile=PROFILE,
                sandbox_id=sandbox_name,
                run_id=run_id,
                session_id=run_id,
                policy_digest=digest,
            ),
            operation_class="sandbox.lifecycle",
            target=project_target(kind="process", scope="siq_analysis_runtime", value=sandbox_name),
            decision="deny",
            error_code=code,
            duration_ms=0,
        )
        append_record(project_root=self.project_root, record=record)

    def _audit_publisher(
        self,
        *,
        spec: RunSpec,
        policy_digest: str,
        decision: str,
        error_code: str,
    ) -> None:
        digest = policy_digest if SHA256_RE.fullmatch(policy_digest) else "0" * 64
        record = build_record(
            context=SecurityRunContext(
                profile=PROFILE,
                sandbox_id=spec.sandbox_name,
                run_id=spec.run_id,
                session_id=spec.run_id,
                policy_digest=digest,
            ),
            operation_class="publisher.index",
            target=project_target(kind="path", scope="company_index", value=f"{spec.market}:{spec.company}"),
            decision=decision,
            error_code=error_code,
            duration_ms=0,
        )
        append_record(project_root=self.project_root, record=record)

    def _publish_company_index(self, spec: RunSpec, *, policy_digest: str) -> dict[str, str]:
        """Publish only the known company index after sandbox resources are gone."""

        def audit(decision: str, error_code: str) -> bool:
            try:
                self._audit_publisher(
                    spec=spec,
                    policy_digest=policy_digest,
                    decision=decision,
                    error_code=error_code,
                )
                return True
            except Exception:
                return False

        if self.publisher_script.is_symlink() or not self.publisher_script.is_file():
            code = "publisher_script_missing"
            audit_ok = audit("audit_only", code)
            result = {"status": "deferred", "error_code": code}
            if not audit_ok:
                result["audit"] = "deferred"
            return result
        try:
            arguments = [
                SYSTEM_PYTHON,
                "-I",
                "-B",
                str(self.publisher_script),
                "--project-root",
                str(self.project_root),
                "--market",
                spec.market,
                "--company-id",
                spec.company,
            ]
            bounded_runner = getattr(self.backend, "run_bounded", None)
            if callable(bounded_runner):
                result = bounded_runner(arguments, timeout_seconds=PUBLISHER_TIMEOUT_SECONDS)
            else:
                # Test/fake backends may only expose the original contract;
                # production SystemBackend always provides run_bounded.
                result = self.backend.run(arguments)
            if result.returncode == 124:
                raise LifecycleError("company_index_publish_timeout")
            if result.returncode != 0:
                raise LifecycleError("company_index_publish_failed")
            payload = self._command_json(result, code="company_index_publish_invalid")
            expected_projection = hashlib.sha256(f"{spec.market}:{spec.company}".encode()).hexdigest()[:24]
            if (
                payload.get("schema_version") != PUBLISHER_SCHEMA
                or payload.get("ok") is not True
                or payload.get("market") != spec.market
                or payload.get("company_projection") != expected_projection
                or payload.get("index_schema_version") != 1
            ):
                raise LifecycleError("company_index_publish_invalid")
        except LifecycleError as exc:
            audit_ok = audit("audit_only", exc.code)
            result = {"status": "deferred", "error_code": exc.code}
            if not audit_ok:
                result["audit"] = "deferred"
            return result
        except Exception:
            code = "company_index_publish_failed"
            audit_ok = audit("audit_only", code)
            result = {"status": "deferred", "error_code": code}
            if not audit_ok:
                result["audit"] = "deferred"
            return result
        audit_ok = audit("allow", "")
        result = {"status": "published"}
        if not audit_ok:
            result["audit"] = "deferred"
            result["error_code"] = "publisher_audit_deferred"
        return result

    def start(
        self,
        spec: RunSpec,
        *,
        sandbox_environment_overrides: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        self.require_formal_lock()
        if sandbox_environment_overrides is None:
            fault_environment: dict[str, str] = {}
        elif (
            not isinstance(sandbox_environment_overrides, Mapping)
            or dict(sandbox_environment_overrides) != FALLBACK_FAULT_ENVIRONMENT
            or not FALLBACK_FAULT_RUN_ID_RE.fullmatch(spec.run_id)
        ):
            raise LifecycleError("fallback_fault_injection_invalid")
        else:
            fault_environment = dict(FALLBACK_FAULT_ENVIRONMENT)
        policy_digest = ""
        manifest: dict[str, Any] | None = None
        guard_record: ProcessRecord | None = None
        forward_record: ProcessRecord | None = None
        transaction_record: dict[str, Any] | None = None
        baseline: HostHermesReceipt | None = None
        if spec.run_dir.exists() or spec.run_dir.is_symlink():
            raise LifecycleError("formal_run_state_conflict")
        try:
            # Preflight is read-only; transaction state is created only after it passes.
            self._validate_gateway_and_conflicts()
            self._validate_service_preflight()
            image_ref, image_id, runtime_config_sha256 = self._validate_candidate_image()
            baseline = self._stable_host_receipt()
            transaction_record = self._tx(
                transaction.create,
                transaction_id=self._transaction_id(spec.run_id),
                intent=self._transaction_intent(spec),
                resources=dict(transaction.FORMAL_RESOURCES),
            )
            transaction_record = self._tx(
                transaction.transition,
                transaction_record["transaction_id"],
                expected_generation=transaction_record["generation"],
                phase="starting",
            )
            transaction_record = self._bind_resource(
                transaction_record,
                "run_dir",
                self._run_dir_intent_sha(spec, baseline),
            )
            _mkdir_private(self.project_root / RUNS_RELATIVE, root=self.project_root)
            _mkdir_private(spec.run_dir, root=self.project_root)
            self._write_host_baseline(spec, baseline)
            if fault_environment:
                _write_json(
                    spec.run_dir / FALLBACK_FAULT_INJECTION_NAME,
                    {
                        "schema_version": FALLBACK_FAULT_INJECTION_SCHEMA,
                        "kind": "primary_http_503",
                        "environment": fault_environment,
                        "bind_scope": "verified_docker_bridge_gateway_only",
                        "expected_status": 503,
                    },
                    root=self.project_root,
                )
            _fsync_directory(spec.run_dir)
            _fsync_directory(spec.run_dir.parent)
            transaction_record = self._commit_resource(
                transaction_record,
                "run_dir",
                self._run_dir_receipt_sha(spec),
            )
            runtime_snapshot, mount_plan, mount_sha256, policy_path, policy_digest = self._prepare_runtime(
                spec,
                expected_runtime_config_sha256=runtime_config_sha256,
            )
            manifest = self._manifest_base(
                spec,
                image_ref=image_ref,
                image_id=image_id,
                runtime_snapshot=runtime_snapshot,
                mount_plan=mount_plan,
                mount_sha256=mount_sha256,
                policy_path=policy_path,
                policy_sha256=policy_digest,
            )
            self._write_manifest(spec, manifest)

            transaction_record = self._bind_resource(
                transaction_record,
                "guard",
                self._guard_intent_sha(spec, policy_digest),
            )
            guard_record = self._spawn_guard(spec, policy_digest)
            transaction_record = self._commit_resource(
                transaction_record,
                "guard",
                self._process_receipt_sha(spec, "guard", guard_record),
            )

            transaction_record = self._bind_resource(
                transaction_record,
                "secrets",
                self._secrets_intent_sha(spec),
            )
            key = secrets.token_hex(32)
            nonce = secrets.token_hex(24)
            self._write_secret(spec.run_dir / "api.key", key, KEY_RE)
            self._write_secret(spec.run_dir / "run.nonce", nonce, NONCE_RE)
            try:
                broker_identity_key = broker_request_identity.read_key_file(
                    self.project_root / BROKER_IDENTITY_KEY_RELATIVE
                )
                identity_bundle = broker_request_identity.issue_broker_identities(
                    broker_identity_key,
                    profile=PROFILE,
                    run_id=spec.run_id,
                    sandbox_id=spec.sandbox_name,
                    session_id=spec.run_id,
                    policy_digest=policy_digest,
                    run_nonce_digest=_sha256_bytes(nonce.encode("ascii")),
                    ttl_seconds=BROKER_IDENTITY_TTL_SECONDS,
                )
            except broker_request_identity.IdentityError as exc:
                raise LifecycleError("broker_identity_issue_failed") from exc
            egress_identity_token = identity_bundle.egress_token
            data_identity_token = identity_bundle.data_token
            self._write_secret(
                spec.run_dir / "egress.identity.token",
                egress_identity_token,
                broker_request_identity.TOKEN_RE,
            )
            self._write_secret(
                spec.run_dir / "data.identity.token",
                data_identity_token,
                broker_request_identity.TOKEN_RE,
            )
            manifest.update(
                {
                    "api_key_sha256": _sha256_bytes(key.encode()),
                    "run_nonce_sha256": _sha256_bytes(nonce.encode()),
                    "updated_at": _utc_now(),
                }
            )
            self._write_manifest(spec, manifest)
            transaction_record = self._commit_resource(
                transaction_record,
                "secrets",
                self._secrets_receipt_sha(spec),
            )

            driver_config = (
                _read_private(mount_plan, root=self.project_root, max_bytes=1024 * 1024).decode("utf-8").strip()
            )
            create_arguments = [
                "sandbox",
                "create",
                "--name",
                spec.sandbox_name,
                "--from",
                image_ref,
                "--cpu",
                "4",
                "--memory",
                "8Gi",
                "--driver-config-json",
                driver_config,
                "--policy",
                str(policy_path),
                "--label",
                f"ai.siq.run-nonce={nonce}",
                "--label",
                f"ai.siq.run-id={spec.run_id}",
                "--label",
                f"ai.siq.profile={PROFILE}",
                "--label",
                f"ai.siq.lifecycle={LIFECYCLE_LABEL}",
                *_sandbox_entrypoint_env_arguments(self.project_root),
                "--env",
                f"API_SERVER_KEY={key}",
                "--env",
                f"SIQ_RUN_ID={spec.run_id}",
                "--env",
                "SIQ_PG_QUERY_BROKER_URL=http://host.openshell.internal:18793",
                "--env",
                "SIQ_REQUIRE_OPENSHELL_PROVIDERS=1",
                "--env",
                "SIQ_OPENSHELL_SANDBOX=1",
                "--env",
                f"{broker_request_identity.EGRESS_TOKEN_ENV}={egress_identity_token}",
                "--env",
                f"{broker_request_identity.DATA_TOKEN_ENV}={data_identity_token}",
                "--env",
                "NO_PROXY=127.0.0.1,localhost,::1",
                "--env",
                "no_proxy=127.0.0.1,localhost,::1",
            ]
            for name, value in fault_environment.items():
                create_arguments.extend(["--env", f"{name}={value}"])
            for provider in PROVIDERS:
                create_arguments.extend(["--provider", provider])
            create_arguments.extend(
                [
                    "--no-auto-providers",
                    "--no-tty",
                    "--",
                    "/bin/sh",
                    "-c",
                    "nohup setsid /opt/siq/entrypoint.sh >/tmp/siq-entrypoint.log 2>&1 </dev/null &",
                ]
            )
            transaction_record = self._bind_resource(
                transaction_record,
                "sandbox",
                self._sandbox_intent_sha(spec, manifest),
            )
            self._run_cli(
                create_arguments,
                "sandbox_create_failed",
                secret_values=(key, nonce, *identity_bundle.secret_values()),
            )
            identity = self.verify_sandbox_identity(
                sandbox_name=spec.sandbox_name,
                run_id=spec.run_id,
                nonce=nonce,
            )
            manifest.update(
                {
                    "phase": "sandbox_created",
                    "sandbox_id": identity.sandbox_id,
                    "container_id": identity.container_id,
                    "updated_at": _utc_now(),
                }
            )
            self._write_manifest(spec, manifest)
            transaction_record = self._commit_resource(
                transaction_record,
                "sandbox",
                self._sandbox_receipt_sha(spec, manifest),
            )

            transaction_record = self._bind_resource(
                transaction_record,
                "forward",
                self._forward_intent_sha(spec),
            )
            forward_record = self._spawn_forward(spec)
            self._wait_health(key, forward_record)
            transaction_record = self._commit_resource(
                transaction_record,
                "forward",
                self._process_receipt_sha(spec, "forward", forward_record),
            )
            manifest.update({"phase": "running", "updated_at": _utc_now()})
            self._write_manifest(spec, manifest)
            transaction_record = self._tx(
                transaction.transition,
                transaction_record["transaction_id"],
                expected_generation=transaction_record["generation"],
                phase="running",
            )
            return {
                "ok": True,
                "profile": PROFILE,
                "run_id": spec.run_id,
                "sandbox_name": spec.sandbox_name,
                "runs_url": f"http://{FORWARD_HOST}:{FORWARD_PORT}/v1/runs",
                "mount_count": BUSINESS_MOUNT_COUNT,
                "providers": list(PROVIDERS),
                "transaction_id": transaction_record["transaction_id"],
            }
        except Exception as exc:
            code = exc.code if isinstance(exc, LifecycleError) else "lifecycle_os_error"
            cleanup_error = ""
            if transaction_record is not None:
                cleanup_error = self._rollback_failed_start(
                    spec,
                    transaction_id=transaction_record["transaction_id"],
                    baseline=baseline,
                    manifest=manifest,
                    guard_record=guard_record,
                    forward_record=forward_record,
                    error_code=code,
                )
            try:
                self._audit(
                    run_id=spec.run_id,
                    sandbox_name=spec.sandbox_name,
                    policy_digest=policy_digest,
                    code=cleanup_error or code,
                )
            except Exception:
                pass
            raise LifecycleError(cleanup_error or code) from exc

    def _rollback_failed_start(
        self,
        spec: RunSpec,
        *,
        transaction_id: str,
        baseline: HostHermesReceipt | None,
        manifest: dict[str, Any] | None,
        guard_record: ProcessRecord | None,
        forward_record: ProcessRecord | None,
        error_code: str,
    ) -> str:
        try:
            record = self._tx(transaction.load, transaction_id)
            if record["phase"] == "running":
                return "start_committed_recovery_required"
            if record["phase"] == "intent":
                record = self._tx(
                    transaction.transition,
                    transaction_id,
                    expected_generation=record["generation"],
                    phase="starting",
                )
            if record["phase"] == "starting":
                record = self._reconcile_run_dir(record, spec, baseline=baseline)
                if not record["terminal_action"]:
                    record = self._tx(
                        transaction.set_terminal_action,
                        transaction_id,
                        expected_generation=record["generation"],
                        action="failed_start",
                    )
                record = self._tx(
                    transaction.transition,
                    transaction_id,
                    expected_generation=record["generation"],
                    phase="rollback_pending",
                    error_code=error_code,
                )
            if record["phase"] != "rollback_pending":
                return "transaction_recovery_required"
            record = self._cleanup_resources(
                record,
                spec,
                manifest=manifest,
                guard_record=guard_record,
                forward_record=forward_record,
            )
            if manifest is not None:
                manifest.update(
                    {
                        "phase": "failed_rolled_back",
                        "updated_at": _utc_now(),
                        "error_code": error_code,
                    }
                )
                self._write_manifest(spec, manifest)
            record = self._tx(
                transaction.transition,
                transaction_id,
                expected_generation=record["generation"],
                phase="rolled_back",
            )
            self._tx(transaction.finalize, transaction_id)
            return ""
        except LifecycleError as exc:
            if manifest is not None:
                try:
                    manifest.update({"phase": "rollback_incomplete", "updated_at": _utc_now(), "error_code": exc.code})
                    self._write_manifest(spec, manifest)
                except LifecycleError:
                    pass
            return exc.code

    def _reconcile_run_dir(
        self,
        record: dict[str, Any],
        spec: RunSpec,
        *,
        baseline: HostHermesReceipt | None,
    ) -> dict[str, Any]:
        resource = record["resources"]["run_dir"]
        if resource["state"] == "present":
            if self._run_dir_receipt_sha(spec) != resource["receipt_sha256"]:
                raise LifecycleError("run_dir_receipt_mismatch")
            return record
        if resource["state"] != "pending":
            raise LifecycleError("run_dir_resource_state_invalid")
        if baseline is None:
            baseline = self._read_host_baseline(spec) if (spec.run_dir / HOST_BASELINE_NAME).exists() else None
            if baseline is None:
                baseline = self._stable_host_receipt()
        record = self._bind_resource(record, "run_dir", self._run_dir_intent_sha(spec, baseline))
        if spec.run_dir.is_symlink() or (spec.run_dir.exists() and not spec.run_dir.is_dir()):
            raise LifecycleError("run_dir_identity_mismatch")
        _mkdir_private(self.project_root / RUNS_RELATIVE, root=self.project_root)
        _mkdir_private(spec.run_dir, root=self.project_root)
        self._write_host_baseline(spec, baseline)
        _fsync_directory(spec.run_dir)
        _fsync_directory(spec.run_dir.parent)
        return self._commit_resource(record, "run_dir", self._run_dir_receipt_sha(spec))

    def _resource_removing(self, record: dict[str, Any], resource: str) -> dict[str, Any]:
        state = record["resources"][resource]["state"]
        if state in {"pending", "present"}:
            return self._tx(
                transaction.update_resource,
                record["transaction_id"],
                expected_generation=record["generation"],
                resource=resource,
                state="removing",
            )
        if state in {"removing", "removed"}:
            return record
        raise LifecycleError(f"{resource}_resource_state_invalid")

    def _resource_removed(self, record: dict[str, Any], resource: str) -> dict[str, Any]:
        if record["resources"][resource]["state"] == "removed":
            return record
        if record["resources"][resource]["state"] != "removing":
            raise LifecycleError(f"{resource}_resource_state_invalid")
        return self._tx(
            transaction.update_resource,
            record["transaction_id"],
            expected_generation=record["generation"],
            resource=resource,
            state="removed",
        )

    def _process_record_for_cleanup(
        self,
        record: dict[str, Any],
        spec: RunSpec,
        resource: str,
        override: ProcessRecord | None,
        manifest: Mapping[str, Any] | None,
    ) -> ProcessRecord | None:
        state = record["resources"][resource]
        process_path = spec.run_dir / f"{resource}.process.json"
        if not process_path.exists() and override is None:
            if state["state"] == "pending" and not state["intent_sha256"]:
                return None
            raise LifecycleError(f"{resource}_unreceipted_identity_uncertain")
        process = override or self._read_process(spec, process_path.name, resource)
        if process.role != resource:
            raise LifecycleError(f"{resource}_process_identity_mismatch")
        if resource == "guard":
            if manifest is None:
                raise LifecycleError("guard_unreceipted_identity_uncertain")
            expected_argv_sha256 = _argv_sha256(self._guard_arguments(spec, str(manifest.get("policy_sha256") or "")))
        else:
            executable = self.project_root / "var/openshell/toolchains/v0.0.83/bin/openshell"
            expected_argv_sha256 = _argv_sha256([str(executable), *self._forward_arguments(spec)[1:]])
        if process.argv_sha256 != expected_argv_sha256:
            raise LifecycleError(f"{resource}_process_identity_mismatch")
        if process_path.exists() and state["receipt_sha256"]:
            if self._process_receipt_sha(spec, resource, process) != state["receipt_sha256"]:
                raise LifecycleError(f"{resource}_receipt_mismatch")
        current = self.backend.process_snapshot(process.pid, resource)
        if current is not None and current != process:
            raise LifecycleError(f"{resource}_pid_identity_mismatch")
        return process

    def _cleanup_process_resource(
        self,
        record: dict[str, Any],
        spec: RunSpec,
        resource: str,
        *,
        override: ProcessRecord | None = None,
        manifest: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        state = record["resources"][resource]
        if state["state"] == "removed":
            process_path = spec.run_dir / f"{resource}.process.json"
            if state["receipt_sha256"]:
                process = self._read_process(spec, process_path.name, resource)
                if self._process_receipt_sha(spec, resource, process) != state["receipt_sha256"]:
                    raise LifecycleError(f"{resource}_receipt_mismatch")
                if self.backend.process_snapshot(process.pid, resource) == process:
                    raise LifecycleError(f"{resource}_removed_resource_reappeared")
            elif process_path.exists():
                raise LifecycleError(f"{resource}_unreceipted_identity_uncertain")
            if resource == "forward" and not self.backend.port_listener_absent(FORWARD_HOST, FORWARD_PORT):
                raise LifecycleError("forward_port_remained_after_stop")
            return record
        process = self._process_record_for_cleanup(record, spec, resource, override, manifest)
        if process is None:
            record = self._resource_removing(record, resource)
            return self._resource_removed(record, resource)
        record = self._resource_removing(record, resource)
        if self.backend.process_snapshot(process.pid, resource) == process:
            self.backend.terminate(process)
        if self.backend.process_snapshot(process.pid, resource) == process:
            raise LifecycleError(f"{resource}_did_not_stop")
        if resource == "forward" and not self.backend.port_listener_absent(FORWARD_HOST, FORWARD_PORT):
            raise LifecycleError("forward_port_remained_after_stop")
        return self._resource_removed(record, resource)

    def _cleanup_sandbox_resource(
        self,
        record: dict[str, Any],
        spec: RunSpec,
        *,
        manifest: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        state = record["resources"]["sandbox"]
        if state["state"] == "removed":
            named = [item for item in self._sandbox_inventory() if item.get("name") == spec.sandbox_name]
            if named or self._docker_container_ids(spec.sandbox_name):
                raise LifecycleError("sandbox_removed_resource_reappeared")
            return record
        if not state["intent_sha256"]:
            record = self._resource_removing(record, "sandbox")
            return self._resource_removed(record, "sandbox")
        nonce = self._read_secret(spec, "run.nonce", NONCE_RE, str((manifest or {}).get("run_nonce_sha256") or ""))
        expected_sandbox_id = str((manifest or {}).get("sandbox_id") or "")
        expected_container_id = str((manifest or {}).get("container_id") or "")
        if state["state"] == "present":
            if manifest is None or self._sandbox_receipt_sha(spec, manifest) != state["receipt_sha256"]:
                raise LifecycleError("sandbox_receipt_mismatch")
        named = [item for item in self._sandbox_inventory() if item.get("name") == spec.sandbox_name]
        containers = self._docker_container_ids(spec.sandbox_name)
        if named:
            self.verify_sandbox_identity(
                sandbox_name=spec.sandbox_name,
                run_id=spec.run_id,
                nonce=nonce,
                expected_sandbox_id=expected_sandbox_id,
                expected_container_id=expected_container_id,
            )
        elif containers:
            raise LifecycleError("sandbox_orphan_identity_uncertain")
        record = self._resource_removing(record, "sandbox")
        if named:
            self._delete_verified_sandbox(
                sandbox_name=spec.sandbox_name,
                run_id=spec.run_id,
                nonce=nonce,
                expected_sandbox_id=expected_sandbox_id,
                expected_container_id=expected_container_id,
            )
        if [
            item for item in self._sandbox_inventory() if item.get("name") == spec.sandbox_name
        ] or self._docker_container_ids(spec.sandbox_name):
            raise LifecycleError("sandbox_remained_after_delete")
        return self._resource_removed(record, "sandbox")

    def _cleanup_secrets_resource(self, record: dict[str, Any], spec: RunSpec) -> dict[str, Any]:
        state = record["resources"]["secrets"]
        if state["state"] == "removed":
            if any(
                (spec.run_dir / name).exists()
                for name in ("api.key", "run.nonce", *BROKER_IDENTITY_SECRET_FILES)
            ):
                raise LifecycleError("secrets_removed_resource_reappeared")
            return record
        if state["state"] == "present" and self._secrets_receipt_sha(spec) != state["receipt_sha256"]:
            raise LifecycleError("secrets_receipt_mismatch")
        record = self._resource_removing(record, "secrets")
        for name in ("api.key", "run.nonce", *BROKER_IDENTITY_SECRET_FILES):
            _remove_private(spec.run_dir / name, root=self.project_root)
        return self._resource_removed(record, "secrets")

    def _cleanup_resources(
        self,
        record: dict[str, Any],
        spec: RunSpec,
        *,
        manifest: Mapping[str, Any] | None,
        guard_record: ProcessRecord | None = None,
        forward_record: ProcessRecord | None = None,
        skip_guard: bool = False,
        restore_snapshot: bool = False,
    ) -> dict[str, Any]:
        baseline = self._read_host_baseline(spec)
        expected_intents = {
            "run_dir": self._run_dir_intent_sha(spec, baseline),
            "secrets": self._secrets_intent_sha(spec),
            "forward": self._forward_intent_sha(spec),
        }
        if manifest is not None:
            expected_intents.update(
                {
                    "guard": self._guard_intent_sha(spec, str(manifest.get("policy_sha256") or "")),
                    "sandbox": self._sandbox_intent_sha(spec, manifest),
                }
            )
        for resource, item in record["resources"].items():
            if not item["intent_sha256"]:
                continue
            expected = expected_intents.get(resource)
            if expected is None or item["intent_sha256"] != expected:
                raise LifecycleError(f"{resource}_intent_mismatch")
        record = self._cleanup_process_resource(
            record,
            spec,
            "forward",
            override=forward_record,
            manifest=manifest,
        )
        record = self._cleanup_sandbox_resource(record, spec, manifest=manifest)
        if restore_snapshot:
            try:
                event = self._guard_event(spec, GUARD_TRIGGER_NAME) or self._guard_event(spec, GUARD_OUTCOME_NAME)
            except LifecycleError as exc:
                if exc.code != "guard_event_invalid":
                    raise
                event = None
            self._restore_guard_snapshot(spec, event)
        if skip_guard:
            guard_state = record["resources"]["guard"]["state"]
            if guard_state == "present":
                record = self._resource_removing(record, "guard")
            elif guard_state not in {"removing", "removed"}:
                raise LifecycleError("guard_resource_state_invalid")
        else:
            record = self._cleanup_process_resource(
                record,
                spec,
                "guard",
                override=guard_record,
                manifest=manifest,
            )
        record = self._cleanup_secrets_resource(record, spec)
        if record["resources"]["run_dir"]["state"] != "present":
            raise LifecycleError("run_dir_retain_receipt_missing")
        return record

    def _guard_event(self, spec: RunSpec, name: str) -> dict[str, Any] | None:
        """Read a strictly scoped guard event without treating it as proof."""

        path = spec.run_dir / name
        if not path.exists() or path.is_symlink():
            return None
        try:
            payload = _read_json(path, root=self.project_root)
        except LifecycleError as exc:
            raise LifecycleError("guard_event_invalid") from exc
        status = payload.get("status")
        if name == GUARD_TRIGGER_NAME:
            allowed_statuses = {"trigger_requested"}
        elif name == GUARD_OUTCOME_NAME:
            if status == "stopped":
                return None
            allowed_statuses = {"triggered", "failed"}
        else:
            allowed_statuses = {"cleanup_pending_guard_exit"}
        if (
            payload.get("schema_version") != GUARD_EVENT_SCHEMA
            or payload.get("profile") != PROFILE
            or payload.get("run_id") != spec.run_id
            or not isinstance(payload.get("reason_code"), str)
            or not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,95}", payload["reason_code"])
            or status not in allowed_statuses
        ):
            raise LifecycleError("guard_event_invalid")
        pid = payload.get("pid")
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 1:
            raise LifecycleError("guard_event_invalid")
        process = self._read_process(spec, "guard.process.json", "guard")
        if process.pid != pid:
            raise LifecycleError("guard_event_identity_mismatch")
        deleted_paths = payload.get("deleted_paths", [])
        if not isinstance(deleted_paths, list) or any(
            not isinstance(item, str)
            or not item
            or "\\" in item
            or Path(item).is_absolute()
            or any(part in {"", ".", ".."} for part in Path(item).parts)
            for item in deleted_paths
        ):
            raise LifecycleError("guard_event_invalid")
        return payload

    def _restore_guard_snapshot(self, spec: RunSpec, event: Mapping[str, Any] | None) -> int:
        snapshot_path = self.project_root / "var/openshell/siq-analysis/deletion-snapshots" / spec.run_id
        observed = event.get("deleted_paths") if event is not None else None
        if not isinstance(observed, list):
            observed = None
        try:
            from scripts.openshell.destructive_action_guard import restore_deletion_snapshot

            return restore_deletion_snapshot(
                project_root=self.project_root,
                analysis_root=spec.analysis_root,
                snapshot_path=snapshot_path,
                observed_paths=observed,
            )
        except Exception as exc:
            raise LifecycleError("deletion_snapshot_restore_failed") from exc

    def handle_guard_trigger(self, *, profile: str, run_id: str, reason_code: str) -> dict[str, Any]:
        """Fence a triggered run and durably leave only the guard process pending.

        The guard worker calls this after it has fenced the sandbox and restored
        the analysis tree.  The worker itself is never terminated here; its
        resource is marked ``removing`` and finalized immediately before the
        worker exits.  A later ``recover`` can safely finish the same journal if
        the worker dies at any boundary.
        """

        self.require_formal_lock()
        if profile != PROFILE:
            raise LifecycleError("profile_must_be_siq_analysis")
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,95}", reason_code):
            raise LifecycleError("guard_reason_invalid")
        record = self._transaction_for_run(run_id)
        spec, manifest = self._load_manifest(run_id)
        if record["phase"] == "running":
            if not record["terminal_action"]:
                record = self._tx(
                    transaction.set_terminal_action,
                    record["transaction_id"],
                    expected_generation=record["generation"],
                    action="stop",
                )
            record = self._tx(
                transaction.transition,
                record["transaction_id"],
                expected_generation=record["generation"],
                phase="stopping",
                error_code=f"guard_triggered_{reason_code}",
            )
        elif record["phase"] == "stopping" and record["terminal_action"] == "stop":
            pass
        elif record["phase"] == "stopping" and record["terminal_action"] == "rollback_to_host":
            return {
                "ok": True,
                "profile": PROFILE,
                "run_id": run_id,
                "status": "rollback_recovery_pending",
                "transaction_id": record["transaction_id"],
            }
        elif record["phase"] in transaction.TERMINAL_PHASES:
            return {"ok": True, "profile": PROFILE, "run_id": run_id, "status": "already_stopped"}
        else:
            raise LifecycleError("guard_trigger_transaction_phase_invalid")

        event = self._guard_event(spec, GUARD_TRIGGER_NAME) or self._guard_event(spec, GUARD_OUTCOME_NAME)
        if event is None:
            raise LifecycleError("guard_event_missing")
        record = self._cleanup_resources(
            record,
            spec,
            manifest=manifest,
            skip_guard=True,
            restore_snapshot=True,
        )
        _write_json(
            spec.run_dir / GUARD_CLEANUP_PENDING_NAME,
            {
                "schema_version": GUARD_EVENT_SCHEMA,
                "status": "cleanup_pending_guard_exit",
                "profile": PROFILE,
                "run_id": run_id,
                "reason_code": reason_code,
                "transaction_id": record["transaction_id"],
                "generation": record["generation"],
            },
            root=self.project_root,
        )
        return {
            "ok": True,
            "profile": PROFILE,
            "run_id": run_id,
            "status": "cleanup_pending_guard_exit",
            "transaction_id": record["transaction_id"],
        }

    def finalize_guard_worker_exit(self, *, profile: str, run_id: str, pid: int) -> dict[str, Any]:
        """Mark the current guard process as removing immediately before exit."""

        self.require_formal_lock()
        if profile != PROFILE or isinstance(pid, bool) or not isinstance(pid, int) or pid <= 1:
            raise LifecycleError("guard_exit_identity_invalid")
        record = self._transaction_for_run(run_id)
        spec, manifest = self._load_manifest(run_id)
        del manifest
        if record["phase"] != "stopping" or record["terminal_action"] != "stop":
            raise LifecycleError("guard_exit_transaction_phase_invalid")
        if record["resources"]["guard"]["state"] != "removing":
            raise LifecycleError("guard_exit_resource_state_invalid")
        process = self._read_process(spec, "guard.process.json", "guard")
        if process.pid != pid or self.backend.process_snapshot(pid, "guard") != process:
            raise LifecycleError("guard_exit_pid_identity_mismatch")
        record = self._resource_removed(record, "guard")
        return {
            "ok": True,
            "profile": PROFILE,
            "run_id": run_id,
            "status": "guard_exit_recorded",
            "transaction_id": record["transaction_id"],
        }

    def _load_manifest(self, run_id: str) -> tuple[RunSpec, dict[str, Any]]:
        if not RUN_ID_RE.fullmatch(run_id):
            raise LifecycleError("unsafe_run_id")
        run_dir = self.project_root / RUNS_RELATIVE / run_id
        manifest = _read_json(run_dir / "run.json", root=self.project_root)
        if set(manifest) != MANIFEST_FIELDS or manifest.get("schema_version") != SCHEMA_VERSION:
            raise LifecycleError("manifest_fields_invalid")
        spec = self.spec(
            profile=str(manifest.get("profile") or ""),
            market=str(manifest.get("market") or ""),
            company=str(manifest.get("company") or ""),
            run_id=run_id,
            require_analysis=False,
        )
        expected_runtime_snapshot = (
            (self.project_root / RUNTIME_SNAPSHOTS_RELATIVE / run_id).relative_to(self.project_root).as_posix()
        )
        expected_policy = (run_dir / "task-policy.yaml").relative_to(self.project_root).as_posix()
        mount_plan_value = manifest.get("mount_plan")
        mount_plan_path = self.project_root / str(mount_plan_value or "")
        phase = manifest.get("phase")
        api_key_digest = str(manifest.get("api_key_sha256") or "")
        nonce_digest = str(manifest.get("run_nonce_sha256") or "")
        secret_digests_valid = (
            phase == "prepared"
            and (not api_key_digest or SHA256_RE.fullmatch(api_key_digest) is not None)
            and (not nonce_digest or SHA256_RE.fullmatch(nonce_digest) is not None)
        ) or (SHA256_RE.fullmatch(api_key_digest) is not None and SHA256_RE.fullmatch(nonce_digest) is not None)
        if (
            spec.run_dir != run_dir
            or manifest.get("analysis_relative_path") != spec.analysis_relative_path
            or manifest.get("sandbox_name") != spec.sandbox_name
            or manifest.get("namespace") != NAMESPACE
            or manifest.get("forward_host") != FORWARD_HOST
            or manifest.get("forward_port") != FORWARD_PORT
            or manifest.get("mount_count") != BUSINESS_MOUNT_COUNT
            or manifest.get("providers") != list(PROVIDERS)
            or manifest.get("runtime_snapshot") != expected_runtime_snapshot
            or manifest.get("policy") != expected_policy
            or manifest.get("guard_process") != "guard.process.json"
            or manifest.get("forward_process") != "forward.process.json"
            or not isinstance(mount_plan_value, str)
            or mount_plan_path.parent != self.project_root / STATE_RELATIVE / "mount-plans"
            or not re.fullmatch(r"[0-9a-f]{64}\.driver-config\.json", mount_plan_path.name)
            or manifest.get("mount_plan_sha256") != _sha256_file(mount_plan_path)
            or not SHA256_RE.fullmatch(str(manifest.get("policy_sha256") or ""))
            or manifest.get("policy_sha256") != _sha256_file(self.project_root / expected_policy)
            or not secret_digests_valid
            or phase
            not in {
                "prepared",
                "sandbox_created",
                "running",
                "failed_rolled_back",
                "rollback_incomplete",
                "stopped",
            }
            or (
                phase in {"sandbox_created", "running", "stopped"}
                and (
                    not UUID_RE.fullmatch(str(manifest.get("sandbox_id") or ""))
                    or not str(manifest.get("container_id") or "")
                )
            )
        ):
            raise LifecycleError("manifest_identity_mismatch")
        return spec, manifest

    def _read_secret(self, spec: RunSpec, name: str, pattern: re.Pattern[str], expected_digest: str) -> str:
        value = _read_private(spec.run_dir / name, root=self.project_root, max_bytes=256).decode("ascii").strip()
        if not pattern.fullmatch(value) or _sha256_bytes(value.encode()) != expected_digest:
            raise LifecycleError(f"{name.replace('.', '_')}_identity_mismatch")
        return value

    def _read_process(self, spec: RunSpec, name: str, role: str) -> ProcessRecord:
        value = _read_json(spec.run_dir / name, root=self.project_root)
        if set(value) != set(ProcessRecord.__dataclass_fields__):
            raise LifecycleError(f"{role}_process_state_invalid")
        try:
            record = ProcessRecord(**value)
        except TypeError as exc:
            raise LifecycleError(f"{role}_process_state_invalid") from exc
        if (
            record.schema_version != PROCESS_SCHEMA_VERSION
            or record.role != role
            or isinstance(record.pid, bool)
            or not isinstance(record.pid, int)
            or record.pid <= 1
            or isinstance(record.start_ticks, bool)
            or not isinstance(record.start_ticks, int)
            or record.start_ticks <= 0
            or not isinstance(record.executable, str)
            or not isinstance(record.argv_sha256, str)
            or not SHA256_RE.fullmatch(record.argv_sha256)
        ):
            raise LifecycleError(f"{role}_process_state_invalid")
        return record

    def _active_run_id(self) -> str | None:
        try:
            discovery = transaction.recover_discovery(self.project_root)
        except transaction.TransactionError as exc:
            raise LifecycleError(exc.code) from exc
        if discovery.transaction is None:
            return None
        if discovery.orphaned:
            raise LifecycleError("transaction_recovery_required")
        return str(discovery.transaction["intent"]["run_id"])

    def _transaction_for_run(self, run_id: str) -> dict[str, Any]:
        try:
            record = transaction.load(self.project_root, self._transaction_id(run_id))
            discovery = transaction.recover_discovery(self.project_root)
        except transaction.TransactionError as exc:
            raise LifecycleError(exc.code) from exc
        if record["intent"]["run_id"] != run_id:
            raise LifecycleError("transaction_run_identity_mismatch")
        if discovery.transaction is not None and discovery.transaction["transaction_id"] == record["transaction_id"]:
            if discovery.orphaned:
                raise LifecycleError("transaction_recovery_required")
        elif record["phase"] not in transaction.TERMINAL_PHASES:
            raise LifecycleError("transaction_active_pointer_mismatch")
        return record

    def _optional_manifest(self, run_id: str) -> tuple[RunSpec, dict[str, Any] | None]:
        try:
            return self._load_manifest(run_id)
        except LifecycleError as exc:
            if exc.code == "required_state_missing":
                spec = self.spec(
                    profile=PROFILE,
                    market="cn",
                    company="recovery",
                    run_id=run_id,
                    require_analysis=False,
                )
                return spec, None
            raise

    def _verify_transaction_receipts(
        self,
        record: Mapping[str, Any],
        spec: RunSpec,
        manifest: Mapping[str, Any] | None,
    ) -> None:
        resources = record["resources"]
        run_resource = resources["run_dir"]
        if run_resource["state"] == "present" and self._run_dir_receipt_sha(spec) != run_resource["receipt_sha256"]:
            raise LifecycleError("run_dir_receipt_mismatch")
        if manifest is None:
            if any(resources[name]["state"] in {"present", "removing"} for name in ("guard", "sandbox", "forward")):
                raise LifecycleError("transaction_manifest_missing")
            return
        expected_intents = {
            "guard": self._guard_intent_sha(spec, str(manifest.get("policy_sha256") or "")),
            "secrets": self._secrets_intent_sha(spec),
            "sandbox": self._sandbox_intent_sha(spec, manifest),
            "forward": self._forward_intent_sha(spec),
        }
        for resource, intent_sha in expected_intents.items():
            item = resources[resource]
            if item["intent_sha256"] and item["intent_sha256"] != intent_sha:
                raise LifecycleError(f"{resource}_intent_mismatch")
        if (
            resources["secrets"]["state"] == "present"
            and self._secrets_receipt_sha(spec) != resources["secrets"]["receipt_sha256"]
        ):
            raise LifecycleError("secrets_receipt_mismatch")
        for resource in ("guard", "forward"):
            item = resources[resource]
            if item["state"] != "present":
                continue
            process = self._read_process(spec, f"{resource}.process.json", resource)
            if self._process_receipt_sha(spec, resource, process) != item["receipt_sha256"]:
                raise LifecycleError(f"{resource}_receipt_mismatch")
            current = self.backend.process_snapshot(process.pid, resource)
            if current is not None and current != process:
                raise LifecycleError(f"{resource}_pid_identity_mismatch")
        if resources["sandbox"]["state"] == "present":
            if self._sandbox_receipt_sha(spec, manifest) != resources["sandbox"]["receipt_sha256"]:
                raise LifecycleError("sandbox_receipt_mismatch")
            nonce = self._read_secret(spec, "run.nonce", NONCE_RE, str(manifest.get("run_nonce_sha256") or ""))
            self.verify_sandbox_identity(
                sandbox_name=spec.sandbox_name,
                run_id=spec.run_id,
                nonce=nonce,
                expected_sandbox_id=str(manifest.get("sandbox_id") or ""),
                expected_container_id=str(manifest.get("container_id") or ""),
            )

    def _finish_terminal_transaction(
        self,
        record: dict[str, Any],
        spec: RunSpec,
        manifest: dict[str, Any] | None,
        *,
        action: str,
        prechecked: bool = False,
        restore_snapshot: bool = False,
    ) -> dict[str, Any]:
        if action not in {"stop", "rollback_to_host"}:
            raise LifecycleError("terminal_action_invalid")
        if record["phase"] == "running":
            if not prechecked:
                self._verify_transaction_receipts(record, spec, manifest)
            if not record["terminal_action"]:
                record = self._tx(
                    transaction.set_terminal_action,
                    record["transaction_id"],
                    expected_generation=record["generation"],
                    action=action,
                )
            elif record["terminal_action"] != action:
                raise LifecycleError("terminal_action_invalid")
            record = self._tx(
                transaction.transition,
                record["transaction_id"],
                expected_generation=record["generation"],
                phase="stopping",
            )
        elif record["phase"] == "stopping":
            if record["terminal_action"] != action:
                raise LifecycleError("terminal_action_invalid")
        elif record["phase"] in transaction.TERMINAL_PHASES:
            self._tx(transaction.finalize, record["transaction_id"])
            return {"ok": True, "profile": PROFILE, "run_id": spec.run_id, "status": "already_stopped"}
        else:
            raise LifecycleError("transaction_phase_invalid")

        # A crash can leave rollback durably in ``stopping`` before cleanup starts.
        # Revalidate the exact host receipt on every resume before touching resources.
        if action == "rollback_to_host":
            baseline = self._read_host_baseline(spec)
            current = self._stable_host_receipt()
            if current != baseline:
                raise LifecycleError("host_hermes_identity_changed_before_stop")
        if manifest is not None and not restore_snapshot:
            # A concurrent operator stop may win the maintenance lock after a
            # guard has already persisted its trigger. Preserve the safety
            # intent and restore the verified deletion snapshot in that path.
            restore_snapshot = self._guard_event(spec, GUARD_TRIGGER_NAME) is not None

        record = self._cleanup_resources(
            record,
            spec,
            manifest=manifest,
            restore_snapshot=restore_snapshot,
        )
        if action == "rollback_to_host":
            baseline = self._read_host_baseline(spec)
            current = self._stable_host_receipt(after_stop=True)
            if current != baseline:
                raise LifecycleError("host_hermes_identity_changed_after_stop")
        # Publish only after both pre-stop and post-stop host identity checks.
        # A host drift must never result in a new immutable index being written.
        publisher_result = (
            self._publish_company_index(spec, policy_digest=str((manifest or {}).get("policy_sha256") or ""))
            if manifest is not None
            else {"status": "skipped"}
        )
        if manifest is not None:
            manifest.update({"phase": "stopped", "updated_at": _utc_now(), "error_code": ""})
            self._write_manifest(spec, manifest)
        record = self._tx(
            transaction.transition,
            record["transaction_id"],
            expected_generation=record["generation"],
            phase="stopped",
        )
        self._tx(transaction.finalize, record["transaction_id"])
        return {
            "ok": True,
            "profile": PROFILE,
            "run_id": spec.run_id,
            "status": "stopped",
            "runtime": "host" if action == "rollback_to_host" else "openshell",
            "host_runs_url": f"http://{FORWARD_HOST}:{HOST_HERMES_PORT}/v1/runs"
            if action == "rollback_to_host"
            else None,
            "host_runtime_unchanged": True if action == "rollback_to_host" else None,
            "host_receipt_sha256": _host_receipt_sha256(self._read_host_baseline(spec))
            if action == "rollback_to_host"
            else None,
            "publisher": publisher_result,
        }

    def stop(self, *, profile: str, run_id: str) -> dict[str, Any]:
        self.require_formal_lock()
        if profile != PROFILE:
            raise LifecycleError("profile_must_be_siq_analysis")
        record = self._transaction_for_run(run_id)
        spec, manifest = self._optional_manifest(run_id)
        try:
            result = self._finish_terminal_transaction(record, spec, manifest, action="stop")
        except LifecycleError as exc:
            try:
                self._audit(
                    run_id=run_id,
                    sandbox_name=spec.sandbox_name,
                    policy_digest=str((manifest or {}).get("policy_sha256") or "0" * 64),
                    code=exc.code,
                )
            except Exception:
                pass
            raise
        return {key: value for key, value in result.items() if value is not None}

    def status(self, *, profile: str, run_id: str | None = None) -> dict[str, Any]:
        if profile != PROFILE:
            raise LifecycleError("profile_must_be_siq_analysis")
        active = self._active_run_id()
        selected = run_id or active
        if selected is None:
            return {"ok": True, "profile": PROFILE, "status": "stopped"}
        if active is not None and selected != active:
            raise LifecycleError("active_run_identity_mismatch")
        spec, manifest = self._load_manifest(selected)
        if manifest["phase"] == "stopped":
            return {"ok": True, "profile": PROFILE, "run_id": selected, "status": "stopped"}
        nonce = self._read_secret(spec, "run.nonce", NONCE_RE, manifest["run_nonce_sha256"])
        key = self._read_secret(spec, "api.key", KEY_RE, manifest["api_key_sha256"])
        guard = self._read_process(spec, manifest["guard_process"], "guard")
        forward = self._read_process(spec, manifest["forward_process"], "forward")
        guard_ok = self.backend.process_snapshot(guard.pid, "guard") == guard
        forward_ok = self.backend.process_snapshot(forward.pid, "forward") == forward
        sandbox_ok = False
        try:
            identity = self.verify_sandbox_identity(
                sandbox_name=spec.sandbox_name,
                run_id=selected,
                nonce=nonce,
                expected_sandbox_id=manifest["sandbox_id"],
                expected_container_id=manifest["container_id"],
            )
            sandbox_ok = identity.sandbox_id == manifest["sandbox_id"]
        except LifecycleError:
            sandbox_ok = False
        health_ok = forward_ok and self.backend.hermes_health(FORWARD_HOST, FORWARD_PORT, key)
        healthy = manifest["phase"] == "running" and guard_ok and forward_ok and sandbox_ok and health_ok
        return {
            "ok": healthy,
            "profile": PROFILE,
            "run_id": selected,
            "status": "running" if healthy else "degraded",
            "guard": guard_ok,
            "forward": forward_ok,
            "sandbox": sandbox_ok,
            "health": health_ok,
        }

    def rollback_to_host(self, *, profile: str, run_id: str) -> dict[str, Any]:
        self.require_formal_lock()
        if profile != PROFILE:
            raise LifecycleError("profile_must_be_siq_analysis")
        record = self._transaction_for_run(run_id)
        spec, manifest = self._optional_manifest(run_id)
        result = self._finish_terminal_transaction(record, spec, manifest, action="rollback_to_host")
        return {key: value for key, value in result.items() if value is not None}

    def recover(self, *, profile: str, run_id: str | None = None) -> dict[str, Any]:
        """Resume only a uniquely identified transaction; ambiguous resources stay active."""

        self.require_formal_lock()
        if profile != PROFILE:
            raise LifecycleError("profile_must_be_siq_analysis")
        try:
            discovery = transaction.recover_discovery(self.project_root)
        except transaction.TransactionError as exc:
            raise LifecycleError(exc.code) from exc
        if discovery.transaction is None:
            return {"ok": True, "profile": PROFILE, "status": "nothing_to_recover"}
        record = discovery.transaction
        selected_run_id = str(record["intent"]["run_id"])
        if run_id is not None and run_id != selected_run_id:
            raise LifecycleError("active_run_identity_mismatch")
        if discovery.orphaned:
            try:
                record = transaction.claim_orphan(self.project_root, record["transaction_id"])
            except transaction.TransactionError as exc:
                raise LifecycleError(exc.code) from exc
        if record["phase"] in transaction.TERMINAL_PHASES:
            self._tx(transaction.finalize, record["transaction_id"])
            return {
                "ok": True,
                "profile": PROFILE,
                "run_id": selected_run_id,
                "status": "finalized",
                "transaction_id": record["transaction_id"],
            }
        intent = record["intent"]
        spec = self.spec(
            profile=PROFILE,
            market=str(intent["market"]),
            company=str(intent["company"]),
            run_id=selected_run_id,
            require_analysis=False,
        )
        try:
            _, manifest = self._load_manifest(selected_run_id)
        except LifecycleError as exc:
            if exc.code == "required_state_missing":
                manifest = None
            else:
                raise
        guard_missing = False
        if record["phase"] in {"running", "stopping"}:
            try:
                guard_process = self._read_process(spec, "guard.process.json", "guard")
                current_guard = self.backend.process_snapshot(guard_process.pid, "guard")
                if current_guard is not None and current_guard != guard_process:
                    raise LifecycleError("guard_pid_identity_mismatch")
                guard_missing = current_guard is None
            except LifecycleError:
                if record["phase"] == "running":
                    raise
        try:
            trigger_event = self._guard_event(spec, GUARD_TRIGGER_NAME)
            outcome_event = self._guard_event(spec, GUARD_OUTCOME_NAME) if trigger_event is None else None
        except LifecycleError as exc:
            if exc.code != "guard_event_invalid" or not guard_missing:
                raise
            # A worker that is already gone cannot leave an invalid event in
            # control of recovery. Treat it as a failed guard and restore the
            # verified snapshot; retain the malformed file for review.
            trigger_event = None
            outcome_event = None
        guard_event = trigger_event or outcome_event
        destructive_trigger = trigger_event is not None or (
            outcome_event is not None and outcome_event.get("status") == "triggered"
        )
        guard_failure_event = outcome_event is not None and outcome_event.get("status") == "failed"
        # A normal worker shutdown may leave ``guard.outcome.json`` with
        # ``stopped``/``failed`` while a rollback transaction is being resumed;
        # those statuses are not destructive triggers and must not change the
        # already durable rollback action.
        if record["phase"] == "stopping" and record["terminal_action"] == "rollback_to_host" and not destructive_trigger:
            guard_event = None
            outcome_event = None
        if (
            record["phase"] == "stopping"
            and record["terminal_action"] == "rollback_to_host"
            and (destructive_trigger or guard_failure_event)
        ):
            result = self._finish_terminal_transaction(
                record,
                spec,
                manifest,
                action="rollback_to_host",
                prechecked=True,
                restore_snapshot=True,
            )
            result["recovered"] = True
            result["guard_triggered"] = destructive_trigger
            result["guard_failure"] = guard_failure_event
            result["transaction_id"] = record["transaction_id"]
            return result
        if (guard_event is not None or guard_missing) and record["phase"] in {"running", "stopping"}:
            reason_code = str(guard_event["reason_code"]) if guard_event is not None else "guard_process_missing"
            if record["phase"] == "running":
                if not record["terminal_action"]:
                    record = self._tx(
                        transaction.set_terminal_action,
                        record["transaction_id"],
                        expected_generation=record["generation"],
                        action="stop",
                    )
                record = self._tx(
                    transaction.transition,
                    record["transaction_id"],
                    expected_generation=record["generation"],
                    phase="stopping",
                    error_code=f"guard_triggered_{reason_code}",
                )
            elif record["terminal_action"] != "stop":
                raise LifecycleError("guard_trigger_transaction_action_invalid")
            result = self._finish_terminal_transaction(
                record,
                spec,
                manifest,
                action="stop",
                prechecked=True,
                restore_snapshot=destructive_trigger or guard_failure_event or guard_missing,
            )
            result["recovered"] = True
            result["guard_triggered"] = destructive_trigger
            result["guard_failure"] = not destructive_trigger
            result["transaction_id"] = record["transaction_id"]
            return result
        if record["phase"] in {"intent", "starting", "rollback_pending"}:
            error_code = record.get("error_code") or "recovered_failed_start"
            cleanup_error = self._rollback_failed_start(
                spec,
                transaction_id=record["transaction_id"],
                baseline=None,
                manifest=manifest,
                guard_record=None,
                forward_record=None,
                error_code=error_code,
            )
            if cleanup_error:
                raise LifecycleError(cleanup_error)
            return {
                "ok": True,
                "profile": PROFILE,
                "run_id": selected_run_id,
                "status": "rolled_back",
                "transaction_id": record["transaction_id"],
            }
        if record["phase"] == "running" and not record["terminal_action"]:
            return {
                "ok": False,
                "profile": PROFILE,
                "run_id": selected_run_id,
                "status": "running_recovery_required",
                "transaction_id": record["transaction_id"],
            }
        action = record["terminal_action"]
        if action not in {"stop", "rollback_to_host"}:
            raise LifecycleError("transaction_terminal_action_invalid")
        result = self._finish_terminal_transaction(record, spec, manifest, action=action)
        result["recovered"] = True
        result["transaction_id"] = record["transaction_id"]
        return result

    def repair(self, *, profile: str, run_id: str | None = None) -> dict[str, Any]:
        """Repair an interrupted lifecycle using the same fail-closed recovery path.

        ``repair`` is an operator-facing name for recovery.  It deliberately
        does not discover or delete unbound resources and does not introduce a
        second cleanup implementation; all identity, journal, and lock checks
        remain owned by :meth:`recover`.
        """

        result = dict(self.recover(profile=profile, run_id=run_id))
        result.setdefault("operation", "repair")
        return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    start = subparsers.add_parser("start")
    start.add_argument("--profile", required=True)
    start.add_argument("--market", required=True, choices=sorted(MARKET_ROOTS))
    start.add_argument("--company", required=True)
    start.add_argument("--run-id", required=True)
    for name in ("stop", "rollback"):
        child = subparsers.add_parser(name)
        child.add_argument("--profile", required=True)
        child.add_argument("--run-id", required=True)
    for name in ("recover", "repair"):
        recovery_parser = subparsers.add_parser(name)
        recovery_parser.add_argument("--profile", required=True)
        recovery_parser.add_argument("--run-id")
    status_parser = subparsers.add_parser("status")
    status_parser.add_argument("--profile", required=True)
    status_parser.add_argument("--run-id")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        adapter = LifecycleAdapter()
        if args.command == "start":
            result = adapter.start(
                adapter.prepare_analysis_root_for_start(
                    profile=args.profile,
                    market=args.market,
                    company=args.company,
                    run_id=args.run_id,
                )
            )
        elif args.command == "stop":
            result = adapter.stop(profile=args.profile, run_id=args.run_id)
        elif args.command == "status":
            result = adapter.status(profile=args.profile, run_id=args.run_id)
        elif args.command == "recover":
            result = adapter.recover(profile=args.profile, run_id=args.run_id)
        elif args.command == "repair":
            result = adapter.repair(profile=args.profile, run_id=args.run_id)
        else:
            result = adapter.rollback_to_host(profile=args.profile, run_id=args.run_id)
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
        return 0 if result.get("ok", False) else 1
    except (LifecycleError, OSError, TypeError, ValueError, UnicodeError) as exc:
        code = exc.code if isinstance(exc, LifecycleError) else "lifecycle_os_error"
        print(json.dumps({"ok": False, "error_code": code}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
