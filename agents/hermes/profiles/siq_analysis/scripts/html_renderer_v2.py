#!/usr/bin/env python3
"""
SIQ HTML Renderer v2 - Professional Financial Report Visualization

Enterprise-grade HTML renderer for SIQ analysis reports.
Features:
- Interactive charts using ECharts (CDN)
- Responsive design with dark/light mode support
- Financial data visualization: waterfall charts, trend lines, gauge meters, radar charts
- Section-based navigation with progress tracking
- Print-optimized CSS
- Accessibility compliant
"""

from __future__ import annotations

import html as html_module
import importlib.util
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

PUBLIC_ORIGIN = os.environ.get("SIQ_PUBLIC_ORIGIN", "https://arthurmao.synology.me:8276").rstrip("/")

_FINANCIAL_CHART_MODULE_PATH = Path(__file__).resolve().parent / "financial_chart_design.py"
_financial_chart_spec = importlib.util.spec_from_file_location("siq_financial_chart_design", _FINANCIAL_CHART_MODULE_PATH)
if not _financial_chart_spec or not _financial_chart_spec.loader:
    raise RuntimeError(f"missing financial chart design module: {_FINANCIAL_CHART_MODULE_PATH}")
_financial_chart_module = importlib.util.module_from_spec(_financial_chart_spec)
_financial_chart_spec.loader.exec_module(_financial_chart_module)
render_income_bridge_svg = _financial_chart_module.render_income_bridge_svg


def public_api_url(path: str) -> str:
    if path.startswith(("http://", "https://")):
        return path
    if path.startswith("/"):
        return f"{PUBLIC_ORIGIN}{path}"
    return path


# =============================================================================
# DATA EXTRACTION HELPERS
# =============================================================================

def safe_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return default


ALIAS_MAP = {
    "total_operating_revenue": ["营业总收入"],
    "operating_revenue": ["营业收入"],
    "net_profit_parent": ["parent_net_profit", "归属于上市公司股东的净利润", "归属于母公司股东的净利润"],
    "net_operating_cash_flow": ["operating_cash_flow_net", "经营活动产生的现金流量净额"],
    "monetary_funds": ["monetary_capital", "货币资金"],
    "gross_margin": ["gross_profit_margin", "毛利率"],
    "accounts_receivable": ["应收账款"],
    "inventory": ["存货"],
    "operating_cost": ["营业成本"],
    "operating_profit": ["营业利润"],
    "total_assets": ["资产总计"],
    "total_liabilities": ["负债合计"],
    "short_term_borrowings": ["短期借款"],
    "capital_expenditure": ["cash_for_purchases_investments", "购建固定资产、无形资产和其他长期资产支付的现金"],
    "equity_attributable_parent": ["归母净资产", "归属于母公司股东的权益"],
    "deducted_parent_net_profit": ["扣非归母净利润", "扣除非经常性损益后的净利润"],
    "current_assets": ["流动资产", "流动资产合计"],
    "current_liabilities": ["流动负债", "流动负债合计"],
    "notes_receivable": ["应收票据"],
    "contract_liabilities": ["合同负债"],
    "current_portion_noncurrent_liabilities": ["一年内到期的非流动负债"],
    "long_term_borrowings": ["长期借款"],
    "notes_payable": ["应付票据"],
    "interest_expense": ["利息费用", "财务费用"],
    "investing_cash_flow_net": ["投资活动现金流净额"],
    "financing_cash_flow_net": ["筹资活动现金流净额"],
    "taxes_and_surcharges": ["营业税金及附加", "税金及附加"],
    "sales_expenses": ["selling_expenses", "销售费用"],
    "administrative_expenses": ["management_expenses", "管理费用"],
    "research_expenses": ["rd_expenses", "研发费用"],
    "financial_expenses": ["finance_expenses", "财务费用"],
    "asset_impairment_loss": ["asset_impairment", "资产减值损失"],
    "credit_impairment_loss": ["credit_impairment", "信用减值损失"],
    "other_income": ["其他收益"],
    "investment_income": ["投资收益"],
    "fair_value_change_income": ["fair_value_change", "公允价值变动收益"],
    "asset_disposal_income": ["资产处置收益"],
    "non_operating_income": ["营业外收入"],
    "non_operating_expenses": ["营业外支出"],
    "total_profit": ["利润总额"],
    "income_tax_expense": ["所得税费用"],
    "net_profit": ["净利润"],
    "minority_profit_loss": ["少数股东损益"],
}


def metric_item(snapshot: dict[str, Any], key: str) -> Any:
    """Resolve a metric item from normalized snapshots and common Chinese aliases."""
    metrics = snapshot.get("metrics", {})
    key_metrics = snapshot.get("key_metrics", {})
    for name in [key, *ALIAS_MAP.get(key, [])]:
        for source in [metrics, key_metrics]:
            if isinstance(source, dict) and name in source:
                return source[name]
    return None


def metric_value_from_item(item: Any, year: str = "2025") -> Any:
    if item is None:
        return None
    if not isinstance(item, dict):
        return safe_float(item)
    values = item.get("values", {})
    if not isinstance(values, dict):
        return safe_float(item.get("value"))
    val = values.get(year)
    if val is None:
        candidates = [(str(k), v) for k, v in values.items() if str(k).startswith(year) and v is not None]
        if candidates:
            val = sorted(candidates, key=lambda pair: pair[0])[-1][1]
    if val is None and values:
        candidates = [(str(k), v) for k, v in values.items() if v is not None]
        if candidates:
            val = sorted(candidates, key=lambda pair: pair[0])[-1][1]
    if val is None:
        return None
    unit = str(item.get("unit", "")).strip()
    result = safe_float(val)
    if unit in {"元", "人民币元", "CNY"}:
        return result / 100_000_000
    return result


def metric_value(snapshot: dict[str, Any], key: str, year: str = "2025") -> Any:
    """Extract metric value from snapshot with alias resolution."""
    return metric_value_from_item(metric_item(snapshot, key), year)


def get_metric_history(snapshot: dict[str, Any], key: str) -> dict[str, float]:
    """Get historical values for a metric across available years."""
    item = metric_item(snapshot, key)
    if isinstance(item, dict):
        values = item.get("values", {})
        result = {}
        if isinstance(values, dict):
            for period, val in values.items():
                if val is None:
                    continue
                year_match = re.search(r"\d{4}", str(period))
                year = year_match.group(0) if year_match else str(period)
                unit = str(item.get("unit", "")).strip()
                value = safe_float(val) / 100_000_000 if unit in {"元", "人民币元", "CNY"} else safe_float(val)
                result[year] = value
        return dict(sorted(result.items()))
    return {}


def fmt_num(value: Any, suffix: str = "") -> str:
    if value is None:
        return "—"
    if isinstance(value, (int, float)):
        if abs(value) >= 10000:
            return f"{value:,.0f}{suffix}"
        elif abs(value) >= 1:
            return f"{value:,.2f}{suffix}"
        else:
            return f"{value:.4f}{suffix}"
    return f"{value}{suffix}"


def fmt_yi(value: Any, suffix: str = "亿") -> str:
    if value is None:
        return "—"
    num = safe_float(value)
    if abs(num) >= 100:
        return f"{num:,.1f}{suffix}"
    return f"{num:,.2f}{suffix}"


def fmt_pct(value: Any) -> str:
    if value is None:
        return "—"
    return f"{safe_float(value):.2f}%"


# =============================================================================
# CHART DATA BUILDERS
# =============================================================================

def build_revenue_profit_trend_data(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Build data for revenue and profit trend chart."""
    revenue_hist = get_metric_history(snapshot, "operating_revenue")
    profit_hist = get_metric_history(snapshot, "net_profit_parent")
    
    years = sorted(set(revenue_hist.keys()) | set(profit_hist.keys()))
    if not years:
        return None
    
    return {
        "years": years,
        "revenue": [revenue_hist.get(y, 0) for y in years],
        "profit": [profit_hist.get(y, 0) for y in years],
    }


def build_cashflow_data(
    snapshot: dict[str, Any],
    preflight: dict[str, Any] | None = None,
    work_dir: Path | None = None,
) -> dict[str, Any]:
    """Build cash flow data using exact consolidated cash-flow rows before snapshot aliases."""
    preflight = preflight or {}
    year = str(preflight.get("report_year") or snapshot.get("report_year") or "2025")
    raw_rows = _raw_cashflow_statement_rows(work_dir, year)

    sources: dict[str, dict[str, Any]] = {}
    validations: list[dict[str, Any]] = []

    ocf_value = _read_verified_metric(
        snapshot,
        year,
        raw_rows,
        "operating",
        ["经营活动产生的现金流量净额"],
        ["operating_cash_flow_net"],
        sources,
    )
    icf_value = _read_verified_metric(
        snapshot,
        year,
        raw_rows,
        "investing",
        ["投资活动产生的现金流量净额", "投资活动使用的现金流量净额"],
        ["investing_cash_flow_net"],
        sources,
    )
    financing_value = _read_verified_metric(
        snapshot,
        year,
        raw_rows,
        "financing",
        ["筹资活动产生的现金流量净额", "筹资活动使用的现金流量净额"],
        ["financing_cash_flow_net"],
        sources,
    )
    capex_value = _read_verified_metric(
        snapshot,
        year,
        raw_rows,
        "capex",
        ["购建固定资产、无形资产和其他长期资产支付的现金"],
        ["cash_for_purchases_investments"],
        sources,
    )
    purchase_cash_value = _raw_statement_value_any(raw_rows, ["购买商品、接受劳务支付的现金"])

    ocf = safe_float(ocf_value)
    icf = safe_float(icf_value)
    financing = safe_float(financing_value)
    capex = safe_float(capex_value)

    fcf_calc = ocf - capex if ocf_value is not None and capex_value is not None else None
    reported_fcf_value = metric_value(snapshot, "free_cash_flow", year)
    if reported_fcf_value is not None and fcf_calc is not None:
        reported_fcf = safe_float(reported_fcf_value)
        diff = reported_fcf - fcf_calc
        validations.append(
            {
                "rule": "free_cash_flow = operating_cash_flow_net - capital_expenditure",
                "status": "ok" if abs(diff) <= 0.05 else "recomputed",
                "computed": round(fcf_calc, 2),
                "snapshot_consistent": abs(diff) <= 0.05,
            }
        )
    elif fcf_calc is not None:
        validations.append(
            {
                "rule": "free_cash_flow = operating_cash_flow_net - capital_expenditure",
                "status": "computed",
                "computed": round(fcf_calc, 2),
            }
        )

    if purchase_cash_value is not None and capex_value is not None:
        validations.append(
            {
                "rule": "capital_expenditure row disambiguation",
                "status": "ok" if abs(safe_float(purchase_cash_value) - capex) > 0.05 else "check",
                "capex_row": "购建固定资产、无形资产和其他长期资产支付的现金",
                "excluded_row": "购买商品、接受劳务支付的现金",
            }
        )
    
    return {
        "operating": round(ocf, 2) if ocf_value is not None else None,
        "investing": round(icf, 2) if icf_value is not None else None,
        "financing": round(financing, 2) if financing_value is not None else None,
        "capex": round(capex, 2) if capex_value is not None else None,
        "free_cash_flow": round(fcf_calc, 2) if fcf_calc is not None else None,
        "sources": sources,
        "validations": validations,
        "notes": [
            "资本开支按合并现金流量表“购建固定资产、无形资产和其他长期资产支付的现金”取数，图中按现金流出方向展示为负值。",
            "自由现金流按经营活动现金流量净额减资本开支现场重算，快照派生值仅用于复核。",
        ],
    }


def build_dupont_data(
    snapshot: dict[str, Any],
    preflight: dict[str, Any] | None = None,
    work_dir: Path | None = None,
) -> dict[str, Any]:
    """Build DuPont data from verified income statement and balance sheet rows."""
    preflight = preflight or {}
    year = str(preflight.get("report_year") or snapshot.get("report_year") or "2025")
    income_rows = _raw_income_statement_rows(work_dir, year)
    balance_rows = _raw_balance_sheet_rows(work_dir, year)
    sources: dict[str, dict[str, Any]] = {}

    revenue = _read_verified_metric(
        snapshot,
        year,
        income_rows,
        "revenue",
        ["营业总收入", "营业收入"],
        ["total_operating_revenue", "operating_revenue"],
        sources,
    )
    net_profit = _read_verified_metric(
        snapshot,
        year,
        income_rows,
        "net_profit_parent",
        ["归属于母公司股东的净利润", "归属于上市公司股东的净利润", "归属于母公司所有者的净利润"],
        ["parent_net_profit"],
        sources,
    )
    total_assets = _read_verified_metric(snapshot, year, balance_rows, "total_assets", ["资产总计"], ["total_assets"], sources)
    total_liabilities = _read_verified_metric(snapshot, year, balance_rows, "total_liabilities", ["负债合计"], ["total_liabilities"], sources)
    equity = _read_verified_metric(
        snapshot,
        year,
        balance_rows,
        "equity_attributable_parent",
        ["归属于母公司所有者权益合计", "归属于母公司股东权益合计", "归属于母公司所有者权益（或股东权益）合计"],
        ["equity_attributable_parent"],
        sources,
    )
    validations: list[dict[str, Any]] = []
    if equity is None and total_assets is not None and total_liabilities is not None:
        equity = total_assets - total_liabilities
        sources["equity_attributable_parent"] = {"source": "computed", "row": "资产总计-负债合计"}
        validations.append({"rule": "equity = total_assets - total_liabilities", "status": "computed", "computed": round(equity, 2)})

    if revenue is None or net_profit is None or total_assets is None or equity is None:
        return None

    def pct_ratio(value: float | None, denominator: float | None) -> float | None:
        if value is None or denominator in (None, 0):
            return None
        return value / denominator * 100

    def plain_ratio(value: float | None, denominator: float | None) -> float | None:
        if value is None or denominator in (None, 0):
            return None
        return value / denominator

    def clamp_score(value: float | None, low: float, high: float) -> float | None:
        if value is None or high <= low:
            return None
        return round(max(0.0, min(100.0, (value - low) / (high - low) * 100)), 2)

    def dupont_dimension(
        key: str,
        name: str,
        value: float | None,
        unit: str,
        low: float,
        high: float,
        formula: str,
    ) -> dict[str, Any]:
        display = "-" if value is None else (f"{value:.2f}%" if unit == "%" else f"{value:.2f}x")
        return {
            "key": key,
            "name": name,
            "raw_value": round(value, 4) if value is not None else None,
            "raw_display": display,
            "score": clamp_score(value, low, high),
            "max": 100,
            "unit": unit,
            "reference_range": {"low": low, "high": high},
            "formula": formula,
        }
    
    net_margin = pct_ratio(net_profit, revenue)
    asset_turnover = plain_ratio(revenue, total_assets)
    equity_multiplier = plain_ratio(total_assets, equity)
    roe = pct_ratio(net_profit, equity)
    dimensions = [
        dupont_dimension("net_margin", "销售净利率", net_margin, "%", -10, 12, "归母净利润 / 营业收入"),
        dupont_dimension("asset_turnover", "资产周转率", asset_turnover, "x", 0, 1.5, "营业收入 / 资产总计"),
        dupont_dimension("equity_multiplier", "权益乘数", equity_multiplier, "x", 1, 6, "资产总计 / 归母权益"),
        dupont_dimension("roe", "ROE", roe, "%", -20, 25, "归母净利润 / 归母权益"),
    ]
    validations.append({
        "rule": "ROE = 销售净利率 × 资产周转率 × 权益乘数",
        "status": "ok",
        "computed_roe": round((net_margin or 0) * (asset_turnover or 0) * (equity_multiplier or 0), 2),
        "reported_roe": round(roe, 2) if roe is not None else None,
    })
    
    return {
        "net_margin": round(net_margin, 2) if net_margin is not None else None,
        "asset_turnover": round(asset_turnover, 4) if asset_turnover is not None else None,
        "equity_multiplier": round(equity_multiplier, 2) if equity_multiplier is not None else None,
        "roe": round(roe, 2) if roe is not None else None,
        "dimensions": dimensions,
        "visual_scale": "reference_range_score_0_100",
        "scale_note": "雷达半径使用行业通用展示区间归一化，tooltip 和标签展示原始杜邦指标。",
        "sources": sources,
        "validations": validations,
    }


def build_asset_structure_data(
    snapshot: dict[str, Any],
    preflight: dict[str, Any] | None = None,
    work_dir: Path | None = None,
) -> dict[str, Any]:
    """Build asset structure data from verified balance-sheet rows without estimates."""
    preflight = preflight or {}
    year = str(preflight.get("report_year") or snapshot.get("report_year") or "2025")
    rows = _raw_balance_sheet_rows(work_dir, year)
    sources: dict[str, dict[str, Any]] = {}
    total = _read_verified_metric(snapshot, year, rows, "total_assets", ["资产总计"], ["total_assets"], sources)
    if total is None or total <= 0:
        return None
    cash = _read_verified_metric(snapshot, year, rows, "cash", ["货币资金"], ["monetary_capital", "monetary_funds"], sources)
    receivables = _read_verified_metric(snapshot, year, rows, "receivables", ["应收账款"], ["accounts_receivable"], sources)
    inventory = _read_verified_metric(snapshot, year, rows, "inventory", ["存货"], ["inventory"], sources)
    current = _read_verified_metric(snapshot, year, rows, "current_assets", ["流动资产合计", "流动资产"], ["current_assets"], sources)
    non_current = _read_verified_metric(snapshot, year, rows, "non_current_assets", ["非流动资产合计", "非流动资产"], ["non_current_assets"], sources)
    if non_current is None and current is not None:
        non_current = total - current
        sources["non_current_assets"] = {"source": "computed", "row": "资产总计-流动资产合计"}
    categories: list[dict[str, Any]] = []
    for name, value in [("货币资金", cash), ("应收账款", receivables), ("存货", inventory)]:
        if value is not None and value > 0:
            categories.append({"name": name, "value": round(value, 2)})
    if current is not None:
        known_current = sum(safe_float(item.get("value")) for item in categories)
        other_current = current - known_current
        if other_current > max(total * 0.001, 0.1):
            categories.append({"name": "其他流动资产", "value": round(other_current, 2)})
    if non_current is not None and non_current > 0:
        categories.append({"name": "非流动资产", "value": round(non_current, 2)})
    residual = total - sum(safe_float(item.get("value")) for item in categories)
    validations = [{"rule": "asset_categories_sum_to_total_assets", "status": "ok" if abs(residual) <= max(total * 0.002, 0.5) else "residual", "diff": round(residual, 2)}]
    if residual > max(total * 0.002, 0.5):
        categories.append({"name": "其他资产/口径差", "value": round(residual, 2)})
    return {
        "categories": categories,
        "total": round(total, 2),
        "sources": sources,
        "validations": validations,
    }


def build_debt_structure_data(
    snapshot: dict[str, Any],
    preflight: dict[str, Any] | None = None,
    work_dir: Path | None = None,
) -> dict[str, Any]:
    """Build debt structure data from verified balance-sheet rows without estimates."""
    preflight = preflight or {}
    year = str(preflight.get("report_year") or snapshot.get("report_year") or "2025")
    rows = _raw_balance_sheet_rows(work_dir, year)
    sources: dict[str, dict[str, Any]] = {}
    total = _read_verified_metric(snapshot, year, rows, "total_liabilities", ["负债合计"], ["total_liabilities"], sources)
    if total is None or total <= 0:
        return None
    short_borrow = _read_verified_metric(snapshot, year, rows, "short_borrow", ["短期借款"], ["short_term_borrowings"], sources)
    current_noncurrent = _read_verified_metric(snapshot, year, rows, "current_portion_noncurrent", ["一年内到期的非流动负债"], ["current_portion_noncurrent_liabilities"], sources)
    long_borrow = _read_verified_metric(snapshot, year, rows, "long_borrow", ["长期借款"], ["long_term_borrowings"], sources)
    notes_payable = _read_verified_metric(snapshot, year, rows, "notes_payable", ["应付票据"], ["notes_payable"], sources)
    current = _read_verified_metric(snapshot, year, rows, "current_liabilities", ["流动负债合计", "流动负债"], ["current_liabilities"], sources)
    non_current = _read_verified_metric(snapshot, year, rows, "non_current_liabilities", ["非流动负债合计", "非流动负债"], ["non_current_liabilities"], sources)
    if non_current is None and current is not None:
        non_current = total - current
        sources["non_current_liabilities"] = {"source": "computed", "row": "负债合计-流动负债合计"}
    categories: list[dict[str, Any]] = []
    for name, value in [("短期借款", short_borrow), ("一年内到期非流动负债", current_noncurrent), ("应付票据", notes_payable)]:
        if value is not None and value > 0:
            categories.append({"name": name, "value": round(value, 2)})
    if current is not None:
        known_current = sum(safe_float(item.get("value")) for item in categories)
        other_current = current - known_current
        if other_current > max(total * 0.001, 0.1):
            categories.append({"name": "其他流动负债", "value": round(other_current, 2)})
    if long_borrow is not None and long_borrow > 0:
        categories.append({"name": "长期借款", "value": round(long_borrow, 2)})
    if non_current is not None:
        other_non_current = non_current - (long_borrow or 0)
        if other_non_current > max(total * 0.001, 0.1):
            categories.append({"name": "其他非流动负债", "value": round(other_non_current, 2)})
    residual = total - sum(safe_float(item.get("value")) for item in categories)
    validations = [{"rule": "debt_categories_sum_to_total_liabilities", "status": "ok" if abs(residual) <= max(total * 0.002, 0.5) else "residual", "diff": round(residual, 2)}]
    if residual > max(total * 0.002, 0.5):
        categories.append({"name": "其他负债/口径差", "value": round(residual, 2)})
    return {
        "categories": categories,
        "total": round(total, 2),
        "sources": sources,
        "validations": validations,
    }


def build_solvency_gauges(
    snapshot: dict[str, Any],
    preflight: dict[str, Any] | None = None,
    work_dir: Path | None = None,
) -> dict[str, Any]:
    """Build solvency indicators from verified balance-sheet rows."""
    preflight = preflight or {}
    year = str(preflight.get("report_year") or snapshot.get("report_year") or "2025")
    rows = _raw_balance_sheet_rows(work_dir, year)
    sources: dict[str, dict[str, Any]] = {}
    total_assets = _read_verified_metric(snapshot, year, rows, "total_assets", ["资产总计"], ["total_assets"], sources)
    total_liabilities = _read_verified_metric(snapshot, year, rows, "total_liabilities", ["负债合计"], ["total_liabilities"], sources)
    current_assets = _read_verified_metric(snapshot, year, rows, "current_assets", ["流动资产合计", "流动资产"], ["current_assets"], sources)
    current_liabilities = _read_verified_metric(snapshot, year, rows, "current_liabilities", ["流动负债合计", "流动负债"], ["current_liabilities"], sources)
    cash = _read_verified_metric(snapshot, year, rows, "cash", ["货币资金"], ["monetary_capital", "monetary_funds"], sources)
    inventory = _read_verified_metric(snapshot, year, rows, "inventory", ["存货"], ["inventory"], sources)
    
    debt_ratio = (total_liabilities / total_assets * 100) if total_assets else None
    current_ratio = (current_assets / current_liabilities) if current_liabilities else None
    quick_ratio = ((current_assets - inventory) / current_liabilities) if current_liabilities and current_assets is not None and inventory is not None else None
    cash_ratio = (cash / current_liabilities) if current_liabilities else None
    
    return {
        "debt_ratio": round(debt_ratio, 2) if debt_ratio else None,
        "current_ratio": round(current_ratio, 2) if current_ratio else None,
        "quick_ratio": round(quick_ratio, 2) if quick_ratio else None,
        "cash_ratio": round(cash_ratio, 2) if cash_ratio else None,
        "sources": sources,
    }


def build_peer_comparison_data(snapshot: dict[str, Any], work_dir: Path | None = None) -> dict[str, Any] | None:
    """Build peer comparison radar chart data."""
    if work_dir:
        peer_file = work_dir / "peer_metrics.json"
        if peer_file.exists():
            try:
                with open(peer_file, "r", encoding="utf-8") as f:
                    peer_data = json.load(f)
                
                if peer_data.get("strict_ok") and peer_data.get("peer_count", 0) >= 3:
                    company_metrics = peer_data.get("company_metrics", {})
                    peer_median = peer_data.get("peer_median", {})
                    
                    # Normalize metrics for radar chart (0-100 scale)
                    metrics = ["revenue", "gross_margin", "net_margin", "roe", "asset_turnover"]
                    company_values = []
                    peer_values = []
                    
                    for m in metrics:
                        cv = safe_float(company_metrics.get(m), 0)
                        pv = safe_float(peer_median.get(m), 0)
                        max_v = max(abs(cv), abs(pv), 1)
                        company_values.append(round(cv / max_v * 100, 1))
                        peer_values.append(round(pv / max_v * 100, 1))
                    
                    return {
                        "metrics": ["营收规模", "毛利率", "净利率", "ROE", "资产周转"],
                        "company": company_values,
                        "peer_median": peer_values,
                        "peer_count": peer_data.get("peer_count", 0),
                    }
            except Exception:
                pass
    return None


# =============================================================================
# HTML TEMPLATE COMPONENTS
# =============================================================================

# =============================================================================
# STATIC PRESENTATION ASSETS
# =============================================================================

_RENDERER_ASSETS_MODULE_PATH = Path(__file__).resolve().parent / "renderer_assets.py"
_renderer_assets_spec = importlib.util.spec_from_file_location("siq_renderer_assets", _RENDERER_ASSETS_MODULE_PATH)
if not _renderer_assets_spec or not _renderer_assets_spec.loader:
    raise RuntimeError(f"missing renderer assets module: {_RENDERER_ASSETS_MODULE_PATH}")
_renderer_assets_module = importlib.util.module_from_spec(_renderer_assets_spec)
_renderer_assets_spec.loader.exec_module(_renderer_assets_module)
CSS_STYLES = _renderer_assets_module.CSS_STYLES
ECHARTS_SCRIPTS = _renderer_assets_module.ECHARTS_SCRIPTS

_RENDERER_SECTIONS_MODULE_PATH = Path(__file__).resolve().parent / "renderer_sections.py"
_renderer_sections_spec = importlib.util.spec_from_file_location("siq_renderer_sections", _RENDERER_SECTIONS_MODULE_PATH)
if not _renderer_sections_spec or not _renderer_sections_spec.loader:
    raise RuntimeError(f"missing renderer sections module: {_RENDERER_SECTIONS_MODULE_PATH}")
_renderer_sections_module = importlib.util.module_from_spec(_renderer_sections_spec)
_renderer_sections_spec.loader.exec_module(_renderer_sections_module)
source_anchor = _renderer_sections_module.source_anchor
split_source_prefix = _renderer_sections_module.split_source_prefix
sentence_paragraphs = _renderer_sections_module.sentence_paragraphs
truncate_text = _renderer_sections_module.truncate_text
render_narrative_item = _renderer_sections_module.render_narrative_item
render_navigation = _renderer_sections_module.render_navigation


def evidence_links_from_id(evidence_id: Any, preflight: dict[str, Any]) -> str:
    return _renderer_sections_module.evidence_links_from_id(
        evidence_id,
        preflight,
        public_api_url=public_api_url,
    )


def render_section_content(section: dict[str, Any], preflight: dict[str, Any] | None = None) -> str:
    return _renderer_sections_module.render_section_content(
        section,
        preflight,
        public_api_url=public_api_url,
    )


# =============================================================================
# HTML RENDERER
# =============================================================================

def render_kpi_cards(snapshot: dict[str, Any]) -> str:
    """Render KPI summary cards at top of report."""
    revenue = safe_float(metric_value(snapshot, "operating_revenue"))
    profit = safe_float(metric_value(snapshot, "net_profit_parent"))
    ocf = safe_float(metric_value(snapshot, "net_operating_cash_flow"))
    total_assets = safe_float(metric_value(snapshot, "total_assets"))
    debt_ratio = safe_float(metric_value(snapshot, "total_liabilities")) / total_assets * 100 if total_assets else None
    gross_margin = safe_float(metric_value(snapshot, "gross_margin"))

    def trend_class(value: float | None, favorable: str = "higher") -> str:
        if value is None:
            return "neutral"
        if value == 0:
            return "neutral"
        if favorable == "lower":
            return "up" if value < 0 else "down"
        return "up" if value > 0 else "down"

    def trend_text(value: float | None) -> str:
        return f"同比 {fmt_num(value, '%')}" if value is not None else "同比未返回"

    cards = []

    # Revenue card
    revenue_class = "positive" if revenue > 0 else "neutral"
    revenue_yoy = yoy_change(snapshot, "operating_revenue")
    cards.append(f"""
    <div class="kpi-card {revenue_class}">
      <div class="kpi-label">营业收入</div>
      <div class="kpi-value">{fmt_num(revenue, "亿元")}</div>
      <div class="kpi-change {trend_class(revenue_yoy)}">{trend_text(revenue_yoy)}</div>
    </div>""")

    # Profit card
    profit_class = "positive" if profit and profit > 0 else "negative" if profit and profit < 0 else "neutral"
    profit_yoy = yoy_change(snapshot, "net_profit_parent")
    cards.append(f"""
    <div class="kpi-card {profit_class}">
      <div class="kpi-label">归母净利润</div>
      <div class="kpi-value">{fmt_num(profit, "亿元")}</div>
      <div class="kpi-change {trend_class(profit_yoy)}">{trend_text(profit_yoy)}</div>
    </div>""")

    # OCF card
    ocf_class = "positive" if ocf and ocf > 0 else "negative"
    ocf_yoy = yoy_change(snapshot, "net_operating_cash_flow")
    cards.append(f"""
    <div class="kpi-card {ocf_class}">
      <div class="kpi-label">经营现金流</div>
      <div class="kpi-value">{fmt_num(ocf, "亿元")}</div>
      <div class="kpi-change {trend_class(ocf_yoy)}">{trend_text(ocf_yoy)}</div>
    </div>""")

    # Gross margin card
    gm_class = "positive" if gross_margin and gross_margin > 20 else "warning" if gross_margin and gross_margin > 10 else "neutral"
    cards.append(f"""
    <div class="kpi-card {gm_class}">
      <div class="kpi-label">毛利率</div>
      <div class="kpi-value">{fmt_num(gross_margin, "%")}</div>
      <div class="kpi-change neutral">盈利质量指标</div>
    </div>""")
    
    # Debt ratio card
    debt_class = "warning" if debt_ratio and debt_ratio > 70 else "positive" if debt_ratio and debt_ratio < 50 else "neutral"
    cards.append(f"""
    <div class="kpi-card {debt_class}">
      <div class="kpi-label">资产负债率</div>
      <div class="kpi-value">{fmt_num(debt_ratio, "%")}</div>
      <div class="kpi-change {'down' if debt_ratio and debt_ratio > 70 else 'up' if debt_ratio is not None else 'neutral'}">{'偏高' if debt_ratio and debt_ratio > 70 else '健康区间' if debt_ratio is not None else '待补充'}</div>
    </div>""")
    
    # Total assets card
    cards.append(f"""
    <div class="kpi-card neutral">
      <div class="kpi-label">总资产</div>
      <div class="kpi-value">{fmt_num(total_assets, "亿元")}</div>
      <div class="kpi-change neutral">规模指标</div>
    </div>""")
    
    return f'<div class="kpi-grid">{ "".join(cards) }</div>'


def render_source_legend() -> str:
    return """
    <section class="source-legend" aria-label="证据来源图例">
      <div>
        <div class="source-legend-title">阅读口径</div>
        <p>报告优先使用本地年报、Wiki 指标、source map 和研究包事实；Tavily/EXA 等外部搜索只作为行业、技术、政策和可比公司补证。</p>
      </div>
      <div class="source-legend-badges">
        <span class="source-badge source-local">本地事实</span>
        <span class="source-badge source-model">模型测算</span>
        <span class="source-badge source-external">外部搜索补证</span>
        <span class="source-badge source-risk">风险链</span>
        <span class="source-badge source-tracking">跟踪信号</span>
      </div>
    </section>
    """


def render_report_summary(
    preflight: dict[str, Any],
    snapshot: dict[str, Any],
    sections: list[dict[str, Any]],
    quality_badges: list[str],
) -> str:
    company_name = preflight.get("company_short_name") or preflight.get("company_id") or snapshot.get("company_id") or "公司"
    report_year = preflight.get("report_year", snapshot.get("report_year", "2025"))
    summary_points = build_investor_summary_points(snapshot, report_year)
    points_html = "".join(
        f'<div class="summary-point"><span>{inline_summary_text(point)}</span></div>'
        for point in summary_points[:4]
    )
    return f"""
    <div class="report-summary">
      <section class="summary-panel" aria-label="核心结论">
        <div class="summary-eyebrow">核心结论</div>
        <div class="summary-title">{html_module.escape(str(company_name))} {html_module.escape(str(report_year))} 年财务诊断摘要</div>
        <div class="summary-body">{points_html}</div>
        <div class="quality-strip">{''.join(quality_badges)}</div>
      </section>
      {render_navigation(sections)}
    </div>
    """


def inline_summary_text(text: str) -> str:
    escaped = html_module.escape(str(text or ""))
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)


def build_investor_summary_points(snapshot: dict[str, Any], report_year: Any) -> list[str]:
    """Build reader-facing conclusions, not internal research process notes."""
    revenue = metric_value(snapshot, "operating_revenue")
    profit = metric_value(snapshot, "net_profit_parent")
    deducted_profit = metric_value(snapshot, "deducted_parent_net_profit")
    ocf = metric_value(snapshot, "net_operating_cash_flow")
    gross_margin = metric_value(snapshot, "gross_margin")
    total_assets = metric_value(snapshot, "total_assets")
    total_liabilities = metric_value(snapshot, "total_liabilities")
    capex = metric_value(snapshot, "capital_expenditure") or metric_value(snapshot, "cash_for_purchases_investments")

    revenue_yoy = yoy_change(snapshot, "operating_revenue")
    profit_yoy = yoy_change(snapshot, "net_profit_parent")
    ocf_value = safe_float(ocf) if ocf is not None else None
    profit_value = safe_float(profit) if profit is not None else None
    deducted_value = safe_float(deducted_profit) if deducted_profit is not None else None
    revenue_value = safe_float(revenue) if revenue is not None else None
    gross_margin_value = safe_float(gross_margin) if gross_margin is not None else None
    debt_ratio = None
    if total_assets not in (None, 0) and total_liabilities is not None and safe_float(total_assets):
        debt_ratio = safe_float(total_liabilities) / safe_float(total_assets) * 100

    growth_phrase = f"同比 {fmt_num(revenue_yoy, '%')}" if revenue_yoy is not None else "同比待确认"
    profit_phrase = f"归母净利润 {fmt_num(profit_value, '亿元')}"
    if profit_yoy is not None:
        profit_phrase += f"，同比 {fmt_num(profit_yoy, '%')}"
    points = [
        f"**经营安全与盈利质量**：{report_year} 年营业收入 {fmt_num(revenue_value, '亿元')}，{growth_phrase}；{profit_phrase}。结论应聚焦盈利修复是否能持续，而不是只看单一增长指标。",
    ]
    if gross_margin_value is not None or deducted_value is not None:
        points.append(
            f"**利润质量**：毛利率 {fmt_num(gross_margin_value, '%')}，扣非归母净利润 {fmt_num(deducted_value, '亿元')}。若扣非利润弱于归母利润，需要继续拆解一次性收益、费用和减值影响。"
        )
    if ocf_value is not None:
        capex_part = f"，资本开支 {fmt_num(safe_float(capex), '亿元')}" if capex is not None else ""
        points.append(
            f"**现金流含金量**：经营现金流 {fmt_num(ocf_value, '亿元')}{capex_part}。经营现金流能否覆盖资本开支和营运资金波动，是判断利润兑现质量的关键。"
        )
    if debt_ratio is not None:
        debt_view = "偏高，需要看短债、现金和融资续接" if debt_ratio >= 65 else "处于可跟踪区间，仍需结合现金和短债结构"
        points.append(
            f"**财务弹性**：资产负债率 {fmt_num(debt_ratio, '%')}，{debt_view}。后续重点观察债务覆盖、存货/应收周转和自由现金流。"
        )
    if len(points) < 4:
        points.append("**后续跟踪**：优先观察收入结构、毛利率、扣非利润、经营现金流、资本开支和同业分位是否同向改善。")
    return points[:4]


def yoy_change(snapshot: dict[str, Any], key: str) -> float | None:
    """Calculate year-over-year change."""
    hist = get_metric_history(snapshot, key)
    years = sorted(hist.keys())
    if len(years) >= 2:
        current = hist[years[-1]]
        previous = hist[years[-2]]
        if previous and previous != 0:
            return (current - previous) / abs(previous) * 100
    return None


def render_header(preflight: dict[str, Any], snapshot: dict[str, Any]) -> str:
    """Render report header with company info."""
    company_id = preflight.get("company_id", snapshot.get("company_id", "未知公司"))
    stock_code = preflight.get("stock_code", "")
    report_year = preflight.get("report_year", snapshot.get("report_year", "2025"))
    company_name = preflight.get("company_short_name", company_id)
    
    return f"""
    <header class="report-header">
      <div class="container">
        <div class="header-content">
          <div class="stock-badge">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"/></svg>
            {html_module.escape(str(stock_code))} · {html_module.escape(str(company_name))}
          </div>
          <h1 class="report-title">{html_module.escape(str(report_year))}年度财务诊断报告</h1>
          <p class="report-subtitle">基于公开年报数据的经营质量分析与风险诊断 · 不构成投资建议</p>
          <div class="report-meta">
            <div class="meta-item">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>
              报告年度：{html_module.escape(str(report_year))}
            </div>
            <div class="meta-item">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
              生成时间：{datetime.now().strftime("%Y-%m-%d %H:%M")}
            </div>
            <div class="meta-item">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/></svg>
              数据来源：年报 / Wiki / PostgreSQL
            </div>
          </div>
        </div>
      </div>
    </header>
"""


def render_chart_section(chart_id: str, title: str, size: str = "", extra_class: str = "", fallback_svg: str = "", note: str = "") -> str:
    """Render a chart container."""
    size_class = f"chart-area {size}" if size else "chart-area"
    fallback = f'<div class="chart-fallback">{fallback_svg}</div>' if fallback_svg else ""
    note_html = f'<div class="chart-note">{html_module.escape(note)}</div>' if note else ""
    return f"""
    <div class="chart-container {extra_class}">
      <div class="chart-head">
        <div class="chart-title">{html_module.escape(title)}</div>
        {note_html}
      </div>
      <div id="{chart_id}" class="{size_class}"></div>
      {fallback}
    </div>
"""


def render_income_bridge_panel(data: dict[str, Any] | None) -> str:
    """Render the first visible chart for single-company HTML reports."""
    period_label = data.get("period_label") if data else "当前报告期"
    starting_value = data.get("starting_value") if data else None
    ending_value = data.get("ending_value") if data else None
    known_count = len(data.get("known_fields", [])) if data else 0
    missing_count = len(data.get("missing_fields", [])) if data else 0
    notes = data.get("notes", []) if data else ["利润表字段不足，暂以降级提示占位，待补充收入、成本费用和净利润口径。"]
    container_class = "income-bridge-panel" if data else "income-bridge-panel static-fallback"

    profit_label = "归母净利润" if safe_float(ending_value) >= 0 else "归母净亏损"
    footnotes = "".join(f"<span>{html_module.escape(str(note))}</span>" for note in notes)
    return f"""
    <div class="chart-container {container_class}" id="income-bridge-panel">
      <div class="income-bridge-head">
        <div class="income-bridge-title-group">
          <div class="chart-title">收支拆解</div>
          <div class="income-bridge-subtitle">{html_module.escape(str(period_label or "当前报告期"))} · 营业收入 {fmt_yi(starting_value)} · {profit_label} {fmt_yi(ending_value)}</div>
        </div>
        <div class="income-bridge-meta">
          <div class="income-bridge-unit">单位：亿元</div>
          <div class="income-bridge-legend">
            <span><i class="income"></i>收入</span>
            <span><i class="expense"></i>支出</span>
            <span><i class="profit"></i>利润</span>
          </div>
        </div>
      </div>
      <div class="chart-fallback">{svg_income_bridge_chart(data)}</div>
      <div class="income-bridge-tooltip" role="status" aria-live="polite"></div>
      <div class="income-bridge-summary">
        <div class="income-bridge-metric">
          <div class="income-bridge-metric-label">报告期</div>
          <div class="income-bridge-metric-value">{html_module.escape(str(period_label or "当前报告期"))}</div>
        </div>
        <div class="income-bridge-metric">
          <div class="income-bridge-metric-label">收入起点</div>
          <div class="income-bridge-metric-value">{fmt_yi(starting_value)}</div>
        </div>
        <div class="income-bridge-metric">
          <div class="income-bridge-metric-label">利润终点</div>
          <div class="income-bridge-metric-value">{fmt_yi(ending_value)}</div>
        </div>
        <div class="income-bridge-metric">
          <div class="income-bridge-metric-label">识别字段</div>
          <div class="income-bridge-metric-value">{known_count} 项 / 缺 {missing_count} 项</div>
        </div>
      </div>
      <div class="income-bridge-footnotes">{footnotes}</div>
    </div>
"""


def build_profitability_waterfall_data(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    """Build a basic profitability waterfall only when exact revenue, cost, and profit exist."""
    revenue_value = metric_value(snapshot, "operating_revenue")
    cost_value = metric_value(snapshot, "operating_cost")
    profit_value = metric_value(snapshot, "net_profit_parent")
    if revenue_value is None or cost_value is None or profit_value is None:
        return None
    revenue = safe_float(revenue_value)
    cost = safe_float(cost_value)
    profit = safe_float(profit_value)
    
    gross_profit = revenue - cost
    
    steps = [
        {"name": "营业收入", "value": revenue, "base": 0},
        {"name": "营业成本", "value": -cost, "base": revenue - cost},
        {"name": "毛利", "value": gross_profit, "base": 0},
        {"name": "期间费用/其他", "value": -(gross_profit - profit), "base": profit},
        {"name": "归母净利润", "value": profit, "base": 0},
    ]
    
    # Recalculate bases for waterfall
    cumulative = 0
    for step in steps:
        step["base"] = cumulative
        if step["name"] not in ["营业收入", "毛利", "归母净利润"]:
            cumulative += step["value"]
        else:
            cumulative = 0
    
    return {"steps": steps}


def build_profitability_waterfall_from_bridge(bridge_data: dict[str, Any] | None) -> dict[str, Any] | None:
    """Build a compact waterfall using the same reconciled bridge as the hero chart."""
    if not bridge_data:
        return None
    nodes = bridge_data.get("flow_nodes") or {}
    revenue = nodes.get("revenue", {}).get("value")
    cost = nodes.get("cost", {}).get("value")
    gross_profit = nodes.get("gross_profit", {}).get("value")
    operating_adjustments = nodes.get("operating_adjustments", {}).get("value")
    operating_profit = nodes.get("operating_profit", {}).get("value")
    pretax_profit = nodes.get("pretax_profit", {}).get("value")
    income_tax = nodes.get("income_tax", {}).get("value")
    attribution = nodes.get("attribution", {}).get("value")
    parent_net_profit = nodes.get("parent_net_profit", {}).get("value")
    if revenue is None:
        return None

    steps: list[dict[str, Any]] = []
    current = 0.0

    def add_absolute(name: str, value: Any, kind: str = "subtotal") -> None:
        nonlocal current
        val = safe_float(value)
        steps.append({"name": name, "value": val, "base": 0.0, "kind": kind, "end": val})
        current = val

    def add_delta(name: str, delta: Any, kind: str = "delta") -> None:
        nonlocal current
        val = safe_float(delta)
        steps.append({"name": name, "value": val, "base": current, "kind": kind, "end": current + val})
        current += val

    add_absolute(nodes.get("revenue", {}).get("name") or "营业收入", revenue, "start")
    if cost is not None:
        add_delta("营业成本", -safe_float(cost), "cost")
    if gross_profit is not None:
        add_absolute("毛利", gross_profit, "subtotal")
    if operating_adjustments is not None:
        add_delta("费用/减值/其他", -safe_float(operating_adjustments), "expense")
    if operating_profit is not None:
        add_absolute(nodes.get("operating_profit", {}).get("name") or "营业利润", operating_profit, "subtotal")
    if pretax_profit is not None and operating_profit is not None:
        pretax_delta = safe_float(pretax_profit) - safe_float(operating_profit)
        if abs(pretax_delta) > 0.000001:
            add_delta("营业外收支", pretax_delta, "non_operating")
        add_absolute(nodes.get("pretax_profit", {}).get("name") or "利润总额", pretax_profit, "subtotal")
    if income_tax is not None and abs(safe_float(income_tax)) > 0.000001:
        add_delta("所得税", -safe_float(income_tax), "tax")
    if attribution is not None and abs(safe_float(attribution)) > 0.000001:
        add_delta("归属调整", -safe_float(attribution), "minority")
    if parent_net_profit is not None:
        add_absolute(nodes.get("parent_net_profit", {}).get("name") or "归母净利润", parent_net_profit, "end")

    return {"steps": steps}


def _period_label(snapshot: dict[str, Any], preflight: dict[str, Any] | None = None) -> str:
    preflight = preflight or {}
    report_type = str(preflight.get("report_type") or snapshot.get("report_type") or "").strip().lower()
    report_year = str(preflight.get("report_year") or snapshot.get("report_year") or "").strip()
    if report_type in {"annual_report", "annual", "yearly"}:
        return f"{report_year}年度"
    if report_type in {"semiannual_report", "semiannual", "half_year", "half-year"}:
        return f"{report_year}半年度"
    if report_type in {"quarterly_report", "quarterly", "quarter"}:
        return f"{report_year}季度"
    return f"{report_year}报告期"


def _bridge_metric(snapshot: dict[str, Any], key: str, year: str = "2025") -> float | None:
    value = metric_value(snapshot, key, year)
    if value is None:
        return None
    return safe_float(value)


def _report_metrics_path(work_dir: Path | None, year: str) -> Path | None:
    if not work_dir:
        return None
    company_dir = work_dir.parents[2] if len(work_dir.parents) > 2 else None
    if not company_dir:
        return None
    candidates = [
        company_dir / "metrics" / "reports" / f"{year}-annual" / "three_statements.json",
        company_dir / "metrics" / "latest" / "three_statements.json",
        company_dir / "metrics" / "three_statements.json",
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def _raw_statement_rows(work_dir: Path | None, year: str, statement_type: str) -> list[dict[str, Any]]:
    metrics_path = _report_metrics_path(work_dir, year)
    if not metrics_path:
        return []
    try:
        payload = json.loads(metrics_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    metrics = payload.get("data", {}).get("metrics", []) if isinstance(payload, dict) else []
    rows: list[dict[str, Any]] = []
    for item in metrics if isinstance(metrics, list) else []:
        if not isinstance(item, dict):
            continue
        if item.get("statement_type") != statement_type or item.get("scope") != "consolidated":
            continue
        period = str(item.get("period") or "")
        if period != str(year) and not period.startswith(f"{year}-"):
            continue
        rows.append(item)
    return rows


def _raw_income_statement_rows(work_dir: Path | None, year: str) -> list[dict[str, Any]]:
    return _raw_statement_rows(work_dir, year, "income_statement")


def _raw_cashflow_statement_rows(work_dir: Path | None, year: str) -> list[dict[str, Any]]:
    return _raw_statement_rows(work_dir, year, "cash_flow_statement")


def _raw_balance_sheet_rows(work_dir: Path | None, year: str) -> list[dict[str, Any]]:
    return _raw_statement_rows(work_dir, year, "balance_sheet")


def _raw_statement_value(rows: list[dict[str, Any]], metric_name: str) -> float | None:
    for item in rows:
        if item.get("metric_name") == metric_name:
            value = item.get("normalized_value")
            if value is not None:
                return safe_float(value)
    return None


def _raw_statement_value_any(rows: list[dict[str, Any]], metric_names: list[str]) -> float | None:
    for name in metric_names:
        value = _raw_statement_value(rows, name)
        if value is not None:
            return value
    normalized_targets = {re.sub(r"[\s（）()①-⑳注:：*]", "", name) for name in metric_names}
    for item in rows:
        metric_name = str(item.get("metric_name") or "")
        normalized_name = re.sub(r"[\s（）()①-⑳注:：*]", "", metric_name)
        if normalized_name in normalized_targets:
            value = item.get("normalized_value")
            if value is not None:
                return safe_float(value)
    return None


def _normalized_statement_name(name: str) -> str:
    return re.sub(r"[\s（）()①-⑳注:：*·、，,]", "", str(name or ""))


def _snapshot_metric_value_strict(
    snapshot: dict[str, Any],
    year: str,
    canonical_keys: list[str],
    allowed_display_names: list[str],
) -> tuple[float | None, str | None]:
    allowed_keys = set(canonical_keys)
    allowed_names = {_normalized_statement_name(name) for name in allowed_display_names}
    for source_name in ["metrics", "key_metrics"]:
        source = snapshot.get(source_name, {})
        if not isinstance(source, dict):
            continue
        for metric_key, item in source.items():
            if not isinstance(item, dict):
                continue
            names = {
                _normalized_statement_name(metric_key),
                _normalized_statement_name(item.get("canonical_name")),
                _normalized_statement_name(item.get("display_name")),
            }
            key_ok = metric_key in allowed_keys or str(item.get("canonical_name") or "") in allowed_keys
            name_ok = bool(allowed_names.intersection(names))
            if not key_ok and not name_ok:
                continue
            value = metric_value_from_item(item, year)
            if value is not None:
                label = str(item.get("display_name") or item.get("canonical_name") or metric_key)
                return safe_float(value), f"{source_name}:{label}"
    return None, None


def _read_verified_metric(
    snapshot: dict[str, Any],
    year: str,
    rows: list[dict[str, Any]],
    field: str,
    statement_names: list[str],
    snapshot_keys: list[str],
    sources: dict[str, dict[str, Any]] | None = None,
) -> float | None:
    for item in rows:
        if not isinstance(item, dict):
            continue
        if item.get("metric_key") in snapshot_keys and item.get("normalized_value") is not None:
            if sources is not None:
                sources[field] = {
                    "source": "three_statements",
                    "scope": "consolidated",
                    "row": item.get("metric_name") or item.get("metric_key"),
                    "metric_key": item.get("metric_key"),
                }
            return safe_float(item.get("normalized_value"))
    raw_value = _raw_statement_value_any(rows, statement_names)
    if raw_value is not None:
        if sources is not None:
            sources[field] = {"source": "three_statements", "scope": "consolidated", "row": statement_names[0]}
        return raw_value
    snapshot_value, label = _snapshot_metric_value_strict(snapshot, year, snapshot_keys, statement_names)
    if snapshot_value is not None:
        if sources is not None:
            sources[field] = {"source": "metric_snapshot_strict", "row": label}
        return snapshot_value
    if sources is not None:
        sources[field] = {"source": "missing", "row": statement_names[0]}
    return None


def _raw_income_value(rows: list[dict[str, Any]], metric_name: str) -> float | None:
    return _raw_statement_value(rows, metric_name)


def _coalesce(*values: float | None) -> float | None:
    for value in values:
        if value is not None:
            return value
    return None


def _positive_outflow(value: float | None) -> float | None:
    """Normalize ordinary expense rows to positive outflow values."""
    if value is None:
        return None
    return abs(value) if abs(value) > 0.000001 else 0.0


def _negative_outflow_encoding(values: list[float | None]) -> bool:
    nonzero = [value for value in values if value is not None and abs(value) > 0.000001]
    if len(nonzero) < 2:
        return False
    negative_count = sum(1 for value in nonzero if value < 0)
    positive_count = sum(1 for value in nonzero if value > 0)
    return negative_count >= 2 and negative_count > positive_count


def _conditional_positive_outflow(value: float | None, reversed_encoding: bool) -> float | None:
    if value is None:
        return None
    if reversed_encoding and value < 0:
        return abs(value)
    return value


def _parse_money_yi(raw: str, unit_hint: str = "") -> float | None:
    cleaned = raw.replace(",", "").replace("，", "").strip()
    if not cleaned:
        return None
    try:
        value = float(cleaned)
    except ValueError:
        return None
    if "千元" in unit_hint:
        return value / 100_000
    if "万元" in unit_hint:
        return value / 10_000
    return value / 100_000_000


def _parse_pct(raw: str) -> float | None:
    cleaned = raw.replace("%", "").replace("％", "").replace(",", "").strip()
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _looks_like_money_cell(raw: str) -> bool:
    cleaned = raw.replace(",", "").replace("，", "").strip()
    if not cleaned or "%" in raw or "％" in raw:
        return False
    try:
        value = abs(float(cleaned))
    except ValueError:
        return False
    return value >= 10_000 or "," in raw or "，" in raw


def _growth_pct(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None or abs(previous) < 1e-9:
        return None
    return (current - previous) / abs(previous) * 100


def _extract_td_rows(table_html: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for row_html in re.findall(r"<tr[^>]*>(.*?)</tr>", table_html, flags=re.I | re.S):
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row_html, flags=re.I | re.S)
        cleaned = [re.sub(r"<[^>]+>", "", cell).strip() for cell in cells]
        if cleaned:
            rows.append(cleaned)
    return rows


def _revenue_segment_entry(
    name: str,
    revenue: float | None,
    previous_revenue: float | None = None,
    cost: float | None = None,
    previous_cost: float | None = None,
    share: float | None = None,
    revenue_yoy: float | None = None,
) -> dict[str, Any] | None:
    if revenue is None or revenue <= 0:
        return None
    gross_margin = (revenue - cost) / revenue * 100 if cost is not None and revenue else None
    return {
        "name": name,
        "revenue": revenue,
        "cost": cost,
        "gross_margin": gross_margin,
        "revenue_yoy": revenue_yoy if revenue_yoy is not None else _growth_pct(revenue, previous_revenue),
        "cost_yoy": _growth_pct(cost, previous_cost) if cost is not None and previous_cost is not None else None,
        "share": share,
    }


def _normalize_product_segment_name(name: str) -> str:
    cleaned = re.sub(r"\s+", "", str(name or "")).strip()
    return re.sub(r"^其中[:：]?", "", cleaned).strip()


def _select_product_level_segments(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    i = 0
    while i < len(items):
        item = dict(items[i])
        next_item = items[i + 1] if i + 1 < len(items) else None
        if isinstance(next_item, dict) and str(next_item.get("raw_name") or "").strip().startswith(("其中", "其中:")):
            children: list[dict[str, Any]] = []
            total_revenue = 0.0
            total_share = 0.0
            j = i + 1
            while j < len(items):
                child = dict(items[j])
                children.append(child)
                total_revenue += safe_float(child.get("revenue"))
                total_share += safe_float(child.get("share"))
                parent_revenue = safe_float(item.get("revenue"))
                parent_share = safe_float(item.get("share"))
                if parent_share and abs(total_share - parent_share) <= max(0.5, parent_share * 0.03):
                    j += 1
                    break
                if parent_revenue and abs(total_revenue - parent_revenue) <= max(1.0, parent_revenue * 0.03):
                    j += 1
                    break
                j += 1
            if children and (
                abs(total_share - safe_float(item.get("share"))) <= max(0.5, safe_float(item.get("share")) * 0.05)
                or abs(total_revenue - safe_float(item.get("revenue"))) <= max(1.0, safe_float(item.get("revenue")) * 0.05)
            ):
                for child in children:
                    child["name"] = _normalize_product_segment_name(str(child.get("raw_name") or child.get("name") or ""))
                    selected.append(child)
                i = j
                continue
        item["name"] = _normalize_product_segment_name(str(item.get("raw_name") or item.get("name") or ""))
        selected.append(item)
        i += 1
    return selected


def _product_segments_from_rows(rows: list[list[str]], unit_hint: str = "") -> list[dict[str, Any]]:
    in_product = False
    segments: list[dict[str, Any]] = []
    for row in rows:
        if row == ["分产品"]:
            in_product = True
            continue
        if row and row[0] == "分地区":
            break
        if not in_product or len(row) < 6:
            continue
        value = _parse_money_yi(row[1], unit_hint)
        yoy = _parse_pct(row[5])
        if value is None or value <= 0:
            continue
        segments.append(
            {
                "raw_name": row[0],
                "name": _normalize_product_segment_name(row[0]),
                "revenue": value,
                "revenue_yoy": yoy,
                "share": _parse_pct(row[2]),
            }
        )
    return _select_product_level_segments(segments)


def _product_costs_from_rows(rows: list[list[str]], unit_hint: str = "") -> dict[str, dict[str, Any]]:
    in_product = False
    costs: list[dict[str, Any]] = []
    for row in rows:
        if row == ["分产品"]:
            in_product = True
            continue
        if row and row[0] == "分地区":
            break
        if not in_product or len(row) < 7:
            continue
        revenue = _parse_money_yi(row[1], unit_hint)
        cost = _parse_money_yi(row[2], unit_hint)
        if revenue is None or revenue <= 0 or cost is None:
            continue
        costs.append(
            {
                "raw_name": row[0],
                "name": _normalize_product_segment_name(row[0]),
                "revenue": revenue,
                "cost": cost,
                "gross_margin": _parse_pct(row[3]),
                "cost_yoy": _parse_pct(row[5]),
                "share": None,
            }
        )
    selected = _select_product_level_segments(costs)
    return {
        _normalize_product_segment_name(str(item.get("name") or item.get("raw_name") or "")): item
        for item in selected
        if item.get("name")
    }


def _extract_product_segments_from_report_markdown(work_dir: Path | None) -> list[dict[str, Any]]:
    if not work_dir:
        return []
    company_dir = work_dir.parents[2] if len(work_dir.parents) > 2 else None
    report_md = company_dir / "reports" / "2025-annual" / "report.md" if company_dir else None
    if not report_md or not report_md.exists():
        return []
    try:
        text = report_md.read_text(encoding="utf-8")
    except OSError:
        return []

    anchor_match = re.search(r"营业收入构成|主营业务分行业、分产品", text)
    anchor = anchor_match.start() if anchor_match else -1
    end_match = re.search(r"\n#{1,6}\s*（?3[）.)、]", text[anchor + 1 :]) if anchor >= 0 else None
    end = anchor + 1 + end_match.start() if end_match else -1
    window = text[anchor:end] if anchor >= 0 and end > anchor else text[anchor:] if anchor >= 0 else text
    tables = re.findall(r"<table>.*?</table>", window, flags=re.I | re.S)
    if not tables:
        return []
    unit_hint = "千元" if re.search(r"单位[:：]?\s*千元", window) else "万元" if re.search(r"单位[:：]?\s*万元", window) else "元"

    segment_names = {"整车业务", "零部件业务", "劳务及其他"}
    extracted: dict[str, dict[str, Any]] = {}
    for table in tables:
        rows = _extract_td_rows(table)
        for row in rows:
            if not row:
                continue
            name = re.sub(r"\s+", "", row[0])
            has_money_series = len(row) >= 5 and all(_looks_like_money_cell(row[i]) for i in [1, 2, 3, 4])
            if name in segment_names and has_money_series:
                entry = _revenue_segment_entry(
                    row[0],
                    _parse_money_yi(row[1], unit_hint),
                    _parse_money_yi(row[3], unit_hint),
                    _parse_money_yi(row[2], unit_hint),
                    _parse_money_yi(row[4], unit_hint),
                )
                if entry and safe_float(entry.get("revenue")) > safe_float(extracted.get(row[0], {}).get("revenue")):
                    extracted[row[0]] = entry
            elif name == "其他业务" and has_money_series:
                entry = _revenue_segment_entry(
                    "其他业务",
                    _parse_money_yi(row[1], unit_hint),
                    _parse_money_yi(row[3], unit_hint),
                    _parse_money_yi(row[2], unit_hint),
                    _parse_money_yi(row[4], unit_hint),
                )
                if entry and safe_float(entry.get("revenue")) > safe_float(extracted.get("其他业务", {}).get("revenue")):
                    extracted["其他业务"] = entry

    if len(extracted) >= 2:
        total = sum(safe_float(item.get("revenue")) for item in extracted.values())
        if total > 0:
            for item in extracted.values():
                item["share"] = safe_float(item.get("revenue")) / total * 100
        return sorted(extracted.values(), key=lambda item: safe_float(item.get("revenue")), reverse=True)

    product_table_index = None
    segments: list[dict[str, Any]] = []
    for index, table in enumerate(tables):
        rows = _extract_td_rows(table)
        table_segments = _product_segments_from_rows(rows, unit_hint)
        if len(table_segments) >= 2:
            segments = table_segments
            product_table_index = index
            break

    if len(segments) < 2:
        return []

    # Add product-level costs from the 10%+ revenue/profit table when available.
    search_start = (product_table_index + 1) if product_table_index is not None else 0
    for table in tables[search_start:]:
        cost_map = _product_costs_from_rows(_extract_td_rows(table), unit_hint)
        if not cost_map:
            continue
        for segment in segments:
            cost_item = cost_map.get(_normalize_product_segment_name(str(segment.get("name") or "")))
            if cost_item:
                segment["cost"] = cost_item.get("cost")
                segment["gross_margin"] = cost_item.get("gross_margin")
                segment["cost_yoy"] = cost_item.get("cost_yoy")
        break

    return segments


def _income_bridge_segments(snapshot: dict[str, Any], total_revenue: float, work_dir: Path | None) -> list[dict[str, Any]]:
    segments = snapshot.get("business_segments")
    if not isinstance(segments, list) or not segments:
        segments = _extract_product_segments_from_report_markdown(work_dir)
    normalized: list[dict[str, Any]] = []
    for segment in segments if isinstance(segments, list) else []:
        if not isinstance(segment, dict):
            continue
        name = str(segment.get("name") or segment.get("segment") or "").strip()
        revenue = safe_float(segment.get("revenue") or segment.get("value"), 0.0)
        if not name or revenue <= 0:
            continue
        normalized.append(
            {
                "name": name,
                "revenue": revenue,
                "cost": segment.get("cost"),
                "gross_margin": segment.get("gross_margin"),
                "revenue_yoy": segment.get("revenue_yoy"),
                "share": segment.get("share") if segment.get("share") is not None else revenue / total_revenue * 100 if total_revenue else None,
            }
        )

    if not normalized:
        normalized = [{"name": "营业收入", "revenue": total_revenue, "share": 100.0}]
    else:
        segment_total = sum(safe_float(item.get("revenue")) for item in normalized)
        residual = total_revenue - segment_total if total_revenue else 0.0
        if residual > max(total_revenue * 0.005, 1.0):
            normalized.append(
                {
                    "name": "利息/手续费等",
                    "revenue": residual,
                    "cost": None,
                    "gross_margin": None,
                    "revenue_yoy": None,
                    "share": residual / total_revenue * 100 if total_revenue else None,
                }
            )
        if total_revenue:
            for item in normalized:
                item["share"] = safe_float(item.get("revenue")) / total_revenue * 100

    normalized.sort(key=lambda item: safe_float(item.get("revenue")), reverse=True)
    if len(normalized) > 8:
        kept = normalized[:7]
        rest = normalized[7:]
        kept.append(
            {
                "name": "其他分项",
                "revenue": sum(safe_float(item.get("revenue")) for item in rest),
                "share": sum(safe_float(item.get("share")) for item in rest),
                "revenue_yoy": None,
            }
        )
        normalized = kept
    return normalized


def build_income_bridge_data(
    snapshot: dict[str, Any],
    preflight: dict[str, Any] | None = None,
    work_dir: Path | None = None,
) -> dict[str, Any] | None:
    """Build a deterministic income-to-net-profit bridge for the hero chart."""
    preflight = preflight or {}
    year = str(preflight.get("report_year") or snapshot.get("report_year") or "2025")
    raw_rows = _raw_income_statement_rows(work_dir, year)

    total_operating_revenue = _coalesce(_raw_income_value(raw_rows, "营业总收入"), _bridge_metric(snapshot, "total_operating_revenue", year))
    operating_revenue = _coalesce(_raw_income_value(raw_rows, "营业收入"), _bridge_metric(snapshot, "operating_revenue", year))
    if total_operating_revenue is not None:
        total_revenue = total_operating_revenue
        revenue_field = "total_operating_revenue"
        revenue_name = "营业总收入"
    else:
        total_revenue = operating_revenue
        revenue_field = "operating_revenue"
        revenue_name = "营业收入"

    operating_cost_item = metric_item(snapshot, "operating_cost")
    operating_cost_label = str(operating_cost_item.get("display_name") or operating_cost_item.get("canonical_name") or "") if isinstance(operating_cost_item, dict) else ""
    snapshot_operating_cost = _bridge_metric(snapshot, "operating_cost", year)
    total_operating_cost = _coalesce(
        _raw_income_value(raw_rows, "营业总成本"),
        snapshot_operating_cost if operating_cost_label == "营业总成本" else None,
    )
    operating_cost = _coalesce(
        _raw_income_value(raw_rows, "营业成本"),
        snapshot_operating_cost if operating_cost_label != "营业总成本" else None,
    )
    taxes_and_surcharges = _coalesce(_raw_income_value(raw_rows, "税金及附加"), _bridge_metric(snapshot, "taxes_and_surcharges", year))
    sales_expenses = _coalesce(_raw_income_value(raw_rows, "销售费用"), _bridge_metric(snapshot, "sales_expenses", year))
    administrative_expenses = _coalesce(_raw_income_value(raw_rows, "管理费用"), _bridge_metric(snapshot, "administrative_expenses", year))
    research_expenses = _coalesce(_raw_income_value(raw_rows, "研发费用"), _bridge_metric(snapshot, "research_expenses", year))
    financial_expenses = _coalesce(_raw_income_value(raw_rows, "财务费用"), _bridge_metric(snapshot, "financial_expenses", year))
    asset_impairment_loss = _coalesce(_raw_income_value(raw_rows, "资产减值损失"), _bridge_metric(snapshot, "asset_impairment_loss", year))
    credit_impairment_loss = _coalesce(_raw_income_value(raw_rows, "信用减值损失"), _bridge_metric(snapshot, "credit_impairment_loss", year))
    other_income = _coalesce(_raw_income_value(raw_rows, "其他收益"), _bridge_metric(snapshot, "other_income", year))
    investment_income = _coalesce(_raw_income_value(raw_rows, "投资收益"), _bridge_metric(snapshot, "investment_income", year))
    fair_value_change_income = _coalesce(_raw_income_value(raw_rows, "公允价值变动收益"), _bridge_metric(snapshot, "fair_value_change_income", year))
    asset_disposal_income = _coalesce(_raw_income_value(raw_rows, "资产处置收益"), _bridge_metric(snapshot, "asset_disposal_income", year))
    non_operating_income = _coalesce(_raw_income_value(raw_rows, "营业外收入"), _bridge_metric(snapshot, "non_operating_income", year))
    non_operating_expenses = _coalesce(_raw_income_value(raw_rows, "营业外支出"), _bridge_metric(snapshot, "non_operating_expenses", year))
    income_tax_expense = _coalesce(_raw_income_value(raw_rows, "所得税费用"), _bridge_metric(snapshot, "income_tax_expense", year))
    minority_profit_loss = _coalesce(_raw_income_value(raw_rows, "少数股东损益"), _bridge_metric(snapshot, "minority_profit_loss", year))
    reported_total_profit = _coalesce(_raw_income_value(raw_rows, "利润总额"), _bridge_metric(snapshot, "total_profit", year))
    reported_net_profit = _coalesce(_raw_income_value(raw_rows, "净利润"), _bridge_metric(snapshot, "net_profit", year))
    parent_net_profit = _coalesce(_raw_income_value(raw_rows, "归属于母公司股东的净利润"), _bridge_metric(snapshot, "net_profit_parent", year))

    reversed_outflow_encoding = _negative_outflow_encoding(
        [
            total_operating_cost,
            operating_cost,
            taxes_and_surcharges,
            sales_expenses,
            administrative_expenses,
            research_expenses,
        ]
    )
    total_operating_cost = _positive_outflow(total_operating_cost)
    operating_cost = _positive_outflow(operating_cost)
    taxes_and_surcharges = _positive_outflow(taxes_and_surcharges)
    sales_expenses = _positive_outflow(sales_expenses)
    administrative_expenses = _positive_outflow(administrative_expenses)
    research_expenses = _positive_outflow(research_expenses)
    financial_expenses = _conditional_positive_outflow(financial_expenses, reversed_outflow_encoding)
    non_operating_expenses = _positive_outflow(non_operating_expenses)
    income_tax_expense = _conditional_positive_outflow(income_tax_expense, reversed_outflow_encoding)

    if total_revenue is None:
        return None

    known_fields: list[str] = [revenue_field]
    missing_fields: list[str] = []
    non_operating_net = None
    if non_operating_income is not None or non_operating_expenses is not None:
        non_operating_net = (non_operating_income or 0.0) - (non_operating_expenses or 0.0)

    expense_items = []
    use_total_cost = total_operating_cost is not None and operating_cost is None
    if use_total_cost:
        expense_items.append(("营业总成本", total_operating_cost, "cost", "total_operating_cost"))
    else:
        expense_items.extend(
            [
                ("营业成本", operating_cost, "cost", "operating_cost"),
                ("税金及附加", taxes_and_surcharges, "expense", "taxes_and_surcharges"),
                ("销售费用", sales_expenses, "expense", "sales_expenses"),
                ("管理费用", administrative_expenses, "expense", "administrative_expenses"),
                ("研发费用", research_expenses, "expense", "research_expenses"),
                ("财务费用", financial_expenses, "expense", "financial_expenses"),
            ]
        )
    expense_items.extend(
        [
            ("其他收益", other_income, "income", "other_income"),
            ("投资收益", investment_income, "income", "investment_income"),
            ("公允价值变动收益", fair_value_change_income, "income", "fair_value_change_income"),
            ("信用减值损失", credit_impairment_loss, "signed", "credit_impairment_loss"),
            ("资产减值损失", asset_impairment_loss, "signed", "asset_impairment_loss"),
            ("资产处置收益", asset_disposal_income, "signed", "asset_disposal_income"),
            ("营业外收支净额", non_operating_net, "income", "non_operating_net"),
        ]
    )

    cumulative = total_revenue
    steps: list[dict[str, Any]] = [
        {
            "name": revenue_name,
            "value": total_revenue,
            "base": 0.0,
            "kind": "start",
            "label": "起点",
        }
    ]
    for name, value, kind, field_name in expense_items:
        if value is None:
            missing_fields.append(field_name)
            continue
        known_fields.append(field_name)
        if abs(value) <= 0.000001:
            continue
        delta = -value if kind in {"cost", "expense"} else value
        cumulative += delta
        steps.append(
            {
                "name": name,
                "value": delta,
                "base": cumulative - delta,
                "kind": kind,
                "label": "费用" if delta < 0 else "收益",
            }
        )

    pretax_profit = reported_total_profit
    if pretax_profit is not None:
        known_fields.append("total_profit")
        pretax_gap = pretax_profit - cumulative
        if abs(pretax_gap) > 0.01:
            steps.append(
                {
                    "name": "利润表其他项目",
                    "value": pretax_gap,
                    "base": cumulative,
                    "kind": "residual",
                    "label": "未归因",
                }
            )
            cumulative = pretax_profit
        steps.append(
            {
                "name": "利润总额",
                "value": pretax_profit,
                "base": 0.0,
                "kind": "subtotal",
                "label": "利润总额",
            }
        )
        cumulative = pretax_profit
    else:
        missing_fields.append("total_profit")

    if income_tax_expense is not None:
        known_fields.append("income_tax_expense")
    else:
        missing_fields.append("income_tax_expense")
    if income_tax_expense is not None and abs(income_tax_expense) > 0.000001:
        delta = -income_tax_expense
        cumulative += delta
        steps.append(
            {
                "name": "所得税费用",
                "value": delta,
                "base": cumulative - delta,
                "kind": "tax",
                "label": "税项",
            }
        )

    if minority_profit_loss is not None:
        known_fields.append("minority_profit_loss")
    if minority_profit_loss is not None and abs(minority_profit_loss) > 0.000001:
        delta = -minority_profit_loss
        cumulative += delta
        steps.append(
            {
                "name": "少数股东损益",
                "value": delta,
                "base": cumulative - delta,
                "kind": "minority",
                "label": "归属调整",
            }
        )

    ending_name = "归母净利润"
    ending_is_reported = True
    if parent_net_profit is None:
        missing_fields.append("net_profit_parent")
        parent_net_profit = reported_net_profit
        ending_name = "净利润"
        if reported_net_profit is not None:
            known_fields.append("net_profit")
    if parent_net_profit is None:
        parent_net_profit = cumulative
        ending_name = "已识别结果"
        ending_is_reported = False
    else:
        known_fields.append("net_profit_parent")
    steps.append(
        {
            "name": ending_name,
            "value": parent_net_profit,
            "base": 0.0,
            "kind": "end",
            "label": "终点",
        }
    )

    bridge_gap = None
    if parent_net_profit is not None and ending_is_reported:
        bridge_gap = parent_net_profit - cumulative
        if abs(bridge_gap) > 0.01:
            steps.insert(
                -1,
                {
                    "name": "其他/口径差",
                    "value": bridge_gap,
                    "base": cumulative,
                    "kind": "residual",
                    "label": "未归因",
                },
            )
            cumulative += bridge_gap

    product_segments = _income_bridge_segments(snapshot, total_revenue, work_dir)
    gross_profit = total_revenue - operating_cost if operating_cost is not None else None
    cost_node_value = operating_cost if operating_cost is not None else total_operating_cost
    cost_node_name = "营业成本" if operating_cost is not None else "营业总成本"
    operating_profit = _bridge_metric(snapshot, "operating_profit", year)
    if raw_rows:
        operating_profit = _coalesce(_raw_income_value(raw_rows, "营业利润"), operating_profit)
    operating_adjustments = None
    if gross_profit is not None and operating_profit is not None:
        operating_adjustments = gross_profit - operating_profit

    operating_adjustment_items: list[dict[str, Any]] = []
    for name, value, field_name, signed_mode in [
        ("税金及附加", taxes_and_surcharges, "taxes_and_surcharges", "expense"),
        ("销售费用", sales_expenses, "sales_expenses", "expense"),
        ("管理费用", administrative_expenses, "administrative_expenses", "expense"),
        ("研发费用", research_expenses, "research_expenses", "expense"),
        ("财务费用", financial_expenses, "financial_expenses", "expense"),
        ("其他收益", other_income, "other_income", "income"),
        ("投资收益", investment_income, "investment_income", "income"),
        ("公允价值变动收益", fair_value_change_income, "fair_value_change_income", "income"),
        ("信用减值损失", credit_impairment_loss, "credit_impairment_loss", "signed"),
        ("资产减值损失", asset_impairment_loss, "asset_impairment_loss", "signed"),
        ("资产处置收益", asset_disposal_income, "asset_disposal_income", "signed"),
    ]:
        if value is None or abs(value) <= 0.000001:
            continue
        if signed_mode == "expense":
            impact = value
        elif signed_mode == "income":
            impact = -value
        else:
            impact = -value
        operating_adjustment_items.append(
            {
                "name": name,
                "value": value,
                "impact": impact,
                "field": field_name,
            }
        )

    profit_after_tax = reported_net_profit if reported_net_profit is not None else None
    tax_outflow = income_tax_expense if income_tax_expense is not None else None
    attribution_adjustment = None
    if profit_after_tax is not None and parent_net_profit is not None:
        attribution_adjustment = profit_after_tax - parent_net_profit

    flow_nodes = {
        "revenue": {"name": revenue_name, "value": total_revenue},
        "cost": {"name": cost_node_name, "value": cost_node_value},
        "gross_profit": {"name": "毛利", "value": gross_profit},
        "operating_adjustments": {"name": "期间费用/减值/其他", "value": operating_adjustments},
        "operating_profit": {"name": "营业利润" if safe_float(operating_profit) >= 0 else "营业亏损", "value": operating_profit},
        "pretax_profit": {"name": "利润总额" if safe_float(pretax_profit) >= 0 else "亏损总额", "value": pretax_profit},
        "income_tax": {"name": "所得税", "value": tax_outflow},
        "attribution": {"name": "其他/归属调整", "value": attribution_adjustment},
        "parent_net_profit": {
            "name": ending_name if safe_float(parent_net_profit) >= 0 else ending_name.replace("利润", "亏损"),
            "value": parent_net_profit,
        },
    }

    return {
        "period_label": _period_label(snapshot, preflight),
        "year": year,
        "currency_unit": "亿元",
        "starting_value": total_revenue,
        "ending_value": parent_net_profit,
        "pretax_profit": pretax_profit,
        "segments": product_segments,
        "flow_nodes": flow_nodes,
        "operating_adjustment_items": operating_adjustment_items,
        "bridge_gap": bridge_gap,
        "steps": steps,
        "known_fields": list(dict.fromkeys(known_fields)),
        "missing_fields": list(dict.fromkeys(missing_fields)),
        "notes": [
            "左侧收入分项优先取年报“营业收入构成-分产品”表。",
            "中部成本、毛利、营业利润和右侧净利链条取合并利润表口径。",
            "若分项或归属口径缺失，图中只展示已识别字段，不把缺失项补零。",
        ],
        "evidence_fields": list(dict.fromkeys(known_fields)),
    }


# =============================================================================
# SVG FALLBACK CHART HELPERS
# =============================================================================

_RENDERER_SVG_CHARTS_MODULE_PATH = Path(__file__).resolve().parent / "renderer_svg_charts.py"
_renderer_svg_charts_spec = importlib.util.spec_from_file_location("siq_renderer_svg_charts", _RENDERER_SVG_CHARTS_MODULE_PATH)
if not _renderer_svg_charts_spec or not _renderer_svg_charts_spec.loader:
    raise RuntimeError(f"missing renderer SVG charts module: {_RENDERER_SVG_CHARTS_MODULE_PATH}")
_renderer_svg_charts_module = importlib.util.module_from_spec(_renderer_svg_charts_spec)
_renderer_svg_charts_spec.loader.exec_module(_renderer_svg_charts_module)
svg_text = _renderer_svg_charts_module.svg_text
chart_attrs = _renderer_svg_charts_module.chart_attrs
svg_title = _renderer_svg_charts_module.svg_title
svg_income_bridge_chart = _renderer_svg_charts_module.svg_income_bridge_chart
svg_bar_line_chart = _renderer_svg_charts_module.svg_bar_line_chart
svg_cashflow_chart = _renderer_svg_charts_module.svg_cashflow_chart
svg_donut_chart = _renderer_svg_charts_module.svg_donut_chart
_radar_ring_points = _renderer_svg_charts_module._radar_ring_points
svg_radar_chart = _renderer_svg_charts_module.svg_radar_chart
svg_waterfall_chart = _renderer_svg_charts_module.svg_waterfall_chart
svg_peer_radar_chart = _renderer_svg_charts_module.svg_peer_radar_chart
svg_solvency_gauges = _renderer_svg_charts_module.svg_solvency_gauges


def render_html_report(
    preflight: dict[str, Any],
    snapshot: dict[str, Any],
    sections: list[dict[str, Any]],
    quality_report: dict[str, Any],
    work_dir: Path | None = None,
) -> str:
    """Render the complete HTML report."""
    
    company_id = preflight.get("company_id", snapshot.get("company_id", "未知公司"))
    report_year = preflight.get("report_year", snapshot.get("report_year", "2025"))
    
    # Build chart data
    revenue_profit_data = build_revenue_profit_trend_data(snapshot)
    cashflow_data = build_cashflow_data(snapshot, preflight, work_dir)
    asset_data = build_asset_structure_data(snapshot, preflight, work_dir)
    debt_data = build_debt_structure_data(snapshot, preflight, work_dir)
    dupont_data = build_dupont_data(snapshot, preflight, work_dir)
    solvency_data = build_solvency_gauges(snapshot, preflight, work_dir)
    peer_data = build_peer_comparison_data(snapshot, work_dir)
    income_bridge_data = build_income_bridge_data(snapshot, preflight, work_dir)
    profitability_data = build_profitability_waterfall_from_bridge(income_bridge_data) or build_profitability_waterfall_data(snapshot)
    
    # Build chart data scripts
    data_scripts = []
    if revenue_profit_data:
        data_scripts.append(f"window.revenueProfitData = {json.dumps(revenue_profit_data, ensure_ascii=False)};")
    if cashflow_data:
        data_scripts.append(f"window.cashFlowData = {json.dumps(cashflow_data, ensure_ascii=False)};")
    if asset_data:
        data_scripts.append(f"window.assetStructureData = {json.dumps(asset_data, ensure_ascii=False)};")
    if debt_data:
        data_scripts.append(f"window.debtStructureData = {json.dumps(debt_data, ensure_ascii=False)};")
    if dupont_data:
        data_scripts.append(f"window.dupontData = {json.dumps(dupont_data, ensure_ascii=False)};")
    if solvency_data:
        data_scripts.append(f"window.solvencyData = {json.dumps(solvency_data, ensure_ascii=False)};")
    if peer_data:
        data_scripts.append(f"window.peerComparisonData = {json.dumps(peer_data, ensure_ascii=False)};")
    if profitability_data:
        data_scripts.append(f"window.profitabilityData = {json.dumps(profitability_data, ensure_ascii=False)};")
    if income_bridge_data:
        data_scripts.append(f"window.incomeBridgeData = {json.dumps(income_bridge_data, ensure_ascii=False)};")
    
    data_script_block = f"<script>{''.join(data_scripts)}</script>" if data_scripts else ""
    
    # Build sections HTML
    sections_html = []
    for i, section in enumerate(sections):
        sid = section.get("section_id", "")
        title = section.get("title", "")
        
        section_html = f"""
        <section class="section" id="section-{sid}" tabindex="-1">
          <div class="section-header">
            <div class="section-number">{i + 1}</div>
            <h2><span class="section-title">{html_module.escape(title)}</span></h2>
          </div>
          <div class="section-content">
            {render_section_content(section, preflight)}
          </div>
        </section>
        """
        sections_html.append(section_html)
    
    # Build chart panels for specific sections
    chart_panels = {}
    
    # Revenue/Profit trend - goes after section 1 (Executive Summary)
    if revenue_profit_data:
        chart_panels["after_executive_summary"] = f"""
        <div class="chart-grid">
          {render_chart_section("revenue-profit-chart", "营业收入与净利润趋势", "large", fallback_svg=svg_bar_line_chart(revenue_profit_data), note="柱形为营业收入左轴，折线为归母净利润右轴，单位均为亿元。")}
          {render_chart_section("cashflow-chart", "现金流结构分析", "large", fallback_svg=svg_cashflow_chart(cashflow_data), note="按经营、投资、筹资、资本开支与自由现金流拆分，正负方向代表现金流入/流出。")}
        </div>
        """
    
    # Asset/Debt structure - goes after section 5 (Asset Quality)
    if asset_data or debt_data:
        charts = []
        if asset_data:
            charts.append(render_chart_section("asset-structure-chart", "资产结构分布", fallback_svg=svg_donut_chart(asset_data), note="按资产负债表项目金额占比展示，单位为亿元。"))
        if debt_data:
            charts.append(render_chart_section("debt-structure-chart", "负债结构分布", fallback_svg=svg_donut_chart(debt_data), note="按负债项目金额占比展示，单位为亿元。"))
        chart_panels["after_asset_quality"] = f'<div class="chart-grid">{"".join(charts)}</div>'
    
    # DuPont + Solvency - goes after section 6 (Debt)
    if dupont_data or solvency_data:
        dupont_html = render_chart_section("dupont-chart", "杜邦分析雷达图", extra_class="dupont-panel", fallback_svg=svg_radar_chart(dupont_data), note="左侧展示原始杜邦因子与公式，右侧为 0-100 标准化雷达展示分。") if dupont_data else ""
        solvency_html = ""
        if solvency_data:
            gauges = []
            gauge_configs = [
                ("gauge-debt-ratio", "资产负债率"),
                ("gauge-current", "流动比率"),
                ("gauge-quick", "速动比率"),
                ("gauge-cash", "现金比率"),
            ]
            for gid, gtitle in gauge_configs:
                gauges.append(f'<div id="{gid}" style="width:100%;height:180px;"></div>')
            solvency_html = f"""
            <div class="chart-container">
              <div class="chart-head">
                <div class="chart-title">偿债能力仪表盘</div>
                <div class="chart-note">资产负债率、流动比率、速动比率和现金比率，颜色用于提示压力区间。</div>
              </div>
              <div class="chart-grid-3">{''.join(gauges)}</div>
              <div class="chart-fallback">{svg_solvency_gauges(solvency_data)}</div>
            </div>
            """
        chart_panels["after_debt"] = f'<div class="chart-grid">{dupont_html}{solvency_html}</div>'
    
    # Peer comparison - goes after section 8 (Industry)
    if peer_data:
        chart_panels["after_industry"] = render_chart_section("peer-comparison-chart", "同业竞争对比", "large", fallback_svg=svg_peer_radar_chart(peer_data), note=f"本公司与同业中位数对比，样本数 {peer_data.get('peer_count', 0)}。")
    
    # Profitability waterfall - goes after section 4 (Profitability)
    if profitability_data:
        chart_panels["after_profitability"] = render_chart_section("profitability-waterfall", "盈利分解瀑布图", "large", fallback_svg=svg_waterfall_chart(profitability_data), note="从收入到利润口径拆分主要正负贡献，单位为亿元。")
    
    # Insert chart panels into sections
    final_sections_html = []
    for i, section_html in enumerate(sections_html):
        sid = sections[i].get("section_id", "")
        final_sections_html.append(section_html)
        
        # Insert charts after specific sections
        if sid == "executive_summary" and "after_executive_summary" in chart_panels:
            final_sections_html.append(f'<div class="container">{chart_panels["after_executive_summary"]}</div>')
        elif sid == "profitability_and_cost" and "after_profitability" in chart_panels:
            final_sections_html.append(f'<div class="container">{chart_panels["after_profitability"]}</div>')
        elif sid == "asset_quality_working_capital" and "after_asset_quality" in chart_panels:
            final_sections_html.append(f'<div class="container">{chart_panels["after_asset_quality"]}</div>')
        elif sid == "debt_liquidity" and "after_debt" in chart_panels:
            final_sections_html.append(f'<div class="container">{chart_panels["after_debt"]}</div>')
        elif sid == "industry_competition" and "after_industry" in chart_panels:
            final_sections_html.append(f'<div class="container">{chart_panels["after_industry"]}</div>')
    
    # Quality summary
    quality_badges = []
    if quality_report.get("overall_pass"):
        quality_badges.append('<span class="status-badge success"><span class="status-dot" style="background:#10b981;"></span>结构验收通过</span>')
    else:
        quality_badges.append('<span class="status-badge danger"><span class="status-dot" style="background:#ef4444;"></span>需复核</span>')
    
    if quality_report.get("all_key_numbers_have_evidence"):
        quality_badges.append('<span class="status-badge success">证据完整</span>')
    else:
        quality_badges.append('<span class="status-badge warning">证据待补</span>')
    
    review_count = len(quality_report.get("review_queue", []))
    if review_count > 0:
        quality_badges.append(f'<span class="status-badge info">{review_count}项待复核</span>')
    
    # Assemble final HTML
    html_doc = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{html_module.escape(str(company_id))} {html_module.escape(str(report_year))} 财务诊断报告</title>
  <style>{CSS_STYLES}</style>
  {data_script_block}
  {ECHARTS_SCRIPTS}
</head>
<body>
  <div class="progress-bar"><div class="progress-bar-fill"></div></div>
  
  <div class="main-content">
    {render_header(preflight, snapshot)}
    
    <div class="container">
      <!-- Quality badges -->
      {render_report_summary(preflight, snapshot, sections, quality_badges)}

      {render_source_legend()}
      
      <!-- KPI Cards -->
      {render_kpi_cards(snapshot)}

      <!-- Hero Chart -->
      {render_income_bridge_panel(income_bridge_data)}
      
      <!-- Sections with embedded charts -->
      {''.join(final_sections_html)}
      
      <!-- Footer -->
      <footer class="report-footer">
        <p>本报告为 A 股上市公司公开年报财务诊断，不构成投资建议</p>
        <p style="margin-top:4px;">生成时间：{datetime.now().strftime("%Y-%m-%d %H:%M:%S")} · SIQ Analysis v2.0</p>
      </footer>
    </div>
  </div>
</body>
</html>"""
    
    return html_doc


# =============================================================================
# BACKWARD COMPATIBILITY: Replace render_html in checkpoint renderer
# =============================================================================

def render_html_v2(
    markdown_text: str,
    preflight: dict[str, Any],
    snapshot: dict[str, Any],
    sections: list[dict[str, Any]] | None = None,
    quality_report: dict[str, Any] | None = None,
    work_dir: Path | None = None,
) -> str:
    """Drop-in replacement for render_html() in render_report_from_checkpoint.py.
    
    If sections and quality_report are provided, uses the new v2 renderer.
    Otherwise falls back to parsing markdown text.
    """
    if sections and quality_report:
        return render_html_report(preflight, snapshot, sections, quality_report, work_dir)
    
    # Fallback: parse markdown and wrap in basic HTML
    return render_html_report(
        preflight, snapshot,
        [{"section_id": "fallback", "title": "报告内容", "facts": [markdown_text], "calculations": [], "judgements": [], "risks_or_improvement_conditions": [], "evidence_ids": [], "review_required": False, "missing_fields": []}],
        {"overall_pass": False, "all_key_numbers_have_evidence": False, "review_queue": []},
        work_dir,
    )


if __name__ == "__main__":
    # Test with sample data
    test_snapshot = {
        "metrics": {
            "operating_revenue": {"values": {"2025": 1650.54, "2024": 1451.76}, "unit": "亿元"},
            "net_profit_parent": {"values": {"2025": 59.57, "2024": 24.75}, "unit": "亿元"},
            "net_operating_cash_flow": {"values": {"2025": 289.14, "2024": 225.16}, "unit": "亿元"},
            "total_assets": {"values": {"2025-12-31": 1439.06}, "unit": "亿元"},
            "total_liabilities": {"values": {"2025-12-31": 1020.48}, "unit": "亿元"},
            "gross_margin": {"values": {"2025": 29.14}, "unit": "%"},
            "monetary_funds": {"values": {"2025": 872.87}, "unit": "亿元"},
            "inventory": {"values": {"2025": 24.47}, "unit": "亿元"},
            "accounts_receivable": {"values": {"2025": 17.36}, "unit": "亿元"},
            "operating_cost": {"values": {"2025": 1169.54}, "unit": "亿元"},
            "current_assets": {"values": {"2025-12-31": 987.83}, "unit": "亿元"},
            "current_liabilities": {"values": {"2025-12-31": 937.00}, "unit": "亿元"},
        }
    }
    
    test_sections = [
        {"section_id": "executive_summary", "title": "一、执行摘要", "facts": ["公司2025年营业收入1650.54亿元，同比增长13.69%"], "calculations": ["毛利率29.14%，归母净利润59.57亿元"], "judgements": ["经营质量与财务安全需交叉验证"], "risks_or_improvement_conditions": ["扣非利润弱于归母利润需警惕"], "evidence_ids": ["operating_revenue:2025"], "review_required": False, "missing_fields": []},
        {"section_id": "key_changes", "title": "二、关键变化概览", "facts": ["收入同比增长13.69%"], "calculations": ["经营现金流同比增长28.42%"], "judgements": ["增长质量需验证"], "risks_or_improvement_conditions": ["毛利率承压风险"], "evidence_ids": ["net_operating_cash_flow:2025"], "review_required": False, "missing_fields": []},
    ]
    
    test_quality = {"overall_pass": True, "all_key_numbers_have_evidence": True, "review_queue": []}
    
    result = render_html_report(
        {"company_id": "601127", "stock_code": "601127", "report_year": 2025, "company_short_name": "赛力斯"},
        test_snapshot,
        test_sections,
        test_quality,
    )
    
    output_path = Path("/home/maoyd/siq-research-engine/data/hermes/home/profiles/siq_analysis/test_v2_renderer.html")
    output_path.write_text(result, encoding="utf-8")
    print(f"Test HTML written to: {output_path}")
    print(f"File size: {len(result)} bytes")
