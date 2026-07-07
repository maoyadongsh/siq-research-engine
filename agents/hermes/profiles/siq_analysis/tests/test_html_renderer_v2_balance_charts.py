import importlib.util
import json
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "html_renderer_v2.py"
spec = importlib.util.spec_from_file_location("html_renderer_v2", MODULE_PATH)
renderer = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(renderer)


def _workdir_with_balance_rows(tmp_path: Path) -> Path:
    company_dir = tmp_path / "companies" / "600104-上汽集团"
    work_dir = company_dir / "analysis" / ".work" / "case"
    metrics_dir = company_dir / "metrics" / "reports" / "2025-annual"
    metrics_dir.mkdir(parents=True)
    work_dir.mkdir(parents=True)
    rows = [
        ("monetary_capital", "货币资金", 1510.46),
        ("accounts_receivable", "应收账款", 820.53),
        ("inventory", "存货", 796.40),
        ("current_assets", "流动资产合计", 6229.54),
        ("non_current_assets", "非流动资产合计", 3372.53),
        ("total_assets", "资产总计", 9602.07),
        ("short_term_borrowings", "短期借款", 406.00),
        ("notes_payable", "应付票据", 1342.17),
        ("current_portion_noncurrent_liabilities", "一年内到期的非流动负债", 113.12),
        ("current_liabilities", "流动负债合计", 5842.36),
        ("long_term_borrowings", "长期借款", 188.08),
        ("non_current_liabilities", "非流动负债合计", 881.95),
        ("total_liabilities", "负债合计", 6724.31),
        ("equity_attributable_parent", "归属于母公司所有者权益合计", 2102.47),
    ]
    metrics = [
        {
            "statement_type": "balance_sheet",
            "scope": "consolidated",
            "period": "2025-12-31",
            "metric_key": key,
            "metric_name": name,
            "normalized_value": value,
        }
        for key, name, value in rows
    ]
    metrics.extend(
        [
            {
                "statement_type": "income_statement",
                "scope": "consolidated",
                "period": "2025",
                "metric_key": "total_operating_revenue",
                "metric_name": "营业总收入",
                "normalized_value": 6562.44,
            },
            {
                "statement_type": "income_statement",
                "scope": "consolidated",
                "period": "2025",
                "metric_key": "parent_net_profit",
                "metric_name": "归属于母公司股东的净利润",
                "normalized_value": 101.06,
            },
        ]
    )
    payload = {"data": {"metrics": metrics}}
    (metrics_dir / "three_statements.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return work_dir


def test_asset_and_debt_structure_use_verified_rows_and_reconcile(tmp_path):
    work_dir = _workdir_with_balance_rows(tmp_path)
    snapshot = {"report_year": "2025", "metrics": {}}

    asset = renderer.build_asset_structure_data(snapshot, {"report_year": "2025"}, work_dir)
    debt = renderer.build_debt_structure_data(snapshot, {"report_year": "2025"}, work_dir)

    assert asset["total"] == 9602.07
    assert round(sum(item["value"] for item in asset["categories"]), 2) == 9602.07
    assert asset["validations"][0]["status"] == "ok"
    assert debt["total"] == 6724.31
    assert round(sum(item["value"] for item in debt["categories"]), 2) == 6724.31
    assert debt["validations"][0]["status"] == "ok"


def test_dupont_and_solvency_use_verified_rows(tmp_path):
    work_dir = _workdir_with_balance_rows(tmp_path)
    snapshot = {"report_year": "2025", "metrics": {}}

    dupont = renderer.build_dupont_data(snapshot, {"report_year": "2025"}, work_dir)
    solvency = renderer.build_solvency_gauges(snapshot, {"report_year": "2025"}, work_dir)

    assert dupont["sources"]["total_assets"]["source"] == "three_statements"
    assert dupont["net_margin"] == round(101.06 / 6562.44 * 100, 2)
    assert dupont["visual_scale"] == "reference_range_score_0_100"
    assert [item["key"] for item in dupont["dimensions"]] == ["net_margin", "asset_turnover", "equity_multiplier", "roe"]
    assert all(0 <= item["score"] <= 100 for item in dupont["dimensions"])
    assert dupont["dimensions"][2]["raw_display"].endswith("x")
    assert solvency["debt_ratio"] == round(6724.31 / 9602.07 * 100, 2)
    assert solvency["quick_ratio"] == round((6229.54 - 796.40) / 5842.36, 2)

    svg = renderer.svg_radar_chart(dupont)
    assert "展示" in svg
    assert "雷达半径为 0-100" in svg
    assert "杜邦三因子分解" in svg
    assert "标准化雷达" in svg
    assert "归母净利润 / 营业收入" in svg
    assert 'data-chart-id="dupont-dim-0"' in svg
    assert 'data-chart-id="dupont-radar"' in svg


def test_asset_structure_does_not_estimate_missing_noncurrent_assets():
    snapshot = {
        "report_year": "2025",
        "metrics": {
            "total_assets": {"display_name": "资产总计", "unit": "亿元", "values": {"2025": 1000}},
            "monetary_capital": {"display_name": "货币资金", "unit": "亿元", "values": {"2025": 100}},
        },
    }

    data = renderer.build_asset_structure_data(snapshot)

    assert data["total"] == 1000
    names = {item["name"] for item in data["categories"]}
    assert "非流动资产" not in names
    assert "其他资产/口径差" in names
