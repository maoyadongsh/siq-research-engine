import importlib
import json
import sys
import types
from pathlib import Path


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _metadata_payload(**overrides):
    candidate = {
        "source_id": "sec",
        "market": "US",
        "company_id": "123",
        "ticker": "ACME",
        "company_name": "ACME INC",
        "form": "10-K",
        "report_type": "10-K",
        "report_family": "annual",
        "report_end": "2025-12-31",
        "published_at": "2026-02-01",
        "accepted_at": "2026-02-01T01:02:03Z",
        "accession_number": "0000000123-26-000001",
        "primary_document": "acme-20251231.htm",
        "document_url": "https://www.sec.gov/Archives/edgar/data/123/000000012326000001/acme.htm",
        "landing_url": "https://www.sec.gov/Archives/edgar/data/123/000000012326000001/index.html",
        "file_format": "html",
        "inline_xbrl": True,
    }
    candidate.update(overrides)
    return {"candidate": candidate, "downloaded_file": {"path": "local"}}


def test_scan_downloads_normalizes_sec_metadata(tmp_path):
    discovery = importlib.import_module("discover_sec_downloaded_cases")
    html = tmp_path / "ACME" / "2025" / "annual" / "ACME_US_ACME_2025-12-31_10-K_2026-02-01_sec_a.html"
    html.parent.mkdir(parents=True)
    html.write_text("<html>ACME filing</html>", encoding="utf-8")
    write_json(html.with_suffix(".html.metadata.json"), _metadata_payload())

    rows = discovery.scan_downloads(tmp_path, forms={"10-K"})

    assert len(rows) == 1
    row = rows[0]
    assert row["ticker"] == "ACME"
    assert row["company_id"] == "123"
    assert row["company_name"] == "ACME INC"
    assert row["form"] == "10-K"
    assert row["fiscal_year"] == 2025
    assert row["period_end"] == "2025-12-31"
    assert row["filing_date"] == "2026-02-01"
    assert row["accession_number"] == "0000000123-26-000001"
    assert row["source_path"].endswith("ACME_US_ACME_2025-12-31_10-K_2026-02-01_sec_a.html")
    assert row["metadata_path"].endswith(".html.metadata.json")
    assert len(row["source_sha256"]) == 64

    index_path = discovery.write_downloads_index(rows, tmp_path / "wiki")
    payload = read_json(index_path)
    assert payload["schema_version"] == "sec_downloads_index_v1"
    assert payload["count"] == 1
    assert payload["items"][0]["ticker"] == "ACME"


def test_scan_downloads_derives_manual_accession_from_sec_url(tmp_path):
    discovery = importlib.import_module("discover_sec_downloaded_cases")
    html = tmp_path / "Apple-Inc" / "2025" / "annual" / "Apple-Inc_US_AAPL_2025-09-27_10-K_2025-10-31_sec_a.htm"
    html.parent.mkdir(parents=True)
    html.write_text("<html>Apple filing</html>", encoding="utf-8")
    write_json(
        html.with_suffix(".htm.metadata.json"),
        _metadata_payload(
            ticker="AAPL",
            accession_number="manual",
            document_url="https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/aapl-20250927.htm",
        ),
    )

    assert discovery.scan_downloads(tmp_path)[0]["accession_number"] == "0000320193-25-000079"

def make_package(package_dir: Path, *, ticker: str = "AAPL", fiscal_year: int = 2025, quality: str = "pass") -> Path:
    for dirname in ("raw", "sections", "tables", "xbrl", "metrics", "qa"):
        (package_dir / dirname).mkdir(parents=True, exist_ok=True)
    filing_id = f"US:0000320193:0000320193-25-000079"
    manifest = {
        "schema_version": "market_evidence_package_v1",
        "market": "US",
        "country": "US",
        "filing_id": filing_id,
        "company_id": "US:0000320193",
        "ticker": ticker,
        "cik": "0000320193",
        "company_name": "Apple Inc.",
        "source_id": "sec",
        "source_tier": "official",
        "form": "10-K",
        "report_type": "annual",
        "accession_number": "0000320193-25-000079",
        "fiscal_year": fiscal_year,
        "fiscal_period": "FY",
        "period_end": f"{fiscal_year}-09-27",
        "filing_date": f"{fiscal_year}-10-31",
        "published_at": f"{fiscal_year}-10-31",
        "source_url": "https://www.sec.gov/Archives/example.htm",
        "local_source_path": "raw/filing.htm",
        "accounting_standard": "US_GAAP",
        "parser_version": "test_parser_v1",
        "rules_version": "test_rules_v1",
        "quality_status": quality,
        "artifact_hashes": {"raw/filing.htm": "abc"},
    }
    write_json(package_dir / "manifest.json", manifest)
    (package_dir / "README.md").write_text("# AAPL\n", encoding="utf-8")
    (package_dir / "raw" / "filing.htm").write_text("<html></html>", encoding="utf-8")
    write_json(package_dir / "metrics" / "financial_data.json", {"statements": [{"statement_type": "balance_sheet", "items": [{"canonical_name": "total_assets", "values": {"2025-09-27": "100"}, "sources": {"2025-09-27": {"evidence_id": "e1"}}}]}], "key_metrics": [], "operating_metrics": []})
    write_json(package_dir / "metrics" / "financial_checks.json", {"overall_status": quality, "warnings": []})
    write_json(package_dir / "metrics" / "normalized_metrics.json", {"metrics": [{"canonical_name": "total_assets", "value": "100", "period_key": "2025-09-27", "evidence_id": "e1"}]})
    write_json(package_dir / "qa" / "quality_report.json", {"overall_status": quality, "section_count": 2, "table_count": 1, "raw_fact_count": 3, "normalized_metric_count": 1, "evidence_coverage_ratio": 1})
    write_json(package_dir / "qa" / "source_map.json", {"entries": [{"evidence_id": "e1"}]})
    return package_dir


def test_build_wiki_index_writes_company_and_root_indexes(tmp_path):
    indexer = importlib.import_module("build_sec_wiki_index")
    make_package(tmp_path / "AAPL" / "2025" / "10-K_0000320193-25-000079")

    summary = indexer.build_wiki_index(tmp_path, forms={"10-K"})

    assert summary["package_count"] == 1
    assert summary["company_count"] == 1
    assert read_json(tmp_path / "AAPL" / "company.json")["ticker"] == "AAPL"
    assert (tmp_path / "AAPL" / "company.md").is_file()
    assert read_json(tmp_path / "AAPL" / "filings.json")["items"][0]["filing_id"].startswith("US:")
    assert (tmp_path / "AAPL" / "metrics" / "latest" / "financial_data.json").is_file()
    assert (tmp_path / "AAPL" / "metrics" / "reports" / "US_0000320193_0000320193-25-000079" / "normalized_metrics.json").is_file()
    assert read_json(tmp_path / "_meta" / "package_index.json")["count"] == 1
    assert read_json(tmp_path / "_meta" / "quality_summary.json")["quality_counts"]["pass"] == 1
    assert read_json(tmp_path / "case_set_50_us_10k.json")["items"][0]["ticker"] == "AAPL"


def test_ingest_case_set_imports_without_milvus_dependency(monkeypatch):
    psycopg = types.ModuleType("psycopg")
    psycopg.connect = lambda *args, **kwargs: None
    psycopg_types = types.ModuleType("psycopg.types")
    psycopg_json = types.ModuleType("psycopg.types.json")
    psycopg_json.Jsonb = lambda value: value
    monkeypatch.setitem(sys.modules, "psycopg", psycopg)
    monkeypatch.setitem(sys.modules, "psycopg.types", psycopg_types)
    monkeypatch.setitem(sys.modules, "psycopg.types.json", psycopg_json)
    sys.modules.pop("ingest_sec_case_set", None)
    sys.modules.pop("siq_sec_pg_import", None)

    module = importlib.import_module("ingest_sec_case_set")

    assert module.DEFAULT_COLLECTION == "siq_us_sec_filings"
    assert module.DEFAULT_VECTOR_DIM == 1024



def test_ingest_package_counts_ignores_table_index(monkeypatch, tmp_path):
    psycopg = types.ModuleType("psycopg")
    psycopg.connect = lambda *args, **kwargs: None
    psycopg_types = types.ModuleType("psycopg.types")
    psycopg_json = types.ModuleType("psycopg.types.json")
    psycopg_json.Jsonb = lambda value: value
    monkeypatch.setitem(sys.modules, "psycopg", psycopg)
    monkeypatch.setitem(sys.modules, "psycopg.types", psycopg_types)
    monkeypatch.setitem(sys.modules, "psycopg.types.json", psycopg_json)
    sys.modules.pop("ingest_sec_case_set", None)
    sys.modules.pop("siq_sec_pg_import", None)
    module = importlib.import_module("ingest_sec_case_set")

    package_dir = tmp_path / "AAPL" / "2025" / "10-K_x"
    write_json(package_dir / "xbrl" / "facts_raw.json", {"facts": [{"fact_id": "f1"}]})
    write_json(package_dir / "metrics" / "normalized_metrics.json", {"metrics": [{"metric_id": "m1"}]})
    write_json(package_dir / "sections.json", {"sections": [{"section_id": "item_1"}]})
    write_json(package_dir / "qa" / "source_map.json", {"entries": [{"evidence_id": "e1"}]})
    write_json(package_dir / "tables" / "table_index.json", {"tables": [{"table_id": "t1"}]})
    write_json(package_dir / "tables" / "table_0001.json", {"table_id": "t1"})

    assert module.package_counts(package_dir)["tables"] == 1

def test_batch_build_invokes_discovery_package_and_index(monkeypatch, tmp_path):
    batch = importlib.import_module("build_sec_wiki")
    source = tmp_path / "a.html"
    source.write_text("<html></html>", encoding="utf-8")
    calls = []
    monkeypatch.setattr(batch.discovery, "scan_downloads", lambda *a, **k: [{"source_path": str(source), "metadata_path": None, "ticker": "AAPL", "form": "10-K"}])
    monkeypatch.setattr(batch.discovery, "write_downloads_index", lambda rows, output_root: output_root / "_meta" / "downloads_index.json")
    monkeypatch.setattr(batch.sec_evidence_lib, "write_evidence_package", lambda *a, **k: calls.append("package") or (tmp_path / "wiki" / "AAPL" / "2025" / "10-K_x"))
    monkeypatch.setattr(batch.indexer, "build_wiki_index", lambda *a, **k: {"package_count": 1})

    report = batch.build_sec_wiki(tmp_path, tmp_path / "wiki", forms={"10-K"}, force=False)

    assert calls == ["package"]
    assert report["discovered_count"] == 1
    assert report["built_count"] == 1
    assert report["index"]["package_count"] == 1


def _ixbrl_fixture(tmp_path: Path) -> Path:
    html = tmp_path / "demo.htm"
    html.write_text(
        """
        <html xmlns:ix="http://www.xbrl.org/2013/inlineXBRL" xmlns:xbrli="http://www.xbrl.org/2003/instance">
          <body>
            <xbrli:context id="c1"><xbrli:entity><xbrli:identifier>0000000001</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:instant>2025-12-31</xbrli:instant></xbrli:period></xbrli:context>
            <xbrli:context id="d1"><xbrli:entity><xbrli:identifier>0000000001</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:startDate>2025-01-01</xbrli:startDate><xbrli:endDate>2025-12-31</xbrli:endDate></xbrli:period></xbrli:context>
            <xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>
            Item 1. Business """ + ("business text " * 80) + """
            Item 7. Management's Discussion and Analysis """ + ("mda text " * 80) + """
            Item 8. Financial Statements """ + ("financial text " * 80) + """
            <ix:nonFraction id="f1" name="us-gaap:Assets" contextRef="c1" unitRef="usd">1000</ix:nonFraction>
          </body>
        </html>
        """,
        encoding="utf-8",
    )
    return html


def _fake_metrics(manifest, facts_raw, tables):
    return {
        "normalized_metrics": [
            {"metric_id": "m1", "filing_id": manifest["filing_id"], "ticker": manifest["ticker"], "canonical_name": "total_assets", "concept": "us-gaap:Assets", "value": "1000", "unit": "USD", "period_key": "2025-12-31", "evidence_id": "e1", "raw_fact_id": facts_raw[0]["fact_id"] if facts_raw else None, "dimensions": {}}
        ],
        "financial_data": {"statements": [{"statement_type": "balance_sheet", "items": [{"canonical_name": "total_assets", "values": {"2025-12-31": "1000"}, "sources": {"2025-12-31": {"evidence_id": "e1"}}}]}], "key_metrics": [], "operating_metrics": []},
        "financial_checks": {"overall_status": "pass", "warnings": []},
        "quality_status": "pass",
        "warnings": [],
    }


def test_sec_manifest_uses_market_evidence_contract(monkeypatch, tmp_path):
    sec_evidence_lib = importlib.import_module("sec_evidence_lib")
    source = _ixbrl_fixture(tmp_path)
    metadata = source.with_suffix(".htm.metadata.json")
    write_json(metadata, _metadata_payload(ticker="DEMO", company_name="Demo Corp", company_id="1"))
    monkeypatch.setattr(sec_evidence_lib, "normalize_metrics", _fake_metrics)

    package = sec_evidence_lib.write_evidence_package(source, tmp_path / "wiki", metadata, force=True)
    manifest = read_json(package / "manifest.json")

    assert manifest["schema_version"] == "market_evidence_package_v1"
    assert manifest["country"] == "US"
    assert manifest["source_tier"] == "official"
    assert manifest["document_format"] == "ixbrl_html"
    assert manifest["parse_run_id"]
    assert manifest["artifact_hashes"]
    assert manifest["artifacts"]["normalized_metrics"] == "metrics/normalized_metrics.json"
