#!/usr/bin/env python3
"""Build a market and valuation snapshot for SIQ reports.

This script is deliberately source-conservative. It never invents a stock
price. It reads optional local market snapshots when present, combines them
with annual-report anchors such as revenue, equity, EPS and inferred share
count, and emits a checkpoint that downstream sections can use.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any


MARKET_CANDIDATES = [
    "market/latest.json",
    "market/manual_snapshot.json",
    "market/market_snapshot.json",
    "analysis/market_snapshot.json",
]


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return load_json(path)


def dump_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def finite(value: Any) -> float | None:
    if isinstance(value, (int, float)) and math.isfinite(value):
        return float(value)
    try:
        parsed = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def metric(snapshot: dict[str, Any], key: str) -> dict[str, Any]:
    metrics = snapshot.get("metrics")
    if isinstance(metrics, dict) and isinstance(metrics.get(key), dict):
        return metrics[key]
    return {}


def metric_value(snapshot: dict[str, Any], key: str, year: int) -> float | None:
    item = metric(snapshot, key)
    values = item.get("values") if isinstance(item.get("values"), dict) else {}
    return finite(values.get(str(year)))


def key_metric_value(company_dir: Path, key: str, year: int) -> float | None:
    payload = load_json_if_exists(company_dir / "metrics" / "key_metrics.json")
    for item in payload.get("data", []) or []:
        if not isinstance(item, dict):
            continue
        item_key = item.get("canonical_name") or item.get("metric_key") or item.get("metric_name")
        if item_key != key:
            continue
        values = item.get("values") if isinstance(item.get("values"), dict) else {}
        return finite(values.get(str(year)))
    return None


def key_metric_source(company_dir: Path, key: str, year: int) -> dict[str, Any]:
    payload = load_json_if_exists(company_dir / "metrics" / "key_metrics.json")
    for item in payload.get("data", []) or []:
        if not isinstance(item, dict):
            continue
        item_key = item.get("canonical_name") or item.get("metric_key") or item.get("metric_name")
        if item_key != key:
            continue
        sources = item.get("sources") if isinstance(item.get("sources"), dict) else {}
        source = sources.get(str(year)) if isinstance(sources.get(str(year)), dict) else {}
        return {
            "file": "metrics/key_metrics.json",
            "table_index": source.get("table_index"),
            "line": source.get("line"),
            "metric_key": key,
        }
    return {}


def load_local_market(company_dir: Path, extra_market_path: Path | None = None) -> tuple[dict[str, Any], list[str]]:
    candidates: list[Path] = []
    if extra_market_path:
        candidates.append(extra_market_path)
    candidates.extend(company_dir / item for item in MARKET_CANDIDATES)
    for path in candidates:
        payload = load_json_if_exists(path)
        if payload:
            return payload, [str(path)]
    return {}, []


def pick_market_value(market: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        if key in market:
            return market.get(key)
    data = market.get("data")
    if isinstance(data, dict):
        for key in keys:
            if key in data:
                return data.get(key)
    quote = market.get("quote")
    if isinstance(quote, dict):
        for key in keys:
            if key in quote:
                return quote.get(key)
    return None


def infer_share_count_yi(parent_profit_yi: float | None, basic_eps_yuan: float | None) -> float | None:
    if parent_profit_yi is None or basic_eps_yuan is None or basic_eps_yuan == 0:
        return None
    shares = parent_profit_yi * 100_000_000 / basic_eps_yuan
    if shares <= 0:
        return None
    return shares / 100_000_000


def safe_ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def fmt(value: float | None, suffix: str = "") -> str:
    if value is None:
        return "未返回"
    return f"{value:.2f}{suffix}"


def build_interpretation(result: dict[str, Any]) -> list[str]:
    market = result["market"]
    anchors = result["annual_report_anchors"]
    valuation = result["valuation"]
    lines = [
        f"年报估值锚：收入 {fmt(anchors.get('revenue_yi'), '亿元')}，归母净资产 {fmt(anchors.get('parent_equity_yi'), '亿元')}，归母净利润 {fmt(anchors.get('parent_net_profit_yi'), '亿元')}。",
        f"每股收益为 {fmt(anchors.get('basic_eps_yuan'), '元/股')}，据归母净利润/EPS 推算加权股本约 {fmt(anchors.get('inferred_weighted_shares_yi'), '亿股')}；该股本用于校验，不替代交易所股本口径。",
    ]
    if market.get("share_price"):
        lines.append(
            f"市场快照日期 {market.get('as_of_date') or '未返回'}，股价 {fmt(market.get('share_price'), '元')}，市值 {fmt(market.get('market_cap_yi'), '亿元')}。"
        )
        lines.append(
            f"P/B={fmt(valuation.get('pb'))}，P/S={fmt(valuation.get('ps'))}；净利润为负时 P/E={valuation.get('pe_status')}。"
        )
    else:
        lines.append("未发现本地股价/市值快照；P/B、P/S、市值分位和市场预期差仍不能做确定性判断。")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--company-dir", required=True, type=Path)
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--year", type=int, default=2025)
    parser.add_argument("--market-json", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    company = load_json_if_exists(args.company_dir / "company.json")
    work_dir = args.work_dir or args.company_dir / "analysis" / ".work"
    snapshot = load_json_if_exists(work_dir / "metric_snapshot.json")

    revenue_yi = metric_value(snapshot, "operating_revenue", args.year)
    parent_equity_yi = metric_value(snapshot, "equity_attributable_parent", args.year)
    parent_profit_yi = metric_value(snapshot, "parent_net_profit", args.year)
    basic_eps_yuan = key_metric_value(args.company_dir, "basic_eps", args.year)
    deducted_basic_eps_yuan = key_metric_value(args.company_dir, "deducted_basic_eps", args.year)
    interest_expense_yi = metric_value(snapshot, "interest_expense", args.year)
    inferred_shares_yi = infer_share_count_yi(parent_profit_yi, basic_eps_yuan)

    market_payload, market_sources = load_local_market(args.company_dir, args.market_json)
    share_price = finite(pick_market_value(market_payload, ["share_price", "close", "price", "latest_price"]))
    market_cap_yi = finite(pick_market_value(market_payload, ["market_cap_yi", "total_market_cap_yi", "market_value_yi"]))
    shares_outstanding_yi = finite(pick_market_value(market_payload, ["shares_outstanding_yi", "total_shares_yi", "share_count_yi"]))
    as_of_date = pick_market_value(market_payload, ["as_of_date", "trade_date", "date"])
    source_name = pick_market_value(market_payload, ["source", "source_name"])

    if market_cap_yi is None and share_price is not None:
        share_base = shares_outstanding_yi or inferred_shares_yi
        if share_base is not None:
            market_cap_yi = share_price * share_base

    pb = safe_ratio(market_cap_yi, parent_equity_yi)
    ps = safe_ratio(market_cap_yi, revenue_yi)
    pe = safe_ratio(market_cap_yi, parent_profit_yi)
    pe_status = "not_applicable_negative_profit" if parent_profit_yi is not None and parent_profit_yi <= 0 else ("computed" if pe is not None else "missing")

    result: dict[str, Any] = {
        "schema_version": 1,
        "generated_by": "market_snapshot_builder.py",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "company_id": company.get("company_id") or args.company_dir.name,
        "stock_code": company.get("stock_code"),
        "report_year": args.year,
        "strict_ok": market_cap_yi is not None and (pb is not None or ps is not None),
        "market": {
            "as_of_date": as_of_date,
            "share_price": share_price,
            "shares_outstanding_yi": shares_outstanding_yi,
            "market_cap_yi": market_cap_yi,
            "source": source_name,
            "source_files": market_sources,
        },
        "annual_report_anchors": {
            "revenue_yi": revenue_yi,
            "parent_equity_yi": parent_equity_yi,
            "parent_net_profit_yi": parent_profit_yi,
            "basic_eps_yuan": basic_eps_yuan,
            "deducted_basic_eps_yuan": deducted_basic_eps_yuan,
            "interest_expense_yi": interest_expense_yi,
            "inferred_weighted_shares_yi": inferred_shares_yi,
            "anchor_sources": {
                "basic_eps": key_metric_source(args.company_dir, "basic_eps", args.year),
                "deducted_basic_eps": key_metric_source(args.company_dir, "deducted_basic_eps", args.year),
                "interest_expense": metric(snapshot, "interest_expense").get("sources", {}).get(str(args.year), {}),
            },
        },
        "valuation": {
            "pb": pb,
            "ps": ps,
            "pe": pe if pe_status == "computed" else None,
            "pe_status": pe_status,
            "valuation_percentile": None,
            "consensus_expectation": None,
        },
        "warnings": [],
    }
    if not result["strict_ok"]:
        result["warnings"].append("market_price_or_market_cap_missing")
    if interest_expense_yi is None:
        result["warnings"].append("interest_expense_missing")
    result["interpretation"] = build_interpretation(result)

    output = args.output or work_dir / "market_snapshot.json"
    dump_json(output, result)
    print(json.dumps({"ok": result["strict_ok"], "output": str(output), "warnings": result["warnings"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
