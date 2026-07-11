from pathlib import Path


SERVICE_DOCKERFILES = (
    "apps/api/Dockerfile",
    "apps/web/Dockerfile",
    "apps/pdf-parser/Dockerfile",
    "apps/document-parser/Dockerfile",
    "services/market-report-finder/Dockerfile",
    "services/market-report-rules/Dockerfile",
)


def test_service_dockerfiles_run_as_non_root_users():
    repo_root = Path(__file__).resolve().parents[3]

    for relative in SERVICE_DOCKERFILES:
        text = (repo_root / relative).read_text(encoding="utf-8")
        assert "\nUSER " in text, relative
        assert "\nUSER root" not in text, relative


def test_market_report_services_have_explicit_compose_users():
    repo_root = Path(__file__).resolve().parents[3]
    compose = (repo_root / "infra/docker/docker-compose.yml").read_text(encoding="utf-8")

    assert "report-finder:\n    build:" in compose
    assert "market-report-finder:\n    profiles:" in compose
    assert compose.count('user: "10001:10001"') >= 5
    assert "report-finder:\n    build:\n      context: ../../services/market-report-finder\n      dockerfile: Dockerfile\n    user: \"10001:10001\"" in compose
    assert "market-report-finder:\n    profiles: [\"external-services\"]\n    build:\n      context: ../../services/market-report-finder\n      dockerfile: Dockerfile\n    user: \"10001:10001\"" in compose


def test_ci_hadolint_covers_market_service_dockerfiles():
    repo_root = Path(__file__).resolve().parents[3]
    workflow = (repo_root / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "services/market-report-finder/Dockerfile" in workflow
    assert "services/market-report-rules/Dockerfile" in workflow
