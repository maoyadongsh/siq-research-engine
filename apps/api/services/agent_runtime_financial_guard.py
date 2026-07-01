"""String guards for financial calculator and reconciliation traces."""

from __future__ import annotations

from pathlib import Path

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
