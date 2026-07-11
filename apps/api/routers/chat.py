import json
import asyncio
import uuid
import base64
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncGenerator
from urllib.parse import quote

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlmodel import select, func
from sqlmodel.ext.asyncio.session import AsyncSession
from sse_starlette.sse import EventSourceResponse

from database import async_engine, get_async_session
from models import Achievement, AgentState, ChatMessage, InteractionLog
from services.auth_service import User
from schemas import (
    AnswerAuditTraceResponse,
    ChatAttachment,
    ChatAttachmentUploadRequest,
    ChatAttachmentUploadResponse,
    ChatRequest,
    ChatResponse,
    AchievementResponse,
)
from services.agent_chat_runtime import (
    HISTORY_LIMIT,
    chat_message_has_visible_payload,
    chat_history_response,
    collect_chat_reply,
    get_active_run_snapshot,
    has_active_run,
    save_message,
    stop_active_run,
    stream_active_run_events,
    stream_chat_reply,
)
from services.agent_runtime_answer_audit import get_answer_audit_trace, is_answer_audit_trace_id
from services.hermes_model_control import maybe_handle_model_control
from services.session_manager import get_session_manager, keeps_sessions_forever
from services.auth_dependencies import get_current_user
from services.path_config import BACKEND_DATA_ROOT
from routers.agent import apply_decay, perform_action
from routers.document_parser import DOCUMENT_PARSER_API_BASE, _document_headers
from routers.workspace import _quota_error_payload
from services.usage_service import (
    AGENT_QUESTION_EVENT,
    DOCUMENT_PARSE_EVENT,
    UserArtifact,
    ensure_within_quota_async,
    record_usage_async,
)

router = APIRouter(tags=["chat"])

# 全局会话变量已移除 - 现在使用用户级会话管理
# 每个用户有独立的会话ID: user-{user_id}-assistant-{uuid}
CHAT_UPLOAD_DIR = BACKEND_DATA_ROOT / "chat_uploads"
CHAT_PDF_PARSE_DIR = CHAT_UPLOAD_DIR / "pdf_parses"
MINERU_API_BASE = os.environ.get("MINERU_API_URL", "http://127.0.0.1:8003").rstrip("/")
VLM_API_BASE = os.environ.get("VLM_API_URL", "http://127.0.0.1:8002").rstrip("/")
CHAT_PDF_PARSE_SUBMIT_TIMEOUT = float(os.environ.get("CHAT_PDF_PARSE_SUBMIT_TIMEOUT", "60"))
CHAT_PDF_PARSE_STATUS_TIMEOUT = float(os.environ.get("CHAT_PDF_PARSE_STATUS_TIMEOUT", "30"))
CHAT_PDF_PARSE_POLL_INTERVAL = float(os.environ.get("CHAT_PDF_PARSE_POLL_INTERVAL", "5"))
CHAT_PDF_PARSE_MAX_POLLS = int(os.environ.get("CHAT_PDF_PARSE_MAX_POLLS", "360"))
CHAT_DOCUMENT_PARSE_SUBMIT_TIMEOUT = float(os.environ.get("CHAT_DOCUMENT_PARSE_SUBMIT_TIMEOUT", "60"))
CHAT_DOCUMENT_PARSE_STATUS_TIMEOUT = float(os.environ.get("CHAT_DOCUMENT_PARSE_STATUS_TIMEOUT", "30"))
CHAT_DOCUMENT_PARSE_POLL_INTERVAL = float(os.environ.get("CHAT_DOCUMENT_PARSE_POLL_INTERVAL", "3"))
CHAT_DOCUMENT_PARSE_MAX_POLLS = int(os.environ.get("CHAT_DOCUMENT_PARSE_MAX_POLLS", "360"))
MAX_CHAT_IMAGE_COUNT = 4
MAX_CHAT_ATTACHMENT_COUNT = 6
MAX_CHAT_IMAGE_BYTES = 10 * 1024 * 1024
MAX_CHAT_DOCUMENT_BYTES = 25 * 1024 * 1024
IMAGE_CONTENT_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
DOCUMENT_CONTENT_TYPES = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/msword": ".doc",
    "text/markdown": ".md",
    "text/x-markdown": ".md",
    "text/plain": ".txt",
    "text/csv": ".csv",
    "application/json": ".json",
    "application/rtf": ".rtf",
    "text/rtf": ".rtf",
}
DOCUMENT_SUFFIX_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".json": "application/json",
    ".rtf": "application/rtf",
}
DATA_URL_RE = re.compile(r"^data:(?P<mime>[^;,]+)?;base64,(?P<data>.+)$", re.DOTALL)


def get_or_create_session(
    user_id: str,
    profile: str = "assistant",
    session_id: str | None = None,
    user_role: object | None = None,
) -> str:
    """获取或创建用户会话"""
    session_mgr = get_session_manager()

    if session_id:
        _assert_session_belongs_to_user(session_id, user_id, profile)
        return session_mgr.set_current_session(user_id, profile, session_id)

    current_session_id = session_mgr.get_current_session_id(user_id, profile)
    if current_session_id:
        return current_session_id

    return session_mgr.create_session(user_id, profile, user_role=user_role)


def _user_session_prefix(user_id: str, profile: str) -> str:
    return f"user-{user_id}-{profile}-"


def _assert_session_belongs_to_user(session_id: str, user_id: str, profile: str) -> None:
    if not session_id.startswith(_user_session_prefix(user_id, profile)):
        raise HTTPException(status_code=403, detail="Session does not belong to this user")


async def resolve_or_create_session(
    async_session: AsyncSession,
    current_user: User,
    profile: str = "assistant",
    session_id: str | None = None,
) -> str:
    """Resolve a requested chat session, restoring DB-backed history after backend restarts."""
    user_id = str(current_user.id)
    user_role = current_user.role
    session_mgr = get_session_manager()

    if session_id:
        _assert_session_belongs_to_user(session_id, user_id, profile)
        try:
            return session_mgr.set_current_session(user_id, profile, session_id)
        except HTTPException as exc:
            if exc.status_code != 404:
                raise

            summary = await _db_session_summary(async_session, session_id)
            if summary:
                return session_mgr.restore_session(
                    user_id,
                    profile,
                    session_id,
                    user_role=user_role,
                    created_at=summary.get("created_at"),
                    updated_at=summary.get("updated_at"),
                    message_count=int(summary.get("message_count") or 0),
                )

            return session_mgr.create_session(user_id, profile, user_role=user_role)

    current_session_id = session_mgr.get_current_session_id(user_id, profile)
    if current_session_id:
        return current_session_id

    db_sessions = await _db_session_summaries(
        async_session,
        user_id=user_id,
        profile=profile,
        limit=1,
    )
    if db_sessions:
        summary = db_sessions[0]
        restored_session_id = str(summary.get("session_id") or "")
        if restored_session_id:
            return session_mgr.restore_session(
                user_id,
                profile,
                restored_session_id,
                user_role=user_role,
                created_at=summary.get("created_at"),
                updated_at=summary.get("updated_at"),
                message_count=int(summary.get("message_count") or 0),
            )

    return session_mgr.create_session(user_id, profile, user_role=user_role)


def _session_limit(current_user: User, requested: int | None) -> int | None:
    if keeps_sessions_forever(getattr(current_user, "role", None)):
        if requested is None or requested <= 0:
            return None
        return requested
    return min(max(int(requested or 100), 1), 100)


def _iso_value(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


async def _delete_chat_messages(async_session: AsyncSession, session_ids: list[str]) -> None:
    if not session_ids:
        return
    from sqlmodel import delete as sql_delete

    statement = sql_delete(ChatMessage).where(ChatMessage.session_id.in_(session_ids))
    await async_session.exec(statement)
    await async_session.commit()


async def _session_message_summaries(async_session: AsyncSession, session_ids: list[str]) -> dict[str, dict]:
    if not session_ids:
        return {}
    summaries: dict[str, dict] = {}
    for session_id in session_ids:
        if not session_id:
            continue
        latest_result = await async_session.exec(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.id.desc())
            .limit(20)
        )
        latest = next((message for message in latest_result.all() if chat_message_has_visible_payload(message)), None)
        first_user_result = await async_session.exec(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id, ChatMessage.role == "user")
            .order_by(ChatMessage.id)
            .limit(20)
        )
        first_user = next((message for message in first_user_result.all() if chat_message_has_visible_payload(message)), None)
        if latest or first_user:
            summaries[session_id] = {
                "title": (first_user.content[:48] if first_user else ""),
                "preview": (latest.content[:120] if latest else ""),
                "first_message_at": first_user.created_at if first_user else latest.created_at,
                "last_message_at": latest.created_at if latest else first_user.created_at,
            }
    return summaries


async def _db_session_summaries(
    async_session: AsyncSession,
    *,
    user_id: str,
    profile: str,
    limit: int | None,
) -> list[dict]:
    prefix = _user_session_prefix(user_id, profile)
    result = await async_session.exec(
        select(ChatMessage)
        .where(ChatMessage.session_id.startswith(prefix))
        .order_by(ChatMessage.id)
    )
    grouped: dict[str, dict] = {}
    for message in result.all():
        session_id = str(message.session_id or "")
        if not session_id.startswith(prefix):
            continue
        if not chat_message_has_visible_payload(message):
            continue
        item = grouped.setdefault(
            session_id,
            {
                "session_id": session_id,
                "user_id": user_id,
                "profile": profile,
                "message_count": 0,
                "created_at": _iso_value(message.created_at),
                "updated_at": _iso_value(message.created_at),
                "title": "",
                "preview": "",
                "first_message_at": _iso_value(message.created_at),
                "last_message_at": _iso_value(message.created_at),
            },
        )
        item["message_count"] += 1
        item["updated_at"] = _iso_value(message.created_at)
        item["last_message_at"] = _iso_value(message.created_at)
        if message.role == "user" and not item["title"]:
            item["title"] = (message.content or "")[:48]
        item["preview"] = (message.content or "")[:120]
    sessions = sorted(grouped.values(), key=lambda item: _iso_value(item.get("last_message_at") or item.get("updated_at")), reverse=True)
    if limit and limit > 0:
        sessions = sessions[:limit]
    return sessions


async def _db_session_summary(async_session: AsyncSession, session_id: str) -> dict | None:
    result = await async_session.exec(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.id)
    )
    messages = [message for message in result.all() if chat_message_has_visible_payload(message)]
    if not messages:
        return None
    first = messages[0]
    last = messages[-1]
    return {
        "session_id": session_id,
        "created_at": first.created_at.isoformat() if hasattr(first.created_at, "isoformat") else str(first.created_at),
        "updated_at": last.created_at.isoformat() if hasattr(last.created_at, "isoformat") else str(last.created_at),
        "message_count": len(messages),
    }


def _merge_sessions(redis_sessions: list[dict], db_sessions: list[dict], limit: int | None) -> list[dict]:
    merged: dict[str, dict] = {}
    db_session_ids = {str(item.get("session_id") or "") for item in db_sessions if item.get("session_id")}
    for item in db_sessions:
        session_id = str(item.get("session_id") or "")
        if session_id:
            merged[session_id] = dict(item)
    for item in redis_sessions:
        session_id = str(item.get("session_id") or "")
        if session_id:
            merged[session_id] = {**item, **merged.get(session_id, {})}
    visible_sessions = [
        item for item in merged.values()
        if str(item.get("session_id") or "") in db_session_ids
    ]
    sessions = sorted(
        visible_sessions,
        key=lambda item: _iso_value(item.get("last_message_at") or item.get("updated_at") or item.get("created_at")),
        reverse=True,
    )
    if limit and limit > 0:
        sessions = sessions[:limit]
    return sessions


def _sessions_payload(
    sessions: list[dict],
    current_session_id: str | None,
    message_summaries: dict[str, dict],
) -> dict:
    normalized = []
    for item in sessions:
        session_id = str(item.get("session_id") or "")
        summary = message_summaries.get(session_id, {})
        message_count = summary.get("message_count") or item.get("message_count") or 0
        if int(message_count or 0) <= 0:
            continue
        created_at = item.get("created_at")
        normalized.append({
            "session_id": session_id,
            "title": item.get("title") or summary.get("title") or "未命名会话",
            "preview": item.get("preview") or summary.get("preview") or "",
            "message_count": message_count,
            "first_message_at": summary.get("first_message_at") or item.get("first_message_at") or created_at,
            "last_message_at": summary.get("last_message_at") or item.get("last_message_at") or item.get("updated_at") or created_at,
            "current": bool(current_session_id and session_id == current_session_id),
        })
    return {"sessions": normalized}


def _now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _decode_image_payload(payload: Any) -> bytes | None:
    if not isinstance(payload, str):
        return None
    value = payload
    if "," in value and value.lower().startswith("data:"):
        value = value.split(",", 1)[1]
    try:
        return base64.b64decode(value, validate=False)
    except Exception:
        return None


def _save_mineru_images(images: Any, images_dir: Path) -> int:
    if not isinstance(images, dict):
        return 0
    images_dir.mkdir(parents=True, exist_ok=True)
    saved = 0
    for name, payload in images.items():
        image_bytes = _decode_image_payload(payload)
        if not image_bytes:
            continue
        safe_name = Path(str(name or f"image_{saved + 1}.jpg")).name
        if not Path(safe_name).suffix:
            safe_name = f"{safe_name}.jpg"
        (images_dir / safe_name).write_bytes(image_bytes)
        saved += 1
    return saved


def _select_mineru_result_file(payload: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    results = payload.get("results")
    if not isinstance(results, dict):
        return None, None
    for file_name, file_data in results.items():
        if isinstance(file_data, dict) and file_data.get("md_content") is not None:
            return str(file_name), file_data
    return None, None


def _save_mineru_pdf_result(parse_dir: Path, payload: dict[str, Any]) -> dict[str, Any]:
    file_name, file_data = _select_mineru_result_file(payload)
    summary: dict[str, Any] = {
        "status": "completed" if file_data else "completed_without_markdown",
        "saved_at": _now_iso(),
        "mineru_backend": payload.get("backend"),
        "mineru_version": payload.get("version"),
        "result_file": file_name,
        "parse_dir": str(parse_dir),
        "artifacts": {},
    }
    _write_json(parse_dir / "result_payload_summary.json", summary)
    if not file_data:
        return summary

    markdown = str(file_data.get("md_content") or "")
    if markdown:
        (parse_dir / "result.md").write_text(markdown, encoding="utf-8")
        summary["artifacts"]["result.md"] = str(parse_dir / "result.md")

    artifact_map = {
        "middle_json": "middle.json",
        "model_output": "model_output.json",
        "content_list": "content_list.json",
    }
    for key, filename in artifact_map.items():
        if key in file_data:
            path = parse_dir / filename
            _write_json(path, file_data[key])
            summary["artifacts"][filename] = str(path)

    image_count = _save_mineru_images(file_data.get("images"), parse_dir / "images")
    if image_count:
        summary["artifacts"]["images"] = str(parse_dir / "images")
        summary["image_count"] = image_count

    _write_json(parse_dir / "result_payload_summary.json", summary)
    return summary


def _save_document_parser_pdf_result(parse_dir: Path, task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "status": "completed",
        "saved_at": _now_iso(),
        "document_parser_task_id": task_id,
        "parse_dir": str(parse_dir),
        "artifacts": {},
    }
    _write_json(parse_dir / "document_result.json", payload)
    summary["artifacts"]["document_result.json"] = str(parse_dir / "document_result.json")

    markdown = str(payload.get("markdown") or "")
    if markdown:
        markdown_path = parse_dir / "result.md"
        markdown_path.write_text(markdown, encoding="utf-8")
        summary["artifacts"]["result.md"] = str(markdown_path)

    manifest = payload.get("manifest")
    if isinstance(manifest, dict):
        manifest_path = parse_dir / "manifest.json"
        _write_json(manifest_path, manifest)
        summary["artifacts"]["manifest.json"] = str(manifest_path)

    artifacts = payload.get("artifacts")
    if isinstance(artifacts, dict):
        artifacts_path = parse_dir / "artifacts.json"
        _write_json(artifacts_path, artifacts)
        summary["artifacts"]["artifacts.json"] = str(artifacts_path)

    _write_json(parse_dir / "result_payload_summary.json", summary)
    return summary


async def _poll_document_parser_result(task_id: str, parse_dir: Path) -> None:
    metadata_path = parse_dir / "metadata.json"
    metadata: dict[str, Any] = {}
    if metadata_path.exists():
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except Exception:
            metadata = {}

    done_statuses = {"completed", "completed_with_warnings", "success", "done", "finished"}
    failed_statuses = {"failed", "error", "failure", "cancelled", "timeout"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(CHAT_DOCUMENT_PARSE_STATUS_TIMEOUT, connect=5.0)) as client:
        for attempt in range(1, CHAT_DOCUMENT_PARSE_MAX_POLLS + 1):
            await asyncio.sleep(CHAT_DOCUMENT_PARSE_POLL_INTERVAL)
            try:
                status_response = await client.get(
                    f"{DOCUMENT_PARSER_API_BASE}/api/status/{task_id}",
                    headers=_document_headers(),
                )
                status_payload = status_response.json() if status_response.content else {}
                if not status_response.is_success:
                    metadata.update(
                        {
                            "document_parser_status": "status_failed",
                            "document_parser_status_http_status": status_response.status_code,
                            "document_parser_status_error": status_payload.get("detail") or status_payload.get("error") or status_response.text,
                            "mineru_parse_status": "status_failed",
                            "last_polled_at": _now_iso(),
                        }
                    )
                    _write_json(metadata_path, metadata)
                    if status_response.status_code == 404:
                        return
                    continue

                raw_status = status_payload.get("status") or status_payload.get("stage") or status_payload.get("task_status")
                status = str(raw_status or "unknown").lower()
                metadata.update(
                    {
                        "document_parser_status": status,
                        "document_parser_stage": status_payload.get("stage"),
                        "document_parser_progress_percent": status_payload.get("progress_percent"),
                        "document_parser_task_id": task_id,
                        "mineru_task_id": task_id,
                        "mineru_parse_status": "completed" if status in done_statuses else status,
                        "last_polled_at": _now_iso(),
                        "poll_attempt": attempt,
                    }
                )
                _write_json(metadata_path, metadata)

                if status in failed_statuses:
                    metadata["document_parser_error"] = status_payload.get("error") or status_payload.get("message") or status_payload.get("detail") or status
                    metadata["mineru_parse_error"] = metadata["document_parser_error"]
                    _write_json(metadata_path, metadata)
                    return

                if status not in done_statuses:
                    continue

                result_response = await client.get(
                    f"{DOCUMENT_PARSER_API_BASE}/api/result/{task_id}",
                    headers=_document_headers(),
                )
                result_payload = result_response.json() if result_response.content else {}
                if not result_response.is_success:
                    metadata.update(
                        {
                            "document_parser_status": "completed_result_fetch_failed",
                            "document_parser_result_http_status": result_response.status_code,
                            "document_parser_result_error": result_payload.get("detail") or result_payload.get("error") or result_response.text,
                            "mineru_parse_status": "completed_result_fetch_failed",
                            "completed_at": _now_iso(),
                        }
                    )
                    _write_json(metadata_path, metadata)
                    return

                summary = _save_document_parser_pdf_result(parse_dir, task_id, result_payload)
                metadata.update(
                    {
                        "document_parser_status": status,
                        "document_parser_page_url": f"/documents?task={quote(task_id, safe='')}",
                        "document_parser_source_map_url": f"/api/documents/artifact/{quote(task_id, safe='')}/source_map.json",
                        "document_parser_blocks_url": f"/api/documents/artifact/{quote(task_id, safe='')}/blocks.json",
                        "document_parser_tables_url": f"/api/documents/artifact/{quote(task_id, safe='')}/tables.json",
                        "document_parser_source_page_url_template": f"/api/documents/source/{quote(task_id, safe='')}/page/<page_number>",
                        "document_parser_source_block_url_template": f"/api/documents/source/{quote(task_id, safe='')}/block/<block_id>",
                        "document_parser_source_table_url_template": f"/api/documents/source/{quote(task_id, safe='')}/table/<table_id>",
                        "mineru_parse_status": summary["status"],
                        "markdown_path": summary.get("artifacts", {}).get("result.md"),
                        "manifest_path": summary.get("artifacts", {}).get("manifest.json"),
                        "document_result_path": summary.get("artifacts", {}).get("document_result.json"),
                        "parse_dir": str(parse_dir),
                        "completed_at": _now_iso(),
                    }
                )
                _write_json(metadata_path, metadata)
                return
            except Exception as exc:
                metadata.update(
                    {
                        "document_parser_status": "poll_failed",
                        "document_parser_error": str(exc),
                        "mineru_parse_status": "poll_failed",
                        "mineru_parse_error": str(exc),
                        "last_polled_at": _now_iso(),
                        "poll_attempt": attempt,
                    }
                )
                _write_json(metadata_path, metadata)

    metadata.update(
        {
            "document_parser_status": "timeout",
            "document_parser_error": f"Document parser result was not ready after {CHAT_DOCUMENT_PARSE_MAX_POLLS} polls.",
            "mineru_parse_status": "timeout",
            "mineru_parse_error": f"Document parser result was not ready after {CHAT_DOCUMENT_PARSE_MAX_POLLS} polls.",
            "completed_at": _now_iso(),
        }
    )
    _write_json(metadata_path, metadata)


async def _poll_mineru_pdf_result(mineru_task_id: str, parse_dir: Path) -> None:
    metadata_path = parse_dir / "metadata.json"
    metadata: dict[str, Any] = {}
    if metadata_path.exists():
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except Exception:
            metadata = {}

    async with httpx.AsyncClient(timeout=httpx.Timeout(CHAT_PDF_PARSE_STATUS_TIMEOUT, connect=5.0)) as client:
        for attempt in range(1, CHAT_PDF_PARSE_MAX_POLLS + 1):
            await asyncio.sleep(CHAT_PDF_PARSE_POLL_INTERVAL)
            try:
                status_response = await client.get(f"{MINERU_API_BASE}/tasks/{mineru_task_id}")
                status_payload = status_response.json() if status_response.content else {}
                if not status_response.is_success:
                    metadata.update(
                        {
                            "mineru_parse_status": "status_failed",
                            "mineru_status_http_status": status_response.status_code,
                            "mineru_status_error": status_payload.get("detail") or status_payload.get("error") or status_response.text,
                            "last_polled_at": _now_iso(),
                        }
                    )
                    _write_json(metadata_path, metadata)
                    if status_response.status_code == 404:
                        return
                    continue

                raw_status = status_payload.get("status") or status_payload.get("state") or status_payload.get("task_status")
                status = str(raw_status or "unknown").lower()
                metadata.update(
                    {
                        "mineru_parse_status": status,
                        "mineru_status_payload": status_payload,
                        "last_polled_at": _now_iso(),
                        "poll_attempt": attempt,
                    }
                )
                _write_json(metadata_path, metadata)

                if status in {"failed", "error", "failure", "cancelled"}:
                    metadata["mineru_parse_error"] = (
                        status_payload.get("error") or status_payload.get("message") or status_payload.get("detail") or status
                    )
                    _write_json(metadata_path, metadata)
                    return

                if status != "completed":
                    continue

                result_response = await client.get(f"{MINERU_API_BASE}/tasks/{mineru_task_id}/result")
                result_payload = result_response.json() if result_response.content else {}
                if not result_response.is_success:
                    metadata.update(
                        {
                            "mineru_parse_status": "completed_result_fetch_failed",
                            "mineru_result_http_status": result_response.status_code,
                            "mineru_result_error": result_payload.get("detail") or result_payload.get("error") or result_response.text,
                            "completed_at": _now_iso(),
                        }
                    )
                    _write_json(metadata_path, metadata)
                    return

                summary = _save_mineru_pdf_result(parse_dir, result_payload)
                metadata.update(
                    {
                        "mineru_parse_status": summary["status"],
                        "markdown_path": summary.get("artifacts", {}).get("result.md"),
                        "content_list_path": summary.get("artifacts", {}).get("content_list.json"),
                        "parse_dir": str(parse_dir),
                        "completed_at": _now_iso(),
                    }
                )
                _write_json(metadata_path, metadata)
                return
            except Exception as exc:
                metadata.update(
                    {
                        "mineru_parse_status": "poll_failed",
                        "mineru_parse_error": str(exc),
                        "last_polled_at": _now_iso(),
                        "poll_attempt": attempt,
                    }
                )
                _write_json(metadata_path, metadata)

    metadata.update(
        {
            "mineru_parse_status": "timeout",
            "mineru_parse_error": f"MinerU result was not ready after {CHAT_PDF_PARSE_MAX_POLLS} polls.",
            "completed_at": _now_iso(),
        }
    )
    _write_json(metadata_path, metadata)


def _safe_attachment_filename(name: str, fallback: str) -> str:
    stem = Path(name or "").stem.strip()
    stem = re.sub(r"[^0-9A-Za-z._-]+", "_", stem).strip("._-")
    return stem[:64] or fallback


def _resolve_upload_type(filename: str, content_type: str, data_type: str) -> tuple[str, str, str]:
    declared_type = (content_type or "").strip().lower()
    payload_type = (data_type or "").strip().lower()
    suffix = Path(filename or "").suffix.lower()

    if declared_type in IMAGE_CONTENT_TYPES:
        return "image", declared_type, IMAGE_CONTENT_TYPES[declared_type]
    if payload_type in IMAGE_CONTENT_TYPES:
        return "image", payload_type, IMAGE_CONTENT_TYPES[payload_type]
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        content = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
            ".gif": "image/gif",
        }[suffix]
        return "image", content, IMAGE_CONTENT_TYPES[content]

    if declared_type in DOCUMENT_CONTENT_TYPES:
        return "document", declared_type, DOCUMENT_CONTENT_TYPES[declared_type]
    if payload_type in DOCUMENT_CONTENT_TYPES:
        return "document", payload_type, DOCUMENT_CONTENT_TYPES[payload_type]
    if suffix in DOCUMENT_SUFFIX_TYPES:
        content = DOCUMENT_SUFFIX_TYPES[suffix]
        return "document", content, DOCUMENT_CONTENT_TYPES[content]

    raise HTTPException(
        status_code=400,
        detail="Only images, PDF, Word, Markdown, TXT, CSV, JSON and RTF files are supported",
    )


def _decode_chat_attachment(filename: str, content_type: str, data_url: str) -> tuple[bytes, str, str, str]:
    match = DATA_URL_RE.match(data_url or "")
    if not match:
        raise HTTPException(status_code=400, detail="Only base64 data URLs are supported")

    kind, effective_type, ext = _resolve_upload_type(
        filename,
        content_type,
        match.group("mime") or "",
    )
    try:
        raw = base64.b64decode(match.group("data"), validate=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid base64 attachment data") from exc

    if not raw:
        raise HTTPException(status_code=400, detail="Attachment is empty")
    max_bytes = MAX_CHAT_IMAGE_BYTES if kind == "image" else MAX_CHAT_DOCUMENT_BYTES
    if len(raw) > max_bytes:
        limit_mb = max_bytes // (1024 * 1024)
        raise HTTPException(status_code=413, detail=f"Attachment exceeds {limit_mb} MB")

    fallback = "image" if kind == "image" else "document"
    safe_name = _safe_attachment_filename(filename, fallback)
    return raw, effective_type, f"{safe_name}{ext}", kind


def _decode_chat_image(filename: str, content_type: str, data_url: str) -> tuple[bytes, str, str]:
    raw, content_type, normalized_name, kind = _decode_chat_attachment(filename, content_type, data_url)
    if kind != "image":
        raise HTTPException(status_code=400, detail="Only image files are supported")
    return raw, content_type, normalized_name


def _role_value(user: User) -> str:
    return str(user.role.value if hasattr(user.role, "value") else user.role)


def _detach_current_user(async_session: AsyncSession, current_user: User) -> None:
    try:
        async_session.expunge(current_user)
    except Exception:
        pass


async def enforce_quota_or_429_async(
    async_session: AsyncSession,
    *,
    user_id: int,
    user_role: str,
    event_type: str,
    increment: int = 1,
) -> tuple[int, int | None]:
    try:
        return await ensure_within_quota_async(
            async_session,
            user_id=user_id,
            user_role=user_role,
            event_type=event_type,
            increment=increment,
        )
    except ValueError as exc:
        parts = str(exc).split(":")
        if len(parts) == 4 and parts[0] == "daily_quota_exceeded":
            raise _quota_error_payload(parts[1], int(parts[2]), int(parts[3])) from exc
        raise


async def record_document_artifact_async(
    async_session: AsyncSession,
    *,
    user_id: int,
    task_id: str,
    filename: str,
    source: str,
) -> UserArtifact:
    existing = (
        await async_session.exec(
            select(UserArtifact).where(
                UserArtifact.user_id == user_id,
                UserArtifact.artifact_type == "document_parse",
                UserArtifact.artifact_key == task_id,
            )
        )
    ).first()
    path = f"/documents?task={quote(task_id, safe='')}"
    if existing:
        changed = False
        for field, value in {
            "title": filename or task_id,
            "path": path,
            "source": source,
            "global_artifact_id": task_id,
        }.items():
            if value and getattr(existing, field) != value:
                setattr(existing, field, value)
                changed = True
        if changed:
            async_session.add(existing)
            await async_session.commit()
            await async_session.refresh(existing)
        return existing

    item = UserArtifact(
        user_id=user_id,
        artifact_type="document_parse",
        artifact_key=task_id,
        title=filename or task_id,
        path=path,
        source=source,
        global_artifact_id=task_id,
    )
    async_session.add(item)
    await async_session.commit()
    await async_session.refresh(item)
    return item


async def _submit_pdf_attachment_to_mineru(
    path: Path,
    stored_name: str,
    parse_dir: Path,
    background_tasks: BackgroundTasks,
    *,
    current_user_id: int,
    current_user_role: str,
    async_session: AsyncSession,
) -> dict:
    parse_dir.mkdir(parents=True, exist_ok=True)
    metadata: dict = {
        "document_parser_api_base": DOCUMENT_PARSER_API_BASE,
        "mineru_submit_status": "skipped",
        "mineru_parse_status": "not_submitted",
        "document_parser_submit_status": "skipped",
        "document_parser_status": "not_submitted",
        "parse_dir": str(parse_dir),
        "queue_policy": "document_parser_chat_attachment",
        "submitted_to_project_queue": False,
    }
    if not path.exists() or not path.is_file():
        metadata["document_parser_submit_error"] = "PDF file is missing"
        metadata["mineru_submit_error"] = "PDF file is missing"
        _write_json(parse_dir / "metadata.json", metadata)
        return metadata

    await enforce_quota_or_429_async(
        async_session,
        user_id=current_user_id,
        user_role=current_user_role,
        event_type=DOCUMENT_PARSE_EVENT,
    )
    form = {
        "source_type": "upload",
        "model_version": "auto",
        "ocr": "auto",
        "enable_formula": "true",
        "enable_table": "true",
        "language": "auto",
        "page_ranges": "",
        "extra_formats": "",
        "no_cache": "false",
        "data_id": f"chat_attachment:{stored_name}",
    }
    try:
        timeout = httpx.Timeout(CHAT_DOCUMENT_PARSE_SUBMIT_TIMEOUT, connect=5.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            raw = path.read_bytes()
            response = await client.post(
                f"{DOCUMENT_PARSER_API_BASE}/api/tasks",
                data=form,
                files={"files": (stored_name, raw, "application/pdf")},
                headers=_document_headers(),
            )
        payload = response.json() if response.content else {}
        if response.is_success:
            tasks = payload.get("tasks") if isinstance(payload, dict) else []
            task = tasks[0] if tasks else {}
            task_id = task.get("task_id")
            metadata.update(
                {
                    "document_parser_submit_status": "submitted" if task_id else "submitted_without_task_id",
                    "document_parser_status": "queued" if task_id else "unknown",
                    "document_parser_task_id": task_id,
                    "document_parser_status_url": f"/api/documents/status/{quote(str(task_id), safe='')}" if task_id else "",
                    "document_parser_result_url": f"/api/documents/result/{quote(str(task_id), safe='')}" if task_id else "",
                    "document_parser_page_url": f"/documents?task={quote(str(task_id), safe='')}" if task_id else "",
                    "mineru_submit_status": "submitted" if task_id else "submitted_without_task_id",
                    "mineru_parse_status": "pending" if task_id else "unknown",
                    "mineru_task_id": task_id,
                    "mineru_status_url": f"{DOCUMENT_PARSER_API_BASE}/api/status/{task_id}" if task_id else "",
                    "mineru_result_url": f"{DOCUMENT_PARSER_API_BASE}/api/result/{task_id}" if task_id else "",
                    "submitted_at": _now_iso(),
                }
            )
            if task_id:
                await record_document_artifact_async(
                    async_session,
                    user_id=current_user_id,
                    task_id=str(task_id),
                    filename=path.name,
                    source="chat_attachment",
                )
                await record_usage_async(
                    async_session,
                    user_id=current_user_id,
                    event_type=DOCUMENT_PARSE_EVENT,
                    source="chat_attachment",
                    metadata_json=json.dumps({"task_id": task_id, "filename": path.name}, ensure_ascii=False),
                )
            _write_json(parse_dir / "metadata.json", metadata)
            if task_id:
                background_tasks.add_task(_poll_document_parser_result, str(task_id), parse_dir)
        else:
            metadata.update(
                {
                    "document_parser_submit_status": "failed",
                    "document_parser_submit_error": payload.get("message") or payload.get("error") or response.text,
                    "document_parser_submit_http_status": response.status_code,
                    "mineru_submit_status": "failed",
                    "mineru_submit_error": payload.get("message") or payload.get("error") or response.text,
                    "mineru_submit_http_status": response.status_code,
                }
            )
            _write_json(parse_dir / "metadata.json", metadata)
    except Exception as exc:
        metadata.update(
            {
                "document_parser_submit_status": "failed",
                "document_parser_submit_error": str(exc),
                "mineru_submit_status": "failed",
                "mineru_submit_error": str(exc),
            }
        )
        _write_json(parse_dir / "metadata.json", metadata)
    return metadata


async def _check_achievements_async(async_session: AsyncSession) -> list[AchievementResponse]:
    """Async equivalent of the achievement checker used by chat routes."""
    achievements = (await async_session.exec(select(Achievement))).all()
    agent = await async_session.get(AgentState, 1)

    chat_count = (await async_session.exec(select(func.count()).where(InteractionLog.action == "chat"))).one()
    feed_count = (await async_session.exec(select(func.count()).where(InteractionLog.action == "feed"))).one()

    conditions = {
        "first_chat": chat_count >= 1,
        "chat_10": chat_count >= 10,
        "feed_5": feed_count >= 5,
        "level_5": agent.level >= 5,
        "all_max": agent.hunger > 90 and agent.mood > 90 and agent.energy > 90,
    }
    progress_map = {
        "first_chat": (chat_count, 1),
        "chat_10": (chat_count, 10),
        "feed_5": (feed_count, 5),
        "level_5": (agent.level, 5),
        "all_max": (1 if conditions["all_max"] else 0, 1),
    }

    newly_unlocked = []
    for achievement in achievements:
        progress, target = progress_map.get(achievement.id, (0, achievement.target))
        achievement.progress = min(progress, target)
        if achievement.unlocked_at is None and conditions.get(achievement.id, False):
            achievement.unlocked_at = datetime.utcnow()
            newly_unlocked.append(AchievementResponse.model_validate(achievement))

    await async_session.commit()
    return newly_unlocked


async def update_agent_and_achievements(async_session: AsyncSession) -> list[AchievementResponse]:
    """Update agent state and achievements without opening a sync DB session in async routes."""
    agent = await async_session.get(AgentState, 1)
    apply_decay(agent)
    perform_action(agent, "chat")
    async_session.add(InteractionLog(action="chat"))
    async_session.add(agent)
    await async_session.commit()
    return await _check_achievements_async(async_session)


async def update_agent_and_achievements_in_new_session() -> list[AchievementResponse]:
    """Open a fresh async session for streaming completion callbacks."""
    async with AsyncSession(async_engine) as async_session:
        return await update_agent_and_achievements(async_session)


@router.post("/chat/attachments", response_model=ChatAttachmentUploadResponse)
async def upload_chat_attachments(
    req: ChatAttachmentUploadRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
):
    files = req.files or []
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")
    if len(files) > MAX_CHAT_ATTACHMENT_COUNT:
        raise HTTPException(status_code=400, detail=f"Upload up to {MAX_CHAT_ATTACHMENT_COUNT} files per message")

    current_user_id = int(current_user.id)
    current_user_role = _role_value(current_user)
    _detach_current_user(async_session, current_user)

    user_upload_dir = CHAT_UPLOAD_DIR / str(current_user_id)
    user_upload_dir.mkdir(parents=True, exist_ok=True)
    attachments: list[ChatAttachment] = []
    for item in files:
        raw, content_type, normalized_name, kind = _decode_chat_attachment(
            item.filename,
            item.content_type,
            item.data_url,
        )
        attachment_id = uuid.uuid4().hex
        stored_name = f"{attachment_id}_{normalized_name}"
        target = user_upload_dir / stored_name
        target.write_bytes(raw)
        metadata = None
        if kind == "document" and content_type == "application/pdf":
            metadata = await _submit_pdf_attachment_to_mineru(
                target,
                stored_name,
                CHAT_PDF_PARSE_DIR / str(current_user_id) / attachment_id,
                background_tasks,
                current_user_id=current_user_id,
                current_user_role=current_user_role,
                async_session=async_session,
            )
        attachments.append(
            ChatAttachment(
                id=attachment_id,
                filename=item.filename or normalized_name,
                content_type=content_type,
                size=len(raw),
                path=str(target),
                url=f"/api/chat/attachments/{stored_name}",
                kind=kind,
                metadata=metadata,
            )
        )
    return ChatAttachmentUploadResponse(attachments=attachments)


@router.get("/chat/attachments/{stored_name}")
async def get_chat_attachment(
    stored_name: str,
    current_user: User = Depends(get_current_user),
):
    if "/" in stored_name or "\\" in stored_name:
        raise HTTPException(status_code=404, detail="Attachment not found")
    path = CHAT_UPLOAD_DIR / str(current_user.id) / stored_name
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Attachment not found")
    return FileResponse(path)


@router.get("/chat/audit-traces/{trace_id}", response_model=AnswerAuditTraceResponse)
async def get_chat_answer_audit_trace(
    trace_id: str,
    current_user: User = Depends(get_current_user),
):
    if not is_answer_audit_trace_id(trace_id):
        raise HTTPException(status_code=404, detail="Audit trace not found")
    trace = get_answer_audit_trace(trace_id)
    session_id = str((trace or {}).get("session_id") or "")
    if not trace or not session_id.startswith(_user_session_prefix(str(current_user.id), "assistant")):
        raise HTTPException(status_code=404, detail="Audit trace not found")
    return {"trace_id": trace_id, "trace": trace}


@router.post("/chat", response_model=ChatResponse)
async def chat(
    req: ChatRequest,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
):
    current_user_id = int(current_user.id)
    current_user_role = _role_value(current_user)
    _detach_current_user(async_session, current_user)

    await enforce_quota_or_429_async(
        async_session,
        user_id=current_user_id,
        user_role=current_user_role,
        event_type=AGENT_QUESTION_EVENT,
    )
    await record_usage_async(async_session, user_id=current_user_id, event_type=AGENT_QUESTION_EVENT, source="assistant")
    # 获取或创建用户会话；后端重启导致内存索引丢失时，从数据库历史恢复。
    session_id = await resolve_or_create_session(
        async_session,
        current_user,
        profile="assistant",
        session_id=getattr(req, "session_id", None),
    )

    control_reply = maybe_handle_model_control(req.message, "siq_assistant")
    if control_reply:
        await save_message(
            async_session,
            "user",
            req.display_message or req.message,
            session_id,
            attachments=req.attachments,
        )
        await save_message(async_session, "assistant", control_reply, session_id)
        get_session_manager().increment_message_count(session_id)
        return ChatResponse(reply=control_reply, new_achievements=[])

    audit_trace_id: str | None = None

    def capture_answer_audit(record: dict) -> None:
        nonlocal audit_trace_id
        candidate = str(record.get("trace_id") or "").strip()
        if is_answer_audit_trace_id(candidate):
            audit_trace_id = candidate

    reply = await collect_chat_reply(
        req.message,
        async_session,
        session_id=session_id,
        profile="siq_assistant",
        context=req.context,
        display_message=req.display_message,
        attachments=req.attachments,
        answer_audit_callback=capture_answer_audit,
    )
    get_session_manager().increment_message_count(session_id)

    new_achs = await update_agent_and_achievements(async_session)

    return ChatResponse(
        reply=reply,
        new_achievements=[a.model_dump(mode="json") for a in new_achs],
        audit_trace_id=audit_trace_id,
    )


@router.post("/chat/stream")
async def chat_stream(
    req: ChatRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
):
    current_user_id = int(current_user.id)
    current_user_role = _role_value(current_user)
    _detach_current_user(async_session, current_user)

    await enforce_quota_or_429_async(
        async_session,
        user_id=current_user_id,
        user_role=current_user_role,
        event_type=AGENT_QUESTION_EVENT,
    )
    await record_usage_async(async_session, user_id=current_user_id, event_type=AGENT_QUESTION_EVENT, source="assistant")
    # 获取或创建用户会话；后端重启导致内存索引丢失时，从数据库历史恢复。
    session_id = await resolve_or_create_session(
        async_session,
        current_user,
        profile="assistant",
        session_id=getattr(req, "session_id", None),
    )
    async def done_payload(_reply: str = "") -> dict:
        new_achs = await update_agent_and_achievements_in_new_session()
        return {"new_achievements": [a.model_dump(mode="json") for a in new_achs]}

    async def event_generator() -> AsyncGenerator[dict, None]:
        control_reply = maybe_handle_model_control(req.message, "siq_assistant")
        if control_reply:
            await save_message(
                async_session,
                "user",
                req.display_message or req.message,
                session_id,
                attachments=req.attachments,
            )
            await save_message(async_session, "assistant", control_reply, session_id)
            get_session_manager().increment_message_count(session_id)
            yield {
                "event": "delta",
                "data": json.dumps({"content": control_reply}, ensure_ascii=False),
            }
            yield {
                "event": "done",
                "data": json.dumps({"new_achievements": [], "content": control_reply}, ensure_ascii=False),
            }
            return

        async for event in stream_chat_reply(
            req.message,
            request,
            async_session,
            session_id=session_id,
            profile="siq_assistant",
            context=req.context,
            display_message=req.display_message,
            attachments=req.attachments,
            done_payload_factory=done_payload,
            emit_audit_trace_id=True,
        ):
            yield event
        get_session_manager().increment_message_count(session_id)

    return EventSourceResponse(event_generator())


@router.post("/chat/stop")
async def stop_chat(
    session_id: str | None = None,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
):
    session_id = await resolve_or_create_session(async_session, current_user, "assistant", session_id)

    return await stop_active_run("siq_assistant", session_id)


@router.get("/chat/active")
async def active_chat(
    session_id: str | None = None,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
):
    session_id = await resolve_or_create_session(async_session, current_user, "assistant", session_id)

    return get_active_run_snapshot("siq_assistant", session_id)


@router.get("/chat/active/stream")
async def active_chat_stream(
    request: Request,
    session_id: str | None = None,
    offset: int = 0,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
):
    session_id = await resolve_or_create_session(async_session, current_user, "assistant", session_id)

    if not has_active_run("siq_assistant", session_id):
        raise HTTPException(status_code=404, detail="No active chat run")
    return EventSourceResponse(
        stream_active_run_events(
            request,
            profile="siq_assistant",
            session_id=session_id,
            offset=offset,
        )
    )


@router.get("/chat/history")
async def chat_history(
    session_id: str = None,
    limit: int = HISTORY_LIMIT,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
):
    session_id = await resolve_or_create_session(async_session, current_user, "assistant", session_id)

    messages = await chat_history_response(async_session, session_id, limit=limit)
    return {"messages": messages, "session_id": session_id}


@router.get("/chat/sessions")
async def chat_sessions(
    limit: int = 100,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
):
    # 直接从Redis获取用户的会话列表
    session_mgr = get_session_manager()
    requested_limit = _session_limit(current_user, limit)
    redis_sessions = session_mgr.list_user_sessions(
        str(current_user.id),
        "assistant",
        limit=requested_limit
    )
    db_sessions = await _db_session_summaries(
        async_session,
        user_id=str(current_user.id),
        profile="assistant",
        limit=requested_limit,
    )
    sessions = _merge_sessions(redis_sessions, db_sessions, requested_limit)
    current_session_id = session_mgr.get_current_session_id(str(current_user.id), "assistant")
    message_summaries = await _session_message_summaries(
        async_session,
        [str(item.get("session_id") or "") for item in sessions],
    )

    return _sessions_payload(sessions, current_session_id, message_summaries)
@router.post("/chat/session")
async def create_session(
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
):
    session_mgr = get_session_manager()
    session_id, deleted_session_ids = session_mgr.create_session(
        user_id=str(current_user.id),
        profile="assistant",
        user_role=current_user.role,
        return_deleted=True,
    )
    await _delete_chat_messages(async_session, deleted_session_ids)
    return {"session_id": session_id, "created": True, "deleted_session_ids": deleted_session_ids}


@router.post("/chat/session/{session_id}")
async def switch_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
):
    session_mgr = get_session_manager()
    try:
        _assert_session_belongs_to_user(session_id, str(current_user.id), "assistant")
        session_mgr.set_current_session(str(current_user.id), "assistant", session_id)
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        summary = await _db_session_summary(async_session, session_id)
        if not summary:
            raise
        session_mgr.restore_session(
            str(current_user.id),
            "assistant",
            session_id,
            user_role=current_user.role,
            created_at=summary.get("created_at"),
            updated_at=summary.get("updated_at"),
            message_count=int(summary.get("message_count") or 0),
        )

    return {"session_id": session_id, "current": True}


@router.delete("/chat/session")
async def reset_session(
    session_id: str | None = None,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session)
):
    session_id = await resolve_or_create_session(async_session, current_user, "assistant", session_id)
    session_mgr = get_session_manager()
    session_mgr.delete_session(session_id, str(current_user.id))

    await _delete_chat_messages(async_session, [session_id])

    # 创建新会话
    new_session_id, deleted_session_ids = session_mgr.create_session(
        user_id=str(current_user.id),
        profile="assistant",
        user_role=current_user.role,
        return_deleted=True,
    )
    await _delete_chat_messages(async_session, deleted_session_ids)

    return {"session_id": new_session_id, "deleted_old": session_id}
