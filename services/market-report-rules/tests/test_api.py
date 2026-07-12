import pytest
from fastapi.testclient import TestClient
from market_report_rules_service.app import DEPLOYMENT_PROFILE_ENV, SERVICE_TOKEN_ENV, SERVICE_TOKEN_HEADER, app


@pytest.fixture(autouse=True)
def clear_rules_service_token(monkeypatch):
    monkeypatch.delenv(SERVICE_TOKEN_ENV, raising=False)
    monkeypatch.setenv(DEPLOYMENT_PROFILE_ENV, "local")


def test_healthz():
    client = TestClient(app)
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert {market["market"] for market in response.json()["markets"]} == {"CN", "HK", "US", "JP", "KR", "EU"}


def test_healthz_remains_public_when_service_token_configured(monkeypatch):
    monkeypatch.setenv(SERVICE_TOKEN_ENV, "internal-token")
    client = TestClient(app)

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.parametrize("path", ["/profiles", "/markets", "/rules"])
def test_metadata_routes_require_service_token_when_configured(monkeypatch, path):
    monkeypatch.setenv(SERVICE_TOKEN_ENV, "internal-token")
    client = TestClient(app)

    missing = client.get(path)
    wrong = client.get(path, headers={SERVICE_TOKEN_HEADER: "wrong-token"})
    valid = client.get(path, headers={SERVICE_TOKEN_HEADER: "internal-token"})

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert valid.status_code == 200


def _minimal_artifact_payload() -> dict[str, object]:
    return {
        "artifact_id": "empty-us",
        "market": "US",
        "company_id": "US:EMPTY",
        "ticker": "EMPTY",
        "report_type": "quarterly",
        "report_form": "10-Q",
    }


def _minimal_extraction_payload() -> dict[str, object]:
    return {
        "rule_version": "test_rules_v1",
        "profile_id": "test_profile",
        "artifact_id": "empty-us",
        "market": "US",
        "accounting_standard": "UNKNOWN",
        "company_id": "US:EMPTY",
        "ticker": "EMPTY",
        "statements": [],
    }


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/extract", _minimal_artifact_payload()),
        ("/validate", _minimal_extraction_payload()),
        ("/process", {"artifact": _minimal_artifact_payload(), "build_load_plan": True}),
        ("/load-plan", _minimal_extraction_payload()),
    ],
)
def test_high_risk_routes_require_service_token_when_configured(monkeypatch, path, payload):
    monkeypatch.setenv(SERVICE_TOKEN_ENV, "internal-token")
    client = TestClient(app)

    missing = client.post(path, json=payload)
    wrong = client.post(path, json=payload, headers={SERVICE_TOKEN_HEADER: "wrong-token"})
    valid = client.post(path, json=payload, headers={SERVICE_TOKEN_HEADER: "internal-token"})

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert valid.status_code == 200


@pytest.mark.parametrize("profile", ["production", "prod", "docker"])
@pytest.mark.parametrize("token", [None, "   "])
def test_protected_profile_rejects_missing_service_token_at_startup(monkeypatch, profile, token):
    monkeypatch.setenv(DEPLOYMENT_PROFILE_ENV, profile)
    if token is None:
        monkeypatch.delenv(SERVICE_TOKEN_ENV, raising=False)
    else:
        monkeypatch.setenv(SERVICE_TOKEN_ENV, token)

    with pytest.raises(RuntimeError, match=SERVICE_TOKEN_ENV):
        with TestClient(app):
            pass


def test_protected_profile_starts_with_token_and_keeps_health_public(monkeypatch):
    monkeypatch.setenv(DEPLOYMENT_PROFILE_ENV, "production")
    monkeypatch.setenv(SERVICE_TOKEN_ENV, "internal-token")

    with TestClient(app) as client:
        health = client.get("/healthz")
        missing = client.get("/profiles")
        wrong = client.get("/profiles", headers={SERVICE_TOKEN_HEADER: "wrong-token"})
        valid = client.get("/profiles", headers={SERVICE_TOKEN_HEADER: "internal-token"})

    assert health.status_code == 200
    assert (missing.status_code, wrong.status_code, valid.status_code) == (401, 401, 200)


def test_markets_register_cn_legacy_pages():
    client = TestClient(app)
    response = client.get("/markets")

    assert response.status_code == 200
    markets = {item["market"]: item for item in response.json()["markets"]}
    assert markets["CN"]["parser_boundary"] == "markets.cn.adapter"
    assert {page["page_id"] for page in markets["CN"]["feature_pages"]} == {
        "cn-report-download",
        "cn-pdf-parsing",
    }


def test_storage_profiles_use_company_wiki_market_roots():
    client = TestClient(app)
    response = client.get("/healthz")

    assert response.status_code == 200
    profiles = {item["market"]: item for item in response.json()["storage_profiles"]}
    assert profiles["HK"]["wiki_namespace"] == "data/wiki/hk"
    assert profiles["HK"]["parsed_artifact_root"] == "data/wiki/hk"
    assert profiles["JP"]["wiki_namespace"] == "data/wiki/jp"
    assert profiles["JP"]["parsed_artifact_root"] == "data/wiki/jp"
    assert profiles["KR"]["wiki_namespace"] == "data/wiki/kr"
    assert profiles["KR"]["parsed_artifact_root"] == "data/wiki/kr"
    assert profiles["EU"]["wiki_namespace"] == "data/wiki/eu"
    assert profiles["EU"]["parsed_artifact_root"] == "data/wiki/eu"


def test_cn_rules_exposes_migrated_entrypoints():
    client = TestClient(app)
    response = client.get("/markets/cn/rules")

    assert response.status_code == 200
    payload = response.json()
    assert payload["market"] == "CN"
    assert payload["rule_version"] == "financial_rules_v14"
    assert payload["rule_source"] == "apps/pdf-parser/financial_extractor.py"
    assert payload["adapter"]["download_service"]["module"] == "market_report_finder_service.app:app"


def test_process_contract_minimal_us():
    client = TestClient(app)
    response = client.post(
        "/process",
        json={
            "artifact": {
                "artifact_id": "empty-us",
                "market": "US",
                "company_id": "US:EMPTY",
                "ticker": "EMPTY",
                "report_type": "quarterly",
                "report_form": "10-Q",
            },
            "build_load_plan": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["financial_data"]["market"] == "US"
    assert payload["load_plan"]["target_database"] == "siq"
    assert payload["load_plan"]["target_schema"] == "sec_us"


def test_process_contract_minimal_eu():
    client = TestClient(app)
    response = client.post(
        "/process",
        json={
            "artifact": {
                "artifact_id": "empty-eu",
                "market": "EU",
                "company_id": "NL:EMPTY",
                "ticker": "EMPTY",
                "report_type": "annual",
                "report_form": "annual",
                "accounting_standard": "IFRS",
                "metadata": {"country": "NL", "document_format": "pdf"},
            },
            "build_load_plan": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["financial_data"]["market"] == "EU"
    assert payload["load_plan"]["target_database"] == "siq"
    assert payload["load_plan"]["target_schema"] == "eu_ifrs"
