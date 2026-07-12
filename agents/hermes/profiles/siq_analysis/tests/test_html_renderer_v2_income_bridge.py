import importlib.util
import re
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


def test_extracts_nested_product_segments_without_parent_double_counting(tmp_path):
    company_dir = tmp_path / "companies" / "000333-美的集团"
    work_dir = company_dir / "analysis" / ".work" / "case"
    report_dir = company_dir / "reports" / "2025-annual"
    work_dir.mkdir(parents=True)
    report_dir.mkdir(parents=True)
    report_md = """
# （1） 营业收入构成
单位：千元
<table><tr><td rowspan="2"></td><td colspan="2">2025年</td><td colspan="2">2024年</td><td rowspan="2">同比增减</td></tr><tr><td>金额</td><td>占营业收入比重</td><td>金额</td><td>占营业收入比重</td></tr><tr><td>营业收入合计</td><td>456,451,731</td><td>100.00%</td><td>407,149,600</td><td>100.00%</td><td>12.11%</td></tr><tr><td colspan="6">分产品</td></tr><tr><td>智能家居业务</td><td>299,927,239</td><td>65.71%</td><td>269,532,353</td><td>66.20%</td><td>11.28%</td></tr><tr><td>商业及工业解决方案</td><td>122,752,958</td><td>26.89%</td><td>104,496,253</td><td>25.67%</td><td>17.47%</td></tr><tr><td>其中:楼宇科技</td><td>35,790,825</td><td>7.84%</td><td>28,469,710</td><td>6.99%</td><td>25.72%</td></tr><tr><td>机器人与自动化</td><td>31,010,933</td><td>6.79%</td><td>28,700,565</td><td>7.05%</td><td>8.05%</td></tr><tr><td>工业技术</td><td>27,232,432</td><td>5.97%</td><td>24,702,076</td><td>6.07%</td><td>10.24%</td></tr><tr><td>其他业务</td><td>28,718,768</td><td>6.29%</td><td>22,623,902</td><td>5.56%</td><td>26.94%</td></tr><tr><td>其他</td><td>33,771,534</td><td>7.40%</td><td>33,120,994</td><td>8.13%</td><td>1.96%</td></tr><tr><td colspan="6">分地区</td></tr><tr><td>国内</td><td>260,504,038</td><td>57.07%</td><td>238,115,217</td><td>58.48%</td><td>9.40%</td></tr></table>
# （2） 占公司营业收入或营业利润 10%以上的行业、产品、地区、销售模式的情况
单位：千元
<table><tr><td></td><td>营业收入</td><td>营业成本</td><td>毛利率</td><td>营业收入比上年同期增减</td><td>营业成本比上年同期增减</td><td>毛利率比上年同期增减</td></tr><tr><td colspan="7">分产品</td></tr><tr><td>智能家居业务</td><td>299,927,239</td><td>210,259,540</td><td>29.90%</td><td>11.28%</td><td>11.40%</td><td>-0.08%</td></tr><tr><td>商业及工业解决方案</td><td>122,752,958</td><td>97,204,037</td><td>20.81%</td><td>17.47%</td><td>18.33%</td><td>-0.58%</td></tr><tr><td>其中:楼宇科技</td><td>35,790,825</td><td>24,846,935</td><td>30.58%</td><td>25.72%</td><td>24.99%</td><td>0.41%</td></tr><tr><td>机器人与自动化</td><td>31,010,933</td><td>24,403,714</td><td>21.31%</td><td>8.05%</td><td>9.01%</td><td>-0.69%</td></tr><tr><td>工业技术</td><td>27,232,432</td><td>22,468,055</td><td>17.50%</td><td>10.24%</td><td>9.74%</td><td>0.38%</td></tr><tr><td>其他业务</td><td>28,718,768</td><td>25,485,333</td><td>11.26%</td><td>26.94%</td><td>31.34%</td><td>-2.97%</td></tr><tr><td>其他</td><td>33,771,534</td><td>28,525,951</td><td>15.53%</td><td>1.96%</td><td>-0.60%</td><td>2.18%</td></tr><tr><td colspan="7">分地区</td></tr></table>
# （3） 公司实物销售收入是否大于劳务收入
"""
    (report_dir / "report.md").write_text(report_md, encoding="utf-8")

    segments = renderer._extract_product_segments_from_report_markdown(work_dir)

    names = [item["name"] for item in segments]
    assert "商业及工业解决方案" not in names
    assert set(names) == {"智能家居业务", "楼宇科技", "机器人与自动化", "工业技术", "其他业务", "其他"}
    by_name = {item["name"]: item for item in segments}
    assert round(sum(item["share"] for item in segments), 2) == 100.0
    assert round(sum(item["revenue"] for item in segments), 2) == 4564.52
    assert round(by_name["楼宇科技"]["cost"], 2) == 248.47
    assert by_name["智能家居业务"]["gross_margin"] == 29.90


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


def test_income_bridge_normalizes_reversed_ordinary_expense_signs():
    snapshot = {
        "report_year": "2025",
        "metrics": {
            "total_operating_revenue": {"display_name": "营业总收入", "values": {"2025": 4585.02407}, "unit": "亿元"},
            "operating_cost": {"display_name": "营业成本", "values": {"2025": -3359.89528}, "unit": "亿元"},
            "taxes_and_surcharges": {"display_name": "税金及附加", "values": {"2025": -22.17289}, "unit": "亿元"},
            "sales_expenses": {"display_name": "销售费用", "values": {"2025": -428.9149}, "unit": "亿元"},
            "administrative_expenses": {"display_name": "管理费用", "values": {"2025": -160.92311}, "unit": "亿元"},
            "research_expenses": {"display_name": "研发费用", "values": {"2025": -177.87624}, "unit": "亿元"},
            "other_income": {"display_name": "其他收益", "values": {"2025": 26.64313}, "unit": "亿元"},
            "investment_income": {"display_name": "投资收益", "values": {"2025": 16.94661}, "unit": "亿元"},
            "credit_impairment_loss": {"display_name": "信用减值损失", "values": {"2025": 7.82358}, "unit": "亿元"},
            "asset_impairment_loss": {"display_name": "资产减值损失", "values": {"2025": -3.5586}, "unit": "亿元"},
            "asset_disposal_income": {"display_name": "资产处置收益", "values": {"2025": -11.56469}, "unit": "亿元"},
            "total_profit": {"display_name": "利润总额", "values": {"2025": 530.85343}, "unit": "亿元"},
            "income_tax_expense": {"display_name": "所得税费用", "values": {"2025": -85.65147}, "unit": "亿元"},
            "net_profit": {"display_name": "净利润", "values": {"2025": 445.20196}, "unit": "亿元"},
        },
    }

    bridge = renderer.build_income_bridge_data(snapshot, {"report_year": "2025"}, None)

    assert bridge is not None
    assert bridge["flow_nodes"]["cost"]["value"] == 3359.89528
    assert round(bridge["flow_nodes"]["gross_profit"]["value"], 2) == 1225.13

    steps = {step["name"]: step for step in bridge["steps"]}
    assert steps["营业成本"]["value"] == -3359.89528
    assert steps["销售费用"]["value"] == -428.9149
    assert steps["所得税费用"]["value"] == -85.65147
    assert steps["信用减值损失"]["value"] == 7.82358
    assert steps["资产减值损失"]["value"] == -3.5586


def test_income_bridge_uses_total_operating_cost_without_double_counting():
    snapshot = {
        "report_year": "2025",
        "metrics": {
            "total_operating_revenue": {"display_name": "营业总收入", "values": {"2025": 100.0}, "unit": "亿元"},
            "operating_cost": {"display_name": "营业总成本", "values": {"2025": -80.0}, "unit": "亿元"},
            "total_profit": {"display_name": "利润总额", "values": {"2025": 20.0}, "unit": "亿元"},
            "net_profit_parent": {"display_name": "归母净利润", "values": {"2025": 20.0}, "unit": "亿元"},
        },
    }

    bridge = renderer.build_income_bridge_data(snapshot, {"report_year": "2025"}, None)

    assert bridge is not None
    assert bridge["flow_nodes"]["cost"] == {"name": "营业总成本", "value": 80.0}
    step_names = [step["name"] for step in bridge["steps"]]
    assert "营业总成本" in step_names
    assert "营业成本" not in step_names
    assert {step["name"]: step for step in bridge["steps"]}["营业总成本"]["value"] == -80.0


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
    assert 'M314.0' in svg
    assert "<line" not in svg
    assert "收入构成" in svg
    assert "收入汇流" in svg


def test_income_bridge_stage_labels_align_with_chart_anchors():
    data = {
        "period_label": "2025年度",
        "starting_value": 6562.44,
        "ending_value": 101.06,
        "segments": [
            {"name": "整车业务", "revenue": 4101.96, "share": 62.51},
            {"name": "零部件业务", "revenue": 2030.20, "share": 30.94},
            {"name": "其他业务", "revenue": 141.41, "share": 2.15},
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

    labels = {
        name: float(x)
        for name, x in re.findall(r'data-stage-label="([^"]+)" x="([0-9.]+)"', svg)
    }

    assert labels == {
        "收入构成": 227.0,
        "收入汇流": 413.0,
        "收入汇总": 542.0,
        "成本/毛利拆分": 720.0,
        "利润形成": 941.0,
    }
    assert 'text-anchor="middle" class="ib-caption"' in svg
    assert labels["收入构成"] < labels["收入汇流"] < labels["收入汇总"] < labels["成本/毛利拆分"] < labels["利润形成"]
