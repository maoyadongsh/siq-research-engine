"""Read-only multi-market universe enumeration and derived artifact access."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from siq_market_contracts import AgentArtifactV2, ResearchIdentity

from services.research_report_package import (
    ResolvedCompany,
    ResolvedReportPackage,
    baseline_analysis_artifact_id,
    enumerate_companies,
    enumerate_report_packages,
    exact_artifact_bindings,
    has_exact_artifact_metadata,
    iter_exact_artifact_sidecars,
    page_exact_artifact_sidecars,
    resolve_company,
    resolve_report_package,
)
from services.research_universe_contracts import (
    RESEARCH_MARKET_METADATA,
    RESEARCH_MARKET_ORDER,
    ResearchUniverseError,
    normalize_agent_type,
    normalize_artifact_type,
    normalize_market,
)


@dataclass(frozen=True)
class ResolvedAgentArtifact:
    artifact: AgentArtifactV2
    market: str
    company_key: str
    sidecar_path: Path | None
    html_path: Path
    legacy_unbound: bool = False

    def to_api_dict(self) -> dict[str, Any]:
        payload = {
            "artifact_id": self.artifact.artifact_id,
            "artifact_type": self.artifact.artifact_type,
            "status": self.artifact.status,
            "created_at": self.artifact.created_at,
            "source_report_id": self.artifact.source_report_id,
            "source_family": self.artifact.source_family,
            "adapter_version": self.artifact.adapter_version,
            "upstream_artifact_ids": list(self.artifact.upstream_artifact_ids),
            "quality": {
                "status": self.artifact.quality.status,
                "warnings": list(self.artifact.quality.warnings),
            },
            "evidence_summary": {
                "citation_count": self.artifact.evidence_summary.citation_count,
                "unresolved_count": self.artifact.evidence_summary.unresolved_count,
            },
            "identity_status": self.artifact.identity_status,
            "filename": self.html_path.name,
            "usable_as_baseline": self.artifact.identity_status == "exact"
            and self.artifact.artifact_type == "analysis"
            and self.artifact.status in {"completed", "degraded"},
            "content_url": f"/api/research-universe/artifacts/{self.artifact.artifact_id}/content",
        }
        if self.artifact.research_target is not None:
            payload["research_identity"] = self.artifact.research_target.research_identity.to_dict()
        return payload


@dataclass(frozen=True)
class _ArtifactPage:
    items: tuple[ResolvedAgentArtifact, ...]
    next_offset: int
    has_more: bool


def _lazy_artifact_api_dict(item: ResolvedAgentArtifact) -> dict[str, Any]:
    payload = item.to_api_dict()
    payload["content_integrity_status"] = (
        "unavailable" if item.legacy_unbound else "deferred_until_content_request"
    )
    return payload


def _flag(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def multi_market_research_enabled() -> bool:
    return _flag("SIQ_MULTI_MARKET_RESEARCH_ENABLED")


def enabled_markets(agent_type: str) -> tuple[str, ...]:
    agent_type = normalize_agent_type(agent_type)
    if agent_type == "legal" or not multi_market_research_enabled():
        return ("CN",)
    return RESEARCH_MARKET_ORDER


def _report_label(package: ResolvedReportPackage) -> str:
    report = package.research_target.source_report
    parts = [str(report.fiscal_year) if report.fiscal_year else None, report.form_type or report.report_type]
    if report.period_end:
        parts.append(f"截止 {report.period_end}")
    if report.quality_status == "warning":
        parts.append("warning")
    return " · ".join(item for item in parts if item) or report.report_id


def _source_filename(package: ResolvedReportPackage) -> str | None:
    raw = str(
        package.manifest.get("source_filename")
        or package.manifest.get("local_source_path")
        or ""
    ).strip()
    if not raw:
        return None
    return Path(raw.replace("\\", "/")).name or None


_BASELINE_UNSET = object()


def _package_capabilities(
    package: ResolvedReportPackage,
    *,
    baseline: str | None | object = _BASELINE_UNSET,
) -> dict[str, bool]:
    if baseline is _BASELINE_UNSET:
        baseline = baseline_analysis_artifact_id(package)
    analysis_output_ready = baseline is not None
    citations_available = bool(package.evidence_paths)
    return {
        **dict(package.capabilities),
        "analysis_output_ready": analysis_output_ready,
        "factcheck_ready": analysis_output_ready and citations_available,
        "tracking_ready": analysis_output_ready,
    }


def _company_report_packages(company: ResolvedCompany, agent_type: str) -> tuple[ResolvedReportPackage, ...]:
    return enumerate_report_packages(company, agent_type=agent_type, include_unready=False)


def list_markets(
    *,
    agent_type: str,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    agent_type = normalize_agent_type(agent_type)
    markets = enabled_markets(agent_type)
    companies = enumerate_companies(wiki_root=wiki_root, markets=markets)
    rows: list[dict[str, Any]] = []
    for market in markets:
        market_companies = [item for item in companies if item.market == market]
        ready_count = sum(bool(_company_report_packages(item, agent_type)) for item in market_companies)
        degraded_reasons: list[str] = []
        if market == "US" and not _flag("SIQ_US_SEC_ANALYSIS_ENABLED"):
            degraded_reasons.append("source_adapter_unavailable")
        rows.append(
            {
                "market": market,
                "label": RESEARCH_MARKET_METADATA[market]["label"],
                "order": RESEARCH_MARKET_METADATA[market]["order"],
                "enabled": True,
                "company_count": ready_count,
                "capabilities": {
                    "parsed_company_selection": ready_count > 0,
                    "analysis_adapter": not degraded_reasons,
                },
                "degraded_reasons": degraded_reasons,
            }
        )
    return {"markets": rows}


def list_companies(
    *,
    market: str,
    agent_type: str,
    q: str = "",
    include_unready: bool = False,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    market = normalize_market(market)
    agent_type = normalize_agent_type(agent_type)
    if market not in enabled_markets(agent_type):
        raise ResearchUniverseError("market_not_supported", "The market is not enabled for this agent.", 404)
    query = str(q or "").strip().casefold()
    rows: list[dict[str, Any]] = []
    for company in enumerate_companies(wiki_root=wiki_root, markets=(market,)):
        if query and query not in " ".join(
            (company.display_code, company.display_name, company.company_id, company.company_wiki_id)
        ).casefold():
            continue
        packages = enumerate_report_packages(
            company,
            agent_type=agent_type,
            include_unready=include_unready,
        )
        if not packages:
            continue
        analysis_output_by_package = {
            package.report_id: has_exact_artifact_metadata(package, "analysis")
            for package in packages
        }
        analysis_output_ready = any(analysis_output_by_package.values())
        capabilities = {
            "analysis_input_ready": any(item.capabilities["analysis_input_ready"] for item in packages),
            "analysis_output_ready": analysis_output_ready,
            "factcheck_ready": any(
                analysis_output_by_package[package.report_id] and bool(package.evidence_paths)
                for package in packages
            ),
            "tracking_ready": analysis_output_ready,
        }
        degraded_reasons = list(
            dict.fromkeys(reason for package in packages for reason in package.degraded_reasons)
        )
        report_company_ids = {
            package.research_identity.company_id
            for package in packages
            if package.readiness["identity_ready"]
        }
        canonical_company_id = (
            next(iter(report_company_ids)) if len(report_company_ids) == 1 else company.company_id
        )
        rows.append(
            {
                "company_key": company.company_key,
                "market": market,
                "company_id": canonical_company_id,
                "company_wiki_id": company.company_wiki_id,
                "display_code": company.display_code,
                "display_name": company.display_name,
                "parsed_report_count": sum(item.readiness["parsed_ready"] for item in packages),
                "readiness": {
                    "catalog_visible": True,
                    "parsed_ready": any(item.readiness["parsed_ready"] for item in packages),
                },
                "capabilities": capabilities,
                "degraded_reasons": degraded_reasons,
            }
        )
    return {"market": market, "companies": rows}


def list_reports(
    *,
    market: str,
    company_key: str,
    agent_type: str,
    include_unready: bool = False,
    defer_artifact_integrity: bool = False,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    market = normalize_market(market)
    agent_type = normalize_agent_type(agent_type)
    if market not in enabled_markets(agent_type):
        raise ResearchUniverseError("market_not_supported", "The market is not enabled for this agent.", 404)
    company = resolve_company(market=market, company_key=company_key, wiki_root=wiki_root)
    rows: list[dict[str, Any]] = []
    packages = enumerate_report_packages(company, agent_type=agent_type, include_unready=include_unready)
    for package in packages:
        report = package.research_target.source_report
        baseline_id = baseline_analysis_artifact_id(
            package,
            verify_content=not defer_artifact_integrity,
        )
        capabilities = _package_capabilities(package, baseline=baseline_id)
        rows.append(
            {
                "report_id": report.report_id,
                "label": _report_label(package),
                "report_type": report.report_type,
                "form_type": report.form_type,
                "fiscal_year": report.fiscal_year,
                "period_end": report.period_end,
                "published_at": report.published_at,
                "filename": _source_filename(package),
                "quality_status": report.quality_status,
                "source_family": report.source_family,
                "document_format": report.document_format,
                "research_identity": package.research_identity.to_dict(),
                "readiness": dict(package.readiness),
                "capabilities": capabilities,
                "degraded_reasons": list(package.degraded_reasons),
                "baseline_analysis_artifact_id": baseline_id,
                "analysis_artifact_id": baseline_id,
                "baseline_analysis_integrity_status": (
                    "deferred_until_content_or_workflow_request"
                    if baseline_id and defer_artifact_integrity
                    else "verified"
                    if baseline_id
                    else None
                ),
            }
        )
    return {"market": market, "company_key": company_key, "reports": rows}


def _legacy_artifact_id(market: str, company_key: str, artifact_type: str, filename: str) -> str:
    digest = hashlib.sha256(f"{market}\0{company_key}\0{artifact_type}\0{filename}".encode("utf-8")).hexdigest()[:32]
    return f"legacy_{digest}"


def _content_sha256(path: Path) -> str | None:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def _legacy_artifacts(package: ResolvedReportPackage, artifact_type: str) -> tuple[ResolvedAgentArtifact, ...]:
    if package.market != "CN":
        return ()
    output_dir = package.output_dir_for(artifact_type)
    if not output_dir.is_dir():
        return ()
    exact_artifacts = iter_exact_artifact_sidecars(package, artifact_type)
    bound_html = {html.resolve() for _artifact, _sidecar, html in exact_artifacts}
    bound_hashes = {
        str(artifact.content_hash or "").removeprefix("sha256:").lower()
        for artifact, _sidecar, _html in exact_artifacts
        if artifact.content_hash
    }
    rows: list[ResolvedAgentArtifact] = []
    for html_path in sorted(output_dir.glob("*.html")):
        if html_path.name == "latest.html" or html_path.resolve() in bound_html or html_path.is_symlink():
            continue
        try:
            resolved = html_path.resolve(strict=True)
            resolved.relative_to(output_dir.resolve())
        except (OSError, ValueError):
            continue
        if _content_sha256(resolved) in bound_hashes:
            continue
        created_at = datetime.fromtimestamp(resolved.stat().st_mtime, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        artifact = AgentArtifactV2.legacy_unbound(
            artifact_id=_legacy_artifact_id(package.market, package.company_key, artifact_type, resolved.name),
            artifact_type=artifact_type,
            html_file=resolved.name,
            created_at=created_at,
        )
        rows.append(
            ResolvedAgentArtifact(
                artifact=artifact,
                market=package.market,
                company_key=package.company_key,
                sidecar_path=None,
                html_path=resolved,
                legacy_unbound=True,
            )
        )
    rows.sort(key=lambda item: (item.artifact.created_at, item.artifact.artifact_id), reverse=True)
    return tuple(rows)


def _safe_artifact_lookup_id(artifact_id: str) -> str:
    value = str(artifact_id or "").strip()
    if not value or "/" in value or "\\" in value or ".." in value:
        raise ResearchUniverseError("artifact_not_found", "The requested artifact was not found.", 404)
    return value


def _legacy_artifact_page(
    package: ResolvedReportPackage,
    artifact_type: str,
    *,
    offset: int = 0,
    limit: int = 20,
    artifact_id: str | None = None,
    filename: str | None = None,
    verify_content: bool = True,
) -> _ArtifactPage:
    if package.market != "CN":
        return _ArtifactPage((), 0, False)
    output_dir = package.output_dir_for(artifact_type)
    if not output_dir.is_dir():
        return _ArtifactPage((), 0, False)

    bindings = exact_artifact_bindings(package, artifact_type)
    bound_html = {path.resolve() for path, _content_hash in bindings}
    bound_hashes = {content_hash for _path, content_hash in bindings if content_hash}

    requested_filename = str(filename or "").strip()
    if requested_filename:
        candidate_name = Path(requested_filename)
        if (
            candidate_name.is_absolute()
            or len(candidate_name.parts) != 1
            or "/" in requested_filename
            or "\\" in requested_filename
            or ".." in requested_filename
        ):
            raise ResearchUniverseError("artifact_not_found", "The requested artifact was not found.", 404)
        paths = (output_dir / requested_filename,)
    else:
        paths = tuple(output_dir.glob("*.html"))

    candidates: list[tuple[Path, str, str]] = []
    for html_path in paths:
        if html_path.name == "latest.html" or html_path.is_symlink():
            continue
        try:
            resolved = html_path.resolve(strict=True)
            resolved.relative_to(output_dir.resolve())
            stat = resolved.stat()
        except (OSError, ValueError):
            continue
        if resolved in bound_html:
            continue
        legacy_id = _legacy_artifact_id(package.market, package.company_key, artifact_type, resolved.name)
        if artifact_id is not None and legacy_id != artifact_id:
            continue
        created_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        candidates.append((resolved, legacy_id, created_at))
    candidates.sort(key=lambda item: (item[2], item[1]), reverse=True)

    start = 0 if artifact_id is not None or requested_filename else max(offset, 0)
    rows: list[ResolvedAgentArtifact] = []
    next_offset = min(start, len(candidates))
    for index, (resolved, legacy_id, created_at) in enumerate(candidates[start:], start=start):
        next_offset = index + 1
        if verify_content and _content_sha256(resolved) in bound_hashes:
            continue
        artifact = AgentArtifactV2.legacy_unbound(
            artifact_id=legacy_id,
            artifact_type=artifact_type,
            html_file=resolved.name,
            created_at=created_at,
        )
        rows.append(
            ResolvedAgentArtifact(
                artifact=artifact,
                market=package.market,
                company_key=package.company_key,
                sidecar_path=None,
                html_path=resolved,
                legacy_unbound=True,
            )
        )
        if len(rows) >= limit:
            break
    return _ArtifactPage(
        items=tuple(rows),
        next_offset=next_offset,
        has_more=artifact_id is None and not requested_filename and next_offset < len(candidates),
    )


def _parse_artifact_cursor(cursor: str | None) -> tuple[str, int]:
    value = str(cursor or "").strip()
    if not value:
        return "exact", 0
    phase, separator, raw_offset = value.partition(":")
    if separator != ":" or phase not in {"exact", "legacy"} or not raw_offset.isdigit():
        raise ResearchUniverseError("artifact_cursor_invalid", "The artifact cursor is invalid.", 400)
    offset = int(raw_offset)
    if offset > 1_000_000:
        raise ResearchUniverseError("artifact_cursor_invalid", "The artifact cursor is invalid.", 400)
    return phase, offset


def _resolved_exact_artifact(
    package: ResolvedReportPackage,
    artifact_type: str,
    artifact_id: str,
    *,
    verify_content: bool = True,
) -> ResolvedAgentArtifact | None:
    page = page_exact_artifact_sidecars(
        package,
        artifact_type,
        limit=1,
        artifact_id=_safe_artifact_lookup_id(artifact_id),
        verify_content=verify_content,
    )
    if not page.items:
        return None
    artifact, sidecar, html = page.items[0]
    return ResolvedAgentArtifact(
        artifact=artifact,
        market=package.market,
        company_key=package.company_key,
        sidecar_path=sidecar,
        html_path=html,
    )


def list_artifacts(
    *,
    market: str,
    company_key: str,
    report_id: str,
    artifact_type: str,
    agent_type: str | None = None,
    limit: int | None = None,
    cursor: str | None = None,
    requested_artifact_id: str | None = None,
    legacy_filename: str | None = None,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    artifact_type = normalize_artifact_type(artifact_type)
    effective_agent_type = normalize_agent_type(agent_type or artifact_type)
    normalized_market = normalize_market(market)
    if normalized_market not in enabled_markets(effective_agent_type):
        raise ResearchUniverseError("market_not_supported", "The market is not enabled for this agent.", 404)
    package = resolve_report_package(
        market=normalized_market,
        company_key=company_key,
        report_id=report_id,
        agent_type=effective_agent_type,
        wiki_root=wiki_root,
    )
    if limit is not None:
        if limit < 1 or limit > 50:
            raise ResearchUniverseError("artifact_limit_invalid", "Artifact page size must be between 1 and 50.", 400)
        requested_id = (
            _safe_artifact_lookup_id(requested_artifact_id)
            if requested_artifact_id
            else None
        )
        if requested_id or legacy_filename:
            resolved = (
                _resolved_exact_artifact(
                    package,
                    artifact_type,
                    requested_id,
                    verify_content=False,
                )
                if requested_id
                else None
            )
            if resolved is None and package.market == "CN":
                legacy_page = _legacy_artifact_page(
                    package,
                    artifact_type,
                    limit=1,
                    artifact_id=requested_id,
                    filename=legacy_filename,
                    verify_content=False,
                )
                resolved = legacy_page.items[0] if legacy_page.items else None
            items = (resolved,) if resolved is not None else ()
            return {
                "market": package.market,
                "company_key": package.company_key,
                "report_id": package.report_id,
                "artifact_type": artifact_type,
                "artifacts": [_lazy_artifact_api_dict(item) for item in items if not item.legacy_unbound],
                "legacy_artifacts": [_lazy_artifact_api_dict(item) for item in items if item.legacy_unbound],
                "items": [_lazy_artifact_api_dict(item) for item in items],
                "pagination": {
                    "limit": limit,
                    "next_cursor": None,
                    "has_more": False,
                    "targeted": True,
                },
            }

        phase, offset = _parse_artifact_cursor(cursor)
        rows: list[ResolvedAgentArtifact] = []
        next_cursor: str | None = None
        if phase == "exact":
            exact_page = page_exact_artifact_sidecars(
                package,
                artifact_type,
                offset=offset,
                limit=limit,
                verify_content=False,
            )
            rows.extend(
                ResolvedAgentArtifact(
                    artifact=artifact,
                    market=package.market,
                    company_key=package.company_key,
                    sidecar_path=sidecar,
                    html_path=html,
                )
                for artifact, sidecar, html in exact_page.items
            )
            if exact_page.has_more:
                next_cursor = f"exact:{exact_page.next_offset}"
            elif package.market == "CN" and len(rows) < limit:
                legacy_page = _legacy_artifact_page(
                    package,
                    artifact_type,
                    limit=limit - len(rows),
                    verify_content=False,
                )
                rows.extend(legacy_page.items)
                if legacy_page.has_more:
                    next_cursor = f"legacy:{legacy_page.next_offset}"
            elif package.market == "CN" and len(rows) == limit:
                next_cursor = "legacy:0"
        else:
            legacy_page = _legacy_artifact_page(
                package,
                artifact_type,
                offset=offset,
                limit=limit,
                verify_content=False,
            )
            rows.extend(legacy_page.items)
            if legacy_page.has_more:
                next_cursor = f"legacy:{legacy_page.next_offset}"

        return {
            "market": package.market,
            "company_key": package.company_key,
            "report_id": package.report_id,
            "artifact_type": artifact_type,
            "artifacts": [_lazy_artifact_api_dict(item) for item in rows if not item.legacy_unbound],
            "legacy_artifacts": [_lazy_artifact_api_dict(item) for item in rows if item.legacy_unbound],
            "items": [_lazy_artifact_api_dict(item) for item in rows],
            "pagination": {
                "limit": limit,
                "next_cursor": next_cursor,
                "has_more": next_cursor is not None,
                "targeted": False,
            },
        }

    exact = [
        ResolvedAgentArtifact(
            artifact=artifact,
            market=package.market,
            company_key=package.company_key,
            sidecar_path=sidecar,
            html_path=html,
        ).to_api_dict()
        for artifact, sidecar, html in iter_exact_artifact_sidecars(package, artifact_type)
    ]
    legacy = [item.to_api_dict() for item in _legacy_artifacts(package, artifact_type)]
    return {
        "market": package.market,
        "company_key": package.company_key,
        "report_id": package.report_id,
        "artifact_type": artifact_type,
        "artifacts": exact,
        "legacy_artifacts": legacy,
    }


def _all_packages(*, wiki_root: Path | str | None = None) -> tuple[ResolvedReportPackage, ...]:
    packages: list[ResolvedReportPackage] = []
    for company in enumerate_companies(wiki_root=wiki_root):
        packages.extend(enumerate_report_packages(company, agent_type="analysis", include_unready=False))
    return tuple(packages)


def resolve_artifact(
    artifact_id: str,
    *,
    expected_identity: Mapping[str, Any] | ResearchIdentity | None = None,
    artifact_type: str | None = None,
    market: str | None = None,
    company_key: str | None = None,
    report_id: str | None = None,
    wiki_root: Path | str | None = None,
) -> ResolvedAgentArtifact:
    artifact_id = _safe_artifact_lookup_id(artifact_id)
    normalized_type = normalize_artifact_type(artifact_type) if artifact_type else None
    matches: list[ResolvedAgentArtifact] = []
    location_scope = (market, company_key, report_id)
    if any(value is not None for value in location_scope):
        if (
            normalized_type is None
            or not all(value is not None and str(value).strip() for value in location_scope)
        ):
            raise ResearchUniverseError("artifact_scope_incomplete", "Artifact scope is incomplete.", 400)
        scoped_package = resolve_report_package(
            market=str(market),
            company_key=str(company_key),
            report_id=str(report_id),
            agent_type=str(normalized_type),
            wiki_root=wiki_root,
        )
        exact = _resolved_exact_artifact(scoped_package, str(normalized_type), artifact_id)
        if exact is not None:
            matches.append(exact)
        if scoped_package.market == "CN":
            matches.extend(
                _legacy_artifact_page(
                    scoped_package,
                    str(normalized_type),
                    limit=1,
                    artifact_id=artifact_id,
                ).items
            )
        packages: tuple[ResolvedReportPackage, ...] = ()
    else:
        packages = _all_packages(wiki_root=wiki_root)
    for package in packages:
        types = (normalized_type,) if normalized_type else ("analysis", "factcheck", "tracking")
        for current_type in types:
            for artifact, sidecar, html in iter_exact_artifact_sidecars(package, current_type):
                if artifact.artifact_id == artifact_id:
                    matches.append(
                        ResolvedAgentArtifact(
                            artifact=artifact,
                            market=package.market,
                            company_key=package.company_key,
                            sidecar_path=sidecar,
                            html_path=html,
                        )
                    )
            for legacy in _legacy_artifacts(package, current_type):
                if legacy.artifact.artifact_id == artifact_id:
                    matches.append(legacy)
    unique_matches = {
        (item.artifact.artifact_id, item.html_path.resolve()): item
        for item in matches
    }
    matches = list(unique_matches.values())
    if len(matches) != 1:
        raise ResearchUniverseError("artifact_not_found", "The requested artifact was not found.", 404)
    resolved = matches[0]
    if resolved.market not in enabled_markets(resolved.artifact.artifact_type):
        raise ResearchUniverseError("artifact_not_found", "The requested artifact was not found.", 404)
    if expected_identity is not None:
        if resolved.artifact.research_target is None:
            raise ResearchUniverseError(
                "artifact_identity_mismatch",
                "A legacy unbound artifact cannot satisfy an exact identity lookup.",
                409,
            )
        raw = expected_identity.to_dict() if isinstance(expected_identity, ResearchIdentity) else dict(expected_identity)
        try:
            expected = ResearchIdentity.from_dict(raw)
        except Exception as exc:
            raise ResearchUniverseError(
                "research_identity_incomplete",
                "The expected artifact identity is incomplete.",
                409,
            ) from exc
        if not resolved.artifact.research_target.research_identity.matches(expected):
            raise ResearchUniverseError(
                "artifact_identity_mismatch",
                "The artifact does not match the requested ResearchIdentity.",
                409,
            )
    return resolved


def delete_artifact(
    artifact_id: str,
    *,
    market: str | None = None,
    company_key: str | None = None,
    report_id: str | None = None,
    artifact_type: str | None = None,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    resolved = resolve_artifact(
        artifact_id,
        market=market,
        company_key=company_key,
        report_id=report_id,
        artifact_type=artifact_type,
        wiki_root=wiki_root,
    )
    return delete_resolved_artifact(resolved)


def delete_resolved_artifact(resolved: ResolvedAgentArtifact) -> dict[str, Any]:
    # All paths come from a validated sidecar and remain inside one derived
    # output directory. Remove aliases/companions first so deleting a v2
    # artifact cannot expose its generator output as a legacy report.
    output_dir = resolved.html_path.parent.resolve()
    metadata = dict(resolved.artifact.metadata or {})
    relative_names: list[str] = []
    for key in ("markdown_file", "json_file"):
        value = metadata.get(key)
        if isinstance(value, str) and value:
            relative_names.append(value)
    aliases = metadata.get("legacy_aliases")
    if isinstance(aliases, list):
        relative_names.extend(str(item) for item in aliases if str(item).strip())
    companions = metadata.get("companion_files")
    if isinstance(companions, list):
        relative_names.extend(str(item) for item in companions if str(item).strip())
    elif isinstance(companions, Mapping):
        for item in companions.values():
            if isinstance(item, Mapping):
                item = item.get("file")
            if isinstance(item, str) and item:
                relative_names.append(item)
    expected_hash = str(resolved.artifact.content_hash or "").removeprefix("sha256:").lower()
    if expected_hash and not resolved.legacy_unbound:
        for candidate in output_dir.glob("*.html"):
            if candidate == resolved.html_path or candidate.is_symlink() or candidate.name == "latest.html":
                continue
            if _content_sha256(candidate) == expected_hash:
                relative_names.append(candidate.name)

    if resolved.sidecar_path is not None:
        resolved.sidecar_path.unlink()
    for name in dict.fromkeys(relative_names):
        relative = Path(name)
        if (
            relative.is_absolute()
            or not relative.parts
            or any(part in {"", ".", ".."} for part in relative.parts)
            or relative.name.endswith(".artifact.json")
        ):
            continue
        candidate = output_dir / relative
        try:
            candidate.parent.resolve().relative_to(output_dir)
        except (OSError, ValueError):
            continue
        if candidate.is_symlink():
            try:
                if candidate.resolve(strict=True) != resolved.html_path:
                    continue
            except OSError:
                pass
            candidate.unlink(missing_ok=True)
            continue
        try:
            safe_candidate = candidate.resolve(strict=True)
            safe_candidate.relative_to(output_dir)
        except (OSError, ValueError):
            continue
        if safe_candidate not in {resolved.html_path, resolved.sidecar_path}:
            safe_candidate.unlink(missing_ok=True)
    resolved.html_path.unlink(missing_ok=True)
    return {"deleted": True, "artifact_id": resolved.artifact.artifact_id}


__all__ = [
    "ResolvedAgentArtifact",
    "delete_artifact",
    "delete_resolved_artifact",
    "enabled_markets",
    "list_artifacts",
    "list_companies",
    "list_markets",
    "list_reports",
    "multi_market_research_enabled",
    "resolve_artifact",
]
