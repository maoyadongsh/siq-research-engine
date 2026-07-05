"""SIQ-native deal retrieval planning and local evidence ranking."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from services import deal_store
from services import external_research_clients
from services import ic_policy
from services import rerank_provider
from services import vector_retrieval


DEAL_RETRIEVAL_SCHEMA = "siq_deal_retrieval_result_v1"
LOCAL_RETRIEVAL_MODE = "local_dynamic_evidence_package_v1"
DEFAULT_LIMIT = 10
MAX_LIMIT = 50

PROFILE_RULES: dict[str, dict[str, Any]] = {
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
    if canonical == "siq_ic_master_coordinator":
        raise ValueError("Deal retrieval is not required for siq_ic_master_coordinator")
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
    include_rerank: bool = False,
    vector_collections: list[str] | tuple[str, ...] | None = None,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    package_dir = deal_store.safe_deal_dir(deal_id, wiki_root=wiki_root)
    if not (package_dir / "manifest.json").is_file():
        raise FileNotFoundError(deal_id)
    normalized_deal_id = deal_store.validate_deal_id(deal_id)
    canonical_profile = normalize_profile_id(profile_id)
    normalized_limit = normalize_limit(limit)
    items, invalid_lines = read_evidence_items(package_dir)
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
    hits = [evidence_hit(item, score=score) for item, score in ranked[:normalized_limit]]

    gaps: list[str] = []
    if invalid_lines:
        gaps.append(f"evidence_items.ndjson has {invalid_lines} invalid lines")
    if not items:
        gaps.append("evidence_package_missing_or_empty")
    if items and not matched:
        gaps.append("no_role_matched_evidence")

    context = _project_context(package_dir, normalized_deal_id, query)
    external_query = dynamic_queries[1]["query"] if dynamic_queries else context["query"]
    vector_payload = vector_retrieval.retrieve_vector_hits(
        query=external_query,
        profile_id=canonical_profile,
        enabled=include_vector,
        collections=vector_collections,
        top_k=min(normalized_limit, 20),
    )
    rerank_candidates = hits + [
        {
            "source_id": item.get("source_id"),
            "evidence_id": item.get("evidence_id"),
            "document_id": item.get("document_id"),
            "quote_preview": item.get("quote_preview") or item.get("text"),
            "snippet": item.get("text"),
            "retrieval_score": item.get("score"),
            "source": "vector",
        }
        for item in vector_payload.get("hits", [])
        if isinstance(item, dict)
    ]
    rerank_payload = rerank_provider.rerank_candidates(
        query=external_query,
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
        "rerank": rerank_payload,
        "external_research": external_research,
        "milvus_used": vector_payload.get("status") == "completed",
        "postgres_used": False,
        "reranker_used": rerank_payload.get("status") == "completed",
    }
