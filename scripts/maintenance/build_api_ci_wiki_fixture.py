#!/usr/bin/env python3
"""Extract the public-disclosure slices required by hermetic API tests."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE_WIKI = REPO_ROOT / "data" / "wiki"
DEFAULT_OUTPUT = REPO_ROOT / "datasets" / "fixtures" / "api_ci" / "wiki"
KEYWORDS = (
    "商誉",
    "goodwill",
    "のれん",
    "员工",
    "职工",
    "人员结构",
    "专业构成",
    "教育程度",
    "personnel expenses",
    "employees",
    "营业收入",
    "经营活动产生的现金流量净额",
    "资产负债表",
    "现金流量表",
    "市场占有率",
    "前十名普通股股东",
)


@dataclass(frozen=True)
class CompanySpec:
    source_relative: str
    output_relative: str
    table_indexes: frozenset[int]
    lines: frozenset[int]
    pages: frozenset[int]
    report_ids: tuple[str, ...] = ("2025-annual",)
    legacy: bool = False
    include_document_text: bool = True


SPECS = (
    CompanySpec("companies/600104-上汽集团", "companies/600104-上汽集团", frozenset({84, 86, 88, 103, 105, 165, 166}), frozenset({184, 1840, 1850, 1904, 4186, 4196}), frozenset({65, 70, 72, 137})),
    CompanySpec("companies/000333-美的集团", "companies/000333-美的集团", frozenset({28, 39, 57, 89, 90, 163, 179}), frozenset({1117, 2497, 2508, 4325, 4518}), frozenset({57, 77, 132, 133, 206, 214})),
    CompanySpec("companies/601229-上海银行", "companies/601229-上海银行", frozenset({90, 135}), frozenset({2428}), frozenset({134, 135})),
    CompanySpec("companies/600143-金发科技", "companies/600143-金发科技", frozenset({26, 47}), frozenset({1567}), frozenset({29, 68})),
    CompanySpec("companies/300383-光环新网", "companies/300383-光环新网", frozenset({11, 21, 22, 26}), frozenset({768}), frozenset({24, 34, 45, 47})),
    CompanySpec("companies/600309-万华化学", "companies/600309-万华化学", frozenset({52, 83, 145}), frozenset(), frozenset({48, 83, 157})),
    CompanySpec("companies/000625-长安汽车", "companies/000625-长安汽车", frozenset({101}), frozenset(), frozenset({128})),
    CompanySpec("companies/601211-国泰海通", "companies/601211-国泰海通", frozenset(), frozenset(), frozenset()),
    CompanySpec("jp/companies/7203-Toyota-Motor-Corporation", "jp/companies/7203-Toyota-Motor-Corporation", frozenset(), frozenset(), frozenset({137}), include_document_text=False),
    CompanySpec("companies/GENBASF-BASF", "companies/GENBASF-BASF", frozenset({70, 145, 260, 261}), frozenset({2437, 4331, 4634, 6434}), frozenset({107, 157, 227, 302, 412}), legacy=True),
)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _text_matches(value: Any) -> bool:
    text = json.dumps(value, ensure_ascii=False).lower()
    return any(keyword.lower() in text for keyword in KEYWORDS)


def _locator_matches(value: Any, spec: CompanySpec) -> bool:
    if not isinstance(value, dict):
        return False
    for key in ("table_index", "source_table_index"):
        if value.get(key) in spec.table_indexes:
            return True
    for key in ("line", "md_line", "source_line"):
        if value.get(key) in spec.lines:
            return True
    for key in ("pdf_page", "pdf_page_number", "page_number", "source_page"):
        if value.get(key) in spec.pages:
            return True
    return False


def _record_matches(value: Any, spec: CompanySpec) -> bool:
    if _locator_matches(value, spec) or _text_matches(value):
        return True
    if isinstance(value, dict):
        return any(_record_matches(item, spec) for item in value.values() if isinstance(item, (dict, list)))
    if isinstance(value, list):
        return any(_record_matches(item, spec) for item in value if isinstance(item, (dict, list)))
    return False


def _copy_json(source: Path, destination: Path) -> None:
    if source.is_file():
        write_json(destination, read_json(source))


def _portable(value: Any) -> Any:
    if isinstance(value, dict):
        blocked = {
            "path",
            "source_path",
            "source_image_path",
            "source_pdf_path",
            "source_pdf_path_legacy",
            "upload_pdf_path",
            "result_dir",
            "document_full_path",
            "pdf2md_result_dir",
            "pdf2md_pdf_pages_dir",
            "parser_result_dir",
        }
        return {
            key: _portable(item)
            for key, item in value.items()
            if key not in blocked
        }
    if isinstance(value, list):
        return [_portable(item) for item in value]
    if isinstance(value, str) and value.startswith(("/home/", "/tmp/", "/var/")):
        return Path(value).name
    return value


def _filtered_wrapper(source: Path, destination: Path, list_keys: tuple[str, ...], spec: CompanySpec) -> None:
    if not source.is_file():
        return
    payload = read_json(source)
    if not isinstance(payload, dict):
        return
    output = _portable(dict(payload))
    for key in list_keys:
        rows = payload.get(key)
        if isinstance(rows, list):
            output[key] = [_portable(row) for row in rows if _record_matches(row, spec)]
    write_json(destination, output)


def _filtered_report(source: Path, destination: Path, spec: CompanySpec) -> None:
    if not source.is_file():
        return
    payload = read_json(source)
    if not isinstance(payload, dict):
        return
    output = {
        key: _portable(payload[key])
        for key in ("schema_version", "identity", "source", "status", "quality_summary", "tables")
        if key in payload
    }
    tables = payload.get("tables")
    if isinstance(tables, list):
        output["tables"] = [
            {
                key: _portable(table[key])
                for key in (
                    "table_index",
                    "line",
                    "heading",
                    "unit",
                    "pdf_page_index",
                    "pdf_page_number",
                    "pdf_page_source",
                    "preview",
                    "table_type",
                    "report_year",
                    "fact_year",
                    "financial_scope",
                    "statement_scope",
                    "scope",
                    "column_scopes",
                )
                if key in table
            }
            for table in tables
            if isinstance(table, dict) and table.get("table_index") in spec.table_indexes
        ]
    write_json(destination, output)


def _sparse_markdown(source: Path, destination: Path, spec: CompanySpec) -> None:
    if not source.is_file():
        return
    lines = source.read_text(encoding="utf-8", errors="replace").splitlines()
    keep: set[int] = set()
    for line_number in spec.lines:
        center = line_number - 1
        keep.update(range(max(0, center - 12), min(len(lines), center + 141)))
    for index, line in enumerate(lines):
        if any(keyword.lower() in line.lower() for keyword in KEYWORDS):
            keep.update(range(max(0, index - 8), min(len(lines), index + 16)))
    for index in tuple(keep):
        for prior in range(index, max(-1, index - 250), -1):
            if "[PDF_PAGE:" in lines[prior]:
                keep.add(prior)
                break
        # Scope inference uses the nearest financial-note heading. Keep that
        # heading with each selected window so the fixture preserves the same
        # consolidated/parent-company contract as the source Wiki.
        for prior in range(index, max(-1, index - 250), -1):
            if lines[prior].lstrip().startswith("#"):
                keep.add(prior)
                break
        for prior in range(index, -1, -1):
            heading = lines[prior].strip()
            if (
                heading.startswith("#")
                and "财务报表" in heading
                and any(term in heading for term in ("附注", "注释"))
            ):
                keep.add(prior)
                break
    output = [line.rstrip() if index in keep else "" for index, line in enumerate(lines)]
    while output and not output[-1]:
        output.pop()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(output) + "\n", encoding="utf-8")


def _content_item_matches(item: Any, spec: CompanySpec) -> bool:
    if not isinstance(item, dict):
        return False
    page_index = item.get("page_idx")
    if not spec.include_document_text:
        return (
            item.get("type") == "page_number"
            and isinstance(page_index, int)
            and page_index + 1 in spec.pages
        )
    if isinstance(page_index, int) and page_index + 1 in spec.pages:
        return True
    return _record_matches(item, spec)


def _filtered_document_full(source: Path, destination: Path, spec: CompanySpec) -> None:
    if not source.is_file():
        return
    payload = read_json(source)
    if not isinstance(payload, dict):
        return
    keep_keys = (
        "schema_version",
        "task",
        "financial_data",
    )
    output = {_key: _portable(payload[_key]) for _key in keep_keys if _key in payload}
    for key in ("content_list", "content_list_enhanced"):
        rows = payload.get(key)
        if isinstance(rows, list):
            selected = [row for row in rows if _content_item_matches(row, spec)]
            output[key] = [_compact_content_item(row) for row in selected]
        elif isinstance(rows, dict):
            output[key] = {
                nested_key: (
                    [_compact_enhanced_item(row, spec) for row in nested_value if _content_item_matches(row, spec)]
                    if isinstance(nested_value, list)
                    else nested_value
                )
                for nested_key, nested_value in rows.items()
            }
    write_json(destination, output)


def _compact_content_item(item: Any) -> Any:
    if not isinstance(item, dict):
        return item
    output = {
        key: _portable(item[key])
        for key in ("type", "page_idx", "text", "table_caption", "table_footnote", "bbox")
        if key in item
    }
    if item.get("table_body"):
        body = str(item["table_body"])
        output["table_body"] = body[:20000]
    return output


def _compact_enhanced_item(item: Any, spec: CompanySpec) -> Any:
    if not isinstance(item, dict):
        return item
    output = {
        key: _portable(item[key])
        for key in (
            "table_index",
            "line",
            "heading",
            "name",
            "title",
            "preview",
            "pdf_page_index",
            "pdf_page_number",
            "printed_page_number",
            "page_number",
            "page_idx",
            "text",
            "bbox",
        )
        if key in item
    }
    return output


def _extract_pdf_company(source: Path, destination: Path, spec: CompanySpec) -> None:
    _copy_json(source / "company.json", destination / "company.json")
    _copy_json(source / "metrics" / "three_statements.json", destination / "metrics" / "three_statements.json")
    _copy_json(source / "semantic" / "document_links.json", destination / "semantic" / "document_links.json")
    _filtered_wrapper(source / "evidence" / "pdf_refs.json", destination / "evidence" / "pdf_refs.json", ("refs",), spec)
    _filtered_wrapper(source / "evidence" / "evidence_index.json", destination / "evidence" / "evidence_index.json", ("evidence", "items", "refs"), spec)
    for report_id in spec.report_ids:
        source_report = source / "reports" / report_id
        destination_report = destination / "reports" / report_id
        report_tables = read_json(source_report / "report.json") if (source_report / "report.json").is_file() else {}
        table_lines = {
            int(table["line"])
            for table in (report_tables.get("tables") or [])
            if isinstance(table, dict)
            and table.get("table_index") in spec.table_indexes
            and isinstance(table.get("line"), int)
        }
        report_spec = replace(spec, lines=spec.lines | table_lines)
        _copy_json(
            source / "metrics" / "reports" / report_id / "three_statements.json",
            destination / "metrics" / "reports" / report_id / "three_statements.json",
        )
        _filtered_report(source_report / "report.json", destination_report / "report.json", report_spec)
        _sparse_markdown(source_report / "report.md", destination_report / "report.md", report_spec)
        _filtered_document_full(source_report / "document_full.json", destination_report / "document_full.json", report_spec)


def _extract_us_company(source_wiki: Path, output: Path) -> None:
    source = source_wiki / "us" / "companies" / "NVDA-NVIDIA-CORP"
    destination = output / "us" / "companies" / "NVDA-NVIDIA-CORP"
    _copy_json(source / "company.json", destination / "company.json")
    filings = read_json(source / "filings.json")
    if isinstance(filings, dict):
        output_filings = {
            key: _portable(filings[key])
            for key in ("schema_version", "ticker", "count", "items")
            if key in filings
        }
        if isinstance(output_filings.get("items"), list):
            output_filings["items"] = [
                {
                    key: _portable(item[key])
                    for key in (
                        "market", "company_id", "filing_id", "report_id", "parse_run_id",
                        "ticker", "company_name", "form", "report_type", "fiscal_year",
                        "period_end", "filing_date", "published_at", "source_url",
                        "retrieval_status", "wiki_ready", "full_document_status",
                    )
                    if key in item
                }
                for item in output_filings["items"]
                if isinstance(item, dict)
            ]
        write_json(destination / "filings.json", output_filings)
    _copy_json(source / "semantic" / "document_links.json", destination / "semantic" / "document_links.json")
    report_id = "2026-10-K-0001045810-26-000021"
    source_report = source / "reports" / report_id
    destination_report = destination / "reports" / report_id
    for relative in (
        "manifest.json",
        "metrics/financial_data.json",
        "metrics/normalized_metrics.json",
    ):
        _copy_json(source_report / relative, destination_report / relative)
    _copy_json(
        source_report / "metrics" / "financial_data.json",
        destination / "metrics" / "latest" / "financial_data.json",
    )


def build_fixture(source_wiki: Path, output: Path, legacy_wiki: Path | None) -> dict[str, Any]:
    if output.exists():
        shutil.rmtree(output)
    sources: list[dict[str, Any]] = []
    for spec in SPECS:
        root = legacy_wiki if spec.legacy else source_wiki
        if root is None:
            raise ValueError(f"--legacy-wiki-root is required for {spec.source_relative}")
        source = root / spec.source_relative
        if not source.is_dir():
            raise FileNotFoundError(source)
        destination = output / spec.output_relative
        _extract_pdf_company(source, destination, spec)
        company_path = destination / "company.json"
        sources.append(
            {
                "company": spec.output_relative,
                "source_company_json_sha256": sha256_file(source / "company.json"),
                "fixture_company_json_sha256": sha256_file(company_path),
            }
        )
    _extract_us_company(source_wiki, output)
    sources.append({"company": "us/companies/NVDA-NVIDIA-CORP"})
    _write_catalogs(output)
    manifest = {
        "schema_version": "siq_api_ci_wiki_fixture_v1",
        "scope": "public_disclosure_test_slices",
        "contains_original_documents": False,
        "contains_runtime_databases": False,
        "companies": sources,
    }
    write_json(output / "fixture_manifest.json", manifest)
    return {"companies": len(sources), "output": str(output)}


def _catalog_entry(company_path: Path, *, market: str | None = None) -> dict[str, Any]:
    company = read_json(company_path / "company.json") or {}
    reports = company.get("reports") if isinstance(company.get("reports"), list) else []
    metadata = reports[0].get("source_filename_metadata") if reports and isinstance(reports[0], dict) else {}
    metadata = metadata if isinstance(metadata, dict) else {}
    stock_code = company.get("stock_code") or company.get("ticker") or metadata.get("stock_code") or metadata.get("raw_ticker")
    raw_company_id = str(company.get("company_id") or "")
    normalized_market = market or str(company.get("market") or metadata.get("market") or "").upper()
    if normalized_market == "JP" and stock_code:
        company_id = f"JP:{stock_code}"
    elif normalized_market == "US" and stock_code and raw_company_id.startswith("US:"):
        company_id = raw_company_id
    else:
        company_id = raw_company_id or (f"{normalized_market}:{stock_code}" if normalized_market and stock_code else company_path.name)
    aliases = list(company.get("aliases") or [])
    for value in (
        company.get("company_short_name"),
        company.get("company_full_name"),
        company.get("company_name"),
        metadata.get("company_short_name"),
    ):
        if value and value not in aliases:
            aliases.append(value)
    return {
        "company_id": company_id,
        "company_wiki_id": company.get("company_wiki_id") or company_path.name,
        "company_path": company_path.relative_to(company_path.parents[1]).as_posix(),
        "stock_code": stock_code,
        "ticker": company.get("ticker") or stock_code,
        "company_short_name": company.get("company_short_name") or metadata.get("company_short_name"),
        "company_name": company.get("company_name") or company.get("company_full_name"),
        "aliases": aliases,
        "market": normalized_market,
        "status": "ready",
        "reports": len(reports),
    }


def _write_catalogs(output: Path) -> None:
    root_companies = output / "companies"
    root_entries = []
    if root_companies.is_dir():
        for company_dir in sorted(root_companies.iterdir()):
            if (company_dir / "company.json").is_file():
                root_entries.append(_catalog_entry(company_dir))
    write_json(
        output / "_meta" / "company_catalog.json",
        {"schema_version": "siq_company_catalog_v1", "company_count": len(root_entries), "companies": root_entries},
    )
    for market in ("us", "jp"):
        market_root = output / market / "companies"
        entries = []
        if market_root.is_dir():
            for company_dir in sorted(market_root.iterdir()):
                if (company_dir / "company.json").is_file():
                    entries.append(_catalog_entry(company_dir, market=market.upper()))
        write_json(
            output / market / "_meta" / "company_catalog.json",
            {"schema_version": "siq_company_catalog_v1", "company_count": len(entries), "companies": entries},
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-wiki-root", type=Path, default=DEFAULT_SOURCE_WIKI)
    parser.add_argument("--legacy-wiki-root", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = build_fixture(
        args.source_wiki_root.resolve(),
        args.output.resolve(),
        args.legacy_wiki_root.resolve() if args.legacy_wiki_root else None,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
