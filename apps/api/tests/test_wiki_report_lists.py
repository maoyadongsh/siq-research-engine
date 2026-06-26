import importlib.util
import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

WIKI_SPEC = importlib.util.spec_from_file_location("wiki_under_test", BACKEND_ROOT / "routers" / "wiki.py")
assert WIKI_SPEC and WIKI_SPEC.loader
wiki = importlib.util.module_from_spec(WIKI_SPEC)
WIKI_SPEC.loader.exec_module(wiki)


def _write_report(path: Path, content: str = "<html>ok</html>") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_wiki_report_list_endpoints_return_html_reports(tmp_path, monkeypatch):
    wiki_root = tmp_path / "wiki"
    company_dir = "000333-美的集团"
    company_root = wiki_root / "companies" / company_dir

    _write_report(company_root / "analysis" / "000333-美的集团-2025-analysis.html")
    _write_report(company_root / "analysis" / "000333-美的集团-2025-analysis-test.html")
    _write_report(company_root / "analysis" / "README.md", "# ignore")
    _write_report(company_root / "factcheck" / "000333-美的集团-2025-factcheck.html")
    _write_report(company_root / "tracking" / "000333-美的集团-跟踪报告.html")
    _write_report(company_root / "legal" / "legal_opinion.html")

    monkeypatch.setattr(wiki, "WIKI_ROOT", str(wiki_root))
    monkeypatch.setattr(wiki, "WIKI_ROOT_PATH", wiki_root.resolve())
    monkeypatch.setattr(wiki, "COMPANIES_DIR", str(wiki_root / "companies"))

    reports = wiki.list_reports(company_dir)["reports"]
    factchecks = wiki.list_factchecks(company_dir)["factchecks"]
    trackings = wiki.list_trackings(company_dir)["trackings"]
    legals = wiki.list_legals(company_dir)["legals"]

    assert [item["filename"] for item in reports] == [
        "000333-美的集团-2025-analysis-test.html",
        "000333-美的集团-2025-analysis.html",
    ]
    assert len(factchecks) == 1
    assert len(trackings) == 1
    assert len(legals) == 1
    assert reports[0]["url"].startswith(f"/api/wiki/companies/{company_dir}/analysis/")


def test_wiki_report_paths_reject_traversal(tmp_path, monkeypatch):
    wiki_root = tmp_path / "wiki"
    (wiki_root / "companies").mkdir(parents=True)

    monkeypatch.setattr(wiki, "WIKI_ROOT", str(wiki_root))
    monkeypatch.setattr(wiki, "WIKI_ROOT_PATH", wiki_root.resolve())
    monkeypatch.setattr(wiki, "COMPANIES_DIR", str(wiki_root / "companies"))

    try:
        wiki.list_reports("../outside")
    except Exception as exc:
        assert getattr(exc, "status_code", None) in {400, 403}
    else:
        raise AssertionError("path traversal was not rejected")
