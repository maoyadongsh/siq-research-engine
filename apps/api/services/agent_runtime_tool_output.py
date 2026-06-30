"""Tool output normalization helpers for the Hermes agent runtime."""

from __future__ import annotations

import json
from typing import Any


def normalize_tool_output(content: Any) -> tuple[str | None, str]:
    raw = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    status: str | None = None
    output = raw
    try:
        payload = json.loads(raw)
        if isinstance(payload, dict):
            status = str(payload.get("status") or "") or None
            output = str(payload.get("output") or payload.get("content") or raw)
    except Exception:
        pass
    return status, output.strip()


__all__ = ["normalize_tool_output"]
