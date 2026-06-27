"""Figure artifact helpers."""

from __future__ import annotations

from typing import Any


def figure_anchor(image_id: str) -> str:
    return f"md-{image_id}"


def figures_with_missing_bbox(figures: list[dict[str, Any]]) -> int:
    return sum(1 for figure in figures if not figure.get("bbox"))
