from __future__ import annotations

import asyncio

import httpx
import pytest
from services.hermes_client import HermesRunStatus
from services.openshell_pool_recovery import (
    OpenShellPoolRecoveryManager,
    recovery_enabled,
    recovery_ready,
)
from services.runtime_coordination import (
    ActiveRunLease,
    attach_active_run_pool_lease,
    bind_active_run,
    claim_active_run,
    list_recoverable_active_runs,
    release_active_run,
    utcnow_naive,
)
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession
from tests.test_openshell_pool_adapter import _adapter_with_binding, _context

from services import hermes_client, openshell_pool_adapter as adapter_module


async def _coordination_engine(
    tmp_path,
    *,
    session_id: str,
    run_id: str,
    admission=None,
    pool_tenant_id: str | None = None,
    pool_user_id: str | None = None,
):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'recovery.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(ActiveRunLease.__table__.create)
    async with AsyncSession(engine) as session:
        session.add(
            ActiveRunLease(
                profile="siq_analysis",
                session_id=session_id,
                run_id=run_id,
                owner_id="pre-restart-owner",
                pool_lease_id=admission.lease_id if admission is not None else None,
                pool_scope_id=admission.scope_id if admission is not None else None,
                pool_binding_run_id=admission.run_id if admission is not None else None,
                pool_owner_generation=(
                    admission.owner_generation if admission is not None else None
                ),
                pool_tenant_id=pool_tenant_id,
                pool_user_id=pool_user_id,
                lease_until=utcnow_naive(),
            )
        )
        await session.commit()
    return engine


async def _empty_coordination_engine(tmp_path, *, name: str = "recovery-empty.db"):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / name}")
    async with engine.begin() as connection:
        await connection.run_sync(ActiveRunLease.__table__.create)
    return engine


async def _wait_for_monitors(manager: OpenShellPoolRecoveryManager) -> None:
    for _ in range(200):
        if not manager._tasks:
            return
        await asyncio.sleep(0.005)
    raise AssertionError("recovery monitor did not finish")


@pytest.mark.anyio
async def test_pool_metadata_attaches_before_bind_and_is_cleared_by_next_claim(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'attach.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(ActiveRunLease.__table__.create)
    async with AsyncSession(engine) as session:
        assert await claim_active_run(
            session,
            profile="siq_analysis",
            session_id="user-1-analysis-attach",
            run_id="claim-first",
            owner_id="owner-first",
        )
        assert await attach_active_run_pool_lease(
            session,
            profile="siq_analysis",
            session_id="user-1-analysis-attach",
            provisional_run_id="claim-first",
            owner_id="owner-first",
            pool_lease_id="lease-" + "1" * 32,
            pool_scope_id="2" * 24,
            pool_binding_run_id="canary-123456789abc",
            pool_owner_generation=7,
        )
        assert await bind_active_run(
            session,
            profile="siq_analysis",
            session_id="user-1-analysis-attach",
            provisional_run_id="claim-first",
            run_id="hermes-first",
            owner_id="owner-first",
        )
        snapshot = (await list_recoverable_active_runs(session, profile="siq_analysis"))[0]
        assert (
            snapshot.pool_lease_id,
            snapshot.pool_scope_id,
            snapshot.pool_binding_run_id,
            snapshot.pool_owner_generation,
        ) == (
            "lease-" + "1" * 32,
            "2" * 24,
            "canary-123456789abc",
            7,
        )
        assert await release_active_run(
            session,
            profile="siq_analysis",
            session_id="user-1-analysis-attach",
            run_id="hermes-first",
            owner_id="owner-first",
        )
        assert await claim_active_run(
            session,
            profile="siq_analysis",
            session_id="user-1-analysis-attach",
            run_id="claim-second",
            owner_id="owner-second",
        )
        row = await session.get(ActiveRunLease, 1)
        assert row is not None
        assert (
            row.pool_lease_id,
            row.pool_scope_id,
            row.pool_binding_run_id,
            row.pool_owner_generation,
        ) == (None, None, None, None)
    await engine.dispose()


def _acquire_bound(adapter, binding, session_id, *, tenant_id=None, user_id=None):
    admission = adapter.acquire(
        binding,
        session_id=session_id,
        tenant_id=tenant_id,
        user_id=user_id,
    )
    return adapter.mark_run_bound(
        binding,
        session_id=session_id,
        tenant_id=tenant_id,
        user_id=user_id,
        owner_token=admission.owner_token,
        owner_generation=admission.owner_generation,
    )


@pytest.mark.anyio
async def test_initial_terminal_main_run_is_orphaned_due_to_unknown_child_window(tmp_path):
    adapter = _adapter_with_binding(tmp_path)
    binding = adapter.resolve_binding(_context())
    session_id = "user-1-analysis-restart-terminal"
    original = _acquire_bound(adapter, binding, session_id)
    engine = await _coordination_engine(
        tmp_path,
        session_id=session_id,
        run_id="hermes-main-terminal",
        admission=original,
    )

    async def completed(run_id, **_kwargs):
        return HermesRunStatus(run_id=run_id, status="completed", quiesced=True)

    manager = OpenShellPoolRecoveryManager(
        enabled=True,
        adapter=adapter,
        engine=engine,
        owner_id="recovery-owner",
        status_getter=completed,
        poll_seconds=0.01,
    )
    report = await manager.start()

    assert report.orphaned == 1
    assert report.terminal_released == 0
    assert adapter.concurrency.status(project_root=tmp_path)["orphaned_leases"] == 1
    with pytest.raises(adapter_module.OpenShellPoolAdapterError, match="openshell_pool_owner_mismatch"):
        adapter.heartbeat(
            binding,
            session_id=session_id,
            owner_token=original.owner_token,
            owner_generation=original.owner_generation,
        )
    await manager.stop()
    await engine.dispose()


@pytest.mark.anyio
async def test_nonterminal_observation_then_quiesced_terminal_releases_exact_lease(tmp_path):
    adapter = _adapter_with_binding(tmp_path)
    binding = adapter.resolve_binding(_context())
    session_id = "user-1-analysis-restart-running"
    admission = _acquire_bound(adapter, binding, session_id)
    engine = await _coordination_engine(
        tmp_path,
        session_id=session_id,
        run_id="hermes-main-running",
        admission=admission,
    )
    statuses = [
        HermesRunStatus(run_id="hermes-main-running", status="running", quiesced=False),
        HermesRunStatus(run_id="hermes-main-running", status="completed", quiesced=True),
    ]
    calls = 0

    async def sequence(_run_id, **_kwargs):
        nonlocal calls
        calls += 1
        return statuses.pop(0)

    manager = OpenShellPoolRecoveryManager(
        enabled=True,
        adapter=adapter,
        engine=engine,
        owner_id="recovery-owner",
        status_getter=sequence,
        poll_seconds=0.01,
    )
    report = await manager.start()
    assert report.monitoring == 1
    await _wait_for_monitors(manager)

    assert calls == 2
    assert adapter.concurrency.status(project_root=tmp_path)["active_leases"] == 0
    async with AsyncSession(engine) as session:
        row = await session.get(ActiveRunLease, 1)
        assert row is not None and row.status == "completed"
        assert row.pool_owner_generation is not None
        assert row.pool_owner_generation > admission.owner_generation
    await manager.stop()
    await engine.dispose()


@pytest.mark.anyio
async def test_explicit_principal_survives_restart_takeover_and_terminal_release(tmp_path):
    adapter = _adapter_with_binding(tmp_path)
    binding = adapter.resolve_binding(_context())
    session_id = "user-7-analysis-restart-principal"
    admission = _acquire_bound(
        adapter,
        binding,
        session_id,
        tenant_id="tenant-a",
        user_id="7",
    )
    engine = await _coordination_engine(
        tmp_path,
        session_id=session_id,
        run_id="hermes-main-principal",
        admission=admission,
        pool_tenant_id="tenant-a",
        pool_user_id="7",
    )
    statuses = [
        HermesRunStatus(run_id="hermes-main-principal", status="running", quiesced=False),
        HermesRunStatus(run_id="hermes-main-principal", status="completed", quiesced=True),
    ]

    async def sequence(_run_id, **_kwargs):
        return statuses.pop(0)

    manager = OpenShellPoolRecoveryManager(
        enabled=True,
        adapter=adapter,
        engine=engine,
        owner_id="recovery-owner",
        status_getter=sequence,
        poll_seconds=0.01,
    )
    report = await manager.start()
    assert report.monitoring == 1
    await _wait_for_monitors(manager)

    assert adapter.concurrency.status(project_root=tmp_path)["active_leases"] == 0
    async with AsyncSession(engine) as session:
        row = await session.get(ActiveRunLease, 1)
        assert row is not None and row.status == "completed"
        assert (row.pool_tenant_id, row.pool_user_id) == ("tenant-a", "7")
        assert row.pool_owner_generation is not None
        assert row.pool_owner_generation > admission.owner_generation
    await manager.stop()
    await engine.dispose()


@pytest.mark.anyio
async def test_partial_durable_principal_is_skipped_without_pool_takeover(tmp_path):
    adapter = _adapter_with_binding(tmp_path)
    binding = adapter.resolve_binding(_context())
    session_id = "user-7-analysis-restart-partial"
    admission = _acquire_bound(
        adapter,
        binding,
        session_id,
        tenant_id="tenant-a",
        user_id="7",
    )
    engine = await _coordination_engine(
        tmp_path,
        session_id=session_id,
        run_id="hermes-main-partial",
        admission=admission,
        pool_tenant_id="tenant-a",
    )
    manager = OpenShellPoolRecoveryManager(
        enabled=True,
        adapter=adapter,
        engine=engine,
        owner_id="recovery-owner",
    )

    report = await manager.start()

    assert report.skipped == 1
    assert adapter.concurrency.status(project_root=tmp_path)["active_leases"] == 1
    assert adapter.heartbeat(
        binding,
        session_id=session_id,
        tenant_id="tenant-a",
        user_id="7",
        owner_token=admission.owner_token,
        owner_generation=admission.owner_generation,
    ).status == "active"
    await manager.stop()
    assert adapter.release(
        session_id=session_id,
        tenant_id="tenant-a",
        user_id="7",
        owner_token=admission.owner_token,
        owner_generation=admission.owner_generation,
        terminal_confirmed=True,
    )
    await engine.dispose()


@pytest.mark.anyio
async def test_wrong_complete_principal_is_skipped_without_releasing_exact_owner(tmp_path):
    adapter = _adapter_with_binding(tmp_path)
    binding = adapter.resolve_binding(_context())
    session_id = "user-7-analysis-restart-wrong-principal"
    admission = _acquire_bound(
        adapter,
        binding,
        session_id,
        tenant_id="tenant-a",
        user_id="7",
    )
    engine = await _coordination_engine(
        tmp_path,
        session_id=session_id,
        run_id="hermes-main-wrong-principal",
        admission=admission,
        pool_tenant_id="tenant-b",
        pool_user_id="7",
    )
    manager = OpenShellPoolRecoveryManager(
        enabled=True,
        adapter=adapter,
        engine=engine,
        owner_id="recovery-owner",
    )

    report = await manager.start()

    assert report.skipped == 1
    assert adapter.heartbeat(
        binding,
        session_id=session_id,
        tenant_id="tenant-a",
        user_id="7",
        owner_token=admission.owner_token,
        owner_generation=admission.owner_generation,
    ).status == "active"
    await manager.stop()
    assert adapter.release(
        session_id=session_id,
        tenant_id="tenant-a",
        user_id="7",
        owner_token=admission.owner_token,
        owner_generation=admission.owner_generation,
        terminal_confirmed=True,
    )
    await engine.dispose()


@pytest.mark.anyio
async def test_periodic_rescan_recovers_exact_durable_lease_created_after_start(tmp_path):
    adapter = _adapter_with_binding(tmp_path)
    binding = adapter.resolve_binding(_context())
    engine = await _empty_coordination_engine(tmp_path)

    async def running(run_id, **_kwargs):
        return HermesRunStatus(run_id=run_id, status="running", quiesced=False)

    manager = OpenShellPoolRecoveryManager(
        enabled=True,
        adapter=adapter,
        engine=engine,
        owner_id="recovery-owner",
        status_getter=running,
        poll_seconds=0.01,
        rescan_seconds=0.01,
    )
    report = await manager.start()
    assert report.candidates == 0
    assert recovery_ready() is True

    session_id = "user-9-analysis-late-durable"
    admission = _acquire_bound(
        adapter,
        binding,
        session_id,
        tenant_id="tenant-a",
        user_id="9",
    )
    async with AsyncSession(engine) as session:
        session.add(
            ActiveRunLease(
                profile="siq_analysis",
                session_id=session_id,
                run_id="hermes-main-late-durable",
                owner_id="pre-restart-owner",
                pool_lease_id=admission.lease_id,
                pool_scope_id=admission.scope_id,
                pool_binding_run_id=admission.run_id,
                pool_owner_generation=admission.owner_generation,
                pool_tenant_id="tenant-a",
                pool_user_id="9",
                lease_until=utcnow_naive(),
            )
        )
        await session.commit()

    for _ in range(200):
        async with AsyncSession(engine) as session:
            row = await session.get(ActiveRunLease, 1)
        if row is not None and row.owner_id == "recovery-owner" and manager._tasks:
            break
        await asyncio.sleep(0.01)
    else:
        raise AssertionError("periodic recovery did not discover durable lease")

    assert row is not None
    assert row.pool_owner_generation is not None
    assert row.pool_owner_generation > admission.owner_generation
    assert (row.pool_tenant_id, row.pool_user_id) == ("tenant-a", "9")
    with pytest.raises(
        adapter_module.OpenShellPoolAdapterError,
        match="openshell_pool_owner_mismatch",
    ):
        adapter.heartbeat(
            binding,
            session_id=session_id,
            tenant_id="tenant-a",
            user_id="9",
            owner_token=admission.owner_token,
            owner_generation=admission.owner_generation,
        )
    await manager.stop()
    await engine.dispose()


@pytest.mark.anyio
async def test_periodic_rescan_never_takes_over_current_process_user_lease(tmp_path):
    adapter = _adapter_with_binding(tmp_path)
    binding = adapter.resolve_binding(_context())
    engine = await _empty_coordination_engine(tmp_path)
    manager = OpenShellPoolRecoveryManager(
        enabled=True,
        adapter=adapter,
        engine=engine,
        owner_id="current-api-owner",
        rescan_seconds=0.01,
    )
    await manager.start()

    session_id = "user-10-analysis-current-api"
    admission = _acquire_bound(
        adapter,
        binding,
        session_id,
        tenant_id="tenant-a",
        user_id="10",
    )
    async with AsyncSession(engine) as session:
        session.add(
            ActiveRunLease(
                profile="siq_analysis",
                session_id=session_id,
                run_id="hermes-main-current-api",
                owner_id="current-api-owner",
                pool_lease_id=admission.lease_id,
                pool_scope_id=admission.scope_id,
                pool_binding_run_id=admission.run_id,
                pool_owner_generation=admission.owner_generation,
                pool_tenant_id="tenant-a",
                pool_user_id="10",
                lease_until=utcnow_naive(),
            )
        )
        await session.commit()

    await asyncio.sleep(0.05)

    heartbeat = adapter.heartbeat(
        binding,
        session_id=session_id,
        tenant_id="tenant-a",
        user_id="10",
        owner_token=admission.owner_token,
        owner_generation=admission.owner_generation,
    )
    assert heartbeat.owner_generation == admission.owner_generation
    assert not manager._tasks
    await manager.stop()
    assert adapter.release(
        session_id=session_id,
        tenant_id="tenant-a",
        user_id="10",
        owner_token=admission.owner_token,
        owner_generation=admission.owner_generation,
        terminal_confirmed=True,
    )
    await engine.dispose()


@pytest.mark.anyio
async def test_authority_task_death_immediately_fails_recovery_readiness(tmp_path):
    adapter = _adapter_with_binding(tmp_path)
    engine = await _empty_coordination_engine(tmp_path)
    manager = OpenShellPoolRecoveryManager(
        enabled=True,
        adapter=adapter,
        engine=engine,
        rescan_seconds=60,
    )
    await manager.start()
    assert recovery_ready() is True
    authority = manager._authority_task
    assert authority is not None

    authority.cancel()
    await asyncio.gather(authority, return_exceptions=True)
    await asyncio.sleep(0)

    assert manager.ready is False
    assert recovery_ready() is False
    await manager.stop()
    await engine.dispose()


@pytest.mark.anyio
async def test_monitor_exception_orphans_exact_lease_and_fails_readiness(
    tmp_path,
    monkeypatch,
):
    adapter = _adapter_with_binding(tmp_path)
    binding = adapter.resolve_binding(_context())
    session_id = "user-8-analysis-monitor-death"
    admission = _acquire_bound(
        adapter,
        binding,
        session_id,
        tenant_id="tenant-a",
        user_id="8",
    )
    engine = await _coordination_engine(
        tmp_path,
        session_id=session_id,
        run_id="hermes-main-monitor-death",
        admission=admission,
        pool_tenant_id="tenant-a",
        pool_user_id="8",
    )

    async def running(run_id, **_kwargs):
        return HermesRunStatus(run_id=run_id, status="running", quiesced=False)

    manager = OpenShellPoolRecoveryManager(
        enabled=True,
        adapter=adapter,
        engine=engine,
        owner_id="recovery-owner",
        status_getter=running,
        poll_seconds=0.01,
    )

    async def broken_renew(_recovered):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(manager, "_renew_db", broken_renew)
    report = await manager.start()
    assert report.monitoring == 1
    for _ in range(200):
        if manager._failed:
            break
        await asyncio.sleep(0.005)
    else:
        raise AssertionError("monitor failure did not fail recovery readiness")

    assert recovery_ready() is False
    assert adapter.concurrency.status(project_root=tmp_path)["orphaned_leases"] == 1
    await manager.stop()
    await engine.dispose()


@pytest.mark.anyio
async def test_initial_cancelled_run_waits_for_quiescence_but_remains_orphaned(tmp_path):
    adapter = _adapter_with_binding(tmp_path)
    binding = adapter.resolve_binding(_context())
    session_id = "user-1-analysis-restart-cancelled"
    admission = _acquire_bound(adapter, binding, session_id)
    engine = await _coordination_engine(
        tmp_path,
        session_id=session_id,
        run_id="hermes-main-cancelled",
        admission=admission,
    )
    statuses = [
        HermesRunStatus(run_id="hermes-main-cancelled", status="cancelled", quiesced=False),
        HermesRunStatus(run_id="hermes-main-cancelled", status="cancelled", quiesced=True),
    ]

    async def sequence(_run_id, **_kwargs):
        return statuses.pop(0)

    manager = OpenShellPoolRecoveryManager(
        enabled=True,
        adapter=adapter,
        engine=engine,
        owner_id="recovery-owner",
        status_getter=sequence,
        poll_seconds=0.01,
    )
    report = await manager.start()
    assert report.monitoring == 1
    await _wait_for_monitors(manager)
    scheduler = adapter.concurrency.status(project_root=tmp_path)
    assert scheduler["orphaned_leases"] == 1
    assert scheduler["active_leases"] == 0
    await manager.stop()
    await engine.dispose()


@pytest.mark.anyio
async def test_unknown_404_run_identity_is_kept_orphaned(tmp_path):
    adapter = _adapter_with_binding(tmp_path)
    binding = adapter.resolve_binding(_context())
    session_id = "user-1-analysis-restart-missing"
    admission = _acquire_bound(adapter, binding, session_id)
    engine = await _coordination_engine(
        tmp_path,
        session_id=session_id,
        run_id="hermes-main-missing",
        admission=admission,
    )

    async def missing(run_id, **_kwargs):
        request = httpx.Request("GET", f"http://sandbox.invalid/v1/runs/{run_id}")
        response = httpx.Response(404, request=request)
        raise httpx.HTTPStatusError("not found", request=request, response=response)

    manager = OpenShellPoolRecoveryManager(
        enabled=True,
        adapter=adapter,
        engine=engine,
        owner_id="recovery-owner",
        status_getter=missing,
    )
    report = await manager.start()
    assert report.orphaned == 1
    assert adapter.concurrency.status(project_root=tmp_path)["orphaned_leases"] == 1
    await manager.stop()
    await engine.dispose()


@pytest.mark.anyio
@pytest.mark.parametrize("lost_fence", ["database", "pool"])
async def test_owner_fence_loss_immediately_orphans_recovered_writer(
    tmp_path,
    monkeypatch,
    lost_fence,
):
    adapter = _adapter_with_binding(tmp_path)
    binding = adapter.resolve_binding(_context())
    session_id = "user-1-analysis-restart-heartbeat"
    admission = _acquire_bound(adapter, binding, session_id)
    engine = await _coordination_engine(
        tmp_path,
        session_id=session_id,
        run_id="hermes-main-heartbeat",
        admission=admission,
    )

    async def running(run_id, **_kwargs):
        return HermesRunStatus(run_id=run_id, status="running", quiesced=False)

    async def failed_heartbeat(*_args, **_kwargs):
        raise adapter_module.OpenShellPoolAdapterError("openshell_pool_owner_mismatch")

    manager = OpenShellPoolRecoveryManager(
        enabled=True,
        adapter=adapter,
        engine=engine,
        owner_id="recovery-owner",
        status_getter=running,
        poll_seconds=0.01,
    )
    if lost_fence == "pool":
        monkeypatch.setattr(adapter, "heartbeat_async", failed_heartbeat)
    else:
        async def failed_renew(_recovered):
            return False

        monkeypatch.setattr(manager, "_renew_db", failed_renew)
    report = await manager.start()
    assert report.monitoring == 1
    await _wait_for_monitors(manager)
    assert adapter.concurrency.status(project_root=tmp_path)["orphaned_leases"] == 1
    await manager.stop()
    await engine.dispose()


@pytest.mark.anyio
async def test_only_one_recovery_process_can_hold_registry_capability(tmp_path):
    adapter = _adapter_with_binding(tmp_path)
    engine = await _coordination_engine(
        tmp_path,
        session_id="unmatched-session",
        run_id="unmatched-run",
    )
    first = OpenShellPoolRecoveryManager(enabled=True, adapter=adapter, engine=engine)
    second = OpenShellPoolRecoveryManager(enabled=True, adapter=adapter, engine=engine)

    first_report = await first.start()
    second_report = await second.start()

    assert first_report.lock_acquired is True
    assert second_report.lock_acquired is False
    await second.stop()
    await first.stop()
    await engine.dispose()


def test_recovery_feature_flag_requires_formal_api_port(monkeypatch):
    monkeypatch.setenv("SIQ_OPENSHELL_POOL_RECOVERY_ENABLED", "1")
    monkeypatch.setenv("SIQ_BACKEND_PORT", "18082")
    assert recovery_enabled() is False
    monkeypatch.setenv("SIQ_BACKEND_PORT", "18081")
    assert recovery_enabled() is True


def test_takeover_rejects_identity_only_without_lifecycle_lock_capability(tmp_path):
    adapter = _adapter_with_binding(tmp_path)
    binding = adapter.resolve_binding(_context())
    original = adapter.acquire(binding, session_id="session-no-capability")

    with pytest.raises(
        adapter_module.OpenShellPoolAdapterError,
        match="openshell_pool_recovery_capability_invalid",
    ):
        adapter.takeover_recovery(
            session_id="session-no-capability",
            recovery_lock_fd=-1,
        )
    assert (
        adapter.heartbeat(
            binding,
            session_id="session-no-capability",
            owner_token=original.owner_token,
            owner_generation=original.owner_generation,
        ).status
        == "active"
    )


def test_takeover_rejects_stale_owner_generation_without_rotating_current_owner(tmp_path):
    adapter = _adapter_with_binding(tmp_path)
    binding = adapter.resolve_binding(_context())
    session_id = "session-stale-recovery-generation"
    original = adapter.acquire(binding, session_id=session_id)
    manager = OpenShellPoolRecoveryManager(enabled=True, adapter=adapter)
    assert manager._acquire_process_lock() is True
    try:
        with pytest.raises(
            adapter_module.OpenShellPoolAdapterError,
            match="openshell_pool_recovery_identity_conflict",
        ):
            adapter.takeover_recovery(
                session_id=session_id,
                expected_lease_id=original.lease_id,
                expected_scope_id=original.scope_id,
                expected_run_id=original.run_id,
                expected_owner_generation=original.owner_generation + 1,
                recovery_lock_fd=manager._lock_fd,
            )
        assert adapter.heartbeat(
            binding,
            session_id=session_id,
            owner_token=original.owner_token,
            owner_generation=original.owner_generation,
        ).owner_generation == original.owner_generation

        recovered = adapter.takeover_recovery(
            session_id=session_id,
            expected_lease_id=original.lease_id,
            expected_scope_id=original.scope_id,
            expected_run_id=original.run_id,
            expected_owner_generation=original.owner_generation,
            recovery_lock_fd=manager._lock_fd,
        )
        assert recovered.admission.owner_generation > original.owner_generation
        assert recovered.admission.lease_id == original.lease_id
        assert recovered.admission.session_namespace == original.session_namespace
        assert recovered.admission.session_namespace.endswith(
            f":l{original.lease_id.removeprefix('lease-')}"
        )
        with pytest.raises(
            adapter_module.OpenShellPoolAdapterError,
            match="openshell_pool_owner_mismatch",
        ):
            adapter.heartbeat(
                binding,
                session_id=session_id,
                owner_token=original.owner_token,
                owner_generation=original.owner_generation,
            )
    finally:
        manager._release_process_lock()


def test_takeover_rejects_wrong_principal_without_rotating_current_owner(tmp_path):
    adapter = _adapter_with_binding(tmp_path)
    binding = adapter.resolve_binding(_context())
    session_id = "user-7-analysis-wrong-principal"
    original = adapter.acquire(
        binding,
        session_id=session_id,
        tenant_id="tenant-a",
        user_id="7",
    )
    manager = OpenShellPoolRecoveryManager(enabled=True, adapter=adapter)
    assert manager._acquire_process_lock() is True
    try:
        with pytest.raises(
            adapter_module.OpenShellPoolAdapterError,
            match="openshell_pool_recovery_lease_not_found",
        ):
            adapter.takeover_recovery(
                session_id=session_id,
                tenant_id="tenant-a",
                user_id="8",
                expected_lease_id=original.lease_id,
                expected_scope_id=original.scope_id,
                expected_run_id=original.run_id,
                expected_owner_generation=original.owner_generation,
                recovery_lock_fd=manager._lock_fd,
            )
        current = adapter.heartbeat(
            binding,
            session_id=session_id,
            tenant_id="tenant-a",
            user_id="7",
            owner_token=original.owner_token,
            owner_generation=original.owner_generation,
        )
        assert current.owner_generation == original.owner_generation
    finally:
        manager._release_process_lock()


def test_host_route_requires_pool_drain_and_unregister(tmp_path, monkeypatch):
    adapter = _adapter_with_binding(tmp_path)
    monkeypatch.setattr(hermes_client, "_project_root", lambda: tmp_path)
    monkeypatch.setattr(hermes_client, "normalize_runtime_target", lambda *_args, **_kwargs: "host")

    with pytest.raises(
        hermes_client.HermesRuntimeSelectionError,
        match="openshell_pool_host_rollback_requires_unregister",
    ):
        hermes_client.resolve_requested_run_route(
            "siq_analysis",
            None,
            session_id="session-host",
            context=_context(),
        )

    adapter.registry.unregister(
        market=binding.market if (binding := adapter.resolve_binding(_context())) else "cn",
        company=binding.company,
        run_id=binding.run_id,
        project_root=tmp_path,
    )
    assert (
        hermes_client.resolve_requested_run_route(
            "siq_analysis",
            None,
            session_id="session-host",
            context=_context(),
        )
        is None
    )
