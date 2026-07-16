#!/usr/bin/env python3
"""
finsight_tracking 主控脚本（规则引擎版）
一键运行五大模块，强制执行工作规则。

用法:
    python run_all.py --stock 000063 --company 中兴通讯
    python run_all.py --stock 000063 --company 中兴通讯 --skip-sentiment
    python run_all.py --validate-all          # 验证所有公司规则合规性

工作规则:
    1. 工作目录: companies/<stock>-<name>/tracking/
    2. 脚本位置: tracking/scripts/（固定）
    3. 报告命名: <stock>-<name>-跟踪报告-<date>.html
    4. 单报告原则: 只生成合并报告，禁止单独HTML
    5. 前置检查: 只跟踪 finsight_analysis 已完成分析的公司
    6. 目录结构: tracking/ 下必须包含 sentiment/metrics/alerts/updates/
"""

import argparse
import json
import os
import sys
from datetime import datetime

# 添加脚本目录到路径
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from finsight_tracking_rules import (
    TrackingRulesEngine,
    configure_wiki_base,
    preflight_check,
    ensure_tracking_structure,
    enforce_single_report_policy,
    get_report_path,
    resolve_report_path,
    WIKI_BASE,
    validate_html_content,
    delete_manual_html_files,
)

# 导入搜索工具
try:
    from search_tools import SearchTools
    SEARCH_TOOLS_AVAILABLE = True
except ImportError:
    SEARCH_TOOLS_AVAILABLE = False

# 导入五大模块
from module1_item_extractor import generate_tracking_items
from module2_sentiment_monitor import run_sentiment_monitor
from module3_metrics_tracker import run_metrics_tracker
from module4_alert_trigger import run_alert_trigger
from module5_report_updater import run_report_updater
from module6_html_reporter import run_html_reporter
from validate_citations import validate_citations


CRITICAL_MODULES = {"module1", "module3", "module4", "module5", "module6"}


def _module_failed(results: dict, module: str, error: Exception, strict: bool) -> None:
    print(f"❌ 失败: {error}")
    results["modules"][module] = {"status": "failed", "error": str(error)}
    if strict and module in CRITICAL_MODULES:
        raise


def _finalize_status(results: dict, html_ok: bool = True) -> dict:
    failed = [name for name, info in results["modules"].items() if info["status"] == "failed"]
    critical_failed = [name for name in failed if name in CRITICAL_MODULES]
    if critical_failed:
        results["status"] = "failed"
    elif failed or not html_ok:
        results["status"] = "partial_success"
    else:
        results["status"] = "success"
    results["failed_modules"] = failed
    return results


def run_all(
    stock_code: str,
    company_name: str,
    wiki_base: str = WIKI_BASE,
    skip_sentiment: bool = False,
    use_search: bool = True,
    allow_simulated_sentiment: bool = False,
    cleanup_html: bool = False,
    strict: bool = False,
    update_analysis: bool = False,
):
    """运行完整跟踪流程（带规则校验）

    Args:
        use_search: 是否使用网络搜索工具（Tavily/Exa）补充数据
    """
    wiki_base = configure_wiki_base(wiki_base)

    print(f"\n{'='*60}")
    print(f" finsight_tracking - 启动完整跟踪流程")
    print(f"{'='*60}")
    print(f" 股票代码: {stock_code}")
    print(f" 公司名称: {company_name}")
    print(f" 工作目录: {wiki_base}")
    print(f" 搜索工具: {'启用' if use_search else '禁用'}")
    print(f" 模拟舆情: {'允许' if allow_simulated_sentiment else '禁用'}")
    print(f" 运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}\n")

    # 检查搜索工具可用性
    if use_search and SEARCH_TOOLS_AVAILABLE:
        search = SearchTools()
        availability = search.check_availability()
        if availability.get("any"):
            backends = []
            if availability.get("tavily"):
                backends.append("Tavily")
            if availability.get("exa"):
                backends.append("Exa")
            print(f"🔍 搜索工具已就绪: {', '.join(backends)}")
        else:
            print("⚠️ 搜索工具不可用（API Key 未配置）")
            use_search = False
    elif use_search and not SEARCH_TOOLS_AVAILABLE:
        print("⚠️ search_tools 模块未找到")
        use_search = False

    # ═══════════════════════════════════════════════════════
    # 规则5: 前置检查
    # ═══════════════════════════════════════════════════════
    print("\n🔍 [规则检查] 前置条件验证")
    print("-" * 40)
    passed, errors = preflight_check(stock_code, company_name)
    if not passed:
        print("❌ 前置检查失败:")
        for e in errors:
            print(f"   - {e}")
        print("\n⛔ 中止执行")
        return {"status": "failed", "reason": "preflight_check", "errors": errors}
    print("✅ 前置检查通过")

    # ═══════════════════════════════════════════════════════
    # 规则1+6: 确保目录结构
    # ═══════════════════════════════════════════════════════
    print("\n📁 [规则检查] 目录结构初始化")
    print("-" * 40)
    try:
        created = ensure_tracking_structure(stock_code, company_name)
        print("✅ 目录结构已确保")
        for name, path in created.items():
            print(f"   {name}/")
    except Exception as e:
        print(f"❌ 目录创建失败: {e}")
        return {"status": "failed", "reason": "directory_creation", "error": str(e)}

    # ═══════════════════════════════════════════════════════
    # 规则4: 强制执行单报告原则
    # ═══════════════════════════════════════════════════════
    print("\n🧹 [规则检查] 单报告原则")
    print("-" * 40)
    if cleanup_html:
        archived = enforce_single_report_policy(stock_code, company_name)
        handled = delete_manual_html_files(stock_code, company_name)
        for f in archived + handled:
            print(f"  📦 已归档HTML: {f}")
        print("✅ 单报告原则已归档清理")
    else:
        print("✅ 保留历史HTML；本次运行将更新 manifest/latest")

    # ═══════════════════════════════════════════════════════
    # 运行五大模块
    # ═══════════════════════════════════════════════════════
    results = {"status": "success", "modules": {}}

    # 模块1: 跟踪事项提取器
    print("\n📋 [模块1] 跟踪事项提取器")
    print("-" * 40)
    try:
        path = generate_tracking_items(stock_code, company_name, wiki_base, use_search=use_search)
        results["modules"]["module1"] = {"status": "success", "path": path}
        print(f"✅ 已生成: {path}")
    except Exception as e:
        _module_failed(results, "module1", e, strict)

    # 模块2: 舆情监控器（支持搜索）
    print("\n📰 [模块2] 舆情监控器")
    print("-" * 40)
    try:
        if skip_sentiment:
            print("⏭️ 跳过（--skip-sentiment）")
            results["modules"]["module2"] = {"status": "skipped"}
        elif use_search:
            # 使用网络搜索获取真实舆情
            from module2_sentiment_monitor import run_sentiment_monitor_with_search
            path = run_sentiment_monitor_with_search(
                stock_code,
                company_name,
                wiki_base,
                date=None,
                allow_simulated=allow_simulated_sentiment,
            )
            results["modules"]["module2"] = {"status": "success", "path": path}
            print(f"✅ 已生成（含网络搜索）: {path}")
        else:
            if allow_simulated_sentiment:
                path = run_sentiment_monitor(stock_code, company_name, wiki_base, use_simulated=True)
                results["modules"]["module2"] = {"status": "success", "path": path}
                print(f"✅ 已生成: {path}")
            else:
                print("⏭️ 跳过：搜索禁用且未允许模拟舆情")
                results["modules"]["module2"] = {"status": "skipped", "reason": "no_real_source"}
    except Exception as e:
        _module_failed(results, "module2", e, strict)

    # 模块3: 指标追踪器（支持搜索）
    print("\n📊 [模块3] 指标追踪器")
    print("-" * 40)
    try:
        path = run_metrics_tracker(stock_code, company_name, wiki_base, use_search=use_search)
        results["modules"]["module3"] = {"status": "success", "path": path}
        print(f"✅ 已生成: {path}")
    except Exception as e:
        _module_failed(results, "module3", e, strict)

    # 模块4: 预警触发器（支持搜索验证）
    print("\n🚨 [模块4] 预警触发器")
    print("-" * 40)
    try:
        path = run_alert_trigger(stock_code, company_name, wiki_base, use_search=use_search)
        results["modules"]["module4"] = {"status": "success", "path": path}
        if path:
            print(f"✅ 已生成: {path}")
        else:
            print("✅ 未触发预警")
    except Exception as e:
        _module_failed(results, "module4", e, strict)

    # 模块5: 报告更新器（支持搜索）
    print("\n📝 [模块5] 报告更新器")
    print("-" * 40)
    try:
        path = run_report_updater(
            stock_code,
            company_name,
            wiki_base,
            use_search=use_search,
            update_analysis=update_analysis,
        )
        results["modules"]["module5"] = {"status": "success", "path": path}
        print(f"✅ 已生成: {path}")
    except Exception as e:
        _module_failed(results, "module5", e, strict)

    # 模块6: 合并HTML报告生成器
    print("\n🌐 [模块6] 合并HTML报告生成器")
    print("-" * 40)
    try:
        path = run_html_reporter(stock_code, company_name, wiki_base)
        results["modules"]["module6"] = {"status": "success", "path": path}
        print(f"✅ 已生成: {path}")
    except Exception as e:
        _module_failed(results, "module6", e, strict)

    # ═══════════════════════════════════════════════════════
    # 最终规则检查（含HTML规则7-9）
    # ═══════════════════════════════════════════════════════
    print("\n🔍 [最终检查] 规则合规性确认")
    print("-" * 40)
    html_ok = False
    report_path = resolve_report_path(stock_code, company_name)
    if report_path and os.path.exists(report_path):
        print(f"✅ 合并报告已生成: {os.path.basename(report_path)}")
        # 校验HTML内容
        html_ok, html_issues = validate_html_content(stock_code, company_name)
        if html_ok:
            print("✅ HTML内容合规（规则7-9）")
        else:
            print("⚠️ HTML内容违规:")
            for issue in html_issues:
                print(f"   - {issue}")
    else:
        print(f"⚠️ 合并报告未找到: {report_path}")

    print("\n🔗 [最终检查] 证据链覆盖")
    print("-" * 40)
    citation_result = validate_citations(stock_code, company_name, wiki_base)
    results["citation_check"] = citation_result
    if citation_result["passed"]:
        print("✅ 证据链校验通过")
    else:
        print("⚠️ 证据链校验发现问题:")
        for issue in citation_result["issues"][:10]:
            print(f"   - {issue}")

    # 汇总
    print(f"\n{'='*60}")
    print(" 执行结果汇总")
    print(f"{'='*60}")
    success_count = sum(1 for m in results["modules"].values() if m["status"] == "success")
    total_count = len(results["modules"])
    _finalize_status(results, html_ok=html_ok and citation_result["passed"])
    print(f" 总状态: {results['status']}")
    print(f" 模块成功: {success_count}/{total_count}")
    for module, info in results["modules"].items():
        status_emoji = {"success": "✅", "failed": "❌", "skipped": "⏭️"}.get(info["status"], "❓")
        print(f" {status_emoji} {module}: {info.get('path', info.get('error', info['status']))}")
    print(f"{'='*60}\n")

    # Refresh wiki/companies/<id>/_index.json so the frontend sees the freshest
    # tracking artifacts. Best-effort; failures here are not pipeline-fatal.
    try:
        from finsight_tracking_rules import COMPANIES_DIR  # noqa: WPS433  (local import to avoid circular at module load)
        import subprocess as _sp
        from pathlib import Path
        company_dir = os.path.join(COMPANIES_DIR, f"{stock_code}-{company_name}")
        project_root = Path(__file__).resolve().parents[4]
        index_script_candidates = [
            project_root / "agents" / "hermes" / "profiles" / "shared" / "scripts" / "update_company_index.py",
            project_root / "data" / "hermes" / "home" / "profiles" / "shared" / "scripts" / "update_company_index.py",
            Path("/home/maoyd/.hermes/profiles/shared/scripts/update_company_index.py"),
        ]
        index_script = next((path for path in index_script_candidates if path.exists()), None)
        if os.path.exists(company_dir) and index_script:
            _sp.run(
                [sys.executable, str(index_script), "--company-dir", company_dir],
                capture_output=True,
                text=True,
                timeout=15,
            )
    except Exception as exc:  # pragma: no cover - non-critical
        print(f"⚠ 公司索引更新失败（不影响跟踪结果）: {exc}")

    return results


def main():
    parser = argparse.ArgumentParser(description="finsight_tracking 主控脚本（规则引擎版）")
    parser.add_argument("--stock", help="股票代码")
    parser.add_argument("--company", help="公司简称")
    parser.add_argument("--wiki-base", default=WIKI_BASE, help="wiki 根目录")
    parser.add_argument("--skip-sentiment", action="store_true", help="跳过舆情监控")
    parser.add_argument("--no-search", action="store_true", help="禁用网络搜索工具（Tavily/Exa）")
    parser.add_argument("--allow-simulated-sentiment", action="store_true", help="允许真实舆情不可用时生成模拟舆情")
    parser.add_argument("--cleanup-html", action="store_true", help="归档历史/手工HTML，默认只更新最新报告指针")
    parser.add_argument("--strict", action="store_true", help="关键模块失败时立即中止")
    parser.add_argument("--update-analysis", action="store_true", help="将跟踪更新索引写回 analysis 报告，默认只写 tracking/updates")
    parser.add_argument("--json-summary", action="store_true", help="输出 JSON 结果摘要")
    parser.add_argument("--validate-all", action="store_true", help="验证所有公司规则合规性")
    parser.add_argument("--setup", action="store_true", help="初始化公司跟踪环境")

    args = parser.parse_args()

    # 全量验证模式
    if args.validate_all:
        engine = TrackingRulesEngine(args.wiki_base)
        results = engine.validate_all()
        print(f"\n{'='*60}")
        print(" 全量规则验证")
        print(f"{'='*60}")
        print(f" 总计: {results['total']} 家公司")
        print(f" 通过: {results['passed']}")
        print(f" 失败: {results['failed']}")
        if results["failed"] > 0:
            print("\n 违规详情:")
            for d in results["details"]:
                if d["issues"]:
                    print(f"\n  ❌ {d['stock']}-{d['name']}:")
                    for issue in d["issues"]:
                        print(f"     - {issue}")
        print(f"{'='*60}\n")
        return

    # 初始化模式
    if args.setup:
        if not args.stock or not args.company:
            print("❌ --setup 需要 --stock 和 --company")
            return
        engine = TrackingRulesEngine(args.wiki_base)
        result = engine.setup_company(args.stock, args.company)
        print(f"\n{'='*60}")
        print(f" 初始化结果: {args.stock}-{args.company}")
        print(f"{'='*60}")
        print(f" 状态: {'✅ 通过' if result['passed'] else '❌ 失败'}")
        if result["errors"]:
            print(f" 错误: {', '.join(result['errors'])}")
        if result["warnings"]:
            print(f" 警告: {', '.join(result['warnings'])}")
        print(f"{'='*60}\n")
        return

    # 正常运行模式
    if not args.stock or not args.company:
        parser.print_help()
        print("\n❌ 必须指定 --stock 和 --company，或使用 --validate-all/--setup")
        return

    try:
        result = run_all(
            args.stock,
            args.company,
            args.wiki_base,
            args.skip_sentiment,
            use_search=not args.no_search,
            allow_simulated_sentiment=args.allow_simulated_sentiment,
            cleanup_html=args.cleanup_html,
            strict=args.strict,
            update_analysis=args.update_analysis,
        )
    except Exception as e:
        result = {"status": "failed", "error": str(e)}
        if args.strict:
            print(f"⛔ 严格模式中止: {e}")

    if args.json_summary:
        print(json.dumps(result, ensure_ascii=False, indent=2))

    if result.get("status") == "failed":
        sys.exit(1)
    if result.get("status") == "partial_success":
        sys.exit(2)


if __name__ == "__main__":
    main()
