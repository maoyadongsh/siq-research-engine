from __future__ import annotations

import html
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any


_EMPTY_VALUES = {"", "-", "--", "---", "n/a", "na", "null", "none", "not applicable"}


def normalize_label(value: Any) -> str:
    text = html.unescape(str(value or "")).strip().lower()
    text = text.replace("\u3000", " ")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[\s\r\n\t]+", " ", text)
    text = text.replace("（", "(").replace("）", ")")
    text = text.replace("，", ",").replace("：", ":")
    text = re.sub(r"[\u200b\u200c\u200d]", "", text)
    return text.strip()


def compact_label(value: Any) -> str:
    text = normalize_label(value)
    return re.sub(r"[^0-9a-z\u3040-\u30ff\u4e00-\u9fff\uac00-\ud7af]+", "", text)


def normalize_concept(value: Any) -> str:
    text = str(value or "").strip()
    if ":" in text:
        text = text.rsplit(":", 1)[-1]
    return re.sub(r"[^0-9a-z]+", "", text.lower())


def parse_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))

    text = html.unescape(str(value)).strip()
    if normalize_label(text) in _EMPTY_VALUES:
        return None

    negative = False
    if re.match(r"^\s*[△▲]", text):
        negative = True
        text = re.sub(r"^\s*[△▲]\s*", "", text)
    if re.fullmatch(r"\(.+\)", text):
        negative = True
        text = text[1:-1]

    text = text.replace("\u2212", "-").replace("\u2013", "-").replace("\u2014", "-")
    text = re.sub(r"^\s*(?:[*＊※]\s*\d+[A-Za-z]?|[†‡]+)\s*", "", text)
    text = re.sub(r"(?i)^\s*(?:note|注)\s*\d+[:：.)）]?\s*", "", text)
    text = re.sub(r"(?i)(hk\$|us\$|rmb|cny|hkd|usd|eur|gbp|chf|jpy|krw|yen|\$|£|€|元|港元|美元)", "", text)
    text = re.sub(r"(?i)(million|billion|thousand|mn|bn|m|k)", "", text)
    text = re.sub(r"(百万円|百万元|百萬|百万|千円|千元|億円|亿元|億元|円)", "", text)
    if re.search(r"[A-Za-z\u3040-\u30ff\u4e00-\u9fff\uac00-\ud7af]", text):
        return None
    text = text.replace(",", "").replace("'", "").replace("%", "").strip()
    text = re.sub(r"[^0-9.\-+]", "", text)
    if text in _EMPTY_VALUES or text in {"-", "+", ".", "-.", "+."}:
        return None

    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    return -number if negative and number > 0 else number


def parse_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if not value:
        return None
    text = str(value).strip()
    for pattern in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            pass
    match = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", text)
    if match:
        year, month, day = (int(part) for part in match.groups())
        try:
            return date(year, month, day)
        except ValueError:
            return None
    jp_matches = re.findall(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", text)
    for parts in reversed(jp_matches):
        year, month, day = (int(part) for part in parts)
        try:
            return date(year, month, day)
        except ValueError:
            continue
    return None


def period_key(period_end: date | None, fiscal_year: int | None = None) -> str:
    if period_end:
        return period_end.isoformat()
    if fiscal_year:
        return str(fiscal_year)
    return "unknown"


def infer_scale(unit: str | None) -> Decimal:
    text = normalize_label(unit)
    if not text:
        return Decimal("1")
    if (
        "100 million" in text
        or "hundred million" in text
        or "억원" in text
        or "억 원" in text
        or "億円" in text
    ):
        return Decimal("100000000")
    if "billion" in text or "十亿" in text or "十億" in text:
        return Decimal("1000000000")
    if (
        "million" in text
        or "백만원" in text
        or "백만 원" in text
        or "百万円" in text
        or "百万元" in text
        or "百萬" in text
        or "百万" in text
    ):
        return Decimal("1000000")
    if (
        "thousand" in text
        or "천원" in text
        or "천 원" in text
        or "千円" in text
        or "千元" in text
        or "千港元" in text
        or "千美元" in text
    ):
        return Decimal("1000")
    return Decimal("1")


def infer_currency(*values: str | None, default: str | None = None) -> str | None:
    text = " ".join(normalize_label(value) for value in values if value)
    if not text:
        return default
    if "hk$" in text or "hkd" in text or "港元" in text:
        return "HKD"
    if "us$" in text or "usd" in text or "美元" in text:
        return "USD"
    if "rmb" in text or "cny" in text or "人民币" in text or "人民幣" in text:
        return "CNY"
    if "eur" in text or "€" in text:
        return "EUR"
    if "gbp" in text or "£" in text or "sterling" in text:
        return "GBP"
    if "chf" in text or "swiss franc" in text:
        return "CHF"
    if "jpy" in text or "yen" in text or "日元" in text:
        return "JPY"
    if "krw" in text or "won" in text or "韩元" in text or "韓元" in text:
        return "KRW"
    return default


def stable_slug(*parts: Any, length: int = 16) -> str:
    import hashlib

    raw = "|".join(str(part or "") for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length]
