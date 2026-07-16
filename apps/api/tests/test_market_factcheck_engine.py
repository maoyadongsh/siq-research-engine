from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
FACTCHECK_SCRIPTS = (
    REPO_ROOT / "agents" / "hermes" / "profiles" / "siq_factchecker_multi_market" / "scripts"
)
if str(FACTCHECK_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(FACTCHECK_SCRIPTS))

from market_factcheck_engine import (  # noqa: E402
    _claim_result,
    _currency_code,
    _normalized_metric_rows,
    _period_is_after,
    _periods_equivalent,
    load_resolved_target,
    run_market_factcheck,
)
from services.research_report_package import enumerate_companies, resolve_report_package  # noqa: E402
from tests.research_universe_fixture import add_company, write_json  # noqa: E402
from tests.specialist_workflow_fixture import write_analysis_target  # noqa: E402


def _package(wiki_root: Path, report_id: str):
    company = next(item for item in enumerate_companies(wiki_root=wiki_root, markets=("US",)))
    return resolve_report_package(
        market="US",
        company_key=company.company_key,
        report_id=report_id,
        agent_type="factcheck",
        wiki_root=wiki_root,
    )


def _load_target(target, tmp_path: Path, wiki_root: Path):
    bundle = tmp_path / "factcheck-target.json"
    bundle.write_text(json.dumps(target.to_bundle()), encoding="utf-8")
    return load_resolved_target(bundle, wiki_root)


def test_real_sec_10k_package_uses_html_and_xbrl_evidence_without_pdf_pages(tmp_path, monkeypatch) -> None:
    source = REPO_ROOT / "data/wiki/us/companies/PEN-Penumbra-Inc/reports/2025-10-K-0001321732-26-000007"
    if not source.is_dir():
        pytest.skip("real SEC 10-K fixture is not available")
    wiki_root = tmp_path / "wiki"
    report_id = "2025-10-K-0001321732-26-000007"
    company_dir = add_company(
        wiki_root,
        market="US",
        code="PEN",
        name="Penumbra Inc",
        company_id="US:0001321732",
        report_id=report_id,
        filing_id="US:0001321732:0001321732-26-000007",
        parse_run_id="c2ee20a6477038cb",
        source_family="sec_ixbrl",
        form_type="10-K",
        period_end="2025-12-31",
    )
    target_report = company_dir / "reports" / report_id
    for relative in (
        "metrics/normalized_metrics.json",
        "metrics/financial_checks.json",
        "qa/source_map.json",
        "sections/risk_factors.md",
        "sections/mda.md",
    ):
        destination = target_report / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / relative, destination)
    monkeypatch.setenv("SIQ_MULTI_MARKET_RESEARCH_ENABLED", "1")
    monkeypatch.setenv("SIQ_US_SEC_ANALYSIS_ENABLED", "1")
    target = write_analysis_target(_package(wiki_root, report_id), artifact_id="analysis-pen-10k-v1")

    report = run_market_factcheck(_load_target(target, tmp_path, wiki_root))

    assert report["checks"]["identity_consistency"]["status"] == "pass"
    assert report["checks"]["data_consistency"]["status"] == "pass"
    assert report["checks"]["period_consistency"]["status"] == "pass"
    assert report["checks"]["calculation_consistency"]["status"] == "pass"
    assert report["checks"]["traceability"]["status"] == "pass"
    assert report["checks"]["market_risk_completeness"]["status"] == "pass"
    assert report["evidence_summary"]
    assert any(item.get("section_id") or item.get("xbrl_fact_id") for item in report["evidence_summary"])
    assert all(
        item.get("pdf_page") is None and item.get("pdf_page_number") is None for item in report["evidence_summary"]
    )
    assert report["metric_evidence_map"]["operating_revenue"]["unit"] == "USD"


def test_sec_10q_fixture_preserves_quarter_period_currency_and_anchor(tmp_path, monkeypatch) -> None:
    wiki_root = tmp_path / "wiki"
    report_id = "2026-10-Q-0000320193-26-000001"
    company_dir = add_company(
        wiki_root,
        market="US",
        code="AAPL",
        name="Apple Inc",
        company_id="US:0000320193",
        report_id=report_id,
        filing_id="US:0000320193:0000320193-26-000001",
        parse_run_id="run-us-aapl-q1",
        source_family="sec_ixbrl",
        form_type="10-Q",
        fiscal_year=2026,
        period_end="2026-03-31",
    )
    report_dir = company_dir / "reports" / report_id
    manifest_path = report_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["report_type"] = "quarterly"
    manifest["fiscal_period"] = "Q1"
    write_json(manifest_path, manifest)
    identity = {
        "filing_id": "US:0000320193:0000320193-26-000001",
        # Real SEC packages preserve the XBRL child extraction run here. The
        # authoritative package ResearchIdentity still uses run-us-aapl-q1.
        "parse_run_id": "sec-xbrl-child-run",
    }
    rows = []
    for period, revenue, assets in (
        ("2026-03-31", 100.0, 250.0),
        ("2025-03-31", 90.0, 230.0),
    ):
        rows.extend(
            [
                {
                    **identity,
                    "canonical_name": "operating_revenue",
                    "value": revenue,
                    "unit": "USD",
                    "currency": "USD",
                    "period_key": period,
                    "duration_days": 90,
                    "qtd_ytd_type": "qtd",
                    "fiscal_period": "Q1",
                    "evidence_id": f"revenue-{period}",
                    "raw_fact_id": f"fact-revenue-{period}",
                },
                {
                    **identity,
                    "canonical_name": "total_assets",
                    "value": assets,
                    "unit": "USD",
                    "currency": "USD",
                    "period_key": period,
                    "qtd_ytd_type": "instant",
                    "fiscal_period": "Q1",
                    "evidence_id": f"assets-{period}",
                    "raw_fact_id": f"fact-assets-{period}",
                },
            ]
        )
    write_json(
        report_dir / "metrics" / "normalized_metrics.json",
        {"schema_version": "sec_normalized_metrics_v1", "metrics": rows},
    )
    write_json(
        report_dir / "metrics" / "financial_checks.json",
        {"schema_version": 1, "overall_status": "pass", "checks": []},
    )
    write_json(
        report_dir / "qa" / "source_map.json",
        {
            "schema_version": "market_source_map_v1",
            "entries": [
                {
                    "evidence_id": "risk-q1",
                    "source_type": "sec_html_section",
                    "section_id": "part_ii_item_1a",
                    "html_anchor": "part_ii_item_1a",
                    "source_url": "https://www.sec.gov/example-10q",
                    "local_path": "sections/risk_factors.md",
                }
            ],
        },
    )
    (report_dir / "sections" / "risk_factors.md").write_text("# Risk Factors\n", encoding="utf-8")
    (report_dir / "sections" / "mda.md").write_text("# MD&A\n", encoding="utf-8")
    monkeypatch.setenv("SIQ_MULTI_MARKET_RESEARCH_ENABLED", "1")
    monkeypatch.setenv("SIQ_US_SEC_ANALYSIS_ENABLED", "1")
    target = write_analysis_target(_package(wiki_root, report_id), artifact_id="analysis-aapl-10q-v1")

    report = run_market_factcheck(_load_target(target, tmp_path, wiki_root))

    assert report["checks"]["identity_consistency"]["status"] == "pass"
    assert report["checks"]["period_consistency"]["status"] == "pass"
    assert report["checks"]["market_risk_completeness"]["status"] == "pass"
    assert report["checks"]["claim_support"]["status"] == "pass"
    assert report["summary"]["market"] == "US"
    assert report["metric_evidence_map"]["operating_revenue"]["unit"] == "USD"
    assert any(item.get("html_anchor") == "part_ii_item_1a" for item in report["evidence_summary"])
    assert all(item.get("pdf_page") is None for item in report["evidence_summary"])

    metrics_path = report_dir / "metrics" / "normalized_metrics.json"
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    metrics["metrics"].extend(
        [
            {
                **identity,
                "canonical_name": "operating_revenue",
                "value": 100.0,
                "unit": "EUR",
                "currency": "EUR",
                "period_key": "2026-03-31",
            },
            {
                "research_identity": {
                    "market": "US",
                    "company_id": "US:OTHER",
                    "filing_id": identity["filing_id"],
                    "parse_run_id": identity["parse_run_id"],
                },
                "canonical_name": "net_income",
                "value": 10.0,
                "unit": "USD",
                "currency": "USD",
                "period_key": "2026-03-31",
            },
        ]
    )
    write_json(metrics_path, metrics)
    source_map_path = report_dir / "qa" / "source_map.json"
    source_map = json.loads(source_map_path.read_text(encoding="utf-8"))
    source_map["entries"].append(
        {
            "evidence_id": "wrong-report-citation",
            "source_type": "sec_html_section",
            "report_id": "another-report",
            "research_identity": {
                "market": "US",
                "company_id": "US:OTHER",
                "filing_id": "US:OTHER:FILING",
                "parse_run_id": "other-run",
            },
            "html_anchor": "wrong-report",
            "source_url": "https://www.sec.gov/wrong-report",
        }
    )
    write_json(source_map_path, source_map)
    sidecar_path = target.analysis_artifact.sidecar_path
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    sidecar.setdefault("metadata", {})["claims"] = [{"claim": "unsupported claim"}]
    write_json(sidecar_path, sidecar)

    rejected = run_market_factcheck(_load_target(target, tmp_path, wiki_root))

    assert rejected["checks"]["identity_consistency"]["status"] == "fail"
    assert rejected["checks"]["data_consistency"]["status"] == "warning"
    assert rejected["checks"]["traceability"]["status"] == "fail"
    assert rejected["checks"]["claim_support"]["status"] == "warning"


def test_report_with_citations_but_without_structured_claims_requests_changes(tmp_path, monkeypatch) -> None:
    wiki_root = tmp_path / "wiki"
    report_id = "2025-10-K-no-structured-claims"
    company_dir = add_company(
        wiki_root,
        market="US",
        code="AAPL",
        name="Apple Inc",
        company_id="US:0000320193",
        report_id=report_id,
        filing_id="US:0000320193:no-structured-claims",
        parse_run_id="run-us-no-structured-claims",
        source_family="sec_ixbrl",
        form_type="10-K",
        period_end="2025-12-31",
    )
    report_dir = company_dir / "reports" / report_id
    write_json(
        report_dir / "metrics" / "normalized_metrics.json",
        {
            "schema_version": "sec_normalized_metrics_v1",
            "metrics": [
                {
                    "research_identity": {
                        "market": "US",
                        "company_id": "US:0000320193",
                        "filing_id": "US:0000320193:no-structured-claims",
                        "parse_run_id": "run-us-no-structured-claims",
                    },
                    "canonical_name": "operating_revenue",
                    "value": 100.0,
                    "unit": "USD",
                    "currency": "USD",
                    "period_key": "2025-12-31",
                    "evidence_id": "revenue-2025",
                }
            ],
        },
    )
    write_json(
        report_dir / "metrics" / "financial_checks.json",
        {"schema_version": 1, "overall_status": "pass", "checks": []},
    )
    write_json(
        report_dir / "qa" / "source_map.json",
        {
            "schema_version": "market_source_map_v1",
            "entries": [
                {
                    "evidence_id": "risk-1",
                    "source_type": "sec_html_section",
                    "section_id": "item_1a",
                    "html_anchor": "item_1a",
                    "source_url": "https://www.sec.gov/example",
                    "local_path": "sections/risk_factors.md",
                }
            ],
        },
    )
    (report_dir / "sections" / "risk_factors.md").write_text("# Risk Factors\n", encoding="utf-8")
    (report_dir / "sections" / "mda.md").write_text("# MD&A\n", encoding="utf-8")
    monkeypatch.setenv("SIQ_MULTI_MARKET_RESEARCH_ENABLED", "1")
    monkeypatch.setenv("SIQ_US_SEC_ANALYSIS_ENABLED", "1")
    target = write_analysis_target(_package(wiki_root, report_id), artifact_id="analysis-with-citations-only")
    sidecar_path = target.analysis_artifact.sidecar_path
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    sidecar["metadata"].pop("claims")
    write_json(sidecar_path, sidecar)

    report = run_market_factcheck(_load_target(target, tmp_path, wiki_root))

    assert report["checks"]["claim_support"]["status"] == "warning"
    assert report["verdict"] == "request_changes"
    assert "结构化声明清单" in report["checks"]["claim_support"]["issues"][0]["message"]


def test_structured_metric_claims_verify_normalized_value_unit_period_and_evidence() -> None:
    row = {
        "canonical_name": "operating_revenue",
        "metric_name": "Revenue",
        "value": 92_352,
        "unit": "EUR million",
        "currency": "EUR",
        # Some PDF-market upstream packages encode the scale only in unit.
        "scale": 1,
        "period": "2025-12-28",
        "evidence_id": "revenue-2025",
    }
    claim = {
        "claim_id": "revenue-current",
        "claim": "2025 财年营业收入为 923.52 亿欧元。",
        "claim_type": "metric_value",
        "metric_key": "operating_revenue",
        "period": "2025-12-28",
        "normalized_value": 92_352_000_000,
        "currency": "EUR",
        "unit": "EUR",
        "evidence_ids": ["revenue-2025"],
    }

    source_entries = [{"evidence_id": "revenue-2025", "pdf_page_number": 12, "table_index": 3}]
    verified = _claim_result(claim, [row], source_entries)
    contradicted = _claim_result({**claim, "normalized_value": 92_352_000}, [row], source_entries)

    assert verified["status"] == "verified"
    assert contradicted["status"] == "contradicted"
    assert "归一化数值" in contradicted["reason"]


def test_structured_claim_rejects_false_hsbc_profit_semantics_and_accepts_ratio_unit_override() -> None:
    trading_row = {
        "canonical_name": "net_profit",
        "metric_name": "Net income from financial instruments held for trading or managed on a fair value basis",
        "value": 19_682,
        "unit": "million",
        "currency": "USD",
        "scale": 1_000_000,
        "period": "2025-12-31",
        "evidence_id": "trading-income-2025",
    }
    false_profit_claim = {
        "claim_id": "net-profit-current",
        "claim": "2025 年净利润为 196.82 亿美元。",
        "claim_type": "metric_value",
        "metric_key": "net_profit",
        "period": "2025-12-31",
        "normalized_value": 19_682_000_000,
        "currency": "USD",
        "evidence_ids": ["trading-income-2025"],
    }
    insurance_revenue_row = {
        "canonical_name": "operating_revenue",
        "metric_name": "- insurance service revenue",
        "value": 3_228,
        "unit": "million",
        "currency": "USD",
        "scale": 1_000_000,
        "period": "2025-12-31",
        "evidence_id": "insurance-revenue-2025",
    }
    false_revenue_claim = {
        "claim_id": "revenue-current",
        "claim": "2025 年营业收入为 32.28 亿美元。",
        "claim_type": "metric_value",
        "metric_key": "operating_revenue",
        "period": "2025-12-31",
        "normalized_value": 3_228_000_000,
        "currency": "USD",
        "evidence_ids": ["insurance-revenue-2025"],
    }
    ratio_row = {
        "canonical_name": "weighted_avg_roe",
        "metric_name": "加权平均净资产收益率",
        "value": 11.38,
        "unit": "CNY million",
        "currency": "CNY",
        "scale": 1_000_000,
        "period": "2025-12-31",
        "evidence_id": "roe-2025",
    }
    ratio_claim = {
        "claim_id": "roe-current",
        "claim": "加权平均净资产收益率为 11.38%。",
        "claim_type": "metric_value",
        "metric_key": "weighted_avg_roe",
        "period": "2025-12-31",
        "normalized_value": 11.38,
        "unit": "%",
        "evidence_ids": ["roe-2025"],
    }

    source_entries = [
        {"evidence_id": "trading-income-2025", "pdf_page_number": 15, "table_index": 3},
        {"evidence_id": "insurance-revenue-2025", "pdf_page_number": 15, "table_index": 4},
        {"evidence_id": "roe-2025", "pdf_page_number": 20, "table_index": 6},
    ]
    false_profit = _claim_result(false_profit_claim, [trading_row], source_entries)
    false_revenue = _claim_result(false_revenue_claim, [insurance_revenue_row], source_entries)
    ratio = _claim_result(ratio_claim, [ratio_row], source_entries)

    assert false_profit["status"] == "contradicted"
    assert "子项" in false_profit["reason"]
    assert false_revenue["status"] == "contradicted"
    assert "保险服务收入" in false_revenue["reason"]
    assert ratio["status"] == "verified"


def test_numeric_claim_cannot_use_an_unrelated_traceable_reference() -> None:
    row = {
        "canonical_name": "operating_revenue",
        "value": 100,
        "unit": "USD",
        "currency": "USD",
        "period": "2025-12-31",
        "evidence_id": "revenue-current",
    }
    claim = {
        "claim_id": "revenue-claim",
        "claim": "2025 年营业收入为 100 美元。",
        "claim_type": "metric_value",
        "metric_key": "operating_revenue",
        "period": "2025-12-31",
        "normalized_value": 100,
        "currency": "USD",
        "evidence_refs": [
            {
                "report_id": "2025-annual",
                "source_url": "https://example.com/report",
                "section_id": "risk-factors",
            }
        ],
    }

    result = _claim_result(claim, [row], [])

    assert result["status"] == "contradicted"
    assert result["reason"] == "声明证据与对应源指标证据不一致"


def test_metric_change_requires_finite_values_and_both_period_evidence() -> None:
    rows = [
        {
            "canonical_name": "operating_revenue",
            "value": 110,
            "unit": "USD",
            "currency": "USD",
            "period": "2025-12-31",
            "evidence_id": "revenue-2025",
        },
        {
            "canonical_name": "operating_revenue",
            "value": 100,
            "unit": "USD",
            "currency": "USD",
            "period": "2024-12-31",
            "evidence_id": "revenue-2024",
        },
    ]
    source_entries = [
        {"evidence_id": "revenue-2025", "pdf_page_number": 10},
        {"evidence_id": "revenue-2024", "pdf_page_number": 11},
    ]
    claim = {
        "claim_id": "revenue-change",
        "claim": "2025 年营业收入同比增长 10%。",
        "claim_type": "metric_change",
        "metric_key": "operating_revenue",
        "period": "2025-12-31",
        "normalized_value": 110,
        "comparison_period": "2024-12-31",
        "comparison_value": 100,
        "change_pct": 10,
        "currency": "USD",
        "evidence_ids": ["revenue-2025", "revenue-2024"],
    }

    assert _claim_result(claim, rows, source_entries)["status"] == "verified"
    missing_prior = _claim_result(
        {**claim, "evidence_ids": ["revenue-2025"]},
        rows,
        source_entries,
    )
    nonfinite = _claim_result({**claim, "change_pct": "NaN"}, rows, source_entries)

    assert missing_prior["status"] == "contradicted"
    assert "比较期" in missing_prior["reason"]
    assert nonfinite["status"] == "unsupported"
    assert "有限数值" in nonfinite["reason"]


def test_period_comparison_does_not_lexically_reject_same_year_labels() -> None:
    assert _period_is_after("2025-Q4", "2025-12-31") is False
    assert _period_is_after("FY2025", "2025-12-31") is False
    assert _period_is_after("2026-Q1", "2025-12-31") is True
    assert _period_is_after("2026-01-01", "2025-12-31") is True


def test_currency_parser_covers_non_euro_european_reporting_currency() -> None:
    assert _currency_code("CHF million") == "CHF"
    assert _currency_code("US$ thousand") == "USD"
    assert _currency_code("PLN mn") == "PLN"
    assert _currency_code("SEK bn") == "SEK"
    assert _currency_code("人民币百万元") == "CNY"


def test_cn_key_metrics_values_are_pre_normalized_and_years_align_to_report_end() -> None:
    rows = _normalized_metric_rows(
        {
            "schema_version": 1,
            "data": [
                {
                    "name": "营业利润",
                    "canonical_name": "operating_profit",
                    "unit": "人民币百万元",
                    "scale": 1_000_000,
                    "values": {"2025": 51_408_000_000.0, "2024": 55_206_000_000.0},
                    "raw_values": {"2025": "51,408", "2024": "55,206"},
                    "sources": {
                        "2025": {"table_index": 9, "line": 293},
                        "2024": {"table_index": 9, "line": 293},
                    },
                }
            ],
        },
        report_period_end="2025-12-31",
    )

    assert [row["period_key"] for row in rows] == ["2025-12-31", "2024-12-31"]
    assert [row["normalized_value"] for row in rows] == [51_408_000_000.0, 55_206_000_000.0]
    assert rows[0]["raw_value"] == "51,408"
    assert rows[0]["source"] == {"table_index": 9, "line": 293}

    cn_rows_without_declared_end = _normalized_metric_rows(
        {
            "data": [
                {
                    "canonical_name": "weighted_avg_roe",
                    "unit": "人民币百万元",
                    "values": {"2025": 9.15},
                }
            ]
        },
        authoritative_identity={"market": "CN"},
    )
    assert cn_rows_without_declared_end[0]["period_key"] == "2025-12-31"
    assert _periods_equivalent("2025", "2025-12-31") is True
    assert _periods_equivalent("2024", "2025-12-31") is False


def test_cn_claim_binds_legacy_table_line_and_pdf_locator_tokens() -> None:
    rows = _normalized_metric_rows(
        {
            "data": [
                {
                    "name": "营业利润",
                    "canonical_name": "operating_profit",
                    "unit": "人民币百万元",
                    "values": {"2025": 51_408_000_000.0},
                    "sources": {"2025": {"table_index": 9, "line": 293}},
                }
            ]
        },
        authoritative_identity={"market": "CN"},
    )
    claim = {
        "claim_id": "cn-operating-profit",
        "claim": "营业利润为 514.08 亿元。",
        "claim_type": "metric_value",
        "metric_key": "operating_profit",
        "period": "2025-12-31",
        "normalized_value": 51_408_000_000.0,
        "unit": "CNY",
        "currency": "CNY",
        "evidence_ids": ["analysis-evidence"],
        "evidence_refs": [
            {"evidence_id": "analysis-evidence", "table_id": "101", "md_line": 2495}
        ],
    }
    source_entries = [
        {
            "metric_key": "operating_profit",
            "period": "2025",
            "task_id": "task-cn",
            "pdf_page_number": 120,
            "table_index": 101,
            "md_line": 2495,
        }
    ]

    result = _claim_result(claim, rows, source_entries)

    assert result["status"] == "verified"


@pytest.mark.parametrize(
    ("unit", "currency", "raw_value", "normalized_value"),
    (
        ("CHF mn", "CHF", 749, 749_000_000),
        ("US$bn", "USD", 2.5, 2_500_000_000),
        ("PLN mn", "PLN", 120, 120_000_000),
        ("JPY 億円", "JPY", 100, 10_000_000_000),
    ),
)
def test_structured_claim_supports_cross_market_scale_abbreviations(
    unit: str,
    currency: str,
    raw_value: float,
    normalized_value: float,
) -> None:
    evidence_id = f"metric-{currency.lower()}"
    row = {
        "canonical_name": "total_assets",
        "metric_name": "Total assets",
        "value": raw_value,
        "unit": unit,
        "currency": currency,
        "scale": 1,
        "period": "2025-12-31",
        "evidence_id": evidence_id,
    }
    claim = {
        "claim_id": evidence_id,
        "claim": "总资产声明。",
        "claim_type": "metric_value",
        "metric_key": "total_assets",
        "period": "2025-12-31",
        "normalized_value": normalized_value,
        "unit": currency,
        "currency": currency,
        "evidence_ids": [evidence_id],
    }

    result = _claim_result(
        claim,
        [row],
        [{"evidence_id": evidence_id, "pdf_page_number": 10}],
    )

    assert result["status"] == "verified"


def test_insurance_revenue_claim_uses_controlled_source_metric_alias() -> None:
    row = {
        "canonical_name": "operating_revenue",
        "metric_name": "Insurance service revenue",
        "value": 3_228,
        "unit": "USD million",
        "currency": "USD",
        "scale": 1,
        "period": "2025-12-31",
        "evidence_id": "insurance-revenue-2025",
    }
    claim = {
        "claim_id": "insurance-revenue-current",
        "claim": "2025 年保险服务收入为 32.28 亿美元。",
        "claim_type": "metric_value",
        "metric_key": "insurance_revenue",
        "period": "2025-12-31",
        "normalized_value": 3_228_000_000,
        "currency": "USD",
        "unit": "USD",
        "evidence_ids": ["insurance-revenue-2025"],
    }
    source_entries = [{"evidence_id": "insurance-revenue-2025", "pdf_page_number": 15, "table_index": 4}]

    verified = _claim_result(claim, [row], source_entries)
    rejected = _claim_result(
        claim,
        [{**row, "metric_name": "Other operating revenue"}],
        source_entries,
    )

    assert verified["status"] == "verified"
    assert rejected["status"] == "contradicted"
    assert "未明确标注" in rejected["reason"]
