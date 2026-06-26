"""
模块3: 指标追踪器

追踪季度财报指标变化
计算环比/同比/偏离度
输出：指标追踪面板
"""

from datetime import datetime
from typing import Optional

from agents.tracking.schemas import MetricTrackingPanel


# 核心财务指标定义
CORE_METRICS = [
    {"name": "营业收入", "unit": "亿元", "category": "盈利能力"},
    {"name": "归母净利润", "unit": "亿元", "category": "盈利能力"},
    {"name": "毛利率", "unit": "%", "category": "盈利能力"},
    {"name": "净利率", "unit": "%", "category": "盈利能力"},
    {"name": "ROE", "unit": "%", "category": "盈利能力"},
    {"name": "资产负债率", "unit": "%", "category": "偿债能力"},
    {"name": "流动比率", "unit": "倍", "category": "偿债能力"},
    {"name": "速动比率", "unit": "倍", "category": "偿债能力"},
    {"name": "经营现金流", "unit": "亿元", "category": "现金流"},
    {"name": "自由现金流", "unit": "亿元", "category": "现金流"},
    {"name": "应收账款周转天数", "unit": "天", "category": "营运能力"},
    {"name": "存货周转天数", "unit": "天", "category": "营运能力"},
    {"name": "总资产周转率", "unit": "次", "category": "营运能力"},
    {"name": "研发费用率", "unit": "%", "category": "成长能力"},
    {"name": "营收增长率", "unit": "%", "category": "成长能力"},
    {"name": "净利润增长率", "unit": "%", "category": "成长能力"},
]

# 偏离度阈值
DEVIATION_THRESHOLDS = {
    "INFO": 10,
    "WATCH": 20,
    "WARNING": 30,
    "CRITICAL": 50,
}


class MetricTracker:
    """指标追踪器"""

    def __init__(self):
        self.core_metrics = CORE_METRICS
        self.thresholds = DEVIATION_THRESHOLDS

    def track_metrics(
        self,
        stock_code: str,
        company_name: str,
        report_period: str,
        current_data: dict[str, float],
        previous_data: Optional[dict[str, float]] = None,
        year_ago_data: Optional[dict[str, float]] = None,
        industry_avg: Optional[dict[str, float]] = None,
    ) -> MetricTrackingPanel:
        """
        追踪指标变化

        Args:
            current_data: 当期指标数据 {metric_name: value}
            previous_data: 上期数据（用于环比）
            year_ago_data: 去年同期数据（用于同比）
            industry_avg: 行业平均值（用于偏离度）

        Returns:
            MetricTrackingPanel
        """
        metrics = []
        abnormal_flags = []

        for metric_def in self.core_metrics:
            name = metric_def["name"]
            unit = metric_def["unit"]
            category = metric_def["category"]

            if name not in current_data:
                continue

            current = current_data[name]

            # 计算环比
            qoq = None
            if previous_data and name in previous_data and previous_data[name] != 0:
                qoq = round((current - previous_data[name]) / abs(previous_data[name]) * 100, 2)

            # 计算同比
            yoy = None
            if year_ago_data and name in year_ago_data and year_ago_data[name] != 0:
                yoy = round((current - year_ago_data[name]) / abs(year_ago_data[name]) * 100, 2)

            # 计算偏离度
            deviation = None
            if industry_avg and name in industry_avg and industry_avg[name] != 0:
                deviation = round((current - industry_avg[name]) / abs(industry_avg[name]) * 100, 2)

            metric_entry = {
                "name": name,
                "unit": unit,
                "category": category,
                "current_value": current,
                "previous_value": previous_data.get(name) if previous_data else None,
                "year_ago_value": year_ago_data.get(name) if year_ago_data else None,
                "industry_avg": industry_avg.get(name) if industry_avg else None,
                "qoq_change": qoq,
                "yoy_change": yoy,
                "deviation": deviation,
            }
            metrics.append(metric_entry)

            # 检测异常
            flag = self._detect_abnormal(name, current, qoq, yoy, deviation)
            if flag:
                abnormal_flags.append(flag)

        # 汇总变化
        changes_summary = self._summarize_changes(metrics)

        # 生成Markdown
        markdown = self._render_markdown(
            stock_code, company_name, report_period, metrics, abnormal_flags
        )

        return MetricTrackingPanel(
            stock_code=stock_code,
            company_name=company_name,
            report_period=report_period,
            metrics=metrics,
            changes_summary=changes_summary,
            abnormal_flags=abnormal_flags,
            markdown=markdown,
        )

    def _detect_abnormal(
        self, name: str, current: float, qoq: Optional[float],
        yoy: Optional[float], deviation: Optional[float]
    ) -> Optional[dict]:
        """检测异常指标"""
        alerts = []

        # 环比异常
        if qoq is not None and abs(qoq) > self.thresholds["WARNING"]:
            level = "WARNING" if abs(qoq) > self.thresholds["CRITICAL"] else "WATCH"
            alerts.append({
                "type": "环比大幅波动",
                "value": qoq,
                "level": level,
            })

        # 同比异常
        if yoy is not None and abs(yoy) > self.thresholds["WARNING"]:
            level = "WARNING" if abs(yoy) > self.thresholds["CRITICAL"] else "WATCH"
            alerts.append({
                "type": "同比大幅波动",
                "value": yoy,
                "level": level,
            })

        # 偏离度异常
        if deviation is not None and abs(deviation) > self.thresholds["INFO"]:
            level = "INFO"
            for lvl, threshold in self.thresholds.items():
                if abs(deviation) >= threshold:
                    level = lvl
            alerts.append({
                "type": "偏离行业均值",
                "value": deviation,
                "level": level,
            })

        if alerts:
            return {
                "metric": name,
                "current_value": current,
                "alerts": alerts,
            }
        return None

    def _summarize_changes(self, metrics: list[dict]) -> dict:
        """汇总变化趋势"""
        improving = 0
        deteriorating = 0
        stable = 0

        for m in metrics:
            yoy = m.get("yoy_change")
            if yoy is None:
                continue

            # 根据指标类型判断好坏
            positive_metrics = ["毛利率", "净利率", "ROE", "经营现金流", "自由现金流",
                              "流动比率", "速动比率", "总资产周转率", "研发费用率",
                              "营收增长率", "净利润增长率"]
            negative_metrics = ["资产负债率", "应收账款周转天数", "存货周转天数"]

            if m["name"] in positive_metrics:
                if yoy > 5:
                    improving += 1
                elif yoy < -5:
                    deteriorating += 1
                else:
                    stable += 1
            elif m["name"] in negative_metrics:
                if yoy < -5:
                    improving += 1
                elif yoy > 5:
                    deteriorating += 1
                else:
                    stable += 1
            else:
                stable += 1

        total = improving + deteriorating + stable
        return {
            "improving": improving,
            "deteriorating": deteriorating,
            "stable": stable,
            "total_analyzed": total,
            "trend": "改善" if improving > deteriorating else "恶化" if deteriorating > improving else "平稳",
        }

    def _render_markdown(
        self, stock_code: str, company_name: str, report_period: str,
        metrics: list[dict], abnormal_flags: list[dict]
    ) -> str:
        """渲染指标追踪面板Markdown"""
        md = f"""# 指标追踪面板 - {company_name} ({stock_code})

**报告期**: {report_period}
**生成时间**: {datetime.now().strftime("%Y-%m-%d %H:%M")}

---

## 核心指标追踪

| 指标 | 单位 | 当期值 | 环比 | 同比 | 行业偏离 |
|------|------|--------|------|------|----------|
"""
        for m in metrics:
            qoq_str = f"{m['qoq_change']:+.1f}%" if m['qoq_change'] is not None else "-"
            yoy_str = f"{m['yoy_change']:+.1f}%" if m['yoy_change'] is not None else "-"
            dev_str = f"{m['deviation']:+.1f}%" if m['deviation'] is not None else "-"
            md += f"| {m['name']} | {m['unit']} | {m['current_value']} | {qoq_str} | {yoy_str} | {dev_str} |\n"

        if abnormal_flags:
            md += "\n---\n\n## ⚠️ 异常指标预警\n\n"
            for flag in abnormal_flags:
                md += f"### {flag['metric']} (当前值: {flag['current_value']})\n\n"
                for alert in flag['alerts']:
                    emoji = {"INFO": "ℹ️", "WATCH": "👀", "WARNING": "⚠️", "CRITICAL": "🚨"}
                    md += f"- {emoji.get(alert['level'], '⚪')} **[{alert['level']}]** {alert['type']}: {alert['value']:+.1f}%\n"
                md += "\n"

        md += "\n---\n\n*本面板由 SIQ Tracking 自动生成*\n"
        return md
