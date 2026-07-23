from __future__ import annotations

import asyncio
import contextlib

import anyio
import pytest
from services.openshell_pool_adapter import ResolvedPoolBinding

from services import openshell_scope_lifecycle


def test_missing_company_binding_is_started_once_for_concurrent_requests(monkeypatch):
    manager = openshell_scope_lifecycle.OpenShellScopeLifecycleManager()
    started = False
    starts: list[tuple[str, str]] = []

    class FakeAdapter:
        def resolve_binding(self, context):
            assert context["company"]["dir"] == "600519-贵州茅台"
            if not started:
                return ResolvedPoolBinding(
                    target="host",
                    market="cn",
                    company="600519-贵州茅台",
                )
            return ResolvedPoolBinding(
                target="openshell",
                market="cn",
                company="600519-贵州茅台",
                run_id="canary-0123456789ab",
                session_namespace="siq:openshell:pool:scope:canary-0123456789ab:siq_analysis",
            )

    def fake_start_binding(*, market: str, company: str) -> None:
        nonlocal started
        starts.append((market, company))
        started = True

    manager.adapter = FakeAdapter()
    monkeypatch.setattr(manager, "_start_binding", fake_start_binding)
    monkeypatch.setattr(manager, "_ensure_sweeper", lambda: None)
    context = {"company": {"market": "cn", "dir": "600519-贵州茅台"}}

    async def run_case():
        return await asyncio.gather(
            manager.ensure_binding(context),
            manager.ensure_binding(context),
        )

    first, second = anyio.run(run_case)

    assert first.target == second.target == "openshell"
    assert first.run_id == second.run_id == "canary-0123456789ab"
    assert starts == [("cn", "600519-贵州茅台")]


def test_context_without_resolved_company_does_not_start_sandbox(monkeypatch):
    manager = openshell_scope_lifecycle.OpenShellScopeLifecycleManager()

    class FakeAdapter:
        def resolve_binding(self, context):
            assert context == {}
            return ResolvedPoolBinding(target="host")

    manager.adapter = FakeAdapter()
    monkeypatch.setattr(
        manager,
        "_start_binding",
        lambda **_kwargs: pytest.fail("sandbox must not start without a verified company scope"),
    )

    binding = anyio.run(manager.ensure_binding, {})

    assert binding.target == "host"


def test_registered_binding_is_probed_once_then_uses_short_health_cache(monkeypatch):
    manager = openshell_scope_lifecycle.OpenShellScopeLifecycleManager()
    binding = ResolvedPoolBinding(
        target="openshell",
        market="cn",
        company="600104-上汽集团",
        run_id="canary-0123456789ab",
    )
    probes: list[str] = []

    class FakeAdapter:
        def resolve_binding(self, _context):
            return binding

    manager.adapter = FakeAdapter()
    monkeypatch.setattr(
        manager,
        "_probe_binding",
        lambda current: probes.append(current.run_id) or True,
    )
    monkeypatch.setattr(manager, "_ensure_sweeper", lambda: None)
    monkeypatch.setattr(openshell_scope_lifecycle.time, "monotonic", lambda: 100.0)

    async def run_case():
        return await manager.ensure_binding({}), await manager.ensure_binding({})

    first, second = anyio.run(run_case)

    assert first == second == binding
    assert probes == ["canary-0123456789ab"]


def test_wide_pilot_probe_error_marks_registered_binding_unhealthy(monkeypatch):
    manager = openshell_scope_lifecycle.OpenShellScopeLifecycleManager()
    binding = ResolvedPoolBinding(
        target="openshell",
        market="cn",
        company="600104-上汽集团",
        run_id="canary-0123456789ab",
    )

    class FakeLifecycleManager:
        def __init__(self, *, project_root):
            assert project_root == manager.project_root

        def probe(self, **_kwargs):
            raise openshell_scope_lifecycle.WidePilotError("canary_pool_runtime_degraded")

    monkeypatch.setattr(openshell_scope_lifecycle, "PoolLifecycleManager", FakeLifecycleManager)

    assert manager._probe_binding(binding) is False


def test_degraded_registered_binding_is_replaced_before_routing(monkeypatch):
    manager = openshell_scope_lifecycle.OpenShellScopeLifecycleManager()
    replaced = False
    stale = ResolvedPoolBinding(
        target="openshell",
        market="cn",
        company="600104-上汽集团",
        run_id="canary-111111111111",
        base="http://127.0.0.1:28652/v1/runs",
    )
    healthy = ResolvedPoolBinding(
        target="openshell",
        market="cn",
        company="600104-上汽集团",
        run_id="canary-222222222222",
        base="http://127.0.0.1:28653/v1/runs",
    )

    class FakeAdapter:
        def resolve_binding(self, _context):
            return healthy if replaced else stale

    def fake_replace(current):
        nonlocal replaced
        assert current == stale
        replaced = True

    manager.adapter = FakeAdapter()
    monkeypatch.setattr(manager, "_probe_binding", lambda current: current == healthy)
    monkeypatch.setattr(manager, "_replace_binding", fake_replace)
    monkeypatch.setattr(manager, "_ensure_sweeper", lambda: None)

    binding = anyio.run(manager.ensure_binding, {})

    assert binding == healthy
    assert replaced is True
    assert manager._recently_verified(healthy)


def test_start_binding_retries_the_next_pool_port(monkeypatch):
    manager = openshell_scope_lifecycle.OpenShellScopeLifecycleManager()
    attempts: list[int] = []

    class FakeLifecycleManager:
        def __init__(self, *, project_root):
            assert project_root == manager.project_root

        def start(self, *, market, company, run_id, local_port):
            attempts.append(local_port)
            if len(attempts) == 1:
                raise openshell_scope_lifecycle.PoolLifecycleError(
                    "openshell_pool_no_port_available"
                )
            return {"ok": True, "run_id": run_id}

        def probe(self, *, market, company, run_id):
            return {"ok": True, "run_id": run_id}

    monkeypatch.setattr(openshell_scope_lifecycle, "PoolLifecycleManager", FakeLifecycleManager)
    monkeypatch.setattr(openshell_scope_lifecycle.pool_registry, "FIRST_POOL_PORT", 28652)
    monkeypatch.setattr(openshell_scope_lifecycle.pool_registry, "LAST_POOL_PORT", 28653)
    monkeypatch.setattr(manager, "_maintenance_lock", contextlib.nullcontext)

    manager._start_binding(market="cn", company="600519-贵州茅台")

    assert attempts == [28652, 28653]


def test_idle_candidate_requires_expired_scope_without_any_leases(monkeypatch):
    manager = openshell_scope_lifecycle.OpenShellScopeLifecycleManager()
    manager._last_used[("cn", "600104-上汽集团")] = 100.0
    registry = {
        "bindings": [
            {
                "market": "cn",
                "company": "600104-上汽集团",
                "scope_id": "9bc20683a73220cad2e19d40",
                "run_id": "canary-0123456789ab",
            }
        ]
    }
    scheduler = {
        "bindings": [
            {
                "scope_id": "9bc20683a73220cad2e19d40",
                "run_id": "canary-0123456789ab",
                "active_leases": 0,
                "orphaned_leases": 0,
                "waiting_leases": 0,
            }
        ]
    }
    monkeypatch.setattr(
        openshell_scope_lifecycle.pool_registry,
        "load_registry",
        lambda **_kwargs: registry,
    )
    monkeypatch.setattr(
        openshell_scope_lifecycle.pool_concurrency,
        "status",
        lambda **_kwargs: scheduler,
    )
    monkeypatch.setattr(openshell_scope_lifecycle.time, "monotonic", lambda: 500.0)
    monkeypatch.setattr(openshell_scope_lifecycle, "_idle_ttl_seconds", lambda: 300)

    assert manager._idle_candidates() == [
        ("cn", "600104-上汽集团", "canary-0123456789ab")
    ]

    scheduler["bindings"][0]["active_leases"] = 1
    assert manager._idle_candidates() == []
