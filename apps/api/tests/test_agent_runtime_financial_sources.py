import re
from typing import Any

from services import agent_runtime_financial_sources as sources


def _source_field_value(line: str, field: str) -> str:
    match = re.search(rf"\b{re.escape(field)}=([^,\n，]+)", line)
    return match.group(1).strip() if match else ""


def _deps(**overrides):
    values = {
        "extract_reference_lines": lambda reply: [line for line in str(reply).splitlines() if "source_type=" in line],
        "source_field_value": _source_field_value,
        "is_statement_query": lambda message: "statement" in message,
        "should_inject_note_detail_context": lambda message: "note" in message,
        "has_structured_evidence_trace": lambda reply: "task_id=real-task" in reply,
        "is_runtime_status_reply": lambda reply: str(reply).startswith("系统状态"),
        "reply_has_requested_metric_evidence": lambda _message, reply: "metric=收入" in reply,
        "merge_primary_data_refs_into_citations": lambda reply, supplement=None: (
            f"{reply}\nSUPPLEMENT::{supplement}" if supplement else f"MERGED::{reply}"
        ),
        "human_efficiency_result": lambda _message, _context: None,
        "render_human_efficiency_evidence_markdown": lambda result: f"human-efficiency::{result['kind']}",
        "human_capital_result": lambda _message, _context: None,
        "render_human_capital_primary_data_supplement": lambda result: f"human-capital::{result['kind']}",
        "statement_metric_result": lambda _message, _context: (None, None),
        "render_statement_table_primary_data_supplement": lambda result: f"statement::{result['kind']}" if result else None,
        "three_statement_core_result": lambda _message, _context: None,
        "render_three_statement_primary_data_supplement": lambda result: f"three-statement::{result['kind']}" if result else None,
        "note_detail_result": lambda _message, _context, **_kwargs: (None, None),
        "render_note_detail_primary_data_supplement": lambda result: f"note::{result['kind']}" if result else None,
        "wiki_fulltext_fallback_result": lambda _message, _context: None,
        "render_wiki_fulltext_primary_data_supplement": lambda result: f"fulltext::{result['kind']}" if result else None,
        "record_postgres_fallback_event": lambda context, **event: context.setdefault("_events", []).append(event),
        "audit_context_with_fallback_event": lambda context, **event: {"original": context, "_events": [event]},
        "postgres_fallback_result": lambda _message, _context: None,
        "render_postgres_primary_data_supplement": lambda result: f"postgres::{result['kind']}" if result else None,
    }
    values.update(overrides)
    return sources.PrimaryDataEvidenceDependencies(**values)


def test_reply_source_detection_uses_wiki_metrics_and_note_sources():
    deps = _deps()
    reply = """
[D1] source_type=wiki_metrics, file=metrics/reports/2025/three_statements.json
[D2] source_type=wiki_document_links, file=semantic/document_links.json
"""

    assert sources.reply_has_wiki_metrics_source(reply, deps=deps)
    assert sources.reply_has_wiki_note_source(reply, deps=deps)
    assert sources.reply_missing_required_wiki_source("statement query", reply, deps=deps) is False


def test_normalize_wiki_metric_file_name_only_for_wiki_sources():
    assert (
        sources.normalize_wiki_metric_file_name(
            "metrics/reports/2025-annual/three_statements.json",
            default_source_type="wiki_metrics",
        )
        == "metrics/three_statements.json"
    )
    assert (
        sources.normalize_wiki_metric_file_name(
            "reports/2025-annual/metrics/three_statements.json",
            default_source_type="wiki_metrics",
        )
        == "metrics/three_statements.json"
    )
    assert (
        sources.normalize_wiki_metric_file_name(
            "reports/2025-annual/metrics/three_statements.json",
            default_source_type="postgresql_agent_view",
        )
        == "reports/2025-annual/metrics/three_statements.json"
    )


def test_normalize_wiki_metric_file_refs_only_for_wiki_sources():
    markdown = (
        "[D1] source_type=wiki_metrics, file=metrics/reports/2025-annual/three_statements.json\n"
        "[D2] source_type=wiki_metrics, file=reports/2024-annual/metrics/three_statements.json"
    )

    assert sources.normalize_wiki_metric_file_refs(markdown, default_source_type="wiki_metrics") == (
        "[D1] source_type=wiki_metrics, file=metrics/three_statements.json\n"
        "[D2] source_type=wiki_metrics, file=metrics/three_statements.json"
    )
    assert sources.normalize_wiki_metric_file_refs(markdown, default_source_type="postgresql_agent_view") == markdown


def test_build_primary_data_evidence_supplement_prefers_human_efficiency_first():
    calls: list[str] = []

    def human_efficiency_result(_message: str, _context: Any):
        calls.append("human_efficiency")
        return {"kind": "efficiency"}

    def human_capital_result(_message: str, _context: Any):
        calls.append("human_capital")
        return {"kind": "capital"}

    result = sources.build_primary_data_evidence_supplement(
        "human efficiency",
        {},
        deps=_deps(human_efficiency_result=human_efficiency_result, human_capital_result=human_capital_result),
    )

    assert result == "human-efficiency::efficiency"
    assert calls == ["human_efficiency"]


def test_build_primary_data_evidence_supplement_combines_statement_and_note_context():
    result = sources.build_primary_data_evidence_supplement(
        "statement with note",
        {},
        deps=_deps(
            statement_metric_result=lambda _message, _context: ({"kind": "detail"}, None),
            note_detail_result=lambda _message, _context, **_kwargs: ({"kind": "lease"}, None),
        ),
    )

    assert result == "statement::detail\n\nnote::lease"


def test_build_primary_data_evidence_supplement_records_postgres_fallback_after_wiki_miss():
    context: dict[str, Any] = {}

    result = sources.build_primary_data_evidence_supplement(
        "revenue",
        context,
        deps=_deps(
            postgres_fallback_result=lambda _message, _context: {"kind": "agent-view"},
        ),
    )

    assert result == "postgres::agent-view"
    assert context["_events"] == [
        {
            "reason": "wiki_fulltext_miss",
            "stage": "primary_data_postgres_fallback_attempt",
            "source": "wiki_first",
        }
    ]


def test_append_primary_data_evidence_skips_supplement_when_requested_metric_is_cited():
    calls: list[str] = []

    result = sources.append_primary_data_evidence_if_needed(
        "收入是多少",
        {},
        "[D1] source_type=wiki_metrics, metric=收入",
        deps=_deps(
            three_statement_core_result=lambda _message, _context: calls.append("build") or {"kind": "core"},
        ),
    )

    assert result == "MERGED::[D1] source_type=wiki_metrics, metric=收入"
    assert calls == []


def test_append_primary_data_evidence_adds_supplement_when_required_wiki_source_is_missing():
    result = sources.append_primary_data_evidence_if_needed(
        "statement query",
        {},
        "结论，没有 wiki metrics 引用",
        deps=_deps(statement_metric_result=lambda _message, _context: ({"kind": "detail"}, None)),
    )

    assert result == "MERGED::结论，没有 wiki metrics 引用\nSUPPLEMENT::statement::detail"


def test_append_primary_data_evidence_preserves_runtime_status_reply():
    result = sources.append_primary_data_evidence_if_needed(
        "statement query",
        {},
        "系统状态：处理中",
        deps=_deps(),
    )

    assert result == "系统状态：处理中"
