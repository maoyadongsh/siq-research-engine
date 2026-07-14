from __future__ import annotations

import hashlib
import json
import os
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncContextManager, Awaitable, Callable, Mapping, Sequence

import httpx
from fastapi import HTTPException

from services.upload_proxy_limits import (
    DEFAULT_MAX_BATCH_BYTES,
    DEFAULT_MAX_FILE_BYTES,
    UPLOAD_PROXY_LIMITER,
    UploadProxyConcurrencyLimiter,
    buffer_upload_files,
    close_buffered_uploads,
)

SUPPORTED_PARSE_MARKETS = frozenset({"CN", "HK", "US", "EU", "KR", "JP"})
TERMINAL_FAILED_TASK_STATUSES = frozenset({"failed", "error", "failure", "cancelled"})
SUPPORTED_SOURCE_CONTEXT_KEYS = frozenset(
    {
        "domain",
        "deal_id",
        "document_id",
        "source_type",
        "parse_run_id",
        "origin",
    }
)

_DOCUMENT_PROFILE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_MAX_SOURCE_CONTEXT_VALUE_CHARS = 255
_MAX_SOURCE_CONTEXT_JSON_BYTES = 2048

Task = dict[str, Any]
TaskLookup = Callable[[], Awaitable[Mapping[str, Mapping[str, Any]] | Sequence[Mapping[str, Any]]]]
QuotaReserveHook = Callable[[int], Awaitable[Any]]
QuotaReleaseHook = Callable[[], Awaitable[Any]]
UsageRecordHook = Callable[[list[Task]], Awaitable[Any]]
ArtifactRecordHook = Callable[[Task, str], Awaitable[Any]]
ArtifactExistsHook = Callable[[str], Awaitable[bool]]
HttpClientFactory = Callable[..., AsyncContextManager[Any]]


async def _empty_task_lookup() -> Sequence[Mapping[str, Any]]:
    return ()


async def _noop_reserve(_count: int) -> None:
    return None


async def _noop_release() -> None:
    return None


async def _noop_usage(_tasks: list[Task]) -> None:
    return None


async def _noop_artifact(_task: Task, _source: str) -> None:
    return None


async def _artifact_missing(_task_id: str) -> bool:
    return False


@dataclass(frozen=True)
class PDFParseConfig:
    backend: str = "hybrid-http-client"
    parse_method: str = "auto"
    market: str = "CN"
    start_page_id: str = ""
    end_page_id: str = ""
    formula_enable: str = "true"
    table_enable: str = "true"
    document_profile: str = ""
    source_context: Mapping[str, str] = field(default_factory=dict)

    @property
    def requested_market(self) -> str:
        return normalize_parse_market(self.market)

    def parser_form(self) -> dict[str, str]:
        form = {
            "backend": self.backend,
            "parse_method": self.parse_method,
            "market": self.requested_market or self.market,
            "start_page_id": self.start_page_id,
            "end_page_id": self.end_page_id,
            "formula_enable": self.formula_enable,
            "table_enable": self.table_enable,
        }
        if self.document_profile:
            form["document_profile"] = self.document_profile
        if self.source_context:
            form["source_context"] = json.dumps(
                dict(self.source_context),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        return form


@dataclass(frozen=True)
class PDFSubmissionHooks:
    """Request-scoped persistence hooks used by API-facing submission flows."""

    lookup_tasks: TaskLookup = _empty_task_lookup
    reserve_quota: QuotaReserveHook = _noop_reserve
    release_quota: QuotaReleaseHook = _noop_release
    record_usage: UsageRecordHook = _noop_usage
    record_artifact: ArtifactRecordHook = _noop_artifact
    has_artifact: ArtifactExistsHook = _artifact_missing


@dataclass(frozen=True)
class PDFParseSubmissionResult:
    status_code: int
    payload: Any | None
    content: bytes
    content_type: str
    requested_market: str
    parse_config_hash: str
    new_tasks: tuple[Task, ...] = ()
    reused_tasks: tuple[Task, ...] = ()

    @property
    def is_json(self) -> bool:
        return self.payload is not None


def normalize_parse_market(value: object) -> str:
    market = str(value or "").strip().upper()
    return market if market in SUPPORTED_PARSE_MARKETS else ""


def parse_bool_field(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _form_text(value: object, default: str) -> str:
    return value if isinstance(value, str) else default


def normalize_document_profile(value: object) -> str:
    profile = str(value or "").strip().lower()
    if not profile:
        return ""
    if not _DOCUMENT_PROFILE_RE.fullmatch(profile):
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_document_profile",
                "message": "document_profile must be a lowercase identifier up to 64 characters",
            },
        )
    return profile


def normalize_source_context(value: Mapping[str, object] | None) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise HTTPException(status_code=400, detail="source_context must be an object")

    unknown_keys = sorted(str(key) for key in value if str(key) not in SUPPORTED_SOURCE_CONTEXT_KEYS)
    if unknown_keys:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "unsupported_source_context_keys",
                "keys": unknown_keys,
            },
        )

    normalized: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key)
        if not isinstance(raw_value, str):
            raise HTTPException(status_code=400, detail=f"source_context.{key} must be a string")
        item = raw_value.strip()
        if not item:
            raise HTTPException(status_code=400, detail=f"source_context.{key} must not be empty")
        if len(item) > _MAX_SOURCE_CONTEXT_VALUE_CHARS:
            raise HTTPException(status_code=400, detail=f"source_context.{key} is too long")
        normalized[key] = item

    encoded = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) > _MAX_SOURCE_CONTEXT_JSON_BYTES:
        raise HTTPException(status_code=400, detail="source_context is too large")
    return normalized


def normalize_pdf_parse_config(
    config: Mapping[str, object] | PDFParseConfig | None = None,
    *,
    document_profile: object = "",
    source_context: Mapping[str, object] | None = None,
) -> PDFParseConfig:
    if isinstance(config, PDFParseConfig):
        raw: Mapping[str, object] = {
            "backend": config.backend,
            "parse_method": config.parse_method,
            "market": config.market,
            "start_page_id": config.start_page_id,
            "end_page_id": config.end_page_id,
            "formula_enable": config.formula_enable,
            "table_enable": config.table_enable,
            "document_profile": config.document_profile,
            "source_context": config.source_context,
        }
    else:
        raw = config or {}

    selected_profile = document_profile if document_profile not in (None, "") else raw.get("document_profile", "")
    selected_context = source_context if source_context is not None else raw.get("source_context")
    if selected_context is not None and not isinstance(selected_context, Mapping):
        raise HTTPException(status_code=400, detail="source_context must be an object")

    return PDFParseConfig(
        backend=_form_text(raw.get("backend"), "hybrid-http-client").strip() or "hybrid-http-client",
        parse_method=_form_text(raw.get("parse_method"), "auto").strip() or "auto",
        market=_form_text(raw.get("market"), "CN").strip() or "CN",
        start_page_id=_form_text(raw.get("start_page_id"), ""),
        end_page_id=_form_text(raw.get("end_page_id"), ""),
        formula_enable=_form_text(raw.get("formula_enable"), "true"),
        table_enable=_form_text(raw.get("table_enable"), "true"),
        document_profile=normalize_document_profile(selected_profile),
        source_context=normalize_source_context(selected_context),
    )


def pdf_parse_config_hash(config: PDFParseConfig, *, parser_version: str | None = None) -> str:
    version = parser_version
    if version is None:
        version = os.environ.get("SIQ_PDF_PARSE_CONFIG_VERSION", "pdf_parser_v1").strip() or "pdf_parser_v1"
    canonical: dict[str, object] = {
        "parser_version": version,
        "market": config.requested_market or "CN",
        "backend": config.backend.strip(),
        "parse_method": config.parse_method.strip(),
        "start_page_id": config.start_page_id,
        "end_page_id": config.end_page_id,
        "formula_enable": parse_bool_field(config.formula_enable, True),
        "table_enable": parse_bool_field(config.table_enable, True),
    }
    if config.document_profile:
        canonical["document_profile"] = config.document_profile
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def pdf_dedupe_key(task: Mapping[str, object], default_market: str = "") -> tuple[str, str, str]:
    market = (
        normalize_parse_market(task.get("market_scope"))
        or normalize_parse_market(task.get("market"))
        or normalize_parse_market(default_market)
        or "CN"
    )
    return (
        market,
        str(task.get("file_sha256") or "").strip().lower(),
        str(task.get("parse_config_hash") or "").strip(),
    )


def _existing_tasks(value: Mapping[str, Mapping[str, Any]] | Sequence[Mapping[str, Any]]) -> list[Task]:
    items = value.values() if isinstance(value, Mapping) else value
    tasks: list[Task] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        task = dict(item)
        if str(task.get("status") or "").strip().lower() in TERMINAL_FAILED_TASK_STATUSES:
            continue
        tasks.append(task)
    return tasks


def _normalize_task(task: Mapping[str, Any], requested_market: str) -> Task:
    normalized = dict(task)
    if requested_market and not normalize_parse_market(normalized.get("market")):
        normalized["market"] = requested_market
    return normalized


def _json_content(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


@asynccontextmanager
async def _unlimited_slot():
    yield


async def submit_pdf_parse(
    *,
    files: Sequence[Any],
    parser_api_base: str,
    config: Mapping[str, object] | PDFParseConfig | None = None,
    document_profile: object = "",
    source_context: Mapping[str, object] | None = None,
    headers: Mapping[str, str] | None = None,
    hooks: PDFSubmissionHooks | None = None,
    parser_version: str | None = None,
    timeout: httpx.Timeout | float = 180.0,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    max_batch_bytes: int = DEFAULT_MAX_BATCH_BYTES,
    limiter: UploadProxyConcurrencyLimiter | None = UPLOAD_PROXY_LIMITER,
    http_client_factory: HttpClientFactory | None = None,
    buffer_uploads_fn: Callable[..., Awaitable[list[Any]]] = buffer_upload_files,
    close_uploads_fn: Callable[[list[Any]], None] = close_buffered_uploads,
) -> PDFParseSubmissionResult:
    """Buffer, deduplicate and submit PDF uploads with request-scoped side effects.

    Callers own authentication and persistence. Hooks keep quota and artifact writes
    in the caller's transaction/session while this service owns their sequencing.
    """

    normalized_config = normalize_pdf_parse_config(
        config,
        document_profile=document_profile,
        source_context=source_context,
    )
    requested_market = normalized_config.requested_market
    config_hash = pdf_parse_config_hash(normalized_config, parser_version=parser_version)
    side_effects = hooks or PDFSubmissionHooks()
    client_factory = http_client_factory or httpx.AsyncClient
    slot = limiter.slot() if limiter is not None else _unlimited_slot()
    buffered_uploads = []
    reserved_count = 0

    async with slot:
        buffered_uploads = await buffer_uploads_fn(
            list(files),
            max_file_bytes=max_file_bytes,
            max_batch_bytes=max_batch_bytes,
            default_filename="upload.pdf",
            default_content_type="application/pdf",
            reject_empty=True,
        )
        try:
            uploads: list[Task] = []
            seen_hashes: set[str] = set()
            for item in buffered_uploads:
                filename = item.filename or "upload.pdf"
                if item.sha256 in seen_hashes:
                    payload = {
                        "error": "duplicate_file_content",
                        "message": f"本次上传中包含重复文档内容，请勿重复解析: {filename}",
                        "filename": filename,
                        "existingTask": None,
                    }
                    return PDFParseSubmissionResult(
                        status_code=409,
                        payload=payload,
                        content=_json_content(payload),
                        content_type="application/json",
                        requested_market=requested_market,
                        parse_config_hash=config_hash,
                    )
                seen_hashes.add(item.sha256)
                uploads.append(
                    {
                        "filename": filename,
                        "content": item.file,
                        "content_type": item.content_type or "application/pdf",
                        "file_sha256": item.sha256,
                        "market": requested_market or normalize_parse_market(normalized_config.market) or "CN",
                        "parse_config_hash": config_hash,
                    }
                )

            existing = _existing_tasks(await side_effects.lookup_tasks())
            existing_by_key = {
                pdf_dedupe_key(task, requested_market): task
                for task in existing
                if str(task.get("file_sha256") or "").strip()
                and str(task.get("parse_config_hash") or "").strip()
            }
            new_parse_count = sum(
                1 for upload in uploads if pdf_dedupe_key(upload, requested_market) not in existing_by_key
            )
            if new_parse_count:
                await side_effects.reserve_quota(new_parse_count)
                reserved_count = new_parse_count

            multipart = [
                ("files", (str(upload["filename"]), upload["content"], str(upload["content_type"])))
                for upload in uploads
            ]
            try:
                async with client_factory(timeout=timeout) as client:
                    response = await client.post(
                        f"{parser_api_base.rstrip('/')}/api/upload",
                        data=normalized_config.parser_form(),
                        files=multipart,
                        headers=dict(headers or {}),
                    )
            except httpx.RequestError as exc:
                if reserved_count:
                    await side_effects.release_quota()
                raise HTTPException(status_code=502, detail=f"PDF 解析服务不可用: {exc}") from exc

            content_type = response.headers.get("content-type", "application/json")
            try:
                payload = response.json()
            except ValueError:
                if reserved_count:
                    await side_effects.release_quota()
                return PDFParseSubmissionResult(
                    status_code=response.status_code,
                    payload=None,
                    content=response.content,
                    content_type=content_type,
                    requested_market=requested_market,
                    parse_config_hash=config_hash,
                )

            duplicate_errors = {"duplicate_filename", "duplicate_file_content"}
            if response.status_code == 409 and isinstance(payload, dict) and payload.get("error") in duplicate_errors:
                existing_task = payload.get("existingTask") or payload.get("existing_task") or {}
                if isinstance(existing_task, Mapping):
                    normalized_existing = _normalize_task(existing_task, requested_market)
                    if isinstance(payload.get("existingTask"), Mapping):
                        payload["existingTask"] = normalized_existing
                    if isinstance(payload.get("existing_task"), Mapping):
                        payload["existing_task"] = normalized_existing
                    task_id = str(normalized_existing.get("task_id") or "")
                    if task_id:
                        artifact_task = dict(normalized_existing)
                        artifact_task.setdefault(
                            "filename",
                            str(payload.get("filename") or task_id or "已有解析任务"),
                        )
                        await side_effects.record_artifact(artifact_task, "reused_parse")
                if reserved_count:
                    await side_effects.release_quota()
                return PDFParseSubmissionResult(
                    status_code=409,
                    payload=payload,
                    content=_json_content(payload),
                    content_type=content_type,
                    requested_market=requested_market,
                    parse_config_hash=config_hash,
                    reused_tasks=(dict(normalized_existing),) if isinstance(existing_task, Mapping) else (),
                )

            if not 200 <= response.status_code < 300:
                if reserved_count:
                    await side_effects.release_quota()
                return PDFParseSubmissionResult(
                    status_code=response.status_code,
                    payload=payload,
                    content=_json_content(payload),
                    content_type=content_type,
                    requested_market=requested_market,
                    parse_config_hash=config_hash,
                )

            raw_tasks = payload.get("tasks") if isinstance(payload, dict) else []
            normalized_tasks = [
                _normalize_task(task, requested_market)
                for task in (raw_tasks or [])
                if isinstance(task, Mapping)
            ]
            if isinstance(payload, dict):
                payload["tasks"] = normalized_tasks

            new_tasks: list[Task] = []
            reused_tasks: list[Task] = []
            for task in normalized_tasks:
                if pdf_dedupe_key(task, requested_market) in existing_by_key:
                    reused_tasks.append(task)
                else:
                    new_tasks.append(task)

            if new_tasks:
                await side_effects.record_usage(new_tasks)
                reserved_count = 0
            elif reserved_count:
                await side_effects.release_quota()
                reserved_count = 0

            for task in new_tasks:
                if str(task.get("task_id") or ""):
                    await side_effects.record_artifact(task, "new_parse")
            for task in reused_tasks:
                task_id = str(task.get("task_id") or "")
                if task_id and not await side_effects.has_artifact(task_id):
                    await side_effects.record_artifact(task, "reused_parse")

            return PDFParseSubmissionResult(
                status_code=response.status_code,
                payload=payload,
                content=_json_content(payload),
                content_type=content_type,
                requested_market=requested_market,
                parse_config_hash=config_hash,
                new_tasks=tuple(new_tasks),
                reused_tasks=tuple(reused_tasks),
            )
        finally:
            close_uploads_fn(buffered_uploads)
