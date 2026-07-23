"""Deterministic SEC/XBRL dimensional facts used by financial Q&A."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

ReadJsonFile = Callable[[Path], Any | None]

US_REVENUE_CONCEPT = "us-gaap:Revenues"
GEOGRAPHICAL_AXIS = "srt:StatementGeographicalAxis"
GEOGRAPHICAL_TEXT_CONCEPT = (
    "us-gaap:ScheduleOfRevenuesFromExternalCustomersAndLongLivedAssetsByGeographicalAreasTableTextBlock"
)
_REVENUE_TERMS = ("营收", "收入", "revenue", "sales")
_GEOGRAPHICAL_TERMS = (
    "地区",
    "地域",
    "地理",
    "区域",
    "美国市场",
    "美国地区",
    "国家",
    "分布",
    "geographic",
    "geographical",
    "region",
    "country",
    "united states",
    "us market",
)
_MEMBER_LABELS = {
    "country:US": ("美国", "United States"),
    "country:TW": ("中国台湾", "Taiwan"),
    "country:CN": ("中国", "China"),
    "country:HK": ("中国香港", "Hong Kong"),
    "nvda:ChinaIncludingHongKongMember": ("中国（含香港）", "China (including Hong Kong)"),
    "nvda:OtherCountriesMember": ("其他", "Other"),
}


def geographical_revenue_query_applies(message: str | None) -> bool:
    """Return whether a question asks for revenue split by geography."""

    text = re.sub(r"\s+", " ", str(message or "")).casefold()
    return bool(
        text
        and any(term.casefold() in text for term in _REVENUE_TERMS)
        and any(term.casefold() in text for term in _GEOGRAPHICAL_TERMS)
    )


def _decimal(value: Any) -> Decimal | None:
    text = str(value or "").strip().replace(",", "")
    if not text:
        return None
    try:
        result = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def _period_matches(fact: Mapping[str, Any], *, period_end: str, fiscal_year: Any) -> bool:
    fact_period = str(fact.get("period_end") or "").strip()
    if period_end and fact_period and fact_period != period_end:
        return False
    if fiscal_year not in (None, "") and fact.get("fiscal_year") not in (None, ""):
        try:
            if int(fact["fiscal_year"]) != int(fiscal_year):
                return False
        except (TypeError, ValueError):
            return False
    # A geography revenue fact is an annual duration fact, not an instant fact.
    return bool(fact.get("period_start") or fact.get("duration_days"))


def _member_display(member: str) -> tuple[str, str]:
    if member in _MEMBER_LABELS:
        return _MEMBER_LABELS[member]
    suffix = member.rsplit(":", 1)[-1]
    suffix = re.sub(r"Member$", "", suffix)
    suffix = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", suffix).strip()
    return suffix or member, suffix or member


def _source_url(company_dir: Path, report_id: str, read_json_file: ReadJsonFile) -> str:
    manifest = read_json_file(company_dir / "reports" / report_id / "manifest.json")
    if isinstance(manifest, Mapping):
        return str(manifest.get("source_url") or "").strip()
    metadata = read_json_file(company_dir / "reports" / report_id / "raw" / "filing.metadata.json")
    return str(metadata.get("source_url") or "").strip() if isinstance(metadata, Mapping) else ""


def resolve_us_geographical_revenue(
    company_dir: Path,
    report_id: str,
    *,
    read_json_file: ReadJsonFile,
) -> dict[str, Any] | None:
    """Load annual revenue facts carrying the SEC geographical dimension."""

    manifest = read_json_file(company_dir / "reports" / report_id / "manifest.json")
    if not isinstance(manifest, Mapping) or str(manifest.get("market") or "").upper() != "US":
        return None
    facts_path = company_dir / "reports" / report_id / "xbrl" / "facts_raw.json"
    payload = read_json_file(facts_path)
    facts = payload.get("facts") if isinstance(payload, Mapping) else payload
    if not isinstance(facts, list):
        return None
    period_end = str(manifest.get("period_end") or "").strip()
    fiscal_year = manifest.get("fiscal_year")
    source_url = _source_url(company_dir, report_id, read_json_file)
    rows: list[dict[str, Any]] = []
    for fact in facts:
        if not isinstance(fact, Mapping):
            continue
        if str(fact.get("concept") or "") != US_REVENUE_CONCEPT:
            continue
        if not _period_matches(fact, period_end=period_end, fiscal_year=fiscal_year):
            continue
        dimensions = fact.get("dimensions")
        if not isinstance(dimensions, Mapping) or GEOGRAPHICAL_AXIS not in dimensions:
            continue
        member = str(dimensions.get(GEOGRAPHICAL_AXIS) or "").strip()
        value = _decimal(fact.get("value_numeric"))
        if not member or value is None:
            continue
        label_zh, label_en = _member_display(member)
        metric = f"regional_revenue_{member.rsplit(':', 1)[-1].casefold()}"
        anchor = str(fact.get("html_anchor") or ((fact.get("raw") or {}).get("id") if isinstance(fact.get("raw"), Mapping) else "") or "").strip()
        raw_snippet = fact.get("raw") if isinstance(fact.get("raw"), Mapping) else {}
        rows.append(
            {
                "region": label_zh,
                "region_en": label_en,
                "member": member,
                "metric": metric,
                "metric_name": f"{label_zh}地区营收",
                "canonical_name": metric,
                "period": period_end,
                "period_key": period_end,
                "value": str(value),
                "raw_value": str(value),
                "unit": str(fact.get("unit") or "USD").strip() or "USD",
                "currency": "USD",
                "scale": fact.get("scale"),
                "evidence_id": str(fact.get("fact_id") or "").strip(),
                "quote": str(fact.get("value_text") or raw_snippet.get("html_snippet") or "").strip(),
                "source_type": "sec_xbrl_fact",
                "evidence_source_type": "sec_xbrl_fact",
                "source_url": source_url,
                "source_anchor": anchor,
                "xbrl_tag": US_REVENUE_CONCEPT,
                "dimensions": dict(dimensions),
            }
        )
    if not rows:
        return None
    # Stable ordering makes the prompt, citations and audit output reproducible.
    rows.sort(key=lambda item: (0 if item["member"] == "country:US" else 1, item["region_en"]))
    basis: dict[str, Any] | None = None
    for fact in facts:
        if not isinstance(fact, Mapping) or str(fact.get("concept") or "") != GEOGRAPHICAL_TEXT_CONCEPT:
            continue
        if _period_matches(fact, period_end=period_end, fiscal_year=fiscal_year):
            raw = fact.get("raw") if isinstance(fact.get("raw"), Mapping) else {}
            basis = {
                "quote": str(fact.get("value_text") or "").strip(),
                "source_anchor": str(fact.get("html_anchor") or raw.get("id") or "").strip(),
                "evidence_id": str(fact.get("fact_id") or "").strip(),
            }
            break
    return {
        "status": "ok",
        "market": "US",
        "company_id": str(manifest.get("company_id") or "").strip(),
        "filing_id": str(manifest.get("filing_id") or "").strip(),
        "parse_run_id": str(manifest.get("parse_run_id") or "").strip(),
        "report_id": report_id,
        "period": period_end,
        "fiscal_year": fiscal_year,
        "source_url": source_url,
        "facts_file": str(facts_path.relative_to(company_dir)),
        "basis": basis or {},
        "rows": rows,
    }


def render_us_geographical_revenue_context(result: Mapping[str, Any]) -> str:
    """Render a model-facing, source-bound geography revenue block."""

    rows = result.get("rows") if isinstance(result.get("rows"), list) else []
    lines = [
        "以下是后端从 SEC/XBRL `facts_raw.json` 读取的地区营收事实；该数据按客户总部所在地统计，不等同于最终用户或发货地。",
        "不得回答“未提供地区细分”；正文中的地区金额必须逐字采用下表。",
        f"- 公司身份: company_id={result.get('company_id')} / filing_id={result.get('filing_id')} / parse_run_id={result.get('parse_run_id')}",
        f"- 期间: {result.get('period')} / 单位: USD / 文件: {result.get('facts_file')}",
        "",
        "| 客户总部所在地 | 原始 USD | billion USD | 亿美元 | SEC anchor |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for row in rows:
        value = _decimal(row.get("value")) or Decimal("0")
        lines.append(
            f"| {row.get('region')} | {value:,.0f} USD | {value / Decimal('1000000000'):.3f} billion USD | "
            f"{value / Decimal('100000000'):,.2f} 亿美元 | {row.get('source_anchor') or '未返回'} |"
        )
    basis = result.get("basis") if isinstance(result.get("basis"), Mapping) else {}
    if basis.get("quote"):
        lines.extend(["", f"- 地区口径原文：{basis['quote']}"])
    lines.extend(
        [
            "",
            "## 地区营收引用要求",
            "每个地区金额必须在唯一的 `## 引用来源` 中使用 `source_type=sec_xbrl_fact`、`file=xbrl/facts_raw.json`、`source_url`、`source_anchor`、`xbrl_tag`、`value` 和 `evidence_id` 回链。",
        ]
    )
    return "\n".join(lines)


__all__ = [
    "geographical_revenue_query_applies",
    "render_us_geographical_revenue_context",
    "resolve_us_geographical_revenue",
]
