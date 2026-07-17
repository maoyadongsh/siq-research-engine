from __future__ import annotations

import pytest

from infra.openshell.sandbox.validate_provider_placeholders import (
    EXPECTED_DATA_BROKER_URL,
    REQUIRED_PROVIDER_ENVS,
    is_placeholder,
    validate_environment,
)


def _environment() -> dict[str, str]:
    values = {name: f"openshell:resolve:env:v12_{name}" for name in REQUIRED_PROVIDER_ENVS}
    values["SIQ_PG_QUERY_BROKER_URL"] = EXPECTED_DATA_BROKER_URL
    return values


def test_provider_environment_accepts_only_openshell_placeholders() -> None:
    environment = _environment()
    validate_environment(environment)
    assert "EXA_API_KEY" not in environment
    assert is_placeholder("TAVILY_API_KEY", "openshell:resolve:env:TAVILY_API_KEY")


@pytest.mark.parametrize("value", ["", "real-secret", "openshell:resolve:env:OTHER_KEY"])
def test_provider_environment_rejects_missing_materialized_or_wrong_key(value: str) -> None:
    environment = _environment()
    environment["TAVILY_API_KEY"] = value

    with pytest.raises(ValueError, match="provider_placeholder_invalid:TAVILY_API_KEY"):
        validate_environment(environment)


def test_provider_environment_rejects_data_broker_bypass() -> None:
    environment = _environment()
    environment["SIQ_PG_QUERY_BROKER_URL"] = "http://host.openshell.internal:15432"

    with pytest.raises(ValueError, match="data_broker_url_invalid"):
        validate_environment(environment)
