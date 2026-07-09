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
    company_dir = tmp_path / "companies" / "AAPL-Apple-Inc"
    assert read_json(company_dir / "company.json")["ticker"] == "AAPL"
    assert (company_dir / "company.md").is_file()
    assert read_json(company_dir / "filings.json")["items"][0]["filing_id"].startswith("US:")
    assert (company_dir / "metrics" / "latest" / "financial_data.json").is_file()
    assert (company_dir / "metrics" / "reports" / "US_0000320193_0000320193-25-000079" / "normalized_metrics.json").is_file()
    assert read_json(tmp_path / "_meta" / "package_index.json")["count"] == 1
    assert read_json(tmp_path / "_meta" / "company_catalog.json")["companies"][0]["company_wiki_id"] == "AAPL-Apple-Inc"
    assert read_json(tmp_path / "_meta" / "quality_summary.json")["quality_counts"]["pass"] == 1
    assert read_json(tmp_path / "_meta" / "case_set_50_us_10k.json")["items"][0]["ticker"] == "AAPL"


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
    monkeypatch.setattr(batch.sec_evidence_lib, "write_evidence_package", lambda *a, **k: calls.append("package") or (tmp_path / "wiki" / "companies" / "AAPL-Apple-Inc" / "reports" / "2025-10-K-x"))
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
            <table id="assets-table"><tr><th>Total assets</th><td><ix:nonFraction id="f1" name="us-gaap:Assets" contextRef="c1" unitRef="usd">1000</ix:nonFraction></td></tr></table>
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
    monkeypatch.setattr(sec_evidence_lib, "DEFAULT_PARSER_RESULTS_ROOT", tmp_path / "parser-results")

    package = sec_evidence_lib.write_evidence_package(source, tmp_path / "wiki", metadata, force=True)
    manifest = read_json(package / "manifest.json")

    assert manifest["schema_version"] == "market_evidence_package_v1"
    assert manifest["country"] == "US"
    assert manifest["source_tier"] == "official"
    assert manifest["document_format"] == "ixbrl_html"
    assert manifest["report_id"].startswith("2025-10-K-")
    assert manifest["company_wiki_id"] == "DEMO-Demo-Corp"
    assert "/companies/DEMO-Demo-Corp/reports/" in manifest["wiki_report_path"]
    assert manifest["parse_run_id"]
    assert manifest["artifact_hashes"]
    assert manifest["artifacts"]["normalized_metrics"] == "metrics/normalized_metrics.json"
    assert manifest["artifacts"]["report_complete"] == "parser/report_complete.md"
    assert manifest["artifacts"]["wiki_report_complete"] == "sections/report_complete.md"
    assert manifest["artifacts"]["document_full"] == "parser/document_full.json"
    assert manifest["artifacts"]["content_list_enhanced"] == "parser/content_list_enhanced.json"
    assert manifest["artifacts"]["table_relations"] == "parser/table_relations.json"
    assert manifest["artifacts"]["wiki_ingestion_plan"] == "qa/wiki_ingestion_plan.json"
    assert manifest["parser_result_dir"]
    for rel in (
        "parser/report_complete.md",
        "sections/report_complete.md",
        "parser/document_full.json",
        "parser/content_list_enhanced.json",
        "parser/table_relations.json",
        "qa/wiki_ingestion_plan.json",
    ):
        assert (package / rel).is_file()
    parser_result_dir = Path(manifest["parser_result_dir"])
    if not parser_result_dir.is_absolute():
        parser_result_dir = sec_evidence_lib.REPO_ROOT / parser_result_dir
    for rel in (
        "raw/filing.htm",
        "document_full.json",
        "report_complete.md",
        "content_list_enhanced.json",
        "table_relations.json",
        "quality_report.json",
        "manifest.json",
    ):
        assert (parser_result_dir / rel).is_file()
    document_full = read_json(package / "parser" / "document_full.json")
    assert document_full["schema_version"] == "sec_html_document_full_v1"
    assert document_full["markdown"]["path"] == "report_complete.md"
    assert document_full["markdown"]["wiki_path"] == "sections/report_complete.md"
    assert document_full["dom_nodes"]
    assert document_full["blocks"]
    assert document_full["tables"]
    assert document_full["facts"]
    assert document_full["relations"]
    report_complete = (package / "parser" / "report_complete.md").read_text(encoding="utf-8")
    assert "<!-- siq:block_id=" in report_complete
    assert "SIQ Enhanced Relation Summary" in report_complete
    assert "business text" in report_complete
    assert "Total assets" in report_complete
    assert (package / "sections" / "report_complete.md").read_text(encoding="utf-8") == report_complete
    source_map = read_json(package / "qa" / "source_map.json")
    assert any(entry.get("source_type") == "sec_html_block" for entry in source_map["entries"])
    assert any(entry.get("local_path") == "parser/report_complete.md" for entry in source_map["entries"])
    quality = read_json(package / "qa" / "quality_report.json")
    assert quality["full_document_status"] == "ready"
    ingestion_plan = read_json(package / "qa" / "wiki_ingestion_plan.json")
    assert ingestion_plan["schema_version"] == "sec_wiki_ingestion_plan_v1"
    assert ingestion_plan["rules"]["source_of_truth"] == "canonical_parser_result"
    assert ingestion_plan["status"] == "ready"
    assert ingestion_plan["raw_html_check"]["status"] == "ok"
    assert all(item["status"] == "ok" for item in ingestion_plan["mirror_checks"])


def test_backfill_sec_full_document_updates_legacy_package(tmp_path, monkeypatch):
    backfill = importlib.import_module("backfill_sec_full_document")
    package = make_package(tmp_path / "companies" / "AAPL-Apple-Inc" / "reports" / "2025-10-K-x")
    package.joinpath("raw", "filing.htm").write_text(_ixbrl_fixture(tmp_path).read_text(encoding="utf-8"), encoding="utf-8")
    write_json(package / "sections.json", {"schema_version": "sec_sections_v1", "sections": [{"section_id": "item_1", "char_start": 0, "char_end": 5000}]})
    write_json(package / "tables" / "table_index.json", {"schema_version": "sec_table_index_v1", "tables": [{"table_id": "t1", "table_index": 1, "section_id": "item_8"}]})
    write_json(package / "xbrl" / "facts_raw.json", {"schema_version": "sec_xbrl_facts_raw_v1", "facts": [{"fact_id": "fact1", "concept": "us-gaap:Assets", "value_text": "1000", "context_ref": "c1", "unit_ref": "usd", "html_anchor": "f1"}]})
    write_json(package / "xbrl" / "contexts.json", {"contexts": {"c1": {"period_end": "2025-12-31"}}})
    write_json(package / "xbrl" / "units.json", {"units": {"usd": {"unit": "USD"}}})
    before_manifest = read_json(package / "manifest.json")

    parser_results_root = tmp_path / "parser-results"
    dry_run = backfill.backfill_full_documents(tmp_path, dry_run=True, no_index=True, parser_results_root=parser_results_root)

    assert dry_run["status_counts"]["would_update"] == 1
    assert not (package / "parser" / "document_full.json").exists()

    monkeypatch.setattr(backfill.build_sec_wiki_index, "build_wiki_index", lambda *a, **k: {"package_count": 1})
    report = backfill.backfill_full_documents(tmp_path, no_index=False, parser_results_root=parser_results_root)

    assert report["status_counts"]["updated"] == 1
    assert report["index"]["package_count"] == 1
    assert (package / "parser" / "document_full.json").is_file()
    assert (package / "parser" / "report_complete.md").is_file()
    assert (package / "sections" / "report_complete.md").is_file()
    assert (package / "parser" / "content_list_enhanced.json").is_file()
    assert (package / "parser" / "table_relations.json").is_file()
    assert (package / "qa" / "wiki_ingestion_plan.json").is_file()
    source_map = read_json(package / "qa" / "source_map.json")
    assert any(entry.get("source_type") == "sec_html_block" for entry in source_map["entries"])
    quality = read_json(package / "qa" / "quality_report.json")
    assert quality["full_document"]["block_count"] > 0
    after_manifest = read_json(package / "manifest.json")
    assert after_manifest["parse_run_id"] != before_manifest.get("parse_run_id")
    assert after_manifest["artifacts"]["report_complete"] == "parser/report_complete.md"
    assert after_manifest["artifacts"]["wiki_report_complete"] == "sections/report_complete.md"
    assert after_manifest["artifacts"]["wiki_ingestion_plan"] == "qa/wiki_ingestion_plan.json"
    assert (parser_results_root / after_manifest["parser_result_task_id"] / "document_full.json").is_file()
    assert "parser/document_full.json" in after_manifest["artifact_hashes"]
    assert "parser/report_complete.md" in after_manifest["artifact_hashes"]


def test_sec_index_exposes_full_document_status(tmp_path):
    indexer = importlib.import_module("build_sec_wiki_index")
    package = make_package(tmp_path / "companies" / "AAPL-Apple-Inc" / "reports" / "2025-10-K-x")
    write_json(package / "qa" / "quality_report.json", {"overall_status": "pass", "full_document": {"block_count": 2, "markdown_chars": 100}})
    for rel in (
        "sections/report_complete.md",
        "parser/report_complete.md",
        "parser/document_full.json",
        "parser/content_list_enhanced.json",
        "parser/table_relations.json",
        "qa/wiki_ingestion_plan.json",
    ):
        path = package / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if rel == "qa/wiki_ingestion_plan.json":
            write_json(
                path,
                {
                    "schema_version": "sec_wiki_ingestion_plan_v1",
                    "status": "ready",
                    "ready": True,
                    "summary": {
                        "status": "ready",
                        "ready": True,
                        "missing_parser_artifact_count": 0,
                        "missing_wiki_artifact_count": 0,
                        "mirror_mismatch_count": 0,
                        "raw_html_status": "ok",
                    },
                },
            )
        else:
            path.write_text("{}" if rel.endswith(".json") else "# Report\n", encoding="utf-8")

    summary = indexer.build_wiki_index(tmp_path)

    assert summary["full_document_status_counts"]["ready"] == 1
    item = read_json(tmp_path / "_meta" / "package_index.json")["items"][0]
    assert item["full_document_status"] == "ready"
    assert item["wiki_ingestion_status"] == "ready"
    assert read_json(tmp_path / "_meta" / "quality_summary.json")["wiki_ingestion_status_counts"]["ready"] == 1
    company = read_json(tmp_path / "companies" / "AAPL-Apple-Inc" / "company.json")
    assert company["reports"][0]["full_document_ready"] is True
    assert company["reports"][0]["wiki_ingestion_ready"] is True


def test_us_financial_recognition_audit_finds_candidate_facts_and_tables(tmp_path):
    audit = importlib.import_module("audit_sec_financial_recognition")
    package = make_package(tmp_path / "companies" / "AAPL-Apple-Inc" / "reports" / "2025-10-K-x", quality="warning")
    core_metrics = [
        {"canonical_name": "operating_revenue", "value": "10", "period_key": "2025-09-27", "evidence_id": "e1"},
        {"canonical_name": "net_profit", "value": "2", "period_key": "2025-09-27", "evidence_id": "e1"},
        {"canonical_name": "total_assets", "value": "100", "period_key": "2025-09-27", "evidence_id": "e1"},
        {"canonical_name": "total_liabilities", "value": "70", "period_key": "2025-09-27", "evidence_id": "e1"},
        {"canonical_name": "total_equity", "value": "30", "period_key": "2025-09-27", "evidence_id": "e1"},
        {"canonical_name": "operating_cash_flow_net", "value": "3", "period_key": "2025-09-27", "evidence_id": "e1"},
    ]
    write_json(
        package / "metrics" / "financial_data.json",
        {
            "statements": [
                {
                    "statement_type": "balance_sheet",
                    "items": [
                        {"canonical_name": item["canonical_name"], "values": {"2025-09-27": item["value"]}, "sources": {"2025-09-27": {"evidence_id": item["evidence_id"]}}}
                        for item in core_metrics
                    ],
                }
            ],
            "key_metrics": [],
            "operating_metrics": [],
        },
    )
    write_json(package / "metrics" / "normalized_metrics.json", {"metrics": core_metrics})
    write_json(
        package / "metrics" / "financial_checks.json",
        {
            "overall_status": "warning",
            "warnings": ["Use standard three-statement bridge checks."],
            "checks": [
                {
                    "rule_id": "bs.assets_eq_liabilities_and_equity",
                    "rule_name": "Assets = liabilities and equity total",
                    "statement_type": "balance_sheet",
                    "period": "2025-09-27",
                    "status": "warning",
                    "reason": "missing_inputs",
                    "right": {"missing": ["total_liabilities_and_equity"]},
                }
            ],
        },
    )
    write_json(
        package / "xbrl" / "facts_raw.json",
        {
            "facts": [
                {
                    "fact_id": "fact-liab-equity",
                    "concept": "us-gaap:LiabilitiesAndStockholdersEquity",
                    "label": "Total liabilities and stockholders equity",
                    "value_text": "100",
                    "period_end": "2025-09-27",
                }
            ]
        },
    )
    write_json(
        package / "parser" / "document_full.json",
        {
            "tables": [
                {
                    "table_id": "table-1",
                    "table_index": 1,
                    "heading": "Consolidated Balance Sheets",
                    "rows": [{"cells": [{"text": "Total liabilities and stockholders equity"}, {"text": "100"}]}],
                }
            ]
        },
    )

    report = audit.audit_packages(tmp_path)

    assert report["package_count"] == 1
    assert report["status_counts"]["needs_review"] == 1
    assert report["concept_affected_package_counts"]["total_liabilities_and_equity"] == 1
    assert report["concept_candidate_counts"]["total_liabilities_and_equity"] == 1
    assert report["table_candidate_counts"]["total_liabilities_and_equity"] == 1
    assert report["optimization_queue"][0]["type"] == "concept_mapping_or_context_selection"


def test_us_financial_review_policy_skips_incomplete_historical_balance_sheet_period():
    sec_evidence_lib = importlib.import_module("sec_evidence_lib")

    financial_checks = {
        "overall_status": "warning",
        "summary": {"pass": 1, "fail": 0, "warning": 1, "skipped": 0},
        "checks": [
            {
                "rule_id": "bs.assets_eq_liabilities_and_equity",
                "rule_name": "Assets = liabilities and equity total",
                "statement_type": "balance_sheet",
                "period": "2023-09-30",
                "status": "warning",
                "reason": "missing_inputs",
                "right": {"missing": ["total_liabilities_and_equity", "total_assets"]},
                "raw": {},
            },
            {
                "rule_id": "bs.current_plus_non_current_assets",
                "rule_name": "Assets = current assets + non-current assets",
                "statement_type": "balance_sheet",
                "period": "2025-09-27",
                "status": "warning",
                "reason": "outside_tolerance",
                "right": {},
                "raw": {},
            },
        ],
    }
    total_assets_fact = types.SimpleNamespace(canonical_name="total_assets", period_key="2025-09-27")
    liabilities_equity_fact = types.SimpleNamespace(canonical_name="total_liabilities_and_equity", period_key="2025-09-27")
    statement = types.SimpleNamespace(statement_type=types.SimpleNamespace(value="balance_sheet"), items=[total_assets_fact, liabilities_equity_fact])
    extraction = types.SimpleNamespace(statements=[statement])

    updated = sec_evidence_lib._apply_us_financial_review_policy(financial_checks, extraction)

    skipped = updated["checks"][0]
    current_period_warning = updated["checks"][1]
    assert skipped["status"] == "skipped"
    assert skipped["reason"] == "incomplete_balance_sheet_period"
    assert skipped["raw"]["previous_status"] == "warning"
    assert current_period_warning["status"] == "warning"
    assert updated["overall_status"] == "warning"
    assert updated["review_policy"]["downgraded_check_count"] == 1
