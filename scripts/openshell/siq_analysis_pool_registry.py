#!/usr/bin/python3 -IB
"""Build and resolve a fail-closed, company-scoped SIQ OpenShell pool registry."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import secrets
import socket
import stat
import sys
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Literal, Mapping

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.openshell.siq_analysis_canary import (  # noqa: E402
    ACTIVE_FIELDS,
    MANIFEST_FIELDS,
    MODE as CANARY_MODE,
    RUN_ID_RE,
    SANDBOX_PREFIX,
    SCHEMA_VERSION as CANARY_SCHEMA_VERSION,
)
from scripts.openshell.siq_analysis_lifecycle import (  # noqa: E402
    BUSINESS_MOUNT_COUNT,
    CANARY_LIFECYCLE_LABEL,
    MARKET_ROOTS,
    PROFILE,
    LifecycleError,
    _assert_no_symlink_chain,
    _mkdir_private,
    _read_json,
    _read_private,
    _sha256_file,
    _write_json,
)
from scripts.openshell.siq_analysis_wide_pilot import (  # noqa: E402
    KNOWN_FORMAL_BLOCKERS_NOT_BYPASSED,
    PROVIDERS,
)

SCHEMA_VERSION = "siq.openshell.siq_analysis_pool_registry.v1"
POOL_RELATIVE = Path("var/openshell/canary/siq-analysis/pool")
SLOTS_RELATIVE = POOL_RELATIVE / "slots"
REGISTRY_RELATIVE = POOL_RELATIVE / "registry.json"
LOCK_RELATIVE = POOL_RELATIVE / "registry.lock"
PORT_RESERVATIONS_RELATIVE = POOL_RELATIVE / "port-reservations.json"
SCHEDULER_RELATIVE = POOL_RELATIVE / "scheduler.json"
LEGACY_ACTIVE_RELATIVE = Path("var/openshell/canary/siq-analysis/active.json")
RUNTIME_SNAPSHOT_RELATIVE = Path("var/openshell/siq-analysis/runtime-snapshots")
MOUNT_PLAN_RELATIVE = Path("var/openshell/siq-analysis/mount-plans")
FORWARD_HOST = "127.0.0.1"
TARGET_PORT = 28651
FIRST_POOL_PORT = 28652
LAST_POOL_PORT = 28750
PORT_RESERVATION_TTL_SECONDS = 900
UNMATCHED_SCOPE = "host"
SCOPE_ID_RE = re.compile(r"[0-9a-f]{24}\Z")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
PORT_RESERVATION_TOKEN_RE = re.compile(r"reservation-[0-9a-f]{32}\Z")
PORT_RESERVATION_SCHEMA = "siq.openshell.siq_analysis_pool_port_reservations.v1"

REGISTRY_FIELDS = {
    "schema_version",
    "profile",
    "unmatched_scope",
    "target_port",
    "generation",
    "bindings",
}
ENTRY_FIELDS = {
    "scope_id",
    "market",
    "company",
    "run_id",
    "active",
    "manifest",
    "manifest_sha256",
    "api_key_sha256",
    "analysis_relative_path",
    "runtime_snapshot",
    "sandbox_name",
    "local_port",
    "target_port",
    "session_namespace",
}


class PoolRegistryError(RuntimeError):
    """A stable, secret-free pool registry failure."""

    def __init__(self, code: str) -> None:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,95}", code):
            code = "openshell_pool_invalid_error"
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class PoolSlotPlan:
    scope_id: str
    market: str
    company: str
    run_id: str
    state_relative: str
    active_relative: str
    analysis_relative_path: str
    sandbox_name: str
    runtime_snapshot: str
    local_port: int
    target_port: int
    session_namespace: str


@dataclass(frozen=True, repr=False)
class PortReservation:
    local_port: int
    reservation_token: str
    expires_at: int

    def __repr__(self) -> str:
        return (
            "PortReservation("
            f"local_port={self.local_port}, reservation_token='<redacted>', "
            f"expires_at={self.expires_at})"
        )


@dataclass(frozen=True, repr=False)
class PoolRoute:
    target: Literal["host", "openshell"]
    market: str
    company: str
    base: str = ""
    api_key: str = ""
    run_id: str = ""
    session_namespace: str = ""
    analysis_relative_path: str = ""

    def __repr__(self) -> str:
        return (
            "PoolRoute("
            f"target={self.target!r}, market={self.market!r}, company={self.company!r}, "
            f"base={self.base!r}, run_id={self.run_id!r}, "
            f"session_namespace={self.session_namespace!r}, "
            f"analysis_relative_path={self.analysis_relative_path!r}, api_key='<redacted>')"
        )


@dataclass(frozen=True, repr=False)
class _ValidatedBinding:
    entry: dict[str, Any]
    api_key: str

    def __repr__(self) -> str:
        return f"_ValidatedBinding(entry={self.entry!r}, api_key='<redacted>')"


def _project_root(project_root: Path) -> Path:
    absolute = project_root.absolute()
    try:
        resolved = project_root.resolve(strict=True)
    except OSError as exc:
        raise PoolRegistryError("openshell_pool_project_root_invalid") from exc
    if resolved != absolute or resolved in {Path("/"), Path("/home"), Path("/tmp"), Path("/var")}:
        raise PoolRegistryError("openshell_pool_project_root_invalid")
    return resolved


def _scope_id(market: str, company: str) -> str:
    return hashlib.sha256(f"{market}\0{company}".encode("utf-8")).hexdigest()[:24]


def _canonical_scope(project_root: Path, market: str, company: str) -> tuple[str, str, Path, str]:
    if market != market.strip().lower() or market not in MARKET_ROOTS:
        raise PoolRegistryError("openshell_pool_market_not_canonical")
    if (
        company != company.strip()
        or not company
        or company in {".", ".."}
        or Path(company).name != company
        or "\0" in company
    ):
        raise PoolRegistryError("openshell_pool_company_not_canonical")
    analysis_relative = MARKET_ROOTS[market] / company / "analysis"
    company_root = project_root / analysis_relative.parent
    analysis_root = project_root / analysis_relative
    try:
        _assert_no_symlink_chain(project_root, analysis_root)
        company_info = company_root.lstat()
        analysis_info = analysis_root.lstat()
    except (FileNotFoundError, LifecycleError, OSError) as exc:
        raise PoolRegistryError("openshell_pool_company_layout_invalid") from exc
    if (
        not stat.S_ISDIR(company_info.st_mode)
        or not stat.S_ISDIR(analysis_info.st_mode)
        or company_root.is_symlink()
        or analysis_root.is_symlink()
    ):
        raise PoolRegistryError("openshell_pool_company_layout_invalid")
    return market, company, analysis_relative, _scope_id(market, company)


def _relative_path(project_root: Path, path: Path, *, code: str) -> Path:
    candidate = path if path.is_absolute() else project_root / path
    candidate = Path(os.path.normpath(os.fspath(candidate)))
    try:
        relative = candidate.relative_to(project_root)
        _assert_no_symlink_chain(project_root, candidate)
    except (ValueError, LifecycleError, OSError) as exc:
        raise PoolRegistryError(code) from exc
    if relative == Path(".") or ".." in relative.parts:
        raise PoolRegistryError(code)
    return relative


def _parse_private_json(project_root: Path, relative: Path, *, max_bytes: int) -> dict[str, Any]:
    try:
        content = _read_private(project_root / relative, root=project_root, max_bytes=max_bytes)
        value = json.loads(content)
    except (LifecycleError, UnicodeError, json.JSONDecodeError, OSError) as exc:
        raise PoolRegistryError("openshell_pool_state_invalid") from exc
    if not isinstance(value, dict):
        raise PoolRegistryError("openshell_pool_state_invalid")
    return value


def _active_state_root(active_relative: Path, *, scope_id: str) -> Path:
    if active_relative == LEGACY_ACTIVE_RELATIVE:
        return active_relative.parent
    expected = SLOTS_RELATIVE / scope_id / "active.json"
    if active_relative != expected:
        raise PoolRegistryError("openshell_pool_active_path_invalid")
    return expected.parent


def _canonical_mount_digest(value: Mapping[str, Any]) -> str:
    content = (json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode()
    return hashlib.sha256(content).hexdigest()


def _validate_mount_plan(
    project_root: Path,
    *,
    manifest: Mapping[str, Any],
    analysis_relative: Path,
    run_id: str,
) -> None:
    raw_relative = manifest.get("mount_plan")
    if not isinstance(raw_relative, str):
        raise PoolRegistryError("openshell_pool_mount_plan_invalid")
    relative = _relative_path(project_root, Path(raw_relative), code="openshell_pool_mount_plan_invalid")
    if relative.parent != MOUNT_PLAN_RELATIVE or not relative.name.endswith(".driver-config.json"):
        raise PoolRegistryError("openshell_pool_mount_plan_invalid")
    value = _parse_private_json(project_root, relative, max_bytes=64 * 1024)
    if _canonical_mount_digest(value) != manifest.get("mount_plan_sha256"):
        raise PoolRegistryError("openshell_pool_mount_plan_invalid")
    docker = value.get("docker")
    mounts = docker.get("mounts") if isinstance(docker, dict) else None
    if not isinstance(mounts, list) or len(mounts) != BUSINESS_MOUNT_COUNT:
        raise PoolRegistryError("openshell_pool_mount_plan_invalid")

    analysis = project_root / analysis_relative
    snapshot_relative = RUNTIME_SNAPSHOT_RELATIVE / run_id
    if manifest.get("runtime_snapshot") != snapshot_relative.as_posix():
        raise PoolRegistryError("openshell_pool_runtime_snapshot_invalid")
    snapshot = project_root / snapshot_relative
    runtime_targets = {
        "runtime-state": "/sandbox/siq-analysis-runtime-state",
        "sessions": str(project_root / "data/hermes/home/profiles/siq_analysis/sessions"),
        "checkpoints": str(project_root / "data/hermes/home/profiles/siq_analysis/checkpoints"),
        "cron": str(project_root / "data/hermes/home/profiles/siq_analysis/cron"),
        "memories": str(project_root / "data/hermes/home/profiles/siq_analysis/memories"),
    }
    expected = {
        (str(project_root / "data/wiki"), str(project_root / "data/wiki"), True),
        (str(analysis), str(analysis), False),
        *{
            (str(snapshot / name), target, False)
            for name, target in runtime_targets.items()
        },
    }
    actual: set[tuple[str, str, bool]] = set()
    for item in mounts:
        if not isinstance(item, dict) or set(item) != {"type", "source", "target", "read_only"}:
            raise PoolRegistryError("openshell_pool_mount_plan_invalid")
        if item.get("type") != "bind" or not isinstance(item.get("read_only"), bool):
            raise PoolRegistryError("openshell_pool_mount_plan_invalid")
        source = item.get("source")
        target = item.get("target")
        if not isinstance(source, str) or not isinstance(target, str):
            raise PoolRegistryError("openshell_pool_mount_plan_invalid")
        actual.add((source, target, item["read_only"]))
    if actual != expected:
        raise PoolRegistryError("openshell_pool_mount_plan_invalid")
    for name in runtime_targets:
        path = snapshot / name
        try:
            info = path.lstat()
        except OSError as exc:
            raise PoolRegistryError("openshell_pool_runtime_snapshot_invalid") from exc
        if not stat.S_ISDIR(info.st_mode) or path.is_symlink():
            raise PoolRegistryError("openshell_pool_runtime_snapshot_invalid")


def _validate_policy(project_root: Path, *, manifest: Mapping[str, Any], analysis_relative: Path) -> None:
    raw_relative = manifest.get("policy")
    if not isinstance(raw_relative, str):
        raise PoolRegistryError("openshell_pool_policy_invalid")
    relative = _relative_path(project_root, Path(raw_relative), code="openshell_pool_policy_invalid")
    try:
        if _sha256_file(project_root / relative) != manifest.get("policy_sha256"):
            raise PoolRegistryError("openshell_pool_policy_invalid")
    except (LifecycleError, OSError) as exc:
        raise PoolRegistryError("openshell_pool_policy_invalid") from exc
    value = _parse_private_json(project_root, relative, max_bytes=256 * 1024)
    filesystem = value.get("filesystem_policy")
    if not isinstance(filesystem, dict):
        raise PoolRegistryError("openshell_pool_policy_invalid")
    read_only = filesystem.get("read_only")
    read_write = filesystem.get("read_write")
    if not isinstance(read_only, list) or not isinstance(read_write, list):
        raise PoolRegistryError("openshell_pool_policy_invalid")
    if not all(isinstance(item, str) for item in [*read_only, *read_write]):
        raise PoolRegistryError("openshell_pool_policy_invalid")
    analysis = project_root / analysis_relative
    allowed_project_writes = {
        analysis,
        *{
            project_root / "data/hermes/home/profiles/siq_analysis" / name
            for name in ("cache", "checkpoints", "cron", "logs", "memories", "sessions", "workspace")
        },
    }
    project_writes: set[Path] = set()
    for raw in read_write:
        candidate = Path(os.path.normpath(raw))
        try:
            candidate.relative_to(project_root)
        except ValueError:
            continue
        project_writes.add(candidate)
    if (
        str(project_root) not in read_only
        or analysis not in project_writes
        or not project_writes.issubset(allowed_project_writes)
    ):
        raise PoolRegistryError("openshell_pool_policy_scope_invalid")


def _validated_binding(project_root: Path, active: Path, local_port: int) -> _ValidatedBinding:
    if not isinstance(local_port, int) or isinstance(local_port, bool) or not 1024 <= local_port <= 65535:
        raise PoolRegistryError("openshell_pool_local_port_invalid")
    active_relative = _relative_path(project_root, active, code="openshell_pool_active_path_invalid")
    active_value = _parse_private_json(project_root, active_relative, max_bytes=4096)
    if set(active_value) != ACTIVE_FIELDS:
        raise PoolRegistryError("openshell_pool_active_invalid")
    market = active_value.get("market")
    company = active_value.get("company")
    if not isinstance(market, str) or not isinstance(company, str):
        raise PoolRegistryError("openshell_pool_active_invalid")
    market, company, analysis_relative, scope_id = _canonical_scope(project_root, market, company)
    state_root = _active_state_root(active_relative, scope_id=scope_id)
    run_id = active_value.get("run_id")
    if not isinstance(run_id, str) or RUN_ID_RE.fullmatch(run_id) is None:
        raise PoolRegistryError("openshell_pool_active_invalid")
    run_state = state_root / "runs" / run_id
    manifest_relative = run_state / "canary.json"
    if (
        active_value.get("schema_version") != CANARY_SCHEMA_VERSION
        or active_value.get("mode") != CANARY_MODE
        or active_value.get("readiness_effect") != "none"
        or active_value.get("profile") != PROFILE
        or active_value.get("run_state") != run_state.as_posix()
        or active_value.get("manifest") != manifest_relative.as_posix()
        or not isinstance(active_value.get("manifest_sha256"), str)
        or SHA256_RE.fullmatch(active_value["manifest_sha256"]) is None
        or not isinstance(active_value.get("api_key_sha256"), str)
        or SHA256_RE.fullmatch(active_value["api_key_sha256"]) is None
    ):
        raise PoolRegistryError("openshell_pool_active_invalid")

    try:
        manifest_content = _read_private(
            project_root / manifest_relative,
            root=project_root,
            max_bytes=64 * 1024,
        )
        manifest = json.loads(manifest_content)
    except (LifecycleError, UnicodeError, json.JSONDecodeError, OSError) as exc:
        raise PoolRegistryError("openshell_pool_manifest_invalid") from exc
    if not isinstance(manifest, dict) or set(manifest) != MANIFEST_FIELDS:
        raise PoolRegistryError("openshell_pool_manifest_invalid")
    expected_analysis = analysis_relative.as_posix()
    if (
        hashlib.sha256(manifest_content).hexdigest() != active_value["manifest_sha256"]
        or manifest.get("schema_version") != CANARY_SCHEMA_VERSION
        or manifest.get("mode") != CANARY_MODE
        or manifest.get("readiness_effect") != "none"
        or manifest.get("phase") != "running"
        or manifest.get("profile") != PROFILE
        or manifest.get("run_id") != run_id
        or manifest.get("market") != market
        or manifest.get("company") != company
        or manifest.get("analysis_relative_path") != expected_analysis
        or manifest.get("writable_relative_path") != expected_analysis
        or manifest.get("write_scope") != "current_company_analysis_root"
        or manifest.get("normal_business_mutations") != ["create", "modify", "rename", "delete"]
        or manifest.get("sandbox_name") != f"{SANDBOX_PREFIX}{run_id}"
        or manifest.get("lifecycle_label") != CANARY_LIFECYCLE_LABEL
        or manifest.get("mount_count") != BUSINESS_MOUNT_COUNT
        or manifest.get("providers") != list(PROVIDERS)
        or manifest.get("formal_blockers_not_overridden") != list(KNOWN_FORMAL_BLOCKERS_NOT_BYPASSED)
        or manifest.get("broker_request_identity_required") is not True
        or manifest.get("result_is_formal_evidence") is not False
        or manifest.get("api_key_sha256") != active_value["api_key_sha256"]
    ):
        raise PoolRegistryError("openshell_pool_manifest_invalid")
    _validate_mount_plan(
        project_root,
        manifest=manifest,
        analysis_relative=analysis_relative,
        run_id=run_id,
    )
    _validate_policy(project_root, manifest=manifest, analysis_relative=analysis_relative)

    try:
        api_key = _read_private(project_root / run_state / "api.key", root=project_root, max_bytes=256)
        api_key_text = api_key.decode("ascii").strip()
    except (LifecycleError, UnicodeError, OSError) as exc:
        raise PoolRegistryError("openshell_pool_api_key_invalid") from exc
    if not re.fullmatch(r"[0-9a-f]{64}", api_key_text) or (
        hashlib.sha256(api_key_text.encode("ascii")).hexdigest() != active_value["api_key_sha256"]
    ):
        raise PoolRegistryError("openshell_pool_api_key_invalid")

    session_namespace = f"siq:openshell:pool:{scope_id}:{run_id}:{PROFILE}"
    entry = {
        "scope_id": scope_id,
        "market": market,
        "company": company,
        "run_id": run_id,
        "active": active_relative.as_posix(),
        "manifest": manifest_relative.as_posix(),
        "manifest_sha256": active_value["manifest_sha256"],
        "api_key_sha256": active_value["api_key_sha256"],
        "analysis_relative_path": expected_analysis,
        "runtime_snapshot": manifest["runtime_snapshot"],
        "sandbox_name": manifest["sandbox_name"],
        "local_port": local_port,
        "target_port": TARGET_PORT,
        "session_namespace": session_namespace,
    }
    return _ValidatedBinding(entry=entry, api_key=api_key_text)


def _empty_registry() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "profile": PROFILE,
        "unmatched_scope": UNMATCHED_SCOPE,
        "target_port": TARGET_PORT,
        "generation": 0,
        "bindings": [],
    }


def _validate_registry(value: Mapping[str, Any]) -> dict[str, Any]:
    if (
        set(value) != REGISTRY_FIELDS
        or value.get("schema_version") != SCHEMA_VERSION
        or value.get("profile") != PROFILE
        or value.get("unmatched_scope") != UNMATCHED_SCOPE
        or value.get("target_port") != TARGET_PORT
        or not isinstance(value.get("generation"), int)
        or isinstance(value.get("generation"), bool)
        or value["generation"] < 0
        or not isinstance(value.get("bindings"), list)
    ):
        raise PoolRegistryError("openshell_pool_registry_invalid")
    bindings = value["bindings"]
    scopes: set[str] = set()
    runs: set[str] = set()
    ports: set[int] = set()
    sandboxes: set[str] = set()
    snapshots: set[str] = set()
    analyses: set[str] = set()
    previous_scope = ""
    for entry in bindings:
        if not isinstance(entry, dict) or set(entry) != ENTRY_FIELDS:
            raise PoolRegistryError("openshell_pool_registry_invalid")
        scope_id = entry.get("scope_id")
        market = entry.get("market")
        company = entry.get("company")
        port = entry.get("local_port")
        if (
            not isinstance(scope_id, str)
            or SCOPE_ID_RE.fullmatch(scope_id) is None
            or not isinstance(market, str)
            or not isinstance(company, str)
            or scope_id != _scope_id(market, company)
            or scope_id <= previous_scope
            or not isinstance(port, int)
            or isinstance(port, bool)
            or not 1024 <= port <= 65535
            or entry.get("target_port") != TARGET_PORT
            or not isinstance(entry.get("run_id"), str)
            or RUN_ID_RE.fullmatch(entry["run_id"]) is None
            or not isinstance(entry.get("manifest_sha256"), str)
            or SHA256_RE.fullmatch(entry["manifest_sha256"]) is None
            or not isinstance(entry.get("api_key_sha256"), str)
            or SHA256_RE.fullmatch(entry["api_key_sha256"]) is None
        ):
            raise PoolRegistryError("openshell_pool_registry_invalid")
        previous_scope = scope_id
        unique_values = (
            (scope_id, scopes),
            (entry["run_id"], runs),
            (port, ports),
            (entry.get("sandbox_name"), sandboxes),
            (entry.get("runtime_snapshot"), snapshots),
            (entry.get("analysis_relative_path"), analyses),
        )
        if any(not isinstance(item, (str, int)) or item in seen for item, seen in unique_values):
            raise PoolRegistryError("openshell_pool_registry_collision")
        for item, seen in unique_values:
            seen.add(item)
    return dict(value)


def load_registry(*, project_root: Path = REPO_ROOT) -> dict[str, Any]:
    root = _project_root(project_root)
    path = root / REGISTRY_RELATIVE
    if not path.exists() and not path.is_symlink():
        return _empty_registry()
    try:
        value = _read_json(path, root=root)
    except (LifecycleError, OSError) as exc:
        raise PoolRegistryError("openshell_pool_registry_unavailable") from exc
    return _validate_registry(value)


@contextmanager
def _registry_lock(project_root: Path) -> Iterator[None]:
    try:
        _mkdir_private(project_root / POOL_RELATIVE, root=project_root)
        lock_path = project_root / LOCK_RELATIVE
        _assert_no_symlink_chain(project_root, lock_path)
        descriptor = os.open(
            lock_path,
            os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise PoolRegistryError("openshell_pool_registry_lock_invalid")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    except OSError as exc:
        raise PoolRegistryError("openshell_pool_registry_lock_failed") from exc
    finally:
        if "descriptor" in locals():
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


def _load_port_reservations(project_root: Path, *, now: int) -> tuple[dict[str, Any], bool]:
    path = project_root / PORT_RESERVATIONS_RELATIVE
    if not path.exists() and not path.is_symlink():
        return {"schema_version": PORT_RESERVATION_SCHEMA, "reservations": []}, False
    try:
        value = _read_json(path, root=project_root)
    except (LifecycleError, OSError) as exc:
        raise PoolRegistryError("openshell_pool_port_reservations_unavailable") from exc
    if (
        set(value) != {"schema_version", "reservations"}
        or value.get("schema_version") != PORT_RESERVATION_SCHEMA
        or not isinstance(value.get("reservations"), list)
    ):
        raise PoolRegistryError("openshell_pool_port_reservations_invalid")
    ports: set[int] = set()
    tokens: set[str] = set()
    retained = []
    for item in value["reservations"]:
        if (
            not isinstance(item, dict)
            or set(item) != {"local_port", "reservation_token", "expires_at"}
            or not isinstance(item.get("local_port"), int)
            or isinstance(item["local_port"], bool)
            or not FIRST_POOL_PORT <= item["local_port"] <= LAST_POOL_PORT
            or not isinstance(item.get("reservation_token"), str)
            or PORT_RESERVATION_TOKEN_RE.fullmatch(item["reservation_token"]) is None
            or not isinstance(item.get("expires_at"), int)
            or isinstance(item["expires_at"], bool)
            or item["expires_at"] < 0
            or item["local_port"] in ports
            or item["reservation_token"] in tokens
        ):
            raise PoolRegistryError("openshell_pool_port_reservations_invalid")
        ports.add(item["local_port"])
        tokens.add(item["reservation_token"])
        if item["expires_at"] > now:
            retained.append(item)
    retained.sort(key=lambda item: item["local_port"])
    return {
        "schema_version": PORT_RESERVATION_SCHEMA,
        "reservations": retained,
    }, len(retained) != len(value["reservations"])


def _write_port_reservations(project_root: Path, value: Mapping[str, Any]) -> None:
    try:
        _write_json(project_root / PORT_RESERVATIONS_RELATIVE, value, root=project_root)
    except (LifecycleError, OSError) as exc:
        raise PoolRegistryError("openshell_pool_port_reservations_write_failed") from exc


def validate_port_reservation(
    *,
    local_port: int,
    reservation_token: str,
    now: int | None = None,
    project_root: Path = REPO_ROOT,
) -> PortReservation:
    root = _project_root(project_root)
    current_time = int(time.time()) if now is None else now
    if not isinstance(current_time, int) or isinstance(current_time, bool) or current_time < 0:
        raise PoolRegistryError("openshell_pool_clock_invalid")
    with _registry_lock(root):
        reservations, changed = _load_port_reservations(root, now=current_time)
        if changed:
            _write_port_reservations(root, reservations)
        item = next(
            (
                value
                for value in reservations["reservations"]
                if value["local_port"] == local_port
                and value["reservation_token"] == reservation_token
            ),
            None,
        )
        if item is None:
            raise PoolRegistryError("openshell_pool_port_reservation_invalid")
        return PortReservation(
            local_port=item["local_port"],
            reservation_token=item["reservation_token"],
            expires_at=item["expires_at"],
        )


def register_active(
    *,
    active: Path,
    local_port: int,
    reservation_token: str | None = None,
    replace: bool = False,
    now: int | None = None,
    project_root: Path = REPO_ROOT,
) -> dict[str, Any]:
    root = _project_root(project_root)
    current_time = int(time.time()) if now is None else now
    if not isinstance(current_time, int) or isinstance(current_time, bool) or current_time < 0:
        raise PoolRegistryError("openshell_pool_clock_invalid")
    with _registry_lock(root):
        binding = _validated_binding(root, active, local_port)
        registry = load_registry(project_root=root)
        entries = list(registry["bindings"])
        existing = next((item for item in entries if item["scope_id"] == binding.entry["scope_id"]), None)
        if existing == binding.entry:
            return registry
        if existing is not None and not replace:
            raise PoolRegistryError("openshell_pool_scope_already_registered")
        retained = [item for item in entries if item["scope_id"] != binding.entry["scope_id"]]
        if any(item["local_port"] == local_port for item in retained):
            raise PoolRegistryError("openshell_pool_local_port_conflict")
        reservations, reservations_changed = _load_port_reservations(root, now=current_time)
        reservation = next(
            (item for item in reservations["reservations"] if item["local_port"] == local_port),
            None,
        )
        if local_port != TARGET_PORT and (
            reservation is None
            or reservation_token is None
            or reservation.get("reservation_token") != reservation_token
        ):
            if reservations_changed:
                _write_port_reservations(root, reservations)
            raise PoolRegistryError("openshell_pool_port_reservation_required")
        for field in ("run_id", "sandbox_name", "runtime_snapshot", "analysis_relative_path"):
            if any(item[field] == binding.entry[field] for item in retained):
                raise PoolRegistryError("openshell_pool_binding_collision")
        retained.append(binding.entry)
        retained.sort(key=lambda item: item["scope_id"])
        updated = {
            **registry,
            "generation": registry["generation"] + 1,
            "bindings": retained,
        }
        if reservation is not None:
            reservations["reservations"] = [
                item
                for item in reservations["reservations"]
                if item["reservation_token"] != reservation["reservation_token"]
            ]
            _write_port_reservations(root, reservations)
        elif reservations_changed:
            _write_port_reservations(root, reservations)
        _write_json(root / REGISTRY_RELATIVE, updated, root=root)
        return updated


def unregister(
    *,
    market: str,
    company: str,
    run_id: str,
    project_root: Path = REPO_ROOT,
) -> dict[str, Any]:
    root = _project_root(project_root)
    market, company, _analysis, scope_id = _canonical_scope(root, market, company)
    del market, company
    with _registry_lock(root):
        registry = load_registry(project_root=root)
        existing = next((item for item in registry["bindings"] if item["scope_id"] == scope_id), None)
        if existing is None:
            return registry
        if existing["run_id"] != run_id:
            raise PoolRegistryError("openshell_pool_unregister_run_mismatch")
        scheduler_path = root / SCHEDULER_RELATIVE
        if scheduler_path.exists() or scheduler_path.is_symlink():
            try:
                scheduler = _read_json(scheduler_path, root=root)
            except (LifecycleError, OSError) as exc:
                raise PoolRegistryError("openshell_pool_scheduler_unavailable") from exc
            leases = scheduler.get("leases")
            if not isinstance(leases, list) or not all(isinstance(item, dict) for item in leases):
                raise PoolRegistryError("openshell_pool_scheduler_invalid")
            if any(
                item.get("scope_id") == scope_id and item.get("run_id") == run_id
                for item in leases
            ):
                raise PoolRegistryError("openshell_pool_unregister_live_leases")
        updated = {
            **registry,
            "generation": registry["generation"] + 1,
            "bindings": [item for item in registry["bindings"] if item["scope_id"] != scope_id],
        }
        _write_json(root / REGISTRY_RELATIVE, updated, root=root)
        return updated


def resolve(
    *,
    market: str,
    company: str,
    project_root: Path = REPO_ROOT,
) -> PoolRoute:
    root = _project_root(project_root)
    market, company, _analysis, scope_id = _canonical_scope(root, market, company)
    registry = load_registry(project_root=root)
    entry = next((item for item in registry["bindings"] if item["scope_id"] == scope_id), None)
    if entry is None:
        return PoolRoute(target="host", market=market, company=company)
    binding = _validated_binding(root, Path(entry["active"]), int(entry["local_port"]))
    if binding.entry != entry:
        raise PoolRegistryError("openshell_pool_binding_stale")
    return PoolRoute(
        target="openshell",
        market=market,
        company=company,
        base=f"http://{FORWARD_HOST}:{entry['local_port']}/v1/runs",
        api_key=binding.api_key,
        run_id=entry["run_id"],
        session_namespace=entry["session_namespace"],
        analysis_relative_path=entry["analysis_relative_path"],
    )


def allocate_local_port(
    *,
    project_root: Path = REPO_ROOT,
    first: int = FIRST_POOL_PORT,
    last: int = LAST_POOL_PORT,
    ttl_seconds: int = PORT_RESERVATION_TTL_SECONDS,
    now: int | None = None,
    available: Callable[[str, int], bool] | None = None,
) -> PortReservation:
    if not FIRST_POOL_PORT <= first <= last <= LAST_POOL_PORT:
        raise PoolRegistryError("openshell_pool_port_range_invalid")
    if not isinstance(ttl_seconds, int) or isinstance(ttl_seconds, bool) or not 1 <= ttl_seconds <= 3600:
        raise PoolRegistryError("openshell_pool_port_reservation_ttl_invalid")
    current_time = int(time.time()) if now is None else now
    if not isinstance(current_time, int) or isinstance(current_time, bool) or current_time < 0:
        raise PoolRegistryError("openshell_pool_clock_invalid")
    root = _project_root(project_root)

    def socket_available(host: str, port: int) -> bool:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
                listener.bind((host, port))
            return True
        except OSError:
            return False

    checker = available or socket_available
    with _registry_lock(root):
        registry = load_registry(project_root=root)
        reservations, _changed = _load_port_reservations(root, now=current_time)
        reserved = {
            item["local_port"]
            for item in [*registry["bindings"], *reservations["reservations"]]
        }
        for port in range(first, last + 1):
            if port in reserved or not checker(FORWARD_HOST, port):
                continue
            token = f"reservation-{secrets.token_hex(16)}"
            expires_at = current_time + ttl_seconds
            reservations["reservations"].append(
                {
                    "local_port": port,
                    "reservation_token": token,
                    "expires_at": expires_at,
                }
            )
            reservations["reservations"].sort(key=lambda item: item["local_port"])
            _write_port_reservations(root, reservations)
            return PortReservation(
                local_port=port,
                reservation_token=token,
                expires_at=expires_at,
            )
    raise PoolRegistryError("openshell_pool_no_port_available")


def prune_unused_port_reservations(
    *,
    project_root: Path = REPO_ROOT,
    now: int | None = None,
    available: Callable[[str, int], bool] | None = None,
) -> dict[str, Any]:
    current_time = int(time.time()) if now is None else now
    if not isinstance(current_time, int) or isinstance(current_time, bool) or current_time < 0:
        raise PoolRegistryError("openshell_pool_clock_invalid")
    root = _project_root(project_root)

    def socket_available(host: str, port: int) -> bool:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
                listener.bind((host, port))
            return True
        except OSError:
            return False

    checker = available or socket_available
    with _registry_lock(root):
        registry = load_registry(project_root=root)
        reservations, changed = _load_port_reservations(root, now=current_time)
        bound_ports = {int(item["local_port"]) for item in registry["bindings"]}
        retained = []
        pruned_ports: list[int] = []
        for item in reservations["reservations"]:
            port = int(item["local_port"])
            if port in bound_ports or not checker(FORWARD_HOST, port):
                retained.append(item)
                continue
            pruned_ports.append(port)
        if pruned_ports or changed:
            _write_port_reservations(
                root,
                {
                    "schema_version": PORT_RESERVATION_SCHEMA,
                    "reservations": retained,
                },
            )
        return {
            "ok": True,
            "schema_version": PORT_RESERVATION_SCHEMA,
            "pruned": len(pruned_ports),
            "ports": pruned_ports,
        }


def plan_slot(
    *,
    market: str,
    company: str,
    run_id: str,
    local_port: int,
    project_root: Path = REPO_ROOT,
) -> PoolSlotPlan:
    root = _project_root(project_root)
    market, company, analysis, scope_id = _canonical_scope(root, market, company)
    if RUN_ID_RE.fullmatch(run_id) is None:
        raise PoolRegistryError("openshell_pool_run_id_invalid")
    if not isinstance(local_port, int) or isinstance(local_port, bool) or not 1024 <= local_port <= 65535:
        raise PoolRegistryError("openshell_pool_local_port_invalid")
    state = SLOTS_RELATIVE / scope_id
    return PoolSlotPlan(
        scope_id=scope_id,
        market=market,
        company=company,
        run_id=run_id,
        state_relative=state.as_posix(),
        active_relative=(state / "active.json").as_posix(),
        analysis_relative_path=analysis.as_posix(),
        sandbox_name=f"{SANDBOX_PREFIX}{run_id}",
        runtime_snapshot=(RUNTIME_SNAPSHOT_RELATIVE / run_id).as_posix(),
        local_port=local_port,
        target_port=TARGET_PORT,
        session_namespace=f"siq:openshell:pool:{scope_id}:{run_id}:{PROFILE}",
    )


def _sanitized_registry(value: Mapping[str, Any]) -> dict[str, Any]:
    # Registry itself is credential-free. Keep this copy helper explicit so CLI
    # output can never accidentally grow a secret field with the route object.
    return json.loads(json.dumps(value, ensure_ascii=True))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=REPO_ROOT)
    subparsers = parser.add_subparsers(dest="command", required=True)
    register = subparsers.add_parser("register")
    register.add_argument("--active", type=Path, required=True)
    register.add_argument("--local-port", type=int, required=True)
    register.add_argument("--reservation-token")
    register.add_argument("--replace", action="store_true")
    unregister_parser = subparsers.add_parser("unregister")
    unregister_parser.add_argument("--market", required=True)
    unregister_parser.add_argument("--company", required=True)
    unregister_parser.add_argument("--run-id", required=True)
    resolve_parser = subparsers.add_parser("resolve")
    resolve_parser.add_argument("--market", required=True)
    resolve_parser.add_argument("--company", required=True)
    allocate = subparsers.add_parser("allocate-port")
    allocate.add_argument("--first", type=int, default=FIRST_POOL_PORT)
    allocate.add_argument("--last", type=int, default=LAST_POOL_PORT)
    plan = subparsers.add_parser("plan")
    plan.add_argument("--market", required=True)
    plan.add_argument("--company", required=True)
    plan.add_argument("--run-id", required=True)
    plan.add_argument("--local-port", type=int, required=True)
    subparsers.add_parser("list")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "register":
            result: Any = register_active(
                active=args.active,
                local_port=args.local_port,
                reservation_token=args.reservation_token,
                replace=args.replace,
                project_root=args.project_root,
            )
        elif args.command == "unregister":
            result = unregister(
                market=args.market,
                company=args.company,
                run_id=args.run_id,
                project_root=args.project_root,
            )
        elif args.command == "resolve":
            route = resolve(market=args.market, company=args.company, project_root=args.project_root)
            result = {
                "target": route.target,
                "market": route.market,
                "company": route.company,
                "base": route.base,
                "run_id": route.run_id,
                "session_namespace": route.session_namespace,
                "analysis_relative_path": route.analysis_relative_path,
                "credential_returned": False,
            }
        elif args.command == "allocate-port":
            result = asdict(
                allocate_local_port(
                    project_root=args.project_root,
                    first=args.first,
                    last=args.last,
                )
            )
        elif args.command == "plan":
            result = asdict(
                plan_slot(
                    market=args.market,
                    company=args.company,
                    run_id=args.run_id,
                    local_port=args.local_port,
                    project_root=args.project_root,
                )
            )
        else:
            result = _sanitized_registry(load_registry(project_root=args.project_root))
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
        return 0
    except PoolRegistryError as exc:
        print(json.dumps({"ok": False, "error": exc.code}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
