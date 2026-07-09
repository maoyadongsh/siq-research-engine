#!/usr/bin/env python3
"""Audit rule and LLM semantic layers for market Wiki recall readiness."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
WIKI_ROOT = REPO_ROOT / "data" / "wiki"
REPORT_MD = REPO_ROOT / "docs" / "superpowers" / "reports" / "market_llm_wiki_semantic_backtest.md"
REPORT_JSON = REPO_ROOT / "docs" / "superpowers" / "reports" / "market_llm_wiki_semantic_backtest.json"
MARKET_ROOTS = {
    "CN": WIKI_ROOT,
    "HK": WIKI_ROOT / "hk",
    "KR": WIKI_ROOT / "kr",
    "JP": WIKI_ROOT / "jp",
    "EU": WIKI_ROOT / "eu",
    "US": WIKI_ROOT / "us",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def markets_from_arg(value: str) -> list[str]:
    if value.upper() == "ALL":
        return ["HK", "KR", "JP", "EU", "US"]
    return [item.strip().upper() for item in value.split(",") if item.strip()]


def report_id_for(company_dir: Path) -> str:
    company = read_json(company_dir / "company.json", {}) or {}
    report_id = company.get("primary_report_id") or "2025-annual"
    if not (company_dir / "reports" / report_id).is_dir():
        reports = sorted(path for path in (company_dir / "reports").glob("*") if path.is_dir())
        if reports:
            report_id = reports[0].name
    return str(report_id)


def list_items(path: Path, key: str) -> list[dict[str, Any]]:
    payload = read_json(path, {}) or {}
    value = payload.get(key)
    return value if isinstance(value, list) else []


def audit_company(company_dir: Path) -> dict[str, Any]:
    semantic_dir = company_dir / "semantic"
    report_id = report_id_for(company_dir)
    segments = list_items(semantic_dir / "segments.json", "segments")
    evidence = list_items(semantic_dir / "evidence_semantic.json", "evidence")
    facts = list_items(semantic_dir / "facts.json", "facts")
    claims = list_items(semantic_dir / "claims.json", "claims")
    retrieval = read_json(semantic_dir / "retrieval_index.json", {}) or {}
    topics = retrieval.get("topics") if isinstance(retrieval.get("topics"), list) else []
    read_order = retrieval.get("recommended_read_order") if isinstance(retrieval.get("recommended_read_order"), list) else []
    allowed_segments = {item.get("segment_id") for item in segments if item.get("segment_id")}
    allowed_evidence = {item.get("evidence_id") for item in evidence if item.get("evidence_id")}

    llm_dir = semantic_dir / "llm" / report_id
    llm_files = {
        "business_profile": ("business_profile.json", "business_profile"),
        "claims": ("claims.json", "claims"),
        "risks": ("risks.json", "risks"),
        "events": ("events.json", "events"),
        "review_queue": ("review_queue.json", "review_queue"),
    }
    llm_counts = {}
    invalid_llm_ids = []
    for name, (filename, key) in llm_files.items():
        items = list_items(llm_dir / filename, key)
        llm_counts[name] = len(items)
        if name == "review_queue":
            continue
        for item in items:
            bad_segments = [sid for sid in item.get("source_segment_ids") or [] if sid not in allowed_segments]
            bad_evidence = [eid for eid in item.get("evidence_ids") or [] if eid not in allowed_evidence]
            if bad_segments or bad_evidence or not item.get("source_segment_ids") or not item.get("evidence_ids"):
                invalid_llm_ids.append({
                    "file": filename,
                    "id": item.get("profile_id") or item.get("claim_id") or item.get("risk_id") or item.get("event_id"),
                    "bad_segments": bad_segments,
                    "bad_evidence": bad_evidence,
                })

    issues = []
    if not segments:
        issues.append("segments_empty")
    if not evidence:
        issues.append("evidence_empty")
    if not facts:
        issues.append("facts_empty")
    if not topics:
        issues.append("retrieval_topics_empty")
    if llm_dir.exists() and invalid_llm_ids:
        issues.append("llm_invalid_ids")
    if llm_dir.exists() and not any(str(path).startswith("semantic/llm/") for path in read_order):
        issues.append("retrieval_index_missing_llm_read_order")

    return {
        "company_dir": company_dir.name,
        "report_id": report_id,
        "status": "ready" if not issues else "needs_review",
        "issues": issues,
        "counts": {
            "segments": len(segments),
            "evidence": len(evidence),
            "facts": len(facts),
            "claims": len(claims),
            "retrieval_topics": len(topics),
            "llm": llm_counts,
            "llm_invalid_ids": len(invalid_llm_ids),
        },
        "invalid_llm_ids": invalid_llm_ids[:20],
    }


def audit_market(market: str, root: Path) -> dict[str, Any]:
    companies_root = root / "companies"
    company_dirs = sorted(path for path in companies_root.iterdir() if path.is_dir()) if companies_root.is_dir() else []
    results = [audit_company(path) for path in company_dirs]
    totals = {
        "companies": len(results),
        "ready": sum(1 for item in results if item["status"] == "ready"),
        "needs_review": sum(1 for item in results if item["status"] != "ready"),
        "segments": sum(item["counts"]["segments"] for item in results),
        "evidence": sum(item["counts"]["evidence"] for item in results),
        "facts": sum(item["counts"]["facts"] for item in results),
        "claims": sum(item["counts"]["claims"] for item in results),
        "retrieval_topics": sum(item["counts"]["retrieval_topics"] for item in results),
        "llm_invalid_ids": sum(item["counts"]["llm_invalid_ids"] for item in results),
    }
    return {
        "market": market,
        "wiki_root": str(root),
        "totals": totals,
        "results": results,
    }


def write_markdown(payload: dict[str, Any]) -> None:
    lines = [
        "# Market LLM-Wiki Semantic Backtest",
        "",
        f"- Generated at: {payload['generated_at']}",
        "- Rule layer is the fact source. LLM layer is a retrieval/analysis candidate layer and must bind allowed segment/evidence ids.",
        "",
        "| Market | Companies | Ready | Needs review | Segments | Evidence | Facts | Retrieval topics | LLM invalid IDs |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for market in payload["markets"]:
        totals = market["totals"]
        lines.append(
            f"| {market['market']} | {totals['companies']} | {totals['ready']} | {totals['needs_review']} | "
            f"{totals['segments']} | {totals['evidence']} | {totals['facts']} | {totals['retrieval_topics']} | {totals['llm_invalid_ids']} |"
        )
    lines.extend(["", "## Needs Review", ""])
    any_issue = False
    for market in payload["markets"]:
        flagged = [item for item in market["results"] if item["status"] != "ready"]
        if not flagged:
            continue
        any_issue = True
        lines.append(f"### {market['market']}")
        for item in flagged[:80]:
            lines.append(f"- `{item['company_dir']}` `{item['report_id']}`: {', '.join(item['issues'])}")
        lines.append("")
    if not any_issue:
        lines.append("No needs-review companies in audited markets.")
    REPORT_MD.parent.mkdir(parents=True, exist_ok=True)
    REPORT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", default="ALL", help="HK,KR,JP,EU,US,CN or ALL")
    parser.add_argument("--wiki-root", default="", help="Override root for a single market")
    args = parser.parse_args()
    markets = []
    for market in markets_from_arg(args.market):
        root = Path(args.wiki_root) if args.wiki_root else MARKET_ROOTS.get(market)
        if root is None:
            continue
        markets.append(audit_market(market, root))
    payload = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "markets": markets,
    }
    write_json(REPORT_JSON, payload)
    write_markdown(payload)
    print(json.dumps({
        "markets": [
            {"market": item["market"], **item["totals"]}
            for item in markets
        ],
        "report": str(REPORT_MD),
        "json": str(REPORT_JSON),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
