from __future__ import annotations

import hashlib
import json
import re
import stat
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path

import pytest

from scripts.openshell import (
    siq_analysis_canary as canary,
    siq_analysis_pool_concurrency as concurrency,
    siq_analysis_pool_registry as pool,
)
from scripts.openshell.siq_analysis_canary import (
    MODE as CANARY_MODE,
    SANDBOX_PREFIX,
    SCHEMA_VERSION as CANARY_SCHEMA_VERSION,
)
from scripts.openshell.siq_analysis_lifecycle import (
    CANARY_LIFECYCLE_LABEL,
    MARKET_ROOTS,
    LifecycleAdapter,
    _write_json,
)
from scripts.openshell.siq_analysis_wide_pilot import KNOWN_FORMAL_BLOCKERS_NOT_BYPASSED, PROVIDERS


def _private_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_bytes(content)
    path.chmod(0o600)


def _company(root: Path, market: str, company: str) -> Path:
    company_root = root / MARKET_ROOTS[market] / company
    analysis = company_root / "analysis"
    analysis.mkdir(mode=0o700, parents=True, exist_ok=True)
    _private_bytes(company_root / "company.json", b"{}\n")
    return analysis


def _admit_company_from_process(arguments: tuple[str, int]) -> tuple[str, int, str]:
    root_value, index = arguments
    admission = concurrency.acquire(
        market="cn",
        company="600104-上汽集团",
        tenant_id="tenant-a",
        user_id=f"process-user-{index}",
        session_id=f"process-session-{index}",
        now=100,
        project_root=Path(root_value),
    )
    return admission.status, admission.queue_position, admission.lease_id


def _mount_plan(root: Path, *, analysis: Path, run_id: str) -> tuple[dict, str, Path]:
    snapshot = root / pool.RUNTIME_SNAPSHOT_RELATIVE / run_id
    runtime_targets = {
        "runtime-state": "/sandbox/siq-analysis-runtime-state",
        "sessions": str(root / "data/hermes/home/profiles/siq_analysis/sessions"),
        "checkpoints": str(root / "data/hermes/home/profiles/siq_analysis/checkpoints"),
        "cron": str(root / "data/hermes/home/profiles/siq_analysis/cron"),
        "memories": str(root / "data/hermes/home/profiles/siq_analysis/memories"),
    }
    for name in runtime_targets:
        (snapshot / name).mkdir(mode=0o700, parents=True)
    mounts = [
        {
            "type": "bind",
            "source": str(root / "data/wiki"),
            "target": str(root / "data/wiki"),
            "read_only": True,
        },
        {
            "type": "bind",
            "source": str(analysis),
            "target": str(analysis),
            "read_only": False,
        },
    ]
    mounts.extend(
        {
            "type": "bind",
            "source": str(snapshot / name),
            "target": target,
            "read_only": False,
        }
        for name, target in runtime_targets.items()
    )
    value = {"docker": {"mounts": mounts}}
    content = (json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode()
    digest = hashlib.sha256(content).hexdigest()
    path = root / pool.MOUNT_PLAN_RELATIVE / f"{digest}.driver-config.json"
    _private_bytes(path, content)
    return value, digest, path


def _policy(root: Path, *, analysis: Path, extra_project_write: Path | None = None) -> tuple[Path, str]:
    profile = root / "data/hermes/home/profiles/siq_analysis"
    read_write = [
        "/dev/null",
        *(
            str(profile / name)
            for name in ("cache", "checkpoints", "cron", "logs", "memories", "sessions", "workspace")
        ),
        str(analysis),
        "/sandbox",
        "/tmp",
    ]
    if extra_project_write is not None:
        read_write.append(str(extra_project_write))
    value = {
        "filesystem_policy": {
            "include_workdir": False,
            "read_only": [str(root)],
            "read_write": read_write,
        },
        "network_policies": {},
    }
    path = analysis / ".policy-fixture.json"
    _write_json(path, value, root=root)
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def _binding(
    root: Path,
    *,
    market: str,
    company: str,
    run_id: str,
    active_relative: Path = pool.LEGACY_ACTIVE_RELATIVE,
    key: str = "a" * 64,
    extra_project_write: Path | None = None,
) -> Path:
    analysis = _company(root, market, company)
    _plan, mount_digest, mount_path = _mount_plan(root, analysis=analysis, run_id=run_id)
    state_root = active_relative.parent
    run_state = state_root / "runs" / run_id
    policy_path, policy_sha256 = _policy(
        root,
        analysis=analysis,
        extra_project_write=extra_project_write,
    )
    # A real policy lives in the run state. Move the fixture there before its
    # digest is bound into the manifest.
    policy_relative = run_state / "task-policy.yaml"
    _private_bytes(root / policy_relative, policy_path.read_bytes())
    policy_sha256 = hashlib.sha256((root / policy_relative).read_bytes()).hexdigest()
    policy_path.unlink()
    key_sha256 = hashlib.sha256(key.encode("ascii")).hexdigest()
    digest = "1" * 64
    manifest = {
        "schema_version": CANARY_SCHEMA_VERSION,
        "mode": CANARY_MODE,
        "readiness_effect": "none",
        "phase": "running",
        "profile": "siq_analysis",
        "run_id": run_id,
        "market": market,
        "company": company,
        "analysis_relative_path": analysis.relative_to(root).as_posix(),
        "writable_relative_path": analysis.relative_to(root).as_posix(),
        "write_scope": "current_company_analysis_root",
        "normal_business_mutations": ["create", "modify", "rename", "delete"],
        "source_sha256": digest,
        "source_stock_code": company.partition("-")[0],
        "sandbox_name": f"{SANDBOX_PREFIX}{run_id}",
        "lifecycle_label": CANARY_LIFECYCLE_LABEL,
        "image_ref": "siq/hermes:test",
        "image_id": f"sha256:{digest}",
        "runtime_snapshot": (pool.RUNTIME_SNAPSHOT_RELATIVE / run_id).as_posix(),
        "mount_plan": mount_path.relative_to(root).as_posix(),
        "mount_plan_sha256": mount_digest,
        "mount_count": 7,
        "policy": policy_relative.as_posix(),
        "policy_sha256": policy_sha256,
        "providers": list(PROVIDERS),
        "formal_blockers_not_overridden": list(KNOWN_FORMAL_BLOCKERS_NOT_BYPASSED),
        "broker_request_identity_required": True,
        "api_key_sha256": key_sha256,
        "run_nonce_sha256": "2" * 64,
        "host_hermes_receipt_sha256": "3" * 64,
        "sandbox_id": f"sandbox-{run_id}",
        "container_id": "4" * 64,
        "guard_process": "guard.process.json",
        "forward_process": "forward.process.json",
        "result_is_formal_evidence": False,
    }
    manifest_relative = run_state / "canary.json"
    _write_json(root / manifest_relative, manifest, root=root)
    manifest_sha256 = hashlib.sha256((root / manifest_relative).read_bytes()).hexdigest()
    _private_bytes(root / run_state / "api.key", (key + "\n").encode("ascii"))
    active = {
        "schema_version": CANARY_SCHEMA_VERSION,
        "mode": CANARY_MODE,
        "readiness_effect": "none",
        "profile": "siq_analysis",
        "run_id": run_id,
        "market": market,
        "company": company,
        "run_state": run_state.as_posix(),
        "manifest": manifest_relative.as_posix(),
        "manifest_sha256": manifest_sha256,
        "api_key_sha256": key_sha256,
    }
    # pathlib creates intermediate parents using the process umask. The real
    # lifecycle makes both the state root and pool root owner-only.
    (root / state_root).chmod(0o700)
    pool_root = root / pool.POOL_RELATIVE
    if pool_root.exists():
        pool_root.chmod(0o700)
    _write_json(root / active_relative, active, root=root)
    return active_relative


def _register(root: Path, *, active: Path, local_port: int) -> dict:
    reservation_token = None
    if local_port != pool.TARGET_PORT:
        reservation = pool.allocate_local_port(
            project_root=root,
            first=local_port,
            last=local_port,
            now=10,
            available=lambda _host, _port: True,
        )
        reservation_token = reservation.reservation_token
    return pool.register_active(
        active=active,
        local_port=local_port,
        reservation_token=reservation_token,
        now=10,
        project_root=root,
    )


def test_register_and_resolve_exact_scope_without_persisting_key(tmp_path: Path) -> None:
    active = _binding(
        tmp_path,
        market="cn",
        company="600104-上汽集团",
        run_id="canary-111111111111",
    )
    _company(tmp_path, "cn", "600519-贵州茅台")

    registry = pool.register_active(active=active, local_port=28651, project_root=tmp_path)
    route = pool.resolve(market="cn", company="600104-上汽集团", project_root=tmp_path)
    unmatched = pool.resolve(market="cn", company="600519-贵州茅台", project_root=tmp_path)

    assert registry["generation"] == 1
    assert route.target == "openshell"
    assert route.base == "http://127.0.0.1:28651/v1/runs"
    assert route.api_key == "a" * 64
    assert route.analysis_relative_path == "data/wiki/companies/600104-上汽集团/analysis"
    assert unmatched.target == "host"
    assert unmatched.api_key == ""
    assert route.api_key not in repr(route)
    registry_bytes = (tmp_path / pool.REGISTRY_RELATIVE).read_bytes()
    assert route.api_key.encode("ascii") not in registry_bytes
    assert stat.S_IMODE((tmp_path / pool.REGISTRY_RELATIVE).stat().st_mode) == 0o600


def test_two_slots_have_independent_ports_state_runtime_and_namespaces(tmp_path: Path) -> None:
    first_plan_seed = _company(tmp_path, "cn", "600104-上汽集团")
    del first_plan_seed
    second_plan_seed = _company(tmp_path, "cn", "600519-贵州茅台")
    del second_plan_seed
    first_plan = pool.plan_slot(
        market="cn",
        company="600104-上汽集团",
        run_id="canary-111111111111",
        local_port=28652,
        project_root=tmp_path,
    )
    second_plan = pool.plan_slot(
        market="cn",
        company="600519-贵州茅台",
        run_id="canary-222222222222",
        local_port=28653,
        project_root=tmp_path,
    )
    first_active = _binding(
        tmp_path,
        market="cn",
        company="600104-上汽集团",
        run_id=first_plan.run_id,
        active_relative=Path(first_plan.active_relative),
    )
    second_active = _binding(
        tmp_path,
        market="cn",
        company="600519-贵州茅台",
        run_id=second_plan.run_id,
        active_relative=Path(second_plan.active_relative),
        key="b" * 64,
    )

    _register(tmp_path, active=first_active, local_port=28652)
    registry = _register(tmp_path, active=second_active, local_port=28653)
    first = pool.resolve(market="cn", company="600104-上汽集团", project_root=tmp_path)
    second = pool.resolve(market="cn", company="600519-贵州茅台", project_root=tmp_path)

    assert len(registry["bindings"]) == 2
    assert first.base != second.base
    assert first.run_id != second.run_id
    assert first.session_namespace != second.session_namespace
    assert first.analysis_relative_path != second.analysis_relative_path
    assert {item["runtime_snapshot"] for item in registry["bindings"]} == {
        first_plan.runtime_snapshot,
        second_plan.runtime_snapshot,
    }


def test_cross_company_project_write_is_rejected(tmp_path: Path) -> None:
    other_analysis = _company(tmp_path, "cn", "600519-贵州茅台")
    active = _binding(
        tmp_path,
        market="cn",
        company="600104-上汽集团",
        run_id="canary-111111111111",
        extra_project_write=other_analysis,
    )

    with pytest.raises(pool.PoolRegistryError, match="openshell_pool_policy_scope_invalid"):
        pool.register_active(active=active, local_port=28651, project_root=tmp_path)


def test_registered_binding_becoming_stale_fails_closed(tmp_path: Path) -> None:
    active = _binding(
        tmp_path,
        market="cn",
        company="600104-上汽集团",
        run_id="canary-111111111111",
    )
    registry = pool.register_active(active=active, local_port=28651, project_root=tmp_path)
    manifest_path = tmp_path / registry["bindings"][0]["manifest"]
    manifest = json.loads(manifest_path.read_text())
    manifest["phase"] = "stopping"
    _write_json(manifest_path, manifest, root=tmp_path)

    with pytest.raises(pool.PoolRegistryError, match="openshell_pool_manifest_invalid"):
        pool.resolve(market="cn", company="600104-上汽集团", project_root=tmp_path)


def test_duplicate_local_port_is_rejected_and_allocator_skips_reserved(tmp_path: Path) -> None:
    _company(tmp_path, "cn", "600104-上汽集团")
    _company(tmp_path, "cn", "600519-贵州茅台")
    first_plan = pool.plan_slot(
        market="cn",
        company="600104-上汽集团",
        run_id="canary-111111111111",
        local_port=28652,
        project_root=tmp_path,
    )
    second_plan = pool.plan_slot(
        market="cn",
        company="600519-贵州茅台",
        run_id="canary-222222222222",
        local_port=28652,
        project_root=tmp_path,
    )
    first_active = _binding(
        tmp_path,
        market="cn",
        company="600104-上汽集团",
        run_id=first_plan.run_id,
        active_relative=Path(first_plan.active_relative),
    )
    second_active = _binding(
        tmp_path,
        market="cn",
        company="600519-贵州茅台",
        run_id=second_plan.run_id,
        active_relative=Path(second_plan.active_relative),
        key="b" * 64,
    )
    with pytest.raises(pool.PoolRegistryError, match="openshell_pool_port_reservation_required"):
        pool.register_active(active=first_active, local_port=28652, project_root=tmp_path)
    _register(tmp_path, active=first_active, local_port=28652)

    reservation = pool.allocate_local_port(
        project_root=tmp_path,
        first=28652,
        last=28654,
        now=20,
        available=lambda _host, _port: True,
    )
    assert reservation.local_port == 28653
    assert reservation.reservation_token not in repr(reservation)
    with pytest.raises(pool.PoolRegistryError, match="openshell_pool_local_port_conflict"):
        pool.register_active(active=second_active, local_port=28652, project_root=tmp_path)


def test_concurrent_port_allocation_creates_unique_durable_reservations(tmp_path: Path) -> None:
    def reserve(_index: int) -> pool.PortReservation:
        return pool.allocate_local_port(
            project_root=tmp_path,
            first=28652,
            last=28655,
            now=10,
            available=lambda _host, _port: True,
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        reservations = list(executor.map(reserve, range(4)))

    assert {item.local_port for item in reservations} == {28652, 28653, 28654, 28655}
    state = json.loads((tmp_path / pool.PORT_RESERVATIONS_RELATIVE).read_text())
    assert {item["local_port"] for item in state["reservations"]} == {28652, 28653, 28654, 28655}
    assert stat.S_IMODE((tmp_path / pool.PORT_RESERVATIONS_RELATIVE).stat().st_mode) == 0o600
    with pytest.raises(pool.PoolRegistryError, match="openshell_pool_no_port_available"):
        reserve(5)


def test_noncanonical_market_is_not_routed(tmp_path: Path) -> None:
    _company(tmp_path, "cn", "600104-上汽集团")
    with pytest.raises(pool.PoolRegistryError, match="openshell_pool_market_not_canonical"):
        pool.resolve(market="CN", company="600104-上汽集团", project_root=tmp_path)


def test_same_company_writers_are_fifo_queued_with_identity_isolation(tmp_path: Path) -> None:
    active = _binding(
        tmp_path,
        market="cn",
        company="600104-上汽集团",
        run_id="canary-111111111111",
    )
    pool.register_active(active=active, local_port=28651, project_root=tmp_path)
    _company(tmp_path, "cn", "600519-贵州茅台")

    first = concurrency.acquire(
        market="cn",
        company="600104-上汽集团",
        tenant_id="tenant-real-1",
        user_id="user-real-1",
        session_id="session-real-1",
        now=100,
        project_root=tmp_path,
    )
    with pytest.raises(concurrency.PoolConcurrencyError, match="openshell_pool_session_scope_conflict"):
        concurrency.acquire(
            market="cn",
            company="600519-贵州茅台",
            tenant_id="tenant-real-1",
            user_id="user-real-1",
            session_id="session-real-1",
            now=100,
            project_root=tmp_path,
        )
    second = concurrency.acquire(
        market="cn",
        company="600104-上汽集团",
        tenant_id="tenant-real-1",
        user_id="user-real-2",
        session_id="session-real-2",
        now=101,
        project_root=tmp_path,
    )

    assert first.status == "active"
    assert second.status == "queued"
    assert second.queue_position == 1
    assert first.write_relative_path != second.write_relative_path
    assert first.session_namespace.endswith(
        f":l{first.lease_id.removeprefix('lease-')}"
    )
    assert re.fullmatch(r".+:l[0-9a-f]{32}", first.session_namespace)
    assert all(
        plaintext not in first.session_namespace
        for plaintext in ("tenant-real-1", "user-real-1", "session-real-1")
    )
    state = (tmp_path / concurrency.SCHEDULER_RELATIVE).read_text()
    assert "tenant-real-1" not in state
    assert "user-real-1" not in state
    assert "session-real-1" not in state

    assert (
        concurrency.release(
            tenant_id="tenant-real-1",
            user_id="user-real-1",
            session_id="session-real-1",
            owner_token=first.owner_token,
            owner_generation=first.owner_generation,
            terminal_confirmed=True,
            now=102,
            project_root=tmp_path,
        )["released"]
        is True
    )
    promoted = concurrency.heartbeat(
        market="cn",
        company="600104-上汽集团",
        tenant_id="tenant-real-1",
        user_id="user-real-2",
        session_id="session-real-2",
        owner_token=second.owner_token,
        owner_generation=second.owner_generation,
        now=103,
        project_root=tmp_path,
    )
    assert promoted.status == "active"
    assert promoted.session_namespace != first.session_namespace
    assert promoted.session_namespace.endswith(
        f":l{promoted.lease_id.removeprefix('lease-')}"
    )
    assert promoted.api_key == "a" * 64
    assert promoted.api_key not in repr(promoted)


def test_same_user_sessions_receive_full_128_bit_anonymous_namespaces(tmp_path: Path) -> None:
    active = _binding(
        tmp_path,
        market="cn",
        company="600104-上汽集团",
        run_id="canary-111111111111",
    )
    pool.register_active(active=active, local_port=28651, project_root=tmp_path)

    first = concurrency.acquire(
        market="cn",
        company="600104-上汽集团",
        tenant_id="tenant-shared",
        user_id="user-shared",
        session_id="session-a",
        now=100,
        project_root=tmp_path,
    )
    waiting = concurrency.acquire(
        market="cn",
        company="600104-上汽集团",
        tenant_id="tenant-shared",
        user_id="user-shared",
        session_id="session-b",
        now=101,
        project_root=tmp_path,
    )
    assert concurrency.release(
        tenant_id="tenant-shared",
        user_id="user-shared",
        session_id="session-a",
        owner_token=first.owner_token,
        owner_generation=first.owner_generation,
        terminal_confirmed=True,
        now=102,
        project_root=tmp_path,
    )["released"] is True
    second = concurrency.heartbeat(
        market="cn",
        company="600104-上汽集团",
        tenant_id="tenant-shared",
        user_id="user-shared",
        session_id="session-b",
        owner_token=waiting.owner_token,
        owner_generation=waiting.owner_generation,
        now=103,
        project_root=tmp_path,
    )

    assert first.session_namespace != second.session_namespace
    for admission in (first, second):
        suffix = admission.session_namespace.rsplit(":l", 1)[1]
        assert suffix == admission.lease_id.removeprefix("lease-")
        assert re.fullmatch(r"[0-9a-f]{32}", suffix)
        assert all(
            plaintext not in admission.session_namespace
            for plaintext in ("tenant-shared", "user-shared", "session-a", "session-b")
        )


def test_same_session_reentry_never_renews_or_shares_active_owner(tmp_path: Path) -> None:
    active = _binding(
        tmp_path,
        market="cn",
        company="600104-上汽集团",
        run_id="canary-111111111111",
    )
    pool.register_active(active=active, local_port=28651, project_root=tmp_path)
    admitted = concurrency.acquire(
        market="cn",
        company="600104-上汽集团",
        tenant_id="tenant-a",
        user_id="user-a",
        session_id="session-a",
        now=100,
        project_root=tmp_path,
    )
    before = json.loads((tmp_path / concurrency.SCHEDULER_RELATIVE).read_text())

    with pytest.raises(concurrency.PoolConcurrencyError, match="openshell_pool_session_reentry"):
        concurrency.acquire(
            market="cn",
            company="600104-上汽集团",
            tenant_id="tenant-a",
            user_id="user-a",
            session_id="session-a",
            now=200,
            project_root=tmp_path,
        )

    after = json.loads((tmp_path / concurrency.SCHEDULER_RELATIVE).read_text())
    assert after["leases"] == before["leases"]
    assert admitted.owner_token not in json.dumps(after)
    assert admitted.owner_token not in repr(admitted)


def test_unconfirmed_abandon_is_atomic_and_only_terminal_release_promotes(tmp_path: Path) -> None:
    active = _binding(
        tmp_path,
        market="cn",
        company="600104-上汽集团",
        run_id="canary-111111111111",
    )
    pool.register_active(active=active, local_port=28651, project_root=tmp_path)
    writer = concurrency.acquire(
        market="cn",
        company="600104-上汽集团",
        tenant_id="tenant-a",
        user_id="writer",
        session_id="writer-session",
        now=100,
        project_root=tmp_path,
    )
    writer = concurrency.mark_run_bound(
        market="cn",
        company="600104-上汽集团",
        tenant_id="tenant-a",
        user_id="writer",
        session_id="writer-session",
        owner_token=writer.owner_token,
        owner_generation=writer.owner_generation,
        now=100,
        project_root=tmp_path,
    )
    cancelled = concurrency.acquire(
        market="cn",
        company="600104-上汽集团",
        tenant_id="tenant-a",
        user_id="cancelled",
        session_id="cancelled-session",
        now=101,
        project_root=tmp_path,
    )
    survivor = concurrency.acquire(
        market="cn",
        company="600104-上汽集团",
        tenant_id="tenant-a",
        user_id="survivor",
        session_id="survivor-session",
        now=102,
        project_root=tmp_path,
    )

    removed = concurrency.abandon(
        tenant_id="tenant-a",
        user_id="cancelled",
        session_id="cancelled-session",
        owner_token=cancelled.owner_token,
        owner_generation=cancelled.owner_generation,
        now=103,
        project_root=tmp_path,
    )
    assert removed == {
        "released": True,
        "abandoned": True,
        "lease_id": cancelled.lease_id,
        "state": "removed",
    }
    uncertain = concurrency.release(
        tenant_id="tenant-a",
        user_id="writer",
        session_id="writer-session",
        owner_token=writer.owner_token,
        owner_generation=writer.owner_generation,
        terminal_confirmed=False,
        now=104,
        project_root=tmp_path,
    )
    assert uncertain["released"] is False
    assert uncertain["state"] == "orphaned"
    state = concurrency.status(now=104, project_root=tmp_path)
    assert (state["orphaned_leases"], state["waiting_leases"], state["active_leases"]) == (1, 1, 0)
    with pytest.raises(concurrency.PoolConcurrencyError, match="openshell_pool_session_reentry"):
        concurrency.acquire(
            market="cn",
            company="600104-上汽集团",
            tenant_id="tenant-a",
            user_id="writer",
            session_id="writer-session",
            now=105,
            project_root=tmp_path,
        )

    concurrency.release(
        tenant_id="tenant-a",
        user_id="writer",
        session_id="writer-session",
        owner_token=writer.owner_token,
        owner_generation=writer.owner_generation,
        terminal_confirmed=True,
        now=106,
        project_root=tmp_path,
    )
    promoted = concurrency.heartbeat(
        market="cn",
        company="600104-上汽集团",
        tenant_id="tenant-a",
        user_id="survivor",
        session_id="survivor-session",
        owner_token=survivor.owner_token,
        owner_generation=survivor.owner_generation,
        now=107,
        project_root=tmp_path,
    )
    assert promoted.status == "active"


def test_cross_company_writers_run_in_parallel_but_session_cannot_hot_switch(tmp_path: Path) -> None:
    _company(tmp_path, "cn", "600104-上汽集团")
    _company(tmp_path, "cn", "600519-贵州茅台")
    first_plan = pool.plan_slot(
        market="cn",
        company="600104-上汽集团",
        run_id="canary-111111111111",
        local_port=28652,
        project_root=tmp_path,
    )
    second_plan = pool.plan_slot(
        market="cn",
        company="600519-贵州茅台",
        run_id="canary-222222222222",
        local_port=28653,
        project_root=tmp_path,
    )
    _register(
        tmp_path,
        active=_binding(
            tmp_path,
            market="cn",
            company="600104-上汽集团",
            run_id=first_plan.run_id,
            active_relative=Path(first_plan.active_relative),
        ),
        local_port=28652,
    )
    _register(
        tmp_path,
        active=_binding(
            tmp_path,
            market="cn",
            company="600519-贵州茅台",
            run_id=second_plan.run_id,
            active_relative=Path(second_plan.active_relative),
            key="b" * 64,
        ),
        local_port=28653,
    )

    first = concurrency.acquire(
        market="cn",
        company="600104-上汽集团",
        tenant_id="tenant-a",
        user_id="user-a",
        session_id="session-a",
        now=100,
        project_root=tmp_path,
    )
    second = concurrency.acquire(
        market="cn",
        company="600519-贵州茅台",
        tenant_id="tenant-b",
        user_id="user-b",
        session_id="session-b",
        now=100,
        project_root=tmp_path,
    )
    assert first.status == second.status == "active"
    assert first.run_id != second.run_id
    assert first.base != second.base

    with pytest.raises(concurrency.PoolConcurrencyError, match="openshell_pool_session_scope_conflict"):
        concurrency.acquire(
            market="cn",
            company="600519-贵州茅台",
            tenant_id="tenant-a",
            user_id="user-a",
            session_id="session-a",
            now=101,
            project_root=tmp_path,
        )


def test_drain_preserves_existing_affinity_and_rejects_new_sessions(tmp_path: Path) -> None:
    active = _binding(
        tmp_path,
        market="cn",
        company="600104-上汽集团",
        run_id="canary-111111111111",
    )
    pool.register_active(active=active, local_port=28651, project_root=tmp_path)
    original = concurrency.acquire(
        market="cn",
        company="600104-上汽集团",
        tenant_id="tenant-a",
        user_id="user-a",
        session_id="session-a",
        now=100,
        project_root=tmp_path,
    )
    original = concurrency.mark_run_bound(
        market="cn",
        company="600104-上汽集团",
        tenant_id="tenant-a",
        user_id="user-a",
        session_id="session-a",
        owner_token=original.owner_token,
        owner_generation=original.owner_generation,
        now=100,
        project_root=tmp_path,
    )
    drained = concurrency.set_traffic_state(
        market="cn",
        company="600104-上汽集团",
        run_id=original.run_id,
        traffic_state="draining",
        now=101,
        project_root=tmp_path,
    )
    assert drained["active_leases"] == 1
    assert drained["drained"] is False
    existing = concurrency.heartbeat(
        market="cn",
        company="600104-上汽集团",
        tenant_id="tenant-a",
        user_id="user-a",
        session_id="session-a",
        owner_token=original.owner_token,
        owner_generation=original.owner_generation,
        now=102,
        project_root=tmp_path,
    )
    assert existing.status == "active"
    with pytest.raises(concurrency.PoolConcurrencyError, match="openshell_pool_binding_draining") as error:
        concurrency.acquire(
            market="cn",
            company="600104-上汽集团",
            tenant_id="tenant-a",
            user_id="user-b",
            session_id="session-b",
            now=102,
            project_root=tmp_path,
        )
    assert error.value.retryable is True
    expired = concurrency.status(now=401, project_root=tmp_path)
    assert expired["active_leases"] == 0
    assert expired["orphaned_leases"] == 1
    assert expired["bindings"][0]["traffic_state"] == "draining"
    assert (
        concurrency.release(
            tenant_id="tenant-a",
            user_id="user-a",
            session_id="session-a",
            owner_token=original.owner_token,
            owner_generation=original.owner_generation,
            terminal_confirmed=True,
            now=402,
            project_root=tmp_path,
        )["released"]
        is True
    )
    assert concurrency.status(now=402, project_root=tmp_path)["orphaned_leases"] == 0


def test_require_idle_drain_rejects_leases_without_dropping_waiters(tmp_path: Path) -> None:
    active = _binding(
        tmp_path,
        market="cn",
        company="600104-上汽集团",
        run_id="canary-111111111111",
    )
    pool.register_active(active=active, local_port=28651, project_root=tmp_path)
    concurrency.acquire(
        market="cn",
        company="600104-上汽集团",
        tenant_id="tenant-a",
        user_id="user-a",
        session_id="session-a",
        now=100,
        project_root=tmp_path,
    )
    queued = concurrency.acquire(
        market="cn",
        company="600104-上汽集团",
        tenant_id="tenant-a",
        user_id="user-b",
        session_id="session-b",
        now=100,
        project_root=tmp_path,
    )
    assert queued.status == "queued"

    with pytest.raises(
        concurrency.PoolConcurrencyError,
        match="openshell_pool_live_leases_require_terminal",
    ) as error:
        concurrency.set_traffic_state(
            market="cn",
            company="600104-上汽集团",
            run_id="canary-111111111111",
            traffic_state="draining",
            require_idle=True,
            now=101,
            project_root=tmp_path,
        )

    assert error.value.retryable is True
    state = concurrency.status(now=101, project_root=tmp_path)
    assert state["bindings"][0]["traffic_state"] == "accepting"
    assert state["active_leases"] == 1
    assert state["waiting_leases"] == 1


def test_ttl_expiry_orphans_writer_until_terminal_then_promotes_waiter(tmp_path: Path) -> None:
    active = _binding(
        tmp_path,
        market="cn",
        company="600104-上汽集团",
        run_id="canary-111111111111",
    )
    pool.register_active(active=active, local_port=28651, project_root=tmp_path)
    writer = concurrency.acquire(
        market="cn",
        company="600104-上汽集团",
        tenant_id="tenant-a",
        user_id="user-a",
        session_id="session-a",
        now=100,
        project_root=tmp_path,
    )
    writer = concurrency.mark_run_bound(
        market="cn",
        company="600104-上汽集团",
        tenant_id="tenant-a",
        user_id="user-a",
        session_id="session-a",
        owner_token=writer.owner_token,
        owner_generation=writer.owner_generation,
        now=100,
        project_root=tmp_path,
    )
    queued = concurrency.acquire(
        market="cn",
        company="600104-上汽集团",
        tenant_id="tenant-a",
        user_id="user-b",
        session_id="session-b",
        now=101,
        project_root=tmp_path,
    )
    assert queued.status == "queued"

    with pytest.raises(concurrency.PoolConcurrencyError, match="openshell_pool_lease_orphaned"):
        concurrency.heartbeat(
            market="cn",
            company="600104-上汽集团",
            tenant_id="tenant-a",
            user_id="user-a",
            session_id="session-a",
            owner_token=writer.owner_token,
            owner_generation=writer.owner_generation,
            now=1000,
            project_root=tmp_path,
        )
    blocked = concurrency.heartbeat(
        market="cn",
        company="600104-上汽集团",
        tenant_id="tenant-a",
        user_id="user-b",
        session_id="session-b",
        owner_token=queued.owner_token,
        owner_generation=queued.owner_generation,
        now=1000,
        project_root=tmp_path,
    )
    assert blocked.status == "queued"
    assert concurrency.status(now=1000, project_root=tmp_path)["orphaned_leases"] == 1
    concurrency.release(
        tenant_id="tenant-a",
        user_id="user-a",
        session_id="session-a",
        owner_token=writer.owner_token,
        owner_generation=writer.owner_generation,
        terminal_confirmed=True,
        now=1001,
        project_root=tmp_path,
    )
    promoted = concurrency.heartbeat(
        market="cn",
        company="600104-上汽集团",
        tenant_id="tenant-a",
        user_id="user-b",
        session_id="session-b",
        owner_token=queued.owner_token,
        owner_generation=queued.owner_generation,
        now=1001,
        project_root=tmp_path,
    )
    assert promoted.status == "active"
    assert concurrency.status(now=1000, project_root=tmp_path)["active_leases"] == 1


def test_v1_scheduler_restart_migrates_active_to_orphan_and_drops_waiters(tmp_path: Path) -> None:
    active = _binding(
        tmp_path,
        market="cn",
        company="600104-上汽集团",
        run_id="canary-111111111111",
    )
    pool.register_active(active=active, local_port=28651, project_root=tmp_path)
    writer = concurrency.acquire(
        market="cn",
        company="600104-上汽集团",
        tenant_id="tenant-a",
        user_id="writer",
        session_id="writer-session",
        now=100,
        project_root=tmp_path,
    )
    concurrency.acquire(
        market="cn",
        company="600104-上汽集团",
        tenant_id="tenant-a",
        user_id="waiter",
        session_id="waiter-session",
        now=101,
        project_root=tmp_path,
    )
    scheduler_path = tmp_path / concurrency.SCHEDULER_RELATIVE
    legacy = json.loads(scheduler_path.read_text())
    legacy["schema_version"] = concurrency.LEGACY_SCHEMA_VERSION
    legacy.pop("next_owner_generation")
    for lease in legacy["leases"]:
        lease.pop("owner_token_hash")
        lease.pop("owner_generation")
        lease.pop("run_bound")
    _write_json(scheduler_path, legacy, root=tmp_path)

    restarted = concurrency.status(now=102, project_root=tmp_path)
    persisted = json.loads(scheduler_path.read_text())
    assert persisted["schema_version"] == concurrency.SCHEMA_VERSION
    assert (restarted["active_leases"], restarted["orphaned_leases"], restarted["waiting_leases"]) == (0, 1, 0)
    assert len(persisted["leases"]) == 1
    assert persisted["leases"][0]["lease_id"] == writer.lease_id
    with pytest.raises(concurrency.PoolConcurrencyError, match="openshell_pool_session_reentry"):
        concurrency.acquire(
            market="cn",
            company="600104-上汽集团",
            tenant_id="tenant-a",
            user_id="writer",
            session_id="writer-session",
            now=103,
            project_root=tmp_path,
        )
    blocked = concurrency.acquire(
        market="cn",
        company="600104-上汽集团",
        tenant_id="tenant-a",
        user_id="new-waiter",
        session_id="new-waiter-session",
        now=103,
        project_root=tmp_path,
    )
    assert blocked.status == "queued"


def test_v2_restart_treats_existing_active_as_run_bound(tmp_path: Path) -> None:
    active = _binding(
        tmp_path,
        market="cn",
        company="600104-上汽集团",
        run_id="canary-111111111111",
    )
    pool.register_active(active=active, local_port=28651, project_root=tmp_path)
    writer = concurrency.acquire(
        market="cn",
        company="600104-上汽集团",
        tenant_id="tenant-a",
        user_id="writer",
        session_id="writer-session",
        now=100,
        project_root=tmp_path,
    )
    scheduler_path = tmp_path / concurrency.SCHEDULER_RELATIVE
    previous = json.loads(scheduler_path.read_text())
    previous["schema_version"] = concurrency.PREVIOUS_SCHEMA_VERSION
    for lease in previous["leases"]:
        lease.pop("run_bound")
    _write_json(scheduler_path, previous, root=tmp_path)

    concurrency.status(now=101, project_root=tmp_path)
    migrated = json.loads(scheduler_path.read_text())
    assert migrated["schema_version"] == concurrency.SCHEMA_VERSION
    assert migrated["leases"][0]["run_bound"] is True
    concurrency.release(
        tenant_id="tenant-a",
        user_id="writer",
        session_id="writer-session",
        owner_token=writer.owner_token,
        owner_generation=writer.owner_generation,
        terminal_confirmed=True,
        now=102,
        project_root=tmp_path,
    )


def test_concurrent_admission_keeps_one_company_writer_and_valid_fifo(tmp_path: Path) -> None:
    active = _binding(
        tmp_path,
        market="cn",
        company="600104-上汽集团",
        run_id="canary-111111111111",
    )
    pool.register_active(active=active, local_port=28651, project_root=tmp_path)

    def admit(index: int) -> concurrency.PoolAdmission:
        return concurrency.acquire(
            market="cn",
            company="600104-上汽集团",
            tenant_id="tenant-a",
            user_id=f"user-{index}",
            session_id=f"session-{index}",
            now=100,
            project_root=tmp_path,
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        admissions = list(executor.map(admit, range(8)))

    assert sum(item.status == "active" for item in admissions) == 1
    assert sum(item.status == "queued" for item in admissions) == 7
    assert sorted(item.queue_position for item in admissions if item.status == "queued") == list(range(1, 8))
    scheduler = concurrency._load_scheduler(tmp_path)
    assert len({item["lease_id"] for item in scheduler["leases"]}) == 8


def test_cross_process_admission_keeps_one_company_writer_and_valid_fifo(tmp_path: Path) -> None:
    active = _binding(
        tmp_path,
        market="cn",
        company="600104-上汽集团",
        run_id="canary-111111111111",
    )
    pool.register_active(active=active, local_port=28651, project_root=tmp_path)

    with ProcessPoolExecutor(max_workers=6) as executor:
        admissions = list(
            executor.map(
                _admit_company_from_process,
                ((str(tmp_path), index) for index in range(6)),
            )
        )

    assert sum(status == "active" for status, _position, _lease_id in admissions) == 1
    assert sum(status == "queued" for status, _position, _lease_id in admissions) == 5
    assert sorted(position for status, position, _lease_id in admissions if status == "queued") == list(range(1, 6))
    assert len({lease_id for _status, _position, lease_id in admissions}) == 6
    scheduler = concurrency._load_scheduler(tmp_path)
    assert len(scheduler["leases"]) == 6


def test_failed_binding_is_isolated_from_other_company(tmp_path: Path) -> None:
    first_active = _binding(
        tmp_path,
        market="cn",
        company="600104-上汽集团",
        run_id="canary-111111111111",
    )
    _company(tmp_path, "cn", "600519-贵州茅台")
    second_plan = pool.plan_slot(
        market="cn",
        company="600519-贵州茅台",
        run_id="canary-222222222222",
        local_port=28652,
        project_root=tmp_path,
    )
    second_active = _binding(
        tmp_path,
        market="cn",
        company="600519-贵州茅台",
        run_id=second_plan.run_id,
        active_relative=Path(second_plan.active_relative),
        key="b" * 64,
    )
    pool.register_active(active=first_active, local_port=28651, project_root=tmp_path)
    _register(tmp_path, active=second_active, local_port=28652)

    healthy = concurrency.acquire(
        market="cn",
        company="600519-贵州茅台",
        tenant_id="tenant-b",
        user_id="user-b",
        session_id="session-b",
        now=100,
        project_root=tmp_path,
    )
    failed_lease = concurrency.acquire(
        market="cn",
        company="600104-上汽集团",
        tenant_id="tenant-a",
        user_id="user-a",
        session_id="session-a",
        now=100,
        project_root=tmp_path,
    )
    failed_lease = concurrency.mark_run_bound(
        market="cn",
        company="600104-上汽集团",
        tenant_id="tenant-a",
        user_id="user-a",
        session_id="session-a",
        owner_token=failed_lease.owner_token,
        owner_generation=failed_lease.owner_generation,
        now=100,
        project_root=tmp_path,
    )
    assert failed_lease.status == "active"
    concurrency.set_traffic_state(
        market="cn",
        company="600104-上汽集团",
        run_id="canary-111111111111",
        traffic_state="failed",
        now=101,
        project_root=tmp_path,
    )
    state_after_failure = concurrency.status(now=101, project_root=tmp_path)
    assert state_after_failure["active_leases"] == 1
    assert state_after_failure["orphaned_leases"] == 1

    with pytest.raises(concurrency.PoolConcurrencyError, match="openshell_pool_binding_failed"):
        concurrency.acquire(
            market="cn",
            company="600104-上汽集团",
            tenant_id="tenant-a",
            user_id="user-a",
            session_id="session-a",
            now=102,
            project_root=tmp_path,
        )
    healthy_again = concurrency.heartbeat(
        market="cn",
        company="600519-贵州茅台",
        tenant_id="tenant-b",
        user_id="user-b",
        session_id="session-b",
        owner_token=healthy.owner_token,
        owner_generation=healthy.owner_generation,
        now=102,
        project_root=tmp_path,
    )
    assert healthy_again.status == "active"
    assert healthy_again.run_id == healthy.run_id


def test_waiting_queue_has_a_hard_capacity_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    active = _binding(
        tmp_path,
        market="cn",
        company="600104-上汽集团",
        run_id="canary-111111111111",
    )
    pool.register_active(active=active, local_port=28651, project_root=tmp_path)
    monkeypatch.setattr(concurrency, "DEFAULT_MAX_WAITING_PER_BINDING", 2)
    for index in range(3):
        admission = concurrency.acquire(
            market="cn",
            company="600104-上汽集团",
            tenant_id="tenant-a",
            user_id=f"user-{index}",
            session_id=f"session-{index}",
            now=100 + index,
            project_root=tmp_path,
        )
        assert admission.status == ("active" if index == 0 else "queued")

    with pytest.raises(concurrency.PoolConcurrencyError, match="openshell_pool_queue_full") as error:
        concurrency.acquire(
            market="cn",
            company="600104-上汽集团",
            tenant_id="tenant-a",
            user_id="user-overflow",
            session_id="session-overflow",
            now=103,
            project_root=tmp_path,
        )
    assert error.value.retryable is True


def test_identity_key_loss_with_live_lease_fails_closed_without_rekey(tmp_path: Path) -> None:
    active = _binding(
        tmp_path,
        market="cn",
        company="600104-上汽集团",
        run_id="canary-111111111111",
    )
    pool.register_active(active=active, local_port=28651, project_root=tmp_path)
    concurrency.acquire(
        market="cn",
        company="600104-上汽集团",
        tenant_id="tenant-a",
        user_id="user-a",
        session_id="session-a",
        now=100,
        project_root=tmp_path,
    )
    identity_key = tmp_path / concurrency.IDENTITY_KEY_RELATIVE
    identity_key.unlink()

    with pytest.raises(concurrency.PoolConcurrencyError, match="openshell_pool_identity_key_missing"):
        concurrency.acquire(
            market="cn",
            company="600104-上汽集团",
            tenant_id="tenant-a",
            user_id="user-b",
            session_id="session-b",
            now=101,
            project_root=tmp_path,
        )
    assert not identity_key.exists()


def test_unregister_requires_all_leases_to_reach_terminal(tmp_path: Path) -> None:
    run_id = "canary-111111111111"
    active = _binding(
        tmp_path,
        market="cn",
        company="600104-上汽集团",
        run_id=run_id,
    )
    pool.register_active(active=active, local_port=28651, project_root=tmp_path)
    admission = concurrency.acquire(
        market="cn",
        company="600104-上汽集团",
        tenant_id="tenant-a",
        user_id="user-a",
        session_id="session-a",
        now=100,
        project_root=tmp_path,
    )
    admission = concurrency.mark_run_bound(
        market="cn",
        company="600104-上汽集团",
        tenant_id="tenant-a",
        user_id="user-a",
        session_id="session-a",
        owner_token=admission.owner_token,
        owner_generation=admission.owner_generation,
        now=100,
        project_root=tmp_path,
    )

    with pytest.raises(pool.PoolRegistryError, match="openshell_pool_unregister_live_leases"):
        pool.unregister(
            market="cn",
            company="600104-上汽集团",
            run_id=run_id,
            project_root=tmp_path,
        )
    concurrency.release(
        tenant_id="tenant-a",
        user_id="user-a",
        session_id="session-a",
        owner_token=admission.owner_token,
        owner_generation=admission.owner_generation,
        terminal_confirmed=True,
        now=101,
        project_root=tmp_path,
    )
    registry = pool.unregister(
        market="cn",
        company="600104-上汽集团",
        run_id=run_id,
        project_root=tmp_path,
    )
    assert registry["bindings"] == []


def test_pool_canary_hooks_atomically_register_inventory_and_prepare_clean_stop(tmp_path: Path) -> None:
    market = "cn"
    company = "600104-上汽集团"
    run_id = "canary-111111111111"
    _company(tmp_path, market, company)
    plan = pool.plan_slot(
        market=market,
        company=company,
        run_id=run_id,
        local_port=28652,
        project_root=tmp_path,
    )
    active = _binding(
        tmp_path,
        market=market,
        company=company,
        run_id=run_id,
        active_relative=Path(plan.active_relative),
    )
    reservation = pool.allocate_local_port(
        project_root=tmp_path,
        first=28652,
        last=28652,
        available=lambda _host, _port: True,
    )
    lifecycle = canary.CanaryLifecycle(
        project_root=tmp_path,
        adapter=LifecycleAdapter(project_root=tmp_path),
        pool_slot_id=plan.scope_id,
        local_port=reservation.local_port,
        reservation_token=reservation.reservation_token,
    )
    spec = lifecycle._spec(market=market, company=company, pilot_id=run_id)
    manifest = json.loads((tmp_path / plan.state_relative / "runs" / run_id / "canary.json").read_text())

    lifecycle._validate_start_scope(market=market, company=company, pilot_id=run_id)
    lifecycle._after_active(spec, manifest)
    registry = pool.load_registry(project_root=tmp_path)
    assert registry["bindings"][0]["active"] == active.as_posix()
    assert lifecycle._pool_owned_sandboxes() == {
        f"{SANDBOX_PREFIX}{run_id}": {
            "run_id": run_id,
            "sandbox_id": f"sandbox-{run_id}",
            "container_id": "4" * 64,
        }
    }

    lifecycle._prepare_stop(spec, manifest)
    scheduler = concurrency.status(now=11, project_root=tmp_path)
    assert scheduler["bindings"][0]["traffic_state"] == "draining"
    assert pool.load_registry(project_root=tmp_path)["bindings"][0]["run_id"] == run_id
    lifecycle._before_stop(spec, manifest)
    assert manifest["phase"] == "stopping"
    # A crash after the durable marker but before unregister must be retryable
    # without resolving the now intentionally stale running binding.
    lifecycle._prepare_stop(spec, manifest)
    lifecycle._after_stop_marked(spec, manifest)
    assert pool.load_registry(project_root=tmp_path)["bindings"] == []
    # Retrying after unregister is safe because the durable stopping marker is
    # written before the binding disappears.
    lifecycle._after_stop_marked(spec, manifest)
    scheduler = concurrency.status(now=11, project_root=tmp_path)
    assert scheduler["active_leases"] == 0
    assert scheduler["orphaned_leases"] == 0


def test_pool_canary_stop_drains_but_never_unregisters_a_live_writer(tmp_path: Path) -> None:
    market = "cn"
    company = "600104-上汽集团"
    run_id = "canary-222222222222"
    _company(tmp_path, market, company)
    plan = pool.plan_slot(
        market=market,
        company=company,
        run_id=run_id,
        local_port=28652,
        project_root=tmp_path,
    )
    _binding(
        tmp_path,
        market=market,
        company=company,
        run_id=run_id,
        active_relative=Path(plan.active_relative),
    )
    reservation = pool.allocate_local_port(
        project_root=tmp_path,
        first=28652,
        last=28652,
        available=lambda _host, _port: True,
    )
    lifecycle = canary.CanaryLifecycle(
        project_root=tmp_path,
        adapter=LifecycleAdapter(project_root=tmp_path),
        pool_slot_id=plan.scope_id,
        local_port=reservation.local_port,
        reservation_token=reservation.reservation_token,
    )
    spec = lifecycle._spec(market=market, company=company, pilot_id=run_id)
    manifest = json.loads((spec.run_dir / "canary.json").read_text())
    lifecycle._after_active(spec, manifest)
    admission = concurrency.acquire(
        market=market,
        company=company,
        tenant_id="tenant-a",
        user_id="user-a",
        session_id="session-a",
        now=100,
        project_root=tmp_path,
    )
    admission = concurrency.mark_run_bound(
        market=market,
        company=company,
        tenant_id="tenant-a",
        user_id="user-a",
        session_id="session-a",
        owner_token=admission.owner_token,
        owner_generation=admission.owner_generation,
        now=100,
        project_root=tmp_path,
    )

    with pytest.raises(canary.WidePilotError, match="canary_pool_live_leases_require_terminal"):
        lifecycle._prepare_stop(spec, manifest)
    assert pool.load_registry(project_root=tmp_path)["bindings"][0]["run_id"] == run_id
    state = concurrency.status(now=101, project_root=tmp_path)
    assert state["bindings"][0]["traffic_state"] == "draining"
    assert state["active_leases"] + state["orphaned_leases"] == 1

    concurrency.release(
        tenant_id="tenant-a",
        user_id="user-a",
        session_id="session-a",
        owner_token=admission.owner_token,
        owner_generation=admission.owner_generation,
        terminal_confirmed=True,
        now=102,
        project_root=tmp_path,
    )
    lifecycle._prepare_stop(spec, manifest)
    lifecycle._before_stop(spec, manifest)
    lifecycle._after_stop_marked(spec, manifest)
    assert pool.load_registry(project_root=tmp_path)["bindings"] == []
