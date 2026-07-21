#!/usr/bin/env python3
"""
模块2: 舆情监控器
监控巨潮资讯/东方财富/财联社/雪球等数据源，生成舆情日报。

由于实际数据源需要 API 或爬虫，本模块提供：
1. 模拟数据生成框架（用于测试和演示）
2. 数据接口定义（供后续接入真实数据源）
3. 舆情日报 Markdown 生成器
"""

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional
import random

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

# 导入搜索工具
SCRIPT_DIR = Path(__file__).parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

try:
    from search_tools import SearchTools
    SEARCH_AVAILABLE = True
except ImportError:
    SEARCH_AVAILABLE = False
    print("⚠️ search_tools 模块未找到，舆情监控将使用模拟数据")


# 舆情分类
SENTIMENT_TYPES = ["正面", "负面", "中性"]

# 数据源定义
DATA_SOURCES = {
    "巨潮资讯": {
        "type": "official",
        "url_pattern": "http://www.cninfo.com.cn/new/information/topSearch/query",
        "priority": "high",
        "description": "深交所/上交所官方公告",
    },
    "东方财富": {
        "type": "research",
        "url_pattern": "https://data.eastmoney.com/report/stock.jshtml",
        "priority": "medium",
        "description": "券商研报",
    },
    "财联社": {
        "type": "news",
        "url_pattern": "https://www.cls.cn/",
        "priority": "medium",
        "description": "财经新闻快讯",
    },
    "雪球": {
        "type": "community",
        "url_pattern": "https://xueqiu.com/",
        "priority": "low",
        "description": "投资者社区讨论",
    },
}

# 模拟舆情模板（用于测试）
SIMULATED_TEMPLATES = {
    "正面": [
        "{company}发布{year}年业绩预告，净利润同比增长{pct}%",
        "{company}获得重大合同，金额约{amount}亿元",
        "券商上调{company}目标价至{price}元，维持买入评级",
        "{company}新产品通过认证，有望打开{market}市场",
        "{company}控股股东计划增持不超过{amount}亿元",
    ],
    "负面": [
        "{company}收到监管关注函，要求说明{issue}情况",
        "{company}{year}年净利润同比下滑{pct}%",
        "媒体报道{company}存在{issue}问题",
        "{company}大股东减持{amount}万股",
        "行业竞争加剧，{company}面临{issue}压力",
    ],
    "中性": [
        "{company}召开股东大会，审议{issue}议案",
        "{company}发布{year}年年度报告",
        "{company}完成{amount}亿元定增发行",
        "{company}与{partner}签署战略合作协议",
        "{company}变更会计师事务所",
    ],
}


def generate_simulated_sentiment(
    stock_code: str,
    company_name: str,
    date: str = None,
    count: int = 8
) -> List[Dict]:
    """生成模拟舆情数据（用于测试）"""
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    items = []
    sources = list(DATA_SOURCES.keys())

    for i in range(count):
        sentiment = random.choice(SENTIMENT_TYPES)
        template = random.choice(SIMULATED_TEMPLATES[sentiment])

        # 填充模板变量
        content = template.format(
            company=company_name,
            year=datetime.now().year,
            pct=random.randint(10, 50),
            amount=round(random.uniform(1, 20), 1),
            price=round(random.uniform(10, 100), 2),
            market=random.choice(["海外", "新能源", "AI", "5G", "半导体"]),
            issue=random.choice(["关联交易", "应收账款", "存货", "商誉", "现金流"]),
            partner=random.choice(["华为", "腾讯", "阿里巴巴", "比亚迪", "宁德时代"]),
        )

        item = {
            "id": f"{stock_code}-SENT-{date}-{i+1:03d}",
            "date": date,
            "source": random.choice(sources),
            "title": content[:50] + "..." if len(content) > 50 else content,
            "content": content,
            "sentiment": sentiment,
            "url": "#",  # 模拟链接
            "published_at": f"{date} {random.randint(9, 17):02d}:{random.randint(0, 59):02d}",
            "relevance": random.choice(["high", "medium", "low"]),
        }
        items.append(item)

    return items


def fetch_real_sentiment(stock_code: str, company_name: str, date: str = None) -> List[Dict]:
    """
    从真实数据源获取舆情（通过 Tavily/Exa 搜索）

    搜索公司最新公告、新闻、研报等信息，生成结构化舆情数据。
    """
    if not SEARCH_AVAILABLE:
        print("⚠️ 搜索工具不可用，返回空列表")
        return []

    search = SearchTools()
    availability = search.check_availability()

    if not availability.get("any"):
        print("⚠️ 没有可用的搜索后端（Tavily/Exa API Key 未配置）")
        return []

    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    items = []

    # 1. 搜索公司最新新闻和公告
    print(f"🔍 搜索 {company_name} ({stock_code}) 最新舆情...")
    news_result = search.search_company_news(company_name, stock_code, max_results=8)

    if news_result.get("success"):
        for i, result in enumerate(news_result.get("results", [])):
            title = result.get("title", "")
            content = result.get("content", "")
            url = result.get("url", "")

            if not title and not content:
                continue

            # 情感分析（基于关键词的简单规则）
            sentiment = _analyze_sentiment(title + " " + content)

            # 判断相关度
            relevance = _judge_relevance(title + " " + content, company_name, stock_code)

            item = {
                "id": f"{stock_code}-SENT-{date}-{i+1:03d}",
                "date": date,
                "source": _extract_source_domain(url),
                "title": title[:80] + "..." if len(title) > 80 else title,
                "content": content[:300] + "..." if len(content) > 300 else content,
                "sentiment": sentiment,
                "url": url,
                "published_at": result.get("published_date", f"{date} 09:00"),
                "relevance": relevance,
                "search_backend": news_result.get("backend", "unknown"),
            }
            items.append(item)

        print(f"  ✅ 通过 {news_result.get('backend', 'search')} 获取 {len(items)} 条舆情")
    else:
        print(f"  ❌ 搜索失败: {news_result.get('error', '未知错误')}")

    # 2. 搜索风险相关信息（补充负面舆情）
    if availability.get("tavily"):
        print(f"🔍 搜索风险相关信息...")
        risk_result = search.search_company_risks(company_name, stock_code, max_results=3)

        if risk_result.get("success"):
            for i, result in enumerate(risk_result.get("results", [])):
                title = result.get("title", "")
                content = result.get("content", "")

                # 风险信息通常标记为负面
                sentiment = "负面"

                item = {
                    "id": f"{stock_code}-SENT-RISK-{date}-{i+1:03d}",
                    "date": date,
                    "source": _extract_source_domain(result.get("url", "")),
                    "title": f"[风险] {title[:70]}..." if len(title) > 70 else f"[风险] {title}",
                    "content": content[:300] + "..." if len(content) > 300 else content,
                    "sentiment": sentiment,
                    "url": result.get("url", ""),
                    "published_at": result.get("published_date", f"{date} 10:00"),
                    "relevance": "high",
                    "search_backend": "tavily",
                    "risk_flag": True,
                }
                items.append(item)

            print(f"  ✅ 获取 {len(risk_result.get('results', []))} 条风险信息")

    # 去重：基于 URL
    seen_urls = set()
    unique_items = []
    for item in items:
        url = item.get("url", "")
        if url and url in seen_urls:
            continue
        if url:
            seen_urls.add(url)
        unique_items.append(item)

    return unique_items


def _analyze_sentiment(text: str) -> str:
    """基于关键词的简单情感分析"""
    text_lower = text.lower()

    # 负面关键词
    negative_words = [
        "下跌", "下滑", "亏损", "暴雷", "违规", "处罚", "立案", "调查", "监管",
        "风险", "警示", "关注函", "问询", "减持", "解禁", "利空", "负面",
        "降", "跌", "减", "亏", "罚", "查", "退", "破", "暴", "雷",
        "decline", "drop", "fall", "loss", "penalty", "investigation",
        "risk", "warning", "negative", "bearish",
    ]

    # 正面关键词
    positive_words = [
        "上涨", "增长", "盈利", "突破", "获奖", "增持", "回购", "分红",
        "利好", "正面", "合作", "签约", "订单", "中标", "扩产", "创新",
        "升", "涨", "增", "盈", "奖", "扩", "新", "优", "强",
        "rise", "growth", "profit", "breakthrough", "positive", "bullish",
        "award", "cooperation", "contract", "order",
    ]

    neg_count = sum(1 for w in negative_words if w in text_lower)
    pos_count = sum(1 for w in positive_words if w in text_lower)

    if neg_count > pos_count:
        return "负面"
    elif pos_count > neg_count:
        return "正面"
    else:
        return "中性"


def _judge_relevance(text: str, company_name: str, stock_code: str) -> str:
    """判断舆情与公司相关度"""
    text_lower = text.lower()
    relevance_score = 0

    if company_name in text:
        relevance_score += 2
    if stock_code in text:
        relevance_score += 2

    # 财务/经营相关词
    biz_words = ["财报", "业绩", "营收", "利润", "订单", "合同", "项目", "产品"]
    relevance_score += sum(1 for w in biz_words if w in text_lower)

    if relevance_score >= 4:
        return "high"
    elif relevance_score >= 2:
        return "medium"
    else:
        return "low"


def _extract_source_domain(url: str) -> str:
    """从 URL 提取来源域名"""
    if not url or url == "#":
        return "网络搜索"

    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        domain = parsed.netloc.lower()

        # 映射到中文名称
        domain_map = {
            "cninfo.com.cn": "巨潮资讯",
            "eastmoney.com": "东方财富",
            "cls.cn": "财联社",
            "xueqiu.com": "雪球",
            "sina.com.cn": "新浪财经",
            "sohu.com": "搜狐财经",
            "qq.com": "腾讯财经",
            "ifeng.com": "凤凰财经",
            "hexun.com": "和讯网",
            "cs.com.cn": "中证网",
            "stcn.com": "证券时报",
            "jrj.com": "金融界",
            "baike.baidu.com": "百度百科",
            "zhihu.com": "知乎",
            "bilibili.com": "哔哩哔哩",
        }

        for key, name in domain_map.items():
            if key in domain:
                return name

        return domain
    except Exception:
        return "网络搜索"


def analyze_sentiment_trend(items: List[Dict]) -> Dict:
    """分析舆情趋势"""
    total = len(items)
    if total == 0:
        return {
            "total": 0,
            "positive": 0,
            "negative": 0,
            "neutral": 0,
            "sentiment_score": 0,
            "trend": "中性",
        }

    positive = sum(1 for item in items if item["sentiment"] == "正面")
    negative = sum(1 for item in items if item["sentiment"] == "负面")
    neutral = sum(1 for item in items if item["sentiment"] == "中性")

    # 情感得分：-1 到 +1
    sentiment_score = (positive - negative) / total

    # 趋势判断
    if sentiment_score >= 0.3:
        trend = "偏正面"
    elif sentiment_score <= -0.3:
        trend = "偏负面"
    else:
        trend = "中性"

    return {
        "total": total,
        "positive": positive,
        "negative": negative,
        "neutral": neutral,
        "sentiment_score": round(sentiment_score, 2),
        "trend": trend,
    }


def sentiment_evidence_path(report_path: str | Path) -> Path:
    return Path(report_path).with_suffix(".evidence.json")


def _sentiment_evidence_payload(
    stock_code: str,
    company_name: str,
    date: str,
    items: List[Dict],
    trend: Dict,
) -> Dict:
    citations = []
    unresolved_evidence_ids = []
    real_items = [item for item in items if item.get("search_backend")]
    simulated_items = [item for item in items if item.get("url") == "#"]
    for item in real_items:
        evidence_id = str(item.get("id") or "").strip()
        source_url = str(item.get("url") or "").strip()
        quote = " ".join(
            part.strip() for part in (str(item.get("title") or ""), str(item.get("content") or "")) if part.strip()
        )
        if not evidence_id or not source_url.startswith(("http://", "https://")) or not quote:
            unresolved_evidence_ids.append(evidence_id or "missing_evidence_id")
            continue
        citations.append(
            {
                "source_type": "tracking_web_search",
                "source_url": source_url,
                "evidence_id": evidence_id,
                "quote": quote,
                "title": item.get("title"),
                "published_at": item.get("published_at"),
                "period": item.get("date") or date,
                "sentiment": item.get("sentiment"),
                "relevance": item.get("relevance"),
                "search_backend": item.get("search_backend"),
            }
        )
    source_mode = "real" if real_items else "simulated" if simulated_items else "empty"
    return {
        "schema_version": "siq_tracking_sentiment_evidence_v1",
        "stock_code": stock_code,
        "company_name": company_name,
        "report_date": date,
        "source_mode": source_mode,
        "summary": trend,
        "item_count": len(items),
        "real_item_count": len(real_items),
        "simulated_item_count": len(simulated_items),
        "unresolved_evidence_ids": unresolved_evidence_ids,
        "citations": citations,
    }


def generate_sentiment_report(
    stock_code: str,
    company_name: str,
    items: List[Dict],
    output_dir: str,
    date: str = None,
) -> str:
    """生成舆情日报 Markdown"""
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    os.makedirs(output_dir, exist_ok=True)

    trend = analyze_sentiment_trend(items)

    output_path = os.path.join(output_dir, f"{date}.md")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(f"# {company_name} ({stock_code}) 舆情日报\n\n")
        f.write(f"> 日期: {date}\n")
        f.write(f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")

        # 摘要
        f.write("## 舆情摘要\n\n")
        f.write(f"- **舆情总量**: {trend['total']} 条\n")
        f.write(
            f"- **正面**: {trend['positive']} 条 | **负面**: {trend['negative']} 条 | **中性**: {trend['neutral']} 条\n"
        )
        f.write(f"- **情感得分**: {trend['sentiment_score']} ({trend['trend']})\n\n")

        # 情感分布可视化（ASCII）
        f.write("### 情感分布\n\n")
        max_bar = max(trend["positive"], trend["negative"], trend["neutral"], 1)
        bar_width = 30
        pos_bar = int(trend["positive"] / max_bar * bar_width)
        neg_bar = int(trend["negative"] / max_bar * bar_width)
        neu_bar = int(trend["neutral"] / max_bar * bar_width)
        f.write(f"正面 {'█' * pos_bar}{'░' * (bar_width - pos_bar)} {trend['positive']}\n")
        f.write(f"负面 {'█' * neg_bar}{'░' * (bar_width - neg_bar)} {trend['negative']}\n")
        f.write(f"中性 {'█' * neu_bar}{'░' * (bar_width - neu_bar)} {trend['neutral']}\n\n")

        # 重点舆情
        f.write("## 重点舆情\n\n")
        if not items:
            f.write("当前没有可用的真实舆情数据；本日报不使用模拟数据填充。\n\n")

        # 按情感分类展示
        for sentiment in ["负面", "正面", "中性"]:
            sentiment_items = [item for item in items if item["sentiment"] == sentiment]
            if not sentiment_items:
                continue

            emoji = {"负面": "🔴", "正面": "🟢", "中性": "⚪"}
            f.write(f"### {emoji.get(sentiment, '')} {sentiment}舆情 ({len(sentiment_items)}条)\n\n")

            for item in sentiment_items[:5]:  # 每类最多显示5条
                f.write(f"**{item['title']}**\n\n")
                if item.get("id"):
                    f.write(f"- 证据ID: `{item['id']}`\n")
                f.write(f"- 来源: {item['source']}\n")
                f.write(f"- 时间: {item['published_at']}\n")
                f.write(f"- 内容: {item['content']}\n")
                if item["url"] != "#":
                    source_url = str(item["url"]).replace(" ", "%20").replace(")", "%29")
                    f.write(f"- 链接: [打开来源]({source_url})\n")
                f.write(f"- 相关度: {item.get('relevance', 'medium')}\n\n")

        # 风险提示
        f.write("## 风险提示\n\n")
        negative_items = [item for item in items if item["sentiment"] == "负面"]
        if negative_items:
            f.write(f"⚠️ 今日共监测到 **{len(negative_items)}** 条负面舆情，建议关注以下方面:\n\n")
            for item in negative_items[:3]:
                f.write(f"- {item['content'][:100]}...\n")
        else:
            f.write("✅ 今日未监测到明显负面舆情。\n")

        f.write("\n")

        # 数据来源标注
        f.write("## 数据来源\n\n")
        has_real_data = any(item.get("search_backend") for item in items)
        has_simulated_data = any(item.get("url") == "#" for item in items)
        if has_real_data:
            f.write("- ✅ 部分数据来自 Tavily/Exa 网络搜索\n")
            f.write("- 📅 数据时效性以搜索结果为准\n")
        elif has_simulated_data:
            f.write("- 📝 当前为模拟数据（用于测试和演示）\n")
            f.write("- ⚠️ 模拟舆情不得作为预警或投资判断依据\n")
        else:
            f.write("- ⚠️ 真实舆情数据源未返回结果，本日报未填充模拟数据\n")
            f.write("- 建议配置 Tavily/Exa 或接入公告/新闻源后重跑\n")
        f.write(f"- 结构化证据: `{date}.evidence.json`\n")
        f.write("\n")

    evidence_payload = _sentiment_evidence_payload(stock_code, company_name, date, items, trend)
    sentiment_evidence_path(output_path).write_text(
        json.dumps(evidence_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"✅ 舆情日报已生成: {output_path}")
    return output_path


def run_sentiment_monitor_summary(
    stock_code: str,
    company_name: str,
    wiki_base: str = DEFAULT_WIKI_BASE,
    date: str = None,
    *,
    use_search: bool = True,
    allow_simulated: bool = False,
) -> Dict:
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")
    company_dir = str(company_dir_path(wiki_base, stock_code, company_name))
    sentiment_dir = os.path.join(company_dir, "tracking", "sentiment")
    items = fetch_real_sentiment(stock_code, company_name, date) if use_search else []
    if not items and allow_simulated:
        print("⚠️ 真实舆情不可用，按显式请求使用模拟数据")
        items = generate_simulated_sentiment(stock_code, company_name, date)
    elif not items:
        print("⚠️ 真实舆情数据源未返回结果；未启用模拟舆情")

    report_path = generate_sentiment_report(stock_code, company_name, items, sentiment_dir, date)
    evidence_path = sentiment_evidence_path(report_path)
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    source_mode = str(evidence.get("source_mode") or "empty")
    unresolved = evidence.get("unresolved_evidence_ids") or []
    status = "success" if source_mode == "real" and not unresolved else "partial_success"
    return {
        "status": status,
        "report_path": str(report_path),
        "evidence_path": str(evidence_path),
        **evidence,
    }


def run_sentiment_monitor(
    stock_code: str,
    company_name: str,
    wiki_base: str = DEFAULT_WIKI_BASE,
    date: str = None,
    use_simulated: bool = False,
) -> str:
    """
    主入口：运行舆情监控

    输出：wiki/tracking/<stock_code>-<company>/sentiment/<date>.md
    """
    result = run_sentiment_monitor_summary(
        stock_code,
        company_name,
        wiki_base,
        date,
        use_search=not use_simulated,
        allow_simulated=use_simulated,
    )
    return str(result["report_path"])


def run_sentiment_monitor_with_search(
    stock_code: str,
    company_name: str,
    wiki_base: str = DEFAULT_WIKI_BASE,
    date: str = None,
    max_results: int = 10,
    allow_simulated: bool = False,
) -> str:
    """
    主入口：运行舆情监控（强制使用网络搜索）

    优先使用 Tavily/Exa 搜索真实舆情数据；只有显式允许时才回退到模拟数据。

    输出：wiki/tracking/<stock_code>-<company>/sentiment/<date>.md
    """
    result = run_sentiment_monitor_summary(
        stock_code,
        company_name,
        wiki_base,
        date,
        use_search=True,
        allow_simulated=allow_simulated,
    )
    return str(result["report_path"])


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="舆情监控器")
    parser.add_argument("--stock", required=True, help="股票代码")
    parser.add_argument("--company", required=True, help="公司简称")
    parser.add_argument("--date", help="日期 (YYYY-MM-DD)")
    parser.add_argument("--wiki-base", default=DEFAULT_WIKI_BASE, help="wiki 根目录")
    parser.add_argument("--real", action="store_true", help="使用真实数据源")
    parser.add_argument("--no-search", action="store_true", help="禁用真实数据源搜索")
    parser.add_argument("--allow-simulated", action="store_true", help="允许真实数据不可用时生成模拟舆情")
    parser.add_argument("--json-summary", action="store_true", help="输出结构化运行摘要")
    args = parser.parse_args()

    result = run_sentiment_monitor_summary(
        args.stock,
        args.company,
        args.wiki_base,
        args.date,
        use_search=args.real or not args.no_search,
        allow_simulated=args.allow_simulated,
    )
    if args.json_summary:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["status"] == "success" else 2)
