import re
import sys
from pathlib import Path

TRACKING_SCRIPT_DIR = Path("/home/maoyd/wiki/tracking/scripts")
if str(TRACKING_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(TRACKING_SCRIPT_DIR))

try:
    from local_citations import enrich_citation_line
except Exception:  # pragma: no cover - chat must still work if local wiki helpers are unavailable
    enrich_citation_line = None


TASK_ID_RE = re.compile(r"\btask_id=([0-9a-fA-F-]{32,36})\b")
PDF_PAGE_RE = re.compile(r"\bpdf_page(?:_number)?=([0-9]+(?:\s*[-,，]\s*[0-9]+)*)\b")
TABLE_INDEX_RE = re.compile(r"\btable_index=([0-9]+(?:\s*[,，]\s*[0-9]+)*)\b")
CITATION_LINE_RE = re.compile(r"^\s*(\[[0-9]+\]).*$")


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


def append_missing_pdf_source_links(text: str) -> str:
    """Append clickable PDF/source links for citation lines that only contain raw ids."""
    if not text:
        return text

    if enrich_citation_line:
        enriched_lines: list[str] = []
        changed = False
        for line in text.splitlines():
            if CITATION_LINE_RE.match(line):
                enriched = enrich_citation_line(line, text)
                changed = changed or enriched != line
                enriched_lines.append(enriched)
            else:
                enriched_lines.append(line)
        if changed:
            text = "\n".join(enriched_lines)

    if "/api/pdf_page/" in text and "## 引用来源" not in text:
        return text

    additions: list[str] = []
    for line in text.splitlines():
        citation = CITATION_LINE_RE.match(line)
        if not citation:
            continue

        task_match = TASK_ID_RE.search(line)
        page_match = PDF_PAGE_RE.search(line)
        if not task_match or not page_match:
            continue

        task_id = task_match.group(1)
        pages = _page_numbers(page_match.group(1))
        if not pages:
            continue

        links: list[str] = []
        for page in pages:
            page_url = f"/api/pdf_page/{task_id}/{page}"
            source_url = f"/api/source/{task_id}/page/{page}"
            if page_url not in line and page_url not in text:
                links.append(f"[打开PDF第{page}页]({page_url})")
            if source_url not in line and source_url not in text:
                links.append(f"[查看第{page}页来源]({source_url})")

        table_match = TABLE_INDEX_RE.search(line)
        if table_match:
            for table_index in _page_numbers(table_match.group(1)):
                table_url = f"/api/source/{task_id}/table/{table_index}"
                if table_url not in line and table_url not in text:
                    links.append(f"[查看可读表格{table_index}]({table_url})")

        if links:
            additions.append(f"{citation.group(1)} " + "，".join(links))

    if not additions:
        return text

    suffix = "\n\n## 可打开来源链接\n\n" + "\n".join(additions)
    return text.rstrip() + suffix
