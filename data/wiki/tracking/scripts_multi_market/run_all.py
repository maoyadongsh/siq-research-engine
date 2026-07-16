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
import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

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
RESEARCH_IDENTITY_FIELDS = ("market", "company_id", "filing_id", "parse_run_id")
SUPPORTED_MULTI_MARKETS = frozenset({"HK", "US", "EU", "KR", "JP"})


def _read_target_bundle(path: str, wiki_base: str) -> dict[str, Any]:
    """Read and validate a server-created tracking target bundle."""

    payload = json.loads(Path(path).expanduser().resolve().read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("tracking target bundle must be a JSON object")
    target = payload.get("research_target") if isinstance(payload.get("research_target"), dict) else payload
    identity = target.get("research_identity") if isinstance(target.get("research_identity"), dict) else {}
    missing = [field for field in RESEARCH_IDENTITY_FIELDS if not str(identity.get(field) or "").strip()]
    if missing:
        raise ValueError(f"tracking target ResearchIdentity is incomplete: {', '.join(missing)}")
    market = str(identity.get("market") or "").strip().upper()
    if market == "CN":
        raise ValueError("cn_legacy_pipeline_required")
    if market not in SUPPORTED_MULTI_MARKETS:
        raise ValueError(f"unsupported_multi_market_tracking_target: {market or 'missing'}")

    resolved_paths = payload.get("resolved_paths") if isinstance(payload.get("resolved_paths"), dict) else {}
    root = Path(wiki_base).expanduser().resolve()

    def scoped_path(key: str, *, parent: Path | None = None, required: bool = True) -> Path | None:
        raw = str(resolved_paths.get(key) or "").strip()
        if not raw:
            if required:
                raise ValueError(f"tracking target is missing resolved path: {key}")
            return None
        candidate = Path(raw).expanduser().resolve()
        scope = (parent or root).resolve()
        try:
            candidate.relative_to(scope)
        except ValueError as exc:
            raise ValueError(f"tracking target path escapes approved scope: {key}") from exc
        return candidate

    company_dir = scoped_path("company_dir")
    if company_dir is None or "companies" not in company_dir.relative_to(root).parts:
        raise ValueError("tracking target company_dir is not a company workspace")
    report_dir = scoped_path("report_dir", parent=company_dir)
    analysis_artifact = scoped_path("analysis_artifact", parent=company_dir / "analysis")
    analysis_sidecar = scoped_path("analysis_sidecar", parent=company_dir / "analysis")
    metrics_path = scoped_path("metrics_path", parent=company_dir, required=False)
    if not company_dir.is_dir() or report_dir is None or not report_dir.is_dir():
        raise ValueError("tracking target company/report directory is unavailable")
    if analysis_artifact is None or not analysis_artifact.is_file():
        raise ValueError("tracking target analysis baseline is unavailable")
    if analysis_sidecar is None or not analysis_sidecar.is_file():
        raise ValueError("tracking target analysis sidecar is unavailable")

    sidecar = json.loads(analysis_sidecar.read_text(encoding="utf-8"))
    if not isinstance(sidecar, dict) or sidecar.get("schema_version") != "siq_agent_artifact_v2":
        raise ValueError("tracking target analysis sidecar is not AgentArtifactV2")
    baseline_id = str(payload.get("baseline_analysis_artifact_id") or "").strip()
    if not baseline_id or str(sidecar.get("artifact_id") or "") != baseline_id:
        raise ValueError("tracking target analysis artifact id does not match its sidecar")
    sidecar_target = sidecar.get("research_target") if isinstance(sidecar.get("research_target"), dict) else {}
    sidecar_identity = (
        sidecar_target.get("research_identity")
        if isinstance(sidecar_target.get("research_identity"), dict)
        else {}
    )
    if any(str(sidecar_identity.get(field) or "") != str(identity.get(field) or "") for field in RESEARCH_IDENTITY_FIELDS):
        raise ValueError("tracking target analysis ResearchIdentity does not match")
    source_report = target.get("source_report") if isinstance(target.get("source_report"), dict) else {}
    if str(sidecar.get("source_report_id") or "") != str(source_report.get("report_id") or ""):
        raise ValueError("tracking target analysis source report does not match")
    if str(sidecar.get("html_file") or "") != analysis_artifact.name:
        raise ValueError("tracking target analysis HTML does not match its sidecar")
    expected_hash = str(sidecar.get("content_hash") or "").removeprefix("sha256:").lower()
    bundle_hash = str(payload.get("baseline_analysis_content_hash") or "").removeprefix("sha256:").lower()
    actual_hash = hashlib.sha256(analysis_artifact.read_bytes()).hexdigest()
    if not expected_hash or bundle_hash != expected_hash or actual_hash != expected_hash:
        raise ValueError("tracking target analysis content hash does not match")

    return {
        **payload,
        "research_target": target,
        "resolved_paths": {
            **resolved_paths,
            "company_dir": str(company_dir),
            "report_dir": str(report_dir),
            "analysis_artifact": str(analysis_artifact),
            "analysis_sidecar": str(analysis_sidecar),
            "metrics_path": str(metrics_path) if metrics_path else "",
        },
    }


def _configure_target_environment(target_bundle: dict[str, Any]) -> None:
    target = target_bundle["research_target"]
    identity = target["research_identity"]
    source_report = target.get("source_report") if isinstance(target.get("source_report"), dict) else {}
    paths = target_bundle["resolved_paths"]
    os.environ["SIQ_RESOLVED_COMPANY_DIR"] = paths["company_dir"]
    os.environ["SIQ_TRACKING_ANALYSIS_ARTIFACT"] = paths["analysis_artifact"]
    os.environ["SIQ_TRACKING_ANALYSIS_SIDECAR"] = paths["analysis_sidecar"]
    os.environ["SIQ_TRACKING_ANALYSIS_ARTIFACT_ID"] = str(
        target_bundle.get("baseline_analysis_artifact_id") or ""
    )
    os.environ["SIQ_TRACKING_PREVIOUS_CHECKPOINT"] = json.dumps(
        target_bundle.get("previous_tracking_checkpoint"),
        ensure_ascii=False,
        sort_keys=True,
    )
    os.environ["SIQ_TRACKING_RESEARCH_IDENTITY"] = json.dumps(identity, ensure_ascii=False, sort_keys=True)
    os.environ["SIQ_TRACKING_REPORT_DIR"] = paths["report_dir"]
    if paths.get("metrics_path"):
        os.environ["SIQ_TRACKING_METRICS_PATH"] = paths["metrics_path"]
    os.environ["SIQ_TRACKING_MARKET"] = str(identity.get("market") or "")
    os.environ["SIQ_TRACKING_REPORT_ID"] = str(source_report.get("report_id") or "")
    os.environ["SIQ_TRACKING_SOURCE_FAMILY"] = str(source_report.get("source_family") or "")
    os.environ["SIQ_TRACKING_ACCOUNTING_STANDARD"] = str(
        source_report.get("accounting_standard") or ""
    )


def _record_module_output(
    results: dict[str, Any],
    module: str,
    path: str | os.PathLike[str] | None,
    *,
    allow_empty: bool = False,
) -> bool:
    if path and Path(path).is_file():
        results["modules"][module] = {"status": "success", "path": str(path)}
        return True
    if allow_empty and not path:
        results["modules"][module] = {"status": "success", "reason": "no_events"}
        return True
    results["modules"][module] = {"status": "failed", "error": "module_output_missing"}
    return False


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
    elif failed or not html_ok or results.get("degraded_reasons"):
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
    target_bundle: dict[str, Any] | None = None,
):
    """运行完整跟踪流程（带规则校验）

    Args:
        use_search: 是否使用网络搜索工具（Tavily/Exa）补充数据
    """
    wiki_base = configure_wiki_base(wiki_base)
    if target_bundle:
        _configure_target_environment(target_bundle)

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
    results = {"status": "success", "modules": {}, "degraded_reasons": []}
    if target_bundle:
        results["research_target"] = target_bundle["research_target"]
        results["source_report_path"] = target_bundle["resolved_paths"]["analysis_artifact"]
        results["previous_tracking_checkpoint"] = target_bundle.get("previous_tracking_checkpoint")

    # 模块1: 跟踪事项提取器
    print("\n📋 [模块1] 跟踪事项提取器")
    print("-" * 40)
    try:
        path = generate_tracking_items(stock_code, company_name, wiki_base, use_search=use_search)
        if _record_module_output(results, "module1", path):
            print(f"✅ 已生成: {path}")
        else:
            print("❌ 模块未生成跟踪事项产物")
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
            if _record_module_output(results, "module2", path):
                print(f"✅ 已生成（含网络搜索）: {path}")
            else:
                results["degraded_reasons"].append("sentiment_output_missing")
        else:
            if allow_simulated_sentiment:
                path = run_sentiment_monitor(stock_code, company_name, wiki_base, use_simulated=True)
                if _record_module_output(results, "module2", path):
                    print(f"✅ 已生成: {path}")
                else:
                    results["degraded_reasons"].append("sentiment_output_missing")
            else:
                print("⏭️ 跳过：搜索禁用且未允许模拟舆情")
                results["modules"]["module2"] = {"status": "unavailable", "reason": "no_real_source"}
                results["degraded_reasons"].append("sentiment_source_unavailable")
    except Exception as e:
        _module_failed(results, "module2", e, strict)

    sentiment_result = results["modules"].get("module2", {})
    os.environ["SIQ_TRACKING_SENTIMENT_STATUS"] = str(
        sentiment_result.get("status") or "unknown"
    )
    os.environ["SIQ_TRACKING_SENTIMENT_REASON"] = str(
        sentiment_result.get("reason") or sentiment_result.get("error") or ""
    )

    # 模块3: 指标追踪器（支持搜索）
    print("\n📊 [模块3] 指标追踪器")
    print("-" * 40)
    try:
        path = run_metrics_tracker(stock_code, company_name, wiki_base, use_search=use_search)
        if _record_module_output(results, "module3", path):
            print(f"✅ 已生成: {path}")
        else:
            print("❌ 模块未生成指标产物")
    except Exception as e:
        _module_failed(results, "module3", e, strict)

    # 模块4: 预警触发器（支持搜索验证）
    print("\n🚨 [模块4] 预警触发器")
    print("-" * 40)
    try:
        path = run_alert_trigger(stock_code, company_name, wiki_base, use_search=use_search)
        _record_module_output(results, "module4", path, allow_empty=True)
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
        if _record_module_output(results, "module5", path):
            print(f"✅ 已生成: {path}")
        else:
            print("❌ 模块未生成更新记录")
    except Exception as e:
        _module_failed(results, "module5", e, strict)

    # 模块6: 合并HTML报告生成器
    print("\n🌐 [模块6] 合并HTML报告生成器")
    print("-" * 40)
    try:
        path = run_html_reporter(stock_code, company_name, wiki_base)
        if _record_module_output(results, "module6", path):
            print(f"✅ 已生成: {path}")
        else:
            print("❌ 模块未生成 HTML 产物")
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

    # Finalized company metadata belongs to ingestion/host publication.  New
    # tracking artifacts are discovered from their versioned sidecars.
    results["company_index_update"] = "deferred_to_host_publisher"

    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="HK/US/EU/KR/JP 权威研究目标持续跟踪")
    parser.add_argument("--wiki-base", default=WIKI_BASE, help="wiki 根目录")
    parser.add_argument("--skip-sentiment", action="store_true", help="跳过舆情监控")
    parser.add_argument("--no-search", action="store_true", help="禁用网络搜索工具（Tavily/Exa）")
    parser.add_argument("--allow-simulated-sentiment", action="store_true", help="允许真实舆情不可用时生成模拟舆情")
    parser.add_argument("--cleanup-html", action="store_true", help="归档历史/手工HTML，默认只更新最新报告指针")
    parser.add_argument("--strict", action="store_true", help="关键模块失败时立即中止")
    parser.add_argument("--update-analysis", action="store_true", help="将跟踪更新索引写回 analysis 报告，默认只写 tracking/updates")
    parser.add_argument("--json-summary", action="store_true", help="输出 JSON 结果摘要")
    parser.add_argument(
        "--target-json",
        required=True,
        help="服务端生成的 ResearchTarget/ResolvedReportPackage bundle",
    )
    args = parser.parse_args()

    try:
        target_bundle = _read_target_bundle(args.target_json, args.wiki_base)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(f"无效的 --target-json: {exc}")
    target = target_bundle["research_target"]
    identity = target["research_identity"]
    stock = str(target.get("display_code") or identity.get("company_id") or "company")
    company = str(target.get("display_name") or target.get("company_wiki_id") or stock)

    try:
        result = run_all(
            stock,
            company,
            args.wiki_base,
            args.skip_sentiment,
            use_search=not args.no_search,
            allow_simulated_sentiment=args.allow_simulated_sentiment,
            cleanup_html=args.cleanup_html,
            strict=args.strict,
            update_analysis=args.update_analysis,
            target_bundle=target_bundle,
        )
    except Exception as e:
        result = {"status": "failed", "error": str(e)}
        if args.strict:
            print(f"⛔ 严格模式中止: {e}")

    if args.json_summary:
        print(json.dumps(result, ensure_ascii=False, indent=2))

    if result.get("status") == "failed":
        return 1
    if result.get("status") == "partial_success":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
