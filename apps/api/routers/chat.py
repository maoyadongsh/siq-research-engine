import json
import asyncio
import uuid
import base64
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncGenerator

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlmodel import Session, select
from sqlmodel.ext.asyncio.session import AsyncSession
from sse_starlette.sse import EventSourceResponse

from database import get_session, get_async_session
from models import PetState, ChatMessage, InteractionLog
from services.auth_service import User
from schemas import (
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
from services.hermes_model_control import maybe_handle_model_control
from services.achievement_checker import check_achievements
from services.session_manager import get_session_manager, keeps_sessions_forever
from services.auth_dependencies import get_current_user
from services.path_config import BACKEND_DATA_ROOT
from routers.pet import apply_decay, perform_action
from routers.workspace import enforce_quota_or_429
from services.usage_service import AGENT_QUESTION_EVENT, record_usage

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


async def _submit_pdf_attachment_to_mineru(
    path: Path,
    stored_name: str,
    parse_dir: Path,
    background_tasks: BackgroundTasks,
) -> dict:
    parse_dir.mkdir(parents=True, exist_ok=True)
    metadata: dict = {
        "mineru_api_base": MINERU_API_BASE,
        "mineru_submit_status": "skipped",
        "mineru_parse_status": "not_submitted",
        "parse_dir": str(parse_dir),
        "queue_policy": "direct_mineru_no_pdf2md_frontend_queue",
        "submitted_to_project_queue": False,
    }
    if not path.exists() or not path.is_file():
        metadata["mineru_submit_error"] = "PDF file is missing"
        _write_json(parse_dir / "metadata.json", metadata)
        return metadata

    data = {
        "backend": "hybrid-http-client",
        "parse_method": "auto",
        "formula_enable": "true",
        "table_enable": "true",
        "server_url": VLM_API_BASE,
        "return_md": "true",
        "return_middle_json": "true",
        "return_model_output": "true",
        "return_content_list": "true",
        "return_images": "true",
        "response_format_zip": "false",
        "return_original_file": "false",
        "lang_list": "ch",
    }
    try:
        timeout = httpx.Timeout(CHAT_PDF_PARSE_SUBMIT_TIMEOUT, connect=5.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            raw = path.read_bytes()
            response = await client.post(
                f"{MINERU_API_BASE}/tasks",
                data=data,
                files={"files": (stored_name, raw, "application/pdf")},
            )
        payload = response.json() if response.content else {}
        if response.is_success:
            task_id = payload.get("task_id")
            metadata.update(
                {
                    "mineru_submit_status": "submitted" if task_id else "submitted_without_task_id",
                    "mineru_parse_status": "pending" if task_id else "unknown",
                    "mineru_task_id": task_id,
                    "mineru_status_url": f"{MINERU_API_BASE}/tasks/{task_id}" if task_id else "",
                    "mineru_result_url": f"{MINERU_API_BASE}/tasks/{task_id}/result" if task_id else "",
                    "submitted_at": _now_iso(),
                }
            )
            _write_json(parse_dir / "metadata.json", metadata)
            if task_id:
                background_tasks.add_task(_poll_mineru_pdf_result, task_id, parse_dir)
        else:
            metadata.update(
                {
                    "mineru_submit_status": "failed",
                    "mineru_submit_error": payload.get("message") or payload.get("error") or response.text,
                    "mineru_submit_http_status": response.status_code,
                }
            )
            _write_json(parse_dir / "metadata.json", metadata)
    except Exception as exc:
        metadata.update(
            {
                "mineru_submit_status": "failed",
                "mineru_submit_error": str(exc),
            }
        )
        _write_json(parse_dir / "metadata.json", metadata)
    return metadata


def update_pet_and_achievements(sync_session: Session) -> list[AchievementResponse]:
    """Sync helper: update pet state and check achievements."""
    pet = sync_session.get(PetState, 1)
    apply_decay(pet)
    perform_action(pet, "chat")
    sync_session.add(InteractionLog(action="chat"))
    sync_session.add(pet)
    sync_session.commit()
    return check_achievements(sync_session)


@router.post("/chat/attachments", response_model=ChatAttachmentUploadResponse)
async def upload_chat_attachments(
    req: ChatAttachmentUploadRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
):
    files = req.files or []
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")
    if len(files) > MAX_CHAT_ATTACHMENT_COUNT:
        raise HTTPException(status_code=400, detail=f"Upload up to {MAX_CHAT_ATTACHMENT_COUNT} files per message")

    user_upload_dir = CHAT_UPLOAD_DIR / str(current_user.id)
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
                CHAT_PDF_PARSE_DIR / str(current_user.id) / attachment_id,
                background_tasks,
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


@router.post("/chat", response_model=ChatResponse)
async def chat(
    req: ChatRequest,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
    sync_session: Session = Depends(get_session),
):
    enforce_quota_or_429(sync_session, current_user, AGENT_QUESTION_EVENT)
    record_usage(sync_session, user_id=int(current_user.id), event_type=AGENT_QUESTION_EVENT, source="assistant")
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

    reply = await collect_chat_reply(
        req.message,
        async_session,
        session_id=session_id,
        profile="siq_assistant",
        context=req.context,
        display_message=req.display_message,
        attachments=req.attachments,
    )
    get_session_manager().increment_message_count(session_id)

    # Update pet & achievements (sync DB)
    loop = asyncio.get_event_loop()
    with next(get_session()) as sync_session:
        new_achs = await loop.run_in_executor(
            None, update_pet_and_achievements, sync_session
        )

    return ChatResponse(reply=reply, new_achievements=[a.model_dump(mode="json") for a in new_achs])


@router.post("/chat/stream")
async def chat_stream(
    req: ChatRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    async_session: AsyncSession = Depends(get_async_session),
    sync_session: Session = Depends(get_session),
):
    enforce_quota_or_429(sync_session, current_user, AGENT_QUESTION_EVENT)
    record_usage(sync_session, user_id=int(current_user.id), event_type=AGENT_QUESTION_EVENT, source="assistant")
    # 获取或创建用户会话；后端重启导致内存索引丢失时，从数据库历史恢复。
    session_id = await resolve_or_create_session(
        async_session,
        current_user,
        profile="assistant",
        session_id=getattr(req, "session_id", None),
    )
    async def done_payload(_reply: str = "") -> dict:
        loop = asyncio.get_event_loop()
        with next(get_session()) as sync_session:
            new_achs = await loop.run_in_executor(
                None, update_pet_and_achievements, sync_session
            )
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
