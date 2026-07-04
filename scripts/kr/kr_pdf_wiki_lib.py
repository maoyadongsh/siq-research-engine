from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CORE_STATEMENT_KEYWORDS = (
    "statement of financial position",
    "statement of profit or loss",
    "statement of comprehensive income",
    "statement of cash flows",
    "statement of changes in equity",
    "summary financial information",
    "연결 재무상태표",
    "연결 손익계산서",
    "연결 포괄손익계산서",
    "연결 현금흐름표",
    "연결 자본변동표",
    "요약재무정보",
)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize_kr_ticker(value: Any) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    if not digits:
        return "000000"
    return digits[-6:].zfill(6)


def _slug(value: str) -> str:
    parts = re.findall(r"[A-Za-z0-9가-힣]+", value or "Company")
    slug = "".join(part[:1].upper() + part[1:] for part in parts)
    return slug or "Company"


def kr_company_dir_name(ticker: str, company_name: str) -> str:
    return f"{normalize_kr_ticker(ticker)}-{_slug(company_name)}"


def _year_from_name(value: str) -> int | None:
    match = re.search(r"(20\d{2})", value)
    return int(match.group(1)) if match else None


def infer_kr_pdf_metadata(
    pdf_path: Path,
    parser_result_dir: Path,
    metadata_path: Path | None = None,
) -> dict[str, Any]:
    parser_manifest = _read_json(parser_result_dir / "manifest.json")
    metadata = dict(parser_manifest)
    if metadata_path:
        metadata.update(_read_json(metadata_path))

    task_id = str(metadata.get("task_id") or parser_result_dir.name)
    ticker = normalize_kr_ticker(metadata.get("ticker") or pdf_path.stem)
    company_name = str(metadata.get("company_name") or metadata.get("company") or pdf_path.stem)
    report_year = int(
        metadata.get("report_year")
        or metadata.get("fiscal_year")
        or _year_from_name(pdf_path.stem)
        or datetime.now(timezone.utc).year
    )
    report_type = str(metadata.get("report_type") or "annual").lower().replace(" ", "_")
    report_id = f"{report_year}-{report_type}_{task_id}"
    return {
        "market": "KR",
        "ticker": ticker,
        "company_name": company_name,
        "report_year": report_year,
        "report_type": report_type,
        "report_id": report_id,
        "pdf_parser_task_id": task_id,
        "source_pdf": str(pdf_path),
        "parser_result_dir": str(parser_result_dir),
    }


def _copy_if_exists(src: Path, dst: Path) -> None:
    if src.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def _iter_tables(parser_result_dir: Path) -> list[dict[str, Any]]:
    document = _read_json(parser_result_dir / "document_full.json")
    tables: list[dict[str, Any]] = []
    for page in document.get("pages", []):
        if not isinstance(page, dict):
            continue
        page_no = page.get("page") or page.get("page_number")
        for table in page.get("tables", []):
            if not isinstance(table, dict):
                continue
            item = dict(table)
            item["pdf_page_number"] = int(page_no) if page_no else None
            item["caption"] = str(item.get("caption") or item.get("title") or "")
            tables.append(item)
    if tables:
        return tables

    content = _read_json(parser_result_dir / "content_list_enhanced.json")
    for item in content.get("items", []):
        if not isinstance(item, dict) or item.get("type") != "table":
            continue
        page_idx = item.get("page_idx")
        tables.append(
            {
                "table_index": item.get("table_index") or len(tables) + 1,
                "caption": str(item.get("caption") or item.get("text") or ""),
                "pdf_page_number": int(page_idx) + 1 if page_idx is not None else None,
            }
        )
    if tables:
        return tables

    for item in content.get("tables", []):
        if not isinstance(item, dict):
            continue
        caption = item.get("caption") or item.get("heading") or item.get("title") or ""
        if not caption and isinstance(item.get("source_caption"), list):
            caption = " ".join(str(part) for part in item["source_caption"] if part)
        tables.append(
            {
                "table_index": item.get("table_index") or len(tables) + 1,
                "caption": str(caption or item.get("preview") or ""),
                "pdf_page_number": item.get("pdf_page_number"),
                "md_line": item.get("line"),
            }
        )
    if tables:
        return tables

    table_index_path = parser_result_dir / "table_index.json"
    if table_index_path.exists():
        payload = json.loads(table_index_path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            for item in payload:
                if not isinstance(item, dict):
                    continue
                caption = item.get("caption") or item.get("heading") or item.get("title") or ""
                if not caption and isinstance(item.get("source_caption"), list):
                    caption = " ".join(str(part) for part in item["source_caption"] if part)
                tables.append(
                    {
                        "table_index": item.get("table_index") or len(tables) + 1,
                        "caption": str(caption or item.get("preview") or ""),
                        "pdf_page_number": item.get("pdf_page_number"),
                        "md_line": item.get("line"),
                    }
                )
    return tables


def _line_for_caption(report_md: str, caption: str) -> int | None:
    caption_lower = caption.lower()
    for idx, line in enumerate(report_md.splitlines(), start=1):
        if caption_lower and caption_lower in line.lower():
            return idx
    return None


def _build_source_map(metadata: dict[str, Any], parser_result_dir: Path) -> dict[str, Any]:
    report_path = parser_result_dir / "report_complete.md"
    report_md = report_path.read_text(encoding="utf-8") if report_path.exists() else ""
    evidence = []
    for index, table in enumerate(_iter_tables(parser_result_dir), start=1):
        caption = str(table.get("caption") or "")
        evidence.append(
            {
                "evidence_id": f"KR-{metadata['ticker']}-{metadata['report_id']}-table-{index}",
                "market": "KR",
                "company_id": f"KR:{metadata['ticker']}",
                "ticker": metadata["ticker"],
                "report_id": metadata["report_id"],
                "filing_id": metadata["report_id"],
                "pdf_parser_task_id": metadata["pdf_parser_task_id"],
                "parser_result_dir": metadata["parser_result_dir"],
                "pdf_page_number": table.get("pdf_page_number"),
                "table_index": table.get("table_index") or index,
                "caption": caption,
                "md_path": "parser/report_complete.md",
                "wiki_path": "parser/report_complete.md",
                "md_line": table.get("md_line") or _line_for_caption(report_md, caption) or 1,
            }
        )
    return {"market": "KR", "report_id": metadata["report_id"], "evidence": evidence}


def _quality_report(metadata: dict[str, Any], source_map: dict[str, Any]) -> dict[str, Any]:
    captions = "\n".join(str(item.get("caption") or "") for item in source_map["evidence"]).lower()
    matched = [keyword for keyword in CORE_STATEMENT_KEYWORDS if keyword.lower() in captions]
    return {
        "market": "KR",
        "report_id": metadata["report_id"],
        "core_statement_matches": matched,
        "evidence_count": len(source_map["evidence"]),
        "financial_checks": {
            "status": "not_generated",
            "passed": 0,
            "failed": 0,
            "skipped": 0,
            "notes": [
                "KR PDF 未确认完整结构化连接财务报表，已按候选识别模式处理；完整数值勾稽建议结合 DART/XBRL 或原文表格复核。"
            ],
        },
    }


def _evidence_index(source_map: dict[str, Any]) -> dict[str, Any]:
    return {
        "market": "KR",
        "report_id": source_map["report_id"],
        "evidence": source_map["evidence"],
    }


def _retrieval_index(metadata: dict[str, Any], source_map: dict[str, Any]) -> dict[str, Any]:
    chunks = []
    for entry in source_map["evidence"]:
        chunks.append(
            {
                "chunk_id": f"{entry['evidence_id']}-retrieval",
                "market": "KR",
                "ticker": metadata["ticker"],
                "company_id": f"KR:{metadata['ticker']}",
                "report_id": metadata["report_id"],
                "evidence_id": entry["evidence_id"],
                "title": entry.get("caption") or "KR report evidence",
                "wiki_path": entry["md_path"],
                "page_number": entry.get("pdf_page_number"),
                "table_index": entry.get("table_index"),
                "source": {
                    "pdf_page_number": entry.get("pdf_page_number"),
                    "table_index": entry.get("table_index"),
                    "md_path": entry["md_path"],
                    "md_line": entry.get("md_line"),
                },
            }
        )
    return {"market": "KR", "report_id": metadata["report_id"], "chunks": chunks}


def _ensure_company_scaffold(company_dir: Path, metadata: dict[str, Any]) -> None:
    for dirname in ("reports", "metrics", "evidence", "semantic", "graph", "analysis", "factcheck", "tracking"):
        (company_dir / dirname).mkdir(parents=True, exist_ok=True)
    _write_json(
        company_dir / "company.json",
        {
            "market": "KR",
            "company_id": f"KR:{metadata['ticker']}",
            "ticker": metadata["ticker"],
            "company_name": metadata["company_name"],
            "wiki_root": "data/wiki/kr",
        },
    )
    company_md = company_dir / "company.md"
    if not company_md.exists():
        company_md.write_text(
            f"# {metadata['company_name']}\n\n市场：KR\n股票代码：{metadata['ticker']}\n",
            encoding="utf-8",
        )


def _update_catalogs(output_root: Path, package_dir: Path, metadata: dict[str, Any]) -> None:
    meta_dir = output_root / "_meta"
    company_catalog_path = meta_dir / "companies.json"
    report_catalog_path = meta_dir / "reports.json"
    company = {
        "market": "KR",
        "ticker": metadata["ticker"],
        "company_name": metadata["company_name"],
        "company_path": str(package_dir.parents[1].relative_to(output_root)),
    }
    report = {
        "market": "KR",
        "ticker": metadata["ticker"],
        "report_id": metadata["report_id"],
        "package_path": str(package_dir.relative_to(output_root)),
    }
    companies = _read_json(company_catalog_path).get("companies", [])
    reports = _read_json(report_catalog_path).get("reports", [])
    companies = [item for item in companies if item.get("ticker") != metadata["ticker"]] + [company]
    reports = [item for item in reports if item.get("package_path") != report["package_path"]] + [report]
    _write_json(company_catalog_path, {"market": "KR", "companies": sorted(companies, key=lambda item: item["ticker"])})
    _write_json(report_catalog_path, {"market": "KR", "reports": sorted(reports, key=lambda item: item["package_path"])})


def write_kr_pdf_wiki_package(
    pdf_path: Path,
    parser_result_dir: Path,
    output_root: Path,
    metadata_path: Path | None = None,
    *,
    force: bool = False,
) -> Path:
    pdf_path = Path(pdf_path)
    parser_result_dir = Path(parser_result_dir)
    output_root = Path(output_root)
    metadata = infer_kr_pdf_metadata(pdf_path, parser_result_dir, metadata_path)
    company_dir = output_root / "companies" / kr_company_dir_name(metadata["ticker"], metadata["company_name"])
    _ensure_company_scaffold(company_dir, metadata)
    package_dir = company_dir / "reports" / metadata["report_id"]
    if package_dir.exists() and force:
        shutil.rmtree(package_dir)
    if package_dir.exists():
        raise FileExistsError(f"Package already exists: {package_dir}")

    for dirname in ("raw", "sections", "tables", "xbrl", "metrics", "evidence", "semantic", "qa", "parser"):
        (package_dir / dirname).mkdir(parents=True, exist_ok=True)

    _copy_if_exists(pdf_path, package_dir / "raw" / pdf_path.name)
    for name in ("document_full.json", "content_list_enhanced.json", "table_index.json", "table_relations.json", "report_complete.md", "manifest.json"):
        _copy_if_exists(parser_result_dir / name, package_dir / "parser" / name)

    source_map = _build_source_map(metadata, parser_result_dir)
    quality = _quality_report(metadata, source_map)
    _write_json(package_dir / "qa" / "source_map.json", source_map)
    _write_json(package_dir / "qa" / "quality_report.json", quality)
    _write_json(package_dir / "evidence" / "evidence_index.json", _evidence_index(source_map))
    _write_json(package_dir / "semantic" / "retrieval_index.json", _retrieval_index(metadata, source_map))
    _write_json(package_dir / "metrics" / "financial_data.json", {"market": "KR", "report_id": metadata["report_id"], "metrics": []})
    _write_json(package_dir / "metrics" / "financial_checks.json", quality["financial_checks"])
    _write_json(
        package_dir / "metrics" / "load_plan.json",
        {"market": "KR", "report_id": metadata["report_id"], "load_targets": ["wiki", "postgresql", "vector_index"]},
    )
    _write_json(
        package_dir / "manifest.json",
        {
            "package_schema": "market_evidence_package_v1",
            "market": "KR",
            "ticker": metadata["ticker"],
            "company_name": metadata["company_name"],
            "report_year": metadata["report_year"],
            "report_type": metadata["report_type"],
            "report_id": metadata["report_id"],
            "filing_id": metadata["report_id"],
            "pdf_parser_task_id": metadata["pdf_parser_task_id"],
            "parser_result_dir": metadata["parser_result_dir"],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "paths": {
                "source_pdf": f"raw/{pdf_path.name}",
                "report_complete": "parser/report_complete.md",
                "document_full": "parser/document_full.json",
                "content_list_enhanced": "parser/content_list_enhanced.json",
                "table_index": "parser/table_index.json",
                "quality_report": "qa/quality_report.json",
                "source_map": "qa/source_map.json",
                "evidence_index": "evidence/evidence_index.json",
                "retrieval_index": "semantic/retrieval_index.json",
                "financial_data": "metrics/financial_data.json",
                "financial_checks": "metrics/financial_checks.json",
                "load_plan": "metrics/load_plan.json",
            },
        },
    )
    (package_dir / "README.md").write_text(
        f"# {metadata['company_name']} {metadata['report_year']} {metadata['report_type']}\n\n"
        "本目录由韩国市场 PDF 解析产物生成，包含 Markdown、表格证据、质量报告以及可回溯到 PDF 页码的来源索引。\n",
        encoding="utf-8",
    )
    _update_catalogs(output_root, package_dir, metadata)
    return package_dir
