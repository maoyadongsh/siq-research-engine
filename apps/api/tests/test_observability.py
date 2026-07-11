import json
import logging

from fastapi.testclient import TestClient

import main
from services.observability import (
    REQUEST_ID_HEADER,
    current_request_id,
    emit_json_log,
    metrics_snapshot,
    normalize_request_id,
    record_answer_audit_observation,
    record_background_job_final_state,
    record_frontend_pipeline_job_failure,
    redact_sensitive,
    record_ingestion_duration,
    record_ingestion_fact_counts,
    record_wiki_postgres_parity_summary,
    render_prometheus_metrics,
    reset_observability_metrics_for_tests,
    reset_request_id,
    set_request_id,
)


def test_request_id_is_returned_on_health_response():
    reset_observability_metrics_for_tests()
    client = TestClient(main.app)

    response = client.get("/health", headers={REQUEST_ID_HEADER: "req-2026.07.07"})

    assert response.status_code == 200
    assert response.headers[REQUEST_ID_HEADER] == "req-2026.07.07"
    assert response.json()["status"] == "ok"
    assert "uptime_seconds" in response.json()


def test_metrics_endpoint_exposes_prometheus_request_counters():
    reset_observability_metrics_for_tests()
    client = TestClient(main.app)

    client.get("/health")
    response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert 'siq_api_request_total{method="GET",path="/health",status_code="200"} 1' in response.text
    assert "siq_api_request_duration_ms_sum" in response.text


def test_answer_audit_observation_updates_source_and_guardrail_metrics():
    reset_observability_metrics_for_tests()

    record_answer_audit_observation(
        {
            "citations": [{"source_type": "wiki_metrics"}, {"source_type": "postgresql_agent_view"}],
            "wiki_facts": [{"source_type": "wiki_metrics"}],
            "postgres_facts": [{"source_type": "postgresql_agent_view"}],
            "fallback_reason": "market_view_hit",
            "calculator_runs": [{"operation": "growth_rate"}],
            "guardrail_result": {"blocked": True},
        }
    )

    snapshot = metrics_snapshot()
    assert snapshot["answer_trace_count"] == 1
    assert snapshot["agent_fact_source_counts"]["wiki_metrics"] == 2
    assert snapshot["agent_fact_source_counts"]["postgresql_agent_view"] == 2
    assert snapshot["postgres_fallback_reason_counts"]["market_view_hit"] == 1
    assert snapshot["answer_guardrail_block_counts"]["true"] == 1
    assert snapshot["answer_calculator_run_count"] == 1
    assert snapshot["answer_citation_count"] == 2

    rendered = render_prometheus_metrics()
    assert 'siq_agent_fact_source_total{source_type="wiki_metrics"} 2' in rendered
    assert 'siq_postgres_fallback_reason_total{reason="market_view_hit"} 1' in rendered
    assert 'siq_answer_guardrail_block_total{blocked="true"} 1' in rendered


def test_ingestion_and_parity_metrics_are_rendered_for_release_observability():
    reset_observability_metrics_for_tests()

    record_ingestion_duration(market="HK", stage="postgres_import", status="success", duration_seconds=1.25)
    record_ingestion_fact_counts(
        market="HK",
        counts={"parse_runs": 1, "facts": 8, "tables": 2, "chunks": 3, "evidence": 4},
    )
    record_wiki_postgres_parity_summary(
        {
            "wiki_postgres_parity_results": [
                {
                    "market": "HK",
                    "warning_diff_code_counts": {"unit_display_diff": 2},
                    "warnings": ["ignored because categorized"],
                },
                {"market": "US", "warnings": ["missing optional generated parity"]},
            ]
        }
    )
    record_frontend_pipeline_job_failure(market="US", action="postgres", reason="document_full_path_missing")

    snapshot = metrics_snapshot()
    assert snapshot["ingestion_duration_seconds"]["HK|postgres_import|success"]["count"] == 1
    assert snapshot["ingestion_fact_counts"]["HK|facts"] == 8
    assert snapshot["wiki_postgres_parity_warning_counts"]["HK|unit_display_diff"] == 2
    assert snapshot["wiki_postgres_parity_warning_counts"]["US|uncategorized"] == 1
    assert snapshot["frontend_pipeline_job_failure_counts"]["US|postgres|document_full_path_missing"] == 1

    rendered = render_prometheus_metrics()
    assert 'siq_ingestion_duration_seconds_count{market="HK",stage="postgres_import",status="success"} 1' in rendered
    assert 'siq_ingestion_fact_count{market="HK",kind="facts"} 8' in rendered
    assert 'siq_wiki_postgres_parity_warning_total{market="HK",diff_code="unit_display_diff"} 2' in rendered
    assert (
        'siq_frontend_pipeline_job_failure_total{market="US",action="postgres",reason="document_full_path_missing"} 1'
        in rendered
    )


def test_background_job_final_state_metrics_are_rendered():
    reset_observability_metrics_for_tests()

    record_background_job_final_state(kind="market-document-full-import", status="succeeded", duration_seconds=2.5)
    record_background_job_final_state(kind="market-document-full-import", status="failed", duration_seconds=1.0)

    snapshot = metrics_snapshot()
    assert snapshot["background_job_final_state_counts"]["market-document-full-import|succeeded"] == 1
    assert snapshot["background_job_final_state_counts"]["market-document-full-import|failed"] == 1
    assert snapshot["background_job_duration_seconds"]["market-document-full-import|succeeded"]["count"] == 1
    assert snapshot["background_job_duration_seconds"]["market-document-full-import|succeeded"]["sum"] == 2.5

    rendered = render_prometheus_metrics()
    assert (
        'siq_background_job_final_state_total{kind="market-document-full-import",status="succeeded"} 1'
        in rendered
    )
    assert (
        'siq_background_job_duration_seconds_count{kind="market-document-full-import",status="failed"} 1'
        in rendered
    )


def test_invalid_request_id_is_replaced_with_safe_value():
    generated = normalize_request_id("bad request id")

    assert generated != "bad request id"
    assert len(generated) == 32
    assert generated.isalnum()


def test_request_id_context_round_trips_and_resets():
    token = set_request_id("req-context")
    try:
        assert current_request_id() == "req-context"
    finally:
        reset_request_id(token)

    assert current_request_id() == ""


def test_structured_log_redacts_sensitive_fields(caplog):
    logger = logging.getLogger("siq.test.observability")
    caplog.set_level(logging.INFO, logger=logger.name)

    emit_json_log(
        logger,
        "unit_event",
        request_id="req-log",
        authorization="Bearer secret",
        nested={"api_key": "secret-key", "safe": "value"},
    )

    payload = json.loads(caplog.records[-1].message)
    assert payload["request_id"] == "req-log"
    assert payload["authorization"] == "***REDACTED***"
    assert payload["nested"]["api_key"] == "***REDACTED***"
    assert payload["nested"]["safe"] == "value"


def test_redact_sensitive_keeps_non_sensitive_values():
    assert redact_sensitive({"token": "abc", "path": "/health"}) == {
        "token": "***REDACTED***",
        "path": "/health",
    }
