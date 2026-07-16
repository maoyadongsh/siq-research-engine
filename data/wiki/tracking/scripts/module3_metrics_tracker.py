#!/usr/bin/env python3
"""
模块3: 指标追踪器
追踪季度财报指标变化，计算环比/同比/偏离度，输出指标追踪面板。
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_SCRIPT_PATH = Path(__file__).resolve()
_PROJECT_ROOT = _SCRIPT_PATH.parents[4]
DEFAULT_WIKI_BASE = str(Path(
    os.environ.get("SIQ_WIKI_ROOT")
    or os.environ.get("WIKI_ROOT")
    or _SCRIPT_PATH.parents[2]
).expanduser().resolve())
WIKISET_DIR = Path(
    os.environ.get("SIQ_WIKISET_ROOT")
    or os.environ.get("WIKISET_ROOT")
    or _PROJECT_ROOT / "scripts" / "wiki" / "wikiset"
).expanduser().resolve()
if str(WIKISET_DIR) not in sys.path:
    sys.path.insert(0, str(WIKISET_DIR))

from company_identity import company_dir_path
from local_citations import format_ref_summary, resolve_key_metric_refs


# 核心财务指标定义
CORE_METRICS = {
    "operating_revenue": {"name": "营业收入", "unit": "元", "direction": "up", "display_unit": "亿元"},
    "net_profit": {"name": "归母净利润", "unit": "元", "direction": "up", "display_unit": "亿元"},
    "parent_net_profit": {"name": "归母净利润", "unit": "元", "direction": "up", "display_unit": "亿元"},
    "gross_profit_margin": {"name": "毛利率", "unit": "%", "direction": "up"},
    "net_profit_margin": {"name": "净利率", "unit": "%", "direction": "up"},
    "roe": {"name": "净资产收益率ROE", "unit": "%", "direction": "up"},
    "roa": {"name": "总资产收益率ROA", "unit": "%", "direction": "up"},
    "debt_ratio": {"name": "资产负债率", "unit": "%", "direction": "down"},
    "current_ratio": {"name": "流动比率", "unit": "倍", "direction": "up"},
    "quick_ratio": {"name": "速动比率", "unit": "倍", "direction": "up"},
    "inventory_turnover": {"name": "存货周转率", "unit": "次", "direction": "up"},
    "receivable_turnover": {"name": "应收账款周转率", "unit": "次", "direction": "up"},
    "cash_flow_operating": {"name": "经营活动现金流", "unit": "元", "direction": "up", "display_unit": "亿元"},
    "operating_cash_flow_net": {"name": "经营活动现金流", "unit": "元", "direction": "up", "display_unit": "亿元"},
    "eps": {"name": "每股收益EPS", "unit": "元", "direction": "up"},
    "basic_eps": {"name": "每股收益EPS", "unit": "元", "direction": "up"},
}

AMOUNT_METRICS = {
    "operating_revenue",
    "net_profit",
    "parent_net_profit",
    "cash_flow_operating",
    "operating_cash_flow_net",
}


def _to_number(value):
    """尽量把财务 JSON 中的值转换为数字，失败则保留原值。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


def format_metric_value(value, canonical: str, source_unit: str, config: Dict) -> str:
    """按来源单位生成可读展示，避免把元误写成百万元。"""
    number = _to_number(value)
    if not isinstance(number, float):
        return f"{value} {source_unit or config.get('unit', '')}".strip()

    unit = source_unit or config.get("unit", "")
    if canonical in AMOUNT_METRICS:
        if unit in {"元", "人民币元", "CNY", "RMB"}:
            return f"{number / 100_000_000:,.2f} 亿元"
        if unit == "万元":
            return f"{number / 10_000:,.2f} 亿元"
        if unit == "百万元":
            return f"{number / 100:,.2f} 亿元"
        if unit == "亿元":
            return f"{number:,.2f} 亿元"

    if unit == "%":
        return f"{number:,.2f}%"
    if unit:
        return f"{number:,.2f} {unit}"
    return f"{number:,.2f}"


def load_metrics(metrics_path: str) -> Dict:
    """加载指标数据"""
    with open(metrics_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def calculate_changes(values: Dict[str, float]) -> Dict:
    """
    计算指标变化率

    输入: {year: value, ...}
    输出: {
        "yoy": {year: change_pct, ...},  # 同比
        "qoq": {year: change_pct, ...},  # 环比 (如有多季度数据)
        "cagr": float,  # 复合年增长率
        "latest_yoy": float,  # 最新同比
        "latest_value": float,  # 最新值
        "previous_value": float,  # 上期值
    }
    """
    years = sorted(values.keys())
    if len(years) < 2:
        return {}

    result = {
        "yoy": {},
        "latest_yoy": None,
        "latest_value": values[years[-1]],
        "previous_value": values[years[-2]],
    }

    # 计算同比
    for i in range(1, len(years)):
        year = years[i]
        prev_year = years[i-1]
        curr = values[year]
        prev = values[prev_year]

        if prev and prev != 0:
            yoy = (curr - prev) / abs(prev) * 100
            result["yoy"][year] = round(yoy, 2)
        else:
            result["yoy"][year] = None

    result["latest_yoy"] = result["yoy"].get(years[-1])

    # 计算 CAGR (复合年增长率)
    if len(years) >= 2:
        first_year = int(years[0])
        last_year = int(years[-1])
        first_val = values[years[0]]
        last_val = values[years[-1]]

        if first_val and first_val > 0 and last_val and last_val > 0 and last_year > first_year:
            n = last_year - first_year
            cagr = ((last_val / first_val) ** (1/n) - 1) * 100
            result["cagr"] = round(cagr, 2)
        else:
            # 指标跨正负号或包含零值时 CAGR 没有稳定经济含义，避免生成复数或误导结论。
            result["cagr"] = None

    return result


def calculate_deviation(current: float, benchmark: float) -> float:
    """计算偏离度 (与行业均值或历史均值比较)"""
    if benchmark and benchmark != 0:
        return round((current - benchmark) / abs(benchmark) * 100, 2)
    return None


def assess_trend(changes: Dict, metric_config: Dict) -> str:
    """评估指标趋势"""
    latest_yoy = changes.get("latest_yoy")
    if latest_yoy is None:
        return "unknown"

    direction = metric_config.get("direction", "up")

    # 根据方向判断好坏
    if direction == "up":
        if latest_yoy > 10:
            return "strong_positive"
        elif latest_yoy > 0:
            return "positive"
        elif latest_yoy > -10:
            return "negative"
        else:
            return "strong_negative"
    else:  # direction == "down"
        if latest_yoy < -10:
            return "strong_positive"
        elif latest_yoy < 0:
            return "positive"
        elif latest_yoy < 10:
            return "negative"
        else:
            return "strong_negative"


def generate_metrics_panel(
    stock_code: str,
    company_name: str,
    metrics_path: str,
    output_dir: str,
    company_dir: str = None,
    period: str = None,
) -> str:
    """
    生成指标追踪面板

    输出：wiki/tracking/<stock_code>-<company>/metrics/<period>.md
    """
    if period is None:
        # 自动推断最新季度/年度
        now = datetime.now()
        quarter = (now.month - 1) // 3 + 1
        if quarter == 1:
            period = f"{now.year - 1}-Q4"
        else:
            period = f"{now.year}-Q{quarter - 1}"

    os.makedirs(output_dir, exist_ok=True)

    # 加载数据
    metrics_data = load_metrics(metrics_path)
    data_list = metrics_data.get("data", [])

    # 构建指标追踪结果
    tracked_metrics = []

    for metric in data_list:
        canonical = metric.get("canonical_name", "")
        if canonical not in CORE_METRICS:
            continue

        config = CORE_METRICS[canonical]
        values = metric.get("values", {})
        source_unit = metric.get("unit") or config.get("unit", "")

        if len(values) < 2:
            continue

        # 计算变化
        changes = calculate_changes(values)
        if not changes:
            continue

        latest_year = sorted(values.keys(), reverse=True)[0]
        source_refs = []
        if company_dir:
            source_refs = resolve_key_metric_refs(Path(company_dir), metric.get("name") or canonical, latest_year)

        trend = assess_trend(changes, config)

        tracked = {
            "canonical_name": canonical,
            "name": config["name"],
            "unit": source_unit,
            "display_unit": config.get("display_unit", source_unit),
            "direction": config["direction"],
            "values": values,
            "changes": changes,
            "trend": trend,
            "latest_value": changes["latest_value"],
            "previous_value": changes["previous_value"],
            "latest_yoy": changes.get("latest_yoy"),
            "cagr": changes.get("cagr"),
            "source_refs": source_refs,
        }
        tracked_metrics.append(tracked)

    # 生成 Markdown
    output_path = os.path.join(output_dir, f"{period}.md")

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(f"# {company_name} ({stock_code}) 指标追踪面板\n\n")
        f.write(f"> 报告期: {period}\n")
        f.write(f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")

        # 摘要
        f.write("## 指标摘要\n\n")

        positive_count = sum(1 for m in tracked_metrics if m["trend"] in ["positive", "strong_positive"])
        negative_count = sum(1 for m in tracked_metrics if m["trend"] in ["negative", "strong_negative"])

        f.write(f"- **正向指标**: {positive_count} 项\n")
        f.write(f"- **负向指标**: {negative_count} 项\n")
        f.write(f"- **追踪指标数**: {len(tracked_metrics)} 项\n\n")

        # 关键指标概览表
        f.write("## 关键指标概览\n\n")
        f.write("| 指标 | 最新值 | 上期值 | 同比变动 | CAGR | 趋势 |\n")
        f.write("|------|--------|--------|----------|------|------|\n")

        trend_emojis = {
            "strong_positive": "🟢🟢",
            "positive": "🟢",
            "negative": "🟡",
            "strong_negative": "🔴",
            "unknown": "⚪",
        }

        for m in tracked_metrics:
            latest = m["latest_value"]
            previous = m["previous_value"]
            yoy = m.get("latest_yoy")
            cagr = m.get("cagr")
            trend = m["trend"]

            latest_str = format_metric_value(latest, m["canonical_name"], m["unit"], CORE_METRICS[m["canonical_name"]])
            prev_str = format_metric_value(previous, m["canonical_name"], m["unit"], CORE_METRICS[m["canonical_name"]])
            yoy_str = f"{yoy:+.2f}%" if yoy is not None else "N/A"
            cagr_str = f"{cagr:+.2f}%" if cagr is not None else "N/A"
            emoji = trend_emojis.get(trend, "⚪")

            f.write(f"| {m['name']} | {latest_str} | {prev_str} | {yoy_str} | {cagr_str} | {emoji} |\n")

        f.write("\n")

        # 详细分析
        f.write("## 详细分析\n\n")

        for m in tracked_metrics:
            emoji = trend_emojis.get(m["trend"], "⚪")
            f.write(f"### {emoji} {m['name']}\n\n")

            # 历史数据表
            f.write("**历史数据**:\n\n")
            f.write("| 年度 | 数值 | 同比变动 |\n")
            f.write("|------|------|----------|\n")

            years = sorted(m["values"].keys(), reverse=True)
            for year in years:
                val = m["values"][year]
                yoy = m["changes"].get("yoy", {}).get(year)
                val_str = format_metric_value(val, m["canonical_name"], m["unit"], CORE_METRICS[m["canonical_name"]])
                yoy_str = f"{yoy:+.2f}%" if yoy is not None else "N/A"
                f.write(f"| {year} | {val_str} | {yoy_str} |\n")

            f.write("\n")

            # 趋势解读
            latest_yoy = m.get("latest_yoy")
            if latest_yoy is not None:
                if m["trend"] == "strong_positive":
                    f.write(f"✅ **趋势解读**: {m['name']}同比大幅增长 {latest_yoy:+.2f}%，表现优异。\n\n")
                elif m["trend"] == "positive":
                    f.write(f"✅ **趋势解读**: {m['name']}同比增长 {latest_yoy:+.2f}%，趋势向好。\n\n")
                elif m["trend"] == "negative":
                    f.write(f"⚠️ **趋势解读**: {m['name']}同比下降 {abs(latest_yoy):.2f}%，需关注。\n\n")
                elif m["trend"] == "strong_negative":
                    f.write(f"🔴 **趋势解读**: {m['name']}同比大幅下降 {abs(latest_yoy):.2f}%，存在风险。\n\n")

            source_refs = m.get("source_refs") or []
            if source_refs:
                f.write(f"**来源**: {format_ref_summary(source_refs[:3])}\n")
                for ref in source_refs[:3]:
                    links = []
                    if ref.get("open_pdf_page_url"):
                        links.append(f"[打开PDF页]({ref['open_pdf_page_url']})")
                    if ref.get("open_source_page_url"):
                        links.append(f"[查看页来源]({ref['open_source_page_url']})")
                    if ref.get("open_source_table_url"):
                        links.append(f"[查看表格]({ref['open_source_table_url']})")
                    if links:
                        f.write("- " + "，".join(links) + "\n")
                f.write("\n")
            else:
                f.write("**来源**: PDF 页码未返回/证据链不完整\n\n")

            f.write("---\n\n")

        # 原始数据附录
        f.write("## 原始数据\n\n")
        f.write("```json\n")
        f.write(json.dumps(tracked_metrics, ensure_ascii=False, indent=2))
        f.write("\n```\n")

    print(f"✅ 指标追踪面板已生成: {output_path}")
    print(f"   共追踪 {len(tracked_metrics)} 项指标")
    return output_path


def run_metrics_tracker(
    stock_code: str,
    company_name: str,
    wiki_base: str = DEFAULT_WIKI_BASE,
    period: str = None,
    use_search: bool = False,
) -> str:
    """
    主入口：运行指标追踪

    输入：wiki/companies/<stock_code>-<company>/metrics/key_metrics.json
    输出：wiki/tracking/<stock_code>-<company>/metrics/<period>.md

    Args:
        use_search: 是否使用网络搜索补充行业对比数据
    """
    company_dir = str(company_dir_path(wiki_base, stock_code, company_name))
    metrics_path = os.path.join(company_dir, "metrics", "key_metrics.json")

    tracking_dir = os.path.join(company_dir, "tracking")
    metrics_dir = os.path.join(tracking_dir, "metrics")

    if not os.path.exists(metrics_path):
        print(f"❌ 指标数据不存在: {metrics_path}")
        return None

    # 如果使用搜索，尝试获取行业对比数据
    if use_search:
        try:
            from search_tools import SearchTools
            search = SearchTools()
            availability = search.check_availability()

            if availability.get("any"):
                print(f"🔍 搜索行业对比数据...")
                # 这里可以添加行业对比数据的获取逻辑
                # 例如：搜索行业平均毛利率、ROE 等
                pass
        except ImportError:
            print("⚠️ search_tools 模块未找到，跳过行业数据搜索")

    return generate_metrics_panel(stock_code, company_name, metrics_path, metrics_dir, company_dir, period)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="指标追踪器")
    parser.add_argument("--stock", required=True, help="股票代码")
    parser.add_argument("--company", required=True, help="公司简称")
    parser.add_argument("--period", help="报告期 (如 2025-Q1)")
    parser.add_argument("--wiki-base", default=DEFAULT_WIKI_BASE, help="wiki 根目录")
    args = parser.parse_args()

    run_metrics_tracker(args.stock, args.company, args.wiki_base, args.period)
