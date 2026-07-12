#!/usr/bin/env python3
"""Validate parser financial extraction against versioned golden cases."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
PDF_PARSER_ROOT = REPO_ROOT / "apps" / "pdf-parser"
DEFAULT_MANIFEST = REPO_ROOT / "eval_datasets" / "parser_financial_golden" / "v1" / "cases.json"
DEFAULT_OUTPUT = REPO_ROOT / "artifacts" / "eval-runs" / "parser-financial-golden" / "report.json"
DEFAULT_MARKDOWN = REPO_ROOT / "artifacts" / "eval-runs" / "parser-financial-golden" / "report.md"
MANIFEST_SCHEMA = "siq_parser_financial_golden_manifest_v1"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative_path(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    path = Path(text)
    if not text or path.is_absolute() or ".." in path.parts:
        return ""
    return path.as_posix()


def validate_manifest(payload: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["manifest must be an object"]
    if payload.get("schema_version") != MANIFEST_SCHEMA:
        errors.append(f"schema_version must be {MANIFEST_SCHEMA}")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        return [*errors, "cases must be a non-empty array"]
    seen: set[str] = set()
    for index, case in enumerate(cases, start=1):
        prefix = f"cases[{index}]"
        if not isinstance(case, dict):
            errors.append(f"{prefix} must be an object")
            continue
        case_id = str(case.get("case_id") or "").strip()
        if not case_id:
            errors.append(f"{prefix}.case_id missing")
        elif case_id in seen:
            errors.append(f"{prefix}.case_id duplicated: {case_id}")
        seen.add(case_id)
        if not _safe_relative_path(case.get("source_path")):
            errors.append(f"{prefix}.source_path must be a safe relative path")
        source_sha256 = str(case.get("source_sha256") or "").strip().lower()
        if len(source_sha256) != 64 or any(char not in "0123456789abcdef" for char in source_sha256):
            errors.append(f"{prefix}.source_sha256 must be a lowercase SHA-256")
        expected_metrics = case.get("expected_metrics")
        if not isinstance(expected_metrics, list) or not expected_metrics:
            errors.append(f"{prefix}.expected_metrics must be a non-empty array")
            continue
        for metric_index, expected in enumerate(expected_metrics, start=1):
            metric_prefix = f"{prefix}.expected_metrics[{metric_index}]"
            if not isinstance(expected, dict):
                errors.append(f"{metric_prefix} must be an object")
                continue
            for field in ("canonical_name", "period", "value"):
                if expected.get(field) in (None, ""):
                    errors.append(f"{metric_prefix}.{field} missing")
    return errors


def _decimal_equal(left: Any, right: Any) -> bool:
    try:
        return Decimal(str(left)) == Decimal(str(right))
    except (InvalidOperation, TypeError, ValueError):
        return False


def _load_financial_extractor():
    if str(PDF_PARSER_ROOT) not in sys.path:
        sys.path.insert(0, str(PDF_PARSER_ROOT))
    from financial_extractor import build_financial_checks, build_financial_data

    return build_financial_data, build_financial_checks


def _case_source(sample_root: Path, case: dict[str, Any]) -> Path:
    relative = _safe_relative_path(case.get("source_path"))
    candidate = (sample_root / relative).resolve()
    root = sample_root.resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("source_path resolves outside sample root")
    return candidate


def run_offline_case(case: dict[str, Any], sample_root: Path) -> dict[str, Any]:
    errors: list[str] = []
    source = _case_source(sample_root, case)
    result: dict[str, Any] = {
        "case_id": case.get("case_id"),
        "source_path": _safe_relative_path(case.get("source_path")),
        "passed": False,
        "errors": errors,
        "observed_metrics": [],
    }
    if not source.is_file():
        errors.append("source file missing")
        result["status"] = "missing"
        return result

    source_bytes = source.stat().st_size
    source_sha256 = file_sha256(source)
    markdown = source.read_text(encoding="utf-8")
    line_count = len(markdown.splitlines())
    result.update(
        {
            "status": "checked",
            "source_bytes": source_bytes,
            "source_lines": line_count,
            "source_sha256": source_sha256,
        }
    )
    if source_sha256 != str(case.get("source_sha256") or "").lower():
        errors.append("source_sha256 mismatch")
    if source_bytes < int(case.get("min_bytes") or 0):
        errors.append(f"source_bytes below minimum {case.get('min_bytes')}")
    if line_count < int(case.get("min_lines") or 0):
        errors.append(f"source_lines below minimum {case.get('min_lines')}")

    build_financial_data, build_financial_checks = _load_financial_extractor()
    financial_data = build_financial_data(
        markdown,
        task_id=str(case.get("task_id") or case.get("case_id") or "golden-case"),
        filename=str(case.get("filename") or Path(result["source_path"]).name),
    )
    checks = build_financial_checks(financial_data)
    metrics = {
        str(item.get("canonical_name") or ""): item
        for item in financial_data.get("key_metrics", [])
        if isinstance(item, dict) and item.get("canonical_name")
    }
    for expected in case.get("expected_metrics", []):
        canonical_name = str(expected.get("canonical_name") or "")
        period = str(expected.get("period") or "")
        metric = metrics.get(canonical_name) or {}
        values = metric.get("values") if isinstance(metric.get("values"), dict) else {}
        sources = metric.get("sources") if isinstance(metric.get("sources"), dict) else {}
        observed_value = values.get(period)
        source_payload = sources.get(period) if isinstance(sources.get(period), dict) else {}
        observed = {
            "canonical_name": canonical_name,
            "period": period,
            "value": observed_value,
            "source_line": source_payload.get("line"),
            "table_index": source_payload.get("table_index"),
        }
        result["observed_metrics"].append(observed)
        if not metric:
            errors.append(f"metric missing: {canonical_name}")
            continue
        if not _decimal_equal(observed_value, expected.get("value")):
            errors.append(
                f"metric value mismatch: {canonical_name} {period} expected {expected.get('value')}, got {observed_value}"
            )
        expected_line = expected.get("source_line")
        if expected_line not in (None, "") and source_payload.get("line") != expected_line:
            errors.append(
                f"metric source line mismatch: {canonical_name} {period} expected {expected_line}, got {source_payload.get('line')}"
            )

    quality_flags = [item for item in financial_data.get("quality_flags", []) if isinstance(item, dict)]
    result["quality_flags"] = quality_flags
    forbidden_codes = {str(code) for code in case.get("forbidden_quality_flag_codes", [])}
    observed_forbidden = sorted({str(item.get("code")) for item in quality_flags if str(item.get("code")) in forbidden_codes})
    if observed_forbidden:
        errors.append(f"forbidden quality flags present: {observed_forbidden}")

    result["financial_checks_overall_status"] = checks.get("overall_status")
    expected_status = str(case.get("expected_financial_checks_status") or "").strip()
    if expected_status and checks.get("overall_status") != expected_status:
        errors.append(
            f"financial checks status expected {expected_status}, got {checks.get('overall_status')}"
        )
    result["passed"] = not errors
    return result


def run_gate(
    *,
    mode: str,
    manifest_path: Path = DEFAULT_MANIFEST,
    sample_root: Path | None = None,
) -> dict[str, Any]:
    manifest = read_json(manifest_path)
    validation_errors = validate_manifest(manifest)
    cases = manifest.get("cases", []) if isinstance(manifest, dict) else []
    results: list[dict[str, Any]] = []
    if mode == "offline-samples" and not validation_errors:
        if sample_root is None:
            validation_errors.append("sample_root is required for offline-samples mode")
        else:
            results = [run_offline_case(case, sample_root) for case in cases]
    passed = not validation_errors and (mode == "contract" or all(item.get("passed") for item in results))
    return {
        "schema_version": "siq_parser_financial_golden_report_v1",
        "generated_at": now_iso(),
        "mode": mode,
        "passed": passed,
        "manifest_schema_version": manifest.get("schema_version") if isinstance(manifest, dict) else None,
        "validation_errors": validation_errors,
        "summary": {
            "case_count": len(cases),
            "passed": sum(1 for item in results if item.get("passed")),
            "failed": sum(1 for item in results if not item.get("passed")),
            "missing": sum(1 for item in results if item.get("status") == "missing"),
        },
        "results": results,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Parser Financial Golden Gate",
        "",
        f"- Mode: `{report.get('mode')}`",
        f"- Status: `{'PASS' if report.get('passed') else 'FAIL'}`",
        f"- Cases: `{report.get('summary', {}).get('case_count', 0)}`",
    ]
    if report.get("validation_errors"):
        lines.extend(["", "## Validation Errors", ""])
        lines.extend(f"- {error}" for error in report["validation_errors"])
    if report.get("results"):
        lines.extend(["", "## Cases", ""])
        for item in report["results"]:
            lines.append(f"### {item.get('case_id')} - {'PASS' if item.get('passed') else 'FAIL'}")
            lines.append("")
            lines.append(f"- Source: `{item.get('source_path')}`")
            lines.append(f"- SHA-256: `{item.get('source_sha256', '')}`")
            lines.extend(f"- Error: {error}" for error in item.get("errors", []))
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("contract", "offline-samples"), default="contract")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--sample-root", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    sample_root = args.sample_root
    if sample_root is None and os.getenv("SIQ_FINANCIAL_GOLDEN_SAMPLE_ROOT"):
        sample_root = Path(os.environ["SIQ_FINANCIAL_GOLDEN_SAMPLE_ROOT"])
    report = run_gate(mode=args.mode, manifest_path=args.manifest, sample_root=sample_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.markdown.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, sort_keys=True))
    print(f"Parser financial golden gate: {'PASS' if report['passed'] else 'FAIL'}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
