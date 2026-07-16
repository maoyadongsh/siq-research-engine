"""Snapshot-bound project Evidence and private-Milvus receipts for IC agents."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from services import deal_retrieval, deal_store, ic_policy
from services.path_config import PROJECT_ROOT

STARTUP_RECEIPTS_V1_SCHEMA = "siq_ic_startup_receipts_v1"
STARTUP_RECEIPTS_SCHEMA = "siq_ic_startup_receipts_v2"
STARTUP_RECEIPT_SCHEMA = "siq_ic_startup_receipt_v2"
STARTUP_RETRIEVAL_MODE = "local_evidence_package_v1"
DEFAULT_EVIDENCE_LIMIT = 10
MAX_EVIDENCE_LIMIT = 50
PROFILE_DIMENSIONS: dict[str, tuple[str, ...]] = {
    "siq_ic_master_coordinator": ("business", "finance", "legal", "risk", "unknown"),
    "siq_ic_strategist": ("business",),
    "siq_ic_sector_expert": ("business",),
    "siq_ic_finance_auditor": ("finance",),
    "siq_ic_legal_scanner": ("legal",),
    "siq_ic_risk_controller": ("risk",),
    "siq_ic_chairman": ("business", "finance", "legal", "risk", "unknown"),
}
LEGACY_BY_PROFILE = {value: key for key, value in ic_policy.LEGACY_PROFILE_IDS.items()}
PROFILE_RULE_FILES = ("SOUL.md", "AGENTS.md", "IDENTITY.md", "TOOLS.md")


def _require_package_dir(deal_id: str, *, wiki_root: Path | str | None = None) -> Path:
    package_dir = deal_store.safe_deal_dir(deal_id, wiki_root=wiki_root)
    if not (package_dir / "manifest.json").is_file():
        raise FileNotFoundError(deal_id)
    return package_dir


def _normalize_profile_id(profile_id: str) -> str:
    canonical = ic_policy.canonical_ic_profile_id(profile_id)
    if canonical not in ic_policy.IC_PROFILE_IDS:
        raise ValueError(f"Unknown IC profile: {profile_id}")
    return canonical


def _normalize_round_name(round_name: str | None) -> str:
    value = str(round_name or "R1").strip().upper()
    if value not in {"R0", "R1", "R1.5", "R2", "R3", "R4"}:
        raise ValueError("round_name must be R0, R1, R1.5, R2, R3, or R4")
    return value


def _phase_for(profile_id: str, round_name: str) -> str:
    if round_name == "R1":
        return "R1B" if profile_id in {"siq_ic_risk_controller", "siq_ic_chairman"} else "R1A"
    return round_name


def _normalize_limit(value: int | str | None) -> int:
    try:
        parsed = int(value) if value is not None else DEFAULT_EVIDENCE_LIMIT
    except (TypeError, ValueError):
        parsed = DEFAULT_EVIDENCE_LIMIT
    return max(1, min(parsed, MAX_EVIDENCE_LIMIT))


def _read_evidence_items(path: Path) -> tuple[list[dict[str, Any]], int]:
    items: list[dict[str, Any]] = []
    invalid = 0
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return items, invalid
    for line in lines:
        raw = line.strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            invalid += 1
            continue
        if isinstance(payload, dict):
            items.append(payload)
        else:
            invalid += 1
    return items, invalid


def _project_query(package_dir: Path, deal_id: str, explicit_query: str | None) -> str:
    query = str(explicit_query or "").strip()
    if query:
        return query[:300]
    project_meta = deal_store.read_json(package_dir / "project_meta.json", {}) or {}
    workflow = deal_store.read_json(package_dir / "phases" / "workflow_state.json", {}) or {}
    parts = [
        project_meta.get("company_name") or workflow.get("company_name"),
        project_meta.get("industry") or workflow.get("industry"),
        project_meta.get("stage") or workflow.get("stage"),
        deal_id,
    ]
    return " ".join(str(part).strip() for part in parts if str(part or "").strip())[:300]


def _workspace_rules_read(profile_id: str) -> list[str]:
    profile_dir = PROJECT_ROOT / "agents" / "hermes" / "profiles" / profile_id
    return [name for name in PROFILE_RULE_FILES if (profile_dir / name).is_file()]


def _quality_missing_dimensions(package_dir: Path) -> list[str]:
    quality = deal_store.read_json(package_dir / "evidence" / "evidence_quality_report.json", {}) or {}
    missing = quality.get("missing_dimensions") if isinstance(quality, dict) else []
    return [str(item) for item in missing if str(item or "").strip()] if isinstance(missing, list) else []


def _primary_market_source_context(package_dir: Path) -> dict[str, Any]:
    snapshot = deal_store.read_json(package_dir / "evidence" / "evidence_snapshot.json", {}) or {}
    active_sources = snapshot.get("active_sources") if isinstance(snapshot.get("active_sources"), list) else []
    source_ids: list[str] = []
    restrictions: dict[str, list[str]] = {}
    identities: list[dict[str, Any]] = []
    for source in active_sources:
        if not isinstance(source, dict) or not source.get("source_id"):
            continue
        source_id = str(source["source_id"])
        source_ids.append(source_id)
        capabilities = source.get("capabilities") if isinstance(source.get("capabilities"), dict) else {}
        restricted = sorted(
            key for key, value in capabilities.items() if str(value or "") != "ready"
        )
        if restricted:
            restrictions[source_id] = restricted
        document_id = str(source.get("document_id") or "")
        parse_run_id = str(source.get("parse_run_id") or "")
        identities.append({
            "domain": "primary_market",
            "market": "CN",
            "company_id": f"PRIMARY:{snapshot.get('deal_id')}",
            "filing_id": f"PROSPECTUS:{document_id}",
            "document_id": document_id,
            "parse_run_id": parse_run_id,
            "source_id": source_id,
        })
    return {
        "source_ids": sorted(source_ids),
        "evidence_snapshot_hash": snapshot.get("snapshot_hash"),
        "capability_restrictions": restrictions,
        "research_identities": identities,
    }


def current_evidence_identity(
    deal_id: str,
    *,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    """Return the active, immutable evidence identity used by IC phase tasks."""

    package_dir = _require_package_dir(deal_id, wiki_root=wiki_root)
    return _primary_market_source_context(package_dir)


def evaluate_startup_receipt_freshness(
    receipt: dict[str, Any] | None,
    current_identity: dict[str, Any],
) -> dict[str, Any]:
    """Compare one receipt with the active source set without mutating it."""

    receipt_payload = receipt if isinstance(receipt, dict) else {}
    current_hash = str(current_identity.get("evidence_snapshot_hash") or "")
    receipt_hash = str(receipt_payload.get("evidence_snapshot_hash") or "")
    current_sources = sorted(str(item) for item in current_identity.get("source_ids") or [] if str(item or ""))
    receipt_sources = sorted(str(item) for item in receipt_payload.get("source_ids") or [] if str(item or ""))
    reasons: list[str] = []
    if not receipt_payload:
        reasons.append("startup_receipt_missing")
    if (current_hash or receipt_hash) and receipt_hash != current_hash:
        reasons.append("evidence_snapshot_changed")
    if (current_sources or receipt_sources) and receipt_sources != current_sources:
        reasons.append("active_source_set_changed")
    if (
        receipt_payload.get("readiness_status") == "stale"
        or receipt_payload.get("stale_reason")
    ):
        reasons.append(str(receipt_payload.get("stale_reason") or "receipt_marked_stale"))
    reasons = list(dict.fromkeys(reasons))
    return {
        "status": "stale" if reasons else "current",
        "stale": bool(reasons),
        "reasons": reasons,
        "receipt_evidence_snapshot_hash": receipt_hash or None,
        "current_evidence_snapshot_hash": current_hash or None,
        "receipt_source_ids": receipt_sources,
        "current_source_ids": current_sources,
    }


def _matches_profile(item: dict[str, Any], profile_id: str, dimensions: tuple[str, ...]) -> bool:
    role_hints = item.get("role_hints") if isinstance(item.get("role_hints"), list) else []
    if profile_id in role_hints:
        return True
    dimension = str(item.get("dimension") or "").strip()
    return dimension in dimensions


def _evidence_hit(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "evidence_id": item.get("evidence_id"),
        "document_id": item.get("document_id"),
        "dimension": item.get("dimension"),
        "evidence_type": item.get("evidence_type"),
        "citation": item.get("citation"),
        "locator": item.get("locator"),
        "source_path": item.get("source_path"),
        "source_url": item.get("source_url"),
        "artifact_url": item.get("artifact_url"),
        "quote_preview": str(item.get("quote") or item.get("claim") or "")[:300],
    }


def _receipt_id(profile_id: str, round_name: str) -> str:
    return f"startup-{profile_id}-{round_name}-001"


def _background_reference(item: dict[str, Any], *, profile_id: str, physical_collection: str) -> dict[str, Any]:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    usage = (
        "methodology"
        if item.get("knowledge_lane") == "methodology"
        or metadata.get("knowledge_type") == "methodology"
        else "background"
    )
    locator = str(item.get("source_id") or item.get("id") or item.get("title") or "").strip()
    stable = json.dumps(
        {
            "profile_id": profile_id,
            "collection": item.get("collection"),
            "locator": locator,
            "text": item.get("text") or item.get("quote_preview"),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    ref_id = "KBREF-" + hashlib.sha256(stable.encode("utf-8")).hexdigest()[:24].upper()
    return {
        "ref_id": ref_id,
        "source_class": "background_knowledge",
        "collection": str(item.get("collection") or profile_id),
        "physical_collection": physical_collection,
        "locator": locator or ref_id,
        "title": str(item.get("title") or item.get("source_id") or profile_id)[:500],
        "usage": usage,
        "quote_preview": str(item.get("quote_preview") or item.get("text") or "")[:500],
    }


def _shared_vector_hit_is_current(
    item: Any,
    *,
    deal_id: str,
    snapshot_hash: str,
) -> bool:
    if not isinstance(item, dict):
        return False
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    return bool(
        snapshot_hash
        and str(item.get("project_tag") or metadata.get("project_tag") or "") == deal_id
        and metadata.get("domain") == "primary_market"
        and metadata.get("source_class") == "project_evidence"
        and metadata.get("project_fact") is True
        and metadata.get("deal_id") == deal_id
        and metadata.get("snapshot_hash") == snapshot_hash
    )


def _read_receipts(path: Path, deal_id: str) -> dict[str, Any]:
    payload = deal_store.read_json(path, None)
    if not isinstance(payload, dict):
        payload = {}
    raw_agents = payload.get("agents") if isinstance(payload.get("agents"), dict) else {}
    agents: dict[str, dict[str, Any]] = {}
    for raw_agent_id, raw_receipt in raw_agents.items():
        if not isinstance(raw_receipt, dict):
            continue
        raw_key = str(raw_agent_id)
        canonical_key = ic_policy.canonical_ic_profile_id(raw_key)
        key = canonical_key if canonical_key in ic_policy.IC_PROFILE_IDS else raw_key
        agents.setdefault(key, dict(raw_receipt))
    by_agent_phase = (
        payload.get("by_agent_phase")
        if isinstance(payload.get("by_agent_phase"), dict)
        else {}
    )
    normalized_history: dict[str, dict[str, Any]] = {}
    for raw_agent_id, rounds in by_agent_phase.items():
        if not isinstance(rounds, dict):
            continue
        raw_key = str(raw_agent_id)
        canonical_key = ic_policy.canonical_ic_profile_id(raw_key)
        key = canonical_key if canonical_key in ic_policy.IC_PROFILE_IDS else raw_key
        target = normalized_history.setdefault(key, {})
        for round_name, receipt in rounds.items():
            if isinstance(receipt, dict):
                target.setdefault(str(round_name).upper(), dict(receipt))
    for agent_id, receipt in agents.items():
        if not isinstance(receipt, dict):
            continue
        round_name = str(receipt.get("round_name") or "R1").upper()
        normalized_history.setdefault(str(agent_id), {}).setdefault(round_name, dict(receipt))
    return {
        "schema_version": STARTUP_RECEIPTS_SCHEMA,
        "deal_id": deal_id,
        "agents": agents,
        "by_agent_phase": normalized_history,
        "updated_at": payload.get("updated_at"),
    }


def generate_startup_retrieval_receipt(
    deal_id: str,
    profile_id: str,
    *,
    round_name: str | None = "R1",
    query: str | None = None,
    limit: int | str | None = DEFAULT_EVIDENCE_LIMIT,
    include_external: bool = False,
    external_providers: list[str] | tuple[str, ...] | None = None,
    include_vector: bool = True,
    include_rerank: bool = True,
    vector_collections: list[str] | tuple[str, ...] | None = None,
    created_by: dict[str, Any] | None = None,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    package_dir = _require_package_dir(deal_id, wiki_root=wiki_root)
    normalized_deal_id = deal_store.validate_deal_id(deal_id)
    canonical_profile = _normalize_profile_id(profile_id)
    normalized_round = _normalize_round_name(round_name)
    normalized_limit = _normalize_limit(limit)
    created_at = deal_store.utc_now_iso()
    source_context = _primary_market_source_context(package_dir)

    retrieval = deal_retrieval.retrieve_for_agent(
        normalized_deal_id,
        canonical_profile,
        query=query,
        limit=normalized_limit,
        include_external=include_external,
        external_providers=external_providers,
        include_vector=include_vector,
        include_rerank=include_rerank,
        vector_collections=vector_collections,
        wiki_root=wiki_root,
    )
    dimensions = tuple(retrieval.get("dimensions") or PROFILE_DIMENSIONS.get(canonical_profile, ()))
    evidence_hits = retrieval.get("evidence_hits") if isinstance(retrieval.get("evidence_hits"), list) else []
    hybrid_hits = retrieval.get("hybrid_hits") if isinstance(retrieval.get("hybrid_hits"), list) else []
    background_hits = (
        retrieval.get("background_knowledge_hits")
        if isinstance(retrieval.get("background_knowledge_hits"), list)
        else []
    )
    methodology_hits = (
        retrieval.get("methodology_hits")
        if isinstance(retrieval.get("methodology_hits"), list)
        else []
    )
    domain_background_hits = (
        retrieval.get("domain_background_hits")
        if isinstance(retrieval.get("domain_background_hits"), list)
        else []
    )
    shared_vector_hits = (
        retrieval.get("shared_vector_hits")
        if isinstance(retrieval.get("shared_vector_hits"), list)
        else []
    )
    vector_retrieval = retrieval.get("vector_retrieval") if isinstance(retrieval.get("vector_retrieval"), dict) else {}
    rerank = retrieval.get("rerank") if isinstance(retrieval.get("rerank"), dict) else {}
    expected_collections = ["siq_deal_shared", canonical_profile]
    actual_collections = [str(item) for item in vector_retrieval.get("collections") or []]
    physical_collections = (
        vector_retrieval.get("physical_collections")
        if isinstance(vector_retrieval.get("physical_collections"), dict)
        else {}
    )
    expected_private_physical = canonical_profile.removeprefix("siq_")
    vector_completed = (
        bool(retrieval.get("milvus_used"))
        and bool(vector_retrieval.get("milvus_used", retrieval.get("milvus_used")))
        and vector_retrieval.get("status") == "completed"
    )
    private_collection_connected = (
        vector_completed
        and canonical_profile in actual_collections
        and physical_collections.get(canonical_profile) == expected_private_physical
    )
    shared_collection_connected = (
        vector_completed
        and "siq_deal_shared" in actual_collections
        and physical_collections.get("siq_deal_shared") == "ic_collaboration_shared"
        and vector_retrieval.get("shared_filter_applied") is True
        and vector_retrieval.get("shared_project_tag") == normalized_deal_id
    )
    private_retrieval_ready = (
        private_collection_connected
        and bool(methodology_hits)
    )
    shared_retrieval_ready = (
        shared_collection_connected
        and bool(shared_vector_hits)
        and all(
            _shared_vector_hit_is_current(
                item,
                deal_id=normalized_deal_id,
                snapshot_hash=str(source_context.get("evidence_snapshot_hash") or ""),
            )
            for item in shared_vector_hits
        )
    )
    rerank_ready = (
        not include_rerank
        or rerank.get("status") == "completed"
        or (rerank.get("status") == "skipped" and rerank.get("reason") == "no_candidates")
    )
    retrieval_ready = private_retrieval_ready and shared_retrieval_ready and rerank_ready
    collections_connected = private_collection_connected and shared_collection_connected
    chat_retrieval_ready = collections_connected and rerank_ready
    if collections_connected:
        connection_status = "connected"
    elif vector_retrieval.get("status") in {"skipped", "disabled"}:
        connection_status = "not_checked"
    elif private_collection_connected or shared_collection_connected:
        connection_status = "partial"
    else:
        connection_status = "failed"
    connection_errors: list[str] = []
    if not shared_collection_connected:
        connection_errors.append("shared_collection_connection_failed")
    if not private_collection_connected:
        connection_errors.append("private_collection_connection_failed")

    gaps = [str(item) for item in retrieval.get("gaps", []) if str(item or "").strip()]
    missing_dimensions = _quality_missing_dimensions(package_dir)
    role_missing = sorted(set(dimensions).intersection(missing_dimensions))
    for dimension in role_missing:
        gaps.append(f"missing_{dimension}_evidence")
    if canonical_profile == "siq_ic_finance_auditor" and any(
        "financial_facts" in restrictions
        for restrictions in source_context["capability_restrictions"].values()
    ):
        gaps.append("primary_market_financial_facts_restricted")
    if methodology_hits and not domain_background_hits:
        gaps.append("private_domain_corpus_empty")
    if not private_collection_connected:
        gaps.append("private_kb_unavailable")
    if not shared_collection_connected:
        gaps.append("deal_scoped_shared_kb_unavailable")
    elif not shared_retrieval_ready:
        gaps.append("deal_scoped_shared_kb_empty")
    if not rerank_ready:
        gaps.append("reranker_unavailable")
    content_warnings: list[str] = []
    if shared_collection_connected and not shared_vector_hits:
        content_warnings.append("deal_scoped_shared_kb_empty")
    if shared_collection_connected and not source_context.get("evidence_snapshot_hash"):
        content_warnings.append("evidence_snapshot_unavailable")
    if private_collection_connected and not methodology_hits:
        content_warnings.append("private_methodology_missing")

    retrieval_blocking_reasons: list[str] = []
    if not private_retrieval_ready:
        if vector_retrieval.get("status") == "completed" and not methodology_hits:
            retrieval_blocking_reasons.append("private_methodology_missing")
        else:
            retrieval_blocking_reasons.append(
                str(vector_retrieval.get("reason") or "background_knowledge_retrieval_incomplete")
            )
    if not shared_retrieval_ready:
        if vector_retrieval.get("status") != "completed":
            shared_reason = str(vector_retrieval.get("reason") or "shared_knowledge_retrieval_incomplete")
        elif vector_retrieval.get("shared_filter_applied") is not True:
            shared_reason = "deal_scoped_shared_filter_missing"
        elif vector_retrieval.get("shared_project_tag") != normalized_deal_id:
            shared_reason = "deal_scoped_shared_project_tag_mismatch"
        else:
            shared_reason = "deal_scoped_shared_kb_empty"
        if shared_reason not in retrieval_blocking_reasons:
            retrieval_blocking_reasons.append(shared_reason)
    if not rerank_ready:
        rerank_reason = str(rerank.get("reason") or rerank.get("status") or "rerank_incomplete")
        retrieval_blocking_reasons.append(rerank_reason)
    physical_collection = str(
        (vector_retrieval.get("physical_collections") or {}).get(canonical_profile)
        or canonical_profile
    )
    background_refs = [
        _background_reference(
            item,
            profile_id=canonical_profile,
            physical_collection=physical_collection,
        )
        for item in background_hits
        if isinstance(item, dict)
    ]
    methodology_refs = [
        item for item in background_refs if item.get("usage") == "methodology"
    ]

    receipt = {
        "schema_version": STARTUP_RECEIPT_SCHEMA,
        "receipt_id": _receipt_id(canonical_profile, normalized_round),
        "deal_id": normalized_deal_id,
        "agent_id": canonical_profile,
        "legacy_agent_id": LEGACY_BY_PROFILE.get(canonical_profile),
        "phase": _phase_for(canonical_profile, normalized_round),
        "round_name": normalized_round,
        "query": _project_query(package_dir, normalized_deal_id, query),
        "queries": [
            item
            for item in [
                _project_query(package_dir, normalized_deal_id, query),
                *[str(value) for value in retrieval.get("dynamic_queries") or []],
            ]
            if item
        ],
        "project_tag": normalized_deal_id,
        "retrieval_mode": STARTUP_RETRIEVAL_MODE,
        "retrieval_contract": retrieval,
        "dynamic_queries": retrieval.get("dynamic_queries") or [],
        "shared_hits": int(retrieval.get("matched_evidence_count") or 0),
        "shared_vector_hits": shared_vector_hits,
        "shared_vector_hit_count": len(shared_vector_hits),
        "local_evidence_hit_count": len(evidence_hits),
        "private_selected_hit_count": len(background_hits),
        "private_hits": len(background_hits),
        "project_evidence_hits": evidence_hits,
        "background_knowledge_hits": background_hits,
        "background_knowledge_hit_count": len(background_hits),
        "background_knowledge_refs": background_refs,
        "methodology_refs": methodology_refs,
        "methodology_hit_count": len(methodology_hits),
        "domain_background_hit_count": len(domain_background_hits),
        "background_selection": retrieval.get("background_selection") or {},
        "retrieval_collections": expected_collections,
        "physical_collections": physical_collections,
        "shared_collection": "siq_deal_shared",
        "private_collection": canonical_profile,
        "private_connected": private_collection_connected,
        "shared_connected": shared_collection_connected,
        "collections_connected": collections_connected,
        "dual_kb_connected": collections_connected,
        "connection_status": connection_status,
        "connection_errors": connection_errors,
        "connection_checked_at": created_at,
        "chat_retrieval_ready": chat_retrieval_ready,
        "content_warnings": content_warnings,
        "private_ready": private_retrieval_ready,
        "shared_ready": shared_retrieval_ready,
        "rerank_ready": rerank_ready,
        "retrieval_status": "ready" if retrieval_ready else "blocked",
        "chat_retrieval_status": "ready" if chat_retrieval_ready else "blocked",
        "collection_connections": {
            "shared": {
                "logical_collection": "siq_deal_shared",
                "physical_collection": physical_collections.get("siq_deal_shared"),
                "connected": shared_collection_connected,
                "filter_applied": vector_retrieval.get("shared_filter_applied") is True,
                "project_tag": vector_retrieval.get("shared_project_tag"),
                "hit_count": len(shared_vector_hits),
            },
            "private": {
                "logical_collection": canonical_profile,
                "physical_collection": physical_collections.get(canonical_profile),
                "connected": private_collection_connected,
                "hit_count": len(background_hits),
            },
        },
        "degraded_reasons": retrieval_blocking_reasons,
        "evidence_hits": evidence_hits,
        "evidence_hit_count": len(evidence_hits),
        "hybrid_hits": hybrid_hits,
        "hybrid_hit_count": len(hybrid_hits),
        "dimensions": list(dimensions),
        "workspace_rules_read": _workspace_rules_read(canonical_profile),
        "gaps": gaps,
        "vector_retrieval": vector_retrieval,
        "retrieval_strategy": vector_retrieval.get("retrieval_strategy") or {},
        "collection_candidate_counts": vector_retrieval.get("collection_candidate_counts") or {},
        "collection_hit_counts": vector_retrieval.get("collection_hit_counts") or {},
        "retrieval_observability": retrieval.get("retrieval_observability") or {},
        "rerank": rerank,
        "external_research": retrieval.get("external_research") or {},
        "milvus_used": bool(retrieval.get("milvus_used")),
        "postgres_used": False,
        "reranker_used": bool(retrieval.get("reranker_used")),
        "hermes_used": False,
        "source_ids": source_context["source_ids"],
        "evidence_snapshot_hash": source_context["evidence_snapshot_hash"],
        "capability_restrictions": source_context["capability_restrictions"],
        "research_identities": source_context["research_identities"],
        "readiness_status": "current",
        "gate": {
            "allowed_to_speak": retrieval_ready,
            "blocking_reasons": retrieval_blocking_reasons,
        },
        "created_at": created_at,
        "created_by": created_by,
    }

    path = package_dir / "phases" / "startup_receipts.json"
    receipts = _read_receipts(path, normalized_deal_id)
    agents = receipts.setdefault("agents", {})
    agents[canonical_profile] = receipt
    by_agent_phase = receipts.setdefault("by_agent_phase", {})
    agent_history = by_agent_phase.setdefault(canonical_profile, {})
    agent_history[normalized_round] = receipt
    receipts["updated_at"] = created_at
    deal_store.write_json(path, receipts)
    deal_store.append_audit_event(
        normalized_deal_id,
        {
            "event_type": "deal_startup_retrieval_receipt_generated",
            "agent_id": canonical_profile,
            "round_name": normalized_round,
            "shared_hits": receipt["shared_hits"],
            "private_hits": receipt["private_hits"],
            "dynamic_query_count": len(receipt.get("dynamic_queries") or []),
            "external_research_enabled": bool(include_external),
            "vector_retrieval_enabled": bool(include_vector),
            "rerank_enabled": bool(include_rerank),
            "gaps": gaps,
            "source_ids": source_context["source_ids"],
            "evidence_snapshot_hash": source_context["evidence_snapshot_hash"],
            "created_by": created_by,
        },
        wiki_root=wiki_root,
    )
    return receipt


def read_startup_retrieval_receipt(
    deal_id: str,
    profile_id: str,
    *,
    round_name: str | None = None,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    package_dir = _require_package_dir(deal_id, wiki_root=wiki_root)
    canonical_profile = _normalize_profile_id(profile_id)
    receipts = _read_receipts(package_dir / "phases" / "startup_receipts.json", deal_store.validate_deal_id(deal_id))
    agents = receipts.get("agents") if isinstance(receipts.get("agents"), dict) else {}
    if round_name is not None:
        normalized_round = _normalize_round_name(round_name)
        history = receipts.get("by_agent_phase") if isinstance(receipts.get("by_agent_phase"), dict) else {}
        agent_history = history.get(canonical_profile) if isinstance(history.get(canonical_profile), dict) else {}
        receipt = agent_history.get(normalized_round)
    else:
        receipt = agents.get(canonical_profile)
    if isinstance(receipt, dict):
        receipt = dict(receipt)
        current = _primary_market_source_context(package_dir)
        freshness = evaluate_startup_receipt_freshness(receipt, current)
        receipt["readiness_status"] = freshness["status"]
        receipt["freshness"] = freshness
        if freshness["stale"]:
            receipt["stale_reason"] = freshness["reasons"][0]
            receipt["current_evidence_snapshot_hash"] = freshness["current_evidence_snapshot_hash"]
            gate = receipt.get("gate") if isinstance(receipt.get("gate"), dict) else {}
            receipt["gate"] = {
                **gate,
                "allowed_to_speak": False,
                "blocking_reasons": freshness["reasons"],
            }
    return {
        "deal_id": deal_store.validate_deal_id(deal_id),
        "agent_id": canonical_profile,
        "receipt": receipt if isinstance(receipt, dict) else None,
    }
