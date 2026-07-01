from datetime import datetime

from services import agent_chat_runtime as runtime
from services import agent_runtime_progress


def test_progress_payload_and_signature_are_stable_with_clock():
    clock = lambda: datetime(2026, 6, 30, 12, 0, 0)
    payload = agent_runtime_progress.progress_payload(
        title="分析年度报告",
        detail="读取表格",
        current=2,
        total=4,
        source="agent_output",
        clock=clock,
    )

    assert payload["percent"] == 50
    assert payload["updated_at"] == "2026-06-30T12:00:00"
    assert agent_runtime_progress.progress_signature(payload, hash_text=runtime._hash_text) == runtime._progress_signature(payload)


def test_progress_payload_clamps_percent_and_defaults_completed():
    clock = lambda: datetime(2026, 6, 30, 12, 0, 0)

    over_done = agent_runtime_progress.progress_payload(
        title="  ",
        current=9,
        total=4,
        clock=clock,
    )
    below_zero = agent_runtime_progress.progress_payload(
        title="回滚",
        current=-1,
        total=4,
        clock=clock,
    )
    completed = agent_runtime_progress.progress_payload(
        status="completed",
        title="结束",
        total=0,
        clock=clock,
    )

    assert over_done["title"] == "正在执行任务"
    assert over_done["percent"] == 100
    assert below_zero["percent"] == 0
    assert completed["percent"] == 100


def test_extract_progress_from_text_uses_last_progress_line():
    text = "\n".join(
        [
            "普通输出",
            "[1/4] 读取公司信息 [░░░]",
            "[4/4] 完成报告 [████] 写入结果",
        ]
    )

    payload = runtime._extract_progress_from_text(text)

    assert payload["status"] == "completed"
    assert payload["percent"] == 100
    assert payload["title"] == "完成报告"
    assert payload["detail"] == "写入结果"


def test_extract_progress_from_text_ignores_malformed_and_old_progress_lines():
    clock = lambda: datetime(2026, 6, 30, 12, 0, 0)
    old_progress = "[1/3] 旧步骤 [░░░]"
    text = "\n".join(
        [old_progress]
        + [f"普通输出 {index}" for index in range(12)]
        + [
            "[x/3] 坏步骤",
            "[2/3]",
        ]
    )

    assert agent_runtime_progress.extract_progress_from_text(text, clock=clock) is None

    payload = agent_runtime_progress.extract_progress_from_text(
        "普通输出\n[2/3] 新步骤 [██░] 继续处理",
        clock=clock,
    )

    assert payload is not None
    assert payload["status"] == "running"
    assert payload["percent"] == 67
    assert payload["title"] == "新步骤"
    assert payload["detail"] == "继续处理"


def test_trim_tool_preview_handles_blank_and_limit_boundary():
    assert agent_runtime_progress.trim_tool_preview(None) == ""
    assert agent_runtime_progress.trim_tool_preview("  abc  ", limit=3) == "abc"
    assert agent_runtime_progress.trim_tool_preview("abcd", limit=3) == "abc..."


def test_display_tool_label_uses_current_runtime_wiki_root(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime, "PROJECT_WIKI_ROOT", tmp_path)
    monkeypatch.setattr(runtime, "WIKI_ROOT", tmp_path)
    preview = f"rg 商誉 {tmp_path}/companies"

    assert runtime._display_tool_label("terminal", preview) == "Search file"
    assert runtime._display_tool_label("execute_code", "") == "Code execution"


def test_file_search_tool_detection_avoids_terminal_false_positive(tmp_path):
    assert agent_runtime_progress.is_file_search_tool_invocation("search_files")
    assert agent_runtime_progress.is_file_search_tool_invocation("read_file")
    assert agent_runtime_progress.is_file_search_tool_invocation(
        "terminal",
        f"cat {tmp_path}/companies/600104/report.md",
        project_wiki_root=tmp_path,
    )
    assert agent_runtime_progress.is_file_search_tool_invocation(
        "terminal",
        "python resolve_company.py 600104",
    )
    assert not agent_runtime_progress.is_file_search_tool_invocation("terminal", "echo target")
    assert not agent_runtime_progress.is_file_search_tool_invocation("execute_code", "rg 商誉")
    assert agent_runtime_progress.display_tool_label(None, "") == "工具"
