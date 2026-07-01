from services import agent_chat_runtime as runtime
from services import agent_runtime_citations as citations


def test_first_record_label_is_shared_with_runtime_wrapper():
    record = {"项目": "货币资金", "2025": "123"}

    assert citations._first_record_label(record) == "货币资金"
    assert runtime._first_record_label(record) == "货币资金"
    assert citations._first_record_label({}) == ""


def test_record_preview_and_statement_value_helpers_handle_empty_values():
    assert citations._record_values_preview({"项目": "收入", "2025": "100", "2024": "", "2023": "80"}) == "100 / 80"
    assert citations._record_values_preview({"项目": "收入"}) == "未返回"
    assert citations._format_statement_value({"raw_value": "", "normalized_value": 123, "unit": "万元"}) == "123 万元"
    assert citations._format_statement_value({"raw_value": "1,234", "unit": ""}) == "1,234"


def test_render_human_capital_primary_data_supplement_limits_rows_and_adds_refs():
    calls = []

    def table_source_links(task_id, pdf_page, table_index):
        calls.append((task_id, pdf_page, table_index))
        return f"/api/source/{task_id}/table/{table_index}"

    supplement = citations._render_human_capital_primary_data_supplement(
        {
            "report_id": "2025-annual",
            "task_id": "11111111-1111-1111-1111-111111111111",
            "pdf_page": 42,
            "table_index": 9,
            "md_line": 300,
            "sections": {
                "scale": [("员工总数", "1000")],
                "profession": [("研发人员", "300"), ("销售人员", "200")],
                "education": [("本科", "600")],
            },
        },
        primary_data_supplement_max_rows=3,
        table_source_links=table_source_links,
    )

    assert supplement is not None
    assert supplement.count("| 员工总数 | 1000 |") == 1
    assert supplement.count("| 研发人员 | 300 |") == 1
    assert supplement.count("| 销售人员 | 200 |") == 1
    assert "本科" not in supplement
    assert "## 主要数据引用来源" in supplement
    assert "[D1] source_type=wiki_report_table" in supplement
    assert "metric=员工情况/人才结构" in supplement
    assert calls == [
        ("11111111-1111-1111-1111-111111111111", 42, 9),
        ("11111111-1111-1111-1111-111111111111", 42, 9),
    ]


def test_render_human_capital_primary_data_supplement_returns_none_without_rows():
    assert citations._render_human_capital_primary_data_supplement(
        {"sections": {"scale": [], "profession": [], "education": []}},
        primary_data_supplement_max_rows=3,
        table_source_links=lambda task_id, pdf_page, table_index: "",
    ) is None


def test_render_statement_table_primary_data_supplement_limits_records_and_adds_refs():
    calls = []

    def table_source_links(task_id, pdf_page, table_index):
        calls.append((task_id, pdf_page, table_index))
        return f"/api/source/{task_id}/table/{table_index}"

    supplement = citations._render_statement_table_primary_data_supplement(
        {
            "report_id": "2025-annual",
            "tables": [
                {
                    "metric": "营业收入",
                    "unit": "万元",
                    "task_id": "11111111-1111-1111-1111-111111111111",
                    "pdf_page": 7,
                    "table_index": 2,
                    "md_line": 50,
                    "records": [
                        {"项目": "营业收入", "2025": "100", "2024": "90"},
                        {"项目": "净利润", "2025": "10", "2024": ""},
                    ],
                },
                {
                    "metric": "资产总计",
                    "unit": "万元",
                    "task_id": "22222222-2222-2222-2222-222222222222",
                    "pdf_page": 8,
                    "table_index": 3,
                    "md_line": 60,
                    "records": [{"项目": "资产总计", "2025": "500"}],
                },
            ],
        },
        primary_data_supplement_max_rows=2,
        table_source_links=table_source_links,
    )

    assert supplement is not None
    assert "| 营业收入 | 100 / 90 万元 |" in supplement
    assert "| 净利润 | 10 万元 |" in supplement
    assert "资产总计" not in supplement
    assert "## 主要数据引用来源" in supplement
    assert "[D1] source_type=wiki_metrics" in supplement
    assert "file=metrics/three_statements.json" in supplement
    assert "metric=营业收入" in supplement
    assert calls == [
        ("11111111-1111-1111-1111-111111111111", 7, 2),
        ("11111111-1111-1111-1111-111111111111", 7, 2),
        ("11111111-1111-1111-1111-111111111111", 7, 2),
    ]


def test_render_note_detail_primary_data_supplement_limits_tables_and_previews_records():
    calls = []

    def table_source_links(task_id, pdf_page, table_index):
        calls.append((task_id, pdf_page, table_index))
        return f"/api/source/{task_id}/table/{table_index}"

    supplement = citations._render_note_detail_primary_data_supplement(
        {
            "report_id": "2025-annual",
            "metric": "附注明细",
            "tables": [
                {
                    "metric": "商誉减值",
                    "unit": "万元",
                    "task_id": "33333333-3333-3333-3333-333333333333",
                    "pdf_page": 88,
                    "table_index": 12,
                    "md_line": 500,
                    "records": [
                        {"被投资单位": "A公司", "期末余额": "100", "占比": "10%"},
                        {"被投资单位": "B公司", "期末余额": "200", "占比": "20%"},
                        {"被投资单位": "C公司", "期末余额": "300", "占比": "30%"},
                        {"被投资单位": "D公司", "期末余额": "400", "占比": "40%"},
                    ],
                },
                {
                    "metric": "递延所得税",
                    "task_id": "44444444-4444-4444-4444-444444444444",
                    "pdf_page": 89,
                    "table_index": 13,
                    "md_line": 510,
                    "rows": [{"项目": "不应出现"}],
                },
            ],
        },
        primary_data_supplement_max_rows=1,
        table_source_links=table_source_links,
    )

    assert supplement is not None
    assert "| 商誉减值 | 单位=万元；解析行数=4；明细预览=A公司: 100 / 10%；B公司: 200 / 20%；C公司: 300 / 30% |" in supplement
    assert "D公司" not in supplement
    assert "递延所得税" not in supplement
    assert "## 主要数据引用来源" in supplement
    assert "[D1] source_type=wiki_document_links" in supplement
    assert "file=semantic/document_links.json" in supplement
    assert calls == [
        ("33333333-3333-3333-3333-333333333333", 88, 12),
        ("33333333-3333-3333-3333-333333333333", 88, 12),
    ]


def test_render_wiki_fulltext_primary_data_supplement_truncates_rows_and_defaults_refs():
    calls = []

    def table_source_links(task_id, pdf_page, table_index):
        calls.append((task_id, pdf_page, table_index))
        return f"/api/source/{task_id}/table/{table_index}"

    long_snippet = "营业收入\n  继续增长 " + ("同比提升 " * 40)
    supplement = citations._render_wiki_fulltext_primary_data_supplement(
        {
            "report_id": "2025-annual",
            "terms": ["营业收入", "净利润"],
            "rows": [
                {
                    "snippet": long_snippet,
                    "task_id": "55555555-5555-5555-5555-555555555555",
                    "pdf_page": 30,
                    "table_index": 0,
                    "md_line": 700,
                },
                {
                    "snippet": "不应出现",
                    "task_id": "66666666-6666-6666-6666-666666666666",
                    "pdf_page": 31,
                    "table_index": 1,
                    "md_line": 710,
                },
            ],
        },
        primary_data_supplement_max_rows=1,
        table_source_links=table_source_links,
    )

    assert supplement is not None
    assert "| F1 / 全文证据 | 营业收入 继续增长" in supplement
    assert "..." in supplement
    assert "不应出现" not in supplement
    assert "## 主要数据引用来源" in supplement
    assert "[D1] source_type=wiki_report_fulltext" in supplement
    assert "file=reports/2025-annual/report.md" in supplement
    assert "metric=营业收入,净利润" in supplement
    assert calls == [
        ("55555555-5555-5555-5555-555555555555", 30, 0),
        ("55555555-5555-5555-5555-555555555555", 30, 0),
    ]


def test_primary_data_supplement_renderers_return_none_for_empty_inputs():
    table_source_links = lambda task_id, pdf_page, table_index: ""

    assert citations._render_statement_table_primary_data_supplement(
        {"tables": []},
        primary_data_supplement_max_rows=3,
        table_source_links=table_source_links,
    ) is None
    assert citations._render_note_detail_primary_data_supplement(
        {"tables": []},
        primary_data_supplement_max_rows=3,
        table_source_links=table_source_links,
    ) is None
    assert citations._render_wiki_fulltext_primary_data_supplement(
        {"rows": []},
        primary_data_supplement_max_rows=3,
        table_source_links=table_source_links,
    ) is None


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


def test_normalize_plain_inline_latex_replaces_known_symbols_only():
    text = "A $\\to$ B，x $ \\leq $ y，保留 $\\unknown$ 和 $x+1$。"

    assert citations.normalize_plain_inline_latex(text) == "A → B，x ≤ y，保留 $\\unknown$ 和 $x+1$。"
    assert citations.normalize_plain_inline_latex(None) == ""


def test_normalize_evidence_trace_for_display_normalizes_latex_before_links(monkeypatch):
    calls = []

    def append_missing_pdf_source_links(content):
        calls.append(content)
        return f"linked::{content}"

    monkeypatch.setattr(citations, "append_missing_pdf_source_links", append_missing_pdf_source_links)

    assert citations.normalize_evidence_trace_for_display("A $\\to$ B") == "linked::A → B"
    assert citations.normalize_evidence_trace_for_display(None) == ""
    assert calls == ["A → B"]


def test_primary_data_evidence_trace_requires_marker_and_structured_locator():
    valid_reply = (
        "## 主要数据溯源补充\n"
        "[D1] source_type=wiki_metrics, task_id=11111111-1111-1111-1111-111111111111, "
        "pdf_page=7, table_index=2"
    )
    no_marker_reply = (
        "[D1] source_type=wiki_metrics, task_id=11111111-1111-1111-1111-111111111111, "
        "pdf_page=7, table_index=2"
    )
    fake_task_reply = (
        "## 主要数据溯源补充\n"
        "[D1] source_type=wiki_metrics, task_id=fake, pdf_page=7, table_index=2"
    )
    no_locator_reply = (
        "## 主要数据溯源补充\n"
        "[D1] source_type=wiki_metrics, task_id=11111111-1111-1111-1111-111111111111"
    )

    markers = ("主要数据溯源补充",)

    assert citations._has_primary_data_evidence_trace(valid_reply, markers=markers)
    assert not citations._has_primary_data_evidence_trace(no_marker_reply, markers=markers)
    assert not citations._has_primary_data_evidence_trace(fake_task_reply, markers=markers)
    assert not citations._has_primary_data_evidence_trace(no_locator_reply, markers=markers)


def test_source_locator_text_uses_defaults_and_appends_links():
    calls = []

    def table_source_links(task_id, pdf_page, table_index):
        calls.append((task_id, pdf_page, table_index))
        return "/api/source/task-1/table/3"

    locator = citations._source_locator_text(
        task_id="task-1",
        pdf_page=0,
        table_index=3,
        md_line="",
        table_source_links=table_source_links,
    )

    assert locator == "task_id=task-1, pdf_page=未返回, table_index=3, md_line=未返回，/api/source/task-1/table/3"
    assert calls == [("task-1", 0, 3)]


def test_primary_data_source_ref_uses_missing_defaults_without_link():
    calls = []

    def table_source_links(task_id, pdf_page, table_index):
        calls.append((task_id, pdf_page, table_index))
        return ""

    ref = citations._primary_data_source_ref(
        3,
        source_type="wiki_metrics",
        file="",
        metric="",
        period=None,
        task_id=None,
        pdf_page=None,
        table_index="",
        md_line=None,
        table_source_links=table_source_links,
    )

    assert (
        ref
        == "[D3] source_type=wiki_metrics, file=未返回, metric=未返回, period=未返回, "
        "task_id=未返回, pdf_page=未返回, table_index=未返回, md_line=未返回"
    )
    assert calls == [(None, None, "")]


def test_append_unique_source_ref_dedupes_by_locator_file_and_metric():
    refs = []
    seen = set()

    for metric in ("收入", "收入", "利润"):
        citations._append_unique_source_ref(
            refs,
            seen,
            source_type="wiki_metrics",
            file="metrics/three_statements.json",
            metric=metric,
            period="2025",
            task_id="task-1",
            pdf_page=7,
            table_index=2,
            md_line=50,
            table_source_links=lambda task_id, pdf_page, table_index: f"/api/source/{task_id}/table/{table_index}",
        )

    assert len(refs) == 2
    assert refs[0].startswith("[D1] source_type=wiki_metrics")
    assert "metric=收入" in refs[0]
    assert refs[1].startswith("[D2] source_type=wiki_metrics")
    assert "metric=利润" in refs[1]


def test_extract_reference_lines_filters_table_and_incomplete_rows():
    complete_ref = (
        "[D1] source_type=wiki_metrics, task_id=11111111-1111-1111-1111-111111111111, "
        "pdf_page=7, table_index=2"
    )
    table_ref = (
        "| [D2] source_type=wiki_metrics, task_id=22222222-2222-2222-2222-222222222222, "
        "pdf_page=8, table_index=3 |"
    )
    missing_page_ref = (
        "[D3] source_type=wiki_metrics, task_id=33333333-3333-3333-3333-333333333333, "
        "table_index=4"
    )
    alias_page_ref = (
        "[D4] source_type=wiki_metrics, task_id=44444444-4444-4444-4444-444444444444, "
        "pdf_page_number=9, table_index=5"
    )

    refs = citations._extract_reference_lines([complete_ref, table_ref, missing_page_ref, alias_page_ref])

    assert refs == [complete_ref, alias_page_ref]
    assert citations._is_reference_line(complete_ref)
    assert not citations._is_reference_line(table_ref)
    assert not citations._is_reference_line(missing_page_ref)


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


def test_strip_auto_evidence_sections_collects_refs_and_keeps_following_sections():
    markdown = """结论正文。

## 主要数据引用来源
说明文字。
[D1] source_type=wiki_metrics, file=metrics/three_statements.json, metric=收入, period=2025, task_id=11111111-1111-1111-1111-111111111111, pdf_page=7, table_index=2, md_line=50

## 风险提示
请复核。
"""

    body, refs = citations._strip_auto_evidence_sections(
        markdown,
        auto_evidence_section_titles={"主要数据引用来源"},
    )

    assert "主要数据引用来源" not in body
    assert "结论正文。" in body
    assert "## 风险提示" in body
    assert refs == [
        "[D1] source_type=wiki_metrics, file=metrics/three_statements.json, metric=收入, period=2025, task_id=11111111-1111-1111-1111-111111111111, pdf_page=7, table_index=2, md_line=50"
    ]


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


def test_reply_has_requested_metric_evidence_checks_requested_terms_in_reference_lines():
    reply = """正文提到了利润，但引用只给收入。

[D1] source_type=wiki_metrics, file=metrics/three_statements.json, metric=营业收入, period=2025, task_id=11111111-1111-1111-1111-111111111111, pdf_page=7, table_index=2, md_line=50
"""

    normalize = lambda value: "".join(str(value).lower().split())

    assert citations._reply_has_requested_metric_evidence(
        "收入是多少",
        reply,
        postgres_requested_metric_terms=lambda message: ["营业收入"],
        normalize_financial_text=normalize,
    )
    assert not citations._reply_has_requested_metric_evidence(
        "利润是多少",
        reply,
        postgres_requested_metric_terms=lambda message: ["净利润"],
        normalize_financial_text=normalize,
    )
    assert citations._reply_has_requested_metric_evidence(
        "随便分析",
        reply,
        postgres_requested_metric_terms=lambda message: [],
        normalize_financial_text=normalize,
    )
