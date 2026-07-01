"""Read-only wiki catalog helpers for the agent runtime."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from services.path_config import WIKI_ROOT as CONFIG_WIKI_ROOT


WIKI_ROOT = CONFIG_WIKI_ROOT

WIKI_CATALOG_COUNT_TERMS = (
    "多少家",
    "几家",
    "总数",
    "数量",
    "规模",
    "count",
)
WIKI_CATALOG_LIST_TERMS = (
    "清单",
    "列表",
    "名单",
    "列出",
    "展示",
    "看看",
    "有哪些",
    "都有谁",
    "list",
)
WIKI_CATALOG_SUBJECT_TERMS = (
    "已入库",
    "入库",
    "wiki",
    "Wiki",
    "公司",
    "财报",
    "工作集",
    "知识库",
)


def _read_json_file(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def is_wiki_catalog_query(
    message: str,
    *,
    is_general_assistant_request: Callable[[str], bool] | None = None,
    count_terms: tuple[str, ...] = WIKI_CATALOG_COUNT_TERMS,
    list_terms: tuple[str, ...] = WIKI_CATALOG_LIST_TERMS,
    subject_terms: tuple[str, ...] = WIKI_CATALOG_SUBJECT_TERMS,
) -> bool:
    text = re.sub(r"\s+", "", message or "")
    if not text:
        return False
    if is_general_assistant_request and is_general_assistant_request(message):
        return False
    lower = text.lower()
    has_subject = any(term in text for term in subject_terms)
    has_count = any(term in lower for term in count_terms)
    has_list = any(term in lower for term in list_terms)
    if has_subject and (has_count or has_list):
        return True
    return "company_catalog" in lower or "公司catalog" in lower


def wiki_catalog_path(*, wiki_root: Path | str | None = None) -> Path:
    root = Path(wiki_root) if wiki_root is not None else WIKI_ROOT
    return root / "_meta" / "company_catalog.json"


def load_wiki_catalog_companies(
    *,
    wiki_root: Path | str | None = None,
    read_json_file: Callable[[Path], Any | None] = _read_json_file,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    catalog = read_json_file(wiki_catalog_path(wiki_root=wiki_root))
    companies = catalog.get("companies") if isinstance(catalog, dict) else None
    if not isinstance(companies, list):
        return catalog if isinstance(catalog, dict) else None, []
    normalized = [item for item in companies if isinstance(item, dict)]
    normalized.sort(key=lambda item: str(item.get("stock_code") or item.get("company_id") or ""))
    return catalog, normalized


def format_catalog_company_line(index: int, company: dict[str, Any]) -> str:
    code = str(company.get("stock_code") or "").strip()
    name = str(company.get("company_short_name") or company.get("company_full_name") or "").strip()
    company_id = str(company.get("company_id") or "").strip()
    status = str(company.get("status") or "").strip()
    report_count = company.get("report_count")
    parts = [f"{index}. {code} {name}".strip()]
    if company_id and (not code or company_id != f"{code}-{name}"):
        parts.append(f"company_id={company_id}")
    if status:
        parts.append(f"status={status}")
    if report_count not in (None, ""):
        parts.append(f"reports={report_count}")
    if company.get("has_three_statement_metrics") is False:
        parts.append("三大表指标=无")
    return "，".join(parts)


def build_wiki_catalog_reply(
    message: str,
    *,
    wiki_root: Path | str | None = None,
    is_general_assistant_request: Callable[[str], bool] | None = None,
    read_json_file: Callable[[Path], Any | None] = _read_json_file,
    count_terms: tuple[str, ...] = WIKI_CATALOG_COUNT_TERMS,
    list_terms: tuple[str, ...] = WIKI_CATALOG_LIST_TERMS,
    subject_terms: tuple[str, ...] = WIKI_CATALOG_SUBJECT_TERMS,
) -> str | None:
    if not is_wiki_catalog_query(
        message,
        is_general_assistant_request=is_general_assistant_request,
        count_terms=count_terms,
        list_terms=list_terms,
        subject_terms=subject_terms,
    ):
        return None

    root = Path(wiki_root) if wiki_root is not None else WIKI_ROOT
    catalog, companies = load_wiki_catalog_companies(wiki_root=root, read_json_file=read_json_file)
    catalog_path = wiki_catalog_path(wiki_root=root)
    if not companies:
        return (
            "## 结论\n"
            "- 当前无法读取已入库公司清单。\n\n"
            "## 依据/数据\n"
            f"- Wiki 根目录：{root}\n"
            f"- catalog：{catalog_path}\n"
            "- 问题：文件不存在、格式异常，或 `companies` 为空。\n\n"
            "## 引用来源\n"
            f"[1] source_type=wiki_metadata, file={catalog_path}, count=0"
        )

    declared_count = catalog.get("company_count") if isinstance(catalog, dict) else None
    actual_count = len(companies)
    generated_at = catalog.get("generated_at") if isinstance(catalog, dict) else None
    ready_count = sum(1 for company in companies if company.get("status") == "ready")
    needs_review_count = sum(1 for company in companies if company.get("status") == "needs_review")
    report_count = sum(int(company.get("report_count") or 0) for company in companies)
    needs_list = any(term in re.sub(r"\s+", "", message or "").lower() for term in list_terms)

    lines = [
        "## 结论",
        f"- 当前 Wiki 已入库公司一共 **{actual_count} 家**。",
        f"- 统计口径：只认当前生产 Wiki catalog `{catalog_path}`，不使用备份目录、历史 README 或模型记忆。",
    ]
    if declared_count not in (None, actual_count):
        lines.append(f"- 注意：catalog 声明 `company_count={declared_count}`，实际 `companies` 数组为 {actual_count}，本次以数组实际数量为准。")

    lines.extend([
        "",
        "## 依据/数据",
        f"- Wiki 根目录：{root}",
        f"- catalog 生成时间：{generated_at or '未返回'}",
        f"- ready：{ready_count} 家；needs_review：{needs_review_count} 家；报告合计：{report_count} 份。",
    ])

    if needs_list:
        lines.append("")
        lines.append("## 公司清单")
        lines.extend(format_catalog_company_line(index, company) for index, company in enumerate(companies, 1))

    lines.extend([
        "",
        "## 引用来源",
        f"[1] source_type=wiki_metadata, file={catalog_path}, count={actual_count}, generated_at={generated_at or '未返回'}",
    ])
    return "\n".join(lines)
