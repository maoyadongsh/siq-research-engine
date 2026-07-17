"""Conservative restart recovery for SIQ OpenShell pool-backed Hermes runs."""

from __future__ import annotations

import asyncio
import fcntl
import logging
import os
import stat
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Awaitable, Callable

import httpx
from database import async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

from services.hermes_client import HermesRunRoute, HermesRunStatus, get_run_status
from services.openshell_pool_adapter import (
    OpenShellPoolAdapter,
    OpenShellPoolAdapterError,
    PoolRecoveryTakeover,
)
from services.path_config import PROJECT_ROOT
from services.runtime_coordination import (
    ActiveRunLeaseSnapshot,
    list_recoverable_active_runs,
    release_active_run,
    renew_active_run,
    runtime_owner_id,
    takeover_active_run,
)

logger = logging.getLogger(__name__)

RECOVERY_PROFILE = "siq_analysis"
RECOVERY_LOCK_RELATIVE = Path("var/openshell/canary/siq-analysis/pool/api-recovery.lock")
_TRUE_VALUES = {"1", "true", "yes", "on"}
_READY_MANAGER: OpenShellPoolRecoveryManager | None = None


def recovery_enabled() -> bool:
    """Recovery is opt-in so local/dev API processes cannot steal production work."""

    enabled = os.getenv("SIQ_OPENSHELL_POOL_RECOVERY_ENABLED", "0").strip().lower() in _TRUE_VALUES
    # start.sh exports this for the formal API. Requiring it explicitly keeps
    # ad-hoc/dev uvicorn processes (including :18082) out of recovery authority.
    return enabled and os.getenv("SIQ_BACKEND_PORT", "").strip() == "18081"


def recovery_required() -> bool:
    return os.getenv("SIQ_OPENSHELL_POOL_RECOVERY_REQUIRED", "0").strip().lower() in _TRUE_VALUES


def recovery_ready() -> bool:
    manager = _READY_MANAGER
    return manager is not None and manager.ready


def readiness_snapshot() -> dict[str, bool]:
    """Return the public, credential-free recovery gate state."""

    return {
        "enabled": recovery_enabled(),
        "required": recovery_required(),
        "ready": recovery_ready(),
    }


def _poll_seconds() -> float:
    try:
        return max(
            0.1,
            min(float(os.getenv("SIQ_OPENSHELL_POOL_RECOVERY_POLL_SECONDS", "2")), 60.0),
        )
    except ValueError:
        return 2.0


def _rescan_seconds() -> float:
    try:
        return max(
            0.1,
            min(float(os.getenv("SIQ_OPENSHELL_POOL_RECOVERY_RESCAN_SECONDS", "5")), 60.0),
        )
    except ValueError:
        return 5.0


def _rescan_limit() -> int:
    try:
        return max(
            1,
            min(int(os.getenv("SIQ_OPENSHELL_POOL_RECOVERY_RESCAN_LIMIT", "100")), 1000),
        )
    except ValueError:
        return 100


@dataclass(frozen=True)
class OpenShellPoolRecoveryReport:
    enabled: bool
    lock_acquired: bool
    candidates: int = 0
    monitoring: int = 0
    terminal_released: int = 0
    orphaned: int = 0
    skipped: int = 0


@dataclass(frozen=True, repr=False)
class _RecoveredRun:
    lease: ActiveRunLeaseSnapshot
    takeover: PoolRecoveryTakeover
    route: HermesRunRoute
    observed_nonterminal: bool = False

    def __repr__(self) -> str:
        return (
            "_RecoveredRun("
            f"profile={self.lease.profile!r}, session_id={self.lease.session_id!r}, "
            f"run_id={self.lease.run_id!r}, route={self.route!r})"
        )


StatusGetter = Callable[..., Awaitable[HermesRunStatus]]
RecoveryKey = tuple[str, str, str]


class OpenShellPoolRecoveryManager:
    """Own restart recovery for one API process and one exact pool registry."""

    def __init__(
        self,
        *,
        enabled: bool | None = None,
        adapter: OpenShellPoolAdapter | None = None,
        engine=async_engine,
        owner_id: str | None = None,
        lock_path: Path | None = None,
        status_getter: StatusGetter = get_run_status,
        poll_seconds: float | None = None,
        rescan_seconds: float | None = None,
        rescan_limit: int | None = None,
    ) -> None:
        self.enabled = recovery_enabled() if enabled is None else enabled
        self.adapter = adapter
        self.engine = engine
        self.owner_id = owner_id or runtime_owner_id()
        lock_root = adapter.project_root if adapter is not None else PROJECT_ROOT
        self.lock_path = lock_path or (lock_root / RECOVERY_LOCK_RELATIVE)
        self.status_getter = status_getter
        self.poll_seconds = _poll_seconds() if poll_seconds is None else max(0.01, poll_seconds)
        self.rescan_seconds = (
            _rescan_seconds() if rescan_seconds is None else max(0.01, rescan_seconds)
        )
        self.rescan_limit = (
            _rescan_limit()
            if rescan_limit is None
            else max(1, min(int(rescan_limit), 1000))
        )
        self._lock_fd: int | None = None
        self._tasks: dict[RecoveryKey, asyncio.Task[None]] = {}
        self._recoveries: dict[RecoveryKey, _RecoveredRun] = {}
        self._authority_task: asyncio.Task[None] | None = None
        self._rescan_stop = asyncio.Event()
        self._rescan_offset = 0
        self._failed = False
        self._started = False
        self._stopping = False

    @property
    def lock_acquired(self) -> bool:
        return self._lock_fd is not None

    @property
    def ready(self) -> bool:
        authority = self._authority_task
        return (
            self._started
            and not self._failed
            and self.lock_acquired
            and authority is not None
            and not authority.done()
        )

    @staticmethod
    def _key(lease: ActiveRunLeaseSnapshot) -> RecoveryKey:
        return (lease.profile, lease.session_id, lease.run_id)

    def _fail_closed(self, *, task_kind: str) -> None:
        global _READY_MANAGER
        if self._stopping:
            return
        if self._failed:
            return
        self._failed = True
        if _READY_MANAGER is self:
            _READY_MANAGER = None
        logger.error("openshell_pool_recovery_authority_failed task=%s", task_kind)

    def _monitor_done(self, task: asyncio.Task[None]) -> None:
        if self._stopping:
            return
        if task.cancelled():
            self._fail_closed(task_kind="monitor_cancelled")
            return
        if task.exception() is not None:
            self._fail_closed(task_kind="monitor_exception")

    def _authority_done(self, task: asyncio.Task[None]) -> None:
        if self._stopping:
            return
        if not task.cancelled():
            task.exception()
        self._fail_closed(task_kind="rescan_authority")

    def _acquire_process_lock(self) -> bool:
        try:
            self.lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            descriptor = os.open(
                self.lock_path,
                os.O_RDWR
                | os.O_CREAT
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) & 0o077
            ):
                os.close(descriptor)
                raise RuntimeError("openshell_pool_recovery_lock_invalid")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(descriptor)
                return False
            self._lock_fd = descriptor
            return True
        except OSError as exc:
            raise RuntimeError("openshell_pool_recovery_lock_unavailable") from exc

    def _release_process_lock(self) -> None:
        descriptor = self._lock_fd
        self._lock_fd = None
        if descriptor is None:
            return
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    async def _list_candidates(self) -> list[ActiveRunLeaseSnapshot]:
        async with AsyncSession(self.engine) as session:
            return await list_recoverable_active_runs(session, profile=RECOVERY_PROFILE)

    async def _takeover_db(
        self,
        lease: ActiveRunLeaseSnapshot,
        takeover: PoolRecoveryTakeover,
    ) -> bool:
        admission = takeover.admission
        async with AsyncSession(self.engine) as session:
            return await takeover_active_run(
                session,
                profile=lease.profile,
                session_id=lease.session_id,
                run_id=lease.run_id,
                expected_owner_id=lease.owner_id,
                expected_pool_lease_id=lease.pool_lease_id or "",
                expected_pool_scope_id=lease.pool_scope_id or "",
                expected_pool_binding_run_id=lease.pool_binding_run_id or "",
                expected_pool_owner_generation=lease.pool_owner_generation or 0,
                expected_pool_tenant_id=lease.pool_tenant_id,
                expected_pool_user_id=lease.pool_user_id,
                owner_id=self.owner_id,
                pool_owner_generation=admission.owner_generation,
            )

    async def _renew_db(self, recovered: _RecoveredRun) -> bool:
        async with AsyncSession(self.engine) as session:
            return await renew_active_run(
                session,
                profile=recovered.lease.profile,
                session_id=recovered.lease.session_id,
                run_id=recovered.lease.run_id,
                owner_id=self.owner_id,
            )

    async def _release_db(self, recovered: _RecoveredRun, *, status: str) -> bool:
        async with AsyncSession(self.engine) as session:
            return await release_active_run(
                session,
                profile=recovered.lease.profile,
                session_id=recovered.lease.session_id,
                run_id=recovered.lease.run_id,
                owner_id=self.owner_id,
                status=status,
            )

    @staticmethod
    def _route(
        takeover: PoolRecoveryTakeover,
        lease: ActiveRunLeaseSnapshot,
    ) -> HermesRunRoute:
        binding = takeover.binding
        admission = takeover.admission
        return HermesRunRoute(
            target="openshell",
            base=binding.base,
            model=RECOVERY_PROFILE,
            authorization=f"Bearer {binding.api_key}",
            session_namespace=binding.session_namespace,
            canary_run_id=binding.run_id,
            pool_binding=binding,
            pool_lease_id=admission.lease_id,
            pool_owner_token=admission.owner_token,
            pool_owner_generation=admission.owner_generation,
            pool_tenant_id=lease.pool_tenant_id,
            pool_user_id=lease.pool_user_id,
            pool_market=binding.market,
            pool_company=binding.company,
            pool_write_relative_path=admission.write_relative_path,
        )

    async def _status(self, recovered: _RecoveredRun) -> HermesRunStatus:
        return await self.status_getter(
            recovered.lease.run_id,
            profile=recovered.lease.profile,
            route=recovered.route,
        )

    async def _heartbeat_pool(self, recovered: _RecoveredRun) -> bool:
        admission = recovered.takeover.admission
        try:
            heartbeat = await self.adapter.heartbeat_async(
                recovered.takeover.binding,
                session_id=recovered.lease.session_id,
                tenant_id=recovered.route.pool_tenant_id,
                user_id=recovered.route.pool_user_id,
                owner_token=admission.owner_token,
                owner_generation=admission.owner_generation,
            )
        except OpenShellPoolAdapterError:
            return False
        return (
            heartbeat.status == "active"
            and heartbeat.lease_id == admission.lease_id
            and heartbeat.run_id == recovered.takeover.binding.run_id
            and heartbeat.owner_generation == admission.owner_generation
        )

    async def _orphan(self, recovered: _RecoveredRun, *, release_db: bool = False) -> None:
        admission = recovered.takeover.admission
        try:
            await self.adapter.abandon_async(
                session_id=recovered.lease.session_id,
                tenant_id=recovered.route.pool_tenant_id,
                user_id=recovered.route.pool_user_id,
                owner_token=admission.owner_token,
                owner_generation=admission.owner_generation,
            )
        except OpenShellPoolAdapterError:
            pass
        if release_db:
            await self._release_db(recovered, status="recovery_unknown")

    async def _release_terminal(
        self,
        recovered: _RecoveredRun,
        status: HermesRunStatus,
    ) -> bool:
        if not status.write_quiesced:
            return False
        admission = recovered.takeover.admission
        try:
            released = await self.adapter.release_async(
                session_id=recovered.lease.session_id,
                tenant_id=recovered.route.pool_tenant_id,
                user_id=recovered.route.pool_user_id,
                owner_token=admission.owner_token,
                owner_generation=admission.owner_generation,
                terminal_confirmed=True,
            )
        except OpenShellPoolAdapterError:
            return False
        if not released:
            return False
        await self._release_db(recovered, status=status.status)
        return True

    @staticmethod
    def _has_complete_execution_receipt(
        recovered: _RecoveredRun,
        _status: HermesRunStatus,
    ) -> bool:
        # ActiveRunLease currently persists only the main run_id. A financial
        # repair child can still be writing after that main run is terminal.
        # A main run that is already terminal on the first post-takeover GET
        # might have a financial repair child still writing. If recovery first
        # observed the main run nonterminal, the postprocessing owner fence
        # guarantees the old API cannot start such a child after takeover.
        return recovered.observed_nonterminal

    @staticmethod
    def _is_identity_uncertain(exc: BaseException) -> bool:
        return (
            isinstance(exc, RuntimeError)
            or (
                isinstance(exc, httpx.HTTPStatusError)
                and exc.response.status_code in {400, 401, 403, 404, 409, 410, 422}
            )
        )

    async def _monitor(self, recovered: _RecoveredRun) -> None:
        key = self._key(recovered.lease)
        try:
            while True:
                if not await self._renew_db(recovered):
                    await self._orphan(recovered, release_db=False)
                    return
                if not await self._heartbeat_pool(recovered):
                    await self._orphan(recovered)
                    return
                try:
                    status = await self._status(recovered)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    if self._is_identity_uncertain(exc):
                        await self._orphan(recovered)
                        return
                    await asyncio.sleep(self.poll_seconds)
                    continue
                if not status.terminal and not recovered.observed_nonterminal:
                    recovered = replace(recovered, observed_nonterminal=True)
                    self._recoveries[key] = recovered
                if status.write_quiesced and self._has_complete_execution_receipt(
                    recovered,
                    status,
                ):
                    if await self._release_terminal(recovered, status):
                        return
                elif status.write_quiesced:
                    await self._orphan(recovered)
                    return
                await asyncio.sleep(self.poll_seconds)
        except asyncio.CancelledError:
            if not self._stopping:
                self._fail_closed(task_kind="monitor_cancelled")
                try:
                    await self._orphan(recovered, release_db=False)
                except BaseException:
                    logger.exception("openshell_pool_recovery_cancel_cleanup_failed")
            raise
        except Exception:
            self._fail_closed(task_kind="monitor_exception")
            try:
                await self._orphan(recovered, release_db=False)
            except BaseException:
                logger.exception("openshell_pool_recovery_monitor_cleanup_failed")
            return
        finally:
            self._recoveries.pop(key, None)
            self._tasks.pop(key, None)

    def _start_monitor(self, recovered: _RecoveredRun) -> None:
        key = self._key(recovered.lease)
        if key in self._tasks:
            raise RuntimeError("openshell_pool_recovery_monitor_duplicate")
        self._recoveries[key] = recovered
        task = asyncio.create_task(
            self._monitor(recovered),
            name=f"openshell-pool-recovery-monitor:{recovered.lease.run_id}",
        )
        self._tasks[key] = task
        task.add_done_callback(self._monitor_done)

    async def _rescan_once(self) -> tuple[int, dict[str, int]]:
        candidates = await self._list_candidates()
        eligible = [
            lease
            for lease in candidates
            if lease.owner_id != self.owner_id
            and self._key(lease) not in self._tasks
            and self._key(lease) not in self._recoveries
        ]
        selected: list[ActiveRunLeaseSnapshot] = []
        if eligible:
            start = self._rescan_offset % len(eligible)
            ordered = eligible[start:] + eligible[:start]
            selected = ordered[: self.rescan_limit]
            self._rescan_offset = (start + len(selected)) % len(eligible)
        counts = {
            "monitoring": 0,
            "terminal_released": 0,
            "orphaned": 0,
            "skipped": 0,
        }
        for lease in selected:
            outcome = await self._recover_one(lease)
            counts[outcome] += 1
        return len(candidates), counts

    async def _authority_loop(self) -> None:
        while not self._rescan_stop.is_set():
            try:
                await asyncio.wait_for(
                    self._rescan_stop.wait(),
                    timeout=self.rescan_seconds,
                )
            except TimeoutError:
                candidates, counts = await self._rescan_once()
                if any(counts.values()):
                    logger.info(
                        "openshell_pool_recovery_rescan candidates=%d monitoring=%d terminal_released=%d orphaned=%d skipped=%d",
                        candidates,
                        counts["monitoring"],
                        counts["terminal_released"],
                        counts["orphaned"],
                        counts["skipped"],
                    )

    async def _recover_one(self, lease: ActiveRunLeaseSnapshot) -> str:
        if not all(
            (
                lease.pool_lease_id,
                lease.pool_scope_id,
                lease.pool_binding_run_id,
                lease.pool_owner_generation,
            )
        ):
            return "skipped"
        if (lease.pool_tenant_id is None) != (lease.pool_user_id is None):
            return "skipped"
        principal = {}
        if lease.pool_tenant_id is not None and lease.pool_user_id is not None:
            principal = {
                "tenant_id": lease.pool_tenant_id,
                "user_id": lease.pool_user_id,
            }
        try:
            if self._lock_fd is None:
                raise RuntimeError("openshell_pool_recovery_capability_missing")
            takeover = await self.adapter.takeover_recovery_async(
                session_id=lease.session_id,
                expected_lease_id=lease.pool_lease_id,
                expected_scope_id=lease.pool_scope_id,
                expected_run_id=lease.pool_binding_run_id,
                expected_owner_generation=lease.pool_owner_generation,
                recovery_lock_fd=self._lock_fd,
                **principal,
            )
        except OpenShellPoolAdapterError as exc:
            if exc.code == "openshell_pool_recovery_lease_not_found":
                return "skipped"
            return "skipped"

        admission = takeover.admission
        provisional = _RecoveredRun(
            lease=lease,
            takeover=takeover,
            route=self._route(takeover, lease),
        )
        try:
            db_taken_over = await self._takeover_db(lease, takeover)
        except BaseException:
            await self._orphan(provisional, release_db=False)
            raise
        if not db_taken_over:
            await self._orphan(provisional, release_db=False)
            return "orphaned"

        recovered = provisional
        if admission.status == "queued":
            await self._orphan(recovered, release_db=True)
            return "orphaned"
        if lease.run_id.startswith("claim-"):
            await self._orphan(recovered, release_db=not admission.run_bound)
            return "orphaned"
        try:
            status = await self._status(recovered)
        except Exception as exc:
            if self._is_identity_uncertain(exc):
                await self._orphan(recovered)
                return "orphaned"
            self._start_monitor(recovered)
            return "monitoring"
        if status.write_quiesced and self._has_complete_execution_receipt(recovered, status):
            if await self._release_terminal(recovered, status):
                return "terminal_released"
            await self._orphan(recovered)
            return "orphaned"
        if status.write_quiesced:
            await self._orphan(recovered)
            return "orphaned"
        if not status.terminal:
            recovered = replace(recovered, observed_nonterminal=True)
        self._start_monitor(recovered)
        return "monitoring"

    async def start(self) -> OpenShellPoolRecoveryReport:
        global _READY_MANAGER
        if not self.enabled:
            return OpenShellPoolRecoveryReport(enabled=False, lock_acquired=False)
        if self._started or self._lock_fd is not None:
            raise RuntimeError("openshell_pool_recovery_already_started")
        self._failed = False
        self._stopping = False
        self._rescan_stop = asyncio.Event()
        if not self._acquire_process_lock():
            return OpenShellPoolRecoveryReport(enabled=True, lock_acquired=False)
        try:
            if self.adapter is None:
                self.adapter = OpenShellPoolAdapter(project_root=PROJECT_ROOT)
            candidate_count, counts = await self._rescan_once()
        except BaseException:
            await self.stop()
            raise
        report = OpenShellPoolRecoveryReport(
            enabled=True,
            lock_acquired=True,
            candidates=candidate_count,
            **counts,
        )
        self._authority_task = asyncio.create_task(
            self._authority_loop(),
            name="openshell-pool-recovery-authority",
        )
        self._authority_task.add_done_callback(self._authority_done)
        self._started = True
        _READY_MANAGER = self
        logger.info(
            "openshell_pool_recovery_started candidates=%d monitoring=%d terminal_released=%d orphaned=%d skipped=%d",
            report.candidates,
            report.monitoring,
            report.terminal_released,
            report.orphaned,
            report.skipped,
        )
        return report

    async def stop(self) -> None:
        global _READY_MANAGER
        self._stopping = True
        self._started = False
        if _READY_MANAGER is self:
            _READY_MANAGER = None
        self._rescan_stop.set()
        authority = self._authority_task
        if authority is not None:
            await asyncio.gather(authority, return_exceptions=True)
        self._authority_task = None
        recoveries = list(self._recoveries.values())
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._recoveries.clear()
        self._tasks.clear()
        for recovered in recoveries:
            await self._orphan(recovered, release_db=False)
        self._release_process_lock()


__all__ = [
    "OpenShellPoolRecoveryManager",
    "OpenShellPoolRecoveryReport",
    "recovery_enabled",
    "recovery_ready",
    "recovery_required",
    "readiness_snapshot",
]
