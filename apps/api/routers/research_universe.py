"""Authenticated Research Universe API."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from services.auth_dependencies import get_current_user
from services.auth_service import User
from services.observability import (
    emit_research_event,
    record_research_readiness,
    record_research_validation_failure,
)
from services.permissions import require_admin, require_user_permission
from services.research_universe import (
    delete_resolved_artifact,
    list_artifacts,
    list_companies,
    list_markets,
    list_reports,
    resolve_artifact,
)
from services.research_universe_contracts import ResearchUniverseError

router = APIRouter(prefix="/research-universe", tags=["research-universe"])
logger = logging.getLogger("siq.api.research_universe")


def _raise_http(
    exc: ResearchUniverseError,
    *,
    agent_type: str = "research-universe",
    market: str = "",
    company_key: str = "",
    research_identity=None,
) -> None:
    if "identity" in exc.code:
        record_research_validation_failure(
            market=market,
            agent_type=agent_type,
            failure="identity_mismatch",
        )
    emit_research_event(
        logger,
        "research_universe_request_rejected",
        agent_type=agent_type,
        market=market,
        company_key=company_key,
        research_identity=research_identity,
        error_code=exc.code,
        status="failed",
    )
    raise HTTPException(status_code=exc.status_code, detail=exc.detail()) from exc


def _permission(user: User, permission: str) -> None:
    try:
        require_user_permission(user, permission)
    except HTTPException as exc:
        _raise_http(ResearchUniverseError("permission_denied", f"Permission required: {permission}", 403))


@router.get("/markets")
def get_markets(
    agent_type: str = Query("analysis"),
    current_user: User = Depends(get_current_user),
):
    _permission(current_user, "company.view")
    try:
        payload = list_markets(agent_type=agent_type)
    except ResearchUniverseError as exc:
        _raise_http(exc, agent_type=agent_type)
    for row in payload["markets"]:
        readiness = (
            "degraded"
            if row.get("degraded_reasons")
            else "ready"
            if row.get("company_count")
            else "unavailable"
        )
        record_research_readiness(
            market=row.get("market"),
            agent_type=agent_type,
            status=readiness,
        )
    emit_research_event(
        logger,
        "research_universe_markets_listed",
        agent_type=agent_type,
        status="completed",
    )
    return payload


@router.get("/companies")
def get_companies(
    market: str = Query(...),
    agent_type: str = Query("analysis"),
    q: str = Query("", max_length=120),
    include_unready: bool = Query(False),
    current_user: User = Depends(get_current_user),
):
    _permission(current_user, "company.view")
    if include_unready:
        try:
            require_admin(current_user)
        except HTTPException:
            _raise_http(ResearchUniverseError("permission_denied", "Administrator permission required.", 403))
    try:
        payload = list_companies(
            market=market,
            agent_type=agent_type,
            q=q,
            include_unready=include_unready,
        )
    except ResearchUniverseError as exc:
        _raise_http(exc, agent_type=agent_type, market=market)
    emit_research_event(
        logger,
        "research_universe_companies_listed",
        agent_type=agent_type,
        market=payload["market"],
        status="completed",
    )
    return payload


@router.get("/companies/{company_key}/reports")
def get_reports(
    company_key: str,
    market: str = Query(...),
    agent_type: str = Query("analysis"),
    include_unready: bool = Query(False),
    defer_artifact_integrity: bool = Query(False),
    current_user: User = Depends(get_current_user),
):
    _permission(current_user, "report.view")
    if include_unready:
        try:
            require_admin(current_user)
        except HTTPException:
            _raise_http(ResearchUniverseError("permission_denied", "Administrator permission required.", 403))
    try:
        payload = list_reports(
            market=market,
            company_key=company_key,
            agent_type=agent_type,
            include_unready=include_unready,
            defer_artifact_integrity=defer_artifact_integrity,
        )
    except ResearchUniverseError as exc:
        _raise_http(exc, agent_type=agent_type, market=market, company_key=company_key)
    emit_research_event(
        logger,
        "research_universe_reports_listed",
        agent_type=agent_type,
        market=payload["market"],
        company_key=company_key,
        status="completed",
    )
    return payload


@router.get("/companies/{company_key}/artifacts")
def get_artifacts(
    company_key: str,
    market: str = Query(...),
    artifact_type: str = Query(...),
    report_id: str = Query(...),
    agent_type: str | None = Query(None),
    limit: int | None = Query(None, ge=1, le=50),
    cursor: str | None = Query(None, max_length=64),
    requested_artifact_id: str | None = Query(None, max_length=240),
    legacy_filename: str | None = Query(None, max_length=512),
    current_user: User = Depends(get_current_user),
):
    _permission(current_user, "report.view")
    try:
        payload = list_artifacts(
            market=market,
            company_key=company_key,
            report_id=report_id,
            artifact_type=artifact_type,
            agent_type=agent_type,
            limit=limit,
            cursor=cursor,
            requested_artifact_id=requested_artifact_id,
            legacy_filename=legacy_filename,
        )
    except ResearchUniverseError as exc:
        _raise_http(
            exc,
            agent_type=agent_type or artifact_type,
            market=market,
            company_key=company_key,
        )
    emit_research_event(
        logger,
        "research_universe_artifacts_listed",
        agent_type=agent_type or artifact_type,
        market=payload["market"],
        company_key=company_key,
        artifact_type=payload["artifact_type"],
        status="completed",
    )
    return payload


@router.get("/artifacts/{artifact_id}/content")
def get_artifact_content(
    artifact_id: str,
    market: str | None = Query(None),
    company_key: str | None = Query(None, max_length=240),
    report_id: str | None = Query(None, max_length=512),
    artifact_type: str | None = Query(None),
    current_user: User = Depends(get_current_user),
):
    _permission(current_user, "report.view")
    try:
        resolved = resolve_artifact(
            artifact_id,
            market=market,
            company_key=company_key,
            report_id=report_id,
            artifact_type=artifact_type,
        )
    except ResearchUniverseError as exc:
        _raise_http(exc)
    emit_research_event(
        logger,
        "research_universe_artifact_viewed",
        agent_type=resolved.artifact.artifact_type,
        artifact_id=artifact_id,
        market=resolved.market,
        company_key=resolved.company_key,
        research_identity=(
            resolved.artifact.research_target.research_identity.to_dict()
            if resolved.artifact.research_target
            else None
        ),
        source_family=resolved.artifact.source_family,
        adapter_version=resolved.artifact.adapter_version,
        status="completed",
    )
    return FileResponse(
        resolved.html_path,
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )


@router.delete("/artifacts/{artifact_id}")
def remove_artifact(
    artifact_id: str,
    market: str | None = Query(None),
    company_key: str | None = Query(None, max_length=240),
    report_id: str | None = Query(None, max_length=512),
    artifact_type: str | None = Query(None),
    current_user: User = Depends(get_current_user),
):
    _permission(current_user, "report.delete")
    try:
        resolved = resolve_artifact(
            artifact_id,
            market=market,
            company_key=company_key,
            report_id=report_id,
            artifact_type=artifact_type,
        )
        payload = delete_resolved_artifact(resolved)
    except ResearchUniverseError as exc:
        _raise_http(exc)
    emit_research_event(
        logger,
        "research_universe_artifact_deleted",
        agent_type=resolved.artifact.artifact_type,
        artifact_id=artifact_id,
        market=resolved.market,
        company_key=resolved.company_key,
        research_identity=(
            resolved.artifact.research_target.research_identity.to_dict()
            if resolved.artifact.research_target
            else None
        ),
        source_family=resolved.artifact.source_family,
        adapter_version=resolved.artifact.adapter_version,
        status="completed",
    )
    return payload


__all__ = ["router"]
