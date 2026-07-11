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


def test_safe_sql_ident_rejects_unsafe_identifier():
    with pytest.raises(ValueError):
        status._safe_sql_ident("pdf2md_hk; drop table parse_runs")
