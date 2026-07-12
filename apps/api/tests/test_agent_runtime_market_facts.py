import json

from services import agent_chat_runtime as runtime, agent_runtime_market_facts as market_facts


def test_normalize_us_statement_items_preserves_period_currency_and_sec_evidence():
    payload = {
        "statements": [
            {
                "statement_type": "income_statement",
                "unit": "USD",
                "scale": "1",
                "items": [
                    {
                        "name": "Revenue",
                        "canonical_name": "operating_revenue",
                        "values": {"2025-09-27": "416161000000"},
                        "raw_values": {"2025-09-27": "416161000000"},
                        "sources": {
                            "2025-09-27": {
                                "source_type": "sec_xbrl_fact",
                                "url": "https://www.sec.gov/example.htm",
                                "anchor": "f-100",
                                "xbrl_tag": "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
                                "html_snippet": "<ix:nonFraction>416,161</ix:nonFraction>",
                            }
                        },
                        "periods": {"2025-09-27": {"fiscal_year": 2025}},
                    }
                ],
            }
        ]
    }

    records = market_facts.normalize_statement_records(payload)

    assert records == [
        {
            "metric_key": "operating_revenue",
            "canonical_name": "operating_revenue",
            "metric_name": "Revenue",
            "statement_type": "income_statement",
            "scope": None,
            "period": "2025-09-27",
            "fiscal_year": 2025,
            "raw_value": "416161000000",
            "normalized_value": "416161000000",
            "unit": "USD",
            "currency": None,
            "scale": "1",
            "source": {
                "source_type": "sec_xbrl_fact",
                "url": "https://www.sec.gov/example.htm",
                "anchor": "f-100",
                "xbrl_tag": "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
                "html_snippet": "<ix:nonFraction>416,161</ix:nonFraction>",
                "pdf_page_number": None,
                "quote_text": "<ix:nonFraction>416,161</ix:nonFraction>",
                "source_url": "https://www.sec.gov/example.htm",
                "source_anchor": "f-100",
            },
        }
    ]


def test_validation_summary_supports_wrapped_and_direct_market_checks():
    wrapped = market_facts.validation_summary(
        {"financial_checks": {"overall_status": "warning", "summary": {"pass": 10, "warning": 1}}}
    )
    direct = market_facts.validation_summary(
        {"overall_status": "pass", "summary": {"pass": 12, "fail": 0}, "market": "US"}
    )

    assert wrapped["status"] == "warning"
    assert wrapped["summary"] == {"pass": 10, "warning": 1}
    assert direct["status"] == "pass"
    assert direct["summary"] == {"pass": 12, "fail": 0}
    assert direct["market"] == "US"


def test_flat_market_record_cleans_heading_leakage_but_preserves_raw_unit():
    records = market_facts.normalize_statement_records(
        {
            "data": {
                "metrics": [
                    {
                        "metric_key": "operating_revenue",
                        "metric_name": "Revenues",
                        "statement_type": "income_statement",
                        "period": "2025-12-31",
                        "raw_value": "751,766",
                        "unit": "RMB’Million 2024RMB’Million Reven",
                    }
                ]
            }
        }
    )

    assert records[0]["unit_hint"] == "RMB million"
    assert records[0]["raw_unit"] == "RMB’Million 2024RMB’Million Reven"
    assert records[0]["unit"] == "RMB’Million 2024RMB’Million Reven"


def test_live_us_wiki_question_uses_same_core_fact_path_with_sec_anchor(tmp_path, monkeypatch):
    wiki_root = tmp_path / "wiki"
    us_root = wiki_root / "us"
    company_dir = us_root / "companies" / "AAPL-Apple-Inc"
    report_id = "2025-10-K"
    report_dir = company_dir / "reports" / report_id
    metrics_dir = report_dir / "metrics"
    metrics_dir.mkdir(parents=True)
    (us_root / "_meta").mkdir(parents=True)
    (us_root / "_meta" / "company_catalog.json").write_text(
        json.dumps(
            {
                "market": "US",
                "companies": [
                    {
                        "market": "US",
                        "company_id": "US:0000320193",
                        "company_wiki_id": "AAPL-Apple-Inc",
                        "ticker": "AAPL",
                        "company_name": "Apple Inc.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (company_dir / "company.json").write_text(
        json.dumps(
            {
                "market": "US",
                "company_id": "US:0000320193",
                "company_wiki_id": "AAPL-Apple-Inc",
                "ticker": "AAPL",
                "company_name": "Apple Inc.",
                "primary_report_id": report_id,
                "reports": [{"report_id": report_id, "filing_id": "US:AAPL:2025"}],
            }
        ),
        encoding="utf-8",
    )
    (report_dir / "manifest.json").write_text(
        json.dumps(
            {
                "market": "US",
                "company_id": "US:0000320193",
                "filing_id": "US:AAPL:2025",
                "parse_run_id": "parse-us-2025",
                "report_id": report_id,
                "quality_status": "pass",
            }
        ),
        encoding="utf-8",
    )
    (metrics_dir / "financial_data.json").write_text(
        json.dumps(
            {
                "market": "US",
                "statements": [
                    {
                        "statement_type": "income_statement",
                        "unit": "USD",
                        "items": [
                            {
                                "name": "Revenue",
                                "canonical_name": "operating_revenue",
                                "values": {"2025-09-27": "416161000000", "2024-09-28": "391035000000"},
                                "raw_values": {"2025-09-27": "416161000000", "2024-09-28": "391035000000"},
                                "sources": {
                                    "2025-09-27": {
                                        "source_type": "sec_xbrl_fact",
                                        "url": "https://www.sec.gov/example.htm",
                                        "anchor": "f-100",
                                        "xbrl_tag": "us-gaap:Revenue",
                                        "html_snippet": "<ix:nonFraction>416,161</ix:nonFraction>",
                                    }
                                },
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (metrics_dir / "financial_checks.json").write_text(
        json.dumps({"market": "US", "overall_status": "pass", "summary": {"pass": 12, "fail": 0}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(runtime, "WIKI_ROOT", wiki_root)
    monkeypatch.setattr(runtime, "PROJECT_WIKI_ROOT", wiki_root)
    monkeypatch.setattr(runtime, "ASSISTANT_WIKI_ROOT", wiki_root)
    monkeypatch.setattr(runtime, "WIKI_FALLBACK_ROOTS", ())
    monkeypatch.setattr(runtime, "_load_local_citation_module", lambda: None)

    result = runtime._three_statement_core_result("US Apple Inc revenue")
    rendered = runtime.build_three_statement_core_context("US Apple Inc revenue")
    resolved_context = runtime._resolved_research_context("US Apple Inc revenue")

    assert result is not None
    assert result["market"] == "US"
    assert result["company_id"] == "US:0000320193"
    assert result["filing_id"] == "US:AAPL:2025"
    assert result["parse_run_id"] == "parse-us-2025"
    assert result["validation"]["status"] == "pass"
    assert result["rows"][0]["raw_value"] == "416161000000"
    assert result["rows"][0]["source_anchor"] == "f-100"
    assert rendered is not None
    assert "validation_status=pass" in rendered
    assert "evidence_source_type=sec_xbrl_fact" in rendered
    assert "[打开披露原文](https://www.sec.gov/example.htm#f-100)" in rendered
    assert resolved_context["research_identity"] == {
        "market": "US",
        "company_id": "US:0000320193",
        "filing_id": "US:AAPL:2025",
        "parse_run_id": "parse-us-2025",
    }
    assert resolved_context["postgres"]["market"] == "US"
    assert resolved_context["postgres"]["parse_run_id"] == "parse-us-2025"


def test_failed_market_validation_blocks_core_rows_and_primary_supplement():
    result = {
        "company_name": "Example Corp",
        "market": "EU",
        "company_id": "EU:EXAMPLE",
        "report_id": "2025-annual",
        "validation_file": "/wiki/validation.json",
        "validation": {"status": "fail"},
        "validation_blocked": True,
        "blocked_row_count": 8,
        "rows": [],
    }

    rendered = runtime._render_three_statement_context(result)

    assert "财务事实质量门禁阻断" in rendered
    assert "已阻断 8 条" in rendered
    assert "不得将其作为确定性数字回答" in rendered
    assert runtime._render_three_statement_primary_data_supplement(result) is None
