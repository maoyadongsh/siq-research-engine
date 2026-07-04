import importlib.util
import json
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[1] / "ingest_market_evidence_chunks.py"
    spec = importlib.util.spec_from_file_location("ingest_market_evidence_chunks", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_market_chunk_metadata_preserves_company_wiki_paths(tmp_path):
    module = _load_module()
    package = tmp_path / "data" / "wiki" / "jp" / "companies" / "7203-Toyota" / "reports" / "2025-annual-securities-report-S100TEST"
    (package / "sections").mkdir(parents=True)
    (package / "tables").mkdir()
    (package / "metrics").mkdir()
    (package / "qa").mkdir()
    (package / "manifest.json").write_text(
        json.dumps(
            {
                "market": "JP",
                "ticker": "7203",
                "company_id": "JP:E02144",
                "company_name": "Toyota Motor Corporation",
                "filing_id": "JP:S100TEST",
                "parse_run_id": "run-1",
                "company_wiki_path": "data/wiki/jp/companies/7203-Toyota-Motor-Corporation",
                "wiki_report_path": "data/wiki/jp/companies/7203-Toyota-Motor-Corporation/reports/2025-annual-securities-report-S100TEST",
                "report_id": "2025-annual-securities-report-S100TEST",
            }
        ),
        encoding="utf-8",
    )
    (package / "sections" / "report.md").write_text("# Report\n\nRevenue and operating profit.", encoding="utf-8")

    chunks = module.iter_chunks(package, include_tables=False, include_metrics=False, include_qa=False)

    assert chunks
    metadata = chunks[0]["metadata"]
    assert metadata["company_wiki_path"] == "data/wiki/jp/companies/7203-Toyota-Motor-Corporation"
    assert metadata["wiki_report_path"] == "data/wiki/jp/companies/7203-Toyota-Motor-Corporation/reports/2025-annual-securities-report-S100TEST"
    assert metadata["report_id"] == "2025-annual-securities-report-S100TEST"
