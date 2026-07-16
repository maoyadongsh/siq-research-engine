#!/usr/bin/env python3
"""
模块5: 报告更新器
根据跟踪结果生成更新记录。默认不改写原始分析报告；
如显式开启，则只维护一个"跟踪更新索引"区块。
"""

import os
import sys
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

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

from company_identity import company_dir_path, generated_report_filename

TRACKING_INDEX_START = "<!-- FINSIGHT_TRACKING_INDEX_START -->"
TRACKING_INDEX_END = "<!-- FINSIGHT_TRACKING_INDEX_END -->"


def find_latest_tracking_data(tracking_dir: str) -> dict:
    """查找最新的跟踪数据文件"""
    data = {
        "tracking_items": None,
        "sentiment": None,
        "metrics": None,
        "alerts": None,
    }

    # 跟踪事项
    items_path = os.path.join(tracking_dir, "tracking-items.md")
    if os.path.exists(items_path):
        data["tracking_items"] = items_path

    # 最新舆情
    sentiment_dir = os.path.join(tracking_dir, "sentiment")
    if os.path.exists(sentiment_dir):
        md_files = sorted([f for f in os.listdir(sentiment_dir) if f.endswith('.md')], reverse=True)
        if md_files:
            data["sentiment"] = os.path.join(sentiment_dir, md_files[0])

    # 最新指标
    metrics_dir = os.path.join(tracking_dir, "metrics")
    if os.path.exists(metrics_dir):
        md_files = sorted([f for f in os.listdir(metrics_dir) if f.endswith('.md')], reverse=True)
        if md_files:
            data["metrics"] = os.path.join(metrics_dir, md_files[0])

    # 最新预警
    alerts_dir = os.path.join(tracking_dir, "alerts")
    if os.path.exists(alerts_dir):
        md_files = sorted([f for f in os.listdir(alerts_dir) if f.endswith('.md')], reverse=True)
        if md_files:
            data["alerts"] = os.path.join(alerts_dir, md_files[0])

    return data


def read_file_head(path: str, lines: int = 50) -> str:
    """读取文件前N行"""
    if not path or not os.path.exists(path):
        return "*数据暂不可用*"
    with open(path, 'r', encoding='utf-8') as f:
        return ''.join(f.readlines()[:lines])


def generate_update_section(tracking_data: dict, update_date: str) -> str:
    """生成跟踪更新章节内容"""
    section = f"\n\n## 跟踪更新 ({update_date})\n\n"
    section += f"> 本章节由 finsight_tracking 系统自动生成\n"
    section += f"> 更新时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

    # 1. 跟踪事项状态
    if tracking_data.get("tracking_items"):
        section += "### 跟踪事项状态\n\n"
        section += f"详见: [`tracking-items.md`](../tracking-items.md)\n\n"
        # 提取摘要
        content = read_file_head(tracking_data["tracking_items"], 30)
        # 提取分类汇总部分
        lines = content.split('\n')
        in_summary = False
        for line in lines:
            if '分类汇总' in line:
                in_summary = True
            if in_summary:
                section += line + '\n'
            if in_summary and line.strip() == '' and '分类汇总' not in line:
                break
        section += '\n'

    # 2. 最新舆情摘要
    if tracking_data.get("sentiment"):
        section += "### 最新舆情摘要\n\n"
        sentiment_file = os.path.basename(tracking_data["sentiment"])
        section += f"详见: [`sentiment/{sentiment_file}`](../sentiment/{sentiment_file})\n\n"
        content = read_file_head(tracking_data["sentiment"], 40)
        # 提取摘要部分
        lines = content.split('\n')
        in_summary = False
        for line in lines:
            if '舆情摘要' in line:
                in_summary = True
            if in_summary and line.startswith('##'):
                continue
            if in_summary:
                section += line + '\n'
            if in_summary and '情感分布' in line:
                break
        section += '\n'

    # 3. 指标变动摘要
    if tracking_data.get("metrics"):
        section += "### 指标变动摘要\n\n"
        metrics_file = os.path.basename(tracking_data["metrics"])
        section += f"详见: [`metrics/{metrics_file}`](../metrics/{metrics_file})\n\n"
        content = read_file_head(tracking_data["metrics"], 50)
        # 提取关键指标表
        lines = content.split('\n')
        in_table = False
        for line in lines:
            if '关键指标概览' in line:
                in_table = True
            if in_table:
                section += line + '\n'
            if in_table and line.strip() == '' and '|' not in line:
                break
        section += '\n'

    # 4. 预警状态
    if tracking_data.get("alerts"):
        section += "### 预警状态\n\n"
        alert_file = os.path.basename(tracking_data["alerts"])
        section += f"⚠️ **存在活跃预警** - 详见: [`alerts/{alert_file}`](../alerts/{alert_file})\n\n"
        content = read_file_head(tracking_data["alerts"], 40)
        lines = content.split('\n')
        in_alerts = False
        for line in lines:
            if '预警摘要' in line or ('###' in line and any(l in line for l in ['严重', '警告', '关注'])):
                in_alerts = True
            if in_alerts:
                section += line + '\n'
            if in_alerts and line.strip().startswith('## ') and '建议措施' in line:
                break
        section += '\n'
    else:
        section += "### 预警状态\n\n"
        section += "✅ 当前无活跃预警\n\n"

    return section


def update_report(
    stock_code: str,
    company_name: str,
    wiki_base: str = DEFAULT_WIKI_BASE,
    report_type: str = "analysis",
    update_date: str = None,
    use_search: bool = False,
    update_analysis: bool = False,
) -> Optional[str]:
    """
    生成跟踪更新记录；可选维护原始报告中的跟踪索引区块。

    输入:
      - wiki/companies/<stock_code>-<company>/analysis/*.md
      - wiki/tracking/<stock_code>-<company>/ 下的跟踪数据

    输出:
      - wiki/tracking/<stock_code>-<company>/updates/<date>-update.md
      - 可选更新原始报告中的固定索引区块
    """
    if update_date is None:
        update_date = datetime.now().strftime("%Y-%m-%d")

    company_dir = str(company_dir_path(wiki_base, stock_code, company_name))
    tracking_dir = os.path.join(company_dir, "tracking")
    updates_dir = os.path.join(tracking_dir, "updates")
    os.makedirs(updates_dir, exist_ok=True)

    # 查找跟踪数据
    tracking_data = find_latest_tracking_data(tracking_dir)

    # 如果使用搜索，补充最新信息
    if use_search:
        try:
            from search_tools import SearchTools
            search = SearchTools()
            availability = search.check_availability()

            if availability.get("any"):
                print(f"🔍 搜索最新信息补充更新...")
                # 搜索公司最新动态
                result = search.search_company_news(company_name, stock_code, max_results=5)
                if result.get("success"):
                    # 将搜索结果添加到更新内容中
                    search_results = result.get("results", [])
                    if search_results:
                        tracking_data["search_results"] = search_results
                        print(f"  ✅ 获取 {len(search_results)} 条最新信息")
        except ImportError:
            print("⚠️ search_tools 模块未找到，跳过信息搜索")

    # 生成更新内容
    update_content = generate_update_section(tracking_data, update_date)

    # 如果有搜索结果，追加到更新内容
    if "search_results" in tracking_data:
        update_content += "\n### 最新动态（网络搜索）\n\n"
        for i, result in enumerate(tracking_data["search_results"][:3], 1):
            update_content += f"{i}. [{result.get('title', 'N/A')}]({result.get('url', '')})\n"
            if result.get("content"):
                update_content += f"   {result['content'][:100]}...\n"
        update_content += "\n"

    # 1. 保存独立更新记录
    update_record_path = os.path.join(updates_dir, f"{update_date}-update.md")
    with open(update_record_path, 'w', encoding='utf-8') as f:
        f.write(f"# {company_name} ({stock_code}) 跟踪更新记录\n\n")
        f.write(f"> 更新日期: {update_date}\n")
        f.write(f"> 类型: 自动跟踪更新\n\n")
        f.write(update_content)

    print(f"✅ 更新记录已保存: {update_record_path}")

    # 2. 可选维护原始分析报告中的固定索引区块
    if not update_analysis:
        print("ℹ️ 默认不改写 analysis 报告；如需写回请使用 --update-analysis")
        return update_record_path

    analysis_dir = os.path.join(company_dir, "analysis")
    if os.path.exists(analysis_dir):
        for fname in os.listdir(analysis_dir):
            if fname.endswith('.md') and fname != 'README.md':
                report_path = os.path.join(analysis_dir, fname)

                # 读取原报告
                with open(report_path, 'r', encoding='utf-8') as f:
                    original_content = f.read()

                archive_dir = os.path.join(updates_dir, "archive")
                os.makedirs(archive_dir, exist_ok=True)
                archive_name = generated_report_filename(stock_code, company_name, "analysis-archive", update_date, Path(fname).suffix or ".md")
                archive_path = os.path.join(archive_dir, archive_name)
                shutil.copy2(report_path, archive_path)
                print(f"   原报告已归档: {archive_path}")

                index_block = (
                    f"\n\n{TRACKING_INDEX_START}\n"
                    f"## 跟踪更新索引\n\n"
                    f"- 最新更新记录: [tracking/updates/{update_date}-update.md](../tracking/updates/{update_date}-update.md)\n"
                    f"- 最新跟踪事项: [tracking/tracking-items.md](../tracking/tracking-items.md)\n"
                    f"- 更新时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                    f"{TRACKING_INDEX_END}\n"
                )

                if TRACKING_INDEX_START in original_content and TRACKING_INDEX_END in original_content:
                    start = original_content.index(TRACKING_INDEX_START)
                    end = original_content.index(TRACKING_INDEX_END) + len(TRACKING_INDEX_END)
                    updated_content = original_content[:start].rstrip() + index_block + original_content[end:].lstrip()
                else:
                    updated_content = original_content.rstrip() + index_block

                with open(report_path, 'w', encoding='utf-8') as f:
                    f.write(updated_content)

                print(f"   原报告已更新: {report_path}")

    return update_record_path


def run_report_updater(
    stock_code: str,
    company_name: str,
    wiki_base: str = DEFAULT_WIKI_BASE,
    date: str = None,
    use_search: bool = False,
    update_analysis: bool = False,
) -> Optional[str]:
    """
    主入口：运行报告更新器

    Args:
        use_search: 是否使用网络搜索补充最新信息
    """
    return update_report(stock_code, company_name, wiki_base, "analysis", date, use_search, update_analysis)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="报告更新器")
    parser.add_argument("--stock", required=True, help="股票代码")
    parser.add_argument("--company", required=True, help="公司简称")
    parser.add_argument("--date", help="更新日期 (YYYY-MM-DD)")
    parser.add_argument("--wiki-base", default=DEFAULT_WIKI_BASE, help="wiki 根目录")
    parser.add_argument("--update-analysis", action="store_true", help="维护 analysis 报告中的跟踪更新索引")
    args = parser.parse_args()

    run_report_updater(args.stock, args.company, args.wiki_base, args.date, update_analysis=args.update_analysis)
