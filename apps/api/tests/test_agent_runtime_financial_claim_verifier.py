from __future__ import annotations

import json

import pytest
from services.agent_runtime_financial_claim_verifier import (
    NUMBER_WITH_UNIT_RE,
    _amount_match_number,
    _extract_source_fields,
    _percent_claim_details,
    _period_tokens,
    _trace_decimal,
    _trusted_evidence_value,
    _trusted_trace_input,
    validate_calculation_traces,
    verify_financial_claims,
)


def test_source_field_parser_ignores_xbrl_assignments_inside_quote():
    fields = _extract_source_fields(
        "[D1] source_type=wiki_metrics, scale=1, "
        'quote="<ix:nonFraction id=\'f-78\' scale=\'6\'>416,161</ix:nonFraction>", '
        "source_url=https://www.sec.gov/example.htm, source_anchor=f-78"
    )

    assert fields["scale"] == "1"
    assert "scale='6'" in fields["quote"]
    assert fields["source_anchor"] == "f-78"


@pytest.mark.parametrize("value", (0, 0.0))
def test_trace_decimal_preserves_numeric_zero(value):
    assert _trace_decimal(value) == 0


@pytest.mark.parametrize("reference", ({"value": 0}, {"raw_value": 0}))
def test_trusted_evidence_numeric_zero_survives_trace_materialization(reference):
    evidence = {
        **reference,
        "canonical_name": "goodwill_impairment",
        "unit": "元",
        "evidence_id": "zero-evidence",
    }

    assert _trusted_evidence_value(evidence) == 0
    assert _trusted_trace_input(evidence, "amount")["value"] == "0"


@pytest.mark.parametrize("minus_sign", ("-", "‐", "‑", "‒", "−", "﹣", "－"))
def test_amount_range_endpoint_is_not_treated_as_negative(minus_sign):
    line = f"预计金额区间为 2亿元{minus_sign}5亿元"
    matches = list(NUMBER_WITH_UNIT_RE.finditer(line))

    values = [
        _amount_match_number(line, match, matches[index - 1] if index else None)
        for index, match in enumerate(matches)
    ]

    assert values == [2.0, 5.0]


@pytest.mark.parametrize("minus_sign", ("-", "‐", "‑", "‒", "−", "﹣", "－"))
def test_amount_range_with_negative_lower_bound_keeps_only_lower_sign(minus_sign):
    line = f"预计金额区间为 {minus_sign}2亿元{minus_sign}5亿元"
    matches = list(NUMBER_WITH_UNIT_RE.finditer(line))

    values = [
        _amount_match_number(line, match, matches[index - 1] if index else None)
        for index, match in enumerate(matches)
    ]

    assert values == [-2.0, 5.0]


@pytest.mark.parametrize("minus_sign", ("-", "‐", "‑", "‒", "−", "﹣", "－"))
def test_amount_subtraction_formula_keeps_negative_operand(minus_sign):
    line = f"2亿元{minus_sign}5亿元 = -3亿元"
    matches = list(NUMBER_WITH_UNIT_RE.finditer(line))

    values = [
        _amount_match_number(line, match, matches[index - 1] if index else None)
        for index, match in enumerate(matches)
    ]

    assert values == [2.0, -5.0, -3.0]



@pytest.mark.parametrize("minus_sign", ("-", "‐", "‑", "‒", "−", "﹣", "－"))
def test_adjacent_negative_amounts_are_not_treated_as_range_endpoints(minus_sign):
    line = f"项目 A、项目 B 分别为 {minus_sign}2亿元 {minus_sign}5亿元"
    matches = list(NUMBER_WITH_UNIT_RE.finditer(line))

    values = [
        _amount_match_number(line, match, matches[index - 1] if index else None)
        for index, match in enumerate(matches)
    ]

    assert values == [-2.0, -5.0]


@pytest.mark.parametrize("minus_sign", ("-", "‐", "‑", "‒", "−", "﹣", "－"))
def test_adjacent_mixed_sign_amounts_are_not_treated_as_range_endpoints(minus_sign):
    line = f"项目 A、项目 B 分别为 2亿元 {minus_sign}5亿元"
    matches = list(NUMBER_WITH_UNIT_RE.finditer(line))

    values = [
        _amount_match_number(line, match, matches[index - 1] if index else None)
        for index, match in enumerate(matches)
    ]

    assert values == [2.0, -5.0]


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


def _identity_reference(
    *, company_id: str = "HK:01398", filing_id: str = "HK:01398:2025", parse_run_id: str = "run-hk-2025"
) -> str:
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


def test_verifier_prefers_complete_identity_for_same_source_locator():
    identity = {
        "market": "KR",
        "company_id": "KR:005930",
        "filing_id": "KR:005930:2025-annual",
        "parse_run_id": "run-kr-2025",
    }
    incomplete = (
        "[1] source_type=wiki_metrics canonical_name=total_assets metric_name=total_assets "
        'period_key=2025-12-31 value=566942110 unit="KRW million" currency=KRW '
        "task_id=11111111-1111-1111-1111-111111111111 pdf_page=83 table_index=67 "
        'evidence_id=EVID-INCOMPLETE quote="total assets 566,942,110"'
    )
    complete = (
        "[D1] source_type=wiki_metrics market=KR company_id=KR:005930 "
        "filing_id=KR:005930:2025-annual parse_run_id=run-kr-2025 "
        "canonical_name=total_assets metric_name=total_assets period_key=2025-12-31 "
        'value=566942110 unit="KRW million" currency=KRW '
        "task_id=11111111-1111-1111-1111-111111111111 pdf_page=83 table_index=67 "
        'evidence_id=EVID-COMPLETE quote="total assets 566,942,110"'
    )

    result = verify_financial_claims(
        f"Total assets were KRW 566,942,110 million in 2025.\n{incomplete}\n{complete}",
        expected_identity=identity,
    )

    assert result.checked is True
    assert result.allowed is True
    assert len(result.facts) == 1
    assert result.facts[0].company_id == "KR:005930"


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


@pytest.mark.parametrize("unit", ("人民币百万元", "人民幣 百萬元"))
def test_verifier_checks_simplified_and_traditional_rmb_million_units(unit: str):
    reference = _reference(
        canonical_name="operating_revenue",
        metric_name="营业收入",
        value="131442",
        unit=unit,
        currency="CNY",
        evidence_id=f"EVID-{unit}-REV-2025",
        quote="营业收入 131,442",
    )

    allowed = verify_financial_claims(f"公司 2025 年营业收入为 131,442 {unit}。\n{reference}")
    blocked = verify_financial_claims(f"公司 2025 年营业收入为 144,586 {unit}。\n{reference}")

    assert allowed.allowed is True
    assert allowed.claims[0].value == 131442.0
    assert blocked.allowed is False
    assert blocked.violations[0].reason == "value_mismatch"


def test_verifier_treats_ascii_parentheses_as_accounting_negatives():
    reference = _reference(
        canonical_name="total_liabilities",
        metric_name="Total liabilities",
        value="(196794886)",
        unit="RMB thousand",
        currency="CNY",
        evidence_id="EVID-HK-LIABILITIES-2025",
        quote="Total liabilities (196,794,886)",
    )

    allowed = verify_financial_claims(
        f"Total liabilities were (196,794,886) RMB thousand in 2025.\n{reference}"
    )
    blocked = verify_financial_claims(
        f"Total liabilities were (216,474,375) RMB thousand in 2025.\n{reference}"
    )

    assert allowed.allowed is True
    assert allowed.claims[0].value == -196794886.0
    assert allowed.facts[0].value == -196794886.0
    assert blocked.allowed is False
    assert blocked.violations[0].reason == "value_mismatch"


def test_verifier_rejects_stale_currency_field_conflicting_with_rmb_unit():
    reply = "\n".join(
        (
            "Revenue was RMB 751,766 million in 2025.",
            _reference(
                canonical_name="revenue",
                metric_name="revenue",
                value="751766",
                unit="RMB million",
                currency="HKD",
                evidence_id="EVID-HK-REV-2025",
                quote="Revenue 751,766",
            ),
        )
    )

    result = verify_financial_claims(reply)

    assert result.checked is True
    assert result.allowed is False
    assert result.violations[0].reason == "currency_mismatch"
    assert result.violations[0].claimed_currency == "CNY"
    assert result.violations[0].evidence_currency == "HKD"


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
                "numerator": {
                    "metric": "gross_profit",
                    "period": "2025",
                    "value": "40",
                    "unit": "HKD million",
                    "evidence_id": "E-GP",
                },
                "denominator": {
                    "metric": "revenue",
                    "period": "2025",
                    "value": "100",
                    "unit": "HKD million",
                    "evidence_id": "E-REV",
                },
            },
            {"ratio": "0.4", "percent": "40"},
            "gross_margin",
            (
                _trace_reference("E-GP", "gross_profit", "2025", "40"),
                _trace_reference("E-REV", "revenue", "2025", "100"),
            ),
        ),
        (
            "cagr",
            {
                "start": {
                    "metric": "revenue",
                    "period": "2022",
                    "value": "100",
                    "unit": "HKD million",
                    "evidence_id": "E-START",
                },
                "end": {
                    "metric": "revenue",
                    "period": "2025",
                    "value": "133.1",
                    "unit": "HKD million",
                    "evidence_id": "E-END",
                },
                "periods": {"role": "period_count", "value": "3"},
            },
            {"rate": "0.1", "percent": "10"},
            "revenue_cagr",
            (
                _trace_reference("E-START", "revenue", "2022", "100"),
                _trace_reference("E-END", "revenue", "2025", "133.1"),
            ),
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


def test_expected_operations_are_minimum_coverage_not_an_allowlist():
    yoy_trace = _structured_trace(
        "yoy",
        {
            "current": {
                "metric": "revenue",
                "period": "2025",
                "value": "120",
                "unit": "HKD million",
                "evidence_id": "E-REV-2025",
            },
            "previous": {
                "metric": "revenue",
                "period": "2024",
                "value": "100",
                "unit": "HKD million",
                "evidence_id": "E-REV-2024",
            },
        },
        {"rate": "0.2", "percent": "20"},
        metric="revenue_yoy",
    )
    ratio_trace = _structured_trace(
        "ratio",
        {
            "numerator": {
                "metric": "gross_profit",
                "period": "2025",
                "value": "40",
                "unit": "HKD million",
                "evidence_id": "E-GP-2025",
            },
            "denominator": {
                "metric": "revenue",
                "period": "2025",
                "value": "100",
                "unit": "HKD million",
                "evidence_id": "E-REV-RATIO-2025",
            },
        },
        {"ratio": "0.4", "percent": "40"},
        metric="gross_margin",
    )
    references = (
        _trace_reference("E-REV-2025", "revenue", "2025", "120"),
        _trace_reference("E-REV-2024", "revenue", "2024", "100"),
        _trace_reference("E-GP-2025", "gross_profit", "2025", "40"),
        _trace_reference("E-REV-RATIO-2025", "revenue", "2025", "100"),
    )
    reply = "\n".join(
        (
            "营业收入同比增长 20%，毛利率为 40%。",
            f"```json\n{yoy_trace}\n{ratio_trace}\n```",
            *references,
        )
    )

    validation = validate_calculation_traces(
        reply,
        expected_identity={
            "market": "HK",
            "company_id": "HK:00700",
            "filing_id": "HK:00700:2025-annual",
            "parse_run_id": "run-hk-00700",
        },
        require_calculator=True,
        expected_operations=frozenset({"yoy"}),
    )

    assert validation.allowed is True
    assert {run["operation"] for run in validation.runs} == {"yoy", "ratio"}


def test_trusted_evidence_set_rejects_model_authored_replacement_evidence():
    trace = _structured_trace(
        "normalize_amount",
        {
            "amount": {
                "metric": "revenue",
                "period": "2025",
                "value": "999",
                "unit": "HKD million",
                "evidence_id": "E-FAKE",
            }
        },
        {"native_base_value": "999000000", "native_100m_value": "9.99"},
        metric="revenue",
    )
    trusted = _trusted_yoy_fact("revenue", "revenue", "2025", "100", "E-REAL")
    reply = "营收约 9.99 亿元。\n" + f"```json\n{trace}\n```\n" + _trace_reference(
        "E-FAKE", "revenue", "2025", "999"
    )

    validation = validate_calculation_traces(
        reply,
        expected_identity={
            "market": "HK",
            "company_id": "HK:00700",
            "filing_id": "HK:00700:2025-annual",
            "parse_run_id": "run-hk-00700",
        },
        require_calculator=True,
        expected_operations=frozenset({"normalize_amount"}),
        trusted_evidence=(trusted,),
    )

    assert validation.allowed is False
    assert validation.reason == "trace_input_evidence_missing"


def test_trusted_evidence_rejects_trace_currency_changed_by_model():
    trusted = _trusted_yoy_fact("revenue", "revenue", "2025", "100", "E-REAL")
    trace = _structured_trace(
        "normalize_amount",
        {
            "amount": {
                "metric": "revenue",
                "period": "2025",
                "value": "100",
                "unit": "HKD million",
                "evidence_id": "E-REAL",
            }
        },
        {"native_base_value": "100000000", "native_100m_value": "1"},
        metric="revenue",
    )
    source = "[D1] source_type=wiki_metrics task_id=task-yoy-binding pdf_page=8 table_index=4"

    validation = validate_calculation_traces(
        f"营收为 1 亿港元。\n```json\n{trace}\n```\n{source}",
        expected_identity={
            "market": "HK",
            "company_id": "HK:00700",
            "filing_id": "HK:00700:2025-annual",
            "parse_run_id": "run-hk-00700",
        },
        require_calculator=True,
        expected_operations=frozenset({"normalize_amount"}),
        trusted_evidence=(trusted,),
    )

    assert validation.allowed is False
    assert validation.reason == "trace_input_currency_mismatch"


@pytest.mark.parametrize(
    ("input_period", "expected_allowed", "expected_reason"),
    (
        ("2025-12-31", True, ""),
        ("2025", True, ""),
        ("2025-03-31", False, "trace_input_period_mismatch"),
        ("2025Q1", False, "trace_input_period_mismatch"),
        ("2025Q4", False, "trace_input_period_mismatch"),
    ),
)
def test_trace_input_period_uses_most_specific_available_granularity(
    input_period: str,
    expected_allowed: bool,
    expected_reason: str,
):
    trusted = _trusted_yoy_fact("revenue", "营业收入", "2025-12-31", "100", "E-REV-ANNUAL")
    trace = _structured_trace(
        "normalize_amount",
        {
            "amount": {
                "metric": "revenue",
                "period": input_period,
                "value": "100",
                "unit": "RMB million",
                "evidence_id": "E-REV-ANNUAL",
            }
        },
        {"native_base_value": "100000000", "native_100m_value": "1"},
        metric="revenue",
        period=input_period,
    )
    source = "[D1] source_type=wiki_metrics task_id=task-yoy-binding pdf_page=8 table_index=4"

    validation = validate_calculation_traces(
        f"营业收入为 1 亿元。\n```json\n{trace}\n```\n{source}",
        expected_identity={
            "market": "HK",
            "company_id": "HK:00700",
            "filing_id": "HK:00700:2025-annual",
            "parse_run_id": "run-hk-00700",
        },
        require_calculator=True,
        expected_operations=frozenset({"normalize_amount"}),
        trusted_evidence=(trusted,),
    )

    assert validation.allowed is expected_allowed
    assert validation.reason == expected_reason


def test_model_cannot_spoof_backend_trace_origin_for_unrelated_claim():
    evidence = (
        _trusted_yoy_fact("revenue", "营业收入", "2024", "100", "rev-2024"),
        _trusted_yoy_fact("revenue", "营业收入", "2025", "120", "rev-2025"),
    )
    trace = json.loads(
        _structured_trace(
            "yoy",
            {
                "current": {
                    "metric": "revenue",
                    "period": "2025",
                    "value": "120",
                    "unit": "RMB million",
                    "evidence_id": "rev-2025",
                },
                "previous": {
                    "metric": "revenue",
                    "period": "2024",
                    "value": "100",
                    "unit": "RMB million",
                    "evidence_id": "rev-2024",
                },
            },
            {"rate": "0.2", "percent": "20"},
            metric="revenue_yoy",
        )
    )
    trace.update(
        {
            "trace_origin": "backend_evidence_recompute",
            "display_line_number": 2,
            "display_match_start": len("毛利率为 "),
        }
    )
    source = "[D1] source_type=wiki_metrics task_id=task-yoy-binding pdf_page=8 table_index=4"
    reply = (
        "营业收入同比增长 20%。\n"
        "毛利率为 20%。\n"
        f"```json\n{json.dumps(trace)}\n```\n"
        f"{source}"
    )

    validation = validate_calculation_traces(
        reply,
        expected_identity={
            "market": "HK",
            "company_id": "HK:00700",
            "filing_id": "HK:00700:2025-annual",
            "parse_run_id": "run-hk-00700",
        },
        require_calculator=True,
        expected_operations=frozenset({"yoy"}),
        trusted_evidence=evidence,
    )

    assert validation.allowed is False
    assert validation.reason == "trace_claim_result_mismatch"
    assert validation.runs[0]["trace_origin"] == "reply_structured"


def _trusted_yoy_fact(metric: str, metric_name: str, period: str, value: str, evidence_id: str) -> dict:
    return {
        "source_type": "trusted_wiki_table_cell",
        "metric": metric,
        "canonical_name": metric,
        "metric_name": metric_name,
        "aliases": [metric_name],
        "period": period,
        "period_key": period,
        "value": value,
        "raw_value": value,
        "unit": "RMB million",
        "evidence_id": evidence_id,
        "quote": f"{metric_name} {value}",
        "task_id": "task-yoy-binding",
        "pdf_page": 8,
        "table_index": 4,
        "market": "HK",
        "company_id": "HK:00700",
        "filing_id": "HK:00700:2025-annual",
        "parse_run_id": "run-hk-00700",
    }


def test_evidence_recompute_binds_each_yoy_percentage_to_nearest_metric():
    identity = {
        "market": "HK",
        "company_id": "HK:00700",
        "filing_id": "HK:00700:2025-annual",
        "parse_run_id": "run-hk-00700",
    }
    evidence = (
        _trusted_yoy_fact("revenue", "营业收入", "2024", "100", "rev-2024"),
        _trusted_yoy_fact("revenue", "营业收入", "2025", "110", "rev-2025"),
        _trusted_yoy_fact("net_profit", "净利润", "2024", "100", "profit-2024"),
        _trusted_yoy_fact("net_profit", "净利润", "2025", "120", "profit-2025"),
    )
    source = "[D1] source_type=wiki_metrics task_id=task-yoy-binding pdf_page=8 table_index=4"
    correct = f"营业收入 2025 年同比增长 10%，净利润 2025 年同比增长 20%。\n{source}"
    swapped = f"营业收入 2025 年同比增长 20%，净利润 2025 年同比增长 10%。\n{source}"

    allowed = validate_calculation_traces(
        correct,
        expected_identity=identity,
        require_calculator=True,
        expected_operations=frozenset({"yoy", "yoy_growth"}),
        trusted_evidence=evidence,
    )
    blocked = validate_calculation_traces(
        swapped,
        expected_identity=identity,
        require_calculator=True,
        expected_operations=frozenset({"yoy", "yoy_growth"}),
        trusted_evidence=evidence,
    )

    assert allowed.allowed is True
    assert blocked.allowed is False
    assert blocked.reason == "trace_unstructured"


@pytest.mark.parametrize(
    "reply_line",
    (
        "营业收入 2025 年同比增长 10%，净利润 2025 年同比增长 10%。",
        "营业收入 2025 年同比增长 20%，净利润 2025 年同比增长 20%。",
    ),
)
def test_evidence_recompute_does_not_reuse_one_percentage_for_another_occurrence(reply_line: str):
    identity = {
        "market": "HK",
        "company_id": "HK:00700",
        "filing_id": "HK:00700:2025-annual",
        "parse_run_id": "run-hk-00700",
    }
    evidence = (
        _trusted_yoy_fact("revenue", "营业收入", "2024", "100", "rev-2024"),
        _trusted_yoy_fact("revenue", "营业收入", "2025", "110", "rev-2025"),
        _trusted_yoy_fact("net_profit", "净利润", "2024", "100", "profit-2024"),
        _trusted_yoy_fact("net_profit", "净利润", "2025", "120", "profit-2025"),
    )
    source = "[D1] source_type=wiki_metrics task_id=task-yoy-binding pdf_page=8 table_index=4"

    result = validate_calculation_traces(
        f"{reply_line}\n{source}",
        expected_identity=identity,
        require_calculator=True,
        expected_operations=frozenset({"yoy", "yoy_growth"}),
        trusted_evidence=evidence,
    )

    assert result.allowed is False
    assert result.reason == "trace_claim_result_mismatch"


def test_evidence_recompute_uses_percentage_business_tolerance_floor_and_boundary():
    identity = {
        "market": "HK",
        "company_id": "HK:00700",
        "filing_id": "HK:00700:2025-annual",
        "parse_run_id": "run-hk-00700",
    }
    within_business_tolerance = (
        _trusted_yoy_fact("revenue", "营业收入", "2024", "100", "rev-2024"),
        _trusted_yoy_fact("revenue", "营业收入", "2025", "110.04", "rev-2025"),
    )
    outside_business_tolerance = (
        _trusted_yoy_fact("revenue", "营业收入", "2024", "100", "rev-2024"),
        _trusted_yoy_fact("revenue", "营业收入", "2025", "110.06", "rev-2025"),
    )
    source = "[D1] source_type=wiki_metrics task_id=task-yoy-binding pdf_page=8 table_index=4"

    rounded = validate_calculation_traces(
        f"营业收入 2025 年同比增长 10.00%。\n{source}",
        expected_identity=identity,
        require_calculator=True,
        expected_operations=frozenset({"yoy"}),
        trusted_evidence=within_business_tolerance,
    )
    outside_boundary = validate_calculation_traces(
        f"营业收入 2025 年同比增长 10.00%。\n{source}",
        expected_identity=identity,
        require_calculator=True,
        expected_operations=frozenset({"yoy"}),
        trusted_evidence=outside_business_tolerance,
    )

    assert rounded.allowed is True
    assert outside_boundary.allowed is False
    assert outside_boundary.reason == "trace_unstructured"


@pytest.mark.parametrize("minus_sign", ("-", "−", "‐", "‑", "‒", "﹣", "－"))
def test_evidence_recompute_preserves_explicit_negative_yoy_for_common_minus_glyphs(minus_sign: str):
    identity = {
        "market": "HK",
        "company_id": "HK:00700",
        "filing_id": "HK:00700:2025-annual",
        "parse_run_id": "run-hk-00700",
    }
    evidence = (
        _trusted_yoy_fact("revenue", "营业收入", "2024", "100", "rev-2024"),
        _trusted_yoy_fact("revenue", "营业收入", "2025", "97", "rev-2025"),
    )
    source = "[D1] source_type=wiki_metrics task_id=task-yoy-binding pdf_page=8 table_index=4"

    result = validate_calculation_traces(
        f"营业收入 2025 年同比 {minus_sign}3.0%。\n{source}",
        expected_identity=identity,
        require_calculator=True,
        expected_operations=frozenset({"yoy"}),
        trusted_evidence=evidence,
    )

    assert result.allowed is True


@pytest.mark.parametrize("minus_sign", ("-", "−", "‐", "‑", "‒", "﹣", "－"))
def test_percent_range_separator_is_not_treated_as_negative_endpoint(minus_sign: str):
    claims = _percent_claim_details(
        f"预测期增长率区间 2%{minus_sign}5%。",
        require_derived_term=True,
    )

    assert tuple((str(value), is_percentage_point) for value, is_percentage_point in claims) == (
        ("0.02", False),
        ("0.05", False),
    )


@pytest.mark.parametrize("minus_sign", ("-", "−", "‐", "‑", "‒", "﹣", "－"))
def test_percent_range_with_negative_lower_bound_keeps_only_lower_sign(minus_sign: str):
    claims = _percent_claim_details(
        f"预测期增长率区间 {minus_sign}2%{minus_sign}5%。",
        require_derived_term=True,
    )

    assert tuple((str(value), is_percentage_point) for value, is_percentage_point in claims) == (
        ("-0.02", False),
        ("0.05", False),
    )


@pytest.mark.parametrize("minus_sign", ("-", "−", "‐", "‑", "‒", "﹣", "－"))
def test_adjacent_mixed_sign_percentages_are_not_treated_as_range_endpoints(minus_sign: str):
    claims = _percent_claim_details(
        f"项目 A、项目 B 增长率分别为 2% {minus_sign}5%。",
        require_derived_term=True,
    )

    assert tuple((str(value), is_percentage_point) for value, is_percentage_point in claims) == (
        ("0.02", False),
        ("-0.05", False),
    )


@pytest.mark.parametrize("minus_sign", ("-", "−", "‐", "‑", "‒", "﹣", "－"))
def test_period_tokens_accept_common_unicode_date_separators(minus_sign: str):
    assert _period_tokens(f"2025{minus_sign}12{minus_sign}31") == ("2025-12-31", "2025")


MIDEA_IDENTITY = {
    "market": "CN",
    "company_id": "000333-美的集团",
    "filing_id": "CN:000333-美的集团:2025-annual",
    "parse_run_id": "task-midea",
}


def _trusted_goodwill_fact(
    metric: str,
    metric_name: str,
    value: str,
    evidence_id: str,
    aliases: tuple[str, ...],
) -> dict:
    return {
        "source_type": "trusted_wiki_table_cell",
        "metric": metric,
        "canonical_name": metric,
        "metric_name": metric_name,
        "aliases": aliases,
        "period": "2025-12-31",
        "period_key": "2025-12-31",
        "value": value,
        "raw_value": value,
        "unit": "人民币千元",
        "evidence_id": evidence_id,
        "quote": f"{metric_name} {value}",
        "task_id": "task-midea",
        "pdf_page": 206,
        "table_index": 163,
        "md_line": 4325,
        "financial_scope": "consolidated",
        **MIDEA_IDENTITY,
    }


def _trusted_period_goodwill_fact(
    metric: str,
    metric_name: str,
    period: str,
    value: str,
    evidence_id: str,
    aliases: tuple[str, ...],
) -> dict:
    item = _trusted_goodwill_fact(metric, metric_name, value, evidence_id, aliases)
    item["period"] = period
    item["period_key"] = period
    return item


def _trusted_goodwill_evidence() -> tuple[dict, ...]:
    return (
        _trusted_goodwill_fact(
            "goodwill_gross",
            "商誉账面原值",
            "34813270",
            "midea-gross-2025",
            ("商誉账面原值", "账面原值", "商誉原值"),
        ),
        _trusted_goodwill_fact(
            "goodwill_impairment_allowance",
            "商誉减值准备",
            "556411",
            "midea-allowance-2025",
            ("商誉减值准备", "减值准备"),
        ),
        _trusted_goodwill_fact(
            "goodwill_net",
            "商誉账面净值",
            "34256859",
            "midea-net-2025",
            ("商誉账面净值", "账面净值", "商誉净值"),
        ),
    )


def _validate_goodwill_reconciliation(line: str, source: str | None = None):
    citation = source or (
        "[D1] source_type=wiki_document_links task_id=task-midea pdf_page=206 table_index=163 md_line=4325"
    )
    return validate_calculation_traces(
        f"{line}\n{citation}",
        expected_identity=MIDEA_IDENTITY,
        require_reconciliation=True,
        trusted_evidence=_trusted_goodwill_evidence(),
    )


def test_evidence_recompute_accepts_strict_rounded_goodwill_reconciliation():
    result = _validate_goodwill_reconciliation("商誉账面原值 348.13 亿元 - 减值准备 5.56 亿元 = 账面净值 342.57 亿元")

    assert result.allowed is True


def test_evidence_recompute_treats_markdown_emphasis_as_formatting_in_reconciliation():
    valid = _validate_goodwill_reconciliation(
        "附注原值 34,813,270 千元 - 减值准备 556,411 千元 = **34,256,859 千元**"
    )
    tampered = _validate_goodwill_reconciliation(
        "附注原值 34,813,270 千元 - 减值准备 556,411 千元 = **99,999,999 千元**"
    )

    assert valid.allowed is True
    assert tampered.allowed is False
    assert tampered.reason == "trace_unstructured"


def test_runtime_reconciliation_receipt_binds_trusted_cells_behind_compact_citations():
    gross, allowance, net = _trusted_goodwill_evidence()
    gross.update({"unit": "元", "task_id": "task-midea", "table_index": 165, "pdf_page": 137})
    allowance.update({"unit": "元", "task_id": "task-midea", "table_index": 166, "pdf_page": 137})
    net.update({"unit": "元", "task_id": "task-midea", "table_index": 84, "pdf_page": 65})
    gross["value"] = gross["raw_value"] = "1282"
    allowance["value"] = allowance["raw_value"] = "99"
    net["value"] = net["raw_value"] = "1183"
    receipt = {
        "operation": "goodwill_reconciliation",
        "status": "pass",
        "result": {
            "note_gross": "1282",
            "impairment_allowance": "99",
            "statement_net": "1183",
        },
        "receipt_source": "hermes_session_tool",
        "receipt_tool_call_id": "call-recon",
    }
    reply = (
        "商誉原值 1,282 元 - 减值准备 99 元 = 净额 1,183 元。\n"
        "[D1] source_type=wiki_metrics evidence_id=midea-net-2025 task_id=task-midea pdf_page=65 table_index=84\n"
        "[D1] source_type=wiki_metrics evidence_id=midea-net-2025 task_id=task-midea pdf_page=65 table_index=84\n"
        "[D2] source_type=wiki_document_links task_id=task-midea pdf_page=137 table_index=165\n"
        "[D3] source_type=wiki_document_links task_id=task-midea pdf_page=137 table_index=166"
    )

    result = validate_calculation_traces(
        reply,
        expected_identity=MIDEA_IDENTITY,
        require_reconciliation=True,
        trusted_runs=(receipt,),
        trusted_evidence=(gross, allowance, net),
    )

    assert result.allowed is True
    assert any(run.get("trace_origin") == "trusted_runtime_receipt" for run in result.runs)


def test_unit_normalization_does_not_misbind_repeated_reconciliation_operands():
    source = "[D1] source_type=wiki_document_links task_id=task-midea pdf_page=206 table_index=163 md_line=4325"
    reply = (
        "商誉账面原值 348.13 亿元，减值准备余额 5.56 亿元，账面净值 342.57 亿元。\n"
        "## 勾稽校验\n"
        "348.13 亿元（原值） - 5.56 亿元（减值准备） = 342.57 亿元（账面价值）。\n"
        f"{source}"
    )

    result = validate_calculation_traces(
        reply,
        expected_identity=MIDEA_IDENTITY,
        require_calculator=True,
        require_reconciliation=True,
        expected_operations=frozenset({"normalize_amount"}),
        trusted_evidence=_trusted_goodwill_evidence(),
    )

    assert result.allowed is True
    assert {run["operation"] for run in result.runs} == {"normalize_amount", "goodwill_reconciliation"}


@pytest.mark.parametrize("minus_sign", ("-", "−", "‐", "‑", "‒", "﹣", "－"))
def test_evidence_recompute_accepts_common_financial_minus_glyphs(minus_sign: str):
    result = _validate_goodwill_reconciliation(
        f"34,813,270（原值）{minus_sign} 556,411（减值准备）= 34,256,859（净额）"
    )

    assert result.allowed is True
    assert result.reason == ""
    assert len(result.runs) == 1
    assert result.runs[0]["operation"] == "goodwill_reconciliation"


@pytest.mark.parametrize("minus_sign", ("-", "−", "‐", "‑", "‒", "﹣", "－"))
def test_evidence_recompute_still_rejects_extra_subtraction_for_all_minus_glyphs(minus_sign: str):
    result = _validate_goodwill_reconciliation(f"34,813,270 {minus_sign} 1 {minus_sign} 556,411 = 34,256,859")

    assert result.allowed is False


def test_evidence_recompute_accepts_formula_clause_before_same_line_status_checks():
    result = _validate_goodwill_reconciliation(
        "- 结果：34,813,270 − 556,411 = 34,256,859 千元；与主表商誉账面价值 34,256,859 千元的差异 = 0；status=pass"
    )

    assert result.allowed is True


def test_evidence_recompute_accepts_trusted_numeric_expression_inside_equality_chain():
    result = _validate_goodwill_reconciliation(
        "- gross − allowance = 34,813,270 − 556,411 = 34,256,859 = 三表商誉净额 ✓"
    )

    assert result.allowed is True
    assert result.reason == ""
    assert len(result.runs) == 1
    assert result.runs[0]["trace_origin"] == "backend_evidence_recompute"


@pytest.mark.parametrize(
    "line",
    (
        "gross − allowance = 34,256,859 = 34,813,270 − 556,411 = 三表商誉净额",
        "gross − allowance = 556,411 − 34,813,270 = 34,256,859 = 三表商誉净额",
        "gross − allowance = 34,813,270 + 556,411 = 34,256,859 = 三表商誉净额",
        "gross − allowance = 999 = 34,813,270 − 556,411 = 34,256,859 = 三表商誉净额",
        "gross − allowance = 34,813,270 − 556,411 = 34,256,859 = 999",
        "gross − allowance = 34,813,270 − 556,411 = 中间结果 = 34,256,859",
        "-34,813,270 - 556,411 = 34,256,859",
    ),
)
def test_evidence_recompute_rejects_untrusted_or_malformed_equality_chain(line: str):
    result = _validate_goodwill_reconciliation(line)

    assert result.allowed is False
    assert result.reason == "trace_unstructured"


def test_evidence_recompute_accepts_adjacent_role_bound_goodwill_table_rows_without_formula():
    result = _validate_goodwill_reconciliation(
        "\n".join(
            (
                "| 商誉原值（未扣减） | 34,813,270 | 30,150,019 |",
                "| 减:减值准备 | (556,411) | (569,005) |",
                "| 账面净值 | 34,256,859 | 29,581,014 |",
            )
        )
    )

    assert result.allowed is True
    assert result.reason == ""
    assert len(result.runs) == 1
    assert result.runs[0]["display_line_number"] == 1


@pytest.mark.parametrize(
    "rows",
    (
        (
            "| 账面原值小计 | 34,813,270 |",
            "| 账面净值 | 34,256,859 |",
        ),
        (
            "| 账面原值小计 | 999 |",
            "| 减:减值准备 | (556,411) |",
            "| 账面净值 | 34,256,859 |",
        ),
        (
            "| 账面原值小计 | 34,813,270 |",
            "| 说明 | 以下为减值数据 |",
            "| 减:减值准备 | (556,411) |",
            "| 账面净值 | 34,256,859 |",
        ),
        (
            "| 账面原值小计 | 34,813,270 |",
            "| 账面净值 | 556,411 |",
            "| 减:减值准备 | 34,256,859 |",
        ),
    ),
)
def test_evidence_recompute_rejects_incomplete_wrong_or_nonadjacent_goodwill_fact_rows(rows: tuple[str, ...]):
    result = _validate_goodwill_reconciliation("\n".join(rows))

    assert result.allowed is False
    assert result.reason == "trace_unstructured"


def test_evidence_recompute_rejects_goodwill_fact_rows_backed_by_mixed_periods():
    gross, allowance, net = _trusted_goodwill_evidence()
    allowance = {**allowance, "period": "2024-12-31", "period_key": "2024-12-31"}
    rows = "\n".join(
        (
            "| 账面原值小计 | 34,813,270 |",
            "| 减:减值准备 | (556,411) |",
            "| 账面净值 | 34,256,859 |",
        )
    )
    source = "[D1] source_type=wiki_document_links task_id=task-midea pdf_page=206 table_index=163 md_line=4325"

    result = validate_calculation_traces(
        f"{rows}\n{source}",
        expected_identity=MIDEA_IDENTITY,
        require_reconciliation=True,
        trusted_evidence=(gross, allowance, net),
    )

    assert result.allowed is False
    assert result.reason == "trace_unstructured"


def test_evidence_recompute_does_not_treat_source_quote_as_visible_goodwill_fact_rows():
    source = (
        "[D1] source_type=wiki_document_links task_id=task-midea pdf_page=206 table_index=163 md_line=4325 "
        'quote="账面原值小计 34,813,270；减值准备 556,411；账面净值 34,256,859"'
    )

    result = _validate_goodwill_reconciliation("仅见附注引用。", source)

    assert result.allowed is False
    assert result.reason == "trace_unstructured"


def _trusted_goodwill_component_evidence() -> tuple[dict, ...]:
    return (
        _trusted_period_goodwill_fact(
            "goodwill_component_kuka",
            "KUKA 集团",
            "2025-12-31",
            "23435302",
            "midea-kuka-2025",
            ("KUKA 集团", "KUKA", "库卡"),
        ),
        _trusted_period_goodwill_fact(
            "goodwill_component_tlsc",
            "TLSC 集团",
            "2025-12-31",
            "2085854",
            "midea-tlsc-2025",
            ("TLSC 集团", "TLSC"),
        ),
        _trusted_period_goodwill_fact(
            "goodwill_component_swan",
            "小天鹅",
            "2025-12-31",
            "1361306",
            "midea-swan-2025",
            ("小天鹅",),
        ),
        _trusted_period_goodwill_fact(
            "goodwill_component_other_i",
            "其他(i)",
            "2025-12-31",
            "7930808",
            "midea-other-2025",
            ("其他(i)", "其他(i)商誉"),
        ),
    )


def _verify_goodwill_component_claims(line: str, evidence: tuple[dict, ...] | None = None):
    source = "[D1] source_type=wiki_document_links task_id=task-midea pdf_page=206 table_index=163 md_line=4325"
    return verify_financial_claims(
        f"{line}\n{source}",
        expected_identity=MIDEA_IDENTITY,
        trusted_evidence=evidence or _trusted_goodwill_component_evidence(),
    )


def test_claim_verifier_binds_each_amount_in_real_multi_component_sentence_to_its_local_alias():
    line = (
        "商誉主要由 KUKA 集团（23,435,302 千元，占 68.4%）、TLSC 集团（2,085,854 千元，6.1%）、"
        "小天鹅（1,361,306 千元，4.0%）以及其他（7,930,808 千元，22.9%）构成。"
    )

    result = _verify_goodwill_component_claims(line)

    assert result.allowed is True
    assert [(claim.metric, claim.value) for claim in result.claims] == [
        ("goodwill_component_kuka", 23435302.0),
        ("goodwill_component_tlsc", 2085854.0),
        ("goodwill_component_swan", 1361306.0),
        ("goodwill_component_other_i", 7930808.0),
    ]


def test_claim_verifier_reports_wrong_footnote_short_alias_value_against_that_metric_not_previous_metric():
    line = "小天鹅（1,361,306 千元，4.0%）以及其他（1,361,306 千元，22.9%）构成。"

    result = _verify_goodwill_component_claims(line)

    assert result.allowed is False
    assert len(result.violations) == 1
    assert result.violations[0].metric == "goodwill_component_other_i"
    assert result.violations[0].evidence_value == 7930808.0


def test_claim_verifier_does_not_guess_when_footnote_stripped_alias_is_ambiguous():
    other_i = _trusted_period_goodwill_fact(
        "goodwill_component_other_i",
        "其他(i)",
        "2025-12-31",
        "7930808",
        "midea-other-i-2025",
        ("其他(i)",),
    )
    other_ii = _trusted_period_goodwill_fact(
        "goodwill_component_other_ii",
        "其他(ii)",
        "2025-12-31",
        "123456",
        "midea-other-ii-2025",
        ("其他(ii)",),
    )

    result = _verify_goodwill_component_claims("其他（7,930,808 千元）", (other_i, other_ii))

    assert result.allowed is True
    assert result.claims == ()


def test_claim_verifier_does_not_bind_a_different_explicit_footnote_to_stripped_alias():
    other_i = (_trusted_goodwill_component_evidence()[3],)

    result = _verify_goodwill_component_claims("其他(ii)（7,930,808 千元）", other_i)

    assert result.allowed is True
    assert result.claims == ()


def test_claim_verifier_does_not_reuse_previous_alias_for_unlabelled_following_amount():
    evidence = _trusted_goodwill_component_evidence()[2:]

    result = _verify_goodwill_component_claims(
        "小天鹅（1,361,306 千元）以及未披露项（7,930,808 千元）",
        evidence,
    )

    assert result.allowed is True
    assert [(claim.metric, claim.value) for claim in result.claims] == [("goodwill_component_swan", 1361306.0)]


def test_claim_verifier_keeps_support_for_parenthetical_alias_after_amount():
    evidence = (_trusted_goodwill_component_evidence()[2],)

    result = _verify_goodwill_component_claims("1,361,306 千元（小天鹅）", evidence)

    assert result.allowed is True
    assert [(claim.metric, claim.value) for claim in result.claims] == [("goodwill_component_swan", 1361306.0)]


@pytest.mark.parametrize((("amount", "allowed")), (("9,671", True), ("96,710", False)))
def test_claim_verifier_checks_parenthesized_impairment_amounts(amount: str, allowed: bool):
    evidence = (
        _trusted_period_goodwill_fact(
            "goodwill_impairment_allowance",
            "商誉减值准备",
            "2025-12-31",
            "9671",
            "byd-allowance-2025",
            ("商誉减值准备", "减值准备"),
        ),
    )

    result = _verify_goodwill_component_claims(f"商誉减值准备为（{amount}）千元。", evidence)

    assert result.allowed is allowed
    assert len(result.claims) == 1
    assert result.claims[0].metric == "goodwill_impairment_allowance"
    assert bool(result.violations) is not allowed


def _trusted_impairment_flow_evidence() -> tuple[dict, ...]:
    allowance = _trusted_period_goodwill_fact(
        "goodwill_impairment_allowance",
        "商誉减值准备",
        "2024-12-31",
        "1351167582.22",
        "halo-allowance-2024",
        ("减值准备", "商誉减值准备"),
    )
    flow = _trusted_period_goodwill_fact(
        "goodwill_impairment_allowance_absolute_change",
        "商誉减值准备变动额",
        "2025-12-31",
        "863685624.45",
        "halo-allowance-change-2025",
        ("商誉减值准备变动", "商誉减值准备绝对变动", "本期净增"),
    )
    return (
        {**allowance, "unit": "元"},
        {**flow, "unit": "元", "change_direction": "increase"},
    )


@pytest.mark.parametrize(
    ("amount", "allowed"),
    (("863,685,624.45", True), ("800,000,000.00", False)),
)
def test_claim_verifier_binds_impairment_recognition_flow_to_change_fact_not_allowance_balance(
    amount: str,
    allowed: bool,
):
    source = "[D1] source_type=wiki_report_fulltext task_id=task-midea pdf_page=206 table_index=163 md_line=4325"

    result = verify_financial_claims(
        f"2025 年商誉减值准备计提 {amount} 元。\n{source}",
        expected_identity=MIDEA_IDENTITY,
        trusted_evidence=_trusted_impairment_flow_evidence(),
    )

    assert result.allowed is allowed
    assert result.claims[0].metric == "goodwill_impairment_allowance_absolute_change"
    if not allowed:
        assert result.violations[0].evidence_value == 863685624.45


def test_evidence_recompute_accepts_attachment_tlsc_rounding_within_business_tolerance():
    evidence = (
        _trusted_period_goodwill_fact(
            "goodwill_component_tlsc",
            "TLSC 集团",
            "2024-12-31",
            "2152719",
            "midea-tlsc-2024",
            ("TLSC", "TLSC 集团"),
        ),
        _trusted_period_goodwill_fact(
            "goodwill_component_tlsc",
            "TLSC 集团",
            "2025-12-31",
            "2085854",
            "midea-tlsc-2025",
            ("TLSC", "TLSC 集团"),
        ),
    )
    source = "[D1] source_type=wiki_document_links task_id=task-midea pdf_page=206 table_index=163 md_line=4325"

    result = validate_calculation_traces(
        f"TLSC 2025 年同比 −3.10%。\n{source}",
        expected_identity=MIDEA_IDENTITY,
        require_calculator=True,
        expected_operations=frozenset({"yoy"}),
        trusted_evidence=evidence,
    )

    assert result.allowed is True


@pytest.mark.parametrize(
    ("metric", "metric_name", "previous", "current", "claim"),
    (
        ("goodwill_net", "商誉净额", "29581014", "34256859", "商誉净额增长 15.8%。"),
        (
            "goodwill_impairment_allowance",
            "商誉减值准备",
            "569005",
            "556411",
            "商誉减值准备下降 2.2%。",
        ),
    ),
)
def test_evidence_recompute_accepts_directional_growth_and_decline_percentages(
    metric: str,
    metric_name: str,
    previous: str,
    current: str,
    claim: str,
):
    evidence = (
        _trusted_period_goodwill_fact(
            metric,
            metric_name,
            "2024-12-31",
            previous,
            f"{metric}-2024",
            (metric_name,),
        ),
        _trusted_period_goodwill_fact(
            metric,
            metric_name,
            "2025-12-31",
            current,
            f"{metric}-2025",
            (metric_name,),
        ),
    )
    source = "[D1] source_type=wiki_document_links task_id=task-midea pdf_page=206 table_index=163 md_line=4325"

    result = validate_calculation_traces(
        f"{claim}\n{source}",
        expected_identity=MIDEA_IDENTITY,
        require_calculator=True,
        expected_operations=frozenset({"yoy"}),
        trusted_evidence=evidence,
    )

    assert result.allowed is True


def test_calculation_trace_ignores_unrelated_metric_terms_in_analysis_prose():
    evidence = (
        _trusted_period_goodwill_fact(
            "goodwill_net",
            "商誉净额",
            "2024-12-31",
            "29581014",
            "goodwill-net-2024",
            ("商誉净额", "商誉账面价值"),
        ),
        _trusted_period_goodwill_fact(
            "goodwill_net",
            "商誉净额",
            "2025-12-31",
            "34256859",
            "goodwill-net-2025",
            ("商誉净额", "商誉账面价值"),
        ),
    )
    source = "[D1] source_type=wiki_document_links task_id=task-midea pdf_page=206 table_index=163 md_line=4325"
    reply = (
        "商誉净额同比增长 15.81%。\n"
        "经营分析显示商誉规模同比仍增长 15.81%。\n"
        "后续需结合预测期、毛利率和折现率判断减值压力。\n"
        f"{source}"
    )

    result = validate_calculation_traces(
        reply,
        expected_identity=MIDEA_IDENTITY,
        require_calculator=True,
        expected_operations=frozenset({"yoy"}),
        trusted_evidence=evidence,
    )

    assert result.allowed is True
    assert len(result.runs) == 1
    assert result.runs[0]["schema_version"] == "siq_financial_calculation_trace_v1"
    assert result.runs[0]["operation"] == "yoy"
    assert result.runs[0]["metric"] == "goodwill_net_yoy"
    assert set(result.runs[0]["inputs"]) == {"current", "previous"}
    assert result.runs[0]["result"]["percent"]
    assert result.runs[0]["research_identity"] == MIDEA_IDENTITY


def test_repeated_ratio_claim_reuses_source_bound_trace_by_subject():
    gross = _trusted_period_goodwill_fact(
        "goodwill_gross",
        "商誉账面原值",
        "2025-12-31",
        "34813270",
        "gross-2025",
        ("商誉账面原值", "商誉原值"),
    )
    kuka = _trusted_period_goodwill_fact(
        "goodwill_component_kuka",
        "KUKA 集团",
        "2025-12-31",
        "23435302",
        "kuka-2025",
        ("KUKA", "KUKA 集团"),
    )
    source = "[D1] source_type=wiki_document_links task_id=task-midea pdf_page=206 table_index=163 md_line=4325"
    reply = (
        "KUKA 期末原值 23,435,302 千元，占商誉原值 34,813,270 千元的 67.32%。\n"
        "结论：KUKA 集团占比为 67.32%。\n"
        f"{source}"
    )

    result = validate_calculation_traces(
        reply,
        expected_identity=MIDEA_IDENTITY,
        require_calculator=True,
        expected_operations=frozenset({"ratio"}),
        trusted_evidence=(gross, kuka),
    )

    assert result.allowed is True
    assert {run["metric"] for run in result.runs} == {"goodwill_component_kuka_ratio"}


def test_ratio_claim_cannot_borrow_same_value_from_another_goodwill_subject():
    gross = _trusted_period_goodwill_fact(
        "goodwill_gross",
        "商誉账面原值",
        "2025-12-31",
        "34813270",
        "gross-2025",
        ("商誉账面原值", "商誉原值"),
    )
    kuka = _trusted_period_goodwill_fact(
        "goodwill_component_kuka",
        "KUKA 集团",
        "2025-12-31",
        "23435302",
        "kuka-2025",
        ("KUKA", "KUKA 集团"),
    )
    source = "[D1] source_type=wiki_document_links task_id=task-midea pdf_page=206 table_index=163 md_line=4325"
    reply = (
        "KUKA 期末原值 23,435,302 千元，占商誉原值 34,813,270 千元的 67.32%。\n"
        "商誉减值覆盖率为 67.32%。\n"
        f"{source}"
    )

    result = validate_calculation_traces(
        reply,
        expected_identity=MIDEA_IDENTITY,
        require_calculator=True,
        expected_operations=frozenset({"ratio"}),
        trusted_evidence=(gross, kuka),
    )

    assert result.allowed is False
    assert result.reason == "trace_claim_result_mismatch"


def test_structured_coverage_trace_requires_allowance_as_numerator():
    gross = _trusted_period_goodwill_fact(
        "goodwill_gross",
        "商誉账面原值",
        "2025-12-31",
        "34813270",
        "gross-2025",
        ("商誉账面原值", "商誉原值"),
    )
    kuka = _trusted_period_goodwill_fact(
        "goodwill_component_kuka",
        "KUKA 集团",
        "2025-12-31",
        "23435302",
        "kuka-2025",
        ("KUKA", "KUKA 集团"),
    )
    trace = json.dumps(
        {
            "schema_version": "siq_financial_calculation_trace_v1",
            "tool": "financial_calculator.py",
            "operation": "ratio",
            "metric": "goodwill_impairment_coverage",
            "period": "2025-12-31",
            "inputs": {
                "numerator": {
                    "metric": "goodwill_component_kuka",
                    "period": "2025-12-31",
                    "value": "23435302",
                    "unit": "人民币千元",
                    "evidence_id": "kuka-2025",
                },
                "denominator": {
                    "metric": "goodwill_gross",
                    "period": "2025-12-31",
                    "value": "34813270",
                    "unit": "人民币千元",
                    "evidence_id": "gross-2025",
                },
            },
            "result": {"ratio": "0.6731715233874899", "percent": "67.31715233874899"},
            "research_identity": MIDEA_IDENTITY,
        }
    )
    source = "[D1] source_type=wiki_document_links task_id=task-midea pdf_page=206 table_index=163 md_line=4325"

    result = validate_calculation_traces(
        f"商誉减值覆盖率为 67.32%。\n```json\n{trace}\n```\n{source}",
        expected_identity=MIDEA_IDENTITY,
        require_calculator=True,
        expected_operations=frozenset({"ratio"}),
        trusted_evidence=(gross, kuka),
    )

    assert result.allowed is False
    assert result.reason == "trace_input_metric_mismatch"


def test_claim_verifier_distinguishes_prior_allowance_balance_from_current_change():
    previous = _trusted_period_goodwill_fact(
        "goodwill_impairment_allowance",
        "商誉减值准备",
        "2024-12-31",
        "569005",
        "midea-allowance-2024",
        ("商誉减值准备", "减值准备", "减值准备余额"),
    )
    current = _trusted_period_goodwill_fact(
        "goodwill_impairment_allowance",
        "商誉减值准备",
        "2025-12-31",
        "556411",
        "midea-allowance-2025",
        ("商誉减值准备", "减值准备", "减值准备余额"),
    )
    change = _trusted_period_goodwill_fact(
        "goodwill_impairment_allowance_absolute_change",
        "商誉减值准备变动额",
        "2025-12-31",
        "12594",
        "midea-allowance-change-2025",
        ("商誉减值准备变动", "减值准备", "减值准备减少", "本期减少", "绝对变动"),
    )
    change["change_direction"] = "decrease"
    source = "[D1] source_type=wiki_document_links task_id=task-midea pdf_page=206 table_index=163 md_line=4325"

    result = verify_financial_claims(
        "减值准备余额较 2024 年末的 5.69 亿元小幅下降 0.13 亿元"
        f"（减少 12,594 千元）。\n{source}",
        expected_identity=MIDEA_IDENTITY,
        trusted_evidence=(previous, current, change),
    )

    assert result.allowed is True
    assert {(claim.metric, claim.period_text) for claim in result.claims} == {
        ("goodwill_impairment_allowance", "2024"),
        ("goodwill_impairment_allowance_absolute_change", "2025-12-31,2025"),
    }


def test_claim_verifier_binds_from_to_allowance_balances_before_change_amount():
    previous = _trusted_period_goodwill_fact(
        "goodwill_impairment_allowance",
        "商誉减值准备",
        "2024-12-31",
        "569005",
        "midea-allowance-2024",
        ("商誉减值准备", "减值准备", "减值准备余额"),
    )
    current = _trusted_period_goodwill_fact(
        "goodwill_impairment_allowance",
        "商誉减值准备",
        "2025-12-31",
        "556411",
        "midea-allowance-2025",
        ("商誉减值准备", "减值准备", "减值准备余额"),
    )
    change = _trusted_period_goodwill_fact(
        "goodwill_impairment_allowance_absolute_change",
        "商誉减值准备变动额",
        "2025-12-31",
        "12594",
        "midea-allowance-change-2025",
        ("商誉减值准备变动", "减值准备", "减值准备减少", "本期减少", "绝对变动"),
    )
    change["change_direction"] = "decrease"
    source = "[D1] source_type=wiki_document_links task_id=task-midea pdf_page=206 table_index=163 md_line=4325"

    result = verify_financial_claims(
        f"减值准备余额由 569,005 千元降至 556,411 千元，减少 12,594 千元。\n{source}",
        expected_identity=MIDEA_IDENTITY,
        trusted_evidence=(previous, current, change),
    )

    assert result.allowed is True
    assert [(claim.metric, claim.value) for claim in result.claims] == [
        ("goodwill_impairment_allowance", 569005.0),
        ("goodwill_impairment_allowance", 556411.0),
        ("goodwill_impairment_allowance_absolute_change", 12594.0),
    ]


@pytest.mark.parametrize(
    "reply",
    (
        "减值准备余额由 569,005 千元降至 569,005 千元，减少 12,594 千元。",
        "减值准备余额由 569,005 千元降至 555,000 千元，减少 12,594 千元。",
        "减值准备余额由 569,005 千元升至 556,411 千元，减少 12,594 千元。",
    ),
)
def test_claim_verifier_rejects_forged_or_directionally_impossible_balance_transition(reply):
    previous = _trusted_period_goodwill_fact(
        "goodwill_impairment_allowance",
        "商誉减值准备",
        "2024-12-31",
        "569005",
        "midea-allowance-2024",
        ("商誉减值准备", "减值准备", "减值准备余额"),
    )
    current = _trusted_period_goodwill_fact(
        "goodwill_impairment_allowance",
        "商誉减值准备",
        "2025-12-31",
        "556411",
        "midea-allowance-2025",
        ("商誉减值准备", "减值准备", "减值准备余额"),
    )
    change = {
        **_trusted_period_goodwill_fact(
            "goodwill_impairment_allowance_absolute_change",
            "商誉减值准备变动额",
            "2025-12-31",
            "12594",
            "midea-allowance-change-2025",
            ("商誉减值准备变动", "减值准备减少", "绝对变动"),
        ),
        "change_direction": "decrease",
    }
    source = "[D1] source_type=wiki_document_links task_id=task-midea pdf_page=206 table_index=163 md_line=4325"

    result = verify_financial_claims(
        f"{reply}\n{source}",
        expected_identity=MIDEA_IDENTITY,
        trusted_evidence=(previous, current, change),
    )

    assert result.allowed is False
    assert len([claim for claim in result.claims if claim.metric == "goodwill_impairment_allowance"]) == 2
    assert any(
        violation.metric == "goodwill_impairment_allowance"
        and violation.reason in {"value_mismatch", "direction_mismatch"}
        for violation in result.violations
    )


def test_claim_verifier_balance_transition_cannot_borrow_exact_value_from_older_period():
    facts = (
        _trusted_period_goodwill_fact(
            "goodwill_impairment_allowance",
            "商誉减值准备",
            "2023-12-31",
            "555000",
            "midea-allowance-2023",
            ("商誉减值准备", "减值准备", "减值准备余额"),
        ),
        _trusted_period_goodwill_fact(
            "goodwill_impairment_allowance",
            "商誉减值准备",
            "2024-12-31",
            "569005",
            "midea-allowance-2024",
            ("商誉减值准备", "减值准备", "减值准备余额"),
        ),
        _trusted_period_goodwill_fact(
            "goodwill_impairment_allowance",
            "商誉减值准备",
            "2025-12-31",
            "556411",
            "midea-allowance-2025",
            ("商誉减值准备", "减值准备", "减值准备余额"),
        ),
    )
    source = "[D1] source_type=wiki_document_links task_id=task-midea pdf_page=206 table_index=163 md_line=4325"

    result = verify_financial_claims(
        f"本期减值准备余额由 569,005 千元降至 555,000 千元。\n{source}",
        expected_identity=MIDEA_IDENTITY,
        trusted_evidence=facts,
    )

    assert result.allowed is False
    assert result.violations[0].metric == "goodwill_impairment_allowance"
    assert result.violations[0].evidence_value == 556411.0


def _trusted_allowance_history() -> tuple[dict, ...]:
    return (
        _trusted_period_goodwill_fact(
            "goodwill_impairment_allowance",
            "商誉减值准备",
            "2023-12-31",
            "555000",
            "midea-allowance-2023",
            ("商誉减值准备", "减值准备", "减值准备余额"),
        ),
        _trusted_period_goodwill_fact(
            "goodwill_impairment_allowance",
            "商誉减值准备",
            "2024-12-31",
            "569005",
            "midea-allowance-2024",
            ("商誉减值准备", "减值准备", "减值准备余额"),
        ),
        _trusted_period_goodwill_fact(
            "goodwill_impairment_allowance",
            "商誉减值准备",
            "2025-12-31",
            "556411",
            "midea-allowance-2025",
            ("商誉减值准备", "减值准备", "减值准备余额"),
        ),
    )


@pytest.mark.parametrize("period_label", ("本期", "本年", "报告期"))
def test_claim_verifier_current_period_transition_binds_latest_two_periods(period_label: str):
    source = "[D1] source_type=wiki_document_links task_id=task-midea pdf_page=206 table_index=163 md_line=4325"
    evidence = _trusted_allowance_history()

    correct = verify_financial_claims(
        f"{period_label}减值准备余额由 569,005 千元降至 556,411 千元。\n{source}",
        expected_identity=MIDEA_IDENTITY,
        trusted_evidence=evidence,
    )
    stale = verify_financial_claims(
        f"{period_label}减值准备余额由 555,000 千元升至 569,005 千元。\n{source}",
        expected_identity=MIDEA_IDENTITY,
        trusted_evidence=evidence,
    )

    assert correct.allowed is True
    assert [claim.period_text for claim in correct.claims] == [
        "2024-12-31,2024",
        "2025-12-31,2025",
    ]
    assert stale.allowed is False
    assert [claim.period_text for claim in stale.claims] == [
        "2024-12-31,2024",
        "2025-12-31,2025",
    ]
    assert {violation.period for violation in stale.violations} == {"2024-12-31", "2025-12-31"}


@pytest.mark.parametrize("separator", ("，", "；", "。"))
def test_claim_verifier_period_endpoint_continuation_inherits_subject(separator: str):
    source = "[D1] source_type=wiki_document_links task_id=task-midea pdf_page=206 table_index=163 md_line=4325"
    evidence = _trusted_allowance_history()

    correct = verify_financial_claims(
        f"本期商誉减值准备期初为 569,005 千元{separator}期末为 556,411 千元。\n{source}",
        expected_identity=MIDEA_IDENTITY,
        trusted_evidence=evidence,
    )
    forged = verify_financial_claims(
        f"本期商誉减值准备期初为 569,005 千元{separator}期末为 999,999 千元。\n{source}",
        expected_identity=MIDEA_IDENTITY,
        trusted_evidence=evidence,
    )

    assert correct.allowed is True
    assert [(claim.value, claim.period_text) for claim in correct.claims] == [
        (569005.0, "2024-12-31,2024"),
        (556411.0, "2025-12-31,2025"),
    ]
    assert forged.allowed is False
    assert any(
        violation.evidence_value == 556411.0 and violation.period == "2025-12-31"
        for violation in forged.violations
    )


@pytest.mark.parametrize("connector", ("降至", "减至", "变为", "调整为", "转为"))
def test_claim_verifier_transition_continuation_inherits_subject(connector: str):
    source = "[D1] source_type=wiki_document_links task_id=task-midea pdf_page=206 table_index=163 md_line=4325"
    evidence = _trusted_allowance_history()

    correct = verify_financial_claims(
        f"商誉减值准备余额为 569,005 千元，{connector} 556,411 千元。\n{source}",
        expected_identity=MIDEA_IDENTITY,
        trusted_evidence=evidence,
    )
    forged = verify_financial_claims(
        f"商誉减值准备余额为 569,005 千元，{connector} 999,999 千元。\n{source}",
        expected_identity=MIDEA_IDENTITY,
        trusted_evidence=evidence,
    )

    assert correct.allowed is True
    assert [claim.value for claim in correct.claims] == [569005.0, 556411.0]
    assert forged.allowed is False
    assert any(violation.evidence_value == 556411.0 for violation in forged.violations)


@pytest.mark.parametrize("claim_period", ("2025-03-31", "2025Q1", "2025年第一季度", "2025Q4"))
def test_claim_verifier_annual_fact_cannot_back_specific_same_year_period(claim_period: str):
    annual = _trusted_period_goodwill_fact(
        "goodwill_net",
        "商誉净额",
        "2025-12-31",
        "100",
        "midea-goodwill-net-annual-2025",
        ("商誉净额",),
    )
    source = "[D1] source_type=wiki_document_links task_id=task-midea pdf_page=206 table_index=163 md_line=4325"

    exact_annual = verify_financial_claims(
        f"2025-12-31 商誉净额为 100 千元。\n{source}",
        expected_identity=MIDEA_IDENTITY,
        trusted_evidence=(annual,),
    )
    year_only = verify_financial_claims(
        f"2025 年商誉净额为 100 千元。\n{source}",
        expected_identity=MIDEA_IDENTITY,
        trusted_evidence=(annual,),
    )
    wrong_granularity = verify_financial_claims(
        f"{claim_period} 商誉净额为 100 千元。\n{source}",
        expected_identity=MIDEA_IDENTITY,
        trusted_evidence=(annual,),
    )

    assert exact_annual.allowed is True
    assert year_only.allowed is True
    assert wrong_granularity.allowed is False
    assert wrong_granularity.violations[0].reason == "period_mismatch"


def test_claim_verifier_quarter_claim_matches_explicit_quarter_fact():
    quarter = _trusted_period_goodwill_fact(
        "goodwill_net",
        "商誉净额",
        "2025Q1",
        "100",
        "midea-goodwill-net-q1-2025",
        ("商誉净额",),
    )
    source = "[D1] source_type=wiki_document_links task_id=task-midea pdf_page=206 table_index=163 md_line=4325"

    result = verify_financial_claims(
        f"2025Q1 商誉净额为 100 千元。\n{source}",
        expected_identity=MIDEA_IDENTITY,
        trusted_evidence=(quarter,),
    )

    assert result.allowed is True


def test_claim_verifier_prefers_named_goodwill_component_over_generic_gross_label():
    component = _trusted_period_goodwill_fact(
        "goodwill_component_kuka",
        "KUKA 集团",
        "2025-12-31",
        "23435302",
        "midea-kuka-2025",
        ("KUKA 集团", "KUKA", "库卡"),
    )
    previous_gross = _trusted_period_goodwill_fact(
        "goodwill_gross",
        "商誉账面原值",
        "2024-12-31",
        "30150019",
        "midea-gross-2024",
        ("商誉账面原值", "账面原值", "商誉原值", "附注原值总额"),
    )
    current_gross = _trusted_period_goodwill_fact(
        "goodwill_gross",
        "商誉账面原值",
        "2025-12-31",
        "34813270",
        "midea-gross-2025",
        ("商誉账面原值", "账面原值", "商誉原值", "附注原值总额"),
    )
    source = "[D1] source_type=wiki_document_links task_id=task-midea pdf_page=206 table_index=163 md_line=4325"

    result = verify_financial_claims(
        f"KUKA 期末账面原值 23,435,302 千元，占附注原值总额的 67.32%。\n{source}",
        expected_identity=MIDEA_IDENTITY,
        trusted_evidence=(component, previous_gross, current_gross),
    )

    assert result.allowed is True
    assert [(claim.metric, claim.value) for claim in result.claims] == [
        ("goodwill_component_kuka", 23435302.0),
    ]


def test_claim_verifier_named_component_cannot_borrow_total_or_peer_component_value():
    kuka = _trusted_period_goodwill_fact(
        "goodwill_component_kuka",
        "KUKA 集团",
        "2025-12-31",
        "23435302",
        "midea-kuka-2025",
        ("KUKA 集团", "KUKA", "库卡"),
    )
    tlsc = _trusted_period_goodwill_fact(
        "goodwill_component_tlsc",
        "TLSC 集团",
        "2025-12-31",
        "2085854",
        "midea-tlsc-2025",
        ("TLSC 集团", "TLSC"),
    )
    gross = _trusted_period_goodwill_fact(
        "goodwill_gross",
        "商誉账面原值",
        "2025-12-31",
        "34813270",
        "midea-gross-2025",
        ("商誉账面原值", "账面原值", "商誉原值", "附注原值总额"),
    )
    source = "[D1] source_type=wiki_document_links task_id=task-midea pdf_page=206 table_index=163 md_line=4325"

    for reply in (
        "KUKA 期末账面原值 34,813,270 千元。",
        "KUKA 期末账面原值 2,085,854 千元。",
        "KUKA 期末账面原值与 TLSC 相同，同为 2,085,854 千元。",
    ):
        result = verify_financial_claims(
            f"{reply}\n{source}",
            expected_identity=MIDEA_IDENTITY,
            trusted_evidence=(kuka, tlsc, gross),
        )

        assert result.allowed is False, reply
        assert any(
            violation.metric == "goodwill_component_kuka"
            and violation.evidence_value == 23435302.0
            for violation in result.violations
        ), reply


def test_claim_verifier_ratio_denominator_remains_bound_to_goodwill_total():
    kuka = _trusted_period_goodwill_fact(
        "goodwill_component_kuka",
        "KUKA 集团",
        "2025-12-31",
        "23435302",
        "midea-kuka-2025",
        ("KUKA 集团", "KUKA", "库卡"),
    )
    gross = _trusted_period_goodwill_fact(
        "goodwill_gross",
        "商誉账面原值",
        "2025-12-31",
        "34813270",
        "midea-gross-2025",
        ("商誉账面原值", "账面原值", "商誉原值", "附注原值总额"),
    )
    source = "[D1] source_type=wiki_document_links task_id=task-midea pdf_page=206 table_index=163 md_line=4325"

    result = verify_financial_claims(
        f"KUKA 占商誉原值 34,813,270 千元的 67.32%。\n{source}",
        expected_identity=MIDEA_IDENTITY,
        trusted_evidence=(kuka, gross),
    )

    assert result.allowed is True
    assert [(claim.metric, claim.value) for claim in result.claims] == [("goodwill_gross", 34813270.0)]


@pytest.mark.parametrize(
    "reply",
    (
        "KUKA 期末账面原值 21,415,464 千元。",
        "KUKA 本期末账面原值 21,415,464 千元。",
        "KUKA 2025年期末账面原值 21,415,464 千元。",
    ),
)
def test_claim_verifier_named_component_cannot_borrow_prior_period_value(reply):
    previous = _trusted_period_goodwill_fact(
        "goodwill_component_kuka",
        "KUKA 集团",
        "2024-12-31",
        "21415464",
        "midea-kuka-2024",
        ("KUKA 集团", "KUKA", "库卡"),
    )
    current = _trusted_period_goodwill_fact(
        "goodwill_component_kuka",
        "KUKA 集团",
        "2025-12-31",
        "23435302",
        "midea-kuka-2025",
        ("KUKA 集团", "KUKA", "库卡"),
    )
    source = "[D1] source_type=wiki_document_links task_id=task-midea pdf_page=206 table_index=163 md_line=4325"

    result = verify_financial_claims(
        f"{reply}\n{source}",
        expected_identity=MIDEA_IDENTITY,
        trusted_evidence=(previous, current),
    )

    assert result.allowed is False
    assert result.violations[0].metric == "goodwill_component_kuka"
    assert result.violations[0].evidence_value == 23435302.0
    assert result.violations[0].period == "2025-12-31"


@pytest.mark.parametrize(
    "reply",
    (
        "KUKA 上年末账面原值 19,000,000 千元。",
        "KUKA 期初账面原值 19,000,000 千元。",
        "KUKA 年初账面原值 19,000,000 千元。",
    ),
)
def test_claim_verifier_opening_balance_binds_immediately_previous_period(reply):
    evidence = tuple(
        _trusted_period_goodwill_fact(
            "goodwill_component_kuka",
            "KUKA 集团",
            period,
            value,
            evidence_id,
            ("KUKA 集团", "KUKA", "库卡"),
        )
        for period, value, evidence_id in (
            ("2023-12-31", "19000000", "midea-kuka-2023"),
            ("2024-12-31", "21415464", "midea-kuka-2024"),
            ("2025-12-31", "23435302", "midea-kuka-2025"),
        )
    )
    source = "[D1] source_type=wiki_document_links task_id=task-midea pdf_page=206 table_index=163 md_line=4325"

    result = verify_financial_claims(
        f"{reply}\n{source}",
        expected_identity=MIDEA_IDENTITY,
        trusted_evidence=evidence,
    )

    assert result.allowed is False
    assert result.violations[0].evidence_value == 21415464.0
    assert result.violations[0].period == "2024-12-31"


def test_midea_goodwill_answer_claim_backtest_has_no_false_positive():
    evidence = (
        _trusted_period_goodwill_fact(
            "goodwill_net",
            "商誉净额",
            "2024-12-31",
            "29581014",
            "net-2024",
            ("商誉净额", "商誉账面价值", "主表商誉"),
        ),
        _trusted_period_goodwill_fact(
            "goodwill_net",
            "商誉净额",
            "2025-12-31",
            "34256859",
            "net-2025",
            ("商誉净额", "商誉账面价值", "主表商誉"),
        ),
        _trusted_period_goodwill_fact(
            "goodwill_net_absolute_change",
            "商誉净额变动额",
            "2025-12-31",
            "4675845",
            "net-change-2025",
            ("商誉增加", "商誉绝对变动", "绝对变动"),
        ),
        _trusted_period_goodwill_fact(
            "goodwill_impairment_allowance",
            "商誉减值准备",
            "2024-12-31",
            "569005",
            "allowance-2024",
            ("商誉减值准备", "减值准备", "减值准备余额"),
        ),
        _trusted_period_goodwill_fact(
            "goodwill_impairment_allowance",
            "商誉减值准备",
            "2025-12-31",
            "556411",
            "allowance-2025",
            ("商誉减值准备", "减值准备", "减值准备余额"),
        ),
        {
            **_trusted_period_goodwill_fact(
                "goodwill_impairment_allowance_absolute_change",
                "商誉减值准备变动额",
                "2025-12-31",
                "12594",
                "allowance-change-2025",
                ("减值准备减少", "减值准备变动", "绝对变动"),
            ),
            "change_direction": "decrease",
        },
        _trusted_period_goodwill_fact(
            "goodwill_component_kuka",
            "KUKA 集团",
            "2025-12-31",
            "23435302",
            "kuka-2025",
            ("KUKA 集团", "KUKA", "库卡"),
        ),
        _trusted_period_goodwill_fact(
            "goodwill_gross",
            "商誉账面原值",
            "2024-12-31",
            "30150019",
            "gross-2024",
            ("商誉账面原值", "账面原值", "附注原值总额"),
        ),
        _trusted_period_goodwill_fact(
            "goodwill_gross",
            "商誉账面原值",
            "2025-12-31",
            "34813270",
            "gross-2025",
            ("商誉账面原值", "账面原值", "附注原值总额"),
        ),
        {
            **_trusted_period_goodwill_fact(
                "goodwill_component_other_absolute_change",
                "其他(i)变动额",
                "2025-12-31",
                "2710278",
                "other-change-2025",
                ("其他(i)净增", "其他(i)变动", "净增"),
            ),
            "change_direction": "increase",
        },
    )
    source = "[D1] source_type=wiki_document_links task_id=task-midea pdf_page=206 table_index=163 md_line=4325"
    reply = (
        "商誉账面价值 2025-12-31 为 342.57 亿元（34,256,859 千元），"
        "较 2024-12-31 的 295.81 亿元（29,581,014 千元）增加 46.76 亿元。\n"
        "减值准备余额由 569,005 千元降至 556,411 千元，减少 12,594 千元。\n"
        "KUKA 期末账面原值 23,435,302 千元，占附注原值总额的 67.32%。\n"
        "附注原值 34,813,270 千元 - 减值准备 556,411 千元 = 主表净额 34,256,859 千元。\n"
        "其他(i)本期净增 27.10 亿元。\n"
        f"{source}"
    )

    result = verify_financial_claims(
        reply,
        expected_identity=MIDEA_IDENTITY,
        trusted_evidence=evidence,
    )

    assert result.allowed is True
    assert result.violations == ()


@pytest.mark.parametrize(
    "equation",
    (
        "账面原值 34,813,270 千元 - 减值准备 556,411 千元 = **34,256,859 千元**。",
        "附注原值 34,813,270 千元 - 减值准备 556,411 千元 = 34,256,859 千元，与主表净额差异为 0。",
    ),
)
def test_claim_verifier_binds_named_reconciliation_equation_rhs_to_net_fact(equation: str):
    source = "[D1] source_type=wiki_document_links task_id=task-midea pdf_page=206 table_index=163 md_line=4325"

    result = verify_financial_claims(
        f"{equation}\n{source}",
        expected_identity=MIDEA_IDENTITY,
        trusted_evidence=_trusted_goodwill_evidence(),
    )

    assert result.allowed is True
    assert [(claim.metric, claim.value, claim.period_text) for claim in result.claims] == [
        ("goodwill_gross", 34813270.0, ""),
        ("goodwill_impairment_allowance", 556411.0, ""),
        ("goodwill_net", 34256859.0, "2025-12-31,2025"),
    ]


def test_claim_verifier_rejects_wrong_reconciliation_rhs_even_when_another_equation_is_valid():
    source = "[D1] source_type=wiki_document_links task_id=task-midea pdf_page=206 table_index=163 md_line=4325"
    reply = (
        "附注原值 34,813,270 千元 - 减值准备 556,411 千元 = **99,999,999 千元**，"
        "与主表净额差异为 0。\n"
        "账面原值 34,813,270 千元 - 减值准备 556,411 千元 = 34,256,859 千元。\n"
        f"{source}"
    )

    result = verify_financial_claims(
        reply,
        expected_identity=MIDEA_IDENTITY,
        trusted_evidence=_trusted_goodwill_evidence(),
    )

    assert result.allowed is False
    assert any(
        violation.line_number == 1
        and violation.metric == "goodwill_net"
        and violation.claimed_value == 99999999.0
        and violation.evidence_value == 34256859.0
        and violation.reason == "value_mismatch"
        for violation in result.violations
    )


def test_reconciliation_rhs_binding_requires_matching_period_and_scope():
    gross, allowance, net = (dict(item) for item in _trusted_goodwill_evidence())
    allowance["financial_scope"] = "parent"
    source = "[D1] source_type=wiki_document_links task_id=task-midea pdf_page=206 table_index=163 md_line=4325"

    result = verify_financial_claims(
        f"账面原值 34,813,270 千元 - 减值准备 556,411 千元 = 34,256,859 千元。\n{source}",
        expected_identity=MIDEA_IDENTITY,
        trusted_evidence=(gross, allowance, net),
    )

    assert all(claim.metric != "goodwill_net" for claim in result.claims)


@pytest.mark.parametrize(
    ("current_overrides", "expected_reason"),
    (
        ({"table_index": 164, "md_line": 4400}, "trace_unstructured"),
        ({"financial_scope": "parent"}, "trace_unstructured"),
    ),
)
def test_evidence_recompute_rejects_yoy_across_table_lineage_or_scope(
    current_overrides: dict,
    expected_reason: str,
):
    previous = _trusted_period_goodwill_fact(
        "goodwill_net",
        "商誉净额",
        "2024-12-31",
        "100",
        "goodwill-net-consolidated-2024",
        ("商誉净额",),
    )
    previous["financial_scope"] = "consolidated"
    current = _trusted_period_goodwill_fact(
        "goodwill_net",
        "商誉净额",
        "2025-12-31",
        "40",
        "goodwill-net-current-2025",
        ("商誉净额",),
    )
    current["financial_scope"] = "consolidated"
    current.update(current_overrides)
    sources = (
        "[D1] source_type=wiki_document_links task_id=task-midea "
        "pdf_page=206 table_index=163 md_line=4325\n"
        "[D2] source_type=wiki_document_links task_id=task-midea "
        f"pdf_page={current['pdf_page']} table_index={current['table_index']} md_line={current['md_line']}"
    )

    result = validate_calculation_traces(
        f"商誉净额同比下降 60%。\n{sources}",
        expected_identity=MIDEA_IDENTITY,
        require_calculator=True,
        expected_operations=frozenset({"yoy"}),
        trusted_evidence=(previous, current),
    )

    assert result.allowed is False
    assert result.reason == expected_reason


def test_structured_trace_cannot_omit_roles_to_bypass_yoy_lineage_check():
    previous = _trusted_period_goodwill_fact(
        "goodwill_net",
        "商誉净额",
        "2024-12-31",
        "100",
        "goodwill-net-consolidated-2024",
        ("商誉净额",),
    )
    previous.update({"financial_scope": "consolidated", "source_lineage": "lineage-consolidated"})
    current = _trusted_period_goodwill_fact(
        "goodwill_net",
        "商誉净额",
        "2025-12-31",
        "40",
        "goodwill-net-parent-2025",
        ("商誉净额",),
    )
    current.update(
        {
            "financial_scope": "parent",
            "source_lineage": "lineage-parent",
            "pdf_page": 207,
            "table_index": 164,
            "md_line": 4400,
        }
    )
    trace = json.dumps(
        {
            "schema_version": "siq_financial_calculation_trace_v1",
            "tool": "financial_calculator.py",
            "operation": "yoy",
            "metric": "goodwill_net_yoy",
            "period": "2025-12-31",
            "inputs": {
                "current": {
                    "metric": "goodwill_net",
                    "period": "2025-12-31",
                    "value": "40",
                    "unit": "人民币千元",
                    "evidence_id": current["evidence_id"],
                },
                "previous": {
                    "metric": "goodwill_net",
                    "period": "2024-12-31",
                    "value": "100",
                    "unit": "人民币千元",
                    "evidence_id": previous["evidence_id"],
                },
            },
            "result": {"rate": "-0.6", "percent": "-60"},
            "research_identity": MIDEA_IDENTITY,
        }
    )
    reply = (
        "商誉净额同比下降 60%。\n"
        f"```json\n{trace}\n```\n"
        "[D1] source_type=wiki_document_links task_id=task-midea "
        "pdf_page=206 table_index=163 md_line=4325\n"
        "[D2] source_type=wiki_document_links task_id=task-midea "
        "pdf_page=207 table_index=164 md_line=4400"
    )

    result = validate_calculation_traces(
        reply,
        expected_identity=MIDEA_IDENTITY,
        require_calculator=True,
        expected_operations=frozenset({"yoy"}),
        trusted_evidence=(previous, current),
    )

    assert result.allowed is False
    assert result.reason == "trace_input_lineage_mismatch"


def test_ratio_recompute_requires_same_lineage_or_explicit_matching_scope():
    gross_profit = _trusted_period_goodwill_fact(
        "gross_profit",
        "毛利润",
        "2025-12-31",
        "40",
        "gross-profit-2025",
        ("毛利润", "毛利"),
    )
    revenue = _trusted_period_goodwill_fact(
        "revenue",
        "营业收入",
        "2025-12-31",
        "100",
        "revenue-2025",
        ("营业收入", "收入"),
    )
    revenue.update({"pdf_page": 207, "table_index": 164, "md_line": 4400})
    gross_profit.pop("financial_scope")
    revenue.pop("financial_scope")
    sources = (
        "[D1] source_type=wiki_document_links task_id=task-midea "
        "pdf_page=206 table_index=163 md_line=4325\n"
        "[D2] source_type=wiki_document_links task_id=task-midea "
        "pdf_page=207 table_index=164 md_line=4400"
    )
    reply = f"公司 2025 年毛利率为 40%。\n{sources}"

    unknown_scope = validate_calculation_traces(
        reply,
        expected_identity=MIDEA_IDENTITY,
        require_calculator=True,
        expected_operations=frozenset({"ratio"}),
        trusted_evidence=(gross_profit, revenue),
    )
    gross_profit["financial_scope"] = "consolidated"
    revenue["financial_scope"] = "consolidated"
    explicit_same_scope = validate_calculation_traces(
        reply,
        expected_identity=MIDEA_IDENTITY,
        require_calculator=True,
        expected_operations=frozenset({"ratio"}),
        trusted_evidence=(gross_profit, revenue),
    )

    assert unknown_scope.allowed is False
    assert unknown_scope.reason == "trace_unstructured"
    assert explicit_same_scope.allowed is True


@pytest.mark.parametrize(("net_scope", "allowed"), (("consolidated", True), ("parent", False)))
def test_evidence_recompute_reconciliation_allows_cross_table_only_with_same_scope(
    net_scope: str,
    allowed: bool,
):
    gross, allowance, net = (dict(item) for item in _trusted_goodwill_evidence())
    gross["financial_scope"] = "consolidated"
    allowance["financial_scope"] = "consolidated"
    net.update(
        {
            "financial_scope": net_scope,
            "pdf_page": 132,
            "table_index": 89,
            "md_line": 2497,
        }
    )
    reply = (
        "商誉账面原值 34,813,270 千元\n"
        "商誉减值准备 556,411 千元\n"
        "商誉净额 34,256,859 千元\n"
        "[D1] source_type=wiki_document_links task_id=task-midea "
        "pdf_page=206 table_index=163 md_line=4325\n"
        "[D2] source_type=wiki_metrics task_id=task-midea "
        "pdf_page=132 table_index=89 md_line=2497"
    )

    result = validate_calculation_traces(
        reply,
        expected_identity=MIDEA_IDENTITY,
        require_reconciliation=True,
        trusted_evidence=(gross, allowance, net),
    )

    assert result.allowed is allowed
    if allowed:
        assert result.runs[0]["inputs"]["gross"]["evidence_id"] == gross["evidence_id"]
        assert result.runs[0]["inputs"]["net"]["evidence_id"] == net["evidence_id"]
    else:
        assert result.reason == "trace_unstructured"


def test_evidence_recompute_reconciliation_accepts_same_identity_when_all_scopes_are_unknown():
    gross, allowance, net = (dict(item) for item in _trusted_goodwill_evidence())
    for item in (gross, allowance, net):
        item.pop("financial_scope", None)
        item.pop("statement_scope", None)
        item.pop("scope", None)
    gross.update({"value": "1282085915.36", "unit": "元", "period": "2025-12-31"})
    allowance.update({"value": "98963594.89", "unit": "元", "period": "2025-12-31"})
    net.update(
        {
            "value": "1183122320.47",
            "unit": "元",
            "period": "2025-12-31",
            "pdf_page": 65,
            "table_index": 84,
            "md_line": 1840,
        }
    )
    reply = (
        "商誉账面原值 1,282,085,915.36 元 - 减值准备 98,963,594.89 元 = "
        "商誉净额 1,183,122,320.47 元。\n"
        "[D1] source_type=wiki_document_links task_id=task-midea "
        "pdf_page=206 table_index=163 md_line=4325\n"
        "[D2] source_type=wiki_metrics task_id=task-midea "
        "pdf_page=65 table_index=84 md_line=1840"
    )

    result = validate_calculation_traces(
        reply,
        expected_identity=MIDEA_IDENTITY,
        require_reconciliation=True,
        trusted_evidence=(gross, allowance, net),
    )

    assert result.allowed is True
    assert any(run["operation"] == "goodwill_reconciliation" for run in result.runs)


def test_evidence_recompute_reconciliation_uses_exact_evidence_for_rounded_display_equation():
    gross, allowance, net = (dict(item) for item in _trusted_goodwill_evidence())
    for item in (gross, allowance, net):
        item.pop("financial_scope", None)
    gross.update({"value": "1282085915.36", "unit": "元", "period": "2025-12-31"})
    allowance.update({"value": "98963594.89", "unit": "元", "period": "2025-12-31"})
    net.update({"value": "1183122320.47", "unit": "元", "period": "2025-12-31"})
    reply = (
        "商誉账面净值 11.83 亿元 = 商誉账面原值 12.82 亿元 - 商誉减值准备 0.99 亿元。\n"
        "[D1] source_type=wiki_document_links task_id=task-midea "
        "pdf_page=206 table_index=163 md_line=4325"
    )

    result = validate_calculation_traces(
        reply,
        expected_identity=MIDEA_IDENTITY,
        require_reconciliation=True,
        trusted_evidence=(gross, allowance, net),
    )

    assert result.allowed is True
    reconciliation = next(run for run in result.runs if run["operation"] == "goodwill_reconciliation")
    assert reconciliation["result"]["net"] == "1183122320.47"


def test_evidence_recompute_accepts_yoy_when_nearest_year_labels_explicit_previous_operand():
    evidence = (
        _trusted_period_goodwill_fact(
            "goodwill_net",
            "商誉净额",
            "2024-12-31",
            "29581014",
            "goodwill-net-2024",
            ("商誉净额",),
        ),
        _trusted_period_goodwill_fact(
            "goodwill_net",
            "商誉净额",
            "2025-12-31",
            "34256859",
            "goodwill-net-2025",
            ("商誉净额",),
        ),
    )
    source = "[D1] source_type=wiki_document_links task_id=task-midea pdf_page=206 table_index=163 md_line=4325"
    reply = "商誉净额 2025-12-31 为 34,256,859 千元，较 2024-12-31 的 29,581,014 千元增长 15.8%。"

    result = validate_calculation_traces(
        f"{reply}\n{source}",
        expected_identity=MIDEA_IDENTITY,
        require_calculator=True,
        expected_operations=frozenset({"yoy"}),
        trusted_evidence=evidence,
    )

    assert result.allowed is True


def test_evidence_recompute_rejects_yoy_with_explicit_untrusted_previous_period_label():
    evidence = (
        _trusted_period_goodwill_fact(
            "goodwill_net",
            "商誉净额",
            "2024-12-31",
            "29581014",
            "goodwill-net-2024",
            ("商誉净额",),
        ),
        _trusted_period_goodwill_fact(
            "goodwill_net",
            "商誉净额",
            "2025-12-31",
            "34256859",
            "goodwill-net-2025",
            ("商誉净额",),
        ),
    )
    source = "[D1] source_type=wiki_document_links task_id=task-midea pdf_page=206 table_index=163 md_line=4325"
    reply = "商誉净额 2025-12-31 为 34,256,859 千元，较 2023-12-31 的 29,581,014 千元增长 15.8%。"

    result = validate_calculation_traces(
        f"{reply}\n{source}",
        expected_identity=MIDEA_IDENTITY,
        require_calculator=True,
        expected_operations=frozenset({"yoy"}),
        trusted_evidence=evidence,
    )

    assert result.allowed is False
    assert result.reason == "trace_unstructured"


@pytest.mark.parametrize("claim", ("商誉净额增长 16.8%。", "商誉净额 YoY 16.8%。", "商誉净额增幅 16.8%。"))
def test_evidence_recompute_rejects_wrong_directional_or_yoy_percentage(claim: str):
    evidence = (
        _trusted_period_goodwill_fact(
            "goodwill_net",
            "商誉净额",
            "2024-12-31",
            "29581014",
            "goodwill-net-2024",
            ("商誉净额",),
        ),
        _trusted_period_goodwill_fact(
            "goodwill_net",
            "商誉净额",
            "2025-12-31",
            "34256859",
            "goodwill-net-2025",
            ("商誉净额",),
        ),
    )
    source = "[D1] source_type=wiki_document_links task_id=task-midea pdf_page=206 table_index=163 md_line=4325"

    result = validate_calculation_traces(
        f"{claim}\n{source}",
        expected_identity=MIDEA_IDENTITY,
        require_calculator=True,
        expected_operations=frozenset({"yoy"}),
        trusted_evidence=evidence,
    )

    assert result.allowed is False
    assert result.reason == "trace_unstructured"


def _trusted_saic_top2_ratio_evidence() -> tuple[dict, ...]:
    huayu = _trusted_period_goodwill_fact(
        "goodwill_component_huayu",
        "华域视觉科技(上海)有限公司",
        "2025-12-31",
        "781115081.73",
        "saic-huayu-2025",
        ("华域视觉科技(上海)有限公司", "华域视觉"),
    )
    finance = _trusted_period_goodwill_fact(
        "goodwill_component_finance",
        "上汽通用汽车金融有限责任公司",
        "2025-12-31",
        "333378433.68",
        "saic-finance-2025",
        ("上汽通用汽车金融有限责任公司", "上汽通用汽车金融", "上汽通用金融"),
    )
    top2 = _trusted_period_goodwill_fact(
        "goodwill_component_sum_top2",
        "华域视觉 + 上汽通用汽车金融",
        "2025-12-31",
        "1114493515.41",
        "saic-top2-2025",
        ("华域视觉 + 上汽通用汽车金融", "华域视觉 + 上汽通用金融", "前两大"),
    )
    gross = _trusted_period_goodwill_fact(
        "goodwill_gross",
        "商誉账面原值",
        "2025-12-31",
        "1282085915.36",
        "saic-gross-2025",
        ("商誉原值", "账面原值"),
    )
    return (
        {**huayu, "unit": "元"},
        {**finance, "unit": "元"},
        {
            **top2,
            "unit": "元",
            "derived_from_evidence_ids": (huayu["evidence_id"], finance["evidence_id"]),
        },
        {**gross, "unit": "元"},
    )


def _validate_saic_top2_ratio(line: str):
    source = "[D1] source_type=wiki_document_links task_id=task-midea pdf_page=206 table_index=163 md_line=4325"
    return validate_calculation_traces(
        f"{line}\n{source}",
        expected_identity=MIDEA_IDENTITY,
        require_calculator=True,
        expected_operations=frozenset({"ratio"}),
        trusted_evidence=_trusted_saic_top2_ratio_evidence(),
    )


@pytest.mark.parametrize(("amount", "allowed"), (("11.14", True), ("10.14", False)))
def test_claim_verifier_binds_named_component_sum_amount_to_derived_top2_fact(amount: str, allowed: bool):
    source = "[D1] source_type=wiki_document_links task_id=task-midea pdf_page=206 table_index=163 md_line=4325"
    evidence = _trusted_saic_top2_ratio_evidence()

    result = verify_financial_claims(
        "华域视觉与上汽通用汽车金融合计原值 "
        f"{amount} 亿元，占商誉原值 86.91%。\n{source}",
        expected_identity=MIDEA_IDENTITY,
        trusted_evidence=evidence,
    )

    assert result.allowed is allowed
    assert result.claims[0].metric == "goodwill_component_sum_top2"
    if not allowed:
        assert result.violations[0].evidence_value == 1114493515.41


def test_evidence_recompute_accepts_ratio_with_backend_derived_sum_expanded_on_formula_line():
    result = _validate_saic_top2_ratio(
        "华域视觉 + 上汽通用金融占比：(781,115,081.73 + 333,378,433.68) "
        "/ 1,282,085,915.36 = 86.91%"
    )

    assert result.allowed is True
    assert len(result.runs) == 1
    assert result.runs[0]["operation"] == "ratio"


def test_evidence_recompute_accepts_explicit_natural_language_component_ratios():
    source = "[D1] source_type=wiki_document_links task_id=task-midea pdf_page=206 table_index=163 md_line=4325"
    reply = (
        "商誉高度集中：华域视觉（占商誉原值 60.93%）与上汽通用汽车金融"
        "（占商誉原值 26.00%）两者合计占商誉原值 86.93%。\n"
        "| 华域视觉 | 781,115,081.73 元 |\n"
        "| 上汽通用汽车金融 | 333,378,433.68 元 |\n"
        "| 商誉原值 | 1,282,085,915.36 元 |\n"
        f"{source}"
    )

    result = validate_calculation_traces(
        reply,
        expected_identity=MIDEA_IDENTITY,
        require_calculator=True,
        expected_operations=frozenset({"ratio"}),
        trusted_evidence=_trusted_saic_top2_ratio_evidence(),
    )

    assert result.allowed is True
    assert len(result.runs) == 3
    assert {run["operation"] for run in result.runs} == {"ratio"}


def test_evidence_recompute_accepts_goodwill_total_as_gross_denominator_alias():
    source = "[D1] source_type=wiki_document_links task_id=task-midea pdf_page=206 table_index=163 md_line=4325"
    evidence = list(_trusted_saic_top2_ratio_evidence())
    evidence[0] = {
        **evidence[0],
        "aliases": ("华域视觉科技(上海)有限公司", "华域视觉"),
    }
    evidence[-1] = {
        **evidence[-1],
        "aliases": ("商誉原值", "账面原值", "商誉总额"),
    }
    reply = (
        "华域视觉原值占商誉总额 60.93%。\n"
        "| 华域视觉 | 781,115,081.73 元 |\n"
        "| 商誉账面原值 | 1,282,085,915.36 元 |\n"
        f"{source}"
    )

    result = validate_calculation_traces(
        reply,
        expected_identity=MIDEA_IDENTITY,
        require_calculator=True,
        expected_operations=frozenset({"ratio"}),
        trusted_evidence=tuple(evidence),
    )

    assert result.allowed is True
    assert {run["operation"] for run in result.runs} == {"ratio"}


def test_evidence_recompute_binds_repeated_goodwill_analysis_by_local_semantics():
    previous_net = _trusted_period_goodwill_fact(
        "goodwill_net",
        "商誉净额",
        "2024-12-31",
        "29581014",
        "midea-net-2024",
        ("商誉", "商誉净额", "商誉账面价值"),
    )
    current_net = _trusted_period_goodwill_fact(
        "goodwill_net",
        "商誉净额",
        "2025-12-31",
        "34256859",
        "midea-net-2025",
        ("商誉", "商誉净额", "商誉账面价值"),
    )
    component = _trusted_period_goodwill_fact(
        "goodwill_component_kuka",
        "KUKA 集团",
        "2025-12-31",
        "23435302",
        "midea-kuka-2025",
        ("KUKA 集团", "KUKA"),
    )
    gross = _trusted_period_goodwill_fact(
        "goodwill_gross",
        "商誉账面原值",
        "2025-12-31",
        "34813270",
        "midea-gross-2025",
        ("商誉原值", "账面原值", "商誉总额"),
    )
    evidence = (previous_net, current_net, component, gross)
    source = "[D1] source_type=wiki_document_links task_id=task-midea pdf_page=206 table_index=163 md_line=4325"
    reply = (
        "## 结论\n"
        "- 商誉账面价值同比增长 15.81%。\n"
        "- 商誉高度集中于 KUKA 集团，其原值占商誉总额 67.32%。\n\n"
        "### 商誉净额\n"
        "- 同比增量 4,675,845 千元，增幅 15.81%。\n"
        "- KUKA 集团原值占商誉总额比例：67.32%。\n"
        "| KUKA 集团 | 23,435,302 千元 |\n"
        "| 商誉账面原值 | 34,813,270 千元 |\n\n"
        "## 计算器校验\n"
        "- 商誉同比增幅：15.81%。\n"
        "- KUKA 集团占比：67.32%。\n"
        f"{source}"
    )

    result = validate_calculation_traces(
        reply,
        expected_identity=MIDEA_IDENTITY,
        require_calculator=True,
        expected_operations=frozenset({"yoy", "yoy_growth", "ratio"}),
        trusted_evidence=evidence,
    )

    assert result.allowed is True
    assert {run["operation"] for run in result.runs} == {"yoy", "ratio"}


def test_evidence_recompute_rejects_natural_language_component_ratios_without_denominator():
    source = "[D1] source_type=wiki_document_links task_id=task-midea pdf_page=206 table_index=163 md_line=4325"
    reply = (
        "商誉高度集中：华域视觉（占 60.93%）与上汽通用汽车金融"
        "（占 26.00%）两者合计占比 86.93%。\n"
        "| 华域视觉 | 781,115,081.73 元 |\n"
        "| 上汽通用汽车金融 | 333,378,433.68 元 |\n"
        "| 商誉原值 | 1,282,085,915.36 元 |\n"
        f"{source}"
    )

    result = validate_calculation_traces(
        reply,
        expected_identity=MIDEA_IDENTITY,
        require_calculator=True,
        expected_operations=frozenset({"ratio"}),
        trusted_evidence=_trusted_saic_top2_ratio_evidence(),
    )

    assert result.allowed is False
    assert result.reason == "trace_unstructured"


def test_evidence_recompute_rejects_natural_ratio_backed_by_opposite_sign_operand():
    source = "[D1] source_type=wiki_document_links task_id=task-midea pdf_page=206 table_index=163 md_line=4325"
    reply = (
        "华域视觉占商誉原值 60.93%。\n"
        "| 华域视觉 | -781,115,081.73 |\n"
        "| 商誉原值 | 1,282,085,915.36 |\n"
        f"{source}"
    )

    result = validate_calculation_traces(
        reply,
        expected_identity=MIDEA_IDENTITY,
        require_calculator=True,
        expected_operations=frozenset({"ratio"}),
        trusted_evidence=_trusted_saic_top2_ratio_evidence(),
    )

    assert result.allowed is False
    assert result.reason == "trace_unstructured"


@pytest.mark.parametrize(
    "line",
    (
        "华域视觉 + 上汽通用金融占比：781,115,081.73 / 1,282,085,915.36 = 86.91%",
        "华域视觉 + 上汽通用金融占比：(781,115,081.73 - 333,378,433.68) / 1,282,085,915.36 = 86.91%",
        "华域视觉 + 上汽通用金融占比：(781,115,081.73 * 333,378,433.68) / 1,282,085,915.36 = 86.91%",
        "华域视觉 + 上汽通用金融占比：(781,115,081.73 + 333,378,433.68 + 1) / 1,282,085,915.36 = 86.91%",
        "华域视觉 781,115,081.73；上汽通用金融 333,378,433.68 / 1,282,085,915.36 = 86.91%",
    ),
)
def test_evidence_recompute_rejects_incomplete_or_malformed_expanded_sum_ratio(line: str):
    result = _validate_saic_top2_ratio(line)

    assert result.allowed is False
    assert result.reason == "trace_unstructured"


@pytest.mark.parametrize(
    ("claim", "allowed"),
    (
        ("集中度超过 86%", True),
        ("集中度高于 90%", False),
        ("集中度低于 90%", True),
        ("集中度不高于 80%", False),
        ("集中度约为 87%", True),
    ),
)
def test_evidence_recompute_validates_ratio_threshold_and_approximation_semantics(claim: str, allowed: bool):
    result = _validate_saic_top2_ratio(
        "华域视觉 + 上汽通用汽车金融合计 1,114,493,515.41 元，占商誉原值 "
        f"1,282,085,915.36 元，{claim}。"
    )

    assert result.allowed is allowed
    if not allowed:
        assert result.reason == "trace_unstructured"


def _trusted_saic_scale_and_collective_ratio_evidence() -> tuple[dict, ...]:
    facts = (
        ("goodwill_net", "商誉账面价值", "1183122320.47", "saic-net", ("商誉", "商誉账面价值")),
        ("total_assets", "资产总计", "960207461450.69", "saic-assets", ("总资产", "资产总计")),
        (
            "parent_shareholders_equity",
            "归属于母公司所有者权益",
            "298812278173.08",
            "saic-parent-equity",
            ("归母净资产", "归属于母公司所有者权益"),
        ),
        (
            "goodwill_gross",
            "商誉账面原值",
            "1282085915.36",
            "saic-gross",
            ("商誉原值", "账面原值"),
        ),
        (
            "goodwill_component_vision",
            "华域视觉",
            "781115081.73",
            "saic-vision",
            ("华域视觉",),
        ),
        (
            "goodwill_component_finance",
            "上汽通用汽车金融",
            "333378433.68",
            "saic-finance",
            ("上汽通用汽车金融",),
        ),
        (
            "goodwill_component_cowheels",
            "Co wheels UK & Trip IQ",
            "66724864.08",
            "saic-cowheels",
            ("Co wheels UK & Trip IQ",),
        ),
        (
            "goodwill_impairment_allowance",
            "商誉减值准备",
            "98963594.89",
            "saic-allowance",
            ("减值准备", "商誉减值准备"),
        ),
    )
    return tuple(
        {
            **_trusted_period_goodwill_fact(metric, name, "2025-12-31", value, evidence_id, aliases),
            "unit": "元",
        }
        for metric, name, value, evidence_id, aliases in facts
    )


def _validate_saic_scale_and_collective_ratio(reply: str):
    source = "[D1] source_type=wiki_document_links task_id=task-midea pdf_page=206 table_index=163 md_line=4325"
    return validate_calculation_traces(
        f"{reply}\n{source}",
        expected_identity=MIDEA_IDENTITY,
        require_calculator=True,
        expected_operations=frozenset({"ratio"}),
        trusted_evidence=_trusted_saic_scale_and_collective_ratio_evidence(),
    )


def test_evidence_recompute_accepts_source_bound_collective_thresholds_and_cross_line_coverage():
    reply = (
        "商誉规模：商誉占总资产、归母净资产比重均不足 0.5%。\n"
        "华域视觉占比约 60.93%（781,115,081.73 / 1,282,085,915.36），其余主体占比均不足 30%。\n"
        "减值准备对账面原值覆盖率为 7.72%。"
    )

    result = _validate_saic_scale_and_collective_ratio(reply)

    assert result.allowed is True
    assert {run["metric"] for run in result.runs} >= {
        "goodwill_to_total_assets_ratio",
        "goodwill_to_parent_equity_ratio",
        "goodwill_impairment_coverage",
    }


@pytest.mark.parametrize(
    ("old", "new"),
    (("0.5%", "0.1%"), ("30%", "20%"), ("7.72%", "8.72%")),
)
def test_evidence_recompute_rejects_forged_source_bound_thresholds(old: str, new: str):
    reply = (
        "商誉规模：商誉占总资产、归母净资产比重均不足 0.5%。\n"
        "华域视觉占比约 60.93%（781,115,081.73 / 1,282,085,915.36），其余主体占比均不足 30%。\n"
        "减值准备对账面原值覆盖率为 7.72%。"
    ).replace(old, new)

    result = _validate_saic_scale_and_collective_ratio(reply)

    assert result.allowed is False
    assert result.reason == "trace_claim_result_mismatch"


def test_evidence_recompute_accepts_attachment_concentration_and_pp_business_tolerance():
    evidence = (
        _trusted_period_goodwill_fact(
            "goodwill_component_sum_top2",
            "KUKA + 其他(i)合计",
            "2024-12-31",
            "26635994",
            "midea-top2-2024",
            ("前两大", "KUKA + 其他(i)"),
        ),
        _trusted_period_goodwill_fact(
            "goodwill_component_sum_top2",
            "KUKA + 其他(i)合计",
            "2025-12-31",
            "31366110",
            "midea-top2-2025",
            ("前两大", "KUKA + 其他(i)"),
        ),
        _trusted_period_goodwill_fact(
            "goodwill_gross",
            "商誉账面原值",
            "2024-12-31",
            "30150019",
            "midea-gross-2024",
            ("商誉原值", "账面原值"),
        ),
        _trusted_period_goodwill_fact(
            "goodwill_gross",
            "商誉账面原值",
            "2025-12-31",
            "34813270",
            "midea-gross-2025",
            ("商誉原值", "账面原值"),
        ),
    )
    source = "[D1] source_type=wiki_document_links task_id=task-midea pdf_page=206 table_index=163 md_line=4325"
    reply = "前两大集中度 2025 年 90.11%，2024 年 88.34%，上升 1.77 个百分点。"

    result = validate_calculation_traces(
        f"{reply}\n{source}",
        expected_identity=MIDEA_IDENTITY,
        require_calculator=True,
        expected_operations=frozenset({"ratio"}),
        trusted_evidence=evidence,
    )

    assert result.allowed is True


def test_claim_verifier_does_not_apply_a_trailing_year_to_an_earlier_amount():
    evidence = (
        _trusted_period_goodwill_fact(
            "goodwill_component_sum_top2",
            "前两大合计",
            "2024-12-31",
            "26635994",
            "midea-top2-2024",
            ("前两大", "前两大合计"),
        ),
        _trusted_period_goodwill_fact(
            "goodwill_component_sum_top2",
            "前两大合计",
            "2025-12-31",
            "31366110",
            "midea-top2-2025",
            ("前两大", "前两大合计"),
        ),
    )
    source = "[D1] source_type=wiki_document_links task_id=task-midea pdf_page=206 table_index=163 md_line=4325"

    result = verify_financial_claims(
        f"前两大合计 31,366,110 千元，较 2024 年进一步提升。\n{source}",
        expected_identity=MIDEA_IDENTITY,
        trusted_evidence=evidence,
    )

    assert result.allowed is True
    assert result.claims[0].period_tokens == ()


def test_claim_verifier_binds_quoted_metric_yoy_result_to_its_absolute_change():
    evidence = (
        _trusted_period_goodwill_fact(
            "goodwill_component_other",
            "其他(i)",
            "2024-12-31",
            "5220530",
            "midea-other-2024",
            ("其他(i)", "其他(i)商誉"),
        ),
        _trusted_period_goodwill_fact(
            "goodwill_component_other",
            "其他(i)",
            "2025-12-31",
            "7930808",
            "midea-other-2025",
            ("其他(i)", "其他(i)商誉"),
        ),
        {
            **_trusted_period_goodwill_fact(
                "goodwill_component_other_absolute_change",
                "其他(i)变动额",
                "2025-12-31",
                "2710278",
                "midea-other-change-2025",
                ("其他(i)变动", "其他(i)同比变动", "本期净增", "绝对变动"),
            ),
            "change_direction": "increase",
        },
    )
    source = "[D1] source_type=wiki_document_links task_id=task-midea pdf_page=206 table_index=163 md_line=4325"
    line = '- "其他(i)" 同比：(7,930,808 − 5,220,530) / |5,220,530| = +51.92%（+2,710,278 千元）'

    result = verify_financial_claims(
        f"{line}\n{source}",
        expected_identity=MIDEA_IDENTITY,
        trusted_evidence=evidence,
    )

    assert result.allowed is True
    assert result.claims[0].metric == "goodwill_component_other_absolute_change"


def test_calculation_trace_accepts_delta_over_previous_as_equivalent_yoy_formula():
    evidence = (
        _trusted_period_goodwill_fact(
            "goodwill_net", "商誉账面价值", "2024-12-31", "1198210116.59", "saic-net-2024", ("商誉",)
        ),
        _trusted_period_goodwill_fact(
            "goodwill_net", "商誉账面价值", "2025-12-31", "1183122320.47", "saic-net-2025", ("商誉",)
        ),
        {
            **_trusted_period_goodwill_fact(
                "goodwill_net_absolute_change",
                "商誉账面价值变动额",
                "2025-12-31",
                "15087796.12",
                "saic-net-change-2025",
                ("商誉变动额", "绝对变动"),
            ),
            "change_direction": "decrease",
        },
    )
    reply = (
        "YoY：-15,087,796.12 / 1,198,210,116.59 = -1.26%。\n"
        "[D1] source_type=wiki_metrics task_id=task-midea pdf_page=206 table_index=163 md_line=4325"
    )

    result = validate_calculation_traces(
        reply,
        expected_identity=MIDEA_IDENTITY,
        require_calculator=True,
        expected_operations=frozenset({"yoy"}),
        trusted_evidence=evidence,
    )

    assert result.allowed is True
    assert result.reason == ""


def test_claim_verifier_binds_declared_reconciliation_roles_before_bare_equation():
    source = "[D1] source_type=wiki_document_links task_id=task-midea pdf_page=206 table_index=163 md_line=4325"
    reply = (
        "账面原值、减值准备余额、账面价值已按附注勾稽："
        "34,813,270 - 556,411 = 34,256,859 千元。\n"
        f"{source}"
    )

    result = verify_financial_claims(
        reply,
        expected_identity=MIDEA_IDENTITY,
        trusted_evidence=_trusted_goodwill_evidence(),
    )

    assert result.allowed is True
    assert [claim.metric for claim in result.claims] == ["goodwill_net"]


def test_claim_verifier_local_component_change_semantics_override_balance_value_proximity():
    evidence = (
        _trusted_period_goodwill_fact(
            "goodwill_component_other",
            "其他(i)",
            "2024-12-31",
            "5220530",
            "midea-other-2024",
            ("其他(i)", "其他(i)商誉"),
        ),
        _trusted_period_goodwill_fact(
            "goodwill_component_other",
            "其他(i)",
            "2025-12-31",
            "7930808",
            "midea-other-2025",
            ("其他(i)", "其他(i)商誉"),
        ),
        {
            **_trusted_period_goodwill_fact(
                "goodwill_component_other_absolute_change",
                "其他(i)变动额",
                "2025-12-31",
                "2710278",
                "midea-other-change-2025",
                ("其他(i)净增", "其他(i)变动", "本期净增", "净增"),
            ),
            "change_direction": "increase",
        },
    )
    source = "[D1] source_type=wiki_document_links task_id=task-midea pdf_page=206 table_index=163 md_line=4325"

    rounded = verify_financial_claims(
        f"其他(i) 本期净增 27.10 亿元。\n{source}",
        expected_identity=MIDEA_IDENTITY,
        trusted_evidence=evidence,
    )
    forged_previous_balance = verify_financial_claims(
        f"其他(i) 本期净增 52.2053 亿元。\n{source}",
        expected_identity=MIDEA_IDENTITY,
        trusted_evidence=evidence,
    )
    calculation = validate_calculation_traces(
        f"其他(i) 本期净增 27.10 亿元。\n{source}",
        expected_identity=MIDEA_IDENTITY,
        require_calculator=True,
        expected_operations=frozenset({"normalize_amount"}),
        trusted_evidence=evidence,
    )

    assert rounded.allowed is True
    assert [(claim.metric, claim.period_text) for claim in rounded.claims] == [
        ("goodwill_component_other_absolute_change", "2025-12-31,2025")
    ]
    assert forged_previous_balance.allowed is False
    assert forged_previous_balance.claims[0].metric == "goodwill_component_other_absolute_change"
    assert forged_previous_balance.violations[0].evidence_value == 2710278.0
    assert calculation.allowed is True
    assert calculation.runs[0]["metric"] == "goodwill_component_other_absolute_change"


def test_claim_verifier_local_net_decrease_binds_component_absolute_change():
    evidence = (
        _trusted_period_goodwill_fact(
            "goodwill_component_tlsc",
            "TLSC 集团",
            "2025-12-31",
            "2085854",
            "midea-tlsc-2025",
            ("TLSC 集团", "TLSC"),
        ),
        {
            **_trusted_period_goodwill_fact(
                "goodwill_component_tlsc_absolute_change",
                "TLSC 集团变动额",
                "2025-12-31",
                "66865",
                "midea-tlsc-change-2025",
                ("TLSC 净减", "TLSC 变动", "本期减少"),
            ),
            "change_direction": "decrease",
        },
    )
    source = "[D1] source_type=wiki_document_links task_id=task-midea pdf_page=206 table_index=163 md_line=4325"

    result = verify_financial_claims(
        f"TLSC 本期净减 66,865 千元。\n{source}",
        expected_identity=MIDEA_IDENTITY,
        trusted_evidence=evidence,
    )

    assert result.allowed is True
    assert result.claims[0].metric == "goodwill_component_tlsc_absolute_change"
    assert result.claims[0].change_direction == "decrease"


def test_claim_verifier_binds_coordinated_disposal_amounts_to_movements_not_closing_balances():
    component_disposal = {
        **_trusted_period_goodwill_fact(
            "goodwill_component_recycler_absolute_change",
            "上海机动车回收服务中心有限公司转出额",
            "2025-12-31",
            "15087796.12",
            "saic-recycler-disposal",
            ("上海机动车回收服务中心", "转出商誉账面原值", "商誉账面原值转出"),
        ),
        "unit": "元",
        "change_direction": "decrease",
    }
    allowance_change = {
        **_trusted_period_goodwill_fact(
            "goodwill_impairment_allowance_absolute_change",
            "商誉减值准备变动额",
            "2025-12-31",
            "5825349.96",
            "saic-allowance-disposal",
            ("减值准备变动", "减值准备", "本期减少"),
        ),
        "unit": "元",
        "change_direction": "decrease",
    }
    balances = (
        {
            **_trusted_period_goodwill_fact(
                "goodwill_gross",
                "商誉账面原值",
                "2025-12-31",
                "1282085915.36",
                "saic-gross-balance",
                ("商誉账面原值", "账面原值"),
            ),
            "unit": "元",
        },
        {
            **_trusted_period_goodwill_fact(
                "goodwill_impairment_allowance",
                "商誉减值准备",
                "2025-12-31",
                "98963594.89",
                "saic-allowance-balance",
                ("减值准备", "商誉减值准备"),
            ),
            "unit": "元",
        },
    )
    source = "[D1] source_type=wiki_document_links task_id=task-midea pdf_page=206 table_index=163 md_line=4325"
    reply = "对应转出商誉账面原值 15,087,796.12 元及减值准备 5,825,349.96 元。"

    valid = verify_financial_claims(
        f"{reply}\n{source}",
        expected_identity=MIDEA_IDENTITY,
        trusted_evidence=(*balances, component_disposal, allowance_change),
    )
    tampered = verify_financial_claims(
        f"{reply.replace('15,087,796.12', '16,087,796.12')}\n{source}",
        expected_identity=MIDEA_IDENTITY,
        trusted_evidence=(*balances, component_disposal, allowance_change),
    )

    assert valid.allowed is True
    assert [claim.metric for claim in valid.claims] == [
        "goodwill_component_recycler_absolute_change",
        "goodwill_impairment_allowance_absolute_change",
    ]
    assert tampered.allowed is False
    assert tampered.violations[0].metric == "goodwill_component_recycler_absolute_change"


@pytest.mark.parametrize("minus_sign", ("-", "−", "‐", "‑", "‒", "﹣", "－"))
def test_claim_verifier_binds_signed_decrease_to_positive_absolute_change_evidence(minus_sign: str):
    evidence = {
        **_trusted_period_goodwill_fact(
            "goodwill_gross_absolute_change",
            "商誉账面原值变动额",
            "2025-12-31",
            "20913146.08",
            "saic-gross-change-2025",
            ("商誉账面原值变动", "商誉原值变动", "本期减少", "绝对变动"),
        ),
        "unit": "元",
        "change_direction": "decrease",
    }
    source = "[D1] source_type=wiki_document_links task_id=task-midea pdf_page=206 table_index=163 md_line=4325"

    result = verify_financial_claims(
        f"商誉账面原值较上年下降（{minus_sign}20,913,146.08 元）。\n{source}",
        expected_identity=MIDEA_IDENTITY,
        trusted_evidence=(evidence,),
    )

    assert result.allowed is True
    assert result.claims[0].metric == "goodwill_gross_absolute_change"
    assert result.claims[0].normalized_value < 0


@pytest.mark.parametrize(
    "claim",
    (
        "商誉账面原值较上年增加（+20,913,146.08 元）。",
        "商誉账面原值较上年上升 20,913,146.08 元。",
        "商誉账面原值较上年下降（+20,913,146.08 元）。",
    ),
)
def test_claim_verifier_rejects_absolute_change_with_opposite_or_conflicting_direction(claim: str):
    evidence = {
        **_trusted_period_goodwill_fact(
            "goodwill_gross_absolute_change",
            "商誉账面原值变动额",
            "2025-12-31",
            "20913146.08",
            "saic-gross-change-2025",
            ("商誉账面原值变动", "商誉原值变动", "本期减少", "绝对变动"),
        ),
        "unit": "元",
        "change_direction": "decrease",
    }
    source = "[D1] source_type=wiki_document_links task_id=task-midea pdf_page=206 table_index=163 md_line=4325"

    result = verify_financial_claims(
        f"{claim}\n{source}",
        expected_identity=MIDEA_IDENTITY,
        trusted_evidence=(evidence,),
    )

    assert result.allowed is False
    assert result.claims[0].metric == "goodwill_gross_absolute_change"
    assert result.violations[0].reason == "direction_mismatch"


@pytest.mark.parametrize(
    "claim",
    (
        "商誉减值准备变动额本期未计提 0 元。",
        "商誉减值准备变动额持平且未新增计提 0 元。",
        "商誉减值准备变动额无新增计提，为 0 元。",
    ),
)
def test_claim_verifier_treats_negated_zero_provision_as_unchanged(claim: str):
    evidence = {
        **_trusted_period_goodwill_fact(
            "goodwill_allowance_absolute_change",
            "商誉减值准备变动额",
            "2025-12-31",
            "0",
            "saic-allowance-change-2025",
            ("商誉减值准备变动", "本期持平", "未计提", "绝对变动"),
        ),
        "unit": "元",
        "change_direction": "unchanged",
    }
    source = "[D1] source_type=wiki_document_links task_id=task-midea pdf_page=206 table_index=163 md_line=4325"

    result = verify_financial_claims(
        f"{claim}\n{source}",
        expected_identity=MIDEA_IDENTITY,
        trusted_evidence=(evidence,),
    )

    assert result.allowed is True
    assert result.claims[0].change_direction == "unchanged"


def test_claim_verifier_rejects_wrong_amount_in_metric_continuation_clause():
    evidence = {
        **_trusted_period_goodwill_fact(
            "goodwill_allowance_absolute_change",
            "商誉减值准备变动额",
            "2025-12-31",
            "0",
            "saic-allowance-change-2025",
            ("商誉减值准备变动", "本期持平", "未计提", "绝对变动"),
        ),
        "unit": "元",
        "change_direction": "unchanged",
    }
    source = "[D1] source_type=wiki_document_links task_id=task-midea pdf_page=206 table_index=163 md_line=4325"

    result = verify_financial_claims(
        f"商誉减值准备变动额无新增计提，为 999 元。\n{source}",
        expected_identity=MIDEA_IDENTITY,
        trusted_evidence=(evidence,),
    )

    assert result.allowed is False
    assert result.claims[0].change_direction == "unchanged"
    assert result.violations[0].reason == "value_mismatch"


def test_evidence_recompute_accepts_locator_before_markdown_links():
    source = (
        "[D1] source_type=wiki_document_links task_id=task-midea "
        "pdf_page=206 table_index=163 md_line=4325，"
        "[打开表格](https://example.test/table/163)"
    )

    result = _validate_goodwill_reconciliation(
        "348.13亿元 - 5.56亿元 = 342.57亿元",
        source,
    )

    assert result.allowed is True


@pytest.mark.parametrize(
    "line",
    (
        "348.13亿元 - 5.56亿元 = 999亿元（正确净值342.57亿元）",
        "348.13亿元 - 1亿元 - 5.56亿元 = 342.57亿元",
        "348.13亿元 - 5.56亿元 = 342.57亿元，另列999亿元",
        "34813270亿元 - 556411亿元 = 34256859亿元",
    ),
)
def test_evidence_recompute_rejects_malformed_or_wrong_unit_reconciliation(line: str):
    result = _validate_goodwill_reconciliation(line)

    assert result.allowed is False
    assert result.reason == "trace_unstructured"


@pytest.mark.parametrize(
    "source",
    (
        "[D1] source_type=wiki_document_links task_id=task-midea pdf_page=999 table_index=163 md_line=4325",
        "[D1] source_type=wiki_document_links task_id=task-midea pdf_page=206 table_index=999 md_line=4325",
        "[D1] source_type=wiki_document_links task_id=task-midea pdf_page=206 table_index=163 md_line=9999",
    ),
)
def test_evidence_recompute_rejects_partially_conflicting_source_locator(source: str):
    result = _validate_goodwill_reconciliation(
        "348.13亿元 - 5.56亿元 = 342.57亿元",
        source,
    )

    assert result.allowed is False
    assert result.reason == "trace_input_source_locator_missing"


def test_claim_verifier_accepts_display_precision_rounding_but_rejects_larger_error():
    identity = {
        "market": "CN",
        "company_id": "000333-美的集团",
        "filing_id": "CN:000333-美的集团:2025-annual",
        "parse_run_id": "task-midea",
    }
    evidence = (
        {
            "source_type": "trusted_wiki_table_cell",
            "metric": "goodwill_impairment_allowance",
            "canonical_name": "goodwill_impairment_allowance",
            "metric_name": "商誉减值准备",
            "aliases": ["商誉减值准备", "减值准备"],
            "period": "2025-12-31",
            "period_key": "2025-12-31",
            "value": "556411",
            "raw_value": "556411",
            "unit": "人民币千元",
            "evidence_id": "midea-allowance-2025",
            "quote": "减:减值准备 (556,411)",
            "task_id": "task-midea",
            "pdf_page": 206,
            "table_index": 163,
            **identity,
        },
    )
    source = "[D1] source_type=wiki_document_links task_id=task-midea pdf_page=206 table_index=163"

    rounded = verify_financial_claims(
        f"美的集团 2025 年商誉减值准备为 5.56 亿元。\n{source}",
        expected_identity=identity,
        trusted_evidence=evidence,
    )
    outside_precision = verify_financial_claims(
        f"美的集团 2025 年商誉减值准备为 5.55 亿元。\n{source}",
        expected_identity=identity,
        trusted_evidence=evidence,
    )

    assert rounded.allowed is True
    assert outside_precision.allowed is False
    assert outside_precision.violations[0].reason == "value_mismatch"


def test_claim_verifier_uses_only_half_display_quantum_and_never_skips_the_whole_line():
    evidence = _trusted_goodwill_fact(
        "goodwill_gross",
        "商誉账面原值",
        "34813270",
        "midea-gross-2025",
        ("商誉账面原值", "账面原值", "商誉原值"),
    )
    source = "[D1] source_type=wiki_document_links task_id=task-midea pdf_page=206 table_index=163 md_line=4325"

    rounded = verify_financial_claims(
        f"商誉账面原值为 348.13 亿元。\n{source}",
        expected_identity=MIDEA_IDENTITY,
        trusted_evidence=(evidence,),
    )
    outside_precision = verify_financial_claims(
        f"商誉账面原值为 348.10 亿元。\n{source}",
        expected_identity=MIDEA_IDENTITY,
        trusted_evidence=(evidence,),
        validated_calculation_lines=frozenset({1}),
    )

    assert rounded.allowed is True
    assert outside_precision.allowed is False
    assert outside_precision.violations[0].reason == "value_mismatch"


BYD_IDENTITY = {
    "market": "CN",
    "company_id": "002594-比亚迪",
    "filing_id": "CN:002594-比亚迪:2025-annual",
    "parse_run_id": "task-byd",
}


def _byd_goodwill_evidence() -> dict:
    return {
        "source_type": "trusted_wiki_table_cell",
        "metric": "goodwill",
        "canonical_name": "goodwill",
        "metric_name": "商誉",
        "aliases": ["商誉", "商誉账面净额"],
        "period": "2025-12-31",
        "period_key": "2025-12-31",
        "value": "4427571",
        "raw_value": "4427571",
        "unit": "千元",
        "evidence_id": "byd-goodwill-2025",
        "quote": "商誉 4,427,571",
        "task_id": "task-byd",
        "pdf_page": 123,
        "table_index": 108,
        "md_line": 3014,
        **BYD_IDENTITY,
    }


def _byd_source(*, include_fact: bool = False) -> str:
    fact = (
        " market=CN company_id=002594-比亚迪 filing_id=CN:002594-比亚迪:2025-annual "
        "parse_run_id=task-byd canonical_name=goodwill metric_name=商誉 period=2025-12-31 "
        'value=4427571 unit=千元 evidence_id=byd-goodwill-2025 quote="商誉 4,427,571"'
        if include_fact
        else ""
    )
    return (
        f"[D1] source_type=wiki_metrics{fact} task_id=task-byd "
        "pdf_page=123 table_index=108 md_line=3014"
    )


@pytest.mark.parametrize(("converted", "allowed"), (("44.27571", True), ("442.7571", False)))
def test_claim_verifier_checks_each_cross_unit_restatement_in_same_clause(converted: str, allowed: bool):
    evidence = (_byd_goodwill_evidence(),)
    reply = (
        f"比亚迪 2025 年商誉账面净额为 4,427,571 千元（约 {converted} 亿元）。\n"
        f"{_byd_source()}"
    )

    result = verify_financial_claims(
        reply,
        expected_identity=BYD_IDENTITY,
        trusted_evidence=evidence,
    )

    assert result.allowed is allowed
    assert [(claim.value, claim.unit) for claim in result.claims] == [
        (4427571.0, "千元"),
        (float(converted), "亿"),
    ]
    if not allowed:
        assert result.violations[0].reason == "value_mismatch"
        assert result.violations[0].claimed_value == 442.7571


def test_evidence_recompute_accepts_correct_amount_normalization_and_rejects_tenfold_error():
    evidence = (_byd_goodwill_evidence(),)

    def validate(converted: str):
        reply = (
            f"比亚迪 2025 年商誉账面净额为 4,427,571 千元（约 {converted} 亿元）。\n"
            f"{_byd_source()}"
        )
        return validate_calculation_traces(
            reply,
            expected_identity=BYD_IDENTITY,
            require_calculator=True,
            expected_operations=frozenset({"normalize_amount"}),
            trusted_evidence=evidence,
        )

    correct = validate("44.27571")
    wrong = validate("442.7571")

    assert correct.allowed is True
    assert correct.runs[0]["operation"] == "normalize_amount"
    assert correct.runs[0]["trace_origin"] == "backend_evidence_recompute"
    assert wrong.allowed is False
    assert wrong.reason == "trace_unstructured"


def test_trusted_amount_normalization_receipt_is_bound_and_checks_visible_result():
    evidence = (_byd_goodwill_evidence(),)
    receipt = {
        "status": "ok",
        "operation": "normalize_amount",
        "input": {"value": "4427571", "unit": "千元", "currency": "CNY"},
        "result": {
            "native_base_value": "4427571000",
            "native_100m_value": "44.27571",
            "cny_base_value": "4427571000",
            "cny_100m_value": "44.27571",
        },
    }

    def validate(converted: str):
        reply = (
            f"比亚迪 2025 年商誉账面净额为 4,427,571 千元（约 {converted} 亿元）。\n"
            f"{_byd_source(include_fact=True)}"
        )
        return validate_calculation_traces(
            reply,
            expected_identity=BYD_IDENTITY,
            require_calculator=True,
            expected_operations=frozenset({"normalize_amount"}),
            trusted_runs=(receipt,),
            trusted_evidence=evidence,
        )

    assert validate("44.27571").allowed is True
    wrong = validate("442.7571")
    assert wrong.allowed is False
    assert wrong.reason == "trace_claim_result_mismatch"
