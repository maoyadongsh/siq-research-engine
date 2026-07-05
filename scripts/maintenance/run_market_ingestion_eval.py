#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
CASE_ROOT = REPO_ROOT / "eval_datasets" / "market_ingestion_cases"
WIKI_ROOTS = {
    "US": REPO_ROOT / "data" / "wiki" / "us",
    "HK": REPO_ROOT / "data" / "wiki" / "hk",
    "JP": REPO_ROOT / "data" / "wiki" / "jp",
    "KR": REPO_ROOT / "data" / "wiki" / "kr",
    "EU": REPO_ROOT / "data" / "wiki" / "eu",
}


def read_json(path: Path, default: Any = None) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else ([] if default is None else default)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    seen: set[tuple[Any, Any, Any, Any]] = set()
    for path in sorted(CASE_ROOT.glob("*_cases.json")):
        payload = read_json(path, [])
        if isinstance(payload, list):
            for item in payload:
                if not isinstance(item, dict):
                    continue
                key = (
                    str(item.get("market") or "").upper(),
                    item.get("country"),
                    item.get("ticker"),
                    item.get("fiscal_year"),
                    item.get("report_type"),
                    item.get("document_format"),
                )
                if key in seen:
                    continue
                seen.add(key)
                cases.append(item)
    return cases


def find_package(case: dict[str, Any]) -> Path | None:
    market = str(case.get("market") or "").upper()
    root = WIKI_ROOTS.get(market)
    if not root:
        return None
    if not root.exists():
        return None
    candidates = []
    for manifest_path in root.rglob("manifest.json"):
        manifest = read_json(manifest_path, {})
        if not isinstance(manifest, dict) or not _manifest_matches_case(manifest, case):
            continue
        candidates.append(manifest_path.parent)
    if not candidates:
        return None
    return sorted(candidates, key=lambda path: path.stat().st_mtime, reverse=True)[0]


def _manifest_matches_case(manifest: dict[str, Any], case: dict[str, Any]) -> bool:
    market = str(case.get("market") or "").upper()
    if str(manifest.get("market") or "").upper() != market:
        return False
    if not _ticker_matches(manifest, case):
        return False
    if not _year_matches(manifest, case):
        return False
    if market == "EU" and case.get("country") and str(manifest.get("country") or "").upper() != str(case.get("country")).upper():
        return False
    return _report_type_matches(manifest, case)


def _ticker_matches(manifest: dict[str, Any], case: dict[str, Any]) -> bool:
    expected = str(case.get("ticker") or case.get("stock_code") or "").strip()
    if not expected:
        return True
    candidates = [
        manifest.get("ticker"),
        manifest.get("stock_code"),
        manifest.get("hkex_stock_code"),
        manifest.get("security_code"),
    ]
    normalized_expected = _normalize_code(expected)
    return any(_normalize_code(value) == normalized_expected for value in candidates if value not in (None, ""))


def _year_matches(manifest: dict[str, Any], case: dict[str, Any]) -> bool:
    expected = str(case.get("fiscal_year") or "").strip()
    if not expected:
        return True
    candidates = [
        manifest.get("fiscal_year"),
        manifest.get("report_year"),
        str(manifest.get("period_end") or "")[:4],
        str(manifest.get("report_id") or "")[:4],
    ]
    return expected in {str(value).strip() for value in candidates if value not in (None, "")}


def _report_type_matches(manifest: dict[str, Any], case: dict[str, Any]) -> bool:
    expected = _normalize_report_type(case.get("report_type"))
    if not expected:
        return True
    candidates = {
        _normalize_report_type(manifest.get("report_type")),
        _normalize_report_type(manifest.get("form")),
    }
    if expected == "annual":
        return bool(candidates.intersection({"annual", "annualsecuritiesreport", "integratedreport", "esef", "10k", "20f"}))
    return expected in candidates


def _normalize_code(value: Any) -> str:
    text = str(value or "").strip().upper()
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits.lstrip("0") or digits or text


def _normalize_report_type(value: Any) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _metric_entries(payload: Any) -> list[Any]:
    if not isinstance(payload, dict):
        return []
    metrics = payload.get("metrics")
    return metrics if isinstance(metrics, list) else []


def evaluate_case(case: dict[str, Any]) -> dict[str, Any]:
    package_dir = find_package(case)
    if not package_dir:
        return {**case, "status": "missing_package", "package_path": None}
    manifest = read_json(package_dir / "manifest.json", {})
    quality = read_json(package_dir / "qa" / "quality_report.json", {})
    metrics_payload = read_json(package_dir / "metrics" / "normalized_metrics.json", {})
    metrics = _metric_entries(metrics_payload)
    metric_names = {item.get("canonical_name") for item in metrics if isinstance(item, dict)}
    source_map = read_json(package_dir / "qa" / "source_map.json", {})
    source_entries = _source_entries(source_map)
    evidence_count = len(source_entries)
    expected = set(case.get("expected_metrics") or [])
    missing_metrics = sorted(expected - metric_names)
    missing_evidence = bool(case.get("expected_evidence")) and evidence_count == 0
    gate_failures = _quality_gate_failures(case, manifest, quality, metrics, metric_names, source_map, package_dir)
    status = "pass" if not missing_metrics and not missing_evidence and not gate_failures else "fail"
    return {
        **case,
        "status": status,
        "package_path": str(package_dir),
        "quality_status": quality.get("overall_status") or manifest.get("quality_status"),
        "document_format": manifest.get("document_format") or case.get("document_format"),
        "counts": {
            "metrics": len(metrics or []),
            "evidence": evidence_count,
            "tables": quality.get("table_count"),
            "raw_facts": quality.get("raw_fact_count"),
        },
        "missing_metrics": missing_metrics,
        "missing_evidence": missing_evidence,
        "gate_failures": gate_failures,
    }


def _quality_gate_failures(
    case: dict[str, Any],
    manifest: dict[str, Any],
    quality: dict[str, Any],
    metrics: list[Any],
    metric_names: set[Any],
    source_map: dict[str, Any],
    package_dir: Path,
) -> list[str]:
    market = str(case.get("market") or manifest.get("market") or "").upper()
    if market != "EU":
        return []
    document_format = str(manifest.get("document_format") or case.get("document_format") or "").lower()
    if document_format in {"esef_zip", "ixbrl_xhtml", "xhtml", "xml"}:
        return _eu_esef_gate_failures(case, manifest, quality, metrics, metric_names, source_map, package_dir)
    return _eu_pdf_gate_failures(case, manifest, quality, metrics, metric_names)


def _eu_pdf_gate_failures(
    case: dict[str, Any],
    manifest: dict[str, Any],
    quality: dict[str, Any],
    metrics: list[Any],
    metric_names: set[Any],
) -> list[str]:
    failures: list[str] = []
    if (quality.get("overall_status") or manifest.get("quality_status")) == "fail":
        failures.append("quality_status_fail")
    if _number(quality.get("table_count")) < 5:
        failures.append("table_count_lt_5")
    if len(metrics or []) < 10:
        failures.append("normalized_metric_count_lt_10")
    if _number(quality.get("evidence_coverage_ratio")) < 0.95:
        failures.append("evidence_coverage_lt_0_95")
    failures.extend(_missing_eu_core_metric_failures(case, metric_names))
    return failures


def _eu_esef_gate_failures(
    case: dict[str, Any],
    manifest: dict[str, Any],
    quality: dict[str, Any],
    metrics: list[Any],
    metric_names: set[Any],
    source_map: dict[str, Any],
    package_dir: Path,
) -> list[str]:
    failures = _eu_pdf_gate_failures(case, manifest, quality, metrics, metric_names)
    facts = _records(read_json(package_dir / "xbrl" / "facts_raw.json", {}), "facts")
    contexts = _records(read_json(package_dir / "xbrl" / "contexts.json", {}), "contexts")
    units = _records(read_json(package_dir / "xbrl" / "units.json", {}), "units")
    if not facts:
        failures.append("xbrl_facts_empty")
    if not contexts:
        failures.append("xbrl_contexts_empty")
    if not units:
        failures.append("xbrl_units_empty")
    entries = source_map.get("entries") if isinstance(source_map, dict) else []
    xbrl_entries = [entry for entry in entries or [] if isinstance(entry, dict) and str(entry.get("source_type") or "").startswith(("xbrl", "ixbrl"))]
    if facts and len(xbrl_entries) / max(1, len(metrics or [])) < 0.95:
        failures.append("xbrl_evidence_coverage_lt_0_95")
    if _has_high_confidence_extension_metric(metrics, facts, quality):
        failures.append("extension_high_confidence_without_warning")
    return sorted(set(failures))


def _missing_eu_core_metric_failures(case: dict[str, Any], metric_names: set[Any]) -> list[str]:
    industry = str(case.get("industry_profile") or "").lower()
    groups = {
        "revenue": {"revenue", "operating_revenue", "total_revenue", "sales"},
        "net_profit": {"net_profit", "profit_for_period", "net_income"},
        "total_assets": {"total_assets", "assets"},
        "total_liabilities": {"total_liabilities", "liabilities"},
        "total_equity": {"total_equity", "equity"},
        "operating_cash_flow": {"operating_cash_flow", "operating_cash_flow_net", "cash_flow_from_operating_activities"},
    }
    failures = []
    names = {str(name) for name in metric_names}
    for key, aliases in groups.items():
        if key == "operating_cash_flow" and industry in {"bank", "insurance"}:
            continue
        if not names.intersection(aliases):
            failures.append(f"missing_core_{key}")
    return failures


def _records(payload: Any, key: str) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            return [item for item in value.values() if isinstance(item, dict)]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _source_entries(source_map: Any) -> list[dict[str, Any]]:
    if not isinstance(source_map, dict):
        return []
    for key in ("entries", "evidence"):
        value = source_map.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _has_high_confidence_extension_metric(metrics: list[Any], facts: list[dict[str, Any]], quality: dict[str, Any]) -> bool:
    warnings = " ".join(str(item).lower() for item in (quality.get("rule_warnings") or []) + (quality.get("parser_warnings") or []))
    if "extension" in warnings:
        return False
    extension_ids = {str(fact.get("fact_id") or fact.get("raw_fact_id")) for fact in facts if fact.get("is_extension")}
    for metric in metrics or []:
        if not isinstance(metric, dict):
            continue
        raw_fact_id = str(metric.get("raw_fact_id") or metric.get("fact_id") or "")
        if raw_fact_id in extension_ids and _number(metric.get("confidence")) >= 0.9:
            return True
    return False


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# Market Ingestion Evaluation",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Cases: `{report['summary']['cases']}`",
        f"- Passed: `{report['summary']['pass']}`",
        f"- Failed: `{report['summary']['fail']}`",
        f"- Missing packages: `{report['summary']['missing_package']}`",
        "",
        "| Market | Country | Ticker | Year | Format | Status | Quality | Metrics | Evidence | Missing | Gates |",
        "| --- | --- | --- | ---: | --- | --- | --- | ---: | ---: | --- | --- |",
    ]
    for item in report["items"]:
        counts = item.get("counts") or {}
        lines.append(
            f"| {item.get('market')} | {item.get('country') or ''} | {item.get('ticker')} | {item.get('fiscal_year')} | "
            f"{item.get('document_format') or ''} | {item.get('status')} | "
            f"{item.get('quality_status') or ''} | {counts.get('metrics', '')} | {counts.get('evidence', '')} | "
            f"{', '.join(item.get('missing_metrics') or [])} | {', '.join(item.get('gate_failures') or [])} |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate market evidence package coverage against static cases.")
    parser.add_argument("--output", type=Path, default=REPO_ROOT / "eval_datasets" / "market_ingestion_cases" / "market_ingestion_eval_report.json")
    parser.add_argument("--markdown", type=Path, default=REPO_ROOT / "eval_datasets" / "market_ingestion_cases" / "market_ingestion_eval_report.md")
    args = parser.parse_args()
    items = [evaluate_case(case) for case in load_cases()]
    summary = {"cases": len(items), "pass": 0, "fail": 0, "missing_package": 0, "by_market": {}}
    for item in items:
        status = item["status"]
        summary[status] = summary.get(status, 0) + 1
        market = item.get("market")
        bucket = summary["by_market"].setdefault(market, {"cases": 0, "pass": 0, "fail": 0, "missing_package": 0})
        bucket["cases"] += 1
        bucket[status] = bucket.get(status, 0) + 1
    report = {"schema_version": "market_ingestion_eval_v1", "generated_at": now_iso(), "summary": summary, "items": items}
    write_json(args.output if args.output.is_absolute() else REPO_ROOT / args.output, report)
    md_path = args.markdown if args.markdown.is_absolute() else REPO_ROOT / args.markdown
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(markdown_report(report), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
