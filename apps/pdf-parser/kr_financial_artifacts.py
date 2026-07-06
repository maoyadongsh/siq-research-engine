from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from financial_extractor import FINANCIAL_CHECKS_SCHEMA_VERSION, FINANCIAL_DATA_SCHEMA_VERSION, FINANCIAL_RULE_VERSION


KR_FINANCIAL_PROFILE_VERSION = "kr-pdf-financial-profile-v2"

REPO_ROOT = Path(__file__).resolve().parents[2]
RULES_SRC = REPO_ROOT / "services" / "market-report-rules" / "src"
KR_SCRIPTS = REPO_ROOT / "scripts" / "kr"
for path in (RULES_SRC, KR_SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from kr_pdf_wiki_lib import build_kr_pdf_artifact  # noqa: E402
from market_report_rules_service.pipeline import process_artifact  # noqa: E402


def build_kr_financial_artifacts(
    task: dict[str, Any],
    markdown: str,
    *,
    result_dir_path: str,
    filename: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    result_dir = Path(result_dir_path)
    pdf_path = _pdf_path(task, filename, result_dir)
    artifact, metadata, document_full = build_kr_pdf_artifact(pdf_path, result_dir)
    if document_full:
        document_full.setdefault("task", task)
        document_full.setdefault("markdown", {"content": markdown})
    result = process_artifact(artifact, include_load_plan=False)
    financial_data = result.extraction.model_dump(mode="json")
    financial_checks = result.validation.model_dump(mode="json")
    resolved_filename = filename or task.get("filename") or pdf_path.name
    financial_data.update(
        {
            "schema_version": FINANCIAL_DATA_SCHEMA_VERSION,
            "rule_version": FINANCIAL_RULE_VERSION,
            "profile_rule_version": KR_FINANCIAL_PROFILE_VERSION,
            "task_id": task.get("task_id") or result_dir.name,
            "filename": resolved_filename,
            "report_kind": "kr_business_report",
            "report_year": artifact.fiscal_year,
            "pdf_parser_result_path": str(result_dir),
            "source_filename": resolved_filename,
            "ticker": metadata.get("ticker") or artifact.ticker,
            "company_name": metadata.get("company_name") or artifact.company_name,
        }
    )
    financial_data["summary"] = _financial_summary(financial_data)
    financial_checks.update(
        {
            "schema_version": FINANCIAL_CHECKS_SCHEMA_VERSION,
            "rule_version": FINANCIAL_RULE_VERSION,
            "profile_rule_version": KR_FINANCIAL_PROFILE_VERSION,
            "task_id": task.get("task_id") or result_dir.name,
            "filename": resolved_filename,
            "report_kind": "kr_business_report",
            "report_year": artifact.fiscal_year,
        }
    )
    financial_checks["summary"] = _checks_summary(financial_checks.get("checks") or [])
    return financial_data, financial_checks


def _pdf_path(task: dict[str, Any], filename: str | None, result_dir: Path) -> Path:
    source_files = task.get("source_files") if isinstance(task.get("source_files"), dict) else {}
    pdf_info = source_files.get("pdf") if isinstance(source_files.get("pdf"), dict) else {}
    if pdf_info.get("path"):
        return Path(str(pdf_info["path"]))
    if filename or task.get("filename"):
        return result_dir / str(filename or task["filename"])
    return result_dir.parent.parent / "uploads" / f"{task.get('task_id') or result_dir.name}.pdf"


def _financial_summary(data: dict[str, Any]) -> dict[str, Any]:
    statements = data.get("statements") or []
    key_metrics = data.get("key_metrics") or []
    operating_metrics = data.get("operating_metrics") or []
    return {
        "statement_count": len(statements),
        "statement_item_count": sum(len(item.get("items") or []) for item in statements if isinstance(item, dict)),
        "key_metric_count": len(key_metrics),
        "operating_metric_count": len(operating_metrics),
        "scopes": sorted({item.get("scope") for item in statements if isinstance(item, dict) and item.get("scope")}),
    }


def _checks_summary(checks: list[dict[str, Any]]) -> dict[str, int]:
    summary = {"total": len(checks), "pass": 0, "fail": 0, "warning": 0, "skipped": 0}
    for check in checks:
        status = str(check.get("status") or "skipped")
        summary[status] = summary.get(status, 0) + 1
    return summary
