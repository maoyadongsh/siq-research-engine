"""Pure parse-only context helpers for the agent runtime."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any


Pdf2mdInfoIterator = Callable[[], Iterable[dict[str, Any]]]
InfoPredicate = Callable[[dict[str, Any]], bool]
MessagePredicate = Callable[[str], bool]
MatchPredicate = Callable[[dict[str, Any], str, Any | None], bool]
ResolveCompanyDir = Callable[[str, Any | None], Path | None]
ContextHint = Callable[[Any | None], str]
NormalizeText = Callable[[Any], str]


def infer_stock_code_from_text(text: str) -> str:
    for pattern in (r"\bCN[_-](\d{6})\b", r"\b(?:SH|SZ|BJ|HK)?[_-]?(\d{6})\b"):
        match = re.search(pattern, text or "", flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return ""


def infer_company_name_from_filename(filename: str) -> str:
    text = Path(str(filename or "")).stem
    text = re.sub(r"[_-](?:CN|SH|SZ|BJ|HK)[_-]\d{6}.*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"[_-]\d{6}.*$", "", text)
    text = re.sub(r"(?:集团股份有限公司|股份有限公司)?[_-]?(?:20\d{2}.*)?$", "", text)
    return text.strip("_- ")


def pdf2md_task_aliases(info: dict[str, Any]) -> list[str]:
    aliases = [
        info.get("task_id"),
        info.get("stock_code"),
        info.get("company_name"),
        info.get("filename"),
    ]
    filename = str(info.get("filename") or "")
    if filename:
        aliases.extend(part for part in re.split(r"[_\-\s]+", filename) if part)
    return [str(alias).strip() for alias in aliases if str(alias or "").strip()]


def pdf2md_info_matches_message(
    info: dict[str, Any],
    message: str,
    context: Any | None = None,
    *,
    normalize_text: NormalizeText,
    context_company_hint: ContextHint,
) -> bool:
    haystack = normalize_text(f"{message}\n{context_company_hint(context)}")
    if not haystack:
        return False
    for alias in pdf2md_task_aliases(info):
        normalized = normalize_text(alias)
        if not normalized:
            continue
        if re.fullmatch(r"\d{6}", normalized):
            if normalized in haystack:
                return True
            continue
        if len(normalized) >= 2 and normalized in haystack:
            return True
    return False


def _pdf2md_parse_only_matches(
    message: str,
    context: Any | None = None,
    *,
    limit: int | None = None,
    iter_pdf2md_task_infos: Pdf2mdInfoIterator,
    pdf2md_info_matches_message: MatchPredicate,
    wiki_company_exists_for_pdf2md_info: InfoPredicate,
    is_general_assistant_request: MessagePredicate,
    resolve_company_dir: ResolveCompanyDir,
) -> list[dict[str, Any]]:
    if is_general_assistant_request(message):
        return []
    if resolve_company_dir(message, context):
        return []
    matches: list[dict[str, Any]] = []
    for info in iter_pdf2md_task_infos():
        if not pdf2md_info_matches_message(info, message, context):
            continue
        if wiki_company_exists_for_pdf2md_info(info):
            continue
        matches.append(info)
        if limit and len(matches) >= limit:
            break
    return matches


def _should_consider_pdf2md_parse_only_context(
    message: str,
    context: Any | None = None,
    *,
    pdf2md_parse_only_matches: Callable[..., list[dict[str, Any]]],
    is_general_assistant_request: MessagePredicate,
    resolve_company_dir: ResolveCompanyDir,
    report_fulltext_fallback_terms: Iterable[str],
    context_company_hint: ContextHint,
) -> bool:
    text = re.sub(r"\s+", "", message or "")
    if not text or is_general_assistant_request(text):
        return False
    if resolve_company_dir(message, context):
        return False
    if any(term.lower() in text.lower() for term in report_fulltext_fallback_terms):
        return bool(pdf2md_parse_only_matches(message, context, limit=1))
    company_hint = context_company_hint(context)
    return bool(company_hint and pdf2md_parse_only_matches(message, context, limit=1))


def build_pdf2md_parse_only_context(
    message: str,
    context: Any | None = None,
    *,
    pdf2md_parse_only_matches: Callable[..., list[dict[str, Any]]],
    parse_only_context_limit: int,
) -> str | None:
    matches = pdf2md_parse_only_matches(message, context, limit=parse_only_context_limit)
    if not matches:
        return None
    lines = [
        "以下公司/报告目前只匹配到 PDF parser results 解析产物，未匹配到本地 Wiki 公司工作集。",
        "输出要求：",
        "- 不得虚构 Wiki 公司目录、Wiki 报告路径、Wiki metrics/semantic/evidence 文件或不存在的 task_id。",
        "- 本轮只能使用下列 PDF parser 解析产物回答；如果需要事实数据，必须先读取对应 `result.md`、`document_full.json`、`content_list_enhanced.json`、`table_index.json` 或 `financial_data.json`。",
        "- 引用来源必须写成 `source_type=pdf2md_parse_result`，并保留下方真实 `task_id`、`pdf_page`/`table_index`/`md_line`；字段没读到时写 `未返回`，不能编造。",
        "",
        "## PDF parser 解析产物",
    ]
    for index, info in enumerate(matches, start=1):
        task_id = info.get("task_id")
        stock_code = info.get("stock_code") or "未返回"
        company_name = info.get("company_name") or "未返回"
        filename = info.get("filename") or "未返回"
        lines.extend(
            [
                "",
                f"### P{index}. {company_name} / 代码 {stock_code}",
                f"- task_id: {task_id}",
                f"- 文件名: {filename}",
                f"- 结果目录: {info.get('result_dir')}",
            ]
        )
        for label, key in (
            ("Markdown", "result_md"),
            ("完整Markdown", "result_complete_md"),
            ("完整JSON", "document_full_json"),
            ("增强content_list", "content_list_enhanced_json"),
            ("content_list", "content_list_json"),
            ("表格索引", "table_index_json"),
            ("财务抽取", "financial_data_json"),
        ):
            value = info.get(key)
            if value:
                lines.append(f"- {label}: {value}")
        lines.append(
            f"- 可用来源模板: `/api/pdf_page/{task_id}/<pdf_page>?format=html`, "
            f"`/api/source/{task_id}/page/<pdf_page>?format=html`, "
            f"`/api/source/{task_id}/table/<table_index>?format=html`"
        )
    return "\n".join(lines)


__all__ = [
    "build_pdf2md_parse_only_context",
    "infer_company_name_from_filename",
    "infer_stock_code_from_text",
    "pdf2md_info_matches_message",
    "pdf2md_task_aliases",
    "_pdf2md_parse_only_matches",
    "_should_consider_pdf2md_parse_only_context",
]
