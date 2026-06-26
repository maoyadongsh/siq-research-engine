#!/usr/bin/env python3
"""Build a deterministic qualitative evidence snapshot for SIQ reports.

The report generator already has structured financial metrics. This builder
adds the business texture: management explanations, strategy, products,
industry risks, governance facts, and other non-numeric evidence that should
shape section-level analysis.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any


BUCKET_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("strategy", ("战略", "变革", "经营管控", "组织效能", "稳合资", "强自主", "拓生态")),
    ("product_brand", ("产品", "车型", "品牌", "传祺", "埃安", "昊铂", "本田", "丰田", "新能源")),
    ("operation_driver", ("销量", "产销", "需求", "渠道", "客户", "供应商", "价格", "竞争")),
    ("rd_technology", ("研发", "技术", "平台", "电池", "发动机", "IPD", "创新")),
    ("industry_competition", ("行业", "市场竞争", "价格战", "竞争格局", "产业生态")),
    ("external_risk", ("地缘", "贸易", "关税", "供应链", "原材料", "政策", "出口")),
    ("governance", ("董事", "审计", "治理", "担保", "资金占用", "关联方", "换届")),
]

IMPORTANT_SEGMENT_TYPES = {
    "management_discussion",
    "business_overview",
    "risk_factors",
    "major_events",
    "corporate_governance",
    "shareholders",
}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return load_json(path)


def dump_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def clean_text(value: Any, limit: int = 220) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def item_text(item: dict[str, Any]) -> str:
    return " ".join(
        clean_text(item.get(key), 1000)
        for key in ["profile_type", "subject", "description", "claim_type", "statement", "reasoning_summary", "risk_type", "risk", "impact", "mitigation", "event_type", "event", "title", "summary"]
        if item.get(key)
    )


def bucket_for(item: dict[str, Any], fallback: str) -> str:
    profile_type = str(item.get("profile_type") or "")
    if profile_type == "strategy":
        return "strategy"
    if profile_type == "rd":
        return "rd_technology"
    if profile_type == "product_service":
        return "product_brand"
    if profile_type == "governance":
        return "governance"

    claim_type = str(item.get("claim_type") or "")
    if claim_type == "industry_position":
        return "industry_competition"
    if claim_type == "operation_driver":
        return "operation_driver"
    if claim_type == "customer_supplier":
        return "operation_driver"

    risk_type = str(item.get("risk_type") or "")
    if risk_type == "market":
        return "industry_competition"
    if risk_type in {"policy", "supply_chain", "external"}:
        return "external_risk"

    event_type = str(item.get("event_type") or "")
    if event_type in {"organization", "governance", "board"}:
        return "governance"

    segment_type = str(item.get("segment_type") or "")
    if segment_type in {"risk_factors"}:
        return "external_risk"
    if segment_type in {"corporate_governance", "shareholders", "major_events"}:
        return "governance"
    if segment_type in {"rd_innovation"}:
        return "rd_technology"
    if segment_type in {"product_service", "segment_performance"}:
        return "product_brand"
    if segment_type in {"management_discussion"}:
        return "strategy"

    text = item_text(item)
    for bucket, keywords in BUCKET_RULES:
        if any(keyword in text for keyword in keywords):
            return bucket
    return fallback


def evidence_ids(item: dict[str, Any]) -> list[str]:
    ids = [str(eid) for eid in as_list(item.get("evidence_ids")) if str(eid).strip()]
    if ids:
        return ids
    return [str(seg) for seg in as_list(item.get("source_segment_ids")) if str(seg).strip()]


def source_segments(item: dict[str, Any]) -> list[str]:
    return [str(seg) for seg in as_list(item.get("source_segment_ids")) if str(seg).strip()]


def add_bucket_item(
    buckets: dict[str, list[dict[str, Any]]],
    bucket: str,
    kind: str,
    text: str,
    item: dict[str, Any],
    *,
    polarity: str | None = None,
) -> None:
    text = clean_text(text, 260)
    if not text:
        return
    existing_texts = {entry.get("text") for entry in buckets.setdefault(bucket, [])}
    if text in existing_texts:
        return
    buckets[bucket].append(
        {
            "kind": kind,
            "text": text,
            "polarity": polarity or item.get("stance") or item.get("confidence") or "neutral",
            "evidence_ids": evidence_ids(item),
            "source_segment_ids": source_segments(item),
            "needs_review": bool(item.get("needs_review")),
        }
    )


def load_llm_items(company_dir: Path, report_id: str) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    llm_dir = company_dir / "semantic" / "llm" / report_id
    raw = {
        "business_profile": load_json_if_exists(llm_dir / "business_profile.json").get("business_profile", []),
        "claims": load_json_if_exists(llm_dir / "claims.json").get("claims", []),
        "risks": load_json_if_exists(llm_dir / "risks.json").get("risks", []),
        "events": load_json_if_exists(llm_dir / "events.json").get("events", []),
        "review_queue": load_json_if_exists(llm_dir / "review_queue.json"),
    }
    return {key: [item for item in as_list(value) if isinstance(item, dict)] for key, value in raw.items() if key != "review_queue"}, raw["review_queue"]


def add_llm_layer(buckets: dict[str, list[dict[str, Any]]], items: dict[str, list[dict[str, Any]]]) -> None:
    for item in items.get("business_profile", []):
        text = f"{item.get('subject')}: {item.get('description')}"
        add_bucket_item(buckets, bucket_for(item, "strategy"), "business_profile", text, item)
    for item in items.get("claims", []):
        text = item.get("statement") or item.get("reasoning_summary")
        add_bucket_item(buckets, bucket_for(item, "operation_driver"), "claim", text, item, polarity=item.get("stance"))
    for item in items.get("risks", []):
        parts = [item.get("risk"), item.get("impact")]
        if item.get("mitigation"):
            parts.append(f"应对：{item.get('mitigation')}")
        add_bucket_item(buckets, bucket_for(item, "external_risk"), "risk", "；".join(str(p) for p in parts if p), item, polarity="risk")
    for item in items.get("events", []):
        text = item.get("event") or item.get("impact")
        add_bucket_item(buckets, bucket_for(item, "governance"), "event", text, item, polarity=item.get("status") or "neutral")


def add_segment_layer(company_dir: Path, buckets: dict[str, list[dict[str, Any]]]) -> None:
    segments_payload = load_json_if_exists(company_dir / "semantic" / "segments.json")
    for item in as_list(segments_payload.get("segments")):
        if not isinstance(item, dict):
            continue
        segment_type = str(item.get("segment_type") or "")
        summary = clean_text(item.get("summary"), 260)
        title = clean_text(item.get("title"), 80)
        if not summary:
            continue
        text = f"{title}: {summary}" if title else summary
        bucket = bucket_for(item, "operation_driver")
        if segment_type in IMPORTANT_SEGMENT_TYPES or bucket in {"strategy", "product_brand", "industry_competition", "external_risk", "governance"}:
            add_bucket_item(buckets, bucket, f"segment:{segment_type or 'unknown'}", text, item)


def trim_buckets(buckets: dict[str, list[dict[str, Any]]], limit: int) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    priority = {
        "business_profile": 0,
        "claim": 1,
        "risk": 2,
        "event": 3,
    }
    for bucket, entries in buckets.items():
        sorted_entries = sorted(
            entries,
            key=lambda item: (
                priority.get(str(item.get("kind")).split(":")[0], 4),
                bool(item.get("needs_review")),
                str(item.get("text")),
            ),
        )
        result[bucket] = sorted_entries[:limit]
    return result


def build_interpretation(buckets: dict[str, list[dict[str, Any]]]) -> dict[str, list[str]]:
    output: dict[str, list[str]] = {}
    for bucket, entries in buckets.items():
        lines: list[str] = []
        for item in entries[:4]:
            evidence = ", ".join(item.get("evidence_ids") or [])
            suffix = f"（证据：{evidence}）" if evidence else ""
            lines.append(f"{item.get('text')}{suffix}")
        output[bucket] = lines
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--company-dir", required=True, type=Path)
    parser.add_argument("--report-id", default="2025-annual")
    parser.add_argument("--year", type=int, default=2025)
    parser.add_argument("--max-items-per-bucket", type=int, default=8)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    company = load_json_if_exists(args.company_dir / "company.json")
    items, review_queue = load_llm_items(args.company_dir, args.report_id)
    buckets: dict[str, list[dict[str, Any]]] = {}
    add_llm_layer(buckets, items)
    add_segment_layer(args.company_dir, buckets)
    buckets = trim_buckets(buckets, args.max_items_per_bucket)

    evidence_count = sum(len(entries) for entries in buckets.values())
    result = {
        "schema_version": 1,
        "generated_by": "qualitative_evidence_builder.py",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "company_id": company.get("company_id") or args.company_dir.name,
        "report_id": args.report_id,
        "report_year": args.year,
        "strict_ok": evidence_count >= 6,
        "bucket_count": len(buckets),
        "evidence_count": evidence_count,
        "buckets": buckets,
        "interpretation": build_interpretation(buckets),
        "review_queue": review_queue,
        "warnings": [] if evidence_count >= 6 else [f"qualitative_evidence_sparse:{evidence_count}<6"],
    }

    output = args.output or args.company_dir / "analysis" / ".work" / "qualitative_snapshot.json"
    dump_json(output, result)
    print(json.dumps({"ok": result["strict_ok"], "output": str(output), "bucket_count": len(buckets), "evidence_count": evidence_count}, ensure_ascii=False, indent=2))
    return 0 if result["strict_ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
