#!/usr/bin/env python3
"""Deterministic financial calculator for SIQ agents.

The script is intentionally dependency-free and Decimal-based.  Hermes agents
should use it for derived financial numbers instead of doing mental arithmetic
in model text.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, getcontext
from typing import Any

getcontext().prec = 36

HUNDRED_MILLION = Decimal("100000000")
TEN_THOUSAND = Decimal("10000")
ONE = Decimal("1")

CURRENCY_LABELS = {
    "CNY": "人民币",
    "USD": "美元",
    "EUR": "欧元",
    "HKD": "港元",
    "JPY": "日元",
    "GBP": "英镑",
    "CHF": "瑞士法郎",
    "CAD": "加拿大元",
    "AUD": "澳元",
    "SGD": "新加坡元",
}

CURRENCY_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("HKD", ("HKD", "HK$", "港元", "港币")),
    ("USD", ("USD", "US$", "美元", "美金", "$", "DOLLAR", "DOLLARS")),
    ("EUR", ("EUR", "EURO", "EUROS", "欧元", "€")),
    ("JPY", ("JPY", "日元", "日圆")),
    ("GBP", ("GBP", "英镑", "£")),
    ("CHF", ("CHF", "瑞士法郎")),
    ("CAD", ("CAD", "加拿大元")),
    ("AUD", ("AUD", "澳元")),
    ("SGD", ("SGD", "新加坡元")),
    ("CNY", ("CNY", "CNH", "RMB", "人民币", "¥", "元")),
)

UNIT_PATTERNS: tuple[tuple[tuple[str, ...], Decimal], ...] = (
    (("万亿元", "万亿", "兆元", "TRILLION"), Decimal("1000000000000")),
    (("十亿元", "BILLION", "BN"), Decimal("1000000000")),
    (("亿元", "亿", "100M"), HUNDRED_MILLION),
    (("百万元", "百万", "MILLION", "MN"), Decimal("1000000")),
    (("万元", "万"), TEN_THOUSAND),
    (("千元", "THOUSAND"), Decimal("1000")),
    (("元",), ONE),
)

COUNT_UNIT_PATTERNS: tuple[tuple[tuple[str, ...], Decimal], ...] = (
    (("亿人",), HUNDRED_MILLION),
    (("万人",), TEN_THOUSAND),
    (("千人",), Decimal("1000")),
    (("百人",), Decimal("100")),
    (("人",), ONE),
    (("亿股",), HUNDRED_MILLION),
    (("万股",), TEN_THOUSAND),
    (("千股",), Decimal("1000")),
    (("股",), ONE),
    (("亿户",), HUNDRED_MILLION),
    (("万户",), TEN_THOUSAND),
    (("户",), ONE),
)


class CalculatorError(ValueError):
    pass


@dataclass(frozen=True)
class MoneyAmount:
    raw_value: Decimal
    raw_unit: str
    currency: str
    unit_scale: Decimal
    base_value: Decimal

    @property
    def native_100m(self) -> Decimal:
        return self.base_value / HUNDRED_MILLION


def as_decimal(value: Any, field_name: str = "value") -> Decimal:
    if isinstance(value, Decimal):
        return value
    if value is None:
        raise CalculatorError(f"{field_name} is required")
    text = str(value).strip()
    if not text:
        raise CalculatorError(f"{field_name} is empty")
    text = text.replace("−", "-").replace("–", "-")
    parenthesized_match = re.search(r"[（(]\s*([-+]?\d[\d,，]*(?:\.\d+)?)\s*[）)]", text)
    negative_by_parentheses = parenthesized_match is not None
    number_source = parenthesized_match.group(1) if parenthesized_match else text
    number_source = number_source.strip("()（）")
    number_source = number_source.replace(",", "").replace("，", "").replace(" ", "")
    match = re.search(r"[-+]?\d+(?:\.\d+)?", number_source)
    if not match:
        raise CalculatorError(f"{field_name} is not numeric: {value!r}")
    number_text = match.group(0)
    try:
        number = Decimal(number_text)
    except InvalidOperation as exc:
        raise CalculatorError(f"{field_name} is not numeric: {value!r}") from exc
    if negative_by_parentheses:
        return -abs(number)
    return number


def plain(value: Decimal | None) -> str | None:
    if value is None:
        return None
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def fixed(value: Decimal | None, digits: int = 2) -> str:
    if value is None:
        return "N/A"
    quantum = Decimal("1").scaleb(-digits)
    return format(value.quantize(quantum, rounding=ROUND_HALF_UP), f".{digits}f")


def normalize_currency(currency: str | None, unit: str = "", default: str = "CNY") -> str:
    explicit = str(currency or "").strip().upper()
    if explicit:
        for code, aliases in CURRENCY_ALIASES:
            if explicit == code or explicit in {alias.upper() for alias in aliases}:
                return code
        return explicit

    text = f"{unit or ''}".upper()
    for code, aliases in CURRENCY_ALIASES:
        if any(alias.upper() in text for alias in aliases):
            return code
    return default


def currency_label(currency: str) -> str:
    return CURRENCY_LABELS.get(currency.upper(), currency.upper())


def scale_from_unit(unit: str | None, *, count: bool = False) -> Decimal:
    text = str(unit or "").strip()
    if not text:
        return ONE
    compact = re.sub(r"\s+", "", text).upper()
    patterns = COUNT_UNIT_PATTERNS if count else UNIT_PATTERNS
    for aliases, scale in patterns:
        if any(unit_alias_matches(compact, alias) for alias in aliases):
            return scale
    return ONE


def unit_alias_matches(compact_unit: str, alias: str) -> bool:
    alias_upper = alias.upper()
    if not alias_upper:
        return False
    if re.fullmatch(r"[A-Z0-9$]+", alias_upper):
        return bool(re.search(rf"(?<![A-Z0-9$]){re.escape(alias_upper)}(?![A-Z0-9$])", compact_unit))
    return alias_upper in compact_unit


def money_unit_name(currency: str, *, hundred_million: bool = False) -> str:
    currency = currency.upper()
    if currency == "CNY":
        return "亿元" if hundred_million else "元"
    prefix = "亿" if hundred_million else ""
    return f"{prefix}{currency_label(currency)}"


def ten_thousand_unit_name(currency: str) -> str:
    if currency.upper() == "CNY":
        return "万元"
    return f"万{currency_label(currency)}"


def build_money(value: Any, unit: str | None, currency: str | None = None) -> MoneyAmount:
    raw_value = as_decimal(value, "amount")
    raw_unit = str(unit or "元")
    resolved_currency = normalize_currency(currency, raw_unit)
    scale = scale_from_unit(raw_unit)
    return MoneyAmount(
        raw_value=raw_value,
        raw_unit=raw_unit,
        currency=resolved_currency,
        unit_scale=scale,
        base_value=raw_value * scale,
    )


def build_count(value: Any, unit: str | None = None) -> tuple[Decimal, Decimal, str]:
    raw_value = as_decimal(value, "count")
    raw_unit = str(unit or "人")
    scale = scale_from_unit(raw_unit, count=True)
    base_count = raw_value * scale
    if base_count <= 0:
        raise CalculatorError("count denominator must be positive")
    return raw_value, base_count, raw_unit


def fx_decimal(value: Any) -> Decimal | None:
    if value is None or str(value).strip() == "":
        return None
    fx = as_decimal(value, "fx_to_cny")
    if fx <= 0:
        raise CalculatorError("fx_to_cny must be positive")
    return fx


def cny_base_value(money: MoneyAmount, fx_to_cny: Decimal | None) -> Decimal | None:
    if money.currency == "CNY":
        return money.base_value
    if fx_to_cny is None:
        return None
    return money.base_value * fx_to_cny


def fx_warnings(currency: str, fx_to_cny: Decimal | None, fx_date: str = "", fx_source: str = "") -> list[str]:
    warnings: list[str] = []
    if currency != "CNY" and fx_to_cny is None:
        warnings.append("non-CNY amount kept in native currency; provide fx-to-cny to produce CNY results")
    if currency != "CNY" and fx_to_cny is not None and not fx_date:
        warnings.append("fx-to-cny is supplied without fx-date")
    if currency != "CNY" and fx_to_cny is not None and not fx_source:
        warnings.append("fx-to-cny is supplied without fx-source")
    return warnings


def money_payload(money: MoneyAmount, fx_to_cny: Decimal | None) -> dict[str, Any]:
    cny_base = cny_base_value(money, fx_to_cny)
    return {
        "raw_value": plain(money.raw_value),
        "raw_unit": money.raw_unit,
        "currency": money.currency,
        "unit_scale": plain(money.unit_scale),
        "native_base_value": plain(money.base_value),
        "native_base_unit": money_unit_name(money.currency),
        "native_100m_value": plain(money.native_100m),
        "native_100m_unit": money_unit_name(money.currency, hundred_million=True),
        "cny_base_value": plain(cny_base),
        "cny_base_unit": "元" if cny_base is not None else None,
        "cny_100m_value": plain(cny_base / HUNDRED_MILLION) if cny_base is not None else None,
        "cny_100m_unit": "亿元" if cny_base is not None else None,
    }


def normalize_amount(args: argparse.Namespace) -> dict[str, Any]:
    money = build_money(args.value, args.unit, args.currency)
    fx = fx_decimal(args.fx_to_cny)
    warnings = fx_warnings(money.currency, fx, args.fx_date, args.fx_source)
    payload = money_payload(money, fx)
    display = f"{fixed(money.native_100m, 2)} {payload['native_100m_unit']}"
    if payload["cny_100m_value"] is not None and money.currency != "CNY":
        display += f"（约 {fixed(Decimal(payload['cny_100m_value']), 2)} 亿元）"
    return {
        "status": "ok",
        "operation": "normalize_amount",
        "input": {
            "value": str(args.value),
            "unit": args.unit,
            "currency": args.currency or money.currency,
            "fx_to_cny": plain(fx),
            "fx_date": args.fx_date or None,
            "fx_source": args.fx_source or None,
        },
        "result": payload,
        "display": display,
        "formula": [
            f"{money.raw_value} * {plain(money.unit_scale)} = {plain(money.base_value)} {money_unit_name(money.currency)}",
            f"{plain(money.base_value)} / 100000000 = {plain(money.native_100m)} {money_unit_name(money.currency, hundred_million=True)}",
        ],
        "warnings": warnings,
    }


def reported_checks(expected: dict[str, Decimal | None], args: argparse.Namespace) -> list[dict[str, Any]]:
    specs = (
        ("native_per", getattr(args, "reported_native_per", None)),
        ("native_10k_per", getattr(args, "reported_native_10k", None)),
        ("cny_per", getattr(args, "reported_cny_per", None)),
        ("cny_10k_per", getattr(args, "reported_cny_10k", None)),
    )
    checks: list[dict[str, Any]] = []
    for name, raw_reported in specs:
        if raw_reported in (None, ""):
            continue
        target = expected.get(name)
        if target is None:
            checks.append({"name": name, "status": "not_applicable", "reported": str(raw_reported)})
            continue
        reported = as_decimal(raw_reported, f"reported_{name}")
        diff = abs(reported - target)
        tolerance = max(Decimal("0.01"), abs(target) * Decimal("0.0005"))
        checks.append(
            {
                "name": name,
                "status": "pass" if diff <= tolerance else "fail",
                "reported": plain(reported),
                "expected": plain(target),
                "difference": plain(diff),
                "tolerance": plain(tolerance),
            }
        )
    return checks


def per_capita(args: argparse.Namespace) -> dict[str, Any]:
    money = build_money(args.amount, args.amount_unit, args.currency)
    raw_count, count, count_unit = build_count(args.count, args.count_unit)
    fx = fx_decimal(args.fx_to_cny)
    cny_base = cny_base_value(money, fx)
    native_per = money.base_value / count
    native_10k = native_per / TEN_THOUSAND
    cny_per = cny_base / count if cny_base is not None else None
    cny_10k = cny_per / TEN_THOUSAND if cny_per is not None else None
    warnings = fx_warnings(money.currency, fx, args.fx_date, args.fx_source)
    result = {
        "amount": money_payload(money, fx),
        "count": {
            "raw_value": plain(raw_count),
            "raw_unit": count_unit,
            "base_count": plain(count),
        },
        "native_per": plain(native_per),
        "native_per_unit": f"{money_unit_name(money.currency)}/{base_count_label(count_unit)}",
        "native_10k_per": plain(native_10k),
        "native_10k_per_unit": f"{ten_thousand_unit_name(money.currency)}/{base_count_label(count_unit)}",
        "cny_per": plain(cny_per),
        "cny_per_unit": f"元/{base_count_label(count_unit)}" if cny_per is not None else None,
        "cny_10k_per": plain(cny_10k),
        "cny_10k_per_unit": f"万元/{base_count_label(count_unit)}" if cny_10k is not None else None,
    }
    display = f"{fixed(native_per, 2)} {result['native_per_unit']}（{fixed(native_10k, 4)} {result['native_10k_per_unit']}）"
    if cny_per is not None and money.currency != "CNY":
        display += f"，约 {fixed(cny_per, 2)} 元/{base_count_label(count_unit)}（{fixed(cny_10k, 4)} 万元/{base_count_label(count_unit)}）"
    checks = reported_checks(
        {
            "native_per": native_per,
            "native_10k_per": native_10k,
            "cny_per": cny_per,
            "cny_10k_per": cny_10k,
        },
        args,
    )
    return {
        "status": "ok",
        "operation": "per_capita",
        "input": {
            "amount": str(args.amount),
            "amount_unit": args.amount_unit,
            "currency": args.currency or money.currency,
            "count": str(args.count),
            "count_unit": count_unit,
            "fx_to_cny": plain(fx),
            "fx_date": args.fx_date or None,
            "fx_source": args.fx_source or None,
        },
        "result": result,
        "display": display,
        "formula": per_capita_formula(money, count, count_unit, native_per, native_10k, fx, cny_per, cny_10k),
        "checks": checks,
        "warnings": warnings,
    }


def base_count_label(unit: str) -> str:
    text = str(unit or "人")
    if "股" in text:
        return "股"
    if "户" in text:
        return "户"
    return "人"


def per_capita_formula(
    money: MoneyAmount,
    count: Decimal,
    count_unit: str,
    native_per: Decimal,
    native_10k: Decimal,
    fx: Decimal | None,
    cny_per: Decimal | None,
    cny_10k: Decimal | None,
) -> list[str]:
    label = base_count_label(count_unit)
    lines = [
        f"{money.raw_value} {money.raw_unit} = {plain(money.base_value)} {money_unit_name(money.currency)}",
        f"{plain(money.base_value)} / {plain(count)} = {fixed(native_per, 6)} {money_unit_name(money.currency)}/{label}",
        f"{fixed(native_per, 6)} / 10000 = {fixed(native_10k, 6)} {ten_thousand_unit_name(money.currency)}/{label}",
    ]
    if fx is not None and cny_per is not None and cny_10k is not None and money.currency != "CNY":
        lines.append(
            f"{fixed(native_per, 6)} * {plain(fx)} = {fixed(cny_per, 6)} 元/{label} = {fixed(cny_10k, 6)} 万元/{label}"
        )
    return lines


def comparable_bases(
    left: MoneyAmount,
    right: MoneyAmount,
    left_fx: Decimal | None,
    right_fx: Decimal | None,
) -> tuple[Decimal | None, Decimal | None, str, list[str]]:
    warnings: list[str] = []
    if left.currency == right.currency:
        return left.base_value, right.base_value, money_unit_name(left.currency), warnings
    left_cny = cny_base_value(left, left_fx)
    right_cny = cny_base_value(right, right_fx)
    if left_cny is None or right_cny is None:
        warnings.append("currencies differ; provide both fx rates to compare in CNY")
        return None, None, "元", warnings
    return left_cny, right_cny, "元", warnings


def ratio(args: argparse.Namespace) -> dict[str, Any]:
    numerator = build_money(args.numerator, args.numerator_unit, args.numerator_currency or args.currency)
    denominator = build_money(args.denominator, args.denominator_unit, args.denominator_currency or args.currency)
    numerator_fx = fx_decimal(args.numerator_fx_to_cny or args.fx_to_cny)
    denominator_fx = fx_decimal(args.denominator_fx_to_cny or args.fx_to_cny)
    left, right, unit, warnings = comparable_bases(numerator, denominator, numerator_fx, denominator_fx)
    warnings.extend(fx_warnings(numerator.currency, numerator_fx, args.fx_date, args.fx_source))
    warnings.extend(fx_warnings(denominator.currency, denominator_fx, args.fx_date, args.fx_source))
    if left is None or right is None:
        return {"status": "fx_required", "operation": "ratio", "warnings": warnings}
    if right == 0:
        return {"status": "division_by_zero", "operation": "ratio", "warnings": warnings}
    value = left / right
    return {
        "status": "ok",
        "operation": "ratio",
        "result": {"ratio": plain(value), "percent": plain(value * Decimal("100")), "percent_unit": "%"},
        "display": f"{fixed(value * Decimal('100'), 2)}%",
        "formula": [f"{plain(left)} {unit} / {plain(right)} {unit} = {plain(value)} = {plain(value * Decimal('100'))}%"],
        "warnings": list(dict.fromkeys(warnings)),
    }


def yoy(args: argparse.Namespace) -> dict[str, Any]:
    current = build_money(args.current, args.current_unit, args.current_currency or args.currency)
    previous = build_money(args.previous, args.previous_unit, args.previous_currency or args.currency)
    current_fx = fx_decimal(args.current_fx_to_cny or args.fx_to_cny)
    previous_fx = fx_decimal(args.previous_fx_to_cny or args.fx_to_cny)
    cur, prev, unit, warnings = comparable_bases(current, previous, current_fx, previous_fx)
    warnings.extend(fx_warnings(current.currency, current_fx, args.fx_date, args.fx_source))
    warnings.extend(fx_warnings(previous.currency, previous_fx, args.fx_date, args.fx_source))
    if cur is None or prev is None:
        return {"status": "fx_required", "operation": "yoy", "warnings": warnings}
    if prev == 0:
        return {"status": "not_applicable", "operation": "yoy", "reason": "previous value is zero", "warnings": warnings}
    delta = cur - prev
    if prev < 0 and not args.allow_negative_base:
        return {
            "status": "not_applicable",
            "operation": "yoy",
            "reason": "previous value is negative; use absolute delta or describe turnround instead of a normal growth rate",
            "result": {
                "delta": plain(delta),
                "delta_unit": unit,
            },
            "formula": [f"{plain(cur)} - {plain(prev)} = {plain(delta)} {unit}"],
            "warnings": list(dict.fromkeys(warnings)),
        }
    rate = delta / abs(prev)
    return {
        "status": "ok",
        "operation": "yoy",
        "result": {
            "delta": plain(delta),
            "delta_unit": unit,
            "rate": plain(rate),
            "percent": plain(rate * Decimal("100")),
            "percent_unit": "%",
        },
        "display": f"{fixed(rate * Decimal('100'), 2)}%",
        "formula": [f"({plain(cur)} - {plain(prev)}) / abs({plain(prev)}) = {plain(rate)} = {plain(rate * Decimal('100'))}%"],
        "warnings": list(dict.fromkeys(warnings)),
    }


def cagr(args: argparse.Namespace) -> dict[str, Any]:
    start = build_money(args.start, args.start_unit, args.start_currency or args.currency)
    end = build_money(args.end, args.end_unit, args.end_currency or args.currency)
    periods = as_decimal(args.periods, "periods")
    if periods <= 0:
        return {"status": "not_applicable", "operation": "cagr", "reason": "periods must be positive"}
    start_fx = fx_decimal(args.start_fx_to_cny or args.fx_to_cny)
    end_fx = fx_decimal(args.end_fx_to_cny or args.fx_to_cny)
    start_base, end_base, unit, warnings = comparable_bases(start, end, start_fx, end_fx)
    if start_base is None or end_base is None:
        return {"status": "fx_required", "operation": "cagr", "warnings": warnings}
    if start_base <= 0 or end_base <= 0:
        return {
            "status": "not_applicable",
            "operation": "cagr",
            "reason": "CAGR requires positive start and end values",
            "warnings": warnings,
        }
    # Decimal has no fractional power. Use float only after base-unit
    # normalization; keep the inputs and formula explicit.
    rate = Decimal(str((float(end_base / start_base) ** (1 / float(periods))) - 1))
    return {
        "status": "ok",
        "operation": "cagr",
        "result": {"rate": plain(rate), "percent": plain(rate * Decimal("100")), "percent_unit": "%"},
        "display": f"{fixed(rate * Decimal('100'), 2)}%",
        "formula": [f"({plain(end_base)} {unit} / {plain(start_base)} {unit})^(1/{plain(periods)}) - 1 = {plain(rate)}"],
        "warnings": warnings,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    lines = ["## 计算器校验", f"- 状态：{payload.get('status')}"]
    if payload.get("error"):
        lines.append(f"- 错误：{payload['error']}")
    if payload.get("reason"):
        lines.append(f"- 原因：{payload['reason']}")
    if payload.get("display"):
        lines.append(f"- 结果：{payload['display']}")
    if payload.get("formula"):
        lines.append("- 公式：")
        lines.extend(f"  - {item}" for item in payload["formula"])
    checks = payload.get("checks") or []
    if checks:
        lines.append("- 报告值复核：")
        for item in checks:
            expected = item.get("expected")
            if expected not in (None, ""):
                try:
                    expected = fixed(as_decimal(expected, "expected"), 6)
                except CalculatorError:
                    pass
            lines.append(
                f"  - {item.get('name')}: {item.get('status')}，reported={item.get('reported')}，expected={expected}"
            )
    warnings = payload.get("warnings") or []
    if warnings:
        lines.append("- 警告：")
        lines.extend(f"  - {item}" for item in warnings)
    return "\n".join(lines)


def add_common_fx_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--fx-to-cny", default="")
    parser.add_argument("--fx-date", default="")
    parser.add_argument("--fx-source", default="")


def add_subcommand_format_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--format", choices=("json", "markdown"), dest="sub_format")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SIQ deterministic financial calculator")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("normalize", help="normalize an amount to native 100m units and optional CNY 100m")
    add_subcommand_format_arg(p)
    p.add_argument("--value", required=True)
    p.add_argument("--unit", default="元")
    p.add_argument("--currency", default="")
    add_common_fx_args(p)
    p.set_defaults(func=normalize_amount)

    p = sub.add_parser("per-capita", help="amount divided by a count denominator")
    add_subcommand_format_arg(p)
    p.add_argument("--amount", required=True)
    p.add_argument("--amount-unit", default="元")
    p.add_argument("--currency", default="")
    p.add_argument("--count", required=True)
    p.add_argument("--count-unit", default="人")
    p.add_argument("--reported-native-per", default="")
    p.add_argument("--reported-native-10k", default="")
    p.add_argument("--reported-cny-per", default="")
    p.add_argument("--reported-cny-10k", default="")
    add_common_fx_args(p)
    p.set_defaults(func=per_capita)

    p = sub.add_parser("ratio", help="numerator divided by denominator")
    add_subcommand_format_arg(p)
    p.add_argument("--numerator", required=True)
    p.add_argument("--numerator-unit", default="元")
    p.add_argument("--numerator-currency", default="")
    p.add_argument("--denominator", required=True)
    p.add_argument("--denominator-unit", default="元")
    p.add_argument("--denominator-currency", default="")
    p.add_argument("--currency", default="")
    p.add_argument("--numerator-fx-to-cny", default="")
    p.add_argument("--denominator-fx-to-cny", default="")
    add_common_fx_args(p)
    p.set_defaults(func=ratio)

    p = sub.add_parser("yoy", help="year-on-year or period-over-period change")
    add_subcommand_format_arg(p)
    p.add_argument("--current", required=True)
    p.add_argument("--current-unit", default="元")
    p.add_argument("--current-currency", default="")
    p.add_argument("--previous", required=True)
    p.add_argument("--previous-unit", default="元")
    p.add_argument("--previous-currency", default="")
    p.add_argument("--currency", default="")
    p.add_argument("--current-fx-to-cny", default="")
    p.add_argument("--previous-fx-to-cny", default="")
    p.add_argument("--allow-negative-base", action="store_true")
    add_common_fx_args(p)
    p.set_defaults(func=yoy)

    p = sub.add_parser("cagr", help="compound annual growth rate")
    add_subcommand_format_arg(p)
    p.add_argument("--start", required=True)
    p.add_argument("--start-unit", default="元")
    p.add_argument("--start-currency", default="")
    p.add_argument("--end", required=True)
    p.add_argument("--end-unit", default="元")
    p.add_argument("--end-currency", default="")
    p.add_argument("--periods", required=True)
    p.add_argument("--currency", default="")
    p.add_argument("--start-fx-to-cny", default="")
    p.add_argument("--end-fx-to-cny", default="")
    add_common_fx_args(p)
    p.set_defaults(func=cagr)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        payload = args.func(args)
    except CalculatorError as exc:
        payload = {"status": "error", "operation": args.command, "error": str(exc)}
    output_format = args.sub_format or args.format
    if output_format == "markdown":
        print(render_markdown(payload))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 1 if payload.get("status") == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
