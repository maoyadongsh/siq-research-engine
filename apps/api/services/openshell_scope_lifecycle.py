"""On-demand OpenShell pool lifecycle for company-scoped analysis chats."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import secrets
import sys
import threading
import time
from pathlib import Path
from typing import Any

from services.openshell_pool_adapter import OpenShellPoolAdapter, ResolvedPoolBinding

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.openshell import (  # noqa: E402
    siq_analysis_pool_concurrency as pool_concurrency,
    siq_analysis_pool_registry as pool_registry,
)
from scripts.openshell.siq_analysis_lifecycle import LifecycleError, SystemBackend  # noqa: E402
from scripts.openshell.siq_analysis_pool_lifecycle import (  # noqa: E402
    PoolLifecycleError,
    PoolLifecycleManager,
    WidePilotError,
)

logger = logging.getLogger(__name__)

_RETRYABLE_PORT_ERRORS = {
    "openshell_pool_local_port_conflict",
    "openshell_pool_no_port_available",
    "openshell_pool_port_reservation_required",
    "openshell_pool_port_reservation_unavailable",
}
_LIFECYCLE_OPERATION_LOCK = threading.Lock()


class OpenShellScopeLifecycleError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def auto_provision_enabled() -> bool:
    return os.getenv("SIQ_OPENSHELL_SCOPE_AUTO_PROVISION", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _idle_ttl_seconds() -> int:
    try:
        return max(60, min(int(os.getenv("SIQ_OPENSHELL_SCOPE_IDLE_TTL_SECONDS", "300")), 86_400))
    except ValueError:
        return 300


def _sweep_seconds() -> int:
    try:
        return max(10, min(int(os.getenv("SIQ_OPENSHELL_SCOPE_SWEEP_SECONDS", "30")), 3600))
    except ValueError:
        return 30


def _probe_ttl_seconds() -> int:
    return 30


class OpenShellScopeLifecycleManager:
    def __init__(self, *, project_root: Path = PROJECT_ROOT) -> None:
        self.project_root = project_root.resolve(strict=True)
        self.adapter = OpenShellPoolAdapter(project_root=self.project_root)
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._last_used: dict[tuple[str, str], float] = {}
        self._last_verified: dict[tuple[str, str, str], float] = {}
        self._sweeper: asyncio.Task[None] | None = None

    def _lock(self, market: str, company: str) -> asyncio.Lock:
        return self._locks.setdefault((market, company), asyncio.Lock())

    def _touch(self, binding: ResolvedPoolBinding) -> None:
        if binding.market and binding.company:
            self._last_used[(binding.market, binding.company)] = time.monotonic()
        self._ensure_sweeper()

    def _ensure_sweeper(self) -> None:
        if self._sweeper is None or self._sweeper.done():
            self._sweeper = asyncio.create_task(
                self._sweeper_loop(),
                name="openshell-scope-idle-sweeper",
            )

    @contextlib.contextmanager
    def _maintenance_lock(self):
        with _LIFECYCLE_OPERATION_LOCK:
            previous = os.environ.get("SIQ_OPENSHELL_MAINTENANCE_FD")
            backend = SystemBackend(project_root=self.project_root)
            try:
                backend.acquire_maintenance_lock()
                raw_descriptor = os.environ.get("SIQ_OPENSHELL_MAINTENANCE_FD", "")
                if not raw_descriptor.isdigit():
                    raise OpenShellScopeLifecycleError("openshell_scope_maintenance_lock_invalid")
                yield
            except LifecycleError as exc:
                raise OpenShellScopeLifecycleError(exc.code) from exc
            finally:
                if previous is None:
                    current = os.environ.pop("SIQ_OPENSHELL_MAINTENANCE_FD", "")
                    if current.isdigit():
                        try:
                            os.close(int(current))
                        except OSError:
                            pass
                else:
                    os.environ["SIQ_OPENSHELL_MAINTENANCE_FD"] = previous

    def _start_binding(self, *, market: str, company: str) -> None:
        with self._maintenance_lock():
            manager = PoolLifecycleManager(project_root=self.project_root)
            for local_port in range(pool_registry.FIRST_POOL_PORT, pool_registry.LAST_POOL_PORT + 1):
                run_id = f"canary-{secrets.token_hex(6)}"
                try:
                    started = manager.start(
                        market=market,
                        company=company,
                        run_id=run_id,
                        local_port=local_port,
                    )
                    if started.get("ok") is not True:
                        raise OpenShellScopeLifecycleError("openshell_scope_start_failed")
                    probed = manager.probe(market=market, company=company, run_id=run_id)
                    if probed.get("ok") is not True:
                        manager.stop(
                            market=market,
                            company=company,
                            run_id=run_id,
                            rollback=True,
                        )
                        raise OpenShellScopeLifecycleError("openshell_scope_probe_failed")
                    return
                except (PoolLifecycleError, WidePilotError) as exc:
                    if exc.code == "openshell_pool_scope_already_registered":
                        return
                    if exc.code in _RETRYABLE_PORT_ERRORS:
                        continue
                    raise OpenShellScopeLifecycleError(exc.code) from exc
        raise OpenShellScopeLifecycleError("openshell_scope_pool_capacity_exhausted")

    def _probe_binding(self, binding: ResolvedPoolBinding) -> bool:
        if not binding.market or not binding.company or not binding.run_id:
            return False
        try:
            result = PoolLifecycleManager(project_root=self.project_root).probe(
                market=binding.market,
                company=binding.company,
                run_id=binding.run_id,
            )
        except (PoolLifecycleError, WidePilotError):
            return False
        return result.get("ok") is True

    def _recently_verified(self, binding: ResolvedPoolBinding) -> bool:
        key = (binding.market, binding.company, binding.run_id)
        verified_at = self._last_verified.get(key)
        return verified_at is not None and time.monotonic() - verified_at < _probe_ttl_seconds()

    def _mark_verified(self, binding: ResolvedPoolBinding) -> None:
        self._last_verified[(binding.market, binding.company, binding.run_id)] = time.monotonic()

    def _replace_binding(self, binding: ResolvedPoolBinding) -> None:
        with self._maintenance_lock():
            try:
                PoolLifecycleManager(project_root=self.project_root).stop(
                    market=binding.market,
                    company=binding.company,
                    run_id=binding.run_id,
                )
            except (PoolLifecycleError, WidePilotError) as exc:
                raise OpenShellScopeLifecycleError(exc.code) from exc
        self._last_verified.pop((binding.market, binding.company, binding.run_id), None)
        self._start_binding(market=binding.market, company=binding.company)

    async def ensure_binding(self, context: Any) -> ResolvedPoolBinding:
        binding = self.adapter.resolve_binding(context)
        if not binding.market or not binding.company:
            return binding
        lock = self._lock(binding.market, binding.company)
        async with lock:
            current = self.adapter.resolve_binding(context)
            if current.target == "openshell":
                if self._recently_verified(current):
                    self._touch(current)
                    return current
                healthy = await asyncio.to_thread(self._probe_binding, current)
                if healthy:
                    self._mark_verified(current)
                    self._touch(current)
                    return current
                await asyncio.to_thread(self._replace_binding, current)
                current = self.adapter.resolve_binding(context)
            else:
                await asyncio.to_thread(
                    self._start_binding,
                    market=binding.market,
                    company=binding.company,
                )
                current = self.adapter.resolve_binding(context)
            if current.target != "openshell":
                raise OpenShellScopeLifecycleError("openshell_scope_binding_not_ready")
            self._mark_verified(current)
            self._touch(current)
            return current

    def _idle_candidates(self) -> list[tuple[str, str, str]]:
        registry = pool_registry.load_registry(project_root=self.project_root)
        scheduler = pool_concurrency.status(project_root=self.project_root)
        controls = {
            (item["scope_id"], item["run_id"]): item
            for item in scheduler.get("bindings", [])
        }
        now = time.monotonic()
        candidates: list[tuple[str, str, str]] = []
        for binding in registry.get("bindings", []):
            key = (binding["market"], binding["company"])
            last_used = self._last_used.setdefault(key, now)
            if now - last_used < _idle_ttl_seconds():
                continue
            control = controls.get((binding["scope_id"], binding["run_id"]))
            if control is None or any(
                control.get(field, 0) != 0
                for field in ("active_leases", "orphaned_leases", "waiting_leases")
            ):
                continue
            candidates.append((binding["market"], binding["company"], binding["run_id"]))
        return candidates

    def _stop_idle_binding(self, *, market: str, company: str, run_id: str) -> None:
        with self._maintenance_lock():
            manager = PoolLifecycleManager(project_root=self.project_root)
            manager.stop(market=market, company=company, run_id=run_id)

    async def _sweep_once(self) -> None:
        candidates = await asyncio.to_thread(self._idle_candidates)
        for market, company, run_id in candidates:
            async with self._lock(market, company):
                current = self.adapter.resolve_binding(
                    {"company": {"market": market, "dir": company}}
                )
                if current.target != "openshell" or current.run_id != run_id:
                    continue
                try:
                    await asyncio.to_thread(
                        self._stop_idle_binding,
                        market=market,
                        company=company,
                        run_id=run_id,
                    )
                except Exception:
                    logger.exception(
                        "OpenShell idle scope cleanup failed market=%s company=%s run_id=%s",
                        market,
                        company,
                        run_id,
                    )
                    continue
                self._last_used.pop((market, company), None)
                self._last_verified.pop((market, company, run_id), None)

    async def _sweeper_loop(self) -> None:
        while True:
            await asyncio.sleep(_sweep_seconds())
            try:
                await self._sweep_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("OpenShell scope idle sweep failed")


_MANAGER: OpenShellScopeLifecycleManager | None = None


def default_manager() -> OpenShellScopeLifecycleManager:
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = OpenShellScopeLifecycleManager()
    return _MANAGER


async def ensure_binding(context: Any) -> ResolvedPoolBinding | None:
    if not auto_provision_enabled():
        return None
    return await default_manager().ensure_binding(context)


def implicit_host_fallback_allowed(context: Any) -> bool:
    """Allow fallback only after the scoped OpenShell binding is absent."""

    try:
        return default_manager().adapter.resolve_binding(context).target == "host"
    except Exception:
        return False


__all__ = [
    "OpenShellScopeLifecycleError",
    "OpenShellScopeLifecycleManager",
    "auto_provision_enabled",
    "default_manager",
    "ensure_binding",
    "implicit_host_fallback_allowed",
]
