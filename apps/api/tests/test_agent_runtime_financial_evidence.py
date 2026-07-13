from services.agent_runtime_financial_evidence import build_trusted_calculation_evidence

IDENTITY = {
    "market": "CN",
    "company_id": "000333-美的集团",
    "filing_id": "CN:000333-美的集团:2025-annual",
    "parse_run_id": "task-midea",
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


def _note_result() -> dict:
    return {
        "company_id": "000333-美的集团",
        "report_id": "2025-annual",
        "task_id": "task-midea",
        "tables": [
            {
                "report_id": "2025-annual",
                "task_id": "task-midea",
                "metric": "(21) 商誉",
                "headers": ["商誉-", "2025年12月31日", "2024年12月31日"],
                "unit": None,
                "pdf_page": 206,
                "table_index": 163,
                "md_line": 4325,
                "records": [
                    {"商誉-": "KUKA集团", "2025年12月31日": "23,435,302", "2024年12月31日": "21,415,464"},
                    {"商誉-": "其他(i)", "2025年12月31日": "7,930,808", "2024年12月31日": "5,220,530"},
                    {"商誉-": "", "2025年12月31日": "34,813,270", "2024年12月31日": "30,150,019"},
                    {"商誉-": "减:减值准备", "2025年12月31日": "(556,411)", "2024年12月31日": "(569,005)"},
                    {"商誉-": "", "2025年12月31日": "34,256,859", "2024年12月31日": "29,581,014"},
                ],
            }
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
    assert all(item["company_id"] == IDENTITY["company_id"] for item in evidence)


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
