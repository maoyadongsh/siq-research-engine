import pytest

from services.runtime_security import (
    DEFAULT_DEV_CORS_ORIGINS,
    cors_origins_from_env,
    is_production_profile,
    validate_runtime_security_config,
)


def test_dev_profile_uses_default_local_cors_origins(monkeypatch):
    monkeypatch.delenv("SIQ_DEPLOYMENT_PROFILE", raising=False)
    monkeypatch.delenv("SIQ_CORS_ALLOW_ORIGINS", raising=False)
    monkeypatch.delenv("SIQ_ALLOWED_ORIGINS", raising=False)

    assert is_production_profile() is False
    assert cors_origins_from_env() == list(DEFAULT_DEV_CORS_ORIGINS)


def test_configured_cors_origins_are_trimmed(monkeypatch):
    monkeypatch.setenv("SIQ_CORS_ALLOW_ORIGINS", " https://app.example/ ,https://ops.example ")

    assert cors_origins_from_env() == ["https://app.example", "https://ops.example"]


def test_production_requires_explicit_cors_origins(monkeypatch):
    monkeypatch.setenv("SIQ_DEPLOYMENT_PROFILE", "production")
    monkeypatch.delenv("SIQ_CORS_ALLOW_ORIGINS", raising=False)
    monkeypatch.delenv("SIQ_ALLOWED_ORIGINS", raising=False)

    with pytest.raises(RuntimeError, match="SIQ_CORS_ALLOW_ORIGINS must be set"):
        cors_origins_from_env()


def test_production_rejects_wildcard_cors(monkeypatch):
    monkeypatch.setenv("SIQ_DEPLOYMENT_PROFILE", "prod")
    monkeypatch.setenv("SIQ_CORS_ALLOW_ORIGINS", "https://app.example,*")

    with pytest.raises(RuntimeError, match="Wildcard CORS origins"):
        cors_origins_from_env()


def test_production_rejects_flask_debug(monkeypatch):
    monkeypatch.setenv("SIQ_DEPLOYMENT_PROFILE", "production")
    monkeypatch.setenv("SIQ_CORS_ALLOW_ORIGINS", "https://app.example")
    monkeypatch.setenv("FLASK_DEBUG", "1")

    with pytest.raises(RuntimeError, match="FLASK_DEBUG"):
        validate_runtime_security_config()


def test_production_accepts_explicit_origins_and_debug_off(monkeypatch):
    monkeypatch.setenv("SIQ_DEPLOYMENT_PROFILE", "production")
    monkeypatch.setenv("SIQ_CORS_ALLOW_ORIGINS", "https://app.example")
    monkeypatch.setenv("FLASK_DEBUG", "0")

    validate_runtime_security_config()
    assert cors_origins_from_env() == ["https://app.example"]
