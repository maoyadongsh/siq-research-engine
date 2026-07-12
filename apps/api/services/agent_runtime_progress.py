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


def task_started_progress_payload(*, clock: Callable[[], datetime] | None = None) -> dict[str, Any]:
    return progress_payload(
        status="running",
        title="任务已启动",
        detail="正在连接智能体并准备执行",
        current=0,
        total=1,
        clock=clock,
    )


def output_loop_stop_progress_payload(sample: Any, *, clock: Callable[[], datetime] | None = None) -> dict[str, Any]:
    return progress_payload(
        status="error",
        title="检测到重复输出",
        detail=f"智能体反复输出“{sample}”，已自动停止本次运行",
        source="runtime",
        clock=clock,
    )


def repeated_tool_call_stop_progress_payload(
    tool_label: Any,
    count: Any,
    *,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    tool_text = str(tool_label or "工具")
    return progress_payload(
        status="error",
        title="检测到工具调用循环",
        detail=f"{tool_text} 连续重复调用 {count} 次，已自动停止",
        source="runtime",
        tool=tool_text,
        clock=clock,
    )


def consecutive_tool_error_stop_progress_payload(
    tool_label: Any,
    count: Any,
    *,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    tool_text = str(tool_label or "工具")
    return progress_payload(
        status="error",
        title="检测到工具错误循环",
        detail=f"{tool_text} 连续失败 {count} 次，已自动停止",
        source="runtime",
        tool=tool_text,
        clock=clock,
    )


def terminal_run_event_progress_payload(
    event_type: str,
    detail: str,
    *,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    failed = event_type == "failed"
    return progress_payload(
        status="error" if failed else "stopped",
        title="任务失败" if failed else "任务已取消",
        detail=detail,
        source="runtime",
        clock=clock,
    )


def timeout_progress_payload(message: str, *, clock: Callable[[], datetime] | None = None) -> dict[str, Any]:
    return progress_payload(
        status="error",
        title="任务超时",
        detail=message,
        source="runtime",
        clock=clock,
    )


def runtime_exception_progress_payload(error: Any, *, clock: Callable[[], datetime] | None = None) -> dict[str, Any]:
    return progress_payload(
        status="error",
        title="任务异常",
        detail=str(error),
        source="runtime",
        clock=clock,
    )


def completed_run_progress_payload(
    detail: str = "结果已写入对话并同步历史记录",
    *,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    return progress_payload(
        status="completed",
        title="任务完成",
        detail=detail,
        current=1,
        total=1,
        clock=clock,
    )


def user_stopped_progress_payload(message: str, *, clock: Callable[[], datetime] | None = None) -> dict[str, Any]:
    return progress_payload(
        status="stopped",
        title="任务已停止",
        detail=message,
        source="runtime",
        clock=clock,
    )


def reasoning_progress_payload(text: str | None, *, clock: Callable[[], datetime] | None = None) -> dict[str, Any]:
    return progress_payload(
        status="running",
        title="正在推理",
        detail=text[:180] if text else None,
        source="reasoning",
        clock=clock,
    )


def orphaned_run_progress_payload(message: str, *, clock: Callable[[], datetime] | None = None) -> dict[str, Any]:
    return progress_payload(
        status="stopped",
        title="后台任务已不存在",
        detail=message,
        source="runtime",
        clock=clock,
    )


def heartbeat_progress_payload(*, clock: Callable[[], datetime] | None = None) -> dict[str, Any]:
    return progress_payload(
        status="running",
        title="等待模型或工具返回",
        detail="后台 Hermes run 仍在运行；本地模型可能正在生成首轮输出，或工具正在执行。",
        source="runtime",
        clock=clock,
    )


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
