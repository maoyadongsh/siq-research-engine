from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from infra.openshell.sandbox.validate_placeholder_auth import (
    PlaceholderAuthError,
    validate_placeholder_auth,
)

ROOT = Path(__file__).resolve().parents[3]
TEMPLATE = ROOT / "infra/openshell/providers/hermes/minimax-cn-auth-pool.template.json"


def _payload() -> dict:
    return json.loads(TEMPLATE.read_text(encoding="utf-8"))


def test_reviewed_template_contains_only_two_minimax_placeholders() -> None:
    payload = _payload()

    validate_placeholder_auth(payload)

    serialized = json.dumps(payload, sort_keys=True)
    assert "openshell:resolve:env:SIQ_MINIMAX_CN_PRIMARY" in serialized
    assert "openshell:resolve:env:SIQ_MINIMAX_CN_BACKUP" in serialized
    assert "sk-" not in serialized


def test_validator_accepts_only_safe_mutable_pool_state() -> None:
    payload = _payload()
    entry = payload["credential_pool"]["minimax-cn"][0]
    entry.update(
        {
            "last_status": "exhausted",
            "last_status_at": 123.0,
            "last_error_code": 429,
            "last_error_reason": "rate_limit",
            "last_error_message": "upstream_limited",
            "last_error_reset_at": 456.0,
            "request_count": 3,
        }
    )
    payload["updated_at"] = "2026-07-15T00:00:00+00:00"

    validate_placeholder_auth(payload)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload["credential_pool"]["minimax-cn"][0].update({"access_token": "real-secret"}),
        lambda payload: payload["credential_pool"].update({"other": []}),
        lambda payload: payload.update({"password": "secret"}),
        lambda payload: payload["credential_pool"]["minimax-cn"][0].update({"refresh_token": "secret"}),
        lambda payload: payload["credential_pool"]["minimax-cn"][0].update({"base_url": "https://evil.example"}),
    ],
)
def test_validator_rejects_secret_or_unreviewed_shape(mutation) -> None:
    payload = copy.deepcopy(_payload())
    mutation(payload)

    with pytest.raises(PlaceholderAuthError):
        validate_placeholder_auth(payload)
