import importlib.util
import json
from pathlib import Path


def _load_repository():
    source = Path(__file__).resolve().parents[1] / "services" / "market_package_repository.py"
    spec = importlib.util.spec_from_file_location("market_package_repository_under_test", source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_manifest(path: Path, filing_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"market": "HK", "filing_id": filing_id}), encoding="utf-8")


def test_iter_market_packages_finds_hk_company_report_layout(tmp_path):
    repo = _load_repository()
    hk_root = tmp_path / "data" / "wiki" / "hk"
    package_dir = hk_root / "companies" / "00700-TENCENT" / "reports" / "2025-annual-12100024"
    _write_manifest(package_dir / "manifest.json", "HK:00700:12100024")

    packages = repo.iter_market_packages("HK", {"HK": hk_root})
    found_code, found_package = repo.find_market_package_by_filing_id(
        "HK:00700:12100024",
        market="HK",
        market_wiki_roots={"HK": hk_root},
    )

    assert packages == [package_dir]
    assert found_code == "HK"
    assert found_package == package_dir


def test_iter_market_packages_finds_kr_company_report_layout(tmp_path):
    repo = _load_repository()
    kr_root = tmp_path / "data" / "wiki" / "kr"
    package_dir = kr_root / "companies" / "005930-SamsungElectronics" / "reports" / "2025-annual-task-kr"
    _write_manifest(package_dir / "manifest.json", "KR:005930:task-kr")

    packages = repo.iter_market_packages("KR", {"KR": kr_root})
    found_code, found_package = repo.find_market_package_by_filing_id(
        "KR:005930:task-kr",
        market="KR",
        market_wiki_roots={"KR": kr_root},
    )

    assert packages == [package_dir]
    assert found_code == "KR"
    assert found_package == package_dir
