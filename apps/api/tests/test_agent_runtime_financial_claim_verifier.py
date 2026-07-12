from __future__ import annotations

import json

import pytest
from services.agent_runtime_financial_claim_verifier import (
    validate_calculation_traces,
    verify_financial_claims,
)


def _reference(
    *,
    canonical_name: str,
    metric_name: str,
    value: str,
    unit: str,
    currency: str,
    evidence_id: str,
    quote: str,
) -> str:
    return (
        "[P1] source_type=wiki_metrics company_id=HK:01398 filing_id=2025-annual "
        f"canonical_name={canonical_name} metric_name={metric_name} period_key=2025 "
        f'value={value} unit="{unit}" currency={currency} '
        f'evidence_id={evidence_id} quote="{quote}"'
    )


def _identity_reference(*, company_id: str = "HK:01398", filing_id: str = "HK:01398:2025", parse_run_id: str = "run-hk-2025") -> str:
    return (
        f"[P1] source_type=wiki_metrics market=HK company_id={company_id} filing_id={filing_id} "
        f"parse_run_id={parse_run_id} canonical_name=operating_revenue metric_name=营业收入 "
        'period_key=2025 value=8382.70 unit="亿元" currency=CNY '
        'evidence_id=EVID-REV-2025 quote="营业收入 838,270"'
    )


@pytest.mark.parametrize(
    ("overrides", "reason"),
    (
        ({"company_id": "HK:WRONG"}, "company_id_mismatch"),
        ({"filing_id": "HK:01398:2024"}, "filing_id_mismatch"),
        ({"parse_run_id": "run-wrong"}, "parse_run_id_mismatch"),
    ),
)
def test_verifier_rejects_equal_value_evidence_from_wrong_research_identity(overrides, reason):
    identity = {
        "market": "HK",
        "company_id": "HK:01398",
        "filing_id": "HK:01398:2025",
        "parse_run_id": "run-hk-2025",
    }
    reference = _identity_reference(**overrides)

    result = verify_financial_claims(
        f"工商银行 2025 年营业收入为 8,382.70 亿元。\n{reference}",
        expected_identity=identity,
    )

    assert result.checked is True
    assert result.allowed is False
    assert result.violations[0].reason == reason
    assert result.violations[0].expected_company_id == "HK:01398"


def test_verifier_allows_equal_value_evidence_with_exact_research_identity():
    identity = {
        "market": "HK",
        "company_id": "HK:01398",
        "filing_id": "HK:01398:2025",
        "parse_run_id": "run-hk-2025",
    }

    result = verify_financial_claims(
        f"工商银行 2025 年营业收入为 8,382.70 亿元。\n{_identity_reference()}",
        expected_identity=identity,
    )

    assert result.checked is True
    assert result.allowed is True


def test_verifier_checks_identity_even_when_source_row_cannot_form_numeric_fact():
    identity = {
        "market": "US",
        "company_id": "US:0000320193",
        "filing_id": "US:0000320193:2025",
        "parse_run_id": "run-us-2025",
    }
    reply = (
        "Apple 2025 revenue was USD 391 billion.\n"
        "[P1] source_type=wiki_report_table market=US company_id=US:WRONG "
        "filing_id=US:0000320193:2025 parse_run_id=run-us-2025 evidence_id=EVID-US-2025"
    )

    result = verify_financial_claims(reply, expected_identity=identity)

    assert result.checked is True
    assert result.allowed is False
    assert result.facts == ()
    assert result.violations[0].reason == "company_id_mismatch"


def test_verifier_associates_multiple_bank_metrics_with_their_nearest_claims():
    reply = "\n".join(
        (
            "工商银行 2025 年营业收入为 8,382.70 亿元，利息净收入为 6,351.26 亿元。",
            _reference(
                canonical_name="operating_revenue",
                metric_name="营业收入",
                value="8382.70",
                unit="亿元",
                currency="CNY",
                evidence_id="EVID-REV-2025",
                quote="营业收入 838,270",
            ),
            _reference(
                canonical_name="bank_net_interest_income",
                metric_name="利息净收入",
                value="6351.26",
                unit="亿元",
                currency="CNY",
                evidence_id="EVID-NII-2025",
                quote="利息净收入 635,126",
            ),
        )
    )

    result = verify_financial_claims(reply)

    assert result.checked is True
    assert result.allowed is True
    assert [(claim.metric, claim.value) for claim in result.claims] == [
        ("operating_revenue", 8382.70),
        ("bank_net_interest_income", 6351.26),
    ]


def test_verifier_reports_each_swapped_bank_metric_against_the_right_fact():
    reply = "\n".join(
        (
            "工商银行 2025 年营业收入为 6,351.26 亿元且利息净收入为 8,382.70 亿元。",
            _reference(
                canonical_name="operating_revenue",
                metric_name="营业收入",
                value="8382.70",
                unit="亿元",
                currency="CNY",
                evidence_id="EVID-REV-2025",
                quote="营业收入 838,270",
            ),
            _reference(
                canonical_name="bank_net_interest_income",
                metric_name="利息净收入",
                value="6351.26",
                unit="亿元",
                currency="CNY",
                evidence_id="EVID-NII-2025",
                quote="利息净收入 635,126",
            ),
        )
    )

    result = verify_financial_claims(reply)

    assert result.allowed is False
    assert [(violation.metric, violation.reason) for violation in result.violations] == [
        ("operating_revenue", "value_mismatch"),
        ("bank_net_interest_income", "value_mismatch"),
    ]


def test_verifier_maps_chinese_respectively_claims_by_metric_order():
    references = (
        _reference(
            canonical_name="operating_revenue",
            metric_name="营业收入",
            value="8382.70",
            unit="亿元",
            currency="CNY",
            evidence_id="EVID-REV-2025",
            quote="营业收入 838,270",
        ),
        _reference(
            canonical_name="bank_net_interest_income",
            metric_name="利息净收入",
            value="6351.26",
            unit="亿元",
            currency="CNY",
            evidence_id="EVID-NII-2025",
            quote="利息净收入 635,126",
        ),
    )
    allowed = verify_financial_claims(
        "\n".join(("工商银行 2025 年营业收入和利息净收入分别为 8,382.70 亿元和 6,351.26 亿元。", *references))
    )
    blocked = verify_financial_claims(
        "\n".join(("工商银行 2025 年营业收入和利息净收入分别为 6,351.26 亿元和 8,382.70 亿元。", *references))
    )

    assert allowed.allowed is True
    assert [(claim.metric, claim.value) for claim in allowed.claims] == [
        ("operating_revenue", 8382.70),
        ("bank_net_interest_income", 6351.26),
    ]
    assert [(violation.metric, violation.reason) for violation in blocked.violations] == [
        ("operating_revenue", "value_mismatch"),
        ("bank_net_interest_income", "value_mismatch"),
    ]


def test_verifier_keeps_curated_short_chinese_metric_aliases():
    reply = "\n".join(
        (
            "工商银行 2025 年营收为 6,351.26 亿元。",
            _reference(
                canonical_name="operating_revenue",
                metric_name="营业收入",
                value="8382.70",
                unit="亿元",
                currency="CNY",
                evidence_id="EVID-REV-2025",
                quote="营业收入 838,270",
            ),
        )
    )

    result = verify_financial_claims(reply)

    assert result.checked is True
    assert result.allowed is False
    assert result.violations[0].reason == "value_mismatch"


@pytest.mark.parametrize(
    ("claim", "value", "unit", "currency"),
    (
        ("Revenue was HKD 751,766 million in 2025.", "751766", "HKD million", "HKD"),
        ("UK service revenue was GBP 6,200 million in 2025.", "6200", "GBP million", "GBP"),
        ("Net sales were USD 416.161 billion in 2025.", "416161000000", "iso4217:USD", "USD"),
        ("2025 年売上高は JPY 1,234 million。", "1234", "JPY million", "JPY"),
        ("2025 年 매출액은 KRW 9,876 million。", "9876", "KRW million", "KRW"),
    ),
)
def test_verifier_normalizes_multi_market_currency_units(claim: str, value: str, unit: str, currency: str):
    reply = "\n".join(
        (
            claim,
            _reference(
                canonical_name="revenue",
                metric_name="revenue",
                value=value,
                unit=unit,
                currency=currency,
                evidence_id=f"EVID-{currency}-REV-2025",
                quote=f"revenue {value}",
            ),
        )
    )

    result = verify_financial_claims(reply)

    assert result.checked is True
    assert result.allowed is True
    assert result.claims[0].currency == currency


@pytest.mark.parametrize(
    ("claim", "canonical_name", "metric_name", "value", "unit", "currency", "category"),
    (
        ("公司 2025 年毛利率为 45.2%。", "gross_margin", "毛利率", "45.2", "%", "", "percent"),
        (
            "公司 2025 年基本每股收益为人民币 1.23 元/股。",
            "basic_earnings_per_share",
            "基本每股收益",
            "1.23",
            "RMB/share",
            "CNY",
            "per_share",
        ),
    ),
)
def test_verifier_supports_ratio_and_per_share_claims(
    claim: str,
    canonical_name: str,
    metric_name: str,
    value: str,
    unit: str,
    currency: str,
    category: str,
):
    reply = "\n".join(
        (
            claim,
            _reference(
                canonical_name=canonical_name,
                metric_name=metric_name,
                value=value,
                unit=unit,
                currency=currency,
                evidence_id=f"EVID-{canonical_name}-2025",
                quote=f"{metric_name} {value}",
            ),
        )
    )

    result = verify_financial_claims(reply)

    assert result.checked is True
    assert result.allowed is True
    assert result.claims[0].value_category == category


def _structured_trace(operation: str, inputs: dict, result: dict, *, metric: str, period: str = "2025") -> str:
    return json.dumps(
        {
            "schema_version": "siq_financial_calculation_trace_v1",
            "tool": "financial_calculator.py",
            "operation": operation,
            "metric": metric,
            "period": period,
            "inputs": inputs,
            "result": result,
            "research_identity": {
                "market": "HK",
                "company_id": "HK:00700",
                "filing_id": "HK:00700:2025-annual",
                "parse_run_id": "run-hk-00700",
            },
        }
    )


def _trace_reference(evidence_id: str, metric: str, period: str, value: str, unit: str = "HKD million") -> str:
    return (
        f"[D] source_type=wiki_metrics market=HK company_id=HK:00700 filing_id=HK:00700:2025-annual "
        f"parse_run_id=run-hk-00700 canonical_name={metric} metric_name={metric} period_key={period} "
        f'value={value} unit="{unit}" evidence_id={evidence_id} quote="{metric} {value}"'
    )


@pytest.mark.parametrize(
    ("operation", "inputs", "result", "metric", "references"),
    (
        (
            "ratio",
            {
                "numerator": {"metric": "gross_profit", "period": "2025", "value": "40", "unit": "HKD million", "evidence_id": "E-GP"},
                "denominator": {"metric": "revenue", "period": "2025", "value": "100", "unit": "HKD million", "evidence_id": "E-REV"},
            },
            {"ratio": "0.4", "percent": "40"},
            "gross_margin",
            (_trace_reference("E-GP", "gross_profit", "2025", "40"), _trace_reference("E-REV", "revenue", "2025", "100")),
        ),
        (
            "cagr",
            {
                "start": {"metric": "revenue", "period": "2022", "value": "100", "unit": "HKD million", "evidence_id": "E-START"},
                "end": {"metric": "revenue", "period": "2025", "value": "133.1", "unit": "HKD million", "evidence_id": "E-END"},
                "periods": {"role": "period_count", "value": "3"},
            },
            {"rate": "0.1", "percent": "10"},
            "revenue_cagr",
            (_trace_reference("E-START", "revenue", "2022", "100"), _trace_reference("E-END", "revenue", "2025", "133.1")),
        ),
    ),
)
def test_structured_calculation_trace_deterministically_recomputes_operations(
    operation: str,
    inputs: dict,
    result: dict,
    metric: str,
    references: tuple[str, ...],
):
    trace = _structured_trace(operation, inputs, result, metric=metric)
    reply = "\n".join((f"```json\n{trace}\n```", *references))

    validation = validate_calculation_traces(
        reply,
        expected_identity={
            "market": "HK",
            "company_id": "HK:00700",
            "filing_id": "HK:00700:2025-annual",
            "parse_run_id": "run-hk-00700",
        },
        require_calculator=True,
    )

    assert validation.allowed is True
