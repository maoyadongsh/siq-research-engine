"""Deterministic Deal Evidence package builder.

The build itself stays local and deterministic. An explicit deployment flag may
trigger primary-market Milvus indexing only after the complete local package and
snapshot have been written.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from services import (
    deal_documents,
    deal_evidence_milvus,
    deal_store,
    ic_policy,
    primary_market_wiki,
)
from services.path_config import DOCUMENT_PARSER_RESULTS_ROOT

EVIDENCE_ITEM_SCHEMA = "siq_deal_evidence_item_v1"
EVIDENCE_INDEX_SCHEMA = "siq_deal_evidence_index_v1"
EVIDENCE_QUALITY_SCHEMA = "siq_deal_evidence_quality_v1"
EVIDENCE_INGEST_DRY_RUN_SCHEMA = "siq_deal_evidence_ingest_dry_run_v1"
EVIDENCE_SNAPSHOT_SCHEMA = "siq_deal_evidence_snapshot_v1"
BUILD_MODE = "offline_document_md_v1"
PDF_ARCHIVE_BUILD_MODE = "deal_archive_pdf_v1"
INGEST_DRY_RUN_MODE = "deal_evidence_ingest_dry_run_v1"
MAX_CHUNK_CHARS = 1600
MAX_QUOTE_CHARS = 1200
ITEMS_PREVIEW_LIMIT = 50
ITEMS_PREVIEW_MAX_LIMIT = 200
ITEMS_PREVIEW_LIMIT_OPTIONS = (10, 20, 50, 100, 200)

DOC_BLOCK_RE = re.compile(r"<!--\s*DOC_BLOCK:\s*(?P<block_id>[^\s>]+)(?P<attrs>.*?)-->", re.I)
ATTR_RE = re.compile(r"(?P<key>[A-Za-z_][\w-]*)=(?P<value>\"[^\"]*\"|'[^']*'|[^\s>]+)")
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)

DIMENSION_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("finance", ("finance", "financial", "model", "valuation", "audit", "revenue", "income", "cash", "cap_table")),
    ("legal", ("legal", "contract", "license", "licence", "ip", "patent", "term_sheet", "regulatory")),
    ("risk", ("risk", "compliance", "security", "privacy", "litigation", "lawsuit", "sanction")),
    ("business", ("business", "bp", "deck", "teaser", "memo", "market", "customer", "product", "commercial", "founder")),
)
ROLE_HINTS_BY_DIMENSION = {
    "business": ["siq_ic_strategist", "siq_ic_sector_expert", "siq_ic_chairman"],
    "finance": ["siq_ic_finance_auditor", "siq_ic_chairman"],
    "legal": ["siq_ic_legal_scanner", "siq_ic_chairman"],
    "risk": ["siq_ic_risk_controller", "siq_ic_chairman"],
}


def _require_package_dir(deal_id: str, *, wiki_root: Path | str | None = None) -> Path:
    package_dir = deal_store.safe_deal_dir(deal_id, wiki_root=wiki_root)
    if not (package_dir / "manifest.json").is_file():
        raise FileNotFoundError(deal_id)
    deal_store.ensure_deal_package_dirs(package_dir)
    return package_dir


def _read_document_metadata(package_dir: Path) -> tuple[list[dict[str, Any]], list[str]]:
    metadata_dir = package_dir / "data_room" / "metadata"
    documents: list[dict[str, Any]] = []
    invalid_files: list[str] = []
    if not metadata_dir.exists():
        return documents, invalid_files
    for path in sorted(metadata_dir.glob("DOC-*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            invalid_files.append(path.name)
            continue
        if isinstance(payload, dict):
            documents.append(payload)
        else:
            invalid_files.append(path.name)
    documents.sort(key=lambda item: str(item.get("document_id") or ""))
    return documents, invalid_files


def _safe_parser_artifact_path(value: Any) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    if not raw:
        return ""
    path = Path(raw)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("parsed_artifact_path must be a relative parser artifact path")
    return path.as_posix().strip("/")[:300]


def _result_relative_path(task_id: str, path: Path) -> str:
    result_dir = DOCUMENT_PARSER_RESULTS_ROOT / task_id
    try:
        relative = path.resolve().relative_to(result_dir.resolve()).as_posix()
    except ValueError:
        relative = path.name
    return f"parser_results/{task_id}/{relative}"


def _safe_existing_result_file(result_dir: Path, candidate: Path) -> Path | None:
    if not candidate.is_file():
        return None
    try:
        candidate.resolve().relative_to(result_dir.resolve())
    except ValueError:
        return None
    return candidate


def _document_md_candidate(document: dict[str, Any]) -> tuple[Path | None, str, str | None]:
    task_id = deal_documents.validate_parser_task_id(str(document.get("parse_task_id") or ""))
    result_dir = DOCUMENT_PARSER_RESULTS_ROOT / task_id
    if not result_dir.is_dir():
        return None, f"parser_results/{task_id}/document.md", "missing_task_dir"

    artifact_path = _safe_parser_artifact_path(document.get("parsed_artifact_path"))
    candidates: list[Path] = []
    if artifact_path:
        artifact = result_dir / artifact_path
        if artifact.is_dir():
            candidates.append(artifact / "document.md")
        elif artifact.name == "document.md":
            candidates.append(artifact)
    candidates.append(result_dir / "document.md")

    for candidate in candidates:
        safe_candidate = _safe_existing_result_file(result_dir, candidate)
        if safe_candidate:
            return safe_candidate, _result_relative_path(task_id, safe_candidate), None
    expected = candidates[0] if candidates else result_dir / "document.md"
    return None, _result_relative_path(task_id, expected), "missing_document_md"


def _company_wiki_candidate(
    package_dir: Path,
    document: dict[str, Any],
) -> tuple[Path | None, str, str | None]:
    document_id = deal_documents.validate_document_id(str(document.get("document_id") or ""))
    category = primary_market_wiki.normalize_material_category(document.get("document_type"))
    expected_path = f"wiki/company/materials/{category}/{document_id}.md"
    wiki_path = str(document.get("wiki_path") or "").strip().replace("\\", "/")
    if document.get("wiki_status") != "ready" or wiki_path != expected_path:
        return None, expected_path, "company_wiki_projection_missing"
    candidate = package_dir / wiki_path
    try:
        candidate.resolve().relative_to((package_dir / "wiki" / "company").resolve())
    except ValueError:
        return None, expected_path, "company_wiki_path_invalid"
    if not candidate.is_file():
        return None, expected_path, "company_wiki_file_missing"
    actual_hash = _sha256_path(candidate)
    metadata_hash = str(document.get("wiki_sha256") or "")
    company_index = deal_store.read_json(
        package_dir / primary_market_wiki.COMPANY_WIKI_INDEX_PATH,
        {},
    ) or {}
    projections = (
        company_index.get("documents")
        if isinstance(company_index.get("documents"), dict)
        else {}
    )
    projection = projections.get(document_id) if isinstance(projections, dict) else None
    if not isinstance(projection, dict):
        return None, expected_path, "company_wiki_index_missing"
    if (
        projection.get("deal_id") != document.get("deal_id")
        or projection.get("document_id") != document_id
        or projection.get("wiki_path") != expected_path
        or str(projection.get("wiki_sha256") or "") != actual_hash
        or metadata_hash != actual_hash
    ):
        return None, expected_path, "company_wiki_hash_mismatch"
    return candidate, expected_path, None


def _analysis_sources(package_dir: Path) -> list[dict[str, Any]]:
    payload = deal_store.read_json(package_dir / "sources" / "analysis_sources.json", {}) or {}
    sources = payload.get("sources") if isinstance(payload, dict) else payload
    return [item for item in sources if isinstance(item, dict)] if isinstance(sources, list) else []


def _active_pdf_source_by_document(package_dir: Path) -> dict[str, dict[str, Any]]:
    active: dict[str, dict[str, Any]] = {}
    for source in _analysis_sources(package_dir):
        if source.get("source_type") != "primary_market_prospectus":
            continue
        status = str(source.get("status") or "")
        if status not in {"ready", "ready_with_restrictions"}:
            continue
        document_id = str(source.get("document_id") or "")
        if document_id:
            active[document_id] = source
    return active


def _safe_pdf_run_dir(
    package_dir: Path,
    document: dict[str, Any],
    source: dict[str, Any] | None,
) -> tuple[Path | None, str | None, str | None]:
    document_id = deal_documents.validate_document_id(str(document.get("document_id") or ""))
    parse_run_id = str((source or {}).get("parse_run_id") or document.get("current_parse_run_id") or "").strip()
    if not re.fullmatch(r"PRUN-[A-Za-z0-9][A-Za-z0-9-]{7,95}", parse_run_id):
        return None, None, "missing_or_invalid_parse_run_id"
    run_dir = package_dir / "parsed_documents" / document_id / "runs" / parse_run_id
    try:
        run_dir.resolve().relative_to(package_dir.resolve())
    except ValueError:
        return None, parse_run_id, "parse_run_path_escape"
    if not run_dir.is_dir():
        return None, parse_run_id, "missing_parse_run_archive"
    return run_dir, parse_run_id, None


def _json_payload(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _pdf_blocks(run_dir: Path) -> list[dict[str, Any]]:
    for name in ("content_list_enhanced.json", "content_list.json"):
        payload = _json_payload(run_dir / name)
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if not isinstance(payload, dict):
            continue
        for key in ("blocks", "items", "content_list"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        pages = payload.get("pages")
        if isinstance(pages, list):
            blocks: list[dict[str, Any]] = []
            for page in pages:
                if not isinstance(page, dict):
                    continue
                page_no = page.get("page") or page.get("page_number") or page.get("page_idx")
                for block in page.get("blocks") or page.get("items") or []:
                    if isinstance(block, dict):
                        blocks.append({"_page": page_no, **block})
            if blocks:
                return blocks
    return []


def _block_text(block: dict[str, Any]) -> str:
    for key in ("text", "content", "markdown", "body"):
        value = block.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _block_page(block: dict[str, Any]) -> int | None:
    for key in ("page", "page_number", "page_no", "page_idx", "_page"):
        value = block.get(key)
        try:
            if value is not None:
                number = int(value)
                return number + 1 if key == "page_idx" and number >= 0 else number
        except (TypeError, ValueError):
            continue
    return None


def _prospectus_dimension(text: str) -> str:
    rules = (
        ("finance", ("财务", "收入", "利润", "现金流", "资产负债", "会计")),
        ("legal", ("法律", "诉讼", "知识产权", "资质", "合规", "关联交易")),
        ("risk", ("风险", "集中度", "不确定性", "重大不利")),
        ("sector", ("行业", "市场规模", "竞争格局", "市场地位", "产业链")),
        ("strategy", ("募集资金", "战略", "发展规划", "募投")),
    )
    for dimension, keywords in rules:
        if any(keyword in text for keyword in keywords):
            return dimension
    return "business"


def _prospectus_role_hints(dimension: str) -> list[str]:
    return {
        "finance": ["siq_ic_finance_auditor", "siq_ic_chairman"],
        "legal": ["siq_ic_legal_scanner", "siq_ic_chairman"],
        "risk": ["siq_ic_risk_controller", "siq_ic_chairman"],
        "sector": ["siq_ic_sector_expert", "siq_ic_chairman"],
        "strategy": ["siq_ic_strategist", "siq_ic_chairman"],
        "business": ["siq_ic_strategist", "siq_ic_sector_expert", "siq_ic_chairman"],
    }.get(dimension, [])


def _build_items_for_pdf_archive(
    *,
    deal_id: str,
    document: dict[str, Any],
    source: dict[str, Any],
    run_dir: Path,
    parse_run_id: str,
    start_index: int,
    built_at: str,
) -> list[dict[str, Any]]:
    document_id = str(document.get("document_id") or "")
    source_id = str(source.get("source_id") or f"PM:{deal_id}:{document_id}:{parse_run_id}")
    capabilities = source.get("capabilities") if isinstance(source.get("capabilities"), dict) else {}
    financial_restricted = capabilities.get("financial_facts") != "ready"
    source_sha256 = str(document.get("sha256") or "")
    items: list[dict[str, Any]] = []
    for block_index, block in enumerate(_pdf_blocks(run_dir), start=1):
        text = _block_text(block)
        page = _block_page(block)
        if not text or page is None:
            continue
        dimension = _prospectus_dimension(text)
        block_id = str(block.get("block_id") or block.get("id") or f"block-{block_index}")
        bbox = block.get("bbox") if isinstance(block.get("bbox"), list) else None
        locator = f"prospectus.pdf:p{page}:{block_id}"
        evidence_type = "restricted" if dimension == "finance" and financial_restricted else "verified"
        sequence = start_index + len(items)
        items.append({
            "schema_version": EVIDENCE_ITEM_SCHEMA,
            "evidence_id": f"EVID-{deal_id}-{sequence:06d}",
            "source_id": source_id,
            "source_type": "primary_market_prospectus",
            "deal_id": deal_id,
            "document_id": document_id,
            "parse_run_id": parse_run_id,
            "dimension": dimension,
            "claim": _quote_text(text),
            "quote": _quote_text(text),
            "page": page,
            "block_id": block_id,
            "bbox": bbox,
            "locator": locator,
            "citation": f"{document.get('original_filename') or document_id} · p{page} · {block_id}",
            "artifact_path": f"parsed_documents/{document_id}/runs/{parse_run_id}",
            "source_path": f"parsed_documents/{document_id}/runs/{parse_run_id}/content_list_enhanced.json",
            "source_sha256": source_sha256,
            "evidence_type": evidence_type,
            "confidence": 0.8 if evidence_type == "verified" else 0.45,
            "capability_restrictions": ["financial_facts"] if evidence_type == "restricted" else [],
            "role_hints": _prospectus_role_hints(dimension),
            "source_url": f"/api/primary-market/projects/{deal_id}/materials/{document_id}/source/page/{page}",
            "artifact_url": f"/api/primary-market/projects/{deal_id}/materials/{document_id}/artifacts/content_list_enhanced.json",
            "created_at": built_at,
        })
    return items


def _dimension_for_document(document: dict[str, Any]) -> str:
    haystack = " ".join(
        str(document.get(key) or "")
        for key in ("document_type", "original_filename", "filename", "source_note")
    ).lower().replace("-", "_").replace(" ", "_")
    for dimension, needles in DIMENSION_RULES:
        if any(needle in haystack for needle in needles):
            return dimension
    return "unknown"


def _parse_int(value: Any) -> int | None:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _parse_marker(line: str) -> dict[str, Any] | None:
    match = DOC_BLOCK_RE.search(line)
    if not match:
        return None
    attrs: dict[str, str] = {}
    for attr in ATTR_RE.finditer(match.group("attrs") or ""):
        value = attr.group("value").strip("\"'")
        attrs[attr.group("key")] = value
    anchor: dict[str, Any] = {"block_id": match.group("block_id")}
    page = _parse_int(attrs.get("page"))
    if page is not None:
        anchor["page"] = page
    if attrs.get("evidence"):
        anchor["source_evidence_id"] = attrs["evidence"]
    if attrs.get("bbox"):
        try:
            anchor["bbox"] = [float(value) for value in attrs["bbox"].split(",")]
        except ValueError:
            pass
    return anchor


def _markdown_segments(markdown: str) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    current_anchor: dict[str, Any] = {}
    current_lines: list[tuple[int, str]] = []

    def flush() -> None:
        nonlocal current_lines
        if current_lines:
            segments.append({"anchor": dict(current_anchor), "lines": current_lines})
            current_lines = []

    for line_no, line in enumerate(markdown.splitlines(), start=1):
        marker = _parse_marker(line)
        if marker:
            flush()
            current_anchor = {**marker, "marker_line": line_no}
            remainder = DOC_BLOCK_RE.sub("", line).strip()
            if remainder:
                current_lines.append((line_no, remainder))
            continue
        current_lines.append((line_no, line))
    flush()
    return segments


def _clean_line(line: str) -> str:
    return HTML_COMMENT_RE.sub("", line).rstrip()


def _paragraphs(lines: list[tuple[int, str]]) -> list[list[tuple[int, str]]]:
    paragraphs: list[list[tuple[int, str]]] = []
    current: list[tuple[int, str]] = []
    for line_no, line in lines:
        cleaned = _clean_line(line)
        if not cleaned.strip():
            if current:
                paragraphs.append(current)
                current = []
            continue
        current.append((line_no, cleaned))
    if current:
        paragraphs.append(current)
    return paragraphs


def _chunk_segment(segment: dict[str, Any]) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    current: list[list[tuple[int, str]]] = []
    current_chars = 0
    for paragraph in _paragraphs(segment.get("lines") or []):
        paragraph_text = "\n".join(line for _, line in paragraph).strip()
        if not paragraph_text:
            continue
        next_chars = current_chars + len(paragraph_text) + (2 if current else 0)
        if current and next_chars > MAX_CHUNK_CHARS:
            chunks.append(_make_chunk(segment.get("anchor") or {}, current))
            current = []
            current_chars = 0
        current.append(paragraph)
        current_chars += len(paragraph_text) + (2 if current_chars else 0)
    if current:
        chunks.append(_make_chunk(segment.get("anchor") or {}, current))
    return [chunk for chunk in chunks if chunk.get("text")]


def _make_chunk(anchor: dict[str, Any], paragraphs: list[list[tuple[int, str]]]) -> dict[str, Any]:
    lines = [line for paragraph in paragraphs for line in paragraph]
    line_numbers = [line_no for line_no, _ in lines]
    text = "\n\n".join("\n".join(line for _, line in paragraph).strip() for paragraph in paragraphs).strip()
    return {
        "anchor": dict(anchor),
        "line_start": min(line_numbers) if line_numbers else None,
        "line_end": max(line_numbers) if line_numbers else None,
        "text": text,
    }


def _quote_text(text: str) -> str:
    normalized = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(normalized) <= MAX_QUOTE_CHARS:
        return normalized
    return normalized[:MAX_QUOTE_CHARS].rstrip() + "..."


def _deal_provenance_path(value: Any, *, required_prefix: str) -> str:
    raw = str(value or "").strip().replace("\\", "/").strip("/")
    path = Path(raw)
    if not raw or path.is_absolute() or ".." in path.parts or not raw.startswith(required_prefix):
        raise ValueError(f"invalid Deal provenance path: {required_prefix}")
    return path.as_posix()


def _source_anchor(chunk: dict[str, Any]) -> dict[str, Any]:
    anchor = dict(chunk.get("anchor") or {})
    line_start = chunk.get("line_start")
    line_end = chunk.get("line_end")
    if line_start:
        anchor["md_line_start"] = line_start
        anchor["md_line"] = line_start
    if line_end:
        anchor["md_line_end"] = line_end
    return anchor


def _locator(chunk: dict[str, Any]) -> str:
    line_start = chunk.get("line_start")
    line_end = chunk.get("line_end")
    if line_start and line_end:
        return f"document.md:L{line_start}-L{line_end}"
    if line_start:
        return f"document.md:L{line_start}"
    return "document.md"


def _citation(document: dict[str, Any], locator: str, anchor: dict[str, Any]) -> str:
    title = str(document.get("original_filename") or document.get("filename") or document.get("document_id") or "document")
    page = anchor.get("page")
    suffix = f" · p{page}" if page else ""
    return f"{title} · {locator}{suffix}"


def _build_items_for_document(
    *,
    deal_id: str,
    document: dict[str, Any],
    document_path: Path,
    source_path: str,
    start_index: int,
    built_at: str,
    source_type: str = "data_room_document",
    source_id: str | None = None,
    parse_run_id: str | None = None,
    capabilities: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    markdown = document_path.read_text(encoding="utf-8")
    if not markdown.strip():
        return []
    task_id = str(document.get("parse_task_id") or "")
    original_path = _deal_provenance_path(
        document.get("storage_path"),
        required_prefix="data_room/raw/",
    )
    parser_source_path = str(document.get("wiki_source_path") or "").strip()
    if not (
        parser_source_path.startswith("parsed_documents/")
        or parser_source_path.startswith("parser_results/")
    ):
        raise ValueError("invalid parser source provenance path")
    is_prospectus = source_type == "primary_market_prospectus"
    capability_map = capabilities or {}
    items: list[dict[str, Any]] = []
    for segment in _markdown_segments(markdown):
        for chunk in _chunk_segment(segment):
            quote = _quote_text(str(chunk.get("text") or ""))
            if not quote:
                continue
            dimension = _prospectus_dimension(quote) if is_prospectus else _dimension_for_document(document)
            role_hints = (
                _prospectus_role_hints(dimension)
                if is_prospectus
                else ROLE_HINTS_BY_DIMENSION.get(dimension, [])
            )
            financial_restricted = is_prospectus and capability_map.get("financial_facts") != "ready"
            evidence_type = "restricted" if dimension == "finance" and financial_restricted else "verified"
            sequence = start_index + len(items)
            anchor = _source_anchor(chunk)
            locator = _locator(chunk)
            item = {
                "schema_version": EVIDENCE_ITEM_SCHEMA,
                "evidence_id": f"EVID-{deal_id}-{sequence:06d}",
                "deal_id": deal_id,
                "document_id": document.get("document_id"),
                "parse_task_id": task_id,
                "parse_run_id": parse_run_id or document.get("current_parse_run_id"),
                "source_id": source_id or document.get("document_id"),
                "source_type": source_type,
                "source_path": source_path,
                "wiki_path": source_path,
                "wiki_sha256": document.get("wiki_sha256"),
                "wiki_source_path": source_path,
                "wiki_source_sha256": document.get("wiki_sha256"),
                "original_path": original_path,
                "original_sha256": document.get("sha256"),
                "parser_source_path": parser_source_path,
                "parser_source_sha256": document.get("wiki_source_sha256"),
                "source_anchor": anchor,
                "locator": locator,
                "citation": _citation(document, locator, anchor),
                "claim": quote,
                "quote": quote,
                "evidence_type": evidence_type,
                "dimension": dimension,
                "confidence": 0.8 if is_prospectus and evidence_type == "verified" else 0.45 if evidence_type == "restricted" else 0.6,
                "capability_restrictions": ["financial_facts"] if evidence_type == "restricted" else [],
                "role_hints": role_hints,
                "page": anchor.get("page"),
                "block_id": anchor.get("block_id"),
                "bbox": anchor.get("bbox"),
                "parser_page_url": f"/documents?task={task_id}" if task_id else None,
                "source_url": (
                    f"/api/primary-market/projects/{deal_id}/materials/{document.get('document_id')}/source/page/{anchor.get('page')}"
                    if is_prospectus and anchor.get("page")
                    else f"/api/documents/source/{task_id}/block/{anchor.get('block_id')}"
                    if task_id and anchor.get("block_id")
                    else f"/api/documents/source/{task_id}/page/{anchor.get('page')}"
                    if task_id and anchor.get("page")
                    else None
                ),
                "artifact_url": (
                    f"/api/primary-market/projects/{deal_id}/materials/{document.get('document_id')}/artifacts/result.md"
                    if is_prospectus
                    else f"/api/documents/artifact/{task_id}/document.md" if task_id else None
                ),
                "created_at": built_at,
            }
            items.append(item)
    return items


def _read_ndjson(path: Path, *, limit: int | None = None) -> tuple[list[dict[str, Any]], int]:
    items: list[dict[str, Any]] = []
    invalid = 0
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return items, invalid
    for line in lines:
        raw = line.strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            invalid += 1
            continue
        if isinstance(payload, dict):
            if limit is None or len(items) < limit:
                items.append(payload)
        else:
            invalid += 1
    return items, invalid


def _normalize_preview_limit(value: int | str | None) -> int:
    try:
        parsed = int(value) if value is not None else ITEMS_PREVIEW_LIMIT
    except (TypeError, ValueError):
        parsed = ITEMS_PREVIEW_LIMIT
    return max(1, min(parsed, ITEMS_PREVIEW_MAX_LIMIT))


def _clean_filter(value: str | None) -> str:
    return str(value or "").strip()


def _item_search_text(item: dict[str, Any]) -> str:
    fields = (
        item.get("claim"),
        item.get("quote"),
        item.get("citation"),
        item.get("locator"),
        item.get("evidence_id"),
        item.get("source_path"),
        item.get("document_id"),
    )
    return " ".join(str(value or "") for value in fields).lower()


def _matches_evidence_filters(
    item: dict[str, Any],
    *,
    q: str = "",
    dimension: str = "",
    document_id: str = "",
    source_url: str = "",
) -> bool:
    if dimension and str(item.get("dimension") or "").lower() != dimension.lower():
        return False
    if document_id and str(item.get("document_id") or "") != document_id:
        return False
    if source_url and source_url.lower() not in str(item.get("source_url") or "").lower():
        return False
    if q and q.lower() not in _item_search_text(item):
        return False
    return True


def _available_filters(items: list[dict[str, Any]], quality: dict[str, Any] | None) -> dict[str, Any]:
    dimensions = sorted({str(item.get("dimension")) for item in items if item.get("dimension")})
    document_ids = sorted({str(item.get("document_id")) for item in items if item.get("document_id")})
    quality_documents = quality.get("documents") if isinstance(quality, dict) else []
    documents: list[dict[str, Any]] = []
    if isinstance(quality_documents, list):
        for document in quality_documents:
            if isinstance(document, dict) and document.get("document_id"):
                documents.append({
                    "document_id": document.get("document_id"),
                    "filename": document.get("filename"),
                    "original_filename": document.get("original_filename"),
                    "document_type": document.get("document_type"),
                    "status": document.get("status"),
                    "items": document.get("items"),
                })
    known = {str(document.get("document_id") or "") for document in documents}
    for document_id in document_ids:
        if document_id not in known:
            documents.append({"document_id": document_id})
    return {
        "dimensions": dimensions,
        "document_ids": document_ids,
        "documents": documents,
        "limits": list(ITEMS_PREVIEW_LIMIT_OPTIONS),
    }


def _write_ndjson(path: Path, items: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in items), encoding="utf-8")


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return ""
    return digest.hexdigest()


def refresh_evidence_snapshot(
    deal_id: str,
    *,
    built_by: dict[str, Any] | None = None,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    package_dir = _require_package_dir(deal_id, wiki_root=wiki_root)
    normalized_deal_id = deal_store.validate_deal_id(deal_id)
    sources = _analysis_sources(package_dir)
    active_sources: list[dict[str, Any]] = []
    hash_lines: list[str] = []
    for source in sorted(sources, key=lambda item: str(item.get("source_id") or "")):
        if str(source.get("status") or "") not in {"ready", "ready_with_restrictions"}:
            continue
        source_id = str(source.get("source_id") or "")
        manifest_path = str(source.get("artifact_manifest_path") or "")
        manifest_hash = str(source.get("archive_manifest_sha256") or "")
        if not manifest_hash and manifest_path:
            candidate = package_dir / manifest_path
            try:
                candidate.resolve().relative_to(package_dir.resolve())
            except ValueError:
                candidate = package_dir / "__invalid__"
            manifest_hash = _sha256_path(candidate)
        active_sources.append({
            "source_id": source_id,
            "document_id": source.get("document_id"),
            "parse_run_id": source.get("parse_run_id"),
            "status": source.get("status"),
            "capabilities": source.get("capabilities") or {},
            "archive_manifest_sha256": manifest_hash,
        })
        hash_lines.append(f"{source_id}:{manifest_hash}")

    evidence_index_sha256 = _sha256_path(package_dir / "evidence" / "evidence_index.json")
    digest_payload = "\n".join([
        EVIDENCE_ITEM_SCHEMA,
        *hash_lines,
        f"evidence_index:{evidence_index_sha256}",
    ]).encode("utf-8")
    snapshot_hash = hashlib.sha256(digest_payload).hexdigest()
    snapshot_path = package_dir / "evidence" / "evidence_snapshot.json"
    previous = deal_store.read_json(snapshot_path, {}) or {}
    snapshot = {
        "schema_version": EVIDENCE_SNAPSHOT_SCHEMA,
        "deal_id": normalized_deal_id,
        "snapshot_hash": snapshot_hash,
        "active_sources": active_sources,
        "source_ids": [item["source_id"] for item in active_sources],
        "evidence_index_sha256": evidence_index_sha256,
        "evidence_contract_version": EVIDENCE_ITEM_SCHEMA,
        "created_at": deal_store.utc_now_iso(),
    }
    deal_store.write_json(snapshot_path, snapshot)
    if previous.get("snapshot_hash") != snapshot_hash:
        receipts_path = package_dir / "phases" / "startup_receipts.json"
        receipts = deal_store.read_json(receipts_path, {}) or {}
        agents = receipts.get("agents") if isinstance(receipts.get("agents"), dict) else {}
        history = (
            receipts.get("by_agent_phase")
            if isinstance(receipts.get("by_agent_phase"), dict)
            else {}
        )
        receipt_candidates = list(agents.values())
        for phases in history.values():
            if isinstance(phases, dict):
                receipt_candidates.extend(phases.values())
        receipts_changed = False
        for receipt in receipt_candidates:
            if not isinstance(receipt, dict):
                continue
            bound_hash = str(receipt.get("evidence_snapshot_hash") or "")
            if bound_hash != snapshot_hash:
                receipt["readiness_status"] = "stale"
                receipt["stale_reason"] = "evidence_snapshot_changed"
                receipt["current_evidence_snapshot_hash"] = snapshot_hash
                gate = receipt.get("gate") if isinstance(receipt.get("gate"), dict) else {}
                blocking_reasons = [
                    str(item)
                    for item in gate.get("blocking_reasons") or []
                    if str(item or "").strip()
                ]
                if "evidence_snapshot_changed" not in blocking_reasons:
                    blocking_reasons.append("evidence_snapshot_changed")
                receipt["gate"] = {
                    **gate,
                    "allowed_to_speak": False,
                    "blocking_reasons": blocking_reasons,
                }
                receipts_changed = True
        if receipts_changed:
            receipts["updated_at"] = snapshot["created_at"]
            deal_store.write_json(receipts_path, receipts)
        decision = deal_store.read_json(package_dir / "phases" / "r4_decision.json", {}) or {}
        confirmation = decision.get("human_confirmation") if isinstance(decision.get("human_confirmation"), dict) else {}
        if previous.get("snapshot_hash") and str(confirmation.get("status") or "") in {
            "confirmed",
            "approved",
            "overridden",
        }:
            workflow_path = package_dir / "phases" / "workflow_state.json"
            workflow = deal_store.read_json(workflow_path, {}) or {}
            workflow["status"] = "decision_review_required"
            workflow["decision_review_required"] = True
            workflow["decision_review_reason"] = "evidence_snapshot_changed"
            workflow["confirmed_decision_snapshot_hash"] = previous.get("snapshot_hash")
            workflow["current_evidence_snapshot_hash"] = snapshot_hash
            workflow["updated_at"] = snapshot["created_at"]
            deal_store.write_json(workflow_path, workflow)
            project_meta_path = package_dir / "project_meta.json"
            project_meta = deal_store.read_json(project_meta_path, {}) or {}
            project_meta["status"] = "decision_review_required"
            project_meta["decision_review_required"] = True
            project_meta["decision_review_reason"] = "evidence_snapshot_changed"
            project_meta["updated_at"] = snapshot["created_at"]
            deal_store.write_json(project_meta_path, project_meta)
        deal_store.append_audit_event(
            normalized_deal_id,
            {
                "event_type": "deal_evidence_snapshot_changed",
                "previous_snapshot_hash": previous.get("snapshot_hash"),
                "snapshot_hash": snapshot_hash,
                "source_ids": snapshot["source_ids"],
                "built_by": built_by,
            },
            wiki_root=wiki_root,
        )
    return snapshot


def _policy_gate() -> dict[str, Any]:
    policy = ic_policy.read_ic_workflow_policy()
    gate = policy.get("evidence_gate") if isinstance(policy.get("evidence_gate"), dict) else {}
    return {
        "policy_version": policy.get("version"),
        "required_verified_items": int(gate.get("required_verified_items") or 0),
        "required_dimensions": list(gate.get("required_dimensions") or []),
    }


def _gate(gate_id: str, status: str, message: str, **details: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"id": gate_id, "status": status, "message": message}
    if details:
        payload["details"] = details
    return payload


def _quality_status(*, errors: list[str], warnings: list[str]) -> str:
    if errors:
        return "fail"
    if warnings:
        return "warn"
    return "pass"


def _item_summary(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "evidence_id": item.get("evidence_id"),
        "document_id": item.get("document_id"),
        "dimension": item.get("dimension"),
        "locator": item.get("locator"),
        "quote_preview": str(item.get("quote") or item.get("claim") or "")[:240],
    }


def _sync_manifest_evidence(package_dir: Path, index: dict[str, Any], quality: dict[str, Any]) -> None:
    manifest = deal_store.read_json(package_dir / "manifest.json", {}) or {}
    evidence = manifest.get("evidence") if isinstance(manifest.get("evidence"), dict) else {}
    evidence.update({
        "index_path": "evidence/evidence_index.json",
        "items_path": "evidence/evidence_items.ndjson",
        "quality_path": "evidence/evidence_quality_report.json",
        "last_build": {
            "build_mode": BUILD_MODE,
            "status": quality.get("status"),
            "built_at": quality.get("built_at"),
            "counts": quality.get("counts") or {},
        },
    })
    manifest["evidence"] = evidence
    manifest["updated_at"] = deal_store.utc_now_iso()
    deal_store.write_json(package_dir / "manifest.json", manifest)


def _sync_manifest_ingest_dry_run(package_dir: Path, report: dict[str, Any]) -> None:
    manifest = deal_store.read_json(package_dir / "manifest.json", {}) or {}
    evidence = manifest.get("evidence") if isinstance(manifest.get("evidence"), dict) else {}
    evidence["ingest_dry_run_path"] = "evidence/evidence_ingest_dry_run.json"
    evidence["last_ingest_dry_run"] = {
        "status": report.get("status"),
        "created_at": report.get("created_at"),
        "counts": report.get("counts") or {},
        "postgres_written": False,
        "milvus_written": False,
    }
    manifest["evidence"] = evidence
    manifest["updated_at"] = deal_store.utc_now_iso()
    deal_store.write_json(package_dir / "manifest.json", manifest)


def _as_text(value: Any) -> str:
    return str(value or "").strip()


def _ingest_status(errors: list[str], warnings: list[str]) -> str:
    if errors:
        return "fail"
    if warnings:
        return "warn"
    return "pass"


def _stable_hash(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _readiness_status(checks: list[dict[str, Any]]) -> str:
    statuses = {str(check.get("status") or "") for check in checks}
    if "fail" in statuses:
        return "fail"
    if "warn" in statuses:
        return "warn"
    return "pass"


def _readiness_check(check_id: str, status: str, message: str, **details: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": check_id,
        "status": status,
        "message": message,
    }
    if details:
        payload["details"] = details
    return payload


def _ingest_readiness_checks(
    *,
    errors: list[str],
    quality_status: Any,
    counts: dict[str, Any],
    postgres_write_enabled: bool,
    milvus_write_enabled: bool,
) -> list[dict[str, Any]]:
    items_valid = int(counts.get("items_valid") or 0)
    rows_planned = int(counts.get("postgres_rows_planned") or 0)
    chunks_planned = int(counts.get("milvus_chunks_planned") or 0)
    checks = [
        _readiness_check(
            "items.required_fields",
            "fail" if errors else "pass",
            "Evidence items satisfy ingest required fields" if not errors else "Evidence items have ingest blockers",
            errors=errors,
        ),
        _readiness_check(
            "quality.status",
            "pass" if quality_status == "pass" else "fail",
            "Evidence quality report is pass" if quality_status == "pass" else "Evidence quality must be pass before real ingest",
            quality_status=quality_status,
        ),
        _readiness_check(
            "plan.count_consistency",
            "pass" if items_valid == rows_planned == chunks_planned else "fail",
            "Planned PostgreSQL rows and Milvus chunks match valid evidence items"
            if items_valid == rows_planned == chunks_planned
            else "Planned ingest counts are inconsistent",
            items_valid=items_valid,
            postgres_rows_planned=rows_planned,
            milvus_chunks_planned=chunks_planned,
        ),
        _readiness_check(
            "target.postgres_write",
            "pass" if postgres_write_enabled else "warn",
            "PostgreSQL write target is enabled" if postgres_write_enabled else "PostgreSQL write target is disabled in dry-run",
            write_enabled=postgres_write_enabled,
        ),
        _readiness_check(
            "target.milvus_write",
            "pass" if milvus_write_enabled else "warn",
            "Milvus write target is enabled" if milvus_write_enabled else "Milvus write target is disabled in dry-run",
            write_enabled=milvus_write_enabled,
        ),
    ]
    return checks


def _postgres_row_plan(item: dict[str, Any]) -> dict[str, Any]:
    anchor = item.get("source_anchor") if isinstance(item.get("source_anchor"), dict) else {}
    return {
        "schema_version": "siq_deal_evidence_row_v1",
        "deal_id": item.get("deal_id"),
        "document_id": item.get("document_id"),
        "evidence_id": item.get("evidence_id"),
        "artifact_path": item.get("source_path"),
        "source_path": item.get("source_path"),
        "source_type": item.get("source_type"),
        "evidence_type": item.get("evidence_type"),
        "dimension": item.get("dimension"),
        "claim": item.get("claim"),
        "quote": item.get("quote"),
        "citation": item.get("citation"),
        "confidence": item.get("confidence"),
        "locator": item.get("locator"),
        "page": anchor.get("page"),
        "block_id": anchor.get("block_id"),
        "md_line_start": anchor.get("md_line_start"),
        "md_line_end": anchor.get("md_line_end"),
        "source_url": item.get("source_url"),
        "artifact_url": item.get("artifact_url"),
        "created_at": item.get("created_at"),
    }


def _milvus_chunk_plan(item: dict[str, Any]) -> dict[str, Any]:
    role_hints = item.get("role_hints") if isinstance(item.get("role_hints"), list) else []
    text = _as_text(item.get("quote") or item.get("claim"))
    return {
        "schema_version": "siq_deal_chunk_v1",
        "collection": "siq_deal_shared",
        "deal_id": item.get("deal_id"),
        "document_id": item.get("document_id"),
        "evidence_id": item.get("evidence_id"),
        "source_path": item.get("source_path"),
        "source_type": item.get("source_type"),
        "confidence": item.get("confidence"),
        "role_hint": role_hints[0] if role_hints else None,
        "role_hints": role_hints,
        "citation": item.get("citation"),
        "dimension": item.get("dimension"),
        "text": text,
        "text_length": len(text),
        "source_url": item.get("source_url"),
        "artifact_url": item.get("artifact_url"),
    }


def build_deal_evidence_ingest_dry_run(
    deal_id: str,
    *,
    created_by: dict[str, Any] | None = None,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    package_dir = _require_package_dir(deal_id, wiki_root=wiki_root)
    normalized_deal_id = deal_store.validate_deal_id(deal_id)
    created_at = deal_store.utc_now_iso()
    items, invalid_lines = _read_ndjson(package_dir / "evidence" / "evidence_items.ndjson")
    quality = deal_store.read_json(package_dir / "evidence" / "evidence_quality_report.json", {}) or {}

    errors: list[str] = []
    warnings: list[str] = []
    if invalid_lines:
        errors.append(f"evidence_items.ndjson has {invalid_lines} invalid lines")
    if not items:
        errors.append("No evidence items found. Build evidence package before ingest dry-run.")
    quality_status = quality.get("status") if isinstance(quality, dict) else None
    if quality_status == "fail":
        warnings.append("Evidence quality status is fail; dry-run generated for inspection only.")
    elif quality_status == "warn":
        warnings.append("Evidence quality status is warn; review quality report before real ingest.")

    required_fields = ("evidence_id", "deal_id", "document_id", "source_path", "quote", "evidence_type", "dimension")
    postgres_rows: list[dict[str, Any]] = []
    milvus_chunks: list[dict[str, Any]] = []
    item_errors: list[dict[str, Any]] = []
    seen_evidence_ids: set[str] = set()
    duplicate_evidence_ids: set[str] = set()
    for item in items:
        evidence_id = _as_text(item.get("evidence_id"))
        missing = [field for field in required_fields if not _as_text(item.get(field))]
        if evidence_id in seen_evidence_ids:
            duplicate_evidence_ids.add(evidence_id)
        if evidence_id:
            seen_evidence_ids.add(evidence_id)
        if missing:
            item_errors.append({"evidence_id": evidence_id or None, "missing": missing})
            continue
        postgres_rows.append(_postgres_row_plan(item))
        milvus_chunks.append(_milvus_chunk_plan(item))

    if duplicate_evidence_ids:
        errors.append(f"Duplicate evidence_id values: {', '.join(sorted(duplicate_evidence_ids))}")
    if item_errors:
        errors.append(f"{len(item_errors)} evidence items are missing required ingest fields")

    counts = {
        "items_total": len(items),
        "items_valid": len(postgres_rows),
        "items_invalid": len(item_errors),
        "postgres_rows_planned": len(postgres_rows),
        "milvus_chunks_planned": len(milvus_chunks),
        "invalid_ndjson_lines": invalid_lines,
    }
    postgres_write_enabled = False
    milvus_write_enabled = False
    preflight_checks = _ingest_readiness_checks(
        errors=errors,
        quality_status=quality_status,
        counts=counts,
        postgres_write_enabled=postgres_write_enabled,
        milvus_write_enabled=milvus_write_enabled,
    )
    plan_hash = _stable_hash({
        "deal_id": normalized_deal_id,
        "mode": INGEST_DRY_RUN_MODE,
        "counts": counts,
        "postgres_rows": postgres_rows,
        "milvus_chunks": milvus_chunks,
    })
    report = {
        "schema_version": EVIDENCE_INGEST_DRY_RUN_SCHEMA,
        "deal_id": normalized_deal_id,
        "mode": INGEST_DRY_RUN_MODE,
        "status": _ingest_status(errors, warnings),
        "write_readiness": _readiness_status(preflight_checks),
        "preflight_checks": preflight_checks,
        "plan_hash": plan_hash,
        "created_at": created_at,
        "postgres_written": False,
        "milvus_written": False,
        "target_postgres": {
            "schema": "deal_os",
            "tables": ["deal_os.evidence_items"],
            "write_enabled": postgres_write_enabled,
        },
        "target_milvus": {
            "collections": ["siq_deal_shared"],
            "write_enabled": milvus_write_enabled,
        },
        "counts": counts,
        "quality_status": quality_status,
        "required_fields": list(required_fields),
        "errors": errors,
        "warnings": warnings,
        "item_errors": item_errors,
        "postgres_rows_preview": postgres_rows[:20],
        "milvus_chunks_preview": milvus_chunks[:20],
        "created_by": created_by,
    }
    evidence_dir = package_dir / "evidence"
    deal_store.write_json(evidence_dir / "evidence_ingest_dry_run.json", report)
    _sync_manifest_ingest_dry_run(package_dir, report)
    deal_store.append_audit_event(
        normalized_deal_id,
        {
            "event_type": "deal_evidence_ingest_dry_run_generated",
            "status": report["status"],
            "counts": counts,
            "created_by": created_by,
        },
        wiki_root=wiki_root,
    )
    return report


def read_deal_evidence_ingest_dry_run(
    deal_id: str,
    *,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    package_dir = _require_package_dir(deal_id, wiki_root=wiki_root)
    report = deal_store.read_json(package_dir / "evidence" / "evidence_ingest_dry_run.json", None)
    return {
        "deal_id": deal_store.validate_deal_id(deal_id),
        "ingest_dry_run": report if isinstance(report, dict) else None,
    }


def build_deal_evidence_package(
    deal_id: str,
    *,
    built_by: dict[str, Any] | None = None,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    package_dir = _require_package_dir(deal_id, wiki_root=wiki_root)
    normalized_deal_id = deal_store.validate_deal_id(deal_id)
    built_at = deal_store.utc_now_iso()
    policy_gate = _policy_gate()
    documents, invalid_metadata_files = _read_document_metadata(package_dir)
    active_pdf_sources = _active_pdf_source_by_document(package_dir)

    items: list[dict[str, Any]] = []
    document_results: list[dict[str, Any]] = []
    warnings: list[str] = []
    errors: list[str] = []
    if invalid_metadata_files:
        errors.append(f"Invalid document metadata files: {', '.join(invalid_metadata_files)}")

    for document in documents:
        document_id = str(document.get("document_id") or "")
        is_pdf_prospectus = (
            document.get("document_type") == "prospectus"
            and str(document.get("parser_kind") or "pdf") == "pdf"
        )
        result = {
            "document_id": document_id,
            "original_filename": document.get("original_filename"),
            "document_type": document.get("document_type"),
            "parse_task_id": document.get("parse_task_id"),
            "parsed_artifact_path": document.get("parsed_artifact_path"),
            "dimension": _dimension_for_document(document),
            "status": "not_bound",
            "items": 0,
        }
        if is_pdf_prospectus:
            source = active_pdf_sources.get(document_id)
            if not source:
                result["status"] = "inactive_analysis_source"
                result["reason"] = "Prospectus analysis source is not active"
                document_results.append(result)
                warnings.append(f"{document_id}: inactive_analysis_source")
                continue
            run_dir, parse_run_id, missing_reason = _safe_pdf_run_dir(package_dir, document, source)
            result.update({
                "parser_kind": "pdf",
                "parse_run_id": parse_run_id,
                "source_id": source.get("source_id"),
                "capabilities": source.get("capabilities") or {},
            })
            if missing_reason or run_dir is None or parse_run_id is None:
                result["status"] = missing_reason or "missing_parse_run_archive"
                result["reason"] = result["status"]
                document_results.append(result)
                errors.append(f"{document_id}: {result['status']}")
                continue
            source_markdown = run_dir / "document.md"
            projection = primary_market_wiki.project_material_to_company_wiki_safe(
                deal_id=normalized_deal_id,
                document_id=document_id,
                source_path=source_markdown if source_markdown.is_file() else None,
                structured_artifact_dir=run_dir,
                parse_task_id=str(document.get("parse_task_id") or "") or None,
                parse_run_id=parse_run_id,
                wiki_root=wiki_root,
                projected_by=built_by,
            )
            document = deal_store.read_json(
                package_dir / "data_room" / "metadata" / f"{document_id}.json",
                document,
            ) or document
            wiki_document_path, wiki_source_path, wiki_reason = _company_wiki_candidate(
                package_dir,
                document,
            )
            if wiki_reason or wiki_document_path is None:
                result["status"] = wiki_reason or "company_wiki_projection_missing"
                result["reason"] = result["status"]
                document_results.append(result)
                errors.append(f"{document_id}: {result['status']}")
                continue
            document_items = _build_items_for_document(
                deal_id=normalized_deal_id,
                document=document,
                document_path=wiki_document_path,
                source_path=wiki_source_path,
                start_index=len(items) + 1,
                built_at=built_at,
                source_type="primary_market_prospectus",
                source_id=str(source.get("source_id") or ""),
                parse_run_id=parse_run_id,
                capabilities=source.get("capabilities") if isinstance(source.get("capabilities"), dict) else {},
            )
            if not document_items:
                result["status"] = "empty_company_wiki"
                result["reason"] = "No indexable company Wiki text found"
                document_results.append(result)
                errors.append(f"{document_id}: empty_company_wiki")
                continue
            items.extend(document_items)
            result["status"] = "indexed"
            result["items"] = len(document_items)
            result["source_path"] = wiki_source_path
            result["wiki_path"] = projection.get("wiki_path")
            result["wiki_sha256"] = projection.get("wiki_sha256")
            document_results.append(result)
            continue
        if not document.get("parse_task_id"):
            result["reason"] = "No parser task is bound"
            document_results.append(result)
            warnings.append(f"{document_id}: parser task is not bound")
            continue

        wiki_document_path, wiki_source_path, wiki_reason = _company_wiki_candidate(
            package_dir,
            document,
        )
        projection: dict[str, Any] = {
            "wiki_path": wiki_source_path,
            "wiki_sha256": document.get("wiki_sha256"),
        }
        if wiki_reason or wiki_document_path is None:
            try:
                document_path, _parser_source_path, missing_reason = _document_md_candidate(document)
            except ValueError as exc:
                result["status"] = "invalid_parser_binding"
                result["reason"] = str(exc)
                document_results.append(result)
                warnings.append(f"{document_id}: invalid parser binding")
                continue
            if missing_reason or not document_path:
                result["status"] = missing_reason or "missing_document_md"
                result["reason"] = result["status"]
                document_results.append(result)
                warnings.append(f"{document_id}: {result['status']}")
                continue
            projection = primary_market_wiki.project_material_to_company_wiki_safe(
                normalized_deal_id,
                document_id,
                source_path=document_path,
                parse_task_id=str(document.get("parse_task_id") or "") or None,
                parse_run_id=str(document.get("current_parse_run_id") or "") or None,
                wiki_root=wiki_root,
                projected_by=built_by,
            )
            document = deal_store.read_json(
                package_dir / "data_room" / "metadata" / f"{document_id}.json",
                document,
            ) or document
            wiki_document_path, wiki_source_path, wiki_reason = _company_wiki_candidate(
                package_dir,
                document,
            )
        if wiki_reason or wiki_document_path is None:
            result["status"] = wiki_reason or "company_wiki_projection_missing"
            result["reason"] = result["status"]
            document_results.append(result)
            errors.append(f"{document_id}: {result['status']}")
            continue

        document_items = _build_items_for_document(
            deal_id=normalized_deal_id,
            document=document,
            document_path=wiki_document_path,
            source_path=wiki_source_path,
            start_index=len(items) + 1,
            built_at=built_at,
        )
        if not document_items:
            result["status"] = "empty_document_md"
            result["reason"] = "document.md is empty or contains no indexable text"
            document_results.append(result)
            warnings.append(f"{document_id}: empty_document_md")
            continue

        items.extend(document_items)
        result["status"] = "indexed"
        result["items"] = len(document_items)
        result["source_path"] = wiki_source_path
        result["wiki_path"] = projection.get("wiki_path")
        result["wiki_sha256"] = projection.get("wiki_sha256")
        document_results.append(result)

    documents_bound = sum(
        1
        for item in documents
        if item.get("parse_task_id") or str(item.get("document_id") or "") in active_pdf_sources
    )
    documents_indexed = sum(1 for item in document_results if item.get("status") == "indexed")
    verified_items = [item for item in items if item.get("evidence_type") == "verified"]
    dimensions = sorted({str(item.get("dimension")) for item in verified_items if item.get("dimension")})
    missing_dimensions = sorted(set(policy_gate["required_dimensions"]) - set(dimensions))
    required_verified_items = int(policy_gate["required_verified_items"])

    if not documents:
        warnings.append("No data-room documents found")
    if documents_bound == 0:
        warnings.append("No data-room documents are bound to parser tasks")
    if documents_bound > 0 and documents_indexed == 0:
        errors.append("All parser-bound documents failed evidence indexing")
    if len(verified_items) < required_verified_items:
        warnings.append(f"Verified evidence item count {len(verified_items)}/{required_verified_items}")
    if missing_dimensions:
        warnings.append(f"Missing evidence dimensions: {', '.join(missing_dimensions)}")

    evidence_dir = package_dir / "evidence"
    items_path = evidence_dir / "evidence_items.ndjson"
    _write_ndjson(items_path, items)
    _, invalid_ndjson_lines = _read_ndjson(items_path)
    if invalid_ndjson_lines:
        errors.append(f"evidence_items.ndjson has {invalid_ndjson_lines} invalid lines")

    counts = {
        "documents_total": len(documents),
        "documents_bound": documents_bound,
        "documents_indexed": documents_indexed,
        "documents_skipped": len([item for item in document_results if item.get("status") != "indexed"]),
        "items": len(items),
        "verified_items": len(verified_items),
        "invalid_metadata_files": len(invalid_metadata_files),
        "invalid_ndjson_lines": invalid_ndjson_lines,
    }
    gates = [
        _gate(
            "document_bindings",
            "pass" if documents_bound else "warn",
            f"{documents_bound}/{len(documents)} documents have parser task bindings",
        ),
        _gate(
            "parser_artifacts",
            "pass" if documents_bound == documents_indexed else "warn" if documents_indexed else "fail" if documents_bound else "warn",
            f"{documents_indexed}/{documents_bound} bound documents were indexed",
        ),
        _gate(
            "verified_items",
            "pass" if len(verified_items) >= required_verified_items else "warn",
            f"{len(verified_items)}/{required_verified_items} verified evidence items",
        ),
        _gate(
            "dimension_coverage",
            "pass" if not missing_dimensions else "warn",
            "Required dimensions are covered" if not missing_dimensions else "Required dimensions are missing",
            missing_dimensions=missing_dimensions,
            dimensions=dimensions,
        ),
        _gate(
            "ndjson_valid",
            "pass" if invalid_ndjson_lines == 0 else "fail",
            "evidence_items.ndjson is valid" if invalid_ndjson_lines == 0 else "evidence_items.ndjson contains invalid lines",
        ),
    ]

    quality = {
        "schema_version": EVIDENCE_QUALITY_SCHEMA,
        "deal_id": normalized_deal_id,
        "status": _quality_status(errors=errors, warnings=warnings),
        "build_mode": BUILD_MODE,
        "llm_used": False,
        "agent_used": False,
        "milvus_written": False,
        "built_at": built_at,
        "policy_version": policy_gate.get("policy_version"),
        "required_verified_items": required_verified_items,
        "required_dimensions": policy_gate["required_dimensions"],
        "item_count": len(items),
        "verified_count": len(verified_items),
        "dimensions": dimensions,
        "missing_dimensions": missing_dimensions,
        "counts": counts,
        "gates": gates,
        "documents": document_results,
        "warnings": sorted(set(warnings)),
        "errors": errors,
    }
    index = {
        "schema_version": EVIDENCE_INDEX_SCHEMA,
        "deal_id": normalized_deal_id,
        "build_mode": BUILD_MODE,
        "llm_used": False,
        "agent_used": False,
        "milvus_written": False,
        "built_at": built_at,
        "paths": {
            "items": "evidence/evidence_items.ndjson",
            "quality": "evidence/evidence_quality_report.json",
        },
        "counts": counts,
        "documents": document_results,
        "items": [_item_summary(item) for item in items],
    }

    deal_store.write_json(evidence_dir / "evidence_index.json", index)
    deal_store.write_json(evidence_dir / "evidence_quality_report.json", quality)
    snapshot = refresh_evidence_snapshot(
        normalized_deal_id,
        built_by=built_by,
        wiki_root=wiki_root,
    )
    _sync_manifest_evidence(package_dir, index, quality)
    deal_store.append_audit_event(
        normalized_deal_id,
        {
            "event_type": "deal_evidence_built",
            "status": quality["status"],
            "counts": counts,
            "built_by": built_by,
        },
        wiki_root=wiki_root,
    )

    result = {
        "deal_id": normalized_deal_id,
        "status": quality["status"],
        "counts": counts,
        "evidence_index": index,
        "quality_report": quality,
        "items_preview": items[:ITEMS_PREVIEW_LIMIT],
        "index": index,
        "quality": quality,
        "documents": document_results,
        "evidence_snapshot": snapshot,
    }
    if deal_evidence_milvus.primary_market_milvus_index_enabled():
        try:
            result["milvus_index"] = deal_evidence_milvus.index_deal_evidence_milvus(
                normalized_deal_id,
                created_by=built_by,
                wiki_root=wiki_root,
            )
        except deal_evidence_milvus.DealEvidenceMilvusIndexError as exc:
            result["milvus_index"] = exc.receipt or {
                "status": "failed",
                "deal_id": normalized_deal_id,
                "error": str(exc)[:300],
            }
        except (FileNotFoundError, ValueError) as exc:
            result["milvus_index"] = {
                "status": "failed",
                "deal_id": normalized_deal_id,
                "error": str(exc)[:300],
            }
    primary_market_wiki.rebuild_primary_market_wiki(
        normalized_deal_id,
        wiki_root=wiki_root,
        append_audit=False,
    )
    return result


def read_deal_evidence_package(
    deal_id: str,
    *,
    wiki_root: Path | str | None = None,
    preview_limit: int = ITEMS_PREVIEW_LIMIT,
    q: str | None = None,
    dimension: str | None = None,
    document_id: str | None = None,
    source_url: str | None = None,
) -> dict[str, Any]:
    package_dir = _require_package_dir(deal_id, wiki_root=wiki_root)
    normalized_deal_id = deal_store.validate_deal_id(deal_id)
    index = deal_store.read_json(package_dir / "evidence" / "evidence_index.json", None)
    quality = deal_store.read_json(package_dir / "evidence" / "evidence_quality_report.json", None)
    snapshot = deal_store.read_json(package_dir / "evidence" / "evidence_snapshot.json", None)
    milvus_index = deal_store.read_json(
        package_dir / deal_evidence_milvus.MILVUS_INDEX_RECEIPT_PATH,
        None,
    )
    wiki = deal_store.read_json(package_dir / primary_market_wiki.CATALOG_PATH, None)
    items, invalid_lines = _read_ndjson(package_dir / "evidence" / "evidence_items.ndjson")
    normalized_limit = _normalize_preview_limit(preview_limit)
    applied_filters = {
        "q": _clean_filter(q),
        "dimension": _clean_filter(dimension),
        "document_id": _clean_filter(document_id),
        "source_url": _clean_filter(source_url),
        "limit": normalized_limit,
    }
    filtered_items = [
        item for item in items
        if _matches_evidence_filters(
            item,
            q=applied_filters["q"],
            dimension=applied_filters["dimension"],
            document_id=applied_filters["document_id"],
            source_url=applied_filters["source_url"],
        )
    ]
    if isinstance(quality, dict) and invalid_lines:
        quality = dict(quality)
        quality.setdefault("warnings", [])
        if isinstance(quality["warnings"], list):
            quality["warnings"].append(f"evidence_items.ndjson has {invalid_lines} invalid lines")
    if isinstance(milvus_index, dict):
        milvus_index = dict(milvus_index)
        current_snapshot_hash = str(snapshot.get("snapshot_hash") or "") if isinstance(snapshot, dict) else ""
        receipt_snapshot_hash = str(milvus_index.get("snapshot_hash") or "")
        is_current = bool(current_snapshot_hash and receipt_snapshot_hash == current_snapshot_hash)
        milvus_index["freshness"] = {
            "status": "current" if is_current else "stale",
            "is_current": is_current,
            "current_snapshot_hash": current_snapshot_hash or None,
            "receipt_snapshot_hash": receipt_snapshot_hash or None,
        }
    return {
        "deal_id": normalized_deal_id,
        "status": quality.get("status") if isinstance(quality, dict) else None,
        "counts": quality.get("counts") if isinstance(quality, dict) else {},
        "evidence_index": index if isinstance(index, dict) else None,
        "quality_report": quality if isinstance(quality, dict) else None,
        "evidence_snapshot": snapshot if isinstance(snapshot, dict) else None,
        "milvus_index": milvus_index if isinstance(milvus_index, dict) else None,
        "wiki": wiki if isinstance(wiki, dict) else None,
        "items_preview": filtered_items[:normalized_limit],
        "matched_count": len(filtered_items),
        "total_item_count": len(items),
        "applied_filters": applied_filters,
        "available_filters": _available_filters(items, quality if isinstance(quality, dict) else None),
        "index": index if isinstance(index, dict) else None,
        "quality": quality if isinstance(quality, dict) else None,
    }


def read_deal_evidence_quality(
    deal_id: str,
    *,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    package_dir = _require_package_dir(deal_id, wiki_root=wiki_root)
    quality = deal_store.read_json(package_dir / "evidence" / "evidence_quality_report.json", None)
    return {
        "deal_id": deal_store.validate_deal_id(deal_id),
        "quality_report": quality if isinstance(quality, dict) else None,
        "quality": quality if isinstance(quality, dict) else None,
    }


def get_deal_evidence_item(
    deal_id: str,
    evidence_id: str,
    *,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    package_dir = _require_package_dir(deal_id, wiki_root=wiki_root)
    normalized = str(evidence_id or "").strip()
    items, _ = _read_ndjson(package_dir / "evidence" / "evidence_items.ndjson")
    for item in items:
        if item.get("evidence_id") == normalized:
            return {"deal_id": deal_store.validate_deal_id(deal_id), "evidence": item}
    raise FileNotFoundError(normalized)
