import importlib.util
import sys
from pathlib import Path


def _load_module():
    backtest_dir = Path(__file__).resolve().parents[1] / "backtests"
    if str(backtest_dir) not in sys.path:
        sys.path.insert(0, str(backtest_dir))
    source = backtest_dir / "agent_query_gate.py"
    spec = importlib.util.spec_from_file_location("agent_query_gate_under_test", source)
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


class FakeAgentDb:
    def __init__(self, *, rows=None, columns=None):
        self.rows = rows or []
        self.columns = columns if columns is not None else [
            "company_id",
            "company_ticker",
            "filing_id",
            "filing_period_end",
            "parse_run_id",
            "statement_type",
            "canonical_name",
            "item_name",
            "period_key",
            "value",
            "raw_value",
            "unit",
            "currency",
            "evidence_id",
            "evidence_page_number",
            "evidence_table_index",
            "quote_text",
            "source_url",
        ]
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def execute(self, sql, params=None):
        text = " ".join(str(sql).split())
        params = tuple(params or ())
        self.executed.append((text, params))
        if "information_schema.tables" in text or "information_schema.views" in text:
            relation = params[1] if len(params) > 1 else ""
            return FakeCursor(row=(1,) if relation == "v_agent_financial_facts" and self.columns else None)
        if "information_schema.columns" in text:
            relation = params[1] if len(params) > 1 else ""
            rows = [(column,) for column in self.columns] if relation == "v_agent_financial_facts" else []
            return FakeCursor(rows=rows)
        if text.startswith("select count(*)") and ".v_agent_financial_facts " in text:
            rows = self._filtered_view_rows(text, params)
            if " is not null" in text:
                predicate_columns = [
                    column
                    for column in self.columns
                    if f"{column} is not null" in text
                ]
                rows = [
                    row
                    for row in rows
                    if any(row.get(column) is not None for column in predicate_columns)
                ]
            return FakeCursor(row=(len(rows),))
        if text.startswith("select ") and ".v_agent_financial_facts " in text:
            selected = self._selected_columns(text)
            rows = self._filtered_view_rows(text, params)
            return FakeCursor(rows=[tuple(row.get(column) for column in selected) for row in rows])
        raise AssertionError(f"unexpected SQL: {sql!r} params={params!r}")

    def _filtered_view_rows(self, text, params):
        rows = list(self.rows)
        if not params or " where " not in text:
            return rows
        where_sql = text.split(" where ", 1)[1].split(" order by ", 1)[0]
        columns = []
        for part in where_sql.split(" and "):
            part = part.strip(" ()")
            if part.endswith("= %s"):
                columns.append(part.split(" = %s", 1)[0].strip())
        for column, value in zip(columns, params):
            rows = [row for row in rows if row.get(column) == value]
        return rows

    @staticmethod
    def _selected_columns(text):
        return [column.strip() for column in text.split("select ", 1)[1].split(" from ", 1)[0].split(",")]


def _agent_row(**overrides):
    row = {
        "company_id": "HK:00700",
        "company_ticker": "00700",
        "filing_id": "filing-hk",
        "filing_period_end": "2025-12-31",
        "parse_run_id": "parse-hk-agent",
        "statement_type": "income_statement",
        "canonical_name": "revenue",
        "item_name": "Revenue",
        "period_key": "FY2025",
        "value": "100",
        "raw_value": "100",
        "unit": "HKD million",
        "currency": "HKD",
        "evidence_id": "ev-1",
        "evidence_page_number": 12,
        "evidence_table_index": 2,
        "quote_text": "Revenue 100",
        "source_url": "https://example.test/report",
    }
    row.update(overrides)
    return row


def test_check_production_agent_case_matches_market_agent_view_row():
    module = _load_module()
    conn = FakeAgentDb(rows=[_agent_row()])

    result = module.check_production_agent_case(
        {
            "case_id": "hk-agent",
            "market": "HK",
            "assertions": [
                {
                    "statement_type": "income_statement",
                    "canonical_name": "revenue",
                    "period_key": "FY2025",
                    "value": "100",
                    "raw_value": "100",
                    "unit": "HKD million",
                    "currency": "HKD",
                    "required_evidence": True,
                }
            ],
        },
        market_schemas={"HK": "pdf2md_hk"},
        database_url_for_market=lambda market, url: f"{url}/{market.lower()}",
        db_selector_for_case=lambda case: ("filing_id = %s", ("filing-hk",)),
        database_url="postgresql://example",
        db_result={"passed": True, "parse_run_id": "parse-hk-agent"},
        connect=lambda _url: conn,
    )

    assert result["passed"] is True
    assert result["checked"] == 1
    assert result["questions"][0]["matched_fact"]["canonical_name"] == "revenue"
    assert any(
        params[:4] == ("parse-hk-agent", "income_statement", "revenue", "FY2025")
        for _sql, params in conn.executed
    )


def test_check_production_agent_case_reports_missing_agent_view():
    module = _load_module()

    result = module.check_production_agent_case(
        {
            "case_id": "hk-agent",
            "market": "HK",
            "assertions": [{"canonical_name": "revenue", "value": "100"}],
        },
        market_schemas={"HK": "pdf2md_hk"},
        database_url_for_market=lambda market, url: url or "",
        db_selector_for_case=lambda case: ("filing_id = %s", ("filing-hk",)),
        db_result={"passed": True, "parse_run_id": "parse-hk-agent"},
        connect=lambda _url: FakeAgentDb(columns=[]),
    )

    assert result["passed"] is False
    assert result["questions"][0]["reason"] == "agent financial facts view missing"
    assert "agent financial facts view missing" in result["errors"][0]


def test_check_production_sample_agent_view_case_probes_parse_run_rows_values_and_evidence():
    module = _load_module()
    conn = FakeAgentDb(rows=[_agent_row(parse_run_id="parse-hk-production")])

    result = module.check_production_sample_agent_view_case(
        {"case_id": "production_sample_hk_01", "market": "HK"},
        market_schemas={"HK": "pdf2md_hk"},
        database_url_for_market=lambda market, url: f"{url}/{market.lower()}",
        database_url="postgresql://example",
        db_result={"passed": True, "parse_run_id": "parse-hk-production"},
        connect=lambda _url: conn,
    )

    assert result["passed"] is True
    assert result["row_count"] == 1
    assert result["value_row_count"] == 1
    assert result["evidence_row_count"] == 1
    assert result["sample_rows"][0]["parse_run_id"] == "parse-hk-production"
    probe_params = [
        params
        for sql, params in conn.executed
        if ".v_agent_financial_facts " in sql and "parse_run_id = %s" in sql
    ]
    assert probe_params
    assert all(params == ("parse-hk-production",) for params in probe_params)
