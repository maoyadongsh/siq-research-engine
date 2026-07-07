#!/usr/bin/env python3
"""Validate SIQ research packs in a report work directory."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ALLOWED_AGENT_IDS = {
    "evidence_curator",
    "financial_modeler",
    "business_strategy_researcher",
    "industry_peer_researcher",
    "governance_risk_researcher",
    "editor_in_chief",
}

REQUIRED_RESEARCH_AGENT_IDS = {
    "evidence_curator",
    "financial_modeler",
    "business_strategy_researcher",
    "industry_peer_researcher",
    "governance_risk_researcher",
}

REQUIRED_TOP_LEVEL_FIELDS = [
    "schema_version",
    "agent_id",
    "company_id",
    "report_year",
    "generated_at",
    "input_files",
    "coverage",
    "key_findings",
    "evidence_facts",
    "calculations",
    "risk_chains",
    "tracking_signals",
    "external_sources",
    "missing_inputs",
    "review_required",
    "prohibited_content_hits",
]

ARRAY_FIELDS = [
    "input_files",
    "key_findings",
    "evidence_facts",
    "calculations",
    "risk_chains",
    "tracking_signals",
    "external_sources",
    "missing_inputs",
    "prohibited_content_hits",
]

COMPLETE_EXTERNAL_SOURCE_FIELDS = ["provider", "query", "url", "title"]
SCHEMA_PATH = Path(__file__).resolve().parent.parent / "templates" / "research_pack.schema.json"


def is_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return True


def load_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        return None, f"read_failed:{path}:{exc}"
    except json.JSONDecodeError as exc:
        return None, f"json_parse_failed:{path}:{exc.msg}:line_{exc.lineno}"
    if not isinstance(data, dict):
        return None, f"json_root_not_object:{path}"
    return data, None


def parse_datetime(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def valid_report_year(value: Any) -> bool:
    if isinstance(value, int):
        year = value
    elif isinstance(value, str) and value.isdigit() and len(value) == 4:
        year = int(value)
    else:
        return False
    return 1900 <= year <= 2100


def has_complete_external_source(items: Any) -> bool:
    if not isinstance(items, list):
        return False
    for item in items:
        if not isinstance(item, dict):
            continue
        if all(is_present(item.get(field)) for field in COMPLETE_EXTERNAL_SOURCE_FIELDS):
            return True
    return False


def has_external_source_gap(missing_inputs: Any) -> bool:
    if not isinstance(missing_inputs, list):
        return False
    keywords = (
        "external_sources",
        "external source",
        "industry_peer_external_sources",
        "provider",
        "query",
        "url",
        "同业",
        "行业",
        "外部来源",
        "外部检索",
    )
    for item in missing_inputs:
        text = json.dumps(item, ensure_ascii=False) if isinstance(item, (dict, list)) else str(item)
        lowered = text.lower()
        if any(keyword in lowered for keyword in keywords):
            return True
    return False


def item_path(path: Path, field: str, index: int, subfield: str | None = None) -> str:
    suffix = f"{field}[{index}]"
    if subfield:
        suffix += f".{subfield}"
    return f"{path.name}:{suffix}"


def validate_key_findings(path: Path, data: dict[str, Any], failures: list[str]) -> None:
    findings = data.get("key_findings")
    if not isinstance(findings, list):
        return
    for index, item in enumerate(findings):
        if not isinstance(item, dict):
            failures.append(f"key_finding_not_object:{item_path(path, 'key_findings', index)}")
            continue
        section_ids = item.get("section_ids")
        if not isinstance(section_ids, list) or not section_ids or not all(is_present(value) for value in section_ids):
            failures.append(f"key_finding_missing_section_ids:{item_path(path, 'key_findings', index)}")
        if not is_present(item.get("claim")):
            failures.append(f"key_finding_missing_claim:{item_path(path, 'key_findings', index)}")
        if "confidence" not in item or not is_present(item.get("confidence")):
            failures.append(f"key_finding_missing_confidence:{item_path(path, 'key_findings', index)}")


def validate_calculations(path: Path, data: dict[str, Any], failures: list[str]) -> None:
    calculations = data.get("calculations")
    if not isinstance(calculations, list):
        return
    for index, item in enumerate(calculations):
        if not isinstance(item, dict):
            failures.append(f"calculation_not_object:{item_path(path, 'calculations', index)}")
            continue
        for field in ["formula", "inputs", "output", "evidence_refs"]:
            if field not in item or not is_present(item.get(field)):
                failures.append(f"calculation_missing_{field}:{item_path(path, 'calculations', index, field)}")


def validate_pack(path: Path) -> dict[str, Any]:
    data, load_error = load_json(path)
    failures: list[str] = []
    warnings: list[str] = []

    if load_error:
        return {
            "path": str(path),
            "agent_id": None,
            "ok": False,
            "failures": [load_error],
            "warnings": warnings,
        }

    assert data is not None
    agent_id = data.get("agent_id")

    missing_fields = [field for field in REQUIRED_TOP_LEVEL_FIELDS if field not in data]
    for field in missing_fields:
        failures.append(f"missing_field:{path.name}:{field}")

    if data.get("schema_version") != "1.0":
        failures.append(f"schema_version_invalid:{path.name}:{data.get('schema_version')}")

    if agent_id not in ALLOWED_AGENT_IDS:
        failures.append(f"agent_id_invalid:{path.name}:{agent_id}")

    if not is_present(data.get("company_id")):
        failures.append(f"company_id_missing:{path.name}")

    if "report_year" in data and not valid_report_year(data.get("report_year")):
        failures.append(f"report_year_invalid:{path.name}:{data.get('report_year')}")

    if "generated_at" in data and not parse_datetime(data.get("generated_at")):
        failures.append(f"generated_at_invalid:{path.name}:{data.get('generated_at')}")

    for field in ARRAY_FIELDS:
        if field in data and not isinstance(data.get(field), list):
            failures.append(f"field_not_array:{path.name}:{field}")

    if "coverage" in data and not isinstance(data.get("coverage"), dict):
        failures.append(f"coverage_not_object:{path.name}")

    if "review_required" in data and not isinstance(data.get("review_required"), bool):
        failures.append(f"review_required_not_boolean:{path.name}")

    validate_key_findings(path, data, failures)
    validate_calculations(path, data, failures)

    prohibited_hits = data.get("prohibited_content_hits")
    if isinstance(prohibited_hits, list) and prohibited_hits:
        failures.append(f"prohibited_content_hits_present:{path.name}:{len(prohibited_hits)}")

    if agent_id == "industry_peer_researcher":
        if not has_complete_external_source(data.get("external_sources")) and not has_external_source_gap(data.get("missing_inputs")):
            failures.append(
                "industry_peer_external_sources_missing:"
                f"{path.name}:requires_provider_query_url_title_or_missing_input_gap"
            )

    if path.stem in ALLOWED_AGENT_IDS and path.stem != agent_id:
        warnings.append(f"filename_agent_id_mismatch:{path.name}:{agent_id}")

    return {
        "path": str(path),
        "agent_id": agent_id if isinstance(agent_id, str) else None,
        "ok": not failures,
        "failures": failures,
        "warnings": warnings,
    }


def validate_work_dir(work_dir: Path, require_all_packs: bool = True) -> dict[str, Any]:
    failures: list[str] = []
    warnings: list[str] = []
    packs: list[dict[str, Any]] = []

    if not SCHEMA_PATH.exists():
        failures.append(f"schema_file_missing:{SCHEMA_PATH}")

    if not work_dir.exists():
        failures.append(f"work_dir_missing:{work_dir}")
        return build_result(work_dir, [], packs, failures, warnings)
    if not work_dir.is_dir():
        failures.append(f"work_dir_not_directory:{work_dir}")
        return build_result(work_dir, [], packs, failures, warnings)

    research_packs_dir = work_dir / "research_packs"
    if not research_packs_dir.exists():
        failures.append(f"research_packs_dir_missing:{research_packs_dir}")
        return build_result(work_dir, [], packs, failures, warnings)
    if not research_packs_dir.is_dir():
        failures.append(f"research_packs_path_not_directory:{research_packs_dir}")
        return build_result(work_dir, [], packs, failures, warnings)

    json_files = sorted(research_packs_dir.glob("*.json"))
    if not json_files:
        failures.append(f"research_pack_json_missing:{research_packs_dir}/*.json")
        return build_result(work_dir, json_files, packs, failures, warnings)

    seen_agents: dict[str, list[str]] = {}
    for path in json_files:
        pack_result = validate_pack(path)
        packs.append(pack_result)
        failures.extend(pack_result["failures"])
        warnings.extend(pack_result["warnings"])
        agent_id = pack_result.get("agent_id")
        if isinstance(agent_id, str):
            seen_agents.setdefault(agent_id, []).append(str(path))

    for agent_id, paths in sorted(seen_agents.items()):
        if len(paths) > 1:
            failures.append(f"duplicate_agent_pack:{agent_id}:{len(paths)}")

    if require_all_packs:
        missing_agents = sorted(REQUIRED_RESEARCH_AGENT_IDS - set(seen_agents))
        for agent_id in missing_agents:
            failures.append(f"missing_required_pack:{agent_id}")

    return build_result(work_dir, json_files, packs, failures, warnings)


def build_result(
    work_dir: Path,
    json_files: list[Path],
    packs: list[dict[str, Any]],
    failures: list[str],
    warnings: list[str],
) -> dict[str, Any]:
    seen_agents = sorted(
        {
            str(pack.get("agent_id"))
            for pack in packs
            if isinstance(pack.get("agent_id"), str)
        }
    )
    return {
        "ok": not failures,
        "work_dir": str(work_dir),
        "research_packs_dir": str(work_dir / "research_packs"),
        "schema_path": str(SCHEMA_PATH),
        "checked_files": [str(path) for path in json_files],
        "allowed_agent_ids": sorted(ALLOWED_AGENT_IDS),
        "required_research_agent_ids": sorted(REQUIRED_RESEARCH_AGENT_IDS),
        "failures": failures,
        "warnings": warnings,
        "packs": packs,
        "metrics": {
            "pack_count": len(json_files),
            "valid_pack_count": sum(1 for pack in packs if pack.get("ok") is True),
            "agents_seen": seen_agents,
            "missing_required_agent_ids": sorted(REQUIRED_RESEARCH_AGENT_IDS - set(seen_agents)),
        },
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate SIQ research_packs/*.json under a report work directory."
    )
    parser.add_argument(
        "work_dir",
        type=Path,
        help="Report work directory, for example data/wiki/companies/<company>/analysis/.work/<report_slug>",
    )
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="Validate present packs without failing on missing required research-agent packs.",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Print compact one-line JSON.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    result = validate_work_dir(args.work_dir, require_all_packs=not args.allow_partial)
    if args.compact:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
