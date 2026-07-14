"""Shared JSON Schema validation primitives for formal IC artifacts."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from jsonschema import Draft202012Validator, FormatChecker


class ICContractValidationError(ValueError):
    """Raised when a formal IC task, report, or phase artifact is invalid."""

    def __init__(self, contract: str, errors: list[str]) -> None:
        self.contract = contract
        self.errors = tuple(errors)
        super().__init__(f"{contract} contract invalid: {'; '.join(errors)}")


def validate_schema(
    payload: Mapping[str, Any],
    schema: Mapping[str, Any],
    *,
    contract: str,
) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ICContractValidationError(contract, ["$: must be a JSON object"])
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = []
    for error in sorted(validator.iter_errors(payload), key=lambda item: list(item.absolute_path)):
        path = "$" + "".join(f"[{item!r}]" for item in error.absolute_path)
        errors.append(f"{path}: {error.message}")
    if errors:
        raise ICContractValidationError(contract, errors)
    return deepcopy(dict(payload))


def require_identity(
    payload: Mapping[str, Any],
    *,
    contract: str,
    expected_deal_id: str | None = None,
    expected_agent_id: str | None = None,
    expected_snapshot_hash: str | None = None,
) -> None:
    errors: list[str] = []
    if expected_deal_id and payload.get("deal_id") != expected_deal_id:
        errors.append("deal_id_mismatch")
    if expected_agent_id and payload.get("agent_id") != expected_agent_id:
        errors.append("agent_id_mismatch")
    if expected_snapshot_hash and payload.get("evidence_snapshot_hash") != expected_snapshot_hash:
        errors.append("evidence_snapshot_hash_mismatch")
    if errors:
        raise ICContractValidationError(contract, errors)


def combine_validation_errors(contract: str, errors: list[str]) -> None:
    normalized = sorted({str(error).strip() for error in errors if str(error).strip()})
    if normalized:
        raise ICContractValidationError(contract, normalized)
