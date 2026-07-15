#!/usr/bin/env python3
"""Run a deterministic cross-market financial verifier backtest.

The runner does not call an LLM. It selects real canonical Wiki companies,
renders answers directly from structured Wiki metrics, and exercises the same
evidence supplement, financial guard, and answer-audit path used at runtime.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "apps" / "api"
DEFAULT_WIKI_ROOT = REPO_ROOT / "data" / "wiki"
DEFAULT_OUTPUT = (
    REPO_ROOT
    / "artifacts"
    / "eval-runs"
    / "financial-verifier"
    / "cross_market_financial_verifier_backtest.json"
)
DEFAULT_MARKDOWN = DEFAULT_OUTPUT.with_suffix(".md")
BASELINE_BINDINGS = REPO_ROOT / "datasets" / "eval" / "financial_qa_benchmark" / "v1" / "wiki_static_artifacts.json"
SCHEMA_VERSION = "siq_cross_market_financial_verifier_backtest_v1"
MARKETS = ("CN", "HK", "US", "JP", "KR", "EU")
MARKET_COMPANY_ROOTS = {
    "CN": Path("companies"),
    "HK": Path("hk/companies"),
    "US": Path("us/companies"),
    "JP": Path("jp/companies"),
    "KR": Path("kr/companies"),
    "EU": Path("eu/companies"),
}
IDENTITY_FIELDS = ("market", "company_id", "filing_id", "parse_run_id")
BLOCK_REASON = "financial_claim_mismatch"
VIOLATION_REASON = "value_mismatch"
FORBIDDEN_SUBJECT_MARKERS = (
    ":fixture:",
    "fixture",
    "synthetic",
    "generic",
    "siqresearchengine",
    "sample-company",
    "demo-company",
    "test-company",
)
SIGNED_QUERY_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "apikey",
        "auth_token",
        "source_token",
        "token",
    }
)
GUARD_REASON_RE = re.compile(r"(?m)^guardrail_reason=([^\s]+)\s*$")
GUARD_BLOCKED_RE = re.compile(r"(?m)^guardrail_status=blocked\s*$")
CLEAN_NUMBER_RE = re.compile(
    r"^\s*(?P<open>\()?\s*(?P<sign>[+\-]?)"
    r"(?P<number>(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)"
    r"\s*(?P<close>\))?\s*$"
)


@dataclass(frozen=True)
class MetricSpec:
    keys: tuple[str, ...]
    query_label: str
    answer_label: str


METRIC_SPECS = (
    MetricSpec(("operating_revenue", "total_operating_revenue", "revenue"), "营业收入", "营业收入"),
    MetricSpec(("net_profit", "net_income"), "净利润", "净利润"),
    MetricSpec(("total_assets",), "总资产", "总资产"),
    MetricSpec(("total_liabilities",), "总负债", "总负债"),
    MetricSpec(("gross_profit",), "毛利润", "毛利润"),
)
QUESTION_TEMPLATES = (
    "分析 {market} {ticker} 的{metric}",
    "{market} {ticker} 最新一期{metric}是多少",
    "请核对 {market} {ticker} 的{metric}并给出来源",
    "查看 {market} {ticker} 报告期{metric}",
    "{market} {ticker} {metric}的披露值是多少",
)
@dataclass(frozen=True)
class CompanyCandidate:
    market: str
    ticker: str
    company_dir: Path
    company: Mapping[str, Any]
    score: tuple[int, ...]


@dataclass(frozen=True)
class SourceCase:
    market: str
    ticker: str
    company_dir: Path
    company: Mapping[str, Any]
    question: str
    metric_spec: MetricSpec
    result: Mapping[str, Any]
    row: Mapping[str, Any]
    identity: Mapping[str, str]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _sha256_file(path: Path | None) -> str | None:
    if path is None or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _portable_path(path: Path | str | None) -> str:
    if path in (None, ""):
        return ""
    candidate = Path(str(path))
    try:
        return candidate.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
    except (OSError, ValueError):
        return "[external]" if candidate.is_absolute() else candidate.as_posix()


def _strip_signed_query(value: str) -> str:
    def replace_url(match: re.Match[str]) -> str:
        raw_url = match.group(0)
        try:
            parsed = urlsplit(raw_url)
            if not parsed.scheme or not parsed.netloc:
                return raw_url
            query = [
                (key, item)
                for key, item in parse_qsl(parsed.query, keep_blank_values=True)
                if key.casefold() not in SIGNED_QUERY_KEYS
            ]
            return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))
        except ValueError:
            return raw_url

    return re.sub(r"https?://[^\s)\]}>]+", replace_url, value)


def sanitize_report_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): sanitize_report_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize_report_value(item) for item in value]
    if isinstance(value, Path):
        return _portable_path(value)
    if isinstance(value, str):
        return _strip_signed_query(value)
    return value


def _ticker(company_dir: Path, company: Mapping[str, Any]) -> str:
    value = company.get("ticker") or company.get("stock_code") or company.get("security_code")
    return str(value or company_dir.name.split("-", 1)[0]).strip().upper()


def _subject_text(company_dir: Path, company: Mapping[str, Any]) -> str:
    fields = (
        company_dir.name,
        company.get("company_id"),
        company.get("company_wiki_id"),
        company.get("company_name"),
        company.get("company_short_name"),
        company.get("company_full_name"),
    )
    return " ".join(str(item or "") for item in fields).casefold()


def _baseline_subjects(path: Path = BASELINE_BINDINGS) -> frozenset[tuple[str, str]]:
    payload = _read_json(path)
    bindings = payload.get("bindings") if isinstance(payload.get("bindings"), list) else []
    subjects: set[tuple[str, str]] = set()
    for binding in bindings:
        if not isinstance(binding, Mapping):
            continue
        identity = binding.get("case_identity") if isinstance(binding.get("case_identity"), Mapping) else {}
        market = str(identity.get("market") or "").strip().upper()
        ticker = str(identity.get("ticker") or "").strip().upper()
        if market and ticker:
            subjects.add((market, ticker))
    return frozenset(subjects)


def _is_real_subject(company_dir: Path, company: Mapping[str, Any], market: str) -> bool:
    ticker = _ticker(company_dir, company)
    if not ticker or set(ticker) <= {"0"}:
        return False
    if (market.upper(), ticker) in _baseline_subjects():
        return False
    text = _subject_text(company_dir, company)
    return not any(marker in text for marker in FORBIDDEN_SUBJECT_MARKERS)


def _candidate_score(company_dir: Path, company: Mapping[str, Any]) -> tuple[int, ...]:
    primary_report_id = str(company.get("primary_report_id") or "").strip()
    reports = [item for item in company.get("reports") or [] if isinstance(item, Mapping)]
    primary_report = next(
        (item for item in reports if str(item.get("report_id") or "") == primary_report_id),
        {},
    )
    metrics = company.get("metrics") if isinstance(company.get("metrics"), Mapping) else {}
    by_report = metrics.get("by_report") if isinstance(metrics.get("by_report"), Mapping) else {}
    status = str(company.get("status") or "").casefold()
    report_status = str(primary_report.get("status") or "").casefold()
    report_dir = company_dir / "reports" / primary_report_id
    manifest_exists = bool(
        primary_report_id
        and any((report_dir / name).is_file() for name in ("manifest.json", "artifact_manifest.json"))
    )
    return (
        int(status == "ready"),
        int(bool(primary_report_id)),
        int(report_status == "ready"),
        int(primary_report_id in by_report),
        int(manifest_exists),
        int(bool(company.get("company_id"))),
    )


def discover_company_candidates(wiki_root: Path, market: str) -> list[CompanyCandidate]:
    company_root = wiki_root / MARKET_COMPANY_ROOTS[market]
    grouped: dict[str, list[CompanyCandidate]] = {}
    if not company_root.is_dir():
        return []
    for company_dir in sorted(path for path in company_root.iterdir() if path.is_dir()):
        company_path = company_dir / "company.json"
        if not company_path.is_file():
            continue
        company = _read_json(company_path)
        if not company or not _is_real_subject(company_dir, company, market):
            continue
        ticker = _ticker(company_dir, company)
        candidate = CompanyCandidate(
            market=market,
            ticker=ticker,
            company_dir=company_dir,
            company=company,
            score=_candidate_score(company_dir, company),
        )
        grouped.setdefault(ticker, []).append(candidate)
    selected = [
        sorted(items, key=lambda item: (tuple(-value for value in item.score), item.company_dir.name))[0]
        for _ticker_value, items in sorted(grouped.items())
    ]
    return sorted(selected, key=lambda item: (item.ticker, item.company_dir.name))


def _clean_number(value: Any) -> Decimal | None:
    match = CLEAN_NUMBER_RE.fullmatch(str(value or ""))
    if not match or bool(match.group("open")) != bool(match.group("close")):
        return None
    text = match.group("number").replace(",", "")
    if match.group("sign") == "-" or match.group("open"):
        text = f"-{text}"
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _metric_spec_for_key(metric_key: Any) -> MetricSpec | None:
    key = str(metric_key or "").strip()
    return next((spec for spec in METRIC_SPECS if key in spec.keys), None)


def _row_has_reviewable_evidence(row: Mapping[str, Any]) -> bool:
    evidence_id = str(row.get("evidence_id") or row.get("source_id") or "").strip()
    task_id = str(row.get("task_id") or "").strip()
    has_local_locator = bool(
        task_id
        and any(
            str(row.get(field) or "").strip() not in {"", "未返回"}
            for field in ("pdf_page", "table_index", "md_line")
        )
    )
    has_external_locator = bool(
        str(row.get("source_url") or "").strip()
        and any(str(row.get(field) or "").strip() for field in ("source_anchor", "xbrl_tag"))
    )
    return bool(evidence_id and (has_local_locator or has_external_locator))


def _validation_passes(result: Mapping[str, Any]) -> bool:
    validation = result.get("validation") if isinstance(result.get("validation"), Mapping) else {}
    status = str(validation.get("status") or "not_available").casefold()
    summary = validation.get("summary") if isinstance(validation.get("summary"), Mapping) else {}
    try:
        failed_checks = int(summary.get("fail") or 0)
    except (TypeError, ValueError):
        failed_checks = 0
    return status in {"pass", "warning"} and failed_checks == 0 and not result.get("validation_blocked")


def _complete_identity(context: Mapping[str, Any]) -> dict[str, str]:
    raw = context.get("research_identity") if isinstance(context.get("research_identity"), Mapping) else {}
    identity = {field: str(raw.get(field) or "").strip() for field in IDENTITY_FIELDS}
    return identity if all(identity.values()) else {}


def _row_matches_source(row: Mapping[str, Any], source: Mapping[str, Any]) -> bool:
    return all(
        str(row.get(field) or "") == str(source.get(field) or "")
        for field in ("metric_key", "period", "raw_value", "unit", "currency")
    )


def _select_metric_row(
    rows: Sequence[Mapping[str, Any]],
    subject_index: int,
) -> tuple[MetricSpec, Mapping[str, Any]] | None:
    specs = METRIC_SPECS[subject_index % len(METRIC_SPECS) :] + METRIC_SPECS[: subject_index % len(METRIC_SPECS)]
    for spec in specs:
        candidates = [
            row
            for row in rows
            if str(row.get("metric_key") or "") in spec.keys
            and _clean_number(row.get("raw_value")) is not None
            and str(row.get("unit") or row.get("currency") or "").strip()
            and _row_has_reviewable_evidence(row)
        ]
        if candidates:
            candidates.sort(key=lambda row: (str(row.get("period") or ""), str(row.get("metric_name") or "")), reverse=True)
            return spec, candidates[0]
    return None


def _metric_question(market: str, ticker: str, metric: str, subject_index: int) -> str:
    template = QUESTION_TEMPLATES[subject_index % len(QUESTION_TEMPLATES)]
    return template.format(market=market, ticker=ticker, metric=metric)


def _resolved_result_company_dir(result: Mapping[str, Any]) -> Path | None:
    value = result.get("company_dir")
    if value in (None, ""):
        return None
    try:
        return Path(str(value)).resolve()
    except (OSError, RuntimeError):
        return None


def admit_source_case(
    runtime: Any,
    candidate: CompanyCandidate,
    subject_index: int,
) -> tuple[SourceCase | None, str]:
    inventory_question = f"分析 {candidate.market} {candidate.ticker} 财务表现"
    inventory_result = runtime._three_statement_core_result(inventory_question, None)
    if not isinstance(inventory_result, Mapping) or not inventory_result.get("rows"):
        return None, "structured_metric_miss"
    if _resolved_result_company_dir(inventory_result) != candidate.company_dir.resolve():
        return None, "resolved_company_dir_mismatch"
    if not _validation_passes(inventory_result):
        return None, "validation_not_pass"
    selected = _select_metric_row(
        [row for row in inventory_result.get("rows") or [] if isinstance(row, Mapping)],
        subject_index,
    )
    if selected is None:
        return None, "supported_clean_metric_missing"
    spec, source_row = selected
    question = _metric_question(
        candidate.market,
        candidate.ticker,
        spec.query_label,
        subject_index,
    )
    metric_result = runtime._three_statement_core_result(question, None)
    if not isinstance(metric_result, Mapping) or not metric_result.get("rows"):
        return None, "metric_specific_retrieval_miss"
    if _resolved_result_company_dir(metric_result) != candidate.company_dir.resolve():
        return None, "metric_specific_company_dir_mismatch"
    if not _validation_passes(metric_result):
        return None, "metric_specific_validation_not_pass"
    metric_rows = [row for row in metric_result.get("rows") or [] if isinstance(row, Mapping)]
    exact = next((row for row in metric_rows if _row_matches_source(row, source_row)), None)
    if exact is None:
        exact = next(
            (
                row
                for row in metric_rows
                if str(row.get("metric_key") or "") in spec.keys
                and _clean_number(row.get("raw_value")) is not None
                and _row_has_reviewable_evidence(row)
            ),
            None,
        )
    if exact is None:
        return None, "metric_specific_clean_fact_missing"
    context = runtime._resolved_research_context(question, None)
    identity = _complete_identity(context if isinstance(context, Mapping) else {})
    if not identity:
        return None, "research_identity_incomplete"
    result_company_id = str(metric_result.get("company_id") or "")
    if result_company_id and result_company_id != identity["company_id"]:
        return None, "retrieval_identity_mismatch"
    return (
        SourceCase(
            market=candidate.market,
            ticker=candidate.ticker,
            company_dir=candidate.company_dir,
            company=candidate.company,
            question=question,
            metric_spec=spec,
            result=metric_result,
            row=exact,
            identity=identity,
        ),
        "",
    )


def _currency_code(row: Mapping[str, Any]) -> str:
    explicit = str(row.get("currency") or "").strip().upper()
    if explicit:
        return "CNY" if explicit == "RMB" else explicit
    unit = str(row.get("unit") or "").upper()
    for code in ("CNY", "RMB", "HKD", "USD", "EUR", "GBP", "CHF", "JPY", "KRW"):
        if code in unit:
            return "CNY" if code == "RMB" else code
    if any(term in unit for term in ("人民币", "元", "千元", "万元", "百万元", "亿元")):
        return "CNY"
    return ""


def _display_amount(row: Mapping[str, Any], raw_value: str) -> str:
    unit = str(row.get("unit") or row.get("currency") or "").strip()
    currency = _currency_code(row)
    display = f"{raw_value} {' '.join(unit.split())}".strip()
    unit_currency = _currency_code({"unit": unit})
    if currency and not unit_currency:
        display += f"（币种 {currency}）"
    return display


def render_answer(source: SourceCase, *, raw_value: str | None = None) -> str:
    value = str(source.row.get("raw_value") if raw_value is None else raw_value)
    period = str(source.row.get("period") or "").strip()
    return f"{source.metric_spec.answer_label}在{period}为{_display_amount(source.row, value)}。"


def tamper_raw_value(raw_value: Any) -> str:
    raw_text = str(raw_value or "")
    match = CLEAN_NUMBER_RE.fullmatch(raw_text)
    number = _clean_number(raw_text)
    if match is None or number is None:
        raise ValueError("raw_value must be a clean numeric value")
    decimals = len(match.group("number").partition(".")[2])
    quantum = Decimal(1).scaleb(-decimals)
    tampered = (number * Decimal("1.1")).quantize(quantum, rounding=ROUND_HALF_UP)
    if tampered == number:
        tampered += quantum
    absolute = abs(tampered)
    rendered = f"{absolute:,.{decimals}f}" if decimals else f"{absolute:,.0f}"
    if tampered < 0:
        return f"({rendered})" if match.group("open") else f"-{rendered}"
    return rendered


def _reviewable_locator(reference: Mapping[str, Any]) -> bool:
    task_id = str(reference.get("task_id") or "").strip()
    local = bool(
        task_id
        and task_id != "未返回"
        and any(
            str(reference.get(field) or "").strip() not in {"", "未返回"}
            for field in ("pdf_page", "pdf_page_number", "table_index", "md_line")
        )
    )
    external = bool(
        str(reference.get("source_url") or "").strip()
        and any(str(reference.get(field) or "").strip() for field in ("source_anchor", "xbrl_tag", "html_anchor"))
    )
    return local or external


def _source_reference_summary(
    audit_module: Any,
    evidence_reply: str,
    identity: Mapping[str, str],
) -> dict[str, Any]:
    references = [
        item
        for item in audit_module._extract_source_references(evidence_reply)
        if str(item.get("source_type") or "").startswith("wiki")
    ]
    evidence_complete = any(
        str(item.get("evidence_id") or "").strip()
        and item.get("value", item.get("raw_value")) not in (None, "")
        and str(item.get("unit") or item.get("currency") or "").strip()
        for item in references
    )
    locator_complete = any(_reviewable_locator(item) for item in references)
    identity_complete = any(
        all(str(item.get(field) or "").strip() == identity[field] for field in IDENTITY_FIELDS)
        for item in references
    )
    return {
        "reference_count": len(references),
        "evidence_complete": evidence_complete,
        "locator_complete": locator_complete,
        "identity_complete": identity_complete,
        "source_types": sorted({str(item.get("source_type") or "") for item in references}),
    }


def _guard_reason(final_reply: str) -> str:
    match = GUARD_REASON_RE.search(final_reply or "")
    return match.group(1) if match else ""


def _claim_violations(claim_result: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = claim_result.get("violations")
    return [item for item in raw if isinstance(item, Mapping)] if isinstance(raw, list) else []


def _source_paths(source: SourceCase) -> dict[str, Path | None]:
    metrics_file = source.result.get("metrics_file")
    metrics_path = Path(str(metrics_file)) if metrics_file not in (None, "") else None
    if metrics_path is not None and not metrics_path.is_absolute():
        metrics_path = source.company_dir / metrics_path
    report_id = str(source.result.get("report_id") or source.company.get("primary_report_id") or "")
    manifest = None
    if report_id:
        report_dir = source.company_dir / "reports" / report_id
        manifest = next(
            (path for name in ("manifest.json", "artifact_manifest.json") if (path := report_dir / name).is_file()),
            None,
        )
    return {
        "company_json": source.company_dir / "company.json",
        "metrics_file": metrics_path,
        "manifest": manifest,
    }


def _source_fact_payload(source: SourceCase) -> dict[str, Any]:
    row = source.row
    return {
        key: row.get(key)
        for key in (
            "statement_type",
            "metric_key",
            "metric_name",
            "period",
            "raw_value",
            "normalized_value",
            "unit",
            "currency",
            "scale",
            "evidence_id",
            "task_id",
            "pdf_page",
            "table_index",
            "md_line",
            "source_anchor",
            "xbrl_tag",
        )
        if row.get(key) not in (None, "")
    }


def _source_quality_payload(source: SourceCase) -> dict[str, Any]:
    validation = source.result.get("validation") if isinstance(source.result.get("validation"), Mapping) else {}
    summary = validation.get("summary") if isinstance(validation.get("summary"), Mapping) else {}
    return {
        "company_status": source.company.get("status") or None,
        "report_id": source.result.get("report_id") or source.company.get("primary_report_id"),
        "validation_status": validation.get("status"),
        "validation_summary": dict(summary),
        "hard_gate_passed": _validation_passes(source.result),
    }


def execute_case(
    runtime: Any,
    audit_module: Any,
    source: SourceCase,
    *,
    case_kind: str,
    raw_value: str | None = None,
) -> dict[str, Any]:
    mutation_type = "value_mismatch" if case_kind == "tampered" else None
    case_id = f"cross-market-{source.market.lower()}-{source.ticker.lower()}-{case_kind}"
    answer = render_answer(source, raw_value=raw_value)
    guard_context: dict[str, Any] = {}
    final_reply = runtime.enforce_financial_evidence_contract(source.question, guard_context, answer)
    audit_context = runtime._resolved_research_context(source.question, guard_context)
    audit_context = dict(audit_context if isinstance(audit_context, Mapping) else {})
    # Capture the exact evidence-bound text inspected by the verifier without
    # feeding a pre-enriched answer back through the production guard twice.
    verification_reply = runtime.append_primary_data_evidence_if_needed(
        source.question,
        audit_context,
        answer,
    )
    audit_context["question_id"] = case_id
    audit_context.setdefault(
        "query_plan",
        {
            "mode": "wiki_first",
            "observed_source_types": ["wiki_metrics"],
            "allow_postgres_fallback": False,
        },
    )
    trace = audit_module.build_answer_audit_trace(
        message=source.question,
        final_reply=final_reply,
        raw_reply=verification_reply,
        context=audit_context,
        profile="siq_assistant",
        session_id=f"offline-{case_id}",
        enforce_evidence_contract=True,
    )
    trace = dict(trace)
    trace["trace_id"] = audit_module.answer_audit_trace_id(trace)
    claim_result = trace.get("claim_verifier_result") if isinstance(trace.get("claim_verifier_result"), Mapping) else {}
    violations = _claim_violations(claim_result)
    claim_count = int(claim_result.get("claim_count") or 0)
    blocked = bool(GUARD_BLOCKED_RE.search(final_reply or ""))
    guard_reason = _guard_reason(final_reply)
    has_value_mismatch = any(str(item.get("reason") or "") == VIOLATION_REASON for item in violations)
    reference_summary = _source_reference_summary(audit_module, verification_reply, source.identity)
    expected_blocked = case_kind == "tampered"
    errors: list[str] = []
    if claim_count < 1:
        errors.append("numeric_claim_not_inspected")
    if case_kind == "correct":
        if blocked:
            errors.append("correct_answer_blocked")
        if claim_result.get("allowed") is not True:
            errors.append("correct_claim_verifier_not_allowed")
    else:
        if not blocked:
            errors.append("tampered_answer_not_blocked")
        if guard_reason != BLOCK_REASON:
            errors.append(f"guardrail_reason_expected_{BLOCK_REASON}_got_{guard_reason or 'none'}")
        if not has_value_mismatch:
            errors.append("value_mismatch_violation_missing")
    if not reference_summary["evidence_complete"]:
        errors.append("structured_evidence_missing")
    if not reference_summary["locator_complete"]:
        errors.append("reviewable_locator_missing")
    source_paths = _source_paths(source)
    payload = {
        "case_id": case_id,
        "case_kind": case_kind,
        "mutation_type": mutation_type,
        "market": source.market,
        "ticker": source.ticker,
        "question": source.question,
        "company_dir": _portable_path(source.company_dir),
        "resolved_identity": dict(source.identity),
        "source_fact": _source_fact_payload(source),
        "source_quality": _source_quality_payload(source),
        "source_files": {
            key: {
                "path": _portable_path(path),
                "sha256": _sha256_file(path),
            }
            for key, path in source_paths.items()
        },
        "expected": {
            "blocked": expected_blocked,
            "guardrail_reason": BLOCK_REASON if expected_blocked else None,
            "violation_reason": VIOLATION_REASON if expected_blocked else None,
        },
        "observed": {
            "blocked": blocked,
            "guardrail_reason": guard_reason or None,
            "claim_count": claim_count,
            "claim_allowed": claim_result.get("allowed"),
            "violation_reasons": [str(item.get("reason") or "") for item in violations],
            "resolved_identity_complete": all(source.identity.get(field) for field in IDENTITY_FIELDS),
            **reference_summary,
        },
        "passed": not errors,
        "errors": errors,
        "answer_audit_trace": trace,
    }
    return sanitize_report_value(payload)


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def summarize_results(
    results: Sequence[Mapping[str, Any]],
    *,
    markets: Sequence[str],
    subjects_per_market: int,
    tampered_per_market: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    correct = [item for item in results if item.get("case_kind") == "correct"]
    tampered = [item for item in results if item.get("case_kind") == "tampered"]
    false_positives = [item for item in correct if bool((item.get("observed") or {}).get("blocked"))]
    false_negatives = [item for item in tampered if not bool((item.get("observed") or {}).get("blocked"))]
    wrong_reasons = [
        item
        for item in tampered
        if bool((item.get("observed") or {}).get("blocked"))
        and (
            (item.get("observed") or {}).get("guardrail_reason") != BLOCK_REASON
            or VIOLATION_REASON not in ((item.get("observed") or {}).get("violation_reasons") or [])
        )
    ]
    inspected = [item for item in results if int((item.get("observed") or {}).get("claim_count") or 0) >= 1]
    evidence = [item for item in results if bool((item.get("observed") or {}).get("evidence_complete"))]
    locator = [item for item in results if bool((item.get("observed") or {}).get("locator_complete"))]
    citation_identity = [item for item in results if bool((item.get("observed") or {}).get("identity_complete"))]
    resolved_identity = [
        item for item in results if bool((item.get("observed") or {}).get("resolved_identity_complete"))
    ]
    market_summaries: list[dict[str, Any]] = []
    coverage_passed = True
    for market in markets:
        market_results = [item for item in results if item.get("market") == market]
        market_correct = [item for item in market_results if item.get("case_kind") == "correct"]
        market_tampered = [item for item in market_results if item.get("case_kind") == "tampered"]
        subject_count = len({str(item.get("ticker") or "") for item in market_correct})
        market_passed = (
            subject_count >= subjects_per_market
            and len(market_tampered) >= tampered_per_market
            and all(bool(item.get("passed")) for item in market_results)
        )
        coverage_passed = coverage_passed and subject_count >= subjects_per_market and len(market_tampered) >= tampered_per_market
        market_summaries.append(
            {
                "market": market,
                "subjects": subject_count,
                "correct_cases": len(market_correct),
                "tampered_cases": len(market_tampered),
                "passed_cases": sum(bool(item.get("passed")) for item in market_results),
                "false_positives": sum(bool((item.get("observed") or {}).get("blocked")) for item in market_correct),
                "false_negatives": sum(not bool((item.get("observed") or {}).get("blocked")) for item in market_tampered),
                "wrong_reasons": sum(item in wrong_reasons for item in market_tampered),
                "inspection_rate": _rate(
                    sum(int((item.get("observed") or {}).get("claim_count") or 0) >= 1 for item in market_results),
                    len(market_results),
                ),
                "passed": market_passed,
            }
        )
    summary = {
        "cases": len(results),
        "correct_cases": len(correct),
        "tampered_cases": len(tampered),
        "passed_cases": sum(bool(item.get("passed")) for item in results),
        "false_positive_count": len(false_positives),
        "false_positive_rate": _rate(len(false_positives), len(correct)),
        "false_negative_count": len(false_negatives),
        "false_negative_rate": _rate(len(false_negatives), len(tampered)),
        "wrong_reason_count": len(wrong_reasons),
        "wrong_reason_rate": _rate(len(wrong_reasons), len(tampered)),
        "numeric_claim_inspection_rate": _rate(len(inspected), len(results)),
        "evidence_coverage_rate": _rate(len(evidence), len(results)),
        "locator_coverage_rate": _rate(len(locator), len(results)),
        "resolved_identity_coverage_rate": _rate(len(resolved_identity), len(results)),
        "citation_identity_coverage_rate": _rate(len(citation_identity), len(results)),
        "market_coverage_passed": coverage_passed,
    }
    return summary, market_summaries


def _load_runtime_modules() -> tuple[Any, Any]:
    if str(API_ROOT) not in sys.path:
        sys.path.insert(0, str(API_ROOT))
    from services import agent_chat_runtime as runtime, agent_runtime_answer_audit

    return runtime, agent_runtime_answer_audit


def run_backtest(
    *,
    wiki_root: Path = DEFAULT_WIKI_ROOT,
    markets: Sequence[str] = MARKETS,
    subjects_per_market: int = 10,
    tampered_per_market: int = 5,
    runtime: Any | None = None,
    audit_module: Any | None = None,
) -> dict[str, Any]:
    runtime, audit_module = (runtime, audit_module) if runtime is not None and audit_module is not None else _load_runtime_modules()
    old_mode = os.environ.get("SIQ_FINANCIAL_GUARDRAIL_MODE")
    os.environ["SIQ_FINANCIAL_GUARDRAIL_MODE"] = "block"
    results: list[dict[str, Any]] = []
    inventory: dict[str, Any] = {}
    try:
        for market in markets:
            candidates = discover_company_candidates(wiki_root, market)
            admitted: list[SourceCase] = []
            rejection_counts: Counter[str] = Counter()
            rejected_samples: list[dict[str, str]] = []
            scanned_candidates = 0
            for candidate in candidates:
                scanned_candidates += 1
                source, reason = admit_source_case(runtime, candidate, len(admitted))
                if source is None:
                    rejection_counts[reason or "unknown"] += 1
                    if len(rejected_samples) < 20:
                        rejected_samples.append(
                            {
                                "ticker": candidate.ticker,
                                "company_dir": _portable_path(candidate.company_dir),
                                "reason": reason or "unknown",
                            }
                        )
                    continue
                admitted.append(source)
                if len(admitted) >= subjects_per_market:
                    break
            for source in admitted:
                results.append(execute_case(runtime, audit_module, source, case_kind="correct"))
            for source in admitted[:tampered_per_market]:
                results.append(
                    execute_case(
                        runtime,
                        audit_module,
                        source,
                        case_kind="tampered",
                        raw_value=tamper_raw_value(source.row.get("raw_value")),
                    )
                )
            inventory[market] = {
                "candidate_subjects": len(candidates),
                "scanned_candidates": scanned_candidates,
                "admitted_subjects": len(admitted),
                "required_subjects": subjects_per_market,
                "rejection_counts": dict(sorted(rejection_counts.items())),
                "rejected_samples": rejected_samples,
                "admitted_tickers": [source.ticker for source in admitted],
                "passed": len(admitted) >= subjects_per_market,
            }
    finally:
        if old_mode is None:
            os.environ.pop("SIQ_FINANCIAL_GUARDRAIL_MODE", None)
        else:
            os.environ["SIQ_FINANCIAL_GUARDRAIL_MODE"] = old_mode
    summary, market_summaries = summarize_results(
        results,
        markets=markets,
        subjects_per_market=subjects_per_market,
        tampered_per_market=tampered_per_market,
    )
    expected_cases = len(markets) * (subjects_per_market + tampered_per_market)
    summary["expected_cases"] = expected_cases
    summary["case_count_complete"] = len(results) == expected_cases
    passed = (
        bool(results)
        and len(results) == expected_cases
        and summary["market_coverage_passed"]
        and summary["false_positive_count"] == 0
        and summary["false_negative_count"] == 0
        and summary["wrong_reason_count"] == 0
        and summary["numeric_claim_inspection_rate"] == 1.0
        and summary["evidence_coverage_rate"] == 1.0
        and summary["locator_coverage_rate"] == 1.0
        and all(bool(item.get("passed")) for item in results)
    )
    return sanitize_report_value(
        {
            "schema_version": SCHEMA_VERSION,
            "created_at": utc_now_iso(),
            "config": {
                "markets": list(markets),
                "subjects_per_market": subjects_per_market,
                "tampered_per_market": tampered_per_market,
                "expected_cases": expected_cases,
                "source": "real_wiki_metrics",
                "llm_called": False,
                "tampered_cases_are_agent_outputs": False,
                "tampered_mutation": "deterministic_10_percent_value_change",
                "guardrail_mode": "block",
                "selection": "canonical_non_baseline_company_then_supported_clean_metric",
                "baseline_binding": _portable_path(BASELINE_BINDINGS),
                "excluded_baseline_subjects": [
                    f"{market}:{ticker}" for market, ticker in sorted(_baseline_subjects())
                ],
            },
            "inventory": inventory,
            "passed": passed,
            "summary": summary,
            "markets": market_summaries,
            "results": results,
        }
    )


def render_markdown(report: Mapping[str, Any]) -> str:
    summary = report.get("summary") if isinstance(report.get("summary"), Mapping) else {}
    config = report.get("config") if isinstance(report.get("config"), Mapping) else {}
    inventory = report.get("inventory") if isinstance(report.get("inventory"), Mapping) else {}
    lines = [
        "# Cross-Market Financial Verifier Backtest",
        "",
        f"Status: **{'PASS' if report.get('passed') else 'FAIL'}**",
        "",
        f"- Cases: {summary.get('passed_cases', 0)}/{summary.get('cases', 0)} passed (expected {summary.get('expected_cases', 0)})",
        f"- Source-backed correct controls: {summary.get('correct_cases', 0)} (deterministic; no LLM call)",
        f"- Intentional tampered negative controls: {summary.get('tampered_cases', 0)} (not agent outputs)",
        f"- False positives: {summary.get('false_positive_count', 0)} ({summary.get('false_positive_rate', 0):.3f})",
        f"- False negatives: {summary.get('false_negative_count', 0)} ({summary.get('false_negative_rate', 0):.3f})",
        f"- Wrong block reasons: {summary.get('wrong_reason_count', 0)} ({summary.get('wrong_reason_rate', 0):.3f})",
        f"- Numeric claim inspection: {summary.get('numeric_claim_inspection_rate', 0):.3f}",
        f"- Evidence coverage: {summary.get('evidence_coverage_rate', 0):.3f}",
        f"- Locator coverage: {summary.get('locator_coverage_rate', 0):.3f}",
        f"- Resolved identity coverage: {summary.get('resolved_identity_coverage_rate', 0):.3f}",
        f"- Citation identity coverage: {summary.get('citation_identity_coverage_rate', 0):.3f}",
        f"- Existing baseline subjects excluded: {len(config.get('excluded_baseline_subjects') or [])}",
        "",
        "| Market | Subjects | Correct | Tampered | FP | FN | Wrong reason | Inspection | Status |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for item in report.get("markets") or []:
        lines.append(
            f"| {item.get('market')} | {item.get('subjects', 0)} | {item.get('correct_cases', 0)} | "
            f"{item.get('tampered_cases', 0)} | {item.get('false_positives', 0)} | "
            f"{item.get('false_negatives', 0)} | {item.get('wrong_reasons', 0)} | "
            f"{item.get('inspection_rate', 0):.3f} | {'PASS' if item.get('passed') else 'FAIL'} |"
        )
    lines.extend(["", "## Tested Subjects", "", "| Market | Tickers |", "| --- | --- |"])
    for market in config.get("markets") or []:
        market_inventory = inventory.get(market) if isinstance(inventory.get(market), Mapping) else {}
        tickers = ", ".join(str(item) for item in market_inventory.get("admitted_tickers") or [])
        lines.append(f"| {market} | {tickers or 'none'} |")
    failures = [item for item in report.get("results") or [] if not item.get("passed")]
    if failures:
        lines.extend(
            [
                "",
                "## Failed Cases",
                "",
                "| Case | Market | Kind | Guard reason | Errors |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for item in failures:
            observed = item.get("observed") or {}
            lines.append(
                f"| {item.get('case_id')} | {item.get('market')} | {item.get('case_kind')} | "
                f"{observed.get('guardrail_reason') or ''} | {', '.join(item.get('errors') or [])} |"
            )
    return "\n".join(lines) + "\n"


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wiki-root", type=Path, default=DEFAULT_WIKI_ROOT)
    parser.add_argument("--markets", default=",".join(MARKETS))
    parser.add_argument("--subjects-per-market", type=int, default=10)
    parser.add_argument("--tampered-per-market", type=int, default=5)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN)
    parser.add_argument("--json", action="store_true", help="Print the complete report.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    markets = tuple(item.strip().upper() for item in str(args.markets).split(",") if item.strip())
    invalid_markets = [market for market in markets if market not in MARKET_COMPANY_ROOTS]
    if invalid_markets:
        raise SystemExit(f"Unsupported markets: {', '.join(invalid_markets)}")
    if args.subjects_per_market <= 0 or args.tampered_per_market < 0:
        raise SystemExit("subjects-per-market must be positive and tampered-per-market cannot be negative")
    if args.tampered_per_market > args.subjects_per_market:
        raise SystemExit("tampered-per-market cannot exceed subjects-per-market")
    report = run_backtest(
        wiki_root=args.wiki_root.expanduser().resolve(),
        markets=markets,
        subjects_per_market=args.subjects_per_market,
        tampered_per_market=args.tampered_per_market,
    )
    output = args.output if args.output.is_absolute() else REPO_ROOT / args.output
    markdown = args.markdown if args.markdown.is_absolute() else REPO_ROOT / args.markdown
    _write_json(output, report)
    markdown.parent.mkdir(parents=True, exist_ok=True)
    markdown.write_text(render_markdown(report), encoding="utf-8")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        summary = report["summary"]
        print(f"{'PASS' if report.get('passed') else 'FAIL'} cross-market financial verifier backtest")
        print(f"Cases: {summary['passed_cases']}/{summary['cases']} (expected {summary['expected_cases']})")
        print(
            "FP/FN/wrong_reason: "
            f"{summary['false_positive_count']}/{summary['false_negative_count']}/{summary['wrong_reason_count']}"
        )
        print(f"Inspection/evidence/locator: {summary['numeric_claim_inspection_rate']:.3f}/"
              f"{summary['evidence_coverage_rate']:.3f}/{summary['locator_coverage_rate']:.3f}")
        print(f"JSON: {output}")
        print(f"Markdown: {markdown}")
    return 0 if report.get("passed") else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
