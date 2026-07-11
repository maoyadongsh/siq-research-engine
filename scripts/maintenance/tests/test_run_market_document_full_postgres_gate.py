import importlib.util
import json
from pathlib import Path


def _load_gate_module():
    source = Path(__file__).resolve().parents[1] / "run_market_document_full_postgres_gate.py"
    spec = importlib.util.spec_from_file_location("run_market_document_full_postgres_gate_under_test", source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _summary(*, passed=True, acceptance_passed=False, db_results=None, parity_results=None, production_agent_results=None):
    return {
        "schema_version": "market_document_full_postgres_backtest_results_v1",
        "passed": passed,
        "acceptance_passed": acceptance_passed,
        "passed_count": 1 if passed else 0,
        "case_count": 1,
        "acceptance_requirements": {
            "fixture_contract": passed,
            "postgres_import_idempotency": acceptance_passed,
        },
        "summary": {"postgres_import_executed": bool(db_results)},
        "results": [],
        "agent_results": [],
        "db_results": db_results or [],
        "production_sample_db_results": [],
        "production_sample_db_coexistence_results": [],
        "production_agent_results": production_agent_results or [],
        "wiki_postgres_parity_results": parity_results or [],
        "production_sample_wiki_postgres_parity_results": [],
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

    monkeypatch.setattr(module, "run_cases", fake_run_cases)
    monkeypatch.setattr(module, "write_report", fake_write_report)
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


def test_contract_mode_default_outputs_stay_under_artifacts():
    module = _load_gate_module()
    args = module._build_parser().parse_args(["--mode", "contract"])

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


def test_offline_postgres_mode_uses_strict_acceptance_gate(monkeypatch, tmp_path):
    module = _load_gate_module()
    calls, writes = _install_fakes(monkeypatch, module, _summary(passed=True, acceptance_passed=True))

    exit_code = module.main(
        [
            "--mode",
            "offline-postgres",
            "--database-url",
            "postgresql://postgres:secret@db/not_market",
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
        }
    ]
    assert writes[0]["output_path"] == tmp_path / "market_document_full_postgres_offline_postgres_gate.json"
    assert writes[0]["markdown_path"] == tmp_path / "market_document_full_postgres_offline_postgres_gate.md"


def test_offline_postgres_mode_requires_acceptance_passed(monkeypatch, tmp_path):
    module = _load_gate_module()
    _install_fakes(monkeypatch, module, _summary(passed=True, acceptance_passed=False))

    exit_code = module.main(["--mode", "offline-postgres", "--output-dir", str(tmp_path)])

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

    exit_code = module.main(["--mode", "offline-postgres", "--output-dir", str(tmp_path)])

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "Failed acceptance requirements: postgres_import_idempotency" in output
    assert "db_results: US us-real-1 failed: missing evidence_citations" in output
    assert "production_agent_results: HK hk-agent-revenue revenue: value_mismatch" in output


def test_release_gate_wrapper_runs_financial_qa_benchmarks():
    repo_root = Path(__file__).resolve().parents[3]
    wrapper = (repo_root / "scripts/ops/run_market_postgres_release_gate.sh").read_text(encoding="utf-8")
    workflow = (repo_root / ".github/workflows/market-postgres-release-gate.yml").read_text(encoding="utf-8")

    assert "run_market_document_full_postgres_gate.py" in wrapper
    assert wrapper.count("run_financial_qa_benchmark.py") == 2
    assert "--mode trace-offline" in wrapper
    assert "--mode wiki-static" in wrapper
    assert "financial_qa_benchmark_trace_offline.json" in wrapper
    assert "financial_qa_benchmark_wiki_static.json" in wrapper
    assert "Financial QA trace-offline" in workflow
    assert "Financial QA wiki-static" in workflow
    assert "runs-on: self-hosted" in workflow
    assert "POSTGRES_HOST_AUTH_METHOD" not in workflow
    assert "POSTGRES_PASSWORD:" in workflow
    assert "SIQ_PGPASSWORD:" in workflow
    assert "PGPASSWORD:" in workflow
    assert "127.0.0.1:15432:5432" in workflow
    assert "- 5432:5432" not in workflow
    assert "SIQ_PGPORT: '15432'" in workflow
