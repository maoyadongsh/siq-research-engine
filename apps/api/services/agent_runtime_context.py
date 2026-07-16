"""Pure context helpers for the Hermes agent runtime."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from pathlib import Path
from typing import Any

from services import market_document_identity

MULTI_COMPANY_SCOPE_NOTICE = (
    "本轮问题命中多家公司；必须分别使用每家公司自己的 Wiki 工作集和 task_id 做证据回溯。"
    "不得只读取第一家公司，也不得把一家公司的 PDF/source/table 链接套用到另一家公司。"
)
NON_CN_RESEARCH_MARKETS = {"HK", "JP", "KR", "EU", "US"}
COMPLETE_RESEARCH_IDENTITY_FIELDS = ("market", "company_id", "filing_id", "parse_run_id")


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
    elif isinstance(context, Mapping):
        raw = dict(context)
    else:
        raw = {}
    return raw if isinstance(raw, dict) else {}


def _dict_field(raw: dict[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key)
    return value if isinstance(value, dict) else {}


def _first_text(*values: Any) -> str | None:
    for value in values:
        if value in (None, ""):
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _market_from_identifier(value: Any) -> str | None:
    text = str(value or "").strip()
    if ":" not in text:
        return None
    prefix = text.split(":", 1)[0]
    market = market_document_identity.normalize_market_code(prefix)
    return market if market in {"CN", "HK", "JP", "KR", "EU", "US"} else None


def research_identity(context: Any | None) -> dict[str, str]:
    raw = context_dict(context)
    if not raw:
        return {}

    identity = _dict_field(raw, "research_identity")
    company = _dict_field(raw, "company")
    report = _dict_field(raw, "report")
    filing = _dict_field(raw, "filing")
    resolved_period = _dict_field(raw, "resolved_period")
    postgres = _dict_field(raw, "postgres")

    company_id = _first_text(
        identity.get("company_id"),
        raw.get("company_id"),
        company.get("company_id"),
        company.get("id"),
        report.get("company_id"),
        filing.get("company_id"),
        postgres.get("company_id"),
    )
    filing_id = _first_text(
        identity.get("filing_id"),
        raw.get("filing_id"),
        company.get("filing_id"),
        report.get("filing_id"),
        filing.get("filing_id"),
        resolved_period.get("filing_id"),
        postgres.get("filing_id"),
    )
    parse_run_id = _first_text(
        identity.get("parse_run_id"),
        raw.get("parse_run_id"),
        raw.get("postgres_parse_run_id"),
        company.get("parse_run_id"),
        report.get("parse_run_id"),
        filing.get("parse_run_id"),
        resolved_period.get("parse_run_id"),
        postgres.get("parse_run_id"),
    )
    market = _first_text(
        identity.get("market"),
        raw.get("market"),
        company.get("market"),
        report.get("market"),
        filing.get("market"),
        resolved_period.get("market"),
        postgres.get("market"),
        _market_from_identifier(company_id),
        _market_from_identifier(filing_id),
    )

    output: dict[str, str] = {}
    if market:
        output["market"] = market_document_identity.normalize_market_code(market)
    if company_id:
        output["company_id"] = company_id
    if filing_id:
        output["filing_id"] = filing_id
    if parse_run_id:
        output["parse_run_id"] = parse_run_id
    return output


def incomplete_non_cn_research_identity(context: Any | None) -> tuple[str | None, tuple[str, ...]]:
    identity = research_identity(context)
    market = identity.get("market")
    if market not in NON_CN_RESEARCH_MARKETS:
        return None, ()
    missing = tuple(field for field in COMPLETE_RESEARCH_IDENTITY_FIELDS if not identity.get(field))
    return market, missing


def _authoritative_research_identity(context: Any | None) -> dict[str, str]:
    explicit_identity = _dict_field(context_dict(context), "research_identity")
    if not all(_first_text(explicit_identity.get(field)) for field in COMPLETE_RESEARCH_IDENTITY_FIELDS):
        return {}
    return research_identity({"research_identity": explicit_identity})


def context_with_research_identity(context: Any | None) -> dict[str, Any]:
    raw = context_dict(context)
    if not raw:
        return {}

    identity = research_identity(raw)
    if not identity:
        return dict(raw)

    output = dict(raw)
    output["research_identity"] = {
        **_dict_field(output, "research_identity"),
        **identity,
    }
    for key, value in identity.items():
        output.setdefault(key, value)

    company = _dict_field(output, "company")
    if company:
        company = {**company}
        for key in ("market", "company_id", "filing_id", "parse_run_id"):
            if identity.get(key):
                company.setdefault(key, identity[key])
        output["company"] = company

    report = _dict_field(output, "report")
    if report:
        report = {**report}
        for key in ("market", "company_id", "filing_id", "parse_run_id"):
            if identity.get(key):
                report.setdefault(key, identity[key])
        output["report"] = report

    resolved_period = _dict_field(output, "resolved_period")
    if resolved_period:
        resolved_period = {**resolved_period}
        for key in ("filing_id", "parse_run_id", "market"):
            if identity.get(key):
                resolved_period.setdefault(key, identity[key])
        output["resolved_period"] = resolved_period

    postgres = _dict_field(output, "postgres")
    if postgres or identity.get("market"):
        postgres = {**postgres}
        for key in ("market", "company_id", "filing_id", "parse_run_id"):
            if identity.get(key):
                postgres.setdefault(key, identity[key])
        output["postgres"] = postgres

    return output


def mutable_context_dict(context: Any | None) -> dict[str, Any]:
    return context_with_research_identity(context)


def research_identity_line(context: Any | None) -> str | None:
    identity = research_identity(context)
    if not identity:
        return None
    parts = []
    for key, label in (
        ("market", "market"),
        ("company_id", "company_id"),
        ("filing_id", "filing_id"),
        ("parse_run_id", "parse_run_id"),
    ):
        if identity.get(key):
            parts.append(f"{label}={clean_context_value(identity[key])}")
    return " / ".join(parts) if parts else None


def _append_identity_parts(parts: list[str], identity: Mapping[str, str], keys: Sequence[str]) -> None:
    for key in keys:
        if identity.get(key):
            parts.append(f"{key} {clean_context_value(identity[key])}")


def format_research_identity_context(context: Any | None) -> list[str]:
    identity = research_identity(context)
    if not identity:
        return []
    line = research_identity_line(identity)
    return [f"- ResearchIdentity: {line}"] if line else []


def context_company(context: Any | None) -> dict[str, Any]:
    raw = context_dict(context)
    return _dict_field(raw, "company")


def context_company_hint(context: Any | None) -> str:
    raw = context_dict(context)
    if not raw:
        return ""
    company = _dict_field(raw, "company")
    report = _dict_field(raw, "report")
    values = [
        company.get("name"),
        company.get("code"),
        company.get("company_id") or company.get("id"),
        company.get("dir"),
        report.get("title"),
        report.get("filename"),
    ]
    return " ".join(str(item) for item in values if item)


def normalized_intent_text(message: str | None) -> str:
    return re.sub(r"\s+", "", message or "").lower()


def compact_intent_text(message: str | None) -> str:
    return re.sub(r"\s+", "", message or "")


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


def statement_query_applies(
    message: str | None,
    *,
    statement_terms: Sequence[str],
    is_general_assistant_request: Callable[[str], bool],
) -> bool:
    text = compact_intent_text(message)
    if is_general_assistant_request(text):
        return False
    normalized = text.casefold()
    return bool(
        text
        and any(compact_intent_text(str(term)).casefold() in normalized for term in statement_terms)
    )


def note_detail_query_applies(
    message: str | None,
    *,
    note_detail_query_terms: Sequence[str],
    note_detail_exclude_terms: Sequence[str],
    financial_note_metric_terms: Sequence[str],
    statement_terms: Sequence[str],
    is_general_assistant_request: Callable[[str], bool],
) -> bool:
    text = compact_intent_text(message)
    if not text:
        return False
    if is_general_assistant_request(text):
        return False
    if any(term in text for term in note_detail_exclude_terms):
        return False
    has_detail_intent = any(term in text for term in note_detail_query_terms)
    has_note_metric = any(term in text for term in financial_note_metric_terms)
    if statement_query_applies(
        text,
        statement_terms=statement_terms,
        is_general_assistant_request=is_general_assistant_request,
    ) and not (has_detail_intent and has_note_metric):
        return False
    return has_detail_intent


def direct_note_detail_answer_applies(
    message: str | None,
    *,
    note_detail_query_terms: Sequence[str],
    note_detail_exclude_terms: Sequence[str],
    note_detail_direct_terms: Sequence[str],
    note_detail_analysis_terms: Sequence[str],
    financial_note_metric_terms: Sequence[str],
    statement_terms: Sequence[str],
    is_general_assistant_request: Callable[[str], bool],
) -> bool:
    text = compact_intent_text(message)
    if not note_detail_query_applies(
        text,
        note_detail_query_terms=note_detail_query_terms,
        note_detail_exclude_terms=note_detail_exclude_terms,
        financial_note_metric_terms=financial_note_metric_terms,
        statement_terms=statement_terms,
        is_general_assistant_request=is_general_assistant_request,
    ):
        return False
    if any(term in text for term in note_detail_analysis_terms):
        return False
    return any(term in text for term in note_detail_direct_terms)


def financial_note_metric_query_applies(
    message: str | None,
    *,
    note_detail_query_terms: Sequence[str],
    note_detail_exclude_terms: Sequence[str],
    financial_note_metric_terms: Sequence[str],
    financial_evidence_action_terms: Sequence[str],
    statement_terms: Sequence[str],
    is_general_assistant_request: Callable[[str], bool],
) -> bool:
    text = compact_intent_text(message)
    if not text:
        return False
    if is_general_assistant_request(text):
        return False
    if any(term in text for term in note_detail_exclude_terms):
        return False
    has_detail_intent = any(term in text for term in note_detail_query_terms)
    if statement_query_applies(
        text,
        statement_terms=statement_terms,
        is_general_assistant_request=is_general_assistant_request,
    ) and not has_detail_intent:
        return False
    return (
        any(term in text for term in financial_note_metric_terms)
        and any(term in text for term in financial_evidence_action_terms)
    )


def note_detail_context_applies(
    message: str | None,
    *,
    note_detail_query_terms: Sequence[str],
    note_detail_exclude_terms: Sequence[str],
    financial_note_metric_terms: Sequence[str],
    financial_evidence_action_terms: Sequence[str],
    statement_terms: Sequence[str],
    is_general_assistant_request: Callable[[str], bool],
) -> bool:
    return note_detail_query_applies(
        message,
        note_detail_query_terms=note_detail_query_terms,
        note_detail_exclude_terms=note_detail_exclude_terms,
        financial_note_metric_terms=financial_note_metric_terms,
        statement_terms=statement_terms,
        is_general_assistant_request=is_general_assistant_request,
    ) or financial_note_metric_query_applies(
        message,
        note_detail_query_terms=note_detail_query_terms,
        note_detail_exclude_terms=note_detail_exclude_terms,
        financial_note_metric_terms=financial_note_metric_terms,
        financial_evidence_action_terms=financial_evidence_action_terms,
        statement_terms=statement_terms,
        is_general_assistant_request=is_general_assistant_request,
    )


def direct_statement_answer_applies(
    message: str | None,
    *,
    statement_terms: Sequence[str],
    statement_direct_terms: Sequence[str],
    note_detail_analysis_terms: Sequence[str],
    is_general_assistant_request: Callable[[str], bool],
) -> bool:
    text = compact_intent_text(message)
    if not statement_query_applies(
        text,
        statement_terms=statement_terms,
        is_general_assistant_request=is_general_assistant_request,
    ):
        return False
    if any(term in text for term in note_detail_analysis_terms):
        return False
    return any(term in text for term in statement_direct_terms)


def goodwill_main_statement_query_applies(
    message: str | None,
    *,
    goodwill_main_statement_terms: Sequence[str],
    is_general_assistant_request: Callable[[str], bool],
) -> bool:
    text = compact_intent_text(message)
    if not text or is_general_assistant_request(text):
        return False
    return "商誉" in text and any(term in text for term in goodwill_main_statement_terms)


def statement_query_with_goodwill_applies(
    message: str | None,
    *,
    statement_terms: Sequence[str],
    goodwill_main_statement_terms: Sequence[str],
    is_general_assistant_request: Callable[[str], bool],
) -> bool:
    # Goodwill is a balance-sheet net amount even when the user asks for
    # analysis, composition, or impairment details. The note is a second
    # layer; it must never replace the primary balance-sheet fact.
    text = compact_intent_text(message)
    multilingual_statement_terms = (
        "goodwill", "のれん", "영업권", "revenue", "sales", "profit", "netincome",
        "balancesheet", "incomestatement", "cashflow", "totalassets", "liabilities",
        "equity", "営業収益", "営業利益", "当期利益", "財政状態計算書", "キャッシュフロー",
        "영업수익", "영업이익", "재무상태표", "현금흐름표", "매출액", "순이익",
    )
    if text and not is_general_assistant_request(text) and (
        "商誉" in text or any(term in text for term in multilingual_statement_terms)
    ):
        return True
    return goodwill_main_statement_query_applies(
        message,
        goodwill_main_statement_terms=goodwill_main_statement_terms,
        is_general_assistant_request=is_general_assistant_request,
    ) or statement_query_applies(
        message,
        statement_terms=statement_terms,
        is_general_assistant_request=is_general_assistant_request,
    )


def direct_statement_answer_with_goodwill_applies(
    message: str | None,
    *,
    statement_terms: Sequence[str],
    statement_direct_terms: Sequence[str],
    note_detail_analysis_terms: Sequence[str],
    goodwill_main_statement_terms: Sequence[str],
    is_general_assistant_request: Callable[[str], bool],
) -> bool:
    if goodwill_main_statement_query_applies(
        message,
        goodwill_main_statement_terms=goodwill_main_statement_terms,
        is_general_assistant_request=is_general_assistant_request,
    ):
        text = compact_intent_text(message)
        if any(term in text for term in note_detail_analysis_terms):
            return False
        return any(term in text for term in statement_direct_terms)
    return direct_statement_answer_applies(
        message,
        statement_terms=statement_terms,
        statement_direct_terms=statement_direct_terms,
        note_detail_analysis_terms=note_detail_analysis_terms,
        is_general_assistant_request=is_general_assistant_request,
    )


def forced_context_company_dir(context: Any | None, *, wiki_root: Path) -> Path | None:
    raw = context_dict(context)
    if not raw or not raw.get("force_company"):
        return None
    company = _dict_field(raw, "company")
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
    relative = path.relative_to(wiki_root)
    if not relative.parts or relative.parts[0] != "companies":
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
        try:
            resolved_candidate = candidate.resolve()
            resolved_root = wiki_root.resolve()
            relative = resolved_candidate.relative_to(resolved_root)
        except (OSError, ValueError):
            relative = None
        if (
            relative is not None
            and relative.parts
            and relative.parts[0] == "companies"
            and resolved_candidate.exists()
        ):
            company_dir = resolved_candidate
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
    company = _dict_field(raw, "company")
    report = _dict_field(raw, "report")
    page = _dict_field(raw, "page")
    identity = research_identity(raw)
    lines.extend(format_research_identity_context(raw))

    company_parts: list[str] = []
    if company.get("name"):
        company_parts.append(clean_context_value(company["name"]))
    if company.get("code"):
        company_parts.append(f"代码 {clean_context_value(company['code'])}")
    _append_identity_parts(company_parts, identity, ("market", "company_id"))
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
    _append_identity_parts(report_parts, identity, ("filing_id", "parse_run_id"))
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


def get_session_default_context(
    profile: Any,
    session_id: str,
    context: Any | None = None,
    *,
    allow_initialize: bool = False,
    session_default_contexts: MutableMapping[tuple[Any, str], str],
    active_key: Callable[[Any, str], tuple[Any, str]],
    format_chat_context: Callable[[Any | None], str | None],
) -> str | None:
    key = active_key(profile, session_id)
    if key in session_default_contexts:
        cached_context = session_default_contexts[key]
        authoritative_identity = _authoritative_research_identity(context)
        identity_line = research_identity_line(authoritative_identity)
        expected_line = f"- ResearchIdentity: {identity_line}" if identity_line else None
        if not expected_line or expected_line in cached_context.splitlines():
            return cached_context

        formatted_context = format_chat_context(context)
        if formatted_context:
            session_default_contexts[key] = formatted_context
            return formatted_context

        del session_default_contexts[key]
        return None

    if not allow_initialize:
        return None

    formatted_context = format_chat_context(context)
    if formatted_context:
        session_default_contexts[key] = formatted_context
    return formatted_context


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


def build_session_contextual_input(
    message: str,
    *,
    profile: Any,
    profile_label: str,
    session_id: str,
    context: Any | None = None,
    allow_initialize: bool = False,
    local_memory_context: str | None = None,
    is_general_assistant_request: Callable[[str], bool],
    session_default_context: Callable[..., str | None],
    resolve_company_dirs: Callable[[str, Any | None], Sequence[Path]],
    context_for_company_dir: Callable[[Path], Any | None],
    message_for_company: Callable[[str, Path], str],
    build_company_wiki_scope_context: Callable[[str, Any | None], str | None],
    build_human_efficiency_evidence_context: Callable[[str, Any | None], str | None],
    build_human_capital_context: Callable[[str, Any | None], str | None],
    build_three_statement_core_context: Callable[[str, Any | None], str | None],
    build_statement_metric_context: Callable[[str, Any | None], str | None],
    build_note_detail_context: Callable[[str, Any | None], str | None],
    build_wiki_fulltext_fallback_context: Callable[[str, Any | None], str | None],
    build_postgres_fallback_context: Callable[[str, Any | None], str | None],
    build_pdf2md_parse_only_context: Callable[[str, Any | None], str | None],
    general_assistant_context: str,
    chat_output_contract: str,
    financial_calculation_runtime_contract: str,
) -> str:
    if is_general_assistant_request(message):
        return build_general_assistant_context_input(
            message,
            profile=profile,
            profile_label=profile_label,
            general_assistant_context=general_assistant_context,
        )

    default_context = session_default_context(
        profile,
        session_id,
        context,
        allow_initialize=allow_initialize,
    )
    blocks: list[str] = []
    if default_context:
        blocks.append(default_context)
    if local_memory_context:
        blocks.append(local_memory_context)

    resolved_company_dirs = resolve_company_dirs(message, context)
    company_scope_blocks, company_context_items = build_company_context_items(
        message,
        context,
        resolved_company_dirs,
        context_for_company_dir=context_for_company_dir,
        message_for_company=message_for_company,
    )
    blocks.extend(company_scope_blocks)

    has_deterministic_evidence_context = False
    human_capital_context = None
    for scoped_message, scoped_context, _company_dir in company_context_items:
        company_scope_context = build_company_wiki_scope_context(scoped_message, scoped_context)
        if company_scope_context and company_scope_context not in blocks:
            blocks.append(company_scope_context)

        human_efficiency_context = build_human_efficiency_evidence_context(scoped_message, scoped_context)
        if human_efficiency_context and human_efficiency_context not in blocks:
            blocks.append(human_efficiency_context)
            has_deterministic_evidence_context = True

        current_human_capital_context = build_human_capital_context(scoped_message, scoped_context)
        if current_human_capital_context and current_human_capital_context not in blocks:
            blocks.append(current_human_capital_context)
            has_deterministic_evidence_context = True
            human_capital_context = current_human_capital_context

    scoped_message, scoped_context = scoped_evidence_input(
        message,
        context,
        company_context_items,
    )
    if human_capital_context:
        has_deterministic_evidence_context = True
    else:
        three_statement_core_context = build_three_statement_core_context(scoped_message, scoped_context)
        if three_statement_core_context:
            blocks.append(three_statement_core_context)
            has_deterministic_evidence_context = True

        statement_context = build_statement_metric_context(scoped_message, scoped_context)
        if statement_context and statement_context not in blocks:
            blocks.append(statement_context)
            has_deterministic_evidence_context = True

        note_detail_context = build_note_detail_context(scoped_message, scoped_context)
        if note_detail_context:
            blocks.append(note_detail_context)
            has_deterministic_evidence_context = True

    if not has_deterministic_evidence_context:
        wiki_fulltext_context = build_wiki_fulltext_fallback_context(scoped_message, scoped_context)
        if wiki_fulltext_context:
            blocks.append(wiki_fulltext_context)
            has_deterministic_evidence_context = True

    if not has_deterministic_evidence_context:
        postgres_context = build_postgres_fallback_context(scoped_message, scoped_context)
        if postgres_context:
            blocks.append(postgres_context)
            has_deterministic_evidence_context = True

    if not has_deterministic_evidence_context:
        parse_only_context = build_pdf2md_parse_only_context(scoped_message, scoped_context)
        if parse_only_context:
            blocks.append(parse_only_context)

    return build_session_contextual_input_text(
        message,
        blocks,
        chat_output_contract=chat_output_contract,
        financial_calculation_runtime_contract=financial_calculation_runtime_contract,
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


def build_hermes_run_input_payload(
    contextual_text: str,
    *,
    has_attachments: bool,
    document_context: str | None = None,
    image_analysis_context: str | None = None,
    image_path_hints: str | None = None,
    image_data_urls: Sequence[str] | None = None,
    use_hermes_image_fallback: bool = True,
) -> str | list[dict[str, Any]]:
    if not has_attachments:
        return contextual_text
    text = build_hermes_run_text(
        contextual_text,
        document_context=document_context,
        image_analysis_context=image_analysis_context,
        image_path_hints=image_path_hints,
    )
    urls = list(image_data_urls or [])
    if not use_hermes_image_fallback or not any(urls):
        return text
    return build_hermes_multimodal_run_input(text, urls)


def attachment_dicts(attachments: Any | None) -> list[dict[str, Any]]:
    if not attachments:
        return []
    items: list[dict[str, Any]] = []
    for item in attachments:
        if hasattr(item, "model_dump"):
            raw = item.model_dump()
        elif isinstance(item, dict):
            raw = dict(item)
        else:
            continue
        if not isinstance(raw, dict):
            continue
        path = str(raw.get("path") or "").strip()
        if not path:
            continue
        items.append(raw)
    return items


def attachment_dicts_for_kind(attachments: Any | None, kind: str, *, default_kind: str = "") -> list[dict[str, Any]]:
    return [
        item
        for item in attachment_dicts(attachments)
        if str(item.get("kind") or default_kind) == kind
    ]


def image_attachment_dicts(attachments: Any | None) -> list[dict[str, Any]]:
    return attachment_dicts_for_kind(attachments, "image", default_kind="image")


def document_attachment_dicts(attachments: Any | None) -> list[dict[str, Any]]:
    return attachment_dicts_for_kind(attachments, "document")


def should_reuse_recent_attachments(message: str | None, followup_pattern: re.Pattern[str]) -> bool:
    text = compact_intent_text(message)
    if not text:
        return False
    return bool(followup_pattern.search(text))
