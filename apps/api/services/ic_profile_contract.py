"""Profile-derived role contracts for primary-market IC agents."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from services import deal_store
from services import ic_policy


IC_PROFILE_CONTRACT_SCHEMA = "siq_ic_profile_contract_v1"
ROLE_SOURCE_FILES = ("IDENTITY.md", "AGENTS.md", "SOUL.md", "USER.md")


def _canonical_profile_id(profile_id: str) -> str:
    canonical = ic_policy.canonical_ic_profile_id(profile_id)
    if canonical not in ic_policy.IC_PROFILE_IDS:
        raise ValueError(f"Unknown IC profile: {profile_id}")
    return canonical


def _profile_dir(profile_id: str, *, profiles_root: Path | str | None = None) -> Path:
    root = Path(profiles_root) if profiles_root else ic_policy.IC_PROFILES_ROOT
    return root / profile_id


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def _markdown_field(text: str, *labels: str) -> str:
    for label in labels:
        pattern = re.compile(rf"\*\*\s*{re.escape(label)}\s*[：:]?\s*\*\*\s*[：:]?\s*(.+)")
        for line in text.splitlines():
            match = pattern.search(line)
            if match:
                return match.group(1).strip().strip("`")
    return ""


def _bullets_after_markdown_field(text: str, *labels: str, limit: int = 6) -> list[str]:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if any(re.search(rf"\*\*\s*{re.escape(label)}\s*[：:]?\s*\*\*", line) for label in labels):
            bullets: list[str] = []
            for candidate in lines[index + 1:]:
                stripped = candidate.strip()
                if not stripped:
                    continue
                if stripped.startswith("#") or re.search(r"\*\*.+\*\*", stripped):
                    break
                if stripped.startswith("-"):
                    item = stripped.lstrip("-").strip()
                    if item:
                        bullets.append(item)
                if len(bullets) >= limit:
                    break
            return bullets
    return []


def _section_bullets(text: str, heading: str, *, limit: int = 8) -> list[str]:
    marker = re.compile(rf"^##+\s+.*{re.escape(heading)}.*$", re.MULTILINE)
    match = marker.search(text)
    if not match:
        return []
    section = text[match.end():]
    next_heading = re.search(r"^##+\s+", section, re.MULTILINE)
    if next_heading:
        section = section[: next_heading.start()]
    bullets: list[str] = []
    for line in section.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(("❌", "-", "*")):
            item = stripped.lstrip("❌-* ").strip()
            if item:
                bullets.append(item)
        if len(bullets) >= limit:
            break
    return bullets


def _matrix_profile(profile_id: str, matrix: dict[str, Any] | None = None) -> dict[str, Any]:
    matrix = matrix or ic_policy.read_ic_profile_matrix()
    profiles = matrix.get("profiles") if isinstance(matrix.get("profiles"), list) else []
    for item in profiles:
        if isinstance(item, dict) and item.get("id") == profile_id:
            return item
    return {}


def _source_files(profile_dir: Path, profile_id: str) -> list[str]:
    files: list[str] = []
    for name in ROLE_SOURCE_FILES:
        path = profile_dir / name
        if path.is_file():
            files.append(f"agents/hermes/profiles/{profile_id}/{name}")
    return files


def _source_file_details(source_files: list[str]) -> list[dict[str, str]]:
    return [
        {
            "name": Path(path).name,
            "path": path,
        }
        for path in source_files
    ]


def get_ic_profile_contract(
    profile_id: str,
    *,
    profiles_root: Path | str | None = None,
) -> dict[str, Any]:
    """Build a role contract from profile markdown and shared IC metadata."""

    canonical = _canonical_profile_id(profile_id)
    profile_dir = _profile_dir(canonical, profiles_root=profiles_root)
    if not profile_dir.is_dir():
        raise FileNotFoundError(f"IC profile directory not found: {canonical}")

    identity = _read_text(profile_dir / "IDENTITY.md")
    agents = _read_text(profile_dir / "AGENTS.md")
    matrix_contract = ic_policy.read_ic_profile_matrix()
    matrix = _matrix_profile(canonical, matrix_contract)
    responsibilities = [
        str(item).strip()
        for item in matrix.get("responsibilities", [])
        if str(item or "").strip()
    ] if isinstance(matrix.get("responsibilities"), list) else []

    role_name = (
        _markdown_field(identity, "角色名称")
        or _markdown_field(agents, "角色定位")
        or str(matrix.get("label") or canonical)
    )
    core_focus = (
        _markdown_field(agents, "核心视角")
        or _markdown_field(identity, "核心使命")
        or ", ".join(responsibilities)
    )
    special_duty = _markdown_field(agents, "特殊职责")
    mission = _markdown_field(identity, "核心使命")
    output_features = _bullets_after_markdown_field(identity, "输出特征")
    profile_boundaries = _section_bullets(agents, "红线") or _section_bullets(agents, "禁止")
    matrix_boundaries = [
        str(item).strip()
        for item in matrix.get("boundaries", [])
        if str(item or "").strip()
    ] if isinstance(matrix.get("boundaries"), list) else []
    boundaries = list(dict.fromkeys([*matrix_boundaries, *profile_boundaries]))
    if not boundaries and canonical != "siq_ic_master_coordinator":
        boundaries = ["不越权代替其他投委会 profile 的专业判断或最终投决。"]
    source_files = _source_files(profile_dir, canonical)
    outputs = output_features or ["结构化观点", "证据引用", "verified/assumed 区分", "下一步建议"]
    role_title = role_name.split("/", 1)[0].strip() or role_name
    phase_capabilities = matrix.get("phase_capabilities") if isinstance(matrix.get("phase_capabilities"), dict) else {}
    output_schemas = matrix.get("output_schemas") if isinstance(matrix.get("output_schemas"), dict) else {}
    retrieval = matrix.get("retrieval") if isinstance(matrix.get("retrieval"), dict) else {}
    logical_collections = [
        str(item) for item in retrieval.get("logical_collections", []) if str(item or "").strip()
    ]
    physical_collections = [
        str(item) for item in retrieval.get("physical_collections", []) if str(item or "").strip()
    ]
    retrieval_required = bool(retrieval.get("required", canonical != "siq_ic_master_coordinator"))
    shared_logical = str((matrix_contract.get("shared_collection") or {}).get("logical") or "siq_deal_shared")
    shared_physical = str((matrix_contract.get("shared_collection") or {}).get("physical") or "")
    private_logical = next((item for item in logical_collections if item != shared_logical), canonical)
    configured_private_physical = str(retrieval.get("private_collection") or "")
    private_physical = configured_private_physical or next(
        (item for item in physical_collections if item != shared_physical),
        "",
    )

    return {
        "schema_version": IC_PROFILE_CONTRACT_SCHEMA,
        "contract_version": matrix_contract.get("contract_version") or IC_PROFILE_CONTRACT_SCHEMA,
        "profile_id": canonical,
        "legacy_profile_id": next(
            (legacy for legacy, target in ic_policy.LEGACY_PROFILE_IDS.items() if target == canonical),
            None,
        ),
        "label": matrix.get("label") or canonical,
        "role": matrix.get("role") or canonical.removeprefix("siq_ic_"),
        "role_name": role_name,
        "role_title": role_title,
        "mission": mission,
        "core_focus": core_focus,
        "focus": core_focus,
        "special_duty": special_duty,
        "responsibilities": responsibilities,
        "output_features": outputs,
        "outputs": outputs,
        "boundaries": boundaries,
        "phase_capabilities": phase_capabilities,
        "output_schemas": output_schemas,
        "retrieval": {
            "required": retrieval_required,
            "logical_collections": logical_collections,
            "physical_collections": physical_collections,
            "private_collection": private_logical,
            "shared_collection": shared_logical,
            "private_physical_collection": private_physical,
            "shared_physical_collection": shared_physical,
            "private_collection_rule": matrix_contract.get("private_collection_rule") or {},
        },
        "startup_retrieval_required": retrieval_required,
        "r1_sequence_index": (
            ic_policy.R1_AGENT_SEQUENCE.index(canonical)
            if canonical in ic_policy.R1_AGENT_SEQUENCE
            else None
        ),
        "retrieval_collections": logical_collections or ["siq_deal_shared", canonical],
        "retrieval_physical_collections": physical_collections,
        "private_knowledge_collection": private_logical,
        "private_physical_collection": private_physical,
        "shared_collection": shared_logical,
        "shared_physical_collection": shared_physical,
        "profile_path": f"agents/hermes/profiles/{canonical}",
        "source_files": source_files,
        "source_file_details": _source_file_details(source_files),
        "updated_at": deal_store.utc_now_iso(),
    }


def list_ic_profile_contracts(
    *,
    profiles_root: Path | str | None = None,
) -> list[dict[str, Any]]:
    return [
        get_ic_profile_contract(profile_id, profiles_root=profiles_root)
        for profile_id in ic_policy.IC_PROFILE_IDS
    ]


def render_meeting_role_guard(profile_id: str | dict[str, Any]) -> str:
    contract = profile_id if isinstance(profile_id, dict) else get_ic_profile_contract(profile_id)
    source_paths = "、".join(str(item) for item in contract.get("source_files", [])) or contract["profile_path"]
    responsibilities = "；".join(contract.get("responsibilities") or [])
    outputs = "；".join(contract.get("output_features") or [])
    boundaries = "；".join(contract.get("boundaries") or [])
    special_duty = str(contract.get("special_duty") or "").strip()
    focus_parts = [str(contract.get("core_focus") or "").strip()]
    if special_duty:
        focus_parts.append(special_duty)
    if responsibilities:
        focus_parts.append(responsibilities)

    return "\n".join(
        [
            "一级市场 IC profile 职责护栏:",
            f"- profile_id: {contract['profile_id']}",
            f"- 角色名称: {contract.get('role_name') or contract.get('label')}",
            f"- 职责来源: {source_paths}",
            f"- startup_retrieval_required: {str(bool(contract.get('startup_retrieval_required'))).lower()}",
            f"- r1_sequence_index: {contract.get('r1_sequence_index') if contract.get('r1_sequence_index') is not None else '-'}",
            f"- 本轮只按该 profile 的职责回答: {'；'.join(part for part in focus_parts if part)}",
            f"- 应输出: {outputs or '结构化观点、证据引用、verified/assumed 区分、下一步建议。'}",
            f"- 角色边界: {boundaries or '不得越权替代其他 IC profile 的专业判断。'}",
            "- 若主持人问题要求越权，先声明角色边界，再只回答本 profile 职责内的部分，并建议应由哪个 SIQ IC profile 接手。",
            "- 涉及事实、评分、投决或风险判断时，必须优先使用 Deal OS evidence、startup-retrieval receipt、R0-R4 产物和用户附件；信息不足时标注 verified/assumed/待核验，不得编造。",
        ]
    )
