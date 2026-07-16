"""Helpers for keeping backend guardrail diagnostics out of model-authored text."""

from __future__ import annotations

import re

GUARDRAIL_DIAGNOSTIC_TITLES = frozenset(
    {
        "证据不足",
        "证据链无效",
        "研究身份不完整",
        "计算校验缺失",
        "计算校验无效",
        "计算校验提示",
        "工具状态纠正",
        "财务证据身份不一致",
        "财务数值证据不一致",
        "校验失败详情",
        "后端确定性财务结果包",
    }
)

_H2_RE = re.compile(r"^\s*##\s+(.+?)\s*$")


def _diagnostic_title(line: str) -> str | None:
    match = _H2_RE.match(line)
    if not match:
        return None
    title = match.group(1).strip().rstrip("：:")
    return title if title in GUARDRAIL_DIAGNOSTIC_TITLES else None


def _sections(text: str) -> list[tuple[str | None, list[str]]]:
    sections: list[tuple[str | None, list[str]]] = []
    current_title: str | None = None
    current_lines: list[str] = []
    fence_marker: str | None = None
    for line in (text or "").splitlines():
        stripped = line.lstrip()
        fence_match = re.match(r"(`{3,}|~{3,})", stripped)
        if fence_match:
            marker = fence_match.group(1)[0]
            if fence_marker is None:
                fence_marker = marker
            elif fence_marker == marker:
                fence_marker = None
        if fence_marker is None and _H2_RE.match(line):
            if current_lines:
                sections.append((current_title, current_lines))
            current_title = _diagnostic_title(line)
            current_lines = [line]
        else:
            current_lines.append(line)
    if current_lines:
        sections.append((current_title, current_lines))
    return sections


def _join_sections(sections: list[tuple[str | None, list[str]]]) -> str:
    lines = [line for _title, section_lines in sections for line in section_lines]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def strip_guardrail_diagnostics(text: str | None) -> str:
    """Remove backend-authored diagnostic sections before model reuse."""
    return _join_sections([section for section in _sections(str(text or "")) if section[0] is None])


def collapse_duplicate_guardrail_diagnostics(text: str | None) -> str:
    """Keep one copy of each backend diagnostic in legacy persisted replies."""
    seen: set[str] = set()
    kept: list[tuple[str | None, list[str]]] = []
    for title, lines in _sections(str(text or "")):
        if title is not None:
            if title in seen:
                continue
            seen.add(title)
        kept.append((title, lines))
    return _join_sections(kept)


__all__ = [
    "GUARDRAIL_DIAGNOSTIC_TITLES",
    "collapse_duplicate_guardrail_diagnostics",
    "strip_guardrail_diagnostics",
]
