#!/usr/bin/env python3
"""Repair SIQ report citations using local wiki/pdf2md trace data.

This is a deterministic post-processing step for SIQ_analysis. It fills
missing PDF page links when a citation already has task_id + table_index, and
it rewrites Markdown/JSON/HTML artifacts consistently.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


SHARED_SCRIPT_DIR = Path("/home/maoyd/.hermes/profiles/shared/scripts")
if str(SHARED_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SHARED_SCRIPT_DIR))

from local_citations import find_company_dir_from_text, resolve_table_refs  # type: ignore  # noqa: E402


TASK_RE = re.compile(r"\btask_id=([0-9a-fA-F-]{32,36})\b")
TABLE_RE = re.compile(r"\btable_index=([0-9]+)\b")
PDF_MISSING_RE = re.compile(r"\bpdf_page(?:_number)?=(?:未返回|None|null|N/A|unknown)(?:\([^)]*\))?", re.I)
PDF_VALUE_RE = re.compile(r"\bpdf_page(?:_number)?=([0-9]+)\b")
MD_LINE_RE = re.compile(r"\bmd_line=([0-9]+|未返回)\b")
MARKDOWN_LINK_RE = re.compile(r"\[([^\[\]]+?)\]\(([^()\s]+?)\)")
MARKDOWN_LINK_WITH_PREFIX_RE = re.compile(r"([，,]\s*)?(\[([^\[\]]+?)\]\(((?:https?://[^/\s)]+)?/api/(?:pdf_page|source)/[^()\s]+?)\))")
PUBLIC_ORIGIN = os.environ.get("SIQ_PUBLIC_ORIGIN", "https://arthurmao.synology.me:8276").rstrip("/")


def public_api_url(path: str) -> str:
    if path.startswith(("http://", "https://")):
        return path
    if path.startswith("/"):
        return f"{PUBLIC_ORIGIN}{path}"
    return path


def is_positive_int_token(value: Any) -> bool:
    text = str(value).strip()
    return bool(re.fullmatch(r"\d+", text)) and int(text) > 0


def is_nonnegative_int_token(value: Any) -> bool:
    text = str(value).strip()
    return bool(re.fullmatch(r"\d+", text))


def invalid_api_url(url: str) -> bool:
    path = urlsplit(url).path if url.startswith(("http://", "https://")) else url
    pdf_match = re.search(r"/api/pdf_page/[^/]+/([^/?#]+)", path)
    if pdf_match:
        return not is_positive_int_token(pdf_match.group(1))
    page_match = re.search(r"/api/source/[^/]+/page/([^/?#]+)", path)
    if page_match:
        return not is_positive_int_token(page_match.group(1))
    table_match = re.search(r"/api/source/[^/]+/table/([^/?#]+)", path)
    if table_match:
        return not is_nonnegative_int_token(table_match.group(1))
    return False


def normalize_subheading_numbers(text: str) -> tuple[str, int]:
    """Keep H3 numbering aligned with the fixed 14 H2 report sections."""
    changed = 0
    section_index = 0
    sub_index = 0
    output: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            section_index += 1
            sub_index = 0
            output.append(line)
            continue
        match = re.match(r"^(###\s+)(\d+)\.(\d+)\s+(.+)$", line)
        if match and section_index:
            sub_index += 1
            replacement = f"{match.group(1)}{section_index}.{sub_index} {match.group(4)}"
            if replacement != line:
                changed += 1
            output.append(replacement)
            continue
        output.append(line)
    trailing = "\n" if text.endswith("\n") else ""
    return "\n".join(output) + trailing, changed


def dedupe_api_markdown_links(line: str) -> str:
    seen: set[str] = set()

    def repl(match: re.Match[str]) -> str:
        prefix = match.group(1) or ""
        link = match.group(2)
        url = match.group(4)
        if invalid_api_url(url):
            return ""
        if url in seen:
            return ""
        seen.add(url)
        return prefix + link

    repaired = MARKDOWN_LINK_WITH_PREFIX_RE.sub(repl, line)
    repaired = re.sub(r"([，,]\s*){2,}", "，", repaired)
    repaired = repaired.replace("，, ", "，").replace(",，", "，")
    return repaired


def report_paths(prefix: Path) -> dict[str, Path]:
    return {
        "md": prefix.parent / f"{prefix.name}.md",
        "json": prefix.parent / f"{prefix.name}.json",
        "html": prefix.parent / f"{prefix.name}.html",
    }


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def infer_company_context(prefix: Path, texts: list[str], company: str | None) -> str:
    if company:
        return company
    joined = "\n".join([str(prefix), *texts[:2]])
    company_dir = find_company_dir_from_text(joined)
    return company_dir.name if company_dir else joined


def table_ref(company_context: str, task_id: str, table_index: str) -> dict[str, Any] | None:
    refs = resolve_table_refs(find_company_dir_from_text(company_context) or Path(), table_index, task_id)
    if refs:
        return refs[0]
    return None


def link_set(task_id: str, page: int | str | None, table_index: int | str | None) -> list[str]:
    links: list[str] = []
    if is_positive_int_token(page):
        links.append(f"[打开PDF页]({public_api_url(f'/api/pdf_page/{task_id}/{page}')})")
        links.append(f"[查看页来源]({public_api_url(f'/api/source/{task_id}/page/{page}')})")
    if is_nonnegative_int_token(table_index):
        links.append(f"[查看表格]({public_api_url(f'/api/source/{task_id}/table/{table_index}')})")
    return links


def ensure_links(line: str, task_id: str, page: int | str | None, table_index: int | str | None) -> str:
    additions = [item for item in link_set(task_id, page, table_index) if item not in line]
    if additions:
        separator = "，" if "，" in line or "," in line else " "
        line = line.rstrip(" ，,") + separator + "，".join(additions)
    return line


def repair_citation_line(line: str, company_context: str, unresolved: list[dict[str, Any]] | None = None) -> tuple[str, bool]:
    task_match = TASK_RE.search(line)
    table_match = TABLE_RE.search(line)
    if not task_match or not table_match:
        # Lines without a task_id+table_index pair are not repairable here, but
        # we still surface them as unresolved when they look like citations.
        if unresolved is not None and ("source_type=" in line or "pdf_page" in line) and PDF_MISSING_RE.search(line):
            unresolved.append({
                "reason": "missing_task_or_table",
                "task_id": task_match.group(1) if task_match else None,
                "table_index": table_match.group(1) if table_match else None,
                "snippet": line.strip()[:240],
            })
        return line, False

    task_id = task_match.group(1)
    table_index = table_match.group(1)
    page_match = PDF_VALUE_RE.search(line)
    page: int | str | None = int(page_match.group(1)) if page_match else None
    md_line: int | str | None = None

    if page is None or PDF_MISSING_RE.search(line):
        ref = table_ref(company_context, task_id, table_index)
        if ref and ref.get("pdf_page"):
            page = ref.get("pdf_page")
            md_line = ref.get("md_line")
            if PDF_MISSING_RE.search(line):
                line = PDF_MISSING_RE.sub(f"pdf_page={page}", line, count=1)
            elif "pdf_page=" not in line and "pdf_page_number=" not in line:
                line = line.rstrip(" ，,") + f"，pdf_page={page}"
        else:
            # Could not resolve PDF page from local wiki; record for review_queue.
            if unresolved is not None:
                unresolved.append({
                    "reason": "pdf_page_unresolved",
                    "task_id": task_id,
                    "table_index": table_index,
                    "snippet": line.strip()[:240],
                })

    if md_line and MD_LINE_RE.search(line):
        line = MD_LINE_RE.sub(f"md_line={md_line}", line, count=1)
    elif md_line and "md_line=" not in line:
        line = line.rstrip(" ，,") + f"，md_line={md_line}"

    before = line
    if page is not None:
        line = ensure_links(line, task_id, page, table_index)
    return line, line != before or page is not None


def repair_markdown_table_row(line: str, company_context: str) -> tuple[str, bool]:
    if not line.startswith("|") or "table_index" in line:
        return line, False
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    uuid_index = next((idx for idx, cell in enumerate(cells) if re.fullmatch(r"[0-9a-fA-F-]{32,36}", cell)), None)
    if uuid_index is None or uuid_index + 2 >= len(cells):
        return line, False
    task_id = cells[uuid_index]
    page_cell_index = uuid_index + 1
    table_cell_index = uuid_index + 2
    table_index = re.sub(r"\D", "", cells[table_cell_index])
    if not table_index:
        return line, False
    page = cells[page_cell_index]
    changed = False
    if not page.isdigit():
        ref = table_ref(company_context, task_id, table_index)
        if ref and ref.get("pdf_page"):
            page = str(ref["pdf_page"])
            cells[page_cell_index] = page
            changed = True
    if page.isdigit() and len(cells) >= table_cell_index + 4:
        pdf_cell = f"[打开]({public_api_url(f'/api/pdf_page/{task_id}/{page}')})"
        source_cell = f"[来源]({public_api_url(f'/api/source/{task_id}/page/{page}')})"
        table_cell = f"[表格]({public_api_url(f'/api/source/{task_id}/table/{table_index}')})"
        for offset, value in [(1, pdf_cell), (2, source_cell), (3, table_cell)]:
            target = table_cell_index + offset
            if target >= len(cells):
                continue
            wants_pdf = offset == 1 and "/api/pdf_page/" not in cells[target]
            wants_source = offset == 2 and "/api/source/" not in cells[target]
            wants_table = offset == 3 and f"/api/source/{task_id}/table/{table_index}" not in cells[target]
            if cells[target] in {"—", "-", "", "未返回"} or wants_pdf or wants_source or wants_table:
                cells[target] = value
                changed = True
    if not changed:
        return line, False
    return "| " + " | ".join(cells) + " |", True


def repair_text(text: str, company_context: str, unresolved: list[dict[str, Any]] | None = None) -> tuple[str, int]:
    changed_count = 0
    text, heading_changes = normalize_subheading_numbers(text)
    changed_count += heading_changes
    output: list[str] = []
    for line in text.splitlines():
        repaired, changed = repair_citation_line(line, company_context, unresolved)
        if not changed:
            repaired, changed = repair_markdown_table_row(line, company_context)
        deduped = dedupe_api_markdown_links(repaired)
        if deduped != repaired:
            repaired = deduped
            changed = True
        if changed and repaired != line:
            changed_count += 1
        output.append(repaired)
    trailing = "\n" if text.endswith("\n") else ""
    return "\n".join(output) + trailing, changed_count


def repair_json_obj(obj: Any, company_context: str, unresolved: list[dict[str, Any]] | None = None) -> tuple[Any, int]:
    changes = 0
    if isinstance(obj, dict):
        updated = {}
        for key, value in obj.items():
            new_value, nested_changes = repair_json_obj(value, company_context, unresolved)
            updated[key] = new_value
            changes += nested_changes
        return updated, changes
    if isinstance(obj, list):
        updated_items = []
        for item in obj:
            new_item, nested_changes = repair_json_obj(item, company_context, unresolved)
            updated_items.append(new_item)
            changes += nested_changes
        return updated_items, changes
    if isinstance(obj, str):
        repaired, nested_changes = repair_text(obj, company_context, unresolved)
        return repaired, nested_changes
    return obj, changes


def inline_markdown(text: str) -> str:
    text = html.escape(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    def link_repl(match: re.Match[str]) -> str:
        label = match.group(1)
        url = match.group(2)
        if invalid_api_url(url):
            return html.escape(label)
        attrs = ' target="_blank" rel="noopener noreferrer"' if "/api/pdf_page/" in url or "/api/source/" in url else ""
        return f'<a href="{url}"{attrs}>{label}</a>'

    return MARKDOWN_LINK_RE.sub(link_repl, text)


def markdown_to_html(markdown: str) -> str:
    parts: list[str] = []
    in_section = False
    in_table = False
    in_ul = False
    in_quote = False
    section_index = 0

    def close_table() -> None:
        nonlocal in_table
        if in_table:
            parts.append("</tbody></table>")
            in_table = False

    def close_ul() -> None:
        nonlocal in_ul
        if in_ul:
            parts.append("</ul>")
            in_ul = False

    def close_quote() -> None:
        nonlocal in_quote
        if in_quote:
            parts.append("</blockquote>")
            in_quote = False

    def close_section() -> None:
        nonlocal in_section
        close_table()
        close_ul()
        close_quote()
        if in_section:
            parts.append("</section>")
            in_section = False

    for raw in markdown.splitlines():
        line = raw.rstrip()
        if not line.strip():
            close_table()
            close_ul()
            close_quote()
            continue
        if line.startswith("# "):
            close_section()
            parts.append(f"<h1>{inline_markdown(line[2:])}</h1>")
        elif line.startswith("## "):
            section_index += 1
            close_section()
            parts.append(f'<section class="section" id="section-{section_index:02d}"><h2>{inline_markdown(line[3:])}</h2>')
            in_section = True
        elif line.startswith("### "):
            close_table()
            close_ul()
            close_quote()
            parts.append(f"<h3>{inline_markdown(line[4:])}</h3>")
        elif line.startswith(">"):
            close_table()
            close_ul()
            if not in_quote:
                parts.append("<blockquote>")
                in_quote = True
            parts.append(f"<p>{inline_markdown(line.lstrip('> ').strip())}</p>")
        elif line.startswith("|") and line.endswith("|"):
            close_ul()
            close_quote()
            cells = [inline_markdown(cell.strip()) for cell in line.strip("|").split("|")]
            if len(cells) >= 2 and all(set(cell.replace(":", "").replace("-", "").strip()) == set() for cell in cells):
                continue
            if not in_table:
                parts.append("<table><tbody>")
                in_table = True
            tag = "th" if any(key in raw for key in ("指标", "来源", "task_id", "pdf_page")) else "td"
            parts.append("<tr>" + "".join(f"<{tag}>{cell}</{tag}>" for cell in cells) + "</tr>")
        elif line.startswith("- "):
            close_table()
            close_quote()
            if not in_ul:
                parts.append("<ul>")
                in_ul = True
            parts.append(f"<li>{inline_markdown(line[2:])}</li>")
        else:
            close_table()
            close_ul()
            close_quote()
            parts.append("<hr>" if line == "---" else f"<p>{inline_markdown(line)}</p>")
    close_section()
    return "\n".join(parts)


def repair_html_from_markdown(existing_html: str, markdown: str) -> str:
    content = markdown_to_html(markdown)
    content_match = re.search(
        r'(<div class="content">\s*)([\s\S]*?)(\n</div>\s*</div>\s*</main>)',
        existing_html,
    )
    if content_match:
        return (
            existing_html[: content_match.start(2)]
            + content
            + existing_html[content_match.end(2):]
        )

    chart_match = re.search(r'<section class="(?:charts|dashboard)"[\s\S]*?</section>\s*', existing_html)
    charts = chart_match.group(0) if chart_match else ""
    match_start = re.search(r"<main\b[^>]*>", existing_html)
    match_end = re.search(r"</main>", existing_html)
    if not match_start or not match_end:
        return existing_html
    return existing_html[: match_start.end()] + "\n" + charts + content + "\n" + existing_html[match_end.start():]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prefix", required=True, type=Path, help="Report prefix without suffix")
    parser.add_argument("--company", help="Optional company_id/stock code/name")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--write-json", type=Path)
    args = parser.parse_args()

    paths = report_paths(args.prefix)
    md_text = read_text(paths["md"]) if paths["md"].exists() else ""
    json_text = read_text(paths["json"]) if paths["json"].exists() else ""
    html_text = read_text(paths["html"]) if paths["html"].exists() else ""
    company_context = infer_company_context(args.prefix, [md_text, json_text, html_text], args.company)

    result: dict[str, Any] = {
        "ok": True,
        "prefix": str(args.prefix),
        "company_context": company_context,
        "changes": {},
        "unresolved": [],
    }

    unresolved: list[dict[str, Any]] = []

    repaired_md, md_changes = repair_text(md_text, company_context, unresolved) if md_text else ("", 0)
    result["changes"]["md"] = md_changes
    if md_changes and not args.dry_run:
        write_text(paths["md"], repaired_md)

    json_changes = 0
    if json_text:
        data = json.loads(json_text)
        repaired_data, json_changes = repair_json_obj(data, company_context, unresolved)
        if json_changes and not args.dry_run:
            write_text(paths["json"], json.dumps(repaired_data, ensure_ascii=False, indent=2) + "\n")
    result["changes"]["json"] = json_changes

    html_changes = 0
    if html_text and repaired_md:
        repaired_html = repair_html_from_markdown(html_text, repaired_md)
        if repaired_html != html_text:
            html_changes = 1
            if not args.dry_run:
                write_text(paths["html"], repaired_html)
    result["changes"]["html"] = html_changes
    result["changed"] = bool(md_changes or json_changes or html_changes)

    # De-duplicate unresolved entries by (task_id, table_index, reason).
    seen_keys: set[tuple[str, str, str]] = set()
    deduped_unresolved: list[dict[str, Any]] = []
    for item in unresolved:
        key = (str(item.get("task_id", "")), str(item.get("table_index", "")), str(item.get("reason", "")))
        if key in seen_keys:
            continue
        seen_keys.add(key)
        deduped_unresolved.append(item)
    result["unresolved"] = deduped_unresolved
    result["unresolved_count"] = len(deduped_unresolved)
    if deduped_unresolved:
        # Mark partial success so downstream tooling can react. The script still
        # exits 0 because the citation repair *step* completed; quality_gate is
        # responsible for converting unresolved citations into review_queue.
        result["warnings"] = [
            f"{len(deduped_unresolved)} citation(s) could not be resolved to a PDF page from local wiki"
        ]

    if args.write_json:
        args.write_json.parent.mkdir(parents=True, exist_ok=True)
        write_text(args.write_json, json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
