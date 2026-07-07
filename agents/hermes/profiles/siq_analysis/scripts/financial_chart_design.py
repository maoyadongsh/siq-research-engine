#!/usr/bin/env python3
"""Deterministic financial chart design components for SIQ HTML reports.

This module owns advanced SVG chart geometry so report renderers do not need to
mix accounting extraction, layout rules, and visual paths in one large file.
"""

from __future__ import annotations

import html
from typing import Any


def safe_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return default


def svg_text(value: Any) -> str:
    return html.escape(str(value))


def fmt_yi(value: Any, suffix: str = "亿") -> str:
    if value is None:
        return "-"
    num = safe_float(value)
    if abs(num) >= 100:
        return f"{num:,.1f}{suffix}"
    return f"{num:,.2f}{suffix}"


def svg_title(title: str, value: Any, detail: str = "") -> str:
    parts = [str(title), fmt_yi(value)]
    if detail:
        parts.append(str(detail))
    return f"<title>{svg_text(' · '.join(part for part in parts if part))}</title>"


def ib_attrs(ib_id: str, related: list[str], title: str, value: Any, detail: str = "") -> str:
    aria = "，".join(part for part in [str(title), fmt_yi(value), str(detail)] if part)
    return (
        f'class="ib-interactive" tabindex="0" role="button" '
        f'data-ib-id="{svg_text(ib_id)}" data-related="{svg_text(",".join(related))}" '
        f'data-title="{svg_text(title)}" data-value="{svg_text(fmt_yi(value))}" data-detail="{svg_text(detail)}" '
        f'aria-label="{svg_text(aria)}"'
    )


def ribbon_path(
    x1: float,
    y1_top: float,
    y1_bottom: float,
    x2: float,
    y2_top: float,
    y2_bottom: float,
    bend: float = 0.42,
) -> str:
    safe_bend = max(0.18, min(0.48, bend))
    c1 = x1 + (x2 - x1) * safe_bend
    c2 = x2 - (x2 - x1) * safe_bend
    return (
        f"M{x1:.1f},{y1_top:.1f} "
        f"C{c1:.1f},{y1_top:.1f} {c2:.1f},{y2_top:.1f} {x2:.1f},{y2_top:.1f} "
        f"L{x2:.1f},{y2_bottom:.1f} "
        f"C{c2:.1f},{y2_bottom:.1f} {c1:.1f},{y1_bottom:.1f} {x1:.1f},{y1_bottom:.1f} Z"
    )


def render_ribbon_band(
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
    path = ribbon_path(x1, y1_top, y1_bottom, x2, y2_top, y2_bottom)
    ratio_attr = "" if ratio is None else f' data-ratio="{ratio:.6f}"'
    return (
        f'<g {ib_attrs(ib_id, related, title, value, detail)}{ratio_attr}>'
        f'{svg_title(title, value, detail)}'
        f'<path class="ib-hit" d="{path}" fill="transparent" stroke="transparent" stroke-width="14"/>'
        f'<path class="ib-flow ib-ribbon" d="{path}" fill="{color}" opacity="{opacity}" '
        f'stroke="rgba(255,255,255,0.58)" stroke-width="0.8"/>'
        f'</g>'
    )


def render_curve(
    ib_id: str,
    related: list[str],
    title: str,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    color: str,
    value: Any,
    width_px: float,
    opacity: float = 0.28,
    detail: str = "",
) -> str:
    c1 = x1 + (x2 - x1) * 0.42
    c2 = x2 - (x2 - x1) * 0.42
    return (
        f'<g {ib_attrs(ib_id, related, title, value, detail)}>'
        f'{svg_title(title, value, detail)}'
        f'<path class="ib-hit" d="M{x1:.1f},{y1:.1f} C{c1:.1f},{y1:.1f} {c2:.1f},{y2:.1f} {x2:.1f},{y2:.1f}" '
        f'fill="none" stroke="transparent" stroke-width="{max(width_px + 18, 26):.1f}" stroke-linecap="round"/>'
        f'<path class="ib-flow" d="M{x1:.1f},{y1:.1f} C{c1:.1f},{y1:.1f} {c2:.1f},{y2:.1f} {x2:.1f},{y2:.1f}" '
        f'fill="none" stroke="{color}" stroke-width="{width_px:.1f}" stroke-linecap="round" opacity="{opacity}"/>'
        f'</g>'
    )


def render_node(
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
        f'{svg_title(label, value, detail)}'
        f'<rect x="{x:.1f}" y="{y - h / 2:.1f}" width="12" height="{h:.1f}" rx="2" fill="{color}"/>'
        f'<text x="{tx:.1f}" y="{y - 7:.1f}" text-anchor="{text_anchor}" class="ib-label">{svg_text(label)}</text>'
        f'<text x="{tx:.1f}" y="{y + 19:.1f}" text-anchor="{text_anchor}" class="ib-value" fill="{value_color}">{svg_text(fmt_yi(value))}</text>'
        f'<rect class="ib-hit" x="{min(x, tx) - 10:.1f}" y="{y - h / 2 - 10:.1f}" width="{abs(tx - x) + 190:.1f}" height="{max(h + 20, 62):.1f}" rx="8" fill="transparent"/>'
        f'</g>'
    )


def _segment_detail(item: dict[str, Any]) -> str:
    parts: list[str] = []
    if item.get("share") is not None:
        parts.append(f"收入占比 {safe_float(item.get('share')):.2f}%")
    if item.get("revenue_yoy") is not None:
        parts.append(f"同比 {safe_float(item.get('revenue_yoy')):+.2f}%")
    if item.get("gross_margin") is not None:
        parts.append(f"毛利率 {safe_float(item.get('gross_margin')):.2f}%")
    return "；".join(parts) if parts else "收入分项"


def render_segment_label(item: dict[str, Any], x: float, y: float, ib_id: str, related: list[str]) -> str:
    yoy = item.get("revenue_yoy")
    yoy_text = "-" if yoy is None else f"{safe_float(yoy):+.2f}%"
    yoy_color = "#8a8f98" if yoy is None else "#cc5b24" if safe_float(yoy) >= 0 else "#1fb59d"
    name = str(item.get("name") or "")
    if len(name) > 13:
        name = name[:12] + "..."
    detail = _segment_detail(item)
    return (
        f'<g {ib_attrs(ib_id, related, str(item.get("name") or ""), item.get("revenue"), detail)}>'
        f'{svg_title(str(item.get("name") or ""), item.get("revenue"), detail)}'
        f'<text x="{x:.1f}" y="{y - 11:.1f}" text-anchor="end" class="ib-yoy" fill="{yoy_color}">{svg_text(yoy_text)}</text>'
        f'<text x="{x + 8:.1f}" y="{y - 11:.1f}" text-anchor="start" class="ib-label">{svg_text(name)}</text>'
        f'<text x="{x + 8:.1f}" y="{y + 15:.1f}" text-anchor="start" class="ib-value" fill="#3498db">{svg_text(fmt_yi(item.get("revenue")))}</text>'
        f'<rect class="ib-hit" x="{x - 118:.1f}" y="{y - 33:.1f}" width="224" height="58" rx="8" fill="transparent"/>'
        f'</g>'
    )


def _stack_segments(values: list[float], center: float, total_height: float, min_height: float = 9.0, gap: float = 3.0) -> list[tuple[float, float]]:
    total = sum(max(0.0, value) for value in values)
    if total <= 0:
        return []
    raw_heights = [max(min_height, value / total * (total_height - gap * max(0, len(values) - 1))) for value in values]
    scale = max(0.1, (total_height - gap * max(0, len(values) - 1)) / sum(raw_heights))
    heights = [height * scale for height in raw_heights]
    y = center - (sum(heights) + gap * max(0, len(values) - 1)) / 2
    bands: list[tuple[float, float]] = []
    for height in heights:
        bands.append((y, y + height))
        y += height + gap
    return bands


def render_income_bridge_svg(data: dict[str, Any] | None, *, width: int = 1280, height: int = 600) -> str:
    if not data or not data.get("flow_nodes"):
        return '<div class="chart-fallback-empty">利润桥数据不足，待补充利润表收入、成本费用和净利润口径。</div>'

    segments = [item for item in data.get("segments") or [] if isinstance(item, dict) and safe_float(item.get("revenue")) > 0]
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
    largest_adjustment = max(
        [item for item in adjustment_items if isinstance(item, dict)],
        key=lambda item: abs(safe_float(item.get("impact"))),
        default=None,
    )
    adjustment_detail = "期间费用、减值及其他经营项目影响"
    adjustment_label = "费用/减值/其他"
    if largest_adjustment:
        adjustment_detail = (
            f"最大影响项：{largest_adjustment.get('name')} {fmt_yi(largest_adjustment.get('value'))}，"
            f"对利润影响 {fmt_yi(-safe_float(largest_adjustment.get('impact')))}"
        )
        adjustment_label = str(largest_adjustment.get("name") or adjustment_label)

    income_blue = "#35a9f4"
    income_blue_soft = "#bfe4fb"
    expense_yellow = "#f2c400"
    expense_soft = "#ffeaa3"
    profit_red = "#ff3548"
    profit_soft = "#ffb6bb"

    text_css = """
  <style>
    .ib-label { font: 720 17px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; fill: #111827; }
    .ib-value { font: 760 22px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; font-variant-numeric: tabular-nums; }
    .ib-yoy { font: 720 15px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; font-variant-numeric: tabular-nums; }
    .ib-muted { font: 500 17px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; fill: #8a8f98; }
    .ib-caption { font: 600 12px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; fill: #94a3b8; }
    .ib-ribbon { vector-effect: non-scaling-stroke; }
  </style>
"""

    left_label_x = 284
    segment_node_x = 390
    segment_out_x = 424
    collector_x = 516
    collector_out_x = 542
    revenue_x, revenue_y = 646, 330
    split_x = 824
    revenue_h = 224.0
    revenue_top = revenue_y - revenue_h / 2
    revenue_bottom = revenue_y + revenue_h / 2
    op_x, op_y = 936, 330
    pretax_x, pretax_y = 1048, 330
    right_x = 1166

    segment_values = [safe_float(item.get("revenue")) for item in segments]
    label_top = 150 if len(segments) >= 5 else 176
    label_bottom = 468 if len(segments) >= 5 else 430
    label_step = (label_bottom - label_top) / max(1, len(segments) - 1) if len(segments) > 1 else 0
    collector_top = revenue_top + 20
    collector_bottom = revenue_bottom - 20
    collector_h = collector_bottom - collector_top
    segment_bands = _stack_segments(segment_values, revenue_y, collector_h, min_height=10.0, gap=5.0)
    if len(segment_bands) != len(segments):
        lane_step = collector_h / max(1, len(segments) - 1) if len(segments) > 1 else 0
        segment_bands = [(collector_top + i * lane_step - 5, collector_top + i * lane_step + 5) for i in range(len(segments))]

    segment_parts: list[str] = []
    for i, item in enumerate(segments):
        value = safe_float(item.get("revenue"))
        label_y = label_top + i * label_step
        band_top, band_bottom = segment_bands[i]
        band_y = (band_top + band_bottom) / 2
        segment_id = f"seg-{i}"
        node_id = f"node-{segment_id}"
        flow_id = f"flow-{segment_id}-revenue"
        detail_parts = ["收入分项汇入营业总收入"]
        if item.get("share") is not None:
            detail_parts.append(f"占营业总收入 {safe_float(item.get('share')):.2f}%")
        if item.get("gross_margin") is not None:
            detail_parts.append(f"分项毛利率 {safe_float(item.get('gross_margin')):.2f}%")
        flow_detail = "；".join(detail_parts)
        # Keep the left port visually stable. The central stack still carries
        # proportional width, while each segment enters the canvas as the same
        # closed-ribbon family instead of mixing giant lobes with thin strokes.
        share = value / total_revenue if total_revenue else 0.0
        port_h = max(10.0, min(24.0, (share ** 0.5) * 30.0)) if share > 0 else 10.0
        lane_h = max(8.0, band_bottom - band_top)
        segment_parts.append(render_segment_label(item, left_label_x, label_y, segment_id, [flow_id, "node-revenue"]))
        segment_parts.append(
            f'<g {ib_attrs(node_id, [segment_id, flow_id, "node-revenue"], str(item.get("name") or ""), value, "收入分项")}>'
            f'{svg_title(str(item.get("name") or ""), value, "收入分项")}'
            f'<rect x="{segment_node_x}" y="{label_y - 12:.1f}" width="12" height="24" rx="2" fill="{income_blue}"/>'
            f'<rect class="ib-hit" x="{segment_node_x - 12}" y="{label_y - 22:.1f}" width="38" height="44" rx="8" fill="transparent"/>'
            f'</g>'
        )
        segment_parts.append(
            render_ribbon_band(
                flow_id,
                [segment_id, node_id, "node-revenue"],
                f"{item.get('name')} -> 营业总收入",
                segment_out_x,
                label_y - port_h / 2,
                label_y + port_h / 2,
                collector_x,
                band_y - lane_h / 2,
                band_y + lane_h / 2,
                income_blue_soft,
                value,
                0.54,
                flow_detail,
                share if total_revenue else None,
            )
        )

    collector_node = (
        f'<g {ib_attrs("node-income-collector", ["node-revenue"], "收入构成汇流槽", total_revenue, "各业务收入按比例汇总后进入营业总收入")}> '
        f'{svg_title("收入构成汇流槽", total_revenue, "各业务收入按比例汇总后进入营业总收入")}'
        f'<rect x="{collector_x:.1f}" y="{collector_top:.1f}" width="14" height="{collector_h:.1f}" rx="3" fill="{income_blue}" opacity="0.88"/>'
        f'<rect class="ib-hit" x="{collector_x - 10:.1f}" y="{collector_top - 10:.1f}" width="40" height="{collector_h + 20:.1f}" rx="8" fill="transparent"/>'
        f'</g>'
    )
    collector_flow = render_ribbon_band(
        "flow-collector-revenue",
        ["node-income-collector", "node-revenue"],
        "收入构成汇总 -> 营业总收入",
        collector_out_x,
        collector_top,
        collector_bottom,
        revenue_x,
        revenue_top,
        revenue_bottom,
        income_blue_soft,
        total_revenue,
        0.44,
        "所有收入分项汇总至营业总收入，宽度按总收入展示",
        1.0 if total_revenue else None,
    )

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

    max_value = max(
        [abs(safe_float(v)) for v in [total_revenue, operating_cost, gross_profit, operating_adjustments, operating_profit, pretax_profit, income_tax, attribution, parent_net_profit]]
        + segment_values
        + [1.0]
    )

    def flow_width(value: Any, minimum: float = 3.5, maximum: float = 52.0) -> float:
        return max(minimum, min(maximum, abs(safe_float(value)) / max_value * maximum))

    center_parts = [
        render_node("node-revenue", ["flow-collector-revenue", "flow-revenue-gross", "flow-revenue-cost"], revenue_x, revenue_y, revenue_h, income_blue, nodes.get("revenue", {}).get("name") or "营业收入", total_revenue, "start", income_blue, "收入分项汇总"),
        render_ribbon_band("flow-revenue-gross", ["node-revenue", "node-gross"], "营业收入 -> 毛利", revenue_x + 12, gross_top, gross_bottom, split_x, gross_top, gross_bottom, profit_soft, gross_profit, 0.62, f"收入扣除营业成本后的毛利，占营业收入 {gross_ratio * 100:.2f}%", gross_ratio),
        render_ribbon_band("flow-revenue-cost", ["node-revenue", "node-cost"], "营业收入 -> 营业成本", revenue_x + 12, cost_top, cost_bottom, split_x, cost_top, cost_bottom, expense_soft, operating_cost, 0.72, f"营业成本流出，占营业收入 {cost_ratio * 100:.2f}%", cost_ratio),
        render_node("node-cost", ["flow-revenue-cost", "flow-cost-op"], split_x, cost_y, max(cost_h, 12), expense_yellow, "营业成本", operating_cost, "start", expense_yellow, "利润表营业成本"),
        render_node("node-gross", ["flow-revenue-gross", "flow-gross-op"], split_x, gross_y, max(gross_h, 12), profit_red, "毛利", gross_profit, "start", profit_red, "营业收入减营业成本"),
        render_curve("flow-gross-op", ["node-gross", "node-operating-profit"], "毛利 -> 营业利润", split_x + 12, gross_y, op_x, op_y - 30, profit_soft, gross_profit, flow_width(gross_profit), 0.64, "毛利经过期间费用和减值抵减后形成营业利润/亏损"),
        render_curve("flow-cost-op", ["node-cost", "node-operating-profit"], f"{adjustment_label} -> 营业利润", split_x + 12, cost_y, op_x, op_y + 28, expense_soft, operating_adjustments, flow_width(operating_adjustments), 0.54, adjustment_detail),
        render_node("node-operating-profit", ["flow-gross-op", "flow-cost-op", "flow-op-pretax"], op_x, op_y, 82, profit_red, nodes.get("operating_profit", {}).get("name") or "营业利润", operating_profit, "start", profit_red, "合并利润表营业利润"),
        render_curve("flow-op-pretax", ["node-operating-profit", "node-pretax"], "营业利润 -> 利润总额", op_x + 12, op_y, pretax_x, pretax_y, profit_soft, pretax_profit, flow_width(pretax_profit), 0.68, "营业外收支后形成利润总额"),
        render_node("node-pretax", ["flow-op-pretax", "flow-pretax-parent", "flow-pretax-tax", "flow-pretax-other"], pretax_x, pretax_y, 72, profit_red, nodes.get("pretax_profit", {}).get("name") or "利润总额", pretax_profit, "start", profit_red, "合并利润表利润总额"),
        render_curve("flow-pretax-parent", ["node-pretax", "node-parent-profit"], "利润总额 -> 归母净利润", pretax_x + 12, pretax_y - 6, right_x, 236, profit_soft, parent_net_profit, flow_width(parent_net_profit), 0.72, "扣除所得税和归属调整后的归母口径"),
        render_curve("flow-pretax-tax", ["node-pretax", "node-tax"], "利润总额 -> 所得税", pretax_x + 12, pretax_y + 12, right_x, 328, expense_soft, income_tax, flow_width(income_tax), 0.70, "所得税费用"),
        render_curve("flow-pretax-other", ["node-pretax", "node-other"], "利润总额 -> 其他", pretax_x + 12, pretax_y + 24, right_x, 404, expense_soft, attribution, flow_width(attribution), 0.42, "净利润与归母口径之间的归属调整"),
        render_node("node-parent-profit", ["flow-pretax-parent"], right_x, 236, 62, profit_red, nodes.get("parent_net_profit", {}).get("name") or "归母净利润", parent_net_profit, "start", profit_red, "最终归母口径"),
        render_node("node-tax", ["flow-pretax-tax"], right_x, 328, 18, expense_yellow, "所得税", income_tax, "start", expense_yellow, "所得税费用"),
        render_node("node-other", ["flow-pretax-other"], right_x, 404, 16, expense_yellow, "其他", attribution, "start", expense_yellow, "少数股东损益或口径调整"),
    ]

    return f"""
<svg viewBox="0 0 {width} {height}" role="img" aria-label="收支拆解利润桥">
  {text_css}
  <rect x="0" y="0" width="{width}" height="{height}" fill="#ffffff"/>
  <text x="{left_label_x - 8}" y="42" text-anchor="start" class="ib-caption">收入构成</text>
  <text x="{collector_x - 6}" y="42" text-anchor="start" class="ib-caption">收入汇流</text>
  <text x="{revenue_x - 18}" y="42" text-anchor="start" class="ib-caption">收入汇总</text>
  <text x="{split_x}" y="42" text-anchor="start" class="ib-caption">成本/毛利拆分</text>
  <text x="{op_x}" y="42" text-anchor="start" class="ib-caption">利润形成</text>
  {''.join(segment_parts)}
  {collector_node}
  {collector_flow}
  {''.join(center_parts)}
  <text x="{revenue_x - 80}" y="{revenue_y - 126}" class="ib-muted">{svg_text(data.get('period_label') or '')}</text>
</svg>
"""
