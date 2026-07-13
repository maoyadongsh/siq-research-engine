#!/usr/bin/env python3
"""Canonical citation resolver shared by all SIQ Hermes profiles.

This module is the single source of truth for citation resolution. It is used by:
- siq_analysis (repair_report_citations.py)
- siq_factchecker (factcheck_cli.py)
- siq_tracking modules (module1/module3 etc.)
- siq_assistant (chat post-processing)

It resolves company directories, task ids, table indexes, and PDF page numbers
from the local wiki without depending on PostgreSQL. PostgreSQL is treated as
an enhancement source by callers, not by this resolver.
"""

from __future__ import annotations

import json
import re
import argparse
import os
from pathlib import Path
from typing import Any


WIKI_BASE = Path(os.environ.get("SIQ_WIKI_ROOT", "/home/maoyd/siq-research-engine/data/wiki")).expanduser()
DEFAULT_SOURCE_TYPE = os.environ.get(
    "SIQ_DEFAULT_SOURCE_TYPE",
    "okf_metrics" if "okf_staging" in str(WIKI_BASE) else "wiki_metrics",
)
PUBLIC_ORIGIN = os.environ.get("SIQ_PUBLIC_ORIGIN", "https://arthurmao.synology.me:9391").rstrip("/")

METRIC_SOURCE_TYPES = {"wiki_metrics", "okf_metrics"}
EVIDENCE_SOURCE_TYPES = {"wiki_evidence", "wiki_index", "okf_evidence", "okf_index"}
SEMANTIC_SOURCE_TYPES = {"wiki_semantic", "semantic", "okf_semantic", "okf_semantic_evidence"}
DOCUMENT_LINK_SOURCE_TYPES = {"wiki_document_links", "document_links", "okf_document_links"}
ANALYSIS_SOURCE_TYPES = {"wiki_analysis", "okf_analysis"}
REPORT_MD_SOURCE_TYPES = {
    "report_md",
    "report_markdown",
    "wiki_report",
    "wiki_report_table",
    "okf_report",
    "okf_report_table",
}
TOOL_SOURCE_TYPES = {
    "financial_calculator",
    "financial_reconciliation_validator",
    "calculator",
    "reconciliation_validator",
}
TOOL_FILE_NAMES = ("financial_calculator.py", "financial_reconciliation_validator.py")

METRIC_ALIASES = {
    "商誉": {"商誉", "商誉账面价值", "商誉账面原值", "商誉减值准备", "商誉构成", "商誉明细", "goodwill"},
    "营业收入": {"营业收入", "operating_revenue"},
    "利润总额": {"利润总额", "total_profit"},
    "净利润": {"净利润", "net income", "net_profit", "net profit", "profit for the year", "income after taxes"},
    "归母净利润": {"归属于上市公司股东的净利润", "归母净利润", "parent_net_profit"},
    "扣非归母净利润": {
        "归属于上市公司股东的扣除非经常性损益的净利润",
        "扣非归母净利润",
        "deducted_parent_net_profit",
    },
    "经营现金流净额": {"经营活动产生的现金流量净额", "经营现金流净额", "operating_cash_flow_net"},
    "基本每股收益": {"基本每股收益", "basic_eps"},
    "扣非基本每股收益": {"扣除非经常性损益后的基本每股收益", "扣非基本每股收益", "deducted_basic_eps"},
    "加权平均ROE": {"加权平均净资产收益率", "加权平均ROE", "weighted_avg_roe"},
    "扣非加权平均ROE": {
        "扣除非经常性损益后的加权平均净资产收益率",
        "扣非加权平均ROE",
        "deducted_weighted_avg_roe",
    },
}
MAIN_STATEMENT_LABELS = {
    "balance_sheet": "资产负债表核心数据",
    "income_statement": "利润表核心数据",
    "cash_flow_statement": "现金流量表核心数据",
}


def _uses_okf_source_type(wiki_base: Path = WIKI_BASE) -> bool:
    return DEFAULT_SOURCE_TYPE.startswith("okf_") or "okf_staging" in str(wiki_base)


def _local_source_type(kind: str, wiki_base: Path = WIKI_BASE) -> str:
    prefix = "okf" if _uses_okf_source_type(wiki_base) else "wiki"
    return f"{prefix}_{kind}"
HUMAN_CAPITAL_METRIC_TERMS = (
    "员工情况",
    "人才结构",
    "人才构成",
    "人员结构",
    "人员构成",
    "员工结构",
    "员工构成",
    "专业构成",
    "教育程度",
    "学历结构",
    "人力资源结构",
)
HUMAN_CAPITAL_TABLE_TERMS = (
    "母公司在职员工的数量",
    "主要子公司在职员工的数量",
    "在职员工的数量合计",
    "专业构成",
    "教育程度",
)
MAIN_STATEMENT_TERMS = {
    "cash_flow_statement": (
        "现金流",
        "现金流量表",
        "经营活动现金",
        "投资活动现金",
        "筹资活动现金",
        "经营活动产生的现金流量净额",
        "经营现金流",
    ),
    "balance_sheet": (
        "资产负债表",
        "资产负债",
        "资产构成",
        "资产结构",
        "负债结构",
        "负债与权益",
        "负债权益",
        "偿债",
        "总资产",
        "总负债",
        "净资产",
        "流动资产",
        "非流动资产",
        "流动负债",
        "非流动负债",
        "所有者权益",
        "股东权益",
    ),
    "income_statement": (
        "利润表",
        "损益表",
        "营业收入",
        "营收",
        "营业成本",
        "营业利润",
        "利润总额",
        "净利润",
        "归母净利润",
        "扣非归母",
        "扣非净利润",
    ),
}

GENERIC_DETAIL_TERMS = {"明细", "详情", "附注"}
GENERIC_PREVIEW_BASES = {"客户", "供应商"}
INTENT_ALIASES = {
    "账龄": ("账龄",),
    "前五名": ("前五名", "前5名", "前五大", "客户名称", "供应商名称", "单位名称", "按欠款方归集"),
    "分类": ("分类", "类别"),
    "构成": ("构成", "分类", "分解", "分布"),
    "分布": ("分布", "分类", "分解", "构成"),
    "组成": ("组成", "构成", "分类", "分解"),
    "减值": ("减值", "准备", "跌价", "坏账", "可收回", "资产组"),
    "准备": ("准备", "减值", "跌价", "坏账", "计提"),
    "原值": ("原值", "账面原值"),
    "账面原值": ("账面原值", "原值"),
    "变动": ("变动", "增加", "减少", "计提", "转回", "转销", "核销"),
    "核销": ("核销",),
    "抵押": ("抵押", "质押", "抵押物"),
    "质押": ("质押", "抵押", "抵押物"),
    "资产组": ("资产组",),
    "可收回": ("可收回", "公允价值", "预计未来现金流量"),
}
BASE_STRIP_TERMS = tuple(
    dict.fromkeys(
        [
            "明细",
            "构成",
            "分布",
            "组成",
            "附注",
            "详情",
            "减值",
            "准备",
            "变动",
            "原值",
            "账面原值",
            "账龄",
            "前五名",
            "资产组",
            "可收回金额",
            *[alias for aliases in INTENT_ALIASES.values() for alias in aliases],
            "是什么",
            "有哪些",
            "列出",
            "展示",
            "显示",
            "情况",
        ]
    )
)
QUESTION_NOISE_TERMS = (
    "请问",
    "请",
    "查询一下",
    "查一下",
    "查询",
    "看看",
    "帮我",
    "给我",
    "列出",
    "展示",
    "显示",
    "打开",
    "是什么",
    "有哪些",
    "多少",
    "如何",
    "怎么",
    "是否",
    "有没有",
    "对应",
    "数据",
    "表格",
    "来源",
    "溯源",
    "情况",
    "内容",
    "报告",
    "年报",
    "年度报告",
    "中的",
    "里面的",
    "里的",
    "关于",
    "和",
    "及",
    "的",
    "吗",
    "呢",
)
QUESTION_NOISE_PATTERNS = (
    r"20\d{2}\s*年(?:度)?(?:年报|年度报告|报告)?",
    r"[?？!！。.,，;；:：]",
)

_TABLE_REF_CACHE: dict[tuple[str, str], dict[int, dict[str, Any]]] = {}
_PRINTED_PAGE_CACHE: dict[tuple[str, str], dict[int, str]] = {}
_COMPANY_TASK_CACHE: dict[str, dict[str, Path]] = {}
_COMPANY_ALIAS_CACHE: dict[str, list[Path]] = {}


def _iter_company_dirs(wiki_base: Path = WIKI_BASE) -> list[Path]:
    """Return both A-share and generic-subject company directories."""
    seen: set[Path] = set()
    result: list[Path] = []

    def add(path: Path) -> None:
        try:
            resolved = path.resolve()
        except OSError:
            return
        if resolved in seen or not path.is_dir():
            return
        seen.add(resolved)
        result.append(path)

    catalog = read_json(wiki_base / "_meta" / "company_catalog.json", {}) or {}
    for item in catalog.get("companies") or []:
        rel_path = item.get("company_path")
        company_id = item.get("company_id")
        if rel_path:
            add(wiki_base / rel_path)
        elif company_id:
            add(wiki_base / "companies" / str(company_id))

    companies_dir = wiki_base / "companies"
    if companies_dir.exists():
        for company_dir in companies_dir.iterdir():
            add(company_dir)
    return result


def read_json(path: Path, default: Any = None) -> Any:
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _to_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize(text: Any) -> str:
    return re.sub(r"[\s（）()_\-：:]+", "", str(text or "").lower())


def _clean_metric_query(company_dir: Path, metric_text: str | None) -> str | None:
    """Remove company/question wording before matching document_links nodes."""
    if not metric_text:
        return metric_text
    text = str(metric_text or "")
    company = read_json(company_dir / "company.json", {}) or {}
    aliases = [
        company_dir.name,
        company.get("company_id"),
        company.get("stock_code"),
        company.get("company_short_name"),
        company.get("company_full_name"),
        *(company.get("aliases") or []),
    ]
    for alias in sorted({str(item) for item in aliases if item}, key=len, reverse=True):
        text = text.replace(alias, " ")
    for pattern in QUESTION_NOISE_PATTERNS:
        text = re.sub(pattern, " ", text)
    for term in QUESTION_NOISE_TERMS:
        text = text.replace(term, " ")
    text = re.sub(r"\s+", "", text).strip()
    return text or metric_text


def _main_statement_type_from_text(text: str | None) -> str | None:
    normalized = _normalize(text)
    if not normalized:
        return None
    if "商誉" in normalized:
        main_terms = ("账面价值", "账面净值", "净额", "主表", "资产负债表", "报表项目", "合并报表", "余额")
        if any(_normalize(term) in normalized for term in main_terms):
            return "balance_sheet"
    if any(_normalize(term) in normalized for term in MAIN_STATEMENT_TERMS["cash_flow_statement"]):
        return "cash_flow_statement"
    if any(_normalize(term) in normalized for term in MAIN_STATEMENT_TERMS["balance_sheet"]):
        if any(term in normalized for term in (_normalize("资产组"), _normalize("资产减值"))):
            return None
        return "balance_sheet"
    if any(_normalize(term) in normalized for term in MAIN_STATEMENT_TERMS["income_statement"]):
        return "income_statement"
    return None


def _company_alias_index(wiki_base: Path = WIKI_BASE) -> dict[str, list[Path]]:
    cache_key = str(wiki_base.resolve())
    if cache_key in _COMPANY_ALIAS_CACHE:
        return _COMPANY_ALIAS_CACHE[cache_key]

    result: dict[str, list[Path]] = {}

    def add_alias(alias: Any, company_dir: Path) -> None:
        key = _normalize(alias)
        if not key:
            return
        result.setdefault(key, [])
        if company_dir not in result[key]:
            result[key].append(company_dir)

    catalog = read_json(wiki_base / "_meta" / "company_catalog.json", {}) or {}
    for item in catalog.get("companies") or []:
        rel_path = item.get("company_path")
        company_id = item.get("company_id")
        company_dir = wiki_base / rel_path if rel_path else wiki_base / "companies" / str(company_id or "")
        if not company_dir.exists():
            continue
        add_alias(company_id, company_dir)
        add_alias(item.get("stock_code"), company_dir)
        add_alias(item.get("company_short_name"), company_dir)
        add_alias(item.get("company_full_name"), company_dir)
        for alias in item.get("aliases") or []:
            add_alias(alias, company_dir)

    for company_dir in _iter_company_dirs(wiki_base):
        add_alias(company_dir.name, company_dir)
        company = read_json(company_dir / "company.json", {}) or {}
        add_alias(company.get("company_id"), company_dir)
        add_alias(company.get("stock_code"), company_dir)
        add_alias(company.get("company_short_name"), company_dir)
        add_alias(company.get("company_full_name"), company_dir)
        for alias in company.get("aliases") or []:
            add_alias(alias, company_dir)

    _COMPANY_ALIAS_CACHE[cache_key] = result
    return result


def _split_metric_tokens(metric_text: str | None) -> list[str]:
    if not metric_text:
        return []
    return [item.strip() for item in re.split(r"[/,，、;；]+", metric_text) if item.strip()]


def _numbers_from_text(text: str | None) -> list[int]:
    if not text:
        return []
    return list(dict.fromkeys(int(item) for item in re.findall(r"\d+", text)))


def _line_bounds_from_text(text: str | None) -> tuple[int | None, int | None]:
    if not text:
        return None, None
    range_match = re.search(r"(\d+)\s*[-~～]\s*(\d+)", text)
    if range_match:
        start, end = int(range_match.group(1)), int(range_match.group(2))
        return (min(start, end), max(start, end))
    numbers = _numbers_from_text(text)
    if not numbers:
        return None, None
    return numbers[0], numbers[0]


def _company_task_index(wiki_base: Path = WIKI_BASE) -> dict[str, Path]:
    cache_key = str(wiki_base.resolve())
    if cache_key in _COMPANY_TASK_CACHE:
        return _COMPANY_TASK_CACHE[cache_key]

    result: dict[str, Path] = {}
    for company_dir in _iter_company_dirs(wiki_base):
        company = read_json(company_dir / "company.json", {}) or {}
        for report in company.get("reports") or []:
            task_id = report.get("task_id")
            if task_id:
                result[str(task_id)] = company_dir
        task_id = company.get("task_id")
        if task_id:
            result[str(task_id)] = company_dir

    _COMPANY_TASK_CACHE[cache_key] = result
    return result


def find_company_dir_from_text(text: str, wiki_base: Path = WIKI_BASE) -> Path | None:
    """Infer a company directory from a chat reply or report snippet."""
    patterns = [
        r"/home/maoyd/okf_staging/companies/([0-9A-Za-z]+-[^/\s`，,]+)",
        r"/home/maoyd/siq-research-engine/data/wiki/companies/([0-9]{6}-[^/\s`，,]+)",
        r"companies/([0-9A-Za-z]+-[^/\s`，,]+)",
        r"\b([0-9A-Za-z]+-[^\s`/，,]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        candidate = wiki_base / "companies" / match.group(1)
        if candidate.exists():
            return candidate

    stock_match = re.search(r"\b([0-9]{6})\b", text)
    if stock_match:
        matches = sorted((wiki_base / "companies").glob(f"{stock_match.group(1)}-*"))
        if matches:
            return matches[0]

    for task_id in re.findall(r"\b[0-9a-fA-F-]{32,36}\b", text):
        company_dir = _company_task_index(wiki_base).get(task_id)
        if company_dir and company_dir.exists():
            return company_dir

    normalized_text = _normalize(text)
    alias_matches: list[tuple[int, str, Path]] = []
    for alias, company_dirs in _company_alias_index(wiki_base).items():
        if len(alias) < 2 or alias not in normalized_text:
            continue
        for company_dir in company_dirs:
            alias_matches.append((len(alias), alias, company_dir))
    if alias_matches:
        alias_matches.sort(key=lambda item: (-item[0], item[1], str(item[2])))
        return alias_matches[0][2]
    return None


def _report_id_from_file_name(file_name: str | None) -> str | None:
    if not file_name:
        return None
    match = re.search(r"(?:^|/)reports/([^/]+)/", str(file_name))
    return match.group(1) if match else None


ANNUAL_REPORT_TERMS = ("年报", "年度报告", "年度报", "annual", "2025年报", "2025年度")
QUARTERLY_REPORT_TERMS = ("季报", "季度报告", "一季报", "三季报", "半年报", "半年度报告", "quarter", "quarterly", "2025q")


def _report_text_blob(report: dict[str, Any]) -> str:
    metadata = report.get("source_filename_metadata") if isinstance(report.get("source_filename_metadata"), dict) else {}
    values = [
        report.get("report_id"),
        report.get("report_kind"),
        report.get("source_filename"),
        metadata.get("report_type"),
        metadata.get("report_end"),
    ]
    return " ".join(str(item or "") for item in values).lower()


def _report_is_annual(report: dict[str, Any]) -> bool:
    text = _report_text_blob(report)
    return "annual" in text or "年报" in text or "年度报告" in text or "2025-annual" in text


def _report_is_quarterly(report: dict[str, Any]) -> bool:
    text = _report_text_blob(report)
    return any(term in text for term in ("quarter", "quarterly", "季报", "季度", "半年报", "半年度"))


def _select_report(reports: list[dict[str, Any]], primary_report_id: str | None, query_text: str | None) -> tuple[dict[str, Any] | None, str | None]:
    if not reports:
        return None, None
    text = re.sub(r"\s+", "", query_text or "").lower()
    wants_quarterly = any(term.lower() in text for term in QUARTERLY_REPORT_TERMS)
    wants_annual = any(term.lower() in text for term in ANNUAL_REPORT_TERMS)

    if wants_quarterly:
        report = next((item for item in reports if _report_is_quarterly(item)), None)
        if report:
            return report, report.get("report_id")
    if wants_annual or not wants_quarterly:
        report = next((item for item in reports if item.get("report_id") == "2025-annual"), None)
        if report:
            return report, report.get("report_id")
        report = next((item for item in reports if _report_is_annual(item)), None)
        if report:
            return report, report.get("report_id")

    report = next((item for item in reports if item.get("report_id") == primary_report_id), None)
    if report:
        return report, report.get("report_id")
    return reports[0], reports[0].get("report_id")


def primary_report(
    company_dir: Path,
    report_id: str | None = None,
    *,
    file_name: str | None = None,
    task_id: str | None = None,
    query_text: str | None = None,
) -> dict[str, Any]:
    company = read_json(company_dir / "company.json", {}) or {}
    reports = [item for item in (company.get("reports") or []) if isinstance(item, dict)]
    requested_report_id = report_id or _report_id_from_file_name(file_name)
    report = None
    if task_id:
        report = next((item for item in reports if str(item.get("task_id") or "") == str(task_id)), None)
    if not report and requested_report_id:
        report = next((item for item in reports if item.get("report_id") == requested_report_id), None)
    if not report:
        report, requested_report_id = _select_report(reports, company.get("primary_report_id"), query_text)
    report = report or {}
    resolved_report_id = requested_report_id or report.get("report_id") or "2025-annual"
    doc_rel = report.get("document_full") or f"reports/{resolved_report_id}/document_full.json"
    return {
        "report_id": resolved_report_id,
        "task_id": report.get("task_id") or company.get("task_id"),
        "document_full": company_dir / doc_rel,
    }


def _printed_page_numbers_by_pdf_page(document_full: dict[str, Any] | None) -> dict[int, str]:
    if not isinstance(document_full, dict):
        return {}
    pages: dict[int, str] = {}
    for item in document_full.get("content_list") or []:
        if not isinstance(item, dict) or item.get("type") != "page_number":
            continue
        page_idx = item.get("page_idx")
        if not isinstance(page_idx, int):
            continue
        value = str(item.get("text") or "").strip()
        if value:
            pages[page_idx + 1] = value
    for item in ((document_full.get("content_list_enhanced") or {}).get("pages") or []):
        if not isinstance(item, dict):
            continue
        page = _to_int(item.get("pdf_page_number") or item.get("page_number"))
        value = str(item.get("printed_page_number") or "").strip()
        if page and value:
            pages.setdefault(page, value)
    return pages


def _printed_page_map(company_dir: Path, report_id: str | None = None) -> dict[int, str]:
    report = primary_report(company_dir, report_id=report_id)
    resolved_report_id = report["report_id"]
    cache_key = (str(company_dir.resolve()), resolved_report_id)
    if cache_key not in _PRINTED_PAGE_CACHE:
        document_full = read_json(report["document_full"], {}) or {}
        _PRINTED_PAGE_CACHE[cache_key] = _printed_page_numbers_by_pdf_page(document_full)
    return _PRINTED_PAGE_CACHE[cache_key]


def _put_ref(refs: dict[int, dict[str, Any]], table_index: Any, source: dict[str, Any]) -> None:
    table_no = _to_int(table_index)
    page = _to_int(source.get("pdf_page_number") or source.get("pdf_page"))
    if table_no is None or page is None:
        return
    existing = refs.get(table_no)
    if existing and existing.get("pdf_page_number"):
        return
    refs[table_no] = {
        "table_index": table_no,
        "pdf_page_number": page,
        "printed_page_number": source.get("printed_page_number"),
        "md_line": _to_int(source.get("md_line") or source.get("line")),
    }


def _collect_table_refs(obj: Any, refs: dict[int, dict[str, Any]]) -> None:
    if isinstance(obj, dict):
        _put_ref(refs, obj.get("table_index"), obj)
        for value in obj.values():
            _collect_table_refs(value, refs)
    elif isinstance(obj, list):
        for item in obj:
            _collect_table_refs(item, refs)


def table_ref_map(company_dir: Path, report_id: str | None = None) -> dict[int, dict[str, Any]]:
    report = primary_report(company_dir, report_id=report_id)
    resolved_report_id = report["report_id"]
    cache_key = (str(company_dir.resolve()), resolved_report_id)
    if cache_key in _TABLE_REF_CACHE:
        return _TABLE_REF_CACHE[cache_key]

    refs: dict[int, dict[str, Any]] = {}

    pdf_refs = read_json(_artifact_path(company_dir, resolved_report_id, "evidence/pdf_refs.json"), {}) or {}
    for ref in pdf_refs.get("refs") or []:
        _put_ref(refs, ref.get("table_index"), ref)

    document_full = read_json(report["document_full"], {}) or {}
    _collect_table_refs(document_full, refs)
    printed_pages = _printed_page_numbers_by_pdf_page(document_full)
    _PRINTED_PAGE_CACHE[cache_key] = printed_pages
    for ref in refs.values():
        page = _to_int(ref.get("pdf_page_number"))
        if page and not ref.get("printed_page_number"):
            ref["printed_page_number"] = printed_pages.get(page)

    _TABLE_REF_CACHE[cache_key] = refs
    return refs


def _metric_matches(metric: dict[str, Any], token: str) -> bool:
    metric_candidates = {
        metric.get("name"),
        metric.get("canonical_name"),
    }
    token_norm = _normalize(token)
    metric_norms = {_normalize(item) for item in metric_candidates if item}

    acceptable = {token_norm}
    for alias, values in METRIC_ALIASES.items():
        alias_norm = _normalize(alias)
        value_norms = {_normalize(value) for value in values}
        if token_norm == alias_norm or token_norm in value_norms:
            acceptable.add(alias_norm)
            acceptable.update(value_norms)

    return bool(acceptable & metric_norms)


def _record_matches_tokens(record: dict[str, Any], tokens: list[str]) -> bool:
    if not tokens:
        return True

    candidates = {
        record.get("metric"),
        record.get("metric_name"),
        record.get("metric_key"),
        record.get("canonical_name"),
        record.get("item_name"),
        record.get("claim"),
        record.get("title"),
    }
    candidate_norms = {_normalize(item) for item in candidates if item}
    if not candidate_norms:
        return False

    for token in tokens:
        token_norm = _normalize(token)
        acceptable = {token_norm}
        for alias, values in METRIC_ALIASES.items():
            alias_norm = _normalize(alias)
            value_norms = {_normalize(value) for value in values}
            if token_norm == alias_norm or token_norm in value_norms:
                acceptable.add(alias_norm)
                acceptable.update(value_norms)
        if acceptable & candidate_norms:
            return True
        # Evidence indexes use richer names than key_metrics. A conservative
        # substring fallback helps match "流动负债占比" style references without
        # reintroducing the broad key_metrics matching bug.
        if len(token_norm) >= 4 and any(token_norm in item or item in token_norm for item in candidate_norms):
            return True
    return False


def _node_matches_tokens(node: dict[str, Any], tokens: list[str]) -> bool:
    if not tokens:
        return True
    candidates = {
        node.get("name"),
        node.get("title"),
        node.get("note_title"),
        node.get("heading"),
        node.get("preview"),
    }
    return _record_matches_tokens({key: value for key, value in {
        "metric_name": " ".join(str(item) for item in candidates if item),
    }.items()}, tokens)


def _question_prefers_detail(metric_text: str | None) -> bool:
    text = str(metric_text or "")
    return any(
        keyword in text
        for keyword in (
            "构成",
            "明细",
            "减值",
            "准备",
            "变动",
            "附注",
            "详情",
            "组成",
            "账龄",
            "分类",
            "分解",
            "分布",
            "前五名",
            "前5名",
            "资产组",
            "可收回",
            "核销",
        )
    )


def _semantic_relation_filter(metric_text: str | None) -> set[str]:
    text = str(metric_text or "")
    relations: set[str] = set()
    if any(keyword in text for keyword in ("减值", "准备", "减值准备")):
        relations.add("impairment_detail")
    if any(keyword in text for keyword in ("构成", "组成", "明细", "原值", "账面原值")):
        relations.update({"composition_detail", "detail_disclosure", "movement_detail"})
    if any(keyword in text for keyword in ("账龄", "分类", "分解", "分布", "前五名", "前5名", "核销")):
        relations.update({"composition_detail", "detail_disclosure", "movement_detail"})
    if any(keyword in text for keyword in ("明细", "详情", "附注")):
        relations.update({"composition_detail", "detail_disclosure", "impairment_detail", "movement_detail"})
    if "变动" in text:
        relations.add("movement_detail")
    return relations


def _detail_base_tokens(metric_text: str | None) -> list[str]:
    raw = _normalize(metric_text)
    if not raw:
        return []
    stripped = raw
    for term in BASE_STRIP_TERMS:
        stripped = stripped.replace(_normalize(term), "")
    return list(dict.fromkeys(item for item in [stripped or raw] if item))


def _query_intent_tokens(metric_text: str | None) -> tuple[list[str], bool]:
    raw = _normalize(metric_text)
    intents: list[str] = []
    specific = False
    for trigger, aliases in INTENT_ALIASES.items():
        trigger_norm = _normalize(trigger)
        if trigger_norm and trigger_norm in raw:
            specific = trigger not in GENERIC_DETAIL_TERMS
            intents.extend(_normalize(alias) for alias in aliases)
    for term in GENERIC_DETAIL_TERMS:
        term_norm = _normalize(term)
        if term_norm and term_norm in raw:
            intents.append(term_norm)
    return list(dict.fromkeys(item for item in intents if item)), specific


def _numbered_note_title(text: Any) -> bool:
    return bool(re.match(r"^\s*[（(]?\d+\s*[).、）]", str(text or "")))


def _note_table_base_score(target: dict[str, Any], source: dict[str, Any], base_tokens: list[str]) -> int:
    if not base_tokens:
        return 0
    source_text = _normalize(
        " ".join(
            str(item)
            for item in (
                source.get("name"),
                source.get("title"),
                source.get("note_title"),
            )
            if item
        )
    )
    target_title_text = _normalize(
        " ".join(
            str(item)
            for item in (
                target.get("name"),
                target.get("title"),
            )
            if item
        )
    )
    target_note_text = _normalize(target.get("note_title"))
    target_preview = _normalize(target.get("preview"))
    score = 0
    for token in base_tokens:
        if not token:
            continue
        if token in source_text:
            score = max(score, 70)
        if token in target_title_text:
            score = max(score, 90)
        if token in target_note_text:
            score = max(score, 60)
        if token in GENERIC_PREVIEW_BASES and token in target_preview:
            score = max(score, 45)
    return score


def _note_table_matches_base(target: dict[str, Any], source: dict[str, Any], base_tokens: list[str]) -> bool:
    return _note_table_base_score(target, source, base_tokens) > 0 or not base_tokens


def _note_table_contains_base(target: dict[str, Any], base_tokens: list[str]) -> bool:
    if not base_tokens:
        return True
    target_text = _normalize(
        " ".join(
            str(item)
            for item in (
                target.get("name"),
                target.get("title"),
                target.get("preview"),
            )
            if item
        )
    )
    return any(token and token in target_text for token in base_tokens)


def _note_table_intent_score(target: dict[str, Any], metric_text: str | None) -> tuple[int, bool]:
    intent_tokens, specific = _query_intent_tokens(metric_text)
    if not intent_tokens:
        return 0, False
    target_text = _normalize(
        " ".join(
            str(item)
            for item in (
                target.get("name"),
                target.get("title"),
                target.get("preview"),
            )
            if item
        )
    )
    generic_tokens = {_normalize(term) for term in GENERIC_DETAIL_TERMS}
    score = 0
    for token in intent_tokens:
        if token and token in target_text:
            score = max(score, 35 if token in generic_tokens else 80)
    return score, specific


def _document_link_relation_score(relation_name: str | None, metric_text: str | None) -> int:
    text = str(metric_text or "")
    if relation_name == "composition_detail" and any(term in text for term in ("构成", "组成", "分类", "分布", "明细")):
        return 35
    if relation_name == "impairment_detail" and any(term in text for term in ("减值", "准备", "跌价", "坏账", "可收回", "资产组")):
        return 35
    if relation_name == "movement_detail" and any(term in text for term in ("变动", "增加", "减少", "计提", "核销", "账龄", "明细")):
        return 20
    if relation_name == "detail_disclosure":
        return 15
    return 0


def _document_link_confidence_score(value: Any) -> int:
    text = str(value or "").lower()
    if text == "high":
        return 12
    if text == "medium":
        return 6
    if text == "low":
        return 1
    return 0


def _document_link_title_penalty(target: dict[str, Any], metric_text: str | None) -> int:
    title = _normalize(target.get("title") or target.get("name"))
    query = _normalize(metric_text)
    penalty = 0
    if any(term in query for term in (_normalize("构成"), _normalize("明细"), _normalize("详情"))):
        if "项目列示" in title:
            penalty -= 25
        if "未办妥" in title:
            penalty -= 12
    return penalty


def _document_link_match_score(
    link: dict[str, Any],
    metric_text: str | None,
    relation_filter: set[str],
    base_tokens: list[str],
    prefer_detail: bool,
) -> int | None:
    relation = link.get("relation") if isinstance(link.get("relation"), dict) else {}
    if relation_filter and relation.get("semantic_relation") not in relation_filter:
        return None
    source = link.get("source") if isinstance(link.get("source"), dict) else {}
    target = link.get("target") if isinstance(link.get("target"), dict) else {}
    if prefer_detail and target.get("kind") not in {"note_table", "table"}:
        return None
    if target.get("kind") not in {"note_table", "table"}:
        return None
    base_score = _note_table_base_score(target, source, base_tokens)
    if base_tokens and base_score <= 0:
        return None
    intent_score, specific_intent = _note_table_intent_score(target, metric_text)
    if specific_intent and intent_score <= 0:
        return None
    query_norm = _normalize(metric_text)
    if not specific_intent and any(term in query_norm for term in (_normalize("明细"), _normalize("详情"), _normalize("附注"))):
        if not _note_table_contains_base(target, base_tokens):
            return None
    return (
        base_score
        + intent_score
        + _document_link_relation_score(relation.get("semantic_relation"), metric_text)
        + _document_link_confidence_score(link.get("confidence") or relation.get("confidence"))
        + _document_link_title_penalty(target, metric_text)
    )


def _record_matches_raw_fields(record: dict[str, Any], *, table_text: str | None = None, line_text: str | None = None) -> bool:
    table_numbers = set(_numbers_from_text(table_text))
    line_numbers = set(_numbers_from_text(line_text))

    source = record.get("source") if isinstance(record.get("source"), dict) else {}
    table_index = _to_int(record.get("table_index") or record.get("source_table_index") or source.get("table_index"))
    md_line = _to_int(
        record.get("md_line")
        or record.get("markdown_line")
        or record.get("line")
        or source.get("md_line")
        or source.get("markdown_line")
        or source.get("line")
    )

    table_ok = not table_numbers or table_index in table_numbers
    line_ok = not line_numbers or md_line in line_numbers
    return table_ok and line_ok


def _period_matches(record: dict[str, Any], period_text: str | None) -> bool:
    years = re.findall(r"20\d{2}", period_text or "")
    if not years:
        return True
    haystack = " ".join(str(record.get(key) or "") for key in ("period", "report_year", "report_id"))
    return any(year in haystack for year in years)


def _period_years(period_text: str | None, values: dict[str, Any]) -> list[str]:
    years = re.findall(r"20\d{2}", period_text or "")
    if years:
        return list(dict.fromkeys(years))
    return sorted((values or {}).keys(), reverse=True)[:1]


def _public_api_url(path: str) -> str:
    if path.startswith(("http://", "https://")):
        return path
    if path.startswith("/"):
        return f"{PUBLIC_ORIGIN}{path}"
    return path


def _with_urls(ref: dict[str, Any]) -> dict[str, Any]:
    task_id = ref.get("task_id")
    page = ref.get("pdf_page")
    table_index = ref.get("table_index")
    table_page = _to_int(ref.pop("_table_pdf_page", None))
    if task_id and page:
        ref["open_pdf_page_url"] = _public_api_url(f"/api/pdf_page/{task_id}/{page}?format=html")
        ref["open_source_page_url"] = _public_api_url(f"/api/source/{task_id}/page/{page}?format=html")
    if task_id and table_index:
        ref["open_source_table_url"] = _public_api_url(f"/api/source/{task_id}/table/{table_index}?format=html")
    if table_index and table_page is not None and page is not None and table_page != _to_int(page):
        ref["table_pdf_page"] = table_page
        ref["table_index_conflict"] = {
            "table_pdf_page": table_page,
            "anchor_pdf_page": _to_int(page),
            "resolution": "table_url_kept_for_structured_table",
        }
    return ref


def _report_md_path(company_dir: Path, report_id: str | None) -> Path:
    return company_dir / "reports" / (report_id or primary_report(company_dir)["report_id"]) / "report.md"


def _artifact_path(company_dir: Path, report_id: str | None, file_name: str) -> Path:
    """Resolve OKF report-scoped artifacts while keeping legacy wiki paths working."""
    rel = str(file_name or "").strip().lstrip("/")
    if not rel:
        rel = "reports/%s/report.md" % (report_id or primary_report(company_dir)["report_id"])
    candidates: list[Path] = []
    if rel.startswith("reports/"):
        candidates.append(company_dir / rel)
    else:
        resolved_report_id = report_id or primary_report(company_dir, file_name=file_name)["report_id"]
        report_scoped = company_dir / "reports" / resolved_report_id / rel
        root_scoped = company_dir / rel
        if _uses_okf_source_type():
            candidates.extend([report_scoped, root_scoped])
        else:
            candidates.extend([root_scoped, report_scoped])
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0] if candidates else company_dir / rel


def _artifact_relpath(company_dir: Path, path: Path, fallback: str) -> str:
    try:
        return str(path.relative_to(company_dir))
    except ValueError:
        return fallback


def _markdown_anchor_page(company_dir: Path, report_id: str | None, line_number: int | None) -> int | None:
    if line_number is None:
        return None
    return _pdf_page_from_markdown_line(_report_md_path(company_dir, report_id), line_number)


def _ref_from_record(company_dir: Path, record: dict[str, Any], source_type: str, file_name: str) -> dict[str, Any] | None:
    report = primary_report(company_dir)
    source = record.get("source") if isinstance(record.get("source"), dict) else {}
    table_index = _to_int(
        record.get("table_index")
        or record.get("source_table_index")
        or source.get("table_index")
    )
    table_ref = table_ref_map(company_dir, record.get("report_id") or report["report_id"]).get(table_index or -1, {})
    table_page = _to_int(table_ref.get("pdf_page_number"))
    field_page = _to_int(
        record.get("pdf_page")
        or record.get("pdf_page_number")
        or record.get("source_page_number")
        or source.get("pdf_page")
        or source.get("pdf_page_number")
        or source.get("source_page_number")
        or table_ref.get("pdf_page_number")
    )
    md_line = _to_int(
        record.get("md_line")
        or record.get("md_line_start")
        or record.get("markdown_line")
        or record.get("line")
        or source.get("md_line")
        or source.get("md_line_start")
        or source.get("markdown_line")
        or source.get("line")
        or table_ref.get("md_line")
    )
    anchor_page = _markdown_anchor_page(company_dir, record.get("report_id") or report["report_id"], md_line)
    page = field_page or anchor_page
    task_id = record.get("task_id") or source.get("task_id") or report.get("task_id")
    if not task_id and not page and table_index is None:
        return None

    return _with_urls({
        "source_type": source_type,
        "file": file_name,
        "metric": record.get("metric_name") or record.get("item_name") or record.get("metric") or record.get("canonical_name"),
        "canonical_name": record.get("canonical_name") or record.get("metric_key"),
        "period": record.get("period") or source.get("period") or record.get("report_year"),
        "task_id": task_id,
        "pdf_page": page,
        "printed_page_number": record.get("printed_page_number") or source.get("printed_page_number") or table_ref.get("printed_page_number"),
        "table_index": table_index,
        "_table_pdf_page": table_page,
        "md_line": md_line,
        "value": record.get("value"),
        "raw_value": record.get("raw_value"),
        "unit": record.get("raw_unit") or record.get("unit"),
        "statement_type": record.get("statement_type"),
        "evidence_id": record.get("evidence_id"),
        "pdf_page_conflict": (
            {
                "field_pdf_page": field_page,
                "markdown_anchor_pdf_page": anchor_page,
                "resolution": "structured_page_preferred",
            }
            if anchor_page and field_page and anchor_page != field_page
            else None
        ),
    })


def _iter_records(obj: Any) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if isinstance(obj, dict):
        if any(key in obj for key in ("metric_name", "metric_key", "canonical_name", "item_name", "table_index", "source")):
            records.append(obj)
        for value in obj.values():
            records.extend(_iter_records(value))
    elif isinstance(obj, list):
        for item in obj:
            records.extend(_iter_records(item))
    return records


def _collect_line_table_records(obj: Any, records: list[dict[str, Any]]) -> None:
    if isinstance(obj, dict):
        table_index = _to_int(obj.get("table_index"))
        line = _to_int(obj.get("line") or obj.get("md_line") or obj.get("markdown_line"))
        if table_index is not None and line is not None:
            records.append(obj)
        for value in obj.values():
            _collect_line_table_records(value, records)
    elif isinstance(obj, list):
        for item in obj:
            _collect_line_table_records(item, records)


def _record_page(record: dict[str, Any]) -> int | None:
    return _to_int(record.get("pdf_page_number") or record.get("pdf_page"))


def _pdf_page_from_markdown_line(report_md: Path, line_number: int | None) -> int | None:
    if line_number is None or not report_md.exists():
        return None
    try:
        lines = report_md.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return None

    index = max(0, min(line_number - 1, len(lines) - 1))
    page: int | None = None
    for current in range(index, -1, -1):
        match = re.search(r"\[PDF_PAGE:\s*(\d+)\]", lines[current])
        if match:
            page = int(match.group(1))
            break
    return page


def _markdown_anchor_distance(report_md: Path, line_number: int | None) -> int | None:
    if line_number is None or not report_md.exists():
        return None
    try:
        lines = report_md.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return None
    index = max(0, min(line_number - 1, len(lines) - 1))
    for current in range(index, -1, -1):
        if re.search(r"\[PDF_PAGE:\s*(\d+)\]", lines[current]):
            return index - current
    return None


def _report_line_text(report_md: Path, line_number: int | None) -> str:
    if line_number is None or not report_md.exists():
        return ""
    try:
        lines = report_md.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return ""
    if line_number < 1 or line_number > len(lines):
        return ""
    return lines[line_number - 1].strip()


def _content_list_refs_for_report_line(
    document_full: dict[str, Any],
    line_text: str,
    line_number: int | None,
) -> list[dict[str, Any]]:
    normalized_line = _normalize(line_text)
    if not normalized_line or len(normalized_line) < 4:
        return []
    refs: list[dict[str, Any]] = []
    for index, item in enumerate(document_full.get("content_list") or []):
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        normalized_item = _normalize(text)
        if normalized_item != normalized_line and normalized_line not in normalized_item:
            continue
        page_idx = _to_int(item.get("page_idx"))
        if page_idx is None:
            continue
        refs.append(
            {
                "source_type": _local_source_type("report_text"),
                "pdf_page_number": page_idx + 1,
                "md_line": line_number,
                "content_index": index,
                "preview": text[:160],
            }
        )
    return refs


def _record_line(record: dict[str, Any]) -> int | None:
    return _to_int(record.get("line") or record.get("md_line") or record.get("markdown_line"))


def _record_matches_metric_text(record: dict[str, Any], metric_tokens: list[str]) -> bool:
    if not metric_tokens:
        return False
    return _record_matches_tokens({"metric_name": _record_search_text(record)}, metric_tokens)


def _record_search_text(record: dict[str, Any]) -> str:
    return " ".join(
        str(record.get(key) or "")
        for key in ("heading", "preview", "title", "name", "metric", "metric_name", "canonical_name")
    )


def _is_human_capital_metric(metric_text: str | None) -> bool:
    normalized = _normalize(metric_text)
    return bool(normalized and any(_normalize(term) in normalized for term in HUMAN_CAPITAL_METRIC_TERMS))


def _human_capital_table_score(record: dict[str, Any]) -> int:
    text = _record_search_text(record)
    normalized = _normalize(text)
    score = 0
    for term in HUMAN_CAPITAL_TABLE_TERMS:
        if _normalize(term) in normalized:
            score += 30
    if all(_normalize(term) in normalized for term in ("母公司在职员工的数量", "专业构成", "教育程度")):
        score += 120
    if _normalize("员工情况") in normalized:
        score += 20
    return score


def _best_human_capital_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scored = [
        (_human_capital_table_score(record), record)
        for record in records
        if _human_capital_table_score(record) > 0
    ]
    if not scored:
        return []
    scored.sort(
        key=lambda item: (
            -item[0],
            _to_int(item[1].get("table_index")) or 10**9,
            _record_line(item[1]) or 10**9,
        )
    )
    best_score = scored[0][0]
    return [record for score, record in scored if score == best_score]


def _record_from_report_table(company_dir: Path, report_id: str, table_index: int) -> dict[str, Any] | None:
    report_json = read_json(company_dir / "reports" / report_id / "report.json", {}) or {}
    for table in report_json.get("tables") or []:
        if isinstance(table, dict) and _to_int(table.get("table_index")) == table_index:
            return table
    return None


def _document_pdf_page_count(document_full: dict[str, Any]) -> int | None:
    if not isinstance(document_full, dict):
        return None
    candidates: list[int] = []
    for source in (
        document_full.get("task"),
        document_full.get("quality_report"),
        (document_full.get("resources") or {}).get("pdf_pages"),
        ((document_full.get("resources") or {}).get("pdf_pages") or {}).get("summary"),
    ):
        if isinstance(source, dict):
            value = _to_int(
                source.get("pdf_page_count")
                or source.get("page_count")
                or source.get("total_pages")
                or source.get("count")
            )
            if value:
                candidates.append(value)
    for item in document_full.get("content_list") or []:
        if isinstance(item, dict):
            page_idx = _to_int(item.get("page_idx"))
            if page_idx is not None:
                candidates.append(page_idx + 1)
    for item in ((document_full.get("content_list_enhanced") or {}).get("pages") or []):
        if isinstance(item, dict):
            page = _to_int(item.get("pdf_page_number") or item.get("page_number") or item.get("page"))
            if page:
                candidates.append(page)
    return max(candidates) if candidates else None


def _report_table_records(company_dir: Path, report_id: str) -> list[dict[str, Any]]:
    report_json = read_json(company_dir / "reports" / report_id / "report.json", {}) or {}
    tables = report_json.get("tables") if isinstance(report_json, dict) else []
    if not isinstance(tables, list):
        return []
    return [table for table in tables if isinstance(table, dict)]


def _merge_report_table_records(
    records: list[dict[str, Any]],
    company_dir: Path,
    report_id: str,
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[tuple[int | None, int | None]] = set()
    for record in [*_report_table_records(company_dir, report_id), *(records or [])]:
        table_index = _to_int(record.get("table_index"))
        line = _record_line(record)
        key = (table_index, line)
        if key in seen:
            continue
        seen.add(key)
        merged.append(record)
    return merged


def _records_near_report_line(
    records: list[dict[str, Any]],
    line_start: int | None,
    line_end: int | None = None,
    *,
    max_distance: int = 0,
    metric_text: str | None = None,
) -> list[dict[str, Any]]:
    if line_start is None:
        return []
    lower = line_start - max_distance
    upper = (line_end or line_start) + max_distance
    selected = [
        record
        for record in records
        if (line := _record_line(record)) is not None
        and lower <= line <= upper
    ]
    if not selected:
        return []
    metric_tokens = [_normalize(token) for token in _split_metric_tokens(metric_text)]
    selected.sort(
        key=lambda record: (
            abs((_record_line(record) or line_start) - line_start),
            0 if _record_matches_metric_text(record, metric_tokens) else 1,
            _record_line(record) or 10**9,
            _to_int(record.get("table_index")) or 10**9,
        )
    )
    closest_distance = abs((_record_line(selected[0]) or line_start) - line_start)
    return [
        record
        for record in selected
        if abs((_record_line(record) or line_start) - line_start) == closest_distance
    ]


def _dedupe_refs(refs: list[dict[str, Any]], limit: int | None = None) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for ref in refs:
        key = (
            ref.get("task_id"),
            ref.get("pdf_page"),
            ref.get("table_index"),
            ref.get("md_line"),
            ref.get("metric"),
            ref.get("period"),
        )
        if key in seen:
            continue
        seen.add(key)
        output.append(ref)
        if limit and len(output) >= limit:
            break
    return output


def resolve_key_metric_refs(
    company_dir: Path,
    metric_text: str | None = None,
    period_text: str | None = None,
) -> list[dict[str, Any]]:
    """Resolve key_metrics.json sources to PDF pages via local wiki artifacts."""
    report = primary_report(company_dir)
    metrics_path = _artifact_path(company_dir, report["report_id"], "metrics/key_metrics.json")
    key_metrics = read_json(metrics_path, {}) or {}
    metrics = key_metrics.get("data") or []
    tokens = _split_metric_tokens(metric_text)
    table_refs = table_ref_map(company_dir, report["report_id"])
    source_type = _local_source_type("metrics")
    file_name = _artifact_relpath(company_dir, metrics_path, "metrics/key_metrics.json")

    output: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()

    for metric in metrics:
        if tokens and not any(_metric_matches(metric, token) for token in tokens):
            continue
        years = _period_years(period_text, metric.get("values") or {})
        for year in years:
            source = (metric.get("sources") or {}).get(str(year)) or {}
            table_index = _to_int(source.get("table_index"))
            table_ref = table_refs.get(table_index or -1, {})
            page = _to_int(source.get("pdf_page") or table_ref.get("pdf_page_number"))
            md_line = _to_int(source.get("md_line") or source.get("line") or table_ref.get("md_line"))
            key = (metric.get("canonical_name"), year, table_index, page, md_line)
            if key in seen:
                continue
            seen.add(key)
            output.append(_with_urls({
                "source_type": source_type,
                "file": file_name,
                "metric": metric.get("name") or metric.get("canonical_name"),
                "canonical_name": metric.get("canonical_name"),
                "period": year,
                "task_id": report.get("task_id"),
                "pdf_page": page,
                "table_index": table_index,
                "md_line": md_line,
            }))

    return output


def resolve_three_statement_refs(
    company_dir: Path,
    metric_text: str | None = None,
    period_text: str | None = None,
    table_text: str | None = None,
    line_text: str | None = None,
    limit: int | None = 12,
) -> list[dict[str, Any]]:
    """Resolve three_statements.json source objects to PDF/table URLs."""
    report = primary_report(company_dir)
    metrics_path = _artifact_path(company_dir, report["report_id"], "metrics/three_statements.json")
    payload = read_json(metrics_path, {}) or {}
    records = _iter_records(payload.get("data") or payload)
    tokens = _split_metric_tokens(metric_text)

    candidates: list[dict[str, Any]] = []
    for record in records:
        if tokens and not _record_matches_tokens(record, tokens):
            continue
        if not _period_matches(record, period_text):
            continue
        candidates.append(record)

    exact_records = [
        record
        for record in candidates
        if _record_matches_raw_fields(record, table_text=table_text, line_text=line_text)
    ]
    selected_records = exact_records or candidates

    refs: list[dict[str, Any]] = []
    for record in selected_records:
        ref = _ref_from_record(
            company_dir,
            record,
            _local_source_type("metrics"),
            _artifact_relpath(company_dir, metrics_path, "metrics/three_statements.json"),
        )
        if ref:
            refs.append(ref)

    return _dedupe_refs(refs, limit)


def resolve_main_statement_refs(
    company_dir: Path,
    metric_text: str | None = None,
    period_text: str | None = None,
    limit: int | None = 12,
) -> list[dict[str, Any]]:
    """Resolve broad main-statement questions to three_statements source tables."""
    statement_type = _main_statement_type_from_text(metric_text)
    if not statement_type:
        return []

    report = primary_report(company_dir)
    metrics_path = _artifact_path(company_dir, report["report_id"], "metrics/three_statements.json")
    payload = read_json(metrics_path, {}) or {}
    records = [
        record
        for record in _iter_records(payload.get("data") or payload)
        if record.get("statement_type") == statement_type and _period_matches(record, period_text)
    ]
    tokens = _split_metric_tokens(metric_text)
    matched_records = [
        record
        for record in records
        if tokens and _record_matches_tokens(record, tokens)
    ]
    if matched_records:
        records = matched_records
    refs: list[dict[str, Any]] = []
    seen_sources: set[tuple[Any, Any, Any, Any]] = set()
    for record in records:
        ref = _ref_from_record(
            company_dir,
            record,
            _local_source_type("metrics"),
            _artifact_relpath(company_dir, metrics_path, "metrics/three_statements.json"),
        )
        if not ref:
            continue
        key = (ref.get("task_id"), ref.get("pdf_page"), ref.get("table_index"), ref.get("md_line"))
        if key in seen_sources:
            continue
        seen_sources.add(key)
        ref["metric"] = MAIN_STATEMENT_LABELS[statement_type]
        ref["statement_type"] = statement_type
        refs.append(ref)

    refs.sort(
        key=lambda ref: (
            _to_int(ref.get("md_line")) or 10**9,
            _to_int(ref.get("table_index")) or 10**9,
            _to_int(ref.get("pdf_page")) or 10**9,
        )
    )
    return _dedupe_refs(refs, limit)


def resolve_evidence_refs(
    company_dir: Path,
    metric_text: str | None = None,
    period_text: str | None = None,
    evidence_id: str | None = None,
    table_text: str | None = None,
    limit: int | None = 12,
) -> list[dict[str, Any]]:
    report = primary_report(company_dir)
    evidence_path = _artifact_path(company_dir, report["report_id"], "evidence/evidence_index.json")
    evidence_index = read_json(evidence_path, {}) or {}
    records = evidence_index.get("evidence") or []
    tokens = _split_metric_tokens(metric_text)
    table_numbers = set(_numbers_from_text(table_text))

    refs: list[dict[str, Any]] = []
    for record in records:
        if evidence_id and str(record.get("evidence_id") or "") != evidence_id:
            continue
        if table_numbers and _to_int(record.get("table_index")) not in table_numbers:
            continue
        if tokens and not _record_matches_tokens(record, tokens):
            continue
        if not _period_matches(record, period_text):
            continue
        ref = _ref_from_record(
            company_dir,
            record,
            _local_source_type("evidence"),
            _artifact_relpath(company_dir, evidence_path, "evidence/evidence_index.json"),
        )
        if ref:
            refs.append(ref)
    return _dedupe_refs(refs, limit)


def resolve_semantic_refs(
    company_dir: Path,
    metric_text: str | None = None,
    period_text: str | None = None,
    evidence_id: str | None = None,
    table_text: str | None = None,
    limit: int | None = 12,
) -> list[dict[str, Any]]:
    report = primary_report(company_dir)
    semantic_path = _artifact_path(company_dir, report["report_id"], "semantic/evidence_semantic.json")
    semantic = read_json(semantic_path, {}) or {}
    records = semantic.get("evidence") or semantic.get("items") or []
    if isinstance(semantic, list):
        records = semantic
    if not isinstance(records, list):
        return []

    tokens = _split_metric_tokens(metric_text)
    table_numbers = set(_numbers_from_text(table_text))
    refs: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        if evidence_id and str(record.get("evidence_id") or "") != evidence_id:
            continue
        if table_numbers and _to_int(record.get("table_index")) not in table_numbers:
            continue
        if tokens and not _record_matches_tokens(record, tokens):
            continue
        if not _period_matches(record, period_text):
            continue
        ref = _ref_from_record(
            company_dir,
            record,
            _local_source_type("semantic"),
            _artifact_relpath(company_dir, semantic_path, "semantic/evidence_semantic.json"),
        )
        if ref:
            refs.append(ref)
    return _dedupe_refs(refs, limit)


def resolve_document_link_refs(
    company_dir: Path,
    metric_text: str | None = None,
    period_text: str | None = None,
    link_type: str | None = None,
    target_use_case: str | None = None,
    limit: int | None = 12,
) -> list[dict[str, Any]]:
    report = primary_report(company_dir)
    printed_pages = _printed_page_map(company_dir, report["report_id"])
    document_links_path = _artifact_path(company_dir, report["report_id"], "semantic/document_links.json")
    payload = read_json(document_links_path, {}) or {}
    links = payload.get("links") if isinstance(payload.get("links"), list) else []
    metric_query = _clean_metric_query(company_dir, metric_text)
    tokens = _split_metric_tokens(metric_query)
    base_tokens = _detail_base_tokens(metric_query)
    prefer_detail = _question_prefers_detail(metric_query)
    relation_filter = _semantic_relation_filter(metric_query)
    file_name = _artifact_relpath(company_dir, document_links_path, "semantic/document_links.json")

    refs: list[dict[str, Any]] = []
    scored_links: list[tuple[int, dict[str, Any]]] = []
    iterable_links: list[tuple[int, dict[str, Any]]]
    if prefer_detail:
        for link in links:
            if not isinstance(link, dict):
                continue
            if link_type and link.get("link_type") != link_type:
                continue
            relation = link.get("relation") if isinstance(link.get("relation"), dict) else {}
            if target_use_case and relation.get("semantic_relation") != target_use_case:
                continue
            score = _document_link_match_score(link, metric_query, relation_filter, base_tokens, prefer_detail)
            if score is not None:
                scored_links.append((score, link))
        if not scored_links and relation_filter:
            for link in links:
                if not isinstance(link, dict):
                    continue
                if link_type and link.get("link_type") != link_type:
                    continue
                relation = link.get("relation") if isinstance(link.get("relation"), dict) else {}
                if target_use_case and relation.get("semantic_relation") != target_use_case:
                    continue
                score = _document_link_match_score(link, metric_query, set(), base_tokens, prefer_detail)
                if score is not None:
                    scored_links.append((score, link))
        scored_links.sort(
            key=lambda item: (
                -item[0],
                _to_int((item[1].get("target") or {}).get("table_index")) or 10**9,
                _to_int((item[1].get("target") or {}).get("md_line") or (item[1].get("target") or {}).get("line")) or 10**9,
                str(item[1].get("document_link_id") or ""),
            )
        )
        iterable_links = scored_links
    else:
        iterable_links = [(0, link) for link in links if isinstance(link, dict)]

    for _score, link in iterable_links:
        if not isinstance(link, dict):
            continue
        if link_type and link.get("link_type") != link_type:
            continue
        relation = link.get("relation") if isinstance(link.get("relation"), dict) else {}
        if target_use_case and relation.get("semantic_relation") != target_use_case:
            continue
        if relation_filter and relation.get("semantic_relation") not in relation_filter:
            continue
        source = link.get("source") if isinstance(link.get("source"), dict) else {}
        target = link.get("target") if isinstance(link.get("target"), dict) else {}
        if not prefer_detail and tokens and not (_node_matches_tokens(source, tokens) or _node_matches_tokens(target, tokens)):
            continue
        if prefer_detail and target.get("kind") not in {"note_table", "table"}:
            continue
        chosen = target if prefer_detail or target.get("kind") in {"note_table", "table", "note"} else source or target
        md_line = _to_int(chosen.get("md_line") or chosen.get("line"))
        field_page = _to_int(chosen.get("pdf_page") or chosen.get("pdf_page_number"))
        anchor_page = _markdown_anchor_page(company_dir, report.get("report_id"), md_line)
        page = field_page or anchor_page
        table_index = _to_int(chosen.get("table_index"))
        table_page = _to_int(chosen.get("pdf_page") or chosen.get("pdf_page_number"))
        if page is None and table_index is None:
            continue
        refs.append(_with_urls({
            "source_type": _local_source_type("document_links"),
            "file": file_name,
            "metric": target.get("title") or target.get("name") or source.get("name") or source.get("title"),
            "canonical_name": relation.get("semantic_relation"),
            "period": period_text or payload.get("report_id"),
            "task_id": report.get("task_id"),
            "pdf_page": page,
            "printed_page_number": chosen.get("printed_page_number") or printed_pages.get(page),
            "table_index": table_index,
            "_table_pdf_page": table_page,
            "md_line": md_line,
            "document_link_id": link.get("document_link_id"),
            "link_type": link.get("link_type"),
            "target_kind": chosen.get("kind"),
            "pdf_page_conflict": (
                {
                    "field_pdf_page": field_page,
                    "markdown_anchor_pdf_page": anchor_page,
                    "resolution": "structured_page_preferred",
                }
                if anchor_page and field_page and anchor_page != field_page
                else None
            ),
        }))
    return _dedupe_refs(refs, limit)


def resolve_table_refs(company_dir: Path, table_text: str | None, task_id: str | None = None) -> list[dict[str, Any]]:
    table_numbers = _numbers_from_text(table_text)
    if not table_numbers:
        return []
    report = primary_report(company_dir)
    refs_by_table = table_ref_map(company_dir, report["report_id"])
    refs: list[dict[str, Any]] = []
    for table_index in table_numbers:
        table_ref = refs_by_table.get(table_index)
        if not table_ref:
            continue
        refs.append(_with_urls({
            "source_type": _local_source_type("table"),
            "file": f"reports/{report['report_id']}/document_full.json",
            "task_id": task_id or report.get("task_id"),
            "pdf_page": table_ref.get("pdf_page_number"),
            "printed_page_number": table_ref.get("printed_page_number"),
            "table_index": table_index,
            "md_line": table_ref.get("md_line"),
        }))
    return _dedupe_refs(refs)


def resolve_report_markdown_refs(
    company_dir: Path,
    file_name: str | None = None,
    line_text: str | None = None,
    table_text: str | None = None,
    page_text: str | None = None,
    metric_text: str | None = None,
    task_id: str | None = None,
    prefer_table_metadata: bool = False,
) -> list[dict[str, Any]]:
    requested_report_id = _report_id_from_file_name(file_name)
    report = primary_report(company_dir, report_id=requested_report_id, file_name=file_name, task_id=task_id)
    report_id = report["report_id"]
    report_rel = file_name if file_name and file_name.startswith("reports/") else f"reports/{report_id}/report.md"
    report_md = company_dir / report_rel
    line_start, line_end = _line_bounds_from_text(line_text)
    cited_pages = _numbers_from_text(page_text)
    table_numbers = set(_numbers_from_text(table_text))
    human_capital_metric = _is_human_capital_metric(metric_text)

    document_full = read_json(report["document_full"], {}) or {}
    printed_pages = _printed_page_numbers_by_pdf_page(document_full)
    records: list[dict[str, Any]] = []
    _collect_line_table_records(document_full, records)
    records = _merge_report_table_records(records, company_dir, report_id)

    page_count = _document_pdf_page_count(document_full)
    if page_count:
        for candidate_line in (page for page in cited_pages if page > page_count):
            if _records_near_report_line(
                records,
                candidate_line,
                candidate_line,
                max_distance=0,
                metric_text=metric_text,
            ):
                line_start = candidate_line
                line_end = candidate_line
                cited_pages = [page for page in cited_pages if page <= page_count]
                break

    markdown_page = _pdf_page_from_markdown_line(report_md, line_start)
    anchor_distance = _markdown_anchor_distance(report_md, line_start)
    coarse_markdown_anchor = anchor_distance is not None and anchor_distance > 200

    selected: list[dict[str, Any]] = []
    if (prefer_table_metadata or human_capital_metric) and human_capital_metric:
        selected = _best_human_capital_records(records)

    if not selected and table_numbers and (prefer_table_metadata or human_capital_metric):
        selected = [
            record
            for record in records
            if _to_int(record.get("table_index")) in table_numbers
        ]
        # report.json keeps the table heading/preview that may be absent from
        # document_full table references, so prefer it when it describes the
        # same table index.
        enriched_selected: list[dict[str, Any]] = []
        for record in selected:
            table_index = _to_int(record.get("table_index"))
            report_record = _record_from_report_table(company_dir, report_id, table_index) if table_index else None
            enriched_selected.append(report_record or record)
        selected = enriched_selected
        if human_capital_metric and selected:
            selected = [record for record in selected if _human_capital_table_score(record) > 0]

    if not selected and (prefer_table_metadata or coarse_markdown_anchor):
        selected = _records_near_report_line(
            records,
            line_start,
            line_end,
            max_distance=0,
            metric_text=metric_text,
        )

    if selected:
        refs: list[dict[str, Any]] = []
        for record in selected:
            table_index = _to_int(record.get("table_index"))
            line = _record_line(record)
            page = _to_int(record.get("pdf_page_number") or record.get("pdf_page"))
            if table_index is None and page is None:
                continue
            refs.append(_with_urls({
                "source_type": _local_source_type("report_table"),
                "file": report_rel,
                "task_id": report.get("task_id"),
                "pdf_page": page,
                "printed_page_number": record.get("printed_page_number") or printed_pages.get(page),
                "table_index": table_index,
                "md_line": line,
            }))
        if refs:
            return _dedupe_refs(refs, limit=6)

    line_content_refs = _content_list_refs_for_report_line(
        document_full,
        _report_line_text(report_md, line_start),
        line_start,
    )
    if line_content_refs:
        refs = []
        for record in line_content_refs:
            page = _to_int(record.get("pdf_page_number") or record.get("pdf_page"))
            refs.append(_with_urls({
                "source_type": record.get("source_type") or _local_source_type("report_text"),
                "file": report_rel,
                "task_id": report.get("task_id"),
                "pdf_page": page,
                "printed_page_number": printed_pages.get(page),
                "table_index": None,
                "md_line": record.get("md_line"),
            }))
        return _dedupe_refs(refs, limit=3)

    # Pure report.md citations use the nearest [PDF_PAGE: n] marker as a text
    # anchor. Structured table citations are resolved above from table metadata.
    pdf_page = markdown_page or (cited_pages[0] if cited_pages else None)
    page_records = [
        record
        for record in records
        if pdf_page is None or _record_page(record) == pdf_page
    ]

    selected = []
    if line_start is not None:
        window_start = line_start - 8
        window_end = (line_end or line_start) + 8
        selected = [
            record
            for record in page_records
            if (line := _record_line(record)) is not None
            and window_start <= line <= window_end
        ]
        if selected:
            metric_tokens = [_normalize(token) for token in _split_metric_tokens(metric_text)]
            selected.sort(
                key=lambda record: (
                    abs((_record_line(record) or line_start) - line_start),
                    0 if _record_matches_metric_text(record, metric_tokens) else 1,
                    _record_line(record) or 10**9,
                    _to_int(record.get("table_index")) or 10**9,
                )
            )
            closest_distance = abs((_record_line(selected[0]) or line_start) - line_start)
            selected = [
                record
                for record in selected
                if abs((_record_line(record) or line_start) - line_start) == closest_distance
            ]

    if not selected and pdf_page is not None and metric_text:
        metric_tokens = [_normalize(token) for token in _split_metric_tokens(metric_text)]
        selected = [
            record
            for record in page_records
            if any(
                token
                and (
                    token in _normalize(record.get("preview"))
                    or token in _normalize(record.get("heading"))
                    or token in _normalize(record.get("title"))
                    or token in _normalize(record.get("canonical_name"))
                )
                for token in metric_tokens
            )
        ]

    if not selected and pdf_page is not None:
        selected = page_records

    if not selected and table_text and pdf_page is None:
        table_numbers = set(_numbers_from_text(table_text))
        selected = [
            record
            for record in records
            if _to_int(record.get("table_index")) in table_numbers
        ]

    refs: list[dict[str, Any]] = []
    for record in selected:
        table_index = _to_int(record.get("table_index"))
        line = _record_line(record)
        # For report.md citations, the markdown marker is the authoritative
        # physical/API page. Never let a conflicting table namespace or a model
        # supplied page override the line anchor.
        page = pdf_page if pdf_page is not None else _to_int(record.get("pdf_page_number") or record.get("pdf_page"))
        if table_index is None and page is None:
            continue
        refs.append(_with_urls({
            "source_type": _local_source_type("report"),
            "file": report_rel,
            "task_id": report.get("task_id"),
            "pdf_page": page,
            "printed_page_number": record.get("printed_page_number") or printed_pages.get(page),
            "table_index": table_index,
            "md_line": line,
        }))

    if refs:
        return _dedupe_refs(refs, limit=6)

    if pdf_page is not None:
        return [_with_urls({
            "source_type": _local_source_type("report"),
            "file": report_rel,
            "task_id": report.get("task_id"),
            "pdf_page": pdf_page,
            "printed_page_number": printed_pages.get(pdf_page),
            "table_index": None,
            "md_line": line_start,
        })]

    return []


def resolve_citation_refs(
    company_text: str,
    metric_text: str | None = None,
    period_text: str | None = None,
    *,
    source_type: str = DEFAULT_SOURCE_TYPE,
    file_name: str = "metrics/three_statements.json",
    table_text: str | None = None,
    line_text: str | None = None,
    page_text: str | None = None,
    evidence_id: str | None = None,
    wiki_base: Path = WIKI_BASE,
) -> dict[str, Any]:
    company_dir = find_company_dir_from_text(company_text, wiki_base)
    if not company_dir:
        return {
            "status": "company_not_found",
            "company_text": company_text,
            "metric": metric_text,
            "period": period_text,
            "refs": [],
            "notes": ["未能从公司名/股票代码定位 wiki 公司目录"],
        }

    refs: list[dict[str, Any]] = []
    evidence_refs: list[dict[str, Any]] = []
    notes: list[str] = []

    if metric_text or evidence_id:
        evidence_refs = resolve_evidence_refs(company_dir, metric_text, period_text, evidence_id, table_text)
        if not evidence_refs and table_text:
            evidence_refs = resolve_evidence_refs(company_dir, metric_text, period_text, evidence_id)
        if not evidence_refs:
            notes.append("该指标在 evidence_index.json 中无独立条目，以下定位来自指定来源或 fallback 解析")

    main_statement_refs = resolve_main_statement_refs(company_dir, metric_text, period_text)

    if main_statement_refs:
        refs = main_statement_refs
    elif file_name.endswith("report.md") or "/report.md" in file_name:
        refs = resolve_report_markdown_refs(company_dir, file_name, line_text, table_text, page_text, metric_text)
    elif source_type in DOCUMENT_LINK_SOURCE_TYPES or "document_links.json" in file_name:
        refs = resolve_document_link_refs(company_dir, metric_text, period_text)
    elif source_type in METRIC_SOURCE_TYPES and "three_statements.json" in file_name:
        refs = resolve_three_statement_refs(company_dir, metric_text, period_text, table_text, line_text)
    elif source_type in METRIC_SOURCE_TYPES and "key_metrics.json" in file_name:
        refs = resolve_key_metric_refs(company_dir, metric_text, period_text)
    elif source_type in EVIDENCE_SOURCE_TYPES or "evidence_index.json" in file_name:
        refs = evidence_refs or resolve_evidence_refs(company_dir, metric_text, period_text, evidence_id, table_text)
    elif source_type in SEMANTIC_SOURCE_TYPES or "evidence_semantic.json" in file_name:
        refs = resolve_semantic_refs(company_dir, metric_text, period_text, evidence_id, table_text)

    if not refs and table_text:
        refs = resolve_table_refs(company_dir, table_text)
    if not refs and _question_prefers_detail(metric_text):
        refs = resolve_document_link_refs(company_dir, metric_text, period_text)
    if not refs and evidence_refs:
        refs = evidence_refs

    report = primary_report(company_dir, file_name=file_name)
    return {
        "status": "ok" if refs else "no_refs",
        "company_id": company_dir.name,
        "report_id": report.get("report_id"),
        "task_id": report.get("task_id"),
        "metric": metric_text,
        "period": period_text,
        "source_type": source_type,
        "file": file_name,
        "evidence_index_hit": bool(evidence_refs),
        "notes": notes,
        "refs": refs,
    }


def collect_company_evidence_refs(
    company_dir: Path,
    metric_text: str | None = None,
    period_text: str | None = None,
    limit: int = 24,
) -> list[dict[str, Any]]:
    refs = resolve_evidence_refs(company_dir, metric_text, period_text, limit=limit)
    if len(refs) < limit:
        refs.extend(resolve_key_metric_refs(company_dir, metric_text, period_text))
    if len(refs) < limit:
        refs.extend(resolve_semantic_refs(company_dir, metric_text, period_text, limit=limit))
    return _dedupe_refs(refs, limit)


def resolve_analysis_refs(company_dir: Path, file_name: str | None = None) -> list[dict[str, Any]]:
    analysis_dir = company_dir / "analysis"
    candidates = []
    if file_name:
        candidates.append(analysis_dir / Path(file_name).name)
    candidates.extend(sorted(analysis_dir.glob("*.md")))

    path = next((item for item in candidates if item.exists() and item.name != "README.md"), None)
    if not path:
        return []

    refs: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        task_match = re.search(r"(?:evidence_id/)?task_id=([0-9a-fA-F-]{32,36})", line)
        page_match = re.search(r"pdf_page(?:_number)?=([0-9]+)", line)
        table_match = re.search(r"table_index=([0-9]+)", line)
        md_line_match = re.search(r"md_line=([0-9]+)", line)
        if not task_match or not page_match:
            continue
        ref = {
            "source_type": _local_source_type("analysis"),
            "file": str(path.relative_to(company_dir)),
            "task_id": task_match.group(1),
            "pdf_page": int(page_match.group(1)),
            "table_index": int(table_match.group(1)) if table_match else None,
            "md_line": int(md_line_match.group(1)) if md_line_match else None,
        }
        key = (ref["task_id"], ref["pdf_page"], ref.get("table_index"), ref.get("md_line"))
        if key in seen:
            continue
        seen.add(key)
        refs.append(_with_urls(ref))
    return refs


def format_ref_summary(refs: list[dict[str, Any]]) -> str:
    if not refs:
        return "证据链不完整：未解析到 PDF 页码"
    pieces = []
    for ref in refs:
        page = ref.get("pdf_page") or "未返回"
        table = ref.get("table_index") or "未返回"
        line = ref.get("md_line") or "未返回"
        pieces.append(f"pdf_page={page}, table_index={table}, md_line={line}")
    return "；".join(dict.fromkeys(pieces))


def _unique_values(refs: list[dict[str, Any]], key: str) -> list[str]:
    values = []
    for ref in refs:
        value = ref.get(key)
        if value is None or value == "":
            continue
        values.append(str(value))
    return list(dict.fromkeys(values))


def _citation_field_value_pattern(name: str) -> str:
    if name in {"pdf_page", "pdf_page_number", "table_index"}:
        return r"(未返回|N/A|None|null|[0-9]+(?:\s*(?:[-,，])\s*[0-9]+)*)"
    if name in {"printed_page", "printed_page_number"}:
        return (
            r"([^,，。.;；\n]+"
            r"(?:\s*[,，]\s*(?!\s*(?:task_id|pdf_page(?:_number)?|printed_page(?:_number)?|table_index|md_line|source_type|file|metric|period|evidence_id|quote)=)"
            r"[^,，。.;；\n]+)*)"
        )
    if name == "md_line":
        return r"(未返回|N/A|None|null|[0-9]+(?:\s*(?:[-~～/,，])\s*[0-9]+)*)"
    return r"(未返回|N/A|None|null|[^,，\n]+)"


def _replace_or_append_field(line: str, names: tuple[str, ...], value: str) -> str:
    if not value:
        return line
    for name in names:
        pattern = rf"({re.escape(name)}=){_citation_field_value_pattern(name)}"
        if re.search(pattern, line):
            return re.sub(pattern, rf"\g<1>{value}", line, count=1)
    trailing = re.match(r"^(?P<body>.*?)(?P<tail>[。.;；])?$", line, flags=re.DOTALL)
    if trailing:
        body = trailing.group("body")
        tail = trailing.group("tail") or ""
        return f"{body}, {names[0]}={value}{tail}"
    return f"{line}, {names[0]}={value}"


def _line_has_complete_trace(line: str) -> bool:
    has_task = re.search(r"\b(?:evidence_id/)?task_id=[0-9a-fA-F-]{32,36}\b", line)
    has_page = re.search(r"\bpdf_page(?:_number)?=[0-9]+", line)
    return bool(has_task and has_page)


def _line_has_complete_table_trace(line: str) -> bool:
    return bool(
        _line_has_complete_trace(line)
        and re.search(r"\btable_index=[0-9]+(?:\b|[。.;；，,])", line)
        and re.search(r"\bmd_line=[0-9]+(?:\b|[。.;；，,])", line)
    )


def _line_has_resolved_printed_page(line: str) -> bool:
    match = re.search(r"\bprinted_page(?:_number)?=([^,，。.;；\n]+)", line)
    if not match:
        return False
    return match.group(1).strip() not in {"", "未返回", "N/A", "None", "null"}


def _numbers_from_citation_field(line: str, names: tuple[str, ...]) -> set[int]:
    for name in names:
        match = re.search(rf"\b{re.escape(name)}=([^,，\n]+(?:\s*[,，]\s*[0-9]+)*)", line)
        if match:
            return set(_numbers_from_text(match.group(1)))
    return set()


def _enrich_complete_document_link_printed_page(line: str, wiki_base: Path = WIKI_BASE) -> str:
    source_match = re.search(r"source_type=([^,，]+)", line)
    file_match = re.search(r"file=([^,，]+)", line)
    source_type = source_match.group(1).strip() if source_match else ""
    file_name = file_match.group(1).strip() if file_match else ""
    is_document_link = source_type in DOCUMENT_LINK_SOURCE_TYPES or "document_links.json" in file_name
    if not is_document_link or not _line_has_complete_table_trace(line) or _line_has_resolved_printed_page(line):
        return line

    task_match = re.search(r"\b(?:evidence_id/)?task_id=([0-9a-fA-F-]{32,36})\b", line)
    pages = _numbers_from_citation_field(line, ("pdf_page", "pdf_page_number"))
    tables = _numbers_from_citation_field(line, ("table_index",))
    md_lines = _numbers_from_citation_field(line, ("md_line",))
    if not task_match or len(pages) != 1 or len(tables) != 1 or len(md_lines) != 1:
        return line

    task_id = task_match.group(1)
    company_dir = _company_task_index(wiki_base).get(task_id)
    if not company_dir:
        return line
    report = primary_report(company_dir, task_id=task_id)
    table_ref = table_ref_map(company_dir, report["report_id"]).get(next(iter(tables)))
    if not table_ref:
        return line

    pdf_page = next(iter(pages))
    md_line = next(iter(md_lines))
    if _to_int(table_ref.get("pdf_page_number")) != pdf_page or _to_int(table_ref.get("md_line")) != md_line:
        return line
    printed_page = str(table_ref.get("printed_page_number") or "").strip()
    if not printed_page:
        return line
    return _replace_or_append_field(line, ("printed_page", "printed_page_number"), printed_page)


def _filter_refs_by_line_trace(line: str, refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pages = _numbers_from_citation_field(line, ("pdf_page", "pdf_page_number"))
    tables = _numbers_from_citation_field(line, ("table_index",))
    lines = _numbers_from_citation_field(line, ("md_line",))
    if not (pages or tables or lines):
        return []
    return [
        ref
        for ref in refs
        if (not pages or _to_int(ref.get("pdf_page")) in pages)
        and (not tables or _to_int(ref.get("table_index")) in tables)
        and (not lines or _to_int(ref.get("md_line")) in lines)
    ]


def _is_tool_source(source_type: str, file_name: str) -> bool:
    return source_type in TOOL_SOURCE_TYPES or any(name in file_name for name in TOOL_FILE_NAMES)


def _citation_number_from_line(line: str) -> int | None:
    match = re.match(r"\s*\[([0-9]+)\]", line)
    return int(match.group(1)) if match else None


def _context_citation_lines(context_text: str) -> dict[int, str]:
    lines: dict[int, str] = {}
    for context_line in context_text.splitlines():
        number = _citation_number_from_line(context_line)
        if number is not None:
            lines[number] = context_line
    return lines


def _citation_field_text(line: str, names: tuple[str, ...]) -> str:
    for name in names:
        match = re.search(rf"\b{re.escape(name)}=([^,，。.;；\n]+(?:\s*[,，]\s*[0-9]+)*)", line)
        if match:
            return match.group(1).strip()
    return ""


def _single_or_index(values: list[int], index: int) -> int | None:
    if not values:
        return None
    return values[index] if index < len(values) else values[-1]


def _trace_refs_from_citation_line(line: str) -> list[dict[str, Any]]:
    source_type = _citation_field_text(line, ("source_type",))
    file_name = _citation_field_text(line, ("file",))
    if _is_tool_source(source_type, file_name):
        return []
    task_match = re.search(r"\b(?:evidence_id/)?task_id=([0-9a-fA-F-]{32,36})", line)
    if not task_match:
        return []
    pages = sorted(_numbers_from_citation_field(line, ("pdf_page", "pdf_page_number")))
    tables = sorted(_numbers_from_citation_field(line, ("table_index",)))
    md_lines = sorted(_numbers_from_citation_field(line, ("md_line",)))
    if not pages:
        return []
    count = max(len(pages), len(tables), len(md_lines), 1)
    refs = []
    for index in range(count):
        ref = {
            "source_type": source_type,
            "file": file_name,
            "metric": _citation_field_text(line, ("metric",)),
            "period": _citation_field_text(line, ("period",)),
            "task_id": task_match.group(1),
            "pdf_page": _single_or_index(pages, index),
            "table_index": _single_or_index(tables, index),
            "md_line": _single_or_index(md_lines, index),
        }
        refs.append(_with_urls(ref))
    return refs


def _tool_bound_citation_numbers(line: str) -> list[int]:
    current = _citation_number_from_line(line)
    numbers = []
    for match in re.finditer(r"\[([0-9]+)\]", line):
        number = int(match.group(1))
        if number != current:
            numbers.append(number)
    return list(dict.fromkeys(numbers))


def _borrow_tool_refs_from_context(line: str, context_text: str) -> list[dict[str, Any]]:
    line_map = _context_citation_lines(context_text)
    numbers = _tool_bound_citation_numbers(line)
    if not numbers:
        current = _citation_number_from_line(line)
        numbers = [number for number in sorted(line_map) if current is None or number < current][-4:]
    refs: list[dict[str, Any]] = []
    for number in numbers:
        refs.extend(_trace_refs_from_citation_line(line_map.get(number, "")))
    return _dedupe_refs(refs, limit=12)


def _apply_trace_refs_to_line(line: str, refs: list[dict[str, Any]]) -> str:
    task_ids = _unique_values(refs, "task_id")
    pages = _unique_values(refs, "pdf_page")
    printed_pages = _unique_values(refs, "printed_page_number")
    tables = _unique_values(refs, "table_index")
    lines = _unique_values(refs, "md_line")
    line = _replace_or_append_field(line, ("evidence_id/task_id", "task_id"), task_ids[0] if task_ids else "")
    line = _replace_or_append_field(line, ("pdf_page", "pdf_page_number"), ",".join(pages))
    line = _replace_or_append_field(line, ("printed_page", "printed_page_number"), ",".join(printed_pages))
    line = _replace_or_append_field(line, ("table_index",), ",".join(tables))
    line = _replace_or_append_field(line, ("md_line",), ",".join(lines))
    return line


def _should_resolve_citation_line(line: str) -> bool:
    file_match = re.search(r"file=([^,，]+)", line)
    source_match = re.search(r"source_type=([^,，]+)", line)
    metric_match = re.search(r"metric=([^,，]+)", line)
    file_name = file_match.group(1).strip() if file_match else ""
    source_type = source_match.group(1).strip() if source_match else ""
    metric_text = metric_match.group(1).strip() if metric_match else ""
    if (
        source_type in DOCUMENT_LINK_SOURCE_TYPES
        or "document_links.json" in file_name
    ) and _line_has_complete_table_trace(line):
        if _main_statement_type_from_text(metric_text):
            return True
        return False
    if "未返回" in line or not _line_has_complete_trace(line):
        return True
    if re.search(r"\bmd_line=", line):
        return True
    if "/api/pdf_page/" in line or "/api/source/" in line:
        return True
    if any(
        name in file_name
        for name in (
            "report.md",
            "key_metrics.json",
            "three_statements.json",
            "evidence_index.json",
            "evidence_semantic.json",
            "document_links.json",
        )
    ):
        return True
    return source_type in {
        "report_md",
        "report_markdown",
        "wiki_report",
        "wiki_report_table",
        "okf_report",
        "okf_report_table",
        "wiki_metrics",
        "okf_metrics",
        "wiki_evidence",
        "okf_evidence",
        "wiki_semantic",
        "okf_semantic",
        "okf_document_links",
        "semantic",
        "semantic_evidence",
        "wiki_analysis",
        "okf_analysis",
    }


def enrich_citation_line(line: str, context_text: str, wiki_base: Path = WIKI_BASE) -> str:
    """Fill task_id/pdf_page/table_index/md_line for local wiki citation lines."""
    line = _enrich_complete_document_link_printed_page(line, wiki_base)
    if not _should_resolve_citation_line(line):
        return line

    source_match = re.search(r"source_type=([^,，]+)", line)
    file_match = re.search(r"file=([^,，]+)", line)
    metric_match = re.search(r"metric=([^,，]+)", line)
    period_match = re.search(r"period=([^,，]+)", line)
    evidence_match = re.search(r"evidence_id=([^,，\s]+)", line)
    task_match = re.search(r"\b(?:evidence_id/)?task_id=([0-9a-fA-F-]{32,36})", line)
    table_match = re.search(r"\btable_index=([^,，\n]+)", line)

    source_type = source_match.group(1).strip() if source_match else ""
    file_name = file_match.group(1).strip() if file_match else ""
    metric_text = metric_match.group(1).strip() if metric_match else ""
    period_text = period_match.group(1).strip() if period_match else ""
    evidence_id = evidence_match.group(1).strip() if evidence_match else ""
    task_id = task_match.group(1).strip() if task_match else ""
    table_text = table_match.group(1).strip() if table_match else ""

    if _is_tool_source(source_type, file_name):
        borrowed_refs = _borrow_tool_refs_from_context(line, context_text)
        if borrowed_refs:
            return _apply_trace_refs_to_line(line, borrowed_refs)

    company_dir = _company_task_index(wiki_base).get(task_id) if task_id else None
    if task_id and not company_dir:
        # An explicit task id is an identity boundary.  Never replace it with
        # another company inferred from surrounding multi-company context.
        return line
    if not company_dir:
        company_dir = find_company_dir_from_text(line, wiki_base)
    if not company_dir:
        company_dir = find_company_dir_from_text(context_text, wiki_base)
    if not company_dir:
        return line

    refs: list[dict[str, Any]] = []
    line_match = re.search(r"\bmd_line=([^,，\n]+)", line)
    line_text = line_match.group(1).strip() if line_match else ""
    page_match = re.search(r"\bpdf_page(?:_number)?=([^,，\n]+)", line)
    page_text = page_match.group(1).strip() if page_match else ""
    main_statement_type = _main_statement_type_from_text(metric_text)
    explicit_report_md = (
        file_name.endswith("report.md")
        or "/report.md" in file_name
        or source_type in REPORT_MD_SOURCE_TYPES
    )

    if _is_tool_source(source_type, file_name):
        refs = _borrow_tool_refs_from_context(line, context_text)

    if not refs and explicit_report_md:
        refs = resolve_report_markdown_refs(
            company_dir,
            file_name,
            line_text,
            table_text,
            page_text,
            metric_text,
            task_id,
            prefer_table_metadata=source_type in {"wiki_report_table", "okf_report_table"},
        )
    elif main_statement_type:
        refs = resolve_main_statement_refs(company_dir, metric_text, period_text)
        if source_type in METRIC_SOURCE_TYPES and "three_statements.json" in file_name:
            matching_refs = _filter_refs_by_line_trace(line, refs)
            if matching_refs:
                refs = matching_refs
    elif source_type in METRIC_SOURCE_TYPES and "three_statements.json" in file_name:
        refs = resolve_three_statement_refs(company_dir, metric_text, period_text, table_text, line_text)
    elif source_type in METRIC_SOURCE_TYPES and "key_metrics.json" in file_name:
        refs = resolve_key_metric_refs(company_dir, metric_text, period_text)
    elif source_type in ANALYSIS_SOURCE_TYPES:
        refs = resolve_analysis_refs(company_dir, file_name)
    elif source_type in DOCUMENT_LINK_SOURCE_TYPES or "document_links.json" in file_name:
        refs = resolve_document_link_refs(company_dir, metric_text, period_text)
    elif source_type in EVIDENCE_SOURCE_TYPES or "evidence_index.json" in file_name:
        refs = resolve_evidence_refs(company_dir, metric_text, period_text, evidence_id, table_text)
    elif source_type in SEMANTIC_SOURCE_TYPES or "evidence_semantic.json" in file_name:
        refs = resolve_semantic_refs(company_dir, metric_text, period_text, evidence_id, table_text)

    if not refs and not main_statement_type and line_text:
        refs = resolve_report_markdown_refs(company_dir, None, line_text, None, page_text, metric_text)
    if not refs and not main_statement_type and table_text:
        refs = resolve_table_refs(company_dir, table_text, task_id)
    if not refs and not main_statement_type and _question_prefers_detail(metric_text):
        refs = resolve_document_link_refs(company_dir, metric_text, period_text)

    if not refs:
        report = primary_report(company_dir)
        task_id = report.get("task_id")
        return _replace_or_append_field(line, ("evidence_id/task_id", "task_id"), task_id) if task_id else line

    task_ids = _unique_values(refs, "task_id")
    pages = _unique_values(refs, "pdf_page")
    printed_pages = _unique_values(refs, "printed_page_number")
    tables = _unique_values(refs, "table_index")
    lines = _unique_values(refs, "md_line")

    if main_statement_type and not explicit_report_md:
        line = _replace_or_append_field(line, ("source_type",), _local_source_type("metrics", wiki_base))
        line = _replace_or_append_field(line, ("file",), "metrics/three_statements.json")
        line = _replace_or_append_field(line, ("metric",), MAIN_STATEMENT_LABELS[main_statement_type])
    line = _replace_or_append_field(line, ("evidence_id/task_id", "task_id"), task_ids[0] if task_ids else "")
    line = _replace_or_append_field(line, ("pdf_page", "pdf_page_number"), ",".join(pages))
    line = _replace_or_append_field(line, ("printed_page", "printed_page_number"), ",".join(printed_pages))
    line = _replace_or_append_field(line, ("table_index",), ",".join(tables))
    line = _replace_or_append_field(line, ("md_line",), ",".join(lines))
    return line


def _format_citation_lines(result: dict[str, Any]) -> str:
    refs = result.get("refs") or []
    if not refs:
        note = "；".join(result.get("notes") or []) or "未解析到可打开来源"
        return f"证据链不完整：{note}"

    lines = []
    for idx, ref in enumerate(refs, start=1):
        page = ref.get("pdf_page") or "未返回"
        printed_page = ref.get("printed_page_number") or "未返回"
        table = ref.get("table_index") or "未返回"
        line = ref.get("md_line") or "未返回"
        task_id = ref.get("task_id") or result.get("task_id") or "未返回"
        pieces = [
            f"[{idx}] source_type={ref.get('source_type') or result.get('source_type')}",
            f"file={ref.get('file') or result.get('file')}",
            f"metric={ref.get('metric') or result.get('metric') or '未返回'}",
            f"period={ref.get('period') or result.get('period') or '未返回'}",
            f"task_id={task_id}",
            f"pdf_page={page}",
            f"printed_page={printed_page}",
            f"table_index={table}",
            f"md_line={line}",
        ]
        links = []
        if ref.get("open_pdf_page_url"):
            links.append(f"[打开PDF页]({ref['open_pdf_page_url']})")
        if ref.get("open_source_page_url"):
            links.append(f"[查看页来源]({ref['open_source_page_url']})")
        if ref.get("open_source_table_url"):
            links.append(f"[查看表格]({ref['open_source_table_url']})")
        lines.append(", ".join(pieces) + (("，" + "，".join(links)) if links else ""))
    notes = result.get("notes") or []
    if notes:
        lines.append("说明：" + "；".join(notes))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Resolve local wiki citation refs into task_id/pdf_page/table_index URLs.")
    parser.add_argument("--company", required=True, help="公司简称、股票代码或 company_id")
    parser.add_argument("--metric", default="", help="指标名/事项名，如 商誉")
    parser.add_argument("--period", default="", help="报告期，如 2025 或 2025-12-31")
    parser.add_argument("--source-type", default=DEFAULT_SOURCE_TYPE)
    parser.add_argument("--file", default="metrics/three_statements.json")
    parser.add_argument("--table-index", default="", help="已有 table_index，可为逗号列表")
    parser.add_argument("--md-line", default="", help="Markdown 行号或行号区间，如 4180-4196")
    parser.add_argument("--pdf-page", default="", help="已有 PDF 页码")
    parser.add_argument("--evidence-id", default="")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    args = parser.parse_args(argv)

    result = resolve_citation_refs(
        args.company,
        args.metric,
        args.period,
        source_type=args.source_type,
        file_name=args.file,
        table_text=args.table_index,
        line_text=args.md_line,
        page_text=args.pdf_page,
        evidence_id=args.evidence_id,
    )
    if args.format == "markdown":
        print(_format_citation_lines(result))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("refs") else 1


if __name__ == "__main__":
    raise SystemExit(main())
