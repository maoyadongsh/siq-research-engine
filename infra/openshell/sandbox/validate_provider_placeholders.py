#!/usr/bin/env python3
"""Reject missing or materialized provider credentials inside the sandbox."""

from __future__ import annotations

import os
import re
import sys

REQUIRED_PROVIDER_ENVS = (
    "KIMI_API_KEY",
    "SIQ_MINIMAX_CN_BACKUP",
    "SIQ_MINIMAX_CN_PRIMARY",
    "SIQ_STEPFUN_LLM_API_KEY",
    "TAVILY_API_KEY",
)
EXPECTED_DATA_BROKER_URL = "http://host.openshell.internal:18793"


def is_placeholder(name: str, value: str) -> bool:
    return re.fullmatch(rf"openshell:resolve:env:(?:v[1-9][0-9]*_)?{re.escape(name)}", value) is not None


def validate_environment(environment: dict[str, str]) -> None:
    for name in REQUIRED_PROVIDER_ENVS:
        value = environment.get(name, "")
        if not is_placeholder(name, value):
            raise ValueError(f"provider_placeholder_invalid:{name}")
    if environment.get("SIQ_PG_QUERY_BROKER_URL") != EXPECTED_DATA_BROKER_URL:
        raise ValueError("data_broker_url_invalid")


def main() -> int:
    try:
        validate_environment(dict(os.environ))
    except ValueError as exc:
        print(f"OpenShell provider environment failed: {exc}", file=sys.stderr)
        return 2
    print("OpenShell provider environment: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
