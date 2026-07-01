from pathlib import Path

from services import agent_runtime_financial_guard as guard


def test_runtime_status_reply_is_not_warned():
    reply = "  [失败] run failed"

    assert guard._is_runtime_status_reply(reply)
    assert guard.append_calculation_trace_warning_if_needed("请计算人均营收", reply) == reply


def test_detects_derived_financial_metric_case_insensitively():
    assert guard._reply_has_derived_financial_metric("未来三年 CAGR 为 8%")
    assert guard._reply_has_derived_financial_metric("人均营收为 120 万元/人")
    assert not guard._reply_has_derived_financial_metric("营业收入为 12 亿元")


def test_detects_calculator_and_reconciliation_traces():
    assert guard._reply_has_calculator_trace("派生计算 operation=ratio")
    assert guard._reply_has_calculator_trace("## 计算器校验\n- ok")
    assert guard._reply_has_reconciliation_trace("## 勾稽校验\n- ok")
    assert guard._reply_has_reconciliation_trace("goodwill_reconciliation passed")
    assert not guard._reply_has_reconciliation_trace("商誉净额说明")


def test_detects_reconciliation_metric_only_with_subject_and_relation():
    assert guard._reply_has_reconciliation_metric("商誉原值 12.82 亿元，减值准备 0.99 亿元，净额 11.83 亿元")
    assert guard._reply_has_reconciliation_metric("坏账准备勾稽关系")
    assert not guard._reply_has_reconciliation_metric("原值、净额和账面价值")
    assert not guard._reply_has_reconciliation_metric("商誉说明")


def test_appends_calculator_warning_for_derived_metric_without_trace():
    guarded = guard.append_calculation_trace_warning_if_needed("请计算人均营收", "人均营收约为 120 万元/人。")

    assert "## 计算校验提示" in guarded
    assert "financial_calculator.py" in guarded


def test_does_not_append_warning_when_calculator_trace_exists():
    reply = "人均营收为 120 万元/人，派生计算（financial_calculator.py）：120000000 / 100 = 1200000 元/人。"

    assert guard.append_calculation_trace_warning_if_needed("请计算人均营收", reply) == reply


def test_requires_reconciliation_trace_even_when_calculator_trace_exists():
    reply = (
        "商誉原值 40.71 亿元，减值准备 0.18 亿元，净额 40.52 亿元。\n"
        "华安基金占比 99.49%（financial_calculator.py ratio）。"
    )

    guarded = guard.append_calculation_trace_warning_if_needed("请分析商誉原值、减值准备和净额", reply)

    assert "## 计算校验提示" in guarded
    assert "financial_reconciliation_validator.py" in guarded


def test_appends_tool_availability_correction_when_script_exists(monkeypatch, tmp_path):
    calculator = tmp_path / "financial_calculator.py"
    validator = tmp_path / "financial_reconciliation_validator.py"
    calculator.write_text("# calculator\n")
    validator.write_text("# validator\n")
    monkeypatch.setattr(guard, "FINANCIAL_CALCULATOR_PATH", calculator)
    monkeypatch.setattr(guard, "FINANCIAL_RECONCILIATION_VALIDATOR_PATH", validator)

    reply = "注：financial_calculator.py 和 financial_reconciliation_validator.py 当前不可用。"

    guarded = guard.append_financial_tool_availability_correction_if_needed(reply)

    assert "## 工具状态纠正" in guarded
    assert str(calculator) in guarded
    assert str(validator) in guarded


def test_does_not_append_tool_availability_correction_without_script(monkeypatch):
    monkeypatch.setattr(guard, "FINANCIAL_CALCULATOR_PATH", Path("/missing/financial_calculator.py"))
    monkeypatch.setattr(guard, "FINANCIAL_RECONCILIATION_VALIDATOR_PATH", Path("/missing/financial_reconciliation_validator.py"))
    reply = "注：financial_calculator.py 当前不可用。"

    assert guard.append_financial_tool_availability_correction_if_needed(reply) == reply


def test_runtime_wrapper_uses_impl_financial_tool_paths(monkeypatch, tmp_path):
    from services import agent_chat_runtime as runtime

    calculator = tmp_path / "financial_calculator.py"
    calculator.write_text("# calculator\n")
    monkeypatch.setattr(runtime, "FINANCIAL_CALCULATOR_PATH", calculator)
    monkeypatch.setattr(runtime, "FINANCIAL_RECONCILIATION_VALIDATOR_PATH", Path("/missing/financial_reconciliation_validator.py"))

    reply = "注：financial_calculator.py 当前不可用。"

    guarded = runtime.append_financial_tool_availability_correction_if_needed(reply)

    assert "## 工具状态纠正" in guarded
    assert str(calculator) in guarded
