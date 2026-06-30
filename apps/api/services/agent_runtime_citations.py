"""Citation and display-normalization facade for the Hermes agent runtime."""

from __future__ import annotations

from .agent_chat_runtime_impl import (
    build_financial_evidence_fallback_reply,
    build_invalid_task_id_evidence_reply,
    build_primary_data_evidence_supplement,
    normalize_evidence_trace_for_display,
    normalize_plain_inline_latex,
)

__all__ = [
    "build_financial_evidence_fallback_reply",
    "build_invalid_task_id_evidence_reply",
    "build_primary_data_evidence_supplement",
    "normalize_evidence_trace_for_display",
    "normalize_plain_inline_latex",
]
