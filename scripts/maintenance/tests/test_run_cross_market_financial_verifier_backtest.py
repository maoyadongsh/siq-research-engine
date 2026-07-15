from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "run_cross_market_financial_verifier_backtest.py"
SPEC = importlib.util.spec_from_file_location("run_cross_market_financial_verifier_backtest_under_test", SOURCE)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _company_root(wiki_root: Path, market: str) -> Path:
    return wiki_root / MODULE.MARKET_COMPANY_ROOTS[market]


def _write_company(
    wiki_root: Path,
    market: str,
    ticker: str,
    suffix: str,
    *,
    status: str = "ready",
    company_id: str | None = None,
) -> Path:
    company_dir = _company_root(wiki_root, market) / f"{ticker}-{suffix}"
    report_id = "2025-annual"
    company_dir.mkdir(parents=True)
    company = {
        "market": market,
        "company_id": company_id or f"{market}:{ticker}",
        "ticker": ticker,
        "company_name": suffix,
        "status": status,
        "primary_report_id": report_id,
        "reports": [{"report_id": report_id, "status": "ready"}],
        "metrics": {"by_report": {report_id: {"three_statements": "metrics.json"}}},
    }
    (company_dir / "company.json").write_text(json.dumps(company), encoding="utf-8")
    report_dir = company_dir / "reports" / report_id
    report_dir.mkdir(parents=True)
    (report_dir / "manifest.json").write_text("{}", encoding="utf-8")
    return company_dir


def _row(metric_key: str, value: str = "1,000", *, currency: str = "USD") -> dict:
    return {
        "statement_type": "income_statement",
        "metric_key": metric_key,
        "metric_name": metric_key,
        "period": "2025-12-31",
        "raw_value": value,
        "unit": f"{currency} million",
        "currency": currency,
        "evidence_id": f"evidence-{metric_key}",
        "task_id": "task-1",
        "pdf_page": 10,
        "table_index": 2,
    }


def _source(tmp_path: Path) -> MODULE.SourceCase:
    company_dir = _write_company(tmp_path / "wiki", "HK", "00001", "Demo Holdings")
    metrics_file = company_dir / "metrics.json"
    metrics_file.write_text("{}", encoding="utf-8")
    return MODULE.SourceCase(
        market="HK",
        ticker="00001",
        company_dir=company_dir,
        company=json.loads((company_dir / "company.json").read_text(encoding="utf-8")),
        question="分析 HK 00001 的营业收入",
        metric_spec=MODULE.METRIC_SPECS[0],
        result={
            "company_dir": company_dir,
            "company_id": "HK:00001",
            "report_id": "2025-annual",
            "metrics_file": metrics_file,
        },
        row=_row("operating_revenue"),
        identity={
            "market": "HK",
            "company_id": "HK:00001",
            "filing_id": "HK:00001:2025-annual",
            "parse_run_id": "run-1",
        },
    )


def test_discover_candidates_excludes_fixtures_and_prefers_ready_canonical_duplicate(tmp_path: Path):
    wiki_root = tmp_path / "wiki"
    legacy = _write_company(wiki_root, "JP", "1111", "Legacy", status="")
    canonical = _write_company(wiki_root, "JP", "1111", "Canonical", status="ready")
    _write_company(wiki_root, "JP", "0000", "Fixture", company_id="JP:FIXTURE:0000")
    _write_company(wiki_root, "JP", "000000", "SiqResearchEngine")

    candidates = MODULE.discover_company_candidates(wiki_root, "JP")

    assert [(item.ticker, item.company_dir) for item in candidates] == [("1111", canonical)]
    assert legacy not in [item.company_dir for item in candidates]


def test_discover_candidates_excludes_existing_baseline_benchmark_subjects(tmp_path: Path):
    wiki_root = tmp_path / "wiki"
    _write_company(wiki_root, "HK", "00700", "Tencent")
    control = _write_company(wiki_root, "HK", "00701", "Control")

    candidates = MODULE.discover_company_candidates(wiki_root, "HK")

    assert [(item.ticker, item.company_dir) for item in candidates] == [("00701", control)]


def test_metric_selection_rotates_supported_metrics_and_falls_back():
    rows = [
        _row("operating_revenue"),
        _row("net_profit"),
        _row("total_assets"),
        _row("total_liabilities"),
        _row("gross_profit"),
    ]

    selected = [MODULE._select_metric_row(rows, index) for index in range(5)]

    assert [item[1]["metric_key"] for item in selected if item] == [
        "operating_revenue",
        "net_profit",
        "total_assets",
        "total_liabilities",
        "gross_profit",
    ]
    fallback = MODULE._select_metric_row([rows[0]], 3)
    assert fallback and fallback[1]["metric_key"] == "operating_revenue"


def test_metric_selection_requires_clean_numeric_and_reviewable_evidence():
    footnoted = _row("operating_revenue", "※1 1,000")
    unlocated = {**_row("net_profit"), "task_id": None, "pdf_page": None, "table_index": None}

    assert MODULE._select_metric_row([footnoted, unlocated], 0) is None


def test_question_templates_and_metrics_rotate_together():
    questions = [MODULE._metric_question("EU", "SAP", spec.query_label, index) for index, spec in enumerate(MODULE.METRIC_SPECS)]

    assert len(set(questions)) == len(MODULE.METRIC_SPECS)
    assert [spec.query_label in question for spec, question in zip(MODULE.METRIC_SPECS, questions, strict=True)] == [True] * 5


def test_tamper_raw_value_preserves_grouping_and_decimal_precision():
    assert MODULE.tamper_raw_value("1,000") == "1,100"
    assert MODULE.tamper_raw_value("1,234.50") == "1,357.95"
    assert MODULE.tamper_raw_value("(1,000)") == "(1,100)"


def test_tamper_raw_value_rejects_footnoted_or_non_numeric_values():
    for value in ("※1 1,000", "-", "N/A"):
        try:
            MODULE.tamper_raw_value(value)
        except ValueError:
            pass
        else:  # pragma: no cover - assertion branch
            raise AssertionError(f"expected ValueError for {value!r}")


def test_render_answer_preserves_parseable_original_foreign_unit(tmp_path: Path):
    source = _source(tmp_path)

    answer = MODULE.render_answer(source)

    assert answer == "营业收入在2025-12-31为1,000 USD million。"


class _FakeRuntime:
    @staticmethod
    def append_primary_data_evidence_if_needed(_question: str, _context: object, answer: str) -> str:
        return (
            answer
            + "\n[D1] source_type=wiki_metrics, market=HK, company_id=HK:00001, "
            "filing_id=HK:00001:2025-annual, parse_run_id=run-1, "
            "canonical_name=operating_revenue, period=2025-12-31, value=1,000, "
            "unit=USD million, currency=USD, evidence_id=evidence-operating_revenue, "
            'quote="Revenue | 1,000", task_id=task-1, pdf_page=10, table_index=2'
        )

    @staticmethod
    def enforce_financial_evidence_contract(_question: str, _context: object, reply: str) -> str:
        if "1,000 USD million" in reply.splitlines()[0]:
            return reply
        return (
            "## 财务数值证据不一致\n"
            "- mismatch_1: reason=value_mismatch\n"
            "guardrail_status=blocked\n"
            "guardrail_reason=financial_claim_mismatch"
        )

    @staticmethod
    def _resolved_research_context(_question: str, _context: object) -> dict:
        return {
            "research_identity": {
                "market": "HK",
                "company_id": "HK:00001",
                "filing_id": "HK:00001:2025-annual",
                "parse_run_id": "run-1",
            }
        }


class _FakeAudit:
    @staticmethod
    def _extract_source_references(reply: str) -> list[dict]:
        assert "source_type=wiki_metrics" in reply
        return [
            {
                "source_type": "wiki_metrics",
                "market": "HK",
                "company_id": "HK:00001",
                "filing_id": "HK:00001:2025-annual",
                "parse_run_id": "run-1",
                "value": "1,000",
                "unit": "USD million",
                "evidence_id": "evidence-operating_revenue",
                "task_id": "task-1",
                "pdf_page": 10,
            }
        ]

    @staticmethod
    def build_answer_audit_trace(*, raw_reply: str, final_reply: str, **_kwargs: object) -> dict:
        correct = "1,000 USD million" in raw_reply.splitlines()[0]
        return {
            "schema_version": "siq_answer_audit_trace_v1",
            "created_at": "2026-07-15T00:00:00Z",
            "question_id": _kwargs["context"]["question_id"],
            "claim_verifier_result": {
                "checked": True,
                "allowed": correct,
                "claim_count": 1,
                "evidence_fact_count": 1,
                "violation_count": 0 if correct else 1,
                "violations": [] if correct else [{"reason": "value_mismatch"}],
            },
            "delivered_claim_verifier_result": {},
            "guardrail_result": {"blocked": "guardrail_status=blocked" in final_reply},
            "citations": [],
            "wiki_facts": [],
            "postgres_facts": [],
            "calculator_runs": [],
        }

    @staticmethod
    def answer_audit_trace_id(_trace: dict) -> str:
        return "aat_0123456789abcdef0123456789abcdef"


def test_execute_case_scores_correct_and_tampered_through_full_chain(tmp_path: Path):
    source = _source(tmp_path)

    correct = MODULE.execute_case(_FakeRuntime(), _FakeAudit(), source, case_kind="correct")
    tampered = MODULE.execute_case(
        _FakeRuntime(),
        _FakeAudit(),
        source,
        case_kind="tampered",
        raw_value="1,100",
    )

    assert correct["passed"] is True
    assert correct["observed"]["blocked"] is False
    assert tampered["passed"] is True
    assert tampered["observed"]["guardrail_reason"] == "financial_claim_mismatch"
    assert tampered["observed"]["violation_reasons"] == ["value_mismatch"]
    assert "raw_reply" not in json.dumps([correct, tampered])


def _result(
    market: str,
    ticker: str,
    kind: str,
    *,
    blocked: bool,
    claim_count: int = 1,
    reason: str | None = None,
    violations: list[str] | None = None,
) -> dict:
    expected_pass = (kind == "correct" and not blocked) or (
        kind == "tampered"
        and blocked
        and reason == "financial_claim_mismatch"
        and "value_mismatch" in (violations or [])
    )
    return {
        "case_id": f"{market}-{ticker}-{kind}",
        "market": market,
        "ticker": ticker,
        "case_kind": kind,
        "passed": expected_pass and claim_count >= 1,
        "observed": {
            "blocked": blocked,
            "guardrail_reason": reason,
            "violation_reasons": violations or [],
            "claim_count": claim_count,
            "evidence_complete": True,
            "locator_complete": True,
            "identity_complete": True,
            "resolved_identity_complete": True,
        },
    }


def test_summary_separates_false_positive_false_negative_wrong_reason_and_inspection_gap():
    results = [
        _result("HK", "1", "correct", blocked=True, reason="financial_claim_mismatch"),
        _result("HK", "2", "tampered", blocked=False),
        _result("HK", "3", "tampered", blocked=True, reason="financial_evidence_missing"),
        _result(
            "HK",
            "4",
            "tampered",
            blocked=True,
            claim_count=0,
            reason="financial_claim_mismatch",
            violations=["value_mismatch"],
        ),
    ]

    summary, markets = MODULE.summarize_results(
        results,
        markets=("HK",),
        subjects_per_market=1,
        tampered_per_market=3,
    )

    assert summary["false_positive_count"] == 1
    assert summary["false_negative_count"] == 1
    assert summary["wrong_reason_count"] == 1
    assert summary["numeric_claim_inspection_rate"] == 0.75
    assert markets[0]["passed"] is False


def test_sanitize_report_value_removes_signed_query_but_keeps_normal_parameters():
    value = {
        "url": "https://example.test/page?format=html&source_token=secret#anchor",
        "nested": ["https://example.test/a?access_token=secret&x=1"],
    }

    sanitized = MODULE.sanitize_report_value(value)

    assert sanitized["url"] == "https://example.test/page?format=html#anchor"
    assert sanitized["nested"] == ["https://example.test/a?x=1"]


def test_render_markdown_includes_failure_metrics_and_case_errors():
    report = {
        "passed": False,
        "summary": {
            "cases": 2,
            "passed_cases": 1,
            "expected_cases": 2,
            "false_positive_count": 1,
            "false_positive_rate": 0.5,
            "false_negative_count": 0,
            "false_negative_rate": 0.0,
            "wrong_reason_count": 0,
            "wrong_reason_rate": 0.0,
            "numeric_claim_inspection_rate": 1.0,
            "evidence_coverage_rate": 1.0,
            "locator_coverage_rate": 1.0,
            "resolved_identity_coverage_rate": 1.0,
            "citation_identity_coverage_rate": 1.0,
        },
        "markets": [
            {
                "market": "HK",
                "subjects": 1,
                "correct_cases": 1,
                "tampered_cases": 1,
                "false_positives": 1,
                "false_negatives": 0,
                "wrong_reasons": 0,
                "inspection_rate": 1.0,
                "passed": False,
            }
        ],
        "results": [
            {
                "case_id": "case-1",
                "market": "HK",
                "case_kind": "correct",
                "passed": False,
                "observed": {"guardrail_reason": "financial_claim_mismatch"},
                "errors": ["correct_answer_blocked"],
            }
        ],
    }

    markdown = MODULE.render_markdown(report)

    assert "False positives: 1 (0.500)" in markdown
    assert "Intentional tampered negative controls" in markdown
    assert "correct_answer_blocked" in markdown
