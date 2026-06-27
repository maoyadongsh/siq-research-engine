from __future__ import annotations

from typing import Any

from .models import EvidenceRef


def evidence_display_target(evidence: EvidenceRef) -> dict[str, Any]:
    """Return a UI-friendly target for PDF, SEC HTML/iXBRL, and table evidence."""
    if evidence.page_number is not None:
        return {
            "mode": "pdf_page",
            "page_number": evidence.page_number,
            "bbox": evidence.bbox,
            "table_index": evidence.table_index,
            "url": evidence.url,
            "path": evidence.path,
            "quote_text": evidence.quote_text,
        }
    if evidence.rendered_page_number is not None:
        return {
            "mode": "rendered_page",
            "page_number": evidence.rendered_page_number,
            "anchor": evidence.anchor,
            "xpath": evidence.xpath,
            "url": evidence.url,
            "quote_text": evidence.quote_text or evidence.html_snippet,
        }
    if evidence.xbrl_tag or evidence.xpath or evidence.anchor:
        return {
            "mode": "sec_html_ixbrl",
            "url": evidence.url,
            "section": evidence.section,
            "anchor": evidence.anchor,
            "xpath": evidence.xpath,
            "xbrl_tag": evidence.xbrl_tag,
            "accession_number": evidence.accession_number,
            "quote_text": evidence.quote_text or evidence.html_snippet,
        }
    return {
        "mode": "generic_artifact",
        "url": evidence.url,
        "path": evidence.path,
        "source_type": evidence.source_type,
        "source_id": evidence.source_id,
        "quote_text": evidence.quote_text,
    }
