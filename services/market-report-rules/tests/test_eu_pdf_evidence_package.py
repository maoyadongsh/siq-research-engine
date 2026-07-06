from pathlib import Path

from market_report_rules_service.evidence_package import validate_evidence_package


def test_build_eu_pdf_evidence_package_from_parser_result(tmp_path, monkeypatch):
    repo_root = Path(__file__).resolve().parents[3]
    scripts_eu = repo_root / "scripts" / "eu"
    monkeypatch.syspath_prepend(str(scripts_eu))
    from eu_pdf_evidence_lib import write_eu_pdf_evidence_package, write_json

    pdf = tmp_path / "ASML-Holding-N.V_EU_ASML_2025-12-31_annual_2026-02-25_eu_direct_test.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    metadata = pdf.with_suffix(pdf.suffix + ".metadata.json")
    write_json(
        metadata,
        {
            "candidate": {
                "source_id": "eu_direct",
                "market": "EU",
                "company_id": "NL:ASML",
                "ticker": "ASML",
                "company_name": "ASML Holding N.V.",
                "report_type": "annual",
                "form": "annual",
                "accession_number": "manual",
                "report_end": "2025-12-31",
                "published_at": "2026-02-25",
                "document_url": "https://example.test/asml.pdf",
                "landing_url": "https://example.test/asml",
                "file_format": "pdf",
                    "metadata": {"country": "NL", "source_tier": "local_uploaded"},
            }
        },
    )
    parser_dir = tmp_path / "parser"
    parser_dir.mkdir()
    tables = [
        (
            "Consolidated Statement of Financial Position",
            183,
            "<table>"
            "<tr><td></td><td>2025</td><td>2024</td></tr>"
            "<tr><td>Total assets</td><td>1000</td><td>900</td></tr>"
            "<tr><td>Total liabilities</td><td>600</td><td>550</td></tr>"
            "<tr><td>Total equity</td><td>400</td><td>350</td></tr>"
            "</table>",
        ),
        (
            "Consolidated Statement of Profit or Loss",
            184,
            "<table>"
            "<tr><td></td><td>2025</td><td>2024</td></tr>"
            "<tr><td>Revenue</td><td>700</td><td>650</td></tr>"
            "<tr><td>Profit before tax</td><td>130</td><td>120</td></tr>"
            "<tr><td>Income tax expense</td><td>(30)</td><td>(25)</td></tr>"
            "<tr><td>Profit for the year</td><td>100</td><td>95</td></tr>"
            "</table>",
        ),
        (
            "Consolidated Statement of Cash Flows",
            185,
            "<table>"
            "<tr><td></td><td>2025</td><td>2024</td></tr>"
            "<tr><td>Net cash from operating activities</td><td>150</td><td>130</td></tr>"
            "<tr><td>Net cash used in investing activities</td><td>(20)</td><td>(10)</td></tr>"
            "<tr><td>Net cash used in financing activities</td><td>(10)</td><td>(8)</td></tr>"
            "</table>",
        ),
    ]
    write_json(
        parser_dir / "document_full.json",
        {
            "task": {"filename": pdf.name},
            "markdown": {"content": "# ASML\n"},
            "content_list": [
                {"type": "table", "table_body": table, "table_caption": [title], "page_idx": page_idx}
                for title, page_idx, table in tables
            ],
            "content_list_enhanced": {
                "tables": [
                    {"table_index": index, "content_table_source_id": index, "pdf_page_number": page_idx + 1}
                    for index, (_, page_idx, _) in enumerate(tables, start=1)
                ]
            },
        },
    )

    package_dir = write_eu_pdf_evidence_package(pdf, parser_dir, tmp_path / "wiki", metadata, force=True)
    result = validate_evidence_package(package_dir)

    assert result.ok, result.errors
    assert result.manifest["market"] == "EU"
    assert result.manifest["country"] == "NL"
    assert result.manifest["document_format"] == "pdf"
    assert (package_dir / "metrics" / "load_plan.json").is_file()
    assert (package_dir / "qa" / "source_map.json").is_file()
