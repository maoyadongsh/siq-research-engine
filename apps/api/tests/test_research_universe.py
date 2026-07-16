from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers import research_universe as research_universe_router
from services import agent_runtime_catalog, research_report_package, research_universe
from services.auth_dependencies import get_current_user
from services.auth_service import User, UserRole
from services.research_report_package import enumerate_companies, resolve_report_package
from services.research_universe import (
    delete_artifact,
    list_artifacts,
    list_companies,
    list_markets,
    list_reports,
    resolve_artifact,
)
from siq_market_contracts import AgentArtifactV2, ArtifactQuality, EvidenceSummary
from tests.research_universe_fixture import add_company, build_six_market_wiki


def _company(wiki_root: Path, market: str):
    return next(item for item in enumerate_companies(wiki_root=wiki_root, markets=(market,)))


def _write_exact_analysis(
    package,
    artifact_id: str = "analysis-us-aapl-v1",
    *,
    created_at: str = "2026-07-16T00:00:00Z",
) -> None:
    output_dir = package.output_dirs["analysis"]
    output_dir.mkdir(parents=True, exist_ok=True)
    html = f"<!doctype html><html><body>exact report {artifact_id}</body></html>"
    html_path = output_dir / f"{artifact_id}.html"
    html_path.write_text(html, encoding="utf-8")
    artifact = AgentArtifactV2(
        artifact_id=artifact_id,
        artifact_type="analysis",
        status="completed",
        created_at=created_at,
        research_target=package.research_target,
        source_report_id=package.report_id,
        source_family=package.research_target.source_report.source_family,
        adapter_version="sec_ixbrl_v1",
        upstream_artifact_ids=(),
        html_file=html_path.name,
        content_hash=hashlib.sha256(html.encode("utf-8")).hexdigest(),
        quality=ArtifactQuality(status="pass"),
        evidence_summary=EvidenceSummary(citation_count=2),
    )
    (output_dir / f"{artifact_id}.artifact.json").write_text(
        __import__("json").dumps(artifact.to_dict()),
        encoding="utf-8",
    )


def test_universe_returns_six_markets_in_product_order_and_legal_is_cn_only(tmp_path, monkeypatch) -> None:
    wiki_root = tmp_path / "wiki"
    build_six_market_wiki(wiki_root)
    monkeypatch.setenv("SIQ_MULTI_MARKET_RESEARCH_ENABLED", "1")

    payload = list_markets(agent_type="analysis", wiki_root=wiki_root)
    assert [(item["market"], item["label"]) for item in payload["markets"]] == [
        ("CN", "中国内地市场"),
        ("HK", "香港市场"),
        ("US", "美国市场"),
        ("EU", "欧洲市场"),
        ("KR", "韩国市场"),
        ("JP", "日本市场"),
    ]
    assert list_markets(agent_type="legal", wiki_root=wiki_root)["markets"] == [payload["markets"][0]]


def test_feature_flags_fail_closed_to_cn_and_degrade_us_adapter(tmp_path, monkeypatch) -> None:
    wiki_root = tmp_path / "wiki"
    build_six_market_wiki(wiki_root)
    monkeypatch.delenv("SIQ_MULTI_MARKET_RESEARCH_ENABLED", raising=False)
    monkeypatch.delenv("SIQ_US_SEC_ANALYSIS_ENABLED", raising=False)

    assert [item["market"] for item in list_markets(agent_type="analysis", wiki_root=wiki_root)["markets"]] == [
        "CN"
    ]

    monkeypatch.setenv("SIQ_MULTI_MARKET_RESEARCH_ENABLED", "1")
    us_market = list_markets(agent_type="analysis", wiki_root=wiki_root)["markets"][2]
    assert us_market["market"] == "US"
    assert us_market["capabilities"]["analysis_adapter"] is False
    assert us_market["degraded_reasons"] == ["source_adapter_unavailable"]
    us_company = list_companies(
        market="US",
        agent_type="analysis",
        wiki_root=wiki_root,
    )["companies"][0]
    assert us_company["capabilities"]["analysis_input_ready"] is False
    assert "source_adapter_unavailable" in us_company["degraded_reasons"]


def test_company_and_report_lists_hide_fail_but_keep_warning(tmp_path, monkeypatch) -> None:
    wiki_root = tmp_path / "wiki"
    build_six_market_wiki(wiki_root)
    monkeypatch.setenv("SIQ_MULTI_MARKET_RESEARCH_ENABLED", "1")

    companies = list_companies(market="JP", agent_type="analysis", wiki_root=wiki_root)["companies"]
    assert [item["display_code"] for item in companies] == ["7203"]
    reports = list_reports(
        market="JP",
        company_key=companies[0]["company_key"],
        agent_type="analysis",
        wiki_root=wiki_root,
    )["reports"]
    assert reports[0]["quality_status"] == "warning"
    assert "warning" in reports[0]["label"]


def test_exact_sidecar_becomes_report_baseline_and_legacy_html_stays_unbound(tmp_path, monkeypatch) -> None:
    wiki_root = tmp_path / "wiki"
    build_six_market_wiki(wiki_root)
    monkeypatch.setenv("SIQ_MULTI_MARKET_RESEARCH_ENABLED", "1")
    monkeypatch.setenv("SIQ_US_SEC_ANALYSIS_ENABLED", "1")
    company = _company(wiki_root, "US")
    package = resolve_report_package(
        market="US",
        company_key=company.company_key,
        report_id="2025-10-K-0000320193-25-000079",
        agent_type="analysis",
        wiki_root=wiki_root,
    )
    _write_exact_analysis(package)

    report = list_reports(
        market="US",
        company_key=company.company_key,
        agent_type="analysis",
        wiki_root=wiki_root,
    )["reports"][0]
    assert report["baseline_analysis_artifact_id"] == "analysis-us-aapl-v1"
    assert report["capabilities"]["factcheck_ready"] is True
    artifacts = list_artifacts(
        market="US",
        company_key=company.company_key,
        report_id=package.report_id,
        artifact_type="analysis",
        wiki_root=wiki_root,
    )
    assert [item["artifact_id"] for item in artifacts["artifacts"]] == ["analysis-us-aapl-v1"]
    assert artifacts["legacy_artifacts"] == []
    assert artifacts["artifacts"][0]["filename"] == "analysis-us-aapl-v1.html"
    assert resolve_artifact(
        "analysis-us-aapl-v1",
        expected_identity=package.research_identity,
        wiki_root=wiki_root,
    ).html_path.is_file()

    cn_company = _company(wiki_root, "CN")
    cn_package = resolve_report_package(
        market="CN",
        company_key=cn_company.company_key,
        report_id="2025-annual",
        agent_type="analysis",
        wiki_root=wiki_root,
    )
    cn_package.output_dirs["analysis"].mkdir(parents=True, exist_ok=True)
    _write_exact_analysis(cn_package, artifact_id="analysis-cn-v1")
    (cn_package.output_dirs["analysis"] / "canonical-alias.html").write_text(
        (cn_package.output_dirs["analysis"] / "analysis-cn-v1.html").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (cn_package.output_dirs["analysis"] / "old-cn-report.html").write_text("<html>legacy</html>", encoding="utf-8")
    cn_artifacts = list_artifacts(
        market="CN",
        company_key=cn_company.company_key,
        report_id=cn_package.report_id,
        artifact_type="analysis",
        wiki_root=wiki_root,
    )
    assert [item["artifact_id"] for item in cn_artifacts["artifacts"]] == ["analysis-cn-v1"]
    assert len(cn_artifacts["legacy_artifacts"]) == 1
    assert cn_artifacts["legacy_artifacts"][0]["filename"] == "old-cn-report.html"
    assert cn_artifacts["legacy_artifacts"][0]["identity_status"] == "legacy_unbound"
    assert cn_artifacts["legacy_artifacts"][0]["usable_as_baseline"] is False


def test_delete_by_artifact_id_only_removes_derived_html_and_sidecar(tmp_path, monkeypatch) -> None:
    wiki_root = tmp_path / "wiki"
    build_six_market_wiki(wiki_root)
    monkeypatch.setenv("SIQ_MULTI_MARKET_RESEARCH_ENABLED", "1")
    monkeypatch.setenv("SIQ_US_SEC_ANALYSIS_ENABLED", "1")
    company = _company(wiki_root, "US")
    package = resolve_report_package(
        market="US",
        company_key=company.company_key,
        report_id="2025-10-K-0000320193-25-000079",
        agent_type="analysis",
        wiki_root=wiki_root,
    )
    _write_exact_analysis(package)
    output_dir = package.output_dirs["analysis"]
    markdown_path = output_dir / "analysis-us-aapl-v1.md"
    json_path = output_dir / "analysis-us-aapl-v1.json"
    markdown_path.write_text("# analysis", encoding="utf-8")
    json_path.write_text("{}", encoding="utf-8")
    sidecar_path = output_dir / "analysis-us-aapl-v1.artifact.json"
    sidecar = __import__("json").loads(sidecar_path.read_text(encoding="utf-8"))
    sidecar["metadata"] = {
        "markdown_file": markdown_path.name,
        "json_file": json_path.name,
    }
    sidecar_path.write_text(__import__("json").dumps(sidecar), encoding="utf-8")
    duplicate_html = output_dir / "generator-output.html"
    duplicate_html.write_text(
        (output_dir / "analysis-us-aapl-v1.html").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    fact_paths = tuple(path for paths in (package.fulltext_paths, package.metric_paths, package.evidence_paths) for path in paths)

    assert delete_artifact("analysis-us-aapl-v1", wiki_root=wiki_root) == {
        "deleted": True,
        "artifact_id": "analysis-us-aapl-v1",
    }
    assert not (package.output_dirs["analysis"] / "analysis-us-aapl-v1.html").exists()
    assert not (package.output_dirs["analysis"] / "analysis-us-aapl-v1.artifact.json").exists()
    assert not markdown_path.exists()
    assert not json_path.exists()
    assert not duplicate_html.exists()
    assert all(path.is_file() for path in fact_paths)


def test_tampered_exact_html_is_not_exposed_or_selected_as_baseline(tmp_path, monkeypatch) -> None:
    wiki_root = tmp_path / "wiki"
    build_six_market_wiki(wiki_root)
    monkeypatch.setenv("SIQ_MULTI_MARKET_RESEARCH_ENABLED", "1")
    monkeypatch.setenv("SIQ_US_SEC_ANALYSIS_ENABLED", "1")
    company = _company(wiki_root, "US")
    package = resolve_report_package(
        market="US",
        company_key=company.company_key,
        report_id="2025-10-K-0000320193-25-000079",
        agent_type="analysis",
        wiki_root=wiki_root,
    )
    _write_exact_analysis(package)
    (package.output_dirs["analysis"] / "analysis-us-aapl-v1.html").write_text(
        "<html>tampered after publish</html>",
        encoding="utf-8",
    )

    report = list_reports(
        market="US",
        company_key=company.company_key,
        agent_type="analysis",
        wiki_root=wiki_root,
    )["reports"][0]
    artifacts = list_artifacts(
        market="US",
        company_key=company.company_key,
        report_id=package.report_id,
        artifact_type="analysis",
        wiki_root=wiki_root,
    )

    assert report["baseline_analysis_artifact_id"] is None
    assert artifacts["artifacts"] == []


def test_artifact_list_does_not_hash_html_and_scoped_content_resolution_hashes_only_selected(
    tmp_path,
    monkeypatch,
) -> None:
    wiki_root = tmp_path / "wiki"
    build_six_market_wiki(wiki_root)
    monkeypatch.setenv("SIQ_MULTI_MARKET_RESEARCH_ENABLED", "1")
    monkeypatch.setenv("SIQ_US_SEC_ANALYSIS_ENABLED", "1")
    company = _company(wiki_root, "US")
    package = resolve_report_package(
        market="US",
        company_key=company.company_key,
        report_id="2025-10-K-0000320193-25-000079",
        agent_type="analysis",
        wiki_root=wiki_root,
    )
    for index in range(5):
        _write_exact_analysis(
            package,
            artifact_id=f"analysis-us-aapl-v{index}",
            created_at=f"2026-07-16T0{index}:00:00Z",
        )

    original_sha256 = research_report_package._sha256
    hashed_paths: list[Path] = []

    def observed_sha256(path: Path) -> str:
        hashed_paths.append(path)
        return original_sha256(path)

    monkeypatch.setattr(research_report_package, "_sha256", observed_sha256)
    company_rows = list_companies(
        market="US",
        agent_type="analysis",
        wiki_root=wiki_root,
    )["companies"]
    assert company_rows[0]["capabilities"]["analysis_output_ready"] is True
    assert hashed_paths == []
    lazy_reports = list_reports(
        market="US",
        company_key=company.company_key,
        agent_type="analysis",
        defer_artifact_integrity=True,
        wiki_root=wiki_root,
    )["reports"]
    assert lazy_reports[0]["baseline_analysis_artifact_id"] == "analysis-us-aapl-v4"
    assert (
        lazy_reports[0]["baseline_analysis_integrity_status"]
        == "deferred_until_content_or_workflow_request"
    )
    assert hashed_paths == []

    original_readable_file = research_report_package._readable_file

    def unexpected_body_probe(path: Path) -> bool:
        if path.suffix.lower() == ".html":
            raise AssertionError("artifact list must not open HTML content")
        return original_readable_file(path)

    monkeypatch.setattr(research_report_package, "_readable_file", unexpected_body_probe)

    first_page = list_artifacts(
        market="US",
        company_key=company.company_key,
        report_id=package.report_id,
        artifact_type="analysis",
        limit=1,
        wiki_root=wiki_root,
    )

    assert [item["artifact_id"] for item in first_page["items"]] == ["analysis-us-aapl-v4"]
    assert first_page["pagination"] == {
        "limit": 1,
        "next_cursor": "exact:1",
        "has_more": True,
        "targeted": False,
    }
    assert first_page["items"][0]["content_integrity_status"] == "deferred_until_content_request"
    assert hashed_paths == []
    assert "html_path" not in str(first_page)

    monkeypatch.setattr(
        research_universe,
        "_all_packages",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("global scan must not run")),
    )
    resolved = resolve_artifact(
        "analysis-us-aapl-v4",
        market="US",
        company_key=company.company_key,
        report_id=package.report_id,
        artifact_type="analysis",
        wiki_root=wiki_root,
    )
    assert resolved.artifact.artifact_id == "analysis-us-aapl-v4"
    assert [path.name for path in hashed_paths] == ["analysis-us-aapl-v4.html"]


def test_artifact_target_restore_and_cursor_validation_are_identity_scoped(tmp_path, monkeypatch) -> None:
    wiki_root = tmp_path / "wiki"
    build_six_market_wiki(wiki_root)
    monkeypatch.setenv("SIQ_MULTI_MARKET_RESEARCH_ENABLED", "1")
    monkeypatch.setenv("SIQ_US_SEC_ANALYSIS_ENABLED", "1")
    company = _company(wiki_root, "US")
    package = resolve_report_package(
        market="US",
        company_key=company.company_key,
        report_id="2025-10-K-0000320193-25-000079",
        agent_type="analysis",
        wiki_root=wiki_root,
    )
    _write_exact_analysis(package, "analysis-us-aapl-v1")

    targeted = list_artifacts(
        market="US",
        company_key=company.company_key,
        report_id=package.report_id,
        artifact_type="analysis",
        limit=1,
        requested_artifact_id="analysis-us-aapl-v1",
        wiki_root=wiki_root,
    )
    assert [item["artifact_id"] for item in targeted["items"]] == ["analysis-us-aapl-v1"]
    assert targeted["pagination"]["targeted"] is True

    try:
        list_artifacts(
            market="US",
            company_key=company.company_key,
            report_id=package.report_id,
            artifact_type="analysis",
            limit=1,
            cursor="../../etc/passwd",
            wiki_root=wiki_root,
        )
    except research_universe.ResearchUniverseError as exc:
        assert exc.code == "artifact_cursor_invalid"
    else:
        raise AssertionError("unsafe cursor must fail closed")


def test_router_enforces_view_and_delete_permissions_without_exposing_paths(tmp_path, monkeypatch) -> None:
    wiki_root = tmp_path / "wiki"
    build_six_market_wiki(wiki_root)
    monkeypatch.setenv("SIQ_MULTI_MARKET_RESEARCH_ENABLED", "1")
    monkeypatch.setenv("SIQ_US_SEC_ANALYSIS_ENABLED", "1")
    monkeypatch.setattr(agent_runtime_catalog, "WIKI_ROOT", wiki_root)
    company = _company(wiki_root, "US")
    package = resolve_report_package(
        market="US",
        company_key=company.company_key,
        report_id="2025-10-K-0000320193-25-000079",
        agent_type="analysis",
        wiki_root=wiki_root,
    )
    _write_exact_analysis(package)
    app = FastAPI()
    app.include_router(research_universe_router.router, prefix="/api")

    async def viewer():
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

    app.dependency_overrides[get_current_user] = viewer
    with TestClient(app) as client:
        response = client.get("/api/research-universe/markets?agent_type=analysis")
        assert response.status_code == 200
        assert response.json()["markets"][2]["market"] == "US"
        assert "/tmp/" not in response.text

        companies = client.get("/api/research-universe/companies?market=US&agent_type=analysis")
        assert companies.status_code == 200
        company_payload = companies.json()["companies"][0]
        assert company_payload["company_key"] == company.company_key
        assert "company_dir" not in companies.text

        reports = client.get(
            f"/api/research-universe/companies/{company.company_key}/reports",
            params={"market": "US", "agent_type": "analysis"},
        )
        assert reports.status_code == 200
        assert reports.json()["reports"][0]["baseline_analysis_artifact_id"] == "analysis-us-aapl-v1"
        assert reports.json()["reports"][0]["baseline_analysis_integrity_status"] == "verified"

        lazy_reports = client.get(
            f"/api/research-universe/companies/{company.company_key}/reports",
            params={
                "market": "US",
                "agent_type": "analysis",
                "defer_artifact_integrity": "true",
            },
        )
        assert lazy_reports.status_code == 200
        assert (
            lazy_reports.json()["reports"][0]["baseline_analysis_integrity_status"]
            == "deferred_until_content_or_workflow_request"
        )

        artifacts = client.get(
            f"/api/research-universe/companies/{company.company_key}/artifacts",
            params={
                "market": "US",
                "agent_type": "analysis",
                "artifact_type": "analysis",
                "report_id": package.report_id,
            },
        )
        assert artifacts.status_code == 200
        assert artifacts.json()["artifacts"][0]["content_url"].endswith(
            "/analysis-us-aapl-v1/content"
        )

        first_page = client.get(
            f"/api/research-universe/companies/{company.company_key}/artifacts",
            params={
                "market": "US",
                "artifact_type": "analysis",
                "report_id": package.report_id,
                "limit": 1,
            },
        )
        assert first_page.status_code == 200
        assert [item["artifact_id"] for item in first_page.json()["items"]] == [
            "analysis-us-aapl-v1"
        ]
        assert first_page.json()["pagination"]["limit"] == 1

        content = client.get("/api/research-universe/artifacts/analysis-us-aapl-v1/content")
        assert content.status_code == 200
        assert "exact report" in content.text
        assert content.headers["cache-control"].startswith("no-store")
        scoped_content = client.get(
            "/api/research-universe/artifacts/analysis-us-aapl-v1/content",
            params={
                "market": "US",
                "company_key": company.company_key,
                "report_id": package.report_id,
                "artifact_type": "analysis",
            },
        )
        assert scoped_content.status_code == 200
        assert "exact report" in scoped_content.text
        incomplete_scope = client.get(
            "/api/research-universe/artifacts/analysis-us-aapl-v1/content",
            params={"market": "US"},
        )
        assert incomplete_scope.status_code == 400
        assert incomplete_scope.json()["detail"]["code"] == "artifact_scope_incomplete"

        forbidden = client.delete("/api/research-universe/artifacts/not-an-artifact")
        assert forbidden.status_code == 403
        assert forbidden.json()["detail"]["code"] == "permission_denied"
        diagnostics = client.get(
            "/api/research-universe/companies",
            params={"market": "US", "agent_type": "analysis", "include_unready": "true"},
        )
        assert diagnostics.status_code == 403
        assert diagnostics.json()["detail"]["code"] == "permission_denied"

    async def administrator():
        return User(
            id=2,
            username="admin",
            email="admin@example.test",
            full_name="Admin",
            hashed_password="x",
            role=UserRole.ADMIN,
            approval_status="approved",
            is_active=True,
        )

    app.dependency_overrides[get_current_user] = administrator
    with TestClient(app) as client:
        deleted = client.delete("/api/research-universe/artifacts/analysis-us-aapl-v1")
        assert deleted.status_code == 200
        assert deleted.json() == {"deleted": True, "artifact_id": "analysis-us-aapl-v1"}
    assert package.fulltext_paths[0].is_file()
