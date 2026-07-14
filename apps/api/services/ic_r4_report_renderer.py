"""Deterministic R4 view model plus Markdown/HTML rendering."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from html import escape
from typing import Any, Mapping, Sequence

IC_R4_REPORT_VIEW_MODEL_SCHEMA = "siq_ic_r4_report_view_model_v1"
IC_R4_RENDERED_REPORT_SCHEMA = "siq_ic_r4_rendered_report_v1"

R4_SECTION_TITLES = (
    "执行摘要与决策结论",
    "项目、轮次、拟投资结构与证据快照",
    "证据充分度、数据限制与未验证事项",
    "企业、产品与商业模式概况",
    "战略与政策分析",
    "行业、市场规模、竞争与技术分析",
    "历史财务、收入质量、现金流、预测与估值",
    "法律、股权、知识产权、合规与交割风险",
    "风险登记、压力测试、预警与止损指标",
    "R1.5 核心分歧与主席裁决",
    "R2 观点变化与评分变化",
    "R3 红蓝对抗与最终裁定",
    "专家加权评分与主席六维评分",
    "最终建议、前置条件、TS 保护条款与投后监控",
    "开放问题、人工确认与审计摘要",
)

INTERNAL_PATH_RE = re.compile(r"/(?:home|Users|var/lib|srv)/[^\s`<]+|[A-Za-z]:\\[^\s`<]+")
CONTROL_TEXT_RE = re.compile(r"(?:system prompt|gateway_url|HERMES_GATEWAY|内部提示词)", re.IGNORECASE)


def _sanitize_text(value: Any) -> str:
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    else:
        text = str(value or "").strip()
    text = INTERNAL_PATH_RE.sub("[内部位置已隐藏]", text)
    text = CONTROL_TEXT_RE.sub("[内部控制信息已隐藏]", text)
    return text


def _items(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value in (None, "", {}):
        return []
    return [value]


def _meaningful_lines(value: Any, *, empty_reason: str) -> list[str]:
    values = [_sanitize_text(item) for item in _items(value)]
    values = [item for item in values if item]
    return values or [f"证据不足：{empty_reason}"]


def _report_candidates(value: Any) -> list[tuple[dict[str, Any], Mapping[str, Any]]]:
    candidates: list[tuple[dict[str, Any], Mapping[str, Any]]] = []
    if isinstance(value, Mapping):
        nested_report = value.get("report")
        if isinstance(nested_report, Mapping):
            report = dict(nested_report)
            for key in (
                "r1_score",
                "r2_score",
                "score_change",
                "revision_rationale",
                "changed_claims",
                "unchanged_claims",
                "remaining_questions",
            ):
                if key not in report and key in value:
                    report[key] = value[key]
            candidates.append((report, value))
        elif value.get("agent_id"):
            candidates.append((dict(value), value))
        else:
            for item in value.values():
                candidates.extend(_report_candidates(item))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for item in value:
            candidates.extend(_report_candidates(item))
    return candidates


def _revision_rank(report: Mapping[str, Any], envelope: Mapping[str, Any], order: int) -> tuple[int, float, int]:
    try:
        revision = int(report.get("revision") or envelope.get("revision") or 0)
    except (TypeError, ValueError):
        revision = 0
    created_at = str(report.get("created_at") or envelope.get("created_at") or "")
    try:
        timestamp = datetime.fromisoformat(created_at.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        timestamp = 0.0
    return revision, timestamp, order


def _reports_by_agent(value: Any) -> dict[str, Mapping[str, Any]]:
    reports: dict[str, Mapping[str, Any]] = {}
    ranks: dict[str, tuple[int, float, int]] = {}
    for order, (report, envelope) in enumerate(_report_candidates(value)):
        agent_id = str(report.get("agent_id") or envelope.get("agent_id") or "")
        if not agent_id:
            continue
        rank = _revision_rank(report, envelope, order)
        if agent_id not in ranks or rank >= ranks[agent_id]:
            reports[agent_id] = report
            ranks[agent_id] = rank
    return reports


def _claim_lines(report: Mapping[str, Any], *, limit: int = 8) -> list[str]:
    lines = []
    for claim in report.get("claims", []):
        if not isinstance(claim, Mapping):
            continue
        evidence = ", ".join(f"[{item}]" for item in claim.get("evidence_ids", [])) or "无项目 Evidence"
        background = ", ".join(f"[{item}]" for item in claim.get("background_knowledge_ref_ids", []))
        suffix = f"；背景参考 {background}" if background else ""
        lines.append(
            f"{claim.get('claim_id')} | {claim.get('status')} | {_sanitize_text(claim.get('conclusion'))} | "
            f"项目证据 {evidence}{suffix}"
        )
        if len(lines) >= limit:
            break
    return lines


def _report_lines(report: Mapping[str, Any] | None, *, label: str) -> list[str]:
    if not report:
        return [f"证据不足：未提供{label}正式结构化报告"]
    lines = [
        f"{label}采用版本：{report.get('phase') or report.get('round_name') or 'unknown'} / revision "
        f"{report.get('revision') or 'unknown'}",
        f"{label}建议：{report.get('recommendation')}",
        f"{label}评分：{report.get('score')}",
        f"{label}摘要：{_sanitize_text(report.get('executive_summary'))}",
    ]
    lines.extend(_claim_lines(report))
    return lines


def _background_reference_lines(reports: Sequence[Mapping[str, Any]]) -> list[str]:
    lines = []
    for report in reports:
        for ref in [*report.get("background_knowledge_refs", []), *report.get("methodology_refs", [])]:
            if not isinstance(ref, Mapping):
                continue
            lines.append(
                f"{report.get('agent_id')} | {ref.get('ref_id')} | {ref.get('usage')} | "
                f"{_sanitize_text(ref.get('title'))}"
            )
    return lines or ["证据不足：本轮没有可用的角色专属背景知识或方法论引用"]


def _dispute_lines(disputes: Any) -> list[str]:
    lines = []
    for item in _items(disputes):
        if not isinstance(item, Mapping):
            lines.append(_sanitize_text(item))
            continue
        lines.append(
            f"{item.get('dispute_id')} | {item.get('severity')} | {_sanitize_text(item.get('question'))} | "
            f"裁决 {item.get('ruling') or item.get('status')} | {_sanitize_text(item.get('rationale'))}"
        )
    return lines or ["证据不足：未提供 R1.5 分歧与主席裁决记录"]


def _r2_lines(revisions: Any) -> list[str]:
    lines = []
    candidates = revisions.values() if isinstance(revisions, Mapping) else _items(revisions)
    for item in candidates:
        if not isinstance(item, Mapping):
            continue
        report = item.get("report") if isinstance(item.get("report"), Mapping) else item
        lines.append(
            f"{report.get('agent_id')} | R1 {item.get('r1_score')} -> R2 {item.get('r2_score')} "
            f"({item.get('score_change')}) | {_sanitize_text(item.get('revision_rationale'))}"
        )
    return lines or ["证据不足：未提供 R2 观点与评分修订记录"]


def _clean_lines(value: Any) -> list[str]:
    return [text for text in (_sanitize_text(item) for item in _items(value)) if text]


def _capability_restrictions(active_sources: Any) -> list[str]:
    restrictions: list[str] = []
    for source in _items(active_sources):
        if not isinstance(source, Mapping):
            continue
        source_id = _sanitize_text(source.get("source_id")) or "unknown_source"
        capabilities = source.get("capabilities") if isinstance(source.get("capabilities"), Mapping) else {}
        for capability, status in capabilities.items():
            if str(status or "") != "ready":
                restrictions.append(f"{source_id} | {capability}={_sanitize_text(status)}")
    return restrictions


def _r0_context(
    *,
    readiness: Mapping[str, Any],
    materials: Mapping[str, Any],
    evidence_quality: Mapping[str, Any],
    evidence_snapshot: Mapping[str, Any],
    decision: Mapping[str, Any],
) -> dict[str, Any]:
    active_sources = evidence_snapshot.get("active_sources") or decision.get("active_sources") or []
    material_limitations = [
        *_clean_lines(materials.get("blocking_reasons")),
        *_clean_lines(materials.get("limitations")),
    ]
    evidence_limitations = [
        *_clean_lines(evidence_quality.get("limitations")),
        *_clean_lines(evidence_quality.get("warnings")),
        *_clean_lines(evidence_quality.get("errors")),
    ]
    return {
        "r0_available": bool(readiness),
        "readiness": _sanitize_text(readiness.get("readiness")),
        "evidence_snapshot_hash": _sanitize_text(readiness.get("evidence_snapshot_hash")),
        "blocking_reasons": _clean_lines(readiness.get("blocking_reasons")),
        "evidence_gaps": _clean_lines(readiness.get("evidence_gaps")),
        "material_completeness": _sanitize_text(readiness.get("material_completeness")),
        "materials_available": bool(materials),
        "materials_status": _sanitize_text(materials.get("status")),
        "materials_completeness": _sanitize_text(materials.get("completeness")),
        "material_limitations": material_limitations,
        "evidence_quality_available": bool(evidence_quality),
        "evidence_quality_status": _sanitize_text(evidence_quality.get("status")),
        "missing_dimensions": _clean_lines(evidence_quality.get("missing_dimensions")),
        "evidence_limitations": evidence_limitations,
        "source_capability_restrictions": _capability_restrictions(active_sources),
    }


def _context_value(label: str, value: Any, *, available: bool, missing_reason: str) -> str:
    if not available:
        return f"{label}：证据不足：{missing_reason}"
    text = _sanitize_text(value)
    return f"{label}：{text or '无'}"


def _context_lines(
    label: str,
    values: Any,
    *,
    available: bool,
    missing_reason: str,
) -> list[str]:
    if not available:
        return [f"{label}：证据不足：{missing_reason}"]
    lines = _clean_lines(values)
    return [f"{label}：{item}" for item in lines] or [f"{label}：无"]


def _r3_lines(debates: Any) -> list[str]:
    lines = []
    for debate in _items(debates):
        if not isinstance(debate, Mapping):
            continue
        lines.append(
            f"{debate.get('debate_id')} | {_sanitize_text(debate.get('topic'))} | 状态 {debate.get('status')} | "
            f"主席裁定 {_sanitize_text(debate.get('chairman_verdict'))}"
        )
        for turn in debate.get("rounds", []):
            if isinstance(turn, Mapping):
                lines.append(
                    f"第 {turn.get('round')} 轮 {turn.get('speaker')}：{_sanitize_text(turn.get('argument'))}"
                )
    return lines or ["证据不足：未提供 R3 对抗记录或结构化 skip 结论"]


def _score_lines(decision: Mapping[str, Any]) -> list[str]:
    lines = [
        f"专家加权评分：{decision.get('weighted_agent_score')}",
        f"主席六维评分：{decision.get('chairman_dimension_score')}",
        f"分差解释：{_sanitize_text(decision.get('score_delta_explanation')) or '证据不足：未提供分差解释'}",
    ]
    for item in decision.get("six_dimension_scorecard", []):
        if isinstance(item, Mapping):
            evidence = ", ".join(f"[{value}]" for value in item.get("evidence_ids", []))
            lines.append(
                f"{item.get('dimension')}：{item.get('score')}，权重 {item.get('weight')}，"
                f"依据 {_sanitize_text(item.get('rationale'))}，证据 {evidence}"
            )
    return lines


def _section(title: str, lines: Sequence[Any]) -> dict[str, Any]:
    normalized = [_sanitize_text(item) for item in lines if _sanitize_text(item)]
    return {"title": title, "lines": normalized or ["证据不足：本章节无可验证内容"]}


def build_r4_report_view_model(bundle: Mapping[str, Any]) -> dict[str, Any]:
    decision = bundle.get("decision") if isinstance(bundle.get("decision"), Mapping) else {}
    r1_reports = _reports_by_agent(bundle.get("r1_reports"))
    r2_reports = _reports_by_agent([bundle.get("r2_reports"), bundle.get("r2_revisions")])
    selected_reports = {**r1_reports, **r2_reports}
    project = bundle.get("project") if isinstance(bundle.get("project"), Mapping) else {}
    evidence_quality = bundle.get("evidence_quality") if isinstance(bundle.get("evidence_quality"), Mapping) else {}
    r0_readiness = bundle.get("r0_readiness") if isinstance(bundle.get("r0_readiness"), Mapping) else {}
    materials = bundle.get("materials_manifest") if isinstance(bundle.get("materials_manifest"), Mapping) else {}
    evidence_snapshot = bundle.get("evidence_snapshot") if isinstance(bundle.get("evidence_snapshot"), Mapping) else {}
    factcheck = bundle.get("factcheck") if isinstance(bundle.get("factcheck"), Mapping) else {}
    all_reports = list(selected_reports.values())
    r0 = _r0_context(
        readiness=r0_readiness,
        materials=materials,
        evidence_quality=evidence_quality,
        evidence_snapshot=evidence_snapshot,
        decision=decision,
    )

    strategist = selected_reports.get("siq_ic_strategist")
    sector = selected_reports.get("siq_ic_sector_expert")
    finance = selected_reports.get("siq_ic_finance_auditor")
    legal = selected_reports.get("siq_ic_legal_scanner")
    risk = selected_reports.get("siq_ic_risk_controller")
    report_selection = {
        agent_id: {
            "source_phase": "R2" if agent_id in r2_reports else "R1",
            "report_id": report.get("report_id"),
            "phase": report.get("phase"),
            "revision": report.get("revision"),
        }
        for agent_id, report in selected_reports.items()
    }

    sections = [
        _section(
            R4_SECTION_TITLES[0],
            [
                f"最终决策：{decision.get('decision')}",
                f"主席定性判断：{_sanitize_text(decision.get('chairman_qualitative_decision'))}",
                f"建议：{decision.get('recommendation')}",
                _context_value(
                    "R0 准入状态",
                    r0.get("readiness"),
                    available=bool(r0.get("r0_available")),
                    missing_reason="未提供 R0 readiness",
                ),
            ],
        ),
        _section(
            R4_SECTION_TITLES[1],
            [
                f"项目：{_sanitize_text(project.get('company_name') or project.get('name'))}",
                f"轮次：{_sanitize_text(project.get('round') or project.get('stage'))}",
                f"拟投资结构：{_sanitize_text(project.get('investment_structure')) or '证据不足：未提供拟投资结构'}",
                f"Deal ID：{decision.get('deal_id')}",
                f"Evidence Snapshot：{decision.get('evidence_snapshot_hash')}",
            ],
        ),
        _section(
            R4_SECTION_TITLES[2],
            [
                f"Evidence 质量：{_sanitize_text(evidence_quality.get('status')) or '证据不足：未提供 Evidence 质量结论'}",
                _context_value(
                    "R0 材料完整性",
                    r0.get("material_completeness"),
                    available=bool(r0.get("r0_available")),
                    missing_reason="未提供 R0 材料完整性结论",
                ),
                _context_value(
                    "材料中心状态",
                    r0.get("materials_status"),
                    available=bool(r0.get("materials_available")),
                    missing_reason="未提供一级市场材料清单",
                ),
                _context_value(
                    "材料中心完整性",
                    r0.get("materials_completeness"),
                    available=bool(r0.get("materials_available")),
                    missing_reason="未提供材料完整性明细",
                ),
                *_context_lines(
                    "R0 阻断原因",
                    r0.get("blocking_reasons"),
                    available=bool(r0.get("r0_available")),
                    missing_reason="未提供 R0 blocking reasons",
                ),
                *_context_lines(
                    "R0 Evidence 缺口",
                    r0.get("evidence_gaps"),
                    available=bool(r0.get("r0_available")),
                    missing_reason="未提供 R0 Evidence gap 清单",
                ),
                *_context_lines(
                    "材料限制",
                    r0.get("material_limitations"),
                    available=bool(r0.get("materials_available")),
                    missing_reason="未提供材料限制清单",
                ),
                *_context_lines(
                    "Evidence 缺失维度",
                    r0.get("missing_dimensions"),
                    available=bool(r0.get("evidence_quality_available")),
                    missing_reason="未提供 Evidence 维度覆盖",
                ),
                *_context_lines(
                    "Evidence 限制",
                    r0.get("evidence_limitations"),
                    available=bool(r0.get("evidence_quality_available")),
                    missing_reason="未提供 Evidence 质量限制",
                ),
                *_context_lines(
                    "来源能力限制",
                    r0.get("source_capability_restrictions"),
                    available=bool(evidence_snapshot or decision.get("active_sources")),
                    missing_reason="未提供 Evidence snapshot active sources",
                ),
                *_background_reference_lines(all_reports),
            ],
        ),
        _section(
            R4_SECTION_TITLES[3],
            [
                _sanitize_text(project.get("company_overview")) or "证据不足：未提供企业概况",
                _sanitize_text(project.get("product_overview")) or "证据不足：未提供产品概况",
                _sanitize_text(project.get("business_model")) or "证据不足：未提供商业模式概况",
            ],
        ),
        _section(R4_SECTION_TITLES[4], _report_lines(strategist, label="战略委员")),
        _section(R4_SECTION_TITLES[5], _report_lines(sector, label="行业委员")),
        _section(R4_SECTION_TITLES[6], _report_lines(finance, label="财务委员")),
        _section(R4_SECTION_TITLES[7], _report_lines(legal, label="法务委员")),
        _section(R4_SECTION_TITLES[8], _report_lines(risk, label="风控委员")),
        _section(R4_SECTION_TITLES[9], _dispute_lines(bundle.get("r1_5_disputes"))),
        _section(R4_SECTION_TITLES[10], _r2_lines(r2_reports)),
        _section(R4_SECTION_TITLES[11], _r3_lines(bundle.get("r3_debates") or bundle.get("r3"))),
        _section(R4_SECTION_TITLES[12], _score_lines(decision)),
        _section(
            R4_SECTION_TITLES[13],
            [
                *_meaningful_lines(decision.get("conditions"), empty_reason="未提供投资前置条件"),
                *_meaningful_lines(decision.get("term_sheet_protections"), empty_reason="未提供 TS 保护条款"),
                *_meaningful_lines(decision.get("monitoring_metrics"), empty_reason="未提供投后监控指标"),
            ],
        ),
        _section(
            R4_SECTION_TITLES[14],
            [
                *_meaningful_lines(bundle.get("open_questions"), empty_reason="当前没有登记开放问题"),
                f"人工确认：{_sanitize_text(bundle.get('human_confirmation')) or 'pending'}",
                f"Factcheck：{factcheck.get('status') or 'not_run'}",
                f"审计摘要：{_sanitize_text(bundle.get('audit_summary')) or '证据不足：未提供审计摘要'}",
            ],
        ),
    ]

    return {
        "schema_version": IC_R4_REPORT_VIEW_MODEL_SCHEMA,
        "report_id": decision.get("report_id"),
        "deal_id": decision.get("deal_id"),
        "evidence_snapshot_hash": decision.get("evidence_snapshot_hash"),
        "decision": decision.get("decision"),
        "weighted_agent_score": decision.get("weighted_agent_score"),
        "chairman_dimension_score": decision.get("chairman_dimension_score"),
        "r0_context": r0,
        "source_report_selection": report_selection,
        "sections": sections,
    }


def render_r4_markdown(view_model: Mapping[str, Any]) -> str:
    lines = ["# 一级市场投资委员会决策报告", ""]
    for index, section in enumerate(view_model.get("sections", []), start=1):
        lines.extend([f"## {index}. {section.get('title')}", ""])
        lines.extend(f"- {_sanitize_text(item)}" for item in section.get("lines", []))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_r4_html(view_model: Mapping[str, Any]) -> str:
    body = ["<main>", "<h1>一级市场投资委员会决策报告</h1>"]
    for index, section in enumerate(view_model.get("sections", []), start=1):
        body.append(f"<section><h2>{index}. {escape(_sanitize_text(section.get('title')))}</h2><ul>")
        body.extend(f"<li>{escape(_sanitize_text(item))}</li>" for item in section.get("lines", []))
        body.append("</ul></section>")
    body.append("</main>")
    return (
        "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        "<title>一级市场投资委员会决策报告</title></head><body>"
        + "".join(body)
        + "</body></html>\n"
    )


def render_r4_report(bundle: Mapping[str, Any]) -> dict[str, Any]:
    view_model = build_r4_report_view_model(bundle)
    markdown = render_r4_markdown(view_model)
    html = render_r4_html(view_model)
    canonical = json.dumps(view_model, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "schema_version": IC_R4_RENDERED_REPORT_SCHEMA,
        "view_model": view_model,
        "view_model_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "markdown": markdown,
        "html": html,
    }


__all__ = [
    "IC_R4_RENDERED_REPORT_SCHEMA",
    "IC_R4_REPORT_VIEW_MODEL_SCHEMA",
    "R4_SECTION_TITLES",
    "build_r4_report_view_model",
    "render_r4_html",
    "render_r4_markdown",
    "render_r4_report",
]
