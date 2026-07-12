#!/usr/bin/env python3
"""Validate SIQ v1.1 report artifacts before declaring success."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


TEMPLATE_JSON = Path(__file__).resolve().parent.parent / "templates" / "siq_analysis_report_v1.1.json"


def load_template() -> dict[str, Any]:
    return json.loads(TEMPLATE_JSON.read_text(encoding="utf-8"))


def template_section_ids() -> list[str]:
    template = load_template()
    sections = sorted(template.get("sections", []), key=lambda item: item.get("order", 0))
    return [str(item["section_id"]) for item in sections]


SECTION_IDS = template_section_ids()

PLACEHOLDER_RE = re.compile(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}")
TRACEABLE_MISSING_PAGE_RE = re.compile(r"pdf_page(?:_number)?=未返回(?:\([^)]*\))?.*?table_index=([0-9]+)")
FINANCIAL_TABLE_CONTAMINATION_RE = re.compile(
    r"^\|\s*(营业收入|归母净利润|扣非归母净利润|经营现金流|总资产/净资产|总资产|总负债|毛利率)"
    r"\s*\|[^\n]*(metrics/|task_id|pdf_page|table_index|[0-9a-fA-F]{8}\.\.\.)",
    re.M,
)
VISIBLE_NEGATIVE_ORDINARY_EXPENSE_RE = re.compile(
    r"(营业总成本|营业成本|税金及附加|销售费用|管理费用|研发费用|营业外支出|所得税费用)"
    r"\s*(?:为|是|：|:)\s*[-−－]\s*\d"
)
GENERIC_MISSING_CITATION_RE = re.compile(r"pdf_page(?:_number)?=未返回.*?table_index=未返回")
MISMATCHED_H3_RE = re.compile(r"^###\s+(\d+)\.(\d+)\s+", re.M)
API_LINK_RE = re.compile(r"\]\((/api/(?:pdf_page|source)/[^()\s]+?)\)")
HTML_API_ANCHOR_RE = re.compile(
    r"<a\b(?=[^>]*\bhref=[\"'][^\"']*/api/(?:pdf_page|source)/[^\"']*[\"'])([^>]*)>",
    re.I,
)
INVALID_PROVENANCE_TOKEN_RE = re.compile(
    r"(?:/api/(?:pdf_page|source)/[^\"')<\s]*(?:/None|/null|/unknown|/未返回)|:p(?:None|null|unknown|未返回))",
    re.I,
)
MECHANICAL_H3_LABELS = {"事实", "计算", "判断", "风险/改善条件", "风险 / 改善条件"}
HTML_SIDEBAR_NAV_MARKERS = (
    "nav-sidebar",
    "nav-toggle",
    "with-sidebar",
    "nav-item",
)
CORE_METRIC_ALIASES = {
    "营业收入": ["operating_revenue"],
    "归母净利润": ["parent_net_profit", "net_profit_parent"],
    "扣非归母净利润": ["deducted_parent_net_profit"],
    "经营现金流": ["operating_cash_flow_net", "net_operating_cash_flow"],
    "总资产": ["total_assets"],
    "归母净资产": ["equity_attributable_parent"],
}
REQUIRED_SECTION_EVIDENCE_MIN = 10
REQUIRED_QUALITY_TRUE_FLAGS = {
    "section_order_valid": "quality_report_section_order_false",
    "all_key_numbers_have_evidence": "quality_report_key_numbers_without_evidence",
    "wiki_inventory_complete": "quality_report_wiki_inventory_missing",
}
FACTCHECK_PATH_KEYS = (
    "factcheck_json",
    "factcheck_path",
    "factcheck_report_path",
    "factcheck_report_json",
)
FACTCHECK_APPROVE_VERDICTS = {"approve", "approved", "pass", "passed"}
FACTCHECK_REVIEW_VERDICTS = {"request_changes", "needs_changes", "review_required"}
FACTCHECK_BLOCK_VERDICTS = {"block", "blocked", "fail", "failed"}
CRITICAL_REVIEW_TERMS = (
    "毛利率",
    "资本开支",
    "短期有息负债",
    "利息费用",
    "市值数据",
    "同业样本未聚合",
    "治理合规章节需补充",
)
AUTOMOTIVE_TEMPLATE_RESIDUE_TERMS = (
    "广汽这类",
    "汽车类公司",
    "单车盈利",
    "车型放量",
    "合资品牌",
    "自主新能源",
)
AUTOMOTIVE_INDUSTRY_TERMS = (
    "汽车",
    "整车",
    "乘用车",
    "商用车",
    "新能源汽车",
    "新能源车",
    "车企",
    "主机厂",
    "汽车零部件",
    "汽车服务",
    "汽车经销",
    "automotive",
)
AUTOMOTIVE_COMPANY_NAME_TERMS = (
    "比亚迪",
    "赛力斯",
    "长安汽车",
    "长城汽车",
    "上汽集团",
    "广汽集团",
    "江淮汽车",
    "东风汽车",
    "北汽",
    "一汽",
    "吉利汽车",
    "理想汽车",
    "小鹏汽车",
    "蔚来",
    "零跑汽车",
)
AUTOMOTIVE_PEER_TERMS = tuple(
    sorted(set(AUTOMOTIVE_COMPANY_NAME_TERMS + ("上汽集团", "广汽集团", "长安汽车", "长城汽车", "江淮汽车")))
)
DIAGNOSIS_BLOCK_TITLE_TERMS = ("诊断", "判断", "结论", "评估", "解释", "核心", "观察")
DIAGNOSIS_BLOCK_ROLES = {"diagnosis", "analysis", "bridge"}
TEMPLATE_INSTRUCTION_TERMS = (
    "必须",
    "需要",
    "不得",
    "不能",
    "避免",
    "禁止",
    "应当",
    "应该",
    "优先看",
    "重点看",
    "要把",
    "要按",
    "需补充",
    "待补",
)
GENERIC_TEMPLATE_PHRASES = (
    "本节",
    "章节",
    "报告必须",
    "分析必须",
    "执行摘要必须",
    "盈利章节必须",
    "偿债章节需要",
    "资产质量的重点是",
    "当前结论是公开年报财务诊断",
)
CORE_DIAGNOSIS_ANCHORS = (
    "营业收入",
    "收入",
    "归母净利润",
    "扣非",
    "经营现金流",
    "毛利率",
    "净利率",
    "费用率",
    "资产负债率",
    "ROE",
    "自由现金流",
    "短债",
    "资本开支",
    "应收",
    "存货",
    "市值",
    "估值",
    "现金",
    "利润",
)
CORE_DIAGNOSIS_SIGNALS = (
    "承压",
    "改善",
    "恶化",
    "修复",
    "背离",
    "高于",
    "低于",
    "弱于",
    "强于",
    "不足",
    "风险",
    "压力",
    "安全",
    "质量",
    "弹性",
    "可持续",
    "侵蚀",
    "依赖",
    "匹配",
    "覆盖",
    "转负",
    "亏损",
    "下降",
    "上升",
    "放缓",
    "加快",
    "收窄",
    "扩大",
    "拖累",
)
RAW_URL_RE = re.compile(r"https?://[^\s)\]\"'<>，。；、]+")
SEARCH_PROVIDER_SNIPPET_RE = re.compile(r"^\s*(?:[-*]\s*)?(?:tavily|exa)\s*:.{40,}-.{40,}", re.I)
SEARCH_JSONISH_LINE_RE = re.compile(r"\b(?:provider|snippet|published_date|score|results?)\b", re.I)
INDUSTRY_RESEARCH_PROVIDER_DETAIL_RE = re.compile(r"industry_research:(?:tavily|exa):\d+:https?://", re.I)


def load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(load_text(path))


def template_id(data: dict[str, Any]) -> str | None:
    template = data.get("template")
    if isinstance(template, dict):
        return template.get("template_id")
    return data.get("template_id")


def report_template_payload(data: dict[str, Any]) -> dict[str, Any]:
    template = data.get("template")
    return template if isinstance(template, dict) else {}


def section_ids(data: dict[str, Any]) -> list[str]:
    sections = data.get("sections")
    if not isinstance(sections, list):
        return []
    ids = []
    for section in sections:
        if isinstance(section, dict):
            ids.append(str(section.get("section_id", "")))
    return ids


def sections_payload(data: dict[str, Any]) -> list[dict[str, Any]]:
    sections = data.get("sections")
    if not isinstance(sections, list):
        return []
    return [section for section in sections if isinstance(section, dict)]


def iter_strings(obj: Any) -> list[str]:
    strings: list[str] = []
    if isinstance(obj, dict):
        for value in obj.values():
            strings.extend(iter_strings(value))
    elif isinstance(obj, list):
        for item in obj:
            strings.extend(iter_strings(item))
    elif isinstance(obj, str):
        strings.append(obj)
    return strings


def infer_company_dir(prefix: Path) -> Path | None:
    parts = prefix.resolve().parts
    if "companies" not in parts:
        return None
    idx = parts.index("companies")
    if idx + 1 >= len(parts):
        return None
    return Path(*parts[: idx + 2])


def load_company_context(prefix: Path) -> tuple[dict[str, Any] | None, str | None]:
    company_dir = infer_company_dir(prefix)
    if not company_dir:
        return None, "company_industry_unavailable:no_company_dir"
    path = company_dir / "company.json"
    if not path.exists():
        return None, "company_industry_unavailable:company_json_missing"
    try:
        return load_json(path), None
    except (OSError, json.JSONDecodeError):
        return None, "company_industry_unavailable:company_json_unreadable"


def report_year(data: dict[str, Any], prefix: Path) -> str | None:
    containers = [data, data.get("report_meta"), data.get("preflight"), data.get("quality_report")]
    for container in containers:
        if not isinstance(container, dict):
            continue
        for key in ("report_year", "year", "fiscal_year"):
            value = container.get(key)
            if isinstance(value, int) and 1900 <= value <= 2100:
                return str(value)
            if isinstance(value, str) and re.fullmatch(r"20\d{2}|19\d{2}", value.strip()):
                return value.strip()
    match = re.search(r"(?:^|[-_])(20\d{2}|19\d{2})(?:[-_]|$)", prefix.name)
    return match.group(1) if match else None


def normalize_factcheck_verdict(value: Any) -> str:
    verdict = str(value or "").strip().lower()
    if not verdict:
        return "missing"
    if verdict in FACTCHECK_APPROVE_VERDICTS:
        return "approve"
    if verdict in FACTCHECK_REVIEW_VERDICTS:
        return "request_changes"
    if verdict in FACTCHECK_BLOCK_VERDICTS:
        return "block"
    return "unknown"


def factcheck_path_candidates(prefix: Path, data: dict[str, Any]) -> list[Path]:
    candidates: list[Path] = []
    containers = [data, data.get("quality_report"), data.get("factcheck")]
    for container in containers:
        if not isinstance(container, dict):
            continue
        for key in FACTCHECK_PATH_KEYS:
            value = container.get(key)
            if not isinstance(value, str) or not value.strip():
                continue
            path = Path(value.strip())
            candidates.append(path if path.is_absolute() else (prefix.parent / path))

    company_dir = infer_company_dir(prefix)
    if company_dir:
        factcheck_dir = company_dir / "factcheck"
        if factcheck_dir.exists():
            year = report_year(data, prefix)
            pattern = f"*-{year}-factcheck.json" if year else "*-factcheck.json"
            candidates.extend(sorted(factcheck_dir.glob(pattern), key=lambda item: (item.stat().st_mtime, item.name), reverse=True))

    unique: list[Path] = []
    seen: set[Path] = set()
    for path in candidates:
        resolved = path.resolve()
        if resolved not in seen:
            unique.append(resolved)
            seen.add(resolved)
    return unique


def factcheck_gate(prefix: Path, data: dict[str, Any]) -> dict[str, Any]:
    embedded = data.get("factcheck")
    quality = data.get("quality_report")
    if not isinstance(embedded, dict) and isinstance(quality, dict):
        embedded = quality.get("factcheck")
    if isinstance(embedded, dict) and "verdict" in embedded:
        normalized = normalize_factcheck_verdict(embedded.get("verdict"))
        return {"verdict": normalized, "raw_verdict": embedded.get("verdict"), "source": "embedded"}

    for path in factcheck_path_candidates(prefix, data):
        if not path.exists():
            continue
        try:
            payload = load_json(path)
        except (OSError, json.JSONDecodeError) as exc:
            return {
                "verdict": "unavailable",
                "raw_verdict": None,
                "source": str(path),
                "warning": f"factcheck_verdict_unreadable:{path.name}:{exc.__class__.__name__}",
            }
        normalized = normalize_factcheck_verdict(payload.get("verdict"))
        return {"verdict": normalized, "raw_verdict": payload.get("verdict"), "source": str(path)}

    return {"verdict": "missing", "raw_verdict": None, "source": None, "warning": "factcheck_verdict_missing"}


def publication_gate(failures: list[str], warnings: list[str], factcheck: dict[str, Any]) -> dict[str, Any]:
    contract_pass = not failures
    factcheck_verdict = str(factcheck.get("verdict") or "missing")
    publish_ready = contract_pass and not warnings and factcheck_verdict == "approve"
    pass_with_review = contract_pass and not publish_ready
    if not contract_pass:
        status = "blocked"
    elif publish_ready:
        status = "publish_ready"
    else:
        status = "pass_with_review"
    return {
        "contract_pass": contract_pass,
        "publish_ready": publish_ready,
        "pass_with_review": pass_with_review,
        "publication_status": status,
    }


def company_text(company: dict[str, Any] | None) -> str:
    if not isinstance(company, dict):
        return ""
    return "\n".join(iter_strings(company))


def is_automotive_company(company: dict[str, Any] | None) -> bool | None:
    if not isinstance(company, dict):
        return None
    text = company_text(company)
    text_lower = text.lower()
    return any(term in text for term in AUTOMOTIVE_INDUSTRY_TERMS if term != "automotive") or any(
        term in text_lower for term in AUTOMOTIVE_INDUSTRY_TERMS if term == "automotive"
    ) or any(term in text for term in AUTOMOTIVE_COMPANY_NAME_TERMS)


def report_company_terms(data: dict[str, Any], company: dict[str, Any] | None) -> list[str]:
    terms: list[str] = []
    containers = [company, data.get("report_meta"), data.get("preflight")]
    for container in containers:
        if not isinstance(container, dict):
            continue
        for key in ["company_short_name", "company_full_name", "company_id", "stock_code"]:
            value = container.get(key)
            if value:
                terms.append(str(value))
        aliases = container.get("aliases")
        if isinstance(aliases, list):
            terms.extend(str(item) for item in aliases if str(item).strip())
    expanded: list[str] = []
    for term in terms:
        expanded.append(term)
        if "-" in term:
            expanded.extend(part for part in term.split("-", 1) if part)
    return sorted({term.strip() for term in expanded if len(term.strip()) >= 2})


def is_template_instruction_sentence(text: str, company_terms: list[str]) -> bool:
    compact = re.sub(r"\s+", "", text)
    if not compact:
        return True
    has_company_or_number = any(term and term in text for term in company_terms) or bool(re.search(r"\d", text))
    if any(phrase in text for phrase in GENERIC_TEMPLATE_PHRASES):
        return True
    if any(term in text for term in TEMPLATE_INSTRUCTION_TERMS) and not has_company_or_number:
        return True
    return False


def is_specific_core_diagnosis(text: str, company_terms: list[str]) -> bool:
    compact = re.sub(r"\s+", "", text)
    if len(compact) < 28:
        return False
    if is_template_instruction_sentence(text, company_terms):
        return False
    has_anchor = (
        any(term and term in text for term in company_terms)
        or bool(re.search(r"\d", text))
        or any(term in text for term in CORE_DIAGNOSIS_ANCHORS)
    )
    has_signal = any(term in text for term in CORE_DIAGNOSIS_SIGNALS)
    return has_anchor and has_signal


def core_diagnosis_candidates(section: dict[str, Any]) -> list[str]:
    candidates: list[str] = []
    judgements = section.get("judgements")
    if isinstance(judgements, list):
        candidates.extend(str(item) for item in judgements if str(item).strip())
    blocks = section.get("narrative_blocks")
    if isinstance(blocks, list):
        for block in blocks:
            if not isinstance(block, dict):
                continue
            role = str(block.get("role") or "").strip().lower()
            title = str(block.get("title") or "")
            if role not in DIAGNOSIS_BLOCK_ROLES and not any(term in title for term in DIAGNOSIS_BLOCK_TITLE_TERMS):
                continue
            items = block.get("items")
            if isinstance(items, list):
                candidates.extend(str(item) for item in items if str(item).strip())
    return candidates


def sections_without_core_diagnosis(sections: list[dict[str, Any]], company_terms: list[str]) -> list[str]:
    offenders: list[str] = []
    for section in sections:
        section_id = str(section.get("section_id", "unknown"))
        if section_id == "data_quality_traceability":
            continue
        candidates = core_diagnosis_candidates(section)
        if not any(is_specific_core_diagnosis(candidate, company_terms) for candidate in candidates):
            offenders.append(section_id)
    return offenders


def hardcoded_template_residue_hits(text: str) -> list[str]:
    return [term for term in AUTOMOTIVE_TEMPLATE_RESIDUE_TERMS if term in text]


def peer_selection_industry_mismatch_hits(text: str) -> list[str]:
    hits: list[str] = []
    if "auto_keyword_automotive" in text:
        hits.append("auto_keyword_automotive")
    if "peer_metrics" in text:
        hits.extend(term for term in AUTOMOTIVE_PEER_TERMS if term in text)
    peer_context_lines = [
        line
        for line in text.splitlines()
        if "同业" in line or "可比" in line or "peer" in line.lower()
    ]
    peer_context = "\n".join(peer_context_lines)
    hits.extend(term for term in AUTOMOTIVE_PEER_TERMS if term in peer_context)
    return sorted(set(hits))


def visible_search_dump_counts(md: str) -> dict[str, int]:
    lines = md.splitlines()
    industry_detail_lines = INDUSTRY_RESEARCH_PROVIDER_DETAIL_RE.findall(md)
    provider_snippet_lines = [line for line in lines if SEARCH_PROVIDER_SNIPPET_RE.search(line)]
    jsonish_provider_lines = [
        line
        for line in lines
        if SEARCH_JSONISH_LINE_RE.search(line) and re.search(r"\b(?:tavily|exa)\b|https?://", line, re.I)
    ]
    external_url_lines = []
    for line in lines:
        urls = RAW_URL_RE.findall(line)
        external_urls = [
            url
            for url in urls
            if "/api/pdf_page/" not in url and "/api/source/" not in url
        ]
        if external_urls:
            external_url_lines.append(line)
    return {
        "industry_detail_lines": len(industry_detail_lines),
        "provider_snippet_lines": len(provider_snippet_lines),
        "jsonish_provider_lines": len(jsonish_provider_lines),
        "external_url_lines": len(external_url_lines),
    }


def classify_search_snippet_dumping(md: str) -> str | None:
    counts = visible_search_dump_counts(md)
    if (
        counts["industry_detail_lines"] >= 6
        or counts["provider_snippet_lines"] >= 4
        or counts["jsonish_provider_lines"] >= 6
        or counts["external_url_lines"] >= 12
    ):
        return "failure"
    if (
        counts["industry_detail_lines"] >= 3
        or counts["provider_snippet_lines"] >= 2
        or counts["jsonish_provider_lines"] >= 3
        or counts["external_url_lines"] >= 6
    ):
        return "warning"
    return None


def visible_negative_ordinary_expense_mentions(md: str) -> list[str]:
    return sorted(set(match.group(1) for match in VISIBLE_NEGATIVE_ORDINARY_EXPENSE_RE.finditer(md)))


def key_metrics_with_three_year_values(prefix: Path) -> dict[str, bool]:
    company_dir = infer_company_dir(prefix)
    if not company_dir:
        return {}
    path = company_dir / "metrics" / "key_metrics.json"
    if not path.exists():
        return {}
    try:
        items = load_json(path).get("data") or []
    except (OSError, json.JSONDecodeError):
        return {}
    by_name = {item.get("canonical_name"): item for item in items if isinstance(item, dict)}
    available: dict[str, bool] = {}
    for label, canonical_names in CORE_METRIC_ALIASES.items():
        values = {}
        for canonical in canonical_names:
            values = by_name.get(canonical, {}).get("values") or {}
            if values:
                break
        available[label] = all(values.get(year) is not None for year in ("2023", "2024", "2025"))
    return available


def core_metric_rows_with_missing_values(md: str, prefix: Path) -> list[str]:
    available = key_metrics_with_three_year_values(prefix)
    if not available:
        return []
    missing: list[str] = []
    for line in md.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 4:
            continue
        metric_name = cells[0]
        matched_label = next((label for label in available if metric_name.startswith(label)), None)
        if not matched_label or not available.get(matched_label):
            continue
        year_cells = cells[1:4]
        if any(cell in {"未返回", "未返回/未返回"} or "未返回" in cell for cell in year_cells):
            missing.append(matched_label)
    return sorted(set(missing))


def mechanical_subheading_sections(md: str) -> list[str]:
    offenders: list[str] = []
    section_bodies = re.split(r"^##\s+", md, flags=re.M)[1:]
    for idx, body in enumerate(section_bodies, start=1):
        labels = [
            match.group(1).strip()
            for match in re.finditer(r"^###\s+(.+?)\s*$", body, flags=re.M)
        ]
        hit_count = sum(1 for label in labels if label in MECHANICAL_H3_LABELS)
        if hit_count >= 3:
            offenders.append(str(idx))
    return offenders


def validate(prefix: Path) -> dict[str, Any]:
    md_path = prefix.parent / f"{prefix.name}.md"
    json_path = prefix.parent / f"{prefix.name}.json"
    html_path = prefix.parent / f"{prefix.name}.html"
    failures: list[str] = []
    warnings: list[str] = []

    for path in [md_path, json_path, html_path]:
        if not path.exists():
            failures.append(f"missing_file:{path}")

    if failures:
        return {"ok": False, "failures": failures, "warnings": warnings}

    md = load_text(md_path)
    html = load_text(html_path)
    data = load_json(json_path)
    company, company_warning = load_company_context(prefix)
    if company_warning:
        warnings.append(company_warning)
    target_is_automotive = is_automotive_company(company)
    company_terms = report_company_terms(data, company)
    semantic_text = md + "\n" + json.dumps(data, ensure_ascii=False)

    tid = template_id(data)
    if tid != "siq_analysis_report_v1.1":
        failures.append(f"template_id_invalid:{tid}")
    template_payload = report_template_payload(data)
    source_json = template_payload.get("template_source_json")
    if source_json and Path(str(source_json)).resolve() != TEMPLATE_JSON.resolve():
        failures.append(f"template_source_json_mismatch:{source_json}")
    if not source_json:
        warnings.append("template_source_json_missing")

    sections = sections_payload(data)
    ids = [str(section.get("section_id", "")) for section in sections]
    if len(ids) != 14:
        failures.append(f"json_section_count_invalid:{len(ids)}")
    if ids and ids != SECTION_IDS:
        failures.append("json_section_order_invalid")

    residue_hits = hardcoded_template_residue_hits(semantic_text)
    if residue_hits:
        if target_is_automotive is False:
            failures.append("hardcoded_template_residue:" + ",".join(residue_hits[:20]))
        elif target_is_automotive is None:
            warnings.append("hardcoded_template_residue_industry_unknown:" + ",".join(residue_hits[:20]))

    peer_mismatch_hits = peer_selection_industry_mismatch_hits(semantic_text)
    if peer_mismatch_hits:
        if target_is_automotive is False:
            failures.append("peer_selection_industry_mismatch:" + ",".join(peer_mismatch_hits[:20]))
        elif target_is_automotive is None:
            warnings.append("peer_selection_industry_unknown:" + ",".join(peer_mismatch_hits[:20]))

    missing_core_diagnosis = sections_without_core_diagnosis(sections, company_terms)
    if missing_core_diagnosis:
        failures.append("section_without_core_diagnosis:" + ",".join(missing_core_diagnosis[:20]))

    search_dumping = classify_search_snippet_dumping(md)
    if search_dumping == "failure":
        failures.append("search_snippet_dumping")
    elif search_dumping == "warning":
        warnings.append("search_snippet_dumping")

    md_h2 = len(re.findall(r"^##\s+", md, flags=re.M))
    if md_h2 != 14:
        failures.append(f"markdown_h2_count_invalid:{md_h2}")

    html_h2 = html.count("<h2>")
    if html_h2 != 14:
        failures.append(f"html_h2_count_invalid:{html_h2}")

    report_sections = html.count('<section class="section"')
    if report_sections != 14:
        failures.append(f"html_report_section_count_invalid:{report_sections}")

    total_section_open = len(re.findall(r"<section\b", html))
    section_close = html.count("</section>")
    if total_section_open != section_close:
        failures.append(f"html_section_unbalanced:{total_section_open}!={section_close}")

    sidebar_nav_markers = [marker for marker in HTML_SIDEBAR_NAV_MARKERS if marker in html]
    if sidebar_nav_markers:
        failures.append("html_sidebar_navigation_present:" + ",".join(sidebar_nav_markers))

    placeholder_hits = sorted(set(PLACEHOLDER_RE.findall(md + "\n" + html)))
    if placeholder_hits:
        failures.append("unresolved_placeholders:" + ",".join(placeholder_hits[:20]))

    traceable_missing_pages: list[str] = []
    for text in [md, *iter_strings(data)]:
        for line in text.splitlines():
            traceable_missing_pages.extend(TRACEABLE_MISSING_PAGE_RE.findall(line))
    if traceable_missing_pages:
        failures.append("traceable_pdf_page_not_repaired:" + ",".join(sorted(set(traceable_missing_pages))[:20]))

    contaminated_rows = [match.group(1) for match in FINANCIAL_TABLE_CONTAMINATION_RE.finditer(md)]
    if contaminated_rows:
        failures.append("financial_table_contaminated_by_evidence_metadata:" + ",".join(sorted(set(contaminated_rows))[:20]))

    negative_expense_mentions = visible_negative_ordinary_expense_mentions(md)
    if negative_expense_mentions:
        failures.append("ordinary_expense_visible_negative:" + ",".join(negative_expense_mentions[:20]))

    generic_missing_citations = GENERIC_MISSING_CITATION_RE.findall(md)
    if len(generic_missing_citations) > 2:
        failures.append(f"too_many_untraceable_citations:{len(generic_missing_citations)}")

    missing_core_metrics = core_metric_rows_with_missing_values(md, prefix)
    if missing_core_metrics:
        failures.append("core_key_metrics_marked_missing_despite_source:" + ",".join(missing_core_metrics[:20]))

    mechanical_sections = mechanical_subheading_sections(md)
    if len(mechanical_sections) >= 3:
        failures.append("mechanical_repeated_subheading_template:" + ",".join(mechanical_sections[:20]))

    weak_json_sections: list[str] = []
    sections_without_evidence: list[str] = []
    sections_missing_judgement_chain: list[str] = []
    sections_missing_risk_or_improvement: list[str] = []
    sections_without_narrative_blocks: list[str] = []
    narrative_block_titles_by_section: list[tuple[str, tuple[str, ...]]] = []
    for section in sections:
        section_id = str(section.get("section_id", "unknown"))
        blocks = section.get("narrative_blocks")
        if not isinstance(blocks, list) or len(blocks) < 3:
            sections_without_narrative_blocks.append(section_id)
        else:
            titles = tuple(
                str(block.get("title") or "").strip()
                for block in blocks
                if isinstance(block, dict) and str(block.get("title") or "").strip()
            )
            narrative_block_titles_by_section.append((section_id, titles))
        text_fields = []
        if isinstance(blocks, list):
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                text_fields.append(str(block.get("title") or ""))
                items = block.get("items")
                if isinstance(items, list):
                    text_fields.extend(str(item) for item in items)
        for key in ["facts", "calculations", "judgements", "risks_or_improvement_conditions"]:
            value = section.get(key)
            if isinstance(value, list):
                text_fields.extend(str(item) for item in value)
        compact_len = len(re.sub(r"\s+", "", "".join(text_fields)))
        if compact_len < 120:
            weak_json_sections.append(section_id)
        evidence_ids = section.get("evidence_ids")
        if not isinstance(evidence_ids, list) or not evidence_ids:
            sections_without_evidence.append(section_id)
        # Fact -> Judgement chain check (skip the data quality / traceability
        # section which is intentionally meta).
        if section_id != "data_quality_traceability":
            judgements = section.get("judgements") if isinstance(section.get("judgements"), list) else []
            facts = section.get("facts") if isinstance(section.get("facts"), list) else []
            if not judgements or not facts:
                sections_missing_judgement_chain.append(section_id)
            risks = section.get("risks_or_improvement_conditions") if isinstance(section.get("risks_or_improvement_conditions"), list) else []
            risks_text = "".join(str(item) for item in risks).strip()
            if not risks or len(re.sub(r"\s+", "", risks_text)) < 30:
                sections_missing_risk_or_improvement.append(section_id)
    if weak_json_sections:
        failures.append("thin_json_section_content:" + ",".join(weak_json_sections[:20]))
    if sections_without_narrative_blocks:
        failures.append("sections_without_cfo_narrative_blocks:" + ",".join(sections_without_narrative_blocks[:20]))
    if len(narrative_block_titles_by_section) >= 6:
        repeated_title_sets: dict[tuple[str, ...], list[str]] = {}
        for section_id, titles in narrative_block_titles_by_section:
            repeated_title_sets.setdefault(titles, []).append(section_id)
        repeated = [
            ids
            for titles, ids in repeated_title_sets.items()
            if len(ids) >= 3 and set(titles[:4]) & MECHANICAL_H3_LABELS
        ]
        if repeated:
            failures.append("narrative_blocks_still_mechanical:" + ",".join(repeated[0][:20]))
    if len(sections_without_evidence) > 2:
        failures.append("sections_without_evidence_ids:" + ",".join(sections_without_evidence[:20]))
    if sections_missing_judgement_chain:
        # Allowing 1 weak section keeps tolerance for executive_summary edge
        # cases; >1 indicates the analyst didn't form fact->judgement links.
        if len(sections_missing_judgement_chain) > 1:
            failures.append(
                "sections_missing_fact_to_judgement_chain:"
                + ",".join(sections_missing_judgement_chain[:20])
            )
        else:
            warnings.append(
                "section_missing_fact_to_judgement_chain:"
                + ",".join(sections_missing_judgement_chain[:20])
            )
    if sections_missing_risk_or_improvement:
        # Risk / improvement signals are required by the SOUL contract.
        if len(sections_missing_risk_or_improvement) > 2:
            failures.append(
                "sections_missing_risk_or_improvement:"
                + ",".join(sections_missing_risk_or_improvement[:20])
            )
        else:
            warnings.append(
                "section_missing_risk_or_improvement:"
                + ",".join(sections_missing_risk_or_improvement[:20])
            )

    # Risk-chain section must contain ≥2 cause→effect chains. We accept ASCII
    # arrows ("->", "=>") and the wide arrow "→".
    risk_chain_section = next(
        (s for s in sections if str(s.get("section_id")) == "risk_chain_scenario"),
        None,
    )
    if isinstance(risk_chain_section, dict):
        chain_text = "".join(
            str(item) for item in (
                (risk_chain_section.get("facts") or [])
                + (risk_chain_section.get("judgements") or [])
                + (risk_chain_section.get("risks_or_improvement_conditions") or [])
            )
        )
        arrow_count = chain_text.count("→") + chain_text.count("->") + chain_text.count("=>")
        if arrow_count < 2:
            failures.append(f"risk_chain_section_lacks_causal_chains:{arrow_count}")

    table_rows_with_missing_links = re.findall(
        r"\|\s*[^|\n]+\s*\|\s*[^|\n]+\s*\|\s*[^|\n]+\s*\|\s*[0-9a-fA-F-]{32,36}\s*\|\s*未返回\s*\|\s*([0-9]+)\s*\|",
        md,
    )
    if table_rows_with_missing_links:
        failures.append("evidence_table_missing_pdf_links:" + ",".join(sorted(set(table_rows_with_missing_links))[:20]))

    evidence_row_link_gaps: list[str] = []
    for line in md.splitlines():
        if not line.startswith("|") or not re.search(r"\|\s*[0-9a-fA-F-]{32,36}\s*\|\s*[0-9]+\s*\|\s*[0-9]+\s*\|", line):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        uuid_index = next((idx for idx, cell in enumerate(cells) if re.fullmatch(r"[0-9a-fA-F-]{32,36}", cell)), None)
        if uuid_index is None or uuid_index + 5 >= len(cells):
            continue
        table_index = cells[uuid_index + 2]
        pdf_link_cell = cells[uuid_index + 3]
        source_link_cell = cells[uuid_index + 4]
        table_link_cell = cells[uuid_index + 5]
        if "/api/pdf_page/" not in pdf_link_cell or "/api/source/" not in source_link_cell or "/api/source/" not in table_link_cell:
            evidence_row_link_gaps.append(table_index)
    if evidence_row_link_gaps:
        failures.append("evidence_table_link_columns_incomplete:" + ",".join(sorted(set(evidence_row_link_gaps))[:20]))

    svg_count = html.count("<svg")
    chart_js_count = html.count("Chart(") + html.count("new Chart")
    if svg_count + chart_js_count < 1:
        failures.append("missing_chart_visualization")

    api_pdf_links = html.count("/api/pdf_page/")
    api_source_links = html.count("/api/source/")
    if api_pdf_links < 1:
        failures.append("missing_pdf_page_links")
    elif api_pdf_links < 3:
        warnings.append(f"weak_pdf_page_link_coverage:{api_pdf_links}")
    if api_source_links < 1:
        warnings.append("missing_source_links")
    elif api_source_links < 6:
        warnings.append(f"weak_source_link_coverage:{api_source_links}")
    invalid_provenance_tokens = INVALID_PROVENANCE_TOKEN_RE.findall("\n".join([md, html, json.dumps(data, ensure_ascii=False)]))
    if invalid_provenance_tokens:
        failures.append(f"invalid_provenance_link_tokens:{len(invalid_provenance_tokens)}")

    api_anchor_attr_gaps = []
    for anchor in HTML_API_ANCHOR_RE.finditer(html):
        attrs = anchor.group(1)
        has_target_blank = re.search(r"\btarget=[\"']_blank[\"']", attrs, re.I)
        rel_match = re.search(r"\brel=[\"']([^\"']+)[\"']", attrs, re.I)
        rel_tokens = set((rel_match.group(1).lower().split() if rel_match else []))
        if not has_target_blank or not {"noopener", "noreferrer"}.issubset(rel_tokens):
            api_anchor_attr_gaps.append(attrs[:160])
    if api_anchor_attr_gaps:
        failures.append(f"api_links_not_new_tab:{len(api_anchor_attr_gaps)}")

    section_bodies = re.split(r"^##\s+", md, flags=re.M)[1:]
    short_sections = []
    for idx, section in enumerate(section_bodies, start=1):
        plain = re.sub(r"\s+", "", re.sub(r"\|.*\|", "", section))
        if len(plain) < 160:
            short_sections.append(str(idx))
        mismatched_h3 = [
            match.group(1)
            for match in MISMATCHED_H3_RE.finditer(section)
            if int(match.group(1)) != idx
        ]
        if mismatched_h3:
            failures.append(f"mismatched_subheading_numbering:{idx}")
    if short_sections:
        warnings.append("thin_section_content:" + ",".join(short_sections[:20]))

    duplicate_link_lines = []
    for line_number, line in enumerate(md.splitlines(), start=1):
        urls = API_LINK_RE.findall(line)
        if len(urls) != len(set(urls)):
            duplicate_link_lines.append(str(line_number))
    if duplicate_link_lines:
        warnings.append("duplicate_api_links:" + ",".join(duplicate_link_lines[:20]))

    quality = data.get("quality_report") or {}
    if isinstance(quality, dict):
        if int(quality.get("module_count") or 0) != 14:
            failures.append(f"quality_report_module_count_invalid:{quality.get('module_count')}")
        for flag, failure in REQUIRED_QUALITY_TRUE_FLAGS.items():
            if quality.get(flag) is not True:
                failures.append(failure)
        if quality.get("tool_sections_misused"):
            failures.append("tool_sections_misused_present")
        if quality.get("prohibited_outputs"):
            failures.append("prohibited_outputs_present")
        review_queue = quality.get("review_queue")
        if isinstance(review_queue, list):
            review_text = "\n".join(str(item) for item in review_queue)
            critical_hits = [term for term in CRITICAL_REVIEW_TERMS if term in review_text]
            if len(critical_hits) >= 3:
                warnings.append("critical_review_queue_items:" + ",".join(critical_hits[:20]))
        html_quality = quality.get("html_structure")
        if isinstance(html_quality, dict) and html_quality.get("html_structure_valid") is False:
            failures.append("quality_report_html_structure_false")
    else:
        failures.append("quality_report_missing")

    evidence_index = data.get("evidence_index")
    evidence_count = 0
    if isinstance(evidence_index, list):
        evidence_count = len(evidence_index)
    elif isinstance(evidence_index, dict):
        keys = evidence_index.get("financial_metric_keys")
        if isinstance(keys, list):
            evidence_count = len(keys)
    if evidence_count and evidence_count < REQUIRED_SECTION_EVIDENCE_MIN:
        warnings.append(f"weak_structured_evidence_index:{evidence_count}")

    required_terms = {
        "dupont": ["杜邦"],
        "ccc": ["CCC", "现金转换周期", "DSO", "DIO", "DPO"],
        "free_cash_flow": ["自由现金流"],
        "altman": ["Altman", "Z-Score"],
        "valuation_gap": ["估值", "市场预期差"],
        "scenario": ["情景", "风险链条"],
    }
    for key, terms in required_terms.items():
        if not any(term in md for term in terms):
            failures.append(f"required_analysis_model_missing:{key}")

    factcheck = factcheck_gate(prefix, data)
    factcheck_verdict = str(factcheck.get("verdict") or "missing")
    if factcheck_verdict == "block":
        failures.append("factcheck_verdict_block")
    elif factcheck_verdict == "request_changes":
        warnings.append("factcheck_verdict_request_changes")
    elif factcheck_verdict != "approve":
        warning = factcheck.get("warning")
        warnings.append(str(warning or f"factcheck_verdict_{factcheck_verdict}"))

    publication = publication_gate(failures, warnings, factcheck)

    return {
        "ok": not failures,
        **publication,
        "failures": failures,
        "warnings": warnings,
        "factcheck": factcheck,
        "metrics": {
            "template_id": tid,
            "json_sections": len(ids),
            "markdown_h2": md_h2,
            "html_h2": html_h2,
            "html_report_sections": report_sections,
            "html_total_section_open": total_section_open,
            "html_section_close": section_close,
            "svg_count": svg_count,
            "chart_js_count": chart_js_count,
            "api_pdf_links": api_pdf_links,
            "api_source_links": api_source_links,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prefix", required=True, type=Path, help="Report prefix without .md/.json/.html suffix")
    parser.add_argument("--write-json", type=Path, help="Optional path to write validation result")
    args = parser.parse_args()

    result = validate(args.prefix)
    if args.write_json:
        args.write_json.parent.mkdir(parents=True, exist_ok=True)
        args.write_json.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
