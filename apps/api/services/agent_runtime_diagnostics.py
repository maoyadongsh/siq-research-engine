import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from services.hermes_client import HermesProfile


def read_json_file(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def profile_diagnostic_context(
    profile: HermesProfile,
    session_file: Path | None = None,
    *,
    runtime_profile: Callable[[HermesProfile], HermesProfile],
    profile_labels: dict[HermesProfile, str],
) -> dict[str, Any]:
    profile = runtime_profile(profile)
    return {
        "scope": "profile",
        "profile": profile,
        "profile_label": profile_labels.get(profile, profile),
        "session_file": str(session_file) if session_file else None,
    }


def session_age_seconds(path: Path) -> float:
    return max(0.0, (datetime.utcnow() - datetime.utcfromtimestamp(path.stat().st_mtime)).total_seconds())


def is_recent_diagnostic_session(path: Path, *, max_age_seconds: int) -> bool:
    return session_age_seconds(path) <= max_age_seconds


def recent_hermes_sessions(
    profile: HermesProfile,
    *,
    profile_dirs: dict[HermesProfile, Path],
    runtime_profile: Callable[[HermesProfile], HermesProfile],
    limit: int = 20,
) -> list[Path]:
    profile = runtime_profile(profile)
    profile_dir = profile_dirs.get(profile)
    sessions_dir = profile_dir / "sessions" if profile_dir else None
    if not sessions_dir or not sessions_dir.exists():
        return []

    candidates = [path for path in sessions_dir.glob("*.json") if path.is_file()]
    if not candidates:
        return []
    return sorted(candidates, key=lambda path: path.stat().st_mtime, reverse=True)[:limit]


def latest_hermes_session(
    profile: HermesProfile,
    *,
    profile_dirs: dict[HermesProfile, Path],
    runtime_profile: Callable[[HermesProfile], HermesProfile],
) -> Path | None:
    recent = recent_hermes_sessions(profile, profile_dirs=profile_dirs, runtime_profile=runtime_profile, limit=1)
    return recent[0] if recent else None


def latest_successful_analysis_recovery(
    *,
    wiki_root: Path,
    profile_labels: dict[HermesProfile, str],
) -> dict[str, Any] | None:
    analysis_root = wiki_root / "companies"
    if not analysis_root.exists():
        return None
    candidates = [
        path
        for path in analysis_root.glob("*/analysis/.work/*/recovery_result.json")
        if path.is_file()
    ]
    if not candidates:
        return None
    latest = max(candidates, key=lambda path: path.stat().st_mtime)
    payload = read_json_file(latest)
    if not isinstance(payload, dict) or not payload.get("ok"):
        return None
    files = payload.get("files") if isinstance(payload.get("files"), dict) else {}
    validation = payload.get("validation") if isinstance(payload.get("validation"), dict) else {}
    metrics = validation.get("metrics") if isinstance(validation.get("metrics"), dict) else {}
    return {
        "scope": "profile",
        "profile": "siq_analysis",
        "profile_label": profile_labels["siq_analysis"],
        "severity": "info",
        "issue": "last_recovery_completed",
        "title": "最近一次恢复已完成",
        "detail": (
            f"确定性恢复流程已通过验收：json_sections={metrics.get('json_sections', '未返回')}，"
            f"markdown_h2={metrics.get('markdown_h2', '未返回')}，"
            f"html_h2={metrics.get('html_h2', '未返回')}，"
            f"api_pdf_links={metrics.get('api_pdf_links', '未返回')}。"
        ),
        "recovery_action": "可以打开恢复生成的 HTML/MD/JSON；后续分析任务若遇到检查点不完整，应继续使用 recover_report_from_workdir.py。",
        "recovery_result": str(latest),
        "html": files.get("html"),
        "last_updated": datetime.fromtimestamp(latest.stat().st_mtime).isoformat(),
    }


def diagnose_latest_hermes_session(
    profile: HermesProfile,
    *,
    profile_dirs: dict[HermesProfile, Path],
    profile_labels: dict[HermesProfile, str],
    wiki_root: Path,
    runtime_profile: Callable[[HermesProfile], HermesProfile],
    normalize_tool_output: Callable[[Any], tuple[str | None, str]],
    detect_output_loop: Callable[[str], dict[str, Any] | None],
    hash_text: Callable[[str], str],
    max_age_seconds: int,
) -> dict[str, Any] | None:
    profile_dir = profile_dirs.get(profile)
    if not profile_dir:
        return None

    profile_label = profile_labels.get(profile, profile)
    gateway_state = read_json_file(profile_dir / "gateway_state.json") or {}
    active_agents = int(gateway_state.get("active_agents") or 0)
    recent_sessions = recent_hermes_sessions(profile, profile_dirs=profile_dirs, runtime_profile=runtime_profile)
    latest_session = recent_sessions[0] if recent_sessions else None
    diagnostic_context = lambda session: profile_diagnostic_context(
        profile,
        session,
        runtime_profile=runtime_profile,
        profile_labels=profile_labels,
    )
    if active_agents > 0:
        return {
            **diagnostic_context(latest_session),
            "severity": "info",
            "issue": "external_run_active",
            "title": "后台仍在运行",
            "detail": f"{profile_label} 的 Hermes profile 显示仍有 {active_agents} 个活跃 agent，网页连接可能已断开，可稍后刷新或重新接入。",
            "recovery_action": "等待后台 run 完成，或通过停止按钮结束后重新发起任务。",
            "active_agents": active_agents,
        }

    if profile == "siq_analysis":
        recovery = latest_successful_analysis_recovery(wiki_root=wiki_root, profile_labels=profile_labels)
        if recovery:
            return recovery
    if not recent_sessions:
        return None

    for latest_session in recent_sessions:
        if not is_recent_diagnostic_session(latest_session, max_age_seconds=max_age_seconds):
            continue
        session_data = read_json_file(latest_session)
        if not isinstance(session_data, dict):
            continue

        messages = session_data.get("messages")
        if not isinstance(messages, list):
            continue

        for message in reversed(messages):
            if not isinstance(message, dict) or message.get("role") != "assistant":
                continue
            text_loop = detect_output_loop(str(message.get("content") or ""))
            if text_loop:
                return {
                    **diagnostic_context(latest_session),
                    "severity": "warning",
                    "issue": "text_output_loop_no_progress",
                    "title": "检测到输出循环",
                    "detail": (
                        f"最近一次 {profile_label} 回复在“{text_loop['sample']}”附近反复输出或逐页扫描，"
                        f"命中行 {text_loop['repeated_lines']} 行、不同形态 "
                        f"{text_loop['unique_lines']} 个；说明模型停留在检索过程，没有继续产生可验证结论。"
                    ),
                    "recovery_action": "从 .work 检查点或已生成文件续跑；必要时使用确定性渲染/验收脚本，而不是继续让模型重复叙述。",
                    "active_agents": active_agents,
                    "last_updated": session_data.get("last_updated"),
                }
            break

        tool_events: list[tuple[str, str | None, str]] = []
        max_tool_iteration_notice = False
        for message in messages[-40:]:
            if not isinstance(message, dict):
                continue
            if message.get("role") == "tool":
                status, output = normalize_tool_output(message.get("content"))
                tool_events.append((str(message.get("name") or "unknown"), status, output))
            elif message.get("role") == "user" and "maximum number of tool-calling iterations" in str(message.get("content") or ""):
                max_tool_iteration_notice = True

        repeated_count = 0
        repeated_tool = ""
        repeated_output = ""
        if tool_events:
            repeated_tool, last_status, repeated_output = tool_events[-1]
            for tool_name, status, output in reversed(tool_events):
                if tool_name == repeated_tool and status == last_status and output == repeated_output:
                    repeated_count += 1
                else:
                    break

        if repeated_count >= 3 or max_tool_iteration_notice:
            output_hash = hash_text(repeated_output) if repeated_output else None
            return {
                **diagnostic_context(latest_session),
                "severity": "warning",
                "issue": "tool_loop_no_progress",
                "title": "工具循环已中断",
                "detail": (
                    f"最近一次 {profile_label} run 没有活跃进程，且 {repeated_tool or '工具'} 连续 "
                    f"{repeated_count or '多'} 次返回相同结果，系统随后触发工具调用上限。"
                ),
                "recovery_action": "从 .work 检查点续跑，或直接进入渲染、溯源修复和质量验收阶段。",
                "active_agents": active_agents,
                "last_repeated_tool": repeated_tool or None,
                "last_repeated_output_hash": output_hash,
                "last_updated": session_data.get("last_updated"),
            }

        # The diagnostic endpoint is meant to describe the latest run state.
        # Once the newest valid Hermes session has no loop/failure signal, do
        # not surface stale warnings from older session snapshots.
        return None

    return None
