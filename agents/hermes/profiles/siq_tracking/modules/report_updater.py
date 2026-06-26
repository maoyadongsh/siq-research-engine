"""
模块5: 报告更新器

根据跟踪结果自动更新原始报告
追加"跟踪更新"章节
归档旧版，保留历史
"""

import os
import shutil
from datetime import datetime
from typing import Optional


class ReportUpdater:
    """报告更新器"""

    def __init__(self, wiki_base_path: str = "wiki/tracking"):
        self.wiki_base = wiki_base_path

    def update_report(
        self,
        stock_code: str,
        company_name: str,
        original_report_path: str,
        tracking_items: Optional[list[dict]] = None,
        sentiment_report: Optional[str] = None,
        metric_panel: Optional[str] = None,
        alert_reports: Optional[list[str]] = None,
    ) -> str:
        """
        更新报告

        Args:
            original_report_path: 原始报告路径
            tracking_items: 跟踪事项列表
            sentiment_report: 舆情日报内容
            metric_panel: 指标面板内容
            alert_reports: 预警报告列表

        Returns:
            str: 更新后的报告路径
        """
        # 确保目录存在
        company_dir = os.path.join(self.wiki_base, f"{stock_code}-{company_name}")
        updates_dir = os.path.join(company_dir, "updates")
        os.makedirs(updates_dir, exist_ok=True)

        # 读取原始报告
        original_content = ""
        if os.path.exists(original_report_path):
            with open(original_report_path, "r", encoding="utf-8") as f:
                original_content = f.read()

        # 生成更新内容
        update_section = self._generate_update_section(
            tracking_items, sentiment_report, metric_panel, alert_reports
        )

        # 构建新报告
        updated_content = self._append_update_section(original_content, update_section)

        # 保存更新后的报告
        update_date = datetime.now().strftime("%Y-%m-%d")
        update_path = os.path.join(updates_dir, f"{update_date}-update.md")

        with open(update_path, "w", encoding="utf-8") as f:
            f.write(updated_content)

        # 归档旧版
        self._archive_original(original_report_path, company_dir)

        return update_path

    def _generate_update_section(
        self,
        tracking_items: Optional[list[dict]] = None,
        sentiment_report: Optional[str] = None,
        metric_panel: Optional[str] = None,
        alert_reports: Optional[list[str]] = None,
    ) -> str:
        """生成"跟踪更新"章节"""
        section = f"\n\n---\n\n# 跟踪更新 ({datetime.now().strftime('%Y-%m-%d %H:%M')})\n\n"

        # 跟踪事项更新
        if tracking_items:
            section += "## 跟踪事项状态\n\n"
            section += "| 分类 | 事项 | 状态 | 到期日 |\n"
            section += "|------|------|------|--------|\n"
            for item in tracking_items:
                status_emoji = {"active": "🟡", "resolved": "✅", "expired": "⏰"}
                status = item.get("status", "active")
                section += f"| {item.get('category', '-')} | {item.get('title', '-')} | {status_emoji.get(status, '⚪')} {status} | {item.get('due_date', '-')} |\n"
            section += "\n"

        # 舆情摘要
        if sentiment_report:
            section += "## 最新舆情\n\n"
            # 提取摘要（前500字）
            summary = sentiment_report[:500] + "..." if len(sentiment_report) > 500 else sentiment_report
            section += summary + "\n\n"

        # 指标摘要
        if metric_panel:
            section += "## 指标变化\n\n"
            summary = metric_panel[:500] + "..." if len(metric_panel) > 500 else metric_panel
            section += summary + "\n\n"

        # 预警信息
        if alert_reports:
            section += "## 预警记录\n\n"
            for i, alert in enumerate(alert_reports, 1):
                section += f"{i}. {alert[:200]}...\n\n"

        return section

    def _append_update_section(self, original_content: str, update_section: str) -> str:
        """将更新章节追加到报告"""
        if not original_content:
            return f"# 报告\n\n{update_section}"

        # 如果已有跟踪更新章节，追加到该章节
        if "# 跟踪更新" in original_content:
            # 在最后一个跟踪更新之前插入新的
            parts = original_content.rsplit("# 跟踪更新", 1)
            if len(parts) == 2:
                return parts[0] + update_section + "\n\n---\n\n# 跟踪更新" + parts[1]

        # 否则追加到文档末尾
        return original_content + update_section

    def _archive_original(self, original_path: str, company_dir: str) -> None:
        """归档原始报告"""
        if not os.path.exists(original_path):
            return

        archive_dir = os.path.join(company_dir, "archive")
        os.makedirs(archive_dir, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = os.path.basename(original_path)
        archive_path = os.path.join(archive_dir, f"{timestamp}_{filename}")

        shutil.copy2(original_path, archive_path)

    def create_tracking_items_file(
        self, stock_code: str, company_name: str, items: list[dict]
    ) -> str:
        """创建跟踪事项清单文件"""
        company_dir = os.path.join(self.wiki_base, f"{stock_code}-{company_name}")
        os.makedirs(company_dir, exist_ok=True)

        file_path = os.path.join(company_dir, "tracking-items.md")

        content = f"# 跟踪事项清单 - {company_name} ({stock_code})\n\n"
        content += f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
        content += "---\n\n"

        # 按分类分组
        by_category = {}
        for item in items:
            cat = item.get("category", "其他")
            by_category.setdefault(cat, []).append(item)

        for category, cat_items in by_category.items():
            content += f"## {category}\n\n"
            for item in cat_items:
                content += f"### {item.get('title', '未命名')}\n\n"
                content += f"- **描述**: {item.get('description', '-')}\n"
                content += f"- **到期日**: {item.get('due_date', '无')}\n"
                content += f"- **阈值**: {item.get('threshold_value', '无')}\n"
                content += f"- **验证方式**: {item.get('verification_method', '-')}\n"
                content += f"- **状态**: {item.get('status', 'active')}\n\n"

        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)

        return file_path

    def save_sentiment_report(
        self, stock_code: str, company_name: str, report_date: str, content: str
    ) -> str:
        """保存舆情日报"""
        company_dir = os.path.join(self.wiki_base, f"{stock_code}-{company_name}")
        sentiment_dir = os.path.join(company_dir, "sentiment")
        os.makedirs(sentiment_dir, exist_ok=True)

        file_path = os.path.join(sentiment_dir, f"{report_date}.md")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)

        return file_path

    def save_metric_panel(
        self, stock_code: str, company_name: str, report_period: str, content: str
    ) -> str:
        """保存指标追踪面板"""
        company_dir = os.path.join(self.wiki_base, f"{stock_code}-{company_name}")
        metrics_dir = os.path.join(company_dir, "metrics")
        os.makedirs(metrics_dir, exist_ok=True)

        file_path = os.path.join(metrics_dir, f"{report_period}.md")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)

        return file_path

    def save_alert_report(
        self, stock_code: str, company_name: str, alert_level: str,
        alert_seq: int, content: str
    ) -> str:
        """保存预警报告"""
        company_dir = os.path.join(self.wiki_base, f"{stock_code}-{company_name}")
        alerts_dir = os.path.join(company_dir, "alerts")
        os.makedirs(alerts_dir, exist_ok=True)

        date_str = datetime.now().strftime("%Y-%m-%d")
        file_path = os.path.join(
            alerts_dir, f"{date_str}-{alert_level.lower()}-{alert_seq:03d}.md"
        )
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)

        return file_path
