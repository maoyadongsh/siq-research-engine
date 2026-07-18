from services.agent_runtime_financial_evidence import (
    _decimal,
    _period,
    build_trusted_calculation_evidence,
    build_trusted_statement_row_evidence,
)


def test_financial_evidence_decimal_preserves_numeric_zero():
    assert _decimal(0) == 0
    assert _decimal(0.0) == 0


def test_financial_evidence_period_parses_year_before_chinese_text():
    assert _period("2025年度合并") == "2025"
    assert _period("截至2025年末") == "2025"


IDENTITY = {
    "market": "CN",
    "company_id": "000333-美的集团",
    "filing_id": "CN:000333-美的集团:2025-annual",
    "parse_run_id": "task-midea",
}

SAIC_IDENTITY = {
    "market": "CN",
    "company_id": "600104-上汽集团",
    "filing_id": "CN:600104-上汽集团:2025-annual",
    "parse_run_id": "task-saic",
}

BYD_IDENTITY = {
    "market": "CN",
    "company_id": "002594-比亚迪",
    "filing_id": "CN:002594-比亚迪:2025-annual",
    "parse_run_id": "task-byd",
}


def _statement_result(company_id: str = "000333-美的集团", task_id: str = "task-midea") -> dict:
    return {
        "company_id": company_id,
        "report_id": "2025-annual",
        "task_id": task_id,
        "tables": [
            {
                "report_id": "2025-annual",
                "task_id": task_id,
                "financial_scope": "consolidated",
                "headers": ["资产", "2025年12月31日", "2024年12月31日"],
                "unit": "人民币千元",
                "pdf_page": 132,
                "table_index": 89,
                "md_line": 2497,
                "records": [
                    {
                        "资产": "商誉",
                        "2025年12月31日": "34,256,859",
                        "2024年12月31日": "29,581,014",
                    }
                ],
            }
        ],
    }


def _note_result(other_label: str = "其他(i)") -> dict:
    return {
        "company_id": "000333-美的集团",
        "report_id": "2025-annual",
        "task_id": "task-midea",
        "tables": [
            {
                "report_id": "2025-annual",
                "task_id": "task-midea",
                "financial_scope": "consolidated",
                "metric": "(21) 商誉",
                "headers": ["商誉-", "2025年12月31日", "2024年12月31日"],
                "unit": None,
                "pdf_page": 206,
                "table_index": 163,
                "md_line": 4325,
                "records": [
                    {"商誉-": "KUKA集团", "2025年12月31日": "23,435,302", "2024年12月31日": "21,415,464"},
                    {"商誉-": other_label, "2025年12月31日": "7,930,808", "2024年12月31日": "5,220,530"},
                    {"商誉-": "", "2025年12月31日": "34,813,270", "2024年12月31日": "30,150,019"},
                    {"商誉-": "减:减值准备", "2025年12月31日": "(556,411)", "2024年12月31日": "(569,005)"},
                    {"商誉-": "", "2025年12月31日": "34,256,859", "2024年12月31日": "29,581,014"},
                ],
            }
        ],
    }


def _saic_statement_result() -> dict:
    return {
        "company_id": "600104-上汽集团",
        "report_id": "2025-annual",
        "task_id": "task-saic",
        "tables": [
            {
                "report_id": "2025-annual",
                "task_id": "task-saic",
                "financial_scope": "consolidated",
                "headers": ["资产", "2025年12月31日", "2024年12月31日"],
                "unit": "元",
                "pdf_page": 65,
                "table_index": 84,
                "md_line": 1840,
                "records": [
                    {
                        "资产": "商誉",
                        "2025年12月31日": "1,183,122,320.47",
                        "2024年12月31日": "1,198,210,116.59",
                    }
                ],
            }
        ],
    }


def _saic_note_result(task_id: str = "task-saic") -> dict:
    label = "被投资单位名称或形成商誉的事项"
    return {
        "company_id": "600104-上汽集团",
        "report_id": "2025-annual",
        "task_id": task_id,
        "tables": [
            {
                "report_id": "2025-annual",
                "task_id": task_id,
                "financial_scope": "consolidated",
                "metric": "(1).商誉账面原值",
                "headers": [label, "期初余额", "本期增加/企业合并形成的", "本期减少/处置", "期末余额"],
                "unit": "元",
                "pdf_page": 137,
                "table_index": 165,
                "md_line": 4186,
                "records": [
                    {
                        label: "华域视觉科技(上海)有限公司(以下简称“华域视觉”)",
                        "期初余额": "781,115,081.73",
                        "本期增加/企业合并形成的": "",
                        "本期减少/处置": "",
                        "期末余额": "781,115,081.73",
                    },
                    {
                        label: "上汽通用汽车金融有限责任公司",
                        "期初余额": "333,378,433.68",
                        "本期增加/企业合并形成的": "",
                        "本期减少/处置": "",
                        "期末余额": "333,378,433.68",
                    },
                    {
                        label: "上海机动车回收服务中心有限公司",
                        "期初余额": "15,087,796.12",
                        "本期增加/企业合并形成的": "",
                        "本期减少/处置": "15,087,796.12",
                        "期末余额": "",
                    },
                    {
                        label: "合计",
                        "期初余额": "1,302,999,061.44",
                        "本期增加/企业合并形成的": "",
                        "本期减少/处置": "20,913,146.08",
                        "期末余额": "1,282,085,915.36",
                    },
                ],
            },
            {
                "report_id": "2025-annual",
                "task_id": task_id,
                "financial_scope": "consolidated",
                "metric": "(2).商誉减值准备",
                "headers": [label, "期初余额", "本期增加/计提", "本期减少/处置", "期末余额"],
                "unit": "元",
                "pdf_page": 137,
                "table_index": 166,
                "md_line": 4196,
                "records": [
                    {
                        label: "商誉减值准备",
                        "期初余额": "104,788,944.85",
                        "本期增加/计提": "",
                        "本期减少/处置": "5,825,349.96",
                        "期末余额": "98,963,594.89",
                    },
                    {
                        label: "合计",
                        "期初余额": "104,788,944.85",
                        "本期增加/计提": "",
                        "本期减少/处置": "5,825,349.96",
                        "期末余额": "98,963,594.89",
                    },
                ],
            },
        ],
    }


def _byd_statement_result() -> dict:
    return {
        "company_id": "002594-比亚迪",
        "report_id": "2025-annual",
        "task_id": "task-byd",
        "tables": [
            {
                "report_id": "2025-annual",
                "task_id": "task-byd",
                "financial_scope": "consolidated",
                "headers": ["资产", "附注七", "2025年12月31日", "2024年12月31日"],
                "unit": "千元",
                "pdf_page": 123,
                "table_index": 108,
                "md_line": 3014,
                "records": [
                    {
                        "资产": "商誉",
                        "附注七": "19",
                        "2025年12月31日": "4,427,571",
                        "2024年12月31日": "4,427,571",
                    }
                ],
            }
        ],
    }


def _byd_note_result() -> dict:
    label = "列1"
    common = {
        "report_id": "2025-annual",
        "task_id": "task-byd",
        "financial_scope": "consolidated",
        "unit": None,
        "pdf_page": 192,
    }
    return {
        "company_id": "002594-比亚迪",
        "report_id": "2025-annual",
        "task_id": "task-byd",
        "tables": [
            {
                **common,
                "metric": "（1） 商誉原值",
                "table_index": 364,
                "md_line": 5682,
                "headers": [label, "年初余额", "本年增加/企业合并", "本年减少", "年末余额"],
                "records": [
                    {label: "比亚迪汽车有限公司", "年初余额": "63,399", "本年增加/企业合并": "-", "本年减少": "-", "年末余额": "63,399"},
                    {label: "JunoNewco", "年初余额": "4,361,657", "本年增加/企业合并": "-", "本年减少": "-", "年末余额": "4,361,657"},
                    {label: "合计", "年初余额": "4,437,242", "本年增加/企业合并": "-", "本年减少": "-", "年末余额": "4,437,242"},
                ],
            },
            {
                **common,
                "metric": "（2） 商誉减值准备",
                "table_index": 365,
                "md_line": 5686,
                "headers": [label, "年初余额", "本年增加", "本年减少", "年末余额"],
                "records": [
                    {label: "比亚迪汽车有限公司", "年初余额": "4,796", "本年增加": "-", "本年减少": "-", "年末余额": "4,796"},
                    {label: "合计", "年初余额": "9,671", "本年增加": "-", "本年减少": "-", "年末余额": "9,671"},
                ],
            },
        ],
    }


def test_builds_midea_blank_total_goodwill_evidence_with_statement_unit():
    evidence = build_trusted_calculation_evidence(
        statement_result=_statement_result(),
        note_result=_note_result(),
        expected_identity=IDENTITY,
    )

    by_metric_period = {
        (item["metric"], item["period"]): item
        for item in evidence
    }
    assert by_metric_period[("goodwill_gross", "2025-12-31")]["value"] == "34813270"
    assert by_metric_period[("goodwill_impairment_allowance", "2025-12-31")]["value"] == "556411"
    assert by_metric_period[("goodwill_net", "2025-12-31")]["value"] == "34256859"
    assert by_metric_period[("goodwill_gross", "2025-12-31")]["unit"] == "人民币千元"
    assert "商誉总额" in by_metric_period[("goodwill_gross", "2025-12-31")]["aliases"]
    assert by_metric_period[("goodwill_gross", "2025-12-31")]["financial_scope"] == "consolidated"
    assert by_metric_period[("goodwill_net", "2025-12-31")]["financial_scope"] == "consolidated"
    assert by_metric_period[("goodwill_gross", "2025-12-31")]["source_lineage"]
    assert by_metric_period[("goodwill_net", "2025-12-31")]["source_lineage"]
    assert (
        by_metric_period[("goodwill_gross", "2025-12-31")]["source_lineage"]
        != by_metric_period[("goodwill_net", "2025-12-31")]["source_lineage"]
    )
    other_component = next(
        item
        for item in evidence
        if item["metric_name"] == "其他(i)" and item["period"] == "2025-12-31"
    )
    assert "其他" in other_component["aliases"]
    assert "其他商誉" in other_component["aliases"]
    assert all(item["company_id"] == IDENTITY["company_id"] for item in evidence)


def test_goodwill_component_sums_do_not_cross_financial_scopes():
    note = _note_result()
    table = note["tables"][0]
    table.update(
        {
            "financial_scope": "",
            "unit": "人民币千元",
            "headers": ["商誉-", "2025年12月31日合并", "2025年12月31日公司"],
            "column_scopes": {
                "2025年12月31日合并": "consolidated",
                "2025年12月31日公司": "parent_company",
            },
            "records": [
                {"商誉-": "KUKA集团", "2025年12月31日合并": "10", "2025年12月31日公司": "1"},
                {"商誉-": "TLSC集团", "2025年12月31日合并": "20", "2025年12月31日公司": "2"},
                {"商誉-": "", "2025年12月31日合并": "30", "2025年12月31日公司": "3"},
                {"商誉-": "减:减值准备", "2025年12月31日合并": "0", "2025年12月31日公司": "0"},
                {"商誉-": "", "2025年12月31日合并": "30", "2025年12月31日公司": "3"},
            ],
        }
    )

    evidence = build_trusted_calculation_evidence(
        statement_result=None,
        note_result=note,
        expected_identity=IDENTITY,
    )

    component_sums = {
        (item["financial_scope"], item["value"])
        for item in evidence
        if item["metric"].startswith("goodwill_component_sum_")
    }
    assert component_sums == {("consolidated", "30"), ("parent", "3")}


def test_statement_note_reference_column_is_not_materialized_as_financial_evidence():
    statement = _statement_result()
    table = statement["tables"][0]
    table["headers"].insert(1, "附注七")
    table["records"][0]["附注七"] = "19"

    evidence = build_trusted_calculation_evidence(
        statement_result=statement,
        note_result=None,
        expected_identity=IDENTITY,
    )

    goodwill = [item for item in evidence if item["metric"] == "goodwill_net"]
    assert {(item["period"], item["value"]) for item in goodwill} == {
        ("2025-12-31", "34256859"),
        ("2024-12-31", "29581014"),
    }
    assert all(item["value"] != "19" for item in evidence)


def test_combined_consolidated_parent_statement_fails_closed_without_column_scope():
    statement = _statement_result(company_id="601857-中国石油", task_id="task-petrochina")
    statement["tables"][0].update(
        {
            "financial_scope": "",
            "headers": [
                "资产",
                "附注",
                "2025年12月31日",
                "2024年12月31日",
                "2025年12月31日#2",
                "2024年12月31日#2",
            ],
            "records": [
                {
                    "资产": "商誉",
                    "附注": "21",
                    "2025年12月31日": "7,263",
                    "2024年12月31日": "7,436",
                    "2025年12月31日#2": "77",
                    "2024年12月31日#2": "77",
                }
            ],
        }
    )
    identity = {
        "market": "CN",
        "company_id": "601857-中国石油",
        "filing_id": "CN:601857-中国石油:2025-annual",
        "parse_run_id": "task-petrochina",
    }

    evidence = build_trusted_calculation_evidence(
        statement_result=statement,
        note_result=None,
        expected_identity=identity,
    )

    assert evidence == ()

    statement["tables"][0]["records"][0]["2025年12月31日#2"] = "-"
    statement["tables"][0]["records"][0]["2024年12月31日#2"] = "-"
    single_value_per_period = build_trusted_calculation_evidence(
        statement_result=statement,
        note_result=None,
        expected_identity=identity,
    )

    assert single_value_per_period == ()


def test_combined_statement_materializes_values_with_explicit_column_scopes():
    statement = _statement_result(company_id="601857-中国石油", task_id="task-petrochina")
    table = statement["tables"][0]
    table.update(
        {
            "financial_scope": "",
            "headers": [
                "资产",
                "附注",
                "2025年12月31日/合并",
                "2024年12月31日/合并",
                "2025年12月31日/公司",
                "2024年12月31日/公司",
            ],
            "column_scopes": {
                "2025年12月31日/合并": "consolidated",
                "2024年12月31日/合并": "consolidated",
                "2025年12月31日/公司": "parent_company",
                "2024年12月31日/公司": "parent_company",
            },
            "records": [
                {
                    "资产": "商誉",
                    "附注": "21",
                    "2025年12月31日/合并": "7,263",
                    "2024年12月31日/合并": "7,436",
                    "2025年12月31日/公司": "77",
                    "2024年12月31日/公司": "77",
                }
            ],
        }
    )
    identity = {
        "market": "CN",
        "company_id": "601857-中国石油",
        "filing_id": "CN:601857-中国石油:2025-annual",
        "parse_run_id": "task-petrochina",
    }

    evidence = build_trusted_calculation_evidence(
        statement_result=statement,
        note_result=None,
        expected_identity=identity,
    )

    values = {
        (item["period"], item["financial_scope"]): item["value"]
        for item in evidence
        if item["metric"] == "goodwill_net"
    }
    assert values == {
        ("2025-12-31", "consolidated"): "7263",
        ("2024-12-31", "consolidated"): "7436",
        ("2025-12-31", "parent"): "77",
        ("2024-12-31", "parent"): "77",
    }


def test_generic_statement_change_keeps_same_table_lineage_without_explicit_scope():
    statement = _statement_result()
    statement["tables"][0].pop("financial_scope")
    statement["tables"][0]["records"][0].update(
        {
            "资产": "营业收入",
            "2025年12月31日": "120",
            "2024年12月31日": "100",
        }
    )

    evidence = build_trusted_calculation_evidence(
        statement_result=statement,
        note_result=None,
        expected_identity=IDENTITY,
    )

    change = next(item for item in evidence if item["metric"] == "operating_revenue_absolute_change")
    assert change["value"] == "20"
    assert change["change_direction"] == "increase"
    assert change["financial_scope"] == ""
    assert change["source_lineage"]


def test_builds_byd_split_goodwill_evidence_and_reconciles_statement_net():
    evidence = build_trusted_calculation_evidence(
        statement_result=_byd_statement_result(),
        note_result=_byd_note_result(),
        expected_identity=BYD_IDENTITY,
    )

    balances = {
        (item["metric"], item["period"]): item["value"]
        for item in evidence
        if item["metric"] in {"goodwill_net", "goodwill_gross", "goodwill_impairment_allowance"}
    }
    assert balances[("goodwill_gross", "2025-12-31")] == "4437242"
    assert balances[("goodwill_impairment_allowance", "2025-12-31")] == "9671"
    assert balances[("goodwill_net", "2025-12-31")] == "4427571"
    assert all(item["unit"] == "千元" for item in evidence)
    assert all(item["value"] != "19" for item in evidence)


def test_goodwill_component_aliases_only_remove_trailing_roman_footnotes():
    fullwidth_evidence = build_trusted_calculation_evidence(
        statement_result=_statement_result(),
        note_result=_note_result(other_label="其他（ii）"),
        expected_identity=IDENTITY,
    )
    embedded_evidence = build_trusted_calculation_evidence(
        statement_result=_statement_result(),
        note_result=_note_result(other_label="其他(i)业务"),
        expected_identity=IDENTITY,
    )
    embedded_legal_suffix_evidence = build_trusted_calculation_evidence(
        statement_result=_statement_result(),
        note_result=_note_result(other_label="其他有限公司业务"),
        expected_identity=IDENTITY,
    )

    fullwidth_component = next(
        item
        for item in fullwidth_evidence
        if item["metric_name"] == "其他（ii）" and item["period"] == "2025-12-31"
    )
    embedded_component = next(
        item
        for item in embedded_evidence
        if item["metric_name"] == "其他(i)业务" and item["period"] == "2025-12-31"
    )
    embedded_legal_suffix_component = next(
        item
        for item in embedded_legal_suffix_evidence
        if item["metric_name"] == "其他有限公司业务" and item["period"] == "2025-12-31"
    )
    assert "其他" in fullwidth_component["aliases"]
    assert "其他" not in embedded_component["aliases"]
    assert "其他业务" not in embedded_component["aliases"]
    assert "其他业务" not in embedded_legal_suffix_component["aliases"]


def test_rejects_cross_company_or_cross_parse_run_retrieval_results():
    wrong_company = build_trusted_calculation_evidence(
        statement_result=_statement_result(company_id="600104-上汽集团"),
        note_result=None,
        expected_identity=IDENTITY,
    )
    wrong_task = build_trusted_calculation_evidence(
        statement_result=_statement_result(task_id="task-other"),
        note_result=None,
        expected_identity=IDENTITY,
    )

    assert wrong_company == ()
    assert wrong_task == ()


def test_builds_saic_separate_gross_and_allowance_tables_with_aligned_periods():
    evidence = build_trusted_calculation_evidence(
        statement_result=_saic_statement_result(),
        note_result=_saic_note_result(),
        expected_identity=SAIC_IDENTITY,
    )

    by_metric_period = {
        (item["metric"], item["period"]): item
        for item in evidence
        if item["metric"] in {"goodwill_net", "goodwill_gross", "goodwill_impairment_allowance"}
    }
    assert by_metric_period[("goodwill_gross", "2024-12-31")]["value"] == "1302999061.44"
    assert by_metric_period[("goodwill_gross", "2025-12-31")]["value"] == "1282085915.36"
    assert by_metric_period[("goodwill_impairment_allowance", "2024-12-31")]["value"] == "104788944.85"
    assert by_metric_period[("goodwill_impairment_allowance", "2025-12-31")]["value"] == "98963594.89"
    assert by_metric_period[("goodwill_net", "2024-12-31")]["value"] == "1198210116.59"
    assert by_metric_period[("goodwill_net", "2025-12-31")]["value"] == "1183122320.47"
    assert all(
        item["table_index"] != 166
        for item in evidence
        if item["metric"] == "goodwill_gross"
    )
    assert all(
        item["period"] in {"2024-12-31", "2025-12-31"}
        for item in evidence
        if item["metric"] in {"goodwill_gross", "goodwill_impairment_allowance"}
    )
    vision_component = next(
        item
        for item in evidence
        if item["metric_name"] == "华域视觉科技(上海)有限公司(以下简称“华域视觉”)"
        and item["period"] == "2025-12-31"
    )
    finance_component = next(
        item
        for item in evidence
        if item["metric_name"] == "上汽通用汽车金融有限责任公司"
        and item["period"] == "2025-12-31"
    )
    component_sum = next(
        item
        for item in evidence
        if item["source_type"] == "trusted_backend_derived_fact"
        and item["value"] == "1114493515.41"
        and item["period"] == "2025-12-31"
    )
    assert "华域视觉" in vision_component["aliases"]
    assert "华域视觉科技(上海)" in vision_component["aliases"]
    assert "上汽通用汽车金融" in finance_component["aliases"]
    assert "华域视觉 + 上汽通用汽车金融" in component_sum["aliases"]
    assert "上汽通用汽车金融 + 华域视觉" in component_sum["aliases"]
    disposed_component = next(
        item
        for item in evidence
        if item["metric"].endswith("_absolute_change")
        and item["metric_name"] == "上海机动车回收服务中心有限公司转出额"
    )
    assert disposed_component["value"] == "15087796.12"
    assert disposed_component["period"] == "2025-12-31"
    assert disposed_component["change_direction"] == "decrease"
    assert "转出商誉账面原值" in disposed_component["aliases"]


def test_statement_evidence_distinguishes_parent_equity_from_parent_profit():
    statement = _saic_statement_result()
    statement["tables"][0]["records"].extend(
        [
            {
                "资产": "资产总计",
                "2025年12月31日": "960,207,461,450.69",
                "2024年12月31日": "957,143,417,731.69",
            },
            {
                "资产": "归属于母公司所有者权益(或股东权益)合计",
                "2025年12月31日": "298,812,278,173.08",
                "2024年12月31日": "287,840,094,973.12",
            },
        ]
    )

    evidence = build_trusted_calculation_evidence(
        statement_result=statement,
        note_result=None,
        expected_identity=SAIC_IDENTITY,
    )

    by_metric_period = {(item["metric"], item["period"]): item for item in evidence}
    assert by_metric_period[("total_assets", "2025-12-31")]["value"] == "960207461450.69"
    assert by_metric_period[("parent_shareholders_equity", "2025-12-31")]["value"] == "298812278173.08"
    assert not any(item["metric"] == "parent_net_profit" for item in evidence)


def test_rejects_saic_note_tables_from_another_parse_run():
    evidence = build_trusted_calculation_evidence(
        statement_result=_saic_statement_result(),
        note_result=_saic_note_result(task_id="task-other"),
        expected_identity=SAIC_IDENTITY,
    )

    assert not any(
        item["metric"] in {"goodwill_gross", "goodwill_impairment_allowance"}
        for item in evidence
    )


def test_rejects_saic_evidence_when_research_identity_is_incomplete():
    incomplete_identity = {key: value for key, value in SAIC_IDENTITY.items() if key != "parse_run_id"}

    evidence = build_trusted_calculation_evidence(
        statement_result=_saic_statement_result(),
        note_result=_saic_note_result(),
        expected_identity=incomplete_identity,
    )

    assert evidence == ()


def test_trusted_statement_rows_preserve_sec_periods_and_external_locators():
    identity = {
        "market": "US",
        "company_id": "US:0001045810",
        "filing_id": "US:0001045810:0001045810-26-000021",
        "parse_run_id": "run-nvda-2026",
    }
    source_url = "https://www.sec.gov/Archives/edgar/data/1045810/filing.htm"
    rows = tuple(
        {
            "statement_type": "income_statement",
            "metric_key": "operating_revenue",
            "metric_name": "Revenues",
            "period": period,
            "normalized_value": value,
            "unit": "USD",
            "currency": "USD",
            "report_id": "2026-10-K-0001045810-26-000021",
            "file": "reports/2026-10-K/metrics/financial_data.json",
            "source_url": source_url,
            "source_anchor": anchor,
            "xbrl_tag": "us-gaap:Revenues",
            "evidence_source_type": "sec_xbrl_fact",
        }
        for period, value, anchor in (
            ("2024-01-28", "60922000000", "f-74"),
            ("2025-01-26", "130497000000", "f-73"),
            ("2026-01-25", "215938000000", "f-72"),
        )
    )

    evidence = build_trusted_statement_row_evidence(rows, expected_identity=identity)
    revenue = [item for item in evidence if item["metric"] == "operating_revenue"]

    assert len(revenue) == 3
    assert {item["period"] for item in revenue} == {"2024-01-28", "2025-01-26", "2026-01-25"}
    assert len({item["evidence_id"] for item in revenue}) == 3
    assert all(item["market"] == "US" for item in revenue)
    assert all(item["company_id"] == "US:0001045810" for item in revenue)
    assert all(item["source_url"] == source_url for item in revenue)
    assert {item["source_anchor"] for item in revenue} == {"f-72", "f-73", "f-74"}
    assert len([item for item in evidence if item["metric"] == "operating_revenue_absolute_change"]) == 2
