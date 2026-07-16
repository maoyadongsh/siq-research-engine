#!/usr/bin/env python3
"""
SIQ_analysis CLI 入口
提供标准化的单公司深度分析工作流
"""

import argparse
import json
import sys
import os
from pathlib import Path
from datetime import datetime

# 添加脚本目录到路径
scripts_dir = Path(__file__).parent
sys.path.insert(0, str(scripts_dir))

from wiki_data_accessor import WikiDataAccessor


def cmd_list(args):
    """列出所有工作集公司"""
    accessor = WikiDataAccessor()
    companies = accessor.list_companies()
    print(f"\n{'='*80}")
    print(f"SIQ_analysis 工作集 - {len(companies)} 家公司")
    print(f"{'='*80}")
    print(f"{'股票代码':<10} {'公司简称':<12} {'申万一级':<10} {'申万二级':<12} {'v6.41':<6}")
    print(f"{'-'*80}")
    for c in companies:
        v641 = "✓" if c.has_v641_metrics else "✗"
        print(f"{c.stock_code:<10} {c.company_short_name:<12} {c.industry_sw1:<10} {c.industry_sw2:<12} {v641:<6}")
    print(f"{'='*80}\n")


def cmd_info(args):
    """显示公司详细信息"""
    accessor = WikiDataAccessor()
    company = accessor.get_company_by_id(args.company_id)
    if not company:
        company = accessor.get_company_by_stock_code(args.company_id)
    if not company:
        print(f"错误: 未找到公司 '{args.company_id}'")
        print("提示: 使用 'siq list' 查看所有可用公司")
        sys.exit(1)

    print(f"\n{'='*80}")
    print(f"公司详细信息: {company.company_short_name}")
    print(f"{'='*80}")
    print(f"  公司ID:        {company.company_id}")
    print(f"  股票代码:      {company.stock_code} ({company.exchange})")
    print(f"  公司全称:      {company.company_full_name}")
    print(f"  申万一级:      {company.industry_sw1} ({company.industry_sw1_code})")
    print(f"  申万二级:      {company.industry_sw2} ({company.industry_sw2_code})")
    print(f"  申万三级:      {company.industry_sw3} ({company.industry_sw3_code})")
    print(f"  状态:          {company.status}")
    print(f"  v6.41指标:     {'✓ 已就绪' if company.has_v641_metrics else '✗ 缺失'}")
    print(f"  主报告ID:      {company.primary_report_id}")
    print(f"  公司目录:      {company.company_path}")
    print(f"{'='*80}\n")


def cmd_check(args):
    """检查公司数据可用性"""
    accessor = WikiDataAccessor()
    company = accessor.get_company_by_id(args.company_id)
    if not company:
        company = accessor.get_company_by_stock_code(args.company_id)
    if not company:
        print(f"错误: 未找到公司 '{args.company_id}'")
        sys.exit(1)

    data = accessor.load_company_full(company.company_id)

    print(f"\n{'='*80}")
    print(f"数据可用性检查: {company.company_short_name} ({company.stock_code})")
    print(f"{'='*80}")

    # 按层级分组显示
    tiers = {
        "公司目录": ["company_info", "company_json"],
        "财务指标 (最高优先级)": ["three_statements", "key_metrics", "validation"],
        "证据链": ["evidence_index", "pdf_refs"],
        "语义层": ["semantic"],
        "报告原文": ["report_md", "report_json", "document_full"],
    }

    all_ready = True
    for tier_name, keys in tiers.items():
        print(f"\n  [{tier_name}]")
        for key in keys:
            available = data["data_availability"].get(key, False)
            status = "✓ 可用" if available else "✗ 缺失"
            print(f"    {status:<10} {key}")
            if not available:
                all_ready = False

    print(f"\n{'='*80}")
    if all_ready:
        print("  状态: ✓ 所有数据就绪，可以生成分析报告")
    else:
        print("  状态: ⚠ 部分数据缺失，分析报告可能不完整")
    print(f"{'='*80}\n")

    return all_ready


def cmd_analyze(args):
    """执行单公司深度分析"""
    accessor = WikiDataAccessor()
    company = accessor.get_company_by_id(args.company_id)
    if not company:
        company = accessor.get_company_by_stock_code(args.company_id)
    if not company:
        print(f"错误: 未找到公司 '{args.company_id}'")
        sys.exit(1)

    print(f"\n{'='*80}")
    print(f"SIQ_analysis 深度分析启动")
    print(f"{'='*80}")
    print(f"  目标公司: {company.company_short_name} ({company.stock_code})")
    print(f"  报告年份: {args.year}")
    print(f"  分析深度: {args.depth}")
    print(f"  输出格式: {args.format}")
    print(f"{'='*80}\n")

    # 阶段一：数据准备
    print("[阶段一] 数据准备...")
    data = accessor.load_company_full(company.company_id)

    if not data["metrics"]:
        print("  ✗ 财务指标缺失，无法继续分析")
        sys.exit(1)

    print(f"  ✓ 财务指标已加载")
    print(f"  ✓ 关键指标项数: {len(data['metrics'].key_metrics.get('data', []))}")
    print(f"  ✓ 证据链: {'已绑定' if data['evidence']['evidence_index'] else '未绑定'}")
    print(f"  ✓ 语义层: {'已生成' if data['semantic'] else '未生成'}")

    # 阶段二：确定性报告流水线
    print("\n[阶段二] 确定性报告流水线...")
    report_script = scripts_dir / "run_analysis_report.py"
    import subprocess
    cmd = [
        sys.executable,
        str(report_script),
        "--company",
        company.company_id,
        "--year",
        str(args.year),
    ]
    if getattr(args, "reuse_checkpoint", False):
        cmd.append("--reuse-checkpoint")
    if getattr(args, "allow_overwrite", False):
        cmd.append("--allow-overwrite")

    print(f"  执行: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    print(result.stdout)
    if result.returncode != 0:
        print(f"  ✗ 报告流水线失败: {result.stderr}")
        sys.exit(1)

    # 阶段三：输出
    print(f"\n[阶段三] 分析完成")
    analysis_dir = accessor.get_analysis_dir(company.company_id)
    print(f"  输出目录: {analysis_dir}")

    # 列出生成的文件
    if analysis_dir.exists():
        files = list(analysis_dir.glob(f"{company.stock_code}-*-{args.year}-analysis.*"))
        if files:
            print(f"  生成文件:")
            for f in files:
                size = f.stat().st_size
                print(f"    - {f.name} ({size:,} bytes)")
        else:
            print(f"  ⚠ 未找到生成的报告文件")

    print(f"\n{'='*80}\n")


def cmd_batch(args):
    """批量分析"""
    accessor = WikiDataAccessor()
    companies = accessor.list_companies()

    print(f"\n{'='*80}")
    print(f"批量分析模式")
    print(f"{'='*80}")
    print(f"  目标公司数: {len(companies)}")
    print(f"  报告年份: {args.year}")
    print(f"  分析深度: {args.depth}")
    print(f"{'='*80}\n")

    if not args.yes:
        print("⚠ 警告: 批量分析将消耗大量时间和资源")
        print("  使用 --yes 参数确认执行")
        print("\n  建议: 先对单家公司测试，确认无误后再批量执行")
        sys.exit(0)

    success_count = 0
    fail_count = 0

    for i, company in enumerate(companies, 1):
        print(f"\n[{i}/{len(companies)}] 分析 {company.company_short_name}...")
        try:
            # 复用单公司分析逻辑
            analyze_args = argparse.Namespace(
                company_id=company.company_id,
                year=args.year,
                depth=args.depth,
                format=args.format,
            )
            cmd_analyze(analyze_args)
            success_count += 1
        except Exception as e:
            print(f"  ✗ 失败: {e}")
            fail_count += 1

    print(f"\n{'='*80}")
    print(f"批量分析完成")
    print(f"  成功: {success_count}")
    print(f"  失败: {fail_count}")
    print(f"{'='*80}\n")


def main():
    parser = argparse.ArgumentParser(
        description="SIQ_analysis - 上市公司深度财务分析工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  siq list                           # 列出所有公司
  siq info 000333-美的集团           # 查看公司详情
  siq check 000333                   # 检查数据可用性
  siq analyze 000333-美的集团        # 生成深度分析报告
  siq batch --year 2025 --yes        # 批量分析所有公司
        """
    )

    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    # list
    subparsers.add_parser("list", help="列出所有工作集公司")

    # info
    info_parser = subparsers.add_parser("info", help="显示公司详细信息")
    info_parser.add_argument("company_id", help="公司ID或股票代码")

    # check
    check_parser = subparsers.add_parser("check", help="检查公司数据可用性")
    check_parser.add_argument("company_id", help="公司ID或股票代码")

    # analyze
    analyze_parser = subparsers.add_parser("analyze", help="执行单公司深度分析")
    analyze_parser.add_argument("company_id", help="公司ID或股票代码")
    analyze_parser.add_argument("--year", type=int, default=2025, help="报告年份 (默认: 2025)")
    analyze_parser.add_argument("--depth", choices=["standard", "deep"], default="deep",
                                help="分析深度 (默认: deep)")
    analyze_parser.add_argument("--format", choices=["markdown", "json", "both"], default="both",
                                help="输出格式 (默认: both)")
    analyze_parser.add_argument("--reuse-checkpoint", action="store_true",
                                help="复用已有 .work 检查点，适合从中断处续跑")
    analyze_parser.add_argument("--allow-overwrite", action="store_true",
                                help="允许覆盖已有最终报告；覆盖前会自动备份")

    # batch
    batch_parser = subparsers.add_parser("batch", help="批量分析所有公司")
    batch_parser.add_argument("--year", type=int, default=2025, help="报告年份 (默认: 2025)")
    batch_parser.add_argument("--depth", choices=["standard", "deep"], default="deep",
                              help="分析深度 (默认: deep)")
    batch_parser.add_argument("--format", choices=["markdown", "json", "both"], default="both",
                              help="输出格式 (默认: both)")
    batch_parser.add_argument("--yes", action="store_true", help="确认执行批量分析")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # 路由到对应命令
    commands = {
        "list": cmd_list,
        "info": cmd_info,
        "check": cmd_check,
        "analyze": cmd_analyze,
        "batch": cmd_batch,
    }

    if args.command in commands:
        commands[args.command](args)
    else:
        print(f"未知命令: {args.command}")
        sys.exit(1)


if __name__ == "__main__":
    main()
