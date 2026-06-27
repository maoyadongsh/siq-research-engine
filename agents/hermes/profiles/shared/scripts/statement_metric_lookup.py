#!/usr/bin/env python3
"""Render traceable main-statement tables from the local OKF/Wiki workset."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from local_citations import find_company_dir_from_text, primary_report  # noqa: E402
from note_detail_lookup import (  # noqa: E402
    extract_table_html,
    markdown_escape,
    parse_html_table,
    public_api_url,
)

WIKI_BASE = Path(os.environ.get("SIQ_WIKI_ROOT", "/home/maoyd/siq-research-engine/data/wiki")).expanduser()
DEFAULT_SOURCE_TYPE = os.environ.get(
    "SIQ_DEFAULT_SOURCE_TYPE",
    "okf_metrics" if "okf_staging" in str(WIKI_BASE) else "wiki_metrics",
)
CASH_FLOW_CORE_LABELS = (
    "经营活动现金流入小计",
    "经营活动现金流出小计",
    "经营活动产生的现金流量净额",
    "投资活动现金流入小计",
    "投资活动现金流出小计",
    "投资活动产生的现金流量净额",
    "筹资活动现金流入小计",
    "筹资活动现金流出小计",
    "筹资活动产生的现金流量净额",
    "现金及现金等价物净增加额",
    "期初现金及现金等价物余额",
    "期末现金及现金等价物余额",
)
BALANCE_SHEET_PRIORITY_LABELS = (
    "流动资产合计",
    "非流动资产合计",
    "资产总计",
    "流动负债合计",
    "非流动负债合计",
    "负债合计",
    "归属于母公司所有者权益",
    "所有者权益合计",
    "股东权益合计",
    "负债和所有者权益总计",
    "负债和股东权益总计",
)
STATEMENT_METRIC_LABELS = {
    "balance_sheet": "资产负债表核心数据",
    "income_statement": "利润表核心数据",
    "cash_flow_statement": "现金流量表核心数据",
}


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def statement_type_from_query(query: str | None) -> str | None:
    text = re.sub(r"\s+", "", str(query or ""))
    if any(term in text for term in ("现金流", "现金流量表", "经营活动现金", "投资活动现金", "筹资活动现金", "经营现金流")):
        return "cash_flow_statement"
    if any(term in text for term in ("资产负债表", "资产负债", "资产构成", "资产结构", "负债结构", "负债与权益", "负债权益", "偿债", "总资产", "总负债", "净资产")):
        return "balance_sheet"
    if any(term in text for term in ("利润表", "损益表", "营业收入", "营收", "营业成本", "营业利润", "利润总额", "净利润", "归母净利润", "扣非归母", "扣非净利润")):
        return "income_statement"
    return None


def metric_payload_path(company_dir: Path, report_id: str) -> Path | None:
    candidates = [
        company_dir / "reports" / report_id / "metrics" / "three_statements.json",
        company_dir / "metrics" / "reports" / report_id / "three_statements.json",
        company_dir / "metrics" / "latest" / "three_statements.json",
        company_dir / "metrics" / "three_statements.json",
    ]
    return next((path for path in candidates if path.exists()), None)


def local_source_type(kind: str) -> str:
    prefix = "okf" if DEFAULT_SOURCE_TYPE.startswith("okf_") or "okf_staging" in str(WIKI_BASE) else "wiki"
    return f"{prefix}_{kind}"


def relative_file(company_dir: Path, path: Path | None, fallback: str) -> str:
    if path:
        try:
            return str(path.relative_to(company_dir))
        except ValueError:
            return str(path)
    return fallback


def iter_records(obj: Any) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if isinstance(obj, dict):
        if any(key in obj for key in ("metric_name", "metric_key", "statement_type", "source")):
            records.append(obj)
        for value in obj.values():
            records.extend(iter_records(value))
    elif isinstance(obj, list):
        for item in obj:
            records.extend(iter_records(item))
    return records


def to_number(value: Any) -> float | None:
    text = str(value or "").replace(",", "").strip()
    if not text:
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    try:
        number = float(text)
    except ValueError:
        return None
    return -number if negative else number


def percent_change(current: Any, previous: Any) -> str | None:
    cur = to_number(current)
    prev = to_number(previous)
    if cur is None or prev in (None, 0):
        return None
    return f"{(cur - prev) / abs(prev) * 100:.2f}%"


def normalize_label(value: Any) -> str:
    return re.sub(r"[\s/、：:]+", "", str(value or ""))


def source_key(source: dict[str, Any]) -> tuple[Any, Any, Any, Any]:
    return (
        source.get("task_id"),
        source.get("pdf_page"),
        source.get("table_index"),
        source.get("md_line"),
    )


def choose_statement_sources(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counter: Counter[tuple[Any, Any, Any, Any]] = Counter()
    source_by_key: dict[tuple[Any, Any, Any, Any], dict[str, Any]] = {}
    first_seen: dict[tuple[Any, Any, Any, Any], int] = {}
    for position, record in enumerate(records):
        source = record.get("source") if isinstance(record.get("source"), dict) else {}
        key = source_key(source)
        if not key[0] or not key[1] or not key[2] or not key[3]:
            continue
        counter[key] += 1
        source_by_key[key] = source
        first_seen.setdefault(key, position)
    return [
        source_by_key[key]
        for key, _count in sorted(
            counter.items(),
            key=lambda item: (
                int(source_by_key[item[0]].get("md_line") or 10**9),
                int(source_by_key[item[0]].get("table_index") or 10**9),
                first_seen[item[0]],
            ),
        )
    ]


def records_for_source(records: list[dict[str, Any]], source: dict[str, Any]) -> list[dict[str, Any]]:
    target_key = source_key(source)
    return [
        record
        for record in records
        if source_key(record.get("source") if isinstance(record.get("source"), dict) else {}) == target_key
    ]


def source_unit_hint(records: list[dict[str, Any]], payload: dict[str, Any]) -> str:
    hints = [str(record.get("unit_hint") or record.get("raw_unit") or "").strip() for record in records]
    hints = [hint for hint in hints if hint]
    if hints:
        return Counter(hints).most_common(1)[0][0]
    return str(payload.get("unit") or "元")


def clean_table_records(headers: list[str], records: list[dict[str, str]]) -> list[dict[str, str]]:
    if not headers or not records:
        return records
    first_values = [records[0].get(header, "") for header in headers]
    if [normalize_label(item) for item in first_values] == [normalize_label(item) for item in headers]:
        return records[1:]
    return records


def display_headers(headers: list[str]) -> list[str]:
    suffixes = []
    for header in headers:
        if "/" not in str(header):
            return headers
        _head, suffix = str(header).split("/", 1)
        suffixes.append(normalize_label(suffix))
    if not suffixes or len(set(suffixes)) != 1:
        return headers
    return [str(header).split("/", 1)[0] or str(header) for header in headers]


def filter_statement_records(parsed_records: list[dict[str, str]], statement_type: str) -> list[dict[str, str]]:
    if statement_type != "cash_flow_statement":
        return parsed_records
    output: list[dict[str, str]] = []
    for record in parsed_records:
        first_value = next(iter(record.values()), "")
        label = normalize_label(first_value)
        if any(item in label for item in CASH_FLOW_CORE_LABELS):
            output.append(record)
    return output


def display_records(records: list[dict[str, str]], statement_type: str, max_rows: int) -> list[dict[str, str]]:
    """Keep key balance-sheet totals visible even when the source table is long."""
    if max_rows <= 0 or len(records) <= max_rows:
        return records
    output = list(records[:max_rows])
    if statement_type != "balance_sheet":
        return output

    seen = {id(record) for record in output}
    for record in records[max_rows:]:
        first_value = next(iter(record.values()), "")
        label = normalize_label(first_value)
        if any(priority in label for priority in BALANCE_SHEET_PRIORITY_LABELS):
            if id(record) not in seen:
                output.append(record)
                seen.add(id(record))
    return output


def table_title(table: dict[str, Any], fallback: str) -> str:
    headers = table.get("headers") or []
    if headers:
        first = str(headers[0] or "").strip()
        if first:
            return first
    return fallback


def resolve_statement_metrics(
    company_text: str,
    metric_text: str,
    report_id: str | None = None,
) -> dict[str, Any]:
    company_dir = find_company_dir_from_text(company_text, WIKI_BASE)
    if not company_dir:
        return {"status": "company_not_found", "company_text": company_text, "tables": []}

    report = primary_report(company_dir, query_text=metric_text)
    resolved_report_id = report_id or report.get("report_id") or "2025-annual"
    statement_type = statement_type_from_query(metric_text)
    if not statement_type:
        return {"status": "unsupported_statement_query", "company_id": company_dir.name, "tables": []}

    metrics_path = metric_payload_path(company_dir, resolved_report_id)
    payload = read_json(metrics_path or Path(), {}) or {}
    metric_records = [
        record
        for record in iter_records(payload.get("data") or payload)
        if record.get("statement_type") == statement_type
    ]
    sources = choose_statement_sources(metric_records)
    if not sources:
        return {"status": "statement_source_not_found", "company_id": company_dir.name, "tables": []}

    report_md = company_dir / "reports" / resolved_report_id / "report.md"
    task_id = report.get("task_id")
    tables: list[dict[str, Any]] = []
    for source in sources:
        md_line = int(source.get("md_line") or 0)
        html = extract_table_html(report_md, md_line)
        parsed = parse_html_table(html or "")
        headers = parsed.get("headers") or []
        records = clean_table_records(headers, parsed.get("records") or [])
        filtered_records = filter_statement_records(records, statement_type)
        if not filtered_records:
            continue
        source_records = records_for_source(metric_records, source)
        table_index = source.get("table_index")
        pdf_page = source.get("pdf_page")
        task_id = source.get("task_id") or task_id
        tables.append(
            {
                "source_type": local_source_type("metrics"),
                "file": relative_file(company_dir, metrics_path, "metrics/three_statements.json"),
                "metric": STATEMENT_METRIC_LABELS.get(statement_type, metric_text),
                "report_id": resolved_report_id,
                "task_id": task_id,
                "pdf_page": pdf_page,
                "table_index": table_index,
                "md_line": md_line,
                "unit": source_unit_hint(source_records, payload),
                "headers": headers,
                "records": filtered_records,
                "open_pdf_page_url": public_api_url(f"/api/pdf_page/{task_id}/{pdf_page}?format=html"),
                "open_source_page_url": public_api_url(f"/api/source/{task_id}/page/{pdf_page}?format=html"),
                "open_source_table_url": public_api_url(f"/api/source/{task_id}/table/{table_index}?format=html"),
            }
        )

    return {
        "status": "ok" if tables else "statement_table_not_parsed",
        "company_id": company_dir.name,
        "report_id": resolved_report_id,
        "task_id": task_id,
        "metric": metric_text,
        "statement_type": statement_type,
        "metrics_file": (
            str(metrics_path.relative_to(company_dir))
            if metrics_path and metrics_path.exists()
            else "metrics/three_statements.json"
        ),
        "tables": tables,
    }


def render_cash_flow_conclusion(records: list[dict[str, str]], unit: str) -> list[str]:
    by_label = {normalize_label(next(iter(record.values()), "")): record for record in records}

    def row_values(label: str) -> tuple[str, str]:
        normalized_label = normalize_label(label)
        record = next((value for key, value in by_label.items() if normalized_label in key), {})
        values = list(record.values())
        return (values[-2], values[-1]) if len(values) >= 2 else ("", "")

    operating_2025, operating_2024 = row_values("经营活动产生的现金流量净额")
    investing_2025, investing_2024 = row_values("投资活动产生的现金流量净额")
    financing_2025, financing_2024 = row_values("筹资活动产生的现金流量净额")
    net_2025, net_2024 = row_values("现金及现金等价物净增加额")
    ending_2025, ending_2024 = row_values("期末现金及现金等价物余额")
    operating_change = percent_change(operating_2025, operating_2024)

    lines = []
    if operating_2025:
        change_text = f"，同比变动 {operating_change}" if operating_change else ""
        lines.append(f"- 经营活动现金流量净额为 **{operating_2025} {unit}**{change_text}。")
    if investing_2025:
        lines.append(f"- 投资活动现金流量净额为 **{investing_2025} {unit}**，2024 年为 {investing_2024 or '未解析'}。")
    if financing_2025:
        lines.append(f"- 筹资活动现金流量净额为 **{financing_2025} {unit}**，2024 年为 {financing_2024 or '未解析'}。")
    if net_2025:
        lines.append(f"- 现金及现金等价物净增加额为 **{net_2025} {unit}**，2024 年为 {net_2024 or '未解析'}。")
    if ending_2025:
        lines.append(f"- 期末现金及现金等价物余额为 **{ending_2025} {unit}**，2024 年末为 {ending_2024 or '未解析'}。")
    return lines


def render_markdown(result: dict[str, Any], max_rows: int = 40) -> str:
    if not result.get("tables"):
        return "## 结论\n- 证据链不完整：未定位到主表结构化数据。"

    tables = result["tables"]
    records = [record for table in tables for record in (table.get("records") or [])]
    lines = ["## 结论"]
    if result.get("statement_type") == "cash_flow_statement":
        lines.extend(render_cash_flow_conclusion(records, tables[0].get("unit") or "元"))
    else:
        table_summary = "、".join(
            f"table_index={table.get('table_index')} / pdf_page={table.get('pdf_page')}"
            for table in tables
        )
        lines.append(f"- 已从三大表结构化数据定位到正文主表：{table_summary}。")
    lines.append("- 以下数据直接来自 `three_statements.json` 指向的年报主表；附注 `document_links` 只可作为后续明细解释，不能替代主表。")

    lines.extend(["", "## 依据/数据"])
    for table_pos, table in enumerate(tables, start=1):
        table_records = table.get("records") or []
        output_records = display_records(table_records, result.get("statement_type") or "", max_rows)
        headers = table.get("headers") or []
        lines.append("")
        lines.append(f"### {table_pos}. {table_title(table, table.get('metric') or '主表数据')}")
        lines.append(f"- 单位：{table.get('unit') or '未返回'}")
        lines.append(f"- 溯源：pdf_page={table.get('pdf_page')}, table_index={table.get('table_index')}, md_line={table.get('md_line')}")
        lines.append(f"- 表格完整性：解析出 {len(table_records)} 行；以下展示 {len(output_records)} 行。")
        if headers and output_records:
            output_headers = display_headers(headers)
            lines.append("")
            lines.append("| " + " | ".join(markdown_escape(item) for item in output_headers) + " |")
            lines.append("| " + " | ".join("---" for _ in headers) + " |")
            for record in output_records:
                lines.append("| " + " | ".join(markdown_escape(record.get(header, "")) for header in headers) + " |")
    lines.extend(["", "## 引用来源"])
    for idx, table in enumerate(tables, start=1):
        links = []
        if table.get("open_pdf_page_url"):
            links.append(f"[打开PDF页]({table['open_pdf_page_url']})")
        if table.get("open_source_page_url"):
            links.append(f"[查看页来源]({table['open_source_page_url']})")
        if table.get("open_source_table_url"):
            links.append(f"[查看表格]({table['open_source_table_url']})")
        lines.append(
            f"[{idx}] source_type={table.get('source_type') or local_source_type('metrics')}, file={table.get('file')}, metric={table.get('metric')}, "
            f"period={table.get('report_id')}, task_id={table.get('task_id')}, "
            f"pdf_page={table.get('pdf_page')}, table_index={table.get('table_index')}, "
            f"md_line={table.get('md_line')}"
            + (("，" + "，".join(links)) if links else "")
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Lookup traceable main-statement data.")
    parser.add_argument("--company", required=True)
    parser.add_argument("--metric", required=True)
    parser.add_argument("--report-id", default="")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    args = parser.parse_args(argv)

    result = resolve_statement_metrics(args.company, args.metric, args.report_id or None)
    if args.format == "markdown":
        print(render_markdown(result))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("tables") else 1


if __name__ == "__main__":
    raise SystemExit(main())
