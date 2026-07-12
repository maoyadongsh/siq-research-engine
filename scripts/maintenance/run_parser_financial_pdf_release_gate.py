#!/usr/bin/env python3
"""Validate a real PDF -> MinerU -> Markdown -> financial parser chain."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
PDF_PARSER_ROOT = REPO_ROOT / "apps" / "pdf-parser"
DEFAULT_MANIFEST = REPO_ROOT / "eval_datasets" / "parser_financial_golden" / "v1" / "cases.json"
DEFAULT_OUTPUT = REPO_ROOT / "artifacts" / "eval-runs" / "parser-financial-pdf" / "report.json"
DEFAULT_MARKDOWN = REPO_ROOT / "artifacts" / "eval-runs" / "parser-financial-pdf" / "report.md"
TERMINAL_STATUSES = {"completed", "completed_missing_artifact", "failed", "error", "failure", "cancelled"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if not spec or not spec.loader:
        raise RuntimeError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _golden_module():
    return _load_module(
        REPO_ROOT / "scripts" / "maintenance" / "run_parser_financial_golden_gate.py",
        "parser_financial_golden_gate",
    )


def _mineru_client_module():
    return _load_module(PDF_PARSER_ROOT / "mineru_client.py", "parser_financial_pdf_mineru_client")


def _page_markers_module():
    if str(PDF_PARSER_ROOT) not in sys.path:
        sys.path.insert(0, str(PDF_PARSER_ROOT))
    return _load_module(
        PDF_PARSER_ROOT / "pdf_parser_page_markers.py",
        "parser_financial_pdf_page_markers",
    )


def _safe_relative_path(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    path = Path(text)
    if not text or path.is_absolute() or ".." in path.parts:
        return ""
    return path.as_posix()


def _parser_origin(value: str) -> str:
    parsed = urllib.parse.urlsplit(str(value or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("parser_url must be an absolute HTTP(S) URL")
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{host}{port}"


def validate_pdf_contract(manifest: Any) -> list[str]:
    golden = _golden_module()
    errors = golden.validate_manifest(manifest)
    cases = manifest.get("cases", []) if isinstance(manifest, dict) else []
    for index, case in enumerate(cases, start=1):
        prefix = f"cases[{index}]"
        if not isinstance(case, dict):
            continue
        if not _safe_relative_path(case.get("pdf_source_path")):
            errors.append(f"{prefix}.pdf_source_path must be a safe relative path")
        sha256 = str(case.get("pdf_source_sha256") or "").strip().lower()
        if len(sha256) != 64 or any(char not in "0123456789abcdef" for char in sha256):
            errors.append(f"{prefix}.pdf_source_sha256 must be a lowercase SHA-256")
        if int(case.get("pdf_min_bytes") or 0) <= 0:
            errors.append(f"{prefix}.pdf_min_bytes must be positive")
        if int(case.get("pdf_page_count") or 0) <= 0:
            errors.append(f"{prefix}.pdf_page_count must be positive")
    return errors


def _case_pdf(pdf_root: Path, case: dict[str, Any]) -> Path:
    relative = _safe_relative_path(case.get("pdf_source_path"))
    candidate = (pdf_root / relative).resolve()
    root = pdf_root.resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("pdf_source_path resolves outside PDF root")
    return candidate


def _pdf_page_count(path: Path) -> int:
    completed = subprocess.run(
        ["pdfinfo", str(path)],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"pdfinfo failed: {completed.stderr.strip()}")
    for line in completed.stdout.splitlines():
        if line.startswith("Pages:"):
            return int(line.split(":", 1)[1].strip())
    raise RuntimeError("pdfinfo did not report a page count")


def inspect_pdf(case: dict[str, Any], pdf_root: Path) -> dict[str, Any]:
    golden = _golden_module()
    source = _case_pdf(pdf_root, case)
    result: dict[str, Any] = {
        "case_id": case.get("case_id"),
        "pdf_source_path": _safe_relative_path(case.get("pdf_source_path")),
        "passed": False,
        "errors": [],
    }
    if not source.is_file():
        result["errors"].append("PDF source file missing")
        result["status"] = "missing"
        return result
    size = source.stat().st_size
    sha256 = golden.file_sha256(source)
    result.update(
        {
            "status": "checked",
            "pdf_source_bytes": size,
            "pdf_source_sha256": sha256,
            "pdf_page_count": None,
        }
    )
    if sha256 != str(case.get("pdf_source_sha256") or "").lower():
        result["errors"].append("pdf_source_sha256 mismatch")
    if size < int(case.get("pdf_min_bytes") or 0):
        result["errors"].append(f"PDF bytes below minimum {case.get('pdf_min_bytes')}")
    try:
        page_count = _pdf_page_count(source)
        result["pdf_page_count"] = page_count
        if page_count != int(case.get("pdf_page_count") or 0):
            result["errors"].append(
                f"PDF page count expected {case.get('pdf_page_count')}, got {page_count}"
            )
    except (FileNotFoundError, RuntimeError, subprocess.TimeoutExpired) as exc:
        result["errors"].append(f"PDF page count unavailable: {exc}")
    result["passed"] = not result["errors"]
    return result


def _json_request(url: str, *, method: str = "GET", timeout: float = 10) -> dict[str, Any]:
    request = urllib.request.Request(url, method=method, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        return {"_error": True, "status": exc.code, "detail": exc.read().decode("utf-8", "replace")}
    except Exception as exc:
        return {"_error": True, "detail": str(exc)}


def parser_preflight(parser_url: str, timeout: float) -> dict[str, Any]:
    payload = _json_request(f"{parser_url.rstrip('/')}/api/health", timeout=timeout)
    errors: list[str] = []
    if payload.get("_error"):
        errors.append(f"parser health request failed: {payload.get('detail')}")
    else:
        if not payload.get("mineru"):
            errors.append(f"MinerU is not ready: {payload.get('mineru_detail') or payload.get('warning')}")
        if not payload.get("submit_ready"):
            errors.append(f"parser submit_ready is false: {payload.get('warning')}")
    return {"passed": not errors, "errors": errors, "health": payload}


def _evaluate_fresh_markdown(case: dict[str, Any], markdown: str) -> dict[str, Any]:
    golden = _golden_module()
    with tempfile.TemporaryDirectory(prefix="siq-parser-pdf-golden-") as directory:
        sample_root = Path(directory)
        sample = sample_root / "result.md"
        sample.write_text(markdown, encoding="utf-8")
        fresh_case = dict(case)
        fresh_case["source_path"] = sample.name
        fresh_case["source_sha256"] = golden.file_sha256(sample)
        result = golden.run_offline_case(fresh_case, sample_root)
    result["baseline_markdown_sha256"] = case.get("source_sha256")
    result["fresh_markdown_sha256"] = result.get("source_sha256")
    result["baseline_markdown_hash_match"] = (
        result.get("source_sha256") == str(case.get("source_sha256") or "").lower()
    )
    layout_prefixes = (
        "source_bytes below minimum",
        "source_lines below minimum",
        "metric source line mismatch:",
    )
    layout_errors = [
        str(error) for error in result.get("errors", []) if str(error).startswith(layout_prefixes)
    ]
    structure_expected = case.get("structure") if isinstance(case.get("structure"), dict) else {}
    structure_observed = {
        "html_table_count": markdown.count("<table"),
        "markdown_table_count": len(re.findall(r"^\s*\|.+\|\s*$", markdown, re.MULTILINE)),
        "heading_count": len(re.findall(r"^#{1,6}\s+", markdown, re.MULTILINE)),
        "image_count": len(re.findall(r"!\[[^]]*\]\([^)]*\)", markdown)),
        "details_count": markdown.count("<details"),
    }
    structure_errors = [
        f"structure {key} expected {structure_expected[key]}, got {structure_observed[key]}"
        for key in structure_observed
        if key in structure_expected and structure_observed[key] != structure_expected[key]
    ]
    semantic_errors = [
        str(error) for error in result.get("errors", []) if not str(error).startswith(layout_prefixes)
    ]
    all_errors = [str(error) for error in result.get("errors", [])]
    for observed in result.get("observed_metrics", []):
        identity = f"{observed.get('canonical_name')} {observed.get('period')}"
        if observed.get("source_line") in (None, ""):
            error = f"metric provenance missing source_line: {identity}"
            semantic_errors.append(error)
            all_errors.append(error)
        if observed.get("table_index") in (None, ""):
            error = f"metric provenance missing table_index: {identity}"
            semantic_errors.append(error)
            all_errors.append(error)
    result["fresh_layout_drift"] = {
        "detected": bool(layout_errors),
        "details": layout_errors,
    }
    result["fresh_structure"] = {
        "checked": bool(structure_expected),
        "expected": structure_expected,
        "observed": structure_observed,
        "passed": not structure_errors,
        "errors": structure_errors,
    }
    all_errors.extend(structure_errors)
    result["semantic_errors"] = semantic_errors
    result["errors"] = all_errors
    result["financial_semantics_passed"] = not semantic_errors
    result["passed"] = not layout_errors and not structure_errors and not semantic_errors
    return result


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _decode_json_payload(value: Any) -> Any:
    decoded = value
    for _attempt in range(2):
        if not isinstance(decoded, str):
            break
        try:
            decoded = json.loads(decoded)
        except json.JSONDecodeError:
            break
    return decoded


def _approved_fresh_baseline_validation(
    case: dict[str, Any],
    financial: dict[str, Any],
    evidence: dict[str, Any],
) -> dict[str, Any] | None:
    approved = case.get("approved_fresh_baseline")
    if not isinstance(approved, dict):
        return None
    errors: list[str] = []
    version = str(approved.get("version") or "").strip()
    if not version:
        errors.append("approved fresh baseline version missing")
    comparisons = (
        ("markdown_sha256", evidence.get("markdown_sha256"), approved.get("markdown_sha256")),
        ("source_bytes", financial.get("source_bytes"), approved.get("source_bytes")),
        ("source_lines", financial.get("source_lines"), approved.get("source_lines")),
    )
    for field, observed, expected in comparisons:
        if observed != expected:
            errors.append(f"approved fresh baseline {field} expected {expected}, got {observed}")
    expected_pages = approved.get("raw_page_count")
    for field in ("raw_pdf_page_count", "raw_model_output_page_count", "raw_content_list_page_count"):
        if evidence.get(field) != expected_pages:
            errors.append(f"approved fresh baseline {field} expected {expected_pages}, got {evidence.get(field)}")
    expected_structure = approved.get("structure") if isinstance(approved.get("structure"), dict) else {}
    observed_structure = (financial.get("fresh_structure") or {}).get("observed") or {}
    for field, expected in expected_structure.items():
        if observed_structure.get(field) != expected:
            errors.append(
                f"approved fresh baseline structure {field} expected {expected}, got {observed_structure.get(field)}"
            )
    observed_metrics = {
        (str(item.get("canonical_name") or ""), str(item.get("period") or "")): item
        for item in financial.get("observed_metrics") or []
        if isinstance(item, dict)
    }
    for expected in approved.get("expected_metrics") or []:
        key = (str(expected.get("canonical_name") or ""), str(expected.get("period") or ""))
        observed = observed_metrics.get(key) or {}
        for field in ("value", "source_line", "table_index"):
            if observed.get(field) != expected.get(field):
                errors.append(
                    f"approved fresh baseline metric {key[0]} {key[1]} {field} "
                    f"expected {expected.get(field)}, got {observed.get(field)}"
                )
    if financial.get("financial_checks_overall_status") != approved.get("expected_financial_checks_status"):
        errors.append("approved fresh baseline financial checks mismatch")
    if list(financial.get("quality_flags") or []) != list(approved.get("required_quality_flags") or []):
        errors.append("approved fresh baseline quality flags mismatch")
    if not financial.get("financial_semantics_passed"):
        errors.append("approved fresh baseline financial semantics failed")
    return {
        "version": version,
        "markdown_sha256": approved.get("markdown_sha256"),
        "passed": not errors,
        "errors": errors,
    }


def _recovered_markdown(path: Path, case: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    raw = path.read_bytes()
    payload = json.loads(raw)
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, dict) or not results:
        raise ValueError("recovered MinerU result must contain a non-empty results object")
    expected_key = Path(str(case.get("pdf_source_path") or "")).stem
    if expected_key in results:
        result_key = expected_key
    elif len(results) == 1:
        result_key = next(iter(results))
    else:
        raise ValueError("recovered MinerU result does not uniquely match the PDF")
    item = results.get(result_key)
    markdown = item.get("md_content") if isinstance(item, dict) else None
    if not isinstance(markdown, str) or not markdown.strip():
        raise ValueError("recovered MinerU result contains no Markdown")
    raw_middle_json = item.get("middle_json") if isinstance(item, dict) else None
    raw_model_output = item.get("model_output") if isinstance(item, dict) else None
    raw_content_list = item.get("content_list") if isinstance(item, dict) else None
    middle_json = _decode_json_payload(raw_middle_json)
    model_output = _decode_json_payload(raw_model_output)
    content_list = _decode_json_payload(raw_content_list)
    raw_markdown = markdown
    page_markers = _page_markers_module()
    markdown = page_markers._inject_pdf_page_markers(
        raw_markdown,
        content_list,
        total_pages=int(case.get("pdf_page_count") or 0) or None,
    )
    markdown, restored_pages = page_markers._backfill_sparse_markdown_pages(
        markdown,
        content_list,
    )
    page_indices = [
        int(row["page_idx"])
        for row in content_list or []
        if isinstance(row, dict) and isinstance(row.get("page_idx"), int)
    ]
    return markdown, {
        "result_sha256": _sha256_bytes(raw),
        "result_bytes": len(raw),
        "backend": str(payload.get("backend") or ""),
        "version": str(payload.get("version") or ""),
        "result_key": result_key,
        "artifact_stage": "pdf_api_final_markdown",
        "raw_markdown_sha256": _sha256_bytes(raw_markdown.encode("utf-8")),
        "raw_markdown_bytes": len(raw_markdown.encode("utf-8")),
        "markdown_sha256": _sha256_bytes(markdown.encode("utf-8")),
        "markdown_bytes": len(markdown.encode("utf-8")),
        "restored_sparse_page_count": len(restored_pages),
        "raw_pdf_page_count": len((middle_json or {}).get("pdf_info") or []) if isinstance(middle_json, dict) else None,
        "raw_model_output_page_count": len(model_output) if isinstance(model_output, list) else None,
        "raw_content_list_item_count": len(content_list) if isinstance(content_list, list) else None,
        "raw_content_list_page_count": max(page_indices) + 1 if page_indices else None,
    }


def run_rebaseline_candidate(
    case: dict[str, Any],
    pdf_root: Path,
    result_path: Path,
    *,
    upstream_task_id: str,
    candidate_version: str,
    approved_version: str = "",
    approved_sha256: str = "",
) -> dict[str, Any]:
    result = run_recovered_case(
        case,
        pdf_root,
        result_path,
        upstream_task_id=upstream_task_id,
    )
    evidence = result.get("recovery_evidence") or {}
    financial = result.get("financial_golden") or {}
    structure = result.get("fresh_structure") or {}
    layout = result.get("fresh_layout_drift") or {}
    observed_metrics = financial.get("observed_metrics") or []
    expected_pages = int(case.get("pdf_page_count") or 0)
    pdf_identity_ok = bool(
        result.get("status") == "checked"
        and result.get("pdf_source_sha256") == str(case.get("pdf_source_sha256") or "").lower()
        and result.get("pdf_page_count") == expected_pages
    )
    raw_pages_ok = (
        evidence.get("raw_pdf_page_count") == expected_pages
        and evidence.get("raw_model_output_page_count") == expected_pages
        and evidence.get("raw_content_list_page_count") == expected_pages
    )
    presentation_only = bool(
        result.get("financial_semantics_passed")
        and structure.get("passed")
        and layout.get("detected")
        and bool(layout.get("details"))
        and all(error in layout.get("details", []) for error in result.get("errors", []))
    )
    financial_ok = bool(
        result.get("financial_semantics_passed")
        and financial.get("financial_checks_overall_status") == "pass"
        and len(observed_metrics) == len(case.get("expected_metrics") or [])
    )
    quality_ok = not financial.get("quality_flags")
    provenance_ok = bool(
        observed_metrics
        and all(
            metric.get("source_line") not in (None, "")
            and metric.get("table_index") not in (None, "")
            for metric in observed_metrics
        )
    )
    eligible = bool(
        pdf_identity_ok
        and raw_pages_ok
        and presentation_only
        and financial_ok
        and quality_ok
        and provenance_ok
    )
    candidate_sha256 = str(evidence.get("markdown_sha256") or "")
    approval_matches = bool(
        approved_version.strip()
        and approved_sha256.strip().lower() == candidate_sha256
        and approved_version.strip() == candidate_version.strip()
    )
    result["baseline_candidate"] = {
        "version": candidate_version.strip(),
        "markdown_sha256": candidate_sha256,
        "eligible": eligible,
        "pdf_identity_contract_passed": pdf_identity_ok,
        "presentation_only": presentation_only,
        "raw_page_contract_passed": raw_pages_ok,
        "structure_contract_passed": bool(structure.get("passed")),
        "financial_contract_passed": financial_ok,
        "quality_contract_passed": quality_ok,
        "provenance_contract_passed": provenance_ok,
        "approval": {
            "required": True,
            "approved": approval_matches and eligible,
            "approved_version": approved_version.strip(),
            "approved_sha256": approved_sha256.strip().lower(),
        },
        "manifest_mutated": False,
    }
    result["presentation_findings"] = list(layout.get("details") or [])
    result["passed"] = bool(eligible and approval_matches)
    result["errors"] = []
    if not eligible:
        result["errors"].append("baseline candidate is not eligible for approval")
    elif not approval_matches:
        result["errors"].append("explicit baseline version and SHA-256 approval is required")
    return result


def run_recovered_case(
    case: dict[str, Any],
    pdf_root: Path,
    result_path: Path,
    *,
    upstream_task_id: str,
) -> dict[str, Any]:
    inspection = inspect_pdf(case, pdf_root)
    inspection["live_status"] = "recovered_result"
    if not inspection["passed"]:
        return inspection
    if not upstream_task_id.strip():
        inspection["errors"].append("upstream_task_id is required for recovered result evidence")
        inspection["passed"] = False
        return inspection
    try:
        markdown, evidence = _recovered_markdown(result_path, case)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        inspection["errors"].append(f"recovered result invalid: {exc}")
        inspection["passed"] = False
        return inspection
    evidence["upstream_task_id"] = upstream_task_id.strip()
    inspection["recovery_evidence"] = evidence
    financial = _evaluate_fresh_markdown(case, markdown)
    inspection["financial_golden"] = financial
    inspection["financial_semantics_passed"] = bool(financial.get("financial_semantics_passed"))
    inspection["fresh_layout_drift"] = financial.get("fresh_layout_drift")
    inspection["fresh_structure"] = financial.get("fresh_structure")
    inspection["errors"].extend(financial.get("errors", []))
    approved = _approved_fresh_baseline_validation(case, financial, evidence)
    inspection["approved_fresh_baseline"] = approved
    if approved and approved.get("passed"):
        layout_errors = set((financial.get("fresh_layout_drift") or {}).get("details") or [])
        inspection["errors"] = [error for error in inspection["errors"] if error not in layout_errors]
        inspection["fresh_layout_drift"]["resolved_by_approved_fresh_baseline"] = True
    if not financial.get("passed") and not financial.get("errors"):
        inspection["errors"].append("fresh financial validation failed without diagnostic")
    inspection["passed"] = bool(financial.get("passed") or (approved and approved.get("passed"))) and not inspection[
        "errors"
    ]
    return inspection


def _checkpoint_view(item: dict[str, Any]) -> dict[str, Any]:
    status = item.get("task_status") if isinstance(item.get("task_status"), dict) else {}
    return {
        "schema_version": "siq_parser_financial_pdf_live_checkpoint_v1",
        "generated_at": _now_iso(),
        "case_id": item.get("case_id"),
        "task_id": item.get("task_id"),
        "live_status": item.get("live_status"),
        "upload_acknowledged": bool(item.get("upload") and not item.get("upload", {}).get("_error")),
        "upstream_task_id": status.get("mineru_task_id") or status.get("upstream_task_id"),
        "task_status": status.get("status"),
        "evidence_captured": bool(item.get("financial_golden")),
        "cleanup": item.get("cleanup"),
        "errors": list(item.get("errors") or []),
    }


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def run_live_case(
    case: dict[str, Any],
    pdf_root: Path,
    parser_url: str,
    *,
    deadline_seconds: float,
    poll_interval: float,
    request_timeout: float,
    checkpoint: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    inspection = inspect_pdf(case, pdf_root)
    inspection["live_status"] = "not_started"
    if not inspection["passed"]:
        return inspection
    source = _case_pdf(pdf_root, case)
    task_id = f"pdf-golden-{uuid.uuid4().hex[:12]}"
    inspection["task_id"] = task_id
    if checkpoint:
        checkpoint(inspection)
    client = _mineru_client_module()
    upload = client.stream_multipart_post(
        f"{parser_url.rstrip('/')}/api/upload",
        fields={"task_id": task_id, "market": case.get("market") or "CN"},
        file_field_name="files",
        filename=source.name,
        file_path=str(source),
        content_type="application/pdf",
        timeout=max(request_timeout, 60),
    )
    inspection["upload"] = upload
    if checkpoint:
        checkpoint(inspection)
    if upload.get("_error") or upload.get("task_id") != task_id:
        inspection["errors"].append(f"parser upload failed: {upload.get('detail') or upload}")
        inspection["passed"] = False
        inspection["live_status"] = "upload_failed"
        inspection["cleanup"] = {
            "attempted": False,
            "reason": "preserved_unconfirmed_upload_for_recovery",
        }
        if checkpoint:
            checkpoint(inspection)
        return inspection

    deadline = time.monotonic() + deadline_seconds
    last_status: dict[str, Any] = {}
    evidence_captured = False
    try:
        while time.monotonic() < deadline:
            status = _json_request(
                f"{parser_url.rstrip('/')}/api/status/{task_id}",
                timeout=request_timeout,
            )
            if status.get("_error"):
                last_status = status
            else:
                last_status = status
                state = str(status.get("status") or "").lower()
                inspection["task_status"] = last_status
                inspection["live_status"] = state or "unknown"
                if checkpoint:
                    checkpoint(inspection)
                if state in TERMINAL_STATUSES:
                    break
            time.sleep(poll_interval)
        else:
            inspection["errors"].append(f"parser task timed out after {deadline_seconds:.0f}s")
        inspection["task_status"] = last_status
        state = str(last_status.get("status") or "").lower()
        inspection["live_status"] = state or "unknown"
        if state != "completed":
            if not inspection["errors"]:
                inspection["errors"].append(
                    f"parser task did not complete: {state or last_status.get('detail') or 'unknown'}"
                )
            inspection["passed"] = False
            return inspection
        result_payload = _json_request(
            f"{parser_url.rstrip('/')}/api/result/{task_id}",
            timeout=max(request_timeout, 60),
        )
        markdown = result_payload.get("markdown") if isinstance(result_payload, dict) else None
        if not isinstance(markdown, str) or not markdown.strip():
            inspection["errors"].append("completed parser task returned no Markdown")
            inspection["passed"] = False
            return inspection
        live_evidence = {
            "markdown_sha256": _sha256_bytes(markdown.encode("utf-8")),
        }
        if isinstance(case.get("approved_fresh_baseline"), dict):
            middle_json = _json_request(
                f"{parser_url.rstrip('/')}/api/artifact/{task_id}/middle.json",
                timeout=max(request_timeout, 60),
            )
            model_output = _json_request(
                f"{parser_url.rstrip('/')}/api/artifact/{task_id}/model_output.json",
                timeout=max(request_timeout, 60),
            )
            content_list = _json_request(
                f"{parser_url.rstrip('/')}/api/artifact/{task_id}/content_list.json",
                timeout=max(request_timeout, 60),
            )
            middle_json = _decode_json_payload(middle_json)
            model_output = _decode_json_payload(model_output)
            content_list = _decode_json_payload(content_list)
            page_indices = [
                int(row["page_idx"])
                for row in content_list or []
                if isinstance(row, dict) and isinstance(row.get("page_idx"), int)
            ]
            live_evidence.update(
                {
                    "raw_pdf_page_count": len(middle_json.get("pdf_info") or [])
                    if isinstance(middle_json, dict)
                    else None,
                    "raw_model_output_page_count": len(model_output) if isinstance(model_output, list) else None,
                    "raw_content_list_page_count": max(page_indices) + 1 if page_indices else None,
                }
            )
            inspection["live_raw_evidence"] = live_evidence
        financial = _evaluate_fresh_markdown(case, markdown)
        inspection["financial_golden"] = financial
        inspection["financial_semantics_passed"] = bool(financial.get("financial_semantics_passed"))
        inspection["fresh_layout_drift"] = financial.get("fresh_layout_drift")
        inspection["fresh_structure"] = financial.get("fresh_structure")
        inspection["errors"].extend(financial.get("errors", []))
        approved = _approved_fresh_baseline_validation(case, financial, live_evidence)
        inspection["approved_fresh_baseline"] = approved
        if approved and approved.get("passed"):
            layout_errors = set((financial.get("fresh_layout_drift") or {}).get("details") or [])
            inspection["errors"] = [error for error in inspection["errors"] if error not in layout_errors]
            inspection["fresh_layout_drift"]["resolved_by_approved_fresh_baseline"] = True
        if not financial.get("passed") and not financial.get("errors"):
            inspection["errors"].append("fresh financial validation failed without diagnostic")
        inspection["passed"] = bool(financial.get("passed") or (approved and approved.get("passed"))) and not inspection[
            "errors"
        ]
        evidence_captured = True
        if checkpoint:
            checkpoint(inspection)
        return inspection
    finally:
        if evidence_captured:
            cleanup = _json_request(
                f"{parser_url.rstrip('/')}/api/tasks/{task_id}",
                method="DELETE",
                timeout=request_timeout,
            )
            inspection["cleanup"] = cleanup
        else:
            inspection["cleanup"] = {
                "attempted": False,
                "reason": "preserved_task_without_result_evidence_for_recovery",
            }
        if checkpoint:
            checkpoint(inspection)


def run_gate(
    *,
    mode: str,
    manifest_path: Path = DEFAULT_MANIFEST,
    pdf_root: Path | None = None,
    parser_url: str = "http://127.0.0.1:15000",
    deadline_seconds: float = 10800,
    poll_interval: float = 10,
    request_timeout: float = 20,
    recovered_result_path: Path | None = None,
    upstream_task_id: str = "",
    checkpoint_path: Path | None = None,
    candidate_version: str = "",
    approved_baseline_version: str = "",
    approved_baseline_sha256: str = "",
) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    validation_errors = validate_pdf_contract(manifest)
    try:
        parser_origin = _parser_origin(parser_url)
    except ValueError as exc:
        parser_origin = ""
        validation_errors.append(str(exc))
    cases = manifest.get("cases", []) if isinstance(manifest, dict) else []
    results: list[dict[str, Any]] = []
    preflight: dict[str, Any] | None = None
    if mode != "contract" and not validation_errors:
        if pdf_root is None:
            validation_errors.append("pdf_root is required")
        else:
            results = [inspect_pdf(case, pdf_root) for case in cases]
            if mode in {"recovered-result", "rebaseline-candidate"}:
                if recovered_result_path is None:
                    validation_errors.append("recovered_result_path is required")
                elif mode == "rebaseline-candidate" and not candidate_version.strip():
                    validation_errors.append("candidate_version is required")
                else:
                    if mode == "rebaseline-candidate":
                        results = [
                            run_rebaseline_candidate(
                                case,
                                pdf_root,
                                recovered_result_path,
                                upstream_task_id=upstream_task_id,
                                candidate_version=candidate_version,
                                approved_version=approved_baseline_version,
                                approved_sha256=approved_baseline_sha256,
                            )
                            for case in cases
                        ]
                    else:
                        results = [
                            run_recovered_case(
                                case,
                                pdf_root,
                                recovered_result_path,
                                upstream_task_id=upstream_task_id,
                            )
                            for case in cases
                        ]
            else:
                preflight = parser_preflight(parser_origin, request_timeout)
            if mode == "live-http" and all(item["passed"] for item in results) and preflight and preflight["passed"]:
                def checkpoint(item: dict[str, Any]) -> None:
                    if checkpoint_path:
                        _write_json_atomic(checkpoint_path, _checkpoint_view(item))

                results = [
                    run_live_case(
                        case,
                        pdf_root,
                        parser_origin,
                        deadline_seconds=deadline_seconds,
                        poll_interval=poll_interval,
                        request_timeout=request_timeout,
                        checkpoint=checkpoint,
                    )
                    for case in cases
                ]
    passed = not validation_errors
    if mode in {"recovered-result", "rebaseline-candidate"}:
        passed = passed and all(item["passed"] for item in results)
    elif mode != "contract":
        passed = passed and bool(preflight and preflight["passed"]) and all(item["passed"] for item in results)
    return {
        "schema_version": "siq_parser_financial_pdf_release_report_v1",
        "generated_at": _now_iso(),
        "mode": mode,
        "passed": passed,
        "parser_url": parser_origin,
        "validation_errors": validation_errors,
        "preflight": preflight,
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
        "# Parser Financial PDF Release Gate",
        "",
        f"- Mode: `{report['mode']}`",
        f"- Status: `{'PASS' if report['passed'] else 'BLOCKED'}`",
        f"- Parser: `{report['parser_url']}`",
    ]
    for error in report.get("validation_errors", []):
        lines.append(f"- Validation error: {error}")
    for error in (report.get("preflight") or {}).get("errors", []):
        lines.append(f"- Preflight error: {error}")
    for item in report.get("results", []):
        lines.extend(
            [
                "",
                f"## {item.get('case_id')}",
                "",
                f"- PDF: `{item.get('pdf_source_path')}`",
                f"- PDF SHA-256: `{item.get('pdf_source_sha256', '')}`",
                f"- Pages: `{item.get('pdf_page_count', '')}`",
                f"- Case result: `{'PASS' if item.get('passed') else 'BLOCKED'}`",
            ]
        )
        recovery = item.get("recovery_evidence") or {}
        if recovery:
            lines.extend(
                [
                    f"- Upstream task: `{recovery.get('upstream_task_id', '')}`",
                    f"- Recovered result SHA-256: `{recovery.get('result_sha256', '')}`",
                    f"- Fresh Markdown SHA-256: `{recovery.get('markdown_sha256', '')}`",
                    f"- Financial semantics: `{'PASS' if item.get('financial_semantics_passed') else 'BLOCKED'}`",
                ]
            )
        layout = item.get("fresh_layout_drift") or {}
        if layout.get("detected"):
            lines.append(f"- Fresh layout drift: `BLOCKED ({len(layout.get('details') or [])} finding(s))`")
        structure = item.get("fresh_structure") or {}
        if structure.get("checked"):
            lines.append(f"- Fresh structure contract: `{'PASS' if structure.get('passed') else 'BLOCKED'}`")
        candidate = item.get("baseline_candidate") or {}
        if candidate:
            approval = candidate.get("approval") or {}
            lines.extend(
                [
                    f"- Candidate baseline version: `{candidate.get('version', '')}`",
                    f"- Candidate eligible: `{candidate.get('eligible')}`",
                    f"- Presentation-only drift: `{candidate.get('presentation_only')}`",
                    f"- Raw 408-page contract: `{candidate.get('raw_page_contract_passed')}`",
                    f"- Candidate approved: `{approval.get('approved')}`",
                    f"- Manifest mutated: `{candidate.get('manifest_mutated')}`",
                ]
            )
        approved = item.get("approved_fresh_baseline") or {}
        if approved:
            lines.append(
                f"- Approved fresh baseline `{approved.get('version', '')}`: "
                f"`{'PASS' if approved.get('passed') else 'BLOCKED'}`"
            )
        lines.extend(f"- Error: {error}" for error in item.get("errors", []))
    return "\n".join(lines).rstrip() + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("contract", "preflight", "live-http", "recovered-result", "rebaseline-candidate"),
        default="contract",
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--pdf-root", type=Path)
    parser.add_argument("--parser-url", default=os.getenv("SIQ_PDF_PARSER_URL", "http://127.0.0.1:15000"))
    parser.add_argument("--deadline-seconds", type=float, default=10800)
    parser.add_argument("--poll-interval", type=float, default=10)
    parser.add_argument("--request-timeout", type=float, default=20)
    parser.add_argument("--recovered-result", type=Path)
    parser.add_argument("--upstream-task-id", default="")
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--candidate-version", default="")
    parser.add_argument("--approved-baseline-version", default="")
    parser.add_argument("--approved-baseline-sha256", default="")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    pdf_root = args.pdf_root
    if pdf_root is None and os.getenv("SIQ_FINANCIAL_GOLDEN_PDF_ROOT"):
        pdf_root = Path(os.environ["SIQ_FINANCIAL_GOLDEN_PDF_ROOT"])
    checkpoint_path = args.checkpoint
    if checkpoint_path is None and args.mode == "live-http":
        checkpoint_path = args.output.with_name(f"{args.output.stem}.checkpoint.json")
    report = run_gate(
        mode=args.mode,
        manifest_path=args.manifest,
        pdf_root=pdf_root,
        parser_url=args.parser_url,
        deadline_seconds=args.deadline_seconds,
        poll_interval=args.poll_interval,
        request_timeout=args.request_timeout,
        recovered_result_path=args.recovered_result,
        upstream_task_id=args.upstream_task_id,
        checkpoint_path=checkpoint_path,
        candidate_version=args.candidate_version,
        approved_baseline_version=args.approved_baseline_version,
        approved_baseline_sha256=args.approved_baseline_sha256,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.markdown.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, sort_keys=True))
    print(f"Parser financial PDF release gate: {'PASS' if report['passed'] else 'BLOCKED'}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
