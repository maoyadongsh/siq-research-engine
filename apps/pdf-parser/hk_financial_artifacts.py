from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from financial_extractor import FINANCIAL_CHECKS_SCHEMA_VERSION, FINANCIAL_DATA_SCHEMA_VERSION, FINANCIAL_RULE_VERSION

HK_FINANCIAL_PROFILE_VERSION = "hk-pdf-financial-profile-v2"

REPO_ROOT = Path(__file__).resolve().parents[2]
RULES_SRC = REPO_ROOT / "services" / "market-report-rules" / "src"
HK_SCRIPTS = REPO_ROOT / "scripts" / "hk"
for path in (RULES_SRC, HK_SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from hk_evidence_lib import (  # noqa: E402
    _default_currency,
    _default_unit,
    _document_full_with_sidecars,
    _markdown_statement_tables,
    infer_metadata,
    parsed_tables_from_document_full,
    read_json,
)
from market_report_rules_service.models import AccountingStandard, Market, ParsedArtifact  # noqa: E402
from market_report_rules_service.normalization import parse_date  # noqa: E402
from market_report_rules_service.pipeline import process_artifact  # noqa: E402


def build_hk_financial_artifacts(
    task: dict[str, Any],
    markdown: str,
    *,
    result_dir_path: str,
    filename: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    result_dir = Path(result_dir_path)
    document_full = _document_full_with_sidecars(result_dir)
    if not document_full:
        document_full = {"task": task, "markdown": {"content": markdown}, "content_list": []}
    document_full.setdefault("task", task)
    document_full.setdefault("markdown", {"content": markdown})
    if isinstance(document_full.get("markdown"), dict) and not document_full["markdown"].get("content"):
        document_full["markdown"]["content"] = markdown
    enhanced = _read_json(result_dir / "content_list_enhanced.json")
    artifact = _build_artifact(task, filename or task.get("filename"), result_dir, document_full, enhanced)
    result = process_artifact(artifact, include_load_plan=False)
    financial_data = result.extraction.model_dump(mode="json")
    financial_checks = result.validation.model_dump(mode="json")
    financial_data.update(
        {
            "schema_version": FINANCIAL_DATA_SCHEMA_VERSION,
            "rule_version": FINANCIAL_RULE_VERSION,
            "profile_rule_version": HK_FINANCIAL_PROFILE_VERSION,
            "task_id": task.get("task_id"),
            "filename": filename or task.get("filename"),
            "report_kind": _report_kind(artifact.report_type),
            "report_year": artifact.fiscal_year,
        }
    )
    financial_data["summary"] = _financial_summary(financial_data)
    financial_checks.update(
        {
            "schema_version": FINANCIAL_CHECKS_SCHEMA_VERSION,
            "rule_version": FINANCIAL_RULE_VERSION,
            "profile_rule_version": HK_FINANCIAL_PROFILE_VERSION,
            "task_id": task.get("task_id"),
            "filename": filename or task.get("filename"),
        }
    )
    financial_checks["summary"] = _checks_summary(financial_checks.get("checks") or [])
    return financial_data, financial_checks


def _build_artifact(
    task: dict[str, Any],
    filename: str | None,
    result_dir: Path,
    document_full: dict[str, Any],
    enhanced: dict[str, Any],
) -> ParsedArtifact:
    pdf_path = _pdf_path(task, filename, result_dir)
    metadata_path = pdf_path.with_suffix(pdf_path.suffix + ".metadata.json")
    metadata = infer_metadata(pdf_path, metadata_path if metadata_path.exists() else None)
    metadata["ticker"] = _ticker_from_filename(filename) or metadata.get("ticker") or task.get("ticker") or "UNKNOWN"
    metadata["company_name"] = _company_from_filename(filename) or metadata.get("company_name")
    metadata["period_end"] = _period_end_from_filename(filename) or metadata.get("period_end")
    metadata["fiscal_year"] = _year(metadata.get("period_end")) or metadata.get("fiscal_year")
    metadata["report_type"] = metadata.get("report_type") or "annual"
    metadata["form"] = metadata.get("form") or metadata["report_type"]
    content_report_type = _hk_report_type_from_content(document_full, markdown=_read_markdown(result_dir))
    if content_report_type:
        metadata["report_type"] = content_report_type
        metadata["form"] = content_report_type
    metadata["industry_profile"] = _industry_from_identity(metadata.get("ticker"), metadata.get("company_name"), metadata.get("industry_profile"))
    metadata["accounting_standard"] = metadata.get("accounting_standard") or "HKFRS"
    tables = parsed_tables_from_document_full(document_full, enhanced)
    tables.extend(_markdown_statement_tables(result_dir, start_index=len(tables)))
    return ParsedArtifact(
        artifact_id=f"HK:{metadata['ticker']}:{task.get('task_id') or result_dir.name}",
        market=Market.HK,
        company_id=f"HK:{metadata['ticker']}",
        ticker=str(metadata["ticker"]),
        company_name=metadata.get("company_name"),
        report_id=f"HK:{metadata['ticker']}:{task.get('task_id') or result_dir.name}",
        report_type=metadata.get("report_type") or "annual",
        report_form=metadata.get("form") or "annual",
        fiscal_year=metadata.get("fiscal_year"),
        fiscal_period="FY",
        period_end=parse_date(metadata.get("period_end")),
        accounting_standard=AccountingStandard(metadata.get("accounting_standard") or "HKFRS"),
        industry_profile=metadata.get("industry_profile") or "general",
        currency=_default_currency(metadata),
        unit=_default_unit(document_full),
        source_url=task.get("source_url") or "",
        source_files={"parser_result": str(result_dir), "pdf": str(pdf_path)},
        tables=tables,
        document_full=document_full,
        metadata=metadata,
    )


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _pdf_path(task: dict[str, Any], filename: str | None, result_dir: Path) -> Path:
    source_files = task.get("source_files") if isinstance(task.get("source_files"), dict) else {}
    pdf_info = source_files.get("pdf") if isinstance(source_files.get("pdf"), dict) else {}
    if pdf_info.get("path"):
        return Path(str(pdf_info["path"]))
    return result_dir.parent.parent / "uploads" / f"{task.get('task_id') or result_dir.name}.pdf"


def _ticker_from_filename(filename: str | None) -> str | None:
    match = re.search(r"_HK_([0-9]{4,5})_", str(filename or ""), flags=re.I)
    return match.group(1).zfill(5) if match else None


def _company_from_filename(filename: str | None) -> str | None:
    text = str(filename or "")
    marker = "_HK_"
    if marker in text:
        return text.split(marker, 1)[0].replace("_", " ")
    return None


def _period_end_from_filename(filename: str | None) -> str | None:
    match = re.search(r"(20\d{2}-\d{2}-\d{2})", str(filename or ""))
    return match.group(1) if match else None


def _year(value: Any) -> int | None:
    try:
        return int(str(value or "")[:4])
    except ValueError:
        return None


def _industry_from_identity(ticker: Any, company_name: Any, fallback: Any) -> str:
    text = f"{ticker or ''} {company_name or ''}".lower()
    if any(term in text for term in ("reit", "link")):
        return "real_estate"
    return str(fallback or "general")


def _report_kind(report_type: str | None) -> str:
    if report_type in {"supplemental_announcement", "corporate_communication_notice", "overseas_regulatory_announcement"}:
        return report_type
    if report_type == "semiannual":
        return "interim_report"
    if report_type == "quarterly":
        return "quarterly_report"
    return "annual_report"


def _hk_report_type_from_content(document_full: dict[str, Any], markdown: str | None = None) -> str | None:
    markdown_payload = document_full.get("markdown") if isinstance(document_full.get("markdown"), dict) else {}
    text = str(markdown or markdown_payload.get("content") or "")[:12000]
    normalized = re.sub(r"\s+", " ", text).lower()
    compact = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", normalized)
    if not normalized:
        return None
    if "supplemental announcement to the annual report" in normalized or "supplemental announcement" in normalized:
        return "supplemental_announcement"
    if "overseas regulatory announcement" in normalized:
        return "overseas_regulatory_announcement"
    if (
        "notice of publication of annual report" in normalized
        or "notice of publication of annual report" in compact
        or "current corporate communication" in normalized
        or "current corporate communications" in normalized
        or "corporate communications" in normalized and "annual report" in normalized and "available on" in normalized
        or "electronic dissemination of corporate communications" in normalized
    ):
        return "corporate_communication_notice"
    if (
        "dear non-registered shareholder" in normalized
        and "annual report" in normalized
        and ("available on" in normalized or "published in english and chinese" in normalized)
    ):
        return "corporate_communication_notice"
    if "致非登記股東" in text and ("年報" in text or "年度報告" in text) and ("網站" in text or "登載" in text):
        return "corporate_communication_notice"
    return None


def _read_markdown(result_dir: Path) -> str:
    for name in ("result.md", "result_complete.md"):
        path = result_dir / name
        if path.exists():
            return path.read_text(encoding="utf-8", errors="ignore")
    return ""


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
