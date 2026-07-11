from __future__ import annotations

import os
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

try:
    import psycopg
    from psycopg.types.json import Jsonb
except ImportError as exc:  # pragma: no cover
    raise SystemExit("psycopg is required: pip install psycopg[binary]") from exc

from market_document_full_rules.base import MarketDocumentFullRows
from market_document_full_rules.common import stable_id
from market_ingestion_contract import (
    database_url as contract_database_url,
    normalize_market,
    quote_ident,
    target_for_market,
    validate_connection_database,
    validate_schema,
)


REPO_ROOT = Path(__file__).resolve().parents[2]

MARKET_CONFIG = {
    "HK": {"schema": "pdf2md_hk", "database": "siq_hk", "ddl": REPO_ROOT / "db" / "ddl" / "020_create_pdf2md_hk_schema.sql"},
    "JP": {"schema": "edinet_jp", "database": "siq_jp", "ddl": REPO_ROOT / "db" / "ddl" / "030_create_edinet_jp_schema.sql"},
    "KR": {"schema": "dart_kr", "database": "siq_kr", "ddl": REPO_ROOT / "db" / "ddl" / "040_create_dart_kr_schema.sql"},
    "EU": {"schema": "eu_ifrs", "database": "siq_eu", "ddl": REPO_ROOT / "db" / "ddl" / "050_create_eu_ifrs_schema.sql"},
    "US": {"schema": "sec_us", "database": "siq_us", "ddl": REPO_ROOT / "db" / "ddl" / "010_create_sec_us_schema.sql"},
}


STATEMENT_SPLIT_TABLES = {
    "balance_sheet": "financial_balance_sheet_items",
    "statement_of_financial_position": "financial_balance_sheet_items",
    "income_statement": "financial_income_statement_items",
    "profit_or_loss": "financial_income_statement_items",
    "cash_flow_statement": "financial_cash_flow_statement_items",
    "cash_flows": "financial_cash_flow_statement_items",
}

EXECUTE_MANY_BATCH_SIZE = 250


def json_safe_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime, Path)):
        return str(value)
    if isinstance(value, dict):
        return {str(key): json_safe_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe_value(item) for item in value]
    return value


def json_value(value: Any) -> Jsonb:
    return Jsonb(json_safe_value(value if value is not None else {}))


def database_url(explicit: str | None, *, market: str) -> str:
    return contract_database_url(explicit, market)


def connect(url: str):
    return psycopg.connect(url, autocommit=False)


def run_market_ddl(conn: Any, market: str) -> None:
    validate_connection_database(conn, market)
    ddl_path = Path(MARKET_CONFIG[market]["ddl"])
    conn.execute(ddl_path.read_text(encoding="utf-8"))


class MarketDocumentFullWriter:
    def __init__(self, conn: Any, *, market: str, schema: str | None = None):
        self.conn = conn
        self.market = normalize_market(market)
        target = target_for_market(self.market)
        self.schema = schema or target.schema
        validate_schema(self.schema, self.market)
        self.schema_sql = quote_ident(self.schema)
        self._column_cache: dict[str, set[str]] = {}
        self._table_cache: dict[str, bool] = {}
        self._parse_run_child_table_cache: list[str] | None = None
        validate_connection_database(conn, self.market)

    def table_exists(self, table: str) -> bool:
        if table not in self._table_cache:
            row = self.conn.execute(
                """
                select 1
                from information_schema.tables
                where table_schema = %s
                  and table_name = %s
                  and table_type = 'BASE TABLE'
                """,
                (self.schema, table),
            ).fetchone()
            self._table_cache[table] = bool(row and row[0])
        return self._table_cache[table]

    def columns(self, table: str) -> set[str]:
        if table not in self._column_cache:
            if not self.table_exists(table):
                self._column_cache[table] = set()
            else:
                rows = self.conn.execute(
                    """
                    select column_name
                    from information_schema.columns
                    where table_schema = %s and table_name = %s
                    """,
                    (self.schema, table),
                ).fetchall()
                self._column_cache[table] = {str(row[0]) for row in rows}
        return self._column_cache[table]

    def import_rows(self, rows: MarketDocumentFullRows) -> str:
        self._reuse_existing_company_identity(rows)
        parse_run_id = str(rows.parse_run["parse_run_id"])
        with self.conn.transaction():
            self._upsert_company(rows.company)
            self._upsert_filing(rows.filing)
            self._upsert_parse_run(rows.parse_run)
            self._delete_run_rows(parse_run_id)
            for item in rows.artifacts:
                self._insert_dynamic("artifacts", {"parse_run_id": parse_run_id, **item}, conflict=("parse_run_id", "artifact_type"))
            self._insert_raw_payload_refs(rows)
            self._insert_sections(rows)
            self._insert_pages(rows)
            self._insert_blocks(rows)
            self._insert_tables(rows)
            self._insert_structure_enhancements(rows)
            self._insert_xbrl_rows(rows)
            self._insert_dynamic_many(
                "evidence_citations",
                (
                    {"filing_id": rows.filing["filing_id"], "parse_run_id": parse_run_id, **citation}
                    for citation in rows.citations
                ),
                conflict=("evidence_id",),
            )
            identity = self.identity_payload(rows)
            self._insert_dynamic_many(
                "financial_statements",
                (
                    {
                        "filing_id": rows.filing["filing_id"],
                        "parse_run_id": parse_run_id,
                        **identity,
                        **statement,
                    }
                    for statement in rows.statements
                ),
                conflict=("parse_run_id", "statement_id"),
            )
            self._insert_statement_items(rows)
            self._insert_checks(rows)
            self._insert_quality_reports(rows)
            self._insert_wide(rows)
            self._insert_chunks(rows)
            self._insert_normalization(rows)
        return parse_run_id

    def _delete_run_rows(self, parse_run_id: str) -> None:
        deleted: set[str] = set()
        for table in (
            "retrieval_chunks",
            "document_chunks",
            "raw_payload_refs",
            "financial_items_enriched",
            "financial_all_metrics_wide_detail",
            "financial_all_metrics_wide",
            "financial_checks",
            "quality_checks",
            "quality_reports",
            "financial_key_metrics",
            "financial_cash_flow_statement_items",
            "financial_income_statement_items",
            "financial_balance_sheet_items",
            "financial_statement_items",
            "financial_statements",
            "operating_metric_facts",
            "financial_facts",
            "xbrl_facts_raw",
            "xbrl_units",
            "xbrl_contexts",
            "evidence_citations",
            "content_blocks",
            "footnotes",
            "toc_entries",
            "financial_note_links",
            "table_relations",
            "table_quality_signals",
            "document_tables",
            "html_tables",
            "pdf_tables",
            "pdf_pages",
            "document_pages",
            "filing_sections",
            "artifacts",
            "parser_artifacts",
        ):
            if self.table_exists(table) and "parse_run_id" in self.columns(table):
                self.conn.execute(f"delete from {self.schema_sql}.{quote_ident(table)} where parse_run_id = %s", (parse_run_id,))
                deleted.add(table)
        for table in self._parse_run_child_tables():
            if table in deleted or table == "parse_runs":
                continue
            self.conn.execute(f"delete from {self.schema_sql}.{quote_ident(table)} where parse_run_id = %s", (parse_run_id,))

    def _parse_run_child_tables(self) -> list[str]:
        if self._parse_run_child_table_cache is not None:
            return list(self._parse_run_child_table_cache)
        rows = self.conn.execute(
            """
            select table_name
            from information_schema.columns
            where table_schema = %s
              and column_name = 'parse_run_id'
              and table_name <> 'parse_runs'
            order by table_name
            """,
            (self.schema,),
        ).fetchall()
        tables: list[str] = []
        for row in rows:
            table = str(row[0])
            if table and table not in tables and self.table_exists(table):
                tables.append(table)
        self._parse_run_child_table_cache = tables
        return tables

    def identity_payload(self, rows: MarketDocumentFullRows) -> dict[str, Any]:
        company = rows.company
        filing = rows.filing
        accession_number = filing.get("accession_number") or filing.get("source_id") or filing.get("filing_id")
        form = filing.get("form") or filing.get("report_type") or "10-K"
        return {
            "market": self.market,
            "company_id": company.get("company_id"),
            "ticker": company.get("ticker") or filing.get("ticker"),
            "stock_code": company.get("stock_code") or filing.get("stock_code"),
            "security_code": company.get("security_code") or filing.get("security_code"),
            "company_name": company.get("company_name"),
            "country": company.get("country") or filing.get("country"),
            "isin": company.get("isin") or filing.get("isin"),
            "lei": company.get("lei") or filing.get("lei"),
            "exchange": company.get("exchange") or filing.get("exchange"),
            "cik": company.get("cik") or filing.get("cik"),
            "accession_number": accession_number,
            "form": form,
            "fiscal_year": filing.get("fiscal_year"),
            "fiscal_period": filing.get("fiscal_period"),
        }

    def _upsert_company(self, company: dict[str, Any]) -> None:
        self._insert_dynamic("companies", company, conflict=("company_id",), update=True)

    def _reuse_existing_company_identity(self, rows: MarketDocumentFullRows) -> None:
        if self.market != "US" or not self.table_exists("companies"):
            return
        company_columns = self.columns("companies")
        if "company_id" not in company_columns or "cik" not in company_columns:
            return
        cik = str(rows.company.get("cik") or rows.filing.get("cik") or "").strip()
        if not cik:
            return
        candidates = [cik]
        if cik.isdigit():
            candidates.append(cik.zfill(10))
        candidates = list(dict.fromkeys(candidate for candidate in candidates if candidate))
        placeholders = ", ".join(["%s"] * len(candidates))
        row = self.conn.execute(
            f"select company_id from {self.schema_sql}.companies where cik in ({placeholders}) limit 1",
            tuple(candidates),
        ).fetchone()
        if not row or not row[0]:
            return
        existing_company_id = str(row[0])
        if existing_company_id == rows.company.get("company_id"):
            return
        rows.company["company_id"] = existing_company_id
        rows.filing["company_id"] = existing_company_id

    def _upsert_filing(self, filing: dict[str, Any]) -> None:
        self._insert_dynamic("filings", filing, conflict=("filing_id",), update=True)

    def _upsert_parse_run(self, parse_run: dict[str, Any]) -> None:
        self._insert_dynamic("parse_runs", parse_run, conflict=("parse_run_id",), update=True)

    def _insert_sections(self, rows: MarketDocumentFullRows) -> None:
        if not self.table_exists("filing_sections"):
            return
        parse_run_id = rows.parse_run["parse_run_id"]
        filing_id = rows.filing["filing_id"]
        self._insert_dynamic_many(
            "filing_sections",
            ({"filing_id": filing_id, "parse_run_id": parse_run_id, **section} for section in rows.sections),
            conflict=("parse_run_id", "section_id"),
        )

    def _insert_pages(self, rows: MarketDocumentFullRows) -> None:
        parse_run_id = rows.parse_run["parse_run_id"]
        filing_id = rows.filing["filing_id"]
        table = "document_pages" if self.table_exists("document_pages") else "pdf_pages"
        if not self.table_exists(table):
            return
        conflict = ("parse_run_id", "page_number") if "parse_run_id" in self.columns(table) else None
        self._insert_dynamic_many(
            table,
            ({"filing_id": filing_id, "parse_run_id": parse_run_id, **page} for page in rows.pages),
            conflict=conflict,
        )

    def _insert_blocks(self, rows: MarketDocumentFullRows) -> None:
        if not self.table_exists("content_blocks"):
            return
        parse_run_id = rows.parse_run["parse_run_id"]
        filing_id = rows.filing["filing_id"]
        self._insert_dynamic_many(
            "content_blocks",
            ({"filing_id": filing_id, "parse_run_id": parse_run_id, **block} for block in rows.blocks),
            conflict=("block_id",) if "block_id" in self.columns("content_blocks") else None,
        )

    def _insert_tables(self, rows: MarketDocumentFullRows) -> None:
        parse_run_id = rows.parse_run["parse_run_id"]
        filing_id = rows.filing["filing_id"]
        payloads_by_table: dict[str, list[dict[str, Any]]] = {}
        for table_row in rows.tables:
            table = "html_tables" if self._should_use_html_tables(table_row) else ""
            if not table:
                table = "document_tables" if self.table_exists("document_tables") else "pdf_tables"
            if not self.table_exists(table):
                continue
            payloads_by_table.setdefault(table, []).append({"filing_id": filing_id, "parse_run_id": parse_run_id, **table_row})
        for table, payloads in payloads_by_table.items():
            conflict = ("parse_run_id", "table_id") if "table_id" in self.columns(table) else None
            self._insert_dynamic_many(table, payloads, conflict=conflict)

    def _should_use_html_tables(self, table_row: dict[str, Any]) -> bool:
        if not self.table_exists("html_tables"):
            return False
        if self.market == "US":
            return True
        raw = table_row.get("raw") if isinstance(table_row.get("raw"), dict) else {}
        source_format = str(table_row.get("source_format") or raw.get("source_format") or "").lower()
        document_format = str(table_row.get("document_format") or raw.get("document_format") or "").lower()
        format_text = " ".join(part for part in (source_format, document_format) if part)
        return bool(
            table_row.get("html_anchor")
            or table_row.get("xpath")
            or "html" in format_text
            or "xhtml" in format_text
            or "ixbrl" in format_text
        )

    def _insert_raw_payload_refs(self, rows: MarketDocumentFullRows) -> None:
        parse_run_id = rows.parse_run["parse_run_id"]
        filing_id = rows.filing["filing_id"]
        for idx, ref in enumerate(rows.raw_payload_refs, start=1):
            payload = {
                "payload_ref_id": ref.get("payload_ref_id") or stable_id(parse_run_id, ref.get("payload_name"), ref.get("path"), idx, prefix="payload"),
                "filing_id": filing_id,
                "parse_run_id": parse_run_id,
                "payload_name": ref.get("payload_name") or ref.get("name") or "payload",
                "local_path": ref.get("local_path") or ref.get("path"),
                "sha256": ref.get("sha256"),
                "size_bytes": ref.get("size_bytes"),
                "summary": ref.get("summary") or {},
                "raw": ref.get("raw") or ref,
            }
            if self.table_exists("raw_payload_refs"):
                self._insert_dynamic("raw_payload_refs", payload, conflict=("payload_ref_id",))
            elif self.table_exists("artifacts"):
                self._insert_dynamic(
                    "artifacts",
                    {
                        "parse_run_id": parse_run_id,
                        "artifact_type": payload["payload_name"],
                        "local_path": payload["local_path"] or "",
                        "sha256": payload["sha256"],
                        "size_bytes": payload["size_bytes"],
                        "raw": payload["raw"],
                    },
                    conflict=("parse_run_id", "artifact_type"),
                )

    def _insert_structure_enhancements(self, rows: MarketDocumentFullRows) -> None:
        parse_run_id = rows.parse_run["parse_run_id"]
        filing_id = rows.filing["filing_id"]
        for table, items, conflict in (
            ("footnotes", rows.footnotes, ("footnote_id",)),
            ("toc_entries", rows.toc_entries, ("toc_entry_id",)),
            ("financial_note_links", rows.financial_note_links, ("link_id",)),
            ("table_relations", rows.table_relations, ("relation_id",)),
            ("table_quality_signals", rows.table_quality_signals, ("signal_id",)),
        ):
            if not self.table_exists(table):
                continue
            self._insert_dynamic_many(
                table,
                ({"filing_id": filing_id, "parse_run_id": parse_run_id, **item} for item in items),
                conflict=conflict,
            )

    def _insert_xbrl_rows(self, rows: MarketDocumentFullRows) -> None:
        parse_run_id = rows.parse_run["parse_run_id"]
        filing_id = rows.filing["filing_id"]
        if self.table_exists("xbrl_contexts"):
            self._insert_dynamic_many(
                "xbrl_contexts",
                (
                    {
                        "context_uid": context.get("context_uid") or stable_id(parse_run_id, str(context.get("context_ref") or ""), prefix="ctx"),
                        "filing_id": filing_id,
                        "parse_run_id": parse_run_id,
                        **context,
                    }
                    for context in rows.xbrl_contexts
                ),
                conflict=("parse_run_id", "context_ref"),
            )
        if self.table_exists("xbrl_units"):
            self._insert_dynamic_many(
                "xbrl_units",
                (
                    {
                        "unit_uid": unit.get("unit_uid") or stable_id(parse_run_id, str(unit.get("unit_ref") or ""), prefix="unit"),
                        "filing_id": filing_id,
                        "parse_run_id": parse_run_id,
                        **unit,
                    }
                    for unit in rows.xbrl_units
                ),
                conflict=("parse_run_id", "unit_ref"),
            )
        if self.table_exists("xbrl_facts_raw"):
            fact_columns = self.columns("xbrl_facts_raw")
            conflict = ("raw_fact_id",) if "raw_fact_id" in fact_columns else ("fact_id",)
            fact_payloads = []
            for fact in rows.xbrl_facts_raw:
                fact_payload = {"filing_id": filing_id, "parse_run_id": parse_run_id, **fact}
                if "raw_fact_id" in fact_columns:
                    stable_fact_id = fact.get("fact_id")
                    source_fact_id = fact.get("raw_fact_id") or stable_fact_id
                    fact_payload["raw_fact_id"] = stable_fact_id
                    fact_payload["fact_id"] = source_fact_id
                fact_payloads.append(fact_payload)
            self._insert_dynamic_many("xbrl_facts_raw", fact_payloads, conflict=conflict)

    def _insert_statement_items(self, rows: MarketDocumentFullRows) -> None:
        parse_run_id = rows.parse_run["parse_run_id"]
        filing = rows.filing
        identity = self.identity_payload(rows)
        all_items = [(item, False) for item in rows.statement_items] + [(item, True) for item in rows.key_metrics]
        financial_key_metrics: list[dict[str, Any]] = []
        financial_statement_items: list[dict[str, Any]] = []
        financial_facts: list[dict[str, Any]] = []
        operating_metrics: list[dict[str, Any]] = []
        split_payloads: dict[str, list[dict[str, Any]]] = {}
        has_key_metrics_table = self.table_exists("financial_key_metrics")
        has_statement_items_table = self.table_exists("financial_statement_items")
        has_operating_metrics_table = self.table_exists("operating_metric_facts")
        for item, is_key_metric in all_items:
            payload = {
                "filing_id": filing["filing_id"],
                "parse_run_id": parse_run_id,
                **identity,
                **item,
                "raw": self._compact_statement_item_raw(item),
            }
            if is_key_metric and has_key_metrics_table:
                financial_key_metrics.append(payload)
            elif has_statement_items_table:
                financial_statement_items.append(payload)
            else:
                financial_facts.append(self._financial_fact_payload(payload))

            split_table = STATEMENT_SPLIT_TABLES.get(str(item.get("statement_type") or ""))
            if split_table and self.table_exists(split_table):
                split_payloads.setdefault(split_table, []).append(payload)
            if is_key_metric and not has_key_metrics_table:
                financial_facts.append(self._financial_fact_payload(payload))

            if item.get("canonical_scope") in {"industry", "company"} and has_operating_metrics_table:
                operating_metrics.append(self._operating_metric_payload(payload))

        self._insert_dynamic_many("financial_key_metrics", financial_key_metrics, conflict=("item_uid",))
        self._insert_dynamic_many("financial_statement_items", financial_statement_items, conflict=("item_uid",))
        self._insert_dynamic_many("financial_facts", financial_facts, conflict=("metric_id",))
        self._insert_dynamic_many("operating_metric_facts", operating_metrics, conflict=("metric_id",))
        for split_table, payloads in split_payloads.items():
            self._insert_dynamic_many(split_table, payloads, conflict=("item_uid",))

    def _insert_financial_fact_from_item(self, item: dict[str, Any]) -> None:
        if not self.table_exists("financial_facts"):
            return
        self._insert_dynamic("financial_facts", self._financial_fact_payload(item), conflict=("metric_id",))

    def _financial_fact_payload(self, item: dict[str, Any]) -> dict[str, Any]:
        return {
            "metric_id": item.get("item_uid"),
            "filing_id": item.get("filing_id"),
            "parse_run_id": item.get("parse_run_id"),
            "country": item.get("country") or item.get("raw", {}).get("country"),
            "ticker": item.get("ticker"),
            "statement_type": item.get("statement_type"),
            "canonical_name": item.get("canonical_name"),
            "local_name": item.get("item_name"),
            "label": item.get("item_name"),
            "value": item.get("value"),
            "raw_value": item.get("raw_value"),
            "unit": item.get("unit"),
            "currency": item.get("currency"),
            "scale": item.get("scale"),
            "period_key": item.get("period_key"),
            "period_start": item.get("period_start"),
            "period_end": item.get("period_end"),
            "fiscal_year": item.get("fiscal_year"),
            "fiscal_period": item.get("fiscal_period"),
            "confidence": item.get("confidence"),
            "evidence_id": item.get("evidence_id"),
            "xbrl_tag": item.get("xbrl_tag") or item.get("concept") or item.get("raw", {}).get("item", {}).get("concept"),
            "context_ref": item.get("context_ref") or item.get("raw", {}).get("item", {}).get("context_ref"),
            "raw": self._compact_statement_item_raw(item),
        }

    def _operating_metric_payload(self, item: dict[str, Any]) -> dict[str, Any]:
        return {
            "metric_id": item.get("item_uid"),
            "filing_id": item.get("filing_id"),
            "parse_run_id": item.get("parse_run_id"),
            "country": item.get("country"),
            "ticker": item.get("ticker"),
            "metric_name": item.get("item_name"),
            "canonical_name": item.get("canonical_name") or item.get("item_name"),
            "industry_profile": item.get("industry_profile"),
            "value": item.get("value"),
            "raw_value": item.get("raw_value"),
            "unit": item.get("unit"),
            "period_key": item.get("period_key"),
            "source_type": "document_full",
            "evidence_id": item.get("evidence_id"),
            "confidence": item.get("confidence"),
            "raw": self._compact_statement_item_raw(item),
        }

    def _insert_checks(self, rows: MarketDocumentFullRows) -> None:
        table = "financial_checks" if self.table_exists("financial_checks") else "quality_checks"
        if not self.table_exists(table):
            return
        self._insert_dynamic_many(
            table,
            (
                {"filing_id": rows.filing["filing_id"], "parse_run_id": rows.parse_run["parse_run_id"], **check}
                for check in rows.checks
            ),
            conflict=("check_id",),
        )

    def _insert_quality_reports(self, rows: MarketDocumentFullRows) -> None:
        if not self.table_exists("quality_reports"):
            return
        parse_run_id = rows.parse_run["parse_run_id"]
        filing_id = rows.filing["filing_id"]
        reports = rows.quality_reports or [
            {
                "parse_run_id": parse_run_id,
                "filing_id": filing_id,
                "overall_status": rows.parse_run.get("status") or "warning",
                "critical_warnings": rows.parse_run.get("warnings") or [],
                "raw": rows.parse_run.get("raw") or {},
            }
        ]
        self._insert_dynamic_many(
            "quality_reports",
            (
                {
                    "filing_id": filing_id,
                    "parse_run_id": parse_run_id,
                    **report,
                }
                for report in reports
            ),
            conflict=("parse_run_id",),
            update=True,
        )

    def _wide_table(self) -> str:
        if self.table_exists("financial_all_metrics_wide_detail"):
            return "financial_all_metrics_wide_detail"
        return "financial_all_metrics_wide"

    def _insert_wide(self, rows: MarketDocumentFullRows) -> None:
        table = self._wide_table()
        if not self.table_exists(table):
            return
        identity = self.identity_payload(rows)
        self._insert_dynamic_many(
            table,
            (
                {
                    "filing_id": rows.filing["filing_id"],
                    "parse_run_id": rows.parse_run["parse_run_id"],
                    **identity,
                    **row,
                }
                for row in rows.wide_rows
            ),
            conflict=("parse_run_id", "period_key"),
        )

    def _insert_chunks(self, rows: MarketDocumentFullRows) -> None:
        table = "document_chunks" if self.table_exists("document_chunks") else "retrieval_chunks"
        if not self.table_exists(table):
            return
        collection_name = target_for_market(self.market).default_collection or f"siq_{self.market.lower()}_reports"
        identity = self.identity_payload(rows)
        self._insert_dynamic_many(
            table,
            (
                {
                    "filing_id": rows.filing["filing_id"],
                    "parse_run_id": rows.parse_run["parse_run_id"],
                    **identity,
                    "collection_name": collection_name,
                    **chunk,
                }
                for chunk in rows.chunks
            ),
            conflict=("chunk_uid",),
        )

    def _insert_normalization(self, rows: MarketDocumentFullRows) -> None:
        if self.table_exists("financial_normalization_rules"):
            self._insert_dynamic_many(
                "financial_normalization_rules",
                rows.normalization_rules,
                conflict=("rule_id",),
                update=True,
            )
        if self.table_exists("financial_items_enriched"):
            identity = self.identity_payload(rows)
            self._insert_dynamic_many(
                "financial_items_enriched",
                (
                    {
                        "source_table": item.get("source_table") or (
                            "financial_key_metrics" if item.get("statement_type") == "key_metrics" else "financial_statement_items"
                        ),
                        "source_uid": item.get("source_uid") or item.get("item_uid") or item.get("enriched_id"),
                        "filing_id": rows.filing["filing_id"],
                        "parse_run_id": rows.parse_run["parse_run_id"],
                        **identity,
                        "market": self.market,
                        "item_name_raw": item.get("item_name_raw") or item.get("item_name"),
                        "period_key_raw": item.get("period_key_raw") or item.get("period_key"),
                        "canonical_source": item.get("canonical_source") or item.get("canonical_scope") or "unmapped",
                        "canonical_rule_id": item.get("canonical_rule_id") or "canonical_common_core_v1",
                        "unit_rule_id": item.get("unit_rule_id") or "unit_scale_from_report_unit_v1",
                        "period_end_date": item.get("period_end_date") or item.get("period_end"),
                        "period_start_date": item.get("period_start_date") or item.get("period_start"),
                        **item,
                        "raw_item": self._compact_enriched_raw_item(item),
                    }
                    for item in rows.enriched_items
                ),
                conflict=("enriched_id",),
            )

    def _compact_enriched_raw_item(self, item: dict[str, Any]) -> dict[str, Any]:
        fields = (
            "enriched_id",
            "item_uid",
            "source_uid",
            "source_table",
            "statement_id",
            "statement_type",
            "statement_name",
            "item_name",
            "item_name_raw",
            "local_name",
            "canonical_name",
            "canonical_label",
            "canonical_scope",
            "canonical_source",
            "period_key",
            "period_key_raw",
            "period_start",
            "period_end",
            "value",
            "raw_value",
            "value_extracted",
            "unit",
            "unit_raw",
            "currency",
            "fact_currency",
            "reporting_currency",
            "presentation_currency",
            "scale",
            "evidence_id",
            "source_page_number",
            "source_table_index",
            "source_row_index",
            "source_column_index",
            "source_bbox",
            "concept",
            "xbrl_tag",
            "taxonomy_tag",
            "context_ref",
            "confidence",
        )
        compact = {field: item.get(field) for field in fields if item.get(field) not in (None, "", {}, [])}
        raw = item.get("raw")
        if isinstance(raw, dict):
            compact_raw = {
                field: raw.get(field)
                for field in (
                    "source",
                    "concept",
                    "xbrl_tag",
                    "taxonomy_tag",
                    "context_ref",
                    "html_anchor",
                    "xpath",
                    "table_index",
                    "page_number",
                    "row_index",
                    "column_index",
                )
                if raw.get(field) not in (None, "", {}, [])
            }
            if compact_raw:
                compact["raw"] = compact_raw
        return compact or {"raw_compacted": True}

    def _compact_statement_item_raw(self, item: dict[str, Any]) -> dict[str, Any]:
        fields = (
            "item_uid",
            "statement_id",
            "statement_type",
            "statement_name",
            "item_name",
            "local_name",
            "canonical_name",
            "canonical_scope",
            "period_key",
            "period_start",
            "period_end",
            "value",
            "raw_value",
            "unit",
            "currency",
            "fact_currency",
            "reporting_currency",
            "presentation_currency",
            "scale",
            "evidence_id",
            "source_page_number",
            "source_table_index",
            "source_row_index",
            "source_column_index",
            "source_bbox",
            "concept",
            "xbrl_tag",
            "taxonomy_tag",
            "context_ref",
        )
        compact = {field: item.get(field) for field in fields if item.get(field) not in (None, "", {}, [])}
        raw = item.get("raw")
        if isinstance(raw, dict):
            compact_raw = {
                field: raw.get(field)
                for field in (
                    "source",
                    "source_type",
                    "source_format",
                    "document_format",
                    "concept",
                    "xbrl_tag",
                    "taxonomy_tag",
                    "context_ref",
                    "html_anchor",
                    "xpath",
                    "table_id",
                    "table_index",
                    "page_number",
                    "row_index",
                    "column_index",
                )
                if raw.get(field) not in (None, "", {}, [])
            }
            raw_item = raw.get("item")
            if isinstance(raw_item, dict):
                item_raw = {
                    field: raw_item.get(field)
                    for field in (
                        "concept",
                        "xbrl_tag",
                        "taxonomy",
                        "taxonomy_tag",
                        "context_ref",
                        "unit_ref",
                        "decimals",
                        "scale",
                        "html_anchor",
                        "xpath",
                    )
                    if raw_item.get(field) not in (None, "", {}, [])
                }
                if item_raw:
                    compact_raw["item"] = item_raw
            if compact_raw:
                compact["raw"] = compact_raw
        return compact or {"raw_compacted": True}

    def _insert_dynamic(
        self,
        table: str,
        payload: dict[str, Any],
        *,
        conflict: tuple[str, ...] | None = None,
        update: bool = False,
    ) -> None:
        if not self.table_exists(table):
            return
        columns = self.columns(table)
        filtered = {key: self._adapt_value(value) for key, value in payload.items() if key in columns}
        if not filtered:
            return
        column_names = tuple(filtered)
        sql = self._insert_sql(table, column_names, columns, conflict=conflict, update=update)
        self.conn.execute(sql, tuple(filtered[col] for col in column_names))

    def _insert_dynamic_many(
        self,
        table: str,
        payloads: Iterable[dict[str, Any]],
        *,
        conflict: tuple[str, ...] | None = None,
        update: bool = False,
    ) -> None:
        if not self.table_exists(table):
            return
        columns = self.columns(table)
        grouped: dict[tuple[str, ...], list[tuple[Any, ...]]] = {}
        sql_by_columns: dict[tuple[str, ...], str] = {}
        for payload in payloads:
            filtered = {key: self._adapt_value(value) for key, value in payload.items() if key in columns}
            if not filtered:
                continue
            column_names = tuple(filtered)
            grouped.setdefault(column_names, []).append(tuple(filtered[col] for col in column_names))
            if column_names not in sql_by_columns:
                sql_by_columns[column_names] = self._insert_sql(table, column_names, columns, conflict=conflict, update=update)

        for column_names, params_list in grouped.items():
            sql = sql_by_columns[column_names]
            self._execute_many(sql, params_list)

    def _execute_many(self, sql: str, params_list: list[tuple[Any, ...]]) -> None:
        if not params_list:
            return
        cursor_factory = getattr(self.conn, "cursor", None)
        if callable(cursor_factory):
            try:
                with self.conn.cursor() as cur:
                    executemany = getattr(cur, "executemany", None)
                    if callable(executemany):
                        for start in range(0, len(params_list), EXECUTE_MANY_BATCH_SIZE):
                            executemany(sql, params_list[start : start + EXECUTE_MANY_BATCH_SIZE])
                        return
            except (AttributeError, TypeError):
                pass
        for params in params_list:
            self.conn.execute(sql, params)

    def _insert_sql(
        self,
        table: str,
        column_names: tuple[str, ...],
        table_columns: set[str],
        *,
        conflict: tuple[str, ...] | None = None,
        update: bool = False,
    ) -> str:
        placeholders = ", ".join(["%s"] * len(column_names))
        safe_table = quote_ident(table)
        safe_columns = [quote_ident(col) for col in column_names]
        sql = f"insert into {self.schema_sql}.{safe_table} ({', '.join(safe_columns)}) values ({placeholders})"
        if conflict:
            conflict_cols = [col for col in conflict if col in table_columns]
            if conflict_cols:
                safe_conflict = ", ".join(quote_ident(col) for col in conflict_cols)
                if update:
                    update_cols = [col for col in column_names if col not in conflict_cols and col not in {"created_at"}]
                    if update_cols:
                        assignments = ", ".join(f"{quote_ident(col)} = excluded.{quote_ident(col)}" for col in update_cols)
                        sql += f" on conflict ({safe_conflict}) do update set {assignments}"
                    else:
                        sql += f" on conflict ({safe_conflict}) do nothing"
                else:
                    sql += f" on conflict ({safe_conflict}) do nothing"
        return sql

    def _adapt_value(self, value: Any) -> Any:
        if isinstance(value, (dict, list)):
            return json_value(value)
        if isinstance(value, Decimal):
            return value
        return value
