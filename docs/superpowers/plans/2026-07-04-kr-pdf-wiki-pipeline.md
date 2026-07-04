# 韩国市场 PDF Wiki 管线 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 基于韩国市场 PDF 解析产物生成独立 KR wiki package，并让后端、前端、PostgreSQL 入库和智能体检索都能按 evidence 回溯到 PDF 页码、表格序号和解析任务。

**Architecture:** 第一版以 `data/pdf-parser/results/<task_id>` 为主输入，在 `data/wiki/kr_reports/companies/<ticker>-<slug>/reports/<report_id>/` 生成 A 股式目录，同时保持 `market_evidence_package_v1` manifest。现有 `scripts/kr/build_kr_evidence_package.py` 保持兼容：PDF 加 `--parser-result` 委派给新的 PDF wiki builder，DART/XBRL 输入继续走原有 KR evidence 逻辑。

**Tech Stack:** Python 3、pytest、FastAPI service helper、React/TypeScript、Vitest/Jest、现有 `market_report_rules_service.evidence_package` 和 `siq_market_contracts`。

## Global Constraints

- 所有新增说明文档和计划文档使用中文；代码标识符、API 名称、路径和 JSON 字段保持英文。
- KR wiki 只写入 `data/wiki/kr_reports`，不能写入 A 股目录 `data/wiki/companies` 或 A 股 `_meta`。
- 每条可被检索的 evidence 必须保留 `report_id`、`pdf_page_number`、`table_index`、`md_line`、`pdf_parser_task_id`、`parser_result_dir`。
- `manifest.json` 必须继续使用 `package_schema = market_evidence_package_v1`，以便现有 API、PostgreSQL 导入和向量入库复用。
- 第一版面向 PDF 解析产物；DART/XBRL 路径不做删除或破坏性改动。
- 不提交批量生成的 30 家 wiki 大文件，除非用户另行要求；代码、测试、计划文档和小型 fixture 可以提交。

---

## File Structure

- Create `scripts/kr/kr_pdf_wiki_lib.py`: 韩国 PDF 解析结果到 wiki package 的核心库，负责元数据推断、目录命名、evidence source map、manifest 和质量摘要。
- Create `scripts/kr/build_kr_pdf_wiki_package.py`: 单个 PDF + parser result 的命令行入口，输出一个 KR wiki package。
- Create `scripts/kr/discover_kr_parsed_cases.py`: 扫描 PDF 下载清单和 parser results，生成 30 家样本 case set。
- Create `scripts/kr/ingest_kr_case_set.py`: 按 case set 批量生成 KR wiki packages，并写 `_meta/ingest_manifest.json`。
- Modify `scripts/kr/build_kr_evidence_package.py`: 保留旧入口；当输入是 PDF 且传入 `--parser-result` 时转到新 builder。
- Modify `apps/api/services/market_report_commands.py`: KR PDF package build 要求 parser result，避免前端误触发空 package。
- Modify `apps/api/services/market_package_repository.py`: 后端发现 KR A 股式目录 `companies/*/reports/*/manifest.json`。
- Create `apps/web/src/features/market-parsing/marketPackagesPanelModel.ts`: 前端 package 面板的纯函数模型，便于单测覆盖排序、标题和主文件选择。
- Create `apps/web/src/components/pdf/MarketEvidencePackagesPanel.tsx`: 市场解析页通用 package 面板，第一版只挂到 KR 页面。
- Modify `apps/web/src/pages/KrParsing.tsx`: 在韩国解析页显示 KR wiki package 和 evidence 入口。
- Modify `apps/web/src/pages/MarketParsingPages.test.ts`: KR 页面必须挂载 package 面板；HK、JP、EU 保持当前页面结构。

### Task 1: KR PDF Wiki 核心库

**Files:**
- Create: `scripts/kr/kr_pdf_wiki_lib.py`
- Test: `scripts/kr/tests/test_kr_pdf_wiki_lib.py`

**Interfaces:**
- Consumes: PDF 文件路径、`data/pdf-parser/results/<task_id>` 目录、可选 metadata JSON。
- Produces: `normalize_kr_ticker(value: Any) -> str`
- Produces: `kr_company_dir_name(ticker: str, company_name: str) -> str`
- Produces: `infer_kr_pdf_metadata(pdf_path: Path, parser_result_dir: Path, metadata_path: Path | None = None) -> dict[str, Any]`
- Produces: `write_kr_pdf_wiki_package(pdf_path: Path, parser_result_dir: Path, output_root: Path, metadata_path: Path | None = None, *, force: bool = False) -> Path`

- [ ] **Step 1: Write the failing tests**

Create `scripts/kr/tests/test_kr_pdf_wiki_lib.py`:

```python
import json
import sys
from pathlib import Path


KR_DIR = Path(__file__).resolve().parents[1]
if str(KR_DIR) not in sys.path:
    sys.path.insert(0, str(KR_DIR))

import kr_pdf_wiki_lib as krwiki


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _minimal_parser_result(root: Path) -> Path:
    result_dir = root / "task-kr-1"
    _write_json(
        result_dir / "manifest.json",
        {
            "task_id": "task-kr-1",
            "market": "KR",
            "source_file": "005930_2025_annual.pdf",
            "company_name": "Samsung Electronics",
            "ticker": "005930",
            "report_year": 2025,
            "report_type": "annual",
        },
    )
    _write_json(
        result_dir / "document_full.json",
        {
            "pages": [
                {
                    "page": 78,
                    "text": "Consolidated Statement of Financial Position\nTotal assets 12345",
                    "tables": [
                        {
                            "table_index": 1,
                            "caption": "Consolidated Statement of Financial Position",
                            "rows": [["Assets", "2025"], ["Total assets", "12345"]],
                        }
                    ],
                }
            ]
        },
    )
    _write_json(
        result_dir / "content_list_enhanced.json",
        {
            "items": [
                {
                    "type": "table",
                    "page_idx": 77,
                    "table_index": 1,
                    "caption": "Consolidated Statement of Financial Position",
                    "text": "Total assets 12345",
                }
            ]
        },
    )
    (result_dir / "report_complete.md").write_text(
        "# Samsung Electronics 2025 Annual Report\n\n"
        "## Consolidated Statement of Financial Position\n\n"
        "| Item | 2025 |\n| --- | ---: |\n| Total assets | 12345 |\n",
        encoding="utf-8",
    )
    return result_dir


def test_write_kr_pdf_wiki_package_keeps_pdf_page_evidence(tmp_path: Path):
    pdf_path = tmp_path / "005930_2025_annual.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    result_dir = _minimal_parser_result(tmp_path / "results")
    output_root = tmp_path / "wiki" / "kr_reports"

    package_dir = krwiki.write_kr_pdf_wiki_package(pdf_path, result_dir, output_root, force=True)

    assert package_dir == output_root / "companies" / "005930-SamsungElectronics" / "reports" / "2025-annual_task-kr-1"
    manifest = json.loads((package_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["market"] == "KR"
    assert manifest["ticker"] == "005930"
    assert manifest["report_id"] == "2025-annual_task-kr-1"
    assert manifest["pdf_parser_task_id"] == "task-kr-1"
    assert manifest["paths"]["report_complete"] == "parser/report_complete.md"

    source_map = json.loads((package_dir / "qa" / "source_map.json").read_text(encoding="utf-8"))
    evidence = source_map["evidence"][0]
    assert evidence["market"] == "KR"
    assert evidence["report_id"] == "2025-annual_task-kr-1"
    assert evidence["pdf_page_number"] == 78
    assert evidence["table_index"] == 1
    assert evidence["md_line"] == 3
    assert evidence["pdf_parser_task_id"] == "task-kr-1"

    quality = json.loads((package_dir / "qa" / "quality_report.json").read_text(encoding="utf-8"))
    assert quality["market"] == "KR"
    assert quality["financial_checks"]["status"] == "not_generated"
    assert "KR PDF" in quality["financial_checks"]["notes"][0]

    company_catalog = json.loads((output_root / "_meta" / "companies.json").read_text(encoding="utf-8"))
    report_catalog = json.loads((output_root / "_meta" / "reports.json").read_text(encoding="utf-8"))
    assert company_catalog["companies"][0]["ticker"] == "005930"
    assert report_catalog["reports"][0]["package_path"].endswith("2025-annual_task-kr-1")


def test_infer_kr_pdf_metadata_prefers_metadata_file(tmp_path: Path):
    pdf_path = tmp_path / "lg_chem.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    result_dir = _minimal_parser_result(tmp_path / "results")
    metadata_path = tmp_path / "metadata.json"
    _write_json(
        metadata_path,
        {
            "ticker": "051910",
            "company_name": "LG Chem",
            "report_year": 2024,
            "report_type": "annual",
        },
    )

    metadata = krwiki.infer_kr_pdf_metadata(pdf_path, result_dir, metadata_path)

    assert metadata["ticker"] == "051910"
    assert metadata["company_name"] == "LG Chem"
    assert metadata["report_year"] == 2024
    assert metadata["report_type"] == "annual"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd /home/maoyd/siq-research-engine
python3 -m pytest scripts/kr/tests/test_kr_pdf_wiki_lib.py -q
```

Expected: command exits non-zero with `ModuleNotFoundError: No module named 'kr_pdf_wiki_lib'`.

- [ ] **Step 3: Create the core implementation**

Create `scripts/kr/kr_pdf_wiki_lib.py` with these public functions and helpers:

```python
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
    "요약재무정보",
    "재무상태표",
    "손익계산서",
    "현금흐름표",
    "자본변동표",
)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


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
```

Add `infer_kr_pdf_metadata()` in the same file:

```python
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
    report_year = int(metadata.get("report_year") or metadata.get("fiscal_year") or _year_from_name(pdf_path.stem) or datetime.now(timezone.utc).year)
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
```

Add table extraction, source map, quality report, catalogs and writer in the same file:

```python
def _copy_if_exists(src: Path, dst: Path) -> None:
    if src.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def _iter_tables(parser_result_dir: Path) -> list[dict[str, Any]]:
    document = _read_json(parser_result_dir / "document_full.json")
    tables: list[dict[str, Any]] = []
    for page in document.get("pages", []):
        page_no = page.get("page") or page.get("page_number")
        for table in page.get("tables", []):
            item = dict(table)
            item["pdf_page_number"] = int(page_no) if page_no else None
            item["caption"] = str(item.get("caption") or item.get("title") or "")
            tables.append(item)
    if tables:
        return tables

    content = _read_json(parser_result_dir / "content_list_enhanced.json")
    for item in content.get("items", []):
        if item.get("type") != "table":
            continue
        page_idx = item.get("page_idx")
        tables.append(
            {
                "table_index": item.get("table_index") or len(tables) + 1,
                "caption": str(item.get("caption") or item.get("text") or ""),
                "pdf_page_number": int(page_idx) + 1 if page_idx is not None else None,
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
                "ticker": metadata["ticker"],
                "report_id": metadata["report_id"],
                "pdf_parser_task_id": metadata["pdf_parser_task_id"],
                "parser_result_dir": metadata["parser_result_dir"],
                "pdf_page_number": table.get("pdf_page_number"),
                "table_index": table.get("table_index") or index,
                "caption": caption,
                "md_path": "parser/report_complete.md",
                "md_line": _line_for_caption(report_md, caption) or 1,
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
    package_dir = output_root / "companies" / kr_company_dir_name(metadata["ticker"], metadata["company_name"]) / "reports" / metadata["report_id"]
    if package_dir.exists() and force:
        shutil.rmtree(package_dir)
    if package_dir.exists():
        raise FileExistsError(f"Package already exists: {package_dir}")

    for dirname in ("raw", "sections", "tables", "xbrl", "metrics", "qa", "parser"):
        (package_dir / dirname).mkdir(parents=True, exist_ok=True)

    _copy_if_exists(pdf_path, package_dir / "raw" / pdf_path.name)
    for name in ("document_full.json", "content_list_enhanced.json", "table_relations.json", "report_complete.md", "manifest.json"):
        _copy_if_exists(parser_result_dir / name, package_dir / "parser" / name)

    source_map = _build_source_map(metadata, parser_result_dir)
    quality = _quality_report(metadata, source_map)
    _write_json(package_dir / "qa" / "source_map.json", source_map)
    _write_json(package_dir / "qa" / "quality_report.json", quality)
    _write_json(package_dir / "metrics" / "financial_data.json", {"market": "KR", "report_id": metadata["report_id"], "metrics": []})
    _write_json(package_dir / "metrics" / "financial_checks.json", quality["financial_checks"])
    _write_json(package_dir / "metrics" / "load_plan.json", {"market": "KR", "report_id": metadata["report_id"], "load_targets": ["wiki", "postgresql", "vector_index"]})
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
                "quality_report": "qa/quality_report.json",
                "source_map": "qa/source_map.json",
                "financial_data": "metrics/financial_data.json",
                "financial_checks": "metrics/financial_checks.json",
                "load_plan": "metrics/load_plan.json",
            },
        },
    )
    (package_dir / "README.md").write_text(
        f"# {metadata['company_name']} {metadata['report_year']} {metadata['report_type']}\n\n"
        "本目录由韩国市场 PDF 解析产物生成，保留 Markdown、表格证据、质量报告和 PDF 页码回溯信息。\n",
        encoding="utf-8",
    )
    _update_catalogs(output_root, package_dir, metadata)
    return package_dir
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
cd /home/maoyd/siq-research-engine
python3 -m pytest scripts/kr/tests/test_kr_pdf_wiki_lib.py -q
```

Expected: `2 passed`.

- [ ] **Step 5: Commit Task 1**

Run:

```bash
cd /home/maoyd/siq-research-engine
git add scripts/kr/kr_pdf_wiki_lib.py scripts/kr/tests/test_kr_pdf_wiki_lib.py
git commit -m "feat(kr): build pdf wiki evidence packages"
```

Expected: commit succeeds and includes only the two Task 1 files.

### Task 2: KR discovery、batch ingest 和 CLI

**Files:**
- Create: `scripts/kr/build_kr_pdf_wiki_package.py`
- Create: `scripts/kr/discover_kr_parsed_cases.py`
- Create: `scripts/kr/ingest_kr_case_set.py`
- Test: `scripts/kr/tests/test_kr_pdf_wiki_scripts.py`

**Interfaces:**
- Consumes from Task 1: `write_kr_pdf_wiki_package(pdf_path, parser_result_dir, output_root, metadata_path=None, force=False) -> Path`
- Produces: `discover_kr_cases(results_root: Path, manifest_path: Path | None, downloads_root: Path) -> list[dict[str, Any]]`
- Produces: `ingest_kr_case_set(case_set_path: Path, output_root: Path, *, force: bool = False, limit: int | None = None, ticker: str | None = None) -> dict[str, Any]`

- [ ] **Step 1: Write the failing script tests**

Create `scripts/kr/tests/test_kr_pdf_wiki_scripts.py`:

```python
import json
import sys
from pathlib import Path


KR_DIR = Path(__file__).resolve().parents[1]
if str(KR_DIR) not in sys.path:
    sys.path.insert(0, str(KR_DIR))

from discover_kr_parsed_cases import discover_kr_cases
from ingest_kr_case_set import ingest_kr_case_set


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_case(results_root: Path, downloads_root: Path, task_id: str, ticker: str, company: str) -> None:
    result_dir = results_root / task_id
    _write_json(
        result_dir / "manifest.json",
        {
            "task_id": task_id,
            "market": "KR",
            "ticker": ticker,
            "company_name": company,
            "report_year": 2025,
            "report_type": "annual",
            "source_file": f"{ticker}_2025.pdf",
        },
    )
    _write_json(
        result_dir / "document_full.json",
        {"pages": [{"page": 10, "tables": [{"table_index": 1, "caption": "Consolidated Statement of Cash Flows"}]}]},
    )
    _write_json(result_dir / "content_list_enhanced.json", {"items": []})
    (result_dir / "report_complete.md").write_text("# Report\n## Consolidated Statement of Cash Flows\n", encoding="utf-8")
    pdf_path = downloads_root / f"{ticker}_2025.pdf"
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(b"%PDF-1.4\n")


def test_discover_kr_cases_links_parser_result_to_download(tmp_path: Path):
    results_root = tmp_path / "results"
    downloads_root = tmp_path / "downloads"
    _write_case(results_root, downloads_root, "task-1", "005930", "Samsung Electronics")
    _write_json(results_root / "task-jp" / "manifest.json", {"task_id": "task-jp", "market": "JP"})

    cases = discover_kr_cases(results_root, None, downloads_root)

    assert len(cases) == 1
    assert cases[0]["ticker"] == "005930"
    assert cases[0]["parser_result_dir"].endswith("task-1")
    assert cases[0]["pdf_path"].endswith("005930_2025.pdf")


def test_ingest_case_set_writes_packages_and_meta_manifest(tmp_path: Path):
    results_root = tmp_path / "results"
    downloads_root = tmp_path / "downloads"
    output_root = tmp_path / "wiki" / "kr_reports"
    _write_case(results_root, downloads_root, "task-1", "005930", "Samsung Electronics")
    case_set_path = tmp_path / "kr_cases.json"
    _write_json(
        case_set_path,
        {
            "market": "KR",
            "cases": [
                {
                    "ticker": "005930",
                    "company_name": "Samsung Electronics",
                    "pdf_path": str(downloads_root / "005930_2025.pdf"),
                    "parser_result_dir": str(results_root / "task-1"),
                }
            ],
        },
    )

    result = ingest_kr_case_set(case_set_path, output_root, force=True)

    assert result["created"] == 1
    assert result["failed"] == 0
    ingest_manifest = json.loads((output_root / "_meta" / "ingest_manifest.json").read_text(encoding="utf-8"))
    assert ingest_manifest["market"] == "KR"
    assert ingest_manifest["created"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd /home/maoyd/siq-research-engine
python3 -m pytest scripts/kr/tests/test_kr_pdf_wiki_scripts.py -q
```

Expected: command exits non-zero because `discover_kr_parsed_cases` and `ingest_kr_case_set` modules do not exist.

- [ ] **Step 3: Add the single-package CLI**

Create `scripts/kr/build_kr_pdf_wiki_package.py`:

```python
from __future__ import annotations

import argparse
from pathlib import Path

from kr_pdf_wiki_lib import write_kr_pdf_wiki_package


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a KR PDF wiki evidence package")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--parser-result", required=True, type=Path)
    parser.add_argument("--metadata", type=Path)
    parser.add_argument("--output-root", type=Path, default=Path("data/wiki/kr_reports"))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    package_dir = write_kr_pdf_wiki_package(args.pdf, args.parser_result, args.output_root, args.metadata, force=args.force)
    print(package_dir)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Add discovery and ingest scripts**

Create `scripts/kr/discover_kr_parsed_cases.py` with `discover_kr_cases()` that:

```python
def discover_kr_cases(results_root: Path, manifest_path: Path | None, downloads_root: Path) -> list[dict[str, Any]]:
    manifest = _read_json(manifest_path) if manifest_path else {}
    cases: list[dict[str, Any]] = []
    for parser_manifest_path in sorted(results_root.glob("*/manifest.json")):
        parser_manifest = _read_json(parser_manifest_path)
        if str(parser_manifest.get("market", "")).upper() != "KR":
            continue
        pdf_path = _find_pdf(downloads_root, manifest, parser_manifest)
        if not pdf_path:
            continue
        cases.append(
            {
                "market": "KR",
                "ticker": normalize_kr_ticker(parser_manifest.get("ticker")),
                "company_name": parser_manifest.get("company_name") or parser_manifest.get("company") or "",
                "report_year": parser_manifest.get("report_year") or parser_manifest.get("fiscal_year"),
                "report_type": parser_manifest.get("report_type") or "annual",
                "pdf_path": str(pdf_path),
                "parser_result_dir": str(parser_manifest_path.parent),
            }
        )
    return cases
```

The same file must include `_read_json()`, `_find_pdf()`, and a CLI `main()` with `--results-root`, `--downloads-root`, `--manifest`, `--output`, `--limit`; `main()` writes `{"market": "KR", "cases": cases}` as UTF-8 JSON and prints `wrote <n> cases to <output>`.

Create `scripts/kr/ingest_kr_case_set.py` with:

```python
def ingest_kr_case_set(
    case_set_path: Path,
    output_root: Path,
    *,
    force: bool = False,
    limit: int | None = None,
    ticker: str | None = None,
) -> dict[str, Any]:
    payload = _read_json(case_set_path)
    selected = payload.get("cases", [])
    if ticker:
        wanted = normalize_kr_ticker(ticker)
        selected = [case for case in selected if normalize_kr_ticker(case.get("ticker")) == wanted]
    if limit:
        selected = selected[:limit]

    packages = []
    failures = []
    for case in selected:
        try:
            package_dir = write_kr_pdf_wiki_package(
                Path(case["pdf_path"]),
                Path(case["parser_result_dir"]),
                output_root,
                None,
                force=force,
            )
            packages.append(str(package_dir))
        except Exception as exc:
            failures.append({"case": case, "error": str(exc)})
    result = {
        "market": "KR",
        "case_set": str(case_set_path),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "selected": len(selected),
        "created": len(packages),
        "failed": len(failures),
        "packages": packages,
        "failures": failures,
    }
    _write_json(output_root / "_meta" / "ingest_manifest.json", result)
    return result
```

The same file must include a CLI `main()` with `--case-set`, `--output-root`, `--force`, `--limit`, `--ticker`; `main()` prints the result JSON.

- [ ] **Step 5: Run script tests to verify they pass**

Run:

```bash
cd /home/maoyd/siq-research-engine
python3 -m pytest scripts/kr/tests/test_kr_pdf_wiki_scripts.py -q
```

Expected: `2 passed`.

- [ ] **Step 6: Commit Task 2**

Run:

```bash
cd /home/maoyd/siq-research-engine
git add scripts/kr/build_kr_pdf_wiki_package.py scripts/kr/discover_kr_parsed_cases.py scripts/kr/ingest_kr_case_set.py scripts/kr/tests/test_kr_pdf_wiki_scripts.py
git commit -m "feat(kr): add pdf wiki ingestion scripts"
```

Expected: commit succeeds and includes only Task 2 files.

### Task 3: 兼容现有 KR build 入口和后端 build plan

**Files:**
- Modify: `scripts/kr/build_kr_evidence_package.py`
- Modify: `apps/api/services/market_report_commands.py`
- Test: `apps/api/tests/test_market_report_commands.py`

**Interfaces:**
- Consumes from Task 1: `write_kr_pdf_wiki_package(pdf_path, parser_result_dir, output_root, metadata_path=None, force=False) -> Path`
- Produces: KR PDF build plan requires parser result and routes to `scripts/kr/build_kr_evidence_package.py --parser-result <dir>`.

- [ ] **Step 1: Write the failing backend test**

Append this test to `apps/api/tests/test_market_report_commands.py`:

```python
def test_kr_pdf_package_build_requires_parser_result(tmp_path: Path):
    source = tmp_path / "005930_2025_annual.pdf"
    source.write_bytes(b"%PDF-1.4\n")

    assert market_report_commands.market_build_requires_parser_result("KR", source) is True

    plan = market_report_commands.build_market_package_command(
        "KR",
        source,
        tmp_path / "wiki" / "kr_reports",
        parser_result=tmp_path / "results" / "task-kr-1",
        force=True,
    )

    assert "scripts/kr/build_kr_evidence_package.py" in " ".join(plan.command)
    assert "--parser-result" in plan.command
```

If the file imports individual functions instead of the module, add:

```python
from apps.api.services import market_report_commands
```

- [ ] **Step 2: Run the backend test to verify it fails**

Run:

```bash
cd /home/maoyd/siq-research-engine
python3 -m pytest apps/api/tests/test_market_report_commands.py -q
```

Expected: command exits non-zero because `market_build_requires_parser_result("KR", pdf)` returns `False`.

- [ ] **Step 3: Update the API command helper**

In `apps/api/services/market_report_commands.py`, update `market_build_requires_parser_result`:

```python
def market_build_requires_parser_result(market: str, source_path: Path) -> bool:
    market = market.upper()
    if market == "EU":
        suffix = source_path.suffix.lower()
        return suffix in {".pdf", ".zip"}
    if market == "KR" and source_path.suffix.lower() == ".pdf":
        return True
    return market == "HK"
```

If the current function has extra EU suffix handling, keep that EU handling and insert the KR branch before the final return.

- [ ] **Step 4: Route PDF input in the KR compatibility CLI**

In `scripts/kr/build_kr_evidence_package.py`, after parsing args and before calling the existing `write_kr_evidence_package`, add:

```python
    if args.source.suffix.lower() == ".pdf" and args.parser_result:
        from kr_pdf_wiki_lib import write_kr_pdf_wiki_package

        package_dir = write_kr_pdf_wiki_package(
            args.source,
            args.parser_result,
            args.output_root,
            args.metadata,
            force=args.force,
        )
        print(package_dir)
        return
```

Keep the existing DART/XBRL path below this branch.

- [ ] **Step 5: Run the backend tests to verify they pass**

Run:

```bash
cd /home/maoyd/siq-research-engine
python3 -m pytest apps/api/tests/test_market_report_commands.py scripts/kr/tests/test_kr_pdf_wiki_lib.py -q
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit Task 3**

Run:

```bash
cd /home/maoyd/siq-research-engine
git add scripts/kr/build_kr_evidence_package.py apps/api/services/market_report_commands.py apps/api/tests/test_market_report_commands.py
git commit -m "fix(kr): require parser results for pdf wiki builds"
```

Expected: commit succeeds and includes only Task 3 files.

### Task 4: 后端 package 扫描和 evidence API 兼容

**Files:**
- Modify: `apps/api/services/market_package_repository.py`
- Modify: `apps/api/tests/test_market_package_repository.py`

**Interfaces:**
- Consumes from Task 1: KR package path `companies/<ticker>-<slug>/reports/<report_id>/manifest.json`。
- Produces: `GET /api/market-reports/packages?market=KR` can discover KR A 股式 package roots.

- [ ] **Step 1: Write the failing repository test**

Append this test to `apps/api/tests/test_market_package_repository.py`:

```python
def test_iter_market_packages_discovers_kr_company_report_layout(tmp_path: Path):
    root = tmp_path / "kr_reports"
    manifest = root / "companies" / "005930-SamsungElectronics" / "reports" / "2025-annual_task-kr" / "manifest.json"
    _write_json(
        manifest,
        {
            "package_schema": "market_evidence_package_v1",
            "market": "KR",
            "ticker": "005930",
            "company_name": "Samsung Electronics",
            "report_id": "2025-annual_task-kr",
            "filing_id": "2025-annual_task-kr",
            "report_year": 2025,
            "paths": {"report_complete": "parser/report_complete.md", "source_map": "qa/source_map.json"},
        },
    )

    packages = list(market_package_repository.iter_market_packages("KR", [root]))

    assert len(packages) == 1
    assert packages[0].market == "KR"
    assert packages[0].ticker == "005930"
    assert packages[0].relative_path == "companies/005930-SamsungElectronics/reports/2025-annual_task-kr"
```

If the test file imports functions directly, add:

```python
from apps.api.services import market_package_repository
```

- [ ] **Step 2: Run the repository test to verify it fails**

Run:

```bash
cd /home/maoyd/siq-research-engine
python3 -m pytest apps/api/tests/test_market_package_repository.py -q
```

Expected: the new KR test fails with zero discovered packages.

- [ ] **Step 3: Add market-specific package patterns**

In `apps/api/services/market_package_repository.py`, add:

```python
def _package_patterns_for_market(market: str) -> tuple[str, ...]:
    market = market.upper()
    if market == "EU":
        return ("*/*/*/*/manifest.json",)
    if market == "KR":
        return ("companies/*/reports/*/manifest.json", "*/*/*/manifest.json")
    return ("*/*/*/manifest.json",)
```

Then update `iter_market_packages` to use:

```python
patterns = _package_patterns_for_market(market)
for root in market_wiki_roots:
    for pattern in patterns:
        manifest_paths.extend(root.glob(pattern))
```

Keep the existing manifest parsing and sorting logic unchanged.

- [ ] **Step 4: Run the repository tests to verify they pass**

Run:

```bash
cd /home/maoyd/siq-research-engine
python3 -m pytest apps/api/tests/test_market_package_repository.py -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit Task 4**

Run:

```bash
cd /home/maoyd/siq-research-engine
git add apps/api/services/market_package_repository.py apps/api/tests/test_market_package_repository.py
git commit -m "feat(kr): discover pdf wiki packages in market API"
```

Expected: commit succeeds and includes only Task 4 files.

### Task 5: KR 前端 package 面板联动

**Files:**
- Create: `apps/web/src/features/market-parsing/marketPackagesPanelModel.ts`
- Create: `apps/web/src/features/market-parsing/marketPackagesPanelModel.test.ts`
- Create: `apps/web/src/components/pdf/MarketEvidencePackagesPanel.tsx`
- Modify: `apps/web/src/pages/KrParsing.tsx`
- Modify: `apps/web/src/pages/MarketParsingPages.test.ts`

**Interfaces:**
- Consumes: `fetchMarketPackages`, `fetchMarketPackageDetail`, `runMarketPackageImportAction`, `runMarketPackageVectorDryRunAction`, `marketPackageFileUrl` from existing market parsing API/action modules.
- Produces: KR parsing page shows package list, primary Markdown/JSON entry, import action and vector dry-run action tied to KR package paths.

- [ ] **Step 1: Write the failing model tests**

Create `apps/web/src/features/market-parsing/marketPackagesPanelModel.test.ts`:

```ts
import { describe, expect, it } from 'vitest'
import { deriveMarketPackageRows, packagePrimaryFile } from './marketPackagesPanelModel'

describe('marketPackagesPanelModel', () => {
  it('maps package summaries into stable rows with busy state', () => {
    const rows = deriveMarketPackageRows(
      {
        market: 'KR',
        packages: [
          {
            market: 'KR',
            package_path: 'companies/005930-SamsungElectronics/reports/2025-annual_task-kr',
            ticker: '005930',
            company_name: 'Samsung Electronics',
            report_year: 2025,
            report_type: 'annual',
            filing_id: '2025-annual_task-kr',
            paths: { report_complete: 'parser/report_complete.md', source_map: 'qa/source_map.json' },
          },
        ],
      },
      'companies/005930-SamsungElectronics/reports/2025-annual_task-kr',
    )

    expect(rows).toEqual([
      expect.objectContaining({
        id: 'companies/005930-SamsungElectronics/reports/2025-annual_task-kr',
        title: '005930 Samsung Electronics',
        summary: '2025 · annual · 2025-annual_task-kr',
        busy: true,
      }),
    ])
  })

  it('prefers report_complete as the primary file', () => {
    expect(packagePrimaryFile({ paths: { source_map: 'qa/source_map.json', report_complete: 'parser/report_complete.md' } })).toBe(
      'parser/report_complete.md',
    )
    expect(packagePrimaryFile({ paths: { source_map: 'qa/source_map.json' } })).toBe('qa/source_map.json')
  })
})
```

- [ ] **Step 2: Run the model tests to verify they fail**

Run:

```bash
cd /home/maoyd/siq-research-engine/apps/web
npm test -- --runTestsByPath src/features/market-parsing/marketPackagesPanelModel.test.ts
```

Expected: command exits non-zero because `marketPackagesPanelModel` does not exist.

- [ ] **Step 3: Implement the model**

Create `apps/web/src/features/market-parsing/marketPackagesPanelModel.ts`:

```ts
import type { MarketPackageSummary, MarketPackagesResponse } from './api'

export interface MarketPackageRow extends MarketPackageSummary {
  id: string
  title: string
  summary: string
  busy: boolean
}

export function packagePrimaryFile(item: Pick<MarketPackageSummary, 'paths'>): string {
  return item.paths.report_complete ?? item.paths.report_markdown ?? item.paths.source_map ?? item.paths.quality_report ?? 'manifest.json'
}

export function deriveMarketPackageRows(payload: MarketPackagesResponse, busyPath = ''): MarketPackageRow[] {
  return payload.packages.map((item) => {
    const titleParts = [item.ticker, item.company_name].filter(Boolean)
    const summaryParts = [item.report_year, item.report_type, item.filing_id].filter(Boolean)
    return {
      ...item,
      id: item.package_path,
      title: titleParts.join(' ') || item.filing_id || item.package_path,
      summary: summaryParts.join(' · '),
      busy: item.package_path === busyPath,
    }
  })
}
```

- [ ] **Step 4: Run the model tests to verify they pass**

Run:

```bash
cd /home/maoyd/siq-research-engine/apps/web
npm test -- --runTestsByPath src/features/market-parsing/marketPackagesPanelModel.test.ts
```

Expected: selected test file passes.

- [ ] **Step 5: Add the package panel component**

Create `apps/web/src/components/pdf/MarketEvidencePackagesPanel.tsx` with:

```tsx
import { ExternalLink, PlayCircle, RefreshCw, UploadCloud } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { fetchMarketPackages, marketPackageFileUrl, type MarketCode, type MarketPackagesResponse } from '../../features/market-parsing/api'
import {
  buildMarketPackageRequest,
  formatMarketPackageActionResult,
  runMarketPackageImportAction,
  runMarketPackageVectorDryRunAction,
} from '../../features/market-parsing/packageActions'
import { deriveMarketPackageRows, packagePrimaryFile } from '../../features/market-parsing/marketPackagesPanelModel'

interface Props {
  market: MarketCode
}

export function MarketEvidencePackagesPanel({ market }: Props) {
  const [payload, setPayload] = useState<MarketPackagesResponse>({ market, packages: [] })
  const [busyPath, setBusyPath] = useState('')
  const [message, setMessage] = useState('')

  const load = useCallback(async () => {
    const next = await fetchMarketPackages(market)
    setPayload(next)
  }, [market])

  useEffect(() => {
    void load()
  }, [load])

  const rows = useMemo(() => deriveMarketPackageRows(payload, busyPath), [payload, busyPath])

  const runImport = async (packagePath: string) => {
    setBusyPath(packagePath)
    try {
      const result = await runMarketPackageImportAction(buildMarketPackageRequest(market, packagePath))
      setMessage(formatMarketPackageActionResult(result))
      await load()
    } finally {
      setBusyPath('')
    }
  }

  const runVectorDryRun = async (packagePath: string) => {
    setBusyPath(packagePath)
    try {
      const result = await runMarketPackageVectorDryRunAction(buildMarketPackageRequest(market, packagePath))
      setMessage(formatMarketPackageActionResult(result))
    } finally {
      setBusyPath('')
    }
  }

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm" aria-label="KR wiki evidence packages">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold text-slate-900">Wiki 证据包</h2>
          <p className="mt-1 text-sm text-slate-500">韩国 PDF 解析后的 wiki、PostgreSQL 和检索回溯入口。</p>
        </div>
        <button type="button" onClick={load} className="inline-flex h-9 w-9 items-center justify-center rounded-md border border-slate-200 text-slate-600 hover:bg-slate-50" title="刷新">
          <RefreshCw className="h-4 w-4" />
        </button>
      </div>
      {message ? <p className="mt-3 rounded-md bg-slate-50 px-3 py-2 text-sm text-slate-600">{message}</p> : null}
      <div className="mt-4 space-y-3">
        {rows.map((row) => {
          const primaryFile = packagePrimaryFile(row)
          return (
            <article key={row.id} className="rounded-md border border-slate-200 p-3">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <h3 className="text-sm font-semibold text-slate-900">{row.title}</h3>
                  <p className="mt-1 text-xs text-slate-500">{row.summary}</p>
                </div>
                <div className="flex items-center gap-2">
                  <a className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-slate-200 text-slate-600 hover:bg-slate-50" href={marketPackageFileUrl(market, row.package_path, primaryFile)} target="_blank" rel="noreferrer" title="打开主文件">
                    <ExternalLink className="h-4 w-4" />
                  </a>
                  <button type="button" disabled={row.busy} onClick={() => runImport(row.package_path)} className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-slate-200 text-slate-600 hover:bg-slate-50 disabled:opacity-50" title="导入 PostgreSQL">
                    <UploadCloud className="h-4 w-4" />
                  </button>
                  <button type="button" disabled={row.busy} onClick={() => runVectorDryRun(row.package_path)} className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-slate-200 text-slate-600 hover:bg-slate-50 disabled:opacity-50" title="向量入库预演">
                    <PlayCircle className="h-4 w-4" />
                  </button>
                </div>
              </div>
            </article>
          )
        })}
        {rows.length === 0 ? <p className="rounded-md border border-dashed border-slate-200 p-4 text-sm text-slate-500">暂无 KR wiki 证据包。</p> : null}
      </div>
    </section>
  )
}
```

- [ ] **Step 6: Mount the panel on KR page and update page tests**

In `apps/web/src/pages/KrParsing.tsx`, import the panel:

```tsx
import { MarketEvidencePackagesPanel } from '../components/pdf/MarketEvidencePackagesPanel'
```

Add this prop to the existing `MarketParsingPage` call:

```tsx
extraPanel={<MarketEvidencePackagesPanel market="KR" />}
```

In `apps/web/src/pages/MarketParsingPages.test.ts`, add:

```ts
it('mounts KR evidence packages panel on the Korean parsing page', () => {
  const source = readPage('KrParsing.tsx')
  expect(source).toContain('MarketEvidencePackagesPanel')
  expect(source).toContain('extraPanel={<MarketEvidencePackagesPanel market="KR" />}')
})
```

Update the existing no-panel assertion so it covers only `JpParsing.tsx`, `HkParsing.tsx`, `EuParsing.tsx`:

```ts
it.each(['JpParsing.tsx', 'HkParsing.tsx', 'EuParsing.tsx'])('does not mount package panel on %s', (page) => {
  const source = readPage(page)
  expect(source).not.toContain('MarketEvidencePackagesPanel')
  expect(source).not.toContain('extraPanel=')
})
```

- [ ] **Step 7: Run frontend tests to verify they pass**

Run:

```bash
cd /home/maoyd/siq-research-engine/apps/web
npm test -- --runTestsByPath src/features/market-parsing/marketPackagesPanelModel.test.ts src/features/market-parsing/packageActions.test.ts src/pages/MarketParsingPages.test.ts
```

Expected: selected frontend tests pass.

- [ ] **Step 8: Commit Task 5**

Run:

```bash
cd /home/maoyd/siq-research-engine
git add apps/web/src/features/market-parsing/marketPackagesPanelModel.ts apps/web/src/features/market-parsing/marketPackagesPanelModel.test.ts apps/web/src/components/pdf/MarketEvidencePackagesPanel.tsx apps/web/src/pages/KrParsing.tsx apps/web/src/pages/MarketParsingPages.test.ts
git commit -m "feat(kr): surface pdf wiki packages in parsing UI"
```

Expected: commit succeeds and includes only Task 5 files.

### Task 6: 端到端 smoke 与 30 家落盘验证

**Files:**
- No source file changes required.
- Generated local artifacts: `eval_datasets/market_ingestion_cases/kr_30_pdf_cases.json`
- Generated local artifacts: `data/wiki/kr_reports/companies/<company>/reports/<report_id>/`

**Interfaces:**
- Consumes from Tasks 1-5: CLI scripts、backend scan、frontend package panel。
- Produces: one verified 30-case ingest manifest and API smoke output showing KR packages can be discovered.

- [ ] **Step 1: Run Python regression tests**

Run:

```bash
cd /home/maoyd/siq-research-engine
python3 -m pytest \
  scripts/kr/tests/test_kr_pdf_wiki_lib.py \
  scripts/kr/tests/test_kr_pdf_wiki_scripts.py \
  apps/api/tests/test_market_report_commands.py \
  apps/api/tests/test_market_package_repository.py \
  -q
```

Expected: selected Python tests pass.

- [ ] **Step 2: Run frontend regression tests**

Run:

```bash
cd /home/maoyd/siq-research-engine/apps/web
npm test -- --runTestsByPath \
  src/features/market-parsing/marketPackagesPanelModel.test.ts \
  src/features/market-parsing/packageActions.test.ts \
  src/pages/MarketParsingPages.test.ts
```

Expected: selected frontend tests pass.

- [ ] **Step 3: Discover the 30 KR parsed cases**

Run:

```bash
cd /home/maoyd/siq-research-engine
python3 scripts/kr/discover_kr_parsed_cases.py \
  --results-root data/pdf-parser/results \
  --downloads-root data/market-report-finder/downloads \
  --manifest data/market-report-finder/kr_2025_annual_download_queue_manifest.json \
  --output eval_datasets/market_ingestion_cases/kr_30_pdf_cases.json \
  --limit 30
```

Expected: output includes `wrote 30 cases` and the JSON file has `market = KR` and 30 cases.

- [ ] **Step 4: Build one package as a smoke test**

Run:

```bash
cd /home/maoyd/siq-research-engine
python3 scripts/kr/ingest_kr_case_set.py \
  --case-set eval_datasets/market_ingestion_cases/kr_30_pdf_cases.json \
  --output-root data/wiki/kr_reports \
  --limit 1 \
  --force
```

Expected: JSON output has `created = 1`, `failed = 0`, and one path under `data/wiki/kr_reports/companies/`.

- [ ] **Step 5: Start or reuse the API service and run package scan smoke**

Check the service first:

```bash
curl -s http://127.0.0.1:18081/health
```

If the health check does not return JSON, start the API in a separate shell:

```bash
cd /home/maoyd/siq-research-engine/apps/api
uv run uvicorn main:app --reload --host 0.0.0.0 --port 18081
```

Then run the package scan:

```bash
curl -s 'http://127.0.0.1:18081/api/market-reports/packages?market=KR&limit=5' | python3 -m json.tool
```

Expected: JSON output includes at least one package whose `package_path` starts with `companies/` and whose `market` is `KR`.

- [ ] **Step 6: Build all 30 KR packages**

Run:

```bash
cd /home/maoyd/siq-research-engine
python3 scripts/kr/ingest_kr_case_set.py \
  --case-set eval_datasets/market_ingestion_cases/kr_30_pdf_cases.json \
  --output-root data/wiki/kr_reports \
  --force
```

Expected: JSON output has `created = 30`, `failed = 0`, and `_meta/ingest_manifest.json` records the same counts.

- [ ] **Step 7: Inspect generated data but do not stage it**

Run:

```bash
cd /home/maoyd/siq-research-engine
git status --short data/wiki/kr_reports eval_datasets/market_ingestion_cases/kr_30_pdf_cases.json
```

Expected: generated wiki data appears as untracked or modified local artifacts; do not include these files in the implementation commits unless the user requests checked-in datasets.

- [ ] **Step 8: Final implementation commit status check**

Run:

```bash
cd /home/maoyd/siq-research-engine
git log --oneline -6
git status --short
```

Expected: the implementation commits are visible at the top of the log. `git status --short` may show unrelated user changes and generated data; none of those files are staged.
