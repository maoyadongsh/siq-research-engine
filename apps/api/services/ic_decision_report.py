"""SIQ-native R4 decision report generation."""

from __future__ import annotations

import json
from html import escape
from pathlib import Path
from typing import Any

from services import deal_disputes
from services import deal_store
from services import ic_scoring


R4_DECISION_SCHEMA = "siq_ic_r4_decision_v1"
R4_MARKDOWN_PATH = "decision/IC_DECISION_REPORT.md"
R4_HTML_PATH = "decision/IC_DECISION_REPORT.html"
R4_PAYLOAD_PATH = "decision/decision_payload.json"


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _dedupe_strings(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _string_items(value: Any) -> list[str]:
    if isinstance(value, list):
        return _dedupe_strings(value)
    if value in (None, ""):
        return []
    return _dedupe_strings([value])


def _markdown_list(values: list[Any], *, empty: str = "暂无") -> str:
    items = _dedupe_strings(values)
    if not items:
        return f"- {empty}"
    return "\n".join(f"- {item}" for item in items)


def _canonical_keyed_payload(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    payload: dict[str, dict[str, Any]] = {}
    for key, item in value.items():
        if not isinstance(item, dict):
            continue
        agent_id = str(item.get("agent_id") or key)
        normalized = dict(item)
        normalized["agent_id"] = agent_id
        payload[agent_id] = normalized
    return payload


def _qualitative_decision(threshold: str) -> str:
    if threshold == "pass":
        return "建议投资，但需设置估值、退出和关键客户验证保护条款"
    if threshold == "review":
        return "建议复核后再议，需补齐关键证据和条款保护"
    return "暂缓投资，待核心风险和证据缺口关闭后重新提交"


def _r1_5_summary(deal_id: str, *, wiki_root: Path | str | None = None) -> dict[str, Any]:
    try:
        return deal_disputes.summarize_deal_disputes(deal_id, wiki_root=wiki_root)
    except FileNotFoundError:
        raise
    except ValueError:
        return {}


def _r3_payload(package_dir: Path) -> dict[str, Any]:
    raw = deal_store.read_json(package_dir / "phases" / "r3_reports.json", {}) or {}
    return raw if isinstance(raw, dict) else {}


def decision_conditions(plan: dict[str, Any]) -> list[str]:
    conditions: list[Any] = []
    disputes = _r1_5_summary(plan["deal_id"], wiki_root=plan.get("wiki_root"))
    for dispute in disputes.get("disputes", []) if isinstance(disputes.get("disputes"), list) else []:
        if not isinstance(dispute, dict):
            continue
        conditions.extend(_string_items(dispute.get("required_followups")))
        ruling = dispute.get("chairman_ruling") if isinstance(dispute.get("chairman_ruling"), dict) else {}
        conditions.extend(_string_items(ruling.get("required_followups")))

    r3_reports = _canonical_keyed_payload(_r3_payload(plan["package_dir"]).get("reports") or {})
    for report in r3_reports.values():
        conditions.extend(_string_items(report.get("challenges")))
    for report in plan["r2_reports"].values():
        conditions.extend(_string_items(report.get("open_questions")))
        conditions.extend(_string_items(report.get("risk_flags")))

    values = _dedupe_strings(conditions)
    return values[:12] or ["投委会人工确认后方可进入投资执行流程"]


def monitoring_metrics(plan: dict[str, Any]) -> list[str]:
    del plan
    return [
        "核心客户续约和收入确认质量",
        "现金流、毛利率和估值敏感性",
        "重大合同、知识产权、诉讼和资质状态",
        "供应链、舆情和黑天鹅风险",
    ]


def build_r4_decision_payload(
    plan: dict[str, Any],
    *,
    created_by: dict[str, Any] | None = None,
) -> dict[str, Any]:
    scoring = ic_scoring.calculate_weighted_agent_score(
        policy=plan["policy"],
        r1_reports=plan["r1_reports"],
        r2_reports=plan["r2_reports"],
    )
    weighted_score = scoring.get("weighted_agent_score")
    chairman_score = ic_scoring.round_score(ic_scoring.numeric(plan.get("chairman_score")))
    if weighted_score is None:
        raise ValueError("R4 finalize blocked: weighted_agent_score_unavailable")
    if chairman_score is None:
        raise ValueError("R4 finalize blocked: chairman_dimension_score_unavailable")

    final_score = chairman_score
    threshold = ic_scoring.threshold_result(final_score, plan["policy"])
    now = deal_store.utc_now_iso()
    return {
        "schema_version": R4_DECISION_SCHEMA,
        "deal_id": plan["deal_id"],
        "decision": threshold,
        "final_score": final_score,
        "weighted_agent_score": weighted_score,
        "chairman_dimension_score": chairman_score,
        "chairman_qualitative_decision": _qualitative_decision(threshold),
        "threshold_result": threshold,
        "source_ids": plan.get("evidence_identity", {}).get("source_ids") or [],
        "evidence_snapshot_hash": plan.get("evidence_identity", {}).get("evidence_snapshot_hash"),
        "active_sources": plan.get("evidence_identity", {}).get("active_sources") or [],
        "conditions": decision_conditions(plan),
        "monitoring_metrics": monitoring_metrics(plan),
        "human_confirmation": {
            "status": "pending",
            "confirmed_by": None,
            "confirmed_at": None,
            "override_reason": None,
        },
        "artifact_paths": {
            "markdown": R4_MARKDOWN_PATH,
            "html": R4_HTML_PATH,
        },
        "scoring_inputs": {
            "weighted_agent_score": scoring.get("inputs") or [],
            "chairman_dimension_source": "siq_ic_chairman.r1_report_score",
            "warnings": scoring.get("warnings") or [],
            "scoring_contract": scoring,
        },
        "generation_mode": "deterministic_siq_r4_finalize_v1",
        "created_by": created_by,
        "created_at": now,
        "updated_at": now,
    }


def render_r4_markdown(decision: dict[str, Any]) -> str:
    scoring_inputs = decision.get("scoring_inputs") if isinstance(decision.get("scoring_inputs"), dict) else {}
    weighted_inputs = _as_list(scoring_inputs.get("weighted_agent_score"))
    scoring_lines = []
    for item in weighted_inputs:
        if not isinstance(item, dict):
            continue
        scoring_lines.append(
            "- {role} / `{agent_id}`: weight `{weight}`, score `{score}`, source `{source}`".format(
                role=item.get("role"),
                agent_id=item.get("agent_id"),
                weight=item.get("weight"),
                score=item.get("score"),
                source=item.get("source_round"),
            )
        )

    lines = [
        "# IC Decision Report",
        "",
        "## Conclusion",
        "",
        f"- Decision: `{decision.get('decision')}`",
        f"- Final score: `{decision.get('final_score')}`",
        f"- Chairman qualitative decision: {decision.get('chairman_qualitative_decision')}",
        "",
        "## Evidence sufficiency",
        "",
        f"- Weighted agent score: `{decision.get('weighted_agent_score')}`",
        f"- Chairman dimension score: `{decision.get('chairman_dimension_score')}`",
        f"- Threshold result: `{decision.get('threshold_result')}`",
        "",
        "## Scoring inputs",
        "",
        _markdown_list(scoring_lines, empty="No weighted scoring inputs available."),
        "",
        "## Key verified facts",
        "",
        "- See R1/R2 expert reports and deal evidence package for source-linked facts.",
        "",
        "## Key unverified assumptions",
        "",
        "- See R2 open questions and R3 challenge items.",
        "",
        "## Core disagreements and chairman ruling",
        "",
        "- See `phases/r1_5_disputes.json` and `discussion/02_R1.5_裁决记录.md`.",
        "",
        "## Investment conditions and post-investment monitoring metrics",
        "",
        "### Conditions",
        "",
        _markdown_list(decision.get("conditions") if isinstance(decision.get("conditions"), list) else []),
        "",
        "### Monitoring Metrics",
        "",
        _markdown_list(decision.get("monitoring_metrics") if isinstance(decision.get("monitoring_metrics"), list) else []),
        "",
        "## Human Confirmation",
        "",
        f"- Status: `{(decision.get('human_confirmation') or {}).get('status')}`",
    ]
    return "\n".join(lines)


def render_r4_html(markdown: str) -> str:
    body = "\n".join(
        f"<p>{escape(line)}</p>" if line.strip() else ""
        for line in markdown.splitlines()
    )
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        "<title>IC Decision Report</title></head><body>"
        f"{body}</body></html>"
    )


def write_r4_decision_artifacts(package_dir: Path, decision: dict[str, Any]) -> dict[str, str]:
    markdown = render_r4_markdown(decision)
    markdown_path = package_dir / R4_MARKDOWN_PATH
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(markdown.rstrip() + "\n", encoding="utf-8")

    html_path = package_dir / R4_HTML_PATH
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(render_r4_html(markdown).rstrip() + "\n", encoding="utf-8")

    payload_path = package_dir / R4_PAYLOAD_PATH
    payload_path.parent.mkdir(parents=True, exist_ok=True)
    payload_path.write_text(json.dumps(decision, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return {
        "markdown": R4_MARKDOWN_PATH,
        "html": R4_HTML_PATH,
        "decision_payload": R4_PAYLOAD_PATH,
    }
