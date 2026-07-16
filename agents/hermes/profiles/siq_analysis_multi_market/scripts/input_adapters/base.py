"""Shared validation and normalization primitives for analysis source adapters."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ADAPTER_CONTRACT_VERSION = "siq_analysis_source_adapter_v1"
RESEARCH_IDENTITY_FIELDS = ("market", "company_id", "filing_id", "parse_run_id")

RATIO_METRIC_KEYS = {
    "roe",
    "roa",
    "weighted_avg_roe",
    "weighted_average_roe",
    "return_on_equity",
    "return_on_assets",
    "capital_adequacy_ratio",
    "core_tier_1_capital_adequacy_ratio",
    "tier_1_capital_ratio",
    "solvency_ratio",
    "combined_ratio",
    "non_performing_loan_ratio",
    "npl_ratio",
    "cost_income_ratio",
}


class SourceAdapterError(ValueError):
    """Stable, user-safe source adapter failure."""

    def __init__(self, code: str, message: str, *, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})


@dataclass(frozen=True)
class AdapterContext:
    research_target: Mapping[str, Any]
    company_dir: Path
    report_dir: Path
    manifest_path: Path
    manifest: Mapping[str, Any]

    def __post_init__(self) -> None:
        company_dir = self.company_dir.resolve()
        report_dir = self.report_dir.resolve()
        manifest_path = self.manifest_path.resolve()
        _ensure_within(report_dir, company_dir, code="unsafe_path_rejected")
        _ensure_within(manifest_path, report_dir, code="unsafe_path_rejected")


def _ensure_within(path: Path, root: Path, *, code: str) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise SourceAdapterError(code, "resolved path escapes the approved report package") from exc
    return resolved


def read_json(path: Path, *, required: bool = False) -> Any:
    if not path.is_file():
        if required:
            raise SourceAdapterError(
                "source_package_not_ready",
                f"required JSON artifact is missing: {path.name}",
            )
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        if required:
            raise SourceAdapterError(
                "source_package_not_ready",
                f"required JSON artifact is unreadable: {path.name}",
            ) from exc
        return None


def read_text(path: Path, *, required: bool = False) -> str:
    if not path.is_file():
        if required:
            raise SourceAdapterError(
                "source_package_not_ready",
                f"required text artifact is missing: {path.name}",
            )
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        if required:
            raise SourceAdapterError(
                "source_package_not_ready",
                f"required text artifact is unreadable: {path.name}",
            ) from exc
        return ""


def source_family_for_manifest(manifest: Mapping[str, Any]) -> str:
    """Route by document/source characteristics rather than by market."""

    declared = str(manifest.get("source_family") or "").strip().lower()
    if declared:
        return declared
    source_id = str(manifest.get("source_id") or "").strip().lower()
    document_format = str(manifest.get("document_format") or "").strip().lower()
    if "esef" in source_id or "esef" in document_format:
        return "esef_ixbrl"
    if source_id == "sec" or document_format in {"ixbrl", "ixbrl_html", "sec_html"}:
        return "sec_ixbrl"
    if document_format == "pdf" or source_id in {
        "cninfo",
        "sse",
        "szse",
        "bse",
        "hkex",
        "edinet",
        "dart_public",
        "issuer_annual_report",
    }:
        return "pdf_market"
    raise SourceAdapterError(
        "source_adapter_unavailable",
        "the report manifest does not identify a supported source family",
        details={"source_id": source_id, "document_format": document_format},
    )


def validate_research_target(target: Mapping[str, Any], manifest: Mapping[str, Any]) -> dict[str, Any]:
    if str(target.get("schema_version") or "") != "siq_research_target_v1":
        raise SourceAdapterError("research_identity_incomplete", "ResearchTargetV1 is required")
    identity_raw = target.get("research_identity")
    identity = dict(identity_raw) if isinstance(identity_raw, Mapping) else {}
    missing = [field for field in RESEARCH_IDENTITY_FIELDS if not str(identity.get(field) or "").strip()]
    if missing:
        raise SourceAdapterError(
            "research_identity_incomplete",
            "formal analysis requires a complete ResearchIdentity",
            details={"missing_fields": missing},
        )
    manifest_identity = {
        "market": str(manifest.get("market") or "").strip().upper(),
        "company_id": str(manifest.get("company_id") or "").strip(),
        "filing_id": str(manifest.get("filing_id") or "").strip(),
        "parse_run_id": str(manifest.get("parse_run_id") or "").strip(),
    }
    normalized_identity = {
        "market": str(identity.get("market") or "").strip().upper(),
        "company_id": str(identity.get("company_id") or "").strip(),
        "filing_id": str(identity.get("filing_id") or "").strip(),
        "parse_run_id": str(identity.get("parse_run_id") or "").strip(),
    }
    mismatches = [
        field
        for field in RESEARCH_IDENTITY_FIELDS
        if manifest_identity[field] and manifest_identity[field] != normalized_identity[field]
    ]
    if mismatches:
        raise SourceAdapterError(
            "research_identity_mismatch",
            "ResearchTargetV1 does not match the selected report manifest",
            details={"mismatch_fields": mismatches},
        )
    report = target.get("source_report")
    report = dict(report) if isinstance(report, Mapping) else {}
    target_report_id = str(report.get("report_id") or "").strip()
    manifest_report_id = str(manifest.get("report_id") or "").strip()
    if not target_report_id:
        raise SourceAdapterError("source_report_not_found", "ResearchTargetV1 report_id is required")
    if manifest_report_id and target_report_id != manifest_report_id:
        raise SourceAdapterError("research_identity_mismatch", "report_id does not match the manifest")
    return {**target, "research_identity": normalized_identity, "source_report": report}


def _manifest_value(manifest: Mapping[str, Any], key: str) -> str:
    for container_name in ("artifacts", "paths"):
        container = manifest.get(container_name)
        if not isinstance(container, Mapping):
            continue
        value = container.get(key)
        if isinstance(value, Mapping):
            value = value.get("path")
        if str(value or "").strip():
            return str(value).strip()
    return ""


def artifact_path(
    context: AdapterContext,
    *,
    manifest_keys: Sequence[str] = (),
    fallbacks: Sequence[str] = (),
    required: bool = False,
) -> Path | None:
    candidates = [value for key in manifest_keys if (value := _manifest_value(context.manifest, key))]
    candidates.extend(fallbacks)
    for raw in candidates:
        relative = Path(raw)
        if relative.is_absolute() or ".." in relative.parts:
            raise SourceAdapterError("unsafe_path_rejected", "manifest artifact path is unsafe")
        candidate = _ensure_within(context.report_dir / relative, context.report_dir, code="unsafe_path_rejected")
        if candidate.is_symlink():
            raise SourceAdapterError("unsafe_path_rejected", "symbolic-link source artifacts are not allowed")
        if candidate.is_file():
            return candidate
    if required:
        label = ", ".join((*manifest_keys, *fallbacks)) or "artifact"
        raise SourceAdapterError("source_package_not_ready", f"required source artifact missing: {label}")
    return None


def company_artifact_path(context: AdapterContext, candidates: Iterable[str]) -> Path | None:
    for raw in candidates:
        relative = Path(raw)
        if relative.is_absolute() or ".." in relative.parts:
            raise SourceAdapterError("unsafe_path_rejected", "company artifact path is unsafe")
        candidate = _ensure_within(context.company_dir / relative, context.company_dir, code="unsafe_path_rejected")
        if candidate.is_symlink():
            raise SourceAdapterError("unsafe_path_rejected", "symbolic-link source artifacts are not allowed")
        if candidate.is_file():
            return candidate
    return None


def list_payload(payload: Any, *keys: str) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [dict(item) for item in payload if isinstance(item, Mapping)]
    if not isinstance(payload, Mapping):
        return []
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return [dict(item) for item in value if isinstance(item, Mapping)]
    return []


def stable_id(prefix: str, *parts: Any) -> str:
    material = "\x1f".join(str(part or "") for part in parts)
    return f"{prefix}_{hashlib.sha256(material.encode('utf-8')).hexdigest()[:24]}"


def normalize_fact(
    raw: Mapping[str, Any],
    *,
    identity: Mapping[str, Any],
    report: Mapping[str, Any],
    evidence_refs: Sequence[Mapping[str, Any]] = (),
    source_family: str,
) -> dict[str, Any]:
    metric_key = str(raw.get("canonical_name") or raw.get("metric_key") or raw.get("metric_id") or "").strip()
    raw_label = str(raw.get("label") or raw.get("metric_name") or raw.get("local_name") or metric_key).strip()
    source = raw.get("source") if isinstance(raw.get("source"), Mapping) else {}
    raw_period = str(
        raw.get("period_end")
        or raw.get("period_key")
        or raw.get("period")
        or source.get("period")
        or ""
    ).strip()
    report_period = str(report.get("period_end") or "").strip()
    if not report_period and report.get("fiscal_year"):
        report_period = f"{int(report['fiscal_year']):04d}-12-31"
    if _is_iso_date(raw_period):
        period_end = raw_period
    elif len(raw_period) == 4 and raw_period.isdigit():
        period_end = f"{raw_period}{report_period[4:]}" if _is_iso_date(report_period) else f"{raw_period}-12-31"
    else:
        period_end = report_period
    period_start = str(raw.get("period_start") or "").strip() or None
    if period_start and not _is_iso_date(period_start):
        period_start = None
    raw_unit = str(raw.get("unit") or "").strip()
    metric_semantics = _metric_semantics(metric_key, raw_unit)
    ratio_fact = metric_semantics == "ratio"
    per_share_fact = metric_semantics == "per_share"
    currency = None if ratio_fact else _currency_code(raw.get("currency"), raw_unit, report.get("reporting_currency"))
    declared_scale = _positive_number(raw.get("scale"), default=1)
    unit_scale = _unit_scale(raw_unit)
    raw_value = raw.get("raw_value") if raw.get("raw_value") is not None else raw.get("value")
    raw_number = _number(raw_value)
    if raw_number is None:
        raw_number = _number(raw.get("value") if raw.get("value") is not None else raw.get("value_numeric"))
    explicit_normalized_value = _number(raw.get("normalized_value"))
    normalization_warnings = [str(item) for item in raw.get("normalization_warnings") or () if item]
    if ratio_fact or per_share_fact:
        scale = 1
        normalized_value = explicit_normalized_value if explicit_normalized_value is not None else raw_number
        if raw_unit and (
            _currency_code(raw.get("currency"), raw_unit)
            or unit_scale != 1
            or (ratio_fact and not _unit_is_ratio(raw_unit))
        ):
            normalization_warnings.append(
                f"metric_unit_semantics_override:{metric_key}:{raw_unit}"
            )
    elif explicit_normalized_value is not None:
        normalized_value = explicit_normalized_value
        inferred_scale = _inferred_scale(raw_number, explicit_normalized_value)
        scale = inferred_scale if inferred_scale is not None else declared_scale
    else:
        scale = unit_scale if unit_scale != 1 else declared_scale
        normalized_value = raw_number * scale if raw_number is not None else None
        if unit_scale != 1 and declared_scale != unit_scale:
            normalization_warnings.append(
                f"scale_unit_conflict:declared={declared_scale}:unit={raw_unit}:applied={unit_scale}"
            )
    normalized_unit = "%" if ratio_fact else f"{currency}/share" if per_share_fact and currency else "per share" if per_share_fact else currency or raw_unit
    fact_id = str(raw.get("metric_id") or "").strip() or stable_id(
        "fact", identity.get("filing_id"), metric_key, period_end, raw_value, raw.get("segment_key")
    )
    return {
        "schema_version": "siq_normalized_fact_v1",
        "fact_id": fact_id,
        "metric_key": metric_key,
        "raw_label": raw_label,
        "raw_value": raw_value,
        "normalized_value": normalized_value,
        "currency": currency,
        "raw_unit": raw_unit or None,
        "raw_scale": raw.get("scale"),
        "scale": scale,
        "period_start": period_start,
        "period_end": period_end,
        "accounting_standard": raw.get("accounting_standard") or report.get("accounting_standard"),
        "research_identity": dict(identity),
        "evidence_refs": [dict(item) for item in evidence_refs],
        # Renderer metadata; shared contract readers ignore extension fields.
        "value": normalized_value,
        "unit": normalized_unit,
        "period": period_end,
        "fiscal_year": raw.get("fiscal_year") or report.get("fiscal_year"),
        "fiscal_period": raw.get("fiscal_period") or report.get("fiscal_period"),
        "statement_type": raw.get("statement_type"),
        "scope": raw.get("scope") or raw.get("segment_key") or "consolidated",
        "dimensions": raw.get("dimensions") if isinstance(raw.get("dimensions"), Mapping) else {},
        "confidence": raw.get("confidence"),
        "accounting_basis": raw.get("accounting_basis"),
        "concept": raw.get("concept"),
        "context_ref": raw.get("context_ref")
        or ((raw.get("raw") or {}).get("context_id") if isinstance(raw.get("raw"), Mapping) else None),
        "context_signature": raw.get("context_signature"),
        "qtd_ytd_type": raw.get("qtd_ytd_type"),
        "duration_days": raw.get("duration_days"),
        "xbrl_unit_ref": raw.get("unit_ref"),
        "xbrl_unit_definition": raw.get("xbrl_unit_definition"),
        "source_family": source_family,
        "canonical_candidate": raw.get("canonical_candidate"),
        "core_metric_eligible": raw.get("core_metric_eligible", True),
        "semantic_status": raw.get("semantic_status") or "accepted",
        "normalization_warnings": list(dict.fromkeys(normalization_warnings)),
    }


def normalize_evidence_ref(
    raw: Mapping[str, Any],
    *,
    identity: Mapping[str, Any],
    report_id: str,
    source_family: str,
    default_kind: str,
) -> dict[str, Any]:
    nested = raw.get("raw") if isinstance(raw.get("raw"), Mapping) else {}
    kind = str(raw.get("source_type") or raw.get("source_kind") or default_kind).strip()
    locator = {
        key: value
        for key, value in {
            "source_url": raw.get("source_url") or nested.get("source_url"),
            "html_anchor": raw.get("html_anchor") or nested.get("html_anchor"),
            "xpath": raw.get("xpath") or nested.get("xpath"),
            "section": raw.get("section_id") or nested.get("section_id") or raw.get("heading"),
            "xbrl_tag": raw.get("xbrl_tag") or raw.get("concept") or nested.get("concept"),
            "fact_id": raw.get("fact_id") or nested.get("fact_id"),
            "context_ref": raw.get("context_ref") or nested.get("context_ref"),
            "pdf_page": raw.get("pdf_page_number") or raw.get("page_number") or nested.get("pdf_page_number"),
            "table_index": raw.get("table_index") or nested.get("table_index"),
            "row_index": raw.get("row_index") or nested.get("row_index"),
            "column_index": raw.get("column_index") or nested.get("column_index"),
            "md_line": raw.get("md_line") or raw.get("line") or nested.get("md_line") or nested.get("line"),
            "local_path": raw.get("local_path") or nested.get("local_path") or nested.get("file"),
        }.items()
        if value not in (None, "")
    }
    evidence_id = str(raw.get("evidence_id") or "").strip() or stable_id(
        "evidence", identity.get("filing_id"), kind, json.dumps(locator, sort_keys=True, ensure_ascii=False)
    )
    source_url = str(locator.get("source_url") or "").strip() or None
    html_anchor = str(locator.get("html_anchor") or "").strip() or None
    xpath = str(locator.get("xpath") or "").strip() or None
    section_id = str(locator.get("section") or "").strip() or None
    fact_id = str(locator.get("fact_id") or "").strip() or None
    concept = str(locator.get("xbrl_tag") or "").strip() or None
    context_ref = str(locator.get("context_ref") or "").strip() or None
    local_source_id = str(locator.get("local_path") or raw.get("source_id") or f"report:{report_id}").strip()
    pdf_page = _nonnegative_int(locator.get("pdf_page"))
    table_id = str(locator.get("table_index") or "").strip() or None
    md_line = _nonnegative_int(locator.get("md_line"))
    if fact_id or (concept and context_ref):
        normalized_kind = "xbrl_fact"
    elif html_anchor or xpath:
        normalized_kind = "html_anchor"
    elif section_id and (source_url or local_source_id):
        normalized_kind = "html_section"
    elif table_id:
        normalized_kind = "pdf_table"
    elif pdf_page is not None:
        normalized_kind = "pdf_page"
    elif md_line is not None:
        normalized_kind = "markdown_line"
    elif source_url:
        normalized_kind = "source_url"
    else:
        normalized_kind = "chunk"
    quote = str(raw.get("quote_text") or raw.get("quote") or nested.get("quote_text") or "").strip()
    if len(quote) > 2000:
        quote = quote[:1997].rstrip() + "..."
    payload = {
        "schema_version": "siq_evidence_ref_v1",
        "evidence_id": evidence_id,
        "research_identity": dict(identity),
        "report_id": report_id,
        "kind": normalized_kind,
        "source_family": source_family,
        "source_url": source_url,
        "local_source_id": local_source_id,
        "pdf_task_id": str(raw.get("task_id") or nested.get("task_id") or "").strip() or None,
        "pdf_page": pdf_page,
        "table_id": table_id,
        "section_id": section_id,
        "html_anchor": html_anchor,
        "xpath": xpath,
        "xbrl_fact_id": fact_id,
        "xbrl_concept": concept,
        "xbrl_context": context_ref,
        "xbrl_unit": str(raw.get("unit") or nested.get("unit") or "").strip() or None,
        "md_line": md_line,
        "chunk_index": 0 if normalized_kind == "chunk" else None,
        "quote": quote,
        "target": str(raw.get("target") or raw.get("metric_key") or nested.get("metric_key") or "").strip(),
        "section_role": str(raw.get("section_role") or nested.get("section_role") or "").strip() or None,
    }
    return {key: value for key, value in payload.items() if value is not None}


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    text = str(value).replace(",", "").strip()
    negative_parentheses = text.startswith("(") and text.endswith(")")
    if negative_parentheses:
        text = text[1:-1].strip()
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    if negative_parentheses:
        number = -number
    return int(number) if number.is_integer() else number


def _metric_semantics(metric_key: str, unit: str) -> str:
    normalized_key = metric_key.strip().lower()
    if (
        normalized_key in RATIO_METRIC_KEYS
        or normalized_key.endswith(("_ratio", "_margin", "_rate", "_yield", "_roe", "_roa"))
        or normalized_key.startswith(("roe_", "roa_"))
    ):
        return "ratio"
    if normalized_key in {"eps", "basic_eps", "diluted_eps", "parent_nav_per_share", "book_value_per_share"} or normalized_key.endswith(
        ("_eps", "_per_share")
    ):
        return "per_share"
    if _unit_is_ratio(unit):
        return "ratio"
    return "amount"


def _unit_is_ratio(unit: str) -> bool:
    normalized = unit.strip().lower()
    return "%" in normalized or "percent" in normalized or normalized in {"ratio", "pct", "percentage points"}


def _unit_scale(unit: str) -> int | float:
    normalized = " ".join(unit.strip().lower().replace("’", "'").split())
    for tokens, scale in (
        (("billion", "bn", "十亿元"), 1_000_000_000),
        (("亿元",), 100_000_000),
        (("million", "mn", "mio", "百万元"), 1_000_000),
        (("万元",), 10_000),
        (("thousand", "千元"), 1_000),
    ):
        if any(token in normalized for token in tokens):
            return scale
    return 1


def _inferred_scale(raw_value: int | float | None, normalized_value: int | float) -> int | float | None:
    if raw_value in (None, 0):
        return None
    scale = normalized_value / raw_value
    if not math.isfinite(scale) or scale <= 0:
        return None
    rounded = round(scale)
    return rounded if math.isclose(scale, rounded, rel_tol=1e-9, abs_tol=1e-9) else scale


def _positive_number(value: Any, *, default: int | float) -> int | float:
    number = _number(value)
    return number if number is not None and number > 0 else default


def _currency_code(*values: Any) -> str:
    text = " ".join(str(value or "").strip() for value in values).upper()
    for token, code in (
        ("CNY", "CNY"),
        ("RMB", "CNY"),
        ("人民币", "CNY"),
        ("HKD", "HKD"),
        ("港元", "HKD"),
        ("USD", "USD"),
        ("美元", "USD"),
        ("JPY", "JPY"),
        ("日元", "JPY"),
        ("KRW", "KRW"),
        ("韩元", "KRW"),
        ("EUR", "EUR"),
        ("欧元", "EUR"),
        ("GBP", "GBP"),
    ):
        if token in text:
            return code
    for value in values:
        candidate = str(value or "").strip().upper()
        if len(candidate) == 3 and candidate.isalpha():
            return candidate
    return ""


def _nonnegative_int(value: Any) -> int | None:
    number = _number(value)
    if number is None or number < 0 or int(number) != number:
        return None
    return int(number)


def _is_iso_date(value: str) -> bool:
    if len(value) != 10 or value[4:5] != "-" or value[7:8] != "-":
        return False
    try:
        year, month, day = (int(item) for item in value.split("-"))
    except ValueError:
        return False
    return year >= 1900 and 1 <= month <= 12 and 1 <= day <= 31
