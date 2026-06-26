"""
模块1: 跟踪事项提取器

从分析报告中提取需持续监控的事项
分类：财务承诺/风险信号/异常指标/关联交易/会计变更/监管动态/重大事项/行业变化
输出结构化YAML清单（含到期日、阈值、验证方式）
"""

import re
import yaml
from datetime import datetime

from agents.tracking.models import TrackingItemCategory


CATEGORY_KEYWORDS = {
    TrackingItemCategory.FINANCIAL_COMMITMENT: [
        "承诺", "业绩承诺", "盈利预测", "对赌", "补偿", "回购",
        "增持", "减持计划", "分红", "派息"
    ],
    TrackingItemCategory.RISK_SIGNAL: [
        "风险", "警示", "关注", "立案调查", "违规", "处罚",
        "诉讼", "仲裁", "冻结", "质押", "违约"
    ],
    TrackingItemCategory.ABNORMAL_METRIC: [
        "异常", "大幅波动", "下滑", "亏损", "毛利率", "现金流",
        "应收账款", "存货", "资产负债率", "ROE"
    ],
    TrackingItemCategory.RELATED_TRANSACTION: [
        "关联", "关联交易", "关联方", "资金占用", "担保"
    ],
    TrackingItemCategory.ACCOUNTING_CHANGE: [
        "会计政策", "会计估计", "审计", "非标", "保留意见"
    ],
    TrackingItemCategory.REGULATORY_DYNAMIC: [
        "监管", "问询", "关注函", "警示函", "立案调查", "证监会"
    ],
    TrackingItemCategory.MAJOR_EVENT: [
        "重组", "并购", "定增", "股权激励", "重大合同", "停产",
        "搬迁", "高管变动", "实控人变更"
    ],
    TrackingItemCategory.INDUSTRY_CHANGE: [
        "行业", "政策", "补贴", "关税", "竞争格局", "技术迭代"
    ],
}


class TrackingItemExtractor:
    """从分析报告中提取需持续监控的事项"""

    def __init__(self):
        self.categories = CATEGORY_KEYWORDS

    def extract_from_report(self, report_text: str, stock_code: str, company_name: str) -> list[dict]:
        """
        从分析报告中提取跟踪事项

        Args:
            report_text: 分析报告全文
            stock_code: 股票代码
            company_name: 公司名称

        Returns:
            list[dict]: 跟踪事项列表
        """
        items = []

        # 按段落分析
        paragraphs = self._split_paragraphs(report_text)

        for para in paragraphs:
            para = para.strip()
            if len(para) < 20:
                continue

            category = self._detect_category(para)
            if category:
                item = self._build_item(para, category, stock_code, company_name)
                if item:
                    items.append(item)

        # 去重
        seen = set()
        unique_items = []
        for item in items:
            key = (item["category"], item["title"])
            if key not in seen:
                seen.add(key)
                unique_items.append(item)

        return unique_items

    def _split_paragraphs(self, text: str) -> list[str]:
        """将报告分割为段落"""
        # 按常见分隔符分割
        separators = r'\n\n+|\n#{1,3}\s+|\n\d+\.\s+|\n[一二三四五六七八九十]、'
        parts = re.split(separators, text)
        return [p.strip() for p in parts if p.strip()]

    def _detect_category(self, text: str) -> str | None:
        """检测文本所属分类"""
        text_lower = text.lower()
        scores = {}

        for category, keywords in self.categories.items():
            score = sum(1 for kw in keywords if kw in text_lower)
            if score > 0:
                scores[category.value] = score

        if not scores:
            return None

        return max(scores, key=scores.get)

    def _build_item(self, text: str, category: str, stock_code: str, company_name: str) -> dict | None:
        """构建跟踪事项"""
        # 提取标题（首句或前30字）
        title = self._extract_title(text)
        if not title:
            return None

        # 提取到期日
        due_date = self._extract_due_date(text)

        # 提取阈值
        threshold = self._extract_threshold(text)

        # 提取验证方式
        verification = self._extract_verification(text, category)

        return {
            "stock_code": stock_code,
            "company_name": company_name,
            "category": category,
            "title": title,
            "description": text[:500],
            "due_date": due_date.isoformat() if due_date else None,
            "threshold_value": threshold,
            "verification_method": verification,
            "status": "active",
        }

    def _extract_title(self, text: str) -> str:
        """提取标题"""
        # 尝试提取首句
        sentences = re.split(r'[。！？\n]', text)
        for s in sentences:
            s = s.strip()
            if len(s) >= 5 and len(s) <= 100:
                return s[:80]
        return text[:80] if len(text) > 5 else ""

    def _extract_due_date(self, text: str) -> datetime | None:
        """提取到期日"""
        patterns = [
            r'(\d{4})年(\d{1,2})月(\d{1,2})日',
            r'(\d{4})-(\d{2})-(\d{2})',
            r'(\d{4})/(\d{2})/(\d{2})',
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                try:
                    groups = match.groups()
                    return datetime(int(groups[0]), int(groups[1]), int(groups[2]))
                except (ValueError, IndexError):
                    continue
        return None

    def _extract_threshold(self, text: str) -> str | None:
        """提取阈值信息"""
        patterns = [
            r'(不低于|不少于|不超过|不高于|大于|小于|等于)[\d\.%]+',
            r'(\d+\.?\d*%?)',
            r'(阈值|警戒线|目标|指标)\s*[：:]\s*([^，。\n]+)',
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(0)
        return None

    def _extract_verification(self, text: str, category: str) -> str:
        """提取验证方式"""
        verification_map = {
            "财务承诺": "查阅定期财报、公告",
            "风险信号": "监控公告、新闻报道",
            "异常指标": "对比下期财报数据",
            "关联交易": "查阅关联交易公告",
            "会计变更": "查阅审计报告",
            "监管动态": "查阅监管公告、问询函",
            "重大事项": "查阅公司公告",
            "行业变化": "跟踪行业政策、新闻",
        }
        return verification_map.get(category, "持续关注相关公告")

    def to_yaml(self, items: list[dict]) -> str:
        """导出为YAML格式"""
        return yaml.dump(
            {"tracking_items": items},
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        )

    def from_yaml(self, yaml_text: str) -> list[dict]:
        """从YAML解析"""
        data = yaml.safe_load(yaml_text)
        return data.get("tracking_items", [])
