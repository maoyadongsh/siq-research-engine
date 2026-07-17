import json
import logging

import main
from fastapi.testclient import TestClient
from services.observability import (
    REQUEST_ID_HEADER,
    current_request_id,
    emit_json_log,
    emit_research_event,
    metrics_snapshot,
    normalize_http_metric_path,
    normalize_request_id,
    record_answer_audit_observation,
    record_background_job_final_state,
    record_background_job_persistence_failure,
    record_frontend_pipeline_job_failure,
    record_ingestion_duration,
    record_ingestion_fact_counts,
    record_research_readiness,
    record_research_validation_failure,
    record_research_workflow_terminal,
    record_wiki_postgres_parity_summary,
    redact_sensitive,
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


def test_health_exposes_credential_free_openshell_recovery_state(monkeypatch):
    expected = {"enabled": True, "required": True, "ready": True}
    monkeypatch.setattr(
        main.openshell_pool_recovery_service,
        "readiness_snapshot",
        lambda: expected,
    )

    response = TestClient(main.app).get("/health")

    assert response.status_code == 200
    assert response.json()["openshell_recovery"] == expected
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


def test_http_metric_path_prefers_route_template_and_collapses_dynamic_fallback():
    assert normalize_http_metric_path("/api/reports/20260712", "/api/reports/{report_id}") == "/api/reports/{report_id}"
    assert normalize_http_metric_path("/api/reports/20260712") == "/__unmatched__"
    assert normalize_http_metric_path("/api/reports/arbitrary-customer-controlled-value") == "/__unmatched__"


def test_http_middleware_records_dynamic_routes_by_template(monkeypatch):
    monkeypatch.delenv("SIQ_METRICS_TOKEN", raising=False)
    monkeypatch.setenv("SIQ_DEPLOYMENT_PROFILE", "development")
    reset_observability_metrics_for_tests()
    client = TestClient(main.app)

    client.get("/api/wiki/companies/customer-controlled-id/reports")
    rendered = client.get("/metrics").text

    assert 'path="/api/wiki/companies/{company_dir}/reports"' in rendered
    assert "customer-controlled-id" not in rendered


def test_metrics_endpoint_accepts_service_token_and_rejects_wrong_token(monkeypatch):
    monkeypatch.setenv("SIQ_METRICS_TOKEN", "metrics-test-token")
    monkeypatch.setenv("SIQ_DEPLOYMENT_PROFILE", "production")
    client = TestClient(main.app)

    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", headers={"X-SIQ-Service-Token": "wrong"}).status_code == 401
    response = client.get("/metrics", headers={"Authorization": "Bearer metrics-test-token"})

    assert response.status_code == 200


def test_metrics_endpoint_fails_closed_in_production_without_token(monkeypatch):
    monkeypatch.delenv("SIQ_METRICS_TOKEN", raising=False)
    monkeypatch.delenv("SIQ_INTERNAL_METRICS_TOKEN", raising=False)
    monkeypatch.setenv("SIQ_DEPLOYMENT_PROFILE", "production")

    assert TestClient(main.app).get("/metrics").status_code == 503


def test_metrics_endpoint_fails_closed_in_docker_without_token(monkeypatch):
    monkeypatch.delenv("SIQ_METRICS_TOKEN", raising=False)
    monkeypatch.delenv("SIQ_INTERNAL_METRICS_TOKEN", raising=False)
    monkeypatch.setenv("SIQ_DEPLOYMENT_PROFILE", "docker")

    assert TestClient(main.app).get("/metrics").status_code == 503


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


def test_background_job_persistence_failure_metric_is_rendered():
    record_background_job_persistence_failure(operation="job_update")

    assert metrics_snapshot()["background_job_persistence_failure_counts"] == {"job_update": 1}
    assert (
        'siq_background_job_persistence_failure_total{operation="job_update"} 1'
        in render_prometheus_metrics()
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


def test_research_observability_uses_bounded_labels_and_exports_all_t9_counters():
    reset_observability_metrics_for_tests()

    record_research_readiness(market="US", agent_type="analysis", status="degraded")
    record_research_readiness(market="user-controlled-market", agent_type="user-agent", status="custom")
    record_research_workflow_terminal(market="HK", agent_type="factchecker", status="completed", ok=True)
    record_research_workflow_terminal(market="JP", agent_type="tracking", status="partial_success", ok=True)
    record_research_workflow_terminal(market="EU", agent_type="analysis", status="timeout", ok=False)
    record_research_validation_failure(market="KR", agent_type="tracking", failure="identity_mismatch")
    record_research_validation_failure(market="US", agent_type="factcheck", failure="citation_failure")

    snapshot = metrics_snapshot()
    assert snapshot["research_readiness_counts"]["US|analysis|degraded"] == 1
    assert snapshot["research_readiness_counts"]["unknown|unknown|unavailable"] == 1
    assert snapshot["research_workflow_terminal_counts"]["HK|factcheck|success"] == 1
    assert snapshot["research_workflow_terminal_counts"]["JP|tracking|degraded"] == 1
    assert snapshot["research_workflow_terminal_counts"]["EU|analysis|failed"] == 1
    assert snapshot["research_identity_mismatch_counts"]["KR|tracking"] == 1
    assert snapshot["research_citation_failure_counts"]["US|factcheck"] == 1

    rendered = render_prometheus_metrics()
    assert 'siq_research_readiness_total{market="US",agent_type="analysis",status="degraded"} 1' in rendered
    assert 'siq_research_workflow_terminal_total{market="HK",agent_type="factcheck",status="success"} 1' in rendered
    assert 'siq_research_identity_mismatch_total{market="KR",agent_type="tracking"} 1' in rendered
    assert 'siq_research_citation_failure_total{market="US",agent_type="factcheck"} 1' in rendered
    assert "user-controlled-market" not in rendered
    assert "user-agent" not in rendered


def test_research_log_hashes_company_key_and_never_serializes_body_prompt_or_path(caplog):
    logger = logging.getLogger("siq.test.research_observability")
    caplog.set_level(logging.INFO, logger=logger.name)
    company_key = "us-aapl-private-selector"
    local_path = "/home/private/wiki/us/companies/AAPL/reports/secret"

    emit_research_event(
        logger,
        "research_workflow_finished",
        agent_type="analysis",
        market="US",
        company_key=company_key,
        research_identity={
            "market": "US",
            "company_id": "US:AAPL",
            "filing_id": "US:AAPL:10-K:2025",
            "parse_run_id": "parse-1",
            "ignored_report_body": "secret body",
            "local_path": local_path,
        },
        source_family="sec_ixbrl",
        adapter_version="sec_ixbrl_v1",
        artifact_id="analysis-aapl-2025",
        status="completed",
    )

    message = caplog.records[-1].message
    payload = json.loads(message)
    assert payload["company_key_summary"].startswith("sha256:")
    assert payload["research_identity"] == {
        "market": "US",
        "company_id": "US:AAPL",
        "filing_id": "US:AAPL:10-K:2025",
        "parse_run_id": "parse-1",
    }
    assert company_key not in message
    assert "secret body" not in message
    assert local_path not in message
    assert "prompt" not in payload
