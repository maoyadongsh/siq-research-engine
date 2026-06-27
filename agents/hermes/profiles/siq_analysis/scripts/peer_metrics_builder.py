#!/usr/bin/env python3
"""Build a deterministic peer metrics snapshot for SIQ reports."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any


METRIC_KEYS = [
    "operating_revenue",
    "parent_net_profit",
    "deducted_parent_net_profit",
    "operating_cash_flow_net",
    "total_assets",
    "total_liabilities",
    "equity_attributable_parent",
    "operating_cost",
    "weighted_avg_roe",
]

ALIASES = {
    "net_profit_parent": "parent_net_profit",
    "net_operating_cash_flow": "operating_cash_flow_net",
    "monetary_funds": "monetary_capital",
}

AUTO_PEER_KEYWORDS = ("汽车", "整车", "乘用车", "新能源")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return load_json(path)


def dump_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def value_to_yi(value: Any, unit: str | None) -> Any:
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    unit = str(unit or "").strip()
    if unit in {"元", "人民币元", "CNY"}:
        return value / 100_000_000
    if unit == "万元":
        return value / 10_000
    return value


def normalize_key(key: Any) -> str:
    return ALIASES.get(str(key or ""), str(key or ""))


def merge_metric(target: dict[str, Any], key: str, values: dict[str, Any], unit: str, source: str) -> None:
    key = normalize_key(key)
    if not key:
        return
    item = target.setdefault(key, {"values": {}, "unit": "亿元", "sources": []})
    for year, value in values.items():
        normalized = value_to_yi(value, unit)
        if normalized is not None:
            item["values"][str(year)] = normalized
    if source not in item["sources"]:
        item["sources"].append(source)


def read_company_metrics(company_dir: Path) -> dict[str, dict[str, Any]]:
    metrics: dict[str, Any] = {}
    key_metrics = load_json_if_exists(company_dir / "metrics" / "key_metrics.json")
    for item in key_metrics.get("data", []) or []:
        if not isinstance(item, dict):
            continue
        values = item.get("values") if isinstance(item.get("values"), dict) else {}
        merge_metric(
            metrics,
            item.get("canonical_name") or item.get("metric_key") or item.get("metric_name"),
            values,
            str(item.get("unit") or "亿元"),
            "metrics/key_metrics.json",
        )
    three = load_json_if_exists(company_dir / "metrics" / "three_statements.json")
    for item in three.get("data", {}).get("metrics", []) or []:
        if not isinstance(item, dict):
            continue
        period = str(item.get("period") or "")
        year = period[:4] if period else "2025"
        value = item.get("normalized_value")
        if isinstance(value, (int, float)):
            merge_metric(
                metrics,
                item.get("metric_key") or item.get("metric_name"),
                {year: value},
                "亿元",
                "metrics/three_statements.json",
            )
    return metrics


def metric_value(metrics: dict[str, Any], key: str, year: int) -> Any:
    item = metrics.get(normalize_key(key)) or {}
    values = item.get("values") if isinstance(item.get("values"), dict) else {}
    return values.get(str(year))


def calc_yoy(metrics: dict[str, Any], key: str, year: int) -> Any:
    current = metric_value(metrics, key, year)
    previous = metric_value(metrics, key, year - 1)
    if isinstance(current, (int, float)) and isinstance(previous, (int, float)) and previous:
        return (current - previous) / abs(previous) * 100
    return None


def safe_ratio(numerator: Any, denominator: Any, multiplier: float = 1.0) -> Any:
    if isinstance(numerator, (int, float)) and isinstance(denominator, (int, float)) and denominator:
        return numerator / denominator * multiplier
    return None


def company_row(company: dict[str, Any], company_dir: Path, year: int) -> dict[str, Any]:
    metrics = read_company_metrics(company_dir)
    revenue = metric_value(metrics, "operating_revenue", year)
    cost = metric_value(metrics, "operating_cost", year)
    profit = metric_value(metrics, "parent_net_profit", year)
    deducted_profit = metric_value(metrics, "deducted_parent_net_profit", year)
    ocf = metric_value(metrics, "operating_cash_flow_net", year)
    assets = metric_value(metrics, "total_assets", year)
    liabilities = metric_value(metrics, "total_liabilities", year)
    equity = metric_value(metrics, "equity_attributable_parent", year)
    gross_margin = None
    if isinstance(revenue, (int, float)) and revenue and isinstance(cost, (int, float)):
        gross_margin = (revenue - cost) / revenue * 100
    debt_ratio = safe_ratio(liabilities, assets, 100)
    net_margin = safe_ratio(profit, revenue, 100)
    ocf_margin = safe_ratio(ocf, revenue, 100)
    roe = metric_value(metrics, "weighted_avg_roe", year)
    if roe is None:
        roe = safe_ratio(profit, equity, 100)
    return {
        "company_id": company.get("company_id"),
        "stock_code": company.get("stock_code"),
        "company_short_name": company.get("company_short_name"),
        "industry_sw1": company.get("industry_sw1"),
        "industry_sw2": company.get("industry_sw2"),
        "industry_sw3": company.get("industry_sw3"),
        "metrics": {
            "operating_revenue_yi": revenue,
            "operating_revenue_yoy_pct": calc_yoy(metrics, "operating_revenue", year),
            "gross_margin_pct": gross_margin,
            "parent_net_profit_yi": profit,
            "deducted_parent_net_profit_yi": deducted_profit,
            "net_margin_pct": net_margin,
            "operating_cash_flow_yi": ocf,
            "operating_cash_flow_margin_pct": ocf_margin,
            "total_assets_yi": assets,
            "debt_to_asset_ratio_pct": debt_ratio,
            "roe_pct": roe,
        },
        "available_metric_count": sum(
            1 for value in [
                revenue,
                gross_margin,
                profit,
                deducted_profit,
                ocf,
                assets,
                debt_ratio,
                roe,
            ]
            if value is not None
        ),
    }


def industry_text(company: dict[str, Any]) -> str:
    return " ".join(
        str(company.get(key) or "")
        for key in ["industry", "industry_sw1", "industry_sw2", "industry_sw3", "company_short_name", "company_full_name"]
    )


def select_peers(catalog: dict[str, Any], target: dict[str, Any], min_peers: int) -> tuple[list[dict[str, Any]], str]:
    companies = [item for item in catalog.get("companies", []) if isinstance(item, dict) and item.get("status") == "ready"]
    target_id = target.get("company_id")
    sw3 = target.get("industry_sw3")
    sw2 = target.get("industry_sw2")
    sw1 = target.get("industry_sw1")
    if sw3:
        peers = [item for item in companies if item.get("industry_sw3") == sw3 and item.get("company_id") != target_id]
        if len(peers) >= min_peers:
            return peers, "same_industry_sw3"
    if sw2:
        peers = [item for item in companies if item.get("industry_sw2") == sw2 and item.get("company_id") != target_id]
        if len(peers) >= min_peers:
            return peers, "same_industry_sw2"
    if sw1:
        peers = [item for item in companies if item.get("industry_sw1") == sw1 and item.get("company_id") != target_id]
        if len(peers) >= min_peers:
            return peers, "same_industry_sw1"
    target_text = industry_text(target)
    keyword_match = any(keyword in target_text for keyword in AUTO_PEER_KEYWORDS)
    if keyword_match or str(target.get("company_short_name") or "").endswith(("汽车", "集团")):
        peers = [
            item for item in companies
            if item.get("company_id") != target_id
            and any(keyword in industry_text(item) for keyword in AUTO_PEER_KEYWORDS)
        ]
        if len(peers) >= min_peers:
            return peers, "auto_keyword_automotive"
    return [item for item in companies if item.get("company_id") != target_id], "all_ready_fallback"


def percentile_rank(values: list[float], target: float, higher_is_better: bool = True) -> float | None:
    clean = sorted(value for value in values if isinstance(value, (int, float)) and math.isfinite(value))
    if not clean:
        return None
    less_or_equal = sum(1 for value in clean if value <= target)
    pct = less_or_equal / len(clean) * 100
    return pct if higher_is_better else 100 - pct


def aggregate(rows: list[dict[str, Any]], target_row: dict[str, Any]) -> dict[str, Any]:
    keys = list(target_row.get("metrics", {}).keys())
    result: dict[str, Any] = {}
    lower_better = {"debt_to_asset_ratio_pct"}
    for key in keys:
        values = [
            row.get("metrics", {}).get(key)
            for row in rows
            if isinstance(row.get("metrics", {}).get(key), (int, float))
        ]
        target_value = target_row.get("metrics", {}).get(key)
        if not values:
            result[key] = {"sample_count": 0}
            continue
        result[key] = {
            "sample_count": len(values),
            "mean": statistics.fmean(values),
            "median": statistics.median(values),
            "min": min(values),
            "max": max(values),
            "target_value": target_value,
            "target_percentile": (
                percentile_rank(values, target_value, key not in lower_better)
                if isinstance(target_value, (int, float))
                else None
            ),
        }
    return result


def fmt_num(value: Any, suffix: str = "") -> str:
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        return "未返回"
    return f"{value:.2f}{suffix}"


def build_interpretation(target_row: dict[str, Any], aggregates: dict[str, Any], peer_rows: list[dict[str, Any]]) -> list[str]:
    metrics = target_row.get("metrics", {})
    lines = [
        f"同业样本数为 {len(peer_rows)}，覆盖 {', '.join(row.get('company_short_name') or row.get('company_id') for row in peer_rows[:8])}。",
    ]
    for key, label, suffix in [
        ("operating_revenue_yi", "收入规模", "亿元"),
        ("gross_margin_pct", "毛利率", "%"),
        ("net_margin_pct", "净利率", "%"),
        ("operating_cash_flow_margin_pct", "经营现金流率", "%"),
        ("debt_to_asset_ratio_pct", "资产负债率", "%"),
        ("roe_pct", "ROE", "%"),
    ]:
        agg = aggregates.get(key, {})
        if not agg or not agg.get("sample_count"):
            continue
        lines.append(
            f"{label}：公司 {fmt_num(metrics.get(key), suffix)}，同业中位数 {fmt_num(agg.get('median'), suffix)}，"
            f"分位约 {fmt_num(agg.get('target_percentile'), '%')}。"
        )
    return lines


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--company-dir", required=True, type=Path)
    parser.add_argument("--wiki-dir", type=Path, default=Path("/home/maoyd/siq-research-engine/data/wiki"))
    parser.add_argument("--year", type=int, default=2025)
    parser.add_argument("--min-peers", type=int, default=3)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    catalog = load_json(args.wiki_dir / "_meta" / "company_catalog.json")
    target_company = load_json(args.company_dir / "company.json")
    target_id = target_company.get("company_id") or args.company_dir.name
    catalog_target = next(
        (item for item in catalog.get("companies", []) if isinstance(item, dict) and item.get("company_id") == target_id),
        target_company,
    )
    peers, selection_method = select_peers(catalog, catalog_target, args.min_peers)
    target_row = company_row(catalog_target, args.company_dir, args.year)

    peer_rows: list[dict[str, Any]] = []
    for peer in peers:
        peer_path = args.wiki_dir / str(peer.get("company_path", ""))
        if not peer_path.exists():
            continue
        row = company_row(peer, peer_path, args.year)
        if row["available_metric_count"] >= 4:
            peer_rows.append(row)

    aggregates = aggregate(peer_rows, target_row)
    strict_ok = len(peer_rows) >= args.min_peers
    result = {
        "schema_version": 1,
        "generated_by": "peer_metrics_builder.py",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "company_id": target_id,
        "report_year": args.year,
        "selection_method": selection_method,
        "min_peers": args.min_peers,
        "peer_count": len(peer_rows),
        "strict_ok": strict_ok,
        "target": target_row,
        "peers": peer_rows,
        "aggregates": aggregates,
        "interpretation": build_interpretation(target_row, aggregates, peer_rows),
        "warnings": [] if strict_ok else [f"peer_sample_below_minimum:{len(peer_rows)}<{args.min_peers}"],
    }

    output = args.output or args.company_dir / "analysis" / ".work" / f"{target_id}-peer_metrics.json"
    dump_json(output, result)
    print(json.dumps({"ok": strict_ok, "output": str(output), "peer_count": len(peer_rows), "selection_method": selection_method}, ensure_ascii=False, indent=2))
    return 0 if strict_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
