from __future__ import annotations

import json

import pytest
from services.meeting_model_catalog import (
    MeetingModelCatalog,
    MeetingModelCatalogError,
)


def _target() -> dict:
    return {
        "model_ref": "meeting:test-model:0123456789ab",
        "target_id": "siq-meeting-test-model-01234567",
        "label": "Test Model",
        "provider_label": "Local runtime",
        "provider": "custom:local-runtime",
        "model": "test/model",
        "locality": "local",
        "runs_url": "http://127.0.0.1:18710/v1/runs",
        "advertised_model": "siq-meeting-test-model-01234567",
        "api_key_env": "SIQ_MEETINGS_HERMES_API_KEY",
        "context_window": 32768,
        "enabled": True,
        "capabilities": ["text", "structured_json"],
        "runtime": {},
    }


def test_catalog_uses_same_immutable_target_pool_without_exposing_runtime(monkeypatch):
    monkeypatch.setenv("SIQ_MEETINGS_HERMES_TARGETS_JSON", json.dumps([_target()]))
    monkeypatch.setenv("SIQ_MEETINGS_HERMES_API_KEY", "server-only-secret")
    monkeypatch.delenv("SIQ_MEETINGS_MODEL_CATALOG_JSON", raising=False)

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(
        "services.meeting_model_catalog.socket.create_connection", lambda *_args, **_kwargs: Connection()
    )

    descriptor = MeetingModelCatalog().list_models()[0]
    payload = descriptor.model_dump(mode="json")
    assert payload["model_ref"] == _target()["model_ref"]
    assert payload["available"] is True
    assert payload["configured"] is True
    assert payload["locality"] == "local"
    serialized = json.dumps(payload)
    assert "18710" not in serialized
    assert "server-only-secret" not in serialized
    assert "SIQ_MEETINGS_HERMES_API_KEY" not in serialized
    assert "runs_url" not in payload


def test_catalog_marks_target_unavailable_without_gateway_key(monkeypatch):
    monkeypatch.setenv("SIQ_MEETINGS_HERMES_TARGETS_JSON", json.dumps([_target()]))
    monkeypatch.delenv("SIQ_MEETINGS_HERMES_API_KEY", raising=False)
    monkeypatch.delenv("SIQ_MEETINGS_MODEL_CATALOG_JSON", raising=False)

    descriptor = MeetingModelCatalog().list_models()[0]
    assert descriptor.configured is False
    assert descriptor.available is False
    assert descriptor.reason_code == "MODEL_GATEWAY_AUTH_UNCONFIGURED"


def test_explicit_public_catalog_override_remains_supported(monkeypatch):
    monkeypatch.delenv("SIQ_MEETING_DEFAULT_MODEL_REF", raising=False)
    monkeypatch.setenv(
        "SIQ_MEETINGS_MODEL_CATALOG_JSON",
        json.dumps(
            [
                {
                    "model_ref": "meeting:cloud:123456789abc",
                    "label": "Cloud model",
                    "provider_label": "Configured cloud",
                    "locality": "cloud",
                    "configured": True,
                    "available": True,
                    "capabilities": ["text", "structured_json"],
                    "data_boundary": "cloud",
                }
            ]
        ),
    )
    assert MeetingModelCatalog().list_models()[0].locality == "cloud"


def test_catalog_marks_configured_default_model(monkeypatch):
    default_ref = "meeting:kimi-for-coding:4c226b751833"
    monkeypatch.setenv("SIQ_MEETING_DEFAULT_MODEL_REF", default_ref)

    catalog = MeetingModelCatalog(
        lambda: [
            {
                "model_ref": "meeting:local:123456789abc",
                "locality": "local",
                "configured": True,
                "available": True,
            },
            {
                "model_ref": default_ref,
                "locality": "cloud",
                "configured": True,
                "available": True,
            },
        ]
    )

    models = catalog.list_models()

    assert [item.model_ref for item in models if item.is_default] == [default_ref]


def test_catalog_rejects_stale_default_model_ref(monkeypatch):
    monkeypatch.setenv("SIQ_MEETING_DEFAULT_MODEL_REF", "meeting:missing:123456789abc")
    catalog = MeetingModelCatalog(
        lambda: [
            {
                "model_ref": "meeting:local:123456789abc",
                "locality": "local",
                "configured": True,
                "available": True,
            }
        ]
    )

    with pytest.raises(MeetingModelCatalogError, match="default meeting model is not configured"):
        catalog.list_models()


def test_public_catalog_rejects_endpoint_or_secret_fields(monkeypatch):
    monkeypatch.setenv(
        "SIQ_MEETINGS_MODEL_CATALOG_JSON",
        json.dumps([{"model_ref": "meeting:test:123", "locality": "local", "base_url": "http://internal"}]),
    )
    with pytest.raises(MeetingModelCatalogError):
        MeetingModelCatalog().list_models()


def test_catalog_uses_short_ttl_cache_and_supports_explicit_refresh():
    now = {"value": 10.0}
    calls = {"value": 0}

    def resolver():
        calls["value"] += 1
        return [
            {
                "model_ref": "meeting:cached:123456789abc",
                "locality": "local",
                "configured": True,
                "available": True,
                "capabilities": ["text"],
            }
        ]

    catalog = MeetingModelCatalog(
        resolver,
        ttl_seconds=15,
        clock=lambda: now["value"],
    )
    assert catalog.list_models()[0].available is True
    assert catalog.list_models()[0].available is True
    assert calls["value"] == 1

    catalog.list_models(refresh=True)
    assert calls["value"] == 2
    now["value"] += 16
    catalog.list_models()
    assert calls["value"] == 3


@pytest.mark.parametrize("value", ["0", "301", "not-an-integer"])
def test_catalog_rejects_invalid_ttl(monkeypatch, value):
    monkeypatch.setenv("SIQ_MEETING_MODEL_CATALOG_TTL_SECONDS", value)
    with pytest.raises(MeetingModelCatalogError):
        MeetingModelCatalog(lambda: []).list_models()
