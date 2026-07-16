import importlib.util
import json
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "html_renderer_v2.py"
spec = importlib.util.spec_from_file_location("html_renderer_v2", MODULE_PATH)
renderer = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(renderer)


def _snapshot_with_bad_derived_fcf() -> dict:
    return {
        "report_year": "2025",
        "metrics": {
            "operating_cash_flow_net": {
                "display_name": "经营活动产生的现金流量净额",
                "unit": "亿元",
                "values": {"2025": 343.066019},
            },
            "investing_cash_flow_net": {
                "display_name": "投资活动产生的现金流量净额",
                "unit": "亿元",
                "values": {"2025": -275.61162},
            },
            "financing_cash_flow_net": {
                "display_name": "筹资活动产生的现金流量净额",
                "unit": "亿元",
                "values": {"2025": -532.184006},
            },
            "cash_for_purchases": {
                "display_name": "购买商品、接受劳务支付的现金",
                "unit": "亿元",
                "values": {"2025": 3686.568291},
            },
            "cash_for_purchases_investments": {
                "display_name": "购建固定资产、无形资产和其他长期资产支付的现金",
                "unit": "亿元",
                "values": {"2025": 217.86491},
            },
            "free_cash_flow": {
                "display_name": "自由现金流",
                "unit": "亿元",
                "values": {"2025": -3343.502272},
                "sources": {
                    "2025": {
                        "derived_from": ["operating_cash_flow_net", "cash_for_purchases"],
                    }
                },
            },
        },
    }


def test_cashflow_uses_capex_row_not_operating_purchase_cash_when_snapshot_only():
    data = renderer.build_cashflow_data(_snapshot_with_bad_derived_fcf())

    assert data["capex"] == 217.86
    assert data["free_cash_flow"] == 125.20
    assert "购建固定资产、无形资产和其他长期资产支付的现金" in data["sources"]["capex"]["row"]
    assert any(item["status"] == "recomputed" for item in data["validations"])


def test_cashflow_prefers_exact_consolidated_cashflow_rows(tmp_path):
    company_dir = tmp_path / "companies" / "600104-上汽集团"
    work_dir = company_dir / "analysis" / ".work" / "case"
    metrics_dir = company_dir / "metrics" / "reports" / "2025-annual"
    metrics_dir.mkdir(parents=True)
    work_dir.mkdir(parents=True)
    payload = {
        "data": {
            "metrics": [
                {
                    "statement_type": "cash_flow_statement",
                    "scope": "consolidated",
                    "period": "2025",
                    "metric_name": "经营活动产生的现金流量净额",
                    "normalized_value": 343.066019,
                },
                {
                    "statement_type": "cash_flow_statement",
                    "scope": "consolidated",
                    "period": "2025",
                    "metric_name": "投资活动产生的现金流量净额",
                    "normalized_value": -275.61162,
                },
                {
                    "statement_type": "cash_flow_statement",
                    "scope": "consolidated",
                    "period": "2025",
                    "metric_name": "筹资活动产生的现金流量净额",
                    "normalized_value": -532.184006,
                },
                {
                    "statement_type": "cash_flow_statement",
                    "scope": "consolidated",
                    "period": "2025",
                    "metric_name": "购买商品、接受劳务支付的现金",
                    "normalized_value": 3686.568291,
                },
                {
                    "statement_type": "cash_flow_statement",
                    "scope": "consolidated",
                    "period": "2025",
                    "metric_name": "购建固定资产、无形资产和其他长期资产支付的现金",
                    "normalized_value": 217.86491,
                },
            ]
        }
    }
    (metrics_dir / "three_statements.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    data = renderer.build_cashflow_data(_snapshot_with_bad_derived_fcf(), {"report_year": "2025"}, work_dir)

    assert data["operating"] == 343.07
    assert data["investing"] == -275.61
    assert data["financing"] == -532.18
    assert data["capex"] == 217.86
    assert data["free_cash_flow"] == 125.20
    assert data["sources"]["operating"]["source"] == "three_statements"
    assert data["sources"]["capex"]["source"] == "three_statements"
    assert any(item["rule"] == "capital_expenditure row disambiguation" for item in data["validations"])


def test_cashflow_missing_values_are_not_rendered_as_zero():
    data = renderer.build_cashflow_data({"report_year": "2025", "metrics": {}})

    assert data["operating"] is None
    assert data["investing"] is None
    assert data["financing"] is None
    assert data["capex"] is None
    assert data["free_cash_flow"] is None
    assert data["sources"]["operating"]["source"] == "missing"
