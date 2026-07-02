import html
import hashlib
import hmac
import os
import re
import time
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlmodel import Session, select

from database import get_session
from services.auth_dependencies import get_current_user
from services.auth_service import AuthService, User
from services.usage_service import UserArtifact


PDF2MD_API_BASE = (os.environ.get("SIQ_PDF2MD_API_BASE") or os.environ.get("PDF2MD_API_BASE", "http://127.0.0.1:15000")).rstrip("/")
PDF2MD_ACCESS_TOKEN = (os.environ.get("SIQ_PDF2MD_ACCESS_TOKEN") or os.environ.get("PDF2MD_ACCESS_TOKEN", "")).strip()
PDF2MD_PROXY_TIMEOUT = float(os.environ.get("SIQ_PDF2MD_PROXY_TIMEOUT") or os.environ.get("PDF2MD_PROXY_TIMEOUT", "60"))
PUBLIC_ORIGIN = (os.environ.get("SIQ_PUBLIC_ORIGIN") or os.environ.get("SIQ_PUBLIC_ORIGIN", "https://arthurmao.synology.me:9391")).rstrip("/")

router = APIRouter(tags=["source"])
optional_security = HTTPBearer(auto_error=False)
SOURCE_ACCESS_TOKEN_TTL_SECONDS = int(os.environ.get("SIQ_SOURCE_ACCESS_TOKEN_TTL_SECONDS", "900"))
SOURCE_TOKEN_SECRET_ENV = "SIQ_SOURCE_TOKEN_SECRET"
SOURCE_ACCEPT_LEGACY_AUTH_SECRET_ENV = "SIQ_SOURCE_ACCEPT_LEGACY_AUTH_SECRET"
MIN_SOURCE_TOKEN_SECRET_LENGTH = 32


def _content_type(headers: httpx.Headers) -> str:
    return headers.get("content-type") or "application/octet-stream"


def _pdf2md_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = dict(extra or {})
    if PDF2MD_ACCESS_TOKEN:
        headers.setdefault("X-PDF2MD-Token", PDF2MD_ACCESS_TOKEN)
    return headers


def _public_url(path: str) -> str:
    if not path:
        return path
    if path.startswith(("http://", "https://")):
        parsed = urlsplit(path)
        if parsed.hostname in {"localhost", "127.0.0.1"} and parsed.path.startswith("/api/"):
            suffix = parsed.path
            if parsed.query:
                suffix = f"{suffix}?{parsed.query}"
            return f"{PUBLIC_ORIGIN}{suffix}"
        return path
    if path.startswith("/api/"):
        return f"{PUBLIC_ORIGIN}{path}"
    return path


def _role_value(user: User) -> str:
    return str(user.role.value if hasattr(user.role, "value") else user.role)


def _is_admin(user: User) -> bool:
    return _role_value(user) in {"admin", "super_admin"}


def _token_user(token: str, session: Session) -> User | None:
    payload = AuthService.decode_token(token)
    if not payload:
        return None
    subject = str(payload.get("sub") or "").strip()
    if not subject:
        return None
    if subject.isdigit():
        user = session.exec(select(User).where(User.id == int(subject))).first()
    else:
        user = session.exec(select(User).where(User.username == subject)).first()
    if not user or not user.is_active:
        return None
    if getattr(user, "approval_status", "approved") != "approved":
        return None
    return user


def _user_has_task_access(session: Session, user: User, task_id: str) -> bool:
    if _is_admin(user):
        return True
    item = session.exec(
        select(UserArtifact).where(
            UserArtifact.user_id == int(user.id),
            UserArtifact.artifact_type == "parse",
            UserArtifact.artifact_key == task_id,
        )
    ).first()
    if item:
        return True
    item = session.exec(
        select(UserArtifact).where(
            UserArtifact.user_id == int(user.id),
            UserArtifact.artifact_type == "parse",
            UserArtifact.global_artifact_id == task_id,
        )
    ).first()
    return item is not None


def _configured_source_token_secret() -> str | None:
    secret = (os.environ.get(SOURCE_TOKEN_SECRET_ENV) or "").strip()
    if not secret:
        return None
    if len(secret) < MIN_SOURCE_TOKEN_SECRET_LENGTH:
        raise RuntimeError(
            f"{SOURCE_TOKEN_SECRET_ENV} must be at least {MIN_SOURCE_TOKEN_SECRET_LENGTH} characters."
        )
    return secret


def _source_token_signing_secret() -> str:
    return _configured_source_token_secret() or AuthService.secret_key()


def _source_token_verification_secrets() -> list[str]:
    source_secret = _configured_source_token_secret()
    if not source_secret:
        return [AuthService.secret_key()]
    return [source_secret]


def _accept_legacy_source_token_secret() -> bool:
    return (os.environ.get(SOURCE_ACCEPT_LEGACY_AUTH_SECRET_ENV) or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _legacy_source_token_verification_secret() -> str | None:
    if not _accept_legacy_source_token_secret():
        return None
    source_secret = _configured_source_token_secret()
    if not source_secret:
        return None
    try:
        legacy_auth_secret = AuthService.secret_key()
    except RuntimeError:
        return None
    if hmac.compare_digest(source_secret, legacy_auth_secret):
        return None
    return legacy_auth_secret


def _source_token_signature(task_id: str, expires_at: int, *, secret: str | None = None) -> str:
    payload = f"{task_id}:{expires_at}".encode("utf-8")
    signing_secret = secret or _source_token_signing_secret()
    return hmac.new(signing_secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def create_source_access_token(task_id: str, ttl_seconds: int = SOURCE_ACCESS_TOKEN_TTL_SECONDS) -> str:
    expires_at = int(time.time()) + max(60, ttl_seconds)
    return f"{expires_at}.{_source_token_signature(task_id, expires_at)}"


def _valid_source_access_token(task_id: str, token: str | None) -> bool:
    if not token or "." not in token:
        return False
    expires_text, signature = token.split(".", 1)
    try:
        expires_at = int(expires_text)
    except ValueError:
        return False
    if expires_at < int(time.time()):
        return False
    for secret in _source_token_verification_secrets():
        expected = _source_token_signature(task_id, expires_at, secret=secret)
        if hmac.compare_digest(signature, expected):
            return True
    legacy_secret = _legacy_source_token_verification_secret()
    if legacy_secret:
        expected = _source_token_signature(task_id, expires_at, secret=legacy_secret)
        return hmac.compare_digest(signature, expected)
    return False


def _request_access_token(request: Request, credentials: HTTPAuthorizationCredentials | None) -> str:
    if credentials and credentials.credentials:
        return credentials.credentials
    return str(request.query_params.get("access_token") or "").strip()


def _authorize_task_access(
    *,
    request: Request,
    task_id: str,
    session: Session,
    credentials: HTTPAuthorizationCredentials | None,
) -> str:
    source_token = str(request.query_params.get("source_token") or "").strip()
    if _valid_source_access_token(task_id, source_token):
        return source_token

    access_token = _request_access_token(request, credentials)
    if not access_token:
        raise HTTPException(status_code=401, detail="Missing source access token")
    user = _token_user(access_token, session)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired source access token")
    if not _user_has_task_access(session, user, task_id):
        raise HTTPException(status_code=403, detail="PDF task does not belong to current user")
    return create_source_access_token(task_id)


def _append_source_token(url: str, source_token: str | None) -> str:
    if not source_token:
        return url
    parsed = urlsplit(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["source_token"] = source_token
    query.pop("access_token", None)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))


def _source_url(path: str, source_token: str | None) -> str:
    return _append_source_token(_public_url(path), source_token)


def _resolve_source_open_path(kind: str, task_id: str, identifier: int) -> str:
    if kind == "pdf_page":
        return f"/api/pdf_page/{task_id}/{identifier}?format=html"
    if kind == "source_page":
        return f"/api/source/{task_id}/page/{identifier}?format=html"
    if kind == "source_table":
        return f"/api/source/{task_id}/table/{identifier}?format=html"
    raise HTTPException(status_code=404, detail="Unknown source link kind")


async def _request_pdf2md(
    request: Request,
    upstream_path: str,
    *,
    method: str | None = None,
    json_body: Any | None = None,
) -> httpx.Response:
    request_method = method or request.method
    url = f"{PDF2MD_API_BASE}{upstream_path}"
    upstream_params = [
        (key, value)
        for key, value in request.query_params.multi_items()
        if key.lower() not in {"access_token", "source_token"}
    ]
    kwargs: dict[str, Any] = {"params": upstream_params, "headers": _pdf2md_headers()}

    if json_body is not None:
        kwargs["json"] = json_body
    elif request_method in {"POST", "PUT", "PATCH"}:
        kwargs["content"] = await request.body()
        content_type = request.headers.get("content-type")
        if content_type:
            kwargs["headers"] = _pdf2md_headers({"content-type": content_type})

    try:
        async with httpx.AsyncClient(timeout=PDF2MD_PROXY_TIMEOUT) as client:
            return await client.request(request_method, url, **kwargs)
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"PDF parser source service unavailable: {exc}",
        ) from exc


async def _source_page_data(task_id: str, page_number: int) -> dict[str, Any] | None:
    try:
        async with httpx.AsyncClient(timeout=PDF2MD_PROXY_TIMEOUT) as client:
            response = await client.get(
                f"{PDF2MD_API_BASE}/api/source/{task_id}/page/{page_number}",
                headers=_pdf2md_headers(),
            )
    except httpx.RequestError:
        return None
    if response.status_code >= 400:
        return None
    try:
        data = response.json()
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    return data


async def _proxy_pdf2md(
    request: Request,
    upstream_path: str,
    *,
    method: str | None = None,
    json_body: Any | None = None,
) -> Response:
    upstream = await _request_pdf2md(request, upstream_path, method=method, json_body=json_body)
    body = b"" if request.method == "HEAD" else upstream.content
    return Response(
        content=body,
        status_code=upstream.status_code,
        media_type=_content_type(upstream.headers),
    )


def _wants_html(request: Request) -> bool:
    fmt = (request.query_params.get("format") or "").lower()
    if fmt == "json":
        return False
    if fmt == "html":
        return True
    accept = request.headers.get("accept", "")
    return "text/html" in accept and "application/json" not in accept


def _clean_table_html(value: str) -> str:
    value = re.sub(r"<\s*script\b[^>]*>.*?<\s*/\s*script\s*>", "", value, flags=re.I | re.S)
    value = re.sub(r"\son\w+\s*=\s*(['\"]).*?\1", "", value, flags=re.I | re.S)
    return value


def _html_shell(*, title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{html.escape(title)}</title>
  <style>
    :root {{ color-scheme: light; font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    body {{ margin: 0; background: #f6f8fb; color: #0f172a; }}
    .wrap {{ max-width: 1180px; margin: 0 auto; padding: 28px 20px 48px; }}
    .header {{ background: #fff; border: 1px solid #e2e8f0; border-radius: 18px; padding: 22px; box-shadow: 0 16px 36px rgba(15,23,42,.07); }}
    h1 {{ margin: 0; font-size: 24px; line-height: 1.25; }}
    h2 {{ margin: 0 0 12px; font-size: 18px; }}
    .sub {{ margin-top: 8px; color: #64748b; word-break: break-all; }}
    .actions {{ display: flex; flex-wrap: wrap; gap: 10px; margin-top: 18px; }}
    .btn {{ display: inline-flex; align-items: center; min-height: 38px; padding: 0 14px; border-radius: 10px; background: #0052ff; color: #fff; text-decoration: none; font-weight: 700; font-size: 14px; }}
    .btn.secondary {{ background: #eef4ff; color: #0f3ea8; }}
    .btn.disabled {{ pointer-events: none; background: #e2e8f0; color: #94a3b8; }}
    .page-nav {{ align-items: center; margin-top: 16px; }}
    .page-nav .btn {{ min-height: 44px; }}
    .page-indicator {{ display: inline-flex; min-height: 44px; align-items: center; padding: 0 12px; border-radius: 10px; background: #f8fafc; color: #475569; font-size: 13px; font-weight: 800; }}
    .meta {{ display: grid; grid-template-columns: repeat(auto-fit,minmax(170px,1fr)); gap: 10px; margin: 16px 0 0; }}
    .meta div {{ border: 1px solid #e2e8f0; background: #f8fafc; border-radius: 12px; padding: 10px 12px; }}
    .meta span {{ display: block; color: #64748b; font-size: 12px; }}
    .meta strong {{ display: block; margin-top: 4px; font-size: 14px; word-break: break-all; }}
    .panel {{ margin-top: 18px; background: #fff; border: 1px solid #e2e8f0; border-radius: 18px; padding: 18px; box-shadow: 0 12px 28px rgba(15,23,42,.05); }}
    .table-scroll {{ overflow: auto; max-height: 72vh; border: 1px solid #e2e8f0; border-radius: 12px; }}
    table {{ border-collapse: collapse; width: max-content; min-width: 100%; background: #fff; font-size: 14px; }}
    th, td {{ border: 1px solid #e2e8f0; padding: 8px 10px; vertical-align: top; }}
    tr:first-child td, th {{ background: #f8fafc; font-weight: 800; position: sticky; top: 0; z-index: 1; }}
    .source-block {{ border: 1px solid #e2e8f0; border-radius: 14px; margin: 12px 0; overflow: hidden; background: #fff; }}
    .source-block-head {{ display: flex; justify-content: space-between; gap: 12px; flex-wrap: wrap; background: #f8fafc; border-bottom: 1px solid #e2e8f0; padding: 10px 12px; color: #475569; font-size: 13px; font-weight: 700; }}
    .source-block-body {{ padding: 12px; line-height: 1.75; }}
    .source-block-body.text {{ white-space: pre-wrap; }}
    .source-block-body .table-scroll {{ max-height: none; }}
    .excerpt table {{ width: 100%; }}
    .excerpt td:first-child {{ width: 88px; color: #64748b; font-variant-numeric: tabular-nums; }}
    .excerpt code {{ white-space: pre-wrap; word-break: break-word; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12px; }}
    .excerpt tr.focus td {{ background: #fff7ed; }}
    .pdf-page-frame {{ overflow: auto; background: #0f172a; border-radius: 14px; padding: 12px; text-align: center; touch-action: pan-x pan-y; }}
    .pdf-page-image {{ display: block; width: 100%; max-width: 980px; height: auto; margin: 0 auto; border-radius: 8px; background: #fff; box-shadow: 0 14px 34px rgba(15,23,42,.24); }}
    @media (max-width: 640px) {{
      .wrap {{ padding: 14px 10px 28px; }}
      .header, .panel {{ border-radius: 14px; padding: 14px; }}
      .btn {{ min-height: 44px; }}
      .pdf-page-frame {{ margin-inline: -4px; padding: 8px; }}
    }}
  </style>
</head>
<body>
  <main class="wrap">{body}</main>
  <script>
    (() => {{
      const tracePathRe = /^\\/api\\/(?:pdf_page|source)\\//;
      const privateHostRe = /^(localhost|127\\.0\\.0\\.1|192\\.168\\.|10\\.|172\\.(1[6-9]|2\\d|3[01])\\.)/;
      const isKnownPublicTraceHost = (url) => url.hostname === 'arthurmao.synology.me';
      document.querySelectorAll('a[href]').forEach((link) => {{
        try {{
          const url = new URL(link.getAttribute('href'), window.location.href);
          if (!tracePathRe.test(url.pathname)) return;
          if (!privateHostRe.test(url.hostname) && !isKnownPublicTraceHost(url)) return;
          link.href = `${{window.location.origin}}${{url.pathname}}${{url.search}}${{url.hash}}`;
        }} catch (error) {{
          // Keep the original href when the browser cannot parse it.
        }}
      }});
    }})();
  </script>
</body>
</html>"""


def _html_page(data: dict[str, Any], *, task_id: str, table_index: int, source_token: str | None = None) -> str:
    table = data.get("table") or {}
    pdf_page = table.get("pdf_page_number") or (data.get("pdf_page_image") or {}).get("page_number")
    line = table.get("line") or table.get("markdown_line")
    heading = table.get("heading") or table.get("preview") or "财报表格来源"
    filename = data.get("filename") or ""
    table_html = data.get("table_html") or table.get("table_html") or "<p>未返回表格 HTML。</p>"
    table_html = _clean_table_html(str(table_html))
    excerpt = data.get("markdown_excerpt") or []
    printed_page = (
        table.get("printed_page_number")
        or (data.get("pdf_page_image") or {}).get("printed_page_number")
        or _printed_page_number(data.get("page_content"))
    )

    page_link = _source_url(f"/api/pdf_page/{task_id}/{pdf_page}", source_token) if pdf_page else ""
    source_page_link = _source_url(f"/api/source/{task_id}/page/{pdf_page}", source_token) if pdf_page else ""
    json_link = _source_url(f"/api/source/{task_id}/table/{table_index}?format=json", source_token)

    def esc(value: Any) -> str:
        return html.escape("" if value is None else str(value))

    excerpt_rows = []
    for item in excerpt:
        cls = "focus" if item.get("focus") else ""
        excerpt_rows.append(
            f"<tr class='{cls}'><td>{esc(item.get('line'))}</td><td><code>{esc(item.get('text'))}</code></td></tr>"
        )

    actions = [f"<a class='btn secondary' href='{json_link}' target='_blank'>查看 JSON</a>"]
    if page_link:
        pdf_label = f"打开 PDF 定位页 {esc(pdf_page)}"
        if printed_page and str(printed_page) != str(pdf_page):
            pdf_label += f" / 印刷页 {esc(printed_page)}"
        actions.insert(0, f"<a class='btn' href='{page_link}' target='_blank'>{pdf_label}</a>")
        actions.insert(1, f"<a class='btn secondary' href='{source_page_link}' target='_blank'>查看整页来源</a>")

    meta_items = [
        ("task_id", task_id),
        ("table_index", table_index),
        ("PDF/API 页序号", pdf_page or "未返回"),
        ("页面印刷页码", printed_page or "未返回"),
        ("md_line", line or "未返回"),
        ("表格类型", table.get("table_type") or "未返回"),
        ("置信度", table.get("source_confidence") or "未返回"),
    ]
    meta_html = "".join(f"<div><span>{esc(label)}</span><strong>{esc(value)}</strong></div>" for label, value in meta_items)

    body = f"""
    <section class="header">
      <h1>{esc(heading)}</h1>
      <div class="sub">{esc(filename)}</div>
      <div class="actions">{"".join(actions)}</div>
      <div class="meta">{meta_html}</div>
    </section>
    <section class="panel">
      <h2>表格内容</h2>
      <div class="table-scroll">{table_html}</div>
    </section>
    <section class="panel excerpt">
      <h2>Markdown 附近原文</h2>
      <table><tbody>{"".join(excerpt_rows) or "<tr><td colspan='2'>未返回附近原文。</td></tr>"}</tbody></table>
    </section>"""
    return _html_shell(title=f"表格来源 {table_index}", body=body)


def _infer_total_pages(data: dict[str, Any]) -> int | None:
    for key in ("page_count", "total_pages", "pdf_page_count"):
        value = data.get(key)
        if isinstance(value, int) and value > 0:
            return value
    for block in data.get("blocks") or []:
        text = str(block.get("text") or "")
        match = re.search(r"\b\d+\s*/\s*(\d+)\b", text)
        if match:
            total = int(match.group(1))
            if total > 0:
                return total
    return None


def _printed_page_number(data: dict[str, Any] | None) -> str | None:
    if not isinstance(data, dict):
        return None
    for key in ("printed_page_number", "printed_page"):
        value = data.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    for block in data.get("blocks") or []:
        if not isinstance(block, dict) or block.get("type") != "page_number":
            continue
        value = str(block.get("text") or "").strip()
        if value:
            return value
    return None


def _page_nav_html(task_id: str, page_number: int, *, mode: str, total_pages: int | None = None, source_token: str | None = None) -> str:
    def page_url(target_page: int) -> str:
        if mode == "source":
            return _source_url(f"/api/source/{task_id}/page/{target_page}", source_token)
        return _source_url(f"/api/pdf_page/{task_id}/{target_page}", source_token)

    def nav_button(label: str, target_page: int | None) -> str:
        if target_page is None:
            return f"<span class='btn secondary disabled' aria-disabled='true'>{html.escape(label)}</span>"
        return f"<a class='btn secondary' href='{page_url(target_page)}'>{html.escape(label)}</a>"

    prev_page = page_number - 1 if page_number > 1 else None
    next_page = page_number + 1 if total_pages is None or page_number < total_pages else None
    state = f"PDF/API {page_number} / {total_pages}" if total_pages else f"PDF/API 第 {page_number} 页"
    return (
        "<div class='actions page-nav' aria-label='页面翻页'>"
        f"{nav_button('上一页', prev_page)}"
        f"<span class='page-indicator'>{html.escape(state)}</span>"
        f"{nav_button('下一页', next_page)}"
        "</div>"
    )


def _source_page_html(data: dict[str, Any], *, task_id: str, page_number: int, source_token: str | None = None) -> str:
    def esc(value: Any) -> str:
        return html.escape("" if value is None else str(value))

    blocks = data.get("blocks") or []
    page_tables = data.get("page_tables") or []
    json_link = _source_url(f"/api/source/{task_id}/page/{page_number}?format=json", source_token)
    pdf_link = _source_url(f"/api/pdf_page/{task_id}/{page_number}", source_token)
    total_pages = _infer_total_pages(data)
    printed_page = _printed_page_number(data)
    page_nav = _page_nav_html(task_id, page_number, mode="source", total_pages=total_pages, source_token=source_token)

    meta_items = [
        ("task_id", task_id),
        ("PDF/API 页序号", data.get("page_number") or page_number),
        ("页面印刷页码", printed_page or "未返回"),
        ("page_index", data.get("page_index") if data.get("page_index") is not None else "未返回"),
        ("内容块数", data.get("block_count") or len(blocks)),
        ("页内表格数", data.get("table_count") or len(page_tables)),
    ]
    meta_html = "".join(f"<div><span>{esc(label)}</span><strong>{esc(value)}</strong></div>" for label, value in meta_items)

    table_links = []
    for item in page_tables:
        idx = item.get("table_index")
        if idx is None:
            continue
        label = item.get("heading") or f"表格 {idx}"
        table_links.append(
            f"<a class='btn secondary' href='{_source_url(f'/api/source/{task_id}/table/{esc(idx)}', source_token)}' target='_blank'>查看可读表格 {esc(idx)} - {esc(label)}</a>"
        )

    rendered_blocks = []
    for index, block in enumerate(blocks, start=1):
        block_type = block.get("type") or "unknown"
        bbox = block.get("bbox")
        bbox_text = f"bbox={bbox}" if bbox else ""
        if block_type == "table":
            table_index = block.get("table_index")
            source_table_index = block.get("source_table_index")
            line = block.get("line")
            table_html = _clean_table_html(str(block.get("table_html") or "<p>未返回表格 HTML。</p>"))
            table_action = ""
            if table_index is not None:
                table_label = f"打开可读表格 {esc(table_index)}"
                if line:
                    table_label += f" / MD 行 {esc(line)}"
                table_action = f"<a class='btn secondary' href='{_source_url(f'/api/source/{task_id}/table/{esc(table_index)}', source_token)}' target='_blank'>{table_label}</a>"
            if source_table_index is not None and source_table_index != table_index:
                bbox_text = f"{bbox_text}; source_table_index={source_table_index}" if bbox_text else f"source_table_index={source_table_index}"
            body = f"<div class='table-scroll'>{table_html}</div><div class='actions'>{table_action}</div>"
        else:
            text = block.get("text") or block.get("heading") or block.get("preview") or ""
            body = f"<div class='source-block-body text'>{esc(text)}</div>"
        rendered_blocks.append(
            f"<section class='source-block'><div class='source-block-head'><span>#{index} {esc(block_type)}</span><span>{esc(bbox_text)}</span></div>{body}</section>"
        )

    body = f"""
    <section class="header">
      <h1>PDF/API 第 {esc(data.get("page_number") or page_number)} 页来源</h1>
      <div class="sub">该页面展示解析后的文本块和表格块；若页面印刷页码不同，以按钮和链接中的 PDF/API 页序号定位原始页面。</div>
      {page_nav}
      <div class="actions">
        <a class="btn" href="{pdf_link}" target="_blank">打开 PDF/API 第 {esc(page_number)} 页</a>
        <a class="btn secondary" href="{json_link}" target="_blank">查看 JSON</a>
        {"".join(table_links)}
      </div>
      <div class="meta">{meta_html}</div>
    </section>
    <section class="panel">
      <h2>页面解析内容</h2>
      {"".join(rendered_blocks) or "<p>未返回页面内容。</p>"}
    </section>"""
    return _html_shell(title=f"PDF/API 第 {page_number} 页来源", body=body)


def _pdf_page_view_html(
    *,
    task_id: str,
    page_number: int,
    total_pages: int | None = None,
    printed_page_number: str | None = None,
    source_token: str | None = None,
) -> str:
    def esc(value: Any) -> str:
        return html.escape("" if value is None else str(value))

    raw_link = _source_url(f"/api/pdf_page/{task_id}/{page_number}?format=image", source_token)
    source_page_link = _source_url(f"/api/source/{task_id}/page/{page_number}", source_token)
    page_nav = _page_nav_html(task_id, page_number, mode="pdf", total_pages=total_pages, source_token=source_token)

    meta_items = [
        ("task_id", task_id),
        ("PDF/API 页序号", page_number),
        ("页面印刷页码", printed_page_number or "未返回"),
        ("查看方式", "页面查看模式"),
    ]
    meta_html = "".join(f"<div><span>{esc(label)}</span><strong>{esc(value)}</strong></div>" for label, value in meta_items)

    body = f"""
    <section class="header">
      <h1>PDF/API 第 {esc(page_number)} 页</h1>
      <div class="sub">下方直接展示原始 PDF 页面；页面内印刷页码可能与 PDF/API 页序号不同。</div>
      {page_nav}
      <div class="actions">
        <a class="btn" href="{raw_link}" target="_blank" rel="noopener noreferrer">打开原图</a>
        <a class="btn secondary" href="{source_page_link}" target="_blank" rel="noopener noreferrer">查看页来源</a>
      </div>
      <div class="meta">{meta_html}</div>
    </section>
    <section class="panel">
      <div class="pdf-page-frame">
        <img class="pdf-page-image" src="{raw_link}" alt="PDF/API 第 {esc(page_number)} 页" />
      </div>
    </section>"""
    return _html_shell(title=f"PDF/API 第 {page_number} 页", body=body)


@router.get("/source_access/{kind}/{task_id}/{identifier}")
async def get_source_open_url(
    request: Request,
    kind: str,
    task_id: str,
    identifier: int,
    credentials: HTTPAuthorizationCredentials | None = Depends(optional_security),
    session: Session = Depends(get_session),
):
    source_token = _authorize_task_access(
        request=request,
        task_id=task_id,
        session=session,
        credentials=credentials,
    )
    path = _resolve_source_open_path(kind, task_id, identifier)
    return {
        "url": _source_url(path, source_token),
        "expires_in": SOURCE_ACCESS_TOKEN_TTL_SECONDS,
    }


@router.get("/source/{task_id}/table/{table_index}")
@router.head("/source/{task_id}/table/{table_index}", include_in_schema=False)
async def get_source_table(
    request: Request,
    task_id: str,
    table_index: int,
    credentials: HTTPAuthorizationCredentials | None = Depends(optional_security),
    session: Session = Depends(get_session),
):
    source_token = _authorize_task_access(
        request=request,
        task_id=task_id,
        session=session,
        credentials=credentials,
    )
    if request.method == "HEAD" or not _wants_html(request):
        return await _proxy_pdf2md(request, f"/api/source/{task_id}/table/{table_index}")

    upstream = await _request_pdf2md(request, f"/api/source/{task_id}/table/{table_index}")
    if upstream.status_code >= 400:
        return Response(content=upstream.content, status_code=upstream.status_code, media_type=_content_type(upstream.headers))
    try:
        data = upstream.json()
    except ValueError:
        return Response(content=upstream.content, status_code=upstream.status_code, media_type=_content_type(upstream.headers))
    return Response(
        content=_html_page(data, task_id=task_id, table_index=table_index, source_token=source_token),
        media_type="text/html; charset=utf-8",
    )


@router.get("/source/{task_id}/page/{page_number}")
@router.head("/source/{task_id}/page/{page_number}", include_in_schema=False)
async def get_source_page(
    request: Request,
    task_id: str,
    page_number: int,
    credentials: HTTPAuthorizationCredentials | None = Depends(optional_security),
    session: Session = Depends(get_session),
):
    source_token = _authorize_task_access(
        request=request,
        task_id=task_id,
        session=session,
        credentials=credentials,
    )
    if request.method == "HEAD" or not _wants_html(request):
        return await _proxy_pdf2md(request, f"/api/source/{task_id}/page/{page_number}")

    upstream = await _request_pdf2md(request, f"/api/source/{task_id}/page/{page_number}")
    if upstream.status_code >= 400:
        return Response(content=upstream.content, status_code=upstream.status_code, media_type=_content_type(upstream.headers))
    try:
        data = upstream.json()
    except ValueError:
        return Response(content=upstream.content, status_code=upstream.status_code, media_type=_content_type(upstream.headers))
    return Response(
        content=_source_page_html(data, task_id=task_id, page_number=page_number, source_token=source_token),
        media_type="text/html; charset=utf-8",
    )


@router.get("/pdf_page/{task_id}/{page_number}")
@router.head("/pdf_page/{task_id}/{page_number}", include_in_schema=False)
async def get_pdf_page(
    request: Request,
    task_id: str,
    page_number: int,
    credentials: HTTPAuthorizationCredentials | None = Depends(optional_security),
    session: Session = Depends(get_session),
):
    source_token = _authorize_task_access(
        request=request,
        task_id=task_id,
        session=session,
        credentials=credentials,
    )
    raw_format = (request.query_params.get("format") or "").lower()
    if (
        request.method != "HEAD"
        and raw_format not in {"image", "raw", "png"}
        and _wants_html(request)
    ):
        source_data = await _source_page_data(task_id, page_number)
        total_pages = _infer_total_pages(source_data) if source_data else None
        printed_page = _printed_page_number(source_data)
        return Response(
            content=_pdf_page_view_html(
                task_id=task_id,
                page_number=page_number,
                total_pages=total_pages,
                printed_page_number=printed_page,
                source_token=source_token,
            ),
            media_type="text/html; charset=utf-8",
        )
    return await _proxy_pdf2md(request, f"/api/pdf_page/{task_id}/{page_number}")


@router.post("/source/{task_id}/table/{table_index}/correction")
async def submit_source_table_correction(
    request: Request,
    task_id: str,
    table_index: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if not _user_has_task_access(session, current_user, task_id):
        raise HTTPException(status_code=403, detail="PDF task does not belong to current user")
    body = await request.json()
    return await _proxy_pdf2md(
        request,
        f"/api/source/{task_id}/table/{table_index}/correction",
        method="POST",
        json_body=body,
    )
