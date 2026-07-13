import importlib.util
import sys
import types
from pathlib import Path


def _load_runner():
    path = Path(__file__).resolve().parents[1] / "backtests" / "market_document_full_postgres_backtest.py"
    spec = importlib.util.spec_from_file_location("market_document_full_postgres_backtest", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeCursor:
    def __init__(self, row=None, rows=None):
        self.row = row
        self.rows = [] if rows is None else rows

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows


class FakeMarketDb:
    def __init__(self, *, counts=None, fact_rows=None, evidence_rows=None, columns=None):
        self.counts = counts or {}
        self.fact_rows = fact_rows or {}
        self.evidence_rows = evidence_rows or []
        self.columns = {
            "companies": ["company_id", "ticker"],
            "filings": ["filing_id", "company_id", "ticker", "period_end", "fiscal_year"],
            "company_filings": ["filing_id", "company_id", "ticker", "period_end", "fiscal_year"],
            "parse_runs": ["parse_run_id", "filing_id"],
            "raw_payload_refs": ["parse_run_id", "filing_id", "payload_name"],
            "document_pages": ["parse_run_id", "filing_id", "page_number"],
            "pdf_pages": ["parse_run_id", "filing_id", "page_number"],
            "filing_sections": ["parse_run_id", "filing_id", "section_id"],
            "content_blocks": ["parse_run_id", "filing_id", "block_id"],
            "artifacts": ["parse_run_id", "artifact_type"],
            "parser_artifacts": ["parse_run_id", "artifact_type"],
            "financial_statements": ["parse_run_id", "filing_id", "statement_type"],
            "financial_statement_items": [
                "parse_run_id",
                "filing_id",
                "statement_type",
                "canonical_name",
                "item_name",
                "period_key",
                "value",
                "raw_value",
                "unit",
                "currency",
                "source_page_number",
                "source_table_index",
                "evidence_id",
            ],
            "financial_facts": ["parse_run_id", "filing_id", "statement_type", "canonical_name", "period_key", "evidence_id"],
            "financial_key_metrics": ["parse_run_id", "filing_id", "canonical_name", "period_key", "evidence_id"],
            "financial_balance_sheet_items": ["parse_run_id", "filing_id", "statement_type", "canonical_name", "period_key", "evidence_id"],
            "financial_income_statement_items": ["parse_run_id", "filing_id", "statement_type", "canonical_name", "period_key", "evidence_id"],
            "financial_cash_flow_statement_items": ["parse_run_id", "filing_id", "statement_type", "canonical_name", "period_key", "evidence_id"],
            "xbrl_facts_raw": [
                "parse_run_id",
                "filing_id",
                "concept",
                "label",
                "value_text",
                "value_numeric",
                "unit",
                "context_ref",
                "period_end",
                "html_anchor",
                "xpath",
                "evidence_id",
            ],
            "document_tables": ["parse_run_id", "filing_id", "table_index"],
            "html_tables": ["parse_run_id", "filing_id", "table_index", "html_anchor"],
            "pdf_tables": ["parse_run_id", "filing_id", "table_index", "page_number"],
            "document_chunks": ["parse_run_id", "filing_id"],
            "retrieval_chunks": ["parse_run_id", "filing_id"],
            "financial_items_enriched": ["parse_run_id", "filing_id", "canonical_label"],
            "financial_checks": ["parse_run_id", "filing_id", "check_id"],
            "quality_checks": ["parse_run_id", "filing_id", "check_id"],
            "quality_reports": ["parse_run_id", "filing_id"],
            "financial_all_metrics_wide": ["parse_run_id", "filing_id", "period_key"],
            "financial_all_metrics_wide_detail": ["parse_run_id", "filing_id", "period_key"],
            "v_agent_financial_facts": [
                "company_id",
                "company_ticker",
                "filing_id",
                "report_type",
                "fiscal_year",
                "filing_period_end",
                "parse_run_id",
                "wiki_package_path",
                "statement_type",
                "canonical_name",
                "item_name",
                "period_key",
                "period_end",
                "value",
                "raw_value",
                "unit",
                "currency",
                "fact_currency",
                "reporting_currency",
                "presentation_currency",
                "converted_currency",
                "converted_value",
                "scale",
                "evidence_id",
                "evidence_page_number",
                "evidence_table_index",
                "quote_text",
                "source_url",
            ],
            "evidence_citations": [
                "evidence_id",
                "parse_run_id",
                "filing_id",
                "page_number",
                "table_index",
                "quote_text",
                "html_anchor",
                "xpath",
                "source_url",
                "local_path",
            ],
        }
        if columns:
            self.columns.update(columns)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def execute(self, sql, params=None):
        text = " ".join(str(sql).split())
        if "information_schema.tables" in text:
            return FakeCursor(row=(1,))
        if "information_schema.views" in text:
            return FakeCursor(row=(1,))
        if "information_schema.columns" in text:
            table = params[1]
            return FakeCursor(rows=[(column,) for column in self.columns.get(table, [])])
        if text.startswith("select count(*)"):
            table = text.split(" from ", 1)[1].split(" where ", 1)[0]
            table_name = table.split(".")[-1]
            if table_name == "v_agent_financial_facts" and table_name in self.fact_rows:
                return FakeCursor(row=(len(self.fact_rows.get(table_name, [])),))
            return FakeCursor(row=(self.counts.get(table, self.counts.get(table_name, 0)),))
        if text.startswith("select parse_run_id from ") and ".parse_runs " in text:
            expected = {str(value) for value in (params or ())}
            rows = [
                row
                for row in self.fact_rows.get("parse_runs", [])
                if str(row.get("parse_run_id")) in expected
            ]
            return FakeCursor(rows=[(row.get("parse_run_id"),) for row in rows])
        if text.startswith("select ") and ".evidence_citations " in text:
            selected = self._selected_columns(text)
            evidence_id = params[0] if params else None
            rows = [row for row in self.evidence_rows if row.get("evidence_id") == evidence_id]
            return FakeCursor(rows=[tuple(row.get(column) for column in selected) for row in rows])
        if text.startswith("select "):
            for table in self.columns:
                if f".{table} " in text:
                    selected = self._selected_columns(text)
                    rows = self.fact_rows.get(table, [])
                    return FakeCursor(rows=[tuple(row.get(column) for column in selected) for row in rows])
        raise AssertionError(f"unexpected SQL: {sql!r} params={params!r}")

    @staticmethod
    def _selected_columns(text):
        return [column.strip() for column in text.split("select ", 1)[1].split(" from ", 1)[0].split(",")]


def _install_fake_psycopg(monkeypatch, conn):
    monkeypatch.setitem(sys.modules, "psycopg", types.SimpleNamespace(connect=lambda _url: conn))


def _hk_counts(**overrides):
    counts = {
        "pdf2md_hk.companies": 1,
        "pdf2md_hk.filings": 1,
        "pdf2md_hk.company_filings": 0,
        "pdf2md_hk.parse_runs": 1,
        "pdf2md_hk.raw_payload_refs": 1,
        "pdf2md_hk.document_pages": 0,
        "pdf2md_hk.pdf_pages": 1,
        "pdf2md_hk.filing_sections": 0,
        "pdf2md_hk.content_blocks": 0,
        "pdf2md_hk.artifacts": 1,
        "pdf2md_hk.parser_artifacts": 0,
        "pdf2md_hk.financial_statements": 1,
        "pdf2md_hk.financial_statement_items": 2,
        "pdf2md_hk.financial_balance_sheet_items": 0,
        "pdf2md_hk.financial_income_statement_items": 0,
        "pdf2md_hk.financial_cash_flow_statement_items": 0,
        "pdf2md_hk.financial_key_metrics": 0,
        "pdf2md_hk.financial_facts": 0,
        "pdf2md_hk.xbrl_facts_raw": 0,
        "pdf2md_hk.document_tables": 1,
        "pdf2md_hk.html_tables": 0,
        "pdf2md_hk.pdf_tables": 0,
        "pdf2md_hk.document_chunks": 0,
        "pdf2md_hk.retrieval_chunks": 3,
        "pdf2md_hk.financial_items_enriched": 2,
        "pdf2md_hk.financial_checks": 0,
        "pdf2md_hk.quality_checks": 0,
        "pdf2md_hk.quality_reports": 1,
        "pdf2md_hk.financial_all_metrics_wide": 1,
        "pdf2md_hk.financial_all_metrics_wide_detail": 0,
        "pdf2md_hk.evidence_citations": 2,
    }
    counts.update(overrides)
    return counts


def _generic_market_counts(**overrides):
    counts = {
        "companies": 1,
        "filings": 1,
        "company_filings": 0,
        "parse_runs": 1,
        "raw_payload_refs": 1,
        "document_pages": 0,
        "pdf_pages": 1,
        "filing_sections": 0,
        "content_blocks": 0,
        "artifacts": 1,
        "parser_artifacts": 0,
        "financial_statements": 1,
        "financial_statement_items": 2,
        "financial_balance_sheet_items": 0,
        "financial_income_statement_items": 0,
        "financial_cash_flow_statement_items": 0,
        "financial_key_metrics": 0,
        "financial_facts": 0,
        "xbrl_facts_raw": 0,
        "document_tables": 1,
        "html_tables": 0,
        "pdf_tables": 0,
        "document_chunks": 3,
        "retrieval_chunks": 0,
        "financial_items_enriched": 2,
        "financial_checks": 0,
        "quality_checks": 0,
        "quality_reports": 1,
        "financial_all_metrics_wide": 1,
        "financial_all_metrics_wide_detail": 0,
        "evidence_citations": 2,
    }
    counts.update(overrides)
    return counts


def _hk_revenue_fact(**overrides):
    row = {
        "filing_id": "HK:FIXTURE:ROW_PERIOD:2025-annual",
        "parse_run_id": "parse-hk-idempotent",
        "statement_type": "income_statement",
        "canonical_name": "revenue",
        "item_name": "Revenues",
        "period_key": "2025-12-31",
        "value": "751766",
        "raw_value": "751,766",
        "unit": "RMB million",
        "currency": "RMB",
        "source_page_number": None,
        "source_table_index": None,
        "evidence_id": "ev-revenue",
    }
    row.update(overrides)
    return row


def _hk_agent_revenue_fact(**overrides):
    row = {
        "company_id": "HK:FIXTURE:ROW_PERIOD",
        "company_ticker": "FIXTURE_ROW_PERIOD",
        "filing_id": "HK:FIXTURE:ROW_PERIOD:2025-annual",
        "report_type": "annual",
        "fiscal_year": 2025,
        "filing_period_end": "2025-12-31",
        "parse_run_id": "parse-hk-idempotent",
        "wiki_package_path": "eval-fixtures/hk/row-period/2025-annual",
        "statement_type": "income_statement",
        "canonical_name": "revenue",
        "item_name": "Revenues",
        "period_key": "2025-12-31",
        "period_end": "2025-12-31",
        "value": "751766",
        "raw_value": "751,766",
        "unit": "RMB million",
        "currency": "RMB",
        "fact_currency": "RMB",
        "reporting_currency": "RMB",
        "presentation_currency": "RMB",
        "scale": "1000000",
        "evidence_id": "ev-revenue",
        "evidence_page_number": 42,
        "evidence_table_index": 4,
        "quote_text": "Revenues | 751,766 | 660,257",
        "source_url": "https://example.invalid/eval-fixtures/hk/row-period",
    }
    row.update(overrides)
    return row


def test_agent_view_row_diffs_classify_value_unit_and_currency():
    runner = _load_runner()

    unit_diffs = runner._agent_view_row_diffs(
        {"value": "1000", "unit": "CNY million", "currency": "RMB"},
        {"value": "1000", "unit": "RMB million", "currency": "CNY"},
    )
    assert [diff["code"] for diff in unit_diffs] == ["unit_display_diff", "currency_label_diff"]

    currency_diffs = runner._agent_view_row_diffs(
        {"value": "1000", "currency": "iso4217:USD"},
        {"value": "1000", "currency": "USD"},
    )
    assert [diff["code"] for diff in currency_diffs] == ["currency_label_diff"]

    value_diffs = runner._agent_view_row_diffs(
        {"value": "999", "unit": "CNY"},
        {"value": "1000", "unit": "CNY"},
    )
    assert [diff["code"] for diff in value_diffs] == ["value_mismatch"]


def test_parity_diff_code_counts_aggregate_questions_only():
    runner = _load_runner()
    counts = runner._diff_code_counts(
        [
            {
                "passed": False,
                "questions": [
                    {"passed": False, "diff_codes": ["wiki_missing"]},
                    {"passed": False, "diff_codes": ["value_mismatch", "unit_display_diff"]},
                ],
            },
            {
                "passed": True,
                "warnings": ["generated mismatch"],
                "questions": [
                    {"passed": False, "diff_codes": ["postgres_missing"]},
                ],
            },
        ]
    )

    assert counts == {
        "postgres_missing": 1,
        "unit_display_diff": 1,
        "value_mismatch": 1,
        "wiki_missing": 1,
    }


def test_wiki_postgres_parity_wrapper_preserves_legacy_signature(monkeypatch, tmp_path):
    runner = _load_runner()
    captured = {}

    def fake_gate(case, cases_path, **kwargs):
        captured.update(kwargs)
        return {
            "case_id": case.get("case_id"),
            "market": case.get("market"),
            "passed": True,
            "checked": 0,
            "errors": [],
            "warnings": [],
            "mode": "wiki_postgres_query_parity",
        }

    monkeypatch.setattr(runner, "_check_wiki_postgres_parity_case", fake_gate)
    result = runner.check_wiki_postgres_parity_case(
        {"case_id": "hk-wrapper", "market": "HK", "document_full_path": "document_full.json"},
        tmp_path / "cases.json",
        database_url="postgresql://example",
        db_result={"passed": True},
        generated_limit=7,
    )

    assert result["passed"] is True
    assert captured["market_schemas"] is runner.MARKET_SCHEMAS
    assert captured["database_url_for_market"] is runner.database_url_for_market
    assert captured["db_selector_for_case"] is runner.db_selector_for_case
    assert captured["document_path_for_case"] is runner.document_path_for_case
    assert captured["read_json"] is runner.read_json
    assert captured["database_url"] == "postgresql://example"
    assert captured["db_result"] == {"passed": True}
    assert captured["generated_limit"] == 7


def test_default_market_document_full_backtest_cases_pass():
    runner = _load_runner()

    summary = runner.run_cases()

    assert summary["passed"] is True
    assert summary["acceptance_passed"] is False
    assert summary["acceptance_requirements"]["fixture_contract"] is True
    assert summary["acceptance_requirements"]["postgres_import_idempotency"] is False
    assert summary["case_count"] == 7
    assert {result["market"] for result in summary["results"]} == {"CN", "HK", "JP", "KR", "EU", "US"}
    assert all(result["fact_count"] >= 1 for result in summary["results"])
    assert summary["summary"]["common_core_assertion_count"] >= 5
    assert summary["summary"]["required_evidence_assertion_count"] == 7
    assert summary["summary"]["evidence_coverage_ratio"] == 1
    assert summary["summary"]["fact_currency_checked_assertion_count"] == 1
    assert summary["summary"]["unit_currency_explainability_ratio"] == 1
    assert summary["summary"]["postgres_roundtrip_verified"] is False
    assert summary["summary"]["postgres_existing_row_check_verified"] is False
    assert summary["summary"]["postgres_import_executed"] is False
    assert summary["summary"]["postgres_idempotency_verified"] is False
    assert summary["summary"]["agent_query_verified"] is True
    assert summary["summary"]["agent_query_mode"] == "fixture_fact_lookup"
    assert summary["summary"]["agent_query_case_count"] == 7
    assert summary["summary"]["production_agent_query_verified"] is False


def test_backtest_reports_fact_mismatch(tmp_path):
    runner = _load_runner()
    source_cases = runner.read_json(runner.DEFAULT_CASES_PATH)
    cases = {
        "schema_version": source_cases["schema_version"],
        "cases": [
            {
                **source_cases["cases"][0],
                "document_full_path": "cn_period_map_document_full.json",
                "assertions": [
                    {
                        **source_cases["cases"][0]["assertions"][0],
                        "currency": "HKD",
                    }
                ],
            }
        ],
    }
    document_source = runner.DEFAULT_CASES_PATH.parent / source_cases["cases"][0]["document_full_path"]
    (tmp_path / "cases.json").write_text(runner.json.dumps(cases, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "cn_period_map_document_full.json").write_text(document_source.read_text(encoding="utf-8"), encoding="utf-8")

    summary = runner.run_cases(tmp_path / "cases.json")

    assert summary["passed"] is False
    assert "currency expected 'HKD', got 'CNY'" in summary["results"][0]["errors"][0]


def test_backtest_writes_json_and_markdown_reports(tmp_path):
    runner = _load_runner()
    output = tmp_path / "backtest_report.json"
    markdown = tmp_path / "backtest_report.md"

    exit_code = runner.main(["--output", str(output), "--markdown", str(markdown)])

    assert exit_code == 1
    payload = runner.read_json(output)
    assert payload["passed"] is True
    assert payload["acceptance_passed"] is False
    text = markdown.read_text(encoding="utf-8")
    assert "Market Document Full PostgreSQL Backtest" in text
    assert "Contract status: **PASS**" in text
    assert "Acceptance status: **PENDING**" in text
    assert "PostgreSQL roundtrip verified: False" in text
    assert "PostgreSQL import executed: False" in text
    assert "PostgreSQL idempotency verified: False" in text
    assert "Fixture Agent fact lookup verified: True" in text
    assert "Production Agent query verified: False" in text


def test_backtest_default_report_paths_are_ignored_artifacts():
    runner = _load_runner()
    repo_root = runner.REPO_ROOT

    assert runner.DEFAULT_OUTPUT_PATH == repo_root / "artifacts" / "eval-runs" / "local" / "market_document_full_postgres_backtest.json"
    assert runner.DEFAULT_MARKDOWN_PATH == repo_root / "artifacts" / "eval-runs" / "local" / "market_document_full_postgres_backtest.md"
    assert "docs/reports" not in str(runner.DEFAULT_MARKDOWN_PATH)
    assert "eval_datasets/market_document_full_postgres/backtest_report.json" not in str(runner.DEFAULT_OUTPUT_PATH)


def test_backtest_db_mode_checks_market_schema_counts(monkeypatch, tmp_path):
    runner = _load_runner()
    source_cases = runner.read_json(runner.DEFAULT_CASES_PATH)
    cases = {
        "schema_version": source_cases["schema_version"],
        "cases": [
                {
                    **source_cases["cases"][1],
                    "parse_run_id": "parse-hk-idempotent",
                    "expected_row_counts": {"facts": 2, "tables": 1, "chunks": 3, "evidence": 2},
                    "expected_family_counts": {
                    "companies": 1,
                    "filings": 1,
                    "parse_runs": 1,
                    "statements": 1,
                    "items": 2,
                    "tables": 1,
                    "chunks": 3,
                    "evidence": 2,
                },
                "expected_table_counts": {"financial_statement_items": 2, "retrieval_chunks": 3},
            }
        ],
    }
    document_source = runner.DEFAULT_CASES_PATH.parent / source_cases["cases"][1]["document_full_path"]
    document_target = tmp_path / "examples" / "hk_row_period_document_full.json"
    document_target.parent.mkdir(parents=True)
    document_target.write_text(document_source.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "cases.json").write_text(runner.json.dumps(cases, ensure_ascii=False), encoding="utf-8")

    _install_fake_psycopg(
        monkeypatch,
        FakeMarketDb(
            counts=_hk_counts(),
            fact_rows={"financial_statement_items": [_hk_revenue_fact()]},
            evidence_rows=[
                {
                    "evidence_id": "ev-revenue",
                    "page_number": 42,
                    "table_index": 4,
                    "quote_text": "Revenues | 751,766 | 660,257",
                }
            ],
        ),
    )

    summary = runner.run_cases(tmp_path / "cases.json", verify_db=True, database_url="postgresql://example")

    assert summary["passed"] is True
    assert summary["summary"]["postgres_existing_row_check_verified"] is True
    assert summary["summary"]["postgres_roundtrip_verified"] is False
    assert summary["summary"]["postgres_import_executed"] is False
    assert summary["summary"]["postgres_roundtrip_case_count"] == 1
    assert summary["db_results"][0]["counts"]["facts"] == 2
    assert summary["db_results"][0]["counts"]["items"] == 2
    assert summary["db_results"][0]["table_counts"]["financial_statement_items"] == 2
    assert summary["db_results"][0]["required_evidence_checks"][0]["mode"] == "evidence_id_join"
    assert summary["summary"]["postgres_required_evidence_checked_count"] == 1
    assert summary["summary"]["postgres_required_evidence_passed_count"] == 1


def test_backtest_db_mode_fails_missing_required_counts_without_idempotency(monkeypatch, tmp_path):
    runner = _load_runner()
    source_cases = runner.read_json(runner.DEFAULT_CASES_PATH)
    cases = {
        "schema_version": source_cases["schema_version"],
        "cases": [
            {
                **source_cases["cases"][1],
                "expected_row_counts": {"parse_runs": 1, "facts": 1, "tables": 1, "chunks": 1, "evidence": 1},
            }
        ],
    }
    document_source = runner.DEFAULT_CASES_PATH.parent / source_cases["cases"][1]["document_full_path"]
    document_target = tmp_path / "examples" / "hk_row_period_document_full.json"
    document_target.parent.mkdir(parents=True)
    document_target.write_text(document_source.read_text(encoding="utf-8"), encoding="utf-8")
    cases["cases"][0]["document_full_path"] = "examples/hk_row_period_document_full.json"
    (tmp_path / "cases.json").write_text(runner.json.dumps(cases, ensure_ascii=False), encoding="utf-8")

    _install_fake_psycopg(monkeypatch, FakeMarketDb(counts=_hk_counts(**{
        "pdf2md_hk.financial_statement_items": 0,
        "pdf2md_hk.financial_statements": 0,
        "pdf2md_hk.document_tables": 0,
        "pdf2md_hk.retrieval_chunks": 0,
        "pdf2md_hk.evidence_citations": 0,
    })))

    summary = runner.run_cases(tmp_path / "cases.json", verify_db=True, database_url="postgresql://postgres:secret@db/not_the_market_db")

    assert summary["passed"] is False
    errors = summary["db_results"][0]["errors"]
    assert "financial facts missing" in errors
    assert "document tables missing" in errors
    assert "retrieval chunks missing" in errors
    assert "evidence citations missing" in errors


def test_backtest_db_mode_fails_required_evidence_without_reviewable_location(monkeypatch, tmp_path):
    runner = _load_runner()
    source_cases = runner.read_json(runner.DEFAULT_CASES_PATH)
    cases = {
        "schema_version": source_cases["schema_version"],
        "cases": [
            {
                **source_cases["cases"][1],
                "expected_family_counts": {"items": 2, "evidence": 2},
                "expected_table_counts": {"financial_statement_items": 2},
            }
        ],
    }
    document_source = runner.DEFAULT_CASES_PATH.parent / source_cases["cases"][1]["document_full_path"]
    document_target = tmp_path / "examples" / "hk_row_period_document_full.json"
    document_target.parent.mkdir(parents=True)
    document_target.write_text(document_source.read_text(encoding="utf-8"), encoding="utf-8")
    cases["cases"][0]["document_full_path"] = "examples/hk_row_period_document_full.json"
    (tmp_path / "cases.json").write_text(runner.json.dumps(cases, ensure_ascii=False), encoding="utf-8")

    _install_fake_psycopg(
        monkeypatch,
        FakeMarketDb(
            counts=_hk_counts(),
            fact_rows={"financial_statement_items": [_hk_revenue_fact(evidence_id="ev-empty")]},
            evidence_rows=[{"evidence_id": "ev-empty"}],
        ),
    )

    summary = runner.run_cases(tmp_path / "cases.json", verify_db=True, database_url="postgresql://example")

    assert summary["passed"] is False
    assert "revenue: required evidence is not reviewable in DB" in summary["db_results"][0]["errors"]
    assert summary["db_results"][0]["required_evidence_checks"][0]["inspected_rows"] == 1


def test_backtest_database_url_rewrites_to_market_database():
    runner = _load_runner()

    assert runner.database_url_for_market("HK", "postgresql://postgres:secret@db/custom") == "postgresql://postgres:secret@db/siq_hk"
    assert runner.database_url_for_market("US", "postgresql://postgres:secret@db/custom") == "postgresql://postgres:secret@db/siq_us"


def test_backtest_database_url_matches_importer_generic_database_url_policy(monkeypatch):
    runner = _load_runner()
    for key in ("SIQ_PGHOST", "PGHOST", "SIQ_PGPORT", "PGPORT", "SIQ_PGUSER", "PGUSER", "SIQ_PGPASSWORD", "PGPASSWORD"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres:secret@generic-host:9999/custom")
    monkeypatch.delenv("SIQ_ALLOW_GENERIC_MARKET_DATABASE_URL", raising=False)

    default_url = runner.database_url_for_market("HK")
    assert default_url == "postgresql://postgres@127.0.0.1:15432/siq_hk"

    monkeypatch.setenv("SIQ_ALLOW_GENERIC_MARKET_DATABASE_URL", "1")
    allowed_url = runner.database_url_for_market("HK")
    assert allowed_url == "postgresql://postgres:secret@generic-host:9999/siq_hk"


def test_backtest_db_import_mode_checks_idempotent_row_counts(monkeypatch, tmp_path):
    runner = _load_runner()
    source_cases = runner.read_json(runner.DEFAULT_CASES_PATH)
    cases = {
        "schema_version": source_cases["schema_version"],
        "cases": [{**source_cases["cases"][1], "expected_row_counts": {"facts": 2, "tables": 1, "chunks": 3, "evidence": 2}}],
    }
    document_source = runner.DEFAULT_CASES_PATH.parent / source_cases["cases"][1]["document_full_path"]
    document_target = tmp_path / "examples" / "hk_row_period_document_full.json"
    document_target.parent.mkdir(parents=True)
    document_target.write_text(document_source.read_text(encoding="utf-8"), encoding="utf-8")
    cases["cases"][0]["document_full_path"] = "examples/hk_row_period_document_full.json"
    (tmp_path / "cases.json").write_text(runner.json.dumps(cases, ensure_ascii=False), encoding="utf-8")

    import_calls = []

    def fake_import(case, *, cases_path, database_url=None, run_ddl=True):
        import_calls.append({"case": case["case_id"], "path": runner.document_path_for_case(case, cases_path), "database_url": database_url, "run_ddl": run_ddl})
        return "parse-hk-idempotent"

    monkeypatch.setattr(runner, "_import_case_document_full", fake_import)
    _install_fake_psycopg(
        monkeypatch,
        FakeMarketDb(
            counts=_hk_counts(**{"pdf2md_hk.document_chunks": 3, "pdf2md_hk.retrieval_chunks": 0}),
            fact_rows={
                "financial_statement_items": [_hk_revenue_fact(source_page_number=42, source_table_index=4)],
                "v_agent_financial_facts": [_hk_agent_revenue_fact()],
            },
        ),
    )

    summary = runner.run_cases(
        tmp_path / "cases.json",
        verify_db=True,
        database_url="postgresql://example",
        import_before_db_check=True,
        idempotency=True,
    )

    assert summary["passed"] is True
    assert summary["acceptance_passed"] is False
    assert summary["mode"] == "document_full_fixture_contract+postgres_import_idempotency"
    assert summary["summary"]["postgres_import_executed"] is True
    assert summary["summary"]["postgres_idempotency_verified"] is True
    assert summary["acceptance_requirements"]["postgres_import_idempotency"] is True
    assert summary["acceptance_requirements"]["production_agent_query"] is False
    assert summary["summary"]["wiki_postgres_query_parity_verified"] is True
    assert summary["summary"]["wiki_postgres_query_parity_diff_code_counts"] == {}
    assert summary["wiki_postgres_parity_results"][0]["diff_code_counts"] == {}
    assert [call["run_ddl"] for call in import_calls] == [True, False]
    assert summary["db_results"][0]["counts"] == summary["db_results"][0]["second_counts"]
    assert summary["db_results"][0]["table_counts"] == summary["db_results"][0]["second_table_counts"]
    assert summary["db_results"][0]["content_hashes"] == summary["db_results"][0]["second_content_hashes"]
    assert summary["db_results"][0]["required_evidence_checks"][0]["mode"] == "fact_location_fields"


def test_backtest_db_import_mode_can_verify_production_agent_view(monkeypatch, tmp_path):
    runner = _load_runner()
    source_cases = runner.read_json(runner.DEFAULT_CASES_PATH)
    cases = {
        "schema_version": source_cases["schema_version"],
        "cases": [{**source_cases["cases"][1], "expected_row_counts": {"facts": 2, "tables": 1, "chunks": 3, "evidence": 2}}],
    }
    document_source = runner.DEFAULT_CASES_PATH.parent / source_cases["cases"][1]["document_full_path"]
    document_target = tmp_path / "examples" / "hk_row_period_document_full.json"
    document_target.parent.mkdir(parents=True)
    document_target.write_text(document_source.read_text(encoding="utf-8"), encoding="utf-8")
    cases["cases"][0]["document_full_path"] = "examples/hk_row_period_document_full.json"
    (tmp_path / "cases.json").write_text(runner.json.dumps(cases, ensure_ascii=False), encoding="utf-8")

    def fake_import(_case, *, cases_path, database_url=None, run_ddl=True):
        return "parse-hk-idempotent"

    monkeypatch.setattr(runner, "_import_case_document_full", fake_import)
    _install_fake_psycopg(
        monkeypatch,
        FakeMarketDb(
            counts=_hk_counts(**{"pdf2md_hk.document_chunks": 3, "pdf2md_hk.retrieval_chunks": 0}),
            fact_rows={
                "financial_statement_items": [_hk_revenue_fact(source_page_number=42, source_table_index=4)],
                "v_agent_financial_facts": [_hk_agent_revenue_fact()],
            },
        ),
    )

    summary = runner.run_cases(
        tmp_path / "cases.json",
        verify_db=True,
        database_url="postgresql://example",
        import_before_db_check=True,
        idempotency=True,
        production_agent_query=True,
    )

    assert summary["passed"] is True
    assert summary["acceptance_passed"] is False
    assert summary["acceptance_requirements"]["real_sample_minimum"] is True
    assert summary["acceptance_requirements"]["real_sample_postgres_roundtrip"] is False
    assert summary["acceptance_requirements"]["production_agent_query"] is True
    assert summary["summary"]["production_agent_query_executed"] is True
    assert summary["summary"]["production_agent_query_verified"] is True
    assert summary["summary"]["production_agent_query_passed_count"] == 1
    assert summary["production_agent_results"][0]["mode"] == "postgres_agent_view"
    assert summary["production_agent_results"][0]["questions"][0]["matched_fact"]["canonical_name"] == "revenue"


def test_backtest_production_agent_view_fails_when_expected_columns_are_missing(monkeypatch, tmp_path):
    runner = _load_runner()
    source_cases = runner.read_json(runner.DEFAULT_CASES_PATH)
    cases = {
        "schema_version": source_cases["schema_version"],
        "cases": [{**source_cases["cases"][1], "expected_row_counts": {"facts": 2, "tables": 1, "chunks": 3, "evidence": 2}}],
    }
    document_source = runner.DEFAULT_CASES_PATH.parent / source_cases["cases"][1]["document_full_path"]
    document_target = tmp_path / "examples" / "hk_row_period_document_full.json"
    document_target.parent.mkdir(parents=True)
    document_target.write_text(document_source.read_text(encoding="utf-8"), encoding="utf-8")
    cases["cases"][0]["document_full_path"] = "examples/hk_row_period_document_full.json"
    (tmp_path / "cases.json").write_text(runner.json.dumps(cases, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(
        runner,
        "_import_case_document_full",
        lambda _case, *, cases_path, database_url=None, run_ddl=True: "parse-hk-idempotent",
    )
    columns = {
        "v_agent_financial_facts": [
            column
            for column in FakeMarketDb().columns["v_agent_financial_facts"]
            if column != "unit"
        ]
    }
    _install_fake_psycopg(
        monkeypatch,
        FakeMarketDb(
            counts=_hk_counts(**{"pdf2md_hk.document_chunks": 3, "pdf2md_hk.retrieval_chunks": 0}),
            fact_rows={
                "financial_statement_items": [_hk_revenue_fact(source_page_number=42, source_table_index=4)],
                "v_agent_financial_facts": [_hk_agent_revenue_fact()],
            },
            columns=columns,
        ),
    )

    summary = runner.run_cases(
        tmp_path / "cases.json",
        verify_db=True,
        database_url="postgresql://example",
        import_before_db_check=True,
        idempotency=True,
        production_agent_query=True,
    )

    assert summary["acceptance_requirements"]["production_agent_query"] is False
    assert "missing expected columns: unit" in summary["production_agent_results"][0]["questions"][0]["reason"]


def test_production_sample_manifest_can_validate_structure_without_local_files(tmp_path):
    runner = _load_runner()
    manifest_path = tmp_path / "production_sample_manifest.json"
    manifest_path.write_text(
        runner.json.dumps(
            {
                "schema_version": "market_document_full_production_sample_manifest_v1",
                "sample_goal_per_market": 3,
                "markets": {
                    market: [
                        f"data/not-tracked/{market.lower()}/sample_{index}/document_full.json"
                        for index in (1, 2, 3)
                    ]
                    for market in runner.MARKET_DATABASES
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    structure_only = runner.validate_production_sample_manifest(manifest_path, require_existing=False)
    strict = runner.validate_production_sample_manifest(manifest_path)

    assert structure_only["passed"] is True
    assert structure_only["require_existing"] is False
    assert structure_only["missing"] == {}
    assert all(count == 3 for count in structure_only["market_counts"].values())
    assert all(count == 0 for count in structure_only["existing_counts"].values())
    assert {sample["exists"] for sample in structure_only["samples"]} == {None}
    assert runner.production_sample_cases_from_manifest(structure_only) == []
    assert strict["passed"] is False
    assert "HK" in strict["missing"]


def test_production_sample_manifest_rejects_unknown_schema_version(tmp_path):
    runner = _load_runner()
    manifest_path = tmp_path / "production_sample_manifest.json"
    manifest_path.write_text(
        runner.json.dumps(
            {
                "schema_version": "not_the_market_document_full_manifest",
                "sample_goal_per_market": 3,
                "markets": {market: [] for market in runner.MARKET_DATABASES},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = runner.validate_production_sample_manifest(manifest_path, require_existing=False)

    assert result["passed"] is False
    assert result["sample_goal_per_market"] == 0
    assert "schema_version must be" in result["reason"]
    assert result["missing"]["__manifest__"] == ["schema_version='not_the_market_document_full_manifest'"]


def test_backtest_production_sample_db_gate_verifies_manifest_idempotency(monkeypatch, tmp_path):
    runner = _load_runner()
    source_cases = runner.read_json(runner.DEFAULT_CASES_PATH)
    cases = {
        "schema_version": source_cases["schema_version"],
        "cases": [{**source_cases["cases"][1], "expected_row_counts": {"facts": 2, "tables": 1, "chunks": 3, "evidence": 2}}],
    }
    document_source = runner.DEFAULT_CASES_PATH.parent / source_cases["cases"][1]["document_full_path"]
    document_target = tmp_path / "examples" / "hk_row_period_document_full.json"
    document_target.parent.mkdir(parents=True)
    document_target.write_text(document_source.read_text(encoding="utf-8"), encoding="utf-8")
    cases["cases"][0]["document_full_path"] = "examples/hk_row_period_document_full.json"
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(runner.json.dumps(cases, ensure_ascii=False), encoding="utf-8")

    manifest_markets = {}
    for market in ("HK", "JP", "KR", "EU", "US"):
        manifest_markets[market] = []
        for index in (1, 2):
            sample_path = tmp_path / "samples" / market.lower() / f"sample_{index}" / "document_full.json"
            sample_path.parent.mkdir(parents=True)
            sample_path.write_text("{}", encoding="utf-8")
            manifest_markets[market].append(str(sample_path))
    manifest_path = tmp_path / "production_sample_manifest.json"
    manifest_path.write_text(
        runner.json.dumps(
            {
                "schema_version": "market_document_full_production_sample_manifest_v1",
                "sample_goal_per_market": 2,
                "markets": manifest_markets,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    import_calls = []

    def fake_import(case, *, cases_path, database_url=None, run_ddl=True):
        import_calls.append({"case_id": case["case_id"], "market": case["market"], "run_ddl": run_ddl})
        return f"parse-{case['case_id']}"

    monkeypatch.setattr(runner, "_import_case_document_full", fake_import)
    _install_fake_psycopg(
        monkeypatch,
        FakeMarketDb(
            counts=_generic_market_counts(),
            fact_rows={
                "parse_runs": [
                    {"parse_run_id": f"parse-production_sample_{market.lower()}_{index:02d}"}
                    for market in ("HK", "JP", "KR", "EU", "US")
                    for index in (1, 2)
                ],
                "financial_statement_items": [_hk_revenue_fact(source_page_number=42, source_table_index=4)],
                "v_agent_financial_facts": [_hk_agent_revenue_fact()],
            },
        ),
    )

    summary = runner.run_cases(
        cases_path,
        verify_db=True,
        database_url="postgresql://example",
        import_before_db_check=True,
        idempotency=True,
        production_sample_manifest_path=manifest_path,
        production_sample_db=True,
    )

    assert summary["passed"] is True
    assert summary["summary"]["production_sample_db_executed"] is True
    assert summary["summary"]["production_sample_db_verified"] is True
    assert summary["summary"]["production_sample_db_passed_count"] == 10
    assert summary["summary"]["production_sample_db_coexistence_verified"] is True
    assert summary["summary"]["production_sample_db_coexistence_passed_count"] == 5
    assert summary["acceptance_requirements"]["real_sample_minimum"] is True
    assert summary["acceptance_requirements"]["real_sample_postgres_roundtrip"] is True
    assert summary["acceptance_requirements"]["wiki_postgres_query_parity"] is True
    assert len(summary["production_sample_db_results"]) == 10
    assert len(import_calls) == 22
    assert [call["run_ddl"] for call in import_calls[:2]] == [True, False]
    production_calls = import_calls[2:]
    for offset in range(0, len(production_calls), 4):
        assert [call["run_ddl"] for call in production_calls[offset : offset + 4]] == [True, False, False, False]


def test_backtest_production_agent_query_probes_real_samples(monkeypatch, tmp_path):
    runner = _load_runner()
    source_cases = runner.read_json(runner.DEFAULT_CASES_PATH)
    cases = {
        "schema_version": source_cases["schema_version"],
        "cases": [{**source_cases["cases"][1], "expected_row_counts": {"facts": 2, "tables": 1, "chunks": 3, "evidence": 2}}],
    }
    document_source = runner.DEFAULT_CASES_PATH.parent / source_cases["cases"][1]["document_full_path"]
    document_target = tmp_path / "examples" / "hk_row_period_document_full.json"
    document_target.parent.mkdir(parents=True)
    document_target.write_text(document_source.read_text(encoding="utf-8"), encoding="utf-8")
    cases["cases"][0]["document_full_path"] = "examples/hk_row_period_document_full.json"
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(runner.json.dumps(cases, ensure_ascii=False), encoding="utf-8")

    manifest_markets = {}
    for market in ("HK", "JP", "KR", "EU", "US"):
        sample_path = tmp_path / "samples" / market.lower() / "document_full.json"
        sample_path.parent.mkdir(parents=True)
        sample_path.write_text("{}", encoding="utf-8")
        manifest_markets[market] = [str(sample_path)]
    manifest_path = tmp_path / "production_sample_manifest.json"
    manifest_path.write_text(
        runner.json.dumps(
            {
                "schema_version": "market_document_full_production_sample_manifest_v1",
                "sample_goal_per_market": 1,
                "markets": manifest_markets,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    def fake_import(case, *, cases_path, database_url=None, run_ddl=True):
        return "parse-hk-idempotent" if str(case["market"]).upper() == "HK" else f"parse-{case['market'].lower()}"

    monkeypatch.setattr(runner, "_import_case_document_full", fake_import)
    _install_fake_psycopg(
        monkeypatch,
        FakeMarketDb(
            counts=_generic_market_counts(**{"v_agent_financial_facts": 1}),
            fact_rows={
                "parse_runs": [
                    {"parse_run_id": f"parse-{market.lower()}"}
                    for market in ("JP", "KR", "EU", "US")
                ] + [{"parse_run_id": "parse-hk-idempotent"}],
                "financial_statement_items": [_hk_revenue_fact(source_page_number=42, source_table_index=4)],
                "v_agent_financial_facts": [_hk_agent_revenue_fact()],
            },
        ),
    )

    summary = runner.run_cases(
        cases_path,
        verify_db=True,
        database_url="postgresql://example",
        import_before_db_check=True,
        idempotency=True,
        production_sample_manifest_path=manifest_path,
        production_sample_db=True,
        production_agent_query=True,
    )

    assert summary["passed"] is True
    assert summary["acceptance_requirements"]["production_agent_query"] is True
    assert summary["acceptance_requirements"]["real_sample_agent_view_query"] is True
    assert summary["summary"]["production_sample_agent_view_verified"] is True
    assert summary["summary"]["production_sample_agent_view_passed_count"] == 5
    assert summary["summary"]["production_agent_query_case_count"] == 6
    assert len(summary["production_sample_agent_results"]) == 5
    assert all(result["mode"] == "production_sample_agent_view_probe" for result in summary["production_sample_agent_results"])


def test_release_mode_never_sends_fixture_cases_to_postgres(monkeypatch):
    runner = _load_runner()
    production_cases = [
        {
            "case_id": f"production_sample_{market.lower()}_01",
            "market": market,
            "document_full_path": f"/external/{market.lower()}/document_full.json",
        }
        for market in ("HK", "JP", "KR", "EU", "US")
    ]
    db_sequences = []
    parity_cases = []

    monkeypatch.setattr(
        runner,
        "validate_production_sample_manifest",
        lambda *_args, **_kwargs: {
            "passed": True,
            "path": "/external/production_sample_manifest.json",
            "require_existing": True,
            "sample_goal_per_market": 1,
            "market_counts": {market: 1 for market in ("HK", "JP", "KR", "EU", "US")},
            "existing_counts": {market: 1 for market in ("HK", "JP", "KR", "EU", "US")},
            "missing": {},
            "samples": [],
        },
    )
    monkeypatch.setattr(
        runner,
        "production_sample_cases_from_manifest",
        lambda _manifest: production_cases,
    )

    def fake_db_sequence(cases, **_kwargs):
        db_sequences.append(list(cases))
        return [
            {
                "case_id": case["case_id"],
                "market": case["market"],
                "passed": True,
                "skipped": False,
                "parse_run_id": f"parse-{case['case_id']}",
                "counts": {family: 1 for family in runner.DB_DEFAULT_REQUIRED_FAMILIES},
                "scope_issues": [],
                "required_evidence_checks": [],
                "imported_before_check": True,
                "idempotency_checked": True,
            }
            for case in cases
        ]

    monkeypatch.setattr(runner, "check_db_case_sequence", fake_db_sequence)
    monkeypatch.setattr(
        runner,
        "check_production_sample_db_coexistence",
        lambda _results, **_kwargs: [
            {"market": market, "passed": True} for market in ("HK", "JP", "KR", "EU", "US")
        ],
    )
    monkeypatch.setattr(
        runner,
        "check_production_agent_case",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("fixture Agent query reached PostgreSQL")
        ),
    )
    monkeypatch.setattr(
        runner,
        "check_production_sample_agent_view_case",
        lambda case, **_kwargs: {
            "case_id": case["case_id"],
            "market": case["market"],
            "passed": True,
            "skipped": False,
        },
    )

    def fake_parity(case, *_args, **_kwargs):
        parity_cases.append(case["case_id"])
        return {
            "case_id": case["case_id"],
            "market": case["market"],
            "passed": True,
            "skipped": False,
        }

    monkeypatch.setattr(runner, "check_wiki_postgres_parity_case", fake_parity)

    summary = runner.run_cases(
        verify_db=True,
        import_before_db_check=True,
        idempotency=True,
        production_sample_db=True,
        production_agent_query=True,
        fixture_postgres=False,
    )

    assert len(db_sequences) == 1
    assert [case["case_id"] for case in db_sequences[0]] == [
        case["case_id"] for case in production_cases
    ]
    assert parity_cases == [case["case_id"] for case in production_cases]
    assert summary["db_results"] == []
    assert summary["fixture_production_agent_results"] == []
    assert summary["wiki_postgres_parity_results"] == []
    assert summary["summary"]["fixture_postgres_policy"] == "prohibited"
    assert summary["summary"]["fixture_postgres_access_executed"] is False
    assert summary["summary"]["fixture_postgres_import_executed"] is False
    assert summary["summary"]["production_sample_idempotency_verified"] is True
    assert summary["acceptance_requirements"]["real_sample_postgres_idempotency"] is True
    assert summary["acceptance_requirements"]["fixture_postgres_write_prohibited"] is True
    assert summary["acceptance_passed"] is True


def test_backtest_rejects_idempotency_without_import_before_db_check():
    runner = _load_runner()

    try:
        runner.run_cases(verify_db=True, idempotency=True)
    except ValueError as exc:
        assert "--idempotency requires --import-before-db-check" in str(exc)
    else:
        raise AssertionError("expected idempotency dependency error")


def test_backtest_cli_import_mode_disables_fixture_postgres(monkeypatch, capsys):
    runner = _load_runner()
    captured = {}

    def fake_run_cases(*_args, **kwargs):
        captured.update(kwargs)
        return {
            "passed": True,
            "acceptance_passed": True,
            "passed_count": 0,
            "case_count": 0,
            "results": [],
        }

    monkeypatch.setattr(runner, "run_cases", fake_run_cases)

    exit_code = runner.main(
        [
            "--no-write",
            "--db",
            "--import-before-db-check",
            "--idempotency",
            "--production-sample-db",
            "--json",
        ]
    )

    assert exit_code == 0
    assert captured["fixture_postgres"] is False
    assert captured["production_sample_db"] is True
    assert capsys.readouterr().out


def test_backtest_rejects_production_agent_query_without_db():
    runner = _load_runner()

    try:
        runner.run_cases(production_agent_query=True)
    except ValueError as exc:
        assert "--production-agent-query requires --db" in str(exc)
    else:
        raise AssertionError("expected production Agent query dependency error")


def test_backtest_rejects_production_sample_db_without_idempotency():
    runner = _load_runner()

    try:
        runner.run_cases(verify_db=True, import_before_db_check=True, production_sample_db=True)
    except ValueError as exc:
        assert "--production-sample-db requires --idempotency" in str(exc)
    else:
        raise AssertionError("expected production sample DB dependency error")
