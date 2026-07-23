"""Runtime security settings for API startup."""

from __future__ import annotations

import os
from urllib.parse import urlsplit

DEFAULT_DEV_CORS_ORIGINS = (
    "http://localhost:15173",
    "http://127.0.0.1:15173",
    "tauri://localhost",
    "https://tauri.localhost",
)
PRODUCTION_PROFILES = {"prod", "production"}
PUBLIC_ORIGIN_REQUIRED_PROFILES = {"docker", *PRODUCTION_PROFILES}


def deployment_profile() -> str:
    return os.getenv("SIQ_DEPLOYMENT_PROFILE", "development").strip().lower()


def is_production_profile() -> bool:
    return deployment_profile() in PRODUCTION_PROFILES


def _split_csv(value: str) -> list[str]:
    return [item.strip().rstrip("/") for item in value.split(",") if item.strip()]


def cors_origins_from_env() -> list[str]:
    raw = os.getenv("SIQ_CORS_ALLOW_ORIGINS") or os.getenv("SIQ_ALLOWED_ORIGINS") or ""
    configured = _split_csv(raw)
    if is_production_profile():
        if not configured:
            raise RuntimeError("SIQ_CORS_ALLOW_ORIGINS must be set when SIQ_DEPLOYMENT_PROFILE=production.")
        if "*" in configured:
            raise RuntimeError("Wildcard CORS origins are not allowed when SIQ_DEPLOYMENT_PROFILE=production.")
        return configured

    return configured or list(DEFAULT_DEV_CORS_ORIGINS)


def public_origin_from_env() -> str:
    raw = os.getenv("SIQ_PUBLIC_ORIGIN", "").strip().rstrip("/")
    if not raw:
        if deployment_profile() in PUBLIC_ORIGIN_REQUIRED_PROFILES:
            raise RuntimeError(
                "SIQ_PUBLIC_ORIGIN must be set when SIQ_DEPLOYMENT_PROFILE is docker or production."
            )
        return ""

    parsed = urlsplit(raw)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError("SIQ_PUBLIC_ORIGIN must be an http(s) origin without credentials, path, query, or fragment.")
    return raw


def validate_runtime_security_config() -> None:
    if is_production_profile():
        if os.getenv("FLASK_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}:
            raise RuntimeError("FLASK_DEBUG must not be enabled when SIQ_DEPLOYMENT_PROFILE=production.")
    cors_origins_from_env()
    public_origin_from_env()
