"""Small observability helpers shared by API runtime code."""

from __future__ import annotations

import contextvars
import json
import logging
import re
import time
import uuid
from collections.abc import Mapping
from typing import Any


REQUEST_ID_HEADER = "X-Request-ID"
REQUEST_ID_LOG_FIELD = "request_id"
MAX_REQUEST_ID_LENGTH = 128
SENSITIVE_KEY_TERMS = ("authorization", "bearer", "cookie", "password", "secret", "token", "api_key", "key")
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:/#=-]+$")
_request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("siq_request_id", default="")


def current_request_id() -> str:
    return _request_id_var.get()


def set_request_id(request_id: str) -> contextvars.Token[str]:
    return _request_id_var.set(request_id)


def reset_request_id(token: contextvars.Token[str]) -> None:
    _request_id_var.reset(token)


def normalize_request_id(value: Any | None) -> str:
    text = str(value or "").strip()
    if text and len(text) <= MAX_REQUEST_ID_LENGTH and _REQUEST_ID_RE.fullmatch(text):
        return text
    return uuid.uuid4().hex


def monotonic_ms(start: float) -> int:
    return max(0, int((time.perf_counter() - start) * 1000))


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(term in lowered for term in SENSITIVE_KEY_TERMS)


def redact_sensitive(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): "***REDACTED***" if _is_sensitive_key(str(key)) else redact_sensitive(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_sensitive(item) for item in value)
    return value


def emit_json_log(logger: logging.Logger, event: str, **fields: Any) -> None:
    payload = {
        "event": event,
        REQUEST_ID_LOG_FIELD: fields.pop(REQUEST_ID_LOG_FIELD, current_request_id()),
        **redact_sensitive(fields),
    }
    logger.info(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
