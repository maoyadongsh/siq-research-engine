import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from db.imports import financial_query_api as api  # noqa: E402


def test_financial_query_api_reuses_project_app_url_but_targets_siq(monkeypatch):
    for key in (
        "SIQ_PDF2MD_DATABASE_URL",
        "SIQ_CN_DATABASE_URL",
        "SIQ_APP_DATABASE_URL",
        "SIQ_PGHOST",
        "SIQ_PGPORT",
        "SIQ_PGDATABASE",
        "SIQ_PGUSER",
        "SIQ_PGPASSWORD",
        "PGHOST",
        "PGPORT",
        "PGDATABASE",
        "PGUSER",
        "PGPASSWORD",
        "POSTGRES_PASSWORD",
        "DB_HOST",
        "DB_PORT",
        "DB_NAME",
        "DB_USER",
        "DB_PASSWORD",
        "DATABASE_URL",
        "SIQ_ALLOW_GENERIC_FINANCIAL_DATABASE_URL",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("SIQ_APP_DATABASE_URL", "postgresql+psycopg://postgres:secret@postgres:5432/siq_app")
    captured = {}

    def fake_connect(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(api.psycopg, "connect", fake_connect)

    api.get_connection()

    assert captured["host"] == "postgres"
    assert captured["port"] == 5432
    assert captured["dbname"] == "siq"
    assert captured["user"] == "postgres"
    assert captured["password"] == "secret"
    assert captured["row_factory"] is api.dict_row


def test_financial_query_api_explicit_pdf2md_url_overrides_app_url(monkeypatch):
    monkeypatch.setenv("SIQ_APP_DATABASE_URL", "postgresql+psycopg://postgres:app@postgres:5432/siq_app")
    monkeypatch.setenv("SIQ_PDF2MD_DATABASE_URL", "postgresql://readonly:pdf@127.0.0.1:15432/custom")
    monkeypatch.setenv("SIQ_PDF2MD_PGDATABASE", "siq")
    captured = {}

    def fake_connect(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(api.psycopg, "connect", fake_connect)

    api.get_connection()

    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 15432
    assert captured["dbname"] == "siq"
    assert captured["user"] == "readonly"
    assert captured["password"] == "pdf"
