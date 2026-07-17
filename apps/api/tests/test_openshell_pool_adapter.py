from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.openshell.siq_analysis_lifecycle import _write_json
from scripts.openshell.tests.test_siq_analysis_pool_registry import _binding, _company, _register
from services import openshell_pool_adapter as adapter_module


def _context(company: str = "600104-上汽集团", *, market: str = "CN") -> dict:
    code, _, name = company.partition("-")
    return {
        "company": {
            "market": market,
            "dir": company,
            "code": code,
            "name": name,
        },
        "research_identity": {
            "market": market,
            "company_id": f"{market}:{code}",
        },
    }


def _adapter_with_binding(tmp_path: Path) -> adapter_module.OpenShellPoolAdapter:
    active = _binding(
        tmp_path,
        market="cn",
        company="600104-上汽集团",
        run_id="canary-111111111111",
    )
    _register(tmp_path, active=active, local_port=28651)
    return adapter_module.OpenShellPoolAdapter(project_root=tmp_path)


def test_resolve_binding_uniquely_matches_canonical_company_and_redacts_key(tmp_path: Path) -> None:
    adapter = _adapter_with_binding(tmp_path)
    _company(tmp_path, "cn", "600519-贵州茅台")

    binding = adapter.resolve_binding(_context())
    host = adapter.resolve_binding(_context("600519-贵州茅台"))
    incomplete = adapter.resolve_binding({"research_identity": {"market": "CN"}})

    assert binding.target == "openshell"
    assert binding.market == "cn"
    assert binding.company == "600104-上汽集团"
    assert binding.run_id == "canary-111111111111"
    assert binding.api_key == "a" * 64
    assert binding.api_key not in repr(binding)
    assert host.target == "host"
    assert (host.market, host.company) == ("cn", "600519-贵州茅台")
    assert incomplete == adapter_module.ResolvedPoolBinding(target="host")


def test_context_conflicts_and_non_unique_code_fail_closed(tmp_path: Path) -> None:
    _company(tmp_path, "cn", "600104-甲")
    _company(tmp_path, "cn", "600104-乙")
    for index, company in enumerate(("600104-甲", "600104-乙"), start=1):
        run_id = f"canary-{index:012x}"
        plan = adapter_module._load_pool_modules()[0].plan_slot(
            market="cn",
            company=company,
            run_id=run_id,
            local_port=28651 + index,
            project_root=tmp_path,
        )
        active = _binding(
            tmp_path,
            market="cn",
            company=company,
            run_id=run_id,
            active_relative=Path(plan.active_relative),
            key=("a" if index == 1 else "b") * 64,
        )
        _register(tmp_path, active=active, local_port=28651 + index)
    adapter = adapter_module.OpenShellPoolAdapter(project_root=tmp_path)

    with pytest.raises(adapter_module.OpenShellPoolAdapterError, match="openshell_pool_context_ambiguous"):
        adapter.resolve_binding(
            {
                "company": {"market": "CN", "code": "600104"},
                "research_identity": {"market": "CN", "company_id": "CN:600104"},
            }
        )
    with pytest.raises(adapter_module.OpenShellPoolAdapterError, match="openshell_pool_context_company_conflict"):
        adapter.resolve_binding(
            {
                "company": {
                    "market": "CN",
                    "dir": "600104-甲",
                    "code": "600104",
                    "name": "乙",
                }
            }
        )


def test_sync_acquire_heartbeat_and_terminal_release_preserve_affinity(tmp_path: Path) -> None:
    adapter = _adapter_with_binding(tmp_path)
    binding = adapter.resolve_binding(_context())

    first = adapter.acquire(binding, session_id="user-1-analysis-session")
    first = adapter.mark_run_bound(
        binding,
        session_id="user-1-analysis-session",
        owner_token=first.owner_token,
        owner_generation=first.owner_generation,
    )
    first_heartbeat = adapter.heartbeat(
        binding,
        session_id="user-1-analysis-session",
        owner_token=first.owner_token,
        owner_generation=first.owner_generation,
    )
    second = adapter.acquire(binding, session_id="user-2-analysis-session")

    assert first.status == first_heartbeat.status == "active"
    assert first.session_namespace == first_heartbeat.session_namespace
    assert second.status == "queued"
    assert first.api_key not in repr(first)
    scheduler_text = (tmp_path / adapter.concurrency.SCHEDULER_RELATIVE).read_text()
    assert "user-1-analysis-session" not in scheduler_text
    assert adapter.release(
        session_id="user-1-analysis-session",
        owner_token=first.owner_token,
        owner_generation=first.owner_generation,
    ) is False
    assert adapter.concurrency.status(project_root=tmp_path)["orphaned_leases"] == 1
    assert adapter.release(
        session_id="user-1-analysis-session",
        owner_token=first.owner_token,
        owner_generation=first.owner_generation,
        terminal_confirmed=True,
    ) is True
    promoted = adapter.heartbeat(
        binding,
        session_id="user-2-analysis-session",
        owner_token=second.owner_token,
        owner_generation=second.owner_generation,
    )
    assert promoted.status == "active"


def test_authenticated_users_share_company_fifo_and_release_with_same_principal(tmp_path: Path) -> None:
    adapter = _adapter_with_binding(tmp_path)
    binding = adapter.resolve_binding(_context())
    first_principal = {"tenant_id": "default", "user_id": "101"}
    second_principal = {"tenant_id": "default", "user_id": "202"}

    first = adapter.acquire(
        binding,
        session_id="user-101-analysis-first",
        **first_principal,
    )
    second = adapter.acquire(
        binding,
        session_id="user-202-analysis-second",
        **second_principal,
    )

    assert first.status == "active"
    assert second.status == "queued"
    assert second.queue_position == 1
    with pytest.raises(
        adapter_module.OpenShellPoolAdapterError,
        match="openshell_pool_lease_not_found",
    ):
        adapter.heartbeat(
            binding,
            session_id="user-101-analysis-first",
            tenant_id="default",
            user_id="202",
            owner_token=first.owner_token,
            owner_generation=first.owner_generation,
        )
    assert adapter.release(
        session_id="user-101-analysis-first",
        tenant_id="default",
        user_id="202",
        owner_token=first.owner_token,
        owner_generation=first.owner_generation,
        terminal_confirmed=True,
    ) is False
    assert adapter.concurrency.status(project_root=tmp_path)["active_leases"] == 1
    assert adapter.release(
        session_id="user-101-analysis-first",
        owner_token=first.owner_token,
        owner_generation=first.owner_generation,
        terminal_confirmed=True,
        **first_principal,
    ) is True
    promoted = adapter.heartbeat(
        binding,
        session_id="user-202-analysis-second",
        owner_token=second.owner_token,
        owner_generation=second.owner_generation,
        **second_principal,
    )
    assert promoted.status == "active"
    assert adapter.release(
        session_id="user-202-analysis-second",
        owner_token=promoted.owner_token,
        owner_generation=promoted.owner_generation,
        terminal_confirmed=True,
        **second_principal,
    ) is True


def test_async_acquire_wait_promotes_after_terminal_and_cleans_timeout(tmp_path: Path) -> None:
    adapter = _adapter_with_binding(tmp_path)
    binding = adapter.resolve_binding(_context())
    first = adapter.acquire(binding, session_id="session-first")
    assert first.status == "active"

    async def exercise() -> None:
        async def finish_first() -> None:
            await asyncio.sleep(0.03)
            assert await adapter.release_async(
                session_id="session-first",
                owner_token=first.owner_token,
                owner_generation=first.owner_generation,
                terminal_confirmed=True,
            ) is True

        finisher = asyncio.create_task(finish_first())
        promoted = await adapter.acquire_wait_async(
            binding,
            session_id="session-second",
            timeout_seconds=0.5,
            poll_interval=0.01,
        )
        await finisher
        assert promoted.status == "active"

        with pytest.raises(adapter_module.OpenShellPoolAdapterError, match="openshell_pool_wait_timeout"):
            await adapter.acquire_wait_async(
                binding,
                session_id="session-third",
                timeout_seconds=0.03,
                poll_interval=0.01,
            )
        status = adapter.concurrency.status(project_root=tmp_path)
        assert status["waiting_leases"] == 0
        assert await adapter.release_async(
            session_id="session-second",
            owner_token=promoted.owner_token,
            owner_generation=promoted.owner_generation,
            terminal_confirmed=True,
        ) is True

    asyncio.run(exercise())


def test_binding_change_or_corruption_returns_stable_secret_free_error(tmp_path: Path) -> None:
    adapter = _adapter_with_binding(tmp_path)
    binding = adapter.resolve_binding(_context())
    registry = adapter.registry.load_registry(project_root=tmp_path)
    manifest_path = tmp_path / registry["bindings"][0]["manifest"]
    manifest = json.loads(manifest_path.read_text())
    manifest["phase"] = "stopping"
    _write_json(manifest_path, manifest, root=tmp_path)

    with pytest.raises(adapter_module.OpenShellPoolAdapterError) as error:
        adapter.acquire(binding, session_id="session-stale")
    assert error.value.code == "openshell_pool_manifest_invalid"
    assert binding.api_key not in str(error.value)


def test_async_wait_cancellation_releases_undelivered_admission(tmp_path: Path) -> None:
    adapter = _adapter_with_binding(tmp_path)
    binding = adapter.resolve_binding(_context())
    holder = adapter.acquire(binding, session_id="session-holder")

    async def exercise() -> None:
        waiting = asyncio.create_task(
            adapter.acquire_wait_async(
                binding,
                session_id="session-cancelled",
                timeout_seconds=1.0,
                poll_interval=0.01,
            )
        )
        for _ in range(100):
            if adapter.concurrency.status(project_root=tmp_path)["waiting_leases"] == 1:
                break
            await asyncio.sleep(0.005)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        assert adapter.concurrency.status(project_root=tmp_path)["waiting_leases"] == 0
        assert await adapter.release_async(
            session_id="session-holder",
            owner_token=holder.owner_token,
            owner_generation=holder.owner_generation,
            terminal_confirmed=True,
        ) is True

    asyncio.run(exercise())


def test_timeout_after_promotion_removes_unbound_writer_without_orphan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _adapter_with_binding(tmp_path)
    binding = adapter.resolve_binding(_context())
    holder = adapter.acquire(binding, session_id="promotion-holder")
    holder = adapter.mark_run_bound(
        binding,
        session_id="promotion-holder",
        owner_token=holder.owner_token,
        owner_generation=holder.owner_generation,
    )

    async def promote_then_timeout(
        bound_binding,
        *,
        session_id,
        owner_token,
        owner_generation,
        **_kwargs,
    ):
        assert await adapter.release_async(
            session_id="promotion-holder",
            owner_token=holder.owner_token,
            owner_generation=holder.owner_generation,
            terminal_confirmed=True,
        )
        promoted = await adapter.heartbeat_async(
            bound_binding,
            session_id=session_id,
            owner_token=owner_token,
            owner_generation=owner_generation,
        )
        assert promoted.status == "active"
        assert promoted.run_bound is False
        raise adapter_module.OpenShellPoolAdapterError(
            "openshell_pool_wait_timeout",
            retryable=True,
        )

    monkeypatch.setattr(adapter, "wait_async", promote_then_timeout)

    async def exercise() -> None:
        with pytest.raises(adapter_module.OpenShellPoolAdapterError, match="openshell_pool_wait_timeout"):
            await adapter.acquire_wait_async(
                binding,
                session_id="promotion-timeout",
                timeout_seconds=1,
                poll_interval=0.01,
            )

    asyncio.run(exercise())
    status = adapter.concurrency.status(project_root=tmp_path)
    assert status["active_leases"] == 0
    assert status["orphaned_leases"] == 0
    assert status["waiting_leases"] == 0


def test_owner_fencing_survives_adapter_restart_and_reacquire(tmp_path: Path) -> None:
    first_adapter = _adapter_with_binding(tmp_path)
    binding = first_adapter.resolve_binding(_context())
    first = first_adapter.acquire(binding, session_id="restart-session")

    restarted_adapter = adapter_module.OpenShellPoolAdapter(project_root=tmp_path)
    resumed = restarted_adapter.heartbeat(
        binding,
        session_id="restart-session",
        owner_token=first.owner_token,
        owner_generation=first.owner_generation,
    )
    assert resumed.status == "active"
    assert restarted_adapter.release(
        session_id="restart-session",
        owner_token=first.owner_token,
        owner_generation=first.owner_generation,
        terminal_confirmed=True,
    ) is True

    replacement = restarted_adapter.acquire(binding, session_id="restart-session")
    assert replacement.lease_id == first.lease_id
    assert replacement.owner_generation > first.owner_generation
    assert replacement.owner_token != first.owner_token
    with pytest.raises(adapter_module.OpenShellPoolAdapterError, match="openshell_pool_owner_mismatch"):
        restarted_adapter.release(
            session_id="restart-session",
            owner_token=first.owner_token,
            owner_generation=first.owner_generation,
            terminal_confirmed=True,
        )
    with pytest.raises(adapter_module.OpenShellPoolAdapterError, match="openshell_pool_owner_mismatch"):
        restarted_adapter.mark_run_bound(
            binding,
            session_id="restart-session",
            owner_token=first.owner_token,
            owner_generation=first.owner_generation,
        )
    assert restarted_adapter.concurrency.status(project_root=tmp_path)["active_leases"] == 1
    replacement = restarted_adapter.mark_run_bound(
        binding,
        session_id="restart-session",
        owner_token=replacement.owner_token,
        owner_generation=replacement.owner_generation,
    )
    assert replacement.run_bound is True
    assert restarted_adapter.release(
        session_id="restart-session",
        owner_token=replacement.owner_token,
        owner_generation=replacement.owner_generation,
        terminal_confirmed=True,
    ) is True


def test_pool_modules_load_from_api_cwd_without_trusting_cwd_package(tmp_path: Path) -> None:
    del tmp_path
    project_root = Path(__file__).resolve().parents[3]
    api_root = project_root / "apps/api"
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(api_root)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from services.openshell_pool_adapter import _load_pool_modules;"
                "print(','.join(module.__name__ for module in _load_pool_modules()))"
            ),
        ],
        cwd=api_root,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ",".join(adapter_module._POOL_MODULE_NAMES)
