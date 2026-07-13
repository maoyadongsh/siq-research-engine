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
    return ({**allowance, "unit": "元"}, {**flow, "unit": "元"})


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
        _trusted_period_goodwill_fact(
            "goodwill_component_other_absolute_change",
            "其他(i)变动额",
            "2025-12-31",
            "2710278",
            "midea-other-change-2025",
            ("其他(i)变动", "其他(i)同比变动", "本期净增", "绝对变动"),
        ),
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
