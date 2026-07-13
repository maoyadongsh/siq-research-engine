import importlib.util
from pathlib import Path


def _load_module():
    source = Path(__file__).resolve().parents[1] / "audit_market_postgres_fixture_contamination.py"
    spec = importlib.util.spec_from_file_location(
        "audit_market_postgres_fixture_contamination_under_test", source
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeResult:
    def __init__(self, *, one=None, rows=None):
        self.one = one
        self.rows = rows or []

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, database, schema, rows=(), *, read_only="on"):
        self.database = database
        self.schema = schema
        self.rows = list(rows)
        self.read_only = read_only
        self.rollback_called = False
        self.close_called = False
        self.fixture_query_count = 0

    def execute(self, sql, params=None):
        normalized = " ".join(sql.split()).lower()
        if normalized == "set transaction read only":
            return FakeResult()
        if "select current_database()" in normalized:
            return FakeResult(one=(self.database, self.read_only))
        if "select to_regclass" in normalized:
            return FakeResult(one=(f"{self.schema}.parse_runs",))
        if "from" in normalized and ".parse_runs" in normalized:
            self.fixture_query_count += 1
            assert params == ("eval_datasets/market_document_full_postgres/examples/",)
            return FakeResult(rows=self.rows)
        raise AssertionError(f"unexpected SQL: {sql}")

    def rollback(self):
        self.rollback_called = True

    def close(self):
        self.close_called = True


def _connections(module, row_counts, *, fixture_version="synthetic_identity_v2"):
    connections = {}
    catalog = module._fixture_catalog(module.FIXTURE_PATH_MARKER)
    for market, target in module.MARKET_TARGETS.items():
        fixtures = [
            (path, expected)
            for path, expected in sorted(catalog.items())
            if expected["market"] == market
        ]
        selected_fixtures = []
        for fixture_path, expected in fixtures[: row_counts.get(market, 0)]:
            version = next(
                item
                for item in expected["known_versions"]
                if item["fixture_version"] == fixture_version
            )
            selected_fixtures.append((fixture_path, version))
        rows = [
            (
                expected.get("parse_run_id") or f"fixture-{market.lower()}-{index}",
                expected["filing_id"],
                f"/repo/{fixture_path}",
                "success",
                None,
                {"document_full.json": expected["document_full_sha256"]},
                (
                    {"task": {"task_id": expected["task_id"]}}
                    if expected["task_id"]
                    else {}
                ),
                expected["company_id"],
            )
            for index, (fixture_path, expected) in enumerate(
                selected_fixtures
            )
        ]
        connections[market] = FakeConnection(target.database, target.schema, rows)
    return connections


def test_audit_reports_six_fixture_runs_and_rolls_back_every_market():
    module = _load_module()
    connections = _connections(module, {"HK": 1, "JP": 1, "KR": 1, "EU": 2, "US": 1})

    report = module.audit_fixture_contamination(
        connect=lambda market: connections[market],
        url_for_market=lambda _explicit, market: market,
    )

    assert report["passed"] is False
    assert report["read_only"] is True
    assert report["market_count"] == 5
    assert report["contaminated_run_count"] == 6
    assert report["exact_match_count"] == 6
    assert report["non_exact_match_count"] == 0
    assert report["cleanup_candidate_count"] == 6
    assert len(report["cleanup_plan"]) == 6
    assert report["error_count"] == 0
    assert report["base_commit"]
    assert isinstance(report["worktree_dirty"], bool)
    assert report["task_id"] == "T12"
    assert report["environment_profile"] == "local-five-market-postgres-read-only"
    assert report["result"] == "fail"
    assert report["duration_seconds"] >= 0
    assert report["failures"]
    assert report["artifact_checksums"]
    markdown = module.render_markdown(report)
    assert "- Base commit:" in markdown
    assert "- Worktree dirty:" in markdown
    assert "- Command:" in markdown
    assert "## Failures" in markdown
    assert "## Artifact Checksums" in markdown
    assert {result["market"]: result["contaminated_run_count"] for result in report["markets"]} == {
        "HK": 1,
        "JP": 1,
        "KR": 1,
        "EU": 2,
        "US": 1,
    }
    assert all(connection.fixture_query_count == 1 for connection in connections.values())
    assert all(connection.rollback_called for connection in connections.values())
    assert all(connection.close_called for connection in connections.values())
    assert all(
        not run["wiki_package_path"].startswith("/")
        for result in report["markets"]
        for run in result["contaminated_runs"]
    )
    us_run = next(
        run
        for result in report["markets"]
        if result["market"] == "US"
        for run in result["contaminated_runs"]
    )
    assert us_run["document_full_sha256_match"] is True
    assert us_run["task_id_match"] is True
    assert us_run["exact_match"] is True
    assert us_run["cleanup_candidate"] is True
    assert us_run["cleanup_assertions"]["task_id_must_be_absent"] is False


def test_audit_keeps_legacy_real_identity_fingerprints_as_exact_cleanup_candidates():
    module = _load_module()
    assert module.LEGACY_REAL_IDENTITY_FIXTURES["us_sec_document_full.json"]["company_id"] == (
        "US:0000320193"
    )
    connections = _connections(
        module,
        {"HK": 1, "JP": 1, "KR": 1, "EU": 2, "US": 1},
        fixture_version="legacy_real_identity_v1",
    )

    report = module.audit_fixture_contamination(
        connect=lambda market: connections[market],
        url_for_market=lambda _explicit, market: market,
    )

    assert report["contaminated_run_count"] == 6
    assert report["exact_match_count"] == 6
    assert report["cleanup_candidate_count"] == 6
    assert {
        run["fixture_version"]
        for result in report["markets"]
        for run in result["contaminated_runs"]
    } == {"legacy_real_identity_v1"}
    for entry in report["cleanup_plan"]:
        assert entry["execute"] is False
        assert set(entry["assertions"]) == {
            "database",
            "schema",
            "parse_run_id",
            "company_id",
            "filing_id",
            "wiki_package_path",
            "document_full_sha256",
            "task_id",
            "task_id_must_be_absent",
        }
    us_cleanup = next(entry for entry in report["cleanup_plan"] if entry["market"] == "US")
    assert us_cleanup["assertions"]["company_id"] == "US:0000320193"
    assert us_cleanup["assertions"]["task_id"] is None
    assert us_cleanup["assertions"]["task_id_must_be_absent"] is True


def test_audit_refuses_cleanup_candidate_when_legacy_parse_run_id_drifts():
    module = _load_module()
    connections = _connections(
        module,
        {"HK": 1, "JP": 1, "KR": 1, "EU": 2, "US": 1},
        fixture_version="legacy_real_identity_v1",
    )
    hk_row = list(connections["HK"].rows[0])
    hk_row[0] = "parse_not_the_audited_fixture"
    connections["HK"].rows[0] = tuple(hk_row)

    report = module.audit_fixture_contamination(
        connect=lambda market: connections[market],
        url_for_market=lambda _explicit, market: market,
    )

    assert report["exact_match_count"] == 5
    assert report["cleanup_candidate_count"] == 5
    hk = next(result for result in report["markets"] if result["market"] == "HK")
    assert hk["contaminated_runs"][0]["parse_run_id_match"] is False
    assert hk["contaminated_runs"][0]["cleanup_candidate"] is False
    assert hk["contaminated_runs"][0]["cleanup_action"] == "manual_assessment_required"


def test_audit_passes_only_when_all_five_fixed_databases_are_clean():
    module = _load_module()
    connections = _connections(module, {})

    report = module.audit_fixture_contamination(
        connect=lambda market: connections[market],
        url_for_market=lambda _explicit, market: market,
    )

    assert report["passed"] is True
    assert report["contaminated_run_count"] == 0
    assert report["error_count"] == 0
    assert all(result["passed"] for result in report["markets"])


def test_audit_fails_closed_before_querying_wrong_database():
    module = _load_module()
    connections = _connections(module, {})
    connections["HK"].database = "siq"

    report = module.audit_fixture_contamination(
        connect=lambda market: connections[market],
        url_for_market=lambda _explicit, market: market,
    )

    hk = next(result for result in report["markets"] if result["market"] == "HK")
    assert report["passed"] is False
    assert "database identity mismatch" in hk["errors"][0]
    assert connections["HK"].fixture_query_count == 0
    assert connections["HK"].rollback_called is True


def test_audit_rewrites_explicit_connection_to_each_fixed_market_database():
    module = _load_module()
    connections = _connections(module, {})
    url_calls = []

    def url_for_market(explicit, market):
        url_calls.append((explicit, market))
        return market

    report = module.audit_fixture_contamination(
        connect=lambda market: connections[market],
        url_for_market=url_for_market,
        explicit_database_url="postgresql://redacted.invalid/source",
    )

    assert report["passed"] is True
    assert url_calls == [
        ("postgresql://redacted.invalid/source", market)
        for market in module.MARKET_TARGETS
    ]


def test_audit_redacts_connection_dsn_from_errors():
    module = _load_module()

    report = module.audit_fixture_contamination(
        connect=lambda _url: (_ for _ in ()).throw(
            RuntimeError("failed postgresql://user:secret@db.example/siq_hk")
        ),
        url_for_market=lambda _explicit, market: market,
    )

    serialized = module.json.dumps(report)
    assert "user:secret" not in serialized
    assert "[redacted-dsn]" in serialized


def test_main_records_portable_markdown_checksum_key(tmp_path, monkeypatch):
    module = _load_module()
    report = {
        "generated_at": "2026-07-13T00:00:00Z",
        "base_commit": "a" * 40,
        "worktree_dirty": True,
        "worktree_summary": {"changed_path_count": 1},
        "task_id": "T12",
        "environment_profile": "test",
        "command": "python audit.py",
        "result": "pass",
        "duration_seconds": 0.1,
        "failures": [],
        "artifact_checksums": {},
        "passed": True,
        "read_only": True,
        "fixture_path_marker": module.FIXTURE_PATH_MARKER,
        "contaminated_run_count": 0,
        "exact_match_count": 0,
        "non_exact_match_count": 0,
        "cleanup_candidate_count": 0,
        "error_count": 0,
        "markets": [],
    }
    monkeypatch.setattr(module, "audit_fixture_contamination", lambda: report)
    json_output = tmp_path / "audit.json"
    markdown_output = tmp_path / "audit.md"

    exit_code = module.main(
        [
            "--json-output",
            str(json_output),
            "--markdown-output",
            str(markdown_output),
        ]
    )

    payload = module.json.loads(json_output.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert set(payload["artifact_checksums"]) == {"<external>/audit.md"}
    assert str(tmp_path) not in module.json.dumps(payload)
