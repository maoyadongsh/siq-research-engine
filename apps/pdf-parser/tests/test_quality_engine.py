import pytest

from quality_engine import candidate_confidence, candidate_group, detect_report_year
from quality_report import CORE_FINANCIAL_TABLE_NAMES, INDICATOR_TABLE_NAMES


def test_detect_report_year_prefers_filename_when_markdown_has_different_year():
    assert (
        detect_report_year(
            "正文标题：2024年年度报告",
            file_name="600000_2025年年度报告.pdf",
        )
        == 2025
    )


def test_detect_report_year_scans_only_first_6000_markdown_characters():
    assert detect_report_year("x" * 5990 + "2024年年度报告") == 2024
    assert detect_report_year("x" * 6000 + "2025年年度报告") is None


@pytest.mark.parametrize(
    ("markdown", "expected"),
    [
        ("公司 2023年第一季度报告", 2023),
        ("公司 2022年第三季度报告", 2022),
        ("公司 2021年报告摘要", 2021),
        ("公司 2020摘要", 2020),
        ("公司 2019半年度报告", 2019),
        ("公司 2018年报", 2018),
    ],
)
def test_detect_report_year_handles_quarterly_and_summary_patterns(markdown, expected):
    assert detect_report_year(markdown) == expected


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (78, "high"),
        (77.99, "medium"),
        (55, "medium"),
        (54.99, "low"),
    ],
)
def test_candidate_confidence_uses_documented_threshold_boundaries(score, expected):
    assert candidate_confidence(score) == expected


def test_candidate_group_classifies_core_indicator_and_other_names():
    assert candidate_group(CORE_FINANCIAL_TABLE_NAMES[0]) == "core"
    assert candidate_group(INDICATOR_TABLE_NAMES[0]) == "indicator"
    assert candidate_group("未知附注表") == "other"
