#!/usr/bin/env python3
"""Audit Wiki-first traceability for FinSight company knowledge.

This is a read-only gate for QA/analysis quality. It treats report.md
``[PDF_PAGE:n]`` markers as the authoritative text-context page anchor and
keeps structured table links separately, because a table can legitimately be
rendered from a neighboring PDF page while the citing markdown line belongs to
the previous page context.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

DEFAULT_WIKI_ROOT = Path("/home/maoyd/wiki")
AUDITED_FILES = (
    "evidence/evidence_index.json",
    "evidence/pdf_refs.json",
    "metrics/key_metrics.json",
    "metrics/three_statements.json",
    "semantic/evidence_semantic.json",
    "semantic/document_links.json",
    "semantic/note_links.json",
    "semantic/facts.json",
    "semantic/claims.json",
    "semantic/segments.json",
)


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def to_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def report_for_company(company_dir: Path) -> dict[str, Any]:
    company = read_json(company_dir / "company.json", {}) or {}
    report_id = company.get("primary_report_id") or "2025-annual"
    reports = company.get("reports") or []
    report = next((item for item in reports if item.get("report_id") == report_id), None)
    if not report and reports:
        report = reports[0]
        report_id = report.get("report_id") or report_id
    return {
        "company": company,
        "report": report or {},
        "report_id": report_id,
        "task_id": (report or {}).get("task_id") or company.get("task_id"),
        "report_md": company_dir / "reports" / report_id / "report.md",
        "document_full": company_dir / "reports" / report_id / "document_full.json",
        "report_json": company_dir / "reports" / report_id / "report.json",
    }


def markdown_page_at_line(report_md: Path, line_number: int | None) -> int | None:
    if line_number is None or not report_md.exists():
        return None
    lines = report_md.read_text(encoding="utf-8", errors="ignore").splitlines()
    if not lines:
        return None
    index = max(0, min(int(line_number) - 1, len(lines) - 1))
    for current in range(index, -1, -1):
        match = re.search(r"\[PDF_PAGE:\s*(\d+)\]", lines[current])
        if match:
            return int(match.group(1))
    return None


def table_page_map(report_json_path: Path) -> dict[int, int]:
    report_json = read_json(report_json_path, {}) or {}
    output: dict[int, int] = {}
    for table in report_json.get("tables") or []:
        table_index = to_int(table.get("table_index"))
        page = to_int(table.get("pdf_page_number") or table.get("pdf_page"))
        if table_index is not None and page is not None:
            output[table_index] = page
    return output


def iter_trace_records(obj: Any, path: str = ""):
    if isinstance(obj, dict):
        if any(
            key in obj
            for key in (
                "pdf_page",
                "pdf_page_number",
                "md_line",
                "md_line_start",
                "line",
                "markdown_line",
                "table_index",
                "task_id",
            )
        ):
            yield path, obj
        for key, value in obj.items():
            yield from iter_trace_records(value, f"{path}.{key}" if path else key)
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            yield from iter_trace_records(value, f"{path}[{index}]")


def record_line(record: dict[str, Any]) -> int | None:
    source = record.get("source") if isinstance(record.get("source"), dict) else {}
    return to_int(
        record.get("md_line")
        or record.get("md_line_start")
        or record.get("markdown_line")
        or record.get("line")
        or source.get("md_line")
        or source.get("md_line_start")
        or source.get("markdown_line")
        or source.get("line")
    )


def record_page(record: dict[str, Any]) -> int | None:
    source = record.get("source") if isinstance(record.get("source"), dict) else {}
    return to_int(
        record.get("pdf_page")
        or record.get("pdf_page_number")
        or record.get("source_page_number")
        or source.get("pdf_page")
        or source.get("pdf_page_number")
        or source.get("source_page_number")
    )


def record_table(record: dict[str, Any]) -> int | None:
    source = record.get("source") if isinstance(record.get("source"), dict) else {}
    return to_int(record.get("table_index") or record.get("source_table_index") or source.get("table_index"))


def record_task(record: dict[str, Any]) -> str:
    source = record.get("source") if isinstance(record.get("source"), dict) else {}
    return str(record.get("task_id") or source.get("task_id") or "")


def audit_company(company_dir: Path, adjacent_tolerance: int = 2) -> dict[str, Any]:
    info = report_for_company(company_dir)
    table_pages = table_page_map(info["report_json"])
    stats = {
        "company_id": company_dir.name,
        "report_id": info["report_id"],
        "task_id": info["task_id"],
        "has_task_id": bool(info["task_id"]),
        "has_report_md": info["report_md"].exists(),
        "has_document_full": info["document_full"].exists(),
        "records": 0,
        "records_with_line": 0,
        "records_with_page": 0,
        "records_with_task": 0,
        "line_anchor_checked": 0,
        "line_anchor_page_differs": 0,
        "line_anchor_page_differs_tolerated": 0,
        "line_anchor_page_differs_high_risk": 0,
        "table_linkable": 0,
        "table_page_differs_from_anchor": 0,
        "table_page_differs_tolerated": 0,
        "table_page_differs_high_risk": 0,
        "missing_page_with_line": 0,
        "missing_task_with_trace": 0,
        "examples": [],
    }
    for rel_path in AUDITED_FILES:
        path = company_dir / rel_path
        if not path.exists():
            continue
        payload = read_json(path)
        for obj_path, record in iter_trace_records(payload):
            stats["records"] += 1
            line = record_line(record)
            page = record_page(record)
            table_index = record_table(record)
            task_id = record_task(record)
            table_page = table_pages.get(table_index or -1)
            anchor_page = markdown_page_at_line(info["report_md"], line)

            if line is not None:
                stats["records_with_line"] += 1
            if page is not None:
                stats["records_with_page"] += 1
            if task_id or info["task_id"]:
                stats["records_with_task"] += 1
            elif page is not None or table_index is not None or line is not None:
                stats["missing_task_with_trace"] += 1
            if table_index is not None and table_page is not None:
                stats["table_linkable"] += 1

            if line is not None and anchor_page is not None:
                stats["line_anchor_checked"] += 1
                if page is None:
                    stats["missing_page_with_line"] += 1
                elif page != anchor_page:
                    stats["line_anchor_page_differs"] += 1
                    if abs(page - anchor_page) <= adjacent_tolerance:
                        stats["line_anchor_page_differs_tolerated"] += 1
                    else:
                        stats["line_anchor_page_differs_high_risk"] += 1
                    if len(stats["examples"]) < 8:
                        stats["examples"].append(
                            {
                                "file": rel_path,
                                "path": obj_path,
                                "line": line,
                                "field_page": page,
                                "anchor_page": anchor_page,
                                "table_index": table_index,
                                "table_page": table_page,
                            }
                        )
                if table_page is not None and table_page != anchor_page:
                    stats["table_page_differs_from_anchor"] += 1
                    if abs(table_page - anchor_page) <= adjacent_tolerance:
                        stats["table_page_differs_tolerated"] += 1
                    else:
                        stats["table_page_differs_high_risk"] += 1
    return stats


def company_dirs(wiki_root: Path, company: str | None) -> list[Path]:
    companies_root = wiki_root / "companies"
    if company:
        if (companies_root / company).exists():
            return [companies_root / company]
        matches = sorted(companies_root.glob(f"{company}-*")) if company.isdigit() else []
        if matches:
            return [matches[0]]
        matches = sorted(path for path in companies_root.iterdir() if company in path.name)
        return matches[:1]
    return sorted(path for path in companies_root.iterdir() if path.is_dir() and not path.name.startswith("."))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wiki-root", default=str(DEFAULT_WIKI_ROOT))
    parser.add_argument("--company", default="", help="company_id, stock code, or name substring")
    parser.add_argument("--adjacent-tolerance", type=int, default=2, help="Treat page differences within N pages as tolerated cross-page/table drift.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    dirs = company_dirs(Path(args.wiki_root), args.company.strip() or None)
    results = [audit_company(path, adjacent_tolerance=max(0, args.adjacent_tolerance)) for path in dirs]
    totals: dict[str, int] = {}
    for key in (
        "records",
        "records_with_line",
        "records_with_page",
        "records_with_task",
        "line_anchor_checked",
        "line_anchor_page_differs",
        "line_anchor_page_differs_tolerated",
        "line_anchor_page_differs_high_risk",
        "table_linkable",
        "table_page_differs_from_anchor",
        "table_page_differs_tolerated",
        "table_page_differs_high_risk",
        "missing_page_with_line",
        "missing_task_with_trace",
    ):
        totals[key] = sum(int(item.get(key) or 0) for item in results)
    output = {
        "wiki_root": str(Path(args.wiki_root)),
        "company_count": len(results),
        "totals": totals,
        "companies": results,
    }
    if args.json:
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(output["totals"], ensure_ascii=False, indent=2))
        for item in sorted(results, key=lambda row: row["line_anchor_page_differs"], reverse=True)[:10]:
            print(
                f"{item['company_id']}: records={item['records']} "
                f"anchor_diffs={item['line_anchor_page_differs']} "
                f"high_risk_anchor_diffs={item['line_anchor_page_differs_high_risk']} "
                f"table_anchor_diffs={item['table_page_differs_from_anchor']} "
                f"high_risk_table_diffs={item['table_page_differs_high_risk']} "
                f"missing_page_with_line={item['missing_page_with_line']}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
