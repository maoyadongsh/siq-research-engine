"""Pure context helpers for the Hermes agent runtime."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

MULTI_COMPANY_SCOPE_NOTICE = (
    "本轮问题命中多家公司；必须分别使用每家公司自己的 Wiki 工作集和 task_id 做证据回溯。"
    "不得只读取第一家公司，也不得把一家公司的 PDF/source/table 链接套用到另一家公司。"
)


def clean_context_value(value: Any) -> str:
    return str(value).replace("\n", " ").strip()


def is_general_assistant_request(
    message: str | None,
    *,
    request_terms: Sequence[str],
    subject_terms: Sequence[str],
) -> bool:
    text = re.sub(r"\s+", "", message or "")
    if not text:
        return False
    lower = text.lower()
    has_request = any(term in text for term in request_terms)
    has_subject = any(term in lower for term in subject_terms)
    return has_request and has_subject


def build_general_assistant_context_input(
    message: str,
    *,
    profile: str,
    profile_label: str,
    general_assistant_context: str,
) -> str:
    return "\n\n".join(
        [
            general_assistant_context,
            f"当前智能体 profile: {profile}",
            f"当前智能体名称: {profile_label}",
            "请由当前 Hermes profile 的模型按自身角色设定回答，不要使用后端固定简介模板。",
            f"用户问题：{message}",
        ]
    )


def context_dict(context: Any | None) -> dict[str, Any]:
    if hasattr(context, "model_dump"):
        raw = context.model_dump(exclude_none=True)
    elif isinstance(context, dict):
        raw = context
    else:
        raw = {}
    return raw if isinstance(raw, dict) else {}


def context_company(context: Any | None) -> dict[str, Any]:
    raw = context_dict(context)
    company = raw.get("company")
    return company if isinstance(company, dict) else {}


def context_company_hint(context: Any | None) -> str:
    raw = context_dict(context)
    if not raw:
        return ""
    company = raw.get("company") or {}
    values = [
        company.get("name"),
        company.get("code"),
        company.get("dir"),
        (raw.get("report") or {}).get("title"),
        (raw.get("report") or {}).get("filename"),
    ]
    return " ".join(str(item) for item in values if item)


def normalized_intent_text(message: str | None) -> str:
    return re.sub(r"\s+", "", message or "").lower()


def force_rebuild_requested(message: str | None, terms: Sequence[str]) -> bool:
    raw_message = message or ""
    return any(term in raw_message for term in terms)


def analysis_completed_guard_applies(
    message: str | None,
    *,
    status_terms: Sequence[str],
    report_terms: Sequence[str],
    generation_terms: Sequence[str],
) -> bool:
    normalized = normalized_intent_text(message)
    if not normalized:
        return False
    if any(term in normalized for term in status_terms):
        return True
    has_report_term = any(term in normalized for term in report_terms)
    has_generation_term = any(term in normalized for term in generation_terms)
    return has_report_term and has_generation_term


def should_use_analysis_completion_guard(
    message: str | None,
    *,
    force_rebuild_terms: Sequence[str],
    status_terms: Sequence[str],
    report_terms: Sequence[str],
    generation_terms: Sequence[str],
) -> bool:
    if force_rebuild_requested(message, force_rebuild_terms):
        return False
    return analysis_completed_guard_applies(
        message,
        status_terms=status_terms,
        report_terms=report_terms,
        generation_terms=generation_terms,
    )


def forced_context_company_dir(context: Any | None, *, wiki_root: Path) -> Path | None:
    raw = context_dict(context)
    if not raw or not raw.get("force_company"):
        return None
    company = raw.get("company") or {}
    candidate = company.get("dir")
    if not candidate:
        return None
    try:
        path = Path(str(candidate)).resolve()
    except OSError:
        return None
    wiki_root = wiki_root.resolve()
    if path == wiki_root or wiki_root not in path.parents:
        return None
    return path if path.exists() else None


def analysis_completed_artifacts(
    context: Any | None,
    *,
    read_json_file,
    wiki_root: Path,
) -> dict[str, str] | None:
    company = context_company(context)
    company_dir_value = str(company.get("dir") or "").strip()
    code = str(company.get("code") or "").strip()
    name = str(company.get("name") or "").strip()

    company_dir: Path | None = None
    if company_dir_value:
        candidate = Path(company_dir_value)
        if not candidate.is_absolute():
            candidate = wiki_root / candidate
        if candidate.exists():
            company_dir = candidate
    if not company_dir and code:
        matches = sorted((wiki_root / "companies").glob(f"{code}-*"))
        if matches:
            company_dir = matches[0]
    if not company_dir or not company_dir.exists():
        return None

    stock_code = code or company_dir.name.split("-", 1)[0]
    short_name = name or (company_dir.name.split("-", 1)[1] if "-" in company_dir.name else company_dir.name)
    analysis_dir = company_dir / "analysis"
    prefix = analysis_dir / f"{stock_code}-{short_name}-2025-analysis"
    files = {
        "md": prefix.with_suffix(".md"),
        "json": prefix.with_suffix(".json"),
        "html": prefix.with_suffix(".html"),
    }
    if not all(path.exists() for path in files.values()):
        return None

    work_dir = analysis_dir / ".work" / prefix.name
    validation = read_json_file(work_dir / "final_validation.json")
    if not isinstance(validation, dict) or not validation.get("ok"):
        return None
    return {key: str(path) for key, path in files.items()} | {"validation": str(work_dir / "final_validation.json")}


def analysis_completion_reply(
    context: Any | None,
    *,
    analysis_completed_artifacts,
    analysis_completed_message: str,
) -> str | None:
    artifacts = analysis_completed_artifacts(context)
    if not artifacts:
        return None
    return (
        f"{analysis_completed_message}\n\n"
        f"Markdown：{artifacts['md']}\n"
        f"HTML：{artifacts['html']}\n"
        f"验收结果：{artifacts['validation']}"
    )


def analysis_completion_guard_input(message: str, artifacts: dict[str, str]) -> str:
    return (
        "后端已做确定性检查：当前公司年度分析报告已经存在，且 final_validation.json 显示验收通过。\n"
        "这不是要求你机械复述固定模板，而是给你的事实约束。请先理解用户这次具体在问什么，再自然回答。\n\n"
        "回复要求：\n"
        "1. 不要启动、建议启动或模拟启动完整报告生成流程；除非用户明确说“强制重建/覆盖重建”。\n"
        "2. 如果用户是在问是否完成、报告在哪、能否生成，说明报告已完成，并给出相关路径。\n"
        "3. 如果用户是在表达困惑或追问原因，要解释为什么系统没有重复生成，以及接下来可以怎么问。\n"
        "4. 语气要像分析助手在思考后回应，不要输出固定模板，不要声称创建了后台生成 run。\n"
        "5. 回答保持简洁。\n\n"
        f"Markdown 路径：{artifacts['md']}\n"
        f"HTML 路径：{artifacts['html']}\n"
        f"验收结果路径：{artifacts['validation']}\n\n"
        f"用户原始问题：{message}"
    )


def build_format_chat_context(*, wiki_root: Path, context: Any | None, context_header: str) -> str | None:
    if not context:
        return None

    raw = context_dict(context)
    if not raw:
        return None

    lines: list[str] = []
    lines.append(f"- Wiki 根目录: {wiki_root}")
    lines.append("- 路径规则: 所有 wiki/company/report 路径必须使用绝对路径，不得从 .hermes 或 profile home 推断。")
    company = raw.get("company") or {}
    report = raw.get("report") or {}
    page = raw.get("page") or {}

    company_parts: list[str] = []
    if company.get("name"):
        company_parts.append(clean_context_value(company["name"]))
    if company.get("code"):
        company_parts.append(f"代码 {clean_context_value(company['code'])}")
    if company.get("dir"):
        company_parts.append(f"目录 {clean_context_value(company['dir'])}")
    if company_parts:
        lines.append(f"- 当前公司: {' / '.join(company_parts)}")

    report_parts: list[str] = []
    if report.get("title"):
        report_parts.append(clean_context_value(report["title"]))
    if report.get("type"):
        report_parts.append(f"类型 {clean_context_value(report['type'])}")
    if report.get("filename"):
        report_parts.append(f"文件 {clean_context_value(report['filename'])}")
    if report.get("mtime"):
        report_parts.append(f"更新时间 {clean_context_value(report['mtime'])}")
    if report.get("url"):
        report_parts.append(f"URL {clean_context_value(report['url'])}")
    if report_parts:
        lines.append(f"- 当前报告: {' / '.join(report_parts)}")

    if page.get("title"):
        lines.append(f"- 当前页面: {clean_context_value(page['title'])}")

    if not lines:
        return None

    return "\n".join([context_header, *lines])


def build_company_context_items(
    message: str,
    context: Any | None,
    resolved_company_dirs: Sequence[Path],
    *,
    context_for_company_dir: Callable[[Path], Any | None],
    message_for_company: Callable[[str, Path], str],
    multi_company_scope_notice: str = MULTI_COMPANY_SCOPE_NOTICE,
) -> tuple[list[str], list[tuple[str, Any | None, Path]]]:
    blocks: list[str] = []
    company_context_items: list[tuple[str, Any | None, Path]] = []
    if len(resolved_company_dirs) > 1:
        blocks.append(multi_company_scope_notice)
        for company_dir in resolved_company_dirs:
            company_context_items.append(
                (
                    message_for_company(message, company_dir),
                    context_for_company_dir(company_dir),
                    company_dir,
                )
            )
    else:
        company_context_items.append(
            (
                message,
                context,
                resolved_company_dirs[0] if resolved_company_dirs else Path(),
            )
        )
    return blocks, company_context_items


def scoped_evidence_input(
    message: str,
    context: Any | None,
    company_context_items: Sequence[tuple[str, Any | None, Path]],
) -> tuple[str, Any | None]:
    if len(company_context_items) == 1:
        scoped_message, scoped_context, _company_dir = company_context_items[0]
        return scoped_message, scoped_context
    return message, context


def build_session_contextual_input_text(
    message: str,
    blocks: Sequence[str],
    *,
    chat_output_contract: str,
    financial_calculation_runtime_contract: str,
) -> str:
    return "\n\n".join(
        [
            *blocks,
            chat_output_contract,
            financial_calculation_runtime_contract,
            f"用户问题：{message}",
        ]
    )


def image_attachment_path_hints(image_attachments: Sequence[dict[str, Any]]) -> str:
    return "\n".join(
        f"[Image attached at: {item.get('path')}]"
        for item in image_attachments
        if item.get("path")
    )


def build_hermes_run_text(
    contextual_text: str,
    *,
    document_context: str | None = None,
    image_analysis_context: str | None = None,
    image_path_hints: str | None = None,
) -> str:
    return "\n\n".join(
        block
        for block in (
            contextual_text,
            document_context,
            image_analysis_context,
            image_path_hints,
        )
        if block
    )


def build_hermes_multimodal_run_input(
    text: str,
    image_data_urls: Sequence[str],
) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = [{"type": "text", "text": text}]
    for data_url in image_data_urls:
        if data_url:
            parts.append({"type": "image_url", "image_url": {"url": data_url}})
    return [{"role": "user", "content": parts}]
