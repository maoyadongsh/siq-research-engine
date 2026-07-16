from __future__ import annotations

import json
from pathlib import Path
from typing import Any

MARKET_SUBDIR = {"CN": "", "HK": "hk", "US": "us", "EU": "eu", "KR": "kr", "JP": "jp"}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def market_root(wiki_root: Path, market: str) -> Path:
    subdir = MARKET_SUBDIR[market]
    return wiki_root / subdir if subdir else wiki_root


def add_company(
    wiki_root: Path,
    *,
    market: str,
    code: str,
    name: str,
    company_id: str,
    report_id: str,
    filing_id: str,
    parse_run_id: str,
    quality_status: str = "pass",
    source_family: str | None = None,
    document_format: str | None = None,
    form_type: str | None = None,
    report_type: str = "annual",
    fiscal_year: int = 2025,
    fiscal_period: str | None = None,
    period_start: str | None = None,
    period_end: str = "2025-12-31",
    published_at: str = "2026-03-01",
    accounting_standard: str | None = None,
    reporting_currency: str | None = None,
    reporting_scale: int | None = None,
    industry_profile: str = "industrial",
    unsafe_document_path: str | None = None,
) -> Path:
    root = market_root(wiki_root, market)
    wiki_id = f"{code}-{name.replace(' ', '-')}"
    company_dir = root / "companies" / wiki_id
    report_dir = company_dir / "reports" / report_id
    family = source_family or ("sec_ixbrl" if market == "US" else "pdf_market")
    doc_format = document_format or ("ixbrl_html" if family == "sec_ixbrl" else "pdf")
    currency = reporting_currency or {
        "CN": "CNY",
        "HK": "HKD",
        "US": "USD",
        "EU": "EUR",
        "KR": "KRW",
        "JP": "JPY",
    }[market]
    scale = reporting_scale or {
        "CN": 1_000_000,
        "HK": 1_000_000,
        "US": 1_000_000,
        "EU": 1_000_000,
        "KR": 1_000_000_000,
        "JP": 1_000_000_000,
    }[market]
    standard = accounting_standard or ("US_GAAP" if market == "US" else "IFRS")
    effective_fiscal_period = fiscal_period or ("FY" if report_type == "annual" else "Q1")
    effective_period_start = period_start or (
        f"{fiscal_year - 1}-10-01" if form_type == "10-K" else f"{fiscal_year}-01-01"
    )
    report = {
        "report_id": report_id,
        "filing_id": filing_id,
        "parse_run_id": parse_run_id,
        "report_type": report_type,
        "form": form_type,
        "fiscal_year": fiscal_year,
        "fiscal_period": effective_fiscal_period,
        "period_end": period_end,
        "published_at": published_at,
        "quality_status": quality_status,
        "status": "ready" if quality_status != "fail" else "failed",
        "task_id": parse_run_id,
    }
    company_path = company_dir / "company.json"
    company_payload = (
        json.loads(company_path.read_text(encoding="utf-8"))
        if company_path.is_file()
        else {}
    )
    reports = [
        item
        for item in company_payload.get("reports", [])
        if isinstance(item, dict) and item.get("report_id") != report_id
    ]
    reports.append(report)
    company_payload.update(
        {
            "market": market,
            "company_id": company_id,
            "company_wiki_id": wiki_id,
            "stock_code": code,
            "company_short_name": name,
            "industry_profile": industry_profile,
            "primary_report_id": company_payload.get("primary_report_id") or report_id,
            "reports": reports,
        }
    )
    write_json(company_path, company_payload)
    write_json(
        company_dir / "_index.json",
        {
            "market": market,
            "company_id": company_id,
            "company_wiki_id": wiki_id,
            "primary_report_id": company_payload["primary_report_id"],
            "report_ids": [item["report_id"] for item in reports],
        },
    )
    catalog_path = root / "_meta" / "company_catalog.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8")) if catalog_path.is_file() else {"companies": []}
    catalog_entry = {
        "market": market,
        "company_id": company_id,
        "company_wiki_id": wiki_id,
        "company_path": f"companies/{wiki_id}",
        "stock_code": code,
        "ticker": code,
        "company_short_name": name,
        "company_name": name,
        "industry_profile": industry_profile,
        "status": "ready",
        "report_count": len(reports),
    }
    catalog["companies"] = [
        item
        for item in catalog.get("companies", [])
        if not (
            isinstance(item, dict)
            and (
                item.get("company_id") == company_id
                or item.get("company_wiki_id") == wiki_id
            )
        )
    ]
    catalog["companies"].append(catalog_entry)
    write_json(catalog_path, catalog)
    write_json(root / "_index.json", {"market": market, "company_count": len(catalog["companies"])})
    write_json(
        root / "companies" / "_index.json",
        {
            "market": market,
            "companies": [
                {
                    "company_id": item["company_id"],
                    "company_wiki_id": item["company_wiki_id"],
                    "report_count": item["report_count"],
                }
                for item in catalog["companies"]
            ],
        },
    )
    for fact_area in ("metrics", "evidence", "semantic", "graph"):
        write_json(
            company_dir / fact_area / "_index.json",
            {
                "market": market,
                "company_id": company_id,
                "report_ids": [item["report_id"] for item in reports],
                "fact_area": fact_area,
            },
        )

    if market == "CN":
        write_json(
            report_dir / "artifact_manifest.json",
            {
                "schema_version": 1,
                "task_id": parse_run_id,
                "core": {"status": "ready", "ready": True},
            },
        )
        (report_dir / "report.md").write_text("# annual report\n", encoding="utf-8")
        write_json(
            company_dir / "metrics" / "reports" / report_id / "key_metrics.json",
            {
                "report_id": report_id,
                "filing_id": filing_id,
                "parse_run_id": parse_run_id,
                "facts": [
                    {
                        "canonical_name": "revenue",
                        "label": "Revenue",
                        "value": 1_200,
                        "currency": currency,
                        "scale": scale,
                        "period_end": period_end,
                    }
                ],
            },
        )
        write_json(
            company_dir / "evidence" / "evidence_index.json",
            {
                "report_id": report_id,
                "filing_id": filing_id,
                "parse_run_id": parse_run_id,
                "items": [{"id": "cn-1", "pdf_page_number": 8, "table_index": 1}],
            },
        )
        return company_dir

    if family == "sec_ixbrl":
        document_path = unsafe_document_path or "parser/document_full.json"
        artifacts = {
            "document_full": document_path,
            "wiki_report_complete": "sections/report_complete.md",
            "financial_data": "metrics/financial_data.json",
            "normalized_metrics": "metrics/normalized_metrics.json",
            "financial_checks": "metrics/financial_checks.json",
            "source_map": "qa/source_map.json",
            "xbrl_facts_raw": "xbrl/facts_raw.json",
            "xbrl_contexts": "xbrl/contexts.json",
            "xbrl_units": "xbrl/units.json",
            "xbrl_labels": "xbrl/labels.json",
            "table_index": "tables/table_index.json",
        }
    else:
        document_path = unsafe_document_path or "document_full.json"
        artifacts = {
            "document_full": document_path,
            "wiki_report_complete": "report.md",
            "financial_data": "metrics/financial_data.json",
            "normalized_metrics": "metrics/normalized_metrics.json",
            "source_map": "qa/source_map.json",
        }
    write_json(
        report_dir / "manifest.json",
        {
            "schema_version": "market_evidence_package_v1",
            "market": market,
            "company_id": company_id,
            "company_wiki_id": wiki_id,
            "filing_id": filing_id,
            "parse_run_id": parse_run_id,
            "report_id": report_id,
            "source_family": family,
            "source_id": "sec" if family == "sec_ixbrl" else "official_pdf",
            "document_format": doc_format,
            "report_type": report_type,
            "form": form_type,
            "fiscal_year": fiscal_year,
            "period_end": period_end,
            "published_at": published_at,
            "fiscal_period": effective_fiscal_period,
            "period_start": effective_period_start,
            "accounting_standard": standard,
            "reporting_currency": currency,
            "reporting_scale": scale,
            "industry_profile": industry_profile,
            "quality_status": quality_status,
            "source_url": "https://www.sec.gov/example.htm" if family == "sec_ixbrl" else None,
            "artifacts": artifacts,
        },
    )
    if unsafe_document_path is None:
        write_json(report_dir / document_path, {"document": "full", "blocks": []})
    (report_dir / ("sections/report_complete.md" if family == "sec_ixbrl" else "report.md")).parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    (report_dir / ("sections/report_complete.md" if family == "sec_ixbrl" else "report.md")).write_text(
        "# complete report\n",
        encoding="utf-8",
    )
    duration_days = 364 if form_type == "10-K" else 90
    raw_fact_id = f"fact-{parse_run_id}"
    context_ref = f"ctx-{parse_run_id}"
    metric_record = {
        "metric_id": f"metric-{parse_run_id}",
        "canonical_name": "revenue",
        "label": "Revenue",
        "concept": "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax" if family == "sec_ixbrl" else None,
        "value": 1_200,
        "raw_value": "1,200",
        "currency": currency,
        "unit": f"{currency} {'billion' if scale == 1_000_000_000 else 'million'}",
        "scale": scale,
        "period_start": effective_period_start,
        "period_end": period_end,
        "fiscal_year": fiscal_year,
        "fiscal_period": effective_fiscal_period,
        "accounting_standard": standard,
        "duration_days": duration_days,
        "raw_fact_id": raw_fact_id if family == "sec_ixbrl" else None,
        "raw": {"context_id": context_ref} if family == "sec_ixbrl" else {},
        "source": {
            "pdf_page_number": 8,
            "table_index": 2,
            "quote_text": "Revenue 1,200",
        },
    }
    write_json(report_dir / "metrics" / "financial_data.json", {"statements": {}})
    write_json(report_dir / "metrics" / "normalized_metrics.json", {"facts": [metric_record]})
    write_json(
        report_dir / "metrics" / "financial_checks.json",
        {
            "status": quality_status,
            "warnings": ["synthetic warning"] if quality_status == "warning" else [],
        },
    )
    source_entry = {
        "evidence_id": f"evidence-{parse_run_id}",
        "source_type": "sec_xbrl_fact" if family == "sec_ixbrl" else "pdf_statement_table",
        "target": "revenue",
        "fact_id": raw_fact_id if family == "sec_ixbrl" else None,
        "xbrl_tag": (
            "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"
            if family == "sec_ixbrl"
            else None
        ),
        "context_ref": context_ref if family == "sec_ixbrl" else None,
        "html_anchor": "F_1" if family == "sec_ixbrl" else None,
        "source_url": "https://www.sec.gov/example.htm" if family == "sec_ixbrl" else None,
        "pdf_page_number": 8 if family != "sec_ixbrl" else None,
        "table_index": 2 if family != "sec_ixbrl" else None,
        "quote_text": "Revenue 1,200",
    }
    write_json(report_dir / "qa" / "source_map.json", {"entries": [source_entry]})
    if family == "sec_ixbrl":
        write_json(
            report_dir / "xbrl" / "facts_raw.json",
            {
                "facts": [
                    {
                        "fact_id": raw_fact_id,
                        "concept": "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
                        "taxonomy": "us-gaap",
                        "value_text": "1200",
                        "context_ref": context_ref,
                        "period_start": effective_period_start,
                        "period_end": period_end,
                        "duration_days": duration_days,
                        "unit_ref": currency,
                        "html_anchor": "F_1",
                        "dimensions": {},
                    }
                ]
            },
        )
        write_json(
            report_dir / "xbrl" / "contexts.json",
            {
                "contexts": {
                    context_ref: {
                        "period_start": effective_period_start,
                        "period_end": period_end,
                        "duration_days": duration_days,
                        "dimensions": {},
                    }
                }
            },
        )
        write_json(report_dir / "xbrl" / "units.json", {"units": {currency: f"iso4217:{currency}"}})
        write_json(report_dir / "xbrl" / "labels.json", {"labels": {}})
        (report_dir / "sections" / "mda.md").write_text(
            "---\nschema_version: sec_section_v1\n---\n# MD&A\nSynthetic management discussion.\n",
            encoding="utf-8",
        )
        write_json(report_dir / "tables" / "table_index.json", {"tables": []})
    return company_dir


def build_six_market_wiki(wiki_root: Path) -> dict[str, Path]:
    primary = {
        "CN": add_company(
            wiki_root,
            market="CN",
            code="600104",
            name="上汽集团",
            company_id="600104-上汽集团",
            report_id="2025-annual",
            filing_id="CN:600104-上汽集团:2025-annual",
            parse_run_id="task-cn-600104",
            accounting_standard="CAS",
        ),
        "HK": add_company(
            wiki_root,
            market="HK",
            code="00005",
            name="HSBC HOLDINGS",
            company_id="HK:00005",
            report_id="2025-annual",
            filing_id="HK:00005:filing-2025",
            parse_run_id="run-hk-00005",
            industry_profile="bank",
            reporting_scale=1_000_000,
        ),
        "US": add_company(
            wiki_root,
            market="US",
            code="AAPL",
            name="Apple Inc",
            company_id="US:0000320193",
            report_id="2025-10-K-0000320193-25-000079",
            filing_id="US:0000320193:0000320193-25-000079",
            parse_run_id="run-us-aapl",
            source_family="sec_ixbrl",
            form_type="10-K",
            fiscal_period="FY",
            period_start="2024-09-29",
            period_end="2025-09-27",
            published_at="2025-10-31",
        ),
        "EU": add_company(
            wiki_root,
            market="EU",
            code="AI",
            name="Air Liquide",
            company_id="EU:FR:AI",
            report_id="2025-annual",
            filing_id="EU:AI:2025-annual",
            parse_run_id="run-eu-ai",
            reporting_scale=1_000_000,
        ),
        "KR": add_company(
            wiki_root,
            market="KR",
            code="005930",
            name="Samsung Electronics",
            company_id="KR:005930",
            report_id="2025-annual",
            filing_id="KR:005930:2025-annual",
            parse_run_id="run-kr-005930",
            reporting_scale=1_000_000_000,
        ),
        "JP": add_company(
            wiki_root,
            market="JP",
            code="7203",
            name="Toyota Motor",
            company_id="JP:JP:7203",
            report_id="2025-annual-S100TEST",
            filing_id="JP:S100TEST",
            parse_run_id="run-jp-7203",
            quality_status="warning",
            reporting_scale=1_000_000_000,
        ),
    }

    add_company(
        wiki_root,
        market="US",
        code="AAPL",
        name="Apple Inc",
        company_id="US:0000320193",
        report_id="2025-10-Q-0000320193-25-000045",
        filing_id="US:0000320193:0000320193-25-000045",
        parse_run_id="run-us-aapl-q3",
        source_family="sec_ixbrl",
        form_type="10-Q",
        report_type="quarterly",
        fiscal_year=2025,
        fiscal_period="Q3",
        period_start="2025-03-30",
        period_end="2025-06-28",
        published_at="2025-08-01",
    )
    berkshire_dir = add_company(
        wiki_root,
        market="US",
        code="BRK.B",
        name="Berkshire Hathaway",
        company_id="US:0001067983",
        report_id="2025-10-K-0001067983-26-000009",
        filing_id="US:0001067983:0001067983-26-000009",
        parse_run_id="run-us-brk-b",
        source_family="sec_ixbrl",
        form_type="10-K",
        fiscal_period="FY",
        period_start="2025-01-01",
        period_end="2025-12-31",
        published_at="2026-02-28",
        reporting_scale=1_000_000_000,
        industry_profile="insurance",
    )
    (berkshire_dir / "analysis").mkdir(parents=True, exist_ok=True)
    (berkshire_dir / "analysis" / "README.md").write_text(
        "# Derived analysis workspace placeholder\n",
        encoding="utf-8",
    )
    add_company(
        wiki_root,
        market="KR",
        code="600104",
        name="Collision Holdings",
        company_id="KR:600104",
        report_id="2025-annual",
        filing_id="KR:600104:2025-annual",
        parse_run_id="run-kr-600104",
        reporting_scale=1_000_000_000,
    )
    add_company(
        wiki_root,
        market="JP",
        code="9999",
        name="Failed Co",
        company_id="JP:JP:9999",
        report_id="2025-fail",
        filing_id="JP:FAIL:2025",
        parse_run_id="run-jp-fail",
        quality_status="fail",
        reporting_scale=1_000_000_000,
    )
    return primary
