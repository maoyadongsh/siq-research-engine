from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from financial_extractor import FINANCIAL_CHECKS_SCHEMA_VERSION, FINANCIAL_DATA_SCHEMA_VERSION, FINANCIAL_RULE_VERSION
from jp_market_profile import detect_jp_report_kind


JP_FINANCIAL_PROFILE_VERSION = "jp-pdf-financial-profile-v1"

REPO_ROOT = Path(__file__).resolve().parents[2]
RULES_SRC = REPO_ROOT / "services" / "market-report-rules" / "src"
JP_SCRIPTS = REPO_ROOT / "scripts" / "jp"
HK_SCRIPTS = REPO_ROOT / "scripts" / "hk"
for path in (RULES_SRC, JP_SCRIPTS, HK_SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from jp_evidence_lib import build_jp_artifact  # noqa: E402
from market_report_rules_service.pipeline import process_artifact  # noqa: E402


def build_jp_financial_artifacts(
    task: dict[str, Any],
    markdown: str,
    *,
    result_dir_path: str,
    filename: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    result_dir = Path(result_dir_path)
    pdf_path = _pdf_path(task, filename, result_dir)
    artifact, metadata, document_full, _raw_facts = build_jp_artifact(pdf_path, None, result_dir)
    report_kind = detect_jp_report_kind(markdown or _markdown_from_document(document_full), filename or task.get("filename"))
    artifact = artifact.model_copy(
        update={
            "report_type": _artifact_report_type(report_kind, artifact.report_type),
            "report_form": metadata.get("form") or artifact.report_form,
            "metadata": {**artifact.metadata, "report_kind": report_kind},
        }
    )
    result = process_artifact(artifact, include_load_plan=False)
    financial_data = result.extraction.model_dump(mode="json")
    financial_checks = result.validation.model_dump(mode="json")
    resolved_filename = filename or task.get("filename") or pdf_path.name
    financial_data.update(
        {
            "schema_version": FINANCIAL_DATA_SCHEMA_VERSION,
            "rule_version": FINANCIAL_RULE_VERSION,
            "profile_rule_version": JP_FINANCIAL_PROFILE_VERSION,
            "task_id": task.get("task_id") or result_dir.name,
            "filename": resolved_filename,
            "report_kind": report_kind,
            "report_year": artifact.fiscal_year,
            "pdf_parser_result_path": str(result_dir),
            "source_filename": resolved_filename,
            "edinet_code": metadata.get("edinet_code"),
            "security_code": metadata.get("security_code") or artifact.ticker,
        }
    )
    financial_data["warnings"] = _normalize_jp_warnings(financial_data.get("warnings") or [])
    financial_data["summary"] = _financial_summary(financial_data)
    financial_checks.update(
        {
            "schema_version": FINANCIAL_CHECKS_SCHEMA_VERSION,
            "rule_version": FINANCIAL_RULE_VERSION,
            "profile_rule_version": JP_FINANCIAL_PROFILE_VERSION,
            "task_id": task.get("task_id") or result_dir.name,
            "filename": resolved_filename,
            "report_kind": report_kind,
            "report_year": artifact.fiscal_year,
        }
    )
    financial_checks["warnings"] = _normalize_jp_warnings(financial_checks.get("warnings") or [])
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


def _markdown_from_document(document_full: dict[str, Any]) -> str:
    markdown = document_full.get("markdown") if isinstance(document_full.get("markdown"), dict) else {}
    return str(markdown.get("content") or "")


def _artifact_report_type(report_kind: str, fallback: str | None) -> str:
    if report_kind == "jp_annual_securities_report":
        return "annual_securities_report"
    if report_kind == "jp_integrated_report":
        return "integrated_report"
    if report_kind == "jp_financial_highlights_only":
        return "financial_highlights"
    return fallback or "annual"


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


def _normalize_jp_warnings(warnings: list[Any]) -> list[str]:
    normalized: list[str] = []
    replacements = {
        "Use standard three-statement bridge checks.": "JP/IFRS 勾稽采用日本市场三表存在性、IFRS 资产负债等式与现金流桥接规则。",
    }
    for warning in warnings:
        text = str(warning or "").strip()
        if not text:
            continue
        text = replacements.get(text, text)
        if text not in normalized:
            normalized.append(text)
    return normalized
