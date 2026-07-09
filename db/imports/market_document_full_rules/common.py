from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


def stable_id(*parts: Any, prefix: str | None = None, length: int = 24) -> str:
    text = "|".join(str(part or "") for part in parts)
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:length]
    return f"{prefix}_{digest}" if prefix else digest


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as infile:
        for chunk in iter(lambda: infile.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def compact_text(value: Any, limit: int = 600) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        value = " ".join(compact_text(item, 120) for item in value[:10])
    elif isinstance(value, dict):
        for key in ("text", "content", "title", "caption", "preview", "value"):
            if key in value:
                value = value.get(key)
                break
        else:
            value = json.dumps(value, ensure_ascii=False)
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text[:limit]


def as_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def as_decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    text = str(value).strip()
    if not text or text in {"-", "—", "–", "N/A", "n/a"}:
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    text = text.replace(",", "").replace("，", "").replace("%", "")
    text = re.sub(r"\s+", "", text)
    try:
        parsed = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    return -parsed if negative else parsed


def as_date_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text).date().isoformat()
    except ValueError:
        return text[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", text) else text


def normalize_scale(value: Any, unit: Any = None) -> Decimal | None:
    parsed = as_decimal(value)
    if parsed is not None:
        return parsed
    unit_text = str(unit or "").lower()
    if any(token in unit_text for token in ("billion", "bn", "十亿")):
        return Decimal("1000000000")
    if any(token in unit_text for token in ("million", "mn", "百万", "百万円", "백만")):
        return Decimal("1000000")
    if any(token in unit_text for token in ("thousand", "千", "천")):
        return Decimal("1000")
    if any(token in unit_text for token in ("万元", "萬元")):
        return Decimal("10000")
    return Decimal("1") if unit_text else None


_CURRENCY_ALIASES = {
    "HK$": "HKD",
    "HKD": "HKD",
    "港币": "HKD",
    "港幣": "HKD",
    "RMB": "CNY",
    "人民币": "CNY",
    "人民幣": "CNY",
    "CNY": "CNY",
    "JPY": "JPY",
    "円": "JPY",
    "日元": "JPY",
    "KRW": "KRW",
    "원": "KRW",
    "韩元": "KRW",
    "EUR": "EUR",
    "€": "EUR",
    "GBP": "GBP",
    "£": "GBP",
    "CHF": "CHF",
    "USD": "USD",
    "$": "USD",
    "SEK": "SEK",
    "DKK": "DKK",
    "NOK": "NOK",
    "PLN": "PLN",
}


def infer_currency(*values: Any) -> str | None:
    haystack = " ".join(str(value or "") for value in values)
    for token, currency in _CURRENCY_ALIASES.items():
        if token and token in haystack:
            return currency
    match = re.search(r"\b([A-Z]{3})\b", haystack)
    if match and match.group(1) in set(_CURRENCY_ALIASES.values()):
        return match.group(1)
    return None


COMMON_CORE_ALIASES = {
    "revenue": {
        "revenue",
        "operating_revenue",
        "营业收入",
        "營業收入",
        "売上収益",
        "매출액",
        "ifrs-full:revenue",
        "us-gaap:revenues",
    },
    "net_profit": {
        "net_profit",
        "profit_for_the_year",
        "profit_loss",
        "净利润",
        "淨利潤",
        "当期利益",
        "당기순이익",
        "ifrs-full:profitloss",
        "us-gaap:netincomeloss",
    },
    "total_assets": {"total_assets", "资产总计", "資產總額", "資産合計", "자산총계", "ifrs-full:assets", "us-gaap:assets"},
    "total_liabilities": {"total_liabilities", "负债合计", "負債總額", "負債合計", "부채총계", "ifrs-full:liabilities"},
    "total_equity": {"total_equity", "equity", "权益合计", "權益總額", "資本合計", "자본총계", "ifrs-full:equity"},
    "operating_cash_flow": {
        "operating_cash_flow",
        "operating_cash_flow_net",
        "ifrs-full:cashflowsfromusedinoperatingactivities",
        "ifrs-full:netcashflowsfromusedinoperatingactivities",
        "us-gaap:netcashprovidedbyusedinoperatingactivities",
        "经营活动产生的现金流量净额",
        "經營活動產生的現金流量淨額",
        "営業活動によるキャッシュ・フロー",
        "영업활동현금흐름",
    },
    "basic_eps": {"basic_eps", "basic earnings per share", "基本每股收益", "基本的1株当たり利益", "기본주당이익"},
    "diluted_eps": {"diluted_eps", "diluted earnings per share", "稀释每股收益", "希薄化後1株当たり利益", "희석주당이익"},
    "gross_profit": {"gross_profit", "gross profit", "毛利", "売上総利益", "매출총이익"},
    "operating_profit": {"operating_profit", "operating income", "营业利润", "營業利潤", "営業利益", "영업이익"},
}


def canonical_common_name(*names: Any) -> tuple[str | None, str]:
    normalized_names = {str(name or "").strip() for name in names if str(name or "").strip()}
    lowered = {name.lower().replace(" ", "").replace("_", "") for name in normalized_names}
    for canonical, aliases in COMMON_CORE_ALIASES.items():
        for alias in aliases:
            alias_key = alias.lower().replace(" ", "").replace("_", "")
            if alias in normalized_names or alias_key in lowered:
                return canonical, "common_core"
    return None, "unmapped"
