from __future__ import annotations

from schemas import ChatRequest


def test_chat_context_preserves_formal_research_selectors_and_source_report() -> None:
    payload = {
        "message": "生成分析报告",
        "context": {
            "market": "US",
            "company_key": "rk1_opaque",
            "report_id": "2025-10-K-aapl",
            "upstream_analysis_artifact_id": "analysis-aapl-v1",
            "company": {
                "company_key": "rk1_opaque",
                "market": "US",
                "company_id": "US:0000320193",
                "code": "AAPL",
                "name": "Apple Inc.",
            },
            "source_report": {
                "report_id": "2025-10-K-aapl",
                "filing_id": "US:0000320193:aapl-filing",
                "parse_run_id": "run-aapl",
                "source_family": "sec_ixbrl",
                "period_end": "2025-09-27",
                "filename": "aapl-20250927.htm",
                "baseline_analysis_artifact_id": "analysis-aapl-v1",
            },
            "research_identity": {
                "market": "US",
                "company_id": "US:0000320193",
                "filing_id": "US:0000320193:aapl-filing",
                "parse_run_id": "run-aapl",
            },
            "research_target": {
                "schema_version": "siq_research_target_v1",
                "company_key": "rk1_opaque",
                "company_wiki_id": "AAPL-Apple-Inc",
                "display_code": "AAPL",
                "display_name": "Apple Inc.",
                "research_identity": {
                    "market": "US",
                    "company_id": "US:0000320193",
                    "filing_id": "US:0000320193:aapl-filing",
                    "parse_run_id": "run-aapl",
                },
                "source_report": {
                    "report_id": "2025-10-K-aapl",
                    "source_family": "sec_ixbrl",
                    "document_format": "ixbrl_html",
                    "report_type": "annual",
                    "period_end": "2025-09-27",
                },
            },
        },
    }

    context = ChatRequest.model_validate(payload).context
    assert context is not None
    dumped = context.model_dump(exclude_none=True)

    assert dumped["company"]["company_key"] == "rk1_opaque"
    assert dumped["source_report"]["baseline_analysis_artifact_id"] == "analysis-aapl-v1"
    assert dumped["source_report"]["filename"] == "aapl-20250927.htm"
    assert dumped["research_target"]["source_report"]["document_format"] == "ixbrl_html"
    assert dumped["upstream_analysis_artifact_id"] == "analysis-aapl-v1"


def test_chat_context_keeps_forward_compatible_selector_extensions() -> None:
    request = ChatRequest.model_validate(
        {
            "message": "track",
            "context": {
                "market": "HK",
                "company_key": "rk1_hk",
                "report_id": "2025-annual",
                "selector_contract_revision": "future-v2",
                "source_report": {
                    "report_id": "2025-annual",
                    "future_locator_policy": "pdf-v2",
                },
            },
        }
    )

    dumped = request.context.model_dump(exclude_none=True)
    assert dumped["selector_contract_revision"] == "future-v2"
    assert dumped["source_report"]["future_locator_policy"] == "pdf-v2"
