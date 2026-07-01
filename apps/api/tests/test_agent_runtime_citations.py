from services import agent_chat_runtime as runtime
from services import agent_runtime_citations as citations


def test_first_record_label_is_shared_with_runtime_wrapper():
    record = {"项目": "货币资金", "2025": "123"}

    assert citations._first_record_label(record) == "货币资金"
    assert runtime._first_record_label(record) == "货币资金"
    assert citations._first_record_label({}) == ""


def test_merge_primary_data_refs_moves_auto_evidence_refs_to_citation_section():
    reply = """结论正文。

## 主要数据引用来源
[D1] source_type=wiki_metrics, file=metrics/three_statements.json, metric=收入, period=2025, task_id=11111111-1111-1111-1111-111111111111, pdf_page=7, table_index=2, md_line=50
"""
    merged = citations._merge_primary_data_refs_into_citations(
        reply,
        auto_evidence_section_titles={"主要数据引用来源"},
    )

    assert "## 主要数据引用来源" not in merged
    assert "## 引用来源" in merged
    assert "metric=收入" in merged
    assert merged.count("task_id=11111111-1111-1111-1111-111111111111") == 1


def test_structured_evidence_requires_real_task_id_and_page_or_table():
    cited = (
        "[1] source_type=postgresql, task_id=11111111-1111-1111-1111-111111111111, "
        "pdf_page=7, table_index=2, md_line=50"
    )
    uncited = "[1] source_type=postgresql, task_id=fake, pdf_page=7, table_index=2"

    assert citations._has_structured_evidence_trace(cited)
    assert runtime._has_structured_evidence_trace(cited)
    assert not citations._has_structured_evidence_trace(uncited)
