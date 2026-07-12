import importlib.util
import json
import os
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


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _configure_wiki_root(wiki_root: Path, monkeypatch) -> None:
    monkeypatch.setattr(wiki, "WIKI_ROOT", str(wiki_root))
    monkeypatch.setattr(wiki, "WIKI_ROOT_PATH", wiki_root.resolve())
    monkeypatch.setattr(wiki, "COMPANIES_DIR", str(wiki_root / "companies"))
    monkeypatch.setattr(wiki, "_companies_list_cache", None)


def test_wiki_report_list_endpoints_return_html_reports(tmp_path, monkeypatch):
    wiki_root = tmp_path / "wiki"
    company_dir = "000333-美的集团"
    company_root = wiki_root / "companies" / company_dir

    _write_report(company_root / "analysis" / "000333-美的集团-2025-analysis.html")
    _write_report(company_root / "analysis" / "000333-美的集团-2025-analysis-test.html")
    _write_report(company_root / "analysis" / "README.md", "# ignore")
    _write_report(company_root / "factcheck" / "000333-美的集团-2025-factcheck.html")
    _write_report(company_root / "tracking" / "000333-美的集团-跟踪报告.html")
    _write_report(company_root / "tracking" / "latest.html")
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


def test_company_list_returns_only_authoritative_company_identity(tmp_path, monkeypatch):
    wiki_root = tmp_path / "wiki"
    authoritative_root = wiki_root / "companies" / "directory-name-is-not-an-identity"
    inferred_root = wiki_root / "companies" / "HK:00999-should-not-be-inferred"
    _write_json(authoritative_root / "company.json", {
        "market": "HK",
        "company_id": "HK:00005",
        "stock_code": "00005",
        "company_short_name": "HSBC HOLDINGS",
    })
    _write_json(inferred_root / "company.json", {
        "stock_code": "00999",
        "company_short_name": "No authoritative identity",
    })
    _configure_wiki_root(wiki_root, monkeypatch)

    companies = {item["dir"]: item for item in wiki.list_companies()["companies"]}

    assert companies[authoritative_root.name]["market"] == "HK"
    assert companies[authoritative_root.name]["company_id"] == "HK:00005"
    assert "market" not in companies[inferred_root.name]
    assert "company_id" not in companies[inferred_root.name]


def test_company_list_skips_unreadable_company_metadata_without_failing(tmp_path, monkeypatch):
    wiki_root = tmp_path / "wiki"
    company_root = wiki_root / "companies" / "migrated-report-only"
    company_root.mkdir(parents=True)
    (company_root / "company.json").write_text("not-json", encoding="utf-8")
    _configure_wiki_root(wiki_root, monkeypatch)

    payload = wiki.list_companies()

    assert len(payload["companies"]) == 1
    company = payload["companies"][0]
    assert {key: company[key] for key in (
        "code", "name", "dir", "hasReport", "reportCount", "hasFactcheck",
        "factcheckCount", "hasTracking", "trackingCount", "hasLegal", "legalCount",
        "sourceReportCount", "latestResultAt",
    )} == {
        "code": "migrated",
        "name": "report-only",
        "dir": "migrated-report-only",
        "hasReport": False,
        "reportCount": 0,
        "hasFactcheck": False,
        "factcheckCount": 0,
        "hasTracking": False,
        "trackingCount": 0,
        "hasLegal": False,
        "legalCount": 0,
        "sourceReportCount": 0,
        "latestResultAt": None,
    }
    assert company["latestWikiAt"]


def test_company_list_maps_explicit_exchange_metadata_to_market(tmp_path, monkeypatch):
    wiki_root = tmp_path / "wiki"
    company_root = wiki_root / "companies" / "opaque-company-dir"
    _write_json(company_root / "company.json", {
        "company_id": "issuer-42",
        "exchange": "SSE",
        "stock_code": "600104",
        "company_short_name": "上汽集团",
    })
    _configure_wiki_root(wiki_root, monkeypatch)

    company = wiki.list_companies()["companies"][0]

    assert company["market"] == "CN"
    assert company["company_id"] == "issuer-42"


def test_report_list_returns_complete_primary_report_identity(tmp_path, monkeypatch):
    wiki_root = tmp_path / "wiki"
    company_dir = "opaque-company-dir"
    company_root = wiki_root / "companies" / company_dir
    filename = "report-output-without-identity.html"
    _write_report(company_root / "analysis" / filename)
    _write_json(company_root / "company.json", {
        "market": "HK",
        "company_id": "HK:00005",
        "primary_report_id": "annual-current",
        "reports": [{
            "report_id": "annual-current",
            "filing_id": "HK:00005:2025-annual",
            "manifest": "reports/annual-current/manifest.json",
        }],
    })
    _write_json(company_root / "reports" / "annual-current" / "manifest.json", {
        "market": "HK",
        "company_id": "HK:00005",
        "filing_id": "HK:00005:2025-annual",
        "parse_run_id": "parse-hk-00005-2025",
    })
    _configure_wiki_root(wiki_root, monkeypatch)

    report = wiki.list_reports(company_dir)["reports"][0]

    expected = {
        "market": "HK",
        "company_id": "HK:00005",
        "filing_id": "HK:00005:2025-annual",
        "parse_run_id": "parse-hk-00005-2025",
    }
    assert report["research_identity"] == expected
    assert {field: report[field] for field in expected} == expected


def test_report_list_uses_explicit_html_artifact_mapping_for_multiple_reports(tmp_path, monkeypatch):
    wiki_root = tmp_path / "wiki"
    company_dir = "opaque-company-dir"
    company_root = wiki_root / "companies" / company_dir
    _write_report(company_root / "analysis" / "annual-analysis.html")
    _write_report(company_root / "analysis" / "quarterly-analysis.html")
    _write_json(company_root / "company.json", {
        "market": "US",
        "company_id": "US:0000320193",
        "reports": [
            {
                "report_id": "2025-10-K",
                "analysis_html": "analysis/annual-analysis.html",
                "filing_id": "US:AAPL:2025-10-K",
                "parse_run_id": "parse-annual",
            },
            {
                "report_id": "2026-10-Q",
                "analysis_html": "quarterly-analysis.html",
                "filing_id": "US:AAPL:2026-Q1-10-Q",
                "parse_run_id": "parse-quarterly",
            },
        ],
    })
    _configure_wiki_root(wiki_root, monkeypatch)

    reports = {item["filename"]: item for item in wiki.list_reports(company_dir)["reports"]}

    assert reports["annual-analysis.html"]["filing_id"] == "US:AAPL:2025-10-K"
    assert reports["annual-analysis.html"]["parse_run_id"] == "parse-annual"
    assert reports["quarterly-analysis.html"]["filing_id"] == "US:AAPL:2026-Q1-10-Q"
    assert reports["quarterly-analysis.html"]["parse_run_id"] == "parse-quarterly"


def test_report_list_omits_complete_identity_when_primary_report_is_incomplete(tmp_path, monkeypatch):
    wiki_root = tmp_path / "wiki"
    company_dir = "opaque-company-dir"
    company_root = wiki_root / "companies" / company_dir
    _write_report(company_root / "analysis" / "analysis.html")
    _write_json(company_root / "company.json", {
        "market": "HK",
        "company_id": "HK:00005",
        "primary_report_id": "2025-annual",
        "reports": [{
            "report_id": "2025-annual",
            "filing_id": "HK:00005:2025-annual",
        }],
    })
    _configure_wiki_root(wiki_root, monkeypatch)

    report = wiki.list_reports(company_dir)["reports"][0]

    assert "research_identity" not in report
    assert not {"market", "company_id", "filing_id", "parse_run_id"} & report.keys()


def test_report_list_requires_company_level_market_and_company_id(tmp_path, monkeypatch):
    wiki_root = tmp_path / "wiki"
    company_dir = "opaque-company-dir"
    company_root = wiki_root / "companies" / company_dir
    _write_report(company_root / "analysis" / "analysis.html")
    _write_json(company_root / "company.json", {
        "primary_report_id": "2025-annual",
        "reports": [{
            "report_id": "2025-annual",
            "market": "HK",
            "company_id": "HK:00005",
            "filing_id": "HK:00005:2025-annual",
            "parse_run_id": "parse-2025",
        }],
    })
    _configure_wiki_root(wiki_root, monkeypatch)

    report = wiki.list_reports(company_dir)["reports"][0]

    assert "research_identity" not in report
    assert not {"market", "company_id", "filing_id", "parse_run_id"} & report.keys()


def test_report_list_omits_complete_identity_on_manifest_conflict(tmp_path, monkeypatch):
    wiki_root = tmp_path / "wiki"
    company_dir = "opaque-company-dir"
    company_root = wiki_root / "companies" / company_dir
    _write_report(company_root / "analysis" / "analysis.html")
    _write_json(company_root / "company.json", {
        "market": "HK",
        "company_id": "HK:00005",
        "primary_report_id": "2025-annual",
        "reports": [{
            "report_id": "2025-annual",
            "filing_id": "HK:00005:2025-annual",
            "parse_run_id": "parse-authoritative",
        }],
    })
    _write_json(company_root / "reports" / "2025-annual" / "manifest.json", {
        "market": "HK",
        "company_id": "HK:00005",
        "filing_id": "HK:00005:2025-annual",
        "parse_run_id": "parse-conflicting",
    })
    _configure_wiki_root(wiki_root, monkeypatch)

    report = wiki.list_reports(company_dir)["reports"][0]

    assert "research_identity" not in report
    assert not {"market", "company_id", "filing_id", "parse_run_id"} & report.keys()


def test_report_list_omits_complete_identity_when_multiple_reports_are_unmapped(tmp_path, monkeypatch):
    wiki_root = tmp_path / "wiki"
    company_dir = "opaque-company-dir"
    company_root = wiki_root / "companies" / company_dir
    _write_report(company_root / "analysis" / "analysis.html")
    _write_json(company_root / "company.json", {
        "market": "HK",
        "company_id": "HK:00005",
        "reports": [
            {
                "report_id": "2024-annual",
                "filing_id": "HK:00005:2024-annual",
                "parse_run_id": "parse-2024",
            },
            {
                "report_id": "2025-annual",
                "filing_id": "HK:00005:2025-annual",
                "parse_run_id": "parse-2025",
            },
        ],
    })
    _configure_wiki_root(wiki_root, monkeypatch)

    report = wiki.list_reports(company_dir)["reports"][0]

    assert "research_identity" not in report
    assert not {"market", "company_id", "filing_id", "parse_run_id"} & report.keys()


def test_tracking_report_list_hides_latest_alias_and_sorts_by_mtime(tmp_path, monkeypatch):
    wiki_root = tmp_path / "wiki"
    company_dir = "000333-美的集团"
    tracking_dir = wiki_root / "companies" / company_dir / "tracking"
    old_report = tracking_dir / "000333-美的集团-跟踪报告-2026-06-11.html"
    new_report = tracking_dir / "000333-美的集团-跟踪报告-2026-07-11.html"
    alias_report = tracking_dir / "latest.html"

    _write_report(old_report)
    _write_report(new_report)
    _write_report(alias_report)
    os.utime(old_report, (1000, 1000))
    os.utime(new_report, (2000, 2000))
    os.utime(alias_report, (3000, 3000))

    monkeypatch.setattr(wiki, "WIKI_ROOT", str(wiki_root))
    monkeypatch.setattr(wiki, "WIKI_ROOT_PATH", wiki_root.resolve())
    monkeypatch.setattr(wiki, "COMPANIES_DIR", str(wiki_root / "companies"))

    trackings = wiki.list_trackings(company_dir)["trackings"]

    assert [item["filename"] for item in trackings] == [
        "000333-美的集团-跟踪报告-2026-07-11.html",
        "000333-美的集团-跟踪报告-2026-06-11.html",
    ]


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
    _write_json(company_root / "company.json", {
        "market": "CN",
        "company_id": "CN:600104",
        "primary_report_id": "2025-annual",
        "reports": [{
            "report_id": "2025-annual",
            "filing_id": "CN:600104:2025-annual",
            "parse_run_id": "parse-cn-600104-2025",
        }],
    })

    monkeypatch.setenv("SIQ_AUTH_SECRET_KEY", "test-secret-for-wiki-report-html-route-123456")
    monkeypatch.setattr(main.wiki, "WIKI_ROOT", str(wiki_root))
    monkeypatch.setattr(main.wiki, "WIKI_ROOT_PATH", wiki_root.resolve())
    monkeypatch.setattr(main.wiki, "COMPANIES_DIR", str(wiki_root / "companies"))
    monkeypatch.setattr(main.wiki, "_companies_list_cache", None)

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
            company_listing = client.get("/api/wiki/companies/list")
            assert company_listing.status_code == 200
            assert company_listing.json()["companies"][0]["market"] == "CN"
            assert company_listing.json()["companies"][0]["company_id"] == "CN:600104"

            listing = client.get(f"/api/wiki/companies/{company_dir}/reports")
            assert listing.status_code == 200
            report = listing.json()["reports"][0]
            assert report["filename"] == filename
            assert "%E4%B8%8A%E6%B1%BD%E9%9B%86%E5%9B%A2" in report["url"]
            assert report["research_identity"] == {
                "market": "CN",
                "company_id": "CN:600104",
                "filing_id": "CN:600104:2025-annual",
                "parse_run_id": "parse-cn-600104-2025",
            }

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
