from __future__ import annotations

from pathlib import Path

import pytest
from services.research_report_package import (
    enumerate_companies,
    enumerate_report_packages,
    resolve_report_package,
    resolve_report_package_from_context,
)
from services.research_universe_contracts import ResearchUniverseError
from tests.research_universe_fixture import add_company, build_six_market_wiki


def _company(wiki_root: Path, market: str):
    return next(item for item in enumerate_companies(wiki_root=wiki_root, markets=(market,)))


def test_resolver_uses_sec_manifest_paths_without_a_share_compatibility_files(tmp_path, monkeypatch) -> None:
    wiki_root = tmp_path / "wiki"
    build_six_market_wiki(wiki_root)
    monkeypatch.setenv("SIQ_US_SEC_ANALYSIS_ENABLED", "1")
    company = _company(wiki_root, "US")

    package = resolve_report_package(
        market="US",
        company_key=company.company_key,
        report_id="2025-10-K-0000320193-25-000079",
        agent_type="analysis",
        wiki_root=wiki_root,
    )

    assert package.readiness["parsed_ready"] is True
    assert package.capabilities["analysis_input_ready"] is True
    assert package.research_target.source_report.source_family == "sec_ixbrl"
    assert package.research_target.source_report.reporting_currency == "USD"
    assert package.research_identity.to_dict() == {
        "market": "US",
        "company_id": "US:0000320193",
        "filing_id": "US:0000320193:0000320193-25-000079",
        "parse_run_id": "run-us-aapl",
    }
    assert {path.relative_to(package.report_dir).as_posix() for path in package.fulltext_paths} >= {
        "parser/document_full.json",
        "sections/report_complete.md",
    }
    assert "metrics/normalized_metrics.json" in {
        path.relative_to(package.report_dir).as_posix() for path in package.metric_paths
    }
    assert "qa/source_map.json" in {path.relative_to(package.report_dir).as_posix() for path in package.evidence_paths}
    assert "xbrl/facts_raw.json" in {path.relative_to(package.report_dir).as_posix() for path in package.xbrl_paths}
    assert package.to_research_target_dict()["company_key"] == company.company_key

    factcheck_package = resolve_report_package(
        market="US",
        company_key=company.company_key,
        report_id=package.report_id,
        agent_type="factcheck",
        wiki_root=wiki_root,
    )
    assert factcheck_package.output_dir == factcheck_package.output_dirs["factcheck"]


def test_cn_legacy_package_gets_authoritative_task_bound_identity(tmp_path) -> None:
    wiki_root = tmp_path / "wiki"
    build_six_market_wiki(wiki_root)
    company = _company(wiki_root, "CN")

    package = resolve_report_package(
        market="CN",
        company_key=company.company_key,
        report_id="2025-annual",
        agent_type="analysis",
        wiki_root=wiki_root,
    )

    assert package.compatibility_mode == "cn_legacy_artifact_manifest"
    assert package.research_identity.filing_id == "CN:600104-上汽集团:2025-annual"
    assert package.research_identity.parse_run_id == "task-cn-600104"
    assert package.readiness["parsed_ready"] is True


def test_context_resolver_revalidates_exact_identity_and_rejects_client_paths(tmp_path, monkeypatch) -> None:
    wiki_root = tmp_path / "wiki"
    build_six_market_wiki(wiki_root)
    monkeypatch.setenv("SIQ_MULTI_MARKET_RESEARCH_ENABLED", "1")
    company = _company(wiki_root, "US")
    context = {
        "market": "US",
        "company": {"company_key": company.company_key},
        "source_report": {"report_id": "2025-10-K-0000320193-25-000079"},
        "research_identity": {
            "market": "US",
            "company_id": "US:0000320193",
            "filing_id": "US:0000320193:0000320193-25-000079",
            "parse_run_id": "run-us-aapl",
        },
    }

    package = resolve_report_package_from_context(context, agent_type="analysis", wiki_root=wiki_root)
    assert package.company_key == company.company_key

    target_only = resolve_report_package_from_context(
        {"research_target": package.to_research_target_dict()},
        agent_type="analysis",
        wiki_root=wiki_root,
    )
    assert target_only.research_identity == package.research_identity

    with pytest.raises(ResearchUniverseError) as mismatch:
        resolve_report_package_from_context(
            {
                **context,
                "research_identity": {**context["research_identity"], "parse_run_id": "other-run"},
            },
            agent_type="analysis",
            wiki_root=wiki_root,
        )
    assert mismatch.value.code == "research_identity_mismatch"

    with pytest.raises(ResearchUniverseError) as unsafe:
        resolve_report_package_from_context(
            {**context, "company_dir": "/tmp/client-controlled"},
            agent_type="analysis",
            wiki_root=wiki_root,
        )
    assert unsafe.value.code == "unsafe_path_rejected"


def test_cross_market_company_key_and_traversing_report_id_are_rejected(tmp_path) -> None:
    wiki_root = tmp_path / "wiki"
    build_six_market_wiki(wiki_root)
    us_company = _company(wiki_root, "US")

    with pytest.raises(ResearchUniverseError) as mismatch:
        resolve_report_package(
            market="HK",
            company_key=us_company.company_key,
            report_id="2025-annual",
            agent_type="analysis",
            wiki_root=wiki_root,
        )
    assert mismatch.value.code == "company_market_mismatch"

    with pytest.raises(ResearchUniverseError) as traversal:
        resolve_report_package(
            market="US",
            company_key=us_company.company_key,
            report_id="../outside",
            agent_type="analysis",
            wiki_root=wiki_root,
        )
    assert traversal.value.code == "source_report_not_found"


def test_manifest_path_traversal_and_symlink_escape_are_rejected(tmp_path) -> None:
    wiki_root = tmp_path / "wiki"
    company_dir = add_company(
        wiki_root,
        market="US",
        code="BAD",
        name="Unsafe Corp",
        company_id="US:0000000001",
        report_id="2025-10-K-bad",
        filing_id="US:0000000001:bad",
        parse_run_id="run-bad",
        source_family="sec_ixbrl",
        form_type="10-K",
        unsafe_document_path="../../../../outside.json",
    )
    company = _company(wiki_root, "US")

    with pytest.raises(ResearchUniverseError) as traversal:
        resolve_report_package(
            market="US",
            company_key=company.company_key,
            report_id="2025-10-K-bad",
            agent_type="analysis",
            wiki_root=wiki_root,
        )
    assert traversal.value.code == "unsafe_path_rejected"

    report_dir = company_dir / "reports" / "2025-10-K-bad"
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    link = report_dir / "parser" / "document_full.json"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(outside)
    manifest_path = report_dir / "manifest.json"
    manifest = __import__("json").loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"]["document_full"] = "parser/document_full.json"
    manifest_path.write_text(__import__("json").dumps(manifest), encoding="utf-8")

    with pytest.raises(ResearchUniverseError) as symlink:
        resolve_report_package(
            market="US",
            company_key=company.company_key,
            report_id="2025-10-K-bad",
            agent_type="analysis",
            wiki_root=wiki_root,
        )
    assert symlink.value.code == "unsafe_path_rejected"


def test_report_index_and_manifest_identity_conflict_is_not_parsed_ready(tmp_path) -> None:
    wiki_root = tmp_path / "wiki"
    company_dir = add_company(
        wiki_root,
        market="US",
        code="DRIFT",
        name="Identity Drift",
        company_id="US:0000000002",
        report_id="2025-10-K-drift",
        filing_id="US:0000000002:manifest-filing",
        parse_run_id="manifest-run",
        source_family="sec_ixbrl",
        form_type="10-K",
    )
    company_json_path = company_dir / "company.json"
    payload = __import__("json").loads(company_json_path.read_text(encoding="utf-8"))
    payload["reports"][0]["filing_id"] = "US:0000000002:different-filing"
    company_json_path.write_text(__import__("json").dumps(payload), encoding="utf-8")
    company = _company(wiki_root, "US")

    packages = enumerate_report_packages(company, agent_type="analysis", include_unready=True)

    assert len(packages) == 1
    assert packages[0].readiness["identity_ready"] is False
    assert packages[0].readiness["parsed_ready"] is False
    assert "report_manifest_identity_mismatch" in packages[0].degraded_reasons
