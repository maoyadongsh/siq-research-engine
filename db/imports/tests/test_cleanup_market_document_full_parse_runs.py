import importlib.util
import sys
from pathlib import Path


def _load_module():
    imports_dir = Path(__file__).resolve().parents[1]
    if str(imports_dir) not in sys.path:
        sys.path.insert(0, str(imports_dir))
    path = imports_dir / "cleanup_market_document_full_parse_runs.py"
    spec = importlib.util.spec_from_file_location("cleanup_market_document_full_parse_runs", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeCursor:
    def __init__(self, rows=None):
        self._rows = rows or []

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class FakeConn:
    def __init__(self, parse_run_rows=None):
        self.executed = []
        self.committed = False
        self.parse_run_rows = [("parse-old",)] if parse_run_rows is None else parse_run_rows

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def cursor(self):
        return self

    def transaction(self):
        return self

    def commit(self):
        self.committed = True

    def fetchone(self):
        return ("siq_hk",)

    def execute(self, sql, params=None):
        self.executed.append((" ".join(str(sql).split()), params))
        text = self.executed[-1][0]
        if "current_database()" in text:
            return FakeCursor([("siq_hk",)])
        if "information_schema.tables" in text:
            return FakeCursor([(1,)])
        if "information_schema.columns" in text:
            if "column_name = 'parse_run_id'" in text:
                return FakeCursor([("financial_statement_items",), ("evidence_citations",), ("parse_runs",)])
            table = params[1]
            columns = {
                "financial_statement_items": [("parse_run_id",)],
                "evidence_citations": [("parse_run_id",)],
                "parse_runs": [("parse_run_id",)],
            }
            return FakeCursor(columns.get(table, []))
        if text.startswith("select pr.parse_run_id"):
            return FakeCursor(self.parse_run_rows)
        if text.startswith("select count(*)"):
            if ".v_agent_financial_facts " in text:
                return FakeCursor([(0 if self.committed else 3,)])
            if self.committed:
                return FakeCursor([(0,)])
            if ".parse_runs " in text:
                return FakeCursor([(1,)])
            return FakeCursor([(2,)])
        return FakeCursor()


def test_cleanup_parse_runs_dry_run_counts_without_deleting(monkeypatch):
    module = _load_module()
    conn = FakeConn()
    monkeypatch.setattr(module, "connect", lambda _url: conn)

    result = module.cleanup_parse_runs("HK", ["parse-old", "parse-old"], database_url_value="postgresql://db/siq")

    assert result["market"] == "HK"
    assert result["database"] == "siq_hk"
    assert result["selectors"]["parse_run_ids"] == ["parse-old"]
    assert result["parse_run_ids"] == ["parse-old"]
    assert result["counts"]["financial_statement_items"] == 2
    assert result["counts"]["parse_runs"] == 1
    assert not any("delete from pdf2md_hk" in sql for sql, _params in conn.executed)
    assert conn.committed is False


def test_cleanup_parse_runs_apply_deletes_children_then_parse_run(monkeypatch):
    module = _load_module()
    conn = FakeConn()
    monkeypatch.setattr(module, "connect", lambda _url: conn)

    result = module.cleanup_parse_runs("HK", ["parse-old"], database_url_value="postgresql://db/siq", apply=True)

    assert result["apply"] is True
    delete_sql = [sql for sql, _params in conn.executed if sql.startswith("delete from")]
    assert any("delete from pdf2md_hk.financial_statement_items where parse_run_id" in sql for sql in delete_sql)
    assert any("delete from pdf2md_hk.evidence_citations where parse_run_id" in sql for sql in delete_sql)
    assert delete_sql[-1] == "delete from pdf2md_hk.parse_runs where parse_run_id = any(%s)"
    assert conn.committed is True
    assert result["post_cleanup_probe"]["cleaned"] is True
    assert result["post_cleanup_probe"]["agent_view_rows"] == 0


def test_cleanup_parse_runs_apply_with_no_matches_does_not_delete_or_commit(monkeypatch):
    module = _load_module()
    conn = FakeConn(parse_run_rows=[])
    monkeypatch.setattr(module, "connect", lambda _url: conn)

    result = module.cleanup_parse_runs(
        "HK",
        [],
        filing_id="missing",
        database_url_value="postgresql://db/siq",
        apply=True,
    )

    assert result["parse_run_ids"] == []
    assert result["post_cleanup_probe"]["cleaned"] is True
    assert conn.committed is False
    assert not any(sql.startswith("delete from") for sql, _params in conn.executed)


def test_cleanup_parse_runs_resolves_selector_filters(monkeypatch):
    module = _load_module()
    conn = FakeConn(parse_run_rows=[("parse-2024-a",), ("parse-2024-b",)])
    monkeypatch.setattr(module, "connect", lambda _url: conn)

    result = module.cleanup_parse_runs(
        "HK",
        [],
        company_id="HK:00005",
        filing_id="HK:00005:2024",
        older_than="2026-07-01T00:00:00+00:00",
        database_url_value="postgresql://db/siq",
    )

    assert result["parse_run_ids"] == ["parse-2024-a", "parse-2024-b"]
    selector_sql = next(sql for sql, _params in conn.executed if sql.startswith("select pr.parse_run_id"))
    assert "f.company_id = %s" in selector_sql
    assert "pr.filing_id = %s" in selector_sql
    assert "coalesce(pr.completed_at, pr.started_at) < %s" in selector_sql


def test_cleanup_parse_runs_refuses_market_wide_older_than_without_explicit_flag():
    module = _load_module()

    try:
        module.cleanup_parse_runs("HK", [], older_than="2026-07-01")
    except SystemExit as exc:
        assert "Refusing market-wide --older-than cleanup" in str(exc)
    else:
        raise AssertionError("expected broad older-than refusal")


def test_cleanup_parse_runs_allows_market_wide_older_than_with_explicit_flag(monkeypatch):
    module = _load_module()
    conn = FakeConn(parse_run_rows=[("parse-old-a",)])
    monkeypatch.setattr(module, "connect", lambda _url: conn)

    result = module.cleanup_parse_runs(
        "HK",
        [],
        older_than="2026-07-01",
        allow_market_wide_older_than=True,
        database_url_value="postgresql://db/siq",
    )

    assert result["parse_run_ids"] == ["parse-old-a"]
    assert result["selectors"]["allow_market_wide_older_than"] is True


def test_cleanup_parse_runs_allows_selector_with_no_matches(monkeypatch):
    module = _load_module()
    conn = FakeConn(parse_run_rows=[])
    monkeypatch.setattr(module, "connect", lambda _url: conn)

    result = module.cleanup_parse_runs("HK", [], filing_id="missing", database_url_value="postgresql://db/siq")

    assert result["parse_run_ids"] == []
    assert result["counts"] == {"parse_runs": 0}


def test_cleanup_parse_runs_requires_at_least_one_selector():
    module = _load_module()

    try:
        module.cleanup_parse_runs("HK", [])
    except SystemExit as exc:
        assert "At least one selector" in str(exc)
    else:
        raise AssertionError("expected selector requirement")


def test_cleanup_parse_runs_refuses_a_share():
    module = _load_module()

    for market in ("CN", "A", "ashare", "A-SHARE", "A SHARE"):
        try:
            module.cleanup_parse_runs(market, ["parse-a"])
        except SystemExit as exc:
            assert "Refusing to clean A-share" in str(exc)
        else:
            raise AssertionError(f"expected A-share cleanup refusal for {market}")
