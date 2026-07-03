from services import market_report_status_service as service


def test_market_ingestion_eval_report_payload_includes_markdown_only_when_present():
    report = {"summary": {"passed": 2}}

    without_markdown = service.market_ingestion_eval_report_payload(
        report=report,
        report_path="eval/report.json",
        markdown_path="eval/report.md",
    )
    assert without_markdown == {
        "ok": True,
        "report_path": "eval/report.json",
        "markdown_path": "eval/report.md",
        "report": report,
    }

    with_markdown = service.market_ingestion_eval_report_payload(
        report={},
        report_path="eval/report.json",
        markdown_path="eval/report.md",
        markdown="# Eval",
    )
    assert with_markdown["ok"] is False
    assert with_markdown["markdown"] == "# Eval"


def test_market_package_quality_payload_keeps_optional_source_map_summary():
    base = service.market_package_quality_payload(
        package_path="data/wiki/us_sec/AAPL/package",
        manifest={"filing_id": "AAPL-10K"},
        quality={"overall_status": "pass"},
        financial_checks={"overall_status": "warning"},
    )
    with_source_map = service.market_package_quality_payload(
        package_path="data/wiki/us_sec/AAPL/package",
        manifest={"filing_id": "AAPL-10K"},
        quality={"overall_status": "pass"},
        financial_checks={"overall_status": "warning"},
        source_map={"entries": [{"evidence_id": "e1"}, {"evidence_id": "e2"}]},
        include_source_map_summary=True,
    )
    malformed_source_map = service.market_package_quality_payload(
        package_path="data/wiki/us_sec/AAPL/package",
        manifest={},
        quality={},
        financial_checks={},
        source_map={"entries": {"bad": "shape"}},
        include_source_map_summary=True,
    )

    assert base == {
        "ok": True,
        "package_path": "data/wiki/us_sec/AAPL/package",
        "manifest": {"filing_id": "AAPL-10K"},
        "quality": {"overall_status": "pass"},
        "financial_checks": {"overall_status": "warning"},
    }
    assert with_source_map["source_map_summary"] == {"evidence": 2}
    assert malformed_source_map["source_map_summary"] == {"evidence": 0}


def test_latest_case_item_for_ticker_selects_latest_case_and_tolerates_malformed_inputs():
    case_set = {
        "items": [
            {
                "ticker": "AAPL",
                "filing_date": "2025-10-31",
                "period_end": "2025-09-27",
                "package_path": "old",
            },
            {
                "ticker": "msft",
                "filing_date": "2025-10-30",
                "period_end": "2025-06-30",
                "package_path": "ignored",
            },
            {
                "ticker": " aapl ",
                "filing_date": "2025-10-31",
                "period_end": "2025-12-31",
                "package_path": "latest-period",
            },
            {
                "ticker": "AAPL",
                "filing_date": "2026-01-15",
                "period_end": "",
                "package_path": "latest-filing",
            },
            "ignored",
        ],
    }

    assert service.latest_case_item_for_ticker(case_set, "aapl")["package_path"] == "latest-filing"
    assert service.latest_case_item_for_ticker(case_set, "MSFT")["package_path"] == "ignored"
    assert service.latest_case_item_for_ticker({"items": [{"ticker": " tsla ", "package_path": "padded"}]}, "TSLA")[
        "package_path"
    ] == "padded"
    assert service.latest_case_item_for_ticker(case_set, "TSLA") is None
    assert service.latest_case_item_for_ticker({"items": {"bad": "shape"}}, "AAPL") is None
    assert service.latest_case_item_for_ticker(["not-a-dict"], "AAPL") is None
    assert service.latest_case_item_for_ticker(case_set, " ") is None


def test_us_sec_case_set_status_payload_summarizes_quality_counts_and_ingest_report():
    payload = service.us_sec_case_set_status_payload(
        case_set={
            "items": [
                {
                    "ticker": "AAPL",
                    "company_name": "Apple Inc.",
                    "fiscal_year": 2025,
                    "period_end": "2025-09-27",
                    "filing_date": "2025-10-31",
                    "quality_status": "pass",
                    "quality_summary": {
                        "xbrl_fact_count": 10,
                        "normalized_metric_count": 4,
                        "section_count": 2,
                        "table_count": 3,
                    },
                    "package_path": "data/wiki/us_sec/AAPL/package",
                },
                {
                    "ticker": "MSFT",
                    "quality_status": "",
                    "quality_summary": {"xbrl_fact_count": 5},
                    "package_path": "data/wiki/us_sec/MSFT/package",
                },
                "ignored",
            ],
        },
        ingest_report={
            "generated_at": "2026-07-03T00:00:00Z",
            "summary": {"inserted": 7},
            "package_count": 2,
            "collection": "siq_documents",
            "batch_tag": "market-evidence",
            "extra": "ignored",
        },
        case_set_path="/tmp/case_set.json",
        ingest_report_path="/tmp/ingest_report.json",
    )

    assert payload["case_set_path"] == "/tmp/case_set.json"
    assert payload["ingest_report_path"] == "/tmp/ingest_report.json"
    assert payload["company_count"] == 2
    assert payload["quality"] == {"pass": 1, "unknown": 1}
    assert payload["counts"] == {
        "xbrl_fact_count": 15,
        "normalized_metric_count": 4,
        "section_count": 2,
        "table_count": 3,
    }
    assert payload["items"] == [
        {
            "ticker": "AAPL",
            "company_name": "Apple Inc.",
            "fiscal_year": 2025,
            "period_end": "2025-09-27",
            "filing_date": "2025-10-31",
            "quality_status": "pass",
            "quality_summary": {
                "xbrl_fact_count": 10,
                "normalized_metric_count": 4,
                "section_count": 2,
                "table_count": 3,
            },
            "package_path": "data/wiki/us_sec/AAPL/package",
        },
        {
            "ticker": "MSFT",
            "company_name": None,
            "fiscal_year": None,
            "period_end": None,
            "filing_date": None,
            "quality_status": "unknown",
            "quality_summary": {"xbrl_fact_count": 5},
            "package_path": "data/wiki/us_sec/MSFT/package",
        },
    ]
    assert payload["ingest_report"] == {
        "generated_at": "2026-07-03T00:00:00Z",
        "summary": {"inserted": 7},
        "package_count": 2,
        "collection": "siq_documents",
        "batch_tag": "market-evidence",
    }


def test_us_sec_case_set_status_payload_tolerates_malformed_inputs():
    payload = service.us_sec_case_set_status_payload(
        case_set={"items": {"not": "a-list"}},
        ingest_report=[],
        case_set_path="/tmp/case_set.json",
        ingest_report_path="/tmp/ingest_report.json",
    )

    assert payload["company_count"] == 0
    assert payload["quality"] == {}
    assert payload["counts"] == {
        "xbrl_fact_count": 0,
        "normalized_metric_count": 0,
        "section_count": 0,
        "table_count": 0,
    }
    assert payload["items"] == []
    assert payload["ingest_report"] == {}


def test_us_sec_case_set_status_payload_tolerates_bad_count_values():
    payload = service.us_sec_case_set_status_payload(
        case_set={
            "items": [
                {
                    "ticker": "AAPL",
                    "quality_status": "warning",
                    "quality_summary": {
                        "xbrl_fact_count": "n/a",
                        "normalized_metric_count": {"bad": "value"},
                        "section_count": "2",
                        "table_count": -1,
                    },
                },
            ],
        },
        ingest_report={},
        case_set_path="/tmp/case_set.json",
        ingest_report_path="/tmp/ingest_report.json",
    )

    assert payload["company_count"] == 1
    assert payload["quality"] == {"warning": 1}
    assert payload["counts"] == {
        "xbrl_fact_count": 0,
        "normalized_metric_count": 0,
        "section_count": 2,
        "table_count": 0,
    }
