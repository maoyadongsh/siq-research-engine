"""
SIQ Tracking Agent - 主控制器

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
)


class TrackingAgent:
    """
    SIQ 跟踪智能体

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
            report_period = _report_period_for_quarter()

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
        items_path = os.path.join(company_dir, "tracking-items.md")
        items = _read_tracking_items(items_path, stock_code, resolved_company_name)

        # 构建面板
        return TrackingDashboard(
            stock_code=stock_code,
            company_name=resolved_company_name,
            active_items=items,
            latest_sentiment=None,
            latest_metrics=None,
            recent_alerts=[],
            summary=(
                f"跟踪目录已创建: {company_dir}"
                if not items
                else f"已加载 {len(items)} 项跟踪事项: {company_dir}"
            ),
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


def _report_period_for_quarter(now: datetime | None = None) -> str:
    now = now or datetime.now()
    quarter = (now.month - 1) // 3 + 1
    return f"{now.year}-Q{quarter}"


def _read_tracking_items(
    items_path: str | os.PathLike[str], stock_code: str, company_name: str
) -> list[TrackingItemResponse]:
    if not os.path.exists(items_path):
        return []

    with open(items_path, "r", encoding="utf-8") as f:
        content = f.read()

    return _parse_tracking_items_markdown(content, stock_code, company_name)


def _parse_tracking_items_markdown(
    content: str, stock_code: str, company_name: str
) -> list[TrackingItemResponse]:
    items: list[dict] = []
    category = "其他"
    current: dict | None = None

    def flush_current() -> None:
        nonlocal current
        if current and current.get("title"):
            items.append(current)
        current = None

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if line.startswith("## ") and not line.startswith("### "):
            flush_current()
            category = line.removeprefix("## ").strip() or "其他"
            continue

        if line.startswith("### "):
            flush_current()
            current = {
                "category": category,
                "title": line.removeprefix("### ").strip() or "未命名",
                "description": "-",
                "due_date": None,
                "threshold_value": None,
                "verification_method": None,
                "status": "active",
                "source_report": None,
            }
            continue

        if not current or not line.startswith("- **"):
            continue

        field_name, field_value = _parse_tracking_item_field(line)
        if field_name == "描述":
            current["description"] = field_value or "-"
        elif field_name == "到期日":
            current["due_date"] = _parse_optional_datetime(field_value)
        elif field_name == "阈值":
            current["threshold_value"] = _none_if_empty_marker(field_value)
        elif field_name == "验证方式":
            current["verification_method"] = _none_if_empty_marker(field_value)
        elif field_name == "状态":
            current["status"] = field_value or "active"
        elif field_name in {"来源报告", "来源"}:
            current["source_report"] = _none_if_empty_marker(field_value)

    flush_current()

    now = datetime.now()
    responses: list[TrackingItemResponse] = []
    for idx, item in enumerate(items, 1):
        responses.append(
            TrackingItemResponse(
                id=idx,
                stock_code=stock_code,
                company_name=company_name,
                category=item.get("category") or "其他",
                title=item.get("title") or "未命名",
                description=item.get("description") or "-",
                due_date=item.get("due_date"),
                threshold_value=item.get("threshold_value"),
                verification_method=item.get("verification_method"),
                status=item.get("status") or "active",
                source_report=item.get("source_report"),
                created_at=now,
                updated_at=now,
            )
        )
    return responses


def _parse_tracking_item_field(line: str) -> tuple[str, str]:
    try:
        label, value = line.removeprefix("- **").split("**:", 1)
    except ValueError:
        return "", ""
    return label.strip(), value.strip()


def _none_if_empty_marker(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return None if stripped in {"", "-", "无", "None", "null"} else stripped


def _parse_optional_datetime(value: str | None) -> datetime | None:
    stripped = _none_if_empty_marker(value)
    if not stripped:
        return None

    normalized = stripped.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        pass

    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(stripped, fmt)
        except ValueError:
            continue
    return None
