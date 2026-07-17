from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
US_SEC_DIR = REPO_ROOT / "scripts" / "us-sec"
MILVUS_SCRIPT = REPO_ROOT / "scripts" / "vector-index" / "milvus-ingestion" / "ingest_sec_wiki_chunks.py"


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _write_fixture(tmp_path: Path) -> tuple[Path, Path]:
    source_path = tmp_path / "demo-20251231.htm"
    source_path.write_text(
        """
        <html xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
              xmlns:xbrli="http://www.xbrl.org/2003/instance">
          <body>
            <xbrli:context id="c1">
              <xbrli:entity><xbrli:identifier>0000000001</xbrli:identifier></xbrli:entity>
              <xbrli:period><xbrli:instant>2025-12-31</xbrli:instant></xbrli:period>
            </xbrli:context>
            <xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>
            <h1 id="item_8">Item 8. Financial Statements</h1>
            <ix:nonFraction id="fact-assets" name="us-gaap:Assets" contextRef="c1"
                unitRef="usd" scale="6" decimals="-6">123</ix:nonFraction>
          </body>
        </html>
        """,
        encoding="utf-8",
    )
    metadata_path = tmp_path / "demo.metadata.json"
    metadata_path.write_text(
        json.dumps(
            {
                "candidate": {
                    "ticker": "DEMO",
                    "company_name": "Demo Corp",
                    "form": "10-K",
                    "report_family": "annual",
                    "report_end": "2025-12-31",
                    "published_at": "2026-02-20",
                    "accession_number": "0000000001-26-000001",
                    "document_url": "https://www.sec.gov/Archives/edgar/data/1/000000000126000001/demo-20251231.htm",
                }
            }
        ),
        encoding="utf-8",
    )
    return source_path, metadata_path


def test_us_sec_metric_keeps_openable_source_and_identity(tmp_path: Path) -> None:
    sys.path.insert(0, str(US_SEC_DIR))
    try:
        evidence_lib = _load_module(US_SEC_DIR / "sec_evidence_lib.py", "test_sec_evidence_lib")
        source_path, metadata_path = _write_fixture(tmp_path)
        package_dir = evidence_lib.write_evidence_package(
            source_path,
            tmp_path / "wiki",
            metadata_path,
        )
    finally:
        sys.path.remove(str(US_SEC_DIR))

    manifest = json.loads((package_dir / "manifest.json").read_text(encoding="utf-8"))
    metrics = json.loads((package_dir / "metrics" / "normalized_metrics.json").read_text(encoding="utf-8"))["metrics"]
    assets = next(metric for metric in metrics if metric["canonical_name"] == "total_assets")

    expected_url = "https://www.sec.gov/Archives/edgar/data/1/000000000126000001/demo-20251231.htm"
    assert manifest["report_id"] == "2025-10-K-0000000001-26-000001"
    assert assets["source_type"] == "sec_xbrl_fact"
    assert assets["source_url"] == expected_url
    assert assets["source_anchor"] == "fact-assets"
    assert assets["source_target"] == f"{expected_url}#fact-assets"
    assert assets["xbrl_tag"] == "us-gaap:Assets"
    assert assets["citation_mode"] == "sec_html_ixbrl"
    assert assets["research_identity"] == {
        "market": "US",
        "company_id": "US:0000000001",
        "filing_id": "US:0000000001:0000000001-26-000001",
        "report_id": "2025-10-K-0000000001-26-000001",
        "parse_run_id": assets["parse_run_id"],
    }
    assert "task_id" not in assets
    assert "pdf_page" not in assets


def test_us_sec_metric_chunk_preserves_sec_anchor_without_pdf_fields(tmp_path: Path) -> None:
    sys.path.insert(0, str(US_SEC_DIR))
    try:
        evidence_lib = _load_module(US_SEC_DIR / "sec_evidence_lib.py", "test_sec_evidence_lib_chunks")
        source_path, metadata_path = _write_fixture(tmp_path)
        package_dir = evidence_lib.write_evidence_package(
            source_path,
            tmp_path / "wiki",
            metadata_path,
        )
    finally:
        sys.path.remove(str(US_SEC_DIR))

    ingestion = _load_module(MILVUS_SCRIPT, "test_ingest_sec_wiki_chunks")
    chunks = ingestion.iter_chunks(package_dir, include_sections=False, include_metrics=True)
    assets = next(chunk["metadata"] for chunk in chunks if chunk["metadata"]["canonical_name"] == "total_assets")

    expected_target = "https://www.sec.gov/Archives/edgar/data/1/000000000126000001/demo-20251231.htm#fact-assets"
    assert assets["source_type"] == "sec_xbrl_fact"
    assert assets["source_anchor"] == "fact-assets"
    assert assets["html_anchor"] == "fact-assets"
    assert assets["source_target"] == expected_target
    assert assets["target"] == expected_target
    assert assets["citation_url"] == expected_target
    assert assets["xbrl_tag"] == "us-gaap:Assets"
    assert assets["citation_mode"] == "sec_html_ixbrl"
    assert assets["research_identity"]["filing_id"] == assets["filing_id"]
    assert assets["research_identity"]["report_id"] == assets["report_id"]
    assert "task_id" not in assets
    assert "pdf_page" not in assets
    assert "open_pdf_page_url" not in assets


def test_sec_source_target_replaces_existing_fragment() -> None:
    sys.path.insert(0, str(US_SEC_DIR))
    try:
        evidence_lib = _load_module(US_SEC_DIR / "sec_evidence_lib.py", "test_sec_evidence_lib_target")
    finally:
        sys.path.remove(str(US_SEC_DIR))

    assert evidence_lib.sec_source_target("https://www.sec.gov/example.htm#old", "fact-7") == (
        "https://www.sec.gov/example.htm#fact-7"
    )


def test_us_sec_chunk_derives_report_id_for_legacy_package() -> None:
    ingestion = _load_module(MILVUS_SCRIPT, "test_ingest_sec_wiki_chunks_legacy")
    manifest = {
        "fiscal_year": 2025,
        "form": "10-K",
        "accession_number": "0000000001-26-000001",
    }

    assert ingestion._report_id(manifest) == "2025-10-K-0000000001-26-000001"


def test_us_sec_section_chunk_links_to_sec_section_without_pdf_fields(tmp_path: Path) -> None:
    package_dir = tmp_path / "wiki" / "DEMO" / "2025" / "10-K_0000000001-26-000001"
    (package_dir / "sections").mkdir(parents=True)
    (package_dir / "tables").mkdir()
    (package_dir / "raw").mkdir()
    (package_dir / "raw" / "filing.htm").write_text(
        '<html><body><h1 id="item_8">Item 8. Financial Statements</h1></body></html>',
        encoding="utf-8",
    )
    (package_dir / "sections" / "financial_statements.md").write_text(
        "Item 8. Financial Statements\n\nThe registrant presents consolidated financial statements.",
        encoding="utf-8",
    )
    (package_dir / "manifest.json").write_text(
        json.dumps(
            {
                "market": "US",
                "ticker": "DEMO",
                "company_id": "US:0000000001",
                "company_name": "Demo Corp",
                "form": "10-K",
                "accession_number": "0000000001-26-000001",
                "filing_id": "US:0000000001:0000000001-26-000001",
                "fiscal_year": 2025,
                "parse_run_id": "parse-us-demo",
                "source_url": "https://www.sec.gov/Archives/edgar/data/1/000000000126000001/demo-20251231.htm",
            }
        ),
        encoding="utf-8",
    )
    (package_dir / "sections.json").write_text(
        json.dumps(
            {
                "sections": [
                    {
                        "section_id": "item_8",
                        "section_title": "Item 8. Financial Statements",
                        "section_order": 8,
                        "file": "financial_statements.md",
                        "html_anchor": "item_8",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (package_dir / "tables" / "table_index.json").write_text('{"tables": []}', encoding="utf-8")

    ingestion = _load_module(MILVUS_SCRIPT, "test_ingest_sec_wiki_chunks_sections")
    chunks = ingestion.iter_chunks(package_dir, include_sections=True, include_metrics=False)
    item_8 = next(chunk["metadata"] for chunk in chunks if chunk["metadata"]["section_id"] == "item_8")

    expected_target = "https://www.sec.gov/Archives/edgar/data/1/000000000126000001/demo-20251231.htm#item_8"
    assert item_8["source_type"] == "sec_html_section"
    assert item_8["citation_mode"] == "sec_html_section"
    assert item_8["source_target"] == expected_target
    assert item_8["citation_url"] == expected_target
    assert item_8["research_identity"]["filing_id"] == item_8["filing_id"]
    assert "task_id" not in item_8
    assert "pdf_page" not in item_8


def test_us_sec_section_chunk_drops_synthetic_anchor(tmp_path: Path) -> None:
    package_dir = tmp_path / "legacy"
    (package_dir / "sections").mkdir(parents=True)
    (package_dir / "tables").mkdir()
    (package_dir / "raw").mkdir()
    (package_dir / "sections" / "financial_statements.md").write_text(
        "Item 8. Financial Statements\n\nLegacy package with a synthetic section id.",
        encoding="utf-8",
    )
    (package_dir / "raw" / "filing.htm").write_text(
        "<html><body><h1>Item 8. Financial Statements</h1></body></html>",
        encoding="utf-8",
    )
    (package_dir / "manifest.json").write_text(
        json.dumps(
            {
                "market": "US",
                "ticker": "DEMO",
                "company_id": "US:0000000001",
                "company_name": "Demo Corp",
                "form": "10-K",
                "accession_number": "0000000001-26-000001",
                "filing_id": "US:0000000001:0000000001-26-000001",
                "fiscal_year": 2025,
                "source_url": "https://www.sec.gov/Archives/edgar/data/1/demo.htm",
            }
        ),
        encoding="utf-8",
    )
    (package_dir / "sections.json").write_text(
        json.dumps(
            {
                "sections": [
                    {
                        "section_id": "item_8",
                        "section_title": "Item 8. Financial Statements",
                        "file": "financial_statements.md",
                        "html_anchor": "item_8",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (package_dir / "tables" / "table_index.json").write_text('{"tables": []}', encoding="utf-8")

    ingestion = _load_module(MILVUS_SCRIPT, "test_ingest_sec_wiki_chunks_synthetic")
    chunk = ingestion.iter_chunks(package_dir, include_sections=True, include_metrics=False)[0]["metadata"]

    assert chunk["source_anchor"] is None
    assert chunk["source_target"] == "https://www.sec.gov/Archives/edgar/data/1/demo.htm"
