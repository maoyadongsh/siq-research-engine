import os
import json
import tempfile
import unittest

import financial_extractor as fe
from financial_extractor import QwenTableJudge, build_financial_checks, build_financial_data, parse_html_table


class FakeTableJudge:
    def __init__(self, decision):
        self.decision = decision
        self.calls = []

    def judge(self, table, grid, missing_types, filename=None, report_year=None, rule_evidence=None, task_id=None, market=None):
        self.calls.append(
            {
                "table_index": table["table_index"],
                "missing_types": list(missing_types),
                "filename": filename,
                "report_year": report_year,
                "task_id": task_id,
                "market": market,
                "rule_evidence": list(rule_evidence or []),
            }
        )
        result = dict(self.decision)
        result.setdefault("table_index", table["table_index"])
        result.setdefault("line", table["line"])
        result.setdefault("table_hash", "fake")
        result.setdefault("model", "fake")
        result.setdefault("prompt_version", "test")
        return result


class FinancialExtractorTests(unittest.TestCase):
    def setUp(self):
        self._env_backup = {
            "FINANCIAL_LLM_JUDGE_ENABLED": os.environ.get("FINANCIAL_LLM_JUDGE_ENABLED"),
            "FINANCIAL_LLM_API_BASE": os.environ.get("FINANCIAL_LLM_API_BASE"),
        }
        os.environ.pop("FINANCIAL_LLM_JUDGE_ENABLED", None)
        os.environ.pop("FINANCIAL_LLM_API_BASE", None)

    def tearDown(self):
        for key, value in self._env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _llm_cash_flow_candidate_markdown(self):
        rows = [
            "<tr><td>项目</td><td>2025年</td><td>2024年</td></tr>",
            "<tr><td colspan='3'>一、经营活动产生的现金流量:</td></tr>",
            "<tr><td>经营活动现金流入小计</td><td>60,000,000</td><td>55,000,000</td></tr>",
            "<tr><td>经营活动现金流出小计</td><td>45,000,000</td><td>43,000,000</td></tr>",
            "<tr><td>经营活动产生的现金流量净额</td><td>15,000,000</td><td>12,000,000</td></tr>",
            "<tr><td>投资活动产生的现金流量净额</td><td>-10,000,000</td><td>-6,000,000</td></tr>",
            "<tr><td>筹资活动产生的现金流量净额</td><td>5,000,000</td><td>2,000,000</td></tr>",
            "<tr><td>现金及现金等价物净增加额</td><td>10,000,000</td><td>8,000,000</td></tr>",
            "<tr><td>年初现金及现金等价物余额</td><td>30,000,000</td><td>22,000,000</td></tr>",
            "<tr><td>年末现金及现金等价物余额</td><td>40,000,000</td><td>30,000,000</td></tr>",
        ]
        return f"""
# 测试公司2025年年度报告

## 现金流量情况
<table>{''.join(rows)}</table>
"""

    def test_parse_html_table_expands_rowspan_and_colspan(self):
        grid = parse_html_table(
            """
            <table>
              <tr><td rowspan="2">项目</td><td colspan="2">本年</td></tr>
              <tr><td>合并</td><td>母公司</td></tr>
            </table>
            """
        )

        self.assertEqual(
            grid,
            [
                ["项目", "本年", "本年"],
                ["项目", "合并", "母公司"],
            ],
        )

    def test_extracts_core_statements_and_passes_mvp_checks(self):
        markdown = """
# 测试公司2025年年度报告

## 主要会计数据
单位：万元
<table>
  <tr><td>项目</td><td>2025年</td><td>2024年</td><td>本年比上年增减(%)</td></tr>
  <tr><td>营业收入</td><td>10,000</td><td>9,000</td><td>11.11</td></tr>
  <tr><td>利润总额</td><td>2,100</td><td>1,900</td><td>10.53</td></tr>
  <tr><td>归属于上市公司股东的净利润</td><td>1,500</td><td>1,300</td><td>15.38</td></tr>
  <tr><td>经营活动产生的现金流量净额</td><td>1,500</td><td>1,200</td><td>25.00</td></tr>
  <tr><td>总资产</td><td>30,000</td><td>28,000</td><td>7.14</td></tr>
  <tr><td>归属于上市公司股东的净资产</td><td>13,000</td><td>12,000</td><td>8.33</td></tr>
</table>

## 合并资产负债表
单位：元
<table>
  <tr><td>项目</td><td>2025年12月31日</td><td>2024年12月31日</td></tr>
  <tr><td>流动资产合计</td><td>120,000,000</td><td>110,000,000</td></tr>
  <tr><td>非流动资产合计</td><td>180,000,000</td><td>170,000,000</td></tr>
  <tr><td>资产总计</td><td>300,000,000</td><td>280,000,000</td></tr>
  <tr><td>流动负债合计</td><td>80,000,000</td><td>75,000,000</td></tr>
  <tr><td>非流动负债合计</td><td>70,000,000</td><td>65,000,000</td></tr>
  <tr><td>负债合计</td><td>150,000,000</td><td>140,000,000</td></tr>
  <tr><td>归属于母公司股东权益合计</td><td>130,000,000</td><td>120,000,000</td></tr>
  <tr><td>少数股东权益</td><td>20,000,000</td><td>20,000,000</td></tr>
  <tr><td>所有者权益合计</td><td>150,000,000</td><td>140,000,000</td></tr>
  <tr><td>负债和所有者权益总计</td><td>300,000,000</td><td>280,000,000</td></tr>
</table>

## 合并利润表
单位：元
<table>
  <tr><td>项目</td><td>2025年度</td><td>2024年度</td></tr>
  <tr><td>营业收入</td><td>100,000,000</td><td>90,000,000</td></tr>
  <tr><td>营业利润</td><td>20,000,000</td><td>18,000,000</td></tr>
  <tr><td>营业外收入</td><td>2,000,000</td><td>2,000,000</td></tr>
  <tr><td>营业外支出</td><td>-1,000,000</td><td>1,000,000</td></tr>
  <tr><td>利润总额</td><td>21,000,000</td><td>19,000,000</td></tr>
  <tr><td>所得税费用</td><td>-5,000,000</td><td>4,000,000</td></tr>
  <tr><td>净利润</td><td>16,000,000</td><td>15,000,000</td></tr>
  <tr><td>归属于母公司股东的净利润</td><td>15,000,000</td><td>13,000,000</td></tr>
  <tr><td>少数股东损益</td><td>1,000,000</td><td>2,000,000</td></tr>
  <tr><td>其他综合收益的税后净额</td><td>500,000</td><td>300,000</td></tr>
  <tr><td>综合收益总额</td><td>16,500,000</td><td>15,300,000</td></tr>
</table>

## 合并现金流量表
单位：元
<table>
  <tr><td>项目</td><td>2025年度</td><td>2024年度</td></tr>
  <tr><td>经营活动现金流入小计</td><td>60,000,000</td><td>55,000,000</td></tr>
  <tr><td>经营活动现金流出小计</td><td>-45,000,000</td><td>43,000,000</td></tr>
  <tr><td>经营活动产生的现金流量净额</td><td>15,000,000</td><td>12,000,000</td></tr>
  <tr><td>投资活动现金流入小计</td><td>10,000,000</td><td>9,000,000</td></tr>
  <tr><td>投资活动现金流出小计</td><td>-20,000,000</td><td>15,000,000</td></tr>
  <tr><td>投资活动产生的现金流量净额</td><td>-10,000,000</td><td>-6,000,000</td></tr>
  <tr><td>筹资活动现金流入小计</td><td>20,000,000</td><td>10,000,000</td></tr>
  <tr><td>筹资活动现金流出小计</td><td>-15,000,000</td><td>8,000,000</td></tr>
  <tr><td>筹资活动产生的现金流量净额</td><td>5,000,000</td><td>2,000,000</td></tr>
  <tr><td>汇率变动对现金及现金等价物的影响</td><td>0</td><td>0</td></tr>
  <tr><td>现金及现金等价物净增加额</td><td>10,000,000</td><td>8,000,000</td></tr>
  <tr><td>期初现金及现金等价物余额</td><td>30,000,000</td><td>22,000,000</td></tr>
  <tr><td>期末现金及现金等价物余额</td><td>40,000,000</td><td>30,000,000</td></tr>
</table>
"""
        data = build_financial_data(markdown, task_id="task-fin", filename="测试公司2025年年度报告.pdf")
        checks = build_financial_checks(data)

        statement_types = {item["statement_type"] for item in data["statements"]}
        self.assertEqual(data["report_year"], 2025)
        self.assertTrue({"balance_sheet", "income_statement", "cash_flow_statement"}.issubset(statement_types))
        self.assertEqual(data["summary"]["key_metric_count"], 6)
        self.assertEqual(checks["overall_status"], "pass")
        self.assertEqual(checks["summary"]["fail"], 0)
        self.assertGreater(checks["summary"]["pass"], 0)

        cross_assets = [item for item in checks["checks"] if item["rule_id"] == "cross.total_assets"]
        self.assertEqual(cross_assets[0]["status"], "pass")

    def test_extracts_main_financial_indicators_without_ratio_false_matches(self):
        markdown = """
# 测试公司2025年年度报告

## 主要财务指标
<table>
  <tr><td>主要财务指标</td><td>2025年</td><td>2024年</td><td>本期比上年同期增减(%)</td></tr>
  <tr><td>基本每股收益(元/股)</td><td>1.23</td><td>1.11</td><td>10.81</td></tr>
  <tr><td>稀释每股收益(元/股)</td><td>1.20</td><td>1.08</td><td>11.11</td></tr>
  <tr><td>扣除非经常性损益后的基本每股收益(元/股)</td><td>1.10</td><td>1.00</td><td>10.00</td></tr>
  <tr><td>加权平均净资产收益率(%)</td><td>8.50</td><td>7.60</td><td>增加0.90个百分点</td></tr>
  <tr><td>扣除非经常性损益后的加权平均净资产收益率(%)</td><td>7.90</td><td>7.10</td><td>增加0.80个百分点</td></tr>
  <tr><td>研发投入占营业收入的比例(%)</td><td>9.00</td><td>8.00</td><td>增加1.00个百分点</td></tr>
</table>
"""
        data = build_financial_data(markdown, task_id="task-indicators", filename="测试公司2025年年度报告.pdf")

        metrics = {item["canonical_name"]: item for item in data["key_metrics"]}
        self.assertEqual(metrics["basic_eps"]["values"]["2025"], 1.23)
        self.assertEqual(metrics["diluted_eps"]["values"]["2025"], 1.20)
        self.assertEqual(metrics["deducted_basic_eps"]["values"]["2025"], 1.10)
        self.assertEqual(metrics["weighted_avg_roe"]["values"]["2025"], 8.50)
        self.assertEqual(metrics["deducted_weighted_avg_roe"]["values"]["2025"], 7.90)
        self.assertNotIn("operating_revenue", metrics)

    def test_annual_report_summary_does_not_warn_missing_core_statements(self):
        markdown = """
# 测试公司2025年年度报告摘要

## 主要会计数据
<table>
  <tr><td>项目</td><td>2025年</td><td>2024年</td></tr>
  <tr><td>营业收入</td><td>100</td><td>90</td></tr>
  <tr><td>归属于上市公司股东的净利润</td><td>10</td><td>9</td></tr>
  <tr><td>经营活动产生的现金流量净额</td><td>8</td><td>7</td></tr>
  <tr><td>总资产</td><td>300</td><td>280</td></tr>
</table>
"""
        data = build_financial_data(markdown, task_id="task-summary", filename="测试公司2025年年度报告摘要.pdf")
        checks = build_financial_checks(data)

        self.assertEqual(data["report_kind"], "annual_report_summary")
        self.assertIn("报告摘要", checks["warnings"][0])
        self.assertFalse(any("未提取到合并" in item for item in checks["warnings"]))

    def test_bank_parent_equity_does_not_override_total_equity(self):
        markdown = """
# 招商银行股份有限公司2025年度报告

## 合并资产负债表
<table>
  <tr><td>项目</td><td>2025年12月31日</td><td>2024年12月31日</td></tr>
  <tr><td>资产合计</td><td>13,070,523</td><td>12,152,036</td></tr>
  <tr><td>负债合计</td><td>11,789,624</td><td>10,918,561</td></tr>
  <tr><td>归属于本行股东权益合计</td><td>1,272,875</td><td>1,226,014</td></tr>
  <tr><td>少数股东权益</td><td>8,024</td><td>7,461</td></tr>
  <tr><td>股东权益合计</td><td>1,280,899</td><td>1,233,475</td></tr>
  <tr><td>负债及股东权益总计</td><td>13,070,523</td><td>12,152,036</td></tr>
</table>
"""
        data = build_financial_data(markdown, task_id="task-bank-equity", filename="招商银行股份有限公司2025年度报告.pdf")
        checks = build_financial_checks(data)
        balance = [item for item in data["statements"] if item["statement_type"] == "balance_sheet"][0]
        items = {item["canonical_name"]: item for item in balance["items"] if item.get("canonical_name")}

        self.assertEqual(items["equity_attributable_parent"]["name"], "归属于本行股东权益合计")
        self.assertEqual(items["total_equity"]["name"], "股东权益合计")
        self.assertEqual(items["total_equity"]["values"]["2025-12-31"], 1280899.0)
        self.assertEqual(items["minority_interests"]["values"]["2025-12-31"], 8024.0)
        self.assertEqual(checks["summary"]["fail"], 0)
        self.assertEqual(checks["overall_status"], "pass")

    def test_infers_report_year_from_key_metric_headers_when_cover_omits_year(self):
        markdown = """
# 年度报告

## 主要会计数据和财务指标
<table>
  <tr><td>项目</td><td>2025 年</td><td>2024 年</td><td>本年比上年增减</td></tr>
  <tr><td>营业收入</td><td>100</td><td>90</td><td>11.11%</td></tr>
</table>

## 合并资产负债表
<table>
  <tr><td>项目</td><td>期末余额</td><td>期初余额</td></tr>
  <tr><td>资产总计</td><td>300</td><td>280</td></tr>
  <tr><td>负债合计</td><td>150</td><td>140</td></tr>
  <tr><td>所有者权益合计</td><td>150</td><td>140</td></tr>
</table>
"""
        data = build_financial_data(markdown, task_id="task-year-fallback", filename="result.md")
        balance = [item for item in data["statements"] if item["statement_type"] == "balance_sheet"][0]

        self.assertEqual(data["report_year"], 2025)
        self.assertEqual([item["key"] for item in balance["columns"]], ["2024-12-31", "2025-12-31"])

    def test_uses_financial_note_unit_and_aligns_cross_metric_scale(self):
        markdown = """
# 测试公司2025年年度报告

## 主要会计数据
单位：元
<table>
  <tr><td>项目</td><td>2025年</td><td>2024年</td></tr>
  <tr><td>营业收入</td><td>100,000,000</td><td>90,000,000</td></tr>
  <tr><td>归属于母公司所有者的扣除非经常性损益后的净利润</td><td>9,000,000</td><td>8,000,000</td></tr>
</table>

财务附注中报表的单位为：千元

## 合并利润表
<table>
  <tr><td>项目</td><td>2025年</td><td>2024年</td></tr>
  <tr><td>营业收入</td><td>100,000</td><td>90,000</td></tr>
  <tr><td>归属于母公司所有者的净利润</td><td>10,000</td><td>9,000</td></tr>
</table>
"""
        data = build_financial_data(markdown, task_id="task-unit", filename="测试公司2025年年度报告.pdf")
        checks = build_financial_checks(data)

        income = [item for item in data["statements"] if item["statement_type"] == "income_statement"][0]
        self.assertEqual(income["scale"], 1000.0)
        metrics = {item["canonical_name"]: item for item in data["key_metrics"]}
        self.assertIn("deducted_parent_net_profit", metrics)
        revenue_check = [item for item in checks["checks"] if item["rule_id"] == "cross.revenue"][0]
        self.assertEqual(revenue_check["status"], "pass")

    def test_combined_group_and_company_balance_sheet_is_split_by_scope(self):
        markdown = """
# 测试公司2025年年度报告

## 合并资产负债表和母公司资产负债表
单位：元
<table>
  <tr><td rowspan="2">项目</td><td colspan="2">本集团</td><td colspan="2">本公司</td></tr>
  <tr><td>2025年12月31日</td><td>2024年12月31日</td><td>2025年12月31日</td><td>2024年12月31日</td></tr>
  <tr><td>资产总计</td><td>300,000,000</td><td>280,000,000</td><td>200,000,000</td><td>180,000,000</td></tr>
  <tr><td>负债合计</td><td>150,000,000</td><td>140,000,000</td><td>90,000,000</td><td>80,000,000</td></tr>
  <tr><td>所有者权益合计</td><td>150,000,000</td><td>140,000,000</td><td>110,000,000</td><td>100,000,000</td></tr>
  <tr><td>负债和所有者权益总计</td><td>300,000,000</td><td>280,000,000</td><td>200,000,000</td><td>180,000,000</td></tr>
</table>
"""
        data = build_financial_data(markdown, task_id="task-wide", filename="测试公司2025年年度报告.pdf")
        checks = build_financial_checks(data)

        scopes = {(item["statement_type"], item["scope"]) for item in data["statements"]}
        self.assertIn(("balance_sheet", "consolidated"), scopes)
        self.assertIn(("balance_sheet", "parent_company"), scopes)
        self.assertFalse([item for item in checks["checks"] if item["status"] == "fail"])
        self.assertGreaterEqual(
            len([item for item in checks["checks"] if item["rule_id"] == "bs.assets_eq_liabilities_plus_equity" and item["status"] == "pass"]),
            4,
        )

    def test_side_by_side_balance_sheet_keeps_left_and_right_blocks_separate(self):
        markdown = """
# 测试公司2025年年度报告

## 母公司资产负债表
单位：元
<table>
  <tr><td>资产</td><td>注释号</td><td>期末数</td><td>上年年末数</td><td>负债和所有者权益</td><td>注释号</td><td>期末数</td><td>上年年末数</td></tr>
  <tr><td>流动资产合计</td><td></td><td>120,000,000</td><td>110,000,000</td><td>流动负债合计</td><td></td><td>60,000,000</td><td>55,000,000</td></tr>
  <tr><td>非流动资产合计</td><td></td><td>80,000,000</td><td>70,000,000</td><td>非流动负债合计</td><td></td><td>40,000,000</td><td>35,000,000</td></tr>
  <tr><td>资产总计</td><td></td><td>200,000,000</td><td>180,000,000</td><td>负债合计</td><td></td><td>100,000,000</td><td>90,000,000</td></tr>
  <tr><td></td><td></td><td></td><td></td><td>所有者权益合计</td><td></td><td>100,000,000</td><td>90,000,000</td></tr>
  <tr><td></td><td></td><td></td><td></td><td>负债和所有者权益总计</td><td></td><td>200,000,000</td><td>180,000,000</td></tr>
</table>
"""
        data = build_financial_data(markdown, task_id="task-side-by-side", filename="测试公司2025年年度报告.pdf")
        checks = build_financial_checks(data)
        balance = [item for item in data["statements"] if item["statement_type"] == "balance_sheet"][0]
        items = {item["canonical_name"]: item for item in balance["items"] if item.get("canonical_name")}

        self.assertEqual(items["non_current_assets"]["values"]["2025-12-31"], 80000000.0)
        self.assertEqual(items["total_equity"]["values"]["2025-12-31"], 100000000.0)
        self.assertEqual(items["total_liabilities_and_equity"]["values"]["2025-12-31"], 200000000.0)
        self.assertFalse([item for item in checks["checks"] if item["status"] == "fail"])

    def test_company_cash_flow_scope_and_cash_flow_aliases(self):
        markdown = """
# 测试公司2025年年报

## 2025年度合并及公司现金流量表
单位：元
<table>
  <tr><td>项目</td><td>2025年度</td><td>2024年度</td><td>2025年度</td><td>2024年度</td></tr>
  <tr><td>项目</td><td>合并</td><td>合并</td><td>公司</td><td>公司</td></tr>
  <tr><td>筹资活动现金流入小计</td><td>20,000,000</td><td>10,000,000</td><td>15,000,000</td><td>8,000,000</td></tr>
  <tr><td>筹资活动现金流出小计</td><td>15,000,000</td><td>8,000,000</td><td>10,000,000</td><td>6,000,000</td></tr>
  <tr><td>筹资活动产生/(使用)的现金流量净额</td><td>5,000,000</td><td>2,000,000</td><td>5,000,000</td><td>2,000,000</td></tr>
</table>
"""
        data = build_financial_data(markdown, task_id="task-company-cf", filename="测试公司2025年年报.pdf")
        checks = build_financial_checks(data)

        self.assertEqual(data["report_year"], 2025)
        scopes = {(item["statement_type"], item["scope"]) for item in data["statements"]}
        self.assertIn(("cash_flow_statement", "consolidated"), scopes)
        self.assertIn(("cash_flow_statement", "parent_company"), scopes)
        self.assertFalse([item for item in checks["checks"] if item["rule_id"] == "cf.financing_net" and item["status"] == "fail"])

    def test_body_signature_fallback_recovers_naked_core_statements(self):
        balance_rows = [
            "<tr><td>资产</td><td>附注五</td><td>2025年12月31日</td><td>2024年12月31日</td></tr>",
            "<tr><td>流动资产合计</td><td></td><td>40,000,000</td><td>35,000,000</td></tr>",
            "<tr><td>非流动资产合计</td><td></td><td>60,000,000</td><td>55,000,000</td></tr>",
            "<tr><td>资产总计</td><td></td><td>100,000,000</td><td>90,000,000</td></tr>",
            *[f"<tr><td>资产明细{i}</td><td></td><td>{i}</td><td>{i}</td></tr>" for i in range(12)],
            "<tr><td>负债和所有者权益</td><td>附注五</td><td>2025年12月31日</td><td>2024年12月31日</td></tr>",
            "<tr><td>流动负债合计</td><td></td><td>30,000,000</td><td>28,000,000</td></tr>",
            "<tr><td>非流动负债合计</td><td></td><td>25,000,000</td><td>22,000,000</td></tr>",
            "<tr><td>负债合计</td><td></td><td>55,000,000</td><td>50,000,000</td></tr>",
            "<tr><td>归属于母公司股东权益合计</td><td></td><td>40,000,000</td><td>35,000,000</td></tr>",
            "<tr><td>少数股东权益</td><td></td><td>5,000,000</td><td>5,000,000</td></tr>",
            "<tr><td>所有者权益合计</td><td></td><td>45,000,000</td><td>40,000,000</td></tr>",
            *[f"<tr><td>权益明细{i}</td><td></td><td>{i}</td><td>{i}</td></tr>" for i in range(8)],
            "<tr><td>负债和所有者权益总计</td><td></td><td>100,000,000</td><td>90,000,000</td></tr>",
        ]
        income_rows = [
            "<tr><td></td><td>附注五</td><td>2025年</td><td>2024年</td></tr>",
            "<tr><td>营业收入</td><td></td><td>100,000,000</td><td>90,000,000</td></tr>",
            "<tr><td>减:营业成本</td><td></td><td>70,000,000</td><td>65,000,000</td></tr>",
            "<tr><td>营业利润</td><td></td><td>20,000,000</td><td>18,000,000</td></tr>",
            "<tr><td>加:营业外收入</td><td></td><td>2,000,000</td><td>2,000,000</td></tr>",
            "<tr><td>减:营业外支出</td><td></td><td>1,000,000</td><td>1,000,000</td></tr>",
            "<tr><td>利润总额</td><td></td><td>21,000,000</td><td>19,000,000</td></tr>",
            "<tr><td>减:所得税费用</td><td></td><td>5,000,000</td><td>4,000,000</td></tr>",
            "<tr><td>净利润</td><td></td><td>16,000,000</td><td>15,000,000</td></tr>",
            "<tr><td>归属于母公司股东的净利润</td><td></td><td>15,000,000</td><td>13,000,000</td></tr>",
            "<tr><td>少数股东损益</td><td></td><td>1,000,000</td><td>2,000,000</td></tr>",
            "<tr><td>基本每股收益</td><td></td><td>1.00</td><td>0.90</td></tr>",
            "<tr><td>稀释每股收益</td><td></td><td>1.00</td><td>0.90</td></tr>",
            *[f"<tr><td>利润表补充{i}</td><td></td><td>{i}</td><td>{i}</td></tr>" for i in range(12)],
        ]
        cash_rows = [
            "<tr><td></td><td>附注五</td><td>2025年</td><td>2024年</td></tr>",
            "<tr><td colspan='4'>一、经营活动产生的现金流量:</td></tr>",
            "<tr><td>经营活动现金流入小计</td><td></td><td>60,000,000</td><td>55,000,000</td></tr>",
            "<tr><td>经营活动现金流出小计</td><td></td><td>45,000,000</td><td>43,000,000</td></tr>",
            "<tr><td>经营活动产生的现金流量净额</td><td></td><td>15,000,000</td><td>12,000,000</td></tr>",
            "<tr><td colspan='4'>二、投资活动产生的现金流量:</td></tr>",
            "<tr><td>投资活动现金流入小计</td><td></td><td>10,000,000</td><td>9,000,000</td></tr>",
            "<tr><td>投资活动现金流出小计</td><td></td><td>20,000,000</td><td>15,000,000</td></tr>",
            "<tr><td>投资活动产生的现金流量净额</td><td></td><td>-10,000,000</td><td>-6,000,000</td></tr>",
            "<tr><td colspan='4'>三、筹资活动产生的现金流量:</td></tr>",
            "<tr><td>筹资活动现金流入小计</td><td></td><td>20,000,000</td><td>10,000,000</td></tr>",
            "<tr><td>筹资活动现金流出小计</td><td></td><td>15,000,000</td><td>8,000,000</td></tr>",
            "<tr><td>筹资活动产生的现金流量净额</td><td></td><td>5,000,000</td><td>2,000,000</td></tr>",
            "<tr><td>汇率变动对现金及现金等价物的影响</td><td></td><td>0</td><td>0</td></tr>",
            "<tr><td>现金及现金等价物净增加额</td><td></td><td>10,000,000</td><td>8,000,000</td></tr>",
            "<tr><td>年初现金及现金等价物余额</td><td></td><td>30,000,000</td><td>22,000,000</td></tr>",
            *[f"<tr><td>现金流补充{i}</td><td></td><td>{i}</td><td>{i}</td></tr>" for i in range(10)],
            "<tr><td>年末现金及现金等价物余额</td><td></td><td>40,000,000</td><td>30,000,000</td></tr>",
        ]
        markdown = f"""
# 测试公司2025年年度报告

中国 北京
<table>{''.join(balance_rows)}</table>

主管会计工作负责人
<table>{''.join(income_rows)}</table>

会计机构负责人
<table>{''.join(cash_rows)}</table>
"""
        data = build_financial_data(markdown, task_id="task-naked", filename="测试公司2025年年度报告.pdf")
        checks = build_financial_checks(data)

        statement_types = {item["statement_type"] for item in data["statements"]}
        self.assertTrue({"balance_sheet", "income_statement", "cash_flow_statement"}.issubset(statement_types))
        self.assertEqual(checks["overall_status"], "pass")
        self.assertTrue(any("body.balance_sheet.full" in item["evidence"] for item in data["classification_evidence"]))

    def test_cash_flow_analysis_table_is_not_treated_as_formal_statement(self):
        rows = [
            "<tr><td>项目</td><td>2025年</td><td>2024年</td><td>同比增减</td></tr>",
            "<tr><td>经营活动产生的现金流量净额</td><td>10</td><td>8</td><td>25%</td></tr>",
            "<tr><td>经营活动现金流入小计</td><td>20</td><td>16</td><td>25%</td></tr>",
            "<tr><td>投资活动产生的现金流量净额</td><td>-5</td><td>-4</td><td>25%</td></tr>",
            "<tr><td>筹资活动产生的现金流量净额</td><td>1</td><td>2</td><td>-50%</td></tr>",
            *[f"<tr><td>分析补充{i}</td><td>{i}</td><td>{i}</td><td>0%</td></tr>" for i in range(24)],
        ]
        markdown = f"""
# 测试公司2025年年度报告

## 5、现金流
<table>{''.join(rows)}</table>
"""
        data = build_financial_data(markdown, task_id="task-analysis", filename="测试公司2025年年度报告.pdf")

        self.assertEqual(data["statements"], [])

    def test_non_financial_document_short_circuits_financial_extraction(self):
        markdown = """
# 合同

本合同由甲乙双方签署。
"""
        data = build_financial_data(markdown, task_id="task-contract", filename="采购合同.pdf")
        checks = build_financial_checks(data)

        self.assertEqual(data["summary"]["statement_count"], 0)
        self.assertEqual(data["summary"]["key_metric_count"], 0)
        self.assertFalse(data.get("statements"))
        self.assertTrue(any("不像财报" in item or "跳过财务抽取" in item for item in data["warnings"]))
        self.assertEqual(checks["overall_status"], "skipped")
        self.assertTrue(any("跳过财务抽取" in item or "跳过财务抽取和勾稽检查" in item for item in checks["warnings"]))

    def test_llm_judge_only_classifies_and_script_extracts_values(self):
        fake = FakeTableJudge(
            {
                "decision": "accept",
                "statement_type": "cash_flow_statement",
                "scope": "consolidated",
                "confidence": 0.91,
                "is_formal_statement": True,
                "evidence": ["经营活动产生的现金流量", "年末现金及现金等价物余额"],
            }
        )
        data = build_financial_data(
            self._llm_cash_flow_candidate_markdown(),
            task_id="task-llm-cash",
            filename="测试公司2025年年度报告.pdf",
            llm_judge=fake,
        )

        self.assertEqual(len(fake.calls), 0)
        cash_flow = [item for item in data["statements"] if item["statement_type"] == "cash_flow_statement"][0]
        items = {item["canonical_name"]: item for item in cash_flow["items"]}
        self.assertEqual(items["cash_equivalents_ending"]["values"]["2025"], 40000000.0)
        self.assertEqual(data["llm_table_judgments"], [])
        self.assertTrue(any(item["table_type"] == "cash_flow_statement" for item in data["classification_evidence"]))

    def test_llm_judge_reject_does_not_create_statement(self):
        fake = FakeTableJudge(
            {
                "decision": "reject",
                "statement_type": "cash_flow_statement",
                "scope": "consolidated",
                "confidence": 0.95,
                "is_formal_statement": False,
                "evidence": ["not_formal_statement"],
            }
        )
        data = build_financial_data(
            self._llm_cash_flow_candidate_markdown(),
            task_id="task-llm-reject",
            filename="测试公司2025年年度报告.pdf",
            llm_judge=fake,
        )

        self.assertEqual(len(fake.calls), 0)
        self.assertTrue(any(item["statement_type"] == "cash_flow_statement" for item in data["statements"]))
        self.assertEqual(data["llm_table_judgments"], [])

    def test_llm_judge_can_use_nearby_statement_title_candidates(self):
        fake = FakeTableJudge(
            {
                "decision": "accept",
                "statement_type": "income_statement",
                "scope": "consolidated",
                "confidence": 0.9,
                "is_formal_statement": True,
                "evidence": ["nearby_title=合并利润及其他综合收益表"],
            }
        )
        markdown = """
# 测试公司2025年年度报告

## 第十二节 财务报告
以下为合并利润及其他综合收益表：
<table>
  <tr><td>项目</td><td>2025年度</td><td>2024年度</td></tr>
  <tr><td>一、持续经营净收益</td><td>10</td><td>9</td></tr>
  <tr><td>二、综合收益总额</td><td>12</td><td>10</td></tr>
  <tr><td>归属于母公司所有者的综合收益总额</td><td>11</td><td>9</td></tr>
</table>
"""
        data = build_financial_data(
            markdown,
            task_id="task-llm-title-candidate",
            filename="测试公司2025年年度报告.pdf",
            llm_judge=fake,
        )

        self.assertEqual(len(fake.calls), 1)
        self.assertTrue(any("llm_candidate.types=income_statement" in item for item in fake.calls[0]["rule_evidence"]))
        statement = [item for item in data["statements"] if item["statement_type"] == "income_statement"][0]
        items = {item["canonical_name"]: item for item in statement["items"]}
        self.assertEqual(items["total_comprehensive_income"]["values"]["2025"], 12.0)

    def test_summary_report_does_not_call_llm_judge_for_missing_core_tables(self):
        fake = FakeTableJudge({"decision": "accept", "statement_type": "cash_flow_statement", "scope": "consolidated"})
        data = build_financial_data(
            self._llm_cash_flow_candidate_markdown(),
            task_id="task-summary-no-llm",
            filename="测试公司2025年年度报告摘要.pdf",
            llm_judge=fake,
        )

        self.assertEqual(data["report_kind"], "annual_report_summary")
        self.assertEqual(len(fake.calls), 0)

    def test_llm_judge_is_not_called_when_rules_find_core_tables(self):
        markdown = """
# 测试公司2025年年度报告

## 合并资产负债表
<table>
  <tr><td>项目</td><td>2025年12月31日</td><td>2024年12月31日</td></tr>
  <tr><td>资产总计</td><td>300</td><td>280</td></tr>
  <tr><td>负债合计</td><td>150</td><td>140</td></tr>
  <tr><td>所有者权益合计</td><td>150</td><td>140</td></tr>
</table>

## 合并利润表
<table>
  <tr><td>项目</td><td>2025年</td><td>2024年</td></tr>
  <tr><td>营业收入</td><td>100</td><td>90</td></tr>
  <tr><td>利润总额</td><td>21</td><td>19</td></tr>
  <tr><td>净利润</td><td>16</td><td>15</td></tr>
</table>

## 合并现金流量表
<table>
  <tr><td>项目</td><td>2025年</td><td>2024年</td></tr>
  <tr><td>经营活动产生的现金流量净额</td><td>15</td><td>12</td></tr>
  <tr><td>现金及现金等价物净增加额</td><td>10</td><td>8</td></tr>
  <tr><td>年末现金及现金等价物余额</td><td>40</td><td>30</td></tr>
</table>
"""
        fake = FakeTableJudge({"decision": "reject"})
        data = build_financial_data(
            markdown,
            task_id="task-no-llm",
            filename="测试公司2025年年度报告.pdf",
            llm_judge=fake,
        )

        self.assertEqual(len(fake.calls), 0)
        self.assertEqual(data["llm_table_judgments"], [])
        self.assertEqual({item["statement_type"] for item in data["statements"]}, {"balance_sheet", "income_statement", "cash_flow_statement"})

    def test_llm_judge_cache_path_sanitizes_model_name_and_accepts_v1_base(self):
        judge = QwenTableJudge(
            api_base="http://127.0.0.1:8000/v1",
            model="local/qwen3.6:test",
            cache_dir="/tmp/financial-cache",
            prompt_version="prompt/v1",
        )

        cache_path = judge._cache_path("abc123")
        self.assertEqual(judge._chat_completions_url(), "http://127.0.0.1:8000/v1/chat/completions")
        self.assertTrue(cache_path.endswith("abc123.prompt_v1.local_qwen3.6_test.json"))

    def test_llm_judge_cache_key_includes_document_market_and_full_table_context(self):
        judge = QwenTableJudge(api_base="http://127.0.0.1:8000/v1", model="model-a", prompt_version="prompt-v1")
        table = {
            "table_index": 7,
            "line": 120,
            "html": "<table><tr><td>Total assets</td><td>100</td></tr></table>",
            "context": {"heading": "Balance Sheet", "unit": "USD", "near_text": "Annual report"},
        }
        grid = [["Metric", "2025"], ["Total assets", "100"]]

        base = judge._request_payload(
            table,
            grid,
            ["balance_sheet"],
            "issuer-2025.pdf",
            2025,
            ["body.balance_sheet.full"],
            task_id="task-a",
            market="HK",
        )
        different_market = judge._request_payload(
            table,
            grid,
            ["balance_sheet"],
            "issuer-2025.pdf",
            2025,
            ["body.balance_sheet.full"],
            task_id="task-a",
            market="JP",
        )
        different_table = judge._request_payload(
            table,
            [["Metric", "2025"], ["Total assets", "200"]],
            ["balance_sheet"],
            "issuer-2025.pdf",
            2025,
            ["body.balance_sheet.full"],
            task_id="task-a",
            market="HK",
        )

        self.assertIn("table_text", base)
        self.assertEqual(base["task_id"], "task-a")
        self.assertEqual(base["market"], "HK")
        self.assertNotEqual(judge._request_payload_hash(base), judge._request_payload_hash(different_market))
        self.assertNotEqual(judge._request_payload_hash(base), judge._request_payload_hash(different_table))

    def test_llm_judge_cache_value_records_raw_parsed_schema_and_expiry(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            judge = QwenTableJudge(api_base="http://127.0.0.1:8000/v1", model="model-a", cache_dir=tmpdir, prompt_version="prompt-v1")
            request = {
                "filename": "issuer-2025.pdf",
                "task_id": "task-a",
                "market": "HK",
                "report_year": 2025,
                "table_hash": "table-hash",
                "missing_statement_types": ["balance_sheet"],
                "rule_evidence": ["body.balance_sheet.full"],
            }
            response = {
                "decision": "accept",
                "statement_type": "balance_sheet",
                "schema_validation": {"valid": True, "errors": []},
            }
            judge._write_cache("cache-key", request, response, raw_response={"choices": []})

            path = judge._cache_path("cache-key")
            payload = json.loads(open(path, "r", encoding="utf-8").read())

        self.assertEqual(payload["cache_key"], "cache-key")
        self.assertEqual(payload["request"], request)
        self.assertEqual(payload["raw_response"], {"choices": []})
        self.assertEqual(payload["parsed_decision"], response)
        self.assertEqual(payload["schema_validation"], {"valid": True, "errors": []})
        self.assertTrue(payload["expires_at"])

    def test_bank_key_metrics_with_bare_year_headers_and_section_unit(self):
        markdown = """
# 工商银行2025年度报告

财务指标
<table>
  <tr><td></td><td>2025</td><td>2024</td><td>2023</td></tr>
  <tr><td>全年经营成果(人民币百万元)</td><td>全年经营成果(人民币百万元)</td><td>全年经营成果(人民币百万元)</td><td>全年经营成果(人民币百万元)</td></tr>
  <tr><td>利息净收入</td><td>635,126</td><td>637,405</td><td>655,013</td></tr>
  <tr><td>营业收入</td><td>838,270</td><td>821,803</td><td>843,070</td></tr>
  <tr><td>营业利润</td><td>424,111</td><td>420,885</td><td>420,760</td></tr>
  <tr><td>税前利润</td><td>424,435</td><td>421,827</td><td>421,966</td></tr>
  <tr><td>净利润</td><td>370,766</td><td>366,946</td><td>365,116</td></tr>
  <tr><td>归属于母公司股东的净利润</td><td>368,562</td><td>365,863</td><td>363,993</td></tr>
  <tr><td>扣除非经常性损益后归属于母公司股东的净利润</td><td>368,126</td><td>364,277</td><td>361,411</td></tr>
  <tr><td>经营活动产生的现金流量净额</td><td>1,890,530</td><td>579,194</td><td>1,417,002</td></tr>
  <tr><td>于报告期末(人民币百万元)</td><td>于报告期末(人民币百万元)</td><td>于报告期末(人民币百万元)</td><td>于报告期末(人民币百万元)</td></tr>
  <tr><td>资产总额</td><td>53,477,773</td><td>48,821,746</td><td>44,697,079</td></tr>
  <tr><td>负债总额</td><td>49,205,749</td><td>44,834,480</td><td>40,920,491</td></tr>
  <tr><td>加权平均净资产收益率(%)</td><td>9.45</td><td>9.88</td><td>10.66</td></tr>
</table>
"""
        data = build_financial_data(markdown, task_id="task-bank-metrics", filename="工商银行2025年度报告.pdf")
        metrics = {item["canonical_name"]: item for item in data["key_metrics"]}

        self.assertEqual(metrics["operating_revenue"]["values"]["2025"], 838270000000.0)
        self.assertEqual(metrics["bank_net_interest_income"]["values"]["2025"], 635126000000.0)
        self.assertEqual(metrics["total_profit"]["values"]["2025"], 424435000000.0)
        self.assertEqual(metrics["total_assets"]["values"]["2025"], 53477773000000.0)
        self.assertIn("body.key_metrics", data["classification_evidence"][0]["evidence"])

    def test_key_metric_conflict_is_machine_readable_quality_flag(self):
        markdown = """
# 测试公司2025年度报告

## 主要会计数据
单位：元
<table>
  <tr><td>项目</td><td>2025年</td><td>2024年</td></tr>
  <tr><td>营业收入</td><td>100</td><td>90</td></tr>
  <tr><td>营业利润</td><td>12</td><td>10</td></tr>
  <tr><td>利润总额</td><td>12</td><td>10</td></tr>
  <tr><td>净利润</td><td>10</td><td>8</td></tr>
  <tr><td>归属于母公司股东的净利润</td><td>10</td><td>8</td></tr>
  <tr><td>经营活动产生的现金流量净额</td><td>5</td><td>4</td></tr>
  <tr><td>资产总额</td><td>300</td><td>280</td></tr>
</table>

## 主要财务指标
单位：元
<table>
  <tr><td>项目</td><td>2025年</td><td>2024年</td></tr>
  <tr><td>营业收入</td><td>102</td><td>90</td></tr>
  <tr><td>营业利润</td><td>12</td><td>10</td></tr>
  <tr><td>利润总额</td><td>12</td><td>10</td></tr>
  <tr><td>净利润</td><td>10</td><td>8</td></tr>
  <tr><td>归属于母公司股东的净利润</td><td>10</td><td>8</td></tr>
  <tr><td>经营活动产生的现金流量净额</td><td>5</td><td>4</td></tr>
  <tr><td>资产总额</td><td>300</td><td>280</td></tr>
</table>
"""
        data = build_financial_data(markdown, task_id="task-conflict", filename="测试公司2025年度报告.pdf")
        checks = build_financial_checks(data)
        flags = data["quality_flags"]

        self.assertEqual(data["key_metrics"][0]["values"]["2025"], 100.0)
        self.assertEqual(flags[0]["code"], "key_metric_value_conflict")
        self.assertEqual(flags[0]["canonical_name"], "operating_revenue")
        self.assertEqual(flags[0]["period"], "2025")
        self.assertEqual(flags[0]["existing_value"], 100.0)
        self.assertEqual(flags[0]["incoming_value"], 102.0)
        self.assertEqual(checks["quality_flags"][0]["code"], "key_metric_value_conflict")
        self.assertEqual(checks["overall_status"], "fail")
        self.assertGreaterEqual(checks["summary"]["fail"], 1)
        self.assertTrue(
            any(
                item["rule_id"] == "quality.key_metric_value_conflict.operating_revenue"
                and item["status"] == "fail"
                for item in checks["checks"]
            )
        )
        self.assertTrue(any("关键指标 operating_revenue" in warning for warning in checks["warnings"]))

    def test_bank_group_and_bank_scope_headers_are_split(self):
        markdown = """
# 工商银行2025年度报告

## 合并及公司资产负债表
金额单位均为人民币百万元
<table>
  <tr><td></td><td>附注四</td><td>本集团</td><td>本集团</td><td>本行</td><td>本行</td></tr>
  <tr><td></td><td></td><td>2025年12月31日</td><td>2024年12月31日</td><td>2025年12月31日</td><td>2024年12月31日</td></tr>
  <tr><td>资产总计</td><td></td><td>53,477,773</td><td>48,821,746</td><td>51,452,618</td><td>46,712,722</td></tr>
  <tr><td>负债合计</td><td></td><td>49,205,749</td><td>44,834,480</td><td>47,417,525</td><td>42,939,505</td></tr>
  <tr><td>股东权益合计</td><td></td><td>4,272,024</td><td>3,987,266</td><td>4,035,093</td><td>3,773,217</td></tr>
  <tr><td>负债及股东权益总计</td><td></td><td>53,477,773</td><td>48,821,746</td><td>51,452,618</td><td>46,712,722</td></tr>
</table>
"""
        data = build_financial_data(markdown, task_id="task-bank-scope", filename="工商银行2025年度报告.pdf")
        statements = {(item["statement_type"], item["scope"]): item for item in data["statements"]}

        self.assertIn(("balance_sheet", "consolidated"), statements)
        self.assertIn(("balance_sheet", "parent_company"), statements)
        parent_assets = [
            item
            for item in statements[("balance_sheet", "parent_company")]["items"]
            if item["canonical_name"] == "total_assets"
        ][0]
        self.assertEqual(parent_assets["values"]["2025-12-31"], 51452618000000.0)

    def test_bank_word_scope_headers_do_not_override_group_values(self):
        markdown = """
# 华夏银行2025年度报告

## 合并及银行资产负债表
金额单位均为人民币百万元
<table>
  <tr><td></td><td>附注八</td><td colspan="2">本集团</td><td colspan="2">本银行</td></tr>
  <tr><td></td><td>附注八</td><td>2025年12月31日</td><td>2024年12月31日</td><td>2025年12月31日</td><td>2024年12月31日</td></tr>
  <tr><td>资产总计</td><td></td><td>4,737,619</td><td>4,376,491</td><td>4,475,636</td><td>4,117,664</td></tr>
  <tr><td>负债合计</td><td></td><td>4,337,819</td><td>4,010,807</td><td>4,096,587</td><td>3,769,751</td></tr>
  <tr><td>归属于母公司股东权益合计</td><td></td><td>395,746</td><td>361,982</td><td>379,049</td><td>347,913</td></tr>
  <tr><td>少数股东权益</td><td></td><td>4,054</td><td>3,702</td><td>-</td><td>-</td></tr>
  <tr><td>股东权益合计</td><td></td><td>399,800</td><td>365,684</td><td>379,049</td><td>347,913</td></tr>
  <tr><td>负债及股东权益总计</td><td></td><td>4,737,619</td><td>4,376,491</td><td>4,475,636</td><td>4,117,664</td></tr>
</table>

## 合并及银行利润表
金额单位均为人民币百万元
<table>
  <tr><td></td><td>附注八</td><td colspan="2">本集团</td><td colspan="2">本银行</td></tr>
  <tr><td></td><td>附注八</td><td>2025年</td><td>2024年</td><td>2025年</td><td>2024年</td></tr>
  <tr><td>营业收入</td><td></td><td>91,914</td><td>97,146</td><td>83,154</td><td>89,258</td></tr>
  <tr><td>利润总额</td><td></td><td>34,174</td><td>35,879</td><td>30,125</td><td>31,750</td></tr>
  <tr><td>净利润</td><td></td><td>27,751</td><td>28,196</td><td>24,496</td><td>24,685</td></tr>
  <tr><td>归属于母公司股东的净利润</td><td></td><td>27,200</td><td>27,676</td><td>24,496</td><td>24,685</td></tr>
  <tr><td>少数股东损益</td><td></td><td>551</td><td>520</td><td>-</td><td>-</td></tr>
</table>
"""
        data = build_financial_data(markdown, task_id="task-bank-word", filename="华夏银行2025年度报告.pdf")
        checks = build_financial_checks(data)
        statements = {(item["statement_type"], item["scope"]): item for item in data["statements"]}

        self.assertIn(("balance_sheet", "consolidated"), statements)
        self.assertIn(("balance_sheet", "parent_company"), statements)
        self.assertIn(("income_statement", "consolidated"), statements)
        balance_items = {
            item["canonical_name"]: item
            for item in statements[("balance_sheet", "consolidated")]["items"]
            if item.get("canonical_name")
        }
        self.assertEqual(balance_items["total_equity"]["values"]["2025-12-31"], 399800000000.0)
        self.assertFalse([item for item in checks["checks"] if item["status"] == "fail"])

    def test_group_and_company_headers_split_by_plain_group_and_company_labels(self):
        markdown = """
# 中国银行2025年度报告

## 2025年12月31日合并及母公司资产负债表
<table>
  <tr><td>资产</td><td>附注</td><td>中国银行集团</td><td>中国银行集团</td><td>中国银行</td><td>中国银行</td></tr>
  <tr><td></td><td></td><td>2025年12月31日</td><td>2024年12月31日</td><td>2025年12月31日</td><td>2024年12月31日</td></tr>
  <tr><td>所有者权益合计</td><td></td><td>3,208,124</td><td>2,952,964</td><td>2,724,756</td><td>2,499,140</td></tr>
  <tr><td>归属于母公司所有者权益合计</td><td></td><td>3,064,044</td><td>2,816,231</td><td>2,724,756</td><td>2,499,140</td></tr>
  <tr><td>少数股东权益</td><td></td><td>144,080</td><td>136,733</td><td>-</td><td>-</td></tr>
</table>
"""
        data = build_financial_data(markdown, task_id="task-bank-group-labels", filename="中国银行2025年度报告.pdf")
        checks = build_financial_checks(data)
        statements = {(item["statement_type"], item["scope"]): item for item in data["statements"]}

        self.assertIn(("balance_sheet", "consolidated"), statements)
        self.assertIn(("balance_sheet", "parent_company"), statements)
        balance = statements[("balance_sheet", "consolidated")]
        items = {item["canonical_name"]: item for item in balance["items"] if item.get("canonical_name")}
        self.assertEqual(items["total_equity"]["values"]["2025-12-31"], 3208124.0)
        self.assertEqual(items["minority_interests"]["values"]["2025-12-31"], 144080.0)
        self.assertFalse([item for item in checks["checks"] if item["status"] == "fail"])

    def test_other_comprehensive_income_prefers_total_row_over_attribution(self):
        markdown = """
# 农业银行2025年度报告

## 合并利润表和利润表
金额单位均为人民币百万元
<table>
  <tr><td></td><td>附注</td><td colspan="2">本集团</td><td colspan="2">本行</td></tr>
  <tr><td></td><td>附注</td><td>2025年</td><td>2024年</td><td>2025年</td><td>2024年</td></tr>
  <tr><td>四、利润总额</td><td></td><td>323,689</td><td>319,201</td><td>309,380</td><td>308,358</td></tr>
  <tr><td>减:所得税费用</td><td></td><td>(31,686)</td><td>(36,530)</td><td>(28,419)</td><td>(33,354)</td></tr>
  <tr><td>五、净利润</td><td></td><td>292,003</td><td>282,671</td><td>280,961</td><td>275,004</td></tr>
  <tr><td>-归属于母公司股东的净利润</td><td></td><td>291,041</td><td>282,083</td><td>280,961</td><td>275,004</td></tr>
  <tr><td>-少数股东损益</td><td></td><td>962</td><td>588</td><td>-</td><td>-</td></tr>
</table>

## 合并利润表和利润表 (续)
金额单位均为人民币百万元
<table>
  <tr><td></td><td>附注</td><td colspan="2">本集团</td><td colspan="2">本行</td></tr>
  <tr><td></td><td>附注</td><td>2025年</td><td>2024年</td><td>2025年</td><td>2024年</td></tr>
  <tr><td>六、其他综合收益的税后净额</td><td>29</td><td></td><td></td><td></td><td></td></tr>
  <tr><td>归属于母公司股东的其他综合收益的税后净额</td><td></td><td>(11,843)</td><td>40,315</td><td>(11,161)</td><td>41,604</td></tr>
  <tr><td>以后将重分类进损益的其他综合收益以公允价值计量且其变动计入其他综合收益的债务工具公允价值变动</td><td></td><td>(23,193)</td><td>39,536</td><td>(21,631)</td><td>36,601</td></tr>
  <tr><td>归属于少数股东的其他综合收益的税后净额</td><td></td><td>(745)</td><td>(1,602)</td><td>-</td><td>-</td></tr>
  <tr><td>其他综合收益税后净额</td><td></td><td>(12,588)</td><td>38,713</td><td>(11,161)</td><td>41,604</td></tr>
  <tr><td>七、综合收益总额</td><td></td><td>279,415</td><td>321,384</td><td>269,800</td><td>316,608</td></tr>
  <tr><td>-归属于母公司股东的综合收益总额</td><td></td><td>279,198</td><td>322,398</td><td>269,800</td><td>316,608</td></tr>
  <tr><td>-归属于少数股东的综合收益总额</td><td></td><td>217</td><td>(1,014)</td><td>-</td><td>-</td></tr>
</table>
"""
        data = build_financial_data(markdown, task_id="task-oci-bank", filename="农业银行2025年度报告.pdf")
        checks = build_financial_checks(data)
        income = [item for item in data["statements"] if item["statement_type"] == "income_statement" and item["scope"] == "consolidated"][0]
        items = {item["canonical_name"]: item for item in income["items"] if item.get("canonical_name")}
        bridge_checks = [item for item in checks["checks"] if item["rule_id"] == "is.total_comprehensive_income_bridge"]

        self.assertEqual(data["schema_version"], fe.FINANCIAL_DATA_SCHEMA_VERSION)
        self.assertEqual(items["other_comprehensive_income"]["name"], "其他综合收益税后净额")
        self.assertEqual(items["other_comprehensive_income"]["values"]["2025"], -12588000000.0)
        self.assertEqual(items["parent_other_comprehensive_income"]["values"]["2025"], -11843000000.0)
        self.assertFalse([item for item in bridge_checks if item["status"] == "fail"])

    def test_other_comprehensive_income_prefers_total_row_over_components(self):
        markdown = """
# 中国平安2025年年度报告

## 合并利润表
金额单位为人民币百万元
<table>
  <tr><td></td><td>附注</td><td>2025年度</td><td>2024年度</td></tr>
  <tr><td>四、利润总额</td><td></td><td>185,590</td><td>170,495</td></tr>
  <tr><td>减:所得税费用</td><td></td><td>(27,289)</td><td>(23,762)</td></tr>
  <tr><td>五、净利润</td><td></td><td>158,301</td><td>146,733</td></tr>
  <tr><td>归属于母公司股东的净利润</td><td></td><td>134,778</td><td>126,607</td></tr>
  <tr><td>少数股东损益</td><td></td><td>23,523</td><td>20,126</td></tr>
  <tr><td>七、其他综合收益的税后净额</td><td>58</td><td></td><td></td></tr>
  <tr><td>归属于母公司股东的其他综合收益的税后净额</td><td></td><td></td><td></td></tr>
  <tr><td>以公允价值计量且其变动计入其他综合收益的债务工具公允价值变动</td><td></td><td>(122,252)</td><td>240,577</td></tr>
  <tr><td>权益法下可转损益的其他综合收益</td><td></td><td>(781)</td><td>1,466</td></tr>
  <tr><td>归属于少数股东的其他综合收益的税后净额</td><td></td><td>(1,307)</td><td>829</td></tr>
  <tr><td>其他综合收益合计</td><td></td><td>(3,952)</td><td>(38,785)</td></tr>
  <tr><td>八、综合收益总额</td><td></td><td>154,349</td><td>107,948</td></tr>
</table>
"""
        data = build_financial_data(markdown, task_id="task-oci-components", filename="中国平安2025年年度报告.pdf")
        checks = build_financial_checks(data)
        income = [item for item in data["statements"] if item["statement_type"] == "income_statement"][0]
        items = {item["canonical_name"]: item for item in income["items"] if item.get("canonical_name")}
        bridge_checks = [item for item in checks["checks"] if item["rule_id"] == "is.total_comprehensive_income_bridge"]

        self.assertEqual(items["other_comprehensive_income"]["name"], "其他综合收益合计")
        self.assertEqual(items["other_comprehensive_income"]["values"]["2025"], -3952000000.0)
        self.assertFalse([item for item in bridge_checks if item["status"] == "fail"])

    def test_total_comprehensive_equal_net_profit_treats_missing_oci_as_zero(self):
        markdown = """
# 测试公司2025年年度报告

## 合并利润表
单位：元
<table>
  <tr><td>项目</td><td>2025年度</td><td>2024年度</td></tr>
  <tr><td>利润总额</td><td>20,000</td><td>18,000</td></tr>
  <tr><td>所得税费用</td><td>5,000</td><td>4,000</td></tr>
  <tr><td>净利润</td><td>15,000</td><td>14,000</td></tr>
  <tr><td>综合收益总额</td><td>15,000</td><td>14,000</td></tr>
</table>
"""
        data = build_financial_data(markdown, task_id="task-oci-zero", filename="测试公司2025年年度报告.pdf")
        checks = build_financial_checks(data)
        bridge_checks = [item for item in checks["checks"] if item["rule_id"] == "is.total_comprehensive_income_bridge"]

        self.assertEqual({item["status"] for item in bridge_checks}, {"pass"})
        self.assertTrue(all("按0" in item["right"]["formula"] for item in bridge_checks))

    def test_total_comprehensive_can_use_attributed_oci_rows(self):
        markdown = """
# 测试公司2025年年度报告

## 合并利润表
单位：元
<table>
  <tr><td>项目</td><td>2025年度</td><td>2024年度</td></tr>
  <tr><td>利润总额</td><td>20,000</td><td>18,000</td></tr>
  <tr><td>所得税费用</td><td>5,000</td><td>4,000</td></tr>
  <tr><td>净利润</td><td>15,000</td><td>14,000</td></tr>
  <tr><td>归属于母公司股东的其他综合收益的税后净额</td><td>200</td><td>100</td></tr>
  <tr><td>归属于少数股东的其他综合收益的税后净额</td><td>50</td><td>30</td></tr>
  <tr><td>综合收益总额</td><td>15,250</td><td>14,130</td></tr>
</table>
"""
        data = build_financial_data(markdown, task_id="task-oci-attributed", filename="测试公司2025年年度报告.pdf")
        checks = build_financial_checks(data)
        bridge_checks = [item for item in checks["checks"] if item["rule_id"] == "is.total_comprehensive_income_bridge"]

        self.assertEqual({item["status"] for item in bridge_checks}, {"pass"})
        self.assertTrue(all("少数股东" in item["right"]["formula"] for item in bridge_checks))

    def test_income_analysis_table_with_change_percent_is_excluded(self):
        rows = [
            "<tr><td>(人民币百万元)</td><td>2025年</td><td>2024年</td><td>变动(%)</td></tr>",
            "<tr><td>利息净收入</td><td>88,021</td><td>93,427</td><td>(5.8)</td></tr>",
            "<tr><td>营业收入</td><td>131,442</td><td>146,695</td><td>(10.4)</td></tr>",
            "<tr><td>营业利润</td><td>51,408</td><td>55,206</td><td>(6.9)</td></tr>",
            "<tr><td>利润总额</td><td>51,159</td><td>54,738</td><td>(6.5)</td></tr>",
            "<tr><td>所得税</td><td>(8,526)</td><td>(10,230)</td><td>(16.7)</td></tr>",
            "<tr><td>净利润</td><td>42,633</td><td>44,508</td><td>(4.2)</td></tr>",
            *[f"<tr><td>分析补充{i}</td><td>{i}</td><td>{i}</td><td>0.0</td></tr>" for i in range(18)],
        ]
        markdown = f"""
# 中国平安2025年年度报告

## 银行业务利源分析
<table>{''.join(rows)}</table>
"""
        data = build_financial_data(markdown, task_id="task-income-analysis", filename="中国平安2025年年度报告.pdf")

        self.assertEqual(data["statements"], [])

    def test_bare_parent_statement_titles_do_not_merge_into_consolidated_checks(self):
        markdown = """
# 中国化学2025年度报告

合并现金流量表
金额单位：人民币元
<table>
  <tr><td>项目</td><td>2025年度</td><td>2024年度</td></tr>
  <tr><td>经营活动产生的现金流量净额</td><td>1,443,378,231.39</td><td>8,722,135,369.15</td></tr>
  <tr><td>投资活动产生的现金流量净额</td><td>-5,394,089,409.47</td><td>-3,759,527,106.76</td></tr>
  <tr><td>筹资活动产生的现金流量净额</td><td>-2,612,579,049.64</td><td>-5,409,129,558.20</td></tr>
  <tr><td>汇率变动对现金及现金等价物的影响</td><td>-163,461,413.91</td><td>-111,059,488</td></tr>
</table>

现金流量表
金额单位：人民币元
<table>
  <tr><td>项目</td><td>2025年度</td><td>2024年度</td></tr>
  <tr><td>经营活动产生的现金流量净额</td><td>-2,322,635,694.12</td><td>4,111,756,150.55</td></tr>
  <tr><td>投资活动产生的现金流量净额</td><td>2,667,305,395.55</td><td>1,689,561,009.94</td></tr>
  <tr><td>筹资活动产生的现金流量净额</td><td>-1,146,847,494.15</td><td>-2,251,530,380.38</td></tr>
  <tr><td>汇率变动对现金及现金等价物的影响</td><td>-7,767,705.01</td><td>5,908,823.26</td></tr>
  <tr><td>现金及现金等价物净增加额</td><td>-809,945,497.73</td><td>3,555,695,603.37</td></tr>
  <tr><td>期初现金及现金等价物余额</td><td>6,906,510,106.46</td><td>3,350,814,503.09</td></tr>
  <tr><td>期末现金及现金等价物余额</td><td>6,096,564,608.73</td><td>6,906,510,106.46</td></tr>
</table>
"""
        data = build_financial_data(markdown, task_id="task-bare-parent-cf", filename="中国化学2025年度报告.pdf")
        checks = build_financial_checks(data)
        statements = {(item["statement_type"], item["scope"]): item for item in data["statements"]}

        self.assertIn(("cash_flow_statement", "consolidated"), statements)
        self.assertIn(("cash_flow_statement", "parent_company"), statements)
        self.assertFalse([item for item in checks["checks"] if item["status"] == "fail"])

    def test_bank_combined_titles_without_company_word_and_equity_heading_boundary(self):
        markdown = """
# 测试银行2025年度报告

## 合并利润表和利润表
金额单位均为人民币百万元
<table>
  <tr><td></td><td>附注</td><td>本集团</td><td>本集团</td><td>本行</td><td>本行</td></tr>
  <tr><td></td><td>附注</td><td>2025年</td><td>2024年</td><td>2025年</td><td>2024年</td></tr>
  <tr><td>一、营业收入</td><td></td><td>265,071</td><td>259,826</td><td>213,441</td><td>211,521</td></tr>
  <tr><td>四、利润总额</td><td></td><td>96,609</td><td>100,350</td><td>80,772</td><td>79,900</td></tr>
  <tr><td>五、净利润</td><td></td><td>96,514</td><td>94,229</td><td>80,772</td><td>78,922</td></tr>
</table>

## 合并现金流量表和现金流量表
金额单位均为人民币百万元
<table>
  <tr><td></td><td>附注</td><td>本集团</td><td>本集团</td><td>本行</td><td>本行</td></tr>
  <tr><td></td><td>附注</td><td>2025年</td><td>2024年</td><td>2025年</td><td>2024年</td></tr>
  <tr><td>一、经营活动产生的现金流量净额</td><td></td><td>10,000</td><td>9,000</td><td>8,000</td><td>7,000</td></tr>
  <tr><td>二、投资活动产生的现金流量净额</td><td></td><td>-5,000</td><td>-4,000</td><td>-3,000</td><td>-2,000</td></tr>
  <tr><td>三、筹资活动产生的现金流量净额</td><td></td><td>1,000</td><td>500</td><td>600</td><td>300</td></tr>
  <tr><td>四、汇率变动对现金及现金等价物的影响</td><td></td><td>0</td><td>0</td><td>0</td><td>0</td></tr>
  <tr><td>五、现金及现金等价物净变动额</td><td></td><td>6,000</td><td>5,500</td><td>5,600</td><td>5,300</td></tr>
  <tr><td>加:年初现金及现金等价物余额</td><td></td><td>20,000</td><td>14,500</td><td>18,000</td><td>12,700</td></tr>
  <tr><td>六、年末现金及现金等价物余额</td><td></td><td>26,000</td><td>20,000</td><td>23,600</td><td>18,000</td></tr>
</table>

## 合并股东权益变动表
<table>
  <tr><td></td><td>归属于母公司股东权益</td><td>股东权益合计</td></tr>
  <tr><td>一、2025年1月1日余额</td><td>100</td><td>100</td></tr>
  <tr><td>三、2025年12月31日余额</td><td>120</td><td>120</td></tr>
</table>
"""
        data = build_financial_data(markdown, task_id="task-bank-combined-title", filename="测试银行2025年度报告.pdf")
        statements = {(item["statement_type"], item["scope"]): item for item in data["statements"]}

        self.assertIn(("income_statement", "consolidated"), statements)
        self.assertIn(("cash_flow_statement", "consolidated"), statements)
        self.assertIn(("income_statement", "parent_company"), statements)
        self.assertIn(("cash_flow_statement", "parent_company"), statements)
        cash_flow = statements[("cash_flow_statement", "consolidated")]
        self.assertEqual(cash_flow["table_indexes"], [2])

    def test_derived_financial_indicator_warnings_are_soft_checks(self):
        markdown = """
# 测试公司2025年年度报告

## 主要会计数据
单位：元
<table>
  <tr><td>项目</td><td>2025年</td><td>2024年</td></tr>
  <tr><td>营业收入</td><td>160</td><td>100</td></tr>
  <tr><td>归属于上市公司股东的净利润</td><td>10</td><td>8</td></tr>
  <tr><td>总资产</td><td>100</td><td>90</td></tr>
  <tr><td>负债总额</td><td>85</td><td>70</td></tr>
  <tr><td>归属于上市公司股东的每股净资产</td><td>1.5</td><td>1.4</td></tr>
  <tr><td>基本每股收益</td><td>0.1</td><td>0.08</td></tr>
  <tr><td>期末总股本</td><td>100</td><td>100</td></tr>
</table>

## 合并资产负债表
单位：元
<table>
  <tr><td>项目</td><td>2025年12月31日</td><td>2024年12月31日</td></tr>
  <tr><td>资产总计</td><td>100</td><td>90</td></tr>
  <tr><td>负债合计</td><td>85</td><td>70</td></tr>
  <tr><td>归属于母公司股东权益合计</td><td>15</td><td>20</td></tr>
  <tr><td>所有者权益合计</td><td>15</td><td>20</td></tr>
  <tr><td>负债和所有者权益总计</td><td>100</td><td>90</td></tr>
</table>

## 合并利润表
单位：元
<table>
  <tr><td>项目</td><td>2025年度</td><td>2024年度</td></tr>
  <tr><td>营业收入</td><td>160</td><td>100</td></tr>
  <tr><td>营业利润</td><td>12</td><td>10</td></tr>
  <tr><td>利润总额</td><td>12</td><td>10</td></tr>
  <tr><td>所得税费用</td><td>2</td><td>2</td></tr>
  <tr><td>净利润</td><td>10</td><td>8</td></tr>
  <tr><td>归属于母公司股东的净利润</td><td>10</td><td>8</td></tr>
</table>
"""
        data = build_financial_data(markdown, task_id="task-derived", filename="测试公司2025年年度报告.pdf")
        checks = build_financial_checks(data)
        by_rule = {item["rule_id"]: item for item in checks["checks"]}

        self.assertEqual(data["industry_profile"], "general")
        self.assertEqual(checks["schema_version"], fe.FINANCIAL_CHECKS_SCHEMA_VERSION)
        self.assertEqual(by_rule["ratio.asset_liability_ratio"]["status"], "warning")
        self.assertIn("rough.basic_eps", by_rule)
        self.assertEqual(by_rule["rough.basic_eps"]["status"], "pass")
        self.assertEqual(by_rule["yoy.key_metric.operating_revenue"]["status"], "warning")

    def test_bank_profile_does_not_warn_on_high_asset_liability_ratio(self):
        markdown = """
# 测试银行2025年度报告

## 主要会计数据
单位：百万元
<table>
  <tr><td>项目</td><td>2025年</td><td>2024年</td></tr>
  <tr><td>利息净收入</td><td>100</td><td>90</td></tr>
  <tr><td>客户存款</td><td>900</td><td>800</td></tr>
  <tr><td>客户贷款及垫款总额</td><td>700</td><td>650</td></tr>
</table>

## 合并资产负债表
金额单位均为人民币百万元
<table>
  <tr><td>项目</td><td>2025年12月31日</td><td>2024年12月31日</td></tr>
  <tr><td>资产总计</td><td>1000</td><td>900</td></tr>
  <tr><td>负债合计</td><td>930</td><td>830</td></tr>
  <tr><td>股东权益合计</td><td>70</td><td>70</td></tr>
  <tr><td>负债及股东权益总计</td><td>1000</td><td>900</td></tr>
</table>
"""
        data = build_financial_data(markdown, task_id="task-bank-profile", filename="测试银行2025年度报告.pdf")
        checks = build_financial_checks(data)
        ratio = [item for item in checks["checks"] if item["rule_id"] == "ratio.asset_liability_ratio"][0]

        self.assertEqual(data["industry_profile"], "bank")
        self.assertEqual(ratio["status"], "pass")

    def test_balance_sheet_source_scope_mismatch_is_warning_not_fail(self):
        statement = {
            "statement_type": "balance_sheet",
            "scope": "parent_company",
            "scale": 1.0,
            "items": [
                {
                    "canonical_name": "total_assets",
                    "values": {"2025-12-31": 272573639769.31},
                    "sources": {"2025-12-31": {"table_index": 362, "line": 7292}},
                },
                {
                    "canonical_name": "total_liabilities",
                    "values": {"2025-12-31": 235510915450.74},
                    "sources": {"2025-12-31": {"table_index": 92, "line": 2787}},
                },
                {
                    "canonical_name": "total_equity",
                    "values": {"2025-12-31": 13351227227.88},
                    "sources": {"2025-12-31": {"table_index": 92, "line": 2787}},
                },
            ],
        }
        checks = build_financial_checks({"statements": [statement]})
        item = [row for row in checks["checks"] if row["rule_id"] == "bs.assets_eq_liabilities_plus_equity"][0]

        self.assertEqual(item["status"], "warning")
        self.assertEqual(item["reason"], "source_scope_mismatch_suspect")
        self.assertEqual(item["source_tables"]["total_assets"], 362)

    def test_balance_sheet_magnitude_truncation_is_warning_not_fail(self):
        statement = {
            "statement_type": "balance_sheet",
            "scope": "consolidated",
            "scale": 1.0,
            "items": [
                {
                    "canonical_name": "current_assets",
                    "values": {"2024-12-31": 171823107421.97},
                    "sources": {"2024-12-31": {"table_index": 78, "line": 2000}},
                },
                {
                    "canonical_name": "non_current_assets",
                    "values": {"2024-12-31": 49386322726.2},
                    "sources": {"2024-12-31": {"table_index": 78, "line": 2000}},
                },
                {
                    "canonical_name": "total_assets",
                    "values": {"2024-12-31": 2212094.0},
                    "sources": {"2024-12-31": {"table_index": 78, "line": 2000}},
                },
            ],
        }
        checks = build_financial_checks({"statements": [statement]})
        item = [row for row in checks["checks"] if row["rule_id"] == "bs.current_plus_non_current_assets"][0]

        self.assertEqual(item["status"], "warning")
        self.assertEqual(item["reason"], "parse_suspect_magnitude_mismatch")

    def test_cash_flow_table_with_generated_or_used_wording_is_recognized(self):
        markdown = """
# 上海医药2025年年度报告

## 合并现金流量表和公司现金流量表
单位：元
<table>
  <tr><td>项目</td><td>附注</td><td>2025年度合并</td><td>2024年度合并</td><td>2025年度公司</td><td>2024年度公司</td></tr>
  <tr><td>一、经营活动产生/(使用)的现金流量:</td><td></td><td></td><td></td><td></td><td></td></tr>
  <tr><td>销售商品、提供劳务收到的现金</td><td></td><td>10,000</td><td>9,000</td><td>8,000</td><td>7,000</td></tr>
  <tr><td>经营活动现金流入小计</td><td></td><td>10,000</td><td>9,000</td><td>8,000</td><td>7,000</td></tr>
  <tr><td>经营活动现金流出小计</td><td></td><td>7,000</td><td>6,000</td><td>7,500</td><td>7,200</td></tr>
  <tr><td>经营活动产生/(使用)的现金流量净额</td><td></td><td>3,000</td><td>3,000</td><td>500</td><td>-200</td></tr>
  <tr><td>二、投资活动产生/(使用)的现金流量:</td><td></td><td></td><td></td><td></td><td></td></tr>
  <tr><td>投资活动现金流入小计</td><td></td><td>4,000</td><td>3,000</td><td>2,000</td><td>2,000</td></tr>
  <tr><td>投资活动现金流出小计</td><td></td><td>5,000</td><td>4,000</td><td>2,500</td><td>2,500</td></tr>
  <tr><td>投资活动产生/(使用)的现金流量净额</td><td></td><td>-1,000</td><td>-1,000</td><td>-500</td><td>-500</td></tr>
  <tr><td>三、筹资活动产生/(使用)的现金流量:</td><td></td><td></td><td></td><td></td><td></td></tr>
  <tr><td>筹资活动现金流入小计</td><td></td><td>2,000</td><td>2,000</td><td>2,000</td><td>2,000</td></tr>
  <tr><td>筹资活动现金流出小计</td><td></td><td>1,000</td><td>1,000</td><td>1,000</td><td>1,000</td></tr>
  <tr><td>筹资活动产生/(使用)的现金流量净额</td><td></td><td>1,000</td><td>1,000</td><td>1,000</td><td>1,000</td></tr>
  <tr><td>四、现金及现金等价物净增加额</td><td></td><td>3,000</td><td>3,000</td><td>1,000</td><td>300</td></tr>
  <tr><td>加：期初现金及现金等价物余额</td><td></td><td>5,000</td><td>4,000</td><td>2,000</td><td>1,700</td></tr>
  <tr><td>六、期末现金及现金等价物余额</td><td></td><td>8,000</td><td>7,000</td><td>3,000</td><td>2,000</td></tr>
</table>
"""
        data = build_financial_data(markdown, task_id="task-cf-generated-used", filename="上海医药2025年年度报告.pdf")
        statements = {(item["statement_type"], item["scope"]): item for item in data["statements"]}

        self.assertIn(("cash_flow_statement", "consolidated"), statements)
        self.assertIn(("cash_flow_statement", "parent_company"), statements)


if __name__ == "__main__":
    unittest.main()
