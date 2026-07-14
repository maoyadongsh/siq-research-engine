import importlib

import pytest
from services.meeting_stream_deployment import (
    MeetingStreamDeploymentError,
    meeting_stream_gateway_mode,
)


def test_stream_gateway_defaults_external_for_protected_profiles():
    assert meeting_stream_gateway_mode(configured="", deployment_profile="development") == "embedded"
    assert meeting_stream_gateway_mode(configured="", deployment_profile="production") == "external"
    assert meeting_stream_gateway_mode(configured="external", deployment_profile="production") == "external"


def test_protected_profile_rejects_embedded_or_unknown_gateway_modes():
    with pytest.raises(MeetingStreamDeploymentError, match="external"):
        meeting_stream_gateway_mode(configured="embedded", deployment_profile="docker")
    with pytest.raises(MeetingStreamDeploymentError, match="embedded or external"):
        meeting_stream_gateway_mode(configured="sidecar", deployment_profile="development")


def test_standalone_gateway_exposes_only_ticket_authenticated_data_plane(monkeypatch):
    monkeypatch.setenv("SIQ_DEPLOYMENT_PROFILE", "development")
    module = importlib.import_module("meeting_stream_gateway")
    routes = {(getattr(route, "path", ""), tuple(sorted(getattr(route, "methods", ())))) for route in module.app.routes}
    assert ("/api/meetings/v1/sessions/{meeting_id}/audio", ("GET",)) in routes
    assert any(path == "/api/meetings/v1/sessions/{meeting_id}/audio" and not methods for path, methods in routes)
    assert all("stream-ticket" not in path and "audio-ticket" not in path for path, _ in routes)
