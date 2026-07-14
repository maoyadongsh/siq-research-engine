"""Deployment-mode contract for the isolated meeting stream gateway."""

from __future__ import annotations

import os


class MeetingStreamDeploymentError(ValueError):
    pass


_PROTECTED_PROFILES = {"docker", "prod", "production"}
_VALID_MODES = {"embedded", "external"}


def meeting_stream_gateway_mode(
    *,
    configured: str | None = None,
    deployment_profile: str | None = None,
) -> str:
    raw_mode = (
        configured
        if configured is not None
        else os.getenv("SIQ_MEETING_STREAM_GATEWAY_MODE", "")
    ).strip().lower()
    profile = (
        deployment_profile
        if deployment_profile is not None
        else os.getenv("SIQ_DEPLOYMENT_PROFILE", "development")
    ).strip().lower()
    if not raw_mode:
        return "external" if profile in _PROTECTED_PROFILES else "embedded"
    if raw_mode not in _VALID_MODES:
        raise MeetingStreamDeploymentError(
            "SIQ_MEETING_STREAM_GATEWAY_MODE must be embedded or external"
        )
    if profile in _PROTECTED_PROFILES and raw_mode != "external":
        raise MeetingStreamDeploymentError(
            "protected deployments require an external meeting stream gateway"
        )
    return raw_mode


def embedded_meeting_stream_gateway_enabled() -> bool:
    return meeting_stream_gateway_mode() == "embedded"
