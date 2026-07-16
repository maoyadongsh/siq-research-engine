"""Deterministic fact-check workflow for the factchecker agent."""

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
from typing import Any, Mapping
from urllib.parse import quote, unquote

from services.command_runner import run_command
from services.observability import (
    emit_research_event,
    record_research_validation_failure,
    record_research_workflow_terminal,
)
from services.path_config import PROJECT_ROOT, WIKI_ROOT
from services.research_universe_contracts import ResearchUniverseError
from services.specialist_artifact_contract import (
    SpecialistArtifactValidation,
    citation_has_locator,
    finalize_specialist_artifact,
    normalize_citations,
    write_specialist_artifact_manifest,
)
from services.specialist_research_target import (
    materialized_target_bundle,
    publish_agent_artifact_v2,
    resolve_specialist_target,
    upstream_analysis_artifact_id,
)

DEFAULT_YEAR = 2025
DEFAULT_TIMEOUT_SECONDS = int(os.getenv("SIQ_FACTCHECK_WORKFLOW_TIMEOUT_SECONDS", "900"))
logger = logging.getLogger(__name__)

MULTI_MARKET_FACTCHECK_MARKETS = frozenset({"HK", "US", "EU", "KR", "JP"})

FACTCHECK_ACTION_RE = re.compile(r"(生成|执行|运行|刷新|重跑|重新生成|产出|创建|做一份|出一份)")
FACTCHECK_OBJECT_RE = re.compile(r"(事实核查|事实核实|核查报告|核实报告|审校报告|校验报告|factcheck)", re.IGNORECASE)
FACTCHECK_META_QUESTION_RE = re.compile(r"(为什么|为何|原因|怎么没有|没有调用|没调用|没有固化|没固化)")
OVERWRITE_RE = re.compile(r"(覆盖|替换现有|覆盖现有|写回默认|更新现有|改写现有)")
YEAR_RE = re.compile(r"(20\d{2})\s*年?")
STOCK_CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")


@dataclass(frozen=True)
class FactcheckWorkflowRequest:
    company_query: str
    year: int = DEFAULT_YEAR
    report_path: Path | None = None
    allow_overwrite: bool = False
    session_id: str = ""
    research_context: dict[str, Any] | None = None
    upstream_analysis_artifact_id: str = ""


@dataclass(frozen=True)
class FactcheckWorkflowResponse:
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


def _context_report(context: Any | None) -> dict[str, Any]:
    raw = _context_dict(context).get("report")
    return raw if isinstance(raw, dict) else {}


def _context_market(context: Any | None) -> str:
    raw = _context_dict(context)
    company = raw.get("company") if isinstance(raw.get("company"), Mapping) else {}
    target = raw.get("research_target") if isinstance(raw.get("research_target"), Mapping) else {}
    identity = (
        target.get("research_identity")
        if isinstance(target.get("research_identity"), Mapping)
        else {}
    )
    market = str(raw.get("market") or company.get("market") or identity.get("market") or "").strip().upper()
    return "US" if market == "US_SEC" else market


def _has_explicit_non_cn_market(context: Any | None) -> bool:
    market = _context_market(context)
    return bool(market and market != "CN")


def _extract_year(message: str, context: Any | None) -> int:
    for source in (message, str(_context_report(context).get("filename") or "")):
        match = YEAR_RE.search(source or "")
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                continue
    return DEFAULT_YEAR


def _clean(value: str | None) -> str:
    return str(value or "").strip().strip(" :：,，。；;")


def _extract_company_query(message: str, context: Any | None) -> str:
    company = _context_company(context)
    for key in ("dir", "code", "name"):
        value = _clean(company.get(key))
        if value:
            return value
    match = STOCK_CODE_RE.search(message or "")
    if match:
        return match.group(1)
    return _clean(message)


def is_factcheck_generation_request(message: str, context: Any | None = None) -> bool:
    text = (message or "").strip()
    if not text or FACTCHECK_META_QUESTION_RE.search(text):
        return False
    return bool(FACTCHECK_ACTION_RE.search(text) and FACTCHECK_OBJECT_RE.search(text))


def _analysis_report_path_from_context(context: Any | None) -> Path | None:
    report = _context_report(context)
    if str(report.get("type") or "") != "analysis":
        return None
    value = str(report.get("url") or "").strip()
    filename = str(report.get("filename") or "").strip()
    company_dir = str(_context_company(context).get("dir") or "").strip()
    if value:
        path = _wiki_analysis_url_to_path(value)
        if path:
            return path
    if filename and company_dir:
        return WIKI_ROOT / "companies" / company_dir / "analysis" / filename
    return None


def _wiki_analysis_url_to_path(value: str) -> Path | None:
    text = unquote(value)
    match = re.search(r"(?:/api/wiki)?/companies/([^/]+)/analysis/([^?#\s]+)", text)
    if not match:
        return None
    company_dir, filename = match.groups()
    path = WIKI_ROOT / "companies" / company_dir / "analysis" / filename
    if path.suffix.lower() in {".html", ".json"}:
        path = path.with_suffix(".md")
    return path


def build_factcheck_workflow_request(message: str, context: Any | None = None) -> FactcheckWorkflowRequest | None:
    if not is_factcheck_generation_request(message, context):
        return None
    company_query = _extract_company_query(message, context)
    if not company_query:
        return None
    raw_context = _context_dict(context)
    structured_context = (
        raw_context
        if _has_explicit_non_cn_market(raw_context)
        else None
    )
    return FactcheckWorkflowRequest(
        company_query=company_query,
        year=_extract_year(message, context),
        report_path=_analysis_report_path_from_context(context),
        allow_overwrite=bool(OVERWRITE_RE.search(message or "")),
        research_context=structured_context,
        upstream_analysis_artifact_id=(
            upstream_analysis_artifact_id(raw_context) if structured_context is not None else ""
        ),
    )


def _candidate_script_paths(*, multi_market: bool = False) -> tuple[Path, ...]:
    profile = "siq_factchecker_multi_market" if multi_market else "siq_factchecker"
    return (
        PROJECT_ROOT / "agents" / "hermes" / "profiles" / profile / "scripts" / "factcheck_cli.py",
        PROJECT_ROOT / "data" / "hermes" / "home" / "profiles" / profile / "scripts" / "factcheck_cli.py",
    )


def _script_supports_report_path(script: Path) -> bool:
    if not script.is_file():
        return False
    try:
        source = script.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return "--report-path" in source and "--output" in source


def _script_supports_resolved_target(script: Path) -> bool:
    if not script.is_file():
        return False
    try:
        source = script.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return "--target-json" in source and "--wiki-root" in source and "--output" in source


def latest_factcheck_script(market: str = "CN") -> Path | None:
    multi_market = market in MULTI_MARKET_FACTCHECK_MARKETS
    for script in _candidate_script_paths(multi_market=multi_market):
        supported = (
            _script_supports_resolved_target(script)
            if multi_market
            else _script_supports_report_path(script)
        )
        if supported:
            return script
    return None


def _load_catalog() -> list[dict[str, Any]]:
    path = WIKI_ROOT / "_meta" / "company_catalog.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    companies = payload.get("companies")
    return companies if isinstance(companies, list) else []


def _normalize(value: str) -> str:
    return re.sub(r"[\s（）()\-_/]", "", str(value or "").lower())


def _resolve_company(company_query: str) -> dict[str, str] | None:
    query = _normalize(company_query)
    for company in _load_catalog():
        if not isinstance(company, dict):
            continue
        values = [
            company.get("company_id"),
            company.get("stock_code"),
            company.get("company_short_name"),
            company.get("company_full_name"),
            company.get("company_path"),
        ]
        if any(query == _normalize(str(value or "")) for value in values):
            return {
                "company_id": str(company.get("company_id") or ""),
                "stock_code": str(company.get("stock_code") or ""),
                "company_short_name": str(company.get("company_short_name") or ""),
                "company_path": str(company.get("company_path") or ""),
            }
    match = STOCK_CODE_RE.search(company_query)
    if match:
        code = match.group(1)
        return {"company_id": code, "stock_code": code, "company_short_name": code, "company_path": ""}
    return None


def _default_output_path(company: dict[str, str], year: int, allow_overwrite: bool) -> Path:
    company_path = company.get("company_path") or f"companies/{company.get('company_id') or company.get('stock_code')}"
    factcheck_dir = WIKI_ROOT / company_path / "factcheck"
    stock_code = company.get("stock_code") or "company"
    short_name = company.get("company_short_name") or stock_code
    if allow_overwrite:
        return factcheck_dir / f"{stock_code}-{short_name}-{year}-factcheck.json"
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    return factcheck_dir / f"{stock_code}-{short_name}-{year}-factcheck-{timestamp}.json"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _relative(path: str | Path | None) -> str:
    if not path:
        return ""
    raw = Path(str(path))
    try:
        return raw.resolve().relative_to(PROJECT_ROOT).as_posix()
    except Exception:
        return str(path)


def _wiki_factcheck_url(html_path: Path) -> str:
    parts = html_path.parts
    try:
        companies_index = parts.index("companies")
        company_dir = parts[companies_index + 1]
    except (ValueError, IndexError):
        return ""
    return (
        f"/api/wiki/companies/{quote(company_dir, safe='')}/factcheck/"
        f"{quote(html_path.name, safe='')}"
    )


def _factcheck_citations(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    candidates: list[Any] = []
    evidence_summary = payload.get("evidence_summary")
    if isinstance(evidence_summary, list):
        candidates.extend(evidence_summary)
    metric_evidence = payload.get("metric_evidence_map")
    if isinstance(metric_evidence, Mapping):
        candidates.extend(metric_evidence.values())
    checks = payload.get("checks")
    if isinstance(checks, Mapping):
        for check in checks.values():
            if not isinstance(check, Mapping):
                continue
            for issue in check.get("issues") or []:
                if isinstance(issue, Mapping):
                    candidates.extend(issue.get("evidence_refs") or [])
    # Normalized metrics remain available to the checker as structured facts,
    # but only evidence with a verifiable locator belongs in the public
    # citation contract.  In particular, a report_id plus a null XBRL/PDF
    # locator must not poison traceability for otherwise valid evidence.
    return [
        citation
        for citation in normalize_citations(
            candidates,
            default_source_type="factcheck_evidence",
        )
        if citation_has_locator(citation)
    ]


def _claim_verdicts(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    structured = payload.get("claim_verdicts")
    if isinstance(structured, list) and structured:
        return [
            {
                "claim_id": str(item.get("claim_id") or f"claim:{index}"),
                "claim": str(item.get("claim") or item.get("claim_id") or ""),
                "verdict": str(item.get("status") or item.get("verdict") or "unknown"),
                "reason": str(item.get("reason") or ""),
                "metric_key": str(item.get("metric_key") or ""),
                "period": str(item.get("period") or ""),
                "evidence_refs": normalize_citations(
                    item.get("evidence_refs") or [],
                    default_source_type="factcheck_evidence",
                ),
            }
            for index, item in enumerate(structured, start=1)
            if isinstance(item, Mapping)
        ]
    verdicts: list[dict[str, Any]] = []
    checks = payload.get("checks")
    if not isinstance(checks, Mapping):
        return verdicts
    for dimension, check in checks.items():
        if not isinstance(check, Mapping):
            continue
        issues = check.get("issues") or []
        if not issues:
            verdicts.append(
                {
                    "claim_id": str(dimension),
                    "claim": str(dimension),
                    "verdict": str(check.get("status") or "unknown"),
                    "reason": "",
                    "evidence_refs": [],
                }
            )
        for index, issue in enumerate(issues):
            if not isinstance(issue, Mapping):
                continue
            verdicts.append(
                {
                    "claim_id": f"{dimension}:{index + 1}",
                    "claim": str(issue.get("location") or issue.get("message") or dimension),
                    "verdict": str(issue.get("severity") or check.get("status") or "unknown"),
                    "reason": str(issue.get("message") or ""),
                    "evidence_refs": normalize_citations(
                        issue.get("evidence_refs") or [],
                        default_source_type="factcheck_evidence",
                    ),
                }
            )
    return verdicts


def _safe_nonnegative_count(value: Any, *, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        count = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return count if count >= 0 else default


def _structured_claim_counts(
    payload: Mapping[str, Any],
    summary: Mapping[str, Any],
) -> tuple[int, int, int, int]:
    """Return counts for actual claims, never legacy check-dimension placeholders."""

    raw_verdicts = payload.get("claim_verdicts")
    if isinstance(raw_verdicts, list):
        verdicts = [item for item in raw_verdicts if isinstance(item, Mapping)]
        statuses = [str(item.get("status") or item.get("verdict") or "").lower() for item in verdicts]
        return (
            len(verdicts),
            sum(status == "verified" for status in statuses),
            sum(status == "contradicted" for status in statuses),
            sum(status == "unsupported" for status in statuses),
        )
    checked = _safe_nonnegative_count(summary.get("checked_claim_count"))
    contradicted = min(checked, _safe_nonnegative_count(summary.get("contradicted_claim_count")))
    unsupported = min(
        max(0, checked - contradicted),
        _safe_nonnegative_count(summary.get("unsupported_claim_count")),
    )
    default_verified = max(0, checked - contradicted - unsupported)
    verified = min(
        max(0, checked - contradicted - unsupported),
        _safe_nonnegative_count(summary.get("verified_claim_count"), default=default_verified),
    )
    return checked, verified, contradicted, unsupported


def _workflow_response(
    request: FactcheckWorkflowRequest,
    result: dict[str, Any],
    *,
    event: str = "factcheck_workflow_finished",
) -> FactcheckWorkflowResponse:
    context = request.research_context if isinstance(request.research_context, Mapping) else {}
    target = context.get("research_target") if isinstance(context.get("research_target"), Mapping) else {}
    identity = target.get("research_identity") if isinstance(target.get("research_identity"), Mapping) else {}
    source_report = target.get("source_report") if isinstance(target.get("source_report"), Mapping) else {}
    company = context.get("company") if isinstance(context.get("company"), Mapping) else {}
    artifact = result.get("artifact") if isinstance(result.get("artifact"), Mapping) else {}
    status = str(result.get("stage") or result.get("status") or "unknown")
    market = str(identity.get("market") or context.get("market") or "CN")
    company_key = str(target.get("company_key") or company.get("company_key") or context.get("company_key") or "")
    source_family = str(artifact.get("source_family") or source_report.get("source_family") or "")
    adapter_version = str(artifact.get("adapter_version") or "")
    artifact_id = str(artifact.get("artifact_id") or "")
    ok = bool(result.get("ok"))
    record_research_workflow_terminal(
        market=market,
        agent_type="factcheck",
        status=status,
        ok=ok,
    )
    validation = result.get("validation_result") if isinstance(result.get("validation_result"), Mapping) else {}
    failures = [str(item).lower() for item in (validation.get("failures") or [])]
    lowered_stage = status.lower()
    if "identity" in lowered_stage or any("identity" in item for item in failures):
        record_research_validation_failure(
            market=market,
            agent_type="factcheck",
            failure="identity_mismatch",
        )
    if "citation" in lowered_stage or any("citation" in item for item in failures):
        record_research_validation_failure(
            market=market,
            agent_type="factcheck",
            failure="citation_failure",
        )
    emit_research_event(
        logger,
        event,
        agent_type="factcheck",
        market=market,
        company_key=company_key,
        research_identity=identity,
        source_family=source_family,
        adapter_version=adapter_version,
        artifact_id=artifact_id,
        status=status,
    )
    return FactcheckWorkflowResponse(True, format_factcheck_workflow_reply(result), result)


def format_factcheck_workflow_reply(result: dict[str, Any]) -> str:
    ok = bool(result.get("ok"))
    title = (
        "已生成降级事实核查报告"
        if ok and result.get("stage") == "degraded"
        else "已生成正式事实核查报告"
        if ok
        else "事实核查报告生成未完成"
    )
    json_path = Path(str(result.get("json_path") or "")) if result.get("json_path") else None
    html_path = Path(str(result.get("html_path") or "")) if result.get("html_path") else None
    html_url = str(result.get("html_url") or "") or (_wiki_factcheck_url(html_path) if html_path else "")
    payload = result.get("factcheck") if isinstance(result.get("factcheck"), dict) else {}
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    lines = [
        f"**{title}**",
        "",
        f"- 公司请求: `{result.get('company_query') or ''}`",
        f"- 年度: `{result.get('year') or ''}`",
        f"- 核查对象: `{payload.get('report_file') or result.get('report_path') or '自动选择最新分析报告'}`",
        f"- 审校结论: `{payload.get('verdict') or result.get('stage') or 'unknown'}`",
        f"- 问题计数: critical={summary.get('critical', 0)} warning={summary.get('warning', 0)} suggestion={summary.get('suggestion', 0)}",
    ]
    if html_url:
        lines.append(f"- 打开报告: [HTML 核查报告]({html_url})")
    if html_path:
        lines.append(f"- HTML: `{_relative(html_path)}`")
    if json_path:
        lines.append(f"- JSON: `{_relative(json_path)}`")
    next_action = str(result.get("next_action") or "").strip()
    if next_action and not ok:
        lines.extend(["", f"下一步: {next_action}"])
    return "\n".join(lines)


def run_factcheck_workflow(
    request: FactcheckWorkflowRequest,
    *,
    timeout: int | float = DEFAULT_TIMEOUT_SECONDS,
) -> FactcheckWorkflowResponse:
    structured_context = (
        request.research_context
        if request.research_context and _has_explicit_non_cn_market(request.research_context)
        else None
    )
    structured_market = _context_market(structured_context)
    if structured_context is not None and structured_market not in MULTI_MARKET_FACTCHECK_MARKETS:
        result = {
            "ok": False,
            "stage": "market_not_supported",
            "company_query": request.company_query,
            "year": request.year,
            "next_action": f"当前事实核查链路暂不支持市场 {structured_market}。",
        }
        return _workflow_response(request, result)
    script = latest_factcheck_script(structured_market or "CN")
    if script is None:
        result = {
            "ok": False,
            "stage": "script_missing",
            "company_query": request.company_query,
            "year": request.year,
            "next_action": "未找到支持 --report-path/--output 的 factcheck_cli.py，请同步 siq_factchecker profile scripts。",
        }
        return _workflow_response(request, result)
    structured_target = None
    if structured_context:
        try:
            structured_target = resolve_specialist_target(
                structured_context,
                agent_type="factcheck",
                artifact_id=request.upstream_analysis_artifact_id,
            )
        except ResearchUniverseError as exc:
            result = {
                "ok": False,
                "stage": exc.code,
                "company_query": request.company_query,
                "year": request.year,
                "next_action": exc.message,
            }
            return _workflow_response(request, result)
        package = structured_target.package
        target = package.research_target
        request_year = target.source_report.fiscal_year or request.year
        company = {
            "company_id": target.research_identity.company_id,
            "stock_code": target.display_code,
            "company_short_name": target.display_name,
            "company_path": str(package.company_dir.relative_to(WIKI_ROOT)),
        }
        timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        suffix = "" if request.allow_overwrite else f"-{timestamp}"
        output_path = package.output_dir_for("factcheck") / (
            f"{target.display_code}-{package.report_id}-factcheck{suffix}.json"
        )
        with materialized_target_bundle(structured_target, prefix="factcheck") as bundle_path:
            cmd = [
                sys.executable,
                str(script),
                "verify",
                "--target-json",
                str(bundle_path),
                "--wiki-root",
                str(WIKI_ROOT),
                "--output",
                str(output_path),
            ]
            try:
                completed = run_command(cmd, cwd=PROJECT_ROOT, timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                result = {
                    "ok": False,
                    "stage": "timeout",
                    "company_query": request.company_query,
                    "year": request_year,
                    "next_action": f"事实核查工作流超时: {exc}",
                }
                return _workflow_response(request, result)
        source_report_path = str(structured_target.analysis_artifact.html_path)
    else:
        company = _resolve_company(request.company_query)
        if company is None:
            result = {
                "ok": False,
                "stage": "company_resolve_failed",
                "company_query": request.company_query,
                "year": request.year,
                "next_action": "请在当前页面选择公司，或在消息中提供唯一股票代码/company_id。",
            }
            return _workflow_response(request, result)
        request_year = request.year
        output_path = _default_output_path(company, request.year, request.allow_overwrite)
        cmd = [
            sys.executable,
            str(script),
            "verify",
            company.get("company_id") or company.get("stock_code") or request.company_query,
            "--year",
            str(request.year),
            "--output",
            str(output_path),
        ]
        if request.report_path:
            cmd.extend(["--report-path", str(request.report_path)])
        try:
            completed = run_command(cmd, cwd=PROJECT_ROOT, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            result = {
                "ok": False,
                "stage": "timeout",
                "company_query": request.company_query,
                "year": request.year,
                "next_action": f"事实核查工作流超时: {exc}",
            }
            return _workflow_response(request, result)
        source_report_path = str(request.report_path or "")

    factcheck = _read_json(output_path)
    html_path = output_path.with_suffix(".html")
    citations = _factcheck_citations(factcheck)
    claim_verdicts = _claim_verdicts(factcheck)
    if not source_report_path and factcheck.get("report_file"):
        company_path = company.get("company_path") or f"companies/{company.get('company_id') or company.get('stock_code')}"
        source_report_path = str(WIKI_ROOT / company_path / "analysis" / str(factcheck["report_file"]))
    checks = {
        "command_succeeded": completed.returncode == 0,
        "payload_present": bool(factcheck),
        "html_present": html_path.exists(),
        "verdict_present": bool(str(factcheck.get("verdict") or "").strip()),
        "claim_verdicts_present": bool(claim_verdicts),
        "source_report_present": bool(source_report_path),
        "citations_present": bool(citations),
        "citations_traceable": bool(citations) and all(citation_has_locator(item) for item in citations),
        "research_identity_consistent": (
            structured_target is None
            or str(((factcheck.get("checks") or {}).get("identity_consistency") or {}).get("status") or "") == "pass"
        ),
    }
    failures = [name for name, passed in checks.items() if not passed]
    validation = SpecialistArtifactValidation(ok=not failures, checks=checks, failures=failures)
    ok = validation.ok
    artifact_output_path = output_path
    artifact_html_path = html_path
    if not ok:
        draft_dir = output_path.parent / "_drafts"
        draft_dir.mkdir(parents=True, exist_ok=True)
        artifact_output_path = draft_dir / output_path.name
        artifact_html_path = draft_dir / html_path.name
        if output_path.exists():
            output_path.replace(artifact_output_path)
        if html_path.exists():
            html_path.replace(artifact_html_path)
    artifact = finalize_specialist_artifact(
        artifact_type="factcheck",
        company_id=company.get("company_id") or company.get("stock_code") or request.company_query,
        source_report_path=source_report_path,
        output_path=str(artifact_output_path),
        html_url=_wiki_factcheck_url(artifact_html_path) if ok and structured_target is None else "",
        citations=citations,
        validation_result=validation,
        profile=("siq_factchecker_multi_market" if structured_target is not None else "siq_factchecker"),
        message=f"{request.company_query}:{request_year}",
        session_id=request.session_id,
        metadata={
            "verdict": factcheck.get("verdict"),
            "claim_verdicts": claim_verdicts,
            "research_identity": structured_target.package.research_identity.to_dict() if structured_target else None,
        },
        specialist_facts={"factcheck_claim_verdicts": claim_verdicts},
    )
    if structured_target is not None:
        audit_dir = output_path.parent / "_audit"
        artifact_manifest_path = audit_dir / f"{artifact.audit_trace_id}.specialist-artifact.json"
    else:
        artifact_manifest_path = artifact_output_path.with_suffix(".artifact.json")
    write_specialist_artifact_manifest(artifact, artifact_manifest_path)
    v2_artifact = None
    v2_manifest_path = None
    v2_html_path = None
    html_url = _wiki_factcheck_url(html_path) if ok else ""
    if ok and structured_target is not None:
        summary = factcheck.get("summary") if isinstance(factcheck.get("summary"), Mapping) else {}
        warnings = []
        if factcheck.get("verdict") != "approve":
            warnings.append(f"factcheck_verdict:{factcheck.get('verdict') or 'unknown'}")
        if structured_target.package.research_target.source_report.quality_status == "warning":
            warnings.append("source_quality_warning")
        checked_count, verified_count, contradicted_count, unsupported_count = (
            _structured_claim_counts(factcheck, summary)
        )
        identity_mismatch_count = _safe_nonnegative_count(summary.get("identity_mismatch_count"))
        citation_locator_failure_count = _safe_nonnegative_count(
            summary.get("citation_locator_failure_count")
        )
        reported_degraded_reasons = summary.get("degraded_reasons")
        if isinstance(reported_degraded_reasons, list):
            warnings.extend(str(item) for item in reported_degraded_reasons if str(item).strip())
        warnings = list(dict.fromkeys(warnings))
        v2_status = "degraded" if warnings else "completed"
        v2_artifact, v2_manifest_path, v2_html_path = publish_agent_artifact_v2(
            structured_target,
            artifact_type="factcheck",
            html_path=html_path,
            status=v2_status,
            adapter_version="market_factcheck_v1",
            citation_count=len(citations),
            unresolved_count=_safe_nonnegative_count(summary.get("critical")),
            warnings=warnings,
            metadata={
                "verdict": factcheck.get("verdict"),
                "audit_trace_id": artifact.audit_trace_id,
                "json_file": output_path.name,
                "checked_claim_count": checked_count,
                "verified_claim_count": verified_count,
                "contradicted_claim_count": contradicted_count,
                "unsupported_claim_count": unsupported_count,
                "identity_mismatch_count": identity_mismatch_count,
                "citation_locator_failure_count": citation_locator_failure_count,
                "degraded_reasons": warnings,
            },
        )
        html_url = f"/api/research-universe/artifacts/{v2_artifact.artifact_id}/content"
    result = {
        "ok": ok,
        "stage": v2_artifact.status if v2_artifact else "completed" if ok else "failed",
        "company_query": request.company_query,
        "year": request_year,
        "report_path": source_report_path,
        "json_path": str(output_path) if ok else "",
        "html_path": str(html_path) if ok else "",
        "draft_json_path": str(artifact_output_path) if not ok else "",
        "draft_html_path": str(artifact_html_path) if not ok else "",
        "factcheck": factcheck,
        "artifact": v2_artifact.to_dict() if v2_artifact else artifact.model_dump(),
        "artifact_manifest_path": str(artifact_manifest_path),
        "agent_artifact_v2_manifest_path": str(v2_manifest_path or ""),
        "agent_artifact_v2_html_path": str(v2_html_path or ""),
        "html_url": html_url,
        "audit_trace_id": artifact.audit_trace_id,
        "validation_result": validation.model_dump(),
        "returncode": completed.returncode,
        "stdout": (completed.stdout or "").strip()[-4000:],
        "stderr": (completed.stderr or "").strip()[-4000:],
    }
    if v2_html_path is not None:
        result["html_path"] = str(v2_html_path)
    if not ok:
        result["next_action"] = "查看 validation_result 与 factcheck_cli stdout/stderr，并补齐 claim verdict、metrics 和可回链 evidence。"
    return _workflow_response(request, result)
