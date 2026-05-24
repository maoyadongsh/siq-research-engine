"""
FinSight Tracking Agent - 主控制器

整合五大模块，提供统一的跟踪与预警服务
"""

import os
import glob
from datetime import datetime
from typing import Optional

from agents.tracking.paths import company_tracking_dir, tracking_base_path
from agents.tracking.modules.item_extractor import TrackingItemExtractor
from agents.tracking.modules.sentiment_monitor import SentimentMonitor
from agents.tracking.modules.metric_tracker import MetricTracker
from agents.tracking.modules.alert_trigger import AlertTrigger
from agents.tracking.modules.report_updater import ReportUpdater
from agents.tracking.schemas import (
    TrackingDashboard,
    TrackingItemResponse,
    SentimentDailyReport,
    MetricTrackingPanel,
    AlertReport,
)


class TrackingAgent:
    """
    FinSight 跟踪智能体

    职责：提取跟踪事项、监控舆情、追踪指标、触发预警
    """

    def __init__(self, wiki_base_path: str | None = None):
        self.extractor = TrackingItemExtractor()
        self.sentiment_monitor = SentimentMonitor()
        self.metric_tracker = MetricTracker()
        self.alert_trigger = AlertTrigger()
        wiki_base_path = wiki_base_path or str(tracking_base_path())
        self.report_updater = ReportUpdater(wiki_base_path)
        self.wiki_base = wiki_base_path

    async def process_report(
        self,
        stock_code: str,
        company_name: str,
        report_text: str,
        metrics_data: Optional[dict] = None,
        previous_metrics: Optional[dict] = None,
        year_ago_metrics: Optional[dict] = None,
    ) -> TrackingDashboard:
        """
        处理分析报告，生成完整的跟踪面板

        Args:
            report_text: 分析报告全文
            metrics_data: 当期指标数据
            previous_metrics: 上期指标数据
            year_ago_metrics: 去年同期指标数据

        Returns:
            TrackingDashboard: 综合跟踪面板
        """
        # 1. 提取跟踪事项
        items = self.extractor.extract_from_report(report_text, stock_code, company_name)

        # 保存跟踪事项到文件
        if items:
            self.report_updater.create_tracking_items_file(stock_code, company_name, items)

        # 2. 收集舆情
        sentiment_records = await self.sentiment_monitor.collect(stock_code, company_name)
        sentiment_report = self.sentiment_monitor.generate_daily_report(
            stock_code, company_name, sentiment_records
        )

        # 保存舆情日报
        self.report_updater.save_sentiment_report(
            stock_code, company_name,
            sentiment_report.report_date, sentiment_report.markdown
        )

        # 3. 追踪指标
        metric_panel = None
        if metrics_data:
            report_period = datetime.now().strftime("%Y-Q%q")  # 简化，实际需要计算季度
            # 修正季度计算
            month = datetime.now().month
            quarter = (month - 1) // 3 + 1
            report_period = f"{datetime.now().year}-Q{quarter}"

            metric_panel = self.metric_tracker.track_metrics(
                stock_code=stock_code,
                company_name=company_name,
                report_period=report_period,
                current_data=metrics_data,
                previous_data=previous_metrics,
                year_ago_data=year_ago_metrics,
            )

            # 保存指标面板
            self.report_updater.save_metric_panel(
                stock_code, company_name, report_period, metric_panel.markdown
            )

        # 4. 触发预警
        alerts = self.alert_trigger.evaluate(
            stock_code=stock_code,
            company_name=company_name,
            metrics_data=metrics_data,
            sentiment_data=sentiment_records,
        )

        # 保存预警报告
        alert_seq = 1
        for alert in alerts:
            self.report_updater.save_alert_report(
                stock_code, company_name,
                alert.alert_level, alert_seq, alert.markdown
            )
            alert_seq += 1

        # 5. 构建综合面板
        item_responses = [
            TrackingItemResponse(
                id=i,
                stock_code=item["stock_code"],
                company_name=item["company_name"],
                category=item["category"],
                title=item["title"],
                description=item["description"],
                due_date=item.get("due_date"),
                threshold_value=item.get("threshold_value"),
                verification_method=item.get("verification_method"),
                status=item.get("status", "active"),
                source_report=item.get("source_report"),
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )
            for i, item in enumerate(items, 1)
        ]

        summary = self._generate_summary(
            stock_code, company_name, len(items), sentiment_report, len(alerts)
        )

        return TrackingDashboard(
            stock_code=stock_code,
            company_name=company_name,
            active_items=item_responses,
            latest_sentiment=sentiment_report,
            latest_metrics=metric_panel,
            recent_alerts=alerts,
            summary=summary,
        )

    async def refresh_sentiment(
        self, stock_code: str, company_name: str
    ) -> SentimentDailyReport:
        """刷新舆情数据"""
        records = await self.sentiment_monitor.collect(stock_code, company_name)
        report = self.sentiment_monitor.generate_daily_report(
            stock_code, company_name, records
        )

        self.report_updater.save_sentiment_report(
            stock_code, company_name, report.report_date, report.markdown
        )

        return report

    def refresh_metrics(
        self,
        stock_code: str,
        company_name: str,
        report_period: str,
        current_data: dict,
        previous_data: Optional[dict] = None,
        year_ago_data: Optional[dict] = None,
    ) -> MetricTrackingPanel:
        """刷新指标追踪"""
        panel = self.metric_tracker.track_metrics(
            stock_code, company_name, report_period,
            current_data, previous_data, year_ago_data
        )

        self.report_updater.save_metric_panel(
            stock_code, company_name, report_period, panel.markdown
        )

        # 检查是否需要触发预警
        alerts = self.alert_trigger.evaluate(
            stock_code=stock_code,
            company_name=company_name,
            metrics_data=current_data,
        )

        alert_seq = 1
        for alert in alerts:
            self.report_updater.save_alert_report(
                stock_code, company_name,
                alert.alert_level, alert_seq, alert.markdown
            )
            alert_seq += 1

        return panel

    def get_dashboard(self, stock_code: str, company_name: str) -> Optional[TrackingDashboard]:
        """获取跟踪面板（从文件系统）"""
        company_dir = company_tracking_dir(self.wiki_base, stock_code, company_name)
        resolved_company_name = company_name

        if not os.path.exists(company_dir):
            matches = sorted(glob.glob(os.path.join(self.wiki_base, f"{stock_code}-*", "tracking")))
            if not matches:
                return None
            company_dir = matches[0]
            parent_name = os.path.basename(os.path.dirname(company_dir))
            if parent_name.startswith(f"{stock_code}-"):
                resolved_company_name = parent_name.split("-", 1)[1]

        # 读取跟踪事项
        items = []
        items_path = os.path.join(company_dir, "tracking-items.md")
        if os.path.exists(items_path):
            # 简化：实际应解析文件内容
            pass

        # 构建面板
        return TrackingDashboard(
            stock_code=stock_code,
            company_name=resolved_company_name,
            active_items=[],
            latest_sentiment=None,
            latest_metrics=None,
            recent_alerts=[],
            summary=f"跟踪目录已创建: {company_dir}",
        )

    def _generate_summary(
        self,
        stock_code: str,
        company_name: str,
        item_count: int,
        sentiment: SentimentDailyReport,
        alert_count: int,
    ) -> str:
        """生成面板摘要"""
        parts = [
            f"## {company_name} ({stock_code}) 跟踪摘要",
            "",
            f"- **跟踪事项**: {item_count} 项待监控",
            f"- **舆情概况**: {sentiment.total_count} 条舆情，平均得分 {sentiment.avg_score:+.3f}",
            f"  - 正面: {sentiment.positive_count} | 负面: {sentiment.negative_count} | 中性: {sentiment.neutral_count}",
            f"- **预警状态**: {alert_count} 条未处理预警",
            "",
        ]

        if sentiment.risk_signals:
            parts.append("**风险信号**:")
            for signal in sentiment.risk_signals[:3]:
                parts.append(f"- [{signal['source']}] {signal['title']}")
            parts.append("")

        parts.append("---")
        parts.append("*数据更新时间: " + datetime.now().strftime("%Y-%m-%d %H:%M") + "*")

        return "\n".join(parts)
