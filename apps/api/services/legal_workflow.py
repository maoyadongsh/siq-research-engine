"""Deterministic legal opinion artifact workflow for the legal agent.

The workflow is intentionally narrow: it only handles explicit requests for a
formal legal artifact/HTML file. Ordinary legal Q&A remains on the chat path so
the assistant can answer naturally without creating wiki reports.
"""

from __future__ import annotations

import html
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote, unquote, urlsplit

from services.command_runner import run_command
from services.path_config import PROJECT_ROOT, WIKI_ROOT
from services.specialist_artifact_contract import (
    SpecialistArtifactValidation,
    citation_has_locator,
    finalize_specialist_artifact,
    write_specialist_artifact_manifest,
)

DEFAULT_TIMEOUT_SECONDS = int(os.getenv("SIQ_LEGAL_WORKFLOW_TIMEOUT_SECONDS", "900"))
MIN_CITATIONS = 3
DEFAULT_TOP_K = 8
MIN_ANNUAL_REPORT_FACTS = 4
MAX_LEGAL_CITATIONS = 18

LEGAL_ACTION_RE = re.compile(r"(生成|出具|保存|导出|固化|落盘|创建|形成|产出|做一份|出一份)")
LEGAL_ARTIFACT_RE = re.compile(r"(HTML|html|网页|页面|文件|法律意见书|法律意见|意见书|合规审查报告|合规报告|法务报告)")
LEGAL_NO_HTML_RE = re.compile(
    r"((不要|不需要|无需|不用).{0,10}(HTML|html|网页|页面|文件|落盘|保存|固化)|"
    r"直接.{0,12}(对话|聊天).{0,12}(输出|回答)|不要生成\s*HTML|不要保存)"
)
LEGAL_META_QUESTION_RE = re.compile(r"(为什么|为何|原因|怎么没有|没有调用|没调用|没有固化|没固化|如何设计|怎么设计)")
OVERWRITE_RE = re.compile(r"(覆盖|替换现有|覆盖现有|写回默认|更新现有|改写现有)")
STOCK_CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")
SAFE_FILENAME_RE = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff._-]+")
ANNUAL_REPORT_RE = re.compile(r"(年报|年度报告|annual\s*report)", re.IGNORECASE)
REPORT_YEAR_RE = re.compile(r"(?<!\d)(20\d{2})(?!\d)")

ANNUAL_REPORT_FACT_SPECS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "audit_opinion",
        "年度报告与财务报表审计意见",
        (
            r"出具.{0,80}(?:标准(?:的)?无保留意见|无保留意见).{0,30}审计报告",
            r"审计报告意见类型.{0,20}(?:标准(?:的)?无保留意见|无保留意见)",
        ),
    ),
    (
        "information_disclosure",
        "定期报告与信息披露",
        (
            r"(?:公司|本公司).{0,20}(?:全年|报告期内).{0,20}(?:完成|披露).{0,30}(?:定期报告|临时公告)",
            r"信息披露.{0,80}(?:真实|准确|完整|及时|评价)",
            r"(?:定期报告|临时公告).{0,80}(?:真实|准确|完整|及时|评价)",
        ),
    ),
    (
        "corporate_governance",
        "公司治理、董事会与专门委员会",
        (
            r"董事会.{0,100}(?:由.{0,30}董事组成|召开.{0,20}次会议)",
            r"董事会由.{0,60}董事组成",
            r"独立董事.{0,80}(?:审计委员会|专门会议|履职)",
        ),
    ),
    (
        "related_party_transactions",
        "关联交易及审议披露",
        (
            r"(?:股东大会|股东会).{0,80}(?:审议通过|批准).{0,40}(?:日常)?关联交易",
            r"(?:日常)?关联交易.{0,100}(?:实际发生|预计金额|审议|披露)",
        ),
    ),
    (
        "fund_occupation",
        "非经营性资金占用",
        (r"非经营性.{0,20}(?:占用资金|资金占用)",),
    ),
    (
        "external_guarantee",
        "对外担保及违规担保",
        (
            r"违规担保",
            r"对外担保.{0,100}(?:余额|总额|审议|披露|不存在|为\s*0)",
            r"担保情况",
        ),
    ),
    (
        "internal_control",
        "内部控制评价与审计",
        (
            r"内部控制.{0,100}(?:重大缺陷|重要缺陷|有效执行)",
            r"(?:内控|内部控制)审计.{0,80}(?:标准(?:的)?无保留意见|无保留意见)",
            r"内部控制审计报告意见类型",
        ),
    ),
    (
        "litigation",
        "重大诉讼与仲裁",
        (r"(?:有|无|不存在).{0,12}重大诉讼.{0,20}仲裁", r"重大诉讼、?仲裁事项"),
    ),
    (
        "regulatory_penalty",
        "违法违规、监管处罚及整改",
        (r"涉嫌违法违规.{0,40}(?:处罚|整改)", r"受到处罚及整改情况"),
    ),
    (
        "financial_reporting",
        "主要财务数据",
        (r"营业收入.{0,100}(?:同比|上年|增长|下降|亿元|万元)",),
    ),
)

ANNUAL_RETRIEVAL_TOPICS: tuple[tuple[str, str], ...] = (
    (
        "定期报告与信息披露",
        "《中华人民共和国证券法》第七十八条 《中华人民共和国证券法》第七十九条 《中华人民共和国证券法》第八十二条 年度报告 定期报告",
    ),
    (
        "公司治理",
        "《中华人民共和国公司法》第一百二十一条 《中华人民共和国公司法》第一百三十六条 《中华人民共和国公司法》第一百三十七条 审计委员会 独立董事",
    ),
    (
        "关联交易",
        "《中华人民共和国公司法》第一百三十九条 《中华人民共和国公司法》第一百八十二条 《中华人民共和国公司法》第一百八十五条 关联交易 回避表决",
    ),
    (
        "对外担保与资金占用",
        "《中华人民共和国公司法》第十五条 《中华人民共和国公司法》第一百三十五条 《中华人民共和国证券法》第八十条 对外担保 资金占用",
    ),
    (
        "内部控制与审计",
        "《中华人民共和国公司法》第一百三十七条 《中华人民共和国公司法》第二百零八条 《中华人民共和国公司法》第二百一十六条 内部控制 审计 财务报告",
    ),
    (
        "诉讼与监管处罚",
        "中华人民共和国证券法 涉及公司的重大诉讼、仲裁 公司涉嫌犯罪被依法立案调查",
    ),
)

ANNUAL_LEGAL_NOISE_TERMS = (
    "证券投资基金法",
    "优化营商环境条例",
    "不动产登记",
    "港口法",
)

ANNUAL_LEGAL_RELEVANCE_TERMS = (
    "年度报告",
    "定期报告",
    "信息披露",
    "上市公司",
    "董事",
    "审计委员会",
    "公司治理",
    "关联交易",
    "担保",
    "资金占用",
    "内部控制",
    "诉讼",
    "仲裁",
    "行政处罚",
    "监管措施",
    "证券法",
    "公司法",
    "股票上市规则",
)

ANNUAL_LEGAL_SPECIALIZED_SOURCE_TERMS = (
    "上市公司信息披露管理办法",
    "上市公司治理准则",
    "上市公司独立董事管理办法",
    "上市公司章程指引",
    "上市公司监管指引",
    "证券交易所股票上市规则",
    "证券交易所上市公司自律监管",
    "公开发行证券的公司信息披露内容与格式准则",
    "企业内部控制基本规范",
    "企业内部控制配套指引",
)

ANNUAL_TOPIC_CONTENT_TERMS: dict[str, tuple[str, ...]] = {
    "定期报告与信息披露": ("年度报告", "定期报告", "信息披露", "真实、准确、完整"),
    "公司治理": ("董事会", "独立董事", "审计委员会", "公司治理"),
    "关联交易": ("关联交易", "关联关系", "回避", "无关联关系董事"),
    "对外担保与资金占用": ("担保", "资金占用", "公司资金", "股东会决议"),
    "内部控制与审计": ("内部控制", "审计", "财务会计报告", "会计资料"),
    "诉讼与监管处罚": ("诉讼", "仲裁", "行政处罚", "监管措施"),
}


@dataclass(frozen=True)
class LegalWorkflowRequest:
    company_query: str
    topic: str
    jurisdiction: str = "中国大陆"
    report_path: Path | None = None
    prompt: str = ""
    top_k: int = DEFAULT_TOP_K
    allow_overwrite: bool = False
    session_id: str = ""


@dataclass(frozen=True)
class LegalWorkflowResponse:
    handled: bool
    reply: str
    result: dict[str, Any]


@dataclass(frozen=True)
class AnnualReportBundle:
    report_path: Path
    report_id: str
    report_year: int | None
    period_end: str
    task_id: str
    facts: list[dict[str, Any]]
    metrics: list[dict[str, Any]]


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


def _clean(value: str | None) -> str:
    return str(value or "").strip().strip(" :：,，。；;")


def is_legal_generation_request(message: str, context: Any | None = None) -> bool:
    """Return True only for explicit legal artifact/HTML generation requests."""

    text = (message or "").strip()
    if not text or LEGAL_META_QUESTION_RE.search(text):
        return False
    if LEGAL_NO_HTML_RE.search(text):
        return False
    return bool(LEGAL_ACTION_RE.search(text) and LEGAL_ARTIFACT_RE.search(text))


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


def _report_path_from_context(context: Any | None) -> Path | None:
    report = _context_report(context)
    value = str(report.get("url") or "").strip()
    filename = str(report.get("filename") or "").strip()
    company_dir = str(_context_company(context).get("dir") or "").strip()
    if value:
        decoded_path = unquote(urlsplit(value).path)
        parts = [part for part in decoded_path.split("/") if part]
        try:
            company_index = parts.index("companies")
            company = parts[company_index + 1]
            section = parts[company_index + 2]
            tail = parts[company_index + 3 :]
        except (ValueError, IndexError):
            tail = []
        if tail and section in {"reports", "analysis", "legal"}:
            candidate = WIKI_ROOT / "companies" / company / section / Path(*tail)
            if candidate.is_dir():
                candidate = candidate / "report.md"
            if candidate.suffix.lower() in {".html", ".json"} and section != "legal":
                md_candidate = candidate.with_suffix(".md")
                if md_candidate.is_file():
                    candidate = md_candidate
            if candidate.is_file() and candidate.suffix.lower() in {".md", ".html", ".json"}:
                return candidate
    if filename and company_dir:
        raw_section = str(report.get("type") or "analysis").strip().lower() or "analysis"
        section = "reports" if raw_section in {"report", "reports", "annual", "annual_report"} else raw_section
        if section not in {"reports", "analysis", "legal"}:
            return None
        candidate = WIKI_ROOT / "companies" / company_dir / section / unquote(filename)
        if candidate.is_file() and candidate.suffix.lower() in {".md", ".html", ".json"}:
            return candidate
    return None


def _strip_intent_words(text: str) -> str:
    text = re.sub(r"(请|麻烦|帮我|基于|按照|当前公司|当前报告|我提供的事实)", " ", text)
    text = LEGAL_ACTION_RE.sub(" ", text)
    text = LEGAL_ARTIFACT_RE.sub(" ", text)
    text = re.sub(r"(法务合规|公司法务|律师|工作底稿|口径|正式|初稿|草稿)", " ", text)
    return re.sub(r"\s+", " ", text).strip(" ：:，,。；;")


def _extract_topic(message: str, context: Any | None) -> str:
    text = _strip_intent_words(message or "")
    company = _context_company(context)
    for value in (company.get("dir"), company.get("code"), company.get("name")):
        clean = _clean(value)
        if clean:
            text = text.replace(clean, " ")
    text = re.sub(r"\s+", " ", text).strip(" ：:，,。；;")
    if text:
        return text[:80]
    report = _context_report(context)
    filename = _clean(report.get("filename"))
    if filename:
        return Path(filename).stem[:80]
    return "当前事项合规审查"


def _extract_jurisdiction(message: str) -> str:
    if re.search(r"(香港|港股|联交所|HKEX)", message, re.IGNORECASE):
        return "中国香港"
    if re.search(r"(美国|SEC|NASDAQ|NYSE)", message, re.IGNORECASE):
        return "美国"
    return "中国大陆"


def build_legal_workflow_request(message: str, context: Any | None = None) -> LegalWorkflowRequest | None:
    if not is_legal_generation_request(message, context):
        return None
    company_query = _extract_company_query(message, context)
    if not company_query:
        return None
    return LegalWorkflowRequest(
        company_query=company_query,
        topic=_extract_topic(message, context),
        jurisdiction=_extract_jurisdiction(message or ""),
        report_path=_report_path_from_context(context),
        prompt=(message or "").strip(),
        allow_overwrite=bool(OVERWRITE_RE.search(message or "")),
    )


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def legal_workflow_autoroute_enabled(request: LegalWorkflowRequest) -> bool:
    if not _env_flag("SIQ_LEGAL_WORKFLOW_AUTOROUTE", True):
        return False
    if _is_annual_report_request(request):
        return _env_flag("SIQ_LEGAL_ANNUAL_WORKFLOW_AUTOROUTE", False)
    return True


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
        return {
            "company_id": code,
            "stock_code": code,
            "company_short_name": code,
            "company_full_name": "",
            "company_path": f"companies/{code}",
        }
    return None


def _company_dir(company: dict[str, str]) -> Path:
    company_path = company.get("company_path")
    if company_path:
        return WIKI_ROOT / company_path
    company_id = company.get("company_id") or company.get("stock_code") or ""
    return WIKI_ROOT / "companies" / company_id


def _company_dir_name(company_dir: Path) -> str:
    return company_dir.name


def _is_annual_report_request(request: LegalWorkflowRequest) -> bool:
    return bool(ANNUAL_REPORT_RE.search(f"{request.topic} {request.prompt}"))


def _requested_report_year(request: LegalWorkflowRequest) -> int | None:
    match = REPORT_YEAR_RE.search(f"{request.topic} {request.prompt}")
    return int(match.group(1)) if match else None


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _path_is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except (OSError, ValueError):
        return False
    return True


def _annual_report_metadata(report_dir: Path) -> tuple[int | None, str]:
    payload = _read_json(report_dir / "report.json")
    report = payload.get("report") if isinstance(payload.get("report"), dict) else {}
    report_kind = str(report.get("report_kind") or "")
    year_raw = report.get("report_year")
    try:
        report_year = int(year_raw) if year_raw not in (None, "") else None
    except (TypeError, ValueError):
        report_year = None
    if report_year is None:
        match = REPORT_YEAR_RE.search(str(report.get("report_id") or report_dir.name))
        report_year = int(match.group(1)) if match else None
    return report_year, report_kind


def _report_dir_from_path(path: Path, reports_root: Path) -> Path | None:
    if not path.exists() or not _path_is_within(path, reports_root):
        return None
    candidate = path if path.is_dir() else path.parent
    while candidate != reports_root and candidate.parent != candidate:
        if candidate.parent == reports_root:
            return candidate
        candidate = candidate.parent
    return None


def _resolve_annual_report_path(company_dir: Path, request: LegalWorkflowRequest) -> Path | None:
    reports_root = company_dir / "reports"
    requested_year = _requested_report_year(request)

    # Prefer the repository's manifest-first resolver when the company has a
    # catalogued report package. Minimal/legacy fixtures fall back to the
    # filesystem-compatible branch below.
    try:
        from services.research_report_package import enumerate_companies, enumerate_report_packages

        resolved_company = next(
            (
                item
                for item in enumerate_companies(wiki_root=WIKI_ROOT, markets=("CN",))
                if item.company_dir.resolve() == company_dir.resolve()
            ),
            None,
        )
        if resolved_company is not None:
            packages = []
            for package in enumerate_report_packages(resolved_company, agent_type="legal"):
                year_match = REPORT_YEAR_RE.search(package.report_id)
                package_year = int(year_match.group(1)) if year_match else None
                is_annual = "annual" in package.report_id.lower() or "年报" in str(package.manifest)
                if not is_annual or (requested_year is not None and package_year != requested_year):
                    continue
                packages.append((package_year or 0, package))
            if packages:
                packages.sort(key=lambda item: (item[0], item[1].report_id), reverse=True)
                package = packages[0][1]
                report_md = next((path for path in package.fulltext_paths if path.name == "report.md"), None)
                if report_md is not None and report_md.is_file():
                    return report_md
    except Exception:
        pass

    preferred_dir = _report_dir_from_path(request.report_path, reports_root) if request.report_path else None
    if preferred_dir is not None:
        report_year, report_kind = _annual_report_metadata(preferred_dir)
        is_annual = report_kind == "annual_report" or "annual" in preferred_dir.name.lower()
        if is_annual and (requested_year is None or report_year in {None, requested_year}):
            preferred_md = preferred_dir / "report.md"
            if preferred_md.is_file():
                return preferred_md

    candidates: list[tuple[int, Path]] = []
    if reports_root.is_dir():
        for report_dir in reports_root.iterdir():
            if not report_dir.is_dir() or not (report_dir / "report.md").is_file():
                continue
            report_year, report_kind = _annual_report_metadata(report_dir)
            if report_kind and report_kind != "annual_report" and "annual" not in report_dir.name.lower():
                continue
            if not report_kind and "annual" not in report_dir.name.lower():
                continue
            if requested_year is not None and report_year != requested_year:
                continue
            candidates.append((report_year or 0, report_dir / "report.md"))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1].parent.name), reverse=True)
    return candidates[0][1]


def _page_for_line(lines: list[str]) -> list[int | None]:
    pages: list[int | None] = []
    current_page: int | None = None
    for line in lines:
        match = re.search(r"\[PDF_PAGE:\s*(\d+)\]", line)
        if match:
            current_page = int(match.group(1))
        pages.append(current_page)
    return pages


def _fact_excerpt(lines: list[str], index: int, limit: int = 620) -> str:
    first = lines[index].strip()
    selected = [first]
    needs_context = first.startswith("#") or len(re.sub(r"[#*\s]", "", first)) < 18
    if needs_context:
        for next_line in lines[index + 1 : index + 6]:
            clean = next_line.strip()
            if not clean:
                continue
            if clean.startswith("#") and len(selected) > 1:
                break
            if re.fullmatch(r"\[PDF_PAGE:\s*\d+\]", clean):
                continue
            selected.append(clean)
            if len(" ".join(selected)) >= limit:
                break
    joined = " ".join(selected)
    joined = re.sub(r"</t[dh]>", " | ", joined, flags=re.IGNORECASE)
    joined = re.sub(r"<[^>]+>", " ", joined)
    joined = html.unescape(joined)
    return _compact(joined, limit)


def _extract_annual_report_facts(
    report_path: Path,
    *,
    report_id: str,
    report_year: int | None,
    period_end: str,
    task_id: str,
) -> list[dict[str, Any]]:
    try:
        lines = report_path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    pages = _page_for_line(lines)
    facts: list[dict[str, Any]] = []
    prefer_last_match = {"fund_occupation", "external_guarantee", "regulatory_penalty"}
    for fact_key, title, patterns in ANNUAL_REPORT_FACT_SPECS:
        matched_index: int | None = None
        for pattern in patterns:
            matches = [
                index
                for index, line in enumerate(lines)
                if re.search(pattern, line, re.IGNORECASE)
            ]
            if matches:
                matched_index = matches[-1] if fact_key in prefer_last_match else matches[0]
                break
        if matched_index is None:
            continue
        line_number = matched_index + 1
        quote_text = _fact_excerpt(lines, matched_index)
        fact: dict[str, Any] = {
            "rank": len(facts) + 1,
            "fact_key": fact_key,
            "title": title,
            "source_type": "annual_report",
            "source": f"{report_year or ''}年年度报告-{title}".lstrip("年"),
            "source_path": str(report_path),
            "report_id": report_id,
            "period": period_end,
            "chunk_index": str(line_number),
            "md_line": line_number,
            "quote": quote_text,
            "relevance": f"用于核验{title}的公司特定事实",
        }
        if pages[matched_index] is not None:
            fact["pdf_page"] = pages[matched_index]
        if task_id:
            fact["task_id"] = task_id
        facts.append(fact)
    return facts


def _format_metric_value(value: float, unit: str) -> str:
    if unit in {"元", "人民币元", "CNY"}:
        return f"{value / 100_000_000:,.2f} 亿元"
    return f"{value:,.4f} {unit}".rstrip()


def _load_annual_metrics(
    company_dir: Path,
    *,
    report_id: str,
    report_year: int | None,
    period_end: str,
    task_id: str,
) -> list[dict[str, Any]]:
    if report_year is None:
        return []
    path = company_dir / "metrics" / "reports" / report_id / "key_metrics.json"
    payload = _read_json(path)
    rows = payload.get("data") if isinstance(payload.get("data"), list) else []
    selected_names = {
        "operating_revenue",
        "parent_net_profit",
        "operating_cash_flow_net",
        "equity_attributable_parent",
        "total_assets",
    }
    current_key = str(report_year)
    prior_key = str(report_year - 1)
    metrics: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping) or str(row.get("canonical_name") or "") not in selected_names:
            continue
        values = row.get("values") if isinstance(row.get("values"), Mapping) else {}
        try:
            current = float(values[current_key])
            prior = float(values[prior_key])
        except (KeyError, TypeError, ValueError):
            continue
        unit = str(row.get("unit") or "")
        yoy = None if prior <= 0 else (current - prior) / prior * 100
        source_map = row.get("sources") if isinstance(row.get("sources"), Mapping) else {}
        locator = source_map.get(current_key) if isinstance(source_map.get(current_key), Mapping) else {}
        metric: dict[str, Any] = {
            "rank": len(metrics) + 1,
            "metric_key": str(row.get("canonical_name") or ""),
            "name": str(row.get("name") or row.get("canonical_name") or ""),
            "current_year": report_year,
            "prior_year": report_year - 1,
            "current_value": current,
            "prior_value": prior,
            "current_display": _format_metric_value(current, unit),
            "prior_display": _format_metric_value(prior, unit),
            "yoy": yoy,
            "yoy_display": "不适用（上期非正数）" if yoy is None else f"{yoy:+.2f}%",
            "source_type": "annual_report_metric",
            "source": f"年度报告关键财务指标-{row.get('name') or row.get('canonical_name')}",
            "source_path": str(path),
            "report_id": report_id,
            "period": period_end,
            "quote": f"{current_key}={values.get(current_key)}; {prior_key}={values.get(prior_key)}; unit={unit}",
            "relevance": "用于核验年度报告关键财务数据及变动幅度",
        }
        if locator.get("table_index") not in (None, ""):
            metric["table_index"] = locator["table_index"]
            metric["chunk_index"] = str(locator["table_index"])
        if locator.get("line") not in (None, ""):
            metric["md_line"] = locator["line"]
            metric.setdefault("chunk_index", str(locator["line"]))
        if task_id:
            metric["task_id"] = task_id
        metrics.append(metric)
    return metrics


def _load_annual_report_bundle(company_dir: Path, report_path: Path) -> AnnualReportBundle:
    report_dir = report_path.parent
    payload = _read_json(report_dir / "report.json")
    report = payload.get("report") if isinstance(payload.get("report"), dict) else {}
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    report_id = str(report.get("report_id") or report_dir.name)
    report_year_raw = report.get("report_year")
    try:
        report_year = int(report_year_raw) if report_year_raw not in (None, "") else None
    except (TypeError, ValueError):
        report_year = None
    if report_year is None:
        match = REPORT_YEAR_RE.search(report_id)
        report_year = int(match.group(1)) if match else None
    filename_metadata = report.get("source_filename_metadata") if isinstance(report.get("source_filename_metadata"), dict) else {}
    period_end = str(filename_metadata.get("report_end") or (f"{report_year}-12-31" if report_year else ""))
    task_id = str(source.get("task_id") or "")
    facts = _extract_annual_report_facts(
        report_path,
        report_id=report_id,
        report_year=report_year,
        period_end=period_end,
        task_id=task_id,
    )
    metrics = _load_annual_metrics(
        company_dir,
        report_id=report_id,
        report_year=report_year,
        period_end=period_end,
        task_id=task_id,
    )
    return AnnualReportBundle(
        report_path=report_path,
        report_id=report_id,
        report_year=report_year,
        period_end=period_end,
        task_id=task_id,
        facts=facts,
        metrics=metrics,
    )


def _legal_milvus_script() -> Path:
    return PROJECT_ROOT / "agents" / "hermes" / "profiles" / "siq_legal" / "scripts" / "legal_milvus_cli.py"


def _validator_script() -> Path:
    return PROJECT_ROOT / "agents" / "hermes" / "profiles" / "siq_legal" / "scripts" / "validate_legal_opinion.py"


def _load_stdout_json(completed: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    stdout = (completed.stdout or "").strip()
    if not stdout:
        return {}
    for index in range(len(stdout)):
        if stdout[index] != "{":
            continue
        try:
            payload = json.loads(stdout[index:])
        except json.JSONDecodeError:
            continue
        return payload if isinstance(payload, dict) else {}
    return {}


def _retrieval_query(request: LegalWorkflowRequest, company: dict[str, str]) -> str:
    company_name = company.get("company_short_name") or company.get("company_full_name") or company.get("stock_code") or ""
    listed_hint = "上市公司 信息披露 公司治理 证券法 公司法"
    return " ".join(part for part in [company_name, request.topic, request.jurisdiction, listed_hint] if part).strip()


def _retrieval_queries(request: LegalWorkflowRequest, company: dict[str, str]) -> list[tuple[str, str]]:
    if not _is_annual_report_request(request):
        return [("事项综合审查", _retrieval_query(request, company))]
    queries: list[tuple[str, str]] = []
    for label, terms in ANNUAL_RETRIEVAL_TOPICS:
        query = " ".join(part for part in (request.jurisdiction, label, terms) if part).strip()
        queries.append((label, query))
    return queries


def _retrieve_legal_sources(
    query: str,
    *,
    top_k: int = DEFAULT_TOP_K,
    timeout: int | float = DEFAULT_TIMEOUT_SECONDS,
) -> tuple[dict[str, Any], subprocess.CompletedProcess[str] | None]:
    script = _legal_milvus_script()
    if not script.is_file():
        return {"ok": False, "stage": "script_missing", "results": []}, None
    cmd = [
        sys.executable,
        str(script),
        "hybrid_search",
        query,
        "--top-k",
        str(max(MIN_CITATIONS, min(top_k, 20))),
    ]
    completed = run_command(cmd, cwd=PROJECT_ROOT, timeout=timeout)
    payload = _load_stdout_json(completed)
    payload["ok"] = completed.returncode == 0 and isinstance(payload.get("results"), list)
    return payload, completed


def _compact(value: str, limit: int = 220) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _citation_source(result: Mapping[str, Any]) -> str:
    source = str(result.get("source") or "").strip()
    if source:
        return source
    source_path = str(result.get("source_path") or "").strip()
    if source_path:
        return Path(source_path).stem
    return "法规检索片段"


def _relevant_legal_quote(result: Mapping[str, Any], limit: int = 360) -> str:
    text = re.sub(r"\s+", " ", str(result.get("text") or "")).strip()
    if not text:
        return ""
    anchors: list[str] = []
    exact_reason = str(result.get("exact_reason") or "")
    article_match = re.search(r"第[一二三四五六七八九十百千万零〇两\d]+条(?:之一|之二|之三)?", exact_reason)
    if article_match and (article_position := text.find(article_match.group(0))) >= 0:
        start = max(0, article_position - 70)
        return ("…" if start else "") + _compact(text[start:], limit)
    topic = str(result.get("retrieval_topic") or "")
    anchors.extend(ANNUAL_TOPIC_CONTENT_TERMS.get(topic, ()))
    positions = [(text.find(anchor), anchor) for anchor in anchors if text.find(anchor) >= 0]
    if not positions:
        return _compact(text, limit)
    position, _anchor = min(positions, key=lambda item: item[0])
    start = max(0, position - 70)
    return ("…" if start else "") + _compact(text[start:], limit)


def _annual_legal_result_is_relevant(result: Mapping[str, Any]) -> bool:
    source = str(result.get("source") or Path(str(result.get("source_path") or "")).name).strip()
    source_name = Path(source).name
    text = " ".join(str(result.get(key) or "") for key in ("text", "relevance"))
    if any(term in f"{source_name} {text}" for term in ANNUAL_LEGAL_NOISE_TERMS):
        return False
    primary_source = bool(
        re.fullmatch(r"中华人民共和国(?:公司法|证券法)(?:_\d{8})?(?:\.md)?", source_name)
    )
    specialized_source = any(term in source_name for term in ANNUAL_LEGAL_SPECIALIZED_SOURCE_TERMS)
    if not primary_source and not specialized_source:
        return False
    if primary_source and "exact_reason" in result:
        exact_reason = str(result.get("exact_reason") or "")
        if not (
            exact_reason.startswith("article:")
            or exact_reason.startswith("neighbor:")
            or exact_reason.startswith("source_focus:")
        ):
            return False
        if exact_reason.startswith("article:"):
            article_match = re.search(
                r"第[一二三四五六七八九十百千万零〇两\d]+条(?:之一|之二|之三)?",
                exact_reason,
            )
            raw_text = str(result.get("text") or "")
            if article_match and not re.search(
                rf"(?<!本法){re.escape(article_match.group(0))}[\s　]",
                raw_text,
            ):
                return False
    topic = str(result.get("retrieval_topic") or "")
    content_terms = ANNUAL_TOPIC_CONTENT_TERMS.get(topic, ANNUAL_LEGAL_RELEVANCE_TERMS)
    return any(term in text for term in content_terms)


def _normalize_citations(results: list[Any], *, annual_report: bool = False) -> list[dict[str, Any]]:
    citations: list[dict[str, Any]] = []
    seen: set[str] = set()
    per_source: dict[str, int] = {}
    ordered_results = list(results)
    if annual_report:
        topic_order = [label for label, _query in ANNUAL_RETRIEVAL_TOPICS]
        grouped: dict[str, list[Any]] = {topic: [] for topic in topic_order}
        for result in results:
            if isinstance(result, Mapping):
                grouped.setdefault(str(result.get("retrieval_topic") or ""), []).append(result)
        ordered_results = []
        max_group_size = max((len(group) for group in grouped.values()), default=0)
        for index in range(max_group_size):
            for topic in topic_order:
                group = grouped.get(topic) or []
                if index < len(group):
                    ordered_results.append(group[index])
    source_limit = 8 if annual_report else 3
    for result in ordered_results:
        if not isinstance(result, Mapping):
            continue
        if annual_report and not _annual_legal_result_is_relevant(result):
            continue
        source_path = str(result.get("source_path") or "").strip()
        chunk_index = str(result.get("chunk_index") or "").strip()
        topic_key = str(result.get("retrieval_topic") or "") if annual_report else ""
        key = f"{source_path}#{chunk_index}#{topic_key}"
        if key in seen:
            continue
        seen.add(key)
        source = _citation_source(result)
        source_key = source_path or source
        if per_source.get(source_key, 0) >= source_limit:
            continue
        text = _relevant_legal_quote(result) if annual_report else _compact(str(result.get("text") or ""), 220)
        if not source_path and not text:
            continue
        per_source[source_key] = per_source.get(source_key, 0) + 1
        citations.append(
            {
                "rank": str(result.get("rank") or len(citations) + 1),
                "source_type": "legal_corpus",
                "source": source,
                "source_path": source_path or source,
                "chunk_index": chunk_index or "N/A",
                "quote": text or source,
                "relevance": str(
                    result.get("relevance")
                    or result.get("retrieval_topic")
                    or "作为本事项法律适用和风险判断的检索依据"
                ),
                "retrieval_topic": str(result.get("retrieval_topic") or "事项综合审查"),
                "exact_reason": str(result.get("exact_reason") or ""),
            }
        )
        if len(citations) >= MAX_LEGAL_CITATIONS:
            break
    return citations


def _retrieve_legal_source_set(
    request: LegalWorkflowRequest,
    company: dict[str, str],
    *,
    timeout: int | float,
) -> tuple[dict[str, Any], subprocess.CompletedProcess[str] | None]:
    query_specs = _retrieval_queries(request, company)
    query_timeout = max(60.0, float(timeout) / max(1, len(query_specs)))
    query_records: list[dict[str, Any]] = []
    combined_results: list[dict[str, Any]] = []
    last_completed: subprocess.CompletedProcess[str] | None = None
    successful_queries = 0
    for label, query in query_specs:
        try:
            payload, completed = _retrieve_legal_sources(
                query,
                top_k=request.top_k,
                timeout=query_timeout,
            )
        except subprocess.TimeoutExpired as exc:
            query_records.append({"topic": label, "query": query, "ok": False, "error": str(exc)})
            continue
        if completed is not None:
            last_completed = completed
        results = payload.get("results") if isinstance(payload.get("results"), list) else []
        query_records.append(
            {
                "topic": label,
                "query": query,
                "ok": bool(payload.get("ok")),
                "result_count": len(results),
                "stage": payload.get("stage"),
            }
        )
        if not payload.get("ok"):
            continue
        successful_queries += 1
        for raw in results:
            if not isinstance(raw, Mapping):
                continue
            combined_results.append({**dict(raw), "retrieval_topic": label, "retrieval_query": query})
    return (
        {
            "ok": successful_queries > 0,
            "stage": "completed" if successful_queries > 0 else "legal_retrieval_failed",
            "collection": "ic_legal_scanner",
            "query": " | ".join(query for _label, query in query_specs),
            "queries": query_records,
            "successful_query_count": successful_queries,
            "results": combined_results,
        },
        last_completed,
    )


def _relative(path: str | Path | None) -> str:
    if not path:
        return ""
    raw = Path(str(path))
    try:
        return raw.resolve().relative_to(PROJECT_ROOT).as_posix()
    except Exception:
        return str(path)


def _wiki_legal_url(html_path: str | Path | None) -> str:
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
        f"/api/wiki/companies/{quote(company_dir, safe='')}/legal/"
        f"{quote(path.name, safe='')}"
    )


def _safe_filename_part(value: str, default: str = "legal_opinion") -> str:
    cleaned = SAFE_FILENAME_RE.sub("_", value.strip()).strip("._-")
    return (cleaned or default)[:48]


def _default_output_path(company_dir: Path, topic: str, allow_overwrite: bool) -> Path:
    legal_dir = company_dir / "legal"
    slug = _safe_filename_part(topic, "current_matter")
    if allow_overwrite:
        return legal_dir / f"legal_opinion_{slug}.html"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return legal_dir / f"legal_opinion_{slug}_{timestamp}.html"


def _h(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


def _build_citation_table_rows(citations: list[dict[str, Any]]) -> str:
    rows = []
    for index, citation in enumerate(citations, start=1):
        rows.append(
            "<tr>"
            f"<td>[{index}]</td>"
            f"<td>{_h(citation.get('source'))}</td>"
            f"<td>{_h(citation.get('source_path'))}</td>"
            f"<td>{_h(citation.get('chunk_index') or citation.get('md_line') or citation.get('table_index'))}</td>"
            f"<td>{_h(citation.get('relevance'))}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def _build_citation_lines(citations: list[dict[str, Any]]) -> str:
    lines = []
    for index, citation in enumerate(citations, start=1):
        lines.append(
            "<p>"
            f"[{index}] source={_h(citation.get('source'))}, "
            f"source_type={_h(citation.get('source_type'))}, "
            f"source_path={_h(citation.get('source_path'))}, "
            f"chunk_index={_h(citation.get('chunk_index') or citation.get('md_line') or citation.get('table_index'))}, "
            f"md_line={_h(citation.get('md_line'))}, "
            f"pdf_page={_h(citation.get('pdf_page'))}, "
            f"quote=&quot;{_h(citation.get('quote'))}&quot;, "
            f"relevance={_h(citation.get('relevance'))}"
            "</p>"
        )
    return "\n".join(lines)


def _build_generic_legal_opinion_html(
    *,
    company: dict[str, str],
    company_dir: Path,
    request: LegalWorkflowRequest,
    citations: list[dict[str, str]],
    retrieval_query: str,
) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    issued_date = now.split()[0]
    stock_code = company.get("stock_code") or company.get("company_id") or ""
    company_name = company.get("company_short_name") or company.get("company_full_name") or stock_code or "当前公司"
    full_name = company.get("company_full_name") or company_name
    subject = f"{stock_code}-{company_name}" if stock_code and stock_code != company_name else company_name
    topic = request.topic or "当前事项合规审查"
    document_no = f"SIQ-LGL-{datetime.now().strftime('%Y%m%d%H%M%S')}-{_safe_filename_part(stock_code or company_name, 'company')}"
    report_note = _relative(request.report_path) if request.report_path else "未绑定特定报告，以用户当前问题和法规检索结果为基础"
    primary_sources = "、".join(_h(citation.get("source")) for citation in citations[:3]) or "本机法律库检索结果"
    citation_refs = "".join(f"[{index}]" for index in range(1, min(len(citations), 5) + 1))
    table_rows = _build_citation_table_rows(citations)
    citation_lines = _build_citation_lines(citations)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_h(subject)} - {_h(topic)}法律意见</title>
<style>
  :root {{ color-scheme: light; --ink: #243041; --muted: #607083; --line: #d7e0e8; --accent: #16697a; --accent-dark: #124b5c; --soft: #eef7f6; --warn: #fff7e8; --paper: #ffffff; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: #f4f7f9; color: var(--ink); font-family: Arial, "Microsoft YaHei", sans-serif; line-height: 1.74; }}
  main {{ max-width: 1040px; margin: 0 auto; padding: 28px 18px 56px; }}
  header {{ position: relative; overflow: hidden; background: var(--paper); border: 1px solid var(--line); border-top: 5px solid var(--accent); padding: 30px 34px; margin-bottom: 18px; }}
  header::after {{ content: ""; position: absolute; inset: 0 0 auto auto; width: 180px; height: 180px; border-radius: 999px; background: rgba(22,105,122,.07); transform: translate(55px,-80px); pointer-events: none; }}
  h1 {{ position: relative; margin: 0 0 10px; font-size: 27px; line-height: 1.38; color: #17364d; letter-spacing: .01em; }}
  h2 {{ margin: 0 0 15px; font-size: 20px; color: #17364d; border-bottom: 1px solid var(--line); padding-bottom: 10px; }}
  h3 {{ margin: 20px 0 8px; font-size: 16px; color: var(--accent-dark); }}
  section {{ background: var(--paper); border: 1px solid var(--line); padding: 25px 32px; margin-bottom: 16px; }}
  p {{ margin: 8px 0 12px; }}
  table {{ width: 100%; border-collapse: collapse; margin: 12px 0; font-size: 14px; }}
  th, td {{ border: 1px solid var(--line); padding: 9px 10px; vertical-align: top; }}
  th {{ background: #eef3f6; color: #17364d; text-align: left; }}
  ul, ol {{ padding-left: 22px; }}
  .doc-kicker {{ position: relative; margin-bottom: 8px; color: var(--accent-dark); font-size: 12px; font-weight: 700; letter-spacing: .16em; text-transform: uppercase; }}
  .doc-subtitle {{ position: relative; margin: 0 0 18px; color: var(--muted); font-size: 14px; }}
  .meta {{ position: relative; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px 14px; margin-top: 18px; }}
  .meta span {{ display: block; color: var(--muted); font-size: 12px; }}
  .meta strong {{ display: block; margin-top: 2px; color: var(--ink); font-size: 14px; font-weight: 650; word-break: break-word; }}
  .notice {{ position: relative; background: var(--warn); border-left: 4px solid #a66413; padding: 12px 14px; margin-top: 16px; color: #704214; }}
  .summary {{ background: var(--soft); border-left: 4px solid var(--accent); padding: 13px 15px; }}
  .issue-list {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin-top: 14px; }}
  .issue-card {{ border: 1px solid var(--line); background: #f8fbfc; padding: 13px 14px; }}
  .issue-card strong {{ display: block; color: #17364d; margin-bottom: 6px; }}
  .risk-table td:first-child {{ font-weight: 650; color: #17364d; }}
  .source-line p {{ margin: 0 0 10px; word-break: break-word; font-size: 12px; color: #526173; }}
  footer {{ margin-top: 22px; border: 1px solid var(--line); background: var(--paper); padding: 14px 18px; color: #697586; font-size: 12px; }}
  .footer-grid {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px 16px; margin-bottom: 8px; }}
  .footer-note {{ margin: 0; text-align: center; }}
  @media (max-width: 720px) {{ main {{ padding: 12px 8px 32px; }} header, section {{ padding: 20px 16px; }} .meta, .issue-list, .footer-grid {{ grid-template-columns: 1fr; }} table {{ display: block; overflow-x: auto; }} }}
</style>
</head>
<body>
<main>
<header>
  <div class="doc-kicker">SIQ Legal Compliance · Legal Opinion Working Draft</div>
  <h1>关于{_h(full_name)}{_h(topic)}之法律意见书</h1>
  <p class="doc-subtitle">面向管理层、董办/证券部及公司法务的合规审查工作底稿；采用事实前提、法规检索、风险判断和行动清单四段式口径。</p>
  <div class="meta">
    <div><span>文书编号</span><strong>{_h(document_no)}</strong></div>
    <div><span>出具日期</span><strong>{_h(issued_date)}</strong></div>
    <div><span>事项主体</span><strong>{_h(full_name)}（股票代码：{_h(stock_code or "未提供")}）</strong></div>
    <div><span>意见类型</span><strong>合规审查 / 风险初筛 / 法务工作底稿</strong></div>
    <div><span>管辖口径</span><strong>{_h(request.jurisdiction)}</strong></div>
    <div><span>公司目录</span><strong>{_h(_company_dir_name(company_dir))}</strong></div>
  </div>
  <div class="notice">本意见基于本机 Milvus 法律库 ic_legal_scanner 检索结果与用户提供事实形成，不构成最终法律意见，不替代执业律师判断。</div>
</header>

<section>
  <h2>一、事项摘要</h2>
  <p class="summary">基于现有事实和本次法规检索结果，初步倾向认为，本事项应先按上市公司合规事项进行审慎识别，再分别核对信息披露、公司治理程序、交易安排及后续监管沟通要求。当前结论以用户提供事实真实、完整且未发生重大变化为前提；如交易结构、关联关系、审批记录或公告时点存在差异，结论需进一步核实。</p>
  <p>从公司法务角度，管理层需要优先关注三件事：第一，是否存在应披露而未披露或披露不充分的事项；第二，内部决策程序、授权链条和留痕材料是否完整；第三，是否需要同步董办、证券部、财务及外部律师形成复核闭环。主要检索依据包括：{primary_sources}。</p>
  <div class="issue-list" aria-label="管理层关注事项">
    <div class="issue-card"><strong>披露判断</strong><span>以重大性、交易性质、关联关系及投资者决策影响为核心，形成可留痕的披露判断底稿。</span></div>
    <div class="issue-card"><strong>程序核验</strong><span>核对董事会/股东大会权限、回避表决、授权审批、用印和会议材料完整性。</span></div>
    <div class="issue-card"><strong>复核闭环</strong><span>由法务牵头，董办、财务、业务和外部律师按证据清单补齐材料后更新结论。</span></div>
  </div>
</section>

<section>
  <h2>二、事实背景</h2>
  <table>
    <tr><th>主体</th><td>{_h(full_name)}（{_h(stock_code or "股票代码未提供")}）</td></tr>
    <tr><th>事项</th><td>{_h(topic)}</td></tr>
    <tr><th>用户请求</th><td>{_h(request.prompt)}</td></tr>
    <tr><th>关联报告</th><td>{_h(report_note)}</td></tr>
    <tr><th>已检索材料</th><td>本机法律库 ic_legal_scanner，检索式：{_h(retrieval_query)}</td></tr>
    <tr><th>审查依据</th><td>用户事实陈述、当前公司上下文、法规/规则检索命中及后续可补充的公告、合同、决议和财务底稿。</td></tr>
    <tr><th>前提假设</th><td>用户提供信息真实、准确、完整，且截至出具日不存在未披露的交易结构变化、关联关系变化或监管沟通事项。</td></tr>
    <tr><th>尚待核实事项</th><td>交易文件、董事会或股东大会决议、关联方识别清单、公告草稿、财务影响测算、监管问询或处罚记录。</td></tr>
  </table>
  <p>本意见以下判断以现有事实为基础。若后续补充材料显示交易金额、交易对方、控制关系、审批权限或信息披露时点与目前描述不一致，应相应调整风险等级和建议动作。</p>
</section>

<section>
  <h2>三、适用法规与检索路径</h2>
  <table>
    <thead><tr><th>序号</th><th>法规/规则名称</th><th>source_path</th><th>chunk_index</th><th>本事项关联</th></tr></thead>
    <tbody>
{table_rows}
    </tbody>
  </table>
</section>

<section>
  <h2>四、法律分析</h2>
  <h3>4.1 信息披露与监管沟通</h3>
  <p>就上市公司二级市场场景而言，信息披露的核心不是简单判断事项是否“重大”，而是结合交易金额、交易性质、交易对方、是否涉及关联关系以及对投资者决策的影响进行综合判断。基于现有事实，建议先按审慎口径准备披露判断底稿，并由董办或证券部核对交易所规则、监管问询口径及历史公告一致性。相关依据参见{citation_refs}。</p>
  <h3>4.2 公司治理程序与内部控制</h3>
  <p>如事项涉及董事会、股东大会、关联董事回避、独立董事或审计委员会前置审查，公司应重点核查授权链条和会议材料是否完整。法务判断上，程序瑕疵往往会放大监管和交易执行风险；即使实体安排具备商业合理性，也建议补齐决议、审批、用印和信息披露留痕。</p>
  <h3>4.3 交易结构、责任边界与后续跟踪</h3>
  <p>若事项涉及关联交易、对外担保、资金占用、重大资产重组、股份减持或回购，应进一步核实交易结构是否触发专项规则。现阶段不宜作出“完全合规”或“必然违规”的结论；更稳妥的处理方式，是将法规适用、事实缺口和需管理层决策的事项分别列明，并在补充材料后出具更新意见。</p>
</section>

<section>
  <h2>五、风险提示</h2>
  <table class="risk-table">
    <thead><tr><th>风险维度</th><th>触发条件</th><th>可能后果</th><th>缓释动作</th></tr></thead>
    <tbody>
      <tr><td>监管风险</td><td>披露义务判断偏保守不足、公告时点滞后或关键事实遗漏</td><td>可能引发问询、监管关注、纪律处分或后续整改要求</td><td>董办先形成披露判断底稿，必要时与交易所或外部律师确认口径</td></tr>
      <tr><td>治理风险</td><td>内部审批权限、关联方回避或会议记录不完整</td><td>可能影响交易程序效力和管理层勤勉履职评价</td><td>补齐章程、议事规则、决议、授权、用印和会议资料链条</td></tr>
      <tr><td>交易风险</td><td>合同条件、付款安排、估值基础或业绩承诺与披露口径不一致</td><td>可能带来交易执行争议、投资者关系压力或后续更正披露</td><td>由业务、财务、法务共同核对交易文件与公告草稿一致性</td></tr>
      <tr><td>检索局限</td><td>本机法律库、当前检索式或材料范围未覆盖最新口径</td><td>可能遗漏地方规则、窗口指导、监管案例或公司章程特别约定</td><td>补充最新交易所规则、监管案例、公司章程及具体交易文件复核</td></tr>
    </tbody>
  </table>
</section>

<section>
  <h2>六、结论与建议</h2>
  <ol>
    <li><strong>初步结论：</strong>基于目前材料，本事项宜按需披露、需留痕、需复核的审慎路径推进。最终结论需以交易文件、决策程序和监管规则核验结果为准。</li>
    <li><strong>立即措施：</strong>建议由法务牵头建立事项清单，董办核对披露口径，财务补充金额和影响测算，业务部门确认交易背景与商业合理性。</li>
    <li><strong>待核实事项：</strong>补充交易对方及关联关系、审批权限、董事/股东回避安排、公告草稿、历史同类事项披露口径。</li>
    <li><strong>外部复核：</strong>如事项金额较大、市场敏感或存在监管问询可能，建议提交外部律师和中介机构复核。</li>
    <li><strong>后续跟踪：</strong>建议纳入持续跟踪清单，关注公告披露、监管问询、交易进展、诉讼仲裁及整改完成情况。</li>
  </ol>
</section>

<section>
  <h2>七、引用来源</h2>
  <div class="source-line">
{citation_lines}
  </div>
</section>

<section>
  <h2>八、免责声明</h2>
  <ul>
    <li>本意见基于本机 Milvus 法律库 ic_legal_scanner 在出具日之前的检索结果，可能未覆盖最新法规修订、地方规则或交易所窗口指导。</li>
    <li>本意见为风险初筛与合规辅助，不替代执业律师与监管机构的正式认定。</li>
    <li>本意见不得作为诉讼、仲裁、行政程序或信息披露文件的最终依据。</li>
    <li>公司在采取实际行动前，应结合完整事实材料咨询具有相应执业资格的律师。</li>
  </ul>
</section>

<footer>
  <div class="footer-grid">
    <span>出具主体：SIQ 法务合规智能体</span>
    <span>文书编号：{_h(document_no)}</span>
    <span>生成时间：{_h(now)}</span>
  </div>
  <p class="footer-note">本文件为内部合规辅助工作底稿；对外提交、公告引用或诉讼/仲裁使用前，应由具备执业资格的律师结合完整事实材料复核。</p>
</footer>
</main>
</body>
</html>
"""


def _annual_fact_table_rows(facts: list[dict[str, Any]]) -> str:
    rows: list[str] = []
    for fact in facts:
        locator_parts = []
        if fact.get("pdf_page") not in (None, ""):
            locator_parts.append(f"PDF 解析页 {fact['pdf_page']}")
        if fact.get("md_line") not in (None, ""):
            locator_parts.append(f"Markdown 第 {fact['md_line']} 行")
        rows.append(
            "<tr>"
            f"<td>{_h(fact.get('title'))}</td>"
            f"<td>{_h(fact.get('quote'))}</td>"
            f"<td>{_h('；'.join(locator_parts) or '源文件行级定位')}</td>"
            "<td><span class=\"status supported\">已定位</span></td>"
            "</tr>"
        )
    return "\n".join(rows)


def _annual_metric_table_rows(metrics: list[dict[str, Any]]) -> str:
    rows: list[str] = []
    for metric in metrics:
        rows.append(
            "<tr>"
            f"<td>{_h(metric.get('name'))}</td>"
            f"<td>{_h(metric.get('current_display'))}</td>"
            f"<td>{_h(metric.get('prior_display'))}</td>"
            f"<td>{_h(metric.get('yoy_display'))}</td>"
            f"<td>{_h(metric.get('table_index') or metric.get('md_line') or '')}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def _fact_text(fact_map: Mapping[str, dict[str, Any]], *keys: str) -> str:
    values = [str(fact_map[key].get("quote") or "") for key in keys if key in fact_map]
    if values:
        return " ".join(values)
    return "本次结构化抽取未在源年报中定位到该维度的明确披露，需由法务、董办结合完整章节和公告材料复核。"


def _law_refs(citations: list[dict[str, Any]], *terms: str) -> str:
    matches = []
    for index, citation in enumerate(citations, start=1):
        searchable = " ".join(
            str(citation.get(key) or "")
            for key in ("source", "quote", "relevance", "retrieval_topic")
        )
        if any(term in searchable for term in terms):
            matches.append(f"[{index}]")
    if not matches:
        matches = [f"[{index}]" for index in range(1, min(3, len(citations)) + 1)]
    return "".join(matches)


def _build_legal_opinion_html(
    *,
    company: dict[str, str],
    company_dir: Path,
    request: LegalWorkflowRequest,
    legal_citations: list[dict[str, Any]],
    retrieval_query: str,
    annual_bundle: AnnualReportBundle | None = None,
) -> str:
    if annual_bundle is None:
        return _build_generic_legal_opinion_html(
            company=company,
            company_dir=company_dir,
            request=request,
            citations=legal_citations,
            retrieval_query=retrieval_query,
        )

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    issued_date = now.split()[0]
    stock_code = company.get("stock_code") or company.get("company_id") or ""
    company_name = company.get("company_short_name") or company.get("company_full_name") or stock_code
    full_name = company.get("company_full_name") or company_name
    subject = f"{stock_code}-{company_name}" if stock_code and stock_code != company_name else company_name
    report_label = f"{annual_bundle.report_year or ''} 年年度报告".strip()
    document_no = f"SIQ-LGL-AR-{annual_bundle.report_year or 'NA'}-{_safe_filename_part(stock_code or company_name, 'company')}-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    fact_map = {str(item.get("fact_key") or ""): item for item in annual_bundle.facts}
    report_citations = [*annual_bundle.facts, *annual_bundle.metrics]
    all_citations = [*legal_citations, *report_citations]
    legal_table_rows = _build_citation_table_rows(legal_citations)
    fact_table_rows = _annual_fact_table_rows(annual_bundle.facts)
    metric_table_rows = _annual_metric_table_rows(annual_bundle.metrics)
    citation_lines = _build_citation_lines(all_citations)
    covered_keys = set(fact_map)
    missing_labels = [
        title
        for key, title, _patterns in ANNUAL_REPORT_FACT_SPECS[:9]
        if key not in covered_keys
    ]
    missing_text = "、".join(missing_labels) if missing_labels else "无核心维度缺口"
    metric_section = (
        f"""<table>
          <thead><tr><th>指标</th><th>{annual_bundle.report_year}</th><th>{(annual_bundle.report_year or 1) - 1}</th><th>同比变动</th><th>表/行定位</th></tr></thead>
          <tbody>{metric_table_rows}</tbody>
        </table>"""
        if annual_bundle.metrics
        else "<p class=\"gap\">未发现与该报告身份绑定的结构化关键指标文件；本意见不据此推导财务趋势，相关数字仅按源年报原文列示。</p>"
    )
    audit_refs = _law_refs(legal_citations, "年度报告", "定期报告", "信息披露")
    governance_refs = _law_refs(legal_citations, "公司治理", "董事", "审计委员会")
    related_refs = _law_refs(legal_citations, "关联交易")
    guarantee_refs = _law_refs(legal_citations, "担保", "资金占用")
    control_refs = _law_refs(legal_citations, "内部控制", "审计")
    enforcement_refs = _law_refs(legal_citations, "诉讼", "仲裁", "处罚", "监管")

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_h(subject)} - {_h(report_label)}法律审查意见</title>
<style>
  :root {{ color-scheme: light; --ink: #243041; --muted: #607083; --line: #d7e0e8; --accent: #16697a; --accent-dark: #124b5c; --soft: #eef7f6; --warn: #fff7e8; --paper: #ffffff; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: #f4f7f9; color: var(--ink); font-family: Arial, "Microsoft YaHei", sans-serif; line-height: 1.72; }}
  main {{ max-width: 1080px; margin: 0 auto; padding: 28px 18px 56px; }}
  header, section {{ background: var(--paper); border: 1px solid var(--line); padding: 26px 32px; margin-bottom: 16px; }}
  header {{ position: relative; overflow: hidden; border-top: 5px solid var(--accent); padding: 30px 34px; }}
  header::after {{ content: ""; position: absolute; inset: 0 0 auto auto; width: 190px; height: 190px; border-radius: 999px; background: rgba(22,105,122,.07); transform: translate(60px,-86px); pointer-events: none; }}
  h1 {{ position: relative; margin: 0 0 12px; color: #17364d; font-size: 27px; line-height: 1.4; letter-spacing: .01em; }}
  h2 {{ margin: 0 0 16px; color: #17364d; font-size: 20px; border-bottom: 1px solid var(--line); padding-bottom: 10px; }}
  h3 {{ margin: 22px 0 9px; color: var(--accent-dark); font-size: 16px; }}
  p {{ margin: 8px 0 12px; }}
  table {{ width: 100%; border-collapse: collapse; margin: 12px 0; font-size: 13px; }}
  th, td {{ border: 1px solid var(--line); padding: 9px 10px; text-align: left; vertical-align: top; }}
  th {{ background: #eef3f6; color: #17364d; }}
  ul, ol {{ padding-left: 22px; }}
  .doc-kicker {{ position: relative; margin-bottom: 8px; color: var(--accent-dark); font-size: 12px; font-weight: 700; letter-spacing: .16em; text-transform: uppercase; }}
  .doc-subtitle {{ position: relative; margin: 0 0 18px; color: var(--muted); font-size: 14px; }}
  .meta {{ position: relative; display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px 14px; margin-top: 18px; }}
  .meta span {{ display: block; color: var(--muted); font-size: 12px; }}
  .meta strong {{ display: block; margin-top: 2px; color: var(--ink); font-size: 14px; font-weight: 650; word-break: break-word; }}
  .notice {{ position: relative; margin-top: 16px; padding: 12px 14px; background: var(--warn); border-left: 4px solid #a66413; }}
  .summary {{ padding: 13px 15px; background: var(--soft); border-left: 4px solid #168071; }}
  .issue-list {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin-top: 14px; }}
  .issue-card {{ border: 1px solid var(--line); background: #f8fbfc; padding: 13px 14px; }}
  .issue-card strong {{ display: block; color: #17364d; margin-bottom: 6px; }}
  .fact {{ padding: 10px 12px; background: #f7fafb; border-left: 3px solid #7aa8b2; color: #344657; }}
  .gap {{ padding: 10px 12px; background: var(--warn); border-left: 3px solid #c47a16; }}
  .status {{ display: inline-block; padding: 1px 7px; border: 1px solid #9ac5bb; color: #176456; font-size: 12px; }}
  .source-line p {{ margin: 0 0 10px; word-break: break-word; font-size: 12px; color: #526173; }}
  footer {{ margin-top: 22px; border: 1px solid var(--line); background: var(--paper); padding: 14px 18px; color: #697586; font-size: 12px; }}
  .footer-grid {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px 16px; margin-bottom: 8px; }}
  .footer-note {{ margin: 0; text-align: center; }}
  @media (max-width: 720px) {{ main {{ padding: 12px 8px 32px; }} header, section {{ padding: 19px 16px; }} .meta, .issue-list, .footer-grid {{ grid-template-columns: 1fr; }} table {{ display: block; overflow-x: auto; }} }}
</style>
</head>
<body>
<main>
<header>
  <div class="doc-kicker">SIQ Legal Compliance · Annual Report Legal Review</div>
  <h1>关于{_h(full_name)}{_h(report_label)}之法律审查意见书</h1>
  <p class="doc-subtitle">面向管理层、董办/证券部、审计委员会及公司法务的年报法律审查工作底稿；以源年报事实定位、法规检索和证据缺口清单为基础。</p>
  <div class="meta">
    <div><span>文书编号</span><strong>{_h(document_no)}</strong></div>
    <div><span>出具日期</span><strong>{_h(issued_date)}</strong></div>
    <div><span>审查主体</span><strong>{_h(full_name)}（{_h(stock_code or "未提供")}）</strong></div>
    <div><span>意见类型</span><strong>年度报告法律审查 / 合规风险初筛</strong></div>
    <div><span>报告身份</span><strong>{_h(annual_bundle.report_id)}</strong></div>
    <div><span>报告期末</span><strong>{_h(annual_bundle.period_end or "未载明")}</strong></div>
    <div><span>解析任务</span><strong>{_h(annual_bundle.task_id or "旧版报告包")}</strong></div>
    <div><span>管辖口径</span><strong>{_h(request.jurisdiction)}</strong></div>
    <div><span>证据覆盖</span><strong>{_h(len(annual_bundle.facts))} 项年报事实 / {_h(len(legal_citations))} 条法规依据</strong></div>
  </div>
  <div class="notice">本意见以公司权威年报包和本机法律库检索结果为基础，属于法务风险初筛，不构成最终法律意见，不替代执业律师判断。</div>
</header>

<section>
  <h2>一、事项摘要</h2>
  <p class="summary">本次审查已绑定 {_h(report_label)}（report_id={_h(annual_bundle.report_id)}），共定位 {_h(len(annual_bundle.facts))} 项公司特定事实、{_h(len(annual_bundle.metrics))} 项结构化财务指标，并保留 {_h(len(legal_citations))} 条经相关性过滤的法规依据。初步工作结论是：已披露项目可进入程序与一致性复核；未定位项目不得推定为“不存在”，应列入核查清单。</p>
  <h3>1.1 审查目标</h3>
  <p>审查年度报告在定期报告披露、公司治理、关联交易、担保与资金占用、内部控制、诉讼处罚及关键财务数据方面是否具备可追溯事实基础，并识别仍需公告、决议、合同或中介文件佐证的事项。</p>
  <h3>1.2 初步结论边界</h3>
  <p>本意见不以单一勾选项替代穿透核查，也不因年报未定位到某项披露即作出“完全合规”判断。最终结论需以公司章程、会议决议、关联方清单、担保台账、公告原文及监管最新口径复核结果为准。</p>
  <div class="issue-list" aria-label="年报法律审查口径">
    <div class="issue-card"><strong>披露一致性</strong><span>交叉核对年报、临时公告、问询回复、审计报告和期后事项，识别遗漏、更正和口径变化。</span></div>
    <div class="issue-card"><strong>治理程序</strong><span>重点复核董事会/股东大会、审计委员会、关联方回避、担保审批和内控整改证据链。</span></div>
    <div class="issue-card"><strong>证据缺口</strong><span>未定位事实不推定为不存在，应形成责任部门、补充文件和完成时限明确的复核台账。</span></div>
  </div>
</section>

<section>
  <h2>二、事实背景与审查范围</h2>
  <table>
    <tr><th>源报告</th><td>{_h(_relative(annual_bundle.report_path))}</td></tr>
    <tr><th>用户事项</th><td>{_h(request.prompt or request.topic)}</td></tr>
    <tr><th>事实覆盖</th><td>{_h(len(annual_bundle.facts))}/{_h(len(ANNUAL_REPORT_FACT_SPECS[:9]))} 个核心法律审查维度已定位</td></tr>
    <tr><th>尚未定位</th><td>{_h(missing_text)}</td></tr>
    <tr><th>审查文件</th><td>源年报正文、结构化事实包、关键财务指标、法规检索结果及后续可补充的公告、决议、合同、台账、函证和监管沟通记录。</td></tr>
    <tr><th>工作假设</th><td>源年报为公司正式披露或权威解析版本，结构化抽取与行级定位未被人为修改；未取得原件的事项均按待核实处理。</td></tr>
    <tr><th>审查限制</th><td>仅对源年报已披露事实作初步法律评价；未取得会议原件、合同、台账和监管沟通记录。</td></tr>
  </table>
  <h3>2.1 年报事实与定位</h3>
  <table>
    <thead><tr><th>审查维度</th><th>源年报披露摘要</th><th>定位</th><th>状态</th></tr></thead>
    <tbody>{fact_table_rows}</tbody>
  </table>
  <h3>2.2 关键财务指标核验</h3>
  {metric_section}
</section>

<section>
  <h2>三、适用法规与检索路径</h2>
  <p>法规检索按定期报告、治理、关联交易、担保与资金占用、内控审计、诉讼处罚六个主题分别执行，并过滤与上市公司年报审查无直接关系的基金法及异地营商环境规则。综合检索式：{_h(retrieval_query)}</p>
  <table>
    <thead><tr><th>序号</th><th>法规/规则</th><th>source_path</th><th>chunk</th><th>适用关联</th></tr></thead>
    <tbody>{legal_table_rows}</tbody>
  </table>
</section>

<section>
  <h2>四、法律分析</h2>
  <h3>4.1 年度报告、审计意见与责任声明</h3>
  <p class="fact">{_h(_fact_text(fact_map, 'audit_opinion'))}</p>
  <p>应进一步核对审计报告正文、关键审计事项、管理层责任声明与年报财务数据是否一致。标准无保留意见本身不等同于所有披露事项均无合规风险，定期报告仍须满足真实、准确、完整和及时要求。适用依据见 {audit_refs}。</p>
  <h3>4.2 定期报告与信息披露</h3>
  <p class="fact">{_h(_fact_text(fact_map, 'information_disclosure'))}</p>
  <p>建议将年报重大事项与报告期内临时公告、交易所问询回复逐项交叉核验，重点识别更正、遗漏、口径变化和披露时点差异。历史信息披露评价可作为管理线索，但不能替代本年度逐项审查。适用依据见 {audit_refs}。</p>
  <h3>4.3 公司治理、董事会与审计委员会</h3>
  <p class="fact">{_h(_fact_text(fact_map, 'corporate_governance'))}</p>
  <p>治理审查应覆盖董事会构成、独立董事比例、审计委员会履职、取消监事会后的监督职能承接，以及关联事项回避表决。需核对章程修订生效时间与会议记录，避免仅凭年报概述推定程序完备。适用依据见 {governance_refs}。</p>
  <h3>4.4 关联交易及审议披露</h3>
  <p class="fact">{_h(_fact_text(fact_map, 'related_party_transactions'))}</p>
  <p>应将预计额度、实际发生额、定价原则、关联方识别和审议层级进行勾稽，并核对关联董事回避、独立董事或审计委员会前置审查及公告一致性。额度内交易仍需满足公允定价和持续披露要求。适用依据见 {related_refs}。</p>
  <h3>4.5 非经营性资金占用与对外担保</h3>
  <p class="fact">{_h(_fact_text(fact_map, 'fund_occupation', 'external_guarantee'))}</p>
  <p>年报勾选“不适用”或披露余额为零时，仍建议以银行流水、往来科目、担保合同、董事会及股东会决议和对外担保台账作反向核验；对子公司担保、存续担保和报告期后事项需单独确认。适用依据见 {guarantee_refs}。</p>
  <h3>4.6 内部控制评价与内部控制审计</h3>
  <p class="fact">{_h(_fact_text(fact_map, 'internal_control'))}</p>
  <p>内控意见应与年报披露的缺陷认定、整改情况和财务报表审计发现交叉核对。即使意见为标准无保留，仍需关注重大业务流程、信息系统、关联交易和资金管理是否存在一般缺陷或持续改进事项。适用依据见 {control_refs}。</p>
  <h3>4.7 重大诉讼、仲裁、处罚及整改</h3>
  <p class="fact">{_h(_fact_text(fact_map, 'litigation', 'regulatory_penalty'))}</p>
  <p>建议与法院公开信息、监管处罚与纪律处分记录、子公司重大案件清单及期后事项进行复核。源年报未披露重大事项只能支持“按报告披露未见”，不能替代外部检索或完整法律函证。适用依据见 {enforcement_refs}。</p>
  <h3>4.8 财务报告重点复核</h3>
  <p class="fact">{_h(_fact_text(fact_map, 'financial_reporting'))}</p>
  <p>结构化指标仅用于识别复核重点。营业收入、归母净利润、经营现金流、总资产和净资产的变动应与审计报告、会计政策、减值、非经常性损益及现金流附注相互印证；不得仅依据同比幅度作出违法违规判断。</p>
</section>

<section>
  <h2>五、风险提示</h2>
  <table>
    <thead><tr><th>风险维度</th><th>当前证据状态</th><th>触发条件</th><th>建议控制动作</th></tr></thead>
    <tbody>
      <tr><td>定期报告披露</td><td>{_h('已定位' if 'information_disclosure' in fact_map else '需复核')}</td><td>年报与临时公告、审计报告或问询回复不一致</td><td>董办建立披露事项勾稽表并留存复核记录</td></tr>
      <tr><td>治理程序</td><td>{_h('已定位' if 'corporate_governance' in fact_map else '需复核')}</td><td>委员会职责承接、回避表决或授权链条不完整</td><td>法务核验章程、议事规则及会议原件</td></tr>
      <tr><td>关联交易</td><td>{_h('已定位' if 'related_party_transactions' in fact_map else '需复核')}</td><td>实际金额超预计、关联方遗漏或定价依据不足</td><td>财务与法务联合核对关联方及额度台账</td></tr>
      <tr><td>担保与资金占用</td><td>{_h('已定位' if {'external_guarantee', 'fund_occupation'} & covered_keys else '需复核')}</td><td>账外担保、期后事项或往来科目异常</td><td>反向核验合同、流水、决议及函证</td></tr>
      <tr><td>内控与审计</td><td>{_h('已定位' if 'internal_control' in fact_map else '需复核')}</td><td>内控评价与审计发现、整改记录不一致</td><td>审计委员会取得缺陷清单及整改闭环证据</td></tr>
      <tr><td>诉讼与处罚</td><td>{_h('已定位' if {'litigation', 'regulatory_penalty'} & covered_keys else '需复核')}</td><td>子公司案件、外部公开记录或期后事项遗漏</td><td>开展外部检索并取得法律函证</td></tr>
    </tbody>
  </table>
</section>

<section>
  <h2>六、结论与建议</h2>
  <h3>6.1 初步结论</h3>
  <p>基于现有事实，源年报已提供若干可追溯的治理、披露和财务审查线索，但本意见不据此作出“全面合规”的保证。已定位事实应进入程序、金额和跨文件一致性复核；尚未定位维度应作为证据缺口处理。</p>
  <h3>6.2 立即复核清单</h3>
  <ol>
    <li>董办：核对年报、四期定期报告、临时公告和交易所问询回复的一致性。</li>
    <li>法务：核对章程、董事会及股东会决议、回避表决、关联交易和担保审批程序。</li>
    <li>财务：核对关联方清单、实际发生额、资金往来、担保台账及关键指标底稿。</li>
    <li>审计委员会：取得内控缺陷、整改记录、内控审计报告和关键审计事项沟通材料。</li>
    <li>外部律师：对重大、敏感或存在监管沟通的事项进行专项复核并更新结论。</li>
  </ol>
  <h3>6.3 持续跟踪</h3>
  <p>建议将缺口项目、责任部门、证据文件、完成时间和复核人纳入闭环台账；出现监管问询、重大诉讼、担保变化或报告更正时，应重新执行本审查。</p>
</section>

<section>
  <h2>七、引用来源</h2>
  <p>以下来源分为法规依据和公司年报事实。法规用于判断适用规则，年报事实用于证明公司特定披露，两类证据不得相互替代。</p>
  <div class="source-line">{citation_lines}</div>
</section>

<section>
  <h2>八、免责声明</h2>
  <ul>
    <li>本意见基于出具时可获得的源年报、结构化指标与本机法律库检索结果，可能未覆盖最新法规修订、监管窗口指导及未公开事实。</li>
    <li>本意见为风险初筛与合规辅助，不替代执业律师、会计师和监管机构的正式认定。</li>
    <li>本意见不得直接作为诉讼、仲裁、行政程序或公开信息披露文件的最终依据。</li>
    <li>公司采取实际行动前，应结合完整原始材料完成内部复核，并在必要时取得外部专业意见。</li>
  </ul>
</section>

<footer>
  <div class="footer-grid">
    <span>出具主体：SIQ 法务合规智能体</span>
    <span>文书编号：{_h(document_no)}</span>
    <span>生成时间：{_h(now)}</span>
  </div>
  <p class="footer-note">本文件为内部合规辅助工作底稿；对外提交、公告引用或诉讼/仲裁使用前，应由具备执业资格的律师结合完整年报、公告和原始决议文件复核。</p>
</footer>
</main>
</body>
</html>
"""


def _validate_legal_artifact(path: Path, validation_path: Path, *, timeout: int | float = 120) -> dict[str, Any]:
    script = _validator_script()
    if not script.is_file():
        return {"ok": False, "failures": ["validator_missing"], "warnings": []}
    completed = run_command(
        [sys.executable, str(script), str(path), "--write-json", str(validation_path)],
        cwd=PROJECT_ROOT,
        timeout=timeout,
    )
    payload = _load_stdout_json(completed)
    if not payload and validation_path.exists():
        try:
            payload = json.loads(validation_path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
    if not payload:
        payload = {
            "ok": False,
            "failures": [f"validator_returncode:{completed.returncode}"],
            "warnings": [],
            "stdout": (completed.stdout or "").strip()[-1000:],
            "stderr": (completed.stderr or "").strip()[-1000:],
        }
    return payload


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_manifest(
    *,
    html_path: Path,
    retrieval_path: Path,
    validation_path: Path,
    company: dict[str, str],
    company_dir: Path,
    request: LegalWorkflowRequest,
    citations: list[dict[str, Any]],
    validation: dict[str, Any],
    annual_bundle: AnnualReportBundle | None = None,
) -> tuple[Path, Path]:
    legal_citation_count = sum(1 for item in citations if item.get("source_type") == "legal_corpus")
    report_fact_count = sum(1 for item in citations if item.get("source_type") == "annual_report")
    metric_count = sum(1 for item in citations if item.get("source_type") == "annual_report_metric")
    manifest = {
        "artifact_type": "legal_opinion_html",
        "company_code": company.get("stock_code") or company.get("company_id") or "",
        "company_name": company.get("company_short_name") or company.get("company_full_name") or "",
        "company_full_name": company.get("company_full_name") or "",
        "company_dir": _company_dir_name(company_dir),
        "subject": request.topic,
        "topic": request.topic,
        "jurisdiction": request.jurisdiction,
        "source_report": str(request.report_path) if request.report_path else "",
        "prompt": request.prompt,
        "html_path": str(html_path),
        "html_url": _wiki_legal_url(html_path),
        "retrieval_path": str(retrieval_path),
        "validation_path": str(validation_path),
        "validation": validation,
        "citation_count": len(citations),
        "legal_citation_count": legal_citation_count,
        "annual_report_fact_count": report_fact_count,
        "annual_report_metric_count": metric_count,
        "report_identity": (
            {
                "report_id": annual_bundle.report_id,
                "report_year": annual_bundle.report_year,
                "period_end": annual_bundle.period_end,
                "task_id": annual_bundle.task_id,
            }
            if annual_bundle is not None
            else None
        ),
        "citations": [dict(item) for item in citations],
        "created_at": datetime.now().isoformat(),
    }
    manifest_path = html_path.with_suffix(".manifest.json")
    latest_manifest_path = html_path.parent / "legal_manifest.json"
    _write_json(manifest_path, manifest)
    _write_json(latest_manifest_path, {**manifest, "manifest_path": str(manifest_path)})
    return manifest_path, latest_manifest_path


def format_legal_workflow_reply(result: dict[str, Any]) -> str:
    ok = bool(result.get("ok"))
    title = "已生成正式法务合规 HTML 意见书" if ok else "法务合规 HTML 意见书生成未完成"
    html_path = str(result.get("html_path") or "")
    html_url = _wiki_legal_url(html_path)
    validation = result.get("validation_result")
    if not isinstance(validation, dict):
        validation = result.get("validation") if isinstance(result.get("validation"), dict) else {}
    validation_status = "通过" if validation.get("ok") else f"需复核，failures={len(validation.get('failures') or [])}"

    lines = [
        f"**{title}**",
        "",
        f"- 公司请求: `{result.get('company_query') or ''}`",
        f"- 公司: `{result.get('stock_code') or ''}-{result.get('company_name') or ''}`",
        f"- 事项: `{result.get('topic') or ''}`",
        f"- 工作流状态: `{result.get('stage') or 'unknown'}`",
        f"- 法规引用: `{result.get('citation_count') or 0}` 条",
        f"- 质量校验: `{validation_status}`",
    ]
    if result.get("report_id"):
        lines.append(f"- 源年报: `{result.get('report_id')}`")
    if result.get("annual_report_fact_count"):
        lines.append(f"- 年报事实: `{result.get('annual_report_fact_count')}` 项")
    if html_url:
        lines.append(f"- 打开意见书: [HTML 法律意见书]({html_url})")
    if html_path:
        lines.append(f"- HTML: `{_relative(html_path)}`")
    for key, label in (
        ("manifest_path", "Manifest"),
        ("retrieval_path", "法规检索记录"),
        ("validation_path", "校验记录"),
    ):
        if result.get(key):
            lines.append(f"- {label}: `{_relative(result.get(key))}`")
    next_action = str(result.get("next_action") or "").strip()
    if next_action and not ok:
        lines.extend(["", f"下一步: {next_action}"])
    return "\n".join(lines)


def _failure_response(stage: str, request: LegalWorkflowRequest, **extra: Any) -> LegalWorkflowResponse:
    result = {
        "ok": False,
        "stage": stage,
        "company_query": request.company_query,
        "topic": request.topic,
        **extra,
    }
    return LegalWorkflowResponse(True, format_legal_workflow_reply(result), result)


def run_legal_workflow(
    request: LegalWorkflowRequest,
    *,
    timeout: int | float = DEFAULT_TIMEOUT_SECONDS,
) -> LegalWorkflowResponse:
    company = _resolve_company(request.company_query)
    if company is None:
        return _failure_response(
            "company_resolve_failed",
            request,
            next_action="请在当前页面选择公司，或在消息中提供唯一股票代码/company_id。",
        )

    company_dir = _company_dir(company)
    stock_code = company.get("stock_code") or company.get("company_id") or request.company_query
    company_name = company.get("company_short_name") or company.get("company_full_name") or stock_code
    annual_report = _is_annual_report_request(request)
    annual_bundle: AnnualReportBundle | None = None
    if annual_report:
        report_path = _resolve_annual_report_path(company_dir, request)
        if report_path is None:
            return _failure_response(
                "source_report_not_found",
                request,
                stock_code=stock_code,
                company_name=company_name,
                next_action="未找到与当前公司及请求年度匹配的已解析年报，已停止生成，避免把历史法律意见或其他报告误作源年报。",
            )
        request = replace(request, report_path=report_path)
        annual_bundle = _load_annual_report_bundle(company_dir, report_path)
        if len(annual_bundle.facts) < MIN_ANNUAL_REPORT_FACTS:
            return _failure_response(
                "insufficient_annual_report_facts",
                request,
                stock_code=stock_code,
                company_name=company_name,
                report_id=annual_bundle.report_id,
                annual_report_fact_count=len(annual_bundle.facts),
                next_action=f"源年报仅定位到 {len(annual_bundle.facts)} 个核心法律审查维度，未达到 {MIN_ANNUAL_REPORT_FACTS} 项发布门槛。请先检查年报解析完整性。",
            )

    retrieval_payload, completed = _retrieve_legal_source_set(request, company, timeout=timeout)
    query = str(retrieval_payload.get("query") or _retrieval_query(request, company))

    if not retrieval_payload.get("ok"):
        return _failure_response(
            str(retrieval_payload.get("stage") or "legal_retrieval_failed"),
            request,
            stock_code=stock_code,
            company_name=company_name,
            returncode=getattr(completed, "returncode", None),
            stdout=(getattr(completed, "stdout", "") or "").strip()[-2000:],
            stderr=(getattr(completed, "stderr", "") or "").strip()[-2000:],
            next_action="请确认 Milvus 法律库、Docker/Attu 容器和 legal_milvus_cli.py 可用；普通问答仍可继续走对话模式。",
        )

    legal_citations = _normalize_citations(
        retrieval_payload.get("results") or [],
        annual_report=annual_report,
    )
    if len(legal_citations) < MIN_CITATIONS:
        return _failure_response(
            "insufficient_legal_citations",
            request,
            stock_code=stock_code,
            company_name=company_name,
            citation_count=len(legal_citations),
            filtered_result_count=len(retrieval_payload.get("results") or []) - len(legal_citations),
            next_action="经主题相关性过滤后，可引用法规不足 3 条。已停止发布，避免将基金法、异地条例等低相关材料写入法律意见。",
        )

    report_citations = [*(annual_bundle.facts if annual_bundle else []), *(annual_bundle.metrics if annual_bundle else [])]
    citations = [*legal_citations, *report_citations]

    html_path = _default_output_path(company_dir, request.topic, request.allow_overwrite)
    draft_dir = html_path.parent / "_drafts"
    draft_path = draft_dir / html_path.name
    validation_path = draft_path.with_suffix(".validation.json")
    draft_dir.mkdir(parents=True, exist_ok=True)

    html_text = _build_legal_opinion_html(
        company=company,
        company_dir=company_dir,
        request=request,
        legal_citations=legal_citations,
        retrieval_query=query,
        annual_bundle=annual_bundle,
    )
    draft_path.write_text(html_text, encoding="utf-8")
    validation = _validate_legal_artifact(draft_path, validation_path)

    if not validation.get("ok"):
        result = {
            "ok": False,
            "stage": "validation_failed",
            "company_query": request.company_query,
            "stock_code": stock_code,
            "company_name": company_name,
            "topic": request.topic,
            "citation_count": len(legal_citations),
            "annual_report_fact_count": len(annual_bundle.facts) if annual_bundle else 0,
            "draft_path": str(draft_path),
            "validation_path": str(validation_path),
            "validation": validation,
            "next_action": "草稿未通过法务意见质量门禁，已留在 _drafts 目录，未发布到公司 legal/ 列表。",
        }
        return LegalWorkflowResponse(True, format_legal_workflow_reply(result), result)

    expected_metric_path = (
        company_dir / "metrics" / "reports" / annual_bundle.report_id / "key_metrics.json"
        if annual_bundle is not None
        else None
    )
    detailed_annual_terms = (
        "定期报告与信息披露",
        "公司治理、董事会与审计委员会",
        "关联交易及审议披露",
        "非经营性资金占用与对外担保",
        "内部控制评价与内部控制审计",
        "重大诉讼、仲裁、处罚及整改",
        "财务报告重点复核",
    )
    annual_legal_topics = {str(item.get("retrieval_topic") or "") for item in legal_citations}
    expected_annual_legal_topics = {label for label, _query in ANNUAL_RETRIEVAL_TOPICS}
    contract_checks = {
        "quality_validator_passed": validation.get("ok") is True,
        "html_present": draft_path.exists(),
        "minimum_citations_met": len(legal_citations) >= MIN_CITATIONS,
        "citations_traceable": bool(citations) and all(citation_has_locator(item) for item in citations),
        "conditional_language_present": "不替代执业律师" in html_text and "最终结论需" in html_text,
        "annual_source_report_bound": not annual_report
        or (
            annual_bundle is not None
            and annual_bundle.report_path.is_file()
            and _path_is_within(annual_bundle.report_path, company_dir / "reports")
        ),
        "annual_report_identity_bound": not annual_report
        or bool(annual_bundle and annual_bundle.report_id and annual_bundle.report_year),
        "annual_report_fact_coverage": not annual_report
        or bool(annual_bundle and len(annual_bundle.facts) >= MIN_ANNUAL_REPORT_FACTS),
        "annual_report_facts_traceable": not annual_report
        or bool(annual_bundle and all(citation_has_locator(item) for item in annual_bundle.facts)),
        "annual_legal_topic_coverage": not annual_report
        or expected_annual_legal_topics.issubset(annual_legal_topics),
        "annual_metrics_verified": not annual_report
        or expected_metric_path is None
        or not expected_metric_path.is_file()
        or bool(annual_bundle and len(annual_bundle.metrics) >= 3),
        "detailed_annual_review_present": not annual_report
        or all(term in html_text for term in detailed_annual_terms),
    }
    contract_failures = [name for name, passed in contract_checks.items() if not passed]
    contract_validation = SpecialistArtifactValidation(
        ok=not contract_failures,
        checks=contract_checks,
        failures=contract_failures,
        warnings=list(validation.get("warnings") or []),
    )
    report_identity = (
        {
            "market": "CN",
            "company_id": company.get("company_id") or stock_code,
            "filing_id": f"CN:{company.get('company_id') or stock_code}:{annual_bundle.report_id}",
            "parse_run_id": annual_bundle.task_id,
        }
        if annual_bundle is not None
        else None
    )
    resolved_period = (
        {
            "report_id": annual_bundle.report_id,
            "fiscal_year": annual_bundle.report_year,
            "period_end": annual_bundle.period_end,
        }
        if annual_bundle is not None
        else None
    )
    artifact_metadata = {
        "topic": request.topic,
        "jurisdiction": request.jurisdiction,
        "report_identity": report_identity,
        "annual_report_fact_count": len(annual_bundle.facts) if annual_bundle else 0,
        "annual_report_metric_count": len(annual_bundle.metrics) if annual_bundle else 0,
        "legal_citation_count": len(legal_citations),
    }
    audit_facts = {
        "resolved_period": resolved_period,
        "research_identity": report_identity,
        "wiki_facts": report_citations,
        "legal_facts": legal_citations,
    }
    if not contract_validation.ok:
        draft_retrieval_path = draft_path.with_suffix(".retrieval.json")
        _write_json(draft_retrieval_path, retrieval_payload)
        artifact = finalize_specialist_artifact(
            artifact_type="legal",
            company_id=company.get("company_id") or stock_code,
            source_report_path=str(request.report_path or draft_retrieval_path),
            output_path=str(draft_path),
            html_url="",
            citations=citations,
            validation_result=contract_validation,
            profile="siq_legal",
            message=request.prompt or request.topic,
            session_id=request.session_id,
            metadata=artifact_metadata,
            specialist_facts=audit_facts,
        )
        artifact_manifest_path = draft_path.with_suffix(".artifact.json")
        write_specialist_artifact_manifest(artifact, artifact_manifest_path)
        result = {
            "ok": False,
            "stage": "contract_validation_failed",
            "company_query": request.company_query,
            "stock_code": stock_code,
            "company_name": company_name,
            "topic": request.topic,
            "citation_count": len(legal_citations),
            "annual_report_fact_count": len(annual_bundle.facts) if annual_bundle else 0,
            "draft_path": str(draft_path),
            "retrieval_path": str(draft_retrieval_path),
            "validation_path": str(validation_path),
            "validation": validation,
            "artifact": artifact.model_dump(),
            "artifact_manifest_path": str(artifact_manifest_path),
            "audit_trace_id": artifact.audit_trace_id,
            "validation_result": contract_validation.model_dump(),
            "next_action": "草稿未通过统一 specialist artifact contract，已留在 _drafts 目录且未发布。",
        }
        return LegalWorkflowResponse(True, format_legal_workflow_reply(result), result)

    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(draft_path.read_text(encoding="utf-8"), encoding="utf-8")
    retrieval_path = html_path.with_suffix(".retrieval.json")
    published_validation_path = html_path.with_suffix(".validation.json")
    _write_json(retrieval_path, retrieval_payload)
    _write_json(published_validation_path, validation)
    manifest_path, latest_manifest_path = _write_manifest(
        html_path=html_path,
        retrieval_path=retrieval_path,
        validation_path=published_validation_path,
        company=company,
        company_dir=company_dir,
        request=request,
        citations=citations,
        validation=validation,
        annual_bundle=annual_bundle,
    )

    artifact = finalize_specialist_artifact(
        artifact_type="legal",
        company_id=company.get("company_id") or stock_code,
        source_report_path=str(request.report_path or retrieval_path),
        output_path=str(html_path),
        html_url=_wiki_legal_url(html_path),
        citations=citations,
        validation_result=contract_validation,
        profile="siq_legal",
        message=request.prompt or request.topic,
        session_id=request.session_id,
        metadata=artifact_metadata,
        specialist_facts=audit_facts,
    )
    artifact_manifest_path = html_path.with_suffix(".artifact.json")
    write_specialist_artifact_manifest(artifact, artifact_manifest_path)

    result = {
        "ok": True,
        "stage": "completed",
        "company_query": request.company_query,
        "stock_code": stock_code,
        "company_name": company_name,
        "company_path": str(company_dir),
        "topic": request.topic,
        "jurisdiction": request.jurisdiction,
        "citation_count": len(legal_citations),
        "annual_report_fact_count": len(annual_bundle.facts) if annual_bundle else 0,
        "annual_report_metric_count": len(annual_bundle.metrics) if annual_bundle else 0,
        "report_id": annual_bundle.report_id if annual_bundle else "",
        "report_year": annual_bundle.report_year if annual_bundle else None,
        "source_report_path": str(annual_bundle.report_path) if annual_bundle else str(request.report_path or ""),
        "html_path": str(html_path),
        "html_url": _wiki_legal_url(html_path),
        "manifest_path": str(manifest_path),
        "latest_manifest_path": str(latest_manifest_path),
        "retrieval_path": str(retrieval_path),
        "validation_path": str(published_validation_path),
        "validation": validation,
        "artifact": artifact.model_dump(),
        "artifact_manifest_path": str(artifact_manifest_path),
        "audit_trace_id": artifact.audit_trace_id,
        "validation_result": contract_validation.model_dump(),
        "finished_at": datetime.now().isoformat(),
    }
    return LegalWorkflowResponse(True, format_legal_workflow_reply(result), result)
