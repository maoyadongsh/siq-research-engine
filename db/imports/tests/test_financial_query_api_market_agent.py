from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from db.imports import financial_query_api as api


class FakeCursor:
    def __init__(self, columns: set[str], rows: list[dict[str, object]]):
        self.columns = columns
        self.rows = rows
        self.queries: list[tuple[str, tuple[object, ...]]] = []
        self._result: list[dict[str, object]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def execute(self, sql, params=()):
        text = " ".join(str(sql).split())
        self.queries.append((text, tuple(params or ())))
        if "information_schema.columns" in text:
            self._result = [{"column_name": column} for column in sorted(self.columns)]
        elif "v_agent_financial_facts" in text and text.lower().startswith("select "):
            rows = list(self.rows)
            scope_params = list(params or ())
            if "parse_run_id = %s" in text and scope_params:
                parse_run_id = scope_params.pop(0)
                rows = [row for row in rows if row.get("parse_run_id") == parse_run_id]
            if "filing_id = %s" in text and scope_params:
                filing_id = scope_params.pop(0)
                rows = [row for row in rows if row.get("filing_id") == filing_id]
            self._result = rows
        else:
            self._result = []

    def fetchall(self):
        return self._result


class FakeConnection:
    def __init__(self, cursor: FakeCursor):
        self.cursor_obj = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def cursor(self):
        return self.cursor_obj


def _agent_columns(*extra: str) -> set[str]:
    return {
        "company_id",
        "company_ticker",
        "company_name",
        "filing_id",
        "fiscal_year",
        "parse_run_id",
        "statement_type",
        "canonical_name",
        "canonical_label",
        "item_name",
        "item_name_raw",
        "period_key",
        "value",
        "raw_value",
        "unit",
        "currency",
        "evidence_table_index",
        "quote_text",
        *extra,
    }


def test_market_agent_query_extracts_embedded_hk_code_without_context(monkeypatch):
    cursor = FakeCursor(
        _agent_columns("stock_code", "hkex_stock_code"),
        [
            {
                "company_id": "HK:00700",
                "company_ticker": "00700",
                "company_name": "Tencent",
                "filing_id": "HK:00700:2025-annual",
                "fiscal_year": 2025,
                "parse_run_id": "parse-hk",
                "statement_type": "income_statement",
                "canonical_name": "revenue",
                "canonical_label": "revenue",
                "item_name": "Revenues",
                "period_key": "2025-12-31",
                "value": "751766",
                "raw_value": "751,766",
                "unit": "RMB million",
                "currency": "RMB",
                "evidence_table_index": 4,
                "quote_text": "Revenues | 751,766",
            }
        ],
    )
    monkeypatch.setattr(api, "get_market_connection", lambda _market: FakeConnection(cursor))

    result = api.query_market_agent_view_result("港股00700 2025收入是多少？", {"query_type": "metric"}, limit=5)

    assert result is not None
    assert result["parsed"]["market"] == "HK"
    assert result["rows"][0]["company_id"] == "HK:00700"
    assert result["agent_facts"][0]["source_type"] == "postgresql_agent_view"
    assert result["agent_facts"][0]["market"] == "HK"
    assert result["agent_facts"][0]["metric_name"] == "收入"
    assert result["agent_facts"][0]["canonical_name"] == "revenue"
    assert result["agent_facts"][0]["table_index"] == 4
    assert result["agent_facts"][0]["quote"] == "Revenues | 751,766"
    final_sql, params = cursor.queries[-1]
    assert "hkex_stock_code = %s" in final_sql
    assert "canonical_name = %s" in final_sql
    assert "00700" in params


def test_market_agent_query_extracts_company_name_from_short_natural_language(monkeypatch):
    cursor = FakeCursor(
        _agent_columns("stock_code", "hkex_stock_code"),
        [
            {
                "company_id": "HK:00700",
                "company_ticker": "00700",
                "company_name": "腾讯",
                "filing_id": "HK:00700:2025-annual",
                "fiscal_year": 2025,
                "parse_run_id": "parse-hk",
                "statement_type": "income_statement",
                "canonical_name": "revenue",
                "canonical_label": "revenue",
                "item_name": "收入",
                "period_key": "2025-12-31",
                "value": "751766",
                "raw_value": "751,766",
                "unit": "RMB million",
                "currency": "RMB",
            }
        ],
    )
    monkeypatch.setattr(api, "get_market_connection", lambda _market: FakeConnection(cursor))

    result = api.query_market_agent_view_result("港股腾讯2025收入是多少？", {"query_type": "metric"}, limit=5)

    assert result is not None
    final_sql, params = cursor.queries[-1]
    assert "company_name ilike %s" in final_sql
    assert "%腾讯%" in params
    assert "%港股腾讯2025收入是多少？%" not in params


def test_market_agent_query_uses_parsed_ticker_and_label_columns(monkeypatch):
    cursor = FakeCursor(
        _agent_columns("ticker", "metric_name_raw", "label"),
        [
            {
                "company_id": "US:CIK0000320193",
                "company_ticker": "AAPL",
                "company_name": "Apple Inc.",
                "filing_id": "US:0000320193:0000320193-25-000079",
                "fiscal_year": 2025,
                "parse_run_id": "parse-us",
                "statement_type": "income_statement",
                "canonical_label": "revenue",
                "item_name_raw": "Net sales",
                "period_key": "2025-09-27",
                "value": "416161000000",
                "raw_value": "416,161",
                "unit": "iso4217:USD",
                "currency": "USD",
                "quote_text": "416,161",
            }
        ],
    )
    monkeypatch.setattr(api, "get_market_connection", lambda _market: FakeConnection(cursor))

    result = api.query_market_agent_view_result(
        "2025 revenue",
        {"market": "US", "ticker": "AAPL", "canonical_name": "revenue", "query_type": "metric"},
        limit=3,
    )

    assert result is not None
    assert result["rows"][0]["metric_name"] == "revenue"
    assert result["rows"][0]["item_name_raw"] == "Net sales"
    final_sql, params = cursor.queries[-1]
    assert "canonical_label = %s" in final_sql
    assert "item_name_raw ilike %s" in final_sql
    assert "AAPL" in params


def test_market_agent_query_scopes_to_target_parse_run_and_filing(monkeypatch):
    cursor = FakeCursor(
        _agent_columns("stock_code", "hkex_stock_code"),
        [
            {
                "company_id": "HK:00700",
                "company_ticker": "00700",
                "company_name": "Tencent",
                "filing_id": "HK:00700:2024-annual",
                "fiscal_year": 2024,
                "parse_run_id": "parse-old",
                "canonical_name": "revenue",
                "canonical_label": "revenue",
                "item_name": "Revenues",
                "period_key": "2024-12-31",
                "value": "600000",
            },
            {
                "company_id": "HK:00700",
                "company_ticker": "00700",
                "company_name": "Tencent",
                "filing_id": "HK:00700:2025-annual",
                "fiscal_year": 2025,
                "parse_run_id": "parse-target",
                "canonical_name": "revenue",
                "canonical_label": "revenue",
                "item_name": "Revenues",
                "period_key": "2025-12-31",
                "value": "751766",
            },
        ],
    )
    monkeypatch.setattr(api, "get_market_connection", lambda _market: FakeConnection(cursor))

    result = api.query_market_agent_view_result(
        "港股00700收入",
        {
            "market": "HK",
            "query_type": "company_all",
            "parse_run_id": "parse-target",
            "filing_id": "HK:00700:2025-annual",
        },
        {
            "parse_run_id": "parse-old",
            "filing_id": "HK:00700:2024-annual",
        },
        limit=5,
    )

    assert result is not None
    assert [row["parse_run_id"] for row in result["rows"]] == ["parse-target"]
    assert result["parsed"]["parse_run_id"] == "parse-target"
    assert result["parsed"]["filing_id"] == "HK:00700:2025-annual"
    final_sql, params = cursor.queries[-1]
    assert "parse_run_id = %s" in final_sql
    assert "filing_id = %s" in final_sql
    assert params[:2] == ("parse-target", "HK:00700:2025-annual")


def test_market_inference_handles_us_wiki_path_and_does_not_match_second():
    assert api.infer_market_from_query_text("", {"dir": "/home/x/data/wiki/us/companies/AAPL-Apple"}) == "US"
    assert api.infer_market_from_query_text("show me the second revenue line", None) is None


def test_agent_fact_from_market_view_preserves_evidence_fields():
    fact = api.agent_fact_from_row(
        {
            "source_table": "sec_us.v_agent_financial_facts",
            "company_id": "US:CIK0000320193",
            "filing_id": "US:0000320193:0000320193-25-000079",
            "parse_run_id": "parse-us",
            "canonical_name": "revenue",
            "item_name_raw": "Net sales",
            "period_key": "2025-09-27",
            "value": "416161000000",
            "raw_value": "416,161",
            "unit": "iso4217:USD",
            "currency": "USD",
            "evidence_id": "ev-us-revenue",
            "evidence_page_number": 42,
            "evidence_table_index": 7,
            "evidence_bbox": [10, 20, 110, 45],
            "quote_text": "Net sales 416,161",
            "source_url": "https://www.sec.gov/example",
            "wiki_package_path": "data/wiki/us/companies/AAPL-Apple/reports/2025-10k",
        },
        source_type="postgresql_agent_view",
    )

    assert fact == {
        "market": "US",
        "schema": "sec_us",
        "company_id": "US:CIK0000320193",
        "filing_id": "US:0000320193:0000320193-25-000079",
        "parse_run_id": "parse-us",
        "metric_name": "Net sales",
        "canonical_name": "revenue",
        "period": "2025-09-27",
        "value": "416161000000",
        "raw_value": "416,161",
        "unit": "iso4217:USD",
        "currency": "USD",
        "source_page": 42,
        "table_index": 7,
        "bbox": [10, 20, 110, 45],
        "evidence_id": "ev-us-revenue",
        "quote": "Net sales 416,161",
        "source_url": "https://www.sec.gov/example",
        "wiki_report_path": "data/wiki/us/companies/AAPL-Apple/reports/2025-10k",
        "source_type": "postgresql_agent_view",
    }


def test_agent_fact_from_legacy_pdf2md_row_uses_same_contract():
    fact = api.agent_fact_from_row(
        {
            "source_table": "pdf2md.financial_income_statement_items",
            "task_id": "task-cn",
            "company_id": "CN:000333",
            "stock_code": "000333",
            "stock_name": "美的集团",
            "report_year": 2025,
            "period_key": "2025",
            "item_name": "营业收入",
            "canonical_name": "revenue",
            "value": "409084000000",
            "raw_value": "4090.84亿元",
            "unit": "CNY",
            "currency": "CNY",
            "source_page_number": 12,
            "source_table_index": 3,
        }
    )

    assert fact["market"] == "CN"
    assert fact["schema"] == "pdf2md"
    assert fact["company_id"] == "CN:000333"
    assert fact["metric_name"] == "营业收入"
    assert fact["canonical_name"] == "revenue"
    assert fact["period"] == "2025"
    assert fact["source_page"] == 12
    assert fact["table_index"] == 3
    assert fact["bbox"] is None
    assert fact["source_type"] == "postgresql"
    assert set(fact) == set(api.AGENT_FINANCIAL_FACT_FIELDS)
    assert fact["filing_id"] is None
    assert fact["evidence_id"] is None


def test_agent_financial_fact_contract_doc_matches_code_fields():
    repo_root = Path(api.__file__).resolve().parents[2]
    contract_path = repo_root / "docs" / "architecture" / "agent-financial-query-contract.md"
    contract_text = contract_path.read_text(encoding="utf-8")
    contract_section = contract_text.split("## AgentFinancialFact", 1)[1].split("## Runtime Policy", 1)[0]
    documented_fields = tuple(
        match.group(1)
        for match in re.finditer(r"^\| `([^`]+)` \|", contract_section, flags=re.MULTILINE)
    )

    assert documented_fields == api.AGENT_FINANCIAL_FACT_FIELDS


def test_query_endpoint_prefers_market_agent_view(monkeypatch):
    def fake_market_view(query_text, parsed=None, company_hint=None, *, limit=20, market=None):
        assert query_text == "港股00700 2025收入是多少？"
        assert limit == 5
        row = {
            "source_table": "pdf2md_hk.v_agent_financial_facts",
            "company_id": "HK:00700",
            "company_name": "Tencent",
            "parse_run_id": "parse-hk",
            "canonical_name": "revenue",
            "item_name": "Revenues",
            "period_key": "2025-12-31",
            "value": "751766",
            "unit": "RMB million",
            "currency": "RMB",
        }
        return {
            "question": query_text,
            "parsed": {"market": "HK", "resolved_company_id": "HK:00700"},
            "source_tables": ["pdf2md_hk.v_agent_financial_facts"],
            "rows": [row],
            "agent_facts": api.agent_facts_from_rows([row], source_type="postgresql_agent_view"),
        }

    monkeypatch.setattr(api, "query_market_agent_view_result", fake_market_view)
    monkeypatch.setattr(api, "get_connection", lambda: (_ for _ in ()).throw(AssertionError("legacy connection should not be used")))

    response = api.query_financial_data(api.QueryRequest(question="港股00700 2025收入是多少？", use_hermes=False, limit=5))

    assert response.source_tables == ["pdf2md_hk.v_agent_financial_facts"]
    assert response.row_count == 1
    assert response.agent_facts[0]["source_type"] == "postgresql_agent_view"
    assert response.agent_facts[0]["market"] == "HK"


def test_query_rest_endpoint_returns_agent_facts_for_market_agent_view(monkeypatch):
    def fake_market_view(query_text, parsed=None, company_hint=None, *, limit=20, market=None):
        row = {
            "source_table": "pdf2md_hk.v_agent_financial_facts",
            "company_id": "HK:00700",
            "company_name": "Tencent",
            "filing_id": "HK:00700:2025-annual",
            "parse_run_id": "parse-hk",
            "canonical_name": "revenue",
            "item_name": "Revenues",
            "period_key": "2025-12-31",
            "value": "751766",
            "unit": "RMB million",
            "currency": "RMB",
            "evidence_table_index": 4,
            "quote_text": "Revenues | 751,766",
        }
        return {
            "question": query_text,
            "parsed": {"market": "HK", "resolved_company_id": "HK:00700"},
            "source_tables": ["pdf2md_hk.v_agent_financial_facts"],
            "rows": [row],
            "agent_facts": api.agent_facts_from_rows([row], source_type="postgresql_agent_view"),
        }

    monkeypatch.setattr(api, "query_market_agent_view_result", fake_market_view)
    monkeypatch.setattr(api, "get_connection", lambda: (_ for _ in ()).throw(AssertionError("legacy connection should not be used")))

    response = TestClient(api.app).post(
        "/query",
        json={"question": "港股00700 2025收入是多少？", "use_hermes": False, "limit": 5},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["source_tables"] == ["pdf2md_hk.v_agent_financial_facts"]
    assert payload["row_count"] == 1
    assert payload["agent_facts"][0]["market"] == "HK"
    assert payload["agent_facts"][0]["schema"] == "pdf2md_hk"
    assert payload["agent_facts"][0]["source_type"] == "postgresql_agent_view"
    assert payload["agent_facts"][0]["quote"] == "Revenues | 751,766"


def test_query_rest_endpoint_preserves_legacy_pdf2md_agent_fact_contract(monkeypatch):
    monkeypatch.setattr(api, "merge_parse", lambda _question, _use_hermes: {"query_type": "metric", "metric_name": "营业收入"})
    monkeypatch.setattr(api, "query_market_agent_view_result", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(api, "get_connection", lambda: FakeConnection(FakeCursor(set(), [])))
    monkeypatch.setattr(
        api,
        "resolve_company",
        lambda _cur, _parsed, _question: {
            "company_id": "CN:000333",
            "stock_code": "000333",
            "stock_name": "美的集团",
        },
    )
    monkeypatch.setattr(api, "require_company_match", lambda _parsed, _company: None)
    monkeypatch.setattr(api, "infer_metric_from_database", lambda _cur, _parsed, _company, _question: None)
    monkeypatch.setattr(
        api,
        "query_metric_from_split_tables",
        lambda _cur, _parsed, _company, _limit: (
            ["pdf2md.financial_income_statement_items"],
            [
                {
                    "source_table": "pdf2md.financial_income_statement_items",
                    "company_id": "CN:000333",
                    "stock_code": "000333",
                    "stock_name": "美的集团",
                    "report_year": 2025,
                    "period_key": "2025",
                    "item_name": "营业收入",
                    "canonical_name": "revenue",
                    "value": "409084000000",
                    "raw_value": "4090.84亿元",
                    "unit": "CNY",
                    "currency": "CNY",
                    "source_page_number": 12,
                    "source_table_index": 3,
                }
            ],
        ),
    )
    monkeypatch.setattr(api, "query_metric_from_wide", lambda *_args, **_kwargs: ([], []))

    response = TestClient(api.app).post(
        "/query",
        json={"question": "美的集团2025营业收入是多少？", "use_hermes": False, "limit": 5},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["source_tables"] == ["pdf2md.financial_income_statement_items"]
    assert payload["row_count"] == 1
    assert payload["agent_facts"][0]["market"] == "CN"
    assert payload["agent_facts"][0]["schema"] == "pdf2md"
    assert payload["agent_facts"][0]["source_type"] == "postgresql"
    assert payload["agent_facts"][0]["metric_name"] == "营业收入"
    assert set(payload["agent_facts"][0]) == set(api.AGENT_FINANCIAL_FACT_FIELDS)


def test_query_rest_endpoint_does_not_expose_postgres_operational_error(monkeypatch):
    def raise_operational_error():
        raise api.psycopg.OperationalError("could not connect to postgresql://user:super-secret@db/siq")

    monkeypatch.setattr(api, "query_market_agent_view_result", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(api, "get_connection", raise_operational_error)

    response = TestClient(api.app).post(
        "/query",
        json={"question": "查一下营业收入", "use_hermes": False, "limit": 5},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "PostgreSQL unavailable"}
    assert "super-secret" not in response.text
