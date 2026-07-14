#!/usr/bin/env python3
"""Build HK company Wiki workspaces from standardized PDF parser results.

This script follows the A-share Wiki contract, but uses HK-specific identity,
period, statement, and evidence rules. It reads the parser result contract under
data/pdf-parser/results and writes data/wiki/hk only when --apply is supplied.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import time
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parents[3]
RULES_SRC = REPO_ROOT / "services" / "market-report-rules" / "src"
PDF_PARSER_APP = REPO_ROOT / "apps" / "pdf-parser"
MAINTENANCE_DIR = REPO_ROOT / "scripts" / "maintenance"
for import_path in (RULES_SRC, PDF_PARSER_APP, MAINTENANCE_DIR):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

from evidence_metadata import attach_evidence_metadata  # noqa: E402
from hk_financial_artifacts import HK_FINANCIAL_PROFILE_VERSION, build_hk_financial_artifacts  # noqa: E402
from market_report_rules_service.evidence_package import build_quality_gates, validate_evidence_package  # noqa: E402
from market_report_rules_service.markets.hk.extractor import resolve_hk_currency  # noqa: E402
from package_facade import write_report_package_facade  # noqa: E402

DEFAULT_RESULTS_DIR = REPO_ROOT / "data" / "pdf-parser" / "results"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "data" / "wiki" / "hk"
DEFAULT_DOWNLOADS_ROOT = REPO_ROOT / "data" / "market-report-finder" / "downloads" / "HK"
HKEX_OFFICIAL_HOST_SUFFIXES = ("hkexnews.hk", "hkex.com.hk")

REPORT_KIND_SLUG = {
    "annual_report": "annual",
    "annual": "annual",
    "年报": "annual",
    "interim_report": "interim",
    "half_year_report": "interim",
    "quarterly_report": "quarterly",
}

HK_FILENAME_RE = re.compile(
    r"^(?P<company>.+?)_"
    r"(?P<market>HK)_"
    r"(?P<ticker>\d{5})_"
    r"(?P<period_end>\d{4}-\d{2}-\d{2})_"
    r"(?P<report_type>[^_]+)_"
    r"(?P<published_at>\d{4}-\d{2}-\d{2})_"
    r"(?P<source_id>.+?)_"
    r"(?P<url_hash>[0-9a-fA-F]{8})"
    r"(?:\.pdf)?$",
    re.IGNORECASE,
)

PRIMARY_CANONICALS = {
    "balance_sheet": {
        "total_assets",
        "total_liabilities",
        "total_equity",
        "net_assets",
        "parent_equity",
        "current_assets",
        "non_current_assets",
        "current_liabilities",
        "non_current_liabilities",
        "cash_and_cash_equivalents",
        "total_liabilities_and_equity",
    },
    "income_statement": {
        "operating_revenue",
        "total_income",
        "gross_profit",
        "operating_profit",
        "profit_before_tax",
        "income_tax_expense",
        "net_profit",
        "net_interest_income",
        "parent_net_profit",
        "finance_costs",
        "total_profit",
    },
    "cash_flow_statement": {
        "operating_cash_flow_net",
        "cash_generated_from_operations",
        "investing_cash_flow_net",
        "financing_cash_flow_net",
        "cash_equivalents_beginning",
        "cash_equivalents_ending",
        "cash_equivalents_net_increase",
    },
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def load_current_hk_financial_artifacts(
    result_dir: Path,
    *,
    metadata: dict[str, Any],
    document_full: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], str]:
    financial_data = read_json(result_dir / "financial_data.json", {})
    financial_checks = read_json(result_dir / "financial_checks.json", {})
    profiles = {
        str(financial_data.get("profile_rule_version") or ""),
        str(financial_checks.get("profile_rule_version") or ""),
    }
    if profiles == {HK_FINANCIAL_PROFILE_VERSION}:
        return normalize_hk_financial_data_currency(financial_data), financial_checks, "stored_current"

    task = deepcopy(document_full.get("task")) if isinstance(document_full.get("task"), dict) else {}
    task["task_id"] = str(task.get("task_id") or result_dir.name)
    task["filename"] = str(task.get("filename") or metadata.get("filename") or "")
    markdown_path = result_dir / "result.md"
    if not markdown_path.is_file():
        markdown_path = result_dir / "result_complete.md"
    if not markdown_path.is_file():
        raise RuntimeError(f"HK financial artifact rebuild has no Markdown source: {result_dir.name}")
    rebuilt_data, rebuilt_checks = build_hk_financial_artifacts(
        task,
        markdown_path.read_text(encoding="utf-8", errors="ignore"),
        result_dir_path=str(result_dir),
        filename=task["filename"],
    )
    return normalize_hk_financial_data_currency(rebuilt_data), rebuilt_checks, "rebuilt_in_memory"


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def rel(path: Path, root: Path = REPO_ROOT) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as infile:
        for chunk in iter(lambda: infile.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_hk_sidecar(
    filename: str,
    *,
    metadata: dict[str, Any] | None = None,
    downloads_root: Path = DEFAULT_DOWNLOADS_ROOT,
) -> dict[str, Any]:
    """Resolve one HKEX download sidecar without guessing a filing identity."""
    metadata = metadata if isinstance(metadata, dict) else {}
    candidates: list[Path] = []
    upload_path = metadata.get("upload_path") or metadata.get("source_path")
    if upload_path:
        upload = Path(str(upload_path)).expanduser()
        candidates.extend([upload.with_suffix(upload.suffix + ".metadata.json"), upload.parent / f"{upload.name}.metadata.json"])
    name = Path(str(filename or "")).name
    if downloads_root.exists() and name:
        candidates.extend(downloads_root.rglob(f"{name}.metadata.json"))
    unique = sorted({path.resolve() for path in candidates if path.is_file()})
    if len(unique) != 1:
        return {
            "status": "missing" if not unique else "ambiguous",
            "candidate_count": len(unique),
            "paths": [rel(path) for path in unique[:10]],
        }
    payload = read_json(unique[0], {})
    candidate = payload.get("candidate") if isinstance(payload, dict) and isinstance(payload.get("candidate"), dict) else {}
    downloaded = payload.get("downloaded_file") if isinstance(payload, dict) and isinstance(payload.get("downloaded_file"), dict) else {}
    expected_sha256 = str(downloaded.get("content_sha256") or "").strip().lower()
    source_pdf_candidates = [Path(str(unique[0]).removesuffix(".metadata.json"))]
    saved_path = str(downloaded.get("saved_path") or "").strip()
    if saved_path:
        saved = Path(saved_path).expanduser()
        if not saved.is_absolute():
            saved = REPO_ROOT / saved
        source_pdf_candidates.append(saved)
    source_pdf = next(
        (
            path.resolve()
            for path in source_pdf_candidates
            if path.is_file() and (not expected_sha256 or sha256_file(path).lower() == expected_sha256)
        ),
        None,
    )
    source_url = candidate.get("document_url") or candidate.get("source_url") or candidate.get("landing_url")
    ticker = str(candidate.get("company_id") or candidate.get("ticker") or "").strip()
    period_end = str(candidate.get("report_end") or candidate.get("period_end") or "").strip()
    return {
        "status": "resolved" if candidate.get("accession_number") and ticker and period_end else "invalid",
        "path": rel(unique[0]),
        "accession_number": str(candidate.get("accession_number") or "").strip(),
        "ticker": ticker.zfill(5) if ticker.isdigit() else ticker,
        "period_end": period_end,
        "report_family": candidate.get("report_family") or candidate.get("report_type") or candidate.get("form"),
        "source_url": source_url,
        "source_domain": candidate.get("source_domain"),
        "content_sha256": expected_sha256,
        "source_pdf_path": rel(source_pdf) if source_pdf else "",
        "source_pdf_hash_verified": bool(source_pdf and expected_sha256),
        "candidate": candidate,
    }


def is_official_hkex_url(value: Any) -> bool:
    parsed = urlparse(str(value or "").strip())
    host = str(parsed.hostname or "").rstrip(".").lower()
    return parsed.scheme.lower() in {"http", "https"} and any(
        host == suffix or host.endswith(f".{suffix}") for suffix in HKEX_OFFICIAL_HOST_SUFFIXES
    )


def canonical_hk_identity(
    sidecar: dict[str, Any],
    *,
    ticker: str,
    period_end: Any,
    report_family: Any = None,
    statement_period_verified: bool = False,
) -> dict[str, str] | None:
    if sidecar.get("status") != "resolved":
        return None
    if sidecar.get("ticker") and str(sidecar.get("ticker")) != str(ticker).zfill(5):
        return None
    expected_period = str(period_end or "").strip()[:10]
    sidecar_period = str(sidecar.get("period_end") or "").strip()[:10]
    period_matches = bool(expected_period and sidecar_period == expected_period)
    verified_same_fiscal_year_correction = bool(
        statement_period_verified
        and expected_period
        and sidecar_period
        and expected_period[:4] == sidecar_period[:4]
    )
    if not period_matches and not verified_same_fiscal_year_correction:
        return None
    if report_family and report_kind_slug(sidecar.get("report_family")) != report_kind_slug(report_family):
        return None
    accession = str(sidecar.get("accession_number") or "").strip()
    content_sha256 = str(sidecar.get("content_sha256") or "").strip().lower()
    source_url = str(sidecar.get("source_url") or "").strip()
    if (
        not accession
        or not re.fullmatch(r"[0-9a-f]{64}", content_sha256)
        or not sidecar.get("source_pdf_hash_verified")
        or not is_official_hkex_url(source_url)
    ):
        return None
    ticker_text = str(ticker).zfill(5)
    return {
        "filing_id": f"HK:{ticker_text}:{accession}",
        "parse_run_id": f"HK:{ticker_text}:{accession}:{content_sha256[:16]}",
        "source_url": source_url,
        "source_sha256": content_sha256,
    }


def safe_slug(value: Any, fallback: str = "UNKNOWN") -> str:
    text = str(value or "").strip()
    text = text.replace("&", " and ")
    text = re.sub(r"[\\/:*?\"<>|\r\n\t]+", " ", text)
    text = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text or fallback


def clean_company_name(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[-_]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def parse_hk_filename(filename: Any) -> dict[str, str]:
    stem = Path(str(filename or "")).name
    stem = re.sub(r"\.pdf$", "", stem, flags=re.IGNORECASE)
    match = HK_FILENAME_RE.match(stem)
    if not match:
        return {}
    data = {key: str(value or "").strip() for key, value in match.groupdict().items()}
    data["company_name"] = clean_company_name(data.get("company"))
    data["source_filename"] = Path(str(filename or "")).name
    data["filename_pattern"] = "<company>_HK_<ticker>_<period_end>_<report_type>_<published_at>_<source_id>_<url_hash>.pdf"
    return data


def report_year_from_period(period_end: Any, fallback: Any = None) -> int | None:
    for value in (period_end, fallback):
        match = re.search(r"(20\d{2}|19\d{2})", str(value or ""))
        if match:
            return int(match.group(1))
    return None


def report_kind_slug(report_kind: Any, report_type: Any = None) -> str:
    key = str(report_kind or report_type or "annual_report").strip()
    return REPORT_KIND_SLUG.get(key, safe_slug(key.lower(), "report"))


def source_metadata(meta: dict[str, Any], filename: str) -> dict[str, Any]:
    parsed = parse_hk_filename(filename)
    return {
        key: value
        for key, value in {
            "source_filename": filename,
            "filename_pattern": parsed.get("filename_pattern"),
            "company_short_name": parsed.get("company_name") or meta.get("company_name"),
            "market": "HK",
            "stock_code": parsed.get("ticker") or meta.get("ticker") or meta.get("stock_code"),
            "raw_ticker": parsed.get("ticker") or meta.get("ticker"),
            "report_end": parsed.get("period_end") or meta.get("period_end"),
            "report_type": parsed.get("report_type") or meta.get("report_type"),
            "published_at": parsed.get("published_at") or meta.get("disclosure_date"),
            "source_id": parsed.get("source_id") or meta.get("source"),
            "url_hash": parsed.get("url_hash"),
            "source": "hk_report_finder_filename" if parsed else meta.get("source"),
        }.items()
        if value
    }


def evidence_urls(task_id: str, page: Any, table_index: Any) -> dict[str, str]:
    payload = {
        "open_pdf_page_url": "",
        "open_source_page_url": "",
        "open_source_table_url": "",
    }
    if task_id and page:
        payload["open_pdf_page_url"] = f"/api/pdf_page/{task_id}/{page}"
        payload["open_source_page_url"] = f"/api/source/{task_id}/page/{page}"
    if task_id and table_index:
        payload["open_source_table_url"] = f"/api/source/{task_id}/table/{table_index}"
    return payload


def table_by_index(table_index: Any, table_index_payload: Any) -> dict[str, Any]:
    try:
        wanted = int(table_index)
    except Exception:
        return {}
    tables = table_index_payload if isinstance(table_index_payload, list) else table_index_payload.get("tables") if isinstance(table_index_payload, dict) else []
    for table in tables or []:
        try:
            if int(table.get("table_index")) == wanted:
                return table
        except Exception:
            continue
    return {}


def evidence_from_item(item: dict[str, Any], result_dir: Path, table_index_payload: Any) -> dict[str, Any]:
    evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
    raw = evidence.get("raw") if isinstance(evidence.get("raw"), dict) else {}
    raw_table = raw.get("table") if isinstance(raw.get("table"), dict) else {}
    table_index = evidence.get("table_index") or raw.get("table_index") or (item.get("raw") or {}).get("table_id")
    table = table_by_index(table_index, table_index_payload)
    md_line = raw_table.get("line") or evidence.get("md_line") or evidence.get("line") or table.get("line")
    page = (
        evidence.get("pdf_page_number")
        or evidence.get("page_number")
        or evidence.get("rendered_page_number")
        or table.get("pdf_page_number")
    )
    if not table_index and evidence.get("source_id"):
        match = re.search(r"(\d+)$", str(evidence.get("source_id")))
        if match:
            table_index = int(match.group(1))
    return {
        "source_type": evidence.get("source_type") or "pdf_statement_table",
        "source_id": evidence.get("source_id"),
        "quote_text": evidence.get("quote_text"),
        "md_line": md_line,
        "pdf_page_number": page,
        "table_index": table_index,
        "row_index": evidence.get("row_index"),
        "column_index": evidence.get("column_index"),
        "heading": raw_table.get("heading") or table.get("heading"),
        "source_kind": raw_table.get("source") or "financial_data_statement",
        "markdown_path": rel(result_dir / "result_complete.md"),
    }


def item_period(item: dict[str, Any], default_period: str = "") -> str:
    return str(item.get("period_key") or item.get("period_end") or default_period or "").strip()


def to_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()").replace(",", "")
    text = re.sub(r"[^0-9.\-]", "", text)
    if text in {"", "-", ".", "-."}:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return -number if negative and number > 0 else number


def prior_year_period(period: str) -> str:
    match = re.match(r"^(\d{4})(-\d{2}-\d{2})$", str(period or ""))
    if not match:
        return ""
    return f"{int(match.group(1)) - 1}{match.group(2)}"


def preferred_periods(row: dict[str, Any]) -> set[str]:
    current = str(row.get("period_end") or "").strip()
    periods = {current} if current else set()
    prior = prior_year_period(current)
    if prior:
        periods.add(prior)
    return periods


def statement_reporting_period(financial_data: dict[str, Any], *, max_period: str = "") -> dict[str, Any]:
    periods_by_statement: dict[str, list[str]] = {}
    for statement in financial_data.get("statements") or []:
        if not isinstance(statement, dict) or (statement.get("scope") or "consolidated") != "consolidated":
            continue
        statement_type = str(statement.get("statement_type") or "")
        if statement_type not in PRIMARY_CANONICALS:
            continue
        periods = {
            item_period(item)
            for item in statement.get("items") or []
            if isinstance(item, dict)
            and item.get("canonical_name") in PRIMARY_CANONICALS[statement_type]
            and re.fullmatch(r"\d{4}-\d{2}-\d{2}", item_period(item))
        }
        if periods:
            periods_by_statement[statement_type] = sorted(periods)
    required = ("balance_sheet", "income_statement", "cash_flow_statement")
    if any(statement not in periods_by_statement for statement in required):
        return {"verified": False, "period_end": "", "periods_by_statement": periods_by_statement}
    common_periods = set(periods_by_statement[required[0]])
    for statement in required[1:]:
        common_periods.intersection_update(periods_by_statement[statement])
    eligible_periods = {
        period
        for period in common_periods
        if not max_period or period <= str(max_period)[:10]
    }
    return {
        "verified": bool(eligible_periods),
        "period_end": max(eligible_periods) if eligible_periods else "",
        "periods_by_statement": periods_by_statement,
        "common_periods": sorted(common_periods),
        "eligible_periods": sorted(eligible_periods),
    }


def source_priority(item: dict[str, Any]) -> int:
    evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
    raw = evidence.get("raw") if isinstance(evidence.get("raw"), dict) else {}
    table = raw.get("table") if isinstance(raw.get("table"), dict) else {}
    source = str(table.get("source") or "")
    heading = str(table.get("heading") or "")
    preview = str(table.get("preview") or "")
    text = f"{heading} {preview}".lower()
    score = 0
    if "result_markdown_statement_table" in source:
        score += 100
    if "result_markdown_formal_statement_window" in source:
        score += 80
    if "result_markdown_selected_operations_fallback" in source:
        score += 30
    if "consolidated" in text:
        score += 40
    if "statement of financial position" in text or "balance sheet" in text:
        score += 20
    if "income statement" in text or "statements of operations" in text:
        score += 20
    if "cash flow" in text:
        score += 20
    if "ifrs" in text or "hkfrs" in text:
        score += 35
    if "china accounting standards" in text or "casbe" in text:
        score -= 60
    if "selected consolidated statements of operations data" in text:
        score -= 25
    if "supplemental information" in text or "supplementary information" in text:
        score -= 50
    try:
        score += int(float(item.get("confidence") or 0) * 10)
    except Exception:
        pass
    return score


def build_three_statements(row: dict[str, Any]) -> dict[str, Any]:
    financial_data = row["financial_data"]
    result_dir = row["result_dir"]
    table_index_payload = row["table_index"]
    metrics_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    allowed_periods = preferred_periods(row)
    for statement in financial_data.get("statements") or []:
        statement_type = statement.get("statement_type")
        if statement_type not in PRIMARY_CANONICALS:
            continue
        scope = statement.get("scope") or "consolidated"
        if scope != "consolidated":
            continue
        for item in statement.get("items") or []:
            canonical = str(item.get("canonical_name") or "").strip()
            if not canonical or canonical not in PRIMARY_CANONICALS[statement_type]:
                continue
            period = item_period(item, row["period_end"])
            if allowed_periods and period not in allowed_periods:
                continue
            value = to_float(item.get("value"))
            evidence = evidence_from_item(item, result_dir, table_index_payload)
            metric_unit = item.get("unit") or statement.get("unit") or financial_data.get("unit") or ""
            metric_currency = resolve_hk_currency(
                unit=metric_unit,
                declared_currency=item.get("currency"),
                title=statement.get("title") or statement.get("statement_name"),
                fallback=statement.get("currency") or financial_data.get("currency"),
            )
            metric = {
                "metric_key": canonical,
                "metric_name": item.get("local_name") or item.get("label") or canonical,
                "canonical_name": canonical,
                "local_name": item.get("local_name") or item.get("label"),
                "raw_value": item.get("raw_value") or item.get("value"),
                "value": value,
                "unit": metric_unit,
                "currency": metric_currency,
                "scale": item.get("scale") or statement.get("scale") or "1",
                "confidence": item.get("confidence"),
                "statement_type": statement_type,
                "scope": scope,
                "period": period,
                "fiscal_year": item.get("fiscal_year") or report_year_from_period(period),
                "source": {
                    **evidence,
                    "task_id": row["task_id"],
                    "period": period,
                    "source_kind": evidence.get("source_kind") or "financial_data_statement",
                },
            }
            metric["source"].update(evidence_urls(row["task_id"], evidence.get("pdf_page_number"), evidence.get("table_index")))
            metric["_source_priority"] = source_priority(item)
            key = (statement_type, canonical, period)
            previous = metrics_by_key.get(key)
            if not previous or metric["_source_priority"] > previous.get("_source_priority", -9999):
                metrics_by_key[key] = metric
    metrics = []
    for metric in metrics_by_key.values():
        metric.pop("_source_priority", None)
        metrics.append(metric)
    metrics.sort(key=lambda item: (str(item.get("statement_type") or ""), str(item.get("metric_key") or ""), str(item.get("period") or "")), reverse=False)
    return {
        "company": row["company_name"],
        "stock_code": row["ticker"],
        "ticker": row["ticker"],
        "market": "HK",
        "report_id": row["report_id"],
        "period_end": row["period_end"],
        "metrics": metrics,
        "extraction_method": "hk_pdf_financial_data_statement_bridge_v1",
    }


def build_package_financial_data(
    row: dict[str, Any],
    three_statements: dict[str, Any],
    evidence_items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Convert selected HK facts into the portable evidence-package contract."""
    buckets: dict[tuple[str, str, str, str, str, str], dict[str, Any]] = {}
    for metric_index, metric in enumerate(three_statements.get("metrics") or []):
        if not isinstance(metric, dict):
            continue
        statement_type = str(metric.get("statement_type") or "").strip()
        canonical_name = str(metric.get("canonical_name") or metric.get("metric_key") or "").strip()
        period = str(metric.get("period") or "").strip()
        if not statement_type or not canonical_name or not period:
            continue
        key = (
            statement_type,
            canonical_name,
            str(metric.get("metric_name") or metric.get("local_name") or canonical_name),
            str(metric.get("unit") or ""),
            str(metric.get("currency") or ""),
            str(metric.get("scale") or "1"),
        )
        item = buckets.setdefault(
            key,
            {
                "name": key[2],
                "canonical_name": canonical_name,
                "statement_type": statement_type,
                "values": {},
                "raw_values": {},
                "sources": {},
                "periods": {},
                "unit": key[3],
                "currency": key[4],
                "scale": key[5],
            },
        )
        source = dict(metric.get("source") or {})
        evidence_item = evidence_items[metric_index] if evidence_items and metric_index < len(evidence_items) else {}
        source["evidence_id"] = evidence_item.get("evidence_id")
        source["artifact_path"] = "parser/result_complete.md"
        source["line"] = source.get("md_line")
        source["page_number"] = source.get("pdf_page_number")
        item["values"][period] = metric.get("value")
        item["raw_values"][period] = metric.get("raw_value")
        item["sources"][period] = source
        item["periods"][period] = {
            "period_end": period,
            "fiscal_year": metric.get("fiscal_year") or report_year_from_period(period),
        }

    statements = []
    for statement_type in ("balance_sheet", "income_statement", "cash_flow_statement"):
        items = [item for key, item in buckets.items() if key[0] == statement_type]
        items.sort(key=lambda item: str(item.get("canonical_name") or ""))
        statements.append(
            {
                "statement_type": statement_type,
                "scope": "consolidated",
                "items": items,
            }
        )
    source_financial_data = row.get("financial_data") if isinstance(row.get("financial_data"), dict) else {}
    return {
        "schema_version": "hk_package_financial_data_v1",
        "market": "HK",
        "company_id": f"HK:{row['ticker']}",
        "ticker": row["ticker"],
        "company_name": row["company_name"],
        "filing_id": row.get("filing_id"),
        "parse_run_id": row.get("parse_run_id"),
        "report_id": row["report_id"],
        "period_end": row["period_end"],
        "currency": source_financial_data.get("currency"),
        "accounting_standard": source_financial_data.get("accounting_standard") or "HKFRS",
        "statements": statements,
        "key_metrics": [],
        "operating_metrics": [],
        "warnings": list(row.get("warnings") or []),
    }


def package_unit_currency_mismatches(financial_data: dict[str, Any]) -> list[dict[str, str]]:
    mismatches: list[dict[str, str]] = []
    for statement_index, statement in enumerate(financial_data.get("statements") or []):
        if not isinstance(statement, dict):
            continue
        for item_index, item in enumerate(statement.get("items") or []):
            if not isinstance(item, dict):
                continue
            unit_currency = resolve_hk_currency(unit=item.get("unit"), declared_currency=None)
            fact_currency = resolve_hk_currency(unit=None, declared_currency=item.get("currency"))
            if unit_currency and fact_currency and unit_currency != fact_currency:
                mismatches.append(
                    {
                        "location": f"statements[{statement_index}].items[{item_index}]",
                        "unit_currency": unit_currency,
                        "currency": fact_currency,
                    }
                )
    return mismatches


def normalize_hk_financial_data_currency(financial_data: dict[str, Any]) -> dict[str, Any]:
    """Repair stale parser currency fields while preserving reported units."""
    normalized = deepcopy(financial_data)
    report_currency = normalized.get("currency")
    for statement in normalized.get("statements") or []:
        if not isinstance(statement, dict):
            continue
        statement_unit = statement.get("unit") or normalized.get("unit")
        statement_currency = resolve_hk_currency(
            unit=statement_unit,
            declared_currency=statement.get("currency"),
            title=statement.get("title") or statement.get("statement_name"),
            fallback=report_currency,
        )
        item_currencies: set[str] = set()
        for item in statement.get("items") or []:
            if not isinstance(item, dict):
                continue
            item_unit = item.get("unit") or statement_unit
            item_currency = resolve_hk_currency(
                unit=item_unit,
                declared_currency=item.get("currency"),
                title=statement.get("title") or statement.get("statement_name"),
                fallback=statement_currency or report_currency,
            )
            if item_currency:
                item["currency"] = item_currency
                item_currencies.add(item_currency)
        if len(item_currencies) == 1:
            statement_currency = next(iter(item_currencies))
        if statement_currency:
            statement["currency"] = statement_currency
    return normalized


def build_evidence_index(row: dict[str, Any], three_statement_payload: dict[str, Any]) -> list[dict[str, Any]]:
    evidence_items: list[dict[str, Any]] = []
    for index, metric in enumerate(three_statement_payload.get("metrics") or [], start=1):
        source = metric.get("source") or {}
        item = {
            "evidence_id": f"{row['ticker']}-{row['report_id']}-metric-{index:05d}",
            "company_id": row["company_wiki_id"],
            "company_wiki_id": row["company_wiki_id"],
            "report_id": row["report_id"],
            "market": "HK",
            "stock_code": row["ticker"],
            "ticker": row["ticker"],
            "metric_key": metric.get("metric_key"),
            "metric_name": metric.get("metric_name"),
            "statement_type": metric.get("statement_type"),
            "scope": metric.get("scope"),
            "period": metric.get("period"),
            "raw_value": metric.get("raw_value"),
            "value": metric.get("value"),
            "unit": metric.get("unit"),
            "currency": metric.get("currency"),
            "scale": metric.get("scale"),
            "task_id": row["task_id"],
            "md_line": source.get("md_line"),
            "pdf_page_number": source.get("pdf_page_number"),
            "table_index": source.get("table_index"),
            "row_index": source.get("row_index"),
            "column_index": source.get("column_index"),
            "quote_text": source.get("quote_text"),
            "heading": source.get("heading"),
            "source_kind": source.get("source_kind"),
            "file": f"metrics/reports/{row['report_id']}/three_statements.json",
        }
        item.update(evidence_urls(row["task_id"], item.get("pdf_page_number"), item.get("table_index")))
        evidence_items.append(item)
    return evidence_items


def build_key_metrics(financial_data: dict[str, Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for item in financial_data.get("key_metrics") or []:
        if isinstance(item, dict):
            output.append(item)
    return output


def build_pdf_refs(evidence_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    refs = []
    seen: set[tuple[Any, Any, Any]] = set()
    for item in evidence_items:
        key = (item.get("task_id"), item.get("pdf_page_number"), item.get("table_index"))
        if key in seen or (not key[1] and not key[2]):
            continue
        seen.add(key)
        ref = {
            "company_id": item.get("company_id"),
            "report_id": item.get("report_id"),
            "task_id": item.get("task_id"),
            "pdf_page_number": item.get("pdf_page_number"),
            "table_index": item.get("table_index"),
            "md_line": item.get("md_line"),
        }
        ref.update(evidence_urls(str(item.get("task_id") or ""), item.get("pdf_page_number"), item.get("table_index")))
        refs.append(ref)
    return refs


def build_retrieval_index(row: dict[str, Any], evidence_items: list[dict[str, Any]]) -> dict[str, Any]:
    chunks = []
    for item in evidence_items:
        chunks.append(
            {
                "chunk_id": f"{item['evidence_id']}-chunk",
                "evidence_id": item["evidence_id"],
                "market": "HK",
                "company_id": row["company_wiki_id"],
                "company_name": row["company_name"],
                "ticker": row["ticker"],
                "report_id": row["report_id"],
                "filing_id": row.get("filing_id"),
                "parse_run_id": row.get("parse_run_id"),
                "topic": item.get("metric_key"),
                "source_type": "wiki_metrics",
                "file": item.get("file"),
                "pdf_page_number": item.get("pdf_page_number"),
                "table_index": item.get("table_index"),
                "md_line": item.get("md_line"),
                "text": " | ".join(
                    str(part)
                    for part in [
                        item.get("statement_type"),
                        item.get("metric_name"),
                        item.get("period"),
                        item.get("raw_value"),
                        item.get("currency"),
                    ]
                    if part not in (None, "")
                ),
            }
        )
    chunks.append(
        {
            "chunk_id": f"{row['ticker']}-{row['report_id']}-full-report",
            "market": "HK",
            "company_id": row["company_wiki_id"],
            "company_name": row["company_name"],
            "ticker": row["ticker"],
            "report_id": row["report_id"],
            "topic": "full_report",
            "source_type": "wiki_report_markdown",
            "file": f"reports/{row['report_id']}/report.md",
            "text": f"{row['company_name']} {row['ticker']} {row['report_year']} annual report full text fallback",
        }
    )
    return {
        "schema_version": "hk_retrieval_index_v1",
        "market": "HK",
        "company_id": row["company_wiki_id"],
        "report_id": row["report_id"],
        "chunk_count": len(chunks),
        "chunks": chunks,
        "generated_at": now_iso(),
    }


def inspect_hk_result(
    result_dir: Path,
    *,
    downloads_root: Path = DEFAULT_DOWNLOADS_ROOT,
) -> dict[str, Any] | None:
    metadata = read_json(result_dir / "metadata.json", {})
    document_full = read_json(result_dir / "document_full.json", {})
    stored_financial_data = read_json(result_dir / "financial_data.json", {})
    if str(metadata.get("market") or stored_financial_data.get("market") or "").upper() != "HK":
        return None
    financial_data, financial_checks, financial_artifact_source = load_current_hk_financial_artifacts(
        result_dir,
        metadata=metadata,
        document_full=document_full,
    )
    quality = read_json(result_dir / "quality_report.json", {})
    table_index = read_json(result_dir / "table_index.json", [])
    artifact_manifest = read_json(result_dir / "artifact_manifest.json", {})
    hash_manifest = read_json(result_dir / "hash_manifest.json", {})
    filename = (
        metadata.get("filename")
        or financial_data.get("filename")
        or ((document_full.get("task") or {}).get("filename") if isinstance(document_full, dict) else "")
        or ""
    )
    parsed = parse_hk_filename(filename)
    ticker = str(metadata.get("ticker") or metadata.get("stock_code") or financial_data.get("ticker") or parsed.get("ticker") or "").zfill(5)
    company_name = clean_company_name(metadata.get("company_name") or financial_data.get("company_name") or parsed.get("company_name") or ticker)
    declared_period_end = str(
        metadata.get("period_end") or financial_data.get("period_end") or parsed.get("period_end") or ""
    )
    reporting_period = statement_reporting_period(financial_data, max_period=declared_period_end)
    period_end = str(reporting_period.get("period_end") or declared_period_end)
    report_year = metadata.get("report_year") or financial_data.get("report_year") or metadata.get("fiscal_year") or report_year_from_period(period_end, filename)
    report_kind = metadata.get("report_kind") or financial_data.get("report_kind") or "annual_report"
    report_id = f"{int(report_year)}-{report_kind_slug(report_kind, metadata.get('report_type'))}" if report_year else f"unknown-{result_dir.name[:8]}"
    company_wiki_id = f"{ticker}-{safe_slug(company_name)}"
    sidecar = resolve_hk_sidecar(filename, metadata=metadata, downloads_root=downloads_root)
    canonical_identity = canonical_hk_identity(
        sidecar,
        ticker=ticker,
        period_end=period_end,
        report_family=report_kind,
        statement_period_verified=bool(reporting_period.get("verified")),
    )
    warnings: list[str] = []
    if not ticker or ticker == "00000":
        warnings.append("missing_hk_ticker")
    if not company_name or company_name == ticker:
        warnings.append("missing_company_name")
    if not report_year:
        warnings.append("missing_report_year")
    if len(financial_data.get("statements") or []) < 3:
        warnings.append("missing_three_financial_statements")
    if financial_checks.get("overall_status") == "fail":
        warnings.append("financial_checks_fail")
    if reporting_period.get("verified") and period_end != declared_period_end:
        warnings.append("reporting_period_corrected_from_three_statement_evidence")
    if sidecar.get("period_end") and str(sidecar.get("period_end"))[:10] != period_end:
        warnings.append("hkex_sidecar_period_corrected_from_three_statement_evidence")
    if sidecar.get("status") != "resolved":
        warnings.append(f"hkex_sidecar_{sidecar.get('status') or 'missing'}")
    elif canonical_identity is None:
        warnings.append("hkex_sidecar_identity_mismatch")
    return {
        "task_id": result_dir.name,
        "result_dir": result_dir,
        "metadata": metadata,
        "document_full": document_full,
        "quality": quality,
        "financial_data": financial_data,
        "financial_checks": financial_checks,
        "financial_artifact_source": financial_artifact_source,
        "table_index": table_index,
        "artifact_manifest": artifact_manifest,
        "hash_manifest": hash_manifest,
        "filename": filename,
        "source_metadata": source_metadata(metadata, filename),
        "ticker": ticker,
        "stock_code": ticker,
        "company_name": company_name,
        "company_wiki_id": company_wiki_id,
        "period_end": period_end,
        "declared_period_end": declared_period_end,
        "reporting_period_evidence": reporting_period,
        "published_at": parsed.get("published_at") or metadata.get("disclosure_date"),
        "source_id": parsed.get("source_id") or metadata.get("source") or "hkex",
        "report_year": int(report_year) if report_year else None,
        "report_kind": report_kind,
        "report_type": metadata.get("report_type") or parsed.get("report_type") or report_kind,
        "report_id": report_id,
        "sidecar": sidecar,
        "source_pdf_path": sidecar.get("source_pdf_path"),
        "canonical_identity": canonical_identity,
        "warnings": warnings,
        "score": (
            (1000 if ticker else 0)
            + (500 if report_year else 0)
            + (300 if financial_checks.get("overall_status") in {"pass", "warning"} else 0)
            + len(financial_data.get("statements") or []) * 100
            + int(quality.get("table_count") or 0)
        ),
    }


def select_active(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    skipped = []
    for row in rows:
        if row["warnings"] and any(w in row["warnings"] for w in ("missing_hk_ticker", "missing_company_name", "missing_report_year")):
            skipped.append({k: row.get(k) for k in ("task_id", "filename", "warnings")})
            continue
        grouped[(row["ticker"], row["report_id"])].append(row)
    active = []
    duplicates = {}
    for key, candidates in sorted(grouped.items()):
        candidates.sort(key=lambda item: item["score"], reverse=True)
        active.append(candidates[0])
        if len(candidates) > 1:
            duplicates[f"{key[0]}-{key[1]}"] = [
                {
                    "task_id": item["task_id"],
                    "filename": item["filename"],
                    "score": item["score"],
                    "selected": index == 0,
                    "warnings": item["warnings"],
                }
                for index, item in enumerate(candidates)
            ]
    return active, {"duplicates": duplicates, "skipped": skipped}


def copy_report_files(row: dict[str, Any], report_dir: Path) -> dict[str, str]:
    result_dir = row["result_dir"]
    copied: dict[str, str] = {}
    file_map = {
        "result_complete.md": "report.md",
        "document_full.json": "document_full.json",
        "artifact_manifest.json": "artifact_manifest.json",
    }
    for source_name, dest_name in file_map.items():
        source = result_dir / source_name
        if source.exists():
            shutil.copy2(source, report_dir / dest_name)
            copied[dest_name] = rel(report_dir / dest_name)
    return copied


def write_company(row_group: list[dict[str, Any]], output_root: Path) -> dict[str, Any]:
    row_group.sort(key=lambda row: (row["report_year"] or 0, row["period_end"], row["task_id"]), reverse=True)
    primary = row_group[0]
    company_dir = output_root / "companies" / primary["company_wiki_id"]
    reports = []
    all_evidence = []
    all_pdf_refs = []
    latest_payload = None
    latest_financial_data = None
    latest_financial_checks = None
    latest_key_metrics = None
    status = "ready"

    workspace_dirs = [
        "reports",
        "metrics/reports",
        "metrics/latest",
        "evidence",
        "semantic/llm",
        "graph/facts",
        "graph/claims",
        "graph/notes",
        "graph/segments",
        "analysis",
        "factcheck",
        "tracking",
        "legal",
        "obsidian",
    ]
    for directory in workspace_dirs:
        (company_dir / directory).mkdir(parents=True, exist_ok=True)

    for row in row_group:
        report_dir = company_dir / "reports" / row["report_id"]
        metrics_dir = company_dir / "metrics" / "reports" / row["report_id"]
        report_dir.mkdir(parents=True, exist_ok=True)
        metrics_dir.mkdir(parents=True, exist_ok=True)
        copied = copy_report_files(row, report_dir)
        three_statements = build_three_statements(row)
        evidence_items = build_evidence_index(row, three_statements)
        package_financial_data = build_package_financial_data(row, three_statements, evidence_items)
        pdf_refs = build_pdf_refs(evidence_items)
        key_metrics = build_key_metrics(row["financial_data"])
        validation = {
            "schema_version": "hk_validation_v1",
            "market": "HK",
            "report_id": row["report_id"],
            "financial_checks": row["financial_checks"],
            "three_statement_source": "financial_data.json",
            "three_statement_metric_count": len(three_statements.get("metrics") or []),
            "financial_check_status": row["financial_checks"].get("overall_status"),
            "financial_artifact_source": row.get("financial_artifact_source"),
            "warnings": row["warnings"],
            "generated_at": now_iso(),
        }
        write_json(metrics_dir / "three_statements.json", {"schema_version": 1, "source": "financial_data.json", "unit": "reported", "data": three_statements, "generated_at": now_iso()})
        write_json(metrics_dir / "key_metrics.json", {"schema_version": 1, "source": "financial_data.json", "data": key_metrics, "generated_at": now_iso()})
        write_json(metrics_dir / "validation.json", validation)
        write_json(metrics_dir / "financial_data.json", row["financial_data"])
        write_json(metrics_dir / "financial_checks.json", row["financial_checks"])
        write_json(metrics_dir / "normalized_metrics.json", {"schema_version": "hk_normalized_metrics_v1", "source": "three_statements.json", "metrics": three_statements.get("metrics") or [], "generated_at": now_iso()})

        report_json = build_report_json(row, report_dir, three_statements, evidence_items, copied)
        write_json(report_dir / "report.json", report_json)
        package_manifest = write_report_package_facade(
            market="HK",
            company_dir=company_dir,
            report_dir=report_dir,
            metrics_dir=metrics_dir,
            row=row,
            report_json=report_json,
            three_statements=three_statements,
            key_metrics=key_metrics,
            validation=validation,
            evidence_items=evidence_items,
            package_financial_data=package_financial_data,
        )
        reports.append(
            {
                "report_id": row["report_id"],
                "report_year": row["report_year"],
                "report_kind": row["report_kind"],
                "report_type": row["report_type"],
                "period_end": row["period_end"],
                "published_at": row["published_at"],
                "status": report_json["status"],
                "task_id": row["task_id"],
                "source_filename": row["filename"],
                "source_filename_metadata": row["source_metadata"],
                "report_md": f"reports/{row['report_id']}/report.md",
                "report_json": f"reports/{row['report_id']}/report.json",
                "document_full": f"reports/{row['report_id']}/document_full.json",
                "manifest": f"reports/{row['report_id']}/manifest.json",
                "package_path": package_manifest.get("wiki_report_path"),
                "retrieval_status": "ready" if report_json["status"] == "ready" else "needs_review",
                "wiki_ready": report_json["status"] == "ready",
                "retrieval_issues": [] if report_json["status"] == "ready" else report_json.get("warnings") or [],
                "metrics": {
                    "three_statements": f"metrics/reports/{row['report_id']}/three_statements.json",
                    "key_metrics": f"metrics/reports/{row['report_id']}/key_metrics.json",
                    "validation": f"metrics/reports/{row['report_id']}/validation.json",
                    "financial_data": f"metrics/reports/{row['report_id']}/financial_data.json",
                    "financial_checks": f"metrics/reports/{row['report_id']}/financial_checks.json",
                },
            }
        )
        all_evidence.extend(evidence_items)
        all_pdf_refs.extend(pdf_refs)
        if row is primary:
            latest_payload = three_statements
            latest_financial_data = row["financial_data"]
            latest_financial_checks = row["financial_checks"]
            latest_key_metrics = key_metrics
        if report_json["status"] != "ready":
            status = "needs_review"

    write_json(company_dir / "metrics" / "latest" / "three_statements.json", {"schema_version": 1, "source": "financial_data.json", "unit": "reported", "data": latest_payload or {}, "generated_at": now_iso()})
    write_json(company_dir / "metrics" / "latest" / "key_metrics.json", {"schema_version": 1, "source": "financial_data.json", "data": latest_key_metrics or [], "generated_at": now_iso()})
    write_json(company_dir / "metrics" / "latest" / "validation.json", {"schema_version": "hk_validation_v1", "financial_checks": latest_financial_checks or {}, "three_statement_metric_count": len((latest_payload or {}).get("metrics") or []), "generated_at": now_iso()})
    write_json(company_dir / "metrics" / "latest" / "financial_data.json", latest_financial_data or {})
    write_json(company_dir / "metrics" / "latest" / "financial_checks.json", latest_financial_checks or {})
    write_json(company_dir / "metrics" / "latest" / "normalized_metrics.json", {"schema_version": "hk_normalized_metrics_v1", "source": "three_statements.json", "metrics": (latest_payload or {}).get("metrics") or [], "generated_at": now_iso()})
    write_json(company_dir / "evidence" / "evidence_index.json", {"schema_version": 1, "market": "HK", "company_id": primary["company_wiki_id"], "evidence_count": len(all_evidence), "evidence": all_evidence, "generated_at": now_iso()})
    write_json(company_dir / "evidence" / "pdf_refs.json", {"schema_version": 1, "market": "HK", "company_id": primary["company_wiki_id"], "refs": all_pdf_refs, "generated_at": now_iso()})
    write_json(company_dir / "evidence" / "image_manifest.json", {"schema_version": 1, "market": "HK", "company_id": primary["company_wiki_id"], "images": [], "generated_at": now_iso()})
    write_json(company_dir / "evidence" / "source_map_latest.json", {"schema_version": "hk_source_map_latest_v1", "source": "evidence_index.json", "latest_report_id": primary["report_id"], "generated_at": now_iso()})
    write_json(company_dir / "semantic" / "retrieval_index.json", build_retrieval_index(primary, all_evidence))
    for name, payload in {
        "subject_profile.json": {"schema_version": "hk_subject_profile_v1", "market": "HK", "company_id": primary["company_wiki_id"], "company_name": primary["company_name"], "ticker": primary["ticker"], "generated_at": now_iso()},
        "segments.json": {"schema_version": "hk_segments_v1", "segments": [], "note": "Rule-based segment extraction is deferred; use report.md full-text fallback.", "generated_at": now_iso()},
        "facts.json": {"schema_version": "hk_facts_v1", "facts": [], "generated_at": now_iso()},
        "relations.json": {"schema_version": "hk_relations_v1", "relations": [], "generated_at": now_iso()},
        "claims.json": {"schema_version": "hk_claims_v1", "claims": [], "generated_at": now_iso()},
        "document_links.json": {"schema_version": "hk_document_links_v1", "links": [], "note": "HK note relation extraction is deferred; use report.md/document_full fallback.", "generated_at": now_iso()},
        "note_links.json": {"schema_version": "hk_note_links_v1", "links": [], "generated_at": now_iso()},
        "evidence_semantic.json": {"schema_version": "hk_evidence_semantic_v1", "items": [], "generated_at": now_iso()},
        "extraction_log.json": {"schema_version": "hk_semantic_extraction_log_v1", "steps": [], "generated_at": now_iso()},
    }.items():
        write_json(company_dir / "semantic" / name, payload)
    write_json(company_dir / "graph" / "graph_index.json", {"schema_version": "hk_graph_index_v1", "market": "HK", "company_id": primary["company_wiki_id"], "nodes": [], "edges": [], "generated_at": now_iso()})
    write_text(company_dir / "graph" / "company.md", f"# {primary['company_name']}\n\nHK company graph workspace.\n")
    write_text(company_dir / "graph" / "report.md", f"# {primary['company_name']} Reports\n\nPrimary report: {primary['report_id']}.\n")
    write_text(company_dir / "analysis" / "README.md", f"# {primary['company_name']} Analysis Workspace\n\nAll important conclusions must cite metrics/evidence/report sources.\n")
    write_text(company_dir / "obsidian" / "README.md", f"# {primary['company_name']} Obsidian Workspace\n")
    write_text(company_dir / "obsidian" / "index.md", f"# {primary['company_name']}\n\n- [[../company.md|Company]]\n")

    company_json = {
        "schema_version": "hk_company_wiki_v1",
        "market": "HK",
        "company_id": f"HK:{primary['ticker']}",
        "company_wiki_id": primary["company_wiki_id"],
        "company_wiki_path": rel(company_dir),
        "stock_code": primary["ticker"],
        "ticker": primary["ticker"],
        "hkex_stock_code": primary["ticker"],
        "exchange": "HKEX",
        "company_short_name": primary["company_name"],
        "company_full_name": primary["company_name"],
        "company_name": primary["company_name"],
        "aliases": sorted({primary["ticker"], primary["company_name"], safe_slug(primary["company_name"])}),
        "currency": (latest_financial_data or {}).get("currency"),
        "accounting_standard": (latest_financial_data or {}).get("accounting_standard") or "HKFRS",
        "industry_profile": (latest_financial_data or {}).get("industry_profile"),
        "primary_report_id": primary["report_id"],
        "report_count": len(reports),
        "reports": reports,
        "metrics": {
            "latest": {
                "three_statements": "metrics/latest/three_statements.json",
                "key_metrics": "metrics/latest/key_metrics.json",
                "validation": "metrics/latest/validation.json",
                "financial_data": "metrics/latest/financial_data.json",
                "financial_checks": "metrics/latest/financial_checks.json",
                "normalized_metrics": "metrics/latest/normalized_metrics.json",
            },
            "by_report": {item["report_id"]: item["metrics"] for item in reports},
        },
        "evidence": {
            "evidence_index": "evidence/evidence_index.json",
            "pdf_refs": "evidence/pdf_refs.json",
            "image_manifest": "evidence/image_manifest.json",
            "source_map_latest": "evidence/source_map_latest.json",
        },
        "semantic": {
            "retrieval_index": "semantic/retrieval_index.json",
            "document_links": "semantic/document_links.json",
            "note_links": "semantic/note_links.json",
        },
        "status": status,
        "updated_at": now_iso(),
    }
    write_json(company_dir / "company.json", company_json)
    write_json(company_dir / "_index.json", {"schema_version": "hk_company_index_v1", "market": "HK", "company_id": primary["company_wiki_id"], "primary_report_id": primary["report_id"], "reports": reports, "status": status, "updated_at": now_iso()})
    write_text(company_dir / "company.md", build_company_md(company_json))
    return {
        "company": company_json,
        "reports": reports,
        "evidence_count": len(all_evidence),
        "status": status,
    }


def build_report_json(row: dict[str, Any], report_dir: Path, three_statements: dict[str, Any], evidence_items: list[dict[str, Any]], copied: dict[str, str]) -> dict[str, Any]:
    status = "ready"
    warnings = list(row["warnings"])
    if not (three_statements.get("metrics") or []):
        status = "needs_review"
        warnings.append("empty_three_statement_metrics")
    elif row["financial_checks"].get("overall_status") == "fail":
        status = "needs_review"
    source_files = {}
    for name in ("result_complete.md", "document_full.json", "financial_data.json", "financial_checks.json", "quality_report.json", "table_index.json", "table_relations.json", "content_list_enhanced.json", "artifact_manifest.json", "hash_manifest.json"):
        path = row["result_dir"] / name
        if path.exists():
            source_files[name] = {"path": rel(path), "sha256": sha256_file(path)}
    return {
        "schema_version": "hk_report_wiki_v1",
        "generated_at": now_iso(),
        "identity": {
            "market": "HK",
            "company_id": f"HK:{row['ticker']}",
            "filing_id": row.get("filing_id"),
            "parse_run_id": row.get("parse_run_id"),
            "company_wiki_id": row["company_wiki_id"],
            "ticker": row["ticker"],
            "company_name": row["company_name"],
        },
        "report": {
            "report_id": row["report_id"],
            "report_year": row["report_year"],
            "report_kind": row["report_kind"],
            "report_type": row["report_type"],
            "period_end": row["period_end"],
            "published_at": row["published_at"],
            "source_filename": row["filename"],
            "source_filename_metadata": row["source_metadata"],
        },
        "source": {
            "task_id": row["task_id"],
            "source_url": row.get("source_url"),
            "source_sha256": row.get("source_sha256"),
            "sidecar": row.get("sidecar"),
            "result_dir": rel(row["result_dir"]),
            "copied": copied,
            "source_files": source_files,
            "pdf_page_url_template": "/api/pdf_page/{task_id}/{page_number}",
            "source_page_url_template": "/api/source/{task_id}/page/{page_number}",
            "source_table_url_template": "/api/source/{task_id}/table/{table_index}",
        },
        "quality_summary": {
            "financial_overall_status": row["financial_checks"].get("overall_status"),
            "financial_summary": row["financial_checks"].get("summary"),
            "table_count": row["quality"].get("table_count"),
            "markdown_chars": row["quality"].get("markdown_chars"),
            "warnings": row["quality"].get("warnings") or [],
        },
        "financial_data_summary": {
            "statement_count": len(row["financial_data"].get("statements") or []),
            "three_statement_metric_count": len(three_statements.get("metrics") or []),
            "key_metric_count": len(row["financial_data"].get("key_metrics") or []),
            "warnings": row["financial_data"].get("warnings") or [],
        },
        "evidence": {"count": len(evidence_items), "sample": evidence_items[:20]},
        "status": status,
        "warnings": sorted(set(warnings)),
    }


def build_company_md(company: dict[str, Any]) -> str:
    lines = [
        f"# {company['company_name']} ({company['ticker']})",
        "",
        "- Market: HK",
        f"- Exchange: {company.get('exchange') or 'HKEX'}",
        f"- Primary report: {company.get('primary_report_id')}",
        f"- Status: {company.get('status')}",
        "",
        "## Reports",
        "",
    ]
    for report in company.get("reports") or []:
        lines.append(f"- {report.get('report_year')} {report.get('report_kind')}: [{report.get('report_id')}]({report.get('report_md')})")
    lines.extend(
        [
            "",
            "## Data Entrypoints",
            "",
            "- [Latest three statements](metrics/latest/three_statements.json)",
            "- [Latest key metrics](metrics/latest/key_metrics.json)",
            "- [Validation](metrics/latest/validation.json)",
            "- [Evidence index](evidence/evidence_index.json)",
            "- [Retrieval index](semantic/retrieval_index.json)",
            "",
        ]
    )
    return "\n".join(lines)


def write_market_root(output_root: Path, company_results: list[dict[str, Any]], selection: dict[str, Any], source_results_dir: Path) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "_meta").mkdir(exist_ok=True)
    (output_root / "derived").mkdir(exist_ok=True)
    (output_root / "_quarantine").mkdir(exist_ok=True)
    (output_root / "_trash").mkdir(exist_ok=True)
    companies = []
    reports = []
    issues = []
    latest = {}
    for result in company_results:
        company = result["company"]
        companies.append(
            {
                "market": "HK",
                "company_id": company.get("company_id"),
                "company_wiki_id": company.get("company_wiki_id"),
                "stock_code": company.get("stock_code"),
                "ticker": company.get("ticker"),
                "exchange": company.get("exchange"),
                "company_short_name": company.get("company_short_name"),
                "company_full_name": company.get("company_full_name"),
                "aliases": company.get("aliases") or [],
                "company_path": company.get("company_wiki_path"),
                "primary_report_id": company.get("primary_report_id"),
                "report_count": company.get("report_count"),
                "status": company.get("status"),
            }
        )
        for report in result.get("reports") or []:
            reports.append({**report, "market": "HK", "company_wiki_id": company.get("company_wiki_id"), "company_path": company.get("company_wiki_path"), "ticker": company.get("ticker"), "company_name": company.get("company_name")})
            if report.get("status") != "ready":
                issues.append({"company_wiki_id": company.get("company_wiki_id"), "report_id": report.get("report_id"), "status": report.get("status")})
        latest_path = output_root / "companies" / str(company.get("company_wiki_id")) / "metrics" / "latest" / "three_statements.json"
        latest[company.get("ticker")] = read_json(latest_path, {})
    companies.sort(key=lambda item: str(item.get("ticker") or ""))
    reports.sort(key=lambda item: (str(item.get("ticker") or ""), str(item.get("report_id") or "")))
    write_json(output_root / "_meta" / "company_catalog.json", {"schema_version": "hk_company_catalog_v1", "market": "HK", "generated_at": now_iso(), "company_count": len(companies), "companies": companies})
    write_json(output_root / "_meta" / "report_catalog.json", {"schema_version": "hk_report_catalog_v1", "market": "HK", "generated_at": now_iso(), "report_count": len(reports), "reports": reports})
    write_json(output_root / "_meta" / "market_profile.json", {"schema_version": "hk_market_profile_v1", "market": "HK", "source": "HKEX PDF annual reports", "company_id_rule": "<5-digit-hkex-code>-<company-slug>", "report_id_rule": "<year>-<report-kind-slug>", "primary_statement_scope": "consolidated", "subsidiary_relation_policy": "not_structured_use_full_text_fallback", "accounting_standard": "HKFRS/IFRS", "generated_at": now_iso()})
    write_json(output_root / "_meta" / "ingestion_manifest.json", {"schema_version": "hk_ingestion_manifest_v1", "market": "HK", "generated_at": now_iso(), "source_results_dir": rel(source_results_dir), "company_count": len(companies), "report_count": len(reports), "selection": selection})
    write_json(output_root / "_meta" / "quality_summary.json", {"schema_version": "hk_quality_summary_v1", "market": "HK", "generated_at": now_iso(), "company_count": len(companies), "report_count": len(reports), "status_counts": dict(Counter(item.get("status") for item in reports)), "issue_count": len(issues)})
    write_json(output_root / "_meta" / "extraction_issues.json", {"schema_version": "hk_extraction_issues_v1", "market": "HK", "generated_at": now_iso(), "issues": issues, "selection": selection})
    write_json(output_root / "derived" / "three_statements_latest.json", latest)
    guide = hk_agent_guide()
    write_text(output_root / "_meta" / "AGENT_GUIDE.md", guide)
    write_text(output_root / "AGENTS.md", guide)
    write_text(output_root / "README.md", hk_readme(len(companies), len(reports)))


def hk_agent_guide() -> str:
    return """# HK Wiki Agent Guide

This market wiki follows the A-share company workspace contract with HK-specific identity and evidence rules.

Default routing:

1. Read `_meta/company_catalog.json` to resolve the HKEX ticker or company alias.
2. Read `companies/<company_wiki_id>/company.json` and choose `primary_report_id` unless the user specified a year/report.
3. For financial statement values, read `metrics/reports/<report_id>/three_statements.json`, then `key_metrics.json` and `validation.json`.
4. Use `evidence/evidence_index.json` for source `task_id`, `pdf_page_number`, `table_index`, and `md_line`.
5. Use `reports/<report_id>/report.md` and `document_full.json` only for full-text fallback, notes, subsidiaries, segments, and cross-checks.

HK-specific rules:

- Primary financial extraction uses consolidated statements only.
- Parent-company, subsidiary, non-controlling-interest, and segment tables are not primary metric sources.
- Subsidiary/segment queries use full-text fallback through `report.md`, `document_full.json`, and table evidence.
- Financial warnings are preserved in `validation.json`; they do not replace source evidence.
"""


def hk_readme(company_count: int, report_count: int) -> str:
    return f"""# HK Company Wiki

This directory is generated from standardized PDF parser results for HKEX PDF reports.

- Companies: `{company_count}`
- Reports: `{report_count}`
- Primary source: `data/pdf-parser/results`
- Main agent entrypoints: `_meta/company_catalog.json`, company `company.json`, `metrics/reports/<report_id>/three_statements.json`, `evidence/evidence_index.json`.

The HK Wiki keeps consolidated financial statements as the primary structured metric layer. Subsidiary, parent-only, and segment details are available through full-text/table fallback and are not promoted into primary metrics.
"""


def build_plan(
    results_dir: Path,
    *,
    downloads_root: Path = DEFAULT_DOWNLOADS_ROOT,
    task_id: str = "",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = []
    for result_dir in sorted(results_dir.iterdir()):
        if not result_dir.is_dir():
            continue
        if task_id and result_dir.name != task_id:
            continue
        row = inspect_hk_result(result_dir, downloads_root=downloads_root)
        if row:
            rows.append(row)
    return select_active(rows)


def _apply_safety_errors(args: argparse.Namespace, output_root: Path) -> list[str]:
    if not bool(args.apply):
        return []
    errors: list[str] = []
    if not bool(args.require_canonical_identity):
        errors.append("apply_requires_canonical_identity")
    if bool(getattr(args, "operational_update", False)):
        return errors
    production_root = DEFAULT_OUTPUT_ROOT.resolve()
    if output_root == production_root or output_root.is_relative_to(production_root):
        errors.append("apply_requires_independent_staging_output")
    if output_root.exists() and (not output_root.is_dir() or any(output_root.iterdir())):
        errors.append("staging_output_must_be_new_or_empty")
    return errors


def validate_staging_packages(output_root: Path) -> dict[str, Any]:
    results = []
    unit_currency_mismatches = []
    quality_gate_blocks = []
    quality_gate_decisions: Counter[str] = Counter()
    for manifest_path in sorted(output_root.glob("companies/*/reports/*/manifest.json")):
        package_dir = manifest_path.parent
        validation = validate_evidence_package(package_dir)
        quality_gates = build_quality_gates(package_dir)
        canonical_decision = str(quality_gates.get("canonical_decision") or quality_gates.get("decision") or "")
        quality_gate_decisions[canonical_decision] += 1
        if canonical_decision == "block":
            quality_gate_blocks.append(
                {
                    "package_path": rel(package_dir),
                    "hard_gate_rule_ids": quality_gates.get("hard_gate_rule_ids") or [],
                    "block_reasons": quality_gates.get("block_reasons") or [],
                    "missing_required_statements": quality_gates.get("missing_required_statements") or [],
                }
            )
        financial_data = read_json(package_dir / "metrics" / "financial_data.json", {})
        mismatches = package_unit_currency_mismatches(financial_data)
        if mismatches:
            unit_currency_mismatches.append(
                {
                    "package_path": rel(package_dir),
                    "count": len(mismatches),
                    "examples": mismatches[:20],
                }
            )
        results.append(
            {
                "package_path": rel(package_dir),
                "ok": validation.ok,
                "errors": validation.errors,
                "warnings": validation.warnings,
            }
        )
    failed = [result for result in results if not result["ok"]]
    return {
        "package_count": len(results),
        "passed_count": len(results) - len(failed),
        "failed_count": len(failed),
        "unit_currency_mismatch_count": sum(item["count"] for item in unit_currency_mismatches),
        "unit_currency_mismatch_packages": unit_currency_mismatches,
        "quality_gate_decisions": dict(sorted(quality_gate_decisions.items())),
        "quality_gate_block_count": len(quality_gate_blocks),
        "quality_gate_blocks": quality_gate_blocks,
        "failures": failed,
        "passed": bool(results) and not failed and not unit_currency_mismatches and not quality_gate_blocks,
    }


def audit_existing_staging(output_root: Path) -> dict[str, Any]:
    output_root = output_root.resolve()
    production_root = DEFAULT_OUTPUT_ROOT.resolve()
    if output_root == production_root or output_root.is_relative_to(production_root):
        return {
            "schema_version": "hk_wiki_rebuild_report_v1",
            "market": "HK",
            "apply": False,
            "audit_existing": True,
            "output_root": rel(output_root),
            "blocked": True,
            "blocking_reason": "audit_existing_requires_independent_staging_output",
        }
    if not output_root.is_dir():
        return {
            "schema_version": "hk_wiki_rebuild_report_v1",
            "market": "HK",
            "apply": False,
            "audit_existing": True,
            "output_root": rel(output_root),
            "blocked": True,
            "blocking_reason": "staging_output_missing",
        }

    manifest_paths = sorted(output_root.glob("companies/*/reports/*/manifest.json"))
    identity_issues: list[dict[str, Any]] = []
    profile_versions: Counter[str] = Counter()
    company_ids: set[str] = set()
    for manifest_path in manifest_paths:
        manifest = read_json(manifest_path, {})
        manifest = manifest if isinstance(manifest, dict) else {}
        source_manifest = (
            manifest.get("source_manifest")
            if isinstance(manifest.get("source_manifest"), dict)
            else {}
        )
        missing_fields = [
            field
            for field, value in (
                ("company_id", manifest.get("company_id")),
                ("filing_id", manifest.get("filing_id")),
                ("parse_run_id", manifest.get("parse_run_id")),
                ("accession_number", manifest.get("accession_number")),
                ("source_url", manifest.get("source_url")),
                ("source_manifest.content_sha256", source_manifest.get("content_sha256")),
            )
            if not value
        ]
        if missing_fields:
            identity_issues.append(
                {
                    "package_path": rel(manifest_path.parent),
                    "missing_fields": missing_fields,
                }
            )
        company_id = str(manifest.get("company_id") or "").strip()
        if company_id:
            company_ids.add(company_id)
        financial_checks = read_json(manifest_path.parent / "metrics" / "financial_checks.json", {})
        profile_version = str(financial_checks.get("profile_rule_version") or "missing")
        profile_versions[profile_version] += 1

    package_contract = validate_staging_packages(output_root)
    blocked = bool(identity_issues) or not package_contract["passed"]
    return {
        "schema_version": "hk_wiki_rebuild_report_v1",
        "market": "HK",
        "apply": False,
        "audit_existing": True,
        "output_root": rel(output_root),
        "candidate_report_count": len(manifest_paths),
        "company_count": len(company_ids),
        "written_company_count": len(company_ids),
        "written_report_count": len(manifest_paths),
        "canonical_identity": {
            "required": True,
            "resolved_reports": len(manifest_paths) - len(identity_issues),
            "unresolved_reports": len(identity_issues),
            "issues": identity_issues,
        },
        "financial_artifacts": {
            "required_profile_version": HK_FINANCIAL_PROFILE_VERSION,
            "profile_versions": dict(sorted(profile_versions.items())),
        },
        "package_contract": package_contract,
        "blocked": blocked,
        "blocking_reason": "existing_staging_audit_failed" if blocked else None,
        "read_only": True,
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    results_dir = args.results_dir.resolve()
    output_root = args.output_root.resolve()
    downloads_root = args.downloads_root.resolve()
    safety_errors = _apply_safety_errors(args, output_root)
    if safety_errors:
        return {
            "schema_version": "hk_wiki_rebuild_report_v1",
            "market": "HK",
            "apply": bool(args.apply),
            "output_root": rel(output_root),
            "blocked": True,
            "blocking_reason": "unsafe_hk_wiki_apply",
            "safety_errors": safety_errors,
        }
    active, selection = build_plan(
        results_dir,
        downloads_root=downloads_root,
        task_id=str(getattr(args, "task_id", "") or "").strip(),
    )
    if args.limit:
        active = active[: args.limit]
    for row in active:
        canonical_identity = row.get("canonical_identity")
        if isinstance(canonical_identity, dict):
            row.update(canonical_identity)
            row.update(
                {
                    "accession_number": row["sidecar"].get("accession_number"),
                    "source_tier": "official_regulator",
                    "source_verification_status": "official_verified",
                    "official_source_verified": True,
                    "regulator_host_verified": True,
                }
            )
            row["source_metadata"] = {
                **row["source_metadata"],
                "accession_number": row["sidecar"].get("accession_number"),
                "source_url": canonical_identity.get("source_url"),
                "source_sha256": canonical_identity.get("source_sha256"),
            }
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in active:
        grouped[row["company_wiki_id"]].append(row)
    plan = {
        "schema_version": "hk_wiki_rebuild_report_v1",
        "market": "HK",
        "apply": bool(args.apply),
        "source_results_dir": rel(results_dir),
        "downloads_root": rel(downloads_root),
        "output_root": rel(output_root),
        "candidate_report_count": len(active),
        "company_count": len(grouped),
        "selection": selection,
        "canonical_identity": {
            "required": bool(args.require_canonical_identity),
            "resolved_reports": sum(1 for row in active if row.get("canonical_identity")),
            "unresolved_reports": sum(1 for row in active if not row.get("canonical_identity")),
            "issues": [
                {
                    "task_id": row["task_id"],
                    "ticker": row["ticker"],
                    "report_id": row["report_id"],
                    "sidecar_status": row["sidecar"].get("status"),
                    "warnings": [warning for warning in row["warnings"] if warning.startswith("hkex_sidecar_")],
                }
                for row in active
                if not row.get("canonical_identity")
            ],
            "period_corrections": [
                {
                    "task_id": row["task_id"],
                    "ticker": row["ticker"],
                    "declared_period_end": row.get("declared_period_end"),
                    "period_end": row.get("period_end"),
                    "sidecar_period_end": row["sidecar"].get("period_end"),
                    "common_statement_periods": row["reporting_period_evidence"].get("common_periods") or [],
                }
                for row in active
                if row.get("declared_period_end") != row.get("period_end")
            ],
        },
        "financial_artifacts": {
            "required_profile_version": HK_FINANCIAL_PROFILE_VERSION,
            "sources": dict(sorted(Counter(row.get("financial_artifact_source") for row in active).items())),
        },
        "companies": [
            {
                "company_wiki_id": key,
                "ticker": rows[0]["ticker"],
                "company_name": rows[0]["company_name"],
                "reports": [row["report_id"] for row in rows],
            }
            for key, rows in sorted(grouped.items())
        ],
    }
    if args.require_canonical_identity and plan["canonical_identity"]["unresolved_reports"]:
        plan["blocked"] = True
        plan["blocking_reason"] = "canonical_hk_identity_incomplete"
        if args.apply:
            raise RuntimeError("canonical HK identity is incomplete; refusing --apply")
        return plan
    if not args.apply:
        return plan
    company_results = [write_company(rows, output_root) for _, rows in sorted(grouped.items())]
    write_market_root(output_root, company_results, selection, results_dir)
    plan["written_company_count"] = len(company_results)
    plan["written_report_count"] = sum(len(item.get("reports") or []) for item in company_results)
    plan["package_contract"] = validate_staging_packages(output_root)
    if not plan["package_contract"]["passed"]:
        plan["blocked"] = True
        plan["blocking_reason"] = "staging_package_contract_failed"
    return plan


def main() -> int:
    started_at = time.monotonic()
    parser = argparse.ArgumentParser(description="Ingest HK PDF parser results into an A-share-aligned company Wiki workspace.")
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS_DIR)
    parser.add_argument("--task-id", default="", help="Only ingest the selected parser task directory.")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--downloads-root", type=Path, default=DEFAULT_DOWNLOADS_ROOT)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--require-canonical-identity",
        action="store_true",
        help="Fail closed unless every selected report resolves to one HKEX accession and source hash.",
    )
    parser.add_argument("--apply", action="store_true", help="Write files. Omit for dry-run.")
    parser.add_argument(
        "--operational-update",
        action="store_true",
        help="Allow the API workflow to update its configured Wiki root; canonical identity remains required.",
    )
    parser.add_argument(
        "--audit-existing",
        action="store_true",
        help="Read and validate an existing independent staging root without rebuilding or writing it.",
    )
    parser.add_argument("--json-output", type=Path, default=None)
    args = parser.parse_args()
    if args.audit_existing and args.apply:
        parser.error("--audit-existing cannot be combined with --apply")
    payload = audit_existing_staging(args.output_root) if args.audit_existing else run(args)
    artifacts = [Path(__file__).resolve()]
    if not args.audit_existing:
        artifacts.extend(sorted(args.results_dir.resolve().glob("*/artifact_manifest.json")))
    if args.apply or args.audit_existing:
        artifacts.extend(sorted(args.output_root.resolve().rglob("manifest.json")))
        artifacts.extend(sorted(args.output_root.resolve().rglob("metrics/financial_data.json")))
        artifacts.extend(sorted(args.output_root.resolve().rglob("metrics/financial_checks.json")))
        artifacts.extend(sorted(args.output_root.resolve().glob("_meta/*.json")))
    failures = []
    if payload.get("blocked"):
        failures.append(
            {
                "code": str(payload.get("blocking_reason") or "hk_wiki_rebuild_blocked"),
                "count": len(payload.get("safety_errors") or []) or 1,
            }
        )
    payload = attach_evidence_metadata(
        payload,
        repo_root=REPO_ROOT,
        task_id="T10",
        environment_profile=(
            "local-hk-existing-staging-read-only"
            if args.audit_existing
            else "local-hk-isolated-staging-build"
            if args.apply
            else "local-hk-wiki-dry-run"
        ),
        command=(
            "python scripts/wiki/market_wikiset/ingest_hk_pdf_wiki.py "
            "--results-dir <configured-path> --downloads-root <configured-path> "
            "--output-root <configured-path> "
            + ("--require-canonical-identity " if args.require_canonical_identity else "")
            + ("--audit-existing " if args.audit_existing else "")
            + ("--apply " if args.apply else "")
            + "--json-output <artifact.json>"
        ),
        result="fail" if payload.get("blocked") else "pass",
        failures=failures,
        started_at=started_at,
        artifacts=artifacts,
    )
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    print(text)
    if args.json_output:
        write_json(args.json_output, payload)
    return 1 if payload.get("blocked") else 0


if __name__ == "__main__":
    raise SystemExit(main())
