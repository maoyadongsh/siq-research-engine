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


def test_repository_market_code_normalizes_and_rejects_unknown_market(tmp_path):
    roots = {"US": tmp_path / "us_sec", "HK": tmp_path / "hk_reports"}

    assert repository.market_code("us", roots) == "US"
    assert repository.market_code("HK", roots) == "HK"

    for value in ("", "CN", None):
        try:
            repository.market_code(value, roots)
        except HTTPException as exc:
            assert exc.status_code == 400
            assert exc.detail == "market must be one of US/HK/JP/KR/EU"
        else:
            raise AssertionError("expected HTTPException")


def test_repository_safe_package_paths_preserve_status_and_detail_contracts(tmp_path):
    repo_root = tmp_path / "repo"
    hk_root = repo_root / "data" / "wiki" / "hk_reports"
    us_root = repo_root / "data" / "wiki" / "us_sec"
    hk_package = _write_package(repo_root / "data" / "wiki")
    us_package = us_root / "AAPL" / "2025" / "10-K_demo"
    _write_json(us_package / "manifest.json", {"ticker": "AAPL"})
    hk_missing_manifest = hk_root / "00700" / "2024" / "annual_missing"
    hk_missing_manifest.mkdir(parents=True)
    us_missing_manifest = us_root / "AAPL" / "2024" / "10-K_missing"
    us_missing_manifest.mkdir(parents=True)
    outside = tmp_path / "outside" / "package"
    _write_json(outside / "manifest.json", {"ticker": "ESCAPE"})
    roots = {"HK": hk_root, "US": us_root}

    assert repository.safe_under(hk_root, hk_package) == hk_package.resolve()
    assert repository.safe_market_package_path(
        "HK",
        str(hk_package.relative_to(repo_root)),
        repo_root=repo_root,
        market_wiki_roots=roots,
    ) == hk_package.resolve()
    assert repository.safe_market_package_path(
        "HK",
        str(hk_package),
        repo_root=repo_root,
        market_wiki_roots=roots,
    ) == hk_package.resolve()
    assert repository.safe_us_sec_package_path(
        str(us_package),
        repo_root=repo_root,
        us_sec_wiki_root=us_root,
    ) == us_package.resolve()

    cases = (
        lambda: repository.safe_under(hk_root, outside),
        lambda: repository.safe_market_package_path("HK", str(outside), repo_root=repo_root, market_wiki_roots=roots),
        lambda: repository.safe_us_sec_package_path(str(outside), repo_root=repo_root, us_sec_wiki_root=us_root),
    )
    for call in cases:
        try:
            call()
        except HTTPException as exc:
            assert exc.status_code == 400
            assert exc.detail == "Path is outside the allowed evidence package root"
        else:
            raise AssertionError("expected HTTPException")

    for call in (
        lambda: repository.safe_market_package_path("HK", "", repo_root=repo_root, market_wiki_roots=roots),
        lambda: repository.safe_us_sec_package_path(None, repo_root=repo_root, us_sec_wiki_root=us_root),
    ):
        try:
            call()
        except HTTPException as exc:
            assert exc.status_code == 400
            assert exc.detail == "package_path is required"
        else:
            raise AssertionError("expected HTTPException")

    for call, detail in (
        (
            lambda: repository.safe_market_package_path(
                "HK",
                str(hk_missing_manifest),
                repo_root=repo_root,
                market_wiki_roots=roots,
            ),
            "Market evidence package not found",
        ),
        (
            lambda: repository.safe_us_sec_package_path(
                str(us_missing_manifest),
                repo_root=repo_root,
                us_sec_wiki_root=us_root,
            ),
            "US SEC package not found",
        ),
    ):
        try:
            call()
        except HTTPException as exc:
            assert exc.status_code == 404
            assert exc.detail == detail
        else:
            raise AssertionError("expected HTTPException")


def test_repository_safe_download_path_preserves_status_and_detail_contracts(tmp_path):
    downloads_root = tmp_path / "downloads"
    report = downloads_root / "EU" / "NL" / "ASML" / "2025" / "report.xhtml"
    report.parent.mkdir(parents=True)
    report.write_text("<html></html>", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    escaped_report = outside / "report.xhtml"
    escaped_report.write_text("<html></html>", encoding="utf-8")
    symlink = downloads_root / "linked-outside"
    symlink.symlink_to(outside, target_is_directory=True)

    assert repository.safe_download_path(
        "EU/NL/ASML/2025/report.xhtml",
        downloads_root=downloads_root,
    ) == report.resolve()

    for value in ("", None):
        try:
            repository.safe_download_path(value, downloads_root=downloads_root)
        except HTTPException as exc:
            assert exc.status_code == 400
            assert exc.detail == "download_relative_path is required"
        else:
            raise AssertionError("expected HTTPException")

    for value in ("/etc/passwd", "../escape.html", "EU/../escape.html"):
        try:
            repository.safe_download_path(value, downloads_root=downloads_root)
        except HTTPException as exc:
            assert exc.status_code == 400
            assert exc.detail == "Invalid download_relative_path"
        else:
            raise AssertionError("expected HTTPException")

    try:
        repository.safe_download_path("linked-outside/report.xhtml", downloads_root=downloads_root)
    except HTTPException as exc:
        assert exc.status_code == 400
        assert exc.detail == "download_relative_path is outside downloads root"
    else:
        raise AssertionError("expected HTTPException")

    try:
        repository.safe_download_path("EU/NL/ASML/2025/missing.xhtml", downloads_root=downloads_root)
    except HTTPException as exc:
        assert exc.status_code == 404
        assert exc.detail == "download_relative_path not found"
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
