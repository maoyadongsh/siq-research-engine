import html
import os
import re
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response


PDF2MD_API_BASE = os.environ.get("PDF2MD_API_BASE", "http://127.0.0.1:5000").rstrip("/")
PDF2MD_PROXY_TIMEOUT = float(os.environ.get("PDF2MD_PROXY_TIMEOUT", "60"))

router = APIRouter(tags=["source"])


def _content_type(headers: httpx.Headers) -> str:
    return headers.get("content-type") or "application/octet-stream"


async def _request_pdf2md(
    request: Request,
    upstream_path: str,
    *,
    method: str | None = None,
    json_body: Any | None = None,
) -> httpx.Response:
    request_method = method or request.method
    url = f"{PDF2MD_API_BASE}{upstream_path}"
    kwargs: dict[str, Any] = {"params": request.query_params}

    if json_body is not None:
        kwargs["json"] = json_body
    elif request_method in {"POST", "PUT", "PATCH"}:
        kwargs["content"] = await request.body()
        content_type = request.headers.get("content-type")
        if content_type:
            kwargs["headers"] = {"content-type": content_type}

    try:
        async with httpx.AsyncClient(timeout=PDF2MD_PROXY_TIMEOUT) as client:
            return await client.request(request_method, url, **kwargs)
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"pdf2md_web source service unavailable: {exc}",
        ) from exc


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
  </style>
</head>
<body>
  <main class="wrap">{body}</main>
</body>
</html>"""


def _html_page(data: dict[str, Any], *, task_id: str, table_index: int) -> str:
    table = data.get("table") or {}
    pdf_page = table.get("pdf_page_number") or (data.get("pdf_page_image") or {}).get("page_number")
    line = table.get("line") or table.get("markdown_line")
    heading = table.get("heading") or table.get("preview") or "财报表格来源"
    filename = data.get("filename") or ""
    table_html = data.get("table_html") or table.get("table_html") or "<p>未返回表格 HTML。</p>"
    table_html = _clean_table_html(str(table_html))
    excerpt = data.get("markdown_excerpt") or []

    page_link = f"/api/pdf_page/{task_id}/{pdf_page}" if pdf_page else ""
    source_page_link = f"/api/source/{task_id}/page/{pdf_page}" if pdf_page else ""
    json_link = f"/api/source/{task_id}/table/{table_index}?format=json"

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
        actions.insert(0, f"<a class='btn' href='{page_link}' target='_blank'>打开 PDF 第 {esc(pdf_page)} 页</a>")
        actions.insert(1, f"<a class='btn secondary' href='{source_page_link}' target='_blank'>查看整页来源</a>")

    meta_items = [
        ("task_id", task_id),
        ("table_index", table_index),
        ("PDF 页码", pdf_page or "未返回"),
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


def _source_page_html(data: dict[str, Any], *, task_id: str, page_number: int) -> str:
    def esc(value: Any) -> str:
        return html.escape("" if value is None else str(value))

    blocks = data.get("blocks") or []
    page_tables = data.get("page_tables") or []
    json_link = f"/api/source/{task_id}/page/{page_number}?format=json"
    pdf_link = f"/api/pdf_page/{task_id}/{page_number}"

    meta_items = [
        ("task_id", task_id),
        ("PDF 页码", data.get("page_number") or page_number),
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
            f"<a class='btn secondary' href='/api/source/{task_id}/table/{esc(idx)}' target='_blank'>查看可读表格 {esc(idx)} - {esc(label)}</a>"
        )

    rendered_blocks = []
    for index, block in enumerate(blocks, start=1):
        block_type = block.get("type") or "unknown"
        bbox = block.get("bbox")
        bbox_text = f"bbox={bbox}" if bbox else ""
        if block_type == "table":
            table_index = block.get("table_index")
            table_html = _clean_table_html(str(block.get("table_html") or "<p>未返回表格 HTML。</p>"))
            table_action = ""
            if table_index is not None:
                table_action = f"<a class='btn secondary' href='/api/source/{task_id}/table/{esc(table_index)}' target='_blank'>打开可读表格 {esc(table_index)}</a>"
            body = f"<div class='table-scroll'>{table_html}</div><div class='actions'>{table_action}</div>"
        else:
            text = block.get("text") or block.get("heading") or block.get("preview") or ""
            body = f"<div class='source-block-body text'>{esc(text)}</div>"
        rendered_blocks.append(
            f"<section class='source-block'><div class='source-block-head'><span>#{index} {esc(block_type)}</span><span>{esc(bbox_text)}</span></div>{body}</section>"
        )

    body = f"""
    <section class="header">
      <h1>PDF 第 {esc(data.get("page_number") or page_number)} 页来源</h1>
      <div class="sub">该页面展示解析后的文本块和表格块；原始 PDF 页可用按钮打开核对。</div>
      <div class="actions">
        <a class="btn" href="{pdf_link}" target="_blank">打开 PDF 第 {esc(page_number)} 页</a>
        <a class="btn secondary" href="{json_link}" target="_blank">查看 JSON</a>
        {"".join(table_links)}
      </div>
      <div class="meta">{meta_html}</div>
    </section>
    <section class="panel">
      <h2>页面解析内容</h2>
      {"".join(rendered_blocks) or "<p>未返回页面内容。</p>"}
    </section>"""
    return _html_shell(title=f"PDF 第 {page_number} 页来源", body=body)


@router.get("/source/{task_id}/table/{table_index}")
@router.head("/source/{task_id}/table/{table_index}", include_in_schema=False)
async def get_source_table(request: Request, task_id: str, table_index: int):
    if request.method == "HEAD" or not _wants_html(request):
        return await _proxy_pdf2md(request, f"/api/source/{task_id}/table/{table_index}")

    upstream = await _request_pdf2md(request, f"/api/source/{task_id}/table/{table_index}")
    if upstream.status_code >= 400:
        return Response(content=upstream.content, status_code=upstream.status_code, media_type=_content_type(upstream.headers))
    try:
        data = upstream.json()
    except ValueError:
        return Response(content=upstream.content, status_code=upstream.status_code, media_type=_content_type(upstream.headers))
    return Response(content=_html_page(data, task_id=task_id, table_index=table_index), media_type="text/html; charset=utf-8")


@router.get("/source/{task_id}/page/{page_number}")
@router.head("/source/{task_id}/page/{page_number}", include_in_schema=False)
async def get_source_page(request: Request, task_id: str, page_number: int):
    if request.method == "HEAD" or not _wants_html(request):
        return await _proxy_pdf2md(request, f"/api/source/{task_id}/page/{page_number}")

    upstream = await _request_pdf2md(request, f"/api/source/{task_id}/page/{page_number}")
    if upstream.status_code >= 400:
        return Response(content=upstream.content, status_code=upstream.status_code, media_type=_content_type(upstream.headers))
    try:
        data = upstream.json()
    except ValueError:
        return Response(content=upstream.content, status_code=upstream.status_code, media_type=_content_type(upstream.headers))
    return Response(content=_source_page_html(data, task_id=task_id, page_number=page_number), media_type="text/html; charset=utf-8")


@router.get("/pdf_page/{task_id}/{page_number}")
@router.head("/pdf_page/{task_id}/{page_number}", include_in_schema=False)
async def get_pdf_page(request: Request, task_id: str, page_number: int):
    return await _proxy_pdf2md(request, f"/api/pdf_page/{task_id}/{page_number}")


@router.post("/source/{task_id}/table/{table_index}/correction")
async def submit_source_table_correction(request: Request, task_id: str, table_index: int):
    body = await request.json()
    return await _proxy_pdf2md(
        request,
        f"/api/source/{task_id}/table/{table_index}/correction",
        method="POST",
        json_body=body,
    )
