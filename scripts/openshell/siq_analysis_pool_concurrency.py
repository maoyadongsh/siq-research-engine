#!/usr/bin/python3 -IB
"""Concurrent admission, affinity, draining, and TTL for SIQ OpenShell slots."""

from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.openshell import siq_analysis_pool_registry as pool  # noqa: E402
from scripts.openshell.siq_analysis_lifecycle import (  # noqa: E402
    LifecycleError,
    _read_json,
    _read_private,
    _write_json,
    _write_private_atomic,
)

SCHEMA_VERSION = "siq.openshell.siq_analysis_pool_scheduler.v3"
PREVIOUS_SCHEMA_VERSION = "siq.openshell.siq_analysis_pool_scheduler.v2"
LEGACY_SCHEMA_VERSION = "siq.openshell.siq_analysis_pool_scheduler.v1"
SCHEDULER_RELATIVE = pool.POOL_RELATIVE / "scheduler.json"
IDENTITY_KEY_RELATIVE = pool.POOL_RELATIVE / "identity-hmac.key"
RECOVERY_LOCK_RELATIVE = pool.POOL_RELATIVE / "api-recovery.lock"
DEFAULT_LEASE_TTL_SECONDS = 900
MIN_LEASE_TTL_SECONDS = 30
MAX_LEASE_TTL_SECONDS = 3600
DEFAULT_MAX_TOTAL_ACTIVE = 16
DEFAULT_MAX_WAITING_PER_BINDING = 64
DEFAULT_MAX_TOTAL_LEASES = 1024
DEFAULT_DRAIN_TIMEOUT_SECONDS = 300
TRAFFIC_STATES = {"accepting", "draining", "failed"}
LEASE_STATES = {"active", "waiting", "orphaned"}
IDENTITY_HASH_RE = re.compile(r"[0-9a-f]{32}\Z")
LEASE_ID_RE = re.compile(r"lease-[0-9a-f]{32}\Z")
OWNER_TOKEN_RE = re.compile(r"owner-[0-9a-f]{64}\Z")
OWNER_TOKEN_HASH_RE = re.compile(r"[0-9a-f]{64}\Z")

SCHEDULER_FIELDS = {
    "schema_version",
    "profile",
    "generation",
    "next_owner_generation",
    "max_total_active",
    "bindings",
    "leases",
}
BINDING_FIELDS = {
    "scope_id",
    "run_id",
    "traffic_state",
    "max_active",
    "lease_ttl_seconds",
    "write_isolation",
}
LEASE_FIELDS = {
    "lease_id",
    "owner_token_hash",
    "owner_generation",
    "run_bound",
    "tenant_hash",
    "user_hash",
    "session_hash",
    "scope_id",
    "run_id",
    "state",
    "enqueue_sequence",
    "created_at",
    "last_seen_at",
    "expires_at",
    "write_relative_path",
}
PREVIOUS_LEASE_FIELDS = LEASE_FIELDS - {"run_bound"}
LEGACY_SCHEDULER_FIELDS = SCHEDULER_FIELDS - {"next_owner_generation"}
LEGACY_LEASE_FIELDS = PREVIOUS_LEASE_FIELDS - {"owner_token_hash", "owner_generation"}


class PoolConcurrencyError(RuntimeError):
    """A stable, secret-free scheduler failure."""

    def __init__(self, code: str, *, retryable: bool = False) -> None:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,95}", code):
            code = "openshell_pool_concurrency_invalid_error"
        self.code = code
        self.retryable = retryable
        super().__init__(code)


@dataclass(frozen=True, repr=False)
class PoolAdmission:
    status: Literal["host", "active", "queued"]
    market: str
    company: str
    lease_id: str = ""
    owner_token: str = ""
    owner_generation: int = 0
    run_bound: bool = False
    queue_position: int = 0
    expires_at: int = 0
    base: str = ""
    api_key: str = ""
    run_id: str = ""
    session_namespace: str = ""
    # This is an anonymous workspace hint, not a filesystem isolation boundary.
    write_relative_path: str = ""
    scope_id: str = ""
    analysis_relative_path: str = ""

    def __repr__(self) -> str:
        return (
            "PoolAdmission("
            f"status={self.status!r}, market={self.market!r}, company={self.company!r}, "
            f"lease_id={self.lease_id!r}, queue_position={self.queue_position}, "
            f"owner_token='<redacted>', owner_generation={self.owner_generation}, "
            f"run_bound={self.run_bound}, "
            f"expires_at={self.expires_at}, base={self.base!r}, run_id={self.run_id!r}, "
            f"session_namespace={self.session_namespace!r}, "
            f"write_relative_path={self.write_relative_path!r}, scope_id={self.scope_id!r}, "
            f"analysis_relative_path={self.analysis_relative_path!r}, api_key='<redacted>')"
        )


def _empty_scheduler() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "profile": pool.PROFILE,
        "generation": 0,
        "next_owner_generation": 1,
        "max_total_active": DEFAULT_MAX_TOTAL_ACTIVE,
        "bindings": [],
        "leases": [],
    }


def _identity(value: str, *, field: str) -> str:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or not value
        or len(value.encode("utf-8")) > 512
        or any(ord(character) < 0x20 for character in value)
    ):
        raise PoolConcurrencyError(f"openshell_pool_{field}_invalid")
    return value


def _identity_key(project_root: Path, *, allow_create: bool) -> bytes:
    path = project_root / IDENTITY_KEY_RELATIVE
    if not path.exists() and not path.is_symlink():
        if not allow_create:
            raise PoolConcurrencyError("openshell_pool_identity_key_missing")
        try:
            _write_private_atomic(path, (secrets.token_hex(32) + "\n").encode("ascii"), root=project_root)
        except (LifecycleError, OSError) as exc:
            raise PoolConcurrencyError("openshell_pool_identity_key_create_failed") from exc
    try:
        raw = _read_private(path, root=project_root, max_bytes=128).decode("ascii").strip()
    except (LifecycleError, UnicodeError, OSError) as exc:
        raise PoolConcurrencyError("openshell_pool_identity_key_invalid") from exc
    if re.fullmatch(r"[0-9a-f]{64}", raw) is None:
        raise PoolConcurrencyError("openshell_pool_identity_key_invalid")
    return bytes.fromhex(raw)


def _digest(key: bytes, label: str, value: str) -> str:
    return hmac.new(key, f"{label}\0{value}".encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def _lease_identity(key: bytes, tenant_id: str, user_id: str, session_id: str) -> dict[str, str]:
    tenant_hash = _digest(key, "tenant", _identity(tenant_id, field="tenant_id"))
    user_hash = _digest(key, "user", _identity(user_id, field="user_id"))
    session_hash = _digest(key, "session", _identity(session_id, field="session_id"))
    lease_digest = hmac.new(
        key,
        f"lease\0{tenant_hash}\0{user_hash}\0{session_hash}".encode("ascii"),
        hashlib.sha256,
    ).hexdigest()[:32]
    return {
        "lease_id": f"lease-{lease_digest}",
        "tenant_hash": tenant_hash,
        "user_hash": user_hash,
        "session_hash": session_hash,
    }


def _new_owner(scheduler: dict[str, Any]) -> tuple[str, str, int]:
    token = f"owner-{secrets.token_hex(32)}"
    generation = int(scheduler["next_owner_generation"])
    scheduler["next_owner_generation"] = generation + 1
    digest = hashlib.sha256(token.encode("ascii")).hexdigest()
    return token, digest, generation


def _owner_credentials(owner_token: str, owner_generation: int) -> tuple[str, int]:
    if not isinstance(owner_token, str) or OWNER_TOKEN_RE.fullmatch(owner_token) is None:
        raise PoolConcurrencyError("openshell_pool_owner_credentials_invalid")
    if not isinstance(owner_generation, int) or isinstance(owner_generation, bool) or owner_generation < 1:
        raise PoolConcurrencyError("openshell_pool_owner_credentials_invalid")
    return hashlib.sha256(owner_token.encode("ascii")).hexdigest(), owner_generation


def _assert_owner(lease: Mapping[str, Any], owner_token_hash: str, owner_generation: int) -> None:
    if (
        not hmac.compare_digest(str(lease["owner_token_hash"]), owner_token_hash)
        or lease["owner_generation"] != owner_generation
    ):
        raise PoolConcurrencyError("openshell_pool_owner_mismatch")


def _assert_recovery_capability(project_root: Path, recovery_lock_fd: int) -> None:
    """Require the caller to hold the fixed API recovery flock."""

    if not isinstance(recovery_lock_fd, int) or isinstance(recovery_lock_fd, bool) or recovery_lock_fd < 0:
        raise PoolConcurrencyError("openshell_pool_recovery_capability_invalid")
    lock_path = project_root / RECOVERY_LOCK_RELATIVE
    try:
        path_info = lock_path.lstat()
        descriptor_info = os.fstat(recovery_lock_fd)
        if (
            stat.S_ISLNK(path_info.st_mode)
            or not stat.S_ISREG(path_info.st_mode)
            or not stat.S_ISREG(descriptor_info.st_mode)
            or path_info.st_uid != os.geteuid()
            or descriptor_info.st_uid != os.geteuid()
            or path_info.st_nlink != 1
            or descriptor_info.st_nlink != 1
            or stat.S_IMODE(path_info.st_mode) & 0o077
            or stat.S_IMODE(descriptor_info.st_mode) & 0o077
            or (path_info.st_dev, path_info.st_ino) != (descriptor_info.st_dev, descriptor_info.st_ino)
        ):
            raise PoolConcurrencyError("openshell_pool_recovery_capability_invalid")
        fcntl.flock(recovery_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except PoolConcurrencyError:
        raise
    except (BlockingIOError, OSError) as exc:
        raise PoolConcurrencyError("openshell_pool_recovery_capability_invalid") from exc


def _binding_control(entry: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "scope_id": entry["scope_id"],
        "run_id": entry["run_id"],
        "traffic_state": "accepting",
        # The current canary grants the whole company analysis root. Until a
        # task-leaf policy exists, concurrency safety requires one writer.
        "max_active": 1,
        "lease_ttl_seconds": DEFAULT_LEASE_TTL_SECONDS,
        "write_isolation": "exclusive_company_analysis_root",
    }


def _validate_scheduler(value: Mapping[str, Any]) -> dict[str, Any]:
    if (
        set(value) != SCHEDULER_FIELDS
        or value.get("schema_version") != SCHEMA_VERSION
        or value.get("profile") != pool.PROFILE
        or not isinstance(value.get("generation"), int)
        or isinstance(value.get("generation"), bool)
        or value["generation"] < 0
        or not isinstance(value.get("next_owner_generation"), int)
        or isinstance(value.get("next_owner_generation"), bool)
        or value["next_owner_generation"] < 1
        or not isinstance(value.get("max_total_active"), int)
        or isinstance(value.get("max_total_active"), bool)
        or not 1 <= value["max_total_active"] <= 1024
        or not isinstance(value.get("bindings"), list)
        or not isinstance(value.get("leases"), list)
    ):
        raise PoolConcurrencyError("openshell_pool_scheduler_invalid")
    binding_keys: set[tuple[str, str]] = set()
    for item in value["bindings"]:
        if (
            not isinstance(item, dict)
            or set(item) != BINDING_FIELDS
            or not isinstance(item.get("scope_id"), str)
            or pool.SCOPE_ID_RE.fullmatch(item["scope_id"]) is None
            or not isinstance(item.get("run_id"), str)
            or pool.RUN_ID_RE.fullmatch(item["run_id"]) is None
            or item.get("traffic_state") not in TRAFFIC_STATES
            or item.get("max_active") != 1
            or not isinstance(item.get("lease_ttl_seconds"), int)
            or not MIN_LEASE_TTL_SECONDS <= item["lease_ttl_seconds"] <= MAX_LEASE_TTL_SECONDS
            or item.get("write_isolation") != "exclusive_company_analysis_root"
        ):
            raise PoolConcurrencyError("openshell_pool_scheduler_invalid")
        key = (item["scope_id"], item["run_id"])
        if key in binding_keys:
            raise PoolConcurrencyError("openshell_pool_scheduler_collision")
        binding_keys.add(key)
    lease_ids: set[str] = set()
    owner_generations: set[int] = set()
    enqueue_sequences: set[int] = set()
    identity_tuples: set[tuple[str, str, str]] = set()
    for lease in value["leases"]:
        if (
            not isinstance(lease, dict)
            or set(lease) != LEASE_FIELDS
            or not isinstance(lease.get("lease_id"), str)
            or LEASE_ID_RE.fullmatch(lease["lease_id"]) is None
            or not isinstance(lease.get("owner_token_hash"), str)
            or OWNER_TOKEN_HASH_RE.fullmatch(lease["owner_token_hash"]) is None
            or not isinstance(lease.get("owner_generation"), int)
            or isinstance(lease.get("owner_generation"), bool)
            or not 1 <= lease["owner_generation"] < value["next_owner_generation"]
            or not isinstance(lease.get("run_bound"), bool)
            or (lease["state"] == "waiting" and lease["run_bound"])
            or (lease["state"] == "orphaned" and not lease["run_bound"])
            or any(
                not isinstance(lease.get(field), str) or IDENTITY_HASH_RE.fullmatch(lease[field]) is None
                for field in ("tenant_hash", "user_hash", "session_hash")
            )
            or not isinstance(lease.get("scope_id"), str)
            or pool.SCOPE_ID_RE.fullmatch(lease["scope_id"]) is None
            or not isinstance(lease.get("run_id"), str)
            or pool.RUN_ID_RE.fullmatch(lease["run_id"]) is None
            or lease.get("state") not in LEASE_STATES
            or not isinstance(lease.get("enqueue_sequence"), int)
            or isinstance(lease["enqueue_sequence"], bool)
            or lease["enqueue_sequence"] < 1
            or any(
                not isinstance(lease.get(field), int) or isinstance(lease[field], bool) or lease[field] < 0
                for field in ("created_at", "last_seen_at", "expires_at")
            )
            or lease["created_at"] > lease["last_seen_at"]
            or (lease["state"] != "orphaned" and lease["last_seen_at"] >= lease["expires_at"])
            or not isinstance(lease.get("write_relative_path"), str)
        ):
            raise PoolConcurrencyError("openshell_pool_scheduler_invalid")
        identity_tuple = (lease["tenant_hash"], lease["user_hash"], lease["session_hash"])
        if (
            lease["lease_id"] in lease_ids
            or lease["owner_generation"] in owner_generations
            or lease["enqueue_sequence"] in enqueue_sequences
            or identity_tuple in identity_tuples
        ):
            raise PoolConcurrencyError("openshell_pool_scheduler_collision")
        lease_ids.add(lease["lease_id"])
        owner_generations.add(lease["owner_generation"])
        enqueue_sequences.add(lease["enqueue_sequence"])
        identity_tuples.add(identity_tuple)
    return json.loads(json.dumps(value, ensure_ascii=True))


def _migrate_scheduler(value: Mapping[str, Any]) -> dict[str, Any] | None:
    schema_version = value.get("schema_version")
    if schema_version not in {LEGACY_SCHEMA_VERSION, PREVIOUS_SCHEMA_VERSION}:
        return None
    expected_scheduler_fields = LEGACY_SCHEDULER_FIELDS if schema_version == LEGACY_SCHEMA_VERSION else SCHEDULER_FIELDS
    expected_lease_fields = LEGACY_LEASE_FIELDS if schema_version == LEGACY_SCHEMA_VERSION else PREVIOUS_LEASE_FIELDS
    if set(value) != expected_scheduler_fields or not isinstance(value.get("leases"), list):
        raise PoolConcurrencyError("openshell_pool_scheduler_invalid")
    migrated = json.loads(json.dumps(value, ensure_ascii=True))
    migrated["schema_version"] = SCHEMA_VERSION
    if schema_version == LEGACY_SCHEMA_VERSION:
        migrated["next_owner_generation"] = 1
    for lease in migrated["leases"]:
        if not isinstance(lease, dict) or set(lease) != expected_lease_fields:
            raise PoolConcurrencyError("openshell_pool_scheduler_invalid")
        if schema_version == LEGACY_SCHEMA_VERSION:
            _, token_hash, generation = _new_owner(migrated)
            lease["owner_token_hash"] = token_hash
            lease["owner_generation"] = generation
        # A pre-v3 active lease may already own a Hermes run. Waiting leases
        # have never been delivered and remain safe to remove.
        lease["run_bound"] = lease["state"] != "waiting"
    if schema_version == LEGACY_SCHEMA_VERSION:
        # Legacy owners had no recoverable token. Keep possible writers fenced
        # and drop only waiters that could never have created a run.
        migrated["leases"] = [lease for lease in migrated["leases"] if lease["state"] != "waiting"]
        for lease in migrated["leases"]:
            if lease["state"] == "active":
                lease["state"] = "orphaned"
    return _validate_scheduler(migrated)


def _load_scheduler(project_root: Path) -> dict[str, Any]:
    path = project_root / SCHEDULER_RELATIVE
    if not path.exists() and not path.is_symlink():
        return _empty_scheduler()
    try:
        raw = _read_json(path, root=project_root)
        migrated = _migrate_scheduler(raw)
        if migrated is not None:
            _write_scheduler(project_root, migrated)
            return migrated
        return _validate_scheduler(raw)
    except (LifecycleError, OSError) as exc:
        raise PoolConcurrencyError("openshell_pool_scheduler_unavailable") from exc


def _sync_bindings(scheduler: dict[str, Any], registry: Mapping[str, Any]) -> bool:
    controls = {(item["scope_id"], item["run_id"]): item for item in scheduler["bindings"]}
    expected_keys = {(item["scope_id"], item["run_id"]) for item in registry["bindings"]}
    updated = [
        controls.get((entry["scope_id"], entry["run_id"]), _binding_control(entry)) for entry in registry["bindings"]
    ]
    updated.sort(key=lambda item: (item["scope_id"], item["run_id"]))
    stale = [lease for lease in scheduler["leases"] if (lease["scope_id"], lease["run_id"]) not in expected_keys]
    if any(lease["state"] in {"active", "orphaned"} for lease in stale):
        raise PoolConcurrencyError("openshell_pool_registry_removed_with_live_lease")
    stale_lease_count = len(scheduler["leases"])
    scheduler["leases"] = [
        lease for lease in scheduler["leases"] if (lease["scope_id"], lease["run_id"]) in expected_keys
    ]
    changed = updated != scheduler["bindings"] or len(scheduler["leases"]) != stale_lease_count
    scheduler["bindings"] = updated
    return changed


def _prune_expired(scheduler: dict[str, Any], *, now: int) -> bool:
    changed = False
    retained = []
    for lease in scheduler["leases"]:
        if lease["expires_at"] > now or lease["state"] == "orphaned":
            retained.append(lease)
            continue
        changed = True
        if lease["state"] == "active":
            if lease["run_bound"]:
                lease["state"] = "orphaned"
                retained.append(lease)
    scheduler["leases"] = retained
    return changed


def _control(scheduler: Mapping[str, Any], scope_id: str, run_id: str) -> dict[str, Any]:
    control = next(
        (item for item in scheduler["bindings"] if item["scope_id"] == scope_id and item["run_id"] == run_id),
        None,
    )
    if control is None:
        raise PoolConcurrencyError("openshell_pool_binding_control_missing")
    return control


def _promote_waiters(scheduler: dict[str, Any]) -> bool:
    changed = False
    total_active = sum(lease["state"] in {"active", "orphaned"} for lease in scheduler["leases"])
    for control in scheduler["bindings"]:
        if control["traffic_state"] != "accepting":
            continue
        active = sum(
            lease["state"] in {"active", "orphaned"}
            and lease["scope_id"] == control["scope_id"]
            and lease["run_id"] == control["run_id"]
            for lease in scheduler["leases"]
        )
        waiters = sorted(
            (
                lease
                for lease in scheduler["leases"]
                if lease["state"] == "waiting"
                and lease["scope_id"] == control["scope_id"]
                and lease["run_id"] == control["run_id"]
            ),
            key=lambda lease: lease["enqueue_sequence"],
        )
        while waiters and active < control["max_active"] and total_active < scheduler["max_total_active"]:
            lease = waiters.pop(0)
            lease["state"] = "active"
            active += 1
            total_active += 1
            changed = True
    return changed


def _queue_position(scheduler: Mapping[str, Any], lease: Mapping[str, Any]) -> int:
    waiters = sorted(
        (
            item
            for item in scheduler["leases"]
            if item["state"] == "waiting"
            and item["scope_id"] == lease["scope_id"]
            and item["run_id"] == lease["run_id"]
        ),
        key=lambda item: item["enqueue_sequence"],
    )
    return next(index for index, item in enumerate(waiters, start=1) if item["lease_id"] == lease["lease_id"])


def _write_scheduler(project_root: Path, scheduler: dict[str, Any]) -> None:
    scheduler["generation"] += 1
    scheduler["bindings"].sort(key=lambda item: (item["scope_id"], item["run_id"]))
    scheduler["leases"].sort(key=lambda item: item["lease_id"])
    _validate_scheduler(scheduler)
    try:
        _write_json(project_root / SCHEDULER_RELATIVE, scheduler, root=project_root)
    except (LifecycleError, OSError) as exc:
        raise PoolConcurrencyError("openshell_pool_scheduler_write_failed") from exc


def _admission(
    *,
    route: pool.PoolRoute,
    lease: Mapping[str, Any],
    status: Literal["active", "queued"],
    owner_token: str,
    queue_position: int = 0,
) -> PoolAdmission:
    session_namespace = ""
    base = ""
    api_key = ""
    if status == "active":
        # The lease ID is a 128-bit HMAC over tenant, user, and session.  Use
        # the complete pseudonymous identifier instead of separately truncated
        # identity hashes: within one tenant/user pair, a 12-hex session suffix
        # would otherwise leave only 48 bits of namespace collision resistance.
        lease_digest = str(lease["lease_id"]).removeprefix("lease-")
        session_namespace = f"{route.session_namespace}:l{lease_digest}"
        base = route.base
        api_key = route.api_key
    return PoolAdmission(
        status=status,
        market=route.market,
        company=route.company,
        lease_id=str(lease["lease_id"]),
        owner_token=owner_token,
        owner_generation=int(lease["owner_generation"]),
        run_bound=bool(lease["run_bound"]),
        queue_position=queue_position,
        expires_at=int(lease["expires_at"]),
        base=base,
        api_key=api_key,
        run_id=route.run_id,
        session_namespace=session_namespace,
        write_relative_path=str(lease["write_relative_path"]),
        scope_id=str(lease["scope_id"]),
        analysis_relative_path=route.analysis_relative_path,
    )


def takeover_recovery(
    *,
    tenant_id: str,
    user_id: str,
    session_id: str,
    expected_lease_id: str | None = None,
    expected_scope_id: str | None = None,
    expected_run_id: str | None = None,
    expected_owner_generation: int | None = None,
    recovery_lock_fd: int,
    now: int | None = None,
    project_root: Path = REPO_ROOT,
) -> PoolAdmission:
    """Fence a crashed API owner and take over its exact durable pool lease.

    Recovery is intentionally identity driven.  The caller never receives the
    previous owner token and does not need it: the registry lock protects one
    atomic owner-token/generation rotation, making all pre-restart credentials
    stale before this function returns.
    """

    root = pool._project_root(project_root)
    _assert_recovery_capability(root, recovery_lock_fd)
    current_time = int(time.time()) if now is None else now
    if not isinstance(current_time, int) or isinstance(current_time, bool) or current_time < 0:
        raise PoolConcurrencyError("openshell_pool_clock_invalid")
    if expected_lease_id is not None and (
        not isinstance(expected_lease_id, str) or LEASE_ID_RE.fullmatch(expected_lease_id) is None
    ):
        raise PoolConcurrencyError("openshell_pool_recovery_lease_invalid")
    if expected_scope_id is not None and (
        not isinstance(expected_scope_id, str) or pool.SCOPE_ID_RE.fullmatch(expected_scope_id) is None
    ):
        raise PoolConcurrencyError("openshell_pool_recovery_scope_invalid")
    if expected_run_id is not None and (
        not isinstance(expected_run_id, str) or pool.RUN_ID_RE.fullmatch(expected_run_id) is None
    ):
        raise PoolConcurrencyError("openshell_pool_recovery_binding_invalid")
    if expected_owner_generation is not None and (
        not isinstance(expected_owner_generation, int)
        or isinstance(expected_owner_generation, bool)
        or expected_owner_generation < 1
    ):
        raise PoolConcurrencyError("openshell_pool_recovery_owner_generation_invalid")

    with pool._registry_lock(root):
        registry = pool.load_registry(project_root=root)
        scheduler = _load_scheduler(root)
        changed = _sync_bindings(scheduler, registry)
        changed = _prune_expired(scheduler, now=current_time) or changed
        key_path = root / IDENTITY_KEY_RELATIVE
        if not scheduler["leases"] and not key_path.exists() and not key_path.is_symlink():
            if changed:
                _write_scheduler(root, scheduler)
            raise PoolConcurrencyError("openshell_pool_recovery_lease_not_found")
        key = _identity_key(root, allow_create=False)
        identity = _lease_identity(key, tenant_id, user_id, session_id)
        lease = next(
            (item for item in scheduler["leases"] if item["lease_id"] == identity["lease_id"]),
            None,
        )
        if lease is None:
            if changed:
                _write_scheduler(root, scheduler)
            raise PoolConcurrencyError("openshell_pool_recovery_lease_not_found")
        if expected_lease_id is not None and lease["lease_id"] != expected_lease_id:
            if changed:
                _write_scheduler(root, scheduler)
            raise PoolConcurrencyError("openshell_pool_recovery_identity_conflict")
        if expected_scope_id is not None and lease["scope_id"] != expected_scope_id:
            if changed:
                _write_scheduler(root, scheduler)
            raise PoolConcurrencyError("openshell_pool_recovery_identity_conflict")
        if expected_run_id is not None and lease["run_id"] != expected_run_id:
            if changed:
                _write_scheduler(root, scheduler)
            raise PoolConcurrencyError("openshell_pool_recovery_identity_conflict")
        if expected_owner_generation is not None and lease["owner_generation"] != expected_owner_generation:
            if changed:
                _write_scheduler(root, scheduler)
            raise PoolConcurrencyError("openshell_pool_recovery_identity_conflict")

        registry_entries = [
            item
            for item in registry["bindings"]
            if item["scope_id"] == lease["scope_id"] and item["run_id"] == lease["run_id"]
        ]
        if len(registry_entries) != 1:
            if changed:
                _write_scheduler(root, scheduler)
            raise PoolConcurrencyError("openshell_pool_recovery_binding_conflict")
        entry = registry_entries[0]
        route = pool.resolve(
            market=str(entry["market"]),
            company=str(entry["company"]),
            project_root=root,
        )
        if (
            route.target != "openshell"
            or route.run_id != lease["run_id"]
            or route.analysis_relative_path != entry["analysis_relative_path"]
        ):
            if changed:
                _write_scheduler(root, scheduler)
            raise PoolConcurrencyError("openshell_pool_recovery_binding_conflict")

        control = _control(scheduler, lease["scope_id"], lease["run_id"])
        owner_token, owner_token_hash, owner_generation = _new_owner(scheduler)
        lease["owner_token_hash"] = owner_token_hash
        lease["owner_generation"] = owner_generation
        lease["last_seen_at"] = current_time
        lease["expires_at"] = current_time + control["lease_ttl_seconds"]
        if lease["state"] == "orphaned":
            lease["state"] = "active"
        _write_scheduler(root, scheduler)
        return _admission(
            route=route,
            lease=lease,
            status="queued" if lease["state"] == "waiting" else "active",
            owner_token=owner_token,
            queue_position=(_queue_position(scheduler, lease) if lease["state"] == "waiting" else 0),
        )


def acquire(
    *,
    market: str,
    company: str,
    tenant_id: str,
    user_id: str,
    session_id: str,
    expected_run_id: str | None = None,
    now: int | None = None,
    project_root: Path = REPO_ROOT,
) -> PoolAdmission:
    root = pool._project_root(project_root)
    current_time = int(time.time()) if now is None else now
    if not isinstance(current_time, int) or isinstance(current_time, bool) or current_time < 0:
        raise PoolConcurrencyError("openshell_pool_clock_invalid")
    with pool._registry_lock(root):
        route = pool.resolve(market=market, company=company, project_root=root)
        registry = pool.load_registry(project_root=root)
        scheduler = _load_scheduler(root)
        changed = _sync_bindings(scheduler, registry)
        changed = _prune_expired(scheduler, now=current_time) or changed
        changed = _promote_waiters(scheduler) or changed
        if route.target == "host":
            if scheduler["leases"]:
                key = _identity_key(root, allow_create=False)
                identity = _lease_identity(key, tenant_id, user_id, session_id)
                existing = next(
                    (lease for lease in scheduler["leases"] if lease["lease_id"] == identity["lease_id"]),
                    None,
                )
                if existing is not None:
                    if changed:
                        _write_scheduler(root, scheduler)
                    raise PoolConcurrencyError("openshell_pool_session_scope_conflict")
            if changed:
                _write_scheduler(root, scheduler)
            return PoolAdmission(status="host", market=route.market, company=route.company)
        registry_entry = next(
            item for item in registry["bindings"] if item["market"] == route.market and item["company"] == route.company
        )
        if expected_run_id is not None and route.run_id != expected_run_id:
            if changed:
                _write_scheduler(root, scheduler)
            raise PoolConcurrencyError("openshell_pool_binding_changed", retryable=True)
        key = _identity_key(root, allow_create=not scheduler["leases"])
        identity = _lease_identity(key, tenant_id, user_id, session_id)
        existing = next(
            (lease for lease in scheduler["leases"] if lease["lease_id"] == identity["lease_id"]),
            None,
        )
        if existing is not None and existing["scope_id"] != registry_entry["scope_id"]:
            if changed:
                _write_scheduler(root, scheduler)
            raise PoolConcurrencyError("openshell_pool_session_scope_conflict")
        control = _control(scheduler, registry_entry["scope_id"], registry_entry["run_id"])
        if control["traffic_state"] == "failed":
            if changed:
                _write_scheduler(root, scheduler)
            raise PoolConcurrencyError("openshell_pool_binding_failed", retryable=True)
        if existing is not None:
            if changed:
                _write_scheduler(root, scheduler)
            raise PoolConcurrencyError("openshell_pool_session_reentry", retryable=True)
        if control["traffic_state"] != "accepting":
            if changed:
                _write_scheduler(root, scheduler)
            raise PoolConcurrencyError("openshell_pool_binding_draining", retryable=True)

        active_for_binding = sum(
            lease["state"] in {"active", "orphaned"}
            and lease["scope_id"] == registry_entry["scope_id"]
            and lease["run_id"] == registry_entry["run_id"]
            for lease in scheduler["leases"]
        )
        total_active = sum(lease["state"] in {"active", "orphaned"} for lease in scheduler["leases"])
        state = (
            "active"
            if active_for_binding < control["max_active"] and total_active < scheduler["max_total_active"]
            else "waiting"
        )
        waiting_for_binding = sum(
            lease["state"] == "waiting"
            and lease["scope_id"] == registry_entry["scope_id"]
            and lease["run_id"] == registry_entry["run_id"]
            for lease in scheduler["leases"]
        )
        if state == "waiting" and (
            waiting_for_binding >= DEFAULT_MAX_WAITING_PER_BINDING
            or len(scheduler["leases"]) >= DEFAULT_MAX_TOTAL_LEASES
        ):
            if changed:
                _write_scheduler(root, scheduler)
            raise PoolConcurrencyError("openshell_pool_queue_full", retryable=True)
        write_relative_path = (
            Path(registry_entry["analysis_relative_path"]) / ".work" / "openshell-leases" / identity["lease_id"]
        ).as_posix()
        owner_token, owner_token_hash, owner_generation = _new_owner(scheduler)
        lease = {
            **identity,
            "owner_token_hash": owner_token_hash,
            "owner_generation": owner_generation,
            "run_bound": False,
            "scope_id": registry_entry["scope_id"],
            "run_id": registry_entry["run_id"],
            "state": state,
            "enqueue_sequence": max(
                (item["enqueue_sequence"] for item in scheduler["leases"]),
                default=0,
            )
            + 1,
            "created_at": current_time,
            "last_seen_at": current_time,
            "expires_at": current_time + control["lease_ttl_seconds"],
            "write_relative_path": write_relative_path,
        }
        scheduler["leases"].append(lease)
        _write_scheduler(root, scheduler)
        if state == "waiting":
            return _admission(
                route=route,
                lease=lease,
                status="queued",
                owner_token=owner_token,
                queue_position=_queue_position(scheduler, lease),
            )
        return _admission(
            route=route,
            lease=lease,
            status="active",
            owner_token=owner_token,
        )


def mark_run_bound(
    *,
    market: str,
    company: str,
    tenant_id: str,
    user_id: str,
    session_id: str,
    owner_token: str,
    owner_generation: int,
    expected_run_id: str | None = None,
    now: int | None = None,
    project_root: Path = REPO_ROOT,
) -> PoolAdmission:
    """Fence one active admission before any downstream Hermes run is created."""

    root = pool._project_root(project_root)
    current_time = int(time.time()) if now is None else now
    if not isinstance(current_time, int) or isinstance(current_time, bool) or current_time < 0:
        raise PoolConcurrencyError("openshell_pool_clock_invalid")
    owner_token_hash, validated_generation = _owner_credentials(owner_token, owner_generation)
    with pool._registry_lock(root):
        route = pool.resolve(market=market, company=company, project_root=root)
        if route.target != "openshell":
            raise PoolConcurrencyError("openshell_pool_binding_not_registered")
        registry = pool.load_registry(project_root=root)
        entry = next(
            item for item in registry["bindings"] if item["market"] == route.market and item["company"] == route.company
        )
        if expected_run_id is not None and route.run_id != expected_run_id:
            raise PoolConcurrencyError("openshell_pool_binding_changed", retryable=True)
        scheduler = _load_scheduler(root)
        changed = _sync_bindings(scheduler, registry)
        changed = _prune_expired(scheduler, now=current_time) or changed
        changed = _promote_waiters(scheduler) or changed
        key = _identity_key(root, allow_create=False)
        identity = _lease_identity(key, tenant_id, user_id, session_id)
        lease = next(
            (item for item in scheduler["leases"] if item["lease_id"] == identity["lease_id"]),
            None,
        )
        if lease is None:
            if changed:
                _write_scheduler(root, scheduler)
            raise PoolConcurrencyError("openshell_pool_lease_not_found")
        try:
            _assert_owner(lease, owner_token_hash, validated_generation)
        except PoolConcurrencyError:
            if changed:
                _write_scheduler(root, scheduler)
            raise
        if lease["scope_id"] != entry["scope_id"] or lease["run_id"] != entry["run_id"]:
            if changed:
                _write_scheduler(root, scheduler)
            raise PoolConcurrencyError("openshell_pool_session_scope_conflict")
        if lease["state"] == "orphaned":
            if changed:
                _write_scheduler(root, scheduler)
            raise PoolConcurrencyError("openshell_pool_lease_orphaned", retryable=True)
        if lease["state"] != "active":
            if changed:
                _write_scheduler(root, scheduler)
            raise PoolConcurrencyError("openshell_pool_lease_not_active", retryable=True)
        control = _control(scheduler, entry["scope_id"], entry["run_id"])
        if not lease["run_bound"] and control["traffic_state"] != "accepting":
            if changed:
                _write_scheduler(root, scheduler)
            raise PoolConcurrencyError("openshell_pool_binding_draining", retryable=True)
        lease["run_bound"] = True
        lease["last_seen_at"] = max(lease["last_seen_at"], current_time)
        if control["traffic_state"] == "accepting":
            lease["expires_at"] = lease["last_seen_at"] + control["lease_ttl_seconds"]
        _write_scheduler(root, scheduler)
        return _admission(
            route=route,
            lease=lease,
            status="active",
            owner_token=owner_token,
        )


def release(
    *,
    tenant_id: str,
    user_id: str,
    session_id: str,
    owner_token: str = "",
    owner_generation: int = 0,
    terminal_confirmed: bool = False,
    now: int | None = None,
    project_root: Path = REPO_ROOT,
) -> dict[str, Any]:
    root = pool._project_root(project_root)
    current_time = int(time.time()) if now is None else now
    if not isinstance(current_time, int) or isinstance(current_time, bool) or current_time < 0:
        raise PoolConcurrencyError("openshell_pool_clock_invalid")
    if not isinstance(terminal_confirmed, bool):
        raise PoolConcurrencyError("openshell_pool_terminal_confirmation_invalid")
    with pool._registry_lock(root):
        registry = pool.load_registry(project_root=root)
        scheduler = _load_scheduler(root)
        changed = _sync_bindings(scheduler, registry)
        changed = _prune_expired(scheduler, now=current_time) or changed
        key_path = root / IDENTITY_KEY_RELATIVE
        if not scheduler["leases"] and not key_path.exists() and not key_path.is_symlink():
            if changed:
                _write_scheduler(root, scheduler)
            return {"released": False, "abandoned": False, "lease_id": "", "state": "missing"}
        key = _identity_key(root, allow_create=False)
        identity = _lease_identity(key, tenant_id, user_id, session_id)
        existing = next(
            (lease for lease in scheduler["leases"] if lease["lease_id"] == identity["lease_id"]),
            None,
        )
        if existing is None:
            if changed:
                _write_scheduler(root, scheduler)
            return {
                "released": False,
                "abandoned": False,
                "lease_id": identity["lease_id"],
                "state": "missing",
            }
        owner_token_hash, validated_generation = _owner_credentials(owner_token, owner_generation)
        try:
            _assert_owner(existing, owner_token_hash, validated_generation)
        except PoolConcurrencyError:
            if changed:
                _write_scheduler(root, scheduler)
            raise

        released = False
        abandoned = False
        resulting_state = existing["state"]
        if terminal_confirmed:
            scheduler["leases"] = [lease for lease in scheduler["leases"] if lease["lease_id"] != identity["lease_id"]]
            released = True
            resulting_state = "released"
        elif existing["state"] == "waiting" or not existing["run_bound"]:
            scheduler["leases"] = [lease for lease in scheduler["leases"] if lease["lease_id"] != identity["lease_id"]]
            released = True
            abandoned = True
            resulting_state = "removed"
        else:
            existing["state"] = "orphaned"
            existing["last_seen_at"] = max(existing["last_seen_at"], current_time)
            abandoned = True
            resulting_state = "orphaned"
        changed = _promote_waiters(scheduler) or changed or released or abandoned
        if changed:
            _write_scheduler(root, scheduler)
        return {
            "released": released,
            "abandoned": abandoned,
            "lease_id": identity["lease_id"],
            "state": resulting_state,
        }


def abandon(
    *,
    tenant_id: str,
    user_id: str,
    session_id: str,
    owner_token: str,
    owner_generation: int,
    now: int | None = None,
    project_root: Path = REPO_ROOT,
) -> dict[str, Any]:
    """Persist an unconfirmed outcome without making the writer slot reusable."""
    return release(
        tenant_id=tenant_id,
        user_id=user_id,
        session_id=session_id,
        owner_token=owner_token,
        owner_generation=owner_generation,
        terminal_confirmed=False,
        now=now,
        project_root=project_root,
    )


def heartbeat(
    *,
    market: str,
    company: str,
    tenant_id: str,
    user_id: str,
    session_id: str,
    owner_token: str,
    owner_generation: int,
    expected_run_id: str | None = None,
    now: int | None = None,
    project_root: Path = REPO_ROOT,
) -> PoolAdmission:
    root = pool._project_root(project_root)
    current_time = int(time.time()) if now is None else now
    if not isinstance(current_time, int) or isinstance(current_time, bool) or current_time < 0:
        raise PoolConcurrencyError("openshell_pool_clock_invalid")
    owner_token_hash, validated_generation = _owner_credentials(owner_token, owner_generation)
    with pool._registry_lock(root):
        route = pool.resolve(market=market, company=company, project_root=root)
        if route.target != "openshell":
            raise PoolConcurrencyError("openshell_pool_heartbeat_binding_missing")
        registry = pool.load_registry(project_root=root)
        registry_entry = next(
            item for item in registry["bindings"] if item["market"] == route.market and item["company"] == route.company
        )
        if expected_run_id is not None and route.run_id != expected_run_id:
            raise PoolConcurrencyError("openshell_pool_binding_changed", retryable=True)
        scheduler = _load_scheduler(root)
        changed = _sync_bindings(scheduler, registry)
        changed = _prune_expired(scheduler, now=current_time) or changed
        changed = _promote_waiters(scheduler) or changed
        key = _identity_key(root, allow_create=False)
        identity = _lease_identity(key, tenant_id, user_id, session_id)
        lease = next(
            (item for item in scheduler["leases"] if item["lease_id"] == identity["lease_id"]),
            None,
        )
        if lease is None:
            if changed:
                _write_scheduler(root, scheduler)
            raise PoolConcurrencyError("openshell_pool_lease_not_found")
        try:
            _assert_owner(lease, owner_token_hash, validated_generation)
        except PoolConcurrencyError:
            if changed:
                _write_scheduler(root, scheduler)
            raise
        if lease["state"] == "orphaned":
            if changed:
                _write_scheduler(root, scheduler)
            raise PoolConcurrencyError("openshell_pool_lease_orphaned", retryable=True)
        if lease["scope_id"] != registry_entry["scope_id"] or lease["run_id"] != registry_entry["run_id"]:
            if changed:
                _write_scheduler(root, scheduler)
            raise PoolConcurrencyError("openshell_pool_session_scope_conflict")
        control = _control(scheduler, registry_entry["scope_id"], registry_entry["run_id"])
        if control["traffic_state"] == "failed":
            if changed:
                _write_scheduler(root, scheduler)
            raise PoolConcurrencyError("openshell_pool_binding_failed", retryable=True)
        lease["last_seen_at"] = current_time
        if control["traffic_state"] == "accepting":
            lease["expires_at"] = current_time + control["lease_ttl_seconds"]
        _write_scheduler(root, scheduler)
        if lease["state"] == "waiting":
            return _admission(
                route=route,
                lease=lease,
                status="queued",
                owner_token=owner_token,
                queue_position=_queue_position(scheduler, lease),
            )
        return _admission(
            route=route,
            lease=lease,
            status="active",
            owner_token=owner_token,
        )


def set_traffic_state(
    *,
    market: str,
    company: str,
    run_id: str,
    traffic_state: Literal["accepting", "draining", "failed"],
    drain_timeout_seconds: int = DEFAULT_DRAIN_TIMEOUT_SECONDS,
    require_idle: bool = False,
    now: int | None = None,
    project_root: Path = REPO_ROOT,
) -> dict[str, Any]:
    if traffic_state not in TRAFFIC_STATES:
        raise PoolConcurrencyError("openshell_pool_traffic_state_invalid")
    if (
        not isinstance(drain_timeout_seconds, int)
        or isinstance(drain_timeout_seconds, bool)
        or not 1 <= drain_timeout_seconds <= MAX_LEASE_TTL_SECONDS
    ):
        raise PoolConcurrencyError("openshell_pool_drain_timeout_invalid")
    if not isinstance(require_idle, bool):
        raise PoolConcurrencyError("openshell_pool_require_idle_invalid")
    root = pool._project_root(project_root)
    current_time = int(time.time()) if now is None else now
    with pool._registry_lock(root):
        route = pool.resolve(market=market, company=company, project_root=root)
        if route.target != "openshell" or route.run_id != run_id:
            raise PoolConcurrencyError("openshell_pool_binding_not_registered")
        registry = pool.load_registry(project_root=root)
        entry = next(
            item for item in registry["bindings"] if item["market"] == route.market and item["company"] == route.company
        )
        scheduler = _load_scheduler(root)
        changed = _sync_bindings(scheduler, registry)
        changed = _prune_expired(scheduler, now=current_time) or changed
        control = _control(scheduler, entry["scope_id"], entry["run_id"])
        scoped_before = [
            lease
            for lease in scheduler["leases"]
            if lease["scope_id"] == entry["scope_id"] and lease["run_id"] == entry["run_id"]
        ]
        if require_idle and scoped_before:
            if changed:
                _write_scheduler(root, scheduler)
            raise PoolConcurrencyError(
                "openshell_pool_live_leases_require_terminal",
                retryable=True,
            )
        control["traffic_state"] = traffic_state
        if traffic_state != "accepting":
            retained = []
            drain_deadline = current_time + drain_timeout_seconds
            for lease in scheduler["leases"]:
                scoped = lease["scope_id"] == entry["scope_id"] and lease["run_id"] == entry["run_id"]
                if scoped and lease["state"] == "waiting":
                    continue
                if scoped and traffic_state == "failed" and lease["state"] == "active":
                    if not lease["run_bound"]:
                        continue
                    lease["state"] = "orphaned"
                if scoped and traffic_state == "draining" and lease["state"] == "active":
                    lease["expires_at"] = min(lease["expires_at"], drain_deadline)
                retained.append(lease)
            scheduler["leases"] = retained
        else:
            _promote_waiters(scheduler)
        _write_scheduler(root, scheduler)
        active = sum(
            lease["state"] in {"active", "orphaned"}
            and lease["scope_id"] == entry["scope_id"]
            and lease["run_id"] == entry["run_id"]
            for lease in scheduler["leases"]
        )
        orphaned = sum(
            lease["state"] == "orphaned"
            and lease["scope_id"] == entry["scope_id"]
            and lease["run_id"] == entry["run_id"]
            for lease in scheduler["leases"]
        )
        waiting = sum(
            lease["state"] == "waiting"
            and lease["scope_id"] == entry["scope_id"]
            and lease["run_id"] == entry["run_id"]
            for lease in scheduler["leases"]
        )
        return {
            "market": route.market,
            "company": route.company,
            "run_id": route.run_id,
            "traffic_state": traffic_state,
            "active_leases": active,
            "orphaned_leases": orphaned,
            "waiting_leases": waiting,
            "drained": active == 0,
            "drain_deadline": (
                current_time + drain_timeout_seconds if traffic_state == "draining" and active > 0 else 0
            ),
        }


def status(*, now: int | None = None, project_root: Path = REPO_ROOT) -> dict[str, Any]:
    root = pool._project_root(project_root)
    current_time = int(time.time()) if now is None else now
    with pool._registry_lock(root):
        registry = pool.load_registry(project_root=root)
        scheduler = _load_scheduler(root)
        changed = _sync_bindings(scheduler, registry)
        changed = _prune_expired(scheduler, now=current_time) or changed
        changed = _promote_waiters(scheduler) or changed
        if changed:
            _write_scheduler(root, scheduler)
        bindings = []
        for control in scheduler["bindings"]:
            scoped = [
                lease
                for lease in scheduler["leases"]
                if lease["scope_id"] == control["scope_id"] and lease["run_id"] == control["run_id"]
            ]
            bindings.append(
                {
                    **control,
                    "active_leases": sum(lease["state"] == "active" for lease in scoped),
                    "orphaned_leases": sum(lease["state"] == "orphaned" for lease in scoped),
                    "waiting_leases": sum(lease["state"] == "waiting" for lease in scoped),
                }
            )
        return {
            "schema_version": SCHEMA_VERSION,
            "generation": scheduler["generation"],
            "active_leases": sum(lease["state"] == "active" for lease in scheduler["leases"]),
            "orphaned_leases": sum(lease["state"] == "orphaned" for lease in scheduler["leases"]),
            "waiting_leases": sum(lease["state"] == "waiting" for lease in scheduler["leases"]),
            "bindings": bindings,
        }
