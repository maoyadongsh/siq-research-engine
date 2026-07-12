"""Deterministic annual analysis report workflow for the analysis agent.

This module keeps formal report generation out of free-form LLM chat. When the
analysis assistant receives an explicit request to generate a complete report,
the API runs the latest research-pack pipeline directly and returns the
artifact links as the assistant reply.
"""

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
from services.path_config import PROJECT_ROOT


DEFAULT_YEAR = 2025
DEFAULT_TIMEOUT_SECONDS = int(os.getenv("SIQ_ANALYSIS_REPORT_WORKFLOW_TIMEOUT_SECONDS", "3600"))


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


@dataclass(frozen=True)
class AnalysisReportWorkflowRequest:
    company_query: str
    year: int = DEFAULT_YEAR
    allow_overwrite: bool = False
    force: bool = True
    research_subagent_mode: str = "deterministic"
    prompt: str = ""


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
    company_query = _extract_company_query(message, context)
    if not company_query:
        return None
    return AnalysisReportWorkflowRequest(
        company_query=company_query,
        year=_extract_year(message, context),
        allow_overwrite=bool(OVERWRITE_RE.search(message or "")),
        force=True,
        research_subagent_mode="deterministic",
        prompt=(message or "").strip(),
    )


def _candidate_script_paths() -> tuple[Path, ...]:
    return (
        PROJECT_ROOT / "agents" / "hermes" / "profiles" / "siq_analysis" / "scripts" / "run_analysis_report.py",
        PROJECT_ROOT / "data" / "hermes" / "home" / "profiles" / "siq_analysis" / "scripts" / "run_analysis_report.py",
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


def format_analysis_report_workflow_reply(result: dict[str, Any]) -> str:
    ok = bool(result.get("ok"))
    stage = str(result.get("stage") or "unknown")
    company_query = str(result.get("company_query") or "")
    year = str(result.get("year") or "")
    files = result.get("files") if isinstance(result.get("files"), dict) else {}
    checkpoints = result.get("checkpoints") if isinstance(result.get("checkpoints"), dict) else {}
    html_path = files.get("html")
    html_url = _wiki_report_url(html_path)
    title = "已使用 research-pack 报告生成器完成正式分析报告" if ok else "research-pack 报告生成未完成"

    lines = [
        f"**{title}**",
        "",
        f"- 公司请求: `{company_query}`",
        f"- 年度: `{year}`",
        f"- 流水线阶段: `{stage}`",
        "- 固化能力: `run_analysis_report.py --use-research-packs --research-subagent-mode deterministic`",
        "- 图表模板: `html_renderer_v2 + financial_chart_design` 收支拆解/利润桥组件",
        f"- 质量验收: `{_validation_status(result)}`",
    ]
    if html_url:
        lines.append(f"- 打开报告: [HTML 报告]({html_url})")
    if html_path:
        lines.append(f"- HTML: `{_relative(html_path)}`")
    if files.get("md"):
        lines.append(f"- Markdown: `{_relative(files.get('md'))}`")
    if files.get("json"):
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
) -> AnalysisReportWorkflowResponse:
    script = latest_research_pack_report_script()
    if script is None:
        result = {
            "ok": False,
            "stage": "script_missing",
            "company_query": request.company_query,
            "year": request.year,
            "next_action": "未找到支持 --use-research-packs 的 run_analysis_report.py，请同步 siq_analysis profile scripts。",
        }
        return AnalysisReportWorkflowResponse(True, format_analysis_report_workflow_reply(result), result)

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
        return AnalysisReportWorkflowResponse(True, format_analysis_report_workflow_reply(result), result)

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
        return AnalysisReportWorkflowResponse(True, format_analysis_report_workflow_reply(result), result)

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
        return AnalysisReportWorkflowResponse(True, format_analysis_report_workflow_reply(payload), payload)

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
    reply = format_analysis_report_workflow_reply(payload)
    return AnalysisReportWorkflowResponse(True, reply, payload)
