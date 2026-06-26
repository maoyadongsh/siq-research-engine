"""Pure quality-report helpers shared by the Web app and tests."""

from __future__ import annotations

import re

from quality_report import CORE_FINANCIAL_TABLE_NAMES, INDICATOR_TABLE_NAMES


def detect_report_year(markdown, file_name=None):
    search_parts = []
    if file_name:
        search_parts.append(str(file_name))
    if markdown:
        search_parts.append(str(markdown)[:6000])
    search_text = "\n".join(search_parts)
    patterns = [
        r"(20\d{2})\s*年\s*(?:年度报告|年报|半年度报告|第一季度报告|第三季度报告|季度报告|报告摘要|摘要)",
        r"(20\d{2})(?:年度报告|年报|半年度报告|第一季度报告|第三季度报告|季度报告|报告摘要|摘要)",
    ]
    for pattern in patterns:
        match = re.search(pattern, search_text)
        if match:
            try:
                return int(match.group(1))
            except (TypeError, ValueError):
                return None
    return None


def candidate_group(name):
    if name in CORE_FINANCIAL_TABLE_NAMES:
        return "core"
    if name in INDICATOR_TABLE_NAMES:
        return "indicator"
    return "other"


def candidate_confidence(score):
    if score >= 78:
        return "high"
    if score >= 55:
        return "medium"
    return "low"
