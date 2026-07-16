from __future__ import annotations

import hashlib
import json
from pathlib import Path

from services.research_universe import ResolvedAgentArtifact
from services.specialist_research_target import ResolvedSpecialistTarget
from siq_market_contracts import AgentArtifactV2, ArtifactQuality, EvidenceSummary


def write_analysis_target(
    package,
    *,
    artifact_id: str = "analysis-us-aapl-v1",
    html: str = "<!doctype html><html><body>risk factors and liquidity analysis</body></html>",
) -> ResolvedSpecialistTarget:
    output_dir = package.output_dir_for("analysis")
    output_dir.mkdir(parents=True, exist_ok=True)
    html_path = output_dir / f"{artifact_id}.html"
    html_path.write_text(html, encoding="utf-8")
    evidence_ref = {
        "source_type": "sec_html_section",
        "report_id": package.report_id,
        "source_url": "https://www.sec.gov/example",
        "section_id": "item_1a",
        "html_anchor": "item_1a",
    }
    artifact = AgentArtifactV2(
        artifact_id=artifact_id,
        artifact_type="analysis",
        status="completed",
        created_at="2026-07-16T00:00:00Z",
        research_target=package.research_target,
        source_report_id=package.report_id,
        source_family=package.research_target.source_report.source_family,
        adapter_version="test_analysis_v1",
        upstream_artifact_ids=(),
        html_file=html_path.name,
        content_hash=hashlib.sha256(html.encode("utf-8")).hexdigest(),
        quality=ArtifactQuality(status="pass"),
        evidence_summary=EvidenceSummary(citation_count=1),
        metadata={
            "claims": [
                {
                    "claim_id": "risk-1",
                    "claim": "Risk factors are disclosed.",
                    "claim_type": "disclosure",
                    "evidence_refs": [evidence_ref],
                }
            ],
            "citations": [evidence_ref],
        },
    )
    sidecar_path = output_dir / f"{artifact_id}.artifact.json"
    sidecar_path.write_text(
        json.dumps(artifact.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    resolved = ResolvedAgentArtifact(
        artifact=artifact,
        market=package.market,
        company_key=package.company_key,
        sidecar_path=sidecar_path,
        html_path=html_path,
    )
    return ResolvedSpecialistTarget(package=package, analysis_artifact=resolved)
