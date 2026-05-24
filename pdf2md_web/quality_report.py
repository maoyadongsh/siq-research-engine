"""Quality report constants shared by the quality engine and Web app."""

from __future__ import annotations


QUALITY_SCHEMA_VERSION = 10

KEY_SECTIONS = [
    "重要提示",
    "公司简介",
    "主要财务指标",
    "管理层讨论与分析",
    "公司治理",
    "重要事项",
    "股份变动",
    "财务报告",
]

CORE_FINANCIAL_TABLE_NAMES = [
    "主要会计数据",
    "主要财务指标",
    "非经常性损益",
    "资产负债表",
    "利润表",
    "现金流量表",
    "所有者权益变动表",
]

INDICATOR_TABLE_NAMES = [
    "营业收入",
    "分行业",
    "分产品",
    "分地区",
    "研发投入",
    "前十名股东",
]

KEY_TABLE_DISPLAY_ORDER = CORE_FINANCIAL_TABLE_NAMES + INDICATOR_TABLE_NAMES + ["股东信息"]
