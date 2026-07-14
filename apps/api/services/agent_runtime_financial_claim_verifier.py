from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any, Mapping, Sequence

FINANCIAL_MINUS_SIGNS = "-‐‑‒−﹣－"
FINANCIAL_MINUS_SIGN_CLASS = re.escape(FINANCIAL_MINUS_SIGNS)
_FINANCIAL_MINUS_TRANSLATION = str.maketrans({sign: "-" for sign in FINANCIAL_MINUS_SIGNS if sign != "-"})


def normalize_financial_minus_signs(value: Any) -> str:
    """Normalize common model/PDF minus glyphs without accepting dash punctuation."""

    return ("" if value is None else str(value)).translate(_FINANCIAL_MINUS_TRANSLATION)


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
FOOTNOTE_ALIAS_SUFFIX_RE = re.compile(
    r"\s*[（(](?:[ivxlcdm]+|\d+|[一二三四五六七八九十]+)[）)]\s*$",
    re.IGNORECASE,
)
ABSOLUTE_CHANGE_CLAIM_TERMS = (
    "同比",
    "变动",
    "增加",
    "减少",
    "上升",
    "下降",
    "净增",
    "差异",
    "计提",
    "损失",
    "本期发生",
    "报告期发生",
)
ABSOLUTE_CHANGE_ALIAS_SUFFIX_RE = re.compile(r"(?:同比变动|绝对变动|变动额|变动)$")
INCREASE_CHANGE_TERMS = ("增加", "增长", "上升", "提升", "净增", "计提")
DECREASE_CHANGE_TERMS = ("减少", "下降", "降低", "下滑", "降幅", "转出")
NEGATED_CHANGE_TERMS = (
    "未新增计提",
    "无新增计提",
    "没有新增计提",
    "未计提",
    "未增加",
    "未增长",
    "未减少",
    "未下降",
)
UNCHANGED_CHANGE_TERMS = ("持平", "不变", "无变化", *NEGATED_CHANGE_TERMS)
EXPLICIT_RANGE_TERMS = ("区间", "范围", "介于", "介乎", "从")

UNIT_MULTIPLIERS = {
    "元": ("currency", 1.0),
    "千元": ("currency", 1_000.0),
    "万元": ("currency", 10_000.0),
    "百万元": ("currency", 1_000_000.0),
    "百万": ("currency", 1_000_000.0),
    "亿元": ("currency", 100_000_000.0),
    "亿": ("currency", 100_000_000.0),
    "cny": ("currency", 1.0),
    "rmb": ("currency", 1.0),
    "人民币": ("currency", 1.0),
    "人民币元": ("currency", 1.0),
    "人民币千元": ("currency", 1_000.0),
    "rmb thousand": ("currency", 1_000.0),
    "cny thousand": ("currency", 1_000.0),
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
    r"(?:[（(]\s*)?"
    rf"(?P<value>[+{FINANCIAL_MINUS_SIGN_CLASS}]?(?:\d{{1,3}}(?:,\d{{3}})+|\d+)(?:\.\d+)?)"
    r"(?:\s*[)）])?"
    r"\s*(?P<unit>人民币元/股|港元/股|美元/股|英镑/股|元/股|百万日元|百万韩元|百万英镑|百万円|백만원|"
    r"亿日元|亿韩元|億元|億円|억원|人民币千元|千元|万元|百万元|人民币元|港元|港币|美元|欧元|英镑|瑞士法郎|日元|韩元|"
    r"billion|million|thousand|per\s+share|百分点|％|%|亿|元|pct)(?![A-Za-z])",
    re.IGNORECASE,
)
CLAUSE_SPLIT_RE = re.compile(r"[。；;！？!?]|(?<!\d)[,，](?!\d)")
CLAUSE_CONTINUATION_RE = re.compile(r"^(?:为|约为|达(?:到)?|是|即(?:为)?)\s*")
SOURCE_FIELD_START_RE = re.compile(r"(?:(?<=^)|(?<=[\s,，;；|]))([A-Za-z_][A-Za-z0-9_]*)=")
DATE_RE = re.compile(
    rf"\b(?P<year>20\d{{2}})[{FINANCIAL_MINUS_SIGN_CLASS}/.年](?P<month>\d{{1,2}})[{FINANCIAL_MINUS_SIGN_CLASS}/.月](?P<day>\d{{1,2}})日?"
)
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
CALCULATOR_OPERATIONS = frozenset(
    {"normalize_amount", "yoy", "yoy_growth", "ratio", "cagr", "per_capita"}
)
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
    change_direction: str = ""


@dataclass(frozen=True)
class NumericClaim:
    metric: str
    value: float
    value_text: str
    unit: str
    normalized_value: float
    value_category: str
    currency: str
    period_tokens: tuple[str, ...]
    period_text: str
    line_number: int
    line: str
    match_start: int = 0
    change_direction: str = ""


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


@dataclass(frozen=True)
class PercentClaimOccurrence:
    value: Decimal
    value_text: str
    is_percentage_point: bool
    line_number: int
    line: str
    match_start: int
    comparison: str = "exact"


def _trace_decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    text = normalize_financial_minus_signs(value).strip().replace(",", "")
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


def _trace_input_records(inputs: Any) -> list[tuple[str, Mapping[str, Any]]]:
    if not isinstance(inputs, Mapping):
        return []
    records: list[tuple[str, Mapping[str, Any]]] = []
    for key, value in inputs.items():
        if isinstance(value, Mapping):
            records.append((str(key), value))
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
    for key in ("rate", "ratio", "value", "native_per", "net", "native_base_value"):
        number = _trace_decimal(result.get(key))
        if number is not None:
            return number
    percent = _trace_decimal(result.get("percent"))
    return percent / Decimal("100") if percent is not None else None


def _trace_result_reason(operation: str, result: Any, expected: Decimal) -> str | None:
    if not isinstance(result, Mapping):
        return "trace_result_missing"
    if operation == "normalize_amount":
        base_value = _trace_decimal(result.get("native_base_value"))
        hundred_million_value = _trace_decimal(result.get("native_100m_value"))
        if base_value is None or hundred_million_value is None:
            return "trace_result_missing"
        if not _trace_numbers_close(base_value, expected):
            return "trace_result_mismatch"
        if not _trace_numbers_close(hundred_million_value * Decimal("100000000"), expected):
            return "trace_result_mismatch"
        return None
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
    if operation == "normalize_amount":
        amount = inputs.get("amount", {})
        return _trace_input_scale(amount) if isinstance(amount, Mapping) else None
    if operation in {"yoy", "yoy_growth"}:
        current = _trace_input_scale(inputs.get("current", {})) if isinstance(inputs.get("current"), Mapping) else None
        previous = (
            _trace_input_scale(inputs.get("previous", {})) if isinstance(inputs.get("previous"), Mapping) else None
        )
        if current is None or previous is None or previous == 0:
            return None
        return (current - previous) / abs(previous)
    if operation == "ratio":
        numerator = (
            _trace_input_scale(inputs.get("numerator", {})) if isinstance(inputs.get("numerator"), Mapping) else None
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
    if operation == "normalize_amount":
        amount = inputs.get("amount")
        if not isinstance(amount, Mapping):
            return "trace_inputs_missing"
        if _unit_multiplier(amount.get("unit")) is None:
            return "trace_input_unit_invalid"
        return None
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


def _trace_reference_aliases(reference: Mapping[str, Any]) -> set[str]:
    aliases = {str(reference.get(key) or "").strip().lower() for key in ("metric", "metric_name", "canonical_name")}
    extra_aliases = reference.get("aliases")
    if isinstance(extra_aliases, Sequence) and not isinstance(extra_aliases, (str, bytes)):
        aliases.update(str(alias or "").strip().lower() for alias in extra_aliases)
    return {alias for alias in aliases if alias}


def _normalized_locator_value(value: Any) -> str:
    text = str(value or "").strip()
    match = re.match(r"^(?P<number>\d+)(?=$|[\s,，;；。)）\]】])", text)
    return match.group("number") if match else text


def _trace_visible_locator_matches(
    trusted: Mapping[str, Any],
    visible_references: Sequence[Mapping[str, Any]],
) -> bool:
    """Require an internal cell fact to remain reachable from the displayed answer."""

    task_id = str(trusted.get("task_id") or "").strip()
    trusted_locator = {
        "pdf_page": _normalized_locator_value(trusted.get("pdf_page") or trusted.get("pdf_page_number")),
        "table_index": _normalized_locator_value(trusted.get("table_index")),
        "md_line": _normalized_locator_value(trusted.get("md_line")),
    }
    if not task_id or not any(trusted_locator.values()):
        return False
    for reference in visible_references:
        if str(reference.get("task_id") or "").strip() != task_id:
            continue
        visible_locator = {
            "pdf_page": _normalized_locator_value(reference.get("pdf_page") or reference.get("pdf_page_number")),
            "table_index": _normalized_locator_value(reference.get("table_index")),
            "md_line": _normalized_locator_value(reference.get("md_line")),
        }
        shared_fields = [field for field in trusted_locator if trusted_locator[field] and visible_locator[field]]
        if shared_fields and all(trusted_locator[field] == visible_locator[field] for field in shared_fields):
            return True
    return False


def _trace_evidence_reason(
    payload: Mapping[str, Any],
    reply: str,
    *,
    trusted_evidence: Sequence[Mapping[str, Any]] = (),
) -> str | None:
    inputs = payload.get("inputs")
    if not isinstance(inputs, Mapping) or not inputs:
        return "trace_inputs_missing"
    visible_references = _extract_source_references(reply)
    trusted_by_id = {
        str(reference.get("evidence_id") or ""): reference
        for reference in trusted_evidence
        if isinstance(reference, Mapping) and str(reference.get("evidence_id") or "")
    }
    trace_identity = payload.get("research_identity") if isinstance(payload.get("research_identity"), Mapping) else {}
    output_period_tokens = set(_period_tokens(payload.get("period")))
    input_period_tokens: set[str] = set()
    input_metrics: list[str] = []
    references_by_role: dict[str, Mapping[str, Any]] = {}
    trusted_reference_roles: set[str] = set()
    for input_role, item in _trace_input_records(inputs):
        # periods is a mathematical scalar, not a report fact.
        declared_role = str(item.get("role") or "").strip()
        period_count_alias = declared_role == "period_count" and input_role in {"periods", "period_count"}
        if declared_role and declared_role != input_role and not period_count_alias:
            return "trace_input_role_mismatch"
        role = "period_count" if period_count_alias else input_role
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
        evidence_id = str(item.get("evidence_id") or "")
        reference = trusted_by_id.get(evidence_id)
        is_server_trusted_reference = reference is not None
        if reference is not None:
            if not _trace_visible_locator_matches(reference, visible_references):
                return "trace_input_source_locator_missing"
        else:
            matches = [
                candidate for candidate in visible_references if str(candidate.get("evidence_id") or "") == evidence_id
            ]
            if not matches:
                return "trace_input_evidence_missing"
            reference = matches[0]
        if role:
            references_by_role[role] = reference
            if is_server_trusted_reference:
                trusted_reference_roles.add(role)
        input_metrics.append(str(item.get("metric") or "").strip().lower())
        input_period_tokens.update(_period_tokens(item.get("period")))
        for field in IDENTITY_FIELDS:
            if _normalized_identity_value(field, reference.get(field)) != _normalized_identity_value(
                field, trace_identity.get(field)
            ):
                return f"trace_input_{field}_mismatch"
        aliases = _trace_reference_aliases(reference)
        if str(item.get("metric") or "").strip().lower() not in aliases:
            return "trace_input_metric_mismatch"
        if not set(_period_tokens(item.get("period"))).intersection(
            _period_tokens(reference.get("period_key") or reference.get("period"))
        ):
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
    if operation in {"normalize_amount", "yoy", "yoy_growth", "cagr", "per_capita"} and not any(
        metric and metric in output_metric for metric in input_metrics
    ):
        return "trace_metric_mismatch"
    if operation in {"yoy", "yoy_growth"} and metrics_by_role.get("current") != metrics_by_role.get("previous"):
        return "trace_input_metric_mismatch"
    if operation in {"yoy", "yoy_growth"}:
        period_roles = ("current", "previous")
        period_pair = tuple(
            references_by_role[role]
            for role in period_roles
            if role in references_by_role
        )
        # Legacy structured/tool traces can carry only displayed evidence
        # references.  Enforce lineage/scope when the references are bound to
        # server-resolved evidence; backend recomputation is always in this path.
        if set(period_roles).issubset(trusted_reference_roles):
            if len(period_pair) != 2 or not _same_source_lineage(period_pair):
                return "trace_input_lineage_mismatch"
            if not _same_financial_scope(period_pair, require_known=False):
                return "trace_input_scope_mismatch"
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
    if operation == "ratio":
        ratio_reference_roles = ("numerator", "denominator")
        ratio_references = tuple(
            references_by_role[role]
            for role in ratio_reference_roles
            if role in references_by_role
        )
        if set(ratio_reference_roles).issubset(trusted_reference_roles):
            if len(ratio_references) != 2 or not _ratio_scope_compatible(ratio_references):
                return "trace_input_scope_mismatch"
    if operation in RECONCILIATION_OPERATIONS:
        expected_roles = {
            "gross": ("gross", "original", "cost"),
            "allowance": ("allowance", "impairment", "provision"),
            "net": ("net", "carrying"),
        }
        for role, aliases in expected_roles.items():
            if not any(alias in metrics_by_role.get(role, "") for alias in aliases):
                return "trace_input_metric_mismatch"
        reconciliation_roles = ("gross", "allowance", "net")
        reconciliation_references = tuple(
            references_by_role[role]
            for role in reconciliation_roles
            if role in references_by_role
        )
        if set(reconciliation_roles).issubset(trusted_reference_roles):
            if len(reconciliation_references) != 3 or not _same_financial_scope(
                reconciliation_references,
                require_known=True,
            ):
                return "trace_input_scope_mismatch"
    return None


DERIVED_PERCENT_CLAIM_RE = re.compile(
    rf"(?P<value>[+{FINANCIAL_MINUS_SIGN_CLASS}]?\d+(?:\.\d+)?)\s*(?P<unit>个百分点|百分点|[%％])"
)
PERCENT_RANGE_PREFIX_RE = re.compile(r"\d[\d,]*(?:\.\d+)?\s*[%％]\s*$")


def _has_explicit_range_context(text: str, endpoint_start: int) -> bool:
    prefix = text[:endpoint_start]
    segment_start = max(
        (prefix.rfind(marker) for marker in ("，", ",", "；", ";", "。", "！", "!", "？", "?")),
        default=-1,
    )
    return any(term in prefix[segment_start + 1 :] for term in EXPLICIT_RANGE_TERMS)

DERIVED_PERCENT_TERMS = (
    "同比",
    "yoy",
    "环比",
    "增长",
    "下降",
    "增幅",
    "降幅",
    "增长率",
    "增速",
    "占",
    "占比",
    "集中度",
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


def _percent_decimal(value: Any) -> Decimal | None:
    return _trace_decimal(value)


def _percent_claim_details(
    reply: str,
    *,
    require_derived_term: bool,
) -> tuple[tuple[Decimal, bool], ...]:
    return tuple(
        (occurrence.value, occurrence.is_percentage_point)
        for occurrence in _percent_claim_occurrences(reply, require_derived_term=require_derived_term)
    )


def _percent_claim_occurrences(
    reply: str,
    *,
    require_derived_term: bool,
) -> tuple[PercentClaimOccurrence, ...]:
    claims: list[PercentClaimOccurrence] = []
    for line_number, line in enumerate((reply or "").splitlines(), start=1):
        lowered = line.lower()
        if "source_type=" in line or "schema_version" in line:
            continue
        if require_derived_term and not any(term in lowered for term in DERIVED_PERCENT_TERMS):
            continue
        for match in DERIVED_PERCENT_CLAIM_RE.finditer(line):
            value_text = match.group("value")
            value = _percent_decimal(value_text)
            if value is not None:
                prefix = line[: match.start()]
                normalized_value_text = normalize_financial_minus_signs(value_text).lstrip()
                previous_endpoint = PERCENT_RANGE_PREFIX_RE.search(prefix)
                range_separator = (
                    normalized_value_text.startswith("-")
                    and previous_endpoint is not None
                    and _has_explicit_range_context(prefix, previous_endpoint.start())
                )
                if range_separator:
                    value = abs(value)
                unsigned_value = range_separator or not normalized_value_text.startswith(("+", "-"))
                segment_start = max((prefix.rfind(marker) for marker in ("，", ",", "；", ";", "。")), default=-1)
                direction_context = prefix[segment_start + 1 :].lower()
                if unsigned_value and any(
                    term in direction_context for term in ("下降", "减少", "降低", "降幅", "下滑", "decrease")
                ):
                    value = -abs(value)
                comparison_context = re.sub(r"[\s*`_'\"“”‘’~]+", "", direction_context[-32:])
                if any(comparison_context.endswith(term) for term in ("不高于", "不超过", "至多", "低于", "小于")):
                    comparison = "upper_bound"
                elif any(
                    comparison_context.endswith(term)
                    for term in ("不低于", "不少于", "至少", "超过", "高于", "大于", "超")
                ):
                    comparison = "lower_bound"
                elif any(comparison_context.endswith(term) for term in ("大约", "约为", "约", "接近", "近")):
                    comparison = "approximate"
                else:
                    comparison = "exact"
                claims.append(
                    PercentClaimOccurrence(
                        value=value / Decimal("100"),
                        value_text=match.group("value"),
                        is_percentage_point="百分点" in match.group("unit"),
                        line_number=line_number,
                        line=line,
                        match_start=match.start(),
                        comparison=comparison,
                    )
                )
    return tuple(claims)


def _derived_percent_claims(reply: str) -> tuple[Decimal, ...]:
    return tuple(value for value, _is_percentage_point in _percent_claim_details(reply, require_derived_term=True))


def _percent_display_tolerance(occurrence: PercentClaimOccurrence) -> Decimal:
    decimals = len(occurrence.value_text.rsplit(".", 1)[1]) if "." in occurrence.value_text else 0
    displayed_percent_quantum = Decimal("1").scaleb(-decimals)
    half_display_quantum = displayed_percent_quantum / Decimal("200")
    business_tolerance = Decimal("0.0005")  # 0.05 percentage points in ratio units.
    return max(half_display_quantum, business_tolerance) + Decimal("1e-12")


def _percent_occurrence_matches_expected(occurrence: PercentClaimOccurrence, expected: Decimal) -> bool:
    if occurrence.comparison == "lower_bound":
        return expected >= occurrence.value
    if occurrence.comparison == "upper_bound":
        return expected <= occurrence.value
    tolerance = _percent_display_tolerance(occurrence)
    if occurrence.comparison == "approximate":
        tolerance = max(tolerance, Decimal("0.005"))
    return abs(occurrence.value - expected) <= tolerance


def _expected_trace_metrics(reply: str) -> frozenset[str]:
    prose = "\n".join(
        line for line in (reply or "").splitlines() if "source_type=" not in line and "schema_version" not in line
    ).lower()
    return frozenset(
        metric for metric, aliases in DERIVED_METRIC_REPLY_ALIASES.items() if any(alias in prose for alias in aliases)
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
                "normalize_amount": (("amount", "value", "unit"),),
                "yoy": (("current", "current", "current_unit"), ("previous", "previous", "previous_unit")),
                "yoy_growth": (
                    ("current", "current", "current_unit"),
                    ("previous", "previous", "previous_unit"),
                ),
                "ratio": (
                    ("numerator", "numerator", "numerator_unit"),
                    ("denominator", "denominator", "denominator_unit"),
                ),
                "cagr": (("start", "start", "start_unit"), ("end", "end", "end_unit")),
                "per_capita": (("amount", "amount", "amount_unit"), ("count", "count", "count_unit")),
            }.get(operation, ())
            inputs: dict[str, Any] = {}
            periods: list[str] = []
            for role, value_key, unit_name_key in role_specs:
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


PLAIN_CALC_NUMBER_RE = re.compile(
    rf"(?<![A-Za-z0-9_])(?P<value>\(?[+{FINANCIAL_MINUS_SIGN_CLASS}]?\d[\d,]*(?:\.\d+)?\)?)"
)


def _trusted_evidence_metric(reference: Mapping[str, Any]) -> str:
    return str(reference.get("canonical_name") or reference.get("metric") or reference.get("metric_name") or "").strip()


def _trusted_evidence_period(reference: Mapping[str, Any]) -> str:
    return str(reference.get("period_key") or reference.get("period") or "").strip()


def _trusted_evidence_scope(reference: Mapping[str, Any]) -> str:
    value = str(
        reference.get("financial_scope")
        or reference.get("statement_scope")
        or reference.get("scope")
        or ""
    ).strip().lower()
    compact = re.sub(r"[\s_\-]+", "", value)
    if compact in {"consolidated", "group", "合并", "集团", "合并报表"}:
        return "consolidated"
    if compact in {"parent", "parentcompany", "company", "standalone", "separate", "母公司", "公司", "单体"}:
        return "parent"
    if compact:
        return compact
    return ""


def _trusted_evidence_lineage(reference: Mapping[str, Any]) -> str:
    if lineage := str(reference.get("source_lineage") or "").strip():
        return lineage
    task_id = str(reference.get("parse_run_id") or reference.get("task_id") or "").strip()
    table_index = str(reference.get("table_index") or "").strip()
    md_line = str(reference.get("md_line") or "").strip()
    if not task_id or not (table_index or md_line):
        return ""
    return f"{task_id}|{table_index}|{md_line}"


def _same_financial_scope(
    references: Sequence[Mapping[str, Any]],
    *,
    require_known: bool,
) -> bool:
    scopes = [_trusted_evidence_scope(reference) for reference in references]
    if require_known and not all(scopes):
        return False
    known_scopes = {scope for scope in scopes if scope}
    return len(known_scopes) <= 1 and (not any(scopes) or all(scopes))


def _same_source_lineage(references: Sequence[Mapping[str, Any]]) -> bool:
    lineages = [_trusted_evidence_lineage(reference) for reference in references]
    return bool(lineages) and all(lineages) and len(set(lineages)) == 1


def _ratio_scope_compatible(references: Sequence[Mapping[str, Any]]) -> bool:
    """Allow same-table ratios, or cross-table ratios with explicit equal scope."""

    return (
        _same_financial_scope(references, require_known=False)
        if _same_source_lineage(references)
        else _same_financial_scope(references, require_known=True)
    )


def _trusted_evidence_value(reference: Mapping[str, Any]) -> Decimal | None:
    value = reference.get("value", reference.get("raw_value"))
    number = _trace_decimal(("" if value is None else str(value)).replace("(", "-").replace(")", ""))
    return number


def _trusted_trace_input(reference: Mapping[str, Any], role: str) -> dict[str, Any]:
    value = reference.get("value", reference.get("raw_value"))
    return {
        "role": role,
        "metric": _trusted_evidence_metric(reference),
        "period": _trusted_evidence_period(reference),
        "value": "" if value is None else str(value),
        "unit": str(reference.get("unit") or reference.get("currency") or ""),
        "scale": reference.get("scale"),
        "evidence_id": str(reference.get("evidence_id") or ""),
    }


def _trace_period_sort_key(reference: Mapping[str, Any]) -> tuple[int, str]:
    period = _trusted_evidence_period(reference)
    years = [int(token) for token in _period_tokens(period) if len(token) == 4 and token.isdigit()]
    return (max(years) if years else -1, period)


def _displayed_result_matches(expected: Decimal, claims: Sequence[tuple[Decimal, bool]]) -> bool:
    return any(
        not is_percentage_point and abs(value - expected) <= Decimal("0.0005") for value, is_percentage_point in claims
    )


def _compact_semantic_text(value: Any) -> str:
    return "".join(char for char in str(value or "").lower() if char.isalnum())


def _line_mentions_reference(line: str, reference: Mapping[str, Any]) -> bool:
    compact_line = _compact_semantic_text(line)
    for alias in _trace_reference_aliases(reference):
        compact_alias = _compact_semantic_text(alias)
        if len(compact_alias) >= 2 and compact_alias not in {"合计", "小计"} and compact_alias in compact_line:
            return True
    return False


def _reference_occurrence_score(
    occurrence: PercentClaimOccurrence,
    reference: Mapping[str, Any],
) -> tuple[int, int, int] | None:
    compact_line_chars: list[str] = []
    compact_claim_start = 0
    for index, char in enumerate(occurrence.line.lower()):
        if char.isalnum() or "\u4e00" <= char <= "\u9fff":
            if index < occurrence.match_start:
                compact_claim_start += 1
            compact_line_chars.append(char)
    compact_line = "".join(compact_line_chars)
    best: tuple[int, int, int] | None = None
    for alias in _trace_reference_aliases(reference):
        compact_alias = _compact_semantic_text(alias)
        if len(compact_alias) < 2 or compact_alias in {"合计", "小计"}:
            continue
        start = compact_line.find(compact_alias)
        while start >= 0:
            end = start + len(compact_alias)
            score = (
                0 if end <= compact_claim_start else 1,
                compact_claim_start - end if end <= compact_claim_start else start - compact_claim_start,
                -len(compact_alias),
            )
            if best is None or score < best:
                best = score
            start = compact_line.find(compact_alias, start + 1)
    return best


def _occurrence_nearest_metrics(
    occurrence: PercentClaimOccurrence,
    references: Sequence[Mapping[str, Any]],
) -> set[str]:
    scored = [
        (score, _trusted_evidence_metric(reference).lower())
        for reference in references
        if (score := _reference_occurrence_score(occurrence, reference)) is not None
    ]
    if not scored:
        return set()
    best = min(score for score, _metric in scored)
    return {metric for score, metric in scored if score == best}


def _occurrence_subject_metrics(
    occurrence: PercentClaimOccurrence,
    references: Sequence[Mapping[str, Any]],
) -> set[str]:
    prefix = occurrence.line[: occurrence.match_start]
    segment_start = max(prefix.rfind("；"), prefix.rfind(";"), prefix.rfind("。")) + 1
    segment = prefix[segment_start:]
    colon_positions = [position for marker in ("：", ":") if (position := segment.find(marker)) >= 0]
    if not colon_positions:
        return set()
    subject = segment[: min(colon_positions)]
    return {
        _trusted_evidence_metric(reference).lower()
        for reference in references
        if _line_mentions_reference(subject, reference)
    }


def _occurrence_period_matches(
    occurrence: PercentClaimOccurrence,
    references: Sequence[Mapping[str, Any]],
) -> bool:
    expected_tokens = {
        token for reference in references for token in _period_tokens(_trusted_evidence_period(reference))
    }
    if not expected_tokens:
        return False
    prefix = occurrence.line[: occurrence.match_start]
    year_matches = list(YEAR_RE.finditer(prefix))
    if not year_matches:
        return True
    nearest = year_matches[-1]
    # A distant year in a section title is weak context; a nearby year label is
    # authoritative for comparative lines containing more than one period.
    if occurrence.match_start - nearest.end() > 96:
        return True
    return nearest.group(1) in expected_tokens


def _derived_sum_ratio_formula_matches(
    line: str,
    numerator: Mapping[str, Any],
    denominator: Mapping[str, Any],
    references: Sequence[Mapping[str, Any]],
) -> bool:
    source_ids = numerator.get("derived_from_evidence_ids")
    if not isinstance(source_ids, Sequence) or isinstance(source_ids, (str, bytes)) or len(source_ids) < 2:
        return False
    references_by_id = {str(item.get("evidence_id") or ""): item for item in references}
    source_references = [references_by_id.get(str(evidence_id or "")) for evidence_id in source_ids]
    if not all(isinstance(item, Mapping) for item in source_references):
        return False

    normalized_numerator = _normalized_amount(
        numerator.get("value", numerator.get("raw_value")),
        numerator.get("unit") or numerator.get("currency"),
        scale=numerator.get("scale"),
    )
    normalized_sources = [
        _normalized_amount(
            item.get("value", item.get("raw_value")),
            item.get("unit") or item.get("currency"),
            scale=item.get("scale"),
        )
        for item in source_references
        if isinstance(item, Mapping)
    ]
    if (
        normalized_numerator is None
        or any(item is None or item[1] != normalized_numerator[1] for item in normalized_sources)
        or abs(sum(item[0] for item in normalized_sources if item is not None) - normalized_numerator[0])
        > max(0.01, abs(normalized_numerator[0]) * 0.000001)
    ):
        return False

    for division in re.finditer(r"[/÷]", line):
        equals = re.search(r"[=＝]", line[division.end() :])
        if equals is None:
            continue
        equals_start = division.end() + equals.start()
        source_spans: list[tuple[int, int]] = []
        for reference in source_references:
            spans = [span for span in _trusted_value_occurrences(line, reference) if span[1] <= division.start()]
            if len(spans) != 1:
                break
            source_spans.append(spans[0])
        else:
            denominator_spans = [
                span
                for span in _trusted_value_occurrences(line, denominator)
                if division.end() <= span[0] and span[1] <= equals_start
            ]
            if len(denominator_spans) != 1:
                continue
            denominator_span = denominator_spans[0]
            ordered_sources = sorted(source_spans)
            if len(set(ordered_sources)) != len(ordered_sources):
                continue
            formula_start = ordered_sources[0][0]
            numeric_spans = [
                _normalized_number_span(match, line)
                for match in PLAIN_CALC_NUMBER_RE.finditer(line)
                if formula_start <= _normalized_number_span(match, line)[0]
                and _normalized_number_span(match, line)[1] <= equals_start
            ]
            if numeric_spans != [*ordered_sources, denominator_span]:
                continue
            numerator_expression = line[formula_start : division.start()]
            if len(re.findall(r"\+", numerator_expression)) != len(ordered_sources) - 1:
                continue
            if re.search(rf"[{FINANCIAL_MINUS_SIGN_CLASS}*/×÷]", numerator_expression):
                continue
            denominator_expression = line[division.end() : equals_start]
            if re.search(rf"[+{FINANCIAL_MINUS_SIGN_CLASS}*/×÷]", denominator_expression):
                continue
            return True
    return False


def _visible_reply_binds_reference(visible_reply: str, reference: Mapping[str, Any]) -> bool:
    return any(
        "source_type=" not in line
        and "schema_version" not in line
        and _line_mentions_reference(line, reference)
        and _line_contains_trusted_value(line, reference)
        for line in visible_reply.splitlines()
    )


def _ratio_semantic_pair_bound(
    occurrence: PercentClaimOccurrence,
    primary: Mapping[str, Any],
    secondary: Mapping[str, Any],
    all_references: Sequence[Mapping[str, Any]],
    visible_reply: str,
) -> bool:
    """Bind natural-language component ratios only when both operands are explicit."""

    prefix = occurrence.line[: occurrence.match_start]
    ratio_marker = prefix.rfind("占")
    if ratio_marker < 0:
        return False
    denominator_context = prefix[ratio_marker + 1 :]
    if not _line_mentions_reference(denominator_context, secondary):
        return False

    subject_boundary = max(
        (prefix.rfind(marker, 0, ratio_marker) for marker in ("，", ",", "；", ";", "。", "：", ":", "）", ")")),
        default=-1,
    )
    subject_context = prefix[subject_boundary + 1 : ratio_marker]
    source_ids = primary.get("derived_from_evidence_ids")
    if isinstance(source_ids, Sequence) and not isinstance(source_ids, (str, bytes)):
        if len(source_ids) < 2 or "合计" not in subject_context:
            return False
        references_by_id = {str(item.get("evidence_id") or ""): item for item in all_references}
        source_references = [references_by_id.get(str(evidence_id or "")) for evidence_id in source_ids]
        if not all(isinstance(item, Mapping) for item in source_references):
            return False
        clause_boundary = max(
            (prefix.rfind(marker, 0, ratio_marker) for marker in ("；", ";", "。", "：", ":")),
            default=-1,
        )
        source_context = prefix[clause_boundary + 1 : ratio_marker]
        if not all(
            _line_mentions_reference(source_context, reference)
            for reference in source_references
            if isinstance(reference, Mapping)
        ):
            return False
        operand_references = (
            *[reference for reference in source_references if isinstance(reference, Mapping)],
            secondary,
        )
    else:
        if not _line_mentions_reference(subject_context, primary):
            compact_subject = _compact_semantic_text(subject_context)
            antecedent_context = prefix[: subject_boundary + 1]
            if not compact_subject.startswith(("其", "该")) or not _line_mentions_reference(
                antecedent_context,
                primary,
            ):
                return False
        operand_references = (primary, secondary)
    return all(_visible_reply_binds_reference(visible_reply, reference) for reference in operand_references)


def _nearest_markdown_heading(occurrence: PercentClaimOccurrence, visible_reply: str) -> str:
    lines = (visible_reply or "").splitlines()
    for line in reversed(lines[: max(0, occurrence.line_number - 1)]):
        if re.match(r"^\s{0,3}#{1,6}\s+", line):
            return line
    return ""


def _matching_percent_occurrences(
    occurrences: Sequence[PercentClaimOccurrence],
    *,
    expected: Decimal,
    operation: str,
    primary: Mapping[str, Any],
    secondary: Mapping[str, Any],
    all_references: Sequence[Mapping[str, Any]],
    output_metric: str = "",
    visible_reply: str = "",
) -> tuple[PercentClaimOccurrence, ...]:
    output_aliases = DERIVED_METRIC_REPLY_ALIASES.get(output_metric, ())
    matches: list[PercentClaimOccurrence] = []
    for occurrence in occurrences:
        if occurrence.is_percentage_point or not _percent_occurrence_matches_expected(occurrence, expected):
            continue
        line = occurrence.line
        direct_primary_value_bound = _line_contains_trusted_value(line, primary)
        derived_sum_formula_bound = operation == "ratio" and _derived_sum_ratio_formula_matches(
            line,
            primary,
            secondary,
            all_references,
        )
        primary_value_bound = direct_primary_value_bound or derived_sum_formula_bound
        secondary_value_bound = _line_contains_trusted_value(line, secondary)
        reply_operands_bound = all(
            _visible_reply_binds_reference(visible_reply, reference) for reference in (primary, secondary)
        )
        period_references = (
            (primary, secondary)
            if operation in {"yoy", "yoy_growth"}
            and ((primary_value_bound and secondary_value_bound) or reply_operands_bound)
            else (primary,)
        )
        if not _occurrence_period_matches(occurrence, period_references):
            continue
        primary_metric = _trusted_evidence_metric(primary).lower()
        semantic_metrics = _occurrence_nearest_metrics(occurrence, all_references) | _occurrence_subject_metrics(
            occurrence,
            all_references,
        )
        semantic_bound = primary_metric in semantic_metrics
        nearest_heading = _nearest_markdown_heading(occurrence, visible_reply)
        heading_semantic_bound = _line_mentions_reference(nearest_heading, primary)
        output_bound = any(_compact_semantic_text(alias) in _compact_semantic_text(line) for alias in output_aliases)
        if operation in {"yoy", "yoy_growth"}:
            # Both periods share one metric, so one metric label plus a derived
            # term is sufficient; raw equations bind by both operand values.
            yoy_context = any(
                term in line for term in ("同比", "增长", "增幅", "增加", "减少", "上升", "下降", "变化", "变动")
            )
            if not (
                (primary_value_bound and secondary_value_bound)
                or ((semantic_bound or heading_semantic_bound) and yoy_context)
            ):
                continue
        elif operation == "ratio":
            ratio_context = any(
                term in line.lower()
                for term in (
                    "占",
                    "集中度",
                    "比率",
                    "毛利率",
                    "净利率",
                    "资产负债率",
                    "收益率",
                    "回报率",
                    "净息差",
                    "ratio",
                    "/",
                )
            )
            expanded_derived_formula = bool(primary.get("derived_from_evidence_ids")) and any(
                operator in line for operator in ("/", "÷")
            )
            component_sum_semantic_bound = "component_sum" in primary_metric and (
                semantic_bound or _line_mentions_reference(line, primary)
            )
            semantic_ratio_bound = component_sum_semantic_bound and not (
                expanded_derived_formula and not direct_primary_value_bound
            )
            explicit_ratio_pair_bound = _ratio_semantic_pair_bound(
                occurrence,
                primary,
                secondary,
                all_references,
                visible_reply,
            )
            component_summary_bound = (
                "计算器校验" in nearest_heading
                and semantic_bound
                and primary_metric.startswith("goodwill_component_")
                and _trusted_evidence_metric(secondary).lower() == "goodwill_gross"
            )
            if not ratio_context or not (
                output_bound
                or (primary_value_bound and secondary_value_bound)
                or semantic_ratio_bound
                or explicit_ratio_pair_bound
                or component_summary_bound
            ):
                continue
        else:
            continue
        matches.append(occurrence)
    return tuple(matches)


def _plain_line_values(line: str) -> tuple[Decimal, ...]:
    values: list[Decimal] = []
    for match in PLAIN_CALC_NUMBER_RE.finditer(line or ""):
        text = normalize_financial_minus_signs(match.group("value"))
        if text.startswith("(") and text.endswith(")"):
            text = f"-{text[1:-1]}"
        else:
            text = text.strip("()")
        value = _trace_decimal(text)
        if value is not None:
            values.append(abs(value))
    return tuple(values)


def _display_amount_tolerance(value_text: str, unit: str, target: float) -> float:
    decimals = len(value_text.rsplit(".", 1)[1]) if "." in value_text else 0
    multiplier = _unit_multiplier(unit)
    quantum = (multiplier[1] if multiplier else 1.0) * (10.0 ** (-decimals))
    floating_slack = max(1e-9, math.ulp(float(target)) * 4)
    return quantum / 2.0 + floating_slack


def _claim_fact_value_distance(
    claim_value: Decimal | float,
    fact_value: Decimal | float,
    metric: str,
) -> Decimal | float:
    if metric.endswith("_absolute_change"):
        return abs(abs(claim_value) - abs(fact_value))
    return abs(claim_value - fact_value)


def _normalized_number_span(match: re.Match[str], line: str) -> tuple[int, int]:
    start, end = match.span("value")
    while start < end and line[start] in f"(（+{FINANCIAL_MINUS_SIGNS}":
        start += 1
    while end > start and line[end - 1] in ")）":
        end -= 1
    return start, end


def _spans_overlap(left: tuple[int, int], right: tuple[int, int]) -> bool:
    return left[0] < right[1] and right[0] < left[1]


def _is_inline_reference_or_period_number(line: str, span: tuple[int, int]) -> bool:
    start, end = span
    before = line[start - 1] if start > 0 else ""
    after = line[end] if end < len(line) else ""
    if (before, after) in {("[", "]"), ("【", "】")}:
        return True
    return after in {"年", "月", "日"}


def _amount_match_number(
    line: str,
    match: re.Match[str],
    previous_match: re.Match[str] | None = None,
) -> float | None:
    value = _clean_number(match.group("value"))
    if value is None or value >= 0 or previous_match is None or any(marker in line for marker in ("=", "＝")):
        return value

    value_text = normalize_financial_minus_signs(match.group("value")).lstrip()
    if not value_text.startswith("-"):
        return value
    if not _has_explicit_range_context(line, previous_match.start("value")):
        return value
    connector = line[previous_match.end() : match.start("value")]
    connector_text = connector.strip()
    previous_unit = previous_match.group("unit")
    current_unit = match.group("unit")
    if connector_text and not (
        connector_text == "元" and previous_unit == current_unit == "亿"
    ):
        return value
    previous_multiplier = _unit_multiplier(previous_unit)
    current_multiplier = _unit_multiplier(current_unit)
    if previous_multiplier != current_multiplier:
        return value
    return abs(value)


def _trusted_value_occurrences(
    line: str,
    reference: Mapping[str, Any],
    *,
    allow_opposite_sign: bool = False,
) -> tuple[tuple[int, int], ...]:
    """Locate a trusted value without treating a unit-bearing number as a raw cell value."""

    raw_value = _trusted_evidence_value(reference)
    observed = _normalized_amount(
        reference.get("value", reference.get("raw_value")),
        reference.get("unit") or reference.get("currency"),
        scale=reference.get("scale"),
    )
    expected_currency = _currency_token(reference.get("currency"), reference.get("unit"))
    unit_matches = list(NUMBER_WITH_UNIT_RE.finditer(line or ""))
    unit_value_spans = tuple(_normalized_number_span(match, line) for match in unit_matches)
    occurrences: list[tuple[int, int]] = []

    if observed is not None:
        target, category = observed
        for match_index, (match, value_span) in enumerate(zip(unit_matches, unit_value_spans, strict=True)):
            previous_match = unit_matches[match_index - 1] if match_index else None
            displayed = _normalized_amount(
                _amount_match_number(line, match, previous_match),
                match.group("unit"),
            )
            if displayed is None or displayed[1] != category:
                continue
            displayed_currency = _currency_token(match.group("currency"))
            if displayed_currency and expected_currency and displayed_currency != expected_currency:
                continue
            difference = (
                abs(abs(displayed[0]) - abs(target)) if allow_opposite_sign else abs(displayed[0] - target)
            )
            if difference <= _display_amount_tolerance(
                match.group("value"),
                match.group("unit"),
                target,
            ):
                occurrences.append(value_span)

    if raw_value is not None:
        for match in PLAIN_CALC_NUMBER_RE.finditer(line or ""):
            value_span = _normalized_number_span(match, line)
            if any(_spans_overlap(value_span, unit_span) for unit_span in unit_value_spans):
                continue
            text = normalize_financial_minus_signs(match.group("value"))
            if text.startswith("(") and text.endswith(")"):
                text = f"-{text[1:-1]}"
            else:
                text = text.strip("()（）")
            value = _trace_decimal(text)
            if value is None:
                continue
            difference = abs(abs(value) - abs(raw_value)) if allow_opposite_sign else abs(value - raw_value)
            if difference <= TRACE_RESULT_ABSOLUTE_TOLERANCE:
                occurrences.append(value_span)

    return tuple(dict.fromkeys(occurrences))


def _line_contains_trusted_value(line: str, reference: Mapping[str, Any]) -> bool:
    return bool(_trusted_value_occurrences(line, reference))


def _reconciliation_equation_clause_matches(
    clause: str,
    gross: Mapping[str, Any],
    allowance: Mapping[str, Any],
    net: Mapping[str, Any],
) -> bool:
    equals_matches = list(re.finditer(r"[=＝]", clause))
    if not equals_matches:
        return False
    gross_occurrences = _trusted_value_occurrences(clause, gross)
    allowance_occurrences = _trusted_value_occurrences(clause, allowance)
    net_occurrences = _trusted_value_occurrences(clause, net)
    numeric_spans = tuple(_normalized_number_span(match, clause) for match in PLAIN_CALC_NUMBER_RE.finditer(clause))
    for equals in equals_matches:
        gross_before_equals = [span for span in gross_occurrences if span[1] <= equals.start()]
        allowance_before_equals = [span for span in allowance_occurrences if span[1] <= equals.start()]
        net_after_equals = [span for span in net_occurrences if span[0] >= equals.end()]
        for gross_span in gross_before_equals:
            for allowance_span in allowance_before_equals:
                for net_span in net_after_equals:
                    if not (gross_span[1] <= allowance_span[0] <= equals.start() <= net_span[0]):
                        continue
                    bound_numbers = [gross_span, allowance_span, net_span]
                    equation_numbers = [
                        span for span in numeric_spans if gross_span[0] <= span[0] and span[1] <= net_span[1]
                    ]
                    if equation_numbers != bound_numbers:
                        continue
                    if any(
                        span not in bound_numbers and not _is_inline_reference_or_period_number(clause, span)
                        for span in numeric_spans
                    ):
                        continue
                    between_operands = clause[gross_span[1] : allowance_span[0]]
                    after_allowance = clause[allowance_span[1] : equals.start()]
                    before_net = clause[equals.end() : net_span[0]]
                    if len(re.findall(rf"[{FINANCIAL_MINUS_SIGN_CLASS}]", between_operands)) != 1:
                        continue
                    if re.search(r"[+*/×÷]", between_operands + after_allowance + before_net):
                        continue
                    if re.search(rf"[{FINANCIAL_MINUS_SIGN_CLASS}=＝]", after_allowance + before_net):
                        continue
                    return True
    return False


def _reconciliation_equation_line(
    reply: str,
    gross: Mapping[str, Any],
    allowance: Mapping[str, Any],
    net: Mapping[str, Any],
) -> int | None:
    for line_number, line in enumerate((reply or "").splitlines(), start=1):
        for clause in re.split(r"[；;。]", line):
            if _reconciliation_equation_clause_matches(clause, gross, allowance, net):
                return line_number
    return None


RECONCILIATION_ROLE_TERMS = {
    "gross": ("账面原值", "商誉原值", "原值小计", "gross"),
    "allowance": ("减值准备", "商誉减值", "allowance", "impairment", "provision"),
    "net": ("账面净值", "商誉净值", "商誉净额", "账面净额", "账面价值", "net"),
}


def _reconciliation_fact_line_matches(
    line: str,
    reference: Mapping[str, Any],
    role: str,
) -> bool:
    """Bind one visible fact row to one trusted reconciliation role."""

    if not line.strip() or "source_type=" in line or "schema_version" in line:
        return False
    compact_line = _compact_semantic_text(line)
    role_terms = RECONCILIATION_ROLE_TERMS[role]
    if not any(_compact_semantic_text(term) in compact_line for term in role_terms):
        return False
    if any(
        _compact_semantic_text(term) in compact_line
        for peer_role, terms in RECONCILIATION_ROLE_TERMS.items()
        if peer_role != role
        for term in terms
    ):
        return False
    return bool(_trusted_value_occurrences(line, reference, allow_opposite_sign=role == "allowance"))


def _reconciliation_fact_block_line(
    reply: str,
    gross: Mapping[str, Any],
    allowance: Mapping[str, Any],
    net: Mapping[str, Any],
) -> int | None:
    """Find adjacent, role-bound gross/allowance/net rows in visible answer text."""

    lines = (reply or "").splitlines()
    references = (("gross", gross), ("allowance", allowance), ("net", net))
    matched_lines: list[list[int]] = [[], [], []]
    for line_number, line in enumerate(lines, start=1):
        for index, (role, reference) in enumerate(references):
            if _reconciliation_fact_line_matches(line, reference, role):
                matched_lines[index].append(line_number)

    for gross_line in matched_lines[0]:
        for allowance_line in matched_lines[1]:
            for net_line in matched_lines[2]:
                if (allowance_line, net_line) != (gross_line + 1, gross_line + 2):
                    continue
                fact_rows = lines[gross_line - 1 : net_line]
                is_markdown_table = all("|" in row for row in fact_rows)
                is_adjacent_text = all(row.strip() and not row.lstrip().startswith("#") for row in fact_rows)
                if is_markdown_table or is_adjacent_text:
                    return gross_line
    return None


def _ratio_pair_allowed(numerator: Mapping[str, Any], denominator: Mapping[str, Any]) -> bool:
    if str(numerator.get("evidence_id") or "") == str(denominator.get("evidence_id") or ""):
        return False
    numerator_metric = _trusted_evidence_metric(numerator).lower()
    denominator_metric = _trusted_evidence_metric(denominator).lower()
    if (
        denominator_metric == "goodwill_gross"
        and numerator_metric.startswith("goodwill_component_")
        and not numerator_metric.endswith("_absolute_change")
    ):
        return True
    known_pairs = {
        "gross_margin": ({"gross_profit"}, {"revenue", "operating_revenue", "total_operating_revenue"}),
        "net_margin": ({"net_profit", "net_income", "parent_net_profit"}, {"revenue", "operating_revenue"}),
        "debt_to_asset_ratio": ({"total_liabilities"}, {"total_assets"}),
        "return_on_equity": ({"net_profit", "net_income", "parent_net_profit"}, {"shareholders_equity"}),
        "return_on_assets": ({"net_profit", "net_income", "parent_net_profit"}, {"total_assets"}),
    }
    return any(
        numerator_metric in numerator_metrics and denominator_metric in denominator_metrics
        for numerator_metrics, denominator_metrics in known_pairs.values()
    )


def materialize_evidence_bound_calculation_runs(
    reply: str,
    trusted_evidence: Sequence[Mapping[str, Any]],
    *,
    expected_identity: Mapping[str, Any] | None = None,
    expected_operations: frozenset[str] = frozenset(),
    require_reconciliation: bool = False,
) -> tuple[Mapping[str, Any], ...]:
    """Recompute model-presented calculations from server-resolved source cells.

    The model never supplies evidence IDs or identity for this path.  Those are
    attached from deterministic Wiki/PostgreSQL retrieval results, and every
    source cell must still have a matching visible page/table citation.
    """

    identity = _trace_identity_payload(expected_identity)
    if not identity:
        return ()
    evidence = tuple(
        item
        for item in trusted_evidence
        if isinstance(item, Mapping)
        and item.get("evidence_id")
        and _trusted_evidence_metric(item)
        and _trusted_evidence_period(item)
        and _trusted_evidence_value(item) is not None
        and (item.get("unit") or item.get("currency"))
    )
    if not evidence:
        return ()

    displayed_percentages = _percent_claim_occurrences(reply, require_derived_term=False)
    materialized: list[Mapping[str, Any]] = []
    seen: set[tuple[str, ...]] = set()

    if not expected_operations or "normalize_amount" in expected_operations:
        for claim, reference, fact in _evidence_bound_unit_normalization_claims(reply, evidence):
            tolerance = _display_amount_tolerance(claim.value_text, claim.unit, fact.normalized_value)
            if _claim_fact_value_distance(claim.normalized_value, fact.normalized_value, fact.metric) > tolerance:
                continue
            inputs = {"amount": _trusted_trace_input(reference, "amount")}
            expected = _trace_expected_result("normalize_amount", inputs)
            if expected is None:
                continue
            key = (
                "normalize_amount",
                inputs["amount"]["evidence_id"],
                str(claim.line_number),
                str(claim.match_start),
            )
            if key in seen:
                continue
            seen.add(key)
            materialized.append(
                {
                    "schema_version": CALCULATION_TRACE_SCHEMA,
                    "tool": "financial_calculator.py",
                    "operation": "normalize_amount",
                    "metric": inputs["amount"]["metric"],
                    "period": inputs["amount"]["period"],
                    "inputs": inputs,
                    "result": {
                        "native_base_value": str(expected),
                        "native_100m_value": str(expected / Decimal("100000000")),
                    },
                    "research_identity": identity,
                    "trace_origin": "backend_evidence_recompute",
                    "display_line_number": claim.line_number,
                    "display_match_start": claim.match_start,
                    "display_claim": str(claim.value),
                }
            )

    if not expected_operations or expected_operations.intersection({"yoy", "yoy_growth"}):
        by_metric: dict[str, list[Mapping[str, Any]]] = {}
        for reference in evidence:
            by_metric.setdefault(_trusted_evidence_metric(reference).lower(), []).append(reference)
        for metric_references in by_metric.values():
            ordered = sorted(metric_references, key=_trace_period_sort_key)
            for previous_index, previous in enumerate(ordered):
                for current in ordered[previous_index + 1 :]:
                    if _trusted_evidence_period(previous) == _trusted_evidence_period(current):
                        continue
                    if not _same_source_lineage((previous, current)):
                        continue
                    if not _same_financial_scope((previous, current), require_known=False):
                        continue
                    previous_year = _trace_period_sort_key(previous)[0]
                    current_year = _trace_period_sort_key(current)[0]
                    if previous_year >= 0 and current_year >= 0 and current_year - previous_year != 1:
                        continue
                    inputs = {
                        "current": _trusted_trace_input(current, "current"),
                        "previous": _trusted_trace_input(previous, "previous"),
                    }
                    expected = _trace_expected_result("yoy", inputs)
                    if expected is None:
                        continue
                    matching_claims = _matching_percent_occurrences(
                        displayed_percentages,
                        expected=expected,
                        operation="yoy",
                        primary=current,
                        secondary=previous,
                        all_references=evidence,
                        visible_reply=reply,
                    )
                    for claim in matching_claims:
                        key = (
                            "yoy",
                            inputs["current"]["evidence_id"],
                            inputs["previous"]["evidence_id"],
                            str(claim.line_number),
                            str(claim.match_start),
                        )
                        if key in seen:
                            continue
                        seen.add(key)
                        materialized.append(
                            {
                                "schema_version": CALCULATION_TRACE_SCHEMA,
                                "tool": "financial_calculator.py",
                                "operation": "yoy",
                                "metric": f"{inputs['current']['metric']}_yoy",
                                "period": inputs["current"]["period"],
                                "inputs": inputs,
                                "result": {"rate": str(expected), "percent": str(expected * Decimal("100"))},
                                "research_identity": identity,
                                "trace_origin": "backend_evidence_recompute",
                                "display_line_number": claim.line_number,
                                "display_match_start": claim.match_start,
                                "display_claim": str(claim.value),
                            }
                        )

    if not expected_operations or "ratio" in expected_operations:
        for denominator in evidence:
            for numerator in evidence:
                if _trusted_evidence_period(numerator) != _trusted_evidence_period(denominator):
                    continue
                if not _ratio_scope_compatible((numerator, denominator)):
                    continue
                if not _ratio_pair_allowed(numerator, denominator):
                    continue
                inputs = {
                    "numerator": _trusted_trace_input(numerator, "numerator"),
                    "denominator": _trusted_trace_input(denominator, "denominator"),
                }
                expected = _trace_expected_result("ratio", inputs)
                if expected is None:
                    continue
                output_metric = _ratio_trace_metric(inputs)
                matching_claims = _matching_percent_occurrences(
                    displayed_percentages,
                    expected=expected,
                    operation="ratio",
                    primary=numerator,
                    secondary=denominator,
                    all_references=evidence,
                    output_metric=output_metric,
                    visible_reply=reply,
                )
                for claim in matching_claims:
                    key = (
                        "ratio",
                        inputs["numerator"]["evidence_id"],
                        inputs["denominator"]["evidence_id"],
                        str(claim.line_number),
                        str(claim.match_start),
                    )
                    if key in seen:
                        continue
                    seen.add(key)
                    materialized.append(
                        {
                            "schema_version": CALCULATION_TRACE_SCHEMA,
                            "tool": "financial_calculator.py",
                            "operation": "ratio",
                            "metric": output_metric,
                            "period": inputs["numerator"]["period"],
                            "inputs": inputs,
                            "result": {"ratio": str(expected), "percent": str(expected * Decimal("100"))},
                            "research_identity": identity,
                            "trace_origin": "backend_evidence_recompute",
                            "display_line_number": claim.line_number,
                            "display_match_start": claim.match_start,
                            "display_claim": str(claim.value),
                        }
                    )

    if require_reconciliation:
        gross_records = [item for item in evidence if "goodwill_gross" in _trusted_evidence_metric(item).lower()]
        allowance_records = [
            item
            for item in evidence
            if "goodwill" in _trusted_evidence_metric(item).lower()
            and any(
                token in _trusted_evidence_metric(item).lower() for token in ("allowance", "impairment", "provision")
            )
        ]
        net_records = [item for item in evidence if "goodwill_net" in _trusted_evidence_metric(item).lower()]
        for gross in gross_records:
            for allowance in allowance_records:
                for net in net_records:
                    periods = {_trusted_evidence_period(item) for item in (gross, allowance, net)}
                    if not _same_financial_scope((gross, allowance, net), require_known=True):
                        continue
                    display_line = _reconciliation_equation_line(reply, gross, allowance, net)
                    if display_line is None:
                        display_line = _reconciliation_fact_block_line(reply, gross, allowance, net)
                    if len(periods) != 1 or display_line is None:
                        continue
                    inputs = {
                        "gross": _trusted_trace_input(gross, "gross"),
                        "allowance": _trusted_trace_input(allowance, "allowance"),
                        "net": _trusted_trace_input(net, "net"),
                    }
                    expected = _trace_expected_result("goodwill_reconciliation", inputs)
                    if expected is None:
                        continue
                    key = ("goodwill_reconciliation", inputs["gross"]["evidence_id"], inputs["net"]["evidence_id"])
                    if key in seen:
                        continue
                    seen.add(key)
                    materialized.append(
                        {
                            "schema_version": RECONCILIATION_TRACE_SCHEMA,
                            "tool": "financial_reconciliation_validator.py",
                            "operation": "goodwill_reconciliation",
                            "metric": "goodwill_gross_allowance_net",
                            "period": inputs["net"]["period"],
                            "inputs": inputs,
                            "result": {"net": str(expected)},
                            "status": "passed",
                            "research_identity": identity,
                            "trace_origin": "backend_evidence_recompute",
                            "display_line_number": display_line,
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
    trusted_evidence: Sequence[Mapping[str, Any]] = (),
) -> CalculationTraceValidation:
    materialized_trusted_runs = materialize_runtime_calculation_runs(
        trusted_runs,
        reply,
        expected_identity=expected_identity,
    )
    evidence_bound_runs = materialize_evidence_bound_calculation_runs(
        reply,
        trusted_evidence,
        expected_identity=expected_identity,
        expected_operations=expected_operations,
        require_reconciliation=require_reconciliation,
    )
    runs = extract_structured_calculation_runs(reply) + tuple(materialized_trusted_runs) + tuple(evidence_bound_runs)
    if not (require_calculator or require_reconciliation):
        return CalculationTraceValidation(checked=False, allowed=True, runs=runs)
    if not runs:
        return CalculationTraceValidation(checked=True, allowed=False, reason="trace_unstructured")
    expected = _expected_identity(expected_identity)
    calculator_seen = False
    reconciliation_seen = False
    seen_operations: set[str] = set()
    calculator_results: list[Decimal] = []
    calculator_run_results: list[tuple[Mapping[str, Any], Decimal]] = []
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
        evidence_reason = _trace_evidence_reason(run, reply, trusted_evidence=trusted_evidence)
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
            calculator_run_results.append((run, actual_result))
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
    evidence_bound_results = [
        (run, result)
        for run, result in calculator_run_results
        if str(run.get("trace_origin") or "") == "backend_evidence_recompute"
    ]
    for occurrence in _percent_claim_occurrences(reply, require_derived_term=True):
        # A prose percentage is commonly rounded to one decimal place.  This
        # tolerance is only for binding the displayed claim to an already
        # strictly recomputed trace result; the trace itself remains 1 ppm.
        if evidence_bound_results:
            candidate_results = [
                result
                for run, result in evidence_bound_results
                if int(run.get("display_line_number") or 0) == occurrence.line_number
                and int(run.get("display_match_start") or -1) == occurrence.match_start
            ]
        else:
            candidate_results = calculator_results
        direct_match = any(_percent_occurrence_matches_expected(occurrence, result) for result in candidate_results)
        difference_candidates = (
            [
                result
                for run, result in evidence_bound_results
                if int(run.get("display_line_number") or 0) == occurrence.line_number
            ]
            if evidence_bound_results and occurrence.is_percentage_point
            else candidate_results
        )
        difference_match = occurrence.is_percentage_point and any(
            _percent_occurrence_matches_expected(occurrence, left - right)
            for left in difference_candidates
            for right in difference_candidates
        )
        if not (direct_match or difference_match):
            return CalculationTraceValidation(True, False, "trace_claim_result_mismatch", runs)
    normalization_runs = [run for run in runs if str(run.get("operation") or "").lower() == "normalize_amount"]
    if normalization_runs:
        for claim, reference, fact in _evidence_bound_unit_normalization_claims(reply, trusted_evidence):
            evidence_id = str(reference.get("evidence_id") or "")
            expected_values = [
                _trace_expected_result("normalize_amount", run.get("inputs", {}))
                for run in normalization_runs
                if isinstance(run.get("inputs"), Mapping)
                and isinstance(run.get("inputs", {}).get("amount"), Mapping)
                and str(run.get("inputs", {}).get("amount", {}).get("evidence_id") or "") == evidence_id
            ]
            tolerance = Decimal(str(_display_amount_tolerance(claim.value_text, claim.unit, fact.normalized_value)))
            claimed = Decimal(str(claim.normalized_value))
            if not any(
                expected is not None and _claim_fact_value_distance(claimed, expected, fact.metric) <= tolerance
                for expected in expected_values
            ):
                return CalculationTraceValidation(True, False, "trace_claim_result_mismatch", runs)
    return CalculationTraceValidation(True, True, runs=runs)


def _clean_number(value: Any) -> float | None:
    text = normalize_financial_minus_signs(value).strip().replace(",", "")
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


def _currency_token_from_value(value: Any) -> str:
    text = str(value or "").lower()
    for alias, token in CURRENCY_ALIASES.items():
        if alias.lower() in text:
            return token
    return ""


def _currency_token(*values: Any) -> str:
    """Prefer an explicit currency field over a unit or free-text fallback."""
    for value in values:
        token = _currency_token_from_value(value)
        if token:
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


def _nearest_preceding_period_tokens(text: str, position: int) -> tuple[str, ...]:
    candidates: list[re.Match[str]] = []
    for pattern in (DATE_RE, QUARTER_RE, YEAR_RE):
        candidates.extend(match for match in pattern.finditer(text[:position]) if match.end() <= position)
    if not candidates:
        return ()
    nearest = max(candidates, key=lambda match: (match.end(), match.end() - match.start()))
    return _period_tokens(nearest.group(0))


def _amount_period_tokens(text: str, position: int) -> tuple[str, ...]:
    candidates: list[re.Match[str]] = []
    for pattern in (DATE_RE, QUARTER_RE, YEAR_RE):
        candidates.extend(match for match in pattern.finditer(text[:position]) if match.end() <= position)
    if not candidates:
        return ()
    nearest = max(candidates, key=lambda match: (match.end(), match.end() - match.start()))
    trailing_context = text[nearest.end() : position]
    if any(term in trailing_context for term in ABSOLUTE_CHANGE_CLAIM_TERMS):
        return ()
    return _period_tokens(nearest.group(0))


_RECONCILIATION_ROLE_AFTER_AMOUNT_RE = re.compile(
    r"^\s*(?:(?:人民币)?(?:元|千元|万元|百万元|亿元|thousand|million|billion)\s*)?[（(]\s*"
    r"(?P<role>原值|减值准备|账面价值|账面净值|账面净额|净值|净额)",
    re.IGNORECASE,
)


def _reconciliation_metric_from_suffix(suffix: str) -> str:
    match = _RECONCILIATION_ROLE_AFTER_AMOUNT_RE.match(suffix)
    if match is None:
        return ""
    role = match.group("role")
    if role == "减值准备":
        return "goodwill_impairment_allowance"
    if role == "原值":
        return "goodwill_gross"
    return "goodwill_net"


def _metric_aliases(fact: Mapping[str, Any]) -> tuple[str, ...]:
    aliases: list[str] = []
    for key in ("metric_name", "metric", "canonical_name", "name", "concept", "label"):
        value = str(fact.get(key) or "").strip()
        if value:
            aliases.append(value)
        canonical = re.sub(r"[^a-z0-9_]+", "_", value.lower()).strip("_")
        aliases.extend(CANONICAL_METRIC_ALIASES.get(canonical, ()))
    extra_aliases = fact.get("aliases")
    if isinstance(extra_aliases, Sequence) and not isinstance(extra_aliases, (str, bytes)):
        aliases.extend(str(alias or "").strip() for alias in extra_aliases)
    metric = str(fact.get("metric") or fact.get("canonical_name") or "").lower()
    if "component_sum" in metric:
        for alias in tuple(aliases):
            if "+" not in alias:
                continue
            for connector in ("与", "和", "及"):
                aliases.append(re.sub(r"\s*\+\s*", connector, alias))
    if metric.endswith("_absolute_change"):
        base_aliases: set[str] = set()
        for alias in aliases:
            base = ABSOLUTE_CHANGE_ALIAS_SUFFIX_RE.sub("", alias).strip()
            if base and base not in {"本期", "本年", "绝对"}:
                base_aliases.add(base)
        for base in base_aliases:
            aliases.extend(
                (
                    f"{base}同比",
                    f"{base}增加",
                    f"{base}减少",
                    f"{base}本期增加",
                    f"{base}本期减少",
                    f"{base}本年增加",
                    f"{base}本年减少",
                    f"{base}计提",
                    f"{base}损失",
                    f"{base}本期发生",
                    f"{base}报告期发生",
                )
            )
        aliases.extend(("本期增加", "本期减少", "本年增加", "本年减少"))
    footnote_short_aliases: set[str] = set()
    for alias in tuple(aliases):
        stripped = FOOTNOTE_ALIAS_SUFFIX_RE.sub("", alias).strip()
        compact = re.sub(r"\s+", "", stripped.lower())
        if stripped != alias.strip() and len(compact) >= 2:
            aliases.append(stripped)
            footnote_short_aliases.add(compact)
    compact_seen: set[str] = set()
    result: list[str] = []
    for alias in aliases:
        normalized = alias.strip()
        compact = re.sub(r"\s+", "", normalized.lower())
        if (
            not normalized
            or (
                len(compact) < 3
                and compact not in SAFE_SHORT_METRIC_ALIASES
                and compact not in footnote_short_aliases
            )
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
                    "source_type",
                    "market",
                    "company_id",
                    "filing_id",
                    "parse_run_id",
                    "file",
                    "metric",
                    "metric_name",
                    "canonical_name",
                    "period",
                    "period_key",
                    "value",
                    "raw_value",
                    "unit",
                    "task_id",
                    "pdf_page",
                    "table_index",
                    "md_line",
                )
            )
            reference["evidence_id"] = "auto:" + hashlib.sha256(stable_fields.encode("utf-8")).hexdigest()[:20]
            reference["_generated_evidence_id"] = True
        references.append(reference)
        if len(references) >= 100:
            break
    return references


def _reference_locator(reference: Mapping[str, Any]) -> tuple[str, str, str] | None:
    task_id = str(reference.get("task_id") or "").strip()
    pdf_page = str(reference.get("pdf_page") or reference.get("pdf_page_number") or "").strip()
    table_index = str(reference.get("table_index") or "").strip()
    if task_id and (pdf_page or table_index):
        return task_id, pdf_page, table_index
    return None


def _coalesce_identity_free_reference_duplicates(
    references: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    complete_locators = {
        locator
        for reference in references
        if (locator := _reference_locator(reference)) is not None
        and all(str(reference.get(field) or "").strip() for field in IDENTITY_FIELDS)
    }
    if not complete_locators:
        return references
    return [
        reference
        for reference in references
        if not (
            _reference_locator(reference) in complete_locators
            and not any(str(reference.get(field) or "").strip() for field in IDENTITY_FIELDS)
        )
    ]


def _identity_fields_compatible(reference: Mapping[str, Any], trusted: Mapping[str, Any]) -> bool:
    for field in IDENTITY_FIELDS:
        visible_value = _normalized_identity_value(field, reference.get(field))
        if visible_value and visible_value != _normalized_identity_value(field, trusted.get(field)):
            return False
    return True


def _complete_server_bound_reference_identity(
    reference: Mapping[str, Any],
    expected: Mapping[str, str],
) -> dict[str, Any]:
    """Complete legacy citation identity only when its parser task is exact."""

    output = dict(reference)
    if not expected or str(reference.get("task_id") or "").strip() != expected.get("parse_run_id"):
        return output
    if not _identity_fields_compatible(reference, expected):
        return output
    report_id = str(reference.get("report_id") or "").strip()
    if (
        report_id
        and expected.get("filing_id") not in {report_id, ""}
        and not expected["filing_id"].endswith(f":{report_id}")
    ):
        return output
    for field in IDENTITY_FIELDS:
        if not str(output.get(field) or "").strip():
            output[field] = expected[field]
    output["_identity_completed_from_task_id"] = True
    return output


def _trusted_claim_references(
    visible_references: Sequence[Mapping[str, Any]],
    trusted_evidence: Sequence[Mapping[str, Any]],
    expected: Mapping[str, str],
) -> list[dict[str, Any]]:
    references: list[dict[str, Any]] = []
    for item in trusted_evidence:
        if not isinstance(item, Mapping):
            continue
        if any(not str(item.get(field) or "").strip() for field in IDENTITY_FIELDS):
            continue
        if expected and _identity_violation_reason(item, expected):
            continue
        if not all(item.get(field) not in (None, "") for field in ("evidence_id", "quote", "unit")):
            continue
        if item.get("value", item.get("raw_value")) in (None, ""):
            continue
        matching_visible = [
            reference for reference in visible_references if _trace_visible_locator_matches(item, (reference,))
        ]
        if not matching_visible:
            continue
        source_type = str(item.get("source_type") or "").lower()
        trusted = dict(item)
        trusted["source_type"] = "postgresql_agent_view" if "postgres" in source_type else "wiki_metrics"
        trusted["line_number"] = int(matching_visible[0].get("line_number") or 0)
        trusted["_trusted_backend_evidence"] = True
        references.append(trusted)
    return references


def _references_with_trusted_evidence(
    visible_references: list[dict[str, Any]],
    trusted_references: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Replace model-authored fields at a trusted locator, but retain identity conflicts."""

    remaining: list[dict[str, Any]] = []
    for visible in visible_references:
        matching_trusted = [
            trusted for trusted in trusted_references if _trace_visible_locator_matches(trusted, (visible,))
        ]
        if matching_trusted and any(_identity_fields_compatible(visible, trusted) for trusted in matching_trusted):
            continue
        remaining.append(visible)
    return _coalesce_identity_free_reference_duplicates([*trusted_references, *remaining])


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
        "aliases",
        "name",
        "concept",
        "period",
        "period_key",
        "value",
        "raw_value",
        "change_direction",
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


def _reference_facts(
    reply: str,
    *,
    references: list[dict[str, Any]] | None = None,
) -> tuple[EvidenceFact, ...]:
    facts: list[EvidenceFact] = []
    for reference in references if references is not None else _extract_source_references(reply):
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
                change_direction=str(fact.get("change_direction") or ""),
            )
        )
    return tuple(facts)


def _claim_clauses(line: str) -> tuple[str, ...]:
    parts = tuple(part.strip() for part in CLAUSE_SPLIT_RE.split(line) if part.strip())
    if not parts:
        return (line,)

    clauses: list[str] = []
    subject_context = ""
    for part in parts:
        if subject_context and CLAUSE_CONTINUATION_RE.match(part):
            combined = f"{subject_context} {part}"
            clauses.append(combined)
            subject_context = combined
        else:
            clauses.append(part)
            subject_context = part
    return tuple(clauses)


def _alias_match_score(
    clause: str,
    amount_start: int,
    fact: EvidenceFact,
    *,
    amount_end: int | None = None,
) -> tuple[int, int, int] | None:
    compact_clause_chars: list[str] = []
    compact_source_positions: list[int] = []
    compact_amount_start = 0
    compact_amount_end = 0
    for index, char in enumerate(clause.lower()):
        if char.isalnum() or "\u4e00" <= char <= "\u9fff":
            compact_clause_chars.append(char)
            compact_source_positions.append(index)
            if index < amount_start:
                compact_amount_start += 1
            if amount_end is not None and index < amount_end:
                compact_amount_end += 1
    compact_clause = "".join(compact_clause_chars)
    annotated_bases = {
        _compact_semantic_text(FOOTNOTE_ALIAS_SUFFIX_RE.sub("", alias))
        for alias in fact.aliases
        if FOOTNOTE_ALIAS_SUFFIX_RE.search(alias)
    }
    candidate_aliases = list(fact.aliases)
    if fact.metric.endswith("_absolute_change") and any(term in clause for term in ABSOLUTE_CHANGE_CLAIM_TERMS):
        for alias in fact.aliases:
            base = ABSOLUTE_CHANGE_ALIAS_SUFFIX_RE.sub("", alias).strip()
            if base and base not in {"本期", "本年", "绝对"}:
                candidate_aliases.append(base)
    best: tuple[int, int, int] | None = None
    for alias in candidate_aliases:
        compact_alias = _compact_semantic_text(alias)
        if not compact_alias:
            continue
        start = compact_clause.find(compact_alias)
        while start >= 0:
            end = start + len(compact_alias)
            raw_alias_end = compact_source_positions[end - 1] + 1
            footnote_match = FOOTNOTE_ALIAS_SUFFIX_RE.search(alias)
            if footnote_match and not clause[raw_alias_end:].lstrip().startswith((")", "）")):
                start = compact_clause.find(compact_alias, start + 1)
                continue
            if compact_alias in annotated_bases and re.match(
                r"\s*[（(](?:[ivxlcdm]+|\d+|[一二三四五六七八九十]+)[）)]",
                clause[raw_alias_end:],
                re.IGNORECASE,
            ):
                start = compact_clause.find(compact_alias, start + 1)
                continue
            if end <= compact_amount_start:
                score = (0, compact_amount_start - end, -len(compact_alias))
            else:
                if amount_end is not None and start >= compact_amount_end:
                    raw_alias_start = compact_source_positions[start]
                    between = re.sub(r"[\s*`_'\"“”‘’]+", "", clause[amount_end:raw_alias_start])
                    if between not in {"(", "（", "[", "【"}:
                        start = compact_clause.find(compact_alias, start + 1)
                        continue
                score = (1, start - compact_amount_start, -len(compact_alias))
            if best is None or score < best:
                best = score
            start = compact_clause.find(compact_alias, start + 1)
    return best


def _fact_for_amount(
    clause: str,
    amount_start: int,
    category: str,
    normalized_value: float,
    value_text: str,
    unit: str,
    facts: tuple[EvidenceFact, ...],
    *,
    amount_end: int | None = None,
) -> EvidenceFact | None:
    candidates: list[tuple[tuple[int, int, int], float, int, EvidenceFact]] = []
    for index, fact in enumerate(facts):
        if fact.value_category != category:
            continue
        score = _alias_match_score(clause, amount_start, fact, amount_end=amount_end)
        if score is not None:
            candidates.append(
                (score, _claim_fact_value_distance(normalized_value, fact.normalized_value, fact.metric), index, fact)
            )
    if not candidates:
        return None
    best_semantic_score = min(candidate[0] for candidate in candidates)
    semantic_candidates = [candidate for candidate in candidates if candidate[0] == best_semantic_score]
    amount_prefix = clause[:amount_start]
    balance_position = amount_prefix.rfind("余额")
    change_position = max(
        (_last_term_position(amount_prefix, terms) for terms in (INCREASE_CHANGE_TERMS, DECREASE_CHANGE_TERMS)),
        default=-1,
    )
    if balance_position >= 0 and balance_position > change_position:
        balance_candidates = [
            candidate for candidate in semantic_candidates if not candidate[3].metric.endswith("_absolute_change")
        ]
        balance_metrics = {candidate[3].metric for candidate in balance_candidates}
        if balance_candidates and len(balance_metrics) == 1:
            return min(balance_candidates, key=lambda item: (item[0], item[1], item[2]))[3]
    if any(term in clause for term in ABSOLUTE_CHANGE_CLAIM_TERMS):
        change_candidates = [
            candidate
            for candidate in semantic_candidates
            if candidate[3].metric.endswith("_absolute_change")
            and candidate[1] <= _display_amount_tolerance(value_text, unit, candidate[3].normalized_value)
        ]
        if change_candidates:
            return min(change_candidates, key=lambda item: (item[0], item[1], item[2]))[3]
    best_metrics = {candidate[3].metric for candidate in semantic_candidates}
    if len(best_metrics) != 1:
        return None
    candidates = [candidate for candidate in candidates if candidate[3].metric in best_metrics]
    return min(candidates, key=lambda item: (item[0], item[1], item[2]))[3]


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


def _is_same_metric_unit_restatement(
    clause: str,
    previous_match: re.Match[str],
    current_match: re.Match[str],
) -> bool:
    previous_multiplier = _unit_multiplier(previous_match.group("unit"))
    current_multiplier = _unit_multiplier(current_match.group("unit"))
    if (
        previous_multiplier is None
        or current_multiplier is None
        or previous_multiplier[0] != current_multiplier[0]
        or previous_multiplier[1] == current_multiplier[1]
    ):
        return False
    connector = re.sub(
        r"[\s*`_'\"“”‘’]+",
        "",
        clause[previous_match.end() : current_match.start()],
    ).lower()
    if not connector:
        return False
    return bool(
        re.fullmatch(
            r"[，,；;:]?[（(\[【]?(?:约(?:为)?|折合(?:为)?|换算(?:为|成)?|相当于|即|≈|~|=|＝|→|->)?",
            connector,
        )
    )


def _last_term_position(text: str, terms: Sequence[str]) -> int:
    return max((text.rfind(term) for term in terms), default=-1)


def _claim_change_direction(clause: str, match: re.Match[str]) -> str:
    value_text = normalize_financial_minus_signs(match.group("value")).lstrip()
    value = _clean_number(value_text)
    explicit_direction = ""
    if value is not None and value != 0:
        if value_text.startswith("-"):
            explicit_direction = "decrease"
        elif value_text.startswith("+"):
            explicit_direction = "increase"

    prefix = clause[: match.start()]
    segment_start = max((prefix.rfind(marker) for marker in ("，", ",", "；", ";", "。")), default=-1)
    context = prefix[segment_start + 1 :]
    directional_context = context
    for term in NEGATED_CHANGE_TERMS:
        directional_context = directional_context.replace(term, " " * len(term))
    positions = {
        "increase": _last_term_position(directional_context, INCREASE_CHANGE_TERMS),
        "decrease": _last_term_position(directional_context, DECREASE_CHANGE_TERMS),
        "unchanged": _last_term_position(context, UNCHANGED_CHANGE_TERMS),
    }
    best_position = max(positions.values())
    textual_direction = (
        next((direction for direction, position in positions.items() if position == best_position), "")
        if best_position >= 0
        else ""
    )
    if explicit_direction and textual_direction and explicit_direction != textual_direction:
        return "conflict"
    return explicit_direction or textual_direction


def _extract_claims(
    reply: str,
    facts: tuple[EvidenceFact, ...],
) -> tuple[NumericClaim, ...]:
    claims: list[NumericClaim] = []
    seen: set[tuple[int, str, float, str]] = set()
    for line_number, raw_line in enumerate((reply or "").splitlines(), start=1):
        line = raw_line.strip()
        if not line or "source_type=" in line or line.startswith("guardrail_") or line.startswith("claim_verifier_"):
            continue
        for clause in _claim_clauses(line):
            matches = list(NUMBER_WITH_UNIT_RE.finditer(clause))
            parallel_facts = _parallel_fact_assignments(clause, matches, facts)
            resolved_facts: list[EvidenceFact | None] = [None] * len(matches)
            for match_index, match in enumerate(matches):
                clause_period_tokens = _amount_period_tokens(clause, match.start())
                previous_match = matches[match_index - 1] if match_index else None
                value = _amount_match_number(clause, match, previous_match)
                unit = match.group("unit")
                normalized = _normalized_amount(value, unit)
                if normalized is None:
                    continue
                normalized_value, category = normalized
                fact = parallel_facts[match_index] if parallel_facts is not None else None
                if fact is None:
                    local_start = matches[match_index - 1].end() if match_index else 0
                    local_end = matches[match_index + 1].start() if match_index + 1 < len(matches) else len(clause)
                    local_clause = clause[local_start:local_end]
                    local_amount_start = match.start() - local_start
                    local_amount_end = match.end() - local_start
                    reconciliation_metric = _reconciliation_metric_from_suffix(clause[match.end() : match.end() + 32])
                    if reconciliation_metric:
                        reconciliation_facts = [
                            item
                            for item in facts
                            if item.metric == reconciliation_metric and item.value_category == category
                        ]
                        if clause_period_tokens:
                            period_facts = [
                                item
                                for item in reconciliation_facts
                                if not _period_tokens(item.period)
                                or set(clause_period_tokens).intersection(_period_tokens(item.period))
                            ]
                            if period_facts:
                                reconciliation_facts = period_facts
                        if reconciliation_facts:
                            fact = min(
                                reconciliation_facts,
                                key=lambda item: _claim_fact_value_distance(
                                    normalized_value,
                                    item.normalized_value,
                                    item.metric,
                                ),
                            )
                    if fact is None:
                        fact = _fact_for_amount(
                            local_clause,
                            local_amount_start,
                            category,
                            normalized_value,
                            match.group("value"),
                            unit,
                            facts,
                            amount_end=local_amount_end,
                        )
                if (
                    fact is None
                    and match_index > 0
                    and resolved_facts[match_index - 1] is not None
                    and _is_same_metric_unit_restatement(clause, matches[match_index - 1], match)
                ):
                    fact = resolved_facts[match_index - 1]
                if fact is None and any(term in clause for term in ABSOLUTE_CHANGE_CLAIM_TERMS):
                    change_facts = tuple(item for item in facts if item.metric.endswith("_absolute_change"))
                    fact = _fact_for_amount(
                        clause,
                        match.start(),
                        category,
                        normalized_value,
                        match.group("value"),
                        unit,
                        change_facts,
                        amount_end=match.end(),
                    )
                resolved_facts[match_index] = fact
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
                        value_text=match.group("value"),
                        unit=unit,
                        normalized_value=normalized_value,
                        value_category=category,
                        currency=_currency_token(match.group("currency"), clause, line),
                        period_tokens=clause_period_tokens,
                        period_text=_period_text(clause_period_tokens),
                        line_number=line_number,
                        line=line,
                        match_start=match.start(),
                        change_direction=_claim_change_direction(clause, match),
                    )
                )
    return tuple(claims)


def _evidence_bound_unit_normalization_claims(
    reply: str,
    trusted_evidence: Sequence[Mapping[str, Any]],
) -> tuple[tuple[NumericClaim, Mapping[str, Any], EvidenceFact], ...]:
    visible_references = _extract_source_references(reply)
    reference_by_evidence_id: dict[str, Mapping[str, Any]] = {}
    fact_references: list[dict[str, Any]] = []
    for item in trusted_evidence:
        if not isinstance(item, Mapping) or not item.get("evidence_id"):
            continue
        if not _trace_visible_locator_matches(item, visible_references):
            continue
        reference = dict(item)
        source_type = str(reference.get("source_type") or "").lower()
        if not (source_type.startswith("wiki") or source_type.startswith("postgres")):
            reference["source_type"] = "wiki_metrics"
        evidence_id = str(reference.get("evidence_id") or "")
        reference_by_evidence_id[evidence_id] = item
        fact_references.append(reference)
    if not fact_references:
        return ()

    facts = _reference_facts(reply, references=fact_references)
    claims = _extract_claims(reply, facts)
    bindings: list[tuple[NumericClaim, Mapping[str, Any], EvidenceFact]] = []
    seen: set[tuple[int, int, str]] = set()
    for claim in claims:
        local_suffix = claim.line[
            claim.match_start + len(claim.value_text) : claim.match_start + len(claim.value_text) + 32
        ]
        reconciliation_metric = _reconciliation_metric_from_suffix(local_suffix)
        candidates: list[tuple[float, EvidenceFact, Mapping[str, Any]]] = []
        for fact in facts:
            expected_metric = reconciliation_metric or claim.metric
            if fact.metric != expected_metric or fact.value_category != claim.value_category:
                continue
            if claim.period_tokens:
                fact_tokens = _period_tokens(fact.period)
                if fact_tokens and not set(claim.period_tokens).intersection(fact_tokens):
                    continue
            reference = reference_by_evidence_id.get(fact.evidence_id)
            if reference is None:
                continue
            source_scale = _normalized_amount(1, fact.unit, scale=reference.get("scale"))
            claim_scale = _normalized_amount(1, claim.unit)
            if (
                source_scale is None
                or claim_scale is None
                or source_scale[1] != claim_scale[1]
                or math.isclose(source_scale[0], claim_scale[0], rel_tol=0.0, abs_tol=1e-12)
            ):
                continue
            candidates.append(
                (
                    _claim_fact_value_distance(claim.normalized_value, fact.normalized_value, fact.metric),
                    fact,
                    reference,
                )
            )
        if not candidates:
            continue
        _distance, fact, reference = min(candidates, key=lambda item: item[0])
        key = (claim.line_number, claim.match_start, fact.evidence_id)
        if key in seen:
            continue
        seen.add(key)
        bindings.append((claim, reference, fact))
    return tuple(bindings)


def has_evidence_bound_unit_normalization(
    reply: str,
    trusted_evidence: Sequence[Mapping[str, Any]],
) -> bool:
    """Return whether a visible amount restates trusted evidence in another unit."""

    return bool(_evidence_bound_unit_normalization_claims(reply, trusted_evidence))


def _change_direction_matches(claim: NumericClaim, fact: EvidenceFact) -> bool:
    if not fact.metric.endswith("_absolute_change"):
        return True
    if claim.change_direction == "conflict":
        return False
    if claim.change_direction:
        return bool(fact.change_direction) and claim.change_direction == fact.change_direction
    return True


def _matches_evidence(claim: NumericClaim, fact: EvidenceFact) -> bool:
    if claim.metric != fact.metric or claim.value_category != fact.value_category:
        return False
    if not _change_direction_matches(claim, fact):
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
    tolerance = _display_amount_tolerance(claim.value_text, claim.unit, fact.normalized_value)
    return _claim_fact_value_distance(claim.normalized_value, fact.normalized_value, fact.metric) <= tolerance


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
    if not _change_direction_matches(claim, fact):
        return "direction_mismatch"
    tolerance = _display_amount_tolerance(claim.value_text, claim.unit, fact.normalized_value)
    if _claim_fact_value_distance(claim.normalized_value, fact.normalized_value, fact.metric) > tolerance:
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
            reference.get("canonical_name") or reference.get("metric_name") or reference.get("metric") or "unknown"
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
    trusted_evidence: Sequence[Mapping[str, Any]] = (),
    validated_calculation_lines: frozenset[int] = frozenset(),
) -> ClaimVerificationResult:
    expected = _expected_identity(expected_identity)
    visible_references = [
        _complete_server_bound_reference_identity(reference, expected)
        for reference in _extract_source_references(reply)
    ]
    trusted_references = _trusted_claim_references(visible_references, trusted_evidence, expected)
    references = _references_with_trusted_evidence(visible_references, trusted_references)
    facts = _reference_facts(reply, references=references)
    violations: list[ClaimViolation] = []
    if expected:
        for reference in references:
            source_type = str(reference.get("source_type") or "")
            if not (
                source_type.startswith("wiki") or source_type.startswith("postgres") or source_type == "postgresql"
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
    # Calculation validation is additive. Skipping a whole validated line would
    # let an unrelated or contradictory amount on that line bypass fact checks.
    claims = _extract_claims(reply, facts)
    for claim in claims:
        candidates = [
            fact for fact in facts if fact.metric == claim.metric and fact.value_category == claim.value_category
        ]
        if not candidates or any(_matches_evidence(claim, fact) for fact in candidates):
            continue
        nearest = min(
            candidates,
            key=lambda fact: _claim_fact_value_distance(claim.normalized_value, fact.normalized_value, fact.metric),
        )
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
