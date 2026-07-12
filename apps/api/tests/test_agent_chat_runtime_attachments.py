import json
import sys
import threading
import time
from pathlib import Path

import anyio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from models import ChatMessage

from services import agent_chat_runtime as runtime


def test_image_attachment_builds_multimodal_runs_input(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "CHAT_UPLOAD_ROOT", tmp_path)
    image_path = tmp_path / "chart.png"
    image_bytes = b"\x89PNG\r\n\x1a\nfake"
    image_path.write_bytes(image_bytes)

    payload = runtime.build_hermes_run_input(
        "这张图里有什么？",
        profile="siq_assistant",
        session_id="attachment-image-test",
        attachments=[
            {
                "id": "img-1",
                "filename": "chart.png",
                "content_type": "image/png",
                "kind": "image",
                "size": len(image_bytes),
                "path": str(image_path),
            }
        ],
    )

    assert isinstance(payload, list)
    content = payload[0]["content"]
    assert content[0]["type"] == "text"
    assert "用户问题：这张图里有什么？" in content[0]["text"]
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_image_attachment_uses_primary_analysis_without_hermes_image_parts(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "CHAT_UPLOAD_ROOT", tmp_path)
    image_path = tmp_path / "chart.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\nfake")

    payload = runtime.build_hermes_run_input(
        "这张图里有什么？",
        profile="siq_assistant",
        session_id="attachment-image-analysis-test",
        attachments=[
            {
                "id": "img-1",
                "filename": "chart.png",
                "content_type": "image/png",
                "kind": "image",
                "size": image_path.stat().st_size,
                "path": str(image_path),
            }
        ],
        image_analysis_context="图片已优先由本机多模态模型处理。\n\n### 图片 1\n图中包含收入趋势。",
        use_hermes_image_fallback=False,
    )

    assert isinstance(payload, str)
    assert "图片已优先由本机多模态模型处理" in payload
    assert "图中包含收入趋势" in payload
    assert "[Image attached at:" in payload
    assert "image_url" not in payload


def test_history_preserves_attachment_local_path_context(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "CHAT_UPLOAD_ROOT", tmp_path)
    image_path = tmp_path / "image.jpg"
    image_path.write_bytes(b"fake-image")
    message = runtime.ChatMessage(
        role="user",
        session_id="attachment-history-test",
        content="提取一下手写体的文字\n\n![图片: image.jpg](/api/chat/attachments/img_image.jpg)",
        attachments_json=json.dumps(
            [
                {
                    "id": "img",
                    "filename": "image.jpg",
                    "content_type": "image/jpeg",
                    "kind": "image",
                    "size": image_path.stat().st_size,
                    "path": str(image_path),
                    "url": "/api/chat/attachments/img_image.jpg",
                }
            ],
            ensure_ascii=False,
        ),
    )

    history = runtime.normalize_history([message], limit=4)

    assert len(history) == 1
    assert "历史附件上下文" in history[0]["content"]
    assert str(image_path) in history[0]["content"]
    assert "不是 Hermes 8642 网关接口" in history[0]["content"]


def test_message_attachments_ignores_malformed_json_and_items_without_path():
    bad_json = runtime.ChatMessage(
        role="user",
        session_id="attachment-filter-test",
        content="",
        attachments_json="{not-json",
    )
    non_list = runtime.ChatMessage(
        role="user",
        session_id="attachment-filter-test",
        content="",
        attachments_json=json.dumps({"path": "/tmp/image.jpg"}),
    )
    missing_path = runtime.ChatMessage(
        role="user",
        session_id="attachment-filter-test",
        content="",
        attachments_json=json.dumps(
            [
                {"filename": "no-path.png", "path": "  "},
                "not-a-dict",
            ]
        ),
    )

    assert runtime._message_attachments(bad_json) == []
    assert runtime._message_attachments(non_list) == []
    assert runtime._message_attachments(missing_path) == []


def test_chat_message_has_visible_payload_accepts_attachment_only_message(tmp_path):
    image_path = tmp_path / "image.jpg"
    message = runtime.ChatMessage(
        role="user",
        session_id="attachment-visible-payload-test",
        content=" \n\t ",
        attachments_json=json.dumps([{"filename": "image.jpg", "path": str(image_path)}]),
    )
    empty_message = runtime.ChatMessage(
        role="user",
        session_id="attachment-visible-payload-test",
        content=" \n\t ",
        attachments_json=json.dumps([{"filename": "image.jpg", "path": " "}]),
    )

    assert runtime.chat_message_has_visible_payload(message)
    assert not runtime.chat_message_has_visible_payload(empty_message)


def test_history_injects_attachment_reference_for_attachment_only_user_message(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "CHAT_UPLOAD_ROOT", tmp_path)
    image_path = tmp_path / "image.jpg"
    image_path.write_bytes(b"fake-image")
    message = runtime.ChatMessage(
        role="user",
        session_id="attachment-only-history-test",
        content=" ",
        attachments_json=json.dumps(
            [
                {
                    "id": "img",
                    "filename": "image.jpg",
                    "content_type": "image/jpeg",
                    "kind": "image",
                    "size": image_path.stat().st_size,
                    "path": str(image_path),
                    "url": "/api/chat/attachments/img_image.jpg",
                }
            ],
            ensure_ascii=False,
        ),
    )

    history = runtime.normalize_history([message], limit=4)

    assert len(history) == 1
    assert history[0]["role"] == "user"
    assert history[0]["content"].lstrip().startswith("历史附件上下文")
    assert "- 图片附件 1: image.jpg" in history[0]["content"]
    assert f"  - 本地路径: {image_path.resolve()}" in history[0]["content"]
    assert "  - 前端链接: /api/chat/attachments/img_image.jpg" in history[0]["content"]


def test_attachment_reference_context_formats_only_safe_saved_files(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "CHAT_UPLOAD_ROOT", tmp_path)
    image_path = tmp_path / "image.jpg"
    doc_path = tmp_path / "report.pdf"
    outside_path = tmp_path.parent / "outside.pdf"
    image_path.write_bytes(b"fake-image")
    doc_path.write_bytes(b"%PDF-1.4\nfake")
    outside_path.write_bytes(b"%PDF-1.4\noutside")

    context = runtime._attachment_reference_context(
        [
            {
                "filename": "image.jpg",
                "content_type": "image/jpeg",
                "kind": "image",
                "size": image_path.stat().st_size,
                "path": str(image_path),
                "url": "/api/chat/attachments/img_image.jpg",
            },
            {
                "filename": "report.pdf",
                "content_type": "application/pdf",
                "kind": "document",
                "size": doc_path.stat().st_size,
                "path": str(doc_path),
            },
            {
                "filename": "outside.pdf",
                "content_type": "application/pdf",
                "kind": "document",
                "size": outside_path.stat().st_size,
                "path": str(outside_path),
                "url": "/api/chat/attachments/outside.pdf",
            },
            {"filename": "missing.png", "path": str(tmp_path / "missing.png")},
            "not-a-dict",
        ]
    )

    assert context.startswith("历史附件上下文")
    assert "- 图片附件 1: image.jpg" in context
    assert f"  - 本地路径: {image_path.resolve()}" in context
    assert "  - 前端链接: /api/chat/attachments/img_image.jpg" in context
    assert "- 文档附件 2: report.pdf" in context
    assert f"  - 本地路径: {doc_path.resolve()}" in context
    assert "outside.pdf" not in context
    assert "missing.png" not in context


def test_attachment_reference_context_returns_empty_for_no_safe_files(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "CHAT_UPLOAD_ROOT", tmp_path)
    outside_path = tmp_path.parent / "outside.txt"
    outside_path.write_text("outside", encoding="utf-8")

    assert runtime._attachment_reference_context(None) == ""
    assert runtime._attachment_reference_context([]) == ""
    assert runtime._attachment_reference_context([{"filename": "outside.txt", "path": str(outside_path)}]) == ""


def test_attachment_followup_reuses_recent_attachment_intent():
    assert runtime._should_reuse_recent_attachments("继续前面的问题")
    assert runtime._should_reuse_recent_attachments("提取刚才那张照片里的手写体")
    assert not runtime._should_reuse_recent_attachments("上海建工的营收是多少？")


def test_collect_chat_reply_image_attachment_passes_old_history_before_saving_current_user(tmp_path, monkeypatch):
    expected_attachment: dict[str, object] = {
        "filename": "chart.png",
        "content_type": "image/png",
        "kind": "image",
        "size": 0,
        "path": "",
        "url": "/api/chat/attachments/chart.png",
    }

    async def run_case():
        image_path = tmp_path / "chart.png"
        image_path.write_bytes(b"\x89PNG\r\n\x1a\nfake")
        attachment = dict(expected_attachment)
        attachment["size"] = image_path.stat().st_size
        attachment["path"] = str(image_path)

        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'attachment-history-order.db'}")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(SQLModel.metadata.create_all)
            async with AsyncSession(engine) as async_session:
                session_id = "attachment-history-order-session"
                async_session.add_all(
                    [
                        ChatMessage(session_id=session_id, role="user", content="旧问题 1"),
                        ChatMessage(session_id=session_id, role="assistant", content="旧回答 1"),
                    ]
                )
                await async_session.commit()

                calls: list[str] = []
                saved: list[tuple[str, str, list[dict] | None]] = []
                captured_history: list[dict[str, str]] = []
                original_load_history = runtime.load_history
                original_save_message = runtime.save_message

                async def wrapped_load_history(_async_session, current_session_id, *, limit):
                    calls.append("load_history")
                    assert current_session_id == session_id
                    assert limit == 7
                    return await original_load_history(_async_session, current_session_id, limit=limit)

                async def fake_ensure_local_memory_context(_async_session, profile, current_session_id):
                    calls.append("ensure_local_memory_context")
                    assert profile == "siq_assistant"
                    assert current_session_id == session_id
                    return "<local-memory>older turns only</local-memory>"

                async def wrapped_save_message(_async_session, role, content, current_session_id, attachments=None, audit_trace_id=None):
                    calls.append(f"save_{role}")
                    assert current_session_id == session_id
                    saved.append((role, content, attachments))
                    await original_save_message(
                        _async_session,
                        role,
                        content,
                        current_session_id,
                        attachments=attachments,
                        audit_trace_id=audit_trace_id,
                    )

                async def fake_analyze_images_with_primary_model(message, attachments):
                    calls.append("analyze_images_with_primary_model")
                    assert message == "分析这张图"
                    assert attachments == [attachment]
                    return ("image analysis context", True)

                def fake_build_hermes_run_input(
                    message,
                    *,
                    profile,
                    session_id: str,
                    context,
                    allow_initialize,
                    attachments,
                    local_memory_context,
                    image_analysis_context,
                    use_hermes_image_fallback,
                ):
                    calls.append("build_hermes_run_input")
                    assert message == "分析这张图"
                    assert profile == "siq_assistant"
                    assert session_id == "attachment-history-order-session"
                    assert context == {"company": "demo"}
                    assert allow_initialize is False
                    assert attachments == [attachment]
                    assert local_memory_context == "<local-memory>older turns only</local-memory>"
                    assert image_analysis_context == "image analysis context"
                    assert use_hermes_image_fallback is False
                    return "run input"

                async def fake_create_run(run_input, conversation_history, *, profile, session_id):
                    calls.append("create_run")
                    assert run_input == "run input"
                    assert profile == "siq_assistant"
                    assert session_id == "siq:siq_assistant:attachment-history-order-session"
                    captured_history.extend(conversation_history)
                    assert all(item["content"] != "分析这张图" for item in conversation_history)
                    return "run-attachment"

                async def fake_collect_run_result(run_id, *, profile, timeout):
                    calls.append("collect_run_result")
                    assert run_id == "run-attachment"
                    assert profile == "siq_assistant"
                    return "assistant reply"

                async def fake_refresh_session_memory(_async_session, profile, current_session_id):
                    calls.append("refresh_session_memory")
                    assert profile == "siq_assistant"
                    assert current_session_id == session_id

                def fake_remember_completed_run(profile, current_session_id, message_hash, reply):
                    calls.append("remember_completed_run")
                    assert profile == "siq_assistant"
                    assert current_session_id == session_id
                    assert reply == "assistant reply"

                monkeypatch.setattr(runtime, "load_history", wrapped_load_history)
                monkeypatch.setattr(runtime, "ensure_local_memory_context", fake_ensure_local_memory_context)
                monkeypatch.setattr(runtime, "save_message", wrapped_save_message)
                monkeypatch.setattr(runtime, "analyze_images_with_primary_model", fake_analyze_images_with_primary_model)
                monkeypatch.setattr(runtime, "build_hermes_run_input", fake_build_hermes_run_input)
                monkeypatch.setattr(runtime, "create_run", fake_create_run)
                monkeypatch.setattr(runtime, "collect_run_result", fake_collect_run_result)
                monkeypatch.setattr(runtime, "refresh_session_memory", fake_refresh_session_memory)
                monkeypatch.setattr(runtime, "_remember_completed_run", fake_remember_completed_run)
                monkeypatch.setattr(runtime, "_recent_duplicate_reply", lambda *_args, **_kwargs: None)
                monkeypatch.setattr(runtime, "build_wiki_catalog_reply", lambda _message: None)
                monkeypatch.setattr(runtime, "normalize_evidence_trace_for_display", lambda value: value)
                monkeypatch.setattr(runtime, "enforce_financial_evidence_contract", lambda _message, _context, reply: reply)
                monkeypatch.setattr(runtime, "hermes_timeout", lambda: object())

                reply = await runtime.collect_chat_reply(
                    "分析这张图",
                    async_session,
                    session_id=session_id,
                    profile="siq_assistant",
                    context={"company": "demo"},
                    attachments=[attachment],
                    history_limit=7,
                )
                result = await async_session.exec(
                    select(ChatMessage)
                    .where(ChatMessage.session_id == session_id)
                    .order_by(ChatMessage.id)
                )
                persisted = result.all()
                return reply, calls, saved, captured_history, persisted, image_path
        finally:
            await engine.dispose()

    reply, calls, saved, captured_history, persisted, image_path = anyio.run(run_case)

    assert reply == "assistant reply"
    assert calls[:3] == [
        "load_history",
        "ensure_local_memory_context",
        "save_user",
    ]
    assert "analyze_images_with_primary_model" in calls
    assert calls.index("analyze_images_with_primary_model") > calls.index("save_user")
    assert calls.index("build_hermes_run_input") > calls.index("analyze_images_with_primary_model")
    assert "create_run" in calls
    assert calls.index("create_run") > calls.index("save_user")
    assert captured_history == [
        {"role": "user", "content": "旧问题 1"},
        {"role": "assistant", "content": "旧回答 1"},
    ]
    assert [message.role for message in persisted] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    current_user = persisted[-2]
    assert current_user.attachments_json is not None
    current_user_attachments = json.loads(current_user.attachments_json)
    assert current_user_attachments[0]["filename"] == "chart.png"
    assert current_user_attachments[0]["path"] == str(image_path)
    assert saved[0][0] == "user"
    assert saved[0][2] == [expected_attachment | {"size": image_path.stat().st_size, "path": str(image_path)}]
    assert saved[1] == ("assistant", "assistant reply", None)


def test_pdf_attachment_context_uses_independent_mineru_parse_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "CHAT_UPLOAD_ROOT", tmp_path)
    monkeypatch.setattr(runtime, "CHAT_PDF_PARSE_ROOT", tmp_path / "pdf_parses")
    pdf_path = tmp_path / "report.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nfake")
    parse_dir = tmp_path / "pdf_parses" / "att-1"
    parse_dir.mkdir(parents=True)
    markdown_path = parse_dir / "result.md"
    markdown_path.write_text("MinerU markdown from chat attachment", encoding="utf-8")
    content_list_path = parse_dir / "content_list.json"
    content_list_path.write_text("[]", encoding="utf-8")
    (parse_dir / "metadata.json").write_text(
        """
{
  "mineru_task_id": "mineru-chat-1",
  "mineru_parse_status": "completed",
  "parse_dir": "%s",
  "markdown_path": "%s",
  "content_list_path": "%s"
}
""".strip()
        % (parse_dir, markdown_path, content_list_path),
        encoding="utf-8",
    )

    context = runtime._document_attachment_context(
        [
            {
                "id": "doc-1",
                "filename": "report.pdf",
                "content_type": "application/pdf",
                "kind": "document",
                "size": pdf_path.stat().st_size,
                "path": str(pdf_path),
                "metadata": {"parse_dir": str(parse_dir), "mineru_task_id": "mineru-chat-1"},
            }
        ]
    )

    assert "MinerU 直连解析任务: mineru-chat-1" in context
    assert "没有进入财报解析前端队列" in context
    assert str(parse_dir) in context
    assert str(markdown_path) in context
    assert "MinerU markdown from chat attachment" in context


def test_pdf_attachment_context_preserves_no_frontend_queue_policy(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "CHAT_UPLOAD_ROOT", tmp_path)
    monkeypatch.setattr(runtime, "CHAT_PDF_PARSE_ROOT", tmp_path / "pdf_parses")
    pdf_path = tmp_path / "report.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nfake")
    parse_dir = tmp_path / "pdf_parses" / "att-queue"
    parse_dir.mkdir(parents=True)
    (parse_dir / "metadata.json").write_text(
        """
{
  "mineru_submit_status": "submitted",
  "mineru_parse_status": "pending",
  "queue_policy": "direct_mineru_no_pdf2md_frontend_queue",
  "submitted_to_project_queue": false,
  "parse_dir": "%s"
}
""".strip()
        % parse_dir,
        encoding="utf-8",
    )

    context = runtime._document_attachment_context(
        [
            {
                "id": "doc-queue",
                "filename": "report.pdf",
                "content_type": "application/pdf",
                "kind": "document",
                "size": pdf_path.stat().st_size,
                "path": str(pdf_path),
                "metadata": {"parse_dir": str(parse_dir)},
            }
        ]
    )

    assert "独立解析目录" in context
    assert "MinerU 提交状态: submitted" in context
    assert "财报解析前端队列" in context
    assert "不会写入任何公司 Wiki/入库解析产物目录" in context


def test_parse_only_pdf2md_context_is_injected_when_wiki_company_missing(tmp_path, monkeypatch):
    results_root = tmp_path / "results"
    task_id = "12345678-1234-4234-9234-123456789abc"
    result_dir = results_root / task_id
    result_dir.mkdir(parents=True)
    (result_dir / "result.md").write_text("[PDF_PAGE: 1]\n# 只在解析目录里的公司\n", encoding="utf-8")
    (result_dir / "financial_data.json").write_text(
        """
{
  "task_id": "12345678-1234-4234-9234-123456789abc",
  "filename": "只在解析目录里的公司_CN_654321_2025-12-31_年报"
}
""".strip(),
        encoding="utf-8",
    )

    wiki_root = tmp_path / "wiki"
    (wiki_root / "companies").mkdir(parents=True)
    monkeypatch.setattr(runtime, "PROJECT_WIKI_ROOT", wiki_root)
    monkeypatch.setattr(runtime, "ASSISTANT_WIKI_ROOT", wiki_root)
    monkeypatch.setattr(runtime, "PDF2MD_RESULTS_ROOTS", (results_root,))
    monkeypatch.setattr(runtime, "PDF2MD_OUTPUT_ROOTS", (tmp_path / "output",))
    runtime.SESSION_DEFAULT_CONTEXTS.clear()

    prompt = runtime.build_session_contextual_input(
        "只在解析目录里的公司2025年报主要数据是什么？",
        profile="siq_assistant",
        session_id="parse-only-context-test",
    )

    assert "只匹配到 PDF parser results 解析产物" in prompt
    assert "source_type=pdf2md_parse_result" in prompt
    assert task_id in prompt
    assert str(result_dir / "result.md") in prompt
    assert "不得虚构 Wiki 公司目录" in prompt


def test_wait_for_pdf_attachment_parse_refreshes_completed_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "CHAT_UPLOAD_ROOT", tmp_path)
    monkeypatch.setattr(runtime, "CHAT_PDF_PARSE_ROOT", tmp_path / "pdf_parses")
    pdf_path = tmp_path / "report.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nfake")
    parse_dir = tmp_path / "pdf_parses" / "att-wait"
    parse_dir.mkdir(parents=True)
    metadata_path = parse_dir / "metadata.json"
    metadata_path.write_text(
        """
{
  "mineru_submit_status": "submitted",
  "mineru_parse_status": "pending",
  "parse_dir": "%s"
}
""".strip()
        % parse_dir,
        encoding="utf-8",
    )

    markdown_path = parse_dir / "result.md"
    content_list_path = parse_dir / "content_list.json"

    def complete_parse():
        time.sleep(0.05)
        markdown_path.write_text("MinerU completed markdown after wait", encoding="utf-8")
        content_list_path.write_text("[]", encoding="utf-8")
        metadata_path.write_text(
            """
{
  "mineru_task_id": "mineru-wait-1",
  "mineru_submit_status": "submitted",
  "mineru_parse_status": "completed",
  "parse_dir": "%s",
  "markdown_path": "%s",
  "content_list_path": "%s"
}
""".strip()
            % (parse_dir, markdown_path, content_list_path),
            encoding="utf-8",
        )

    thread = threading.Thread(target=complete_parse)
    thread.start()

    async def wait_for_parse():
        return await runtime.wait_for_pdf_attachment_parses(
            [
                {
                    "id": "doc-wait",
                    "filename": "report.pdf",
                    "content_type": "application/pdf",
                    "kind": "document",
                    "size": pdf_path.stat().st_size,
                    "path": str(pdf_path),
                    "metadata": {"parse_dir": str(parse_dir)},
                }
            ],
            timeout_seconds=2,
            poll_seconds=1,
        )

    try:
        statuses = anyio.run(wait_for_parse)
    finally:
        thread.join(timeout=1)

    assert statuses[0]["mineru_parse_status"] == "completed"

    context = runtime._document_attachment_context(
        [
            {
                "id": "doc-wait",
                "filename": "report.pdf",
                "content_type": "application/pdf",
                "kind": "document",
                "size": pdf_path.stat().st_size,
                "path": str(pdf_path),
                "metadata": {"parse_dir": str(parse_dir)},
            }
        ]
    )

    assert "MinerU completed markdown after wait" in context
    assert str(markdown_path) in context


def test_pdf_parse_is_terminal_accepts_success_failure_and_queue_terminal_statuses():
    terminal_cases = [
        {"document_parser_status": "completed"},
        {"document_parser_status": "completed_with_warnings"},
        {"mineru_parse_status": "completed_without_markdown"},
        {"mineru_parse_status": "failed"},
        {"mineru_parse_status": "error"},
        {"mineru_parse_status": "failure"},
        {"mineru_parse_status": "cancelled"},
        {"mineru_parse_status": "timeout"},
        {"mineru_submit_status": "completed_result_fetch_failed"},
        {"mineru_submit_status": "status_failed"},
        {"mineru_submit_status": "poll_failed"},
    ]

    for metadata in terminal_cases:
        assert runtime._pdf_parse_is_terminal(metadata), metadata


def test_pdf_parse_is_terminal_keeps_pending_latest_queue_statuses_nonterminal():
    nonterminal_cases = [
        {},
        {"document_parser_status": "pending"},
        {"mineru_parse_status": "running"},
        {"mineru_submit_status": "submitted"},
        {"document_parser_status": "queued", "mineru_parse_status": "completed"},
        {"document_parser_status": " ", "mineru_parse_status": "pending"},
    ]

    for metadata in nonterminal_cases:
        assert not runtime._pdf_parse_is_terminal(metadata), metadata
