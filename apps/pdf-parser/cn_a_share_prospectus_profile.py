"""Conservative quality signals for the A-share prospectus parser profile."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

DOCUMENT_PROFILE = "cn_a_share_prospectus"
PROFILE_VERSION = "cn_a_share_prospectus_v1"
MIN_REPORTING_YEARS = 3

SECTION_RULES = (
    ("offering_overview", "本次发行概况", (r"本次发行(?:的)?基本情况", r"本次发行概况", r"发行概况")),
    ("risk_factors", "风险因素", (r"风险因素", r"重大风险提示")),
    ("issuer_overview", "发行人基本情况", (r"发行人基本情况", r"发行人概况")),
    ("business_and_technology", "业务与技术", (r"业务与技术", r"主营业务", r"发行人业务")),
    ("corporate_governance", "公司治理", (r"公司治理", r"独立性", r"同业竞争与关联交易")),
    (
        "financial_analysis",
        "财务会计信息与管理层分析",
        (r"财务会计信息", r"管理层讨论与分析", r"财务状况与盈利能力"),
    ),
    ("use_of_proceeds", "募集资金运用", (r"募集资金运用", r"募集资金用途", r"募集资金投资项目")),
)

PAGE_MARKER_RE = re.compile(
    r"(?m)^[ \t]*(?:<!--\s*PDF_PAGE:\s*(\d+)\s*-->|\[PDF_PAGE:\s*(\d+)\])\s*$"
)
REPORTING_PERIOD_PATTERNS = (
    re.compile(r"(?<!\d)((?:20)\d{2})\s*年度"),
    re.compile(r"(?<!\d)((?:20)\d{2})\s*年\s*12\s*月\s*31\s*日"),
    re.compile(r"(?<!\d)((?:20)\d{2})[-/.]12[-/.]31"),
)


def _read_text(result_dir: Path) -> str:
    for name in ("result_complete.md", "result.md"):
        path = result_dir / name
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if text.strip():
            return text
    return ""


def _read_json(result_dir: Path, name: str) -> Any:
    path = result_dir / name
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as infile:
            return json.load(infile)
    except (OSError, json.JSONDecodeError):
        return None


def chapter_coverage(markdown: str) -> dict[str, Any]:
    lines = [line.strip().lstrip("#").strip() for line in str(markdown or "").splitlines()]
    sections = []
    for section_id, title, patterns in SECTION_RULES:
        matched_heading = None
        matched_pattern = None
        for line in lines:
            if not line:
                continue
            for pattern in patterns:
                if re.search(pattern, line):
                    matched_heading = line[:240]
                    matched_pattern = pattern
                    break
            if matched_heading:
                break
        sections.append(
            {
                "section_id": section_id,
                "title": title,
                "matched": bool(matched_heading),
                "matched_heading": matched_heading,
                "matched_pattern": matched_pattern,
            }
        )

    matched_count = sum(1 for section in sections if section["matched"])
    required_count = len(sections)
    ratio = round(matched_count / required_count, 4) if required_count else 0.0
    if ratio >= 0.75:
        status = "pass"
    elif ratio >= 0.5:
        status = "warning"
    else:
        status = "fail"
    return {
        "status": status,
        "matched_count": matched_count,
        "required_count": required_count,
        "coverage_ratio": ratio,
        "missing_section_ids": [section["section_id"] for section in sections if not section["matched"]],
        "sections": sections,
    }


def reporting_period_check(markdown: str, *, minimum_years: int = MIN_REPORTING_YEARS) -> dict[str, Any]:
    matches: dict[int, set[str]] = {}
    for pattern in REPORTING_PERIOD_PATTERNS:
        for match in pattern.finditer(str(markdown or "")):
            year = int(match.group(1))
            matches.setdefault(year, set()).add(match.group(0))

    years = sorted(matches)
    if not years:
        status = "unavailable"
        issues = ["reporting_periods_not_detected"]
    elif len(years) < minimum_years:
        status = "warning"
        issues = ["reporting_period_span_below_expected"]
    else:
        status = "pass"
        issues = []
    return {
        "status": status,
        "years": years,
        "distinct_year_count": len(years),
        "minimum_expected_years": minimum_years,
        "evidence": {str(year): sorted(values)[:5] for year, values in sorted(matches.items())},
        "issues": issues,
    }


def result_capabilities(result_dir: Path, markdown: str) -> dict[str, Any]:
    enhanced = _read_json(result_dir, "content_list_enhanced.json")
    document_full = _read_json(result_dir, "document_full.json")
    financial_data = _read_json(result_dir, "financial_data.json")
    table_index = _read_json(result_dir, "table_index.json")

    pages = enhanced.get("pages") if isinstance(enhanced, dict) else None
    tables = enhanced.get("tables") if isinstance(enhanced, dict) else None
    statements = financial_data.get("statements") if isinstance(financial_data, dict) else None
    page_markers = {
        int(first or second)
        for first, second in PAGE_MARKER_RE.findall(markdown)
        if first or second
    }
    document_markdown = ""
    if isinstance(document_full, dict) and isinstance(document_full.get("markdown"), dict):
        document_markdown = str(document_full["markdown"].get("content") or "")

    return {
        "text_extraction": {
            "available": bool(markdown.strip() or document_markdown.strip()),
            "markdown_chars": max(len(markdown), len(document_markdown)),
        },
        "page_trace": {
            "available": bool(page_markers or pages),
            "page_marker_count": len(page_markers),
            "structured_page_count": len(pages) if isinstance(pages, list) else 0,
        },
        "tables": {
            "available": bool(tables or table_index),
            "table_count": max(
                len(tables) if isinstance(tables, list) else 0,
                len(table_index) if isinstance(table_index, list) else 0,
            ),
        },
        "financial_statements": {
            "available": bool(statements),
            "statement_count": len(statements) if isinstance(statements, list) else 0,
        },
    }


def build_profile_analysis(document_profile: str | None, result_dir: Path) -> dict[str, Any] | None:
    if document_profile != DOCUMENT_PROFILE:
        return None

    markdown = _read_text(result_dir)
    coverage = chapter_coverage(markdown)
    periods = reporting_period_check(markdown)
    capabilities = result_capabilities(result_dir, markdown)
    capabilities["prospectus_chapter_coverage"] = {
        "available": coverage["matched_count"] > 0,
        "coverage_ratio": coverage["coverage_ratio"],
    }
    capabilities["reporting_periods"] = {
        "available": periods["distinct_year_count"] > 0,
        "distinct_year_count": periods["distinct_year_count"],
    }

    issues = [f"missing_section:{section_id}" for section_id in coverage["missing_section_ids"]]
    issues.extend(periods["issues"])
    if coverage["status"] == "fail":
        quality_status = "fail"
    elif coverage["status"] == "warning" or periods["status"] != "pass":
        quality_status = "warning"
    else:
        quality_status = "pass"
    return {
        "profile": DOCUMENT_PROFILE,
        "profile_version": PROFILE_VERSION,
        "quality_status": quality_status,
        "issues": issues,
        "chapter_coverage": coverage,
        "reporting_period_check": periods,
        "capabilities": capabilities,
    }
