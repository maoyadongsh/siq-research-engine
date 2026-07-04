from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from hk_financial_artifacts import build_hk_financial_artifacts


FINANCIAL_TABLE_SIGNAL = re.compile(
    r"financial highlight|statement of financial position|balance sheet|statement of profit or loss|"
    r"income statement|comprehensive income|statement of cash flows|cash flow statement|"
    r"operating revenue|turnover|revenue|total assets|total liabilities|net cash generated",
    flags=re.I,
)


def _hk_result_dirs() -> list[Path]:
    root = Path("data/pdf-parser/results")
    if not root.exists():
        return []
    result: list[Path] = []
    for path in sorted(root.iterdir()):
        document_full_path = path / "document_full.json"
        result_md_path = path / "result.md"
        if not document_full_path.exists() or not result_md_path.exists():
            continue
        try:
            document_full = json.loads(document_full_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        task = document_full.get("task") if isinstance(document_full, dict) else {}
        filename = str((task or {}).get("filename") or "")
        if "_HK_" in filename or "hkex" in filename.lower() or "sehk" in filename.lower():
            result.append(path)
    return result


def _table_text(item: dict) -> str:
    raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
    structure = item.get("structure") if isinstance(item.get("structure"), dict) else raw.get("structure")
    header_preview = structure.get("header_preview") if isinstance(structure, dict) else []
    fields = [
        item.get("title"),
        item.get("heading"),
        item.get("preview"),
        raw.get("title"),
        raw.get("heading"),
        raw.get("preview"),
        " ".join(str(value) for value in item.get("source_caption") or []),
        " ".join(str(value) for value in raw.get("source_caption") or []),
        " ".join(str(value) for value in header_preview or []),
    ]
    return " ".join(str(value or "") for value in fields)


def _has_financial_table_signal(table_index: object) -> bool:
    tables = table_index if isinstance(table_index, list) else (table_index or {}).get("tables") or []
    return any(isinstance(item, dict) and FINANCIAL_TABLE_SIGNAL.search(_table_text(item)) for item in tables)


@pytest.mark.parametrize("result_dir", _hk_result_dirs())
def test_all_local_hk_parser_samples_build_hk_financial_artifacts(result_dir: Path):
    document_full = json.loads((result_dir / "document_full.json").read_text(encoding="utf-8"))
    task = document_full.get("task") or {"task_id": result_dir.name, "filename": result_dir.name}
    markdown = (result_dir / "result.md").read_text(encoding="utf-8")

    data, checks = build_hk_financial_artifacts(
        task,
        markdown,
        result_dir_path=str(result_dir),
        filename=task.get("filename"),
    )

    assert data["market"] == "HK"
    assert checks["market"] == "HK"
    assert data["schema_version"] == 13
    assert checks["schema_version"] == 12
    assert "statements" in data
    assert "checks" in checks
    table_index_path = result_dir / "table_index.json"
    if table_index_path.exists():
        table_index = json.loads(table_index_path.read_text(encoding="utf-8"))
        table_count = len(table_index) if isinstance(table_index, list) else len(table_index.get("tables") or [])
        extracted_count = data["summary"]["statement_count"] + data["summary"].get("operating_metric_count", 0)
        if table_count and _has_financial_table_signal(table_index):
            assert extracted_count >= 1
        elif table_count:
            assert "No mapped HKEX/PDF table rows were extracted" in " ".join(data.get("warnings") or [])
        if table_count >= 20:
            assert data["summary"]["statement_count"] + data["summary"].get("operating_metric_count", 0) >= 1
    if result_dir.name == "50090c9f-a424-4d73-b28c-96fa60dd99ff":
        assert checks["overall_status"] != "skipped"
        assert data["summary"]["statement_count"] >= 2
