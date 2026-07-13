import importlib.util
from pathlib import Path

import pytest


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


def test_hk_importer_connection_kwargs_default_to_siq_hk(monkeypatch):
    importer = _load_importer()
    for name in (
        "DATABASE_URL",
        "SIQ_HK_PGDATABASE",
        "SIQ_PGDATABASE",
        "PGDATABASE",
        "SIQ_PGPASSWORD",
        "PGPASSWORD",
    ):
        monkeypatch.delenv(name, raising=False)

    assert importer.connection_kwargs()["dbname"] == "siq_hk"
    assert importer.connection_kwargs()["password"] == ""


def test_hk_importer_connection_kwargs_prefer_hk_database_env(monkeypatch):
    importer = _load_importer()
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:secret@example.invalid/unsafe")
    monkeypatch.setenv("SIQ_PGDATABASE", "siq")
    monkeypatch.setenv("PGDATABASE", "postgres")
    monkeypatch.setenv("SIQ_HK_PGDATABASE", "siq_hk_custom")

    assert importer.connection_kwargs()["dbname"] == "siq_hk_custom"
    assert "DATABASE_URL" not in importer.connection_kwargs()


def test_hk_importer_cli_rejects_database_url_credentials():
    importer = _load_importer()

    with pytest.raises(SystemExit):
        importer.build_parser().parse_args(["--database-url", "postgresql://user:secret@db/siq_hk"])


def test_hk_importer_validates_exact_connected_database():
    importer = _load_importer()

    class Result:
        @staticmethod
        def fetchone():
            return ("siq_hk_stage",)

    class Connection:
        @staticmethod
        def execute(_sql):
            return Result()

    importer.validate_connection_database(Connection(), "siq_hk_stage")
    with pytest.raises(SystemExit, match="does not match"):
        importer.validate_connection_database(Connection(), "siq_hk")


def test_hk_importer_write_requires_expected_database(tmp_path):
    importer = _load_importer()

    with pytest.raises(SystemExit, match="--expected-database is required"):
        importer.main([str(tmp_path / "package")])


def test_hk_ddl_exposes_agent_recall_columns_and_views():
    importer = _load_importer()
    ddl = importer.DDL_PATH.read_text(encoding="utf-8").lower()

    assert "alter table pdf2md_hk.filings add column if not exists report_id text" in ddl
    assert "alter table pdf2md_hk.pdf_tables add column if not exists bbox jsonb" in ddl
    assert "alter table pdf2md_hk.evidence_citations add column if not exists bbox jsonb" in ddl
    assert "alter table pdf2md_hk.retrieval_chunks add column if not exists company_id text" in ddl
    assert "alter table pdf2md_hk.retrieval_chunks add column if not exists text text" in ddl
    assert "create or replace view pdf2md_hk.v_agent_financial_facts" in ddl
    assert "create or replace view pdf2md_hk.v_latest_company_reports" in ddl
    assert "unique" in ddl and "hkex_stock_code" in ddl
    assert "create table if not exists pdf2md_hk.financial_normalization_rules" in ddl
    assert "create table if not exists pdf2md_hk.financial_items_enriched" in ddl
    assert "source_uid text not null" in ddl
    assert "unique (source_table, source_uid)" in ddl
    assert "canonical_rule_id text references pdf2md_hk.financial_normalization_rules(rule_id)" in ddl
    assert "idx_pdf2md_hk_items_enriched_lookup" in ddl


def _sample_manifest():
    return {
        "schema_version": "market_evidence_package_v1",
        "market": "HK",
        "filing_id": "HK:00700:12100024",
        "company_id": "HK:00700",
        "ticker": "700",
        "stock_code": "700",
        "hkex_stock_code": "700",
        "exchange": "HKEX",
        "company_name": "Tencent Holdings Limited",
        "company_short_name": "TENCENT",
        "company_name_zh": "腾讯控股有限公司",
        "aliases": ["腾讯", "Tencent Holdings Limited"],
        "source_id": "hkex",
        "form": "annual",
        "report_type": "annual",
        "report_id": "2025-annual-12100024",
        "fiscal_year": 2025,
        "fiscal_period": "FY",
        "period_end": "2025-12-31",
        "published_at": "2026-04-09",
        "local_source_path": "raw/report.pdf",
        "source_url": "https://www1.hkexnews.hk/test.pdf",
        "accounting_standard": "HKFRS",
        "industry_profile": "internet",
        "parser_version": "p1",
        "rules_version": "r1",
        "quality_status": "warning",
        "accession_number": "12100024",
        "artifact_hashes": {},
    }


def test_hk_importer_builds_company_filing_and_evidence_records(tmp_path):
    importer = _load_importer()
    manifest = _sample_manifest()

    company = importer.build_company_record(manifest)
    assert company["company_id"] == "HK:00700"
    assert company["hkex_stock_code"] == "00700"
    assert company["stock_code"] == "00700"
    assert company["exchange"] == "HKEX"
    assert "Tencent Holdings Limited" in company["aliases"]

    filing = importer.build_filing_record(manifest, tmp_path, {"overall_status": "pass"})
    assert filing["filing_id"] == "HK:00700:12100024"
    assert filing["report_id"] == "2025-annual-12100024"
    assert filing["quality_status"] == "pass"

    row = importer.build_evidence_row(
        {
            "evidence_id": "e1",
            "page_number": 25,
            "table_index": 6,
            "row_index": 3,
            "column_index": 2,
            "bbox": [1, 2, 3, 4],
            "quote_text": "Total assets",
        },
        filing_id="HK:00700:12100024",
        parse_run_id="run1",
    )
    assert row["bbox"] == [1, 2, 3, 4]
    assert row["page_number"] == 25
    assert row["filing_id"] == "HK:00700:12100024"
    assert row["parse_run_id"] == "run1"


def test_hk_importer_generates_stable_table_id_when_archive_index_omits_it(tmp_path):
    importer = _load_importer()
    table_dir = tmp_path / "tables"
    table_dir.mkdir()
    (table_dir / "table_index.json").write_text(
        '{"tables":[{"table_index":7,"page_number":12}]}\n',
        encoding="utf-8",
    )
    calls = []

    class Connection:
        @staticmethod
        def execute(sql, params):
            calls.append((sql, params))

    importer._insert_tables(Connection(), "pdf2md_hk", tmp_path, "HK:00700:filing", "run1")

    assert len(calls) == 1
    assert calls[0][1][2] == importer.stable_id("run1", "pdf_table", 7)


def test_hk_importer_uses_manifest_ticker_when_normalized_metric_omits_it(tmp_path):
    importer = _load_importer()
    metrics_dir = tmp_path / "metrics"
    metrics_dir.mkdir()
    (tmp_path / "manifest.json").write_text(
        '{"market":"HK","ticker":"00005"}\n',
        encoding="utf-8",
    )
    (metrics_dir / "normalized_metrics.json").write_text(
        '{"metrics":[{"statement_type":"income_statement","canonical_name":"net_interest_income","value":34794}]}\n',
        encoding="utf-8",
    )
    calls = []

    class Connection:
        @staticmethod
        def execute(sql, params):
            calls.append((sql, params))

    importer._insert_financial_facts(Connection(), "pdf2md_hk", tmp_path, "HK:00005:filing", "run1")

    assert len(calls) == 1
    assert calls[0][1][3] == "00005"


def test_hk_importer_builds_statement_item_rows_with_source_coordinates():
    importer = _load_importer()
    manifest = _sample_manifest()
    financial_data = {
        "statements": [
            {
                "statement_id": "balance_sheet",
                "statement_type": "balance_sheet",
                "statement_name": "Consolidated Statement of Financial Position",
                "scope": "consolidated",
                "items": [
                    {
                        "item_name": "Total assets",
                        "canonical_name": "total_assets",
                        "values": {
                            "2025-12-31": {
                                "value": 1000,
                                "raw_value": "1,000",
                                "unit": "HKD million",
                                "currency": "HKD",
                                "evidence_id": "ev-assets",
                            }
                        },
                    }
                ],
            }
        ]
    }
    source_map = {
        "entries": [
            {
                "evidence_id": "ev-assets",
                "page_number": 25,
                "table_index": 6,
                "row_index": 3,
                "column_index": 2,
                "bbox": [1, 2, 3, 4],
            }
        ]
    }

    rows = importer.build_statement_item_rows(manifest, financial_data, source_map, "run1")

    assert rows[0]["company_id"] == "HK:00700"
    assert rows[0]["statement_type"] == "balance_sheet"
    assert rows[0]["canonical_name"] == "total_assets"
    assert rows[0]["source_page_number"] == 25
    assert rows[0]["source_table_index"] == 6
    assert rows[0]["source_bbox"] == [1, 2, 3, 4]


def test_hk_importer_builds_retrieval_chunks_for_financial_facts(tmp_path):
    importer = _load_importer()
    manifest = _sample_manifest()
    financial_data = {
        "statements": [
            {
                "statement_id": "income_statement",
                "statement_type": "income_statement",
                "items": [
                    {
                        "item_name": "Revenue",
                        "canonical_name": "revenue",
                        "values": {
                            "2025": {
                                "value": 1200,
                                "raw_value": "1,200",
                                "unit": "HKD million",
                                "evidence_id": "ev-revenue",
                            }
                        },
                    }
                ],
            }
        ]
    }
    source_map = {"entries": [{"evidence_id": "ev-revenue", "page_number": 6, "table_index": 1}]}

    rows = importer.build_retrieval_chunk_rows(manifest, financial_data, {}, source_map, "run1", tmp_path)

    assert rows[0]["company_id"] == "HK:00700"
    assert rows[0]["doc_type"] == "financial_fact"
    assert rows[0]["canonical_name"] == "revenue"
    assert rows[0]["page_number"] == 6
    assert "Revenue" in rows[0]["text"]
