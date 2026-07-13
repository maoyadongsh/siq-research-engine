import importlib.util
import json
from copy import deepcopy
from pathlib import Path


def _load_report_writer():
    source = Path(__file__).resolve().parents[1] / "backtests" / "report_writer.py"
    assert source.exists(), "expected db/imports/backtests/report_writer.py to exist"
    spec = importlib.util.spec_from_file_location("report_writer_under_test", source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert callable(getattr(module, "render_markdown_report", None))
    assert callable(getattr(module, "write_report", None))
    return module


def _summary():
    return {
        "schema_version": "market_document_full_postgres_backtest_results_v1",
        "passed": True,
        "acceptance_passed": False,
        "passed_count": 1,
        "case_count": 1,
        "market_counts": {"HK": 1},
        "acceptance_requirements": {
            "fixture_contract": True,
            "fixture_agent_fact_lookup": True,
            "postgres_import_idempotency": False,
            "real_sample_postgres_roundtrip": False,
            "production_agent_query": False,
        },
        "summary": {
            "assertion_count": 3,
            "common_core_assertion_count": 2,
            "required_evidence_assertion_count": 2,
            "evidence_coverage_ratio": 1.0,
            "unit_checked_assertion_count": 1,
            "currency_checked_assertion_count": 1,
            "unit_currency_explainability_ratio": 1.0,
            "postgres_existing_row_check_verified": False,
            "postgres_roundtrip_verified": True,
            "postgres_import_executed": True,
            "postgres_idempotency_verified": False,
            "postgres_roundtrip_passed_count": 1,
            "postgres_roundtrip_case_count": 1,
            "postgres_family_count_checked_count": 13,
            "postgres_table_count_checked_count": 29,
            "postgres_scope_issue_count": 0,
            "postgres_required_evidence_passed_count": 2,
            "postgres_required_evidence_checked_count": 2,
            "real_sample_minimum_met": True,
            "production_sample_manifest_path": "eval_datasets/market_document_full_postgres/production_sample_manifest.json",
            "production_sample_require_existing": True,
            "production_sample_manifest_counts": {"HK": 2, "JP": 2},
            "production_sample_existing_counts": {"HK": 2, "JP": 1},
            "production_sample_db_executed": True,
            "production_sample_db_verified": False,
            "production_sample_db_passed_count": 1,
            "production_sample_db_case_count": 2,
            "production_sample_db_scope_issue_count": 0,
            "production_sample_db_coexistence_verified": True,
            "production_sample_db_coexistence_passed_count": 1,
            "production_sample_db_coexistence_market_count": 1,
            "agent_query_verified": True,
            "agent_query_passed_count": 1,
            "agent_query_case_count": 1,
            "production_agent_query_executed": True,
            "production_agent_query_verified": False,
            "production_agent_query_passed_count": 1,
            "production_agent_query_case_count": 2,
            "production_sample_agent_view_verified": False,
            "production_sample_agent_view_passed_count": 1,
            "production_sample_agent_view_case_count": 2,
            "wiki_postgres_query_parity_verified": False,
            "wiki_postgres_query_parity_passed_count": 0,
            "wiki_postgres_query_parity_case_count": 0,
            "production_sample_wiki_postgres_query_parity_passed_count": 0,
            "production_sample_wiki_postgres_query_parity_case_count": 0,
            "wiki_postgres_query_parity_warning_count": 0,
            "production_sample_wiki_postgres_query_parity_warning_count": 0,
            "wiki_postgres_query_parity_diff_code_counts": {},
            "production_sample_wiki_postgres_query_parity_diff_code_counts": {},
        },
        "results": [
            {
                "case_id": "fixture-hk",
                "market": "HK",
                "passed": True,
                "fact_count": 3,
                "errors": [],
            }
        ],
        "agent_results": [
            {
                "case_id": "fixture-hk",
                "market": "HK",
                "passed": True,
                "checked": 3,
                "errors": [],
            }
        ],
        "db_results": [
            {
                "case_id": "fixture-hk",
                "market": "HK",
                "passed": True,
                "counts": {"facts": 2, "tables": 1},
                "errors": [],
            }
        ],
        "production_sample_db_results": [
            {
                "case_id": "prod-hk-001",
                "market": "HK",
                "passed": True,
                "counts": {"facts": 2, "tables": 1},
                "errors": [],
            }
        ],
        "production_sample_db_coexistence_results": [
            {
                "market": "HK",
                "passed": True,
                "observed_parse_run_ids": ["prod-hk-001"],
                "errors": [],
            }
        ],
        "production_agent_results": [
            {
                "case_id": "prod-hk-001",
                "market": "HK",
                "mode": "production_sample_agent_view",
                "passed": True,
                "checked": 9,
                "errors": [],
            }
        ],
    }


def test_write_report_writes_json_and_markdown(tmp_path):
    report_writer = _load_report_writer()
    summary = _summary()
    output_path = tmp_path / "nested" / "backtest_report.json"
    markdown_path = tmp_path / "nested" / "backtest_report.md"

    report_writer.write_report(summary, output_path, markdown_path)

    assert json.loads(output_path.read_text(encoding="utf-8")) == summary
    assert markdown_path.read_text(encoding="utf-8") == report_writer.render_markdown_report(summary)


def test_write_report_redacts_local_absolute_paths(tmp_path):
    report_writer = _load_report_writer()
    summary = _summary()
    summary["cases_path"] = "/home/operator/project/eval/cases.json"
    summary["summary"]["production_sample_manifest_path"] = "/tmp/release/manifest.json"
    summary["results"][0]["errors"] = ["failed while reading /home/operator/private/object.json"]
    output_path = tmp_path / "report.json"
    markdown_path = tmp_path / "report.md"

    report_writer.write_report(summary, output_path, markdown_path)

    json_text = output_path.read_text(encoding="utf-8")
    markdown = markdown_path.read_text(encoding="utf-8")
    assert "/home/operator" not in json_text + markdown
    assert "/tmp/release" not in json_text + markdown
    assert json.loads(json_text)["cases_path"] == "[external]"
    assert "[local-path]" in json_text


def test_render_markdown_report_uses_dynamic_acceptance_copy():
    report_writer = _load_report_writer()
    pending_summary = _summary()

    pending = report_writer.render_markdown_report(pending_summary)

    assert "Contract status: **PASS**" in pending
    assert "Acceptance status: **PENDING**" in pending
    assert "| postgres_import_idempotency | PENDING |" in pending
    assert "- postgres_import_idempotency" in pending

    passing_summary = deepcopy(pending_summary)
    passing_summary["acceptance_passed"] = True
    passing_summary["acceptance_requirements"] = {
        name: True for name in passing_summary["acceptance_requirements"]
    }

    passing = report_writer.render_markdown_report(passing_summary)

    assert "Acceptance status: **PASS**" in passing
    assert "| postgres_import_idempotency | PASS |" in passing
    assert "## Remaining Production Gates\n\n- None" in passing


def test_render_markdown_report_keeps_production_sample_and_agent_sections():
    report_writer = _load_report_writer()

    markdown = report_writer.render_markdown_report(_summary())

    assert "- Real sample manifest: eval_datasets/market_document_full_postgres/production_sample_manifest.json" in markdown
    assert '- Real sample manifest counts: `{"HK": 2, "JP": 2}`' in markdown
    assert '- Real sample DB cases: 1/2' in markdown
    assert "## Real Sample PostgreSQL" in markdown
    assert '| prod-hk-001 | HK | PASS | `{"facts": 2, "tables": 1}` |' in markdown
    assert "## Real Sample PostgreSQL Coexistence" in markdown
    assert "## Production Agent View Query" in markdown
    assert "| prod-hk-001 | HK | production_sample_agent_view | PASS | 9 |" in markdown


def test_render_markdown_report_surfaces_postgres_scope_issues():
    report_writer = _load_report_writer()
    summary = _summary()
    message = (
        "DB scope selector missing for table financial_statement_items: selector 'parse_run_id' "
        "and fallback case selectors ['parse_run_id'] are absent; refusing full-table count"
    )
    summary["summary"]["postgres_scope_issue_count"] = 1
    summary["db_results"][0]["passed"] = False
    summary["db_results"][0]["errors"] = [message]
    summary["db_results"][0]["scope_issues"] = [{"table": "financial_statement_items", "message": message}]

    markdown = report_writer.render_markdown_report(summary)

    assert "- PostgreSQL scope issues: 1" in markdown
    assert message in markdown
