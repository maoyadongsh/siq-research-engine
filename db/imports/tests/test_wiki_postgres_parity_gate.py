import importlib.util
import json
import sys
from pathlib import Path


def _load_module():
    backtest_dir = Path(__file__).resolve().parents[1] / "backtests"
    if str(backtest_dir) not in sys.path:
        sys.path.insert(0, str(backtest_dir))
    source = backtest_dir / "wiki_postgres_parity_gate.py"
    spec = importlib.util.spec_from_file_location("wiki_postgres_parity_gate_under_test", source)
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
    def __init__(self, rows=None):
        self.rows = rows or []
        self.columns = [
            "company_id",
            "filing_id",
            "parse_run_id",
            "statement_type",
            "canonical_name",
            "item_name",
            "period_key",
            "period_end",
            "filing_period_end",
            "value",
            "raw_value",
            "unit",
            "currency",
            "evidence_page_number",
            "evidence_table_index",
            "quote_text",
            "source_url",
        ]

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def execute(self, sql, params=None):
        text = " ".join(str(sql).split())
        params = tuple(params or ())
        if "information_schema.tables" in text or "information_schema.views" in text:
            relation = params[1] if len(params) > 1 else ""
            return FakeCursor(row=(1,) if relation == "v_agent_financial_facts" else None)
        if "information_schema.columns" in text:
            relation = params[1] if len(params) > 1 else ""
            rows = [(column,) for column in self.columns] if relation == "v_agent_financial_facts" else []
            return FakeCursor(rows=rows)
        if text.startswith("select ") and ".v_agent_financial_facts " in text:
            selected = self._selected_columns(text)
            rows = self._filtered_rows(text, params)
            return FakeCursor(rows=[tuple(row.get(column) for column in selected) for row in rows])
        raise AssertionError(f"unexpected SQL: {sql!r} params={params!r}")

    def _filtered_rows(self, text, params):
        rows = list(self.rows)
        where_sql = text.split(" where ", 1)[1].split(" order by ", 1)[0] if " where " in text else ""
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


def _write_document(tmp_path, *, values=None):
    values = values or {"FY2025": "100", "FY2024": "90"}
    document_full = {
        "financial_data": {
            "market": "HK",
            "company_id": "HK:00700",
            "reporting_currency": "HKD",
            "statements": [
                {
                    "statement_type": "income_statement",
                    "unit": "HKD million",
                    "currency": "HKD",
                    "items": [
                        {
                            "canonical_name": "revenue",
                            "name": "Revenue",
                            "values": values,
                            "raw_values": {period: str(value) for period, value in values.items()},
                            "sources": {
                                period: {"table_index": index + 1, "quote_text": f"Revenue {value}"}
                                for index, (period, value) in enumerate(values.items())
                            },
                        }
                    ],
                }
            ],
        },
        "content_list_enhanced": {
            "tables": [
                {"table_index": index + 1, "page_number": 10 + index}
                for index in range(len(values))
            ]
        },
    }
    (tmp_path / "document_full.json").write_text(json.dumps(document_full), encoding="utf-8")
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(json.dumps({"cases": []}), encoding="utf-8")
    return cases_path


def _case(**overrides):
    case = {
        "case_id": "hk-parity",
        "market": "HK",
        "company_id": "HK:00700",
        "period_key": "FY2025",
        "document_full_path": "document_full.json",
    }
    case.update(overrides)
    return case


def _pg_row(**overrides):
    row = {
        "company_id": "HK:00700",
        "filing_id": "filing-hk",
        "parse_run_id": "parse-hk",
        "statement_type": "income_statement",
        "canonical_name": "revenue",
        "item_name": "Revenue",
        "period_key": "FY2025",
        "period_end": "FY2025",
        "filing_period_end": "2025-12-31",
        "value": "100",
        "raw_value": "100",
        "unit": "HKD million",
        "currency": "HKD",
        "evidence_page_number": 10,
        "evidence_table_index": 1,
        "quote_text": "Revenue 100",
        "source_url": "https://example.test/report",
    }
    row.update(overrides)
    return row


def _run(module, case, cases_path, rows):
    return module.check_wiki_postgres_parity_case(
        case,
        cases_path,
        market_schemas={"HK": "pdf2md_hk"},
        database_url_for_market=lambda market, url: url or "",
        db_selector_for_case=lambda _case: ("company_id = %s", ("HK:00700",)),
        document_path_for_case=lambda case, cases_path: cases_path.parent / case["document_full_path"],
        read_json=lambda path: json.loads(path.read_text(encoding="utf-8")),
        db_result={"passed": True, "parse_run_id": "parse-hk"},
        connect=lambda _url: FakeAgentDb(rows),
    )


def test_explicit_parity_matches_agent_view_fact(tmp_path):
    module = _load_module()
    cases_path = _write_document(tmp_path)
    case = _case(
        assertions=[
            {
                "statement_type": "income_statement",
                "canonical_name": "revenue",
                "period_key": "FY2025",
                "expected_value": "100",
                "unit": "HKD million",
                "currency": "HKD",
                "required_evidence": True,
            }
        ]
    )

    result = _run(module, case, cases_path, [_pg_row()])

    assert result["passed"] is True
    assert result["checked"] == 1
    assert result["questions"][0]["matched_postgres_fact"]["canonical_name"] == "revenue"
    assert result["diff_code_counts"] == {}


def test_explicit_parity_classifies_period_alias_as_hard_failure(tmp_path):
    module = _load_module()
    cases_path = _write_document(tmp_path)
    case = _case(
        assertions=[
            {
                "statement_type": "income_statement",
                "canonical_name": "revenue",
                "period_key": "FY2025",
                "expected_value": "100",
            }
        ]
    )

    result = _run(module, case, cases_path, [_pg_row(period_key="2025-12-31", period_end="2025-12-31")])

    assert result["passed"] is False
    assert result["error_diff_code_counts"] == {"period_alias_diff": 1}
    assert result["questions"][0]["diff_codes"] == ["period_alias_diff"]
    assert result["questions"][0]["first_postgres_candidate"]["period_key"] == "2025-12-31"


def test_generated_parity_demotes_extra_diffs_to_warnings_after_minimum_passes(tmp_path):
    module = _load_module()
    cases_path = _write_document(tmp_path, values={"FY2025": "100", "FY2024": "90", "FY2023": "80"})

    result = _run(
        module,
        _case(assertions=[]),
        cases_path,
        [
            _pg_row(period_key="FY2025", value="100", raw_value="100"),
            _pg_row(period_key="FY2024", value="90", raw_value="90"),
            _pg_row(period_key="FY2023", value="999", raw_value="80"),
        ],
    )

    assert result["passed"] is True
    assert result["passed_checks"] == 2
    assert result["minimum_generated_passes"] == 2
    assert result["errors"] == []
    assert result["warnings"]
    assert result["warning_diff_code_counts"] == {"value_mismatch": 1}
