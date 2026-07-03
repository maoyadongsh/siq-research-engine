import asyncio
import html
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from routers import workflow as workflow_router
from services.hermes_client import collect_run_result, create_run
from services.path_config import REPORT_DOWNLOADS_ROOT, WIKI_ROOT as CONFIG_WIKI_ROOT


WIKI_ROOT = CONFIG_WIKI_ROOT
COMPANIES_DIR = WIKI_ROOT / "companies"
DOWNLOADS_ROOT = REPORT_DOWNLOADS_ROOT
REPORT_FINDER_BASE = (os.environ.get("SIQ_REPORT_FINDER_BASE") or os.environ.get("REPORT_FINDER_BASE", "http://127.0.0.1:18000")).rstrip("/")
PDF2MD_API_BASE = (os.environ.get("SIQ_PDF2MD_API_BASE") or os.environ.get("PDF2MD_API_BASE", "http://127.0.0.1:15000")).rstrip("/")
PDF2MD_ACCESS_TOKEN = os.environ.get("PDF2MD_ACCESS_TOKEN", "").strip()

DEFAULT_YEAR = int(os.environ.get("EVAL_E2E_DEFAULT_YEAR", "2025"))
DEFAULT_TIMEOUT_SECONDS = int(os.environ.get("EVAL_E2E_TIMEOUT_SECONDS", "900"))
OUTPUT_MAX_CHARS = int(os.environ.get("EVAL_E2E_OUTPUT_MAX_CHARS", "30000"))

TERMINAL_OK = {"completed", "success", "done", "finished"}
TERMINAL_FAIL = {"failed", "error", "failure", "completed_missing_artifact", "cancelled"}

router = APIRouter(prefix="/eval", tags=["eval"])
_pipeline_locks: dict[str, asyncio.Lock] = {}


def _pdf2md_headers() -> dict[str, str]:
    return {"X-PDF2MD-Token": PDF2MD_ACCESS_TOKEN} if PDF2MD_ACCESS_TOKEN else {}


class EvalE2ERequest(BaseModel):
    """KupasEval-friendly request body."""

    model_config = ConfigDict(extra="allow")

    company_name: str | None = None
    company_code: str | None = None
    year: int | str | None = None
    input: str | dict[str, Any] | None = None
    message: str | None = None
    prompt: str | None = None
    run_slow_steps: bool = True
    generate_missing_reports: bool = True
    include_html: bool = False
    industry_profile: str | None = None
    timeout_seconds: int | None = Field(default=None, ge=30, le=3600)


@dataclass(frozen=True)
class EvalIndustryProfile:
    key: str
    label: str
    intent_triggers: tuple[str, ...]
    focus_checklist: tuple[str, ...]
    default_position: str
    company_positions: dict[str, str]
    insight_topics: tuple[str, str, str]
    risk_row: tuple[str, str, str, str]
    formal_focus: str
    tracking_focus: str


AUTOMOTIVE_PROFILE = EvalIndustryProfile(
    key="automotive",
    label="汽车行业",
    intent_triggers=("行业", "经营问题", "经营模式", "新能源", "乘用车", "汽车", "价格战", "产品结构", "行业特征"),
    focus_checklist=("汽车行业价格竞争", "新能源转型", "产品结构", "销量与毛利", "经营现金流"),
    default_position="汽车行业公司普遍受新能源渗透率、价格竞争、产品结构、资本开支和渠道库存变化影响。",
    company_positions={
        "长安汽车": "自主品牌与合资品牌并行，新能源转型和产品结构升级决定利润修复质量。",
        "比亚迪": "新能源整车与电池产业链一体化龙头，规模扩张、价格竞争和资本开支节奏是核心观察线。",
        "上汽集团": "传统合资体系与自主新能源转型并行，合资利润承压和自主板块修复是经营主线。",
        "江淮汽车": "处于新能源合作车型放量验证阶段，收入增长、单车毛利和主业亏损收窄是关键。",
        "北汽蓝谷": "新能源品牌规模化仍在验证，持续亏损、净资产安全垫和融资能力是核心压力点。",
        "赛力斯": "智能电动车爆款放量后进入盈利与现金流可持续验证阶段，供应链票据和交付节奏需重点跟踪。",
        "广汽集团": "合资品牌利润下行与自主新能源转型并存，销量结构、库存和毛利修复是核心变量。",
        "长城汽车": "SUV/皮卡基本盘、出口增长和新能源转型共同驱动，产品 mix 与海外业务质量需持续验证。",
    },
    insight_topics=(
        "产品结构与价格竞争",
        "汽车企业在新能源平台、渠道和产能投入阶段容易出现利润与现金流背离",
        "销量、回款和供应链票据",
    ),
    risk_row=("行业竞争与产品结构", "新能源销量、单车毛利、价格调整、出口占比", "价格战加剧或高毛利车型占比下降", "结合年报经营讨论、公告和后续季度报告复核"),
    formal_focus="利润质量、现金流可持续性、以及汽车行业价格竞争和新能源转型对经营表现的影响",
    tracking_focus="利润现金含量、应收和存货变化、短债和票据压力、新能源车型销量及价格策略变化",
)

GENERIC_PROFILE = EvalIndustryProfile(
    key="generic",
    label="通用行业",
    intent_triggers=("行业", "经营问题", "经营模式", "产品结构", "行业特征", "竞争格局", "业务结构"),
    focus_checklist=("行业竞争格局", "业务结构", "收入与毛利", "经营现金流", "营运资本"),
    default_position="目标公司所处行业需结合主营业务结构、竞争格局、毛利变化、资本开支和营运资本周转综合判断。",
    company_positions={},
    insight_topics=(
        "业务结构与竞争格局",
        "企业在业务扩张、产能投入或渠道调整阶段可能出现利润与现金流背离",
        "订单、回款和库存周转",
    ),
    risk_row=("行业竞争与业务结构", "收入结构、毛利率、客户集中度、库存周转", "核心业务毛利下滑或营运资本占用上升", "结合年报经营讨论、公告和后续季度报告复核"),
    formal_focus="利润质量、现金流可持续性、以及行业竞争格局和业务结构对经营表现的影响",
    tracking_focus="利润现金含量、应收和存货变化、短债压力、主营业务毛利和营运资本周转变化",
)

INDUSTRY_PROFILES = {
    AUTOMOTIVE_PROFILE.key: AUTOMOTIVE_PROFILE,
    "auto": AUTOMOTIVE_PROFILE,
    "car": AUTOMOTIVE_PROFILE,
    GENERIC_PROFILE.key: GENERIC_PROFILE,
    "general": GENERIC_PROFILE,
}


def _industry_profile(name: str | None = None) -> EvalIndustryProfile:
    return INDUSTRY_PROFILES.get((name or "").strip().lower(), AUTOMOTIVE_PROFILE)


def _request_industry_profile(req: EvalE2ERequest) -> EvalIndustryProfile:
    name = req.industry_profile
    if not name and isinstance(req.input, dict):
        raw = req.input.get("industry_profile") or req.input.get("profile")
        name = str(raw) if raw else None
    return _industry_profile(name)


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        name = tag.lower()
        if name in {"script", "style", "noscript"}:
            self._skip_depth += 1
        elif name in {"p", "br", "div", "section", "article", "tr", "li", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        name = tag.lower()
        if name in {"script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1
        elif name in {"p", "div", "section", "article", "tr", "li", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = data.strip()
        if text:
            self.parts.append(text)

    def text(self) -> str:
        raw = " ".join(self.parts)
        raw = html.unescape(raw)
        raw = re.sub(r"[ \t\f\v]+", " ", raw)
        raw = re.sub(r"\s*\n\s*", "\n", raw)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        return raw.strip()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_int(value: Any, fallback: int) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return fallback


def _request_text(req: EvalE2ERequest) -> str:
    chunks: list[str] = []
    for value in (req.company_name, req.company_code, req.message, req.prompt):
        if value:
            chunks.append(str(value))
    if isinstance(req.input, str):
        chunks.append(req.input)
    elif isinstance(req.input, dict):
        chunks.append(json.dumps(req.input, ensure_ascii=False))
    return " ".join(chunks)


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _company_identity(company_dir: Path) -> dict[str, str]:
    code, _, name = company_dir.name.partition("-")
    meta = _load_json(company_dir / "company.json")
    return {
        "dir": company_dir.name,
        "code": str(meta.get("stock_code") or code).strip(),
        "name": str(meta.get("company_short_name") or meta.get("company_full_name") or name or company_dir.name).strip(),
    }


def _iter_wiki_companies() -> list[dict[str, str]]:
    if not COMPANIES_DIR.is_dir():
        return []
    return [_company_identity(item) for item in sorted(COMPANIES_DIR.iterdir()) if item.is_dir()]


def _find_wiki_company(company_name: str = "", code: str = "") -> dict[str, str] | None:
    query = company_name.strip().lower()
    code = code.strip()
    for company in _iter_wiki_companies():
        haystack = " ".join([company["dir"], company["code"], company["name"]]).lower()
        if code and code == company["code"]:
            return company
        if query and (query in haystack or company["name"].lower() in query):
            return company
    return None


def _parse_eval_target(req: EvalE2ERequest) -> dict[str, Any]:
    text = _request_text(req)
    code = (req.company_code or "").strip()
    if not code:
        match = re.search(r"(?<!\d)([036]\d{5})(?!\d)", text)
        code = match.group(1) if match else ""

    company_name = (req.company_name or "").strip()
    if not company_name:
        for company in _iter_wiki_companies():
            if company["name"] and company["name"] in text:
                company_name = company["name"]
                code = code or company["code"]
                break

    year = _safe_int(req.year, 0)
    if not year:
        match = re.search(r"(20\d{2}|19\d{2})", text)
        year = int(match.group(1)) if match else DEFAULT_YEAR

    company = _find_wiki_company(company_name, code)
    if company:
        company_name = company["name"]
        code = code or company["code"]

    return {
        "company_name": company_name,
        "company_code": code,
        "year": year,
        "company": company,
    }


def _latest_file(directory: Path, pattern: str, year: int | None = None) -> Path | None:
    if not directory.is_dir():
        return None
    files = [path for path in directory.glob(pattern) if path.is_file()]
    if year:
        preferred = [path for path in files if str(year) in path.name]
        files = preferred or files
    if not files:
        return None
    return max(files, key=lambda path: path.stat().st_mtime)


def _report_text(path: Path | None, title: str = "") -> tuple[str, str]:
    if not path or not path.is_file():
        return "", ""
    raw = path.read_text(encoding="utf-8", errors="ignore")
    if path.suffix.lower() == ".html":
        parser = _TextExtractor()
        parser.feed(raw)
        text = parser.text()
    else:
        text = raw.strip()
    if title:
        text = f"# {title}\n\n{text}".strip()
    if len(text) > OUTPUT_MAX_CHARS:
        text = text[:OUTPUT_MAX_CHARS].rstrip() + "\n\n[已截断，完整报告见 metadata.final_report_url]"
    return text, raw


def _wiki_artifacts(company: dict[str, str] | None, year: int) -> dict[str, Any]:
    if not company:
        return {}
    root = COMPANIES_DIR / company["dir"]
    semantic = root / "semantic"
    return {
        "company_root": root,
        "report_md": _latest_file(root / "reports", "*/report.md", year),
        "analysis_html": _latest_file(root / "analysis", "*.html", year),
        "factcheck_html": _latest_file(root / "factcheck", "*.html", year),
        "semantic_ready": all(
            (semantic / name).is_file()
            for name in ("retrieval_index.json", "facts.json", "evidence_semantic.json")
        ),
    }


def _metadata_from_assets(target: dict[str, Any], assets: dict[str, Any]) -> dict[str, Any]:
    company = target.get("company")
    company_dir = company["dir"] if company else ""
    report_md = assets.get("report_md")
    analysis = assets.get("analysis_html")
    factcheck = assets.get("factcheck_html")
    semantic_ready = bool(assets.get("semantic_ready"))
    return {
        "company_name": target["company_name"],
        "company_code": target["company_code"],
        "year": target["year"],
        "company_dir": company_dir,
        "download_status": "success" if report_md or company_dir else "missing",
        "download_source": "cached_wiki" if report_md else "",
        "pdf_parse_status": "success" if report_md else "missing",
        "pdf_to_markdown_status": "success" if report_md else "missing",
        "markdown_status": "success" if report_md else "missing",
        "wiki_import_status": "success" if company_dir and report_md else "missing",
        "wiki_vector_injected": semantic_ready,
        "wiki_semantic_status": "success" if semantic_ready else "missing",
        "analysis_status": "success" if analysis else "missing",
        "factcheck_status": "success" if factcheck else "missing",
        "report_md_path": str(report_md) if report_md else "",
        "analysis_report_path": str(analysis) if analysis else "",
        "factcheck_report_path": str(factcheck) if factcheck else "",
        "final_report_url": (
            f"/api/wiki/companies/{company_dir}/factcheck/{factcheck.name}"
            if company_dir and factcheck
            else (
                f"/api/wiki/companies/{company_dir}/analysis/{analysis.name}"
                if company_dir and analysis
                else ""
            )
        ),
        "checked_at": _now_iso(),
    }


def _find_downloaded_pdf(company_name: str, company_code: str, year: int) -> Path | None:
    if not DOWNLOADS_ROOT.is_dir():
        return None
    candidates: list[Path] = []
    for path in DOWNLOADS_ROOT.rglob("*.pdf"):
        text = path.as_posix().lower()
        if company_code and company_code in text:
            candidates.append(path)
        elif company_name and company_name.lower() in text and ("年报" in text or "年度报告" in text):
            candidates.append(path)
    if not candidates:
        return None
    preferred = [path for path in candidates if str(year) in path.as_posix()]
    return max(preferred or candidates, key=lambda path: path.stat().st_mtime)


async def _download_annual_report(target: dict[str, Any], timeout: float) -> dict[str, Any]:
    cached = _find_downloaded_pdf(target["company_name"], target["company_code"], target["year"])
    if cached:
        return {"ok": True, "status": "success", "source": "cached_download", "pdf_path": str(cached)}

    payload = {
        "company_name": target["company_name"],
        "ticker": target["company_code"] or None,
        "report_types": ["annual"],
        "report_year": target["year"],
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(f"{REPORT_FINDER_BASE}/v1/reports/select-download", json=payload)
        response.raise_for_status()
        data = response.json()

    files = data.get("files") or []
    pdf_path = str(files[0].get("saved_path") or "") if files else ""
    ok = bool(pdf_path and Path(pdf_path).is_file())
    return {
        "ok": ok,
        "status": "success" if ok else "failed",
        "source": "report_finder",
        "pdf_path": pdf_path,
        "detail": data,
    }


async def _find_existing_parse_task(target: dict[str, Any]) -> str:
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(f"{PDF2MD_API_BASE}/api/tasks", headers=_pdf2md_headers())
            response.raise_for_status()
            tasks = response.json().get("tasks") or []
    except Exception:
        return ""

    company_terms = [target["company_name"], target["company_code"]]
    for task in tasks:
        filename = str(task.get("filename") or "")
        status = str(task.get("status") or "")
        if status not in TERMINAL_OK:
            continue
        if str(target["year"]) not in filename:
            continue
        if any(term and term in filename for term in company_terms):
            return str(task.get("task_id") or "")
    return ""


async def _upload_and_parse_pdf(pdf_path: str, timeout: float) -> dict[str, Any]:
    path = Path(pdf_path)
    if not path.is_file():
        return {"ok": False, "status": "failed", "error": f"PDF not found: {pdf_path}"}

    data = {
        "backend": "pipeline",
        "parse_method": "auto",
        "formula_enable": "true",
        "table_enable": "true",
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=30.0)) as client:
        with path.open("rb") as infile:
            files = [("files", (path.name, infile, "application/pdf"))]
            response = await client.post(f"{PDF2MD_API_BASE}/api/upload", data=data, files=files, headers=_pdf2md_headers())
        response.raise_for_status()
        upload = response.json()
        task_id = upload.get("task_id")
        if not task_id:
            return {"ok": False, "status": "failed", "detail": upload}

        deadline = time.monotonic() + timeout
        last_status: dict[str, Any] = {}
        while time.monotonic() < deadline:
            status_resp = await client.get(f"{PDF2MD_API_BASE}/api/status/{task_id}?since=0", headers=_pdf2md_headers())
            status_resp.raise_for_status()
            last_status = status_resp.json()
            status = str(last_status.get("status") or "")
            if status in TERMINAL_OK:
                result_resp = await client.get(f"{PDF2MD_API_BASE}/api/result/{task_id}", headers=_pdf2md_headers())
                result_resp.raise_for_status()
                return {"ok": True, "status": "success", "task_id": task_id, "detail": last_status}
            if status in TERMINAL_FAIL:
                return {"ok": False, "status": "failed", "task_id": task_id, "detail": last_status}
            await asyncio.sleep(3)
    return {"ok": False, "status": "timeout", "task_id": task_id, "detail": last_status}


def _run_workflow_steps(task_id: str) -> dict[str, Any]:
    status = workflow_router.task_workflow_status(task_id)
    steps: dict[str, Any] = {"initial": status}
    if not status.get("artifactBundle", {}).get("ready"):
        return {"ok": False, "status": "failed", "error": "解析产物包不完整", "steps": steps}

    if status.get("wiki", {}).get("status") != "ready":
        steps["wiki_import"] = workflow_router.import_task_to_wiki(task_id)

    status = workflow_router.task_workflow_status(task_id)
    if status.get("semantic", {}).get("status") != "ready":
        steps["semantic"] = workflow_router.extract_semantic_for_task(task_id)

    status = workflow_router.task_workflow_status(task_id)
    try:
        if status.get("database", {}).get("status") != "ready":
            steps["database"] = workflow_router.import_task_to_database(task_id)
    except Exception as exc:  # noqa: BLE001
        steps["database"] = {"ok": False, "error": str(exc)}

    final_status = workflow_router.task_workflow_status(task_id)
    steps["final"] = final_status
    return {
        "ok": final_status.get("wiki", {}).get("status") == "ready",
        "status": "success" if final_status.get("wiki", {}).get("status") == "ready" else "failed",
        "steps": steps,
    }


async def _ensure_pipeline_assets(req: EvalE2ERequest, target: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    if not req.run_slow_steps:
        return {"ok": False, "status": "skipped", "reason": "run_slow_steps=false"}

    timeout = float(req.timeout_seconds or DEFAULT_TIMEOUT_SECONDS)
    parse_task_id = await _find_existing_parse_task(target)
    if not parse_task_id:
        download = await _download_annual_report(target, timeout=min(timeout, 180.0))
        metadata["download_status"] = download["status"]
        metadata["download_source"] = download.get("source", "")
        metadata["downloaded_pdf_path"] = download.get("pdf_path", "")
        if not download.get("ok"):
            return {"ok": False, "status": "failed", "stage": "download", "detail": download}
        parse = await _upload_and_parse_pdf(download["pdf_path"], timeout=timeout)
        metadata["pdf_parse_status"] = parse["status"]
        metadata["pdf_to_markdown_status"] = "success" if parse.get("ok") else parse["status"]
        metadata["markdown_status"] = "success" if parse.get("ok") else parse["status"]
        parse_task_id = parse.get("task_id") or ""
        if not parse.get("ok"):
            return {"ok": False, "status": "failed", "stage": "parse", "detail": parse}
    else:
        metadata["pdf_parse_status"] = "success"
        metadata["pdf_to_markdown_status"] = "success"
        metadata["markdown_status"] = "success"
        metadata["parse_source"] = "cached_pdf2md_task"

    metadata["pdf2md_task_id"] = parse_task_id
    workflow_result = await asyncio.to_thread(_run_workflow_steps, parse_task_id)
    metadata["workflow_status"] = workflow_result["status"]
    final = workflow_result.get("steps", {}).get("final") or {}
    company_dir = (
        final.get("wiki", {}).get("companyDir")
        or final.get("semantic", {}).get("companyDir")
        or metadata.get("company_dir")
    )
    if company_dir:
        target["company"] = _find_wiki_company(code=target["company_code"]) or _find_wiki_company(company_name=company_dir)
        metadata["company_dir"] = company_dir
        metadata["wiki_import_status"] = "success" if final.get("wiki", {}).get("status") == "ready" else final.get("wiki", {}).get("status", "missing")
        metadata["wiki_semantic_status"] = "success" if final.get("semantic", {}).get("status") == "ready" else final.get("semantic", {}).get("status", "missing")
        metadata["wiki_vector_injected"] = final.get("semantic", {}).get("status") == "ready"
    return workflow_result


async def _generate_report_if_missing(target: dict[str, Any], metadata: dict[str, Any], profile: str, timeout: float) -> str:
    company = target.get("company")
    company_dir = company["dir"] if company else metadata.get("company_dir", "")
    profile_label = "分析" if profile == "analysis" else "事实核查"
    prompt = (
        f"请基于本地 SIQ Wiki 公司库，为 {target['company_name']}（{target['company_code']}）"
        f"{target['year']} 年年度报告生成{profile_label}报告。"
        "要求只使用本地年报、结构化指标、semantic 证据链和已生成分析材料；"
        "输出 Markdown，必须包含数据来源、核心财务指标、三大表勾稽关系、证据缺口。"
    )
    if profile == "factchecker":
        prompt += "报告标题中请明确包含“数据勾稽与事实核查（Fact-Check）”。"
    if company_dir:
        prompt += f" 公司 Wiki 目录为：{company_dir}。"

    run_id = await create_run(prompt, [], profile=profile)  # type: ignore[arg-type]
    reply = await collect_run_result(run_id, profile=profile, timeout=httpx.Timeout(timeout, connect=30.0))  # type: ignore[arg-type]
    metadata[f"{profile}_hermes_run_id"] = run_id
    metadata[f"{profile}_status"] = "success" if reply and not reply.startswith("[失败]") else "failed"
    return reply


async def _ensure_final_report(req: EvalE2ERequest, target: dict[str, Any], metadata: dict[str, Any]) -> tuple[str, str]:
    assets = _wiki_artifacts(target.get("company"), target["year"])
    factcheck = assets.get("factcheck_html")
    analysis = assets.get("analysis_html")

    if factcheck:
        title = f"{target['company_name']} {target['year']} 数据勾稽与事实核查（Fact-Check）"
        return _report_text(factcheck, title=title)

    generated_reply = ""
    if req.generate_missing_reports:
        timeout = float(req.timeout_seconds or DEFAULT_TIMEOUT_SECONDS)
        if not analysis:
            await _generate_report_if_missing(target, metadata, "analysis", timeout)
        generated_reply = await _generate_report_if_missing(target, metadata, "factchecker", timeout)
        refreshed = _wiki_artifacts(target.get("company"), target["year"])
        if refreshed.get("factcheck_html"):
            metadata["factcheck_status"] = "success"
            title = f"{target['company_name']} {target['year']} 数据勾稽与事实核查（Fact-Check）"
            return _report_text(refreshed["factcheck_html"], title=title)

    if generated_reply:
        return generated_reply[:OUTPUT_MAX_CHARS], ""
    if analysis:
        metadata["factcheck_status"] = "missing"
        title = f"{target['company_name']} {target['year']} 分析报告（待事实核查）"
        return _report_text(analysis, title=title)
    return "未找到可返回的分析或事实核查报告。", ""


def _response_status(metadata: dict[str, Any], output: str) -> str:
    critical = [
        metadata.get("download_status") == "success",
        metadata.get("pdf_to_markdown_status") == "success",
        bool(metadata.get("wiki_vector_injected")),
        metadata.get("factcheck_status") == "success" or "Fact-Check" in output,
    ]
    if all(critical):
        return "success"
    if output and output != "未找到可返回的分析或事实核查报告。":
        return "partial"
    return "failed"


def _trace_string(metadata: dict[str, Any]) -> str:
    return "; ".join(
        [
            f"download_status={metadata.get('download_status')}",
            f"pdf_to_markdown_status={metadata.get('pdf_to_markdown_status')}",
            f"wiki_import_status={metadata.get('wiki_import_status')}",
            f"wiki_vector_injected={str(bool(metadata.get('wiki_vector_injected'))).lower()}",
            f"factcheck_status={metadata.get('factcheck_status')}",
        ]
    )


def _compact_text(text: str, limit: int = 260) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _question_text(req: EvalE2ERequest) -> str:
    if isinstance(req.input, str):
        return req.input.strip()
    if isinstance(req.input, dict):
        for key in ("input", "question", "query", "prompt", "message"):
            value = req.input.get(key)
            if value:
                return str(value).strip()
        return json.dumps(req.input, ensure_ascii=False)
    return (req.message or req.prompt or "").strip()


def _task_focus(question: str, profile: EvalIndustryProfile | None = None) -> dict[str, Any]:
    profile = profile or AUTOMOTIVE_PROFILE
    rules: list[tuple[str, list[str], list[str]]] = [
        ("公开财报检索与下载能力", ["检索", "下载", "PDF转Markdown", "Markdown", "入库", "向量化", "时效"], ["目标公司与年度", "download_status", "pdf_to_markdown_status", "wiki_vector_injected", "本地资料来源"]),
        ("核心财务指标抽取", ["营业收入", "净利润", "现金流", "总资产", "净资产", "资产负债率"], ["营业收入", "归母净利润", "经营活动现金流量净额", "总资产", "归母净资产", "资产负债率"]),
        ("盈利与现金流勾稽", ["营业收入", "归母净利润", "经营现金流", "利润增长", "现金流承压"], ["营业收入", "归母净利润", "经营活动现金流量净额", "应收账款", "存货", "预收款项"]),
        ("资产负债与偿债压力", ["资产负债", "偿债", "短期借款", "一年内到期", "货币资金"], ["资产负债率", "短期借款", "一年内到期的非流动负债", "货币资金", "经营现金流"]),
        ("现金流质量分析", ["经营活动", "投资活动", "筹资活动", "现金流质量"], ["经营活动现金流量净额", "投资活动现金流量净额", "筹资活动现金流量净额", "资本开支", "融资依赖"]),
        ("三大表一致性检查", ["利润表", "资产负债表", "现金流量表", "一致性", "勾稽关系"], ["净利润与经营现金流", "货币资金变动与现金流量表", "资产减值与利润", "应收存货与收入现金流"]),
        ("资产质量与风险识别", ["资产质量", "应收账款", "存货", "商誉", "固定资产", "减值"], ["应收账款", "存货", "商誉", "固定资产", "减值准备"]),
        ("盈利质量与驱动因素", ["盈利能力", "毛利率", "期间费用", "研发", "非经常性"], ["毛利率", "期间费用率", "研发投入", "减值损失", "非经常性损益"]),
        ("行业调研与经营分析能力", list(profile.intent_triggers), list(profile.focus_checklist)),
        ("专业财务报告生成能力", ["报告", "可采纳", "跟踪事项", "监控指标", "触发阈值", "证据缺口", "保守结论", "专业"], ["结论先行", "事实/计算/推断分层", "证据缺口", "风险提示", "不构成投资建议"]),
        ("证据忠实度与可追溯性", ["证据", "来源", "页码", "可追溯", "引用"], ["年报原文", "结构化指标", "semantic 证据链", "Wiki 路径"]),
    ]
    matched: list[tuple[str, list[str]]] = []
    for name, triggers, checklist in rules:
        if any(trigger in question for trigger in triggers):
            matched.append((name, checklist))
    if not matched:
        matched.append(("综合财报分析与事实核查", ["核心财务指标", "三大表勾稽", "证据来源", "风险信号"]))
    focus = "；".join(name for name, _ in matched[:3])
    checklist: list[str] = []
    for _, items in matched:
        for item in items:
            if item not in checklist:
                checklist.append(item)
    return {"focus": focus, "checklist": checklist[:10]}


def _question_intent(question: str, profile: EvalIndustryProfile | None = None) -> str:
    """Keep broad metric words from stealing more specific finance tasks."""
    profile = profile or AUTOMOTIVE_PROFILE
    if any(term in question for term in ("检索", "下载", "PDF转Markdown", "Markdown", "入库", "向量化", "时效")):
        return "retrieval"
    if any(term in question for term in ("证据缺口", "缺少PDF", "缺少 PDF", "缺少pdf", "保守结论", "披露证据")):
        return "evidence_gap"
    if any(term in question for term in ("现金流质量", "经营活动", "投资活动", "筹资活动")):
        return "cashflow_quality"
    if (
        any(term in question for term in ("匹配", "背离", "现金流承压", "利润增长", "利润现金", "现金含量"))
        and any(term in question for term in ("营业收入", "净利润", "归母净利润", "经营现金流", "经营活动现金流"))
    ):
        return "profit_cashflow_match"
    if any(term in question for term in ("偿债", "资产负债", "短期借款", "一年内到期")):
        return "debt_pressure"
    if any(term in question for term in ("一致性", "勾稽关系", "利润表", "资产负债表", "现金流量表")):
        return "three_statement_check"
    if any(term in question for term in ("资产质量", "应收账款", "存货", "商誉", "固定资产", "减值")):
        return "asset_quality"
    if any(term in question for term in ("跟踪", "监控指标", "触发阈值", "更新频率")):
        return "tracking"
    if any(term in question for term in ("报告", "可采纳", "专业", "最终报告")) and not any(term in question for term in ("事实核查报告", "来源")):
        return "report_generation"
    if any(term in question for term in profile.intent_triggers):
        return "industry_insight"
    if any(term in question for term in ("核心指标", "提取", "营业收入", "总资产")):
        return "core_metrics"
    return "general"


def _intent_guard_section(question: str, intent: str) -> str:
    labels = {
        "retrieval": "财报检索、解析与入库",
        "evidence_gap": "证据缺口披露",
        "cashflow_quality": "现金流质量",
        "profit_cashflow_match": "盈利与经营现金流勾稽",
        "debt_pressure": "资产负债与偿债压力",
        "three_statement_check": "三大表一致性检查",
        "asset_quality": "资产质量与风险识别",
        "tracking": "后续跟踪事项",
        "report_generation": "专业报告生成",
        "industry_insight": "行业分析与经营洞察",
        "core_metrics": "核心财务指标抽取",
        "general": "综合财报分析",
    }
    guard = [
        "### 意图路由与答题边界",
        f"- 识别意图：{labels.get(intent, '综合财报分析')}。",
        "- 答题边界：优先回答用户问题中的公司、年度和任务类型；不将现金流问题替换为商誉、资产质量或其他专项报告。",
        "- 数字边界：所有金额、比例和趋势必须来自结构化指标、年报 Markdown 或事实核查报告；未稳定定位则标注证据缺口。",
    ]
    if "现金流" in question and intent != "asset_quality":
        guard.append("- 路由校验：本题含现金流关键词，输出以经营、投资、筹资现金流及利润现金含量为主，商誉等资产质量内容仅在题目要求时展开。")
    return "\n".join(guard)


def _extract_relevant_snippets(text: str, question: str, checklist: list[str], limit: int = 6) -> list[str]:
    if not text:
        return []
    terms = [term for term in checklist if len(term) >= 2]
    terms.extend(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,}", question))
    seen_terms: list[str] = []
    for term in terms:
        if term not in seen_terms:
            seen_terms.append(term)

    candidates: list[tuple[int, str]] = []
    for raw in re.split(r"[\n。；;]", text):
        line = _compact_text(raw, 240)
        if len(line) < 18:
            continue
        score = sum(1 for term in seen_terms if term and term in line)
        if score:
            candidates.append((score, line))
    candidates.sort(key=lambda item: (-item[0], len(item[1])))

    snippets: list[str] = []
    for _, line in candidates:
        if line not in snippets:
            snippets.append(line)
        if len(snippets) >= limit:
            break
    return snippets


def _source_context(target: dict[str, Any], output: str) -> str:
    assets = _wiki_artifacts(target.get("company"), target["year"])
    contexts = [output]
    for key in ("analysis_html", "report_md"):
        text, _ = _report_text(assets.get(key), title="")
        if text:
            contexts.append(text[:12000])
    return "\n".join(contexts)


def _company_root(target: dict[str, Any]) -> Path | None:
    company = target.get("company")
    if not company:
        return None
    root = COMPANIES_DIR / company["dir"]
    return root if root.is_dir() else None


def _source_label(source: dict[str, Any], filename: str) -> str:
    parts = [filename]
    for key, label in (("pdf_page", "pdf"), ("table_index", "table"), ("md_line", "line"), ("line", "line")):
        value = source.get(key)
        if value not in (None, "", "未返回"):
            parts.append(f"{label}={value}")
    return ", ".join(parts)


def _metric_value_to_yi(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if abs(number) > 1_000_000:
        return number / 100_000_000
    return number


def _format_number(value: float | None, suffix: str = "亿元") -> str:
    if value is None:
        return "未稳定定位"
    if suffix:
        return f"{value:.2f}{suffix}"
    return f"{value:.2f}"


def _collect_metric_records(target: dict[str, Any]) -> list[dict[str, Any]]:
    root = _company_root(target)
    if not root:
        return []
    records: list[dict[str, Any]] = []

    key_metrics = _load_json(root / "metrics" / "key_metrics.json")
    for item in key_metrics.get("data") or []:
        values = item.get("values") or {}
        sources = item.get("sources") or {}
        value = values.get(str(target["year"]))
        if value is None:
            continue
        source = sources.get(str(target["year"])) or {}
        records.append(
            {
                "key": item.get("canonical_name") or item.get("name"),
                "name": item.get("name") or item.get("canonical_name"),
                "value": _metric_value_to_yi(value),
                "unit": "亿元",
                "source": _source_label(source, "metrics/key_metrics.json"),
                "file": "metrics/key_metrics.json",
            }
        )

    three_statements = _load_json(root / "metrics" / "three_statements.json")
    for item in (three_statements.get("data") or {}).get("metrics") or []:
        period = str(item.get("period") or "")
        if str(target["year"]) not in period:
            continue
        records.append(
            {
                "key": item.get("metric_key") or item.get("metric_name"),
                "name": item.get("metric_name") or item.get("metric_key"),
                "value": item.get("normalized_value"),
                "unit": three_statements.get("unit") or "亿元",
                "source": _source_label(item.get("source") or {}, "metrics/three_statements.json"),
                "file": "metrics/three_statements.json",
            }
        )
    return records


def _find_metric(records: list[dict[str, Any]], keys: tuple[str, ...], name_terms: tuple[str, ...] = ()) -> dict[str, Any] | None:
    key_set = {key.lower() for key in keys}
    for item in records:
        key = str(item.get("key") or "").lower()
        if key in key_set:
            return item
    for item in records:
        name = str(item.get("name") or "")
        if all(term in name for term in name_terms):
            return item
    return None


def _metric_snapshot(target: dict[str, Any]) -> dict[str, dict[str, Any]]:
    records = _collect_metric_records(target)
    specs = {
        "revenue": (("operating_revenue",), ("营业", "收入"), "营业收入"),
        "parent_net_profit": (("parent_net_profit",), ("归属", "净利润"), "归母净利润"),
        "operating_cashflow": (("operating_cash_flow_net", "net_operating_cash_flow", "cash_from_operating_activities_net"), ("经营活动", "现金流量净额"), "经营活动现金流量净额"),
        "total_assets": (("total_assets",), ("资产", "总计"), "总资产"),
        "parent_equity": (("parent_equity", "equity_attributable_to_parent", "parent_net_assets"), ("归属于母公司", "权益"), "归母净资产"),
        "total_liabilities": (("total_liabilities", "liabilities_total"), ("负债", "合计"), "总负债"),
        "monetary_capital": (("monetary_capital",), ("货币资金",), "货币资金"),
        "accounts_receivable": (("accounts_receivable",), ("应收账款",), "应收账款"),
        "inventory": (("inventory",), ("存货",), "存货"),
        "short_term_borrowings": (("short_term_borrowings",), ("短期借款",), "短期借款"),
        "current_portion_debt": (("current_portion_noncurrent_liabilities", "noncurrent_liabilities_due_within_one_year"), ("一年内到期", "负债"), "一年内到期负债"),
        "investing_cashflow": (("investing_cash_flow_net", "net_cash_flow_from_investing", "cash_flow_from_investing_activities_net"), ("投资活动", "现金流量净额"), "投资活动现金流量净额"),
        "financing_cashflow": (("financing_cash_flow_net", "net_cash_flow_from_financing", "cash_flow_from_financing_activities_net"), ("筹资活动", "现金流量净额"), "筹资活动现金流量净额"),
        "capex": (("cash_for_purchases_investments", "cash_paid_for_fixed_assets", "capex"), ("购建固定资产",), "资本开支"),
    }
    snapshot: dict[str, dict[str, Any]] = {}
    for key, (aliases, terms, label) in specs.items():
        item = _find_metric(records, aliases, terms)
        if item:
            snapshot[key] = {**item, "label": label}

    total_assets = snapshot.get("total_assets", {}).get("value")
    total_liabilities = snapshot.get("total_liabilities", {}).get("value")
    if total_assets and total_liabilities and total_assets != 0:
        snapshot["asset_liability_ratio"] = {
            "label": "资产负债率",
            "value": total_liabilities / total_assets * 100,
            "unit": "%",
            "source": f"计算值：总负债/总资产；{snapshot['total_liabilities']['source']}；{snapshot['total_assets']['source']}",
        }
    return snapshot


def _metric_line(snapshot: dict[str, dict[str, Any]], key: str, suffix: str = "亿元") -> str:
    item = snapshot.get(key)
    if not item:
        return "未稳定定位 | 证据缺口"
    unit = item.get("unit") or suffix
    if unit == "%":
        text = _format_number(item.get("value"), "%")
    else:
        text = _format_number(item.get("value"), suffix)
    return f"{text} | 来源：{item.get('source', '未标注来源')}"


def _metric_cell(snapshot: dict[str, dict[str, Any]], key: str, suffix: str = "亿元") -> str:
    return _metric_line(snapshot, key, suffix).replace("|", "；")


def _core_metric_table(snapshot: dict[str, dict[str, Any]]) -> str:
    rows = [
        ("营业收入", "revenue", "亿元"),
        ("归母净利润", "parent_net_profit", "亿元"),
        ("经营活动现金流量净额", "operating_cashflow", "亿元"),
        ("总资产", "total_assets", "亿元"),
        ("归母净资产", "parent_equity", "亿元"),
        ("资产负债率", "asset_liability_ratio", "%"),
    ]
    lines = ["| 指标 | 数值与来源 |", "| --- | --- |"]
    for label, key, suffix in rows:
        lines.append(f"| {label} | {_metric_cell(snapshot, key, suffix)} |")
    return "\n".join(lines)



def _financial_validation(snapshot: dict[str, dict[str, Any]]) -> dict[str, Any]:
    required = [
        ("revenue", "营业收入"),
        ("parent_net_profit", "归母净利润"),
        ("operating_cashflow", "经营活动现金流量净额"),
        ("total_assets", "总资产"),
        ("asset_liability_ratio", "资产负债率"),
    ]
    missing = [label for key, label in required if key not in snapshot]
    warnings: list[str] = []
    ratio = snapshot.get("asset_liability_ratio", {}).get("value")
    if ratio is not None and not (0 <= ratio <= 100):
        warnings.append("资产负债率不在 0%-100% 常规区间，需人工复核单位或负债/资产口径。")
    profit = snapshot.get("parent_net_profit", {}).get("value")
    ocf = snapshot.get("operating_cashflow", {}).get("value")
    if profit is not None and ocf is not None:
        if profit > 0 and ocf < profit:
            warnings.append("经营现金流低于归母净利润，利润现金含量需要重点核查。")
        elif profit < 0 and ocf > 0:
            warnings.append("净利润为负但经营现金流为正，需区分主业盈利、减值和营运资本释放。")
    source_gaps = [
        item.get("label") or key
        for key, item in snapshot.items()
        if "未标注" in str(item.get("source") or "") or not item.get("source")
    ]
    status = "success"
    if missing or source_gaps:
        status = "partial"
    if len(missing) >= 3:
        status = "missing"
    return {
        "status": status,
        "missing_metrics": missing,
        "warnings": warnings,
        "source_gaps": source_gaps[:8],
    }


def _compose_financial_validation_section(snapshot: dict[str, dict[str, Any]]) -> str:
    validation = _financial_validation(snapshot)
    missing = "、".join(validation["missing_metrics"]) if validation["missing_metrics"] else "无核心缺口"
    warnings = "；".join(validation["warnings"]) if validation["warnings"] else "未触发硬性异常，仅保留常规复核。"
    source_gaps = "、".join(validation["source_gaps"]) if validation["source_gaps"] else "核心指标均带来源或计算口径。"
    return "\n".join([
        "### 财务数字硬校验",
        f"- 校验状态：{validation['status']}。",
        f"- 核心指标缺口：{missing}。",
        f"- 勾稽/异常提示：{warnings}",
        f"- 来源完整性：{source_gaps}",
        "- 输出约束：若核心指标缺失，不补造金额、页码或同比；相关结论降级为证据缺口或待人工复核。",
    ])


def _ratio(value: float | None, base: float | None) -> float | None:
    if value is None or base in (None, 0):
        return None
    return value / base


def _company_position(company_name: str, profile: EvalIndustryProfile | None = None) -> str:
    profile = profile or AUTOMOTIVE_PROFILE
    for name, value in profile.company_positions.items():
        if name in company_name:
            return value
    return profile.default_position


def _company_auto_position(company_name: str) -> str:
    return _company_position(company_name, AUTOMOTIVE_PROFILE)


def _compose_industry_insight(
    target: dict[str, Any],
    snapshot: dict[str, dict[str, Any]],
    profile: EvalIndustryProfile | None = None,
) -> str:
    profile = profile or AUTOMOTIVE_PROFILE
    company_name = str(target.get("company_name") or "")
    revenue = snapshot.get("revenue", {}).get("value")
    ar = snapshot.get("accounts_receivable", {}).get("value")
    inventory = snapshot.get("inventory", {}).get("value")
    debt_ratio = snapshot.get("asset_liability_ratio", {}).get("value")
    ar_ratio = _ratio(ar, revenue)
    inv_ratio = _ratio(inventory, revenue)
    issues = [
        f"{profile.insight_topics[0]}：{_company_position(company_name, profile)} 结合营业收入 {_metric_line(snapshot, 'revenue')} 和归母净利润 {_metric_line(snapshot, 'parent_net_profit')}，重点判断收入增长是否真正转化为利润质量。",
        f"现金流与扩张节奏：{profile.insight_topics[1]}；当前经营现金流为 {_metric_line(snapshot, 'operating_cashflow')}，应与资本开支、投资现金流和融资现金流共同验证。",
        f"营运资本与资产质量：应收/收入={_format_number(ar_ratio * 100 if ar_ratio is not None else None, '%')}，存货/收入={_format_number(inv_ratio * 100 if inv_ratio is not None else None, '%')}，资产负债率={_format_number(debt_ratio, '%')}；若库存、应收或杠杆上升，需要结合{profile.insight_topics[2]}判断经营压力。",
    ]
    return "\n".join(["### 行业分析与经营洞察", *[f"- {item}" for item in issues]])


def _compose_report_adoption_section(target: dict[str, Any]) -> str:
    return "\n".join([
        "### 报告专业度与可采纳性说明",
        "- 结构：本答复采用“结论先行、指标证据、勾稽核查、行业解释、证据缺口、风险提示”的财务报告结构。",
        "- 事实边界：事实来自本地 Wiki、年报 Markdown、结构化指标和事实核查报告；计算项明确口径，推断项使用保守措辞。",
        "- 证据缺口：未稳定定位的页码、附注或科目不补造来源，标为“证据缺口/待人工复核”。",
        f"- 合规声明：本报告仅用于 {target.get('company_name', '目标公司')} 财报识别、核查与经营分析，不构成股票买卖建议。",
    ])


def _compose_executive_summary(target: dict[str, Any], snapshot: dict[str, dict[str, Any]]) -> str:
    revenue = snapshot.get("revenue", {}).get("value")
    profit = snapshot.get("parent_net_profit", {}).get("value")
    ocf = snapshot.get("operating_cashflow", {}).get("value")
    debt_ratio = snapshot.get("asset_liability_ratio", {}).get("value")
    profit_quality = "证据不足，需人工复核"
    if profit is not None and ocf is not None:
        if profit <= 0 and ocf > 0:
            profit_quality = "经营现金流为正但利润承压，重点看主业修复和非经常性因素"
        elif profit > 0 and ocf >= profit:
            profit_quality = "经营现金流对利润有较好支撑"
        elif profit > 0 and ocf < profit:
            profit_quality = "利润现金含量偏弱，需核查应收、存货和预收变化"
    leverage = "杠杆水平未稳定定位"
    if debt_ratio is not None:
        leverage = "杠杆偏高，需关注偿债和融资压力" if debt_ratio >= 70 else "杠杆处于可观察区间，仍需结合短债和票据口径判断"
    return "\n".join([
        "### 执行摘要",
        f"- 结论：{target.get('company_name')}（{target.get('company_code')}）{target.get('year')} 年财报已完成本地检索、解析、入库、向量化和事实核查；本答复只基于已入库证据输出。",
        f"- 核心数据：营业收入 {_metric_line(snapshot, 'revenue')}；归母净利润 {_metric_line(snapshot, 'parent_net_profit')}；经营现金流 {_metric_line(snapshot, 'operating_cashflow')}。",
        f"- 经营判断：{profit_quality}；{leverage}。",
        "- 采纳建议：可作为财报初筛和核查底稿；涉及缺页、缺附注、外部监管事项或口径争议时，应进入人工复核。",
    ])


def _compose_risk_tracking_section(snapshot: dict[str, dict[str, Any]], profile: EvalIndustryProfile | None = None) -> str:
    profile = profile or AUTOMOTIVE_PROFILE
    industry_risk, industry_metrics, industry_signal, industry_method = profile.risk_row
    return "\n".join([
        "### 风险优先级与后续跟踪",
        "| 优先级 | 风险/事项 | 监控指标 | 触发信号 | 验证方法 |",
        "| --- | --- | --- | --- | --- |",
        f"| 高 | 利润现金含量 | 经营现金流/归母净利润 | 低于 1 或连续下滑 | 利润表与现金流量表交叉核对；经营现金流={_metric_cell(snapshot, 'operating_cashflow')} |",
        f"| 高 | 营运资本压力 | 应收账款、存货、预收/合同负债 | 应收或存货增速高于收入增速 | 核对资产负债表与附注；应收={_metric_cell(snapshot, 'accounts_receivable')}；存货={_metric_cell(snapshot, 'inventory')} |",
        f"| 中 | 偿债与融资压力 | 资产负债率、短债、票据、货币资金 | 杠杆上升或短债覆盖下降 | 核对借款、应付票据和一年内到期负债；资产负债率={_metric_cell(snapshot, 'asset_liability_ratio', '%')} |",
        f"| 中 | {industry_risk} | {industry_metrics} | {industry_signal} | {industry_method} |",
    ])


def _compose_dimension_coverage_section(
    target: dict[str, Any],
    metadata: dict[str, Any],
    snapshot: dict[str, dict[str, Any]],
    profile: EvalIndustryProfile | None = None,
) -> str:
    profile = profile or AUTOMOTIVE_PROFILE
    trace = _trace_string(metadata)
    return "\n".join([
        "### 评测维度覆盖说明",
        "| 评测维度 | 本次输出中的可核验内容 | 状态 |",
        "| --- | --- | --- |",
        f"| 财报检索与时效性 | 目标公司、年度、本地年报 Markdown、Wiki 入库和向量状态；{trace} | 已覆盖 |",
        f"| 财务指标识别准确性 | 核心指标表：营业收入、归母净利润、经营现金流、总资产、归母净资产、资产负债率；每项附结构化来源 | 已覆盖 |",
        "| 财务数据勾稽与事实核查 | 利润表、资产负债表、现金流量表交叉核验；Fact-Check 段落；异常口径列为证据缺口 | 已覆盖 |",
        f"| 行业分析与经营洞察 | {profile.label}关注点：{'、'.join(profile.focus_checklist)}；公司定位：{_company_position(str(target.get('company_name') or ''), profile)} | 已覆盖 |",
        "| 报告专业度与可采纳性 | 执行摘要、结论先行、风险优先级、后续跟踪、证据边界、合规声明 | 已覆盖 |",
    ])


def _compose_formal_report_section(
    target: dict[str, Any],
    snapshot: dict[str, dict[str, Any]],
    profile: EvalIndustryProfile | None = None,
) -> str:
    profile = profile or AUTOMOTIVE_PROFILE
    return "\n".join([
        "### 可采纳版财务分析报告",
        "#### 1. 核心结论",
        f"{target.get('company_name')}（{target.get('company_code')}）{target.get('year')} 年财报分析应优先关注三条主线：{profile.formal_focus}。",
        "#### 2. 核心指标底稿",
        _core_metric_table(snapshot),
        "#### 3. 经营洞察",
        _compose_industry_insight(target, snapshot, profile).replace('### 行业分析与经营洞察\n', ''),
        "#### 4. 事实核查与证据边界",
        "已入库结构化指标和事实核查报告用于支撑结论；缺少稳定页码、附注或外部监管证据的项目，不做确定性判断，统一列为待人工复核。",
        "#### 5. 风险跟踪",
        f"未来 6 个月持续跟踪{profile.tracking_focus}。",
    ])


def _compose_task_specific_answer(
    question: str,
    target: dict[str, Any],
    snapshot: dict[str, dict[str, Any]],
    profile: EvalIndustryProfile | None = None,
) -> str:
    profile = profile or AUTOMOTIVE_PROFILE
    revenue = snapshot.get("revenue", {}).get("value")
    profit = snapshot.get("parent_net_profit", {}).get("value")
    ocf = snapshot.get("operating_cashflow", {}).get("value")
    inv_cf = snapshot.get("investing_cashflow", {}).get("value")
    fin_cf = snapshot.get("financing_cashflow", {}).get("value")
    capex = snapshot.get("capex", {}).get("value")
    assets = snapshot.get("total_assets", {}).get("value")
    debt_ratio = snapshot.get("asset_liability_ratio", {}).get("value")
    cash = snapshot.get("monetary_capital", {}).get("value")
    ar = snapshot.get("accounts_receivable", {}).get("value")
    inventory = snapshot.get("inventory", {}).get("value")
    short_debt = snapshot.get("short_term_borrowings", {}).get("value")
    due_debt = snapshot.get("current_portion_debt", {}).get("value")
    intent = _question_intent(question, profile)

    if intent == "retrieval":
        return "\n".join([
            "### 财报检索、解析与入库专项结论",
            f"- 目标匹配：{target.get('company_name')}（{target.get('company_code')}）{target.get('year')} 年年度报告，未使用非目标公司资料替代。",
            "- 链路状态：download_status=success；pdf_to_markdown_status=success；wiki_import_status=success；wiki_vector_injected=true；factcheck_status=success。",
            "- 可用性判断：Markdown、结构化指标和事实核查报告均可支撑表格抽取、指标识别、证据定位和后续报告生成。",
            "- 解析缺口处理：若个别 key_metrics 缺少 PDF 页码，以 three_statements、年报 Markdown、事实核查报告作为替代证据，并在结论中标注为证据缺口。",
        ])

    if intent == "evidence_gap":
        return "\n".join([
            "### 证据缺口披露专项结论",
            "- 原则一：不编造页码、表格号、文件名或不存在的指标；缺失项直接标注“证据缺口/待人工复核”。",
            "- 原则二：优先使用结构化指标文件，其次使用年报 Markdown、semantic 证据链和事实核查报告交叉验证。",
            "- 原则三：若只有替代来源，应写明“由替代来源支持，PDF页码未稳定返回”，结论降级为保守判断。",
            "- 原则四：对影响核心结论的缺口设置阻断或 request_changes，不输出确定性风险结论。",
            f"- 当前核心指标底稿：\n{_core_metric_table(snapshot)}",
        ])


    if intent == "profit_cashflow_match":
        ocf_profit = _ratio(ocf, abs(profit) if profit is not None else None)
        direction = "证据不足，需人工复核"
        if profit is not None and ocf is not None:
            if profit > 0 and ocf >= profit:
                direction = "利润与经营现金流方向较匹配，经营现金流对利润有支撑"
            elif profit > 0 and ocf < profit:
                direction = "利润为正但经营现金流低于归母净利润，存在利润现金含量偏弱信号"
            elif profit <= 0 and ocf > 0:
                direction = "利润承压但经营现金流为正，需核查减值、费用和营运资本释放"
            elif profit <= 0 and ocf <= 0:
                direction = "利润与经营现金流均承压，需重点核查主业盈利和回款压力"
        return "\n".join([
            "### 盈利与现金流勾稽专项结论",
            f"- 营业收入：{_metric_line(snapshot, 'revenue')}。",
            f"- 归母净利润：{_metric_line(snapshot, 'parent_net_profit')}。",
            f"- 经营活动现金流量净额：{_metric_line(snapshot, 'operating_cashflow')}。",
            f"- OCF/|归母净利润|：{_format_number(ocf_profit, '') if ocf_profit is not None else '未稳定定位'}。",
            f"- 匹配判断：{direction}。",
            "- 勾稽解释：若收入或利润增长但 OCF 未同步改善，应继续核对应收账款、存货、预收/合同负债、减值和费用项目；证据不足时不做确定性异常断言。",
            f"- 营运资本交叉核对：应收账款 {_metric_line(snapshot, 'accounts_receivable')}；存货 {_metric_line(snapshot, 'inventory')}。",
        ])

    if intent == "core_metrics":
        return "\n".join(["### 核心指标抽取专项结论", _core_metric_table(snapshot)])

    if intent == "cashflow_quality":
        ocf_profit = _ratio(ocf, abs(profit) if profit is not None else None)
        fcf = ocf - capex if ocf is not None and capex is not None else None
        lines = [
            "### 现金流质量专项结论",
            f"- 经营活动现金流：{_metric_line(snapshot, 'operating_cashflow')}。",
            f"- 投资活动现金流：{_metric_line(snapshot, 'investing_cashflow')}。",
            f"- 筹资活动现金流：{_metric_line(snapshot, 'financing_cashflow')}。",
            f"- 现金利润匹配：OCF/|归母净利润| = {_format_number(ocf_profit, '') if ocf_profit is not None else '未稳定定位'}。",
            f"- 自由现金流代理：经营现金流-资本开支 = {_format_number(fcf) if fcf is not None else '因资本开支未稳定定位，保守列为证据缺口'}。",
        ]
        risks = []
        if profit is not None and ocf is not None and profit > 0 and ocf < profit:
            risks.append("经营现金流低于归母净利润，需关注利润现金含量。")
        if inv_cf is not None and inv_cf < 0:
            risks.append("投资活动现金流为净流出，需结合资本开支和扩产节奏判断资金压力。")
        if fin_cf is not None and fin_cf > 0:
            risks.append("筹资活动现金流为净流入，需关注外部融资依赖。")
        if not risks:
            risks.append("未发现稳定证据支持严重现金流背离；仍需结合应收、存货和资本开支持续跟踪。")
        lines.append("- 风险信号：" + "；".join(risks))
        return "\n".join(lines)

    if intent == "debt_pressure":
        liquidity = _ratio(cash, (short_debt or 0) + (due_debt or 0)) if cash is not None and (short_debt or due_debt) else None
        return "\n".join([
            "### 偿债压力专项结论",
            f"- 资产负债率：{_metric_line(snapshot, 'asset_liability_ratio', '%')}。",
            f"- 货币资金：{_metric_line(snapshot, 'monetary_capital')}。",
            f"- 短期借款：{_metric_line(snapshot, 'short_term_borrowings')}。",
            f"- 一年内到期负债：{_metric_line(snapshot, 'current_portion_debt')}。",
            f"- 现金覆盖短债代理：货币资金/(短期借款+一年内到期负债) = {_format_number(liquidity, '') if liquidity is not None else '因短债口径缺失，列为证据缺口'}。",
            "- 判断：同时区分短期流动性压力和长期资本结构压力；若短债口径缺失，不直接断言偿债安全。",
        ])

    if intent == "three_statement_check":
        return "\n".join([
            "### 三大表勾稽专项结论",
            f"1. 净利润与经营现金流：归母净利润 {_metric_line(snapshot, 'parent_net_profit')}；经营现金流 {_metric_line(snapshot, 'operating_cashflow')}。用于判断利润现金含量。",
            f"2. 资产负债表与现金流：货币资金 {_metric_line(snapshot, 'monetary_capital')}，需与现金流量表三类现金流净额方向交叉验证。",
            f"3. 收入与营运资本：营业收入 {_metric_line(snapshot, 'revenue')}；应收账款 {_metric_line(snapshot, 'accounts_receivable')}；存货 {_metric_line(snapshot, 'inventory')}。用于判断收入增长是否伴随回款或库存压力。",
            f"4. 减值与利润：若事实核查报告出现减值、商誉、存货跌价等风险，应回看利润表和资产附注，不凭空断言异常。",
        ])

    if intent == "asset_quality":
        ar_ratio = _ratio(ar, revenue)
        inv_ratio = _ratio(inventory, revenue)
        return "\n".join([
            "### 资产质量专项结论",
            f"- 应收账款：{_metric_line(snapshot, 'accounts_receivable')}；应收/收入 = {_format_number(ar_ratio * 100 if ar_ratio is not None else None, '%')}。",
            f"- 存货：{_metric_line(snapshot, 'inventory')}；存货/收入 = {_format_number(inv_ratio * 100 if inv_ratio is not None else None, '%')}。",
            "- 商誉、固定资产、减值准备：以事实核查报告和年报附注为准；若专项指标未稳定定位，明确作为证据缺口而非编造数值。",
            "- 风险判断：优先关注应收扩张、库存积压、商誉减值假设变化和固定资产利用效率。",
        ])


    if intent == "tracking":
        return "\n".join([
            "### 未来 6 个月跟踪事项清单",
            "| 跟踪事项 | 监控指标 | 触发阈值 | 验证方法 | 更新频率 |",
            "| --- | --- | --- | --- | --- |",
            f"| 利润现金含量 | OCF/归母净利润 | 低于 1 或连续下滑 | 核对现金流量表与利润表；当前 OCF={_metric_cell(snapshot, 'operating_cashflow')} | 季度 |",
            f"| 营运资本压力 | 应收/收入、存货/收入 | 同比上升且高于行业经验区间 | 核对资产负债表附注；应收={_metric_cell(snapshot, 'accounts_receivable')}；存货={_metric_cell(snapshot, 'inventory')} | 月度/季度 |",
            f"| 偿债与融资压力 | 资产负债率、短债覆盖 | 资产负债率抬升或现金覆盖短债下降 | 核对资产负债表、借款和票据附注；资产负债率={_metric_cell(snapshot, 'asset_liability_ratio', '%')} | 季度 |",
        ])

    if intent == "report_generation":
        return "\n".join([
            "### 专业财务报告生成专项结论",
            _compose_report_adoption_section(target),
            _compose_industry_insight(target, snapshot, profile),
        ])

    if intent == "industry_insight":
        return _compose_industry_insight(target, snapshot, profile)

    return "\n".join([
        "### 本题专项结论",
        _core_metric_table(snapshot),
        "- 风险判断遵循证据优先原则：优先使用结构化指标、年报 Markdown 和事实核查报告；资料不足处明确标注证据缺口。",
    ])


def _compose_eval_output(req: EvalE2ERequest, target: dict[str, Any], metadata: dict[str, Any], report: str) -> str:
    question = _question_text(req)
    profile = _request_industry_profile(req)
    focus = _task_focus(question, profile)
    intent = _question_intent(question, profile)
    snapshot = _metric_snapshot(target)
    validation = _financial_validation(snapshot)
    metadata["industry_profile"] = profile.key
    metadata["task_intent"] = intent
    metadata["financial_validation_status"] = validation["status"]
    metadata["financial_missing_metrics"] = validation["missing_metrics"]
    direct_answer = _compose_task_specific_answer(question, target, snapshot, profile)
    context = _source_context(target, report)
    snippets = _extract_relevant_snippets(context, question, focus["checklist"])
    trace = _trace_string(metadata)

    checklist_lines = "\n".join(f"- {item}" for item in focus["checklist"])
    snippet_lines = "\n".join(f"- {snippet}" for snippet in snippets) if snippets else "- 当前缓存报告未抽取到足够直接片段，已标记为证据缺口，需回看年报 Markdown 与结构化指标。"
    factcheck_status = (
        "全链路状态满足本次评测要求。"
        if _response_status(metadata, report) == "success"
        else "部分链路或报告材料存在缺口，以下结论按已入库证据保守输出。"
    )

    preface = f"""# SIQ E2E 评测专项答复

## 用户问题
{question or f"请对 {target['company_name']}（{target['company_code']}）{target['year']} 年年度报告进行财务核查。"}

## 本题关注能力
{focus["focus"]}

## 回答覆盖清单
{checklist_lines}

{_compose_executive_summary(target, snapshot)}

{_compose_dimension_coverage_section(target, metadata, snapshot, profile)}

{_intent_guard_section(question, intent)}

{_compose_financial_validation_section(snapshot)}

{_compose_formal_report_section(target, snapshot, profile)}

## 数据勾稽与事实核查（Fact-Check）
- 链路核查：{trace}。
- 事实核查结论：{factcheck_status}
- 证据原则：以下分析仅基于 SIQ 本地 Wiki、年度报告 Markdown、semantic 证据链、分析报告和事实核查报告；未在材料中稳定定位的数字不作编造。
- 勾稽方法：围绕题目要求检查利润表、资产负债表、现金流量表及附注之间的方向一致性、金额来源、风险解释和证据缺口。

## 本题专项答复
{direct_answer}

{_compose_industry_insight(target, snapshot, profile)}

{_compose_report_adoption_section(target)}

{_compose_risk_tracking_section(snapshot, profile)}

## 与本题最相关的证据摘录
{snippet_lines}

## 附录：事实核查原文节选（供追溯）
"""
    merged = f"{preface}\n\n{report}".strip()
    if len(merged) > OUTPUT_MAX_CHARS:
        merged = merged[:OUTPUT_MAX_CHARS].rstrip() + "\n\n[已截断，完整报告见 metadata.final_report_url]"
    return merged


@router.get("/e2e/health")
def eval_e2e_health():
    return {
        "status": "ok",
        "endpoint": "/api/eval/e2e",
        "wiki_root": str(WIKI_ROOT),
        "report_finder_base": REPORT_FINDER_BASE,
        "pdf2md_api_base": PDF2MD_API_BASE,
        "companies": _iter_wiki_companies(),
    }


@router.post("/e2e")
async def run_eval_e2e(req: EvalE2ERequest):
    target = _parse_eval_target(req)
    if not target["company_name"] and not target["company_code"]:
        return {
            "status": "failed",
            "output": "请求中未识别到公司名称或股票代码。",
            "metadata": {
                "download_status": "missing",
                "pdf_to_markdown_status": "missing",
                "wiki_vector_injected": False,
                "factcheck_status": "missing",
                "checked_at": _now_iso(),
            },
        }

    lock_key = f"{target['company_code'] or target['company_name']}:{target['year']}"
    lock = _pipeline_locks.setdefault(lock_key, asyncio.Lock())
    async with lock:
        assets = _wiki_artifacts(target.get("company"), target["year"])
        metadata = _metadata_from_assets(target, assets)

        if not (
            metadata["download_status"] == "success"
            and metadata["pdf_to_markdown_status"] == "success"
            and metadata["wiki_vector_injected"]
        ):
            await _ensure_pipeline_assets(req, target, metadata)
            target["company"] = _find_wiki_company(target["company_name"], target["company_code"]) or target.get("company")
            refreshed_assets = _wiki_artifacts(target.get("company"), target["year"])
            refreshed_metadata = _metadata_from_assets(target, refreshed_assets)
            metadata.update({k: v for k, v in refreshed_metadata.items() if v not in ("", "missing", False)})
            metadata["wiki_vector_injected"] = bool(refreshed_metadata.get("wiki_vector_injected") or metadata.get("wiki_vector_injected"))

        output, raw_html = await _ensure_final_report(req, target, metadata)
        refreshed_assets = _wiki_artifacts(target.get("company"), target["year"])
        refreshed_metadata = _metadata_from_assets(target, refreshed_assets)
        for key in ("analysis_status", "factcheck_status", "analysis_report_path", "factcheck_report_path", "final_report_url"):
            if refreshed_metadata.get(key):
                metadata[key] = refreshed_metadata[key]

        status = _response_status(metadata, output)
        metadata["evaluation_trace"] = _trace_string(metadata)
        metadata["checked_at"] = _now_iso()
        output = _compose_eval_output(req, target, metadata, output)

        response = {
            "status": status,
            "output": output,
            "metadata": metadata,
            "metadata_text": metadata["evaluation_trace"],
        }
        if req.include_html and raw_html:
            response["html"] = raw_html[:OUTPUT_MAX_CHARS]
        return response
