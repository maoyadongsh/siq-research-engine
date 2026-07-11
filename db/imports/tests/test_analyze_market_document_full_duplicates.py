import importlib.util
import sys
from pathlib import Path


def _load_module():
    imports_dir = Path(__file__).resolve().parents[1]
    if str(imports_dir) not in sys.path:
        sys.path.insert(0, str(imports_dir))
    path = imports_dir / "analyze_market_document_full_duplicates.py"
    spec = importlib.util.spec_from_file_location("analyze_market_document_full_duplicates", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeCursor:
    def __init__(self, rows=None):
        self._rows = rows or []

    def fetchall(self):
        return self._rows


class FakeConn:
    def __init__(self, rows):
        self.rows = rows
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def execute(self, sql, params=None):
        self.executed.append((" ".join(str(sql).split()), params))
        return FakeCursor(self.rows)


def _row(
    parse_run_id,
    filing_id,
    *,
    company_id="HK:00005",
    ticker="00005",
    report_type="annual",
    fiscal_year=2025,
    status="success",
    started_at="2026-07-02T00:00:00+00:00",
    completed_at="2026-07-02T00:01:00+00:00",
    wiki_package_path="data/wiki/hk/companies/00005/reports/2025-annual",
    package_path="data/pdf-parser/results/task/document_full.json",
    document_full_sha256="sha-a",
):
    return (
        parse_run_id,
        filing_id,
        company_id,
        ticker,
        report_type,
        fiscal_year,
        status,
        started_at,
        completed_at,
        wiki_package_path,
        package_path,
        document_full_sha256,
    )


def test_analyze_duplicate_parse_runs_marks_older_runs_and_cleanup_command(monkeypatch):
    module = _load_module()
    conn = FakeConn(
        [
            _row("parse-new", "HK:00005:2025", document_full_sha256="sha-b"),
            _row("parse-old", "HK:00005:2025", document_full_sha256="sha-a"),
            _row("parse-only", "HK:00011:2025", company_id="HK:00011", ticker="00011"),
        ]
    )
    monkeypatch.setattr(module, "connect", lambda _url: conn)

    result = module.analyze_duplicate_parse_runs("HK", database_url_value="postgresql://db/siq")

    assert result["market"] == "HK"
    assert result["duplicate_group_count"] == 1
    assert result["candidate_obsolete_parse_run_count"] == 1
    group = result["duplicate_groups"][0]
    assert group["filing_id"] == "HK:00005:2025"
    assert group["latest_parse_run_id"] == "parse-new"
    assert group["candidate_obsolete_parse_run_ids"] == ["parse-old"]
    assert group["reason"] == "same_filing_multiple_parse_runs_different_document_hash"
    assert group["cleanup_dry_run_argv"] == [
        "python3",
        "db/imports/cleanup_market_document_full_parse_runs.py",
        "--market",
        "HK",
        "--parse-run-id",
        "parse-old",
    ]


def test_analyze_duplicate_parse_runs_applies_company_and_filing_filters(monkeypatch):
    module = _load_module()
    conn = FakeConn([_row("parse-new", "HK:00005:2025"), _row("parse-old", "HK:00005:2025")])
    monkeypatch.setattr(module, "connect", lambda _url: conn)

    module.analyze_duplicate_parse_runs(
        "HK",
        company_id="HK:00005",
        filing_id="HK:00005:2025",
        database_url_value="postgresql://db/siq",
    )

    sql, params = conn.executed[0]
    assert "f.company_id = %s" in sql
    assert "pr.filing_id = %s" in sql
    assert params == ("HK:00005", "HK:00005:2025")


def test_analyze_duplicate_parse_runs_refuses_a_share_aliases():
    module = _load_module()

    for market in ("CN", "A", "ashare", "A-SHARE", "A SHARE"):
        try:
            module.analyze_duplicate_parse_runs(market)
        except SystemExit as exc:
            assert "Refusing to analyze A-share" in str(exc)
        else:
            raise AssertionError(f"expected A-share analysis refusal for {market}")
