from fastapi.testclient import TestClient

from market_report_rules_service.app import app


def test_healthz():
    client = TestClient(app)
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert {market["market"] for market in response.json()["markets"]} == {"CN", "HK", "US", "JP", "KR", "EU"}


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
