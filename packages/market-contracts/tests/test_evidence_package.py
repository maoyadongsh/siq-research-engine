import json
from pathlib import Path

from siq_market_contracts import (
    SCHEMA_VERSION,
    compute_artifact_hashes,
    read_market_package_detail,
    read_market_package_summary,
    source_map_from_financial_data,
    stable_parse_run_id,
    validate_evidence_package,
    write_json,
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


def _write_package(root: Path) -> Path:
    package_dir = root / "hk_reports" / "00700" / "2025" / "annual_12100024"
    for name in ("raw", "sections", "tables", "xbrl", "metrics", "qa", "parser"):
        (package_dir / name).mkdir(parents=True, exist_ok=True)
    (package_dir / "README.md").write_text("# Test\n", encoding="utf-8")
    (package_dir / "raw" / "report.pdf").write_bytes(b"%PDF-1.4 test")
    (package_dir / "sections" / "report.md").write_text("# Report\n", encoding="utf-8")
    (package_dir / "sections" / "report_complete.md").write_text("# Report Complete\n", encoding="utf-8")
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
        package_dir / "parser" / "document_full.json",
        {
            "content_list_enhanced": {
                "footnotes": {"references": [{"id": "fn1"}], "definitions": [], "bindings": [], "summary": {"count": 1}},
                "toc": {"headings": [{"title": "Overview"}], "toc_candidates": [], "content_headings": [], "summary": {"count": 1}},
                "financial_note_links": {"links": [{"note": "1"}], "summary": {"count": 1}},
                "tables": [{"table_index": 1, "relations": [{"type": "footnote", "target": "fn1"}]}],
                "quality_signals": {"tables": [{"table_index": 1, "score": 0.95}], "summary": {"count": 1}},
                "pages": [{"page_number": 88}],
            }
        },
    )
    write_json(
        package_dir / "parser" / "content_list_enhanced.json",
        {
            "footnotes": {"references": [{"id": "fn1"}], "definitions": [], "bindings": [], "summary": {"count": 1}},
            "toc": {"headings": [{"title": "Overview"}], "toc_candidates": [], "content_headings": [], "summary": {"count": 1}},
            "financial_note_links": {"links": [{"note": "1"}], "summary": {"count": 1}},
            "tables": [{"table_index": 1, "relations": [{"type": "footnote", "target": "fn1"}]}],
            "quality_signals": {"tables": [{"table_index": 1, "score": 0.95}], "summary": {"count": 1}},
            "pages": [{"page_number": 88}],
        },
    )
    write_json(package_dir / "parser" / "table_relations.json", {"schema_version": "hk_table_relations_v1", "relations": [{"type": "footnote", "target": "fn1"}]})
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
    write_json(package_dir / "qa" / "footnotes.json", {"schema_version": "hk_footnotes_v1", "payload": {"references": [{"id": "fn1"}], "definitions": [], "bindings": [], "summary": {"count": 1}}})
    write_json(package_dir / "qa" / "toc.json", {"schema_version": "hk_toc_v1", "payload": {"headings": [{"title": "Overview"}], "toc_candidates": [], "content_headings": [], "summary": {"count": 1}}})
    write_json(package_dir / "qa" / "financial_note_links.json", {"schema_version": "hk_financial_note_links_v1", "payload": {"links": [{"note": "1"}], "summary": {"count": 1}}})
    write_json(package_dir / "qa" / "table_quality_signals.json", {"schema_version": "hk_table_quality_signals_v1", "payload": {"tables": [{"table_index": 1, "score": 0.95}], "summary": {"count": 1}}})
    manifest["artifact_hashes"] = compute_artifact_hashes(package_dir)
    write_json(package_dir / "manifest.json", manifest)
    return package_dir


def test_validate_and_read_market_package(tmp_path):
    package_dir = _write_package(tmp_path)

    validation = validate_evidence_package(package_dir)
    summary = read_market_package_summary(package_dir, display_path="hk_reports/00700/2025/annual_12100024")
    detail = read_market_package_detail(package_dir, display_path="hk_reports/00700/2025/annual_12100024")

    assert validation.ok, validation.errors
    assert summary["package_path"] == "hk_reports/00700/2025/annual_12100024"
    assert summary["paths"]["manifest"] == "manifest.json"
    assert summary["paths"]["document_full"] == "parser/document_full.json"
    assert summary["paths"]["content_list_enhanced"] == "parser/content_list_enhanced.json"
    assert summary["paths"]["report_complete"] == "sections/report_complete.md"
    assert summary["paths"]["footnotes"] == "qa/footnotes.json"
    assert summary["paths"]["toc"] == "qa/toc.json"
    assert summary["paths"]["financial_note_links"] == "qa/financial_note_links.json"
    assert summary["paths"]["table_quality_signals"] == "qa/table_quality_signals.json"
    assert summary["counts"] == {"sections": 1, "tables": 1, "raw_facts": 0, "metrics": 1, "evidence": 1}
    assert detail["manifest"]["schema_version"] == SCHEMA_VERSION
    assert detail["metrics"] == [{"metric_id": "m1"}]
    assert detail["tables"] == [{"table_index": 1}]
    assert detail["parser_artifacts"]["document_full"]["content_list_enhanced"]["footnotes"]["references"] == [{"id": "fn1"}]
    assert detail["parser_artifacts"]["content_list_enhanced"]["toc"]["headings"] == [{"title": "Overview"}]
    assert detail["parser_artifacts"]["table_relations"]["relations"] == [{"type": "footnote", "target": "fn1"}]
    assert detail["qa_artifacts"]["footnotes"]["payload"]["references"] == [{"id": "fn1"}]
    assert detail["qa_artifacts"]["toc"]["payload"]["headings"] == [{"title": "Overview"}]
    assert detail["qa_artifacts"]["financial_note_links"]["payload"]["links"] == [{"note": "1"}]
    assert detail["qa_artifacts"]["table_quality_signals"]["payload"]["tables"] == [{"table_index": 1, "score": 0.95}]


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

    assert not result.ok
    assert any("missing evidence" in error for error in result.errors)
