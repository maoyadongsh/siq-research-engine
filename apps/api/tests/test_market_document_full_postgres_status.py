import hashlib
import sys
import types
from pathlib import Path

import pytest

from services import market_document_full_postgres_status as status


class FakeCursor:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class FakeConn:
    def __init__(self, counts=None):
        self.counts = counts or {}

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def execute(self, sql, params=None):
        text = " ".join(str(sql).split())
        if "information_schema.tables" in text:
            return FakeCursor((1,))
        if text.startswith("select count(*)"):
            table = text.split(" from ", 1)[1].split(" where ", 1)[0]
            counts = {
                "pdf2md_hk.parse_runs": 1,
                "pdf2md_hk.financial_statement_items": 2,
                "pdf2md_hk.document_tables": 1,
                "pdf2md_hk.document_chunks": 3,
                "pdf2md_hk.evidence_citations": 2,
            }
            counts.update(self.counts)
            return FakeCursor((counts.get(table, 0),))
        raise AssertionError(f"unexpected SQL: {sql!r} params={params!r}")


def test_market_document_full_db_status_counts_ready_parse_run(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "psycopg", types.SimpleNamespace(connect=lambda _url: FakeConn()))
    document_root = tmp_path / "document-full"
    document_root.mkdir()

    result = status.market_document_full_db_status(
        "HK",
        repo_root=tmp_path,
        market_document_full_roots={"HK": document_root},
        safe_market_document_full_path=lambda _market, value: Path(str(value)),
        market_databases={"HK": "siq_hk"},
        parse_run_id="parse-1",
    )

    assert result["status"] == "postgres_ready"
    assert result["database"] == "siq_hk"
    assert result["schema"] == "pdf2md_hk"
    assert result["parse_runs"] == 1
    assert result["facts"] == 2
    assert result["tables"] == 1
    assert result["chunks"] == 3
    assert result["evidence"] == 2
    assert result["missing_counts"] == []


def test_market_document_full_db_status_missing_counts_cover_ready_contract(monkeypatch, tmp_path):
    monkeypatch.setitem(
        sys.modules,
        "psycopg",
        types.SimpleNamespace(
            connect=lambda _url: FakeConn(
                counts={
                    "pdf2md_hk.financial_statement_items": 0,
                    "pdf2md_hk.document_tables": 0,
                    "pdf2md_hk.document_chunks": 0,
                    "pdf2md_hk.evidence_citations": 0,
                }
            )
        ),
    )
    document_root = tmp_path / "document-full"
    document_root.mkdir()

    result = status.market_document_full_db_status(
        "HK",
        repo_root=tmp_path,
        market_document_full_roots={"HK": document_root},
        safe_market_document_full_path=lambda _market, value: Path(str(value)),
        market_databases={"HK": "siq_hk"},
        parse_run_id="parse-1",
    )

    assert result["status"] == "missing"
    assert result["parse_runs"] == 1
    assert result["missing_counts"] == ["facts", "tables", "chunks", "evidence"]


def test_market_document_full_db_status_reports_missing_parse_run(monkeypatch, tmp_path):
    monkeypatch.setitem(
        sys.modules,
        "psycopg",
        types.SimpleNamespace(connect=lambda _url: FakeConn(counts={"pdf2md_hk.parse_runs": 0})),
    )
    document_root = tmp_path / "document-full"
    document_root.mkdir()

    result = status.market_document_full_db_status(
        "HK",
        repo_root=tmp_path,
        market_document_full_roots={"HK": document_root},
        safe_market_document_full_path=lambda _market, value: Path(str(value)),
        market_databases={"HK": "siq_hk"},
        parse_run_id="parse-1",
    )

    assert result["status"] == "missing"
    assert result["missing_counts"] == ["parse_runs"]


def test_market_document_full_db_status_warning_lists_partial_missing_counts(monkeypatch, tmp_path):
    monkeypatch.setitem(
        sys.modules,
        "psycopg",
        types.SimpleNamespace(
            connect=lambda _url: FakeConn(
                counts={
                    "pdf2md_hk.document_tables": 0,
                    "pdf2md_hk.document_chunks": 0,
                    "pdf2md_hk.evidence_citations": 0,
                }
            )
        ),
    )
    document_root = tmp_path / "document-full"
    document_root.mkdir()

    result = status.market_document_full_db_status(
        "HK",
        repo_root=tmp_path,
        market_document_full_roots={"HK": document_root},
        safe_market_document_full_path=lambda _market, value: Path(str(value)),
        market_databases={"HK": "siq_hk"},
        parse_run_id="parse-1",
    )

    assert result["status"] == "warning"
    assert result["missing_counts"] == ["tables", "chunks", "evidence"]


class PathLookupConn(FakeConn):
    def __init__(self, *, stored_sha256: str):
        super().__init__(
            counts={
                "sec_us.parse_runs": 1,
                "sec_us.financial_statement_items": 2,
                "sec_us.document_tables": 1,
                "sec_us.document_chunks": 3,
                "sec_us.evidence_citations": 2,
            }
        )
        self.stored_sha256 = stored_sha256

    def execute(self, sql, params=None):
        text = " ".join(str(sql).split())
        if "from sec_us.parse_runs" in text and "raw->>'document_full_path'" in text:
            return FakeCursor(("parse-us-path", "US:filing-1", self.stored_sha256))
        return super().execute(sql, params)


@pytest.mark.parametrize(
    ("stored_sha256", "expected_status", "expected_artifact_status"),
    [
        ("current", "postgres_ready", "current"),
        ("0" * 64, "stale", "stale"),
    ],
)
def test_market_document_full_db_status_compares_current_artifact_sha(
    monkeypatch,
    tmp_path,
    stored_sha256,
    expected_status,
    expected_artifact_status,
):
    document_root = tmp_path / "document-full"
    document_full = document_root / "task-1" / "document_full.json"
    document_full.parent.mkdir(parents=True)
    document_full.write_text('{"market":"US"}', encoding="utf-8")
    current_sha256 = hashlib.sha256(document_full.read_bytes()).hexdigest()
    database_sha256 = current_sha256 if stored_sha256 == "current" else stored_sha256
    monkeypatch.setitem(
        sys.modules,
        "psycopg",
        types.SimpleNamespace(connect=lambda _url: PathLookupConn(stored_sha256=database_sha256)),
    )

    result = status.market_document_full_db_status(
        "US",
        repo_root=tmp_path,
        market_document_full_roots={"US": document_root},
        safe_market_document_full_path=lambda _market, _value: document_full,
        market_databases={"US": "siq_us"},
        document_full_path="task-1/document_full.json",
    )

    assert result["status"] == expected_status
    assert result["artifact_status"] == expected_artifact_status
    assert result["current_document_full_sha256"] == current_sha256
    assert result["postgres_document_full_sha256"] == database_sha256
    assert result["missing_counts"] == []


def test_safe_sql_ident_rejects_unsafe_identifier():
    with pytest.raises(ValueError):
        status._safe_sql_ident("pdf2md_hk; drop table parse_runs")
