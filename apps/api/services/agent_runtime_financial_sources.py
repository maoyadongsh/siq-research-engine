"""Financial source orchestration helpers for agent runtime evidence supplements."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

GOODWILL_TERMS = ("商誉", "商譽", "goodwill", "のれん", "영업권")


def _is_goodwill_query(message: str) -> bool:
    normalized = str(message or "").casefold()
    return any(term.casefold() in normalized for term in GOODWILL_TERMS)


@dataclass(frozen=True)
class PrimaryDataEvidenceDependencies:
    extract_reference_lines: Callable[[str], list[str]]
    source_field_value: Callable[[str, str], str]
    is_statement_query: Callable[[str], bool]
    should_inject_note_detail_context: Callable[[str], bool]
    has_structured_evidence_trace: Callable[[str], bool]
    is_runtime_status_reply: Callable[[str], bool]
    reply_has_requested_metric_evidence: Callable[[str, str], bool]
    merge_primary_data_refs_into_citations: Callable[[str, str | None], str]
    human_efficiency_result: Callable[[str, Any | None], dict[str, Any] | None]
    render_human_efficiency_evidence_markdown: Callable[[dict[str, Any]], str]
    human_capital_result: Callable[[str, Any | None], dict[str, Any] | None]
    render_human_capital_primary_data_supplement: Callable[[dict[str, Any]], str | None]
    statement_metric_result: Callable[..., tuple[dict[str, Any] | None, Callable[..., str] | None]]
    render_statement_table_primary_data_supplement: Callable[[dict[str, Any]], str | None]
    three_statement_core_result: Callable[[str, Any | None], dict[str, Any] | None]
    render_three_statement_primary_data_supplement: Callable[[dict[str, Any]], str | None]
    note_detail_result: Callable[..., tuple[dict[str, Any] | None, Callable[..., str] | None]]
    render_note_detail_primary_data_supplement: Callable[[dict[str, Any]], str | None]
    wiki_fulltext_fallback_result: Callable[[str, Any | None], dict[str, Any] | None]
    render_wiki_fulltext_primary_data_supplement: Callable[[dict[str, Any]], str | None]
    record_postgres_fallback_event: Callable[..., None]
    audit_context_with_fallback_event: Callable[..., Any]
    postgres_fallback_result: Callable[[str, Any | None], dict[str, Any] | None]
    render_postgres_primary_data_supplement: Callable[[dict[str, Any]], str | None]


def normalize_wiki_metric_file_name(file_name: str, *, default_source_type: str) -> str:
    if not str(default_source_type or "").startswith("wiki_"):
        return file_name
    if re.fullmatch(r"(?:metrics/reports/[^/]+|reports/[^/]+/metrics)/three_statements\.json", file_name):
        return "metrics/three_statements.json"
    return file_name


def normalize_wiki_metric_file_refs(markdown: str, *, default_source_type: str) -> str:
    if not str(default_source_type or "").startswith("wiki_"):
        return markdown
    return re.sub(
        r"file=(?:metrics/reports/[^,\s]+|reports/[^,\s]+/metrics)/three_statements\.json",
        "file=metrics/three_statements.json",
        markdown,
    )


def reply_has_wiki_metrics_source(reply: str, *, deps: PrimaryDataEvidenceDependencies) -> bool:
    for line in deps.extract_reference_lines(reply):
        source_type = deps.source_field_value(line, "source_type")
        file_name = deps.source_field_value(line, "file")
        if source_type.endswith("_metrics") and "three_statements.json" in file_name:
            return True
    return False


def reply_has_wiki_note_source(reply: str, *, deps: PrimaryDataEvidenceDependencies) -> bool:
    for line in deps.extract_reference_lines(reply):
        source_type = deps.source_field_value(line, "source_type")
        file_name = deps.source_field_value(line, "file")
        if source_type.endswith("_document_links") or source_type.endswith("_note_links"):
            return True
        if file_name in {"semantic/document_links.json", "semantic/note_links.json"}:
            return True
    return False


def reply_missing_required_wiki_source(
    message: str,
    reply: str,
    *,
    deps: PrimaryDataEvidenceDependencies,
) -> bool:
    has_note_detail_intent = deps.should_inject_note_detail_context(message)
    if deps.is_statement_query(message) and not reply_has_wiki_metrics_source(reply, deps=deps):
        return True
    if (
        has_note_detail_intent
        and not reply_has_wiki_note_source(reply, deps=deps)
        and not deps.has_structured_evidence_trace(reply)
    ):
        return True
    return False


def build_primary_data_evidence_supplement(
    message: str,
    context: Any | None = None,
    *,
    deps: PrimaryDataEvidenceDependencies,
) -> str | None:
    human_efficiency = deps.human_efficiency_result(message, context)
    if human_efficiency:
        return deps.render_human_efficiency_evidence_markdown(human_efficiency)

    human_capital = deps.human_capital_result(message, context)
    if human_capital:
        return deps.render_human_capital_primary_data_supplement(human_capital)

    if _is_goodwill_query(message) or deps.is_statement_query(message):
        supplements: list[str] = []
        # Keep evidence order stable for every statement-oriented question:
        # three-statement snapshot, metric-specific body table, then notes.
        statement_result = deps.three_statement_core_result(message, context)
        statement_supplement = deps.render_three_statement_primary_data_supplement(statement_result or {})
        if statement_supplement:
            supplements.append(statement_supplement)

        detailed_statement_result, _renderer = deps.statement_metric_result(message, context)
        statement_table_supplement = deps.render_statement_table_primary_data_supplement(
            detailed_statement_result or {}
        )
        if statement_table_supplement:
            supplements.append(statement_table_supplement)

        note_result, _note_renderer = deps.note_detail_result(message, context, limit=8)
        note_supplement = deps.render_note_detail_primary_data_supplement(note_result or {})
        if note_supplement:
            supplements.append(note_supplement)
        if supplements:
            return "\n\n".join(supplements)

        fulltext = deps.wiki_fulltext_fallback_result(message, context)
        fulltext_supplement = deps.render_wiki_fulltext_primary_data_supplement(fulltext or {})
        if fulltext_supplement:
            return fulltext_supplement
        return None

    detailed_statement_result, _renderer = deps.statement_metric_result(message, context)
    statement_table_supplement = deps.render_statement_table_primary_data_supplement(detailed_statement_result or {})
    if statement_table_supplement:
        if deps.should_inject_note_detail_context(message):
            note_result, _note_renderer = deps.note_detail_result(message, context, limit=8)
            note_supplement = deps.render_note_detail_primary_data_supplement(note_result or {})
            if note_supplement:
                return f"{statement_table_supplement}\n\n{note_supplement}"
        return statement_table_supplement

    statement_result = deps.three_statement_core_result(message, context)
    statement_supplement = deps.render_three_statement_primary_data_supplement(statement_result or {})
    if statement_supplement:
        return statement_supplement

    note_result, _note_renderer = deps.note_detail_result(message, context, limit=8)
    note_supplement = deps.render_note_detail_primary_data_supplement(note_result or {})
    if note_supplement:
        return note_supplement

    fulltext = deps.wiki_fulltext_fallback_result(message, context)
    fulltext_supplement = deps.render_wiki_fulltext_primary_data_supplement(fulltext or {})
    if fulltext_supplement:
        return fulltext_supplement

    if isinstance(context, dict):
        postgres_context = context
        deps.record_postgres_fallback_event(
            postgres_context,
            reason="wiki_fulltext_miss",
            stage="primary_data_postgres_fallback_attempt",
            source="wiki_first",
        )
    else:
        postgres_context = deps.audit_context_with_fallback_event(
            context,
            reason="wiki_fulltext_miss",
            stage="primary_data_postgres_fallback_attempt",
            source="wiki_first",
        )
    postgres = deps.postgres_fallback_result(message, postgres_context)
    postgres_supplement = deps.render_postgres_primary_data_supplement(postgres or {})
    if postgres_supplement:
        return postgres_supplement
    return None


def append_primary_data_evidence_if_needed(
    message: str,
    context: Any | None,
    reply: str,
    *,
    deps: PrimaryDataEvidenceDependencies,
) -> str:
    if deps.is_runtime_status_reply(reply):
        return reply
    reply = deps.merge_primary_data_refs_into_citations(reply, None)
    # Goodwill is a reconciliation query: note citations alone are not enough.
    # Always materialize the main-statement -> body-table -> note chain before
    # accepting an otherwise matching metric citation.
    if _is_goodwill_query(message):
        supplement = build_primary_data_evidence_supplement(message, context, deps=deps)
        if supplement:
            return deps.merge_primary_data_refs_into_citations(reply, supplement)
    if reply_missing_required_wiki_source(message, reply, deps=deps):
        supplement = build_primary_data_evidence_supplement(message, context, deps=deps)
        if supplement:
            return deps.merge_primary_data_refs_into_citations(reply, supplement)
    if deps.reply_has_requested_metric_evidence(message, reply):
        return reply
    supplement = build_primary_data_evidence_supplement(message, context, deps=deps)
    if not supplement:
        return reply
    return deps.merge_primary_data_refs_into_citations(reply, supplement)


__all__ = [
    "PrimaryDataEvidenceDependencies",
    "append_primary_data_evidence_if_needed",
    "build_primary_data_evidence_supplement",
    "normalize_wiki_metric_file_name",
    "normalize_wiki_metric_file_refs",
    "reply_has_wiki_metrics_source",
    "reply_has_wiki_note_source",
    "reply_missing_required_wiki_source",
]
