import asyncio

from services import system_status


HERMES_PROFILES = {
    "siq_assistant": {"base": "http://127.0.0.1:18642/v1/runs"},
    "siq_analysis": {"base": "http://127.0.0.1:18651/v1/runs"},
    "siq_factchecker": {"base": "http://127.0.0.1:18649/v1/runs"},
    "siq_tracking": {"base": "http://127.0.0.1:18650/v1/runs"},
    "siq_legal": {"base": "http://127.0.0.1:18652/v1/runs"},
    "siq_ic_master_coordinator": {"base": "http://127.0.0.1:18660/v1/runs"},
    "siq_ic_chairman": {"base": "http://127.0.0.1:18661/v1/runs"},
    "siq_ic_strategist": {"base": "http://127.0.0.1:18662/v1/runs"},
    "siq_ic_sector_expert": {"base": "http://127.0.0.1:18663/v1/runs"},
    "siq_ic_finance_auditor": {"base": "http://127.0.0.1:18664/v1/runs"},
    "siq_ic_legal_scanner": {"base": "http://127.0.0.1:18665/v1/runs"},
    "siq_ic_risk_controller": {"base": "http://127.0.0.1:18666/v1/runs"},
}


def _wiki_ok() -> dict:
    return {
        "root": "/tmp/wiki",
        "companiesDir": "/tmp/wiki/companies",
        "exists": True,
        "companyCount": 1,
        "generatedResultCount": 2,
    }


def _model_stub() -> dict:
    return {
        "activeProvider": "local",
        "activeProviderName": "local",
        "activeModel": "test-model",
        "activeBaseUrl": "http://127.0.0.1:8000/v1",
        "providers": {},
        "hermesProfiles": {},
        "note": "",
    }


def _install_status_stubs(monkeypatch, failing_ids: set[str] | None = None) -> list[str]:
    calls: list[str] = []
    failing_ids = failing_ids or set()

    monkeypatch.setattr(system_status, "HERMES_PROFILES", HERMES_PROFILES)
    monkeypatch.setattr(system_status, "_wiki_status", _wiki_ok)
    monkeypatch.setattr(system_status, "_model_status", _model_stub)

    async def fake_probe_service(
        client,
        *,
        service_id: str,
        name: str,
        category: str,
        url: str,
        required: bool = True,
        headers: dict[str, str] | None = None,
    ) -> dict:
        calls.append(service_id)
        ok = service_id not in failing_ids
        return {
            "id": service_id,
            "name": name,
            "category": category,
            "url": url,
            "required": required,
            "enabled": True,
            "ok": ok,
            "status": "running" if ok else "unavailable",
            "statusCode": 200 if ok else 503,
            "latencyMs": 1,
            "detail": {"ok": ok},
        }

    monkeypatch.setattr(system_status, "_probe_service", fake_probe_service)
    return calls


def _ic_services(result: dict) -> list[dict]:
    return [service for service in result["services"] if service["id"].startswith("hermes_siq_ic_")]


def test_system_status_marks_ic_hermes_disabled_by_default(monkeypatch):
    monkeypatch.delenv("SIQ_ENABLE_IC_HERMES", raising=False)
    calls = _install_status_stubs(monkeypatch)
    monkeypatch.setattr(system_status, "_health_url_is_open", lambda url: False)

    result = asyncio.run(system_status.collect_system_status())

    ic_services = _ic_services(result)
    assert result["status"] == "ok"
    assert len(ic_services) == 7
    assert not any(call.startswith("hermes_siq_ic_") for call in calls)
    assert all(service["enabled"] is False for service in ic_services)
    assert all(service["required"] is False for service in ic_services)
    assert all(service["status"] == "disabled" for service in ic_services)


def test_system_status_probes_running_ic_hermes_even_when_env_disabled(monkeypatch):
    monkeypatch.delenv("SIQ_ENABLE_IC_HERMES", raising=False)
    calls = _install_status_stubs(monkeypatch)
    monkeypatch.setattr(system_status, "_health_url_is_open", lambda url: "1866" in url)

    result = asyncio.run(system_status.collect_system_status())

    ic_services = _ic_services(result)
    assert result["status"] == "ok"
    assert len(ic_services) == 7
    assert len([call for call in calls if call.startswith("hermes_siq_ic_")]) == 7
    assert all(service["enabled"] is True for service in ic_services)
    assert all(service["status"] == "running" for service in ic_services)


def test_system_status_probes_ic_hermes_when_enabled(monkeypatch):
    monkeypatch.setenv("SIQ_ENABLE_IC_HERMES", "1")
    calls = _install_status_stubs(monkeypatch, {"hermes_siq_ic_chairman"})
    monkeypatch.setattr(system_status, "_health_url_is_open", lambda url: False)

    result = asyncio.run(system_status.collect_system_status())

    ic_services = _ic_services(result)
    by_id = {service["id"]: service for service in ic_services}
    assert result["status"] == "degraded"
    assert len(ic_services) == 7
    assert len([call for call in calls if call.startswith("hermes_siq_ic_")]) == 7
    assert all(service["enabled"] is True for service in ic_services)
    assert by_id["hermes_siq_ic_chairman"]["required"] is True
    assert by_id["hermes_siq_ic_chairman"]["ok"] is False
    assert by_id["hermes_siq_ic_chairman"]["status"] == "unavailable"
