#!/usr/bin/env python3
"""
Run 100 natural-language test cases against financial_query_api.

The cases are generated from the current database contents so they stay useful
after re-importing a different 10-sample set.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient


ROOT = Path("/home/maoyd")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CONFIG_DIR = Path("/home/maoyd/finance_evidence_poc/DB/DML")
if str(CONFIG_DIR) not in sys.path:
    sys.path.insert(0, str(CONFIG_DIR))

import postgresql_connect as pg
from DB.PROGRAM.financial_query_api import app


@dataclass
class Case:
    name: str
    question: str
    expect_status: int = 200
    expect_rows: bool = True
    expect_source_contains: str | None = None
    expect_company: str | None = None
    expect_metric_contains: str | None = None


def fetch_seed_data() -> tuple[list[str], dict[str, list[str]]]:
    with pg.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT stock_name FROM pdf2md.companies ORDER BY stock_name")
            companies = [row["stock_name"] for row in cur.fetchall()]
            metrics: dict[str, list[str]] = {}
            for table, column in (
                ("financial_balance_sheet_items", "item_name"),
                ("financial_income_statement_items", "item_name"),
                ("financial_cash_flow_statement_items", "item_name"),
            ):
                cur.execute(
                    f"""
                    SELECT DISTINCT {column} AS name
                    FROM pdf2md.{table}
                    WHERE {column} IS NOT NULL
                    ORDER BY {column}
                    LIMIT 20
                    """
                )
                metrics[table] = [row["name"] for row in cur.fetchall()]
    if not companies:
        raise RuntimeError("No companies found. Import sample data first.")
    return companies, metrics


def fuzzy_company(name: str) -> str:
    compact = name.replace("_", "")
    if len(compact) <= 2:
        return compact
    if compact.endswith("证券"):
        return compact[:-2]
    return compact[:2]


def build_cases() -> list[Case]:
    companies, metrics = fetch_seed_data()
    cases: list[Case] = []

    statement_specs = [
        ("资产负债表", "pdf2md.financial_balance_sheet_items"),
        ("利润表", "pdf2md.financial_income_statement_items"),
        ("现金流量表", "pdf2md.financial_cash_flow_statement_items"),
    ]
    for company in companies:
        for statement_name, source_table in statement_specs:
            cases.append(Case(
                name=f"{company}-{statement_name}-full",
                question=f"查询{company}2025年{statement_name}数据",
                expect_source_contains=source_table,
                expect_company=company,
            ))
            cases.append(Case(
                name=f"{company}-{statement_name}-fuzzy-company",
                question=f"给我看{fuzzy_company(company)}2025年的{statement_name}",
                expect_source_contains=source_table,
                expect_company=company,
            ))

    metric_aliases = [
        ("营业总收入", "营业总收入"),
        ("营收", "营业总收入"),
        ("基本每股收益", "基本每股收益"),
        ("EPS", "基本每股收益"),
        ("货币资金", "货币资金"),
        ("总资产", "资产总计"),
        ("经营现金流", "经营活动产生的现金流量净额"),
        ("现金净增加额", "现金及现金等价物净增加额"),
    ]
    for company in companies:
        for alias, expected in metric_aliases:
            cases.append(Case(
                name=f"{company}-{alias}-metric",
                question=f"查询{company}2025年{alias}指标",
                expect_source_contains="pdf2md.",
                expect_company=company,
                expect_metric_contains=expected,
            ))

    for company in companies:
        cases.append(Case(
            name=f"{company}-balance-date",
            question=f"{company}2025-12-31资产负债表",
            expect_source_contains="pdf2md.financial_balance_sheet_items",
            expect_company=company,
        ))
        cases.append(Case(
            name=f"{company}-income-english",
            question=f"{company} 2025 income statement total_operating_revenue",
            expect_source_contains="pdf2md.financial_income_statement_items",
            expect_company=company,
        ))

    dynamic_metrics = []
    for table, names in metrics.items():
        for name in names[:4]:
            dynamic_metrics.append((table, name))
    for index, (table, metric) in enumerate(dynamic_metrics[:20]):
        company = companies[index % len(companies)]
        cases.append(Case(
            name=f"dynamic-{index + 1}",
            question=f"请查询{company}2025年{metric}",
            expect_source_contains="pdf2md.",
            expect_company=company,
            expect_metric_contains=metric,
        ))

    cases.append(Case(
        name="unknown-company-no-rows",
        question="查询不存在公司2025年利润表营业总收入",
        expect_rows=False,
    ))
    cases.append(Case(
        name="missing-statement-400",
        question="查询信达证券2025年报表数据",
        expect_status=400,
        expect_rows=False,
    ))

    return cases[:100]


def payload_text(row: dict[str, Any]) -> str:
    return json.dumps(row, ensure_ascii=False, default=str)


def assert_case(case: Case, response: Any) -> str | None:
    if response.status_code != case.expect_status:
        return f"status {response.status_code} != {case.expect_status}: {response.text[:300]}"
    if response.status_code != 200:
        return None
    data = response.json()
    rows = data.get("rows") or []
    if case.expect_rows and not rows:
        return "expected rows, got 0"
    if not case.expect_rows and rows:
        return f"expected no rows, got {len(rows)}"
    if case.expect_source_contains:
        sources = " ".join(data.get("source_tables") or [])
        row_sources = " ".join(str(row.get("source_table")) for row in rows)
        if case.expect_source_contains not in f"{sources} {row_sources}":
            return f"source missing {case.expect_source_contains}"
    if case.expect_company and rows:
        if not any(row.get("stock_name") == case.expect_company for row in rows):
            return f"company missing {case.expect_company}"
    if case.expect_metric_contains and rows:
        text = " ".join(payload_text(row) for row in rows)
        if case.expect_metric_contains not in text:
            return f"metric text missing {case.expect_metric_contains}"
    if rows and not all(row.get("source_table") for row in rows):
        return "some rows missing source_table"
    return None


def main() -> int:
    client = TestClient(app)
    cases = build_cases()
    failures = []
    for index, case in enumerate(cases, start=1):
        response = client.post("/query", json={"question": case.question, "use_hermes": False, "limit": 20})
        error = assert_case(case, response)
        if error:
            failures.append({"index": index, "name": case.name, "question": case.question, "error": error})

    print(json.dumps({
        "total": len(cases),
        "passed": len(cases) - len(failures),
        "failed": len(failures),
        "failures": failures[:20],
    }, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
