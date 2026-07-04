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


def test_build_hk_evidence_package_uses_standalone_enhanced_payload_when_document_full_lacks_embedded_payload(tmp_path, monkeypatch):
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
    write_json(parser_dir / "quality_report.json", {"overall_status": "ok", "warnings": []})
    write_json(
        parser_dir / "document_full.json",
        {
            "task": {"filename": pdf.name},
            "markdown": {"content": "# TENCENT\n\nStandalone enhanced fixture.\n"},
            "content_list": [
                {
                    "type": "table",
                    "table_body": (
                        "<table>"
                        "<tr><td></td><td>2025</td><td>2024</td></tr>"
                        "<tr><td>Total assets</td><td>1000</td><td>900</td></tr>"
                        "</table>"
                    ),
                    "table_caption": ["Consolidated Statement of Financial Position"],
                    "page_idx": 87,
                }
            ],
        },
    )
    standalone_enhanced = {
        "footnotes": {
            "references": [{"id": "standalone_ref", "marker": "1", "page": 88}],
            "definitions": [{"id": "standalone_def", "marker": "1", "text": "Standalone definition"}],
            "bindings": [{"reference_id": "standalone_ref", "definition_id": "standalone_def"}],
            "summary": {"count": 1},
        },
        "toc": {
            "headings": [{"level": 1, "title": "Standalone heading", "page": 3}],
            "toc_candidates": [{"title": "Standalone toc", "page": 2}],
            "content_headings": [{"title": "Standalone content heading", "page": 88}],
            "summary": {"count": 3},
        },
        "financial_note_links": {
            "links": [{"statement": "balance_sheet", "note": "1", "page": 88}],
            "summary": {"count": 1},
        },
        "quality_signals": {
            "signals": [{"type": "standalone_signal", "status": "ok", "page": 88}],
            "summary": {"count": 1},
        },
        "tables": [
            {
                "table_index": 1,
                "content_table_source_id": 1,
                "pdf_page_number": 88,
                "relations": [{"target_table_index": 1, "relation_type": "standalone_relation"}],
            }
        ],
        "pages": [{"page_number": 88, "image_count": 0, "table_count": 1}],
    }
    write_json(parser_dir / "content_list_enhanced.json", standalone_enhanced)

    package_dir = write_hk_evidence_package(pdf, parser_dir, tmp_path / "wiki", metadata, force=True)
    result = validate_evidence_package(package_dir)

    assert result.ok, result.errors
    assert json.loads((package_dir / "parser" / "content_list_enhanced.json").read_text(encoding="utf-8")) == standalone_enhanced
    assert json.loads((package_dir / "qa" / "footnotes.json").read_text(encoding="utf-8"))["payload"] == standalone_enhanced["footnotes"]
    assert json.loads((package_dir / "qa" / "toc.json").read_text(encoding="utf-8"))["payload"] == standalone_enhanced["toc"]
    assert json.loads((package_dir / "qa" / "financial_note_links.json").read_text(encoding="utf-8"))["payload"] == standalone_enhanced["financial_note_links"]
    assert json.loads((package_dir / "qa" / "table_quality_signals.json").read_text(encoding="utf-8"))["payload"] == standalone_enhanced["quality_signals"]
    assert json.loads((package_dir / "parser" / "table_relations.json").read_text(encoding="utf-8")) == {
        "schema_version": "hk_table_relations_v1",
        "relations": [
            {
                "table_index": 1,
                "content_table_source_id": 1,
                "pdf_page_number": 88,
                "target_table_index": 1,
                "relation_type": "standalone_relation",
            }
        ],
    }


def test_force_rebuild_preserves_package_local_source_inputs(tmp_path, monkeypatch):
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
    parser_quality = {"schema_version": "hk_parser_quality_report_v1", "overall_status": "ok", "warnings": ["parser retained"]}
    parser_financial_data = {
        "schema_version": 13,
        "task_id": "package-local-parser-task",
        "statements": [{"statement_id": "parser-balance-sheet"}],
        "key_metrics": [{"metric_id": "parser-revenue"}],
        "operating_metrics": [],
        "warnings": ["parser financial retained"],
        "summary": {"statement_count": 1},
    }
    parser_financial_checks = {
        "schema_version": 12,
        "task_id": "package-local-parser-task",
        "overall_status": "pass",
        "checks": [{"rule_id": "parser-check"}],
        "warnings": ["parser checks retained"],
        "summary": {"total": 1},
    }
    enhanced_payload = {
        "footnotes": {
            "references": [{"id": "fn_ref_1", "marker": "1", "page": 88}],
            "definitions": [{"id": "fn_def_1", "marker": "1", "text": "Retained footnote"}],
            "bindings": [{"reference_id": "fn_ref_1", "definition_id": "fn_def_1"}],
            "summary": {"count": 1},
        },
        "toc": {
            "headings": [{"level": 1, "title": "Retained heading", "page": 3}],
            "toc_candidates": [{"title": "Retained toc", "page": 2}],
            "content_headings": [{"title": "Retained content", "page": 88}],
            "summary": {"count": 3},
        },
        "financial_note_links": {
            "links": [{"statement": "balance_sheet", "note": "1", "page": 88}],
            "summary": {"count": 1},
        },
        "quality_signals": {
            "signals": [{"type": "package_local_parser", "status": "ok", "page": 88}],
            "summary": {"count": 1},
        },
        "tables": [
            {
                "table_index": 1,
                "content_table_source_id": 1,
                "pdf_page_number": 88,
                "relations": [{"target_table_index": 1, "relation_type": "retained_relation"}],
            }
        ],
        "pages": [{"page_number": 88, "image_count": 0, "table_count": 1}],
    }
    write_json(parser_dir / "quality_report.json", parser_quality)
    write_json(parser_dir / "financial_data.json", parser_financial_data)
    write_json(parser_dir / "financial_checks.json", parser_financial_checks)
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
            "content_list_enhanced": enhanced_payload,
        },
    )

    package_dir = write_hk_evidence_package(pdf, parser_dir, tmp_path / "wiki", metadata, force=True)
    package_pdf = package_dir / "raw" / "report.pdf"
    package_metadata = package_dir / "raw" / "report.metadata.json"
    package_parser_dir = package_dir / "parser"

    rebuilt_dir = write_hk_evidence_package(package_pdf, package_parser_dir, tmp_path / "wiki", package_metadata, force=True)
    result = validate_evidence_package(rebuilt_dir)

    assert rebuilt_dir == package_dir
    assert package_pdf.is_file()
    assert package_metadata.is_file()
    assert package_parser_dir.is_dir()
    assert result.ok, result.errors
    assert json.loads((rebuilt_dir / "parser" / "quality_report.json").read_text(encoding="utf-8")) == parser_quality
    assert json.loads((rebuilt_dir / "parser" / "financial_data.json").read_text(encoding="utf-8")) == parser_financial_data
    assert json.loads((rebuilt_dir / "parser" / "financial_checks.json").read_text(encoding="utf-8")) == parser_financial_checks
    assert json.loads((rebuilt_dir / "parser" / "content_list_enhanced.json").read_text(encoding="utf-8")) == enhanced_payload
    assert json.loads((rebuilt_dir / "qa" / "financial_note_links.json").read_text(encoding="utf-8"))["payload"] == enhanced_payload["financial_note_links"]
    assert json.loads((rebuilt_dir / "qa" / "table_quality_signals.json").read_text(encoding="utf-8"))["payload"] == enhanced_payload["quality_signals"]



def test_hk_parsed_tables_use_enhanced_header_preview_when_content_list_is_empty(monkeypatch):
    repo_root = Path(__file__).resolve().parents[3]
    scripts_hk = repo_root / "scripts" / "hk"
    monkeypatch.syspath_prepend(str(scripts_hk))
    from hk_evidence_lib import parsed_tables_from_document_full

    tables = parsed_tables_from_document_full(
        {"content_list": []},
        {
            "tables": [
                {
                    "table_index": 1,
                    "pdf_page_number": 4,
                    "heading": "(1) Principal financial data",
                    "structure": {
                        "header_preview": [
                            "Items | 2025RMB million | 2024RMB million | Change(%)",
                            "Operating income | 2,783,583 | 3,074,562 | (9.5)",
                            "Profit before taxation | 43,143 | 75,103 | (42.6)",
                        ]
                    },
                    "preview": "Items 2025RMB million 2024RMB million Change(%) Operating income 2,783,583 3,074,562 (9.5)",
                }
            ]
        },
    )

    assert len(tables) == 1
    assert tables[0].title == "(1) Principal financial data"
    assert tables[0].page_number == 4
    assert tables[0].rows == [
        ["Items", "2025RMB million", "2024RMB million", "Change(%)"],
        ["Operating income", "2,783,583", "3,074,562", "(9.5)"],
        ["Profit before taxation", "43,143", "75,103", "(42.6)"],
    ]


def test_build_hk_evidence_package_falls_back_to_parser_financial_data_and_table_index(tmp_path, monkeypatch):
    repo_root = Path(__file__).resolve().parents[3]
    scripts_hk = repo_root / "scripts" / "hk"
    monkeypatch.syspath_prepend(str(scripts_hk))
    from hk_evidence_lib import write_hk_evidence_package, write_json

    pdf = tmp_path / "TENCENT_HK_00700_2025-12-31_年报_2026-04-09_hkex_test.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    metadata = pdf.with_suffix(pdf.suffix + ".metadata.json")
    write_json(metadata, {"candidate": {"source_id": "hkex", "market": "HK", "ticker": "00700", "company_id": "00700", "company_name": "TENCENT", "report_type": "annual", "form": "annual", "accession_number": "12100024", "report_end": "2025-12-31", "published_at": "2026-04-09", "document_url": "https://www1.hkexnews.hk/test.pdf"}})
    parser_dir = tmp_path / "parser"
    parser_dir.mkdir()
    write_json(parser_dir / "document_full.json", {"task": {"filename": pdf.name}, "markdown": {"content": "# TENCENT\n"}, "content_list": [], "content_list_enhanced": {"tables": [{"table_id": "hk_table_0001", "table_index": 1, "page_number": 4, "row_count": 4, "column_count": 3, "unit": "RMB in millions", "currency": "CNY", "raw": {"table_index": 1, "pdf_page_number": 4, "preview": "As of March 31 2025 2024 Total assets 250000 230000", "structure": {"header_preview": ["As of March 31", "2025 | 2024"]}}}]}})
    write_json(parser_dir / "table_index.json", {"schema_version": "hk_table_index_v1", "tables": [{"table_id": "hk_table_0001", "table_index": 1, "title": "Consolidated Balance Sheets", "page_number": 4, "row_count": 4, "column_count": 3, "table_json_path": "tables/table_0001.json", "unit": "RMB in millions", "currency": "CNY", "raw": {"preview": "Total assets 250000 230000"}}]})
    parser_financial_data = {
        "schema_version": 1, "rule_version": "hkex_rules_v1", "profile_id": "hkex_pdf_tables_v1", "market": "HK", "artifact_id": "HK:00700:12100024", "company_id": "HK:00700", "ticker": "00700", "company_name": "TENCENT", "report_id": "HK:00700:12100024", "report_type": "annual", "report_form": "annual", "fiscal_year": 2025, "fiscal_period": "FY", "period_end": "2025-12-31", "accounting_standard": "HKFRS", "industry_profile": "general", "company_overrides": {},
        "statements": [{"statement_id": "balance_sheet", "statement_type": "balance_sheet", "statement_name": "Balance Sheet", "scope": "consolidated", "scope_name": "Consolidated", "title": "Balance Sheet", "unit": "RMB in millions", "scale": "1000000", "currency": "CNY", "table_indexes": [1], "columns": [{"period_key": "2025-12-31", "label": "2025-12-31"}], "items": [{"item_index": 1, "name": "Total assets", "canonical_name": "total_assets", "statement_type": "balance_sheet", "values": {"2025-12-31": "1000"}, "raw_values": {"2025-12-31": "1,000"}, "sources": {"2025-12-31": {"source_type": "pdf_table", "source_id": "hk_table_0001", "page_number": 4, "table_index": 1, "row_index": 2, "column_index": 1, "url": "https://www1.hkexnews.hk/test.pdf", "quote_text": "Total assets | 1,000"}}, "unit": "RMB in millions", "currency": "CNY", "scale": "1000000", "periods": {"2025-12-31": {"period_end": "2025-12-31", "fiscal_year": 2025, "fiscal_period": "FY"}}, "taxonomy": "hkex_pdf_table", "gaap_status": "reported_gaap", "confidence": "0.84", "raw": []}]}],
        "key_metrics": [], "operating_metrics": [], "summary": {"statement_count": 1, "statement_item_count": 1, "evidence_count": 1}, "warnings": [], "generated_at": "2026-07-04T00:00:00+00:00"
    }
    parser_financial_checks = {"schema_version": 1, "rule_version": "hkex_rules_v1", "profile_id": "hkex_pdf_tables_v1", "market": "HK", "artifact_id": "HK:00700:12100024", "industry_profile": "general", "overall_status": "pass", "summary": {"pass": 1, "fail": 0}, "checks": [], "warnings": [], "generated_at": "2026-07-04T00:00:00+00:00"}
    write_json(parser_dir / "financial_data.json", parser_financial_data)
    write_json(parser_dir / "financial_checks.json", parser_financial_checks)

    package_dir = write_hk_evidence_package(pdf, parser_dir, tmp_path / "wiki", metadata, force=True)

    assert json.loads((package_dir / "metrics" / "financial_data.json").read_text(encoding="utf-8")) == parser_financial_data
    assert json.loads((package_dir / "metrics" / "financial_checks.json").read_text(encoding="utf-8")) == parser_financial_checks
    table_index = json.loads((package_dir / "tables" / "table_index.json").read_text(encoding="utf-8"))
    assert len(table_index["tables"]) == 1
    assert (package_dir / "tables" / "table_0001.json").is_file()
    normalized = json.loads((package_dir / "metrics" / "normalized_metrics.json").read_text(encoding="utf-8"))
    source_map = json.loads((package_dir / "qa" / "source_map.json").read_text(encoding="utf-8"))
    assert normalized["metrics"][0]["canonical_name"] == "total_assets"
    assert source_map["entries"][0]["table_json_path"] == "tables/table_0001.json"



def test_hk_financial_metric_count_accepts_long_fact_items(monkeypatch):
    repo_root = Path(__file__).resolve().parents[3]
    scripts_hk = repo_root / "scripts" / "hk"
    monkeypatch.syspath_prepend(str(scripts_hk))
    import hk_evidence_lib

    data = {
        "statements": [
            {
                "statement_id": "income_statement",
                "items": [
                    {"canonical_name": "operating_revenue", "value": "2783583", "period_key": "2025-12-31"},
                    {"canonical_name": "net_profit", "value": "35893", "period_key": "2025-12-31"},
                ],
            }
        ],
        "key_metrics": [{"canonical_name": "basic_eps", "value": "0.262", "period_key": "2025-12-31"}],
        "operating_metrics": [{"canonical_name": "proved_reserves", "value": "2074", "period_key": "2025-12-31"}],
    }

    assert hk_evidence_lib._financial_metric_count(data) == 4


def test_hk_financial_data_currency_prefers_unit_over_stale_currency(monkeypatch):
    repo_root = Path(__file__).resolve().parents[3]
    scripts_hk = repo_root / "scripts" / "hk"
    monkeypatch.syspath_prepend(str(scripts_hk))
    import hk_evidence_lib

    data = {
        "statements": [
            {
                "statement_id": "balance_sheet",
                "unit": "RMB’Million",
                "currency": "HKD",
                "scale": "1",
                "items": [
                    {
                        "name": "Total assets",
                        "canonical_name": "total_assets",
                        "unit": "RMB’Million",
                        "currency": "HKD",
                        "scale": "1",
                        "values": {"2025-12-31": "1000"},
                    }
                ],
            }
        ],
        "key_metrics": [],
        "operating_metrics": [],
    }

    normalized = hk_evidence_lib._normalize_financial_data_units(data)

    statement = normalized["statements"][0]
    item = statement["items"][0]
    assert statement["currency"] == "CNY"
    assert item["currency"] == "CNY"
    assert statement["scale"] == "1000000"
    assert item["scale"] == "1000000"



def test_hk_metadata_infers_industry_profile_from_issuer_identity(tmp_path, monkeypatch):
    repo_root = Path(__file__).resolve().parents[3]
    scripts_hk = repo_root / "scripts" / "hk"
    monkeypatch.syspath_prepend(str(scripts_hk))
    from hk_evidence_lib import infer_metadata, write_json

    cases = [
        ("00005", "HSBC HOLDINGS", "bank"),
        ("01299", "AIA", "insurance"),
        ("00700", "TENCENT", "internet_platform"),
        ("00981", "SMIC", "semiconductor"),
        ("00728", "CHINA TELECOM", "telecom"),
        ("00386", "SINOPEC CORP", "energy"),
        ("00175", "GEELY AUTO", "manufacturing"),
    ]
    for ticker, company_name, expected_profile in cases:
        pdf = tmp_path / f"{company_name}_HK_{ticker}_2025-12-31_annual.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        metadata = pdf.with_suffix(pdf.suffix + ".metadata.json")
        write_json(
            metadata,
            {
                "candidate": {
                    "source_id": "hkex",
                    "ticker": ticker,
                    "company_id": ticker,
                    "company_name": company_name,
                    "report_type": "annual",
                    "form": "annual",
                    "accession_number": f"acc-{ticker}",
                    "report_end": "2025-12-31",
                    "published_at": "2026-04-01",
                    "document_url": "https://www1.hkexnews.hk/test.pdf",
                }
            },
        )

        assert infer_metadata(pdf, metadata)["industry_profile"] == expected_profile



def test_hk_metadata_uses_package_style_sibling_metadata_by_default(tmp_path, monkeypatch):
    repo_root = Path(__file__).resolve().parents[3]
    scripts_hk = repo_root / "scripts" / "hk"
    monkeypatch.syspath_prepend(str(scripts_hk))
    from hk_evidence_lib import infer_metadata, write_json

    pdf = tmp_path / "raw" / "report.pdf"
    pdf.parent.mkdir()
    pdf.write_bytes(b"%PDF-1.4")
    write_json(
        pdf.parent / "report.metadata.json",
        {
            "candidate": {
                "source_id": "hkex",
                "ticker": "00728",
                "company_id": "00728",
                "company_name": "CHINA TELECOM",
                "report_type": "annual",
                "form": "annual",
                "accession_number": "12125044",
                "report_end": "2025-12-31",
                "published_at": "2026-04-23",
                "document_url": "https://www1.hkexnews.hk/test.pdf",
            }
        },
    )

    metadata = infer_metadata(pdf)

    assert metadata["ticker"] == "00728"
    assert metadata["company_name"] == "CHINA TELECOM"
    assert metadata["industry_profile"] == "telecom"


def test_build_hk_evidence_package_accepts_list_table_index_fallback(tmp_path, monkeypatch):
    repo_root = Path(__file__).resolve().parents[3]
    scripts_hk = repo_root / "scripts" / "hk"
    monkeypatch.syspath_prepend(str(scripts_hk))
    from hk_evidence_lib import write_hk_evidence_package, write_json

    pdf = tmp_path / "SMIC_HK_00981_2025-12-31_annual.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    metadata = pdf.with_suffix(pdf.suffix + ".metadata.json")
    write_json(metadata, {"candidate": {"source_id": "hkex", "ticker": "00981", "company_id": "00981", "company_name": "SMIC", "report_type": "annual", "form": "annual", "accession_number": "121", "report_end": "2025-12-31", "published_at": "2026-04-01", "document_url": "https://www1.hkexnews.hk/test.pdf"}})
    parser_dir = tmp_path / "parser"
    parser_dir.mkdir()
    write_json(parser_dir / "document_full.json", {"task": {"filename": pdf.name}, "markdown": {"content": "# SMIC\n"}, "content_list": []})
    write_json(parser_dir / "financial_data.json", {"statements": [], "key_metrics": [], "operating_metrics": []})
    write_json(parser_dir / "financial_checks.json", {"overall_status": "skipped", "checks": [], "warnings": [], "summary": {}})
    write_json(parser_dir / "table_index.json", [{"table_id": "hk_table_0001", "table_index": 1, "page_number": 3, "row_count": 2, "column_count": 2, "raw": {"preview": "Revenue 100"}}])

    package_dir = write_hk_evidence_package(pdf, parser_dir, tmp_path / "wiki", metadata, force=True)

    table_index = json.loads((package_dir / "tables" / "table_index.json").read_text(encoding="utf-8"))
    assert len(table_index["tables"]) == 1
    assert (package_dir / "tables" / "table_0001.json").is_file()
