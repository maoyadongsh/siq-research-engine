import importlib.util
import sys
from pathlib import Path


def _load_module():
    backtest_dir = Path(__file__).resolve().parents[1] / "backtests"
    if str(backtest_dir) not in sys.path:
        sys.path.insert(0, str(backtest_dir))
    source = backtest_dir / "postgres_roundtrip_helpers.py"
    spec = importlib.util.spec_from_file_location("postgres_roundtrip_helpers_under_test", source)
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
    def __init__(self, *, counts=None, columns=None, rows=None, evidence_rows=None):
        self.counts = counts or {}
        self.rows = rows or {}
        self.evidence_rows = evidence_rows or []
        self.columns = {
            "companies": ["company_id", "ticker"],
            "parse_runs": ["parse_run_id", "filing_id"],
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
            "document_tables": ["parse_run_id", "filing_id", "table_index"],
            "document_chunks": ["parse_run_id", "filing_id", "chunk_uid"],
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
        if columns:
            self.columns.update(columns)

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
            if not rows:
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


def test_sql_identifier_and_scoped_where_helpers():
    module = _load_module()
    conn = FakeDb(columns={"companies": ["company_id"], "v_agent_financial_facts": ["company_ticker", "filing_period_end"]})

    assert module.safe_sql_ident("financial_statement_items") == "financial_statement_items"
    assert module.simple_selector_column("parse_run_id = %s") == "parse_run_id"
    assert module.scoped_where_for_table(
        conn,
        "pdf2md_hk",
        "companies",
        {"expected_identity": {"company_id": "HK:00005"}},
        "parse_run_id = %s",
        ("missing-column",),
    ) == ("company_id = %s", ("HK:00005",))
    assert module.scoped_where_for_relation(
        conn,
        "pdf2md_hk",
        "v_agent_financial_facts",
        {"ticker": "00005", "period_end": "2025-12-31"},
        "parse_run_id = %s",
        ("missing-column",),
    ) == ("company_ticker = %s", ("00005",))


def test_counts_and_expectations_cover_family_and_table_rules():
    module = _load_module()
    conn = FakeDb(
        counts={
            "parse_runs": 1,
            "financial_statement_items": 2,
            "document_tables": 1,
            "document_chunks": 3,
            "evidence_citations": 2,
        }
    )
    case = {
        "parse_run_id": "parse-1",
        "expected_row_counts": {"facts": 2},
        "expected_family_counts": {"chunks": {"min": 3}},
        "expected_table_counts": {"financial_statement_items": {"exact": 2}},
    }

    family_counts = module.db_family_counts(conn, "pdf2md_hk", case, "parse_run_id = %s", ("parse-1",))
    table_counts = module.db_table_counts(
        conn,
        "pdf2md_hk",
        case,
        "parse_run_id = %s",
        ("parse-1",),
        case["expected_table_counts"],
    )
    errors = []
    module.check_expected_counts(errors, family_counts=family_counts, table_counts=table_counts, case=case)

    assert family_counts["facts"] == 2
    assert family_counts["chunks"] == 3
    assert table_counts["financial_statement_items"] == 2
    assert errors == []


def test_missing_selector_column_refuses_full_table_count():
    module = _load_module()
    conn = FakeDb(
        counts={"unscoped_fact_shadow": 99},
        columns={"unscoped_fact_shadow": ["source_system", "value"]},
    )
    case = {"parse_run_id": "parse-1"}

    observed = module.db_count_for_case(
        conn,
        "pdf2md_hk",
        "unscoped_fact_shadow",
        case,
        "parse_run_id = %s",
        ("parse-1",),
    )
    issues = module.db_scope_issues(
        conn,
        "pdf2md_hk",
        case,
        "parse_run_id = %s",
        ("parse-1",),
        {"unscoped_fact_shadow": {"min": 1}},
    )

    assert observed == 0
    assert issues == [
        {
            "table": "unscoped_fact_shadow",
            "selector": "parse_run_id",
            "case_selector_columns": ["parse_run_id"],
            "message": (
                "DB scope selector missing for table unscoped_fact_shadow: selector 'parse_run_id' "
                "and fallback case selectors ['parse_run_id'] are absent; refusing full-table count"
            ),
        }
    ]


def test_required_evidence_passes_via_join_and_content_hashes_are_stable():
    module = _load_module()
    conn = FakeDb(
        rows={
            "financial_statement_items": [
                {
                    "parse_run_id": "parse-1",
                    "filing_id": "filing-1",
                    "statement_type": "income_statement",
                    "canonical_name": "revenue",
                    "period_key": "FY2025",
                    "value": "100",
                    "raw_value": "100",
                    "evidence_id": "ev-1",
                }
            ],
            "document_chunks": [{"parse_run_id": "parse-1", "chunk_uid": "chunk-1"}],
            "document_tables": [{"parse_run_id": "parse-1", "table_index": 7}],
        },
        evidence_rows=[
            {
                "evidence_id": "ev-1",
                "parse_run_id": "parse-1",
                "page_number": 12,
                "quote_text": "Revenue 100",
            }
        ],
    )
    case = {"parse_run_id": "parse-1", "period_key": "FY2025"}

    check = module.db_required_evidence_check(
        conn,
        "pdf2md_hk",
        case,
        "parse_run_id = %s",
        ("parse-1",),
        {"statement_type": "income_statement", "canonical_name": "revenue", "required_evidence": True},
    )
    hashes = module.db_content_hashes(conn, "pdf2md_hk", "parse_run_id = %s", ("parse-1",))

    assert check["passed"] is True
    assert check["mode"] == "evidence_id_join"
    assert hashes["critical_content"] == module.db_content_hashes(
        conn,
        "pdf2md_hk",
        "parse_run_id = %s",
        ("parse-1",),
    )["critical_content"]
