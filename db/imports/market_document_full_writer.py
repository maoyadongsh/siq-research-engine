from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path
from typing import Any

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


def json_value(value: Any) -> Jsonb:
    return Jsonb(value if value is not None else {})


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
            for citation in rows.citations:
                self._insert_dynamic("evidence_citations", {"filing_id": rows.filing["filing_id"], "parse_run_id": parse_run_id, **citation}, conflict=("evidence_id",))
            for statement in rows.statements:
                self._insert_dynamic(
                    "financial_statements",
                    {
                        "filing_id": rows.filing["filing_id"],
                        "parse_run_id": parse_run_id,
                        **self.identity_payload(rows),
                        **statement,
                    },
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

    def _upsert_filing(self, filing: dict[str, Any]) -> None:
        self._insert_dynamic("filings", filing, conflict=("filing_id",), update=True)

    def _upsert_parse_run(self, parse_run: dict[str, Any]) -> None:
        self._insert_dynamic("parse_runs", parse_run, conflict=("parse_run_id",), update=True)

    def _insert_sections(self, rows: MarketDocumentFullRows) -> None:
        if not self.table_exists("filing_sections"):
            return
        parse_run_id = rows.parse_run["parse_run_id"]
        filing_id = rows.filing["filing_id"]
        for section in rows.sections:
            self._insert_dynamic(
                "filing_sections",
                {"filing_id": filing_id, "parse_run_id": parse_run_id, **section},
                conflict=("parse_run_id", "section_id"),
            )

    def _insert_pages(self, rows: MarketDocumentFullRows) -> None:
        parse_run_id = rows.parse_run["parse_run_id"]
        filing_id = rows.filing["filing_id"]
        table = "document_pages" if self.table_exists("document_pages") else "pdf_pages"
        if not self.table_exists(table):
            return
        for page in rows.pages:
            payload = {"filing_id": filing_id, "parse_run_id": parse_run_id, **page}
            conflict = ("parse_run_id", "page_number") if "parse_run_id" in self.columns(table) else None
            self._insert_dynamic(table, payload, conflict=conflict)

    def _insert_blocks(self, rows: MarketDocumentFullRows) -> None:
        if not self.table_exists("content_blocks"):
            return
        parse_run_id = rows.parse_run["parse_run_id"]
        filing_id = rows.filing["filing_id"]
        for block in rows.blocks:
            self._insert_dynamic("content_blocks", {"filing_id": filing_id, "parse_run_id": parse_run_id, **block}, conflict=("parse_run_id", "block_id") if "block_id" in self.columns("content_blocks") else None)

    def _insert_tables(self, rows: MarketDocumentFullRows) -> None:
        parse_run_id = rows.parse_run["parse_run_id"]
        filing_id = rows.filing["filing_id"]
        for table_row in rows.tables:
            table = "html_tables" if self._should_use_html_tables(table_row) else ""
            if not table:
                table = "document_tables" if self.table_exists("document_tables") else "pdf_tables"
            if not self.table_exists(table):
                continue
            payload = {"filing_id": filing_id, "parse_run_id": parse_run_id, **table_row}
            conflict = ("parse_run_id", "table_id") if "table_id" in self.columns(table) else None
            self._insert_dynamic(table, payload, conflict=conflict)

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
            for item in items:
                self._insert_dynamic(table, {"filing_id": filing_id, "parse_run_id": parse_run_id, **item}, conflict=conflict)

    def _insert_xbrl_rows(self, rows: MarketDocumentFullRows) -> None:
        parse_run_id = rows.parse_run["parse_run_id"]
        filing_id = rows.filing["filing_id"]
        if self.table_exists("xbrl_contexts"):
            for context in rows.xbrl_contexts:
                context_ref = str(context.get("context_ref") or "")
                self._insert_dynamic(
                    "xbrl_contexts",
                    {
                        "context_uid": context.get("context_uid") or stable_id(parse_run_id, context_ref, prefix="ctx"),
                        "filing_id": filing_id,
                        "parse_run_id": parse_run_id,
                        **context,
                    },
                    conflict=("parse_run_id", "context_ref"),
                )
        if self.table_exists("xbrl_units"):
            for unit in rows.xbrl_units:
                unit_ref = str(unit.get("unit_ref") or "")
                self._insert_dynamic(
                    "xbrl_units",
                    {
                        "unit_uid": unit.get("unit_uid") or stable_id(parse_run_id, unit_ref, prefix="unit"),
                        "filing_id": filing_id,
                        "parse_run_id": parse_run_id,
                        **unit,
                    },
                    conflict=("parse_run_id", "unit_ref"),
                )
        if self.table_exists("xbrl_facts_raw"):
            fact_columns = self.columns("xbrl_facts_raw")
            conflict = ("raw_fact_id",) if "raw_fact_id" in fact_columns else ("fact_id",)
            for fact in rows.xbrl_facts_raw:
                fact_payload = {"filing_id": filing_id, "parse_run_id": parse_run_id, **fact}
                if "raw_fact_id" in fact_columns:
                    stable_fact_id = fact.get("fact_id")
                    source_fact_id = fact.get("raw_fact_id") or stable_fact_id
                    fact_payload["raw_fact_id"] = stable_fact_id
                    fact_payload["fact_id"] = source_fact_id
                self._insert_dynamic(
                    "xbrl_facts_raw",
                    fact_payload,
                    conflict=conflict,
                )

    def _insert_statement_items(self, rows: MarketDocumentFullRows) -> None:
        parse_run_id = rows.parse_run["parse_run_id"]
        filing = rows.filing
        company = rows.company
        all_items = [(item, False) for item in rows.statement_items] + [(item, True) for item in rows.key_metrics]
        for item, is_key_metric in all_items:
            payload = {
                "filing_id": filing["filing_id"],
                "parse_run_id": parse_run_id,
                **self.identity_payload(rows),
                **item,
            }
            if is_key_metric and self.table_exists("financial_key_metrics"):
                self._insert_dynamic("financial_key_metrics", payload, conflict=("item_uid",))
            elif self.table_exists("financial_statement_items"):
                self._insert_dynamic("financial_statement_items", payload, conflict=("item_uid",))
            else:
                self._insert_financial_fact_from_item(payload)

            split_table = STATEMENT_SPLIT_TABLES.get(str(item.get("statement_type") or ""))
            if split_table and self.table_exists(split_table):
                self._insert_dynamic(split_table, payload, conflict=("item_uid",))
            if is_key_metric and not self.table_exists("financial_key_metrics"):
                self._insert_financial_fact_from_item(payload)

            if item.get("canonical_scope") in {"industry", "company"} and self.table_exists("operating_metric_facts"):
                self._insert_dynamic("operating_metric_facts", self._operating_metric_payload(payload), conflict=("metric_id",))

    def _insert_financial_fact_from_item(self, item: dict[str, Any]) -> None:
        if not self.table_exists("financial_facts"):
            return
        payload = {
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
            "xbrl_tag": item.get("raw", {}).get("item", {}).get("concept"),
            "context_ref": item.get("raw", {}).get("item", {}).get("context_ref"),
            "raw": item.get("raw") or item,
        }
        self._insert_dynamic("financial_facts", payload, conflict=("metric_id",))

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
            "raw": item.get("raw") or item,
        }

    def _insert_checks(self, rows: MarketDocumentFullRows) -> None:
        table = "financial_checks" if self.table_exists("financial_checks") else "quality_checks"
        if not self.table_exists(table):
            return
        for check in rows.checks:
            self._insert_dynamic(table, {"filing_id": rows.filing["filing_id"], "parse_run_id": rows.parse_run["parse_run_id"], **check}, conflict=("check_id",))

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
        for report in reports:
            self._insert_dynamic(
                "quality_reports",
                {
                    "filing_id": filing_id,
                    "parse_run_id": parse_run_id,
                    **report,
                },
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
        for row in rows.wide_rows:
            payload = {
                "filing_id": rows.filing["filing_id"],
                "parse_run_id": rows.parse_run["parse_run_id"],
                **self.identity_payload(rows),
                **row,
            }
            self._insert_dynamic(table, payload, conflict=("parse_run_id", "period_key"))

    def _insert_chunks(self, rows: MarketDocumentFullRows) -> None:
        table = "document_chunks" if self.table_exists("document_chunks") else "retrieval_chunks"
        if not self.table_exists(table):
            return
        collection_name = target_for_market(self.market).default_collection or f"siq_{self.market.lower()}_reports"
        for chunk in rows.chunks:
            payload = {"filing_id": rows.filing["filing_id"], "parse_run_id": rows.parse_run["parse_run_id"], **self.identity_payload(rows), "collection_name": collection_name, **chunk}
            self._insert_dynamic(table, payload, conflict=("chunk_uid",))

    def _insert_normalization(self, rows: MarketDocumentFullRows) -> None:
        if self.table_exists("financial_normalization_rules"):
            for rule in rows.normalization_rules:
                self._insert_dynamic("financial_normalization_rules", rule, conflict=("rule_id",), update=True)
        if self.table_exists("financial_items_enriched"):
            for item in rows.enriched_items:
                payload = {
                    "source_table": item.get("source_table") or (
                        "financial_key_metrics" if item.get("statement_type") == "key_metrics" else "financial_statement_items"
                    ),
                    "source_uid": item.get("source_uid") or item.get("item_uid") or item.get("enriched_id"),
                    "filing_id": rows.filing["filing_id"],
                    "parse_run_id": rows.parse_run["parse_run_id"],
                    **self.identity_payload(rows),
                    "market": self.market,
                    "item_name_raw": item.get("item_name_raw") or item.get("item_name"),
                    "period_key_raw": item.get("period_key_raw") or item.get("period_key"),
                    "canonical_source": item.get("canonical_source") or item.get("canonical_scope") or "unmapped",
                    "canonical_rule_id": item.get("canonical_rule_id") or "canonical_common_core_v1",
                    "unit_rule_id": item.get("unit_rule_id") or "unit_scale_from_report_unit_v1",
                    "period_end_date": item.get("period_end_date") or item.get("period_end"),
                    "period_start_date": item.get("period_start_date") or item.get("period_start"),
                    "raw_item": item.get("raw_item") or item.get("raw") or item,
                    **item,
                }
                self._insert_dynamic("financial_items_enriched", payload, conflict=("enriched_id",))

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
        column_names = list(filtered)
        placeholders = ", ".join(["%s"] * len(column_names))
        safe_table = quote_ident(table)
        safe_columns = [quote_ident(col) for col in column_names]
        sql = f"insert into {self.schema_sql}.{safe_table} ({', '.join(safe_columns)}) values ({placeholders})"
        if conflict:
            conflict_cols = [col for col in conflict if col in columns]
            if conflict_cols:
                if update:
                    update_cols = [col for col in column_names if col not in conflict_cols and col not in {"created_at"}]
                    if update_cols:
                        assignments = ", ".join(f"{quote_ident(col)} = excluded.{quote_ident(col)}" for col in update_cols)
                        sql += f" on conflict ({', '.join(quote_ident(col) for col in conflict_cols)}) do update set {assignments}"
                    else:
                        sql += f" on conflict ({', '.join(quote_ident(col) for col in conflict_cols)}) do nothing"
                else:
                    sql += f" on conflict ({', '.join(quote_ident(col) for col in conflict_cols)}) do nothing"
        self.conn.execute(sql, tuple(filtered[col] for col in column_names))

    def _adapt_value(self, value: Any) -> Any:
        if isinstance(value, (dict, list)):
            return json_value(value)
        if isinstance(value, Decimal):
            return value
        return value
