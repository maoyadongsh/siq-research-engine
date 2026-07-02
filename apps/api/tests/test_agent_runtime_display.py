import json

import anyio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from models import ChatMessage
from services import agent_chat_runtime as runtime
from services import agent_runtime_display

SH_BANK_TASK_ID = "fb07089b-9570-4902-bf20-eb38578f2b76"


async def _with_temp_chat_history(tmp_path, callback):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'display-history.db'}")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        async with AsyncSession(engine) as session:
            return await callback(session)
    finally:
        await engine.dispose()


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


def test_display_message_with_attachments_omits_none_url_link_target_for_image():
    result = agent_runtime_display._display_message_with_attachments(
        "",
        [{"filename": "chart.png", "kind": "image", "url": None}],
    )

    assert result == "请分析这个附件\n\n[图片: chart.png]"


def test_display_message_with_attachments_uses_generic_label_when_path_has_no_basename():
    result = agent_runtime_display._display_message_with_attachments(
        "看下附件",
        [{"filename": " ", "kind": "document", "path": "/", "url": "/api/chat/attachments/root"}],
    )

    assert result == "看下附件\n\n[文档: attachment](/api/chat/attachments/root)"


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


def test_markdown_link_url_encodes_internal_control_whitespace():
    assert (
        agent_runtime_display._markdown_link_url("/api/file?name=line\nbreak\tchart.png")
        == "/api/file?name=line%0Abreak%09chart.png"
    )


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


def test_chat_history_response_limits_after_visible_filtering_and_session_scope(tmp_path):
    async def run_case(async_session):
        session_id = "display-history-session"
        async_session.add_all(
            [
                ChatMessage(session_id="foreign-display-history-session", role="user", content="foreign"),
                ChatMessage(session_id=session_id, role="user", content="旧问题"),
                ChatMessage(session_id=session_id, role="user", content=""),
                ChatMessage(session_id=session_id, role="assistant", content="旧回答"),
                ChatMessage(
                    session_id=session_id,
                    role="user",
                    content="",
                    attachments_json=json.dumps(
                        [
                            {
                                "filename": "chart.png",
                                "kind": "image",
                                "path": "/tmp/chart.png",
                            }
                        ],
                        ensure_ascii=False,
                    ),
                ),
                ChatMessage(session_id=session_id, role="assistant", content="附件回答"),
                ChatMessage(session_id=session_id, role="user", content="最后问题"),
            ]
        )
        await async_session.commit()
        return await runtime.chat_history_response(async_session, session_id, limit=3)

    messages = anyio.run(_with_temp_chat_history, tmp_path, run_case)

    assert [item["role"] for item in messages] == ["user", "assistant", "user"]
    assert messages[0]["content"] == ""
    assert messages[0]["attachments"] == [
        {
            "filename": "chart.png",
            "kind": "image",
            "path": "/tmp/chart.png",
        }
    ]
    assert messages[1]["content"] == "附件回答"
    assert messages[2]["content"] == "最后问题"
    assert all(item["session_id"] == "display-history-session" for item in messages)
    assert all(item["content"] != "foreign" for item in messages)
    assert all(item["content"] != "旧问题" for item in messages)
    assert all(item["content"] != "旧回答" for item in messages)


def test_chat_history_response_uses_display_payload_contract_for_real_db_rows(tmp_path):
    polluted = (
        "[系统已整理] 上一轮助手输出疑似进入循环，详细重复内容已从后续上下文中移除。"
        "请基于当前用户问题重新定位数据，不要沿用上一轮的逐页扫描或重复搜索过程。\n\n"
        "## 引用来源\n"
        "[D1] source_type=wiki_document_links, file=semantic/document_links.json"
    )
    evidence = (
        "[1] source_type=report_md, file=reports/2025-annual/report.md, "
        f"metric=前十名普通股股东, task_id={SH_BANK_TASK_ID}, "
        "pdf_page=135, table_index=135, md_line=2428。"
    )

    async def run_case(async_session):
        session_id = "display-history-payload-contract"
        async_session.add_all(
            [
                ChatMessage(session_id=session_id, role="user", content="请看股东表"),
                ChatMessage(session_id=session_id, role="assistant", content=polluted),
                ChatMessage(session_id=session_id, role="assistant", content=evidence),
            ]
        )
        await async_session.commit()
        return await runtime.chat_history_response(async_session, session_id, limit=5)

    messages = anyio.run(_with_temp_chat_history, tmp_path, run_case)

    assert [item["role"] for item in messages] == ["user", "assistant", "assistant"]
    assert messages[0]["content"] == "请看股东表"
    assert messages[1]["content"] == runtime.OUTPUT_LOOP_STOP_MESSAGE
    assert "系统已整理" not in messages[1]["content"]
    assert "source_type=wiki_document_links" not in messages[1]["content"]
    assert "pdf_page=134" in messages[2]["content"]
    assert "table_index=90" in messages[2]["content"]
    assert "printed_page=133" in messages[2]["content"]
    assert f"/api/pdf_page/{SH_BANK_TASK_ID}/134?format=html" in messages[2]["content"]
    assert f"/api/source/{SH_BANK_TASK_ID}/table/90?format=html" in messages[2]["content"]


def test_chat_history_response_uses_runtime_visibility_and_payload_patch_points(tmp_path, monkeypatch):
    async def run_case(async_session):
        session_id = "display-history-wrapper-session"
        async_session.add_all(
            [
                ChatMessage(session_id=session_id, role="user", content="first"),
                ChatMessage(session_id=session_id, role="assistant", content="hidden"),
                ChatMessage(session_id=session_id, role="user", content="second"),
            ]
        )
        await async_session.commit()

        visibility_calls: list[str] = []
        payload_calls: list[str] = []

        def fake_chat_message_has_visible_payload(message):
            visibility_calls.append(message.content)
            return message.content != "hidden"

        def fake_chat_message_payload(message):
            payload_calls.append(message.content)
            return {"role": message.role, "content": f"payload:{message.content}"}

        monkeypatch.setattr(
            runtime,
            "chat_message_has_visible_payload",
            fake_chat_message_has_visible_payload,
        )
        monkeypatch.setattr(runtime, "_chat_message_payload", fake_chat_message_payload)

        messages = await runtime.chat_history_response(async_session, session_id, limit=2)
        return visibility_calls, payload_calls, messages

    visibility_calls, payload_calls, messages = anyio.run(
        _with_temp_chat_history,
        tmp_path,
        run_case,
    )

    assert visibility_calls == ["first", "hidden", "second"]
    assert payload_calls == ["first", "second"]
    assert messages == [
        {"role": "user", "content": "payload:first"},
        {"role": "user", "content": "payload:second"},
    ]
