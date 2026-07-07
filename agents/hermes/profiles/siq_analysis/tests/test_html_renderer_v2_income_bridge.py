import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "html_renderer_v2.py"
spec = importlib.util.spec_from_file_location("html_renderer_v2", MODULE_PATH)
renderer = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(renderer)


def test_extracts_product_segments_and_other_business_from_report_md(tmp_path):
    company_dir = tmp_path / "companies" / "600104-上汽集团"
    work_dir = company_dir / "analysis" / ".work" / "case"
    report_dir = company_dir / "reports" / "2025-annual"
    work_dir.mkdir(parents=True)
    report_dir.mkdir(parents=True)
    report_md = """
## 营业收入构成
<table><tr><td rowspan="2">项目</td><td colspan="2">本期发生额</td><td colspan="2">上期发生额</td></tr><tr><td>收入</td><td>成本</td><td>收入</td><td>成本</td></tr><tr><td>主营业务</td><td>632,007,457,472.74</td><td>570,847,895,293.20</td><td>598,531,183,108.65</td><td>544,284,899,090.84</td></tr><tr><td>其他业务</td><td>14,144,644,416.56</td><td>10,096,088,356.21</td><td>15,542,878,709.48</td><td>12,165,137,595.60</td></tr><tr><td>合计</td><td>646,152,101,889.30</td><td>580,943,983,649.41</td><td>614,074,061,818.13</td><td>556,450,036,686.44</td></tr></table>
<table><tr><td></td><td colspan="2">本年累计数</td><td colspan="2">上年累计数</td></tr><tr><td></td><td>营业收入</td><td>营业成本</td><td>营业收入</td><td>营业成本</td></tr><tr><td>整车业务</td><td>410,196,473,001.11</td><td>392,545,642,811.17</td><td>381,262,343,730.33</td><td>366,534,174,722.96</td></tr><tr><td>零部件业务</td><td>203,019,866,775.26</td><td>164,149,718,120.28</td><td>190,675,933,362.83</td><td>156,532,375,412.29</td></tr><tr><td>劳务及其他</td><td>18,791,117,696.37</td><td>14,152,534,361.75</td><td>26,592,906,015.49</td><td>21,218,348,955.59</td></tr><tr><td>合计</td><td>632,007,457,472.74</td><td>570,847,895,293.20</td><td>598,531,183,108.65</td><td>544,284,899,090.84</td></tr></table>
## （2）其他
"""
    (report_dir / "report.md").write_text(report_md, encoding="utf-8")

    segments = renderer._extract_product_segments_from_report_markdown(work_dir)

    names = {item["name"] for item in segments}
    assert {"整车业务", "零部件业务", "劳务及其他", "其他业务"}.issubset(names)
    by_name = {item["name"]: item for item in segments}
    assert round(by_name["整车业务"]["revenue"], 2) == 4101.96
    assert round(by_name["零部件业务"]["cost"], 2) == 1641.50
    assert by_name["其他业务"]["revenue_yoy"] < 0


def test_income_bridge_adds_residual_segment_to_reconcile_total_revenue():
    snapshot = {
        "business_segments": [
            {"name": "整车业务", "revenue": 4101.9647300111},
            {"name": "零部件业务", "revenue": 2030.1986677526},
            {"name": "劳务及其他", "revenue": 187.9111769637},
            {"name": "其他业务", "revenue": 141.4464441656},
        ]
    }

    segments = renderer._income_bridge_segments(snapshot, 6562.44, None)

    by_name = {item["name"]: item for item in segments}
    assert "利息/手续费等" in by_name
    assert round(sum(item["revenue"] for item in segments), 2) == 6562.44


def test_income_bridge_svg_uses_uniform_interactive_ribbons():
    data = {
        "period_label": "2025年度",
        "starting_value": 6562.44,
        "ending_value": 101.06,
        "segments": [
            {"name": "整车业务", "revenue": 4101.96, "revenue_yoy": 7.59, "share": 62.51},
            {"name": "零部件业务", "revenue": 2030.20, "revenue_yoy": 6.47, "share": 30.94},
            {"name": "劳务及其他", "revenue": 187.91, "revenue_yoy": -29.34, "share": 2.86},
            {"name": "其他业务", "revenue": 141.41, "revenue_yoy": -9.00, "share": 2.15},
            {"name": "利息/手续费等", "revenue": 100.96, "share": 1.54},
        ],
        "flow_nodes": {
            "revenue": {"name": "营业总收入", "value": 6562.44},
            "cost": {"name": "营业成本", "value": 5809.44},
            "gross_profit": {"name": "毛利", "value": 753.0},
            "operating_adjustments": {"name": "期间费用/减值/其他", "value": 498.7},
            "operating_profit": {"name": "营业利润", "value": 254.3},
            "pretax_profit": {"name": "利润总额", "value": 249.1},
            "income_tax": {"name": "所得税", "value": 74.65},
            "attribution": {"name": "其他/归属调整", "value": 73.38},
            "parent_net_profit": {"name": "归母净利润", "value": 101.06},
        },
    }

    svg = renderer.svg_income_bridge_chart(data)

    assert svg.count("ib-ribbon") >= 7
    assert 'data-ib-id="flow-seg-0-revenue"' in svg
    assert 'data-ib-id="flow-revenue-cost"' in svg
    assert 'data-ib-id="flow-revenue-gross"' in svg
    assert 'data-ib-id="node-income-collector"' in svg
    assert 'data-ib-id="flow-collector-revenue"' in svg
    assert 'M424.0' in svg
    assert "<line" not in svg
    assert "收入构成" in svg
    assert "收入汇流" in svg
