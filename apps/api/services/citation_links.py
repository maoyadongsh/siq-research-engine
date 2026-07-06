import re
import sys
import os
import importlib
from pathlib import Path
from urllib.parse import urlsplit

from services.path_config import HERMES_SHARED_SCRIPTS_ROOT, WIKI_ROOT

SHARED_SCRIPT_DIR = HERMES_SHARED_SCRIPTS_ROOT
TRACKING_SCRIPT_DIR = WIKI_ROOT / "tracking" / "scripts"
for script_dir in (TRACKING_SCRIPT_DIR, SHARED_SCRIPT_DIR):
    script_path = str(script_dir)
    if script_path in sys.path:
        sys.path.remove(script_path)
    sys.path.insert(0, script_path)

_LOCAL_CITATIONS_MODULE = None
_LOCAL_CITATIONS_MTIME: float | None = None


def _local_citations_mtime() -> float | None:
    path = SHARED_SCRIPT_DIR / "local_citations.py"
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _get_enrich_citation_line():
    """Load/reload the shared resolver so long-running API processes pick up fixes."""
    global _LOCAL_CITATIONS_MODULE, _LOCAL_CITATIONS_MTIME
    try:
        mtime = _local_citations_mtime()
        if _LOCAL_CITATIONS_MODULE is None:
            _LOCAL_CITATIONS_MODULE = importlib.import_module("local_citations")
            _LOCAL_CITATIONS_MTIME = mtime
        elif mtime is not None and _LOCAL_CITATIONS_MTIME is not None and mtime > _LOCAL_CITATIONS_MTIME:
            _LOCAL_CITATIONS_MODULE = importlib.reload(_LOCAL_CITATIONS_MODULE)
            _LOCAL_CITATIONS_MTIME = mtime
        return getattr(_LOCAL_CITATIONS_MODULE, "enrich_citation_line", None)
    except Exception:  # pragma: no cover - chat must still work if local wiki helpers are unavailable
        return None


TASK_ID_RE = re.compile(r"\btask_id=([0-9a-fA-F-]{32,36})\b")
PDF_PAGE_RE = re.compile(r"\bpdf_page(?:_number)?=([0-9]+(?:\s*[-,，]\s*[0-9]+)*)\b")
PRINTED_PAGE_RE = re.compile(
    r"\bprinted_page(?:_number)?=([^,，。.;；\n]+"
    r"(?:\s*[,，]\s*(?!\s*(?:task_id|pdf_page(?:_number)?|printed_page(?:_number)?|table_index|md_line|source_type|file|metric|period|evidence_id|quote)=)"
    r"[^,，。.;；\n]+)*)"
)
TABLE_INDEX_RE = re.compile(r"\btable_index=([0-9]+(?:\s*[,，]\s*[0-9]+)*)\b")
CITATION_LINE_RE = re.compile(r"^\s*(\[[0-9]+\]).*$")
LOCAL_API_LINK_RE = re.compile(r"\]\((?:https?://(?:localhost|127\.0\.0\.1)(?::[0-9]+)?)?(/api/(?:pdf_page|source)/[^)\s]+)\)")
TRACE_LINK_RE = re.compile(r"[，,、\s]*\[[^\]]*(?:PDF|页来源|来源|表格|可读表格)[^\]]*\]\((?:https?://[^)\s]+|/api/(?:pdf_page|source)/[^)\s]+)\)")
BARE_TRACE_LINK_RE = re.compile(
    r"[，,、\s]*(?:打开PDF页|打开PDF定位页[0-9]*|查看页来源|查看定位页[0-9]*来源|查看表格|查看可读表格[0-9]*)"
    r"\((?:https?://[^)\s]+|/api/(?:pdf_page|source)/[^)\s]+)\)"
)
CITATION_HEADING_RE = re.compile(r"^\s*(?:#{1,4}\s+)?引用来源[:：]?\s*$")
OPEN_LINK_HEADING_RE = re.compile(r"^\s*(?:#{1,4}\s+)?可打开来源链接[:：]?\s*$")


def _public_origin() -> str:
    return (os.environ.get("SIQ_PUBLIC_ORIGIN") or os.environ.get("SIQ_PUBLIC_ORIGIN", "https://arthurmao.synology.me:9391")).rstrip("/")


def _public_api_url(path: str) -> str:
    if not path:
        return path
    if path.startswith(("http://", "https://")):
        parsed = urlsplit(path)
        if parsed.hostname in {"localhost", "127.0.0.1"} and parsed.path.startswith("/api/"):
            suffix = parsed.path
            if parsed.query:
                suffix = f"{suffix}?{parsed.query}"
            return f"{_public_origin()}{suffix}"
        return path
    if path.startswith("/api/"):
        return f"{_public_origin()}{path}"
    return path


def _normalize_api_links(text: str) -> str:
    return LOCAL_API_LINK_RE.sub(lambda match: f"]({_public_api_url(match.group(1))})", text)


def _strip_trace_links(line: str) -> str:
    cleaned = TRACE_LINK_RE.sub("", line)
    cleaned = BARE_TRACE_LINK_RE.sub("", cleaned)
    return re.sub(r"[，,、\s]+$", "", cleaned).rstrip()


def _strip_bare_trace_links(line: str) -> str:
    cleaned = BARE_TRACE_LINK_RE.sub("", line)
    return re.sub(r"[，,、\s]+$", "", cleaned).rstrip()


def _drop_standalone_open_link_blocks(text: str) -> str:
    lines = text.splitlines()
    output: list[str] = []
    index = 0
    while index < len(lines):
        if OPEN_LINK_HEADING_RE.match(lines[index]):
            index += 1
            while index < len(lines):
                trimmed = lines[index].strip()
                if not trimmed:
                    index += 1
                    continue
                if CITATION_HEADING_RE.match(trimmed) or re.match(r"^\s*#{1,4}\s+", trimmed):
                    break
                if re.match(r"^\s*\[[0-9]+\]\s+", trimmed):
                    index += 1
                    continue
                break
            continue
        output.append(lines[index])
        index += 1
    return "\n".join(output)


def _page_numbers(value: str) -> list[int]:
    pages: list[int] = []
    for part in re.split(r"[,，]", value):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_text, end_text = [item.strip() for item in part.split("-", 1)]
            if start_text.isdigit() and end_text.isdigit():
                start, end = int(start_text), int(end_text)
                if 0 < start <= end and end - start <= 10:
                    pages.extend(range(start, end + 1))
                elif start > 0:
                    pages.append(start)
            continue
        if part.isdigit():
            pages.append(int(part))
    return list(dict.fromkeys(page for page in pages if page > 0))


def _printed_page_labels(value: str) -> list[str]:
    labels: list[str] = []
    for part in re.split(r"[,，]", value or ""):
        label = part.strip().strip("。.;；")
        if not label:
            continue
        if label in {"未返回", "N/A", "None", "null"}:
            labels.append("")
            continue
        labels.append(label)
    return labels


def _append_inline_links(line: str, links: list[str]) -> str:
    if not links:
        return line
    trailing = re.match(r"^(?P<body>.*?)(?P<tail>[。.;；])?$", line, flags=re.DOTALL)
    if not trailing:
        return f"{line}，" + "，".join(links)
    body = trailing.group("body")
    tail = trailing.group("tail") or ""
    return f"{body}，" + "，".join(links) + tail


def append_missing_pdf_source_links(text: str) -> str:
    """Normalize citation lines and append clickable PDF/source links inline."""
    if not text:
        return text

    text = _drop_standalone_open_link_blocks(text)

    enrich_citation_line = _get_enrich_citation_line()
    enriched_lines: list[str] = []
    changed = False
    for line in text.splitlines():
        if CITATION_LINE_RE.match(line):
            stripped = _strip_bare_trace_links(line)
            if enrich_citation_line:
                stripped = _strip_trace_links(stripped)
                try:
                    enriched = enrich_citation_line(stripped, text)
                except Exception:  # pragma: no cover - citation enrichment must not break chat replies
                    enriched = stripped
            else:
                enriched = stripped
            changed = changed or enriched != line
            enriched_lines.append(enriched)
        else:
            enriched_lines.append(line)
    if changed:
        text = "\n".join(enriched_lines)

    text = _normalize_api_links(text)

    output_lines: list[str] = []
    changed = False
    for line in text.splitlines():
        citation = CITATION_LINE_RE.match(line)
        if not citation:
            output_lines.append(line)
            continue

        task_match = TASK_ID_RE.search(line)
        page_match = PDF_PAGE_RE.search(line)
        if not task_match or not page_match:
            output_lines.append(line)
            continue

        task_id = task_match.group(1)
        pages = _page_numbers(page_match.group(1))
        if not pages:
            output_lines.append(line)
            continue

        links: list[str] = []
        printed_labels = _printed_page_labels(PRINTED_PAGE_RE.search(line).group(1)) if PRINTED_PAGE_RE.search(line) else []
        for page_pos, page in enumerate(pages):
            printed = printed_labels[page_pos] if page_pos < len(printed_labels) else ""
            suffix = f" / 印刷页{printed}" if printed and printed != str(page) else ""
            page_url = _public_api_url(f"/api/pdf_page/{task_id}/{page}?format=html")
            source_url = _public_api_url(f"/api/source/{task_id}/page/{page}?format=html")
            if page_url not in line:
                links.append(f"[打开PDF定位页{page}{suffix}]({page_url})")
            if source_url not in line:
                links.append(f"[查看定位页{page}来源{suffix}]({source_url})")

        table_match = TABLE_INDEX_RE.search(line)
        if table_match:
            for table_index in _page_numbers(table_match.group(1)):
                table_url = _public_api_url(f"/api/source/{task_id}/table/{table_index}?format=html")
                if table_url not in line:
                    links.append(f"[查看可读表格{table_index}]({table_url})")

        if links:
            line = _append_inline_links(line, links)
            changed = True
        output_lines.append(line)

    return "\n".join(output_lines) if changed else text
