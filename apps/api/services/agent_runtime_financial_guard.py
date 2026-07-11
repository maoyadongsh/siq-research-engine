"""String guards for financial calculator and reconciliation traces."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from services.path_config import FINANCIAL_CALCULATOR_SCRIPT, FINANCIAL_RECONCILIATION_VALIDATOR_SCRIPT


FINANCIAL_CALCULATOR_PATH = FINANCIAL_CALCULATOR_SCRIPT
FINANCIAL_RECONCILIATION_VALIDATOR_PATH = FINANCIAL_RECONCILIATION_VALIDATOR_SCRIPT
FINANCIAL_CALCULATOR_PATH_TEXT = str(FINANCIAL_CALCULATOR_PATH)
FINANCIAL_RECONCILIATION_VALIDATOR_PATH_TEXT = str(FINANCIAL_RECONCILIATION_VALIDATOR_PATH)

RUNTIME_STATUS_PREFIXES = ("[已停止]", "[失败]", "[已取消]", "[错误]")
DERIVED_FINANCIAL_TERMS = (
    "人均",
    "每股",
    "同比",
    "环比",
    "增长率",
    "增速",
    "占比",
    "毛利率",
    "净利率",
    "资产负债率",
    "CAGR",
    "复合增长率",
    "折人民币",
    "换算人民币",
    "万元/人",
    "元/人",
    "万欧元/人",
    "欧元/人",
)
CALCULATOR_TRACE_TERMS = (
    "financial_calculator.py",
    "financial_reconciliation_validator.py",
    "## 计算器校验",
    "## 勾稽校验",
    "计算器校验",
    "勾稽校验",
    "operation=",
    "\"operation\"",
)
RECONCILIATION_TRACE_TERMS = (
    "financial_reconciliation_validator.py",
    "## 勾稽校验",
    "勾稽校验",
    "goodwill_reconciliation",
    "note_gross - impairment_allowance",
)
FINANCIAL_TOOL_UNAVAILABLE_PATTERNS = (
    "financial_calculator.py 和 financial_reconciliation_validator.py 当前不可用",
    "financial_calculator.py和financial_reconciliation_validator.py当前不可用",
    "financial_calculator.py 当前不可用",
    "financial_calculator.py当前不可用",
    "financial_reconciliation_validator.py 当前不可用",
    "financial_reconciliation_validator.py当前不可用",
    "financial_calculator.py 不可用",
    "financial_calculator.py不可用",
    "financial_reconciliation_validator.py 不可用",
    "financial_reconciliation_validator.py不可用",
)
RECONCILIATION_SUBJECT_TERMS = (
    "商誉",
    "坏账准备",
    "存货跌价准备",
    "资产减值准备",
    "减值准备",
)
RECONCILIATION_RELATION_TERMS = (
    "原值",
    "账面原值",
    "减值准备",
    "备抵",
    "净额",
    "账面净额",
    "账面价值",
    "勾稽",
    "平衡",
)


def _is_runtime_status_reply(reply: str, *, runtime_status_prefixes: tuple[str, ...] | None = None) -> bool:
    text = (reply or "").lstrip()
    prefixes = RUNTIME_STATUS_PREFIXES if runtime_status_prefixes is None else runtime_status_prefixes
    return any(text.startswith(prefix) for prefix in prefixes)


def _reply_has_derived_financial_metric(reply: str) -> bool:
    text = reply or ""
    return any(term.lower() in text.lower() for term in DERIVED_FINANCIAL_TERMS)


def _reply_has_calculator_trace(reply: str) -> bool:
    text = reply or ""
    return any(term in text for term in CALCULATOR_TRACE_TERMS)


def _reply_has_reconciliation_trace(reply: str) -> bool:
    text = reply or ""
    return any(term in text for term in RECONCILIATION_TRACE_TERMS)


def _reply_has_reconciliation_metric(reply: str) -> bool:
    text = reply or ""
    if not any(term in text for term in RECONCILIATION_SUBJECT_TERMS):
        return False
    relation_hits = sum(1 for term in RECONCILIATION_RELATION_TERMS if term in text)
    if relation_hits >= 2:
        return True
    return "勾稽" in text or ("=" in text and ("原值" in text or "准备" in text))


def append_financial_tool_availability_correction_if_needed(
    reply: str,
    *,
    calculator_path: Path | None = None,
    reconciliation_validator_path: Path | None = None,
) -> str:
    text = reply or ""
    if not any(pattern in text for pattern in FINANCIAL_TOOL_UNAVAILABLE_PATTERNS):
        return reply

    calculator_path = calculator_path or FINANCIAL_CALCULATOR_PATH
    reconciliation_validator_path = reconciliation_validator_path or FINANCIAL_RECONCILIATION_VALIDATOR_PATH
    available_tools: list[str] = []
    if calculator_path.exists():
        available_tools.append(str(calculator_path))
    if reconciliation_validator_path.exists():
        available_tools.append(str(reconciliation_validator_path))
    if not available_tools:
        return reply

    correction = (
        "\n\n## 工具状态纠正\n"
        "- 后端检测到财务计算/勾稽脚本实际存在，并非不可用。"
        "- 若本轮工具调用失败，通常是 CLI 参数位置或参数名错误导致，应按脚本 `--help` 修正后重试。"
        f"\n- 可用脚本：{'; '.join(available_tools)}"
    )
    if "工具状态纠正" in text:
        return reply
    return reply.rstrip() + correction


def append_calculation_trace_warning_if_needed(
    message: str,
    reply: str,
    *,
    runtime_status_prefixes: tuple[str, ...] | None = None,
    calculator_path: Path | None = None,
    reconciliation_validator_path: Path | None = None,
    calculator_path_text: str | None = None,
    reconciliation_validator_path_text: str | None = None,
) -> str:
    if _is_runtime_status_reply(reply, runtime_status_prefixes=runtime_status_prefixes):
        return reply
    calculator_path = calculator_path or FINANCIAL_CALCULATOR_PATH
    reconciliation_validator_path = reconciliation_validator_path or FINANCIAL_RECONCILIATION_VALIDATOR_PATH
    calculator_path_text = calculator_path_text or str(calculator_path)
    reconciliation_validator_path_text = reconciliation_validator_path_text or str(reconciliation_validator_path)
    reply = append_financial_tool_availability_correction_if_needed(
        reply,
        calculator_path=calculator_path,
        reconciliation_validator_path=reconciliation_validator_path,
    )
    needs_calculator_trace = _reply_has_derived_financial_metric(message) or _reply_has_derived_financial_metric(reply)
    needs_reconciliation_trace = _reply_has_reconciliation_metric(message) or _reply_has_reconciliation_metric(reply)
    if not (needs_calculator_trace or needs_reconciliation_trace):
        return reply
    if needs_reconciliation_trace and not _reply_has_reconciliation_trace(reply):
        warning = (
            "\n\n## 计算校验提示\n"
            "- 本轮回答包含或可能包含原值/准备/净额勾稽，但未检测到 `financial_reconciliation_validator.py` 或 `## 勾稽校验` 痕迹。"
            "商誉、坏账准备、存货跌价准备、资产减值准备等口径不能只用普通比例/差额计算替代；"
            f"请使用 `{reconciliation_validator_path_text}` 重新校验后再采信相关结论。"
        )
        return reply.rstrip() + warning
    if _reply_has_calculator_trace(reply):
        return reply
    tool_hint = reconciliation_validator_path_text if needs_reconciliation_trace and not needs_calculator_trace else calculator_path_text
    warning = (
        "\n\n## 计算校验提示\n"
        "- 本轮回答包含或可能包含派生财务指标/原值准备净额勾稽，但未检测到 `financial_calculator.py`、"
        "`financial_reconciliation_validator.py` 或 `## 计算器校验`/`## 勾稽校验` 痕迹。"
        f"请使用 `{tool_hint}` 重新校验后再采信相关数值。"
    )
    return reply.rstrip() + warning


@dataclass(frozen=True)
class FinancialEvidenceContractDependencies:
    build_primary_data_evidence_supplement: Callable[[str, Any | None], str | None]
    merge_primary_data_refs_into_citations: Callable[[str, str | None], str]
    build_human_efficiency_evidence_context: Callable[[str, Any | None], str | None]
    build_three_statement_core_context: Callable[[str, Any | None], str | None]
    is_statement_query: Callable[[str], bool]
    statement_metric_result: Callable[[str, Any | None], tuple[Any, Callable[..., str | None] | None]]
    should_inject_note_detail_context: Callable[[str], bool]
    note_detail_result: Callable[..., tuple[Any, Callable[..., str | None] | None]]
    build_wiki_fulltext_fallback_context: Callable[[str, Any | None], str | None]
    build_postgres_fallback_context: Callable[[str, Any | None], str | None]
    build_pdf2md_parse_only_context: Callable[[str, Any | None], str | None]
    is_runtime_status_reply: Callable[[str], bool]
    invalid_task_ids_in_reply: Callable[[str, Any | None, str], list[str]]
    needs_financial_evidence_contract: Callable[[str, Any | None], bool]
    append_primary_data_evidence_if_needed: Callable[[str, Any | None, str], str]
    append_calculation_trace_warning_if_needed: Callable[[str, str], str]
    has_primary_data_evidence_trace: Callable[[str], bool]
    has_structured_evidence_trace: Callable[[str], bool]


def _render_evidence_result(result: Any, renderer: Callable[..., str | None] | None, *, max_rows: int) -> str | None:
    if not result or renderer is None:
        return None
    try:
        return renderer(result, max_rows=max_rows)
    except Exception:
        return None


def build_financial_evidence_fallback_reply(
    message: str,
    context: Any | None = None,
    *,
    deps: FinancialEvidenceContractDependencies,
) -> str | None:
    """Return deterministic evidence when a model skips required citations."""
    primary_data_supplement = deps.build_primary_data_evidence_supplement(message, context)
    if primary_data_supplement:
        return deps.merge_primary_data_refs_into_citations(
            "## 证据校验\n"
            "- 模型本轮输出缺少主要数据级溯源，后端已补充主要指标、PDF 页、表格/文本块和来源链接。\n"
            "- 需要解释或评价时，应基于 `## 引用来源` 继续组织语言。",
            primary_data_supplement,
        )

    human_efficiency_context = deps.build_human_efficiency_evidence_context(message, context)
    if human_efficiency_context:
        return (
            "## 证据校验\n"
            "- 模型本轮输出缺少指标级财务溯源，后端已补充人效/人均指标底稿。\n"
            "- 以下返回后端确定性解析出的指标、公式、PDF 页和表格入口；需要解释或评价时，应基于这些来源继续分析。\n\n"
            f"{human_efficiency_context}"
        )

    three_statement_context = deps.build_three_statement_core_context(message, context)
    if three_statement_context:
        return (
            "## 证据校验\n"
            "- 模型本轮输出缺少可解析的本地 Wiki 三大表证据引用，后端已阻断该事实答案。\n"
            "- 以下返回后端确定性解析出的三大表核心底稿；需要润色或解释时，应基于这些来源继续组织语言。\n\n"
            f"{three_statement_context}"
        )

    if deps.is_statement_query(message):
        result, renderer = deps.statement_metric_result(message, context)
        body = _render_evidence_result(result, renderer, max_rows=40)
        if body:
            return (
                "## 证据校验\n"
                "- 模型本轮输出缺少可解析的本地 Wiki 证据引用，后端已阻断该事实答案。\n"
                "- 以下返回后端确定性解析出的主表证据；需要解释或评价时，应基于这些来源继续分析。\n\n"
                f"{body}"
            )

    if deps.should_inject_note_detail_context(message):
        result, renderer = deps.note_detail_result(message, context, limit=8)
        body = _render_evidence_result(result, renderer, max_rows=80)
        if body:
            return (
                "## 证据校验\n"
                "- 模型本轮输出缺少可解析的本地 Wiki 证据引用，后端已阻断该事实答案。\n"
                "- 以下返回后端确定性解析出的附注证据；需要解释或评价时，应基于这些来源继续分析。\n\n"
                f"{body}"
            )

    wiki_fulltext_context = deps.build_wiki_fulltext_fallback_context(message, context)
    if wiki_fulltext_context:
        return (
            "## 证据校验\n"
            "- 模型本轮输出缺少可解析的结构化 Wiki 证据引用；后端已改用完整年报 Markdown 和完整 document_full.json 兜底检索。\n"
            "- 以下返回后端确定性检索出的原文证据；需要解释或评价时，应基于这些来源继续分析。\n\n"
            f"{wiki_fulltext_context}"
        )

    postgres_context = deps.build_postgres_fallback_context(message, context)
    if postgres_context:
        return (
            "## 证据校验\n"
            "- 模型本轮输出缺少可解析的本地 Wiki 证据引用，且 Wiki 确定性解析未返回足够证据。\n"
            "- 以下返回后端只读查询 PostgreSQL `pdf2md` 得到的补充证据；需要解释或评价时，应基于这些来源继续分析。\n\n"
            f"{postgres_context}"
        )

    parse_only_context = deps.build_pdf2md_parse_only_context(message, context)
    if parse_only_context:
        return (
            "## 证据校验\n"
            "- 模型本轮输出缺少可解析的本地 Wiki 证据引用；后端发现该报告尚未进入 Wiki，只返回真实 pdf2md 解析产物目录。\n"
            "- 原回答已被阻断；需要事实答案时，请基于下列 `result.md` / `document_full.json` / `financial_data.json` 重新定位证据。\n\n"
            f"{parse_only_context}"
        )
    return None


def build_invalid_task_id_evidence_reply(
    message: str,
    context: Any | None,
    invalid_task_ids: list[str],
    *,
    deps: FinancialEvidenceContractDependencies,
) -> str:
    fallback = build_financial_evidence_fallback_reply(message, context, deps=deps)
    if fallback:
        return (
            "## 证据链无效\n"
            "- 模型本轮输出引用了本地不存在的 `task_id`，后端已阻断原回答并改用确定性证据返回。\n"
            f"- 无效 task_id: {', '.join(invalid_task_ids)}\n\n"
            f"{fallback}"
        )
    return (
        "## 证据链无效\n"
        "- 模型本轮输出引用了本地不存在的 `task_id`，后端已阻断原回答，避免伪造引用进入历史。\n"
        f"- 无效 task_id: {', '.join(invalid_task_ids)}\n"
        "- 当前后端未检索到可替换的本地 Wiki / pdf2md 确定性证据。请先完成对应 PDF 解析入库，或明确指定一个已存在的解析任务。"
    )


def enforce_financial_evidence_contract(
    message: str,
    context: Any | None,
    reply: str,
    *,
    deps: FinancialEvidenceContractDependencies,
) -> str:
    """Do not let financial fact answers enter history without structured evidence."""
    if deps.is_runtime_status_reply(reply):
        return reply
    invalid_task_ids = deps.invalid_task_ids_in_reply(message, context, reply)
    if invalid_task_ids:
        return build_invalid_task_id_evidence_reply(message, context, invalid_task_ids, deps=deps)
    if not deps.needs_financial_evidence_contract(message, context):
        return reply
    reply = deps.append_primary_data_evidence_if_needed(message, context, reply)
    reply = deps.append_calculation_trace_warning_if_needed(message, reply)
    if deps.has_primary_data_evidence_trace(reply) or deps.has_structured_evidence_trace(reply):
        invalid_task_ids = deps.invalid_task_ids_in_reply(message, context, reply)
        if invalid_task_ids:
            return build_invalid_task_id_evidence_reply(message, context, invalid_task_ids, deps=deps)
        return reply
    fallback = build_financial_evidence_fallback_reply(message, context, deps=deps)
    return fallback or reply
