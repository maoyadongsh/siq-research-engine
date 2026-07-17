from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from overseas_report_template import (  # noqa: E402
    REQUIRED_ANALYSIS_DIMENSIONS,
    SECTION_SPECS,
    TEMPLATE_ID,
    build_template_contract,
    validate_template_contract,
)


def test_overseas_template_is_market_specific_and_depth_bounded() -> None:
    contract = build_template_contract(
        "US",
        report_type="annual",
        accounting_standard="US_GAAP",
        entity_profile={"kind": "general"},
    )

    assert contract["template_id"] == TEMPLATE_ID
    assert contract["market"] == "US"
    assert contract["section_ids"] == [section_id for section_id, _, _ in SECTION_SPECS]
    assert set(REQUIRED_ANALYSIS_DIMENSIONS) <= set(contract["required_analysis_dimensions"])
    assert all(contract["section_minimum_items"][section_id] >= 4 for section_id, _, _ in SECTION_SPECS)
    assert any("10-K" in item for item in contract["market_adaptations"])
    assert validate_template_contract(contract) == []


def test_overseas_template_rejects_cn_market() -> None:
    try:
        build_template_contract("CN")
    except ValueError as exc:
        assert "unsupported overseas report market" in str(exc)
    else:
        raise AssertionError("CN must remain outside the overseas report template")
