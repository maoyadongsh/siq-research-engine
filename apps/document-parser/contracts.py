"""Shared constants and lightweight helpers for document parse artifacts."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


APP_VERSION = "document_parser_0.1.0"

SCHEMA_MANIFEST = "generic_document_parse_v1"
SCHEMA_BLOCKS = "document_blocks_v1"
SCHEMA_TABLES = "document_tables_v1"
SCHEMA_FIGURES = "document_figures_v1"
SCHEMA_SOURCE_MAP = "document_source_map_v1"
SCHEMA_QUALITY = "document_quality_v1"
SCHEMA_DOCUMENT_FULL = "document_full_v1"
SCHEMA_LAYOUT_BLOCKS = "document_layout_blocks_v1"
SCHEMA_READING_ORDER = "document_reading_order_v1"
SCHEMA_COMPARISON_MAP = "document_comparison_map_v1"
SCHEMA_LOGICAL_TABLES = "document_logical_tables_v1"
SCHEMA_TABLE_RELATIONS = "document_table_relations_v1"


QUEUED = "queued"
UPLOADED = "uploaded"
DETECTING_TYPE = "detecting_type"
RUNNING = "running"
POSTPROCESSING = "postprocessing"
COMPLETED = "completed"
COMPLETED_WITH_WARNINGS = "completed_with_warnings"
FAILED = "failed"
CANCELLED = "cancelled"

TERMINAL_STATUSES = {COMPLETED, COMPLETED_WITH_WARNINGS, FAILED, CANCELLED}


SUPPORTED_EXTENSIONS = {
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".jp2",
    ".webp",
    ".gif",
    ".bmp",
    ".doc",
    ".docx",
    ".ppt",
    ".pptx",
    ".xls",
    ".xlsx",
    ".html",
    ".htm",
    ".txt",
    ".md",
    ".markdown",
}

ARTIFACT_ALLOWLIST = {
    "manifest.json",
    "document.md",
    "document_full.json",
    "blocks.json",
    "blocks.ndjson",
    "tables.json",
    "table_index.json",
    "logical_tables.json",
    "table_relations.json",
    "table_merge_corrections.json",
    "figures.json",
    "figure_index.json",
    "source_map.json",
    "quality_report.json",
    "layout_blocks.json",
    "reading_order.json",
    "comparison_map.json",
    "extraction/schema.json",
    "extraction/result.json",
    "extraction/evidence_map.json",
    "extraction/validation_report.json",
}


@dataclass
class ParseConfig:
    model_version: str = "auto"
    ocr: str = "auto"
    enable_formula: bool = True
    enable_table: bool = True
    language: str = "auto"
    page_ranges: str = ""
    extra_formats: list[str] = field(default_factory=list)
    no_cache: bool = False
    data_id: str = ""

    def to_manifest(self) -> dict[str, Any]:
        return {
            "model_version": self.model_version,
            "ocr": self.ocr,
            "enable_formula": self.enable_formula,
            "enable_table": self.enable_table,
            "language": self.language,
            "page_ranges": self.page_ranges,
            "extra_formats": self.extra_formats,
            "no_cache": self.no_cache,
        }


@dataclass
class SourceFile:
    path: Path
    filename: str
    mime_type: str
    extension: str
    file_size: int
    sha256: str
    source_type: str = "upload"
    source_url: str = ""


@dataclass
class ParseOutput:
    markdown: str
    blocks: list[dict[str, Any]]
    tables: list[dict[str, Any]] = field(default_factory=list)
    figures: list[dict[str, Any]] = field(default_factory=list)
    page_metadata: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    page_count: int = 0
    provider_name: str = "simple_text_parser"
    upstream_parser_version: str = ""
    document_kind: str = "text"
    language_detected: list[str] = field(default_factory=list)
    raw_artifacts_dir: str = ""
    upstream_task_id: str = ""
