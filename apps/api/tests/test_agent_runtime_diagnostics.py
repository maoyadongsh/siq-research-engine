import json
import time
from pathlib import Path

from services import agent_runtime_diagnostics as diagnostics


def _runtime_profile(profile: str) -> str:
    return "siq_assistant" if profile == "assistant" else profile


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_diagnose_latest_hermes_session_reports_active_gateway(tmp_path):
    profile_dir = tmp_path / "profiles" / "siq_assistant"
    _write_json(profile_dir / "gateway_state.json", {"active_agents": 2})
    session_file = profile_dir / "sessions" / "latest.json"
    _write_json(session_file, {"messages": [], "last_updated": "now"})

    result = diagnostics.diagnose_latest_hermes_session(
        "siq_assistant",
        profile_dirs={"siq_assistant": profile_dir},
        profile_labels={"siq_assistant": "通用助手", "siq_analysis": "智能分析助手"},
        wiki_root=tmp_path / "wiki",
        runtime_profile=_runtime_profile,
        normalize_tool_output=lambda value: ("ok", str(value)),
        detect_output_loop=lambda _text: None,
        hash_text=lambda text: f"hash:{text}",
        max_age_seconds=3600,
    )

    assert result is not None
    assert result["issue"] == "external_run_active"
    assert result["active_agents"] == 2
    assert result["session_file"] == str(session_file)


def test_diagnose_latest_hermes_session_reports_tool_loop(tmp_path):
    profile_dir = tmp_path / "profiles" / "siq_assistant"
    _write_json(profile_dir / "gateway_state.json", {"active_agents": 0})
    session_file = profile_dir / "sessions" / "loop.json"
    _write_json(
        session_file,
        {
            "last_updated": "now",
            "messages": [
                {"role": "assistant", "content": "正常输出"},
                {"role": "tool", "name": "read_file", "content": "same output"},
                {"role": "tool", "name": "read_file", "content": "same output"},
                {"role": "tool", "name": "read_file", "content": "same output"},
            ],
        },
    )

    result = diagnostics.diagnose_latest_hermes_session(
        "siq_assistant",
        profile_dirs={"siq_assistant": profile_dir},
        profile_labels={"siq_assistant": "通用助手", "siq_analysis": "智能分析助手"},
        wiki_root=tmp_path / "wiki",
        runtime_profile=_runtime_profile,
        normalize_tool_output=lambda value: ("ok", str(value)),
        detect_output_loop=lambda _text: None,
        hash_text=lambda text: f"hash:{text}",
        max_age_seconds=3600,
    )

    assert result is not None
    assert result["issue"] == "tool_loop_no_progress"
    assert result["last_repeated_tool"] == "read_file"
    assert result["last_repeated_output_hash"] == "hash:same output"


def test_latest_successful_analysis_recovery_reads_latest_ok_result(tmp_path):
    old = tmp_path / "wiki" / "companies" / "000001-A" / "analysis" / ".work" / "old" / "recovery_result.json"
    latest = tmp_path / "wiki" / "companies" / "000002-B" / "analysis" / ".work" / "latest" / "recovery_result.json"
    _write_json(old, {"ok": True, "validation": {"metrics": {"json_sections": 1}}, "files": {"html": "old.html"}})
    _write_json(
        latest,
        {
            "ok": True,
            "validation": {"metrics": {"json_sections": 3, "markdown_h2": 4, "html_h2": 5, "api_pdf_links": 6}},
            "files": {"html": "latest.html"},
        },
    )
    now = time.time()
    old.touch()
    latest.touch()
    old_mtime = now - 60
    latest_mtime = now
    old.chmod(0o600)
    latest.chmod(0o600)
    import os

    os.utime(old, (old_mtime, old_mtime))
    os.utime(latest, (latest_mtime, latest_mtime))

    result = diagnostics.latest_successful_analysis_recovery(
        wiki_root=tmp_path / "wiki",
        profile_labels={"siq_analysis": "智能分析助手"},
    )

    assert result is not None
    assert result["issue"] == "last_recovery_completed"
    assert result["html"] == "latest.html"
    assert "json_sections=3" in result["detail"]
