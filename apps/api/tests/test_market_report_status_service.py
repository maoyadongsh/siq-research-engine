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
