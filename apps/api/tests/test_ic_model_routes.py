"""Model-backed IC workflow route wiring tests."""


from __future__ import annotations

import asyncio
from types import SimpleNamespace

from routers import deals


def _user() -> SimpleNamespace:
    return SimpleNamespace(id=7, username="ic-admin")


def test_r2_route_calls_model_wrapper_for_explicit_model_mode(monkeypatch):
    calls = []

    async def fake_run(deal_id, **kwargs):
        calls.append((deal_id, kwargs))
        return {"phase": "R2", "hermes_called": True, "generation_mode": "hermes_model"}

    monkeypatch.setattr(deals, "require_deal_access", lambda *_args: None)
    monkeypatch.setattr(deals.ic_agent_runtime, "run_workflow_r2_async", fake_run)
    result = asyncio.run(deals.post_workflow_run_r2(
        "DEAL-MODEL-ROUTE-001",
        deals.WorkflowRunR2Request(dry_run=False, mode="model", timeout=42),
        current_user=_user(),
    ))
    assert result["hermes_called"] is True
    assert calls[0][1] == {
        "mode": "model",
        "timeout": 42.0,
        "created_by": {"id": 7, "username": "ic-admin"},
    }


def test_r3_and_r4_routes_forward_explicit_modes(monkeypatch):
    calls = []

    async def fake_r3(deal_id, **kwargs):
        calls.append(("R3", deal_id, kwargs))
        return {"phase": "R3", "hermes_called": True}

    async def fake_r4(deal_id, **kwargs):
        calls.append(("R4", deal_id, kwargs))
        return {"phase": "R4", "hermes_called": False, "fallback": True}

    monkeypatch.setattr(deals, "require_deal_access", lambda *_args: None)
    monkeypatch.setattr(deals.ic_agent_runtime, "run_workflow_r3_async", fake_r3)
    monkeypatch.setattr(deals.ic_agent_runtime, "finalize_workflow_r4_async", fake_r4)
    r3 = asyncio.run(deals.post_workflow_run_r3(
        "DEAL-MODEL-ROUTE-001",
        deals.WorkflowRunR3Request(dry_run=False, mode="model", timeout=60),
        current_user=_user(),
    ))
    r4 = asyncio.run(deals.post_workflow_finalize_r4(
        "DEAL-MODEL-ROUTE-001",
        deals.WorkflowFinalizeR4Request(dry_run=False, mode="deterministic_fallback", overwrite=True),
        current_user=_user(),
    ))
    assert r3["hermes_called"] is True
    assert r4["fallback"] is True
    assert calls[0][2]["mode"] == "model"
    assert calls[1][2]["mode"] == "deterministic_fallback"


def test_r15_chairman_route_calls_real_model_wrapper(monkeypatch):
    calls = []

    async def fake_run(deal_id, **kwargs):
        calls.append((deal_id, kwargs))
        return {"phase": "R1.5", "hermes_called": True, "rulings": []}

    monkeypatch.setattr(deals, "require_deal_access", lambda *_args: None)
    monkeypatch.setattr(deals.ic_agent_runtime, "run_workflow_r1_5_model", fake_run)
    result = asyncio.run(deals.post_workflow_run_r1_5_chairman(
        "DEAL-MODEL-ROUTE-001",
        deals.WorkflowRunR15ChairmanRequest(dry_run=False, mode="model", overwrite=True, timeout=90),
        current_user=_user(),
    ))
    assert result["hermes_called"] is True
    assert calls[0][1]["overwrite"] is True
    assert calls[0][1]["timeout"] == 90.0
