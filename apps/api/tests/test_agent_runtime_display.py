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


def test_display_message_with_no_attachments_returns_trimmed_message_or_empty_string():
    assert agent_runtime_display._display_message_with_attachments("  分析一下  ", None) == "分析一下"
    assert agent_runtime_display._display_message_with_attachments("", []) == ""


def test_display_message_with_multiple_attachments_and_empty_message_uses_plural_prompt():
    result = agent_runtime_display._display_message_with_attachments(
        "",
        [
            {"filename": "chart.png", "kind": "image", "url": "/api/chat/attachments/1"},
            {"filename": "report.pdf", "kind": "document"},
        ],
    )

    assert result.startswith("请分析这些附件\n\n")
    assert "![图片: chart.png](/api/chat/attachments/1)" in result
    assert "[文档: report.pdf]" in result


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


def test_display_message_with_attachments_uses_path_name_when_filename_blank_and_normalizes_kind():
    result = agent_runtime_display._display_message_with_attachments(
        "",
        [
            {
                "filename": "  ",
                "kind": " IMAGE ",
                "url": "/api/chat/attachments/chart.png",
                "path": "/tmp/reports/chart.png",
            }
        ],
    )

    assert result == "请分析这个附件\n\n![图片: chart.png](/api/chat/attachments/chart.png)"


def test_display_message_with_attachments_uses_generic_label_when_name_missing():
    result = agent_runtime_display._display_message_with_attachments(
        "",
        [{"kind": "document"}],
    )

    assert result == "请分析这个附件\n\n[文档: attachment]"


def test_display_message_with_unknown_kind_uses_document_link():
    result = agent_runtime_display._display_message_with_attachments(
        "看下附件",
        [{"filename": "report.pdf", "kind": "pdf", "url": "/api/chat/attachments/report.pdf"}],
    )

    assert result == "看下附件\n\n[文档: report.pdf](/api/chat/attachments/report.pdf)"


def test_display_message_with_attachments_omits_blank_url_link_target():
    result = agent_runtime_display._display_message_with_attachments(
        "",
        [{"filename": "report.pdf", "kind": "document", "url": "   "}],
    )

    assert result == "请分析这个附件\n\n[文档: report.pdf]"


def test_markdown_link_label_strips_whitespace_and_brackets():
    assert agent_runtime_display._markdown_link_label("  图[表]\nA  ") == "图(表) A"


def test_markdown_link_url_preserves_structure_and_encodes_values():
    assert (
        agent_runtime_display._markdown_link_url("/api/file?name=a b(1).png&token=x#p 1")
        == "/api/file?name=a%20b%281%29.png&token=x#p%201"
    )
    assert agent_runtime_display._markdown_link_url("  https://host/a b.png?x=1&y=two words  ") == (
        "https://host/a%20b.png?x=1&y=two%20words"
    )
    assert agent_runtime_display._markdown_link_url(None) == ""


def test_display_message_with_attachments_escapes_markdown_link_url():
    result = agent_runtime_display._display_message_with_attachments(
        "",
        [
            {
                "filename": "chart [draft].png",
                "kind": "image",
                "url": "/api/chat/attachments/chart draft(1).png",
            }
        ],
    )

    assert "![图片: chart (draft).png](/api/chat/attachments/chart%20draft%281%29.png)" in result
    assert agent_runtime_display._markdown_link_url("/api/a b(1).png") == "/api/a%20b%281%29.png"


def test_display_message_with_mixed_url_attachments_keeps_each_item_independent():
    result = agent_runtime_display._display_message_with_attachments(
        "",
        [
            {"filename": "draft [v1].pdf", "kind": "document", "url": " "},
            {"filename": "chart final.png", "kind": "image", "url": "/api/chat/attachments/chart final.png"},
        ],
    )

    assert result == (
        "请分析这些附件\n\n"
        "[文档: draft (v1).pdf]\n"
        "![图片: chart final.png](/api/chat/attachments/chart%20final.png)"
    )


def test_display_message_with_document_url_encodes_absolute_url_and_path_filename():
    result = agent_runtime_display._display_message_with_attachments(
        "",
        [
            {
                "kind": "document",
                "path": "/tmp/reports/board pack [final].pdf",
                "url": "https://files.example.com/board pack [final].pdf?download=1",
            }
        ],
    )

    assert result == (
        "请分析这个附件\n\n"
        "[文档: board pack (final).pdf]"
        "(https://files.example.com/board%20pack%20%5Bfinal%5D.pdf?download=1)"
    )
