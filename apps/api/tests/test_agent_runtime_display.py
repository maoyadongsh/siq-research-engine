from services import agent_chat_runtime as runtime
from services import agent_runtime_display


def test_display_message_with_attachments_uses_default_prompt_for_single_attachment():
    result = agent_runtime_display._display_message_with_attachments(
        "",
        [
            {
                "filename": "chart.png",
                "kind": "image",
                "url": "/api/chat/attachments/1",
                "path": "/tmp/chart.png",
            }
        ],
    )

    assert result.startswith("请分析这个附件\n\n")
    assert "![图片: chart.png](/api/chat/attachments/1)" in result


def test_display_message_with_attachments_uses_plural_prompt_for_multiple_attachments():
    result = agent_runtime_display._display_message_with_attachments(
        "  请看一下  ",
        [
            {"filename": "chart.png", "kind": "image", "url": "/api/chat/attachments/1"},
            {"filename": "report.pdf", "kind": "document"},
        ],
    )

    assert result.startswith("请看一下\n\n")
    assert "![图片: chart.png](/api/chat/attachments/1)" in result
    assert "[文档: report.pdf]" in result
    assert "请分析这些附件" not in result


def test_display_message_with_attachments_is_reexported_by_chat_runtime():
    result = runtime._display_message_with_attachments(
        "分析一下",
        [{"filename": "note.docx", "kind": "document", "path": "/tmp/note.docx"}],
    )

    assert result == "分析一下\n\n[文档: note.docx]"


def test_display_message_with_attachments_uses_path_name_when_filename_missing():
    result = agent_runtime_display._display_message_with_attachments(
        "看下附件",
        [{"kind": "document", "url": "/api/chat/attachments/doc-1", "path": "/tmp/reports/report.pdf"}],
    )

    assert result == "看下附件\n\n[文档: report.pdf](/api/chat/attachments/doc-1)"


def test_display_message_with_attachments_uses_generic_label_when_name_missing():
    result = agent_runtime_display._display_message_with_attachments(
        "",
        [{"kind": "document"}],
    )

    assert result == "请分析这个附件\n\n[文档: attachment]"


def test_markdown_link_label_strips_whitespace_and_brackets():
    assert agent_runtime_display._markdown_link_label("  图[表]\nA  ") == "图(表) A"
