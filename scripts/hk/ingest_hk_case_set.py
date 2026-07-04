#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hk_evidence_lib import REPO_ROOT, write_hk_evidence_package

RULES_SRC = REPO_ROOT / "services" / "market-report-rules" / "src"
import sys

if str(RULES_SRC) not in sys.path:
    sys.path.insert(0, str(RULES_SRC))

from market_report_rules_service.evidence_package import validate_evidence_package


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _find_parser_result(results_root: Path, pdf_path: Path) -> Path | None:
    stem = pdf_path.stem
    for document_full in sorted(results_root.glob("*/document_full.json")):
        payload = _read_json(document_full)
        task = payload.get("task") if isinstance(payload, dict) else {}
        filename = str(task.get("filename") or "") if isinstance(task, dict) else ""
        if stem in filename or filename in pdf_path.name:
            return document_full.parent
    return None


def _resolve_path(value: Any) -> Path | None:
    if not value:
        return None
    path = Path(str(value))
    return path if path.is_absolute() else REPO_ROOT / path


def _case_items_from_manifest(path: Path) -> list[dict[str, Any]]:
    payload = _read_json(path)
    if isinstance(payload, dict):
        items = payload.get("items") or payload.get("cases") or []
    else:
        items = payload
    cases: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict):
            cases.append(item)
        else:
            cases.append({"pdf_path": item})
    return cases


def _metrics_summary(package_dir: Path) -> dict[str, Any]:
    quality = _read_json(package_dir / "qa" / "quality_report.json")
    source_map = _read_json(package_dir / "qa" / "source_map.json")
    normalized = _read_json(package_dir / "metrics" / "normalized_metrics.json")
    metrics = normalized.get("metrics") if isinstance(normalized, dict) else []
    return {
        "quality_status": quality.get("overall_status"),
        "table_count": quality.get("table_count"),
        "statement_table_count": quality.get("statement_table_count"),
        "normalized_metric_count": quality.get("normalized_metric_count") or len(metrics or []),
        "evidence_coverage_ratio": quality.get("evidence_coverage_ratio"),
        "source_map_entries": len(source_map.get("entries") or []) if isinstance(source_map, dict) else 0,
        "critical_warnings": quality.get("critical_warnings") or [],
        "parser_warnings": quality.get("parser_warnings") or [],
        "rule_warnings": quality.get("rule_warnings") or [],
    }


def _markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# HK Ingestion Case Set Report",
        "",
        f"- Generated: `{report.get('generated_at')}`",
        f"- Succeeded: `{report['summary'].get('succeeded', 0)}`",
        f"- Failed: `{report['summary'].get('failed', 0)}`",
        f"- Skipped: `{report['summary'].get('skipped', 0)}`",
        f"- Validated: `{report['summary'].get('validated', 0)}`",
        f"- Validation failed: `{report['summary'].get('validation_failed', 0)}`",
        f"- Imported: `{report['summary'].get('imported', 0)}`",
        "",
        "| Ticker | Status | Quality | Metrics | Evidence | Coverage | Package | Reason |",
        "| --- | --- | --- | ---: | ---: | ---: | --- | --- |",
    ]
    for item in report.get("items") or []:
        metrics = item.get("metrics") or {}
        package_path = item.get("package_path") or ""
        lines.append(
            f"| {item.get('ticker') or ''} | {item.get('status')} | {metrics.get('quality_status') or ''} | "
            f"{metrics.get('normalized_metric_count') or ''} | {metrics.get('source_map_entries') or ''} | "
            f"{metrics.get('evidence_coverage_ratio') if metrics.get('evidence_coverage_ratio') is not None else ''} | "
            f"{package_path} | {item.get('reason') or item.get('error') or ''} |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Build HK evidence packages for a directory or manifest of HK PDFs.")
    parser.add_argument("--downloads-root", type=Path, default=REPO_ROOT / "data" / "market-report-finder" / "downloads" / "HK")
    parser.add_argument("--results-root", type=Path, default=REPO_ROOT / "data" / "pdf-parser" / "results")
    parser.add_argument("--output-root", type=Path, default=REPO_ROOT / "data" / "wiki" / "hk")
    parser.add_argument("--manifest", type=Path, default=None)
    parser.add_argument("--cases", type=Path, default=None, help="Alias for --manifest with taskbook case fields.")
    parser.add_argument("--ticker", default="")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--build-package", action="store_true", help="Accepted for taskbook compatibility; package build is the default action.")
    parser.add_argument("--validate-package", action="store_true")
    parser.add_argument("--import-db", action="store_true")
    parser.add_argument("--report", type=Path, default=REPO_ROOT / "data" / "wiki" / "hk" / "_meta" / "hk_ingest_report.json")
    parser.add_argument("--report-output", type=Path, default=None)
    parser.add_argument("--markdown", type=Path, default=None)
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args()

    manifest_path = args.cases or args.manifest
    if manifest_path:
        cases = _case_items_from_manifest(manifest_path if manifest_path.is_absolute() else REPO_ROOT / manifest_path)
    else:
        cases = [{"pdf_path": str(path)} for path in sorted(args.downloads_root.rglob("*.pdf"))]
    if args.ticker:
        cases = [
            item
            for item in cases
            if f"_HK_{args.ticker.zfill(5)}_" in str(item.get("pdf_path") or "")
            or args.ticker.upper() in str(item.get("pdf_path") or item.get("ticker") or "").upper()
        ]
    if args.limit:
        cases = cases[: args.limit]

    report: dict[str, Any] = {
        "generated_at": _now_iso(),
        "items": [],
        "summary": {"succeeded": 0, "failed": 0, "skipped": 0, "validated": 0, "validation_failed": 0, "imported": 0},
    }
    importer = None
    if args.import_db:
        import importlib.util

        importer_path = REPO_ROOT / "db" / "imports" / "import_hk_evidence_package_to_postgres.py"
        spec = importlib.util.spec_from_file_location("import_hk_evidence_package_to_postgres", importer_path)
        importer = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(importer)

    for case in cases:
        pdf_path = _resolve_path(case.get("pdf_path") or case.get("local_path"))
        if not pdf_path:
            report["summary"]["skipped"] += 1
            report["items"].append({**case, "status": "skipped", "reason": "pdf_path missing"})
            continue
        metadata = _resolve_path(case.get("metadata_json")) or pdf_path.with_suffix(pdf_path.suffix + ".metadata.json")
        parser_result = _resolve_path(case.get("parser_result_dir")) or _find_parser_result(args.results_root, pdf_path)
        item_report = {**case, "pdf_path": str(pdf_path), "metadata_json": str(metadata) if metadata else None}
        if not parser_result:
            report["summary"]["skipped"] += 1
            report["items"].append({**item_report, "status": "skipped", "reason": "parser result not found"})
            continue
        try:
            package_dir = write_hk_evidence_package(pdf_path, parser_result, args.output_root, metadata if metadata.exists() else None, force=args.force)
            metrics = _metrics_summary(package_dir)
            validation_payload = None
            if args.validate_package:
                validation = validate_evidence_package(package_dir)
                validation_payload = validation.as_dict()
                if validation.ok:
                    report["summary"]["validated"] += 1
                else:
                    report["summary"]["validation_failed"] += 1
            imported_parse_run_id = None
            if args.import_db and importer is not None:
                with importer.psycopg.connect(importer.database_url(args.database_url), autocommit=False) as conn:
                    importer.run_ddl(conn)
                    imported_parse_run_id = importer.import_package(conn, package_dir.resolve(), "pdf2md_hk")
                    conn.commit()
                report["summary"]["imported"] += 1
            report["summary"]["succeeded"] += 1
            report["items"].append(
                {
                    **item_report,
                    "status": "succeeded",
                    "parser_result_dir": str(parser_result),
                    "package_path": str(package_dir),
                    "metrics": metrics,
                    "validation": validation_payload,
                    "imported_parse_run_id": imported_parse_run_id,
                }
            )
        except Exception as exc:
            report["summary"]["failed"] += 1
            report["items"].append({**item_report, "status": "failed", "parser_result_dir": str(parser_result), "error": str(exc)})
    report_path = args.report_output or args.report
    _write_json(report_path if report_path.is_absolute() else REPO_ROOT / report_path, report)
    if args.markdown:
        markdown_path = args.markdown if args.markdown.is_absolute() else REPO_ROOT / args.markdown
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(_markdown_report(report), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
