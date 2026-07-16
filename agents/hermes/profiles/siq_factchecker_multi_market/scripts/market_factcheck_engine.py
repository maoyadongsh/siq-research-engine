#!/usr/bin/env python3
"""Market-neutral fact-checking for resolved non-CN report packages."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

IDENTITY_FIELDS = ("market", "company_id", "filing_id", "parse_run_id")
SUPPORTED_MULTI_MARKETS = frozenset({"HK", "US", "EU", "KR", "JP"})
LOCATOR_FIELDS = (
    "pdf_page",
    "pdf_page_number",
    "chunk_index",
    "html_anchor",
    "xpath",
    "xbrl_fact_id",
    "fact_id",
    "table_id",
    "section_id",
    "md_line",
    "quote",
    "quote_text",
)
SOURCE_FIELDS = (
    "source_path",
    "file",
    "task_id",
    "pdf_task_id",
    "evidence_id",
    "report_id",
    "source_url",
    "local_source_id",
    "local_path",
)
ISO_CURRENCY_CODES = {
    "AED",
    "ARS",
    "AUD",
    "BDT",
    "BGN",
    "BHD",
    "BRL",
    "CAD",
    "CHF",
    "CLP",
    "CNH",
    "CNY",
    "COP",
    "CZK",
    "DKK",
    "EGP",
    "EUR",
    "GBP",
    "HKD",
    "HUF",
    "IDR",
    "ILS",
    "INR",
    "ISK",
    "JPY",
    "KRW",
    "KWD",
    "KZT",
    "MAD",
    "MXN",
    "MYR",
    "NGN",
    "NOK",
    "NZD",
    "OMR",
    "PEN",
    "PHP",
    "PKR",
    "PLN",
    "QAR",
    "RON",
    "RUB",
    "SAR",
    "SEK",
    "SGD",
    "THB",
    "TRY",
    "TWD",
    "UAH",
    "USD",
    "VND",
    "ZAR",
}
CLAIM_SOURCE_METRIC_ALIASES = {
    # PDF-market adapters refine this source label to avoid presenting an
    # insurance sub-line as a general-company revenue metric.
    "insurance_revenue": {"insurance_revenue", "operating_revenue"},
}


@dataclass(frozen=True)
class ResolvedFactcheckTarget:
    research_target: dict[str, Any]
    company_dir: Path
    report_dir: Path
    analysis_artifact: Path
    analysis_sidecar: Path
    metrics_path: Path
    source_map_path: Path
    financial_checks_path: Path | None


def _read_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _scoped_path(
    raw: Any,
    *,
    scope: Path,
    field: str,
    required: bool = True,
) -> Path | None:
    value = str(raw or "").strip()
    if not value:
        if required:
            raise ValueError(f"factcheck target is missing resolved path: {field}")
        return None
    candidate = Path(value).expanduser().resolve()
    try:
        candidate.relative_to(scope.resolve())
    except ValueError as exc:
        raise ValueError(f"factcheck target path escapes approved scope: {field}") from exc
    if required and not candidate.is_file() and not candidate.is_dir():
        raise ValueError(f"factcheck target path is unavailable: {field}")
    return candidate


def load_resolved_target(bundle_path: str | Path, wiki_root: str | Path) -> ResolvedFactcheckTarget:
    payload = _read_json(Path(bundle_path).expanduser().resolve())
    target = payload.get("research_target") if isinstance(payload.get("research_target"), dict) else payload
    identity = target.get("research_identity") if isinstance(target.get("research_identity"), dict) else {}
    missing = [field for field in IDENTITY_FIELDS if not str(identity.get(field) or "").strip()]
    if missing:
        raise ValueError(f"factcheck target ResearchIdentity is incomplete: {', '.join(missing)}")
    market = str(identity.get("market") or "").strip().upper()
    if market == "CN":
        raise ValueError("cn_legacy_pipeline_required")
    if market not in SUPPORTED_MULTI_MARKETS:
        raise ValueError(f"unsupported_multi_market_factcheck_target: {market or 'missing'}")
    paths = payload.get("resolved_paths") if isinstance(payload.get("resolved_paths"), dict) else {}
    root = Path(wiki_root).expanduser().resolve()
    company_dir = _scoped_path(paths.get("company_dir"), scope=root, field="company_dir")
    if company_dir is None or not company_dir.is_dir() or "companies" not in company_dir.relative_to(root).parts:
        raise ValueError("factcheck target company_dir is not a company workspace")
    report_dir = _scoped_path(paths.get("report_dir"), scope=company_dir, field="report_dir")
    analysis = _scoped_path(
        paths.get("analysis_artifact"),
        scope=company_dir / "analysis",
        field="analysis_artifact",
    )
    if report_dir is None or not report_dir.is_dir() or analysis is None or not analysis.is_file():
        raise ValueError("factcheck target report or analysis baseline is unavailable")
    sidecar_raw = paths.get("analysis_sidecar")
    sidecar = _scoped_path(
        sidecar_raw or analysis.with_suffix(".artifact.json"),
        scope=company_dir / "analysis",
        field="analysis_sidecar",
    )
    metrics = _scoped_path(paths.get("metrics_path"), scope=company_dir, field="metrics_path")
    source_map = _scoped_path(paths.get("source_map_path"), scope=company_dir, field="source_map_path")
    financial_checks = _scoped_path(
        paths.get("financial_checks_path"),
        scope=company_dir,
        field="financial_checks_path",
        required=False,
    )
    if sidecar is None or metrics is None or source_map is None:
        raise ValueError("factcheck target evidence package is incomplete")
    sidecar_payload = _read_json(sidecar)
    if sidecar_payload.get("schema_version") != "siq_agent_artifact_v2":
        raise ValueError("factcheck analysis sidecar is not AgentArtifactV2")
    baseline_id = str(payload.get("baseline_analysis_artifact_id") or "").strip()
    if not baseline_id or str(sidecar_payload.get("artifact_id") or "") != baseline_id:
        raise ValueError("factcheck analysis artifact id does not match its sidecar")
    sidecar_identity = _identity_from_sidecar(sidecar_payload)
    if not _identity_matches(identity, sidecar_identity):
        raise ValueError("factcheck analysis ResearchIdentity does not match")
    source_report = target.get("source_report") if isinstance(target.get("source_report"), dict) else {}
    if str(sidecar_payload.get("source_report_id") or "") != str(source_report.get("report_id") or ""):
        raise ValueError("factcheck analysis source report does not match")
    if str(sidecar_payload.get("html_file") or "") != analysis.name:
        raise ValueError("factcheck analysis HTML does not match its sidecar")
    expected_hash = str(sidecar_payload.get("content_hash") or "").removeprefix("sha256:").lower()
    bundle_hash = str(payload.get("baseline_analysis_content_hash") or "").removeprefix("sha256:").lower()
    actual_hash = hashlib.sha256(analysis.read_bytes()).hexdigest()
    if not expected_hash or bundle_hash != expected_hash or actual_hash != expected_hash:
        raise ValueError("factcheck analysis content hash does not match")
    return ResolvedFactcheckTarget(
        research_target=target,
        company_dir=company_dir,
        report_dir=report_dir,
        analysis_artifact=analysis,
        analysis_sidecar=sidecar,
        metrics_path=metrics,
        source_map_path=source_map,
        financial_checks_path=financial_checks,
    )


def _identity_from_sidecar(payload: dict[str, Any]) -> dict[str, Any]:
    target = payload.get("research_target")
    if isinstance(target, dict) and isinstance(target.get("research_identity"), dict):
        return target["research_identity"]
    identity = payload.get("research_identity")
    if isinstance(identity, dict):
        return identity
    metadata = payload.get("metadata")
    if isinstance(metadata, dict) and isinstance(metadata.get("research_identity"), dict):
        return metadata["research_identity"]
    return {}


def _identity_matches(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    return all(str(expected.get(field) or "") == str(actual.get(field) or "") for field in IDENTITY_FIELDS)


def _embedded_identity(value: dict[str, Any]) -> dict[str, Any]:
    nested = value.get("research_identity")
    if isinstance(nested, dict):
        return nested
    return {field: value.get(field) for field in IDENTITY_FIELDS if value.get(field) not in (None, "")}


def _declared_identity_mismatch(expected: dict[str, Any], value: dict[str, Any]) -> bool:
    declared = _embedded_identity(value)
    return bool(declared) and any(
        field in declared and str(declared.get(field) or "") != str(expected.get(field) or "")
        for field in IDENTITY_FIELDS
    )


def _has_locator(value: dict[str, Any]) -> bool:
    has_source = any(value.get(field) not in (None, "", [], {}) for field in SOURCE_FIELDS)
    has_locator = any(value.get(field) not in (None, "", [], {}) for field in LOCATOR_FIELDS)
    has_xbrl_locator = value.get("xbrl_fact_id") not in (None, "") or (
        value.get("xbrl_concept") not in (None, "") and value.get("xbrl_context") not in (None, "")
    )
    return has_source and (has_locator or has_xbrl_locator)


def _walk_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _collect_citations(*payloads: dict[str, Any]) -> list[dict[str, Any]]:
    citations: list[dict[str, Any]] = []
    seen: set[str] = set()
    for payload in payloads:
        for item in _walk_dicts(payload):
            if not _has_locator(item):
                continue
            raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
            normalized = {
                key: item.get(key)
                for key in (
                    "source_type",
                    "research_identity",
                    "source_url",
                    "source_path",
                    "file",
                    "task_id",
                    "pdf_task_id",
                    "evidence_id",
                    "report_id",
                    "local_source_id",
                    "pdf_page",
                    "pdf_page_number",
                    "table_index",
                    "table_id",
                    "section_id",
                    "html_anchor",
                    "xpath",
                    "xbrl_fact_id",
                    "fact_id",
                    "xbrl_concept",
                    "xbrl_context",
                    "xbrl_unit",
                    "md_line",
                    "quote",
                )
                if item.get(key) not in (None, "", [], {})
            }
            if item.get("local_path") and not normalized.get("local_source_id"):
                normalized["local_source_id"] = item["local_path"]
            if item.get("quote_text") and not normalized.get("quote"):
                normalized["quote"] = item["quote_text"]
            if item.get("xbrl_tag") and not normalized.get("xbrl_concept"):
                normalized["xbrl_concept"] = item["xbrl_tag"]
            if item.get("context_ref") and not normalized.get("xbrl_context"):
                normalized["xbrl_context"] = item["context_ref"]
            if raw.get("fact_id") and not normalized.get("xbrl_fact_id"):
                normalized["xbrl_fact_id"] = raw["fact_id"]
            marker = json.dumps(normalized, ensure_ascii=False, sort_keys=True)
            if marker in seen:
                continue
            seen.add(marker)
            citations.append(normalized)
            if len(citations) >= 200:
                return citations
    return citations


def _source_entries(payload: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("entries", "evidence", "items"):
        rows = payload.get(key)
        if isinstance(rows, list):
            return [item for item in rows if isinstance(item, dict)]
    return []


def _numeric_value(row: dict[str, Any]) -> float | None:
    value = next(
        (
            candidate
            for candidate in (
                row.get("normalized_value"),
                row.get("value"),
                row.get("value_numeric"),
                row.get("raw_value"),
            )
            if candidate not in (None, "")
        ),
        None,
    )
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _metric_key(row: dict[str, Any]) -> str:
    return str(row.get("canonical_name") or row.get("metric_key") or row.get("metric_id") or "").strip()


def _metric_period(row: dict[str, Any]) -> str:
    return str(row.get("period_key") or row.get("period_end") or row.get("period") or "").strip()


def _metric_is_ratio(metric_key: str, unit: Any = "") -> bool:
    normalized = metric_key.strip().lower()
    unit_text = str(unit or "").strip().lower()
    return (
        "%" in unit_text
        or "percent" in unit_text
        or normalized.endswith(("_ratio", "_margin", "_rate", "_yield", "_roe", "_roa"))
        or normalized in {"roe", "roa", "weighted_avg_roe", "weighted_average_roe"}
    )


def _unit_scale(unit: Any) -> float:
    normalized = " ".join(str(unit or "").strip().lower().replace("_", " ").replace("’", "'").split())
    if any(token in normalized for token in ("trillion", "兆")):
        return 1_000_000_000_000.0
    if any(token in normalized for token in ("billion", "十亿", "십억")) or re.search(
        r"(?:^|[\s$€£¥])bn(?:\b|$)", normalized
    ):
        return 1_000_000_000.0
    if any(token in normalized for token in ("亿元", "億円", "억")):
        return 100_000_000.0
    if (
        any(token in normalized for token in ("million", "百万", "百萬", "백만", "mio"))
        or re.search(r"(?:^|[\s$€£¥])mn(?:\b|$)", normalized)
        or re.search(r"(?:[a-z]{3}|[$€£¥])\s*'?m(?:\b|$)", normalized)
    ):
        return 1_000_000.0
    if any(token in normalized for token in ("万元", "萬元", "万円")):
        return 10_000.0
    if any(token in normalized for token in ("thousand", "千", "천")):
        return 1_000.0
    return 1.0


def _normalized_metric_value(row: dict[str, Any]) -> float | None:
    normalized = row.get("normalized_value")
    if normalized not in (None, ""):
        try:
            number = float(normalized)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None
    value = row.get("value", row.get("value_numeric", row.get("raw_value")))
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    metric_key = _metric_key(row)
    if _metric_is_ratio(metric_key, row.get("unit")):
        return number
    try:
        declared_scale = float(row.get("scale") or 1)
    except (TypeError, ValueError):
        declared_scale = 1.0
    if not math.isfinite(declared_scale) or declared_scale <= 0:
        declared_scale = 1.0
    return number * max(declared_scale, _unit_scale(row.get("unit")))


def _currency_code(value: Any) -> str:
    text = str(value or "").strip().upper()
    for token, code in {
        "US$": "USD",
        "HK$": "HKD",
        "AU$": "AUD",
        "CA$": "CAD",
        "NT$": "TWD",
        "CN¥": "CNY",
        "RMB": "CNY",
        "人民币": "CNY",
        "港币": "HKD",
        "港元": "HKD",
        "日元": "JPY",
        "韩元": "KRW",
        "€": "EUR",
        "£": "GBP",
        "₩": "KRW",
    }.items():
        if token in text:
            return code
    for candidate in re.findall(r"(?<![A-Z])[A-Z]{3}(?![A-Z])", text):
        if candidate in ISO_CURRENCY_CODES:
            return candidate
    return ""


def _canonical_metric_unit(row: dict[str, Any]) -> str:
    metric_key = _metric_key(row)
    unit = row.get("unit") or row.get("raw_unit") or ""
    if _metric_is_ratio(metric_key, unit):
        return "%"
    return _currency_code(row.get("currency")) or _currency_code(unit) or str(unit).strip().upper()


def _metric_label(row: dict[str, Any]) -> str:
    return str(
        row.get("metric_name") or row.get("local_name") or row.get("label") or row.get("raw_label") or ""
    ).strip()


def _semantic_label_conflict(metric_key: str, label: str) -> str:
    normalized_key = metric_key.strip().lower()
    normalized_label = " ".join(label.strip().lower().split())
    if not normalized_label:
        return ""
    if normalized_key in {"net_income", "net_profit", "parent_net_profit", "net_profit_parent"}:
        subitems = (
            "financial instruments held for trading",
            "financial instruments managed on a fair value basis",
            "net interest income",
            "interest income",
            "insurance service",
            "before tax",
            "operating profit",
            "gross profit",
        )
        if any(token in normalized_label for token in subitems):
            return "源指标原始标签属于利润表子项，不能作为公司净利润"
    if normalized_key in {"revenue", "operating_revenue", "total_revenue"}:
        if "insurance service revenue" in normalized_label:
            return "保险服务收入不能作为公司营业收入或总收入"
    if normalized_key == "total_assets" and any(
        token in normalized_label for token in ("non-current assets", "current assets", "assets held for sale")
    ):
        return "资产子项不能作为总资产"
    if normalized_key == "total_liabilities" and any(
        token in normalized_label for token in ("current liabilities", "non-current liabilities")
    ):
        return "负债子项不能作为总负债"
    return ""


def _claim_source_metric_keys(metric_key: str) -> set[str]:
    normalized = metric_key.strip().lower()
    return CLAIM_SOURCE_METRIC_ALIASES.get(normalized, {normalized})


def _claim_metric_semantic_conflict(metric_key: str, row: dict[str, Any]) -> str:
    source_metric_key = _metric_key(row).strip().lower()
    label = _metric_label(row)
    normalized_label = " ".join(label.strip().lower().lstrip("-–— ").split())
    if metric_key.strip().lower() == "insurance_revenue" and source_metric_key == "operating_revenue":
        if not normalized_label or not any(
            token in normalized_label for token in ("insurance service revenue", "insurance revenue", "保险服务收入")
        ):
            return "原始营业收入指标未明确标注为保险服务收入，不能映射为保险收入"
    return _semantic_label_conflict(metric_key, label)


def _claim_evidence_ids(claim: dict[str, Any]) -> set[str]:
    raw_ids = claim.get("evidence_ids")
    values = (
        {str(item).strip() for item in raw_ids if str(item).strip()}
        if isinstance(raw_ids, (list, tuple, set))
        else set()
    )
    for key in ("evidence_refs", "citations"):
        refs = claim.get(key)
        if not isinstance(refs, list):
            continue
        for ref in refs:
            if isinstance(ref, dict) and str(ref.get("evidence_id") or "").strip():
                values.add(str(ref["evidence_id"]).strip())
    return values


def _claim_has_evidence(claim: dict[str, Any]) -> bool:
    return bool(
        _claim_evidence_ids(claim)
        or (isinstance(claim.get("evidence_refs"), list) and claim["evidence_refs"])
        or (isinstance(claim.get("citations"), list) and claim["citations"])
    )


def _claim_has_traceable_evidence(
    claim: dict[str, Any],
    source_entries: list[dict[str, Any]],
) -> bool:
    for key in ("evidence_refs", "citations"):
        refs = claim.get(key)
        if isinstance(refs, list) and any(isinstance(ref, dict) and _has_locator(ref) for ref in refs):
            return True
    evidence_ids = _claim_evidence_ids(claim)
    return any(
        str(entry.get("evidence_id") or "").strip() in evidence_ids and _has_locator(entry) for entry in source_entries
    )


def _source_metric_evidence_ids(
    row: dict[str, Any],
    source_entries: list[dict[str, Any]],
) -> set[str]:
    raw_ids = row.get("evidence_ids")
    evidence_ids = {str(row.get("evidence_id") or "").strip()}
    if isinstance(raw_ids, (list, tuple, set)):
        evidence_ids.update(str(item).strip() for item in raw_ids if str(item).strip())
    refs = row.get("evidence_refs")
    if isinstance(refs, list):
        evidence_ids.update(
            str(item.get("evidence_id") or "").strip()
            for item in refs
            if isinstance(item, dict) and str(item.get("evidence_id") or "").strip()
        )
    metric_key = _metric_key(row)
    period = _metric_period(row)
    for entry in source_entries:
        nested = entry.get("raw") if isinstance(entry.get("raw"), dict) else {}
        target = str(entry.get("target") or entry.get("metric_key") or nested.get("metric_key") or "").strip()
        entry_period = str(entry.get("period") or entry.get("period_end") or nested.get("period") or "").strip()
        if target == metric_key and _periods_equivalent(entry_period, period):
            evidence_ids.add(str(entry.get("evidence_id") or "").strip())
    evidence_ids.discard("")
    return evidence_ids


def _locator_tokens(value: Any) -> set[str]:
    """Return strong identifiers that bind a claim reference to a metric row."""

    tokens: set[str] = set()
    if not isinstance(value, dict):
        return tokens
    for key in ("evidence_id", "xbrl_fact_id", "fact_id", "raw_fact_id"):
        item = str(value.get(key) or "").strip()
        if item:
            tokens.add(f"id:{item}")
    task_id = str(value.get("pdf_task_id") or value.get("task_id") or "").strip()
    page = value.get("pdf_page") if value.get("pdf_page") is not None else value.get("pdf_page_number")
    table = value.get("table_id") if value.get("table_id") is not None else value.get("table_index")
    line = value.get("md_line") if value.get("md_line") is not None else value.get("line")
    if task_id and page not in (None, ""):
        tokens.add(f"pdf:{task_id}:page:{page}")
    if task_id and table not in (None, ""):
        tokens.add(f"pdf:{task_id}:table:{table}")
    if table not in (None, "") and line not in (None, ""):
        tokens.add(f"table:{table}:line:{line}")
    for key in ("raw", "source"):
        nested = value.get(key)
        if isinstance(nested, dict):
            tokens.update(_locator_tokens(nested))
    for key in ("evidence_refs", "citations"):
        refs = value.get(key)
        if isinstance(refs, list):
            for ref in refs:
                tokens.update(_locator_tokens(ref))
    return tokens


def _claim_evidence_tokens(claim: dict[str, Any]) -> set[str]:
    return {f"id:{item}" for item in _claim_evidence_ids(claim)} | _locator_tokens(claim)


def _metric_evidence_tokens(
    row: dict[str, Any],
    source_entries: list[dict[str, Any]],
) -> set[str]:
    tokens = {f"id:{item}" for item in _source_metric_evidence_ids(row, source_entries)}
    tokens.update(_locator_tokens(row))
    metric_key = _metric_key(row)
    period = _metric_period(row)
    for entry in source_entries:
        nested = entry.get("raw") if isinstance(entry.get("raw"), dict) else {}
        target = str(entry.get("target") or entry.get("metric_key") or nested.get("metric_key") or "").strip()
        entry_period = str(entry.get("period") or entry.get("period_end") or nested.get("period") or "").strip()
        if target == metric_key and _periods_equivalent(entry_period, period):
            tokens.update(_locator_tokens(entry))
    return tokens


def _periods_equivalent(left: Any, right: Any) -> bool:
    left_value = str(left or "").strip()
    right_value = str(right or "").strip()
    if not left_value or not right_value:
        return True
    if left_value == right_value:
        return True
    left_year = _report_year(left_value)
    right_year = _report_year(right_value)
    return bool(
        left_year
        and left_year == right_year
        and (re.fullmatch(r"20\d{2}", left_value) or re.fullmatch(r"20\d{2}", right_value))
    )


def _numbers_match(expected: float, actual: float) -> bool:
    tolerance = max(abs(expected), abs(actual), 1.0) * 1e-9
    return abs(expected - actual) <= tolerance


def _claim_result(
    claim: dict[str, Any],
    rows: list[dict[str, Any]],
    source_entries: list[dict[str, Any]],
) -> dict[str, Any]:
    claim_id = str(claim.get("claim_id") or "").strip()
    claim_text = str(claim.get("claim") or claim.get("text") or "").strip()
    claim_type = str(claim.get("claim_type") or "").strip().lower()
    raw_evidence_refs = claim.get("evidence_refs")
    if not isinstance(raw_evidence_refs, list):
        raw_evidence_refs = claim.get("citations")
    result = {
        "claim_id": claim_id,
        "claim": claim_text,
        "claim_type": claim_type,
        "status": "unsupported",
        "reason": "",
        "metric_key": str(claim.get("metric_key") or "").strip(),
        "period": str(claim.get("period") or "").strip(),
        "evidence_refs": [dict(item) for item in raw_evidence_refs or [] if isinstance(item, dict)],
    }
    if not claim_id or not claim_text or not claim_type:
        result["reason"] = "结构化声明缺少 claim_id、claim 或 claim_type"
        return result
    if not _claim_has_evidence(claim):
        result["reason"] = "声明未绑定证据"
        return result
    if not _claim_has_traceable_evidence(claim, source_entries):
        result["reason"] = "声明证据缺少可回溯定位"
        return result
    if claim_type in {"disclosure", "risk_disclosure", "governance_disclosure"}:
        result["status"] = "verified"
        return result
    if claim_type not in {"metric_value", "metric_change"}:
        result["reason"] = f"不支持的结构化声明类型: {claim_type}"
        return result

    metric_key = result["metric_key"]
    period = result["period"]
    expected_value = claim.get("normalized_value")
    if not metric_key or not period or expected_value in (None, ""):
        result["reason"] = "数值声明缺少 metric_key、period 或 normalized_value"
        return result
    try:
        expected_number = float(expected_value)
    except (TypeError, ValueError):
        result["reason"] = "声明 normalized_value 不是有限数值"
        return result
    if not math.isfinite(expected_number):
        result["reason"] = "声明 normalized_value 不是有限数值"
        return result
    source_metric_keys = _claim_source_metric_keys(metric_key)
    candidates = [
        row for row in rows if _metric_key(row).strip().lower() in source_metric_keys and _metric_period(row) == period
    ]
    if not candidates:
        result["reason"] = "源指标中不存在相同 metric_key 与 period"
        return result
    semantic_conflicts = [
        conflict for row in candidates if (conflict := _claim_metric_semantic_conflict(metric_key, row))
    ]
    semantically_valid = [row for row in candidates if not _claim_metric_semantic_conflict(metric_key, row)]
    if not semantically_valid:
        result["status"] = "contradicted"
        result["reason"] = semantic_conflicts[0] if semantic_conflicts else "源指标语义与声明不一致"
        return result
    value_matches = [
        row
        for row in semantically_valid
        if (actual := _normalized_metric_value(row)) is not None and _numbers_match(expected_number, actual)
    ]
    if not value_matches:
        result["status"] = "contradicted"
        result["reason"] = "声明数值与源指标归一化数值不一致"
        return result
    expected_unit = (
        "%"
        if _metric_is_ratio(metric_key, claim.get("unit"))
        else (
            _currency_code(claim.get("currency"))
            or _currency_code(claim.get("unit"))
            or str(claim.get("unit") or "").strip().upper()
        )
    )
    if expected_unit:
        unit_matches = [row for row in value_matches if _canonical_metric_unit(row) == expected_unit]
        if not unit_matches:
            result["status"] = "contradicted"
            result["reason"] = "声明单位或币种与源指标不一致"
            return result
        value_matches = unit_matches
    claim_evidence_ids = _claim_evidence_ids(claim)
    source_evidence_ids = set().union(*(_source_metric_evidence_ids(row, source_entries) for row in value_matches))
    if claim_evidence_ids and source_evidence_ids and claim_evidence_ids.isdisjoint(source_evidence_ids):
        result["status"] = "contradicted"
        result["reason"] = "声明证据与对应源指标证据不一致"
        return result
    claim_evidence_tokens = _claim_evidence_tokens(claim)
    current_evidence_tokens = set().union(*(_metric_evidence_tokens(row, source_entries) for row in value_matches))
    if not current_evidence_tokens:
        result["reason"] = "对应源指标缺少证据标识，无法核验声明证据"
        return result
    if claim_evidence_tokens.isdisjoint(current_evidence_tokens):
        result["status"] = "contradicted"
        result["reason"] = "声明证据与对应源指标证据不一致"
        return result

    if claim_type == "metric_change":
        comparison_period = str(claim.get("comparison_period") or "").strip()
        comparison_value = claim.get("comparison_value")
        change_pct = claim.get("change_pct")
        if not comparison_period or comparison_value in (None, "") or change_pct in (None, ""):
            result["reason"] = "同比声明缺少 comparison_period、comparison_value 或 change_pct"
            return result
        try:
            comparison_number = float(comparison_value)
            expected_change = float(change_pct)
        except (TypeError, ValueError):
            result["reason"] = "同比声明的比较数值不是有限数值"
            return result
        if not math.isfinite(comparison_number) or not math.isfinite(expected_change):
            result["reason"] = "同比声明的比较数值不是有限数值"
            return result
        previous_rows = [
            row
            for row in rows
            if _metric_key(row).strip().lower() in source_metric_keys
            and _metric_period(row) == comparison_period
            and not _claim_metric_semantic_conflict(metric_key, row)
            and (not expected_unit or _canonical_metric_unit(row) == expected_unit)
        ]
        matched_previous_rows = [
            row
            for row in previous_rows
            if (value := _normalized_metric_value(row)) is not None and _numbers_match(comparison_number, value)
        ]
        if not matched_previous_rows:
            result["status"] = "contradicted"
            result["reason"] = "同比声明的比较期数值与源指标不一致"
            return result
        previous_evidence_tokens = set().union(
            *(_metric_evidence_tokens(row, source_entries) for row in matched_previous_rows)
        )
        if not previous_evidence_tokens:
            result["reason"] = "比较期源指标缺少证据标识，无法核验同比声明"
            return result
        if claim_evidence_tokens.isdisjoint(previous_evidence_tokens):
            result["status"] = "contradicted"
            result["reason"] = "同比声明未绑定比较期源指标证据"
            return result
        if comparison_number == 0:
            result["reason"] = "比较期数值为零，不能核验同比百分比"
            return result
        actual_change = (expected_number - comparison_number) / abs(comparison_number) * 100
        if abs(expected_change - actual_change) > 0.011:
            result["status"] = "contradicted"
            result["reason"] = "同比声明的变化百分比重算不一致"
            return result

    result["status"] = "verified"
    result["reason"] = ""
    return result


def _report_year(period: Any) -> int | None:
    match = re.search(r"(?:^|\D)(20\d{2})(?:\D|$)", str(period or ""))
    return int(match.group(1)) if match else None


def _period_is_after(period: Any, report_period_end: Any) -> bool:
    period_text = str(period or "").strip()
    report_text = str(report_period_end or "").strip()
    if not period_text or not report_text:
        return False
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", period_text) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", report_text):
        return period_text > report_text
    period_year = _report_year(period_text)
    report_year = _report_year(report_text)
    return bool(period_year and report_year and period_year > report_year)


def _bind_sec_metric_identity(
    row: dict[str, Any],
    authoritative_identity: dict[str, Any],
) -> dict[str, Any]:
    # SEC normalized facts carry the XBRL extraction child-run id in a flat
    # parse_run_id field, while ResearchIdentity uses the authoritative report
    # package run from manifest.json. Only project legacy flat rows when their
    # filing boundary is exact. Explicit nested identities remain authoritative
    # and are never overwritten, so cross-company/report rows still fail closed.
    if isinstance(row.get("research_identity"), dict):
        return row
    filing_id = str(row.get("filing_id") or "")
    if not filing_id or filing_id != str(authoritative_identity.get("filing_id") or ""):
        return row
    for field in ("market", "company_id"):
        declared = str(row.get(field) or "")
        if declared and declared != str(authoritative_identity.get(field) or ""):
            return row
    bound = dict(row)
    child_run_id = str(row.get("parse_run_id") or "")
    if child_run_id and child_run_id != str(authoritative_identity.get("parse_run_id") or ""):
        bound["source_parse_run_id"] = child_run_id
    bound["research_identity"] = dict(authoritative_identity)
    return bound


def _normalized_metric_rows(
    payload: dict[str, Any],
    *,
    authoritative_identity: dict[str, Any] | None = None,
    report_period_end: str = "",
) -> list[dict[str, Any]]:
    rows = payload.get("metrics")
    if not isinstance(rows, list):
        rows = payload.get("facts")
    if isinstance(rows, list):
        normalized_rows = [row for row in rows if isinstance(row, dict)]
        if authoritative_identity and str(payload.get("schema_version") or "") == "sec_normalized_metrics_v1":
            return [_bind_sec_metric_identity(row, authoritative_identity) for row in normalized_rows]
        return normalized_rows
    data = payload.get("data")
    if not isinstance(data, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        values = item.get("values") if isinstance(item.get("values"), dict) else {}
        raw_values = item.get("raw_values") if isinstance(item.get("raw_values"), dict) else {}
        sources = item.get("sources") if isinstance(item.get("sources"), dict) else {}
        evidence_by_period = (
            item.get("evidence_refs_by_period") if isinstance(item.get("evidence_refs_by_period"), dict) else {}
        )
        for period, value in values.items():
            period_key = str(period)
            if re.fullmatch(r"20\d{2}", period_key) and re.fullmatch(
                rf"{re.escape(period_key)}-\d{{2}}-\d{{2}}",
                report_period_end,
            ):
                period_key = report_period_end
            elif re.fullmatch(r"20\d{2}", period_key) and re.fullmatch(
                r"20\d{2}-(\d{2}-\d{2})",
                report_period_end,
            ):
                period_key = f"{period_key}-{report_period_end[5:]}"
            elif (
                re.fullmatch(r"20\d{2}", period_key)
                and str((authoritative_identity or {}).get("market") or "").upper() == "CN"
            ):
                period_key = f"{period_key}-12-31"
            normalized.append(
                {
                    "canonical_name": item.get("canonical_name") or item.get("name"),
                    "metric_name": item.get("name") or item.get("label"),
                    # CN key_metrics values are already normalized to base
                    # currency; scale applies to raw_values only.
                    "normalized_value": value,
                    "raw_value": raw_values.get(period),
                    "period_key": period_key,
                    "unit": item.get("unit"),
                    "scale": item.get("scale"),
                    "source": sources.get(period) if isinstance(sources.get(period), dict) else {},
                    "evidence_refs": evidence_by_period.get(period, []),
                }
            )
    return normalized


def _financial_check_failures(payload: dict[str, Any]) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for item in _walk_dicts(payload):
        status = str(item.get("status") or item.get("result") or "").strip().lower()
        passed = item.get("passed")
        if status in {"fail", "failed", "error", "invalid"} or passed is False:
            failures.append(item)
    return failures[:50]


def _issue(severity: str, message: str, evidence_refs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "severity": severity,
        "message": message,
        "evidence_refs": evidence_refs or [],
    }


def _check(status: str, issues: list[dict[str, Any]]) -> dict[str, Any]:
    return {"status": status, "issues": issues}


def run_market_factcheck(target: ResolvedFactcheckTarget) -> dict[str, Any]:
    identity = target.research_target["research_identity"]
    market = str(identity["market"]).upper()
    sidecar = _read_json(target.analysis_sidecar)
    metrics_payload = _read_json(target.metrics_path)
    source_map = _read_json(target.source_map_path)
    financial_checks = _read_json(target.financial_checks_path)
    citations = _collect_citations(sidecar, source_map, metrics_payload)
    source_report = (
        target.research_target.get("source_report")
        if isinstance(target.research_target.get("source_report"), dict)
        else {}
    )
    rows = _normalized_metric_rows(
        metrics_payload,
        authoritative_identity=identity,
        report_period_end=str(source_report.get("period_end") or ""),
    )
    report_id = str(source_report.get("report_id") or "")
    metadata = sidecar.get("metadata") if isinstance(sidecar.get("metadata"), dict) else {}
    declared_evidence_refs: list[dict[str, Any]] = list(_source_entries(source_map))
    metadata_citations = metadata.get("citations")
    if isinstance(metadata_citations, list):
        declared_evidence_refs.extend(item for item in metadata_citations if isinstance(item, dict))
    for key in ("claims", "claim_verdicts", "key_claims"):
        claim_rows = metadata.get(key)
        if not isinstance(claim_rows, list):
            continue
        for claim in claim_rows:
            if not isinstance(claim, dict):
                continue
            for ref_key in ("evidence_refs", "citations"):
                refs = claim.get(ref_key)
                if isinstance(refs, list):
                    declared_evidence_refs.extend(item for item in refs if isinstance(item, dict))
    for row in rows:
        refs = row.get("evidence_refs")
        if isinstance(refs, list):
            declared_evidence_refs.extend(item for item in refs if isinstance(item, dict))
    untraceable_declared: list[dict[str, Any]] = []
    seen_untraceable: set[str] = set()
    for item in declared_evidence_refs:
        if _has_locator(item):
            continue
        marker = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
        if marker in seen_untraceable:
            continue
        seen_untraceable.add(marker)
        untraceable_declared.append(item)
    mismatched_citations = [
        citation
        for citation in citations
        if _declared_identity_mismatch(identity, citation)
        or (citation.get("report_id") not in (None, "") and str(citation.get("report_id")) != report_id)
    ]
    for citation in citations:
        citation.setdefault("report_id", report_id)
        citation.setdefault("research_identity", identity)

    checks: dict[str, dict[str, Any]] = {}
    identity_issues: list[dict[str, Any]] = []
    sidecar_identity = _identity_from_sidecar(sidecar)
    if not sidecar_identity:
        identity_issues.append(_issue("critical", "分析产物 sidecar 缺少 ResearchIdentity"))
    elif not _identity_matches(identity, sidecar_identity):
        identity_issues.append(_issue("critical", "分析产物与所选源报告 ResearchIdentity 不一致"))
    mismatched_rows = [row for row in rows if _declared_identity_mismatch(identity, row)]
    if mismatched_rows:
        identity_issues.append(_issue("critical", f"{len(mismatched_rows)} 条指标来自其他公司、报告或解析批次"))
    if mismatched_citations:
        identity_issues.append(_issue("critical", f"{len(mismatched_citations)} 条引用来自其他公司、报告或解析批次"))
    checks["identity_consistency"] = _check("fail" if identity_issues else "pass", identity_issues)

    data_issues: list[dict[str, Any]] = []
    if not rows:
        data_issues.append(_issue("critical", "未取得结构化财务指标"))
    invalid_numeric = [row for row in rows if _numeric_value(row) is None]
    if invalid_numeric:
        data_issues.append(_issue("critical", f"{len(invalid_numeric)} 条指标不是有限数值"))
    missing_unit = [
        row
        for row in rows
        if _numeric_value(row) is not None and not str(row.get("unit") or row.get("currency") or "").strip()
    ]
    if missing_unit:
        data_issues.append(_issue("warning", f"{len(missing_unit)} 条指标缺少单位或币种"))
    ratio_unit_conflicts = [
        row
        for row in rows
        if _metric_is_ratio(_metric_key(row))
        and (
            _currency_code(row.get("currency"))
            or (_currency_code(row.get("unit")) and "%" not in str(row.get("unit") or ""))
        )
    ]
    if ratio_unit_conflicts:
        data_issues.append(_issue("warning", f"{len(ratio_unit_conflicts)} 条比例指标被标注为货币单位"))
    reporting_currency = (
        _currency_code(source_report.get("reporting_currency"))
        or str(source_report.get("reporting_currency") or "").upper()
    )
    conflicting_units: dict[tuple[str, str], set[str]] = {}
    for row in rows:
        canonical = str(row.get("canonical_name") or row.get("metric_key") or "")
        period = str(row.get("period_key") or row.get("period") or row.get("period_end") or "")
        unit = _canonical_metric_unit(row)
        if canonical and period and unit:
            conflicting_units.setdefault((canonical, period), set()).add(unit)
    conflicts = [key for key, units in conflicting_units.items() if len(units) > 1]
    if conflicts:
        data_issues.append(_issue("warning", f"{len(conflicts)} 个指标期间存在多单位口径"))
    if reporting_currency:
        foreign_monetary = [
            row
            for row in rows
            if _currency_code(row.get("currency") or row.get("unit"))
            and _currency_code(row.get("currency") or row.get("unit")) != reporting_currency
        ]
        if foreign_monetary:
            data_issues.append(
                _issue("warning", f"{len(foreign_monetary)} 条指标币种与报告币种 {reporting_currency} 不同")
            )
    failed_financial_checks = _financial_check_failures(financial_checks)
    if failed_financial_checks:
        data_issues.append(
            _issue(
                "warning",
                f"源报告 financial checks 有 {len(failed_financial_checks)} 项失败或异常",
            )
        )
    checks["data_consistency"] = _check(
        "fail"
        if any(item["severity"] == "critical" for item in data_issues)
        else ("warning" if data_issues else "pass"),
        data_issues,
    )

    period_issues: list[dict[str, Any]] = []
    fiscal_year = source_report.get("fiscal_year")
    period_end = str(source_report.get("period_end") or "")
    future_rows = [
        row
        for row in rows
        if period_end
        and _period_is_after(
            row.get("period_key") or row.get("period") or row.get("period_end"),
            period_end,
        )
    ]
    if future_rows:
        period_issues.append(_issue("critical", f"{len(future_rows)} 条指标期间晚于所选源报告截止日"))
    years = {
        year
        for row in rows
        if (year := _report_year(row.get("period_key") or row.get("period") or row.get("period_end")))
    }
    if fiscal_year and years and int(fiscal_year) not in years:
        period_issues.append(_issue("warning", "结构化指标不包含所选报告财年"))
    checks["period_consistency"] = _check(
        "fail"
        if any(item["severity"] == "critical" for item in period_issues)
        else ("warning" if period_issues else "pass"),
        period_issues,
    )

    calculation_issues: list[dict[str, Any]] = []
    if not financial_checks:
        calculation_issues.append(_issue("warning", "源报告未提供 financial checks，算术和同比只能做有限核验"))
    if failed_financial_checks:
        calculation_issues.append(_issue("critical", f"financial checks 有 {len(failed_financial_checks)} 项失败"))
    checks["calculation_consistency"] = _check(
        "fail"
        if any(item["severity"] == "critical" for item in calculation_issues)
        else ("warning" if calculation_issues else "pass"),
        calculation_issues,
    )

    trace_issues: list[dict[str, Any]] = []
    if not source_map:
        trace_issues.append(_issue("critical", "源报告 source map 不可用"))
    if not citations:
        trace_issues.append(_issue("critical", "分析产物没有可核验引用"))
    if mismatched_citations:
        trace_issues.append(_issue("critical", f"{len(mismatched_citations)} 条引用身份与所选源报告不一致"))
    untraceable = untraceable_declared
    if untraceable:
        trace_issues.append(_issue("critical", f"{len(untraceable)} 条引用缺少定位信息"))
    checks["traceability"] = _check("fail" if trace_issues else "pass", trace_issues)

    claim_issues: list[dict[str, Any]] = []
    claims = []
    seen_claims: set[str] = set()
    for key in ("claims", "claim_verdicts", "key_claims"):
        value = metadata.get(key)
        if isinstance(value, list):
            for item in value:
                if not isinstance(item, dict):
                    continue
                marker = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
                if marker in seen_claims:
                    continue
                seen_claims.add(marker)
                claims.append(item)
    claim_results = [_claim_result(claim, rows, _source_entries(source_map)) for claim in claims]
    unsupported_claims = [item for item in claim_results if item["status"] == "unsupported"]
    contradicted_claims = [item for item in claim_results if item["status"] == "contradicted"]
    verified_claims = [item for item in claim_results if item["status"] == "verified"]
    if contradicted_claims:
        claim_issues.append(
            _issue(
                "critical",
                f"{len(contradicted_claims)} 条分析声明与源指标、单位、期间、语义或证据不一致",
            )
        )
    if unsupported_claims:
        claim_issues.append(_issue("warning", f"{len(unsupported_claims)} 条分析声明只能做有限核查"))
    if not claims:
        claim_issues.append(
            _issue(
                "critical" if not citations else "warning",
                (
                    "分析产物没有可核验声明或引用"
                    if not citations
                    else "分析产物未提供结构化声明清单；本次仅完成数据、身份与引用层有限核查"
                ),
            )
        )
    checks["claim_support"] = _check(
        "fail"
        if any(item["severity"] == "critical" for item in claim_issues)
        else ("warning" if claim_issues else "pass"),
        claim_issues,
    )

    logic_issues: list[dict[str, Any]] = []
    if not sidecar.get("content_hash"):
        logic_issues.append(_issue("warning", "分析产物 sidecar 缺少 content_hash"))
    if not sidecar.get("source_report_id") and not (
        isinstance(sidecar.get("research_target"), dict)
        and isinstance(sidecar["research_target"].get("source_report"), dict)
        and sidecar["research_target"]["source_report"].get("report_id")
    ):
        logic_issues.append(_issue("warning", "分析产物未显式记录 source_report_id"))
    checks["logic_support"] = _check("warning" if logic_issues else "pass", logic_issues)

    risk_issues: list[dict[str, Any]] = []
    analysis_text = target.analysis_artifact.read_text(encoding="utf-8", errors="replace").lower()
    if market == "US":
        for section in ("risk_factors.md", "mda.md"):
            if not (target.report_dir / "sections" / section).is_file():
                risk_issues.append(_issue("warning", f"SEC 报告缺少 sections/{section}"))
        form_type = str(source_report.get("form_type") or source_report.get("form") or "").upper()
        if form_type not in {"10-K", "10-Q"}:
            risk_issues.append(_issue("warning", "SEC 报告表单类型不是 10-K/10-Q"))
    elif market == "CN":
        cn_risk_groups = (
            ("审计", "非标", "持续经营"),
            ("问询", "处罚", "立案", "监管"),
            ("质押", "冻结", "减持", "解禁"),
            ("商誉", "减值", "债务"),
        )
        missing_groups = [group for group in cn_risk_groups if not any(term in analysis_text for term in group)]
        if missing_groups:
            risk_issues.append(_issue("warning", f"A 股风险披露有 {len(missing_groups)} 类未在分析产物中识别"))
    elif not source_map:
        risk_issues.append(_issue("warning", "市场风险披露缺少可用 source map"))
    checks["market_risk_completeness"] = _check("warning" if risk_issues else "pass", risk_issues)

    template_issues: list[dict[str, Any]] = []
    if not target.analysis_artifact.is_file() or target.analysis_artifact.stat().st_size == 0:
        template_issues.append(_issue("critical", "分析产物为空"))
    checks["template_compliance"] = _check("fail" if template_issues else "pass", template_issues)

    issues = [issue for check in checks.values() for issue in check["issues"]]
    counts = {
        "critical": sum(1 for item in issues if item["severity"] == "critical"),
        "warning": sum(1 for item in issues if item["severity"] == "warning"),
        "suggestion": sum(1 for item in issues if item["severity"] == "suggestion"),
    }
    verdict = "block" if counts["critical"] else ("request_changes" if counts["warning"] else "approve")
    evidence_summary = citations[:50]
    source_by_evidence_id = {
        str(item.get("evidence_id")): item for item in _source_entries(source_map) if item.get("evidence_id")
    }
    metric_evidence_map: dict[str, dict[str, Any]] = {}
    for row in rows:
        canonical = str(row.get("canonical_name") or row.get("metric_key") or "").strip()
        if not canonical or canonical in metric_evidence_map:
            continue
        refs = row.get("evidence_refs") if isinstance(row.get("evidence_refs"), list) else []
        raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
        raw_fact = raw.get("raw") if isinstance(raw.get("raw"), dict) else {}
        source_entry = source_by_evidence_id.get(str(row.get("evidence_id") or ""), {})
        metric_evidence_map[canonical] = {
            "source_type": "xbrl_fact" if row.get("raw_fact_id") else "normalized_metric",
            "report_id": report_id,
            "source_url": source_entry.get("source_url"),
            "local_source_id": source_entry.get("local_path"),
            "metric_or_claim": canonical,
            "period": row.get("period_key") or row.get("period") or row.get("period_end"),
            "value": row.get("value"),
            "unit": row.get("unit") or row.get("currency"),
            "xbrl_fact_id": row.get("raw_fact_id") or raw_fact.get("fact_id"),
            "html_anchor": source_entry.get("html_anchor") or raw_fact.get("anchor"),
            "evidence_refs": refs,
        }
    recommendations = [
        f"[{name}] {issue['message']}"
        for name, check in checks.items()
        for issue in check["issues"]
        if issue["severity"] in {"critical", "warning"}
    ][:20]
    return {
        "schema_version": "siq_market_factcheck_v1",
        "verdict": verdict,
        "company_id": identity["company_id"],
        "research_identity": identity,
        "report_file": target.analysis_artifact.name,
        "source_report_id": report_id,
        "summary": {
            **counts,
            "database_status": "not_required",
            "database_connection": "not_required",
            "company_evidence_status": "wiki_exact_identity",
            "evidence_rows": len(evidence_summary),
            "local_evidence_rows": len(evidence_summary),
            "metric_evidence_items": len(metric_evidence_map),
            "market": market,
            "checked_claim_count": len(claims),
            "verified_claim_count": len(verified_claims),
            "contradicted_claim_count": len(contradicted_claims),
            "unsupported_claim_count": len(unsupported_claims),
            "identity_mismatch_count": (
                (1 if not sidecar_identity or not _identity_matches(identity, sidecar_identity) else 0)
                + len(mismatched_rows)
                + len(mismatched_citations)
            ),
            "citation_locator_failure_count": len(untraceable),
            "degraded_reasons": [
                f"{name}:{issue['message']}"
                for name, check in checks.items()
                for issue in check["issues"]
                if issue["severity"] in {"critical", "warning"}
            ],
        },
        "checks": checks,
        "claim_verdicts": claim_results,
        "evidence_summary": evidence_summary,
        "metric_evidence_map": metric_evidence_map,
        "calculation_audit": [],
        "recommendations": recommendations,
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }
