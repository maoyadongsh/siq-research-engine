#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACTS_SRC = REPO_ROOT / "packages" / "market-contracts" / "src"
if CONTRACTS_SRC.is_dir() and str(CONTRACTS_SRC) not in sys.path:
    sys.path.insert(0, str(CONTRACTS_SRC))

from siq_market_contracts import build_quality_gates as _contract_quality_gates
from siq_market_contracts import is_resolvable_evidence_source


CASE_ROOT = REPO_ROOT / "datasets" / "market_ingestion"
LEGACY_CASE_ROOT = REPO_ROOT / "eval_datasets" / "market_ingestion_cases"
DEFAULT_OUTPUT = REPO_ROOT / "artifacts" / "eval-runs" / "market_ingestion" / "market_ingestion_eval_report.json"
DEFAULT_MARKDOWN = REPO_ROOT / "artifacts" / "eval-runs" / "market_ingestion" / "market_ingestion_eval_report.md"
WIKI_ROOTS = {
    "US": REPO_ROOT / "data" / "wiki" / "us",
    "HK": REPO_ROOT / "data" / "wiki" / "hk",
    "JP": REPO_ROOT / "data" / "wiki" / "jp",
    "KR": REPO_ROOT / "data" / "wiki" / "kr",
    "EU": REPO_ROOT / "data" / "wiki" / "eu",
}
DEFAULT_QUALITY_THRESHOLDS = {
    "CN": {
        "evidence_coverage_ratio": 0.8,
        "evidence_resolvability_ratio": 1.0,
        "statement_coverage": 1.0,
        "bridge_check_pass_rate": 0.95,
    },
    "HK": {
        "evidence_coverage_ratio": 0.8,
        "evidence_resolvability_ratio": 1.0,
        "statement_coverage": 1.0,
        "bridge_check_pass_rate": 0.95,
    },
    "US": {
        "evidence_coverage_ratio": 0.95,
        "evidence_resolvability_ratio": 1.0,
        "statement_coverage": 1.0,
        "bridge_check_pass_rate": 0.95,
    },
    "EU": {
        "evidence_coverage_ratio": 0.8,
        "evidence_resolvability_ratio": 1.0,
        "statement_coverage": 1.0,
        "bridge_check_pass_rate": 0.95,
    },
    "JP": {
        "evidence_coverage_ratio": 0.8,
        "evidence_resolvability_ratio": 1.0,
        "statement_coverage": 1.0,
        "bridge_check_pass_rate": 0.95,
    },
    "KR": {
        "evidence_coverage_ratio": 0.8,
        "evidence_resolvability_ratio": 1.0,
        "statement_coverage": 1.0,
        "bridge_check_pass_rate": 0.95,
    },
}
OFFICIAL_SOURCE_DOMAINS = {
    "HK": ("hkexnews.hk", "hkex.com.hk"),
    "US": ("sec.gov",),
    "JP": ("edinet-fsa.go.jp", "disclosure2.edinet-fsa.go.jp", "fsa.go.jp"),
    "KR": ("dart.fss.or.kr", "opendart.fss.or.kr"),
    "EU": ("esma.europa.eu", "filing.xbrl.org", "xbrl.org", "six-group.com", "six-exchange-regulation.com"),
}
REVIEW_GATE_FAILURES = {
    "official_source_unverified",
}
CURRENCY_ALIASES = {
    "RMB": "CNY",
    "CNH": "CNY",
    "US$": "USD",
    "HK$": "HKD",
    "EURO": "EUR",
    "YEN": "JPY",
    "WON": "KRW",
}


def read_json(path: Path, default: Any = None) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else ([] if default is None else default)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _case_files(case_root: Path) -> list[Path]:
    if not case_root.exists():
        return []
    return sorted(case_root.glob("*_cases.json"))


def load_cases(case_root: Path = CASE_ROOT, *, legacy_case_root: Path | None = LEGACY_CASE_ROOT) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    seen: set[tuple[Any, Any, Any, Any]] = set()
    case_files = _case_files(case_root)
    if not case_files and legacy_case_root is not None and legacy_case_root != case_root:
        case_files = _case_files(legacy_case_root)
    for path in case_files:
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
        return bool(
            candidates.intersection(
                {
                    "annual",
                    "annualreport",
                    "annualsecuritiesreport",
                    "businessreport",
                    "integratedreport",
                    "esef",
                    "euannualreport",
                    "euesefannualreport",
                    "jpannualsecuritiesreport",
                    "krbusinessreport",
                    "10k",
                    "20f",
                }
            )
        )
    if expected == "quarterly":
        return bool(candidates.intersection({"quarterly", "quarterlyreport", "10q"}))
    return expected in candidates


def _normalize_code(value: Any) -> str:
    text = str(value or "").strip().upper()
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits.lstrip("0") or digits or text


def _normalize_report_type(value: Any) -> str:
    text = str(value or "").strip().lower()
    aliases = {
        "年报": "annual",
        "年度报告": "annual",
        "annual report": "annualreport",
        "annual_report": "annualreport",
        "annual securities report": "annualsecuritiesreport",
        "annual_securities_report": "annualsecuritiesreport",
        "business report": "businessreport",
        "business_report": "businessreport",
        "quarterly report": "quarterlyreport",
        "quarterly_report": "quarterlyreport",
    }
    if text in aliases:
        return aliases[text]
    return "".join(ch for ch in text if ch.isalnum())


def _metric_entries(payload: Any) -> list[Any]:
    if not isinstance(payload, dict):
        return []
    metrics = payload.get("metrics")
    return metrics if isinstance(metrics, list) else []


def _financial_data_sources(financial_data: Any) -> list[dict[str, Any]]:
    if not isinstance(financial_data, dict):
        return []
    sources: list[dict[str, Any]] = []
    for statement in financial_data.get("statements") or []:
        if not isinstance(statement, dict):
            continue
        for item in statement.get("items") or []:
            if not isinstance(item, dict):
                continue
            item_sources = item.get("sources")
            if isinstance(item_sources, dict):
                sources.extend(source for source in item_sources.values() if isinstance(source, dict))
    for bucket in ("key_metrics", "operating_metrics"):
        for item in financial_data.get(bucket) or []:
            if not isinstance(item, dict):
                continue
            item_sources = item.get("sources")
            if isinstance(item_sources, dict):
                sources.extend(source for source in item_sources.values() if isinstance(source, dict))
    return sources


def _package_quality_gates(package_dir: Path) -> dict[str, Any]:
    try:
        gates = _contract_quality_gates(package_dir)
    except Exception as exc:
        return {
            "schema_version": "siq_quality_gates_v1",
            "overall_status": "fail",
            "artifact_hash_status": "unknown",
            "resolvable_evidence_count": 0,
            "unresolvable_evidence_count": 0,
            "evidence_resolvability_ratio": None,
            "gate_error": str(exc),
        }
    return gates if isinstance(gates, dict) else {}


def evaluate_case(case: dict[str, Any]) -> dict[str, Any]:
    package_dir = find_package(case)
    expected_gate_status = _normalize_gate_status(case.get("expected_gate_status"))
    if not package_dir:
        return {
            **case,
            "status": "missing_package",
            "eval_gate_status": "block",
            "expected_gate_status": expected_gate_status,
            "gate_status_matches_expected": None,
            "package_path": None,
        }
    manifest = read_json(package_dir / "manifest.json", {})
    quality = read_json(package_dir / "qa" / "quality_report.json", {})
    metrics_payload = read_json(package_dir / "metrics" / "normalized_metrics.json", {})
    metrics = _metric_entries(metrics_payload)
    metric_names = {item.get("canonical_name") for item in metrics if isinstance(item, dict)}
    financial_data = read_json(package_dir / "metrics" / "financial_data.json", {})
    source_map = read_json(package_dir / "qa" / "source_map.json", {})
    source_entries = _source_entries(source_map)
    quality_gates = _package_quality_gates(package_dir)
    resolvable_evidence_count = _number(quality_gates.get("resolvable_evidence_count"))
    if not source_entries and resolvable_evidence_count == 0:
        resolvable_evidence_count = sum(
            1
            for item in _financial_data_sources(financial_data)
            if is_resolvable_evidence_source(item, manifest=manifest, package_dir=package_dir)
        )
    evidence_count = int(resolvable_evidence_count)
    bridge_checks = _read_first_json(
        package_dir,
        [
            Path("qa/financial_checks.json"),
            Path("checks/financial_checks.json"),
            Path("metrics/financial_checks.json"),
            Path("financial_checks.json"),
        ],
    )
    gate_evidence_coverage = _number_or_none(quality_gates.get("evidence_coverage_ratio"))
    evidence_coverage_ratio = (
        gate_evidence_coverage
        if gate_evidence_coverage is not None
        else _evidence_coverage_ratio(quality, evidence_count, case)
    )
    statement_coverage = _statement_coverage(quality, case)
    bridge_check_pass_rate = _bridge_check_pass_rate(bridge_checks)
    bridge_check_status = _bridge_check_status(bridge_checks)
    expected = set(case.get("expected_metrics") or [])
    missing_metrics = sorted(expected - metric_names)
    missing_evidence = bool(case.get("expected_evidence")) and evidence_count == 0
    gate_failures = _quality_gate_failures(
        case,
        manifest,
        quality,
        financial_data,
        metrics,
        metric_names,
        source_map,
        package_dir,
        quality_gates=quality_gates,
        evidence_coverage_ratio=evidence_coverage_ratio,
        statement_coverage=statement_coverage,
        bridge_check_pass_rate=bridge_check_pass_rate,
        bridge_check_status=bridge_check_status,
    )
    eval_gate_status = _eval_gate_status(
        quality_gates,
        gate_failures=gate_failures,
        missing_metrics=missing_metrics,
        missing_evidence=missing_evidence,
    )
    gate_status_matches_expected = None
    if expected_gate_status:
        gate_status_matches_expected = eval_gate_status == expected_gate_status
        if not gate_status_matches_expected:
            gate_failures = sorted(
                {
                    *gate_failures,
                    f"expected_gate_status_{expected_gate_status}_got_{eval_gate_status}",
                }
            )
    status = "pass" if not missing_metrics and not missing_evidence and not gate_failures and eval_gate_status == "pass" else "fail"
    return {
        **case,
        "status": status,
        "eval_gate_status": eval_gate_status,
        "expected_gate_status": expected_gate_status,
        "gate_status_matches_expected": gate_status_matches_expected,
        "package_path": str(package_dir),
        "quality_status": quality.get("overall_status") or manifest.get("quality_status"),
        "document_format": manifest.get("document_format") or case.get("document_format"),
        "counts": {
            "metrics": len(metrics or []),
            "evidence": len(source_entries),
            "resolvable_evidence": evidence_count,
            "unresolvable_evidence": int(_number(quality_gates.get("unresolvable_evidence_count"))),
            "tables": quality.get("table_count"),
            "raw_facts": quality.get("raw_fact_count"),
        },
        "evidence_coverage_ratio": evidence_coverage_ratio,
        "evidence_resolvability_ratio": quality_gates.get("evidence_resolvability_ratio"),
        "statement_coverage": statement_coverage,
        "bridge_check_pass_rate": bridge_check_pass_rate,
        "bridge_check_status": bridge_check_status,
        "artifact_hash_status": quality_gates.get("artifact_hash_status"),
        "quality_gates": quality_gates,
        "missing_metrics": missing_metrics,
        "missing_evidence": missing_evidence,
        "gate_failures": gate_failures,
    }


def _read_first_json(package_dir: Path, relative_paths: list[Path]) -> Any:
    for relative_path in relative_paths:
        path = package_dir / relative_path
        if path.exists():
            return read_json(path, {})
    return {}


def _evidence_coverage_ratio(quality: dict[str, Any], evidence_count: int, case: dict[str, Any]) -> float | None:
    for key in ("evidence_coverage_ratio", "evidence_coverage", "coverage_ratio"):
        value = _number_or_none(quality.get(key))
        if value is not None:
            return value / 100 if 1 < value <= 100 else value
    if case.get("expected_evidence"):
        return 1.0 if evidence_count > 0 else 0.0
    return None


def _statement_coverage(quality: dict[str, Any], case: dict[str, Any]) -> float | None:
    status = quality.get("required_statement_status")
    if isinstance(status, dict) and status:
        present = sum(1 for value in status.values() if _is_present_status(value))
        return present / len(status)
    expected = [str(item) for item in case.get("expected_statements") or []]
    missing = quality.get("missing_required_statements")
    if expected and isinstance(missing, list):
        missing_set = {str(item) for item in missing}
        return (len(expected) - len(missing_set.intersection(expected))) / len(expected)
    return None


def _bridge_check_pass_rate(payload: Any) -> float | None:
    if not isinstance(payload, dict) or not payload:
        return None
    summary = payload.get("summary")
    if isinstance(summary, dict):
        passed = _number_or_none(summary.get("pass")) or 0.0
        failed = (_number_or_none(summary.get("fail")) or 0.0) + (_number_or_none(summary.get("error")) or 0.0)
        total = passed + failed
        return passed / total if total else None
    checks = payload.get("checks")
    if isinstance(checks, list) and checks:
        statuses = [str(item.get("status") or "").lower() for item in checks if isinstance(item, dict)]
        hard_statuses = [status for status in statuses if status in {"pass", "fail", "failed", "error", "critical"}]
        if hard_statuses:
            return hard_statuses.count("pass") / len(hard_statuses)
    overall = str(payload.get("overall_status") or payload.get("status") or "").lower()
    if overall == "pass":
        return 1.0
    if overall in {"warning", "fail", "error"}:
        return 0.0
    return None


def _bridge_check_status(payload: Any) -> str | None:
    if not isinstance(payload, dict) or not payload:
        return None
    overall = str(payload.get("overall_status") or payload.get("status") or "").strip().lower()
    if overall:
        return overall
    checks = payload.get("checks")
    if isinstance(checks, list) and checks:
        statuses = [str(item.get("status") or "").lower() for item in checks if isinstance(item, dict)]
        if any(status in {"fail", "error"} for status in statuses):
            return "fail"
        if any(status in {"warning", "warn"} for status in statuses):
            return "warning"
        if statuses and all(status == "pass" for status in statuses):
            return "pass"
    return None


def _is_present_status(value: Any) -> bool:
    return str(value).strip().lower() in {"present", "pass", "ok", "ready", "available", "true"}


def _normalize_gate_status(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if text in {"allow", "allowed", "ok", "pass", "passed", "ready", "success"}:
        return "pass"
    if text in {"warn", "warning", "needs_review", "review"}:
        return "review"
    if text in {"block", "blocked", "fail", "failed", "error", "critical", "missing_package"}:
        return "block"
    return None


def _eval_gate_status(
    quality_gates: dict[str, Any],
    *,
    gate_failures: list[str],
    missing_metrics: list[str],
    missing_evidence: bool,
) -> str:
    decision = _normalize_gate_status(
        quality_gates.get("canonical_decision")
        or quality_gates.get("decision")
        or ((quality_gates.get("decisions_by_target") or {}).get("canonical") or {}).get("decision")
    )
    if decision == "block" or _has_blocking_eval_failure(gate_failures, missing_metrics, missing_evidence):
        return "block"
    if decision == "review" or _has_review_eval_failure(gate_failures):
        return "review"
    return "pass"


def _has_blocking_eval_failure(gate_failures: list[str], missing_metrics: list[str], missing_evidence: bool) -> bool:
    if missing_metrics or missing_evidence:
        return True
    return any(failure not in REVIEW_GATE_FAILURES for failure in gate_failures)


def _has_review_eval_failure(gate_failures: list[str]) -> bool:
    return any(failure in REVIEW_GATE_FAILURES for failure in gate_failures)


def _quality_gate_failures(
    case: dict[str, Any],
    manifest: dict[str, Any],
    quality: dict[str, Any],
    financial_data: dict[str, Any],
    metrics: list[Any],
    metric_names: set[Any],
    source_map: dict[str, Any],
    package_dir: Path,
    *,
    quality_gates: dict[str, Any],
    evidence_coverage_ratio: float | None,
    statement_coverage: float | None,
    bridge_check_pass_rate: float | None,
    bridge_check_status: str | None,
) -> list[str]:
    market = str(case.get("market") or manifest.get("market") or "").upper()
    failures: list[str] = []
    if not isinstance(manifest, dict) or not manifest:
        failures.append("manifest_missing")
    elif manifest.get("schema_version") != "market_evidence_package_v1":
        failures.append("manifest_schema_invalid")

    quality_status = str(quality.get("overall_status") or manifest.get("quality_status") or "").lower()
    if quality_status in {"fail", "failed", "error", "critical"}:
        failures.append("quality_status_fail")
    if quality_gates.get("overall_status") == "fail":
        failures.append("quality_gate_fail")

    artifact_hash_status = str(quality_gates.get("artifact_hash_status") or "").lower()
    if artifact_hash_status and artifact_hash_status != "ok":
        failures.append(f"artifact_hash_{artifact_hash_status}")

    thresholds = _case_quality_thresholds(case, market)
    failures.extend(_threshold_failures(thresholds, "evidence_coverage_ratio", evidence_coverage_ratio))
    failures.extend(_threshold_failures(thresholds, "statement_coverage", statement_coverage))
    failures.extend(_threshold_failures(thresholds, "bridge_check_pass_rate", bridge_check_pass_rate))
    failures.extend(
        _threshold_failures(
            thresholds,
            "evidence_resolvability_ratio",
            _number_or_none(quality_gates.get("evidence_resolvability_ratio")),
        )
    )
    if _number(quality_gates.get("unresolvable_evidence_count")) > 0:
        failures.append("unresolvable_evidence_present")
    if bridge_check_status in {"fail", "failed", "error", "critical"}:
        failures.append("bridge_check_status_fail")
    failures.extend(_metadata_gate_failures(case, manifest, financial_data, metrics))

    if market != "EU":
        return sorted(set(failures))
    document_format = str(manifest.get("document_format") or case.get("document_format") or "").lower()
    if document_format in {"esef_zip", "ixbrl_xhtml", "xhtml", "xml"}:
        failures.extend(_eu_esef_gate_failures(case, manifest, quality, metrics, metric_names, source_map, package_dir))
    else:
        failures.extend(_eu_pdf_gate_failures(case, manifest, quality, metrics, metric_names))
    return sorted(set(failures))


def _case_quality_thresholds(case: dict[str, Any], market: str) -> dict[str, float]:
    thresholds = dict(DEFAULT_QUALITY_THRESHOLDS.get(market, {}))
    raw = case.get("quality_thresholds")
    if isinstance(raw, dict):
        for key, value in raw.items():
            number = _number_or_none(value)
            if number is not None:
                thresholds[str(key)] = number
    return thresholds


def _threshold_failures(thresholds: dict[str, float], key: str, value: float | None) -> list[str]:
    threshold = thresholds.get(key)
    if threshold is None:
        return []
    if value is None:
        return [f"{key}_missing"]
    if value < threshold:
        suffix = str(threshold).replace(".", "_")
        return [f"{key}_lt_{suffix}"]
    return []


def _metadata_gate_failures(
    case: dict[str, Any],
    manifest: dict[str, Any],
    financial_data: dict[str, Any],
    metrics: list[Any],
) -> list[str]:
    failures: list[str] = []
    failures.extend(_currency_gate_failures(case, manifest, financial_data, metrics))
    failures.extend(_period_gate_failures(case, manifest, financial_data, metrics))
    failures.extend(_source_gate_failures(case, manifest))
    return failures


def _currency_gate_failures(
    case: dict[str, Any],
    manifest: dict[str, Any],
    financial_data: dict[str, Any],
    metrics: list[Any],
) -> list[str]:
    expected = _normalize_currency(
        case.get("expected_currency")
        or case.get("reporting_currency")
        or case.get("currency")
    )
    if not expected:
        return []
    actual = _currency_candidates(manifest, financial_data, metrics)
    if actual and expected not in actual:
        return [f"currency_mismatch_expected_{expected.lower()}"]
    return []


def _currency_candidates(manifest: dict[str, Any], financial_data: dict[str, Any], metrics: list[Any]) -> set[str]:
    candidates: set[str] = set()
    for payload in (manifest, financial_data):
        if not isinstance(payload, dict):
            continue
        for key in ("expected_currency", "reporting_currency", "presentation_currency", "currency", "unit_currency"):
            currency = _normalize_currency(payload.get(key))
            if currency:
                candidates.add(currency)
    for metric in metrics or []:
        if not isinstance(metric, dict):
            continue
        for key in ("reporting_currency", "currency", "unit_currency", "unit", "unit_id"):
            currency = _normalize_currency(metric.get(key))
            if currency:
                candidates.add(currency)
    return candidates


def _normalize_currency(value: Any) -> str:
    text = str(value or "").strip().upper()
    if not text:
        return ""
    if text in CURRENCY_ALIASES:
        return CURRENCY_ALIASES[text]
    compact = "".join(ch for ch in text if ch.isalnum() or ch == "$")
    if compact in CURRENCY_ALIASES:
        return CURRENCY_ALIASES[compact]
    for code in ("USD", "HKD", "EUR", "JPY", "KRW", "CNY", "CHF", "GBP"):
        if code in compact:
            return code
    if "RMB" in compact:
        return "CNY"
    return compact if len(compact) == 3 and compact.isalpha() else ""


def _period_gate_failures(
    case: dict[str, Any],
    manifest: dict[str, Any],
    financial_data: dict[str, Any],
    metrics: list[Any],
) -> list[str]:
    expected = _normalize_period_end(case.get("expected_period_end") or case.get("period_end"))
    if not expected:
        return []
    actual = _period_candidates(manifest, financial_data, metrics)
    if actual and expected not in actual:
        return [f"period_end_mismatch_expected_{expected}"]
    return []


def _period_candidates(manifest: dict[str, Any], financial_data: dict[str, Any], metrics: list[Any]) -> set[str]:
    candidates: set[str] = set()
    for payload in (manifest, financial_data):
        if not isinstance(payload, dict):
            continue
        for key in ("period_end", "expected_period_end", "fiscal_period_end", "report_period_end", "end_date"):
            period_end = _normalize_period_end(payload.get(key))
            if period_end:
                candidates.add(period_end)
    for metric in metrics or []:
        if not isinstance(metric, dict):
            continue
        for key in ("period_end", "fiscal_period_end", "report_period_end", "end_date", "instant"):
            period_end = _normalize_period_end(metric.get(key))
            if period_end:
                candidates.add(period_end)
    return candidates


def _normalize_period_end(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    return text


def _source_gate_failures(case: dict[str, Any], manifest: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    if _source_marked_unverified(case, manifest) and _expects_official_source(case, manifest):
        failures.append("official_source_unverified")
    if _official_source_url_untrusted(case, manifest):
        failures.append("official_source_url_untrusted")
    return failures


def _source_marked_unverified(case: dict[str, Any], manifest: dict[str, Any]) -> bool:
    for value in (
        manifest.get("source_verification_status"),
        manifest.get("official_source_verified"),
        case.get("source_verification_status"),
        case.get("official_source_verified"),
    ):
        if isinstance(value, bool):
            return not value
        text = str(value or "").strip().lower()
        if text in {"unverified", "not_verified", "failed", "unknown", "false", "no"}:
            return True
        if text in {"verified", "pass", "true", "yes"}:
            return False
    return False


def _expects_official_source(case: dict[str, Any], manifest: dict[str, Any]) -> bool:
    explicit = case.get("expected_official_source")
    if isinstance(explicit, bool):
        return explicit
    source_tier = str(manifest.get("source_tier") or case.get("source_tier") or "").lower()
    if source_tier in {"official", "regulator", "exchange", "issuer"} or source_tier.startswith("official_"):
        return True
    source_id = str(manifest.get("source_id") or case.get("source_id") or "").lower()
    return source_id in {"hkex", "sec", "edgar", "edinet", "dart", "esef", "six_direct", "eu_direct"}


def _official_source_url_untrusted(case: dict[str, Any], manifest: dict[str, Any]) -> bool:
    if not _expects_official_source(case, manifest):
        return False
    source_url = str(manifest.get("source_url") or manifest.get("filing_url") or case.get("source_url") or "").strip()
    if not source_url:
        return False
    market = str(case.get("market") or manifest.get("market") or "").upper()
    allowed_domains = _allowed_official_source_domains(case, manifest, market)
    if not allowed_domains:
        return False
    hostname = urlparse(source_url).hostname or ""
    hostname = hostname.lower().removeprefix("www.")
    return not any(hostname == domain or hostname.endswith(f".{domain}") for domain in allowed_domains)


def _allowed_official_source_domains(case: dict[str, Any], manifest: dict[str, Any], market: str) -> tuple[str, ...]:
    explicit = case.get("official_source_domains") or manifest.get("official_source_domains")
    if isinstance(explicit, list):
        domains = tuple(str(domain).lower().removeprefix("www.") for domain in explicit if str(domain or "").strip())
        if domains:
            return domains
    source_id = str(manifest.get("source_id") or case.get("source_id") or "").lower()
    known_source_ids = {
        "HK": {"hkex"},
        "US": {"sec", "edgar"},
        "JP": {"edinet"},
        "KR": {"dart"},
        "EU": {"esef", "six_direct"},
    }
    if source_id not in known_source_ids.get(market, set()):
        return ()
    return OFFICIAL_SOURCE_DOMAINS.get(market, ())


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


def _number_or_none(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def summarize_items(items: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "cases": len(items),
        "pass": 0,
        "fail": 0,
        "missing_package": 0,
        "eval_gate_status": {"pass": 0, "review": 0, "block": 0},
        "by_market": {},
    }
    for item in items:
        status = item["status"]
        summary[status] = summary.get(status, 0) + 1
        gate_status = _item_gate_status(item)
        summary["eval_gate_status"][gate_status] = summary["eval_gate_status"].get(gate_status, 0) + 1
        market = item.get("market")
        bucket = summary["by_market"].setdefault(
            market,
            {
                "cases": 0,
                "pass": 0,
                "fail": 0,
                "missing_package": 0,
                "eval_gate_status": {"pass": 0, "review": 0, "block": 0},
            },
        )
        bucket["cases"] += 1
        bucket[status] = bucket.get(status, 0) + 1
        bucket["eval_gate_status"][gate_status] = bucket["eval_gate_status"].get(gate_status, 0) + 1
    summary["quality_metrics"] = _quality_metrics(items)
    return summary


def _item_gate_status(item: dict[str, Any]) -> str:
    gate_status = _normalize_gate_status(item.get("eval_gate_status"))
    if gate_status:
        return gate_status
    status = str(item.get("status") or "").lower()
    if status == "pass":
        return "pass"
    if status in {"fail", "missing_package"}:
        return "block"
    return "review"


def _quality_metrics(items: list[dict[str, Any]]) -> dict[str, float | None]:
    return {
        "official_source_hit_rate": _rate(items, _is_official_source),
        "parser_success_rate": _rate(items, lambda item: item.get("status") != "missing_package"),
        "evidence_coverage_ratio": _mean(_metric_values(items, "evidence_coverage_ratio")),
        "statement_coverage": _mean(_metric_values(items, "statement_coverage")),
        "bridge_check_pass_rate": _mean(_metric_values(items, "bridge_check_pass_rate")),
        "answer_citation_rate": _answer_eval_rate(items, "has_valid_citation"),
        "numeric_accuracy": _answer_eval_rate(items, "numeric_correct"),
        "hallucination_block_rate": _answer_eval_rate(items, "hallucination_blocked"),
    }


def _rate(items: list[dict[str, Any]], predicate: Any) -> float | None:
    if not items:
        return None
    return sum(1 for item in items if predicate(item)) / len(items)


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _metric_values(items: list[dict[str, Any]], key: str) -> list[float]:
    values = []
    for item in items:
        value = _number_or_none(item.get(key))
        if value is not None:
            values.append(value)
    return values


def _is_official_source(item: dict[str, Any]) -> bool:
    source_tier = str(item.get("source_tier") or "").lower()
    if source_tier in {"official", "regulator", "exchange", "issuer"} or source_tier.startswith("official_"):
        return True
    source_text = " ".join(
        str(item.get(key) or "").lower()
        for key in ("source_id", "source_pdf", "source_file", "pdf_path", "metadata_json")
    )
    official_markers = ("hkex", "sec", "edgar", "edinet", "dart", "esef", "six_direct", "eu_direct")
    return any(marker in source_text for marker in official_markers)


def _answer_eval_rate(items: list[dict[str, Any]], key: str) -> float | None:
    values = []
    for item in items:
        for entry in _answer_evaluations(item):
            if key in entry:
                values.append(bool(entry.get(key)))
    return sum(1 for value in values if value) / len(values) if values else None


def _answer_evaluations(item: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("answer_evals", "answer_evaluations", "qa_evaluations"):
        payload = item.get(key)
        if isinstance(payload, list):
            return [entry for entry in payload if isinstance(entry, dict)]
    return []


def _format_metric(value: Any) -> str:
    number = _number_or_none(value)
    return "-" if number is None else f"{number:.2%}"


def markdown_report(report: dict[str, Any]) -> str:
    gate_status = report["summary"].get("eval_gate_status") or {}
    lines = [
        "# Market Ingestion Evaluation",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Cases: `{report['summary']['cases']}`",
        f"- Passed: `{report['summary']['pass']}`",
        f"- Failed: `{report['summary']['fail']}`",
        f"- Missing packages: `{report['summary']['missing_package']}`",
        f"- Gate pass/review/block: `{gate_status.get('pass', 0)}` / `{gate_status.get('review', 0)}` / `{gate_status.get('block', 0)}`",
        "",
        "## Quality Metrics",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
    ]
    metrics = report["summary"].get("quality_metrics") or {}
    for key in (
        "official_source_hit_rate",
        "parser_success_rate",
        "evidence_coverage_ratio",
        "statement_coverage",
        "bridge_check_pass_rate",
        "answer_citation_rate",
        "numeric_accuracy",
        "hallucination_block_rate",
    ):
        lines.append(f"| {key} | {_format_metric(metrics.get(key))} |")
    lines.extend(
        [
            "",
            "| Market | Country | Ticker | Year | Format | Status | Gate | Quality | Metrics | Evidence | Missing | Gates |",
            "| --- | --- | --- | ---: | --- | --- | --- | --- | ---: | ---: | --- | --- |",
        ]
    )
    for item in report["items"]:
        counts = item.get("counts") or {}
        lines.append(
            f"| {item.get('market')} | {item.get('country') or ''} | {item.get('ticker')} | {item.get('fiscal_year')} | "
            f"{item.get('document_format') or ''} | {item.get('status')} | "
            f"{item.get('eval_gate_status') or ''} | "
            f"{item.get('quality_status') or ''} | {counts.get('metrics', '')} | {counts.get('evidence', '')} | "
            f"{', '.join(item.get('missing_metrics') or [])} | {', '.join(item.get('gate_failures') or [])} |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate market evidence package coverage against static cases.")
    parser.add_argument("--case-root", type=Path, default=CASE_ROOT)
    parser.add_argument("--legacy-case-root", type=Path, default=LEGACY_CASE_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN)
    args = parser.parse_args()
    case_root = args.case_root if args.case_root.is_absolute() else REPO_ROOT / args.case_root
    legacy_case_root = args.legacy_case_root if args.legacy_case_root.is_absolute() else REPO_ROOT / args.legacy_case_root
    items = [evaluate_case(case) for case in load_cases(case_root, legacy_case_root=legacy_case_root)]
    summary = summarize_items(items)
    report = {"schema_version": "market_ingestion_eval_v1", "generated_at": now_iso(), "summary": summary, "items": items}
    write_json(args.output if args.output.is_absolute() else REPO_ROOT / args.output, report)
    md_path = args.markdown if args.markdown.is_absolute() else REPO_ROOT / args.markdown
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(markdown_report(report), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
