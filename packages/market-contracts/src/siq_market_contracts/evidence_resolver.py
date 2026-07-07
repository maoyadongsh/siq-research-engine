from __future__ import annotations

from pathlib import Path
from typing import Any


def _source_map_entries(source_map: dict[str, Any]) -> list[Any]:
    entries = source_map.get("entries") if isinstance(source_map, dict) else []
    return entries if isinstance(entries, list) else []


def _has_value(value: Any) -> bool:
    return value is not None and str(value).strip() != ""


def _first_value(*values: Any) -> Any:
    for value in values:
        if _has_value(value):
            return value
    return None


def _raw_field(payload: dict[str, Any], key: str) -> Any:
    raw = payload.get("raw") if isinstance(payload, dict) else {}
    return raw.get(key) if isinstance(raw, dict) else None


def _target_locator_kind(target: Any) -> str | None:
    text = str(target or "").strip()
    if not text:
        return None
    lower = text.lower()
    if lower.startswith("page="):
        fields = {}
        for item in text.split(";"):
            if "=" not in item:
                continue
            key, value = item.split("=", 1)
            fields[key.strip().lower()] = value.strip()
        if fields.get("page") and fields.get("table") and ((fields.get("row") and fields.get("column")) or fields.get("quote")):
            return "pdf"
    if lower.startswith(("http://", "https://")) and "#" in text and text.rsplit("#", 1)[-1].strip():
        return "html"
    if lower.startswith("xbrl:") and len([part for part in text.split(":") if part]) >= 3:
        return "xbrl"
    return None


def evidence_source_resolvability(
    evidence: dict[str, Any],
    *,
    manifest: dict[str, Any] | None = None,
    package_dir: Path | None = None,
) -> dict[str, Any]:
    if not isinstance(evidence, dict) or not evidence:
        return {"resolvable": False, "kind": None, "reason": "empty_evidence"}

    page_number = _first_value(evidence.get("page_number"), evidence.get("pdf_page_number"))
    table_index = evidence.get("table_index")
    row_index = evidence.get("row_index")
    column_index = evidence.get("column_index")
    quote = _first_value(evidence.get("quote_text"), evidence.get("quote"), evidence.get("html_snippet"))
    if _has_value(page_number) and _has_value(table_index) and ((_has_value(row_index) and _has_value(column_index)) or _has_value(quote)):
        return {"resolvable": True, "kind": "pdf_table", "reason": None}

    url = _first_value(evidence.get("url"), evidence.get("source_url"), (manifest or {}).get("source_url"))
    anchor = _first_value(evidence.get("anchor"), evidence.get("html_anchor"), _raw_field(evidence, "fact_id"))
    xpath = evidence.get("xpath")
    tag = _first_value(evidence.get("tag"), evidence.get("xbrl_tag"))
    if _has_value(url) and (_has_value(anchor) or _has_value(xpath) or _has_value(tag)):
        return {"resolvable": True, "kind": "html_xbrl", "reason": None}

    context_ref = _first_value(evidence.get("context_ref"), _raw_field(evidence, "context_ref"))
    unit_ref = _first_value(evidence.get("unit_ref"), _raw_field(evidence, "unit_ref"))
    fact_id = _first_value(evidence.get("fact_id"), evidence.get("raw_fact_id"), _raw_field(evidence, "fact_id"))
    if _has_value(tag) and _has_value(context_ref) and (_has_value(unit_ref) or _has_value(fact_id) or _has_value(anchor) or _has_value(url)):
        return {"resolvable": True, "kind": "xbrl_fact", "reason": None}

    artifact_path = _first_value(
        evidence.get("artifact_path"),
        evidence.get("local_path"),
        evidence.get("path"),
        evidence.get("table_json_path"),
    )
    line = _first_value(evidence.get("line"), evidence.get("line_number"))
    cell = evidence.get("cell")
    if _has_value(artifact_path) and (
        _has_value(line)
        or _has_value(cell)
        or (_has_value(table_index) and (_has_value(row_index) or _has_value(column_index)))
        or _has_value(quote)
        or _has_value(xpath)
        or _has_value(tag)
    ):
        if package_dir is not None:
            local = package_dir / str(artifact_path)
            if not local.exists() and not (_has_value(page_number) or _has_value(url)):
                return {"resolvable": False, "kind": "artifact", "reason": f"artifact_path_missing:{artifact_path}"}
        return {"resolvable": True, "kind": "artifact", "reason": None}

    target_kind = _target_locator_kind(evidence.get("target"))
    if target_kind:
        return {"resolvable": True, "kind": target_kind, "reason": None}
    return {"resolvable": False, "kind": None, "reason": "missing_locator"}


def is_resolvable_evidence_source(
    evidence: dict[str, Any],
    *,
    manifest: dict[str, Any] | None = None,
    package_dir: Path | None = None,
) -> bool:
    return bool(evidence_source_resolvability(evidence, manifest=manifest, package_dir=package_dir).get("resolvable"))


def iter_financial_data_items(financial_data: dict[str, Any]):
    for statement in financial_data.get("statements") or []:
        for item in statement.get("items") or []:
            yield item
    for key in ("key_metrics", "operating_metrics"):
        for item in financial_data.get(key) or []:
            yield item


def evidence_resolvability_summary(
    *,
    financial_data: dict[str, Any] | None = None,
    source_map: dict[str, Any] | None = None,
    manifest: dict[str, Any] | None = None,
    package_dir: Path | None = None,
) -> dict[str, Any]:
    metric_value_count = 0
    resolvable_metric_source_count = 0
    missing_metric_source_count = 0
    missing_metric_sources: list[str] = []
    unresolvable_metric_source_count = 0
    unresolvable_metric_sources: list[str] = []
    for item in iter_financial_data_items(financial_data or {}):
        values = item.get("values") if isinstance(item, dict) else {}
        sources = item.get("sources") if isinstance(item, dict) else {}
        if not isinstance(values, dict):
            continue
        for period_key in values:
            metric_value_count += 1
            evidence = sources.get(period_key) if isinstance(sources, dict) else None
            metric_name = str(item.get("canonical_name") or item.get("name") or "unknown")
            if not isinstance(evidence, dict) or not evidence:
                missing_metric_source_count += 1
                missing_metric_sources.append(f"{metric_name}:{period_key}")
                continue
            if is_resolvable_evidence_source(evidence, manifest=manifest, package_dir=package_dir):
                resolvable_metric_source_count += 1
            else:
                unresolvable_metric_source_count += 1
                unresolvable_metric_sources.append(f"{metric_name}:{period_key}")

    entries = _source_map_entries(source_map or {})
    resolvable_source_map_entry_count = 0
    unresolvable_source_map_entry_count = 0
    unresolvable_source_map_entries: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            unresolvable_source_map_entry_count += 1
            unresolvable_source_map_entries.append("<non_object>")
            continue
        if is_resolvable_evidence_source(entry, manifest=manifest, package_dir=package_dir):
            resolvable_source_map_entry_count += 1
        else:
            unresolvable_source_map_entry_count += 1
            unresolvable_source_map_entries.append(str(entry.get("evidence_id") or "<missing_evidence_id>"))

    source_map_entry_count = len(entries)
    if source_map_entry_count:
        resolvable_evidence_count = resolvable_source_map_entry_count
        unresolvable_evidence_count = unresolvable_source_map_entry_count
    else:
        resolvable_evidence_count = resolvable_metric_source_count
        unresolvable_evidence_count = unresolvable_metric_source_count
    denominator = resolvable_evidence_count + unresolvable_evidence_count
    return {
        "metric_value_count": metric_value_count,
        "resolvable_metric_source_count": resolvable_metric_source_count,
        "missing_metric_source_count": missing_metric_source_count,
        "missing_metric_sources": missing_metric_sources,
        "unresolvable_metric_source_count": unresolvable_metric_source_count,
        "unresolvable_metric_sources": unresolvable_metric_sources,
        "source_map_entry_count": source_map_entry_count,
        "resolvable_source_map_entry_count": resolvable_source_map_entry_count,
        "unresolvable_source_map_entry_count": unresolvable_source_map_entry_count,
        "unresolvable_source_map_entries": unresolvable_source_map_entries,
        "resolvable_evidence_count": resolvable_evidence_count,
        "unresolvable_evidence_count": unresolvable_evidence_count,
        "evidence_resolvability_ratio": round(resolvable_evidence_count / denominator, 6) if denominator else None,
    }


def missing_financial_data_evidence(financial_data: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for row in iter_financial_data_items(financial_data):
        sources = row.get("sources") if isinstance(row, dict) else None
        if not isinstance(sources, dict) or not sources:
            missing.append(str(row.get("canonical_name") or row.get("name") or "unknown"))
            continue
        for period_key, evidence in sources.items():
            if not isinstance(evidence, dict) or not evidence:
                missing.append(f"{row.get('canonical_name') or row.get('name')}:{period_key}")
    return missing


def unresolvable_financial_data_evidence(
    financial_data: dict[str, Any],
    *,
    manifest: dict[str, Any] | None = None,
    package_dir: Path | None = None,
) -> list[str]:
    unresolvable: list[str] = []
    for row in iter_financial_data_items(financial_data):
        sources = row.get("sources") if isinstance(row, dict) else None
        if not isinstance(sources, dict) or not sources:
            continue
        for period_key, evidence in sources.items():
            if not isinstance(evidence, dict) or not evidence:
                continue
            if not is_resolvable_evidence_source(evidence, manifest=manifest, package_dir=package_dir):
                unresolvable.append(f"{row.get('canonical_name') or row.get('name')}:{period_key}")
    return unresolvable
