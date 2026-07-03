import json
import sys
from pathlib import Path

from fastapi import HTTPException

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from services import market_package_repository as repository


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_package(root: Path) -> Path:
    package_dir = root / "hk_reports" / "00700" / "2025" / "annual_abc123"
    _write_json(
        package_dir / "manifest.json",
        {
            "market": "HK",
            "filing_id": "HK:00700:abc123",
            "ticker": "00700",
            "company_name": "Tencent Holdings",
            "form": "annual",
            "report_type": "annual",
            "fiscal_year": 2025,
            "fiscal_period": "FY",
            "period_end": "2025-12-31",
            "published_at": "2026-04-09",
            "quality_status": "warning",
        },
    )
    _write_json(
        package_dir / "qa" / "quality_report.json",
        {
            "overall_status": "pass",
            "section_count": 2,
            "table_count": 3,
            "raw_fact_count": 0,
            "normalized_metric_count": 1,
        },
    )
    _write_json(
        package_dir / "qa" / "source_map.json",
        {"entries": [{"evidence_id": "hk:abc123:p88:t1:r2", "local_path": "sections/report.md"}]},
    )
    _write_json(package_dir / "metrics" / "normalized_metrics.json", {"metrics": [{"metric_id": "m1"}]})
    _write_json(package_dir / "metrics" / "financial_data.json", {"statements": []})
    _write_json(package_dir / "metrics" / "financial_checks.json", {"overall_status": "pass"})
    _write_json(package_dir / "tables" / "table_index.json", {"tables": [{"table_index": 1}]})
    return package_dir


def _write_eu_package(root: Path) -> Path:
    package_dir = root / "eu_reports" / "NL" / "ASML" / "2025" / "annual_eu123"
    _write_json(
        package_dir / "manifest.json",
        {
            "market": "EU",
            "filing_id": "EU:NL:ASML:eu123",
            "ticker": "ASML",
            "company_name": "ASML Holding N.V.",
        },
    )
    return package_dir


def test_repository_reads_and_finds_market_package(tmp_path):
    package_dir = _write_package(tmp_path)
    roots = {"HK": tmp_path / "hk_reports", "US": tmp_path / "us_sec"}

    summary = repository.read_market_package_summary(package_dir)
    detail = repository.read_market_package_detail(package_dir)
    market, found_package = repository.find_market_package_by_filing_id(
        "HK:00700:abc123",
        market="HK",
        market_wiki_roots=roots,
    )
    evidence_market, evidence_package, evidence = repository.find_market_evidence(
        "hk:abc123:p88:t1:r2",
        market="HK",
        market_wiki_roots=roots,
    )

    assert summary["market"] == "HK"
    assert summary["quality_status"] == "pass"
    assert summary["counts"] == {"sections": 2, "tables": 3, "raw_facts": 0, "metrics": 1, "evidence": 1}
    assert detail["tables"] == [{"table_index": 1}]
    assert market == "HK"
    assert found_package == package_dir
    assert evidence_market == "HK"
    assert evidence_package == package_dir
    assert evidence["local_path"] == "sections/report.md"


def test_repository_finds_eu_four_level_package(tmp_path):
    package_dir = _write_eu_package(tmp_path)
    roots = {"EU": tmp_path / "eu_reports", "HK": tmp_path / "hk_reports"}

    packages = repository.iter_market_packages("EU", roots)
    market, found_package = repository.find_market_package_by_filing_id(
        "EU:NL:ASML:eu123",
        market="EU",
        market_wiki_roots=roots,
    )

    assert packages == [package_dir]
    assert market == "EU"
    assert found_package == package_dir


def test_repository_rejects_empty_ids(tmp_path):
    roots = {"HK": tmp_path / "hk_reports"}

    for call in (
        lambda: repository.find_market_package_by_filing_id("", market_wiki_roots=roots),
        lambda: repository.find_market_evidence(" ", market_wiki_roots=roots),
    ):
        try:
            call()
        except HTTPException as exc:
            assert exc.status_code == 400
        else:
            raise AssertionError("expected HTTPException")


def test_repository_reports_missing_package_and_evidence(tmp_path):
    package_dir = _write_package(tmp_path)
    roots = {"HK": tmp_path / "hk_reports", "EU": tmp_path / "eu_reports"}

    try:
        repository.find_market_package_by_filing_id("missing", market="HK", market_wiki_roots=roots)
    except HTTPException as exc:
        assert exc.status_code == 404
        assert exc.detail == "Market evidence package not found"
    else:
        raise AssertionError("expected HTTPException")

    try:
        repository.find_market_evidence("missing-evidence", package_dir=package_dir, market_wiki_roots=roots)
    except HTTPException as exc:
        assert exc.status_code == 404
        assert exc.detail == "Evidence not found"
    else:
        raise AssertionError("expected HTTPException")
