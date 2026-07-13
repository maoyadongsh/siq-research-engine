import sys
from pathlib import Path

SCRIPT_DIR = (
    Path(__file__).resolve().parents[3]
    / "agents"
    / "hermes"
    / "profiles"
    / "shared"
    / "scripts"
)
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import statement_metric_lookup as lookup  # noqa: E402


def test_source_financial_scope_keeps_only_unambiguous_scope():
    consolidated = [{"scope": "consolidated"}, {"scope": "consolidated"}]
    mixed = [{"scope": "consolidated"}, {"scope": "parent_company"}]
    headers = ["资产", "2025年12月31日", "2024年12月31日"]

    assert lookup.source_financial_scope(consolidated, headers, {}) == "consolidated"
    assert lookup.source_financial_scope(mixed, headers, {}) == ""
    assert lookup.source_financial_scope([{}], headers, {}) == ""
    assert lookup.source_financial_scope(
        consolidated,
        headers,
        {"2025年12月31日": "parent_company", "2024年12月31日": "parent_company"},
    ) == ""
    assert lookup.source_financial_scope(
        consolidated,
        [*headers, "2025年12月31日#2", "2024年12月31日#2"],
        {},
    ) == ""


def test_column_financial_scopes_preserve_combined_statement_columns():
    parsed = {
        "headers": [
            "资产",
            "附注",
            "2025年12月31日/合并",
            "2024年12月31日/合并",
            "2025年12月31日/公司",
            "2024年12月31日/公司",
        ],
        "header_rows": [
            ["资产", "附注", "2025年12月31日", "2024年12月31日", "2025年12月31日", "2024年12月31日"],
            ["资产", "附注", "合并", "合并", "公司", "公司"],
        ],
    }

    assert lookup.column_financial_scopes(parsed) == {
        "2025年12月31日/合并": "consolidated",
        "2024年12月31日/合并": "consolidated",
        "2025年12月31日/公司": "parent_company",
        "2024年12月31日/公司": "parent_company",
    }
