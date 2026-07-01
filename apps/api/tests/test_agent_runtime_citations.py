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


def test_merge_primary_data_refs_adds_supplement_refs_to_existing_citation_section():
    reply = """结论正文。

## 引用来源
[D1] source_type=wiki_metrics, file=metrics/three_statements.json, metric=收入, period=2025, task_id=11111111-1111-1111-1111-111111111111, pdf_page=7, table_index=2, md_line=50

## 风险提示
请复核口径。
"""
    supplement = """## 主要数据引用来源
[D2] source_type=wiki_document_links, file=semantic/document_links.json, metric=商誉, period=2025, task_id=22222222-2222-2222-2222-222222222222, pdf_page=88, table_index=12, md_line=500
"""

    merged = citations._merge_primary_data_refs_into_citations(
        reply,
        supplement=supplement,
        auto_evidence_section_titles={"主要数据引用来源"},
    )

    citation_section, risk_section = merged.split("## 风险提示")
    assert "metric=收入" in citation_section
    assert "metric=商誉" in citation_section
    assert "metric=商誉" not in risk_section


def test_structured_evidence_requires_real_task_id_and_page_or_table():
    cited = (
        "[1] source_type=postgresql, task_id=11111111-1111-1111-1111-111111111111, "
        "pdf_page=7, table_index=2, md_line=50"
    )
    uncited = "[1] source_type=postgresql, task_id=fake, pdf_page=7, table_index=2"

    assert citations._has_structured_evidence_trace(cited)
    assert runtime._has_structured_evidence_trace(cited)
    assert not citations._has_structured_evidence_trace(uncited)


def test_source_reference_key_normalizes_alias_field_names():
    line_a = (
        "[P1] source_type=postgresql, task_id=11111111-1111-1111-1111-111111111111, "
        "pdf_page_number=7, table_index=2, markdown_line=50"
    )
    line_b = (
        "[P2] source_type=postgresql, task_id=11111111-1111-1111-1111-111111111111, "
        "pdf_page=7, table_index=2, md_line=50"
    )

    assert citations._source_field_value(line_a, "pdf_page") == ""
    assert citations._source_field_value(line_a, "pdf_page_number") == "7"
    assert citations._source_field_value(line_a, "md_line") == ""
    assert citations._source_field_value(line_a, "markdown_line") == "50"
    assert citations._source_reference_key(line_a) == citations._source_reference_key(line_b)


def test_merge_refs_into_reference_section_dedupes_alias_field_names():
    body = "结论正文。"
    refs = [
        (
            "[P1] source_type=postgresql, task_id=11111111-1111-1111-1111-111111111111, "
            "pdf_page_number=7, table_index=2, markdown_line=50"
        ),
        (
            "[P2] source_type=postgresql, task_id=11111111-1111-1111-1111-111111111111, "
            "pdf_page=7, table_index=2, md_line=50"
        ),
    ]

    merged = citations._merge_refs_into_reference_section(body, refs)

    assert "## 引用来源" in merged
    assert merged.count("source_type=postgresql") == 1


def test_merge_refs_into_reference_section_skips_refs_already_in_body():
    body = """结论正文。

## 引用来源
[D1] source_type=wiki_metrics, file=metrics/three_statements.json, metric=收入, period=2025, task_id=11111111-1111-1111-1111-111111111111, pdf_page=7, table_index=2, md_line=50
"""
    refs = [
        "[D1-copy] source_type=wiki_metrics, file=metrics/three_statements.json, metric=收入, period=2025, task_id=11111111-1111-1111-1111-111111111111, pdf_page=7, table_index=2, md_line=50",
        "[D2] source_type=wiki_metrics, file=metrics/three_statements.json, metric=利润, period=2025, task_id=22222222-2222-2222-2222-222222222222, pdf_page=8, table_index=3, md_line=60",
    ]

    merged = citations._merge_refs_into_reference_section(body, refs)

    assert "D1-copy" not in merged
    assert "metric=收入" in merged
    assert "metric=利润" in merged
    assert merged.count("source_type=wiki_metrics") == 2
