from __future__ import annotations

import pytest

from siq_market_contracts import (
    AgentArtifactV2,
    ArtifactQuality,
    ContractValidationError,
    EvidenceKind,
    EvidenceRefV1,
    EvidenceSummary,
    NormalizedFactV1,
    ResearchIdentity,
    ResearchTargetV1,
    SourceReportV1,
)


def _identity() -> ResearchIdentity:
    return ResearchIdentity(
        market="US",
        company_id="US:0000320193",
        filing_id="US:0000320193:0000320193-25-000079",
        parse_run_id="run-aapl-2025",
    )


def _target() -> ResearchTargetV1:
    return ResearchTargetV1(
        company_key="rk1_safeopaque",
        company_wiki_id="AAPL-Apple-Inc",
        display_code="AAPL",
        display_name="Apple Inc.",
        research_identity=_identity(),
        source_report=SourceReportV1(
            report_id="2025-10-K-0000320193-25-000079",
            source_family="sec_ixbrl",
            document_format="ixbrl_html",
            report_type="annual",
            form_type="10-K",
            fiscal_year=2025,
            period_end="2025-09-27",
            published_at="2025-10-31",
            accounting_standard="US_GAAP",
            reporting_currency="usd",
            quality_status="warning",
        ),
    )


def test_research_target_round_trip_keeps_exact_identity_and_raw_currency() -> None:
    target = _target()

    restored = ResearchTargetV1.from_dict(target.to_dict())

    assert restored == target
    assert restored.source_report.reporting_currency == "USD"
    assert restored.research_identity.to_dict() == {
        "market": "US",
        "company_id": "US:0000320193",
        "filing_id": "US:0000320193:0000320193-25-000079",
        "parse_run_id": "run-aapl-2025",
    }


@pytest.mark.parametrize("missing", ["company_id", "filing_id", "parse_run_id"])
def test_research_identity_fails_closed_when_any_field_is_missing(missing: str) -> None:
    payload = _identity().to_dict()
    payload[missing] = ""

    with pytest.raises(ContractValidationError, match=missing):
        ResearchIdentity.from_dict(payload)


def test_research_identity_rejects_cross_market_identifier() -> None:
    with pytest.raises(ContractValidationError, match="conflicts with market"):
        ResearchIdentity(
            market="US",
            company_id="HK:00005",
            filing_id="US:filing",
            parse_run_id="run",
        )


@pytest.mark.parametrize(
    ("kind", "locator"),
    [
        (EvidenceKind.PDF_PAGE.value, {"pdf_task_id": "task-1", "pdf_page": 12}),
        (EvidenceKind.HTML_ANCHOR.value, {"source_url": "https://sec.gov/a", "html_anchor": "item7"}),
        (EvidenceKind.XBRL_FACT.value, {"xbrl_fact_id": "fact-123"}),
    ],
)
def test_evidence_ref_accepts_pdf_html_and_xbrl_locators(kind: str, locator: dict) -> None:
    ref = EvidenceRefV1(
        research_identity=_identity(),
        report_id="2025-10-K-0000320193-25-000079",
        kind=kind,
        quote="short excerpt",
        **locator,
    )

    assert EvidenceRefV1.from_dict(ref.to_dict()) == ref


def test_evidence_ref_never_accepts_locator_free_sec_citation() -> None:
    with pytest.raises(ContractValidationError, match="locator is incomplete"):
        EvidenceRefV1(
            research_identity=_identity(),
            report_id="2025-10-K-0000320193-25-000079",
            kind="html_anchor",
            quote="no anchor",
        )


def test_normalized_fact_preserves_raw_value_scale_and_missing_value() -> None:
    identity = _identity()
    fact = NormalizedFactV1(
        metric_key="revenue",
        raw_label="Revenue",
        raw_value="391035",
        normalized_value=None,
        currency="USD",
        raw_unit="USD millions",
        scale=1_000_000,
        period_start="2024-09-29",
        period_end="2025-09-27",
        accounting_standard="US_GAAP",
        research_identity=identity,
        evidence_refs=(
            EvidenceRefV1(
                research_identity=identity,
                report_id="2025-10-K-0000320193-25-000079",
                kind="xbrl_fact",
                xbrl_fact_id="fact-123",
            ),
        ),
    )

    restored = NormalizedFactV1.from_dict(fact.to_dict())

    assert restored == fact
    assert restored.normalized_value is None
    assert restored.raw_value == "391035"
    assert restored.scale == 1_000_000


def test_normalized_fact_rejects_cross_filing_evidence() -> None:
    other = ResearchIdentity(
        market="US",
        company_id="US:0000320193",
        filing_id="US:0000320193:other-filing",
        parse_run_id="other-run",
    )
    with pytest.raises(ContractValidationError, match="evidence identity mismatch"):
        NormalizedFactV1(
            metric_key="revenue",
            raw_label="Revenue",
            raw_value="1",
            normalized_value=1,
            currency="USD",
            raw_unit="USD",
            scale=1,
            period_start=None,
            period_end="2025-09-27",
            accounting_standard="US_GAAP",
            research_identity=_identity(),
            evidence_refs=(
                EvidenceRefV1(
                    research_identity=other,
                    report_id="other",
                    kind="xbrl_fact",
                    xbrl_fact_id="other-fact",
                ),
            ),
        )


def test_agent_artifact_round_trip_and_downstream_binding() -> None:
    artifact = AgentArtifactV2(
        artifact_id="aapl-analysis-v1",
        artifact_type="analysis",
        status="completed",
        created_at="2026-07-16T00:00:00Z",
        research_target=_target(),
        source_report_id="2025-10-K-0000320193-25-000079",
        source_family="sec_ixbrl",
        adapter_version="sec_ixbrl_v1",
        upstream_artifact_ids=(),
        html_file="aapl-analysis-v1.html",
        content_hash="a" * 64,
        quality=ArtifactQuality(status="pass"),
        evidence_summary=EvidenceSummary(citation_count=10),
    )

    restored = AgentArtifactV2.from_dict(artifact.to_dict())

    assert restored == artifact
    assert restored.identity_status == "exact"

    with pytest.raises(ContractValidationError, match="upstream analysis"):
        AgentArtifactV2(
            **{
                **artifact.to_dict(),
                "artifact_id": "factcheck-v1",
                "artifact_type": "factcheck",
                "html_file": "factcheck-v1.html",
                "quality": artifact.quality,
                "evidence_summary": artifact.evidence_summary,
                "research_target": artifact.research_target,
            }
        )


def test_legacy_artifact_is_explicitly_unbound() -> None:
    artifact = AgentArtifactV2.legacy_unbound(
        artifact_id="legacy_a1",
        artifact_type="analysis",
        html_file="old-report.html",
        created_at="2026-07-16T00:00:00Z",
    )

    assert artifact.status == "legacy_unbound"
    assert artifact.identity_status == "legacy_unbound"
    assert artifact.research_target is None


def test_legacy_specialist_json_is_read_without_fabricating_identity() -> None:
    artifact = AgentArtifactV2.from_dict(
        {
            "schema_version": "siq_specialist_artifact_v1",
            "artifact_type": "factcheck",
            "html_url": "/api/wiki/companies/600104/factcheck/old-factcheck.html",
            "created_at": "2026-07-16T00:00:00Z",
            "research_identity": {"market": "CN", "company_id": "fabricated"},
        }
    )

    assert artifact.status == "legacy_unbound"
    assert artifact.html_file == "old-factcheck.html"
    assert artifact.research_target is None
