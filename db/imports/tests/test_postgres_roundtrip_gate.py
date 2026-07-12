import importlib.util
import sys
import types
from pathlib import Path


def _load_module():
    backtest_dir = Path(__file__).resolve().parents[1] / "backtests"
    if str(backtest_dir) not in sys.path:
        sys.path.insert(0, str(backtest_dir))
    source = backtest_dir / "postgres_roundtrip_gate.py"
    spec = importlib.util.spec_from_file_location("postgres_roundtrip_gate_under_test", source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
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


class FakeDb:
    def __init__(self, *, counts=None, rows=None, evidence_rows=None):
        self.counts = counts or {}
        self.rows = rows or {}
        self.evidence_rows = evidence_rows or []
        self.columns = {
            "companies": ["company_id"],
            "filings": ["filing_id"],
            "company_filings": ["filing_id"],
            "parse_runs": ["parse_run_id", "filing_id"],
            "raw_payload_refs": ["parse_run_id", "filing_id"],
            "document_pages": ["parse_run_id", "filing_id"],
            "pdf_pages": ["parse_run_id", "filing_id"],
            "filing_sections": ["parse_run_id", "filing_id"],
            "content_blocks": ["parse_run_id", "filing_id"],
            "artifacts": ["parse_run_id"],
            "parser_artifacts": ["parse_run_id"],
            "financial_statements": ["parse_run_id", "filing_id"],
            "financial_statement_items": [
                "parse_run_id",
                "filing_id",
                "statement_type",
                "canonical_name",
                "period_key",
                "value",
                "raw_value",
                "unit",
                "currency",
                "source_page_number",
                "source_table_index",
                "evidence_id",
            ],
            "financial_balance_sheet_items": ["parse_run_id", "filing_id"],
            "financial_income_statement_items": ["parse_run_id", "filing_id"],
            "financial_cash_flow_statement_items": ["parse_run_id", "filing_id"],
            "financial_key_metrics": ["parse_run_id", "filing_id"],
            "financial_facts": ["parse_run_id", "filing_id"],
            "xbrl_facts_raw": ["parse_run_id", "filing_id"],
            "document_tables": ["parse_run_id", "filing_id", "table_index"],
            "html_tables": ["parse_run_id", "filing_id"],
            "pdf_tables": ["parse_run_id", "filing_id"],
            "document_chunks": ["parse_run_id", "filing_id", "chunk_uid"],
            "retrieval_chunks": ["parse_run_id", "filing_id"],
            "financial_items_enriched": ["parse_run_id", "filing_id"],
            "financial_checks": ["parse_run_id", "filing_id"],
            "quality_checks": ["parse_run_id", "filing_id"],
            "quality_reports": ["parse_run_id", "filing_id"],
            "financial_all_metrics_wide": ["parse_run_id", "filing_id"],
            "financial_all_metrics_wide_detail": ["parse_run_id", "filing_id"],
            "evidence_citations": [
                "evidence_id",
                "parse_run_id",
                "filing_id",
                "page_number",
                "table_index",
                "quote_text",
                "source_url",
            ],
        }

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
            return FakeCursor(rows=[(column,) for column in self.columns.get(params[1], [])])
        if text.startswith("select count(*)"):
            table = text.split(" from ", 1)[1].split(" where ", 1)[0]
            table_name = table.split(".")[-1]
            return FakeCursor(row=(self.counts.get(table, self.counts.get(table_name, 0)),))
        if text.startswith("select ") and ".evidence_citations " in text:
            selected = self._selected_columns(text)
            evidence_id = params[0] if params else None
            rows = [row for row in self.evidence_rows if row.get("evidence_id") == evidence_id]
            if not rows and "evidence_id = %s" not in text:
                rows = self.rows.get("evidence_citations", [])
            return FakeCursor(rows=[tuple(row.get(column) for column in selected) for row in rows])
        if text.startswith("select "):
            for table_name in self.columns:
                if f".{table_name} " in text:
                    selected = self._selected_columns(text)
                    rows = self.rows.get(table_name, [])
                    return FakeCursor(rows=[tuple(row.get(column) for column in selected) for row in rows])
        raise AssertionError(f"unexpected SQL: {sql!r} params={params!r}")

    @staticmethod
    def _selected_columns(text):
        return [column.strip() for column in text.split("select ", 1)[1].split(" from ", 1)[0].split(",")]


def _install_fake_psycopg(monkeypatch, conn):
    monkeypatch.setitem(sys.modules, "psycopg", types.SimpleNamespace(connect=lambda _url: conn))


def _counts():
    return {
        "companies": 1,
        "filings": 1,
        "parse_runs": 1,
        "raw_payload_refs": 1,
        "financial_statement_items": 2,
        "document_tables": 1,
        "document_chunks": 3,
        "evidence_citations": 2,
        "artifacts": 1,
        "financial_items_enriched": 1,
        "quality_reports": 1,
        "financial_all_metrics_wide": 1,
    }


def _fact():
    return {
        "parse_run_id": "parse-hk-1",
        "filing_id": "filing-hk-1",
        "statement_type": "income_statement",
        "canonical_name": "revenue",
        "period_key": "FY2025",
        "value": "100",
        "raw_value": "100",
        "evidence_id": "ev-1",
    }


def test_check_db_case_import_idempotency_uses_injected_importer(monkeypatch, tmp_path):
    module = _load_module()
    calls = []

    def importer(case, *, cases_path, database_url=None, run_ddl=True):
        calls.append((case["case_id"], run_ddl, cases_path, database_url))
        return "parse-hk-1"

    _install_fake_psycopg(
        monkeypatch,
        FakeDb(
            counts=_counts(),
            rows={
                "financial_statement_items": [_fact()],
                "document_chunks": [{"parse_run_id": "parse-hk-1", "chunk_uid": "chunk-1"}],
                "document_tables": [{"parse_run_id": "parse-hk-1", "table_index": 7}],
            },
            evidence_rows=[{"evidence_id": "ev-1", "parse_run_id": "parse-hk-1", "page_number": 3}],
        ),
    )

    result = module.check_db_case(
        {
            "case_id": "hk-case",
            "market": "HK",
            "document_full_path": "doc.json",
            "assertions": [
                {
                    "statement_type": "income_statement",
                    "canonical_name": "revenue",
                    "period_key": "FY2025",
                    "required_evidence": True,
                }
            ],
        },
        cases_path=tmp_path / "cases.json",
        market_schemas={"HK": "pdf2md_hk"},
        database_url_for_market=lambda market, url: f"{url}/{market.lower()}",
        database_url="postgresql://example",
        import_before_check=True,
        idempotency=True,
        import_case_document_full=importer,
    )

    assert result["passed"] is True
    assert [call[1] for call in calls] == [True, False]
    assert result["parse_run_id"] == "parse-hk-1"
    assert result["idempotency_checked"] is True
    assert result["required_evidence_checks"][0]["passed"] is True


def test_check_db_case_sequence_runs_ddl_once_per_market(monkeypatch, tmp_path):
    module = _load_module()
    calls = []

    def importer(case, *, cases_path, database_url=None, run_ddl=True):
        calls.append((case["case_id"], case["market"], run_ddl))
        return f"parse-{case['case_id']}"

    _install_fake_psycopg(monkeypatch, FakeDb(counts=_counts(), rows={"financial_statement_items": [_fact()]}))

    results = module.check_db_case_sequence(
        [
            {"case_id": "hk-1", "market": "HK", "document_full_path": "hk1.json"},
            {"case_id": "hk-2", "market": "HK", "document_full_path": "hk2.json"},
            {"case_id": "us-1", "market": "US", "document_full_path": "us1.json"},
        ],
        cases_path=tmp_path / "cases.json",
        market_schemas={"HK": "pdf2md_hk", "US": "sec_us"},
        database_url_for_market=lambda market, url: f"{url}/{market.lower()}",
        database_url="postgresql://example",
        import_before_check=True,
        idempotency=False,
        import_case_document_full=importer,
    )

    assert [call[2] for call in calls] == [True, False, True]
    assert [result["check_type"] for result in results] == ["import_roundtrip", "import_roundtrip", "import_roundtrip"]


def test_check_db_case_fails_when_count_table_cannot_be_scoped(monkeypatch, tmp_path):
    module = _load_module()
    conn = FakeDb(counts={**_counts(), "financial_statement_items": 99})
    conn.columns["financial_statement_items"] = ["source_system", "canonical_name", "value"]
    _install_fake_psycopg(monkeypatch, conn)

    result = module.check_db_case(
        {
            "case_id": "hk-scope-drift",
            "market": "HK",
            "parse_run_id": "parse-hk-1",
            "expected_table_counts": {"financial_statement_items": {"min": 1}},
        },
        cases_path=tmp_path / "cases.json",
        market_schemas={"HK": "pdf2md_hk"},
        database_url_for_market=lambda market, url: f"{url}/{market.lower()}",
        database_url="postgresql://example",
        import_case_document_full=lambda **_kwargs: "",
    )

    assert result["passed"] is False
    assert result["table_counts"]["financial_statement_items"] == 0
    assert result["scope_issues"][0]["table"] == "financial_statement_items"
    assert "refusing full-table count" in result["scope_issues"][0]["message"]
    assert any("financial_statement_items" in error for error in result["errors"])


def test_check_db_case_skips_unsupported_market_and_validates_document_path(tmp_path):
    module = _load_module()
    case = {"case_id": "cn-case", "market": "CN", "document_full_path": "nested/doc.json"}

    result = module.check_db_case(
        case,
        cases_path=tmp_path / "cases.json",
        market_schemas={"HK": "pdf2md_hk"},
        database_url_for_market=lambda market, url: url or "",
        import_case_document_full=lambda **_kwargs: "",
    )

    assert result["skipped"] is True
    assert result["reason"] == "legacy_or_unsupported_market"
    assert module.document_path_for_case(case, tmp_path / "cases.json") == (tmp_path / "nested" / "doc.json").resolve()
