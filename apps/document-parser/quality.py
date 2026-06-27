"""Quality scoring helpers for normalized document artifacts."""

from __future__ import annotations

from typing import Any


def warning_status(warnings: list[dict[str, Any]]) -> str:
    return "pass" if not warnings else "warning"


def ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0
