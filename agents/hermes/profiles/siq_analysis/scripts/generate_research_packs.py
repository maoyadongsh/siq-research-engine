#!/usr/bin/env python3
"""Build deterministic SIQ research packs from existing report checkpoints."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


RESEARCH_AGENT_IDS = [
    "evidence_curator",
    "financial_modeler",
    "business_strategy_researcher",
    "industry_peer_researcher",
    "governance_risk_researcher",
]

SECTION_IDS = [
    "executive_summary",
    "key_changes",
    "operating_quality",
    "profitability_and_cost",
    "asset_quality_working_capital",
    "debt_liquidity",
    "cash_flow_quality",
    "industry_competition",
    "strategy_policy_external_risk",
    "governance_compliance_shareholders",
    "valuation_expectation_gap",
    "risk_chain_scenario",
    "tracking_checklist",
    "data_quality_traceability",
]

AGENT_SECTION_IDS = {
    "evidence_curator": SECTION_IDS,
    "financial_modeler": [
        "executive_summary",
        "key_changes",
        "operating_quality",
        "profitability_and_cost",
        "asset_quality_working_capital",
        "debt_liquidity",
        "cash_flow_quality",
        "valuation_expectation_gap",
        "risk_chain_scenario",
        "tracking_checklist",
    ],
    "business_strategy_researcher": [
        "operating_quality",
        "profitability_and_cost",
        "strategy_policy_external_risk",
        "tracking_checklist",
    ],
    "industry_peer_researcher": [
        "industry_competition",
        "strategy_policy_external_risk",
        "valuation_expectation_gap",
        "risk_chain_scenario",
    ],
    "governance_risk_researcher": [
        "debt_liquidity",
        "governance_compliance_shareholders",
        "risk_chain_scenario",
        "tracking_checklist",
        "data_quality_traceability",
    ],
}

CHECKPOINT_PURPOSES = {
    "preflight.json": "company and artifact readiness",
    "wiki_inventory.json": "local wiki file coverage",
    "metric_snapshot.json": "three-year financial metrics",
    "evidence_package.json": "traceable financial evidence",
    "analysis_outline.json": "core thesis, red flags, and derived metrics",
    "peer_metrics.json": "local peer comparison",
    "qualitative_snapshot.json": "semantic annual-report evidence",
    "market_snapshot.json": "price, market cap, and valuation anchors",
    "industry_research.json": "Hermes web-search industry evidence",
}

CORE_METRIC_KEYS = [
    "operating_revenue",
    "parent_net_profit",
    "deducted_parent_net_profit",
    "operating_cash_flow_net",
    "total_assets",
    "total_liabilities",
    "equity_attributable_parent",
    "monetary_capital",
    "inventory",
    "accounts_receivable",
]


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"_load_error": f"json_parse_failed:{exc.msg}:line_{exc.lineno}"}
    if isinstance(data, dict):
        return data
    return {"_load_error": "json_root_not_object"}


def dump_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def checkpoint_inputs(work_dir: Path, names: list[str]) -> list[dict[str, str]]:
    inputs: list[dict[str, str]] = []
    for name in names:
        path = work_dir / name
        status = "read" if path.exists() else "missing"
        notes = ""
        if path.exists():
            loaded = load_json_if_exists(path)
            notes = str(loaded.get("_load_error") or "")
            if notes:
                status = "parse_failed"
        inputs.append({
            "path": name,
            "status": status,
            "purpose": CHECKPOINT_PURPOSES.get(name, "checkpoint"),
            "notes": notes,
        })
    return inputs


def read_checkpoints(work_dir: Path) -> dict[str, dict[str, Any]]:
    return {
        name: load_json_if_exists(work_dir / name)
        for name in CHECKPOINT_PURPOSES
    }


def company_id_of(checkpoints: dict[str, dict[str, Any]]) -> str:
    for name in ["preflight.json", "metric_snapshot.json", "industry_research.json", "qualitative_snapshot.json"]:
        value = checkpoints.get(name, {}).get("company_id")
        if value:
            return str(value)
    return "unknown_company"


def metric_entry(snapshot: dict[str, Any], key: str) -> dict[str, Any]:
    metrics = snapshot.get("metrics")
    if not isinstance(metrics, dict):
        return {}
    value = metrics.get(key)
    return value if isinstance(value, dict) else {}


def metric_value(snapshot: dict[str, Any], key: str, year: int) -> Any:
    entry = metric_entry(snapshot, key)
    values = entry.get("values")
    if not isinstance(values, dict):
        return None
    return values.get(str(year))


def metric_ref(snapshot: dict[str, Any], key: str, year: int) -> dict[str, Any]:
    entry = metric_entry(snapshot, key)
    sources = entry.get("sources") if isinstance(entry.get("sources"), dict) else {}
    source = sources.get(str(year)) if isinstance(sources, dict) else {}
    if not isinstance(source, dict):
        source = {}
    ref: dict[str, Any] = {
        "evidence_id": f"metric:{key}:{year}",
        "source_file": str(source.get("file") or "metric_snapshot.json"),
    }
    for target, source_key in [
        ("pdf_page", "pdf_page"),
        ("table_index", "table_index"),
        ("md_line", "md_line"),
    ]:
        if source.get(source_key) is not None:
            ref[target] = source.get(source_key)
    return ref


def fmt_number(value: Any, unit: str | None = None) -> str:
    if value is None:
        return "未取得"
    if isinstance(value, (int, float)):
        if unit:
            return f"{value:.2f}{unit}"
        return f"{value:.2f}"
    return str(value)


def yoy_text(current: Any, previous: Any) -> str:
    if not isinstance(current, (int, float)) or not isinstance(previous, (int, float)) or previous == 0:
        return ""
    return f"，同比 {(current - previous) / abs(previous) * 100:.2f}%"


def metric_fact(snapshot: dict[str, Any], key: str, year: int) -> dict[str, Any] | None:
    entry = metric_entry(snapshot, key)
    if not entry:
        return None
    unit = str(entry.get("unit") or "")
    display = str(entry.get("display_name") or entry.get("canonical_name") or key)
    current = metric_value(snapshot, key, year)
    previous = metric_value(snapshot, key, year - 1)
    fact = f"{display} {year} 年为 {fmt_number(current, unit)}{yoy_text(current, previous)}。"
    return {
        "fact": fact,
        "period": str(year),
        "unit": unit,
        "scope": "financial_metric",
        "evidence_refs": [metric_ref(snapshot, key, year)],
    }


def metric_facts(snapshot: dict[str, Any], keys: list[str], year: int) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    for key in keys:
        fact = metric_fact(snapshot, key, year)
        if fact:
            facts.append(fact)
    return facts


def key_finding(section_ids: list[str], claim: str, confidence: float, source_file: str, rationale: str = "") -> dict[str, Any]:
    return {
        "section_ids": section_ids,
        "claim": claim,
        "confidence": confidence,
        "rationale": rationale,
        "evidence_refs": [{"source_file": source_file}],
    }


def calculation(name: str, formula: str, inputs: Any, output: Any, source_file: str) -> dict[str, Any]:
    return {
        "name": name,
        "formula": formula,
        "inputs": inputs,
        "output": output,
        "evidence_refs": [{"source_file": source_file}],
    }


def missing_input(name: str, reason: str, impact: str, section_ids: list[str], requested_action: str = "") -> dict[str, Any]:
    item = {
        "name": name,
        "reason": reason,
        "impact": impact,
        "section_ids": section_ids,
    }
    if requested_action:
        item["requested_action"] = requested_action
    return item


def risk_chain(items: list[str], section_ids: list[str], severity: str = "unknown") -> dict[str, Any]:
    chain = [str(item).strip() for item in items if str(item).strip()]
    if len(chain) < 2:
        chain = [chain[0], "需要补充财务或外部证据验证"] if chain else ["证据不足", "结论需降级表达"]
    return {
        "section_ids": section_ids,
        "chain": chain[:5],
        "severity": severity,
        "evidence_refs": [{"source_file": "analysis_outline.json"}],
        "counter_signals": [],
    }


def tracking_signal(text: str, section_ids: list[str], direction: str = "unknown", source_hint: str = "annual report") -> dict[str, Any]:
    return {
        "signal": text,
        "why_it_matters": "用于确认或推翻当前分析结论。",
        "direction": direction,
        "source_hint": source_hint,
        "section_ids": section_ids,
    }


def qualitative_items(qualitative: dict[str, Any], buckets: list[str], limit: int = 8) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    bucket_map = qualitative.get("buckets")
    if not isinstance(bucket_map, dict):
        return result
    for bucket in buckets:
        values = bucket_map.get(bucket)
        if not isinstance(values, list):
            continue
        for item in values:
            if isinstance(item, dict) and str(item.get("text") or "").strip():
                result.append(item)
            if len(result) >= limit:
                return result
    return result


def qualitative_fact(item: dict[str, Any], section_ids: list[str]) -> dict[str, Any]:
    refs = [
        {"evidence_id": str(eid), "source_file": "qualitative_snapshot.json"}
        for eid in item.get("evidence_ids", [])[:4]
        if str(eid).strip()
    ] or [{"source_file": "qualitative_snapshot.json"}]
    return {
        "fact": str(item.get("text") or "").strip(),
        "period": "",
        "unit": "",
        "scope": ",".join(section_ids),
        "evidence_refs": refs,
        "notes": str(item.get("kind") or ""),
    }


def external_sources(industry_research: dict[str, Any]) -> list[dict[str, Any]]:
    queries = industry_research.get("queries") if isinstance(industry_research.get("queries"), list) else []
    default_query = str(queries[0]) if queries else "industry research"
    sources: list[dict[str, Any]] = []
    results = industry_research.get("results")
    if not isinstance(results, list):
        return sources
    for index, item in enumerate(results[:12]):
        if not isinstance(item, dict):
            continue
        provider = str(item.get("provider") or "").strip()
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        if not provider or not title or not url:
            continue
        sources.append({
            "provider": provider,
            "query": str(item.get("query") or (queries[index % len(queries)] if queries else default_query)),
            "url": url,
            "title": title,
            "retrieved_at": str(industry_research.get("generated_at") or now_iso()),
            "summary": str(item.get("snippet") or "")[:500],
            "reliability": "unknown",
        })
    return sources


def base_pack(
    agent_id: str,
    checkpoints: dict[str, dict[str, Any]],
    year: int,
    input_names: list[str],
    source_scope: list[str],
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "agent_id": agent_id,
        "company_id": company_id_of(checkpoints),
        "report_year": year,
        "generated_at": now_iso(),
        "input_files": checkpoint_inputs(Path(checkpoints["_work_dir"]["path"]), input_names),
        "coverage": {
            "section_ids": AGENT_SECTION_IDS[agent_id],
            "time_periods": [str(year), str(year - 1), str(year - 2)],
            "source_scope": source_scope,
            "known_limits": [],
        },
        "key_findings": [],
        "evidence_facts": [],
        "calculations": [],
        "risk_chains": [],
        "tracking_signals": [],
        "external_sources": [],
        "missing_inputs": [],
        "review_required": False,
        "prohibited_content_hits": [],
    }


def build_evidence_curator(checkpoints: dict[str, dict[str, Any]], year: int) -> dict[str, Any]:
    snapshot = checkpoints["metric_snapshot.json"]
    inventory = checkpoints["wiki_inventory.json"]
    pack = base_pack(
        "evidence_curator",
        checkpoints,
        year,
        ["preflight.json", "wiki_inventory.json", "metric_snapshot.json", "evidence_package.json"],
        ["annual_report", "metrics", "evidence_index", "wiki_inventory"],
    )
    file_count = inventory.get("file_count")
    missing_required = inventory.get("missing_required_files") if isinstance(inventory.get("missing_required_files"), list) else []
    pack["key_findings"].append(key_finding(
        SECTION_IDS,
        f"本次报告已形成本地证据盘点，wiki_inventory file_count={file_count}，核心 metric_snapshot 与 evidence_package 可作为章节底稿来源。",
        0.86,
        "wiki_inventory.json",
        "用于保证 14 章都能回到本地证据或明确缺口。",
    ))
    pack["evidence_facts"] = metric_facts(snapshot, CORE_METRIC_KEYS[:6], year)
    if missing_required:
        pack["missing_inputs"].append(missing_input(
            "wiki_required_files",
            "wiki_inventory 标记部分必需文件缺失。",
            "相关章节必须保留证据缺口和审阅标记。",
            SECTION_IDS,
            "补齐缺失文件或在报告中降级表达。",
        ))
    for item in snapshot.get("missing_core_metrics", []) or []:
        pack["missing_inputs"].append(missing_input(
            str(item),
            "metric_snapshot.missing_core_metrics",
            "核心财务指标缺失会降低对应章节结论置信度。",
            ["executive_summary", "key_changes", "data_quality_traceability"],
        ))
    pack["risk_chains"].append(risk_chain(["证据缺口", "章节结论降级", "进入人工复核队列"], ["data_quality_traceability"], "medium"))
    pack["tracking_signals"].append(tracking_signal("证据包、引用页码和 metric_snapshot 一致性", ["data_quality_traceability"], "confirm", "pipeline validation"))
    pack["review_required"] = bool(pack["missing_inputs"])
    return pack


def build_financial_modeler(checkpoints: dict[str, dict[str, Any]], year: int) -> dict[str, Any]:
    snapshot = checkpoints["metric_snapshot.json"]
    outline = checkpoints["analysis_outline.json"]
    market = checkpoints["market_snapshot.json"]
    pack = base_pack(
        "financial_modeler",
        checkpoints,
        year,
        ["metric_snapshot.json", "evidence_package.json", "analysis_outline.json", "market_snapshot.json"],
        ["metrics", "derived_ratios", "valuation_anchors"],
    )
    core_judgment = str(outline.get("core_judgment") or "").strip()
    core_contradiction = str(outline.get("core_contradiction") or "").strip()
    if core_judgment:
        pack["key_findings"].append(key_finding(["executive_summary"], core_judgment, 0.82, "analysis_outline.json"))
    if core_contradiction:
        pack["key_findings"].append(key_finding(["executive_summary", "risk_chain_scenario"], core_contradiction, 0.8, "analysis_outline.json"))
    pack["evidence_facts"] = metric_facts(snapshot, CORE_METRIC_KEYS, year)
    derived = outline.get("calculated_derived_metrics")
    if isinstance(derived, dict):
        for name, value in derived.items():
            pack["calculations"].append(calculation(str(name), "pipeline derived metric", {"source": "metric_snapshot.json"}, value, "analysis_outline.json"))
    for flag in outline.get("red_flags", []) or []:
        pack["risk_chains"].append(risk_chain([str(flag), "影响盈利质量、现金流或资产负债表安全边际"], ["risk_chain_scenario"], "high"))
    for text in (outline.get("improvement_items", []) or [])[:4]:
        pack["tracking_signals"].append(tracking_signal(str(text), ["tracking_checklist"], "improve", "analysis_outline.json"))
    for text in (outline.get("falsifying_evidence", []) or [])[:4]:
        pack["tracking_signals"].append(tracking_signal(str(text), ["tracking_checklist", "risk_chain_scenario"], "falsify", "analysis_outline.json"))
    if market and not market.get("strict_ok"):
        pack["missing_inputs"].append(missing_input(
            "market_snapshot",
            "本地股价、市值或估值快照不足。",
            "估值与预期差章节只能使用年报锚和条件判断，不能给确定性估值结论。",
            ["valuation_expectation_gap"],
            "补齐交易行情或估值分位快照。",
        ))
    pack["review_required"] = bool(pack["missing_inputs"])
    return pack


def build_business_strategy_researcher(checkpoints: dict[str, dict[str, Any]], year: int) -> dict[str, Any]:
    qualitative = checkpoints["qualitative_snapshot.json"]
    pack = base_pack(
        "business_strategy_researcher",
        checkpoints,
        year,
        ["preflight.json", "wiki_inventory.json", "qualitative_snapshot.json"],
        ["semantic", "annual_report_business_profile", "strategy_claims"],
    )
    items = qualitative_items(qualitative, ["strategy", "product_brand", "operation_driver", "rd_technology"], 12)
    for item in items[:6]:
        pack["key_findings"].append(key_finding(
            AGENT_SECTION_IDS["business_strategy_researcher"],
            str(item.get("text") or "").strip(),
            0.72,
            "qualitative_snapshot.json",
            str(item.get("kind") or ""),
        ))
    pack["evidence_facts"] = [qualitative_fact(item, AGENT_SECTION_IDS["business_strategy_researcher"]) for item in items[:10]]
    for item in qualitative_items(qualitative, ["external_risk"], 4):
        pack["risk_chains"].append(risk_chain([str(item.get("text") or ""), "映射到收入、毛利率、费用率和现金流验证"], ["strategy_policy_external_risk"], "medium"))
    for item in items[:5]:
        pack["tracking_signals"].append(tracking_signal(str(item.get("text") or "")[:180], ["tracking_checklist"], "confirm", "qualitative_snapshot.json"))
    if not qualitative.get("strict_ok", True):
        pack["missing_inputs"].append(missing_input(
            "qualitative_snapshot_strict_ok",
            "定性语义证据未达到严格通过状态。",
            "业务战略章节需要保留人工复核或外部补证。",
            ["strategy_policy_external_risk", "tracking_checklist"],
        ))
    pack["review_required"] = bool(pack["missing_inputs"])
    return pack


def build_industry_peer_researcher(checkpoints: dict[str, dict[str, Any]], year: int) -> dict[str, Any]:
    peer_metrics = checkpoints["peer_metrics.json"]
    industry_research = checkpoints["industry_research.json"]
    pack = base_pack(
        "industry_peer_researcher",
        checkpoints,
        year,
        ["peer_metrics.json", "industry_research.json", "market_snapshot.json", "qualitative_snapshot.json"],
        ["peer_metrics", "external_search", "industry_trends"],
    )
    for text in peer_metrics.get("interpretation", []) or []:
        pack["key_findings"].append(key_finding(["industry_competition"], str(text), 0.76, "peer_metrics.json"))
    for text in industry_research.get("interpretation", []) or []:
        pack["key_findings"].append(key_finding(["industry_competition", "strategy_policy_external_risk"], str(text), 0.64, "industry_research.json"))
    peer_aggregates = peer_metrics.get("aggregates")
    if not peer_aggregates:
        peer_aggregates = {
            "status": "missing",
            "reason": "peer_metrics.aggregates unavailable",
            "fallback": "industry peer section must downgrade to directional discussion",
        }
    pack["calculations"].append(calculation(
        "peer_metrics_aggregates",
        "peer_metrics_builder.py aggregate output",
        {"selection_method": peer_metrics.get("selection_method"), "peer_count": peer_metrics.get("peer_count")},
        peer_aggregates,
        "peer_metrics.json",
    ))
    pack["external_sources"] = external_sources(industry_research)
    for warning in peer_metrics.get("peer_selection_warnings", peer_metrics.get("warnings", [])) or []:
        pack["missing_inputs"].append(missing_input(
            "peer_selection",
            str(warning),
            "同业比较样本需降级表达，避免把宽口径样本当作严格同业。",
            ["industry_competition"],
            "补齐同行业公司或重新运行 peer_metrics_builder.py。",
        ))
    if not peer_metrics.get("strict_ok", False):
        pack["missing_inputs"].append(missing_input(
            "peer_metrics_strict_ok",
            "同业样本未达到严格通过状态。",
            "行业竞争位置只能方向性表达。",
            ["industry_competition"],
        ))
    if not industry_research.get("strict_ok", False):
        pack["missing_inputs"].append(missing_input(
            "industry_peer_external_sources",
            "外部行业检索未达到严格通过状态。",
            "行业趋势和政策变量必须标注待核验。",
            ["industry_competition", "strategy_policy_external_risk"],
            "优先补 Hermes Tavily/EXA 的权威行业来源。",
        ))
    for text in (industry_research.get("warnings", []) or [])[:4]:
        pack["risk_chains"].append(risk_chain([str(text), "行业结论置信度下降"], ["industry_competition"], "medium"))
    pack["tracking_signals"].append(tracking_signal("同业样本行业路径、peer_count、strict_ok", ["industry_competition"], "confirm", "peer_metrics.json"))
    pack["review_required"] = bool(pack["missing_inputs"])
    return pack


def build_governance_risk_researcher(checkpoints: dict[str, dict[str, Any]], year: int) -> dict[str, Any]:
    qualitative = checkpoints["qualitative_snapshot.json"]
    outline = checkpoints["analysis_outline.json"]
    pack = base_pack(
        "governance_risk_researcher",
        checkpoints,
        year,
        ["qualitative_snapshot.json", "analysis_outline.json", "evidence_package.json", "preflight.json"],
        ["semantic_governance", "risk_flags", "traceability"],
    )
    governance_items = qualitative_items(qualitative, ["governance", "external_risk"], 10)
    for item in governance_items[:6]:
        pack["key_findings"].append(key_finding(
            AGENT_SECTION_IDS["governance_risk_researcher"],
            str(item.get("text") or "").strip(),
            0.7,
            "qualitative_snapshot.json",
            str(item.get("kind") or ""),
        ))
    pack["evidence_facts"] = [qualitative_fact(item, AGENT_SECTION_IDS["governance_risk_researcher"]) for item in governance_items[:8]]
    for flag in outline.get("red_flags", []) or []:
        pack["risk_chains"].append(risk_chain([str(flag), "触发财务安全边际或治理复核要求"], ["risk_chain_scenario"], "high"))
    for item in governance_items[:4]:
        pack["tracking_signals"].append(tracking_signal(str(item.get("text") or "")[:180], ["tracking_checklist"], "confirm", "qualitative_snapshot.json"))
    if not governance_items:
        pack["missing_inputs"].append(missing_input(
            "governance_semantic_evidence",
            "qualitative_snapshot 未提供治理或外部风险证据。",
            "治理合规章节需人工补证或保留空白降级。",
            ["governance_compliance_shareholders"],
        ))
    pack["review_required"] = bool(pack["missing_inputs"])
    return pack


BUILDERS = {
    "evidence_curator": build_evidence_curator,
    "financial_modeler": build_financial_modeler,
    "business_strategy_researcher": build_business_strategy_researcher,
    "industry_peer_researcher": build_industry_peer_researcher,
    "governance_risk_researcher": build_governance_risk_researcher,
}


def build_manifest(work_dir: Path, output_dir: Path, packs: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "generated_by": "generate_research_packs.py",
        "generated_at": now_iso(),
        "work_dir": str(work_dir),
        "research_packs_dir": str(output_dir),
        "implementation_stage": "deterministic_checkpoint_scaffold",
        "agent_ids": [pack["agent_id"] for pack in packs],
        "pack_files": {
            pack["agent_id"]: str(output_dir / f"{pack['agent_id']}.json")
            for pack in packs
        },
        "review_required_agent_ids": [pack["agent_id"] for pack in packs if pack.get("review_required")],
        "missing_input_count": sum(len(pack.get("missing_inputs", [])) for pack in packs),
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate siq_analysis research_packs from report checkpoints.")
    parser.add_argument("--work-dir", required=True, type=Path)
    parser.add_argument("--year", type=int, default=2025)
    parser.add_argument("--output-dir", type=Path, help="Default: <work-dir>/research_packs")
    parser.add_argument("--write-manifest", type=Path, help="Default: <work-dir>/research_pack_manifest.json")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    work_dir = args.work_dir
    output_dir = args.output_dir or work_dir / "research_packs"
    manifest_path = args.write_manifest or work_dir / "research_pack_manifest.json"
    checkpoints = read_checkpoints(work_dir)
    checkpoints["_work_dir"] = {"path": str(work_dir)}

    packs: list[dict[str, Any]] = []
    failures: list[str] = []
    for agent_id in RESEARCH_AGENT_IDS:
        try:
            pack = BUILDERS[agent_id](checkpoints, args.year)
        except Exception as exc:  # pragma: no cover - defensive operational guard
            failures.append(f"{agent_id}:build_failed:{exc}")
            continue
        output_path = output_dir / f"{agent_id}.json"
        dump_json(output_path, pack)
        packs.append(pack)

    manifest = build_manifest(work_dir, output_dir, packs)
    dump_json(manifest_path, manifest)
    result = {
        "ok": not failures and len(packs) == len(RESEARCH_AGENT_IDS),
        "stage": "completed" if not failures else "build_failed",
        "work_dir": str(work_dir),
        "research_packs_dir": str(output_dir),
        "manifest": str(manifest_path),
        "pack_count": len(packs),
        "failures": failures,
        "manifest_summary": manifest,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
