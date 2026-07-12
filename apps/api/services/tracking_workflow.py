"""Deterministic continuous-tracking report workflow for the tracking agent."""

from __future__ import annotations

import json
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
from services.path_config import PROJECT_ROOT, WIKI_ROOT
from services.specialist_artifact_contract import (
    SpecialistArtifactValidation,
    finalize_specialist_artifact,
    normalize_citations,
    write_specialist_artifact_manifest,
)

DEFAULT_TIMEOUT_SECONDS = int(os.getenv("SIQ_TRACKING_WORKFLOW_TIMEOUT_SECONDS", "1200"))

TRACKING_ACTION_RE = re.compile(r"(生成|执行|运行|刷新|重跑|重新生成|产出|创建|做一份|出一份|开启|更新)")
TRACKING_OBJECT_RE = re.compile(r"(持续跟踪|跟踪报告|跟踪面板|跟踪事项|预警报告|预警面板|tracking)", re.IGNORECASE)
TRACKING_META_QUESTION_RE = re.compile(r"(为什么|为何|原因|怎么没有|没有调用|没调用|没有固化|没固化|如何设计|怎么设计)")
STOCK_CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")


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
    return TrackingWorkflowRequest(
        company_query=company_query,
        skip_sentiment=bool(re.search(r"(跳过|不跑|不要|禁用).{0,8}(舆情|sentiment)", text, re.IGNORECASE)),
        use_search=not bool(re.search(r"(禁用|不要|不使用|关闭).{0,8}(搜索|联网|search)", text, re.IGNORECASE)),
        allow_simulated_sentiment=bool(re.search(r"(允许|使用|启用).{0,8}(模拟舆情|模拟数据|simulated)", text, re.IGNORECASE)),
        cleanup_html=bool(re.search(r"(清理|归档|cleanup).{0,8}(html|HTML|历史报告|旧报告)", text)),
        strict=bool(re.search(r"(严格模式|strict)", text, re.IGNORECASE)),
        update_analysis=bool(re.search(r"(写回|更新|改写).{0,12}(analysis|分析报告|原报告)", text, re.IGNORECASE)),
        prompt=text.strip(),
    )


def _tracking_script() -> Path:
    return PROJECT_ROOT / "data" / "wiki" / "tracking" / "scripts" / "run_all.py"


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
    title = "已生成正式持续跟踪报告" if ok else "持续跟踪报告生成未完全通过"
    html_path = result.get("html_path") or ""
    html_url = _wiki_tracking_url(html_path)
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


def _tracking_citations(summary: Mapping[str, Any], artifact_paths: Mapping[str, str]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
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
    explicit = summary.get("citations") or summary.get("evidence_refs") or []
    if isinstance(explicit, list):
        candidates.extend(item for item in explicit if isinstance(item, dict))
    return normalize_citations(candidates, default_source_type="tracking_artifact")


def run_tracking_workflow(
    request: TrackingWorkflowRequest,
    *,
    timeout: int | float = DEFAULT_TIMEOUT_SECONDS,
) -> TrackingWorkflowResponse:
    script = _tracking_script()
    company = _resolve_company(request.company_query)
    if not script.is_file():
        result = {
            "ok": False,
            "stage": "script_missing",
            "company_query": request.company_query,
            "next_action": "未找到 data/wiki/tracking/scripts/run_all.py，请同步 tracking 生产脚本。",
        }
        return TrackingWorkflowResponse(True, format_tracking_workflow_reply(result), result)
    if company is None:
        result = {
            "ok": False,
            "stage": "company_resolve_failed",
            "company_query": request.company_query,
            "next_action": "请在当前页面选择公司，或在消息中提供唯一股票代码/company_id。",
        }
        return TrackingWorkflowResponse(True, format_tracking_workflow_reply(result), result)

    stock_code = company.get("stock_code") or company.get("company_id") or request.company_query
    company_name = company.get("company_short_name") or stock_code
    cmd = [
        sys.executable,
        str(script),
        "--stock",
        stock_code,
        "--company",
        company_name,
        "--wiki-base",
        str(WIKI_ROOT),
        "--json-summary",
    ]
    if request.skip_sentiment:
        cmd.append("--skip-sentiment")
    if not request.use_search:
        cmd.append("--no-search")
    if request.allow_simulated_sentiment:
        cmd.append("--allow-simulated-sentiment")
    if request.cleanup_html:
        cmd.append("--cleanup-html")
    if request.strict:
        cmd.append("--strict")
    if request.update_analysis:
        cmd.append("--update-analysis")

    env = os.environ.copy()
    env["SIQ_WIKI_ROOT"] = str(WIKI_ROOT)
    env["SIQ_WIKISET_ROOT"] = str(PROJECT_ROOT / "scripts" / "wiki" / "wikiset")
    try:
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
        return TrackingWorkflowResponse(True, format_tracking_workflow_reply(result), result)

    summary = _load_stdout_json(completed)
    company_dir = _company_dir(company)
    artifact_paths = _extract_artifact_paths(summary, company_dir)
    html_exists = bool(artifact_paths.get("html_path") and Path(artifact_paths["html_path"]).exists())
    status = str(summary.get("status") or "unknown")
    citations = _tracking_citations(summary, artifact_paths)
    citation_check = summary.get("citation_check") if isinstance(summary.get("citation_check"), dict) else {}
    modules = summary.get("modules") if isinstance(summary.get("modules"), dict) else {}
    latest_analysis = _latest_analysis_source(company_dir)
    source_report_path = str(summary.get("source_report_path") or latest_analysis or "")
    checks = {
        "command_succeeded": completed.returncode == 0,
        "pipeline_succeeded": status == "success",
        "html_present": html_exists,
        "modules_reported": bool(modules),
        "source_report_present": bool(source_report_path),
        "citation_validator_passed": citation_check.get("passed") is True,
        "citations_present": bool(citations),
    }
    failures = [name for name, passed in checks.items() if not passed]
    validation = SpecialistArtifactValidation(ok=not failures, checks=checks, failures=failures)
    ok = validation.ok
    html_path = str(artifact_paths.get("html_path") or "")
    artifact = finalize_specialist_artifact(
        artifact_type="tracking",
        company_id=company.get("company_id") or stock_code,
        source_report_path=source_report_path,
        output_path=html_path,
        html_url=_wiki_tracking_url(html_path) if ok else "",
        citations=citations,
        validation_result=validation,
        profile="siq_tracking",
        message=request.prompt or request.company_query,
        session_id=request.session_id,
        metadata={
            "modules": modules,
            "postgres_query_status": summary.get("postgres_query_status") or "not_run",
            "postgres_queries": summary.get("postgres_queries") or [],
        },
        specialist_facts={
            "tracking_facts": citations,
            "tracking_module_status": modules,
            "tracking_postgres_query_status": summary.get("postgres_query_status") or "not_run",
            "tracking_postgres_queries": summary.get("postgres_queries") or [],
            "tracking_postgres_facts": summary.get("postgres_facts") or [],
        },
    )
    artifact_manifest_path = Path(html_path).with_suffix(".artifact.json") if html_path else company_dir / "tracking" / "tracking.artifact.json"
    write_specialist_artifact_manifest(artifact, artifact_manifest_path)
    result = {
        **summary,
        **artifact_paths,
        "ok": ok,
        "stage": "completed" if ok else status or "failed",
        "company_query": request.company_query,
        "stock_code": stock_code,
        "company_name": company_name,
        "company_path": str(company_dir),
        "artifact": artifact.model_dump(),
        "artifact_manifest_path": str(artifact_manifest_path),
        "audit_trace_id": artifact.audit_trace_id,
        "validation_result": validation.model_dump(),
        "returncode": completed.returncode,
        "stdout": (completed.stdout or "").strip()[-4000:],
        "stderr": (completed.stderr or "").strip()[-4000:],
        "finished_at": datetime.now().isoformat(),
    }
    if not ok:
        result["draft_html_path"] = result.pop("html_path", "")
        result["next_action"] = "查看 validation_result 与 tracking workflow stdout/stderr，并确认模块状态、evidence 与 HTML/citation validator 可用。"
    return TrackingWorkflowResponse(True, format_tracking_workflow_reply(result), result)
