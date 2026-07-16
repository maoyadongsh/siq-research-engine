from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from analysis_market_policy import (  # noqa: E402
    CHAPTER_IDS,
    POLICY_SCHEMA_VERSION,
    build_analysis_market_policy,
    build_market_policy,
)


@pytest.mark.parametrize(
    ("market", "label", "context_term", "standard"),
    (
        ("CN", "中国内地市场", "企业会计准则", "CAS"),
        ("HK", "香港市场", "HKFRS/IFRS", "HKFRS"),
        ("US", "美国市场", "10-K/10-Q", "US_GAAP"),
        ("EU", "欧洲市场", "ESEF", "IFRS"),
        ("KR", "韩国市场", "K-IFRS", "K_IFRS"),
        ("JP", "日本市场", "日本财年", "J_GAAP"),
    ),
)
def test_six_market_policies_are_chinese_detailed_and_reusable(market, label, context_term, standard):
    payload = build_analysis_market_policy(
        market,
        source_report={
            "report_type": "annual",
            "accounting_standard": standard,
            "quality_status": "pass",
        },
        financial_checks={"status": "pass"},
        entity_profile={"kind": "general"},
    )

    assert payload["schema_version"] == POLICY_SCHEMA_VERSION
    assert payload["market"] == {"code": market, "label": label}
    assert context_term in payload["reporting_context"]["policy_context"]
    assert payload["reporting_context"]["boundary_note"].startswith("市场政策用于选择分析口径")
    assert set(payload["sections"]) == set(CHAPTER_IDS)
    for chapter_id in CHAPTER_IDS:
        insights = payload["sections"][chapter_id]
        assert len(insights) >= 2
        assert all(item["text"] and item["basis"] and item["scope"] for item in insights)
        assert all(any("\u4e00" <= char <= "\u9fff" for char in item["text"]) for item in insights)


def test_sec_excerpt_recognition_only_emits_topic_labels_with_evidence_boundary():
    raw_excerpt = (
        "Our products are sold to customers worldwide. We face cybersecurity and supply chain risks. "
        "Management concluded there was no material weakness in internal control over financial reporting."
    )
    catalog = [
        {
            "role": "business",
            "file": "sections/business.md",
            "heading": "Item 1. Business",
            "excerpt": raw_excerpt,
            "evidence_ids": ["ev-business-1"],
        },
        {
            "role": "controls",
            "file": "sections/controls.md",
            "heading": "Item 9A. Controls",
            "excerpt": raw_excerpt,
            "evidence_id": "ev-controls-1",
        },
    ]

    payload = build_market_policy(
        "US",
        source_report={"form_type": "10-K", "accounting_standard": "US GAAP"},
        source_metadata={"section_catalog": catalog},
        financial_checks={"status": "pass"},
    )

    assert len(payload["source_topics"]) == 2
    controls = next(item for item in payload["source_topics"] if item["role"] == "controls")
    assert controls["text"].startswith("原文涉及主题：")
    assert "重大缺陷相关表述" in controls["themes"]
    assert "存在重大缺陷" not in controls["text"]
    assert raw_excerpt not in controls["text"]
    assert controls["evidence"] == {
        "kind": "section_catalog",
        "role": "controls",
        "locator": "sections/controls.md",
        "heading": "Item 9A. Controls",
        "evidence_ids": ["ev-controls-1"],
    }


def test_unknown_excerpt_detail_is_not_translated_or_promoted_to_company_fact():
    payload = build_analysis_market_policy(
        "US",
        source_report={"form_type": "10-Q", "accounting_standard": "US_GAAP"},
        source_metadata={
            "section_catalog": [
                {
                    "role": "business",
                    "file": "business.md",
                    "excerpt": "Our products include Project Zephyr, a previously undisclosed launch.",
                }
            ]
        },
        financial_checks={"status": "pass"},
    )

    serialized_text = " ".join(
        [item["text"] for item in payload["source_topics"]]
        + [item["text"] for item in payload["sections"]["business_overview"]]
    )
    assert "Project Zephyr" not in serialized_text
    assert "previously undisclosed" not in serialized_text
    assert "原文涉及主题：产品与服务相关表述" in serialized_text


def test_financial_check_warning_is_counted_without_copying_raw_messages():
    raw_message = "raw upstream parser exploded at /private/server/path"
    payload = build_analysis_market_policy(
        "HK",
        source_report={"report_type": "annual", "accounting_standard": "IFRS"},
        financial_checks={
            "overall_status": "warning",
            "warnings": [raw_message, "another raw warning"],
            "failures": ["raw failure"],
        },
    )

    accounting_text = " ".join(item["text"] for item in payload["sections"]["accounting_quality"])
    assert "2 条告警、1 条失败项" in accounting_text
    assert raw_message not in accounting_text
    assert any(item["code"] == "financial_checks_not_pass" for item in payload["quality"]["warnings"])


def test_us_missing_sec_roles_are_grouped_and_do_not_claim_non_disclosure():
    payload = build_analysis_market_policy(
        "US",
        source_report={"form_type": "10-K", "accounting_standard": "US_GAAP"},
        source_metadata={
            "section_catalog": [{"role": "business", "file": "business.md", "excerpt": "Our products and services."}]
        },
        financial_checks={"status": "pass"},
    )

    warnings = [item for item in payload["quality"]["warnings"] if item["code"] == "sec_section_roles_incomplete"]
    assert len(warnings) == 1
    assert "缺失角色不等同于发行人未披露" in warnings[0]["message"]
    assert "risk_factors" in warnings[0]["message"]


def test_output_is_deterministic_and_rejects_unsafe_section_locator():
    kwargs = {
        "source_report": {"report_type": "annual", "accounting_standard": "IFRS"},
        "source_metadata": {
            "section_catalog": [
                {
                    "role": "notes",
                    "file": "../../private/report.md",
                    "heading": "Notes",
                    "excerpt": "Revenue recognition and impairment are discussed.",
                }
            ]
        },
        "financial_checks": {"status": "pass"},
    }

    first = build_analysis_market_policy("EU", **kwargs)
    second = build_analysis_market_policy("EU", **kwargs)

    assert first == second
    assert "locator" not in first["source_topics"][0]["evidence"]
