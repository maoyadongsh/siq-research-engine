#!/usr/bin/env python3
"""Evaluate parsed annual-report artifacts with artifact-based quality proxies.

This script is intentionally conservative: it does not claim to measure true
PDF-to-Markdown accuracy without human labels. Instead, it turns the artifacts
we already keep (Markdown, content_list, table index, structured financial data
and financial checks) into repeatable quality signals that are useful for
regression testing and prioritizing manual review.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import statistics
import sys
from zoneinfo import ZoneInfo


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

import app  # noqa: E402
from financial_extractor import build_financial_checks, build_financial_data  # noqa: E402


TEXT_BLOCK_TYPES = {
    "text",
    "title",
    "list",
    "list_item",
    "paragraph",
    "caption",
    "footnote",
}
CORE_TABLES_BY_REPORT_KIND = {
    "annual_report": {
        "主要会计数据",
        "主要财务指标",
        "非经常性损益",
        "资产负债表",
        "利润表",
        "现金流量表",
        "所有者权益变动表",
    },
    "quarterly_report": {
        "主要会计数据",
        "主要财务指标",
        "资产负债表",
        "利润表",
        "现金流量表",
    },
}
NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?(?![A-Za-z0-9])"
)
SUPERSCRIPT_FOOTNOTE_REF_RE = re.compile(r"[\u00b9\u00b2\u00b3\u2070-\u2079]")
INLINE_FOOTNOTE_REF_RE = re.compile(r"(?<=[\u4e00-\u9fffA-Za-z])[1-9](?=[\u4e00-\u9fff])")
FOOTNOTE_DEF_RE = re.compile(r"^\s*(?:注|注释|说明)?\s*(?:[\u00b9\u00b2\u00b3\u2070-\u2079]|[1-9][\.、）)])\s*")
INLINE_FOOTNOTE_PREV_EXCLUDE = set("第表图附注")
INLINE_FOOTNOTE_NEXT_EXCLUDE = set("页章节条款项年月日号个亿万元股倍")
TABLE_RE = re.compile(r"<table\b.*?</table>", flags=re.IGNORECASE | re.DOTALL)


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _safe_div(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def _normalize_for_match(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _normalize_number(value: str) -> str:
    return (value or "").replace(",", "").strip()


def _text_from_block(block: dict) -> str:
    parts: list[str] = []
    for key in ("text", "content", "table_caption", "caption", "table_footnote", "footnote"):
        value = block.get(key)
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, list):
            parts.extend(str(item) for item in value if item is not None)
    return " ".join(parts)


def _json_artifact(path: Path):
    if not path.exists():
        return None
    try:
        return app._coerce_json_artifact(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return None


def _parse_since(value: str | None, tz_name: str) -> datetime | None:
    if not value:
        return None
    raw = value.strip()
    try:
        if raw.endswith("Z"):
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        parsed = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(tz_name))
    return parsed.astimezone(timezone.utc)


def _task_completed_at(row: dict) -> datetime | None:
    value = row.get("completed_at") or row.get("created_at")
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _task_rows(
    db_path: Path,
    limit: int,
    task_ids: set[str] | None = None,
    since: datetime | None = None,
) -> list[dict]:
    if not db_path.exists():
        return []
    query = """
        SELECT task_id, filename, status, created_at, completed_at, pdf_page_count,
               markdown_path, upload_path
        FROM tasks
        WHERE status IN ('completed', 'done', 'success')
          AND COALESCE(cancelled, 0) = 0
    """
    params: list[object] = []
    if task_ids:
        placeholders = ",".join("?" for _ in task_ids)
        query += f" AND task_id IN ({placeholders})"
        params.extend(sorted(task_ids))
    query += " ORDER BY COALESCE(completed_at, created_at) DESC"
    if limit and not since:
        query += " LIMIT ?"
        params.append(limit)
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = [dict(row) for row in conn.execute(query, params).fetchall()]
    except sqlite3.Error:
        return []
    if since:
        rows = [row for row in rows if (_task_completed_at(row) and _task_completed_at(row) >= since)]
        if limit:
            rows = rows[:limit]
    return rows


def _result_paths(task: dict, results_dir: Path) -> tuple[Path, Path]:
    task_id = str(task.get("task_id") or "")
    md_path = Path(task["markdown_path"]) if task.get("markdown_path") else results_dir / task_id / "result.md"
    result_dir = md_path.parent if md_path.name == "result.md" else results_dir / task_id
    return result_dir, md_path


def _content_text_recall(markdown: str, content_list) -> dict:
    if not isinstance(content_list, list):
        return {"score": 0.0, "total": 0, "hits": 0}
    normalized_md = _normalize_for_match(app._strip_html(markdown or ""))
    total = 0
    hits = 0
    for block in content_list:
        if not isinstance(block, dict):
            continue
        if str(block.get("type") or "").lower() not in TEXT_BLOCK_TYPES:
            continue
        text = _normalize_for_match(_text_from_block(block))
        if len(text) < 12:
            continue
        total += 1
        needle = text[:120]
        if needle and needle in normalized_md:
            hits += 1
            continue
        # Long blocks can differ in whitespace or punctuation; a mid-block
        # snippet is often a better recall signal than the full text.
        mid = len(text) // 2
        snippet = text[max(0, mid - 40) : mid + 40]
        if len(snippet) >= 20 and snippet in normalized_md:
            hits += 1
    return {"score": round(_safe_div(hits, total), 4), "total": total, "hits": hits}


def _number_recall(markdown: str, content_list) -> dict:
    normalized_md_numbers = Counter(_normalize_number(item) for item in NUMBER_RE.findall(markdown or ""))
    source_numbers: Counter[str] = Counter()
    if isinstance(content_list, list):
        for block in content_list:
            if not isinstance(block, dict):
                continue
            text = _text_from_block(block)
            if block.get("type") == "table":
                text += " " + str(block.get("table_body") or "")
            for number in NUMBER_RE.findall(text):
                normalized = _normalize_number(number)
                if normalized:
                    source_numbers[normalized] += 1
    total = sum(source_numbers.values())
    hits = 0
    remaining = normalized_md_numbers.copy()
    for number, count in source_numbers.items():
        matched = min(count, remaining.get(number, 0))
        hits += matched
        remaining[number] -= matched
    return {"score": round(_safe_div(hits, total), 4), "total": total, "hits": hits}


def _page_marker_coverage(markdown: str, pdf_page_count: int | None) -> dict:
    markers = {
        int(match.group(1))
        for match in re.finditer(r"\[PDF_PAGE:\s*(\d+)\]", markdown or "")
        if match.group(1).isdigit()
    }
    expected = int(pdf_page_count or 0)
    if expected <= 0:
        return {"score": 0.0, "pages_with_marker": len(markers), "expected_pages": 0}
    return {
        "score": round(_safe_div(len(markers), expected), 4),
        "pages_with_marker": len(markers),
        "expected_pages": expected,
    }


def _heading_score(markdown: str) -> dict:
    lines = (markdown or "").splitlines()
    heading_count = sum(1 for line in lines if line.lstrip().startswith("#"))
    table_count = len(TABLE_RE.findall(markdown or ""))
    # 财报正文天然表多标题少，这里只做很弱的结构代理分。
    expected = max(8, table_count // 30)
    return {
        "score": round(_clamp(_safe_div(heading_count, expected)), 4),
        "heading_count": heading_count,
        "expected_floor": expected,
    }


def _footnote_score(markdown: str) -> dict:
    lines = (markdown or "").splitlines()
    superscript_refs = len(SUPERSCRIPT_FOOTNOTE_REF_RE.findall(markdown or ""))
    inline_refs = 0
    for match in INLINE_FOOTNOTE_REF_RE.finditer(markdown or ""):
        prev_char = markdown[match.start() - 1] if match.start() > 0 else ""
        next_char = markdown[match.end()] if match.end() < len(markdown) else ""
        if prev_char in INLINE_FOOTNOTE_PREV_EXCLUDE or next_char in INLINE_FOOTNOTE_NEXT_EXCLUDE:
            continue
        inline_refs += 1
    defs = sum(1 for line in lines if FOOTNOTE_DEF_RE.search(line))
    refs = superscript_refs + inline_refs
    if refs == 0:
        return {
            "score": 1.0,
            "refs": 0,
            "superscript_refs": 0,
            "inline_refs": 0,
            "definitions": defs,
            "applicable": False,
        }
    # Pure inline digit heuristics are noisy in annual reports: section titles,
    # page references and numbered terms often look like converted footnotes.
    # If the candidate volume is very high and there are no real superscripts,
    # mark the signal as not applicable instead of polluting the score.
    if superscript_refs == 0 and inline_refs > 80:
        return {
            "score": 1.0,
            "refs": refs,
            "superscript_refs": superscript_refs,
            "inline_refs": inline_refs,
            "definitions": defs,
            "applicable": False,
            "note": "inline_footnote_heuristic_too_noisy",
        }
    score = _clamp(_safe_div(defs, refs))
    return {
        "score": round(score, 4),
        "refs": refs,
        "superscript_refs": superscript_refs,
        "inline_refs": inline_refs,
        "definitions": defs,
        "applicable": True,
    }


def _core_candidate_score(report: dict, report_kind: str) -> dict:
    expected_names = CORE_TABLES_BY_REPORT_KIND.get(report_kind, CORE_TABLES_BY_REPORT_KIND["annual_report"])
    candidates = report.get("core_financial_table_candidates") or []
    found = {
        item.get("name")
        for item in candidates
        if item.get("name") in expected_names and item.get("status") == "found"
    }
    high = {
        item.get("name")
        for item in candidates
        if item.get("name") in expected_names
        and item.get("status") == "found"
        and item.get("confidence") == "high"
    }
    return {
        "score": round(0.65 * _safe_div(len(found), len(expected_names)) + 0.35 * _safe_div(len(high), len(expected_names)), 4),
        "expected": sorted(expected_names),
        "found": sorted(found),
        "high": sorted(high),
        "missing": sorted(expected_names - found),
    }


def _score_task(task: dict, results_dir: Path) -> dict:
    result_dir, md_path = _result_paths(task, results_dir)
    if not md_path.exists():
        return {
            "task_id": task.get("task_id"),
            "filename": task.get("filename"),
            "status": "missing_markdown",
        }
    markdown = md_path.read_text(encoding="utf-8", errors="ignore")
    content_list = _json_artifact(result_dir / "content_list.json")
    if content_list is None:
        content_list = app._load_json_artifact(task, "content_list.json")
    enhanced = app._build_content_list_enhanced(
        markdown,
        content_list=content_list,
        report_year=app._detect_report_year(markdown, file_name=task.get("filename")),
    )
    report = app._build_quality_report(
        markdown,
        task,
        file_name=task.get("filename"),
        content_list=content_list,
    )
    financial_data = build_financial_data(
        markdown,
        task_id=task.get("task_id"),
        filename=task.get("filename"),
    )
    financial_checks = build_financial_checks(financial_data)
    report = app._merge_quality_candidates_from_financial_data(report, financial_data)
    report["financial_summary"] = financial_checks.get("summary", {})
    report["financial_overall_status"] = financial_checks.get("overall_status")
    report["financial_statement_count"] = financial_data.get("summary", {}).get("statement_count", 0)
    report["financial_key_metric_count"] = financial_data.get("summary", {}).get("key_metric_count", 0)
    report["warnings"] = app._quality_report_warnings(report, financial_data)

    table_count = int(enhanced.get("table_count") or report.get("table_count") or 0)
    source_counts = Counter(enhanced.get("source_counts") or {})
    exact_tables = int(source_counts.get("content_list_body_exact") or 0)
    inferred_tables = int(source_counts.get("markdown_marker_inferred") or 0)
    missing_page_tables = sum(1 for item in report.get("table_index") or [] if not item.get("pdf_page_number"))
    bbox_tables = sum(1 for item in report.get("table_index") or [] if item.get("bbox"))
    suspicious_tables = len(report.get("suspicious_tables") or [])

    exact_rate = _safe_div(exact_tables, table_count)
    page_rate = 1.0 - _safe_div(missing_page_tables, table_count)
    bbox_rate = _safe_div(bbox_tables, table_count)
    suspicious_penalty = min(0.18, _safe_div(suspicious_tables, max(table_count, 1)) * 1.5)
    report_kind = financial_data.get("report_kind") or report.get("report_kind") or "annual_report"
    core_score = _core_candidate_score(report, report_kind)
    table_score = _clamp(
        0.50 * exact_rate
        + 0.20 * bbox_rate
        + 0.15 * page_rate
        + 0.15 * core_score["score"]
        - suspicious_penalty
    )

    text_recall = _content_text_recall(markdown, content_list)
    marker_coverage = _page_marker_coverage(markdown, task.get("pdf_page_count"))
    heading = _heading_score(markdown)
    structure_score = _clamp(0.55 * core_score["score"] + 0.25 * marker_coverage["score"] + 0.20 * heading["score"])
    footnotes = _footnote_score(markdown)
    numbers = _number_recall(markdown, content_list)

    financial_summary = financial_checks.get("summary") or {}
    fail_count = int(financial_summary.get("fail") or 0)
    warning_count = int(financial_summary.get("warning") or 0)
    skipped_count = int(financial_summary.get("skipped") or 0)
    total_checks = int(financial_summary.get("total") or 0)
    financial_pass_score = 0.0 if fail_count else 1.0
    skipped_penalty = min(0.2, _safe_div(skipped_count, total_checks) * 0.25) if total_checks else 0.0
    number_score = _clamp(0.65 * numbers["score"] + 0.30 * financial_pass_score + 0.05 * (1.0 - skipped_penalty))

    total_score = _clamp(
        0.35 * table_score
        + 0.25 * text_recall["score"]
        + 0.15 * structure_score
        + 0.15 * footnotes["score"]
        + 0.10 * number_score
    )

    flags: list[dict] = []
    if fail_count:
        flags.append({"severity": "high", "type": "financial_check_fail", "message": f"{fail_count} 条财务勾稽硬失败"})
    if report_kind == "annual_report" and core_score["missing"]:
        flags.append({"severity": "high", "type": "missing_core_tables", "message": "完整年报核心表缺失: " + ", ".join(core_score["missing"])})
    if missing_page_tables:
        flags.append({"severity": "high", "type": "missing_table_pages", "message": f"{missing_page_tables} 张表缺页码"})
    if table_score < 0.62:
        flags.append({"severity": "medium", "type": "low_table_score", "message": f"表格代理分偏低: {table_score:.2f}"})
    if text_recall["total"] and text_recall["score"] < 0.88:
        flags.append({"severity": "medium", "type": "low_text_recall", "message": f"content_list 文本块召回偏低: {text_recall['score']:.2f}"})
    if footnotes["applicable"] and footnotes["score"] < 0.5:
        flags.append({"severity": "medium", "type": "weak_footnote_binding", "message": f"疑似脚注引用 {footnotes['refs']} 个，定义 {footnotes['definitions']} 个"})
    if warning_count >= 5:
        flags.append({"severity": "low", "type": "many_financial_warnings", "message": f"{warning_count} 条财务 warning"})
    if exact_rate < 0.55 and table_count >= 20:
        flags.append({"severity": "low", "type": "low_exact_source_rate", "message": f"content_list 精确表格覆盖率 {exact_rate:.2%}"})

    return {
        "task_id": task.get("task_id"),
        "filename": task.get("filename"),
        "status": "ok",
        "completed_at": task.get("completed_at"),
        "report_kind": report_kind,
        "markdown_chars": len(markdown),
        "pdf_page_count": task.get("pdf_page_count"),
        "table_count": table_count,
        "source_counts": dict(source_counts),
        "rates": {
            "exact_table_rate": round(exact_rate, 4),
            "inferred_table_rate": round(_safe_div(inferred_tables, table_count), 4),
            "bbox_rate": round(bbox_rate, 4),
            "table_page_rate": round(page_rate, 4),
        },
        "scores": {
            "table_score": round(table_score, 4),
            "text_score": text_recall["score"],
            "structure_score": round(structure_score, 4),
            "footnote_score": footnotes["score"],
            "number_score": round(number_score, 4),
            "total_score": round(total_score, 4),
        },
        "signals": {
            "content_text_recall": text_recall,
            "number_recall": numbers,
            "page_marker_coverage": marker_coverage,
            "heading_score": heading,
            "core_candidate_score": core_score,
            "footnotes": footnotes,
            "suspicious_tables": suspicious_tables,
            "missing_page_tables": missing_page_tables,
        },
        "financial": {
            "overall_status": financial_checks.get("overall_status"),
            "summary": financial_summary,
            "statement_count": financial_data.get("summary", {}).get("statement_count", 0),
            "key_metric_count": financial_data.get("summary", {}).get("key_metric_count", 0),
            "warnings": (financial_checks.get("warnings") or [])[:10],
        },
        "flags": flags,
    }


def _score_stats(values: list[float]) -> dict:
    if not values:
        return {"avg": 0, "min": 0, "median": 0}
    return {
        "avg": round(statistics.fmean(values), 4),
        "min": round(min(values), 4),
        "median": round(statistics.median(values), 4),
    }


def _summarize(items: list[dict]) -> dict:
    ok_items = [item for item in items if item.get("status") == "ok"]
    source_counts = Counter()
    report_kinds = Counter()
    financial_status = Counter()
    flag_counts = Counter()
    severity_counts = Counter()
    for item in ok_items:
        source_counts.update(item.get("source_counts") or {})
        report_kinds[item.get("report_kind") or "unknown"] += 1
        financial_status[(item.get("financial") or {}).get("overall_status") or "unknown"] += 1
        for flag in item.get("flags") or []:
            flag_counts[flag.get("type") or "unknown"] += 1
            severity_counts[flag.get("severity") or "unknown"] += 1
    score_names = ["table_score", "text_score", "structure_score", "footnote_score", "number_score", "total_score"]
    scores = {
        name: _score_stats([float((item.get("scores") or {}).get(name) or 0) for item in ok_items])
        for name in score_names
    }
    table_count = sum(int(item.get("table_count") or 0) for item in ok_items)
    missing_pages = sum(int((item.get("signals") or {}).get("missing_page_tables") or 0) for item in ok_items)
    bbox_tables = sum(int(item.get("rates", {}).get("bbox_rate", 0) * int(item.get("table_count") or 0)) for item in ok_items)
    financial_checks_total = 0
    financial_checks_fail = 0
    financial_checks_warning = 0
    financial_checks_skipped = 0
    for item in ok_items:
        summary = ((item.get("financial") or {}).get("summary") or {})
        financial_checks_total += int(summary.get("total") or 0)
        financial_checks_fail += int(summary.get("fail") or 0)
        financial_checks_warning += int(summary.get("warning") or 0)
        financial_checks_skipped += int(summary.get("skipped") or 0)
    return {
        "tasks": len(items),
        "ok_tasks": len(ok_items),
        "error_tasks": len(items) - len(ok_items),
        "table_count": table_count,
        "missing_page_tables": missing_pages,
        "bbox_tables": bbox_tables,
        "source_counts": dict(source_counts),
        "source_rates": {
            "exact_rate": round(_safe_div(source_counts.get("content_list_body_exact", 0), table_count), 4),
            "inferred_rate": round(_safe_div(source_counts.get("markdown_marker_inferred", 0), table_count), 4),
            "missing_page_rate": round(_safe_div(missing_pages, table_count), 4),
        },
        "report_kinds": dict(report_kinds),
        "financial_status": dict(financial_status),
        "financial_checks": {
            "total": financial_checks_total,
            "fail": financial_checks_fail,
            "warning": financial_checks_warning,
            "skipped": financial_checks_skipped,
        },
        "scores": scores,
        "flag_counts": dict(flag_counts),
        "severity_counts": dict(severity_counts),
        "lowest_score_tasks": [
            {
                "task_id": item.get("task_id"),
                "filename": item.get("filename"),
                "total_score": (item.get("scores") or {}).get("total_score"),
                "table_score": (item.get("scores") or {}).get("table_score"),
                "flags": item.get("flags"),
            }
            for item in sorted(ok_items, key=lambda x: (x.get("scores") or {}).get("total_score", 0))[:15]
        ],
    }


def _write_markdown_report(payload: dict, path: Path) -> None:
    summary = payload.get("summary") or {}
    scores = summary.get("scores") or {}
    lines = [
        "# 解析质量量化评测报告",
        "",
        "本报告使用现有解析产物计算代理指标，不等同于人工标注的 PDF 真值评测。",
        "",
        "## 总览",
        "",
        f"- 任务数：`{summary.get('tasks', 0)}`",
        f"- 成功评测任务：`{summary.get('ok_tasks', 0)}`",
        f"- Markdown 表格数：`{summary.get('table_count', 0)}`",
        f"- 缺页码表格：`{summary.get('missing_page_tables', 0)}`",
        f"- 财务勾稽硬失败：`{(summary.get('financial_checks') or {}).get('fail', 0)}`",
        f"- 财务勾稽 warning：`{(summary.get('financial_checks') or {}).get('warning', 0)}`",
        "",
        "## 分数",
        "",
        "| 指标 | 平均 | 中位数 | 最低 |",
        "| --- | ---: | ---: | ---: |",
    ]
    for name, label in [
        ("table_score", "表格代理分"),
        ("text_score", "文本召回代理分"),
        ("structure_score", "结构代理分"),
        ("footnote_score", "脚注代理分"),
        ("number_score", "数字代理分"),
        ("total_score", "总分"),
    ]:
        stat = scores.get(name) or {}
        lines.append(f"| {label} | {stat.get('avg', 0)} | {stat.get('median', 0)} | {stat.get('min', 0)} |")
    lines.extend(
        [
            "",
            "## 来源覆盖",
            "",
            f"- `content_list_body_exact`：`{(summary.get('source_counts') or {}).get('content_list_body_exact', 0)}`",
            f"- `markdown_marker_inferred`：`{(summary.get('source_counts') or {}).get('markdown_marker_inferred', 0)}`",
            f"- 精确表格覆盖率：`{(summary.get('source_rates') or {}).get('exact_rate', 0)}`",
            f"- 页码推断覆盖率：`{(summary.get('source_rates') or {}).get('inferred_rate', 0)}`",
            "",
            "## 风险标记",
            "",
        ]
    )
    flag_counts = summary.get("flag_counts") or {}
    if flag_counts:
        for key, value in sorted(flag_counts.items(), key=lambda item: (-item[1], item[0])):
            lines.append(f"- `{key}`：`{value}`")
    else:
        lines.append("- 未发现高优先级风险标记。")
    lines.extend(["", "## 低分任务", ""])
    for item in summary.get("lowest_score_tasks") or []:
        flag_text = "; ".join(flag.get("message", "") for flag in item.get("flags") or []) or "无"
        lines.append(
            f"- `{item.get('total_score')}` / 表格 `{item.get('table_score')}` / {item.get('filename')} / {flag_text}"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", default=app.RESULTS_FOLDER, help="Parsed results directory.")
    parser.add_argument("--db", default=app.DB_PATH, help="Task database path.")
    parser.add_argument("--limit", type=int, default=110, help="Maximum recent tasks to evaluate.")
    parser.add_argument("--since", default="", help="Only evaluate tasks completed after this local/ISO time.")
    parser.add_argument("--timezone", default="Asia/Shanghai", help="Timezone for --since when it has no timezone.")
    parser.add_argument("--task-id", action="append", default=[], help="Only evaluate this task id. Can be repeated.")
    parser.add_argument("--output-dir", default=str(BASE_DIR / "reports"), help="Directory for JSON and Markdown reports.")
    parser.add_argument("--details", action="store_true", help="Print per-task JSON lines before the summary.")
    args = parser.parse_args(argv)

    results_dir = Path(args.results_dir)
    app.RESULTS_FOLDER = str(results_dir)
    since = _parse_since(args.since, args.timezone)
    task_ids = set(args.task_id) if args.task_id else None
    rows = _task_rows(Path(args.db), args.limit, task_ids=task_ids, since=since)

    items = []
    for task in rows:
        item = _score_task(task, results_dir)
        items.append(item)
        if args.details:
            print(json.dumps(item, ensure_ascii=False))

    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "parameters": {
            "limit": args.limit,
            "since": args.since,
            "timezone": args.timezone,
            "task_ids": sorted(task_ids) if task_ids else [],
        },
        "summary": _summarize(items),
        "tasks": items,
    }

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(ZoneInfo(args.timezone)).strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"parse_quality_eval_{stamp}.json"
    md_path = output_dir / f"parse_quality_eval_{stamp}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_markdown_report(payload, md_path)
    print(json.dumps({"json": str(json_path), "markdown": str(md_path), "summary": payload["summary"]}, ensure_ascii=False, indent=2))
    return 1 if payload["summary"].get("financial_checks", {}).get("fail") else 0


if __name__ == "__main__":
    raise SystemExit(main())
