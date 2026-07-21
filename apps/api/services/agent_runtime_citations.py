"""Citation and evidence helpers for the Hermes agent runtime."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from services.agent_runtime_source_fields import extract_source_fields as _extract_source_fields_shared
from services.citation_links import append_missing_pdf_source_links, strip_sec_pdf_locators

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

PROCESS_PREAMBLE_MARKERS = (
    "无需重新路由",
    "先验证",
    "用终端",
    "工具调用",
    "打输一个表情",
    "一顿乱投",
)


def strip_process_preamble(content: str | None) -> str:
    """Remove leaked model scratch text before the first formal section."""

    text = str(content or "")
    heading = re.search(r"(?m)^##\s+\S", text)
    if heading is None or heading.start() == 0:
        return text
    preamble = text[: heading.start()]
    if not any(marker in preamble for marker in PROCESS_PREAMBLE_MARKERS):
        return text
    return text[heading.start() :].lstrip()


def normalize_plain_inline_latex(content: str | None) -> str:
    if not content:
        return content or ""

    def replace_symbol(match: re.Match[str]) -> str:
        body = match.group(1).strip()
        return LATEX_INLINE_SYMBOLS.get(body, match.group(0))

    return re.sub(r"\$\s*(\\[A-Za-z]+|\\%)\s*\$", replace_symbol, content)


def normalize_evidence_trace_for_display(content: str | None) -> str:
    """Apply the single citation/link normalization path used by every agent."""
    if not content:
        return content or ""
    return append_missing_pdf_source_links(
        normalize_plain_inline_latex(strip_process_preamble(content))
    )


def _has_structured_evidence_trace(reply: str) -> bool:
    text = reply or ""
    if "source_type=" not in text:
        return False
    for line in text.splitlines():
        if "source_type=" not in line:
            continue
        has_sec_url = re.search(r"\bsource_url=https://(?:www\.)?sec\.gov/\S+", line, re.IGNORECASE)
        has_sec_anchor = re.search(r"\b(?:source_anchor|xbrl_tag)=[^,\s]+", line)
        if has_sec_url and has_sec_anchor:
            return True
        if "task_id=" not in line:
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
    statement_type: Any = None,
    canonical_name: Any = None,
    metric_name: Any = None,
    value: Any = None,
    raw_value: Any = None,
    unit: Any = None,
    currency: Any = None,
    scale: Any = None,
    market: Any = None,
    company_id: Any = None,
    report_id: Any = None,
    filing_id: Any = None,
    parse_run_id: Any = None,
    evidence_id: Any = None,
    quote: Any = None,
    evidence_source_type: Any = None,
    source_url: Any = None,
    source_anchor: Any = None,
    xbrl_tag: Any = None,
) -> str:
    structured_fields = {
        "statement_type": statement_type,
        "canonical_name": canonical_name,
        "metric_name": metric_name,
        "value": value,
        "raw_value": raw_value,
        "unit": unit,
        "currency": currency,
        "scale": scale,
        "market": market,
        "company_id": company_id,
        "report_id": report_id,
        "filing_id": filing_id,
        "parse_run_id": parse_run_id,
        "evidence_id": evidence_id,
        "quote": quote,
        "evidence_source_type": evidence_source_type,
    }
    structured = ", ".join(
        f"{key}={_reference_value(value)}"
        for key, value in structured_fields.items()
        if value not in (None, "")
    )
    prefix = (
        f"[D{index}] source_type={source_type}, file={file or '未返回'}, "
        f"metric={metric or '未返回'}, period={period or '未返回'}, "
    )
    external_url = str(source_url or "").strip()
    external_anchor = str(source_anchor or "").strip()
    is_sec_source = bool(re.match(r"^https://(?:www\.)?sec\.gov/", external_url, re.IGNORECASE))
    if is_sec_source:
        target = external_url if not external_anchor or "#" in external_url else f"{external_url}#{external_anchor}"
        locator = (
            f"source_url={external_url}, source_anchor={external_anchor or '未返回'}, "
            f"xbrl_tag={xbrl_tag or '未返回'}, [打开披露原文]({target})"
        )
    else:
        locator = _source_locator_text(
            task_id=task_id,
            pdf_page=pdf_page,
            table_index=table_index,
            md_line=md_line,
            table_source_links=table_source_links,
        )
        if external_url:
            target = external_url if not external_anchor or "#" in external_url else f"{external_url}#{external_anchor}"
            locator += (
                f", source_url={external_url}, source_anchor={external_anchor or '未返回'}, "
                f"xbrl_tag={xbrl_tag or '未返回'}, [打开披露原文]({target})"
            )
    return prefix + (f"{structured}, " if structured else "") + locator


def sanitize_sec_xbrl_reference_lines(
    reply: str,
    trusted_evidence: Sequence[Mapping[str, Any]],
    *,
    table_source_links: Callable[[Any, Any, Any], str],
) -> str:
    """Rebuild SEC citation lines from server-trusted XBRL facts only."""

    reply = strip_sec_pdf_locators(reply)
    trusted: dict[tuple[str, str], Mapping[str, Any]] = {}
    trusted_by_fact: dict[str, Mapping[str, Any]] = {}
    trusted_by_metric_period: dict[tuple[str, str], Mapping[str, Any]] = {}
    has_us_sec_evidence = False
    for item in trusted_evidence or ():
        if not isinstance(item, Mapping):
            continue
        source_url = str(item.get("source_url") or "").strip()
        source_anchor = str(item.get("source_anchor") or "").strip()
        if not re.match(r"^https://(?:www\.)?sec\.gov/", source_url, re.IGNORECASE) or not source_anchor:
            continue
        has_us_sec_evidence = True
        trusted[(source_url, source_anchor)] = item
        xbrl_tag = _normalized_source_value(item.get("xbrl_tag") or item.get("source_id") or item.get("evidence_id"))
        if xbrl_tag:
            trusted_by_fact.setdefault(xbrl_tag, item)
        period = _normalized_source_value(item.get("period"))
        for metric_value in (
            item.get("metric"),
            item.get("canonical_name"),
            item.get("metric_name"),
            item.get("evidence_id"),
            item.get("xbrl_tag"),
        ):
            metric = _normalized_source_value(metric_value)
            if metric and period:
                trusted_by_metric_period.setdefault((metric, period), item)
    if not trusted:
        return reply

    output: list[str] = []
    emitted: set[str] = set()
    reference_index = 0
    for raw_line in (reply or "").splitlines():
        if "source_type=" not in raw_line:
            if has_us_sec_evidence and _looks_like_us_sec_foreign_pdf_locator(raw_line):
                continue
            output.append(raw_line)
            continue
        fields = _extract_source_fields_shared(raw_line)
        source_type = _normalized_source_value(fields.get("source_type"))
        evidence_source_type = _normalized_source_value(fields.get("evidence_source_type"))
        is_sec_line = (
            "sec.gov/" in raw_line.lower()
            or source_type == "sec_xbrl_fact"
            or evidence_source_type == "sec_xbrl_fact"
        )
        if not is_sec_line:
            if has_us_sec_evidence and _looks_like_us_sec_foreign_pdf_locator(raw_line):
                continue
            output.append(raw_line)
            continue
        key = (
            str(fields.get("source_url") or "").strip(),
            str(fields.get("source_anchor") or "").strip(),
        )
        item = trusted.get(key)
        if item is None:
            item = trusted_by_fact.get(_normalized_source_value(fields.get("xbrl_tag")))
        if item is None:
            period = _normalized_source_value(fields.get("period"))
            for metric_value in (
                fields.get("metric"),
                fields.get("canonical_name"),
                fields.get("metric_name"),
                fields.get("evidence_id"),
            ):
                item = trusted_by_metric_period.get((_normalized_source_value(metric_value), period))
                if item is not None:
                    break
        if item is None:
            continue
        emitted_key = "|".join(
            str(item.get(field) or "")
            for field in ("source_url", "source_anchor", "metric", "period", "evidence_id")
        )
        if emitted_key in emitted:
            continue
        emitted.add(emitted_key)
        reference_index += 1
        output.append(
            _primary_data_source_ref(
                reference_index,
                source_type="wiki_metrics",
                file=str(item.get("file") or ""),
                metric=str(item.get("metric_name") or item.get("metric") or ""),
                period=item.get("period"),
                task_id=item.get("task_id"),
                pdf_page=item.get("pdf_page"),
                table_index=item.get("table_index"),
                md_line=item.get("md_line"),
                table_source_links=table_source_links,
                statement_type=item.get("statement_type"),
                canonical_name=item.get("canonical_name") or item.get("metric"),
                metric_name=item.get("metric_name"),
                value=item.get("value"),
                raw_value=item.get("raw_value"),
                unit=item.get("unit"),
                currency=item.get("currency"),
                scale=item.get("scale"),
                market=item.get("market"),
                company_id=item.get("company_id"),
                report_id=item.get("report_id"),
                filing_id=item.get("filing_id"),
                parse_run_id=item.get("parse_run_id"),
                evidence_id=item.get("evidence_id"),
                quote=item.get("quote"),
                evidence_source_type=item.get("evidence_source_type"),
                source_url=item.get("source_url"),
                source_anchor=item.get("source_anchor"),
                xbrl_tag=item.get("xbrl_tag"),
            )
        )
    return "\n".join(output)


def _normalized_source_value(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip().casefold()


def _looks_like_us_sec_foreign_pdf_locator(line: str) -> bool:
    text = str(line or "")
    if not text.strip():
        return False
    if "/api/pdf_page/" in text or "打开PDF" in text or "pdf_page=" in text or "table_index=" in text:
        return "task_id=" in text or re.search(r"\b[0-9a-fA-F-]{32,36}\b", text) is not None
    return bool(re.search(r"\btask_id=[0-9a-fA-F-]{32,36}\b", text))


def _reference_value(value: Any) -> str:
    text = str(value)
    if any(marker in text for marker in (",", "，", ";", "；", "|", "\n")):
        return '"' + text.replace('"', "'").replace("\n", " ") + '"'
    return text


def _append_unique_source_ref(
    refs: list[str],
    seen: set[tuple[Any, ...]],
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
    **structured_fields: Any,
) -> None:
    source_value = structured_fields.get("value")
    if source_value in (None, ""):
        source_value = structured_fields.get("raw_value")
    key = (
        task_id,
        pdf_page,
        table_index,
        str(file or ""),
        str(metric or ""),
        str(structured_fields.get("evidence_id") or ""),
        str(structured_fields.get("canonical_name") or structured_fields.get("metric_name") or ""),
        str(source_value if source_value not in (None, "") else ""),
    )
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
            **structured_fields,
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
    has_sec_url = re.search(r"\bsource_url=https://(?:www\.)?sec\.gov/\S+", text, re.IGNORECASE)
    has_sec_anchor = re.search(r"\b(?:source_anchor|xbrl_tag)=[^,\s]+", text)
    return (
        "source_type=" in text
        and (
            (
                "task_id=" in text
                and ("pdf_page=" in text or "pdf_page_number=" in text)
                and "table_index=" in text
            )
            or (has_sec_url and has_sec_anchor)
        )
        and not text.startswith("|")
    )


def _extract_reference_lines(lines: list[str] | str) -> list[str]:
    source_lines = lines.splitlines() if isinstance(lines, str) else lines
    return [line.strip() for line in source_lines if _is_reference_line(line)]


def _source_field_value(line: str, field: str) -> str:
    match = re.search(rf"{re.escape(field)}=([^,，\]\)\n]+)", line or "")
    return match.group(1).strip().strip("。；;") if match else ""


def _source_reference_key(line: str) -> tuple[str, ...]:
    source_url = _source_field_value(line, "source_url")
    source_anchor = _source_field_value(line, "source_anchor")
    xbrl_tag = _source_field_value(line, "xbrl_tag")
    if source_url and re.match(r"^https://(?:www\.)?sec\.gov/", source_url, re.IGNORECASE):
        fields = _extract_source_fields_shared(
            line,
            allowed_fields={"metric", "canonical_name", "metric_name", "value", "raw_value", "evidence_id"},
        )
        canonical_metric = fields.get("canonical_name") or fields.get("metric_name") or fields.get("metric") or ""
        return (
            source_url,
            source_anchor,
            xbrl_tag,
            canonical_metric,
        )
    task_id = _source_field_value(line, "task_id")
    pdf_page = _source_field_value(line, "pdf_page") or _source_field_value(line, "pdf_page_number")
    table_index = _source_field_value(line, "table_index")
    md_line = _source_field_value(line, "md_line") or _source_field_value(line, "markdown_line")
    if task_id or pdf_page or table_index or md_line:
        fields = _extract_source_fields_shared(
            line,
            allowed_fields={"metric", "canonical_name", "metric_name", "value", "raw_value", "evidence_id"},
        )
        canonical_metric = fields.get("canonical_name") or fields.get("metric_name") or fields.get("metric") or ""
        source_value = fields.get("value") or fields.get("raw_value") or ""
        return (
            task_id,
            pdf_page,
            table_index,
            md_line,
            fields.get("evidence_id") or "",
            canonical_metric,
            source_value,
        )
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
    references = _extract_reference_lines(reply)
    reference_text = normalize_financial_text(" ".join(references))
    has_metric = all(term and term in reference_text for term in normalized_terms)
    has_numeric_fact = any(
        (_source_field_value(line, "value") or _source_field_value(line, "raw_value"))
        and _source_field_value(line, "unit")
        for line in references
    )
    return has_metric and has_numeric_fact


def _reference_completeness(line: str) -> int:
    return sum(
        bool(_source_field_value(line, field))
        for field in (
            "statement_type",
            "canonical_name",
            "metric_name",
            "value",
            "raw_value",
            "unit",
            "currency",
            "market",
            "company_id",
            "report_id",
            "filing_id",
            "parse_run_id",
            "evidence_id",
            "quote",
        )
    )


def _reference_locator_key(line: str) -> tuple[str, str, str] | None:
    task_id = _source_field_value(line, "task_id")
    pdf_page = _source_field_value(line, "pdf_page") or _source_field_value(line, "pdf_page_number")
    table_index = _source_field_value(line, "table_index")
    if task_id and (pdf_page or table_index):
        return task_id, pdf_page, table_index
    return None


def _reference_has_research_identity(line: str) -> bool:
    return all(
        _source_field_value(line, field)
        for field in ("company_id", "filing_id", "parse_run_id")
    )


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
    lines = body.splitlines() if body else []
    existing: dict[tuple[Any, ...], tuple[int, int]] = {}
    locator_entries: dict[tuple[str, str, str], list[tuple[tuple[Any, ...], int, int]]] = {}
    for index, line in enumerate(lines):
        if _is_reference_line(line):
            key = _source_reference_key(line)
            score = _reference_completeness(line)
            existing[key] = (index, score)
            locator = _reference_locator_key(line)
            if locator is not None:
                locator_entries.setdefault(locator, []).append((key, index, score))
    unique_refs: list[str] = []
    for ref in refs:
        if not _is_reference_line(ref):
            continue
        key = _source_reference_key(ref)
        score = _reference_completeness(ref)
        current = existing.get(key)
        if current is not None:
            if score > current[1]:
                lines[current[0]] = ref.strip()
                existing[key] = (current[0], score)
            continue
        locator = _reference_locator_key(ref)
        if locator is not None and _reference_has_research_identity(ref):
            replaceable = next(
                (
                    (old_key, old_index, old_score)
                    for old_key, old_index, old_score in locator_entries.get(locator, [])
                    if not _reference_has_research_identity(lines[old_index]) and score > old_score
                ),
                None,
            )
            if replaceable is not None:
                old_key, old_index, _old_score = replaceable
                lines[old_index] = ref.strip()
                existing.pop(old_key, None)
                existing[key] = (old_index, score)
                locator_entries[locator] = [
                    entry for entry in locator_entries[locator] if entry[1] != old_index
                ] + [(key, old_index, score)]
                continue
        existing[key] = (-1, score)
        unique_refs.append(ref.strip())
    body = "\n".join(lines).strip()
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
        "- 后端已从结构化三大表补充本轮主要财务数据的指标级来源；正文如使用这些数值，应以本表的 PDF 页/表格或监管披露 anchor 为准。",
        "",
        "| 指标 | 期间 | 原始披露值 | 来源 |",
        "| --- | --- | ---: | --- |",
    ]
    refs: list[str] = []
    seen_refs: set[tuple[Any, ...]] = set()
    for row in rows[:primary_data_supplement_max_rows]:
        metric = row.get("metric_name") or row.get("metric_key") or "未返回"
        value = _format_statement_value(row)
        source_value = row.get("raw_value")
        if source_value in (None, ""):
            source_value = row.get("value")
        if source_value in (None, ""):
            source_value = row.get("normalized_value")
        locator = _source_locator_text(
            task_id=row.get("task_id"),
            pdf_page=row.get("pdf_page"),
            table_index=row.get("table_index"),
            md_line=row.get("md_line"),
            table_source_links=table_source_links,
        )
        source_url = str(row.get("source_url") or "").strip()
        source_anchor = str(row.get("source_anchor") or "").strip()
        if source_url:
            target = source_url if not source_anchor or "#" in source_url else f"{source_url}#{source_anchor}"
            locator = f"source_url={source_url}, source_anchor={source_anchor or '未返回'}，[打开披露原文]({target})"
        lines.append(
            f"| {row.get('statement_label') or row.get('statement_type') or '三大表'} / {metric} | "
            f"{row.get('period') or result.get('report_id') or '未返回'} | {value or '未返回'} | {locator} |"
        )
        if source_url:
            _append_unique_source_ref(
                refs,
                seen_refs,
                source_type=row.get("source_type") or "wiki_metrics",
                file=row.get("file") or "metrics/financial_data.json",
                metric=row.get("statement_label") or metric,
                period=row.get("period") or row.get("report_id") or result.get("report_id"),
                task_id=row.get("task_id"),
                pdf_page=row.get("pdf_page"),
                table_index=row.get("table_index"),
                md_line=row.get("md_line"),
                table_source_links=table_source_links,
                statement_type=row.get("statement_type"),
                canonical_name=row.get("canonical_name") or row.get("metric_key"),
                metric_name=row.get("metric_name") or metric,
                value=source_value,
                raw_value=row.get("raw_value"),
                unit=row.get("unit"),
                currency=row.get("currency"),
                scale=row.get("scale") or row.get("base_scale"),
                market=row.get("market") or result.get("market"),
                company_id=row.get("company_id") or result.get("company_id"),
                report_id=row.get("report_id") or result.get("report_id"),
                filing_id=row.get("filing_id") or result.get("filing_id"),
                parse_run_id=row.get("parse_run_id") or result.get("parse_run_id"),
                evidence_id=row.get("evidence_id"),
                quote=row.get("source_quote") or row.get("quote_text"),
                evidence_source_type=row.get("evidence_source_type"),
                source_url=source_url,
                source_anchor=source_anchor,
                xbrl_tag=row.get("xbrl_tag"),
            )
        else:
            _append_unique_source_ref(
                refs,
                seen_refs,
                source_type=row.get("source_type") or "wiki_metrics",
                file=row.get("file") or "metrics/three_statements.json",
                metric=row.get("statement_label") or metric,
                period=row.get("period") or row.get("period_key") or row.get("report_id") or result.get("report_id"),
                task_id=row.get("task_id"),
                pdf_page=row.get("pdf_page"),
                table_index=row.get("table_index"),
                md_line=row.get("md_line"),
                table_source_links=table_source_links,
                statement_type=row.get("statement_type"),
                canonical_name=row.get("canonical_name") or row.get("metric_key"),
                metric_name=row.get("metric_name") or metric,
                value=source_value,
                raw_value=row.get("raw_value"),
                unit=row.get("unit"),
                currency=row.get("currency"),
                scale=row.get("scale") or row.get("base_scale"),
                market=row.get("market") or result.get("market"),
                company_id=row.get("company_id") or result.get("company_id"),
                report_id=row.get("report_id") or result.get("report_id"),
                filing_id=row.get("filing_id") or result.get("filing_id"),
                parse_run_id=row.get("parse_run_id") or result.get("parse_run_id"),
                evidence_id=row.get("evidence_id"),
                quote=row.get("source_quote") or row.get("quote_text"),
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
    unit = row.get("unit") or row.get("currency") or ""
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
    seen_refs: set[tuple[Any, ...]] = set()
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
            market=table.get("market") or result.get("market"),
            company_id=table.get("company_id") or result.get("company_id"),
            report_id=table.get("report_id") or result.get("report_id"),
            filing_id=table.get("filing_id") or result.get("filing_id"),
            parse_run_id=table.get("parse_run_id") or result.get("parse_run_id"),
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
    seen_refs: set[tuple[Any, ...]] = set()
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
            market=table.get("market") or result.get("market"),
            company_id=table.get("company_id") or result.get("company_id"),
            report_id=table.get("report_id") or result.get("report_id"),
            filing_id=table.get("filing_id") or result.get("filing_id"),
            parse_run_id=table.get("parse_run_id") or result.get("parse_run_id"),
            evidence_id=table.get("document_link_id"),
            quote=table.get("raw_preview"),
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
    seen_refs: set[tuple[Any, ...]] = set()
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
            file=row.get("file") or f"reports/{result.get('report_id') or '未返回'}/report.md",
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
    schema_label = "pdf2md"
    if source_tables and "." in source_tables[0]:
        schema_label = source_tables[0].split(".", 1)[0]

    lines = [
        f"以下是后端在本地 Wiki 确定性证据未命中或命中不足时，从 PostgreSQL `{schema_label}` schema 只读查询得到的补充证据。模型可以基于这些数据回答，但必须说明这是数据库 fallback；若后续定位到 Wiki 结构化证据，默认以 Wiki 为主。",
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
    "_first_record_label",
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
