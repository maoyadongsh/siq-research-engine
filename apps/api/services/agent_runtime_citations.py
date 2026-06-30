"""Citation and evidence helpers for the Hermes agent runtime."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from services.citation_links import append_missing_pdf_source_links

LATEX_INLINE_SYMBOLS: dict[str, str] = {
    r"\to": "→",
    r"\rightarrow": "→",
    r"\longrightarrow": "→",
    r"\leftarrow": "←",
    r"\longleftarrow": "←",
    r"\leftrightarrow": "↔",
    r"\longleftrightarrow": "↔",
    r"\uparrow": "↑",
    r"\downarrow": "↓",
    r"\Rightarrow": "⇒",
    r"\Leftarrow": "⇐",
    r"\Leftrightarrow": "⇔",
    r"\implies": "⇒",
    r"\le": "≤",
    r"\leq": "≤",
    r"\ge": "≥",
    r"\geq": "≥",
    r"\neq": "≠",
    r"\ne": "≠",
    r"\approx": "≈",
    r"\times": "×",
    r"\cdot": "·",
    r"\pm": "±",
    r"\%": "%",
}


def normalize_plain_inline_latex(content: str | None) -> str:
    if not content:
        return content or ""

    def replace_symbol(match: re.Match[str]) -> str:
        body = match.group(1).strip()
        return LATEX_INLINE_SYMBOLS.get(body, match.group(0))

    return re.sub(r"\$(\\[A-Za-z]+|\\%)\$", replace_symbol, content)


def normalize_evidence_trace_for_display(content: str | None) -> str:
    """Apply the single citation/link normalization path used by every agent."""
    if not content:
        return content or ""
    return append_missing_pdf_source_links(normalize_plain_inline_latex(content))


def _has_structured_evidence_trace(reply: str) -> bool:
    text = reply or ""
    if "source_type=" not in text:
        return False
    for line in text.splitlines():
        if "source_type=" not in line or "task_id=" not in line:
            continue
        if not re.search(r"\btask_id=[0-9a-fA-F-]{32,36}\b", line):
            continue
        has_page = re.search(r"\bpdf_page(?:_number)?=[0-9]+", line)
        has_table = re.search(r"\btable_index=[0-9]+", line)
        if has_page or has_table:
            return True
    return False


def _has_primary_data_evidence_trace(reply: str, *, markers: tuple[str, ...]) -> bool:
    text = reply or ""
    return any(marker in text for marker in markers) and _has_structured_evidence_trace(text)


def _source_locator_text(
    *,
    task_id: Any,
    pdf_page: Any,
    table_index: Any,
    md_line: Any,
    table_source_links: Callable[[Any, Any, Any], str],
) -> str:
    parts = [
        f"task_id={task_id or '未返回'}",
        f"pdf_page={pdf_page or '未返回'}",
        f"table_index={table_index if table_index not in (None, '') else '未返回'}",
        f"md_line={md_line or '未返回'}",
    ]
    links = table_source_links(task_id, pdf_page, table_index)
    return ", ".join(parts) + (f"，{links}" if links else "")


def _primary_data_source_ref(
    index: int,
    *,
    source_type: str,
    file: str,
    metric: str,
    period: Any,
    task_id: Any,
    pdf_page: Any,
    table_index: Any,
    md_line: Any,
    table_source_links: Callable[[Any, Any, Any], str],
) -> str:
    return (
        f"[D{index}] source_type={source_type}, file={file or '未返回'}, "
        f"metric={metric or '未返回'}, period={period or '未返回'}, "
        f"{_source_locator_text(task_id=task_id, pdf_page=pdf_page, table_index=table_index, md_line=md_line, table_source_links=table_source_links)}"
    )


def _append_unique_source_ref(
    refs: list[str],
    seen: set[tuple[Any, Any, Any, str, str]],
    *,
    source_type: str,
    file: str,
    metric: str,
    period: Any,
    task_id: Any,
    pdf_page: Any,
    table_index: Any,
    md_line: Any,
    table_source_links: Callable[[Any, Any, Any], str],
) -> None:
    key = (task_id, pdf_page, table_index, str(file or ""), str(metric or ""))
    if key in seen:
        return
    seen.add(key)
    refs.append(
        _primary_data_source_ref(
            len(refs) + 1,
            source_type=source_type,
            file=file,
            metric=metric,
            period=period,
            task_id=task_id,
            pdf_page=pdf_page,
            table_index=table_index,
            md_line=md_line,
            table_source_links=table_source_links,
        )
    )


def _markdown_heading(line: str) -> tuple[int, str] | None:
    match = re.match(r"^\s{0,3}(#{1,6})\s+(.+?)\s*$", line or "")
    if not match:
        return None
    title = match.group(2).strip().rstrip("#").strip()
    return len(match.group(1)), title


def _is_reference_line(line: str) -> bool:
    text = (line or "").strip()
    return (
        "source_type=" in text
        and "task_id=" in text
        and ("pdf_page=" in text or "pdf_page_number=" in text)
        and "table_index=" in text
        and not text.startswith("|")
    )


def _extract_reference_lines(lines: list[str] | str) -> list[str]:
    source_lines = lines.splitlines() if isinstance(lines, str) else lines
    return [line.strip() for line in source_lines if _is_reference_line(line)]


def _source_field_value(line: str, field: str) -> str:
    match = re.search(rf"{re.escape(field)}=([^,，\]\)\n]+)", line or "")
    return match.group(1).strip().strip("。；;") if match else ""


def _source_reference_key(line: str) -> tuple[str, str, str, str] | tuple[str]:
    task_id = _source_field_value(line, "task_id")
    pdf_page = _source_field_value(line, "pdf_page") or _source_field_value(line, "pdf_page_number")
    table_index = _source_field_value(line, "table_index")
    md_line = _source_field_value(line, "md_line") or _source_field_value(line, "markdown_line")
    if task_id or pdf_page or table_index or md_line:
        return (task_id, pdf_page, table_index, md_line)
    return (re.sub(r"\s+", " ", line or "").strip(),)


def _reply_has_requested_metric_evidence(
    message: str,
    reply: str,
    *,
    postgres_requested_metric_terms: Callable[[str], list[str]],
    normalize_financial_text: Callable[[Any], str],
) -> bool:
    if not _has_structured_evidence_trace(reply):
        return False
    requested_terms = postgres_requested_metric_terms(message)
    if not requested_terms:
        return True
    normalized_terms = [normalize_financial_text(term) for term in requested_terms]
    reference_text = normalize_financial_text(" ".join(_extract_reference_lines(reply)))
    return all(term and term in reference_text for term in normalized_terms)


def _strip_auto_evidence_sections(
    markdown: str,
    *,
    auto_evidence_section_titles: set[str],
) -> tuple[str, list[str]]:
    lines = (markdown or "").splitlines()
    kept: list[str] = []
    refs: list[str] = []
    index = 0
    while index < len(lines):
        heading = _markdown_heading(lines[index])
        if heading and heading[1] in auto_evidence_section_titles:
            level = heading[0]
            skipped: list[str] = []
            index += 1
            while index < len(lines):
                next_heading = _markdown_heading(lines[index])
                if next_heading and next_heading[0] <= level:
                    break
                skipped.append(lines[index])
                index += 1
            refs.extend(_extract_reference_lines(skipped))
            while kept and not kept[-1].strip():
                kept.pop()
            continue
        kept.append(lines[index])
        index += 1
    return "\n".join(kept).strip(), refs


def _merge_refs_into_reference_section(markdown: str, refs: list[str]) -> str:
    body = (markdown or "").strip()
    existing_keys = {_source_reference_key(line) for line in _extract_reference_lines(body)}
    unique_refs: list[str] = []
    seen = set(existing_keys)
    for ref in refs:
        if not _is_reference_line(ref):
            continue
        key = _source_reference_key(ref)
        if key in seen:
            continue
        seen.add(key)
        unique_refs.append(ref.strip())
    if not unique_refs:
        return body

    lines = body.splitlines() if body else []
    citation_index: int | None = None
    citation_level = 2
    for idx, line in enumerate(lines):
        heading = _markdown_heading(line)
        if heading and heading[1] == "引用来源":
            citation_index = idx
            citation_level = heading[0]
            break

    if citation_index is None:
        prefix = f"{body.rstrip()}\n\n" if body else ""
        return f"{prefix}## 引用来源\n" + "\n".join(unique_refs)

    insert_at = len(lines)
    for idx in range(citation_index + 1, len(lines)):
        heading = _markdown_heading(lines[idx])
        if heading and heading[0] <= citation_level:
            insert_at = idx
            break

    insert_lines: list[str] = []
    if insert_at > 0 and lines[insert_at - 1].strip():
        insert_lines.append("")
    insert_lines.extend(unique_refs)
    if insert_at < len(lines):
        insert_lines.append("")
    return "\n".join(lines[:insert_at] + insert_lines + lines[insert_at:]).strip()


def _merge_primary_data_refs_into_citations(
    reply: str,
    supplement: str | None = None,
    *,
    auto_evidence_section_titles: set[str],
) -> str:
    body, refs = _strip_auto_evidence_sections(reply or "", auto_evidence_section_titles=auto_evidence_section_titles)
    if supplement:
        _supplement_body, supplement_refs = _strip_auto_evidence_sections(
            supplement,
            auto_evidence_section_titles=auto_evidence_section_titles,
        )
        refs.extend(supplement_refs or _extract_reference_lines(supplement))
    return _merge_refs_into_reference_section(body, refs)


def _render_three_statement_primary_data_supplement(
    result: dict[str, Any],
    *,
    primary_data_supplement_max_rows: int,
    table_source_links: Callable[[Any, Any, Any], str],
) -> str | None:
    rows = result.get("rows") or []
    if not rows:
        return None
    lines = [
        "## 主要数据溯源补充",
        "- 后端已从结构化三大表补充本轮主要财务数据的指标级来源；正文如使用这些数值，应以本表的 PDF 页和表格为准。",
        "",
        "| 指标 | 期间 | 原始披露值 | 来源 |",
        "| --- | --- | ---: | --- |",
    ]
    refs: list[str] = []
    seen_refs: set[tuple[Any, Any, Any, str, str]] = set()
    for row in rows[:primary_data_supplement_max_rows]:
        metric = row.get("metric_name") or row.get("metric_key") or "未返回"
        value = _format_statement_value(row)
        locator = _source_locator_text(
            task_id=row.get("task_id"),
            pdf_page=row.get("pdf_page"),
            table_index=row.get("table_index"),
            md_line=row.get("md_line"),
            table_source_links=table_source_links,
        )
        lines.append(
            f"| {row.get('statement_label') or row.get('statement_type') or '三大表'} / {metric} | "
            f"{row.get('period') or result.get('report_id') or '未返回'} | {value or '未返回'} | {locator} |"
        )
        _append_unique_source_ref(
            refs,
            seen_refs,
            source_type=row.get("source_type") or "wiki_metrics",
            file=row.get("file") or "metrics/three_statements.json",
            metric=row.get("statement_label") or metric,
            period=row.get("report_id") or result.get("report_id"),
            task_id=row.get("task_id"),
            pdf_page=row.get("pdf_page"),
            table_index=row.get("table_index"),
            md_line=row.get("md_line"),
            table_source_links=table_source_links,
        )
    if refs:
        lines.extend(["", "## 主要数据引用来源", *refs])
    return "\n".join(lines)


def _first_record_label(record: dict[str, Any]) -> str:
    if not isinstance(record, dict) or not record:
        return ""
    first_value = next(iter(record.values()), "")
    return str(first_value or "").strip()


def _record_values_preview(record: dict[str, Any], *, max_values: int = 4) -> str:
    values = [str(value).strip() for value in list(record.values())[1:] if str(value).strip()]
    if not values:
        return "未返回"
    return " / ".join(values[:max_values])


def _format_statement_value(row: dict[str, Any]) -> str:
    value = row.get("raw_value")
    unit = row.get("unit") or ""
    if value in (None, ""):
        value = row.get("normalized_value")
    return f"{value} {unit}".strip()


def _render_statement_table_primary_data_supplement(
    result: dict[str, Any],
    *,
    primary_data_supplement_max_rows: int,
    table_source_links: Callable[[Any, Any, Any], str],
) -> str | None:
    tables = result.get("tables") or []
    if not tables:
        return None
    lines = [
        "## 主要数据溯源补充",
        "- 后端已从年报主表补充本轮主要数据来源；主表口径优先于附注跳转或全文片段。",
        "",
        "| 指标/行 | 数值预览 | 来源 |",
        "| --- | --- | --- |",
    ]
    refs: list[str] = []
    seen_refs: set[tuple[Any, Any, Any, str, str]] = set()
    remaining = primary_data_supplement_max_rows
    for table in tables:
        records = [record for record in (table.get("records") or []) if isinstance(record, dict)]
        if not records:
            records = [{}]
        for record in records:
            if remaining <= 0:
                break
            metric = _first_record_label(record) or table.get("metric") or "主表数据"
            locator = _source_locator_text(
                task_id=table.get("task_id"),
                pdf_page=table.get("pdf_page"),
                table_index=table.get("table_index"),
                md_line=table.get("md_line"),
                table_source_links=table_source_links,
            )
            lines.append(f"| {metric} | {_record_values_preview(record)} {table.get('unit') or ''} | {locator} |")
            remaining -= 1
        _append_unique_source_ref(
            refs,
            seen_refs,
            source_type=table.get("source_type") or "wiki_metrics",
            file=table.get("file") or "metrics/three_statements.json",
            metric=table.get("metric") or "主表数据",
            period=table.get("report_id") or result.get("report_id"),
            task_id=table.get("task_id"),
            pdf_page=table.get("pdf_page"),
            table_index=table.get("table_index"),
            md_line=table.get("md_line"),
            table_source_links=table_source_links,
        )
        if remaining <= 0:
            break
    if refs:
        lines.extend(["", "## 主要数据引用来源", *refs])
    return "\n".join(lines)


def _render_note_detail_primary_data_supplement(
    result: dict[str, Any],
    *,
    primary_data_supplement_max_rows: int,
    table_source_links: Callable[[Any, Any, Any], str],
) -> str | None:
    tables = result.get("tables") or []
    if not tables:
        return None
    lines = [
        "## 主要数据溯源补充",
        "- 后端已从附注结构化表补充本轮主要明细/构成数据来源；表内所有金额和行项目以对应可打开表格为准。",
        "",
        "| 明细表 | 口径 | 来源 |",
        "| --- | --- | --- |",
    ]
    refs: list[str] = []
    seen_refs: set[tuple[Any, Any, Any, str, str]] = set()
    for table in tables[:primary_data_supplement_max_rows]:
        metric = table.get("metric") or result.get("metric") or "附注明细"
        records = [record for record in (table.get("records") or []) if isinstance(record, dict)]
        row_count = len(records or table.get("rows") or [])
        preview_rows: list[str] = []
        for record in records[:3]:
            label = _first_record_label(record)
            values = _record_values_preview(record, max_values=3)
            if label:
                preview_rows.append(f"{label}: {values}")
        preview = "；".join(preview_rows) if preview_rows else "可打开表格查看完整行"
        locator = _source_locator_text(
            task_id=table.get("task_id"),
            pdf_page=table.get("pdf_page"),
            table_index=table.get("table_index"),
            md_line=table.get("md_line"),
            table_source_links=table_source_links,
        )
        lines.append(
            f"| {metric} | 单位={table.get('unit') or '未返回'}；解析行数={row_count or '未返回'}；明细预览={preview} | {locator} |"
        )
        _append_unique_source_ref(
            refs,
            seen_refs,
            source_type=table.get("source_type") or "wiki_document_links",
            file=table.get("file") or "semantic/document_links.json",
            metric=metric,
            period=table.get("report_id") or result.get("report_id"),
            task_id=table.get("task_id"),
            pdf_page=table.get("pdf_page"),
            table_index=table.get("table_index"),
            md_line=table.get("md_line"),
            table_source_links=table_source_links,
        )
    if refs:
        lines.extend(["", "## 主要数据引用来源", *refs])
    return "\n".join(lines)


def _render_human_capital_primary_data_supplement(
    result: dict[str, Any],
    *,
    primary_data_supplement_max_rows: int,
    table_source_links: Callable[[Any, Any, Any], str],
) -> str | None:
    sections = result.get("sections") or {}
    source_locator = _source_locator_text(
        task_id=result.get("task_id"),
        pdf_page=result.get("pdf_page"),
        table_index=result.get("table_index"),
        md_line=result.get("md_line"),
        table_source_links=table_source_links,
    )
    lines = [
        "## 主要数据溯源补充",
        "- 后端已从年报员工情况表补充本轮人员/人才结构数据来源；员工数、专业构成和学历构成均来自同一张员工情况表。",
        "",
        "| 指标 | 数值 | 来源 |",
        "| --- | ---: | --- |",
    ]
    count = 0
    for rows in (sections.get("scale") or [], sections.get("profession") or [], sections.get("education") or []):
        for label, value in rows:
            if count >= primary_data_supplement_max_rows:
                break
            lines.append(f"| {label} | {value} | {source_locator} |")
            count += 1
        if count >= primary_data_supplement_max_rows:
            break
    if count == 0:
        return None
    lines.extend(
        [
            "",
            "## 主要数据引用来源",
            _primary_data_source_ref(
                1,
                source_type="wiki_report_table",
                file=f"reports/{result.get('report_id')}/report.md",
                metric="员工情况/人才结构",
                period=result.get("report_id"),
                task_id=result.get("task_id"),
                pdf_page=result.get("pdf_page"),
                table_index=result.get("table_index"),
                md_line=result.get("md_line"),
                table_source_links=table_source_links,
            ),
        ]
    )
    return "\n".join(lines)


def _render_wiki_fulltext_primary_data_supplement(
    result: dict[str, Any],
    *,
    primary_data_supplement_max_rows: int,
    table_source_links: Callable[[Any, Any, Any], str],
) -> str | None:
    rows = result.get("rows") or []
    if not rows:
        return None
    lines = [
        "## 主要数据溯源补充",
        "- 结构化指标未完全命中时，后端已从完整年报 Markdown / document_full.json 补充原文证据片段；正文主要数值应回看对应 PDF 页或文本块。",
        "",
        "| 证据片段 | 原文预览 | 来源 |",
        "| --- | --- | --- |",
    ]
    refs: list[str] = []
    seen_refs: set[tuple[Any, Any, Any, str, str]] = set()
    for index, row in enumerate(rows[:primary_data_supplement_max_rows], start=1):
        snippet = re.sub(r"\s+", " ", str(row.get("snippet") or "")).strip()
        if len(snippet) > 180:
            snippet = f"{snippet[:180].rstrip()}..."
        locator = _source_locator_text(
            task_id=row.get("task_id"),
            pdf_page=row.get("pdf_page"),
            table_index=row.get("table_index"),
            md_line=row.get("md_line"),
            table_source_links=table_source_links,
        )
        lines.append(f"| F{index} / {row.get('source_type') or '全文证据'} | {snippet or '未返回'} | {locator} |")
        _append_unique_source_ref(
            refs,
            seen_refs,
            source_type=row.get("source_type") or "wiki_report_fulltext",
            file=row.get("file") or "reports/2025-annual/report.md",
            metric=",".join(result.get("terms") or []) or "全文检索",
            period=result.get("report_id"),
            task_id=row.get("task_id"),
            pdf_page=row.get("pdf_page"),
            table_index=row.get("table_index"),
            md_line=row.get("md_line"),
            table_source_links=table_source_links,
        )
    if refs:
        lines.extend(["", "## 主要数据引用来源", *refs])
    return "\n".join(lines)


def _render_postgres_primary_data_supplement(
    result: dict[str, Any],
    *,
    primary_data_supplement_max_rows: int,
    evidence_url: Callable[[Any, Any, Any, str], str | None],
    markdown_table_cell: Callable[[Any], str],
    table_source_links: Callable[[Any, Any, Any], str],
    postgres_row_pdf_page: Callable[[dict[str, Any]], Any],
    postgres_row_table_index: Callable[[dict[str, Any]], Any],
    postgres_row_md_line: Callable[[dict[str, Any]], Any],
    postgres_row_metric_name: Callable[[dict[str, Any]], str],
    postgres_row_value: Callable[[dict[str, Any]], Any],
    postgres_row_unit: Callable[[dict[str, Any]], Any],
) -> str | None:
    rows = result.get("rows") or []
    if not rows:
        return None
    lines = [
        "## 主要数据溯源补充",
        "- Wiki 结构化证据未完全命中时，后端已从 PostgreSQL `pdf2md` 只读结果补充主要数据来源；若后续定位到 Wiki 表格，默认以 Wiki 为主。",
        "",
        "| 指标 | 期间 | 原始值/值 | 来源 |",
        "| --- | --- | ---: | --- |",
    ]
    refs: list[str] = []
    seen_refs: set[tuple[Any, Any, Any, str, str]] = set()
    for row in rows[:primary_data_supplement_max_rows]:
        pdf_page = postgres_row_pdf_page(row)
        table_index = postgres_row_table_index(row)
        md_line = postgres_row_md_line(row)
        locator = _source_locator_text(
            task_id=row.get("task_id"),
            pdf_page=pdf_page,
            table_index=table_index,
            md_line=md_line,
            table_source_links=table_source_links,
        )
        lines.append(
            f"| {markdown_table_cell(postgres_row_metric_name(row))} | "
            f"{markdown_table_cell(row.get('period_key') or row.get('report_period') or row.get('report_year'))} | "
            f"{markdown_table_cell(postgres_row_value(row))} {markdown_table_cell(postgres_row_unit(row))} | {locator} |"
        )
    lines.extend(["", "## PostgreSQL 引用"])
    for index, row in enumerate(rows, start=1):
        pdf_page = postgres_row_pdf_page(row)
        table_index = postgres_row_table_index(row)
        task_id = row.get("task_id")
        links = []
        pdf_url = evidence_url(task_id, pdf_page, table_index, "pdf")
        page_url = evidence_url(task_id, pdf_page, table_index, "page")
        table_url = evidence_url(task_id, pdf_page, table_index, "table")
        if pdf_url:
            links.append(f"[打开PDF页]({pdf_url})")
        if page_url:
            links.append(f"[查看页来源]({page_url})")
        if table_url:
            links.append(f"[查看表格]({table_url})")
        lines.append(
            f"[P{index}] source_type=postgresql, table={row.get('source_table') or '未返回'}, "
            f"statement_id={row.get('statement_id') or '未返回'}, metric={postgres_row_metric_name(row)}, "
            f"period_key={row.get('period_key') or '未返回'}, task_id={task_id or '未返回'}, "
            f"pdf_page={pdf_page or '未返回'}, table_index={table_index if table_index not in (None, '') else '未返回'}, "
            f"md_line={postgres_row_md_line(row) or '未返回'}"
            + (("，" + "，".join(links)) if links else "")
        )
    return "\n".join(lines)


def _render_postgres_fallback_context(
    result: dict[str, Any],
    *,
    evidence_url: Callable[[Any, Any, Any, str], str | None],
    markdown_table_cell: Callable[[Any], str],
    table_source_links: Callable[[Any, Any, Any], str],
    postgres_row_pdf_page: Callable[[dict[str, Any]], Any],
    postgres_row_table_index: Callable[[dict[str, Any]], Any],
    postgres_row_md_line: Callable[[dict[str, Any]], Any],
    postgres_row_metric_name: Callable[[dict[str, Any]], str],
    postgres_row_value: Callable[[dict[str, Any]], Any],
    postgres_row_unit: Callable[[dict[str, Any]], Any],
) -> str:
    rows = result.get("rows") or []
    parsed = result.get("parsed") if isinstance(result.get("parsed"), dict) else {}
    company_name = parsed.get("resolved_stock_name") or parsed.get("company_name") or "未返回"
    stock_code = parsed.get("resolved_stock_code") or parsed.get("stock_code") or "未返回"
    company_id = parsed.get("resolved_company_id") or "未返回"
    source_tables = [str(item) for item in (result.get("source_tables") or [])]

    lines = [
        "以下是后端在本地 Wiki 确定性证据未命中或命中不足时，从 PostgreSQL `pdf2md` schema 只读查询得到的补充证据。模型可以基于这些数据回答，但必须说明这是数据库 fallback；若后续定位到 Wiki 结构化证据，默认以 Wiki 为主。",
        "输出要求：",
        "- 不得把 PostgreSQL fallback 伪装成 Wiki 证据。",
        "- 只使用下方返回行里的数值、期间、公司、task_id、pdf_page、table_index 和来源表；字段为空时写 `未返回`。",
        "- `## 引用来源` 必须保留 `source_type=postgresql`、`table`、`task_id`、`pdf_page`、`table_index`。",
        f"- 公司: {company_name} / 代码 {stock_code} / company_id={company_id}",
        f"- 查询类型: {parsed.get('query_type') or '未返回'} / statement_type={parsed.get('statement_type') or '未返回'} / metric={parsed.get('metric_name') or parsed.get('canonical_name') or '未返回'}",
        f"- 数据源表: {', '.join(source_tables) if source_tables else '未返回'}",
        "",
        "## PostgreSQL 补充底稿",
        "| 来源表 | 项目/指标 | 期间 | 原始值/值 | 单位 | task_id | pdf_page | table_index |",
        "| --- | --- | --- | ---: | --- | --- | ---: | ---: |",
    ]
    for row in rows:
        pdf_page = postgres_row_pdf_page(row)
        table_index = postgres_row_table_index(row)
        lines.append(
            "| "
            + " | ".join(
                markdown_table_cell(value)
                for value in (
                    row.get("source_table"),
                    postgres_row_metric_name(row),
                    row.get("period_key") or row.get("report_period") or row.get("report_year"),
                    postgres_row_value(row),
                    postgres_row_unit(row),
                    row.get("task_id"),
                    pdf_page,
                    table_index,
                )
            )
            + " |"
        )

    lines.extend(["", "## PostgreSQL 引用"])
    for index, row in enumerate(rows, start=1):
        pdf_page = postgres_row_pdf_page(row)
        table_index = postgres_row_table_index(row)
        task_id = row.get("task_id")
        links = []
        pdf_url = evidence_url(task_id, pdf_page, table_index, "pdf")
        page_url = evidence_url(task_id, pdf_page, table_index, "page")
        table_url = evidence_url(task_id, pdf_page, table_index, "table")
        if pdf_url:
            links.append(f"[打开PDF页]({pdf_url})")
        if page_url:
            links.append(f"[查看页来源]({page_url})")
        if table_url:
            links.append(f"[查看表格]({table_url})")
        lines.append(
            f"[P{index}] source_type=postgresql, table={row.get('source_table') or '未返回'}, "
            f"statement_id={row.get('statement_id') or '未返回'}, metric={postgres_row_metric_name(row)}, "
            f"period_key={row.get('period_key') or '未返回'}, task_id={task_id or '未返回'}, "
            f"pdf_page={pdf_page or '未返回'}, table_index={table_index if table_index not in (None, '') else '未返回'}, "
            f"md_line={postgres_row_md_line(row) or '未返回'}"
            + (("，" + "，".join(links)) if links else "")
        )
    return "\n".join(lines)


__all__ = [
    "_append_unique_source_ref",
    "_extract_reference_lines",
    "_has_primary_data_evidence_trace",
    "_has_structured_evidence_trace",
    "_is_reference_line",
    "_markdown_heading",
    "_merge_primary_data_refs_into_citations",
    "_merge_refs_into_reference_section",
    "_primary_data_source_ref",
    "_record_values_preview",
    "_reply_has_requested_metric_evidence",
    "_render_human_capital_primary_data_supplement",
    "_render_note_detail_primary_data_supplement",
    "_render_postgres_fallback_context",
    "_render_postgres_primary_data_supplement",
    "_render_statement_table_primary_data_supplement",
    "_render_three_statement_primary_data_supplement",
    "_render_wiki_fulltext_primary_data_supplement",
    "_source_field_value",
    "_source_locator_text",
    "_source_reference_key",
    "_strip_auto_evidence_sections",
    "normalize_evidence_trace_for_display",
    "normalize_plain_inline_latex",
]
