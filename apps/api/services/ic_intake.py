"""SIQ-native R0 intake verification for Deal OS."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from services import deal_store
from services import external_research_clients


IC_R0_INTAKE_SCHEMA = "siq_ic_r0_intake_v1"
R0_MARKDOWN_PATH = "discussion/00_\u9879\u76ee\u4fe1\u606f_R0.md"
R0_JSON_PATH = "phases/r0_intake.json"
DEFAULT_MAX_RESULTS = 5
MAX_RESULTS = 20
PUBLIC_SECTIONS: tuple[tuple[str, str], ...] = (
    ("funding", "{company} funding valuation financing round"),
    ("business", "{company} business product customer"),
    ("team", "{company} founder CEO team"),
    ("profile", "{company} company profile main business"),
    ("industry", "{company} industry analysis competitors"),
)


def _require_package_dir(deal_id: str, *, wiki_root: Path | str | None = None) -> Path:
    package_dir = deal_store.safe_deal_dir(deal_id, wiki_root=wiki_root)
    if not (package_dir / "manifest.json").is_file():
        raise FileNotFoundError(deal_id)
    return package_dir


def _normalize_max_results(value: int | str | None) -> int:
    try:
        parsed = int(value) if value is not None else DEFAULT_MAX_RESULTS
    except (TypeError, ValueError):
        parsed = DEFAULT_MAX_RESULTS
    return max(1, min(parsed, MAX_RESULTS))


def _normalize_company_name(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\(.*?\)|\uff08.*?\uff09", "", text)
    for suffix in (
        "股份有限公司",
        "有限责任公司",
        "有限公司",
        "普通合伙",
        "特殊普通合伙",
        "有限合伙",
        "Inc.",
        "Inc",
        "Ltd.",
        "Ltd",
        "LLC",
    ):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
    return text.strip().lower()


def _project_context(package_dir: Path, deal_id: str) -> dict[str, Any]:
    project_meta = deal_store.read_json(package_dir / "project_meta.json", {}) or {}
    workflow = deal_store.read_json(package_dir / "phases" / "workflow_state.json", {}) or {}
    return {
        "deal_id": deal_store.validate_deal_id(deal_id),
        "company_name": project_meta.get("company_name") or workflow.get("company_name") or "",
        "industry": project_meta.get("industry") or workflow.get("industry") or "",
        "stage": project_meta.get("stage") or workflow.get("stage") or "",
        "deal_type": project_meta.get("deal_type") or "",
        "source": project_meta.get("source") or "",
    }


def _task_description(context: dict[str, Any], override: dict[str, Any] | None) -> dict[str, Any]:
    payload = {
        "company_name": context.get("company_name") or "",
        "industry": context.get("industry") or "",
        "stage": context.get("stage") or "",
        "deal_type": context.get("deal_type") or "",
    }
    if isinstance(override, dict):
        payload.update({str(key): value for key, value in override.items() if value not in (None, "")})
    return payload


def _providers(providers: list[str] | tuple[str, ...] | None) -> list[str]:
    raw = providers or list(external_research_clients.DEFAULT_PROVIDERS)
    normalized: list[str] = []
    for provider in raw:
        value = str(provider or "").strip().lower()
        if value and value not in normalized:
            normalized.append(value)
    return normalized


def _run_external_section(
    *,
    query: str,
    providers: list[str],
    include_external: bool,
    max_results: int,
) -> dict[str, Any]:
    if not providers:
        return {
            "schema_version": external_research_clients.EXTERNAL_RESEARCH_SCHEMA,
            "enabled": bool(include_external),
            "query": query,
            "providers": [],
            "results": [],
            "result_count": 0,
        }
    return external_research_clients.run_external_research(
        query=query,
        providers=providers,
        max_results=max_results,
        enabled=include_external,
    )


def _run_external_checks(
    *,
    search_key: str,
    providers: list[str],
    include_external: bool,
    max_results: int,
) -> dict[str, Any]:
    public_providers = [provider for provider in providers if provider in {"exa", "tavily"}]
    sections: dict[str, Any] = {
        "qcc_registration": _run_external_section(
            query=search_key,
            providers=["qcc"] if "qcc" in providers else [],
            include_external=include_external and "qcc" in providers,
            max_results=1,
        )
    }
    for section, template in PUBLIC_SECTIONS:
        sections[section] = _run_external_section(
            query=template.format(company=search_key),
            providers=public_providers,
            include_external=include_external and bool(public_providers),
            max_results=max_results,
        )
    return sections


def _section_results(section_payload: dict[str, Any]) -> list[dict[str, Any]]:
    results = section_payload.get("results") if isinstance(section_payload, dict) else []
    return [item for item in results if isinstance(item, dict)] if isinstance(results, list) else []


def _result_text(item: dict[str, Any]) -> str:
    return " ".join(
        str(item.get(key) or "")
        for key in ("title", "snippet", "url", "published_date")
    )


def _parse_jsonish(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            return {}
        try:
            parsed = json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            return {}
    return parsed if isinstance(parsed, dict) else {}


def extract_qcc_fields(external_checks: dict[str, Any]) -> dict[str, Any]:
    qcc_results = _section_results(external_checks.get("qcc_registration", {}))
    if not qcc_results:
        return {}
    raw_payload = _parse_jsonish(str(qcc_results[0].get("snippet") or ""))
    return {
        "company_name": raw_payload.get("\u4f01\u4e1a\u540d\u79f0") or raw_payload.get("name") or raw_payload.get("companyName"),
        "credit_code": raw_payload.get("\u7edf\u4e00\u793e\u4f1a\u4fe1\u7528\u4ee3\u7801") or raw_payload.get("creditCode"),
        "legal_rep": raw_payload.get("\u6cd5\u5b9a\u4ee3\u8868\u4eba") or raw_payload.get("legalRep"),
        "reg_capital": raw_payload.get("\u6ce8\u518c\u8d44\u672c") or raw_payload.get("regCapital"),
        "establish_date": raw_payload.get("\u6210\u7acb\u65e5\u671f") or raw_payload.get("establishDate"),
        "company_status": raw_payload.get("\u767b\u8bb0\u72b6\u6001") or raw_payload.get("status"),
        "business_scope": raw_payload.get("\u7ecf\u8425\u8303\u56f4") or raw_payload.get("businessScope"),
        "reg_address": raw_payload.get("\u6ce8\u518c\u5730\u5740") or raw_payload.get("regAddress"),
    }


def extract_public_facts(external_checks: dict[str, Any]) -> dict[str, Any]:
    facts: dict[str, Any] = {
        "funding_history": [],
        "business_description": None,
        "founders": [],
        "industry_summary": None,
        "source_count": 0,
    }
    for section in ("funding", "business", "team", "profile", "industry"):
        results = _section_results(external_checks.get(section, {}))
        facts["source_count"] += len(results)
        for item in results:
            text = _result_text(item)
            lowered = text.lower()
            if section == "funding" and any(term in lowered for term in ("funding", "financing", "valuation", "\u878d\u8d44", "\u4f30\u503c")):
                facts["funding_history"].append({
                    "source_id": item.get("source_id"),
                    "provider": item.get("provider"),
                    "url": item.get("url"),
                    "snippet": str(item.get("snippet") or "")[:300],
                })
            if section in {"business", "profile"} and not facts.get("business_description"):
                facts["business_description"] = str(item.get("snippet") or item.get("title") or "")[:600]
            if section == "team" and any(term in lowered for term in ("founder", "ceo", "\u521b\u59cb\u4eba", "\u56e2\u961f")):
                facts["founders"].append({
                    "source_id": item.get("source_id"),
                    "provider": item.get("provider"),
                    "url": item.get("url"),
                    "snippet": str(item.get("snippet") or "")[:300],
                })
            if section == "industry" and not facts.get("industry_summary"):
                facts["industry_summary"] = str(item.get("snippet") or item.get("title") or "")[:600]
    return facts


def _task_value(task_description: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in task_description and task_description[key] not in (None, ""):
            return task_description[key]
    return None


def _terms(text: Any) -> set[str]:
    return {
        item.lower()
        for item in re.findall(r"[\w\u4e00-\u9fff]{2,}", str(text or ""))
        if item.strip()
    }


def compare_intake_data(
    *,
    qcc_fields: dict[str, Any],
    public_facts: dict[str, Any],
    task_description: dict[str, Any],
) -> list[dict[str, Any]]:
    discrepancies: list[dict[str, Any]] = []
    task_company = _task_value(task_description, ("company_name", "\u516c\u53f8\u540d\u79f0", "\u516c\u53f8\u5168\u79f0", "\u4f01\u4e1a\u540d\u79f0"))
    qcc_company = qcc_fields.get("company_name")
    if task_company and qcc_company and _normalize_company_name(task_company) != _normalize_company_name(qcc_company):
        discrepancies.append({
            "field": "company_name",
            "task_value": task_company,
            "qcc_value": qcc_company,
            "severity": "HIGH",
            "description": "Company name differs from QCC registration data.",
        })

    for field, keys, description in (
        ("establish_date", ("\u6210\u7acb\u65e5\u671f", "establish_date", "founded_at"), "Founding year differs from QCC data."),
        ("reg_capital", ("\u6ce8\u518c\u8d44\u672c", "reg_capital"), "Registered capital differs from QCC data."),
    ):
        task_item = _task_value(task_description, keys)
        qcc_item = qcc_fields.get(field)
        if task_item and qcc_item and str(task_item)[:4] != str(qcc_item)[:4]:
            discrepancies.append({
                "field": field,
                "task_value": task_item,
                "qcc_value": qcc_item,
                "severity": "MEDIUM" if field == "reg_capital" else "HIGH",
                "description": description,
            })

    task_funding = _task_value(task_description, ("\u878d\u8d44\u5386\u53f2", "funding_history", "\u4f30\u503c", "valuation"))
    if task_funding and not public_facts.get("funding_history"):
        discrepancies.append({
            "field": "funding_history",
            "task_value": task_funding,
            "public_value": "No public funding source found.",
            "severity": "MEDIUM",
            "description": "Task mentions financing but public checks did not confirm it.",
        })

    task_business = _task_value(task_description, ("\u4e3b\u8425\u4e1a\u52a1", "main_business", "business_description"))
    public_business = public_facts.get("business_description")
    if task_business and public_business:
        overlap = _terms(task_business).intersection(_terms(public_business))
        if len(overlap) < 2:
            discrepancies.append({
                "field": "main_business",
                "task_value": str(task_business)[:200],
                "public_value": str(public_business)[:200],
                "severity": "HIGH",
                "description": "Business description differs materially from public sources.",
            })

    task_founder = _task_value(task_description, ("\u521b\u59cb\u4eba", "founder", "ceo"))
    legal_rep = qcc_fields.get("legal_rep")
    founders = public_facts.get("founders") if isinstance(public_facts.get("founders"), list) else []
    founder_seen = any(str(task_founder or "") in str(item.get("snippet") or "") for item in founders if isinstance(item, dict))
    if task_founder and legal_rep and str(task_founder).strip() != str(legal_rep).strip() and not founder_seen:
        discrepancies.append({
            "field": "founder_or_legal_rep",
            "task_value": task_founder,
            "qcc_value": legal_rep,
            "severity": "MEDIUM",
            "description": "Founder in task description differs from QCC legal representative and was not confirmed publicly.",
        })
    return discrepancies


def coverage_gaps(
    *,
    include_external: bool,
    external_checks: dict[str, Any],
    qcc_fields: dict[str, Any],
    public_facts: dict[str, Any],
) -> list[str]:
    gaps: list[str] = []
    if not include_external:
        gaps.append("external_checks_disabled")
    if include_external and not qcc_fields:
        gaps.append("qcc_registration_not_confirmed")
    if include_external and int(public_facts.get("source_count") or 0) == 0:
        gaps.append("public_sources_not_confirmed")
    for section, payload in external_checks.items():
        providers = payload.get("providers") if isinstance(payload, dict) else []
        for provider in providers if isinstance(providers, list) else []:
            if not isinstance(provider, dict):
                continue
            status = str(provider.get("status") or "")
            if status in {"skipped", "error"}:
                gaps.append(f"{section}:{provider.get('provider')}:{provider.get('reason') or status}")
    return sorted(set(gaps))


def generate_scorecard(discrepancies: list[dict[str, Any]], gaps: list[str]) -> dict[str, Any]:
    high_count = sum(1 for item in discrepancies if item.get("severity") == "HIGH")
    medium_count = sum(1 for item in discrepancies if item.get("severity") == "MEDIUM")
    low_count = sum(1 for item in discrepancies if item.get("severity") == "LOW")
    if high_count:
        level = "L1 - danger"
        level_code = 1
        action = "PAUSE_AND_CLARIFY"
    elif medium_count or gaps:
        level = "L2 - review_required"
        level_code = 2
        action = "PROCEED_WITH_CAUTION"
    elif low_count:
        level = "L3 - reliable"
        level_code = 3
        action = "PROCEED"
    else:
        level = "L4 - trusted"
        level_code = 4
        action = "PROCEED"
    return {
        "level": level,
        "level_code": level_code,
        "action": action,
        "high_severity_count": high_count,
        "medium_severity_count": medium_count,
        "low_severity_count": low_count,
        "coverage_gap_count": len(gaps),
        "generated_at": deal_store.utc_now_iso(),
    }


def _r0_workflow_status(action: str) -> str:
    if action == "PAUSE_AND_CLARIFY":
        return "blocked"
    if action == "PROCEED_WITH_CAUTION":
        return "review_required"
    return "completed"


def generate_markdown_report(package: dict[str, Any]) -> str:
    scorecard = package.get("scorecard") if isinstance(package.get("scorecard"), dict) else {}
    qcc_fields = package.get("qcc_fields") if isinstance(package.get("qcc_fields"), dict) else {}
    public_facts = package.get("public_facts") if isinstance(package.get("public_facts"), dict) else {}
    discrepancies = package.get("discrepancies") if isinstance(package.get("discrepancies"), list) else []
    gaps = package.get("coverage_gaps") if isinstance(package.get("coverage_gaps"), list) else []
    lines = [
        "# SIQ R0 Intake Verification",
        "",
        f"- deal_id: {package.get('deal_id')}",
        f"- company: {package.get('company_name')}",
        f"- search_key: {package.get('search_key')}",
        f"- action: {scorecard.get('action')}",
        f"- level: {scorecard.get('level')}",
        f"- generated_at: {package.get('generated_at')}",
        "",
        "## QCC Registration",
        "",
    ]
    if qcc_fields:
        for key in ("company_name", "credit_code", "legal_rep", "reg_capital", "establish_date", "company_status"):
            lines.append(f"- {key}: {qcc_fields.get(key) or 'N/A'}")
    else:
        lines.append("- status: not confirmed")
    lines.extend(["", "## Public Facts", ""])
    lines.append(f"- public_source_count: {public_facts.get('source_count') or 0}")
    lines.append(f"- funding_sources: {len(public_facts.get('funding_history') or [])}")
    lines.append(f"- founder_sources: {len(public_facts.get('founders') or [])}")
    if public_facts.get("business_description"):
        lines.append(f"- business_description: {str(public_facts.get('business_description'))[:300]}")
    lines.extend(["", "## Discrepancies", ""])
    if discrepancies:
        for item in discrepancies:
            lines.append(f"- [{item.get('severity')}] {item.get('field')}: {item.get('description')}")
    else:
        lines.append("- none")
    lines.extend(["", "## Coverage Gaps", ""])
    if gaps:
        lines.extend(f"- {gap}" for gap in gaps)
    else:
        lines.append("- none")
    lines.extend(["", "## Decision", ""])
    if scorecard.get("action") == "PAUSE_AND_CLARIFY":
        lines.append("R0 should pause until high-severity discrepancies are clarified.")
    elif scorecard.get("action") == "PROCEED_WITH_CAUTION":
        lines.append("R0 may proceed only with explicit notation of unresolved source gaps or medium-risk differences.")
    else:
        lines.append("R0 may proceed to expert diligence.")
    return "\n".join(lines) + "\n"


def _write_r0_artifacts(package_dir: Path, package: dict[str, Any]) -> None:
    deal_store.write_json(package_dir / R0_JSON_PATH, package)
    markdown_path = package_dir / R0_MARKDOWN_PATH
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(generate_markdown_report(package), encoding="utf-8")


def _update_workflow_state(package_dir: Path, package: dict[str, Any]) -> None:
    workflow_path = package_dir / "phases" / "workflow_state.json"
    workflow = deal_store.read_json(workflow_path, {}) or {}
    phases = workflow.setdefault("phases", {})
    r0 = phases.setdefault("R0", {})
    scorecard = package.get("scorecard") if isinstance(package.get("scorecard"), dict) else {}
    r0.update({
        "status": _r0_workflow_status(str(scorecard.get("action") or "")),
        "intake_path": R0_JSON_PATH,
        "markdown_path": R0_MARKDOWN_PATH,
        "action": scorecard.get("action"),
        "level_code": scorecard.get("level_code"),
        "updated_at": package.get("generated_at"),
    })
    workflow["updated_at"] = package.get("generated_at")
    deal_store.write_json(workflow_path, workflow)


def _update_project_meta(package_dir: Path, package: dict[str, Any]) -> None:
    meta_path = package_dir / "project_meta.json"
    project_meta = deal_store.read_json(meta_path, {}) or {}
    scorecard = package.get("scorecard") if isinstance(package.get("scorecard"), dict) else {}
    project_meta.update({
        "r0_intake_status": scorecard.get("action"),
        "r0_intake_level": scorecard.get("level"),
        "r0_intake_path": R0_JSON_PATH,
        "updated_at": package.get("generated_at"),
    })
    deal_store.write_json(meta_path, project_meta)


def run_r0_intake(
    deal_id: str,
    *,
    search_key: str | None = None,
    task_description: dict[str, Any] | None = None,
    include_external: bool = False,
    external_providers: list[str] | tuple[str, ...] | None = None,
    max_results: int | str | None = DEFAULT_MAX_RESULTS,
    dry_run: bool = False,
    created_by: dict[str, Any] | None = None,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    package_dir = _require_package_dir(deal_id, wiki_root=wiki_root)
    normalized_deal_id = deal_store.validate_deal_id(deal_id)
    context = _project_context(package_dir, normalized_deal_id)
    company_name = str(context.get("company_name") or "").strip()
    normalized_search_key = " ".join(str(search_key or company_name or normalized_deal_id).split())[:300]
    if not normalized_search_key:
        raise ValueError("search_key or deal company_name is required")
    normalized_providers = _providers(external_providers)
    normalized_max_results = _normalize_max_results(max_results)
    generated_at = deal_store.utc_now_iso()
    task_payload = _task_description(context, task_description)
    external_checks = _run_external_checks(
        search_key=normalized_search_key,
        providers=normalized_providers,
        include_external=include_external,
        max_results=normalized_max_results,
    )
    qcc_fields = extract_qcc_fields(external_checks)
    public_facts = extract_public_facts(external_checks)
    discrepancies = compare_intake_data(
        qcc_fields=qcc_fields,
        public_facts=public_facts,
        task_description=task_payload,
    )
    gaps = coverage_gaps(
        include_external=include_external,
        external_checks=external_checks,
        qcc_fields=qcc_fields,
        public_facts=public_facts,
    )
    scorecard = generate_scorecard(discrepancies, gaps)
    package: dict[str, Any] = {
        "schema_version": IC_R0_INTAKE_SCHEMA,
        "deal_id": normalized_deal_id,
        "phase": "R0",
        "company_name": company_name,
        "search_key": normalized_search_key,
        "task_description": task_payload,
        "verification_mode": "external_cross_check" if include_external else "local_metadata_only",
        "include_external": bool(include_external),
        "external_providers": normalized_providers,
        "external_checks": external_checks,
        "qcc_fields": qcc_fields,
        "public_facts": public_facts,
        "discrepancies": discrepancies,
        "coverage_gaps": gaps,
        "scorecard": scorecard,
        "json_path": R0_JSON_PATH,
        "markdown_path": R0_MARKDOWN_PATH,
        "generated_at": generated_at,
        "created_by": created_by,
        "dry_run": bool(dry_run),
        "written": False,
    }
    if not dry_run:
        _write_r0_artifacts(package_dir, package)
        _update_workflow_state(package_dir, package)
        _update_project_meta(package_dir, package)
        package["written"] = True
        deal_store.append_audit_event(
            normalized_deal_id,
            {
                "event_type": "deal_r0_intake_generated",
                "phase": "R0",
                "action": scorecard.get("action"),
                "level_code": scorecard.get("level_code"),
                "discrepancy_count": len(discrepancies),
                "coverage_gap_count": len(gaps),
                "include_external": bool(include_external),
                "external_providers": normalized_providers,
                "created_by": created_by,
            },
            wiki_root=wiki_root,
        )
    return package


def read_r0_intake(
    deal_id: str,
    *,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    package_dir = _require_package_dir(deal_id, wiki_root=wiki_root)
    payload = deal_store.read_json(package_dir / R0_JSON_PATH, None)
    return {
        "deal_id": deal_store.validate_deal_id(deal_id),
        "intake": payload if isinstance(payload, dict) else None,
    }
