import hashlib
import json
from pathlib import Path

from siq_market_contracts import (
    SCHEMA_VERSION,
    build_quality_gates,
    canonical_value_polarity,
    compute_artifact_hashes,
    is_resolvable_evidence_source,
    read_market_package_detail,
    read_market_package_summary,
    source_map_from_financial_data,
    stable_parse_run_id,
    validate_evidence_package,
    write_json,
)
from siq_market_contracts.evidence_gates import GateSeverity as ExtractedGateSeverity
from siq_market_contracts.evidence_package import (
    GateSeverity as FacadeGateSeverity,
    evidence_value_verification_summary,
)


def _financial_data() -> dict:
    return {
        "schema_version": 1,
        "market": "HK",
        "statements": [
            {
                "statement_type": "balance_sheet",
                "items": [
                    {
                        "name": "Total assets",
                        "canonical_name": "total_assets",
                        "statement_type": "balance_sheet",
                        "values": {"2025-12-31": "1000"},
                        "raw_values": {"2025-12-31": "1,000"},
                        "sources": {
                            "2025-12-31": {
                                "source_type": "pdf_statement_table",
                                "source_id": "table_1",
                                "page_number": 88,
                                "table_index": 1,
                                "row_index": 1,
                                "column_index": 1,
                                "quote_text": "Total assets | 1,000",
                            }
                        },
                        "periods": {"2025-12-31": {"period_end": "2025-12-31", "fiscal_year": 2025}},
                    }
                ],
            }
        ],
        "key_metrics": [],
        "operating_metrics": [],
        "warnings": [],
    }


def _financial_data_with_value(
    *,
    market: str,
    canonical_name: str,
    normalized_value: str,
    raw_value: str,
) -> dict:
    payload = _financial_data()
    payload["market"] = market
    item = payload["statements"][0]["items"][0]
    item["name"] = canonical_name.replace("_", " ").title()
    item["canonical_name"] = canonical_name
    item["statement_type"] = "income_statement"
    item["values"]["2025-12-31"] = normalized_value
    item["raw_values"]["2025-12-31"] = raw_value
    item["sources"]["2025-12-31"]["quote_text"] = f"{item['name']} | {raw_value}"
    return payload


def test_evidence_package_preserves_gate_imports():
    assert FacadeGateSeverity is ExtractedGateSeverity
    assert FacadeGateSeverity.HARD.value == "hard"


def _write_package(root: Path) -> Path:
    package_dir = root / "hk" / "companies" / "00700-TENCENT" / "reports" / "2025-annual-12100024"
    for name in ("raw", "sections", "tables", "xbrl", "metrics", "qa"):
        (package_dir / name).mkdir(parents=True, exist_ok=True)
    (package_dir / "README.md").write_text("# Test\n", encoding="utf-8")
    (package_dir / "raw" / "report.pdf").write_bytes(b"%PDF-1.4 test")
    source_digest = hashlib.sha256((package_dir / "raw" / "report.pdf").read_bytes()).hexdigest()
    (package_dir / "sections" / "report.md").write_text("# Report\n", encoding="utf-8")
    write_json(package_dir / "tables" / "table_index.json", {"tables": [{"table_index": 1}]})
    write_json(package_dir / "xbrl" / "facts_raw.json", {"facts": []})
    financial_data = _financial_data()
    financial_checks = {"overall_status": "warning", "warnings": [], "checks": []}
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "market": "HK",
        "filing_id": "HK:00700:12100024",
        "company_id": "HK:00700",
        "ticker": "00700",
        "company_name": "TENCENT",
        "source_id": "hkex",
        "form": "annual",
        "report_type": "annual",
        "fiscal_year": 2025,
        "fiscal_period": "FY",
        "period_end": "2025-12-31",
        "published_at": "2026-04-09",
        "source_url": "https://www1.hkexnews.hk/example.pdf",
        "source_tier": "official_regulator",
        "source_verification_status": "official_verified",
        "source_manifest": {
            "schema_version": "siq_source_manifest_v1",
            "source_tier": "official_regulator",
            "source_verification_status": "official_verified",
            "initial_url": "https://www1.hkexnews.hk/example.pdf",
            "final_url": "https://www1.hkexnews.hk/example.pdf",
            "redirect_chain": [],
            "content_sha256": source_digest,
            "content_hash": f"sha256:{source_digest}",
            "retrieved_at": "2026-07-06T00:00:00Z",
            "regulator_host_verified": True,
        },
        "local_source_path": "raw/report.pdf",
        "accounting_standard": "HKFRS",
        "parser_version": "test_parser_v1",
        "rules_version": "test_rules_v1",
        "quality_status": "warning",
        "artifact_hashes": {},
    }
    manifest["parse_run_id"] = stable_parse_run_id(manifest, {})
    source_map = source_map_from_financial_data(manifest=manifest, financial_data=financial_data, package_dir=package_dir)
    write_json(package_dir / "metrics" / "financial_data.json", financial_data)
    write_json(package_dir / "metrics" / "financial_checks.json", financial_checks)
    write_json(package_dir / "metrics" / "normalized_metrics.json", {"metrics": [{"metric_id": "m1"}]})
    write_json(
        package_dir / "qa" / "quality_report.json",
        {
            "overall_status": "warning",
            "section_count": 1,
            "table_count": 1,
            "raw_fact_count": 0,
            "normalized_metric_count": 1,
            "evidence_coverage_ratio": 1,
            "required_statement_status": {"balance_sheet": "present"},
            "critical_warnings": [],
            "parser_warnings": [],
            "rule_warnings": [],
        },
    )
    write_json(package_dir / "qa" / "source_map.json", source_map)
    manifest["artifact_hashes"] = compute_artifact_hashes(package_dir)
    write_json(package_dir / "manifest.json", manifest)
    return package_dir


def _mark_quality_pass(package_dir: Path) -> None:
    quality_path = package_dir / "qa" / "quality_report.json"
    quality = json.loads(quality_path.read_text(encoding="utf-8"))
    quality["overall_status"] = "pass"
    quality["required_statement_status"] = {
        "income_statement": "present",
        "balance_sheet": "present",
        "cash_flow_statement": "present",
    }
    quality["parser_warnings"] = []
    quality["rule_warnings"] = []
    quality["critical_warnings"] = []
    write_json(quality_path, quality)

    checks_path = package_dir / "metrics" / "financial_checks.json"
    checks = json.loads(checks_path.read_text(encoding="utf-8"))
    checks["overall_status"] = "pass"
    checks["warnings"] = []
    write_json(checks_path, checks)

    manifest = json.loads((package_dir / "manifest.json").read_text(encoding="utf-8"))
    manifest["quality_status"] = "pass"
    manifest["artifact_hashes"] = compute_artifact_hashes(package_dir)
    write_json(package_dir / "manifest.json", manifest)


def _update_manifest(package_dir: Path, **updates) -> dict:
    manifest = json.loads((package_dir / "manifest.json").read_text(encoding="utf-8"))
    manifest.update(updates)
    write_json(package_dir / "manifest.json", manifest)
    return manifest


def test_market_package_summary_exposes_load_plan_decisions(tmp_path):
    package_dir = _write_package(tmp_path)
    write_json(
        package_dir / "metrics" / "load_plan.json",
        {
            "can_import": False,
            "can_vector_ingest": False,
            "blocked_reasons": ["canonical:review:accounting.standard.known"],
            "promotion_decisions": {
                "canonical": {"decision": "review"},
                "retrieval": {"decision": "review"},
            },
            "rows": [{"table": "financial_data_artifacts"}],
            "quarantine_rows": [{"table": "financial_facts"}],
        },
    )
    _update_manifest(package_dir, artifact_hashes=compute_artifact_hashes(package_dir))

    summary = read_market_package_summary(package_dir)
    detail = read_market_package_detail(package_dir)

    assert summary["paths"]["load_plan"] == "metrics/load_plan.json"
    assert summary["load_plan"]["can_import"] is False
    assert summary["load_plan"]["can_vector_ingest"] is False
    assert summary["load_plan"]["quarantine_row_count"] == 1
    assert detail["load_plan"]["can_import"] is False


def test_load_plan_is_derived_and_excluded_from_artifact_hashes(tmp_path):
    package_dir = _write_package(tmp_path)
    write_json(package_dir / "metrics" / "load_plan.json", {"can_import": True})

    hashes = compute_artifact_hashes(package_dir)

    assert "metrics/financial_data.json" in hashes
    assert "metrics/load_plan.json" not in hashes


def test_validate_and_read_market_package(tmp_path):
    package_dir = _write_package(tmp_path)

    validation = validate_evidence_package(package_dir)
    summary = read_market_package_summary(package_dir, display_path="hk/companies/00700-TENCENT/reports/2025-annual-12100024")
    detail = read_market_package_detail(package_dir, display_path="hk/companies/00700-TENCENT/reports/2025-annual-12100024")

    assert validation.ok, validation.errors
    assert summary["package_path"] == "hk/companies/00700-TENCENT/reports/2025-annual-12100024"
    assert summary["paths"]["manifest"] == "manifest.json"
    assert summary["counts"] == {
        "sections": 1,
        "tables": 1,
        "raw_facts": 0,
        "metrics": 1,
        "evidence": 1,
        "resolvable_evidence": 1,
        "unresolvable_evidence": 0,
    }
    assert summary["quality_gates"]["overall_status"] == "warning"
    assert summary["quality_gates"]["import_blocked"] is True
    assert summary["quality_gates"]["gate_contract_version"] == "risk_calibrated_gate_v1"
    assert summary["quality_gates"]["decisions_by_target"]["draft"]["decision"] == "allow"
    assert summary["quality_gates"]["decisions_by_target"]["canonical"]["decision"] == "review"
    assert summary["quality_gates"]["decisions_by_target"]["retrieval"]["decision"] == "review"
    assert summary["quality_gates"]["force_allowed"] is True
    canonical_required_gate = next(
        gate
        for gate in summary["quality_gates"]["gate_results"]
        if gate["rule_id"] == "package.required_statements.missing" and gate["target"] == "canonical"
    )
    assert canonical_required_gate["severity"] == "soft"
    assert canonical_required_gate["decision"] == "review"
    assert {"rule_id", "severity", "reason", "target", "evidence_refs"} <= set(canonical_required_gate)
    assert summary["quality_gates"]["evidence_coverage_ratio"] == 1
    assert summary["quality_gates"]["unresolvable_evidence_count"] == 0
    assert summary["quality_gates"]["evidence_value_verification_issue_count"] == 0
    assert summary["quality_gates"]["evidence_value_verification"]["pdf_checked_count"] == 1
    assert summary["quality_gates"]["evidence_value_verification"]["quote_checked_count"] == 1
    assert summary["source_tier"] == "official_regulator"
    assert summary["quality_gates"]["official_evidence_allowed"] is True
    assert "income_statement" in summary["quality_gates"]["missing_required_statements"]
    assert detail["manifest"]["schema_version"] == SCHEMA_VERSION
    assert detail["quality_gates"]["artifact_hash_status"] == "ok"
    assert detail["metrics"] == [{"metric_id": "m1"}]
    assert detail["tables"] == [{"table_index": 1}]

    value_summary = evidence_value_verification_summary(financial_data=detail["financial_data"])
    assert value_summary["issue_count"] == 0
    assert value_summary["value_verification_ratio"] == 1


def test_evidence_value_verification_accepts_declared_deduction_presentation_sign():
    assert canonical_value_polarity("HK", "finance_costs") == "deduction_magnitude"
    assert canonical_value_polarity("EU", "income_tax_expense") == "deduction_magnitude"

    for market in ("HK", "EU"):
        summary = evidence_value_verification_summary(
            financial_data=_financial_data_with_value(
                market=market,
                canonical_name="income_tax_expense",
                normalized_value="30",
                raw_value="(30)",
            )
        )

        assert summary["polarity_contract_version"] == "siq_financial_value_polarity_v1"
        assert summary["issue_count"] == 0, (market, summary["issues"])
        assert summary["value_verification_ratio"] == 1


def test_evidence_value_verification_keeps_revenue_and_profit_sign_strict():
    for canonical_name in ("operating_revenue", "net_profit"):
        summary = evidence_value_verification_summary(
            financial_data=_financial_data_with_value(
                market="HK",
                canonical_name=canonical_name,
                normalized_value="30",
                raw_value="(30)",
            )
        )

        assert canonical_value_polarity("HK", canonical_name) == "signed"
        assert summary["failed_fact_count"] == 1
        assert summary["issue_count"] == 3


def test_evidence_value_verification_does_not_apply_hk_eu_polarity_to_us_expenses():
    summary = evidence_value_verification_summary(
        financial_data=_financial_data_with_value(
            market="US",
            canonical_name="income_tax_expense",
            normalized_value="30",
            raw_value="(30)",
        )
    )

    assert canonical_value_polarity("US", "income_tax_expense") == "signed"
    assert summary["failed_fact_count"] == 1
    assert summary["issue_count"] == 3


def test_evidence_value_verification_polarity_normalization_is_one_way():
    summary = evidence_value_verification_summary(
        financial_data=_financial_data_with_value(
            market="HK",
            canonical_name="income_tax_expense",
            normalized_value="-30",
            raw_value="30",
        )
    )

    assert summary["failed_fact_count"] == 1
    assert summary["issue_count"] == 3


def _us_ixbrl_financial_data(*, quote_text: str = "2,999", period_end: str | None = "2025-06-30") -> dict:
    source_raw = {
        "context_ref": "c-2025",
        "unit_ref": "usd",
        "decimals": "-6",
        "xbrl_scale_exponent": "6",
        "scale_multiplier": "1000000",
        "sign": None,
    }
    if period_end is not None:
        source_raw["period_end"] = period_end
    return {
        "market": "US",
        "statements": [
            {
                "statement_type": "balance_sheet",
                "items": [
                    {
                        "canonical_name": "borrowings",
                        "values": {"2025-06-30": "2999000000"},
                        "raw_values": {"2025-06-30": "2,999"},
                        "scale": "1",
                        "periods": {"2025-06-30": {"period_end": "2025-06-30"}},
                        "sources": {
                            "2025-06-30": {
                                "source_type": "sec_xbrl_fact",
                                "source_id": "us-gaap:LongTermDebtCurrent",
                                "xbrl_tag": "us-gaap:LongTermDebtCurrent",
                                "quote_text": quote_text,
                                "raw": source_raw,
                            }
                        },
                    }
                ],
            }
        ],
        "key_metrics": [],
        "operating_metrics": [],
    }


def test_evidence_value_verification_applies_explicit_ixbrl_scale_multiplier():
    summary = evidence_value_verification_summary(financial_data=_us_ixbrl_financial_data())

    assert summary["checked_fact_count"] == 1
    assert summary["verified_fact_count"] == 1
    assert summary["issue_count"] == 0


def test_evidence_value_verification_keeps_ixbrl_quote_and_period_strict():
    wrong_quote = evidence_value_verification_summary(
        financial_data=_us_ixbrl_financial_data(quote_text="2,998")
    )
    missing_period = evidence_value_verification_summary(
        financial_data=_us_ixbrl_financial_data(period_end=None)
    )

    assert wrong_quote["failed_fact_count"] == 1
    assert [issue["rule"] for issue in wrong_quote["issues"]] == ["quote.value.explainable"]
    assert missing_period["failed_fact_count"] == 1
    assert [issue["rule"] for issue in missing_period["issues"]] == ["xbrl.fields.present"]


def test_evidence_value_verification_applies_ixbrl_sign_without_relaxing_us_polarity():
    payload = _us_ixbrl_financial_data(quote_text="51,699")
    item = payload["statements"][0]["items"][0]
    item["canonical_name"] = "financing_cash_flow_net"
    item["values"]["2025-06-30"] = "-51699000000"
    item["raw_values"]["2025-06-30"] = "51,699"
    item["sources"]["2025-06-30"]["raw"]["sign"] = "-"

    summary = evidence_value_verification_summary(financial_data=payload)

    assert summary["verified_fact_count"] == 1
    assert summary["issue_count"] == 0


def test_quality_gates_allow_official_regulator_source_manifest_for_canonical(tmp_path):
    package_dir = _write_package(tmp_path)
    _mark_quality_pass(package_dir)

    gates = build_quality_gates(package_dir)

    assert gates["overall_status"] == "pass"
    assert gates["source_tier"] == "official_regulator"
    assert gates["official_evidence_allowed"] is True
    assert gates["decisions_by_target"]["canonical"]["decision"] == "allow"
    assert gates["decisions_by_target"]["retrieval"]["decision"] == "allow"


def test_quality_gates_review_unverified_web_source_for_canonical(tmp_path):
    package_dir = _write_package(tmp_path)
    _mark_quality_pass(package_dir)
    source_digest = "b" * 64
    _update_manifest(
        package_dir,
        source_url="https://search.example/result?q=tencent",
        source_tier="unverified_web",
        source_verification_status="manual_unverified",
        source_manifest={
            "schema_version": "siq_source_manifest_v1",
            "source_tier": "unverified_web",
            "source_verification_status": "manual_unverified",
            "initial_url": "https://search.example/result?q=tencent",
            "final_url": "https://cdn.example/report.pdf",
            "redirect_chain": [
                {
                    "status_code": 302,
                    "from_url": "https://search.example/result?q=tencent",
                    "to_url": "https://cdn.example/report.pdf",
                }
            ],
            "content_sha256": source_digest,
            "content_hash": f"sha256:{source_digest}",
            "retrieved_at": "2026-07-06T00:00:00Z",
        },
    )

    result = validate_evidence_package(package_dir)
    gates = build_quality_gates(package_dir)

    assert result.ok, result.errors
    assert gates["overall_status"] == "warning"
    assert gates["source_tier"] == "unverified_web"
    assert gates["official_evidence_allowed"] is False
    assert gates["decisions_by_target"]["canonical"]["decision"] == "review"
    assert gates["decisions_by_target"]["retrieval"]["decision"] == "review"
    assert gates["import_blocked"] is True
    assert gates["vector_ingest_blocked"] is True
    assert gates["force_allowed"] is True
    assert "package.source.unverified_for_official_evidence" in gates["soft_gate_rule_ids"]


def test_quality_gates_block_official_regulator_claim_outside_allowlist(tmp_path):
    package_dir = _write_package(tmp_path)
    _mark_quality_pass(package_dir)
    source_digest = "c" * 64
    _update_manifest(
        package_dir,
        source_url="https://www1.hkexnews.hk/example.pdf",
        source_tier="official_regulator",
        source_verification_status="official_verified",
        source_manifest={
            "schema_version": "siq_source_manifest_v1",
            "source_tier": "official_regulator",
            "source_verification_status": "official_verified",
            "initial_url": "https://www1.hkexnews.hk/example.pdf",
            "final_url": "https://unknown-cdn.example/example.pdf",
            "redirect_chain": [
                {
                    "status_code": 302,
                    "from_url": "https://www1.hkexnews.hk/example.pdf",
                    "to_url": "https://unknown-cdn.example/example.pdf",
                }
            ],
            "content_sha256": source_digest,
            "content_hash": f"sha256:{source_digest}",
            "retrieved_at": "2026-07-06T00:00:00Z",
        },
    )

    result = validate_evidence_package(package_dir)
    gates = build_quality_gates(package_dir)

    assert not result.ok
    assert any("package.source.official_regulator_unverified" in error for error in result.errors)
    assert gates["overall_status"] == "fail"
    assert gates["decisions_by_target"]["canonical"]["decision"] == "block"
    assert gates["decisions_by_target"]["retrieval"]["decision"] == "block"
    assert gates["import_blocked"] is True
    assert gates["vector_ingest_blocked"] is True
    assert gates["force_allowed"] is False
    assert "package.source.official_regulator_unverified" in gates["hard_gate_rule_ids"]


def test_validate_rejects_missing_evidence(tmp_path):
    package_dir = _write_package(tmp_path)
    data_path = package_dir / "metrics" / "financial_data.json"
    payload = json.loads(data_path.read_text(encoding="utf-8"))
    payload["statements"][0]["items"][0]["sources"] = {}
    write_json(data_path, payload)
    manifest = json.loads((package_dir / "manifest.json").read_text(encoding="utf-8"))
    manifest["artifact_hashes"] = compute_artifact_hashes(package_dir)
    write_json(package_dir / "manifest.json", manifest)

    result = validate_evidence_package(package_dir)
    gates = build_quality_gates(package_dir)

    assert not result.ok
    assert any("missing evidence" in error for error in result.errors)
    assert gates["decisions_by_target"]["canonical"]["decision"] == "block"
    assert gates["import_blocked"] is True
    assert gates["vector_ingest_blocked"] is True
    assert gates["force_allowed"] is False
    assert "package.evidence.missing" in gates["hard_gate_rule_ids"]


def test_evidence_resolvability_requires_a_reviewable_locator(tmp_path):
    package_dir = _write_package(tmp_path)
    data_path = package_dir / "metrics" / "financial_data.json"
    payload = json.loads(data_path.read_text(encoding="utf-8"))
    payload["statements"][0]["items"][0]["sources"]["2025-12-31"] = {
        "source_type": "pdf_statement_table",
        "source_id": "table_1",
        "table_index": 1,
    }
    write_json(data_path, payload)
    manifest = json.loads((package_dir / "manifest.json").read_text(encoding="utf-8"))
    source_map = source_map_from_financial_data(manifest=manifest, financial_data=payload, package_dir=package_dir)
    write_json(package_dir / "qa" / "source_map.json", source_map)
    manifest["artifact_hashes"] = compute_artifact_hashes(package_dir)
    write_json(package_dir / "manifest.json", manifest)

    result = validate_evidence_package(package_dir)
    gates = build_quality_gates(package_dir)

    assert not is_resolvable_evidence_source(payload["statements"][0]["items"][0]["sources"]["2025-12-31"], manifest=manifest)
    assert not result.ok
    assert any("unresolvable evidence" in error for error in result.errors)
    assert gates["evidence_coverage_ratio"] == 0
    assert gates["unresolvable_evidence_count"] == 1
    assert "unresolvable evidence present" in gates["block_reasons"]


def test_unresolvable_evidence_hard_blocks_vector_ingest_even_when_quality_passes(tmp_path):
    package_dir = _write_package(tmp_path)
    _mark_quality_pass(package_dir)
    data_path = package_dir / "metrics" / "financial_data.json"
    payload = json.loads(data_path.read_text(encoding="utf-8"))
    payload["statements"][0]["items"][0]["sources"]["2025-12-31"] = {
        "source_type": "pdf_statement_table",
        "source_id": "table_1",
        "table_index": 1,
    }
    write_json(data_path, payload)
    manifest = json.loads((package_dir / "manifest.json").read_text(encoding="utf-8"))
    source_map = source_map_from_financial_data(manifest=manifest, financial_data=payload, package_dir=package_dir)
    write_json(package_dir / "qa" / "source_map.json", source_map)
    manifest["artifact_hashes"] = compute_artifact_hashes(package_dir)
    write_json(package_dir / "manifest.json", manifest)

    gates = build_quality_gates(package_dir)

    assert gates["overall_status"] == "fail"
    assert gates["decisions_by_target"]["canonical"]["decision"] == "block"
    assert gates["decisions_by_target"]["retrieval"]["decision"] == "block"
    assert gates["import_blocked"] is True
    assert gates["vector_ingest_blocked"] is True
    assert gates["force_allowed"] is False
    assert "package.evidence.unresolvable" in gates["hard_gate_rule_ids"]


def test_quality_gates_review_pdf_value_verification_failures(tmp_path):
    package_dir = _write_package(tmp_path)
    _mark_quality_pass(package_dir)
    data_path = package_dir / "metrics" / "financial_data.json"
    payload = json.loads(data_path.read_text(encoding="utf-8"))
    payload["statements"][0]["items"][0]["raw_values"]["2025-12-31"] = "2,000"
    write_json(data_path, payload)
    manifest = json.loads((package_dir / "manifest.json").read_text(encoding="utf-8"))
    source_map = source_map_from_financial_data(manifest=manifest, financial_data=payload, package_dir=package_dir)
    write_json(package_dir / "qa" / "source_map.json", source_map)
    manifest["artifact_hashes"] = compute_artifact_hashes(package_dir)
    write_json(package_dir / "manifest.json", manifest)

    gates = build_quality_gates(package_dir)

    assert gates["overall_status"] == "warning"
    assert gates["decisions_by_target"]["draft"]["decision"] == "allow"
    assert gates["decisions_by_target"]["canonical"]["decision"] == "review"
    assert gates["force_allowed"] is True
    assert gates["evidence_value_verification_issue_count"] == 2
    assert "package.evidence.value_verification_failed" in gates["soft_gate_rule_ids"]
    value_gate = next(
        gate
        for gate in gates["gate_results"]
        if gate["rule_id"] == "package.evidence.value_verification_failed" and gate["target"] == "canonical"
    )
    assert value_gate["severity"] == "soft"
    assert value_gate["decision"] == "review"


def test_quality_gates_review_quote_value_verification_failures(tmp_path):
    package_dir = _write_package(tmp_path)
    _mark_quality_pass(package_dir)
    data_path = package_dir / "metrics" / "financial_data.json"
    payload = json.loads(data_path.read_text(encoding="utf-8"))
    payload["statements"][0]["items"][0]["sources"]["2025-12-31"]["quote_text"] = "Total assets | 2,000"
    write_json(data_path, payload)
    manifest = json.loads((package_dir / "manifest.json").read_text(encoding="utf-8"))
    source_map = source_map_from_financial_data(manifest=manifest, financial_data=payload, package_dir=package_dir)
    write_json(package_dir / "qa" / "source_map.json", source_map)
    manifest["artifact_hashes"] = compute_artifact_hashes(package_dir)
    write_json(package_dir / "manifest.json", manifest)

    gates = build_quality_gates(package_dir)

    assert gates["overall_status"] == "warning"
    assert gates["decisions_by_target"]["canonical"]["decision"] == "review"
    assert gates["evidence_value_verification_issue_count"] == 1
    assert gates["evidence_value_verification"]["quote_failed_count"] == 1
    assert gates["evidence_value_verification"]["issues"][0]["rule"] == "quote.value.explainable"


def test_quality_gates_review_xbrl_value_verification_failures(tmp_path):
    package_dir = _write_package(tmp_path)
    _mark_quality_pass(package_dir)
    data_path = package_dir / "metrics" / "financial_data.json"
    payload = json.loads(data_path.read_text(encoding="utf-8"))
    payload["statements"][0]["items"][0]["sources"]["2025-12-31"] = {
        "source_type": "xbrl_fact",
        "source_id": "us-gaap:Assets",
        "xbrl_tag": "us-gaap:Assets",
        "raw": {
            "context_ref": "c-2025",
            "unit_ref": "usd",
            "period_end": "2025-12-31",
            "decimals": "-6",
        },
    }
    write_json(data_path, payload)
    manifest = json.loads((package_dir / "manifest.json").read_text(encoding="utf-8"))
    source_map = source_map_from_financial_data(manifest=manifest, financial_data=payload, package_dir=package_dir)
    write_json(package_dir / "qa" / "source_map.json", source_map)
    manifest["artifact_hashes"] = compute_artifact_hashes(package_dir)
    write_json(package_dir / "manifest.json", manifest)

    gates = build_quality_gates(package_dir)

    assert gates["overall_status"] == "warning"
    assert gates["decisions_by_target"]["canonical"]["decision"] == "review"
    assert gates["evidence_value_verification"]["xbrl_checked_count"] == 1
    assert gates["evidence_value_verification"]["xbrl_failed_count"] == 1
    assert gates["evidence_value_verification"]["issues"][0]["rule"] == "xbrl.fields.present"
    assert "scale" in gates["evidence_value_verification"]["issues"][0]["reason"]


def test_quality_gates_fail_on_artifact_hash_mismatch(tmp_path):
    package_dir = _write_package(tmp_path)
    (package_dir / "sections" / "report.md").write_text("# Tampered\n", encoding="utf-8")

    gates = build_quality_gates(package_dir)

    assert gates["overall_status"] == "fail"
    assert gates["artifact_hash_status"] == "mismatch"
    assert gates["import_blocked"] is True
    assert gates["vector_ingest_blocked"] is True
    assert gates["force_allowed"] is False
    assert gates["decisions_by_target"]["draft"]["decision"] == "allow"
    assert gates["decisions_by_target"]["canonical"]["decision"] == "block"
    assert gates["decisions_by_target"]["retrieval"]["decision"] == "block"
    canonical_hash_gate = next(
        gate
        for gate in gates["gate_results"]
        if gate["rule_id"] == "package.artifact_hashes.mismatch" and gate["target"] == "canonical"
    )
    assert canonical_hash_gate["severity"] == "hard"
    assert canonical_hash_gate["decision"] == "block"
    assert "sections/report.md" in gates["artifact_hash_mismatches"]


def test_quality_gates_can_observe_without_blocking_promotions(tmp_path):
    package_dir = _write_package(tmp_path)
    quality_path = package_dir / "qa" / "quality_report.json"
    quality = json.loads(quality_path.read_text(encoding="utf-8"))
    quality["overall_status"] = "unknown"
    quality["required_statement_status"] = {
        "income_statement": "present",
        "balance_sheet": "present",
        "cash_flow_statement": "present",
    }
    write_json(quality_path, quality)
    checks_path = package_dir / "metrics" / "financial_checks.json"
    checks = json.loads(checks_path.read_text(encoding="utf-8"))
    checks["overall_status"] = "unknown"
    write_json(checks_path, checks)
    manifest = json.loads((package_dir / "manifest.json").read_text(encoding="utf-8"))
    manifest["quality_status"] = "unknown"
    manifest["artifact_hashes"] = compute_artifact_hashes(package_dir)
    write_json(package_dir / "manifest.json", manifest)

    gates = build_quality_gates(package_dir)

    assert gates["overall_status"] == "unknown"
    assert gates["import_blocked"] is False
    assert gates["decisions_by_target"]["canonical"]["decision"] == "allow"
    observe_gate = next(
        gate
        for gate in gates["gate_results"]
        if gate["rule_id"] == "package.quality_status.unknown" and gate["target"] == "canonical"
    )
    assert observe_gate["severity"] == "observe"
    assert observe_gate["decision"] == "allow"


def test_rule_advisories_are_visible_without_weakening_real_warning_gates(tmp_path):
    package_dir = _write_package(tmp_path)
    _mark_quality_pass(package_dir)
    quality_path = package_dir / "qa" / "quality_report.json"
    checks_path = package_dir / "metrics" / "financial_checks.json"
    quality = json.loads(quality_path.read_text(encoding="utf-8"))
    checks = json.loads(checks_path.read_text(encoding="utf-8"))
    advisory = "Use standard three-statement bridge checks."
    quality["rule_advisories"] = [advisory]
    checks["advisories"] = [advisory]
    write_json(quality_path, quality)
    write_json(checks_path, checks)
    manifest = json.loads((package_dir / "manifest.json").read_text(encoding="utf-8"))
    manifest["artifact_hashes"] = compute_artifact_hashes(package_dir)
    write_json(package_dir / "manifest.json", manifest)

    advisory_gates = build_quality_gates(package_dir)

    assert advisory_gates["rule_advisories"] == [advisory]
    assert "package.parser_or_rule_warnings.present" not in {
        gate["rule_id"] for gate in advisory_gates["gate_results"]
    }

    quality["rule_warnings"] = ["Suspicious HK financial table extraction requires review."]
    write_json(quality_path, quality)
    manifest["artifact_hashes"] = compute_artifact_hashes(package_dir)
    write_json(package_dir / "manifest.json", manifest)

    warning_gates = build_quality_gates(package_dir)
    canonical_warning_gate = next(
        gate
        for gate in warning_gates["gate_results"]
        if gate["rule_id"] == "package.parser_or_rule_warnings.present"
        and gate["target"] == "canonical"
    )

    assert warning_gates["rule_advisories"] == [advisory]
    assert canonical_warning_gate["severity"] == "soft"
    assert canonical_warning_gate["decision"] == "review"
