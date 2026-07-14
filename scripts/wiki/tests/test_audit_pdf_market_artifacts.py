import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace


def _load_module():
    source = Path(__file__).resolve().parents[1] / "audit_pdf_market_artifacts.py"
    spec = importlib.util.spec_from_file_location("audit_pdf_market_artifacts_under_test", source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write(path: Path, content: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_json(path: Path, payload: dict) -> None:
    _write(path, json.dumps(payload, ensure_ascii=False))


def _base_package(tmp_path: Path, market: str = "JP") -> tuple[Path, Path]:
    company_dir = tmp_path / "data" / "wiki" / market.lower() / "companies" / "7203-Toyota"
    package_dir = company_dir / "reports" / "2025-annual"
    _write_json(
        package_dir / "manifest.json",
        {
            "schema_version": "market_evidence_package_v1",
            "market": market,
            "company_wiki_id": company_dir.name,
            "report_id": "2025-annual",
            "ticker": "7203",
            "company_name": "Toyota",
            "quality_status": "pass",
            "local_source_path": "raw/report.pdf",
        },
    )
    for rel in (
        "README.md",
        "report.md",
        "document_full.json",
        "artifact_manifest.json",
        "raw/report.pdf",
        "parser/result_complete.md",
        "parser/document_full.json",
        "parser/table_index.json",
        "parser/table_relations.json",
        "parser/financial_data.json",
        "parser/financial_checks.json",
        "parser/quality_report.json",
        "tables/table_index.json",
        "tables/table_relations.json",
        "metrics/financial_data.json",
        "metrics/financial_checks.json",
        "qa/quality_report.json",
        "qa/source_map.json",
    ):
        _write(package_dir / rel)
    for rel in (
        "company.json",
        "company.md",
        "_index.json",
        "metrics/latest/financial_data.json",
        "metrics/latest/financial_checks.json",
        "evidence/evidence_index.json",
        "semantic/retrieval_index.json",
        "graph/graph_index.json",
    ):
        _write(company_dir / rel)
    return company_dir, package_dir


def test_complete_pdf_archive_is_a_status(tmp_path: Path):
    module = _load_module()
    _, package_dir = _base_package(tmp_path)

    result = module.audit_package(package_dir, "JP")

    assert result["status"] == "A_complete_pdf_wiki_archive"
    assert result["capabilities"]["can_agent_deep_research"] is True
    assert result["capabilities"]["can_postgres_import"] is False
    assert result["package_contract"]["ok"] is False
    assert result["missing"]["parser_archive"] == []


def test_postgres_import_capability_requires_standard_package_contract(tmp_path: Path, monkeypatch):
    module = _load_module()
    _, package_dir = _base_package(tmp_path)
    monkeypatch.setattr(
        module,
        "validate_evidence_package",
        lambda _path: SimpleNamespace(ok=True, errors=[], warnings=[]),
    )

    result = module.audit_package(package_dir, "JP")

    assert result["capabilities"]["can_postgres_import"] is True
    assert result["package_contract"] == {"ok": True, "errors": [], "warnings": []}


def test_missing_root_compat_only_is_b_status(tmp_path: Path):
    module = _load_module()
    _, package_dir = _base_package(tmp_path)
    (package_dir / "report.md").unlink()
    (package_dir / "document_full.json").unlink()
    (package_dir / "artifact_manifest.json").unlink()

    result = module.audit_package(package_dir, "JP")

    assert result["status"] == "B_missing_root_compat_only"
    assert result["capabilities"]["can_basic_wiki"] is True
    assert result["capabilities"]["can_agent_deep_research"] is False


def test_missing_parser_archive_is_c_status(tmp_path: Path):
    module = _load_module()
    _, package_dir = _base_package(tmp_path)
    (package_dir / "parser" / "table_relations.json").unlink()

    result = module.audit_package(package_dir, "JP")

    assert result["status"] == "C_missing_parser_archive"
    assert "parser/table_relations.json" in result["missing"]["parser_archive"]
    assert result["capabilities"]["can_note_relation_extract"] is True


def test_missing_package_evidence_is_d_status(tmp_path: Path):
    module = _load_module()
    _, package_dir = _base_package(tmp_path)
    (package_dir / "qa" / "source_map.json").unlink()

    result = module.audit_package(package_dir, "JP")

    assert result["status"] == "D_missing_financial_evidence_or_table_layer"
    assert "qa/source_map.json" in result["missing"]["package_required"]
    assert result["capabilities"]["can_postgres_import"] is False


def test_audit_markets_summarizes_packages(tmp_path: Path):
    module = _load_module()
    _base_package(tmp_path, market="HK")

    result = module.audit_markets(tmp_path / "data" / "wiki", ["HK"])

    assert result["package_count"] == 1
    assert result["status_counts"] == {"A_complete_pdf_wiki_archive": 1}
    assert result["capability_counts"]["can_agent_deep_research"] == 1
