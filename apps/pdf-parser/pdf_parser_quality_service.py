"""Quality report helpers split out from the Flask app boundary."""

from __future__ import annotations

import os
import re

from quality_engine import candidate_group as quality_candidate_group
from quality_report import CORE_FINANCIAL_TABLE_NAMES


def compact_candidate_text(text):
    return re.sub(r"\s+", "", str(text or ""))


def unique_preserve_order(items):
    seen = set()
    result = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def candidate_signal_text(context, source, table_text):
    caption = " ".join(source.get("caption") or [])
    footnote = " ".join(source.get("footnote") or [])
    heading = context.get("heading") or ""
    near_text = context.get("near_text") or ""
    preview = table_text[:600]
    direct = " ".join(filter(None, [heading, caption, footnote, table_text[:180]]))
    broad = " ".join(filter(None, [direct, near_text[:220], preview]))
    return direct, broad


def candidate_title_text(context, source):
    caption = " ".join(source.get("caption") or [])
    footnote = " ".join(source.get("footnote") or [])
    heading = context.get("heading") or ""
    return " ".join(filter(None, [heading, caption, footnote]))


def table_item_text(table_item):
    return compact_candidate_text(
        " ".join(
            str(part or "")
            for part in (
                table_item.get("heading"),
                table_item.get("preview"),
                table_item.get("text_preview"),
            )
        )
    )


def statement_line_or_table_line(lines, pos, table_item):
    if pos < len(lines) and lines[pos]:
        return lines[pos]
    return table_item.get("line")


def nearest_table_for_statement_lines(report, lines, statement_type):
    if not lines:
        return None
    table_items = [
        item
        for item in (report.get("table_index") or [])
        if isinstance(item, dict) and item.get("table_index") and item.get("line")
    ]
    if not table_items:
        return None

    bad_balance_terms = (
        "平均余额",
        "平均收益率",
        "平均成本率",
        "利息收入/支出",
        "生息资产",
        "计息负债",
    )
    best = None
    min_line = min(
        int(line)
        for line in lines
        if isinstance(line, int) or (isinstance(line, str) and line.isdigit())
    )
    for line in lines:
        try:
            source_line = int(line)
        except (TypeError, ValueError):
            continue
        for table_item in table_items:
            try:
                table_line = int(table_item.get("line") or 0)
            except (TypeError, ValueError):
                continue
            if table_line <= 0:
                continue
            table_text = table_item_text(table_item)
            if statement_type == "balance_sheet" and any(term in table_text for term in bad_balance_terms):
                continue
            distance = abs(source_line - table_line)
            if distance > 40:
                continue
            if statement_type == "balance_sheet":
                has_asset_heading = "资产" in table_text and not any(term in table_text for term in ("负债", "股东权益", "所有者权益"))
                starts_before_first_total = table_line <= min_line
                score = (0 if has_asset_heading else 1, 0 if starts_before_first_total else 1, abs(min_line - table_line), table_line)
            else:
                # Prefer the table that starts immediately before the verified total row.
                direction_penalty = 0 if table_line <= source_line else 1
                score = (distance, direction_penalty, table_line)
            if best is None or score < best["score"]:
                best = {
                    "score": score,
                    "table_index": table_item.get("table_index"),
                    "line": min_line if statement_type == "balance_sheet" else source_line,
                    "table_item": table_item,
                }
    return best


def statement_display_source(statement, report, statement_type):
    indexes = statement.get("table_indexes") or []
    lines = statement.get("line_numbers") or []
    table_lookup = {
        item.get("table_index"): item
        for item in (report.get("table_index") or [])
        if isinstance(item, dict) and item.get("table_index")
    }
    bad_balance_terms = (
        "平均余额",
        "平均收益率",
        "平均成本率",
        "利息收入/支出",
        "生息资产",
        "计息负债",
    )
    fallback_table_item = table_lookup.get(indexes[0]) if indexes else None
    fallback = {
        "table_index": indexes[0] if indexes else None,
        "line": statement_line_or_table_line(lines, 0, fallback_table_item or {}) if indexes else (lines[0] if lines else None),
        "table_item": fallback_table_item,
    }
    if not indexes and lines:
        nearby_table = nearest_table_for_statement_lines(report, lines, statement_type)
        if nearby_table:
            fallback = nearby_table
    for pos, table_index in enumerate(indexes):
        table_item = table_lookup.get(table_index) or {}
        table_text = table_item_text(table_item)
        if statement_type == "balance_sheet" and any(term in table_text for term in bad_balance_terms):
            continue
        return {
            "table_index": table_index,
            "line": statement_line_or_table_line(lines, pos, table_item),
            "table_item": table_item,
        }
    return fallback


def merge_quality_candidates_from_financial_data(report, financial_data):
    if not isinstance(report, dict) or not isinstance(financial_data, dict):
        return report
    statements = financial_data.get("statements") or []
    metrics = financial_data.get("key_metrics") or []
    if not statements:
        statements = []

    existing = report.get("key_table_candidates") or {}
    table_lookup = {
        item.get("table_index"): item
        for item in (report.get("table_index") or [])
        if isinstance(item, dict) and item.get("table_index")
    }
    by_name = {}
    for statement in statements:
        statement_type = statement.get("statement_type")
        scope = statement.get("scope")
        if statement_type == "balance_sheet":
            by_name.setdefault("资产负债表", []).append(statement)
            if scope == "consolidated":
                by_name.setdefault("合并资产负债表", []).append(statement)
            elif scope == "parent_company":
                by_name.setdefault("公司资产负债表", []).append(statement)
        elif statement_type == "income_statement":
            by_name.setdefault("利润表", []).append(statement)
            if scope == "consolidated":
                by_name.setdefault("合并利润表", []).append(statement)
            elif scope == "parent_company":
                by_name.setdefault("公司利润表", []).append(statement)
        elif statement_type == "cash_flow_statement":
            by_name.setdefault("现金流量表", []).append(statement)
            if scope == "consolidated":
                by_name.setdefault("合并现金流量表", []).append(statement)
            elif scope == "parent_company":
                by_name.setdefault("公司现金流量表", []).append(statement)
        elif statement_type == "equity_statement":
            by_name.setdefault("所有者权益变动表", []).append(statement)

    if metrics:
        def _metric_source_for(canonical_names):
            for canonical_name in canonical_names:
                for item in metrics:
                    if item.get("canonical_name") != canonical_name:
                        continue
                    sources = item.get("sources") or {}
                    if sources:
                        return item, next(iter(sources.values()))
            return None, None

        metric_sources = {
            "主要会计数据": _metric_source_for(
                (
                    "operating_revenue",
                    "operating_profit",
                    "total_profit",
                    "parent_net_profit",
                    "operating_cash_flow_net",
                    "total_assets",
                    "total_liabilities",
                    "equity_attributable_parent",
                )
            ),
            "主要财务指标": _metric_source_for(
                (
                    "weighted_avg_roe",
                    "deducted_weighted_avg_roe",
                    "parent_nav_per_share",
                    "basic_eps",
                    "diluted_eps",
                    "deducted_basic_eps",
                )
            ),
        }
        for name, (metric_item, metric_source) in metric_sources.items():
            if metric_source is not None:
                metric_table = table_lookup.get(metric_source.get("table_index")) or {}
                by_name.setdefault(name, []).append(
                    {
                        "name": name,
                        "status": "found",
                        "table_index": metric_source.get("table_index"),
                        "line": metric_source.get("line") or metric_table.get("line"),
                        "pdf_page_number": metric_table.get("pdf_page_number"),
                        "pdf_page_source": metric_table.get("pdf_page_source"),
                        "pdf_page_inference_reason": metric_table.get("pdf_page_inference_reason"),
                        "bbox": metric_table.get("bbox") or [],
                        "rows": metric_table.get("rows"),
                        "cells": metric_table.get("cells"),
                        "empty_ratio": metric_table.get("empty_ratio"),
                        "numeric_ratio": metric_table.get("numeric_ratio"),
                        "heading": metric_table.get("heading") or (metric_item.get("name") if metric_item else name),
                        "unit": (metric_item.get("unit") if metric_item else "") or metric_table.get("unit") or "",
                        "table_type": metric_table.get("table_type") or "fact",
                        "year_binding_required": True,
                        "report_year": financial_data.get("report_year"),
                        "candidate_group": quality_candidate_group(name),
                        "candidate_score": 99.0,
                        "confidence": "high",
                        "preview": metric_table.get("preview") or (metric_item.get("name") if metric_item else name),
                        "is_primary": True,
                        "_source": "financial_data",
                    }
                )

    for name, statement_rows in by_name.items():
        existing_rows = existing.get(name) or []
        if any(
            item.get("status") == "found" and item.get("table_index") and item.get("line")
            for item in existing_rows
        ):
            continue
        fallback_rows = []
        for idx, statement in enumerate(statement_rows, start=1):
            statement_type = statement.get("statement_type")
            display_source = statement_display_source(statement, report, statement_type)
            display_table = display_source.get("table_item") or {}
            if isinstance(statement, dict) and statement.get("status") == "found" and (
                statement.get("table_index") or statement.get("line")
            ):
                fallback = dict(statement)
                fallback.setdefault("name", name)
                fallback.setdefault("candidate_group", quality_candidate_group(name))
                fallback.setdefault("candidate_score", 100.0 - idx)
                fallback.setdefault("confidence", "high")
                fallback.setdefault("is_primary", idx == 1)
                fallback_rows.append(fallback)
                continue
            fallback_rows.append(
                {
                    "name": name,
                    "status": "found",
                    "table_index": display_source.get("table_index"),
                    "line": display_source.get("line"),
                    "pdf_page_number": display_table.get("pdf_page_number"),
                    "pdf_page_source": display_table.get("pdf_page_source"),
                    "pdf_page_inference_reason": display_table.get("pdf_page_inference_reason"),
                    "bbox": display_table.get("bbox") or [],
                    "rows": display_table.get("rows"),
                    "cells": display_table.get("cells"),
                    "empty_ratio": display_table.get("empty_ratio"),
                    "numeric_ratio": display_table.get("numeric_ratio"),
                    "heading": display_table.get("heading") or statement.get("title") or statement.get("statement_name") or name,
                    "unit": statement.get("unit") or "",
                    "table_type": "fact",
                    "year_binding_required": True,
                    "report_year": financial_data.get("report_year"),
                    "candidate_group": "core",
                    "candidate_score": 100.0 - idx,
                    "confidence": "high",
                    "preview": display_table.get("preview") or statement.get("title") or statement.get("statement_name") or name,
                    "is_primary": idx == 1,
                    "_source": "financial_data",
                }
            )
        if fallback_rows:
            existing[name] = fallback_rows

    report["key_table_candidates"] = existing

    financial_names = []
    financial_rows = {}
    for name in CORE_FINANCIAL_TABLE_NAMES:
        rows = existing.get(name) or []
        found_row = next(
            (item for item in rows if item.get("table_index") and item.get("line")),
            None,
        )
        if found_row:
            financial_names.append(name)
            financial_rows[name] = found_row
    report["found_financial_tables"] = financial_names
    core_candidates = []
    for name in CORE_FINANCIAL_TABLE_NAMES:
        row = financial_rows.get(name) or {}
        item = {
            "name": name,
            "status": "found" if name in financial_names else "missing",
            "candidate_group": quality_candidate_group(name),
        }
        if row:
            for key in (
                "table_index",
                "line",
                "pdf_page_number",
                "pdf_page_source",
                "pdf_page_inference_reason",
                "bbox",
                "rows",
                "cells",
                "empty_ratio",
                "numeric_ratio",
                "heading",
                "unit",
                "table_type",
                "year_binding_required",
                "report_year",
                "candidate_score",
                "confidence",
                "preview",
                "_source",
            ):
                if key in row:
                    item[key] = row.get(key)
        core_candidates.append(item)
    report["core_financial_table_candidates"] = core_candidates
    report["report_kind"] = financial_data.get("report_kind") or report.get("report_kind")
    return report


def quality_report_warnings(report, financial_data=None):
    warnings = list(report.get("warnings") or [])
    if financial_data and financial_data.get("summary", {}).get("statement_count", 0) >= 3:
        warnings = [
            item
            for item in warnings
            if "财报核心表标题召回偏少" not in item and "核心表" not in item
        ]
    if report.get("report_kind") in {"annual_report_summary", "interim_report_summary"}:
        warnings = [item for item in warnings if "三大表" not in item and "核心表" not in item]
        if financial_data and financial_data.get("key_metrics"):
            warnings.append("当前文件为报告摘要，已按摘要模式处理主要会计数据/财务指标。")
            if financial_data.get("summary", {}).get("statement_count", 0) == 0:
                warnings.append("摘要文件不提供完整三大表；如需勾稽校验，请切换到年度报告全文。")
    return warnings


def candidate_summary_list(key_table_candidates, names):
    summary = []
    for name in names:
        rows = key_table_candidates.get(name) or []
        if not rows:
            summary.append({"name": name, "status": "missing", "candidate_group": quality_candidate_group(name)})
            continue
        primary = dict(rows[0])
        primary["name"] = name
        primary["status"] = "found"
        primary["candidate_count"] = len(rows)
        summary.append(primary)
    return summary


def required_core_financial_table_names(report_kind):
    if report_kind == "quarterly_report":
        return [name for name in CORE_FINANCIAL_TABLE_NAMES if name != "所有者权益变动表"]
    return list(CORE_FINANCIAL_TABLE_NAMES)


def priority_review_tables(table_index, core_candidates, key_table_candidates):
    lookup = {item.get("table_index"): item for item in table_index}
    priority = []
    seen = set()

    def add_table(table_index_value, extra_reason=None):
        if not table_index_value or table_index_value in seen:
            return
        source = lookup.get(table_index_value)
        if not source:
            return
        item = dict(source)
        reasons = list(item.get("suspect_reasons") or [])
        if extra_reason and extra_reason not in reasons:
            reasons.append(extra_reason)
        if not reasons:
            return
        item["suspect_reasons"] = reasons
        priority.append(item)
        seen.add(table_index_value)

    for candidate in core_candidates:
        if candidate.get("status") != "found":
            continue
        reason = None
        if candidate.get("confidence") == "low":
            reason = "low_confidence_core_candidate"
        elif candidate.get("confidence") == "medium":
            reason = "medium_confidence_core_candidate"
        table_item = lookup.get(candidate.get("table_index"))
        if reason or (table_item and table_item.get("suspect_reasons")):
            add_table(candidate.get("table_index"), reason)

    for rows in key_table_candidates.values():
        for candidate in rows:
            table_item = lookup.get(candidate.get("table_index"))
            if table_item and table_item.get("suspect_reasons"):
                add_table(candidate.get("table_index"))

    for item in table_index:
        if item.get("suspect_reasons"):
            add_table(item.get("table_index"))

    return priority[:30]


def quality_report_path(task, result_dir):
    return os.path.join(result_dir(task), "quality_report.json")


def table_index_path(task, result_dir):
    return os.path.join(result_dir(task), "table_index.json")


def read_quality_report(task, result_dir, read_json_cached):
    report_path = quality_report_path(task, result_dir)
    if os.path.exists(report_path):
        return read_json_cached(report_path)
    return None


def write_quality_report_files(task, report, result_dir, write_json):
    write_json(quality_report_path(task, result_dir), report)
    write_json(table_index_path(task, result_dir), report.get("table_index", []))
