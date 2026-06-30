from services import agent_chat_runtime as runtime
from services.agent_runtime_tool_output import normalize_tool_output


def test_normalize_tool_output_preserves_plain_text():
    assert normalize_tool_output("  raw output  ") == (None, "raw output")


def test_normalize_tool_output_extracts_status_and_output_from_json():
    content = '{"status":"ok","output":" completed "}'

    assert normalize_tool_output(content) == ("ok", "completed")


def test_normalize_tool_output_accepts_structured_content():
    assert normalize_tool_output({"status": "failed", "content": " details "}) == ("failed", "details")


def test_runtime_private_wrapper_keeps_compatibility():
    assert runtime._normalize_tool_output({"status": "ok", "output": "done"}) == normalize_tool_output(
        {"status": "ok", "output": "done"}
    )
