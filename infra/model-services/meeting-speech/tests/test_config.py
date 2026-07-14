import pytest
from meeting_speech_service.config import Settings
from pydantic import ValidationError


def test_defaults_are_disabled_and_use_funasr() -> None:
    settings = Settings(_env_file=None)

    assert settings.enabled is False
    assert settings.adapter == "funasr"
    assert settings.host == "127.0.0.1"
    assert settings.port == 8901


def test_mock_requires_explicit_opt_in() -> None:
    with pytest.raises(ValidationError, match="allow_degraded_mock"):
        Settings(adapter="mock", _env_file=None)


def test_production_requires_token_and_forbids_mock() -> None:
    with pytest.raises(ValidationError, match="internal_service_token"):
        Settings(enabled=True, deployment_profile="production", _env_file=None)

    with pytest.raises(ValidationError, match="mock speech adapters"):
        Settings(
            enabled=True,
            deployment_profile="production",
            internal_service_token="not-a-real-secret",
            adapter="mock",
            allow_degraded_mock=True,
            _env_file=None,
        )


def test_buffer_limits_must_be_internally_consistent() -> None:
    with pytest.raises(ValidationError, match="max_pending_bytes"):
        Settings(max_frame_bytes=32_000, max_pending_bytes=10_000, _env_file=None)


def test_embedding_endpoint_requires_token_even_for_local_development() -> None:
    with pytest.raises(ValidationError, match="internal_service_token"):
        Settings(embedding_endpoint_enabled=True, _env_file=None)


def test_protected_http_finalizer_must_be_loopback_or_tls() -> None:
    with pytest.raises(ValidationError, match="HTTPS or a loopback"):
        Settings(
            enabled=True,
            deployment_profile="production",
            internal_service_token="not-a-real-secret",
            finalizer="funasr_http",
            http_finalizer_url="http://example.invalid/asr",
            _env_file=None,
        )
