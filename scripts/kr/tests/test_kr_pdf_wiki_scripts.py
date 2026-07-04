import json
import sys
from pathlib import Path


KR_DIR = Path(__file__).resolve().parents[1]
if str(KR_DIR) not in sys.path:
    sys.path.insert(0, str(KR_DIR))

from discover_kr_parsed_cases import discover_kr_cases
from ingest_kr_case_set import ingest_kr_case_set


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_case(results_root: Path, downloads_root: Path, task_id: str, ticker: str, company: str) -> None:
    result_dir = results_root / task_id
    _write_json(
        result_dir / "manifest.json",
        {
            "task_id": task_id,
            "market": "KR",
            "ticker": ticker,
            "company_name": company,
            "report_year": 2025,
            "report_type": "annual",
            "source_file": f"{ticker}_2025.pdf",
        },
    )
    _write_json(
        result_dir / "document_full.json",
        {"pages": [{"page": 10, "tables": [{"table_index": 1, "caption": "Consolidated Statement of Cash Flows"}]}]},
    )
    _write_json(result_dir / "content_list_enhanced.json", {"items": []})
    (result_dir / "report_complete.md").write_text("# Report\n## Consolidated Statement of Cash Flows\n", encoding="utf-8")
    pdf_path = downloads_root / f"{ticker}_2025.pdf"
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(b"%PDF-1.4\n")


def test_discover_kr_cases_links_parser_result_to_download(tmp_path: Path):
    results_root = tmp_path / "results"
    downloads_root = tmp_path / "downloads"
    _write_case(results_root, downloads_root, "task-1", "005930", "Samsung Electronics")
    _write_json(results_root / "task-jp" / "manifest.json", {"task_id": "task-jp", "market": "JP"})

    cases = discover_kr_cases(results_root, None, downloads_root)

    assert len(cases) == 1
    assert cases[0]["ticker"] == "005930"
    assert cases[0]["parser_result_dir"].endswith("task-1")
    assert cases[0]["pdf_path"].endswith("005930_2025.pdf")


def test_ingest_case_set_writes_packages_and_meta_manifest(tmp_path: Path):
    results_root = tmp_path / "results"
    downloads_root = tmp_path / "downloads"
    output_root = tmp_path / "wiki" / "kr"
    _write_case(results_root, downloads_root, "task-1", "005930", "Samsung Electronics")
    case_set_path = tmp_path / "kr_cases.json"
    _write_json(
        case_set_path,
        {
            "market": "KR",
            "cases": [
                {
                    "ticker": "005930",
                    "company_name": "Samsung Electronics",
                    "pdf_path": str(downloads_root / "005930_2025.pdf"),
                    "parser_result_dir": str(results_root / "task-1"),
                }
            ],
        },
    )

    result = ingest_kr_case_set(case_set_path, output_root, force=True)

    assert result["created"] == 1
    assert result["failed"] == 0
    ingest_manifest = json.loads((output_root / "_meta" / "ingest_manifest.json").read_text(encoding="utf-8"))
    assert ingest_manifest["market"] == "KR"
    assert ingest_manifest["created"] == 1
