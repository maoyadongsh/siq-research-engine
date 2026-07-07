import importlib.util
import sys
from pathlib import Path

from fastapi.testclient import TestClient


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

WIKI_SPEC = importlib.util.spec_from_file_location("wiki_under_test", BACKEND_ROOT / "routers" / "wiki.py")
assert WIKI_SPEC and WIKI_SPEC.loader
wiki = importlib.util.module_from_spec(WIKI_SPEC)
WIKI_SPEC.loader.exec_module(wiki)

import main  # noqa: E402
from services.auth_dependencies import get_current_user  # noqa: E402
from services.auth_service import User, UserRole  # noqa: E402


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
    assert reports[0]["url"].startswith("/api/wiki/companies/000333-%E7%BE%8E%E7%9A%84%E9%9B%86%E5%9B%A2/analysis/")


def test_wiki_report_list_encodes_chinese_analysis_html_url(tmp_path, monkeypatch):
    wiki_root = tmp_path / "wiki"
    company_dir = "600104-上汽集团"
    filename = "600104-上汽集团-2025-analysis-research-pack-sample.html"
    company_root = wiki_root / "companies" / company_dir
    _write_report(company_root / "analysis" / filename, "<!doctype html><html><body>上汽集团</body></html>")

    monkeypatch.setattr(wiki, "WIKI_ROOT", str(wiki_root))
    monkeypatch.setattr(wiki, "WIKI_ROOT_PATH", wiki_root.resolve())
    monkeypatch.setattr(wiki, "COMPANIES_DIR", str(wiki_root / "companies"))

    reports = wiki.list_reports(company_dir)["reports"]

    assert reports == [
        {
            "filename": filename,
            "url": (
                "/api/wiki/companies/600104-%E4%B8%8A%E6%B1%BD%E9%9B%86%E5%9B%A2/"
                "analysis/600104-%E4%B8%8A%E6%B1%BD%E9%9B%86%E5%9B%A2-2025-analysis-research-pack-sample.html"
            ),
            "size": len("<!doctype html><html><body>上汽集团</body></html>".encode("utf-8")),
            "mtime": reports[0]["mtime"],
        }
    ]


def test_wiki_report_html_route_serves_encoded_analysis_url(tmp_path, monkeypatch):
    wiki_root = tmp_path / "wiki"
    company_dir = "600104-上汽集团"
    filename = "600104-上汽集团-2025-analysis-research-pack-sample.html"
    html = "<!doctype html><html><body>上汽集团 HTML 报告</body></html>"
    company_root = wiki_root / "companies" / company_dir
    _write_report(company_root / "analysis" / filename, html)

    monkeypatch.setenv("SIQ_AUTH_SECRET_KEY", "test-secret-for-wiki-report-html-route-123456")
    monkeypatch.setattr(main.wiki, "WIKI_ROOT", str(wiki_root))
    monkeypatch.setattr(main.wiki, "WIKI_ROOT_PATH", wiki_root.resolve())
    monkeypatch.setattr(main.wiki, "COMPANIES_DIR", str(wiki_root / "companies"))

    original_overrides = main.app.dependency_overrides.copy()

    async def current_user():
        return User(
            id=1,
            username="viewer",
            email="viewer@example.test",
            full_name="Viewer",
            hashed_password="x",
            role=UserRole.VIEWER,
            approval_status="approved",
            is_active=True,
        )

    main.app.dependency_overrides[get_current_user] = current_user
    try:
        with TestClient(main.app) as client:
            listing = client.get(f"/api/wiki/companies/{company_dir}/reports")
            assert listing.status_code == 200
            report = listing.json()["reports"][0]
            assert report["filename"] == filename
            assert "%E4%B8%8A%E6%B1%BD%E9%9B%86%E5%9B%A2" in report["url"]

            response = client.get(report["url"])
            assert response.status_code == 200
            assert "text/html" in response.headers["content-type"]
            assert "上汽集团 HTML 报告" in response.text
            assert response.headers["Cache-Control"].startswith("no-store")
    finally:
        main.app.dependency_overrides.clear()
        main.app.dependency_overrides.update(original_overrides)


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
