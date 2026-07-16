"""Attachment owner for the Hermes agent runtime."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import subprocess
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import httpx
from models import ChatMessage
from sqlalchemy import text as sql_text
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from services import agent_runtime_context, agent_runtime_display
from services.path_config import BACKEND_DATA_ROOT


def _env_int(name: str, default: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
    raw = os.getenv(name)
    try:
        value = int(str(raw)) if raw not in (None, "") else int(default)
    except ValueError:
        value = int(default)
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _env_int_any(names: tuple[str, ...], default: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
    for name in names:
        raw = os.getenv(name)
        if raw not in (None, ""):
            return _env_int(name, default, minimum=minimum, maximum=maximum)
    return _env_int(names[0], default, minimum=minimum, maximum=maximum)


def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() not in {"0", "false", "no", "off"}


def _env_bool_any(names: tuple[str, ...], default: bool = True) -> bool:
    for name in names:
        raw = os.getenv(name)
        if raw is not None:
            return _env_bool(name, default)
    return default


CHAT_UPLOAD_ROOT = BACKEND_DATA_ROOT / "chat_uploads"
CHAT_PDF_PARSE_ROOT = CHAT_UPLOAD_ROOT / "pdf_parses"
CHAT_PDF_ATTACHMENT_WAIT_TIMEOUT_SECONDS = _env_int(
    "SIQ_CHAT_PDF_ATTACHMENT_WAIT_TIMEOUT_SECONDS",
    150,
    minimum=0,
    maximum=600,
)
CHAT_PDF_ATTACHMENT_WAIT_POLL_SECONDS = _env_int(
    "SIQ_CHAT_PDF_ATTACHMENT_WAIT_POLL_SECONDS",
    3,
    minimum=1,
    maximum=30,
)
_CHAT_MESSAGE_ATTACHMENTS_COLUMN_READY = False
_CHAT_MESSAGE_ATTACHMENTS_COLUMN_LOCK: asyncio.Lock | None = None
_CHAT_MESSAGE_ATTACHMENTS_COLUMN_LOCK_LOOP: asyncio.AbstractEventLoop | None = None
ATTACHMENT_FOLLOWUP_RE = re.compile(
    r"(继续|前面|刚才|上[一个轮条张份次]|这张|那张|这份|那份|图片|照片|附件|手写|ocr|OCR)",
    re.IGNORECASE,
)
IMAGE_MODEL_BASE_URL = (
    os.getenv("SIQ_IMAGE_MODEL_BASE_URL")
    or os.getenv("SIQ_IMAGE_MODEL_URL")
    or "http://127.0.0.1:8007/v1"
).rstrip("/")
IMAGE_MODEL_NAME = (os.getenv("SIQ_IMAGE_MODEL") or "").strip()
IMAGE_MODEL_ENABLED = _env_bool_any(("SIQ_IMAGE_MODEL_ENABLED",), True)
IMAGE_MODEL_TIMEOUT_SECONDS = _env_int_any(("SIQ_IMAGE_MODEL_TIMEOUT_SECONDS",), 90, minimum=5, maximum=600)
MAX_DOCUMENT_CONTEXT_CHARS = 16000
_IMAGE_MODEL_NAME_CACHE: str | None = None


def _attachment_dicts(attachments: Any | None) -> list[dict[str, Any]]:
    return agent_runtime_context.attachment_dicts(attachments)


def _image_attachment_dicts(attachments: Any | None) -> list[dict[str, Any]]:
    return agent_runtime_context.image_attachment_dicts(attachments)


def _document_attachment_dicts(attachments: Any | None) -> list[dict[str, Any]]:
    return agent_runtime_context.document_attachment_dicts(attachments)


def _should_reuse_recent_attachments(message: str) -> bool:
    return agent_runtime_context.should_reuse_recent_attachments(message, ATTACHMENT_FOLLOWUP_RE)


def _safe_chat_path(raw_path: str, *, must_be_file: bool = True) -> Path | None:
    if not raw_path:
        return None
    try:
        resolved = Path(raw_path).resolve()
        root = CHAT_UPLOAD_ROOT.resolve()
        if root not in resolved.parents:
            return None
        if must_be_file and not resolved.is_file():
            return None
        return resolved
    except Exception:
        return None


def _safe_uploaded_path(item: dict[str, Any]) -> Path | None:
    return _safe_chat_path(str(item.get("path") or ""))


def _attachment_reference_context(attachments: Any | None) -> str:
    items: list[str] = []
    for index, item in enumerate(_attachment_dicts(attachments), start=1):
        kind = str(item.get("kind") or "image").strip().lower()
        if kind == "audio":
            continue
        path = _safe_uploaded_path(item)
        if path is None:
            continue
        label = "图片" if kind == "image" else "文档"
        filename = str(item.get("filename") or path.name or f"attachment-{index}").strip()
        content_type = str(item.get("content_type") or "application/octet-stream").strip()
        lines = [
            f"- {label}附件 {index}: {filename}",
            f"  - 本地路径: {path}",
            f"  - 类型: {content_type}",
            f"  - 大小: {item.get('size', 0)} bytes",
        ]
        url = str(item.get("url") or "").strip()
        if url:
            lines.append(f"  - 前端链接: {url}")
        items.append("\n".join(lines))
    if not items:
        return ""
    return (
        "历史附件上下文：以下附件已由 SIQ 后端保存。继续/重试时请使用本地路径读取；"
        "`/api/chat/attachments/...` 是前端后端路由，不是 Hermes 8642 网关接口。\n"
        + "\n".join(items)
    )


def _image_attachment_data_url(item: dict[str, Any]) -> str | None:
    path = Path(str(item.get("path") or ""))
    try:
        resolved = path.resolve()
        root = CHAT_UPLOAD_ROOT.resolve()
        if root not in resolved.parents:
            return None
        raw = resolved.read_bytes()
    except Exception:
        return None
    if not raw:
        return None
    content_type = str(item.get("content_type") or "image/png").strip() or "image/png"
    if not content_type.startswith("image/"):
        content_type = "image/png"
    return f"data:{content_type};base64,{base64.b64encode(raw).decode('ascii')}"


async def _resolve_image_model_name() -> str | None:
    global _IMAGE_MODEL_NAME_CACHE
    if IMAGE_MODEL_NAME:
        return IMAGE_MODEL_NAME
    if _IMAGE_MODEL_NAME_CACHE:
        return _IMAGE_MODEL_NAME_CACHE
    if not IMAGE_MODEL_ENABLED or not IMAGE_MODEL_BASE_URL:
        return None
    try:
        timeout = httpx.Timeout(10.0, connect=3.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(f"{IMAGE_MODEL_BASE_URL}/models")
        if not response.is_success:
            return None
        payload = response.json() if response.content else {}
        data = payload.get("data")
        if not isinstance(data, list):
            return None
        for item in data:
            if isinstance(item, dict) and item.get("id"):
                _IMAGE_MODEL_NAME_CACHE = str(item["id"])
                return _IMAGE_MODEL_NAME_CACHE
    except Exception:
        return None
    return None


def _extract_openai_message_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices") if isinstance(payload, dict) else None
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0] if isinstance(choices[0], dict) else {}
    message = first.get("message") if isinstance(first.get("message"), dict) else {}
    content = message.get("content") or first.get("text") or ""
    if isinstance(content, list):
        pieces: list[str] = []
        for part in content:
            if isinstance(part, dict):
                value = part.get("text") or part.get("content")
                if value:
                    pieces.append(str(value))
        return "\n".join(pieces).strip()
    return str(content or "").strip()


async def _analyze_single_image_with_primary_model(
    client: httpx.AsyncClient,
    *,
    model: str,
    message: str,
    item: dict[str, Any],
    index: int,
    total: int,
) -> str:
    data_url = _image_attachment_data_url(item)
    if not data_url:
        raise RuntimeError("image file is unavailable")
    filename = str(item.get("filename") or Path(str(item.get("path") or "")).name or f"image-{index}")
    prompt = (
        "用户在聊天对话框上传了一张图片。请用中文客观分析这张图片，优先提取可见文字、数字、表格、图表结构、"
        "关键对象和可能影响财务/合规判断的信息；无法确定的内容明确说明不确定。"
        f"\n\n图片: {filename} ({index}/{total})"
        f"\n用户问题: {(message or '').strip() or '请分析图片内容'}"
    )
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
        "temperature": 0.1,
        "max_tokens": 1200,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    response = await client.post(f"{IMAGE_MODEL_BASE_URL}/chat/completions", json=payload)
    if not response.is_success:
        raise RuntimeError(f"image model HTTP {response.status_code}: {response.text[:300]}")
    text = _extract_openai_message_text(response.json() if response.content else {})
    if not text:
        raise RuntimeError("image model returned empty content")
    return f"### 图片 {index}: {filename}\n{text}"


async def analyze_images_with_primary_model(
    message: str,
    attachments: Any | None,
) -> tuple[str, bool]:
    images = _image_attachment_dicts(attachments)
    if not images or not IMAGE_MODEL_ENABLED:
        return "", False
    model = await _resolve_image_model_name()
    if not model:
        return "", False
    blocks: list[str] = []
    try:
        timeout = httpx.Timeout(IMAGE_MODEL_TIMEOUT_SECONDS, connect=10.0, read=IMAGE_MODEL_TIMEOUT_SECONDS)
        async with httpx.AsyncClient(timeout=timeout) as client:
            for index, item in enumerate(images, start=1):
                blocks.append(
                    await _analyze_single_image_with_primary_model(
                        client,
                        model=model,
                        message=message,
                        item=item,
                        index=index,
                        total=len(images),
                    )
                )
    except Exception as exc:
        print(f"[chat-attachments] primary image model unavailable, falling back to Hermes: {exc}")
        return "", False
    return (
        "图片已优先由本机多模态模型处理。下面是模型初步分析，回答时应结合用户问题使用；"
        "如需复核细节，可继续读取图片本地路径。\n\n" + "\n\n".join(blocks),
        True,
    )


def _attachment_metadata(item: dict[str, Any]) -> dict[str, Any]:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    merged = dict(metadata or {})
    parse_dir = _safe_chat_path(str(merged.get("parse_dir") or ""), must_be_file=False)
    if parse_dir is None:
        return merged
    metadata_path = parse_dir / "metadata.json"
    if metadata_path.is_file():
        try:
            parsed = json.loads(metadata_path.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                merged.update(parsed)
        except Exception:
            pass
    merged["parse_dir"] = str(parse_dir)
    merged["metadata_path"] = str(metadata_path)
    return merged


def _pdf_attachment_parse_dirs(attachments: Any | None) -> list[Path]:
    parse_dirs: list[Path] = []
    seen: set[Path] = set()
    for item in _document_attachment_dicts(attachments):
        content_type = str(item.get("content_type") or "").lower()
        path = Path(str(item.get("path") or ""))
        if content_type != "application/pdf" and path.suffix.lower() != ".pdf":
            continue
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        parse_dir = _safe_chat_path(str(metadata.get("parse_dir") or ""), must_be_file=False)
        if parse_dir and parse_dir not in seen:
            seen.add(parse_dir)
            parse_dirs.append(parse_dir)
    return parse_dirs


def _pdf_parse_is_terminal(metadata: dict[str, Any]) -> bool:
    status = str(
        metadata.get("document_parser_status")
        or metadata.get("mineru_parse_status")
        or metadata.get("mineru_submit_status")
        or ""
    ).lower()
    if status in {"completed_with_warnings"}:
        return True
    if status in {"completed", "completed_without_markdown", "failed", "error", "failure", "cancelled", "timeout"}:
        return True
    if status in {"completed_result_fetch_failed", "status_failed", "poll_failed"}:
        return True
    return False


async def wait_for_pdf_attachment_parses(
    attachments: Any | None,
    *,
    timeout_seconds: int = CHAT_PDF_ATTACHMENT_WAIT_TIMEOUT_SECONDS,
    poll_seconds: int = CHAT_PDF_ATTACHMENT_WAIT_POLL_SECONDS,
) -> list[dict[str, Any]]:
    parse_dirs = _pdf_attachment_parse_dirs(attachments)
    if not parse_dirs or timeout_seconds <= 0:
        return []

    deadline = asyncio.get_running_loop().time() + timeout_seconds
    last_statuses: list[dict[str, Any]] = []
    while True:
        pending = False
        statuses: list[dict[str, Any]] = []
        for parse_dir in parse_dirs:
            metadata_path = parse_dir / "metadata.json"
            metadata: dict[str, Any] = {"parse_dir": str(parse_dir), "metadata_path": str(metadata_path)}
            if metadata_path.is_file():
                try:
                    parsed = json.loads(metadata_path.read_text(encoding="utf-8"))
                    if isinstance(parsed, dict):
                        metadata.update(parsed)
                except Exception as exc:
                    metadata["mineru_parse_status"] = "metadata_read_failed"
                    metadata["mineru_parse_error"] = str(exc)
            else:
                metadata["mineru_parse_status"] = "metadata_missing"
            if not _pdf_parse_is_terminal(metadata):
                pending = True
            statuses.append(metadata)
        last_statuses = statuses
        if not pending or asyncio.get_running_loop().time() >= deadline:
            return last_statuses
        await asyncio.sleep(max(1, poll_seconds))


def _attachments_with_fresh_metadata(attachments: Any | None) -> list[dict[str, Any]]:
    refreshed: list[dict[str, Any]] = []
    for item in _attachment_dicts(attachments):
        updated = dict(item)
        metadata = _attachment_metadata(updated)
        if metadata:
            updated["metadata"] = metadata
        refreshed.append(updated)
    return refreshed


def _chatmessage_attachments_column_lock() -> asyncio.Lock:
    global _CHAT_MESSAGE_ATTACHMENTS_COLUMN_LOCK, _CHAT_MESSAGE_ATTACHMENTS_COLUMN_LOCK_LOOP
    loop = asyncio.get_running_loop()
    if (
        _CHAT_MESSAGE_ATTACHMENTS_COLUMN_LOCK is None
        or (
            _CHAT_MESSAGE_ATTACHMENTS_COLUMN_LOCK_LOOP is not loop
            and not _CHAT_MESSAGE_ATTACHMENTS_COLUMN_LOCK.locked()
        )
    ):
        _CHAT_MESSAGE_ATTACHMENTS_COLUMN_LOCK = asyncio.Lock()
        _CHAT_MESSAGE_ATTACHMENTS_COLUMN_LOCK_LOOP = loop
    return _CHAT_MESSAGE_ATTACHMENTS_COLUMN_LOCK


async def _chatmessage_columns(async_session: AsyncSession, dialect: str) -> set[str]:
    if dialect == "sqlite":
        result = await async_session.exec(sql_text("PRAGMA table_info(chatmessage)"))
        return {str(row[1]) for row in result.all()}
    result = await async_session.exec(
        sql_text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = 'chatmessage'
              AND column_name IN ('attachments_json', 'audit_trace_id', 'research_identity_json')
            """
        )
    )
    return {str(row[0]) for row in result.all()}


async def _ensure_chatmessage_attachments_column(async_session: AsyncSession) -> None:
    global _CHAT_MESSAGE_ATTACHMENTS_COLUMN_READY
    if _CHAT_MESSAGE_ATTACHMENTS_COLUMN_READY:
        return
    async with _chatmessage_attachments_column_lock():
        if _CHAT_MESSAGE_ATTACHMENTS_COLUMN_READY:
            return
        try:
            bind = async_session.get_bind()
            dialect = bind.dialect.name if bind is not None else ""
            columns = await _chatmessage_columns(async_session, dialect)
            definitions = (
                ("attachments_json", "TEXT"),
                ("audit_trace_id", "VARCHAR(64)"),
                ("research_identity_json", "TEXT"),
            )
            missing = [(name, definition) for name, definition in definitions if name not in columns]
            for name, definition in missing:
                if dialect == "sqlite":
                    statement = f"ALTER TABLE chatmessage ADD COLUMN {name} {definition}"
                else:
                    statement = f"ALTER TABLE chatmessage ADD COLUMN IF NOT EXISTS {name} {definition}"
                await async_session.exec(sql_text(statement))
            if missing:
                await async_session.commit()
            _CHAT_MESSAGE_ATTACHMENTS_COLUMN_READY = True
        except BaseException:
            _CHAT_MESSAGE_ATTACHMENTS_COLUMN_READY = False
            await async_session.rollback()
            raise


def _read_text_file(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8", "utf-8-sig", "gb18030", "latin-1"):
        try:
            return raw.decode(encoding, errors="replace")
        except Exception:
            continue
    return raw.decode("utf-8", errors="replace")


def _read_docx_text(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            xml = archive.read("word/document.xml")
        root = ElementTree.fromstring(xml)
        ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        paragraphs: list[str] = []
        for paragraph in root.iter(f"{ns}p"):
            pieces = [node.text or "" for node in paragraph.iter(f"{ns}t")]
            text = "".join(pieces).strip()
            if text:
                paragraphs.append(text)
        return "\n".join(paragraphs)
    except Exception as exc:
        return f"[DOCX 文本抽取失败: {exc}]"


def _read_pdf_text_with_pdftotext(path: Path) -> str:
    try:
        result = subprocess.run(
            ["pdftotext", "-layout", "-f", "1", "-l", "8", str(path), "-"],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except FileNotFoundError:
        return "[PDF 快速文本抽取跳过: 系统未安装 pdftotext；请使用 MinerU 解析任务结果。]"
    except Exception as exc:
        return f"[PDF 快速文本抽取失败: {exc}]"
    text = (result.stdout or "").strip()
    if text:
        return text
    err = (result.stderr or "").strip()
    return f"[PDF 快速文本抽取无可用文本{f': {err}' if err else ''}；请使用 MinerU 解析任务结果。]"


def _document_text_preview(item: dict[str, Any]) -> str:
    path = _safe_uploaded_path(item)
    if path is None:
        return "[文档文件不可读取或路径不在允许目录内]"
    content_type = str(item.get("content_type") or "").lower()
    suffix = path.suffix.lower()
    if content_type == "application/pdf" or suffix == ".pdf":
        metadata = _attachment_metadata(item)
        markdown_path = _safe_chat_path(str(metadata.get("markdown_path") or ""))
        if markdown_path and markdown_path.is_file():
            return _read_text_file(markdown_path)
        return _read_pdf_text_with_pdftotext(path)
    if suffix == ".docx" or content_type.endswith("wordprocessingml.document"):
        return _read_docx_text(path)
    if suffix == ".doc":
        return "[旧版 .doc 已保存，但当前环境未配置稳定的 .doc 文本抽取器；如需精读，请先转换为 .docx/PDF/Markdown。]"
    return _read_text_file(path)


def _truncate_document_text(text: str, limit: int = MAX_DOCUMENT_CONTEXT_CHARS) -> str:
    cleaned = re.sub(r"\n{4,}", "\n\n\n", text or "").strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit].rstrip() + f"\n\n[文档预览已截断，仅展示前 {limit} 字符。请用文件路径或 MinerU 任务结果继续精读。]"


def _document_attachment_context(attachments: Any | None) -> str:
    docs = _document_attachment_dicts(attachments)
    if not docs:
        return ""
    blocks = [
        "用户本轮上传了以下文档附件。请优先基于附件内容回答；需要全文、表格或版面证据时，使用给出的本地路径或 MinerU/PDF 解析任务信息继续读取。"
    ]
    for index, item in enumerate(docs, start=1):
        filename = str(item.get("filename") or Path(str(item.get("path") or "")).name or f"document-{index}")
        path = str(item.get("path") or "")
        content_type = str(item.get("content_type") or "application/octet-stream")
        metadata = _attachment_metadata(item)
        lines = [
            f"### 文档附件 {index}: {filename}",
            f"- 本地路径: {path}",
            f"- 类型: {content_type}",
            f"- 大小: {item.get('size', 0)} bytes",
        ]
        task_id = metadata.get("mineru_task_id") if metadata else None
        if task_id:
            lines.extend(
                [
                    f"- 解析任务: {task_id}",
                    f"- MinerU 直连解析任务: {task_id}",
                    f"- 通用文档解析任务: {metadata.get('document_parser_task_id') or task_id}",
                    f"- 状态接口: {metadata.get('document_parser_status_url') or metadata.get('mineru_status_url')}",
                    f"- 结果接口: {metadata.get('document_parser_result_url') or metadata.get('mineru_result_url')}",
                    f"- 工作台页面: {metadata.get('document_parser_page_url') or ''}",
                    f"- 独立解析目录: {metadata.get('parse_dir')}",
                    f"- 元数据文件: {metadata.get('metadata_path')}",
                    f"- 当前解析状态: {metadata.get('document_parser_status') or metadata.get('mineru_parse_status') or metadata.get('mineru_submit_status')}",
                    "- 该 PDF 走通用 document-parser，不进入财报解析前端队列。",
                    "- 如用户询问 PDF 版面、表格或长文档细节，应优先读取独立解析目录中的 result.md，或使用通用文档解析 source map / blocks / tables 产物。",
                ]
            )
            if metadata.get("document_parser_source_map_url"):
                lines.append(f"- Source map: {metadata.get('document_parser_source_map_url')}")
            if metadata.get("document_parser_blocks_url"):
                lines.append(f"- Blocks: {metadata.get('document_parser_blocks_url')}")
            if metadata.get("document_parser_tables_url"):
                lines.append(f"- Tables: {metadata.get('document_parser_tables_url')}")
            if metadata.get("document_parser_source_page_url_template"):
                lines.append(f"- 页来源模板: {metadata.get('document_parser_source_page_url_template')}")
            if metadata.get("document_parser_source_block_url_template"):
                lines.append(f"- 块来源模板: {metadata.get('document_parser_source_block_url_template')}")
            if metadata.get("document_parser_source_table_url_template"):
                lines.append(f"- 表格来源模板: {metadata.get('document_parser_source_table_url_template')}")
            lines.append("- 引用来源如需给出可点击链接，优先使用 `/api/documents/source/<task_id>/page/<page_number>`、`/api/documents/source/<task_id>/block/<block_id>` 或 `/api/documents/source/<task_id>/table/<table_id>`。")
            if not metadata.get("document_parser_task_id"):
                lines.append("- 该 PDF 没有进入财报解析前端队列，也不会写入任何公司 Wiki/入库解析产物目录。")
            if metadata.get("markdown_path"):
                lines.append(f"- Markdown: {metadata.get('markdown_path')}")
            if metadata.get("content_list_path"):
                lines.append(f"- content_list: {metadata.get('content_list_path')}")
        elif metadata:
            if metadata.get("parse_dir"):
                lines.append(f"- 独立解析目录: {metadata.get('parse_dir')}")
            if metadata.get("document_parser_submit_status"):
                lines.append(f"- 文档解析提交状态: {metadata.get('document_parser_submit_status')}")
            else:
                lines.append(f"- MinerU 提交状态: {metadata.get('mineru_submit_status')}")
            if metadata.get("document_parser_status"):
                lines.append(f"- 文档解析状态: {metadata.get('document_parser_status')}")
            if metadata.get("mineru_parse_status"):
                lines.append(f"- MinerU 解析状态: {metadata.get('mineru_parse_status')}")
            if (
                metadata.get("queue_policy") == "direct_mineru_no_pdf2md_frontend_queue"
                or metadata.get("queue_policy") == "document_parser_chat_attachment"
                or metadata.get("submitted_to_project_queue") is False
            ):
                if metadata.get("queue_policy") == "direct_mineru_no_pdf2md_frontend_queue":
                    lines.append("- 该 PDF 没有进入财报解析前端队列，也不会写入任何公司 Wiki/入库解析产物目录。")
                else:
                    lines.append("- 该 PDF 没有进入财报解析前端队列。")
            if metadata.get("document_parser_submit_error"):
                lines.append(f"- 文档解析提交错误: {metadata.get('document_parser_submit_error')}")
            if metadata.get("document_parser_error"):
                lines.append(f"- 文档解析错误: {metadata.get('document_parser_error')}")
            if metadata.get("mineru_submit_error"):
                lines.append(f"- MinerU 提交错误: {metadata.get('mineru_submit_error')}")
            if metadata.get("mineru_parse_error"):
                lines.append(f"- MinerU 解析错误: {metadata.get('mineru_parse_error')}")
        preview = _truncate_document_text(_document_text_preview(item))
        if preview:
            lines.extend(["", "```text", preview, "```"])
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _display_message_with_attachments(message: str, attachments: Any | None) -> str:
    return agent_runtime_display._display_message_with_attachments(message, _attachment_dicts(attachments))


def _message_attachments(message: ChatMessage) -> list[dict[str, Any]]:
    attachments: list[dict[str, Any]] = []
    if getattr(message, "attachments_json", None):
        try:
            parsed = json.loads(message.attachments_json or "[]")
            if isinstance(parsed, list):
                attachments = [
                    item for item in parsed
                    if isinstance(item, dict) and str(item.get("path") or "").strip()
                ]
        except Exception:
            attachments = []
    return attachments


def chat_message_has_visible_payload(message: ChatMessage) -> bool:
    if str(message.content or "").strip():
        return True
    return bool(_message_attachments(message))


async def load_recent_session_attachments(
    async_session: AsyncSession,
    session_id: str,
    *,
    limit: int = 8,
) -> list[dict[str, Any]]:
    result = await async_session.exec(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.id.desc())
        .limit(max(1, limit))
    )
    for message in result.all():
        attachments = [
            item
            for item in _attachments_with_fresh_metadata(_message_attachments(message))
            if str(item.get("kind") or "image").strip().lower() in {"image", "document"}
        ]
        if attachments:
            return attachments
    return []


__all__ = [
    "ATTACHMENT_FOLLOWUP_RE",
    "CHAT_PDF_ATTACHMENT_WAIT_POLL_SECONDS",
    "CHAT_PDF_ATTACHMENT_WAIT_TIMEOUT_SECONDS",
    "CHAT_PDF_PARSE_ROOT",
    "CHAT_UPLOAD_ROOT",
    "IMAGE_MODEL_BASE_URL",
    "IMAGE_MODEL_ENABLED",
    "IMAGE_MODEL_NAME",
    "IMAGE_MODEL_TIMEOUT_SECONDS",
    "MAX_DOCUMENT_CONTEXT_CHARS",
    "_attachment_dicts",
    "_attachment_metadata",
    "_attachment_reference_context",
    "_attachments_with_fresh_metadata",
    "_display_message_with_attachments",
    "_document_attachment_context",
    "_document_attachment_dicts",
    "_document_text_preview",
    "_ensure_chatmessage_attachments_column",
    "_extract_openai_message_text",
    "_image_attachment_data_url",
    "_image_attachment_dicts",
    "_message_attachments",
    "_pdf_attachment_parse_dirs",
    "_pdf_parse_is_terminal",
    "_read_docx_text",
    "_read_pdf_text_with_pdftotext",
    "_resolve_image_model_name",
    "_safe_chat_path",
    "_safe_uploaded_path",
    "_should_reuse_recent_attachments",
    "_truncate_document_text",
    "analyze_images_with_primary_model",
    "chat_message_has_visible_payload",
    "load_recent_session_attachments",
    "wait_for_pdf_attachment_parses",
]
