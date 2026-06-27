"""Build normalized SIQ document parser artifacts."""

from __future__ import annotations

import json
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from contracts import (
    APP_VERSION,
    ParseConfig,
    ParseOutput,
    SCHEMA_BLOCKS,
    SCHEMA_COMPARISON_MAP,
    SCHEMA_DOCUMENT_FULL,
    SCHEMA_FIGURES,
    SCHEMA_LAYOUT_BLOCKS,
    SCHEMA_LOGICAL_TABLES,
    SCHEMA_MANIFEST,
    SCHEMA_QUALITY,
    SCHEMA_READING_ORDER,
    SCHEMA_SOURCE_MAP,
    SCHEMA_TABLES,
    SourceFile,
)
from figures import figures_with_missing_bbox
from quality import ratio, warning_status
from source_map import evidence_id as make_evidence_id
from source_map import source_map_coverage
from table_merge import empty_table_relations, single_fragment_logical_table


CORE_ARTIFACTS = [
    "manifest.json",
    "document.md",
    "document_full.json",
    "blocks.json",
    "blocks.ndjson",
    "tables.json",
    "table_index.json",
    "logical_tables.json",
    "table_relations.json",
    "figures.json",
    "figure_index.json",
    "source_map.json",
    "quality_report.json",
    "layout_blocks.json",
    "reading_order.json",
    "comparison_map.json",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def build_artifacts(
    *,
    task_id: str,
    result_dir: Path,
    source: SourceFile,
    config: ParseConfig,
    output: ParseOutput,
    source_type: str,
    source_url: str = "",
) -> dict[str, Any]:
    result_dir.mkdir(parents=True, exist_ok=True)
    (result_dir / "raw" / "original").mkdir(parents=True, exist_ok=True)
    (result_dir / "images" / "original").mkdir(parents=True, exist_ok=True)
    (result_dir / "images" / "crops").mkdir(parents=True, exist_ok=True)
    (result_dir / "images" / "page_previews").mkdir(parents=True, exist_ok=True)
    (result_dir / "exports").mkdir(parents=True, exist_ok=True)
    (result_dir / "extraction").mkdir(parents=True, exist_ok=True)

    original_copy = result_dir / "raw" / "original" / source.filename
    if source.path.resolve() != original_copy.resolve():
        shutil.copy2(source.path, original_copy)
    if output.document_kind == "image":
        image_copy = result_dir / "images" / "original" / source.filename
        if source.path.resolve() != image_copy.resolve():
            shutil.copy2(source.path, image_copy)
    if output.raw_artifacts_dir:
        raw_source_dir = Path(output.raw_artifacts_dir)
        if raw_source_dir.exists() and raw_source_dir.is_dir():
            raw_target_dir = result_dir / "raw" / "mineru"
            shutil.rmtree(raw_target_dir, ignore_errors=True)
            shutil.copytree(raw_source_dir, raw_target_dir, ignore=shutil.ignore_patterns("*.tmp", "__pycache__", ".pytest_cache"))

    blocks = _normalize_blocks(task_id, output.blocks, source)
    figures = output.figures or []
    source_map = _build_source_map(task_id, blocks, output.tables, figures)
    layout_blocks = _build_layout_blocks(task_id, blocks)
    reading_order = _build_reading_order(task_id, blocks)
    comparison_map = _build_comparison_map(task_id, blocks)
    tables_payload = _build_tables(task_id, output.tables)
    logical_tables = _build_logical_tables(task_id, output.tables)
    table_relations = _empty_table_relations(task_id)
    figures_payload = _build_figures(task_id, figures)
    quality = _build_quality_report(task_id, output, blocks, source_map)

    manifest = {
        "schema_version": SCHEMA_MANIFEST,
        "task_id": task_id,
        "data_id": config.data_id,
        "filename": source.filename,
        "original_extension": source.extension,
        "mime_type": source.mime_type,
        "source_type": source_type,
        "source_url": source_url,
        "file_size": source.file_size,
        "file_sha256": source.sha256,
        "document_kind": output.document_kind,
        "parser_provider": output.provider_name,
        "parser_version": APP_VERSION,
        "upstream_parser_version": output.upstream_parser_version,
        "parse_config": config.to_manifest(),
        "raw_artifacts": "raw/mineru" if output.raw_artifacts_dir else "",
        "status": "completed" if quality["overall_status"] == "pass" else "completed_with_warnings",
        "quality_status": quality["overall_status"],
        "created_at": now_iso(),
        "completed_at": now_iso(),
        "external_processing": False,
        "artifact_hashes": {},
    }

    document_full = {
        "schema_version": SCHEMA_DOCUMENT_FULL,
        "task_id": task_id,
        "manifest": manifest,
        "artifacts": {name: {"path": name} for name in CORE_ARTIFACTS},
        "summary": {
            "block_count": len(blocks),
            "table_count": len(output.tables),
            "image_count": len(figures),
            "page_count": output.page_count,
        },
    }

    (result_dir / "document.md").write_text(output.markdown or "", encoding="utf-8")
    write_json(result_dir / "manifest.json", manifest)
    write_json(result_dir / "document_full.json", document_full)
    write_json(result_dir / "blocks.json", {"schema_version": SCHEMA_BLOCKS, "task_id": task_id, "blocks": blocks})
    (result_dir / "blocks.ndjson").write_text(
        "".join(json.dumps(block, ensure_ascii=False) + "\n" for block in blocks),
        encoding="utf-8",
    )
    write_json(result_dir / "tables.json", tables_payload)
    write_json(result_dir / "table_index.json", _build_table_index(task_id, output.tables))
    write_json(result_dir / "logical_tables.json", logical_tables)
    write_json(result_dir / "table_relations.json", table_relations)
    write_json(result_dir / "table_merge_corrections.json", {"schema_version": "document_table_merge_corrections_v1", "task_id": task_id, "relations": {}, "manual_logical_tables": []})
    write_json(result_dir / "figures.json", figures_payload)
    write_json(result_dir / "figure_index.json", _build_figure_index(task_id, figures))
    write_json(result_dir / "source_map.json", {"schema_version": SCHEMA_SOURCE_MAP, "task_id": task_id, "sources": source_map})
    write_json(result_dir / "quality_report.json", quality)
    write_json(result_dir / "layout_blocks.json", layout_blocks)
    write_json(result_dir / "reading_order.json", reading_order)
    write_json(result_dir / "comparison_map.json", comparison_map)
    _write_default_extraction_files(result_dir, task_id)
    _build_zip(result_dir)
    return manifest


def artifact_summary(task_id: str, result_dir: Path) -> dict[str, dict[str, Any]]:
    summary: dict[str, dict[str, Any]] = {}
    for name in CORE_ARTIFACTS + ["exports/full.zip"]:
        path = result_dir / name
        summary[name] = {
            "exists": path.exists(),
            "path": name,
            "url": f"/api/artifact/{task_id}/{name}",
            "size": path.stat().st_size if path.exists() else 0,
        }
    images_dir = result_dir / "images"
    summary["images"] = {
        "exists": images_dir.exists(),
        "path": "images",
        "url": f"/api/artifact/{task_id}/images",
        "size": 0,
    }
    return summary


def _normalize_blocks(task_id: str, blocks: list[dict[str, Any]], source: SourceFile) -> list[dict[str, Any]]:
    normalized = []
    for index, block in enumerate(blocks, start=1):
        item = dict(block)
        block_id = str(item.get("block_id") or f"b{index:06d}")
        page_number = int(item.get("page_number") or 1)
        source_ref = dict(item.get("source_ref") or {})
        source_ref.setdefault("evidence_id", f"doc:{task_id}:p{page_number}:{block_id}")
        source_ref.setdefault("source_type", f"{item.get('type') or 'text'}_block")
        source_ref.setdefault("path", f"raw/original/{source.filename}")
        item.update(
            {
                "block_id": block_id,
                "type": str(item.get("type") or "paragraph"),
                "sub_type": str(item.get("sub_type") or ""),
                "text": str(item.get("text") or ""),
                "markdown": str(item.get("markdown") or item.get("text") or ""),
                "html": str(item.get("html") or ""),
                "page_number": page_number,
                "page_index": int(item.get("page_index") if item.get("page_index") is not None else page_number - 1),
                "sheet_name": str(item.get("sheet_name") or ""),
                "slide_number": item.get("slide_number"),
                "bbox": item.get("bbox") if isinstance(item.get("bbox"), list) else [],
                "bbox_unit": str(item.get("bbox_unit") or "none"),
                "reading_order": int(item.get("reading_order") or index),
                "parent_block_id": str(item.get("parent_block_id") or ""),
                "source_ref": source_ref,
                "confidence": float(item.get("confidence") if item.get("confidence") is not None else 1.0),
                "warnings": item.get("warnings") if isinstance(item.get("warnings"), list) else [],
            }
        )
        normalized.append(item)
    return normalized


def _build_source_map(
    task_id: str,
    blocks: list[dict[str, Any]],
    tables: list[dict[str, Any]],
    figures: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    sources = []
    figures_by_block = {
        str(figure.get("block_id") or ""): figure
        for figure in figures
        if figure.get("block_id")
    }
    for block in blocks:
        evidence_id = (block.get("source_ref") or {}).get("evidence_id") or make_evidence_id(task_id, int(block.get("page_number") or 1), str(block.get("block_id") or ""))
        figure = figures_by_block.get(str(block.get("block_id") or "")) or {}
        image_id = str(figure.get("image_id") or "") if block.get("type") == "image" else ""
        sources.append(
            {
                "evidence_id": evidence_id,
                "source_type": "text_block" if block.get("type") not in {"image", "table"} else f"{block.get('type')}_block",
                "artifact": "blocks.json",
                "block_id": block.get("block_id", ""),
                "table_id": "",
                "logical_table_id": "",
                "image_id": image_id,
                "markdown_anchor": str(figure.get("markdown_anchor") or f"md-{block.get('block_id', '')}"),
                "page_number": block.get("page_number") or 1,
                "bbox": block.get("bbox") or [],
                "quote": str(block.get("text") or block.get("markdown") or "")[:240],
                "open_source_url": f"/api/documents/source/{task_id}/page/{block.get('page_number') or 1}?block={block.get('block_id', '')}",
                "open_artifact_url": f"/api/documents/artifact/{task_id}/blocks.json",
            }
        )
    for table in tables:
        table_id = table.get("table_id", "")
        sources.append(
            {
                "evidence_id": make_evidence_id(task_id, int(table.get("page_number") or 1), str(table_id)),
                "source_type": "table_block",
                "artifact": "tables.json",
                "block_id": table.get("block_id", ""),
                "table_id": table_id,
                "logical_table_id": "",
                "image_id": "",
                "markdown_anchor": f"md-{table_id}",
                "page_number": table.get("page_number") or 1,
                "bbox": table.get("bbox") or [],
                "quote": str(table.get("title") or table.get("caption") or table_id),
                "open_source_url": f"/api/documents/source/{task_id}/table/{table_id}",
                "open_artifact_url": f"/api/documents/artifact/{task_id}/tables.json",
            }
        )
    for figure in figures:
        sources.append(
            {
                "evidence_id": figure.get("evidence_id") or make_evidence_id(task_id, int(figure.get("page_number") or 1), str(figure.get("image_id") or "")),
                "source_type": "image_block",
                "artifact": "figures.json",
                "block_id": figure.get("block_id", ""),
                "table_id": "",
                "logical_table_id": "",
                "image_id": figure.get("image_id", ""),
                "markdown_anchor": figure.get("markdown_anchor") or f"md-{figure.get('image_id', '')}",
                "page_number": figure.get("page_number") or 1,
                "bbox": figure.get("bbox") or [],
                "quote": figure.get("caption") or figure.get("alt_text") or figure.get("image_id", ""),
                "open_source_url": f"/api/documents/source/{task_id}/image/{figure.get('image_id', '')}",
                "open_artifact_url": f"/api/documents/artifact/{task_id}/{figure.get('image_path', '')}",
            }
        )
    return sources


def _build_layout_blocks(task_id: str, blocks: list[dict[str, Any]]) -> dict[str, Any]:
    pages: dict[int, dict[str, Any]] = {}
    for block in blocks:
        page_number = int(block.get("page_number") or 1)
        page = pages.setdefault(
            page_number,
            {
                "page_number": page_number,
                "page_index": page_number - 1,
                "width": 0,
                "height": 0,
                "blocks": [],
            },
        )
        page["blocks"].append(
            {
                "layout_block_id": f"p{page_number:04d}-{block.get('block_id')}",
                "block_id": block.get("block_id"),
                "type": block.get("type"),
                "bbox": block.get("bbox") or [],
                "bbox_unit": block.get("bbox_unit") or "none",
                "text_preview": str(block.get("text") or block.get("markdown") or "")[:120],
                "confidence": block.get("confidence", 1.0),
            }
        )
    return {"schema_version": SCHEMA_LAYOUT_BLOCKS, "task_id": task_id, "pages": list(pages.values())}


def _build_reading_order(task_id: str, blocks: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_READING_ORDER,
        "task_id": task_id,
        "items": [
            {
                "reading_order": block.get("reading_order"),
                "block_id": block.get("block_id"),
                "page_number": block.get("page_number"),
                "type": block.get("type"),
            }
            for block in blocks
        ],
    }


def _build_comparison_map(task_id: str, blocks: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_COMPARISON_MAP,
        "task_id": task_id,
        "entries": [
            {
                "entry_id": f"cmp-{index:06d}",
                "block_id": block.get("block_id"),
                "layout_block_id": f"p{int(block.get('page_number') or 1):04d}-{block.get('block_id')}",
                "markdown_anchor": f"md-{block.get('block_id')}",
                "json_pointer": f"/blocks/{index - 1}",
                "page_number": block.get("page_number") or 1,
                "bbox": block.get("bbox") or [],
                "evidence_id": (block.get("source_ref") or {}).get("evidence_id", ""),
                "text_hash": "",
            }
            for index, block in enumerate(blocks, start=1)
        ],
    }


def _build_tables(task_id: str, tables: list[dict[str, Any]]) -> dict[str, Any]:
    return {"schema_version": SCHEMA_TABLES, "task_id": task_id, "tables": tables, "physical_tables": tables}


def _build_table_index(task_id: str, tables: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": "document_table_index_v1",
        "task_id": task_id,
        "tables": [
            {
                "table_id": table.get("table_id"),
                "title": table.get("title") or table.get("caption") or "",
                "page_number": table.get("page_number") or 1,
                "sheet_name": table.get("sheet_name") or "",
                "row_count": (table.get("quality") or {}).get("row_count"),
                "column_count": (table.get("quality") or {}).get("column_count"),
            }
            for table in tables
        ],
    }


def _build_logical_tables(task_id: str, tables: list[dict[str, Any]]) -> dict[str, Any]:
    logical = [single_fragment_logical_table(task_id, table, index) for index, table in enumerate(tables, start=1)]
    return {"schema_version": SCHEMA_LOGICAL_TABLES, "task_id": task_id, "logical_tables": logical}


def _empty_table_relations(task_id: str) -> dict[str, Any]:
    return empty_table_relations(task_id)


def _build_figures(task_id: str, figures: list[dict[str, Any]]) -> dict[str, Any]:
    return {"schema_version": SCHEMA_FIGURES, "task_id": task_id, "figures": figures}


def _build_figure_index(task_id: str, figures: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": "document_figure_index_v1",
        "task_id": task_id,
        "figures": [
            {
                "image_id": figure.get("image_id"),
                "type": figure.get("type"),
                "page_number": figure.get("page_number") or 1,
                "caption": figure.get("caption") or "",
                "image_path": figure.get("image_path") or "",
            }
            for figure in figures
        ],
    }


def _build_quality_report(
    task_id: str,
    output: ParseOutput,
    blocks: list[dict[str, Any]],
    source_map: list[dict[str, Any]],
) -> dict[str, Any]:
    source_ratio = source_map_coverage(blocks)
    overall = warning_status(output.warnings)
    return {
        "schema_version": SCHEMA_QUALITY,
        "task_id": task_id,
        "overall_status": overall,
        "document_kind": output.document_kind,
        "page_count": output.page_count,
        "block_count": len(blocks),
        "table_count": len(output.tables),
        "image_count": len(output.figures),
        "equation_count": 0,
        "ocr_used": False,
        "language_detected": output.language_detected or [],
        "coverage": {
            "pages_with_text_ratio": ratio(1 if blocks else 0, 1),
            "blocks_with_source_ratio": source_ratio,
            "tables_with_cells_ratio": 1.0 if output.tables else 0.0,
            "extraction_evidence_ratio": 0.0,
        },
        "image_quality": {
            "image_count": len(output.figures),
            "figure_count": len(output.figures),
            "chart_count": 0,
            "diagram_count": 0,
            "images_with_crop_ratio": 1.0 if output.figures else 0.0,
            "images_with_source_ratio": 1.0 if output.figures else 0.0,
            "images_with_caption_ratio": 1.0 if output.figures else 0.0,
            "images_with_ocr_ratio": 0.0,
            "low_resolution_count": 0,
            "missing_bbox_count": figures_with_missing_bbox(output.figures),
        },
        "source_map_count": len(source_map),
        "warnings": output.warnings,
        "ready_for_knowledge_base": bool(blocks),
    }


def _write_default_extraction_files(result_dir: Path, task_id: str) -> None:
    write_json(result_dir / "extraction" / "schema.json", {"schema_version": "document_extraction_schema_v1", "task_id": task_id, "schema": {}})
    write_json(result_dir / "extraction" / "result.json", {"schema_version": "document_extraction_result_v1", "task_id": task_id, "status": "not_run", "result": {}})
    write_json(result_dir / "extraction" / "evidence_map.json", {"schema_version": "document_extraction_evidence_v1", "task_id": task_id, "evidence_map": {}})
    write_json(result_dir / "extraction" / "validation_report.json", {"schema_version": "document_extraction_validation_v1", "task_id": task_id, "schema_valid": False, "evidence_coverage_ratio": 0.0, "warnings": []})


def _build_zip(result_dir: Path) -> None:
    zip_path = result_dir / "exports" / "full.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in result_dir.rglob("*"):
            if not path.is_file() or path == zip_path:
                continue
            archive.write(path, path.relative_to(result_dir).as_posix())
