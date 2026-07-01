import sys
import threading
import time
import json
from pathlib import Path

import anyio

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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


def test_attachment_followup_reuses_recent_attachment_intent():
    assert runtime._should_reuse_recent_attachments("继续前面的问题")
    assert runtime._should_reuse_recent_attachments("提取刚才那张照片里的手写体")
    assert not runtime._should_reuse_recent_attachments("上海建工的营收是多少？")


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
