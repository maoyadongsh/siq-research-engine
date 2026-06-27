#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DOWNLOADS_ROOT = REPO_ROOT / "data" / "market-report-finder" / "downloads" / "HK"
DEFAULT_RESULTS_ROOT = REPO_ROOT / "data" / "pdf-parser" / "results"
DEFAULT_OUTPUT = REPO_ROOT / "eval_datasets" / "market_ingestion_cases" / "hk_50_cases.json"

BANK_CODES = {"00939", "01288", "01398", "03968", "03988", "02388", "00005"}
INSURANCE_CODES = {"01299", "02628", "02318", "02328"}
PROPERTY_NAMES = ("PROPERTY", "PROPERTIES", "LAND", "地产", "地產", "置地")
ENERGY_NAMES = ("PETRO", "CNOOC", "SINOPEC", "SHENHUA", "YANKUANG", "ZIJIN", "COPPER", "ENERGY")
INTERNET_NAMES = ("TENCENT", "BABA", "MEITUAN", "JD-", "KUAISHOU", "NTES", "BIDU", "XIAOMI", "LI-AUTO", "XPENG")


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return {} if default is None else default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def filename_index(results_root: Path) -> dict[str, Path]:
    index: dict[str, Path] = {}
    for document_full in sorted(results_root.glob("*/document_full.json")):
        payload = read_json(document_full, {})
        task = payload.get("task") if isinstance(payload, dict) else {}
        filename = str(task.get("filename") or "") if isinstance(task, dict) else ""
        if filename:
            index[filename] = document_full.parent
            index[Path(filename).stem] = document_full.parent
        summary = read_json(document_full.parent / "result_payload_summary.json", {})
        result_file = str(summary.get("result_file") or "") if isinstance(summary, dict) else ""
        if result_file:
            index[result_file] = document_full.parent
            index[Path(result_file).stem] = document_full.parent
    return index


def find_parser_result(pdf_path: Path, index: dict[str, Path]) -> Path | None:
    for key in (pdf_path.name, pdf_path.stem):
        if key in index:
            return index[key]
    for key, parser_dir in index.items():
        if not key:
            continue
        if key in pdf_path.name or pdf_path.stem in key:
            return parser_dir
    return None


def metadata_for_pdf(pdf_path: Path) -> tuple[dict[str, Any], Path | None]:
    metadata_path = pdf_path.with_suffix(pdf_path.suffix + ".metadata.json")
    return read_json(metadata_path, {}), metadata_path if metadata_path.exists() else None


def infer_profile(ticker: str, company_name: str) -> str:
    name = company_name.upper()
    if ticker in BANK_CODES or "BANK" in name or "银行" in company_name or "銀行" in company_name:
        return "bank"
    if ticker in INSURANCE_CODES or "INSURANCE" in name or "LIFE" in name or "AIA" in name or "保险" in company_name or "保險" in company_name:
        return "insurance"
    if any(token in name for token in PROPERTY_NAMES):
        return "property"
    if any(token in name for token in ENERGY_NAMES):
        return "energy"
    if any(token in name for token in INTERNET_NAMES):
        return "internet_platform"
    return "general"


def expected_metrics(profile: str) -> list[str]:
    if profile == "bank":
        return ["net_profit", "parent_net_profit", "total_assets", "total_liabilities", "total_equity"]
    if profile == "insurance":
        return ["net_profit", "parent_net_profit", "total_assets", "total_liabilities", "total_equity"]
    return [
        "operating_revenue",
        "net_profit",
        "total_assets",
        "total_liabilities",
        "total_equity",
        "operating_cash_flow_net",
    ]


def case_from_pdf(pdf_path: Path, parser_dir: Path | None) -> tuple[dict[str, Any], list[str]]:
    metadata, metadata_path = metadata_for_pdf(pdf_path)
    candidate = metadata.get("candidate") if isinstance(metadata, dict) else {}
    if not isinstance(candidate, dict):
        candidate = {}
    parts = pdf_path.stem.split("_")
    ticker = str(candidate.get("ticker") or (parts[2] if len(parts) > 2 else "")).zfill(5)
    company_name = str(candidate.get("company_name") or (parts[0] if parts else pdf_path.stem))
    report_type = _report_type(candidate.get("report_type") or candidate.get("report_family") or pdf_path.parent.name)
    period_end = candidate.get("report_end") or candidate.get("period_end") or _filename_date(pdf_path.name)
    fiscal_year = _int_or_none(str(period_end or "")[:4]) or _int_or_none(candidate.get("year")) or _int_or_none(pdf_path.parent.parent.name)
    profile = infer_profile(ticker, company_name)
    warnings: list[str] = []
    if parser_dir is None:
        warnings.append("parser_result_missing")
    else:
        for required in ("document_full.json", "table_index.json"):
            if not (parser_dir / required).exists():
                warnings.append(f"{required}_missing")
    if metadata_path is None:
        warnings.append("metadata_json_missing")
    case = {
        "market": "HK",
        "ticker": ticker,
        "stock_code": ticker,
        "company_name": company_name,
        "report_type": report_type,
        "fiscal_year": fiscal_year,
        "period_end": period_end,
        "published_at": candidate.get("published_at"),
        "source_url": candidate.get("document_url") or candidate.get("source_url") or candidate.get("landing_url"),
        "pdf_path": str(pdf_path.relative_to(REPO_ROOT) if pdf_path.is_relative_to(REPO_ROOT) else pdf_path),
        "metadata_json": str(metadata_path.relative_to(REPO_ROOT) if metadata_path and metadata_path.is_relative_to(REPO_ROOT) else metadata_path) if metadata_path else None,
        "parser_result_dir": str(parser_dir.relative_to(REPO_ROOT) if parser_dir and parser_dir.is_relative_to(REPO_ROOT) else parser_dir) if parser_dir else None,
        "pdf_parser_task_id": parser_dir.name if parser_dir else None,
        "industry_profile": profile,
        "expected_metrics": expected_metrics(profile),
        "expected_evidence": True,
        "warnings": warnings,
    }
    return case, warnings


def discover_cases(downloads_root: Path, results_root: Path, *, limit: int = 50, ticker: str = "") -> list[dict[str, Any]]:
    index = filename_index(results_root)
    pdfs = sorted(downloads_root.rglob("*.pdf"))
    if ticker:
        token = ticker.upper()
        pdfs = [path for path in pdfs if token in path.name.upper() or f"_HK_{ticker.zfill(5)}_" in path.name]
    cases: list[dict[str, Any]] = []
    for pdf_path in pdfs:
        parser_dir = find_parser_result(pdf_path, index)
        case, _ = case_from_pdf(pdf_path, parser_dir)
        cases.append(case)
        if limit and len(cases) >= limit:
            break
    return cases


def _report_type(value: Any) -> str:
    text = str(value or "").lower()
    if any(token in text for token in ("interim", "semi", "中期", "半年")):
        return "semiannual"
    if any(token in text for token in ("quarter", "q1", "q2", "q3", "季度")):
        return "quarterly"
    return "annual"


def _filename_date(filename: str) -> str | None:
    match = re.search(r"(20\d{2}-\d{2}-\d{2})", filename)
    return match.group(1) if match else None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Discover HK downloaded PDFs that have PDF parser results and write a case manifest.")
    parser.add_argument("--downloads-root", type=Path, default=DEFAULT_DOWNLOADS_ROOT)
    parser.add_argument("--results-root", type=Path, default=DEFAULT_RESULTS_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--ticker", default="")
    parser.add_argument("--summary", type=Path, default=None)
    args = parser.parse_args()

    cases = discover_cases(args.downloads_root.resolve(), args.results_root.resolve(), limit=args.limit, ticker=args.ticker)
    output = args.output if args.output.is_absolute() else REPO_ROOT / args.output
    write_json(output, cases)
    summary = {
        "generated_at": now_iso(),
        "output": str(output),
        "cases": len(cases),
        "with_parser_result": sum(1 for item in cases if item.get("parser_result_dir")),
        "with_metadata": sum(1 for item in cases if item.get("metadata_json")),
        "warnings": sum(len(item.get("warnings") or []) for item in cases),
        "by_profile": {},
    }
    for item in cases:
        profile = item.get("industry_profile") or "unknown"
        summary["by_profile"][profile] = summary["by_profile"].get(profile, 0) + 1
    if args.summary:
        summary_path = args.summary if args.summary.is_absolute() else REPO_ROOT / args.summary
        write_json(summary_path, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
