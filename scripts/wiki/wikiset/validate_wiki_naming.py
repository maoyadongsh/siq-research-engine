#!/usr/bin/env python3
"""Validate wiki company/report naming contract."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from company_identity import (  # noqa: E402
    canonical_company_id,
    looks_like_report_instance_name,
    parse_download_filename_identity,
)

REPORT_ID_RE = re.compile(r"^\d{4}-[a-z0-9][a-z0-9-]*$")
REPORT_INSTANCE_FRAGMENT_RE = re.compile(
    r"[\u4e00-\u9fa5A-Za-z0-9.-]+_"
    r"(?:CN|HK|US)_[^_\s\"/]+_"
    r"\d{4}-\d{2}-\d{2}_[^_\s\"/]+_"
    r"\d{4}-\d{2}-\d{2}_[^_\s\"/]+_"
    r"[0-9a-fA-F]{8}"
)
SKIP_JSON_FILES = {"document_full.json"}
SKIP_PATH_PARTS = {"raw"}
PROVENANCE_KEYS = {
    "filename",
    "source_filename",
    "source_filename_metadata",
    "filename_pattern",
    "result_file",
    "raw_request_sha256",
    "raw_response_sha256",
    "response_content_sha256",
}
IDENTITY_OR_SEMANTIC_NAME_KEYS = {
    "company_id",
    "company_dir",
    "company_short_name",
    "stock_name",
    "company_full_name",
    "name",
    "value",
    "subject",
    "source_entity_name",
    "target_entity_name",
}

A_SHARE_STOCK_CODE_RE = re.compile(
    r"^(?:000|001|002|003|300|301|600|601|603|605|688|689|8\d{5}|4\d{5})$"
)
NON_A_SHARE_DIR_RE = re.compile(r"^(?:HK|KR|JP|US|EU)[A-Za-z0-9]", re.IGNORECASE)
NON_A_SHARE_MARKETS = {"HK", "KR", "JP", "US", "EU"}


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception:
        return default


def issue(issues: list[dict[str, Any]], path: Path, code: str, detail: str) -> None:
    issues.append({"path": str(path), "code": code, "detail": detail})


def contains_report_instance_name(value: Any) -> bool:
    text = str(value or "")
    return bool(looks_like_report_instance_name(text) or REPORT_INSTANCE_FRAGMENT_RE.search(text))


def validate_structured_names(path: Path, payload: Any, issues: list[dict[str, Any]], key: str | None = None) -> None:
    if isinstance(payload, dict):
        for item_key, item_value in payload.items():
            validate_structured_names(path, item_value, issues, str(item_key))
        return
    if isinstance(payload, list):
        for item in payload:
            validate_structured_names(path, item, issues, key)
        return
    if not isinstance(payload, str) or key in PROVENANCE_KEYS:
        return
    if key in IDENTITY_OR_SEMANTIC_NAME_KEYS and contains_report_instance_name(payload):
        issue(issues, path, "structured_name_contains_report_instance", f"{key}: {payload[:240]}")


def validate_company_structured_json(company_dir: Path, issues: list[dict[str, Any]]) -> None:
    for path in sorted(company_dir.rglob("*.json")):
        rel_parts = set(path.relative_to(company_dir).parts)
        if path.name in SKIP_JSON_FILES or rel_parts.intersection(SKIP_PATH_PARTS):
            continue
        payload = read_json(path, None)
        if payload is None:
            continue
        validate_structured_names(path, payload, issues)


def is_a_share_company_dir(company_dir: Path, company: dict[str, Any]) -> bool:
    """Limit this legacy naming gate to A-share company wiki entries."""
    if NON_A_SHARE_DIR_RE.match(company_dir.name):
        return False
    market = str(
        company.get("market")
        or company.get("source_market")
        or company.get("listing_market")
        or ""
    ).strip().upper()
    if market in NON_A_SHARE_MARKETS:
        return False
    if company.get("identity_route") == "generic_non_a_share_wiki_import":
        return False
    stock_code = str(company.get("stock_code") or "").strip()
    if stock_code:
        return bool(A_SHARE_STOCK_CODE_RE.match(stock_code))
    dir_code = company_dir.name.split("-", 1)[0]
    return bool(A_SHARE_STOCK_CODE_RE.match(dir_code))


def validate_company_dir(company_dir: Path, issues: list[dict[str, Any]]) -> None:
    company_json_path = company_dir / "company.json"
    company = read_json(company_json_path, {})
    if not isinstance(company, dict) or not company:
        issue(issues, company_json_path, "missing_company_json", "company.json is missing or invalid")
        return

    stock_code = str(company.get("stock_code") or "").strip()
    short_name = str(company.get("company_short_name") or "").strip()
    company_id = str(company.get("company_id") or "").strip()
    expected_id = canonical_company_id(stock_code, short_name)

    if company_dir.name != expected_id:
        issue(issues, company_dir, "company_dir_mismatch", f"expected {expected_id}")
    if company_id != expected_id:
        issue(issues, company_json_path, "company_id_mismatch", f"expected {expected_id}, got {company_id}")
    if looks_like_report_instance_name(company_id):
        issue(issues, company_json_path, "company_id_contains_report_instance", company_id)
    if looks_like_report_instance_name(short_name):
        issue(issues, company_json_path, "company_short_name_contains_report_instance", short_name)

    reports = company.get("reports") if isinstance(company.get("reports"), list) else []
    for report in reports:
        if not isinstance(report, dict):
            continue
        report_id = str(report.get("report_id") or "").strip()
        report_dir = company_dir / "reports" / report_id
        if report_id and not REPORT_ID_RE.match(report_id):
            issue(issues, report_dir, "report_id_mismatch", f"unexpected report_id {report_id}")
        if report_id and not report_dir.exists():
            issue(issues, report_dir, "missing_report_dir", "report directory not found")
        source_filename = report.get("source_filename")
        parsed = parse_download_filename_identity(source_filename)
        if parsed:
            parsed_code = parsed.get("stock_code")
            parsed_short = parsed.get("company_short_name")
            if parsed_code and parsed_code != stock_code:
                issue(issues, company_json_path, "source_filename_stock_mismatch", f"{source_filename}: {parsed_code} != {stock_code}")
            if parsed_short and parsed_short != short_name:
                issue(issues, company_json_path, "source_filename_short_name_mismatch", f"{source_filename}: {parsed_short} != {short_name}")


def validate_catalog(wiki_root: Path, issues: list[dict[str, Any]]) -> None:
    catalog_path = wiki_root / "_meta" / "company_catalog.json"
    catalog = read_json(catalog_path, {})
    for company in catalog.get("companies") or []:
        if not isinstance(company, dict):
            continue
        company_path = str(company.get("company_path") or "")
        company_dir = wiki_root / company_path if company_path else wiki_root / "companies" / str(company.get("company_id") or "")
        if not is_a_share_company_dir(company_dir, company):
            continue
        stock_code = str(company.get("stock_code") or "").strip()
        short_name = str(company.get("company_short_name") or "").strip()
        expected_id = canonical_company_id(stock_code, short_name)
        expected_path = f"companies/{expected_id}"
        if company.get("company_id") != expected_id:
            issue(issues, catalog_path, "catalog_company_id_mismatch", f"{company.get('company_id')} should be {expected_id}")
        if company.get("company_path") != expected_path:
            issue(issues, catalog_path, "catalog_company_path_mismatch", f"{company.get('company_path')} should be {expected_path}")
        if not (wiki_root / expected_path).exists():
            issue(issues, catalog_path, "catalog_company_path_missing", expected_path)

    report_catalog_path = wiki_root / "_meta" / "report_catalog.json"
    report_catalog = read_json(report_catalog_path, {})
    for report in report_catalog.get("reports") or []:
        if not isinstance(report, dict):
            continue
        company_path = str(report.get("company_path") or "")
        company_dir = wiki_root / company_path if company_path else wiki_root / "companies" / str(report.get("company_id") or "")
        if not is_a_share_company_dir(company_dir, report):
            continue
        stock_code = str(report.get("stock_code") or "").strip()
        short_name = str(report.get("company_short_name") or "").strip()
        expected_id = canonical_company_id(stock_code, short_name)
        expected_path = f"companies/{expected_id}"
        if report.get("company_id") != expected_id:
            issue(issues, report_catalog_path, "report_catalog_company_id_mismatch", f"{report.get('company_id')} should be {expected_id}")
        if report.get("company_path") != expected_path:
            issue(issues, report_catalog_path, "report_catalog_company_path_mismatch", f"{report.get('company_path')} should be {expected_path}")
    validate_structured_names(catalog_path, catalog, issues)
    validate_structured_names(report_catalog_path, report_catalog, issues)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wiki-root", default="/home/maoyd/wiki")
    args = parser.parse_args()

    wiki_root = Path(args.wiki_root)
    companies_root = wiki_root / "companies"
    issues: list[dict[str, Any]] = []
    for company_dir in sorted(path for path in companies_root.iterdir() if path.is_dir()):
        company = read_json(company_dir / "company.json", {})
        if isinstance(company, dict) and company and not is_a_share_company_dir(company_dir, company):
            continue
        validate_company_dir(company_dir, issues)
        validate_company_structured_json(company_dir, issues)
    validate_catalog(wiki_root, issues)

    payload = {"schema_version": 1, "wiki_root": str(wiki_root), "issue_count": len(issues), "issues": issues}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
