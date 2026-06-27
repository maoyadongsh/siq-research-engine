"""Route a source file to the best available local parser."""

from __future__ import annotations

from contracts import ParseConfig, ParseOutput, SourceFile
from providers.simple import (
    parse_docx_document,
    parse_html_document,
    parse_image_document,
    parse_office_placeholder,
    parse_pdf_document,
    parse_spreadsheet_document,
    parse_text_document,
)


def parse_source(task_id: str, source: SourceFile, config: ParseConfig, document_kind: str) -> ParseOutput:
    if document_kind == "pdf":
        return parse_pdf_document(task_id, source, config)
    if document_kind == "image":
        return parse_image_document(task_id, source, config)
    if document_kind == "html":
        return parse_html_document(task_id, source, config)
    if document_kind == "text":
        return parse_text_document(task_id, source, config)
    if document_kind == "excel":
        return parse_spreadsheet_document(task_id, source, config)
    if document_kind == "word" and source.extension == ".docx":
        return parse_docx_document(task_id, source, config)
    if document_kind in {"word", "ppt"}:
        return parse_office_placeholder(task_id, source, config, document_kind)
    return parse_text_document(task_id, source, config)
