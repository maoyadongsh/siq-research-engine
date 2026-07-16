"""Deterministic continuous-tracking report workflow for the tracking agent."""

from __future__ import annotations

import hashlib
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
from urllib.parse import quote

from services.command_runner import run_command
from services.observability import (
    emit_research_event,
    record_research_validation_failure,
    record_research_workflow_terminal,
)
from services.path_config import PROJECT_ROOT, WIKI_ROOT
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
from services.research_universe_contracts import ResearchUniverseError

DEFAULT_TIMEOUT_SECONDS = int(os.getenv("SIQ_TRACKING_WORKFLOW_TIMEOUT_SECONDS", "1200"))
logger = logging.getLogger(__name__)

TRACKING_ACTION_RE = re.compile(r"(生成|执行|运行|刷新|重跑|重新生成|产出|创建|做一份|出一份|开启|更新)")
TRACKING_OBJECT_RE = re.compile(r"(持续跟踪|跟踪报告|跟踪面板|跟踪事项|预警报告|预警面板|tracking)", re.IGNORECASE)
TRACKING_META_QUESTION_RE = re.compile(r"(为什么|为何|原因|怎么没有|没有调用|没调用|没有固化|没固化|如何设计|怎么设计)")
STOCK_CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")
MULTI_MARKET_TRACKING_MARKETS = frozenset({"HK", "US", "EU", "KR", "JP"})


@dataclass(frozen=True)
class TrackingWorkflowRequest:
    company_query: str
    skip_sentiment: bool = False
    use_search: bool = True
    allow_simulated_sentiment: bool = False
    cleanup_html: bool = False
    strict: bool = False
    update_analysis: bool = False
    prompt: str = ""
    session_id: str = ""
    research_context: dict[str, Any] | None = None
    upstream_analysis_artifact_id: str = ""


@dataclass(frozen=True)
class TrackingWorkflowResponse:
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


def _context_market(context: Any | None) -> str:
    raw = _context_dict(context)
    target = raw.get("research_target") if isinstance(raw.get("research_target"), Mapping) else {}
    identity = target.get("research_identity") if isinstance(target.get("research_identity"), Mapping) else {}
    company = raw.get("company") if isinstance(raw.get("company"), Mapping) else {}
    return str(
        identity.get("market")
        or target.get("market")
        or raw.get("market")
        or company.get("market")
        or ""
    ).strip().upper()


def _uses_multi_market_tracking(context: Any | None) -> bool:
    return _context_market(context) in MULTI_MARKET_TRACKING_MARKETS


def _has_explicit_non_cn_market(context: Any | None) -> bool:
    market = _context_market(context)
    return bool(market and market != "CN")


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


def is_tracking_generation_request(message: str, context: Any | None = None) -> bool:
    text = (message or "").strip()
    if not text or TRACKING_META_QUESTION_RE.search(text):
        return False
    return bool(TRACKING_ACTION_RE.search(text) and TRACKING_OBJECT_RE.search(text))


def build_tracking_workflow_request(message: str, context: Any | None = None) -> TrackingWorkflowRequest | None:
    if not is_tracking_generation_request(message, context):
        return None
    company_query = _extract_company_query(message, context)
    if not company_query:
        return None
    text = message or ""
    raw_context = _context_dict(context)
    structured_context = raw_context if _has_explicit_non_cn_market(raw_context) else None
    return TrackingWorkflowRequest(
        company_query=company_query,
        skip_sentiment=bool(re.search(r"(跳过|不跑|不要|禁用).{0,8}(舆情|sentiment)", text, re.IGNORECASE)),
        use_search=not bool(re.search(r"(禁用|不要|不使用|关闭).{0,8}(搜索|联网|search)", text, re.IGNORECASE)),
        allow_simulated_sentiment=bool(re.search(r"(允许|使用|启用).{0,8}(模拟舆情|模拟数据|simulated)", text, re.IGNORECASE)),
        cleanup_html=bool(re.search(r"(清理|归档|cleanup).{0,8}(html|HTML|历史报告|旧报告)", text)),
        strict=bool(re.search(r"(严格模式|strict)", text, re.IGNORECASE)),
        update_analysis=bool(re.search(r"(写回|更新|改写).{0,12}(analysis|分析报告|原报告)", text, re.IGNORECASE)),
        prompt=text.strip(),
        research_context=structured_context,
        upstream_analysis_artifact_id=(
            upstream_analysis_artifact_id(raw_context) if structured_context is not None else ""
        ),
    )


def _tracking_script(*, multi_market: bool = False) -> Path:
    scripts_dir = "scripts_multi_market" if multi_market else "scripts"
    return PROJECT_ROOT / "data" / "wiki" / "tracking" / scripts_dir / "run_all.py"


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


def _company_payload(company: dict[str, Any]) -> dict[str, str]:
    return {
        "company_id": str(company.get("company_id") or ""),
        "stock_code": str(company.get("stock_code") or ""),
        "company_short_name": str(company.get("company_short_name") or ""),
        "company_full_name": str(company.get("company_full_name") or ""),
        "company_path": str(company.get("company_path") or ""),
    }


def _resolve_company(company_query: str) -> dict[str, str] | None:
    query = _normalize(company_query)
    if not query:
        return None

    best: tuple[int, dict[str, str]] | None = None
    for company in _load_catalog():
        if not isinstance(company, dict):
            continue
        values = [
            company.get("company_id"),
            company.get("stock_code"),
            company.get("company_short_name"),
            company.get("company_full_name"),
            company.get("company_path"),
            *(company.get("aliases") or []),
        ]
        normalized_values = [_normalize(str(value or "")) for value in values]
        if any(query == value for value in normalized_values if value):
            return _company_payload(company)
        containment_scores = [len(value) for value in normalized_values if value and value in query]
        if containment_scores:
            score = max(containment_scores)
            if best is None or score > best[0]:
                best = (score, _company_payload(company))

    if best is not None:
        return best[1]
    match = STOCK_CODE_RE.search(company_query)
    if match:
        code = match.group(1)
        return {"company_id": code, "stock_code": code, "company_short_name": code, "company_path": ""}
    return None


def _company_dir(company: dict[str, str]) -> Path:
    company_path = company.get("company_path")
    if company_path:
        return WIKI_ROOT / company_path
    company_id = company.get("company_id") or company.get("stock_code") or ""
    return WIKI_ROOT / "companies" / company_id


def _load_stdout_json(completed: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    stdout = (completed.stdout or "").strip()
    if not stdout:
        return {}
    for index in range(len(stdout) - 1, -1, -1):
        if stdout[index] != "{":
            continue
        try:
            payload = json.loads(stdout[index:])
        except json.JSONDecodeError:
            continue
        return payload if isinstance(payload, dict) else {}
    return {}


def _relative(path: str | Path | None) -> str:
    if not path:
        return ""
    raw = Path(str(path))
    try:
        return raw.resolve().relative_to(PROJECT_ROOT).as_posix()
    except Exception:
        return str(path)


def _wiki_tracking_url(html_path: str | Path | None) -> str:
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
        f"/api/wiki/companies/{quote(company_dir, safe='')}/tracking/"
        f"{quote(path.name, safe='')}"
    )


def _latest_html_from_manifest(company_dir: Path) -> Path | None:
    manifest_path = company_dir / "tracking" / "report_manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        manifest = {}
    latest = str(manifest.get("latest_report") or "").strip()
    candidate = company_dir / "tracking" / latest if latest else None
    if candidate and candidate.exists():
        return candidate
    reports = sorted((company_dir / "tracking").glob("*.html"))
    reports = [path for path in reports if path.name != "latest.html"]
    return reports[-1] if reports else None


def _latest_analysis_source(company_dir: Path) -> Path | None:
    analysis_dir = company_dir / "analysis"
    reports = [path for path in analysis_dir.glob("*.md") if path.is_file()]
    if not reports:
        return None
    return max(reports, key=lambda path: (path.stat().st_mtime_ns, path.name))


def _module_statuses(result: dict[str, Any]) -> str:
    modules = result.get("modules") if isinstance(result.get("modules"), dict) else {}
    if not modules:
        return "未返回"
    return " ".join(f"{name}={info.get('status', 'unknown')}" for name, info in modules.items() if isinstance(info, dict))


def format_tracking_workflow_reply(result: dict[str, Any]) -> str:
    status = str(result.get("status") or result.get("stage") or "unknown")
    ok = bool(result.get("ok"))
    title = (
        "已生成降级持续跟踪报告"
        if ok and status in {"partial_success", "degraded"}
        else "已生成正式持续跟踪报告"
        if ok
        else "持续跟踪报告生成未完全通过"
    )
    html_path = result.get("html_path") or ""
    html_url = str(result.get("html_url") or "") or _wiki_tracking_url(html_path)
    citation = result.get("citation_check") if isinstance(result.get("citation_check"), dict) else {}
    citation_status = "通过" if citation.get("passed") else f"需复核，issues={len(citation.get('issues') or [])}"

    lines = [
        f"**{title}**",
        "",
        f"- 公司请求: `{result.get('company_query') or ''}`",
        f"- 公司: `{result.get('stock_code') or ''}-{result.get('company_name') or ''}`",
        f"- 流水线状态: `{status}`",
        "- 固化能力: `data/wiki/tracking/scripts/run_all.py` 六模块流水线",
        f"- 模块状态: `{_module_statuses(result)}`",
        f"- 证据链校验: `{citation_status}`",
    ]
    if html_url:
        lines.append(f"- 打开报告: [HTML 跟踪报告]({html_url})")
    if html_path:
        lines.append(f"- HTML: `{_relative(html_path)}`")
    for key, label in (
        ("tracking_items_path", "跟踪事项"),
        ("metrics_path", "指标面板"),
        ("alerts_path", "预警记录"),
        ("updates_path", "更新记录"),
    ):
        if result.get(key):
            lines.append(f"- {label}: `{_relative(result.get(key))}`")
    next_action = str(result.get("next_action") or "").strip()
    if next_action and not ok:
        lines.extend(["", f"下一步: {next_action}"])
    return "\n".join(lines)


def _extract_artifact_paths(summary: dict[str, Any], company_dir: Path) -> dict[str, str]:
    modules = summary.get("modules") if isinstance(summary.get("modules"), dict) else {}

    def module_path(name: str) -> str:
        module = modules.get(name)
        return str(module.get("path") or "") if isinstance(module, dict) else ""

    html_path = module_path("module6")
    if not html_path:
        latest = _latest_html_from_manifest(company_dir)
        html_path = str(latest) if latest else ""
    return {
        "tracking_items_path": module_path("module1"),
        "metrics_path": module_path("module3"),
        "alerts_path": module_path("module4"),
        "updates_path": module_path("module5"),
        "html_path": html_path,
    }


_CITATION_SOURCE_KEYS = {
    "source_path",
    "file",
    "task_id",
    "pdf_task_id",
    "evidence_id",
    "report_id",
    "source_url",
    "local_source_id",
}
_CITATION_LOCATOR_KEYS = {
    "pdf_page",
    "pdf_page_number",
    "table_index",
    "table_id",
    "md_line",
    "section_id",
    "html_anchor",
    "xpath",
    "xbrl_fact_id",
    "xbrl_concept",
    "chunk_index",
    "quote",
}


def _collect_citation_candidates(value: Any, output: list[dict[str, Any]]) -> None:
    if isinstance(value, Mapping):
        has_source = any(
            value.get(key) not in (None, "", [], {})
            for key in _CITATION_SOURCE_KEYS
        )
        has_locator = any(
            value.get(key) not in (None, "", [], {})
            for key in _CITATION_LOCATOR_KEYS
        )
        if has_source and has_locator:
            output.append(dict(value))
        for child in value.values():
            _collect_citation_candidates(child, output)
    elif isinstance(value, list | tuple):
        for child in value:
            _collect_citation_candidates(child, output)


def _tracking_citations(summary: Mapping[str, Any], artifact_paths: Mapping[str, str]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    _collect_citation_candidates(summary.get("citations") or summary.get("evidence_refs") or [], candidates)
    for key, path_value in artifact_paths.items():
        if not path_value or key == "html_path":
            continue
        path = Path(path_value)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in re.finditer(
            r"task_id[\"']?\s*[:=]\s*[\"']?([0-9a-fA-F-]{8,})[\"']?.{0,240}?"
            r"(?:pdf_page|pdf_page_number)[\"']?\s*[:=]\s*[\"']?(\d+)",
            text,
            re.DOTALL,
        ):
            candidates.append(
                {
                    "source_type": "tracking_artifact",
                    "source_path": str(path),
                    "task_id": match.group(1),
                    "pdf_page": int(match.group(2)),
                }
            )
        for block in re.findall(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE):
            try:
                payload = json.loads(block)
            except json.JSONDecodeError:
                continue
            _collect_citation_candidates(payload, candidates)
    return normalize_citations(candidates, default_source_type="tracking_artifact")


def _analysis_baseline_citations(structured_target: Any) -> list[dict[str, Any]]:
    resolved = structured_target.analysis_artifact
    sidecar_path = resolved.sidecar_path
    if sidecar_path is None or not sidecar_path.is_file():
        return []
    try:
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return []
    metadata = sidecar.get("metadata") if isinstance(sidecar.get("metadata"), Mapping) else {}
    filename = str(metadata.get("json_file") or "").strip()
    relative = Path(filename)
    if (
        not filename
        or relative.is_absolute()
        or len(relative.parts) != 1
        or relative.suffix.lower() != ".json"
    ):
        return []
    analysis_dir = resolved.html_path.parent.resolve()
    json_path = (analysis_dir / relative).resolve()
    try:
        json_path.relative_to(analysis_dir)
    except ValueError:
        return []
    try:
        json_bytes = json_path.read_bytes()
        content_hashes = (
            metadata.get("content_hashes")
            if isinstance(metadata.get("content_hashes"), Mapping)
            else {}
        )
        expected_hash = str(content_hashes.get("json") or "").removeprefix("sha256:").lower()
        if not expected_hash or hashlib.sha256(json_bytes).hexdigest() != expected_hash:
            return []
        payload = json.loads(json_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return []
    if not isinstance(payload, Mapping):
        return []
    expected_identity = structured_target.package.research_identity.to_dict()
    payload_identity = payload.get("research_identity")
    if not isinstance(payload_identity, Mapping) or any(
        str(payload_identity.get(field) or "") != str(expected_identity.get(field) or "")
        for field in ("market", "company_id", "filing_id", "parse_run_id")
    ):
        return []
    candidates: list[dict[str, Any]] = []
    _collect_citation_candidates(payload.get("evidence_refs") or [], candidates)
    citations = normalize_citations(candidates, default_source_type="analysis_baseline")
    report_id = structured_target.package.report_id
    return [
        citation
        for citation in citations
        if citation_has_locator(citation)
        and str(citation.get("report_id") or report_id) == report_id
        and (
            not isinstance(citation.get("research_identity"), Mapping)
            or all(
                str(citation["research_identity"].get(field) or "")
                == str(expected_identity.get(field) or "")
                for field in ("market", "company_id", "filing_id", "parse_run_id")
            )
        )
    ]


def _workflow_response(
    request: TrackingWorkflowRequest,
    result: dict[str, Any],
    *,
    event: str = "tracking_workflow_finished",
) -> TrackingWorkflowResponse:
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
        agent_type="tracking",
        status=status,
        ok=ok,
    )
    validation = result.get("validation_result") if isinstance(result.get("validation_result"), Mapping) else {}
    failures = [str(item).lower() for item in (validation.get("failures") or [])]
    lowered_stage = status.lower()
    if "identity" in lowered_stage or any("identity" in item for item in failures):
        record_research_validation_failure(
            market=market,
            agent_type="tracking",
            failure="identity_mismatch",
        )
    if "citation" in lowered_stage or any("citation" in item for item in failures):
        record_research_validation_failure(
            market=market,
            agent_type="tracking",
            failure="citation_failure",
        )
    emit_research_event(
        logger,
        event,
        agent_type="tracking",
        market=market,
        company_key=company_key,
        research_identity=identity,
        source_family=source_family,
        adapter_version=adapter_version,
        artifact_id=artifact_id,
        status=status,
    )
    return TrackingWorkflowResponse(True, format_tracking_workflow_reply(result), result)


def run_tracking_workflow(
    request: TrackingWorkflowRequest,
    *,
    timeout: int | float = DEFAULT_TIMEOUT_SECONDS,
) -> TrackingWorkflowResponse:
    structured_market = _context_market(request.research_context)
    if (
        request.research_context
        and _has_explicit_non_cn_market(request.research_context)
        and structured_market not in MULTI_MARKET_TRACKING_MARKETS
    ):
        result = {
            "ok": False,
            "stage": "market_not_supported",
            "company_query": request.company_query,
            "next_action": f"当前持续跟踪链路暂不支持市场 {structured_market}。",
        }
        return _workflow_response(request, result)
    multi_market = _uses_multi_market_tracking(request.research_context)
    script = _tracking_script(multi_market=multi_market)
    if not script.is_file():
        result = {
            "ok": False,
            "stage": "script_missing",
            "company_query": request.company_query,
            "next_action": "未找到 data/wiki/tracking/scripts/run_all.py，请同步 tracking 生产脚本。",
        }
        return _workflow_response(request, result)
    structured_target = None
    host_degraded_reasons: list[str] = []
    if multi_market:
        try:
            structured_target = resolve_specialist_target(
                request.research_context,
                agent_type="tracking",
                artifact_id=request.upstream_analysis_artifact_id,
            )
        except ResearchUniverseError as exc:
            result = {
                "ok": False,
                "stage": exc.code,
                "company_query": request.company_query,
                "next_action": exc.message,
            }
            return _workflow_response(request, result)
        package = structured_target.package
        target = package.research_target
        company = {
            "company_id": target.research_identity.company_id,
            "stock_code": target.display_code,
            "company_short_name": target.display_name,
            "company_path": str(package.company_dir.relative_to(WIKI_ROOT)),
        }
        company_dir = package.company_dir
        if request.skip_sentiment:
            host_degraded_reasons.append("sentiment_skipped_by_request")
        if request.allow_simulated_sentiment:
            host_degraded_reasons.append("simulated_sentiment_not_permitted")
        if request.update_analysis:
            host_degraded_reasons.append("analysis_baseline_writeback_not_permitted")
    else:
        company = _resolve_company(request.company_query)
        if company is None:
            result = {
                "ok": False,
                "stage": "company_resolve_failed",
                "company_query": request.company_query,
                "next_action": "请在当前页面选择公司，或在消息中提供唯一股票代码/company_id。",
            }
            return _workflow_response(request, result)
        company_dir = _company_dir(company)

    stock_code = company.get("stock_code") or company.get("company_id") or request.company_query
    company_name = company.get("company_short_name") or stock_code
    cmd = [sys.executable, str(script)]
    if structured_target is None:
        cmd.extend(["--stock", stock_code, "--company", company_name])
    cmd.extend(["--wiki-base", str(WIKI_ROOT), "--json-summary"])
    if request.skip_sentiment:
        cmd.append("--skip-sentiment")
    if not request.use_search:
        cmd.append("--no-search")
    if request.allow_simulated_sentiment and structured_target is None:
        cmd.append("--allow-simulated-sentiment")
    if request.cleanup_html:
        cmd.append("--cleanup-html")
    if request.strict:
        cmd.append("--strict")
    if request.update_analysis and structured_target is None:
        cmd.append("--update-analysis")

    env = os.environ.copy()
    env["SIQ_WIKI_ROOT"] = str(WIKI_ROOT)
    env["SIQ_WIKISET_ROOT"] = str(PROJECT_ROOT / "scripts" / "wiki" / "wikiset")
    try:
        if structured_target is not None:
            with materialized_target_bundle(structured_target, prefix="tracking") as bundle_path:
                completed = run_command(
                    [*cmd, "--target-json", str(bundle_path)],
                    cwd=PROJECT_ROOT,
                    timeout=timeout,
                    env=env,
                )
        else:
            completed = run_command(cmd, cwd=PROJECT_ROOT, timeout=timeout, env=env)
    except subprocess.TimeoutExpired as exc:
        result = {
            "ok": False,
            "stage": "timeout",
            "company_query": request.company_query,
            "stock_code": stock_code,
            "company_name": company_name,
            "next_action": f"持续跟踪工作流超时: {exc}",
        }
        return _workflow_response(request, result)

    summary = _load_stdout_json(completed)
    degraded_reasons = [
        str(item)
        for item in (summary.get("degraded_reasons") or [])
        if str(item).strip()
    ]
    for reason in host_degraded_reasons:
        if reason not in degraded_reasons:
            degraded_reasons.append(reason)
    summary["degraded_reasons"] = degraded_reasons
    artifact_paths = _extract_artifact_paths(summary, company_dir)
    html_exists = bool(artifact_paths.get("html_path") and Path(artifact_paths["html_path"]).exists())
    status = str(summary.get("status") or "unknown")
    citations = _tracking_citations(summary, artifact_paths)
    if not citations and structured_target is not None:
        citations = _analysis_baseline_citations(structured_target)
        if citations and "citation_fallback_analysis_baseline" not in degraded_reasons:
            degraded_reasons.append("citation_fallback_analysis_baseline")
    citation_check = summary.get("citation_check") if isinstance(summary.get("citation_check"), dict) else {}
    modules = summary.get("modules") if isinstance(summary.get("modules"), dict) else {}
    latest_analysis = _latest_analysis_source(company_dir) if structured_target is None else None
    source_report_path = str(
        structured_target.analysis_artifact.html_path
        if structured_target is not None
        else summary.get("source_report_path") or latest_analysis or ""
    )
    critical_modules = ("module1", "module3", "module4", "module5", "module6")
    critical_modules_completed = all(
        isinstance(modules.get(name), Mapping) and modules[name].get("status") == "success"
        for name in critical_modules
    )
    research_identity_consistent = True
    if structured_target is not None:
        returned_target = summary.get("research_target") if isinstance(summary.get("research_target"), Mapping) else {}
        returned_identity = (
            returned_target.get("research_identity")
            if isinstance(returned_target.get("research_identity"), Mapping)
            else {}
        )
        expected_identity = structured_target.package.research_identity.to_dict()
        research_identity_consistent = all(
            str(returned_identity.get(field) or "") == str(expected_identity.get(field) or "")
            for field in ("market", "company_id", "filing_id", "parse_run_id")
        )
    if multi_market:
        checks = {
            "command_completed": completed.returncode in {0, 2},
            "pipeline_completed": status in {"success", "partial_success"},
            "html_present": html_exists,
            "modules_reported": bool(modules),
            "critical_modules_completed": critical_modules_completed,
            "source_report_present": bool(source_report_path),
            "citation_validator_passed": citation_check.get("passed") is True,
            "citations_present": bool(citations),
            "citations_traceable": bool(citations) and all(citation_has_locator(item) for item in citations),
            "research_identity_consistent": research_identity_consistent,
        }
        # Partial source coverage is publishable only for the isolated
        # multi-market pipeline and remains explicitly degraded.
        non_blocking_checks = {"citation_validator_passed"}
    else:
        # Preserve the pre-existing A-share validation contract verbatim.
        checks = {
            "command_succeeded": completed.returncode == 0,
            "pipeline_succeeded": status == "success",
            "html_present": html_exists,
            "modules_reported": bool(modules),
            "source_report_present": bool(source_report_path),
            "citation_validator_passed": citation_check.get("passed") is True,
            "citations_present": bool(citations),
        }
        non_blocking_checks = set()
    failures = [
        name
        for name, passed in checks.items()
        if not passed and name not in non_blocking_checks
    ]
    validation_warnings = (
        ["citation_validation_incomplete"]
        if multi_market and citation_check.get("passed") is not True
        else []
    )
    if validation_warnings and "citation_validation_incomplete" not in degraded_reasons:
        degraded_reasons.append("citation_validation_incomplete")
    validation = SpecialistArtifactValidation(
        ok=not failures,
        checks=checks,
        failures=failures,
        warnings=validation_warnings,
    )
    ok = validation.ok
    html_path = Path(str(artifact_paths.get("html_path") or "")) if artifact_paths.get("html_path") else None
    artifact_html_path = html_path
    if multi_market and not ok and html_path and html_path.exists():
        draft_dir = company_dir / "tracking" / "_drafts"
        draft_dir.mkdir(parents=True, exist_ok=True)
        artifact_html_path = draft_dir / html_path.name
        html_path.replace(artifact_html_path)
    artifact = finalize_specialist_artifact(
        artifact_type="tracking",
        company_id=company.get("company_id") or stock_code,
        source_report_path=source_report_path,
        output_path=str(artifact_html_path or ""),
        html_url=_wiki_tracking_url(artifact_html_path) if ok and structured_target is None and artifact_html_path else "",
        citations=citations,
        validation_result=validation,
        profile="siq_tracking_multi_market" if multi_market else "siq_tracking",
        message=request.prompt or request.company_query,
        session_id=request.session_id,
        metadata={
            "modules": modules,
            "postgres_query_status": summary.get("postgres_query_status") or "not_run",
            "postgres_queries": summary.get("postgres_queries") or [],
            **(
                {
                    "degraded_reasons": degraded_reasons,
                    "research_identity": structured_target.package.research_identity.to_dict(),
                }
                if structured_target is not None
                else {}
            ),
        },
        specialist_facts={
            "tracking_facts": citations,
            "tracking_module_status": modules,
            "tracking_postgres_query_status": summary.get("postgres_query_status") or "not_run",
            "tracking_postgres_queries": summary.get("postgres_queries") or [],
            "tracking_postgres_facts": summary.get("postgres_facts") or [],
        },
    )
    if structured_target is not None:
        audit_dir = company_dir / "tracking" / "_audit"
        artifact_manifest_path = audit_dir / f"{artifact.audit_trace_id}.specialist-artifact.json"
    else:
        artifact_manifest_path = (
            artifact_html_path.with_suffix(".artifact.json")
            if artifact_html_path
            else company_dir / "tracking" / "tracking.artifact.json"
        )
    write_specialist_artifact_manifest(artifact, artifact_manifest_path)
    v2_artifact = None
    v2_manifest_path = None
    v2_html_path = None
    html_url = _wiki_tracking_url(artifact_html_path) if ok and artifact_html_path else ""
    if ok and structured_target is not None and html_path is not None:
        warnings = list(degraded_reasons)
        if status == "partial_success" and "pipeline_partial_success" not in warnings:
            warnings.append("pipeline_partial_success")
        if structured_target.package.research_target.source_report.quality_status == "warning":
            warnings.append("source_quality_warning")
        v2_status = "degraded" if warnings else "completed"
        v2_artifact, v2_manifest_path, v2_html_path = publish_agent_artifact_v2(
            structured_target,
            artifact_type="tracking",
            html_path=html_path,
            status=v2_status,
            adapter_version="market_tracking_v1",
            citation_count=len(citations),
            unresolved_count=len(citation_check.get("issues") or []),
            warnings=warnings,
            metadata={
                "audit_trace_id": artifact.audit_trace_id,
                "pipeline_status": status,
                "degraded_reasons": warnings,
                "module_status": {
                    name: info.get("status")
                    for name, info in modules.items()
                    if isinstance(info, Mapping)
                },
                "analysis_baseline_content_hash": structured_target.analysis_artifact.artifact.content_hash,
                "previous_tracking_checkpoint": summary.get("previous_tracking_checkpoint"),
                "checked_at": datetime.now().astimezone().isoformat(),
            },
        )
        html_url = f"/api/research-universe/artifacts/{v2_artifact.artifact_id}/content"
    result = {
        **summary,
        **artifact_paths,
        "ok": ok,
        "stage": v2_artifact.status if v2_artifact else "completed" if ok else status or "failed",
        "company_query": request.company_query,
        "stock_code": stock_code,
        "company_name": company_name,
        "company_path": str(company_dir),
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
        "finished_at": datetime.now().isoformat(),
    }
    if v2_html_path is not None:
        result["html_path"] = str(v2_html_path)
    if not ok:
        if multi_market:
            result["draft_html_path"] = str(artifact_html_path or "")
            result["html_path"] = ""
            result["html_url"] = ""
        else:
            result["draft_html_path"] = result.pop("html_path", "")
        result["next_action"] = "查看 validation_result 与 tracking workflow stdout/stderr，并确认模块状态、evidence 与 HTML/citation validator 可用。"
    return _workflow_response(request, result)
