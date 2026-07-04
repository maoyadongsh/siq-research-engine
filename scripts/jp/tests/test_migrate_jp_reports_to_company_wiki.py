import importlib.util
import json
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[1] / "migrate_jp_reports_to_company_wiki.py"
    spec = importlib.util.spec_from_file_location("migrate_jp_reports_to_company_wiki", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _legacy_package(root: Path) -> Path:
    package = root / "jp_reports" / "7203" / "2025" / "annual_securities_report_S100TEST"
    (package / "raw").mkdir(parents=True)
    (package / "sections").mkdir()
    (package / "metrics").mkdir()
    (package / "parser").mkdir()
    (package / "qa").mkdir()
    (package / "raw" / "toyota.xml").write_text("<xbrl />", encoding="utf-8")
    (package / "sections" / "report.md").write_text("# Toyota\n", encoding="utf-8")
    _write_json(package / "parser" / "document_full.json", {"markdown": {"content": "# Toyota"}})
    _write_json(package / "qa" / "quality_report.json", {"overall_status": "warning"})
    _write_json(
        package / "manifest.json",
        {
            "schema_version": "market_evidence_package_v1",
            "market": "JP",
            "filing_id": "JP:S100TEST",
            "company_id": "JP:E02144",
            "ticker": "7203",
            "security_code": "7203",
            "edinet_code": "E02144",
            "doc_id": "S100TEST",
            "company_name": "Toyota Motor Corporation",
            "company_name_ja": "トヨタ自動車株式会社",
            "form": "Annual Securities Report",
            "report_type": "annual_securities_report",
            "fiscal_year": 2025,
            "period_end": "2025-03-31",
            "published_at": "2025-06-24",
            "quality_status": "warning",
        },
    )
    return package


def test_migrate_package_moves_legacy_jp_report_under_company_wiki(tmp_path):
    module = _load_module()
    source_package = _legacy_package(tmp_path)
    output_root = tmp_path / "wiki"

    target = module.migrate_package(source_package, output_root, force=False)

    expected = output_root / "jp" / "companies" / "7203-Toyota-Motor-Corporation" / "reports" / "2025-annual-securities-report-S100TEST"
    assert target == expected
    assert (target / "sections" / "report.md").exists()
    assert (target / "parser" / "document_full.json").exists()

    manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["company_wiki_id"] == "7203-Toyota-Motor-Corporation"
    assert manifest["company_wiki_path"] == "data/wiki/jp/companies/7203-Toyota-Motor-Corporation"
    assert manifest["wiki_report_path"] == "data/wiki/jp/companies/7203-Toyota-Motor-Corporation/reports/2025-annual-securities-report-S100TEST"
    assert manifest["report_id"] == "2025-annual-securities-report-S100TEST"

    company = json.loads((output_root / "jp" / "companies" / "7203-Toyota-Motor-Corporation" / "company.json").read_text(encoding="utf-8"))
    assert company["schema_version"] == "jp_company_wiki_v1"
    assert company["company_wiki_path"] == "data/wiki/jp/companies/7203-Toyota-Motor-Corporation"
    assert company["reports"][0]["wiki_report_path"] == manifest["wiki_report_path"]


def test_migrate_packages_handles_empty_legacy_root(tmp_path):
    module = _load_module()

    summary = module.migrate_packages(tmp_path / "jp_reports", tmp_path / "wiki")

    assert summary["candidates"] == 0
    assert summary["migrated"] == 0
    assert summary["failed"] == 0
