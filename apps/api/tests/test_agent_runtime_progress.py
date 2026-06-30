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


def test_display_tool_label_uses_current_runtime_wiki_root(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime, "PROJECT_WIKI_ROOT", tmp_path)
    monkeypatch.setattr(runtime, "WIKI_ROOT", tmp_path)
    preview = f"rg 商誉 {tmp_path}/companies"

    assert runtime._display_tool_label("terminal", preview) == "Search file"
    assert runtime._display_tool_label("execute_code", "") == "Code execution"
