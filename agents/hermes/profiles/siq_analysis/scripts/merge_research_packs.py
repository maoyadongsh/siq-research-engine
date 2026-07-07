#!/usr/bin/env python3
"""Merge SIQ research packs into section_drafts without changing the renderer contract."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


SECTION_BLOCK_TITLES = {
    "executive_summary": "综合补证判断",
    "key_changes": "关键变化补证",
    "operating_quality": "经营质量补证",
    "profitability_and_cost": "盈利与成本补证",
    "asset_quality_working_capital": "资产质量补证",
    "debt_liquidity": "偿债安全补证",
    "cash_flow_quality": "现金流补证",
    "industry_competition": "行业同业补证",
    "strategy_policy_external_risk": "战略与外部变量补证",
    "governance_compliance_shareholders": "治理风险补证",
    "valuation_expectation_gap": "估值锚补证",
    "risk_chain_scenario": "风险链补证",
    "tracking_checklist": "跟踪信号补证",
    "data_quality_traceability": "证据质量补证",
}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"json_root_not_object:{path}")
    return data


def dump_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_packs(research_packs_dir: Path) -> list[dict[str, Any]]:
    packs: list[dict[str, Any]] = []
    for path in sorted(research_packs_dir.glob("*.json")):
        pack = load_json(path)
        pack["_pack_file"] = str(path)
        packs.append(pack)
    return packs


def clean_text(value: Any, limit: int = 220) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."


def collect_findings_by_section(packs: list[dict[str, Any]]) -> dict[str, list[dict[str, str]]]:
    by_section: dict[str, list[dict[str, str]]] = {}
    for pack in packs:
        agent_id = str(pack.get("agent_id") or "unknown_agent")
        for finding in pack.get("key_findings", []) or []:
            if not isinstance(finding, dict):
                continue
            claim = clean_text(finding.get("claim"))
            if not claim:
                continue
            section_ids = finding.get("section_ids")
            if not isinstance(section_ids, list):
                continue
            for section_id in section_ids:
                sid = str(section_id)
                by_section.setdefault(sid, []).append({"agent_id": agent_id, "claim": claim})
    return by_section


def collect_missing_by_section(packs: list[dict[str, Any]]) -> dict[str, list[str]]:
    by_section: dict[str, list[str]] = {}
    for pack in packs:
        agent_id = str(pack.get("agent_id") or "unknown_agent")
        for item in pack.get("missing_inputs", []) or []:
            if not isinstance(item, dict):
                continue
            name = clean_text(item.get("name"), 120)
            reason = clean_text(item.get("reason"), 160)
            section_ids = item.get("section_ids")
            if not isinstance(section_ids, list):
                continue
            marker = f"{agent_id}:{name}:{reason}" if reason else f"{agent_id}:{name}"
            for section_id in section_ids:
                by_section.setdefault(str(section_id), []).append(marker)
    return by_section


def dedupe(items: list[str], limit: int) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        text = clean_text(item)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
        if len(result) >= limit:
            break
    return result


def merge_section(section: dict[str, Any], findings_by_section: dict[str, list[dict[str, str]]], missing_by_section: dict[str, list[str]]) -> bool:
    section_id = str(section.get("section_id") or "")
    findings = findings_by_section.get(section_id, [])
    missing = missing_by_section.get(section_id, [])
    changed = False

    blocks = section.get("narrative_blocks")
    if not isinstance(blocks, list):
        blocks = []
        section["narrative_blocks"] = blocks
    original_count = len(blocks)
    blocks[:] = [
        block for block in blocks
        if not (isinstance(block, dict) and block.get("source") == "research_pack_merge")
    ]
    if len(blocks) != original_count:
        changed = True

    claims = dedupe([f"{item['claim']}（来源：{item['agent_id']}）" for item in findings], 4)
    if claims:
        blocks.append({
            "title": SECTION_BLOCK_TITLES.get(section_id, "补充证据"),
            "role": "evidence",
            "source": "research_pack_merge",
            "items": claims,
        })
        changed = True

    if claims:
        judgements = section.get("judgements")
        if isinstance(judgements, list):
            for claim in claims[:2]:
                if claim not in judgements:
                    judgements.append(claim)
                    changed = True

    if missing:
        missing_fields = section.get("missing_fields")
        if not isinstance(missing_fields, list):
            missing_fields = []
            section["missing_fields"] = missing_fields
        for marker in dedupe([f"research_pack:{item}" for item in missing], 5):
            if marker not in missing_fields:
                missing_fields.append(marker)
                changed = True
        if section.get("review_required") is not True:
            section["review_required"] = True
            changed = True

    refs = section.get("research_pack_refs")
    wanted_refs = sorted({item["agent_id"] for item in findings} | {item.split(":", 1)[0] for item in missing})
    if wanted_refs and refs != wanted_refs:
        section["research_pack_refs"] = wanted_refs
        changed = True

    return changed


def build_manifest(work_dir: Path, packs: list[dict[str, Any]], changed_sections: list[str]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "generated_by": "merge_research_packs.py",
        "generated_at": now_iso(),
        "work_dir": str(work_dir),
        "pack_count": len(packs),
        "agent_ids": [str(pack.get("agent_id") or "") for pack in packs],
        "changed_sections": changed_sections,
        "review_required_agent_ids": [str(pack.get("agent_id")) for pack in packs if pack.get("review_required")],
        "missing_input_count": sum(len(pack.get("missing_inputs", []) or []) for pack in packs),
    }


def merge_research_packs(work_dir: Path, section_drafts_path: Path, output: Path | None = None) -> dict[str, Any]:
    output_path = output or section_drafts_path
    drafts = load_json(section_drafts_path)
    research_packs_dir = work_dir / "research_packs"
    packs = load_packs(research_packs_dir)
    findings_by_section = collect_findings_by_section(packs)
    missing_by_section = collect_missing_by_section(packs)

    sections = drafts.get("sections")
    if not isinstance(sections, list):
        raise ValueError("section_drafts.sections_not_list")

    changed_sections: list[str] = []
    for section in sections:
        if not isinstance(section, dict):
            continue
        if merge_section(section, findings_by_section, missing_by_section):
            changed_sections.append(str(section.get("section_id") or "unknown"))

    manifest = build_manifest(work_dir, packs, changed_sections)
    drafts["research_pack_manifest"] = manifest
    quality_report = drafts.get("quality_report")
    if isinstance(quality_report, dict):
        quality_report["research_pack_manifest"] = manifest
        review_queue = quality_report.get("review_queue")
        if isinstance(review_queue, list):
            for agent_id in manifest["review_required_agent_ids"]:
                marker = f"research_pack_review_required:{agent_id}"
                if marker not in review_queue:
                    review_queue.append(marker)
    dump_json(output_path, drafts)
    return {
        "ok": True,
        "stage": "completed",
        "work_dir": str(work_dir),
        "research_packs_dir": str(research_packs_dir),
        "section_drafts": str(output_path),
        "manifest": manifest,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Merge research_packs into section_drafts.json.")
    parser.add_argument("--work-dir", required=True, type=Path)
    parser.add_argument("--year", type=int, default=2025, help="Reserved for future versioned merge policies.")
    parser.add_argument("--section-drafts", type=Path, help="Default: <work-dir>/section_drafts.json")
    parser.add_argument("--output", type=Path, help="Default: overwrite section drafts in place")
    parser.add_argument("--write-manifest", type=Path, help="Optional separate merge manifest JSON")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    section_drafts_path = args.section_drafts or args.work_dir / "section_drafts.json"
    try:
        result = merge_research_packs(args.work_dir, section_drafts_path, args.output)
    except Exception as exc:
        result = {
            "ok": False,
            "stage": "merge_failed",
            "work_dir": str(args.work_dir),
            "section_drafts": str(section_drafts_path),
            "error": str(exc),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2
    if args.write_manifest:
        dump_json(args.write_manifest, result["manifest"])
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
