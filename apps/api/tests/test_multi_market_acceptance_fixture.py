from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from services.research_report_package import (
    ResolvedCompany,
    ResolvedReportPackage,
    enumerate_companies,
    enumerate_report_packages,
    resolve_report_package,
)
from siq_market_contracts import AgentArtifactV2, ArtifactQuality, EvidenceRefV1, EvidenceSummary
from tests.fact_surface_hash import (
    PROTECTED_COMPANY_ENTRIES,
    assert_fact_surface_unchanged,
    snapshot_company_fact_surface,
)
from tests.research_universe_fixture import build_six_market_wiki


REPO_ROOT = Path(__file__).resolve().parents[3]
ANALYSIS_PROFILE = "siq_analysis_multi_market"
ANALYSIS_SCRIPTS = REPO_ROOT / "agents" / "hermes" / "profiles" / ANALYSIS_PROFILE / "scripts"
if str(ANALYSIS_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(ANALYSIS_SCRIPTS))

from analysis_input_bundle import build_analysis_input_bundle  # noqa: E402


GOLDEN_PATH = Path(__file__).with_name("golden") / "secondary_market_multi_market_sidecars.json"


def _company(companies: tuple[ResolvedCompany, ...], *, market: str, code: str) -> ResolvedCompany:
    return next(item for item in companies if item.market == market and item.display_code == code)


def _package(
    companies: tuple[ResolvedCompany, ...],
    record: dict[str, Any],
    *,
    wiki_root: Path,
) -> ResolvedReportPackage:
    company = _company(companies, market=record["market"], code=record["display_code"])
    return resolve_report_package(
        market=record["market"],
        company_key=company.company_key,
        report_id=record["report_id"],
        agent_type="analysis",
        wiki_root=wiki_root,
        require_parsed_ready=False,
    )


def _publish_golden_sidecar(
    package: ResolvedReportPackage,
    *,
    sample_id: str,
    expected: dict[str, Any],
    analysis_artifact_id: str | None,
) -> AgentArtifactV2:
    artifact_type = expected["artifact_type"]
    artifact_id = f"golden-{sample_id}-{artifact_type}-v1"
    html = f"<!doctype html><html><body>{sample_id}:{artifact_type}</body></html>"
    output_dir = package.output_dir_for(artifact_type)
    output_dir.mkdir(parents=True, exist_ok=True)
    html_path = output_dir / f"{artifact_id}.html"
    html_path.write_text(html, encoding="utf-8")
    if artifact_type != "analysis" and analysis_artifact_id is None:
        raise AssertionError("downstream golden sidecars require an analysis artifact")
    upstream = () if artifact_type == "analysis" else (analysis_artifact_id,)
    artifact = AgentArtifactV2(
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        status=expected["status"],
        created_at="2026-07-16T00:00:00Z",
        research_target=package.research_target,
        source_report_id=package.report_id,
        source_family=package.research_target.source_report.source_family,
        adapter_version="1.0.0",
        upstream_artifact_ids=upstream,
        html_file=html_path.name,
        content_hash=hashlib.sha256(html.encode("utf-8")).hexdigest(),
        quality=ArtifactQuality(status=expected["quality_status"]),
        evidence_summary=EvidenceSummary(citation_count=1),
        metadata={"golden_sample_id": sample_id},
    )
    sidecar_path = output_dir / f"{artifact_id}.artifact.json"
    sidecar_path.write_text(
        json.dumps(artifact.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return AgentArtifactV2.from_dict(json.loads(sidecar_path.read_text(encoding="utf-8")))


def test_six_market_fixture_is_repeatable_and_encodes_acceptance_edges(tmp_path, monkeypatch) -> None:
    assert ANALYSIS_PROFILE == "siq_analysis_multi_market"
    assert ANALYSIS_SCRIPTS.parent.name == ANALYSIS_PROFILE
    wiki_root = tmp_path / "wiki"
    primary = build_six_market_wiki(wiki_root)
    monkeypatch.setenv("SIQ_US_SEC_ANALYSIS_ENABLED", "1")
    companies = enumerate_companies(wiki_root=wiki_root)
    first_snapshots = {
        company.company_key: snapshot_company_fact_surface(company.company_dir)
        for company in companies
    }

    assert set(primary) == {"CN", "HK", "US", "EU", "KR", "JP"}
    assert {company.market for company in companies} == {"CN", "HK", "US", "EU", "KR", "JP"}
    assert {company.display_code for company in companies if company.market == "US"} == {"AAPL", "BRK.B"}
    assert {company.display_code for company in companies if company.market == "KR"} >= {"005930", "600104"}
    assert _company(companies, market="CN", code="600104").company_key != _company(
        companies,
        market="KR",
        code="600104",
    ).company_key

    aapl = _company(companies, market="US", code="AAPL")
    aapl_packages = enumerate_report_packages(aapl, agent_type="analysis", include_unready=True)
    assert {item.research_target.source_report.form_type for item in aapl_packages} == {"10-K", "10-Q"}
    assert {item.report_id for item in aapl_packages} == {
        "2025-10-K-0000320193-25-000079",
        "2025-10-Q-0000320193-25-000045",
    }
    annual = next(item for item in aapl_packages if item.research_target.source_report.form_type == "10-K")
    assert annual.research_target.source_report.period_end == "2025-09-27"
    assert annual.research_target.source_report.reporting_currency == "USD"

    hsbc = _company(companies, market="HK", code="00005")
    berkshire = _company(companies, market="US", code="BRK.B")
    assert hsbc.company_metadata["industry_profile"] == "bank"
    assert berkshire.company_metadata["industry_profile"] == "insurance"
    assert (berkshire.company_dir / "analysis" / "README.md").is_file()
    assert not list((berkshire.company_dir / "analysis").glob("*.artifact.json"))

    toyota = _company(companies, market="JP", code="7203")
    toyota_package = enumerate_report_packages(toyota, agent_type="analysis")[0]
    assert toyota_package.research_target.source_report.quality_status == "warning"
    failed = _company(companies, market="JP", code="9999")
    assert enumerate_report_packages(failed, agent_type="analysis") == ()
    assert enumerate_report_packages(failed, agent_type="analysis", include_unready=True)[0].readiness[
        "parsed_ready"
    ] is False

    for snapshot in first_snapshots.values():
        assert set(PROTECTED_COMPANY_ENTRIES).issubset(snapshot.covered_entries)
        assert "companies/_index.json" in snapshot.covered_entries
        assert "market/_index.json" in snapshot.covered_entries

    build_six_market_wiki(wiki_root)
    repeated_companies = enumerate_companies(wiki_root=wiki_root)
    assert [(item.market, item.display_code, item.company_key) for item in repeated_companies] == [
        (item.market, item.display_code, item.company_key) for item in companies
    ]
    for company in repeated_companies:
        assert_fact_surface_unchanged(
            first_snapshots[company.company_key],
            snapshot_company_fact_surface(company.company_dir),
        )


def test_golden_sidecar_records_cover_six_markets_and_preserve_all_fact_surfaces(
    tmp_path,
    monkeypatch,
) -> None:
    wiki_root = tmp_path / "wiki"
    build_six_market_wiki(wiki_root)
    monkeypatch.setenv("SIQ_US_SEC_ANALYSIS_ENABLED", "1")
    companies = enumerate_companies(wiki_root=wiki_root)
    before = {
        company.company_key: snapshot_company_fact_surface(company.company_dir)
        for company in companies
    }
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))

    assert golden["schema_version"] == "siq_multi_market_acceptance_v1"
    records = golden["records"]
    assert {record["market"] for record in records} == {"CN", "HK", "US", "EU", "KR", "JP"}
    assert {record["quality_status"] for record in records} >= {"pass", "warning", "fail"}
    assert {record["form_type"] for record in records} >= {None, "10-K", "10-Q"}
    assert {record["reporting_currency"] for record in records} >= {
        "CNY",
        "HKD",
        "USD",
        "EUR",
        "KRW",
        "JPY",
    }
    for record in records:
        expected_types = set() if record["market"] == "CN" or record["quality_status"] == "fail" else {
            "analysis",
            "factcheck",
            "tracking",
        }
        assert {item["artifact_type"] for item in record["result_sidecars"]} == expected_types
        if record["market"] == "CN":
            assert record["acceptance_mode"] == "legacy_golden_readonly"

    for record in records:
        package = _package(companies, record, wiki_root=wiki_root)
        identity = package.research_identity.to_dict()
        assert identity == {
            "market": record["market"],
            "company_id": record["company_id"],
            "filing_id": record["filing_id"],
            "parse_run_id": record["parse_run_id"],
        }
        assert package.research_target.display_code == record["display_code"]
        assert package.research_target.source_report.form_type == record["form_type"]
        assert package.research_target.source_report.source_family == record["source_family"]
        assert package.research_target.source_report.quality_status == record["quality_status"]
        assert package.manifest.get("industry_profile", "industrial") == record["industry_profile"]

        if record["market"] == "CN":
            assert record["result_sidecars"] == []
            assert all(
                not list(package.output_dir_for(kind).glob("*.artifact.json"))
                for kind in ("analysis", "factcheck", "tracking")
            )
            continue

        if record["quality_status"] == "fail":
            assert package.readiness["parsed_ready"] is False
            assert record["result_sidecars"] == []
            assert all(not package.output_dir_for(kind).exists() for kind in ("analysis", "factcheck", "tracking"))
            continue

        bundle = build_analysis_input_bundle(
            research_target=package.to_research_target_dict(),
            company_dir=package.company_dir,
            report_dir=package.report_dir,
            manifest_path=package.manifest_path,
            sec_adapter_enabled=True,
        )
        assert bundle["adapter"] == {
            "name": record["source_family"],
            "version": record["adapter_version"],
            "source_family": record["source_family"],
        }
        assert bundle["quality"]["status"] == record["quality_status"]
        assert bundle["normalized_facts"][0]["currency"] == record["reporting_currency"]
        assert bundle["normalized_facts"][0]["scale"] == record["scale"]
        assert EvidenceRefV1.from_dict(bundle["evidence_refs"][0]).kind == record["evidence_kind"]
        if record["form_type"] == "10-Q":
            assert bundle["normalized_facts"][0]["qtd_ytd_type"] == "qtd"
            assert bundle["normalized_facts"][0]["context_ref"].startswith("ctx-")
        if record["sample_id"] == "us_10k_non_calendar":
            assert package.research_target.source_report.period_end == "2025-09-27"

        analysis_artifact_id: str | None = None
        for expected_sidecar in record["result_sidecars"]:
            artifact = _publish_golden_sidecar(
                package,
                sample_id=record["sample_id"],
                expected=expected_sidecar,
                analysis_artifact_id=analysis_artifact_id,
            )
            if artifact.artifact_type == "analysis":
                analysis_artifact_id = artifact.artifact_id
            assert artifact.schema_version == "siq_agent_artifact_v2"
            assert artifact.status == expected_sidecar["status"]
            assert artifact.source_report_id == record["report_id"]
            assert artifact.source_family == record["source_family"]
            assert artifact.adapter_version == record["adapter_version"]
            assert artifact.quality.status == expected_sidecar["quality_status"]
            assert len(artifact.upstream_artifact_ids) == expected_sidecar["upstream_artifact_count"]
            assert artifact.research_target is not None
            assert artifact.research_target.research_identity.to_dict() == identity

    for company in enumerate_companies(wiki_root=wiki_root):
        assert_fact_surface_unchanged(
            before[company.company_key],
            snapshot_company_fact_surface(company.company_dir),
        )


def test_fact_surface_hash_ignores_derived_agents_but_detects_source_mutation(tmp_path) -> None:
    wiki_root = tmp_path / "wiki"
    primary = build_six_market_wiki(wiki_root)
    company_dir = primary["HK"]
    before = snapshot_company_fact_surface(company_dir)

    for artifact_type in ("analysis", "factcheck", "tracking"):
        output = company_dir / artifact_type / f"{artifact_type}.html"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(f"<html>{artifact_type}</html>", encoding="utf-8")
    assert_fact_surface_unchanged(before, snapshot_company_fact_surface(company_dir))

    report_path = company_dir / "reports" / "2025-annual" / "report.md"
    report_path.write_text("# mutated source report\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="company fact surface changed"):
        assert_fact_surface_unchanged(before, snapshot_company_fact_surface(company_dir))
