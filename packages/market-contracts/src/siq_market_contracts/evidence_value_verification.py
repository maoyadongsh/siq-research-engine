from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .evidence_hashing import stable_id
from .evidence_resolver import iter_financial_data_items
from .financial_value_polarity import (
    FINANCIAL_VALUE_POLARITY_CONTRACT_VERSION,
    CanonicalValuePolarity,
    canonical_value_polarity,
)

_EMPTY_VALUE_TEXT = {"", "-", "--", "---", "n/a", "na", "null", "none"}
_NUMBER_RE = re.compile(r"\(?[-+]?\d[\d,']*(?:\.\d+)?%?\)?")


def _has_value(value: Any) -> bool:
    return value is not None and str(value).strip() != ""


def _first_value(*values: Any) -> Any:
    for value in values:
        if _has_value(value):
            return value
    return None


def _raw_field(payload: dict[str, Any], key: str) -> Any:
    raw = payload.get("raw") if isinstance(payload, dict) else {}
    return raw.get(key) if isinstance(raw, dict) else None


def _value_payload(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _decimal_from_value(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int | float):
        return Decimal(str(value))

    text = str(value).strip()
    if text.lower() in _EMPTY_VALUE_TEXT:
        return None

    negative = False
    if re.fullmatch(r"\(.+\)", text):
        negative = True
        text = text[1:-1]
    text = text.replace("\u2212", "-").replace("\u2013", "-").replace("\u2014", "-")
    text = re.sub(r"(?i)(hk\$|us\$|rmb|cny|hkd|usd|eur|gbp|jpy|krw|\$)", "", text)
    text = re.sub(r"(?i)(million|billion|thousand|mn|bn|m|k)", "", text)
    if re.search(r"[A-Za-z]", text):
        return None
    text = text.replace(",", "").replace("'", "").replace("%", "").strip()
    text = re.sub(r"[^0-9.\-+]", "", text)
    if text.lower() in _EMPTY_VALUE_TEXT or text in {"-", "+", ".", "-.", "+."}:
        return None
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    return -number if negative and number > 0 else number


def _decimals_from_text(value: Any) -> list[Decimal]:
    text = "" if value is None else str(value)
    numbers: list[Decimal] = []
    for match in _NUMBER_RE.findall(text.replace("\u2212", "-").replace("\u2013", "-").replace("\u2014", "-")):
        number = _decimal_from_value(match)
        if number is not None:
            numbers.append(number)
    return numbers


def _scale_factor(value: Any) -> Decimal:
    if value is None or value == "":
        return Decimal("1")
    try:
        number = Decimal(str(value).replace(",", ""))
    except InvalidOperation:
        number = None
    if number is None:
        number = _decimal_from_value(value)
    if number is not None and number > 0:
        return number
    text = str(value).lower()
    if "billion" in text or "bn" in text:
        return Decimal("1000000000")
    if "million" in text or "mn" in text:
        return Decimal("1000000")
    if "thousand" in text:
        return Decimal("1000")
    return Decimal("1")


def _decimal_close(left: Decimal, right: Decimal) -> bool:
    if left == right:
        return True
    tolerance = max(abs(left), abs(right), Decimal("1")) * Decimal("0.000001")
    return abs(left - right) <= tolerance


def _period_token(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    match = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", text)
    if match:
        year, month, day = match.groups()
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
    return text


def _value_matches_expected(
    expected: Any,
    observed: Any,
    *,
    scale: Any = None,
    sign: Any = None,
    polarity: CanonicalValuePolarity = "signed",
) -> bool | None:
    expected_number = _decimal_from_value(expected)
    if expected_number is None:
        return None
    observed_numbers: list[Decimal] = []
    observed_number = _decimal_from_value(observed)
    if observed_number is not None:
        observed_numbers.append(observed_number)
    observed_numbers.extend(_decimals_from_text(observed))
    if not observed_numbers:
        return False

    factor = _scale_factor(scale)
    for number in observed_numbers:
        if str(sign or "").strip() == "-":
            number = -abs(number)
        candidates = [number]
        if factor not in {Decimal("0"), Decimal("1")}:
            candidates.extend([number * factor, number / factor])
        if any(_decimal_close(expected_number, candidate) for candidate in candidates):
            return True
        if polarity == "deduction_magnitude" and expected_number >= 0:
            if any(candidate < 0 and _decimal_close(expected_number, -candidate) for candidate in candidates):
                return True
    return False


def _fact_ref(item: dict[str, Any], period_key: Any) -> str:
    return f"{item.get('canonical_name') or item.get('name') or 'unknown'}:{period_key}"


def _fact_value_fields(item: dict[str, Any], period_key: Any, evidence: dict[str, Any]) -> dict[str, Any]:
    values = item.get("values") if isinstance(item.get("values"), dict) else {}
    raw_values = item.get("raw_values") if isinstance(item.get("raw_values"), dict) else {}
    display_values = item.get("display_values") if isinstance(item.get("display_values"), dict) else {}
    normalized_values = item.get("normalized_values") if isinstance(item.get("normalized_values"), dict) else {}
    value_payload = _value_payload(values.get(period_key))
    base_value = None if isinstance(values.get(period_key), dict) else values.get(period_key)
    quote_text = _first_value(evidence.get("quote_text"), evidence.get("quote"), evidence.get("html_snippet"))
    return {
        "raw_value": _first_value(
            value_payload.get("raw_value"),
            raw_values.get(period_key),
            evidence.get("raw_value"),
            _raw_field(evidence, "raw_value"),
            _raw_field(evidence, "value_text"),
            _raw_field(evidence, "value"),
            evidence.get("cell_text"),
            evidence.get("cell"),
            quote_text,
        ),
        "display_value": _first_value(
            value_payload.get("display_value"),
            display_values.get(period_key),
            value_payload.get("value_text"),
            value_payload.get("raw_value"),
            raw_values.get(period_key),
            base_value,
        ),
        "normalized_value": _first_value(
            value_payload.get("normalized_value"),
            normalized_values.get(period_key),
            value_payload.get("value"),
            base_value,
        ),
        "quote_text": quote_text,
        "scale": _first_value(
            value_payload.get("scale_multiplier"),
            evidence.get("scale_multiplier"),
            _raw_field(evidence, "scale_multiplier"),
            value_payload.get("scale"),
            item.get("scale"),
            evidence.get("scale"),
            _raw_field(evidence, "scale"),
        ),
        "sign": _first_value(value_payload.get("sign"), evidence.get("sign"), _raw_field(evidence, "sign")),
    }


def _evidence_value_issue(
    *,
    fact_ref: str,
    rule: str,
    reason: str,
    item: dict[str, Any],
    period_key: Any,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    issue_id = stable_id("evidence_value_verification", fact_ref, rule, reason)
    return {
        "issue_id": issue_id,
        "fact_ref": fact_ref,
        "rule": rule,
        "reason": reason,
        "canonical_name": item.get("canonical_name") or item.get("name"),
        "period_key": period_key,
        "source_type": evidence.get("source_type"),
        "source_id": evidence.get("source_id"),
        "evidence_id": evidence.get("evidence_id"),
        "evidence_refs": [f"fact:{fact_ref}", f"issue:{issue_id}"],
    }


def _is_pdf_evidence(evidence: dict[str, Any]) -> bool:
    source_type = str(evidence.get("source_type") or "").lower()
    return (
        "pdf" in source_type
        or _has_value(evidence.get("page_number"))
        or _has_value(evidence.get("pdf_page_number"))
        or (_has_value(evidence.get("table_index")) and _has_value(evidence.get("row_index")))
    )


def _is_xbrl_evidence(evidence: dict[str, Any]) -> bool:
    source_type = str(evidence.get("source_type") or "").lower()
    target = str(evidence.get("target") or "").lower()
    return (
        "xbrl" in source_type
        or target.startswith("xbrl:")
        or _has_value(evidence.get("xbrl_tag"))
        or _has_value(evidence.get("tag"))
        or _has_value(_raw_field(evidence, "concept"))
        or _has_value(_raw_field(evidence, "xbrl_tag"))
    )


def _xbrl_field_values(item: dict[str, Any], period_key: Any, evidence: dict[str, Any]) -> dict[str, Any]:
    periods = item.get("periods") if isinstance(item.get("periods"), dict) else {}
    period = periods.get(period_key) if isinstance(periods.get(period_key), dict) else {}
    evidence_period = _first_value(
        evidence.get("period_key"),
        evidence.get("period_end"),
        evidence.get("instant"),
        _raw_field(evidence, "period_key"),
        _raw_field(evidence, "period_end"),
        _raw_field(evidence, "end_date"),
        _raw_field(evidence, "end"),
        _raw_field(evidence, "instant"),
    )
    return {
        "tag": _first_value(evidence.get("xbrl_tag"), evidence.get("tag"), _raw_field(evidence, "xbrl_tag"), _raw_field(evidence, "concept")),
        "context": _first_value(evidence.get("context_ref"), _raw_field(evidence, "context_ref"), _raw_field(evidence, "contextRef"), _raw_field(evidence, "context_id")),
        "unit": _first_value(evidence.get("unit_ref"), evidence.get("unit"), _raw_field(evidence, "unit_ref"), _raw_field(evidence, "unitRef"), _raw_field(evidence, "unit")),
        "period": evidence_period,
        "fact_period": _first_value(period.get("period_end"), period_key),
        "decimals": _first_value(evidence.get("decimals"), _raw_field(evidence, "decimals")),
        "scale": _first_value(
            evidence.get("xbrl_scale_exponent"),
            _raw_field(evidence, "xbrl_scale_exponent"),
            evidence.get("scale"),
            _raw_field(evidence, "scale"),
            evidence.get("scale_multiplier"),
            _raw_field(evidence, "scale_multiplier"),
            item.get("scale"),
        ),
    }


def evidence_value_verification_summary(
    *,
    financial_data: dict[str, Any] | None = None,
    manifest: dict[str, Any] | None = None,
    package_dir: Path | None = None,
) -> dict[str, Any]:
    del package_dir
    manifest = manifest if isinstance(manifest, dict) else {}
    market = (financial_data or {}).get("market") or manifest.get("market")
    fact_count = 0
    checked_fact_count = 0
    verified_fact_count = 0
    pdf_checked_count = 0
    xbrl_checked_count = 0
    quote_checked_count = 0
    pdf_failed_count = 0
    xbrl_failed_count = 0
    quote_failed_count = 0
    issues: list[dict[str, Any]] = []

    for item in iter_financial_data_items(financial_data or {}):
        if not isinstance(item, dict):
            continue
        values = item.get("values") if isinstance(item.get("values"), dict) else {}
        sources = item.get("sources") if isinstance(item.get("sources"), dict) else {}
        for period_key in values:
            fact_count += 1
            evidence = sources.get(period_key) if isinstance(sources, dict) else None
            if not isinstance(evidence, dict) or not evidence:
                continue

            fact_ref = _fact_ref(item, period_key)
            value_fields = _fact_value_fields(item, period_key, evidence)
            normalized_value = value_fields["normalized_value"]
            scale = value_fields["scale"]
            polarity = canonical_value_polarity(
                market,
                item.get("canonical_name") or item.get("name"),
            )
            fact_issue_count = len(issues)
            checked = False

            if _is_pdf_evidence(evidence):
                checked = True
                pdf_checked_count += 1
                raw_value = value_fields["raw_value"]
                display_value = value_fields["display_value"]
                raw_match = _value_matches_expected(
                    normalized_value,
                    raw_value,
                    scale=scale,
                    polarity=polarity,
                )
                display_match = _value_matches_expected(
                    normalized_value,
                    display_value,
                    scale=scale,
                    polarity=polarity,
                )
                if raw_match is not True:
                    issues.append(
                        _evidence_value_issue(
                            fact_ref=fact_ref,
                            rule="pdf.raw_value.explainable",
                            reason="pdf evidence raw value does not explain normalized value",
                            item=item,
                            period_key=period_key,
                            evidence=evidence,
                        )
                    )
                if display_match is not True:
                    issues.append(
                        _evidence_value_issue(
                            fact_ref=fact_ref,
                            rule="pdf.display_value.explainable",
                            reason="pdf evidence display value does not explain normalized value",
                            item=item,
                            period_key=period_key,
                            evidence=evidence,
                        )
                    )

            if _is_xbrl_evidence(evidence):
                checked = True
                xbrl_checked_count += 1
                fields = _xbrl_field_values(item, period_key, evidence)
                missing_fields = [
                    key
                    for key in ("tag", "context", "unit", "period", "decimals", "scale")
                    if not _has_value(fields.get(key))
                ]
                if missing_fields:
                    issues.append(
                        _evidence_value_issue(
                            fact_ref=fact_ref,
                            rule="xbrl.fields.present",
                            reason=f"xbrl evidence missing fields: {', '.join(missing_fields)}",
                            item=item,
                            period_key=period_key,
                            evidence=evidence,
                        )
                    )
                evidence_period = _period_token(fields.get("period"))
                fact_period = _period_token(fields.get("fact_period"))
                if evidence_period and fact_period and evidence_period != fact_period:
                    issues.append(
                        _evidence_value_issue(
                            fact_ref=fact_ref,
                            rule="xbrl.period.consistent",
                            reason="xbrl evidence period does not match fact period",
                            item=item,
                            period_key=period_key,
                            evidence=evidence,
                        )
                    )

            quote_text = value_fields["quote_text"]
            if _has_value(quote_text):
                checked = True
                quote_checked_count += 1
                quote_match = _value_matches_expected(
                    normalized_value,
                    quote_text,
                    scale=scale,
                    sign=value_fields["sign"] if _is_xbrl_evidence(evidence) else None,
                    polarity=polarity,
                )
                if quote_match is not True:
                    issues.append(
                        _evidence_value_issue(
                            fact_ref=fact_ref,
                            rule="quote.value.explainable",
                            reason="quote evidence does not contain a value explainable from the fact",
                            item=item,
                            period_key=period_key,
                            evidence=evidence,
                        )
                    )

            if checked:
                checked_fact_count += 1
                if len(issues) == fact_issue_count:
                    verified_fact_count += 1
                else:
                    if _is_pdf_evidence(evidence):
                        pdf_failed_count += 1
                    if _is_xbrl_evidence(evidence):
                        xbrl_failed_count += 1
                    if _has_value(quote_text):
                        quote_failed_count += 1

    failed_fact_count = checked_fact_count - verified_fact_count
    return {
        "schema_version": "siq_evidence_value_verification_v1",
        "polarity_contract_version": FINANCIAL_VALUE_POLARITY_CONTRACT_VERSION,
        "fact_count": fact_count,
        "checked_fact_count": checked_fact_count,
        "verified_fact_count": verified_fact_count,
        "failed_fact_count": failed_fact_count,
        "issue_count": len(issues),
        "issues": issues,
        "pdf_checked_count": pdf_checked_count,
        "pdf_failed_count": pdf_failed_count,
        "xbrl_checked_count": xbrl_checked_count,
        "xbrl_failed_count": xbrl_failed_count,
        "quote_checked_count": quote_checked_count,
        "quote_failed_count": quote_failed_count,
        "value_verification_ratio": round(verified_fact_count / checked_fact_count, 6) if checked_fact_count else None,
    }
