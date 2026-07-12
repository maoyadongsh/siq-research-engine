from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any, Mapping, Sequence

CANONICAL_METRIC_ALIASES = {
    "total_operating_revenue": ("营业总收入", "营业收入合计", "total operating revenue"),
    "operating_revenue": ("营业收入", "营收", "operating revenue", "revenue"),
    "revenue": ("营业收入", "营收", "销售收入", "销售额", "revenue", "net sales", "売上高", "매출액"),
    "bank_net_interest_income": ("利息净收入", "净利息收入", "net interest income"),
    "net_interest_income": ("利息净收入", "净利息收入", "net interest income"),
    "net_fee_and_commission_income": ("手续费及佣金净收入", "手续费佣金净收入", "net fee and commission income"),
    "fee_and_commission_income": ("手续费及佣金收入", "手续费佣金收入", "fee and commission income"),
    "non_interest_income": ("非利息收入", "non-interest income", "non interest income"),
    "net_profit": ("净利润", "税后利润", "net profit", "当期純利益", "당기순이익"),
    "net_income": ("净利润", "税后利润", "net income", "当期純利益", "당기순이익"),
    "parent_net_profit": ("归母净利润", "归属于母公司股东的净利润", "母公司股东应占利润"),
    "net_profit_attributable_to_parent": (
        "归母净利润",
        "归属于母公司股东的净利润",
        "母公司股东应占利润",
        "net income attributable",
    ),
    "operating_profit": ("营业利润", "经营利润", "operating profit"),
    "profit_before_tax": ("利润总额", "税前利润", "profit before tax"),
    "gross_profit": ("毛利润", "毛利", "gross profit"),
    "gross_margin": ("毛利率", "gross margin"),
    "net_margin": ("净利率", "净利润率", "net margin"),
    "debt_to_asset_ratio": ("资产负债率", "debt-to-asset ratio", "debt to asset ratio"),
    "net_interest_margin": ("净息差", "净利息收益率", "net interest margin", "NIM"),
    "non_performing_loan_ratio": ("不良贷款率", "不良率", "NPL ratio"),
    "return_on_equity": ("净资产收益率", "股本回报率", "return on equity", "ROE"),
    "return_on_assets": ("总资产收益率", "资产回报率", "return on assets", "ROA"),
    "basic_earnings_per_share": ("基本每股收益", "基本EPS", "basic earnings per share", "basic EPS"),
    "diluted_earnings_per_share": ("稀释每股收益", "稀释EPS", "diluted earnings per share", "diluted EPS"),
    "earnings_per_share": ("每股收益", "EPS", "earnings per share"),
    "total_assets": ("总资产", "资产总计", "total assets"),
    "total_liabilities": ("总负债", "负债合计", "total liabilities"),
    "shareholders_equity": ("股东权益", "所有者权益", "shareholders' equity"),
    "cash_and_cash_equivalents": ("货币资金", "现金及现金等价物", "cash and cash equivalents"),
    "goodwill": ("商誉", "goodwill"),
}
SAFE_SHORT_METRIC_ALIASES = {"营收", "毛利", "商誉"}

UNIT_MULTIPLIERS = {
    "元": ("currency", 1.0),
    "万元": ("currency", 10_000.0),
    "百万元": ("currency", 1_000_000.0),
    "百万": ("currency", 1_000_000.0),
    "亿元": ("currency", 100_000_000.0),
    "亿": ("currency", 100_000_000.0),
    "cny": ("currency", 1.0),
    "rmb": ("currency", 1.0),
    "人民币": ("currency", 1.0),
    "人民币元": ("currency", 1.0),
    "rmb million": ("currency", 1_000_000.0),
    "cny million": ("currency", 1_000_000.0),
    "rmb 百万元": ("currency", 1_000_000.0),
    "人民币百万元": ("currency", 1_000_000.0),
    "hkd": ("currency", 1.0),
    "hk$": ("currency", 1.0),
    "hkd million": ("currency", 1_000_000.0),
    "hk$ million": ("currency", 1_000_000.0),
    "港元": ("currency", 1.0),
    "港币": ("currency", 1.0),
    "港币百万元": ("currency", 1_000_000.0),
    "usd": ("currency", 1.0),
    "us$": ("currency", 1.0),
    "usd million": ("currency", 1_000_000.0),
    "us$ million": ("currency", 1_000_000.0),
    "eur": ("currency", 1.0),
    "eur million": ("currency", 1_000_000.0),
    "gbp": ("currency", 1.0),
    "gbp million": ("currency", 1_000_000.0),
    "£": ("currency", 1.0),
    "£ million": ("currency", 1_000_000.0),
    "英镑": ("currency", 1.0),
    "百万英镑": ("currency", 1_000_000.0),
    "chf": ("currency", 1.0),
    "chf million": ("currency", 1_000_000.0),
    "瑞士法郎": ("currency", 1.0),
    "jpy": ("currency", 1.0),
    "jpy million": ("currency", 1_000_000.0),
    "日元": ("currency", 1.0),
    "百万日元": ("currency", 1_000_000.0),
    "百万円": ("currency", 1_000_000.0),
    "亿日元": ("currency", 100_000_000.0),
    "億円": ("currency", 100_000_000.0),
    "krw": ("currency", 1.0),
    "krw million": ("currency", 1_000_000.0),
    "韩元": ("currency", 1.0),
    "百万韩元": ("currency", 1_000_000.0),
    "백만원": ("currency", 1_000_000.0),
    "亿韩元": ("currency", 100_000_000.0),
    "억원": ("currency", 100_000_000.0),
    "million": ("currency", 1_000_000.0),
    "billion": ("currency", 1_000_000_000.0),
    "thousand": ("currency", 1_000.0),
    "元/股": ("per_share", 1.0),
    "人民币元/股": ("per_share", 1.0),
    "港元/股": ("per_share", 1.0),
    "美元/股": ("per_share", 1.0),
    "rmb/share": ("per_share", 1.0),
    "cny/share": ("per_share", 1.0),
    "hkd/share": ("per_share", 1.0),
    "usd/share": ("per_share", 1.0),
    "gbp/share": ("per_share", 1.0),
    "英镑/股": ("per_share", 1.0),
    "per share": ("per_share", 1.0),
    "%": ("percent", 1.0),
    "％": ("percent", 1.0),
    "pct": ("percent", 1.0),
    "percentage_point": ("percent", 1.0),
    "百分点": ("percent", 1.0),
}

CURRENCY_PREFIX_PATTERN = (
    r"人民币元|人民币|港元|港币|美元|欧元|英镑|瑞士法郎|日元|韩元|"
    r"RMB|CNY|HKD|HK\$|USD|US\$|EUR|GBP|£|CHF|JPY|KRW"
)
NUMBER_WITH_UNIT_RE = re.compile(
    r"(?<![A-Za-z0-9_])"
    rf"(?:(?P<currency>{CURRENCY_PREFIX_PATTERN})\s*)?"
    r"(?P<value>[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)"
    r"\s*(?P<unit>人民币元/股|港元/股|美元/股|英镑/股|元/股|百万日元|百万韩元|百万英镑|百万円|백만원|"
    r"亿日元|亿韩元|億元|億円|억원|万元|百万元|人民币元|港元|港币|美元|欧元|英镑|瑞士法郎|日元|韩元|"
    r"billion|million|thousand|per\s+share|百分点|％|%|亿|元|pct)(?![A-Za-z])",
    re.IGNORECASE,
)
CLAUSE_SPLIT_RE = re.compile(r"[。；;！？!?]|(?<!\d)[,，](?!\d)")
SOURCE_FIELD_START_RE = re.compile(r"(?:(?<=^)|(?<=[\s,，;；|]))([A-Za-z_][A-Za-z0-9_]*)=")
DATE_RE = re.compile(r"\b(?P<year>20\d{2})[-/.年](?P<month>\d{1,2})[-/.月](?P<day>\d{1,2})日?")
QUARTER_RE = re.compile(r"\b(?P<year>20\d{2})\s*(?:Q(?P<q1>[1-4])|年?第?(?P<q2>[一二三四1234])季度)", re.IGNORECASE)
YEAR_RE = re.compile(r"\b(20\d{2})(?:\s*(?:年(?:度|末)?|FY))?\b", re.IGNORECASE)
CHINESE_QUARTER_MAP = {"一": "1", "二": "2", "三": "3", "四": "4"}
CURRENCY_ALIASES = {
    "cny": "CNY",
    "rmb": "CNY",
    "人民币": "CNY",
    "人民币元": "CNY",
    "hkd": "HKD",
    "hk$": "HKD",
    "港元": "HKD",
    "港币": "HKD",
    "usd": "USD",
    "us$": "USD",
    "美元": "USD",
    "美金": "USD",
    "eur": "EUR",
    "欧元": "EUR",
    "gbp": "GBP",
    "£": "GBP",
    "英镑": "GBP",
    "chf": "CHF",
    "瑞士法郎": "CHF",
    "jpy": "JPY",
    "日元": "JPY",
    "krw": "KRW",
    "韩元": "KRW",
}

CALCULATION_TRACE_SCHEMA = "siq_financial_calculation_trace_v1"
RECONCILIATION_TRACE_SCHEMA = "siq_financial_reconciliation_trace_v1"
CALCULATOR_OPERATIONS = frozenset({"yoy", "yoy_growth", "ratio", "cagr", "per_capita"})
RECONCILIATION_OPERATIONS = frozenset({"goodwill_reconciliation", "gross_allowance_net_reconciliation"})
TRACE_SCHEMAS = frozenset({CALCULATION_TRACE_SCHEMA, RECONCILIATION_TRACE_SCHEMA})
TRACE_RESULT_RELATIVE_TOLERANCE = Decimal("0.000001")
TRACE_RESULT_ABSOLUTE_TOLERANCE = Decimal("0.00000001")


@dataclass(frozen=True)
class EvidenceFact:
    metric: str
    value: float
    unit: str
    normalized_value: float
    value_category: str
    aliases: tuple[str, ...]
    currency: str = ""
    period: str = ""
    market: str = ""
    company_id: str = ""
    filing_id: str = ""
    parse_run_id: str = ""
    evidence_id: str = ""
    quote: str = ""
    source_type: str = ""


@dataclass(frozen=True)
class NumericClaim:
    metric: str
    value: float
    unit: str
    normalized_value: float
    value_category: str
    currency: str
    period_tokens: tuple[str, ...]
    period_text: str
    line_number: int
    line: str


@dataclass(frozen=True)
class ClaimViolation:
    reason: str
    metric: str
    line_number: int
    claimed_value: float
    claimed_unit: str
    claimed_currency: str
    claimed_period: str
    evidence_value: float
    evidence_unit: str
    evidence_currency: str
    evidence_id: str
    evidence_quote: str
    period: str
    market: str
    company_id: str
    filing_id: str
    parse_run_id: str
    expected_market: str = ""
    expected_company_id: str = ""
    expected_filing_id: str = ""
    expected_parse_run_id: str = ""


@dataclass(frozen=True)
class ClaimVerificationResult:
    checked: bool
    allowed: bool
    claims: tuple[NumericClaim, ...]
    facts: tuple[EvidenceFact, ...]
    violations: tuple[ClaimViolation, ...]


@dataclass(frozen=True)
class CalculationTraceValidation:
    checked: bool
    allowed: bool
    reason: str = ""
    runs: tuple[Mapping[str, Any], ...] = ()


def _trace_decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _trace_json_objects(reply: str) -> list[dict[str, Any]]:
    decoder = json.JSONDecoder()
    objects: list[dict[str, Any]] = []
    text = reply or ""
    cursor = 0
    while cursor < len(text):
        start = text.find("{", cursor)
        if start < 0:
            break
        try:
            payload, consumed = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            cursor = start + 1
            continue
        cursor = start + consumed
        if isinstance(payload, dict) and str(payload.get("schema_version") or "") in TRACE_SCHEMAS:
            objects.append(payload)
    return objects


def extract_structured_calculation_runs(reply: str) -> tuple[Mapping[str, Any], ...]:
    """Return only versioned, machine-readable calculator/reconciliation envelopes."""
    return tuple(_trace_json_objects(reply))


def _trace_input_records(inputs: Any) -> list[Mapping[str, Any]]:
    if not isinstance(inputs, Mapping):
        return []
    records: list[Mapping[str, Any]] = []
    for value in inputs.values():
        if isinstance(value, Mapping):
            records.append(value)
    return records


def _trace_input_value(inputs: Mapping[str, Any], name: str) -> Decimal | None:
    item = inputs.get(name)
    if isinstance(item, Mapping):
        return _trace_decimal(item.get("value"))
    return None


def _trace_input_scale(item: Mapping[str, Any]) -> Decimal | None:
    normalized = _normalized_amount(item.get("value"), item.get("unit"), scale=item.get("scale"))
    if normalized is None:
        return _trace_decimal(item.get("value"))
    return Decimal(str(normalized[0]))


def _trace_result_value(result: Any) -> Decimal | None:
    if not isinstance(result, Mapping):
        return None
    for key in ("rate", "ratio", "value", "native_per", "net"):
        number = _trace_decimal(result.get(key))
        if number is not None:
            return number
    percent = _trace_decimal(result.get("percent"))
    return percent / Decimal("100") if percent is not None else None


def _trace_result_reason(operation: str, result: Any, expected: Decimal) -> str | None:
    if not isinstance(result, Mapping):
        return "trace_result_missing"
    scalar_keys = {
        "yoy": ("rate",),
        "yoy_growth": ("rate",),
        "ratio": ("ratio",),
        "cagr": ("rate",),
        "per_capita": ("native_per", "value"),
        "goodwill_reconciliation": ("net",),
        "gross_allowance_net_reconciliation": ("net",),
    }.get(operation, ())
    observed = False
    for key in scalar_keys:
        if result.get(key) in (None, ""):
            continue
        observed = True
        value = _trace_decimal(result.get(key))
        if value is None:
            return "trace_result_invalid"
        if not _trace_numbers_close(value, expected):
            return "trace_result_mismatch"
    if operation in {"yoy", "yoy_growth", "ratio", "cagr"} and result.get("percent") not in (None, ""):
        observed = True
        percent = _trace_decimal(result.get("percent"))
        if percent is None:
            return "trace_result_invalid"
        if not _trace_numbers_close(percent / Decimal("100"), expected):
            return "trace_result_mismatch"
    return None if observed else "trace_result_missing"


def _trace_numbers_close(actual: Decimal, expected: Decimal) -> bool:
    tolerance = max(TRACE_RESULT_ABSOLUTE_TOLERANCE, abs(expected) * TRACE_RESULT_RELATIVE_TOLERANCE)
    return abs(actual - expected) <= tolerance


def _trace_expected_result(operation: str, inputs: Mapping[str, Any]) -> Decimal | None:
    if operation in {"yoy", "yoy_growth"}:
        current = _trace_input_scale(inputs.get("current", {})) if isinstance(inputs.get("current"), Mapping) else None
        previous = _trace_input_scale(inputs.get("previous", {})) if isinstance(inputs.get("previous"), Mapping) else None
        if current is None or previous is None or previous == 0:
            return None
        return (current - previous) / abs(previous)
    if operation == "ratio":
        numerator = (
            _trace_input_scale(inputs.get("numerator", {}))
            if isinstance(inputs.get("numerator"), Mapping)
            else None
        )
        denominator = (
            _trace_input_scale(inputs.get("denominator", {}))
            if isinstance(inputs.get("denominator"), Mapping)
            else None
        )
        if numerator is None or denominator is None or denominator == 0:
            return None
        return numerator / denominator
    if operation == "cagr":
        start = _trace_input_scale(inputs.get("start", {})) if isinstance(inputs.get("start"), Mapping) else None
        end = _trace_input_scale(inputs.get("end", {})) if isinstance(inputs.get("end"), Mapping) else None
        periods = _trace_input_value(inputs, "periods")
        if start is None or end is None or periods is None or start <= 0 or end <= 0 or periods <= 0:
            return None
        with localcontext() as context:
            context.prec = 36
            return (end / start) ** (Decimal("1") / periods) - Decimal("1")
    if operation == "per_capita":
        amount = _trace_input_scale(inputs.get("amount", {})) if isinstance(inputs.get("amount"), Mapping) else None
        count = _trace_input_scale(inputs.get("count", {})) if isinstance(inputs.get("count"), Mapping) else None
        if amount is None or count is None or count <= 0:
            return None
        return amount / count
    if operation in RECONCILIATION_OPERATIONS:
        gross_item = inputs.get("gross", {})
        allowance_item = inputs.get("allowance", {})
        net_item = inputs.get("net", {})
        if not all(isinstance(item, Mapping) for item in (gross_item, allowance_item, net_item)):
            return None
        if len({str(item.get("unit") or "").strip().lower() for item in (gross_item, allowance_item, net_item)}) != 1:
            return None
        gross = _trace_decimal(gross_item.get("value"))
        allowance = _trace_decimal(allowance_item.get("value"))
        net = _trace_decimal(net_item.get("value"))
        if gross is None or allowance is None or net is None:
            return None
        expected = gross - allowance
        return expected if _trace_numbers_close(net, expected) else None
    return None


def _trace_comparable_input_reason(operation: str, inputs: Mapping[str, Any]) -> str | None:
    names = {
        "yoy": ("current", "previous"),
        "yoy_growth": ("current", "previous"),
        "ratio": ("numerator", "denominator"),
        "cagr": ("start", "end"),
    }.get(operation)
    if not names:
        return None
    items = [inputs.get(name) for name in names]
    if not all(isinstance(item, Mapping) for item in items):
        return "trace_inputs_missing"
    units = [str(item.get("unit") or "").strip() for item in items]
    normalized = [_unit_multiplier(unit) for unit in units]
    if any(item is None for item in normalized):
        return "trace_input_unit_invalid"
    if len({item[0] for item in normalized if item is not None}) != 1:
        return "trace_input_unit_mismatch"
    currencies = [_currency_token(item.get("currency"), item.get("unit")) for item in items]
    if len({currency for currency in currencies if currency}) > 1:
        return "trace_input_currency_mismatch"
    if any(currencies) and not all(currencies):
        return "trace_input_currency_mismatch"
    return None


def _trace_identity_reason(payload: Mapping[str, Any], expected: Mapping[str, str]) -> str | None:
    identity = payload.get("research_identity")
    if not isinstance(identity, Mapping):
        return "trace_identity_missing"
    for field in IDENTITY_FIELDS:
        actual = _normalized_identity_value(field, identity.get(field))
        if not actual:
            return f"trace_identity_missing_{field}"
        if expected and actual != expected[field]:
            return f"trace_identity_{field}_mismatch"
    return None


def _trace_evidence_reason(payload: Mapping[str, Any], reply: str) -> str | None:
    inputs = payload.get("inputs")
    if not isinstance(inputs, Mapping) or not inputs:
        return "trace_inputs_missing"
    references = _extract_source_references(reply)
    trace_identity = payload.get("research_identity") if isinstance(payload.get("research_identity"), Mapping) else {}
    output_period_tokens = set(_period_tokens(payload.get("period")))
    input_period_tokens: set[str] = set()
    input_metrics: list[str] = []
    for item in _trace_input_records(inputs):
        # periods is a mathematical scalar, not a report fact.
        role = str(item.get("role") or "")
        if role == "period_count":
            if _trace_decimal(item.get("value")) is None:
                return "trace_input_invalid"
            continue
        required = ("metric", "period", "value", "unit", "evidence_id")
        if any(item.get(field) in (None, "") for field in required):
            return "trace_input_fields_missing"
        item_value = _trace_decimal(item.get("value"))
        if item_value is None:
            return "trace_input_invalid"
        matches = [
            reference
            for reference in references
            if str(reference.get("evidence_id") or "") == str(item.get("evidence_id") or "")
        ]
        if not matches:
            return "trace_input_evidence_missing"
        reference = matches[0]
        input_metrics.append(str(item.get("metric") or "").strip().lower())
        input_period_tokens.update(_period_tokens(item.get("period")))
        for field in IDENTITY_FIELDS:
            if _normalized_identity_value(field, reference.get(field)) != _normalized_identity_value(
                field, trace_identity.get(field)
            ):
                return f"trace_input_{field}_mismatch"
        aliases = {str(reference.get(key) or "").strip().lower() for key in ("metric", "metric_name", "canonical_name")}
        if str(item.get("metric") or "").strip().lower() not in aliases:
            return "trace_input_metric_mismatch"
        if not set(_period_tokens(item.get("period"))).intersection(_period_tokens(reference.get("period_key") or reference.get("period"))):
            return "trace_input_period_mismatch"
        reference_value = _trace_decimal(reference.get("value", reference.get("raw_value")))
        if reference_value is None or not _trace_numbers_close(item_value, reference_value):
            return "trace_input_value_mismatch"
    if not output_period_tokens or not output_period_tokens.intersection(input_period_tokens):
        return "trace_period_mismatch"
    operation = str(payload.get("operation") or "").strip().lower()
    output_metric = str(payload.get("metric") or "").strip().lower()
    metrics_by_role = {
        str(role): str(item.get("metric") or "").strip().lower()
        for role, item in inputs.items()
        if isinstance(item, Mapping) and str(item.get("role") or "") != "period_count"
    }
    if operation in {"yoy", "yoy_growth", "cagr", "per_capita"} and not any(
        metric and metric in output_metric for metric in input_metrics
    ):
        return "trace_metric_mismatch"
    if operation in {"yoy", "yoy_growth"} and metrics_by_role.get("current") != metrics_by_role.get("previous"):
        return "trace_input_metric_mismatch"
    if operation == "cagr" and metrics_by_role.get("start") != metrics_by_role.get("end"):
        return "trace_input_metric_mismatch"
    ratio_roles = {
        "gross_margin": (("gross_profit",), ("revenue", "operating_revenue", "total_operating_revenue")),
        "net_margin": (("net_profit", "net_income", "parent_net_profit"), ("revenue", "operating_revenue")),
        "debt_to_asset_ratio": (("total_liabilities",), ("total_assets",)),
        "return_on_equity": (("net_profit", "net_income", "parent_net_profit"), ("shareholders_equity",)),
        "return_on_assets": (("net_profit", "net_income", "parent_net_profit"), ("total_assets",)),
    }
    if operation == "ratio" and output_metric in ratio_roles:
        numerator_metrics, denominator_metrics = ratio_roles[output_metric]
        if metrics_by_role.get("numerator") not in numerator_metrics:
            return "trace_input_metric_mismatch"
        if metrics_by_role.get("denominator") not in denominator_metrics:
            return "trace_input_metric_mismatch"
    if operation in RECONCILIATION_OPERATIONS:
        expected_roles = {
            "gross": ("gross", "original", "cost"),
            "allowance": ("allowance", "impairment", "provision"),
            "net": ("net", "carrying"),
        }
        for role, aliases in expected_roles.items():
            if not any(alias in metrics_by_role.get(role, "") for alias in aliases):
                return "trace_input_metric_mismatch"
    return None


DERIVED_PERCENT_CLAIM_RE = re.compile(r"(?P<value>[+-]?\d+(?:\.\d+)?)\s*[%％]")
DERIVED_PERCENT_TERMS = (
    "同比",
    "环比",
    "增长率",
    "增速",
    "占比",
    "毛利率",
    "净利率",
    "资产负债率",
    "收益率",
    "回报率",
    "净息差",
    "cagr",
)
DERIVED_METRIC_REPLY_ALIASES = {
    "gross_margin": ("毛利率", "gross margin"),
    "net_margin": ("净利率", "净利润率", "net margin"),
    "debt_to_asset_ratio": ("资产负债率", "debt-to-asset ratio", "debt to asset ratio"),
    "return_on_equity": ("净资产收益率", "股本回报率", "roe", "return on equity"),
    "return_on_assets": ("总资产收益率", "资产回报率", "roa", "return on assets"),
    "net_interest_margin": ("净息差", "净利息收益率", "nim", "net interest margin"),
}


def _derived_percent_claims(reply: str) -> tuple[Decimal, ...]:
    claims: list[Decimal] = []
    for line in (reply or "").splitlines():
        lowered = line.lower()
        if "source_type=" in line or "schema_version" in line:
            continue
        if not any(term in lowered for term in DERIVED_PERCENT_TERMS):
            continue
        for match in DERIVED_PERCENT_CLAIM_RE.finditer(line):
            value = _trace_decimal(match.group("value"))
            if value is not None:
                claims.append(value / Decimal("100"))
    return tuple(claims)


def _expected_trace_metrics(reply: str) -> frozenset[str]:
    prose = "\n".join(
        line
        for line in (reply or "").splitlines()
        if "source_type=" not in line and "schema_version" not in line
    ).lower()
    return frozenset(
        metric
        for metric, aliases in DERIVED_METRIC_REPLY_ALIASES.items()
        if any(alias in prose for alias in aliases)
    )


def _trace_reference_metric(reference: Mapping[str, Any]) -> str:
    return str(
        reference.get("canonical_name")
        or reference.get("metric_name")
        or reference.get("metric")
        or reference.get("name")
        or ""
    ).strip()


def _trace_reference_period(reference: Mapping[str, Any]) -> str:
    return str(reference.get("period_key") or reference.get("period") or "").strip()


def _trace_reference_for_value(
    value: Any,
    unit: Any,
    references: Sequence[Mapping[str, Any]],
    used: set[str],
) -> Mapping[str, Any] | None:
    normalized = _normalized_amount(value, unit)
    if normalized is None:
        return None
    target, category = normalized
    candidates: list[Mapping[str, Any]] = []
    for reference in references:
        evidence_id = str(reference.get("evidence_id") or "")
        if not evidence_id or evidence_id in used:
            continue
        reference_value = reference.get("value", reference.get("raw_value"))
        reference_unit = reference.get("unit") or reference.get("currency") or reference.get("fact_currency")
        observed = _normalized_amount(reference_value, reference_unit, scale=reference.get("scale"))
        if observed is None or observed[1] != category:
            continue
        if abs(observed[0] - target) <= max(0.01, abs(target) * 0.000001):
            candidates.append(reference)
    return candidates[0] if len(candidates) == 1 else None


def _trace_identity_payload(expected_identity: Mapping[str, Any] | None) -> dict[str, str]:
    expected = _expected_identity(expected_identity)
    if not expected:
        return {}
    return {field: expected[field] for field in IDENTITY_FIELDS}


def _ratio_trace_metric(inputs: Mapping[str, Any]) -> str:
    numerator = str(inputs.get("numerator", {}).get("metric") or "").strip().lower()
    denominator = str(inputs.get("denominator", {}).get("metric") or "").strip().lower()
    if numerator in {"gross_profit", "gross_income", "毛利润", "毛利"} and denominator in {
        "revenue",
        "operating_revenue",
        "total_operating_revenue",
        "营业收入",
        "营业总收入",
    }:
        return "gross_margin"
    if numerator in {"net_profit", "net_income", "parent_net_profit", "净利润", "归母净利润"} and denominator in {
        "revenue",
        "operating_revenue",
        "营业收入",
    }:
        return "net_margin"
    if numerator in {"total_liabilities", "liabilities", "负债合计", "总负债"} and denominator in {
        "total_assets",
        "assets",
        "资产总计",
        "总资产",
    }:
        return "debt_to_asset_ratio"
    return f"{numerator}_ratio"


def materialize_runtime_calculation_runs(
    receipts: Sequence[Mapping[str, Any]],
    reply: str,
    *,
    expected_identity: Mapping[str, Any] | None = None,
) -> tuple[Mapping[str, Any], ...]:
    """Turn a trusted CLI receipt into the strict trace envelope used by the verifier.

    The receipt contains only script output.  Metric, period and evidence IDs
    are bound here to source lines already present in the guarded reply; no
    model-authored identity or evidence fields are accepted.
    """

    references = _extract_source_references(reply)
    identity = _trace_identity_payload(expected_identity)
    materialized: list[Mapping[str, Any]] = []
    for receipt in receipts:
        schema = str(receipt.get("schema_version") or "")
        if schema in TRACE_SCHEMAS:
            materialized.append(receipt)
            continue
        operation = str(receipt.get("operation") or "").strip().lower()
        if operation not in CALCULATOR_OPERATIONS and operation not in RECONCILIATION_OPERATIONS:
            continue
        status = str(receipt.get("status") or "").strip().lower()
        if status not in {"ok", "pass", "passed"}:
            continue
        used: set[str] = set()
        if operation in CALCULATOR_OPERATIONS:
            raw_input = receipt.get("input")
            if not isinstance(raw_input, Mapping):
                continue
            role_specs = {
                "yoy": (("current", "current_unit"), ("previous", "previous_unit")),
                "yoy_growth": (("current", "current_unit"), ("previous", "previous_unit")),
                "ratio": (("numerator", "numerator_unit"), ("denominator", "denominator_unit")),
                "cagr": (("start", "start_unit"), ("end", "end_unit")),
                "per_capita": (("amount", "amount_unit"), ("count", "count_unit")),
            }.get(operation, ())
            inputs: dict[str, Any] = {}
            periods: list[str] = []
            for role, unit_key in role_specs:
                value_key, unit_name_key = role, unit_key
                reference = _trace_reference_for_value(
                    raw_input.get(value_key),
                    raw_input.get(unit_name_key),
                    references,
                    used,
                )
                if reference is None:
                    continue
                evidence_id = str(reference.get("evidence_id") or "")
                used.add(evidence_id)
                period = _trace_reference_period(reference)
                periods.append(period)
                inputs[role] = {
                    "role": role,
                    "metric": _trace_reference_metric(reference),
                    "period": period,
                    "value": str(raw_input.get(value_key)),
                    "unit": str(raw_input.get(unit_name_key) or ""),
                    "evidence_id": evidence_id,
                }
            if len(inputs) != len(role_specs):
                continue
            if operation == "ratio":
                metric = _ratio_trace_metric(inputs)
            else:
                metric = inputs[next(iter(inputs))]["metric"]
            trace = {
                "schema_version": CALCULATION_TRACE_SCHEMA,
                "tool": "financial_calculator.py",
                "operation": operation,
                "metric": metric,
                "period": periods[0] if periods else "",
                "inputs": inputs,
                "result": receipt.get("result"),
                "research_identity": identity,
                "receipt": {key: receipt[key] for key in receipt if key.startswith("receipt_")},
            }
            materialized.append(trace)
            continue

        result = receipt.get("result")
        if not isinstance(result, Mapping):
            continue
        reconciliation_roles = (
            ("gross", "note_gross"),
            ("allowance", "impairment_allowance"),
            ("net", "statement_net"),
        )
        inputs: dict[str, Any] = {}
        periods: list[str] = []
        for role, result_key in reconciliation_roles:
            reference = _trace_reference_for_value(result.get(result_key), "元", references, used)
            if reference is None:
                continue
            evidence_id = str(reference.get("evidence_id") or "")
            used.add(evidence_id)
            period = _trace_reference_period(reference)
            periods.append(period)
            inputs[role] = {
                "role": role,
                "metric": _trace_reference_metric(reference),
                "period": period,
                "value": str(result.get(result_key)),
                "unit": str(reference.get("unit") or "元"),
                "evidence_id": evidence_id,
            }
        if len(inputs) != len(reconciliation_roles):
            continue
        materialized.append(
            {
                "schema_version": RECONCILIATION_TRACE_SCHEMA,
                "tool": "financial_reconciliation_validator.py",
                "operation": operation,
                "metric": "goodwill",
                "period": periods[0] if periods else str(receipt.get("report_id") or ""),
                "inputs": inputs,
                "result": {"net": str(result.get("statement_net"))},
                "status": status,
                "research_identity": identity,
                "receipt": {key: receipt[key] for key in receipt if key.startswith("receipt_")},
            }
        )
    return tuple(materialized)


def validate_calculation_traces(
    reply: str,
    *,
    expected_identity: Mapping[str, Any] | None = None,
    require_calculator: bool = False,
    require_reconciliation: bool = False,
    expected_operations: frozenset[str] = frozenset(),
    trusted_runs: Sequence[Mapping[str, Any]] = (),
) -> CalculationTraceValidation:
    materialized_trusted_runs = materialize_runtime_calculation_runs(
        trusted_runs,
        reply,
        expected_identity=expected_identity,
    )
    runs = extract_structured_calculation_runs(reply) + tuple(materialized_trusted_runs)
    if not (require_calculator or require_reconciliation):
        return CalculationTraceValidation(checked=False, allowed=True, runs=runs)
    if not runs:
        return CalculationTraceValidation(checked=True, allowed=False, reason="trace_unstructured")
    expected = _expected_identity(expected_identity)
    calculator_seen = False
    reconciliation_seen = False
    seen_operations: set[str] = set()
    calculator_results: list[Decimal] = []
    expected_metrics = _expected_trace_metrics(reply)
    for run in runs:
        schema = str(run.get("schema_version") or "")
        tool = str(run.get("tool") or "")
        operation = str(run.get("operation") or "").strip().lower()
        metric = str(run.get("metric") or "").strip()
        period = str(run.get("period") or "").strip()
        if not operation or not metric or not period:
            return CalculationTraceValidation(True, False, "trace_fields_missing", runs)
        if expected_metrics and metric.lower() not in expected_metrics:
            return CalculationTraceValidation(True, False, "trace_metric_mismatch", runs)
        is_reconciliation = schema == RECONCILIATION_TRACE_SCHEMA
        allowed_operations = RECONCILIATION_OPERATIONS if is_reconciliation else CALCULATOR_OPERATIONS
        expected_tool = "financial_reconciliation_validator.py" if is_reconciliation else "financial_calculator.py"
        if operation not in allowed_operations:
            return CalculationTraceValidation(True, False, "trace_unknown_operation", runs)
        if not is_reconciliation and expected_operations and operation not in expected_operations:
            return CalculationTraceValidation(True, False, "trace_operation_mismatch", runs)
        if tool != expected_tool:
            return CalculationTraceValidation(True, False, "trace_tool_mismatch", runs)
        identity_reason = _trace_identity_reason(run, expected)
        if identity_reason:
            return CalculationTraceValidation(True, False, identity_reason, runs)
        evidence_reason = _trace_evidence_reason(run, reply)
        if evidence_reason:
            return CalculationTraceValidation(True, False, evidence_reason, runs)
        inputs = run.get("inputs", {})
        comparable_reason = _trace_comparable_input_reason(operation, inputs)
        if comparable_reason:
            return CalculationTraceValidation(True, False, comparable_reason, runs)
        expected_result = _trace_expected_result(operation, inputs)
        actual_result = _trace_result_value(run.get("result"))
        if expected_result is None:
            return CalculationTraceValidation(True, False, "trace_result_missing", runs)
        result_reason = _trace_result_reason(operation, run.get("result"), expected_result)
        if result_reason:
            return CalculationTraceValidation(True, False, result_reason, runs)
        if actual_result is None:
            return CalculationTraceValidation(True, False, "trace_result_missing", runs)
        if operation in RECONCILIATION_OPERATIONS:
            status = str(run.get("status") or "").strip().lower()
            if status not in {"pass", "passed", "ok"}:
                return CalculationTraceValidation(True, False, "trace_reconciliation_status_invalid", runs)
        calculator_seen = calculator_seen or not is_reconciliation
        reconciliation_seen = reconciliation_seen or is_reconciliation
        seen_operations.add(operation)
        if not is_reconciliation:
            calculator_results.append(actual_result)
    if require_calculator and not calculator_seen:
        return CalculationTraceValidation(True, False, "calculator_trace_missing", runs)
    if require_reconciliation and not reconciliation_seen:
        return CalculationTraceValidation(True, False, "reconciliation_trace_missing", runs)
    if expected_operations:
        covered_operations = set(seen_operations)
        if "yoy" in covered_operations or "yoy_growth" in covered_operations:
            covered_operations.update({"yoy", "yoy_growth"})
        if set(expected_operations) - covered_operations:
            return CalculationTraceValidation(True, False, "trace_operation_missing", runs)
    for claim in _derived_percent_claims(reply):
        # A prose percentage is commonly rounded to one decimal place.  This
        # tolerance is only for binding the displayed claim to an already
        # strictly recomputed trace result; the trace itself remains 1 ppm.
        if not any(abs(claim - result) <= Decimal("0.0005") for result in calculator_results):
            return CalculationTraceValidation(True, False, "trace_claim_result_mismatch", runs)
    return CalculationTraceValidation(True, True, runs=runs)


def _clean_number(value: Any) -> float | None:
    text = str(value or "").strip().replace(",", "")
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    if not math.isfinite(number):
        return None
    return number


def _clean_positive_scale(value: Any) -> float | None:
    number = _clean_number(value)
    if number is None or number <= 0:
        return None
    return number


def _unit_multiplier(unit: Any) -> tuple[str, float] | None:
    normalized = str(unit or "").strip().lower()
    if normalized in UNIT_MULTIPLIERS:
        return UNIT_MULTIPLIERS[normalized]
    if normalized.startswith("iso4217:") and _currency_token(normalized):
        return "currency", 1.0
    return None


def _unit_is_base_currency(unit: Any) -> bool:
    normalized = str(unit or "").strip().lower()
    return normalized.startswith("iso4217:") or normalized in {
        "元",
        "cny",
        "rmb",
        "人民币",
        "人民币元",
        "hkd",
        "hk$",
        "港元",
        "港币",
        "usd",
        "us$",
        "eur",
        "gbp",
        "£",
        "英镑",
        "chf",
        "瑞士法郎",
        "jpy",
        "日元",
        "krw",
        "韩元",
    }


def _looks_like_currency_unit(unit: Any) -> bool:
    normalized = str(unit or "").strip().lower()
    if not normalized:
        return False
    return any(
        token in normalized
        for token in (
            "元",
            "円",
            "원",
            "rmb",
            "cny",
            "hkd",
            "hk$",
            "港币",
            "港元",
            "usd",
            "us$",
            "eur",
            "gbp",
            "£",
            "英镑",
            "chf",
            "瑞士法郎",
            "jpy",
            "krw",
            "million",
            "billion",
            "thousand",
            "百万",
            "亿",
            "億",
            "백만",
            "억",
        )
    )


def _currency_token(*values: Any) -> str:
    text = " ".join(str(value or "") for value in values).lower()
    for alias, token in CURRENCY_ALIASES.items():
        if alias.lower() in text:
            return token
    return ""


def _normalized_amount(value: Any, unit: Any, *, scale: Any = None) -> tuple[float, str] | None:
    number = _clean_number(value)
    multiplier = _unit_multiplier(unit)
    explicit_scale = _clean_positive_scale(scale)
    if number is None:
        return None
    if multiplier is not None:
        category, unit_scale = multiplier
        if category == "currency" and explicit_scale is not None and _unit_is_base_currency(unit):
            unit_scale *= explicit_scale
        return number * unit_scale, category
    if explicit_scale is not None and _looks_like_currency_unit(unit):
        return number * explicit_scale, "currency"
    return None


def _period_tokens(text: Any) -> tuple[str, ...]:
    raw = str(text or "")
    tokens: list[str] = []

    for match in DATE_RE.finditer(raw):
        year = int(match.group("year"))
        month = int(match.group("month"))
        day = int(match.group("day"))
        if 1 <= month <= 12 and 1 <= day <= 31:
            tokens.append(f"{year:04d}-{month:02d}-{day:02d}")
            tokens.append(f"{year:04d}")

    for match in QUARTER_RE.finditer(raw):
        quarter = match.group("q1") or CHINESE_QUARTER_MAP.get(match.group("q2") or "")
        if quarter:
            tokens.append(f"{int(match.group('year')):04d}Q{quarter}")
            tokens.append(f"{int(match.group('year')):04d}")

    for match in YEAR_RE.finditer(raw):
        tokens.append(match.group(1))

    seen: set[str] = set()
    result: list[str] = []
    for token in tokens:
        if token not in seen:
            seen.add(token)
            result.append(token)
    return tuple(result)


def _period_text(tokens: tuple[str, ...]) -> str:
    return ",".join(tokens)


def _metric_aliases(fact: Mapping[str, Any]) -> tuple[str, ...]:
    aliases: list[str] = []
    for key in ("metric_name", "metric", "canonical_name", "name", "concept", "label"):
        value = str(fact.get(key) or "").strip()
        if value:
            aliases.append(value)
        canonical = re.sub(r"[^a-z0-9_]+", "_", value.lower()).strip("_")
        aliases.extend(CANONICAL_METRIC_ALIASES.get(canonical, ()))
    compact_seen: set[str] = set()
    result: list[str] = []
    for alias in aliases:
        normalized = alias.strip()
        compact = re.sub(r"\s+", "", normalized.lower())
        if (
            not normalized
            or (len(compact) < 3 and compact not in SAFE_SHORT_METRIC_ALIASES)
            or compact in compact_seen
        ):
            continue
        compact_seen.add(compact)
        result.append(normalized)
    return tuple(result)


def _extract_source_fields(raw_line: str) -> dict[str, str]:
    matches = list(SOURCE_FIELD_START_RE.finditer(raw_line or ""))
    fields: dict[str, str] = {}
    for index, match in enumerate(matches):
        key = match.group(1).strip()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(raw_line)
        value = raw_line[start:end].strip().strip(" \t,，;；|。")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1].strip()
        if value:
            fields[key] = value
    return fields


def _extract_source_references(reply: str) -> list[dict[str, Any]]:
    references: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate((reply or "").splitlines(), start=1):
        if "source_type=" not in raw_line:
            continue
        fields = _extract_source_fields(raw_line)
        if not fields.get("source_type"):
            continue
        reference = {"line_number": line_number, "raw": raw_line.strip(), **fields}
        if not reference.get("evidence_id"):
            stable_fields = "|".join(
                str(reference.get(key) or "")
                for key in (
                    "source_type", "market", "company_id", "filing_id", "parse_run_id",
                    "file", "metric", "metric_name", "canonical_name", "period", "period_key",
                    "value", "raw_value", "unit", "task_id", "pdf_page", "table_index", "md_line",
                )
            )
            reference["evidence_id"] = "auto:" + hashlib.sha256(stable_fields.encode("utf-8")).hexdigest()[:20]
            reference["_generated_evidence_id"] = True
        references.append(reference)
        if len(references) >= 100:
            break
    return references


def _fact_from_reference(reference: Mapping[str, Any]) -> dict[str, Any]:
    preferred_keys = (
        "source_type",
        "market",
        "company_id",
        "filing_id",
        "parse_run_id",
        "report_id",
        "metric",
        "metric_name",
        "canonical_name",
        "name",
        "concept",
        "period",
        "period_key",
        "value",
        "raw_value",
        "unit",
        "currency",
        "scale",
        "fact_currency",
        "task_id",
        "evidence_id",
        "_generated_evidence_id",
        "quote",
        "quote_text",
        "source_url",
        "line_number",
    )
    fact = {key: reference[key] for key in preferred_keys if key in reference and reference[key] not in (None, "")}
    if "metric_name" not in fact:
        for key in ("metric", "name", "concept"):
            if fact.get(key):
                fact["metric_name"] = fact[key]
                break
    return fact


def _reference_facts(reply: str) -> tuple[EvidenceFact, ...]:
    facts: list[EvidenceFact] = []
    for reference in _extract_source_references(reply):
        source_type = str(reference.get("source_type") or "")
        if not (source_type.startswith("wiki") or source_type.startswith("postgres") or source_type == "postgresql"):
            continue
        fact = _fact_from_reference(reference)
        value = fact.get("value", fact.get("raw_value"))
        unit = str(fact.get("unit") or fact.get("currency") or fact.get("fact_currency") or "").strip()
        normalized = _normalized_amount(value, unit, scale=fact.get("scale"))
        if normalized is None:
            continue
        normalized_value, category = normalized
        aliases = _metric_aliases(fact)
        if not aliases:
            continue
        metric = str(fact.get("canonical_name") or fact.get("metric_name") or fact.get("metric") or aliases[0]).strip()
        facts.append(
            EvidenceFact(
                metric=metric,
                value=float(_clean_number(value) or 0.0),
                unit=unit,
                normalized_value=normalized_value,
                value_category=category,
                aliases=aliases,
                currency=_currency_token(fact.get("currency"), fact.get("fact_currency"), unit),
                period=str(fact.get("period_key") or fact.get("period") or ""),
                market=str(fact.get("market") or "").strip().upper(),
                company_id=str(fact.get("company_id") or ""),
                filing_id=str(fact.get("filing_id") or fact.get("report_id") or ""),
                parse_run_id=str(fact.get("parse_run_id") or ""),
                evidence_id="" if fact.get("_generated_evidence_id") else str(fact.get("evidence_id") or ""),
                quote=str(fact.get("quote") or fact.get("quote_text") or ""),
                source_type=source_type,
            )
        )
    return tuple(facts)


def _claim_clauses(line: str) -> tuple[str, ...]:
    clauses = tuple(part.strip() for part in CLAUSE_SPLIT_RE.split(line) if part.strip())
    return clauses or (line,)


def _alias_match_score(clause: str, amount_start: int, fact: EvidenceFact) -> tuple[int, int, int] | None:
    compact_clause = re.sub(r"\s+", "", clause.lower())
    compact_amount_start = len(re.sub(r"\s+", "", clause[:amount_start]))
    best: tuple[int, int, int] | None = None
    for alias in fact.aliases:
        compact_alias = re.sub(r"\s+", "", alias.lower())
        if not compact_alias:
            continue
        start = compact_clause.find(compact_alias)
        while start >= 0:
            end = start + len(compact_alias)
            if end <= compact_amount_start:
                score = (0, compact_amount_start - end, -len(compact_alias))
            else:
                score = (1, start - compact_amount_start, -len(compact_alias))
            if best is None or score < best:
                best = score
            start = compact_clause.find(compact_alias, start + 1)
    return best


def _fact_for_amount(
    clause: str,
    amount_start: int,
    category: str,
    facts: tuple[EvidenceFact, ...],
) -> EvidenceFact | None:
    candidates: list[tuple[tuple[int, int, int], int, EvidenceFact]] = []
    for index, fact in enumerate(facts):
        if fact.value_category != category:
            continue
        score = _alias_match_score(clause, amount_start, fact)
        if score is not None:
            candidates.append((score, index, fact))
    if not candidates:
        return None
    return min(candidates, key=lambda item: (item[0], item[1]))[2]


def _parallel_fact_assignments(
    clause: str,
    matches: list[re.Match[str]],
    facts: tuple[EvidenceFact, ...],
) -> tuple[EvidenceFact, ...] | None:
    marker = clause.find("分别")
    if marker < 0 or not matches or any(match.start() <= marker for match in matches):
        return None
    mentions: list[tuple[int, int, int, EvidenceFact]] = []
    prefix = clause[:marker].lower()
    for fact_index, fact in enumerate(facts):
        best: tuple[int, int, int, EvidenceFact] | None = None
        for alias in fact.aliases:
            pattern = r"\s*".join(re.escape(part) for part in alias.lower().split())
            if not pattern:
                continue
            for alias_match in re.finditer(pattern, prefix):
                candidate = (alias_match.start(), -len(alias_match.group(0)), fact_index, fact)
                if best is None or candidate[:3] < best[:3]:
                    best = candidate
        if best is not None:
            mentions.append(best)
    mentions.sort(key=lambda item: item[:3])
    ordered_facts: list[EvidenceFact] = []
    seen_metrics: set[str] = set()
    for _start, _length, _index, fact in mentions:
        if fact.metric in seen_metrics:
            continue
        seen_metrics.add(fact.metric)
        ordered_facts.append(fact)
    if len(ordered_facts) != len(matches):
        return None
    for fact, match in zip(ordered_facts, matches, strict=True):
        normalized = _normalized_amount(match.group("value"), match.group("unit"))
        if normalized is None or normalized[1] != fact.value_category:
            return None
    return tuple(ordered_facts)


def _extract_claims(reply: str, facts: tuple[EvidenceFact, ...]) -> tuple[NumericClaim, ...]:
    claims: list[NumericClaim] = []
    seen: set[tuple[int, str, float, str]] = set()
    for line_number, raw_line in enumerate((reply or "").splitlines(), start=1):
        line = raw_line.strip()
        if not line or "source_type=" in line or line.startswith("guardrail_") or line.startswith("claim_verifier_"):
            continue
        line_period_tokens = _period_tokens(line)
        for clause in _claim_clauses(line):
            clause_period_tokens = _period_tokens(clause) or line_period_tokens
            matches = list(NUMBER_WITH_UNIT_RE.finditer(clause))
            parallel_facts = _parallel_fact_assignments(clause, matches, facts)
            for match_index, match in enumerate(matches):
                value = _clean_number(match.group("value"))
                unit = match.group("unit")
                normalized = _normalized_amount(value, unit)
                if normalized is None:
                    continue
                normalized_value, category = normalized
                fact = parallel_facts[match_index] if parallel_facts is not None else None
                if fact is None:
                    fact = _fact_for_amount(clause, match.start(), category, facts)
                if fact is None:
                    continue
                key = (line_number, fact.metric, normalized_value, unit)
                if key in seen:
                    continue
                seen.add(key)
                claims.append(
                    NumericClaim(
                        metric=fact.metric,
                        value=float(value or 0.0),
                        unit=unit,
                        normalized_value=normalized_value,
                        value_category=category,
                        currency=_currency_token(match.group("currency"), clause, line),
                        period_tokens=clause_period_tokens,
                        period_text=_period_text(clause_period_tokens),
                        line_number=line_number,
                        line=line,
                    )
                )
    return tuple(claims)


def _matches_evidence(claim: NumericClaim, fact: EvidenceFact) -> bool:
    if claim.metric != fact.metric or claim.value_category != fact.value_category:
        return False
    if not fact.company_id or not fact.filing_id:
        return False
    if not fact.evidence_id or not fact.quote:
        return False
    if claim.currency and fact.currency and claim.currency != fact.currency:
        return False
    if claim.period_tokens:
        fact_tokens = _period_tokens(fact.period)
        if fact_tokens and not set(claim.period_tokens).intersection(fact_tokens):
            return False
    tolerance = max(0.01, abs(fact.normalized_value) * 0.0001)
    return abs(claim.normalized_value - fact.normalized_value) <= tolerance


def _violation_reason(claim: NumericClaim, fact: EvidenceFact) -> str:
    if not fact.company_id:
        return "missing_company_id"
    if not fact.filing_id:
        return "missing_filing_id"
    if not fact.evidence_id:
        return "missing_evidence_id"
    if not fact.quote:
        return "missing_quote"
    if claim.currency and fact.currency and claim.currency != fact.currency:
        return "currency_mismatch"
    if claim.period_tokens:
        fact_tokens = _period_tokens(fact.period)
        if fact_tokens and not set(claim.period_tokens).intersection(fact_tokens):
            return "period_mismatch"
    tolerance = max(0.01, abs(fact.normalized_value) * 0.0001)
    if abs(claim.normalized_value - fact.normalized_value) > tolerance:
        return "value_mismatch"
    return "claim_mismatch"


def _expected_identity(identity: Mapping[str, Any] | None) -> dict[str, str]:
    if not identity:
        return {}
    normalized = {field: _normalized_identity_value(field, identity.get(field)) for field in IDENTITY_FIELDS}
    return normalized if all(normalized.values()) else {}


IDENTITY_FIELDS = ("market", "company_id", "filing_id", "parse_run_id")


def _normalized_identity_value(field: str, value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if field == "market":
        return "US" if text.upper() in {"US", "US_SEC", "US-SEC"} else text.upper()
    if field in {"company_id", "filing_id"} and ":" in text:
        market, suffix = text.split(":", 1)
        normalized_market = "US" if market.upper() in {"US", "US_SEC", "US-SEC"} else market.upper()
        if field == "company_id" and normalized_market == "US" and suffix.upper().startswith("CIK"):
            suffix = suffix[3:]
        return f"{normalized_market}:{suffix}"
    return text


def _identity_violation_reason(actual: Mapping[str, Any], expected: Mapping[str, str]) -> str | None:
    for field in IDENTITY_FIELDS:
        actual_value = _normalized_identity_value(field, actual.get(field))
        if not actual_value:
            return f"missing_{field}"
        if actual_value != expected[field]:
            return f"{field}_mismatch"
    return None


def _identity_violation(reference: Mapping[str, Any], expected: Mapping[str, str], reason: str) -> ClaimViolation:
    value = _clean_number(reference.get("value", reference.get("raw_value"))) or 0.0
    unit = str(reference.get("unit") or reference.get("currency") or reference.get("fact_currency") or "")
    return ClaimViolation(
        reason=reason,
        metric=str(
            reference.get("canonical_name")
            or reference.get("metric_name")
            or reference.get("metric")
            or "unknown"
        ),
        line_number=int(reference.get("line_number") or 0),
        claimed_value=0.0,
        claimed_unit="",
        claimed_currency="",
        claimed_period="",
        evidence_value=value,
        evidence_unit=unit,
        evidence_currency=_currency_token(reference.get("currency"), reference.get("fact_currency"), unit),
        evidence_id=str(reference.get("evidence_id") or ""),
        evidence_quote=str(reference.get("quote") or reference.get("quote_text") or ""),
        period=str(reference.get("period_key") or reference.get("period") or ""),
        market=str(reference.get("market") or ""),
        company_id=str(reference.get("company_id") or ""),
        filing_id=str(reference.get("filing_id") or reference.get("report_id") or ""),
        parse_run_id=str(reference.get("parse_run_id") or ""),
        expected_market=expected["market"],
        expected_company_id=expected["company_id"],
        expected_filing_id=expected["filing_id"],
        expected_parse_run_id=expected["parse_run_id"],
    )


def verify_financial_claims(
    reply: str,
    *,
    expected_identity: Mapping[str, Any] | None = None,
) -> ClaimVerificationResult:
    facts = _reference_facts(reply)
    violations: list[ClaimViolation] = []
    expected = _expected_identity(expected_identity)
    if expected:
        for reference in _extract_source_references(reply):
            source_type = str(reference.get("source_type") or "")
            if not (
                source_type.startswith("wiki")
                or source_type.startswith("postgres")
                or source_type == "postgresql"
            ):
                continue
            reason = _identity_violation_reason(reference, expected)
            if reason:
                violations.append(_identity_violation(reference, expected, reason))
    if not facts:
        return ClaimVerificationResult(
            checked=bool(expected),
            allowed=not violations,
            claims=(),
            facts=(),
            violations=tuple(violations),
        )
    claims = _extract_claims(reply, facts)
    for claim in claims:
        candidates = [fact for fact in facts if fact.metric == claim.metric and fact.value_category == claim.value_category]
        if not candidates or any(_matches_evidence(claim, fact) for fact in candidates):
            continue
        nearest = min(candidates, key=lambda fact: abs(claim.normalized_value - fact.normalized_value))
        violations.append(
            ClaimViolation(
                reason=_violation_reason(claim, nearest),
                metric=claim.metric,
                line_number=claim.line_number,
                claimed_value=claim.value,
                claimed_unit=claim.unit,
                claimed_currency=claim.currency,
                claimed_period=claim.period_text,
                evidence_value=nearest.value,
                evidence_unit=nearest.unit,
                evidence_currency=nearest.currency,
                evidence_id=nearest.evidence_id,
                evidence_quote=nearest.quote,
                period=nearest.period,
                market=nearest.market,
                company_id=nearest.company_id,
                filing_id=nearest.filing_id,
                parse_run_id=nearest.parse_run_id,
            )
        )
    return ClaimVerificationResult(
        checked=bool(claims or expected),
        allowed=not violations,
        claims=claims,
        facts=facts,
        violations=tuple(violations),
    )


def claim_verification_payload(result: ClaimVerificationResult) -> dict[str, Any]:
    return {
        "checked": result.checked,
        "allowed": result.allowed,
        "claim_count": len(result.claims),
        "evidence_fact_count": len(result.facts),
        "violation_count": len(result.violations),
        "violations": [
            {
                "reason": item.reason,
                "metric": item.metric,
                "line_number": item.line_number,
                "claimed_value": item.claimed_value,
                "claimed_unit": item.claimed_unit,
                "claimed_currency": item.claimed_currency,
                "claimed_period": item.claimed_period,
                "evidence_value": item.evidence_value,
                "evidence_unit": item.evidence_unit,
                "evidence_currency": item.evidence_currency,
                "evidence_id": item.evidence_id,
                "evidence_quote": item.evidence_quote,
                "period": item.period,
                "market": item.market,
                "company_id": item.company_id,
                "filing_id": item.filing_id,
                "parse_run_id": item.parse_run_id,
                "expected_market": item.expected_market,
                "expected_company_id": item.expected_company_id,
                "expected_filing_id": item.expected_filing_id,
                "expected_parse_run_id": item.expected_parse_run_id,
            }
            for item in result.violations[:20]
        ],
    }
