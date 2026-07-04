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
                "footnotes": {},
                "toc": {},
                "financial_note_links": {},
                "quality_signals": {},
                "tables": [{"table_index": 1, "content_table_source_id": 1, "pdf_page_number": 88, "relations": []}],
                "pages": [],
            },
        },
    )

    package_dir = write_hk_evidence_package(pdf, parser_dir, tmp_path / "wiki", metadata, force=True)
    package_pdf = package_dir / "raw" / "report.pdf"
    package_metadata = package_dir / "raw" / "report.metadata.json"

    rebuilt_dir = write_hk_evidence_package(package_pdf, parser_dir, tmp_path / "wiki", package_metadata, force=True)
    result = validate_evidence_package(rebuilt_dir)

    assert rebuilt_dir == package_dir
    assert package_pdf.is_file()
    assert package_metadata.is_file()
    assert result.ok, result.errors
