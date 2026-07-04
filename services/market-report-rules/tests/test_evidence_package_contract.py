import json
from pathlib import Path

from market_report_rules_service.evidence_package import (
    SCHEMA_VERSION,
    compute_artifact_hashes,
    source_map_from_financial_data,
    stable_parse_run_id,
    validate_evidence_package,
    write_json,
)


def _minimal_financial_data() -> dict:
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


def _write_package(root: Path) -> Path:
    package_dir = root / "hk" / "companies" / "00700-TENCENT" / "reports" / "2025-annual-12100024"
    for name in ("raw", "sections", "tables", "xbrl", "metrics", "qa"):
        (package_dir / name).mkdir(parents=True, exist_ok=True)
    (package_dir / "README.md").write_text("# Test\n", encoding="utf-8")
    (package_dir / "raw" / "report.pdf").write_bytes(b"%PDF-1.4 test")
    (package_dir / "sections" / "report.md").write_text("# Report\n", encoding="utf-8")
    write_json(package_dir / "tables" / "table_index.json", {"tables": []})
    write_json(package_dir / "xbrl" / "facts_raw.json", {"facts": []})
    financial_data = _minimal_financial_data()
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


def test_market_evidence_package_validator_accepts_minimal_package(tmp_path):
    package_dir = _write_package(tmp_path)

    result = validate_evidence_package(package_dir)

    assert result.ok, result.errors
    assert result.manifest["schema_version"] == SCHEMA_VERSION
    assert result.manifest["market"] == "HK"
    assert "metrics/financial_data.json" in result.artifact_hashes


def test_market_evidence_package_validator_rejects_missing_evidence(tmp_path):
    package_dir = _write_package(tmp_path)
    data_path = package_dir / "metrics" / "financial_data.json"
    payload = json.loads(data_path.read_text(encoding="utf-8"))
    payload["statements"][0]["items"][0]["sources"] = {}
    write_json(data_path, payload)
    manifest = json.loads((package_dir / "manifest.json").read_text(encoding="utf-8"))
    manifest["artifact_hashes"] = compute_artifact_hashes(package_dir)
    write_json(package_dir / "manifest.json", manifest)

    result = validate_evidence_package(package_dir)

    assert not result.ok
    assert any("missing evidence" in error for error in result.errors)
