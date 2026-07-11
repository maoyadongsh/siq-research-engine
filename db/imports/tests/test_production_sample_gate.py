import importlib.util
import json
from pathlib import Path


def _load_gate_module():
    source = Path(__file__).resolve().parents[1] / "backtests" / "production_sample_gate.py"
    spec = importlib.util.spec_from_file_location("production_sample_gate_under_test", source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_validate_production_sample_manifest_structure_without_local_files(tmp_path):
    gate = _load_gate_module()
    markets = {"HK": "siq_hk", "JP": "siq_jp"}
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": gate.PRODUCTION_SAMPLE_MANIFEST_SCHEMA_VERSION,
                "sample_goal_per_market": 2,
                "markets": {
                    "HK": ["data/hk/a/document_full.json", "data/hk/b/document_full.json"],
                    "JP": ["data/jp/a/document_full.json", "data/jp/b/document_full.json"],
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = gate.validate_production_sample_manifest(
        manifest_path,
        repo_root=tmp_path,
        market_databases=markets,
        require_existing=False,
    )

    assert result["passed"] is True
    assert result["market_counts"] == {"HK": 2, "JP": 2}
    assert result["existing_counts"] == {"HK": 0, "JP": 0}
    assert {sample["exists"] for sample in result["samples"]} == {None}


def test_validate_production_sample_manifest_requires_schema_version(tmp_path):
    gate = _load_gate_module()
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps({"schema_version": "wrong", "markets": {"HK": []}}, ensure_ascii=False),
        encoding="utf-8",
    )

    result = gate.validate_production_sample_manifest(
        manifest_path,
        repo_root=tmp_path,
        market_databases={"HK": "siq_hk"},
        require_existing=False,
    )

    assert result["passed"] is False
    assert "schema_version must be" in result["reason"]
    assert result["missing"]["__manifest__"] == ["schema_version='wrong'"]


def test_validate_production_sample_manifest_reports_disabled_and_missing_files(tmp_path):
    gate = _load_gate_module()

    disabled = gate.validate_production_sample_manifest(
        None,
        repo_root=tmp_path,
        market_databases={"HK": "siq_hk"},
    )
    missing = gate.validate_production_sample_manifest(
        tmp_path / "missing.json",
        repo_root=tmp_path,
        market_databases={"HK": "siq_hk"},
    )

    assert disabled["passed"] is False
    assert disabled["reason"] == "sample manifest disabled"
    assert missing["passed"] is False
    assert missing["missing"]["__manifest__"] == [str(tmp_path / "missing.json")]


def test_validate_production_sample_manifest_checks_existing_files_and_dedupes_paths(tmp_path):
    gate = _load_gate_module()
    existing = tmp_path / "data" / "hk" / "one" / "document_full.json"
    absolute = tmp_path / "absolute" / "document_full.json"
    existing.parent.mkdir(parents=True)
    absolute.parent.mkdir(parents=True)
    existing.write_text("{}", encoding="utf-8")
    absolute.write_text("{}", encoding="utf-8")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": gate.PRODUCTION_SAMPLE_MANIFEST_SCHEMA_VERSION,
                "sample_goal_per_market": 2,
                "markets": {
                    "HK": [
                        "data/hk/one/document_full.json",
                        "data/hk/one/document_full.json",
                        str(absolute),
                        "data/hk/missing/document_full.json",
                    ],
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = gate.validate_production_sample_manifest(
        manifest_path,
        repo_root=tmp_path,
        market_databases={"HK": "siq_hk"},
        require_existing=True,
    )

    assert result["passed"] is False
    assert result["market_counts"] == {"HK": 3}
    assert result["existing_counts"] == {"HK": 2}
    assert result["missing"] == {"HK": ["data/hk/missing/document_full.json"]}
    assert [sample["path"] for sample in result["samples"]] == [
        "data/hk/one/document_full.json",
        str(absolute),
        "data/hk/missing/document_full.json",
    ]


def test_validate_production_sample_manifest_rejects_non_list_market_and_bad_sample_goal(tmp_path):
    gate = _load_gate_module()
    non_list_path = tmp_path / "non_list.json"
    non_list_path.write_text(
        json.dumps(
            {
                "schema_version": gate.PRODUCTION_SAMPLE_MANIFEST_SCHEMA_VERSION,
                "markets": {"HK": "not-a-list"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    bad_goal_path = tmp_path / "bad_goal.json"
    bad_goal_path.write_text(
        json.dumps(
            {
                "schema_version": gate.PRODUCTION_SAMPLE_MANIFEST_SCHEMA_VERSION,
                "sample_goal_per_market": "many",
                "markets": {"HK": []},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    non_list = gate.validate_production_sample_manifest(
        non_list_path,
        repo_root=tmp_path,
        market_databases={"HK": "siq_hk"},
        require_existing=False,
    )
    bad_goal = gate.validate_production_sample_manifest(
        bad_goal_path,
        repo_root=tmp_path,
        market_databases={"HK": "siq_hk"},
        require_existing=False,
    )

    assert non_list["passed"] is False
    assert non_list["missing"] == {"HK": ["manifest markets.HK is not a list"]}
    assert bad_goal["passed"] is False
    assert bad_goal["reason"] == "sample manifest sample_goal_per_market must be a positive integer"
    assert bad_goal["missing"] == {"__manifest__": ["sample_goal_per_market='many'"]}


def test_production_sample_cases_from_manifest_uses_existing_samples_only(tmp_path):
    gate = _load_gate_module()
    first = tmp_path / "hk" / "one" / "document_full.json"
    second = tmp_path / "hk" / "two" / "document_full.json"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_text("{}", encoding="utf-8")
    second.write_text("{}", encoding="utf-8")
    manifest = {
        "samples": [
            {"market": "HK", "path": "hk/one/document_full.json", "resolved_path": str(first), "exists": True},
            {"market": "HK", "path": "hk/two/document_full.json", "resolved_path": str(second), "exists": True},
            {"market": "US", "path": "missing/document_full.json", "resolved_path": "missing", "exists": False},
            {"market": "CN", "path": "ignored/document_full.json", "resolved_path": "ignored", "exists": True},
        ]
    }

    cases = gate.production_sample_cases_from_manifest(manifest, market_databases={"HK": "siq_hk", "US": "siq_us"})

    assert cases == [
        {
            "case_id": "production_sample_hk_01",
            "market": "HK",
            "document_full_path": str(first),
            "production_sample_path": "hk/one/document_full.json",
        },
        {
            "case_id": "production_sample_hk_02",
            "market": "HK",
            "document_full_path": str(second),
            "production_sample_path": "hk/two/document_full.json",
        },
    ]


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, rows_by_sql, *, relation_available=True):
        self.rows_by_sql = rows_by_sql
        self.relation_available = relation_available
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        return _Rows(self.rows_by_sql.get(tuple(params or ()), []))

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_check_production_sample_db_coexistence_verifies_parse_runs_and_ignores_failed_results():
    gate = _load_gate_module()
    connections = []

    def connect(url):
        conn = _FakeConn({("parse-a", "parse-b"): [("parse-a",), ("parse-b",)]})
        connections.append((url, conn))
        return conn

    results = gate.check_production_sample_db_coexistence(
        [
            {"market": "HK", "passed": True, "parse_run_id": "parse-a"},
            {"market": "HK", "passed": True, "parse_run_id": "parse-b"},
            {"market": "HK", "passed": False, "parse_run_id": "ignored-failed"},
            {"market": "JP", "skipped": True, "parse_run_id": "ignored-skipped"},
            {"market": "CN", "passed": True, "parse_run_id": "ignored-market"},
        ],
        market_schemas={"HK": "pdf2md_hk", "JP": "edinet_jp"},
        database_url_for_market=lambda market, explicit: f"{explicit or 'postgresql://db'}/{market.lower()}",
        relation_exists=lambda conn, schema, relation: conn.relation_available and schema == "pdf2md_hk" and relation == "parse_runs",
        safe_sql_ident=lambda value: value,
        database_url="postgresql://example",
        connect=connect,
    )

    assert results == [
        {
            "market": "HK",
            "passed": True,
            "errors": [],
            "expected_parse_run_ids": ["parse-a", "parse-b"],
            "observed_parse_run_ids": ["parse-a", "parse-b"],
            "expected_count": 2,
            "observed_count": 2,
        }
    ]
    assert connections[0][0] == "postgresql://example/hk"
    assert "pdf2md_hk.parse_runs" in connections[0][1].executed[0][0]


def test_check_production_sample_db_coexistence_reports_duplicates_missing_and_missing_table():
    gate = _load_gate_module()

    def connect(_url):
        return _FakeConn({("parse-a", "parse-missing"): [("parse-a",)]}, relation_available=True)

    duplicate_and_missing = gate.check_production_sample_db_coexistence(
        [
            {"market": "HK", "passed": True, "parse_run_id": "parse-a"},
            {"market": "HK", "passed": True, "parse_run_id": "parse-a"},
            {"market": "HK", "passed": True, "parse_run_id": "parse-missing"},
        ],
        market_schemas={"HK": "pdf2md_hk"},
        database_url_for_market=lambda market, _explicit: market,
        relation_exists=lambda _conn, _schema, _relation: True,
        safe_sql_ident=lambda value: value,
        connect=connect,
    )

    missing_table = gate.check_production_sample_db_coexistence(
        [{"market": "HK", "passed": True, "parse_run_id": "parse-a"}],
        market_schemas={"HK": "pdf2md_hk"},
        database_url_for_market=lambda market, _explicit: market,
        relation_exists=lambda _conn, _schema, _relation: False,
        safe_sql_ident=lambda value: value,
        connect=lambda _url: _FakeConn({}),
    )

    assert duplicate_and_missing[0]["passed"] is False
    assert any("duplicate parse_run_id" in error for error in duplicate_and_missing[0]["errors"])
    assert any("parse-missing" in error for error in duplicate_and_missing[0]["errors"])
    assert missing_table[0]["passed"] is False
    assert "parse_runs table missing" in missing_table[0]["errors"]
