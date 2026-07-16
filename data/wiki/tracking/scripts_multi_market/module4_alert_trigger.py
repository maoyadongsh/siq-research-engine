#!/usr/bin/env python3
"""
模块4: 预警触发器
四级预警：INFO/WATCH/WARNING/CRITICAL
触发条件：突破阈值/重大负面舆情/监管处罚

输出：预警报告 + 通知
"""

import json
import os
import sys
import yaml
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

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


# 四级预警定义
ALERT_LEVELS = {
    "INFO": {
        "name": "信息",
        "emoji": "🔵",
        "description": "一般性信息更新，无需立即行动",
        "color": "#3b82f6",
        "notify_channels": ["log"],
    },
    "WATCH": {
        "name": "关注",
        "emoji": "🟡",
        "description": "需要关注的信号，建议定期复查",
        "color": "#eab308",
        "notify_channels": ["log", "daily_report"],
    },
    "WARNING": {
        "name": "警告",
        "emoji": "🟠",
        "description": "明确的预警信号，需要采取跟进措施",
        "color": "#f97316",
        "notify_channels": ["log", "daily_report", "alert_report"],
    },
    "CRITICAL": {
        "name": "严重",
        "emoji": "🔴",
        "description": "严重预警，需要立即关注和处理",
        "color": "#ef4444",
        "notify_channels": ["log", "daily_report", "alert_report", "urgent_notify"],
    },
}

# 预警规则定义
#
# 每条规则现在带 `predicate(ctx)` 可调用谓词，规则与执行器解耦：
# 1) 添加新规则只需 append；evaluate_rules 不再分支硬编码。
# 2) `condition` 字符串保留为人读说明，不再被求值，避免 v1 的"字符串与代码漂移"。
# 3) `details_extractor(ctx)` 在 triggered=True 时生成 details；不存在时返回空 dict。
ALERT_RULES = [
    {
        "id": "RULE-001",
        "name": "净利润大幅下滑",
        "category": "异常指标",
        "condition": "net_profit_yoy < -30",
        "level": "WARNING",
        "message": "归母净利润同比下滑超过30%",
        "predicate": lambda ctx: ctx.get("net_profit_yoy") is not None and ctx["net_profit_yoy"] < -30,
        "details_extractor": lambda ctx: {"net_profit_yoy": ctx.get("net_profit_yoy")},
    },
    {
        "id": "RULE-002",
        "name": "净利润连续下滑",
        "category": "异常指标",
        "condition": "net_profit_yoy < -10 AND previous_net_profit_yoy < -10",
        "level": "CRITICAL",
        "message": "归母净利润连续两期同比下滑超过10%",
        "predicate": lambda ctx: (
            ctx.get("net_profit_yoy") is not None
            and ctx.get("previous_net_profit_yoy") is not None
            and ctx["net_profit_yoy"] < -10
            and ctx["previous_net_profit_yoy"] < -10
        ),
        "details_extractor": lambda ctx: {
            "net_profit_yoy": ctx.get("net_profit_yoy"),
            "previous_net_profit_yoy": ctx.get("previous_net_profit_yoy"),
        },
    },
    {
        "id": "RULE-003",
        "name": "毛利率异常下降",
        "category": "异常指标",
        "condition": "gross_margin_yoy < -5",
        "level": "WATCH",
        "message": "毛利率同比下降超过5个百分点",
        "predicate": lambda ctx: ctx.get("gross_margin_yoy") is not None and ctx["gross_margin_yoy"] < -5,
        "details_extractor": lambda ctx: {"gross_margin_yoy": ctx.get("gross_margin_yoy")},
    },
    {
        "id": "RULE-004",
        "name": "资产负债率上升",
        "category": "异常指标",
        "condition": "debt_ratio_yoy > 10",
        "level": "WATCH",
        "message": "资产负债率同比上升超过10个百分点",
        "predicate": lambda ctx: ctx.get("debt_ratio_yoy") is not None and ctx["debt_ratio_yoy"] > 10,
        "details_extractor": lambda ctx: {"debt_ratio_yoy": ctx.get("debt_ratio_yoy")},
    },
    {
        "id": "RULE-005",
        "name": "经营现金流恶化",
        "category": "异常指标",
        "condition": "operating_cash_flow_yoy < -50",
        "level": "WARNING",
        "message": "经营活动现金流同比下滑超过50%",
        "predicate": lambda ctx: ctx.get("operating_cash_flow_yoy") is not None and ctx["operating_cash_flow_yoy"] < -50,
        "details_extractor": lambda ctx: {"operating_cash_flow_yoy": ctx.get("operating_cash_flow_yoy")},
    },
    {
        "id": "RULE-006",
        "name": "负面舆情激增",
        "category": "舆情",
        "condition": "negative_sentiment_count >= 3",
        "level": "WATCH",
        "message": "单日负面舆情达到3条及以上",
        "predicate": lambda ctx: ctx.get("negative_count", 0) >= 3,
        "details_extractor": lambda ctx: {"negative_count": ctx.get("negative_count")},
    },
    {
        "id": "RULE-007",
        "name": "重大负面舆情",
        "category": "舆情",
        "condition": "critical_negative_sentiment == True",
        "level": "WARNING",
        "message": "出现重大负面舆情（监管/处罚/立案调查）",
        "predicate": lambda ctx: bool(ctx.get("critical_negative")),
        "details_extractor": lambda ctx: {"critical_negative": ctx.get("critical_negative")},
    },
    {
        "id": "RULE-008",
        "name": "跟踪事项到期",
        "category": "跟踪",
        "condition": "tracking_item_due_within_days <= 7",
        "level": "INFO",
        "message": "跟踪事项将在7天内到期",
        "predicate": lambda ctx: len(ctx.get("due_soon_items", [])) > 0,
        "details_extractor": lambda ctx: {
            "due_soon_count": len(ctx.get("due_soon_items", [])),
            "items": [i.get("id") for i in ctx.get("due_soon_items", [])[:3]],
        },
    },
    {
        "id": "RULE-009",
        "name": "跟踪事项超期",
        "category": "跟踪",
        "condition": "tracking_item_overdue == True",
        "level": "WARNING",
        "message": "存在已超期的跟踪事项",
        "predicate": lambda ctx: len(ctx.get("overdue_items", [])) > 0,
        "details_extractor": lambda ctx: {
            "overdue_count": len(ctx.get("overdue_items", [])),
            "items": [i.get("id") for i in ctx.get("overdue_items", [])[:3]],
        },
    },
    {
        "id": "RULE-010",
        "name": "ROE持续低迷",
        "category": "异常指标",
        "condition": "roe < 5",
        "level": "WATCH",
        "message": "ROE低于5%",
        "predicate": lambda ctx: ctx.get("roe") is not None and ctx["roe"] < 5,
        "details_extractor": lambda ctx: {"roe": ctx.get("roe")},
    },
]


def load_tracking_data(tracking_dir: str) -> Dict:
    """加载跟踪数据"""
    data = {
        "items": [],
        "sentiment": [],
        "metrics": {},
    }

    # 加载跟踪事项
    items_path = os.path.join(tracking_dir, "tracking-items.md")
    if os.path.exists(items_path):
        # 从 Markdown 中提取 YAML 数据
        with open(items_path, 'r', encoding='utf-8') as f:
            content = f.read()
        # 查找 YAML 代码块
        yaml_start = content.find("```yaml")
        if yaml_start >= 0:
            yaml_end = content.find("```", yaml_start + 7)
            if yaml_end >= 0:
                yaml_content = content[yaml_start + 7:yaml_end].strip()
                try:
                    parsed = yaml.safe_load(yaml_content)
                    if parsed and "items" in parsed:
                        data["items"] = parsed["items"]
                except Exception as e:
                    print(f"解析 tracking-items.md YAML 失败: {e}")

    # 加载最新舆情
    sentiment_dir = os.path.join(tracking_dir, "sentiment")
    if os.path.exists(sentiment_dir):
        # 找最新的舆情文件
        md_files = sorted([f for f in os.listdir(sentiment_dir) if f.endswith('.md')], reverse=True)
        if md_files:
            latest_sentiment = os.path.join(sentiment_dir, md_files[0])
            with open(latest_sentiment, 'r', encoding='utf-8') as f:
                content = f.read()
            # 查找 JSON 代码块
            json_start = content.find("```json")
            if json_start >= 0:
                json_end = content.find("```", json_start + 7)
                if json_end >= 0:
                    json_content = content[json_start + 7:json_end].strip()
                    try:
                        data["sentiment"] = json.loads(json_content)
                    except Exception as e:
                        print(f"解析舆情 JSON 失败: {e}")

    # 加载最新指标
    metrics_dir = os.path.join(tracking_dir, "metrics")
    if os.path.exists(metrics_dir):
        md_files = sorted([f for f in os.listdir(metrics_dir) if f.endswith('.md')], reverse=True)
        if md_files:
            latest_metrics = os.path.join(metrics_dir, md_files[0])
            with open(latest_metrics, 'r', encoding='utf-8') as f:
                content = f.read()
            json_start = content.find("```json")
            if json_start >= 0:
                json_end = content.find("```", json_start + 7)
                if json_end >= 0:
                    json_content = content[json_start + 7:json_end].strip()
                    try:
                        metrics_list = json.loads(json_content)
                        for m in metrics_list:
                            data["metrics"][m.get("canonical_name", "")] = m
                    except Exception as e:
                        print(f"解析指标 JSON 失败: {e}")

    return data


def evaluate_rules(data: Dict) -> List[Dict]:
    """评估所有预警规则"""
    alerts = []

    metrics = data.get("metrics", {})
    sentiment = data.get("sentiment", [])
    items = data.get("items", [])

    # 计算辅助变量
    net_profit_yoy = None
    net_profit_metric = metrics.get("net_profit") or metrics.get("parent_net_profit")
    if net_profit_metric:
        net_profit_yoy = net_profit_metric.get("latest_yoy")

    gross_margin_yoy = None
    if "gross_profit_margin" in metrics:
        gross_margin_yoy = metrics["gross_profit_margin"].get("latest_yoy")

    debt_ratio_yoy = None
    if "debt_ratio" in metrics:
        debt_ratio_yoy = metrics["debt_ratio"].get("latest_yoy")

    roe = None
    if "roe" in metrics:
        roe = metrics["roe"].get("latest_value")

    operating_cash_flow_yoy = None
    cash_flow_metric = metrics.get("cash_flow_operating") or metrics.get("operating_cash_flow_net")
    if cash_flow_metric:
        operating_cash_flow_yoy = cash_flow_metric.get("latest_yoy")

    previous_net_profit_yoy = None
    if net_profit_metric:
        yoy_map = net_profit_metric.get("changes", {}).get("yoy", {})
        yoy_years = sorted(yoy_map.keys())
        if len(yoy_years) >= 2:
            previous_net_profit_yoy = yoy_map.get(yoy_years[-2])

    metric_refs_by_rule = {
        "RULE-001": (net_profit_metric or {}).get("source_refs") or [],
        "RULE-002": (net_profit_metric or {}).get("source_refs") or [],
        "RULE-003": (metrics.get("gross_profit_margin") or {}).get("source_refs") or [],
        "RULE-004": (metrics.get("debt_ratio") or {}).get("source_refs") or [],
        "RULE-005": (cash_flow_metric or {}).get("source_refs") or [],
        "RULE-010": (metrics.get("roe") or {}).get("source_refs") or [],
    }

    negative_count = sum(1 for s in sentiment if s.get("sentiment") == "负面")
    critical_negative = any(
        kw in s.get("content", "")
        for s in sentiment
        if s.get("sentiment") == "负面"
        for kw in ["立案", "调查", "处罚", "监管", "非标", "保留意见"]
    )

    now = datetime.now()
    due_soon_items = [
        item for item in items
        if item.get("status") == "open"
        and "due_date" in item
        and (datetime.strptime(item["due_date"], "%Y-%m-%d") - now).days <= 7
        and (datetime.strptime(item["due_date"], "%Y-%m-%d") - now).days >= 0
    ]
    overdue_items = [
        item for item in items
        if item.get("status") == "open"
        and "due_date" in item
        and datetime.strptime(item["due_date"], "%Y-%m-%d") < now
    ]

    # 装入上下文，供每条规则的 predicate / details_extractor 使用。
    ctx = {
        "net_profit_yoy": net_profit_yoy,
        "previous_net_profit_yoy": previous_net_profit_yoy,
        "gross_margin_yoy": gross_margin_yoy,
        "debt_ratio_yoy": debt_ratio_yoy,
        "operating_cash_flow_yoy": operating_cash_flow_yoy,
        "roe": roe,
        "negative_count": negative_count,
        "critical_negative": critical_negative,
        "due_soon_items": due_soon_items,
        "overdue_items": overdue_items,
    }

    # 评估每条规则（去硬编码分支；新增/删除规则只改 ALERT_RULES）
    for rule in ALERT_RULES:
        predicate = rule.get("predicate")
        if not callable(predicate):
            # 兼容老规则：缺 predicate 时跳过并提示。
            print(f"⚠️ 规则 {rule.get('id')} 缺少 predicate，已跳过。")
            continue
        try:
            triggered = bool(predicate(ctx))
        except Exception as exc:
            print(f"⚠️ 规则 {rule.get('id')} 求值失败: {exc}")
            continue

        if not triggered:
            continue

        details_extractor = rule.get("details_extractor")
        details = {}
        if callable(details_extractor):
            try:
                extracted = details_extractor(ctx)
                if isinstance(extracted, dict):
                    details = extracted
            except Exception as exc:
                print(f"⚠️ 规则 {rule.get('id')} details 抽取失败: {exc}")

        identity_raw = os.environ.get("SIQ_TRACKING_RESEARCH_IDENTITY", "")
        try:
            research_identity = json.loads(identity_raw) if identity_raw else None
        except json.JSONDecodeError:
            research_identity = None
        alert = {
            "rule_id": rule["id"],
            "rule_name": rule["name"],
            "category": rule["category"],
            "level": rule["level"],
            "message": rule["message"],
            "details": details,
            "triggered_at": datetime.now().astimezone().isoformat(),
        }
        if isinstance(research_identity, dict):
            alert.update(
                {
                    "research_identity": research_identity,
                    "analysis_artifact_id": os.environ.get("SIQ_TRACKING_ANALYSIS_ARTIFACT_ID", ""),
                    "source_family": os.environ.get("SIQ_TRACKING_SOURCE_FAMILY", ""),
                    "adapter_version": "market_tracking_v1",
                }
            )
        source_refs = metric_refs_by_rule.get(rule["id"]) or []
        if source_refs:
            alert["source_refs"] = source_refs[:5]
            alert["evidence_refs"] = source_refs[:5]
            alert["data_sources"] = source_refs[:5]
        alerts.append(alert)

    return alerts


def verify_alerts_with_search(
    alerts: List[Dict],
    stock_code: str,
    company_name: str,
) -> List[Dict]:
    """
    通过网络搜索验证预警信息

    对已触发的预警进行网络搜索验证，补充最新信息和证据。
    """
    if not SEARCH_AVAILABLE:
        return alerts

    search = SearchTools()
    availability = search.check_availability()

    if not availability.get("any"):
        return alerts

    print(f"🔍 通过网络搜索验证预警信息...")

    for alert in alerts:
        rule_name = alert.get("rule_name", "")
        message = alert.get("message", "")

        # 根据预警类型构建搜索查询
        if alert.get("category") == "异常指标":
            query = f"{company_name} {stock_code} {message} 最新"
        elif alert.get("category") == "舆情":
            query = f"{company_name} {stock_code} 负面舆情 最新进展"
        else:
            query = f"{company_name} {stock_code} {rule_name}"

        try:
            result = search.search(query, backend="tavily", max_results=3, search_depth="basic")

            if result.get("success") and result.get("results"):
                # 添加验证信息到预警详情
                verification_results = []
                for r in result.get("results", [])[:2]:
                    verification_results.append({
                        "title": r.get("title", ""),
                        "url": r.get("url", ""),
                        "snippet": r.get("content", "")[:200] + "..." if len(r.get("content", "")) > 200 else r.get("content", ""),
                    })

                alert["verification"] = {
                    "searched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "query": query,
                    "results": verification_results,
                    "backend": result.get("backend", "unknown"),
                }
        except Exception as e:
            print(f"  ⚠️ 验证预警 '{rule_name}' 时出错: {e}")

    verified_count = sum(1 for a in alerts if "verification" in a)
    print(f"  ✅ 已验证 {verified_count}/{len(alerts)} 条预警")

    return alerts


def generate_alert_report(
    stock_code: str,
    company_name: str,
    alerts: List[Dict],
    output_dir: str,
    date: str = None,
) -> str:
    """生成预警报告"""
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    os.makedirs(output_dir, exist_ok=True)

    # 生成唯一编号
    existing_alerts = [f for f in os.listdir(output_dir) if f.startswith(date)]
    alert_seq = len(existing_alerts) + 1

    # 确定最高预警级别
    max_level = "INFO"
    level_priority = {"INFO": 0, "WATCH": 1, "WARNING": 2, "CRITICAL": 3}
    for alert in alerts:
        if level_priority.get(alert["level"], 0) > level_priority.get(max_level, 0):
            max_level = alert["level"]

    level_info = ALERT_LEVELS.get(max_level, ALERT_LEVELS["INFO"])
    filename = f"{date}-{max_level.lower()}-{alert_seq:03d}.md"
    output_path = os.path.join(output_dir, filename)

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(f"# {level_info['emoji']} {company_name} ({stock_code}) 预警报告\n\n")
        f.write(f"> 预警级别: **{max_level}** - {level_info['name']}\n")
        f.write(f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"> 触发规则数: {len(alerts)}\n\n")

        # 预警摘要
        f.write("## 预警摘要\n\n")
        f.write(f"本次监控共触发 **{len(alerts)}** 条预警规则，最高级别为 **{level_info['name']}**。\n\n")

        # 按级别分组
        for level in ["CRITICAL", "WARNING", "WATCH", "INFO"]:
            level_alerts = [a for a in alerts if a["level"] == level]
            if not level_alerts:
                continue

            info = ALERT_LEVELS.get(level, ALERT_LEVELS["INFO"])
            f.write(f"### {info['emoji']} {info['name']} ({len(level_alerts)}条)\n\n")

            for alert in level_alerts:
                f.write(f"**{alert['rule_name']}** (`{alert['rule_id']}`)\n\n")
                f.write(f"- 消息: {alert['message']}\n")
                f.write(f"- 分类: {alert['category']}\n")
                if alert.get("details"):
                    f.write(f"- 详情: {json.dumps(alert['details'], ensure_ascii=False)}\n")
                if alert.get("verification"):
                    f.write(f"- 网络验证: {alert['verification']['searched_at']}\n")
                    f.write(f"- 验证查询: {alert['verification']['query']}\n")
                    if alert["verification"].get("results"):
                        f.write("- 验证结果:\n")
                        for vr in alert["verification"]["results"]:
                            f.write(f"  - [{vr.get('title', 'N/A')[:50]}]({vr.get('url', '')})\n")
                            if vr.get("snippet"):
                                f.write(f"    {vr['snippet'][:100]}...\n")
                f.write(f"- 触发时间: {alert['triggered_at']}\n\n")

        # 建议措施
        f.write("## 建议措施\n\n")
        if max_level == "CRITICAL":
            f.write("🔴 **立即行动**:\n")
            f.write("1. 召集专项会议评估影响\n")
            f.write("2. 联系公司IR获取最新信息\n")
            f.write("3. 复核投资假设、风险暴露和组合影响，并提交人工审阅记录\n")
            f.write("4. 更新风险评估模型与跟踪阈值\n")
        elif max_level == "WARNING":
            f.write("🟠 **跟进处理**:\n")
            f.write("1. 深入分析预警原因\n")
            f.write("2. 设定复查时间表\n")
            f.write("3. 关注后续公告和舆情\n")
        elif max_level == "WATCH":
            f.write("🟡 **持续观察**:\n")
            f.write("1. 纳入日常监控范围\n")
            f.write("2. 设定复查提醒\n")
        else:
            f.write("🔵 **记录存档**:\n")
            f.write("1. 更新跟踪日志\n")
            f.write("2. 定期复查\n")

        f.write("\n")

        # 原始数据
        f.write("## 原始数据\n\n")
        f.write("```json\n")
        f.write(json.dumps(alerts, ensure_ascii=False, indent=2))
        f.write("\n```\n")

    print(f"✅ 预警报告已生成: {output_path}")
    print(f"   最高级别: {max_level}，共 {len(alerts)} 条预警")
    return output_path


def run_alert_trigger(
    stock_code: str,
    company_name: str,
    wiki_base: str = DEFAULT_WIKI_BASE,
    date: str = None,
    use_search: bool = True,
) -> Optional[str]:
    """
    主入口：运行预警触发器

    输入：
      - wiki/tracking/<stock_code>-<company>/tracking-items.md
      - wiki/tracking/<stock_code>-<company>/sentiment/*.md
      - wiki/tracking/<stock_code>-<company>/metrics/*.md

    输出：
      - wiki/tracking/<stock_code>-<company>/alerts/<date>-<level>-<seq>.md

    Args:
        use_search: 是否使用网络搜索验证预警信息（默认启用）
    """
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    company_dir = str(company_dir_path(wiki_base, stock_code, company_name))
    tracking_dir = os.path.join(company_dir, "tracking")
    alerts_dir = os.path.join(tracking_dir, "alerts")

    if not os.path.exists(tracking_dir):
        print(f"❌ 跟踪目录不存在: {tracking_dir}")
        return None

    # 加载数据
    print(f"📊 加载跟踪数据...")
    data = load_tracking_data(tracking_dir)
    print(f"   跟踪事项: {len(data['items'])} 条")
    print(f"   舆情数据: {len(data['sentiment'])} 条")
    print(f"   指标数据: {len(data['metrics'])} 项")

    # 评估规则
    print(f"🔍 评估预警规则...")
    alerts = evaluate_rules(data)

    # 通过网络搜索验证预警
    if use_search:
        alerts = verify_alerts_with_search(alerts, stock_code, company_name)

    if not alerts:
        print("✅ 未触发任何预警")
        return None

    # 生成报告
    return generate_alert_report(stock_code, company_name, alerts, alerts_dir, date)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="预警触发器")
    parser.add_argument("--stock", required=True, help="股票代码")
    parser.add_argument("--company", required=True, help="公司简称")
    parser.add_argument("--date", help="日期 (YYYY-MM-DD)")
    parser.add_argument("--wiki-base", default=DEFAULT_WIKI_BASE, help="wiki 根目录")
    args = parser.parse_args()

    run_alert_trigger(args.stock, args.company, args.wiki_base, args.date)
