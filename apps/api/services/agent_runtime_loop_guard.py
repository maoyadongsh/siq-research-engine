"""Loop detection and history sanitization for the Hermes agent runtime."""

from __future__ import annotations

import re
from typing import Any

STOPPED_MESSAGE = "[已停止] 本次对话已停止，后台 Hermes run 已收到停止请求。"
TIMEOUT_MESSAGE = "[已停止] 本次对话超过网页聊天时限，已自动停止。长时间任务建议改用后台工作流。"
IDLE_TIMEOUT_MESSAGE = "[已停止] 智能体长时间没有返回新事件，系统已自动停止本次回答。请重新发送问题或缩小查询范围。"
ORPHANED_RUN_MESSAGE = "[已停止] 后台 Hermes run 已不存在或已被网关清理，本地已结束该任务状态。"
OUTPUT_LOOP_STOP_MESSAGE = (
    "[已停止] 检测到智能体重复输出同一阶段，疑似进入文本循环。"
    "系统已停止本次 Hermes run；请从已生成文件或 .work 检查点继续，避免重复从头生成。"
)
HISTORY_LOOP_SANITIZED_MESSAGE = (
    "[上一轮助手输出已因循环被系统截断，不能作为事实依据。"
    "请基于当前用户问题重新定位数据，不要沿用上一轮的逐页扫描或重复搜索过程。]"
)
LEGACY_HISTORY_LOOP_SANITIZED_PREFIX = "[系统已整理] 上一轮助手输出疑似进入循环"
TOOL_FAILURE_STOP_MESSAGE = (
    "[已停止] 检测到智能体连续调用工具失败，疑似进入工具错误循环。"
    "系统已停止本次 Hermes run；请先修复路径、环境或输入条件后再重新执行。"
)
REPEATED_TOOL_CALL_STOP_MESSAGE = (
    "[已停止] 检测到智能体反复调用同一个工具且没有产生新回复，疑似进入工具调用循环。"
    "系统已停止本次 Hermes run；请缩小问题范围或新建会话后重试。"
)
RUN_FAILED_MESSAGE = "[失败] 后台 Hermes run 返回失败状态，系统已结束本次任务。"
RUN_CANCELLED_MESSAGE = "[已取消] 后台 Hermes run 已取消，系统已结束本次任务。"
CONSECUTIVE_TOOL_ERROR_LIMIT = 3
REPEATED_TOOL_CALL_LIMIT = 5

OUTPUT_LOOP_SAME_LINE_LIMIT = 10
OUTPUT_LOOP_TAIL_MIN_LINES = 24
OUTPUT_LOOP_TAIL_WINDOW = 40
OUTPUT_LOOP_UNIQUE_LIMIT = 4
OUTPUT_LOOP_SHORT_LINE_MAX = 140
OUTPUT_LOOP_TRIGGER_TERMS = (
    "继续执行",
    "让我继续",
    "报告生成",
    "HTML渲染",
    "完成HTML",
    "处理HTML",
    "脚本执行超时",
    "分步骤执行",
    "分步执行",
    "读取第",
    "继续读取",
    "逐页",
    "搜索",
    "关键词",
    "定位关键词",
    "search_files",
    "local_citations",
    "读取",
    "查看",
    "检查",
    "表格",
    "完整内容",
    "Python",
    "文件",
    "确认当前工作集",
    "重新确认",
)
PAGE_SCAN_LINE_RE = re.compile(
    r"(?:让我|我来|继续|现在|再|先)?[^。\n]{0,32}"
    r"(?:读取|查看|检查|打开|扫描|定位)[^。\n]{0,40}"
    r"第\s*(?P<page>\d{1,4})\s*页"
)
PAGE_SCAN_MIN_LINES = 8
PAGE_SCAN_TAIL_WINDOW = 80
PAGE_SCAN_CONTEXT_TERMS = (
    "商誉",
    "管理层讨论",
    "资产减值",
    "减值",
    "附注",
    "年报",
    "报告",
    "原文",
    "内容",
)
REPEATED_INTENT_MIN_LINES = 8
REPEATED_INTENT_TAIL_WINDOW = 32
REPEATED_INTENT_UNIQUE_LIMIT = 3
REPEATED_INTENT_TERMS = (
    "正确的方式",
    "搜索",
    "关键词",
    "定位",
    "search_files",
    "local_citations",
    "读取",
    "查看",
    "继续",
    "表格",
    "完整内容",
    "Python",
)
PROCESS_TRACE_MIN_LINES = 6
PROCESS_TRACE_TAIL_WINDOW = 28
PROCESS_TRACE_UNIQUE_LIMIT = 8
PROCESS_TRACE_TERMS = (
    "用户要求我",
    "用户问",
    "让我",
    "我需要",
    "现在需要",
    "先读取",
    "继续检查",
    "继续读取",
    "从上下文来看",
    "可能是指",
    "搜索结果",
    "我看到 wiki",
    "我已经",
    "让我用 Python",
    "需要读取",
)


def _normalize_output_loop_line(line: str) -> str:
    normalized = re.sub(r"\s+\[[█░▓▒#=\-\s]{3,}\]\s*", " ", line.strip())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized.rstrip("。.!！")


def _line_looks_like_agent_status(line: str) -> bool:
    if not line or len(line) > OUTPUT_LOOP_SHORT_LINE_MAX:
        return False
    return any(term in line for term in OUTPUT_LOOP_TRIGGER_TERMS)


def _detect_repeated_output_loop(text: str) -> dict[str, Any] | None:
    lines = [
        normalized
        for raw_line in text.splitlines()
        if (normalized := _normalize_output_loop_line(raw_line))
    ]
    if len(lines) < OUTPUT_LOOP_TAIL_MIN_LINES:
        return None

    last_line = lines[-1]
    consecutive = 1
    for line in reversed(lines[:-1]):
        if line != last_line:
            break
        consecutive += 1

    if consecutive >= OUTPUT_LOOP_SAME_LINE_LIMIT and _line_looks_like_agent_status(last_line):
        return {
            "reason": "same_line_repeated",
            "sample": last_line[:120],
            "repeated_lines": consecutive,
            "unique_lines": 1,
        }

    tail = lines[-OUTPUT_LOOP_TAIL_WINDOW:]
    status_lines = [line for line in tail if _line_looks_like_agent_status(line)]
    min_status_lines = max(12, int(len(tail) * 0.65))
    if len(status_lines) < min_status_lines:
        return None

    unique_status_lines = set(status_lines)
    if len(unique_status_lines) > OUTPUT_LOOP_UNIQUE_LIMIT:
        return None

    return {
        "reason": "short_status_tail_repeated",
        "sample": status_lines[-1][:120],
        "repeated_lines": len(status_lines),
        "unique_lines": len(unique_status_lines),
    }


def _detect_linear_page_scan_loop(text: str) -> dict[str, Any] | None:
    lines = [
        normalized
        for raw_line in text.splitlines()
        if (normalized := _normalize_output_loop_line(raw_line))
    ]
    if len(lines) < PAGE_SCAN_MIN_LINES:
        return None

    scan_items: list[tuple[int, str]] = []
    for line in lines[-PAGE_SCAN_TAIL_WINDOW:]:
        match = PAGE_SCAN_LINE_RE.search(line)
        if not match:
            continue
        scan_items.append((int(match.group("page")), line))

    if len(scan_items) < PAGE_SCAN_MIN_LINES:
        return None

    recent = scan_items[-max(PAGE_SCAN_MIN_LINES, 16):]
    pages = [page for page, _line in recent]
    unique_pages = set(pages)
    if len(unique_pages) < PAGE_SCAN_MIN_LINES:
        return None

    longest_run = 1
    current_run = 1
    increasing_pairs = 0
    for previous, current in zip(pages, pages[1:]):
        if previous < current <= previous + 3:
            current_run += 1
            increasing_pairs += 1
        else:
            current_run = 1
        longest_run = max(longest_run, current_run)

    context_hits = sum(1 for _page, line in recent if any(term in line for term in PAGE_SCAN_CONTEXT_TERMS))
    if longest_run < PAGE_SCAN_MIN_LINES and increasing_pairs < PAGE_SCAN_MIN_LINES - 1:
        return None
    if context_hits < max(4, PAGE_SCAN_MIN_LINES // 2):
        return None

    return {
        "reason": "linear_page_scan_loop",
        "sample": recent[-1][1][:120],
        "repeated_lines": len(recent),
        "unique_lines": len(unique_pages),
        "page_start": min(unique_pages),
        "page_end": max(unique_pages),
    }


def _intent_loop_signature(line: str) -> str | None:
    if not line or len(line) > OUTPUT_LOOP_SHORT_LINE_MAX:
        return None
    if sum(1 for term in REPEATED_INTENT_TERMS if term in line) < 2:
        return None
    signature = re.sub(r"第\s*\d{1,4}\s*页", "第N页", line)
    signature = re.sub(r"\d{1,4}", "N", signature)
    signature = re.sub(r"\s+", "", signature)
    return signature[:96]


def _detect_repeated_intent_loop(text: str) -> dict[str, Any] | None:
    lines = [
        normalized
        for raw_line in text.splitlines()
        if (normalized := _normalize_output_loop_line(raw_line))
    ]
    if len(lines) < REPEATED_INTENT_MIN_LINES:
        return None

    signatures = [
        signature
        for line in lines[-REPEATED_INTENT_TAIL_WINDOW:]
        if (signature := _intent_loop_signature(line))
    ]
    if len(signatures) < REPEATED_INTENT_MIN_LINES:
        return None

    tail = signatures[-max(REPEATED_INTENT_MIN_LINES, 12):]
    unique = set(tail)
    if len(unique) > REPEATED_INTENT_UNIQUE_LIMIT:
        return None

    sample = next(
        (
            line
            for line in reversed(lines[-REPEATED_INTENT_TAIL_WINDOW:])
            if _intent_loop_signature(line)
        ),
        tail[-1],
    )
    return {
        "reason": "repeated_search_intent_loop",
        "sample": sample[:120],
        "repeated_lines": len(tail),
        "unique_lines": len(unique),
    }


def _process_trace_signature(line: str) -> str | None:
    if not line or len(line) > OUTPUT_LOOP_SHORT_LINE_MAX:
        return None
    if not any(term in line for term in PROCESS_TRACE_TERMS):
        return None
    signature = re.sub(r"`[^`]+`", "`X`", line)
    signature = re.sub(r'"[^"]{1,80}"', '"X"', signature)
    signature = re.sub(r"\d{1,6}", "N", signature)
    signature = re.sub(r"\s+", "", signature)
    return signature[:96]


def _detect_process_trace_loop(text: str) -> dict[str, Any] | None:
    lines = [
        normalized
        for raw_line in text.splitlines()
        if (normalized := _normalize_output_loop_line(raw_line))
    ]
    if len(lines) < PROCESS_TRACE_MIN_LINES:
        return None
    if re.search(r"^\s*#{1,4}\s*(?:结论|依据/数据|引用来源)\b", text, flags=re.MULTILINE):
        return None

    trace_lines = [
        line
        for line in lines[-PROCESS_TRACE_TAIL_WINDOW:]
        if _process_trace_signature(line)
    ]
    if len(trace_lines) < PROCESS_TRACE_MIN_LINES:
        return None

    signatures = [
        signature
        for line in trace_lines
        if (signature := _process_trace_signature(line))
    ]
    unique = set(signatures)
    if len(unique) > PROCESS_TRACE_UNIQUE_LIMIT:
        return None

    sample = trace_lines[-1]
    return {
        "reason": "process_trace_loop",
        "sample": sample[:120],
        "repeated_lines": len(trace_lines),
        "unique_lines": len(unique),
    }


def _detect_output_loop(text: str) -> dict[str, Any] | None:
    return (
        _detect_repeated_output_loop(text)
        or _detect_linear_page_scan_loop(text)
        or _detect_repeated_intent_loop(text)
        or _detect_process_trace_loop(text)
    )


def _detect_stream_output_loop(profile: object, text: str) -> dict[str, Any] | None:
    if str(profile) == "siq_assistant":
        return _detect_linear_page_scan_loop(text)
    return _detect_output_loop(text)


def _is_loop_polluted_assistant_message(content: str) -> bool:
    if not content:
        return False
    if (
        OUTPUT_LOOP_STOP_MESSAGE in content
        or TOOL_FAILURE_STOP_MESSAGE in content
        or REPEATED_TOOL_CALL_STOP_MESSAGE in content
        or HISTORY_LOOP_SANITIZED_MESSAGE in content
        or LEGACY_HISTORY_LOOP_SANITIZED_PREFIX in content
    ):
        return True
    if RUN_CANCELLED_MESSAGE in content or STOPPED_MESSAGE in content:
        lines = [
            normalized
            for raw_line in content.splitlines()
            if (normalized := _normalize_output_loop_line(raw_line))
        ]
        trace_count = sum(1 for line in lines if _process_trace_signature(line))
        if trace_count >= max(4, len(lines) // 2):
            return True
    return _detect_output_loop(content) is not None


def _sanitize_assistant_history_reply(content: str) -> str:
    if not _is_loop_polluted_assistant_message(content):
        return content
    loop = _detect_output_loop(content) or {}
    details = [HISTORY_LOOP_SANITIZED_MESSAGE]
    if loop.get("reason"):
        details.append(f"循环类型：{loop['reason']}")
    if loop.get("sample"):
        details.append(f"样本：{loop['sample']}")
    return "\n".join(details)


def _assistant_reply_for_display(content: str | None) -> str:
    text = str(content or "")
    if not text:
        return ""
    if _is_loop_polluted_assistant_message(text):
        return OUTPUT_LOOP_STOP_MESSAGE
    return text


def _failed_run_reply_for_history(content: str | None) -> str:
    text = str(content or "").strip()
    if not text:
        return RUN_FAILED_MESSAGE
    if _is_loop_polluted_assistant_message(text):
        return OUTPUT_LOOP_STOP_MESSAGE
    return text


__all__ = [
    "CONSECUTIVE_TOOL_ERROR_LIMIT",
    "HISTORY_LOOP_SANITIZED_MESSAGE",
    "IDLE_TIMEOUT_MESSAGE",
    "LEGACY_HISTORY_LOOP_SANITIZED_PREFIX",
    "ORPHANED_RUN_MESSAGE",
    "OUTPUT_LOOP_STOP_MESSAGE",
    "REPEATED_TOOL_CALL_LIMIT",
    "REPEATED_TOOL_CALL_STOP_MESSAGE",
    "RUN_CANCELLED_MESSAGE",
    "RUN_FAILED_MESSAGE",
    "STOPPED_MESSAGE",
    "TIMEOUT_MESSAGE",
    "TOOL_FAILURE_STOP_MESSAGE",
    "_assistant_reply_for_display",
    "_detect_linear_page_scan_loop",
    "_detect_output_loop",
    "_detect_process_trace_loop",
    "_detect_repeated_intent_loop",
    "_detect_repeated_output_loop",
    "_detect_stream_output_loop",
    "_failed_run_reply_for_history",
    "_intent_loop_signature",
    "_is_loop_polluted_assistant_message",
    "_line_looks_like_agent_status",
    "_normalize_output_loop_line",
    "_process_trace_signature",
    "_sanitize_assistant_history_reply",
]
