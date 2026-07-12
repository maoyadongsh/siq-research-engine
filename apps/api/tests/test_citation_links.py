import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, "/home/maoyd/.hermes/profiles/shared/scripts")

from local_citations import _report_table_records, primary_report, resolve_citation_refs
from services.citation_links import append_missing_pdf_source_links

from services import citation_links

SH_BANK_TASK_ID = "fb07089b-9570-4902-bf20-eb38578f2b76"
KINGFA_TASK_ID = "23658e24-111e-4399-8c0b-e42c41eeb943"
MIDEA_TASK_ID = "f4dead73-e0de-42b4-b1b7-d8cf217214ee"
SABIC_TASK_ID = "914d6a5a-9aed-47ab-b4ae-a380a9b95253"
BASF_TASK_ID = "03690a47-062e-42eb-9ad7-d609a87cf777"
WANHUA_TASK_ID = "f256875c-dad2-4fbf-9240-ef288fea0b0f"
PURE_HELPER_TASK_ID = "11111111-1111-1111-1111-111111111111"


def _disable_local_enricher(monkeypatch):
    monkeypatch.setattr(citation_links, "_get_enrich_citation_line", lambda: None)


def test_report_table_records_treats_null_tables_as_empty(tmp_path):
    report_dir = tmp_path / "reports" / "2025-annual"
    report_dir.mkdir(parents=True)
    (report_dir / "report.json").write_text('{"tables": null}', encoding="utf-8")

    assert _report_table_records(tmp_path, "2025-annual") == []


def test_chat_citation_postprocessor_keeps_reply_when_enricher_fails(monkeypatch):
    def broken_enricher(line, context_text):
        raise TypeError("bad citation metadata")

    monkeypatch.setattr(citation_links, "_get_enrich_citation_line", lambda: broken_enricher)
    text = f"[1] source_type=report_md, file=report.md, task_id={SH_BANK_TASK_ID}, pdf_page=1"

    cleaned = append_missing_pdf_source_links(text)

    assert "bad citation metadata" not in cleaned
    assert f"task_id={SH_BANK_TASK_ID}" in cleaned


def test_postprocessor_skips_citations_without_task_id_or_pdf_page(monkeypatch):
    _disable_local_enricher(monkeypatch)
    text = f"""[1] source_type=wiki_metrics, pdf_page=7, table_index=3
[2] source_type=wiki_metrics, task_id={PURE_HELPER_TASK_ID}, table_index=3
"""

    assert append_missing_pdf_source_links(text) == text.rstrip("\n")


def test_postprocessor_normalizes_local_api_links_preserving_query_and_fragment(monkeypatch):
    _disable_local_enricher(monkeypatch)
    monkeypatch.setenv("SIQ_PUBLIC_ORIGIN", "https://public.example")
    text = (
        f"[1] source_type=wiki_metrics, task_id={PURE_HELPER_TASK_ID}, pdf_page=7, table_index=3，"
        f"[打开PDF定位页7](http://localhost:8276/api/pdf_page/{PURE_HELPER_TASK_ID}/7?format=html#page%207)，"
        f"[查看定位页7来源](/api/source/{PURE_HELPER_TASK_ID}/page/7?format=html#quote%2F7)，"
        f"[查看可读表格3](http://127.0.0.1:8000/api/source/{PURE_HELPER_TASK_ID}/table/3?format=html#table%203)"
    )

    cleaned = append_missing_pdf_source_links(text)

    assert "http://localhost" not in cleaned
    assert "http://127.0.0.1" not in cleaned
    assert f"https://public.example/api/pdf_page/{PURE_HELPER_TASK_ID}/7?format=html#page%207" in cleaned
    assert f"https://public.example/api/source/{PURE_HELPER_TASK_ID}/page/7?format=html#quote%2F7" in cleaned
    assert f"https://public.example/api/source/{PURE_HELPER_TASK_ID}/table/3?format=html#table%203" in cleaned
    assert cleaned.count(f"/api/pdf_page/{PURE_HELPER_TASK_ID}/7?format=html") == 1
    assert cleaned.count(f"/api/source/{PURE_HELPER_TASK_ID}/page/7?format=html") == 1
    assert cleaned.count(f"/api/source/{PURE_HELPER_TASK_ID}/table/3?format=html") == 1


def test_postprocessor_strips_bare_trace_links_before_appending_links(monkeypatch):
    _disable_local_enricher(monkeypatch)
    monkeypatch.setenv("SIQ_PUBLIC_ORIGIN", "https://public.example")
    text = (
        f"[1] source_type=wiki_document_links, task_id={PURE_HELPER_TASK_ID}, pdf_page=137, table_index=165, md_line=4186，"
        f"打开PDF页(/api/pdf_page/{PURE_HELPER_TASK_ID}/137)，"
        f"查看页来源(/api/source/{PURE_HELPER_TASK_ID}/page/137)，"
        f"查看表格(/api/source/{PURE_HELPER_TASK_ID}/table/165)"
    )

    cleaned = append_missing_pdf_source_links(text)

    assert "打开PDF页(/api/pdf_page" not in cleaned
    assert "查看页来源(/api/source" not in cleaned
    assert "查看表格(/api/source" not in cleaned
    assert f"https://public.example/api/pdf_page/{PURE_HELPER_TASK_ID}/137?format=html" in cleaned
    assert f"https://public.example/api/source/{PURE_HELPER_TASK_ID}/page/137?format=html" in cleaned
    assert f"https://public.example/api/source/{PURE_HELPER_TASK_ID}/table/165?format=html" in cleaned


def test_tool_citation_borrows_explicit_bound_source_refs(monkeypatch):
    monkeypatch.setenv("SIQ_PUBLIC_ORIGIN", "https://public.example")
    text = f"""## 引用来源

[1] source_type=wiki_document_links, file=semantic/document_links.json, metric=(1).商誉账面原值, period=2025-annual, task_id={PURE_HELPER_TASK_ID}, pdf_page=137, table_index=165, md_line=4186。
[2] source_type=wiki_document_links, file=semantic/document_links.json, metric=(2).商誉减值准备, period=2025-annual, task_id={PURE_HELPER_TASK_ID}, pdf_page=137, table_index=166, md_line=4196。
[3] source_type=financial_calculator, file=agents/hermes/profiles/shared/scripts/financial_calculator.py, metric=原值变动/减值准备变动/集中度占比, period=2025-annual, task_id={PURE_HELPER_TASK_ID}, pdf_page=未返回, table_index=未返回, md_line=未返回, note=派生计算器校验，分子分母分别绑定引用 [1][2]
"""

    cleaned = append_missing_pdf_source_links(text)
    tool_line = next(line for line in cleaned.splitlines() if line.startswith("[3]"))

    assert "source_type=financial_calculator" in tool_line
    assert "pdf_page=137" in tool_line
    assert "table_index=165,166" in tool_line
    assert "md_line=4186,4196" in tool_line
    assert f"https://public.example/api/source/{PURE_HELPER_TASK_ID}/table/165?format=html" in tool_line
    assert f"https://public.example/api/source/{PURE_HELPER_TASK_ID}/table/166?format=html" in tool_line


def test_tool_citation_without_explicit_refs_borrows_recent_source_refs(monkeypatch):
    monkeypatch.setenv("SIQ_PUBLIC_ORIGIN", "https://public.example")
    text = f"""## 引用来源

[1] source_type=wiki_document_links, file=semantic/document_links.json, metric=(1).商誉账面原值, period=2025-annual, task_id={PURE_HELPER_TASK_ID}, pdf_page=137, table_index=165, md_line=4186。
[2] source_type=wiki_document_links, file=semantic/document_links.json, metric=(2).商誉减值准备, period=2025-annual, task_id={PURE_HELPER_TASK_ID}, pdf_page=137, table_index=166, md_line=4196。
[3] source_type=financial_reconciliation_validator, file=agents/hermes/profiles/shared/scripts/financial_reconciliation_validator.py, metric=原值-减值准备=净额, period=2025-annual, task_id={PURE_HELPER_TASK_ID}, pdf_page=未返回, table_index=未返回, md_line=未返回, note=原值/减值/净额三项勾稽校验
"""

    cleaned = append_missing_pdf_source_links(text)
    tool_line = next(line for line in cleaned.splitlines() if line.startswith("[3]"))

    assert "source_type=financial_reconciliation_validator" in tool_line
    assert "pdf_page=137" in tool_line
    assert "table_index=165,166" in tool_line
    assert "md_line=4186,4196" in tool_line
    assert f"https://public.example/api/source/{PURE_HELPER_TASK_ID}/table/165?format=html" in tool_line
    assert f"https://public.example/api/source/{PURE_HELPER_TASK_ID}/table/166?format=html" in tool_line


def test_postprocessor_keeps_printed_page_labels_aligned_with_missing_slots(monkeypatch):
    _disable_local_enricher(monkeypatch)
    text = (
        f"[1] source_type=wiki_metrics, task_id={PURE_HELPER_TASK_ID}, "
        "pdf_page=7,8, printed_page=未返回,12, table_index=3"
    )

    cleaned = append_missing_pdf_source_links(text)

    assert "打开PDF定位页7 / 印刷页12" not in cleaned
    assert "查看定位页7来源 / 印刷页12" not in cleaned
    assert "打开PDF定位页8 / 印刷页12" in cleaned
    assert "查看定位页8来源 / 印刷页12" in cleaned


def test_report_md_line_uses_markdown_page_anchor():
    result = resolve_citation_refs(
        "上海银行 601229",
        "前十名普通股股东",
        "2025",
        source_type="report_md",
        file_name="reports/2025-annual/report.md",
        line_text="2428",
        table_text="135",
        page_text="135",
    )

    assert result["status"] == "ok"
    first_ref = result["refs"][0]
    assert first_ref["pdf_page"] == 134
    assert first_ref["printed_page_number"] == "133"
    assert first_ref["table_index"] == 90
    assert first_ref["md_line"] == 2428
    assert first_ref["open_pdf_page_url"].endswith(f"/api/pdf_page/{SH_BANK_TASK_ID}/134?format=html")
    assert first_ref["open_source_table_url"].endswith(f"/api/source/{SH_BANK_TASK_ID}/table/90?format=html")


def test_primary_report_prefers_annual_for_shanghai_bank_annual_questions():
    company_dir = Path("/home/maoyd/wiki/companies/601229-上海银行")

    annual = primary_report(company_dir, query_text="上海银行2025年报前十名普通股股东")
    quarterly = primary_report(company_dir, query_text="上海银行2025三季报营业收入")

    assert annual["report_id"] == "2025-annual"
    assert annual["task_id"] == SH_BANK_TASK_ID
    assert str(annual["document_full"]).endswith("reports/2025-annual/document_full.json")
    assert quarterly["report_id"] == "2025-quarterly-report"


def test_chat_citation_postprocessor_rewrites_wrong_trace_links_inline():
    text = f"""上海银行前十大股东如下。

## 引用来源

[1] source_type=report_md, file=report.md, quote="前十名普通股股东明细", period=2025, task_id={SH_BANK_TASK_ID}, pdf_page=135, table_index=135, md_line=2428，[打开PDF第135页](https://arthurmao.synology.me:8276/api/pdf_page/{SH_BANK_TASK_ID}/135)，[查看第135页来源](https://arthurmao.synology.me:8276/api/source/{SH_BANK_TASK_ID}/page/135)，[查看可读表格135](https://arthurmao.synology.me:8276/api/source/{SH_BANK_TASK_ID}/table/135)

## 可打开来源链接

[1] [打开PDF第135页](https://arthurmao.synology.me:8276/api/pdf_page/{SH_BANK_TASK_ID}/135)
"""

    cleaned = append_missing_pdf_source_links(text)

    assert "## 可打开来源链接" not in cleaned
    assert "pdf_page=134" in cleaned
    assert "printed_page=133" in cleaned
    assert "table_index=90" in cleaned
    assert "打开PDF定位页134 / 印刷页133" in cleaned
    assert f"/api/pdf_page/{SH_BANK_TASK_ID}/134?format=html" in cleaned
    assert f"/api/source/{SH_BANK_TASK_ID}/page/134?format=html" in cleaned
    assert f"/api/source/{SH_BANK_TASK_ID}/table/90?format=html" in cleaned
    assert f"/api/pdf_page/{SH_BANK_TASK_ID}/135" not in cleaned
    assert f"/api/source/{SH_BANK_TASK_ID}/table/135" not in cleaned


def test_chat_citation_postprocessor_replaces_old_multi_page_field():
    text = f"""## 引用来源

[1] source_type=report_md, file=report.md, quote="前十名普通股股东明细", period=2025, task_id={SH_BANK_TASK_ID}, pdf_page=135,136, table_index=135, md_line=2428
"""

    cleaned = append_missing_pdf_source_links(text)

    assert "pdf_page=134" in cleaned
    assert "pdf_page=134,136" not in cleaned
    assert "pdf_page=135,136" not in cleaned
    assert "table_index=90" in cleaned


def test_chat_citation_postprocessor_keeps_appended_fields_inside_sentence():
    text = f"""## 引用来源

[1] source_type=report_md, file=reports/2025-annual/report.md, metric=前十名普通股股东, task_id={SH_BANK_TASK_ID}, pdf_page=135, table_index=135, md_line=2428。
"""

    cleaned = append_missing_pdf_source_links(text)

    assert "md_line=2428, printed_page=133，[" in cleaned
    assert "md_line=2428。, printed_page=133" not in cleaned
    assert "印刷页133。]" not in cleaned
    assert "查看可读表格90](" in cleaned
    assert cleaned.rstrip().endswith(")。")


def test_wiki_report_table_human_capital_trace_stays_on_structured_table():
    text = f"""金发科技人员结构如下。

## 引用来源

[1] source_type=wiki_report_table, file=reports/2025-annual/report.md, metric=员工情况/人才结构, period=2025-annual, task_id={KINGFA_TASK_ID}, pdf_page=68, table_index=47, md_line=1567。
"""

    cleaned = append_missing_pdf_source_links(text)

    assert "source_type=wiki_report_table" in cleaned
    assert "pdf_page=68" in cleaned
    assert "table_index=47" in cleaned
    assert "md_line=1567" in cleaned
    assert f"/api/pdf_page/{KINGFA_TASK_ID}/68?format=html" in cleaned
    assert f"/api/source/{KINGFA_TASK_ID}/table/47?format=html" in cleaned
    assert "pdf_page=29" not in cleaned
    assert "table_index=26" not in cleaned


def test_wiki_report_table_human_capital_wrong_trace_is_corrected_by_table_content():
    text = f"""金发科技人员结构如下。

## 引用来源

[1] source_type=wiki_report_table, file=reports/2025-annual/report.md, metric=员工情况/人才结构, period=2025-annual, task_id={KINGFA_TASK_ID}, pdf_page=29, table_index=26, md_line=726。
"""

    cleaned = append_missing_pdf_source_links(text)

    assert "source_type=wiki_report_table" in cleaned
    assert "pdf_page=68" in cleaned
    assert "table_index=47" in cleaned
    assert "md_line=1567" in cleaned
    assert f"/api/pdf_page/{KINGFA_TASK_ID}/68?format=html" in cleaned
    assert f"/api/source/{KINGFA_TASK_ID}/page/68?format=html" in cleaned
    assert f"/api/source/{KINGFA_TASK_ID}/table/47?format=html" in cleaned
    assert "pdf_page=29" not in cleaned
    assert "table_index=26" not in cleaned


def test_wiki_report_table_human_capital_midea_wrong_trace_is_corrected_by_table_content():
    text = f"""美的集团人员结构如下。

## 引用来源

[1] source_type=wiki_report_table, file=reports/2025-annual/report.md, metric=员工情况/人才结构, period=2025-annual, task_id={MIDEA_TASK_ID}, pdf_page=57, table_index=28, md_line=857。
"""

    cleaned = append_missing_pdf_source_links(text)

    assert "source_type=wiki_report_table" in cleaned
    assert "pdf_page=77" in cleaned
    assert "table_index=39" in cleaned
    assert "md_line=1117" in cleaned
    assert f"/api/pdf_page/{MIDEA_TASK_ID}/77?format=html" in cleaned
    assert f"/api/source/{MIDEA_TASK_ID}/page/77?format=html" in cleaned
    assert f"/api/source/{MIDEA_TASK_ID}/table/39?format=html" in cleaned
    assert "pdf_page=57" not in cleaned
    assert "table_index=28" not in cleaned


def test_document_link_keeps_structured_table_url_when_anchor_page_differs():
    result = resolve_citation_refs(
        "000625-长安汽车",
        "应收账款明细",
        "2025",
        source_type="wiki_document_links",
        file_name="semantic/document_links.json",
    )

    refs = result["refs"]
    target = next(ref for ref in refs if ref.get("table_index") == 101)
    assert target["pdf_page"] == 128
    assert target.get("table_pdf_page") is None
    assert target["pdf_page_conflict"]["resolution"] == "structured_page_preferred"
    assert target["open_source_page_url"].endswith("/page/128?format=html")
    assert target["open_source_table_url"].endswith("/table/101?format=html")


def test_document_link_detail_query_prioritizes_specific_intent_table():
    result = resolve_citation_refs(
        "上汽集团",
        "应收账款账龄",
        "2025",
        source_type="wiki_document_links",
        file_name="semantic/document_links.json",
    )

    assert result["status"] == "ok"
    first_ref = result["refs"][0]
    assert first_ref["table_index"] == 103
    assert first_ref["metric"] == "(1).按账龄披露"
    assert first_ref["open_source_table_url"].endswith("/table/103?format=html")


def test_document_link_generic_detail_requires_target_table_base():
    result = resolve_citation_refs(
        "美的集团",
        "商誉明细",
        "2025",
        source_type="wiki_document_links",
        file_name="semantic/document_links.json",
    )

    assert result["status"] == "ok"
    assert [ref["table_index"] for ref in result["refs"]] == [163]
    assert result["refs"][0]["metric"] == "(21) 商誉"


def test_goodwill_book_value_resolves_to_balance_sheet_main_statement():
    result = resolve_citation_refs(
        "上汽集团",
        "商誉账面价值",
        "2025",
        source_type="wiki_metrics",
        file_name="metrics/three_statements.json",
    )

    assert result["status"] == "ok"
    first_ref = result["refs"][0]
    assert first_ref["source_type"] == "wiki_metrics"
    assert first_ref["file"] == "metrics/three_statements.json"
    assert first_ref["metric"] == "资产负债表核心数据"
    assert first_ref["pdf_page"] == 65
    assert first_ref["table_index"] == 84


def test_cash_flow_document_link_citation_is_corrected_to_main_statement():
    text = """请评估上汽集团现金流。

## 引用来源

[1] source_type=wiki_document_links, file=semantic/document_links.json, metric=现金流量表核心数据, period=2025-annual, task_id=7dbc35a7-7626-4e81-810e-5dbb764434e0, pdf_page=135, table_index=163, md_line=4152。
"""

    cleaned = append_missing_pdf_source_links(text)

    assert "source_type=wiki_metrics" in cleaned
    assert "file=metrics/three_statements.json" in cleaned
    assert "metric=现金流量表核心数据" in cleaned
    assert "pdf_page=72" in cleaned
    assert "table_index=88" in cleaned
    assert "md_line=1904" in cleaned
    assert "/api/source/7dbc35a7-7626-4e81-810e-5dbb764434e0/table/88?format=html" in cleaned
    assert "table/163" not in cleaned
    assert "pdf_page=135" not in cleaned


def test_balance_sheet_document_link_citation_is_corrected_to_main_statement_tables():
    text = """美的集团资产负债结构如下。

## 引用来源

[1] source_type=wiki_document_links, file=semantic/document_links.json, metric=资产构成概览, period=2025-annual, task_id=f4dead73-e0de-42b4-b1b7-d8cf217214ee, pdf_page=214, table_index=179, md_line=4518。
"""

    cleaned = append_missing_pdf_source_links(text)

    assert "source_type=wiki_metrics" in cleaned
    assert "file=metrics/three_statements.json" in cleaned
    assert "metric=资产负债表核心数据" in cleaned
    assert "pdf_page=132,133" in cleaned
    assert "table_index=89,90" in cleaned
    assert "md_line=2497,2508" in cleaned
    assert "/api/source/f4dead73-e0de-42b4-b1b7-d8cf217214ee/table/89?format=html" in cleaned
    assert "/api/source/f4dead73-e0de-42b4-b1b7-d8cf217214ee/table/90?format=html" in cleaned
    assert "table/179" not in cleaned
    assert "pdf_page=214" not in cleaned


@pytest.mark.slow
def test_sabic_report_markdown_citations_use_real_pages_and_tables():
    text = f"""SABIC 人效数据如下。

## 引用来源

[1] source_type=report_markdown, file=companies/GENSABIC-SABIC/reports/2025-annual/report.md, metric=员工总数, period=2025-annual, evidence_id=ev_GENSABIC_2025_annual_000005, task_id={SABIC_TASK_ID}, pdf_page=3, table_index=未返回, md_line=1035。
[2] source_type=report_markdown, file=companies/GENSABIC-SABIC/reports/2025-annual/report.md, metric=员工福利, period=2025-annual, evidence_id=ev_GENSABIC_2025_annual_000023, task_id={SABIC_TASK_ID}, pdf_page=3, table_index=未返回, md_line=1876。
[3] source_type=report_markdown, file=companies/GENSABIC-SABIC/reports/2025-annual/report.md, metric=人效KPI, period=2025-annual, evidence_id=ev_GENSABIC_2025_annual_000029, task_id={SABIC_TASK_ID}, pdf_page=3, table_index=未返回, md_line=2967。
[4] source_type=report_markdown, file=companies/GENSABIC-SABIC/reports/2025-annual/report.md, metric=培训KPI, period=2025-annual, evidence_id=ev_GENSABIC_2025_annual_000051, task_id={SABIC_TASK_ID}, pdf_page=3, table_index=未返回, md_line=3015。
"""

    cleaned = append_missing_pdf_source_links(text)

    assert "pdf_page=23" in cleaned
    assert "md_line=1035" in cleaned
    assert "pdf_page=46" in cleaned
    assert "table_index=19" in cleaned
    assert f"/api/source/{SABIC_TASK_ID}/table/19?format=html" in cleaned
    assert "pdf_page=74" in cleaned
    assert "table_index=24" in cleaned
    assert f"/api/source/{SABIC_TASK_ID}/table/24?format=html" in cleaned
    assert "pdf_page=75" in cleaned
    assert "table_index=25" in cleaned
    assert f"/api/source/{SABIC_TASK_ID}/table/25?format=html" in cleaned
    assert re.search(r"\bpdf_page=3(?:\b|[，,])", cleaned) is None


@pytest.mark.slow
def test_report_markdown_citation_treats_out_of_range_page_as_possible_line_anchor():
    text = f"""BASF 净利润溯源。

## 引用来源

[4] source_type=wiki_evidence, file=reports/2025-annual/report.md, metric=净利润, period=2025-annual, task_id={BASF_TASK_ID}, pdf_page=2437, table_index=未返回, md_line=4634
"""

    cleaned = append_missing_pdf_source_links(text)

    assert "source_type=wiki_evidence" in cleaned
    assert "file=reports/2025-annual/report.md" in cleaned
    assert "pdf_page=107" in cleaned
    assert "table_index=70" in cleaned
    assert "md_line=2437" in cleaned
    assert f"/api/pdf_page/{BASF_TASK_ID}/107?format=html" in cleaned
    assert f"/api/source/{BASF_TASK_ID}/page/107?format=html" in cleaned
    assert f"/api/source/{BASF_TASK_ID}/table/70?format=html" in cleaned
    assert "pdf_page=2437" not in cleaned
    assert f"/api/pdf_page/{BASF_TASK_ID}/2437" not in cleaned


@pytest.mark.slow
def test_multi_company_citations_resolve_each_line_by_own_task_id():
    text = f"""## 修正后的引用来源

### 万华化学（task_id: {WANHUA_TASK_ID}）

[1] source_type=wiki_metrics, file=metrics/three_statements.json, metric=利润表核心数据, period=2025-annual, task_id={WANHUA_TASK_ID}, pdf_page=83, table_index=83, md_line=2099。

### 巴斯夫（task_id: {BASF_TASK_ID}）

[5] source_type=wiki_report_table, file=reports/2025-annual/report.md, metric=营业收入, period=2025-annual, task_id={BASF_TASK_ID}, pdf_page=157, table_index=145, md_line=4331。
[6] source_type=wiki_report_table, file=reports/2025-annual/report.md, metric=人力成本(Personnel expenses), period=2025-annual, task_id={BASF_TASK_ID}, pdf_page=227, table_index=260, md_line=6434。
"""

    cleaned = append_missing_pdf_source_links(text)

    basf_block = cleaned.split("### 巴斯夫", 1)[1]
    assert f"task_id={BASF_TASK_ID}" in basf_block
    assert f"/api/pdf_page/{BASF_TASK_ID}/" in basf_block
    assert f"/api/source/{BASF_TASK_ID}/page/" in basf_block
    assert f"/api/source/{BASF_TASK_ID}/table/260?format=html" in basf_block
    assert f"/api/pdf_page/{WANHUA_TASK_ID}/" not in basf_block
    assert f"/api/source/{WANHUA_TASK_ID}/" not in basf_block
