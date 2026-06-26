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
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

PUBLIC_ORIGIN = os.environ.get("SIQ_PUBLIC_ORIGIN", "https://arthurmao.synology.me:8276").rstrip("/")


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
    "capital_expenditure": ["cash_for_purchases", "购建固定资产、无形资产和其他长期资产支付的现金"],
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


def build_cashflow_data(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Build data for cash flow composition chart."""
    ocf = safe_float(metric_value(snapshot, "net_operating_cash_flow"))
    icf = safe_float(metric_value(snapshot, "investing_cash_flow_net"))
    fcf = safe_float(metric_value(snapshot, "financing_cash_flow_net"))
    capex = safe_float(metric_value(snapshot, "capital_expenditure"))
    
    fcf_calc = ocf - capex if ocf and capex else None
    
    return {
        "operating": round(ocf, 2) if ocf else 0,
        "investing": round(icf, 2) if icf else 0,
        "financing": round(fcf, 2) if fcf else 0,
        "capex": round(capex, 2) if capex else 0,
        "free_cash_flow": round(fcf_calc, 2) if fcf_calc else None,
    }


def build_dupont_data(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Build DuPont analysis data."""
    revenue = safe_float(metric_value(snapshot, "operating_revenue"))
    net_profit = safe_float(metric_value(snapshot, "net_profit_parent"))
    total_assets = safe_float(metric_value(snapshot, "total_assets"))
    total_liabilities = safe_float(metric_value(snapshot, "total_liabilities"))
    equity = total_assets - total_liabilities if total_assets and total_liabilities else None
    
    net_margin = (net_profit / revenue * 100) if revenue and revenue != 0 else None
    asset_turnover = (revenue / total_assets) if total_assets and total_assets != 0 else None
    equity_multiplier = (total_assets / equity) if equity and equity != 0 else None
    roe = (net_margin * asset_turnover * equity_multiplier) if all([net_margin, asset_turnover, equity_multiplier]) else None
    
    return {
        "net_margin": round(net_margin, 2) if net_margin else None,
        "asset_turnover": round(asset_turnover, 4) if asset_turnover else None,
        "equity_multiplier": round(equity_multiplier, 2) if equity_multiplier else None,
        "roe": round(roe, 2) if roe else None,
    }


def build_asset_structure_data(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Build asset structure pie chart data."""
    total = safe_float(metric_value(snapshot, "total_assets"))
    if not total:
        return None
    
    cash = safe_float(metric_value(snapshot, "monetary_funds"))
    receivables = safe_float(metric_value(snapshot, "accounts_receivable"))
    inventory = safe_float(metric_value(snapshot, "inventory"))
    current = safe_float(metric_value(snapshot, "current_assets"))
    
    # Non-current assets = total - current
    non_current = total - current if current else total * 0.6  # estimate
    other_current = current - cash - receivables - inventory if current else total * 0.15
    
    return {
        "categories": [
            {"name": "货币资金", "value": round(cash, 2) if cash else 0},
            {"name": "应收账款", "value": round(receivables, 2) if receivables else 0},
            {"name": "存货", "value": round(inventory, 2) if inventory else 0},
            {"name": "其他流动资产", "value": round(max(0, other_current), 2)},
            {"name": "非流动资产", "value": round(max(0, non_current), 2)},
        ]
    }


def build_debt_structure_data(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Build debt structure data."""
    total = safe_float(metric_value(snapshot, "total_liabilities"))
    if not total:
        return None
    
    short_borrow = safe_float(metric_value(snapshot, "short_term_borrowings"))
    current_noncurrent = safe_float(metric_value(snapshot, "current_portion_noncurrent_liabilities"))
    long_borrow = safe_float(metric_value(snapshot, "long_term_borrowings"))
    notes_payable = safe_float(metric_value(snapshot, "notes_payable"))
    current = safe_float(metric_value(snapshot, "current_liabilities"))
    
    other_current = current - short_borrow - current_noncurrent - notes_payable if current else 0
    non_current = total - current if total and current else total * 0.3
    
    return {
        "categories": [
            {"name": "短期借款", "value": round(short_borrow, 2) if short_borrow else 0},
            {"name": "一年内到期非流动负债", "value": round(current_noncurrent, 2) if current_noncurrent else 0},
            {"name": "应付票据", "value": round(notes_payable, 2) if notes_payable else 0},
            {"name": "其他流动负债", "value": round(max(0, other_current), 2)},
            {"name": "长期借款", "value": round(long_borrow, 2) if long_borrow else 0},
            {"name": "其他非流动负债", "value": round(max(0, non_current - (long_borrow or 0)), 2)},
        ]
    }


def build_solvency_gauges(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Build solvency indicator gauges."""
    total_assets = safe_float(metric_value(snapshot, "total_assets"))
    total_liabilities = safe_float(metric_value(snapshot, "total_liabilities"))
    current_assets = safe_float(metric_value(snapshot, "current_assets"))
    current_liabilities = safe_float(metric_value(snapshot, "current_liabilities"))
    cash = safe_float(metric_value(snapshot, "monetary_funds"))
    
    debt_ratio = (total_liabilities / total_assets * 100) if total_assets else None
    current_ratio = (current_assets / current_liabilities) if current_liabilities else None
    quick_ratio = ((current_assets - safe_float(metric_value(snapshot, "inventory"))) / current_liabilities) if current_liabilities else None
    cash_ratio = (cash / current_liabilities) if current_liabilities else None
    
    return {
        "debt_ratio": round(debt_ratio, 2) if debt_ratio else None,
        "current_ratio": round(current_ratio, 2) if current_ratio else None,
        "quick_ratio": round(quick_ratio, 2) if quick_ratio else None,
        "cash_ratio": round(cash_ratio, 2) if cash_ratio else None,
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

CSS_STYLES = """
:root {
  --bg-primary: #0f172a;
  --bg-secondary: #1e293b;
  --bg-card: #1e293b;
  --bg-card-hover: #27354f;
  --text-primary: #f1f5f9;
  --text-secondary: #94a3b8;
  --text-muted: #64748b;
  --border-color: #334155;
  --accent-blue: #3b82f6;
  --accent-cyan: #06b6d4;
  --accent-green: #10b981;
  --accent-red: #ef4444;
  --accent-orange: #f59e0b;
  --accent-purple: #8b5cf6;
  --accent-pink: #ec4899;
  --shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.3), 0 2px 4px -2px rgba(0, 0, 0, 0.3);
  --shadow-lg: 0 10px 15px -3px rgba(0, 0, 0, 0.4), 0 4px 6px -4px rgba(0, 0, 0, 0.4);
  --radius: 12px;
  --radius-sm: 8px;
  --transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}

* { margin: 0; padding: 0; box-sizing: border-box; }

body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
  background: var(--bg-primary);
  color: var(--text-primary);
  line-height: 1.6;
  min-height: 100vh;
}

/* Scrollbar */
::-webkit-scrollbar { width: 8px; }
::-webkit-scrollbar-track { background: var(--bg-primary); }
::-webkit-scrollbar-thumb { background: var(--border-color); border-radius: 4px; }
::-webkit-scrollbar-thumb:hover { background: var(--text-muted); }

/* Layout */
.container { max-width: 1400px; margin: 0 auto; padding: 0 24px; }

/* Header */
.report-header {
  background: linear-gradient(135deg, var(--bg-secondary) 0%, var(--bg-primary) 100%);
  border-bottom: 1px solid var(--border-color);
  padding: 40px 0 32px;
  position: relative;
  overflow: hidden;
}
.report-header::before {
  content: '';
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  height: 4px;
  background: linear-gradient(90deg, var(--accent-blue), var(--accent-green), var(--accent-orange), var(--accent-purple));
  pointer-events: none;
}
.header-content { position: relative; z-index: 1; }
.stock-badge {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  background: rgba(59,130,246,0.15);
  border: 1px solid rgba(59,130,246,0.3);
  color: var(--accent-blue);
  padding: 6px 16px;
  border-radius: 20px;
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 16px;
}
.report-title {
  font-size: 36px;
  font-weight: 800;
  background: linear-gradient(135deg, var(--text-primary) 0%, var(--accent-cyan) 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
  margin-bottom: 8px;
}
.report-subtitle {
  color: var(--text-secondary);
  font-size: 15px;
  margin-bottom: 24px;
}
.report-meta {
  display: flex;
  gap: 24px;
  flex-wrap: wrap;
}
.meta-item {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--text-muted);
  font-size: 13px;
}
.meta-item svg { width: 16px; height: 16px; opacity: 0.7; }

/* KPI Cards */
.kpi-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 16px;
  margin: 24px 0;
}
.kpi-card {
  background: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: var(--radius);
  padding: 20px;
  transition: var(--transition);
  position: relative;
  overflow: hidden;
}
.kpi-card::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 3px;
  background: linear-gradient(90deg, var(--accent-blue), var(--accent-cyan));
  opacity: 0;
  transition: var(--transition);
}
.kpi-card:hover { transform: translateY(-2px); box-shadow: var(--shadow-lg); border-color: var(--accent-blue); }
.kpi-card:hover::before { opacity: 1; }
.kpi-card.positive::before { background: linear-gradient(90deg, var(--accent-green), #34d399); }
.kpi-card.negative::before { background: linear-gradient(90deg, var(--accent-red), #f87171); }
.kpi-card.warning::before { background: linear-gradient(90deg, var(--accent-orange), #fbbf24); }
.kpi-label {
  color: var(--text-muted);
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 8px;
}
.kpi-value {
  font-size: 28px;
  font-weight: 700;
  color: var(--text-primary);
  margin-bottom: 4px;
}
.kpi-change {
  font-size: 13px;
  font-weight: 600;
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.kpi-change.up { color: var(--accent-green); }
.kpi-change.down { color: var(--accent-red); }
.kpi-change.neutral { color: var(--text-muted); }

/* Section styling */
.section {
  background: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: var(--radius);
  margin: 24px 0;
  overflow: hidden;
  scroll-margin-top: 24px;
}
.section-header {
  padding: 20px 24px;
  border-bottom: 1px solid var(--border-color);
  display: flex;
  align-items: center;
  gap: 12px;
}
.section-number {
  width: 36px;
  height: 36px;
  border-radius: 10px;
  background: linear-gradient(135deg, var(--accent-blue), var(--accent-cyan));
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  font-weight: 700;
  color: white;
  flex-shrink: 0;
}
.section-title {
  font-size: 18px;
  font-weight: 700;
  color: var(--text-primary);
  flex: 1;
  margin: 0;
  line-height: 1.35;
}
.section-header h2 {
  flex: 1;
  margin: 0;
  line-height: 1.35;
}
.section-content {
  padding: 24px;
  display: block;
}

/* Subsection */
.subsection {
  margin-bottom: 24px;
}
.subsection:last-child { margin-bottom: 0; }
.subsection-title {
  font-size: 14px;
  font-weight: 700;
  color: var(--accent-cyan);
  text-transform: uppercase;
  letter-spacing: 0.8px;
  margin-bottom: 12px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.subsection-title::before {
  content: '';
  width: 4px;
  height: 16px;
  background: linear-gradient(180deg, var(--accent-cyan), var(--accent-blue));
  border-radius: 2px;
}

/* Lists */
.content-list {
  list-style: none;
  padding: 0;
}
.content-list li {
  padding: 10px 0;
  padding-left: 24px;
  position: relative;
  color: var(--text-secondary);
  font-size: 14px;
  line-height: 1.7;
  border-bottom: 1px solid rgba(51,65,85,0.3);
}
.content-list li:last-child { border-bottom: none; }
.content-list li::before {
  content: '';
  position: absolute;
  left: 0;
  top: 16px;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--accent-blue);
  opacity: 0.6;
}
.content-list li.fact::before { background: var(--accent-blue); }
.content-list li.calc::before { background: var(--accent-cyan); }
.content-list li.judge::before { background: var(--accent-purple); }
.content-list li.risk::before { background: var(--accent-red); }
.content-list li.evidence::before { background: var(--accent-green); opacity: 0.4; }

/* Evidence tags */
.evidence-tag {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  background: rgba(16,185,129,0.1);
  border: 1px solid rgba(16,185,129,0.2);
  color: var(--accent-green);
  padding: 3px 10px;
  border-radius: 6px;
  font-size: 11px;
  font-family: "SF Mono", "Fira Code", monospace;
  margin: 2px;
}
.evidence-tag a {
  color: inherit;
  text-decoration: none;
  border-left: 1px solid rgba(16,185,129,0.35);
  margin-left: 6px;
  padding-left: 6px;
}
.evidence-tag a:hover { text-decoration: underline; }
.evidence-tag.missing {
  background: rgba(239,68,68,0.1);
  border-color: rgba(239,68,68,0.2);
  color: var(--accent-red);
}

/* Charts */
.chart-container {
  background: var(--bg-primary);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  padding: 16px;
  margin: 16px 0;
  position: relative;
}
.chart-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 12px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.chart-title::before {
  content: '';
  width: 3px;
  height: 16px;
  background: linear-gradient(180deg, var(--accent-blue), var(--accent-cyan));
  border-radius: 2px;
}
.chart-area {
  width: 100%;
  height: 320px;
  display: none;
}
.chart-area.small { height: 240px; }
.chart-area.large { height: 400px; }
.chart-area.hero { height: 480px; }
.charts-enhanced .chart-area { display: block; }
.charts-enhanced .chart-fallback { display: none; }
.charts-enhanced .chart-container.static-fallback .chart-area { display: none; }
.chart-fallback {
  min-height: 260px;
  display: flex;
  align-items: stretch;
}
.charts-enhanced .chart-container.static-fallback .chart-fallback { display: flex; }
.chart-fallback svg {
  width: 100%;
  height: auto;
  display: block;
}
.chart-fallback svg text,
.chart-fallback svg rect,
.chart-fallback svg path,
.chart-fallback svg circle,
.chart-fallback svg polygon,
.chart-fallback svg polyline {
  transition: opacity 160ms ease, stroke-width 160ms ease, filter 160ms ease;
}
.svg-axis { stroke: #475569; stroke-width: 1; }
.svg-grid { stroke: rgba(71,85,105,0.35); stroke-width: 1; }
.svg-label { fill: var(--text-secondary); font-size: 12px; font-weight: 600; }
.svg-value { fill: var(--text-primary); font-size: 12px; font-weight: 700; font-variant-numeric: tabular-nums; }
.svg-muted { fill: var(--text-muted); font-size: 11px; }
.svg-line-blue { fill: none; stroke: var(--accent-blue); stroke-width: 3; stroke-linecap: round; stroke-linejoin: round; }
.svg-line-green { fill: none; stroke: var(--accent-green); stroke-width: 3; stroke-linecap: round; stroke-linejoin: round; }
.svg-dot { fill: var(--bg-primary); stroke-width: 2; }
.svg-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin: 8px 8px 0 0;
  color: var(--text-secondary);
  font-size: 12px;
}
.svg-chip i {
  width: 10px;
  height: 10px;
  border-radius: 3px;
  display: inline-block;
}
.chart-fallback-empty {
  width: 100%;
  min-height: 180px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-secondary);
  font-size: 14px;
  text-align: center;
}
.chart-interactive {
  cursor: pointer;
  outline: none;
}
.chart-interactive:focus-visible .chart-hit {
  stroke: #f8fafc;
  stroke-width: 2;
}
.chart-container.chart-has-active .chart-interactive {
  opacity: 0.24;
}
.chart-container.chart-has-active .chart-interactive.is-active {
  opacity: 1;
}
.chart-container .chart-interactive.is-active .chart-mark {
  filter: drop-shadow(0 6px 12px rgba(15,23,42,0.22));
}
.report-chart-tooltip {
  position: fixed;
  z-index: 500;
  max-width: 280px;
  pointer-events: none;
  background: rgba(17,24,39,0.96);
  color: #ffffff;
  border: 1px solid rgba(255,255,255,0.14);
  border-radius: 8px;
  padding: 10px 12px;
  box-shadow: 0 16px 34px -24px rgba(15,23,42,0.9);
  opacity: 0;
  transform: translate(-50%, -120%);
  transition: opacity 120ms ease;
  font-size: 12px;
  line-height: 1.5;
}
.report-chart-tooltip.visible {
  opacity: 1;
}
.report-chart-tooltip strong {
  display: block;
  font-size: 13px;
  margin-bottom: 4px;
}
.report-chart-tooltip span {
  display: block;
  color: #d1d5db;
}
.income-bridge-panel {
  background: #ffffff;
  border-color: #e5e7eb;
  box-shadow: 0 18px 42px -32px rgba(15,23,42,0.38);
  color: #111827;
}
.income-bridge-summary {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px;
  margin: 0 0 12px;
}
.income-bridge-metric {
  background: #f8fafc;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  padding: 12px;
}
.income-bridge-metric-label {
  color: #64748b;
  font-size: 12px;
  margin-bottom: 4px;
}
.income-bridge-metric-value {
  color: #111827;
  font-size: 20px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}
.income-bridge-panel .chart-title {
  color: #111827;
  font-size: 28px;
  font-weight: 800;
  margin-bottom: 4px;
}
.income-bridge-panel .chart-title::before {
  display: none;
}
.income-bridge-head {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: flex-start;
  border-bottom: 1px solid #eef2f7;
  padding-bottom: 12px;
  margin-bottom: 18px;
}
.income-bridge-subtitle {
  color: #7c8794;
  font-size: 14px;
  font-variant-numeric: tabular-nums;
}
.income-bridge-unit {
  color: #7c8794;
  font-size: 14px;
  white-space: nowrap;
  padding-top: 10px;
}
.income-bridge-legend {
  display: flex;
  gap: 18px;
  align-items: center;
  color: #111827;
  font-size: 15px;
  margin: 0 0 8px;
}
.income-bridge-legend span {
  display: inline-flex;
  align-items: center;
  gap: 7px;
}
.income-bridge-legend i {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  display: inline-block;
}
.income-bridge-legend .income { background: #35a9f4; }
.income-bridge-legend .expense { background: #f2c400; }
.income-bridge-legend .profit { background: #ff3548; }
.income-bridge-panel .chart-fallback {
  display: block;
  min-height: 520px;
}
.income-bridge-panel .chart-fallback svg {
  display: block;
}
.income-bridge-panel .chart-fallback svg text,
.income-bridge-panel .chart-fallback svg rect,
.income-bridge-panel .chart-fallback svg path {
  transition: opacity 160ms ease, stroke-width 160ms ease, filter 160ms ease;
}
.ib-interactive {
  cursor: pointer;
  outline: none;
}
.ib-interactive:focus-visible .ib-hit {
  stroke: #111827;
  stroke-width: 2;
}
.income-bridge-panel.ib-has-active .ib-interactive {
  opacity: 0.24;
}
.income-bridge-panel.ib-has-active .ib-interactive.is-active,
.income-bridge-panel.ib-has-active .ib-interactive.is-neighbor {
  opacity: 1;
}
.income-bridge-panel .ib-interactive.is-active .ib-flow,
.income-bridge-panel .ib-interactive.is-neighbor .ib-flow {
  filter: drop-shadow(0 6px 12px rgba(15,23,42,0.18));
}
.income-bridge-tooltip {
  position: fixed;
  z-index: 500;
  max-width: 280px;
  pointer-events: none;
  background: rgba(17,24,39,0.96);
  color: #ffffff;
  border: 1px solid rgba(255,255,255,0.14);
  border-radius: 8px;
  padding: 10px 12px;
  box-shadow: 0 16px 34px -24px rgba(15,23,42,0.9);
  opacity: 0;
  transform: translate(-50%, -120%);
  transition: opacity 120ms ease;
  font-size: 12px;
  line-height: 1.5;
}
.income-bridge-tooltip.visible {
  opacity: 1;
}
.income-bridge-tooltip strong {
  display: block;
  font-size: 13px;
  margin-bottom: 4px;
}
.income-bridge-tooltip span {
  display: block;
  color: #d1d5db;
}
.income-bridge-footnotes {
  margin-top: 10px;
  color: #64748b;
  font-size: 12px;
  line-height: 1.65;
}
.income-bridge-footnotes span {
  display: inline-block;
  margin-right: 14px;
}

/* Grid layouts for charts */
.chart-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(400px, 1fr));
  gap: 16px;
}
.chart-grid-3 {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  gap: 16px;
}

/* Risk indicators */
.risk-card {
  background: rgba(239,68,68,0.05);
  border: 1px solid rgba(239,68,68,0.15);
  border-radius: var(--radius-sm);
  padding: 16px;
  margin: 8px 0;
}
.risk-card.warning {
  background: rgba(245,158,11,0.05);
  border-color: rgba(245,158,11,0.15);
}
.risk-card.info {
  background: rgba(59,130,246,0.05);
  border-color: rgba(59,130,246,0.15);
}
.risk-card-title {
  font-size: 13px;
  font-weight: 700;
  color: var(--accent-red);
  margin-bottom: 8px;
  display: flex;
  align-items: center;
  gap: 6px;
}
.risk-card.warning .risk-card-title { color: var(--accent-orange); }
.risk-card.info .risk-card-title { color: var(--accent-blue); }
.risk-card-content {
  color: var(--text-secondary);
  font-size: 13px;
  line-height: 1.6;
}

/* Status badges */
.status-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 12px;
  border-radius: 20px;
  font-size: 12px;
  font-weight: 600;
}
.status-badge.success {
  background: rgba(16,185,129,0.15);
  color: var(--accent-green);
}
.status-badge.danger {
  background: rgba(239,68,68,0.15);
  color: var(--accent-red);
}
.status-badge.warning {
  background: rgba(245,158,11,0.15);
  color: var(--accent-orange);
}
.status-badge.info {
  background: rgba(59,130,246,0.15);
  color: var(--accent-blue);
}
.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  animation: pulse 2s infinite;
}
@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}

/* Table styling */
.data-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
  margin: 12px 0;
}
.data-table th {
  background: var(--bg-primary);
  color: var(--text-muted);
  font-weight: 600;
  text-align: left;
  padding: 10px 12px;
  border-bottom: 2px solid var(--border-color);
  text-transform: uppercase;
  font-size: 11px;
  letter-spacing: 0.5px;
}
.data-table td {
  padding: 10px 12px;
  border-bottom: 1px solid rgba(51,65,85,0.3);
  color: var(--text-secondary);
}
.data-table tr:hover td {
  background: rgba(59,130,246,0.05);
  color: var(--text-primary);
}
.data-table .num { text-align: right; font-family: "SF Mono", monospace; }
.data-table .positive { color: var(--accent-green); }
.data-table .negative { color: var(--accent-red); }

.main-content {
  width: 100%;
}

/* Progress bar */
.progress-bar {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  height: 3px;
  background: var(--bg-secondary);
  z-index: 200;
}
.progress-bar-fill {
  height: 100%;
  background: linear-gradient(90deg, var(--accent-blue), var(--accent-cyan));
  transition: width 0.3s ease;
  width: 0%;
}

/* Footer */
.report-footer {
  background: var(--bg-secondary);
  border-top: 1px solid var(--border-color);
  padding: 24px 0;
  margin-top: 48px;
  text-align: center;
  color: var(--text-muted);
  font-size: 12px;
}

/* Print styles */
@media print {
  body { background: white; color: #1f2937; }
  .progress-bar { display: none !important; }
  .main-content { margin-left: 0 !important; }
  .section { border: 1px solid #e5e7eb; break-inside: avoid; }
  .section-content { display: block !important; }
  .chart-container { break-inside: avoid; }
  .kpi-card { border: 1px solid #e5e7eb; }
}

/* Responsive */
@media (max-width: 768px) {
  .container { padding: 0 16px; }
  .report-title { font-size: 24px; }
  .kpi-grid { grid-template-columns: 1fr; }
  .chart-grid, .chart-grid-3 { grid-template-columns: 1fr; }
  .chart-area { height: 250px; }
  .chart-area.hero { height: 360px; }
}

/* Tooltip */
.tooltip {
  position: relative;
}
.tooltip::after {
  content: attr(data-tooltip);
  position: absolute;
  bottom: 100%;
  left: 50%;
  transform: translateX(-50%);
  padding: 6px 12px;
  background: var(--bg-secondary);
  border: 1px solid var(--border-color);
  border-radius: 6px;
  font-size: 12px;
  color: var(--text-primary);
  white-space: nowrap;
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.2s;
}
.tooltip:hover::after { opacity: 1; }
"""


ECHARTS_SCRIPTS = """
<script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
<script>
// Chart theme colors
const chartColors = ['#3b82f6', '#06b6d4', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#ec4899', '#14b8a6'];
const chartBg = '#0f172a';
const chartText = '#94a3b8';
const chartGrid = '#334155';

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function placeReportTooltip(tooltip, event, fallbackEl) {
  const rect = fallbackEl?.getBoundingClientRect?.() || { left: 0, top: 80, width: window.innerWidth };
  const x = event?.clientX ?? rect.left + rect.width / 2;
  const y = event?.clientY ?? rect.top + 80;
  tooltip.style.left = `${Math.min(window.innerWidth - 18, Math.max(18, x))}px`;
  tooltip.style.top = `${Math.max(24, y - 12)}px`;
}

function chartTooltipHtml(title, value, detail) {
  return `<strong>${escapeHtml(title)}</strong><span>${escapeHtml(value || '')}</span>${detail ? `<span>${escapeHtml(detail)}</span>` : ''}<span>点击可锁定，再次点击取消</span>`;
}

function reportTooltipBase(trigger = 'axis') {
  return {
    trigger,
    confine: true,
    appendToBody: true,
    enterable: false,
    backgroundColor: 'rgba(17,24,39,0.96)',
    borderColor: 'rgba(255,255,255,0.14)',
    borderWidth: 1,
    padding: [10, 12],
    textStyle: { color: '#f1f5f9', fontSize: 12, lineHeight: 18 },
    extraCssText: 'border-radius:8px;box-shadow:0 16px 34px -24px rgba(15,23,42,0.9);'
  };
}

function reportValueLabel(position = 'top') {
  return {
    show: true,
    position,
    color: '#0f172a',
    fontSize: 11,
    fontWeight: 700,
    backgroundColor: 'rgba(241,245,249,0.92)',
    borderColor: 'rgba(148,163,184,0.4)',
    borderWidth: 1,
    borderRadius: 4,
    padding: [3, 6],
    formatter: function(p) {
      const value = Array.isArray(p.value) ? p.value[p.value.length - 1] : p.value;
      return Number(value) > 0 ? Number(value).toFixed(2) : String(value);
    }
  };
}

function initReportChartInteractions() {
  document.querySelectorAll('.chart-container:not(.income-bridge-panel)').forEach((panel) => {
    const items = Array.from(panel.querySelectorAll('.chart-interactive'));
    if (!items.length) return;
    let tooltip = panel.querySelector('.report-chart-tooltip');
    if (!tooltip) {
      tooltip = document.createElement('div');
      tooltip.className = 'report-chart-tooltip';
      tooltip.setAttribute('role', 'status');
      tooltip.setAttribute('aria-live', 'polite');
      panel.appendChild(tooltip);
    }
    let locked = null;
    const clear = () => {
      if (locked) return;
      panel.classList.remove('chart-has-active');
      items.forEach((el) => el.classList.remove('is-active'));
      tooltip.classList.remove('visible');
    };
    const activate = (item, event, force = false) => {
      if (!force && locked && locked !== item) return;
      panel.classList.add('chart-has-active');
      items.forEach((el) => el.classList.toggle('is-active', el === item));
      tooltip.innerHTML = chartTooltipHtml(item.dataset.title || '图表项目', item.dataset.value || '', item.dataset.detail || '');
      placeReportTooltip(tooltip, event, panel);
      tooltip.classList.add('visible');
    };
    items.forEach((item) => {
      item.addEventListener('mouseenter', (event) => activate(item, event));
      item.addEventListener('mousemove', (event) => placeReportTooltip(tooltip, event, panel));
      item.addEventListener('mouseleave', clear);
      item.addEventListener('focus', (event) => activate(item, event));
      item.addEventListener('blur', clear);
      item.addEventListener('click', (event) => {
        event.preventDefault();
        locked = locked === item ? null : item;
        if (locked) activate(item, event, true);
        else clear();
      });
      item.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          item.dispatchEvent(new MouseEvent('click', { bubbles: true, clientX: window.innerWidth / 2, clientY: window.innerHeight / 2 }));
        }
        if (event.key === 'Escape') {
          locked = null;
          clear();
        }
      });
    });
    panel.addEventListener('mouseleave', clear);
  });
}

function initIncomeBridgeInteractions() {
  const panels = document.querySelectorAll('.income-bridge-panel');
  panels.forEach((panel) => {
    const tooltip = panel.querySelector('.income-bridge-tooltip');
    const items = Array.from(panel.querySelectorAll('.ib-interactive'));
    if (!tooltip || !items.length) return;
    let locked = null;

    const relatedIds = (item) => new Set(String(item.dataset.related || '').split(',').filter(Boolean));
    const clear = () => {
      if (locked) return;
      panel.classList.remove('ib-has-active');
      items.forEach((el) => el.classList.remove('is-active', 'is-neighbor'));
      tooltip.classList.remove('visible');
    };
    const placeTooltip = (event) => {
      const rect = panel.getBoundingClientRect();
      const x = event?.clientX ?? rect.left + rect.width / 2;
      const y = event?.clientY ?? rect.top + 80;
      tooltip.style.left = `${Math.min(window.innerWidth - 18, Math.max(18, x))}px`;
      tooltip.style.top = `${Math.max(24, y - 12)}px`;
    };
    const activate = (item, event, force = false) => {
      if (!force && locked && locked !== item) return;
      const id = item.dataset.ibId;
      const related = relatedIds(item);
      panel.classList.add('ib-has-active');
      items.forEach((el) => {
        const isActive = el === item;
        const isNeighbor = related.has(el.dataset.ibId) || relatedIds(el).has(id);
        el.classList.toggle('is-active', isActive);
        el.classList.toggle('is-neighbor', !isActive && isNeighbor);
      });
      const title = item.dataset.title || '收支拆解';
      const value = item.dataset.value || '';
      const detail = item.dataset.detail || '';
      tooltip.innerHTML = `<strong>${escapeHtml(title)}</strong><span>${escapeHtml(value)}</span>${detail ? `<span>${escapeHtml(detail)}</span>` : ''}<span>点击可锁定，再次点击取消</span>`;
      placeTooltip(event);
      tooltip.classList.add('visible');
    };
    items.forEach((item) => {
      item.addEventListener('mouseenter', (event) => activate(item, event));
      item.addEventListener('mousemove', placeTooltip);
      item.addEventListener('mouseleave', clear);
      item.addEventListener('focus', (event) => activate(item, event));
      item.addEventListener('blur', clear);
      item.addEventListener('click', (event) => {
        event.preventDefault();
        locked = locked === item ? null : item;
        if (locked) activate(item, event, true);
        else clear();
      });
      item.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          item.dispatchEvent(new MouseEvent('click', { bubbles: true, clientX: window.innerWidth / 2, clientY: window.innerHeight / 2 }));
        }
        if (event.key === 'Escape') {
          locked = null;
          clear();
        }
      });
    });
    panel.addEventListener('mouseleave', clear);
  });
}

// Common chart option base
function baseOption() {
  return {
    backgroundColor: 'transparent',
    textStyle: { fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif' },
    tooltip: {
      ...reportTooltipBase('axis'),
      trigger: 'axis',
      axisPointer: { type: 'shadow', shadowStyle: { color: 'rgba(59,130,246,0.1)' } }
    },
    legend: {
      textStyle: { color: chartText, fontSize: 12 },
      itemWidth: 14,
      itemHeight: 10,
      itemGap: 16,
      top: 0
    },
    grid: { left: '3%', right: '4%', bottom: '3%', top: 40, containLabel: true },
  };
}

// Initialize all charts when DOM is ready
document.addEventListener('DOMContentLoaded', function() {
  if (typeof echarts !== 'undefined') {
    document.documentElement.classList.add('charts-enhanced');
    initRevenueProfitChart();
    initCashFlowChart();
    initAssetStructureChart();
    initDebtStructureChart();
    initDupontChart();
    initSolvencyGauges();
    initPeerComparisonChart();
    initIncomeBridgeChart();
    initProfitabilityWaterfall();
  }
  initIncomeBridgeInteractions();
  initReportChartInteractions();
  
  // Progress bar
  window.addEventListener('scroll', function() {
    const scrollTop = window.scrollY;
    const docHeight = document.documentElement.scrollHeight - window.innerHeight;
    const progress = (scrollTop / docHeight) * 100;
    const progressFill = document.querySelector('.progress-bar-fill');
    if (progressFill) progressFill.style.width = progress + '%';
    
  });
});

function fmtYi(value) {
  if (value === null || value === undefined || isNaN(Number(value))) return '未返回';
  const num = Number(value);
  return (Math.abs(num) >= 100 ? num.toFixed(1) : num.toFixed(2)) + ' 亿元';
}

function initIncomeBridgeChart() {
  // The hero income bridge is rendered as deterministic inline SVG so its
  // layout stays aligned with the supplied mobile-finance Sankey reference.
}

function bindEChartInteractions(chart, el, defaultDataIndex = 0) {
  if (!chart || !el) return;
  el.setAttribute('tabindex', '0');
  let locked = null;
  let lastParams = null;

  const downplayAll = () => chart.dispatchAction({ type: 'downplay' });
  const show = (params) => {
    if (!params) return;
    const payload = {
      type: 'showTip',
      seriesIndex: params.seriesIndex ?? 0,
      dataIndex: params.dataIndex ?? defaultDataIndex
    };
    if (params.componentType === 'xAxis' || params.axisIndex !== undefined) {
      payload.dataIndex = params.dataIndex ?? defaultDataIndex;
    }
    chart.dispatchAction(payload);
    chart.dispatchAction({
      type: 'highlight',
      seriesIndex: params.seriesIndex ?? 0,
      dataIndex: params.dataIndex ?? defaultDataIndex
    });
  };
  const clear = () => {
    if (locked) return;
    downplayAll();
    chart.dispatchAction({ type: 'hideTip' });
  };

  chart.on('mouseover', (params) => { lastParams = params; });
  chart.on('globalout', clear);
  chart.on('click', (params) => {
    const key = `${params.seriesIndex ?? 0}:${params.dataIndex ?? defaultDataIndex}`;
    if (locked === key) {
      locked = null;
      downplayAll();
      chart.dispatchAction({ type: 'hideTip' });
      return;
    }
    locked = key;
    downplayAll();
    show(params);
  });
  el.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      const params = lastParams || { seriesIndex: 0, dataIndex: defaultDataIndex };
      const key = `${params.seriesIndex ?? 0}:${params.dataIndex ?? defaultDataIndex}`;
      if (locked === key) {
        locked = null;
        downplayAll();
        chart.dispatchAction({ type: 'hideTip' });
      } else {
        locked = key;
        downplayAll();
        show(params);
      }
    }
    if (event.key === 'Escape') {
      locked = null;
      downplayAll();
      chart.dispatchAction({ type: 'hideTip' });
    }
  });
}

function initRevenueProfitChart() {
  const el = document.getElementById('revenue-profit-chart');
  if (!el || !window.revenueProfitData) return;
  const data = window.revenueProfitData;
  const chart = echarts.init(el);
  const option = {
    ...baseOption(),
    tooltip: {
      ...reportTooltipBase('axis'),
      trigger: 'axis',
      axisPointer: { type: 'cross' },
      formatter: function(params) {
        const year = params[0]?.axisValue || '';
        const rows = params.map(p => `${p.marker}${p.seriesName}: ${fmtYi(p.value)}`).join('<br/>');
        return '<strong>' + escapeHtml(year) + '</strong><br/>' + rows + '<br/><span style="color:#d1d5db">点击可锁定，再次点击取消</span>';
      }
    },
    legend: { data: ['营业收入', '归母净利润'], textStyle: { color: chartText } },
    xAxis: {
      type: 'category',
      data: data.years,
      axisLine: { lineStyle: { color: chartGrid } },
      axisLabel: { color: chartText }
    },
    yAxis: [
      {
        type: 'value',
        name: '亿元',
        nameTextStyle: { color: chartText },
        axisLine: { lineStyle: { color: chartGrid } },
        axisLabel: { color: chartText },
        splitLine: { lineStyle: { color: 'rgba(51,65,85,0.3)' } }
      },
      {
        type: 'value',
        name: '亿元',
        nameTextStyle: { color: chartText },
        axisLine: { lineStyle: { color: chartGrid } },
        axisLabel: { color: chartText },
        splitLine: { show: false }
      }
    ],
    series: [
      {
        name: '营业收入',
        type: 'bar',
        data: data.revenue,
        itemStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: '#3b82f6' },
            { offset: 1, color: '#1d4ed8' }
          ]),
          borderRadius: [6, 6, 0, 0]
        },
        barWidth: '40%'
      },
      {
        name: '归母净利润',
        type: 'line',
        yAxisIndex: 1,
        data: data.profit,
        smooth: true,
        symbol: 'circle',
        symbolSize: 8,
        lineStyle: { color: '#10b981', width: 3 },
        itemStyle: { color: '#10b981', borderWidth: 2, borderColor: '#0f172a' },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: 'rgba(16,185,129,0.3)' },
            { offset: 1, color: 'rgba(16,185,129,0.02)' }
          ])
        },
        label: {
          ...reportValueLabel('top'),
          formatter: function(p) { return Number(p.value).toFixed(2); }
        }
      }
    ]
  };
  chart.setOption(option);
  bindEChartInteractions(chart, el);
  window.addEventListener('resize', () => chart.resize());
}

function initCashFlowChart() {
  const el = document.getElementById('cashflow-chart');
  if (!el || !window.cashFlowData) return;
  const data = window.cashFlowData;
  const chart = echarts.init(el);
  
  const items = [
    { name: '经营现金流', value: data.operating, color: '#10b981' },
    { name: '投资现金流', value: data.investing, color: '#ef4444' },
    { name: '筹资现金流', value: data.financing, color: '#f59e0b' },
    { name: '资本开支', value: -data.capex, color: '#8b5cf6' },
  ];
  if (data.free_cash_flow !== null) {
    items.push({ name: '自由现金流', value: data.free_cash_flow, color: '#06b6d4' });
  }
  
  const option = {
    ...baseOption(),
    tooltip: {
      ...reportTooltipBase('item'),
      trigger: 'item',
      formatter: function(p) {
        return chartTooltipHtml(p.name, fmtYi(p.value), p.value >= 0 ? '现金流入或余额贡献' : '现金流出或资本投入');
      }
    },
    xAxis: {
      type: 'category',
      data: items.map(i => i.name),
      axisLine: { lineStyle: { color: chartGrid } },
      axisLabel: { color: chartText, fontSize: 11, rotate: 15 }
    },
    yAxis: {
      type: 'value',
      name: '亿元',
      nameTextStyle: { color: chartText },
      axisLine: { lineStyle: { color: chartGrid } },
      axisLabel: { color: chartText },
      splitLine: { lineStyle: { color: 'rgba(51,65,85,0.3)' } }
    },
    series: [{
      type: 'bar',
      data: items.map(i => ({
        value: i.value,
        itemStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: i.color },
            { offset: 1, color: i.color + '66' }
          ]),
          borderRadius: i.value >= 0 ? [6, 6, 0, 0] : [0, 0, 6, 6]
        }
      })),
      barWidth: '50%',
      label: {
        ...reportValueLabel('top'),
        formatter: function(p) { return Number(p.value).toFixed(2); }
      }
    }]
  };
  chart.setOption(option);
  bindEChartInteractions(chart, el);
  window.addEventListener('resize', () => chart.resize());
}

function initAssetStructureChart() {
  const el = document.getElementById('asset-structure-chart');
  if (!el || !window.assetStructureData) return;
  const data = window.assetStructureData;
  const chart = echarts.init(el);
  const option = {
    ...baseOption(),
    tooltip: {
      ...reportTooltipBase('item'),
      trigger: 'item',
      formatter: function(p) { return chartTooltipHtml(p.name, fmtYi(p.value), '占比 ' + p.percent + '%'); }
    },
    legend: {
      orient: 'vertical',
      right: 10,
      top: 'center',
      textStyle: { color: chartText, fontSize: 11 }
    },
    series: [{
      type: 'pie',
      radius: ['45%', '75%'],
      center: ['40%', '50%'],
      avoidLabelOverlap: true,
      itemStyle: {
        borderRadius: 6,
        borderColor: chartBg,
        borderWidth: 2
      },
      label: {
        show: true,
        color: '#0f172a',
        fontSize: 11,
        backgroundColor: 'rgba(241,245,249,0.92)',
        borderColor: 'rgba(148,163,184,0.4)',
        borderWidth: 1,
        borderRadius: 4,
        padding: [3, 6],
        formatter: function(p) { return p.name + '\\n' + p.percent + '%'; }
      },
      emphasis: {
        label: { show: true, fontSize: 13, fontWeight: 'bold', color: '#0f172a', backgroundColor: 'rgba(241,245,249,0.95)' },
        itemStyle: { shadowBlur: 10, shadowColor: 'rgba(0,0,0,0.5)' }
      },
      data: data.categories.map((item, i) => ({
        name: item.name,
        value: item.value,
        itemStyle: { color: chartColors[i % chartColors.length] }
      }))
    }]
  };
  chart.setOption(option);
  bindEChartInteractions(chart, el);
  window.addEventListener('resize', () => chart.resize());
}

function initDebtStructureChart() {
  const el = document.getElementById('debt-structure-chart');
  if (!el || !window.debtStructureData) return;
  const data = window.debtStructureData;
  const chart = echarts.init(el);
  const option = {
    ...baseOption(),
    tooltip: {
      ...reportTooltipBase('item'),
      trigger: 'item',
      formatter: function(p) { return chartTooltipHtml(p.name, fmtYi(p.value), '占比 ' + p.percent + '%'); }
    },
    legend: {
      orient: 'vertical',
      right: 10,
      top: 'center',
      textStyle: { color: chartText, fontSize: 11 }
    },
    series: [{
      type: 'pie',
      radius: ['40%', '70%'],
      center: ['38%', '50%'],
      roseType: 'radius',
      itemStyle: {
        borderRadius: 6,
        borderColor: chartBg,
        borderWidth: 2
      },
      label: {
        show: true,
        color: '#0f172a',
        fontSize: 11,
        backgroundColor: 'rgba(241,245,249,0.92)',
        borderColor: 'rgba(148,163,184,0.4)',
        borderWidth: 1,
        borderRadius: 4,
        padding: [3, 6],
        formatter: function(p) { return p.name + '\\n' + p.percent + '%'; }
      },
      emphasis: {
        label: { show: true, fontSize: 13, fontWeight: 'bold', color: '#0f172a', backgroundColor: 'rgba(241,245,249,0.95)' }
      },
      data: data.categories.map((item, i) => ({
        name: item.name,
        value: item.value,
        itemStyle: { color: chartColors[(i + 3) % chartColors.length] }
      }))
    }]
  };
  chart.setOption(option);
  bindEChartInteractions(chart, el);
  window.addEventListener('resize', () => chart.resize());
}

function initDupontChart() {
  const el = document.getElementById('dupont-chart');
  if (!el || !window.dupontData) return;
  const data = window.dupontData;
  const chart = echarts.init(el);
  
  const indicators = [
    { name: '销售净利率', max: 50, value: data.net_margin || 0 },
    { name: '资产周转率', max: 2, value: (data.asset_turnover || 0) * 25 },
    { name: '权益乘数', max: 10, value: (data.equity_multiplier || 0) * 5 },
    { name: 'ROE', max: 50, value: data.roe || 0 },
  ];
  
  const option = {
    ...baseOption(),
    tooltip: {
      ...reportTooltipBase('item'),
      formatter: function(p) {
        const values = p.value || [];
        return chartTooltipHtml('杜邦分析', values.map((v, i) => `${indicators[i].name}: ${Number(v).toFixed(2)}`).join(' / '), '点击可锁定，再次点击取消');
      }
    },
    radar: {
      indicator: indicators.map(i => ({ name: i.name, max: i.max })),
      center: ['50%', '55%'],
      radius: '65%',
      axisName: { color: chartText, fontSize: 12 },
      splitArea: {
        areaStyle: {
          color: ['rgba(59,130,246,0.05)', 'rgba(59,130,246,0.02)']
        }
      },
      axisLine: { lineStyle: { color: chartGrid } },
      splitLine: { lineStyle: { color: chartGrid } }
    },
    series: [{
      type: 'radar',
      data: [{
        value: indicators.map(i => i.value),
        name: '杜邦分析',
        areaStyle: { color: 'rgba(59,130,246,0.2)' },
        lineStyle: { color: '#3b82f6', width: 2 },
        itemStyle: { color: '#3b82f6' },
        symbol: 'circle',
        symbolSize: 6
      }]
    }]
  };
  chart.setOption(option);
  bindEChartInteractions(chart, el);
  window.addEventListener('resize', () => chart.resize());
}

function initSolvencyGauges() {
  const gauges = window.solvencyData;
  if (!gauges) return;
  
  const gaugeConfigs = [
    { id: 'gauge-debt-ratio', name: '资产负债率', value: gauges.debt_ratio, max: 100, unit: '%', threshold: 70 },
    { id: 'gauge-current', name: '流动比率', value: gauges.current_ratio, max: 3, unit: 'x', threshold: 1.5 },
    { id: 'gauge-quick', name: '速动比率', value: gauges.quick_ratio, max: 3, unit: 'x', threshold: 1 },
    { id: 'gauge-cash', name: '现金比率', value: gauges.cash_ratio, max: 1, unit: 'x', threshold: 0.3 },
  ];
  
  gaugeConfigs.forEach(cfg => {
    const el = document.getElementById(cfg.id);
    if (!el || cfg.value === null) return;
    const chart = echarts.init(el);
    const color = cfg.value > cfg.threshold ? '#ef4444' : cfg.value > cfg.threshold * 0.7 ? '#f59e0b' : '#10b981';
    
    const option = {
      series: [{
        type: 'gauge',
        startAngle: 200,
        endAngle: -20,
        min: 0,
        max: cfg.max,
        splitNumber: 5,
        itemStyle: { color: color },
        progress: { show: true, width: 12, roundCap: true },
        pointer: { show: false },
        axisLine: { lineStyle: { width: 12, color: [[1, 'rgba(51,65,85,0.3)']] } },
        axisTick: { show: false },
        splitLine: { show: false },
        axisLabel: { show: false },
        title: {
          offsetCenter: [0, '30%'],
          fontSize: 12,
          color: chartText
        },
        detail: {
          fontSize: 22,
          fontWeight: 'bold',
          offsetCenter: [0, '-10%'],
          formatter: function(value) { return value + cfg.unit; },
          color: color
        },
        data: [{ value: cfg.value, name: cfg.name }]
      }]
    };
    chart.setOption(option);
    bindEChartInteractions(chart, el);
    window.addEventListener('resize', () => chart.resize());
  });
}

function initPeerComparisonChart() {
  const el = document.getElementById('peer-comparison-chart');
  if (!el || !window.peerComparisonData) return;
  const data = window.peerComparisonData;
  const chart = echarts.init(el);
  
  const option = {
    ...baseOption(),
    tooltip: {
      ...reportTooltipBase('item'),
      formatter: function(p) {
        const values = p.value || [];
        return chartTooltipHtml(p.name, values.map((v, i) => `${data.metrics[i]}: ${Number(v).toFixed(2)}`).join(' / '), `样本数 ${data.peer_count || 0}`);
      }
    },
    legend: {
      data: ['本公司', '行业中位数'],
      textStyle: { color: chartText }
    },
    radar: {
      indicator: data.metrics.map(m => ({ name: m, max: 100 })),
      center: ['50%', '55%'],
      radius: '65%',
      axisName: { color: chartText, fontSize: 11 },
      splitArea: {
        areaStyle: {
          color: ['rgba(59,130,246,0.03)', 'rgba(16,185,129,0.03)']
        }
      },
      axisLine: { lineStyle: { color: chartGrid } },
      splitLine: { lineStyle: { color: chartGrid } }
    },
    series: [{
      type: 'radar',
      data: [
        {
          value: data.company,
          name: '本公司',
          areaStyle: { color: 'rgba(59,130,246,0.2)' },
          lineStyle: { color: '#3b82f6', width: 2 },
          itemStyle: { color: '#3b82f6' }
        },
        {
          value: data.peer_median,
          name: '行业中位数',
          areaStyle: { color: 'rgba(245,158,11,0.15)' },
          lineStyle: { color: '#f59e0b', width: 2, type: 'dashed' },
          itemStyle: { color: '#f59e0b' }
        }
      ]
    }]
  };
  chart.setOption(option);
  bindEChartInteractions(chart, el);
  window.addEventListener('resize', () => chart.resize());
}

function initProfitabilityWaterfall() {
  const el = document.getElementById('profitability-waterfall');
  if (!el || !window.profitabilityData) return;
  const data = window.profitabilityData;
  const chart = echarts.init(el);
  const barRows = data.steps.map(s => {
    const base = Number(s.base || 0);
    const value = Number(s.value || 0);
    const end = Number((s.end !== undefined ? s.end : base + value) || 0);
    return {
      ...s,
      _plotBase: Math.min(base, end),
      _plotValue: Math.abs(end - base),
      _delta: value
    };
  });
  
  const option = {
    ...baseOption(),
    tooltip: {
      ...reportTooltipBase('axis'),
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: function(params) {
        const p = params.find(item => item.seriesName === '项目');
        if (!p) return '';
        const row = barRows[p.dataIndex];
        return chartTooltipHtml(row.name, fmtYi(row._delta), `base ${row.base.toFixed(2)} 亿 / end ${row.end.toFixed(2)} 亿`);
      }
    },
    xAxis: {
      type: 'category',
      data: barRows.map(s => s.name),
      axisLine: { lineStyle: { color: chartGrid } },
      axisLabel: { color: chartText, fontSize: 11, rotate: 20 }
    },
    yAxis: {
      type: 'value',
      name: '亿元',
      nameTextStyle: { color: chartText },
      axisLine: { lineStyle: { color: chartGrid } },
      axisLabel: { color: chartText },
      splitLine: { lineStyle: { color: 'rgba(51,65,85,0.3)' } }
    },
    series: [{
      type: 'bar',
      stack: 'Total',
      itemStyle: { borderColor: 'transparent', color: 'transparent' },
      emphasis: { itemStyle: { borderColor: 'transparent', color: 'transparent' } },
      data: barRows.map(s => s._plotBase)
    }, {
      name: '项目',
      type: 'bar',
      stack: 'Total',
      data: barRows.map((s, i) => ({
        value: s._plotValue,
        itemStyle: {
          color: s._delta >= 0 
            ? new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                { offset: 0, color: '#10b981' },
                { offset: 1, color: '#059669' }
              ])
            : new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                { offset: 0, color: '#ef4444' },
                { offset: 1, color: '#dc2626' }
              ]),
          borderRadius: [4, 4, 4, 4]
        }
      })),
      label: {
        ...reportValueLabel('top'),
        formatter: function(p) {
          const value = barRows[p.dataIndex]._delta;
          return value > 0 ? '+' + value.toFixed(2) : value.toFixed(2);
        }
      }
    }]
  };
  chart.setOption(option);
  bindEChartInteractions(chart, el);
  window.addEventListener('resize', () => chart.resize());
}
</script>
"""


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
    
    cards = []
    
    # Revenue card
    revenue_class = "positive" if revenue > 0 else "neutral"
    cards.append(f"""
    <div class="kpi-card {revenue_class}">
      <div class="kpi-label">营业收入</div>
      <div class="kpi-value">{fmt_num(revenue, "亿元")}</div>
      <div class="kpi-change up">↗ 同比 {fmt_num(yoy_change(snapshot, 'operating_revenue'), "%")}</div>
    </div>""")
    
    # Profit card
    profit_class = "positive" if profit and profit > 0 else "negative" if profit and profit < 0 else "neutral"
    cards.append(f"""
    <div class="kpi-card {profit_class}">
      <div class="kpi-label">归母净利润</div>
      <div class="kpi-value">{fmt_num(profit, "亿元")}</div>
      <div class="kpi-change {'up' if profit and profit > 0 else 'down' if profit and profit < 0 else 'neutral'}">{'↗' if profit and profit > 0 else '↘' if profit and profit < 0 else '—'} 同比 {fmt_num(yoy_change(snapshot, 'net_profit_parent'), "%")}</div>
    </div>""")
    
    # OCF card
    ocf_class = "positive" if ocf and ocf > 0 else "negative"
    cards.append(f"""
    <div class="kpi-card {ocf_class}">
      <div class="kpi-label">经营现金流</div>
      <div class="kpi-value">{fmt_num(ocf, "亿元")}</div>
      <div class="kpi-change up">↗ 同比 {fmt_num(yoy_change(snapshot, 'net_operating_cash_flow'), "%")}</div>
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
      <div class="kpi-change {'down' if debt_ratio and debt_ratio > 70 else 'up'}">{'⚠ 偏高' if debt_ratio and debt_ratio > 70 else '✓ 健康'}</div>
    </div>""")
    
    # Total assets card
    cards.append(f"""
    <div class="kpi-card neutral">
      <div class="kpi-label">总资产</div>
      <div class="kpi-value">{fmt_num(total_assets, "亿元")}</div>
      <div class="kpi-change neutral">规模指标</div>
    </div>""")
    
    return f'<div class="kpi-grid">{ "".join(cards) }</div>'


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


def is_positive_int_token(value: Any) -> bool:
    text = str(value).strip()
    return bool(re.fullmatch(r"\d+", text)) and int(text) > 0


def is_nonnegative_int_token(value: Any) -> bool:
    text = str(value).strip()
    return bool(re.fullmatch(r"\d+", text))


def source_anchor(url: str, label: str) -> str:
    safe_url = html_module.escape(url, quote=True)
    safe_label = html_module.escape(label)
    return f'<a href="{safe_url}" target="_blank" rel="noopener noreferrer">{safe_label}</a>'


def evidence_links_from_id(evidence_id: Any, preflight: dict[str, Any]) -> str:
    text = str(evidence_id)
    if text.endswith(":missing"):
        return ""
    task_id = preflight.get("task_id") or ""
    page_match = re.search(r":p([^:]+)", text)
    table_match = re.search(r":t([^:]+)", text)
    page = page_match.group(1) if page_match else ""
    table = table_match.group(1) if table_match else ""
    links = []
    if task_id and is_positive_int_token(page):
        links.append(source_anchor(public_api_url(f"/api/pdf_page/{task_id}/{page}"), "PDF"))
        links.append(source_anchor(public_api_url(f"/api/source/{task_id}/page/{page}"), "页来源"))
    if task_id and is_nonnegative_int_token(table):
        links.append(source_anchor(public_api_url(f"/api/source/{task_id}/table/{table}"), "表格"))
    return "".join(links)


def render_section_content(section: dict[str, Any], preflight: dict[str, Any] | None = None) -> str:
    """Render a single section's content with CFO-style narrative blocks."""
    parts = []
    preflight = preflight or {}

    blocks = section.get("narrative_blocks", [])
    if isinstance(blocks, list) and blocks:
        for block in blocks:
            if not isinstance(block, dict):
                continue
            title = str(block.get("title") or "").strip()
            items = block.get("items", [])
            if not title or not isinstance(items, list) or not items:
                continue
            role = str(block.get("role") or "analysis").strip()
            parts.append(f'<div class="subsection narrative-block {html_module.escape(role)}">')
            parts.append(f'<div class="subsection-title">{html_module.escape(title)}</div>')
            parts.append('<ul class="content-list">')
            for item in items:
                parts.append(f'<li>{html_module.escape(str(item))}</li>')
            parts.append('</ul></div>')

        evidence = section.get("evidence_ids", [])
        if evidence:
            parts.append('<div class="subsection">')
            parts.append('<div class="subsection-title">本节证据</div>')
            parts.append('<div style="display:flex;flex-wrap:wrap;gap:4px;margin-top:8px;">')
            for ev in evidence:
                is_missing = str(ev).endswith(":missing") or str(ev).endswith("未返回")
                cls = "missing" if is_missing else ""
                links = evidence_links_from_id(ev, preflight)
                parts.append(f'<span class="evidence-tag {cls}">{html_module.escape(str(ev))}{links}</span>')
            parts.append('</div></div>')

        return "\n".join(parts)
    
    # Legacy fallback: keep old fields readable, but do not revive the
    # mechanical 事实/计算/判断/风险 skeleton.
    facts = section.get("facts", [])
    if facts:
        parts.append('<div class="subsection">')
        parts.append('<div class="subsection-title">证据锚点</div>')
        parts.append('<ul class="content-list">')
        for fact in facts:
            parts.append(f'<li class="fact">{html_module.escape(str(fact))}</li>')
        parts.append('</ul></div>')
    
    calcs = section.get("calculations", [])
    if calcs:
        parts.append('<div class="subsection">')
        parts.append('<div class="subsection-title">模型口径</div>')
        parts.append('<ul class="content-list">')
        for calc in calcs:
            parts.append(f'<li class="calc">{html_module.escape(str(calc))}</li>')
        parts.append('</ul></div>')
    
    judgements = section.get("judgements", [])
    if judgements:
        parts.append('<div class="subsection">')
        parts.append('<div class="subsection-title">财务解释</div>')
        parts.append('<ul class="content-list">')
        for j in judgements:
            parts.append(f'<li class="judge">{html_module.escape(str(j))}</li>')
        parts.append('</ul></div>')
    
    risks = section.get("risks_or_improvement_conditions", [])
    if risks:
        parts.append('<div class="subsection">')
        parts.append('<div class="subsection-title">验证边界</div>')
        parts.append('<ul class="content-list">')
        for risk in risks:
            parts.append(f'<li class="risk">{html_module.escape(str(risk))}</li>')
        parts.append('</ul></div>')
    
    # Evidence
    evidence = section.get("evidence_ids", [])
    if evidence:
        parts.append('<div class="subsection">')
        parts.append('<div class="subsection-title">本节证据</div>')
        parts.append('<div style="display:flex;flex-wrap:wrap;gap:4px;margin-top:8px;">')
        for ev in evidence:
            is_missing = str(ev).endswith(":missing") or str(ev).endswith("未返回")
            cls = "missing" if is_missing else ""
            links = evidence_links_from_id(ev, preflight)
            parts.append(f'<span class="evidence-tag {cls}">{html_module.escape(str(ev))}{links}</span>')
        parts.append('</div></div>')
    
    return "\n".join(parts)


def render_navigation(sections: list[dict[str, Any]]) -> str:
    """No sidebar navigation for report output.

    Financial reports are read as dense documents; a fixed side directory
    reduces space for charts and long Chinese paragraphs. Keep section anchors
    in the DOM, but do not render a persistent navigation rail.
    """
    return ""


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


def render_chart_section(chart_id: str, title: str, size: str = "", extra_class: str = "", fallback_svg: str = "") -> str:
    """Render a chart container."""
    size_class = f"chart-area {size}" if size else "chart-area"
    fallback = f'<div class="chart-fallback">{fallback_svg}</div>' if fallback_svg else ""
    return f"""
    <div class="chart-container {extra_class}">
      <div class="chart-title">{html_module.escape(title)}</div>
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
        <div>
          <div class="chart-title">收支拆解</div>
          <div class="income-bridge-subtitle">{html_module.escape(str(period_label or "当前报告期"))} · 营业收入 {fmt_yi(starting_value)} · {profit_label} {fmt_yi(ending_value)}</div>
        </div>
        <div class="income-bridge-unit">单位：亿元</div>
      </div>
      <div class="income-bridge-legend">
        <span><i class="income"></i>收入</span>
        <span><i class="expense"></i>支出</span>
        <span><i class="profit"></i>利润</span>
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
    """Build waterfall chart data for profitability decomposition."""
    revenue = safe_float(metric_value(snapshot, "operating_revenue"))
    cost = safe_float(metric_value(snapshot, "operating_cost"))
    profit = safe_float(metric_value(snapshot, "net_profit_parent"))
    
    if not revenue:
        return None
    
    gross_profit = revenue - cost if cost else revenue * 0.2
    
    steps = [
        {"name": "营业收入", "value": revenue, "base": 0},
        {"name": "营业成本", "value": -(cost or revenue * 0.8), "base": revenue - (cost or revenue * 0.8)},
        {"name": "毛利", "value": gross_profit, "base": 0},
        {"name": "期间费用", "value": -(gross_profit - profit) if profit else -(gross_profit * 0.7), "base": profit if profit else gross_profit * 0.3},
        {"name": "归母净利润", "value": profit if profit else gross_profit * 0.3, "base": 0},
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


def _raw_income_statement_rows(work_dir: Path | None, year: str) -> list[dict[str, Any]]:
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
        if item.get("statement_type") != "income_statement" or item.get("scope") != "consolidated":
            continue
        if str(item.get("period") or "") != str(year):
            continue
        rows.append(item)
    return rows


def _raw_income_value(rows: list[dict[str, Any]], metric_name: str) -> float | None:
    for item in rows:
        if item.get("metric_name") == metric_name:
            value = item.get("normalized_value")
            if value is not None:
                return safe_float(value)
    return None


def _coalesce(*values: float | None) -> float | None:
    for value in values:
        if value is not None:
            return value
    return None


def _parse_money_yi(raw: str) -> float | None:
    cleaned = raw.replace(",", "").replace("，", "").strip()
    if not cleaned:
        return None
    try:
        return float(cleaned) / 100_000_000
    except ValueError:
        return None


def _parse_pct(raw: str) -> float | None:
    cleaned = raw.replace("%", "").replace("％", "").replace(",", "").strip()
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _extract_td_rows(table_html: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for row_html in re.findall(r"<tr[^>]*>(.*?)</tr>", table_html, flags=re.I | re.S):
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row_html, flags=re.I | re.S)
        cleaned = [re.sub(r"<[^>]+>", "", cell).strip() for cell in cells]
        if cleaned:
            rows.append(cleaned)
    return rows


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

    anchor = text.find("# （1）营业收入构成")
    end = text.find("# （2）", anchor + 1) if anchor >= 0 else -1
    window = text[anchor:end if end > anchor else anchor + 12000] if anchor >= 0 else text[:12000]
    tables = re.findall(r"<table>.*?</table>", window, flags=re.I | re.S)
    if not tables:
        return []
    rows = _extract_td_rows(tables[0])
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
        value = _parse_money_yi(row[1])
        yoy = _parse_pct(row[5])
        if value is None or value <= 0:
            continue
        segments.append(
            {
                "name": row[0],
                "revenue": value,
                "revenue_yoy": yoy,
                "share": _parse_pct(row[2]),
            }
        )

    if len(segments) < 2:
        return []

    # Add product-level costs from the 10%+ revenue/profit table when available.
    if len(tables) > 1:
        for row in _extract_td_rows(tables[1]):
            if len(row) < 7:
                continue
            name = row[0]
            for segment in segments:
                if segment["name"] == name:
                    segment["cost"] = _parse_money_yi(row[2])
                    segment["gross_margin"] = _parse_pct(row[3])
                    segment["cost_yoy"] = _parse_pct(row[5])
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

    if total_revenue is None:
        return None

    known_fields: list[str] = [revenue_field]
    missing_fields: list[str] = []
    non_operating_net = None
    if non_operating_income is not None or non_operating_expenses is not None:
        non_operating_net = (non_operating_income or 0.0) - (non_operating_expenses or 0.0)

    expense_items = []
    use_total_cost = total_operating_cost is not None and raw_rows and operating_cost is None
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
        "cost": {"name": "营业成本", "value": operating_cost},
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


def svg_text(value: Any) -> str:
    return html_module.escape(str(value))


def chart_attrs(chart_id: str, title: str, value: Any, detail: str = "", value_text: str | None = None) -> str:
    display_value = value_text if value_text is not None else fmt_yi(value)
    return (
        f'class="chart-interactive" tabindex="0" role="button" '
        f'data-chart-id="{svg_text(chart_id)}" '
        f'data-title="{svg_text(title)}" data-value="{svg_text(display_value)}" data-detail="{svg_text(detail)}"'
    )


def svg_income_bridge_chart(data: dict[str, Any] | None, *, width: int = 1120, height: int = 540) -> str:
    if not data or not data.get("flow_nodes"):
        return '<div class="chart-fallback-empty">利润桥数据不足，待补充利润表收入、成本费用和净利润口径。</div>'

    segments = data.get("segments") or []
    nodes = data.get("flow_nodes") or {}
    total_revenue = safe_float(nodes.get("revenue", {}).get("value") or data.get("starting_value"))
    operating_cost = safe_float(nodes.get("cost", {}).get("value"))
    gross_profit = nodes.get("gross_profit", {}).get("value")
    operating_adjustments = nodes.get("operating_adjustments", {}).get("value")
    operating_profit = nodes.get("operating_profit", {}).get("value")
    pretax_profit = nodes.get("pretax_profit", {}).get("value")
    income_tax = nodes.get("income_tax", {}).get("value")
    attribution = nodes.get("attribution", {}).get("value")
    parent_net_profit = nodes.get("parent_net_profit", {}).get("value")
    adjustment_items = data.get("operating_adjustment_items") or []
    largest_adjustment = None
    if isinstance(adjustment_items, list) and adjustment_items:
        largest_adjustment = max(
            [item for item in adjustment_items if isinstance(item, dict)],
            key=lambda item: abs(safe_float(item.get("impact"))),
            default=None,
        )
    adjustment_detail = "期间费用、减值及其他经营项目影响"
    adjustment_label = "费用/减值/其他"
    if largest_adjustment:
        adjustment_detail = (
            f"最大影响项：{largest_adjustment.get('name')} "
            f"{fmt_yi(largest_adjustment.get('value'))}，"
            f"对利润影响 {fmt_yi(-safe_float(largest_adjustment.get('impact')))}"
        )
        adjustment_label = str(largest_adjustment.get("name") or adjustment_label)
    max_value = max(
        [abs(safe_float(v)) for v in [total_revenue, operating_cost, gross_profit, operating_adjustments, operating_profit, pretax_profit, income_tax, attribution, parent_net_profit]]
        + [abs(safe_float(item.get("revenue"))) for item in segments]
        + [1.0]
    )

    def flow_width(value: Any, minimum: float = 3.0, maximum: float = 54.0) -> float:
        return max(minimum, min(maximum, abs(safe_float(value)) / max_value * maximum))

    def ib_attrs(ib_id: str, related: list[str], title: str, value: Any, detail: str = "") -> str:
        return (
            f'class="ib-interactive" tabindex="0" role="button" '
            f'data-ib-id="{svg_text(ib_id)}" data-related="{svg_text(",".join(related))}" '
            f'data-title="{svg_text(title)}" data-value="{svg_text(fmt_yi(value))}" data-detail="{svg_text(detail)}"'
        )

    def curve(
        ib_id: str,
        related: list[str],
        title: str,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        color: str,
        value: Any,
        opacity: float = 0.28,
        detail: str = "",
    ) -> str:
        c1 = x1 + (x2 - x1) * 0.52
        c2 = x2 - (x2 - x1) * 0.52
        return (
            f'<g {ib_attrs(ib_id, related, title, value, detail)}>'
            f'<path class="ib-hit" d="M{x1:.1f},{y1:.1f} C{c1:.1f},{y1:.1f} {c2:.1f},{y2:.1f} {x2:.1f},{y2:.1f}" '
            f'fill="none" stroke="transparent" stroke-width="{max(flow_width(value) + 18, 26):.1f}" stroke-linecap="round"/>'
            f'<path class="ib-flow" d="M{x1:.1f},{y1:.1f} C{c1:.1f},{y1:.1f} {c2:.1f},{y2:.1f} {x2:.1f},{y2:.1f}" '
            f'fill="none" stroke="{color}" stroke-width="{flow_width(value):.1f}" stroke-linecap="round" opacity="{opacity}"/>'
            f'</g>'
        )

    def ribbon(
        ib_id: str,
        related: list[str],
        title: str,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        color: str,
        value: Any,
        opacity: float = 0.58,
        detail: str = "",
        minimum: float = 8.0,
        maximum: float = 58.0,
    ) -> str:
        width_px = flow_width(value, minimum, maximum)
        half = width_px / 2
        c1 = x1 + (x2 - x1) * 0.54
        c2 = x2 - (x2 - x1) * 0.54
        path = (
            f"M{x1:.1f},{y1 - half:.1f} "
            f"C{c1:.1f},{y1 - half:.1f} {c2:.1f},{y2 - half:.1f} {x2:.1f},{y2 - half:.1f} "
            f"L{x2:.1f},{y2 + half:.1f} "
            f"C{c2:.1f},{y2 + half:.1f} {c1:.1f},{y1 + half:.1f} {x1:.1f},{y1 + half:.1f} Z"
        )
        return (
            f'<g {ib_attrs(ib_id, related, title, value, detail)}>'
            f'<path class="ib-hit" d="{path}" fill="transparent" stroke="transparent" stroke-width="14"/>'
            f'<path class="ib-flow ib-ribbon" d="{path}" fill="{color}" opacity="{opacity}" stroke="rgba(255,255,255,0.56)" stroke-width="0.8"/>'
            f'</g>'
        )

    def ribbon_band(
        ib_id: str,
        related: list[str],
        title: str,
        x1: float,
        y1_top: float,
        y1_bottom: float,
        x2: float,
        y2_top: float,
        y2_bottom: float,
        color: str,
        value: Any,
        opacity: float = 0.62,
        detail: str = "",
        ratio: float | None = None,
    ) -> str:
        c1 = x1 + (x2 - x1) * 0.54
        c2 = x2 - (x2 - x1) * 0.54
        path = (
            f"M{x1:.1f},{y1_top:.1f} "
            f"C{c1:.1f},{y1_top:.1f} {c2:.1f},{y2_top:.1f} {x2:.1f},{y2_top:.1f} "
            f"L{x2:.1f},{y2_bottom:.1f} "
            f"C{c2:.1f},{y2_bottom:.1f} {c1:.1f},{y1_bottom:.1f} {x1:.1f},{y1_bottom:.1f} Z"
        )
        ratio_attr = "" if ratio is None else f' data-ratio="{ratio:.6f}"'
        return (
            f'<g {ib_attrs(ib_id, related, title, value, detail)}{ratio_attr}>'
            f'<path class="ib-hit" d="{path}" fill="transparent" stroke="transparent" stroke-width="14"/>'
            f'<path class="ib-flow ib-ribbon" d="{path}" fill="{color}" opacity="{opacity}" stroke="rgba(255,255,255,0.58)" stroke-width="0.8"/>'
            f'</g>'
        )

    def node(
        ib_id: str,
        related: list[str],
        x: float,
        y: float,
        h: float,
        color: str,
        label: str,
        value: Any,
        anchor: str = "start",
        value_color: str | None = None,
        detail: str = "",
    ) -> str:
        tx = x + 18 if anchor == "start" else x - 12
        text_anchor = "start" if anchor == "start" else "end"
        value_color = value_color or color
        return (
            f'<g {ib_attrs(ib_id, related, label, value, detail)}>'
            f'<rect x="{x:.1f}" y="{y - h / 2:.1f}" width="12" height="{h:.1f}" rx="2" fill="{color}"/>'
            f'<text x="{tx:.1f}" y="{y - 7:.1f}" text-anchor="{text_anchor}" class="ib-label">{svg_text(label)}</text>'
            f'<text x="{tx:.1f}" y="{y + 19:.1f}" text-anchor="{text_anchor}" class="ib-value" fill="{value_color}">{svg_text(fmt_yi(value))}</text>'
            f'<rect class="ib-hit" x="{min(x, tx) - 10:.1f}" y="{y - h / 2 - 10:.1f}" width="{abs(tx - x) + 190:.1f}" height="{max(h + 20, 62):.1f}" rx="8" fill="transparent"/>'
            f'</g>'
        )

    def segment_label(item: dict[str, Any], x: float, y: float, ib_id: str, related: list[str]) -> str:
        yoy = item.get("revenue_yoy")
        yoy_text = "—" if yoy is None else f"{safe_float(yoy):+.2f}%"
        yoy_color = "#cc5b24" if safe_float(yoy) >= 0 else "#1fb59d"
        name = str(item.get("name") or "")
        if len(name) > 12:
            name = name[:11] + "..."
        detail = "同比：" + yoy_text if yoy is not None else "同比：未返回"
        return (
            f'<g {ib_attrs(ib_id, related, str(item.get("name") or ""), item.get("revenue"), detail)}>'
            f'<text x="{x:.1f}" y="{y - 11:.1f}" text-anchor="end" class="ib-yoy" fill="{yoy_color}">{svg_text(yoy_text)}</text>'
            f'<text x="{x + 8:.1f}" y="{y - 11:.1f}" text-anchor="start" class="ib-label">{svg_text(name)}</text>'
            f'<text x="{x + 8:.1f}" y="{y + 15:.1f}" text-anchor="start" class="ib-value" fill="#3498db">{svg_text(fmt_yi(item.get("revenue")))}</text>'
            f'<rect class="ib-hit" x="{x - 92:.1f}" y="{y - 40:.1f}" width="305" height="70" rx="8" fill="transparent"/>'
            f'</g>'
        )

    income_blue = "#35a9f4"
    income_blue_soft = "#bfe4fb"
    expense_yellow = "#f2c400"
    expense_soft = "#ffeaa3"
    profit_red = "#ff3548"
    profit_soft = "#ffb6bb"
    text_css = """
  <style>
    .ib-label { font: 700 18px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; fill: #111827; }
    .ib-value { font: 700 22px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; font-variant-numeric: tabular-nums; }
    .ib-yoy { font: 700 16px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; font-variant-numeric: tabular-nums; }
    .ib-muted { font: 500 18px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; fill: #8a8f98; }
    .ib-ribbon { vector-effect: non-scaling-stroke; }
  </style>
"""

    left_x, left_node_x = 300, 330
    revenue_x, revenue_y = 455, 284
    split_x = 608
    revenue_h = 190.0
    revenue_top = revenue_y - revenue_h / 2
    revenue_bottom = revenue_y + revenue_h / 2
    cost_ratio = min(1.0, max(0.0, safe_float(operating_cost) / total_revenue)) if total_revenue else 0.0
    gross_ratio = min(1.0, max(0.0, safe_float(gross_profit) / total_revenue)) if total_revenue else 0.0
    ratio_total = cost_ratio + gross_ratio
    if ratio_total > 1.000001:
        cost_ratio /= ratio_total
        gross_ratio /= ratio_total
    gross_h = revenue_h * gross_ratio
    cost_h = revenue_h * cost_ratio
    gross_top = revenue_top
    gross_bottom = gross_top + gross_h
    cost_top = gross_bottom
    cost_bottom = cost_top + cost_h
    gross_y = (gross_top + gross_bottom) / 2 if gross_h > 0 else revenue_top + 10
    cost_y = (cost_top + cost_bottom) / 2 if cost_h > 0 else revenue_y
    op_x, op_y = 745, 284
    pretax_x, pretax_y = 850, 284
    right_x = 1000

    seg_count = max(1, len(segments))
    top = 116 if seg_count >= 5 else 150
    step_y = min(55, 310 / max(1, seg_count - 1)) if seg_count > 1 else 0
    segment_parts: list[str] = []
    for i, item in enumerate(segments):
        y = top + i * step_y
        value = safe_float(item.get("revenue"))
        segment_id = f"seg-{i}"
        flow_id = f"flow-{segment_id}-revenue"
        segment_parts.append(segment_label(item, 118, y, segment_id, [flow_id, "node-revenue"]))
        segment_parts.append(
            f'<g {ib_attrs(f"node-{segment_id}", [segment_id, flow_id, "node-revenue"], str(item.get("name") or ""), value, "收入分项")}>'
            f'<rect x="{left_node_x}" y="{y - 12:.1f}" width="12" height="24" rx="2" fill="{income_blue}"/>'
            f'<rect class="ib-hit" x="{left_node_x - 12}" y="{y - 22:.1f}" width="38" height="44" rx="8" fill="transparent"/>'
            f'</g>'
        )
        segment_parts.append(curve(flow_id, [segment_id, f"node-{segment_id}", "node-revenue"], f"{item.get('name')} → 营业收入", left_node_x + 12, y, revenue_x, revenue_y, income_blue_soft, value, 0.72, "收入分项汇入营业收入"))

    center_parts = [
        node("node-revenue", ["flow-revenue-gross", "flow-revenue-cost"], revenue_x, revenue_y, revenue_h, income_blue, nodes.get("revenue", {}).get("name") or "营业收入", total_revenue, "start", income_blue, "收入分项汇总"),
        ribbon_band("flow-revenue-gross", ["node-revenue", "node-gross"], "营业收入 → 毛利", revenue_x + 12, gross_top, gross_bottom, split_x, gross_top, gross_bottom, profit_soft, gross_profit, 0.62, f"收入扣除营业成本后的毛利，占营业收入 {gross_ratio * 100:.2f}%", gross_ratio),
        ribbon_band("flow-revenue-cost", ["node-revenue", "node-cost"], "营业收入 → 营业成本", revenue_x + 12, cost_top, cost_bottom, split_x, cost_top, cost_bottom, expense_soft, operating_cost, 0.72, f"营业成本流出，占营业收入 {cost_ratio * 100:.2f}%", cost_ratio),
        node("node-cost", ["flow-revenue-cost", "flow-cost-op"], split_x, cost_y, cost_h, expense_yellow, "营业成本", operating_cost, "start", expense_yellow, "利润表营业成本"),
        node("node-gross", ["flow-revenue-gross", "flow-gross-op"], split_x, gross_y, gross_h, profit_red, "毛利", gross_profit, "start", profit_red, "营业收入减营业成本"),
        curve("flow-gross-op", ["node-gross", "node-operating-profit"], "毛利 → 营业利润", split_x + 12, gross_y, op_x, op_y - 28, profit_soft, gross_profit, 0.64, "毛利经过期间费用和减值抵减后形成营业利润/亏损"),
        curve("flow-cost-op", ["node-cost", "node-operating-profit"], f"{adjustment_label} → 营业利润", split_x + 12, cost_y, op_x, op_y + 24, expense_soft, operating_adjustments, 0.54, adjustment_detail),
        node("node-operating-profit", ["flow-gross-op", "flow-cost-op", "flow-op-pretax"], op_x, op_y, 82, profit_red, nodes.get("operating_profit", {}).get("name") or "营业利润", operating_profit, "start", profit_red, "合并利润表营业利润"),
        curve("flow-op-pretax", ["node-operating-profit", "node-pretax"], "营业利润 → 利润总额", op_x + 12, op_y, pretax_x, pretax_y, profit_soft, pretax_profit, 0.68, "营业外收支后形成利润总额"),
        node("node-pretax", ["flow-op-pretax", "flow-pretax-parent", "flow-pretax-tax", "flow-pretax-other"], pretax_x, pretax_y, 72, profit_red, nodes.get("pretax_profit", {}).get("name") or "利润总额", pretax_profit, "start", profit_red, "合并利润表利润总额"),
        curve("flow-pretax-parent", ["node-pretax", "node-parent-profit"], "利润总额 → 归母净利润", pretax_x + 12, pretax_y - 6, right_x, 220, profit_soft, parent_net_profit, 0.72, "扣除所得税和归属调整后的归母口径"),
        curve("flow-pretax-tax", ["node-pretax", "node-tax"], "利润总额 → 所得税", pretax_x + 12, pretax_y + 12, right_x, 298, expense_soft, income_tax, 0.70, "所得税费用"),
        curve("flow-pretax-other", ["node-pretax", "node-other"], "利润总额 → 其他", pretax_x + 12, pretax_y + 24, right_x, 365, expense_soft, attribution, 0.42, "净利润与归母口径之间的归属调整"),
        node("node-parent-profit", ["flow-pretax-parent"], right_x, 220, 62, profit_red, nodes.get("parent_net_profit", {}).get("name") or "归母净利润", parent_net_profit, "start", profit_red, "最终归母口径"),
        node("node-tax", ["flow-pretax-tax"], right_x, 298, 18, expense_yellow, "所得税", income_tax, "start", expense_yellow, "所得税费用"),
        node("node-other", ["flow-pretax-other"], right_x, 365, 16, expense_yellow, "其他", attribution, "start", expense_yellow, "少数股东损益或口径调整"),
    ]

    return f"""
<svg viewBox="0 0 {width} {height}" role="img" aria-label="收支拆解利润桥">
  {text_css}
  <rect x="0" y="0" width="{width}" height="{height}" fill="#ffffff"/>
  {''.join(segment_parts)}
  {''.join(center_parts)}
  <text x="{revenue_x - 62}" y="{revenue_y - 98}" class="ib-muted">{svg_text(data.get("period_label") or "")}</text>
</svg>
"""


def svg_bar_line_chart(data: dict[str, Any] | None, *, width: int = 760, height: int = 330) -> str:
    if not data:
        return '<div class="chart-fallback-empty">趋势数据不足，待补充历史口径。</div>'
    years = [str(item) for item in data.get("years", [])]
    revenue = [safe_float(v) for v in data.get("revenue", [])]
    profit = [safe_float(v) for v in data.get("profit", [])]
    if not years:
        return '<div class="chart-fallback-empty">趋势数据不足，待补充历史口径。</div>'

    left, right, top, bottom = 58, 28, 34, 48
    plot_w = width - left - right
    plot_h = height - top - bottom
    max_revenue = max(revenue + [1])
    min_profit = min(profit + [0])
    max_profit = max(profit + [0])
    profit_span = max(max_profit - min_profit, 1)
    gap = plot_w / max(1, len(years))
    bar_w = min(54, gap * 0.44)
    bars = []
    line_points = []
    for i, year in enumerate(years):
        cx = left + gap * i + gap / 2
        rev = revenue[i] if i < len(revenue) else 0
        prof = profit[i] if i < len(profit) else 0
        bar_h = max(2, rev / max_revenue * plot_h)
        x = cx - bar_w / 2
        y = top + plot_h - bar_h
        py = top + (max_profit - prof) / profit_span * plot_h
        line_points.append((cx, py, prof))
        bars.append(
            f'<g {chart_attrs(f"trend-revenue-{i}", f"{year} 营业收入", rev, "营业收入趋势")}>'
            f'<rect class="chart-mark" x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{bar_h:.1f}" rx="6" fill="#3b82f6" opacity="0.88"/>'
            f'<rect class="chart-hit" x="{x - 8:.1f}" y="{top:.1f}" width="{bar_w + 16:.1f}" height="{plot_h:.1f}" fill="transparent"/>'
            f'</g>'
            f'<text x="{cx:.1f}" y="{height - 18}" text-anchor="middle" class="svg-muted">{svg_text(year)}</text>'
        )
    path = " ".join(f"{x:.1f},{y:.1f}" for x, y, _ in line_points)
    dots = "".join(
        f'<g {chart_attrs(f"trend-profit-{i}", f"{years[i]} 归母净利润", value, "归母净利润趋势")}>'
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4.5" class="svg-dot chart-mark" stroke="#10b981"/>'
        f'<circle class="chart-hit" cx="{x:.1f}" cy="{y:.1f}" r="16" fill="transparent"/>'
        f'</g>'
        f'<text x="{x:.1f}" y="{max(14, y - 10):.1f}" text-anchor="middle" class="svg-value">{fmt_num(value)}</text>'
        for i, (x, y, value) in enumerate(line_points)
    )
    return f"""
<svg viewBox="0 0 {width} {height}" role="img" aria-label="营业收入与净利润趋势">
  <line x1="{left}" y1="{top + plot_h}" x2="{width - right}" y2="{top + plot_h}" class="svg-axis"/>
  <line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_h}" class="svg-axis"/>
  <text x="{left}" y="20" class="svg-label">亿元</text>
  {''.join(bars)}
  <polyline points="{path}" class="svg-line-green"/>
  {dots}
  <g transform="translate({width - 205}, 16)">
    <rect width="10" height="10" rx="3" fill="#3b82f6"/><text x="16" y="10" class="svg-muted">营业收入</text>
    <line x1="90" y1="6" x2="112" y2="6" class="svg-line-green"/><text x="120" y="10" class="svg-muted">归母净利润</text>
  </g>
</svg>
"""


def svg_cashflow_chart(data: dict[str, Any] | None, *, width: int = 760, height: int = 330) -> str:
    if not data:
        return '<div class="chart-fallback-empty">现金流数据不足，待补充三表口径。</div>'
    items = [
        ("经营现金流", safe_float(data.get("operating")), "#10b981"),
        ("投资现金流", safe_float(data.get("investing")), "#ef4444"),
        ("筹资现金流", safe_float(data.get("financing")), "#f59e0b"),
        ("资本开支", -safe_float(data.get("capex")), "#8b5cf6"),
    ]
    if data.get("free_cash_flow") is not None:
        items.append(("自由现金流", safe_float(data.get("free_cash_flow")), "#06b6d4"))
    left, right, top, bottom = 70, 24, 34, 78
    plot_w = width - left - right
    plot_h = height - top - bottom
    max_abs = max([abs(v) for _, v, _ in items] + [1])
    zero_y = top + plot_h / 2
    gap = plot_w / max(1, len(items))
    bar_w = min(58, gap * 0.48)
    rows = []
    for i, (label, value, color) in enumerate(items):
        cx = left + gap * i + gap / 2
        bar_h = abs(value) / max_abs * (plot_h / 2 - 12)
        y = zero_y - bar_h if value >= 0 else zero_y
        rows.append(
            f'<g {chart_attrs(f"cashflow-{i}", label, value, "现金流结构项目")}>'
            f'<rect class="chart-mark" x="{cx - bar_w / 2:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{max(2, bar_h):.1f}" rx="6" fill="{color}" opacity="0.9"/>'
            f'<rect class="chart-hit" x="{cx - bar_w / 2 - 8:.1f}" y="{top:.1f}" width="{bar_w + 16:.1f}" height="{plot_h:.1f}" fill="transparent"/>'
            f'</g>'
            f'<text x="{cx:.1f}" y="{height - 42}" text-anchor="middle" class="svg-muted">{svg_text(label)}</text>'
            f'<text x="{cx:.1f}" y="{height - 22}" text-anchor="middle" class="svg-value">{fmt_num(value)}</text>'
        )
    return f"""
<svg viewBox="0 0 {width} {height}" role="img" aria-label="现金流结构分析">
  <line x1="{left}" y1="{zero_y:.1f}" x2="{width - right}" y2="{zero_y:.1f}" class="svg-axis"/>
  <line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_h}" class="svg-axis"/>
  <text x="{left}" y="20" class="svg-label">亿元</text>
  {''.join(rows)}
</svg>
"""


def svg_donut_chart(data: dict[str, Any] | None, *, width: int = 520, height: int = 300) -> str:
    categories = data.get("categories", []) if data else []
    categories = [c for c in categories if safe_float(c.get("value")) > 0]
    if not categories:
        return '<div class="chart-fallback-empty">结构数据不足，待补充资产负债表明细。</div>'
    colors = ["#3b82f6", "#06b6d4", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6", "#ec4899"]
    total = sum(safe_float(c.get("value")) for c in categories) or 1
    cx, cy, radius = 145, 140, 92
    current = -90.0
    segments = []
    legend = []
    for i, item in enumerate(categories):
        value = safe_float(item.get("value"))
        pct = value / total
        angle = pct * 360
        end = current + angle
        large = 1 if angle > 180 else 0
        start_rad = current * 3.141592653589793 / 180
        end_rad = end * 3.141592653589793 / 180
        x1 = cx + radius * __import__("math").cos(start_rad)
        y1 = cy + radius * __import__("math").sin(start_rad)
        x2 = cx + radius * __import__("math").cos(end_rad)
        y2 = cy + radius * __import__("math").sin(end_rad)
        color = colors[i % len(colors)]
        segments.append(
            f'<g {chart_attrs(f"donut-{i}", str(item.get("name") or ""), value, f"占比 {pct * 100:.1f}%")}>'
            f'<path class="chart-mark" d="M {cx} {cy} L {x1:.1f} {y1:.1f} A {radius} {radius} 0 {large} 1 {x2:.1f} {y2:.1f} Z" fill="{color}" opacity="0.9"/>'
            f'<path class="chart-hit" d="M {cx} {cy} L {x1:.1f} {y1:.1f} A {radius} {radius} 0 {large} 1 {x2:.1f} {y2:.1f} Z" fill="transparent" stroke="transparent" stroke-width="10"/>'
            f'</g>'
        )
        ly = 72 + i * 28
        legend.append(
            f'<rect x="300" y="{ly - 10}" width="10" height="10" rx="3" fill="{color}"/>'
            f'<text x="318" y="{ly}" class="svg-label">{svg_text(item.get("name"))}</text>'
            f'<text x="{width - 22}" y="{ly}" text-anchor="end" class="svg-value">{pct * 100:.1f}%</text>'
        )
        current = end
    return f"""
<svg viewBox="0 0 {width} {height}" role="img" aria-label="结构分布环图">
  {''.join(segments)}
  <circle cx="{cx}" cy="{cy}" r="54" fill="#0f172a"/>
  <text x="{cx}" y="{cy - 4}" text-anchor="middle" class="svg-label">合计</text>
  <text x="{cx}" y="{cy + 20}" text-anchor="middle" class="svg-value">{fmt_num(total, "亿元")}</text>
  {''.join(legend)}
</svg>
"""


def svg_radar_chart(data: dict[str, Any] | None, *, width: int = 520, height: int = 320) -> str:
    if not data:
        return '<div class="chart-fallback-empty">雷达图数据不足，待补充完整比率口径。</div>'
    labels = ["销售净利率", "资产周转率", "权益乘数", "ROE"]
    values = [
        min(100, abs(safe_float(data.get("net_margin"))) * 2),
        min(100, abs(safe_float(data.get("asset_turnover"))) * 50),
        min(100, abs(safe_float(data.get("equity_multiplier"))) * 12),
        min(100, abs(safe_float(data.get("roe"))) * 2),
    ]
    cx, cy, radius = width / 2, height / 2 + 10, 104
    math = __import__("math")
    axes = []
    points = []
    for i, label in enumerate(labels):
        angle = -90 + i * 360 / len(labels)
        rad = angle * math.pi / 180
        ax = cx + radius * math.cos(rad)
        ay = cy + radius * math.sin(rad)
        axes.append(f'<line x1="{cx}" y1="{cy}" x2="{ax:.1f}" y2="{ay:.1f}" class="svg-grid"/>')
        axes.append(f'<text x="{cx + (radius + 28) * math.cos(rad):.1f}" y="{cy + (radius + 22) * math.sin(rad):.1f}" text-anchor="middle" class="svg-label">{svg_text(label)}</text>')
        value_radius = radius * values[i] / 100
        points.append((cx + value_radius * math.cos(rad), cy + value_radius * math.sin(rad)))
    polygon = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    rings = "".join(f'<circle cx="{cx}" cy="{cy}" r="{radius * scale / 4:.1f}" fill="none" class="svg-grid"/>' for scale in range(1, 5))
    detail = " / ".join(f"{label}: {value:.2f}" for label, value in zip(labels, values))
    return f"""
<svg viewBox="0 0 {width} {height}" role="img" aria-label="杜邦分析雷达图">
  {rings}
  {''.join(axes)}
  <g {chart_attrs("dupont-radar", "杜邦分析", 0, detail, "综合比率")}>
    <polygon class="chart-mark" points="{polygon}" fill="rgba(59,130,246,0.24)" stroke="#3b82f6" stroke-width="3"/>
    <polygon class="chart-hit" points="{polygon}" fill="transparent" stroke="transparent" stroke-width="18"/>
  </g>
  {''.join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="#3b82f6"/>' for x, y in points)}
</svg>
"""


def svg_waterfall_chart(data: dict[str, Any] | None, *, width: int = 760, height: int = 330) -> str:
    steps = data.get("steps", []) if data else []
    if not steps:
        return '<div class="chart-fallback-empty">盈利分解数据不足，待补充收入成本口径。</div>'
    left, right, top, bottom = 60, 24, 34, 72
    plot_w = width - left - right
    plot_h = height - top - bottom
    lows = [safe_float(s.get("base")) for s in steps]
    highs = [safe_float(s.get("base")) + safe_float(s.get("value")) for s in steps]
    min_v = min(lows + highs + [0])
    max_v = max(lows + highs + [1])
    span = max(max_v - min_v, 1)
    zero_y = top + (max_v - 0) / span * plot_h
    gap = plot_w / max(1, len(steps))
    bar_w = min(64, gap * 0.52)
    bars = []
    for i, step in enumerate(steps):
        base = safe_float(step.get("base"))
        value = safe_float(step.get("value"))
        y1 = top + (max_v - base) / span * plot_h
        y2 = top + (max_v - (base + value)) / span * plot_h
        y = min(y1, y2)
        h = max(2, abs(y2 - y1))
        cx = left + gap * i + gap / 2
        color = "#10b981" if value >= 0 else "#ef4444"
        end_value = base + value
        bars.append(
            f'<g {chart_attrs(f"waterfall-{i}", str(step.get("name") or ""), value, f"base {base:.2f} 亿 / end {end_value:.2f} 亿")}>'
            f'<rect class="chart-mark" x="{cx - bar_w / 2:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{h:.1f}" rx="6" fill="{color}" opacity="0.9"/>'
            f'<rect class="chart-hit" x="{cx - bar_w / 2 - 8:.1f}" y="{top:.1f}" width="{bar_w + 16:.1f}" height="{plot_h:.1f}" fill="transparent"/>'
            f'</g>'
            f'<text x="{cx:.1f}" y="{height - 42}" text-anchor="middle" class="svg-muted">{svg_text(step.get("name"))}</text>'
            f'<text x="{cx:.1f}" y="{max(14, y - 8):.1f}" text-anchor="middle" class="svg-value">{fmt_num(value)}</text>'
        )
    return f"""
<svg viewBox="0 0 {width} {height}" role="img" aria-label="盈利分解瀑布图">
  <line x1="{left}" y1="{zero_y:.1f}" x2="{width - right}" y2="{zero_y:.1f}" class="svg-axis"/>
  <line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_h}" class="svg-axis"/>
  <text x="{left}" y="20" class="svg-label">亿元</text>
  {''.join(bars)}
</svg>
"""


def svg_peer_radar_chart(data: dict[str, Any] | None, *, width: int = 760, height: int = 340) -> str:
    if not data:
        return '<div class="chart-fallback-empty">同业样本不足，暂不生成同业雷达图。</div>'
    labels = data.get("metrics", [])
    company = [safe_float(v) for v in data.get("company", [])]
    peer = [safe_float(v) for v in data.get("peer_median", [])]
    if not labels:
        return '<div class="chart-fallback-empty">同业样本不足，暂不生成同业雷达图。</div>'
    math = __import__("math")
    cx, cy, radius = width / 2, height / 2 + 10, 104

    def points(values: list[float]) -> str:
        result = []
        for i, value in enumerate(values[: len(labels)]):
            rad = (-90 + i * 360 / len(labels)) * math.pi / 180
            result.append((cx + radius * min(100, max(0, value)) / 100 * math.cos(rad), cy + radius * min(100, max(0, value)) / 100 * math.sin(rad)))
        return " ".join(f"{x:.1f},{y:.1f}" for x, y in result)

    axes = []
    for i, label in enumerate(labels):
        rad = (-90 + i * 360 / len(labels)) * math.pi / 180
        axes.append(f'<line x1="{cx}" y1="{cy}" x2="{cx + radius * math.cos(rad):.1f}" y2="{cy + radius * math.sin(rad):.1f}" class="svg-grid"/>')
        axes.append(f'<text x="{cx + (radius + 34) * math.cos(rad):.1f}" y="{cy + (radius + 24) * math.sin(rad):.1f}" text-anchor="middle" class="svg-label">{svg_text(label)}</text>')
    rings = "".join(f'<circle cx="{cx}" cy="{cy}" r="{radius * scale / 4:.1f}" fill="none" class="svg-grid"/>' for scale in range(1, 5))
    company_detail = " / ".join(f"{label}: {value:.2f}" for label, value in zip(labels, company))
    peer_detail = " / ".join(f"{label}: {value:.2f}" for label, value in zip(labels, peer))
    return f"""
<svg viewBox="0 0 {width} {height}" role="img" aria-label="同业竞争对比雷达图">
  {rings}
  {''.join(axes)}
  <g {chart_attrs("peer-company", "本公司", 0, company_detail, "评分雷达")}>
    <polygon class="chart-mark" points="{points(company)}" fill="rgba(59,130,246,0.20)" stroke="#3b82f6" stroke-width="3"/>
    <polygon class="chart-hit" points="{points(company)}" fill="transparent" stroke="transparent" stroke-width="18"/>
  </g>
  <g {chart_attrs("peer-median", "行业中位数", 0, peer_detail, "评分雷达")}>
    <polygon class="chart-mark" points="{points(peer)}" fill="rgba(245,158,11,0.12)" stroke="#f59e0b" stroke-width="2" stroke-dasharray="6 4"/>
    <polygon class="chart-hit" points="{points(peer)}" fill="transparent" stroke="transparent" stroke-width="18"/>
  </g>
  <g transform="translate({width - 210}, 18)">
    <rect width="10" height="10" rx="3" fill="#3b82f6"/><text x="16" y="10" class="svg-muted">本公司</text>
    <rect x="88" width="10" height="10" rx="3" fill="#f59e0b"/><text x="104" y="10" class="svg-muted">行业中位数</text>
  </g>
</svg>
"""


def svg_solvency_gauges(data: dict[str, Any] | None, *, width: int = 760, height: int = 220) -> str:
    if not data:
        return '<div class="chart-fallback-empty">偿债指标不足，待补充流动资产与负债口径。</div>'
    items = [
        ("资产负债率", data.get("debt_ratio"), 100, "%"),
        ("流动比率", data.get("current_ratio"), 3, "x"),
        ("速动比率", data.get("quick_ratio"), 3, "x"),
        ("现金比率", data.get("cash_ratio"), 1, "x"),
    ]
    cards = []
    for i, (label, raw, max_v, unit) in enumerate(items):
        value = safe_float(raw)
        pct = max(0, min(1, value / max_v if max_v else 0))
        x = 30 + i * 180
        color = "#10b981" if (label != "资产负债率" and value >= 1) or (label == "资产负债率" and value <= 60) else "#f59e0b"
        cards.append(
            f'<g {chart_attrs(f"solvency-{i}", label, value, "偿债能力指标", fmt_num(value, unit))}>'
            f'<rect class="chart-mark" x="{x}" y="36" width="138" height="120" rx="10" fill="rgba(15,23,42,0.55)" stroke="#334155"/>'
            f'<rect x="{x + 18}" y="112" width="102" height="10" rx="5" fill="#334155"/>'
            f'<rect class="chart-mark" x="{x + 18}" y="112" width="{102 * pct:.1f}" height="10" rx="5" fill="{color}"/>'
            f'<rect class="chart-hit" x="{x}" y="36" width="138" height="120" rx="10" fill="transparent"/>'
            f'</g>'
            f'<text x="{x + 69}" y="82" text-anchor="middle" class="svg-value">{fmt_num(value, unit)}</text>'
            f'<text x="{x + 69}" y="144" text-anchor="middle" class="svg-label">{svg_text(label)}</text>'
        )
    return f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="偿债能力仪表盘">{"".join(cards)}</svg>'


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
    cashflow_data = build_cashflow_data(snapshot)
    asset_data = build_asset_structure_data(snapshot)
    debt_data = build_debt_structure_data(snapshot)
    dupont_data = build_dupont_data(snapshot)
    solvency_data = build_solvency_gauges(snapshot)
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
        <section class="section" id="section-{sid}">
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
          {render_chart_section("revenue-profit-chart", "营业收入与净利润趋势", "large", fallback_svg=svg_bar_line_chart(revenue_profit_data))}
          {render_chart_section("cashflow-chart", "现金流结构分析", "large", fallback_svg=svg_cashflow_chart(cashflow_data))}
        </div>
        """
    
    # Asset/Debt structure - goes after section 5 (Asset Quality)
    if asset_data or debt_data:
        charts = []
        if asset_data:
            charts.append(render_chart_section("asset-structure-chart", "资产结构分布", fallback_svg=svg_donut_chart(asset_data)))
        if debt_data:
            charts.append(render_chart_section("debt-structure-chart", "负债结构分布", fallback_svg=svg_donut_chart(debt_data)))
        chart_panels["after_asset_quality"] = f'<div class="chart-grid">{"".join(charts)}</div>'
    
    # DuPont + Solvency - goes after section 6 (Debt)
    if dupont_data or solvency_data:
        dupont_html = render_chart_section("dupont-chart", "杜邦分析雷达图", fallback_svg=svg_radar_chart(dupont_data)) if dupont_data else ""
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
              <div class="chart-title">偿债能力仪表盘</div>
              <div class="chart-grid-3">{''.join(gauges)}</div>
              <div class="chart-fallback">{svg_solvency_gauges(solvency_data)}</div>
            </div>
            """
        chart_panels["after_debt"] = f'<div class="chart-grid">{dupont_html}{solvency_html}</div>'
    
    # Peer comparison - goes after section 8 (Industry)
    if peer_data:
        chart_panels["after_industry"] = render_chart_section("peer-comparison-chart", "同业竞争对比", "large", fallback_svg=svg_peer_radar_chart(peer_data))
    
    # Profitability waterfall - goes after section 4 (Profitability)
    if profitability_data:
        chart_panels["after_profitability"] = render_chart_section("profitability-waterfall", "盈利分解瀑布图", "large", fallback_svg=svg_waterfall_chart(profitability_data))
    
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
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin:20px 0;">
        {''.join(quality_badges)}
      </div>
      
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
    
    output_path = Path("/home/maoyd/.hermes/profiles/siq_analysis/test_v2_renderer.html")
    output_path.write_text(result, encoding="utf-8")
    print(f"Test HTML written to: {output_path}")
    print(f"File size: {len(result)} bytes")
