#!/usr/bin/env python3
"""
HTML报告独立生成器
基于已有跟踪数据重新生成合并HTML报告，无需重新运行五大模块。

用法:
    python html_generator.py --stock 000063 --company 中兴通讯
    python html_generator.py --stock 000063 --company 中兴通讯 --date 2025-05-16
    python html_generator.py --stock 000063 --company 中兴通讯 --force  # 强制覆盖

工作规则:
    规则7: 必须通过模块6或本脚本生成合并HTML报告，禁止手工创建HTML
    规则8: 统一浅色审阅式CSS，包含首屏状态面板、统计卡片、可折叠区块、响应式布局
    规则9: 必须包含跟踪事项、指标追踪、舆情监控、预警状态、更新记录五大板块
"""

import argparse
import os
import sys
from datetime import datetime

# 添加脚本目录到路径
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from finsight_tracking_rules import (
    WIKI_BASE,
    configure_wiki_base,
    get_tracking_dir,
    get_report_path,
    validate_html_report_exists,
    validate_html_content,
    delete_manual_html_files,
    enforce_single_report_policy,
)
from module6_html_reporter import run_html_reporter


def generate_html(
    stock_code: str,
    company_name: str,
    wiki_base: str = WIKI_BASE,
    report_date: str = None,
    force: bool = False,
) -> str:
    """
    基于已有跟踪数据生成合并HTML报告

    参数:
        stock_code: 股票代码
        company_name: 公司简称
        wiki_base: wiki根目录
        report_date: 报告日期 (默认今天)
        force: 是否强制覆盖已有报告

    返回:
        生成的HTML文件路径
    """
    wiki_base = configure_wiki_base(wiki_base)

    if report_date is None:
        report_date = datetime.now().strftime("%Y-%m-%d")

    tracking_dir = get_tracking_dir(stock_code, company_name)

    # 检查跟踪数据是否存在
    if not os.path.exists(tracking_dir):
        print(f"❌ 跟踪目录不存在: {tracking_dir}")
        print("   请先运行 run_all.py 或各模块生成跟踪数据")
        return None

    # 检查是否有跟踪数据文件
    tracking_items_path = os.path.join(tracking_dir, "tracking-items.md")
    if not os.path.exists(tracking_items_path):
        print(f"⚠️ 警告: 跟踪事项文件不存在: {tracking_items_path}")
        print("   HTML报告将包含空板块")

    # 检查是否已有报告
    report_path = get_report_path(stock_code, company_name, report_date)
    if os.path.exists(report_path) and not force:
        print(f"⚠️ 报告已存在: {report_path}")
        print("   使用 --force 覆盖")
        return report_path

    # 清理手工HTML文件（规则7）
    print("🧹 清理手工HTML文件...")
    deleted = delete_manual_html_files(stock_code, company_name)
    if deleted:
        for f in deleted:
            print(f"   🗑️  删除: {f}")

    # 强制执行单报告原则（规则4）
    enforce_single_report_policy(stock_code, company_name, report_date)

    # 生成HTML报告
    print(f"\n🌐 生成HTML报告...")
    try:
        output_path = run_html_reporter(stock_code, company_name, wiki_base, report_date)
    except Exception as e:
        print(f"❌ HTML生成失败: {e}")
        return None

    # 验证HTML内容（规则8+9）
    print("\n🔍 验证HTML内容合规性...")
    ok, issues = validate_html_content(stock_code, company_name, report_date)
    if ok:
        print("✅ HTML内容合规（规则7-9）")
    else:
        print("⚠️ HTML内容违规:")
        for issue in issues:
            print(f"   - {issue}")

    print(f"\n✅ HTML报告已生成: {output_path}")
    return output_path


def regenerate_all(wiki_base: str = WIKI_BASE, force: bool = False):
    """
    为所有已配置跟踪的公司重新生成HTML报告
    """
    from finsight_tracking_rules import TrackingRulesEngine

    wiki_base = configure_wiki_base(wiki_base)
    engine = TrackingRulesEngine(wiki_base)
    companies = engine.get_all_tracking_companies()

    print(f"\n{'='*60}")
    print(f" 批量重新生成HTML报告")
    print(f"{'='*60}")
    print(f" 总计: {len(companies)} 家公司")
    print(f" 强制覆盖: {'是' if force else '否'}")
    print(f"{'='*60}\n")

    success_count = 0
    failed_count = 0

    for stock, name in companies:
        print(f"\n{'─'*50}")
        print(f" 处理: {stock} - {name}")
        print(f"{'─'*50}")

        result = generate_html(stock, name, wiki_base, force=force)
        if result:
            success_count += 1
        else:
            failed_count += 1

    print(f"\n{'='*60}")
    print(f" 批量生成完成")
    print(f"{'='*60}")
    print(f" 成功: {success_count}")
    print(f" 失败: {failed_count}")
    print(f"{'='*60}\n")


def main():
    parser = argparse.ArgumentParser(
        description="HTML报告独立生成器 - 基于已有跟踪数据重新生成合并HTML报告"
    )
    parser.add_argument("--stock", help="股票代码")
    parser.add_argument("--company", help="公司简称")
    parser.add_argument("--date", help="报告日期 (YYYY-MM-DD，默认今天)")
    parser.add_argument("--wiki-base", default=WIKI_BASE, help="wiki 根目录")
    parser.add_argument("--force", action="store_true", help="强制覆盖已有报告")
    parser.add_argument("--all", action="store_true", help="为所有公司重新生成")

    args = parser.parse_args()

    # 批量模式
    if args.all:
        regenerate_all(args.wiki_base, args.force)
        return

    # 单公司模式
    if not args.stock or not args.company:
        parser.print_help()
        print("\n❌ 必须指定 --stock 和 --company，或使用 --all")
        return

    generate_html(args.stock, args.company, args.wiki_base, args.date, args.force)


if __name__ == "__main__":
    main()
