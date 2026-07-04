import json
import sys
from pathlib import Path


KR_DIR = Path(__file__).resolve().parents[1]
if str(KR_DIR) not in sys.path:
    sys.path.insert(0, str(KR_DIR))

import kr_pdf_wiki_lib as krwiki


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _minimal_parser_result(root: Path) -> Path:
    result_dir = root / "task-kr-1"
    _write_json(
        result_dir / "manifest.json",
        {
            "task_id": "task-kr-1",
            "market": "KR",
            "source_file": "005930_2025_annual.pdf",
            "company_name": "Samsung Electronics",
            "ticker": "005930",
            "report_year": 2025,
            "report_type": "annual",
        },
    )
    _write_json(
        result_dir / "document_full.json",
        {
            "pages": [
                {
                    "page": 78,
                    "text": "Consolidated Statement of Financial Position\nTotal assets 12345",
                    "tables": [
                        {
                            "table_index": 1,
                            "caption": "Consolidated Statement of Financial Position",
                            "rows": [["Assets", "2025"], ["Total assets", "12345"]],
                        }
                    ],
                }
            ]
        },
    )
    _write_json(
        result_dir / "content_list_enhanced.json",
        {
            "items": [
                {
                    "type": "table",
                    "page_idx": 77,
                    "table_index": 1,
                    "caption": "Consolidated Statement of Financial Position",
                    "text": "Total assets 12345",
                }
            ]
        },
    )
    (result_dir / "report_complete.md").write_text(
        "# Samsung Electronics 2025 Annual Report\n\n"
        "## Consolidated Statement of Financial Position\n\n"
        "| Item | 2025 |\n| --- | ---: |\n| Total assets | 12345 |\n",
        encoding="utf-8",
    )
    return result_dir


def test_write_kr_pdf_wiki_package_keeps_pdf_page_evidence(tmp_path: Path):
    pdf_path = tmp_path / "005930_2025_annual.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    result_dir = _minimal_parser_result(tmp_path / "results")
    output_root = tmp_path / "wiki" / "kr"

    package_dir = krwiki.write_kr_pdf_wiki_package(pdf_path, result_dir, output_root, force=True)

    assert package_dir == output_root / "companies" / "005930-SamsungElectronics" / "reports" / "2025-annual_task-kr-1"
    manifest = json.loads((package_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["market"] == "KR"
    assert manifest["ticker"] == "005930"
    assert manifest["report_id"] == "2025-annual_task-kr-1"
    assert manifest["pdf_parser_task_id"] == "task-kr-1"
    assert manifest["paths"]["report_complete"] == "parser/report_complete.md"

    source_map = json.loads((package_dir / "qa" / "source_map.json").read_text(encoding="utf-8"))
    evidence = source_map["evidence"][0]
    assert evidence["market"] == "KR"
    assert evidence["report_id"] == "2025-annual_task-kr-1"
    assert evidence["pdf_page_number"] == 78
    assert evidence["table_index"] == 1
    assert evidence["md_line"] == 3
    assert evidence["pdf_parser_task_id"] == "task-kr-1"

    quality = json.loads((package_dir / "qa" / "quality_report.json").read_text(encoding="utf-8"))
    assert quality["market"] == "KR"
    assert quality["financial_checks"]["status"] == "not_generated"
    assert "KR PDF" in quality["financial_checks"]["notes"][0]

    company_catalog = json.loads((output_root / "_meta" / "companies.json").read_text(encoding="utf-8"))
    report_catalog = json.loads((output_root / "_meta" / "reports.json").read_text(encoding="utf-8"))
    assert company_catalog["companies"][0]["ticker"] == "005930"
    assert report_catalog["reports"][0]["package_path"].endswith("2025-annual_task-kr-1")

    company_dir = output_root / "companies" / "005930-SamsungElectronics"
    for dirname in ("reports", "metrics", "evidence", "semantic", "graph", "analysis", "factcheck", "tracking"):
        assert (company_dir / dirname).is_dir()
    company_json = json.loads((company_dir / "company.json").read_text(encoding="utf-8"))
    assert company_json["company_id"] == "KR:005930"
    evidence_index = json.loads((package_dir / "evidence" / "evidence_index.json").read_text(encoding="utf-8"))
    retrieval_index = json.loads((package_dir / "semantic" / "retrieval_index.json").read_text(encoding="utf-8"))
    assert evidence_index["evidence"][0]["evidence_id"] == evidence["evidence_id"]
    assert retrieval_index["chunks"][0]["evidence_id"] == evidence["evidence_id"]


def test_infer_kr_pdf_metadata_prefers_metadata_file(tmp_path: Path):
    pdf_path = tmp_path / "lg_chem.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    result_dir = _minimal_parser_result(tmp_path / "results")
    metadata_path = tmp_path / "metadata.json"
    _write_json(
        metadata_path,
        {
            "ticker": "051910",
            "company_name": "LG Chem",
            "report_year": 2024,
            "report_type": "annual",
        },
    )

    metadata = krwiki.infer_kr_pdf_metadata(pdf_path, result_dir, metadata_path)

    assert metadata["ticker"] == "051910"
    assert metadata["company_name"] == "LG Chem"
    assert metadata["report_year"] == 2024
    assert metadata["report_type"] == "annual"


def test_write_kr_pdf_wiki_package_reads_legacy_table_index(tmp_path: Path):
    pdf_path = tmp_path / "005930_2025_annual.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    result_dir = tmp_path / "results" / "legacy-task"
    _write_json(
        result_dir / "manifest.json",
        {
            "task_id": "legacy-task",
            "market": "KR",
            "company_name": "Samsung Electronics",
            "ticker": "005930",
            "report_year": 2025,
            "report_type": "annual",
        },
    )
    _write_json(
        result_dir / "document_full.json",
        {
            "schema_version": 1,
            "task": {"task_id": "legacy-task"},
        },
    )
    _write_json(
        result_dir / "content_list_enhanced.json",
        {
            "schema_version": 10,
            "tables": [],
        },
    )
    _write_json(
        result_dir / "table_index.json",
        [
            {
                "table_index": 556,
                "heading": "Consolidated Statement of Financial Position",
                "line": 6053,
                "pdf_page_number": 320,
                "rows": 40,
                "cells": 160,
                "preview": "Assets Total assets",
            }
        ],
    )
    (result_dir / "report_complete.md").write_text(
        "# Samsung Electronics\n\n"
        "## Consolidated Statement of Financial Position\n\n"
        "| Assets | 2025 |\n",
        encoding="utf-8",
    )

    package_dir = krwiki.write_kr_pdf_wiki_package(pdf_path, result_dir, tmp_path / "wiki" / "kr", force=True)

    source_map = json.loads((package_dir / "qa" / "source_map.json").read_text(encoding="utf-8"))
    evidence = source_map["evidence"][0]
    assert evidence["table_index"] == 556
    assert evidence["pdf_page_number"] == 320
    assert evidence["caption"] == "Consolidated Statement of Financial Position"
    assert evidence["md_line"] == 6053
