"""
模块2: 舆情监控器

数据源：巨潮资讯公告/东方财富研报/财联社新闻/雪球社区
分类：正面/负面/中性
输出：舆情日报Markdown
"""

import random
from datetime import datetime, timedelta
from typing import Optional

from agents.tracking.schemas import SentimentDailyReport


# 模拟数据源配置
DATA_SOURCES = [
    {"name": "巨潮资讯", "base_url": "http://www.cninfo.com.cn"},
    {"name": "东方财富", "base_url": "https://data.eastmoney.com"},
    {"name": "财联社", "base_url": "https://www.cls.cn"},
    {"name": "雪球", "base_url": "https://xueqiu.com"},
]


class SentimentMonitor:
    """舆情监控器"""

    def __init__(self):
        self.sources = DATA_SOURCES

    async def collect(self, stock_code: str, company_name: str, days: int = 1) -> list[dict]:
        """
        收集舆情数据

        实际实现中应调用各数据源的API或爬虫
        当前为模拟实现
        """
        records = []
        base_date = datetime.now() - timedelta(days=days)

        # 模拟从各数据源收集数据
        for source in self.sources:
            # 模拟获取该来源的舆情
            source_records = await self._fetch_from_source(
                source["name"], stock_code, company_name, base_date
            )
            records.extend(source_records)

        return records

    async def _fetch_from_source(
        self, source_name: str, stock_code: str, company_name: str, since: datetime
    ) -> list[dict]:
        """从单个数据源获取舆情（模拟）"""
        records = []

        # 模拟数据 - 实际应替换为真实API调用
        templates = {
            "巨潮资讯": [
                {"title": f"{company_name}发布季度报告", "polarity": "中性", "score": 0.0},
                {"title": f"{company_name}董事会决议公告", "polarity": "中性", "score": 0.1},
                {"title": f"{company_name}获得政府补贴", "polarity": "正面", "score": 0.6},
            ],
            "东方财富": [
                {"title": f"分析师上调{company_name}评级", "polarity": "正面", "score": 0.7},
                {"title": f"{company_name}研报：业绩符合预期", "polarity": "中性", "score": 0.2},
                {"title": f"{company_name}面临行业竞争加剧", "polarity": "负面", "score": -0.4},
            ],
            "财联社": [
                {"title": f"{company_name}签署重大合同", "polarity": "正面", "score": 0.8},
                {"title": f"{company_name}回应市场传闻", "polarity": "中性", "score": 0.0},
                {"title": f"行业政策利好{company_name}", "polarity": "正面", "score": 0.5},
            ],
            "雪球": [
                {"title": f"讨论：{company_name}投资价值分析", "polarity": "中性", "score": 0.1},
                {"title": f"{company_name}股价走势分析", "polarity": "中性", "score": -0.1},
                {"title": f"担忧：{company_name}应收账款风险", "polarity": "负面", "score": -0.5},
            ],
        }

        for template in templates.get(source_name, []):
            record = {
                "stock_code": stock_code,
                "source": source_name,
                "title": template["title"],
                "url": None,
                "polarity": template["polarity"],
                "score": template["score"],
                "summary": template["title"],
                "published_at": since + timedelta(hours=random.randint(0, 23)),
            }
            records.append(record)

        return records

    def classify(self, text: str) -> tuple[str, float]:
        """
        舆情分类

        Returns:
            (极性, 得分) 得分范围 -1~1
        """
        positive_keywords = [
            "利好", "增长", "盈利", "超预期", "上调", "买入", "推荐",
            "获奖", "突破", "创新", "扩张", "合作", "签约", "补贴"
        ]
        negative_keywords = [
            "风险", "下滑", "亏损", "下调", "卖出", "减持", "违规",
            "处罚", "诉讼", "冻结", "质押", "违约", "退市", "警示"
        ]

        text_lower = text.lower()
        pos_score = sum(1 for kw in positive_keywords if kw in text_lower)
        neg_score = sum(1 for kw in negative_keywords if kw in text_lower)

        total = pos_score + neg_score
        if total == 0:
            return "中性", 0.0

        raw_score = (pos_score - neg_score) / total

        if raw_score > 0.3:
            return "正面", min(raw_score, 1.0)
        elif raw_score < -0.3:
            return "负面", max(raw_score, -1.0)
        else:
            return "中性", raw_score

    def generate_daily_report(
        self, stock_code: str, company_name: str, records: list[dict]
    ) -> SentimentDailyReport:
        """生成舆情日报"""
        report_date = datetime.now().strftime("%Y-%m-%d")

        total = len(records)
        positive = [r for r in records if r["polarity"] == "正面"]
        negative = [r for r in records if r["polarity"] == "负面"]
        neutral = [r for r in records if r["polarity"] == "中性"]

        avg_score = sum(r["score"] for r in records) / total if total > 0 else 0

        # 亮点（正面舆情）
        highlights = [
            {"source": r["source"], "title": r["title"], "score": r["score"]}
            for r in sorted(positive, key=lambda x: x["score"], reverse=True)[:3]
        ]

        # 风险信号（负面舆情）
        risk_signals = [
            {"source": r["source"], "title": r["title"], "score": r["score"]}
            for r in sorted(negative, key=lambda x: x["score"])[:3]
        ]

        # 生成Markdown
        markdown = self._render_markdown(
            stock_code, company_name, report_date, records,
            len(positive), len(negative), len(neutral), avg_score,
            highlights, risk_signals
        )

        return SentimentDailyReport(
            stock_code=stock_code,
            company_name=company_name,
            report_date=report_date,
            total_count=total,
            positive_count=len(positive),
            negative_count=len(negative),
            neutral_count=len(neutral),
            avg_score=round(avg_score, 3),
            highlights=highlights,
            risk_signals=risk_signals,
            markdown=markdown,
        )

    def _render_markdown(
        self, stock_code: str, company_name: str, report_date: str,
        records: list[dict], pos_count: int, neg_count: int, neu_count: int,
        avg_score: float, highlights: list[dict], risk_signals: list[dict]
    ) -> str:
        """渲染舆情日报Markdown"""
        md = f"""# 舆情日报 - {company_name} ({stock_code})

**报告日期**: {report_date}
**生成时间**: {datetime.now().strftime("%Y-%m-%d %H:%M")}

---

## 舆情概览

| 指标 | 数值 |
|------|------|
| 舆情总量 | {len(records)} |
| 正面 | {pos_count} |
| 负面 | {neg_count} |
| 中性 | {ne_count} |
| 平均情感得分 | {avg_score:+.3f} |

---

## 正面舆情亮点

"""
        if highlights:
            for i, h in enumerate(highlights, 1):
                md += f"{i}. **[{h['source']}]** {h['title']} (得分: {h['score']:+.2f})\n"
        else:
            md += "*今日无显著正面舆情*\n"

        md += "\n---\n\n## 风险信号\n\n"
        if risk_signals:
            for i, r in enumerate(risk_signals, 1):
                md += f"{i}. **[{r['source']}]** {r['title']} (得分: {r['score']:+.2f})\n"
        else:
            md += "*今日无显著负面舆情*\n"

        md += "\n---\n\n## 详细舆情列表\n\n"
        md += "| 来源 | 标题 | 极性 | 得分 |\n"
        md += "|------|------|------|------|\n"
        for r in records:
            polarity_emoji = {"正面": "🟢", "负面": "🔴", "中性": "⚪"}.get(r["polarity"], "⚪")
            md += f"| {r['source']} | {r['title']} | {polarity_emoji} {r['polarity']} | {r['score']:+.2f} |\n"

        md += f"\n---\n\n*本报告由 SIQ Tracking 自动生成*\n"
        return md
