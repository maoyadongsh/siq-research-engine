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
    write_json(
        parser_dir / "document_full.json",
        {
            "task": {"filename": pdf.name},
            "markdown": {"content": "# TENCENT\n"},
            "content_list": [{"type": "table", "table_body": table, "table_caption": ["Consolidated Statement of Financial Position"], "page_idx": 87}],
            "content_list_enhanced": {"tables": [{"table_index": 1, "content_table_source_id": 1, "pdf_page_number": 88}]},
        },
    )

    package_dir = write_hk_evidence_package(pdf, parser_dir, tmp_path / "wiki", metadata, force=True)
    result = validate_evidence_package(package_dir)

    assert result.ok, result.errors
    assert (package_dir / "metrics" / "load_plan.json").is_file()
    assert (package_dir / "qa" / "source_map.json").is_file()
