from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .evidence_gates import (
    GATE_CONTRACT_VERSION,
    LOCAL_UPLOADED_TIER,
    OFFICIAL_EVIDENCE_TIERS,
    OFFICIAL_ISSUER_TIER,
    OFFICIAL_REGULATOR_HOST_SUFFIXES_BY_MARKET,
    OFFICIAL_REGULATOR_TIER,
    PROMOTION_TARGETS,
    RECOGNIZED_VENDOR_TIER,
    REVIEW_SOURCE_TIERS,
    SOURCE_MANIFEST_VERSION,
    UNVERIFIED_WEB_TIER,
    GateDecision,
    GateMode,
    GateSeverity,
    PromotionTarget,
    _aggregate_gate_decisions,
    _gate_decisions_for_severity,
    _gate_mode_for_severity,
    _gate_results_for_issue,
    _gate_rule_ids,
    source_manifest_summary,
)

from .evidence_hashing import (
    DERIVED_PLAN_FILES,
    PACKAGE_FILE_PATHS,
    compute_artifact_hashes,
    market_package_paths,
    read_json,
    sha256_file,
    stable_id,
    stable_parse_run_id,
    write_json,
)
from .evidence_resolver import (
    evidence_resolvability_summary,
    evidence_source_resolvability,
    is_resolvable_evidence_source,
    iter_financial_data_items,
    missing_financial_data_evidence,
    unresolvable_financial_data_evidence,
)
from .evidence_value_verification import evidence_value_verification_summary


SCHEMA_VERSION = "market_evidence_package_v1"

REQUIRED_MANIFEST_FIELDS = (
    "schema_version",
    "market",
    "filing_id",
    "company_id",
    "ticker",
    "company_name",
    "source_id",
    "form",
    "report_type",
    "fiscal_year",
    "fiscal_period",
    "period_end",
    "published_at",
    "source_url",
    "local_source_path",
    "accounting_standard",
    "parser_version",
    "rules_version",
    "quality_status",
    "artifact_hashes",
)

REQUIRED_DIRECTORIES = ("raw", "sections", "tables", "xbrl", "metrics", "qa")
REQUIRED_FILES = (
    "manifest.json",
    "README.md",
    "metrics/financial_data.json",
    "metrics/financial_checks.json",
    "qa/quality_report.json",
    "qa/source_map.json",
)
@dataclass
class EvidencePackageValidation:
    package_dir: Path
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    manifest: dict[str, Any] = field(default_factory=dict)
    artifact_hashes: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "package_dir": str(self.package_dir),
            "ok": self.ok,
            "errors": self.errors,
            "warnings": self.warnings,
            "manifest": self.manifest,
            "artifact_hashes": self.artifact_hashes,
        }


def _quality_count(quality: dict[str, Any], key: str, summary_key: str | None = None) -> Any:
    if not isinstance(quality, dict):
        return None
    if quality.get(key) is not None:
        return quality.get(key)
    summary = quality.get("summary") if isinstance(quality.get("summary"), dict) else {}
    return summary.get(summary_key or key)


def _load_plan_summary(load_plan: dict[str, Any]) -> dict[str, Any]:
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


def _source_map_entries(source_map: dict[str, Any]) -> list[Any]:
    entries = source_map.get("entries") if isinstance(source_map, dict) else []
    return entries if isinstance(entries, list) else []


def _normalized_metrics(payload: dict[str, Any]) -> list[Any]:
    metrics = payload.get("metrics") if isinstance(payload, dict) else []
    return metrics if isinstance(metrics, list) else []


def _tables(payload: dict[str, Any]) -> list[Any]:
    tables = payload.get("tables") if isinstance(payload, dict) else []
    return tables if isinstance(tables, list) else []


def _list_field(payload: dict[str, Any], key: str) -> list[str]:
    value = payload.get(key) if isinstance(payload, dict) else []
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item or "").strip()]


def _quality_status(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"pass", "passed", "ok", "ready", "success"}:
        return "pass"
    if text in {"fail", "failed", "error", "critical"}:
        return "fail"
    if text in {"warning", "warn", "needs_review", "review"}:
        return "warning"
    return "unknown"


def _required_statement_status(quality: dict[str, Any], financial_data: dict[str, Any]) -> dict[str, str]:
    raw_status = quality.get("required_statement_status") if isinstance(quality, dict) else {}
    if isinstance(raw_status, dict) and raw_status:
        normalized = {str(key): str(value or "unknown") for key, value in raw_status.items()}
        return {
            statement: normalized.get(statement, "missing")
            for statement in ("income_statement", "balance_sheet", "cash_flow_statement")
        }

    statements = (financial_data.get("statements") if isinstance(financial_data, dict) else []) or []
    present = {
        str(statement.get("statement_type") or "")
        for statement in statements
        if isinstance(statement, dict)
    }
    return {
        statement: "present" if statement in present else "missing"
        for statement in ("income_statement", "balance_sheet", "cash_flow_statement")
    }


def _evidence_coverage_ratio(
    quality: dict[str, Any],
    financial_data: dict[str, Any],
    *,
    manifest: dict[str, Any] | None = None,
    source_map: dict[str, Any] | None = None,
    package_dir: Path | None = None,
) -> float | None:
    metric_value_count = 0
    covered = 0
    for item in iter_financial_data_items(financial_data if isinstance(financial_data, dict) else {}):
        values = item.get("values") if isinstance(item, dict) else {}
        sources = item.get("sources") if isinstance(item, dict) else {}
        if not isinstance(values, dict):
            continue
        for period_key in values:
            metric_value_count += 1
            evidence = sources.get(period_key) if isinstance(sources, dict) else None
            if isinstance(evidence, dict) and is_resolvable_evidence_source(evidence, manifest=manifest, package_dir=package_dir):
                covered += 1
    if metric_value_count:
        return round(covered / metric_value_count, 4)

    raw_ratio = quality.get("evidence_coverage_ratio") if isinstance(quality, dict) else None
    if isinstance(raw_ratio, int | float):
        return max(0.0, min(float(raw_ratio), 1.0))
    summary = evidence_resolvability_summary(
        financial_data=financial_data if isinstance(financial_data, dict) else {},
        source_map=source_map,
        manifest=manifest,
        package_dir=package_dir,
    )
    if summary["source_map_entry_count"]:
        return summary["evidence_resolvability_ratio"]
    return None


def _artifact_hash_check(package_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    listed = manifest.get("artifact_hashes") if isinstance(manifest, dict) else {}
    if not isinstance(listed, dict) or not listed:
        return {"status": "missing", "mismatches": [], "missing": []}

    computed = compute_artifact_hashes(package_dir)
    mismatches: list[str] = []
    missing: list[str] = []
    for rel, expected_hash in listed.items():
        if rel == "manifest.json":
            continue
        if rel in DERIVED_PLAN_FILES:
            continue
        actual_hash = computed.get(str(rel))
        if actual_hash is None:
            missing.append(str(rel))
        elif str(actual_hash) != str(expected_hash):
            mismatches.append(str(rel))
    status = "ok" if not mismatches and not missing else "mismatch"
    return {"status": status, "mismatches": mismatches, "missing": missing}


def build_quality_gates(
    package_dir: Path,
    *,
    manifest: dict[str, Any] | None = None,
    quality: dict[str, Any] | None = None,
    financial_data: dict[str, Any] | None = None,
    financial_checks: dict[str, Any] | None = None,
) -> dict[str, Any]:
    package_dir = package_dir.resolve()
    manifest = manifest if isinstance(manifest, dict) else read_json(package_dir / "manifest.json", {})
    quality = quality if isinstance(quality, dict) else read_json(package_dir / "qa" / "quality_report.json", {})
    financial_data = financial_data if isinstance(financial_data, dict) else read_json(package_dir / "metrics" / "financial_data.json", {})
    financial_checks = financial_checks if isinstance(financial_checks, dict) else read_json(package_dir / "metrics" / "financial_checks.json", {})
    source_map = read_json(package_dir / "qa" / "source_map.json", {})

    required_status = _required_statement_status(quality, financial_data)
    missing_required = [
        statement
        for statement, status in required_status.items()
        if str(status).lower() not in {"present", "pass", "ok"}
    ]
    artifact_hash = _artifact_hash_check(package_dir, manifest)
    parser_warnings = _list_field(quality, "parser_warnings")
    rule_warnings = _list_field(quality, "rule_warnings")
    rule_advisories = _list_field(quality, "rule_advisories")
    critical_warnings = _list_field(quality, "critical_warnings")
    resolvability = evidence_resolvability_summary(
        financial_data=financial_data,
        source_map=source_map if isinstance(source_map, dict) else {},
        manifest=manifest,
        package_dir=package_dir,
    )
    value_verification = evidence_value_verification_summary(
        financial_data=financial_data,
        manifest=manifest,
        package_dir=package_dir,
    )
    source_summary = source_manifest_summary(manifest=manifest)
    missing_metric_source_count = resolvability["missing_metric_source_count"]
    unresolvable_evidence_count = resolvability["unresolvable_evidence_count"]
    evidence_resolvability_ratio = resolvability["evidence_resolvability_ratio"]
    value_verification_issue_count = value_verification["issue_count"]
    source_hard_issue_count = source_summary["hard_issue_count"]
    source_review_issue_count = source_summary["review_issue_count"]

    base_status = _quality_status(
        quality.get("overall_status")
        or manifest.get("quality_status")
        or financial_checks.get("overall_status")
    )
    if (
        artifact_hash["status"] == "mismatch"
        or critical_warnings
        or base_status == "fail"
        or missing_metric_source_count
        or source_hard_issue_count
        or (evidence_resolvability_ratio is not None and evidence_resolvability_ratio < 0.8)
    ):
        overall_status = "fail"
    elif (
        base_status == "warning"
        or missing_required
        or artifact_hash["status"] == "missing"
        or parser_warnings
        or rule_warnings
        or unresolvable_evidence_count
        or value_verification_issue_count
        or source_review_issue_count
    ):
        overall_status = "warning"
    elif base_status == "pass":
        overall_status = "pass"
    else:
        overall_status = "unknown"

    block_reasons: list[str] = []
    if missing_required:
        block_reasons.append("required statements missing")
    if artifact_hash["status"] == "missing":
        block_reasons.append("artifact hashes missing")
    if artifact_hash["status"] == "mismatch":
        block_reasons.append("artifact hash mismatch")
    if critical_warnings:
        block_reasons.append("critical warnings present")
    if parser_warnings or rule_warnings:
        block_reasons.append("parser or rule warnings present")
    if missing_metric_source_count:
        block_reasons.append("missing evidence present")
    if unresolvable_evidence_count:
        block_reasons.append("unresolvable evidence present")
    if evidence_resolvability_ratio is not None and evidence_resolvability_ratio < 0.8:
        block_reasons.append("evidence resolvability below 80%")
    if value_verification_issue_count:
        block_reasons.append("evidence value verification failed")
    if source_hard_issue_count:
        block_reasons.append("official source verification failed")
    if source_review_issue_count:
        block_reasons.append("source manifest requires review")
    if overall_status not in {"pass", "unknown"} and not block_reasons:
        block_reasons.append(f"quality status is {overall_status}")

    gate_results: list[dict[str, Any]] = []

    def add_gate_issue(
        *,
        rule_id: str,
        severity: str,
        reason: str,
        evidence_refs: list[str] | None = None,
        mode: str | None = None,
        decisions_by_target: dict[str, str] | None = None,
    ) -> None:
        gate_results.extend(
            _gate_results_for_issue(
                rule_id=rule_id,
                severity=severity,
                reason=reason,
                evidence_refs=evidence_refs,
                mode=mode,
                decisions_by_target=decisions_by_target,
            )
        )

    if base_status == "fail":
        add_gate_issue(
            rule_id="package.quality_status.fail",
            severity=GateSeverity.HARD.value,
            reason="quality status is fail",
            evidence_refs=["qa/quality_report.json:overall_status", "metrics/financial_checks.json:overall_status"],
        )
    if missing_required:
        marked_pass = base_status == "pass"
        add_gate_issue(
            rule_id="package.required_statements.missing_marked_pass" if marked_pass else "package.required_statements.missing",
            severity=GateSeverity.HARD.value if marked_pass else GateSeverity.SOFT.value,
            reason=(
                "required statements missing while package is marked pass"
                if marked_pass
                else "required statements missing"
            ),
            evidence_refs=[f"statement:{statement}" for statement in missing_required],
        )
    if artifact_hash["status"] == "missing":
        add_gate_issue(
            rule_id="package.artifact_hashes.missing",
            severity=GateSeverity.HARD.value,
            reason="artifact hashes missing",
            evidence_refs=["manifest.json:artifact_hashes"],
        )
    if artifact_hash["status"] == "mismatch":
        add_gate_issue(
            rule_id="package.artifact_hashes.mismatch",
            severity=GateSeverity.HARD.value,
            reason="artifact hash mismatch",
            evidence_refs=[f"artifact:{rel}" for rel in [*artifact_hash["mismatches"], *artifact_hash["missing"]]],
        )
    if critical_warnings:
        add_gate_issue(
            rule_id="package.critical_warnings.present",
            severity=GateSeverity.HARD.value,
            reason="critical warnings present",
            evidence_refs=[f"qa/quality_report.json:critical_warnings:{index}" for index, _ in enumerate(critical_warnings)],
        )
    if parser_warnings or rule_warnings:
        add_gate_issue(
            rule_id="package.parser_or_rule_warnings.present",
            severity=GateSeverity.SOFT.value,
            reason="parser or rule warnings present",
            evidence_refs=[
                *[f"qa/quality_report.json:parser_warnings:{index}" for index, _ in enumerate(parser_warnings)],
                *[f"qa/quality_report.json:rule_warnings:{index}" for index, _ in enumerate(rule_warnings)],
            ],
        )
    if missing_metric_source_count:
        add_gate_issue(
            rule_id="package.evidence.missing",
            severity=GateSeverity.HARD.value,
            reason="financial facts missing evidence",
            evidence_refs=[f"fact:{item}" for item in resolvability["missing_metric_sources"][:20]],
        )
    if unresolvable_evidence_count:
        add_gate_issue(
            rule_id="package.evidence.unresolvable",
            severity=GateSeverity.HARD.value,
            reason="unresolvable evidence present",
            evidence_refs=[
                f"evidence:{item}"
                for item in (
                    resolvability["unresolvable_source_map_entries"]
                    or resolvability["unresolvable_metric_sources"]
                )
            ],
        )
    if evidence_resolvability_ratio is not None and evidence_resolvability_ratio < 0.8:
        add_gate_issue(
            rule_id="package.evidence.resolvability_below_threshold",
            severity=GateSeverity.HARD.value,
            reason="evidence resolvability below 80%",
            evidence_refs=["qa/source_map.json", "metrics/financial_data.json"],
        )
    if value_verification_issue_count:
        add_gate_issue(
            rule_id="package.evidence.value_verification_failed",
            severity=GateSeverity.SOFT.value,
            reason="evidence value verification failed",
            evidence_refs=[
                ref
                for issue in value_verification["issues"][:20]
                for ref in issue.get("evidence_refs", [])
            ],
        )
    for issue in source_summary["issues"]:
        add_gate_issue(
            rule_id=str(issue["rule_id"]),
            severity=str(issue["severity"]),
            reason=str(issue["reason"]),
            evidence_refs=[str(ref) for ref in issue.get("evidence_refs", [])],
        )
    if base_status == "warning" and not (
        missing_required
        or critical_warnings
        or parser_warnings
        or rule_warnings
        or missing_metric_source_count
        or unresolvable_evidence_count
        or (evidence_resolvability_ratio is not None and evidence_resolvability_ratio < 0.8)
        or value_verification_issue_count
        or source_hard_issue_count
        or source_review_issue_count
    ):
        add_gate_issue(
            rule_id="package.quality_status.warning",
            severity=GateSeverity.SOFT.value,
            reason="quality status is warning",
            evidence_refs=["qa/quality_report.json:overall_status", "metrics/financial_checks.json:overall_status"],
        )
    if base_status == "unknown" and not gate_results:
        add_gate_issue(
            rule_id="package.quality_status.unknown",
            severity=GateSeverity.OBSERVE.value,
            reason="quality status is unknown",
            evidence_refs=["qa/quality_report.json:overall_status", "manifest.json:quality_status"],
        )

    decisions_by_target = _aggregate_gate_decisions(gate_results)
    canonical_decision = decisions_by_target[PromotionTarget.CANONICAL.value]["decision"]
    retrieval_decision = decisions_by_target[PromotionTarget.RETRIEVAL.value]["decision"]
    action_blocked = canonical_decision != GateDecision.ALLOW.value
    hard_gate_rule_ids = sorted(_gate_rule_ids(gate_results, GateSeverity.HARD.value))
    soft_gate_rule_ids = sorted(_gate_rule_ids(gate_results, GateSeverity.SOFT.value))
    return {
        "schema_version": "siq_quality_gates_v1",
        "gate_contract_version": GATE_CONTRACT_VERSION,
        "overall_status": overall_status,
        "decision": canonical_decision,
        "canonical_decision": canonical_decision,
        "retrieval_decision": retrieval_decision,
        "promotion_targets": list(PROMOTION_TARGETS),
        "decisions_by_target": decisions_by_target,
        "gate_results": gate_results,
        "hard_gate_rule_ids": hard_gate_rule_ids,
        "soft_gate_rule_ids": soft_gate_rule_ids,
        "action_blocked": action_blocked,
        "import_blocked": action_blocked,
        "vector_ingest_blocked": retrieval_decision != GateDecision.ALLOW.value,
        "force_allowed": bool(soft_gate_rule_ids) and not hard_gate_rule_ids,
        "block_reasons": block_reasons,
        "evidence_coverage_ratio": _evidence_coverage_ratio(
            quality,
            financial_data,
            manifest=manifest,
            source_map=source_map if isinstance(source_map, dict) else {},
            package_dir=package_dir,
        ),
        "missing_evidence_count": missing_metric_source_count,
        "missing_evidence": resolvability["missing_metric_sources"],
        "resolvable_evidence_count": resolvability["resolvable_evidence_count"],
        "unresolvable_evidence_count": unresolvable_evidence_count,
        "evidence_resolvability_ratio": evidence_resolvability_ratio,
        "unresolvable_evidence": resolvability["unresolvable_source_map_entries"] or resolvability["unresolvable_metric_sources"],
        "evidence_value_verification": value_verification,
        "evidence_value_verification_issue_count": value_verification_issue_count,
        "evidence_value_verification_ratio": value_verification["value_verification_ratio"],
        "source_summary": source_summary,
        "source_tier": source_summary["source_tier"],
        "source_verification_status": source_summary["source_verification_status"],
        "official_evidence_allowed": source_summary["official_evidence_allowed"],
        "required_statement_status": required_status,
        "missing_required_statements": missing_required,
        "artifact_hash_status": artifact_hash["status"],
        "artifact_hash_mismatches": artifact_hash["mismatches"],
        "artifact_hash_missing": artifact_hash["missing"],
        "parser_warnings": parser_warnings,
        "rule_warnings": rule_warnings,
        "rule_advisories": rule_advisories,
        "critical_warnings": critical_warnings,
    }


def _artifact_payloads(package_dir: Path, artifacts: dict[str, str]) -> dict[str, Any]:
    return {
        key: read_json(package_dir / rel, {})
        for key, rel in artifacts.items()
        if (package_dir / rel).exists()
    }


def read_market_package_summary(package_dir: Path, *, display_path: str | None = None) -> dict[str, Any]:
    package_dir = package_dir.resolve()
    manifest = read_json(package_dir / "manifest.json", {})
    quality = read_json(package_dir / "qa" / "quality_report.json", {})
    financial_data = read_json(package_dir / "metrics" / "financial_data.json", {})
    financial_checks = read_json(package_dir / "metrics" / "financial_checks.json", {})
    load_plan = read_json(package_dir / "metrics" / "load_plan.json", {})
    metrics = _normalized_metrics(read_json(package_dir / "metrics" / "normalized_metrics.json", {}))
    source_map_payload = read_json(package_dir / "qa" / "source_map.json", {})
    source_map = _source_map_entries(source_map_payload)
    source_summary = source_manifest_summary(manifest=manifest)
    resolvability = evidence_resolvability_summary(
        financial_data=financial_data,
        source_map=source_map_payload if isinstance(source_map_payload, dict) else {},
        manifest=manifest,
        package_dir=package_dir,
    )
    return {
        "package_path": display_path or str(package_dir),
        "paths": market_package_paths(package_dir),
        "market": manifest.get("market") if isinstance(manifest, dict) else None,
        "country": manifest.get("country") if isinstance(manifest, dict) else None,
        "document_format": manifest.get("document_format") if isinstance(manifest, dict) else None,
        "filing_id": manifest.get("filing_id") if isinstance(manifest, dict) else None,
        "parse_run_id": manifest.get("parse_run_id") if isinstance(manifest, dict) else None,
        "ticker": manifest.get("ticker") if isinstance(manifest, dict) else None,
        "company_name": manifest.get("company_name") if isinstance(manifest, dict) else None,
        "source_tier": source_summary["source_tier"],
        "source_verification_status": source_summary["source_verification_status"],
        "official_evidence_allowed": source_summary["official_evidence_allowed"],
        "source_summary": source_summary,
        "form": manifest.get("form") if isinstance(manifest, dict) else None,
        "report_type": manifest.get("report_type") if isinstance(manifest, dict) else None,
        "fiscal_year": manifest.get("fiscal_year") if isinstance(manifest, dict) else None,
        "fiscal_period": manifest.get("fiscal_period") if isinstance(manifest, dict) else None,
        "period_end": manifest.get("period_end") if isinstance(manifest, dict) else None,
        "published_at": (manifest.get("published_at") or manifest.get("filing_date")) if isinstance(manifest, dict) else None,
        "quality_status": (_quality_count(quality, "overall_status") or manifest.get("quality_status")) if isinstance(manifest, dict) else None,
        "counts": {
            "sections": _quality_count(quality, "section_count"),
            "tables": _quality_count(quality, "table_count"),
            "raw_facts": _quality_count(quality, "raw_fact_count", "xbrl_fact_count"),
            "metrics": _quality_count(quality, "normalized_metric_count") or len(metrics),
            "evidence": len(source_map),
            "resolvable_evidence": resolvability["resolvable_evidence_count"],
            "unresolvable_evidence": resolvability["unresolvable_evidence_count"],
        },
        "load_plan": _load_plan_summary(load_plan),
        "quality_gates": build_quality_gates(
            package_dir,
            manifest=manifest,
            quality=quality,
            financial_data=financial_data,
            financial_checks=financial_checks,
        ),
    }


def read_market_package_detail(package_dir: Path, *, display_path: str | None = None) -> dict[str, Any]:
    package_dir = package_dir.resolve()
    summary = read_market_package_summary(package_dir, display_path=display_path)
    source_map = read_json(package_dir / "qa" / "source_map.json", {})
    table_index = read_json(package_dir / "tables" / "table_index.json", {})
    normalized_metrics = read_json(package_dir / "metrics" / "normalized_metrics.json", {})
    parser_artifact_paths = {
        "document_full": PACKAGE_FILE_PATHS["document_full"],
        "content_list_enhanced": PACKAGE_FILE_PATHS["content_list_enhanced"],
        "table_relations": PACKAGE_FILE_PATHS["table_relations"],
    }
    qa_artifact_paths = {
        "footnotes": PACKAGE_FILE_PATHS["footnotes"],
        "toc": PACKAGE_FILE_PATHS["toc"],
        "financial_note_links": PACKAGE_FILE_PATHS["financial_note_links"],
        "table_quality_signals": PACKAGE_FILE_PATHS["table_quality_signals"],
    }
    return {
        **summary,
        "manifest": read_json(package_dir / "manifest.json", {}),
        "quality": read_json(package_dir / "qa" / "quality_report.json", {}),
        "financial_data": read_json(package_dir / "metrics" / "financial_data.json", {}),
        "financial_checks": read_json(package_dir / "metrics" / "financial_checks.json", {}),
        "load_plan": read_json(package_dir / "metrics" / "load_plan.json", {}),
        "metrics": _normalized_metrics(normalized_metrics),
        "source_map": _source_map_entries(source_map),
        "tables": _tables(table_index),
        "parser_artifacts": _artifact_payloads(package_dir, parser_artifact_paths),
        "qa_artifacts": _artifact_payloads(package_dir, qa_artifact_paths),
    }


def validate_evidence_package(package_dir: Path, *, strict_hashes: bool = True) -> EvidencePackageValidation:
    package_dir = package_dir.resolve()
    errors: list[str] = []
    warnings: list[str] = []
    manifest_path = package_dir / "manifest.json"
    manifest = read_json(manifest_path, {})

    if not package_dir.is_dir():
        errors.append(f"Package directory does not exist: {package_dir}")
        return EvidencePackageValidation(package_dir=package_dir, ok=False, errors=errors)
    if not manifest:
        errors.append("manifest.json is missing or empty")

    for rel in REQUIRED_FILES:
        if not (package_dir / rel).is_file():
            errors.append(f"Required file missing: {rel}")
    for rel in REQUIRED_DIRECTORIES:
        if not (package_dir / rel).is_dir():
            errors.append(f"Required directory missing: {rel}")

    missing = [field for field in REQUIRED_MANIFEST_FIELDS if field not in manifest]
    if missing:
        errors.append(f"Manifest required fields missing: {', '.join(missing)}")
    blank = [
        field
        for field in REQUIRED_MANIFEST_FIELDS
        if field in manifest and field != "artifact_hashes" and manifest.get(field) in (None, "")
    ]
    if blank:
        errors.append(f"Manifest required fields are blank: {', '.join(blank)}")

    if manifest.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"manifest.schema_version must be {SCHEMA_VERSION}")
    if manifest.get("market") not in {"CN", "US", "HK", "JP", "KR", "EU"}:
        errors.append("manifest.market must be one of CN/US/HK/JP/KR/EU")

    local_source_path = manifest.get("local_source_path")
    if local_source_path and not (package_dir / str(local_source_path)).is_file():
        errors.append(f"local_source_path does not exist: {local_source_path}")

    listed_hashes = manifest.get("artifact_hashes")
    if not isinstance(listed_hashes, dict) or not listed_hashes:
        errors.append("manifest.artifact_hashes must be a non-empty object")
        listed_hashes = {}
    computed_hashes = compute_artifact_hashes(package_dir)
    for rel, expected_hash in listed_hashes.items():
        if rel == "manifest.json":
            continue
        if rel in DERIVED_PLAN_FILES:
            continue
        actual_hash = computed_hashes.get(rel)
        if actual_hash is None:
            errors.append(f"artifact_hashes entry is missing on disk: {rel}")
        elif strict_hashes and actual_hash != expected_hash:
            errors.append(f"artifact hash mismatch: {rel}")

    source_summary = source_manifest_summary(manifest=manifest)
    for issue in source_summary["issues"]:
        if issue["severity"] == GateSeverity.HARD.value:
            errors.append(f"{issue['rule_id']}: {issue['reason']}")

    financial_data = read_json(package_dir / "metrics" / "financial_data.json", {})
    source_map = read_json(package_dir / "qa" / "source_map.json", {})
    source_entries = source_map.get("entries") if isinstance(source_map, dict) else []
    if not isinstance(source_entries, list):
        errors.append("qa/source_map.json entries must be a list")
        source_entries = []
    evidence_ids = {entry.get("evidence_id") for entry in source_entries if isinstance(entry, dict)}
    missing_evidence = missing_financial_data_evidence(financial_data)
    if missing_evidence:
        errors.append(f"financial_data metrics missing evidence: {', '.join(missing_evidence[:20])}")
    unresolvable_metric_evidence = unresolvable_financial_data_evidence(financial_data, manifest=manifest, package_dir=package_dir)
    if unresolvable_metric_evidence:
        errors.append(f"financial_data metrics have unresolvable evidence: {', '.join(unresolvable_metric_evidence[:20])}")
    for entry in source_entries:
        if not isinstance(entry, dict):
            errors.append("source_map entry must be an object")
            continue
        if not entry.get("evidence_id"):
            errors.append("source_map entry missing evidence_id")
        if not is_resolvable_evidence_source(entry, manifest=manifest, package_dir=package_dir):
            errors.append(f"source_map entry target is not resolvable: {entry.get('evidence_id')}")
    if source_entries and not evidence_ids:
        errors.append("source_map entries do not define evidence_id values")

    quality = read_json(package_dir / "qa" / "quality_report.json", {})
    for field_name in (
        "overall_status",
        "section_count",
        "table_count",
        "raw_fact_count",
        "normalized_metric_count",
        "evidence_coverage_ratio",
        "required_statement_status",
        "critical_warnings",
        "parser_warnings",
        "rule_warnings",
    ):
        if isinstance(quality, dict) and field_name not in quality:
            warnings.append(f"quality_report missing recommended field: {field_name}")

    return EvidencePackageValidation(
        package_dir=package_dir,
        ok=not errors,
        errors=errors,
        warnings=warnings,
        manifest=manifest,
        artifact_hashes=computed_hashes,
    )


def source_map_from_financial_data(
    *,
    manifest: dict[str, Any],
    financial_data: dict[str, Any],
    package_dir: Path | None = None,
) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in iter_financial_data_items(financial_data):
        canonical_name = row.get("canonical_name") or row.get("name")
        statement_type = row.get("statement_type")
        values = row.get("values") if isinstance(row.get("values"), dict) else {}
        sources = row.get("sources") if isinstance(row.get("sources"), dict) else {}
        for period_key, evidence in sources.items():
            if not isinstance(evidence, dict):
                continue
            evidence_id = evidence_id_for_fact(manifest, canonical_name, period_key, evidence)
            if evidence_id in seen:
                continue
            seen.add(evidence_id)
            target = evidence_target(evidence)
            local_path = evidence.get("path") or local_path_for_evidence(evidence)
            quote_text = evidence.get("quote_text") or evidence.get("html_snippet")
            entry = {
                "evidence_id": evidence_id,
                "market": manifest.get("market"),
                "country": manifest.get("country"),
                "filing_id": manifest.get("filing_id"),
                "parse_run_id": manifest.get("parse_run_id"),
                "ticker": manifest.get("ticker"),
                "company_name": manifest.get("company_name"),
                "canonical_name": canonical_name,
                "statement_type": statement_type,
                "period_key": period_key,
                "value": values.get(period_key),
                "source_type": evidence.get("source_type"),
                "source_id": evidence.get("source_id"),
                "page_number": evidence.get("page_number"),
                "table_index": evidence.get("table_index"),
                "row_index": evidence.get("row_index"),
                "column_index": evidence.get("column_index"),
                "xbrl_tag": evidence.get("xbrl_tag"),
                "context_ref": evidence.get("raw", {}).get("context_ref") if isinstance(evidence.get("raw"), dict) else None,
                "unit_ref": evidence.get("raw", {}).get("unit_ref") if isinstance(evidence.get("raw"), dict) else None,
                "fact_id": evidence.get("raw", {}).get("fact_id") if isinstance(evidence.get("raw"), dict) else None,
                "accession_number": evidence.get("accession_number"),
                "html_anchor": evidence.get("anchor"),
                "xpath": evidence.get("xpath"),
                "source_url": evidence.get("url") or manifest.get("source_url"),
                "local_path": local_path,
                "table_json_path": local_path if local_path and str(local_path).startswith("tables/") else None,
                "pdf_local_path": manifest.get("local_source_path"),
                "quote_text": quote_text,
                "text_hash": stable_id(quote_text) if quote_text else None,
                "target": target,
                "raw": evidence,
            }
            resolvability = evidence_source_resolvability(entry, manifest=manifest, package_dir=package_dir)
            entry["resolvable"] = resolvability["resolvable"]
            entry["resolvability_kind"] = resolvability["kind"]
            entry["resolvability_reason"] = resolvability["reason"]
            if package_dir is not None and entry["local_path"]:
                local = package_dir / str(entry["local_path"])
                if not local.exists():
                    entry["raw"] = {**entry["raw"], "local_path_missing": True}
            entries.append(entry)
    return {
        "schema_version": "market_source_map_v1",
        "market": manifest.get("market"),
        "filing_id": manifest.get("filing_id"),
        "entries": entries,
    }


def evidence_id_for_fact(
    manifest: dict[str, Any],
    canonical_name: Any,
    period_key: Any,
    evidence: dict[str, Any],
) -> str:
    if manifest.get("market") == "EU":
        country = str(manifest.get("country") or "unknown").lower()
        filing_id = str(manifest.get("filing_id") or "unknown").replace(":", "-")
        source_type = str(evidence.get("source_type") or "")
        page_number = evidence.get("page_number")
        table_index = evidence.get("table_index")
        row_index = evidence.get("row_index")
        column_index = evidence.get("column_index")
        if page_number is not None and table_index is not None and row_index is not None:
            parts = ["eu", country, filing_id, f"p{page_number}", f"t{table_index}", f"r{row_index}"]
            if column_index is not None:
                parts.append(f"c{column_index}")
            return ":".join(parts)
        if evidence.get("xbrl_tag"):
            fact_key = (
                evidence.get("raw", {}).get("fact_id")
                if isinstance(evidence.get("raw"), dict)
                else None
            ) or stable_id(
                evidence.get("xbrl_tag"),
                evidence.get("raw", {}).get("context_ref") if isinstance(evidence.get("raw"), dict) else None,
                evidence.get("raw", {}).get("unit_ref") if isinstance(evidence.get("raw"), dict) else None,
                canonical_name,
                period_key,
            )[:24]
            return f"eu:{country}:{filing_id}:xbrl:{fact_key}"
        if "html" in source_type and table_index is not None and row_index is not None:
            parts = ["eu", country, filing_id, "html", f"t{table_index}", f"r{row_index}"]
            if column_index is not None:
                parts.append(f"c{column_index}")
            return ":".join(parts)
    if manifest.get("market") == "HK":
        page_number = evidence.get("page_number")
        table_index = evidence.get("table_index")
        row_index = evidence.get("row_index")
        column_index = evidence.get("column_index")
        if page_number is not None and table_index is not None and row_index is not None:
            parts = [
                "hk",
                str(manifest.get("filing_id") or "unknown"),
                f"p{page_number}",
                f"t{table_index}",
                f"r{row_index}",
            ]
            if column_index is not None:
                parts.append(f"c{column_index}")
            return ":".join(parts)
    return stable_id(
        manifest.get("filing_id"),
        canonical_name,
        period_key,
        evidence.get("source_type"),
        evidence.get("source_id"),
        evidence.get("page_number"),
        evidence.get("table_index"),
        evidence.get("row_index"),
        evidence.get("column_index"),
        evidence.get("xbrl_tag"),
        evidence.get("raw", {}).get("context_ref") if isinstance(evidence.get("raw"), dict) else None,
    )


def local_path_for_evidence(evidence: dict[str, Any]) -> str | None:
    source_type = str(evidence.get("source_type") or "")
    table_index = evidence.get("table_index")
    if table_index is not None and ("table" in source_type or source_type.startswith("pdf_")):
        try:
            return f"tables/table_{int(table_index):04d}.json"
        except (TypeError, ValueError):
            return "tables/table_index.json"
    if evidence.get("xbrl_tag"):
        return "xbrl/facts_raw.json"
    return evidence.get("path")


def evidence_target(evidence: dict[str, Any]) -> str:
    url = evidence.get("url") or ""
    anchor = evidence.get("anchor") or evidence.get("html_anchor")
    page_number = evidence.get("page_number")
    table_index = evidence.get("table_index")
    row_index = evidence.get("row_index")
    column_index = evidence.get("column_index")
    if url and anchor:
        return f"{url}#{anchor}"
    if page_number is not None:
        return f"page={page_number};table={table_index};row={row_index};column={column_index}"
    if url:
        return str(url)
    if evidence.get("xbrl_tag"):
        return f"xbrl:{evidence.get('xbrl_tag')}:{evidence.get('source_id') or ''}"
    return ""


def normalized_metrics_from_financial_data(
    *,
    manifest: dict[str, Any],
    financial_data: dict[str, Any],
    source_map: dict[str, Any],
) -> list[dict[str, Any]]:
    entries_by_key: dict[tuple[Any, Any, Any, Any, Any, Any, Any, Any], dict[str, Any]] = {}
    for entry in source_map.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        key = (
            entry.get("canonical_name"),
            entry.get("period_key"),
            entry.get("source_type"),
            entry.get("source_id"),
            entry.get("page_number"),
            entry.get("table_index"),
            entry.get("row_index"),
            entry.get("column_index"),
        )
        entries_by_key[key] = entry

    rows: list[dict[str, Any]] = []
    parse_run_id = manifest.get("parse_run_id") or stable_parse_run_id(manifest)
    for item in iter_financial_data_items(financial_data):
        sources = item.get("sources") if isinstance(item.get("sources"), dict) else {}
        values = item.get("values") if isinstance(item.get("values"), dict) else {}
        raw_values = item.get("raw_values") if isinstance(item.get("raw_values"), dict) else {}
        periods = item.get("periods") if isinstance(item.get("periods"), dict) else {}
        for period_key, value in values.items():
            evidence = sources.get(period_key) if isinstance(sources, dict) else None
            if not isinstance(evidence, dict):
                evidence = {}
            lookup_key = (
                item.get("canonical_name"),
                period_key,
                evidence.get("source_type"),
                evidence.get("source_id"),
                evidence.get("page_number"),
                evidence.get("table_index"),
                evidence.get("row_index"),
                evidence.get("column_index"),
            )
            entry = entries_by_key.get(lookup_key, {})
            period = periods.get(period_key) if isinstance(periods.get(period_key), dict) else {}
            metric_id = stable_id(
                parse_run_id,
                item.get("canonical_name"),
                period_key,
                evidence.get("source_type"),
                evidence.get("source_id"),
                evidence.get("row_index"),
                evidence.get("column_index"),
            )
            rows.append(
                {
                    "metric_id": metric_id,
                    "filing_id": manifest.get("filing_id"),
                    "parse_run_id": parse_run_id,
                    "market": manifest.get("market"),
                    "ticker": manifest.get("ticker"),
                    "statement_type": item.get("statement_type"),
                    "canonical_name": item.get("canonical_name"),
                    "local_name": item.get("name"),
                    "label": item.get("name"),
                    "value": value,
                    "raw_value": raw_values.get(period_key),
                    "unit": item.get("unit"),
                    "currency": item.get("currency"),
                    "scale": item.get("scale"),
                    "period_key": period_key,
                    "period_start": period.get("period_start"),
                    "period_end": period.get("period_end") or period_key,
                    "duration_days": period.get("duration_days"),
                    "frame": period.get("frame"),
                    "qtd_ytd_type": period.get("qtd_ytd_type"),
                    "fiscal_year": period.get("fiscal_year") or manifest.get("fiscal_year"),
                    "fiscal_period": period.get("fiscal_period") or manifest.get("fiscal_period"),
                    "accounting_standard": manifest.get("accounting_standard"),
                    "taxonomy": item.get("taxonomy"),
                    "gaap_status": item.get("gaap_status"),
                    "confidence": item.get("confidence"),
                    "source_type": evidence.get("source_type"),
                    "evidence_id": entry.get("evidence_id"),
                    "raw_fact_id": evidence.get("raw", {}).get("fact_id") if isinstance(evidence.get("raw"), dict) else None,
                    "xbrl_tag": evidence.get("xbrl_tag"),
                    "context_ref": evidence.get("raw", {}).get("context_ref") if isinstance(evidence.get("raw"), dict) else None,
                    "page_number": evidence.get("page_number"),
                    "table_index": evidence.get("table_index"),
                    "row_index": evidence.get("row_index"),
                    "column_index": evidence.get("column_index"),
                    "raw": item.get("raw"),
                }
            )
    return rows


def build_quality_report(
    *,
    manifest: dict[str, Any],
    financial_data: dict[str, Any],
    financial_checks: dict[str, Any],
    section_count: int,
    table_count: int,
    raw_fact_count: int,
    source_map: dict[str, Any],
    parser_warnings: list[str] | None = None,
    rule_warnings: list[str] | None = None,
) -> dict[str, Any]:
    metric_count = 0
    evidence_count = 0
    unresolvable_evidence_count = 0
    statement_status: dict[str, str] = {
        "balance_sheet": "missing",
        "income_statement": "missing",
        "cash_flow_statement": "missing",
    }
    for statement in financial_data.get("statements") or []:
        statement_type = statement.get("statement_type")
        item_count = len(statement.get("items") or [])
        if statement_type in statement_status and item_count > 0:
            statement_status[statement_type] = "present"
        for item in statement.get("items") or []:
            values = item.get("values") if isinstance(item.get("values"), dict) else {}
            sources = item.get("sources") if isinstance(item.get("sources"), dict) else {}
            metric_count += len(values)
            for key in values:
                evidence = sources.get(key)
                if evidence and is_resolvable_evidence_source(evidence, manifest=manifest):
                    evidence_count += 1
                elif evidence:
                    unresolvable_evidence_count += 1
    for bucket in ("key_metrics", "operating_metrics"):
        for item in financial_data.get(bucket) or []:
            values = item.get("values") if isinstance(item.get("values"), dict) else {}
            sources = item.get("sources") if isinstance(item.get("sources"), dict) else {}
            metric_count += len(values)
            for key in values:
                evidence = sources.get(key)
                if evidence and is_resolvable_evidence_source(evidence, manifest=manifest):
                    evidence_count += 1
                elif evidence:
                    unresolvable_evidence_count += 1

    missing = missing_financial_data_evidence(financial_data)
    critical_warnings = []
    if missing:
        critical_warnings.append({"type": "missing_evidence", "metrics": missing})
    if unresolvable_evidence_count:
        critical_warnings.append(
            {
                "type": "unresolvable_evidence",
                "count": unresolvable_evidence_count,
                "metrics": unresolvable_financial_data_evidence(financial_data, manifest=manifest),
            }
        )
    if any(status == "missing" for status in statement_status.values()):
        critical_warnings.append({"type": "missing_statement", "required_statement_status": statement_status})

    extraction_status = "ok"
    extraction_blockers: list[dict[str, Any]] = []
    if metric_count == 0 and table_count == 0:
        extraction_status = "parser_table_not_detected"
        extraction_blockers.append(
            {
                "type": "parser_table_not_detected",
                "message": "Parser output did not expose structured tables for rule extraction.",
            }
        )
    elif metric_count == 0 and table_count > 0 and all(status == "missing" for status in statement_status.values()):
        extraction_status = "financial_statement_table_not_recognized"
        extraction_blockers.append(
            {
                "type": "financial_statement_table_not_recognized",
                "message": "Parsed tables exist, but none were recognized as financial statement tables.",
            }
        )
    elif any(status == "missing" for status in statement_status.values()):
        extraction_status = "partial_statement_coverage"
        extraction_blockers.append(
            {
                "type": "partial_statement_coverage",
                "required_statement_status": statement_status,
            }
        )

    ratio = 1.0 if metric_count == 0 else round(evidence_count / metric_count, 6)
    resolvability = evidence_resolvability_summary(
        financial_data=financial_data,
        source_map=source_map,
        manifest=manifest,
    )
    return {
        "schema_version": "market_quality_report_v1",
        "market": manifest.get("market"),
        "filing_id": manifest.get("filing_id"),
        "parse_run_id": manifest.get("parse_run_id"),
        "overall_status": financial_checks.get("overall_status") or manifest.get("quality_status") or "warning",
        "section_count": section_count,
        "table_count": table_count,
        "raw_fact_count": raw_fact_count,
        "normalized_metric_count": metric_count,
        "evidence_coverage_ratio": ratio,
        "resolvable_evidence_count": evidence_count,
        "unresolvable_evidence_count": resolvability["unresolvable_evidence_count"],
        "evidence_resolvability_ratio": resolvability["evidence_resolvability_ratio"],
        "extraction_status": extraction_status,
        "extraction_blockers": extraction_blockers,
        "required_statement_status": statement_status,
        "critical_warnings": critical_warnings,
        "parser_warnings": parser_warnings or [],
        "rule_warnings": rule_warnings or financial_data.get("warnings") or financial_checks.get("warnings") or [],
        "rule_advisories": _list_field(financial_checks, "advisories"),
        "source_map_entry_count": len(source_map.get("entries") or []),
        "resolvable_source_map_entry_count": resolvability["resolvable_source_map_entry_count"],
        "unresolvable_source_map_entry_count": resolvability["unresolvable_source_map_entry_count"],
    }
