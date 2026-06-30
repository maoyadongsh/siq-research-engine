"""Deterministic context/tool facade for the Hermes agent runtime."""

from __future__ import annotations

from .agent_chat_runtime_impl import (
    build_company_wiki_scope_context,
    build_direct_human_capital_reply,
    build_direct_note_detail_reply,
    build_direct_statement_metric_reply,
    build_hermes_run_input,
    build_human_capital_context,
    build_human_efficiency_evidence_context,
    build_note_detail_context,
    build_pdf2md_parse_only_context,
    build_postgres_fallback_context,
    build_session_contextual_input,
    build_statement_metric_context,
    build_three_statement_core_context,
    build_wiki_catalog_reply,
    build_wiki_fulltext_fallback_context,
    collect_chat_reply,
    get_session_default_context,
)

__all__ = [
    "build_company_wiki_scope_context",
    "build_direct_human_capital_reply",
    "build_direct_note_detail_reply",
    "build_direct_statement_metric_reply",
    "build_hermes_run_input",
    "build_human_capital_context",
    "build_human_efficiency_evidence_context",
    "build_note_detail_context",
    "build_pdf2md_parse_only_context",
    "build_postgres_fallback_context",
    "build_session_contextual_input",
    "build_statement_metric_context",
    "build_three_statement_core_context",
    "build_wiki_catalog_reply",
    "build_wiki_fulltext_fallback_context",
    "collect_chat_reply",
    "get_session_default_context",
]
