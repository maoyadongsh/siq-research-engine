"""Lightweight quality checks for IC agent meeting replies."""

from __future__ import annotations

import re
from typing import Any

from services import ic_policy

IC_AGENT_OUTPUT_QUALITY_SCHEMA = "siq_ic_agent_output_quality_v1"


def _canonical_profile_id(profile_id: str) -> str:
    canonical = ic_policy.canonical_ic_profile_id(profile_id)
    if canonical not in ic_policy.IC_PROFILE_IDS:
        raise ValueError(f"Unknown IC profile: {profile_id}")
    return canonical


def _check(check_id: str, status: str, message: str, **details: Any) -> dict[str, Any]:
    return {
        "id": check_id,
        "status": status,
        "detail": message,
        "message": message,
        **{key: value for key, value in details.items() if value not in (None, "", [])},
    }


def _has_evidence_reference(text: str) -> bool:
    patterns = (
        r"\bEVID[-_A-Za-z0-9]*",
        r"\bsource_type\s*=",
        r"\bevidence_id\b",
        r"\bstartup-[A-Za-z0-9_-]+",
        r"\breceipt_id\b",
        r"\btask_id\b",
        r"\bpdf_page\b",
        r"\bmd_line\b",
        r"证据",
        r"引用",
    )
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def _with_legacy_check_aliases(checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    aliases = {
        "evidence.reference": "evidence_reference",
        "verified_assumed": "verification_status",
        "role.boundary": "role_boundary",
        "response.length": "response_length",
    }
    expanded = list(checks)
    existing = {str(item.get("id") or "") for item in expanded}
    for item in checks:
        alias = aliases.get(str(item.get("id") or ""))
        if alias and alias not in existing:
            legacy = dict(item)
            legacy["id"] = alias
            expanded.append(legacy)
            existing.add(alias)
    return expanded


def _project_evidence_available(context: dict[str, Any] | None) -> bool | None:
    if not isinstance(context, dict):
        return None
    receipt = context.get("startup_receipt")
    if not isinstance(receipt, dict):
        return None
    counts: list[int] = []
    for key in ("shared_vector_hit_count", "evidence_hit_count", "shared_hits"):
        if key not in receipt or receipt.get(key) is None:
            continue
        try:
            counts.append(int(receipt[key]))
        except (TypeError, ValueError):
            continue
    return any(value > 0 for value in counts) if counts else None


def evaluate_ic_agent_reply(
    profile_id: str,
    message: str,
    reply: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    canonical = _canonical_profile_id(profile_id)
    text = str(reply or "")
    normalized_text = re.sub(r"[*_`]", "", text)
    checks: list[dict[str, Any]] = []

    if text.strip():
        checks.append(_check("reply.non_empty", "pass", "回复非空。"))
    else:
        checks.append(_check("reply.non_empty", "fail", "回复为空。"))

    if _has_evidence_reference(text):
        checks.append(_check("evidence.reference", "pass", "包含证据、receipt 或来源引用线索。"))
    else:
        receipt = context.get("startup_receipt") if isinstance(context, dict) and isinstance(context.get("startup_receipt"), dict) else {}
        detail = (
            "缺少可解析的证据或来源引用线索；且当前缺少 startup receipt，本轮只能作为临时咨询。"
            if receipt.get("present") is False
            else "缺少可解析的证据或来源引用线索。"
        )
        checks.append(_check("evidence.reference", "warn", detail))

    evidence_available = _project_evidence_available(context)
    if evidence_available is False and re.search(
        r"私有(?:库)?证据|private\s+(?:kb\s+)?evidence",
        normalized_text,
        re.IGNORECASE,
    ):
        checks.append(
            _check(
                "evidence.private_background_boundary",
                "fail",
                "当前项目 Evidence 为空，但回复把角色私库背景误称为项目证据。",
            )
        )
    else:
        checks.append(
            _check(
                "evidence.private_background_boundary",
                "pass",
                "未发现把角色私库背景冒充项目证据。",
            )
        )

    unsupported_project_rating = evidence_available is False and bool(
        re.search(
            r"(?:当前|综合|本项目|法律|合规).{0,16}(?:指数|评分|得分).{0,8}\d+(?:\.\d+)?\s*分?"
            r"|风险评级\s*[：:]\s*(?:🟢|🟡|🔴)?\s*(?:低|中|高)",
            normalized_text,
            re.IGNORECASE,
        )
    )
    if unsupported_project_rating:
        checks.append(
            _check(
                "evidence.project_rating_support",
                "fail",
                "当前项目 Evidence 为空，不能输出项目专属分数或风险评级。",
            )
        )
    else:
        checks.append(
            _check(
                "evidence.project_rating_support",
                "pass",
                "未发现无项目 Evidence 支撑的项目分数或风险评级。",
            )
        )

    if re.search(r"verified|assumed|待核验|已核验|已验证|假设|未知|未返回", text, re.IGNORECASE):
        checks.append(_check("verified_assumed", "pass", "区分了 verified/assumed 或待核验状态。"))
    else:
        checks.append(_check("verified_assumed", "warn", "未明显区分 verified/assumed/待核验。"))

    if re.search(r"下一步|建议|补充|追问|行动|条件|触发|需.{0,6}确认", text):
        checks.append(_check("next_action", "pass", "包含下一步或行动建议。"))
    else:
        checks.append(_check("next_action", "warn", "缺少下一步行动建议。"))

    if canonical not in {"siq_ic_chairman", "siq_ic_master_coordinator"} and re.search(
        r"最终投决|最终决定|正式通过|正式否决|我决定投资|我决定否决",
        text,
    ):
        checks.append(_check("role.boundary", "fail", "非主席/总协调员回复疑似越权表达最终投决。"))
    else:
        checks.append(_check("role.boundary", "pass", "未发现明显最终投决越权表达。"))

    if len(text) > 18000:
        checks.append(_check("response.length", "warn", "回复较长，前端可能需要折叠或结构化。", chars=len(text)))
    else:
        checks.append(_check("response.length", "pass", "回复长度处于可展示范围。", chars=len(text)))

    expanded_checks = _with_legacy_check_aliases(checks)
    status = "fail" if any(item["status"] == "fail" for item in expanded_checks) else (
        "warn" if any(item["status"] == "warn" for item in expanded_checks) else "pass"
    )
    return {
        "schema_version": IC_AGENT_OUTPUT_QUALITY_SCHEMA,
        "profile_id": canonical,
        "status": status,
        "message_preview": str(message or "")[:300],
        "checks": expanded_checks,
        "context": context or {},
    }
