"""String guards for financial calculator and reconciliation traces."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from services import agent_runtime_context
from services.agent_runtime_financial_claim_verifier import (
    FINANCIAL_MINUS_SIGN_CLASS,
    ClaimVerificationResult,
    has_evidence_bound_unit_normalization,
    validate_calculation_traces,
    verify_financial_claims,
)
from services.agent_runtime_guardrail_text import strip_guardrail_diagnostics
from services.path_config import FINANCIAL_CALCULATOR_SCRIPT, FINANCIAL_RECONCILIATION_VALIDATOR_SCRIPT

FINANCIAL_CALCULATOR_PATH = FINANCIAL_CALCULATOR_SCRIPT
FINANCIAL_RECONCILIATION_VALIDATOR_PATH = FINANCIAL_RECONCILIATION_VALIDATOR_SCRIPT
FINANCIAL_CALCULATOR_PATH_TEXT = str(FINANCIAL_CALCULATOR_PATH)
FINANCIAL_RECONCILIATION_VALIDATOR_PATH_TEXT = str(FINANCIAL_RECONCILIATION_VALIDATOR_PATH)

RUNTIME_STATUS_PREFIXES = ("[已停止]", "[失败]", "[已取消]", "[错误]")
EXTERNAL_TOOL_LOOP_GUARD_MARKERS = (
    "same_tool_failure_halt",
    "i stopped retrying terminal",
    "[tool loop hard stop:",
)
YOY_FINANCIAL_TERMS = (
    "同比",
    "环比",
    "yoy",
    "增长率",
    "增速",
    "增幅",
    "降幅",
    "增长幅度",
    "下降幅度",
)
YOY_CHANGE_TERMS = ("增长", "下降", "上升", "减少", "增加", "降低", "提升", "下滑")
_YOY_CHANGE_PATTERN = "|".join(YOY_CHANGE_TERMS)
_YOY_PERCENTAGE_CHANGE_RE = re.compile(
    rf"(?:{_YOY_CHANGE_PATTERN})[^，。；;\n]{{0,12}}?[+{FINANCIAL_MINUS_SIGN_CLASS}]?\d[\d,.]*\s*(?:%|％|个?\s*百分点)"
    rf"|[+{FINANCIAL_MINUS_SIGN_CLASS}]?\d[\d,.]*\s*(?:%|％|个?\s*百分点)[^，。；;\n]{{0,6}}?(?:的)?(?:{_YOY_CHANGE_PATTERN})",
    re.IGNORECASE,
)
_YOY_COMPARISON_RE = re.compile(
    r"(?:较|比|相较(?:于)?|对比)\s*(?:\d{4}\s*年?|上年|上一年|去年|上期|前期|同期|年初|期初)"
    r"|与\s*(?:\d{4}\s*年?|上年|上一年|去年|上期|前期|同期|年初|期初)[^，。；;\n]{0,16}?相比",
    re.IGNORECASE,
)
_RATIO_PERCENTAGE_RE = re.compile(
    r"(?:占(?!用|款)[^，。；;\n]{0,32}?|(?:占用率|占款比例|覆盖率)[^，。；;\n]{0,16}?)"
    rf"[+{FINANCIAL_MINUS_SIGN_CLASS}]?\d[\d,.]*\s*(?:%|％|个?\s*百分点)",
    re.IGNORECASE,
)
_AMOUNT_NORMALIZATION_RE = re.compile(
    rf"[+{FINANCIAL_MINUS_SIGN_CLASS}]?\d[\d,，]*(?:\.\d+)?\s*"
    r"(?P<unit>人民币千元|人民币元|千元|万元|百万元|亿元|元|million|billion|thousand)",
    re.IGNORECASE,
)
_AMOUNT_UNIT_SCALES = {
    "人民币元": 1,
    "元": 1,
    "人民币千元": 1_000,
    "千元": 1_000,
    "thousand": 1_000,
    "万元": 10_000,
    "百万元": 1_000_000,
    "million": 1_000_000,
    "亿元": 100_000_000,
    "billion": 1_000_000_000,
}
DERIVED_FINANCIAL_TERMS = (
    "人均",
    # Directly reported per-share fields (EPS/book value per share) are
    # evidence-bound facts, not calculator outputs.  Keep only generic
    # per-share wording here; explicit derived per-share metrics are listed
    # below so they still require a calculator trace.
    *YOY_FINANCIAL_TERMS,
    "占比",
    "覆盖率",
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
NEGATED_DERIVED_TERM_MARKERS = (
    "未计算",
    "不计算",
    "不要计算",
    "无需计算",
    "未使用",
    "未涉及",
    "不涉及",
    "不适用",
    "不含",
    "仅作上下文",
    "仅作参考",
)
PER_CAPITA_CALCULATION_TERMS = (
    "人均",
    "每人",
    "/人",
    "／人",
    "万元/人",
    "元/人",
    "万欧元/人",
    "欧元/人",
)
PER_CAPITA_EVIDENCE_GAP_MARKERS = (
    "未返回",
    "未披露",
    "未结构化",
    "未收录",
    "未提供",
    "未获取",
    "未找到",
    "未涉及",
    "不涉及",
    "不含",
    "不适用",
    "没有可引用",
    "无可引用",
    "缺少",
    "缺口",
    "无法计算",
    "不能计算",
    "不可计算",
    "无法拆出",
    "不能拆出",
    "不得补全",
    "留白",
)

_NEGATED_DERIVED_MARKER_PATTERN = "|".join(
    re.escape(marker) for marker in sorted(NEGATED_DERIVED_TERM_MARKERS, key=len, reverse=True)
)
_DERIVED_TERM_PATTERN = "|".join(
    re.escape(term)
    for term in sorted((*DERIVED_FINANCIAL_TERMS, *DERIVED_PER_SHARE_TERMS, "每股"), key=len, reverse=True)
)
_NEGATED_DERIVED_TERM_RE = re.compile(
    rf"(?:{_NEGATED_DERIVED_MARKER_PATTERN})\s*(?:任何\s*)?(?:(?:{_DERIVED_TERM_PATTERN})(?:\s*(?:、|和|及|或|/)\s*)?)+",
    re.IGNORECASE,
)
_PER_CAPITA_TERM_RE = re.compile(
    r"(?:人均|每人|每名员工|每位员工|"
    r"(?:元|万元|千元|百万元|亿元|美元|欧元|港元|日元|韩元|million|billion)\s*[/／]\s*人)",
    re.IGNORECASE,
)


def financial_guardrail_mode() -> str:
    """Return ``block`` (safe default) or ``warn`` (retain output for tuning)."""
    mode = str(os.environ.get(FINANCIAL_GUARDRAIL_MODE_ENV) or "block").strip().lower()
    return mode if mode in {"block", "warn"} else "block"


def _may_observe_calculation_failure(reason: str) -> bool:
    """Preserve output only while calculation validity is still unknown."""

    return reason in {
        "calculator_trace_missing",
        "reconciliation_trace_missing",
        "trace_unstructured",
    }


def _observation_reply(original_reply: str, diagnostic: str) -> str:
    """Keep the model output while attaching the exact diagnostic that would block it."""
    notice = (diagnostic or "").replace("原始模型回答已被阻断", "原始模型回答已保留（观察模式，仅供调试）")
    notice = notice.replace("后端已阻断原始模型回答", "后端未阻断原始模型回答（观察模式，仅供调试）")
    notice = notice.replace("后端已阻断原回答", "后端未阻断原回答（观察模式，仅供调试）")
    notice = notice.replace("guardrail_status=blocked", "guardrail_status=warning")
    if "guardrail_status=" not in notice:
        notice = f"{notice.rstrip()}\n\nguardrail_status=warning"
    clean_reply = strip_guardrail_diagnostics(original_reply).rstrip()
    if not clean_reply:
        return notice.strip()
    return f"{clean_reply}\n\n{notice.strip()}"


def _is_runtime_status_reply(reply: str, *, runtime_status_prefixes: tuple[str, ...] | None = None) -> bool:
    text = (reply or "").lstrip()
    prefixes = RUNTIME_STATUS_PREFIXES if runtime_status_prefixes is None else runtime_status_prefixes
    return any(text.startswith(prefix) for prefix in prefixes)


def _financial_claim_text(reply: str) -> str:
    text = strip_guardrail_diagnostics(reply)
    return "\n".join(
        line
        for line in text.splitlines()
        if "source_type=" not in line and not line.lstrip().startswith(("guardrail_", "claim_verifier_"))
    )


def _calculation_claim_text(text: str) -> str:
    meaningful_lines = []
    for line in _financial_claim_text(text).splitlines():
        cleaned_line = _NEGATED_DERIVED_TERM_RE.sub("", line)
        if cleaned_line.strip():
            meaningful_lines.append(cleaned_line)
    return "\n".join(meaningful_lines)


def _has_per_capita_term(text: str) -> bool:
    return bool(_PER_CAPITA_TERM_RE.search(text or ""))


def _has_contextual_per_capita(text: str) -> bool:
    for clause in re.split(r"[。！？；;\n]|(?:但|但是|不过|然而)", text or ""):
        if not _has_per_capita_term(clause):
            continue
        if any(marker in clause for marker in PER_CAPITA_EVIDENCE_GAP_MARKERS):
            continue
        return True
    return False


def _has_contextual_yoy_change(text: str) -> bool:
    for line in (text or "").splitlines():
        if not any(term in line for term in YOY_CHANGE_TERMS):
            continue
        if _YOY_PERCENTAGE_CHANGE_RE.search(line) or _YOY_COMPARISON_RE.search(line):
            return True
    return False


def _has_contextual_ratio(text: str) -> bool:
    ratio_terms = (
        "占比", "比例", "比重", "覆盖比", "覆盖率", "资产负债率",
        "流动比率", "速动比率", "现金比率", "毛利率", "净利率",
        "收益率", "回报率", "净息差",
    )
    percent_re = re.compile(rf"[+{FINANCIAL_MINUS_SIGN_CLASS}]?\d[\d,.]*\s*(?:%|％)")
    for line in (text or "").splitlines():
        if _RATIO_PERCENTAGE_RE.search(line):
            return True
        for clause in re.split(r"[。；;\n]", line):
            for match in percent_re.finditer(clause):
                prefix = clause[max(0, match.start() - 240) : match.start()]
                if any(term in prefix for term in ratio_terms):
                    return True
                if "/" in prefix and len(_AMOUNT_NORMALIZATION_RE.findall(prefix)) >= 2:
                    return True
    return False


def _has_contextual_amount_normalization(text: str) -> bool:
    for line in (text or "").splitlines():
        matches = list(_AMOUNT_NORMALIZATION_RE.finditer(line))
        for previous, current in zip(matches, matches[1:], strict=False):
            previous_scale = _AMOUNT_UNIT_SCALES.get(previous.group("unit").lower())
            current_scale = _AMOUNT_UNIT_SCALES.get(current.group("unit").lower())
            if previous_scale is None or current_scale is None or previous_scale == current_scale:
                continue
            connector = re.sub(r"[\s*`_'\"“”‘’]+", "", line[previous.end() : current.start()]).lower()
            if connector and re.fullmatch(
                r"[，,；;:]?[（(\[【]?(?:约(?:为)?|折合(?:为)?|换算(?:为|成)?|相当于|即|≈|~|=|＝|→|->)?",
                connector,
            ):
                return True
    return False


def _reply_has_derived_financial_metric(reply: str) -> bool:
    text = _calculation_claim_text(reply)
    lowered = text.lower()
    if _has_contextual_yoy_change(text) or _has_contextual_ratio(text) or _has_contextual_amount_normalization(text):
        return True
    if _has_contextual_per_capita(text):
        return True
    if any(term.lower() in lowered for term in DERIVED_PER_SHARE_TERMS):
        return True
    # A bare "每股" request is ambiguous and remains conservative, but a
    # direct reported field such as 基本每股收益/EPS should pass with its
    # structured evidence citation and need no synthetic calculator trace.
    if "每股" in text and not any(term.lower() in lowered for term in DIRECT_REPORTED_PER_SHARE_TERMS):
        return True
    return any(
        term.lower() in lowered
        for term in DERIVED_FINANCIAL_TERMS
        if term not in PER_CAPITA_CALCULATION_TERMS
    )


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


def _required_calculator_operations(
    message: str,
    reply: str,
    *,
    trusted_evidence: Sequence[Mapping[str, Any]] = (),
) -> frozenset[str]:
    message_text = _calculation_claim_text(message)
    reply_text = _calculation_claim_text(reply)
    text = f"{message_text}\n{reply_text}".lower()
    operations: set[str] = set()
    if _has_contextual_amount_normalization(text) or has_evidence_bound_unit_normalization(reply, trusted_evidence):
        operations.add("normalize_amount")
    if "cagr" in text or "复合增长率" in text:
        operations.add("cagr")
    if any(term in text for term in YOY_FINANCIAL_TERMS) or _has_contextual_yoy_change(text):
        operations.update({"yoy", "yoy_growth"})
    if _has_per_capita_term(message_text) or _has_contextual_per_capita(reply_text):
        operations.add("per_capita")
    ratio_terms = ("占比", "比例", "百分比", "覆盖率", "毛利率", "净利率", "资产负债率", "收益率", "回报率", "净息差")
    # A ratio explicitly requested by the user is mandatory. In the answer,
    # require it only for an actual contextual percentage claim; plain prose
    # such as "关注毛利率和折现率假设" is not itself a calculation.
    if any(term in message_text for term in ratio_terms) or _has_contextual_ratio(reply_text):
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


def is_external_tool_loop_guard_reply(reply: str) -> bool:
    """Return whether Hermes exposed an internal tool-loop stop as its answer."""
    lowered = (reply or "").lower()
    return any(marker in lowered for marker in EXTERNAL_TOOL_LOOP_GUARD_MARKERS)


def recover_financial_tool_loop_reply(
    message: str,
    context: Any | None,
    reply: str,
    *,
    deps: FinancialEvidenceContractDependencies,
) -> str | None:
    """Replace an internal tool-loop stop with deterministic evidence or a clean status."""
    if not is_external_tool_loop_guard_reply(reply):
        return None

    fallback = build_financial_evidence_fallback_reply(message, context, deps=deps)
    if fallback:
        return (
            "## 运行状态\n"
            "- 本轮财务工具调用参数连续失败，系统已停止重复执行；这不是已检索财务证据被拒绝。\n"
            "- 系统未采用失败工具输出，以下改用后端已验证的原始事实收束；"
            "未完成校验的派生分析不予输出。\n\n"
            f"{fallback}"
        )
    return (
        "## 运行状态\n"
        "- 本轮工具调用参数连续失败，系统已停止重复执行。\n"
        "- 当前没有足够的已验证证据用于确定性收束，因此未输出未经校验的财务结论。"
    )


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


def build_invalid_calculation_trace_guardrail_reply(
    reason: str,
    failures: Sequence[Mapping[str, Any]] = (),
    deterministic_pack: str | None = None,
) -> str:
    detail_lines = []
    for index, failure in enumerate(failures[:5], start=1):
        fields = " ".join(
            f"{key}={failure[key]}"
            for key in (
                "line_number",
                "operation",
                "metric",
                "claimed_value",
                "claimed_unit",
                "expected_results",
                "expected_normalized_value",
                "expected_normalized_values",
                "evidence_id",
                "evidence_value",
                "evidence_unit",
            )
            if failure.get(key) not in (None, "", [])
        )
        detail_lines.append(f"- failure_{index}: {fields or 'detail_unavailable'}")
    details = ("\n" + "\n".join(detail_lines)) if detail_lines else ""
    pack = f"\n\n{deterministic_pack}" if deterministic_pack else ""
    return (
        "## 计算校验无效\n"
        "- 后端检测到计算/勾稽 trace 不是完整结构化运行记录，或其 operation、metric、period、inputs、result、"
        "ResearchIdentity、证据绑定或确定性重算不一致。\n"
        "- 工具名、章节标题和手写 operation/result 文本不构成可信 trace；原始模型回答已被阻断。"
        f"{details}\n\n"
        "guardrail_status=blocked\n"
        f"guardrail_reason={FINANCIAL_CALCULATION_TRACE_MISSING_GUARDRAIL_REASON}\n"
        f"calculation_trace_reason={reason}"
        f"{pack}"
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


_CALCULATION_OPERATION_LABELS = {
    "normalize_amount": "金额单位换算",
    "yoy": "同比变动",
    "yoy_growth": "同比增长",
    "ratio": "比例计算",
    "cagr": "复合增长率",
    "per_capita": "人均计算",
}


def _display_trace_input(item: Any) -> str:
    if not isinstance(item, Mapping):
        return "?"
    value = str(item.get("value") or "?").strip()
    unit = str(item.get("unit") or "").strip()
    return f"{value} {unit}".strip()


def _display_trace_metric(item: Any, fallback: str) -> str:
    if not isinstance(item, Mapping):
        return fallback
    return str(item.get("metric") or fallback).strip()


def _display_percent(result: Any) -> str:
    if not isinstance(result, Mapping) or result.get("percent") in (None, ""):
        return "?"
    try:
        return f"{float(result['percent']):.2f}%"
    except (TypeError, ValueError):
        return f"{result['percent']}%"


def _calculation_run_summary(run: Mapping[str, Any]) -> str:
    operation = str(run.get("operation") or "").strip().lower()
    inputs = run.get("inputs") if isinstance(run.get("inputs"), Mapping) else {}
    result = run.get("result") if isinstance(run.get("result"), Mapping) else {}
    metric = str(run.get("metric") or _CALCULATION_OPERATION_LABELS.get(operation, "派生计算"))
    if operation in {"yoy", "yoy_growth"}:
        current = inputs.get("current")
        previous = inputs.get("previous")
        return (
            f"✅ {metric}：({_display_trace_input(current)} − {_display_trace_input(previous)}) "
            f"÷ |{_display_trace_input(previous)}| = {_display_percent(result)}"
        )
    if operation == "ratio":
        numerator = inputs.get("numerator")
        denominator = inputs.get("denominator")
        label = (
            f"{_display_trace_metric(numerator, '分子')} / "
            f"{_display_trace_metric(denominator, '分母')}"
        )
        return (
            f"✅ {label}：{_display_trace_input(numerator)} ÷ "
            f"{_display_trace_input(denominator)} = {_display_percent(result)}"
        )
    if operation == "normalize_amount":
        amount = inputs.get("amount")
        normalized = result.get("native_100m_value")
        normalized_text = f"{normalized} 亿元" if normalized not in (None, "") else "换算结果已通过"
        return f"✅ {_display_trace_metric(amount, metric)}：{_display_trace_input(amount)} = {normalized_text}"
    if operation == "cagr":
        return (
            f"✅ {metric}：{_display_trace_input(inputs.get('start'))} → "
            f"{_display_trace_input(inputs.get('end'))} = {_display_percent(result)}"
        )
    if operation == "per_capita":
        value = result.get("native_per", result.get("value", "?"))
        return (
            f"✅ {metric}：{_display_trace_input(inputs.get('amount'))} ÷ "
            f"{_display_trace_input(inputs.get('count'))} = {value}"
        )
    return f"✅ {_CALCULATION_OPERATION_LABELS.get(operation, '派生计算')}：确定性重算通过"


def _trace_mapping_fingerprint(item: Any) -> tuple[tuple[str, str], ...] | str:
    if not isinstance(item, Mapping):
        return repr(item)
    return tuple((str(key), str(value)) for key, value in sorted(item.items()))


def _calculation_run_display_key(run: Mapping[str, Any]) -> tuple[Any, ...]:
    inputs = run.get("inputs") if isinstance(run.get("inputs"), Mapping) else {}
    result = run.get("result") if isinstance(run.get("result"), Mapping) else {}
    input_key = tuple(
        (str(name), _trace_mapping_fingerprint(value))
        for name, value in sorted(inputs.items())
    )
    return (
        str(run.get("operation") or "").strip().lower(),
        str(run.get("metric") or "").strip(),
        str(run.get("period") or "").strip(),
        input_key,
        _trace_mapping_fingerprint(result),
    )


def _dedupe_calculator_runs_for_display(
    runs: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    deduped: list[Mapping[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for run in runs:
        key = _calculation_run_display_key(run)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(run)
    return deduped


def _reconciliation_run_summary(run: Mapping[str, Any]) -> str:
    inputs = run.get("inputs") if isinstance(run.get("inputs"), Mapping) else {}
    result = run.get("result") if isinstance(run.get("result"), Mapping) else {}
    gross = inputs.get("gross")
    allowance = inputs.get("allowance")
    net = inputs.get("net")
    expected = result.get("net", _display_trace_input(net))
    return (
        f"✅ {_display_trace_input(gross)} − {_display_trace_input(allowance)} = "
        f"{expected}；与 {_display_trace_metric(net, '主表净额')} 一致"
    )


_VALIDATION_REASON_LABELS = {
    "calculator_trace_missing": "缺少可验证的计算运行记录",
    "reconciliation_trace_missing": "缺少可验证的勾稽运行记录",
    "trace_unstructured": "计算记录无法绑定为结构化证据链",
    "trace_operation_missing": "部分派生指标缺少对应计算操作",
    "trace_claim_result_mismatch": "正文值与确定性重算结果不一致",
    "trace_evidence_mismatch": "计算输入与引用证据不一致",
    "trace_result_mismatch": "运行结果与后端重算不一致",
    "trace_identity_company_id_mismatch": "计算记录绑定了错误的公司身份",
    "trace_identity_market_mismatch": "计算记录绑定了错误的市场身份",
    "trace_identity_filing_id_mismatch": "计算记录绑定了错误的报告身份",
    "trace_identity_parse_run_id_mismatch": "计算记录绑定了错误的解析任务",
    "trace_unknown_operation": "计算操作类型无法识别",
    "trace_input_fields_missing": "计算输入字段不完整",
    "trace_input_value_mismatch": "计算输入值与证据不一致",
    "trace_input_currency_mismatch": "计算输入币种与证据不一致",
    "trace_input_metric_mismatch": "计算输入指标口径不匹配",
    "trace_reconciliation_status_invalid": "勾稽运行状态未通过",
}


def _failure_summary(reason: str, failure: Mapping[str, Any] | None = None) -> str:
    failure = failure or {}
    label = _VALIDATION_REASON_LABELS.get(reason, "该项暂时无法完成自动验证")
    details = []
    if failure.get("metric"):
        details.append(f"指标：{failure['metric']}")
    if failure.get("claimed_value") not in (None, ""):
        details.append(f"正文值：{failure['claimed_value']}{failure.get('claimed_unit') or ''}")
    expected = failure.get("expected_results") or failure.get("expected_normalized_values")
    if expected:
        details.append(f"后端重算：{expected}")
    elif failure.get("expected_normalized_value") not in (None, ""):
        details.append(f"后端重算：{failure['expected_normalized_value']}")
    elif failure.get("evidence_value") not in (None, ""):
        details.append(
            f"证据值：{failure['evidence_value']}{failure.get('evidence_unit') or ''}"
        )
    suffix = f"；{'；'.join(details)}" if details else ""
    return f"⚠️ {label}{suffix}。建议核对对应公式、输入口径和引用来源。"


def append_financial_validation_report(
    reply: str,
    *,
    runs: Sequence[Mapping[str, Any]],
    allowed: bool,
    reason: str = "",
    failures: Sequence[Mapping[str, Any]] = (),
) -> str:
    """Append complete readable validation cards without changing the answer body."""

    calculator_runs = _dedupe_calculator_runs_for_display(
        [run for run in runs if str(run.get("tool") or "") == "financial_calculator.py"]
    )
    reconciliation_runs = [
        run for run in runs if str(run.get("tool") or "") == "financial_reconciliation_validator.py"
    ]
    sections: list[str] = []
    if calculator_runs or (not allowed and reason != "reconciliation_trace_missing"):
        lines = [_calculation_run_summary(run) for run in calculator_runs]
        if not allowed:
            lines.extend(_failure_summary(reason, failure) for failure in (failures or ({},)))
        status = "全部通过" if allowed else "存在待核对项"
        summary = (
            f"- 状态：{len(calculator_runs)}/{len(calculator_runs)} 项通过。"
            if allowed
            else f"- 状态：{len(calculator_runs)} 项运行记录已检测，至少 1 项待核对。"
        )
        sections.append(
            f"## 计算器校验（{status}）\n{summary}\n"
            + "\n".join(f"- {line}" for line in lines)
        )
    if reconciliation_runs or reason == "reconciliation_trace_missing":
        lines = [_reconciliation_run_summary(run) for run in reconciliation_runs]
        if not allowed:
            lines.append(_failure_summary(reason, failures[0] if failures else None))
        status = "全部通过" if allowed else "存在待核对项"
        summary = (
            f"- 状态：{len(reconciliation_runs)}/{len(reconciliation_runs)} 项通过。"
            if allowed
            else f"- 状态：{len(reconciliation_runs)} 项运行记录已检测，至少 1 项待核对。"
        )
        sections.append(
            f"## 勾稽校验（{status}）\n{summary}\n"
            + "\n".join(f"- {line}" for line in lines)
        )
    if not sections:
        return reply
    cleaned_reply = re.sub(
        r"(?ms)^## (?:计算器校验|勾稽校验)(?:[（(][^\n）)]*[）)])?\s*\n"
        r".*?(?=^## |\Z)",
        "",
        reply or "",
    )
    cleaned_reply = re.sub(r"\n{3,}", "\n\n", cleaned_reply).strip()
    return _insert_before_reference_section(cleaned_reply, "\n\n".join(sections))


def _insert_before_reference_section(reply: str, section: str) -> str:
    reference_heading = re.search(r"(?m)^## 引用来源[:：]?\s*$", reply or "")
    if not reference_heading:
        return f"{reply.rstrip()}\n\n{section.strip()}".strip()
    before = reply[: reference_heading.start()].rstrip()
    after = reply[reference_heading.start() :].lstrip()
    return f"{before}\n\n{section.strip()}\n\n{after}".strip()


def append_successful_calculation_validation_summary(
    reply: str,
    runs: Sequence[Mapping[str, Any]],
) -> str:
    """Expose a compact success summary without leaking machine trace fields."""

    return append_financial_validation_report(reply, runs=runs, allowed=True)


def enforce_financial_evidence_contract(
    message: str,
    context: Any | None,
    reply: str,
    *,
    deps: FinancialEvidenceContractDependencies,
    trusted_calculation_runs: Sequence[Mapping[str, Any]] = (),
    trusted_calculation_evidence: Sequence[Mapping[str, Any]] = (),
    deterministic_calculation_pack: str | None = None,
    strict_validation: bool = False,
) -> str:
    """Do not let financial fact answers enter history without structured evidence."""
    if deps.is_runtime_status_reply(reply):
        return reply
    # Backend diagnostics may be copied by the model from a prior assistant
    # turn. They are not model evidence or calculation traces for this turn.
    reply = strip_guardrail_diagnostics(reply)
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
    needs_calculator_trace = (
        _reply_has_derived_financial_metric(message)
        or _reply_has_derived_financial_metric(reply)
        or has_evidence_bound_unit_normalization(reply, trusted_calculation_evidence)
    )
    needs_reconciliation_trace = _reply_has_reconciliation_metric(message) or _reply_has_reconciliation_metric(reply)
    calculation_trace = validate_calculation_traces(
        reply,
        expected_identity=agent_runtime_context.research_identity(context),
        require_calculator=needs_calculator_trace,
        require_reconciliation=needs_reconciliation_trace,
        expected_operations=_required_calculator_operations(
            message,
            reply,
            trusted_evidence=trusted_calculation_evidence,
        ),
        trusted_runs=trusted_calculation_runs,
        trusted_evidence=trusted_calculation_evidence,
    )
    if calculation_trace.checked and not calculation_trace.allowed:
        reason = calculation_trace.reason
        visible_trace = _reply_has_calculator_trace(reply) or _reply_has_reconciliation_trace(reply)
        if reason in {"calculator_trace_missing", "reconciliation_trace_missing", "trace_unstructured"} and not visible_trace:
            reason = "reconciliation_trace_missing" if needs_reconciliation_trace else "calculator_trace_missing"
        if strict_validation:
            if reason in {"calculator_trace_missing", "reconciliation_trace_missing"} and not visible_trace:
                diagnostic = build_missing_calculation_trace_guardrail_reply(message, reply, reason)
            else:
                diagnostic = build_invalid_calculation_trace_guardrail_reply(
                    reason,
                    calculation_trace.failures,
                    deterministic_calculation_pack,
                )
            if financial_guardrail_mode() == "warn" and _may_observe_calculation_failure(
                calculation_trace.reason
            ):
                return _observation_reply(reply, diagnostic)
            return append_financial_validation_report(
                diagnostic,
                runs=calculation_trace.runs,
                allowed=False,
                reason=reason,
                failures=calculation_trace.failures,
            )
        return append_financial_validation_report(
            reply,
            runs=calculation_trace.runs,
            allowed=False,
            reason=reason,
            failures=calculation_trace.failures,
        )
    # A successful trusted receipt or backend evidence recomputation is the
    # calculation validation. Do not append the legacy text-only warning just
    # because the model omitted a tool heading from its visible summary.
    if not calculation_trace.checked:
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
            trusted_evidence=trusted_calculation_evidence,
            validated_calculation_lines=frozenset(
                int(run.get("display_line_number") or 0)
                for run in calculation_trace.runs
                if str(run.get("trace_origin") or "") == "backend_evidence_recompute"
                and int(run.get("display_line_number") or 0) > 0
            ),
        )
        if not claim_verification.allowed:
            failures = tuple(
                {
                    "metric": violation.metric,
                    "claimed_value": violation.claimed_value,
                    "claimed_unit": violation.claimed_unit,
                    "evidence_value": violation.evidence_value,
                    "evidence_unit": violation.evidence_unit,
                }
                for violation in claim_verification.violations[:5]
            )
            if strict_validation:
                # A deterministic evidence mismatch is known-bad financial data,
                # so the user-facing runtime must not deliver or persist it.
                return append_financial_validation_report(
                    build_financial_claim_mismatch_guardrail_reply(claim_verification),
                    runs=calculation_trace.runs,
                    allowed=False,
                    reason="trace_evidence_mismatch",
                    failures=failures,
                )
            return append_financial_validation_report(
                reply,
                runs=calculation_trace.runs,
                allowed=False,
                reason="trace_evidence_mismatch",
                failures=failures,
            )
        return append_successful_calculation_validation_summary(reply, calculation_trace.runs)
    fallback = build_financial_evidence_fallback_reply(message, context, deps=deps)
    diagnostic = fallback or build_missing_financial_evidence_guardrail_reply(message, context)
    if financial_guardrail_mode() == "warn":
        return _observation_reply(reply, diagnostic)
    return diagnostic
