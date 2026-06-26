#!/usr/bin/env python3
"""Resolve and render financial note detail tables from the local OKF/Wiki workset.

The target use case is questions such as "上汽集团商誉明细/构成/分布".
Those answers should not stop at the main statement metric. They must follow
semantic/document_links.json to the note table, parse the table in report.md,
and keep task_id/pdf_page/table_index/md_line for traceability.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from local_citations import find_company_dir_from_text, primary_report  # noqa: E402

WIKI_BASE = Path(os.environ.get("SIQ_WIKI_ROOT", "/home/maoyd/wiki")).expanduser()
DEFAULT_SOURCE_TYPE = os.environ.get(
    "SIQ_DEFAULT_SOURCE_TYPE",
    "okf_metrics" if "okf_staging" in str(WIKI_BASE) else "wiki_metrics",
)
DETAIL_TERMS = ("明细", "构成", "分布", "组成", "附注", "详情", "减值", "准备", "变动")
RELATION_DETAIL_TERMS = DETAIL_TERMS + ("原值", "账面原值", "账龄", "前五名", "资产组", "可收回金额")
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
        list(RELATION_DETAIL_TERMS)
        + [alias for aliases in INTENT_ALIASES.values() for alias in aliases]
        + ["是什么", "有哪些", "列出", "展示", "显示", "情况"]
    )
)
QUESTION_NOISE_TERMS = (
    "下面我来分析",
    "我来分析",
    "现在来分析",
    "先来分析",
    "下面我来",
    "我来",
    "现在来",
    "先来",
    "请问",
    "请",
    "查询一下",
    "查一下",
    "了解一下",
    "分析一下",
    "看一下",
    "一下",
    "了解",
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
    "分析",
    "评估",
    "评价",
    "判断",
    "影响",
    "风险",
    "原因",
    "趋势",
    "对比",
    "预测",
    "建议",
    "异常",
    "合理",
    "解释",
    "为什么",
    "怎么看",
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
KNOWN_FINANCIAL_NOTE_TERMS = (
    "商誉",
    "应收账款",
    "其他应收款",
    "预付款项",
    "存货",
    "合同资产",
    "固定资产",
    "在建工程",
    "无形资产",
    "开发支出",
    "长期股权投资",
    "投资性房地产",
    "递延所得税资产",
    "短期借款",
    "长期借款",
    "应付账款",
    "合同负债",
    "预计负债",
    "营业收入",
    "营业成本",
    "销售费用",
    "管理费用",
    "研发费用",
    "财务费用",
    "资产减值损失",
    "信用减值损失",
    "递延所得税",
    "递延所得税负债",
    "长期待摊费用",
    "其他非流动资产",
    "使用权受到限制的资产",
)
QUESTION_NOISE_PATTERNS = (
    r"20\d{2}\s*年(?:度)?(?:年报|年度报告|报告)?",
    r"[?？!！。.,，;；:：]",
)
CHINESE_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def local_source_type(kind: str) -> str:
    prefix = "okf" if DEFAULT_SOURCE_TYPE.startswith("okf_") or "okf_staging" in str(WIKI_BASE) else "wiki"
    return f"{prefix}_{kind}"


def report_artifact_path(company_dir: Path, report_id: str, rel_path: str) -> Path:
    candidates = [
        company_dir / "reports" / report_id / rel_path,
        company_dir / rel_path,
    ]
    return next((path for path in candidates if path.exists()), candidates[0])


def relative_file(company_dir: Path, path: Path, fallback: str) -> str:
    try:
        return str(path.relative_to(company_dir))
    except ValueError:
        return fallback


def normalize(text: Any) -> str:
    return re.sub(r"[\s（）()_\-：:、,，;；/]+", "", str(text or "").lower())


def chinese_note_number(text: str) -> int | None:
    value = str(text or "").strip()
    if not value:
        return None
    if value == "十":
        return 10
    if "十" in value:
        left, _, right = value.partition("十")
        tens = CHINESE_DIGITS.get(left, 1 if left == "" else None)
        ones = CHINESE_DIGITS.get(right, 0 if right == "" else None)
        if tens is None or ones is None:
            return None
        return tens * 10 + ones
    if len(value) == 1:
        return CHINESE_DIGITS.get(value)
    return None


def leading_note_number(title: Any) -> int | None:
    text = re.sub(r"^#+\s*", "", str(title or "")).strip()
    match = re.match(r"^[（(]\s*(\d{1,3})\s*[）)]", text)
    if match:
        return int(match.group(1))
    match = re.match(r"^[（(]\s*([零〇一二两三四五六七八九十]{1,4})\s*[）)]", text)
    if match:
        return chinese_note_number(match.group(1))
    match = re.match(r"^(\d{1,3})(?:\s*[、.．]\s*|\s+)(?!年|年度)", text)
    if match:
        return int(match.group(1))
    return None


def clean_metric_query(metric_text: str, company_dir: Path) -> str:
    """Strip company and question wording so matching uses the financial item."""
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
    if text:
        normalized = normalize(text)
        matches = [term for term in KNOWN_FINANCIAL_NOTE_TERMS if normalize(term) in normalized]
        if matches:
            base = sorted(matches, key=lambda item: len(normalize(item)), reverse=True)[0]
            suffix = normalized.replace(normalize(base), "")
            intent_parts = [
                alias
                for aliases in INTENT_ALIASES.values()
                for alias in aliases
                if normalize(alias) in suffix
            ]
            intent_parts.extend(
                term
                for term in GENERIC_DETAIL_TERMS
                if normalize(term) in suffix
            )
            return base + "".join(dict.fromkeys(intent_parts))
    return text or str(metric_text or "")


def to_int(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def public_origin() -> str:
    return os.environ.get("SIQ_PUBLIC_ORIGIN", "https://arthurmao.synology.me:9391").rstrip("/")


def public_api_url(path: str | None) -> str | None:
    if not path:
        return None
    if "?" not in path and (
        path.startswith("/api/")
        or re.match(r"https?://[^/]+/api/(?:pdf_page|source)/", path)
    ):
        path = f"{path}?format=html"
    if path.startswith(("http://", "https://")):
        return path
    if path.startswith("/"):
        return f"{public_origin()}{path}"
    return path


class HtmlTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.raw_rows: list[list[dict[str, Any]]] = []
        self._row: list[dict[str, Any]] | None = None
        self._cell: dict[str, Any] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._row = []
            return
        if tag not in {"td", "th"} or self._row is None:
            return
        attr = {key: value for key, value in attrs}
        self._cell = {
            "tag": tag,
            "rowspan": max(1, to_int(attr.get("rowspan")) or 1),
            "colspan": max(1, to_int(attr.get("colspan")) or 1),
            "text": [],
        }

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell["text"].append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._row is not None and self._cell is not None:
            text = re.sub(r"\s+", " ", "".join(self._cell["text"])).strip()
            self._row.append({
                "text": text,
                "rowspan": self._cell["rowspan"],
                "colspan": self._cell["colspan"],
            })
            self._cell = None
            return
        if tag == "tr" and self._row is not None:
            self.raw_rows.append(self._row)
            self._row = None


def expand_table(raw_rows: list[list[dict[str, Any]]]) -> list[list[str]]:
    active: dict[int, list[Any]] = {}
    grid: list[list[str]] = []
    for raw_row in raw_rows:
        row: list[str] = []
        col = 0

        def fill_active() -> bool:
            nonlocal col
            if col not in active:
                return False
            remaining, text = active[col]
            row.append(text)
            remaining -= 1
            if remaining <= 0:
                del active[col]
            else:
                active[col][0] = remaining
            col += 1
            return True

        for cell in raw_row:
            while fill_active():
                pass
            text = cell.get("text") or ""
            rowspan = max(1, int(cell.get("rowspan") or 1))
            colspan = max(1, int(cell.get("colspan") or 1))
            for offset in range(colspan):
                row.append(text)
                if rowspan > 1:
                    active[col + offset] = [rowspan - 1, text]
            col += colspan
        while fill_active():
            pass
        if any(item.strip() for item in row):
            grid.append(row)

    width = max((len(row) for row in grid), default=0)
    return [row + [""] * (width - len(row)) for row in grid]


def parse_html_table(html: str) -> dict[str, Any]:
    parser = HtmlTableParser()
    parser.feed(html)
    rows = expand_table(parser.raw_rows)
    if not rows:
        return {"headers": [], "rows": [], "records": []}

    data_start = 0
    for idx, row in enumerate(rows):
        has_number = any(re.search(r"\d", cell) for cell in row[1:])
        first_cell = normalize(row[0])
        label_count = sum(1 for cell in row[1:] if re.search(r"[\u4e00-\u9fffA-Za-z]", cell) and not re.fullmatch(r"20\d{2}年度?", cell.strip()))
        non_empty_tail = [cell.strip() for cell in row[1:] if cell.strip()]
        if has_number and first_cell == "" and non_empty_tail and all(re.fullmatch(r"20\d{2}年度?", cell) for cell in non_empty_tail):
            continue
        if has_number and label_count >= 2 and first_cell in {"", "项目", "名称"}:
            continue
        if has_number and first_cell not in {"项目", "名称", "合计"} and "被投资单位" not in first_cell:
            data_start = idx
            break
    else:
        data_start = min(1, len(rows))

    header_rows = rows[:data_start] or [rows[0]]
    headers: list[str] = []
    width = max((len(row) for row in rows), default=0)
    for col in range(width):
        parts: list[str] = []
        for row in header_rows:
            value = row[col].strip() if col < len(row) else ""
            if value and value not in parts:
                parts.append(value)
        headers.append("/".join(parts) if parts else f"列{col + 1}")
    seen_headers: dict[str, int] = {}
    unique_headers: list[str] = []
    for index, header in enumerate(headers, start=1):
        base = header or f"列{index}"
        seen_headers[base] = seen_headers.get(base, 0) + 1
        unique_headers.append(base if seen_headers[base] == 1 else f"{base}#{seen_headers[base]}")
    headers = unique_headers

    data_rows = rows[data_start:]
    records = []
    for row in data_rows:
        if not any(cell.strip() for cell in row):
            continue
        records.append({headers[idx]: row[idx] if idx < len(row) else "" for idx in range(len(headers))})
    return {"headers": headers, "rows": data_rows, "records": records}


def report_md_path(company_dir: Path, report_id: str) -> Path:
    return company_dir / "reports" / report_id / "report.md"


def extract_table_html(report_md: Path, md_line: int | None) -> str | None:
    if md_line is None or not report_md.exists():
        return None
    lines = report_md.read_text(encoding="utf-8", errors="ignore").splitlines()
    start = max(0, md_line - 4)
    end = min(len(lines), md_line + 4)
    window = "\n".join(lines[start:end])
    match = re.search(r"<table\b.*?</table>", window, flags=re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(0)
    if 1 <= md_line <= len(lines):
        line = lines[md_line - 1]
        match = re.search(r"<table\b.*?</table>", line, flags=re.IGNORECASE | re.DOTALL)
        if match:
            return match.group(0)
    return None


def relation_filter(query: str) -> set[str]:
    text = str(query or "")
    relations: set[str] = set()
    if any(term in text for term in ("构成", "组成", "明细", "分布", "原值", "账面原值", "分类", "账龄", "前五名", "前5名")):
        relations.update({"composition_detail", "detail_disclosure", "movement_detail", "main_statement_to_note"})
    if any(term in text for term in ("减值", "准备", "坏账", "跌价", "可收回", "资产组")):
        relations.add("impairment_detail")
    if any(term in text for term in ("明细", "详情", "附注")):
        relations.update({"composition_detail", "detail_disclosure", "impairment_detail", "movement_detail"})
    if any(term in text for term in ("变动", "账龄", "核销", "计提", "转回", "转销")):
        relations.add("movement_detail")
    return relations


def base_query_norms(query: str) -> list[str]:
    raw = normalize(query)
    if not raw:
        return []
    stripped = raw
    for term in BASE_STRIP_TERMS:
        stripped = stripped.replace(normalize(term), "")
    bases = [stripped or raw]
    if "收入" in stripped:
        bases.extend(["主营业务收入", "业务收入"])
        if stripped == "收入":
            bases.append("收入")
    if "成本" in stripped:
        bases.extend(["主营业务成本", "业务成本"])
        if stripped == "成本":
            bases.append("成本")
    return list(dict.fromkeys(normalize(item) for item in bases if item))


def query_intent_norms(query: str) -> tuple[list[str], bool]:
    raw = normalize(query)
    intents: list[str] = []
    specific = False
    for trigger, aliases in INTENT_ALIASES.items():
        trigger_norm = normalize(trigger)
        if trigger_norm and trigger_norm in raw:
            specific = trigger not in GENERIC_DETAIL_TERMS
            intents.extend(normalize(alias) for alias in aliases)
    for term in GENERIC_DETAIL_TERMS:
        term_norm = normalize(term)
        if term_norm and term_norm in raw:
            intents.append(term_norm)
    return list(dict.fromkeys(item for item in intents if item)), specific


def numbered_note_title(text: Any) -> bool:
    return bool(re.match(r"^\s*[（(]?\d+\s*[).、）]", str(text or "")))


def target_table_matches_base(target: dict[str, Any], source: dict[str, Any], base_norms: list[str]) -> int:
    if not base_norms:
        return 0
    source_text = normalize(
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
    target_title_text = normalize(
        " ".join(
            str(item)
            for item in (
                target.get("name"),
                target.get("title"),
            )
            if item
        )
    )
    target_note_text = normalize(target.get("note_title"))
    target_preview = normalize(target.get("preview"))

    score = 0
    for base in base_norms:
        if not base:
            continue
        if base in source_text:
            score = max(score, 70)
        if base in target_title_text:
            score = max(score, 90)
        if base in target_note_text:
            score = max(score, 60)
        if base in GENERIC_PREVIEW_BASES and base in target_preview:
            score = max(score, 45)
    return score


def target_table_contains_base(target: dict[str, Any], base_norms: list[str]) -> bool:
    if not base_norms:
        return True
    target_text = normalize(
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
    return any(base and base in target_text for base in base_norms)


def target_looks_like_inherited_cross_section(target: dict[str, Any], base_norms: list[str]) -> bool:
    """Reject stale links where a later top-level note inherited the previous note title."""
    if not base_norms or target_table_contains_base(target, base_norms):
        return False
    title = str(target.get("title") or target.get("name") or "")
    title_norm = normalize(title)
    if "附注续" in title_norm or title_norm.endswith("附注"):
        return True
    for term in KNOWN_FINANCIAL_NOTE_TERMS:
        term_norm = normalize(term)
        if not term_norm or not title_norm or term_norm not in title_norm:
            continue
        if not any(base and (base in term_norm or term_norm in base) for base in base_norms):
            return True
    return False


def target_table_intent_score(target: dict[str, Any], query: str) -> tuple[int, bool]:
    intent_norms, specific = query_intent_norms(query)
    if not intent_norms:
        return 0, False
    title_target = normalize(
        " ".join(
            str(item)
            for item in (
                target.get("name"),
                target.get("title"),
            )
            if item
        )
    )
    preview_target = normalize(target.get("preview"))
    score = 0
    for intent in intent_norms:
        if not intent:
            continue
        if intent in title_target:
            score = max(score, 35 if intent in {normalize(term) for term in GENERIC_DETAIL_TERMS} else 95)
        elif intent in preview_target:
            score = max(score, 20 if intent in {normalize(term) for term in GENERIC_DETAIL_TERMS} else 45)
    return score, specific


def relation_satisfies_specific_intent(relation_name: str | None, query: str) -> bool:
    """Use document_links semantics when table titles omit user intent words."""
    text = str(query or "")
    if relation_name in {"composition_detail", "detail_disclosure"} and any(term in text for term in ("构成", "组成", "分类", "分布")):
        return True
    if relation_name == "impairment_detail" and any(term in text for term in ("减值", "准备", "跌价", "坏账", "可收回", "资产组")):
        return True
    if relation_name in {"movement_detail", "detail_disclosure"} and any(term in text for term in ("变动", "增加", "减少", "计提", "转回", "转销", "核销")):
        return True
    return False


def relation_score(relation_name: str | None, query: str) -> int:
    text = str(query or "")
    if relation_name == "composition_detail" and any(term in text for term in ("构成", "组成", "分类", "分布", "明细")):
        return 35
    if relation_name == "detail_disclosure" and any(term in text for term in ("构成", "组成", "分类", "分布", "账龄", "前五名", "前5名")):
        return 32
    if relation_name == "impairment_detail" and any(term in text for term in ("减值", "准备", "跌价", "坏账", "可收回", "资产组")):
        return 35
    if relation_name == "movement_detail" and any(term in text for term in ("变动", "增加", "减少", "计提", "核销", "账龄", "明细")):
        return 20
    if relation_name == "detail_disclosure":
        return 15
    return 0


def confidence_score(value: Any) -> int:
    text = str(value or "").lower()
    if text == "high":
        return 12
    if text == "medium":
        return 6
    if text == "low":
        return 1
    return 0


def generic_title_penalty(target: dict[str, Any], query: str) -> int:
    text = normalize(target.get("title") or target.get("name"))
    query_norm = normalize(query)
    penalty = 0
    if any(term in query_norm for term in (normalize("构成"), normalize("明细"), normalize("详情"))):
        if "项目列示" in text:
            penalty -= 25
        if "未办妥" in text:
            penalty -= 12
    return penalty


def link_matches_query(link: dict[str, Any], query: str, relations: set[str]) -> bool:
    return link_match_score(link, query, relations) is not None


def link_match_score(link: dict[str, Any], query: str, relations: set[str]) -> int | None:
    source = link.get("source") if isinstance(link.get("source"), dict) else {}
    target = link.get("target") if isinstance(link.get("target"), dict) else {}
    relation = link.get("relation") if isinstance(link.get("relation"), dict) else {}
    if target.get("kind") not in {"note_table", "table"}:
        return None
    if relations and relation.get("semantic_relation") not in relations:
        return None
    base_norms = base_query_norms(query)
    base_score = target_table_matches_base(target, source, base_norms)
    if base_norms and base_score <= 0:
        return None
    if target_looks_like_inherited_cross_section(target, base_norms):
        return None

    relation_name = relation.get("semantic_relation")
    intent_score, specific_intent = target_table_intent_score(target, query)
    if specific_intent and intent_score <= 0 and not relation_satisfies_specific_intent(relation_name, query):
        return None
    if not specific_intent and any(term in normalize(query) for term in (normalize("明细"), normalize("详情"), normalize("附注"))):
        if not target_table_contains_base(target, base_norms):
            return None

    query_norm = normalize(query)
    fields = [
        source.get("name"),
        source.get("title"),
        source.get("note_title"),
        target.get("name"),
        target.get("title"),
        target.get("note_title"),
        target.get("preview"),
    ]
    candidates = [normalize(item) for item in fields if item]
    if not query_norm:
        return base_score
    text_match = 0
    for candidate in candidates:
        if len(candidate) >= 2 and (candidate in query_norm or query_norm in candidate):
            text_match = 10
            break
    # Detail terms often extend a base item, e.g. "商誉明细" vs note title "商誉".
    stripped = query_norm
    for term in DETAIL_TERMS:
        stripped = stripped.replace(normalize(term), "")
    if stripped and any(stripped in candidate for candidate in candidates):
        text_match = max(text_match, 10)

    total = (
        base_score
        + intent_score
        + text_match
        + relation_score(relation_name, query)
        + confidence_score(link.get("confidence") or relation.get("confidence"))
        + generic_title_penalty(target, query)
    )
    return total


def resolve_note_detail_tables(
    company_text: str,
    metric_text: str,
    report_id: str | None = None,
    limit: int = 8,
) -> dict[str, Any]:
    company_dir = find_company_dir_from_text(company_text, WIKI_BASE)
    if not company_dir:
        return {"status": "company_not_found", "company_text": company_text, "tables": []}

    report = primary_report(company_dir, query_text=metric_text)
    resolved_report_id = report_id or report.get("report_id") or "2025-annual"
    document_links_path = report_artifact_path(company_dir, resolved_report_id, "semantic/document_links.json")
    document_links_file = relative_file(company_dir, document_links_path, "semantic/document_links.json")
    payload = read_json(document_links_path, {}) or {}
    links = payload.get("links") if isinstance(payload.get("links"), list) else []
    clean_metric_text = clean_metric_query(metric_text, company_dir)
    relations = relation_filter(clean_metric_text)
    scored = [
        (score, link)
        for link in links
        if isinstance(link, dict)
        and (score := link_match_score(link, clean_metric_text, relations)) is not None
    ]
    if not scored and relations:
        scored = [
            (score, link)
            for link in links
            if isinstance(link, dict)
            and (score := link_match_score(link, clean_metric_text, set())) is not None
        ]
    scored.sort(
        key=lambda item: (
            -item[0],
            to_int((item[1].get("target") or {}).get("table_index")) or 10**9,
            to_int((item[1].get("target") or {}).get("md_line") or (item[1].get("target") or {}).get("line")) or 10**9,
            str(item[1].get("document_link_id") or ""),
        )
    )
    matched = [link for _, link in scored]
    score_by_id = {id(link): score for score, link in scored}

    tables: list[dict[str, Any]] = []
    md_path = report_md_path(company_dir, resolved_report_id)
    seen: set[tuple[Any, Any]] = set()
    for link in matched:
        target = link.get("target") if isinstance(link.get("target"), dict) else {}
        table_index = to_int(target.get("table_index"))
        md_line = to_int(target.get("md_line") or target.get("line"))
        key = (table_index, md_line)
        if key in seen:
            continue
        seen.add(key)
        html = extract_table_html(md_path, md_line)
        parsed = parse_html_table(html or "")
        tables.append({
            "document_link_id": link.get("document_link_id"),
            "source_type": local_source_type("document_links"),
            "file": document_links_file,
            "company_id": company_dir.name,
            "report_id": resolved_report_id,
            "metric": target.get("title") or target.get("name") or metric_text,
            "semantic_relation": (link.get("relation") or {}).get("semantic_relation"),
            "confidence": link.get("confidence") or (link.get("relation") or {}).get("confidence"),
            "match_score": score_by_id.get(id(link)),
            "unit": target.get("unit"),
            "task_id": report.get("task_id"),
            "pdf_page": to_int(target.get("pdf_page_number") or target.get("pdf_page")),
            "table_index": table_index,
            "md_line": md_line,
            "open_pdf_page_url": public_api_url(target.get("open_pdf_page_url") or f"/api/pdf_page/{report.get('task_id')}/{target.get('pdf_page_number')}?format=html"),
            "open_source_page_url": public_api_url(target.get("open_source_page_url") or f"/api/source/{report.get('task_id')}/page/{target.get('pdf_page_number')}?format=html"),
            "open_source_table_url": public_api_url(target.get("open_source_table_url") or (f"/api/source/{report.get('task_id')}/table/{table_index}?format=html" if table_index is not None else None)),
            "headers": parsed["headers"],
            "rows": parsed["rows"],
            "records": parsed["records"],
            "raw_preview": target.get("preview"),
            "html_found": bool(html),
        })
        if len(tables) >= limit:
            break

    return {
        "status": "ok" if tables else "no_note_tables",
        "company_id": company_dir.name,
        "report_id": resolved_report_id,
        "task_id": report.get("task_id"),
        "metric": clean_metric_text,
        "tables": tables,
        "notes": [] if tables else [f"{document_links_file} 未定位到匹配的 note_table/table"],
    }


def markdown_escape(value: Any) -> str:
    text = str(value if value is not None else "")
    return text.replace("|", "\\|").replace("\n", " ").strip()


def render_markdown(result: dict[str, Any], max_rows: int = 20) -> str:
    if not result.get("tables"):
        notes = "；".join(result.get("notes") or []) or "未定位到附注明细表"
        return f"## 结论\n- 证据链不完整：{notes}"

    lines: list[str] = ["## 结论"]
    first_file = (result["tables"][0] or {}).get("file") if result.get("tables") else "semantic/document_links.json"
    lines.append(f"- 已从 `{result['company_id']}` 的 `{first_file}` 定位到 `{result.get('metric')}` 相关附注明细表。")
    lines.append("- 以下金额直接来自年报 `report.md` 中的表格行，不是 RAG 摘要。")
    lines.append("- 空白单元格表示原表为空或未披露，不得改写为 `0`；英文名称和缩写按原表保留。")

    for idx, table in enumerate(result["tables"], start=1):
        title = table.get("metric") or f"附注表 {idx}"
        unit = table.get("unit") or "未返回"
        lines.extend(["", f"## {idx}. {title}", f"- 单位：{unit}", f"- 溯源：pdf_page={table.get('pdf_page') or '未返回'}, table_index={table.get('table_index') or '未返回'}, md_line={table.get('md_line') or '未返回'}"])
        headers = table.get("headers") or []
        records = table.get("records") or []
        shown_rows = min(len(records), max_rows)
        if records:
            completeness = "完整列出" if shown_rows == len(records) else f"仅列出前 {shown_rows} 行"
            lines.append(f"- 表格完整性：{completeness}，共 {len(records)} 行。")
        if headers and records:
            lines.append("")
            lines.append("| " + " | ".join(markdown_escape(h) for h in headers) + " |")
            lines.append("| " + " | ".join("---" for _ in headers) + " |")
            for record in records[:max_rows]:
                lines.append("| " + " | ".join(markdown_escape(record.get(h, "")) for h in headers) + " |")
            if len(records) > max_rows:
                lines.append(f"| ... | {' | '.join(['...'] * max(0, len(headers) - 1))} |")
        else:
            lines.append("- 未能解析表格 HTML，已保留表格入口用于人工打开核验。")

    lines.extend(["", "## 引用来源"])
    for idx, table in enumerate(result["tables"], start=1):
        links = []
        if table.get("open_pdf_page_url"):
            links.append(f"[打开PDF页]({table['open_pdf_page_url']})")
        if table.get("open_source_page_url"):
            links.append(f"[查看页来源]({table['open_source_page_url']})")
        if table.get("open_source_table_url"):
            links.append(f"[查看表格]({table['open_source_table_url']})")
        lines.append(
            f"[{idx}] source_type={table.get('source_type') or local_source_type('document_links')}, file={table.get('file')}, "
            f"metric={table.get('metric')}, period={table.get('report_id')}, "
            f"task_id={table.get('task_id') or '未返回'}, pdf_page={table.get('pdf_page') or '未返回'}, "
            f"table_index={table.get('table_index') or '未返回'}, md_line={table.get('md_line') or '未返回'}"
            + (("，" + "，".join(links)) if links else "")
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Lookup note detail tables with traceable PDF/table links.")
    parser.add_argument("--company", required=True, help="公司简称、股票代码或 company_id")
    parser.add_argument("--metric", required=True, help="附注事项，如 商誉明细、存货构成、应收账款账龄")
    parser.add_argument("--report-id", default="", help="报告 ID，默认使用 primary_report_id")
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--max-rows", type=int, default=20)
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    args = parser.parse_args(argv)

    result = resolve_note_detail_tables(args.company, args.metric, args.report_id or None, args.limit)
    if args.format == "markdown":
        print(render_markdown(result, max_rows=args.max_rows))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("tables") else 1


if __name__ == "__main__":
    raise SystemExit(main())
