import json
import logging

from fastapi.testclient import TestClient

import main
from services.observability import (
    REQUEST_ID_HEADER,
    current_request_id,
    emit_json_log,
    normalize_request_id,
    redact_sensitive,
    reset_request_id,
    set_request_id,
)


def test_request_id_is_returned_on_health_response():
    client = TestClient(main.app)

    response = client.get("/health", headers={REQUEST_ID_HEADER: "req-2026.07.07"})

    assert response.status_code == 200
    assert response.headers[REQUEST_ID_HEADER] == "req-2026.07.07"


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
