"""Request query parsing helpers for document parser routes."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


_RAISE_ON_INVALID = object()
QUERY_FLAG_TRUE_VALUES = {"1", "true", "yes"}


def parse_int_arg(
    args: Mapping[str, Any],
    name: str,
    default: int,
    *,
    invalid_default: int | object = _RAISE_ON_INVALID,
) -> int:
    raw_value = args.get(name)
    if raw_value is None or raw_value == "":
        return default
    try:
        return int(raw_value)
    except (TypeError, ValueError):
        if invalid_default is _RAISE_ON_INVALID:
            raise
        return int(invalid_default)


def query_flag_enabled(args: Mapping[str, Any], name: str) -> bool:
    return args.get(name) in QUERY_FLAG_TRUE_VALUES
