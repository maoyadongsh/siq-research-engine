import importlib.util
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "validate_legal_opinion.py"
SPEC = importlib.util.spec_from_file_location("validate_legal_opinion_under_test", SCRIPT_PATH)
assert SPEC and SPEC.loader
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)


def _legal_opinion_body(summary: str) -> str:
    return f"""# 测试公司--关联交易事项法律意见

## 一、事项摘要

{summary}

## 二、事实背景

本意见以公司已提供的交易背景、交易对方关系和拟披露安排为事实基础。尚待核实交易金额、董事会审议程序和关联董事回避情况。

## 三、适用法规与检索路径

已通过本机法规库检索公司法、证券法和信息披露相关规则。

## 四、法律分析

基于现有事实，交易可能触发关联交易审议和披露要求。该判断仍取决于交易金额、关联关系认定和交易所规则口径。

## 五、风险提示

如审议程序或公告披露不充分，公司存在被监管问询、要求补充披露或采取监管措施的风险。

## 六、结论与建议

建议公司先补充核实关联关系、交易金额、董事会审议安排和信息披露时间表，并视情况提交外部律师复核。

## 七、引用来源

[1] source=中华人民共和国公司法, source_path=/legal/company-law.md, chunk_index=10, quote="董事、监事、高级管理人员应当遵守法律、行政法规和公司章程", relevance=董监高勤勉义务
[2] source=中华人民共和国证券法, source_path=/legal/securities-law.md, chunk_index=22, quote="信息披露义务人披露的信息应当真实、准确、完整", relevance=信息披露基本要求
[3] source=上市公司信息披露管理办法, source_path=/legal/disclosure-rules.md, chunk_index=8, quote="发生可能对证券交易价格产生较大影响的重大事件", relevance=临时公告判断
[4] source=股票上市规则, source_path=/legal/listing-rules.md, chunk_index=15, quote="上市公司应当及时披露重大事项", relevance=交易所披露程序
[5] source=上市公司治理准则, source_path=/legal/governance-code.md, chunk_index=6, quote="上市公司应当建立健全内部控制制度", relevance=公司治理整改建议

## 八、免责声明

本意见为风险初筛与合规辅助，不替代执业律师判断。
"""


def test_validate_legal_opinion_accepts_professional_conditional_tone(tmp_path):
    path = tmp_path / "opinion.md"
    path.write_text(
        _legal_opinion_body(
            "基于现有事实和本机法规库检索结果，初步倾向认为该事项需要履行关联交易识别、内部审议和信息披露核查程序。"
        ),
        encoding="utf-8",
    )

    result = validator.validate(path)

    assert result["ok"] is True
    assert result["failures"] == []
    assert result["warnings"] == []


def test_validate_legal_opinion_rejects_casual_absolute_assurance(tmp_path):
    path = tmp_path / "bad-opinion.md"
    path.write_text(
        _legal_opinion_body("放心，该事项完全没问题，一定合规，无需任何核实，也不会被监管关注。"),
        encoding="utf-8",
    )

    result = validator.validate(path)

    assert result["ok"] is False
    assert any(item.startswith("unprofessional_or_absolute_tone:") for item in result["failures"])


def test_validate_annual_opinion_requires_company_report_facts_and_dimensions(tmp_path):
    path = tmp_path / "sparse-annual-opinion.md"
    path.write_text(
        _legal_opinion_body(
            "基于现有事实，对测试公司年度报告进行初步审查，倾向认为应进一步核实信息披露。"
        ),
        encoding="utf-8",
    )

    result = validator.validate(path)

    assert result["ok"] is False
    assert any(item.startswith("missing_annual_review_dimensions:") for item in result["failures"])
    assert "too_few_annual_report_facts:0" in result["failures"]
