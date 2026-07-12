import importlib.util
import re
import sys
import types
from pathlib import Path

import pytest

EXPECTED_RUNTIME_DDL = {
    "HK": ("pdf2md_hk", "siq_hk", "020_create_pdf2md_hk_schema.sql"),
    "JP": ("edinet_jp", "siq_jp", "030_create_edinet_jp_schema.sql"),
    "KR": ("dart_kr", "siq_kr", "040_create_dart_kr_schema.sql"),
    "EU": ("eu_ifrs", "siq_eu", "050_create_eu_ifrs_schema.sql"),
    "US": ("sec_us", "siq_us", "010_create_sec_us_schema.sql"),
}
LEGACY_MARKET_ALIASES = {"CN", "A", "ASHARE", "A_SHARE"}


def _load_contract():
    imports_dir = Path(__file__).resolve().parents[1]
    if str(imports_dir) not in sys.path:
        sys.path.insert(0, str(imports_dir))
    source = imports_dir / "market_ingestion_contract.py"
    spec = importlib.util.spec_from_file_location("market_ingestion_contract_under_test", source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_runtime_writer():
    imports_dir = Path(__file__).resolve().parents[1]

    def fake_connect(*_args, **_kwargs):
        return None

    def fake_jsonb(value):
        return value

    fake_psycopg = types.ModuleType("psycopg")
    fake_psycopg.connect = fake_connect
    fake_psycopg_types = types.ModuleType("psycopg.types")
    fake_psycopg_json = types.ModuleType("psycopg.types.json")
    fake_psycopg_json.Jsonb = fake_jsonb
    fake_modules = {
        "psycopg": fake_psycopg,
        "psycopg.types": fake_psycopg_types,
        "psycopg.types.json": fake_psycopg_json,
    }
    previous = {name: sys.modules.get(name) for name in fake_modules}
    sys.modules.update(fake_modules)
    try:
        source = imports_dir / "market_document_full_writer.py"
        spec = importlib.util.spec_from_file_location("market_document_full_writer_authority_under_test", source)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        for name, module in previous.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


def _ddl_relations(ddl: str, schema: str) -> dict[str, dict[str, str]]:
    relations: dict[str, dict[str, str]] = {}
    patterns = {
        "table": rf"create\s+table\s+(?:if\s+not\s+exists\s+)?{re.escape(schema)}\.(?P<name>\w+)\s*\((?P<body>.*?)\);",
        "view": rf"create\s+(?:or\s+replace\s+)?view\s+{re.escape(schema)}\.(?P<name>\w+)\s+as\s+(?P<body>.*?);",
    }
    for kind, pattern in patterns.items():
        for match in re.finditer(pattern, ddl, flags=re.IGNORECASE | re.DOTALL):
            relations[match.group("name").lower()] = {"kind": kind, "body": match.group("body").lower()}
    return relations


def _table_columns(table_body: str) -> set[str]:
    columns: set[str] = set()
    constraint_prefixes = {"check", "constraint", "exclude", "foreign", "primary", "unique"}
    for raw_line in table_body.splitlines():
        line = raw_line.strip().lstrip(",")
        match = re.match(r'"?(?P<name>[a-zA-Z_]\w*)"?\s+', line)
        if match and match.group("name").lower() not in constraint_prefixes:
            columns.add(match.group("name").lower())
    return columns


class FakeConn:
    def __init__(self, database="siq_hk"):
        self.database = database
        self.executed = []

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def execute(self, sql):
        self.executed.append(sql)
        return self

    def fetchone(self):
        return (self.database,)


def test_generated_reset_ddl_is_dry_run_by_default():
    contract = _load_contract()
    conn = FakeConn()

    with pytest.raises(SystemExit) as exc:
        contract.run_market_ddl(conn, "HK")

    message = str(exc.value)
    assert "checked-in db/ddl/*.sql" in message
    assert "DROP SCHEMA CASCADE" in message
    assert conn.executed == []


def test_generated_reset_ddl_requires_explicit_unsafe_flag():
    contract = _load_contract()
    conn = FakeConn()

    contract.run_market_ddl(conn, "HK", allow_unsafe_reset=True)

    executed = "\n".join(conn.executed).lower()
    assert "select current_database()" in executed
    assert "drop schema if exists pdf2md_hk cascade" in executed


def test_checked_in_runtime_ddl_authority_has_required_market_schema_contracts():
    writer = _load_runtime_writer()

    assert set(writer.MARKET_CONFIG) == set(EXPECTED_RUNTIME_DDL)
    assert LEGACY_MARKET_ALIASES.isdisjoint(writer.MARKET_CONFIG)

    for market, (schema, database, ddl_name) in EXPECTED_RUNTIME_DDL.items():
        config = writer.MARKET_CONFIG[market]
        ddl_path = Path(config["ddl"])
        assert config["schema"] == schema
        assert config["database"] == database
        assert ddl_path.name == ddl_name
        assert ddl_path.is_file(), f"{market} runtime DDL authority missing: {ddl_path}"

        ddl = ddl_path.read_text(encoding="utf-8")
        assert not re.search(r"\bdrop\s+schema\b", ddl, flags=re.IGNORECASE), (
            f"{market} runtime authority must remain additive and must not execute DROP SCHEMA CASCADE"
        )
        relations = _ddl_relations(ddl, schema)
        assert relations.get("parse_runs", {}).get("kind") == "table", f"{market} parse_runs table missing"
        assert relations.get("v_latest_parse_runs", {}).get("kind") == "view", (
            f"{market} v_latest_parse_runs view missing"
        )
        assert relations.get("v_agent_financial_facts", {}).get("kind") == "view", (
            f"{market} v_agent_financial_facts view missing"
        )

        parse_run_columns = _table_columns(relations["parse_runs"]["body"])
        assert {
            "parse_run_id",
            "filing_id",
            "parser_version",
            "rules_version",
            "wiki_package_path",
            "status",
            "completed_at",
        } <= parse_run_columns, f"{market} parse_runs critical columns drifted"

        latest_view = relations["v_latest_parse_runs"]["body"]
        for token in ("parse_run_id", "filing_id", "status", "wiki_package_path"):
            assert token in latest_view, f"{market} latest parse-run view lost {token}"
        assert "status in ('pass', 'warning', 'completed', 'success')" in latest_view, (
            f"{market} latest parse-run view lost latest-successful status semantics"
        )

        agent_view = relations["v_agent_financial_facts"]["body"]
        for token in (
            "company_id",
            "filing_id",
            "parse_run_id",
            "statement_type",
            "period_key",
            "value",
            "raw_value",
            "evidence_id",
        ):
            assert token in agent_view, f"{market} agent fact view lost {token}"
        assert f"join {schema}.v_latest_parse_runs" in agent_view, (
            f"{market} agent fact view must stay scoped to latest-successful parse runs"
        )
