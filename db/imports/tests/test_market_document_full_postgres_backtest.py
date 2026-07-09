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


def test_default_market_document_full_backtest_cases_pass():
    runner = _load_runner()

    summary = runner.run_cases()

    assert summary["passed"] is True
    assert summary["case_count"] == 7
    assert {result["market"] for result in summary["results"]} == {"CN", "HK", "JP", "KR", "EU", "US"}
    assert all(result["fact_count"] >= 1 for result in summary["results"])
    assert summary["summary"]["common_core_assertion_count"] >= 5
    assert summary["summary"]["required_evidence_assertion_count"] == 7
    assert summary["summary"]["fact_currency_checked_assertion_count"] == 1
    assert summary["summary"]["postgres_roundtrip_verified"] is False
    assert summary["summary"]["postgres_existing_row_check_verified"] is False
    assert summary["summary"]["postgres_import_executed"] is False
    assert summary["summary"]["postgres_idempotency_verified"] is False
    assert summary["summary"]["agent_query_verified"] is False


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

    assert exit_code == 0
    payload = runner.read_json(output)
    assert payload["passed"] is True
    text = markdown.read_text(encoding="utf-8")
    assert "Market Document Full PostgreSQL Backtest" in text
    assert "PostgreSQL roundtrip verified: False" in text
    assert "PostgreSQL import executed: False" in text
    assert "PostgreSQL idempotency verified: False" in text
    assert "Agent query verified: False" in text


def test_backtest_db_mode_checks_market_schema_counts(monkeypatch, tmp_path):
    runner = _load_runner()
    source_cases = runner.read_json(runner.DEFAULT_CASES_PATH)
    cases = {
        "schema_version": source_cases["schema_version"],
        "cases": [
            {
                **source_cases["cases"][1],
                "expected_row_counts": {"facts": 2, "tables": 1, "chunks": 3, "evidence": 2},
            }
        ],
    }
    document_source = runner.DEFAULT_CASES_PATH.parent / source_cases["cases"][1]["document_full_path"]
    document_target = tmp_path / "examples" / "hk_row_period_document_full.json"
    document_target.parent.mkdir(parents=True)
    document_target.write_text(document_source.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "cases.json").write_text(runner.json.dumps(cases, ensure_ascii=False), encoding="utf-8")

    class FakeCursor:
        def __init__(self, row):
            self.row = row

        def fetchone(self):
            return self.row

    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def execute(self, sql, params=None):
            text = " ".join(str(sql).split())
            if "information_schema.tables" in text:
                return FakeCursor((1,))
            if "information_schema.columns" in text:
                table = params[1]
                columns = {
                    "financial_statement_items": ["statement_type", "canonical_name", "item_name", "period_key", "value", "raw_value", "unit", "currency", "source_table_index"],
                    "xbrl_facts_raw": ["concept", "label", "value_text", "value_numeric", "unit", "context_ref", "period_end", "html_anchor"],
                }
                return types.SimpleNamespace(fetchall=lambda: [(column,) for column in columns.get(table, [])])
            if text.startswith("select statement_type") or text.startswith("select concept"):
                return types.SimpleNamespace(fetchall=lambda: [])
            if text.startswith("select count(*)"):
                table = text.split(" from ", 1)[1].split(" where ", 1)[0]
                counts = {
                    "pdf2md_hk.parse_runs": 1,
                    "pdf2md_hk.financial_statement_items": 2,
                    "pdf2md_hk.financial_facts": 0,
                    "pdf2md_hk.xbrl_facts_raw": 0,
                    "pdf2md_hk.document_tables": 1,
                    "pdf2md_hk.html_tables": 0,
                    "pdf2md_hk.pdf_tables": 0,
                    "pdf2md_hk.document_chunks": 0,
                    "pdf2md_hk.retrieval_chunks": 3,
                    "pdf2md_hk.evidence_citations": 2,
                }
                return FakeCursor((counts.get(table, 0),))
            raise AssertionError(f"unexpected SQL: {sql!r} params={params!r}")

    fake_psycopg = types.SimpleNamespace(connect=lambda _url: FakeConn())
    monkeypatch.setitem(sys.modules, "psycopg", fake_psycopg)

    summary = runner.run_cases(tmp_path / "cases.json", verify_db=True, database_url="postgresql://example")

    assert summary["passed"] is True
    assert summary["summary"]["postgres_existing_row_check_verified"] is True
    assert summary["summary"]["postgres_roundtrip_verified"] is False
    assert summary["summary"]["postgres_import_executed"] is False
    assert summary["summary"]["postgres_roundtrip_case_count"] == 1
    assert summary["db_results"][0]["counts"]["facts"] == 2


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

    class FakeCursor:
        def __init__(self, row):
            self.row = row

        def fetchone(self):
            return self.row

    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def execute(self, sql, params=None):
            text = " ".join(str(sql).split())
            if "information_schema.tables" in text:
                return FakeCursor((1,))
            if "information_schema.columns" in text:
                table = params[1]
                columns = {
                    "financial_statement_items": ["statement_type", "canonical_name", "item_name", "period_key", "value", "raw_value", "unit", "currency", "source_table_index"],
                    "xbrl_facts_raw": ["concept", "label", "value_text", "value_numeric", "unit", "context_ref", "period_end", "html_anchor"],
                }
                return types.SimpleNamespace(fetchall=lambda: [(column,) for column in columns.get(table, [])])
            if text.startswith("select statement_type") or text.startswith("select concept"):
                return types.SimpleNamespace(fetchall=lambda: [])
            if text.startswith("select count(*)"):
                table = text.split(" from ", 1)[1].split(" where ", 1)[0]
                counts = {
                    "pdf2md_hk.parse_runs": 1,
                    "pdf2md_hk.financial_statement_items": 2,
                    "pdf2md_hk.financial_facts": 0,
                    "pdf2md_hk.xbrl_facts_raw": 0,
                    "pdf2md_hk.document_tables": 1,
                    "pdf2md_hk.html_tables": 0,
                    "pdf2md_hk.pdf_tables": 0,
                    "pdf2md_hk.document_chunks": 3,
                    "pdf2md_hk.retrieval_chunks": 0,
                    "pdf2md_hk.evidence_citations": 2,
                }
                return FakeCursor((counts.get(table, 0),))
            raise AssertionError(f"unexpected SQL: {sql!r} params={params!r}")

    monkeypatch.setattr(runner, "_import_case_document_full", fake_import)
    monkeypatch.setitem(sys.modules, "psycopg", types.SimpleNamespace(connect=lambda _url: FakeConn()))

    summary = runner.run_cases(
        tmp_path / "cases.json",
        verify_db=True,
        database_url="postgresql://example",
        import_before_db_check=True,
        idempotency=True,
    )

    assert summary["passed"] is True
    assert summary["mode"] == "document_full_fixture_contract+postgres_import_idempotency"
    assert summary["summary"]["postgres_import_executed"] is True
    assert summary["summary"]["postgres_idempotency_verified"] is True
    assert [call["run_ddl"] for call in import_calls] == [True, False]
    assert summary["db_results"][0]["counts"] == summary["db_results"][0]["second_counts"]
    assert summary["db_results"][0]["content_hashes"] == summary["db_results"][0]["second_content_hashes"]


def test_backtest_rejects_idempotency_without_import_before_db_check():
    runner = _load_runner()

    try:
        runner.run_cases(verify_db=True, idempotency=True)
    except ValueError as exc:
        assert "--idempotency requires --import-before-db-check" in str(exc)
    else:
        raise AssertionError("expected idempotency dependency error")
