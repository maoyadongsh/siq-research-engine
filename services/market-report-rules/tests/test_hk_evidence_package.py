import json
from pathlib import Path

from market_report_rules_service.evidence_package import validate_evidence_package


def test_build_hk_evidence_package_from_parser_result(tmp_path, monkeypatch):
    repo_root = Path(__file__).resolve().parents[3]
    scripts_hk = repo_root / "scripts" / "hk"
    monkeypatch.syspath_prepend(str(scripts_hk))
    from hk_evidence_lib import write_hk_evidence_package, write_json

    pdf = tmp_path / "TENCENT_HK_00700_2025-12-31_年报_2026-04-09_hkex_test.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    metadata = pdf.with_suffix(pdf.suffix + ".metadata.json")
    write_json(
        metadata,
        {
            "candidate": {
                "source_id": "hkex",
                "market": "HK",
                "ticker": "00700",
                "company_id": "00700",
                "company_name": "TENCENT",
                "report_type": "annual",
                "form": "annual",
                "accession_number": "12100024",
                "report_end": "2025-12-31",
                "published_at": "2026-04-09",
                "document_url": "https://www1.hkexnews.hk/test.pdf",
            }
        },
    )
    parser_dir = tmp_path / "parser"
    parser_dir.mkdir()
    table = (
        "<table>"
        "<tr><td></td><td>2025</td><td>2024</td></tr>"
        "<tr><td>Total assets</td><td>1000</td><td>900</td></tr>"
        "<tr><td>Total liabilities</td><td>600</td><td>550</td></tr>"
        "<tr><td>Total equity</td><td>400</td><td>350</td></tr>"
        "</table>"
    )
    write_json(parser_dir / "quality_report.json", {"overall_status": "ok", "warnings": []})
    write_json(
        parser_dir / "document_full.json",
        {
            "task": {"filename": pdf.name},
            "markdown": {"content": "# TENCENT\n\n管理层讨论与分析。\n"},
            "content_list": [
                {
                    "type": "table",
                    "table_body": table,
                    "table_caption": ["Consolidated Statement of Financial Position"],
                    "page_idx": 87,
                }
            ],
            "content_list_enhanced": {
                "footnotes": {
                    "references": [{"id": "fn_ref_1", "marker": "1", "page": 88}],
                    "definitions": [{"id": "fn_def_1", "marker": "1", "text": "Footnote definition"}],
                    "bindings": [{"reference_id": "fn_ref_1", "definition_id": "fn_def_1"}],
                    "summary": {"count": 1},
                },
                "toc": {
                    "headings": [{"level": 1, "title": "管理层讨论与分析", "page": 3}],
                    "toc_candidates": [{"title": "Financial Highlights", "page": 2}],
                    "content_headings": [{"title": "Consolidated Financial Statements", "page": 88}],
                    "summary": {"count": 3},
                },
                "financial_note_links": {
                    "links": [{"statement": "balance_sheet", "note": "1", "page": 88}],
                    "summary": {"count": 1},
                },
                "quality_signals": {
                    "signals": [{"type": "table_header", "status": "ok", "page": 88}],
                    "summary": {"count": 1},
                },
                "tables": [{"table_index": 1, "content_table_source_id": 1, "pdf_page_number": 88, "relations": [{"target_table_index": 1, "relation_type": "statement_note"}]}],
                "pages": [{"page_number": 88, "image_count": 0, "table_count": 1}],
            },
        },
    )

    package_dir = write_hk_evidence_package(pdf, parser_dir, tmp_path / "wiki", metadata, force=True)
    result = validate_evidence_package(package_dir)

    expected_files = [
        "parser/document_full.json",
        "parser/content_list_enhanced.json",
        "parser/table_relations.json",
        "sections/report_complete.md",
        "qa/footnotes.json",
        "qa/toc.json",
        "qa/financial_note_links.json",
        "qa/table_quality_signals.json",
    ]

    assert result.ok, result.errors
    assert package_dir == tmp_path / "wiki" / "companies" / "00700-TENCENT" / "reports" / "2025-annual-12100024"
    assert result.manifest["wiki_company_path"] == "companies/00700-TENCENT"
    assert result.manifest["wiki_report_path"] == "companies/00700-TENCENT/reports/2025-annual-12100024"
    assert result.manifest["report_id"] == "2025-annual-12100024"
    assert (package_dir / "metrics" / "load_plan.json").is_file()
    assert (package_dir / "qa" / "source_map.json").is_file()
    for rel_path in expected_files:
        assert (package_dir / rel_path).is_file(), rel_path
        assert rel_path in result.manifest["artifact_hashes"]
    parser_financial_data = json.loads((package_dir / "parser" / "financial_data.json").read_text(encoding="utf-8"))
    parser_financial_checks = json.loads((package_dir / "parser" / "financial_checks.json").read_text(encoding="utf-8"))
    assert parser_financial_data == {
        "statements": [],
        "key_metrics": [],
        "operating_metrics": [],
        "warnings": [],
        "summary": {},
    }
    assert parser_financial_checks == {
        "overall_status": "unknown",
        "checks": [],
        "warnings": [],
        "summary": {},
    }
    company_json = json.loads((tmp_path / "wiki" / "companies" / "00700-TENCENT" / "company.json").read_text(encoding="utf-8"))
    assert company_json["company_id"] == "HK:00700"
    assert company_json["market"] == "HK"
    assert company_json["stock_code"] == "00700"
    assert company_json["primary_report_id"] == "2025-annual-12100024"
    assert company_json["reports"][0]["package_path"] == "reports/2025-annual-12100024"
    assert company_json["metrics"]["latest"]["financial_data"] == "reports/2025-annual-12100024/metrics/financial_data.json"
    catalog = json.loads((tmp_path / "wiki" / "_meta" / "company_catalog.json").read_text(encoding="utf-8"))
    assert catalog["market"] == "HK"
    assert catalog["companies"][0]["company_path"] == "companies/00700-TENCENT"


def test_build_hk_evidence_package_preserves_parser_financial_files_and_normalizes_malformed_enhanced_payloads(tmp_path, monkeypatch):
    repo_root = Path(__file__).resolve().parents[3]
    scripts_hk = repo_root / "scripts" / "hk"
    monkeypatch.syspath_prepend(str(scripts_hk))
    from hk_evidence_lib import write_hk_evidence_package, write_json

    pdf = tmp_path / "TENCENT_HK_00700_2025-12-31_年报_2026-04-09_hkex_test.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    metadata = pdf.with_suffix(pdf.suffix + ".metadata.json")
    write_json(
        metadata,
        {
            "candidate": {
                "source_id": "hkex",
                "market": "HK",
                "ticker": "00700",
                "company_id": "00700",
                "company_name": "TENCENT",
                "report_type": "annual",
                "form": "annual",
                "accession_number": "12100024",
                "report_end": "2025-12-31",
                "published_at": "2026-04-09",
                "document_url": "https://www1.hkexnews.hk/test.pdf",
            }
        },
    )
    parser_dir = tmp_path / "parser"
    parser_dir.mkdir()
    write_json(
        parser_dir / "document_full.json",
        {
            "task": {"filename": pdf.name},
            "markdown": {"content": "# TENCENT\n"},
            "content_list": [],
            "content_list_enhanced": {
                "footnotes": "broken",
                "toc": "broken",
                "financial_note_links": "broken",
                "quality_signals": "broken",
                "tables": [{"table_index": 1, "content_table_source_id": 1, "pdf_page_number": 88, "relations": "broken"}],
                "pages": "broken",
            },
        },
    )
    parser_financial_data = {
        "schema_version": 13,
        "task_id": "parser-task",
        "statements": [{"statement_id": "parser-balance-sheet"}],
        "key_metrics": [],
        "operating_metrics": [],
        "warnings": [],
        "summary": {"statement_count": 1},
    }
    parser_financial_checks = {
        "schema_version": 12,
        "task_id": "parser-task",
        "overall_status": "pass",
        "checks": [{"rule_id": "parser-check"}],
        "warnings": [],
        "summary": {"total": 1},
    }
    write_json(parser_dir / "financial_data.json", parser_financial_data)
    write_json(parser_dir / "financial_checks.json", parser_financial_checks)

    package_dir = write_hk_evidence_package(pdf, parser_dir, tmp_path / "wiki", metadata, force=True)

    assert json.loads((package_dir / "parser" / "financial_data.json").read_text(encoding="utf-8")) == parser_financial_data
    assert json.loads((package_dir / "parser" / "financial_checks.json").read_text(encoding="utf-8")) == parser_financial_checks
    assert json.loads((package_dir / "qa" / "footnotes.json").read_text(encoding="utf-8"))["payload"] == {
        "references": [],
        "definitions": [],
        "bindings": [],
        "summary": {},
    }
    assert json.loads((package_dir / "qa" / "toc.json").read_text(encoding="utf-8"))["payload"] == {
        "headings": [],
        "toc_candidates": [],
        "content_headings": [],
        "summary": {},
    }
    assert json.loads((package_dir / "qa" / "financial_note_links.json").read_text(encoding="utf-8"))["payload"] == {
        "links": [],
        "summary": {},
    }
    assert json.loads((package_dir / "qa" / "table_quality_signals.json").read_text(encoding="utf-8"))["payload"] == {
        "signals": [],
        "summary": {},
    }
    assert json.loads((package_dir / "parser" / "content_list_enhanced.json").read_text(encoding="utf-8")) == {
        "footnotes": {},
        "toc": {},
        "financial_note_links": {},
        "quality_signals": {},
        "tables": [{"table_index": 1, "content_table_source_id": 1, "pdf_page_number": 88, "relations": []}],
        "pages": [],
    }
    assert json.loads((package_dir / "parser" / "table_relations.json").read_text(encoding="utf-8")) == {
        "schema_version": "hk_table_relations_v1",
        "relations": [],
    }


def test_migrate_legacy_hk_reports_to_company_wiki_layout(tmp_path, monkeypatch):
    repo_root = Path(__file__).resolve().parents[3]
    scripts_hk = repo_root / "scripts" / "hk"
    monkeypatch.syspath_prepend(str(scripts_hk))
    from hk_evidence_lib import write_json
    from migrate_hk_reports_to_company_wiki import migrate_packages

    legacy_package = tmp_path / "wiki" / "hk_reports" / "00700" / "2025" / "annual_12100024"
    for name in ("raw", "sections", "tables", "xbrl", "metrics", "qa"):
        (legacy_package / name).mkdir(parents=True, exist_ok=True)
    (legacy_package / "raw" / "report.pdf").write_bytes(b"%PDF-1.4")
    (legacy_package / "sections" / "report.md").write_text("# TENCENT\n", encoding="utf-8")
    write_json(legacy_package / "tables" / "table_index.json", {"tables": []})
    write_json(legacy_package / "xbrl" / "facts_raw.json", {"facts": []})
    write_json(legacy_package / "metrics" / "financial_data.json", {"statements": [], "key_metrics": [], "operating_metrics": [], "warnings": []})
    write_json(legacy_package / "metrics" / "financial_checks.json", {"overall_status": "warning", "checks": [], "warnings": []})
    write_json(legacy_package / "qa" / "quality_report.json", {"overall_status": "warning", "table_count": 0})
    write_json(legacy_package / "qa" / "source_map.json", {"entries": []})
    write_json(
        legacy_package / "manifest.json",
        {
            "schema_version": "market_evidence_package_v1",
            "market": "HK",
            "filing_id": "HK:00700:12100024",
            "company_id": "HK:00700",
            "ticker": "00700",
            "stock_code": "00700",
            "hkex_stock_code": "00700",
            "company_name": "TENCENT",
            "source_id": "hkex",
            "form": "annual",
            "report_type": "annual",
            "fiscal_year": 2025,
            "fiscal_period": "FY",
            "period_end": "2025-12-31",
            "published_at": "2026-04-09",
            "local_source_path": "raw/report.pdf",
            "accounting_standard": "HKFRS",
            "parser_version": "p1",
            "rules_version": "r1",
            "quality_status": "warning",
            "accession_number": "12100024",
            "artifact_hashes": {},
        },
    )

    summary = migrate_packages(tmp_path / "wiki" / "hk_reports", tmp_path / "wiki" / "hk", force=True)

    target = tmp_path / "wiki" / "hk" / "companies" / "00700-TENCENT" / "reports" / "2025-annual-12100024"
    assert summary["migrated"] == 1
    assert target.is_dir()
    manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["report_id"] == "2025-annual-12100024"
    assert manifest["wiki_company_path"] == "companies/00700-TENCENT"
    assert manifest["wiki_report_path"] == "companies/00700-TENCENT/reports/2025-annual-12100024"
    company_json = json.loads((tmp_path / "wiki" / "hk" / "companies" / "00700-TENCENT" / "company.json").read_text(encoding="utf-8"))
    assert company_json["primary_report_id"] == "2025-annual-12100024"
    assert company_json["metrics"]["latest"]["financial_data"] == "reports/2025-annual-12100024/metrics/financial_data.json"
    catalog = json.loads((tmp_path / "wiki" / "hk" / "_meta" / "company_catalog.json").read_text(encoding="utf-8"))
    assert catalog["company_count"] == 1
    assert catalog["companies"][0]["company_path"] == "companies/00700-TENCENT"
