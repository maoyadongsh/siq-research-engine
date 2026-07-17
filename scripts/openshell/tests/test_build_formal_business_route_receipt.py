from __future__ import annotations

from typing import Any

import pytest

from scripts.openshell import build_formal_business_route_receipt as module


def test_business_route_consumer_requires_current_ab_contract_versions() -> None:
    assert module.ab_eval.RAW_SCHEMA_VERSION == "siq.openshell.siq-analysis-ab-raw.v2"
    assert module.ab_eval.SUMMARY_SCHEMA_VERSION == "siq.openshell.siq-analysis-ab-summary.v3"
    assert module.ab_prerequisites.SCHEMA_VERSION == "siq.openshell.siq-analysis-ab-prerequisites.v3"
    assert module.ab_prepare.PROVENANCE_SCHEMA == "siq.openshell.siq-analysis-ab-provenance.v3"


def _record(
    *,
    successful_tools: list[str],
    failed_tools: list[str],
    tool_matched: int,
) -> dict[str, Any]:
    return {
        "case_id": "workflow_analysis_roundtrip",
        "status": "completed",
        "policy_denied": False,
        "successful_tools": successful_tools,
        "failed_tools": failed_tools,
        "scores": {
            "task_success": True,
            "tools": {"matched": tool_matched, "expected": 1},
        },
    }


def test_workflow_projection_accepts_a_required_tool_recovered_to_success() -> None:
    records = [
        _record(
            successful_tools=["read_file", "terminal"],
            failed_tools=["terminal"],
            tool_matched=1,
        )
        for _ in range(3)
    ]

    projection = module._workflow_projection(
        records,
        case_id="workflow_analysis_roundtrip",
        required_tool="terminal",
    )

    assert projection["task_success_count"] == 3
    assert projection["terminal_completed_count"] == 3


def test_workflow_projection_rejects_a_required_tool_ending_in_failure() -> None:
    records = [
        _record(successful_tools=[], failed_tools=["terminal"], tool_matched=0)
        for _ in range(3)
    ]

    with pytest.raises(module.BusinessRouteReceiptError, match="business_route_workflow_tool_failed"):
        module._workflow_projection(
            records,
            case_id="workflow_analysis_roundtrip",
            required_tool="terminal",
        )
