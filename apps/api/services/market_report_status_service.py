from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any


PACKAGE_LIST_SEARCH_KEYS = (
    "package_path",
    "market",
    "filing_id",
    "ticker",
    "company_name",
    "form",
    "report_type",
    "fiscal_year",
)


def _count_value(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def market_ingestion_eval_report_payload(
    *,
    report: Any,
    report_path: str,
    markdown_path: str,
    markdown: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ok": bool(report),
        "report_path": report_path,
        "markdown_path": markdown_path,
        "report": report,
    }
    if markdown is not None:
        result["markdown"] = markdown
    return result


def load_plan_summary(load_plan: Any) -> dict[str, Any]:
    if not isinstance(load_plan, dict) or not load_plan:
        return {}
    rows = load_plan.get("rows") if isinstance(load_plan.get("rows"), list) else []
    quarantine_rows = load_plan.get("quarantine_rows") if isinstance(load_plan.get("quarantine_rows"), list) else []
    return {
        "can_import": load_plan.get("can_import"),
        "can_vector_ingest": load_plan.get("can_vector_ingest"),
        "blocked_reasons": load_plan.get("blocked_reasons") if isinstance(load_plan.get("blocked_reasons"), list) else [],
        "promotion_decisions": load_plan.get("promotion_decisions") if isinstance(load_plan.get("promotion_decisions"), dict) else {},
        "row_count": len(rows),
        "quarantine_row_count": len(quarantine_rows),
    }


def merge_load_plan_decision_into_gates(gates: dict[str, Any], load_plan: Any) -> dict[str, Any]:
    if not isinstance(load_plan, dict) or not load_plan:
        return gates
    merged = dict(gates)
    summary = load_plan_summary(load_plan)
    merged["load_plan"] = summary
    merged["can_import"] = summary.get("can_import")
    merged["can_vector_ingest"] = summary.get("can_vector_ingest")
    decisions = summary.get("promotion_decisions") if isinstance(summary.get("promotion_decisions"), dict) else {}
    hard_gate_rule_ids = list(merged.get("hard_gate_rule_ids") or [])
    soft_gate_rule_ids = list(merged.get("soft_gate_rule_ids") or [])

    def apply_target(*, target: str, blocked_key: str, can_key: str) -> None:
        if load_plan.get(can_key) is not False:
            return
        decision = decisions.get(target) if isinstance(decisions.get(target), dict) else {}
        decision_value = str(decision.get("decision") or "block")
        rule_id = f"load_plan.{target}.{decision_value}"
        merged[blocked_key] = True
        if decision_value == "review":
            if rule_id not in soft_gate_rule_ids:
                soft_gate_rule_ids.append(rule_id)
        else:
            if rule_id not in hard_gate_rule_ids:
                hard_gate_rule_ids.append(rule_id)

    apply_target(target="canonical", blocked_key="import_blocked", can_key="can_import")
    apply_target(target="retrieval", blocked_key="vector_ingest_blocked", can_key="can_vector_ingest")
    merged["hard_gate_rule_ids"] = hard_gate_rule_ids
    merged["soft_gate_rule_ids"] = soft_gate_rule_ids
    merged["force_allowed"] = bool(soft_gate_rule_ids) and not hard_gate_rule_ids
    return merged


def market_package_quality_payload(
    *,
    package_path: str,
    manifest: Any,
    quality: Any,
    financial_checks: Any,
    load_plan: Any | None = None,
    quality_gates: Any | None = None,
    source_map: Any | None = None,
    include_source_map_summary: bool = False,
) -> dict[str, Any]:
    payload = {
        "ok": True,
        "package_path": package_path,
        "manifest": manifest,
        "quality": quality,
        "financial_checks": financial_checks,
    }
    if isinstance(load_plan, dict) and load_plan:
        payload["load_plan"] = load_plan_summary(load_plan)
    if isinstance(quality_gates, dict) and quality_gates:
        payload["quality_gates"] = quality_gates
    if include_source_map_summary:
        source_entries = source_map.get("entries") if isinstance(source_map, dict) else []
        if not isinstance(source_entries, list):
            source_entries = []
        payload["source_map_summary"] = {"evidence": len(source_entries)}
    return payload


def market_package_quality_response(
    package_dir: Path,
    *,
    rel_or_abs: Callable[[Path], str],
    read_json_file: Callable[[Path, Any], Any],
    load_plan_for_package: Callable[[Path], dict[str, Any]],
    quality_gates_with_load_plan: Callable[[Path], dict[str, Any]],
    include_source_map_summary: bool = False,
) -> dict[str, Any]:
    source_map = None
    if include_source_map_summary:
        source_map = read_json_file(package_dir / "qa" / "source_map.json", {})
    manifest = read_json_file(package_dir / "manifest.json", {})
    quality = read_json_file(package_dir / "qa" / "quality_report.json", {})
    financial_checks = read_json_file(package_dir / "metrics" / "financial_checks.json", {})
    load_plan = load_plan_for_package(package_dir)
    quality_gates = quality_gates_with_load_plan(package_dir)
    return market_package_quality_payload(
        package_path=rel_or_abs(package_dir),
        manifest=manifest,
        quality=quality,
        financial_checks=financial_checks,
        load_plan=load_plan,
        quality_gates=quality_gates,
        source_map=source_map,
        include_source_map_summary=include_source_map_summary,
    )


def market_package_list_payload(
    *,
    market_codes: list[str],
    package_summaries: list[dict[str, Any]],
    roots: dict[str, str],
    query: str = "",
    limit: int = 80,
) -> dict[str, Any]:
    normalized_limit = max(1, min(int(limit or 80), 500))
    normalized_query = str(query or "").strip().lower()
    packages: list[dict[str, Any]] = []
    for summary in package_summaries:
        if not isinstance(summary, dict):
            continue
        haystack = " ".join(str(summary.get(key) or "") for key in PACKAGE_LIST_SEARCH_KEYS).lower()
        if normalized_query and normalized_query not in haystack:
            continue
        packages.append(summary)
    packages.sort(key=lambda item: str(item.get("published_at") or item.get("period_end") or ""), reverse=True)
    limited_packages = packages[:normalized_limit]
    return {
        "ok": True,
        "market": market_codes[0] if len(market_codes) == 1 else None,
        "markets": market_codes,
        "roots": roots,
        "count": len(limited_packages),
        "packages": limited_packages,
    }


def market_document_full_status_payload(
    *,
    market_codes: list[str],
    document_full_roots: dict[str, Path],
    import_scripts: dict[str, Path],
    market_databases: dict[str, str],
    schemas: dict[str, str],
    rel_or_abs: Callable[[Path], str],
    db_status_for_market: Callable[[str], dict[str, Any]],
    record_fact_counts: Callable[[str, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    markets: dict[str, dict[str, Any]] = {}
    for code in market_codes:
        root = document_full_roots[code]
        script = import_scripts[code]
        market_payload = {
            "document_full_root": rel_or_abs(root),
            "document_full_root_exists": root.exists(),
            "script": rel_or_abs(script),
            "script_exists": script.is_file(),
            "database": market_databases.get(code),
            "schema": schemas.get(code),
        }
        db_status = db_status_for_market(code)
        if db_status:
            market_payload["postgres"] = db_status
            if record_fact_counts is not None:
                record_fact_counts(code, db_status)
        markets[code] = market_payload
    return {"ok": True, "markets": markets}


def _list_from_mapping(value: Any, key: str) -> list[Any]:
    items = value.get(key) if isinstance(value, dict) else []
    return items if isinstance(items, list) else []


def _dimension_fact_evidence(entry: Any) -> dict[str, Any]:
    if not isinstance(entry, dict):
        return {}
    keys = (
        "evidence_id",
        "source_type",
        "section_id",
        "xbrl_tag",
        "context_ref",
        "html_anchor",
        "local_path",
        "source_url",
        "target",
        "quote_text",
    )
    return {key: entry[key] for key in keys if entry.get(key) is not None}


def _dimension_fact_sample(fact: Any, evidence: Any) -> dict[str, Any]:
    if not isinstance(fact, dict):
        return {}
    value = fact.get("value_numeric")
    if value is None:
        value = fact.get("value_text")
    period = {
        "start": fact.get("period_start"),
        "end": fact.get("period_end"),
        "instant": fact.get("instant"),
        "fiscal_year": fact.get("fiscal_year"),
        "duration_days": fact.get("duration_days"),
    }
    return {
        "fact_id": fact.get("fact_id"),
        "concept": fact.get("concept"),
        "label": fact.get("label"),
        "value": value,
        "unit": fact.get("unit") or fact.get("unit_ref"),
        "period": {key: item for key, item in period.items() if item is not None},
        "context": fact.get("context_ref"),
        "dimensions": fact.get("dimensions"),
        "anchor": fact.get("html_anchor"),
        "evidence": _dimension_fact_evidence(evidence),
    }


def _dimension_fact_samples(
    facts: list[Any],
    source_map: list[Any],
    *,
    limit: int = 80,
) -> tuple[int, list[dict[str, Any]]]:
    dimension_facts = [
        fact
        for fact in facts
        if isinstance(fact, dict) and isinstance(fact.get("dimensions"), dict) and fact.get("dimensions")
    ]
    sample_facts = dimension_facts[: max(0, limit)]
    fact_ids = {str(fact.get("fact_id")) for fact in sample_facts if fact.get("fact_id")}
    anchors = {str(fact.get("html_anchor")) for fact in sample_facts if fact.get("html_anchor")}
    evidence_by_fact_id: dict[str, dict[str, Any]] = {}
    evidence_by_anchor: dict[str, dict[str, Any]] = {}
    for entry in source_map:
        if not isinstance(entry, dict):
            continue
        raw = entry.get("raw") if isinstance(entry.get("raw"), dict) else {}
        fact_id = str(entry.get("fact_id") or raw.get("fact_id") or "")
        anchor = str(entry.get("html_anchor") or "")
        if fact_id in fact_ids and fact_id not in evidence_by_fact_id:
            evidence_by_fact_id[fact_id] = entry
        if anchor in anchors and anchor not in evidence_by_anchor:
            evidence_by_anchor[anchor] = entry
    samples = [
        _dimension_fact_sample(
            fact,
            evidence_by_fact_id.get(str(fact.get("fact_id") or ""))
            or evidence_by_anchor.get(str(fact.get("html_anchor") or "")),
        )
        for fact in sample_facts
    ]
    return len(dimension_facts), samples


def _positive_count(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _semantic_rule_status_from_log(log: Any) -> tuple[str, dict[str, Any], str]:
    if not isinstance(log, dict):
        return "missing", {}, "规则语义层未生成"
    counts = log.get("counts") if isinstance(log.get("counts"), dict) else {}
    inputs = log.get("inputs") if isinstance(log.get("inputs"), dict) else {}
    if not counts and not inputs:
        return "missing", counts, "规则语义层未生成，仅有 Wiki 占位文件"
    if not inputs:
        return "missing", counts, "规则语义层缺少输入指纹，需重新生成"
    if _positive_count(counts.get("segments")) <= 0 or _positive_count(counts.get("evidence")) <= 0:
        return "missing", counts, "规则语义层缺少有效 segments/evidence，需重新生成"
    return "ready", counts, "规则语义增强已生成"


def us_sec_semantic_status_for_package(
    package_dir: Path,
    *,
    read_json_file: Callable[[Path, Any], Any],
) -> dict[str, Any]:
    company_dir = package_dir.parent.parent if package_dir.parent.name == "reports" else package_dir.parent
    if not company_dir.name:
        return {}
    company_json = read_json_file(company_dir / "company.json", {})
    company_map = company_json if isinstance(company_json, dict) else {}
    report_id = str(company_map.get("primary_report_id") or package_dir.name or "2025-annual")
    semantic_dir = company_dir / "semantic"
    required = [
        "subject_profile.json",
        "segments.json",
        "facts.json",
        "relations.json",
        "claims.json",
        "retrieval_index.json",
        "note_links.json",
        "evidence_semantic.json",
        "extraction_log.json",
    ]
    missing = [name for name in required if not (semantic_dir / name).is_file()]
    log = read_json_file(semantic_dir / "extraction_log.json", {}) if not missing or (semantic_dir / "extraction_log.json").is_file() else {}
    rule_status, counts, message = _semantic_rule_status_from_log(log)
    if missing:
        rule_status = "missing"
        message = "Wiki语义增强未生成"

    llm_dir = semantic_dir / "llm" / report_id
    llm_required = [
        "enrichment.json",
        "business_profile.json",
        "claims.json",
        "risks.json",
        "events.json",
        "review_queue.json",
        "extraction_log.json",
    ]
    llm_missing = [name for name in llm_required if not (llm_dir / name).is_file()]
    llm_log = read_json_file(llm_dir / "extraction_log.json", {}) if not llm_missing or (llm_dir / "extraction_log.json").is_file() else {}
    llm_counts = llm_log.get("counts") if isinstance(llm_log, dict) and isinstance(llm_log.get("counts"), dict) else {}
    llm_status = "missing" if llm_missing else "ready"
    return {
        "status": rule_status,
        "companyDir": company_dir.name,
        "reportId": report_id,
        "missing": missing,
        "counts": counts,
        "quality": log.get("quality") if isinstance(log, dict) and isinstance(log.get("quality"), dict) else {},
        "warnings": log.get("warnings") if isinstance(log, dict) and isinstance(log.get("warnings"), list) else [],
        "llm": {
            "status": llm_status,
            "reportId": report_id,
            "outputDir": str(llm_dir),
            "missing": llm_missing,
            "counts": llm_counts,
            "message": "项目设置模型语义增强已生成" if llm_status == "ready" else "LLM 语义增强未生成",
        },
        "message": message,
    }


def us_sec_package_detail_response(
    package_dir: Path,
    *,
    rel_or_abs: Callable[[Path], str],
    read_json_file: Callable[[Path, Any], Any],
    quality_gates_for_package: Callable[[Path], dict[str, Any]],
) -> dict[str, Any]:
    manifest = read_json_file(package_dir / "manifest.json", {})
    manifest_map = manifest if isinstance(manifest, dict) else {}
    quality = read_json_file(package_dir / "qa" / "quality_report.json", {})
    financial_data = read_json_file(package_dir / "metrics" / "financial_data.json", {})
    financial_checks = read_json_file(package_dir / "metrics" / "financial_checks.json", {})
    sections = _list_from_mapping(read_json_file(package_dir / "sections.json", {}), "sections")
    tables = _list_from_mapping(read_json_file(package_dir / "tables" / "table_index.json", {}), "tables")
    metrics = _list_from_mapping(read_json_file(package_dir / "metrics" / "normalized_metrics.json", {}), "metrics")
    source_map = _list_from_mapping(read_json_file(package_dir / "qa" / "source_map.json", {}), "entries")
    facts = _list_from_mapping(read_json_file(package_dir / "xbrl" / "facts_raw.json", {}), "facts")
    dimension_fact_count, dimension_facts = _dimension_fact_samples(facts, source_map)
    # Compatibility view for existing clients. Consolidated metric selection intentionally excludes most dimensions.
    dimension_metrics = [item for item in metrics if isinstance(item, dict) and item.get("dimensions")]
    checks = financial_checks.get("checks") if isinstance(financial_checks, dict) else []
    if not isinstance(checks, list):
        checks = []
    bridge_checks = [
        check for check in checks
        if isinstance(check, dict) and (
            str(check.get("rule_id") or "").startswith(("bs.", "is.", "cf.", "cross."))
            or str(check.get("rule_name") or "").lower().find("cash") >= 0
        )
    ]
    bridge_summary: dict[str, int] = {}
    for check in bridge_checks:
        status = str(check.get("status") or "unknown")
        bridge_summary[status] = bridge_summary.get(status, 0) + 1
    default_markdown = ""
    if (package_dir / "parser" / "report_complete.md").is_file():
        default_markdown = "parser/report_complete.md"
    elif (package_dir / "sections" / "report_complete.md").is_file():
        default_markdown = "sections/report_complete.md"
    elif sections:
        first_section = sections[0] if isinstance(sections[0], dict) else {}
        default_markdown = f"sections/{first_section.get('file')}"
    return {
        "package_path": rel_or_abs(package_dir),
        "parser_result_dir": manifest_map.get("parser_result_dir") or "",
        "parser_result_task_id": manifest_map.get("parser_result_task_id") or "",
        "semantic_status": us_sec_semantic_status_for_package(package_dir, read_json_file=read_json_file),
        "manifest": manifest,
        "quality": quality,
        "quality_gates": quality_gates_for_package(package_dir),
        "financial_data": financial_data,
        "financial_checks": financial_checks,
        "bridge_checks": {
            "overall_status": financial_checks.get("overall_status") if isinstance(financial_checks, dict) else None,
            "summary": bridge_summary,
            "checks": bridge_checks[:120],
        },
        "counts": {
            "sections": len(sections),
            "tables": len(tables),
            "metrics": len(metrics),
            "evidence": len(source_map),
            "dimension_facts": dimension_fact_count,
            "dimension_metrics": len(dimension_metrics),
        },
        "sections": sections,
        "tables": tables[:200],
        "metrics": metrics[:300],
        "dimension_facts": dimension_facts,
        "dimension_metrics": dimension_metrics[:80],
        "preview": {
            "raw_html": "raw/filing.htm" if (package_dir / "raw" / "filing.htm").is_file() else "",
            "default_markdown": default_markdown,
        },
    }


def latest_case_item_for_ticker(case_set: Any, ticker: str) -> dict[str, Any] | None:
    normalized_ticker = str(ticker or "").strip().upper()
    if not normalized_ticker:
        return None
    items = case_set.get("items") if isinstance(case_set, dict) else []
    if not isinstance(items, list):
        return None
    candidates = [
        item for item in items
        if isinstance(item, dict) and str(item.get("ticker") or "").strip().upper() == normalized_ticker
    ]
    if not candidates:
        return None
    return sorted(
        candidates,
        key=lambda item: (str(item.get("filing_date") or ""), str(item.get("period_end") or "")),
        reverse=True,
    )[0]


def us_sec_case_set_status_payload(
    *,
    case_set: Any,
    ingest_report: Any,
    case_set_path: str,
    ingest_report_path: str,
    semantic_status_for_item: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    items = case_set.get("items") if isinstance(case_set, dict) else []
    if not isinstance(items, list):
        items = []

    quality: dict[str, int] = {}
    total_counts = {
        "xbrl_fact_count": 0,
        "normalized_metric_count": 0,
        "section_count": 0,
        "table_count": 0,
    }
    by_ticker = []
    for item in items:
        if not isinstance(item, dict):
            continue
        status = str(item.get("quality_status") or "unknown")
        quality[status] = quality.get(status, 0) + 1
        summary = item.get("quality_summary") if isinstance(item.get("quality_summary"), dict) else {}
        total_counts["xbrl_fact_count"] += _count_value(summary.get("xbrl_fact_count"))
        total_counts["normalized_metric_count"] += _count_value(summary.get("normalized_metric_count"))
        total_counts["section_count"] += _count_value(summary.get("section_count"))
        total_counts["table_count"] += _count_value(summary.get("table_count"))
        row = {
            "ticker": item.get("ticker"),
            "company_name": item.get("company_name"),
            "fiscal_year": item.get("fiscal_year"),
            "period_end": item.get("period_end"),
            "filing_date": item.get("filing_date"),
            "quality_status": status,
            "retrieval_status": item.get("retrieval_status"),
            "wiki_ready": item.get("wiki_ready"),
            "retrieval_issues": item.get("retrieval_issues") or [],
            "quality_summary": summary,
            "package_path": item.get("package_path"),
            "full_document_paths": item.get("full_document_paths") if isinstance(item.get("full_document_paths"), dict) else {},
            "parser_result_dir": item.get("parser_result_dir"),
            "parser_result_task_id": item.get("parser_result_task_id"),
        }
        if semantic_status_for_item is not None:
            semantic_status = semantic_status_for_item(item)
            if semantic_status:
                row["semantic_status"] = semantic_status
        by_ticker.append(row)

    relationship = {}
    if isinstance(ingest_report, dict):
        relationship = {
            "generated_at": ingest_report.get("generated_at"),
            "summary": ingest_report.get("summary") or {},
            "package_count": ingest_report.get("package_count"),
            "collection": ingest_report.get("collection"),
            "batch_tag": ingest_report.get("batch_tag"),
        }

    return {
        "case_set_path": case_set_path,
        "ingest_report_path": ingest_report_path,
        "company_count": len(by_ticker),
        "quality": quality,
        "counts": total_counts,
        "items": by_ticker,
        "ingest_report": relationship,
    }
