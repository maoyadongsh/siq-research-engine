#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NamedTuple

from jp_evidence_lib import REPO_ROOT, read_json, validate_evidence_package, write_jp_evidence_package, write_json


DEFAULT_RESULTS_ROOT = REPO_ROOT / "data" / "pdf-parser" / "results"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "data" / "wiki"
DEFAULT_REPORT = REPO_ROOT / "data" / "wiki" / "jp" / "_meta" / "jp_parser_ingest_report.json"


class ParserResult(NamedTuple):
    parser_dir: Path
    metadata: dict[str, Any]
    pdf_path: Path | None


def discover_jp_parser_results(results_root: Path) -> list[ParserResult]:
    rows: list[ParserResult] = []
    for document_full_path in sorted(results_root.glob("*/document_full.json")):
        parser_dir = document_full_path.parent
        document_full = read_json(document_full_path, {})
        quality = read_json(parser_dir / "quality_report.json", {})
        financial_data = read_json(parser_dir / "financial_data.json", {})
        if not _is_jp_result(document_full, quality, financial_data):
            continue
        metadata = _metadata_from_parser_result(parser_dir, document_full, quality, financial_data)
        rows.append(ParserResult(parser_dir=parser_dir, metadata=metadata, pdf_path=_pdf_path(document_full)))
    return rows


def ingest_parser_results(
    results_root: Path,
    output_root: Path,
    *,
    force: bool = False,
    limit: int = 0,
    task_id: str = "",
    ticker: str = "",
) -> dict[str, Any]:
    rows = discover_jp_parser_results(results_root)
    if task_id:
        rows = [row for row in rows if row.metadata.get("doc_id") == task_id or row.parser_dir.name == task_id]
    if ticker:
        rows = [row for row in rows if str(row.metadata.get("ticker") or "") == str(ticker)]
    if limit:
        rows = rows[:limit]
    report: dict[str, Any] = {
        "generated_at": _now_iso(),
        "results_root": str(results_root),
        "output_root": str(output_root),
        "summary": {"candidates": len(rows), "succeeded": 0, "failed": 0, "skipped": 0, "validation_failed": 0},
        "items": [],
    }
    for row in rows:
        item = {"task_id": row.metadata.get("doc_id"), "ticker": row.metadata.get("ticker"), "company_name": row.metadata.get("company_name"), "parser_result_dir": str(row.parser_dir)}
        if not row.pdf_path or not row.pdf_path.exists():
            report["summary"]["skipped"] += 1
            report["items"].append({**item, "status": "skipped", "reason": "source pdf missing", "pdf_path": str(row.pdf_path) if row.pdf_path else None})
            continue
        try:
            with tempfile.TemporaryDirectory(prefix="siq-jp-parser-metadata-") as temp_dir:
                doc_id = str(row.metadata["doc_id"])
                metadata_path = Path(temp_dir) / f"{doc_id}.metadata.json"
                write_json(metadata_path, {"candidate": row.metadata})
                package_dir = write_jp_evidence_package(
                    row.pdf_path,
                    output_root,
                    metadata_path=metadata_path,
                    parser_result_dir=row.parser_dir,
                    force=force,
                )
            validation = validate_evidence_package(package_dir)
            if not validation.ok:
                report["summary"]["validation_failed"] += 1
            report["summary"]["succeeded"] += 1
            report["items"].append(
                {
                    **item,
                    "status": "succeeded" if validation.ok else "validation_failed",
                    "pdf_path": str(row.pdf_path),
                    "package_path": str(package_dir),
                    "validation_errors": validation.errors,
                }
            )
        except Exception as exc:
            report["summary"]["failed"] += 1
            report["items"].append({**item, "status": "failed", "pdf_path": str(row.pdf_path), "error": str(exc)})
    return report


def _is_jp_result(document_full: dict[str, Any], quality: dict[str, Any], financial_data: dict[str, Any]) -> bool:
    task = document_full.get("task") if isinstance(document_full, dict) else {}
    submit_config = task.get("submit_config") if isinstance(task, dict) else {}
    filename = str(task.get("filename") or quality.get("filename") or financial_data.get("filename") or "") if isinstance(task, dict) else ""
    return (
        (isinstance(submit_config, dict) and submit_config.get("market") == "JP")
        or quality.get("market") == "JP"
        or financial_data.get("market") == "JP"
        or "_JP_" in filename
    )


def _metadata_from_parser_result(parser_dir: Path, document_full: dict[str, Any], quality: dict[str, Any], financial_data: dict[str, Any]) -> dict[str, Any]:
    task = document_full.get("task") if isinstance(document_full, dict) else {}
    filename = str(task.get("filename") or quality.get("filename") or financial_data.get("filename") or parser_dir.name)
    stem_parts = Path(filename).stem.split("_")
    task_id = str(task.get("task_id") or quality.get("task_id") or financial_data.get("task_id") or parser_dir.name)
    ticker = str(stem_parts[2] if len(stem_parts) > 2 else task.get("ticker") or "UNKNOWN")
    period_end = str(stem_parts[3] if len(stem_parts) > 3 else quality.get("period_end") or "") or None
    published_at = str(stem_parts[5] if len(stem_parts) > 5 else quality.get("published_at") or "") or None
    report_type = _report_type(quality.get("report_kind") or financial_data.get("report_kind") or filename)
    pdf_path = _pdf_path(document_full)
    source_url = _source_url(document_full) or (pdf_path.as_uri() if pdf_path and pdf_path.is_absolute() else str(pdf_path) if pdf_path else None)
    return {
        "ticker": ticker,
        "security_code": ticker,
        "doc_id": task_id,
        "company_name": _company_name(stem_parts[0] if stem_parts else filename),
        "source_id": "pdf-parser",
        "form": _form(report_type),
        "report_type": report_type,
        "period_end": period_end,
        "published_at": published_at,
        "filing_date": published_at,
        "fiscal_year": _int_or_none(str(period_end or "")[:4]),
        "source_url": source_url,
    }


def _pdf_path(document_full: dict[str, Any]) -> Path | None:
    source_files = document_full.get("source_files") if isinstance(document_full, dict) else {}
    pdf = source_files.get("pdf") if isinstance(source_files, dict) else {}
    value = pdf.get("path") if isinstance(pdf, dict) else None
    if not value:
        return None
    path = Path(str(value))
    return path if path.is_absolute() else REPO_ROOT / path


def _source_url(document_full: dict[str, Any]) -> str | None:
    task = document_full.get("task") if isinstance(document_full, dict) else {}
    if isinstance(task, dict):
        submit_config = task.get("submit_config") if isinstance(task.get("submit_config"), dict) else {}
        return submit_config.get("source_url") or submit_config.get("document_url") or submit_config.get("landing_url")
    return None


def _company_name(value: Any) -> str:
    text = str(value or "").replace("-", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text or "UNKNOWN"


def _report_type(value: Any) -> str:
    text = str(value or "").lower()
    if "integrated" in text:
        return "integrated_report"
    if "annual_securities" in text or "annual securities" in text or "有価証券報告書" in text:
        return "annual_securities_report"
    if any(token in text for token in ("semi", "half", "半期", "中間")):
        return "semiannual"
    if any(token in text for token in ("quarter", "四半期", "q1", "q2", "q3")):
        return "quarterly"
    return "annual"


def _form(report_type: str) -> str:
    return {
        "integrated_report": "Integrated Report",
        "annual_securities_report": "Annual Securities Report",
        "semiannual": "Semiannual Report",
        "quarterly": "Quarterly Report",
    }.get(report_type, "Annual Report")


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build JP company Wiki evidence packages from PDF parser results.")
    parser.add_argument("--results-root", type=Path, default=DEFAULT_RESULTS_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--task-id", default="")
    parser.add_argument("--ticker", default="")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()

    report = ingest_parser_results(args.results_root.resolve(), args.output_root.resolve(), force=args.force, limit=args.limit, task_id=args.task_id, ticker=args.ticker)
    report_path = args.report if args.report.is_absolute() else REPO_ROOT / args.report
    write_json(report_path, report)
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
