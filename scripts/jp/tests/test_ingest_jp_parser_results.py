import importlib.util
import json
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[1] / "ingest_jp_parser_results.py"
    spec = importlib.util.spec_from_file_location("ingest_jp_parser_results", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _parser_result(tmp_path: Path) -> Path:
    pdf = tmp_path / "uploads" / "toyota.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF-1.4\n")
    parser_dir = tmp_path / "results" / "run-1"
    parser_dir.mkdir(parents=True)
    filename = "Toyota-Motor-Corporation_JP_7203_2025-03-31_年报_2026-04-03_issuer_annual_report_9685efd4.pdf"
    _write_json(
        parser_dir / "document_full.json",
        {
            "task": {
                "task_id": "run-1",
                "filename": filename,
                "status": "completed",
                "submit_config": {"market": "JP"},
            },
            "source_files": {"pdf": {"path": str(pdf), "exists": True, "kind": "pdf"}},
            "markdown": {"content": "# Toyota Motor Corporation\n\nRevenue."},
            "tables": [],
            "content_list": [],
        },
    )
    _write_json(
        parser_dir / "quality_report.json",
        {
            "market": "JP",
            "filename": filename,
            "report_kind": "jp_integrated_report",
            "table_count": 1,
            "overall_status": "warning",
        },
    )
    _write_json(parser_dir / "financial_data.json", {"market": "JP", "report_kind": "jp_integrated_report"})
    _write_json(parser_dir / "financial_checks.json", {"market": "JP", "overall_status": "skipped"})
    return parser_dir


def test_discover_jp_parser_results_infers_metadata_from_document_full(tmp_path):
    module = _load_module()
    parser_dir = _parser_result(tmp_path)

    rows = module.discover_jp_parser_results(tmp_path / "results")

    assert [row.parser_dir for row in rows] == [parser_dir]
    assert rows[0].metadata["ticker"] == "7203"
    assert rows[0].metadata["company_name"] == "Toyota Motor Corporation"
    assert rows[0].metadata["report_type"] == "integrated_report"
    assert rows[0].metadata["fiscal_year"] == 2025
    assert rows[0].metadata["doc_id"] == "run-1"


def test_ingest_parser_results_builds_company_wiki_package(tmp_path):
    module = _load_module()
    _parser_result(tmp_path)
    output_root = tmp_path / "wiki"

    report = module.ingest_parser_results(tmp_path / "results", output_root, force=True)

    assert report["summary"]["succeeded"] == 1
    assert report["summary"]["failed"] == 0
    assert report["summary"]["validation_failed"] == 0
    package = output_root / "jp" / "companies" / "7203-Toyota-Motor-Corporation" / "reports" / "2025-integrated-report-run-1"
    assert package.exists()
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["wiki_report_path"] == "data/wiki/jp/companies/7203-Toyota-Motor-Corporation/reports/2025-integrated-report-run-1"
    assert manifest["company_wiki_path"] == "data/wiki/jp/companies/7203-Toyota-Motor-Corporation"
    assert (package / "parser" / "document_full.json").exists()
    assert (output_root / "jp" / "companies" / "7203-Toyota-Motor-Corporation" / "company.json").exists()
