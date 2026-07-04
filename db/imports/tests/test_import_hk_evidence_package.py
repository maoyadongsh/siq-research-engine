import importlib.util
from pathlib import Path


def _load_importer():
    path = Path(__file__).resolve().parents[1] / "import_hk_evidence_package_to_postgres.py"
    spec = importlib.util.spec_from_file_location("import_hk_evidence_package_to_postgres", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_hk_importer_rejects_non_hk_schema():
    importer = _load_importer()
    try:
        importer.validate_schema("pdf2md")
    except SystemExit as exc:
        assert "pdf2md_hk" in str(exc)
    else:
        raise AssertionError("validate_schema should reject legacy pdf2md")


def test_hk_importer_parse_run_id_is_stable():
    importer = _load_importer()
    manifest = {
        "filing_id": "HK:00700:12100024",
        "parser_version": "p1",
        "rules_version": "r1",
        "artifact_hashes": {"metrics/financial_data.json": "abc"},
    }
    first = importer.stable_parse_run_id(manifest, manifest["artifact_hashes"])
    second = importer.stable_parse_run_id(manifest, manifest["artifact_hashes"])
    assert first == second


def test_hk_importer_database_url_defaults_to_siq_hk(monkeypatch):
    importer = _load_importer()
    for name in ("DATABASE_URL", "SIQ_HK_PGDATABASE", "SIQ_PGDATABASE", "PGDATABASE"):
        monkeypatch.delenv(name, raising=False)

    assert importer.database_url(None).endswith("/siq_hk")


def test_hk_importer_database_url_prefers_hk_database_env(monkeypatch):
    importer = _load_importer()
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("SIQ_PGDATABASE", "siq")
    monkeypatch.setenv("PGDATABASE", "postgres")
    monkeypatch.setenv("SIQ_HK_PGDATABASE", "siq_hk_custom")

    assert importer.database_url(None).endswith("/siq_hk_custom")


def test_hk_ddl_exposes_agent_recall_columns_and_views():
    importer = _load_importer()
    ddl = importer.DDL_PATH.read_text(encoding="utf-8")

    assert "alter table pdf2md_hk.filings add column if not exists report_id text" in ddl
    assert "alter table pdf2md_hk.pdf_tables add column if not exists bbox jsonb" in ddl
    assert "alter table pdf2md_hk.evidence_citations add column if not exists bbox jsonb" in ddl
    assert "alter table pdf2md_hk.retrieval_chunks add column if not exists company_id text" in ddl
    assert "alter table pdf2md_hk.retrieval_chunks add column if not exists text text" in ddl
    assert "uq_pdf2md_hk_companies_hkex_stock_code" in ddl
    assert "on pdf2md_hk.companies (hkex_stock_code)" in ddl
    assert "create or replace view pdf2md_hk.v_agent_financial_facts" in ddl
    assert "create or replace view pdf2md_hk.v_latest_company_reports" in ddl


def test_hk_importer_builds_stable_company_and_filing_records(tmp_path):
    importer = _load_importer()
    manifest = {
        "market": "HK",
        "company_id": "HK:00700",
        "ticker": "00700",
        "stock_code": "00700",
        "hkex_stock_code": "00700",
        "company_name": "TENCENT",
        "company_name_en": "Tencent Holdings Limited",
        "company_name_zh": "腾讯控股有限公司",
        "exchange": "HKEX",
        "filing_id": "HK:00700:12100024",
        "report_id": "2025-annual-12100024",
        "report_type": "annual",
        "fiscal_year": 2025,
        "period_end": "2025-12-31",
        "published_at": "2026-04-09",
        "source_url": "https://www1.hkexnews.hk/test.pdf",
        "local_source_path": "raw/report.pdf",
    }

    company = importer.build_company_record(manifest)
    assert company["company_id"] == "HK:00700"
    assert company["hkex_stock_code"] == "00700"
    assert company["stock_code"] == "00700"
    assert company["exchange"] == "HKEX"
    assert "Tencent Holdings Limited" in company["aliases"]
    assert "腾讯控股有限公司" in company["aliases"]

    filing = importer.build_filing_record(manifest, tmp_path, {"overall_status": "pass"})
    assert filing["filing_id"] == "HK:00700:12100024"
    assert filing["company_id"] == "HK:00700"
    assert filing["report_id"] == "2025-annual-12100024"
    assert filing["local_path"].endswith("raw/report.pdf")


def test_hk_importer_evidence_rows_preserve_bbox_and_page_coordinates():
    importer = _load_importer()
    row = importer.build_evidence_row(
        {"evidence_id": "e1", "page_number": 25, "table_index": 6, "row_index": 3, "column_index": 2, "bbox": [1, 2, 3, 4], "quote_text": "Total assets"},
        filing_id="HK:00700:12100024",
        parse_run_id="run1",
    )
    assert row["evidence_id"] == "e1"
    assert row["page_number"] == 25
    assert row["table_index"] == 6
    assert row["row_index"] == 3
    assert row["column_index"] == 2
    assert row["bbox"] == [1, 2, 3, 4]


def test_hk_importer_statement_items_keep_source_page_and_bbox():
    importer = _load_importer()
    manifest = {"filing_id": "HK:00700:12100024", "company_id": "HK:00700", "ticker": "00700", "stock_code": "00700", "company_name": "TENCENT", "exchange": "HKEX"}
    financial_data = {
        "statements": [{
            "statement_id": "bs-1",
            "statement_type": "balance_sheet",
            "statement_name": "Statement of Financial Position",
            "items": [{
                "item_name": "Total assets",
                "canonical_name": "total_assets",
                "period_key": "2025-12-31",
                "value": "1000",
                "unit": "million",
                "currency": "HKD",
                "source": {"page_number": 25, "table_index": 6, "row_index": 10, "column_index": 2, "bbox": [1, 2, 3, 4]},
                "evidence_id": "ev-total-assets",
            }],
        }]
    }

    rows = importer.build_statement_item_rows(manifest, financial_data, {"entries": []}, "run1")

    assert len(rows) == 1
    row = rows[0]
    assert row["company_id"] == "HK:00700"
    assert row["statement_type"] == "balance_sheet"
    assert row["canonical_name"] == "total_assets"
    assert row["source_page_number"] == 25
    assert row["source_table_index"] == 6
    assert row["source_bbox"] == [1, 2, 3, 4]


def test_hk_importer_retrieval_chunks_are_agent_friendly(tmp_path):
    importer = _load_importer()
    manifest = {"filing_id": "HK:00700:12100024", "company_id": "HK:00700", "ticker": "00700", "stock_code": "00700", "report_id": "2025-annual-12100024"}
    financial_data = {"statements": [{"statement_type": "income_statement", "items": [{"canonical_name": "revenue", "item_name": "Revenue", "period_key": "2025", "raw_value": "100", "source": {"page_number": 6, "table_index": 1}}]}]}

    rows = importer.build_retrieval_chunk_rows(manifest, financial_data, {"overall_status": "pass"}, {"entries": []}, "run1", tmp_path)

    assert rows
    assert rows[0]["company_id"] == "HK:00700"
    assert rows[0]["doc_type"] == "financial_fact"
    assert rows[0]["canonical_name"] == "revenue"
    assert rows[0]["page_number"] == 6
    assert "Revenue" in rows[0]["text"]
