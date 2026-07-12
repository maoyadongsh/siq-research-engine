import importlib.util
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_module():
    source = Path(__file__).resolve().parents[1] / "run_financial_qa_benchmark.py"
    spec = importlib.util.spec_from_file_location("run_financial_qa_benchmark_under_test", source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_answer_audit_module():
    api_root = REPO_ROOT / "apps" / "api"
    if str(api_root) not in sys.path:
        sys.path.insert(0, str(api_root))
    from services import agent_runtime_answer_audit

    return agent_runtime_answer_audit


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


def _case(**overrides):
    payload = {
        "schema_version": "siq_financial_qa_benchmark_case_v1",
        "case_id": "case-1",
        "tier": "P0",
        "market": "HK",
        "company_id": "HK:00700",
        "filing_id": "HK:00700:2025-annual",
        "period": "2025-12-31",
        "question": "收入是多少？",
        "source_policy": {
            "primary": "wiki_metrics",
            "allow_postgres_fallback": True,
            "allowed_fallback_reasons": ["wiki_missing"],
            "forbid_semantic_numeric_source": True,
        },
        "expected_facts": [
            {
                "canonical_name": "revenue",
                "statement_type": "income_statement",
                "period": "2025-12-31",
                "value": "100",
                "raw_value": "100",
                "unit": "RMB million",
                "currency": "RMB",
                "tolerance_ratio": 0,
                "required_source_types": ["wiki_metrics"],
                "fallback_source_types": ["postgresql_agent_view"],
                "required_evidence": ["table_index", "quote"],
            }
        ],
        "required_evidence": [{"table_index": 4, "quote": "Revenue 100"}],
        "expected_guardrail": {"should_answer": True},
        "expected_trace": {"must_have_wiki_facts": True, "fallback_reason": None},
    }
    payload.update(overrides)
    return payload


def _trace(**overrides):
    payload = {
        "schema_version": "siq_answer_audit_trace_v1",
        "question_id": "case-1",
        "resolved_company": {"market": "HK", "id": "HK:00700"},
        "resolved_period": {"period": "2025-12-31", "filing_id": "HK:00700:2025-annual"},
        "query_plan": {"mode": "wiki_first"},
        "wiki_facts": [
            {
                "source_type": "wiki_metrics",
                "canonical_name": "revenue",
                "statement_type": "income_statement",
                "period": "2025-12-31",
                "value": "100",
                "raw_value": "100",
                "unit": "RMB million",
                "currency": "RMB",
                "table_index": 4,
                "quote": "Revenue 100",
            }
        ],
        "postgres_facts": [],
        "fallback_reason": None,
        "calculator_runs": [],
        "citations": [],
        "guardrail_result": {"blocked": False, "has_wiki_facts": True},
    }
    payload.update(overrides)
    return payload


def test_trace_offline_benchmark_passes_default_dataset():
    module = _load_module()

    report = module.run_benchmark(mode="trace-offline")

    assert report["passed"] is True
    assert report["summary"]["key_fact_accuracy"] == 1.0
    assert report["summary"]["source_policy_pass_rate"] == 1.0
    assert report["summary"]["evidence_coverage_rate"] == 1.0


def test_trace_offline_consumes_runtime_built_answer_audit_trace(tmp_path):
    module = _load_module()
    audit = _load_answer_audit_module()
    case_root = tmp_path / "bench"
    case = _case(
        case_id="runtime-trace-1",
        expected_trace={"must_have_wiki_facts": True, "fallback_reason": None},
    )
    trace = audit.build_answer_audit_trace(
        message="question_id=runtime-trace-1 收入是多少？",
        final_reply=(
            "[D1] source_type=wiki_metrics, company_id=HK:00700, filing_id=HK:00700:2025-annual, "
            "statement_type=income_statement, canonical_name=revenue, period=2025-12-31, "
            "value=100, raw_value=100, unit=RMB million, currency=RMB, table_index=4, quote=Revenue 100"
        ),
        context={
            "company": {"market": "HK", "id": "HK:00700"},
            "resolved_period": {"period_end": "2025-12-31", "filing_id": "HK:00700:2025-annual"},
            "query_plan": {"mode": "wiki_first", "allow_postgres_fallback": True},
        },
        profile="siq_assistant",
        session_id="user-1-assistant-runtime-trace",
        enforce_evidence_contract=True,
    )
    _write_jsonl(case_root / "cases.jsonl", [case])
    _write_jsonl(case_root / "traces.jsonl", [trace])

    report = module.run_benchmark(case_root=case_root, trace_log=case_root / "traces.jsonl", mode="trace-offline")

    assert report["passed"] is True
    assert report["results"][0]["facts"][0]["source_bucket"] == "wiki_facts"
    assert report["summary"]["source_policy_pass_rate"] == 1.0


def test_trace_offline_blocks_postgres_fact_without_fallback_reason(tmp_path):
    module = _load_module()
    case_root = tmp_path / "bench"
    _write_jsonl(case_root / "cases.jsonl", [_case()])
    _write_jsonl(
        case_root / "traces.jsonl",
        [
            _trace(
                wiki_facts=[],
                postgres_facts=[
                    {
                        "source_type": "postgresql_agent_view",
                        "canonical_name": "revenue",
                        "statement_type": "income_statement",
                        "period": "2025-12-31",
                        "value": "100",
                        "raw_value": "100",
                        "unit": "RMB million",
                        "currency": "RMB",
                        "table_index": 4,
                        "quote": "Revenue 100",
                    }
                ],
            )
        ],
    )

    report = module.run_benchmark(case_root=case_root, trace_log=case_root / "traces.jsonl", mode="trace-offline")

    assert report["passed"] is False
    assert "postgres_facts present without fallback_reason" in report["results"][0]["errors"]


def test_trace_offline_blocks_postgres_fact_when_policy_forbids_fallback(tmp_path):
    module = _load_module()
    case_root = tmp_path / "bench"
    case = _case(
        source_policy={
            "primary": "wiki_metrics",
            "allow_postgres_fallback": False,
            "allowed_fallback_reasons": ["wiki_missing"],
            "forbid_semantic_numeric_source": True,
        },
        expected_trace={"must_have_wiki_facts": False, "fallback_reason": "wiki_missing"},
    )
    _write_jsonl(case_root / "cases.jsonl", [case])
    _write_jsonl(
        case_root / "traces.jsonl",
        [
            _trace(
                wiki_facts=[],
                fallback_reason="wiki_missing",
                postgres_facts=[
                    {
                        "source_type": "postgresql_agent_view",
                        "canonical_name": "revenue",
                        "statement_type": "income_statement",
                        "period": "2025-12-31",
                        "value": "100",
                        "raw_value": "100",
                        "unit": "RMB million",
                        "currency": "RMB",
                        "table_index": 4,
                        "quote": "Revenue 100",
                    }
                ],
            )
        ],
    )

    report = module.run_benchmark(case_root=case_root, trace_log=case_root / "traces.jsonl", mode="trace-offline")

    result = report["results"][0]
    assert report["passed"] is False
    assert "postgres_facts present but source_policy.allow_postgres_fallback is false" in result["errors"]
    assert "postgres fallback is forbidden by source_policy.allow_postgres_fallback" in result["facts"][0]["errors"]
    assert result["facts"][0]["source_policy_passed"] is False


def test_trace_offline_rejects_semantic_numeric_source(tmp_path):
    module = _load_module()
    case_root = tmp_path / "bench"
    _write_jsonl(case_root / "cases.jsonl", [_case(expected_facts=[{**_case()["expected_facts"][0], "required_source_types": ["semantic"]}])])
    _write_jsonl(
        case_root / "traces.jsonl",
        [
            _trace(
                wiki_facts=[
                    {
                        "source_type": "semantic",
                        "canonical_name": "revenue",
                        "statement_type": "income_statement",
                        "period": "2025-12-31",
                        "value": "100",
                        "raw_value": "100",
                        "unit": "RMB million",
                        "currency": "RMB",
                        "table_index": 4,
                        "quote": "Revenue 100",
                    }
                ]
            )
        ],
    )

    report = module.run_benchmark(case_root=case_root, trace_log=case_root / "traces.jsonl", mode="trace-offline")

    assert report["passed"] is False
    assert "semantic source is not allowed for numeric fact" in report["results"][0]["errors"]


def test_trace_offline_requires_statement_type_when_case_declares_it(tmp_path):
    module = _load_module()
    case_root = tmp_path / "bench"
    _write_jsonl(case_root / "cases.jsonl", [_case()])
    trace = _trace()
    del trace["wiki_facts"][0]["statement_type"]
    _write_jsonl(case_root / "traces.jsonl", [trace])

    report = module.run_benchmark(case_root=case_root, trace_log=case_root / "traces.jsonl", mode="trace-offline")

    assert report["passed"] is False
    assert "missing trace fact" in report["results"][0]["errors"][0]


def test_trace_offline_validates_resolved_identity(tmp_path):
    module = _load_module()
    case_root = tmp_path / "bench"
    _write_jsonl(case_root / "cases.jsonl", [_case()])
    _write_jsonl(case_root / "traces.jsonl", [_trace(resolved_company={"market": "HK", "id": "HK:00005"})])

    report = module.run_benchmark(case_root=case_root, trace_log=case_root / "traces.jsonl", mode="trace-offline")

    assert report["passed"] is False
    assert "resolved_company.id expected 'HK:00700', got 'HK:00005'" in report["results"][0]["errors"]


def test_trace_offline_validates_exact_evidence_values(tmp_path):
    module = _load_module()
    case_root = tmp_path / "bench"
    _write_jsonl(case_root / "cases.jsonl", [_case(required_evidence=[{"table_index": 5, "quote": "Revenue 100"}])])
    _write_jsonl(case_root / "traces.jsonl", [_trace()])

    report = module.run_benchmark(case_root=case_root, trace_log=case_root / "traces.jsonl", mode="trace-offline")

    assert report["passed"] is False
    assert "evidence.table_index expected 5, got 4" in report["results"][0]["errors"]


def test_trace_offline_reports_case_schema_errors(tmp_path):
    module = _load_module()
    case_root = tmp_path / "bench"
    broken = _case(case_id="", source_policy="wiki-first", expected_facts=[{"value": "100"}])
    _write_jsonl(case_root / "cases.jsonl", [broken])
    _write_jsonl(case_root / "traces.jsonl", [])

    report = module.run_benchmark(case_root=case_root, trace_log=case_root / "traces.jsonl", mode="trace-offline")

    assert report["passed"] is False
    assert report["summary"]["cases"] == 1
    assert "case.case_id missing" in report["results"][0]["errors"]
    assert "case.source_policy must be an object" in report["results"][0]["errors"]
    assert "case.expected_facts[1] missing metric identifier" in report["results"][0]["errors"]


def test_trace_offline_allows_guardrail_refusal_case(tmp_path):
    module = _load_module()
    case_root = tmp_path / "bench"
    case = _case(
        expected_facts=[],
        expected_guardrail={"should_answer": False},
        expected_trace={"must_have_wiki_facts": False, "fallback_reason": None},
        required_evidence=[],
    )
    _write_jsonl(case_root / "cases.jsonl", [case])
    _write_jsonl(
        case_root / "traces.jsonl",
        [_trace(wiki_facts=[], guardrail_result={"blocked": True, "reason": "evidence_missing"})],
    )

    report = module.run_benchmark(case_root=case_root, trace_log=case_root / "traces.jsonl", mode="trace-offline")

    assert report["passed"] is True
    assert report["summary"]["facts"] == 0
    assert report["summary"]["key_fact_accuracy"] == 1.0
    assert report["summary"]["guardrail_block_count"] == 1


def test_trace_offline_requires_exact_financial_claim_guardrail_reason_and_violation(tmp_path):
    module = _load_module()
    case_root = tmp_path / "bench"
    case = _case(
        expected_facts=[],
        required_evidence=[],
        expected_guardrail={
            "should_answer": False,
            "reason": "financial_claim_mismatch",
            "claim_violations": [
                {
                    "reason": "value_mismatch",
                    "metric": "operating_revenue",
                    "claimed_value": 6351.26,
                    "evidence_value": 8382.70,
                }
            ],
        },
        expected_trace={"must_have_wiki_facts": False, "fallback_reason": None},
    )
    trace = _trace(
        wiki_facts=[],
        guardrail_result={"blocked": True, "reason": "financial_claim_mismatch"},
        claim_verifier_result={
            "checked": True,
            "allowed": False,
            "violations": [
                {
                    "reason": "value_mismatch",
                    "metric": "operating_revenue",
                    "claimed_value": 6351.26,
                    "evidence_value": 8382.70,
                }
            ],
        },
    )
    _write_jsonl(case_root / "cases.jsonl", [case])
    _write_jsonl(case_root / "traces.jsonl", [trace])

    report = module.run_benchmark(case_root=case_root, trace_log=case_root / "traces.jsonl", mode="trace-offline")

    assert report["passed"] is True

    trace["claim_verifier_result"]["violations"][0]["evidence_value"] = 6351.26
    _write_jsonl(case_root / "traces.jsonl", [trace])
    report = module.run_benchmark(case_root=case_root, trace_log=case_root / "traces.jsonl", mode="trace-offline")

    assert report["passed"] is False
    assert "missing claim_verifier violation[1]" in report["results"][0]["errors"][0]


def test_trace_offline_requires_exact_financial_evidence_identity_violation(tmp_path):
    module = _load_module()
    case_root = tmp_path / "bench"
    expected_violation = {
        "reason": "company_id_mismatch",
        "market": "HK",
        "company_id": "HK:WRONG",
        "filing_id": "HK:01398:2025-annual",
        "parse_run_id": "run-hk-2025",
        "expected_market": "HK",
        "expected_company_id": "HK:01398",
        "expected_filing_id": "HK:01398:2025-annual",
        "expected_parse_run_id": "run-hk-2025",
    }
    case = _case(
        company_id="HK:01398",
        filing_id="HK:01398:2025-annual",
        expected_facts=[],
        required_evidence=[],
        expected_guardrail={
            "should_answer": False,
            "reason": "financial_evidence_identity_mismatch",
            "claim_violations": [expected_violation],
        },
        expected_trace={"must_have_wiki_facts": False, "fallback_reason": None},
    )
    trace = _trace(
        resolved_company={"market": "HK", "id": "HK:01398"},
        resolved_period={"period": "2025-12-31", "filing_id": "HK:01398:2025-annual"},
        wiki_facts=[],
        guardrail_result={"blocked": True, "reason": "financial_evidence_identity_mismatch"},
        claim_verifier_result={"checked": True, "allowed": False, "violations": [expected_violation.copy()]},
    )
    _write_jsonl(case_root / "cases.jsonl", [case])
    _write_jsonl(case_root / "traces.jsonl", [trace])

    report = module.run_benchmark(case_root=case_root, trace_log=case_root / "traces.jsonl", mode="trace-offline")

    assert report["passed"] is True
    trace["claim_verifier_result"]["violations"][0]["expected_company_id"] = "HK:WRONG"
    _write_jsonl(case_root / "traces.jsonl", [trace])
    report = module.run_benchmark(case_root=case_root, trace_log=case_root / "traces.jsonl", mode="trace-offline")
    assert report["passed"] is False
    assert "missing claim_verifier violation[1]" in report["results"][0]["errors"][0]


def test_trace_offline_fails_when_refusal_case_is_not_blocked(tmp_path):
    module = _load_module()
    case_root = tmp_path / "bench"
    case = _case(
        expected_facts=[],
        expected_guardrail={"should_answer": False},
        expected_trace={"must_have_wiki_facts": False, "fallback_reason": None},
        required_evidence=[],
    )
    _write_jsonl(case_root / "cases.jsonl", [case])
    _write_jsonl(case_root / "traces.jsonl", [_trace(wiki_facts=[], guardrail_result={"blocked": False})])

    report = module.run_benchmark(case_root=case_root, trace_log=case_root / "traces.jsonl", mode="trace-offline")

    assert report["passed"] is False
    assert "guardrail should block this answer" in report["results"][0]["errors"]


def test_trace_offline_validates_expected_calculator_runs(tmp_path):
    module = _load_module()
    case_root = tmp_path / "bench"
    case = _case(
        expected_calculations=[
            {
                "operation": "yoy_growth",
                "numerator": "120",
                "denominator": "100",
                "result": "0.2",
                "tolerance_ratio": 0,
            }
        ]
    )
    _write_jsonl(case_root / "cases.jsonl", [case])
    _write_jsonl(
        case_root / "traces.jsonl",
        [
            _trace(
                calculator_runs=[
                    {
                        "operation": "yoy_growth",
                        "numerator": "120",
                        "denominator": "100",
                        "result": "0.2",
                    }
                ]
            )
        ],
    )

    report = module.run_benchmark(case_root=case_root, trace_log=case_root / "traces.jsonl", mode="trace-offline")

    assert report["passed"] is True
    assert report["summary"]["calculations"] == 1
    assert report["summary"]["calculator_run_accuracy"] == 1.0


def test_trace_offline_fails_when_expected_calculator_run_is_missing(tmp_path):
    module = _load_module()
    case_root = tmp_path / "bench"
    case = _case(expected_calculations=[{"operation": "yoy_growth", "result": "0.2"}])
    _write_jsonl(case_root / "cases.jsonl", [case])
    _write_jsonl(case_root / "traces.jsonl", [_trace(calculator_runs=[])])

    report = module.run_benchmark(case_root=case_root, trace_log=case_root / "traces.jsonl", mode="trace-offline")

    assert report["passed"] is False
    assert report["summary"]["calculator_run_accuracy"] == 0.0
    assert "missing calculator_run[1] operation='yoy_growth' result='0.2'" in report["results"][0]["errors"]


def test_mode_specific_cases_are_skipped_outside_declared_mode(tmp_path):
    module = _load_module()
    case_root = tmp_path / "bench"
    _write_jsonl(case_root / "cases.jsonl", [_case(modes=["trace-offline"])])
    _write_jsonl(case_root / "traces.jsonl", [_trace()])

    trace_report = module.run_benchmark(case_root=case_root, trace_log=case_root / "traces.jsonl", mode="trace-offline")
    wiki_report = module.run_benchmark(case_root=case_root, trace_log=case_root / "traces.jsonl", mode="wiki-static")

    assert trace_report["summary"]["cases"] == 1
    assert wiki_report["summary"]["cases"] == 0
    assert wiki_report["passed"] is False


def test_case_modes_missing_runs_all_implemented_modes():
    module = _load_module()

    assert module.case_modes(_case()) == module.IMPLEMENTED_MODES


def test_case_schema_rejects_reserved_and_unknown_modes():
    module = _load_module()

    reserved_errors = module.validate_case(_case(modes=["postgres-fallback"]))
    unknown_errors = module.validate_case(_case(modes=["future-live-agent"]))

    assert "case.modes contains unsupported modes: ['postgres-fallback']" in reserved_errors
    assert "case.modes contains unsupported modes: ['future-live-agent']" in unknown_errors


def test_wiki_static_mode_validates_document_full_golden_facts():
    module = _load_module()

    report = module.run_benchmark(mode="wiki-static")

    assert report["passed"] is True
    assert report["summary"]["key_fact_accuracy"] == 1.0


def test_main_returns_nonzero_when_p0_case_fails(tmp_path):
    module = _load_module()
    case_root = tmp_path / "bench"
    _write_jsonl(case_root / "cases.jsonl", [_case()])
    _write_jsonl(case_root / "traces.jsonl", [_trace(wiki_facts=[])])

    exit_code = module.main(
        [
            "--mode",
            "trace-offline",
            "--case-root",
            str(case_root),
            "--trace-log",
            str(case_root / "traces.jsonl"),
            "--output",
            str(tmp_path / "report.json"),
            "--markdown",
            str(tmp_path / "report.md"),
        ]
    )

    assert exit_code == 1
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["passed"] is False
