from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.openshell import (
    siq_analysis_pool_concurrency as concurrency,
    siq_analysis_pool_lifecycle as lifecycle_module,
    siq_analysis_pool_registry as pool,
)


def _company(root: Path, company: str) -> None:
    analysis = root / "data/wiki/companies" / company / "analysis"
    analysis.mkdir(parents=True)


def _binding(*, scope_id: str, company: str, run_id: str, local_port: int = 28652) -> dict:
    return {
        "scope_id": scope_id,
        "market": "cn",
        "company": company,
        "run_id": run_id,
        "active": f"var/openshell/canary/siq-analysis/pool/slots/{scope_id}/active.json",
        "local_port": local_port,
        "target_port": 28651,
    }


class _MigrationHarness:
    company = "600104-上汽集团"
    other_company = "600519-贵州茅台"
    old_run_id = "canary-111111111111"
    new_run_id = "canary-222222222222"
    other_run_id = "canary-333333333333"

    def __init__(self, root: Path, monkeypatch) -> None:
        _company(root, self.company)
        _company(root, self.other_company)
        self.root = root
        self.scope_id = hashlib.sha256(f"cn\0{self.company}".encode()).hexdigest()[:24]
        self.other_scope_id = hashlib.sha256(f"cn\0{self.other_company}".encode()).hexdigest()[:24]
        self.legacy_binding = {
            **_binding(
                scope_id=self.scope_id,
                company=self.company,
                run_id=self.old_run_id,
                local_port=pool.TARGET_PORT,
            ),
            "active": pool.LEGACY_ACTIVE_RELATIVE.as_posix(),
        }
        self.other_binding = _binding(
            scope_id=self.other_scope_id,
            company=self.other_company,
            run_id=self.other_run_id,
            local_port=28653,
        )
        self.bindings = [dict(self.other_binding), dict(self.legacy_binding)]
        self.events: list[object] = []
        self.traffic_state = "accepting"
        self.live_lease_state = ""
        self.unregister_fails = False
        self.old_stop_fails = False
        self.old_stop_failures_remaining = 0
        self.old_stop_retry_phase = "running"
        self.old_stop_retry_run_id = self.old_run_id
        self.new_start_fails = False
        self.new_start_registers_before_failure = False
        self.lock_held = True
        self.reservation_token = "reservation-" + "a" * 32

        monkeypatch.setattr(pool, "load_registry", self.load_registry)
        monkeypatch.setattr(pool, "resolve", self.resolve)
        monkeypatch.setattr(pool, "unregister", self.unregister)
        monkeypatch.setattr(pool, "allocate_local_port", self.allocate)
        monkeypatch.setattr(concurrency, "set_traffic_state", self.set_traffic_state)

    def load_registry(self, **_kwargs) -> dict:
        return {"bindings": [dict(item) for item in self.bindings]}

    def resolve(self, *, market: str, company: str, **_kwargs):
        binding = next(
            (item for item in self.bindings if item["market"] == market and item["company"] == company),
            None,
        )
        if binding is None:
            self.events.append(("resolve", company, "host"))
            return pool.PoolRoute(target="host", market=market, company=company)
        return pool.PoolRoute(
            target="openshell",
            market=market,
            company=company,
            base=f"http://127.0.0.1:{binding['local_port']}/v1/runs",
            run_id=binding["run_id"],
        )

    def unregister(self, *, market: str, company: str, run_id: str, **_kwargs) -> dict:
        self.events.append(("unregister", run_id))
        if self.unregister_fails:
            raise pool.PoolRegistryError("openshell_pool_unregister_write_failed")
        matching = [item for item in self.bindings if item["market"] == market and item["company"] == company]
        if len(matching) != 1 or matching[0]["run_id"] != run_id:
            raise pool.PoolRegistryError("openshell_pool_unregister_run_mismatch")
        self.bindings = [item for item in self.bindings if item is not matching[0]]
        return self.load_registry()

    def allocate(self, **kwargs):
        self.events.append(("allocate", kwargs["first"], kwargs["last"]))
        return pool.PortReservation(
            local_port=kwargs["first"],
            reservation_token=self.reservation_token,
            expires_at=900,
        )

    def set_traffic_state(self, *, traffic_state: str, require_idle: bool = False, **kwargs):
        self.events.append(("traffic", traffic_state, require_idle))
        if require_idle and self.live_lease_state:
            raise concurrency.PoolConcurrencyError(
                "openshell_pool_live_leases_require_terminal",
                retryable=True,
            )
        self.traffic_state = traffic_state
        return {
            "market": kwargs["market"],
            "company": kwargs["company"],
            "run_id": kwargs["run_id"],
            "traffic_state": traffic_state,
            "active_leases": 0,
            "orphaned_leases": 0,
            "waiting_leases": 0,
            "drained": True,
        }

    def lifecycle_factory(self, **kwargs):
        harness = self
        pool_managed = "pool_slot_id" in kwargs

        class FakeLifecycle:
            adapter = SimpleNamespace(
                backend=SimpleNamespace(
                    maintenance_lock_held=lambda: harness.lock_held,
                )
            )
            active_path = harness.root / pool.LEGACY_ACTIVE_RELATIVE

            @staticmethod
            def status(*, pilot_id):
                harness.events.append(("legacy_status", pilot_id))
                return {"ok": True, "status": "running", "run_id": pilot_id}

            @staticmethod
            def stop(*, pilot_id):
                harness.events.append(("legacy_stop", pilot_id))
                if harness.old_stop_failures_remaining > 0:
                    harness.old_stop_failures_remaining -= 1
                    raise RuntimeError("secret-bearing backend failure must not escape")
                if harness.old_stop_fails:
                    raise RuntimeError("secret-bearing backend failure must not escape")
                return {"ok": True, "status": "stopped", "run_id": pilot_id}

            @staticmethod
            def _load_active_spec(pilot_id):
                harness.events.append(("legacy_retry_validate", pilot_id))
                run_id = harness.old_stop_retry_run_id
                spec = SimpleNamespace(
                    run_id=run_id,
                    market="cn",
                    company=harness.company,
                    analysis_relative_path=f"data/wiki/companies/{harness.company}/analysis",
                )
                manifest = {
                    "phase": harness.old_stop_retry_phase,
                    "run_id": run_id,
                    "market": "cn",
                    "company": harness.company,
                    "analysis_relative_path": f"data/wiki/companies/{harness.company}/analysis",
                }
                return spec, manifest

            @staticmethod
            def start(*, market, company, pilot_id):
                assert pool_managed is True
                harness.events.append(("pool_start", pilot_id))
                replacement = _binding(
                    scope_id=kwargs["pool_slot_id"],
                    company=company,
                    run_id=pilot_id,
                    local_port=kwargs["local_port"],
                )
                if harness.new_start_registers_before_failure:
                    harness.bindings.append(replacement)
                if harness.new_start_fails:
                    raise lifecycle_module.WidePilotError("fake_new_start_failed")
                if not harness.new_start_registers_before_failure:
                    harness.bindings.append(replacement)
                return {"ok": True, "status": "running", "run_id": pilot_id}

        return FakeLifecycle()

    def manager(self) -> lifecycle_module.PoolLifecycleManager:
        return lifecycle_module.PoolLifecycleManager(
            project_root=self.root,
            lifecycle_factory=self.lifecycle_factory,
        )

    def migrate(self) -> dict:
        return self.manager().migrate_legacy(
            market="cn",
            company=self.company,
            old_run_id=self.old_run_id,
            new_run_id=self.new_run_id,
            local_port=28652,
        )


def test_manager_allocates_exact_port_and_never_exposes_reservation_token(tmp_path: Path, monkeypatch) -> None:
    company = "600519-贵州茅台"
    run_id = "canary-123456789abc"
    _company(tmp_path, company)
    scope_id = hashlib.sha256(f"cn\0{company}".encode()).hexdigest()[:24]
    state: dict[str, object] = {"bindings": []}
    token = "reservation-" + "a" * 32
    observed: dict[str, object] = {}

    monkeypatch.setattr(
        pool,
        "load_registry",
        lambda **_kwargs: {"bindings": list(state["bindings"])},
    )

    def allocate(**kwargs):
        observed["allocate"] = kwargs
        return pool.PortReservation(local_port=28652, reservation_token=token, expires_at=900)

    monkeypatch.setattr(pool, "allocate_local_port", allocate)

    def resolve(**_kwargs):
        return pool.PoolRoute(
            target="openshell",
            market="cn",
            company=company,
            base="http://127.0.0.1:28652/v1/runs",
            run_id=run_id,
        )

    monkeypatch.setattr(pool, "resolve", resolve)

    class FakeLifecycle:
        def __init__(self, **kwargs):
            observed["lifecycle"] = kwargs

        def start(self, *, market, company, pilot_id):
            assert (market, company, pilot_id) == ("cn", "600519-贵州茅台", run_id)
            state["bindings"] = [_binding(scope_id=scope_id, company=company, run_id=run_id)]
            return {"ok": True, "status": "running", "runs_url": "http://127.0.0.1:28652/v1/runs"}

    manager = lifecycle_module.PoolLifecycleManager(
        project_root=tmp_path,
        lifecycle_factory=FakeLifecycle,
    )
    result = manager.start(market="cn", company=company, run_id=run_id, local_port=28652)

    assert observed["allocate"]["first"] == observed["allocate"]["last"] == 28652
    assert observed["lifecycle"]["pool_slot_id"] == scope_id
    assert observed["lifecycle"]["reservation_token"] == token
    assert result["local_port"] == 28652
    assert result["target_port"] == 28651
    assert token not in json.dumps(result, ensure_ascii=False)


def test_manager_recovers_unregistered_orphan_before_start(tmp_path: Path, monkeypatch) -> None:
    company = "600104-上汽集团"
    orphan_run_id = "canary-111111111111"
    new_run_id = "canary-222222222222"
    _company(tmp_path, company)
    scope_id = hashlib.sha256(f"cn\0{company}".encode()).hexdigest()[:24]
    slot = tmp_path / pool.SLOTS_RELATIVE / scope_id
    run_state = pool.SLOTS_RELATIVE / scope_id / "runs" / orphan_run_id
    active = {
        "schema_version": lifecycle_module.CANARY_SCHEMA_VERSION,
        "mode": lifecycle_module.CANARY_MODE,
        "readiness_effect": "none",
        "profile": pool.PROFILE,
        "run_id": orphan_run_id,
        "market": "cn",
        "company": company,
        "run_state": run_state.as_posix(),
        "manifest": f"{run_state.as_posix()}/canary.json",
        "manifest_sha256": "1" * 64,
        "api_key_sha256": "2" * 64,
    }
    slot.mkdir(parents=True)
    active_path = slot / "active.json"
    active_path.write_text(json.dumps(active), encoding="utf-8")
    active_path.chmod(0o600)

    state: dict[str, object] = {"bindings": []}
    events: list[object] = []
    token = "reservation-" + "a" * 32

    monkeypatch.setattr(pool, "load_registry", lambda **_kwargs: {"bindings": list(state["bindings"])})
    monkeypatch.setattr(
        pool,
        "allocate_local_port",
        lambda **kwargs: pool.PortReservation(
            local_port=kwargs["first"],
            reservation_token=token,
            expires_at=900,
        ),
    )
    monkeypatch.setattr(
        concurrency,
        "status",
        lambda **_kwargs: {"active_leases": 0, "orphaned_leases": 0, "waiting_leases": 0},
    )
    monkeypatch.setattr(
        pool,
        "resolve",
        lambda **_kwargs: pool.PoolRoute(
            target="openshell",
            market="cn",
            company=company,
            base="http://127.0.0.1:28652/v1/runs",
            run_id=new_run_id,
        ),
    )

    class FakeLifecycle:
        def __init__(self, **kwargs):
            events.append(("init", dict(kwargs)))

        def stop(self, *, pilot_id):
            events.append(("stop", pilot_id))
            return {"ok": True, "status": "stopped", "run_id": pilot_id}

        def start(self, *, market, company, pilot_id):
            events.append(("start", pilot_id))
            state["bindings"] = [
                _binding(scope_id=scope_id, company=company, run_id=pilot_id)
            ]
            return {"ok": True, "status": "running", "run_id": pilot_id}

    result = lifecycle_module.PoolLifecycleManager(
        project_root=tmp_path,
        lifecycle_factory=FakeLifecycle,
    ).start(market="cn", company=company, run_id=new_run_id, local_port=28652)

    assert result["status"] == "running"
    assert ("stop", orphan_run_id) in events
    assert events.index(("stop", orphan_run_id)) < events.index(("start", new_run_id))
    orphan_init = next(value for event, value in events if event == "init" and value.get("allow_pool_orphan_stop"))
    assert orphan_init["pool_slot_id"] == scope_id
    assert orphan_init["local_port"] == 28652


def test_manager_prunes_unused_port_reservation_and_retries(tmp_path: Path, monkeypatch) -> None:
    company = "600104-上汽集团"
    run_id = "canary-123456789abc"
    _company(tmp_path, company)
    scope_id = hashlib.sha256(f"cn\0{company}".encode()).hexdigest()[:24]
    state: dict[str, object] = {"bindings": []}
    events: list[object] = []
    token = "reservation-" + "a" * 32

    monkeypatch.setattr(pool, "load_registry", lambda **_kwargs: {"bindings": list(state["bindings"])})

    def allocate(**kwargs):
        events.append(("allocate", kwargs["first"]))
        if events.count(("allocate", kwargs["first"])) == 1:
            raise pool.PoolRegistryError("openshell_pool_no_port_available")
        return pool.PortReservation(
            local_port=kwargs["first"],
            reservation_token=token,
            expires_at=900,
        )

    monkeypatch.setattr(pool, "allocate_local_port", allocate)
    monkeypatch.setattr(
        pool,
        "prune_unused_port_reservations",
        lambda **_kwargs: events.append(("prune", 28652)) or {"ok": True, "pruned": 1},
    )
    monkeypatch.setattr(
        pool,
        "resolve",
        lambda **_kwargs: pool.PoolRoute(
            target="openshell",
            market="cn",
            company=company,
            base="http://127.0.0.1:28652/v1/runs",
            run_id=run_id,
        ),
    )

    class FakeLifecycle:
        def __init__(self, **_kwargs):
            pass

        def start(self, *, market, company, pilot_id):
            state["bindings"] = [
                _binding(scope_id=scope_id, company=company, run_id=pilot_id)
            ]
            return {"ok": True, "status": "running", "run_id": pilot_id}

    result = lifecycle_module.PoolLifecycleManager(
        project_root=tmp_path,
        lifecycle_factory=FakeLifecycle,
    ).start(market="cn", company=company, run_id=run_id, local_port=28652)

    assert result["status"] == "running"
    assert events == [("allocate", 28652), ("prune", 28652), ("allocate", 28652)]


def test_status_and_stop_derive_exact_endpoint_from_registered_binding(tmp_path: Path, monkeypatch) -> None:
    company = "600519-贵州茅台"
    run_id = "canary-123456789abc"
    _company(tmp_path, company)
    scope_id = hashlib.sha256(f"cn\0{company}".encode()).hexdigest()[:24]
    binding = _binding(scope_id=scope_id, company=company, run_id=run_id)
    observed: list[dict] = []

    monkeypatch.setattr(pool, "load_registry", lambda **_kwargs: {"bindings": [binding]})
    monkeypatch.setattr(
        pool,
        "resolve",
        lambda **_kwargs: pool.PoolRoute(
            target="openshell",
            market="cn",
            company=company,
            base="http://127.0.0.1:28652/v1/runs",
            run_id=run_id,
        ),
    )

    class FakeLifecycle:
        def __init__(self, **kwargs):
            observed.append(kwargs)

        @staticmethod
        def status(*, pilot_id):
            return {"ok": True, "status": "running", "run_id": pilot_id}

        @staticmethod
        def stop(*, pilot_id):
            return {"ok": True, "status": "stopped", "run_id": pilot_id}

    manager = lifecycle_module.PoolLifecycleManager(
        project_root=tmp_path,
        lifecycle_factory=FakeLifecycle,
    )
    status = manager.status(market="cn", company=company, run_id=run_id)
    stopped = manager.stop(market="cn", company=company, run_id=run_id)

    assert status["status"] == "running"
    assert stopped["status"] == "stopped"
    assert [value["local_port"] for value in observed] == [28652, 28652]
    assert all(value["pool_slot_id"] == scope_id for value in observed)
    assert all("reservation_token" not in value for value in observed)


def test_migrate_legacy_replaces_only_exact_company_binding(tmp_path: Path, monkeypatch) -> None:
    harness = _MigrationHarness(tmp_path, monkeypatch)

    result = harness.migrate()

    assert result == {
        "ok": True,
        "status": "migrated",
        "profile": "siq_analysis",
        "market": "cn",
        "company": harness.company,
        "old_run_id": harness.old_run_id,
        "new_run_id": harness.new_run_id,
        "old_local_port": 28651,
        "local_port": 28652,
        "target_port": 28651,
        "pool_schema_version": lifecycle_module.SCHEMA_VERSION,
        "pool_slot_id": harness.scope_id,
        "pool_state": f"var/openshell/canary/siq-analysis/pool/slots/{harness.scope_id}",
        "host_fallback_boundary_verified": True,
        "other_bindings_unchanged": True,
    }
    assert harness.other_binding in harness.bindings
    replacement = next(item for item in harness.bindings if item["scope_id"] == harness.scope_id)
    assert replacement["run_id"] == harness.new_run_id
    assert replacement["local_port"] == 28652
    assert harness.reservation_token not in json.dumps(result, ensure_ascii=False)
    assert ("traffic", "draining", True) in harness.events
    assert ("resolve", harness.company, "host") in harness.events
    assert harness.events.index(("unregister", harness.old_run_id)) < harness.events.index(
        ("legacy_stop", harness.old_run_id)
    )
    assert harness.events.index(("legacy_stop", harness.old_run_id)) < harness.events.index(
        ("pool_start", harness.new_run_id)
    )


@pytest.mark.parametrize("lease_state", ["active", "orphaned", "waiting"])
def test_migrate_legacy_rejects_every_live_lease_without_draining(
    tmp_path: Path,
    monkeypatch,
    lease_state: str,
) -> None:
    harness = _MigrationHarness(tmp_path, monkeypatch)
    harness.live_lease_state = lease_state

    with pytest.raises(
        lifecycle_module.PoolLifecycleError,
        match="openshell_pool_legacy_migration_live_leases",
    ) as error:
        harness.migrate()

    assert error.value.code == "openshell_pool_legacy_migration_live_leases"
    assert harness.traffic_state == "accepting"
    assert harness.bindings == [harness.other_binding, harness.legacy_binding]
    assert ("unregister", harness.old_run_id) not in harness.events
    assert ("legacy_stop", harness.old_run_id) not in harness.events
    assert ("pool_start", harness.new_run_id) not in harness.events


def test_migrate_legacy_restores_accepting_when_unregister_fails(tmp_path: Path, monkeypatch) -> None:
    harness = _MigrationHarness(tmp_path, monkeypatch)
    harness.unregister_fails = True

    with pytest.raises(
        lifecycle_module.PoolLifecycleError,
        match="openshell_pool_legacy_migration_unregister_failed",
    ):
        harness.migrate()

    assert harness.traffic_state == "accepting"
    assert harness.bindings == [harness.other_binding, harness.legacy_binding]
    assert harness.events[-1] == ("traffic", "accepting", False)
    assert ("legacy_stop", harness.old_run_id) not in harness.events


def test_migrate_legacy_old_stop_failure_leaves_company_on_host(tmp_path: Path, monkeypatch) -> None:
    harness = _MigrationHarness(tmp_path, monkeypatch)
    harness.old_stop_fails = True

    with pytest.raises(
        lifecycle_module.PoolLifecycleError,
        match="openshell_pool_legacy_migration_old_stop_failed",
    ) as error:
        harness.migrate()

    assert error.value.code == "openshell_pool_legacy_migration_old_stop_failed"
    assert harness.resolve(market="cn", company=harness.company).target == "host"
    assert harness.bindings == [harness.other_binding]
    assert ("pool_start", harness.new_run_id) not in harness.events
    assert "secret-bearing backend failure" not in str(error.value)


def test_migrate_legacy_retries_stopping_old_run_once_and_succeeds(tmp_path: Path, monkeypatch) -> None:
    harness = _MigrationHarness(tmp_path, monkeypatch)
    harness.old_stop_failures_remaining = 1
    harness.old_stop_retry_phase = "stopping"

    result = harness.migrate()

    assert result["status"] == "migrated"
    assert harness.events.count(("legacy_stop", harness.old_run_id)) == 2
    assert harness.events.count(("legacy_retry_validate", harness.old_run_id)) == 1
    assert ("pool_start", harness.new_run_id) in harness.events


def test_migrate_legacy_second_old_stop_failure_stays_on_host(tmp_path: Path, monkeypatch) -> None:
    harness = _MigrationHarness(tmp_path, monkeypatch)
    harness.old_stop_failures_remaining = 2
    harness.old_stop_retry_phase = "stopping"

    with pytest.raises(
        lifecycle_module.PoolLifecycleError,
        match="openshell_pool_legacy_migration_old_stop_failed",
    ):
        harness.migrate()

    assert harness.events.count(("legacy_stop", harness.old_run_id)) == 2
    assert harness.events.count(("legacy_retry_validate", harness.old_run_id)) == 1
    assert harness.resolve(market="cn", company=harness.company).target == "host"
    assert harness.bindings == [harness.other_binding]
    assert ("pool_start", harness.new_run_id) not in harness.events


def test_migrate_legacy_does_not_retry_uncertain_old_identity(tmp_path: Path, monkeypatch) -> None:
    harness = _MigrationHarness(tmp_path, monkeypatch)
    harness.old_stop_failures_remaining = 1
    harness.old_stop_retry_phase = "stopping"
    harness.old_stop_retry_run_id = "canary-ffffffffffff"

    with pytest.raises(
        lifecycle_module.PoolLifecycleError,
        match="openshell_pool_legacy_migration_old_stop_failed",
    ):
        harness.migrate()

    assert harness.events.count(("legacy_stop", harness.old_run_id)) == 1
    assert harness.events.count(("legacy_retry_validate", harness.old_run_id)) == 1
    assert harness.resolve(market="cn", company=harness.company).target == "host"
    assert ("pool_start", harness.new_run_id) not in harness.events


@pytest.mark.parametrize("registered_before_failure", [False, True])
def test_migrate_legacy_new_start_failure_leaves_company_on_host(
    tmp_path: Path,
    monkeypatch,
    registered_before_failure: bool,
) -> None:
    harness = _MigrationHarness(tmp_path, monkeypatch)
    harness.new_start_fails = True
    harness.new_start_registers_before_failure = registered_before_failure

    with pytest.raises(
        lifecycle_module.PoolLifecycleError,
        match="openshell_pool_legacy_migration_new_start_failed",
    ) as error:
        harness.migrate()

    assert error.value.code == "openshell_pool_legacy_migration_new_start_failed"
    assert harness.resolve(market="cn", company=harness.company).target == "host"
    assert harness.bindings == [harness.other_binding]
    assert ("legacy_stop", harness.old_run_id) in harness.events
    assert ("pool_start", harness.new_run_id) in harness.events


def test_migrate_legacy_requires_inherited_maintenance_lock(tmp_path: Path, monkeypatch) -> None:
    harness = _MigrationHarness(tmp_path, monkeypatch)
    harness.lock_held = False

    with pytest.raises(
        lifecycle_module.PoolLifecycleError,
        match="openshell_pool_legacy_migration_maintenance_lock_required",
    ):
        harness.migrate()

    assert harness.events == []
    assert harness.bindings == [harness.other_binding, harness.legacy_binding]


def test_pool_wrapper_holds_maintenance_lock_and_scrubs_environment() -> None:
    source = (Path(__file__).resolve().parents[1] / "run_siq_analysis_pool_lifecycle.sh").read_text(encoding="utf-8")
    assert "siq_openshell_acquire_maintenance_lock" in source
    assert '"$COMMAND" == migrate-legacy' in source
    assert "<start|migrate-legacy|stop|status|probe|rollback>" in source
    assert "/usr/bin/env -i" in source
    assert "SIQ_HERMES_RUNTIME" not in source
