#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "data" / "wiki" / "us"
DEFAULT_REPORT_PATH = DEFAULT_OUTPUT_ROOT / "_meta" / "financial_recognition_audit.json"

CORE_METRICS = (
    "operating_revenue",
    "net_profit",
    "total_assets",
    "total_liabilities",
    "total_equity",
    "operating_cash_flow_net",
)

METRIC_KEYWORDS = {
    "operating_revenue": ("revenue", "revenues", "sales", "net sales", "operating revenue"),
    "net_profit": ("net income", "net earnings", "net loss", "profit loss"),
    "total_assets": ("total assets", "assets"),
    "total_liabilities": ("total liabilities", "liabilities"),
    "total_equity": ("total equity", "stockholders equity", "shareholders equity", "members equity"),
    "total_liabilities_and_equity": (
        "total liabilities and equity",
        "total liabilities and stockholders equity",
        "total liabilities and shareholders equity",
        "liabilities and stockholders equity",
        "liabilities and shareholders equity",
        "liabilities and members equity",
    ),
    "operating_cash_flow_net": ("net cash provided by operating", "net cash used in operating", "operating activities"),
    "gross_profit": ("gross profit",),
    "cost_of_sales": ("cost of sales", "cost of revenue", "cost of goods"),
    "cash_equivalents_ending": ("cash and cash equivalents at end", "cash cash equivalents restricted cash"),
    "cash_equivalents_beginning": ("cash and cash equivalents at beginning", "cash cash equivalents restricted cash"),
}

NON_BLOCKING_WARNING_PATTERNS = (
    "Use standard three-statement bridge checks.",
    "Derived ",
)


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return {} if default is None else default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def parse_csv_set(value: str | None, *, upper: bool = True) -> set[str] | None:
    if not value:
        return None
    items = {item.strip() for item in value.split(",") if item.strip()}
    if upper:
        items = {item.upper() for item in items}
    return items or None


def discover_packages(
    output_root: Path,
    *,
    tickers: set[str] | None = None,
    forms: set[str] | None = None,
    limit: int = 0,
) -> list[Path]:
    packages: list[Path] = []
    for manifest_path in sorted((output_root / "companies").glob("*/reports/*/manifest.json")):
        manifest = read_json(manifest_path, {})
        if manifest.get("market") != "US":
            continue
        ticker = str(manifest.get("ticker") or "").upper()
        form = str(manifest.get("form") or "").upper()
        if tickers and ticker not in tickers:
            continue
        if forms and form not in forms:
            continue
        packages.append(manifest_path.parent)
        if limit and len(packages) >= limit:
            break
    return packages


def audit_packages(
    output_root: Path,
    *,
    tickers: set[str] | None = None,
    forms: set[str] | None = None,
    limit: int = 0,
) -> dict[str, Any]:
    packages = discover_packages(output_root, tickers=tickers, forms=forms, limit=limit)
    items = [_audit_package(package_dir) for package_dir in packages]
    warning_counter: Counter[str] = Counter()
    recognition_counter: Counter[str] = Counter()
    profile_counter: Counter[str] = Counter()
    concept_candidate_counts: Counter[str] = Counter()
    concept_affected_counts: Counter[str] = Counter()
    table_candidate_counts: Counter[str] = Counter()
    bridge_gaps: Counter[str] = Counter()
    for item in items:
        profile_counter[str(item.get("industry_profile") or "unknown")] += 1
        recognition_counter[str(item.get("recognition_status") or "unknown")] += 1
        for warning in item.get("warning_classes") or []:
            warning_counter[str(warning)] += 1
        for candidate in item.get("missing_metric_candidates") or []:
            metric = str(candidate.get("metric"))
            fact_count = int(candidate.get("candidate_fact_count") or 0)
            table_count = int(candidate.get("candidate_table_count") or 0)
            concept_candidate_counts[metric] += fact_count
            table_candidate_counts[metric] += table_count
            if fact_count or table_count:
                concept_affected_counts[metric] += 1
        for gap in item.get("bridge_gaps") or []:
            bridge_gaps[str(gap.get("rule_id"))] += 1
    optimization_queue = _optimization_queue(items, concept_candidate_counts, concept_affected_counts, table_candidate_counts, bridge_gaps)
    return {
        "schema_version": "sec_financial_recognition_audit_v1",
        "market": "US",
        "output_root": str(output_root),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "package_count": len(items),
        "status_counts": dict(recognition_counter),
        "industry_profile_counts": dict(profile_counter),
        "warning_class_counts": dict(warning_counter),
        "concept_candidate_counts": dict(concept_candidate_counts),
        "concept_affected_package_counts": dict(concept_affected_counts),
        "table_candidate_counts": dict(table_candidate_counts),
        "bridge_gap_counts": dict(bridge_gaps),
        "optimization_queue": optimization_queue,
        "items": items,
    }


def _audit_package(package_dir: Path) -> dict[str, Any]:
    manifest = read_json(package_dir / "manifest.json", {})
    quality = read_json(package_dir / "qa" / "quality_report.json", {})
    checks = read_json(package_dir / "metrics" / "financial_checks.json", {})
    financial_data = read_json(package_dir / "metrics" / "financial_data.json", {})
    normalized_metrics = read_json(package_dir / "metrics" / "normalized_metrics.json", {})
    facts = _facts(package_dir)
    document_tables = _document_tables(package_dir)

    present_metrics = _present_metrics(financial_data, normalized_metrics)
    missing_core = [metric for metric in CORE_METRICS if metric not in present_metrics]
    failed_or_warning_checks = [
        check
        for check in checks.get("checks") or []
        if isinstance(check, dict) and str(check.get("status") or "").lower() in {"warning", "fail"}
    ]
    bridge_gaps = _bridge_gaps(failed_or_warning_checks)
    warning_classes = _warning_classes(quality, checks)
    review_policy = checks.get("review_policy") if isinstance(checks.get("review_policy"), dict) else {}
    missing_metric_candidates = [
        _candidate_summary(metric, facts, document_tables)
        for metric in sorted(set(missing_core + _missing_inputs_from_checks(failed_or_warning_checks)))
    ]
    blocking_warning_classes = [
        warning
        for warning in warning_classes
        if warning not in {"non_blocking_profile_note", "derived_metric_notice", "optional_table_count_delta"}
    ]
    if not missing_core and not bridge_gaps and not blocking_warning_classes:
        recognition_status = "ready"
    elif missing_core or any(gap.get("status") == "fail" for gap in bridge_gaps):
        recognition_status = "needs_rule_work"
    else:
        recognition_status = "needs_review"
    return {
        "package_path": repo_relative(package_dir),
        "ticker": manifest.get("ticker"),
        "company_name": manifest.get("company_name"),
        "form": manifest.get("form"),
        "accession_number": manifest.get("accession_number"),
        "industry_profile": manifest.get("industry_profile"),
        "quality_status": quality.get("overall_status") or manifest.get("quality_status"),
        "financial_check_status": checks.get("overall_status"),
        "review_policy": {
            "schema_version": review_policy.get("schema_version"),
            "downgraded_check_count": review_policy.get("downgraded_check_count", 0),
        },
        "recognition_status": recognition_status,
        "present_core_metrics": [metric for metric in CORE_METRICS if metric in present_metrics],
        "missing_core_metrics": missing_core,
        "normalized_metric_count": len(normalized_metrics.get("metrics") or []),
        "xbrl_fact_count": len(facts),
        "full_document_table_count": len(document_tables),
        "warning_classes": warning_classes,
        "bridge_gaps": bridge_gaps[:30],
        "missing_metric_candidates": missing_metric_candidates,
        "advice": _advice(missing_core, bridge_gaps, missing_metric_candidates, warning_classes),
    }


def _facts(package_dir: Path) -> list[dict[str, Any]]:
    payload = read_json(package_dir / "xbrl" / "facts_raw.json", {})
    facts = payload.get("facts") if isinstance(payload, dict) else []
    return [fact for fact in facts if isinstance(fact, dict)]


def _document_tables(package_dir: Path) -> list[dict[str, Any]]:
    payload = read_json(package_dir / "parser" / "document_full.json", {})
    tables = payload.get("tables") if isinstance(payload, dict) else []
    return [table for table in tables if isinstance(table, dict)]


def _present_metrics(financial_data: dict[str, Any], normalized_metrics: dict[str, Any]) -> set[str]:
    present: set[str] = set()
    for statement in financial_data.get("statements") or []:
        if not isinstance(statement, dict):
            continue
        for item in statement.get("items") or []:
            if isinstance(item, dict) and item.get("canonical_name"):
                present.add(str(item["canonical_name"]))
    for bucket in ("key_metrics", "operating_metrics"):
        for item in financial_data.get(bucket) or []:
            if isinstance(item, dict) and item.get("canonical_name"):
                present.add(str(item["canonical_name"]))
    for metric in normalized_metrics.get("metrics") or []:
        if isinstance(metric, dict) and metric.get("canonical_name"):
            present.add(str(metric["canonical_name"]))
    return present


def _bridge_gaps(checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    gaps = []
    for check in checks:
        rule_id = str(check.get("rule_id") or "")
        reason = str(check.get("reason") or "")
        if not rule_id or reason in {"dimension_specific_scope", "alternative_total_liabilities_and_equity_bridge_passed"}:
            continue
        if reason == "incomplete_balance_sheet_period":
            continue
        if str(check.get("status") or "").lower() == "skipped":
            continue
        right = check.get("right") if isinstance(check.get("right"), dict) else {}
        missing = right.get("missing") if isinstance(right.get("missing"), list) else []
        gaps.append(
            {
                "rule_id": rule_id,
                "status": check.get("status"),
                "reason": reason,
                "missing": [str(item) for item in missing],
                "rule_name": check.get("rule_name"),
            }
        )
    return gaps


def _missing_inputs_from_checks(checks: list[dict[str, Any]]) -> list[str]:
    missing: list[str] = []
    for check in checks:
        if check.get("reason") != "missing_inputs":
            continue
        right = check.get("right") if isinstance(check.get("right"), dict) else {}
        for name in right.get("missing") or []:
            if str(name):
                missing.append(str(name))
    return missing


def _warning_classes(quality: dict[str, Any], checks: dict[str, Any]) -> list[str]:
    classes: set[str] = set()
    warnings = [
        *(quality.get("parser_warnings") if isinstance(quality.get("parser_warnings"), list) else []),
        *(quality.get("rule_warnings") if isinstance(quality.get("rule_warnings"), list) else []),
        *(checks.get("warnings") if isinstance(checks.get("warnings"), list) else []),
    ]
    for warning in warnings:
        text = str(warning)
        if text == "Use standard three-statement bridge checks.":
            classes.add("non_blocking_profile_note")
        elif text.startswith("Derived ") and "US metrics" in text:
            classes.add("derived_metric_notice")
        elif "HTML table count" in text and "differs from tables/table_index.json" in text:
            classes.add("optional_table_count_delta")
        elif "No mapped SEC/XBRL facts" in text:
            classes.add("no_mapped_xbrl_facts")
        else:
            classes.add("other_warning")
    critical = quality.get("critical_warnings") if isinstance(quality.get("critical_warnings"), list) else []
    for item in critical:
        if isinstance(item, dict) and item.get("type"):
            classes.add(f"critical:{item['type']}")
    return sorted(classes)


def _candidate_summary(metric: str, facts: list[dict[str, Any]], tables: list[dict[str, Any]]) -> dict[str, Any]:
    keywords = METRIC_KEYWORDS.get(metric) or tuple(_metric_tokens(metric))
    fact_candidates = [
        fact
        for fact in facts
        if _matches_keywords(" ".join(str(fact.get(key) or "") for key in ("concept", "label", "value_text")), keywords)
    ]
    table_candidates = []
    for table in tables:
        text = " ".join(
            [
                str(table.get("heading") or ""),
                str(table.get("section_id") or ""),
                str(table.get("title") or ""),
                " ".join(_row_texts(table)[:8]),
            ]
        )
        if _matches_keywords(text, keywords):
            table_candidates.append(table)
    return {
        "metric": metric,
        "keywords": list(keywords),
        "candidate_fact_count": len(fact_candidates),
        "candidate_table_count": len(table_candidates),
        "sample_facts": [_fact_sample(fact) for fact in fact_candidates[:8]],
        "sample_tables": [_table_sample(table) for table in table_candidates[:5]],
    }


def _matches_keywords(text: str, keywords: tuple[str, ...]) -> bool:
    compact = re.sub(r"[^a-z0-9]+", "", text.lower())
    for keyword in keywords:
        candidate = re.sub(r"[^a-z0-9]+", "", keyword.lower())
        if candidate and candidate in compact:
            return True
    return False


def _metric_tokens(metric: str) -> list[str]:
    return [part for part in metric.replace("_", " ").split() if len(part) > 2]


def _row_texts(table: dict[str, Any]) -> list[str]:
    rows = table.get("rows") if isinstance(table.get("rows"), list) else []
    texts = []
    for row in rows:
        cells = row.get("cells") if isinstance(row, dict) and isinstance(row.get("cells"), list) else []
        texts.append(" ".join(str(cell.get("text") or "") for cell in cells if isinstance(cell, dict)))
    return texts


def _fact_sample(fact: dict[str, Any]) -> dict[str, Any]:
    return {
        "concept": fact.get("concept"),
        "label": fact.get("label"),
        "value_text": fact.get("value_text"),
        "period_end": fact.get("period_end"),
        "context_ref": fact.get("context_ref"),
        "dimensions": fact.get("dimensions") or {},
        "html_anchor": fact.get("html_anchor"),
    }


def _table_sample(table: dict[str, Any]) -> dict[str, Any]:
    return {
        "table_id": table.get("table_id"),
        "table_index": table.get("table_index"),
        "section_id": table.get("section_id"),
        "heading": str(table.get("heading") or "")[:180],
        "row_count": table.get("row_count"),
        "column_count": table.get("column_count"),
        "fact_count": len(table.get("fact_ids") or []),
    }


def _advice(
    missing_core: list[str],
    bridge_gaps: list[dict[str, Any]],
    missing_metric_candidates: list[dict[str, Any]],
    warning_classes: list[str],
) -> list[str]:
    advice: list[str] = []
    if missing_core:
        advice.append("core_metric_missing: extend US concept aliases or fallback to full-document table/fact relations.")
    if any(item.get("candidate_fact_count") for item in missing_metric_candidates):
        advice.append("candidate_facts_found: likely concept mapping or context-selection issue, not source absence.")
    if any(item.get("candidate_table_count") for item in missing_metric_candidates):
        advice.append("candidate_tables_found: table classifier can be improved using document_full/table_relations.")
    if bridge_gaps:
        advice.append("bridge_gap_present: inspect whether optional bridge should be observe-level or whether a component concept alias is missing.")
    if warning_classes == ["non_blocking_profile_note"] or set(warning_classes).issubset({"non_blocking_profile_note", "derived_metric_notice", "optional_table_count_delta"}):
        advice.append("non_blocking_only: package can be treated as parser/wiki ready; financial review note should not block artifact readiness.")
    return advice


def _optimization_queue(
    items: list[dict[str, Any]],
    concept_gaps: Counter[str],
    concept_affected_counts: Counter[str],
    table_candidate_counts: Counter[str],
    bridge_gaps: Counter[str],
) -> list[dict[str, Any]]:
    queue: list[dict[str, Any]] = []
    for metric, count in concept_gaps.most_common():
        affected = [
            item
            for item in items
            if any(candidate.get("metric") == metric and candidate.get("candidate_fact_count") for candidate in item.get("missing_metric_candidates") or [])
        ]
        if affected:
            queue.append(
                {
                    "type": "concept_mapping_or_context_selection",
                    "metric": metric,
                    "affected_package_count": concept_affected_counts.get(metric, len(affected)),
                    "candidate_fact_count": count,
                    "candidate_table_count": table_candidate_counts.get(metric, 0),
                    "affected_packages": [item.get("package_path") for item in affected[:12]],
                }
            )
    for rule_id, count in bridge_gaps.most_common(20):
        queue.append({"type": "bridge_rule_review", "rule_id": rule_id, "affected_package_count": count})
    return queue


def _summary(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": report.get("schema_version"),
        "market": report.get("market"),
        "report_path": str(DEFAULT_REPORT_PATH),
        "package_count": report.get("package_count"),
        "status_counts": report.get("status_counts"),
        "warning_class_counts": report.get("warning_class_counts"),
        "concept_affected_package_counts": report.get("concept_affected_package_counts"),
        "bridge_gap_counts": report.get("bridge_gap_counts"),
        "optimization_queue": (report.get("optimization_queue") or [])[:10],
    }


def repo_relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit US SEC financial recognition coverage and produce rule-optimization candidates.")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--tickers", default="")
    parser.add_argument("--forms", default="")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--print-full", action="store_true", help="Print the full report JSON instead of a compact summary.")
    args = parser.parse_args()
    report = audit_packages(
        args.output_root,
        tickers=parse_csv_set(args.tickers),
        forms=parse_csv_set(args.forms),
        limit=args.limit,
    )
    write_json(args.report, report)
    payload = report if args.print_full else {**_summary(report), "report_path": str(args.report)}
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
