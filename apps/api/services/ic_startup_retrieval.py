"""Local startup retrieval receipts for Deal OS IC agents.

This P1 bridge is intentionally file-backed and deterministic. It prepares the
receipt contract that IC agents must have before speaking, without invoking
Hermes, PostgreSQL, Milvus, or a private knowledge base.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from services import deal_retrieval
from services import deal_store
from services import ic_policy
from services.path_config import PROJECT_ROOT


STARTUP_RECEIPTS_SCHEMA = "siq_ic_startup_receipts_v1"
STARTUP_RETRIEVAL_MODE = "local_evidence_package_v1"
DEFAULT_EVIDENCE_LIMIT = 10
MAX_EVIDENCE_LIMIT = 50
PROFILE_DIMENSIONS: dict[str, tuple[str, ...]] = {
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
    if canonical == "siq_ic_master_coordinator":
        raise ValueError("Startup retrieval is not required for siq_ic_master_coordinator")
    return canonical


def _normalize_round_name(round_name: str | None) -> str:
    value = str(round_name or "R1").strip().upper()
    if value not in {"R1", "R2", "R4"}:
        raise ValueError("round_name must be R1, R2, or R4")
    return value


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


def _read_receipts(path: Path, deal_id: str) -> dict[str, Any]:
    payload = deal_store.read_json(path, None)
    if not isinstance(payload, dict):
        payload = {}
    agents = payload.get("agents") if isinstance(payload.get("agents"), dict) else {}
    return {
        "schema_version": STARTUP_RECEIPTS_SCHEMA,
        "deal_id": deal_id,
        "agents": agents,
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
    include_vector: bool = False,
    include_rerank: bool = False,
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

    gaps = [str(item) for item in retrieval.get("gaps", []) if str(item or "").strip()]
    missing_dimensions = _quality_missing_dimensions(package_dir)
    role_missing = sorted(set(dimensions).intersection(missing_dimensions))
    for dimension in role_missing:
        gaps.append(f"missing_{dimension}_evidence")

    receipt = {
        "receipt_id": _receipt_id(canonical_profile, normalized_round),
        "agent_id": canonical_profile,
        "legacy_agent_id": LEGACY_BY_PROFILE.get(canonical_profile),
        "round_name": normalized_round,
        "query": _project_query(package_dir, normalized_deal_id, query),
        "project_tag": normalized_deal_id,
        "retrieval_mode": STARTUP_RETRIEVAL_MODE,
        "retrieval_contract": retrieval,
        "dynamic_queries": retrieval.get("dynamic_queries") or [],
        "shared_hits": int(retrieval.get("matched_evidence_count") or 0),
        "private_hits": 0,
        "evidence_hits": evidence_hits,
        "evidence_hit_count": len(evidence_hits),
        "hybrid_hits": hybrid_hits,
        "hybrid_hit_count": len(hybrid_hits),
        "dimensions": list(dimensions),
        "workspace_rules_read": _workspace_rules_read(canonical_profile),
        "gaps": gaps,
        "vector_retrieval": retrieval.get("vector_retrieval") or {},
        "rerank": retrieval.get("rerank") or {},
        "external_research": retrieval.get("external_research") or {},
        "milvus_used": bool(retrieval.get("milvus_used")),
        "postgres_used": False,
        "reranker_used": bool(retrieval.get("reranker_used")),
        "hermes_used": False,
        "created_at": created_at,
        "created_by": created_by,
    }

    path = package_dir / "phases" / "startup_receipts.json"
    receipts = _read_receipts(path, normalized_deal_id)
    agents = receipts.setdefault("agents", {})
    agents[canonical_profile] = receipt
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
            "created_by": created_by,
        },
        wiki_root=wiki_root,
    )
    return receipt


def read_startup_retrieval_receipt(
    deal_id: str,
    profile_id: str,
    *,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    package_dir = _require_package_dir(deal_id, wiki_root=wiki_root)
    canonical_profile = _normalize_profile_id(profile_id)
    receipts = _read_receipts(package_dir / "phases" / "startup_receipts.json", deal_store.validate_deal_id(deal_id))
    agents = receipts.get("agents") if isinstance(receipts.get("agents"), dict) else {}
    receipt = agents.get(canonical_profile)
    return {
        "deal_id": deal_store.validate_deal_id(deal_id),
        "agent_id": canonical_profile,
        "receipt": receipt if isinstance(receipt, dict) else None,
    }
