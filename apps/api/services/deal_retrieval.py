"""SIQ-native deal retrieval planning and local evidence ranking."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from services import deal_store, external_research_clients, ic_policy, rerank_provider, vector_retrieval

DEAL_RETRIEVAL_SCHEMA = "siq_deal_retrieval_result_v1"
LOCAL_RETRIEVAL_MODE = "local_dynamic_evidence_package_v1"
DEFAULT_LIMIT = 10
MAX_LIMIT = 50
_PRIVATE_PATH_FIELDS = (
    "source",
    "source_path",
    "path",
    "file_path",
    "wiki_path",
    "artifact_path",
    "root_path",
    "workspace_path",
    "source_uri",
)
_PRIVATE_SCOPE_FIELDS = (
    "agent_group",
    "source_class",
    "market_scope",
    "research_scope",
    "knowledge_scope",
    "domain",
)
_SECONDARY_MARKET_SCOPE_VALUES = {"secondary_market", "secondary-market"}

PROFILE_RULES: dict[str, dict[str, Any]] = {
    "siq_ic_master_coordinator": {
        "dimensions": ("business", "finance", "legal", "risk", "unknown"),
        "focus_terms": ("readiness", "scope", "evidence gap", "workflow", "准入", "范围", "证据缺口", "流程"),
    },
    "siq_ic_strategist": {
        "dimensions": ("business",),
        "focus_terms": ("strategy", "market", "policy", "growth", "exit", "战略", "市场", "政策", "增长", "退出"),
    },
    "siq_ic_sector_expert": {
        "dimensions": ("business",),
        "focus_terms": ("market", "competition", "technology", "customer", "行业", "竞争", "技术", "客户"),
    },
    "siq_ic_finance_auditor": {
        "dimensions": ("finance",),
        "focus_terms": ("revenue", "margin", "cash", "valuation", "cap table", "收入", "毛利", "现金流", "估值", "融资"),
    },
    "siq_ic_legal_scanner": {
        "dimensions": ("legal",),
        "focus_terms": ("contract", "license", "patent", "lawsuit", "compliance", "合同", "资质", "专利", "诉讼", "合规"),
    },
    "siq_ic_risk_controller": {
        "dimensions": ("risk",),
        "focus_terms": ("risk", "supply chain", "litigation", "sanction", "风险", "供应链", "处罚", "舆情"),
    },
    "siq_ic_chairman": {
        "dimensions": ("business", "finance", "legal", "risk", "unknown"),
        "focus_terms": ("decision", "risk", "score", "terms", "exit", "决策", "风险", "评分", "条款", "退出"),
    },
}

CN_STOPWORDS = {
    "的", "了", "在", "是", "和", "及", "与", "或", "对", "为", "于", "以", "中", "本", "该",
    "项目", "公司", "情况", "相关", "主要", "进行", "目前", "截至", "包括", "通过",
}


def normalize_profile_id(profile_id: str) -> str:
    canonical = ic_policy.canonical_ic_profile_id(profile_id)
    if canonical not in ic_policy.IC_PROFILE_IDS:
        raise ValueError(f"Unknown IC profile: {profile_id}")
    return canonical


def normalize_limit(value: int | str | None) -> int:
    try:
        parsed = int(value) if value is not None else DEFAULT_LIMIT
    except (TypeError, ValueError):
        parsed = DEFAULT_LIMIT
    return max(1, min(parsed, MAX_LIMIT))


def read_evidence_items(package_dir: Path) -> tuple[list[dict[str, Any]], int]:
    items: list[dict[str, Any]] = []
    invalid = 0
    try:
        lines = (package_dir / "evidence" / "evidence_items.ndjson").read_text(encoding="utf-8").splitlines()
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


def _tokenize(text: str) -> list[str]:
    normalized = re.sub(r"[^\w\u4e00-\u9fff]+", " ", str(text or "").lower())
    tokens: list[str] = []
    for segment in normalized.split():
        if len(segment) <= 1:
            continue
        if re.fullmatch(r"[0-9.]+", segment):
            continue
        if re.search(r"[\u4e00-\u9fff]", segment):
            if segment not in CN_STOPWORDS:
                tokens.append(segment)
            if len(segment) >= 4:
                for size in (4, 3, 2):
                    for index in range(0, len(segment) - size + 1):
                        candidate = segment[index : index + size]
                        if candidate not in CN_STOPWORDS:
                            tokens.append(candidate)
        else:
            tokens.append(segment)
    return tokens


def _project_context(package_dir: Path, deal_id: str, explicit_query: str | None) -> dict[str, str]:
    project_meta = deal_store.read_json(package_dir / "project_meta.json", {}) or {}
    workflow = deal_store.read_json(package_dir / "phases" / "workflow_state.json", {}) or {}
    company_name = str(project_meta.get("company_name") or workflow.get("company_name") or "").strip()
    industry = str(project_meta.get("industry") or workflow.get("industry") or "").strip()
    stage = str(project_meta.get("stage") or workflow.get("stage") or "").strip()
    query = " ".join(str(explicit_query or "").split())
    if not query:
        query = " ".join(part for part in (company_name, industry, stage, deal_id) if part).strip()
    return {
        "company_name": company_name,
        "industry": industry,
        "stage": stage,
        "query": query[:300],
    }


def _private_background_query(
    *,
    context: dict[str, str],
    focus_terms: list[str],
    matched_evidence: list[tuple[dict[str, Any], float]],
) -> str:
    evidence_text = " ".join(
        str(item.get("claim") or item.get("quote") or item.get("citation") or "").strip()
        for item, _score in matched_evidence[:6]
        if str(item.get("claim") or item.get("quote") or item.get("citation") or "").strip()
    )
    return " ".join(
        part
        for part in (
            context.get("industry"),
            context.get("stage"),
            " ".join(focus_terms[:10]),
            evidence_text[:600],
        )
        if str(part or "").strip()
    )[:900]


def _hit_project_tag(hit: dict[str, Any]) -> str:
    metadata = hit.get("metadata") if isinstance(hit.get("metadata"), dict) else {}
    return str(hit.get("project_tag") or metadata.get("project_tag") or "").strip()


def _shared_hit_matches_snapshot(
    hit: dict[str, Any],
    *,
    deal_id: str,
    snapshot_hash: str,
) -> bool:
    metadata = hit.get("metadata") if isinstance(hit.get("metadata"), dict) else {}
    return bool(
        snapshot_hash
        and _hit_project_tag(hit) == deal_id
        and metadata.get("domain") == "primary_market"
        and metadata.get("source_class") == "project_evidence"
        and metadata.get("project_fact") is True
        and metadata.get("deal_id") == deal_id
        and metadata.get("snapshot_hash") == snapshot_hash
    )


def _private_hit_has_secondary_market_source(hit: dict[str, Any]) -> bool:
    metadata = hit.get("metadata") if isinstance(hit.get("metadata"), dict) else {}
    provenance = metadata.get("provenance") if isinstance(metadata.get("provenance"), dict) else {}
    source_metadata = (
        metadata.get("source_metadata")
        if isinstance(metadata.get("source_metadata"), dict)
        else {}
    )
    for container in (hit, metadata, provenance, source_metadata):
        for field in _PRIVATE_SCOPE_FIELDS:
            value = str(container.get(field) or "").strip().lower()
            if value in _SECONDARY_MARKET_SCOPE_VALUES:
                return True
        for field in _PRIVATE_PATH_FIELDS:
            value = str(container.get(field) or "").strip().replace("\\", "/").lower()
            if (
                value == "data/wiki/companies"
                or "data/wiki/companies/" in value
                or value.startswith("secondary_market/")
                or "/secondary_market/" in value
            ):
                return True
    return False


def _primary_market_vector_collections(
    configured: list[str] | tuple[str, ...] | None,
    *,
    profile_id: str,
) -> tuple[list[str] | None, list[str]]:
    required = [vector_retrieval.SHARED_DEAL_COLLECTION, profile_id]
    if configured is None:
        return required, []
    requested = list(dict.fromkeys(
        str(item).strip() for item in configured if str(item or "").strip()
    ))
    if not requested:
        return required, []
    allowed = {vector_retrieval.SHARED_DEAL_COLLECTION, profile_id}
    rejected = [item for item in requested if item not in allowed]
    # Primary-market retrieval always executes both required lanes. In
    # particular, never let the generic SIQ_MILVUS_COLLECTIONS environment
    # variable select a secondary-market collection before post-filtering.
    return required, rejected


def _filter_primary_market_vector_payload(
    payload: dict[str, Any],
    *,
    profile_id: str,
    deal_id: str,
    snapshot_hash: str,
) -> tuple[dict[str, Any], dict[str, int]]:
    raw_hits = [item for item in payload.get("hits") or [] if isinstance(item, dict)]
    accepted_hits: list[dict[str, Any]] = []
    rejected_shared = 0
    rejected_stale_shared = 0
    rejected_private = 0
    rejected_collection = 0
    for item in raw_hits:
        collection = str(item.get("collection") or "")
        if collection == vector_retrieval.SHARED_DEAL_COLLECTION:
            if _hit_project_tag(item) != deal_id:
                rejected_shared += 1
                continue
            if not _shared_hit_matches_snapshot(
                item,
                deal_id=deal_id,
                snapshot_hash=snapshot_hash,
            ):
                rejected_stale_shared += 1
                continue
        elif collection == profile_id and _private_hit_has_secondary_market_source(item):
            rejected_private += 1
            continue
        elif collection not in {vector_retrieval.SHARED_DEAL_COLLECTION, profile_id}:
            rejected_collection += 1
            continue
        accepted_hits.append(item)

    raw_methodology = [
        item for item in payload.get("methodology_hits") or [] if isinstance(item, dict)
    ]
    accepted_methodology = [
        item
        for item in raw_methodology
        if str(item.get("collection") or "") == profile_id
        and not _private_hit_has_secondary_market_source(item)
    ]
    rejected_private += sum(
        1
        for item in raw_methodology
        if str(item.get("collection") or "") == profile_id
        and _private_hit_has_secondary_market_source(item)
    )
    rejected_collection += sum(
        1 for item in raw_methodology if str(item.get("collection") or "") != profile_id
    )

    filtered_payload = dict(payload)
    filtered_payload["hits"] = accepted_hits
    filtered_payload["hit_count"] = len(accepted_hits)
    filtered_payload["methodology_hits"] = accepted_methodology
    filtered_payload["methodology_hit_count"] = len(accepted_methodology)
    if isinstance(payload.get("collection_hit_counts"), dict):
        filtered_payload["collection_hit_counts"] = {
            collection: sum(
                1 for item in accepted_hits if str(item.get("collection") or "") == collection
            )
            for collection in payload["collection_hit_counts"]
        }
    filtered_payload["primary_market_filter"] = {
        "deal_id": deal_id,
        "evidence_snapshot_hash": snapshot_hash or None,
        "cross_deal_shared_hits_rejected": rejected_shared,
        "stale_or_unbound_shared_hits_rejected": rejected_stale_shared,
        "secondary_market_private_hits_rejected": rejected_private,
        "disallowed_collection_hits_rejected": rejected_collection,
    }
    return filtered_payload, {
        "cross_deal_shared_hits_rejected": rejected_shared,
        "stale_or_unbound_shared_hits_rejected": rejected_stale_shared,
        "secondary_market_private_hits_rejected": rejected_private,
        "disallowed_collection_hits_rejected": rejected_collection,
    }


def _dedupe_background_hits(
    hits: list[dict[str, Any]],
    *,
    methodology: bool,
) -> tuple[list[dict[str, Any]], int]:
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    dropped = 0
    for item in hits:
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        if methodology:
            key = str(
                metadata.get("content_hash")
                or metadata.get("knowledge_id")
                or item.get("source_id")
                or ""
            )
        else:
            key = str(
                metadata.get("source_path")
                or metadata.get("source")
                or item.get("title")
                or item.get("source_id")
                or ""
            )
        if not key or key in seen:
            dropped += 1
            continue
        seen.add(key)
        selected.append(dict(item))
    return selected, dropped


def _select_private_background_hits(
    *,
    methodology_hits: list[dict[str, Any]],
    domain_hits: list[dict[str, Any]],
    limit: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    methodology_unique, methodology_duplicates = _dedupe_background_hits(
        methodology_hits,
        methodology=True,
    )
    domain_unique, domain_duplicates = _dedupe_background_hits(
        domain_hits,
        methodology=False,
    )
    methodology_limit = min(2, limit)
    selected_methodology = [
        {
            **item,
            "source_class": "background_knowledge",
            "knowledge_lane": "methodology",
            "selection_reason": "managed_profile_methodology",
        }
        for item in methodology_unique[:methodology_limit]
    ]
    domain_limit = min(
        2,
        len(selected_methodology),
        max(0, limit - len(selected_methodology)),
    )
    selected_domain = [
        {
            **item,
            "source_class": "background_knowledge",
            "knowledge_lane": "domain_background",
            "selection_reason": "role_evidence_vector_relevance",
        }
        for item in domain_unique[:domain_limit]
    ]
    selected = [*selected_methodology, *selected_domain]
    return selected, {
        "methodology_candidates": len(methodology_hits),
        "methodology_selected": len(selected_methodology),
        "methodology_duplicates_dropped": methodology_duplicates,
        "domain_candidates": len(domain_hits),
        "domain_selected": len(selected_domain),
        "domain_duplicates_dropped": domain_duplicates,
        "selected_total": len(selected),
        "limit": limit,
    }


def _top_project_terms(items: list[dict[str, Any]], *, limit: int = 12) -> list[str]:
    counter: Counter[str] = Counter()
    for item in items:
        text = " ".join(
            str(item.get(key) or "")
            for key in ("quote", "claim", "citation", "dimension", "evidence_type")
        )
        counter.update(_tokenize(text))
    return [term for term, _ in counter.most_common(limit)]


def build_dynamic_queries(
    *,
    package_dir: Path,
    deal_id: str,
    profile_id: str,
    query: str | None = None,
    evidence_items: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    canonical_profile = normalize_profile_id(profile_id)
    context = _project_context(package_dir, deal_id, query)
    rules = PROFILE_RULES.get(canonical_profile, {})
    focus_terms = [str(term) for term in rules.get("focus_terms", ())]
    project_terms = _top_project_terms(evidence_items or [], limit=8)
    base_parts = [context["query"], context["company_name"], context["industry"], context["stage"]]
    base_query = " ".join(part for part in base_parts if part).strip()
    role_query = " ".join([base_query, *focus_terms[:8], *project_terms[:6]]).strip()
    evidence_gap_query = " ".join([base_query, "evidence gap verification", *focus_terms[:4]]).strip()
    return [
        {
            "query_type": "base",
            "query": base_query[:400],
            "terms": _tokenize(base_query)[:30],
        },
        {
            "query_type": "role_focus",
            "query": role_query[:400],
            "terms": _tokenize(role_query)[:50],
        },
        {
            "query_type": "evidence_gap",
            "query": evidence_gap_query[:400],
            "terms": _tokenize(evidence_gap_query)[:50],
        },
    ]


def _matches_profile(item: dict[str, Any], profile_id: str, dimensions: tuple[str, ...]) -> bool:
    role_hints = item.get("role_hints") if isinstance(item.get("role_hints"), list) else []
    if profile_id in role_hints:
        return True
    dimension = str(item.get("dimension") or "").strip()
    return dimension in dimensions


def _evidence_text(item: dict[str, Any]) -> str:
    return " ".join(
        str(item.get(key) or "")
        for key in ("evidence_id", "quote", "claim", "citation", "dimension", "evidence_type")
    )


def _score_item(item: dict[str, Any], *, profile_id: str, dimensions: tuple[str, ...], query_terms: set[str]) -> float:
    score = 0.0
    if _matches_profile(item, profile_id, dimensions):
        score += 8.0
    if str(item.get("dimension") or "") in dimensions:
        score += 4.0
    if str(item.get("evidence_type") or "") == "verified":
        score += 2.0
    text_terms = set(_tokenize(_evidence_text(item)))
    overlap = text_terms.intersection(query_terms)
    score += min(len(overlap), 12) * 1.2
    return round(score, 4)


def evidence_hit(item: dict[str, Any], *, score: float | None = None) -> dict[str, Any]:
    hit = {
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
    if score is not None:
        hit["retrieval_score"] = score
    return hit


def retrieve_for_agent(
    deal_id: str,
    profile_id: str,
    *,
    query: str | None = None,
    limit: int | str | None = DEFAULT_LIMIT,
    include_external: bool = False,
    external_providers: list[str] | tuple[str, ...] | None = None,
    include_vector: bool = False,
    include_rerank: bool = True,
    vector_collections: list[str] | tuple[str, ...] | None = None,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    package_dir = deal_store.safe_deal_dir(deal_id, wiki_root=wiki_root)
    if not (package_dir / "manifest.json").is_file():
        raise FileNotFoundError(deal_id)
    normalized_deal_id = deal_store.validate_deal_id(deal_id)
    canonical_profile = normalize_profile_id(profile_id)
    normalized_limit = normalize_limit(limit)
    snapshot = deal_store.read_json(package_dir / "evidence" / "evidence_snapshot.json", {}) or {}
    current_snapshot_hash = str(snapshot.get("snapshot_hash") or "") if isinstance(snapshot, dict) else ""
    items, invalid_lines = read_evidence_items(package_dir)
    unbound_local_item_count = 0
    if not current_snapshot_hash and items:
        unbound_local_item_count = len(items)
        items = []
    rules = PROFILE_RULES.get(canonical_profile, {})
    dimensions = tuple(str(item) for item in rules.get("dimensions", ()))
    dynamic_queries = build_dynamic_queries(
        package_dir=package_dir,
        deal_id=normalized_deal_id,
        profile_id=canonical_profile,
        query=query,
        evidence_items=items,
    )
    query_terms: set[str] = set()
    for dynamic_query in dynamic_queries:
        query_terms.update(str(term) for term in dynamic_query.get("terms") or [])

    scored = [
        (item, _score_item(item, profile_id=canonical_profile, dimensions=dimensions, query_terms=query_terms))
        for item in items
    ]
    matched = [(item, score) for item, score in scored if _matches_profile(item, canonical_profile, dimensions)]
    if not matched and canonical_profile == "siq_ic_chairman":
        matched = scored
    ranked = sorted(matched, key=lambda pair: pair[1], reverse=True)
    hits = [
        {**evidence_hit(item, score=score), "source_class": "project_evidence"}
        for item, score in ranked[:normalized_limit]
    ]

    gaps: list[str] = []
    if invalid_lines:
        gaps.append(f"evidence_items.ndjson has {invalid_lines} invalid lines")
    if unbound_local_item_count:
        gaps.append(
            "evidence_snapshot_missing_local_evidence_rejected: "
            f"{unbound_local_item_count}"
        )
    if not items:
        gaps.append("evidence_package_missing_or_empty")
    if items and not matched:
        gaps.append("no_role_matched_evidence")

    context = _project_context(package_dir, normalized_deal_id, query)
    external_query = dynamic_queries[1]["query"] if dynamic_queries else context["query"]
    private_background_query = _private_background_query(
        context=context,
        focus_terms=[str(item) for item in rules.get("focus_terms", ())],
        matched_evidence=ranked,
    )
    safe_vector_collections, rejected_vector_collections = _primary_market_vector_collections(
        vector_collections,
        profile_id=canonical_profile,
    )
    if rejected_vector_collections:
        gaps.append(
            "disallowed_primary_market_vector_collections: "
            + ", ".join(rejected_vector_collections)
        )
    vector_payload = vector_retrieval.retrieve_vector_hits(
        query=external_query,
        profile_id=canonical_profile,
        private_query=private_background_query,
        enabled=include_vector,
        collections=safe_vector_collections,
        required_physical_collections=vector_retrieval.primary_market_physical_collections(
            canonical_profile
        ),
        allowed_project_tag=normalized_deal_id,
        top_k=min(normalized_limit, 20),
    )
    vector_payload, primary_market_filter = _filter_primary_market_vector_payload(
        vector_payload,
        profile_id=canonical_profile,
        deal_id=normalized_deal_id,
        snapshot_hash=current_snapshot_hash,
    )
    if primary_market_filter["cross_deal_shared_hits_rejected"]:
        gaps.append(
            "cross_deal_shared_vector_hits_rejected: "
            f"{primary_market_filter['cross_deal_shared_hits_rejected']}"
        )
    if primary_market_filter["stale_or_unbound_shared_hits_rejected"]:
        gaps.append(
            "stale_or_unbound_shared_vector_hits_rejected: "
            f"{primary_market_filter['stale_or_unbound_shared_hits_rejected']}"
        )
    if primary_market_filter["secondary_market_private_hits_rejected"]:
        gaps.append(
            "secondary_market_private_vector_hits_rejected: "
            f"{primary_market_filter['secondary_market_private_hits_rejected']}"
        )
    if primary_market_filter["disallowed_collection_hits_rejected"]:
        gaps.append(
            "disallowed_vector_collection_hits_rejected: "
            f"{primary_market_filter['disallowed_collection_hits_rejected']}"
        )
    vector_hits = [item for item in vector_payload.get("hits", []) if isinstance(item, dict)]
    raw_domain_background_hits = [
        {**item, "source_class": "background_knowledge"}
        for item in vector_hits
        if str(item.get("collection") or "") == canonical_profile
        and str((item.get("metadata") or {}).get("managed_by") or "")
        != vector_retrieval.MANAGED_KNOWLEDGE_WRITER
    ]
    raw_methodology_hits = [
        item
        for item in vector_payload.get("methodology_hits") or []
        if isinstance(item, dict)
    ]
    background_knowledge_hits, background_selection = _select_private_background_hits(
        methodology_hits=raw_methodology_hits,
        domain_hits=raw_domain_background_hits,
        limit=normalized_limit,
    )
    methodology_hits = [
        item for item in background_knowledge_hits if item.get("knowledge_lane") == "methodology"
    ]
    domain_background_hits = [
        item
        for item in background_knowledge_hits
        if item.get("knowledge_lane") == "domain_background"
    ]
    shared_vector_hits = [
        {**item, "source_class": "project_evidence"}
        for item in vector_hits
        if str(item.get("collection") or "") == "siq_deal_shared"
    ]
    rerank_candidates = [
        {**item, "source_class": "project_evidence"}
        for item in hits
    ] + [
        {
            "source_id": item.get("source_id"),
            "evidence_id": item.get("evidence_id"),
            "document_id": item.get("document_id"),
            "quote_preview": item.get("quote_preview") or item.get("text"),
            "snippet": item.get("text"),
            "retrieval_score": item.get("score"),
            "source": "vector",
            "collection": item.get("collection"),
            "source_class": (
                "background_knowledge"
                if str(item.get("collection") or "") == canonical_profile
                else "project_evidence"
            ),
        }
        for item in [*shared_vector_hits, *background_knowledge_hits]
    ]
    rerank_query = " ".join(
        part for part in (external_query, private_background_query) if str(part or "").strip()
    )[:600]
    rerank_payload = rerank_provider.rerank_candidates(
        query=rerank_query,
        candidates=rerank_candidates,
        enabled=include_rerank,
        top_n=normalized_limit,
    )
    hybrid_hits = rerank_payload.get("results") if isinstance(rerank_payload.get("results"), list) else []
    external_research = external_research_clients.run_external_research(
        query=external_query,
        providers=external_providers,
        max_results=min(normalized_limit, 10),
        enabled=include_external,
    )

    return {
        "schema_version": DEAL_RETRIEVAL_SCHEMA,
        "deal_id": normalized_deal_id,
        "agent_id": canonical_profile,
        "query": context["query"],
        "retrieval_mode": LOCAL_RETRIEVAL_MODE,
        "dynamic_queries": dynamic_queries,
        "dimensions": list(dimensions),
        "evidence_hits": hits,
        "evidence_hit_count": len(hits),
        "hybrid_hits": hybrid_hits,
        "hybrid_hit_count": len(hybrid_hits),
        "matched_evidence_count": len(matched),
        "total_evidence_count": len(items),
        "invalid_evidence_lines": invalid_lines,
        "gaps": gaps,
        "vector_retrieval": vector_payload,
        "shared_vector_hits": shared_vector_hits,
        "private_background_query": private_background_query,
        "rerank_query": rerank_query,
        "background_knowledge_hits": background_knowledge_hits,
        "background_knowledge_hit_count": len(background_knowledge_hits),
        "methodology_hits": methodology_hits,
        "methodology_hit_count": len(methodology_hits),
        "domain_background_hits": domain_background_hits,
        "domain_background_hit_count": len(domain_background_hits),
        "background_selection": background_selection,
        "rerank": rerank_payload,
        "external_research": external_research,
        "milvus_used": vector_payload.get("status") == "completed",
        "postgres_used": False,
        "reranker_used": rerank_payload.get("status") == "completed",
        "retrieval_observability": {
            "strategy": vector_payload.get("retrieval_strategy") or {},
            "collection_candidate_counts": vector_payload.get("collection_candidate_counts") or {},
            "collection_hit_counts": vector_payload.get("collection_hit_counts") or {},
            "rerank_status": rerank_payload.get("status"),
            "rerank_reason": rerank_payload.get("reason"),
            "rerank_candidate_count": rerank_payload.get("candidate_count", 0),
            "rerank_result_count": rerank_payload.get("result_count", 0),
        },
    }
