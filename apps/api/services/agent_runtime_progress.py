"""Progress payload and tool-display helpers for Hermes runtime streaming."""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Callable

PROGRESS_LINE_RE = re.compile(r"(?:\[[^\]]+\]\s*)?\[(?P<current>\d{1,3})/(?P<total>\d{1,3})\]\s*(?P<body>.+)")
PROGRESS_BAR_RE = re.compile(r"\s+\[[█░▓▒#=\-\s]{3,}\]\s*")


def progress_signature(payload: dict[str, Any], *, hash_text: Callable[[str], str]) -> str:
    stable = {
        "status": payload.get("status"),
        "title": payload.get("title"),
        "detail": payload.get("detail"),
        "current": payload.get("current"),
        "total": payload.get("total"),
        "source": payload.get("source"),
        "tool": payload.get("tool"),
    }
    return hash_text(json.dumps(stable, ensure_ascii=False, sort_keys=True))


def progress_payload(
    *,
    status: str = "running",
    title: str,
    detail: str | None = None,
    current: int | None = None,
    total: int | None = None,
    source: str = "runtime",
    tool: str | None = None,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    percent: int | None = None
    if total and total > 0 and current is not None:
        percent = max(0, min(100, round(current / total * 100)))
    elif status == "completed":
        percent = 100

    now = clock() if clock else datetime.utcnow()
    payload: dict[str, Any] = {
        "status": status,
        "title": title.strip() or "正在执行任务",
        "source": source,
        "updated_at": now.isoformat(),
    }
    if detail:
        payload["detail"] = detail.strip()
    if current is not None:
        payload["current"] = current
    if total is not None:
        payload["total"] = total
    if percent is not None:
        payload["percent"] = percent
    if tool:
        payload["tool"] = tool
    return payload


def extract_progress_from_text(text: str, *, clock: Callable[[], datetime] | None = None) -> dict[str, Any] | None:
    for raw_line in reversed(text.splitlines()[-12:]):
        line = raw_line.strip()
        if not line:
            continue
        match = PROGRESS_LINE_RE.search(line)
        if not match:
            continue

        current = int(match.group("current"))
        total = int(match.group("total"))
        body = match.group("body").strip()
        parts = PROGRESS_BAR_RE.split(body, maxsplit=1)
        title = parts[0].strip()
        detail = parts[1].strip() if len(parts) > 1 else ""
        status = "completed" if total > 0 and current >= total else "running"
        return progress_payload(
            status=status,
            title=title,
            detail=detail or None,
            current=current,
            total=total,
            source="agent_output",
            clock=clock,
        )
    return None


def trim_tool_preview(value: Any, limit: int = 280) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text if len(text) <= limit else f"{text[:limit]}..."


def is_file_search_tool_invocation(
    tool: str | None,
    preview: str | None = None,
    *,
    project_wiki_root: object | None = None,
    wiki_root: object | None = None,
) -> bool:
    name = str(tool or "").strip()
    text = str(preview or "").strip()
    if name in {"search_files", "read_file"}:
        return True
    if name != "terminal":
        return False
    wiki_markers = []
    if project_wiki_root is not None:
        wiki_markers.append(f"{project_wiki_root}/")
    if wiki_root is not None:
        wiki_markers.append(str(wiki_root))
    return (
        bool(re.search(r"(^|\s)(rg|grep|find)\s", text))
        or "resolve_company.py" in text
        or "note_detail_lookup.py" in text
        or any(marker and marker in text for marker in wiki_markers)
    )


def display_tool_label(
    tool: str | None,
    preview: str | None = None,
    *,
    project_wiki_root: object | None = None,
    wiki_root: object | None = None,
) -> str:
    name = str(tool or "").strip()
    if is_file_search_tool_invocation(
        name,
        preview,
        project_wiki_root=project_wiki_root,
        wiki_root=wiki_root,
    ):
        return "Search file"
    if name == "execute_code":
        return "Code execution"
    return name or "工具"
