"""Parse flat source-reference fields without reading assignments inside quotes."""

from __future__ import annotations

import re
from collections.abc import Collection

SOURCE_FIELD_START_RE = re.compile(r"(?:(?<=^)|(?<=[\s,，;；|]))([A-Za-z_][A-Za-z0-9_]*)=")


def _quoted_positions(text: str) -> tuple[bool, ...]:
    quoted = [False] * len(text)
    active_quote = ""
    escaped = False
    for index, char in enumerate(text):
        if active_quote:
            quoted[index] = True
            if char == active_quote and not escaped:
                active_quote = ""
            escaped = char == "\\" and not escaped
            if char != "\\":
                escaped = False
            continue
        if char not in {"'", '"'}:
            continue
        previous = index - 1
        while previous >= 0 and text[previous].isspace():
            previous -= 1
        if previous >= 0 and text[previous] == "=":
            active_quote = char
            quoted[index] = True
    return tuple(quoted)


def extract_source_fields(
    raw_line: str,
    *,
    allowed_fields: Collection[str] | None = None,
) -> dict[str, str]:
    """Extract top-level ``key=value`` fields from one source-reference line."""

    text = raw_line or ""
    quoted = _quoted_positions(text)
    matches = [
        match
        for match in SOURCE_FIELD_START_RE.finditer(text)
        if (allowed_fields is None or match.group(1) in allowed_fields)
        and not quoted[match.start()]
    ]
    fields: dict[str, str] = {}
    for index, match in enumerate(matches):
        key = match.group(1).strip()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        value = text[start:end].strip().strip(" \t,，;；|。")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1].strip()
        if value and key not in fields:
            fields[key] = value
    return fields


__all__ = ["extract_source_fields"]
