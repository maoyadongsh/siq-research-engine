from services import agent_chat_runtime as runtime
from services.agent_runtime_tool_output import normalize_tool_output


def test_normalize_tool_output_preserves_plain_text():
    assert normalize_tool_output("  raw output  ") == (None, "raw output")


def test_normalize_tool_output_extracts_status_and_output_from_json():
    content = '{"status":"ok","output":" completed "}'

    assert normalize_tool_output(content) == ("ok", "completed")


def test_normalize_tool_output_accepts_structured_content():
    assert normalize_tool_output({"status": "failed", "content": " details "}) == ("failed", "details")


def test_normalize_tool_output_handles_none_and_blank_text():
    assert normalize_tool_output(None) == (None, "null")
    assert normalize_tool_output(" \n\t ") == (None, "")


def test_normalize_tool_output_preserves_list_json_shape():
    assert normalize_tool_output(["first", {"second": 2}]) == (None, '["first", {"second": 2}]')


def test_normalize_tool_output_preserves_internal_newlines_without_truncating():
    long_output = "line 1\n" + ("x" * 2048) + "\nline 3"

    assert normalize_tool_output({"status": "ok", "output": f" {long_output} "}) == ("ok", long_output)


def test_normalize_tool_output_does_not_treat_tool_label_as_status_or_output():
    content = {"tool": "analysis.search", "label": "Search Results", "content": " result body "}

    assert normalize_tool_output(content) == (None, "result body")


def test_runtime_private_wrapper_keeps_compatibility():
    assert runtime._normalize_tool_output({"status": "ok", "output": "done"}) == normalize_tool_output(
        {"status": "ok", "output": "done"}
    )
