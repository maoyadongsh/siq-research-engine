from services import agent_chat_runtime as runtime, agent_runtime_citations as citations


def test_first_record_label_is_shared_with_runtime_wrapper():
    record = {"项目": "货币资金", "2025": "123"}

    assert citations._first_record_label(record) == "货币资金"
    assert runtime._first_record_label(record) == "货币资金"
    assert citations._first_record_label({}) == ""
    assert citations._first_record_label({"项目": "  货币资金  "}) == "货币资金"
    assert citations._first_record_label({"项目": "   ", "2025": "123"}) == ""


def test_record_preview_and_statement_value_helpers_handle_empty_values():
    assert citations._record_values_preview({"项目": "收入", "2025": "100", "2024": "", "2023": "80"}) == "100 / 80"
    assert citations._record_values_preview({"项目": "收入"}) == "未返回"
    assert citations._format_statement_value({"raw_value": "", "normalized_value": 123, "unit": "万元"}) == "123 万元"
    assert citations._format_statement_value({"raw_value": "1,234", "unit": ""}) == "1,234"


def test_statement_row_and_citation_keep_raw_value_unit_and_base_scale(tmp_path):
    company_dir = tmp_path / "000333-demo"
    metrics_file = company_dir / "metrics" / "three_statements.json"
    row = runtime._statement_record_to_row(
        {
            "statement_type": "balance_sheet",
            "metric_key": "goodwill",
            "metric_name": "商誉",
            "period": "2025-12-31",
            "raw_value": "34,256,859",
            "normalized_value": 342.56859,
            "unit_hint": "人民币千元",
            "base_scale": 1000,
            "source": {
                "task_id": "11111111-1111-1111-1111-111111111111",
                "pdf_page": 83,
                "table_index": 67,
            },
        },
        "2025-annual",
        metrics_file,
        company_dir,
    )

    supplement = citations._render_three_statement_primary_data_supplement(
        {"report_id": "2025-annual", "rows": [row]},
        primary_data_supplement_max_rows=1,
        table_source_links=lambda *_: "",
    )

    assert row["raw_value"] == "34,256,859"
    assert row["normalized_value"] == 342.56859
    assert row["unit"] == "人民币千元"
    assert row["scale"] == 1000
    assert row["base_scale"] == 1000
    assert supplement is not None
    assert "| 资产负债表 / 商誉 | 2025-12-31 | 34,256,859 人民币千元 |" in supplement
    assert 'value="34,256,859"' in supplement
    assert "value=342.56859" not in supplement
    assert "unit=人民币千元" in supplement
    assert "scale=1000" in supplement


def test_external_statement_citation_keeps_identity_value_and_regulatory_locator():
    supplement = citations._render_three_statement_primary_data_supplement(
        {
            "market": "US",
            "company_id": "US:0000320193",
            "report_id": "2025-10-K-0000320193-25-000079",
            "filing_id": "US:0000320193:0000320193-25-000079",
            "parse_run_id": "run-aapl",
            "rows": [
                {
                    "source_type": "wiki_metrics",
                    "statement_type": "income_statement",
                    "statement_label": "利润表",
                    "metric_key": "operating_revenue",
                    "metric_name": "Revenue",
                    "period": "2025-09-27",
                    "raw_value": "416161000000",
                    "unit": "USD",
                    "currency": "USD",
                    "evidence_id": "E-AAPL-REV",
                    "source_quote": "Revenue 416,161",
                    "evidence_source_type": "sec_xbrl_fact",
                    "source_url": "https://www.sec.gov/example.htm",
                    "source_anchor": "f-78",
                    "xbrl_tag": "us-gaap:Revenue",
                }
            ],
        },
        primary_data_supplement_max_rows=1,
        table_source_links=lambda *_: "",
    )
    assert supplement is not None
    assert "canonical_name=operating_revenue" in supplement
    assert "value=416161000000" in supplement
    assert "unit=USD" in supplement
    assert "market=US" in supplement
    assert "company_id=US:0000320193" in supplement
    assert "filing_id=US:0000320193:0000320193-25-000079" in supplement
    assert "parse_run_id=run-aapl" in supplement
    assert "evidence_id=E-AAPL-REV" in supplement
    assert "source_url=https://www.sec.gov/example.htm" in supplement
    assert "source_anchor=f-78" in supplement
    assert "xbrl_tag=us-gaap:Revenue" in supplement


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


def test_render_three_statement_primary_data_supplement_limits_rows_and_adds_refs():
    calls = []

    def table_source_links(task_id, pdf_page, table_index):
        calls.append((task_id, pdf_page, table_index))
        return f"/api/source/{task_id}/table/{table_index}"

    supplement = citations._render_three_statement_primary_data_supplement(
        {
            "market": "HK",
            "company_id": "HK:00700",
            "report_id": "2025-annual",
            "filing_id": "HK:00700:2025-annual:fixture",
            "parse_run_id": "HK:fixture",
            "rows": [
                {
                    "statement_label": "利润表",
                    "statement_type": "income_statement",
                    "metric_key": "operating_revenue",
                    "metric_name": "营业收入",
                    "period": "2025",
                    "raw_value": "1,000",
                    "unit": "万元",
                    "task_id": "11111111-1111-1111-1111-111111111111",
                    "pdf_page": 7,
                    "table_index": 2,
                    "md_line": 50,
                },
                {
                    "statement_label": "资产负债表",
                    "metric_name": "资产总计",
                    "period": "2025",
                    "raw_value": "5,000",
                    "unit": "万元",
                    "task_id": "22222222-2222-2222-2222-222222222222",
                    "pdf_page": 8,
                    "table_index": 3,
                    "md_line": 60,
                },
            ],
        },
        primary_data_supplement_max_rows=1,
        table_source_links=table_source_links,
    )

    assert supplement is not None
    assert "| 利润表 / 营业收入 | 2025 | 1,000 万元 |" in supplement
    assert "资产总计" not in supplement
    assert "## 主要数据引用来源" in supplement
    assert "[D1] source_type=wiki_metrics" in supplement
    assert "file=metrics/three_statements.json" in supplement
    assert "metric=利润表" in supplement
    assert "statement_type=income_statement" in supplement
    assert "canonical_name=operating_revenue" in supplement
    assert "metric_name=营业收入" in supplement
    assert "value=\"1,000\"" in supplement
    assert "raw_value=\"1,000\"" in supplement
    assert "unit=万元" in supplement
    assert "market=HK" in supplement
    assert "company_id=HK:00700" in supplement
    assert "report_id=2025-annual" in supplement
    assert "filing_id=HK:00700:2025-annual:fixture" in supplement
    assert "parse_run_id=HK:fixture" in supplement
    assert calls == [
        ("11111111-1111-1111-1111-111111111111", 7, 2),
        ("11111111-1111-1111-1111-111111111111", 7, 2),
    ]


def test_render_three_statement_supplement_supports_sec_xbrl_locator():
    supplement = citations._render_three_statement_primary_data_supplement(
        {
            "report_id": "2025-10-K",
            "rows": [
                {
                    "statement_label": "利润表",
                    "metric_name": "Revenue",
                    "period": "2025-09-27",
                    "raw_value": "416161000000",
                    "unit": "USD",
                    "source_type": "wiki_metrics",
                    "evidence_source_type": "sec_xbrl_fact",
                    "source_url": "https://www.sec.gov/example.htm",
                    "source_anchor": "f-78",
                    "xbrl_tag": "us-gaap:Revenue",
                    "file": "reports/2025-10-K/metrics/financial_data.json",
                }
            ],
        },
        primary_data_supplement_max_rows=3,
        table_source_links=lambda _task_id, _pdf_page, _table_index: "",
    )

    assert supplement is not None
    assert "source_url=https://www.sec.gov/example.htm" in supplement
    assert "source_anchor=f-78" in supplement
    assert "xbrl_tag=us-gaap:Revenue" in supplement
    assert supplement.count("[打开披露原文](https://www.sec.gov/example.htm#f-78)") == 2
    assert citations._has_structured_evidence_trace(supplement)


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


def test_render_wiki_fulltext_primary_data_supplement_defaults_file_to_report_id():
    supplement = citations._render_wiki_fulltext_primary_data_supplement(
        {
            "report_id": "2024-annual",
            "rows": [
                {
                    "snippet": "净利润增长",
                    "task_id": "56565656-5656-5656-5656-565656565656",
                    "pdf_page": 31,
                    "table_index": 1,
                    "md_line": 710,
                }
            ],
        },
        primary_data_supplement_max_rows=1,
        table_source_links=lambda task_id, pdf_page, table_index: "",
    )

    assert supplement is not None
    assert "file=reports/2024-annual/report.md" in supplement
    assert "file=reports/2025-annual/report.md" not in supplement


def test_render_postgres_primary_data_supplement_adds_rows_refs_and_links():
    evidence_calls = []
    source_calls = []

    def evidence_url(task_id, pdf_page, table_index, kind):
        evidence_calls.append((task_id, pdf_page, table_index, kind))
        return f"/evidence/{kind}/{task_id}/{pdf_page}/{table_index}"

    def markdown_table_cell(value):
        return str(value or "未返回")

    def table_source_links(task_id, pdf_page, table_index):
        source_calls.append((task_id, pdf_page, table_index))
        return f"/api/source/{task_id}/table/{table_index}"

    row = {
        "source_table": "financial_metrics",
        "statement_id": "stmt-1",
        "metric_name": "营业收入",
        "period_key": "2025",
        "value": "100",
        "unit": "万元",
        "task_id": "77777777-7777-7777-7777-777777777777",
        "pdf_page": 7,
        "table_index": 2,
        "md_line": 50,
    }
    supplement = citations._render_postgres_primary_data_supplement(
        {"rows": [row]},
        primary_data_supplement_max_rows=1,
        evidence_url=evidence_url,
        markdown_table_cell=markdown_table_cell,
        table_source_links=table_source_links,
        postgres_row_pdf_page=lambda item: item.get("pdf_page"),
        postgres_row_table_index=lambda item: item.get("table_index"),
        postgres_row_md_line=lambda item: item.get("md_line"),
        postgres_row_metric_name=lambda item: item.get("metric_name") or "未返回",
        postgres_row_value=lambda item: item.get("value"),
        postgres_row_unit=lambda item: item.get("unit"),
    )

    assert supplement is not None
    assert "| 营业收入 | 2025 | 100 万元 |" in supplement
    assert "## PostgreSQL 引用" in supplement
    assert "[P1] source_type=postgresql, table=financial_metrics" in supplement
    assert "statement_id=stmt-1" in supplement
    assert "metric=营业收入" in supplement
    assert "period_key=2025" in supplement
    assert "[打开PDF页](/evidence/pdf/77777777-7777-7777-7777-777777777777/7/2)" in supplement
    assert "[查看页来源](/evidence/page/77777777-7777-7777-7777-777777777777/7/2)" in supplement
    assert "[查看表格](/evidence/table/77777777-7777-7777-7777-777777777777/7/2)" in supplement
    assert source_calls == [("77777777-7777-7777-7777-777777777777", 7, 2)]
    assert evidence_calls == [
        ("77777777-7777-7777-7777-777777777777", 7, 2, "pdf"),
        ("77777777-7777-7777-7777-777777777777", 7, 2, "page"),
        ("77777777-7777-7777-7777-777777777777", 7, 2, "table"),
    ]


def test_primary_data_supplement_renderers_return_none_for_empty_inputs():
    def table_source_links(_task_id, _pdf_page, _table_index):
        return ""

    assert citations._render_three_statement_primary_data_supplement(
        {"rows": []},
        primary_data_supplement_max_rows=3,
        table_source_links=table_source_links,
    ) is None
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
    assert citations._render_postgres_primary_data_supplement(
        {"rows": []},
        primary_data_supplement_max_rows=3,
        evidence_url=lambda task_id, pdf_page, table_index, kind: "",
        markdown_table_cell=str,
        table_source_links=table_source_links,
        postgres_row_pdf_page=lambda row: row.get("pdf_page"),
        postgres_row_table_index=lambda row: row.get("table_index"),
        postgres_row_md_line=lambda row: row.get("md_line"),
        postgres_row_metric_name=lambda row: row.get("metric_name") or "",
        postgres_row_value=lambda row: row.get("value"),
        postgres_row_unit=lambda row: row.get("unit"),
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


def test_merge_primary_data_refs_replaces_same_locator_with_richer_numeric_reference():
    reply = """结论正文。

## 引用来源
[D1] source_type=wiki_metrics, metric=营业收入, period=2025, task_id=11111111-1111-1111-1111-111111111111, pdf_page=7, table_index=2, md_line=50
"""
    richer = (
        "[D2] source_type=wiki_metrics, metric=营业收入, period=2025, value=100, raw_value=100, "
        "unit=万元, company_id=HK:00700, filing_id=filing-1, parse_run_id=run-1, "
        "task_id=11111111-1111-1111-1111-111111111111, pdf_page=7, table_index=2, md_line=50"
    )

    merged = citations._merge_refs_into_reference_section(reply, [richer])

    assert merged.count("task_id=11111111-1111-1111-1111-111111111111") == 1
    assert "value=100" in merged
    assert "parse_run_id=run-1" in merged


def test_merge_primary_data_refs_replaces_incomplete_identity_despite_md_line_difference():
    reply = """结论正文。

## 引用来源
[D1] source_type=wiki_metrics, metric=总资产, period=2025, value=100, unit=KRW, task_id=11111111-1111-1111-1111-111111111111, pdf_page=83, table_index=67, md_line=未返回
"""
    authoritative = (
        "[D2] source_type=wiki_metrics, metric=总资产, period=2025, value=100, unit=KRW, "
        "market=KR, company_id=KR:005930, filing_id=filing-1, parse_run_id=run-1, "
        "task_id=11111111-1111-1111-1111-111111111111, pdf_page=83, table_index=67, md_line=1800"
    )

    merged = citations._merge_refs_into_reference_section(reply, [authoritative])

    assert merged.count("task_id=11111111-1111-1111-1111-111111111111") == 1
    assert "company_id=KR:005930" in merged
    assert "md_line=1800" in merged


def test_primary_data_supplement_emits_stable_evidence_id_and_quote():
    supplement = citations._render_three_statement_primary_data_supplement(
        {
            "market": "KR",
            "company_id": "KR:005930",
            "filing_id": "KR:005930:2025-annual",
            "parse_run_id": "run-kr",
            "report_id": "2025-annual",
            "rows": [
                {
                    "statement_type": "balance_sheet",
                    "statement_label": "资产负债表",
                    "metric_key": "total_assets",
                    "metric_name": "总资产",
                    "period": "2025-12-31",
                    "raw_value": "566,942,110",
                    "unit": "KRW million",
                    "currency": "KRW",
                    "scale": "1000000",
                    "task_id": "task-kr",
                    "pdf_page": 83,
                    "table_index": 67,
                    "md_line": 1385,
                    "evidence_id": "wiki:fact-1",
                    "source_quote": "总资产 | 566,942,110",
                }
            ],
        },
        primary_data_supplement_max_rows=4,
        table_source_links=lambda *_: "",
    )

    assert supplement is not None
    assert "evidence_id=wiki:fact-1" in supplement
    assert 'quote="总资产 | 566,942,110"' in supplement


def test_structured_evidence_requires_real_task_id_and_page_or_table():
    cited = (
        "[1] source_type=postgresql, task_id=11111111-1111-1111-1111-111111111111, "
        "pdf_page=7, table_index=2, md_line=50"
    )
    uncited = "[1] source_type=postgresql, task_id=fake, pdf_page=7, table_index=2"
    table_only = "[1] source_type=postgresql, task_id=22222222-2222-2222-2222-222222222222, table_index=2"
    page_alias = (
        "[1] source_type=postgresql, task_id=33333333-3333-3333-3333-333333333333, "
        "pdf_page_number=8"
    )
    sec_xbrl = (
        "[1] source_type=wiki_metrics, evidence_source_type=sec_xbrl_fact, "
        "source_url=https://www.sec.gov/example.htm, source_anchor=f-78, xbrl_tag=us-gaap:Revenue"
    )

    assert citations._has_structured_evidence_trace(cited)
    assert runtime._has_structured_evidence_trace(cited)
    assert citations._has_structured_evidence_trace(table_only)
    assert citations._has_structured_evidence_trace(page_alias)
    assert citations._has_structured_evidence_trace(sec_xbrl)
    assert runtime._has_structured_evidence_trace(sec_xbrl)
    assert not citations._has_structured_evidence_trace(uncited)


def test_normalize_plain_inline_latex_replaces_known_symbols_only():
    text = "A $\\to$ B，x $ \\leq $ y，保留 $\\unknown$ 和 $x+1$。"

    assert citations.normalize_plain_inline_latex(text) == "A → B，x ≤ y，保留 $\\unknown$ 和 $x+1$。"
    assert citations.normalize_plain_inline_latex("毛利率 $\\%$，A $\\Rightarrow$ B，x $\\approx$ y") == (
        "毛利率 %，A ⇒ B，x ≈ y"
    )
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


def test_three_statement_refs_keep_distinct_metrics_from_the_same_table():
    shared_locator = {
        "task_id": "task-cn",
        "pdf_page": 83,
        "table_index": 67,
        "md_line": 1800,
        "statement_label": "资产负债表",
        "statement_type": "balance_sheet",
        "period": "2025-12-31",
        "unit": "人民币元",
    }
    supplement = citations._render_three_statement_primary_data_supplement(
        {
            "report_id": "2025-annual",
            "rows": [
                {
                    **shared_locator,
                    "metric_key": "current_assets",
                    "metric_name": "流动资产合计",
                    "raw_value": "128,553,272,323.33",
                    "evidence_id": "wiki:current-assets",
                },
                {
                    **shared_locator,
                    "metric_key": "total_assets",
                    "metric_name": "资产总计",
                    "raw_value": "202,961,073,175.76",
                    "evidence_id": "wiki:total-assets",
                },
            ],
        },
        primary_data_supplement_max_rows=2,
        table_source_links=lambda *_: "",
    )
    merged = citations._merge_primary_data_refs_into_citations(
        "结论正文。",
        supplement=supplement,
        auto_evidence_section_titles={"主要数据引用来源", "主要数据溯源补充"},
    )

    assert supplement is not None
    assert supplement.count("[D1]") == 1
    assert supplement.count("[D2]") == 1
    assert "canonical_name=current_assets" in supplement
    assert "evidence_id=wiki:current-assets" in supplement
    assert 'value="128,553,272,323.33"' in supplement
    assert "canonical_name=total_assets" in supplement
    assert "evidence_id=wiki:total-assets" in supplement
    assert 'value="202,961,073,175.76"' in supplement
    assert merged.count("source_type=wiki_metrics") == 2
    assert "canonical_name=current_assets" in merged
    assert "canonical_name=total_assets" in merged


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


def test_merge_refs_into_reference_section_handles_empty_body():
    ref = (
        "[D1] source_type=wiki_metrics, file=metrics/three_statements.json, metric=收入, "
        "period=2025, task_id=11111111-1111-1111-1111-111111111111, pdf_page=7, "
        "table_index=2, md_line=50"
    )

    merged = citations._merge_refs_into_reference_section("", [ref])

    assert merged == f"## 引用来源\n{ref}"


def test_merge_refs_into_reference_section_returns_body_when_all_refs_invalid():
    body = "结论正文。"
    refs = [
        "普通说明文字。",
        "[D1] source_type=wiki_metrics, pdf_page=7, table_index=2",
        "[D2] source_type=wiki_metrics, task_id=22222222-2222-2222-2222-222222222222, table_index=3",
        "| [D3] source_type=wiki_metrics, task_id=33333333-3333-3333-3333-333333333333, pdf_page=9, table_index=4 |",
    ]

    merged = citations._merge_refs_into_reference_section(body, refs)

    assert merged == body


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


def test_merge_refs_into_reference_section_inserts_before_next_peer_heading():
    body = """结论正文。

## 引用来源
已有说明。

### 补充说明
仍属于引用来源章节。

## 风险提示
请复核。
"""
    refs = [
        "[D1] source_type=wiki_metrics, file=metrics/three_statements.json, metric=收入, period=2025, task_id=11111111-1111-1111-1111-111111111111, pdf_page=7, table_index=2, md_line=50"
    ]

    merged = citations._merge_refs_into_reference_section(body, refs)
    citation_section, risk_section = merged.split("## 风险提示")

    assert "### 补充说明" in citation_section
    assert "metric=收入" in citation_section
    assert "metric=收入" not in risk_section


def test_merge_refs_into_tertiary_reference_section_stops_before_peer_or_parent_heading():
    ref = (
        "[D1] source_type=wiki_metrics, file=metrics/three_statements.json, metric=收入, "
        "period=2025, task_id=11111111-1111-1111-1111-111111111111, pdf_page=7, "
        "table_index=2, md_line=50"
    )
    peer_body = """## 数据分析
正文。

### 引用来源
已有说明。

#### 来源说明
仍属于引用来源章节。

### 风险提示
请复核。
"""
    parent_body = """## 数据分析
正文。

### 引用来源
已有说明。

#### 来源说明
仍属于引用来源章节。

## 风险提示
请复核。
"""

    peer_merged = citations._merge_refs_into_reference_section(peer_body, [ref])
    parent_merged = citations._merge_refs_into_reference_section(parent_body, [ref])
    peer_citation_section, peer_risk_section = peer_merged.split("### 风险提示")
    parent_citation_section, parent_risk_section = parent_merged.split("## 风险提示")

    assert "#### 来源说明" in peer_citation_section
    assert "metric=收入" in peer_citation_section
    assert "metric=收入" not in peer_risk_section
    assert "#### 来源说明" in parent_citation_section
    assert "metric=收入" in parent_citation_section
    assert "metric=收入" not in parent_risk_section


def test_reply_has_requested_metric_evidence_checks_requested_terms_in_reference_lines():
    reply = """正文提到了利润，但引用只给收入。

[D1] source_type=wiki_metrics, file=metrics/three_statements.json, metric=营业收入, period=2025, value=100, raw_value=100, unit=万元, task_id=11111111-1111-1111-1111-111111111111, pdf_page=7, table_index=2, md_line=50
"""

    def normalize(value):
        return "".join(str(value).lower().split())

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


def test_reply_has_requested_metric_evidence_rejects_locator_without_numeric_fact():
    reply = (
        "[D1] source_type=wiki_metrics, metric=营业收入, period=2025, "
        "task_id=11111111-1111-1111-1111-111111111111, pdf_page=7, table_index=2, md_line=50"
    )

    assert not citations._reply_has_requested_metric_evidence(
        "收入是多少",
        reply,
        postgres_requested_metric_terms=lambda _message: ["营业收入"],
        normalize_financial_text=lambda value: "".join(str(value).lower().split()),
    )


def test_reply_has_requested_metric_evidence_ignores_metrics_outside_reference_lines():
    reply = """正文提到了净利润。

[D1] source_type=wiki_metrics, file=metrics/three_statements.json, metric=营业收入, period=2025, task_id=11111111-1111-1111-1111-111111111111, pdf_page=7, table_index=2, md_line=50
"""

    def normalize(value):
        return "".join(str(value).lower().split())

    assert not citations._reply_has_requested_metric_evidence(
        "净利润是多少",
        reply,
        postgres_requested_metric_terms=lambda message: ["净利润"],
        normalize_financial_text=normalize,
    )
