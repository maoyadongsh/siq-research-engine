"""Runtime meeting model catalog with opaque references and secret filtering."""

from __future__ import annotations

import json
import os
import socket
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from services.meeting_contracts import MeetingModelDescriptor, utcnow
from services.meeting_hermes_runner import (
    MeetingHermesConfigurationError,
    MeetingHermesTarget,
    MeetingHermesTargetPool,
)


class MeetingModelCatalogError(ValueError):
    pass


_ALLOWED_FIELDS = {
    "model_ref",
    "label",
    "provider_label",
    "locality",
    "configured",
    "available",
    "is_default",
    "capabilities",
    "context_window",
    "data_boundary",
    "reason_code",
    "checked_at",
}
_FORBIDDEN_HINTS = ("key", "token", "secret", "authorization", "base_url", "endpoint")


def _safe_descriptor(item: Mapping[str, Any], *, checked_at: datetime) -> MeetingModelDescriptor:
    forbidden = [key for key in item if any(hint in key.lower() for hint in _FORBIDDEN_HINTS)]
    if forbidden:
        raise MeetingModelCatalogError("model catalog contains forbidden credential or endpoint fields")
    unknown = set(item) - _ALLOWED_FIELDS
    if unknown:
        raise MeetingModelCatalogError(f"model catalog contains unsupported fields: {sorted(unknown)!r}")
    model_ref = str(item.get("model_ref") or "").strip()
    if not model_ref:
        raise MeetingModelCatalogError("model_ref is required")
    locality = str(item.get("locality") or "").strip().lower()
    if locality not in {"local", "cloud"}:
        raise MeetingModelCatalogError("model locality must be local or cloud")
    capabilities = [str(value) for value in item.get("capabilities") or []]
    timestamp = item.get("checked_at") or checked_at
    return MeetingModelDescriptor(
        model_ref=model_ref,
        label=str(item.get("label") or model_ref),
        provider_label=str(item.get("provider_label") or "configured provider"),
        locality=locality,
        configured=bool(item.get("configured", True)),
        available=bool(item.get("available", False)),
        is_default=bool(item.get("is_default", False)),
        capabilities=capabilities,
        context_window=item.get("context_window"),
        data_boundary=str(item.get("data_boundary") or locality),
        reason_code=str(item["reason_code"]) if item.get("reason_code") else None,
        checked_at=timestamp,
    )


class MeetingModelCatalog:
    def __init__(
        self,
        resolver: Callable[[], Iterable[Mapping[str, Any]]] | None = None,
        *,
        ttl_seconds: int | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._resolver = resolver or self._resolve_runtime
        self._ttl_override = ttl_seconds
        self._clock = clock
        self._cache_lock = threading.Lock()
        self._cached_models: list[MeetingModelDescriptor] | None = None
        self._cache_expires_at = 0.0

    def _ttl_seconds(self) -> int:
        if self._ttl_override is not None:
            value = self._ttl_override
        else:
            raw = os.getenv("SIQ_MEETING_MODEL_CATALOG_TTL_SECONDS", "15").strip()
            try:
                value = int(raw)
            except ValueError as exc:
                raise MeetingModelCatalogError("SIQ_MEETING_MODEL_CATALOG_TTL_SECONDS must be an integer") from exc
        if value < 1 or value > 300:
            raise MeetingModelCatalogError("SIQ_MEETING_MODEL_CATALOG_TTL_SECONDS must be between 1 and 300")
        return value

    @staticmethod
    def _resolve_public_override(raw: str) -> Iterable[Mapping[str, Any]]:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise MeetingModelCatalogError("SIQ_MEETINGS_MODEL_CATALOG_JSON is invalid JSON") from exc
        if not isinstance(payload, list):
            raise MeetingModelCatalogError("SIQ_MEETINGS_MODEL_CATALOG_JSON must be a list")
        if not all(isinstance(item, dict) for item in payload):
            raise MeetingModelCatalogError("every meeting model catalog item must be an object")
        return payload

    @staticmethod
    def _target_available(target: MeetingHermesTarget) -> bool:
        parsed = urlparse(target.runs_url)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        try:
            with socket.create_connection((parsed.hostname or "", port), timeout=0.15):
                return True
        except OSError:
            return False

    @classmethod
    def _target_descriptor(cls, target: MeetingHermesTarget) -> Mapping[str, Any]:
        key_configured = bool(os.getenv(target.api_key_env, "").strip())
        configured = target.enabled and key_configured
        available = configured and cls._target_available(target)
        reason_code = None
        if not target.enabled:
            reason_code = "MODEL_DISABLED"
        elif not key_configured:
            reason_code = "MODEL_GATEWAY_AUTH_UNCONFIGURED"
        elif not available:
            reason_code = "MODEL_GATEWAY_UNAVAILABLE"
        return {
            "model_ref": target.model_ref,
            "label": target.label,
            "provider_label": target.provider_label,
            "locality": target.locality,
            "configured": configured,
            "available": available,
            "capabilities": list(target.capabilities),
            "context_window": target.context_window,
            "data_boundary": target.locality,
            "reason_code": reason_code,
        }

    @classmethod
    def _resolve_runtime(cls) -> Iterable[Mapping[str, Any]]:
        public_override = os.getenv("SIQ_MEETINGS_MODEL_CATALOG_JSON", "").strip()
        if public_override:
            return cls._resolve_public_override(public_override)
        try:
            targets = MeetingHermesTargetPool.from_env().list_targets()
        except MeetingHermesConfigurationError as exc:
            raise MeetingModelCatalogError("meeting Hermes target pool is invalid") from exc
        return [cls._target_descriptor(target) for target in targets]

    def list_models(
        self,
        purpose: str = "meeting_postprocess",
        *,
        refresh: bool = False,
    ) -> list[MeetingModelDescriptor]:
        if purpose != "meeting_postprocess":
            raise MeetingModelCatalogError("unsupported meeting model purpose")
        ttl_seconds = self._ttl_seconds()
        with self._cache_lock:
            now = self._clock()
            if not refresh and self._cached_models is not None and now < self._cache_expires_at:
                return [item.model_copy(deep=True) for item in self._cached_models]
            checked_at = utcnow()
            descriptors = [_safe_descriptor(item, checked_at=checked_at) for item in self._resolver()]
            refs = [item.model_ref for item in descriptors]
            if len(refs) != len(set(refs)):
                raise MeetingModelCatalogError("meeting model_ref values must be unique")
            configured_default = os.getenv("SIQ_MEETING_DEFAULT_MODEL_REF", "").strip()
            declared_defaults = [item.model_ref for item in descriptors if item.is_default]
            if len(declared_defaults) > 1:
                raise MeetingModelCatalogError("meeting model catalog may declare only one default")
            default_ref = configured_default or (declared_defaults[0] if declared_defaults else "")
            if default_ref and default_ref not in refs:
                raise MeetingModelCatalogError("default meeting model is not configured")
            if default_ref:
                descriptors = [
                    item.model_copy(update={"is_default": item.model_ref == default_ref})
                    for item in descriptors
                ]
            self._cached_models = [item.model_copy(deep=True) for item in descriptors]
            self._cache_expires_at = now + ttl_seconds
            return descriptors

    def clear_cache(self) -> None:
        with self._cache_lock:
            self._cached_models = None
            self._cache_expires_at = 0.0

    def require_available(self, model_ref: str) -> MeetingModelDescriptor:
        model = next((item for item in self.list_models() if item.model_ref == model_ref), None)
        if model is None:
            raise MeetingModelCatalogError("selected meeting model is not configured")
        if not model.configured or not model.available:
            raise MeetingModelCatalogError("selected meeting model is unavailable")
        return model
