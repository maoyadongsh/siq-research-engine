import importlib.util
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_SPEC = importlib.util.spec_from_file_location("workflow_under_test", BACKEND_ROOT / "routers" / "workflow.py")
assert WORKFLOW_SPEC and WORKFLOW_SPEC.loader
workflow = importlib.util.module_from_spec(WORKFLOW_SPEC)
WORKFLOW_SPEC.loader.exec_module(workflow)


class DummyBuilder:
    @staticmethod
    def evidence_urls(task_id, page, table_index):
        return {
            "open_pdf_page_url": f"/api/pdf_page/{task_id}/{page}" if page else "",
            "open_source_page_url": f"/api/source/{task_id}/page/{page}" if page else "",
            "open_source_table_url": f"/api/source/{task_id}/table/{table_index}" if table_index else "",
        }


def test_build_pdf_refs_uses_report_tables_when_evidence_is_empty():
    refs = workflow._build_pdf_refs_from_import(
        DummyBuilder(),
        identity={"company_id": "GENBASF-BASF"},
        report_id="2025-annual",
        task_id="03690a47-062e-42eb-9ad7-d609a87cf777",
        evidence=[],
        report_json={
            "tables": [
                {
                    "table_index": 260,
                    "line": 7420,
                    "pdf_page_number": 412,
                    "heading": "Personnel expenses",
                    "preview": "Personnel expenses 12,299",
                }
            ]
        },
        row={"quality": {"table_index": []}, "enhanced": {"tables": []}},
    )

    assert refs == [
        {
            "company_id": "GENBASF-BASF",
            "report_id": "2025-annual",
            "task_id": "03690a47-062e-42eb-9ad7-d609a87cf777",
            "pdf_page_number": 412,
            "table_index": 260,
            "md_line": 7420,
            "source_type": "report_json_table",
            "heading": "Personnel expenses",
            "preview": "Personnel expenses 12,299",
            "open_pdf_page_url": "/api/pdf_page/03690a47-062e-42eb-9ad7-d609a87cf777/412",
            "open_source_page_url": "/api/source/03690a47-062e-42eb-9ad7-d609a87cf777/page/412",
            "open_source_table_url": "/api/source/03690a47-062e-42eb-9ad7-d609a87cf777/table/260",
        }
    ]


def test_build_pdf_refs_deduplicates_evidence_and_table_refs():
    refs = workflow._build_pdf_refs_from_import(
        DummyBuilder(),
        identity={"company_id": "000001-测试公司"},
        report_id="2025-annual",
        task_id="11111111-1111-4111-8111-111111111111",
        evidence=[
            {
                "company_id": "000001-测试公司",
                "report_id": "2025-annual",
                "task_id": "11111111-1111-4111-8111-111111111111",
                "pdf_page_number": 12,
                "table_index": 3,
                "md_line": 120,
                "source_kind": "upstream_financial_data",
                "metric_key": "operating_revenue",
            }
        ],
        report_json={"tables": [{"table_index": 3, "line": 120, "pdf_page_number": 12, "heading": "利润表"}]},
        row={"quality": {"table_index": [{"table_index": 3, "line": 120, "pdf_page_number": 12}]}, "enhanced": {"tables": []}},
    )

    assert len(refs) == 3
    assert {ref["source_type"] for ref in refs} == {
        "upstream_financial_data",
        "report_json_table",
        "quality_table_index",
    }
    assert all(ref["open_source_table_url"].endswith("/table/3") for ref in refs)
