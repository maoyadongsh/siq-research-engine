#!/usr/bin/python3 -IB
"""Operate one company-scoped SIQ OpenShell pool slot without exposing lease tokens."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Callable, Mapping

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.openshell import (  # noqa: E402
    siq_analysis_pool_concurrency as concurrency,
    siq_analysis_pool_registry as pool,
)
from scripts.openshell.siq_analysis_canary import (  # noqa: E402
    ACKNOWLEDGEMENT,
    CanaryLifecycle,
)
from scripts.openshell.siq_analysis_wide_pilot import WidePilotError  # noqa: E402

SCHEMA_VERSION = "siq.openshell.siq_analysis_pool_lifecycle.v1"
DEFAULT_LOCAL_PORT = 28652
LifecycleFactory = Callable[..., CanaryLifecycle]


class PoolLifecycleError(RuntimeError):
    """A stable, secret-free pool lifecycle failure."""

    def __init__(self, code: str) -> None:
        if re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,95}", code) is None:
            code = "openshell_pool_lifecycle_invalid_error"
        self.code = code
        super().__init__(code)


class PoolLifecycleManager:
    def __init__(
        self,
        *,
        project_root: Path = REPO_ROOT,
        lifecycle_factory: LifecycleFactory = CanaryLifecycle,
    ) -> None:
        try:
            self.project_root = pool._project_root(project_root)
        except pool.PoolRegistryError as exc:
            raise PoolLifecycleError(exc.code) from exc
        self.lifecycle_factory = lifecycle_factory

    @staticmethod
    def _binding_for_scope(
        registry: Mapping[str, Any],
        *,
        scope_id: str,
        expected_run_id: str | None,
    ) -> dict[str, Any]:
        entries = registry.get("bindings")
        if not isinstance(entries, list):
            raise PoolLifecycleError("openshell_pool_registry_invalid")
        binding = next(
            (item for item in entries if isinstance(item, dict) and item.get("scope_id") == scope_id),
            None,
        )
        if binding is None:
            raise PoolLifecycleError("openshell_pool_binding_not_registered")
        if expected_run_id is not None and binding.get("run_id") != expected_run_id:
            raise PoolLifecycleError("openshell_pool_binding_run_mismatch")
        return dict(binding)

    def _registered_binding(
        self,
        *,
        market: str,
        company: str,
        expected_run_id: str | None = None,
    ) -> tuple[dict[str, Any], Any]:
        plan = pool.plan_slot(
            market=market,
            company=company,
            run_id=expected_run_id or "canary-000000000000",
            local_port=DEFAULT_LOCAL_PORT,
            project_root=self.project_root,
        )
        try:
            route = pool.resolve(market=market, company=company, project_root=self.project_root)
            registry = pool.load_registry(project_root=self.project_root)
        except pool.PoolRegistryError as exc:
            raise PoolLifecycleError(exc.code) from exc
        if route.target != "openshell":
            raise PoolLifecycleError("openshell_pool_binding_not_registered")
        binding = self._binding_for_scope(
            registry,
            scope_id=plan.scope_id,
            expected_run_id=expected_run_id,
        )
        if (
            binding.get("run_id") != route.run_id
            or binding.get("target_port") != pool.TARGET_PORT
            or not isinstance(binding.get("local_port"), int)
            or not pool.FIRST_POOL_PORT <= binding["local_port"] <= pool.LAST_POOL_PORT
        ):
            raise PoolLifecycleError("openshell_pool_binding_endpoint_invalid")
        return binding, route

    def _legacy_binding(
        self,
        *,
        market: str,
        company: str,
        expected_run_id: str,
    ) -> tuple[dict[str, Any], Any, dict[str, Any]]:
        try:
            plan = pool.plan_slot(
                market=market,
                company=company,
                run_id=expected_run_id,
                local_port=pool.TARGET_PORT,
                project_root=self.project_root,
            )
            route = pool.resolve(market=market, company=company, project_root=self.project_root)
            registry = pool.load_registry(project_root=self.project_root)
        except pool.PoolRegistryError as exc:
            raise PoolLifecycleError(exc.code) from exc
        if route.target != "openshell":
            raise PoolLifecycleError("openshell_pool_legacy_binding_not_registered")
        binding = self._binding_for_scope(
            registry,
            scope_id=plan.scope_id,
            expected_run_id=expected_run_id,
        )
        expected_base = f"http://{pool.FORWARD_HOST}:{pool.TARGET_PORT}/v1/runs"
        if (
            binding.get("active") != pool.LEGACY_ACTIVE_RELATIVE.as_posix()
            or binding.get("local_port") != pool.TARGET_PORT
            or binding.get("target_port") != pool.TARGET_PORT
            or binding.get("run_id") != route.run_id
            or route.base != expected_base
        ):
            raise PoolLifecycleError("openshell_pool_legacy_binding_invalid")
        return binding, route, registry

    @staticmethod
    def _other_bindings(registry: Mapping[str, Any], *, scope_id: str) -> list[dict[str, Any]]:
        bindings = registry.get("bindings")
        if not isinstance(bindings, list) or not all(isinstance(item, dict) for item in bindings):
            raise PoolLifecycleError("openshell_pool_registry_invalid")
        return [dict(item) for item in bindings if item.get("scope_id") != scope_id]

    @staticmethod
    def _require_maintenance_lock(lifecycle: Any) -> None:
        adapter = getattr(lifecycle, "adapter", None)
        backend = getattr(adapter, "backend", None)
        checker = getattr(backend, "maintenance_lock_held", None)
        try:
            held = checker() if callable(checker) else False
        except Exception as exc:
            raise PoolLifecycleError("openshell_pool_legacy_migration_maintenance_lock_required") from exc
        if held is not True:
            raise PoolLifecycleError("openshell_pool_legacy_migration_maintenance_lock_required")

    def _restore_accepting(self, *, market: str, company: str, run_id: str) -> None:
        try:
            restored = concurrency.set_traffic_state(
                market=market,
                company=company,
                run_id=run_id,
                traffic_state="accepting",
                project_root=self.project_root,
            )
        except Exception as exc:
            raise PoolLifecycleError("openshell_pool_legacy_migration_restore_accepting_failed") from exc
        if restored.get("traffic_state") != "accepting" or restored.get("run_id") != run_id:
            raise PoolLifecycleError("openshell_pool_legacy_migration_restore_accepting_failed")

    def _ensure_host_after_failed_new_start(
        self,
        *,
        market: str,
        company: str,
        scope_id: str,
        run_id: str,
    ) -> None:
        try:
            registry = pool.load_registry(project_root=self.project_root)
            bindings = registry.get("bindings")
            if not isinstance(bindings, list):
                raise PoolLifecycleError("openshell_pool_registry_invalid")
            scoped = [item for item in bindings if isinstance(item, dict) and item.get("scope_id") == scope_id]
            if scoped:
                if len(scoped) != 1 or scoped[0].get("run_id") != run_id:
                    raise PoolLifecycleError("openshell_pool_legacy_migration_new_binding_conflict")
                pool.unregister(
                    market=market,
                    company=company,
                    run_id=run_id,
                    project_root=self.project_root,
                )
            route = pool.resolve(market=market, company=company, project_root=self.project_root)
        except PoolLifecycleError:
            raise
        except Exception as exc:
            raise PoolLifecycleError("openshell_pool_legacy_migration_new_start_host_restore_failed") from exc
        if route.target != "host":
            raise PoolLifecycleError("openshell_pool_legacy_migration_new_start_host_restore_failed")

    def _legacy_stop_retry_allowed(self, lifecycle: Any, *, plan: Any) -> bool:
        loader = getattr(lifecycle, "_load_active_spec", None)
        active_path = getattr(lifecycle, "active_path", None)
        if not callable(loader) or active_path != self.project_root / pool.LEGACY_ACTIVE_RELATIVE:
            return False
        try:
            spec, manifest = loader(plan.run_id)
        except Exception:
            return False
        return (
            getattr(spec, "run_id", None) == plan.run_id
            and getattr(spec, "market", None) == plan.market
            and getattr(spec, "company", None) == plan.company
            and getattr(spec, "analysis_relative_path", None) == plan.analysis_relative_path
            and isinstance(manifest, Mapping)
            and manifest.get("phase") == "stopping"
            and manifest.get("run_id") == plan.run_id
            and manifest.get("market") == plan.market
            and manifest.get("company") == plan.company
            and manifest.get("analysis_relative_path") == plan.analysis_relative_path
        )

    def _stop_legacy_with_one_retry(self, lifecycle: Any, *, plan: Any) -> Mapping[str, Any]:
        first_error: Exception | None = None
        try:
            stopped = lifecycle.stop(pilot_id=plan.run_id)
        except Exception as exc:
            first_error = exc
            stopped = None
        if (
            isinstance(stopped, Mapping)
            and stopped.get("ok") is True
            and stopped.get("status") == "stopped"
            and stopped.get("run_id") == plan.run_id
        ):
            return stopped
        if not self._legacy_stop_retry_allowed(lifecycle, plan=plan):
            raise PoolLifecycleError("openshell_pool_legacy_migration_old_stop_failed") from first_error
        try:
            retried = lifecycle.stop(pilot_id=plan.run_id)
        except Exception as exc:
            raise PoolLifecycleError("openshell_pool_legacy_migration_old_stop_failed") from exc
        if (
            not isinstance(retried, Mapping)
            or retried.get("ok") is not True
            or retried.get("status") != "stopped"
            or retried.get("run_id") != plan.run_id
        ):
            raise PoolLifecycleError("openshell_pool_legacy_migration_old_stop_failed")
        return retried

    @staticmethod
    def _result(
        result: Mapping[str, Any],
        *,
        binding: Mapping[str, Any],
        status: str | None = None,
    ) -> dict[str, Any]:
        sanitized = dict(result)
        sanitized.update(
            {
                "pool_schema_version": SCHEMA_VERSION,
                "pool_slot_id": binding["scope_id"],
                "pool_state": str(Path(str(binding["active"])).parent),
                "local_port": binding["local_port"],
                "target_port": binding["target_port"],
            }
        )
        if status is not None:
            sanitized["status"] = status
        return sanitized

    def start(
        self,
        *,
        market: str,
        company: str,
        run_id: str,
        local_port: int = DEFAULT_LOCAL_PORT,
    ) -> dict[str, Any]:
        try:
            plan = pool.plan_slot(
                market=market,
                company=company,
                run_id=run_id,
                local_port=local_port,
                project_root=self.project_root,
            )
            registry = pool.load_registry(project_root=self.project_root)
            if any(item.get("scope_id") == plan.scope_id for item in registry["bindings"]):
                raise PoolLifecycleError("openshell_pool_scope_already_registered")
            reservation = pool.allocate_local_port(
                project_root=self.project_root,
                first=local_port,
                last=local_port,
            )
        except PoolLifecycleError:
            raise
        except pool.PoolRegistryError as exc:
            raise PoolLifecycleError(exc.code) from exc

        lifecycle = self.lifecycle_factory(
            project_root=self.project_root,
            pool_slot_id=plan.scope_id,
            local_port=reservation.local_port,
            reservation_token=reservation.reservation_token,
        )
        try:
            result = lifecycle.start(market=plan.market, company=plan.company, pilot_id=plan.run_id)
        except WidePilotError as exc:
            # The reservation remains owner-only until its short TTL expires.
            # Reusing it after an uncertain start would weaken port ownership.
            raise PoolLifecycleError(exc.code) from exc

        binding, route = self._registered_binding(
            market=plan.market,
            company=plan.company,
            expected_run_id=plan.run_id,
        )
        if (
            binding["local_port"] != reservation.local_port
            or route.base != f"http://{pool.FORWARD_HOST}:{reservation.local_port}/v1/runs"
        ):
            raise PoolLifecycleError("openshell_pool_post_register_verification_failed")
        return self._result(result, binding=binding)

    def status(self, *, market: str, company: str, run_id: str | None = None) -> dict[str, Any]:
        binding, _route = self._registered_binding(
            market=market,
            company=company,
            expected_run_id=run_id,
        )
        lifecycle = self.lifecycle_factory(
            project_root=self.project_root,
            pool_slot_id=binding["scope_id"],
            local_port=binding["local_port"],
        )
        return self._result(lifecycle.status(pilot_id=binding["run_id"]), binding=binding)

    def probe(self, *, market: str, company: str, run_id: str | None = None) -> dict[str, Any]:
        binding, _route = self._registered_binding(
            market=market,
            company=company,
            expected_run_id=run_id,
        )
        lifecycle = self.lifecycle_factory(
            project_root=self.project_root,
            pool_slot_id=binding["scope_id"],
            local_port=binding["local_port"],
        )
        return self._result(lifecycle.probe(pilot_id=binding["run_id"]), binding=binding)

    def stop(
        self,
        *,
        market: str,
        company: str,
        run_id: str | None = None,
        rollback: bool = False,
    ) -> dict[str, Any]:
        binding, _route = self._registered_binding(
            market=market,
            company=company,
            expected_run_id=run_id,
        )
        lifecycle = self.lifecycle_factory(
            project_root=self.project_root,
            pool_slot_id=binding["scope_id"],
            local_port=binding["local_port"],
        )
        if rollback:
            result = lifecycle.rollback(run_id=binding["run_id"])
        else:
            result = lifecycle.stop(pilot_id=binding["run_id"])
        return self._result(result, binding=binding)

    def migrate_legacy(
        self,
        *,
        market: str,
        company: str,
        old_run_id: str,
        new_run_id: str,
        local_port: int = DEFAULT_LOCAL_PORT,
    ) -> dict[str, Any]:
        try:
            old_plan = pool.plan_slot(
                market=market,
                company=company,
                run_id=old_run_id,
                local_port=pool.TARGET_PORT,
                project_root=self.project_root,
            )
            new_plan = pool.plan_slot(
                market=market,
                company=company,
                run_id=new_run_id,
                local_port=local_port,
                project_root=self.project_root,
            )
        except pool.PoolRegistryError as exc:
            raise PoolLifecycleError(exc.code) from exc
        if old_run_id == new_run_id:
            raise PoolLifecycleError("openshell_pool_legacy_migration_run_ids_must_differ")
        if not pool.FIRST_POOL_PORT <= local_port <= pool.LAST_POOL_PORT:
            raise PoolLifecycleError("openshell_pool_legacy_migration_port_invalid")
        if old_plan.scope_id != new_plan.scope_id:
            raise PoolLifecycleError("openshell_pool_legacy_migration_scope_mismatch")

        legacy_lifecycle = self.lifecycle_factory(project_root=self.project_root)
        self._require_maintenance_lock(legacy_lifecycle)
        legacy_binding, _legacy_route, registry = self._legacy_binding(
            market=old_plan.market,
            company=old_plan.company,
            expected_run_id=old_plan.run_id,
        )
        other_bindings = self._other_bindings(registry, scope_id=old_plan.scope_id)
        try:
            legacy_status = legacy_lifecycle.status(pilot_id=old_plan.run_id)
        except Exception as exc:
            raise PoolLifecycleError("openshell_pool_legacy_migration_old_status_failed") from exc
        if (
            not isinstance(legacy_status, Mapping)
            or legacy_status.get("ok") is not True
            or legacy_status.get("status") != "running"
            or legacy_status.get("run_id") != old_plan.run_id
        ):
            raise PoolLifecycleError("openshell_pool_legacy_migration_old_status_failed")

        try:
            drained = concurrency.set_traffic_state(
                market=old_plan.market,
                company=old_plan.company,
                run_id=old_plan.run_id,
                traffic_state="draining",
                require_idle=True,
                project_root=self.project_root,
            )
        except concurrency.PoolConcurrencyError as exc:
            code = (
                "openshell_pool_legacy_migration_live_leases"
                if exc.code == "openshell_pool_live_leases_require_terminal"
                else exc.code
            )
            raise PoolLifecycleError(code) from exc
        except Exception as exc:
            raise PoolLifecycleError("openshell_pool_legacy_migration_drain_failed") from exc
        if (
            drained.get("traffic_state") != "draining"
            or drained.get("run_id") != old_plan.run_id
            or drained.get("drained") is not True
            or any(drained.get(field) != 0 for field in ("active_leases", "orphaned_leases", "waiting_leases"))
        ):
            self._restore_accepting(
                market=old_plan.market,
                company=old_plan.company,
                run_id=old_plan.run_id,
            )
            raise PoolLifecycleError("openshell_pool_legacy_migration_live_leases")

        try:
            current_binding, _route, current_registry = self._legacy_binding(
                market=old_plan.market,
                company=old_plan.company,
                expected_run_id=old_plan.run_id,
            )
            if (
                current_binding != legacy_binding
                or self._other_bindings(
                    current_registry,
                    scope_id=old_plan.scope_id,
                )
                != other_bindings
            ):
                raise PoolLifecycleError("openshell_pool_legacy_migration_registry_changed")
        except Exception as exc:
            self._restore_accepting(
                market=old_plan.market,
                company=old_plan.company,
                run_id=old_plan.run_id,
            )
            if isinstance(exc, PoolLifecycleError):
                raise
            raise PoolLifecycleError("openshell_pool_legacy_migration_pre_unregister_failed") from exc

        try:
            unregistered = pool.unregister(
                market=old_plan.market,
                company=old_plan.company,
                run_id=old_plan.run_id,
                project_root=self.project_root,
            )
        except Exception as exc:
            try:
                self._legacy_binding(
                    market=old_plan.market,
                    company=old_plan.company,
                    expected_run_id=old_plan.run_id,
                )
            except Exception as state_exc:
                raise PoolLifecycleError("openshell_pool_legacy_migration_unregister_uncertain") from state_exc
            self._restore_accepting(
                market=old_plan.market,
                company=old_plan.company,
                run_id=old_plan.run_id,
            )
            raise PoolLifecycleError("openshell_pool_legacy_migration_unregister_failed") from exc

        if self._other_bindings(unregistered, scope_id=old_plan.scope_id) != other_bindings:
            raise PoolLifecycleError("openshell_pool_legacy_migration_other_binding_changed")
        try:
            host_route = pool.resolve(
                market=old_plan.market,
                company=old_plan.company,
                project_root=self.project_root,
            )
        except Exception as exc:
            raise PoolLifecycleError("openshell_pool_legacy_migration_host_fallback_failed") from exc
        if host_route.target != "host":
            raise PoolLifecycleError("openshell_pool_legacy_migration_host_fallback_failed")

        self._stop_legacy_with_one_retry(legacy_lifecycle, plan=old_plan)

        try:
            self.start(
                market=new_plan.market,
                company=new_plan.company,
                run_id=new_plan.run_id,
                local_port=new_plan.local_port,
            )
        except Exception as exc:
            try:
                self._ensure_host_after_failed_new_start(
                    market=new_plan.market,
                    company=new_plan.company,
                    scope_id=new_plan.scope_id,
                    run_id=new_plan.run_id,
                )
            except PoolLifecycleError as restore_exc:
                raise restore_exc from exc
            raise PoolLifecycleError("openshell_pool_legacy_migration_new_start_failed") from exc

        try:
            new_binding, new_route = self._registered_binding(
                market=new_plan.market,
                company=new_plan.company,
                expected_run_id=new_plan.run_id,
            )
            final_registry = pool.load_registry(project_root=self.project_root)
            if (
                new_binding.get("local_port") != new_plan.local_port
                or new_route.base != f"http://{pool.FORWARD_HOST}:{new_plan.local_port}/v1/runs"
                or self._other_bindings(final_registry, scope_id=new_plan.scope_id) != other_bindings
            ):
                raise PoolLifecycleError("openshell_pool_legacy_migration_postcheck_failed")
        except Exception as exc:
            try:
                self._ensure_host_after_failed_new_start(
                    market=new_plan.market,
                    company=new_plan.company,
                    scope_id=new_plan.scope_id,
                    run_id=new_plan.run_id,
                )
            except PoolLifecycleError as restore_exc:
                raise restore_exc from exc
            raise PoolLifecycleError("openshell_pool_legacy_migration_postcheck_failed") from exc
        return {
            "ok": True,
            "status": "migrated",
            "profile": pool.PROFILE,
            "market": new_plan.market,
            "company": new_plan.company,
            "old_run_id": old_plan.run_id,
            "new_run_id": new_plan.run_id,
            "old_local_port": pool.TARGET_PORT,
            "local_port": new_plan.local_port,
            "target_port": pool.TARGET_PORT,
            "pool_schema_version": SCHEMA_VERSION,
            "pool_slot_id": new_plan.scope_id,
            "pool_state": str(Path(str(new_binding["active"])).parent),
            "host_fallback_boundary_verified": True,
            "other_bindings_unchanged": True,
        }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    start = subparsers.add_parser("start")
    start.add_argument(ACKNOWLEDGEMENT, action="store_true", required=True)
    start.add_argument("--market", required=True, choices=sorted(pool.MARKET_ROOTS))
    start.add_argument("--company", required=True)
    start.add_argument("--run-id", required=True)
    start.add_argument("--local-port", type=int, default=DEFAULT_LOCAL_PORT)
    migrate = subparsers.add_parser("migrate-legacy")
    migrate.add_argument(ACKNOWLEDGEMENT, action="store_true", required=True)
    migrate.add_argument("--market", required=True, choices=sorted(pool.MARKET_ROOTS))
    migrate.add_argument("--company", required=True)
    migrate.add_argument("--old-run-id", required=True)
    migrate.add_argument("--new-run-id", required=True)
    migrate.add_argument("--local-port", type=int, default=DEFAULT_LOCAL_PORT)
    for name in ("status", "probe", "stop", "rollback"):
        child = subparsers.add_parser(name)
        child.add_argument("--market", required=True, choices=sorted(pool.MARKET_ROOTS))
        child.add_argument("--company", required=True)
        child.add_argument("--run-id")
    return parser


def main(argv: list[str] | None = None) -> int:
    os.umask(0o077)
    args = _parser().parse_args(argv)
    try:
        manager = PoolLifecycleManager()
        if args.command == "start":
            result = manager.start(
                market=args.market,
                company=args.company,
                run_id=args.run_id,
                local_port=args.local_port,
            )
        elif args.command == "migrate-legacy":
            result = manager.migrate_legacy(
                market=args.market,
                company=args.company,
                old_run_id=args.old_run_id,
                new_run_id=args.new_run_id,
                local_port=args.local_port,
            )
        elif args.command == "status":
            result = manager.status(market=args.market, company=args.company, run_id=args.run_id)
        elif args.command == "probe":
            result = manager.probe(market=args.market, company=args.company, run_id=args.run_id)
        else:
            result = manager.stop(
                market=args.market,
                company=args.company,
                run_id=args.run_id,
                rollback=args.command == "rollback",
            )
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
        return 0 if result.get("ok") is True else 1
    except (OSError, TypeError, ValueError, UnicodeError, PoolLifecycleError, WidePilotError) as exc:
        if isinstance(exc, (PoolLifecycleError, WidePilotError)):
            code = exc.code
        else:
            code = "openshell_pool_lifecycle_os_error"
        print(json.dumps({"ok": False, "error_code": code}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
