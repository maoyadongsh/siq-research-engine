import importlib.util
import sys
from pathlib import Path


def _load_importer():
    imports_dir = Path(__file__).resolve().parents[1]
    if str(imports_dir) not in sys.path:
        sys.path.insert(0, str(imports_dir))
    spec = importlib.util.spec_from_file_location(
        "a_share_document_full_importer_contract",
        imports_dir / "import_document_full_to_postgres.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _document_full_payload():
    return {
        "schema_version": 8,
        "generated_at": "2026-07-09T00:00:00Z",
        "task": {
            "task_id": "cn-task-001",
            "filename": "600000_2025_浦发银行_年度报告.pdf",
            "status": "completed",
            "pdf_page_count": 88,
        },
        "artifacts": {
            "document_full.json": {"path": "/tmp/results/cn-task-001/document_full.json", "exists": True},
            "result.md": {"path": "/tmp/results/cn-task-001/result.md", "exists": True},
        },
        "source_files": {
            "markdown": {"path": "/tmp/results/cn-task-001/result.md"},
            "complete_markdown": {"path": "/tmp/results/cn-task-001/result_complete.md"},
        },
        "markdown": {
            "pages": [
                {"page_number": 12, "preview": "资产负债表摘要"},
                {"page_number": 13, "preview": "利润表摘要"},
            ],
            "chars": 100,
            "line_count": 8,
        },
        "content_list": [
            {"type": "text", "page_idx": 11, "text": "合并资产负债表"},
            {"type": "table", "page_idx": 12, "table_body": "<table></table>"},
        ],
        "content_list_enhanced": {
            "tables": [
                {
                    "table_index": 1,
                    "pdf_page_number": 12,
                    "bbox": [0, 1, 2, 3],
                    "preview": "资产总计 100",
                    "source_image_path": "/tmp/page-12.png",
                    "structure": {"multi_level_header_candidate": False},
                }
            ],
            "financial_note_links": [
                {"item_name": "资产总计", "canonical_name": "total_assets", "table_index": 1, "page_number": 12}
            ],
            "footnotes": [{"page_number": 12, "text": "单位：百万元"}],
            "toc": [{"page_number": 12, "title": "财务报表", "level": 1}],
        },
        "quality_report": {
            "report_kind": "annual",
            "report_year": 2025,
            "table_index": [{"table_index": 1, "pdf_page_number": 12, "preview": "资产总计 100"}],
            "warnings": ["fixture-warning"],
        },
        "financial_data": {
            "report_kind": "annual",
            "report_year": 2025,
            "statements": [
                {
                    "statement_id": "bs",
                    "statement_type": "balance_sheet",
                    "statement_name": "合并资产负债表",
                    "unit": "人民币百万元",
                    "currency": "CNY",
                    "items": [
                        {
                            "name": "资产总计",
                            "canonical_name": "total_assets",
                            "values": {"2025": "100"},
                            "raw_values": {"2025": "100"},
                            "sources": {"2025": {"page_number": 12, "table_index": 1, "bbox": [0, 1, 2, 3]}},
                        }
                    ],
                },
                {
                    "statement_id": "is",
                    "statement_type": "income_statement",
                    "statement_name": "合并利润表",
                    "unit": "人民币百万元",
                    "currency": "CNY",
                    "items": [
                        {
                            "name": "营业收入",
                            "values": {"2025": "20"},
                            "raw_values": {"2025": "20"},
                            "sources": {"2025": {"page_number": 13, "table_index": 2}},
                        }
                    ],
                },
                {
                    "statement_id": "cf",
                    "statement_type": "cash_flow_statement",
                    "statement_name": "合并现金流量表",
                    "unit": "人民币百万元",
                    "currency": "CNY",
                    "items": [
                        {
                            "name": "经营活动产生的现金流量净额",
                            "values": {"2025": "10"},
                            "raw_values": {"2025": "10"},
                            "sources": {"2025": {"page_number": 14, "table_index": 3}},
                        }
                    ],
                },
            ],
            "key_metrics": [
                {
                    "name": "基本每股收益",
                    "values": {"2025": "1.23"},
                    "raw_values": {"2025": "1.23"},
                    "unit": "元/股",
                    "sources": {"2025": {"page_number": 13, "table_index": 2}},
                }
            ],
        },
        "financial_checks": {
            "overall_status": "pass",
            "checks": [
                {
                    "rule_id": "assets_equal_liabilities_plus_equity",
                    "rule_name": "资产=负债+权益",
                    "statement_type": "balance_sheet",
                    "period": "2025",
                    "status": "pass",
                    "diff": "0",
                    "tolerance": "1",
                }
            ],
        },
    }


def test_a_share_importer_contract_keeps_document_full_as_primary_input(tmp_path):
    importer = _load_importer()
    payload = _document_full_payload()
    document_full_path = tmp_path / "cn-task-001" / "document_full.json"
    document_full_path.parent.mkdir(parents=True)
    document_full_path.write_text("{}", encoding="utf-8")

    doc = importer.collect_document_params(payload, document_full_path)
    assert doc["task_id"] == "cn-task-001"
    assert doc["document_full_path"] == "/tmp/results/cn-task-001/document_full.json"
    assert doc["report_kind"] == "annual"

    tables = importer.table_rows(doc["task_id"], payload)
    assert len(tables) == 1
    assert tables[0]["table_index"] == 1
    assert tables[0]["pdf_page_number"] == 12

    statement_rows, item_rows = importer.financial_statement_rows(doc["task_id"], payload)
    assert [row["statement_type"] for row in statement_rows] == [
        "balance_sheet",
        "income_statement",
        "cash_flow_statement",
    ]
    assert {row["item_name"] for row in item_rows} == {"资产总计", "营业收入", "经营活动产生的现金流量净额"}

    split_rows = importer.statement_split_rows(doc["task_id"], payload, {"parse_run_id": "run-1"})
    assert len(split_rows["balance_sheet"]) == 1
    assert split_rows["balance_sheet"][0]["canonical_name"] == "total_assets"
    assert split_rows["balance_sheet"][0]["source_page_number"] == 12
    assert split_rows["balance_sheet"][0]["source_table_index"] == 1
    assert split_rows["income_statement"][0]["canonical_name"] == "operating_revenue"
    assert split_rows["cash_flow_statement"][0]["canonical_name"] == "operating_cash_flow_net"

    wide_rows = importer.financial_all_metrics_wide_rows(doc["task_id"], payload, {"parse_run_id": "run-1"})
    assert wide_rows[0]["period_key"] == "2025"
    all_metrics = wide_rows[0]["all_metrics"].obj
    assert "total_assets" in all_metrics
    assert "operating_revenue" in all_metrics
    assert "operating_cash_flow_net" in all_metrics
    assert "basic_eps" in all_metrics

    chunks = importer.document_chunk_rows(doc["task_id"], "run-1", payload)
    citations = importer.evidence_citation_rows(doc["task_id"], "run-1", payload)
    assert {chunk["chunk_type"] for chunk in chunks} == {"page", "table"}
    assert citations[0]["source_type"] == "table"
    assert citations[0]["page_number"] == 12


def test_a_share_importer_ignores_sibling_package_artifacts(tmp_path):
    importer = _load_importer()
    payload = _document_full_payload()
    document_dir = tmp_path / "cn-task-001"
    document_full_path = document_dir / "document_full.json"
    document_dir.mkdir(parents=True)
    document_full_path.write_text("{}", encoding="utf-8")
    for name in ("financial_data.json", "financial_checks.json", "quality_report.json"):
        (document_dir / name).write_text("{not valid json", encoding="utf-8")
    for name in ("result.md", "result_complete.md"):
        (document_dir / name).write_text("SHOULD NOT BE READ\n", encoding="utf-8")

    assert importer.find_document_full_files(tmp_path, recursive=True) == [document_full_path]

    doc = importer.collect_document_params(payload, document_full_path)
    assert doc["task_id"] == "cn-task-001"
    assert doc["report_kind"] == "annual"
    assert doc["report_year"] == 2025
    assert doc["document_full_path"] == "/tmp/results/cn-task-001/document_full.json"

    statement_rows, item_rows = importer.financial_statement_rows(doc["task_id"], payload)
    assert [row["statement_type"] for row in statement_rows] == [
        "balance_sheet",
        "income_statement",
        "cash_flow_statement",
    ]
    assert {row["item_name"] for row in item_rows} == {"资产总计", "营业收入", "经营活动产生的现金流量净额"}


def test_a_share_importer_delete_contract_covers_child_tables():
    dml = (Path(__file__).resolve().parents[2] / "dml" / "001_upsert_document_full.sql").read_text(encoding="utf-8")
    expected_child_tables = [
        "raw_payload_refs",
        "financial_all_metrics_wide",
        "financial_cash_flow_statement_items",
        "financial_income_statement_items",
        "financial_balance_sheet_items",
        "financial_checks",
        "financial_key_metrics",
        "financial_statement_items",
        "financial_statements",
        "financial_note_links",
        "toc_entries",
        "footnotes",
        "quality_warnings",
        "document_tables",
        "content_blocks",
        "document_pages",
        "document_artifacts",
        "document_chunks",
        "evidence_citations",
    ]
    for table in expected_child_tables:
        assert f"DELETE FROM pdf2md.{table} WHERE task_id = %(task_id)s;" in dml
