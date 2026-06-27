#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from eu_pdf_evidence_lib import REPO_ROOT, infer_industry_profile, infer_metadata, sniff_document_format


DEFAULT_DOWNLOAD_ROOT = REPO_ROOT / "data" / "market-report-finder" / "downloads" / "EU"
DEFAULT_RESULTS_ROOT = REPO_ROOT / "data" / "pdf-parser" / "results"
DEFAULT_OUTPUT = REPO_ROOT / "eval_datasets" / "market_ingestion_cases" / "eu_15_pdf_cases.json"


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return {} if default is None else default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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


def find_parser_result(source_path: Path, index: dict[str, Path]) -> Path | None:
    for key in (source_path.name, source_path.stem):
        if key in index:
            return index[key]
    for key, parser_dir in index.items():
        if not key:
            continue
        if key in source_path.name or source_path.stem in key:
            return parser_dir
    return None


def metadata_path_for(source_path: Path) -> Path | None:
    candidate = source_path.with_suffix(source_path.suffix + ".metadata.json")
    return candidate if candidate.exists() else None


def case_from_source(source_path: Path, parser_dir: Path | None) -> dict[str, Any]:
    metadata_path = metadata_path_for(source_path)
    metadata = infer_metadata(source_path, metadata_path)
    profile = infer_industry_profile(metadata["ticker"], metadata["company_name"], str(metadata.get("title") or ""))
    warnings: list[str] = []
    if parser_dir is None and metadata["document_format"] == "pdf":
        warnings.append("parser_result_missing")
    elif parser_dir is not None:
        for required in ("document_full.json", "table_index.json"):
            if not (parser_dir / required).exists():
                warnings.append(f"{required}_missing")
    if metadata_path is None:
        warnings.append("metadata_json_missing")
    if metadata["document_format"] != "pdf":
        warnings.append(f"non_pdf_document_format:{metadata['document_format']}")
    return {
        "market": "EU",
        "country": metadata["country"],
        "ticker": metadata["ticker"],
        "company_name": metadata["company_name"],
        "report_type": metadata["report_type"],
        "fiscal_year": metadata["fiscal_year"],
        "period_end": metadata["period_end"],
        "published_at": metadata["published_at"],
        "document_format": metadata["document_format"],
        "source_pdf": _rel(source_path),
        "source_file": _rel(source_path),
        "metadata_json": _rel(metadata_path) if metadata_path else None,
        "parser_result_dir": _rel(parser_dir) if parser_dir else None,
        "pdf_parser_task_id": parser_dir.name if parser_dir else None,
        "industry_profile": profile,
        "source_url": metadata["source_url"],
        "source_tier": metadata["source_tier"],
        "expected_metrics": expected_metrics(profile),
        "expected_evidence": True,
        "warnings": warnings,
    }


def expected_metrics(profile: str) -> list[str]:
    if profile == "bank":
        return ["net_profit", "total_assets", "total_liabilities", "total_equity"]
    if profile == "insurance":
        return ["net_profit", "total_assets", "total_liabilities", "total_equity"]
    return ["operating_revenue", "net_profit", "total_assets", "total_liabilities", "total_equity", "operating_cash_flow_net"]


def discover_cases(
    download_root: Path,
    results_root: Path,
    *,
    limit: int = 15,
    country: str = "",
    ticker: str = "",
    document_format: str = "",
) -> list[dict[str, Any]]:
    index = filename_index(results_root)
    document_format_filter = {item.strip() for item in document_format.split(",") if item.strip()}
    files = [
        path
        for path in sorted(download_root.rglob("*"))
        if path.is_file()
        and not path.name.endswith(".metadata.json")
        and not path.name.startswith(".")
        and sniff_document_format(path) in {"pdf", "esef_zip", "ixbrl_xhtml", "html", "xml"}
    ]
    if document_format_filter:
        files = [path for path in files if sniff_document_format(path) in document_format_filter]
    if country:
        country_token = country.upper()
        files = [path for path in files if f"/{country_token}/" in f"/{path.as_posix()}".upper()]
    if ticker:
        token = ticker.upper()
        files = [path for path in files if token in path.name.upper() or f"_EU_{token}_" in path.name.upper()]
    cases: list[dict[str, Any]] = []
    for source_path in files:
        parser_dir = find_parser_result(source_path, index)
        cases.append(case_from_source(source_path, parser_dir))
        if limit and len(cases) >= limit:
            break
    return cases


def _rel(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def main() -> None:
    parser = argparse.ArgumentParser(description="Discover EU downloaded annual reports and match PDF parser results when available.")
    parser.add_argument("--download-root", "--downloads-root", dest="download_root", type=Path, default=DEFAULT_DOWNLOAD_ROOT)
    parser.add_argument("--results-root", type=Path, default=DEFAULT_RESULTS_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--limit", type=int, default=15)
    parser.add_argument("--country", default="")
    parser.add_argument("--ticker", default="")
    parser.add_argument("--document-format", default="", help="Comma-separated filter: pdf,esef_zip,ixbrl_xhtml,html,xml")
    parser.add_argument("--summary", type=Path, default=None)
    args = parser.parse_args()

    cases = discover_cases(
        args.download_root.resolve(),
        args.results_root.resolve(),
        limit=args.limit,
        country=args.country,
        ticker=args.ticker,
        document_format=args.document_format,
    )
    output = args.output if args.output.is_absolute() else REPO_ROOT / args.output
    write_json(output, cases)
    summary: dict[str, Any] = {
        "generated_at": now_iso(),
        "output": str(output),
        "cases": len(cases),
        "with_parser_result": sum(1 for item in cases if item.get("parser_result_dir")),
        "with_metadata": sum(1 for item in cases if item.get("metadata_json")),
        "warnings": sum(len(item.get("warnings") or []) for item in cases),
        "by_country": {},
        "by_document_format": {},
        "by_profile": {},
    }
    for item in cases:
        for key, field in (("by_country", "country"), ("by_document_format", "document_format"), ("by_profile", "industry_profile")):
            value = item.get(field) or "unknown"
            summary[key][value] = summary[key].get(value, 0) + 1
    if args.summary:
        summary_path = args.summary if args.summary.is_absolute() else REPO_ROOT / args.summary
        write_json(summary_path, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
