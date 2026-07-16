#!/usr/bin/env python3
"""Accept a bounded health payload only when one top-level flag is exactly true."""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any

MAX_PAYLOAD_BYTES = 1024 * 1024
FIELD_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,63}")


def health_flag_is_true(payload: Any, field: str) -> bool:
    return isinstance(payload, dict) and payload.get(field) is True


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("field")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not FIELD_PATTERN.fullmatch(args.field):
        return 2
    content = sys.stdin.buffer.read(MAX_PAYLOAD_BYTES + 1)
    if len(content) > MAX_PAYLOAD_BYTES:
        return 1
    try:
        payload = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return 1
    return 0 if health_flag_is_true(payload, args.field) else 1


if __name__ == "__main__":
    raise SystemExit(main())
