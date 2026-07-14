from __future__ import annotations

import importlib.util
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "compare_financial_quality_baselines.py"
SPEC = importlib.util.spec_from_file_location("compare_financial_quality_baselines_under_test", SOURCE)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def financial_report(*, evidence: float = 1.0, cases: int = 3, blocks: int = 1) -> dict:
    summary = {field: 1.0 for field in MODULE.QUALITY_RATE_FIELDS}
    summary.update(
        {
            "evidence_coverage_rate": evidence,
            "cases": cases,
            "passed_cases": cases,
            "guardrail_block_count": blocks,
        }
    )
    return {"mode": "trace-offline", "passed": True, "summary": summary}


def performance_report(
    *,
    p95: float = 10.0,
    domain_p95: float = 8.0,
    hit_rate: float = 1.0,
    recall_at_k: float = 1.0,
    cases: int = 3,
    repeat: int = 5,
) -> dict:
    return {
        "mode": "contract",
        "passed": True,
        "settings": {"repeat": repeat, "max_benchmark_seconds": 30.0},
        "benchmarks": [
            {
                "name": "market_ingestion_contract",
                "passed": True,
                "elapsed_ms": {"p95": p95},
                "domain": {
                    "cases": cases,
                    "passed_cases": cases,
                    "hit_rate": hit_rate,
                    "recall_at_k": recall_at_k,
                    "latency_ms": {"p95": domain_p95},
                },
            }
        ],
    }


def test_financial_comparison_accepts_equal_quality_and_more_attack_cases():
    result = MODULE.compare_financial_report(
        financial_report(cases=3, blocks=1),
        financial_report(cases=5, blocks=2),
    )

    assert result["passed"] is True
    assert result["failures"] == []


def test_financial_comparison_rejects_evidence_or_guardrail_coverage_drop():
    result = MODULE.compare_financial_report(
        financial_report(evidence=1.0, blocks=2),
        financial_report(evidence=0.9, blocks=1),
    )

    assert result["passed"] is False
    assert any("evidence_coverage_rate regressed" in item for item in result["failures"])
    assert any("guardrail_block_count decreased" in item for item in result["failures"])


def test_performance_comparison_accepts_p95_inside_named_budget():
    result = MODULE.compare_performance_report(
        performance_report(p95=10.0),
        performance_report(p95=10.5),
    )

    assert result["passed"] is True
    assert result["benchmarks"][0]["change_percent"] == 5.0


def test_performance_comparison_rejects_p95_over_budget():
    result = MODULE.compare_performance_report(
        performance_report(p95=10.0),
        performance_report(p95=10.501),
    )

    assert result["passed"] is False
    assert any("exceeds 5% budget" in item for item in result["failures"])


def test_performance_comparison_rejects_recall_or_case_coverage_drop():
    result = MODULE.compare_performance_report(
        performance_report(hit_rate=1.0, cases=3),
        performance_report(hit_rate=0.8, recall_at_k=0.8, cases=2),
    )

    assert result["passed"] is False
    assert any("domain.cases decreased" in item for item in result["failures"])
    assert any("domain.hit_rate decreased" in item for item in result["failures"])
    assert any("domain.recall_at_k decreased" in item for item in result["failures"])


def test_performance_comparison_rejects_business_domain_p95_over_budget():
    result = MODULE.compare_performance_report(
        performance_report(domain_p95=8.0),
        performance_report(domain_p95=8.401),
    )

    assert result["passed"] is False
    assert any("domain latency p95 regression exceeds 5% budget" in item for item in result["failures"])


def test_performance_comparison_rejects_mismatched_measurement_settings():
    result = MODULE.compare_performance_report(
        performance_report(repeat=1),
        performance_report(repeat=50),
    )

    assert result["passed"] is False
    assert any("setting mismatch for repeat" in item for item in result["failures"])


def test_performance_comparison_rejects_embedding_throughput_drop():
    baseline = performance_report()
    current = performance_report()
    baseline["benchmarks"][0]["domain"].update({"input_count": 3, "vector_count": 3, "texts_per_second": 10.0})
    current["benchmarks"][0]["domain"].update({"input_count": 3, "vector_count": 3, "texts_per_second": 9.0})

    result = MODULE.compare_performance_report(baseline, current)

    assert result["passed"] is False
    assert any("domain.texts_per_second decreased" in item for item in result["failures"])


def test_performance_comparison_rejects_passing_but_empty_baseline():
    baseline = performance_report()
    baseline["benchmarks"] = []

    result = MODULE.compare_performance_report(baseline, performance_report())

    assert result["passed"] is False
    assert "performance baseline has no benchmarks" in result["failures"]
    assert any("benchmark missing from baseline" in item for item in result["failures"])


def test_performance_comparison_requires_baseline_to_cover_every_current_benchmark():
    baseline = performance_report()
    current = performance_report()
    extra = {**current["benchmarks"][0], "name": "new_production_probe"}
    current["benchmarks"].append(extra)

    result = MODULE.compare_performance_report(baseline, current)

    assert result["passed"] is False
    assert "performance new_production_probe: benchmark missing from baseline" in result["failures"]
    missing = next(item for item in result["benchmarks"] if item["name"] == "new_production_probe")
    assert missing == {
        "name": "new_production_probe",
        "passed": False,
        "failures": ["benchmark missing from baseline"],
    }
