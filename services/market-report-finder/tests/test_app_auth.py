import pytest
from fastapi.testclient import TestClient

from market_report_finder_service.app import app, settings


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "internal_service_token", None)
    return TestClient(app)


def test_v1_routes_remain_local_compatible_when_token_unconfigured(client):
    response = client.get("/v1/sources")

    assert response.status_code == 200


def test_v1_route_rejects_missing_service_token_when_configured(client, monkeypatch):
    monkeypatch.setattr(settings, "internal_service_token", "finder-secret")

    response = client.get("/v1/sources")

    assert response.status_code == 401
    assert response.json() == {"detail": "Unauthorized"}


def test_v1_route_rejects_wrong_service_token_when_configured(client, monkeypatch):
    monkeypatch.setattr(settings, "internal_service_token", "finder-secret")

    response = client.get("/v1/sources", headers={"X-SIQ-Service-Token": "wrong-token"})

    assert response.status_code == 401
    assert response.json() == {"detail": "Unauthorized"}


def test_v1_route_accepts_valid_service_token_when_configured(client, monkeypatch):
    monkeypatch.setattr(settings, "internal_service_token", "finder-secret")

    response = client.get("/v1/sources", headers={"X-SIQ-Service-Token": "finder-secret"})

    assert response.status_code == 200
    assert "sources" in response.json()


def test_public_routes_skip_service_token_when_configured(client, monkeypatch):
    monkeypatch.setattr(settings, "internal_service_token", "finder-secret")

    health = client.get("/health")
    index = client.get("/")

    assert health.status_code == 200
    assert index.status_code == 200
