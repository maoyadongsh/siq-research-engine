import importlib.util
import re
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path


def _load_module(name: str, rel: str):
    imports_dir = Path(__file__).resolve().parents[1]
    if str(imports_dir) not in sys.path:
        sys.path.insert(0, str(imports_dir))
    spec = importlib.util.find_spec(rel.removesuffix(".py").replace("/", "."))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _ddl_parse_run_tables(ddl_path: Path, schema: str) -> set[str]:
    ddl = ddl_path.read_text(encoding="utf-8")
    table_bodies = {
        match.group("table").split(".")[-1]: match.group("body")
        for match in re.finditer(
            rf"create\s+table\s+if\s+not\s+exists\s+{re.escape(schema)}\.(?P<table>\w+)\s*\((?P<body>.*?)\);",
            ddl,
            flags=re.IGNORECASE | re.DOTALL,
        )
    }

    parse_run_tables = set()
    changed = True
    while changed:
        changed = False
        for table, body in table_bodies.items():
            like_sources = {source.split(".")[-1] for source in re.findall(r"\blike\s+([\w.]+)", body, flags=re.IGNORECASE)}
            has_parse_run_id = bool(re.search(r"\bparse_run_id\b", body, flags=re.IGNORECASE))
            inherits_parse_run_id = any(source in parse_run_tables for source in like_sources)
            if table not in parse_run_tables and (has_parse_run_id or inherits_parse_run_id):
                parse_run_tables.add(table)
                changed = True
    return parse_run_tables


class FakeCursor:
    def __init__(self, rows=None):
        self._rows = rows or []

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class FakeConn:
    def __init__(self, columns, database="siq_hk", existing_company_by_cik=None):
        self.columns = columns
        self.database = database
        self.existing_company_by_cik = existing_company_by_cik or {}
        self.executed = []

    def cursor(self):
        return self

    def transaction(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def fetchone(self):
        return (self.database,)

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        if "current_database()" in sql:
            return FakeCursor([(self.database,)])
        if "information_schema.tables" in sql:
            table = params[1]
            return FakeCursor([(1,)] if table in self.columns else [])
        if "information_schema.columns" in sql:
            if "column_name = 'parse_run_id'" in sql:
                return FakeCursor(
                    [(table,) for table, columns in sorted(self.columns.items()) if "parse_run_id" in columns]
                )
            table = params[1]
            return FakeCursor([(column,) for column in self.columns.get(table, set())])
        if "from sec_us.companies" in sql and "where cik in" in sql:
            for cik in params or ():
                company_id = self.existing_company_by_cik.get(cik)
                if company_id:
                    return FakeCursor([(company_id,)])
            return FakeCursor()
        return FakeCursor()


def test_writer_maps_enriched_rows_to_derivative_layer_contract():
    writer_module = _load_module("market_document_full_writer", "market_document_full_writer.py")
    base = _load_module("market_document_full_base", "market_document_full_rules/base.py")
    columns = {
        "financial_items_enriched": {
            "enriched_id",
            "source_table",
            "source_uid",
            "filing_id",
            "parse_run_id",
            "company_id",
            "ticker",
            "market",
            "item_name_raw",
            "period_key_raw",
            "canonical_label",
            "canonical_source",
            "canonical_rule_id",
            "value_extracted",
            "unit_raw",
            "unit_rule_id",
            "period_end_date",
            "raw_item",
        },
    }
    conn = FakeConn(columns, database="siq_hk")
    writer = writer_module.MarketDocumentFullWriter(conn, market="HK")
    rows = base.MarketDocumentFullRows(
        company={"company_id": "HK:00700", "ticker": "00700"},
        filing={"filing_id": "HK:00700:2025"},
        parse_run={"parse_run_id": "parse-1"},
        enriched_items=[
            {
                "enriched_id": "enriched-1",
                "item_uid": "item-1",
                "statement_type": "income_statement",
                "item_name": "收益",
                "period_key": "2025",
                "period_end": "2025-12-31",
                "canonical_label": "revenue",
                "canonical_scope": "common_core",
                "value_extracted": "100",
                "unit_raw": "HKD million",
                "raw": {"item": "raw"},
            }
        ],
    )

    writer._insert_normalization(rows)

    insert_sql, params = conn.executed[-1]
    assert "insert into pdf2md_hk.financial_items_enriched" in insert_sql
    payload = dict(zip(insert_sql.split("(", 1)[1].split(")", 1)[0].split(", "), params))
    assert payload["source_table"] == "financial_statement_items"
    assert payload["source_uid"] == "item-1"
    assert payload["item_name_raw"] == "收益"
    assert payload["period_key_raw"] == "2025"
    assert payload["market"] == "HK"


def test_writer_persists_sections_and_xbrl_raw_contract():
    writer_module = _load_module("market_document_full_writer", "market_document_full_writer.py")
    base = _load_module("market_document_full_base", "market_document_full_rules/base.py")
    columns = {
        "companies": {"company_id", "ticker"},
        "filings": {"filing_id", "company_id", "ticker"},
        "parse_runs": {"parse_run_id", "filing_id", "parser_version", "rules_version", "wiki_package_path", "status"},
        "filing_sections": {"parse_run_id", "filing_id", "section_id", "section_title", "html_anchor", "raw"},
        "xbrl_contexts": {"parse_run_id", "filing_id", "context_ref", "period_start", "period_end", "dimensions", "raw"},
        "xbrl_units": {"parse_run_id", "filing_id", "unit_ref", "unit", "raw"},
        "xbrl_facts_raw": {
            "fact_id",
            "parse_run_id",
            "filing_id",
            "concept",
            "label",
            "value_text",
            "value_numeric",
            "unit_ref",
            "context_ref",
            "dimensions",
            "html_anchor",
            "raw",
        },
        "financial_statements": {"parse_run_id"},
    }
    conn = FakeConn(columns, database="siq_us")
    writer = writer_module.MarketDocumentFullWriter(conn, market="US")
    rows = base.MarketDocumentFullRows(
        company={"company_id": "US:CIK0000320193", "ticker": "AAPL"},
        filing={"filing_id": "US:f1", "company_id": "US:CIK0000320193", "ticker": "AAPL"},
        parse_run={
            "parse_run_id": "parse-us-1",
            "filing_id": "US:f1",
            "parser_version": "sec",
            "rules_version": "document_full_v1",
            "wiki_package_path": "/tmp/document_full.json",
            "status": "pass",
        },
        sections=[{"section_id": "financials", "section_title": "Financial Statements", "html_anchor": "#fs", "raw": {}}],
        xbrl_contexts=[{"context_ref": "c-2025", "period_start": "2024-09-29", "period_end": "2025-09-27", "dimensions": {}, "raw": {}}],
        xbrl_units=[{"unit_ref": "usd", "unit": "iso4217:USD", "raw": {}}],
        xbrl_facts_raw=[
            {
                "fact_id": "fact-revenue",
                "concept": "us-gaap:Revenues",
                "label": "Net sales",
                "value_text": "416,161",
                "value_numeric": "416161000000",
                "unit_ref": "usd",
                "context_ref": "c-2025",
                "dimensions": {},
                "html_anchor": "f-revenue",
                "raw": {},
            }
        ],
    )

    writer.import_rows(rows)

    sqls = [sql for sql, _params in conn.executed]
    assert any("insert into sec_us.filing_sections" in sql for sql in sqls)
    assert any("insert into sec_us.xbrl_contexts" in sql for sql in sqls)
    assert any("insert into sec_us.xbrl_units" in sql for sql in sqls)
    assert any("insert into sec_us.xbrl_facts_raw" in sql for sql in sqls)
    assert any("delete from sec_us.financial_statements where parse_run_id" in sql for sql in sqls)


def test_writer_reuses_existing_us_company_id_for_cik_unique_key():
    writer_module = _load_module("market_document_full_writer_reuse_us_cik", "market_document_full_writer.py")
    base = _load_module("market_document_full_base_reuse_us_cik", "market_document_full_rules/base.py")
    columns = {
        "companies": {"company_id", "ticker", "cik"},
        "filings": {"filing_id", "company_id", "ticker", "accession_number"},
        "parse_runs": {"parse_run_id", "filing_id", "parser_version", "rules_version", "wiki_package_path", "status"},
        "financial_statements": {"parse_run_id"},
    }
    conn = FakeConn(columns, database="siq_us", existing_company_by_cik={"0001341439": "US:0001341439"})
    writer = writer_module.MarketDocumentFullWriter(conn, market="US")
    rows = base.MarketDocumentFullRows(
        company={"company_id": "US:CIK0001341439", "ticker": "ORCL", "cik": "0001341439"},
        filing={"filing_id": "US:f1", "company_id": "US:CIK0001341439", "ticker": "ORCL", "accession_number": "0001193125-26-277521"},
        parse_run={
            "parse_run_id": "parse-us-orcl",
            "filing_id": "US:f1",
            "parser_version": "sec",
            "rules_version": "document_full_v1",
            "wiki_package_path": "/tmp/document_full.json",
            "status": "pass",
        },
    )

    writer.import_rows(rows)

    assert rows.company["company_id"] == "US:0001341439"
    assert rows.filing["company_id"] == "US:0001341439"
    company_insert = next((item for item in conn.executed if "insert into sec_us.companies" in item[0]), None)
    filing_insert = next((item for item in conn.executed if "insert into sec_us.filings" in item[0]), None)
    assert company_insert is not None
    assert filing_insert is not None
    assert "on conflict (company_id) do update" in company_insert[0]
    assert "US:0001341439" in company_insert[1]
    assert "US:0001341439" in filing_insert[1]


def test_writer_compacts_statement_item_raw_payloads():
    writer_module = _load_module("market_document_full_writer_compact_statement_raw", "market_document_full_writer.py")
    base = _load_module("market_document_full_base_compact_statement_raw", "market_document_full_rules/base.py")
    columns = {
        "companies": {"company_id", "ticker"},
        "filings": {"filing_id", "company_id", "ticker"},
        "parse_runs": {"parse_run_id", "filing_id"},
        "financial_statement_items": {
            "item_uid",
            "filing_id",
            "parse_run_id",
            "ticker",
            "statement_type",
            "item_name",
            "canonical_name",
            "period_key",
            "value",
            "raw",
        },
    }
    conn = FakeConn(columns, database="siq_us")
    writer = writer_module.MarketDocumentFullWriter(conn, market="US")
    huge_text = "x" * 100_000
    rows = base.MarketDocumentFullRows(
        company={"company_id": "US:CIK0000320193", "ticker": "AAPL"},
        filing={"filing_id": "US:f1", "company_id": "US:CIK0000320193", "ticker": "AAPL"},
        parse_run={"parse_run_id": "parse-us-compact", "filing_id": "US:f1"},
        statement_items=[
            {
                "item_uid": "item-revenue",
                "ticker": "AAPL",
                "statement_type": "income_statement",
                "item_name": "Net sales",
                "canonical_name": "revenue",
                "period_key": "2025-09-27",
                "value": "416161000000",
                "raw": {
                    "source": "xbrl",
                    "huge_text": huge_text,
                    "item": {
                        "concept": "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
                        "context_ref": "c-2025",
                        "full_payload": huge_text,
                    },
                },
            }
        ],
    )

    writer.import_rows(rows)

    statement_insert = next(
        (sql, params)
        for sql, params in conn.executed
        if "insert into sec_us.financial_statement_items" in sql
    )
    sql, params = statement_insert
    payload = dict(zip(sql.split("(", 1)[1].split(")", 1)[0].split(", "), params))
    raw = payload["raw"].obj
    assert raw["raw"]["source"] == "xbrl"
    assert raw["raw"]["item"]["concept"] == "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"
    assert "huge_text" not in raw["raw"]
    assert "full_payload" not in raw["raw"]["item"]


def test_writer_json_value_sanitizes_decimal_and_dates():
    writer_module = _load_module("market_document_full_writer_json_safe", "market_document_full_writer.py")

    wrapped = writer_module.json_value({"value": Decimal("1.23"), "date": date(2025, 12, 31), "items": (Decimal("4"),)})

    assert wrapped.obj == {"value": "1.23", "date": "2025-12-31", "items": ["4"]}


def test_writer_dynamically_deletes_future_parse_run_child_tables():
    writer_module = _load_module("market_document_full_writer_dynamic_delete", "market_document_full_writer.py")
    columns = {
        "financial_statements": {"parse_run_id", "statement_id"},
        "future_metric_snapshots": {"parse_run_id", "snapshot_id", "raw"},
        "parse_runs": {"parse_run_id"},
    }
    conn = FakeConn(columns, database="siq_hk")
    writer = writer_module.MarketDocumentFullWriter(conn, market="HK")

    writer._delete_run_rows("parse-future")

    sqls = [sql for sql, _params in conn.executed]
    assert any("delete from pdf2md_hk.financial_statements where parse_run_id" in sql for sql in sqls)
    assert any("delete from pdf2md_hk.future_metric_snapshots where parse_run_id" in sql for sql in sqls)
    assert not any("delete from pdf2md_hk.parse_runs where parse_run_id" in sql for sql in sqls)


def test_delete_run_rows_covers_every_parse_run_child_table_in_market_ddls():
    writer_module = _load_module("market_document_full_writer_ddl_delete_coverage", "market_document_full_writer.py")

    for market, config in writer_module.MARKET_CONFIG.items():
        schema = config["schema"]
        parse_run_tables = _ddl_parse_run_tables(Path(config["ddl"]), schema)
        child_tables = parse_run_tables - {"parse_runs"}
        columns = {table: {"parse_run_id"} for table in parse_run_tables}
        conn = FakeConn(columns, database=config["database"])
        writer = writer_module.MarketDocumentFullWriter(conn, market=market)

        writer._delete_run_rows(f"parse-ddl-{market.lower()}")

        delete_pattern = re.compile(rf"delete from {re.escape(schema)}\.(?P<table>\w+) where parse_run_id")
        deleted_tables = {
            match.group("table")
            for sql, params in conn.executed
            if (match := delete_pattern.search(sql)) and params == (f"parse-ddl-{market.lower()}",)
        }
        assert "parse_runs" in parse_run_tables, f"{market} DDL should define parse_runs"
        assert "parse_runs" not in deleted_tables, f"{market} must retain parse_runs during idempotent child cleanup"
        assert deleted_tables == child_tables, f"{market} delete coverage mismatch"


def test_market_agent_fact_views_are_scoped_to_latest_successful_parse_runs():
    writer_module = _load_module("market_document_full_writer_ddl_latest_agent", "market_document_full_writer.py")
    contract_module = _load_module("market_ingestion_contract_latest_agent", "market_ingestion_contract.py")
    successful_status_filter = "status in ('pass', 'warning', 'completed', 'success')"

    template_sql = contract_module.build_market_schema_sql("HK").lower()
    assert successful_status_filter in template_sql
    assert "join pdf2md_hk.v_latest_parse_runs pr on pr.parse_run_id = e.parse_run_id" in template_sql
    assert "join pdf2md_hk.parse_runs pr on pr.parse_run_id = e.parse_run_id" not in template_sql

    for market, config in writer_module.MARKET_CONFIG.items():
        schema = config["schema"]
        ddl = Path(config["ddl"]).read_text(encoding="utf-8").lower()

        assert f"create or replace view {schema}.v_latest_parse_runs" in ddl, f"{market} latest view missing"
        assert successful_status_filter in ddl, f"{market} latest view should ignore failed parse runs"
        assert (
            f"join {schema}.v_latest_parse_runs pr on pr.parse_run_id = fsi.parse_run_id" in ddl
        ), f"{market} agent view should use latest parse runs for normalized facts"
        assert (
            f"join {schema}.parse_runs pr on pr.parse_run_id = fsi.parse_run_id" not in ddl
        ), f"{market} agent view must not expose obsolete normalized parse runs"
        if market == "US":
            assert "join sec_us.v_latest_parse_runs pr on pr.parse_run_id = x.parse_run_id" in ddl
            assert "join sec_us.parse_runs pr on pr.parse_run_id = x.parse_run_id" not in ddl


def test_writer_maps_stable_xbrl_fact_id_to_eu_raw_fact_primary_key():
    writer_module = _load_module("market_document_full_writer", "market_document_full_writer.py")
    base = _load_module("market_document_full_base", "market_document_full_rules/base.py")
    columns = {
        "xbrl_facts_raw": {
            "raw_fact_id",
            "fact_id",
            "parse_run_id",
            "filing_id",
            "concept",
            "value_text",
            "value_numeric",
            "unit_ref",
            "context_ref",
            "raw",
        },
    }
    conn = FakeConn(columns, database="siq_eu")
    writer = writer_module.MarketDocumentFullWriter(conn, market="EU")
    rows = base.MarketDocumentFullRows(
        company={"company_id": "EU:NL:ASML:NL0010273215", "ticker": "ASML", "country": "NL"},
        filing={"filing_id": "EU:f1"},
        parse_run={"parse_run_id": "parse-eu"},
        xbrl_facts_raw=[
            {
                "fact_id": "fact_stable_parse_scoped",
                "raw_fact_id": "source-fact-1",
                "concept": "ifrs-full:Revenue",
                "value_text": "100",
                "value_numeric": "100",
                "unit_ref": "eur",
                "context_ref": "fy2025",
                "raw": {},
            }
        ],
    )

    writer._insert_xbrl_rows(rows)

    insert_sql, params = next(item for item in conn.executed if "insert into eu_ifrs.xbrl_facts_raw" in item[0])
    columns_written = [column.strip() for column in insert_sql.split("(", 1)[1].split(")", 1)[0].split(",")]
    payload = dict(zip(columns_written, params))
    assert payload["raw_fact_id"] == "fact_stable_parse_scoped"
    assert payload["fact_id"] == "source-fact-1"


def test_writer_routes_us_html_tables_and_uses_sec_collection():
    writer_module = _load_module("market_document_full_writer", "market_document_full_writer.py")
    base = _load_module("market_document_full_base", "market_document_full_rules/base.py")
    columns = {
        "html_tables": {
            "parse_run_id",
            "filing_id",
            "table_id",
            "section_id",
            "title",
            "row_count",
            "column_count",
            "html_anchor",
            "xpath",
            "raw",
        },
        "retrieval_chunks": {
            "chunk_uid",
            "filing_id",
            "parse_run_id",
            "ticker",
            "collection_name",
            "doc_type",
            "canonical_name",
            "period_key",
            "text",
            "text_hash",
        },
    }
    conn = FakeConn(columns, database="siq_us")
    writer = writer_module.MarketDocumentFullWriter(conn, market="US")
    rows = base.MarketDocumentFullRows(
        company={"company_id": "US:CIK0000320193", "ticker": "AAPL"},
        filing={"filing_id": "US:f1", "company_id": "US:CIK0000320193", "ticker": "AAPL"},
        parse_run={"parse_run_id": "parse-us-1", "filing_id": "US:f1"},
        tables=[
            {
                "table_id": "table-1",
                "section_id": "financials",
                "title": "Statements",
                "row_count": 2,
                "column_count": 3,
                "xpath": "//table[1]",
                "raw": {},
            }
        ],
        chunks=[
            {
                "chunk_uid": "chunk-1",
                "doc_type": "financial_fact",
                "canonical_name": "revenue",
                "period_key": "FY2025",
                "text": "Revenue 1",
                "text_hash": "hash-1",
            }
        ],
    )

    writer.import_rows(rows)

    html_insert = next((item for item in conn.executed if "insert into sec_us.html_tables" in item[0]), None)
    chunk_insert = next((item for item in conn.executed if "insert into sec_us.retrieval_chunks" in item[0]), None)
    assert html_insert is not None
    assert chunk_insert is not None
    chunk_sql, chunk_params = chunk_insert
    payload = dict(zip([column.strip() for column in chunk_sql.split("(", 1)[1].split(")", 1)[0].split(",")], chunk_params))
    assert payload["collection_name"] == "siq_us_sec_filings"


def test_writer_does_not_route_pdf_table_to_html_tables_from_section_id_only():
    writer_module = _load_module("market_document_full_writer_pdf_routing", "market_document_full_writer.py")
    base = _load_module("market_document_full_base_pdf_routing", "market_document_full_rules/base.py")
    columns = {
        "pdf_tables": {
            "parse_run_id",
            "filing_id",
            "table_id",
            "section_id",
            "page_number",
            "table_index",
            "title",
            "source_format",
            "document_format",
            "raw",
        },
        "html_tables": {
            "parse_run_id",
            "filing_id",
            "table_id",
            "section_id",
            "html_anchor",
            "xpath",
            "title",
            "source_format",
            "document_format",
            "raw",
        },
    }
    conn = FakeConn(columns, database="siq_eu")
    writer = writer_module.MarketDocumentFullWriter(conn, market="EU")
    rows = base.MarketDocumentFullRows(
        company={"company_id": "EU:NL:ASML:NL0010273215", "ticker": "ASML"},
        filing={"filing_id": "EU:f1", "company_id": "EU:NL:ASML:NL0010273215"},
        parse_run={"parse_run_id": "parse-eu", "filing_id": "EU:f1"},
        tables=[{"table_id": "table-1", "section_id": "financials", "page_number": 12, "table_index": 1, "title": "PDF statement", "source_format": "pdf", "raw": {}}],
    )

    writer._insert_tables(rows)

    assert any("insert into eu_ifrs.pdf_tables" in sql for sql, _params in conn.executed)
    assert not any("insert into eu_ifrs.html_tables" in sql for sql, _params in conn.executed)


def test_writer_hydrates_eu_identity_for_required_fact_and_wide_columns():
    writer_module = _load_module("market_document_full_writer", "market_document_full_writer.py")
    base = _load_module("market_document_full_base", "market_document_full_rules/base.py")
    columns = {
        "financial_statement_items": {
            "item_uid",
            "filing_id",
            "parse_run_id",
            "company_id",
            "country",
            "ticker",
            "isin",
            "lei",
            "canonical_name",
            "period_key",
            "value",
            "currency",
            "fact_currency",
            "reporting_currency",
            "presentation_currency",
        },
        "financial_all_metrics_wide": {
            "filing_id",
            "parse_run_id",
            "company_id",
            "country",
            "ticker",
            "isin",
            "lei",
            "period_key",
            "all_metrics",
        },
    }
    conn = FakeConn(columns, database="siq_eu")
    writer = writer_module.MarketDocumentFullWriter(conn, market="EU")
    rows = base.MarketDocumentFullRows(
        company={"company_id": "EU:GB:VOD:GB00BH4HKS39", "ticker": "VOD", "country": "GB", "isin": "GB00BH4HKS39", "lei": "lei-1"},
        filing={"filing_id": "EU:f1", "fiscal_year": 2025, "fiscal_period": "FY"},
        parse_run={"parse_run_id": "parse-eu"},
        statement_items=[
            {
                "item_uid": "item-1",
                "canonical_name": "revenue",
                "period_key": "2025",
                "value": "100",
                "currency": "GBP",
                "fact_currency": "GBP",
                "reporting_currency": "EUR",
                "presentation_currency": "EUR",
            }
        ],
        wide_rows=[{"period_key": "2025", "all_metrics": {"revenue": {"value": "100"}}}],
    )

    writer._insert_statement_items(rows)
    writer._insert_wide(rows)

    inserts = [(sql, dict(zip(sql.split("(", 1)[1].split(")", 1)[0].split(", "), params))) for sql, params in conn.executed if sql.startswith("insert")]
    item_payload = next(payload for sql, payload in inserts if "financial_statement_items" in sql)
    wide_payload = next(payload for sql, payload in inserts if "financial_all_metrics_wide" in sql)
    assert item_payload["country"] == "GB"
    assert item_payload["isin"] == "GB00BH4HKS39"
    assert item_payload["fact_currency"] == "GBP"
    assert item_payload["reporting_currency"] == "EUR"
    assert wide_payload["country"] == "GB"
    assert wide_payload["lei"] == "lei-1"


def test_writer_writes_us_wide_detail_table_not_aggregate_view():
    writer_module = _load_module("market_document_full_writer", "market_document_full_writer.py")
    base = _load_module("market_document_full_base", "market_document_full_rules/base.py")
    columns = {
        "financial_all_metrics_wide_detail": {
            "filing_id",
            "parse_run_id",
            "ticker",
            "accession_number",
            "form",
            "period_key",
            "all_metrics",
        },
    }
    conn = FakeConn(columns, database="siq_us")
    writer = writer_module.MarketDocumentFullWriter(conn, market="US")
    rows = base.MarketDocumentFullRows(
        company={"company_id": "US:CIK0000320193", "ticker": "AAPL", "cik": "320193"},
        filing={"filing_id": "US:f1", "accession_number": "0000320193-25-000001", "form": "10-K"},
        parse_run={"parse_run_id": "parse-us"},
        wide_rows=[{"period_key": "FY2025", "all_metrics": {"revenue": {"value": "1"}}}],
    )

    writer._insert_wide(rows)

    assert any("insert into sec_us.financial_all_metrics_wide_detail" in sql for sql, _params in conn.executed)
    assert not any("insert into sec_us.financial_all_metrics_wide " in sql for sql, _params in conn.executed)


def test_writer_persists_raw_refs_quality_and_hk_enhancements():
    writer_module = _load_module("market_document_full_writer", "market_document_full_writer.py")
    base = _load_module("market_document_full_base", "market_document_full_rules/base.py")
    columns = {
        "companies": {"company_id", "ticker"},
        "filings": {"filing_id", "company_id", "ticker"},
        "parse_runs": {"parse_run_id", "filing_id", "parser_version", "rules_version", "wiki_package_path", "status"},
        "raw_payload_refs": {
            "payload_ref_id",
            "filing_id",
            "parse_run_id",
            "payload_name",
            "local_path",
            "sha256",
            "summary",
            "raw",
        },
        "quality_reports": {
            "parse_run_id",
            "filing_id",
            "overall_status",
            "table_count",
            "critical_warnings",
            "raw",
        },
        "footnotes": {"footnote_id", "filing_id", "parse_run_id", "page_number", "table_index", "footnote_key", "content", "raw"},
        "toc_entries": {"toc_entry_id", "filing_id", "parse_run_id", "page_number", "title", "level", "raw"},
        "financial_note_links": {"link_id", "filing_id", "parse_run_id", "table_index", "target", "note_key", "raw"},
        "table_relations": {"relation_id", "filing_id", "parse_run_id", "table_index", "target", "related_table_id", "relation_type", "raw"},
        "table_quality_signals": {"signal_id", "filing_id", "parse_run_id", "table_index", "signal_type", "signal_value", "raw"},
    }
    conn = FakeConn(columns, database="siq_hk")
    writer = writer_module.MarketDocumentFullWriter(conn, market="HK")
    rows = base.MarketDocumentFullRows(
        company={"company_id": "HK:00700", "ticker": "00700"},
        filing={"filing_id": "HK:f1", "company_id": "HK:00700", "ticker": "00700"},
        parse_run={"parse_run_id": "parse-hk", "filing_id": "HK:f1", "parser_version": "pdf", "rules_version": "v1", "wiki_package_path": "/tmp/document_full.json", "status": "warning"},
        raw_payload_refs=[{"payload_name": "document_full", "path": "/tmp/document_full.json", "sha256": "abc", "summary": {"market": "HK"}}],
        quality_reports=[{"overall_status": "warning", "table_count": 3, "critical_warnings": ["missing"], "raw": {"overall_status": "warning"}}],
        footnotes=[{"footnote_id": "fn-1", "page_number": 8, "table_index": 2, "footnote_key": "1", "content": "Note text", "raw": {}}],
        toc_entries=[{"toc_entry_id": "toc-1", "page_number": 7, "title": "Financials", "level": 1, "raw": {}}],
        financial_note_links=[{"link_id": "link-1", "table_index": 2, "target": "revenue", "note_key": "1", "raw": {}}],
        table_relations=[{"relation_id": "rel-1", "table_index": 2, "target": "revenue", "related_table_id": "table-3", "relation_type": "footnote", "raw": {}}],
        table_quality_signals=[{"signal_id": "signal-1", "table_index": 2, "signal_type": "table_quality", "signal_value": "0.95", "raw": {}}],
    )

    writer.import_rows(rows)

    sqls = [sql for sql, _params in conn.executed]
    for table in (
        "raw_payload_refs",
        "quality_reports",
        "footnotes",
        "toc_entries",
        "financial_note_links",
        "table_relations",
        "table_quality_signals",
    ):
        assert any(f"delete from pdf2md_hk.{table}" in sql for sql in sqls)
        assert any(f"insert into pdf2md_hk.{table}" in sql for sql in sqls)
