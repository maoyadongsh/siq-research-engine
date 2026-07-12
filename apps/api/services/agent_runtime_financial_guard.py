"""String guards for financial calculator and reconciliation traces."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from services import agent_runtime_context
from services.agent_runtime_financial_claim_verifier import (
    ClaimVerificationResult,
    validate_calculation_traces,
    verify_financial_claims,
)
from services.path_config import FINANCIAL_CALCULATOR_SCRIPT, FINANCIAL_RECONCILIATION_VALIDATOR_SCRIPT

FINANCIAL_CALCULATOR_PATH = FINANCIAL_CALCULATOR_SCRIPT
FINANCIAL_RECONCILIATION_VALIDATOR_PATH = FINANCIAL_RECONCILIATION_VALIDATOR_SCRIPT
FINANCIAL_CALCULATOR_PATH_TEXT = str(FINANCIAL_CALCULATOR_PATH)
FINANCIAL_RECONCILIATION_VALIDATOR_PATH_TEXT = str(FINANCIAL_RECONCILIATION_VALIDATOR_PATH)

RUNTIME_STATUS_PREFIXES = ("[已停止]", "[失败]", "[已取消]", "[错误]")
DERIVED_FINANCIAL_TERMS = (
    "人均",
    # Directly reported per-share fields (EPS/book value per share) are
    # evidence-bound facts, not calculator outputs.  Keep only generic
    # per-share wording here; explicit derived per-share metrics are listed
    # below so they still require a calculator trace.
    "同比",
    "环比",
    "增长率",
    "增速",
    "占比",
    "毛利率",
    "净利率",
    "资产负债率",
    "净资产收益率",
    "总资产收益率",
    "股本回报率",
    "资产回报率",
    "净息差",
    "净利息收益率",
    "ROE",
    "ROA",
    "NIM",
    "CAGR",
    "复合增长率",
    "折人民币",
    "换算人民币",
    "万元/人",
    "元/人",
    "万欧元/人",
    "欧元/人",
)
DERIVED_PER_SHARE_TERMS = (
    "每股营收",
    "每股收入",
    "每股现金流",
    "每股经营现金流",
    "每股自由现金流",
    "每股利润",
    "每股净利润",
    "每股增长",
    "每股同比",
    "每股环比",
)
DIRECT_REPORTED_PER_SHARE_TERMS = (
    "基本每股收益",
    "稀释每股收益",
    "每股收益",
    "每股净资产",
    "每股账面价值",
    "eps",
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
RECONCILIATION_RELATION_GROUPS = (
    ("原值", "账面原值"),
    ("减值准备", "坏账准备", "跌价准备", "备抵"),
    ("净额", "账面净额", "账面价值"),
)
FINANCIAL_EVIDENCE_MISSING_GUARDRAIL_REASON = "financial_evidence_missing"
FINANCIAL_CLAIM_MISMATCH_GUARDRAIL_REASON = "financial_claim_mismatch"
FINANCIAL_EVIDENCE_IDENTITY_MISMATCH_GUARDRAIL_REASON = "financial_evidence_identity_mismatch"
FINANCIAL_CALCULATION_TRACE_MISSING_GUARDRAIL_REASON = "financial_calculation_trace_missing"
FINANCIAL_RESEARCH_IDENTITY_INCOMPLETE_GUARDRAIL_REASON = "financial_research_identity_incomplete"
FINANCIAL_GUARDRAIL_MODE_ENV = "SIQ_FINANCIAL_GUARDRAIL_MODE"


def financial_guardrail_mode() -> str:
    """Return ``block`` (safe default) or ``warn`` (retain output for tuning)."""
    mode = str(os.environ.get(FINANCIAL_GUARDRAIL_MODE_ENV) or "block").strip().lower()
    return mode if mode in {"block", "warn"} else "block"


def _observation_reply(original_reply: str, diagnostic: str) -> str:
    """Keep the model output while attaching the exact diagnostic that would block it."""
    notice = (diagnostic or "").replace("原始模型回答已被阻断", "原始模型回答已保留（观察模式，仅供调试）")
    notice = notice.replace("后端已阻断原始模型回答", "后端未阻断原始模型回答（观察模式，仅供调试）")
    notice = notice.replace("后端已阻断原回答", "后端未阻断原回答（观察模式，仅供调试）")
    notice = notice.replace("guardrail_status=blocked", "guardrail_status=warning")
    if "guardrail_status=" not in notice:
        notice = f"{notice.rstrip()}\n\nguardrail_status=warning"
    return f"{(original_reply or '').rstrip()}\n\n{notice.strip()}"


def _is_runtime_status_reply(reply: str, *, runtime_status_prefixes: tuple[str, ...] | None = None) -> bool:
    text = (reply or "").lstrip()
    prefixes = RUNTIME_STATUS_PREFIXES if runtime_status_prefixes is None else runtime_status_prefixes
    return any(text.startswith(prefix) for prefix in prefixes)


def _financial_claim_text(reply: str) -> str:
    return "\n".join(
        line
        for line in (reply or "").splitlines()
        if "source_type=" not in line and not line.lstrip().startswith(("guardrail_", "claim_verifier_"))
    )


def _reply_has_derived_financial_metric(reply: str) -> bool:
    text = _financial_claim_text(reply)
    lowered = text.lower()
    if any(term.lower() in lowered for term in DERIVED_PER_SHARE_TERMS):
        return True
    # A bare "每股" request is ambiguous and remains conservative, but a
    # direct reported field such as 基本每股收益/EPS should pass with its
    # structured evidence citation and need no synthetic calculator trace.
    if "每股" in text and not any(term.lower() in lowered for term in DIRECT_REPORTED_PER_SHARE_TERMS):
        return True
    return any(term.lower() in lowered for term in DERIVED_FINANCIAL_TERMS)


def _reply_has_calculator_trace(reply: str) -> bool:
    text = reply or ""
    return any(term in text for term in CALCULATOR_TRACE_TERMS)


def _reply_has_reconciliation_trace(reply: str) -> bool:
    text = reply or ""
    return any(term in text for term in RECONCILIATION_TRACE_TERMS)


def _reply_has_reconciliation_metric(reply: str) -> bool:
    text = "\n".join(
        line
        for line in _financial_claim_text(reply).splitlines()
        if "financial_reconciliation_validator.py" not in line
        and "siq_financial_reconciliation_trace_v1" not in line
        and "勾稽校验" not in line
    )
    if not any(term in text for term in RECONCILIATION_SUBJECT_TERMS):
        return False
    relation_hits = sum(1 for terms in RECONCILIATION_RELATION_GROUPS if any(term in text for term in terms))
    # A reconciliation claim must cover all three legs.  Merely discussing
    # gross and allowance is direct note disclosure, not a derived net tie-out.
    if relation_hits >= 3:
        return True
    return "勾稽" in text or ("=" in text and ("原值" in text or "准备" in text))


def _required_calculator_operations(message: str, reply: str) -> frozenset[str]:
    text = f"{_financial_claim_text(message)}\n{_financial_claim_text(reply)}".lower()
    operations: set[str] = set()
    if "cagr" in text or "复合增长率" in text:
        operations.add("cagr")
    if "同比" in text or "环比" in text or "增长率" in text or "增速" in text:
        operations.update({"yoy", "yoy_growth"})
    if "人均" in text or "/人" in text:
        operations.add("per_capita")
    if any(term in text for term in ("占比", "毛利率", "净利率", "资产负债率", "收益率", "回报率", "净息差")):
        operations.add("ratio")
    return frozenset(operations)


def requires_financial_calculation_trace(message: str, reply: str) -> bool:
    """Return whether the answer needs a trusted calculator or reconciliation receipt."""

    return (
        _reply_has_derived_financial_metric(message)
        or _reply_has_derived_financial_metric(reply)
        or _reply_has_reconciliation_metric(message)
        or _reply_has_reconciliation_metric(reply)
    )


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


def _calculation_trace_missing_reason(message: str, reply: str) -> str:
    if _is_runtime_status_reply(reply):
        return ""
    needs_calculator_trace = _reply_has_derived_financial_metric(message) or _reply_has_derived_financial_metric(reply)
    needs_reconciliation_trace = _reply_has_reconciliation_metric(message) or _reply_has_reconciliation_metric(reply)
    if needs_reconciliation_trace and not _reply_has_reconciliation_trace(reply):
        return "reconciliation_trace_missing"
    if needs_calculator_trace and not _reply_has_calculator_trace(reply):
        return "calculator_trace_missing"
    return ""


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


def build_missing_financial_evidence_guardrail_reply(message: str, context: Any | None = None) -> str:
    return (
        "## 证据不足\n"
        "- 后端检测到本轮问题涉及财务事实、数值或指标，但未检索到可核验的确定性证据、Wiki 引用、"
        "PostgreSQL agent fact 或 fallback 证据。\n"
        "- 原始模型回答已被阻断，避免无证据财务数值进入历史或被误用。\n"
        "- 当前不能确定该财务事实；请先指定已入库报告、完成 PDF 解析入库，或提供可核验来源后重试。\n\n"
        "guardrail_status=blocked\n"
        f"guardrail_reason={FINANCIAL_EVIDENCE_MISSING_GUARDRAIL_REASON}"
    )


def _record_incomplete_research_identity_event(
    context: Any | None,
    *,
    market: str,
    missing_fields: tuple[str, ...],
) -> None:
    if not isinstance(context, dict):
        return
    event = {
        "reason": "research_identity_incomplete",
        "stage": "financial_answer_blocked_for_non_cn_market",
        "source": "research_identity_guard",
        "detail": f"market={market} missing={','.join(missing_fields)}",
    }
    context.setdefault("_audit_fallback_events", []).append(event)
    context["fallback_reason"] = "research_identity_incomplete"


def build_incomplete_research_identity_guardrail_reply(
    *,
    market: str,
    missing_fields: tuple[str, ...],
) -> str:
    return (
        "## 研究身份不完整\n"
        f"- 本轮问题涉及 {market} 市场财务事实，但请求缺少完整 ResearchIdentity，后端已阻断原始模型回答。\n"
        "- 非 A 市场金融问答必须明确绑定 market/company_id/filing_id/parse_run_id，"
        "不得依赖目录猜测、最近一次解析或 A 股 legacy PostgreSQL fallback。\n"
        f"- identity_market={market}\n"
        f"- identity_missing_fields={','.join(missing_fields)}\n\n"
        "guardrail_status=blocked\n"
        f"guardrail_reason={FINANCIAL_RESEARCH_IDENTITY_INCOMPLETE_GUARDRAIL_REASON}"
    )


def build_missing_calculation_trace_guardrail_reply(message: str, reply: str, reason: str) -> str:
    tool = (
        "financial_reconciliation_validator.py"
        if reason == "reconciliation_trace_missing"
        else "financial_calculator.py"
    )
    return (
        "## 计算校验缺失\n"
        "- 后端检测到本轮回答涉及派生财务指标、比例、增长率、人均/派生每股指标或原值/准备/净额勾稽，"
        "但未检测到对应的确定性计算器/勾稽 trace。\n"
        "- 原始模型回答已被阻断，避免未校验的派生数值进入历史或被误用。\n"
        f"- 请使用 `{tool}` 生成 `## 计算器校验` / `## 勾稽校验` trace 后重试。\n\n"
        "guardrail_status=blocked\n"
        f"guardrail_reason={FINANCIAL_CALCULATION_TRACE_MISSING_GUARDRAIL_REASON}\n"
        f"calculation_trace_reason={reason}"
    )


def build_invalid_calculation_trace_guardrail_reply(reason: str) -> str:
    return (
        "## 计算校验无效\n"
        "- 后端检测到计算/勾稽 trace 不是完整结构化运行记录，或其 operation、metric、period、inputs、result、"
        "ResearchIdentity、证据绑定或确定性重算不一致。\n"
        "- 工具名、章节标题和手写 operation/result 文本不构成可信 trace；原始模型回答已被阻断。\n\n"
        "guardrail_status=blocked\n"
        f"guardrail_reason={FINANCIAL_CALCULATION_TRACE_MISSING_GUARDRAIL_REASON}\n"
        f"calculation_trace_reason={reason}"
    )


def _has_identity_violation(verification: ClaimVerificationResult) -> bool:
    return any(
        violation.reason.startswith("missing_")
        or violation.reason.endswith(("market_mismatch", "company_id_mismatch", "filing_id_mismatch", "parse_run_id_mismatch"))
        for violation in verification.violations
        if violation.expected_company_id
    )


def build_financial_claim_mismatch_guardrail_reply(verification: ClaimVerificationResult) -> str:
    identity_mismatch = _has_identity_violation(verification)
    lines = [
        "## 财务证据身份不一致" if identity_mismatch else "## 财务数值证据不一致",
        (
            "- 后端检测到本轮回答中的证据身份与请求绑定的 ResearchIdentity 不一致，原始模型回答已被阻断。"
            if identity_mismatch
            else "- 后端检测到本轮回答中的财务数值 claim 与结构化证据行不一致，原始模型回答已被阻断。"
        ),
        (
            "- 请仅使用与请求 market/company_id/filing_id/parse_run_id 完全一致的证据重新回答。"
            if identity_mismatch
            else "- 请基于下列 evidence value 重新改写答案；不要保留原回答中的错配数值。"
        ),
    ]
    for index, violation in enumerate(verification.violations[:5], start=1):
        if violation.expected_company_id:
            lines.append(
                "- identity_mismatch_{index}: reason={reason} "
                "expected={expected_market}/{expected_company_id}/{expected_filing_id}/{expected_parse_run_id} "
                "actual={market}/{company_id}/{filing_id}/{parse_run_id} evidence_id={evidence_id}".format(
                    index=index,
                    reason=violation.reason,
                    expected_market=violation.expected_market or "missing",
                    expected_company_id=violation.expected_company_id or "missing",
                    expected_filing_id=violation.expected_filing_id or "missing",
                    expected_parse_run_id=violation.expected_parse_run_id or "missing",
                    market=violation.market or "missing",
                    company_id=violation.company_id or "missing",
                    filing_id=violation.filing_id or "missing",
                    parse_run_id=violation.parse_run_id or "missing",
                    evidence_id=violation.evidence_id or "unknown",
                )
            )
            continue
        evidence_ref = violation.evidence_id or violation.filing_id or "unknown"
        period = violation.period or "unknown"
        lines.append(
            "- mismatch_{index}: reason={reason} metric={metric} period={period} claimed_period={claimed_period} "
            "claimed={claimed:g}{claimed_unit} claimed_currency={claimed_currency} "
            "evidence={evidence:g}{evidence_unit} evidence_currency={evidence_currency} evidence_id={evidence_id}".format(
                index=index,
                reason=violation.reason,
                metric=violation.metric,
                period=period,
                claimed_period=violation.claimed_period or "unknown",
                claimed=violation.claimed_value,
                claimed_unit=violation.claimed_unit,
                claimed_currency=violation.claimed_currency or "unknown",
                evidence=violation.evidence_value,
                evidence_unit=violation.evidence_unit,
                evidence_currency=violation.evidence_currency or "unknown",
                evidence_id=evidence_ref,
            )
        )
    lines.extend(
        [
            "guardrail_status=blocked",
            "guardrail_reason="
            + (
                FINANCIAL_EVIDENCE_IDENTITY_MISMATCH_GUARDRAIL_REASON
                if identity_mismatch
                else FINANCIAL_CLAIM_MISMATCH_GUARDRAIL_REASON
            ),
            "claim_verifier_status=failed",
        ]
    )
    return "\n".join(lines)


def enforce_financial_evidence_contract(
    message: str,
    context: Any | None,
    reply: str,
    *,
    deps: FinancialEvidenceContractDependencies,
    trusted_calculation_runs: Sequence[Mapping[str, Any]] = (),
) -> str:
    """Do not let financial fact answers enter history without structured evidence."""
    if deps.is_runtime_status_reply(reply):
        return reply
    needs_financial_evidence = deps.needs_financial_evidence_contract(message, context)
    if needs_financial_evidence:
        market, missing_fields = agent_runtime_context.incomplete_non_cn_research_identity(context)
        if market and missing_fields:
            _record_incomplete_research_identity_event(
                context,
                market=market,
                missing_fields=missing_fields,
            )
            diagnostic = build_incomplete_research_identity_guardrail_reply(
                market=market,
                missing_fields=missing_fields,
            )
            if financial_guardrail_mode() == "warn":
                return _observation_reply(reply, diagnostic)
            return diagnostic
    invalid_task_ids = deps.invalid_task_ids_in_reply(message, context, reply)
    if invalid_task_ids:
        diagnostic = build_invalid_task_id_evidence_reply(message, context, invalid_task_ids, deps=deps)
        if financial_guardrail_mode() == "warn":
            return _observation_reply(reply, diagnostic)
        return diagnostic
    if not needs_financial_evidence:
        return reply
    # Complete the evidence chain before validating calculation traces. In
    # observation mode an invalid/missing trace preserves the model answer;
    # the preserved answer must still carry main statement, body table, and
    # note citations in deterministic priority order.
    reply = deps.append_primary_data_evidence_if_needed(message, context, reply)
    needs_calculator_trace = _reply_has_derived_financial_metric(message) or _reply_has_derived_financial_metric(reply)
    needs_reconciliation_trace = _reply_has_reconciliation_metric(message) or _reply_has_reconciliation_metric(reply)
    calculation_trace = validate_calculation_traces(
        reply,
        expected_identity=agent_runtime_context.research_identity(context),
        require_calculator=needs_calculator_trace,
        require_reconciliation=needs_reconciliation_trace,
        expected_operations=_required_calculator_operations(message, reply),
        trusted_runs=trusted_calculation_runs,
    )
    if calculation_trace.checked and not calculation_trace.allowed:
        if calculation_trace.reason in {"calculator_trace_missing", "reconciliation_trace_missing", "trace_unstructured"} and not (
            _reply_has_calculator_trace(reply) or _reply_has_reconciliation_trace(reply)
        ):
            missing_reason = "reconciliation_trace_missing" if needs_reconciliation_trace else "calculator_trace_missing"
            diagnostic = build_missing_calculation_trace_guardrail_reply(message, reply, missing_reason)
        else:
            diagnostic = build_invalid_calculation_trace_guardrail_reply(calculation_trace.reason)
        if financial_guardrail_mode() == "warn":
            return _observation_reply(reply, diagnostic)
        return diagnostic
    reply = deps.append_calculation_trace_warning_if_needed(message, reply)
    if deps.has_primary_data_evidence_trace(reply) or deps.has_structured_evidence_trace(reply):
        invalid_task_ids = deps.invalid_task_ids_in_reply(message, context, reply)
        if invalid_task_ids:
            diagnostic = build_invalid_task_id_evidence_reply(message, context, invalid_task_ids, deps=deps)
            if financial_guardrail_mode() == "warn":
                return _observation_reply(reply, diagnostic)
            return diagnostic
        claim_verification = verify_financial_claims(
            reply,
            expected_identity=agent_runtime_context.research_identity(context),
        )
        if not claim_verification.allowed:
            diagnostic = build_financial_claim_mismatch_guardrail_reply(claim_verification)
            if financial_guardrail_mode() == "warn":
                return _observation_reply(reply, diagnostic)
            return diagnostic
        return reply
    fallback = build_financial_evidence_fallback_reply(message, context, deps=deps)
    diagnostic = fallback or build_missing_financial_evidence_guardrail_reply(message, context)
    if financial_guardrail_mode() == "warn":
        return _observation_reply(reply, diagnostic)
    return diagnostic
