#!/usr/bin/python3 -IB
"""Probe one existing formal siq_analysis sandbox without managing its lifecycle."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import selectors
import signal
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

import yaml

_MODULE_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_MODULE_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_MODULE_REPO_ROOT))

from scripts.openshell.build_siq_analysis_mount_plan import (  # noqa: E402
    BUSINESS_MOUNT_COUNT,
    RUNTIME_STATE_DIRECTORY,
    SANDBOX_RUNTIME_STATE_ROOT,
)
from scripts.openshell.siq_analysis_lifecycle import (  # noqa: E402
    SECURITY_PROBE_ID_RE,
    SECURITY_PROBE_SCHEMA_VERSION,
    LifecycleAdapter,
    LifecycleError,
    SandboxIdentity,
    SecurityProbePlan,
    _minimal_child_environment,
    _remove_private as _lifecycle_remove_private,
    _write_json as _lifecycle_write_json,
    _write_private_atomic as _lifecycle_write_private_atomic,
)

SCHEMA_VERSION = "siq.openshell.siq_analysis_security_probe.v1"
INDEPENDENT_SCHEMA_VERSION = SECURITY_PROBE_SCHEMA_VERSION
LIFECYCLE_SCHEMA_VERSION = "siq.openshell.siq_analysis_lifecycle.v1"
PROFILE = "siq_analysis"
NAMESPACE = "siq-openshell-dev"
LIFECYCLE_LABEL = "siq-analysis-v1"
REPO_ROOT = _MODULE_REPO_ROOT
RUNS_RELATIVE = Path("var/openshell/siq-analysis/runs")
SNAPSHOTS_RELATIVE = Path("var/openshell/siq-analysis/runtime-snapshots")
MOUNT_PLANS_RELATIVE = Path("var/openshell/siq-analysis/mount-plans")
SECURITY_PROBES_RELATIVE = Path("var/openshell/siq-analysis/security-probes")
HERMES_HOME_RELATIVE = Path("data/hermes/home/profiles/siq_analysis")
WIKI_RELATIVE = Path("data/wiki")
RUN_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,47}\Z")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
CONTAINER_ID_RE = re.compile(r"[0-9a-f]{12,64}\Z")
NONCE_RE = re.compile(r"[0-9a-f]{48}\Z")
MAX_JSON_BYTES = 1024 * 1024
MAX_EXEC_OUTPUT_BYTES = 128 * 1024
DEFAULT_EXEC_TIMEOUT_SECONDS = 20
DOCKER = Path("/usr/bin/docker")
DOCKER_INSPECT_FORMAT = (
    '{"mounts":{{json .Mounts}},'
    '"privileged":{{json .HostConfig.Privileged}},'
    '"cap_add":{{json .HostConfig.CapAdd}},'
    '"devices":{{json .HostConfig.Devices}},'
    '"device_requests":{{json .HostConfig.DeviceRequests}},'
    '"security_opt":{{json .HostConfig.SecurityOpt}},'
    '"user":{{json .Config.User}}}'
)
EXPECTED_SUPERVISOR_CAP_ADD = frozenset({"NET_ADMIN", "SYS_ADMIN", "SYS_PTRACE", "SYSLOG"})
EXPECTED_SUPERVISOR_SECURITY_OPT = frozenset({"apparmor=unconfined"})
CONTAINER_HARDENING_SCHEMA_VERSION = "siq.openshell.container_hardening.v1"
PROCESS_HARDENING_SCHEMA_VERSION = "siq.openshell.process_hardening.v1"
PROCESS_HARDENING_CONTROLS = (
    "non_root_identity",
    "root_group_absent",
    "no_new_privs",
    "capability_sets_clear",
    "docker_socket_inaccessible",
    "sudo_denied",
    "su_denied",
    "setuid_root_denied",
    "setgid_root_denied",
    "mount_denied",
    "unshare_denied",
    "setns_denied",
    "raw_devices_absent",
    "block_device_create_denied",
)
SANDBOX_PYTHON = "/opt/siq/hermes/venv/bin/python"
SANDBOX_ROOT = Path("/home/maoyd/siq-research-engine")
SANDBOX_HERMES_HOME = SANDBOX_ROOT / HERMES_HOME_RELATIVE
RUNTIME_DIRECTORY_NAMES = ("sessions", "checkpoints", "cron", "memories")
CONTROL_MOUNT_COUNT = 5
FILESYSTEM_IMMUTABLE_DENIALS = (
    "source_data_read_only",
    "code_read_only",
    "configuration_read_only",
    "prompt_read_only",
    "workflow_read_only",
)
FILESYSTEM_SENSITIVE_DENIALS = (
    "control_credentials_read_only",
    "sensitive_paths_hidden",
)
FILESYSTEM_ALLOWED_WRITES = (
    "analysis_bind_read_write",
    "runtime_state_directory_bind_read_write",
    "runtime_session_bind_read_write",
    "runtime_memory_bind_read_write",
    "tmp_scratch_write",
)
FILESYSTEM_IDENTITY_CHECKS = (
    "lifecycle_identity_and_health",
    "openshell_sandbox_identity",
    "active_policy_matches_manifest",
    "mount_contract_7_plus_5",
)
MARKET_PREFIXES = {
    "cn": PurePosixPath("data/wiki/companies"),
    "eu": PurePosixPath("data/wiki/eu/companies"),
    "hk": PurePosixPath("data/wiki/hk/companies"),
    "jp": PurePosixPath("data/wiki/jp/companies"),
    "kr": PurePosixPath("data/wiki/kr/companies"),
    "us": PurePosixPath("data/wiki/us/companies"),
}
REQUIRED_MANIFEST_FIELDS = {
    "schema_version",
    "phase",
    "profile",
    "run_id",
    "market",
    "company",
    "analysis_relative_path",
    "sandbox_name",
    "namespace",
    "image_ref",
    "image_id",
    "runtime_snapshot",
    "mount_plan",
    "mount_plan_sha256",
    "mount_count",
    "policy",
    "policy_sha256",
    "run_nonce_sha256",
    "sandbox_id",
    "container_id",
}


class ProbeError(RuntimeError):
    """A stable probe failure that never includes runtime data."""

    def __init__(self, code: str, *, cleanup_code: str = "") -> None:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,95}", code):
            code = "invalid_probe_error"
        self.code = code
        self.cleanup_code = cleanup_code
        super().__init__(code)


@dataclass(frozen=True)
class ProbeContext:
    project_root: Path
    run_id: str
    run_dir: Path
    sandbox_name: str
    sandbox_id: str
    container_id: str
    analysis_path: Path
    runtime_snapshot: Path
    mount_plan: Path
    mount_plan_sha256: str
    policy_path: Path
    policy: Mapping[str, Any]
    manifest: Mapping[str, Any]


@dataclass(frozen=True)
class IndependentProbeContext:
    project_root: Path
    probe_id: str
    state_dir: Path
    sandbox_name: str
    sandbox_id: str
    container_id: str
    nonce: str
    analysis_path: Path
    runtime_snapshot: Path
    mount_plan: Path
    mount_plan_sha256: str
    policy_path: Path
    policy: Mapping[str, Any]
    manifest: Mapping[str, Any]


@dataclass(frozen=True)
class SentinelPaths:
    name: str
    marker: bytes
    analysis_host: Path
    state_host: Path
    runtime_host: Path
    memory_host: Path
    wiki_host: Path
    analysis_sandbox: Path
    state_sandbox: Path
    runtime_sandbox: Path
    memory_sandbox: Path
    wiki_sandbox: Path
    upload_sandbox: Path
    profile_sandbox: Path
    scripts_sandbox: Path
    hermes_source_sandbox: Path

    @classmethod
    def build(cls, context: ProbeContext | IndependentProbeContext) -> "SentinelPaths":
        token = secrets.token_hex(12)
        name = f".siq-security-probe-{token}"
        marker = f"siq-security-probe:{token}".encode("ascii")
        return cls.from_persisted(context, name=name, marker=marker)

    @classmethod
    def from_persisted(
        cls,
        context: ProbeContext | IndependentProbeContext,
        *,
        name: str,
        marker: bytes,
    ) -> "SentinelPaths":
        matched = re.fullmatch(r"\.siq-security-probe-([0-9a-f]{24})", name)
        if matched is None or marker != f"siq-security-probe:{matched.group(1)}".encode("ascii"):
            raise ProbeError("probe_sentinel_identity_invalid")
        analysis_sandbox = SANDBOX_ROOT / context.manifest["analysis_relative_path"] / name
        return cls(
            name=name,
            marker=marker,
            analysis_host=context.analysis_path / name,
            state_host=context.runtime_snapshot / RUNTIME_STATE_DIRECTORY / name,
            runtime_host=context.runtime_snapshot / "sessions" / name,
            memory_host=context.runtime_snapshot / "memories" / name,
            wiki_host=context.project_root / WIKI_RELATIVE / name,
            analysis_sandbox=analysis_sandbox,
            state_sandbox=SANDBOX_RUNTIME_STATE_ROOT / name,
            runtime_sandbox=SANDBOX_HERMES_HOME / "sessions" / name,
            memory_sandbox=SANDBOX_HERMES_HOME / "memories" / name,
            wiki_sandbox=SANDBOX_ROOT / WIKI_RELATIVE / name,
            upload_sandbox=Path("/tmp") / f"{name}.upload",
            profile_sandbox=SANDBOX_ROOT / "agents/hermes/profiles/siq_analysis" / name,
            scripts_sandbox=SANDBOX_ROOT / "agents/hermes/profiles/siq_analysis/scripts" / name,
            hermes_source_sandbox=Path("/opt/hermes-agent") / name,
        )


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _assert_no_symlink_components(path: Path, *, code: str) -> None:
    if not path.is_absolute() or ".." in path.parts:
        raise ProbeError(code)
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            break
        if stat.S_ISLNK(mode):
            raise ProbeError(code)


def _read_regular_file(
    path: Path,
    *,
    code: str,
    max_bytes: int = MAX_JSON_BYTES,
    private: bool = False,
) -> bytes:
    _assert_no_symlink_components(path, code=code)
    try:
        expected = path.lstat()
    except OSError as exc:
        raise ProbeError(code) from exc
    if (
        not stat.S_ISREG(expected.st_mode)
        or expected.st_nlink != 1
        or expected.st_size > max_bytes
        or (private and (expected.st_uid != os.geteuid() or stat.S_IMODE(expected.st_mode) & 0o077))
    ):
        raise ProbeError(code)
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise ProbeError(code) from exc
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (expected.st_dev, expected.st_ino):
            raise ProbeError(code)
        content = bytearray()
        while chunk := os.read(descriptor, min(64 * 1024, max_bytes + 1 - len(content))):
            content.extend(chunk)
            if len(content) > max_bytes:
                raise ProbeError(code)
        finished = os.fstat(descriptor)
        if (finished.st_size, finished.st_mtime_ns, finished.st_ino) != (
            opened.st_size,
            opened.st_mtime_ns,
            opened.st_ino,
        ):
            raise ProbeError(code)
        return bytes(content)
    finally:
        os.close(descriptor)


def _read_json_file(path: Path, *, code: str, private: bool = False) -> dict[str, Any]:
    try:
        value = json.loads(_read_regular_file(path, code=code, private=private))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProbeError(code) from exc
    if not isinstance(value, dict):
        raise ProbeError(code)
    return value


def _relative_manifest_path(value: Any, *, code: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ProbeError(code)
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ProbeError(code)
    return path


def _canonical_project_child(project_root: Path, relative: PurePosixPath, *, code: str) -> Path:
    path = project_root.joinpath(*relative.parts)
    _assert_no_symlink_components(path, code=code)
    try:
        canonical = path.resolve(strict=True)
    except OSError as exc:
        raise ProbeError(code) from exc
    if canonical != path or project_root not in path.parents:
        raise ProbeError(code)
    return path


def _validate_analysis_relative(manifest: Mapping[str, Any]) -> PurePosixPath:
    market = manifest.get("market")
    company = manifest.get("company")
    relative = _relative_manifest_path(manifest.get("analysis_relative_path"), code="analysis_path_invalid")
    if (
        market not in MARKET_PREFIXES
        or not isinstance(company, str)
        or not company
        or len(company) > 128
        or not company[0].isalnum()
        or any(not (character.isalnum() or character in "-_.()\uff08\uff09") for character in company)
    ):
        raise ProbeError("analysis_path_invalid")
    expected = MARKET_PREFIXES[market] / company / "analysis"
    if relative != expected:
        raise ProbeError("analysis_path_invalid")
    return relative


def load_context(project_root: Path, run_id: str) -> ProbeContext:
    if not RUN_ID_RE.fullmatch(run_id):
        raise ProbeError("run_id_invalid")
    try:
        project_root = project_root.expanduser().resolve(strict=True)
    except OSError as exc:
        raise ProbeError("project_root_invalid") from exc
    if project_root != REPO_ROOT or not project_root.is_dir() or project_root.is_symlink():
        raise ProbeError("project_root_invalid")
    run_dir = project_root / RUNS_RELATIVE / run_id
    manifest = _read_json_file(run_dir / "run.json", code="manifest_invalid", private=True)
    if not REQUIRED_MANIFEST_FIELDS.issubset(manifest):
        raise ProbeError("manifest_invalid")
    if (
        manifest.get("schema_version") != LIFECYCLE_SCHEMA_VERSION
        or manifest.get("phase") != "running"
        or manifest.get("profile") != PROFILE
        or manifest.get("run_id") != run_id
        or manifest.get("sandbox_name") != f"siq-analysis-{run_id}"
        or manifest.get("namespace") != NAMESPACE
        or manifest.get("mount_count") != BUSINESS_MOUNT_COUNT
        or not isinstance(manifest.get("image_ref"), str)
        or not re.fullmatch(r"siq/hermes-openshell-siq-analysis:[0-9a-f]{24}", manifest["image_ref"])
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(manifest.get("image_id") or ""))
        or not UUID_RE.fullmatch(str(manifest.get("sandbox_id") or ""))
        or not CONTAINER_ID_RE.fullmatch(str(manifest.get("container_id") or ""))
        or not SHA256_RE.fullmatch(str(manifest.get("mount_plan_sha256") or ""))
        or not SHA256_RE.fullmatch(str(manifest.get("policy_sha256") or ""))
        or not SHA256_RE.fullmatch(str(manifest.get("run_nonce_sha256") or ""))
    ):
        raise ProbeError("manifest_invalid")

    analysis_relative = _validate_analysis_relative(manifest)
    analysis_path = _canonical_project_child(project_root, analysis_relative, code="analysis_path_invalid")
    if not analysis_path.is_dir():
        raise ProbeError("analysis_path_invalid")

    runtime_relative = _relative_manifest_path(manifest.get("runtime_snapshot"), code="runtime_snapshot_invalid")
    expected_runtime_relative = PurePosixPath(SNAPSHOTS_RELATIVE.as_posix()) / run_id
    if runtime_relative != expected_runtime_relative:
        raise ProbeError("runtime_snapshot_invalid")
    runtime_snapshot = _canonical_project_child(
        project_root,
        runtime_relative,
        code="runtime_snapshot_invalid",
    )
    if not runtime_snapshot.is_dir() or not (runtime_snapshot / "sessions").is_dir():
        raise ProbeError("runtime_snapshot_invalid")

    mount_relative = _relative_manifest_path(manifest.get("mount_plan"), code="mount_plan_invalid")
    if (
        mount_relative.parent != PurePosixPath(MOUNT_PLANS_RELATIVE.as_posix())
        or not re.fullmatch(r"[0-9a-f]{64}\.driver-config\.json", mount_relative.name)
        or mount_relative.name != f"{manifest['mount_plan_sha256']}.driver-config.json"
    ):
        raise ProbeError("mount_plan_invalid")
    mount_plan = _canonical_project_child(project_root, mount_relative, code="mount_plan_invalid")
    mount_content = _read_regular_file(mount_plan, code="mount_plan_invalid", private=True)
    if _sha256_bytes(mount_content) != manifest["mount_plan_sha256"]:
        raise ProbeError("mount_plan_digest_mismatch")

    policy_relative = _relative_manifest_path(manifest.get("policy"), code="policy_invalid")
    expected_policy_relative = PurePosixPath(RUNS_RELATIVE.as_posix()) / run_id / "task-policy.yaml"
    if policy_relative != expected_policy_relative:
        raise ProbeError("policy_invalid")
    policy_path = _canonical_project_child(project_root, policy_relative, code="policy_invalid")
    policy_content = _read_regular_file(policy_path, code="policy_invalid", private=True)
    if _sha256_bytes(policy_content) != manifest["policy_sha256"]:
        raise ProbeError("policy_digest_mismatch")
    try:
        policy = json.loads(policy_content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProbeError("policy_invalid") from exc
    if not isinstance(policy, dict):
        raise ProbeError("policy_invalid")

    return ProbeContext(
        project_root=project_root,
        run_id=run_id,
        run_dir=run_dir,
        sandbox_name=str(manifest["sandbox_name"]),
        sandbox_id=str(manifest["sandbox_id"]),
        container_id=str(manifest["container_id"]),
        analysis_path=analysis_path,
        runtime_snapshot=runtime_snapshot,
        mount_plan=mount_plan,
        mount_plan_sha256=str(manifest["mount_plan_sha256"]),
        policy_path=policy_path,
        policy=policy,
        manifest=manifest,
    )


def _expected_business_mounts(context: ProbeContext | IndependentProbeContext) -> list[dict[str, Any]]:
    plan = _read_json_file(context.mount_plan, code="mount_plan_invalid", private=True)
    if set(plan) != {"docker"} or not isinstance(plan.get("docker"), dict):
        raise ProbeError("mount_plan_invalid")
    docker = plan["docker"]
    if set(docker) != {"mounts"} or not isinstance(docker.get("mounts"), list):
        raise ProbeError("mount_plan_invalid")
    mounts = docker["mounts"]
    if len(mounts) != BUSINESS_MOUNT_COUNT:
        raise ProbeError("business_mount_count_invalid")

    root = context.project_root
    hermes_home = root / HERMES_HOME_RELATIVE
    expected: list[tuple[Path, Path, bool]] = [
        (root / WIKI_RELATIVE, root / WIKI_RELATIVE, True),
        (context.analysis_path, context.analysis_path, False),
        (context.runtime_snapshot / RUNTIME_STATE_DIRECTORY, SANDBOX_RUNTIME_STATE_ROOT, False),
    ]
    expected.extend((context.runtime_snapshot / name, hermes_home / name, False) for name in RUNTIME_DIRECTORY_NAMES)
    normalized: list[dict[str, Any]] = []
    for _index, (raw, fixed) in enumerate(zip(mounts, expected, strict=True)):
        if not isinstance(raw, dict) or set(raw) != {"type", "source", "target", "read_only"}:
            raise ProbeError("mount_plan_invalid")
        source, target, read_only = fixed
        observed = (
            raw.get("type"),
            raw.get("source"),
            raw.get("target"),
            raw.get("read_only"),
        )
        wanted = ("bind", source.as_posix(), target.as_posix(), read_only)
        if observed != wanted:
            raise ProbeError("business_mount_contract_mismatch")
        _assert_no_symlink_components(source, code="business_mount_source_unsafe")
        try:
            source_info = source.lstat()
        except OSError as exc:
            raise ProbeError("business_mount_source_unsafe") from exc
        if not stat.S_ISDIR(source_info.st_mode):
            raise ProbeError("business_mount_source_type_mismatch")
        normalized.append(raw)
    return normalized


def _kill_process_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    except OSError:
        if process.poll() is None:
            try:
                process.kill()
            except OSError:
                pass


def _run_command(argv: Sequence[str], *, timeout: int, code: str) -> subprocess.CompletedProcess[str]:
    environment = _minimal_child_environment(REPO_ROOT)
    command = list(argv)
    try:
        process = subprocess.Popen(
            command,
            cwd=REPO_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            env=environment,
            start_new_session=True,
        )
    except OSError as exc:
        raise ProbeError(code) from exc

    if process.stdout is None or process.stderr is None:
        _kill_process_group(process)
        process.wait()
        raise ProbeError(code)
    streams = selectors.DefaultSelector()
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    streams.register(process.stdout, selectors.EVENT_READ, "stdout")
    streams.register(process.stderr, selectors.EVENT_READ, "stderr")
    deadline = time.monotonic() + timeout
    try:
        while streams.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProbeError(code)
            for key, _ in streams.select(min(remaining, 0.25)):
                try:
                    chunk = os.read(key.fd, 64 * 1024)
                except OSError as exc:
                    raise ProbeError(code) from exc
                if not chunk:
                    streams.unregister(key.fileobj)
                    continue
                buffer = buffers[str(key.data)]
                buffer.extend(chunk)
                if len(buffer) > MAX_EXEC_OUTPUT_BYTES:
                    raise ProbeError(f"{code}_output_too_large")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProbeError(code)
        try:
            returncode = process.wait(timeout=remaining)
        except subprocess.TimeoutExpired as exc:
            raise ProbeError(code) from exc
    except BaseException:
        _kill_process_group(process)
        process.wait()
        raise
    finally:
        streams.close()
        process.stdout.close()
        process.stderr.close()
    return subprocess.CompletedProcess(
        command,
        returncode,
        stdout=buffers["stdout"].decode("utf-8", errors="replace"),
        stderr=buffers["stderr"].decode("utf-8", errors="replace"),
    )


def _docker_inspect_container(
    context: ProbeContext | IndependentProbeContext,
    *,
    timeout: int,
) -> Mapping[str, Any]:
    if not DOCKER.is_file() or DOCKER.is_symlink() or not os.access(DOCKER, os.X_OK):
        raise ProbeError("docker_cli_invalid")
    result = _run_command(
        [
            str(DOCKER),
            "inspect",
            "--type",
            "container",
            "--format",
            DOCKER_INSPECT_FORMAT,
            context.container_id,
        ],
        timeout=timeout,
        code="docker_inspect_failed",
    )
    if result.returncode != 0 or result.stderr.strip():
        raise ProbeError("docker_inspect_failed")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ProbeError("docker_inspect_invalid") from exc
    expected_fields = {
        "mounts",
        "privileged",
        "cap_add",
        "devices",
        "device_requests",
        "security_opt",
        "user",
    }
    if not isinstance(payload, dict) or set(payload) != expected_fields:
        raise ProbeError("docker_inspect_invalid")
    mounts = payload.get("mounts")
    if not isinstance(mounts, list) or not all(isinstance(item, dict) for item in mounts):
        raise ProbeError("docker_inspect_invalid")
    return payload


def _docker_inspect_mounts(
    context: ProbeContext | IndependentProbeContext,
    *,
    timeout: int,
) -> list[Mapping[str, Any]]:
    inspection = _docker_inspect_container(context, timeout=timeout)
    return list(inspection["mounts"])


def validate_container_hardening(inspection: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the exact privileged bootstrap envelope used by OpenShell.

    The Docker supervisor needs a narrow root/capability bootstrap in v0.0.83.
    The workload process is verified separately after OpenShell drops identity,
    capability sets and privilege regain.  Accepting only the known bootstrap
    values prevents an unnoticed expansion of the container-level envelope.
    """

    cap_add = inspection.get("cap_add")
    devices = inspection.get("devices")
    device_requests = inspection.get("device_requests")
    security_opt = inspection.get("security_opt")
    if cap_add is None:
        cap_add = []
    if devices is None:
        devices = []
    if device_requests is None:
        device_requests = []
    if security_opt is None:
        security_opt = []
    if (
        inspection.get("privileged") is not False
        or not isinstance(cap_add, list)
        or any(not isinstance(item, str) for item in cap_add)
        or len(cap_add) != len(set(cap_add))
        or set(cap_add) != EXPECTED_SUPERVISOR_CAP_ADD
        or not isinstance(devices, list)
        or devices
        or not isinstance(device_requests, list)
        or device_requests
        or not isinstance(security_opt, list)
        or any(not isinstance(item, str) for item in security_opt)
        or len(security_opt) != len(set(security_opt))
        or set(security_opt) != EXPECTED_SUPERVISOR_SECURITY_OPT
        or inspection.get("user") != "0"
    ):
        raise ProbeError("container_hardening_contract_mismatch")
    return {
        "schema_version": CONTAINER_HARDENING_SCHEMA_VERSION,
        "privileged": False,
        "supervisor_user": "0",
        "cap_add_count": len(cap_add),
        "cap_add_profile": "openshell_v0.0.83_bootstrap_exact",
        "host_device_count": 0,
        "device_request_count": 0,
        "security_opt_profile": "apparmor_unconfined_only",
    }


def _mount_observation(mounts: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    observation: list[dict[str, Any]] = []
    for raw in mounts:
        destination = raw.get("Destination")
        observation.append(
            {
                "destination": destination if isinstance(destination, str) else "invalid",
                "type": raw.get("Type") if isinstance(raw.get("Type"), str) else "invalid",
                "read_write": raw.get("RW") if isinstance(raw.get("RW"), bool) else None,
                "mode": raw.get("Mode") if isinstance(raw.get("Mode"), str) else "invalid",
                "propagation": (raw.get("Propagation") if isinstance(raw.get("Propagation"), str) else "invalid"),
            }
        )
    return sorted(observation, key=lambda item: item["destination"])


def _validate_openshell_identity(context: ProbeContext, *, timeout: int) -> None:
    run_cli = context.project_root / "scripts/openshell/run_cli.sh"
    if not run_cli.is_file() or run_cli.is_symlink() or not os.access(run_cli, os.X_OK):
        raise ProbeError("openshell_wrapper_invalid")
    result = _run_command(
        [
            str(run_cli),
            "sandbox",
            "list",
            "-o",
            "json",
            "--selector",
            f"ai.siq.run-id={context.run_id}",
        ],
        timeout=timeout,
        code="sandbox_inventory_failed",
    )
    if result.returncode != 0 or result.stderr.strip():
        raise ProbeError("sandbox_inventory_failed")
    try:
        inventory = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ProbeError("sandbox_inventory_invalid") from exc
    if not isinstance(inventory, list) or len(inventory) != 1 or not isinstance(inventory[0], dict):
        raise ProbeError("sandbox_inventory_invalid")
    item = inventory[0]
    labels = item.get("labels")
    nonce = labels.get("ai.siq.run-nonce") if isinstance(labels, dict) else None
    if (
        item.get("name") != context.sandbox_name
        or item.get("id") != context.sandbox_id
        or not isinstance(labels, dict)
        or labels.get("ai.siq.run-id") != context.run_id
        or labels.get("ai.siq.profile") != PROFILE
        or labels.get("ai.siq.lifecycle") != LIFECYCLE_LABEL
        or not isinstance(nonce, str)
        or not NONCE_RE.fullmatch(nonce)
        or _sha256_bytes(nonce.encode("ascii")) != context.manifest["run_nonce_sha256"]
    ):
        raise ProbeError("sandbox_identity_mismatch")


def _validate_lifecycle_status(context: ProbeContext, *, timeout: int) -> None:
    status_wrapper = context.project_root / "scripts/openshell/status_hermes_gateway.sh"
    if not status_wrapper.is_file() or status_wrapper.is_symlink() or not os.access(status_wrapper, os.X_OK):
        raise ProbeError("lifecycle_status_wrapper_invalid")
    result = _run_command(
        [
            str(status_wrapper),
            "--profile",
            PROFILE,
            "--run-id",
            context.run_id,
        ],
        timeout=timeout,
        code="lifecycle_status_failed",
    )
    if result.returncode != 0 or result.stderr.strip():
        raise ProbeError("lifecycle_status_failed")
    try:
        status_result = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ProbeError("lifecycle_status_invalid") from exc
    expected_true = ("ok", "guard", "forward", "sandbox", "health")
    if (
        not isinstance(status_result, dict)
        or status_result.get("profile") != PROFILE
        or status_result.get("run_id") != context.run_id
        or status_result.get("status") != "running"
        or any(status_result.get(key) is not True for key in expected_true)
    ):
        raise ProbeError("lifecycle_status_invalid")


def _validate_active_policy(context: ProbeContext | IndependentProbeContext, *, timeout: int) -> None:
    run_cli = context.project_root / "scripts/openshell/run_cli.sh"
    result = _run_command(
        [str(run_cli), "sandbox", "get", "--policy-only", context.sandbox_name],
        timeout=timeout,
        code="active_policy_read_failed",
    )
    if result.returncode != 0 or result.stderr.strip():
        raise ProbeError("active_policy_read_failed")
    try:
        active_policy = yaml.safe_load(result.stdout)
    except yaml.YAMLError as exc:
        raise ProbeError("active_policy_invalid") from exc
    if (
        isinstance(active_policy, dict)
        and "network_policies" not in active_policy
        and context.policy.get("network_policies") == {}
    ):
        active_policy = {**active_policy, "network_policies": {}}
    if isinstance(active_policy, dict):
        # OpenShell adds two fixed read-only system roots and materializes the
        # explicitly attached provider profiles into the effective policy.
        # Normalize only those exact, source-derived additions before comparing
        # the rest of the active policy with the lifecycle manifest.
        active_policy = json.loads(json.dumps(active_policy))
        active_ro = active_policy.get("filesystem_policy", {}).get("read_only")
        expected_ro = context.policy.get("filesystem_policy", {}).get("read_only")
        if isinstance(active_ro, list) and isinstance(expected_ro, list):
            extras = [item for item in active_ro if item not in expected_ro]
            if sorted(extras) == ["/etc", "/var/log"]:
                active_policy["filesystem_policy"]["read_only"] = [
                    item for item in active_ro if item not in extras
                ]

        manifest_path = context.project_root / "infra/openshell/providers/manifest.json"
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
        profiles = manifest.get("providers") if isinstance(manifest, dict) else None
        expected_provider_policies: dict[str, object] = {}
        if isinstance(profiles, list):
            for provider in profiles:
                if not isinstance(provider, dict) or provider.get("name") == "siq-exa-search":
                    continue
                profile_path = provider.get("profile")
                if not isinstance(profile_path, str):
                    continue
                profile = yaml.safe_load(
                    (context.project_root / "infra/openshell/providers" / profile_path).read_text(encoding="utf-8")
                )
                if not isinstance(profile, dict):
                    continue
                key = "_provider_" + re.sub(r"[^a-z0-9]+", "_", str(provider["name"]).lower()).strip("_")
                expected_provider_policies[key] = {
                    "name": key,
                    "endpoints": profile.get("endpoints"),
                    "binaries": [{"path": path} for path in profile.get("binaries", [])],
                }
        active_network = active_policy.get("network_policies")
        if isinstance(active_network, dict):
            actual_provider_policies = {
                key: value for key, value in active_network.items() if key.startswith("_provider_")
            }
            if actual_provider_policies == expected_provider_policies:
                active_policy["network_policies"] = {
                    key: value for key, value in active_network.items() if not key.startswith("_provider_")
                }
    if not isinstance(active_policy, dict) or active_policy != context.policy:
        raise ProbeError("active_policy_mismatch")


def _require_safe_control_source(path: Path, *, regular: bool = True) -> None:
    _assert_no_symlink_components(path, code="control_mount_source_unsafe")
    try:
        info = path.lstat()
    except OSError as exc:
        raise ProbeError("control_mount_source_unsafe") from exc
    if regular and (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise ProbeError("control_mount_source_unsafe")


def _validate_control_mounts(
    project_root: Path,
    sandbox_id: str,
    controls: Sequence[Mapping[str, Any]],
) -> None:
    if len(controls) != 5:
        raise ProbeError("control_mount_count_invalid")
    for raw in controls:
        if (
            raw.get("Type") != "bind"
            or raw.get("RW") is not False
            or raw.get("Mode") not in {"ro", "ro,z"}
            or raw.get("Propagation") != "rprivate"
        ):
            raise ProbeError("control_mount_not_read_only")

    supervisor_pair = (
        (project_root / "var/openshell/toolchains/v0.0.83/bin/openshell-sandbox").as_posix(),
        "/opt/openshell/bin/openshell-sandbox",
    )
    tls_pairs = {
        (
            (project_root / "var/openshell/gateway/siq-openshell-dev/tls/ca.crt").as_posix(),
            "/etc/openshell/tls/client/ca.crt",
        ),
        (
            (project_root / "var/openshell/gateway/siq-openshell-dev/tls/client/tls.crt").as_posix(),
            "/etc/openshell/tls/client/tls.crt",
        ),
        (
            (project_root / "var/openshell/gateway/siq-openshell-dev/tls/client/tls.key").as_posix(),
            "/etc/openshell/tls/client/tls.key",
        ),
    }
    control_pairs = {(str(raw["Source"]), str(raw["Destination"])) for raw in controls}
    if supervisor_pair not in control_pairs or not tls_pairs.issubset(control_pairs):
        raise ProbeError("control_mount_contract_mismatch")
    for source, _ in (supervisor_pair, *sorted(tls_pairs)):
        _require_safe_control_source(Path(source))

    remaining = control_pairs - {supervisor_pair} - tls_pairs
    if len(remaining) != 1:
        raise ProbeError("control_mount_contract_mismatch")
    token_source_raw, token_target = next(iter(remaining))
    token_source = Path(token_source_raw)
    expected_token = (
        project_root
        / "var/openshell/xdg/state/openshell/docker-sandbox-tokens/siq-openshell-dev"
        / sandbox_id
        / "sandbox.jwt"
    )
    if token_target != "/etc/openshell/auth/sandbox.jwt" or token_source != expected_token:
        raise ProbeError("control_mount_contract_mismatch")
    _require_safe_control_source(token_source)


def validate_container_mounts(
    context: ProbeContext | IndependentProbeContext,
    mounts: Sequence[Mapping[str, Any]],
) -> dict[str, int]:
    expected_total = BUSINESS_MOUNT_COUNT + CONTROL_MOUNT_COUNT
    if len(mounts) != expected_total:
        raise ProbeError("docker_mount_count_invalid")

    actual_by_pair: dict[tuple[str, str], Mapping[str, Any]] = {}
    for raw in mounts:
        if not isinstance(raw, dict):
            raise ProbeError("docker_mount_invalid")
        source = raw.get("Source")
        destination = raw.get("Destination")
        if not isinstance(source, str) or not isinstance(destination, str):
            raise ProbeError("docker_mount_invalid")
        key = (source, destination)
        if key in actual_by_pair:
            raise ProbeError("docker_mount_duplicate")
        actual_by_pair[key] = raw

    business = _expected_business_mounts(context)
    consumed: set[tuple[str, str]] = set()
    for expected in business:
        key = (expected["source"], expected["target"])
        actual = actual_by_pair.get(key)
        read_only = expected["read_only"]
        allowed_modes = {"ro"} if read_only else {"", "rw"}
        if (
            actual is None
            or actual.get("Type") != "bind"
            or actual.get("RW") is not (not read_only)
            or actual.get("Mode") not in allowed_modes
            or actual.get("Propagation") != "rprivate"
        ):
            raise ProbeError("business_mount_runtime_mismatch")
        consumed.add(key)

    controls = [raw for key, raw in actual_by_pair.items() if key not in consumed]
    _validate_control_mounts(context.project_root, context.sandbox_id, controls)
    return {
        "business_mount_count": BUSINESS_MOUNT_COUNT,
        "control_mount_count": CONTROL_MOUNT_COUNT,
        "total_mount_count": expected_total,
    }


FILESYSTEM_PROBE = r"""
import errno
import json
import os
import stat
import sys
from pathlib import Path

root = Path(sys.argv[1])
analysis = Path(sys.argv[2])
hermes_home = Path(sys.argv[3])
runtime_state = Path(sys.argv[4])
name = sys.argv[5]
marker = sys.argv[6].encode("ascii")
denied = {errno.EACCES, errno.EPERM, errno.EROFS}

def fail(code):
    print(json.dumps({"ok": False, "error_code": code}, sort_keys=True))
    raise SystemExit(2)

def require_regular_readable(path, code):
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or path.is_symlink():
            fail(code)
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            os.read(fd, 1)
        finally:
            os.close(fd)
    except OSError:
        fail(code)

def require_write_open_denied(path, code):
    try:
        fd = os.open(path, os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        if exc.errno in denied:
            return
        fail(code)
    else:
        os.close(fd)
        fail(code)

def require_create_denied(directory, code):
    candidate = directory / name
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(candidate, flags, 0o600)
    except OSError as exc:
        if exc.errno in denied:
            return
        fail(code)
    else:
        try:
            os.write(fd, marker)
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            candidate.unlink()
        except OSError:
            pass
        fail(code)

def require_sensitive_read_denied(path, code):
    if not os.path.lexists(path):
        return
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        if exc.errno in denied:
            return
        fail(code)
    else:
        os.close(fd)
        fail(code)

def create_positive(path, code):
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o600)
        try:
            view = memoryview(marker)
            while view:
                count = os.write(fd, view)
                if count <= 0:
                    fail(code)
                view = view[count:]
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        fail(code)

def exercise_normal_mutations(directory, code):
    work = directory / (name + ".dir")
    source = work / "source"
    renamed = work / "renamed"
    try:
        work.mkdir(mode=0o700)
        source.write_bytes(marker)
        with source.open("ab") as handle:
            handle.write(b":updated")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(source, renamed)
        if renamed.read_bytes() != marker + b":updated":
            fail(code)
        renamed.unlink()
        work.rmdir()
    except OSError:
        fail(code)

if os.geteuid() == 0:
    fail("sandbox_root_user")

wiki_readme = root / "data/wiki/README.md"
soul = root / "agents/hermes/profiles/siq_analysis/SOUL.md"
profile_config = root / "agents/hermes/profiles/siq_analysis/config.yaml"
workflow_rule = root / "agents/hermes/profiles/siq_analysis/rules/report_workflow.md"
profile_script = root / "agents/hermes/profiles/siq_analysis/scripts/run_analysis_report.py"
hermes_source = Path("/opt/hermes-agent/pyproject.toml")
for path, code in (
    (wiki_readme, "wiki_read_failed"),
    (soul, "soul_read_failed"),
    (profile_config, "profile_config_read_failed"),
    (workflow_rule, "workflow_rule_read_failed"),
    (profile_script, "profile_script_read_failed"),
    (hermes_source, "hermes_source_read_failed"),
):
    require_regular_readable(path, code)
    require_write_open_denied(path, code.replace("read", "write"))

require_create_denied(root / "data/wiki", "wiki_create_allowed")
require_create_denied(soul.parent, "profile_create_allowed")
require_create_denied(hermes_home, "hermes_control_home_create_allowed")
require_create_denied(workflow_rule.parent, "profile_rules_create_allowed")
require_create_denied(profile_script.parent, "profile_scripts_create_allowed")
require_create_denied(hermes_source.parent, "hermes_source_create_allowed")
# OpenShell needs its sandbox JWT and mTLS client key at runtime. Their safety
# boundary is the identity-checked, read-only control mount contract validated
# by the host runner; the agent still cannot modify those files or access any
# other host credential roots.
require_write_open_denied(Path("/etc/openshell/auth/sandbox.jwt"), "sandbox_jwt_writable")
require_write_open_denied(Path("/etc/openshell/tls/client/tls.key"), "sandbox_tls_key_writable")

for path in (
    root / ".env",
    root / "env",
    root / "infra/env",
    root / ".git",
    root / "apps",
    root / "infra",
    root / "scripts",
    root / "start_all.sh",
    hermes_home / ".env",
    hermes_home / "auth.json",
    Path("/home/sandbox/.ssh"),
    Path("/home/sandbox/.aws"),
    Path("/home/sandbox/.kube"),
    Path("/home/sandbox/.docker"),
    Path("/root/.ssh"),
    Path("/root/.aws"),
    Path("/root/.kube"),
    Path("/root/.docker"),
    Path("/var/run/docker.sock"),
    Path("/run/docker.sock"),
):
    if os.path.lexists(path):
        fail("sensitive_path_visible")

for directory, code in (
    (analysis, "analysis_write_failed"),
    (runtime_state, "runtime_state_write_failed"),
    (hermes_home / "sessions", "runtime_write_failed"),
    (hermes_home / "memories", "memory_write_failed"),
):
    exercise_normal_mutations(directory, code)
    create_positive(directory / name, code)
create_positive(Path("/tmp") / (name + ".upload"), "tmp_write_failed")
print(
    json.dumps(
        {
            "ok": True,
            "check": "filesystem",
            "immutable_write_denials": {
                "source_data_read_only": True,
                "code_read_only": True,
                "configuration_read_only": True,
                "prompt_read_only": True,
                "workflow_read_only": True,
            },
            "sensitive_read_denials": {
                "control_credentials_read_only": True,
                "sensitive_paths_hidden": True,
            },
            "allowed_writes": {
                "analysis_bind_read_write": True,
                "runtime_state_directory_bind_read_write": True,
                "runtime_session_bind_read_write": True,
                "runtime_memory_bind_read_write": True,
                "tmp_scratch_write": True,
            },
        },
        sort_keys=True,
    )
)
"""


CLEANUP_PROBE = r"""
import json
import os
import stat
import sys
from pathlib import Path

marker = sys.argv[1].encode("ascii")
paths = [Path(value) for value in sys.argv[2:]]
cleaned = 0
for path in paths:
    try:
        info = path.lstat()
    except FileNotFoundError:
        continue
    except OSError:
        raise SystemExit(2)
    if path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1:
        raise SystemExit(2)
    if info.st_size > len(marker):
        raise SystemExit(2)
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        raise SystemExit(2)
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            raise SystemExit(2)
        content = os.read(fd, len(marker) + 1)
        if os.read(fd, 1):
            raise SystemExit(2)
        finished = os.fstat(fd)
        if (finished.st_size, finished.st_mtime_ns, finished.st_ino) != (
            opened.st_size,
            opened.st_mtime_ns,
            opened.st_ino,
        ):
            raise SystemExit(2)
    finally:
        os.close(fd)
    if content not in (b"", marker):
        raise SystemExit(2)
    try:
        current = path.lstat()
    except OSError:
        raise SystemExit(2)
    if (current.st_dev, current.st_ino, current.st_size) != (info.st_dev, info.st_ino, info.st_size):
        raise SystemExit(2)
    try:
        path.unlink()
    except OSError:
        raise SystemExit(2)
    cleaned += 1
print(json.dumps({"ok": True, "cleaned": cleaned}, sort_keys=True))
"""


DIRECT_UPLOAD_PROBE = r"""
test -x /usr/bin/curl || exit 2
test ! -e "$1" || exit 2
umask 077
printf '%s\n' 'siq-upload-probe' >"$1" || exit 2
trap 'rm -f -- "$1"' EXIT
health_code="$(/usr/bin/curl --fail --silent --show-error \
  --connect-timeout 2 --max-time 5 --output /dev/null --write-out '%{http_code}' \
  'http://host.openshell.internal:18792/health')" || exit 2
test "$health_code" = 200 || exit 2
http_code="$(/usr/bin/curl --proto '=https' --tlsv1.2 --fail --silent --show-error \
  --connect-timeout 2 --max-time 5 --upload-file "$1" \
  --output /dev/null --write-out '%{http_code}' \
  'https://example.com/siq-openshell-upload-probe' 2>/dev/null)"
rc=$?
printf '{"ok":true,"curl_returncode":%s,"http_code":"%s"}\n' "$rc" "$http_code"
exit 0
"""


BROKER_MULTIPART_PROBE = r"""
import json
import urllib.error
import urllib.request

opener = urllib.request.build_opener()

def broker_request(payload):
    request = urllib.request.Request(
        "http://host.openshell.internal:18792/v1/request",
        data=json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("ascii"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with opener.open(request, timeout=8) as response:
            return response.status, response.read(65537)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(65537)

head_status, head_content = broker_request({"method": "HEAD", "url": "https://example.com/"})
if head_status != 200 or len(head_content) > 65536:
    raise SystemExit(2)
try:
    head_result = json.loads(head_content)
except (UnicodeDecodeError, json.JSONDecodeError):
    raise SystemExit(2)
head_upstream_status = head_result.get("status") if isinstance(head_result, dict) else None
if (
    head_result.get("ok") is not True
    or isinstance(head_upstream_status, bool)
    or not isinstance(head_upstream_status, int)
    or not 200 <= head_upstream_status < 400
):
    raise SystemExit(2)

multipart_status, content = broker_request(
    {
        "method": "POST",
        "url": "https://example.com/siq-openshell-upload-probe",
        "headers": {"Content-Type": "multipart/form-data"},
        "json_body": {"probe": "synthetic-marker-only"},
    }
)
if multipart_status != 403 or len(content) > 65536:
    raise SystemExit(2)
try:
    result = json.loads(content)
except (UnicodeDecodeError, json.JSONDecodeError):
    raise SystemExit(2)
egress = result.get("egress") if isinstance(result, dict) else None
if (
    result.get("ok") is not False
    or result.get("error_code") != "broker_multipart_denied"
    or not isinstance(egress, dict)
    or egress.get("rule_id") != "broker_multipart_denied"
    or egress.get("decision") != "deny"
):
    raise SystemExit(2)
print(
    json.dumps(
        {
            "ok": True,
            "public_head_control": True,
            "status": 403,
            "error_code": "broker_multipart_denied",
        },
        sort_keys=True,
    )
)
"""


NEMOTRON_PROBE = r"""
import json
import urllib.request

opener = urllib.request.build_opener()
request = urllib.request.Request("http://host.openshell.internal:8007/v1/models", method="GET")
with opener.open(request, timeout=8) as response:
    content = response.read(131073)
    if response.status != 200 or len(content) > 131072:
        raise SystemExit(2)
try:
    payload = json.loads(content)
except (UnicodeDecodeError, json.JSONDecodeError):
    raise SystemExit(2)
models = payload.get("data") if isinstance(payload, dict) else None
if not isinstance(models, list) or not any(
    isinstance(item, dict) and item.get("id") == "nemotron_3_nano_omni" for item in models
):
    raise SystemExit(2)
print(json.dumps({"ok": True, "status": 200, "model_present": True}, sort_keys=True))
"""


PROVIDER_INDEPENDENT_NETWORK_PROBE = r"""
import errno
import json
import socket

targets = (
    ("public_https", "1.1.1.1", 443),
    ("internal_model", "host.openshell.internal", 8007),
    ("egress_broker", "host.openshell.internal", 18792),
    ("cloud_metadata", "169.254.169.254", 80),
)
observed = {}
policy_errno = {
    errno.EACCES,
    errno.EPERM,
    errno.ENETUNREACH,
    errno.EHOSTUNREACH,
    errno.ETIMEDOUT,
    errno.ECONNREFUSED,
    errno.ECONNRESET,
    errno.ECONNABORTED,
    errno.ENETDOWN,
    errno.ENETRESET,
    errno.EHOSTDOWN,
    errno.EADDRNOTAVAIL,
    errno.EPIPE,
}
policy_gai_errno = {
    value
    for value in (
        getattr(socket, "EAI_AGAIN", None),
        getattr(socket, "EAI_FAIL", None),
        getattr(socket, "EAI_NONAME", None),
        getattr(socket, "EAI_NODATA", None),
    )
    if isinstance(value, int)
}
for label, host, port in targets:
    try:
        connection = socket.create_connection((host, port), timeout=2)
    except socket.gaierror as exc:
        reason = f"gai_{exc.errno}"
        if exc.errno not in policy_gai_errno:
            print(
                json.dumps(
                    {"ok": False, "error_code": "network_control_unavailable", "target": label, "reason": reason},
                    sort_keys=True,
                )
            )
            raise SystemExit(2)
        observed[label] = {"result": "denied", "reason": reason}
    except OSError as exc:
        reason = "timeout" if isinstance(exc, TimeoutError) else errno.errorcode.get(exc.errno, str(exc.errno))
        if not isinstance(exc, TimeoutError) and exc.errno not in policy_errno:
            print(
                json.dumps(
                    {"ok": False, "error_code": "network_control_unavailable", "target": label, "reason": reason},
                    sort_keys=True,
                )
            )
            raise SystemExit(2)
        observed[label] = {"result": "denied", "reason": reason}
    else:
        connection.close()
        print(json.dumps({"ok": False, "error_code": "network_target_allowed"}, sort_keys=True))
        raise SystemExit(2)
print(
    json.dumps(
        {
            "ok": True,
            "check": "network_deny",
            "policy_evidence": "active_network_policies_empty",
            "observed": observed,
        },
        sort_keys=True,
    )
)
"""


PROVIDER_INDEPENDENT_UPLOAD_PROBE = r"""
test -x /usr/bin/curl || exit 2
http_code="$(/usr/bin/curl --proto '=https' --tlsv1.2 --fail --silent --show-error \
  --connect-timeout 2 --max-time 5 --upload-file "$1" \
  --output /dev/null --write-out '%{http_code}' \
  'https://example.com/siq-openshell-provider-independent-upload-probe' 2>/dev/null)"
rc=$?
printf '{"ok":true,"check":"unknown_upload_deny","curl_returncode":%s,"http_code":"%s"}\n' \
  "$rc" "$http_code"
exit 0
"""


PROVIDER_INDEPENDENT_RUNTIME_PROBE = r"""
import json
import os
from pathlib import Path

def process_argv(pid):
    try:
        content = (Path("/proc") / pid / "cmdline").read_bytes().rstrip(b"\0")
    except OSError:
        return []
    return [item.decode("utf-8", errors="replace") for item in content.split(b"\0") if item]

hermes_processes = []
for entry in Path("/proc").iterdir():
    if not entry.name.isdigit() or entry.name == str(os.getpid()):
        continue
    argv = process_argv(entry.name)
    if not argv:
        continue
    exact_entrypoint = any(item in {"/opt/siq/entrypoint.sh", "/opt/siq/hermes/venv/bin/hermes"} for item in argv)
    gateway_run = any(argv[index:index + 2] == ["gateway", "run"] for index in range(len(argv) - 1))
    if exact_entrypoint or gateway_run:
        hermes_processes.append(entry.name)

listen_port = format(28651, "04X")
api_listeners = 0
for table in (Path("/proc/net/tcp"), Path("/proc/net/tcp6")):
    try:
        lines = table.read_text(encoding="ascii").splitlines()[1:]
    except OSError:
        continue
    for line in lines:
        fields = line.split()
        if len(fields) > 3 and fields[1].rsplit(":", 1)[-1].upper() == listen_port and fields[3] == "0A":
            api_listeners += 1

provider_fragments = ("ANTHROPIC", "EXA", "KIMI", "MINIMAX", "OPENAI", "PROVIDER", "STEPFUN", "TAVILY")
provider_env = [
    name
    for name in os.environ
    if name == "API_SERVER_KEY"
    or name.endswith("_API_KEY")
    or any(fragment in name.upper() for fragment in provider_fragments)
]
auth_paths = (
    Path(os.environ.get("HERMES_HOME", "/nonexistent")) / "auth.json",
    Path(os.environ.get("HERMES_HOME", "/nonexistent")) / ".env",
    Path("/sandbox/runtime-auth/auth.json"),
    Path("/etc/openshell/providers"),
)
auth_material = [str(path) for path in auth_paths if os.path.lexists(path)]
result = {
    "ok": not hermes_processes and api_listeners == 0 and not provider_env and not auth_material,
    "check": "runtime_isolation",
    "hermes_process_count": len(hermes_processes),
    "api_listener_count": api_listeners,
    "provider_env_count": len(provider_env),
    "auth_material_count": len(auth_material),
    "provider_call_capable_processes": len(hermes_processes),
}
if not result["ok"]:
    result["error_code"] = "provider_independent_runtime_present"
print(json.dumps(result, sort_keys=True))
raise SystemExit(0 if result["ok"] else 2)
"""


PROCESS_HARDENING_PROBE = r"""
import ctypes
import errno
import json
import os
import pwd
import stat
import subprocess
import tempfile
from pathlib import Path

EXPECTED_UID = 1000
EXPECTED_GID = 1000
CLONE_NEWNS = 0x00020000
DENIED_ERRNOS = {errno.EACCES, errno.EPERM}
CAPABILITY_FIELDS = ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb")

def fail(code):
    print(json.dumps({"ok": False, "error_code": code}, sort_keys=True))
    raise SystemExit(2)

def require_denied_call(label, action):
    try:
        action()
    except OSError as exc:
        if exc.errno in DENIED_ERRNOS:
            return
        fail(label + "_unexpected_errno")
    fail(label + "_allowed")

def require_denied_syscall(label, action):
    ctypes.set_errno(0)
    if action() == -1:
        if ctypes.get_errno() in DENIED_ERRNOS:
            return
        fail(label + "_unexpected_errno")
    fail(label + "_allowed")

def require_command_denied(path, arguments, label):
    if not path.exists():
        return
    try:
        result = subprocess.run(
            [str(path), *arguments],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env={"HOME": "/tmp", "LC_ALL": "C", "PATH": "/usr/sbin:/usr/bin:/sbin:/bin"},
            timeout=3,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return
    except OSError as exc:
        if exc.errno in {errno.EACCES, errno.ENOENT, errno.EPERM}:
            return
        fail(label + "_exec_failed")
    if result.returncode == 0:
        fail(label + "_allowed")

uids = os.getresuid()
gids = os.getresgid()
if uids != (EXPECTED_UID, EXPECTED_UID, EXPECTED_UID):
    fail("non_root_identity_invalid")
if gids != (EXPECTED_GID, EXPECTED_GID, EXPECTED_GID):
    fail("non_root_group_invalid")
try:
    if pwd.getpwuid(EXPECTED_UID).pw_name != "sandbox":
        fail("sandbox_identity_invalid")
except KeyError:
    fail("sandbox_identity_invalid")
if 0 in os.getgroups():
    fail("root_group_present")

status = {}
try:
    for line in Path("/proc/self/status").read_text(encoding="ascii").splitlines():
        key, separator, value = line.partition(":")
        if separator:
            status[key] = value.strip()
except OSError:
    fail("process_status_unreadable")
if status.get("NoNewPrivs") != "1":
    fail("no_new_privs_disabled")
try:
    capability_values = {name: int(status[name], 16) for name in CAPABILITY_FIELDS}
except (KeyError, ValueError):
    fail("capability_status_invalid")
if any(capability_values.values()):
    fail("capability_set_not_empty")

for socket_path in (Path("/var/run/docker.sock"), Path("/run/docker.sock")):
    if os.path.lexists(socket_path):
        fail("docker_socket_visible")

require_command_denied(Path("/usr/bin/sudo"), ["-n", "/usr/bin/id", "-u"], "sudo")
require_command_denied(
    Path("/usr/bin/su"),
    ["-s", "/bin/sh", "-c", "/usr/bin/id -u", "root"],
    "su",
)
require_denied_call("setuid_root", lambda: os.setuid(0))
require_denied_call("setgid_root", lambda: os.setgid(0))

libc = ctypes.CDLL(None, use_errno=True)
libc.mount.argtypes = (
    ctypes.c_char_p,
    ctypes.c_char_p,
    ctypes.c_char_p,
    ctypes.c_ulong,
    ctypes.c_void_p,
)
libc.mount.restype = ctypes.c_int
libc.umount2.argtypes = (ctypes.c_char_p, ctypes.c_int)
libc.umount2.restype = ctypes.c_int
libc.unshare.argtypes = (ctypes.c_int,)
libc.unshare.restype = ctypes.c_int
libc.setns.argtypes = (ctypes.c_int, ctypes.c_int)
libc.setns.restype = ctypes.c_int

mount_target = Path(tempfile.mkdtemp(prefix="siq-process-hardening-mount-", dir="/tmp"))
try:
    target_bytes = os.fsencode(mount_target)
    ctypes.set_errno(0)
    mount_result = libc.mount(b"none", target_bytes, b"tmpfs", 0, None)
    if mount_result == 0:
        libc.umount2(target_bytes, 2)
        fail("mount_allowed")
    if ctypes.get_errno() not in DENIED_ERRNOS:
        fail("mount_unexpected_errno")
finally:
    try:
        mount_target.rmdir()
    except OSError:
        pass

require_denied_syscall("unshare", lambda: libc.unshare(CLONE_NEWNS))
try:
    namespace_fd = os.open("/proc/1/ns/mnt", os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
except OSError as exc:
    if exc.errno not in DENIED_ERRNOS:
        fail("setns_target_unreadable")
else:
    try:
        require_denied_syscall("setns", lambda: libc.setns(namespace_fd, CLONE_NEWNS))
    finally:
        os.close(namespace_fd)

dangerous_device_patterns = (
    "/dev/bpf*",
    "/dev/dm-*",
    "/dev/dri*",
    "/dev/fuse",
    "/dev/kvm",
    "/dev/loop*",
    "/dev/mem",
    "/dev/kmem",
    "/dev/kmsg",
    "/dev/mmcblk*",
    "/dev/net/tun",
    "/dev/nvme*",
    "/dev/nvidia*",
    "/dev/port",
    "/dev/raw*",
    "/dev/sd*",
    "/dev/vd*",
    "/dev/vhost*",
    "/dev/xvd*",
)
for pattern in dangerous_device_patterns:
    parent = Path(pattern).parent
    if any(os.path.lexists(candidate) for candidate in parent.glob(Path(pattern).name)):
        fail("dangerous_raw_device_visible")
for root, directories, files in os.walk("/dev", followlinks=False):
    for name in (*directories, *files):
        path = Path(root) / name
        try:
            mode = path.lstat().st_mode
        except OSError:
            fail("device_tree_unreadable")
        if stat.S_ISBLK(mode):
            fail("block_device_visible")

device_directory = Path(tempfile.mkdtemp(prefix="siq-process-hardening-device-", dir="/tmp"))
device_path = device_directory / "probe-block-device"
try:
    require_denied_call(
        "block_device_create",
        lambda: os.mknod(device_path, stat.S_IFBLK | 0o600, os.makedev(1, 0)),
    )
finally:
    try:
        device_path.unlink()
    except OSError:
        pass
    try:
        device_directory.rmdir()
    except OSError:
        pass

controls = {
    "non_root_identity": True,
    "root_group_absent": True,
    "no_new_privs": True,
    "capability_sets_clear": True,
    "docker_socket_inaccessible": True,
    "sudo_denied": True,
    "su_denied": True,
    "setuid_root_denied": True,
    "setgid_root_denied": True,
    "mount_denied": True,
    "unshare_denied": True,
    "setns_denied": True,
    "raw_devices_absent": True,
    "block_device_create_denied": True,
}
print(
    json.dumps(
        {
            "ok": True,
            "schema_version": "siq.openshell.process_hardening.v1",
            "check": "process_hardening",
            "uid": os.geteuid(),
            "gid": os.getegid(),
            "controls": controls,
        },
        sort_keys=True,
    )
)
"""


def _sandbox_exec(
    context: ProbeContext | IndependentProbeContext,
    command: Sequence[str],
    *,
    timeout: int,
    code: str,
    require_success: bool = True,
) -> subprocess.CompletedProcess[str]:
    run_cli = context.project_root / "scripts/openshell/run_cli.sh"
    if not run_cli.is_file() or run_cli.is_symlink() or not os.access(run_cli, os.X_OK):
        raise ProbeError("openshell_wrapper_invalid")
    result = _run_command(
        [
            str(run_cli),
            "sandbox",
            "exec",
            "--name",
            context.sandbox_name,
            "--timeout",
            str(timeout),
            "--no-tty",
            "--",
            *command,
        ],
        timeout=timeout + 5,
        code=code,
    )
    if require_success and (result.returncode != 0 or result.stderr.strip()):
        raise ProbeError(code)
    return result


def _record_independent_exec_failure(
    context: ProbeContext | IndependentProbeContext,
    *,
    code: str,
    result: subprocess.CompletedProcess[str],
) -> None:
    if not isinstance(context, IndependentProbeContext):
        return
    stdout = result.stdout[:8192]
    stderr = result.stderr[:8192]
    diagnostic = {
        "schema_version": "siq.openshell.provider_independent_exec_failure.v1",
        "stage": code,
        "returncode": result.returncode,
        "stdout": stdout,
        "stdout_sha256": _sha256_bytes(result.stdout.encode("utf-8", errors="replace")),
        "stdout_truncated": len(result.stdout) > len(stdout),
        "stderr": stderr,
        "stderr_sha256": _sha256_bytes(result.stderr.encode("utf-8", errors="replace")),
        "stderr_truncated": len(result.stderr) > len(stderr),
    }
    safe_stage = re.sub(r"[^a-z0-9_.-]", "_", code.lower())[:48] or "unknown"
    try:
        _lifecycle_write_json(
            context.state_dir / f"exec-failure-{safe_stage}.json",
            diagnostic,
            root=context.project_root,
        )
    except (LifecycleError, OSError):
        pass


def _sandbox_exec_json(
    context: ProbeContext | IndependentProbeContext,
    command: Sequence[str],
    *,
    timeout: int,
    code: str,
) -> Mapping[str, Any]:
    result = _sandbox_exec(
        context,
        command,
        timeout=timeout,
        code=code,
        require_success=False,
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        _record_independent_exec_failure(context, code=code, result=result)
        if result.returncode != 0 or result.stderr.strip():
            raise ProbeError(code) from exc
        raise ProbeError(f"{code}_response_invalid") from exc
    if result.returncode != 0 or result.stderr.strip():
        _record_independent_exec_failure(context, code=code, result=result)
        embedded_code = payload.get("error_code") if isinstance(payload, dict) else None
        combined_code = f"{code}.{embedded_code}"
        if isinstance(embedded_code, str) and re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,63}", embedded_code):
            if len(combined_code) <= 96:
                raise ProbeError(combined_code)
        raise ProbeError(code)
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        _record_independent_exec_failure(context, code=code, result=result)
        raise ProbeError(f"{code}_response_invalid")
    return payload


def _verify_host_sentinel(path: Path, marker: bytes, *, code: str) -> str:
    _assert_no_symlink_components(path, code=code)
    try:
        expected = path.lstat()
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise ProbeError(code) from exc
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or opened.st_uid != os.geteuid()
            or stat.S_IMODE(opened.st_mode) & 0o077
            or (opened.st_dev, opened.st_ino) != (expected.st_dev, expected.st_ino)
        ):
            raise ProbeError(code)
        content = os.read(descriptor, len(marker) + 1)
        if content != marker or os.read(descriptor, 1):
            raise ProbeError(code)
        finished = os.fstat(descriptor)
        final = path.lstat()
        identity = (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
        if identity != (finished.st_dev, finished.st_ino, finished.st_size, finished.st_mtime_ns) or identity != (
            final.st_dev,
            final.st_ino,
            final.st_size,
            final.st_mtime_ns,
        ):
            raise ProbeError(code)
    except OSError as exc:
        raise ProbeError(code) from exc
    finally:
        os.close(descriptor)
    receipt = {
        "device": opened.st_dev,
        "inode": opened.st_ino,
        "size": opened.st_size,
        "mode": stat.S_IMODE(opened.st_mode),
        "uid": opened.st_uid,
        "content_sha256": _sha256_bytes(content),
    }
    return _sha256_bytes(json.dumps(receipt, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("ascii"))


def _safe_host_cleanup(path: Path, marker: bytes) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise ProbeError("probe_cleanup_failed") from exc
    if path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1:
        raise ProbeError("probe_cleanup_failed")
    content = _read_regular_file(path, code="probe_cleanup_failed", max_bytes=len(marker) + 1)
    if content not in (b"", marker):
        raise ProbeError("probe_cleanup_failed")
    try:
        current = path.lstat()
    except OSError as exc:
        raise ProbeError("probe_cleanup_failed") from exc
    if (current.st_dev, current.st_ino, current.st_size) != (info.st_dev, info.st_ino, info.st_size):
        raise ProbeError("probe_cleanup_failed")
    try:
        path.unlink()
    except OSError as exc:
        raise ProbeError("probe_cleanup_failed") from exc


def _cleanup(
    context: ProbeContext | IndependentProbeContext,
    sentinels: SentinelPaths,
    *,
    timeout: int,
) -> None:
    sandbox_paths = (
        sentinels.analysis_sandbox,
        sentinels.state_sandbox,
        sentinels.runtime_sandbox,
        sentinels.memory_sandbox,
        sentinels.wiki_sandbox,
        sentinels.upload_sandbox,
        sentinels.profile_sandbox,
        sentinels.scripts_sandbox,
        sentinels.hermes_source_sandbox,
    )
    sandbox_error = False
    try:
        _sandbox_exec_json(
            context,
            [
                SANDBOX_PYTHON,
                "-c",
                CLEANUP_PROBE,
                sentinels.marker.decode("ascii"),
                *(path.as_posix() for path in sandbox_paths),
            ],
            timeout=timeout,
            code="probe_cleanup_exec_failed",
        )
    except ProbeError:
        sandbox_error = True

    host_error = False
    for path in (
        sentinels.analysis_host,
        sentinels.state_host,
        sentinels.runtime_host,
        sentinels.memory_host,
        sentinels.wiki_host,
    ):
        try:
            _safe_host_cleanup(path, sentinels.marker)
        except ProbeError:
            host_error = True
    if sandbox_error or host_error:
        raise ProbeError("probe_cleanup_failed")
    if any(
        path.exists() or path.is_symlink()
        for path in (
            sentinels.analysis_host,
            sentinels.state_host,
            sentinels.runtime_host,
            sentinels.memory_host,
            sentinels.wiki_host,
        )
    ):
        raise ProbeError("probe_cleanup_failed")


def _security_probe_manifest(plan: SecurityProbePlan, nonce: str) -> dict[str, Any]:
    spec = plan.spec
    return {
        "schema_version": INDEPENDENT_SCHEMA_VERSION,
        "mode": "provider-independent",
        "phase": "prepared",
        "profile": PROFILE,
        "probe_id": spec.run_id,
        "market": spec.market,
        "company": spec.company,
        "analysis_relative_path": spec.analysis_relative_path,
        "sandbox_name": spec.sandbox_name,
        "namespace": NAMESPACE,
        "formal_business_sandbox": False,
        "host_runtime_unchanged": True,
        "providers": [],
        "provider_calls": None,
        "provider_calls_observed": False,
        "runtime_isolation": {},
        "network_isolation": {},
        "container_hardening": {},
        "process_hardening": {},
        "quality_validated": False,
        "readiness_effect": "none",
        "image_ref": plan.image_ref,
        "image_id": plan.image_id,
        "runtime_snapshot": plan.runtime_snapshot.relative_to(spec.project_root).as_posix(),
        "mount_plan": plan.mount_plan.relative_to(spec.project_root).as_posix(),
        "mount_plan_sha256": plan.mount_plan_sha256,
        "mount_count": BUSINESS_MOUNT_COUNT,
        "policy": plan.policy_path.relative_to(spec.project_root).as_posix(),
        "policy_sha256": plan.policy_sha256,
        "network_mode": "deny-all",
        "run_nonce_sha256": _sha256_bytes(nonce.encode("ascii")),
        "sandbox_id": "",
        "container_id": "",
        "sentinel_name": "",
        "sentinel_marker": "",
        "mount_observation": [],
        "checks": [],
        "mounts": {},
        "error_code": "",
        "cleanup_error_code": "",
    }


def _independent_context(
    plan: SecurityProbePlan,
    identity: SandboxIdentity,
    nonce: str,
    manifest: Mapping[str, Any],
) -> IndependentProbeContext:
    policy = _read_json_file(plan.policy_path, code="security_probe_policy_invalid", private=True)
    if (
        policy.get("network_policies") != {}
        or _sha256_bytes(_read_regular_file(plan.policy_path, code="security_probe_policy_invalid", private=True))
        != plan.policy_sha256
    ):
        raise ProbeError("security_probe_policy_invalid")
    return IndependentProbeContext(
        project_root=plan.spec.project_root,
        probe_id=plan.spec.run_id,
        state_dir=plan.spec.run_dir,
        sandbox_name=plan.spec.sandbox_name,
        sandbox_id=identity.sandbox_id,
        container_id=identity.container_id,
        nonce=nonce,
        analysis_path=plan.spec.analysis_root,
        runtime_snapshot=plan.runtime_snapshot,
        mount_plan=plan.mount_plan,
        mount_plan_sha256=plan.mount_plan_sha256,
        policy_path=plan.policy_path,
        policy=policy,
        manifest=manifest,
    )


def _remove_private_tree(path: Path, *, project_root: Path) -> None:
    _assert_no_symlink_components(path, code="security_probe_state_cleanup_failed")
    try:
        path.relative_to(project_root)
    except ValueError as exc:
        raise ProbeError("security_probe_state_cleanup_failed") from exc
    try:
        root_info = path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise ProbeError("security_probe_state_cleanup_failed") from exc
    if (
        not stat.S_ISDIR(root_info.st_mode)
        or root_info.st_uid != os.geteuid()
        or stat.S_IMODE(root_info.st_mode) & 0o077
    ):
        raise ProbeError("security_probe_state_cleanup_failed")
    file_plan: list[tuple[Path, tuple[int, int, int, int]]] = []
    directory_plan: list[tuple[Path, tuple[int, int]]] = []
    for current_raw, directories, files in os.walk(path, topdown=False, followlinks=False):
        current = Path(current_raw)
        for name in files:
            candidate = current / name
            info = candidate.lstat()
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) & 0o077
            ):
                raise ProbeError("security_probe_state_cleanup_failed")
            file_plan.append((candidate, (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)))
        for name in directories:
            candidate = current / name
            info = candidate.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
                raise ProbeError("security_probe_state_cleanup_failed")
            directory_plan.append((candidate, (info.st_dev, info.st_ino)))
    for candidate, expected in file_plan:
        info = candidate.lstat()
        if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) != expected:
            raise ProbeError("security_probe_state_cleanup_failed")
        candidate.unlink()
    for candidate, expected in directory_plan:
        info = candidate.lstat()
        if (info.st_dev, info.st_ino) != expected or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
            raise ProbeError("security_probe_state_cleanup_failed")
        candidate.rmdir()
    current_root = path.lstat()
    if (current_root.st_dev, current_root.st_ino) != (root_info.st_dev, root_info.st_ino):
        raise ProbeError("security_probe_state_cleanup_failed")
    path.rmdir()


def _load_provider_independent_recovery_context(
    *,
    project_root: Path,
    probe_id: str,
) -> tuple[dict[str, Any], str, Path, SandboxIdentity]:
    state_dir = project_root / SECURITY_PROBES_RELATIVE / probe_id
    manifest = _read_json_file(state_dir / "probe.json", code="security_probe_recovery_intent_invalid", private=True)
    try:
        nonce = (
            _read_regular_file(
                state_dir / "run.nonce",
                code="security_probe_recovery_intent_invalid",
                private=True,
                max_bytes=128,
            )
            .decode("ascii")
            .strip()
        )
    except UnicodeDecodeError as exc:
        raise ProbeError("security_probe_recovery_intent_invalid") from exc
    if not NONCE_RE.fullmatch(nonce):
        raise ProbeError("security_probe_recovery_intent_invalid")
    if (
        manifest.get("schema_version") != INDEPENDENT_SCHEMA_VERSION
        or manifest.get("mode") != "provider-independent"
        or manifest.get("phase") not in {"prepared", "sandbox_created", "failed", "cleanup_incomplete"}
        or manifest.get("profile") != PROFILE
        or manifest.get("probe_id") != probe_id
        or manifest.get("namespace") != NAMESPACE
        or manifest.get("sandbox_name") != f"siq-analysis-security-{probe_id}"
        or manifest.get("formal_business_sandbox") is not False
        or manifest.get("host_runtime_unchanged") is not True
        or manifest.get("quality_validated") is not False
        or manifest.get("readiness_effect") != "none"
        or manifest.get("run_nonce_sha256") != _sha256_bytes(nonce.encode("ascii"))
    ):
        raise ProbeError("security_probe_recovery_intent_invalid")

    sandbox_id = str(manifest.get("sandbox_id") or "")
    container_id = str(manifest.get("container_id") or "")
    if (sandbox_id and not UUID_RE.fullmatch(sandbox_id)) or (
        container_id and not CONTAINER_ID_RE.fullmatch(container_id)
    ):
        raise ProbeError("security_probe_recovery_intent_invalid")

    return (
        manifest,
        nonce,
        state_dir,
        SandboxIdentity(sandbox_id=sandbox_id, container_id=container_id),
    )


def run_provider_independent_probe(
    *,
    project_root: Path,
    profile: str,
    market: str,
    company: str,
    probe_id: str,
    timeout: int,
    adapter: LifecycleAdapter | None = None,
) -> dict[str, Any]:
    if not SECURITY_PROBE_ID_RE.fullmatch(probe_id):
        raise ProbeError("probe_id_invalid")
    spec = None
    state_preexisting = True
    snapshot_preexisting = True
    try:
        project_root = project_root.resolve(strict=True)
        lifecycle = adapter or LifecycleAdapter(project_root=project_root)
        spec = lifecycle.security_probe_spec(
            profile=profile,
            market=market,
            company=company,
            probe_id=probe_id,
        )
        state_preexisting = spec.run_dir.exists() or spec.run_dir.is_symlink()
        snapshot_path = project_root / SNAPSHOTS_RELATIVE / probe_id
        snapshot_preexisting = snapshot_path.exists() or snapshot_path.is_symlink()
        plan = lifecycle.prepare_security_probe_runtime(spec)
    except (LifecycleError, OSError) as exc:
        code = exc.code if isinstance(exc, LifecycleError) else "security_probe_prepare_failed"
        cleanup_code = ""
        if spec is not None:
            cleanup_targets = (
                (project_root / SNAPSHOTS_RELATIVE / probe_id, snapshot_preexisting),
                (spec.run_dir, state_preexisting),
            )
            for path, preexisting in cleanup_targets:
                if preexisting:
                    continue
                try:
                    _remove_private_tree(path, project_root=project_root)
                except (ProbeError, OSError):
                    cleanup_code = "security_probe_prepare_cleanup_failed"
        raise ProbeError(code, cleanup_code=cleanup_code) from exc

    nonce = secrets.token_hex(24)
    nonce_path = spec.run_dir / "run.nonce"
    manifest_path = spec.run_dir / "probe.json"
    manifest = _security_probe_manifest(plan, nonce)
    try:
        _lifecycle_write_private_atomic(nonce_path, f"{nonce}\n".encode("ascii"), root=project_root)
        _lifecycle_write_json(manifest_path, manifest, root=project_root)
    except (LifecycleError, OSError) as exc:
        raise ProbeError("security_probe_intent_write_failed") from exc

    checks: list[str] = []
    mount_counts: dict[str, int] = {}
    runtime_isolation: dict[str, int] = {}
    network_isolation: dict[str, Mapping[str, Any]] = {}
    container_hardening: dict[str, Any] = {}
    process_hardening: dict[str, Any] = {}
    provider_calls: int | None = None
    identity: SandboxIdentity | None = None
    context: IndependentProbeContext | None = None
    sentinels: SentinelPaths | None = None
    sentinels_may_exist = False
    create_attempted = False
    primary_error: ProbeError | None = None
    cleanup_error = ""
    try:
        create_attempted = True
        identity = lifecycle.create_security_probe_sandbox(
            probe_id=probe_id,
            nonce=nonce,
            image_ref=plan.image_ref,
            mount_plan=plan.mount_plan,
            policy_path=plan.policy_path,
        )
        manifest.update(
            {
                "phase": "sandbox_created",
                "sandbox_id": identity.sandbox_id,
                "container_id": identity.container_id,
            }
        )
        _lifecycle_write_json(manifest_path, manifest, root=project_root)
        context = _independent_context(plan, identity, nonce, manifest)

        _validate_active_policy(context, timeout=timeout)
        checks.append("active_policy_matches_deny_all_manifest")
        inspection = _docker_inspect_container(context, timeout=timeout)
        mounts = list(inspection["mounts"])
        manifest["mount_observation"] = _mount_observation(mounts)
        container_hardening = validate_container_hardening(inspection)
        manifest["container_hardening"] = container_hardening
        _lifecycle_write_json(manifest_path, manifest, root=project_root)
        mount_counts = validate_container_mounts(context, mounts)
        checks.extend(
            (
                "strict_bind_contract_7_plus_5",
                "container_not_privileged",
                "supervisor_capability_contract_exact",
                "no_host_devices",
                "supervisor_security_opt_exact",
                "supervisor_bootstrap_user_exact",
            )
        )

        sentinels = SentinelPaths.build(context)
        for host_path in (
            sentinels.analysis_host,
            sentinels.state_host,
            sentinels.runtime_host,
            sentinels.memory_host,
            sentinels.wiki_host,
        ):
            if host_path.exists() or host_path.is_symlink():
                raise ProbeError("probe_sentinel_conflict")
        manifest.update(
            {
                "sentinel_name": sentinels.name,
                "sentinel_marker": sentinels.marker.decode("ascii"),
            }
        )
        _lifecycle_write_json(manifest_path, manifest, root=project_root)
        sentinels_may_exist = True
        filesystem = _sandbox_exec_json(
            context,
            [
                SANDBOX_PYTHON,
                "-c",
                FILESYSTEM_PROBE,
                SANDBOX_ROOT.as_posix(),
                (SANDBOX_ROOT / spec.analysis_relative_path).as_posix(),
                SANDBOX_HERMES_HOME.as_posix(),
                SANDBOX_RUNTIME_STATE_ROOT.as_posix(),
                sentinels.name,
                sentinels.marker.decode("ascii"),
            ],
            timeout=timeout,
            code="provider_independent_filesystem_probe_failed",
        )
        if filesystem.get("check") != "filesystem":
            raise ProbeError("filesystem_probe_response_invalid")
        checks.extend(
            (
                "source_data_read_only",
                "code_read_only",
                "configuration_read_only",
                "prompt_read_only",
                "workflow_read_only",
                "control_credentials_read_only",
                "sensitive_paths_hidden",
            )
        )
        _verify_host_sentinel(
            sentinels.analysis_host,
            sentinels.marker,
            code="analysis_bind_visibility_failed",
        )
        checks.append("analysis_bind_read_write")
        _verify_host_sentinel(
            sentinels.state_host,
            sentinels.marker,
            code="runtime_state_bind_visibility_failed",
        )
        checks.append("runtime_state_directory_bind_read_write")
        _verify_host_sentinel(
            sentinels.runtime_host,
            sentinels.marker,
            code="runtime_bind_visibility_failed",
        )
        checks.append("runtime_session_bind_read_write")
        _verify_host_sentinel(
            sentinels.memory_host,
            sentinels.marker,
            code="memory_bind_visibility_failed",
        )
        checks.append("memory_path_read_write")

        process = _sandbox_exec_json(
            context,
            [SANDBOX_PYTHON, "-c", PROCESS_HARDENING_PROBE],
            timeout=timeout,
            code="provider_independent_process_hardening_probe_failed",
        )
        controls = process.get("controls")
        if (
            process.get("schema_version") != PROCESS_HARDENING_SCHEMA_VERSION
            or process.get("check") != "process_hardening"
            or process.get("uid") != 1000
            or process.get("gid") != 1000
            or not isinstance(controls, dict)
            or set(controls) != set(PROCESS_HARDENING_CONTROLS)
            or any(controls.get(name) is not True for name in PROCESS_HARDENING_CONTROLS)
        ):
            raise ProbeError("provider_independent_process_hardening_probe_invalid")
        process_hardening = {
            "schema_version": PROCESS_HARDENING_SCHEMA_VERSION,
            "uid": 1000,
            "gid": 1000,
            **{name: True for name in PROCESS_HARDENING_CONTROLS},
        }
        manifest["process_hardening"] = process_hardening
        _lifecycle_write_json(manifest_path, manifest, root=project_root)
        checks.extend(PROCESS_HARDENING_CONTROLS)

        network = _sandbox_exec_json(
            context,
            [SANDBOX_PYTHON, "-c", PROVIDER_INDEPENDENT_NETWORK_PROBE],
            timeout=timeout,
            code="provider_independent_network_probe_failed",
        )
        observed_network = network.get("observed")
        expected_network_targets = {
            "public_https",
            "internal_model",
            "egress_broker",
            "cloud_metadata",
        }
        if (
            network.get("check") != "network_deny"
            or network.get("policy_evidence") != "active_network_policies_empty"
            or not isinstance(observed_network, dict)
            or set(observed_network) != expected_network_targets
            or any(
                not isinstance(value, dict)
                or value.get("result") != "denied"
                or not isinstance(value.get("reason"), str)
                for value in observed_network.values()
            )
        ):
            raise ProbeError("provider_independent_network_probe_invalid")
        network_isolation = {str(name): dict(value) for name, value in observed_network.items()}
        checks.extend(
            (
                "public_network_denied",
                "internal_model_network_denied",
                "egress_broker_network_denied",
                "cloud_metadata_network_denied",
            )
        )

        upload = _sandbox_exec_json(
            context,
            [
                "/bin/sh",
                "-c",
                PROVIDER_INDEPENDENT_UPLOAD_PROBE,
                "siq-provider-independent-upload-probe",
                sentinels.upload_sandbox.as_posix(),
            ],
            timeout=timeout,
            code="provider_independent_upload_probe_failed",
        )
        curl_returncode = upload.get("curl_returncode")
        if (
            upload.get("check") != "unknown_upload_deny"
            or isinstance(curl_returncode, bool)
            or not isinstance(curl_returncode, int)
            or not 1 <= curl_returncode <= 255
            or upload.get("http_code") != "000"
        ):
            raise ProbeError("provider_independent_unknown_upload_allowed")
        checks.append("unknown_curl_upload_denied")

        runtime = _sandbox_exec_json(
            context,
            [SANDBOX_PYTHON, "-c", PROVIDER_INDEPENDENT_RUNTIME_PROBE],
            timeout=timeout,
            code="provider_independent_runtime_probe_failed",
        )
        expected_runtime_keys = (
            "hermes_process_count",
            "api_listener_count",
            "provider_env_count",
            "auth_material_count",
            "provider_call_capable_processes",
        )
        if runtime.get("check") != "runtime_isolation" or any(runtime.get(name) != 0 for name in expected_runtime_keys):
            raise ProbeError("provider_independent_runtime_probe_invalid")
        runtime_isolation = {name: int(runtime[name]) for name in expected_runtime_keys}
        provider_calls = runtime_isolation["provider_call_capable_processes"]
        manifest.update(
            {
                "provider_calls": provider_calls,
                "provider_calls_observed": True,
                "runtime_isolation": runtime_isolation,
                "network_isolation": network_isolation,
            }
        )
        _lifecycle_write_json(manifest_path, manifest, root=project_root)
        checks.extend(
            (
                "no_hermes_process",
                "no_hermes_api_listener",
                "no_provider_environment",
                "no_provider_auth_material",
            )
        )
    except ProbeError as exc:
        primary_error = exc
    except LifecycleError as exc:
        primary_error = ProbeError(exc.code)
    except Exception:
        primary_error = ProbeError("probe_internal_error")
    finally:
        if sentinels_may_exist and context is not None and sentinels is not None:
            try:
                _cleanup(context, sentinels, timeout=timeout)
                checks.append("probe_sentinels_removed")
            except ProbeError as exc:
                cleanup_error = exc.code
        if create_attempted:
            try:
                lifecycle.delete_security_probe_sandbox(
                    probe_id=probe_id,
                    nonce=nonce,
                    expected_sandbox_id=identity.sandbox_id if identity is not None else "",
                    expected_container_id=identity.container_id if identity is not None else "",
                )
                checks.append("verified_probe_sandbox_deleted")
            except LifecycleError:
                cleanup_error = cleanup_error or "security_probe_sandbox_cleanup_failed"
        if not cleanup_error:
            try:
                _remove_private_tree(plan.runtime_snapshot, project_root=project_root)
                checks.append("runtime_snapshot_removed")
            except (ProbeError, OSError):
                cleanup_error = "security_probe_runtime_snapshot_cleanup_failed"
        if not cleanup_error:
            try:
                _lifecycle_remove_private(nonce_path, root=project_root)
            except (LifecycleError, OSError):
                cleanup_error = "security_probe_nonce_cleanup_failed"

        manifest.update(
            {
                "phase": "cleanup_incomplete" if cleanup_error else ("passed" if primary_error is None else "failed"),
                "checks": checks,
                "mounts": mount_counts,
                "runtime_isolation": runtime_isolation,
                "network_isolation": network_isolation,
                "container_hardening": container_hardening,
                "process_hardening": process_hardening,
                "error_code": primary_error.code if primary_error is not None else "",
                "cleanup_error_code": cleanup_error,
            }
        )
        try:
            _lifecycle_write_json(manifest_path, manifest, root=project_root)
        except (LifecycleError, OSError):
            cleanup_error = cleanup_error or "security_probe_manifest_finalize_failed"

    if primary_error is not None:
        if cleanup_error:
            raise ProbeError(primary_error.code, cleanup_code=cleanup_error)
        raise primary_error
    if cleanup_error:
        raise ProbeError(cleanup_error)
    return {
        "schema_version": INDEPENDENT_SCHEMA_VERSION,
        "ok": True,
        "mode": "provider-independent",
        "profile": PROFILE,
        "probe_id": probe_id,
        "provider_calls": provider_calls,
        "provider_calls_observed": True,
        "providers": [],
        "formal_business_sandbox": False,
        "host_runtime_unchanged": True,
        "phase": "passed",
        "quality_validated": False,
        "readiness_effect": "none",
        "checks": checks,
        "mounts": mount_counts,
        "runtime_isolation": runtime_isolation,
        "network_isolation": network_isolation,
        "container_hardening": container_hardening,
        "process_hardening": process_hardening,
        "state": manifest_path.relative_to(project_root).as_posix(),
    }


def recover_provider_independent_probe(
    *,
    project_root: Path,
    probe_id: str,
    timeout: int,
    adapter: LifecycleAdapter | None = None,
) -> dict[str, Any]:
    if not SECURITY_PROBE_ID_RE.fullmatch(probe_id):
        raise ProbeError("probe_id_invalid")
    try:
        project_root = project_root.resolve(strict=True)
        lifecycle = adapter or LifecycleAdapter(project_root=project_root)
        manifest, nonce, state_dir, identity = _load_provider_independent_recovery_context(
            project_root=project_root,
            probe_id=probe_id,
        )
    except ProbeError:
        raise
    except (LifecycleError, OSError) as exc:
        raise ProbeError("security_probe_recovery_intent_invalid") from exc

    manifest_path = state_dir / "probe.json"
    checks: list[str] = []
    try:
        lifecycle.recover_security_probe_sandbox(
            probe_id=probe_id,
            nonce=nonce,
            expected_sandbox_id=identity.sandbox_id,
            expected_container_id=identity.container_id,
        )
        checks.append("verified_probe_sandbox_reconciled")

        sentinel_name = manifest.get("sentinel_name")
        sentinel_marker = manifest.get("sentinel_marker")
        if sentinel_name or sentinel_marker:
            if not isinstance(sentinel_name, str) or not isinstance(sentinel_marker, str):
                raise ProbeError("probe_sentinel_identity_invalid")
            try:
                marker = sentinel_marker.encode("ascii")
            except UnicodeEncodeError as exc:
                raise ProbeError("probe_sentinel_identity_invalid") from exc
            analysis_relative = _validate_analysis_relative(manifest)
            analysis_path = _canonical_project_child(
                project_root,
                analysis_relative,
                code="probe_sentinel_identity_invalid",
            )
            runtime_snapshot = project_root / SNAPSHOTS_RELATIVE / probe_id
            recovery_context = IndependentProbeContext(
                project_root=project_root,
                probe_id=probe_id,
                state_dir=state_dir,
                sandbox_name=f"siq-analysis-security-{probe_id}",
                sandbox_id=identity.sandbox_id,
                container_id=identity.container_id,
                nonce=nonce,
                analysis_path=analysis_path,
                runtime_snapshot=runtime_snapshot,
                mount_plan=Path("/nonexistent"),
                mount_plan_sha256="0" * 64,
                policy_path=state_dir / "task-policy.yaml",
                policy={},
                manifest=manifest,
            )
            sentinels = SentinelPaths.from_persisted(
                recovery_context,
                name=sentinel_name,
                marker=marker,
            )
            for path in (
                sentinels.analysis_host,
                sentinels.state_host,
                sentinels.runtime_host,
                sentinels.memory_host,
                sentinels.wiki_host,
            ):
                _safe_host_cleanup(path, sentinels.marker)
            if any(
                path.exists() or path.is_symlink()
                for path in (
                    sentinels.analysis_host,
                    sentinels.state_host,
                    sentinels.runtime_host,
                    sentinels.memory_host,
                    sentinels.wiki_host,
                )
            ):
                raise ProbeError("probe_cleanup_failed")
            checks.append("interrupted_probe_host_sentinels_removed")

        runtime_snapshot = project_root / SNAPSHOTS_RELATIVE / probe_id
        _remove_private_tree(runtime_snapshot, project_root=project_root)
        _remove_private_tree(state_dir, project_root=project_root)
        checks.append("interrupted_probe_private_state_removed")
    except (LifecycleError, ProbeError, OSError) as exc:
        code = exc.code if isinstance(exc, (LifecycleError, ProbeError)) else "security_probe_recovery_failed"
        manifest.update(
            {
                "phase": "cleanup_incomplete",
                "error_code": code,
                "cleanup_error_code": code,
                "checks": checks,
            }
        )
        try:
            _lifecycle_write_json(manifest_path, manifest, root=project_root)
        except (LifecycleError, OSError):
            pass
        raise ProbeError(code) from exc

    return {
        "schema_version": INDEPENDENT_SCHEMA_VERSION,
        "ok": True,
        "mode": "provider-independent-recover",
        "profile": PROFILE,
        "probe_id": probe_id,
        "providers": [],
        "provider_calls": manifest.get("provider_calls"),
        "provider_calls_observed": manifest.get("provider_calls_observed"),
        "formal_business_sandbox": False,
        "host_runtime_unchanged": True,
        "quality_validated": False,
        "readiness_effect": "none",
        "phase": "recovered",
        "checks": checks,
    }


def run_filesystem_boundary_probe(context: ProbeContext, *, timeout: int) -> dict[str, Any]:
    """Probe only formal sandbox filesystem identity, denials and writable binds."""

    checks: list[str] = []
    sentinels = SentinelPaths.build(context)
    for host_path in (
        sentinels.analysis_host,
        sentinels.state_host,
        sentinels.runtime_host,
        sentinels.memory_host,
        sentinels.wiki_host,
    ):
        if host_path.exists() or host_path.is_symlink():
            raise ProbeError("probe_sentinel_conflict")

    primary_error: ProbeError | None = None
    mount_counts: dict[str, int] = {}
    host_receipts: dict[str, str] = {}
    filesystem_response: Mapping[str, Any] = {}
    sentinels_may_exist = False
    try:
        _validate_lifecycle_status(context, timeout=timeout)
        checks.append("lifecycle_identity_and_health")
        _validate_openshell_identity(context, timeout=timeout)
        checks.append("openshell_sandbox_identity")
        _validate_active_policy(context, timeout=timeout)
        checks.append("active_policy_matches_manifest")
        mounts = _docker_inspect_mounts(context, timeout=timeout)
        mount_counts = validate_container_mounts(context, mounts)
        checks.append("mount_contract_7_plus_5")

        # The filesystem probe is the first operation that can create a sentinel.
        # Mark cleanup necessary before exec because a timeout can occur mid-write.
        sentinels_may_exist = True
        filesystem_response = _sandbox_exec_json(
            context,
            [
                SANDBOX_PYTHON,
                "-c",
                FILESYSTEM_PROBE,
                SANDBOX_ROOT.as_posix(),
                (SANDBOX_ROOT / context.manifest["analysis_relative_path"]).as_posix(),
                SANDBOX_HERMES_HOME.as_posix(),
                SANDBOX_RUNTIME_STATE_ROOT.as_posix(),
                sentinels.name,
                sentinels.marker.decode("ascii"),
            ],
            timeout=timeout,
            code="filesystem_probe_failed",
        )
        expected_filesystem_response = {
            "ok": True,
            "check": "filesystem",
            "immutable_write_denials": {key: True for key in FILESYSTEM_IMMUTABLE_DENIALS},
            "sensitive_read_denials": {key: True for key in FILESYSTEM_SENSITIVE_DENIALS},
            "allowed_writes": {key: True for key in FILESYSTEM_ALLOWED_WRITES},
        }
        if filesystem_response != expected_filesystem_response:
            raise ProbeError("filesystem_probe_response_invalid")
        checks.extend((*FILESYSTEM_IMMUTABLE_DENIALS, *FILESYSTEM_SENSITIVE_DENIALS))

        host_receipts["analysis"] = _verify_host_sentinel(
            sentinels.analysis_host,
            sentinels.marker,
            code="analysis_bind_visibility_failed",
        )
        checks.append("analysis_bind_read_write")
        host_receipts["runtime_state"] = _verify_host_sentinel(
            sentinels.state_host,
            sentinels.marker,
            code="runtime_state_bind_visibility_failed",
        )
        checks.append("runtime_state_directory_bind_read_write")
        host_receipts["runtime_session"] = _verify_host_sentinel(
            sentinels.runtime_host,
            sentinels.marker,
            code="runtime_bind_visibility_failed",
        )
        checks.append("runtime_session_bind_read_write")
        host_receipts["runtime_memory"] = _verify_host_sentinel(
            sentinels.memory_host,
            sentinels.marker,
            code="memory_bind_visibility_failed",
        )
        checks.append("runtime_memory_bind_read_write")
        checks.append("tmp_scratch_write")

    except ProbeError as exc:
        primary_error = exc
    except Exception:
        primary_error = ProbeError("probe_internal_error")
    finally:
        if sentinels_may_exist:
            try:
                _cleanup(context, sentinels, timeout=timeout)
                checks.append("probe_sentinels_removed")
            except ProbeError as cleanup_error:
                if primary_error is None:
                    primary_error = cleanup_error
                else:
                    primary_error = ProbeError(primary_error.code, cleanup_code=cleanup_error.code)

    if primary_error is not None:
        raise primary_error
    if set(host_receipts) != {"analysis", "runtime_state", "runtime_session", "runtime_memory"}:
        raise ProbeError("filesystem_host_receipts_incomplete")
    return {
        "schema_version": "siq.openshell.formal_filesystem_boundary_probe.v1",
        "ok": True,
        "profile": PROFILE,
        "run_id": context.run_id,
        "checks": checks,
        "mounts": mount_counts,
        "immutable_write_denials": dict(filesystem_response["immutable_write_denials"]),
        "sensitive_read_denials": dict(filesystem_response["sensitive_read_denials"]),
        "allowed_writes": dict(filesystem_response["allowed_writes"]),
        "host_visibility_receipts": host_receipts,
        "filesystem_response_sha256": _sha256_bytes(
            json.dumps(filesystem_response, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("ascii")
        ),
        "filesystem_probe_sha256": _sha256_bytes(FILESYSTEM_PROBE.encode("utf-8")),
        "cleanup_succeeded": True,
        "residual_host_sentinel_count": 0,
    }


def run_probe(context: ProbeContext, *, timeout: int) -> dict[str, Any]:
    filesystem = run_filesystem_boundary_probe(context, timeout=timeout)
    checks = list(filesystem["checks"])
    mount_counts = dict(filesystem["mounts"])
    sentinels = SentinelPaths.build(context)
    primary_error: ProbeError | None = None
    network_sentinel_may_exist = False
    try:
        broker = _sandbox_exec_json(
            context,
            [SANDBOX_PYTHON, "-c", BROKER_MULTIPART_PROBE],
            timeout=timeout,
            code="broker_multipart_probe_failed",
        )
        if (
            broker.get("public_head_control") is not True
            or broker.get("status") != 403
            or broker.get("error_code") != "broker_multipart_denied"
        ):
            raise ProbeError("broker_multipart_response_invalid")
        checks.extend(("broker_public_head_control", "broker_unknown_multipart_denied"))

        network_sentinel_may_exist = True
        direct = _sandbox_exec_json(
            context,
            ["/bin/sh", "-c", DIRECT_UPLOAD_PROBE, "siq-upload-probe", sentinels.upload_sandbox.as_posix()],
            timeout=timeout,
            code="direct_upload_probe_failed",
        )
        curl_returncode = direct.get("curl_returncode")
        if (
            isinstance(curl_returncode, bool)
            or not isinstance(curl_returncode, int)
            or not 1 <= curl_returncode <= 255
            or direct.get("http_code") != "000"
        ):
            raise ProbeError("direct_unknown_upload_allowed")
        checks.append("direct_unknown_upload_denied")

        nemotron = _sandbox_exec_json(
            context,
            [SANDBOX_PYTHON, "-c", NEMOTRON_PROBE],
            timeout=timeout,
            code="nemotron_probe_failed",
        )
        if nemotron.get("status") != 200 or nemotron.get("model_present") is not True:
            raise ProbeError("nemotron_response_invalid")
        checks.append("nemotron_8007_model_catalog")
    except ProbeError as exc:
        primary_error = exc
    except Exception:
        primary_error = ProbeError("probe_internal_error")
    finally:
        if network_sentinel_may_exist:
            try:
                _cleanup(context, sentinels, timeout=timeout)
            except ProbeError as cleanup_error:
                if primary_error is None:
                    primary_error = cleanup_error
                else:
                    primary_error = ProbeError(primary_error.code, cleanup_code=cleanup_error.code)

    if primary_error is not None:
        raise primary_error
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": True,
        "profile": PROFILE,
        "run_id": context.run_id,
        "provider_calls": 0,
        "checks": checks,
        "mounts": mount_counts,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("formal-running", "provider-independent"),
        default="formal-running",
    )
    parser.add_argument("--run-id")
    parser.add_argument("--probe-id")
    parser.add_argument("--recover", action="store_true")
    parser.add_argument("--profile", default=PROFILE)
    parser.add_argument("--market", choices=sorted(MARKET_PREFIXES))
    parser.add_argument("--company")
    parser.add_argument("--project-root", type=Path, default=REPO_ROOT)
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_EXEC_TIMEOUT_SECONDS,
        help="Per-command timeout in seconds (5-60)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    started = time.monotonic()
    error_code = ""
    report: dict[str, Any]
    probe_id = args.probe_id or ""
    try:
        if not 5 <= args.timeout <= 60:
            raise ProbeError("timeout_invalid")
        if args.mode == "provider-independent":
            if args.run_id is not None or args.profile != PROFILE:
                raise ProbeError("provider_independent_arguments_invalid")
            try:
                project_root = args.project_root.expanduser().resolve(strict=True)
            except OSError as exc:
                raise ProbeError("project_root_invalid") from exc
            if project_root != REPO_ROOT or not project_root.is_dir() or project_root.is_symlink():
                raise ProbeError("project_root_invalid")
            if args.recover:
                if not SECURITY_PROBE_ID_RE.fullmatch(probe_id) or args.market is not None or args.company is not None:
                    raise ProbeError("provider_independent_recovery_arguments_invalid")
                report = recover_provider_independent_probe(
                    project_root=project_root,
                    probe_id=probe_id,
                    timeout=args.timeout,
                )
            else:
                if (
                    not SECURITY_PROBE_ID_RE.fullmatch(probe_id)
                    or args.market not in MARKET_PREFIXES
                    or not isinstance(args.company, str)
                    or not args.company
                ):
                    raise ProbeError("provider_independent_arguments_invalid")
                report = run_provider_independent_probe(
                    project_root=project_root,
                    profile=args.profile,
                    market=args.market,
                    company=args.company,
                    probe_id=probe_id,
                    timeout=args.timeout,
                )
        else:
            if (
                args.recover
                or args.probe_id is not None
                or args.market is not None
                or args.company is not None
                or args.run_id is None
            ):
                raise ProbeError("formal_probe_arguments_invalid")
            context = load_context(args.project_root, args.run_id)
            report = run_probe(context, timeout=args.timeout)
    except ProbeError as exc:
        error_code = exc.code
        if args.mode == "provider-independent":
            report = {
                "schema_version": INDEPENDENT_SCHEMA_VERSION,
                "ok": False,
                "mode": "provider-independent-recover" if args.recover else args.mode,
                "profile": PROFILE,
                "probe_id": probe_id if SECURITY_PROBE_ID_RE.fullmatch(probe_id) else "invalid",
                "provider_calls": None,
                "provider_calls_observed": False,
                "providers": [],
                "formal_business_sandbox": False,
                "host_runtime_unchanged": True,
                "quality_validated": False,
                "readiness_effect": "none",
                "checks": [],
                "error_code": error_code,
            }
        else:
            report = {
                "schema_version": SCHEMA_VERSION,
                "ok": False,
                "profile": PROFILE,
                "run_id": args.run_id if RUN_ID_RE.fullmatch(str(args.run_id)) else "invalid",
                "provider_calls": 0,
                "checks": [],
                "error_code": error_code,
            }
        if exc.cleanup_code:
            report["cleanup_error_code"] = exc.cleanup_code
    except Exception:
        error_code = "probe_internal_error"
        report = {
            "schema_version": INDEPENDENT_SCHEMA_VERSION if args.mode == "provider-independent" else SCHEMA_VERSION,
            "ok": False,
            "mode": "provider-independent-recover" if args.recover else args.mode,
            "profile": PROFILE,
            "provider_calls": None if args.mode == "provider-independent" else 0,
            "checks": [],
            "error_code": error_code,
        }
        if args.mode == "provider-independent":
            report.update(
                {
                    "probe_id": probe_id if SECURITY_PROBE_ID_RE.fullmatch(probe_id) else "invalid",
                    "providers": [],
                    "provider_calls_observed": False,
                    "formal_business_sandbox": False,
                    "host_runtime_unchanged": True,
                    "quality_validated": False,
                    "readiness_effect": "none",
                }
            )
        else:
            report["run_id"] = args.run_id if RUN_ID_RE.fullmatch(str(args.run_id)) else "invalid"
    report["duration_ms"] = max(0, int((time.monotonic() - started) * 1000))
    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0 if not error_code else 1


if __name__ == "__main__":
    sys.exit(main())
