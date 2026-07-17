import hashlib
import json
from pathlib import Path

import anyio
import httpx
import pytest
from pydantic import ValidationError
from schemas import ChatRequest

from services import agent_chat_runtime as runtime, agent_runtime_streaming, hermes_client, openshell_pool_adapter


@pytest.fixture(autouse=True)
def _enable_internal_runtime_override_tests(monkeypatch):
    monkeypatch.setenv("SIQ_HERMES_REQUEST_RUNTIME_OVERRIDE_ENABLED", "1")


def _canary_route() -> hermes_client.HermesRunRoute:
    return hermes_client.HermesRunRoute(
        target="openshell",
        base="http://127.0.0.1:28651/v1/runs",
        model="siq_analysis",
        authorization="Bearer " + "a" * 64,
        session_namespace="siq:openshell:canary-0123456789ab:siq_analysis",
        canary_run_id="canary-0123456789ab",
    )


def _company_context(
    *,
    company: str = "example",
    market: str = "us",
) -> dict[str, object]:
    return {
        "company": {
            "code": company.split("-", 1)[0],
            "name": company.split("-", 1)[-1],
            "dir": company,
            "market": market,
        },
        "research_identity": {"market": market, "company_id": company.split("-", 1)[0]},
    }


def _write_runtime_selection(root: Path, *, target: str, session_mode: str = "all") -> Path:
    state_root = root / "var" / "openshell" / "runtime-selection"
    state_root.mkdir(parents=True, exist_ok=True)
    for directory in (root / "var" / "openshell", state_root):
        directory.chmod(0o700)
    state = state_root / "siq-analysis.json"
    state.write_text(
        json.dumps(
            {
                "schema_version": "siq.openshell.runtime_selection.v1",
                "profile": "siq_analysis",
                "target": target,
                "session_mode": session_mode,
                "unmatched_scope": "host",
            },
            sort_keys=True,
        ),
        encoding="ascii",
    )
    state.chmod(0o600)
    return state


def _write_canary_state(root: Path, *, key: str = "a" * 64) -> tuple[Path, Path]:
    state_root = root / "var" / "openshell" / "canary" / "siq-analysis"
    run_id = "canary-0123456789ab"
    run_state = state_root / "runs" / run_id
    for directory in (
        root / "var" / "openshell",
        root / "var" / "openshell" / "canary",
        state_root,
        state_root / "runs",
        run_state,
    ):
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o700)

    manifest = run_state / "canary.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "siq.openshell.siq_analysis_canary_lifecycle.v1",
                "mode": "NOT_PRODUCTION_CANARY",
                "readiness_effect": "none",
                "phase": "running",
                "profile": "siq_analysis",
                "run_id": run_id,
                "market": "us",
                "company": "example",
                "analysis_relative_path": "data/wiki/us/companies/example/analysis",
                "writable_relative_path": "data/wiki/us/companies/example/analysis",
                "write_scope": "current_company_analysis_root",
                "normal_business_mutations": ["create", "modify", "rename", "delete"],
                "source_sha256": "b" * 64,
                "source_stock_code": "EXAMPLE",
                "sandbox_name": "siq-analysis-canary-0123456789ab",
                "lifecycle_label": "siq-analysis-canary-not-production-v1",
                "image_ref": "example@sha256:" + "c" * 64,
                "image_id": "sha256:" + "d" * 64,
                "runtime_snapshot": "var/openshell/runtime.json",
                "mount_plan": "var/openshell/mount.json",
                "mount_plan_sha256": "e" * 64,
                "mount_count": 7,
                "policy": "var/openshell/policy.yaml",
                "policy_sha256": "f" * 64,
                "providers": [
                    "siq-minimax-cn-pool",
                    "siq-stepfun",
                    "siq-kimi-coding",
                    "siq-tavily-search",
                ],
                "formal_blockers_not_overridden": [
                    "siq-exa-search_not_configured",
                    "local_model_8004_not_required",
                    "local_model_8006_not_required",
                    "milvus_formal_proof_not_required",
                    "clash_fake_ip_egress_guard_compatibility_unresolved",
                ],
                "broker_request_identity_required": True,
                "api_key_sha256": hashlib.sha256(key.encode("ascii")).hexdigest(),
                "run_nonce_sha256": "1" * 64,
                "host_hermes_receipt_sha256": "2" * 64,
                "sandbox_id": "sandbox-id",
                "container_id": "3" * 64,
                "guard_process": "guard.process.json",
                "forward_process": "forward.process.json",
                "result_is_formal_evidence": False,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    manifest.chmod(0o600)
    manifest_sha256 = hashlib.sha256(manifest.read_bytes()).hexdigest()
    api_key_sha256 = hashlib.sha256(key.encode("ascii")).hexdigest()
    active = state_root / "active.json"
    active.write_text(
        json.dumps(
            {
                "schema_version": "siq.openshell.siq_analysis_canary_lifecycle.v1",
                "mode": "NOT_PRODUCTION_CANARY",
                "readiness_effect": "none",
                "profile": "siq_analysis",
                "run_id": run_id,
                "market": "us",
                "company": "example",
                "run_state": f"var/openshell/canary/siq-analysis/runs/{run_id}",
                "manifest": f"var/openshell/canary/siq-analysis/runs/{run_id}/canary.json",
                "manifest_sha256": manifest_sha256,
                "api_key_sha256": api_key_sha256,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    active.chmod(0o600)
    key_path = run_state / "api.key"
    key_path.write_text(key + "\n", encoding="ascii")
    key_path.chmod(0o600)
    return active, key_path


def test_chat_request_runtime_target_is_explicit_and_closed_set():
    assert ChatRequest(message="question").runtime_target is None
    assert ChatRequest(message="question", runtime_target="openshell").runtime_target == "openshell"
    with pytest.raises(ValidationError):
        ChatRequest(message="question", runtime_target="automatic")


def test_request_runtime_override_is_forbidden_by_default(monkeypatch):
    monkeypatch.delenv("SIQ_HERMES_REQUEST_RUNTIME_OVERRIDE_ENABLED", raising=False)

    with pytest.raises(
        hermes_client.HermesRuntimeSelectionError,
        match="hermes_runtime_request_override_forbidden",
    ):
        hermes_client.normalize_runtime_target("siq_analysis", "host")


def test_implicit_runtime_selection_uses_internal_pinned_target_without_enabling_request_override(
    tmp_path,
    monkeypatch,
):
    _write_canary_state(tmp_path)
    monkeypatch.setattr(hermes_client, "_project_root", lambda: tmp_path)
    monkeypatch.delenv("SIQ_HERMES_REQUEST_RUNTIME_OVERRIDE_ENABLED", raising=False)
    monkeypatch.setenv("SIQ_HERMES_RUNTIME", "openshell")
    monkeypatch.setenv("SIQ_HERMES_ANALYSIS_OPENSHELL_CANARY_ENABLED", "1")
    monkeypatch.setenv("SIQ_HERMES_ANALYSIS_OPENSHELL_CANARY_SESSION_MODE", "all")

    route = hermes_client.resolve_requested_run_route(
        "siq_analysis",
        None,
        session_id="implicit-session",
        context=_company_context(),
    )

    assert route is not None
    assert route.target == "openshell"


def test_runtime_target_defaults_to_host_and_other_profiles_cannot_select_canary(monkeypatch):
    monkeypatch.setenv("SIQ_HERMES_ANALYSIS_OPENSHELL_CANARY_ENABLED", "1")

    assert hermes_client.normalize_runtime_target("siq_analysis", None) == "host"
    assert hermes_client.normalize_runtime_target("siq_analysis", "host") == "host"
    assert hermes_client.normalize_runtime_target("siq_assistant", "openshell") == "host"
    assert hermes_client.normalize_runtime_target("siq_ic_chairman", "openshell") == "host"


def test_analysis_runtime_uses_environment_default_and_explicit_host_is_rollback(monkeypatch):
    monkeypatch.setenv("SIQ_HERMES_RUNTIME", "openshell")
    monkeypatch.setenv("SIQ_HERMES_ANALYSIS_OPENSHELL_CANARY_ENABLED", "1")
    monkeypatch.setenv("SIQ_HERMES_ANALYSIS_OPENSHELL_CANARY_SESSION_IDS", "session-a")

    assert hermes_client.normalize_runtime_target("siq_analysis", None, session_id="session-a") == "openshell"
    assert hermes_client.normalize_runtime_target("siq_analysis", "host", session_id="session-a") == "host"
    assert hermes_client.normalize_runtime_target("siq_assistant", None, session_id="session-a") == "host"


def test_analysis_runtime_can_explicitly_authorize_all_sessions(monkeypatch):
    monkeypatch.setenv("SIQ_HERMES_RUNTIME", "openshell")
    monkeypatch.setenv("SIQ_HERMES_ANALYSIS_OPENSHELL_CANARY_ENABLED", "1")
    monkeypatch.setenv("SIQ_HERMES_ANALYSIS_OPENSHELL_CANARY_SESSION_MODE", "all")
    monkeypatch.delenv("SIQ_HERMES_ANALYSIS_OPENSHELL_CANARY_SESSION_IDS", raising=False)

    assert hermes_client.normalize_runtime_target("siq_analysis", None, session_id="new-session") == "openshell"
    assert hermes_client.normalize_runtime_target("siq_analysis", None, session_id=None) == "openshell"
    assert hermes_client.normalize_runtime_target("siq_analysis", "host", session_id="new-session") == "host"
    assert hermes_client.normalize_runtime_target("siq_factchecker", None, session_id="new-session") == "host"


def test_analysis_runtime_rejects_unknown_session_mode(monkeypatch):
    monkeypatch.setenv("SIQ_HERMES_ANALYSIS_OPENSHELL_CANARY_ENABLED", "1")
    monkeypatch.setenv("SIQ_HERMES_ANALYSIS_OPENSHELL_CANARY_SESSION_MODE", "automatic")

    with pytest.raises(
        hermes_client.HermesRuntimeSelectionError,
        match="openshell_canary_session_mode_invalid",
    ):
        hermes_client.normalize_runtime_target("siq_analysis", "openshell", session_id="session-a")


def test_invalid_environment_runtime_fails_closed(monkeypatch):
    monkeypatch.setenv("SIQ_HERMES_RUNTIME", "automatic")
    with pytest.raises(hermes_client.HermesRuntimeSelectionError, match="hermes_runtime_target_invalid"):
        hermes_client.normalize_runtime_target("siq_analysis", None, session_id="session-a")


def test_analysis_canary_requires_operator_enablement_and_session_authorization(monkeypatch):
    monkeypatch.delenv("SIQ_HERMES_ANALYSIS_OPENSHELL_CANARY_ENABLED", raising=False)
    with pytest.raises(hermes_client.HermesRuntimeSelectionError, match="openshell_canary_not_enabled"):
        hermes_client.normalize_runtime_target("siq_analysis", "openshell", session_id="session-a")

    monkeypatch.setenv("SIQ_HERMES_ANALYSIS_OPENSHELL_CANARY_ENABLED", "1")
    monkeypatch.delenv("SIQ_HERMES_ANALYSIS_OPENSHELL_CANARY_SESSION_IDS", raising=False)
    with pytest.raises(
        hermes_client.HermesRuntimeSelectionError,
        match="openshell_canary_session_not_authorized",
    ):
        hermes_client.normalize_runtime_target("siq_analysis", "openshell", session_id="session-a")

    monkeypatch.setenv("SIQ_HERMES_ANALYSIS_OPENSHELL_CANARY_SESSION_IDS", "session-b")
    with pytest.raises(
        hermes_client.HermesRuntimeSelectionError,
        match="openshell_canary_session_not_authorized",
    ):
        hermes_client.normalize_runtime_target("siq_analysis", "openshell", session_id="session-a")
    assert (
        hermes_client.normalize_runtime_target("siq_analysis", "openshell", session_id="session-b")
        == "openshell"
    )


def test_canary_route_is_bound_to_private_active_state_and_separate_namespace(tmp_path, monkeypatch):
    _write_canary_state(tmp_path)
    monkeypatch.setattr(hermes_client, "_project_root", lambda: tmp_path)
    monkeypatch.setenv("SIQ_HERMES_ANALYSIS_OPENSHELL_CANARY_ENABLED", "1")
    monkeypatch.setenv("SIQ_HERMES_ANALYSIS_OPENSHELL_CANARY_SESSION_IDS", "analysis-session")

    route = hermes_client.resolve_run_route(
        "siq_analysis",
        "openshell",
        session_id="analysis-session",
        context=_company_context(),
    )

    assert route.target == "openshell"
    assert route.base == "http://127.0.0.1:28651/v1/runs"
    assert route.canary_run_id == "canary-0123456789ab"
    assert route.authorization == "Bearer " + "a" * 64
    assert (
        hermes_client.route_session_id(route, "siq_analysis", "analysis-session")
        == "siq:openshell:canary-0123456789ab:siq_analysis:us:"
        "e9563115951142ee:analysis-session"
    )
    assert "a" * 64 not in repr(route)


def test_pool_registry_route_is_resolved_once_for_the_matching_company(tmp_path, monkeypatch):
    registry = tmp_path / hermes_client.OPENSHELL_POOL_REGISTRY_RELATIVE
    registry.parent.mkdir(parents=True)
    for directory in (
        tmp_path / "var",
        tmp_path / "var/openshell",
        tmp_path / "var/openshell/canary",
        tmp_path / "var/openshell/canary/siq-analysis",
        registry.parent,
    ):
        directory.chmod(0o700)
    registry.write_text("{}\n", encoding="ascii")
    registry.chmod(0o600)
    binding = openshell_pool_adapter.ResolvedPoolBinding(
        target="openshell",
        market="cn",
        company="600104-上汽集团",
        scope_id="9bc20683a73220cad2e19d40",
        run_id="canary-0123456789ab",
        base="http://127.0.0.1:28652/v1/runs",
        api_key="b" * 64,
        session_namespace="siq:openshell:pool:scope:canary-0123456789ab:siq_analysis",
        analysis_relative_path="data/wiki/companies/600104-上汽集团/analysis",
    )

    class FakeAdapter:
        def __init__(self, *, project_root):
            assert project_root == tmp_path

        def resolve_binding(self, context):
            assert context["company"]["code"] == "600104"
            return binding

    monkeypatch.setattr(hermes_client, "_project_root", lambda: tmp_path)
    monkeypatch.setattr(hermes_client.openshell_pool_adapter, "OpenShellPoolAdapter", FakeAdapter)
    monkeypatch.setenv("SIQ_HERMES_RUNTIME", "openshell")
    monkeypatch.setenv("SIQ_HERMES_ANALYSIS_OPENSHELL_CANARY_ENABLED", "1")
    monkeypatch.setenv("SIQ_HERMES_ANALYSIS_OPENSHELL_CANARY_SESSION_MODE", "all")

    route = hermes_client.resolve_run_route(
        "siq_analysis",
        "openshell",
        session_id="analysis-session",
        context={"company": {"market": "CN", "code": "600104", "name": "上汽集团"}},
    )

    assert route.base == binding.base
    assert route.canary_run_id == binding.run_id
    assert route.pool_binding is binding
    assert route.pool_market == "cn"
    assert route.pool_company == "600104-上汽集团"
    assert "b" * 64 not in repr(route)


def test_pool_context_conflict_falls_back_only_for_implicit_runtime_selection(tmp_path, monkeypatch):
    registry = tmp_path / hermes_client.OPENSHELL_POOL_REGISTRY_RELATIVE
    registry.parent.mkdir(parents=True)
    for directory in (
        tmp_path / "var",
        tmp_path / "var/openshell",
        tmp_path / "var/openshell/canary",
        tmp_path / "var/openshell/canary/siq-analysis",
        registry.parent,
    ):
        directory.chmod(0o700)
    registry.write_text("{}\n", encoding="ascii")
    registry.chmod(0o600)

    class ConflictingAdapter:
        def __init__(self, *, project_root):
            assert project_root == tmp_path

        def resolve_binding(self, context):
            raise openshell_pool_adapter.OpenShellPoolAdapterError(
                "openshell_pool_context_company_conflict",
            )

    monkeypatch.setattr(hermes_client, "_project_root", lambda: tmp_path)
    monkeypatch.setattr(hermes_client.openshell_pool_adapter, "OpenShellPoolAdapter", ConflictingAdapter)
    monkeypatch.setenv("SIQ_HERMES_RUNTIME", "openshell")
    monkeypatch.setenv("SIQ_HERMES_ANALYSIS_OPENSHELL_CANARY_ENABLED", "1")
    monkeypatch.setenv("SIQ_HERMES_ANALYSIS_OPENSHELL_CANARY_SESSION_MODE", "all")

    assert (
        hermes_client.resolve_requested_run_route(
            "siq_analysis",
            None,
            session_id="analysis-session",
            context={"company": {"market": "CN", "dir": "600104-甲", "code": "600104", "name": "乙"}},
        )
        is None
    )
    with pytest.raises(
        hermes_client.HermesRuntimeSelectionError,
        match="openshell_pool_context_company_conflict",
    ):
        hermes_client.resolve_requested_run_route(
            "siq_analysis",
            "openshell",
            session_id="analysis-session",
            context={"company": {"market": "CN", "dir": "600104-甲", "code": "600104", "name": "乙"}},
        )


def test_async_route_provisions_company_scope_before_resolving(monkeypatch):
    provisioned = False
    expected = _canary_route()

    monkeypatch.setattr(runtime, "normalize_runtime_target", lambda *_args, **_kwargs: "openshell")

    async def fake_ensure(context):
        nonlocal provisioned
        assert context["company"]["dir"] == "600519-贵州茅台"
        provisioned = True

    def fake_route(profile, runtime_target, session_id, context):
        assert profile == "siq_analysis"
        assert runtime_target is None
        assert session_id == "analysis-session"
        return expected if provisioned else None

    monkeypatch.setattr(runtime.openshell_scope_lifecycle, "ensure_binding", fake_ensure)
    monkeypatch.setattr(runtime, "_requested_run_route", fake_route)

    route = anyio.run(
        runtime._requested_run_route_with_scope_lifecycle,
        "siq_analysis",
        None,
        "analysis-session",
        {"company": {"market": "cn", "dir": "600519-贵州茅台"}},
    )

    assert route is expected


def test_async_route_keeps_implicit_host_fallback_when_provisioning_fails(monkeypatch):
    monkeypatch.setattr(runtime, "normalize_runtime_target", lambda *_args, **_kwargs: "openshell")

    async def fake_ensure(_context):
        raise runtime.openshell_scope_lifecycle.OpenShellScopeLifecycleError(
            "openshell_scope_start_failed"
        )

    monkeypatch.setattr(runtime.openshell_scope_lifecycle, "ensure_binding", fake_ensure)
    monkeypatch.setattr(runtime, "_requested_run_route", lambda *_args, **_kwargs: None)

    route = anyio.run(
        runtime._requested_run_route_with_scope_lifecycle,
        "siq_analysis",
        None,
        "analysis-session",
        {"company": {"market": "cn", "dir": "600519-贵州茅台"}},
    )

    assert route is None


def test_pool_admission_binds_affinity_namespace_and_releases_only_with_terminal_receipt(monkeypatch):
    binding = openshell_pool_adapter.ResolvedPoolBinding(
        target="openshell",
        market="cn",
        company="600104-上汽集团",
        run_id="canary-0123456789ab",
    )
    route = hermes_client.HermesRunRoute(
        target="openshell",
        base="http://127.0.0.1:28652/v1/runs",
        model="siq_analysis",
        authorization="Bearer " + "b" * 64,
        session_namespace="pool-base",
        canary_run_id=binding.run_id,
        pool_binding=binding,
        pool_market=binding.market,
        pool_company=binding.company,
    )
    admission = openshell_pool_adapter.PoolAdapterAdmission(
        status="active",
        target="openshell",
        market=binding.market,
        company=binding.company,
        lease_id="lease-123",
        owner_token="owner-" + "1" * 64,
        owner_generation=7,
        base=route.base,
        api_key="c" * 64,
        run_id=binding.run_id,
        session_namespace="pool-affinity-hash",
        write_relative_path="data/wiki/companies/600104-上汽集团/analysis/.work/lease-123",
    )
    released = []

    async def fake_acquire(*args, **kwargs):
        assert args == (binding,)
        assert kwargs["session_id"] == "user-1-analysis-session"
        assert kwargs["tenant_id"] == "default"
        assert kwargs["user_id"] == "1"
        return admission

    async def fake_release(**kwargs):
        released.append(kwargs)
        return True

    monkeypatch.setattr(runtime.openshell_pool_adapter, "acquire_wait_async", fake_acquire)
    monkeypatch.setattr(runtime.openshell_pool_adapter, "release_async", fake_release)

    async def run_case():
        admitted = await runtime._acquire_pool_route(
            route,
            session_id="user-1-analysis-session",
            tenant_id=" default ",
            user_id=" 1 ",
        )
        await runtime._release_pool_route(
            admitted,
            session_id="user-1-analysis-session",
            terminal_confirmed=True,
        )
        return admitted

    admitted = anyio.run(run_case)

    assert admitted is not None
    assert runtime._runtime_research_identity_scope(
        {"company": {"market": "CN", "code": "600104"}},
        admitted,
    ) == {"market": "CN", "company_id": "600104"}
    assert admitted.pool_lease_id == "lease-123"
    assert admitted.pool_owner_token == "owner-" + "1" * 64
    assert admitted.pool_owner_generation == 7
    assert admitted.pool_tenant_id == "default"
    assert admitted.pool_user_id == "1"
    assert admitted.authorization == "Bearer " + "c" * 64
    assert hermes_client.route_session_id(
        admitted,
        "siq_analysis",
        "user-1-analysis-session",
    ) == "pool-affinity-hash"
    assert released == [
        {
            "session_id": "user-1-analysis-session",
            "tenant_id": "default",
            "user_id": "1",
            "owner_token": "owner-" + "1" * 64,
            "owner_generation": 7,
            "terminal_confirmed": True,
        }
    ]


def test_runtime_provenance_tracks_conversation_sandbox_generations():
    first_binding = openshell_pool_adapter.ResolvedPoolBinding(
        target="openshell",
        market="cn",
        company="600104-上汽集团",
        scope_id="9bc20683a73220cad2e19d40",
        run_id="canary-0123456789ab",
    )
    second_binding = openshell_pool_adapter.ResolvedPoolBinding(
        target="openshell",
        market="cn",
        company="600519-贵州茅台",
        scope_id="7025f3f8b5186fe8a87f8a12",
        run_id="canary-abcdef012345",
    )
    first_route = hermes_client.HermesRunRoute(
        target="openshell",
        base="http://127.0.0.1:28652/v1/runs",
        model="siq_analysis",
        authorization="Bearer " + "a" * 64,
        session_namespace="scope-a:conversation-affinity",
        canary_run_id=first_binding.run_id,
        pool_binding=first_binding,
        pool_lease_id="lease-a",
        pool_company=first_binding.company,
    )
    second_route = hermes_client.HermesRunRoute(
        target="openshell",
        base="http://127.0.0.1:28654/v1/runs",
        model="siq_analysis",
        authorization="Bearer " + "b" * 64,
        session_namespace="scope-b:conversation-affinity",
        canary_run_id=second_binding.run_id,
        pool_binding=second_binding,
        pool_lease_id="lease-b",
        pool_company=second_binding.company,
    )

    first = runtime._runtime_provenance(first_route)
    repeated = runtime._runtime_provenance(first_route)
    switched = runtime._runtime_provenance(second_route)

    assert first["sandbox_generation_id"] == repeated["sandbox_generation_id"]
    assert first["sandbox_generation_id"] != switched["sandbox_generation_id"]
    assert first["sandbox_scope_id"] == first_binding.scope_id
    assert switched["sandbox_scope_id"] == second_binding.scope_id
    assert first["sandbox_company"] == first_binding.company
    assert switched["sandbox_company"] == second_binding.company


def test_pool_admission_rejects_explicit_user_session_mismatch_before_scheduler(monkeypatch):
    route = hermes_client.HermesRunRoute(
        target="openshell",
        base="http://127.0.0.1:28652/v1/runs",
        model="siq_analysis",
        authorization="Bearer " + "b" * 64,
        session_namespace="pool-base",
        canary_run_id="canary-0123456789ab",
        pool_binding=object(),
    )

    async def forbidden_acquire(*_args, **_kwargs):
        raise AssertionError("principal mismatch must fail before pool acquire")

    monkeypatch.setattr(runtime.openshell_pool_adapter, "acquire_wait_async", forbidden_acquire)

    async def run_case():
        with pytest.raises(RuntimeError, match="^openshell_pool_principal_session_mismatch$"):
            await runtime._acquire_pool_route(
                route,
                session_id="user-101-analysis-session",
                tenant_id="default",
                user_id="202",
            )

    anyio.run(run_case)


@pytest.mark.parametrize(
    ("tenant_id", "user_id", "error_code"),
    [
        (None, None, "openshell_pool_principal_incomplete"),
        ("default", None, "openshell_pool_principal_incomplete"),
        (None, "101", "openshell_pool_principal_incomplete"),
        (" ", "101", "openshell_pool_principal_invalid"),
        ("default", "01", "openshell_pool_principal_invalid"),
        ("default", "101-analysis", "openshell_pool_principal_invalid"),
        ("default", "1" * 21, "openshell_pool_principal_invalid"),
        ("default\u0000other", "101", "openshell_pool_principal_invalid"),
    ],
)
def test_pool_admission_rejects_incomplete_or_blank_principal_before_scheduler(
    monkeypatch,
    tenant_id,
    user_id,
    error_code,
):
    route = hermes_client.HermesRunRoute(
        target="openshell",
        base="http://127.0.0.1:28652/v1/runs",
        model="siq_analysis",
        authorization="Bearer " + "b" * 64,
        session_namespace="pool-base",
        canary_run_id="canary-0123456789ab",
        pool_binding=object(),
    )

    async def forbidden_acquire(*_args, **_kwargs):
        raise AssertionError("invalid principal must fail before pool acquire")

    monkeypatch.setattr(runtime.openshell_pool_adapter, "acquire_wait_async", forbidden_acquire)

    async def run_case():
        with pytest.raises(RuntimeError, match=f"^{error_code}$"):
            await runtime._acquire_pool_route(
                route,
                session_id="user-101-analysis-session",
                tenant_id=tenant_id,
                user_id=user_id,
            )

    anyio.run(run_case)


def test_pool_admission_rejects_cross_profile_session_for_authenticated_user(monkeypatch):
    route = hermes_client.HermesRunRoute(
        target="openshell",
        base="http://127.0.0.1:28652/v1/runs",
        model="siq_analysis",
        authorization="Bearer " + "b" * 64,
        session_namespace="pool-base",
        canary_run_id="canary-0123456789ab",
        pool_binding=object(),
    )

    async def forbidden_acquire(*_args, **_kwargs):
        raise AssertionError("cross-profile session must fail before pool acquire")

    monkeypatch.setattr(runtime.openshell_pool_adapter, "acquire_wait_async", forbidden_acquire)

    async def run_case():
        with pytest.raises(RuntimeError, match="^openshell_pool_principal_session_mismatch$"):
            await runtime._acquire_pool_route(
                route,
                session_id="user-101-factchecker-session",
                tenant_id="default",
                user_id="101",
            )

    anyio.run(run_case)


def test_runtime_file_hot_switch_overrides_environment_and_preserves_other_profiles(tmp_path, monkeypatch):
    _write_canary_state(tmp_path)
    _write_runtime_selection(tmp_path, target="openshell")
    monkeypatch.setattr(hermes_client, "_project_root", lambda: tmp_path)
    monkeypatch.setenv("SIQ_HERMES_RUNTIME_SELECTION_ENABLED", "1")
    monkeypatch.setenv("SIQ_HERMES_RUNTIME", "host")

    assert hermes_client.normalize_runtime_target("siq_analysis", None, session_id="new") == "openshell"
    assert hermes_client.normalize_runtime_target("siq_factchecker", None, session_id="new") == "host"

    _write_runtime_selection(tmp_path, target="host", session_mode="allowlist")
    assert hermes_client.normalize_runtime_target("siq_analysis", None, session_id="new") == "host"
    with pytest.raises(hermes_client.HermesRuntimeSelectionError, match="not_enabled"):
        hermes_client.normalize_runtime_target("siq_analysis", "openshell", session_id="new")


def test_runtime_file_is_ignored_unless_this_api_process_explicitly_enables_it(tmp_path, monkeypatch):
    _write_canary_state(tmp_path)
    _write_runtime_selection(tmp_path, target="openshell")
    monkeypatch.setattr(hermes_client, "_project_root", lambda: tmp_path)
    monkeypatch.delenv("SIQ_HERMES_RUNTIME_SELECTION_ENABLED", raising=False)
    monkeypatch.setenv("SIQ_HERMES_RUNTIME", "host")

    assert hermes_client.normalize_runtime_target("siq_analysis", None, session_id="new") == "host"


def test_invalid_runtime_file_cannot_break_explicit_host_or_other_profiles(tmp_path, monkeypatch):
    state = _write_runtime_selection(tmp_path, target="openshell")
    state.write_text('{"target":"corrupt"}\n', encoding="ascii")
    state.chmod(0o600)
    monkeypatch.setattr(hermes_client, "_project_root", lambda: tmp_path)
    monkeypatch.setenv("SIQ_HERMES_RUNTIME_SELECTION_ENABLED", "1")

    assert hermes_client.normalize_runtime_target("siq_analysis", "host", session_id="new") == "host"
    assert hermes_client.normalize_runtime_target("siq_factchecker", None, session_id="new") == "host"
    assert hermes_client.normalize_runtime_target("siq_ic_chairman", "openshell", session_id="new") == "host"
    with pytest.raises(hermes_client.HermesRuntimeSelectionError, match="selection_invalid"):
        hermes_client.normalize_runtime_target("siq_analysis", None, session_id="new")


def test_company_scope_mismatch_falls_back_only_for_implicit_selection(tmp_path, monkeypatch):
    _write_canary_state(tmp_path)
    _write_runtime_selection(tmp_path, target="openshell")
    monkeypatch.setattr(hermes_client, "_project_root", lambda: tmp_path)
    monkeypatch.setenv("SIQ_HERMES_RUNTIME_SELECTION_ENABLED", "1")

    mismatched = _company_context(company="other", market="us")
    assert runtime._requested_run_route("siq_analysis", None, "new-session", mismatched) is None
    with pytest.raises(
        hermes_client.HermesRuntimeSelectionError,
        match="company_not_authorized",
    ):
        runtime._requested_run_route("siq_analysis", "openshell", "new-session", mismatched)

    route = runtime._requested_run_route(
        "siq_analysis",
        None,
        "new-session",
        _company_context(),
    )
    assert route is not None
    assert route.target == "openshell"


def test_implicit_openshell_preference_uses_host_until_canary_exists(tmp_path, monkeypatch):
    (tmp_path / "var" / "openshell").mkdir(parents=True, mode=0o700)
    _write_runtime_selection(tmp_path, target="openshell")
    monkeypatch.setattr(hermes_client, "_project_root", lambda: tmp_path)
    monkeypatch.setenv("SIQ_HERMES_RUNTIME_SELECTION_ENABLED", "1")

    assert runtime._requested_run_route(
        "siq_analysis",
        None,
        "new-session",
        _company_context(),
    ) is None
    with pytest.raises(hermes_client.HermesRuntimeSelectionError, match="not_active"):
        runtime._requested_run_route(
            "siq_analysis",
            "openshell",
            "new-session",
            _company_context(),
        )


def test_canary_route_rejects_world_readable_key_and_symlinked_active_state(tmp_path, monkeypatch):
    active, key_path = _write_canary_state(tmp_path)
    monkeypatch.setattr(hermes_client, "_project_root", lambda: tmp_path)
    monkeypatch.setenv("SIQ_HERMES_ANALYSIS_OPENSHELL_CANARY_ENABLED", "1")
    monkeypatch.setenv("SIQ_HERMES_ANALYSIS_OPENSHELL_CANARY_SESSION_IDS", "session")
    key_path.chmod(0o644)

    with pytest.raises(hermes_client.HermesRuntimeSelectionError):
        hermes_client.resolve_run_route(
            "siq_analysis",
            "openshell",
            session_id="session",
            context=_company_context(),
        )

    key_path.chmod(0o600)
    original = active.with_suffix(".original")
    active.rename(original)
    active.symlink_to(original.name)
    with pytest.raises(hermes_client.HermesRuntimeSelectionError):
        hermes_client.resolve_run_route(
            "siq_analysis",
            "openshell",
            session_id="session",
            context=_company_context(),
        )


def test_canary_route_rejects_key_that_no_longer_matches_manifest(tmp_path, monkeypatch):
    _, key_path = _write_canary_state(tmp_path)
    monkeypatch.setattr(hermes_client, "_project_root", lambda: tmp_path)
    monkeypatch.setenv("SIQ_HERMES_ANALYSIS_OPENSHELL_CANARY_ENABLED", "1")
    monkeypatch.setenv("SIQ_HERMES_ANALYSIS_OPENSHELL_CANARY_SESSION_IDS", "session")
    key_path.write_text("b" * 64 + "\n", encoding="ascii")
    key_path.chmod(0o600)

    with pytest.raises(hermes_client.HermesRuntimeSelectionError, match="api_key_mismatch"):
        hermes_client.resolve_run_route(
            "siq_analysis",
            "openshell",
            session_id="session",
            context=_company_context(),
        )


def test_host_create_keeps_legacy_call_shape_and_session_namespace(monkeypatch):
    calls = []

    async def fake_create(run_input, history, *, profile, session_id):
        calls.append((run_input, history, profile, session_id))
        return "host-run"

    async def run_case():
        monkeypatch.setattr(runtime, "create_run", fake_create)
        return await runtime._create_routed_run(
            "same input",
            [{"role": "user", "content": "same history"}],
            profile="siq_analysis",
            session_id="session-a",
            route=None,
        )

    result = anyio.run(run_case)
    assert result == ("host-run", None)
    assert calls == [
        (
            "same input",
            [{"role": "user", "content": "same history"}],
            "siq_analysis",
            "siq:siq_analysis:session-a",
        )
    ]


def test_canary_create_preserves_business_payload_and_pins_route(monkeypatch):
    route = _canary_route()
    calls = []

    async def fake_create(run_input, history, *, profile, session_id, route):
        calls.append((run_input, history, profile, session_id, route))
        return "canary-run"

    async def run_case():
        monkeypatch.setattr(runtime, "create_run", fake_create)
        return await runtime._create_routed_run(
            "unchanged prompt and tool contract",
            [{"role": "assistant", "content": "unchanged memory"}],
            profile="siq_analysis",
            session_id="session-a",
            route=route,
        )

    result = anyio.run(run_case)
    assert result == ("canary-run", route)
    assert calls == [
        (
            "unchanged prompt and tool contract",
            [{"role": "assistant", "content": "unchanged memory"}],
            "siq_analysis",
            "siq:openshell:canary-0123456789ab:siq_analysis:session-a",
            route,
        )
    ]


def test_canary_preconnect_failure_fails_closed_without_host_replay(monkeypatch):
    route = _canary_route()
    calls = []

    async def fake_create(run_input, history, *, profile, session_id, **kwargs):
        calls.append((run_input, history, profile, session_id, kwargs.get("route")))
        raise httpx.ConnectError("connection refused", request=httpx.Request("POST", route.base))

    async def run_case():
        monkeypatch.setattr(runtime, "create_run", fake_create)
        with pytest.raises(httpx.ConnectError):
            await runtime._create_routed_run(
                "input",
                [],
                profile="siq_analysis",
                session_id="session-a",
                route=route,
            )

    anyio.run(run_case)
    assert [call[3] for call in calls] == [
        "siq:openshell:canary-0123456789ab:siq_analysis:session-a",
    ]
    assert [call[4] for call in calls] == [route]


@pytest.mark.parametrize("failure_kind", ["http_status", "read_timeout"])
def test_canary_does_not_replay_after_request_may_have_reached_runtime(monkeypatch, failure_kind):
    route = _canary_route()
    calls = []

    async def fake_create(*_args, **kwargs):
        calls.append(kwargs)
        request = httpx.Request("POST", route.base)
        if failure_kind == "http_status":
            response = httpx.Response(503, request=request)
            raise httpx.HTTPStatusError("unavailable", request=request, response=response)
        raise httpx.ReadTimeout("response timeout", request=request)

    async def run_case():
        monkeypatch.setattr(runtime, "create_run", fake_create)
        with pytest.raises((httpx.HTTPStatusError, httpx.ReadTimeout)):
            await runtime._create_routed_run(
                "input",
                [],
                profile="siq_analysis",
                session_id="session-a",
                route=route,
            )

    anyio.run(run_case)
    assert len(calls) == 1
    assert calls[0]["route"] is route


def test_canary_stream_and_stop_keep_the_route_without_recreating_run(monkeypatch):
    route = _canary_route()
    stream_calls = []
    stop_calls = []
    create_calls = []

    async def fake_stream(run_id, *, profile, timeout, route):
        stream_calls.append((run_id, profile, timeout, route))
        yield hermes_client.StreamEvent(type="done", text="ok", status="completed")

    async def fake_stop(run_id, *, profile, route):
        stop_calls.append((run_id, profile, route))
        return {"status": "cancelled"}

    async def fake_create(*args, **kwargs):
        create_calls.append((args, kwargs))
        raise AssertionError("stream/stop must never replay create")

    async def run_case():
        monkeypatch.setattr(runtime, "stream_run", fake_stream)
        monkeypatch.setattr(runtime, "stop_run", fake_stop)
        monkeypatch.setattr(runtime, "create_run", fake_create)
        events = []
        async for event in runtime._stream_routed_run(
            "canary-hermes-run",
            profile="siq_analysis",
            timeout=12.0,
            route=route,
        ):
            events.append(event)

        state = agent_runtime_streaming.ActiveRunState(
            profile="siq_analysis",
            session_id="session-a",
            run_id="canary-hermes-run",
            run_route=route,
        )
        key = agent_runtime_streaming._active_key(state.profile, state.session_id)
        agent_runtime_streaming.ACTIVE_RUNS[key] = state
        try:
            stopped = await agent_runtime_streaming.stop_active_run(
                state.profile,
                state.session_id,
                stop_run_call=fake_stop,
                stopped_message="stopped",
                orphaned_run_message="orphaned",
            )
        finally:
            agent_runtime_streaming.ACTIVE_RUNS.pop(key, None)
        return events, stopped

    events, stopped = anyio.run(run_case)
    assert [event.type for event in events] == ["done"]
    assert stream_calls == [("canary-hermes-run", "siq_analysis", 12.0, route)]
    assert stop_calls == [("canary-hermes-run", "siq_analysis", route)]
    assert stopped["stopped"] is True
    assert create_calls == []


def test_pool_stop_ack_does_not_release_without_executor_quiescence(monkeypatch):
    base = _canary_route()
    route = hermes_client.HermesRunRoute(
        **{
            **base.__dict__,
            "pool_binding": object(),
            "pool_lease_id": "lease-runtime-stop",
        }
    )
    stop_calls = []

    async def fake_stop(run_id, *, profile, route):
        stop_calls.append((run_id, profile, route))
        return {"run_id": run_id, "status": "stopping"}

    async def fake_status(run_id, *, profile, route):
        return hermes_client.HermesRunStatus(
            run_id=run_id,
            status="cancelled",
            quiesced=False,
        )

    monkeypatch.setattr(runtime, "_stop_routed_run", fake_stop)
    monkeypatch.setattr(runtime, "_get_routed_run_status", fake_status)

    async def run_case():
        return await runtime._stop_and_confirm_routed_run(
            "hermes-run",
            profile="siq_analysis",
            route=route,
            timeout_seconds=0.01,
        )

    confirmed = anyio.run(run_case)

    assert confirmed is False
    assert stop_calls == [("hermes-run", "siq_analysis", route)]


def test_pool_stop_releases_after_completed_status_receipt(monkeypatch):
    base = _canary_route()
    route = hermes_client.HermesRunRoute(
        **{
            **base.__dict__,
            "pool_binding": object(),
            "pool_lease_id": "lease-runtime-stop",
        }
    )

    async def fake_stop(run_id, *, profile, route):
        return {"run_id": run_id, "status": "stopping"}

    async def fake_status(run_id, *, profile, route):
        return hermes_client.HermesRunStatus(
            run_id=run_id,
            status="completed",
            quiesced=True,
        )

    monkeypatch.setattr(runtime, "_stop_routed_run", fake_stop)
    monkeypatch.setattr(runtime, "_get_routed_run_status", fake_status)

    async def run_case():
        return await runtime._stop_and_confirm_routed_run(
            "hermes-run",
            profile="siq_analysis",
            route=route,
            timeout_seconds=0.01,
        )

    confirmed = anyio.run(run_case)

    assert confirmed is True


def test_durable_session_claim_precedes_pool_admission(monkeypatch):
    route = _canary_route()
    order = []

    async def fake_claim(*_args, **_kwargs):
        order.append("durable_claim")
        return True

    async def fake_acquire(current, *, session_id):
        order.append("pool_acquire")
        return current

    async def fake_mark_bound(current, *, session_id):
        order.append("pool_mark_bound")
        return current

    async def fake_create(*_args, **kwargs):
        order.append("hermes_create")
        return "hermes-run", kwargs["route"]

    async def fake_bind(*_args, **_kwargs):
        order.append("durable_bind")
        return True

    monkeypatch.setattr(runtime, "_claim_durable_active_run", fake_claim)
    monkeypatch.setattr(runtime, "_acquire_pool_route", fake_acquire)
    monkeypatch.setattr(runtime, "_mark_pool_route_bound", fake_mark_bound)
    monkeypatch.setattr(runtime, "_create_routed_run", fake_create)
    monkeypatch.setattr(runtime, "_bind_durable_active_run", fake_bind)

    async def run_case():
        return await runtime._claim_create_and_bind_routed_run(
            "input",
            [],
            profile="siq_analysis",
            session_id="session-order",
            route=route,
        )

    claimed = anyio.run(run_case)

    assert claimed is not None
    assert claimed[:2] == ("hermes-run", route)
    assert order == [
        "durable_claim",
        "pool_acquire",
        "pool_mark_bound",
        "hermes_create",
        "durable_bind",
    ]


def test_durable_session_conflict_never_touches_pool(monkeypatch):
    calls = []

    async def fake_claim(*_args, **_kwargs):
        calls.append("durable_claim")
        return False

    async def forbidden_acquire(*_args, **_kwargs):
        raise AssertionError("pool admission must follow the durable session claim")

    monkeypatch.setattr(runtime, "_claim_durable_active_run", fake_claim)
    monkeypatch.setattr(runtime, "_acquire_pool_route", forbidden_acquire)

    async def run_case():
        return await runtime._claim_create_and_bind_routed_run(
            "input",
            [],
            profile="siq_analysis",
            session_id="session-conflict",
            route=_canary_route(),
        )

    assert anyio.run(run_case) is None
    assert calls == ["durable_claim"]


def test_uncertain_create_failure_orphans_pool_lease(monkeypatch):
    route = _canary_route()
    admitted_route = hermes_client.HermesRunRoute(
        **{
            **route.__dict__,
            "pool_binding": object(),
            "pool_lease_id": "lease-uncertain-create",
            "pool_owner_token": "owner-" + "2" * 64,
            "pool_owner_generation": 2,
        }
    )
    cleanup = []

    async def fake_claim(*_args, **_kwargs):
        return True

    async def fake_acquire(*_args, **_kwargs):
        return admitted_route

    async def fake_mark_bound(*_args, **_kwargs):
        return admitted_route

    async def fake_attach(*_args, **_kwargs):
        return True

    async def fake_create(*_args, **_kwargs):
        request = httpx.Request("POST", admitted_route.base)
        raise httpx.ReadTimeout("response lost", request=request)

    async def fake_durable_cleanup(*_args, **_kwargs):
        cleanup.append(("durable", _kwargs))

    async def fake_pool_cleanup(route, *, session_id, terminal_confirmed):
        cleanup.append(("pool", route, session_id, terminal_confirmed))

    monkeypatch.setattr(runtime, "_claim_durable_active_run", fake_claim)
    monkeypatch.setattr(runtime, "_acquire_pool_route", fake_acquire)
    monkeypatch.setattr(runtime, "_attach_durable_pool_lease", fake_attach)
    monkeypatch.setattr(runtime, "_mark_pool_route_bound", fake_mark_bound)
    monkeypatch.setattr(runtime, "_create_routed_run", fake_create)
    monkeypatch.setattr(runtime, "_release_provisional_durable_claim", fake_durable_cleanup)
    monkeypatch.setattr(runtime, "_release_pool_route", fake_pool_cleanup)

    async def run_case():
        await runtime._claim_create_and_bind_routed_run(
            "input",
            [],
            profile="siq_analysis",
            session_id="session-uncertain-create",
            route=route,
        )

    with pytest.raises(httpx.ReadTimeout):
        anyio.run(run_case)
    assert cleanup[-1] == (
        "pool",
        admitted_route,
        "session-uncertain-create",
        False,
    )
