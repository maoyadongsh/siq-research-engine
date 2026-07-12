from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

REDACTED_CONNECTION_URL = "[redacted-connection-url]"

_SENSITIVE_QUERY_PARTS = (
    "access_key",
    "api_key",
    "auth",
    "credential",
    "password",
    "passwd",
    "secret",
    "token",
)


def _is_sensitive_query_key(key: str) -> bool:
    normalized = key.strip().lower().replace("-", "_")
    return normalized == "pwd" or any(part in normalized for part in _SENSITIVE_QUERY_PARTS)


def redact_connection_url(value: object) -> str:
    """Return a connection location that is safe to include in operational logs."""

    raw = str(value or "").strip()
    if not raw or "://" not in raw:
        return REDACTED_CONNECTION_URL

    try:
        parsed = urlsplit(raw)
        if not parsed.scheme:
            return REDACTED_CONNECTION_URL

        # Accessing these properties validates malformed ports and IPv6 brackets.
        _ = parsed.hostname
        _ = parsed.port

        if parsed.scheme.lower().startswith("sqlite"):
            return f"{parsed.scheme}:///[redacted]"
        if not parsed.netloc or parsed.hostname is None:
            return REDACTED_CONNECTION_URL

        netloc = parsed.netloc
        if "@" in netloc:
            _userinfo, location = netloc.rsplit("@", 1)
            netloc = f"[redacted]@{location}"

        query_items = []
        for key, item_value in parse_qsl(parsed.query, keep_blank_values=True):
            query_items.append((key, "[redacted]" if _is_sensitive_query_key(key) else item_value))

        return urlunsplit(
            (
                parsed.scheme,
                netloc,
                parsed.path,
                urlencode(query_items, doseq=True),
                "",
            )
        )
    except (TypeError, ValueError):
        return REDACTED_CONNECTION_URL
