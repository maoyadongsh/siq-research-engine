"""Shared exact-identity inputs for factcheck and tracking workflows."""

from __future__ import annotations

import json
import hashlib
import os
import tempfile
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator
from datetime import datetime, timezone
from uuid import uuid4

from siq_market_contracts import AgentArtifactV2, ArtifactQuality, EvidenceSummary

from services.path_config import PROJECT_ROOT
from services.research_report_package import (
    ResolvedReportPackage,
    iter_exact_artifact_sidecars,
    resolve_report_package_from_context,
)
from services.research_universe import ResolvedAgentArtifact, resolve_artifact
from services.research_universe_contracts import ResearchUniverseError


@dataclass(frozen=True)
class ResolvedSpecialistTarget:
    package: ResolvedReportPackage
    analysis_artifact: ResolvedAgentArtifact

    def to_bundle(self) -> dict[str, Any]:
        package = self.package
        previous_tracking = next(
            (
                artifact
                for artifact, _sidecar, _html in iter_exact_artifact_sidecars(package, "tracking")
                if artifact.status in {"completed", "degraded"}
            ),
            None,
        )
        metrics_path = _preferred_path(
            package.metric_paths,
            ("normalized_metrics.json", "key_metrics.json", "financial_data.json"),
        )
        source_map_path = _preferred_path(
            package.evidence_paths,
            ("source_map.json", "source_map_latest.json", "evidence_index.json", "pdf_refs.json"),
        )
        financial_checks_path = _preferred_path(
            package.metric_paths,
            ("financial_checks.json", "validation.json"),
            required=False,
        )
        if metrics_path is None or source_map_path is None:
            raise ResearchUniverseError(
                "source_package_not_ready",
                "The selected report does not expose metrics and an evidence source map.",
                409,
            )
        return {
            "schema_version": "siq_specialist_target_bundle_v1",
            "research_target": package.to_research_target_dict(),
            "baseline_analysis_artifact_id": self.analysis_artifact.artifact.artifact_id,
            "baseline_analysis_content_hash": self.analysis_artifact.artifact.content_hash,
            "previous_tracking_checkpoint": (
                {
                    "artifact_id": previous_tracking.artifact_id,
                    "created_at": previous_tracking.created_at,
                    "content_hash": previous_tracking.content_hash,
                    "adapter_version": previous_tracking.adapter_version,
                }
                if previous_tracking
                else None
            ),
            "resolved_paths": {
                "company_dir": str(package.company_dir),
                "report_dir": str(package.report_dir),
                "manifest_path": str(package.manifest_path),
                "analysis_artifact": str(self.analysis_artifact.html_path),
                "analysis_sidecar": str(self.analysis_artifact.sidecar_path or ""),
                "metrics_path": str(metrics_path),
                "source_map_path": str(source_map_path),
                "financial_checks_path": str(financial_checks_path or ""),
            },
        }


def _preferred_path(
    paths: tuple[Path, ...],
    names: tuple[str, ...],
    *,
    required: bool = True,
) -> Path | None:
    for name in names:
        for path in paths:
            if path.name == name and path.is_file():
                return path
    if required:
        return next((path for path in paths if path.is_file()), None)
    return None


def upstream_analysis_artifact_id(context: Any) -> str:
    if hasattr(context, "model_dump"):
        context = context.model_dump(exclude_none=True)
    raw = dict(context) if isinstance(context, Mapping) else {}
    source_report = raw.get("source_report") if isinstance(raw.get("source_report"), Mapping) else {}
    capabilities = raw.get("capabilities") if isinstance(raw.get("capabilities"), Mapping) else {}
    return str(
        raw.get("upstream_analysis_artifact_id")
        or raw.get("baseline_analysis_artifact_id")
        or source_report.get("baseline_analysis_artifact_id")
        or capabilities.get("baseline_analysis_artifact_id")
        or ""
    ).strip()


def has_structured_research_target(context: Any) -> bool:
    if hasattr(context, "model_dump"):
        context = context.model_dump(exclude_none=True)
    raw = dict(context) if isinstance(context, Mapping) else {}
    company = raw.get("company") if isinstance(raw.get("company"), Mapping) else {}
    source_report = raw.get("source_report") if isinstance(raw.get("source_report"), Mapping) else {}
    return bool(
        (raw.get("market") or company.get("market"))
        and (raw.get("company_key") or company.get("company_key"))
        and (raw.get("report_id") or source_report.get("report_id"))
    )


def resolve_specialist_target(
    context: Any,
    *,
    agent_type: str,
    artifact_id: str | None = None,
    wiki_root: Path | str | None = None,
) -> ResolvedSpecialistTarget:
    package = resolve_report_package_from_context(
        context,
        agent_type=agent_type,
        wiki_root=wiki_root,
    )
    baseline_id = str(artifact_id or upstream_analysis_artifact_id(context)).strip()
    if not baseline_id:
        raise ResearchUniverseError(
            "analysis_baseline_required",
            "The selected source report does not have an exact analysis baseline.",
            409,
        )
    baseline = resolve_artifact(
        baseline_id,
        expected_identity=package.research_identity,
        artifact_type="analysis",
        wiki_root=wiki_root,
    )
    if baseline.market != package.market or baseline.company_key != package.company_key:
        raise ResearchUniverseError(
            "artifact_identity_mismatch",
            "The selected analysis baseline belongs to another company or market.",
            409,
        )
    if baseline.artifact.source_report_id != package.report_id:
        raise ResearchUniverseError(
            "artifact_identity_mismatch",
            "The selected analysis baseline belongs to another source report.",
            409,
        )
    return ResolvedSpecialistTarget(package=package, analysis_artifact=baseline)


@contextmanager
def materialized_target_bundle(
    target: ResolvedSpecialistTarget,
    *,
    prefix: str,
) -> Iterator[Path]:
    runtime_root = PROJECT_ROOT / "var" / "workflow-inputs"
    runtime_root.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix=f"{prefix}-",
        suffix=".json",
        dir=runtime_root,
        delete=False,
    )
    path = Path(handle.name)
    try:
        with handle:
            json.dump(target.to_bundle(), handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        yield path
    finally:
        path.unlink(missing_ok=True)


def publish_agent_artifact_v2(
    target: ResolvedSpecialistTarget,
    *,
    artifact_type: str,
    html_path: Path,
    status: str,
    adapter_version: str,
    citation_count: int,
    unresolved_count: int,
    warnings: list[str] | tuple[str, ...] = (),
    metadata: Mapping[str, Any] | None = None,
) -> tuple[AgentArtifactV2, Path, Path]:
    output_dir = target.package.output_dir_for(artifact_type).resolve()
    html_path = html_path.expanduser().resolve()
    try:
        html_path.relative_to(output_dir)
    except ValueError as exc:
        raise ResearchUniverseError(
            "unsafe_path_rejected",
            "The generated artifact escaped its approved output directory.",
            400,
        ) from exc
    html_bytes = html_path.read_bytes()
    digest = hashlib.sha256(html_bytes).hexdigest()
    now = datetime.now(timezone.utc)
    artifact_id = f"{artifact_type}_{now.strftime('%Y%m%dT%H%M%S')}_{uuid4().hex[:12]}"
    canonical_html_path = output_dir / f"{artifact_id}.html"
    temporary_html_path = canonical_html_path.with_suffix(".html.tmp")
    legacy_alias = (
        html_path.relative_to(output_dir).as_posix()
        if html_path != canonical_html_path
        else ""
    )
    artifact_metadata = dict(metadata or {})
    if legacy_alias:
        artifact_metadata["legacy_aliases"] = [legacy_alias]
    quality_status = "pass" if status == "completed" and not warnings else ("warning" if status != "failed" else "fail")
    artifact = AgentArtifactV2(
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        status=status,
        created_at=now.isoformat().replace("+00:00", "Z"),
        research_target=target.package.research_target,
        source_report_id=target.package.report_id,
        source_family=target.package.research_target.source_report.source_family,
        adapter_version=adapter_version,
        upstream_artifact_ids=(target.analysis_artifact.artifact.artifact_id,),
        html_file=canonical_html_path.name,
        content_hash=digest,
        quality=ArtifactQuality(status=quality_status, warnings=tuple(warnings)),
        evidence_summary=EvidenceSummary(
            citation_count=citation_count,
            unresolved_count=unresolved_count,
        ),
        metadata=artifact_metadata,
    )
    sidecar_path = output_dir / f"{artifact.artifact_id}.artifact.json"
    temporary = sidecar_path.with_suffix(sidecar_path.suffix + ".tmp")
    try:
        temporary_html_path.write_bytes(html_bytes)
        temporary.write_text(
            json.dumps(artifact.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        # The sidecar is the discovery marker. Commit it before the HTML so a
        # crash cannot leave a discoverable-looking HTML without its contract.
        # Readers already reject a sidecar whose referenced HTML is absent.
        temporary.replace(sidecar_path)
        temporary_html_path.replace(canonical_html_path)
        if legacy_alias:
            html_path.unlink()
            html_path.symlink_to(os.path.relpath(canonical_html_path, start=html_path.parent))
    except Exception:
        if legacy_alias and html_path.is_symlink():
            html_path.unlink(missing_ok=True)
        if legacy_alias and not html_path.exists():
            html_path.parent.mkdir(parents=True, exist_ok=True)
            html_path.write_bytes(html_bytes)
        temporary_html_path.unlink(missing_ok=True)
        temporary.unlink(missing_ok=True)
        sidecar_path.unlink(missing_ok=True)
        canonical_html_path.unlink(missing_ok=True)
        raise
    return artifact, sidecar_path, canonical_html_path


__all__ = [
    "ResolvedSpecialistTarget",
    "has_structured_research_target",
    "materialized_target_bundle",
    "publish_agent_artifact_v2",
    "resolve_specialist_target",
    "upstream_analysis_artifact_id",
]
