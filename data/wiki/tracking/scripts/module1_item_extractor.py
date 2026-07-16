#!/usr/bin/env python3
"""
模块1: 跟踪事项提取器
从分析报告中提取需持续监控的事项，输出结构化 YAML 清单。

分类：财务承诺/风险信号/异常指标/关联交易/会计变更/监管动态/重大事项/行业变化
"""

import json
import yaml
import re
import os
import sys
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path

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
from local_citations import format_ref_summary, resolve_analysis_refs, resolve_key_metric_refs

# 导入搜索工具
SCRIPT_DIR = Path(__file__).parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

try:
    from search_tools import SearchTools
    SEARCH_AVAILABLE = True
except ImportError:
    SEARCH_AVAILABLE = False

# 跟踪事项分类体系
TRACKING_CATEGORIES = [
    "财务承诺",
    "风险信号",
    "异常指标",
    "关联交易",
    "会计变更",
    "监管动态",
    "重大事项",
    "行业变化",
]

# 分类关键词映射
CATEGORY_KEYWORDS = {
    "财务承诺": ["承诺", "业绩承诺", "补偿", "对赌", "回购", "增持", "减持计划", "分红", "派息"],
    "风险信号": ["风险", "警示", "关注", "非标", "保留意见", "强调事项", "持续经营", "流动性风险", "债务风险"],
    "异常指标": ["异常", "波动", "突变", "大幅", "骤降", "激增", "偏离", "毛利率异常", "费用率异常"],
    "关联交易": ["关联", "关联方", "关联交易", "资金占用", "担保", "往来款"],
    "会计变更": ["会计政策", "会计估计", "变更", "追溯调整", "重述"],
    "监管动态": ["监管", "问询", "关注函", "警示函", "立案", "调查", "处罚", "整改"],
    "重大事项": ["重组", "并购", "定增", "股权激励", "重大合同", "诉讼", "仲裁", "破产", "清算"],
    "行业变化": ["行业", "政策", "补贴", "退坡", "技术路线", "竞争格局", "市场份额"],
}

# 默认验证方式映射
DEFAULT_VERIFICATION = {
    "财务承诺": "查阅下一期财报附注或公告",
    "风险信号": "持续监控财报审计意见及公告",
    "异常指标": "对比下一季度/年度财报数据",
    "关联交易": "查阅关联交易专项报告",
    "会计变更": "核对后续财报会计政策一致性",
    "监管动态": "跟踪监管网站及公司公告",
    "重大事项": "查阅公司进展公告",
    "行业变化": "跟踪行业政策及市场数据",
}


BALANCE_SHEET_SCALE_METRICS = {
    "total_assets",
    "total_liabilities",
    "equity_attributable_parent",
    "total_equity",
    "shareholders_equity",
}


def _clean_yaml_scalar(value):
    if isinstance(value, str):
        value = value.replace("\x00", "")
        value = value.replace("```", "` ` `")
        return value
    if isinstance(value, list):
        return [_clean_yaml_scalar(v) for v in value]
    if isinstance(value, dict):
        return {k: _clean_yaml_scalar(v) for k, v in value.items()}
    return value


def looks_like_unit_or_scale_issue(canonical: str, latest_val: float, prev_val: float, yoy_change: float) -> bool:
    """识别疑似单位/口径问题，避免把数据清洗错误误报为经营风险。"""
    if canonical not in BALANCE_SHEET_SCALE_METRICS:
        return False
    if latest_val == 0 or prev_val == 0:
        return False
    size_ratio = min(abs(latest_val), abs(prev_val)) / max(abs(latest_val), abs(prev_val))
    return abs(yoy_change) > 0.9 and size_ratio < 0.1


def classify_item(text: str) -> str:
    """根据文本内容分类跟踪事项"""
    text_lower = text.lower()
    scores = {}
    for category, keywords in CATEGORY_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text_lower)
        if score > 0:
            scores[category] = score
    if not scores:
        return "重大事项"
    return max(scores, key=scores.get)


def extract_date_hints(text: str) -> dict:
    """从文本中提取日期提示"""
    hints = {}
    # 匹配 "2025年6月30日"、"2025-06-30" 等
    date_patterns = [
        r'(\d{4})年(\d{1,2})月(\d{1,2})日',
        r'(\d{4})-(\d{2})-(\d{2})',
        r'(\d{4})/(\d{2})/(\d{2})',
    ]
    for pattern in date_patterns:
        matches = re.findall(pattern, text)
        if matches:
            hints['explicit_dates'] = matches
            break
    # 匹配季度提示
    quarter_patterns = [
        r'(\d{4})年.*?[一二三四]季度',
        r'Q([1-4]).*?(\d{4})',
        r'(\d{4}).*?Q([1-4])',
    ]
    for pattern in quarter_patterns:
        matches = re.findall(pattern, text)
        if matches:
            hints['quarter_hints'] = matches
            break
    return hints


def infer_due_date(category: str, text: str, report_year: int = None) -> str:
    """推断跟踪事项的到期日"""
    hints = extract_date_hints(text)
    now = datetime.now()

    # 如果有明确日期
    if 'explicit_dates' in hints:
        y, m, d = hints['explicit_dates'][0]
        return f"{y}-{int(m):02d}-{int(d):02d}"

    # 根据分类推断默认周期
    if category == "财务承诺":
        # 通常承诺有明确期限，假设年报后一年
        target_year = report_year or now.year
        return f"{target_year + 1}-04-30"
    elif category == "风险信号":
        return (now + timedelta(days=90)).strftime("%Y-%m-%d")
    elif category == "异常指标":
        # 下一季度报告
        quarter = (now.month - 1) // 3 + 1
        if quarter >= 4:
            return f"{now.year + 1}-04-30"
        else:
            next_q_month = (quarter + 1) * 3
            return f"{now.year}-{next_q_month:02d}-30"
    elif category == "关联交易":
        return (now + timedelta(days=180)).strftime("%Y-%m-%d")
    elif category == "会计变更":
        return (now + timedelta(days=365)).strftime("%Y-%m-%d")
    elif category == "监管动态":
        return (now + timedelta(days=30)).strftime("%Y-%m-%d")
    elif category == "重大事项":
        return (now + timedelta(days=60)).strftime("%Y-%m-%d")
    elif category == "行业变化":
        return (now + timedelta(days=180)).strftime("%Y-%m-%d")

    return (now + timedelta(days=90)).strftime("%Y-%m-%d")


def infer_threshold(category: str, text: str) -> str:
    """推断跟踪阈值"""
    # 尝试提取数字
    numbers = re.findall(r'([\d,]+\.?\d*)%?', text)
    if numbers:
        # 取最后一个数字作为参考
        num = numbers[-1].replace(',', '')
        try:
            val = float(num)
            if val > 100 and '万' in text:
                return f"金额变动 ±10% 或绝对值变化超过 {val * 0.1:.0f} 万元"
            elif val > 1:
                return f"指标变动 ±5% 或偏离度 >10%"
        except:
            pass

    defaults = {
        "财务承诺": "承诺完成度 < 90% 或延期",
        "风险信号": "风险等级升级或新增风险",
        "异常指标": "指标偏离度 > 15% 或趋势恶化",
        "关联交易": "交易金额变动 > 20% 或新增关联方",
        "会计变更": "再次变更或影响金额 > 5%",
        "监管动态": "新增监管措施或处罚",
        "重大事项": "事项进展偏离预期 > 30 天",
        "行业变化": "行业政策重大调整或市场份额变化 > 5%",
    }
    return defaults.get(category, "需人工设定阈值")


def extract_items_from_report(report_path: str, stock_code: str, company_name: str, company_dir: str = None) -> list:
    """从分析报告提取跟踪事项"""
    items = []

    if not os.path.exists(report_path):
        print(f"报告不存在: {report_path}")
        return items

    with open(report_path, 'r', encoding='utf-8') as f:
        content = f.read()

    analysis_refs = []
    if company_dir:
        analysis_refs = resolve_analysis_refs(Path(company_dir), os.path.basename(report_path))[:5]

    # 尝试提取报告年份
    year_match = re.search(r'(20\d{2})', os.path.basename(report_path))
    report_year = int(year_match.group(1)) if year_match else datetime.now().year

    # 按段落分割，识别潜在跟踪事项
    paragraphs = re.split(r'\n\n+', content)

    item_id = 0
    for para in paragraphs:
        para = para.strip()
        if len(para) < 20:
            continue

        # 启发式规则：包含风险/承诺/异常等关键词的段落
        trigger_words = ['风险', '承诺', '异常', '关联', '监管', '处罚', '诉讼', '重组', '并购', '变更', '关注', '警示']
        if not any(w in para for w in trigger_words):
            continue

        # 避免重复：检查是否已提取类似内容
        is_duplicate = False
        for existing in items:
            # 简单相似度检查
            if len(set(para.split()) & set(existing['description'].split())) > len(para.split()) * 0.7:
                is_duplicate = True
                break
        if is_duplicate:
            continue

        category = classify_item(para)
        item_id += 1

        item = {
            'id': f"{stock_code}-ITEM-{item_id:03d}",
            'category': category,
            'description': para[:500],  # 限制长度
            'source': os.path.basename(report_path),
            'source_file': f"analysis/{os.path.basename(report_path)}",
            'source_type': 'wiki_analysis',
            'source_refs': analysis_refs,
            'extracted_at': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'due_date': infer_due_date(category, para, report_year),
            'threshold': infer_threshold(category, para),
            'verification': DEFAULT_VERIFICATION.get(category, "人工复核"),
            'status': 'open',
            'priority': 'medium',
        }

        # 根据关键词调整优先级
        high_priority_keywords = ['重大风险', '立案调查', '非标', '持续经营', '破产', '清算', '重大违规']
        if any(kw in para for kw in high_priority_keywords):
            item['priority'] = 'high'

        items.append(item)

    return items


def extract_items_from_metrics(metrics_path: str, stock_code: str, company_dir: str = None) -> list:
    """从指标数据中提取异常指标跟踪事项"""
    items = []

    if not os.path.exists(metrics_path):
        return items

    with open(metrics_path, 'r', encoding='utf-8') as f:
        metrics = json.load(f)

    data = metrics.get('data', [])

    for metric in data:
        name = metric.get('name', '')
        canonical = metric.get('canonical_name', '')
        values = metric.get('values', {})

        if len(values) < 2:
            continue

        years = sorted(values.keys(), reverse=True)
        if len(years) < 2:
            continue

        latest_val = values.get(years[0])
        prev_val = values.get(years[1])

        if latest_val is None or prev_val is None or prev_val == 0:
            continue

        yoy_change = (latest_val - prev_val) / abs(prev_val)

        # 异常阈值判断。先识别疑似单位/口径错误，避免把数据质量问题误报为经营风险。
        is_abnormal = False
        abnormal_desc = ""
        data_quality_issue = looks_like_unit_or_scale_issue(canonical, latest_val, prev_val, yoy_change)

        if data_quality_issue:
            is_abnormal = True
            abnormal_desc = (
                f"{name}同比变动 {yoy_change*100:.1f}%，但最新值与上期值量级差异过大，"
                "疑似单位、口径或数据抽取异常，需先复核原始财报与 key_metrics.json"
            )
        elif canonical in {'net_profit', 'parent_net_profit'} and yoy_change < -0.3:
            is_abnormal = True
            abnormal_desc = f"净利润同比大幅下降 {yoy_change*100:.1f}%"
        elif canonical == 'gross_profit_margin' and yoy_change < -0.05:
            is_abnormal = True
            abnormal_desc = f"毛利率同比下降 {yoy_change*100:.1f}个百分点"
        elif canonical == 'roe' and yoy_change < -0.2:
            is_abnormal = True
            abnormal_desc = f"ROE同比大幅下降 {yoy_change*100:.1f}%"
        elif canonical == 'debt_ratio' and yoy_change > 0.1:
            is_abnormal = True
            abnormal_desc = f"资产负债率同比上升 {yoy_change*100:.1f}个百分点"
        elif abs(yoy_change) > 0.5:
            is_abnormal = True
            abnormal_desc = f"{name}同比变动 {yoy_change*100:.1f}%，超过50%阈值"

        if is_abnormal:
            source_refs = []
            if company_dir:
                source_refs = resolve_key_metric_refs(Path(company_dir), name or canonical, years[0])
            primary_ref = source_refs[0] if source_refs else {}
            item = {
                'id': f"{stock_code}-METRIC-{canonical}",
                'category': '异常指标',
                'description': abnormal_desc,
                'source': 'key_metrics.json',
                'source_file': 'metrics/key_metrics.json',
                'source_type': 'wiki_metrics',
                'source_refs': source_refs,
                'task_id': primary_ref.get('task_id'),
                'pdf_page': primary_ref.get('pdf_page'),
                'table_index': primary_ref.get('table_index'),
                'md_line': primary_ref.get('md_line'),
                'open_pdf_page_url': primary_ref.get('open_pdf_page_url'),
                'open_source_page_url': primary_ref.get('open_source_page_url'),
                'open_source_table_url': primary_ref.get('open_source_table_url'),
                'extracted_at': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                'due_date': infer_due_date('异常指标', abnormal_desc),
                'threshold': (
                    '先核实单位/口径/抽取结果；确认无误后再按指标偏离度 > 15% 或趋势持续恶化触发预警'
                    if data_quality_issue else '指标偏离度 > 15% 或趋势持续恶化'
                ),
                'verification': (
                    '核对 key_metrics.json、三大财务报表宽表及PDF原文，确认单位和报告口径一致'
                    if data_quality_issue else '对比下一季度/年度财报数据'
                ),
                'status': 'open',
                'priority': 'medium' if data_quality_issue else ('high' if abs(yoy_change) > 0.5 else 'medium'),
                'data_quality_issue': data_quality_issue,
                'metric_name': name,
                'canonical_name': canonical,
                'latest_value': latest_val,
                'previous_value': prev_val,
                'yoy_change_pct': round(yoy_change * 100, 2),
            }
            items.append(item)

    return items


def search_and_extract_items(stock_code: str, company_name: str, company_dir: str = None) -> list:
    """
    通过网络搜索辅助提取跟踪事项

    搜索公司最新风险信息、监管动态、重大事项等，补充到跟踪事项清单中。
    """
    items = []

    if not SEARCH_AVAILABLE:
        return items

    search = SearchTools()
    availability = search.check_availability()

    if not availability.get("any"):
        return items

    print(f"🔍 通过网络搜索辅助提取跟踪事项...")

    # 1. 搜索风险信息
    risk_result = search.search_company_risks(company_name, stock_code, max_results=5)
    if risk_result.get("success"):
        for i, result in enumerate(risk_result.get("results", [])):
            title = result.get("title", "")
            content = result.get("content", "")

            if not title:
                continue

            # 分类判断
            category = classify_item(title + " " + content)

            item = {
                'id': f"{stock_code}-WEB-{i+1:03d}",
                'category': category,
                'description': f"[网络搜索] {title}\n\n{content[:300]}..." if len(content) > 300 else f"[网络搜索] {title}\n\n{content}",
                'source': _extract_source_domain(result.get("url", "")),
                'source_file': f"web_search/{result.get('url', '')}",
                'source_type': 'web_search',
                'source_refs': [],
                'extracted_at': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                'due_date': infer_due_date(category, title),
                'threshold': infer_threshold(category, title),
                'verification': '通过网络搜索持续跟踪，核实原始来源',
                'status': 'open',
                'priority': 'high' if category in ['风险信号', '监管动态'] else 'medium',
                'web_url': result.get("url", ""),
                'search_backend': risk_result.get("backend", "unknown"),
            }
            items.append(item)

        print(f"  ✅ 从网络搜索提取 {len(risk_result.get('results', []))} 条风险/监管信息")

    # 2. 搜索最新公告
    news_result = search.search_company_news(company_name, stock_code, max_results=3)
    if news_result.get("success"):
        for i, result in enumerate(news_result.get("results", [])):
            title = result.get("title", "")
            content = result.get("content", "")

            if not title:
                continue

            # 只提取重大事项相关的
            category = classify_item(title + " " + content)
            if category not in ['重大事项', '财务承诺', '行业变化']:
                continue

            item = {
                'id': f"{stock_code}-WEB-NEWS-{i+1:03d}",
                'category': category,
                'description': f"[网络搜索-公告] {title}\n\n{content[:300]}..." if len(content) > 300 else f"[网络搜索-公告] {title}\n\n{content}",
                'source': _extract_source_domain(result.get("url", "")),
                'source_file': f"web_search/{result.get('url', '')}",
                'source_type': 'web_search',
                'source_refs': [],
                'extracted_at': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                'due_date': infer_due_date(category, title),
                'threshold': infer_threshold(category, title),
                'verification': '查阅公司官方公告核实',
                'status': 'open',
                'priority': 'medium',
                'web_url': result.get("url", ""),
                'search_backend': news_result.get("backend", "unknown"),
            }
            items.append(item)

        print(f"  ✅ 从网络搜索提取公告/重大事项信息")

    return items


def _extract_source_domain(url: str) -> str:
    """从 URL 提取来源域名"""
    if not url:
        return "网络搜索"

    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        domain = parsed.netloc.lower()

        domain_map = {
            "cninfo.com.cn": "巨潮资讯",
            "eastmoney.com": "东方财富",
            "cls.cn": "财联社",
            "xueqiu.com": "雪球",
            "sina.com.cn": "新浪财经",
            "sohu.com": "搜狐财经",
            "qq.com": "腾讯财经",
            "stcn.com": "证券时报",
            "cs.com.cn": "中证网",
        }

        for key, name in domain_map.items():
            if key in domain:
                return name

        return domain
    except Exception:
        return "网络搜索"


def generate_tracking_items(
    stock_code: str,
    company_name: str,
    wiki_base: str = DEFAULT_WIKI_BASE,
    use_search: bool = True,
) -> str:
    """
    主入口：为指定公司生成跟踪事项清单

    输入：
      - wiki/companies/<stock_code>-<company>/analysis/*.md 分析报告
      - wiki/companies/<stock_code>-<company>/metrics/key_metrics.json 指标数据

    输出：
      - wiki/companies/<stock_code>-<company>/tracking/tracking-items.md
    """
    company_dir = str(company_dir_path(wiki_base, stock_code, company_name))
    tracking_dir = os.path.join(company_dir, "tracking")

    os.makedirs(tracking_dir, exist_ok=True)

    all_items = []

    # 1. 从分析报告中提取
    analysis_dir = os.path.join(company_dir, "analysis")
    if os.path.exists(analysis_dir):
        for fname in os.listdir(analysis_dir):
            if fname.endswith('.md') and fname != 'README.md':
                report_path = os.path.join(analysis_dir, fname)
                items = extract_items_from_report(report_path, stock_code, company_name, company_dir)
                all_items.extend(items)
                print(f"  从 {fname} 提取 {len(items)} 项")

    # 2. 从指标数据中提取异常
    metrics_path = os.path.join(company_dir, "metrics", "key_metrics.json")
    metric_items = extract_items_from_metrics(metrics_path, stock_code, company_dir)
    all_items.extend(metric_items)
    print(f"  从 key_metrics.json 提取 {len(metric_items)} 项")

    # 3. 从网络搜索辅助提取
    if use_search:
        web_items = search_and_extract_items(stock_code, company_name, company_dir)
        all_items.extend(web_items)
        print(f"  从网络搜索提取 {len(web_items)} 项")
    else:
        print("  跳过网络搜索提取（use_search=False）")

    # 去重：按 description 相似度
    unique_items = []
    seen_descs = set()
    for item in all_items:
        desc_key = item['description'][:100]
        if desc_key not in seen_descs:
            seen_descs.add(desc_key)
            unique_items.append(item)

    # 生成输出
    output_path = os.path.join(tracking_dir, "tracking-items.md")

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(f"# {company_name} ({stock_code}) 跟踪事项清单\n\n")
        f.write(f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"> 来源: 分析报告 + 财务指标自动提取\n")
        f.write(f"> 事项总数: {len(unique_items)}\n\n")

        # 按分类汇总
        f.write("## 分类汇总\n\n")
        category_counts = {}
        for item in unique_items:
            cat = item['category']
            category_counts[cat] = category_counts.get(cat, 0) + 1
        for cat in TRACKING_CATEGORIES:
            count = category_counts.get(cat, 0)
            if count > 0:
                f.write(f"- **{cat}**: {count} 项\n")
        f.write("\n")

        # 按优先级排序
        priority_order = {'high': 0, 'medium': 1, 'low': 2}
        unique_items.sort(key=lambda x: priority_order.get(x.get('priority', 'medium'), 1))

        # 详细清单
        f.write("## 跟踪事项明细\n\n")
        for item in unique_items:
            priority_emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}
            emoji = priority_emoji.get(item.get('priority', 'medium'), "🟡")
            f.write(f"### {emoji} {item['id']} | {item['category']}\n\n")
            f.write(f"**描述**: {item['description']}\n\n")
            f.write(f"**来源**: {item['source']}")
            if item.get('web_url'):
                f.write(f" | [查看原文]({item['web_url']})")
            f.write("\n\n")

            source_refs = item.get('source_refs') or []
            if source_refs:
                f.write(f"**证据链**: {format_ref_summary(source_refs[:3])}\n")
                for ref in source_refs[:3]:
                    links = []
                    if ref.get('open_pdf_page_url'):
                        links.append(f"[打开PDF页]({ref['open_pdf_page_url']})")
                    if ref.get('open_source_page_url'):
                        links.append(f"[查看页来源]({ref['open_source_page_url']})")
                    if ref.get('open_source_table_url'):
                        links.append(f"[查看表格]({ref['open_source_table_url']})")
                    if links:
                        f.write(f"- {ref.get('source_type', item.get('source_type', 'source'))}: " + "，".join(links) + "\n")
                f.write("\n")
            elif item.get('source_type') in {'wiki_metrics', 'wiki_analysis'}:
                f.write("**证据链**: PDF 页码未返回/证据链不完整\n\n")
            f.write(f"**到期日**: {item['due_date']}\n\n")
            f.write(f"**阈值**: {item['threshold']}\n\n")
            f.write(f"**验证方式**: {item['verification']}\n\n")
            f.write(f"**状态**: {item['status']} | **优先级**: {item.get('priority', 'medium')}\n\n")

            # 如果有指标数据，附加显示
            if 'metric_name' in item:
                f.write(f"**指标数据**:\n")
                f.write(f"- 最新值: {item['latest_value']}\n")
                f.write(f"- 上期值: {item['previous_value']}\n")
                f.write(f"- 同比变动: {item['yoy_change_pct']}%\n\n")

            f.write("---\n\n")

        # YAML 结构化数据附录
        f.write("## 结构化数据 (YAML)\n\n")
        f.write("```yaml\n")
        yaml_data = {
            'stock_code': stock_code,
            'company_name': company_name,
            'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'item_count': len(unique_items),
            'items': _clean_yaml_scalar(deepcopy(unique_items)),
        }
        f.write(yaml.dump(yaml_data, allow_unicode=True, sort_keys=False))
        f.write("```\n")

    print(f"\n✅ 跟踪事项清单已生成: {output_path}")
    print(f"   共 {len(unique_items)} 项跟踪事项")
    return output_path


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="跟踪事项提取器")
    parser.add_argument("--stock", required=True, help="股票代码")
    parser.add_argument("--company", required=True, help="公司简称")
    parser.add_argument("--wiki-base", default=DEFAULT_WIKI_BASE, help="wiki 根目录")
    args = parser.parse_args()

    generate_tracking_items(args.stock, args.company, args.wiki_base)
