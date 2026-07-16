"""Deterministic annual analysis report workflow for the analysis agent.

This module keeps formal report generation out of free-form LLM chat. When the
analysis assistant receives an explicit request to generate a complete report,
the API runs the latest research-pack pipeline directly and returns the
artifact links as the assistant reply.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import quote

from services.command_runner import run_command
from services.observability import (
    emit_research_event,
    record_research_validation_failure,
    record_research_workflow_terminal,
)
from services.path_config import PROJECT_ROOT

DEFAULT_YEAR = 2025
DEFAULT_TIMEOUT_SECONDS = int(os.getenv("SIQ_ANALYSIS_REPORT_WORKFLOW_TIMEOUT_SECONDS", "3600"))
logger = logging.getLogger(__name__)


REPORT_GENERATION_ACTION_RE = re.compile(
    r"(生成|产出|输出|创建|制作|构建|重跑|重新生成|刷新|更新|补全|跑一份|出一份)"
)
REPORT_OBJECT_RE = re.compile(
    r"(完整报告|正式报告|深度报告|分析报告|研究报告|财务分析报告|财务诊断报告|财务核查报告|年度报告|最新报告|14章报告|HTML报告|html报告)",
    re.IGNORECASE,
)
REPORT_META_QUESTION_RE = re.compile(r"(为什么|为何|原因|怎么没有|为何没有|没有调用|没调用|没有固化|没固化)")
OVERWRITE_RE = re.compile(r"(覆盖|替换现有|覆盖现有|写回默认|更新现有|改写现有)")
YEAR_RE = re.compile(r"(20\d{2})\s*年?")
STOCK_CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")
NON_CN_MARKET_TERMS = {
    "HK": ("香港市场", "港股", "港交所", "HKEX"),
    "US": ("美国市场", "美股", "SEC", "NYSE", "NASDAQ"),
    "EU": ("欧洲市场", "欧股", "Euronext"),
    "KR": ("韩国市场", "韩股", "KOSPI", "KOSDAQ"),
    "JP": ("日本市场", "日股", "东证", "东京证券"),
}
MULTI_MARKET_ANALYSIS_MARKETS = frozenset(NON_CN_MARKET_TERMS)
LEGACY_ANALYSIS_PROFILE = "siq_analysis"
MULTI_MARKET_ANALYSIS_PROFILE = "siq_analysis_multi_market"


@dataclass(frozen=True)
class AnalysisReportWorkflowRequest:
    company_query: str
    year: int = DEFAULT_YEAR
    allow_overwrite: bool = False
    force: bool = True
    research_subagent_mode: str = "deterministic"
    prompt: str = ""
    formal_target: bool = False
    context_payload: Mapping[str, Any] | None = None
    company_key: str = ""
    report_id: str = ""
    research_identity: Mapping[str, Any] | None = None
    validation_error: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class AnalysisReportWorkflowResponse:
    handled: bool
    reply: str
    result: dict[str, Any]


def _context_dict(context: Any | None) -> dict[str, Any]:
    if context is None:
        return {}
    if isinstance(context, Mapping):
        return dict(context)
    if hasattr(context, "model_dump"):
        dumped = context.model_dump(exclude_none=True)
        return dumped if isinstance(dumped, dict) else {}
    return {}


def _context_company(context: Any | None) -> dict[str, Any]:
    raw = _context_dict(context).get("company")
    return raw if isinstance(raw, dict) else {}


def _context_source_report(context: Any | None) -> dict[str, Any]:
    payload = _context_dict(context)
    raw = payload.get("source_report")
    if not isinstance(raw, dict):
        raw = payload.get("report")
    return raw if isinstance(raw, dict) else {}


def _context_research_target(context: Any | None) -> dict[str, Any]:
    raw = _context_dict(context).get("research_target")
    return raw if isinstance(raw, dict) else {}


def _context_research_identity(context: Any | None) -> dict[str, str]:
    payload = _context_dict(context)
    company = _context_company(context)
    source_report = _context_source_report(context)
    target = _context_research_target(context)
    target_identity = target.get("research_identity") if isinstance(target.get("research_identity"), dict) else {}
    raw = payload.get("research_identity") if isinstance(payload.get("research_identity"), dict) else {}
    identity: dict[str, str] = {}
    for field in ("market", "company_id", "filing_id", "parse_run_id"):
        value = (
            target_identity.get(field)
            or raw.get(field)
            or payload.get(field)
            or source_report.get(field)
            or company.get(field)
        )
        identity[field] = str(value or "").strip()
    identity["market"] = identity["market"].upper()
    return identity


def _formal_context_fields(context: Any | None) -> tuple[bool, str, str, dict[str, str]]:
    payload = _context_dict(context)
    company = _context_company(context)
    source_report = _context_source_report(context)
    target = _context_research_target(context)
    target_report = target.get("source_report") if isinstance(target.get("source_report"), dict) else {}
    company_key = str(target.get("company_key") or company.get("company_key") or payload.get("company_key") or "").strip()
    report_id = str(
        target_report.get("report_id")
        or source_report.get("report_id")
        or payload.get("report_id")
        or ""
    ).strip()
    identity = _context_research_identity(context)
    formal = bool(company_key or report_id or (identity["market"] and identity["market"] != "CN"))
    return formal, company_key, report_id, identity


def _explicit_non_cn_market(message: str) -> str:
    lower = str(message or "").lower()
    for market, terms in NON_CN_MARKET_TERMS.items():
        for term in terms:
            token = term.lower()
            if token.isascii():
                if re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", lower):
                    return market
            elif token in lower:
                return market
    return ""


def _clean_company_query(value: str | None) -> str:
    text = str(value or "").strip()
    text = re.sub(r"^[\s:：,，。；;]+|[\s:：,，。；;]+$", "", text)
    return text


def _extract_year(message: str, context: Any | None) -> int:
    for source in (message, str((_context_dict(context).get("report") or {}).get("filename") or "")):
        match = YEAR_RE.search(source or "")
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                continue
    return DEFAULT_YEAR


def _extract_company_query(message: str, context: Any | None) -> str:
    company = _context_company(context)
    for key in ("dir", "code", "name"):
        value = _clean_company_query(company.get(key))
        if value:
            return value

    code = STOCK_CODE_RE.search(message or "")
    if code:
        return code.group(1)

    # Let resolve_company.py do deterministic fuzzy matching when the message
    # includes a company name but no structured page context.
    return _clean_company_query(message)


def is_analysis_report_generation_request(message: str, context: Any | None = None) -> bool:
    text = (message or "").strip()
    if not text:
        return False
    if REPORT_META_QUESTION_RE.search(text):
        return False
    if not REPORT_GENERATION_ACTION_RE.search(text):
        return False
    if REPORT_OBJECT_RE.search(text):
        return True
    return "报告" in text and "分析" in text


def build_analysis_report_workflow_request(
    message: str,
    context: Any | None = None,
) -> AnalysisReportWorkflowRequest | None:
    if not is_analysis_report_generation_request(message, context):
        return None
    context_payload = _context_dict(context)
    company_query = _extract_company_query(message, context)
    formal_target, company_key, report_id, identity = _formal_context_fields(context)
    explicit_market = _explicit_non_cn_market(message)
    if explicit_market and not formal_target:
        formal_target = True
        identity["market"] = explicit_market
    if not company_query and not formal_target:
        return None
    validation_error: dict[str, Any] | None = None
    # CN keeps the original company/year workflow even when the page supplies a
    # structured selector. The strict ResearchIdentity gate belongs exclusively
    # to the non-CN AnalysisInputBundle pipeline.
    if formal_target and identity["market"] in MULTI_MARKET_ANALYSIS_MARKETS:
        missing_identity = [field for field, value in identity.items() if not value]
        missing_selector = [
            field
            for field, value in (("company_key", company_key), ("report_id", report_id))
            if not value
        ]
        if missing_identity:
            validation_error = {
                "code": "research_identity_incomplete",
                "missing_fields": missing_identity,
            }
        elif missing_selector:
            validation_error = {
                "code": "company_not_found" if "company_key" in missing_selector else "source_report_not_found",
                "missing_fields": missing_selector,
            }
    return AnalysisReportWorkflowRequest(
        company_query=company_query or str(company_key),
        year=_extract_year(message, context),
        allow_overwrite=bool(OVERWRITE_RE.search(message or "")),
        force=True,
        research_subagent_mode="deterministic",
        prompt=(message or "").strip(),
        formal_target=formal_target,
        context_payload=context_payload,
        company_key=company_key,
        report_id=report_id,
        research_identity=identity if formal_target else None,
        validation_error=validation_error,
    )


def _candidate_script_paths(profile: str = LEGACY_ANALYSIS_PROFILE) -> tuple[Path, ...]:
    return (
        PROJECT_ROOT / "agents" / "hermes" / "profiles" / profile / "scripts" / "run_analysis_report.py",
        PROJECT_ROOT / "data" / "hermes" / "home" / "profiles" / profile / "scripts" / "run_analysis_report.py",
    )


def _script_supports_research_packs(script: Path) -> bool:
    if not script.is_file():
        return False
    try:
        source = script.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    script_dir = script.parent
    required_quality_files = (
        "html_renderer_v2.py",
        "financial_chart_design.py",
        "renderer_svg_charts.py",
        "renderer_assets.py",
        "run_research_subagents.py",
        "validate_research_packs.py",
        "merge_research_packs.py",
    )
    return (
        "--use-research-packs" in source
        and all((script_dir / filename).is_file() for filename in required_quality_files)
    )


def latest_research_pack_report_script() -> Path | None:
    for script in _candidate_script_paths():
        if _script_supports_research_packs(script):
            return script
    return None


def _script_supports_analysis_input_bundle(script: Path) -> bool:
    if not script.is_file():
        return False
    try:
        source = script.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    script_dir = script.parent
    return (
        "--input-bundle" in source
        and (script_dir / "analysis_input_bundle.py").is_file()
        and (script_dir / "analysis_bundle_renderer.py").is_file()
        and (script_dir / "formal_research_packs.py").is_file()
        and (script_dir / "input_adapters" / "__init__.py").is_file()
    )


def latest_multi_market_report_script() -> Path | None:
    for script in _candidate_script_paths(MULTI_MARKET_ANALYSIS_PROFILE):
        if _script_supports_analysis_input_bundle(script):
            return script
    return None


def _load_stdout_json(completed: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    try:
        payload = json.loads((completed.stdout or "").strip() or "{}")
    except json.JSONDecodeError:
        payload = {}
    return payload if isinstance(payload, dict) else {}


def _run_json_command(args: list[str], *, timeout: int | float = DEFAULT_TIMEOUT_SECONDS) -> tuple[dict[str, Any], subprocess.CompletedProcess[str]]:
    completed = run_command(args, cwd=PROJECT_ROOT, timeout=timeout)
    return _load_stdout_json(completed), completed


def _safe_filename_part(value: str) -> str:
    text = re.sub(r"[^\w\u4e00-\u9fff.-]+", "", value or "").strip("._-")
    return text or "company"


def _unique_output_prefix(resolved: dict[str, Any], year: int) -> Path:
    company = resolved.get("company") if isinstance(resolved.get("company"), dict) else {}
    paths = resolved.get("paths") if isinstance(resolved.get("paths"), dict) else {}
    company_dir_payload = paths.get("company_dir") if isinstance(paths.get("company_dir"), dict) else {}
    company_dir = Path(str(company_dir_payload.get("path") or ""))
    if not str(company_dir) or not company_dir.exists():
        company_path = str(company.get("company_path") or "").strip()
        company_dir = PROJECT_ROOT / "data" / "wiki" / company_path if company_path else PROJECT_ROOT / "data" / "wiki" / "companies"
    analysis_dir = company_dir / "analysis"
    stock_code = _safe_filename_part(str(company.get("stock_code") or "company"))
    short_name = _safe_filename_part(str(company.get("company_short_name") or company_dir.name.split("-", 1)[-1]))
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    return analysis_dir / f"{stock_code}-{short_name}-{year}-analysis-research-pack-{timestamp}"


def _package_value(package: Any, name: str, default: Any = None) -> Any:
    if isinstance(package, Mapping):
        return package.get(name, default)
    return getattr(package, name, default)


def _package_research_target(package: Any) -> dict[str, Any]:
    to_dict = getattr(package, "to_research_target_dict", None)
    if callable(to_dict):
        payload = to_dict()
    else:
        payload = _package_value(package, "research_target", {})
        if hasattr(payload, "model_dump"):
            payload = payload.model_dump(mode="json")
    if not isinstance(payload, Mapping):
        raise ValueError("resolved package did not provide ResearchTargetV1")
    return dict(payload)


def _formal_output_prefix(package: Any, target: Mapping[str, Any]) -> Path:
    company_dir = Path(str(_package_value(package, "company_dir", "")))
    if not company_dir.is_dir():
        raise ValueError("resolved package company directory is unavailable")
    report = target.get("source_report") if isinstance(target.get("source_report"), Mapping) else {}
    code = _safe_filename_part(str(target.get("display_code") or "company"))
    name = _safe_filename_part(str(target.get("display_name") or target.get("company_wiki_id") or "company"))
    period = _safe_filename_part(str(report.get("period_end") or report.get("fiscal_year") or "period"))
    report_id = _safe_filename_part(str(report.get("report_id") or "report"))
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    return company_dir / "analysis" / f"{code}-{name}-{period}-{report_id}-analysis-{timestamp}"


def _multi_market_research_enabled() -> bool:
    return os.getenv("SIQ_MULTI_MARKET_RESEARCH_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}


def _default_package_resolver(context: Mapping[str, Any]) -> Any:
    from services.research_report_package import resolve_report_package_from_context

    return resolve_report_package_from_context(context, agent_type="analysis")


def _load_bundle_functions(script_dir: Path) -> tuple[Callable[..., dict[str, Any]], Callable[[Path, Mapping[str, Any]], None]]:
    inserted = False
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
        inserted = True
    try:
        from analysis_input_bundle import build_analysis_input_bundle, write_analysis_input_bundle

        return build_analysis_input_bundle, write_analysis_input_bundle
    finally:
        if inserted:
            sys.path.remove(str(script_dir))


def _relative(path: str | Path | None) -> str:
    if not path:
        return ""
    raw = Path(str(path))
    try:
        return raw.resolve().relative_to(PROJECT_ROOT).as_posix()
    except Exception:
        return str(path)


def _wiki_report_url(html_path: str | Path | None) -> str:
    if not html_path:
        return ""
    path = Path(str(html_path))
    parts = path.parts
    try:
        companies_index = parts.index("companies")
        company_dir = parts[companies_index + 1]
    except (ValueError, IndexError):
        return ""
    return (
        f"/api/wiki/companies/{quote(company_dir, safe='')}/analysis/"
        f"{quote(path.name, safe='')}"
    )


def _validation_status(result: dict[str, Any]) -> str:
    validation = result.get("validation") if isinstance(result.get("validation"), dict) else {}
    if not validation:
        return "未返回"
    ok = validation.get("ok")
    status = validation.get("status") or validation.get("stage")
    failures = validation.get("failures")
    if ok is True:
        return str(status or "通过")
    if failures:
        return f"未通过，failures={len(failures)}"
    return str(status or ok or "未通过")


def _workflow_error(
    request: AnalysisReportWorkflowRequest,
    *,
    stage: str,
    next_action: str,
    details: Mapping[str, Any] | None = None,
) -> AnalysisReportWorkflowResponse:
    result = {
        "ok": False,
        "stage": stage,
        "company_query": request.company_query,
        "year": request.year,
        "next_action": next_action,
    }
    if details:
        result["details"] = dict(details)
    return _workflow_response(request, result, event="analysis_workflow_failed")


def _identity_matches(expected: Mapping[str, Any], actual: Mapping[str, Any]) -> bool:
    return all(
        str(expected.get(field) or "").strip().upper() == str(actual.get(field) or "").strip().upper()
        for field in ("market", "company_id", "filing_id", "parse_run_id")
    )


def _company_key_summary(company_key: str) -> str:
    import hashlib

    return hashlib.sha256(company_key.encode("utf-8")).hexdigest()[:12] if company_key else ""


def _workflow_response(
    request: AnalysisReportWorkflowRequest,
    result: dict[str, Any],
    *,
    event: str = "analysis_workflow_finished",
) -> AnalysisReportWorkflowResponse:
    identity = dict(request.research_identity or {})
    context = dict(request.context_payload or {})
    target = context.get("research_target") if isinstance(context.get("research_target"), Mapping) else {}
    target_identity = target.get("research_identity") if isinstance(target.get("research_identity"), Mapping) else {}
    if not identity and target_identity:
        identity = dict(target_identity)
    source_report = target.get("source_report") if isinstance(target.get("source_report"), Mapping) else {}
    adapter = result.get("adapter") if isinstance(result.get("adapter"), Mapping) else {}
    artifact = result.get("artifact") if isinstance(result.get("artifact"), Mapping) else {}
    status = str(result.get("stage") or result.get("status") or "unknown")
    market = str(identity.get("market") or "CN")
    company_key = request.company_key or str(target.get("company_key") or "")
    source_family = str(
        adapter.get("source_family")
        or artifact.get("source_family")
        or source_report.get("source_family")
        or ""
    )
    adapter_version = str(adapter.get("version") or artifact.get("adapter_version") or "")
    artifact_id = str(result.get("artifact_id") or artifact.get("artifact_id") or "")
    ok = bool(result.get("ok"))
    record_research_workflow_terminal(
        market=market,
        agent_type="analysis",
        status=status,
        ok=ok,
    )
    lowered_stage = status.lower()
    validation = result.get("validation") if isinstance(result.get("validation"), Mapping) else {}
    failures = [str(item).lower() for item in (validation.get("failures") or [])]
    if "identity" in lowered_stage or any("identity" in item for item in failures):
        record_research_validation_failure(
            market=market,
            agent_type="analysis",
            failure="identity_mismatch",
        )
    if "citation" in lowered_stage or any("citation" in item for item in failures):
        record_research_validation_failure(
            market=market,
            agent_type="analysis",
            failure="citation_failure",
        )
    emit_research_event(
        logger,
        event,
        agent_type="analysis",
        market=market,
        company_key=company_key,
        research_identity=identity,
        source_family=source_family,
        adapter_version=adapter_version,
        artifact_id=artifact_id,
        status=status,
        company_id=str(identity.get("company_id") or ""),
        filing_id=str(identity.get("filing_id") or ""),
        parse_run_id=str(identity.get("parse_run_id") or ""),
        company_ref=_company_key_summary(company_key),
    )
    return AnalysisReportWorkflowResponse(True, format_analysis_report_workflow_reply(result), result)


def _run_formal_analysis_report_workflow(
    request: AnalysisReportWorkflowRequest,
    *,
    script: Path,
    timeout: int | float,
    package_resolver: Callable[[Mapping[str, Any]], Any] | None,
    bundle_builder: Callable[..., dict[str, Any]] | None,
    bundle_writer: Callable[[Path, Mapping[str, Any]], None] | None,
) -> AnalysisReportWorkflowResponse:
    resolver = package_resolver or _default_package_resolver
    try:
        package = resolver(dict(request.context_payload or {}))
        target = _package_research_target(package)
        target_identity = target.get("research_identity") if isinstance(target.get("research_identity"), Mapping) else {}
        if not _identity_matches(request.research_identity or {}, target_identity):
            return _workflow_error(
                request,
                stage="research_identity_mismatch",
                next_action="页面选择身份与服务端权威报告身份不一致，请重新选择源报告。",
            )
        source_report = target.get("source_report") if isinstance(target.get("source_report"), Mapping) else {}
        if str(source_report.get("report_id") or "") != request.report_id:
            return _workflow_error(
                request,
                stage="research_identity_mismatch",
                next_action="页面源报告与服务端权威 report_id 不一致，请重新选择源报告。",
            )
        build_bundle, write_bundle = (
            (bundle_builder, bundle_writer)
            if bundle_builder is not None and bundle_writer is not None
            else _load_bundle_functions(script.parent)
        )
        prefix = _formal_output_prefix(package, target)
        work_dir = prefix.parent / ".work" / prefix.name
        bundle_path = work_dir / "analysis_input_bundle.json"
        bundle = build_bundle(
            research_target=target,
            company_dir=Path(str(_package_value(package, "company_dir", ""))),
            report_dir=Path(str(_package_value(package, "report_dir", ""))),
            manifest_path=Path(str(_package_value(package, "manifest_path", ""))),
        )
        write_bundle(bundle_path, bundle)
    except Exception as exc:
        code = str(getattr(exc, "code", "source_package_not_ready"))
        details = getattr(exc, "details", None)
        return _workflow_error(
            request,
            stage=code,
            next_action=str(exc),
            details=details if isinstance(details, Mapping) else None,
        )

    cmd = [
        sys.executable,
        str(script),
        "--input-bundle",
        str(bundle_path),
        "--output-prefix",
        str(prefix),
        "--force",
    ]
    if request.allow_overwrite:
        cmd.append("--allow-overwrite")
    try:
        payload, completed = _run_json_command(cmd, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        return _workflow_error(
            request,
            stage="timeout",
            next_action=f"正式报告流水线超时: {exc}",
        )
    if not payload:
        payload = {
            "ok": False,
            "stage": "invalid_stdout",
            "returncode": completed.returncode,
            "next_action": "正式报告脚本未返回合法 JSON，请查看服务端任务日志。",
        }
    payload.setdefault("company_query", request.company_query)
    payload.setdefault("year", source_report.get("fiscal_year"))
    payload.setdefault("research_identity", dict(target_identity))
    payload.setdefault("source_report", dict(source_report))
    payload.setdefault("adapter", dict(bundle.get("adapter") or {}))
    payload.setdefault("pipeline_mode", "formal_analysis_input_bundle")
    payload["analysis_report_workflow"] = {
        "script": script.name,
        "pipeline_mode": "formal_analysis_input_bundle",
        "company_key_summary": _company_key_summary(request.company_key),
        "returncode": completed.returncode,
    }
    return _workflow_response(request, payload, event="formal_analysis_workflow_completed")


def format_analysis_report_workflow_reply(result: dict[str, Any]) -> str:
    ok = bool(result.get("ok"))
    stage = str(result.get("stage") or "unknown")
    company_query = str(result.get("company_query") or "")
    year = str(result.get("year") or "")
    files = result.get("files") if isinstance(result.get("files"), dict) else {}
    checkpoints = result.get("checkpoints") if isinstance(result.get("checkpoints"), dict) else {}
    html_path = files.get("html")
    artifact_id = str(result.get("artifact_id") or "").strip()
    html_url = (
        f"/api/research-universe/artifacts/{quote(artifact_id, safe='')}/content"
        if artifact_id
        else _wiki_report_url(html_path)
    )
    formal_mode = str(result.get("pipeline_mode") or "") == "formal_analysis_input_bundle"
    if formal_mode:
        title = "已根据所选源报告完成正式分析报告" if ok else "所选源报告的正式分析未完成"
    else:
        title = "已使用 research-pack 报告生成器完成正式分析报告" if ok else "research-pack 报告生成未完成"

    lines = [
        f"**{title}**",
        "",
        f"- 公司请求: `{company_query}`",
        f"- {'源报告' if formal_mode else '年度'}: `{str((result.get('source_report') or {}).get('report_id') or year)}`",
        f"- 流水线阶段: `{stage}`",
        (
            f"- 输入适配: `{str((result.get('adapter') or {}).get('source_family') or 'unknown')}@{str((result.get('adapter') or {}).get('version') or 'unknown')}`"
            if formal_mode
            else "- 固化能力: `run_analysis_report.py --use-research-packs --research-subagent-mode deterministic`"
        ),
        (
            "- 输出契约: `siq_analysis_report_v2 + siq_agent_artifact_v2`"
            if formal_mode
            else "- 图表模板: `html_renderer_v2 + financial_chart_design` 收支拆解/利润桥组件"
        ),
        f"- 质量验收: `{_validation_status(result)}`",
    ]
    if html_url:
        lines.append(f"- 打开报告: [HTML 报告]({html_url})")
    if html_path and not formal_mode:
        lines.append(f"- HTML: `{_relative(html_path)}`")
    if files.get("md") and not formal_mode:
        lines.append(f"- Markdown: `{_relative(files.get('md'))}`")
    if files.get("json") and not formal_mode:
        lines.append(f"- JSON: `{_relative(files.get('json'))}`")
    if checkpoints.get("research_pack_validation"):
        lines.append(f"- Research pack 校验: `{_relative(checkpoints.get('research_pack_validation'))}`")
    if checkpoints.get("research_subagent_run_manifest"):
        lines.append(f"- 子智能体运行清单: `{_relative(checkpoints.get('research_subagent_run_manifest'))}`")
    next_action = str(result.get("next_action") or "").strip()
    if next_action and not ok:
        lines.extend(["", f"下一步: {next_action}"])
    return "\n".join(lines)


def run_analysis_report_workflow(
    request: AnalysisReportWorkflowRequest,
    *,
    timeout: int | float = DEFAULT_TIMEOUT_SECONDS,
    package_resolver: Callable[[Mapping[str, Any]], Any] | None = None,
    bundle_builder: Callable[..., dict[str, Any]] | None = None,
    bundle_writer: Callable[[Path, Mapping[str, Any]], None] | None = None,
) -> AnalysisReportWorkflowResponse:
    requested_market = str((request.research_identity or {}).get("market") or "").upper()
    multi_market_target = request.formal_target and requested_market in MULTI_MARKET_ANALYSIS_MARKETS
    if request.validation_error and multi_market_target:
        return _workflow_error(
            request,
            stage=str(request.validation_error.get("code") or "research_target_incomplete"),
            next_action="请选择确切市场、公司和已解析源报告后重试。",
            details=request.validation_error,
        )
    if request.formal_target and requested_market not in {"", "CN", *MULTI_MARKET_ANALYSIS_MARKETS}:
        return _workflow_error(
            request,
            stage="unsupported_market",
            next_action=f"当前分析链路暂不支持市场 {requested_market}。",
        )

    if multi_market_target and not _multi_market_research_enabled():
        return _workflow_error(
            request,
            stage="multi_market_research_disabled",
            next_action="全市场正式分析功能当前未启用。",
        )

    script = (
        latest_multi_market_report_script()
        if multi_market_target
        else latest_research_pack_report_script()
    )
    if script is None:
        profile = MULTI_MARKET_ANALYSIS_PROFILE if multi_market_target else LEGACY_ANALYSIS_PROFILE
        result = {
            "ok": False,
            "stage": "script_missing",
            "company_query": request.company_query,
            "year": request.year,
            "next_action": f"未找到可用的 run_analysis_report.py，请同步 {profile} profile scripts。",
        }
        return _workflow_response(request, result)

    if multi_market_target:
        return _run_formal_analysis_report_workflow(
            request,
            script=script,
            timeout=timeout,
            package_resolver=package_resolver,
            bundle_builder=bundle_builder,
            bundle_writer=bundle_writer,
        )

    try:
        resolved, resolve_completed = _run_json_command(
            [sys.executable, str(script.parent / "resolve_company.py"), "--company", request.company_query, "--year", str(request.year)],
            timeout=min(timeout, 120),
        )
    except subprocess.TimeoutExpired as exc:
        result = {
            "ok": False,
            "stage": "resolve_timeout",
            "company_query": request.company_query,
            "year": request.year,
            "next_action": f"公司解析超时: {exc}",
        }
        return _workflow_response(request, result)

    if not resolved.get("ok"):
        result = {
            "ok": False,
            "stage": "resolve_failed",
            "company_query": request.company_query,
            "year": request.year,
            "resolve": resolved,
            "stderr": (resolve_completed.stderr or "").strip()[-2000:],
            "next_action": "请在当前页面选择公司，或在消息中提供唯一股票代码/company_id。",
        }
        return _workflow_response(request, result)

    cmd = [
        sys.executable,
        str(script),
        "--company",
        request.company_query,
        "--year",
        str(request.year),
        "--use-research-packs",
        "--research-subagent-mode",
        request.research_subagent_mode,
        "--force",
    ]
    if request.allow_overwrite:
        cmd.append("--allow-overwrite")
    else:
        cmd.extend(["--output-prefix", str(_unique_output_prefix(resolved, request.year))])
    if request.prompt:
        cmd.extend(["--research-subagent-prompt", request.prompt])

    try:
        payload, completed = _run_json_command(cmd, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        payload = {
            "ok": False,
            "stage": "timeout",
            "company_query": request.company_query,
            "year": request.year,
            "next_action": f"报告流水线超时: {exc}",
        }
        return _workflow_response(request, payload)

    if not payload:
        payload = {
            "ok": False,
            "stage": "invalid_stdout",
            "company_query": request.company_query,
            "year": request.year,
            "returncode": completed.returncode,
            "stdout": (completed.stdout or "").strip()[-2000:],
            "stderr": (completed.stderr or "").strip()[-2000:],
            "next_action": "报告脚本未返回合法 JSON，请查看 stdout/stderr。",
        }
    payload.setdefault("company_query", request.company_query)
    payload.setdefault("year", request.year)
    payload["analysis_report_workflow"] = {
        "script": str(script),
        "research_packs_required": True,
        "research_subagent_mode": request.research_subagent_mode,
        "allow_overwrite": request.allow_overwrite,
        "returncode": completed.returncode,
    }
    return _workflow_response(request, payload)
