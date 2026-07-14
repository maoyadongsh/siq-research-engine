import importlib.util
import json
import os
import re
import subprocess
from pathlib import Path

import pytest


def _required_live_release_env() -> dict[str, str]:
    return {
        "SIQ_LIVE_MODEL_BENCHMARK_MODE": "live-http",
        "SIQ_LIVE_MODEL_BENCHMARK_REQUIRED": "1",
        "SIQ_LIVE_MODEL_URL": "https://live.example.test/v1/chat",
        "SIQ_LIVE_MODEL_AUTH_TOKEN": "fake-live-release-token",
        "SIQ_LIVE_MODEL_PROTOCOL": "json",
    }


def _load_gate_module():
    source = Path(__file__).resolve().parents[1] / "run_market_document_full_postgres_gate.py"
    spec = importlib.util.spec_from_file_location("run_market_document_full_postgres_gate_under_test", source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _summary(*, passed=True, acceptance_passed=False, db_results=None, parity_results=None, production_agent_results=None):
    safe_production_db_results = [
        {
            "case_id": "production_sample_hk_01",
            "market": "HK",
            "passed": True,
            "imported_before_check": True,
            "idempotency_checked": True,
        }
    ] if acceptance_passed else []
    safe_production_agent_results = (
        production_agent_results
        if production_agent_results is not None
        else ([{"case_id": "production_sample_hk_01", "market": "HK", "passed": True}] if acceptance_passed else [])
    )
    return {
        "schema_version": "market_document_full_postgres_backtest_results_v1",
        "passed": passed,
        "acceptance_passed": acceptance_passed,
        "passed_count": 1 if passed else 0,
        "case_count": 1,
        "acceptance_requirements": {
            "fixture_contract": passed,
            "fixture_postgres_write_prohibited": acceptance_passed,
            "postgres_import_idempotency": acceptance_passed,
            "postgres_required_evidence": acceptance_passed,
            "real_sample_minimum": acceptance_passed,
            "real_sample_postgres_roundtrip": acceptance_passed,
            "real_sample_postgres_idempotency": acceptance_passed,
            "real_sample_postgres_coexistence": acceptance_passed,
            "real_sample_agent_view_query": acceptance_passed,
            "wiki_postgres_query_parity": acceptance_passed,
            "production_agent_query": acceptance_passed,
        },
        "summary": {
            "postgres_import_executed": bool(db_results),
            "fixture_postgres_policy": "prohibited",
            "fixture_postgres_access_executed": False,
            "fixture_postgres_import_executed": False,
        },
        "results": [],
        "agent_results": [],
        "db_results": db_results or [],
        "production_sample_db_results": safe_production_db_results,
        "production_sample_db_coexistence_results": (
            [{"market": "HK", "passed": True}] if acceptance_passed else []
        ),
        "fixture_production_agent_results": [],
        "production_sample_agent_results": safe_production_agent_results,
        "production_agent_results": safe_production_agent_results,
        "wiki_postgres_parity_results": parity_results or [],
        "production_sample_wiki_postgres_parity_results": (
            [{"case_id": "production_sample_hk_01", "market": "HK", "passed": True}]
            if acceptance_passed
            else []
        ),
    }


def _install_fakes(monkeypatch, module, summary):
    calls = []
    writes = []

    def fake_run_cases(cases_path, **kwargs):
        calls.append({"cases_path": cases_path, **kwargs})
        return summary

    def fake_write_report(payload, output_path, markdown_path):
        writes.append({"payload": payload, "output_path": output_path, "markdown_path": markdown_path})
        output_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload), encoding="utf-8")
        markdown_path.write_text("# Gate\n", encoding="utf-8")

    def fake_validate_production_sample_manifest(path, *, require_existing):
        return {
            "path": str(path),
            "passed": True,
            "require_existing": require_existing,
            "samples": [],
        }

    monkeypatch.setattr(module, "run_cases", fake_run_cases)
    monkeypatch.setattr(module, "write_report", fake_write_report)
    monkeypatch.setattr(module, "validate_production_sample_manifest", fake_validate_production_sample_manifest)
    monkeypatch.setattr(
        module,
        "audit_fixture_contamination",
        lambda **_kwargs: {
            "passed": True,
            "contaminated_run_count": 0,
            "error_count": 0,
        },
    )
    return calls, writes


def test_contract_mode_runs_without_db_or_parity(monkeypatch, tmp_path):
    module = _load_gate_module()
    calls, writes = _install_fakes(monkeypatch, module, _summary(passed=True, acceptance_passed=False))

    exit_code = module.main(["--mode", "contract", "--output-dir", str(tmp_path)])

    assert exit_code == 0
    assert calls == [
        {
            "cases_path": module.DEFAULT_CASES_PATH,
            "verify_db": False,
            "database_url": None,
            "import_before_db_check": False,
            "idempotency": False,
            "production_sample_manifest_path": module.DEFAULT_PRODUCTION_SAMPLE_MANIFEST_PATH,
            "require_production_sample_files": False,
            "production_sample_db": False,
            "production_agent_query": False,
        }
    ]
    assert writes[0]["output_path"] == tmp_path / "market_document_full_postgres_contract_gate.json"
    assert writes[0]["markdown_path"] == tmp_path / "market_document_full_postgres_contract_gate.md"


def test_default_outputs_stay_under_ignored_artifacts_for_all_modes():
    module = _load_gate_module()

    for mode in ("contract", "offline-postgres"):
        args = module._build_parser().parse_args(["--mode", mode])
        json_output, markdown_output = module._report_paths(args)

        for path in (json_output, markdown_output):
            relative = path.relative_to(module.REPO_ROOT)
            assert relative.parts[:2] == ("artifacts", "eval-runs")
            assert relative.parts[:2] != ("docs", "reports")
            assert relative.parts[0] != "eval_datasets"


def test_contract_mode_fails_if_backtest_produces_db_or_parity_results(monkeypatch, tmp_path):
    module = _load_gate_module()
    _install_fakes(
        monkeypatch,
        module,
        _summary(passed=True, db_results=[{"case_id": "hk"}], parity_results=[{"case_id": "hk"}]),
    )

    exit_code = module.main(["--mode", "contract", "--output-dir", str(tmp_path)])

    assert exit_code == 1


def test_offline_postgres_mode_requires_external_production_sample_root(monkeypatch, tmp_path):
    module = _load_gate_module()
    calls, _writes = _install_fakes(monkeypatch, module, _summary(passed=True, acceptance_passed=True))
    monkeypatch.delenv(module.PRODUCTION_SAMPLE_ROOT_ENV, raising=False)

    try:
        module.main(["--mode", "offline-postgres", "--output-dir", str(tmp_path)])
    except SystemExit as exc:
        message = str(exc)
    else:  # pragma: no cover - protects the fail-fast contract
        raise AssertionError("offline-postgres accepted a missing production sample root")

    assert "--production-sample-root" in message
    assert module.PRODUCTION_SAMPLE_ROOT_ENV in message
    assert calls == []


def test_offline_postgres_preflight_lists_all_missing_samples_before_db_gate(monkeypatch, tmp_path, capsys):
    module = _load_gate_module()
    calls, writes = _install_fakes(monkeypatch, module, _summary(passed=True, acceptance_passed=True))
    sample_root = tmp_path / "external-market-samples"
    missing_samples = [
        {
            "market": market,
            "path": f"data/{market.lower()}/sample-{index}/document_full.json",
            "resolved_path": str(sample_root / market.lower() / f"sample-{index}" / "document_full.json"),
            "exists": False,
            "existence_checked": True,
        }
        for market in ("HK", "JP", "KR", "EU", "US")
        for index in range(1, 4)
    ]
    monkeypatch.setattr(
        module,
        "validate_production_sample_manifest",
        lambda _path, *, require_existing: {
            "passed": False,
            "require_existing": require_existing,
            "reason": "missing samples",
            "samples": missing_samples,
        },
    )

    exit_code = module.main(
        [
            "--mode",
            "offline-postgres",
            "--production-sample-root",
            str(sample_root),
            "--output-dir",
            str(tmp_path / "output"),
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "FAIL offline-postgres production sample preflight" in output
    assert "Missing required production sample files: 15" in output
    assert output.count(" -> ") == 15
    assert calls == []
    assert writes == []


def test_offline_postgres_mode_uses_strict_acceptance_gate(monkeypatch, tmp_path):
    module = _load_gate_module()
    calls, writes = _install_fakes(monkeypatch, module, _summary(passed=True, acceptance_passed=True))

    exit_code = module.main(
        [
            "--mode",
            "offline-postgres",
            "--database-url",
            "postgresql://postgres:secret@db/not_market",
            "--production-sample-root",
            str(tmp_path / "market-samples"),
            "--output-dir",
            str(tmp_path),
        ]
    )

    assert exit_code == 0
    assert calls == [
        {
            "cases_path": module.DEFAULT_CASES_PATH,
            "verify_db": True,
            "database_url": "postgresql://postgres:secret@db/not_market",
            "import_before_db_check": True,
            "idempotency": True,
            "production_sample_manifest_path": module.DEFAULT_PRODUCTION_SAMPLE_MANIFEST_PATH,
            "require_production_sample_files": True,
            "production_sample_db": True,
            "production_agent_query": True,
            "fixture_postgres": False,
        }
    ]
    assert writes[0]["output_path"] == tmp_path / "market_document_full_postgres_offline_postgres_gate.json"
    assert writes[0]["markdown_path"] == tmp_path / "market_document_full_postgres_offline_postgres_gate.md"
    assert module.PRODUCTION_SAMPLE_ROOT_ENV not in module.os.environ


def test_offline_postgres_gate_fails_closed_if_fixture_db_results_appear(monkeypatch, tmp_path):
    module = _load_gate_module()
    summary = _summary(passed=True, acceptance_passed=True)
    summary["db_results"] = [{"case_id": "hk-fixture", "market": "HK", "passed": True}]
    _install_fakes(monkeypatch, module, summary)

    exit_code = module.main(
        [
            "--mode",
            "offline-postgres",
            "--production-sample-root",
            str(tmp_path / "market-samples"),
            "--output-dir",
            str(tmp_path),
        ]
    )

    assert exit_code == 1
    assert summary["offline_fixture_safety"]["passed"] is False
    assert summary["offline_fixture_safety"]["fixture_result_counts"]["db_results"] == 1


def test_offline_postgres_gate_requires_real_sample_idempotency_evidence(monkeypatch, tmp_path):
    module = _load_gate_module()
    summary = _summary(passed=True, acceptance_passed=True)
    summary["production_sample_db_results"][0]["idempotency_checked"] = False
    _install_fakes(monkeypatch, module, summary)

    exit_code = module.main(
        [
            "--mode",
            "offline-postgres",
            "--production-sample-root",
            str(tmp_path / "market-samples"),
            "--output-dir",
            str(tmp_path),
        ]
    )

    assert exit_code == 1
    assert summary["offline_fixture_safety"]["production_sample_db_idempotency_proven"] is False


@pytest.mark.parametrize(
    "result_field",
    [
        "production_sample_db_results",
        "production_sample_db_coexistence_results",
        "production_sample_agent_results",
        "production_sample_wiki_postgres_parity_results",
    ],
)
def test_offline_postgres_gate_requires_every_real_sample_result_family(
    monkeypatch, tmp_path, result_field
):
    module = _load_gate_module()
    summary = _summary(passed=True, acceptance_passed=True)
    summary[result_field] = []
    _install_fakes(monkeypatch, module, summary)

    exit_code = module.main(
        [
            "--mode",
            "offline-postgres",
            "--production-sample-root",
            str(tmp_path / "market-samples"),
            "--output-dir",
            str(tmp_path),
        ]
    )

    assert exit_code == 1
    assert summary["offline_fixture_safety"]["real_sample_result_fields_present"] is False


def test_offline_postgres_gate_blocks_existing_fixture_contamination(
    monkeypatch, tmp_path, capsys
):
    module = _load_gate_module()
    summary = _summary(passed=True, acceptance_passed=True)
    _install_fakes(monkeypatch, module, summary)
    monkeypatch.setattr(
        module,
        "audit_fixture_contamination",
        lambda **_kwargs: {
            "passed": False,
            "contaminated_run_count": 6,
            "error_count": 0,
        },
    )

    exit_code = module.main(
        [
            "--mode",
            "offline-postgres",
            "--production-sample-root",
            str(tmp_path / "market-samples"),
            "--output-dir",
            str(tmp_path),
        ]
    )

    assert exit_code == 1
    assert summary["fixture_contamination_audit"]["contaminated_run_count"] == 6
    assert summary["offline_fixture_safety"]["contamination_audit_clean"] is False
    assert "Offline fixture safety failed: contaminated_runs=6" in capsys.readouterr().out


def test_offline_postgres_mode_restores_existing_sample_root_env(monkeypatch, tmp_path):
    module = _load_gate_module()
    _install_fakes(monkeypatch, module, _summary(passed=True, acceptance_passed=True))
    monkeypatch.setenv(module.PRODUCTION_SAMPLE_ROOT_ENV, "/existing/sample-root")

    assert module.main(
        [
            "--mode",
            "offline-postgres",
            "--production-sample-root",
            str(tmp_path / "temporary-samples"),
            "--output-dir",
            str(tmp_path / "output"),
        ]
    ) == 0

    assert module.os.environ[module.PRODUCTION_SAMPLE_ROOT_ENV] == "/existing/sample-root"


def test_offline_postgres_mode_requires_acceptance_passed(monkeypatch, tmp_path):
    module = _load_gate_module()
    _install_fakes(monkeypatch, module, _summary(passed=True, acceptance_passed=False))

    exit_code = module.main(
        [
            "--mode",
            "offline-postgres",
            "--production-sample-root",
            str(tmp_path / "market-samples"),
            "--output-dir",
            str(tmp_path),
        ]
    )

    assert exit_code == 1


def test_failed_gate_prints_actionable_summary(monkeypatch, tmp_path, capsys):
    module = _load_gate_module()
    _install_fakes(
        monkeypatch,
        module,
        _summary(
            passed=True,
            acceptance_passed=False,
            db_results=[
                {
                    "market": "US",
                    "case_id": "us-real-1",
                    "status": "failed",
                    "errors": ["missing evidence_citations"],
                }
            ],
            production_agent_results=[
                {
                    "market": "HK",
                    "case_id": "hk-agent-revenue",
                    "metric": "revenue",
                    "warnings": ["value_mismatch"],
                }
            ],
        ),
    )

    exit_code = module.main(
        [
            "--mode",
            "offline-postgres",
            "--production-sample-root",
            str(tmp_path / "market-samples"),
            "--output-dir",
            str(tmp_path),
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "postgres_import_idempotency" in output
    assert "db_results: US us-real-1 failed: missing evidence_citations" in output
    assert "production_agent_results: HK hk-agent-revenue revenue: value_mismatch" in output


def test_failed_gate_prints_scope_issues_even_without_duplicate_errors(monkeypatch, tmp_path, capsys):
    module = _load_gate_module()
    message = (
        "DB scope selector missing for table financial_statement_items: selector 'parse_run_id' "
        "and fallback case selectors ['parse_run_id'] are absent; refusing full-table count"
    )
    _install_fakes(
        monkeypatch,
        module,
        _summary(
            passed=True,
            acceptance_passed=False,
            db_results=[
                {
                    "market": "HK",
                    "case_id": "hk-scope-drift",
                    "status": "failed",
                    "scope_issues": [{"table": "financial_statement_items", "message": message}],
                }
            ],
        ),
    )

    exit_code = module.main(
        [
            "--mode",
            "offline-postgres",
            "--production-sample-root",
            str(tmp_path / "market-samples"),
            "--output-dir",
            str(tmp_path),
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == 1
    assert message in output


def test_failed_gate_omits_intentional_skipped_market_results(monkeypatch, tmp_path, capsys):
    module = _load_gate_module()
    _install_fakes(
        monkeypatch,
        module,
        _summary(
            passed=True,
            acceptance_passed=False,
            db_results=[
                {
                    "market": "CN",
                    "case_id": "cn-legacy-fixture",
                    "passed": True,
                    "skipped": True,
                    "reason": "legacy_or_unsupported_market",
                }
            ],
        ),
    )

    exit_code = module.main(
        [
            "--mode",
            "offline-postgres",
            "--production-sample-root",
            str(tmp_path / "market-samples"),
            "--output-dir",
            str(tmp_path),
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "legacy_or_unsupported_market" not in output


def test_failed_gate_reports_skipped_result_without_explicit_pass(monkeypatch, tmp_path, capsys):
    module = _load_gate_module()
    _install_fakes(
        monkeypatch,
        module,
        _summary(
            passed=True,
            acceptance_passed=False,
            db_results=[
                {
                    "market": "US",
                    "case_id": "production-sample-us",
                    "skipped": True,
                    "reason": "database unavailable",
                }
            ],
        ),
    )

    exit_code = module.main(
        [
            "--mode",
            "offline-postgres",
            "--production-sample-root",
            str(tmp_path / "market-samples"),
            "--output-dir",
            str(tmp_path),
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "database unavailable" in output


def test_release_gate_wrapper_runs_financial_qa_benchmarks():
    repo_root = Path(__file__).resolve().parents[3]
    wrapper = (repo_root / "scripts/ops/run_market_postgres_release_gate.sh").read_text(encoding="utf-8")
    workflow = (repo_root / ".github/workflows/market-postgres-release-gate.yml").read_text(encoding="utf-8")
    ci_workflow = (repo_root / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "run_market_document_full_postgres_gate.py" in wrapper
    assert "audit_market_postgres_fixture_contamination.py" in wrapper
    assert "fixture-contamination-audit.json" in wrapper
    assert wrapper.count('"$PYTHON_BIN" scripts/maintenance/run_financial_qa_benchmark.py') == 2
    assert "run_performance_baseline.py" in wrapper
    assert "run_market_ingestion_eval.py" in wrapper
    assert "--strict" in wrapper
    assert "market_ingestion_eval_report.json" in wrapper
    assert "compare_financial_quality_baselines.py" in wrapper
    assert "performance-comparison.json" in wrapper
    assert "SIQ_PERFORMANCE_BASELINE_REPORT" in wrapper
    assert "SIQ_PERFORMANCE_COMPARISON_REQUIRED" in wrapper
    assert "--mode nightly" in wrapper
    assert "--require-nightly-inputs" in wrapper
    assert "--require-agent-memory-vector-probes" in wrapper
    assert "--require-ic-vector-retrieval-probe" in wrapper
    assert "--skip-agent-memory-vector-probes" in wrapper
    assert "--agent-memory-vector-collection" in wrapper
    assert "--agent-memory-retrieval-cases" in wrapper
    assert "--agent-memory-retrieval-top-k" in wrapper
    assert "--agent-memory-embedding-model" in wrapper
    assert "--agent-memory-embedding-base-url" not in wrapper
    assert "DEFAULT_AGENT_MEMORY_RETRIEVAL_CASES" in wrapper
    assert "eval_datasets/agent_memory_retrieval_contract/cases.json" in wrapper
    assert "DEFAULT_AGENT_MEMORY_VECTOR_SEED_PROFILES" in wrapper
    assert "siq_assistant,siq_ic_legal_scanner,siq_ic_chairman" in wrapper
    assert "performance_baseline_nightly.json" in wrapper
    assert "performance_baseline_contract.json" in wrapper
    assert "ingest_agent_memory_to_milvus.py" in wrapper
    assert "check_agent_memory_vector_health.py" in wrapper
    assert "agent_memory_milvus_seed.json" in wrapper
    assert "agent_memory_vector_preflight.json" in wrapper
    assert "agent_memory_vector_post_seed_health.json" in wrapper
    assert "SIQ_AGENT_MEMORY_VECTOR_SEED" in wrapper
    assert "SIQ_AGENT_MEMORY_VECTOR_HEALTH_REQUIRE_COLLECTION" in wrapper
    assert "--mode trace-offline" in wrapper
    assert "--mode wiki-static" in wrapper
    assert "financial_qa_benchmark_trace_offline.json" in wrapper
    assert "financial_qa_benchmark_wiki_static.json" in wrapper
    assert "run_parser_financial_pdf_release_gate.py" in wrapper
    assert "SIQ_FINANCIAL_GOLDEN_SAMPLE_ROOT" in wrapper
    assert "run_permission_negative_report.py" in wrapper
    assert "SIQ_PERMISSION_NEGATIVE_GATE_REQUIRED" in wrapper
    assert "run_restore_matrix.py" in wrapper
    assert "SIQ_RESTORE_MATRIX_BACKUP_DIR" in wrapper
    assert "SIQ_RESTORE_MATRIX_ADMIN_URL" in wrapper
    assert "restore-matrix.json" in wrapper
    assert "check_production_config.py" in wrapper
    assert "SIQ_PRODUCTION_CONFIG_REQUIRED" in wrapper
    assert "write_release_artifact_manifest.py" in wrapper
    assert "MANIFEST_REQUIRED_ARGS" in wrapper
    assert "--required-artifact" in wrapper
    assert "market_document_full_postgres_contract_gate.json" in wrapper
    assert "market_document_full_postgres_offline_postgres_gate.json" in wrapper
    assert "financial_qa_benchmark_trace_offline.json" in wrapper
    assert "financial_qa_benchmark_wiki_static.json" in wrapper
    assert "parser_financial_golden.json" in wrapper
    assert "permission-negative-report.json" in wrapper
    assert "SIQ_PARSER_FINANCIAL_PDF_GATE_MODE" in wrapper
    assert "SIQ_PARSER_FINANCIAL_PDF_GATE_REQUIRED" in wrapper
    assert "off|preflight|live-http" in wrapper
    assert "parser_financial_pdf_release.json" in wrapper
    assert 'if [[ "$PARSER_FINANCIAL_PDF_GATE_MODE" != "off" ]] || is_truthy' in wrapper
    assert "run_live_financial_qa_benchmark.py" in wrapper
    assert "--required" in wrapper
    assert "live_financial_qa_benchmark.json" in wrapper
    assert "Financial QA trace-offline" in workflow
    assert "Financial QA wiki-static" in workflow
    assert "Financial QA live-http" in workflow
    assert "required_execution_satisfied" in workflow
    assert "network_requests_started" in workflow
    assert "Performance Baseline" in workflow
    assert "performance_baseline_nightly.json" in workflow
    assert "p95_ms" in workflow
    assert "domain_latency_p95_ms" in workflow
    assert "domain_units" in workflow
    assert "reason=" in workflow
    assert "SIQ_AGENT_MEMORY_VECTOR_PROBES_REQUIRED" in workflow
    assert "SIQ_PERFORMANCE_BASELINE_REPORT" in workflow
    assert "Require versioned performance baseline" in workflow
    assert "SIQ_AGENT_MEMORY_VECTOR_SEED" in workflow
    assert "Agent memory vector seed passed" in workflow
    assert "Agent memory vector preflight passed" in workflow
    assert "Agent memory vector post-seed health passed" in workflow
    assert "SIQ_AGENT_MEMORY_EMBEDDING_BASE_URL" in workflow
    assert "SIQ_AGENT_MEMORY_MILVUS_COLLECTION" in workflow
    assert "SIQ_AGENT_MEMORY_VECTOR_HEALTH_REQUIRE_COLLECTION" in workflow
    assert "SIQ_MILVUS_PASSWORD" in workflow
    assert "pymilvus>=2.4" in workflow
    assert "runs-on: self-hosted" in workflow
    assert "POSTGRES_HOST_AUTH_METHOD" not in workflow
    assert "POSTGRES_PASSWORD:" in workflow
    assert "SIQ_PGPASSWORD:" in workflow
    assert "PGPASSWORD:" in workflow
    assert "127.0.0.1:15432:5432" in workflow
    assert "- 5432:5432" not in workflow
    assert "SIQ_PGPORT: '15432'" in workflow
    assert "SIQ_MARKET_POSTGRES_SAMPLE_ROOT" in workflow
    assert "SIQ_FINANCIAL_GOLDEN_PDF_ROOT" in workflow
    assert "SIQ_PARSER_FINANCIAL_PDF_GATE_MODE" in workflow
    assert "SIQ_PARSER_FINANCIAL_PDF_GATE_REQUIRED" in workflow
    assert "SIQ_PARSER_FINANCIAL_PDF_GATE_MODE: live-http" in workflow
    assert "SIQ_PARSER_FINANCIAL_PDF_GATE_REQUIRED: '1'" in workflow
    assert "Require live parser financial PDF inputs" in workflow
    assert "Parser Financial PDF Release" in workflow
    assert "timeout-minutes: 240" in workflow
    assert "Repository variable SIQ_PDF_PARSER_URL must identify the live PDF parser origin." in workflow
    assert "run_parser_financial_pdf_release_gate.py --mode contract" in ci_workflow
    assert "test_run_parser_financial_pdf_release_gate.py" in ci_workflow
    assert "clean: false" not in workflow


def test_release_workflow_requires_live_financial_qa_inputs():
    repo_root = Path(__file__).resolve().parents[3]
    workflow = (repo_root / ".github/workflows/market-postgres-release-gate.yml").read_text(encoding="utf-8")

    for contract in (
        "SIQ_LIVE_MODEL_BENCHMARK_MODE: live-http",
        "SIQ_LIVE_MODEL_BENCHMARK_REQUIRED: '1'",
        "SIQ_LIVE_MODEL_URL: ${{ vars.SIQ_LIVE_MODEL_URL }}",
        "SIQ_LIVE_MODEL_AUTH_TOKEN: ${{ secrets.SIQ_LIVE_MODEL_AUTH_TOKEN }}",
    ):
        assert contract in workflow
    preflight = workflow.split("- name: Require live financial QA inputs", 1)[1].split("- name:", 1)[0]
    assert "SIQ_LIVE_MODEL_URL" in preflight
    assert "SIQ_LIVE_MODEL_AUTH_TOKEN" in preflight
    assert "https://*" in preflight
    assert preflight.count("exit 1") >= 5


def test_release_workflow_requires_live_parser_financial_pdf_inputs():
    repo_root = Path(__file__).resolve().parents[3]
    workflow = (repo_root / ".github/workflows/market-postgres-release-gate.yml").read_text(encoding="utf-8")

    for contract in (
        "SIQ_PARSER_FINANCIAL_PDF_GATE_MODE: live-http",
        "SIQ_PARSER_FINANCIAL_PDF_GATE_REQUIRED: '1'",
        "SIQ_FINANCIAL_GOLDEN_PDF_ROOT: ${{ vars.SIQ_FINANCIAL_GOLDEN_PDF_ROOT }}",
        "SIQ_PDF_PARSER_URL: ${{ vars.SIQ_PDF_PARSER_URL }}",
    ):
        assert contract in workflow
    preflight = workflow.split("- name: Require live parser financial PDF inputs", 1)[1].split("- name:", 1)[0]
    assert "SIQ_FINANCIAL_GOLDEN_PDF_ROOT" in preflight
    assert "SIQ_PDF_PARSER_URL" in preflight
    assert "must be outside the checkout" in preflight
    assert "absolute HTTP(S) URL" in preflight
    assert preflight.count("exit 1") >= 7


@pytest.mark.parametrize(
    ("name", "value", "expected"),
    [
        ("SIQ_LIVE_MODEL_BENCHMARK_MODE", "disabled", "requires SIQ_LIVE_MODEL_BENCHMARK_MODE=live-http"),
        ("SIQ_LIVE_MODEL_BENCHMARK_REQUIRED", "0", "requires SIQ_LIVE_MODEL_BENCHMARK_REQUIRED=1"),
        ("SIQ_LIVE_MODEL_URL", "", "SIQ_LIVE_MODEL_URL must identify"),
        ("SIQ_LIVE_MODEL_URL", "http://live.example.test/v1/chat", "must use HTTPS"),
        ("SIQ_LIVE_MODEL_AUTH_TOKEN", "", "SIQ_LIVE_MODEL_AUTH_TOKEN must be configured"),
    ],
)
def test_release_workflow_live_preflight_rejects_missing_or_unsafe_inputs(name, value, expected):
    repo_root = Path(__file__).resolve().parents[3]
    workflow = (repo_root / ".github/workflows/market-postgres-release-gate.yml").read_text(encoding="utf-8")
    preflight = workflow.split("- name: Require live financial QA inputs", 1)[1].split("- name:", 1)[0]
    run_block = preflight.split("        run: |\n", 1)[1]
    script = "\n".join(line.removeprefix("          ") for line in run_block.splitlines())
    env = {
        **os.environ,
        "SIQ_LIVE_MODEL_BENCHMARK_MODE": "live-http",
        "SIQ_LIVE_MODEL_BENCHMARK_REQUIRED": "1",
        "SIQ_LIVE_MODEL_URL": "https://live.example.test/v1/chat",
        "SIQ_LIVE_MODEL_AUTH_TOKEN": "fake-test-token",
    }
    env[name] = value

    completed = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", script],
        cwd=repo_root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert expected in completed.stderr


def test_release_workflow_live_preflight_accepts_complete_https_inputs():
    repo_root = Path(__file__).resolve().parents[3]
    workflow = (repo_root / ".github/workflows/market-postgres-release-gate.yml").read_text(encoding="utf-8")
    preflight = workflow.split("- name: Require live financial QA inputs", 1)[1].split("- name:", 1)[0]
    run_block = preflight.split("        run: |\n", 1)[1]
    script = "\n".join(line.removeprefix("          ") for line in run_block.splitlines())
    env = {
        **os.environ,
        "SIQ_LIVE_MODEL_BENCHMARK_MODE": "live-http",
        "SIQ_LIVE_MODEL_BENCHMARK_REQUIRED": "1",
        "SIQ_LIVE_MODEL_URL": "https://live.example.test/v1/chat",
        "SIQ_LIVE_MODEL_AUTH_TOKEN": "fake-test-token",
    }

    completed = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", script],
        cwd=repo_root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


def test_release_workflow_requires_external_restore_matrix_inputs():
    repo_root = Path(__file__).resolve().parents[3]
    workflow = (repo_root / ".github/workflows/market-postgres-release-gate.yml").read_text(encoding="utf-8")

    for contract in (
        "SIQ_RESTORE_MATRIX_REQUIRED: '1'",
        "SIQ_RESTORE_MATRIX_BACKUP_DIR: ${{ vars.SIQ_RESTORE_MATRIX_BACKUP_DIR }}",
        "SIQ_RESTORE_MATRIX_ADMIN_URL: ${{ secrets.SIQ_RESTORE_MATRIX_ADMIN_URL }}",
        "SIQ_RESTORE_MATRIX_VOICEPRINT_TOMBSTONE_REQUIRED: '1'",
        "SIQ_MEETING_VOICEPRINT_TOMBSTONE_PATH: ${{ vars.SIQ_MEETING_VOICEPRINT_TOMBSTONE_PATH }}",
        "SIQ_MEETING_VOICEPRINT_TOMBSTONE_HMAC_KEY: ${{ secrets.SIQ_MEETING_VOICEPRINT_TOMBSTONE_HMAC_KEY }}",
        "SIQ_MEETING_VOICEPRINT_TOMBSTONE_EXPECTED_COUNT: ${{ vars.SIQ_MEETING_VOICEPRINT_TOMBSTONE_EXPECTED_COUNT }}",
        "SIQ_MEETING_VOICEPRINT_TOMBSTONE_EXPECTED_HEAD_HMAC: ${{ vars.SIQ_MEETING_VOICEPRINT_TOMBSTONE_EXPECTED_HEAD_HMAC }}",
    ):
        assert contract in workflow

    preflight = workflow.split("- name: Require external restore matrix inputs", 1)[1].split("- name:", 1)[0]
    for required_name in (
        "SIQ_RESTORE_MATRIX_BACKUP_DIR",
        "SIQ_RESTORE_MATRIX_ADMIN_URL",
        "SIQ_MEETING_VOICEPRINT_TOMBSTONE_PATH",
        "SIQ_MEETING_VOICEPRINT_TOMBSTONE_HMAC_KEY",
        "SIQ_MEETING_VOICEPRINT_TOMBSTONE_EXPECTED_COUNT",
        "SIQ_MEETING_VOICEPRINT_TOMBSTONE_EXPECTED_HEAD_HMAC",
    ):
        assert required_name in preflight
    assert 'if [[ -z "${!name:-}" ]]' in preflight
    assert 'if [[ ! -d "$SIQ_RESTORE_MATRIX_BACKUP_DIR"' in preflight
    assert 'if [[ ! -f "$SIQ_MEETING_VOICEPRINT_TOMBSTONE_PATH"' in preflight
    assert '"$SIQ_MEETING_VOICEPRINT_TOMBSTONE_EXPECTED_COUNT" =~ ^(0|[1-9][0-9]*)$' in preflight
    assert '"$SIQ_MEETING_VOICEPRINT_TOMBSTONE_EXPECTED_HEAD_HMAC" =~ ^[0-9a-fA-F]{64}$' in preflight
    assert 'case "$backup_dir/" in' in preflight
    assert 'case "$ledger_path" in' in preflight
    assert preflight.count("exit 1") >= 4


@pytest.mark.parametrize(
    "missing_name",
    [
        "SIQ_RESTORE_MATRIX_BACKUP_DIR",
        "SIQ_RESTORE_MATRIX_ADMIN_URL",
        "SIQ_MEETING_VOICEPRINT_TOMBSTONE_PATH",
        "SIQ_MEETING_VOICEPRINT_TOMBSTONE_HMAC_KEY",
        "SIQ_MEETING_VOICEPRINT_TOMBSTONE_EXPECTED_COUNT",
        "SIQ_MEETING_VOICEPRINT_TOMBSTONE_EXPECTED_HEAD_HMAC",
    ],
)
def test_release_workflow_restore_preflight_rejects_missing_inputs(missing_name, tmp_path):
    repo_root = Path(__file__).resolve().parents[3]
    workflow = (repo_root / ".github/workflows/market-postgres-release-gate.yml").read_text(encoding="utf-8")
    preflight = workflow.split("- name: Require external restore matrix inputs", 1)[1].split("- name:", 1)[0]
    run_block = preflight.split("        run: |\n", 1)[1]
    script = "\n".join(line.removeprefix("          ") for line in run_block.splitlines())
    workspace = tmp_path / "workspace"
    backup = tmp_path / "backup"
    ledger = tmp_path / "security" / "voiceprint-tombstones.jsonl"
    workspace.mkdir()
    backup.mkdir()
    ledger.parent.mkdir()
    ledger.touch()
    github_env = tmp_path / "github.env"
    env = {
        **os.environ,
        "GITHUB_WORKSPACE": str(workspace),
        "GITHUB_ENV": str(github_env),
        "SIQ_RESTORE_MATRIX_BACKUP_DIR": str(backup),
        "SIQ_RESTORE_MATRIX_ADMIN_URL": "postgresql://restore:secret@db.example.test/postgres",
        "SIQ_MEETING_VOICEPRINT_TOMBSTONE_PATH": str(ledger),
        "SIQ_MEETING_VOICEPRINT_TOMBSTONE_HMAC_KEY": "test-only-hmac",
        "SIQ_MEETING_VOICEPRINT_TOMBSTONE_EXPECTED_COUNT": "0",
        "SIQ_MEETING_VOICEPRINT_TOMBSTONE_EXPECTED_HEAD_HMAC": "0" * 64,
    }
    env[missing_name] = ""

    completed = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", script],
        cwd=repo_root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert f"{missing_name} must be configured" in completed.stderr
    assert not github_env.exists()


@pytest.mark.parametrize(
    ("count", "head_hmac", "message"),
    [
        ("-1", "0" * 64, "EXPECTED_COUNT must be a non-negative integer"),
        ("0", "g" * 64, "EXPECTED_HEAD_HMAC must contain 64 hexadecimal"),
        ("0", "1" * 64, "empty voiceprint tombstone ledger"),
    ],
)
def test_release_workflow_restore_preflight_rejects_invalid_voiceprint_checkpoint(
    count, head_hmac, message, tmp_path
):
    repo_root = Path(__file__).resolve().parents[3]
    workflow = (repo_root / ".github/workflows/market-postgres-release-gate.yml").read_text(
        encoding="utf-8"
    )
    preflight = workflow.split("- name: Require external restore matrix inputs", 1)[1].split(
        "- name:", 1
    )[0]
    run_block = preflight.split("        run: |\n", 1)[1]
    script = "\n".join(line.removeprefix("          ") for line in run_block.splitlines())
    workspace = tmp_path / "workspace"
    backup = tmp_path / "backup"
    ledger = tmp_path / "security" / "voiceprint-tombstones.jsonl"
    workspace.mkdir()
    backup.mkdir()
    ledger.parent.mkdir()
    ledger.touch()
    github_env = tmp_path / "github.env"
    env = {
        **os.environ,
        "GITHUB_WORKSPACE": str(workspace),
        "GITHUB_ENV": str(github_env),
        "SIQ_RESTORE_MATRIX_BACKUP_DIR": str(backup),
        "SIQ_RESTORE_MATRIX_ADMIN_URL": "postgresql://restore:secret@db.example.test/postgres",
        "SIQ_MEETING_VOICEPRINT_TOMBSTONE_PATH": str(ledger),
        "SIQ_MEETING_VOICEPRINT_TOMBSTONE_HMAC_KEY": "test-only-hmac",
        "SIQ_MEETING_VOICEPRINT_TOMBSTONE_EXPECTED_COUNT": count,
        "SIQ_MEETING_VOICEPRINT_TOMBSTONE_EXPECTED_HEAD_HMAC": head_hmac,
    }

    completed = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", script],
        cwd=repo_root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert message.lower() in completed.stderr.lower()
    assert not github_env.exists()


def test_release_gate_wrapper_blocks_required_pdf_gate_failure(tmp_path):
    repo_root = Path(__file__).resolve().parents[3]
    fake_python = tmp_path / "fake-python"
    log_path = tmp_path / "python-args.log"
    fake_python.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "$SIQ_FAKE_PYTHON_LOG"\n'
        'case "$*" in\n'
        "  *run_parser_financial_pdf_release_gate.py*) exit 1 ;;\n"
        "  *) exit 0 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    env = os.environ.copy()
    env.update(
        {
            "PYTHON": str(fake_python),
            "SIQ_FAKE_PYTHON_LOG": str(log_path),
            "SIQ_PARSER_FINANCIAL_PDF_GATE_MODE": "preflight",
            "SIQ_PARSER_FINANCIAL_PDF_GATE_REQUIRED": "1",
            "SIQ_FINANCIAL_GOLDEN_PDF_ROOT": str(tmp_path / "pdf-samples"),
        }
    )

    completed = subprocess.run(
        [
            "bash",
            "scripts/ops/run_market_postgres_release_gate.sh",
            "--mode",
            "contract",
            "--output-dir",
            str(tmp_path / "output"),
        ],
        cwd=repo_root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    calls = log_path.read_text(encoding="utf-8")
    assert "run_parser_financial_pdf_release_gate.py --mode preflight" in calls
    assert f"--pdf-root {tmp_path / 'pdf-samples'}" in calls
    assert "--output " in calls and "parser_financial_pdf_release.json" in calls


def test_release_gate_wrapper_blocks_any_executed_pdf_gate_failure(tmp_path):
    repo_root = Path(__file__).resolve().parents[3]
    fake_python = tmp_path / "fake-python"
    fake_python.write_text(
        "#!/usr/bin/env bash\n"
        'case "$*" in\n'
        "  *run_parser_financial_pdf_release_gate.py*) exit 1 ;;\n"
        "  *) exit 0 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    env = os.environ.copy()
    env.update(
        {
            "PYTHON": str(fake_python),
            "SIQ_PARSER_FINANCIAL_PDF_GATE_MODE": "preflight",
            "SIQ_PARSER_FINANCIAL_PDF_GATE_REQUIRED": "0",
        }
    )

    completed = subprocess.run(
        [
            "bash",
            "scripts/ops/run_market_postgres_release_gate.sh",
            "--mode",
            "contract",
            "--output-dir",
            str(tmp_path / "output"),
        ],
        cwd=repo_root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1


def test_release_gate_wrapper_requires_explicit_production_config_by_default(tmp_path):
    repo_root = Path(__file__).resolve().parents[3]
    fake_python = tmp_path / "fake-python"
    log_path = tmp_path / "python-args.log"
    config_path = tmp_path / "production.env"
    config_path.write_text("SIQ_DEPLOYMENT_PROFILE=development\n", encoding="utf-8")
    fake_python.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "$SIQ_FAKE_PYTHON_LOG"\n'
        'case "$*" in\n'
        "  *check_production_config.py*) exit 1 ;;\n"
        "  *) exit 0 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    env = os.environ.copy()
    env.update(
        {
            "PYTHON": str(fake_python),
            "SIQ_FAKE_PYTHON_LOG": str(log_path),
            "SIQ_PRODUCTION_CONFIG_FILE": str(config_path),
        }
    )

    completed = subprocess.run(
        ["bash", "scripts/ops/run_market_postgres_release_gate.sh", "--mode", "contract", "--output-dir", str(tmp_path / "output")],
        cwd=repo_root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    config_call = next(line for line in log_path.read_text(encoding="utf-8").splitlines() if "check_production_config.py" in line)
    assert "--required" in config_call


def test_release_gate_wrapper_loads_required_gate_plan_from_config_file(tmp_path):
    repo_root = Path(__file__).resolve().parents[3]
    fake_python = tmp_path / "fake-python"
    log_path = tmp_path / "python-args.log"
    config_path = tmp_path / "production.env"
    config_path.write_text(
        "\n".join(
            [
                "SIQ_PRODUCTION_CONFIG_REQUIRED=1",
                "SIQ_LIVE_MODEL_BENCHMARK_MODE=live-http",
                "SIQ_LIVE_MODEL_BENCHMARK_REQUIRED=1",
                "SIQ_LIVE_MODEL_URL=https://live.example.test/v1/runs",
                "SIQ_LIVE_MODEL_AUTH_TOKEN=live-token-from-file",
                "SIQ_PERMISSION_NEGATIVE_GATE_REQUIRED=1",
                "SIQ_PERMISSION_NEGATIVE_GATE_SKIP=0",
                "SIQ_RESTORE_MATRIX_REQUIRED=1",
                f"SIQ_RESTORE_MATRIX_BACKUP_DIR={tmp_path / 'backup'}",
                "SIQ_RESTORE_MATRIX_ADMIN_URL=postgresql://restore:secret@db.example.test/postgres",
                "SIQ_AGENT_MEMORY_VECTOR_PROBES_REQUIRED=1",
                "SIQ_AGENT_MEMORY_VECTOR_PROBES_SKIP=0",
                "SIQ_PERFORMANCE_COMPARISON_REQUIRED=1",
                f"SIQ_PERFORMANCE_BASELINE_REPORT={tmp_path / 'performance-v1.json'}",
            ]
        ),
        encoding="utf-8",
    )
    fake_python.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "$SIQ_FAKE_PYTHON_LOG"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    (tmp_path / "performance-v1.json").write_text('{"mode":"nightly","passed":true,"benchmarks":[]}\n', encoding="utf-8")
    env = os.environ.copy()
    for key in (
        "SIQ_PRODUCTION_CONFIG_REQUIRED",
        "SIQ_LIVE_MODEL_BENCHMARK_MODE",
        "SIQ_LIVE_MODEL_BENCHMARK_REQUIRED",
        "SIQ_LIVE_MODEL_URL",
        "SIQ_LIVE_MODEL_AUTH_TOKEN",
        "SIQ_PERMISSION_NEGATIVE_GATE_REQUIRED",
        "SIQ_PERMISSION_NEGATIVE_GATE_SKIP",
        "SIQ_RESTORE_MATRIX_REQUIRED",
        "SIQ_RESTORE_MATRIX_BACKUP_DIR",
        "SIQ_RESTORE_MATRIX_ADMIN_URL",
        "SIQ_AGENT_MEMORY_VECTOR_PROBES_REQUIRED",
        "SIQ_AGENT_MEMORY_VECTOR_PROBES_SKIP",
        "SIQ_PERFORMANCE_COMPARISON_REQUIRED",
        "SIQ_PERFORMANCE_BASELINE_REPORT",
    ):
        env.pop(key, None)
    env.update(
        {
            "PYTHON": str(fake_python),
            "SIQ_FAKE_PYTHON_LOG": str(log_path),
            "SIQ_PRODUCTION_CONFIG_FILE": str(config_path),
        }
    )

    completed = subprocess.run(
        [
            "bash",
            "scripts/ops/run_market_postgres_release_gate.sh",
            "--mode",
            "offline-postgres",
            "--output-dir",
            str(tmp_path / "output"),
        ],
        cwd=repo_root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    calls = log_path.read_text(encoding="utf-8").splitlines()
    live_call = next(call for call in calls if "run_live_financial_qa_benchmark.py --mode live-http" in call)
    assert "--required" in live_call
    assert "live-token-from-file" not in live_call
    assert any("run_restore_matrix.py --backup-dir" in call for call in calls)
    assert any("compare_financial_quality_baselines.py --baseline-performance" in call for call in calls)
    manifest_call = next(call for call in calls if "write_release_artifact_manifest.py" in call)
    assert "--required-artifact live_financial_qa_benchmark.json" in manifest_call
    assert all("live-token-from-file" not in call and "restore:secret" not in call for call in calls)


def test_release_gate_wrapper_requires_explicit_pdf_gate_mode(tmp_path):
    repo_root = Path(__file__).resolve().parents[3]
    env = os.environ.copy()
    env.update(
        {
            "SIQ_PARSER_FINANCIAL_PDF_GATE_MODE": "off",
            "SIQ_PARSER_FINANCIAL_PDF_GATE_REQUIRED": "1",
        }
    )

    completed = subprocess.run(
        [
            "bash",
            "scripts/ops/run_market_postgres_release_gate.sh",
            "--mode",
            "contract",
            "--output-dir",
            str(tmp_path / "output"),
        ],
        cwd=repo_root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert "requires an explicit preflight or live-http mode" in completed.stderr
    assert not (tmp_path / "output").exists()


def test_release_gate_wrapper_requires_production_config_file_when_required(tmp_path):
    repo_root = Path(__file__).resolve().parents[3]
    env = os.environ.copy()
    env.pop("SIQ_PRODUCTION_CONFIG_FILE", None)
    env.update({"SIQ_PRODUCTION_CONFIG_REQUIRED": "1"})

    completed = subprocess.run(
        [
            "bash",
            "scripts/ops/run_market_postgres_release_gate.sh",
            "--mode",
            "contract",
            "--output-dir",
            str(tmp_path / "output"),
        ],
        cwd=repo_root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert "requires SIQ_PRODUCTION_CONFIG_FILE" in completed.stderr
    assert not (tmp_path / "output").exists()


def test_release_gate_wrapper_does_not_disable_required_live_model_gate(tmp_path):
    repo_root = Path(__file__).resolve().parents[3]
    env = os.environ.copy()
    env.update(
        {
            "SIQ_LIVE_MODEL_BENCHMARK_MODE": "disabled",
            "SIQ_LIVE_MODEL_BENCHMARK_REQUIRED": "1",
        }
    )

    completed = subprocess.run(
        [
            "bash",
            "scripts/ops/run_market_postgres_release_gate.sh",
            "--mode",
            "contract",
            "--output-dir",
            str(tmp_path / "output"),
        ],
        cwd=repo_root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert "SIQ_LIVE_MODEL_BENCHMARK_MODE=live-http" in completed.stderr
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize(
    ("overrides", "removed", "expected"),
    [
        (
            {"SIQ_LIVE_MODEL_BENCHMARK_MODE": "disabled"},
            (),
            "requires SIQ_LIVE_MODEL_BENCHMARK_MODE=live-http",
        ),
        (
            {"SIQ_LIVE_MODEL_BENCHMARK_REQUIRED": "0"},
            (),
            "requires SIQ_LIVE_MODEL_BENCHMARK_REQUIRED=1",
        ),
        ({}, ("SIQ_LIVE_MODEL_URL",), "requires SIQ_LIVE_MODEL_URL"),
        (
            {"SIQ_LIVE_MODEL_URL": "http://live.example.test/v1/chat"},
            (),
            "requires SIQ_LIVE_MODEL_URL to use HTTPS",
        ),
        ({}, ("SIQ_LIVE_MODEL_AUTH_TOKEN",), "requires SIQ_LIVE_MODEL_AUTH_TOKEN"),
    ],
)
def test_offline_release_gate_requires_live_http_execution_inputs(tmp_path, overrides, removed, expected):
    repo_root = Path(__file__).resolve().parents[3]
    env = os.environ.copy()
    env.update(_required_live_release_env())
    env.update(overrides)
    for name in removed:
        env.pop(name, None)

    completed = subprocess.run(
        [
            "bash",
            "scripts/ops/run_market_postgres_release_gate.sh",
            "--mode",
            "offline-postgres",
            "--output-dir",
            str(tmp_path / "output"),
        ],
        cwd=repo_root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert expected in completed.stderr
    assert not (tmp_path / "output").exists()


def test_contract_gate_never_invokes_or_requires_live_financial_qa(tmp_path):
    repo_root = Path(__file__).resolve().parents[3]
    fake_python = tmp_path / "fake-python"
    log_path = tmp_path / "python-args.log"
    fake_python.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "$SIQ_FAKE_PYTHON_LOG"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    env = os.environ.copy()
    for name in _required_live_release_env():
        env.pop(name, None)
    env.update(
        {
            "PYTHON": str(fake_python),
            "SIQ_FAKE_PYTHON_LOG": str(log_path),
            "SIQ_LIVE_MODEL_BENCHMARK_MODE": "disabled",
            "SIQ_LIVE_MODEL_BENCHMARK_REQUIRED": "0",
        }
    )

    completed = subprocess.run(
        [
            "bash",
            "scripts/ops/run_market_postgres_release_gate.sh",
            "--mode",
            "contract",
            "--output-dir",
            str(tmp_path / "output"),
        ],
        cwd=repo_root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    calls = log_path.read_text(encoding="utf-8").splitlines()
    assert all("run_live_financial_qa_benchmark.py" not in call for call in calls)
    manifest_call = next(call for call in calls if "write_release_artifact_manifest.py" in call)
    assert "live_financial_qa_benchmark.json" not in manifest_call


def test_contract_gate_rejects_live_http_configuration(tmp_path):
    repo_root = Path(__file__).resolve().parents[3]
    env = os.environ.copy()
    env.update(_required_live_release_env())
    env["SIQ_LIVE_MODEL_BENCHMARK_REQUIRED"] = "0"

    completed = subprocess.run(
        [
            "bash",
            "scripts/ops/run_market_postgres_release_gate.sh",
            "--mode",
            "contract",
            "--output-dir",
            str(tmp_path / "output"),
        ],
        cwd=repo_root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert "The contract gate is deterministic" in completed.stderr
    assert not (tmp_path / "output").exists()


def test_release_gate_wrapper_does_not_skip_required_permission_gate(tmp_path):
    repo_root = Path(__file__).resolve().parents[3]
    env = os.environ.copy()
    env.update(
        {
            "SIQ_PERMISSION_NEGATIVE_GATE_SKIP": "1",
            "SIQ_PERMISSION_NEGATIVE_GATE_REQUIRED": "1",
        }
    )

    completed = subprocess.run(
        [
            "bash",
            "scripts/ops/run_market_postgres_release_gate.sh",
            "--mode",
            "contract",
            "--output-dir",
            str(tmp_path / "output"),
        ],
        cwd=repo_root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert "cannot bypass a required permission negative gate" in completed.stderr
    assert not (tmp_path / "output").exists()


def test_release_gate_wrapper_does_not_accept_required_permission_gate_in_contract_mode(tmp_path):
    repo_root = Path(__file__).resolve().parents[3]
    env = os.environ.copy()
    env.update(
        {
            "SIQ_PERMISSION_NEGATIVE_GATE_SKIP": "0",
            "SIQ_PERMISSION_NEGATIVE_GATE_REQUIRED": "1",
        }
    )

    completed = subprocess.run(
        [
            "bash",
            "scripts/ops/run_market_postgres_release_gate.sh",
            "--mode",
            "contract",
            "--output-dir",
            str(tmp_path / "output"),
        ],
        cwd=repo_root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert "requires --mode offline-postgres" in completed.stderr
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize(
    "required_name",
    ["SIQ_AGENT_MEMORY_VECTOR_PROBES_REQUIRED", "SIQ_AGENT_MEMORY_VECTOR_SEED"],
)
def test_release_gate_wrapper_does_not_accept_required_vector_work_in_contract_mode(
    required_name, tmp_path
):
    repo_root = Path(__file__).resolve().parents[3]
    env = os.environ.copy()
    env.update({required_name: "1"})

    completed = subprocess.run(
        [
            "bash",
            "scripts/ops/run_market_postgres_release_gate.sh",
            "--mode",
            "contract",
            "--output-dir",
            str(tmp_path / "output"),
        ],
        cwd=repo_root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert "requires --mode offline-postgres" in completed.stderr
    assert not (tmp_path / "output").exists()


def test_release_gate_wrapper_requires_restore_matrix_inputs_when_required(tmp_path):
    repo_root = Path(__file__).resolve().parents[3]
    env = os.environ.copy()
    env.update({"SIQ_RESTORE_MATRIX_REQUIRED": "1"})
    env.pop("SIQ_RESTORE_MATRIX_BACKUP_DIR", None)
    env.pop("SIQ_RESTORE_MATRIX_ADMIN_URL", None)

    completed = subprocess.run(
        ["bash", "scripts/ops/run_market_postgres_release_gate.sh", "--mode", "contract", "--output-dir", str(tmp_path / "output")],
        cwd=repo_root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert "requires SIQ_RESTORE_MATRIX_BACKUP_DIR" in completed.stderr
    assert not (tmp_path / "output").exists()


def test_release_gate_wrapper_requires_restore_admin_url_for_backup_dir(tmp_path):
    repo_root = Path(__file__).resolve().parents[3]
    env = os.environ.copy()
    env.update({"SIQ_RESTORE_MATRIX_BACKUP_DIR": str(tmp_path / "backup")})
    env.pop("SIQ_RESTORE_MATRIX_ADMIN_URL", None)

    completed = subprocess.run(
        ["bash", "scripts/ops/run_market_postgres_release_gate.sh", "--mode", "contract", "--output-dir", str(tmp_path / "output")],
        cwd=repo_root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert "requires SIQ_RESTORE_MATRIX_ADMIN_URL" in completed.stderr
    assert not (tmp_path / "output").exists()


def test_release_gate_wrapper_blocks_strict_market_ingestion_failure(tmp_path):
    repo_root = Path(__file__).resolve().parents[3]
    fake_python = tmp_path / "fake-python"
    log_path = tmp_path / "python-args.log"
    fake_python.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "$SIQ_FAKE_PYTHON_LOG"\n'
        'case "$*" in\n'
        "  *run_market_ingestion_eval.py*) exit 1 ;;\n"
        "  *) exit 0 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    env = os.environ.copy()
    env.update({"PYTHON": str(fake_python), "SIQ_FAKE_PYTHON_LOG": str(log_path)})

    completed = subprocess.run(
        [
            "bash",
            "scripts/ops/run_market_postgres_release_gate.sh",
            "--mode",
            "contract",
            "--output-dir",
            str(tmp_path / "output"),
        ],
        cwd=repo_root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    ingestion_call = next(
        line for line in log_path.read_text(encoding="utf-8").splitlines() if "run_market_ingestion_eval.py" in line
    )
    assert "--strict" in ingestion_call
    assert "--portable" in ingestion_call
    assert "--case-root eval_datasets/market_ingestion_contract/cases" in ingestion_call


def test_release_gate_wrapper_blocks_fixture_contamination_audit_failure(tmp_path):
    repo_root = Path(__file__).resolve().parents[3]
    fake_python = tmp_path / "fake-python"
    log_path = tmp_path / "python-args.log"
    fake_python.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "$SIQ_FAKE_PYTHON_LOG"\n'
        'case "$*" in\n'
        "  *audit_market_postgres_fixture_contamination.py*) exit 1 ;;\n"
        "  *) exit 0 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    env = os.environ.copy()
    env.update(_required_live_release_env())
    env.update({"PYTHON": str(fake_python), "SIQ_FAKE_PYTHON_LOG": str(log_path)})

    completed = subprocess.run(
        [
            "bash",
            "scripts/ops/run_market_postgres_release_gate.sh",
            "--mode",
            "offline-postgres",
            "--output-dir",
            str(tmp_path / "output"),
        ],
        cwd=repo_root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    calls = log_path.read_text(encoding="utf-8").splitlines()
    audit_call = next(line for line in calls if "audit_market_postgres_fixture_contamination.py" in line)
    manifest_call = next(line for line in calls if "write_release_artifact_manifest.py" in line)
    assert "--json-output" in audit_call
    assert "--markdown-output" in audit_call
    assert "--required-artifact fixture-contamination-audit.json" in manifest_call


def test_release_gate_wrapper_requires_versioned_performance_baseline_before_output(tmp_path):
    repo_root = Path(__file__).resolve().parents[3]
    env = os.environ.copy()
    env["SIQ_PERFORMANCE_COMPARISON_REQUIRED"] = "1"
    env.pop("SIQ_PERFORMANCE_BASELINE_REPORT", None)

    completed = subprocess.run(
        [
            "bash",
            "scripts/ops/run_market_postgres_release_gate.sh",
            "--mode",
            "contract",
            "--output-dir",
            str(tmp_path / "output"),
        ],
        cwd=repo_root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert "requires SIQ_PERFORMANCE_BASELINE_REPORT" in completed.stderr
    assert not (tmp_path / "output").exists()


def test_release_gate_wrapper_compares_before_and_current_performance(tmp_path):
    repo_root = Path(__file__).resolve().parents[3]
    fake_python = tmp_path / "fake-python"
    log_path = tmp_path / "python-args.log"
    baseline = tmp_path / "performance-v1.json"
    baseline.write_text('{"mode":"contract","passed":true,"benchmarks":[]}\n', encoding="utf-8")
    fake_python.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "$SIQ_FAKE_PYTHON_LOG"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    env = os.environ.copy()
    env.update(
        {
            "PYTHON": str(fake_python),
            "SIQ_FAKE_PYTHON_LOG": str(log_path),
            "SIQ_PERFORMANCE_COMPARISON_REQUIRED": "1",
            "SIQ_PERFORMANCE_BASELINE_REPORT": str(baseline),
        }
    )

    completed = subprocess.run(
        [
            "bash",
            "scripts/ops/run_market_postgres_release_gate.sh",
            "--mode",
            "contract",
            "--output-dir",
            str(tmp_path / "output"),
        ],
        cwd=repo_root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    calls = log_path.read_text(encoding="utf-8").splitlines()
    comparison_call = next(line for line in calls if "compare_financial_quality_baselines.py" in line)
    manifest_call = next(line for line in calls if "write_release_artifact_manifest.py" in line)
    assert f"--baseline-performance {baseline}" in comparison_call
    assert "--current-performance" in comparison_call
    assert "performance_baseline_contract.json" in comparison_call
    assert "--required-artifact performance-comparison.json" in manifest_call


def test_release_gate_wrapper_cannot_skip_required_vector_probes(tmp_path):
    repo_root = Path(__file__).resolve().parents[3]
    env = os.environ.copy()
    env.update(_required_live_release_env())
    env.update(
        {
            "SIQ_AGENT_MEMORY_VECTOR_PROBES_REQUIRED": "1",
            "SIQ_AGENT_MEMORY_VECTOR_PROBES_SKIP": "1",
        }
    )

    completed = subprocess.run(
        [
            "bash",
            "scripts/ops/run_market_postgres_release_gate.sh",
            "--mode",
            "offline-postgres",
            "--output-dir",
            str(tmp_path / "output"),
        ],
        cwd=repo_root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert "cannot bypass required production vector probes" in completed.stderr
    assert not (tmp_path / "output").exists()


def test_release_gate_wrapper_dependencies_exist_and_clean_checkout_requires_git_tracking():
    repo_root = Path(__file__).resolve().parents[3]
    wrapper = (repo_root / "scripts/ops/run_market_postgres_release_gate.sh").read_text(encoding="utf-8")
    workflow = (repo_root / ".github/workflows/market-postgres-release-gate.yml").read_text(encoding="utf-8")
    block = re.search(r"RELEASE_GATE_REPO_SCRIPTS=\(\n(?P<body>.*?)\n\)", wrapper, re.DOTALL)
    assert block is not None
    dependencies = {line.strip() for line in block.group("body").splitlines() if line.strip()}
    referenced = set(re.findall(r"(?:scripts/[A-Za-z0-9_./-]+\.py)", wrapper))

    assert referenced.issubset(dependencies)
    assert all((repo_root / dependency).is_file() for dependency in dependencies)
    assert "git ls-files --error-unmatch" in workflow
    for dependency in (
        "scripts/maintenance/check_production_config.py",
        "scripts/maintenance/run_live_financial_qa_benchmark.py",
        "scripts/maintenance/run_permission_negative_report.py",
        "scripts/ops/run_restore_matrix.py",
    ):
        assert dependency in workflow


@pytest.mark.parametrize(
    "name",
    [
        "SIQ_PARSER_FINANCIAL_PDF_GATE_REQUIRED",
        "SIQ_PRODUCTION_CONFIG_REQUIRED",
        "SIQ_LIVE_MODEL_BENCHMARK_REQUIRED",
        "SIQ_PERMISSION_NEGATIVE_GATE_SKIP",
        "SIQ_PERMISSION_NEGATIVE_GATE_REQUIRED",
        "SIQ_RESTORE_MATRIX_REQUIRED",
        "SIQ_AGENT_MEMORY_VECTOR_PROBES_REQUIRED",
        "SIQ_AGENT_MEMORY_VECTOR_PROBES_SKIP",
        "SIQ_PERFORMANCE_COMPARISON_REQUIRED",
    ],
)
def test_release_gate_wrapper_rejects_unknown_critical_boolean(name, tmp_path):
    repo_root = Path(__file__).resolve().parents[3]
    env = os.environ.copy()
    env[name] = "tru"

    completed = subprocess.run(
        ["bash", "scripts/ops/run_market_postgres_release_gate.sh", "--mode", "contract", "--output-dir", str(tmp_path / "output")],
        cwd=repo_root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert f"{name} must be an explicit boolean" in completed.stderr
    assert not (tmp_path / "output").exists()


def test_release_gate_wrapper_passes_vector_probe_args_without_endpoint_on_cli(tmp_path):
    repo_root = Path(__file__).resolve().parents[3]
    fake_python = tmp_path / "fake-python"
    log_path = tmp_path / "python-args.log"
    fake_python.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "$SIQ_FAKE_PYTHON_LOG"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    env = os.environ.copy()
    env.update(_required_live_release_env())
    env.update(
        {
            "PYTHON": str(fake_python),
            "SIQ_FAKE_PYTHON_LOG": str(log_path),
            "SIQ_AGENT_MEMORY_VECTOR_PROBES_REQUIRED": "1",
            "SIQ_AGENT_MEMORY_EMBEDDING_BASE_URL": "http://embedding.internal/v1?api_key=secret",
            "SIQ_AGENT_MEMORY_EMBEDDING_MODEL": "fake-embedding-model",
            "SIQ_AGENT_MEMORY_EMBEDDING_TIMEOUT": "7",
            "SIQ_AGENT_MEMORY_EMBEDDING_PROBE_TEXTS": "4",
            "SIQ_AGENT_MEMORY_MILVUS_COLLECTION": "siq_agent_memory_perf",
            "SIQ_AGENT_MEMORY_RETRIEVAL_CASES": "eval_datasets/agent_memory/cases.json",
            "SIQ_AGENT_MEMORY_RETRIEVAL_TOP_K": "6",
            "SIQ_AGENT_MEMORY_RETRIEVAL_MAX_CASES": "5",
        }
    )

    subprocess.run(
        [
            "bash",
            "scripts/ops/run_market_postgres_release_gate.sh",
            "--mode",
            "offline-postgres",
            "--output-dir",
            str(tmp_path / "out"),
        ],
        cwd=repo_root,
        env=env,
        check=True,
    )

    calls = log_path.read_text(encoding="utf-8").splitlines()
    perf_call = next(line for line in calls if "run_performance_baseline.py" in line)
    health_call = next(line for line in calls if "check_agent_memory_vector_health.py" in line)
    assert "--require-milvus" in health_call
    assert "--collection siq_agent_memory_perf" in health_call
    assert "--require-agent-memory-vector-probes" in perf_call
    assert "--require-ic-vector-retrieval-probe" in perf_call
    assert "--agent-memory-embedding-model fake-embedding-model" in perf_call
    assert "--agent-memory-embedding-timeout 7" in perf_call
    assert "--agent-memory-embedding-probe-texts 4" in perf_call
    assert "--agent-memory-vector-collection siq_agent_memory_perf" in perf_call
    assert "--agent-memory-retrieval-cases eval_datasets/agent_memory/cases.json" in perf_call
    assert "--agent-memory-retrieval-top-k 6" in perf_call
    assert "--agent-memory-retrieval-max-cases 5" in perf_call
    assert "--agent-memory-embedding-base-url" not in perf_call
    assert "embedding.internal" not in perf_call
    assert "secret" not in perf_call
    assert "embedding.internal" not in health_call
    assert "secret" not in health_call
    ingestion_call = next(line for line in calls if "run_market_ingestion_eval.py" in line)
    assert "--evidence-profile final-v5-staging" in ingestion_call
    assert "--strict" in ingestion_call


def test_release_gate_wrapper_seeds_vector_collection_without_endpoint_on_cli(tmp_path):
    repo_root = Path(__file__).resolve().parents[3]
    fake_python = tmp_path / "fake-python"
    log_path = tmp_path / "python-args.log"
    output_dir = tmp_path / "out"
    fake_python.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "$SIQ_FAKE_PYTHON_LOG"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    env = os.environ.copy()
    env.update(_required_live_release_env())
    env.update(
        {
            "PYTHON": str(fake_python),
            "SIQ_FAKE_PYTHON_LOG": str(log_path),
            "SIQ_AGENT_MEMORY_VECTOR_SEED": "1",
            "SIQ_AGENT_MEMORY_EMBEDDING_BASE_URL": "http://embedding.internal/v1?api_key=secret",
            "SIQ_AGENT_MEMORY_EMBEDDING_MODEL": "fake-embedding-model",
            "SIQ_AGENT_MEMORY_EMBEDDING_DIM": "8",
            "SIQ_AGENT_MEMORY_MILVUS_COLLECTION": "siq_agent_memory_perf",
            "SIQ_AGENT_MEMORY_VECTOR_SEED_PROFILES": "siq_assistant,siq_ic_chairman",
            "SIQ_AGENT_MEMORY_VECTOR_SEED_BATCH_SIZE": "2",
            "SIQ_AGENT_MEMORY_VECTOR_SEED_TIMEOUT": "9",
        }
    )

    subprocess.run(
        [
            "bash",
            "scripts/ops/run_market_postgres_release_gate.sh",
            "--mode",
            "offline-postgres",
            "--output-dir",
            str(output_dir),
        ],
        cwd=repo_root,
        env=env,
        check=True,
    )

    calls = log_path.read_text(encoding="utf-8").splitlines()
    seed_call = next(line for line in calls if "ingest_agent_memory_to_milvus.py" in line)
    health_calls = [line for line in calls if "check_agent_memory_vector_health.py" in line]
    assert len(health_calls) == 2
    preflight_call, post_seed_call = health_calls
    assert f"--output {output_dir}/agent_memory_vector_preflight.json" in preflight_call
    assert f"--markdown {output_dir}/agent_memory_vector_preflight.md" in preflight_call
    assert "--require-milvus" in preflight_call
    assert "--require-collection" not in preflight_call
    assert f"--output {output_dir}/agent_memory_vector_post_seed_health.json" in post_seed_call
    assert f"--markdown {output_dir}/agent_memory_vector_post_seed_health.md" in post_seed_call
    assert "--require-milvus" in post_seed_call
    assert "--require-collection" in post_seed_call
    assert f"--output {output_dir}/agent_memory_milvus_seed.json" in seed_call
    assert f"--markdown {output_dir}/agent_memory_milvus_seed.md" in seed_call
    assert "--require-configured-embed-url" in seed_call
    assert "--collection siq_agent_memory_perf" in seed_call
    assert "--embed-model fake-embedding-model" in seed_call
    assert "--vector-dim 8" in seed_call
    assert "--profiles siq_assistant,siq_ic_chairman" in seed_call
    assert "--batch-size 2" in seed_call
    assert "--timeout 9" in seed_call
    assert "--flush" in seed_call
    assert "--embed-url" not in seed_call
    assert "embedding.internal" not in seed_call
    assert "secret" not in seed_call
    assert "embedding.internal" not in preflight_call + post_seed_call
    assert "secret" not in preflight_call + post_seed_call


def test_release_gate_wrapper_skips_post_seed_health_for_seed_dry_run(tmp_path):
    repo_root = Path(__file__).resolve().parents[3]
    fake_python = tmp_path / "fake-python"
    log_path = tmp_path / "python-args.log"
    fake_python.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "$SIQ_FAKE_PYTHON_LOG"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    env = os.environ.copy()
    env.update(_required_live_release_env())
    env.update(
        {
            "PYTHON": str(fake_python),
            "SIQ_FAKE_PYTHON_LOG": str(log_path),
            "SIQ_AGENT_MEMORY_VECTOR_SEED": "1",
            "SIQ_AGENT_MEMORY_VECTOR_SEED_DRY_RUN": "1",
        }
    )

    subprocess.run(
        [
            "bash",
            "scripts/ops/run_market_postgres_release_gate.sh",
            "--mode",
            "offline-postgres",
            "--output-dir",
            str(tmp_path / "out"),
        ],
        cwd=repo_root,
        env=env,
        check=True,
    )

    calls = log_path.read_text(encoding="utf-8").splitlines()
    seed_call = next(line for line in calls if "ingest_agent_memory_to_milvus.py" in line)
    health_calls = [line for line in calls if "check_agent_memory_vector_health.py" in line]
    assert "--dry-run" in seed_call
    assert len(health_calls) == 1
    assert "agent_memory_vector_preflight.json" in health_calls[0]
    assert "agent_memory_vector_post_seed_health.json" not in health_calls[0]


def test_release_gate_wrapper_uses_agent_memory_contract_defaults(tmp_path):
    repo_root = Path(__file__).resolve().parents[3]
    fake_python = tmp_path / "fake-python"
    log_path = tmp_path / "python-args.log"
    fake_python.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "$SIQ_FAKE_PYTHON_LOG"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    env = os.environ.copy()
    env.update(_required_live_release_env())
    env.update(
        {
            "PYTHON": str(fake_python),
            "SIQ_FAKE_PYTHON_LOG": str(log_path),
            "SIQ_AGENT_MEMORY_VECTOR_SEED": "1",
        }
    )
    for name in (
        "SIQ_AGENT_MEMORY_RETRIEVAL_CASES",
        "SIQ_AGENT_MEMORY_VECTOR_SEED_PROFILES",
        "SIQ_AGENT_MEMORY_EMBEDDING_BASE_URL",
    ):
        env.pop(name, None)

    subprocess.run(
        [
            "bash",
            "scripts/ops/run_market_postgres_release_gate.sh",
            "--mode",
            "offline-postgres",
            "--output-dir",
            str(tmp_path / "out"),
        ],
        cwd=repo_root,
        env=env,
        check=True,
    )

    calls = log_path.read_text(encoding="utf-8").splitlines()
    seed_call = next(line for line in calls if "ingest_agent_memory_to_milvus.py" in line)
    perf_call = next(line for line in calls if "run_performance_baseline.py" in line)
    assert "--profiles siq_assistant,siq_ic_legal_scanner,siq_ic_chairman" in seed_call
    assert "--agent-memory-retrieval-cases eval_datasets/agent_memory_retrieval_contract/cases.json" in perf_call


def test_agent_memory_retrieval_contract_fixture_aligns_with_seed_profiles():
    repo_root = Path(__file__).resolve().parents[3]
    cases_path = repo_root / "eval_datasets/agent_memory_retrieval_contract/cases.json"
    cases = json.loads(cases_path.read_text(encoding="utf-8"))

    assert len(cases) == 3
    profiles = {str(item["profile"]) for item in cases}
    assert profiles == {"siq_assistant", "siq_ic_legal_scanner", "siq_ic_chairman"}
    for item in cases:
        assert item["profile"] in str(item["expected_path_contains"])
        assert (repo_root / "agents/hermes/profiles" / str(item["profile"])).is_dir()
