"""Read-only source adapters for formal SIQ analysis inputs."""

from .base import AdapterContext, SourceAdapterError, source_family_for_manifest
from .pdf_market import PDFMarketAdapter
from .sec_ixbrl import SecIxbrlAdapter

__all__ = [
    "AdapterContext",
    "PDFMarketAdapter",
    "SecIxbrlAdapter",
    "SourceAdapterError",
    "source_family_for_manifest",
]
