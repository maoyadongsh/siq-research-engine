"""
模块4: 预警触发器

四级预警：INFO/WATCH/WARNING/CRITICAL
触发条件：突破阈值/重大负面舆情/监管处罚
输出：预警报告 + 通知
"""

from datetime import datetime
from typing import Optional

from agents.tracking.models import AlertLevel
from agents.tracking.schemas import AlertReport


# 预警规则配置
ALERT_RULES = [
    {
        "name": "营收大幅下滑",
        "level": "WARNING",
        "condition": "metric.营收增长率 < -30",
        "description": "营业收入同比下降超过30%",
    },
    {
        "name": "净利润转亏",
        "level": "CRITICAL",
        "condition": "metric.归母净利润 < 0 and previous.归母净利润 > 0",
        "description": "归母净利润由盈转亏",
    },
    {
        "name": "资产负债率过高",
        "level": "WATCH",
        "condition": "metric.资产负债率 > 70",
        "description": "资产负债率超过70%",
    },
    {
        "name": "经营现金流恶化",
        "level": "WARNING",
        "condition": "metric.经营现金流 < 0 and qoq_change < -50",
        "description": "经营现金流转负且环比大幅下降",
    },
    {
        "name": "重大负面舆情",
        "level": "WARNING",
        "condition": "sentiment.negative_score < -0.8",
        "description": "检测到重大负面舆情信号",
    },
    {
        "name": "监管处罚",
        "level": "CRITICAL",
        "condition": "event.type == '监管处罚'",
        "description": "公司收到监管处罚决定",
    },
    {
        "name": "毛利率异常波动",
        "level": "WATCH",
        "condition": "abs(metric.毛利率.qoq) > 20",
        "description": "毛利率环比波动超过20个百分点",
    },
    {
        "name": "关联交易异常",
        "level": "INFO",
        "condition": "event.type == '关联交易' and amount > 净资产_10%",
        "description": "关联交易金额超过净资产的10%",
    },
]


class AlertTrigger:
    """预警触发器"""

    def __init__(self):
        self.rules = ALERT_RULES

    def evaluate(
        self,
        stock_code: str,
        company_name: str,
        metrics_data: Optional[dict] = None,
        sentiment_data: Optional[list[dict]] = None,
        events: Optional[list[dict]] = None,
    ) -> list[AlertReport]:
        """
        评估预警条件

        Args:
            metrics_data: 指标数据
            sentiment_data: 舆情数据
            events: 事件列表

        Returns:
            list[AlertReport]: 触发的预警报告
        """
        alerts = []

        # 评估指标类预警
        if metrics_data:
            for rule in self.rules:
                if rule["condition"].startswith("metric."):
                    alert = self._evaluate_metric_rule(rule, stock_code, company_name, metrics_data)
                    if alert:
                        alerts.append(alert)

        # 评估舆情类预警
        if sentiment_data:
            for rule in self.rules:
                if rule["condition"].startswith("sentiment."):
                    alert = self._evaluate_sentiment_rule(rule, stock_code, company_name, sentiment_data)
                    if alert:
                        alerts.append(alert)

        # 评估事件类预警
        if events:
            for rule in self.rules:
                if rule["condition"].startswith("event."):
                    alert = self._evaluate_event_rule(rule, stock_code, company_name, events)
                    if alert:
                        alerts.append(alert)

        # 按级别排序：CRITICAL > WARNING > WATCH > INFO
        level_order = {"CRITICAL": 0, "WARNING": 1, "WATCH": 2, "INFO": 3}
        alerts.sort(key=lambda x: level_order.get(x.alert_level, 99))

        return alerts

    def _evaluate_metric_rule(
        self, rule: dict, stock_code: str, company_name: str, metrics_data: dict
    ) -> Optional[AlertReport]:
        """评估指标规则（简化版）"""
        condition = rule["condition"]

        # 简单的规则解析 - 实际应使用更完善的表达式引擎
        triggered = False
        detail = ""

        if "营收增长率" in condition and "营收增长率" in metrics_data:
            value = metrics_data.get("营收增长率")
            if value is not None and value < -30:
                triggered = True
                detail = f"营收增长率为 {value:.2f}%，低于-30%阈值"

        elif "归母净利润" in condition and "归母净利润" in metrics_data:
            value = metrics_data.get("归母净利润")
            if value is not None and value < 0:
                triggered = True
                detail = f"归母净利润为 {value:.2f} 亿元，出现亏损"

        elif "资产负债率" in condition and "资产负债率" in metrics_data:
            value = metrics_data.get("资产负债率")
            if value is not None and value > 70:
                triggered = True
                detail = f"资产负债率为 {value:.2f}%，超过70%"

        elif "经营现金流" in condition and "经营现金流" in metrics_data:
            value = metrics_data.get("经营现金流")
            if value is not None and value < 0:
                triggered = True
                detail = f"经营现金流为 {value:.2f} 亿元，出现负值"

        if triggered:
            return self._build_alert_report(
                stock_code, company_name, rule, detail
            )
        return None

    def _evaluate_sentiment_rule(
        self, rule: dict, stock_code: str, company_name: str, sentiment_data: list[dict]
    ) -> Optional[AlertReport]:
        """评估舆情规则"""
        negative_records = [r for r in sentiment_data if r.get("polarity") == "负面"]

        if not negative_records:
            return None

        # 检查是否有重大负面舆情
        severe_negative = [r for r in negative_records if r.get("score", 0) < -0.8]

        if severe_negative:
            detail = f"检测到 {len(severe_negative)} 条重大负面舆情"
            return self._build_alert_report(stock_code, company_name, rule, detail)

        return None

    def _evaluate_event_rule(
        self, rule: dict, stock_code: str, company_name: str, events: list[dict]
    ) -> Optional[AlertReport]:
        """评估事件规则"""
        for event in events:
            event_type = event.get("type", "")
            if "监管处罚" in event_type or "处罚" in event_type:
                detail = f"收到监管处罚: {event.get('description', '')}"
                return self._build_alert_report(stock_code, company_name, rule, detail)

            if "关联交易" in event_type:
                amount = event.get("amount", 0)
                net_asset_ratio = event.get("net_asset_ratio", 0)
                if net_asset_ratio > 10:
                    detail = f"关联交易金额 {amount} 亿元，占净资产 {net_asset_ratio:.1f}%"
                    return self._build_alert_report(stock_code, company_name, rule, detail)

        return None

    def _build_alert_report(
        self, stock_code: str, company_name: str, rule: dict, detail: str
    ) -> AlertReport:
        """构建预警报告"""
        level = rule["level"]
        title = rule["name"]
        description = f"{rule['description']}\n\n详细信息: {detail}"

        # 根据级别生成建议
        recommendation = self._generate_recommendation(level, title)

        # 生成Markdown
        markdown = self._render_alert_markdown(
            stock_code, company_name, level, title, description, recommendation
        )

        return AlertReport(
            stock_code=stock_code,
            company_name=company_name,
            alert_level=level,
            alert_type=rule.get("alert_type", "自动触发"),
            title=title,
            description=description,
            recommendation=recommendation,
            triggered_at=datetime.now(),
            markdown=markdown,
        )

    def _generate_recommendation(self, level: str, alert_name: str) -> str:
        """生成应对建议"""
        recommendations = {
            "INFO": "建议关注后续发展，纳入常规跟踪。",
            "WATCH": "建议加强监控频率，评估对投资逻辑的影响。",
            "WARNING": "建议深入分析原因，考虑调整仓位或风险对冲。",
            "CRITICAL": "建议立即评估持仓风险，考虑减仓或止损。",
        }
        base = recommendations.get(level, "请根据具体情况评估。")

        specific = {
            "营收大幅下滑": "重点关注收入下滑的结构性原因，区分周期性与结构性因素。",
            "净利润转亏": "分析亏损原因，评估是否为一次性因素或持续性恶化。",
            "资产负债率过高": "关注偿债能力，评估再融资风险。",
            "经营现金流恶化": "警惕盈利质量下降，关注营运资本变动。",
            "重大负面舆情": "核实舆情真实性，评估对公司声誉和业务的影响。",
            "监管处罚": "关注处罚后续影响，评估是否涉及核心业务。",
        }

        return base + " " + specific.get(alert_name, "")

    def _render_alert_markdown(
        self, stock_code: str, company_name: str, level: str,
        title: str, description: str, recommendation: str
    ) -> str:
        """渲染预警报告Markdown"""
        level_emoji = {
            "INFO": "ℹ️",
            "WATCH": "👀",
            "WARNING": "⚠️",
            "CRITICAL": "🚨",
        }
        level_color = {
            "INFO": "#3498db",
            "WATCH": "#f39c12",
            "WARNING": "#e67e22",
            "CRITICAL": "#e74c3c",
        }

        md = f"""# {level_emoji.get(level, '⚪')} 预警报告 - {title}

**公司**: {company_name} ({stock_code})
**预警级别**: <span style="color: {level_color.get(level, '#333')}">{level}</span>
**触发时间**: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

---

## 预警详情

{description}

---

## 应对建议

{recommendation}

---

*本预警由 SIQ Tracking 自动生成，请结合实际情况判断。*
"""
        return md

    def should_notify(self, alert_level: str) -> bool:
        """判断是否需要发送通知"""
        return alert_level in ("WARNING", "CRITICAL")
