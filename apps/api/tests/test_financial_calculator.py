import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
CALCULATOR_PATH = REPO_ROOT / "agents/hermes/profiles/shared/scripts/financial_calculator.py"
RECONCILIATION_PATH = REPO_ROOT / "agents/hermes/profiles/shared/scripts/financial_reconciliation_validator.py"


def load_calculator():
    spec = importlib.util.spec_from_file_location("financial_calculator_under_test", CALCULATOR_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_hkd_currency_code_is_not_thousand_unit():
    calculator = load_calculator()
    money = calculator.build_money("100", "HKD", "HKD")

    assert calculator.plain(money.unit_scale) == "1"
    assert calculator.plain(money.base_value) == "100"


def test_hk_dollar_symbol_is_not_thousand_unit():
    calculator = load_calculator()
    money = calculator.build_money("100", "HK$", "HKD")

    assert calculator.plain(money.unit_scale) == "1"
    assert calculator.plain(money.base_value) == "100"


def test_currency_prefixed_english_scale_units_are_preserved():
    calculator = load_calculator()

    usd_billion = calculator.build_money("215.938", "USD billion", "USD")
    jpy_million = calculator.build_money("48036704", "JPY million", "JPY")

    assert calculator.plain(usd_billion.unit_scale) == "1000000000"
    assert calculator.plain(usd_billion.base_value) == "215938000000"
    assert calculator.plain(jpy_million.unit_scale) == "1000000"
    assert calculator.plain(jpy_million.base_value) == "48036704000000"


def test_parenthesized_number_with_unit_is_negative():
    calculator = load_calculator()
    money = calculator.build_money("(1,016) 百万欧元", "百万欧元", "EUR")

    assert calculator.plain(money.raw_value) == "-1016"
    assert calculator.plain(money.base_value) == "-1016000000"


def test_yoy_negative_base_is_not_applicable_by_default():
    calculator = load_calculator()
    args = calculator.build_parser().parse_args(
        [
            "yoy",
            "--current",
            "100",
            "--current-unit",
            "亿元",
            "--previous",
            "-100",
            "--previous-unit",
            "亿元",
        ]
    )

    payload = calculator.yoy(args)

    assert payload["status"] == "not_applicable"
    assert payload["result"]["delta"] == "20000000000"


def test_yoy_negative_base_can_be_explicitly_allowed():
    calculator = load_calculator()
    args = calculator.build_parser().parse_args(
        [
            "yoy",
            "--current",
            "100",
            "--current-unit",
            "亿元",
            "--previous",
            "-100",
            "--previous-unit",
            "亿元",
            "--allow-negative-base",
        ]
    )

    payload = calculator.yoy(args)

    assert payload["status"] == "ok"
    assert payload["result"]["percent"] == "200"


def test_controlled_non_ok_status_exits_zero():
    result = subprocess.run(
        [
            sys.executable,
            str(CALCULATOR_PATH),
            "--format",
            "json",
            "cagr",
            "--start",
            "-100",
            "--start-unit",
            "亿元",
            "--end",
            "200",
            "--end-unit",
            "亿元",
            "--periods",
            "3",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert '"status": "not_applicable"' in result.stdout


def test_goodwill_reconciliation_passes_against_wiki():
    result = subprocess.run(
        [
            sys.executable,
            str(RECONCILIATION_PATH),
            "--format",
            "json",
            "goodwill",
            "--company",
            "600104",
            "--report-id",
            "2025-annual",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "pass"
    assert payload["result"]["note_gross"] == "1282085915.36"
    assert payload["result"]["impairment_allowance"] == "98963594.89"
    assert payload["result"]["statement_net"] == "1183122320.47"
    assert payload["result"]["difference"] == "0"


def test_goodwill_reconciliation_handles_compact_chinese_goodwill_table():
    result = subprocess.run(
        [
            sys.executable,
            str(RECONCILIATION_PATH),
            "goodwill",
            "--company",
            "国泰海通",
            "--report-id",
            "2025-annual",
            "--format",
            "json",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "pass"
    assert payload["table_mode"] == "compact_goodwill_table"
    assert payload["result"]["note_gross"] == "4070761462"
    assert payload["result"]["impairment_allowance"] == "18405276"
    assert payload["result"]["calculated_net"] == "4052356186"
    assert payload["result"]["statement_net"] == "4052356186"
    assert payload["movement_checks"]["impairment_allowance_change"] == "18405276"
    assert payload["movement_checks"]["current_period_impairment_loss"] == "18405276"
    assert payload["movement_checks"]["has_current_period_goodwill_impairment"] is True


def test_goodwill_reconciliation_handles_blank_midea_totals_and_thousand_yuan_unit():
    result = subprocess.run(
        [
            sys.executable,
            str(RECONCILIATION_PATH),
            "goodwill",
            "--company",
            "000333-美的集团",
            "--report-id",
            "2025-annual",
            "--format",
            "json",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "pass"
    assert payload["table_mode"] == "compact_goodwill_table"
    assert payload["result"]["note_gross"] == "34813270"
    assert payload["result"]["impairment_allowance"] == "556411"
    assert payload["result"]["calculated_net"] == "34256859"
    assert payload["result"]["statement_net"] == "34256859"
    assert payload["result"]["difference"] == "0"
    assert payload["result"]["note_net"] == "34256859"
    assert payload["result"]["note_net_difference"] == "0"
    assert payload["units"] == {
        "note_amount_unit": "人民币千元",
        "note_base_scale": "1000",
        "note_unit_inferred_from_statement": True,
        "statement_amount_unit": "人民币千元",
        "statement_base_scale": "1000",
    }
    assert payload["display"] == "348.13亿元 - 5.56亿元 = 342.57亿元；主表净额 342.57亿元"


def test_reconciliation_format_argument_after_subcommand():
    result = subprocess.run(
        [
            sys.executable,
            str(RECONCILIATION_PATH),
            "goodwill",
            "--company",
            "上汽集团",
            "--report-id",
            "2025-annual",
            "--format",
            "markdown",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "状态：pass" in result.stdout
    assert "主表商誉=账面净额" in result.stdout


def test_calculator_format_argument_after_subcommand():
    result = subprocess.run(
        [
            sys.executable,
            str(CALCULATOR_PATH),
            "ratio",
            "--numerator",
            "1114493515.41",
            "--denominator",
            "1282085915.36",
            "--format",
            "json",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert payload["display"] == "86.93%"


def test_runtime_warns_when_derived_metric_has_no_calculator_trace():
    pytest.importorskip("sqlmodel")
    from services import agent_chat_runtime as runtime

    reply = "人均营收约为 120 万元/人。"

    guarded = runtime.append_calculation_trace_warning_if_needed("请计算人均营收", reply)

    assert "## 计算校验提示" in guarded
    assert "financial_calculator.py" in guarded


def test_runtime_does_not_warn_when_calculator_trace_exists():
    pytest.importorskip("sqlmodel")
    from services import agent_chat_runtime as runtime

    reply = "人均营收为 120 万元/人，派生计算（financial_calculator.py）：120000000 / 100 = 1200000 元/人。"

    guarded = runtime.append_calculation_trace_warning_if_needed("请计算人均营收", reply)

    assert guarded == reply


def test_runtime_warns_when_reconciliation_has_no_validator_trace():
    pytest.importorskip("sqlmodel")
    from services import agent_chat_runtime as runtime

    reply = "商誉原值 12.82 亿元，减值准备 0.99 亿元，净额 11.83 亿元。"

    guarded = runtime.append_calculation_trace_warning_if_needed("商誉原值和减值准备怎么勾稽", reply)

    assert "## 计算校验提示" in guarded
    assert "financial_reconciliation_validator.py" in guarded


def test_runtime_does_not_warn_when_reconciliation_trace_exists():
    pytest.importorskip("sqlmodel")
    from services import agent_chat_runtime as runtime

    reply = "## 勾稽校验\n- 结果：12.82亿元 - 0.99亿元 = 11.83亿元；主表净额 11.83亿元"

    guarded = runtime.append_calculation_trace_warning_if_needed("商誉原值和减值准备怎么勾稽", reply)

    assert guarded == reply


def test_runtime_requires_reconciliation_trace_for_goodwill_even_with_calculator_trace():
    pytest.importorskip("sqlmodel")
    from services import agent_chat_runtime as runtime

    reply = (
        "国泰海通商誉原值 40.71 亿元，减值准备 0.18 亿元，净额 40.52 亿元。\n"
        "华安基金占比 99.49%（financial_calculator.py ratio）。"
    )

    guarded = runtime.append_calculation_trace_warning_if_needed("请分析国泰海通商誉原值、减值准备和净额", reply)

    assert "## 计算校验提示" in guarded
    assert "financial_reconciliation_validator.py" in guarded


def test_runtime_corrects_false_financial_tool_unavailable_claim():
    pytest.importorskip("sqlmodel")
    from services import agent_chat_runtime as runtime

    reply = (
        "## 计算器校验\n"
        "- 注：financial_calculator.py 和 financial_reconciliation_validator.py 当前不可用，以上为手工计算。"
    )

    guarded = runtime.append_calculation_trace_warning_if_needed("请分析商誉净额勾稽", reply)

    assert "## 工具状态纠正" in guarded
    assert "实际存在，并非不可用" in guarded
