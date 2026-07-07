#!/usr/bin/env python3
"""SVG fallback chart helpers for the SIQ HTML report renderer."""

from __future__ import annotations

import html as html_module
import importlib.util
from pathlib import Path
from typing import Any

_FINANCIAL_CHART_MODULE_PATH = Path(__file__).resolve().parent / "financial_chart_design.py"
_financial_chart_spec = importlib.util.spec_from_file_location("siq_financial_chart_design", _FINANCIAL_CHART_MODULE_PATH)
if not _financial_chart_spec or not _financial_chart_spec.loader:
    raise RuntimeError(f"missing financial chart design module: {_FINANCIAL_CHART_MODULE_PATH}")
_financial_chart_module = importlib.util.module_from_spec(_financial_chart_spec)
_financial_chart_spec.loader.exec_module(_financial_chart_module)
render_income_bridge_svg = _financial_chart_module.render_income_bridge_svg

def safe_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return default

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

def svg_text(value: Any) -> str:
    return html_module.escape(str(value))

def chart_attrs(chart_id: str, title: str, value: Any, detail: str = "", value_text: str | None = None) -> str:
    display_value = value_text if value_text is not None else fmt_yi(value)
    aria = "，".join(part for part in [str(title), str(display_value), str(detail)] if part)
    return (
        f'class="chart-interactive" tabindex="0" role="button" '
        f'data-chart-id="{svg_text(chart_id)}" '
        f'data-title="{svg_text(title)}" data-value="{svg_text(display_value)}" data-detail="{svg_text(detail)}" '
        f'aria-label="{svg_text(aria)}"'
    )

def svg_title(title: str, value: Any, detail: str = "", value_text: str | None = None) -> str:
    display_value = value_text if value_text is not None else fmt_yi(value)
    parts = [str(title), str(display_value)]
    if detail:
        parts.append(str(detail))
    return f"<title>{svg_text(' · '.join(part for part in parts if part))}</title>"

def svg_income_bridge_chart(data: dict[str, Any] | None, *, width: int = 1280, height: int = 600) -> str:
    """Render income bridge via the reusable financial chart design module."""
    return render_income_bridge_svg(data, width=width, height=height)

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
            f'{svg_title(f"{year} 营业收入", rev, "营业收入趋势")}'
            f'<rect class="chart-mark" x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{bar_h:.1f}" rx="6" fill="#3b82f6" opacity="0.88"/>'
            f'<rect class="chart-hit" x="{x - 8:.1f}" y="{top:.1f}" width="{bar_w + 16:.1f}" height="{plot_h:.1f}" fill="transparent"/>'
            f'</g>'
            f'<text x="{cx:.1f}" y="{max(16, y - 8):.1f}" text-anchor="middle" class="svg-value">{fmt_num(rev)}</text>'
            f'<text x="{cx:.1f}" y="{height - 18}" text-anchor="middle" class="svg-muted">{svg_text(year)}</text>'
        )
    path = " ".join(f"{x:.1f},{y:.1f}" for x, y, _ in line_points)
    dots = "".join(
        f'<g {chart_attrs(f"trend-profit-{i}", f"{years[i]} 归母净利润", value, "归母净利润趋势")}>'
        f'{svg_title(f"{years[i]} 归母净利润", value, "归母净利润趋势")}'
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
    sources = data.get("sources") if isinstance(data.get("sources"), dict) else {}

    def source_detail(key: str, extra: str = "") -> str:
        item = sources.get(key, {}) if isinstance(sources, dict) else {}
        if item.get("source") == "three_statements":
            detail = f"来源：合并现金流量表｜{item.get('row')}"
        elif item.get("source") == "metric_snapshot":
            detail = f"来源：指标快照｜{item.get('row') or key}"
        else:
            detail = "来源：未匹配到原始科目"
        return "；".join(part for part in [detail, extra] if part)

    items = []
    for key, label, color, extra in [
        ("operating", "经营现金流", "#10b981", ""),
        ("investing", "投资现金流", "#ef4444", ""),
        ("financing", "筹资现金流", "#f59e0b", ""),
        ("capex", "资本开支", "#8b5cf6", "按现金流出方向展示为负值"),
    ]:
        raw_value = data.get(key)
        if raw_value is None:
            continue
        value = -safe_float(raw_value) if key == "capex" else safe_float(raw_value)
        items.append((label, value, color, source_detail(key, extra)))
    if data.get("free_cash_flow") is not None:
        items.append(("自由现金流", safe_float(data.get("free_cash_flow")), "#06b6d4", "公式：经营现金流净额 - 资本开支；优先按原始三表现场重算"))
    if not items:
        return '<div class="chart-fallback-empty">现金流数据不足，未匹配到经营、投资、筹资或资本开支科目。</div>'
    left, right, top, bottom = 70, 24, 34, 78
    plot_w = width - left - right
    plot_h = height - top - bottom
    max_abs = max([abs(v) for _, v, _, _ in items] + [1])
    zero_y = top + plot_h / 2
    gap = plot_w / max(1, len(items))
    bar_w = min(58, gap * 0.48)
    rows = []
    for i, (label, value, color, detail) in enumerate(items):
        cx = left + gap * i + gap / 2
        bar_h = abs(value) / max_abs * (plot_h / 2 - 12)
        y = zero_y - bar_h if value >= 0 else zero_y
        rows.append(
            f'<g {chart_attrs(f"cashflow-{i}", label, value, detail)}>'
            f'{svg_title(label, value, detail)}'
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
            f'{svg_title(str(item.get("name") or ""), value, f"占比 {pct * 100:.1f}%")}'
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
  <circle cx="{cx}" cy="{cy}" r="54" fill="#ffffff" stroke="#dbe3ef"/>
  <text x="{cx}" y="{cy - 4}" text-anchor="middle" class="svg-label">合计</text>
  <text x="{cx}" y="{cy + 20}" text-anchor="middle" class="svg-value">{fmt_num(total, "亿元")}</text>
  {''.join(legend)}
</svg>
"""

def _radar_ring_points(cx: float, cy: float, radius: float, count: int) -> str:
    math = __import__("math")
    return " ".join(
        f"{cx + radius * math.cos((-90 + i * 360 / count) * math.pi / 180):.1f},{cy + radius * math.sin((-90 + i * 360 / count) * math.pi / 180):.1f}"
        for i in range(count)
    )

def svg_radar_chart(data: dict[str, Any] | None, *, width: int = 760, height: int = 380) -> str:
    if not data:
        return '<div class="chart-fallback-empty">雷达图数据不足，待补充完整比率口径。</div>'
    dimensions = data.get("dimensions") if isinstance(data.get("dimensions"), list) else []
    if not dimensions:
        net_margin = safe_float(data.get("net_margin"))
        asset_turnover = safe_float(data.get("asset_turnover"))
        equity_multiplier = safe_float(data.get("equity_multiplier"))
        roe = safe_float(data.get("roe"))
        dimensions = [
            {"name": "销售净利率", "raw_display": f"{net_margin:.2f}%", "score": max(0, min(100, (net_margin + 10) / 22 * 100)), "formula": "归母净利润 / 营业收入"},
            {"name": "资产周转率", "raw_display": f"{asset_turnover:.2f}x", "score": max(0, min(100, asset_turnover / 1.5 * 100)), "formula": "营业收入 / 资产总计"},
            {"name": "权益乘数", "raw_display": f"{equity_multiplier:.2f}x", "score": max(0, min(100, (equity_multiplier - 1) / 5 * 100)), "formula": "资产总计 / 归母权益"},
            {"name": "ROE", "raw_display": f"{roe:.2f}%", "score": max(0, min(100, (roe + 20) / 45 * 100)), "formula": "归母净利润 / 归母权益"},
        ]
    labels = [str(item.get("name") or "") for item in dimensions]
    raw_values = [str(item.get("raw_display") or "-") for item in dimensions]
    values = [max(0.0, min(100.0, safe_float(item.get("score")))) for item in dimensions]
    cx, cy, radius = 532.0, 195.0, 92.0
    math = __import__("math")
    axes: list[str] = []
    points: list[tuple[float, float]] = []
    for i, label in enumerate(labels):
        angle = -90 + i * 360 / len(labels)
        rad = angle * math.pi / 180
        ax = cx + radius * math.cos(rad)
        ay = cy + radius * math.sin(rad)
        axes.append(f'<line x1="{cx}" y1="{cy}" x2="{ax:.1f}" y2="{ay:.1f}" class="svg-grid"/>')
        cos_v = math.cos(rad)
        sin_v = math.sin(rad)
        label_x = cx + (radius + 46) * cos_v
        label_y = cy + (radius + 36) * sin_v
        if cos_v < -0.25:
            anchor = "end"
        elif cos_v > 0.25:
            anchor = "start"
        else:
            anchor = "middle"
        axes.append(
            f'<text x="{label_x:.1f}" y="{label_y - 8:.1f}" text-anchor="{anchor}" class="svg-label">{svg_text(label)}</text>'
            f'<text x="{label_x:.1f}" y="{label_y + 12:.1f}" text-anchor="{anchor}" class="svg-value" fill="#2563eb">{svg_text(raw_values[i])}</text>'
        )
        value_radius = radius * values[i] / 100
        points.append((cx + value_radius * math.cos(rad), cy + value_radius * math.sin(rad)))
    polygon = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    rings = "".join(f'<polygon points="{_radar_ring_points(cx, cy, radius * scale / 4, len(labels))}" fill="none" class="svg-grid"/>' for scale in range(1, 5))
    detail = " / ".join(f"{label}: {raw}（展示 {value:.1f}/100）" for label, raw, value in zip(labels, raw_values, values))
    card_colors = ["#2563eb", "#0891b2", "#7c3aed", "#dc2626"]
    cards: list[str] = []
    for i, item in enumerate(dimensions):
        x = 28
        y = 52 + i * 72
        score = values[i]
        color = card_colors[i % len(card_colors)]
        raw = raw_values[i]
        formula = str(item.get("formula") or "-")
        name = str(item.get("name") or labels[i])
        reference = item.get("reference_range") if isinstance(item.get("reference_range"), dict) else {}
        ref_text = ""
        if reference:
            ref_text = f"展示区间 {reference.get('low')} - {reference.get('high')} {item.get('unit') or ''}".strip()
        card_detail = "；".join(part for part in [formula, ref_text, f"标准化展示分 {score:.1f}/100"] if part)
        cards.append(
            f'<g {chart_attrs(f"dupont-dim-{i}", name, score, card_detail, raw)}>'
            f'{svg_title(name, score, card_detail, raw)}'
            f'<rect class="chart-mark" x="{x}" y="{y}" width="284" height="58" rx="8" fill="#ffffff" stroke="#dbe3ef"/>'
            f'<rect x="{x}" y="{y}" width="4" height="58" rx="2" fill="{color}"/>'
            f'<text x="{x + 16}" y="{y + 23}" class="svg-label" fill="#0f172a">{svg_text(name)}</text>'
            f'<text x="{x + 16}" y="{y + 45}" class="svg-muted">{svg_text(formula)}</text>'
            f'<text x="{x + 250}" y="{y + 24}" text-anchor="end" class="svg-value" fill="{color}">{svg_text(raw)}</text>'
            f'<rect x="{x + 172}" y="{y + 38}" width="78" height="5" rx="2.5" fill="#e2e8f0"/>'
            f'<rect x="{x + 172}" y="{y + 38}" width="{max(2, 78 * score / 100):.1f}" height="5" rx="2.5" fill="{color}" opacity="0.82"/>'
            f'<rect class="chart-hit" x="{x}" y="{y}" width="284" height="58" rx="8" fill="transparent"/>'
            f'</g>'
        )
    title_detail = "标准化展示分；原始值见标签与左侧卡片。"
    return f"""
<svg viewBox="0 0 {width} {height}" role="img" aria-label="杜邦分析雷达图">
  <rect x="0" y="0" width="{width}" height="{height}" fill="#ffffff"/>
  <text x="28" y="28" class="svg-label" fill="#0f172a">杜邦三因子分解</text>
  <text x="408" y="28" class="svg-label" fill="#0f172a">标准化雷达</text>
  <text x="28" y="350" class="svg-muted">雷达半径为 0-100 归一化展示分；展示分不等同于投资评级。</text>
  {''.join(cards)}
  <g transform="translate(0,0)">
    <polygon points="{_radar_ring_points(cx, cy, radius, len(labels))}" fill="rgba(37,99,235,0.035)" stroke="none"/>
    {rings}
    {''.join(axes)}
    <g {chart_attrs("dupont-radar", "杜邦分析", 0, detail, "综合比率")}>
      {svg_title("杜邦分析", 0, detail, "综合比率")}
      <polygon class="chart-mark" points="{polygon}" fill="rgba(37,99,235,0.20)" stroke="#2563eb" stroke-width="2.6" stroke-linejoin="round"/>
      <polygon class="chart-hit" points="{polygon}" fill="transparent" stroke="transparent" stroke-width="20"/>
    </g>
    {''.join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4.5" fill="#2563eb" stroke="#ffffff" stroke-width="1.4"/>' for x, y in points)}
  </g>
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
            f'{svg_title(str(step.get("name") or ""), value, f"base {base:.2f} 亿 / end {end_value:.2f} 亿")}'
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
    {svg_title("本公司", 0, company_detail, "评分雷达")}
    <polygon class="chart-mark" points="{points(company)}" fill="rgba(59,130,246,0.20)" stroke="#3b82f6" stroke-width="3"/>
    <polygon class="chart-hit" points="{points(company)}" fill="transparent" stroke="transparent" stroke-width="18"/>
  </g>
  <g {chart_attrs("peer-median", "行业中位数", 0, peer_detail, "评分雷达")}>
    {svg_title("行业中位数", 0, peer_detail, "评分雷达")}
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
            f'{svg_title(label, value, "偿债能力指标", fmt_num(value, unit))}'
            f'<rect class="chart-mark" x="{x}" y="36" width="138" height="120" rx="10" fill="#ffffff" stroke="#dbe3ef"/>'
            f'<rect x="{x + 18}" y="112" width="102" height="10" rx="5" fill="#e2e8f0"/>'
            f'<rect class="chart-mark" x="{x + 18}" y="112" width="{102 * pct:.1f}" height="10" rx="5" fill="{color}"/>'
            f'<rect class="chart-hit" x="{x}" y="36" width="138" height="120" rx="10" fill="transparent"/>'
            f'</g>'
            f'<text x="{x + 69}" y="82" text-anchor="middle" class="svg-value">{fmt_num(value, unit)}</text>'
            f'<text x="{x + 69}" y="144" text-anchor="middle" class="svg-label">{svg_text(label)}</text>'
        )
    return f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="偿债能力仪表盘">{"".join(cards)}</svg>'

__all__ = [
    "svg_text",
    "chart_attrs",
    "svg_title",
    "svg_income_bridge_chart",
    "svg_bar_line_chart",
    "svg_cashflow_chart",
    "svg_donut_chart",
    "_radar_ring_points",
    "svg_radar_chart",
    "svg_waterfall_chart",
    "svg_peer_radar_chart",
    "svg_solvency_gauges",
]
