#!/usr/bin/env python3
"""Validate SIQ research packs in a report work directory."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from jsonschema import Draft202012Validator


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
NON_EVIDENCE_KEY_FINDING_STATUSES = {"assumption", "gap", "external_context"}
REVIEW_REQUIRED_FACT_STATUSES = {"assumption", "gap", "modeled_estimate"}


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


def load_schema(path: Path = SCHEMA_PATH) -> tuple[dict[str, Any] | None, str | None]:
    return load_json(path)


def json_pointer(parts: Any) -> str:
    tokens = [str(part).replace("~", "~0").replace("/", "~1") for part in parts]
    return "/" + "/".join(tokens) if tokens else "/"


def schema_failures(path: Path, data: dict[str, Any], schema: dict[str, Any] | None) -> list[str]:
    if not schema:
        return []
    validator = Draft202012Validator(schema)
    failures: list[str] = []
    for error in sorted(validator.iter_errors(data), key=lambda item: list(item.path)):
        failures.append(f"schema_validation:{path.name}:{json_pointer(error.path)}:{error.message}")
    return failures


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


def parse_confidence(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    confidence = float(value)
    if not 0 <= confidence <= 1:
        return None
    return confidence


def is_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def evidence_candidate_paths(work_dir: Path, pack_path: Path, source_file: str) -> list[Path]:
    source_path = Path(source_file)
    if source_path.is_absolute():
        return [source_path]
    candidates = [
        work_dir / source_path,
        work_dir.parent / source_path,
        pack_path.parent / source_path,
        Path.cwd() / source_path,
    ]
    result: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved not in seen:
            result.append(resolved)
            seen.add(resolved)
    return result


def parse_positive_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def line_number_resolves(path: Path, line_value: Any) -> bool:
    line = parse_positive_int(line_value)
    if line is None:
        return True
    try:
        with path.open("r", encoding="utf-8") as handle:
            for index, _line in enumerate(handle, start=1):
                if index >= line:
                    return True
    except UnicodeDecodeError:
        return True
    except OSError:
        return False
    return False


def evidence_ref_resolution(work_dir: Path, pack_path: Path, ref: dict[str, Any]) -> tuple[bool, str | None]:
    source_file = str(ref.get("source_file") or "").strip()
    if not source_file:
        return False, "missing_source_file"
    if is_url(source_file):
        return True, None
    for candidate in evidence_candidate_paths(work_dir, pack_path, source_file):
        if candidate.is_file():
            if not line_number_resolves(candidate, ref.get("md_line")):
                return False, f"md_line_out_of_range:{source_file}:{ref.get('md_line')}"
            return True, None
    return False, f"source_file_not_found:{source_file}"


def validate_evidence_refs(path: Path, work_dir: Path, data: dict[str, Any], failures: list[str]) -> None:
    for field in ("key_findings", "evidence_facts", "calculations", "risk_chains"):
        items = data.get(field)
        if not isinstance(items, list):
            continue
        for item_index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            refs = item.get("evidence_refs")
            if not isinstance(refs, list):
                continue
            for ref_index, ref in enumerate(refs):
                if not isinstance(ref, dict):
                    continue
                ok, reason = evidence_ref_resolution(work_dir, path, ref)
                if not ok:
                    failures.append(
                        f"evidence_ref_unresolvable:{item_path(path, field, item_index, f'evidence_refs[{ref_index}]')}:{reason}"
                    )


def validate_key_findings(path: Path, data: dict[str, Any], failures: list[str], warnings: list[str]) -> None:
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
            continue
        confidence = parse_confidence(item.get("confidence"))
        if confidence is None:
            failures.append(f"key_finding_invalid_confidence:{item_path(path, 'key_findings', index)}")
            continue
        fact_status = str(item.get("fact_status") or "verified_fact").strip()
        if confidence < 0.60 and data.get("review_required") is not True:
            failures.append(f"key_finding_low_confidence_requires_review:{item_path(path, 'key_findings', index)}")
        evidence_refs = item.get("evidence_refs")
        has_evidence_refs = isinstance(evidence_refs, list) and bool(evidence_refs)
        if not has_evidence_refs and fact_status not in NON_EVIDENCE_KEY_FINDING_STATUSES:
            failures.append(f"key_finding_missing_evidence_or_fact_status:{item_path(path, 'key_findings', index)}")
        if fact_status in REVIEW_REQUIRED_FACT_STATUSES and data.get("review_required") is not True and item.get("review_required") is not True:
            failures.append(f"key_finding_fact_status_requires_review:{item_path(path, 'key_findings', index)}:{fact_status}")
        if confidence < 0.75 and not has_evidence_refs and not is_present(item.get("rationale")):
            warnings.append(f"key_finding_medium_confidence_without_rationale_or_evidence:{item_path(path, 'key_findings', index)}")


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


def validate_pack(path: Path, *, work_dir: Path, schema: dict[str, Any] | None = None) -> dict[str, Any]:
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

    failures.extend(schema_failures(path, data, schema))

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

    validate_key_findings(path, data, failures, warnings)
    validate_calculations(path, data, failures)
    validate_evidence_refs(path, work_dir, data, failures)

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

    schema: dict[str, Any] | None = None
    if not SCHEMA_PATH.exists():
        failures.append(f"schema_file_missing:{SCHEMA_PATH}")
    else:
        schema, schema_error = load_schema(SCHEMA_PATH)
        if schema_error:
            failures.append(schema_error)

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
        pack_result = validate_pack(path, work_dir=work_dir, schema=schema)
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
